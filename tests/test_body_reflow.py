"""Selected numbered body paragraphs: hanging wraps, ownership and bounded capacity."""
from copy import deepcopy
from io import BytesIO

from fontTools.ttLib import TTFont
from fontTools.pens.ttGlyphPen import TTGlyphPen
import pymupdf
import pytest

from pdfeditor.attributed import digest
from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, replace_in_flow
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow, plan_shared_flow, _contract
import page_flow_fixture as fx
from test_page_proposal import pixels


ROWS = ['(1) 申請書を提出してください。',
        '(2) 必要書類を提出してください。必要書類を提出',
        'してください。']


@pytest.fixture(scope='module')
def numbered(tmp_path_factory):
    root = tmp_path_factory.mktemp('numbered-body')
    original = fx.characters
    with pytest.MonkeyPatch.context() as m:
        m.setattr(fx, 'characters', lambda: sorted(set(original()) | set('()12 ')))
        font = TTFont(BytesIO(fx.build_font()))
    cmap = font.getBestCmap()
    for ch in '()12 ':
        name = cmap[ord(ch)]; advance = 220 if ch == ' ' else 500
        pen = TTGlyphPen(None)
        if ch != ' ':
            pen.moveTo((50,0));pen.lineTo((advance-50,0));pen.lineTo((advance-50,700));pen.lineTo((50,700));pen.closePath()
        font['glyf'][name] = pen.glyph()
        font['hmtx'][name] = (advance, 0)
    path = root/'body.ttf';font.save(path)
    source = root/'source.pdf'
    doc = pymupdf.open();page = doc.new_page(width=360,height=480)
    page.insert_font(fontname='F1',fontbuffer=path.read_bytes())
    page.insert_text((40,100),'申請書',fontname='F1',fontsize=10.5)
    stream = page.get_contents()[0];parts=[]
    body_x=40+10.5*1.72
    for row,(text,y) in enumerate(zip(ROWS,[140,166,186])):
        x=body_x if row==2 else 40.
        for i,ch in enumerate(text):
            gid=font.getGlyphID(cmap[ord(ch)])
            parts.append(f'BT /F1 10.5 Tf 1 0 0 1 {x:.6f} {480-y} Tm <{gid:04x}> Tj ET')
            x+=font['hmtx'][cmap[ord(ch)]][0]*10.5/1000
            if i>=4 and i%3==2:x-=.12
    # An independent heading above and a fixed drawing below remain foreign.
    parts.append('q .5 g 30 70 300 1 re f Q')
    doc.update_stream(stream,doc.xref_stream(stream)+('\n'.join(parts)).encode())
    doc.save(source);doc.close()
    proposal=propose_page_flow(source,font_candidates=[path],line_ids=['p1-l2','p1-l3','p1-l4'])
    assert proposal['status']=='proposed',proposal['refusals']
    return source,path,proposal


def test_numbered_body_two_saves_hanging_wrap_and_following_gap(numbered,tmp_path):
    source,_,proposal=numbered
    assert [p['line_ids'] for p in proposal['paragraphs']]==[['p1-l2'],['p1-l3','p1-l4']]
    assert all(p['policy']['first_line_indent']['value']==pytest.approx(-18.06) for p in proposal['paragraphs'])
    state=accept_page_flow(source,proposal);before=source;sha=source_sha(source)
    original_p2=state['paragraphs']['P2']['logical']['text']
    gap=proposal['follows'][0]['minimum_baseline_gap'];assert gap==26
    for i,(find,replacement) in enumerate([('申請書','申請書を確認し、必要書類を提出してください。'),('確認','再確認')],1):
        pdf,sidecar=tmp_path/f'{i}.pdf',tmp_path/f'{i}.json'
        report=edit_shared_flow(before,state,pdf,sidecar,replace_in_flow(state,find,replacement,paragraph_id='P1'))
        opened=open_shared_flow(pdf,sidecar);assert opened['status']=='restored',opened.get('reason')
        a,b=[report['plan']['fragments'][s] for s in ('slot-0','slot-1')]
        assert len(a['lines'])==2 and len(b['lines'])==2
        assert b['lines'][0]['baseline']==pytest.approx(186)
        assert b['lines'][0]['baseline']-a['lines'][-1]['baseline']==pytest.approx(gap)
        # First glyph on wrapped continuation aligns with the body, not the marker.
        for f in (a,b):
            starts=[next(g for g in f['glyphs'] if g['start']==line['start'])['origin'][0] for line in f['lines']]
            assert starts==pytest.approx([40,58.06],abs=.002)
        assert opened['state']['paragraphs']['P2']['logical']['text']==original_p2
        assert pixels(source,(0,0,360,125))==pixels(pdf,(0,0,360,125))
        assert pixels(source,(0,400,360,480))==pixels(pdf,(0,400,360,480))
        before,state=pdf,opened['state']
    assert source_sha(source)==sha


def test_overflow_foreign_collision_and_invalid_selection_refused(numbered,tmp_path):
    source,font,proposal=numbered
    state=accept_page_flow(source,proposal)
    pdf,sidecar=tmp_path/'bad.pdf',tmp_path/'bad.json'
    with pytest.raises(PdfError,match='exceed all explicitly confirmed shared regions'):
        edit_shared_flow(source,state,pdf,sidecar,replace_in_flow(state,'申請書','申請書'*100,paragraph_id='P1'))
    assert not pdf.exists() and not sidecar.exists()
    with pytest.raises(PdfError,match='collides with foreign content'):
        accept_page_flow(source,proposal,overrides={'region_bottom':440})
    with pytest.raises(PdfError,match='consecutive'):
        propose_page_flow(source,font_candidates=[font],line_ids=['p1-l2','p1-l4'])
    with pytest.raises(PdfError,match='paragraph_starts'):
        accept_page_flow(source,proposal,overrides={'paragraph_starts':['p1-l3']})
    # Removing the marker/head from the selection cannot silently absorb its continuation.
    refused=propose_page_flow(source,font_candidates=[font],line_ids=['p1-l1','p1-l2','p1-l3','p1-l4'])
    assert refused['status']=='refused'


def test_hanging_overhang_cannot_escape_confirmed_region(numbered):
    source,_,proposal=numbered
    state=accept_page_flow(source,proposal)
    for indent in (-100, float('-inf')):
        bad=deepcopy(state);bad['paragraph_policies']['P1']['first_line_indent']=indent
        bad['contract_sha256']=_contract(bad);bad.pop('model_sha256');bad['model_sha256']=digest(bad)
        assert open_shared_flow(source,bad)['status']=='needs_confirmation'


def test_numbered_noedit_raster_is_identical(numbered,tmp_path):
    source,_,proposal=numbered
    state=accept_page_flow(source,proposal)
    out,model=tmp_path/'noop.pdf',tmp_path/'noop.json'
    edit_shared_flow(source,state,out,model,{})
    assert open_shared_flow(out,model)['status']=='restored'
    assert pixels(source,(0,0,360,480))==pixels(out,(0,0,360,480))
