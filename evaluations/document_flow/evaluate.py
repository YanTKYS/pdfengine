"""Reviewed external ranges: explicit A -> B, repeated edits and empty states.

Raw PDFs, models, reports and renders remain in ignored runs/. Only source
hashes, authored evaluation inputs and aggregate results are published.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

import pymupdf

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.document_flow import confirm_document, edit_flow, open_document, _reseal
from pdfeditor.elements import _close, _paint_value, inspect_element
from pdfeditor.model import Rect
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.proof_session import proof_session
from evaluations.anchors.evaluate import replay_gate
from evaluations.attributed.evaluate import font_mapping_audit, original_code_audit
from evaluations.backend.followup import independent_edit_audit
from evaluations.elements.evaluate import audit_render, extraction
from evaluations.realpdf.evaluate import image_fingerprints, write_json

ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent
SOURCE=ROOT/'evaluations/realpdf/corpus/print_fcc_ms.pdf'
SOURCE_SHA='11ad813fce341afed902f98f7c5335f346bd9e746c648a509c1c7f9ba4f7f3fd'
LONG='Adhesive for plastic material.\nCheck PDF 2026 before printing.\nKeep the label clean.\nConfirm the saved document.'
CASES={
    'fcc':dict(source=SOURCE,sha256=SOURCE_SHA,ids=[list(range(376,477)),list(range(477,509))],
        width=196,bottom=155,bounds=[565,66,772,155],font='C:/Windows/Fonts/arial.ttf',
        paints=[37,38,40,42,44],long=LONG,short='Check PDF 2026.',retype='Example : PDF 2026',
        url='https://fccid.io/OVNAC4/Label/Label-Sample-2-3606396.pdf'),
    'word_niigata':dict(source=ROOT/'evaluations/realpdf/corpus/word_niigata_hearing.pdf',
        sha256='9b8498becfda63c3cca8ac4a747cd17e5436c7e3e7372c192a771ae0114046e7',
        ids=[list(range(1116,1143)),list(range(1143,1160))],width=320,bottom=797,bounds=[58,754,534,797],
        font='C:/Windows/Fonts/msmincho.ttc',paints=[],
        long='新潟市中央区新光町4番地1の受付窓口で、申請書類と受付番号 PDF-2026、提出先と連絡事項を確認してください。',
        short='受付番号 PDF-2026。',retype='新潟県の受付窓口',
        url='https://kenpo.pref.niigata.lg.jp/bn/R06_10/1008_t78/t78_20241008i30673.pdf'),
    'word_okinawa':dict(source=ROOT/'evaluations/realpdf/corpus/word_okinawa_procurement.pdf',
        sha256='bbaa2b12eeaf1c7eac2dc91aca03c00da4ed948e7c14d99d879ff4c3083fd0b3',page=2,
        ids=[list(range(912,941)),list(range(941,1011))],width=468,bottom=768,bounds=[70,694,540,768],
        font='C:/Windows/Fonts/msmincho.ttc',paints=[],
        long='(7) 仕様書と応募要領を確認し、受付番号 PDF-2026、提出する書類、申請内容、保存先と連絡事項を確認してください。',
        short='(7) 受付番号 PDF-2026。',retype='(8) 受付窓口に提出してください。',
        url='https://www.pref.okinawa.jp/_res/projects/default_project/_page_/001/035/156/01_koukoku.pdf'),
}


@proof_session
def prepare(directory,spec,reuse_gates=None):
    source=spec['source']
    if source_sha(source)!=spec['sha256']:raise ValueError('reviewed source PDF changed')
    elements={};gates={}
    for ident,ids in zip(('A','B'),spec['ids']):
        selection=make_selection(source,page=spec.get('page',1),glyph_ids=ids,explicit_width=spec['width'])
        sub=directory/ident;sub.mkdir()
        if reuse_gates is None:
            gate,_=replay_gate(source,selection,sub)
            gates[ident]=dict(poppler_all_changed_pixels=gate['poppler_diff']['all_changed_pixels'],
                              mupdf_all_pixels_equal=gate['mupdf_page_pixels'])
        else:
            # Verify the stored replay's decoded operator program against the
            # exact current selection; no new PDF or render is needed. This
            # also allows reuse after a later import was safely refused.
            from pdfeditor.content_stream import ContentPage,patch_streams
            from pypdf import PdfReader
            content=ContentPage(source,spec.get('page',1))
            try:
                chosen=set(ids)
                expected=patch_streams(content,content.selected_events(chosen),chosen,remove=False)[-content.page.xref]
            finally:content.close()
            reader=PdfReader(reuse_gates/ident/'no-op.pdf')
            if reader.is_encrypted:reader.decrypt('')
            if reader.pages[spec.get('page',1)-1].get_contents().get_data()!=expected:
                raise ValueError('reused source gate has a different selected operator program')
            from PIL import Image,ImageChops
            with Image.open(reuse_gates/ident/'no-op/before.png') as a,Image.open(reuse_gates/ident/'no-op/after.png') as b:
                if ImageChops.difference(a.convert('RGB'),b.convert('RGB')).getbbox():
                    raise ValueError('stored source gate pixels differ')
            with pymupdf.open(source) as a,pymupdf.open(reuse_gates/ident/'no-op.pdf') as b:
                if any(a[i].get_pixmap().samples!=b[i].get_pixmap().samples for i in range(len(a))):
                    raise ValueError('stored source replay no longer matches source')
            gates[ident]=dict(poppler_all_changed_pixels=0,mupdf_all_pixels_equal=True,
                              reused_from=str(reuse_gates.resolve().relative_to(BASE)))
        p=inspect_paragraph(source,selection);e=inspect_element(source,selection)
        # Paint sequence 37 is the yellow shared rectangle, confirmed against
        # the source rendering. It is NOT owned by either text paragraph.
        paths=[x for x in e['paths'] if x['proof'].get('paint_seqnos') in [[i] for i in spec['paints']]]
        if len(paths)!=len(spec['paints']):raise ValueError('reviewed shared background/border/French underlines changed')
        elements[ident]=dict(paragraph=p,layout=dict(width=spec['width'],max_bottom=spec['bottom']),
            fonts={style['id']:{'path':'C:/Windows/Fonts/arial.ttf' if style['font_name']=='Arial' else spec['font']}
                   for style in p['styles']},owned_paints=[],
            paint_relations=[dict(source_id=p['source_id'],relation='backgrounds' if p['proof']['paint_seqnos']==[37] else 'unrelated',
                                  behavior='fixed-to-page') for p in paths])
    gap=(elements['B']['paragraph']['layout_suggestion']['baseline']-
         elements['A']['paragraph']['layout_suggestion']['base_baselines'][-1])
    write_json(directory/'source-gates.json',gates)
    model=confirm_document(source,elements,container_id='confirmed-text-region',bounds=spec['bounds'],page=spec.get('page',1),
                            follows=[dict(before='A',after='B',gap=gap)])
    write_json(directory/'initial.json',model)
    return model,gates


def audit(before,after,report,directory,*,noop=False):
    directory.mkdir()
    area=None
    for step in report['steps']:
        rect=Rect(**step['report']['audit_bbox']);area=rect if area is None else area.union(rect)
    edit=next(s['report'] for s in report['steps'] if s['kind']=='edit')
    page=edit['selection']['page']
    visual=audit_render(before,after,page,directory/'render',area.tuple(),noop=noop)
    old=extraction(before,directory,'before');new=extraction(after,directory,'after')
    if edit['before']:independent_edit_audit(old,new,page,edit['before'],edit['composed_text'])
    elif edit['after']:independent_edit_audit(new,old,page,edit['composed_text'],'')
    elif old['pages']!=new['pages']:raise ValueError('empty edit changed independent extraction')
    font_mapping_audit(after,edit);original_code_audit(before,edit)
    def nontext(path):
        result=interpreted_paints(path,page)
        if result['errors']:raise ValueError(result['errors'][0])
        return [_paint_value(p) for p in result['events'] if p['kind'] not in ('fill-text','stroke-text','ignore-text')]
    if not _close(nontext(before),nontext(after)):raise ValueError('fixed nontext paint changed')
    with pymupdf.open(before) as a,pymupdf.open(after) as b:
        if any(image_fingerprints(a[i])!=image_fingerprints(b[i]) for i in range(len(a))):
            raise ValueError('image content changed')
    return dict(poppler_outside_changed_pixels=visual['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        mupdf_outside_pixels_equal=True,other_pages_equal=visual['outside_page_mupdf_pixels_equal'],
        fixed_nontext_paints_equal=True,images_equal=True,independent_full_page_text=True,font_codes_gids_widths=True,
        noop_poppler_all_changed_pixels=visual['poppler_diff']['all_changed_pixels'] if noop else None,
        noop_mupdf_all_pixels_equal=visual['mupdf_page_pixels'] if noop else None)


def run(directory,spec,reuse_gates=None):
    model,gates=prepare(directory,spec,reuse_gates);pdf=spec['source'];records=[]
    write_json(directory/'source-gates.json',gates)
    for n,(ident,text) in enumerate([('A',spec['long']),('A',spec['short']),('B',''),('A',spec['long']),
                                    ('A',''),('A',spec['short']),('B',spec['retype']),('A',None)],1):
        out,path=directory/f'g{n}.pdf',directory/f'g{n}.json'
        old=model['elements'][ident]['binding']['paragraph']['text'];text=old if text is None else text
        write_json(directory/'progress.json',dict(stage=f'g{n}',element=ident,target=out.name))
        # The evaluator confirms the paragraph's first style for the entire
        # newly authored replacement and for typing into the empty element.
        style=model['elements'][ident]['binding']['paragraph']['styles'][0]['id']
        report=edit_flow(pdf,model,out,path,ident,[dict(start=0,end=len(old),text=text,style_id=style)] if old!=text else [],
                         empty_style_id=style)
        write_json(directory/f'g{n}-report.json',report)
        restored=open_document(out,path)
        if restored['status']!='restored':raise ValueError(restored['reason'])
        state=restored['state']
        if (state['follows']!=model['follows'] or state['container']!=model['container']
                or list(state['elements'])!=list(model['elements'])
                or state['elements'][ident]['binding']['paragraph']['text']!=text):
            raise ValueError('logical identity, relation, container or authored content changed')
        proof=audit(pdf,out,report,directory/f'g{n}-audit',noop=n==8)
        record=dict(stage=f'g{n}',element=ident,status='passed',baseline_delta=report['baseline_delta'],
            moved_elements=report['moved_elements'],empty_elements=[i for i,e in state['elements'].items() if not e['binding']['paragraph']['text']],
            output_sha256=source_sha(out),**proof)
        records.append(record);print(json.dumps(record),flush=True)
        write_json(directory/'stages.json',records)
        pdf,model=out,state
    refused=[]
    for mode in ('missing_edge','container_overflow','external_save'):
        candidate=deepcopy(model);current=pdf
        if mode=='missing_edge':candidate['follows']=[];candidate=_reseal(candidate)
        if mode=='external_save':
            from pypdf import PdfReader,PdfWriter
            w=PdfWriter(clone_from=PdfReader(pdf));w.add_metadata({'/Title':'external save'})
            current=directory/'external.pdf';w.write(current)
        text=spec['long'] if mode!='container_overflow' else spec['long']+'\nOne more line.\nAnother line.'
        out,path=directory/f'{mode}.pdf',directory/f'{mode}.json'
        old=candidate['elements']['A']['binding']['paragraph']['text']
        style=candidate['elements']['A']['binding']['paragraph']['styles'][0]['id']
        try:edit_flow(current,candidate,out,path,'A',[dict(start=0,end=len(old),text=text,style_id=style)])
        except (PdfError,ValueError) as exc:
            if out.exists() or path.exists():raise ValueError('refused transaction was published')
            refused.append(dict(case=mode,status='safely_refused',reason=str(exc),output_created=False))
        else:raise ValueError('negative control unexpectedly succeeded: '+mode)
    return dict(status='passed',source_sha256=spec['sha256'],source_url=spec['url'],
        source_replay=gates,font_policy='explicit full font for changed text; original B codes retained until deletion',
        font_sha256=source_sha(spec['font']),stages=records,negative_controls=refused,
        scope='two caller-confirmed paragraphs, fixed nontext paints, explicit follows; no page flow or owned-path movement in this source')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True)
    parser.add_argument('--reuse-gates',type=Path);parser.add_argument('--case',choices=CASES,default='fcc');args=parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True,exist_ok=False)
    environment=dict(engine_sha256={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))},
                     runner_sha256=source_sha(Path(__file__)),pymupdf=pymupdf.VersionBind)
    try:result=run(directory,CASES[args.case],args.reuse_gates)
    except Exception as exc:
        progress=directory/'progress.json';progress=json.loads(progress.read_text()) if progress.exists() else {}
        stages=directory/'stages.json';stages=json.loads(stages.read_text()) if stages.exists() else []
        created=(directory/progress['target']).exists() if 'target' in progress else False
        result=dict(status=('partial_success_then_safe_refusal' if stages else 'safely_refused')
                    if isinstance(exc,PdfError) and not created else 'evaluation_failure',
                    phase=progress,error_type=type(exc).__name__,reason=str(exc),stages=stages,output_created=created)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        write_json(directory/'summary.json',dict(environment=environment,result=result))
        if isinstance(exc,PdfError) and not created:return
        raise
    write_json(directory/'summary.json',dict(environment=environment,result=result))


if __name__=='__main__':main()
