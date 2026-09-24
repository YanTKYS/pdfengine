"""Reviewed LibreOffice source -> confirmed empty area on existing page six.

Only evaluator decisions (ranges, geometry and provider choices) live here.
PDFs, full text, glyph logs and raster artifacts stay under ignored runs/.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

import pymupdf
from pypdf import PdfReader

from pdfeditor.attributed import digest
from pdfeditor.content_stream import ContentPage, patch_streams
from pdfeditor.continuation import confirm_continuation_destination, slot_id
from pdfeditor.elements import _close, _paint_value
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.pdf_save import font_fingerprints, program_pdf_bytes
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow, plan_shared_flow
from evaluations.attributed.evaluate import font_mapping_audit
from evaluations.continuation.resources import generated_block_bytes, inventory
import evaluations.backend.followup as renderer_paths
import evaluations.elements.evaluate as extractor_paths
from evaluations.elements.evaluate import audit_render, extraction
from evaluations.flow_transaction.evaluate import normalize, refuse
from evaluations.realpdf.evaluate import image_fingerprints
from evaluations.story_flow.evaluate import SOURCE, SOURCE_SHA, URL, annotation_fingerprint
from evaluations.story_styles.evaluate import prepare as prepare_story


ROOT=Path(__file__).resolve().parents[2];BASE=Path(__file__).resolve().parent
DEPENDENCIES=['evaluations/story_flow/evaluate.py','evaluations/story_styles/evaluate.py','evaluations/elements/evaluate.py',
    'evaluations/flow_transaction/evaluate.py','evaluations/attributed/evaluate.py','evaluations/backend/followup.py',
    'evaluations/realpdf/evaluate.py','evaluations/realpdf/independent_extract.py','evaluations/continuation/resources.py']
GLYPH_FIELDS=('unicode','glyph_id','origin','size','advance','code','cid','nominal_pdf_width')
PROVIDER_FIELDS=('filename','font_index','sha256','variations')
REVIEWED_PROVIDERS='evaluations/story_styles/summary.json'
CAPACITY_REASON='paragraphs exceed all explicitly confirmed shared regions'
STAGES=('overflow','second','shorten','regrow','noop1','noop2','noop3')
EDITED=('4','5','6')
# Counts a repeated identical save must not change.
RESOURCE_COUNTS=('page_font_resources','generated_fonts_by_page','type0_fonts','generated_fonts','generated_font_graph_objects')


def write(path,value):path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


def prepare():
    if source_sha(SOURCE)!=SOURCE_SHA:raise ValueError('reviewed source differs')
    story=prepare_story();pid=story['logical']['id']
    regions={ident:dict(page=c['page'],bounds=c['bounds'],x=c['layout']['x'],width=c['layout']['width'],
                       first_baseline=c['layout']['baseline']) for ident,c in story['containers'].items()}
    regions['page6']=dict(page=6,bounds=[55,80,385,120],x=56.8,width=326,first_baseline=92)
    destination=confirm_continuation_destination(SOURCE,destination_id='reviewed-page6-space',paragraph_id=pid,
        region_id='page6',page=6,bounds=regions['page6']['bounds'],insertion='before-page-program',
        graphics_state='isolated-pdf-initial-state')
    state=confirm_shared_flow(SOURCE,{pid:story},flow_id='reviewed-continuation',paragraph_order=[pid],
        regions=regions,region_order=list(regions),slot_regions={pid:{'A':'A','B':'B'}},
        paragraph_policies={pid:dict(min_line_height=21.6,first_line_indent=0,keep_together=False,
            break_before='auto',break_after='auto',empty=dict(kind='reserve-line',ascent=8.4,descent=2.1))},follows=[],
        protected_regions={str(i):[dict(role='fixed',bounds=[398,58,540,111])] for i in (4,5,6)},
        continuation_destinations={destination['destination_id']:destination})
    return state,pid,destination


def source_replay(directory,state):
    programs={}
    for slot in state['slots'].values():
        p=slot['binding']['paragraph'];page=p['selection']['page'];content=ContentPage(SOURCE,page)
        try:
            ids=set(p['selection']['glyph_ids'])
            programs[page-1]=patch_streams(content,content.selected_events(ids),ids)[-content.page.xref]
        finally:content.close()
    # program_pdf_bytes has a single-page entry point: perform independent
    # source replay gates, never use these artifacts as editing inputs.
    proof=[]
    for page,program in programs.items():
        replay=directory/f'source-replay-{page+1}.pdf';replay.write_bytes(program_pdf_bytes(SOURCE,page,program))
        v=audit_render(SOURCE,replay,page+1,directory/f'source-replay-{page+1}',(0,0,595,842),noop=True)
        if extraction(SOURCE,directory,'original')['pages']!=extraction(replay,directory,'replayed')['pages']:
            raise ValueError('source replay changes independent Unicode')
        proof.append(dict(page=page+1,mupdf_equal=v['mupdf_page_pixels'],poppler_changed_pixels=v['poppler_diff']['all_changed_pixels']))
    return proof


def audit(before,after,state,initial,report,directory,original_text,*,noop=False,owned=None):
    directory.mkdir();visual={};edited={4,5,6}
    for page in range(1,state['page_count']+1):
        if page in edited or noop:
            r=next((r for r in state['regions'].values() if r['page']==page),None)
            v=audit_render(before,after,page,directory/f'page-{page}',r['bounds'] if r else (0,0,0,0),
                noop=noop or page not in edited,edited_pages=edited)
            visual[str(page)]=dict(mupdf_equal=v['mupdf_page_pixels'],poppler_changed_pixels=v['poppler_diff']['all_changed_pixels'],
                poppler_outside_changed_pixels=v['poppler_diff_with_1pt_margin']['outside_changed_pixels'])
    for step in report['steps']:font_mapping_audit(after,step['report'])
    extracted=extraction(after,directory,'saved')['pages']
    for page in range(1,state['page_count']+1):
        text=normalize(original_text[page-1])
        initial_slots=[s for s in initial['slots'].values() if initial['regions'][s['region_id']]['page']==page]
        current=[s for s in state['slots'].values() if state['regions'][s['region_id']]['page']==page]
        if initial_slots:
            old=''.join(normalize(s['binding']['paragraph']['text']) for s in initial_slots)
            if text.count(old)!=1:raise ValueError('source Unicode is not uniquely delimited')
            replacement=''.join(normalize(s['binding']['paragraph']['text']) for s in current)
            text=text.replace(old,replacement)
        elif current:
            text=''.join(normalize(s['binding']['paragraph']['text']) for s in current)+text
        if normalize(extracted[page-1])!=text:raise ValueError(f'complete-page Unicode differs on {page}')
    old,new=PdfReader(before),PdfReader(after)
    with pymupdf.open(before) as a,pymupdf.open(after) as b:
        for i in range(len(a)):
            if i+1 not in edited and a[i].get_pixmap(dpi=144).samples!=b[i].get_pixmap(dpi=144).samples:
                raise ValueError('fixed page changed')
            pa,pb=interpreted_paints(before,i+1),interpreted_paints(after,i+1)
            paint=lambda p:[_paint_value(e) for e in p['events'] if e['kind'] not in ('fill-text','stroke-text','ignore-text')]
            if pa['errors'] or pb['errors'] or not _close(paint(pa),paint(pb)):raise ValueError('fixed paint changed')
            if image_fingerprints(a[i])!=image_fingerprints(b[i]) or annotation_fingerprint(old,i+1)!=annotation_fingerprint(new,i+1):
                raise ValueError('image/annotation changed')
            aliases={g['font_resource'][1:] for step in report['steps'] if step['report']['selection']['page']==i+1
                     for g in step['report']['glyph_plan']}
            # Only aliases the previous revision's verified records prove to be
            # pdfengine-generated, and that this save re-targeted, may differ.
            retargeted={a[1:] for a in report['generated_font_outcome'].get(str(i+1),{}) if a in (owned or {}).get(str(i+1),{})}
            if ([f for f in font_fingerprints(b,i) if f[0][3] not in aliases]
                    !=[f for f in font_fingerprints(a,i) if f[0][3] not in retargeted]):
                raise ValueError('original font resource changed')
    return dict(renderers=visual,all_page_unicode=True,all_nontext_paint_images_annotations_original_fonts=True,cid_gid_w=True,
        allocation={sid:dict(range=s['range'],occupancy=s['occupancy']) for sid,s in state['slots'].items()})


def tools():
    poppler=subprocess.run([str(renderer_paths.DEFAULT_POPPLER),'-v'],capture_output=True,timeout=60)
    extractor=subprocess.run([str(extractor_paths.DEFAULT_PYPDF),'-c','import sys,pypdf;print(pypdf.__version__,sys.version.split()[0])'],
        capture_output=True,timeout=60)
    # Some Poppler builds exit 99 after printing -v; identify the tool by its banner.
    banner=next((l.strip() for l in (poppler.stderr+poppler.stdout).decode(errors='replace').splitlines() if 'pdftoppm version' in l),None)
    if banner is None or extractor.returncode:raise ValueError('independent renderer or extractor is unavailable')
    version,python=extractor.stdout.decode().split()
    return dict(poppler=banner,independent_pypdf=version,independent_python=python)


def provider_evidence(registry):
    return {i:dict(filename=Path(r['reflow_provider']['path']).name,font_index=r['reflow_provider'].get('font_index',0),
        sha256=r['reflow_provider']['sha256'],variations=r['reflow_provider'].get('variations')) for i,r in registry.items()}


def reviewed_providers():
    """Providers published by the story_styles evaluation: same bytes and face, not merely the same file name."""
    data=(ROOT/REVIEWED_PROVIDERS).read_bytes();styles=json.loads(data.decode('utf-8'))['logical_styles']
    return ({i:{k:style['provider'][k] for k in PROVIDER_FIELDS} for i,style in styles.items()},
            dict(path=REVIEWED_PROVIDERS,sha256=hashlib.sha256(data).hexdigest()))


def stable_slot(slot):
    """Semantic slot record; byte offsets, xrefs and resource aliases legitimately change on resave."""
    binding=slot['binding']
    return dict({k:slot.get(k) for k in ('paragraph_id','region_id','destination_id','creation_provenance','range','render_end','occupancy')},
        text=binding['paragraph']['text'],lines=binding['physical_layout']['lines'],alignment=binding['logical_element'].get('alignment'),
        styles=[{k:v for k,v in style.items() if k not in ('font_resource','font_xref')} for style in binding['paragraph']['styles']])


def resources(pdf,state,destination,source_aliases):
    """Font/resource sizes of one saved revision; ownership only from verified records."""
    records=state.get('generated_fonts',{})
    inv=inventory(pdf,records=records,source=SOURCE)
    for page,entries in inv['pages'].items():
        if any(e['kind']=='unknown' for e in entries):raise ValueError(f'unrecorded font resource on page {page}')
        if {e['alias'] for e in entries if e['kind']=='original'}!=source_aliases[page]:
            raise ValueError(f'source font resource removed or changed on page {page}')
    return dict(pdf_bytes=Path(pdf).stat().st_size,
        page_font_resources={p:len(inv['pages'][p]) for p in EDITED},
        generated_fonts_by_page={p:len(records.get(p,{})) for p in EDITED},
        type0_fonts=inv['type0_fonts'],generated_fonts=inv['owned_font_roots'],
        generated_font_graph_objects=inv['owned_font_graph_objects'],
        generated_block_bytes=generated_block_bytes(pdf,6,destination))


def run(directory):
    engine={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    environment=tools()
    state,pid,destination=prepare();initial=deepcopy(state)
    providers=provider_evidence(state['paragraphs'][pid]['style_registry'])
    reviewed,provider_evidence_source=reviewed_providers()
    if providers!=reviewed:
        raise ValueError('reflow providers differ from the reviewed story_styles providers: '+json.dumps(providers,sort_keys=True))
    write(directory/'initial.json',state)
    replay=source_replay(directory,state);original_text=extraction(SOURCE,directory,'original')['pages']
    logical=deepcopy(state['paragraphs'][pid]['logical']);sid=slot_id(destination)
    extra='確認した空き領域へ同じ文章の続きを配置し、再編集と保存後の文字位置を確認します。'*2
    source_aliases={p:{e['alias'] for e in v} for p,v in inventory(SOURCE,source=SOURCE)['pages'].items()}
    baseline=resources(SOURCE,state,destination,source_aliases)
    stages=[];pdf=SOURCE;previous=creation=None;previous_sizes=baseline
    for name in STAGES:
        noop=name.startswith('noop')
        edits=({pid:dict(edits=[dict(start=0,end=len(state['paragraphs'][pid]['logical']['text']),text='確認。',style_id='body')])}
               if name=='shorten' else {} if noop else
               {pid:dict(edits=[dict(start=0,end=len(state['paragraphs'][pid]['logical']['text']),runs=[
                   *[dict(text=logical['text'][s['start']:s['end']],style_id=s['style_id']) for s in logical['style_spans']],
                   dict(text=extra.replace('再編集','追加編集') if name=='second' else extra,style_id='body')])])})
        preview=plan_shared_flow(pdf,state,edits);write(directory/f'{name}-plan.json',preview)
        out,side=directory/f'{name}.pdf',directory/f'{name}.json'
        report=edit_shared_flow(pdf,state,out,side,edits);write(directory/f'{name}-report.json',report)
        if report['plan']!=preview:raise ValueError('executed plan differs')
        opened=open_shared_flow(out,side)
        if opened['status']!='restored':raise ValueError(opened['reason'])
        updated=opened['state']
        if sid not in updated['slots'] or len(updated['slots'])!=3:raise ValueError('generated identity changed')
        if (updated['slots'][sid]['occupancy'] is None)!=(name=='shorten'):raise ValueError('continuation did not activate/dormant')
        if updated['contract_sha256']!=initial['contract_sha256']:raise ValueError('confirmation changed')
        # Only the first overflow creates the generated slot; later edits reuse it.
        if set(report['plan']['new_slots'])!=({sid} if name=='overflow' else set()):raise ValueError('generated slot was not reused')
        creation=creation or updated['slots'][sid]['creation_binding']
        if updated['slots'][sid]['creation_binding']!=creation:raise ValueError('generated creation provenance changed')
        if name=='shorten' and updated['slots'][sid]['binding']['paragraph']['text']:raise ValueError('dormant slot still paints text')
        checks={}
        if noop:
            for ident,s in state['slots'].items():
                if s['range']!=updated['slots'][ident]['range'] or s['occupancy']!=updated['slots'][ident]['occupancy']:
                    raise ValueError('no-op allocation changed')
            if updated['paragraphs']!=state['paragraphs'] or updated['continuation_destinations']!=state['continuation_destinations']:
                raise ValueError('no-op changed paragraph, style or destination records')
            if any(stable_slot(s)!=stable_slot(updated['slots'][i]) for i,s in state['slots'].items()):
                raise ValueError('no-op changed slot identity, allocation, layout or inline style')
            glyphs=lambda r:[[{k:g[k] for k in GLYPH_FIELDS} for g in step['report']['glyph_plan']] for step in r['steps']]
            if glyphs(previous)!=glyphs(report):raise ValueError('no-op changed planned glyphs')
            checks=dict(paragraph_style_destination_records_equal=True,slot_identity_allocation_layout_style_equal=True,
                planned_glyph_fields_equal=list(GLYPH_FIELDS),planned_glyphs=sum(map(len,glyphs(report))))
        proof=audit(pdf,out,updated,initial,report,directory/(name+'-audit'),original_text,noop=noop,
                    owned=state.get('generated_fonts',{}))
        sizes=resources(out,updated,destination,source_aliases);outcome=report['generated_font_outcome']
        if noop:
            # An identical re-save keeps every generated font object and adds none.
            if any(sizes[k]!=previous_sizes[k] for k in RESOURCE_COUNTS):raise ValueError('no-op changed font/resource counts')
            if any(set(v.values())!={'reused'} for v in outcome.values()):raise ValueError('no-op wrote a new font object')
            if updated['generated_fonts']!=state['generated_fonts']:raise ValueError('no-op changed generated font records')
        stages.append(dict(stage=name,status='passed',restored=True,slot_id=sid,saves=report['saves'],
            new_slots=sorted(report['plan']['new_slots']),
            generated_lines=len(updated['slots'][sid]['binding']['physical_layout']['lines']),**checks,**proof,
            resources=dict(sizes,font_outcome=outcome)))
        write(directory/'stages.json',stages);print(name,'passed',flush=True)
        pdf,state,previous,previous_sizes=out,updated,report,sizes
    # 245+160 chars exceed the ~271-char confirmed capacity by several lines.
    # Layout measures every break candidate per line (cubic in Japanese text):
    # extra*20 would need roughly 25 GB before the same refusal is reached.
    refused=refuse(directory,'capacity',lambda o,j:edit_shared_flow(pdf,state,o,j,
        {pid:dict(edits=[dict(start=0,end=0,text=extra*2,style_id='body')])}))
    if refused['reason']!=CAPACITY_REASON:
        raise ValueError('capacity negative control was refused for an unexpected reason: '+refused['reason'])
    if engine!={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}:raise ValueError('engine changed during evaluation')
    return dict(schema='pdfengine-continuation-evaluation-2',status='passed',source_url=URL,source_sha256=SOURCE_SHA,
        source_replay=replay,source_resources=baseline,stages=stages,negative_controls=[refused],destination=destination,
        scope='one external LibreOffice paragraph, original slots on pages 4/5, reviewed empty area on existing page 6',
        providers=providers,provider_evidence_source=provider_evidence_source,
        environment=dict(engine_digest=hashlib.sha256(json.dumps(engine,sort_keys=True).encode()).hexdigest(),engine_sha256=engine,
            runner_sha256=source_sha(Path(__file__)),evaluation_dependencies_sha256={d:source_sha(ROOT/d) for d in DEPENDENCIES},
            python=sys.version.split()[0],platform=platform.platform(),pymupdf=pymupdf.VersionBind,**environment))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True)
    write(directory/'summary.json',run(directory))
