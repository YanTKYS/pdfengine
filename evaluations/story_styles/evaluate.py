"""External mixed-style paragraph: global styles, destination-specific bindings."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import pymupdf

from pdfeditor.attributed import inspect_paragraph,digest
from pdfeditor.content_stream import ContentPage,patch_streams
from pdfeditor.document_flow import _reseal
from pdfeditor.pdf_save import font_fingerprints,program_pdf_bytes
from pdfeditor.replay import glyph_observations,compare_glyphs
from pdfeditor.selection import make_selection,source_sha
from pdfeditor.story_flow import confirm_story,edit_story,open_story,plan_story
from pdfeditor.story_styles import style_ids
from evaluations.elements.evaluate import audit_render,extraction
from evaluations.flow_transaction.evaluate import refuse,normalize
from evaluations.story_flow.evaluate import audit,SOURCE,SOURCE_SHA,URL


ROOT=Path(__file__).resolve().parents[2];BASE=Path(__file__).resolve().parent
RUNS=[dict(text='文章の書式と配置先を確認します。',style_id='body'),
      dict(text='PDF editing preserves one logical style across the confirmed page boundary, while every destination receives its own verified font binding. ',style_id='latin'),
      dict(text='保存後も文章と書式の対応を維持し、次の編集へ引き継ぎます。',style_id='body')]
SHORT=[dict(text='書式を確認。',style_id='body'),dict(text='PDF 2026',style_id='latin')]


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


def prepare():
    if source_sha(SOURCE)!=SOURCE_SHA:raise ValueError('reviewed source changed')
    specs={}
    for ident,page,ids,width,bottom,bounds in [('A',4,list(range(1203,1254)),471,785,[66,758,540,785]),
                                            ('B',5,list(range(114)),482,194,[55,130,540,194])]:
        p=inspect_paragraph(SOURCE,make_selection(SOURCE,page,glyph_ids=ids,explicit_width=width))
        layout=p['layout_suggestion']
        specs[ident]=dict(page=page,bounds=bounds,paragraph=p,paint_relations=[],
            layout=dict(x=layout['x'],baseline=layout['baseline'],width=width,max_bottom=bottom,
                        min_line_height=21.6,first_line_indent=0))
    return confirm_story(SOURCE,specs,paragraph_id='reviewed-mixed-training-paragraph',chain=['A','B'],
        styles={'body':dict(provider=dict(path='C:/Windows/Fonts/msmincho.ttc',font_index=1),provider_relation='confirmed_reflow_provider'),
                'latin':dict(provider=dict(path='C:/Windows/Fonts/times.ttf'),provider_relation='confirmed_reflow_provider')},
        style_assignments={'A':{'s0':'body','s1':'body'},'B':{'s0':'body','s1':'body','s2':'latin'}},
        typing_style_id='body',protected_regions={str(i):[dict(role='fixed',bounds=[398,58,540,111])] for i in (4,5)})


def replace(state,runs):return [dict(start=0,end=len(state['logical']['text']),runs=runs)]


def source_gates(directory,state):
    # The exact A selection and all replay-related engine modules are unchanged.
    previous=read(ROOT/'evaluations/story_flow/summary.json')
    for name in ('content_stream.py','pdf_save.py','replay.py','selection.py'):
        if source_sha(ROOT/'pdfeditor'/name)!=previous['environment']['engine_sha256'][name]:
            raise ValueError('old source replay evidence is no longer reusable')
    old_root=ROOT/'evaluations/story_flow/runs/final-lo';old=read(old_root/'initial.json')
    if old['fragments']['A']['binding']['paragraph']!=state['fragments']['A']['binding']['paragraph']:
        raise ValueError('A source selection differs from previously verified replay')
    a=old_root/'A-source-replay.pdf'
    # Bind the retained artifact to the source program, then reuse its published
    # renderer result. This is not counted as a newly executed renderer gate.
    content=ContentPage(SOURCE,4);saved=ContentPage(a,4)
    try:
        selected=set(old['fragments']['A']['binding']['paragraph']['selection']['glyph_ids'])
        program=patch_streams(content,content.selected_events(selected),selected,remove=False)[-content.page.xref]
        if saved.streams[-saved.page.xref]!=program:raise ValueError('retained A replay program changed')
    finally:content.close();saved.close()
    gates={'A':dict(**previous['source_replay']['A'],evidence='reused verified identical source selection/program',
                    replay_pdf_sha256=source_sha(a),prior_commit='6690eae')}
    b=state['fragments']['B']['binding'];content=ContentPage(SOURCE,5)
    try:
        selected=set(b['paragraph']['selection']['glyph_ids'])
        program=patch_streams(content,content.selected_events(selected),selected,remove=False)[-content.page.xref]
        target=directory/'B-source-replay.pdf';target.write_bytes(program_pdf_bytes(SOURCE,4,program))
    finally:content.close()
    with pymupdf.open(SOURCE) as before,pymupdf.open(target) as after:
        for i in range(len(before)):
            if not compare_glyphs(glyph_observations(before[i]),glyph_observations(after[i]))['passed']:
                raise ValueError('mixed source replay glyph or paint state differs')
            if font_fingerprints(before,i)!=font_fingerprints(after,i):raise ValueError('mixed source replay font resource differs')
    visual=audit_render(SOURCE,target,5,directory/'B-source-replay',state['containers']['B']['bounds'],noop=True)
    original=extraction(SOURCE,directory,'source')
    if extraction(target,directory,'B-source-replay')['pages']!=original['pages']:raise ValueError('mixed source replay Unicode differs')
    gates['B']=dict(mupdf_all_equal=visual['mupdf_page_pixels'],poppler_all_changed_pixels=visual['poppler_diff']['all_changed_pixels'],
                    evidence='new mixed-source operator replay',replay_pdf_sha256=source_sha(target))
    return gates,original


def style_audit(state,initial,report):
    for key in ('style_registry','flow_chain','containers','pages'):
        if state[key]!=initial[key]:raise ValueError('logical style provenance or confirmed flow policy changed')
    if state['logical']['id']!=initial['logical']['id']:raise ValueError('logical paragraph identity changed')
    ids=style_ids(state['logical']['text'],state['logical']['style_spans'],state['style_registry'])
    crossing=[]
    for cut in state['physical_breaks']:
        i=cut['offset']
        if 0<i<len(ids) and ids[i-1]==ids[i]:crossing.append(dict(offset=i,style_id=ids[i]))
    mappings={}
    for step in report['steps']:
        r=step['report'];fragment=state['fragments'][step['container_id']];a=fragment['range'][0]
        if 'destination_style_binding' not in r:raise ValueError('missing destination graphics context proof')
        if r['retained_glyph_count']!=0:raise ValueError('story regeneration must not claim original code retention')
        for g in r['glyph_plan']:
            logical=ids[a+g['start']]
            if g['style_id']!='logical:'+logical or g['provider']!=g['style_id']:
                raise ValueError('glyph was rebound to the wrong logical style/provider')
            attrs=state['style_registry'][logical]['attributes']
            if g['size']!=attrs['font_size']['value'] or g['color']!=attrs['observed_color']['value']:
                raise ValueError('output glyph lost its logical inline attributes')
            provider=state['style_registry'][logical]['reflow_provider']
            if r['fonts'][g['provider']]['source_sha256']!=provider['sha256']:
                raise ValueError('font resource came from another style provider')
        mappings[step['container_id']]=dict(style_binding=fragment['style_binding'],
            glyph_count=len(r['glyph_plan']),provided_glyph_count=r['provided_font_glyph_count'],
            resource_by_provider={k:v['resource'] for k,v in r['fonts'].items()},
            destination_context=r['destination_style_binding'])
    return dict(style_registry_sha256=digest(state['style_registry']),style_spans=state['logical']['style_spans'],
        typing_style_id=state['logical']['typing_style_id'],styles_crossing_physical_boundaries=crossing,
        destinations=mappings,logical_style_and_provider_and_context_verified=True)


def run(directory):
    state=prepare();initial=deepcopy(state);write(directory/'initial.json',state)
    gates,source_text=source_gates(directory,state);prefix_suffix={}
    prefix_suffix['_unchanged']={str(i+1):t for i,t in enumerate(source_text['pages']) if i+1 not in (4,5)}
    for ident,page in [('A',4),('B',5)]:
        part=normalize(state['fragments'][ident]['binding']['paragraph']['text']);full=normalize(source_text['pages'][page-1])
        if full.count(part)!=1:raise ValueError('reviewed source paragraph is not independently unique')
        prefix_suffix[ident]=full.split(part)
    pdf=SOURCE;stages=[]
    for n in range(1,7):
        options={}
        if n in (1,5):edits=replace(state,RUNS)
        elif n==2:
            start=state['logical']['style_spans'][1]['start']-2;cut=state['fragments']['A']['range'][1]
            edits=[dict(start=start,end=cut+2,runs=[dict(text='書式をまたぐ編集を確認します。',style_id='body'),
                dict(text='Verified rich replacement crosses both style and page boundaries. ',style_id='latin')])]
        elif n==3:edits=replace(state,SHORT)
        elif n==4:edits=replace(state,[]);options['typing_style_id']='latin'
        else:edits=[]
        preview=plan_story(pdf,state,edits,**options)
        write(directory/'progress.json',dict(stage=n,action='source mutation'))
        out,sidecar=directory/f'v{n}.pdf',directory/f'v{n}.json'
        report=edit_story(pdf,state,out,sidecar,edits,**options);write(directory/f'v{n}-report.json',report)
        if preview!=report['plan']:raise ValueError('preview and executed plan differ')
        opened=open_story(out,sidecar)
        if opened['status']!='restored':raise ValueError(opened['reason'])
        updated=opened['state'];semantic=style_audit(updated,initial,report)
        if n in (1,5) and not any(c['style_id']=='latin' for c in semantic['styles_crossing_physical_boundaries']):
            raise ValueError('evaluation did not move one Latin style across the page boundary')
        proof=audit(pdf,out,updated,report,directory/f'v{n}-audit',prefix_suffix,noop=n==6)
        record=dict(stage=f'v{n}',status='passed',output_sha256=source_sha(out),**semantic,**proof)
        stages.append(record);write(directory/'stages.json',stages);print(json.dumps(record),flush=True)
        pdf,state=out,updated
    boundary=state['logical']['style_spans'][1]['start']
    negatives=[refuse(directory,'ambiguous_cross_style',lambda o,s:edit_story(pdf,state,o,s,[dict(start=boundary-1,end=boundary+2,text='X')])),
        refuse(directory,'ambiguous_style_boundary_insertion',lambda o,s:edit_story(pdf,state,o,s,[dict(start=boundary,end=boundary,text='X')])),
        refuse(directory,'all_regions_exhausted',lambda o,s:edit_story(pdf,state,o,s,replace(state,RUNS*2)))]
    bad=deepcopy(state);bad['fragments']['A']['style_binding']['styles']={k:'body' for k in bad['fragments']['A']['style_binding']['styles']};bad=_reseal(bad)
    negatives.append(refuse(directory,'wrong_destination_style_binding',lambda o,s:edit_story(pdf,bad,o,s,[])))
    bad=deepcopy(state);bad['style_registry']['latin']['reflow_provider']['sha256']='0'*64;bad=_reseal(bad)
    negatives.append(refuse(directory,'changed_style_provider',lambda o,s:edit_story(pdf,bad,o,s,[])))
    return dict(schema='pdfengine-story-styles-evaluation-1',status='passed',source_url=URL,source_sha256=SOURCE_SHA,
        source_logical_length=len(initial['logical']['text']),source_style_spans=initial['logical']['style_spans'],
        source_replay=gates,stages=stages,negative_controls=negatives,
        logical_style_registry=initial['style_registry'],container_contract=initial['containers'],page_policy=initial['pages'],
        scope='one complete reviewed mixed-style paragraph across existing page-4/page-5 regions; same logical style spans may cross a physical page boundary',
        environment=dict(pymupdf=pymupdf.VersionBind,runner_sha256=source_sha(Path(__file__)),
            evaluation_dependencies_sha256={p:source_sha(ROOT/p) for p in ['evaluations/story_flow/evaluate.py','evaluations/elements/evaluate.py','evaluations/backend/followup.py','evaluations/attributed/evaluate.py']},
            engine_sha256={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True)
    write(directory/'summary.json',run(directory))


if __name__=='__main__':main()
