"""External shared note with explicitly confirmed straight-band frame resizing."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import pymupdf

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.document_flow import confirm_document, _reseal
from pdfeditor.elements import _close, _paint_value, inspect_element
from pdfeditor.flow_transaction import edit_flow_batch
from pdfeditor.model import Rect
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.paint_resize import MODEL, resize_paints
from pdfeditor.selection import make_selection,source_sha
from pdfeditor.proof_session import proof_session
from pdfeditor.variable_container import (confirm_variable_container,edit_variable_container,
                                          open_variable_container,plan_variable_container)
from evaluations.attributed.evaluate import font_mapping_audit
from evaluations.elements.evaluate import audit_render,extraction
from evaluations.flow_transaction.evaluate import refuse,normalize
from evaluations.realpdf.evaluate import image_fingerprints


ROOT=Path(__file__).resolve().parents[2];BASE=Path(__file__).resolve().parent
SOURCE=ROOT/'evaluations/realpdf/corpus/print_fcc_ms.pdf'
SOURCE_SHA='11ad813fce341afed902f98f7c5335f346bd9e746c648a509c1c7f9ba4f7f3fd'
LONG='Adhesive for plastic material.\nCheck PDF 2026 before printing.\nKeep the label clean.\nConfirm the saved document.\nReview the font and spacing.\nConfirm the final document.'
SHORT='Check PDF 2026.'


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path,value):path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))


@proof_session
def prepare():
    if source_sha(SOURCE)!=SOURCE_SHA:raise ValueError('reviewed source changed')
    specs={}
    for ident,ids in [('H',range(257,376)),('A',range(376,477)),('B',range(477,509))]:
        p=inspect_paragraph(SOURCE,make_selection(SOURCE,glyph_ids=list(ids),explicit_width=196))
        e=inspect_element(SOURCE,p['selection']);relations=[]
        for path in e['paths']:
            seq=path['proof'].get('paint_seqnos')
            if seq not in ([37],[38],[40],[42],[44]):continue
            relation='backgrounds' if seq==[37] else 'borders' if seq==[38] else 'decorates' if ident=='H' else 'unrelated'
            relations.append(dict(source_id=path['source_id'],relation=relation,behavior='fixed-to-page'))
        specs[ident]=dict(paragraph=p,layout=dict(width=196,max_bottom=155),
            fonts={s['id']:{'path':'C:/Windows/Fonts/arial.ttf'} for s in p['styles']},paint_relations=relations)
    gap=specs['B']['paragraph']['layout_suggestion']['baseline']-specs['A']['paragraph']['layout_suggestion']['base_baselines'][-1]
    document=confirm_document(SOURCE,specs,container_id='confirmed-shared-note',bounds=[565,66,772,155],page=1,
                              follows=[dict(before='A',after='B',gap=gap)])
    paths=document['elements']['H']['binding']['element']['paths']
    paints=[dict(source_id=p['source_id'],role='backgrounds' if p['proof']['paint_seqnos']==[37] else 'borders',
                 band=[100,150],geometry_model=MODEL,bottom_anchor='preserve-offset-to-container-bottom')
            for p in paths if p['proof'].get('paint_seqnos') in ([37],[38])]
    model=confirm_variable_container(SOURCE,document,paints=paints,min_bottom=155,max_bottom=220,baseline_bottom_gap=4,
                                    available_paint_region=[563,64,775,222],fixed_children=['H'])
    return model


def changes(model,texts):
    return {i:dict(edits=[] if t is None else [dict(start=0,end=len(model['document']['elements'][i]['binding']['paragraph']['text']),
        text=t,style_id='s0')],empty_style_id='s0') for i,t in texts.items()}


def restored(pdf,model):
    r=open_variable_container(pdf,model)
    if r['status']!='restored':raise ValueError(r['reason'])
    return r['state']


def audit(before,after,before_model,state,report,directory,prefix,suffix,*,noop=False):
    directory.mkdir();area=None
    for step in report['steps']:
        reports=([step['report']] if step['kind']=='resize' else
            [s['report'] for t in step['report']['transactions'] for s in t['report']['steps']])
        for r in reports:
            box=Rect(**r['audit_bbox']);area=box if area is None else area.union(box)
            if 'glyph_plan' in r:font_mapping_audit(after,r)
    visual=audit_render(before,after,1,directory/'render',area.tuple(),noop=noop)
    text=extraction(after,directory,'after')
    expected=prefix+''.join(normalize(state['document']['elements'][i]['binding']['paragraph']['text']) for i in ('A','B'))+suffix
    if len(text['pages'])!=1 or normalize(text['pages'][0])!=expected:raise ValueError('independent complete-page text mismatch')
    old=interpreted_paints(before,1);new=interpreted_paints(after,1)
    if old['errors'] or new['errors']:raise ValueError('paint observation failed')
    a=[_paint_value(p) for p in old['events'] if p['kind'] not in ('fill-text','stroke-text','ignore-text')]
    b=[_paint_value(p) for p in new['events'] if p['kind'] not in ('fill-text','stroke-text','ignore-text')]
    expected_paints=report['plan']['paint_plans']
    initial_paths=before_model['document']['elements']['H']['binding']['element']['paths']
    changed=0
    for path in initial_paths:
        if path['source_id'] not in expected_paints:continue
        original=_paint_value(path['proof']['paints'][0])
        matches=[i for i,value in enumerate(a) if _close(value,original)]
        if len(matches)!=1:raise ValueError('container paint correspondence is ambiguous')
        a[matches[0]]=expected_paints[path['source_id']]['expected'];changed+=1
    if changed!=2 or not _close(a,b):raise ValueError('paint differs beyond the two planned container paths')
    with pymupdf.open(before) as x,pymupdf.open(after) as y:
        if any(image_fingerprints(x[i])!=image_fingerprints(y[i]) for i in range(len(x))):raise ValueError('image changed')
    if before_model['fixed_children']!=state['fixed_children']:raise ValueError('fixed French content or anchor changed')
    return dict(poppler_outside_changed_pixels=visual['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        mupdf_outside_pixels_equal=True,independent_complete_page_text=True,font_cid_gid_widths=True,
        only_confirmed_paint_geometry_changed=True,images_equal=True,fixed_french_content_and_anchor=True,
        noop_poppler_all_changed_pixels=visual['poppler_diff']['all_changed_pixels'] if noop else None,
        noop_mupdf_all_pixels_equal=visual['mupdf_page_pixels'] if noop else None,other_pages='none in this one-page source')


def run(directory):
    prior=read(ROOT/'evaluations/ink_collision/summary.json')
    if prior['source_sha256']!=SOURCE_SHA or any(g['poppler_all_changed_pixels'] or not g['mupdf_all_pixels_equal'] for g in prior['source_replay'].values()):
        raise ValueError('original A/B source replay evidence is unavailable')
    state=prepare();write(directory/'initial.json',state)
    initial=deepcopy(state);text=extraction(SOURCE,directory,'source')
    original=''.join(normalize(state['document']['elements'][i]['binding']['paragraph']['text']) for i in ('A','B'))
    page=normalize(text['pages'][0])
    if page.count(original)!=1:raise ValueError('reviewed A/B interval is not unique')
    prefix,suffix=page.split(original)
    h=state['document']['elements']['H']['binding'];noop=directory/'paint-noop.pdf'
    gate=resize_paints(SOURCE,noop,h['element'],state['paints'],delta=0,available_bounds=state['layout_policy']['available_paint_region'])
    write(directory/'paint-noop-report.json',gate)
    visual=audit_render(SOURCE,noop,1,directory/'paint-noop-render',tuple(gate['audit_bbox'].values()),noop=True)
    if extraction(noop,directory,'paint-noop')['pages']!=text['pages']:raise ValueError('paint replay changed text')
    negatives=[refuse(directory,'fixed_container_growth',lambda o,s:edit_flow_batch(SOURCE,initial['document'],o,s,changes(initial,{'A':LONG})))]
    pdf=SOURCE;stages=[]
    for n,texts in enumerate([{'A':LONG},{'A':SHORT},{'A':'','B':''},{'A':LONG,'B':'Example : PDF 2026'},{'A':None,'B':None}],1):
        request=changes(state,texts);plan=plan_variable_container(pdf,state,request)
        out,sidecar=directory/f'v{n}.pdf',directory/f'v{n}.json'
        write(directory/'progress.json',dict(stage=f'v{n}',target=out.name))
        report=edit_variable_container(pdf,state,out,sidecar,request);write(directory/f'v{n}-report.json',report)
        if report['plan']!=plan:raise ValueError('preview differs from execution plan')
        updated=restored(out,sidecar)
        if (updated['layout_policy']!=state['layout_policy'] or updated['semantic_owner']!=state['semantic_owner']
                or updated['document']['follows']!=state['document']['follows']):raise ValueError('semantic or resize policy changed')
        proof=audit(pdf,out,state,updated,report,directory/f'v{n}-audit',prefix,suffix,noop=n==5)
        record=dict(stage=f'v{n}',status='passed',delta=plan['delta'],content_bottom=plan['final_bottom'],
            execution=plan['execution'],empty_elements=[i for i,e in updated['document']['elements'].items() if not e['binding']['paragraph']['text']],
            output_sha256=source_sha(out),**proof)
        stages.append(record);write(directory/'stages.json',stages);print(json.dumps(record),flush=True)
        pdf,state=out,updated
    negatives.append(refuse(directory,'maximum_region',lambda o,s:edit_variable_container(pdf,state,o,s,changes(state,{'A':LONG+'\nMore.'*12}))))
    negatives.append(refuse(directory,'fixed_french_child',lambda o,s:edit_variable_container(pdf,state,o,s,changes(state,{'H':'Changed'}))))
    bad=deepcopy(state);bad['paints'][0]['owner']='A';bad=_reseal(bad)
    negatives.append(refuse(directory,'paragraph_claims_shared_paint',lambda o,s:edit_variable_container(pdf,bad,o,s,changes(bad,{'A':SHORT}))))
    bad=deepcopy(state);bad['paints'][1]['band']=[65,150];bad=_reseal(bad)
    negatives.append(refuse(directory,'band_crosses_curved_corner',lambda o,s:edit_variable_container(pdf,bad,o,s,changes(bad,{'A':SHORT}))))
    return dict(schema='pdfengine-variable-container-evaluation-1',status='passed',source_url=prior['source_url'],source_sha256=SOURCE_SHA,
        reused_source_replay=prior['source_replay'],new_paint_replay=dict(poppler_all_changed_pixels=visual['poppler_diff']['all_changed_pixels'],
        mupdf_all_pixels_equal=visual['mupdf_page_pixels'],unicode_equal=True),font_sha256=source_sha('C:/Windows/Fonts/arial.ttf'),
        confirmed_policy=initial['layout_policy'],shared_container_children=['H','A','B'],fixed_children=['H'],
        paint_contract=dict(model=MODEL,band=[100,150],bottom_anchor='preserve-offset-to-container-bottom',paint_count=2,
                            background='one rectangular solid fill',border='compound solid fill with four cubic corners; certified vertical crossings only'),
        stages=stages,negative_controls=negatives,
        environment=dict(engine_sha256={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))},runner_sha256=source_sha(Path(__file__)),pymupdf=pymupdf.VersionBind),
        scope='one explicitly confirmed bottom-variable container; shared paints; fixed header; guarded sequential resize/text writers; no compound mutation')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True,exist_ok=False)
    write(directory/'summary.json',run(directory))


if __name__=='__main__':main()
