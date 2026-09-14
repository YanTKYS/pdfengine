"""Two reviewed LibreOffice paragraphs sharing explicitly confirmed capacity."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import pymupdf
from pypdf import PdfReader

from pdfeditor.attributed import inspect_paragraph,digest
from pdfeditor.content_stream import ContentPage,patch_streams
from pdfeditor.document_flow import _reseal
from pdfeditor.elements import _close,_paint_value
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.pdf_save import font_fingerprints,program_pdf_bytes
from pdfeditor.replay import glyph_observations,compare_glyphs
from pdfeditor.selection import make_selection,source_sha
from pdfeditor.shared_flow import confirm_shared_flow,edit_shared_flow,open_shared_flow,plan_shared_flow
from pdfeditor.story_flow import confirm_story
from pdfeditor.story_styles import style_ids
from evaluations.attributed.evaluate import font_mapping_audit
from evaluations.elements.evaluate import audit_render,extraction
from evaluations.flow_transaction.evaluate import refuse,normalize
from evaluations.realpdf.evaluate import image_fingerprints
from evaluations.story_flow.evaluate import SOURCE,SOURCE_SHA,URL,annotation_fingerprint
from evaluations.story_styles.evaluate import RUNS


ROOT=Path(__file__).resolve().parents[2];BASE=Path(__file__).resolve().parent
A='reviewed-mixed-training-paragraph';B='reviewed-following-training-paragraph'
SHORT=[dict(text='書式と',style_id='body'),dict(text='PDF',style_id='latin'),dict(text='を確認。',style_id='body')]


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
def replace(state,pid,runs):return dict(edits=[dict(start=0,end=len(state['paragraphs'][pid]['logical']['text']),runs=runs)])


def prepare():
    if source_sha(SOURCE)!=SOURCE_SHA:raise ValueError('reviewed source changed')
    # Reuse the validated source import from the mixed-style phase. Opening
    # it checks the current original PDF; no inference or selection rerun.
    first=read(ROOT/'evaluations/story_styles/runs/final/initial.json')
    selection=make_selection(SOURCE,5,glyph_ids=list(range(114,254)),explicit_width=482)
    p=inspect_paragraph(SOURCE,selection)
    second=confirm_story(SOURCE,{'C':dict(page=5,bounds=[55,196,540,260],paragraph=p,paint_relations=[],
        layout=dict(x=56.8,baseline=210.5,width=482,max_bottom=260,min_line_height=21.6,first_line_indent=0))},
        paragraph_id=B,chain=['C'],protected_regions={},
        styles={'body':dict(provider=dict(path='C:/Windows/Fonts/msmincho.ttc',font_index=1),provider_relation='confirmed_reflow_provider')},
        style_assignments={'C':{s['id']:'body' for s in p['styles']}},typing_style_id='body')
    policies={pid:dict(min_line_height=21.6,first_line_indent=10.5,keep_together=False,break_before='auto',break_after='auto',
                      empty=dict(kind='reserve-line',ascent=8.4,descent=2.1)) for pid in (A,B)}
    return confirm_shared_flow(SOURCE,{A:first,B:second},flow_id='reviewed-training-body',paragraph_order=[A,B],
        regions={'page-4-body':dict(page=4,bounds=[55,758,540,785],x=56.8,width=482,first_baseline=769.5),
                 'page-5-body':dict(page=5,bounds=[55,130,540,260],x=56.8,width=482,first_baseline=142)},
        region_order=['page-4-body','page-5-body'],slot_regions={A:{'A':'page-4-body','B':'page-5-body'},B:{'C':'page-5-body'}},
        paragraph_policies=policies,
        follows=[dict(before=A,after=B,minimum_baseline_gap=26.5,region_start='reset-to-region-baseline')],
        protected_regions={str(i):[dict(role='fixed',bounds=[398,58,540,111])] for i in (4,5)})


def source_gates(directory,state):
    previous=read(ROOT/'evaluations/story_styles/summary.json')
    for name in ('content_stream.py','pdf_save.py','replay.py','selection.py'):
        if source_sha(ROOT/'pdfeditor'/name)!=previous['environment']['engine_sha256'][name]:raise ValueError('source replay code changed')
    result={};source_text=extraction(SOURCE,directory,'source')
    for sid,slot in state['slots'].items():
        page=state['regions'][slot['region_id']]['page'];content=ContentPage(SOURCE,page)
        try:
            selected=set(slot['binding']['paragraph']['selection']['glyph_ids'])
            program=patch_streams(content,content.selected_events(selected),selected,remove=False)[-content.page.xref]
        finally:content.close()
        if slot['paragraph_id']==A:
            key='A' if page==4 else 'B'
            path=ROOT/('evaluations/story_flow/runs/final-lo/A-source-replay.pdf' if page==4 else
                       'evaluations/story_styles/runs/final/B-source-replay.pdf')
            if source_sha(path)!=previous['source_replay'][key]['replay_pdf_sha256']:raise ValueError('prior replay artifact differs')
            restored=ContentPage(path,page)
            try:
                if restored.streams[-restored.page.xref]!=program:raise ValueError('exact source replay program is not reusable')
            finally:restored.close()
            result[sid]=dict(previous['source_replay'][key])
            result[sid]['evidence']='reused exact program and artifact hash from ab781fa'
        else:
            path=directory/'following-source-replay.pdf';path.write_bytes(program_pdf_bytes(SOURCE,page-1,program))
            with pymupdf.open(SOURCE) as before,pymupdf.open(path) as after:
                for i in range(len(before)):
                    if not compare_glyphs(glyph_observations(before[i]),glyph_observations(after[i]))['passed']:
                        raise ValueError('new source replay glyph/graphics state differs')
                    if font_fingerprints(before,i)!=font_fingerprints(after,i):raise ValueError('new source replay font differs')
            visual=audit_render(SOURCE,path,page,directory/'following-source-replay',state['regions'][slot['region_id']]['bounds'],noop=True)
            if extraction(path,directory,'following-replay')['pages']!=source_text['pages']:raise ValueError('new source replay Unicode differs')
            result[sid]=dict(evidence='new reviewed following paragraph replay',mupdf_all_equal=visual['mupdf_page_pixels'],
                poppler_all_changed_pixels=visual['poppler_diff']['all_changed_pixels'],replay_pdf_sha256=source_sha(path))
    return result,source_text


def audit(before,after,state,initial,report,directory,prefix_suffix,*,noop=False):
    directory.mkdir(exist_ok=True);pages={}
    for key in ('flow','regions','pages','paragraph_policies','follows','paragraph_boundaries','contract_sha256'):
        if state[key]!=initial[key]:raise ValueError('paragraph relation or shared capacity contract changed')
    for pid,p in state['paragraphs'].items():
        if p['logical']['id']!=pid or p['style_registry']!=initial['paragraphs'][pid]['style_registry']:
            raise ValueError('paragraph identity or style provenance changed')
    destinations={};edited_pages={4,5}
    for step in report['steps']:
        r=step['report'];slot=state['slots'][step['slot_id']];pid=step['paragraph_id'];p=state['paragraphs'][pid]
        font_mapping_audit(after,r)
        if r['retained_glyph_count']!=0:raise ValueError('reflow must count supplied glyphs separately from source code reuse')
        ids=style_ids(p['logical']['text'],p['logical']['style_spans'],p['style_registry']);start=slot['range'][0]
        for glyph in r['glyph_plan']:
            ident=ids[start+glyph['start']];provider='logical:'+ident;attrs=p['style_registry'][ident]['attributes']
            if (glyph['provider']!=provider or glyph['style_id']!=provider
                    or glyph['size']!=attrs['font_size']['value'] or glyph['color']!=attrs['observed_color']['value']
                    or r['fonts'][provider]['source_sha256']!=p['style_registry'][ident]['reflow_provider']['sha256']):
                raise ValueError('destination glyph differs from its own paragraph style/provider')
        destinations[step['slot_id']]=dict(paragraph_id=pid,region_id=step['region_id'],style_binding=slot['style_binding'],
            destination_context=r['destination_style_binding'],provider_resources={k:v['resource'] for k,v in r['fonts'].items()},
            glyph_count=len(r['glyph_plan']),retained_source_glyph_count=r['retained_glyph_count'])
    for rid,region in state['regions'].items():
        page=region['page'];v=audit_render(before,after,page,directory/f'page-{page}',region['bounds'],noop=noop,edited_pages=edited_pages)
        pages[str(page)]=dict(poppler_outside_changed_pixels=v['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
            mupdf_outside_equal=True,all_pixels_equal=noop and v['mupdf_page_pixels'] and v['poppler_diff']['all_changed_pixels']==0)
    unchanged=[i for i in range(1,state['page_count']+1) if i not in edited_pages]
    for page in unchanged if noop else unchanged[:1]:
        v=audit_render(before,after,page,directory/f'page-{page}',(0,0,0,0),noop=True,edited_pages=edited_pages)
        pages[str(page)]=dict(poppler_all_changed_pixels=v['poppler_diff']['all_changed_pixels'],mupdf_all_equal=v['mupdf_page_pixels'])
    extracted=extraction(after,directory,'after')
    for page,expected in prefix_suffix['_unchanged'].items():
        if extracted['pages'][int(page)-1]!=expected:raise ValueError('unchanged independent page Unicode differs')
    for page in edited_pages:
        prefix,suffix=prefix_suffix[str(page)];parts=[]
        for pid in state['flow']['paragraphs']:
            for slot in state['slots'].values():
                if slot['paragraph_id']==pid and state['regions'][slot['region_id']]['page']==page:
                    parts.append(normalize(slot['binding']['paragraph']['text']))
        if normalize(extracted['pages'][page-1])!=prefix+''.join(parts)+suffix:
            raise ValueError('independent complete-page paragraph Unicode differs')
    reader_a,reader_b=PdfReader(before),PdfReader(after)
    with pymupdf.open(before) as a,pymupdf.open(after) as b:
        for i in range(len(a)):
            if i+1 in unchanged and a[i].get_pixmap(dpi=144).samples!=b[i].get_pixmap(dpi=144).samples:raise ValueError('unchanged page pixels differ')
            pa,pb=interpreted_paints(before,i+1),interpreted_paints(after,i+1)
            if pa['errors'] or pb['errors']:raise ValueError('paint observation failed')
            nontext=lambda values:[_paint_value(p) for p in values['events'] if p['kind'] not in ('fill-text','stroke-text','ignore-text')]
            if not _close(nontext(pa),nontext(pb)):raise ValueError('nontext paint changed')
            if image_fingerprints(a[i])!=image_fingerprints(b[i]):raise ValueError('image changed')
            links=lambda page:[{k:v for k,v in link.items() if k!='xref'} for link in page.get_links()]
            if links(a[i])!=links(b[i]) or annotation_fingerprint(reader_a,i+1)!=annotation_fingerprint(reader_b,i+1):raise ValueError('annotation changed')
            aliases={g['font_resource'][1:] for step in report['steps'] if state['regions'][step['region_id']]['page']==i+1 for g in step['report']['glyph_plan']}
            if [f for f in font_fingerprints(b,i) if f[0][3] not in aliases]!=font_fingerprints(a,i):raise ValueError('original page font resources changed')
    return dict(pages=pages,destinations=destinations,independent_complete_page_unicode=True,
        original_font_resources_and_nontext_paint_images_annotations_preserved=True,font_cid_gid_widths_verified=True,
        paragraph_ids=list(state['paragraphs']),paragraph_boundaries=state['paragraph_boundaries'],
        logical={pid:dict(length=len(p['logical']['text']),style_spans=p['logical']['style_spans'],typing_style_id=p['logical']['typing_style_id'],
                         hard_breaks=p['logical']['boundaries'],style_registry_sha256=digest(p['style_registry'])) for pid,p in state['paragraphs'].items()},
        fragment_allocation={sid:{k:s[k] for k in ('paragraph_id','region_id','range','render_end','occupancy')} for sid,s in state['slots'].items()},
        physical_breaks=state['physical_breaks'],schedule=report['plan']['schedule'],dependencies=report['plan']['source_occupancy_dependencies'])


def run(directory):
    engine={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    state=prepare();initial=deepcopy(state);write(directory/'initial.json',state)
    gates,source_text=source_gates(directory,state)
    prefix_suffix={'_unchanged':{str(i+1):t for i,t in enumerate(source_text['pages']) if i+1 not in (4,5)}}
    for page in (4,5):
        part=''.join(normalize(s['binding']['paragraph']['text']) for s in state['slots'].values() if state['regions'][s['region_id']]['page']==page)
        full=normalize(source_text['pages'][page-1])
        if full.count(part)!=1:raise ValueError('reviewed complete paragraph source range is not unique')
        prefix_suffix[str(page)]=full.split(part)
    original_b=deepcopy(state['paragraphs'][B]['logical']);pdf=SOURCE;stages=[]
    for n in range(1,6):
        if n==1:changes={A:replace(state,A,SHORT)}
        elif n==2:changes={A:replace(state,A,RUNS)}
        elif n==3:changes={A:dict(replace(state,A,[]),typing_style_id='latin')}
        elif n==4:changes={A:replace(state,A,RUNS),B:dict(edits=[dict(start=0,end=2,text='担当')])}
        else:changes={}
        preview=plan_shared_flow(pdf,state,changes);write(directory/f'v{n}-plan.json',preview)
        write(directory/'progress.json',dict(stage=n,action='mutating verified source slots'))
        out,sidecar=directory/f'v{n}.pdf',directory/f'v{n}.json'
        report=edit_shared_flow(pdf,state,out,sidecar,changes);write(directory/f'v{n}-report.json',report)
        if report['plan']!=preview:raise ValueError('executed allocation differs from preview')
        opened=open_shared_flow(out,sidecar)
        if opened['status']!='restored':raise ValueError(opened['reason'])
        updated=opened['state']
        if n<=3 and updated['paragraphs'][B]['logical']!=original_b:raise ValueError('unchanged follower lost independent logical content')
        proof=audit(pdf,out,updated,initial,report,directory/f'v{n}-audit',prefix_suffix,noop=n==5)
        record=dict(stage=f'v{n}',status='passed',output_sha256=source_sha(out),**proof)
        stages.append(record);write(directory/'stages.json',stages);print(json.dumps(dict(stage=n,status='passed',logical=proof['logical'])),flush=True)
        pdf,state=out,updated
    negative=[]
    for name,mutation in [('unconfirmed_spacing',lambda s:s['follows'][0].update(minimum_baseline_gap='unknown')),
                          ('merged_paragraph_identity',lambda s:s['paragraphs'][B]['logical'].update(id=A)),
                          ('changed_provider',lambda s:s['paragraphs'][B]['style_registry']['body']['reflow_provider'].update(sha256='0'*64))]:
        bad=deepcopy(state);mutation(bad);bad=_reseal(bad)
        negative.append(refuse(directory,name,lambda o,j:edit_shared_flow(pdf,bad,o,j,{})))
    negative.append(refuse(directory,'shared_capacity_exhausted',lambda o,j:edit_shared_flow(pdf,state,o,j,
        {B:replace(state,B,[dict(text='確認します。\n'*8+'終了。',style_id='body')])})))
    if engine!={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}:raise ValueError('engine changed during external evaluation')
    return dict(schema='pdfengine-shared-flow-evaluation-1',status='passed',source_url=URL,source_sha256=SOURCE_SHA,
        source_replay=gates,stages=stages,negative_controls=negative,paragraph_policies=initial['paragraph_policies'],
        follows=initial['follows'],regions=initial['regions'],page_policy=initial['pages'],
        style_registries={pid:p['style_registry'] for pid,p in initial['paragraphs'].items()},
        scope='two explicitly reviewed paragraphs sharing existing page-4/page-5 body regions; third paragraph remains fixed',
        environment=dict(pymupdf=pymupdf.VersionBind,engine_sha256=engine,runner_sha256=source_sha(Path(__file__)),
            evaluation_dependencies_sha256={str(p.relative_to(ROOT)).replace('\\','/'):source_sha(p) for p in
                [ROOT/'evaluations/story_flow/evaluate.py',ROOT/'evaluations/story_styles/evaluate.py',ROOT/'evaluations/elements/evaluate.py',
                 ROOT/'evaluations/flow_transaction/evaluate.py',ROOT/'evaluations/attributed/evaluate.py',ROOT/'evaluations/backend/followup.py',ROOT/'evaluations/realpdf/evaluate.py']}))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True)
    write(directory/'summary.json',run(directory))


if __name__=='__main__':main()
