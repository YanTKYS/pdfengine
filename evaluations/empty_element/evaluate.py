"""External PDFs: delete all glyphs, reopen, type again, and repeat.

Compare retyping with an independently executed direct replacement under the
same explicitly supplied font/layout. This tests loss of editing meaning;
it does not mislabel font substitution as original-font no-op equivalence.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import pymupdf

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.editable import write_editable, edit_document, open_editable
from pdfeditor.elements import _close, _paint_value, inspect_element
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.selection import source_sha, make_selection
from evaluations.anchors.evaluate import replay_gate
from evaluations.attributed.evaluate import font_mapping_audit, original_code_audit
from evaluations.backend.followup import independent_edit_audit
from evaluations.elements.evaluate import audit_render, bounds, extraction
from evaluations.realpdf.evaluate import image_fingerprints, write_json

ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent
CASES=[
    dict(id='word_okinawa_procurement',width=468,bottom=156,font='msmincho.ttc',face=0,
         text='受付番号 PDF-2026。\n申請内容と保存先を確認してください。',font_policy='explicit_MS_Mincho_full_font'),
    dict(id='print_canvia',width=200,bottom=158,font='arialbd.ttf',face=0,
         text='Account / PDF 2026',font_policy='explicit_Arial_Bold_full_font',confirm_backgrounds=True),
    dict(id='lo_enterprise_en',width=210,bottom=189,font='arial.ttf',face=0,
         text='Compatible PDF workflows',font_policy='explicit_Arial_substitution_for_SourceSansPro'),
    dict(id='lo_newfeatures_ja',width=532,bottom=154,font='msmincho.ttc',face=1,
         text='PDF 2026 の編集内容を確認してください。\n保存先と設定内容を再確認します。',
         font_policy='explicit_MS_PMincho_substitution_for_IPAPMincho'),
    dict(id='print_fcc_ms_body',source='print_fcc_ms',glyph_ids=list(range(376,477)),
         width=196,bottom=133,font='arial.ttf',face=0,confirm_backgrounds=True,
         text='Adhesive for plastic material.\nCheck PDF 2026 before printing.',
         font_policy='explicit_Arial_full_font_for_CIDFont_F3'),
    dict(id='print_fcc_ms_example',source='print_fcc_ms',glyph_ids=list(range(477,509)),
         width=196,bottom=153,font='arial.ttf',face=0,confirm_backgrounds=True,
         text='Example : PDF 2026',font_policy='explicit_Arial_full_font_for_CIDFont_F3'),
]


def audit(source,output,report,directory):
    page=report['selection']['page']
    visual=audit_render(source,output,page,directory/'render',bounds(report['audit_bbox']))
    before=extraction(source,directory,'before');after=extraction(output,directory,'after')
    if report['before']:
        independent_edit_audit(before,after,page,report['before'],report['composed_text'])
    elif report['after']:
        # Independent reverse deletion must recover the complete empty page's
        # extraction, including all unrelated text. No empty-string control.
        independent_edit_audit(after,before,page,report['composed_text'],'')
    elif before['pages']!=after['pages']:
        raise ValueError('empty no-op changed independent text extraction')
    font_mapping_audit(output,report);original_code_audit(source,report)
    def nontext(path):
        observed=interpreted_paints(path,page)
        if observed['errors']:raise ValueError(observed['errors'][0])
        return [_paint_value(p) for p in observed['events'] if p['kind'] not in ('fill-text','stroke-text','ignore-text')]
    if not _close(nontext(source),nontext(output)):
        raise ValueError('nontext paint changed during the empty element lifecycle')
    with pymupdf.open(source) as a,pymupdf.open(output) as b:
        if any(image_fingerprints(a[i])!=image_fingerprints(b[i]) for i in range(len(a))):
            raise ValueError('image content changed')
    return dict(poppler_outside_pixels=visual['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        unedited_pages_equal=visual['outside_page_mupdf_pixels_equal'],
        independent_full_page_text=True,nontext_paints_equal=True,images_equal=True,font_codes_gids_widths=True)


def run_case(spec,directory,seeds,font_dir):
    directory.mkdir()
    seed=seeds[spec.get('source',spec['id'])]
    source=ROOT/seed['source'];selection=dict(seed['selection'])
    if 'glyph_ids' in spec:selection=make_selection(source,glyph_ids=spec['glyph_ids'])
    selection['explicitly_supplied_width']=spec['width']
    gate,_=replay_gate(source,selection,directory)
    paragraph=inspect_paragraph(source,selection)
    if len(paragraph['styles'])!=1:raise ValueError('this evaluator selection expects one observed style')
    font={'path':str(font_dir/spec['font']),'font_index':spec['face']}
    options=dict(max_bottom=spec['bottom'])
    if spec.get('confirm_backgrounds'):
        # This evaluator explicitly confirms the reviewed background paints.
        # The engine never branches on this PDF or converts candidates itself.
        element=inspect_element(source,selection)
        fixed=[dict(source_id=p['source_id'],relation='backgrounds',behavior='fixed-to-page')
               for p in element['paths'] if p['relationship'].get('hypothesis')=='background_candidate']
        options.update(element_snapshot=element,element_relations=fixed)
        write_json(directory/'confirmed-element.json',element);write_json(directory/'fixed.json',fixed)
    control,control_state=directory/'control.pdf',directory/'control.json'
    write_json(directory/'progress.json',dict(stage='direct_replacement_control',target=control.name))
    direct=write_editable(source,control,control_state,paragraph,
        [dict(start=0,end=len(paragraph['text']),text=paragraph['text'])],fonts={'s0':font},**options)
    write_json(directory/'control-report.json',direct)
    control_evidence=audit(source,control,direct,directory/'control-audit')
    pdf,state=directory/'empty.pdf',directory/'empty.json'
    write_json(directory/'progress.json',dict(stage='empty',target=pdf.name))
    report=write_editable(source,pdf,state,paragraph,[dict(start=0,end=len(paragraph['text']),text='')],
        fonts={'s0':font},**options)
    records=[dict(stage='empty',**audit(source,pdf,report,directory/'empty-audit'))]
    write_json(directory/'empty-report.json',report)
    first=open_editable(pdf,state)['state']
    if first['paragraph']['selection']['glyph_ids'] or first['paragraph']['text']:
        raise ValueError('empty logical paragraph retained glyphs or old text')
    if first['paragraph']['styles']!=paragraph['styles']:
        raise ValueError('independent style changed when glyphs disappeared')
    for n,text in enumerate(['',paragraph['text'],'',spec['text'],spec['text']],1):
        next_pdf,next_state=directory/f'g{n}.pdf',directory/f'g{n}.json'
        write_json(directory/'progress.json',dict(stage=f'g{n}',target=next_pdf.name))
        previous=open_editable(pdf,state)['state']
        before=previous['paragraph']['text']
        # No style, width, baseline, selection or font supplied again.
        edits=[] if text==before else [dict(start=0,end=len(before),text=text)]
        report=edit_document(pdf,state,next_pdf,next_state,edits)
        write_json(directory/f'g{n}-report.json',report)
        saved=open_editable(next_pdf,next_state)['state']
        if saved['paragraph']['text']!=text:raise ValueError('logical Unicode not retained exactly')
        if any(saved[k]!=first[k] for k in ('layout','layout_provenance','container','logical_element')):
            raise ValueError('paragraph identity, format or confirmed container drifted')
        evidence=audit(pdf,next_pdf,report,directory/f'g{n}-audit')
        if n in (1,2,5):
            reference=control if n==2 else pdf
            comparison=audit_render(reference,next_pdf,selection['page'],directory/f'g{n}-equivalence',
                                    bounds(report['audit_bbox']),noop=True)
            evidence['equivalent_poppler_all_pixels']=comparison['poppler_diff']['all_changed_pixels']
            evidence['equivalent_mupdf_all_pixels']=comparison['mupdf_page_pixels']
        records.append(dict(stage=f'g{n}',text_length=len(text),glyph_count=len(report['glyph_plan']),**evidence))
        pdf,state=next_pdf,next_state
    return dict(case=spec['id'],status='passed',source_sha256=source_sha(source),
        output_sha256=source_sha(pdf),font_sha256=source_sha(font['path']),font_policy=spec['font_policy'],
        source_replay_poppler_all_pixels=gate['poppler_diff']['all_changed_pixels'],
        source_replay_mupdf=gate['mupdf_page_pixels'],control=control_evidence,stages=records,
        empty_style_preserved=True,logical_identity_region_preserved=True,
        fixed_relations_preserved=bool(first.get('relations')),
        scope='one confirmed paragraph; fixed backgrounds only; no decoration, container resize or follows declared')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True)
    parser.add_argument('--case',action='append')
    parser.add_argument('--font-dir',type=Path,default=Path('C:/Windows/Fonts'))
    args=parser.parse_args();sys.stdout.reconfigure(encoding='utf-8')
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True,exist_ok=False)
    seeds={s['id']:s for s in json.loads((ROOT/'evaluations/backend/selections.json').read_text(encoding='utf-8'))}
    environment=dict(engine_sha256={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))},
                     runner_sha256=source_sha(Path(__file__)),pymupdf=pymupdf.VersionBind)
    cases=[]
    for spec in CASES:
        if args.case and spec['id'] not in args.case:continue
        try:result=run_case(spec,directory/spec['id'],seeds,args.font_dir)
        except Exception as exc:
            progress=directory/spec['id']/'progress.json'
            progress=json.loads(progress.read_text()) if progress.exists() else {}
            created=(directory/spec['id']/progress.get('target','control.pdf')).exists()
            result=dict(case=spec['id'],status='rejected' if isinstance(exc,PdfError) and not created else 'failed_evaluation',
                classification='safe_backend_refusal' if isinstance(exc,PdfError) and not created else 'evaluation_failure',
                output_created=created,phase=progress.get('stage','source_preflight'),error_type=type(exc).__name__,error=str(exc))
        cases.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
        write_json(directory/'summary.json',dict(environment=environment,cases=cases,
                   counts=dict(Counter(c['status'] for c in cases))))
    return int(any(c['status']=='failed_evaluation' for c in cases))


if __name__=='__main__':raise SystemExit(main())
