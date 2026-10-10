"""New-glyph providers are explicit; original glyph identity remains independent."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path

from fontTools.ttLib import TTFont
import pymupdf
import pytest

from pdfeditor.attributed import digest
from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, accept_page_flow_report, replace_in_flow
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow, _contract
from pdfeditor.shaped_font import ShapedFont, FontError
import page_flow_fixture as fx
from test_page_proposal import pixels

NEW = '再申請書'
LONG = '再申請書を確認し、必要書類一式とあわせて、担当窓口へ持参または郵送により提出してください。'


@pytest.fixture(scope='module')
def coverage(tmp_path_factory):
    root=tmp_path_factory.mktemp('font-coverage')
    source=fx.make_page(root/'source.pdf',fx.build_font())
    paths={}
    for name,options in [('different',dict(advance=900,salt=7)),('other',dict(advance=1100,salt=9)),
                         ('same_metrics',dict(salt=13)),('missing',dict(drop=['再']))]:
        paths[name]=root/f'{name}.ttf';paths[name].write_bytes(fx.build_font(**options))
    font=TTFont(BytesIO(paths['different'].read_bytes()));font['OS/2'].fsType=2
    paths['restricted']=root/'restricted.ttf';font.save(paths['restricted'])
    paths['broken']=root/'broken.ttf';paths['broken'].write_bytes(b'not a font')
    return source,paths


def propose(source, files, cache, text=NEW):
    return propose_page_flow(source,font_candidates=files,include_embedded_fonts=True,font_cache=cache,
                             new_text={'style-1':text})


def choice(path):
    return {'style-1':dict(sha256=source_sha(path),font_index=0,relation='substituted')}


def test_automatic_source_and_installed_discovery_needs_explicit_choice(coverage,tmp_path,monkeypatch):
    source,fonts=coverage
    import pdfeditor.page_proposal as front
    monkeypatch.setattr(front,'default_font_roots',lambda:[str(fonts['different'].parent)])
    p=propose_page_flow(source,font_cache=tmp_path/'cache',new_text={'style-1':NEW})
    assert p['font_search']['include_embedded_fonts'] is True
    assert p['status']=='needs-choice'
    assert '再' in p['styles'][0]['source_font']['missing_new_characters']
    assert len(p['styles'][0]['providers']['substitutes'])==3
    with pymupdf.open(source) as doc:
        for entry in p['embedded_fonts']:
            assert Path(entry['path']).read_bytes()==doc.extract_font(entry['source_xref'])[3]
    with pytest.raises(PdfError,match='explicit-substitution-required'):
        accept_page_flow(source,p)
    with pytest.raises(PdfError,match='metric-verified candidate'):
        accept_page_flow(source,p,provider_choices={'style-1':{'sha256':source_sha(fonts['different'])}})
    accepted=accept_page_flow_report(source,p,provider_choices=choice(fonts['different']))
    entry=accepted['state']['paragraphs']['P2']['style_registry']['style-1']
    assert entry['provider_relation']=='substituted'
    assert entry['provider_selection']['sha256']==source_sha(fonts['different'])
    assert accepted['receipt']['providers']['style-1']['provenance']=='caller-substitution'
    assert accepted['receipt']['reproduction']['status']=='reproduced'


def test_equal_metrics_do_not_auto_authorize_a_different_program(coverage,tmp_path):
    source,fonts=coverage
    p=propose(source,[fonts['same_metrics']],tmp_path/'cache')
    assert p['status']=='needs-choice'
    providers=p['styles'][0]['providers']
    assert providers['substitutes'][0]['source_metric_qualified'] is True
    assert providers['substitutes'][0]['source_program_identical'] is False
    assert len(providers['verified'])==2  # source and full font retain strict metric evidence
    with pytest.raises(PdfError):accept_page_flow(source,p)
    state=accept_page_flow(source,p,provider_choices=choice(fonts['same_metrics']))
    assert state['paragraphs']['P2']['style_registry']['style-1']['provider_relation']=='substituted'


def test_two_edits_wrap_keep_source_codes_and_reopen(coverage,tmp_path):
    source,fonts=coverage;sha=source_sha(source)
    p=propose(source,[fonts['different']],tmp_path/'cache',text=LONG)
    # The full provider does not match source advances; the old glyphs do not need it.
    assert not p['styles'][0]['providers']['substitutes'][0]['source_metric_qualified']
    state=accept_page_flow(source,p,provider_choices=choice(fonts['different']))
    noop,model=tmp_path/'noop.pdf',tmp_path/'noop.json'
    report=edit_shared_flow(source,state,noop,model,{})
    assert pixels(source,(0,0,*fx.PAGE))==pixels(noop,(0,0,*fx.PAGE))
    assert all(s['report']['provided_font_glyph_count']==0 for s in report['steps'])
    before=source
    for index,replacement in enumerate((NEW,LONG),1):
        pdf,sidecar=tmp_path/f'{index}.pdf',tmp_path/f'{index}.json'
        report=edit_shared_flow(before,state,pdf,sidecar,
            replace_in_flow(state,'申請書' if index==1 else NEW,replacement,paragraph_id='P2'))
        opened=open_shared_flow(pdf,sidecar);assert opened['status']=='restored',opened.get('reason')
        with pymupdf.open(before) as old:
            chars=[c for span in old[0].get_texttrace() for c in span['chars']]
        for step in report['steps']:
            for glyph in step['report']['glyph_plan']:
                if glyph['source_index'] is not None:
                    c=chars[glyph['source_index']]
                    assert glyph['unicode']==chr(c[0]) and glyph['glyph_id']==c[1]
        assert pixels(source,(0,780,*fx.PAGE))==pixels(pdf,(0,780,*fx.PAGE))
        before,state=pdf,opened['state']
    assert len(report['plan']['fragments']['slot-1']['lines'])>=2
    assert state['slots']['slot-2']['occupancy']['baseline'] > p['paragraphs'][2]['first_baseline']
    assert source_sha(source)==sha


@pytest.mark.parametrize('kind',['missing','restricted','broken'])
def test_unusable_providers_are_refused(coverage,tmp_path,kind):
    source,fonts=coverage
    p=propose(source,[fonts[kind]],tmp_path/'cache')
    assert p['status']=='unresolved'
    assert p['styles'][0]['providers']['substitutes']==[]
    with pytest.raises(PdfError):accept_page_flow(source,p,provider_choices=choice(fonts[kind]))
    assert source.exists()


def test_runtime_missing_glyph_is_atomic(coverage,tmp_path):
    source,fonts=coverage
    p=propose(source,[fonts['different']],tmp_path/'cache')
    state=accept_page_flow(source,p,provider_choices=choice(fonts['different']))
    pdf,sidecar=tmp_path/'bad.pdf',tmp_path/'bad.json'
    with pytest.raises(FontError,match='notdef'):
        edit_shared_flow(source,state,pdf,sidecar,replace_in_flow(state,'申請書','龍',paragraph_id='P2'))
    assert not pdf.exists() and not sidecar.exists()


def test_provider_revocation_and_tampering_require_confirmation(coverage,tmp_path):
    source,fonts=coverage
    provider=tmp_path/'provider.ttf';provider.write_bytes(fonts['different'].read_bytes())
    p=propose(source,[provider],tmp_path/'cache')
    state=accept_page_flow(source,p,provider_choices=choice(provider))
    pdf,sidecar=tmp_path/'saved.pdf',tmp_path/'saved.json'
    edit_shared_flow(source,state,pdf,sidecar,replace_in_flow(state,'申請書',NEW,paragraph_id='P2'))
    saved=open_shared_flow(pdf,sidecar)['state']
    for field,value in [('font_index',1),('sha256','0'*64),('qualification','trusted by font name')]:
        bad=deepcopy(saved)
        bad['paragraphs']['P2']['style_registry']['style-1']['provider_selection'][field]=value
        bad['contract_sha256']=_contract(bad);bad.pop('model_sha256');bad['model_sha256']=digest(bad)
        assert open_shared_flow(pdf,bad)['status']=='needs_confirmation'
    bad=deepcopy(saved)
    next(iter(bad['generated_fonts']['1'].values()))['provider']['font_index']=99
    bad.pop('model_sha256');bad['model_sha256']=digest(bad)
    assert open_shared_flow(pdf,bad)['status']=='needs_confirmation'
    provider.write_bytes(provider.read_bytes()+b'changed')
    assert open_shared_flow(pdf,sidecar)['status']=='needs_confirmation'
    provider.unlink()
    assert open_shared_flow(pdf,sidecar)['status']=='needs_confirmation'


def test_face_index_cannot_alias_a_single_face_program(coverage):
    _,fonts=coverage
    for index in (-1,1,True):
        with pytest.raises(FontError,match='face index'):
            ShapedFont(fonts['different'],font_index=index)


def test_malformed_collection_header_has_no_candidates(coverage,tmp_path):
    source,_=coverage
    font=tmp_path/'broken.ttc';font.write_bytes(b'ttcf'+bytes.fromhex('00010000ffffffff'))
    p=propose(source,[font],tmp_path/'cache')
    assert p['status']=='unresolved'


def test_cli_requires_explicit_substitute_choice(coverage,tmp_path,capsys):
    import json
    from pdfeditor.cli import main
    source,fonts=coverage
    query=tmp_path/'query.json';query.write_text(json.dumps({'style-1':NEW}),encoding='utf-8')
    proposal=tmp_path/'proposal.json';state=tmp_path/'state.json';receipt=tmp_path/'receipt.json'
    assert main(['propose-page',str(source),'--json',str(proposal),
                 '--font',str(fonts['different']),'--include-embedded-fonts',
                 '--font-cache',str(tmp_path/'cache'),'--new-text',str(query)])==0
    args=['accept-page',str(source),'--proposal',str(proposal),'--json',str(state),'--receipt',str(receipt)]
    assert main(args)==2
    assert not state.exists() and not receipt.exists()
    assert main(args+['--substitute-provider','style-1='+source_sha(fonts['different'])+':0'])==0
    assert json.loads(receipt.read_text())['providers']['style-1']['provenance']=='caller-substitution'
    pdf,sidecar=tmp_path/'saved.pdf',tmp_path/'saved.json'
    assert main(['replace-text',str(source),str(pdf),'--state',str(state),'--state-output',str(sidecar),
                 '--find','申請書','--replacement',NEW])==0
    assert open_shared_flow(pdf,sidecar)['status']=='restored'


def test_discovered_default_variable_instance_survives_reopen(coverage,tmp_path):
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables._f_v_a_r import Axis
    source,fonts=coverage
    font=TTFont(fonts['different'])
    font['fvar']=newTable('fvar');font['fvar'].axes=[];font['fvar'].instances=[]
    axis=Axis();axis.axisTag='wght';axis.minValue=100;axis.defaultValue=400;axis.maxValue=900
    axis.flags=0;axis.axisNameID=256;font['fvar'].axes.append(axis)
    font['gvar']=newTable('gvar');font['gvar'].variations={name:[] for name in font.getGlyphOrder()}
    provider=tmp_path/'variable.ttf';font.save(provider);font.close()
    p=propose(source,[provider],tmp_path/'cache')
    state=accept_page_flow(source,p,provider_choices=choice(provider))
    assert state['paragraphs']['P2']['style_registry']['style-1']['reflow_provider']['variations']=={'wght':400}
    pdf,sidecar=tmp_path/'saved.pdf',tmp_path/'saved.json'
    edit_shared_flow(source,state,pdf,sidecar,replace_in_flow(state,'申請書',NEW,paragraph_id='P2'))
    assert open_shared_flow(pdf,sidecar)['status']=='restored'
