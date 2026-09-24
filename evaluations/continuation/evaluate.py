"""Reviewed LibreOffice source -> confirmed empty area on existing page six.

Only evaluator decisions (ranges, geometry and provider choices) live here.
PDFs, full text, glyph logs and raster artifacts stay under ignored runs/.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

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
from evaluations.elements.evaluate import audit_render, extraction
from evaluations.flow_transaction.evaluate import normalize, refuse
from evaluations.realpdf.evaluate import image_fingerprints
from evaluations.story_flow.evaluate import SOURCE, SOURCE_SHA, URL, annotation_fingerprint
from evaluations.story_styles.evaluate import prepare as prepare_story


ROOT=Path(__file__).resolve().parents[2];BASE=Path(__file__).resolve().parent


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


def audit(before,after,state,initial,report,directory,original_text,*,noop=False):
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
            if [f for f in font_fingerprints(b,i) if f[0][3] not in aliases]!=font_fingerprints(a,i):
                raise ValueError('original font resource changed')
    return dict(renderers=visual,all_page_unicode=True,all_nontext_paint_images_annotations_original_fonts=True,cid_gid_w=True,
        allocation={sid:dict(range=s['range'],occupancy=s['occupancy']) for sid,s in state['slots'].items()})


def run(directory):
    engine={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    state,pid,destination=prepare();initial=deepcopy(state);write(directory/'initial.json',state)
    replay=source_replay(directory,state);original_text=extraction(SOURCE,directory,'original')['pages']
    logical=deepcopy(state['paragraphs'][pid]['logical']);sid=slot_id(destination)
    extra='確認した空き領域へ同じ文章の続きを配置し、再編集と保存後の文字位置を確認します。'*2
    stages=[];pdf=SOURCE
    for name in ('overflow','second','shorten','regrow','noop'):
        edits=({pid:dict(edits=[dict(start=0,end=len(state['paragraphs'][pid]['logical']['text']),text='確認。',style_id='body')])}
               if name=='shorten' else {} if name=='noop' else
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
        if name=='noop':
            for ident,s in state['slots'].items():
                if s['range']!=updated['slots'][ident]['range'] or s['occupancy']!=updated['slots'][ident]['occupancy']:
                    raise ValueError('no-op allocation changed')
        proof=audit(pdf,out,updated,initial,report,directory/(name+'-audit'),original_text,noop=name=='noop')
        stages.append(dict(stage=name,status='passed',restored=True,slot_id=sid,saves=report['saves'],**proof))
        write(directory/'stages.json',stages);print(name,'passed',flush=True);pdf,state=out,updated
    refused=refuse(directory,'capacity',lambda o,j:edit_shared_flow(pdf,state,o,j,
        {pid:dict(edits=[dict(start=0,end=0,text=extra*20,style_id='body')])}))
    if engine!={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}:raise ValueError('engine changed during evaluation')
    return dict(schema='pdfengine-continuation-evaluation-1',status='passed',source_url=URL,source_sha256=SOURCE_SHA,
        source_replay=replay,stages=stages,negative_controls=[refused],destination=destination,
        scope='one external LibreOffice paragraph, original slots on pages 4/5, reviewed empty area on existing page 6',
        environment=dict(engine_sha256=engine,runner_sha256=source_sha(Path(__file__)),pymupdf=pymupdf.VersionBind))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True)
    write(directory/'summary.json',run(directory))
