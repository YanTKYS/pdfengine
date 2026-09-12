"""One reviewed external sentence, two existing page regions, one logical ID."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pymupdf
from pypdf import PdfReader
from pypdf.generic import IndirectObject,StreamObject,NullObject,BooleanObject

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.document_flow import _reseal
from pdfeditor.content_stream import ContentPage,patch_streams
from pdfeditor.elements import _close,_paint_value
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.pdf_save import font_fingerprints,program_pdf_bytes
from pdfeditor.replay import glyph_observations,compare_glyphs
from pdfeditor.selection import make_selection,source_sha
from pdfeditor.story_flow import confirm_story,edit_story,open_story,plan_story
from evaluations.attributed.evaluate import font_mapping_audit
from evaluations.elements.evaluate import audit_render,extraction
from evaluations.flow_transaction.evaluate import refuse,normalize
from evaluations.realpdf.evaluate import image_fingerprints


ROOT=Path(__file__).resolve().parents[2];BASE=Path(__file__).resolve().parent
SOURCE=ROOT/'evaluations/realpdf/corpus/lo_migration_ja.pdf'
SOURCE_SHA='13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3'
URL='https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf'
LONG=('文章全体の編集範囲を確認し、PDF 2026 の保存後も一つの文章として扱います。'
      '内容が最初の領域に収まらない場合は、明示した次領域へ続けて配置します。')
SHORT='PDF 2026 の文章と保存結果を確認してください。'


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


def annotation_fingerprint(reader,page):
    """Compare annotation graphs by content and page identity, not xref numbers.

    Preserve sharing/cycles through deterministic local references; appearance
    streams compare decoded bytes and all semantic dictionary entries.
    """
    page_ids={(p.indirect_reference.idnum,p.indirect_reference.generation):i+1 for i,p in enumerate(reader.pages)}
    references={};definitions=[]
    def value(obj):
        if isinstance(obj,IndirectObject):
            key=(obj.idnum,obj.generation)
            if key in page_ids:return {'page':page_ids[key]}
            if key not in references:
                references[key]=len(definitions);definitions.append(None)
                definitions[references[key]]=value(obj.get_object())
            return {'ref':references[key]}
        if isinstance(obj,StreamObject):
            return {'entries':{str(k):value(v) for k,v in sorted(obj.items()) if k not in ('/Length','/Filter','/DecodeParms')},
                    'decoded_sha256':hashlib.sha256(obj.get_data()).hexdigest()}
        if isinstance(obj,dict):return {str(k):value(v) for k,v in sorted(obj.items())}
        if isinstance(obj,(list,tuple)):return [value(v) for v in obj]
        if isinstance(obj,bytes):return {'bytes':obj.hex()}
        if isinstance(obj,NullObject):return None
        if isinstance(obj,BooleanObject):return bool(obj.value)
        if isinstance(obj,(str,int,float,bool)) or obj is None:return obj
        raise ValueError('unknown annotation object cannot be compared')
    root=value(reader.pages[page-1].get('/Annots',[]))
    return dict(root=root,objects=definitions)


def prepare(*,whole_source=False):
    if source_sha(SOURCE)!=SOURCE_SHA:raise ValueError('reviewed source changed')
    specs={}
    for ident,page,ids,width,bottom,bounds in [('A',4,list(range(1203,1254)),471,785,[66,758,540,785]),
                                            ('B',5,list(range(36)),339.2,151,[55,130,396,151])]:
        if whole_source and ident=='B':ids,width,bottom,bounds=list(range(114)),482,194,[55,130,540,194]
        p=inspect_paragraph(SOURCE,make_selection(SOURCE,page,glyph_ids=ids,explicit_width=width))
        suggestion=p['layout_suggestion']
        specs[ident]=dict(page=page,bounds=bounds,paragraph=p,paint_relations=[],
            layout=dict(x=suggestion['x'],baseline=suggestion['baseline'],width=width,max_bottom=bottom,
                        min_line_height=21.6,first_line_indent=0))
    return confirm_story(SOURCE,specs,paragraph_id='reviewed-training-sentence',chain=['A','B'],
        font={'path':'C:/Windows/Fonts/msmincho.ttc','font_index':1},
        protected_regions={str(i):[dict(role='fixed',bounds=[398,58,540,111])] for i in (4,5)})


def request(state,text):return [dict(start=0,end=len(state['logical']['text']),text=text)]


def audit(before,after,state,report,directory,prefix_suffix,*,noop=False):
    directory.mkdir(exist_ok=True);pages={};edited_pages={step['page'] for step in report['steps']}
    for step in report['steps']:
        r=step['report'];font_mapping_audit(after,r)
        page=step['page'];area=tuple(r['audit_bbox'][k] for k in ('x0','y0','x1','y1'))
        visual=audit_render(before,after,page,directory/f'page-{page}',area,noop=noop,edited_pages=edited_pages)
        pages[str(page)]=dict(poppler_outside_changed_pixels=visual['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
            mupdf_outside_equal=True,all_pixels_equal=noop and visual['mupdf_page_pixels'] and visual['poppler_diff']['all_changed_pixels']==0)
    # One unchanged external page per stage; all eight in the final audit.
    unchanged=[i for i in range(1,state['page_count']+1) if str(i) not in pages]
    for page in unchanged if noop else unchanged[:1]:
        stable=audit_render(before,after,page,directory/f'page-{page}',(0,0,0,0),noop=True,edited_pages=edited_pages)
        pages[str(page)]=dict(poppler_all_changed_pixels=stable['poppler_diff']['all_changed_pixels'],mupdf_all_equal=stable['mupdf_page_pixels'])
    extracted=extraction(after,directory,'after')
    if len(extracted['pages'])!=state['page_count']:raise ValueError('page count changed')
    for page,expected in prefix_suffix['_unchanged'].items():
        if extracted['pages'][int(page)-1]!=expected:raise ValueError('independent Unicode on an unchanged page differs')
    for ident,page in [('A',4),('B',5)]:
        prefix,suffix=prefix_suffix[ident]
        expected=prefix+normalize(state['fragments'][ident]['binding']['paragraph']['text'])+suffix
        if normalize(extracted['pages'][page-1])!=expected:raise ValueError('complete-page independent Unicode mismatch')
    original_reader,edited_reader=PdfReader(before),PdfReader(after)
    with pymupdf.open(before) as a,pymupdf.open(after) as b:
        for i in range(len(a)):
            if i+1 in unchanged and a[i].get_pixmap(dpi=144).samples!=b[i].get_pixmap(dpi=144).samples:
                raise ValueError('unchanged external page pixels differ')
            pa,pb=interpreted_paints(before,i+1),interpreted_paints(after,i+1)
            if pa['errors'] or pb['errors']:raise ValueError('nontext paint observation failed')
            nontext=lambda values:[_paint_value(p) for p in values['events'] if p['kind'] not in ('fill-text','stroke-text','ignore-text')]
            if not _close(nontext(pa),nontext(pb)):raise ValueError('fixed nontext paint changed')
            if image_fingerprints(a[i])!=image_fingerprints(b[i]):raise ValueError('image changed')
            links=lambda page:[{k:v for k,v in link.items() if k!='xref'} for link in page.get_links()]
            if (links(a[i])!=links(b[i]) or annotation_fingerprint(original_reader,i+1)!=annotation_fingerprint(edited_reader,i+1)):
                raise ValueError('link or annotation changed')
            old=font_fingerprints(a,i);new=font_fingerprints(b,i)
            aliases={g['font_resource'][1:] for step in report['steps'] if step['page']==i+1 for g in step['report']['glyph_plan']}
            if [f for f in new if f[0][3] not in aliases]!=old:raise ValueError('existing page font resources changed')
    return dict(pages=pages,independent_complete_page_unicode=True,font_cid_gid_widths=True,
        existing_page_font_resources_preserved=True,fixed_nontext_paint_images_annotations_preserved=True,
        logical_hard_break_count=len(state['logical']['boundaries']),logical_length=len(state['logical']['text']),
        physical_ranges={i:f['range'] for i,f in state['fragments'].items()},physical_breaks=state['physical_breaks'])


def run(directory,*,resume_stage_1=False):
    state=prepare()
    if resume_stage_1 and read(directory/'initial.json')!=state:raise ValueError('resume input model differs')
    write(directory/'initial.json',state);initial=deepcopy(state)
    source_text=extraction(SOURCE,directory,'source');prefix_suffix={};gates={}
    prefix_suffix['_unchanged']={str(i+1):t for i,t in enumerate(source_text['pages']) if i+1 not in (4,5)}
    for ident in ['A','B']:
        b=state['fragments'][ident]['binding'];page=state['containers'][ident]['page'];part=normalize(b['paragraph']['text'])
        full=normalize(source_text['pages'][page-1])
        if full.count(part)!=1:raise ValueError('source contact range is not independently unique')
        prefix_suffix[ident]=full.split(part)
        target=directory/f'{ident}-source-replay.pdf'
        content=ContentPage(SOURCE,page)
        try:
            selected=set(b['paragraph']['selection']['glyph_ids'])
            replay=patch_streams(content,content.selected_events(selected),selected,remove=False)[-content.page.xref]
            if not resume_stage_1:target.write_bytes(program_pdf_bytes(SOURCE,page-1,replay))
            else:
                restored=ContentPage(target,page)
                try:
                    if restored.streams[-restored.page.xref]!=replay:raise ValueError('cached replay program differs')
                finally:restored.close()
        finally:content.close()
        with pymupdf.open(SOURCE) as before,pymupdf.open(target) as after:
            for i in range(len(before)):
                if not compare_glyphs(glyph_observations(before[i]),glyph_observations(after[i]))['passed']:
                    raise ValueError('source replay glyph identity, origin or paint differs')
                if font_fingerprints(before,i)!=font_fingerprints(after,i):raise ValueError('source replay font resources changed')
        gate=audit_render(SOURCE,target,page,directory/f'{ident}-source-replay',state['containers'][ident]['bounds'],noop=True)
        if extraction(target,directory,ident+'-replay')['pages']!=source_text['pages']:raise ValueError('source replay Unicode mismatch')
        gates[ident]=dict(mupdf_all_equal=gate['mupdf_page_pixels'],poppler_all_changed_pixels=gate['poppler_diff']['all_changed_pixels'])
    pdf=SOURCE;stages=[]
    for n in range(1,7):
        if n in (1,5):edits=request(state,LONG)
        elif n==2:
            cut=state['fragments']['A']['range'][1]
            edits=[dict(start=cut-2,end=cut+2,text='PDF 2026')]
        elif n==3:edits=request(state,SHORT)
        elif n==4:edits=request(state,'')
        else:edits=[]
        preview=plan_story(pdf,state,edits);out,sidecar=directory/f'v{n}.pdf',directory/f'v{n}.json'
        write(directory/'progress.json',dict(stage=n,action='source mutation'))
        if n==1 and resume_stage_1:
            report=read(directory/'v1-report.json')
            if report['pdf_sha256']!=source_sha(out):raise ValueError('cached first-stage PDF revision differs')
        else:
            report=edit_story(pdf,state,out,sidecar,edits);write(directory/f'v{n}-report.json',report)
        if preview!=report['plan']:raise ValueError('preview and executed plan differ')
        opened=open_story(out,sidecar)
        if opened['status']!='restored':raise ValueError(opened['reason'])
        updated=opened['state']
        if (updated['logical']['id']!=initial['logical']['id'] or updated['flow_chain']!=initial['flow_chain']
                or updated['containers']!=initial['containers'] or updated['pages']!=initial['pages']):raise ValueError('confirmed identity or policy changed')
        proof=audit(pdf,out,updated,report,directory/f'v{n}-audit',prefix_suffix,noop=n==6)
        record=dict(stage=f'v{n}',status='passed',output_sha256=source_sha(out),**proof)
        stages.append(record);write(directory/'stages.json',stages);print(json.dumps(record),flush=True)
        pdf,state=out,updated
    negatives=[refuse(directory,'all_regions_exhausted',lambda o,s:edit_story(pdf,state,o,s,request(state,LONG*3))),
               refuse(directory,'heterogeneous_source_paragraph',lambda o,s:prepare(whole_source=True))]
    bad=deepcopy(state);bad['flow_chain']['provenance']='inferred_from_coordinates';bad=_reseal(bad)
    negatives.append(refuse(directory,'inferred_continuation',lambda o,s:edit_story(pdf,bad,o,s,request(bad,SHORT))))
    bad=deepcopy(state);bad['pages']['5']['protected_regions'].append(dict(role='header',bounds=[55,130,396,151],provenance='explicitly_confirmed'));bad=_reseal(bad)
    negatives.append(refuse(directory,'protected_continuation_region',lambda o,s:edit_story(pdf,bad,o,s,request(bad,SHORT))))
    negatives.append(refuse(directory,'external_revision',lambda o,s:edit_story(pdf,initial,o,s,request(initial,SHORT))))
    return dict(schema='pdfengine-story-flow-evaluation-1',status='passed',source_url=URL,source_sha256=SOURCE_SHA,
        paragraph_id=initial['logical']['id'],source_replay=gates,stages=stages,negative_controls=negatives,
        container_contract={i:{k:v for k,v in c.items() if k!='layout'} for i,c in initial['containers'].items()},
        flow_chain=initial['flow_chain'],page_policy=initial['pages'],font_sha256=initial['font_recipe']['sha256'],font_index=1,
        scope='one complete source sentence explicitly confirmed as one logical paragraph across two fixed one-line regions on pages 4/5; other eight pages unchanged',
        environment=dict(pymupdf=pymupdf.VersionBind,runner_sha256=source_sha(Path(__file__)),
            evaluation_dependencies_sha256={p:source_sha(ROOT/p) for p in ['evaluations/backend/followup.py','evaluations/elements/evaluate.py','evaluations/attributed/evaluate.py']},
            engine_sha256={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True)
    parser.add_argument('--resume-stage-1',action='store_true');args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True,exist_ok=args.resume_stage_1)
    write(directory/'summary.json',run(directory,resume_stage_1=args.resume_stage_1))


if __name__=='__main__':main()
