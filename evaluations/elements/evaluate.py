"""External PDF evidence for explicit ownership and source-linked paint edits.

Frozen glyph IDs and paint sequence numbers below are evaluator decisions.
They never enter engine conditionals. Original/derived PDF, text, paths and
pixels remain in ignored runs; only the compact result is public.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

from PIL import Image, ImageChops
import pymupdf

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, patch_streams
from pdfeditor.elements import inspect_element, move_element
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.paragraph import edit_paragraph
from pdfeditor.pdf_save import program_pdf_bytes
from pdfeditor.selection import make_selection, resolve_selection, source_sha
from evaluations.attributed.evaluate import font_mapping_audit, original_code_audit
from evaluations.backend.followup import independent_edit_audit, render_audit, require_render_audit
from evaluations.realpdf.evaluate import (DEFAULT_PYPDF, independent_text, write_json,
                                         drawings_fingerprint, image_fingerprints)

ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent


def bounds(value):
    return tuple(value[k] for k in ('x0','y0','x1','y1'))


def audit_render(source, output, page, directory, bbox, *, noop=False, edited_pages=None):
    directory.mkdir(parents=True,exist_ok=True)
    audit=render_audit(source,output,page,directory,bbox,edited_pages=edited_pages)
    require_render_audit(audit)
    if audit['poppler_diff_with_1pt_margin']['outside_changed_pixels']:
        raise ValueError('any-channel Poppler difference outside the allowed one-point margin')
    if noop and (audit['poppler_diff']['all_changed_pixels'] or not audit['mupdf_page_pixels']):
        raise ValueError('no-op differs in MuPDF or Poppler; subsequent edit prohibited')
    return audit


def extraction(path, directory, label):
    result=independent_text(DEFAULT_PYPDF,path,directory/f'{label}-text.json')
    if result.get('error') or not isinstance(result.get('pages'),list):
        raise ValueError(f'independent extraction failed: {label}')
    return result


def no_op_gate(source, replayed, page, directory, bbox):
    audit=audit_render(source,replayed,page,directory/'no-op',bbox,noop=True)
    before=extraction(source,directory,'source')
    after=extraction(replayed,directory,'no-op')
    if before['pages']!=after['pages']:
        raise ValueError('no-op changed independent Unicode extraction')
    return audit,before


def relations_for(snapshot, sequence_relations, behavior):
    result=[]
    for seq,relation in sequence_relations.items():
        matches=[p for p in snapshot['paths'] if p['proof'].get('paint_seqnos')==[seq]]
        if len(matches)!=1:
            raise ValueError(f'evaluation source/proof changed at paint sequence {seq}')
        result.append(dict(source_id=matches[0]['source_id'],relation=relation,behavior=behavior))
    return result


def common_evidence(source, output, snapshot, report):
    with pymupdf.open(source) as a,pymupdf.open(output) as b:
        images_equal=len(a)==len(b) and all(image_fingerprints(a[i])==image_fingerprints(b[i]) for i in range(len(a)))
    if not images_equal:
        raise ValueError('existing images changed')
    if source_sha(source)!=snapshot['source_sha256']:
        raise ValueError('original PDF changed')
    return dict(source_sha256=source_sha(source),output_sha256=source_sha(output),images_unchanged=images_equal,
        source_path_count=len(snapshot['paths']),
        proven_path_count=sum(p['proof']['status']=='proven' for p in snapshot['paths']),
        source_snapshot_sha256=snapshot['snapshot_sha256'])


def changed_pixels(a,b):
    if a.size!=b.size:
        raise ValueError('image crop dimensions differ')
    difference=ImageChops.difference(a.convert('RGB'),b.convert('RGB'))
    raw=difference.tobytes()
    return sum(raw[i:i+3]!=b'\0\0\0' for i in range(0,len(raw),3))


def movement(directory):
    source=ROOT/'evaluations/realpdf/corpus/print_fcc_ms.pdf'
    selection=make_selection(source,page=1,glyph_ids=list(range(257,509)))
    snapshot=inspect_element(source,selection)
    relations=relations_for(snapshot,{37:'backgrounds',38:'borders',40:'decorates',42:'decorates',44:'decorates'},'fixed-to-element')
    write_json(directory/'element.json',snapshot)
    write_json(directory/'relations.json',{'relations':relations})
    replayed=directory/'no-op.pdf'
    noop=move_element(source,replayed,snapshot,relations,dx=0,dy=0)
    gate,before=no_op_gate(source,replayed,1,directory,bounds(noop['audit_bbox']))
    output,removed=directory/'moved.pdf',directory/'removed.pdf'
    report=move_element(source,output,snapshot,relations,dx=0,dy=180,removal_output=removed)
    write_json(directory/'report.json',report)
    audit=audit_render(source,output,1,directory/'movement',bounds(report['audit_bbox']))
    removal_audit=audit_render(source,removed,1,directory/'removal',bounds(report['before_bounds']))
    after=extraction(output,directory,'moved')
    deleted=extraction(removed,directory,'removed')
    selected=''.join(g.text for g in sorted(resolve_selection(source,selection).glyphs,key=lambda g:g.source_order))
    edit=independent_edit_audit(before,after,1,selected,selected)
    removal=independent_edit_audit(before,deleted,1,selected,'')
    # Translation is an integer 360 pixels at 144 dpi. Compare the entire
    # group crop, including border and underlines, against its new location.
    box=bounds(report['before_bounds'])
    crop=(math.floor(box[0]*2)-2,math.floor(box[1]*2)-2,math.ceil(box[2]*2)+2,math.ceil(box[3]*2)+2)
    moved_crop=(crop[0],crop[1]+360,crop[2],crop[3]+360)
    with Image.open(directory/'movement/before.png') as a,Image.open(directory/'movement/after.png') as b,Image.open(directory/'removal/after.png') as c:
        translated=changed_pixels(a.crop(crop),b.crop(moved_crop))
        old_cleared=changed_pixels(b.crop(crop),c.crop(crop))
        # The blank interval between the two disjoint rectangles is excluded
        # from neither the backend union mask nor the crop; check it explicitly.
        gap=(crop[0],crop[3],crop[2],moved_crop[1])
        gap_changed=changed_pixels(a.crop(gap),b.crop(gap))
    if translated or old_cleared or gap_changed:
        raise ValueError(f'moved crop / old removal / intervening gap differ: {translated}, {old_cleared}, {gap_changed}')
    return dict(case='print_fcc_ms_callout_move',status='passed',classification='source_linked_rigid_element',
        **common_evidence(source,output,snapshot,report),moved_glyphs=report['moved_glyphs'],
        moved_text_operators=report['moved_text_operators'],moved_path_operators=report['moved_path_operators'],
        original_fonts_preserved=True,paint_plan_verified=report['paint_plan_verified'],
        path_writer=report['path_writer'],
        noop_poppler_changed_pixels=gate['poppler_diff']['all_changed_pixels'],
        poppler_outside_changed_pixels=audit['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        translated_crop_changed_pixels=translated,old_region_matches_removal_pixels=old_cleared,
        intervening_gap_changed_pixels=gap_changed,
        removal_poppler_outside_changed_pixels=removal_audit['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        other_pages_mupdf_equal=audit['outside_page_mupdf_pixels_equal'],independent_edit=edit['passed'],independent_removal=removal['passed'])


def paragraph(directory,font_dir):
    source=ROOT/'evaluations/realpdf/corpus/lo_enterprise_en.pdf'
    selection=make_selection(source,page=2,glyph_ids=list(range(24)),explicit_width=226)
    element=inspect_element(source,selection)
    relations=relations_for(element,{0:'backgrounds'},'fixed-to-page')
    write_json(directory/'element.json',element)
    write_json(directory/'relations.json',{'relations':relations})
    resolved=resolve_selection(source,selection)
    content=ContentPage(source,2)
    try:
        selected=set(selection['glyph_ids'])
        data=patch_streams(content,content.selected_events(selected),selected,remove=False)[-content.page.xref]
    finally:
        content.close()
    replayed=directory/'no-op.pdf'
    replayed.write_bytes(program_pdf_bytes(source,1,data))
    gate,before=no_op_gate(source,replayed,2,directory,resolved.bbox.tuple())
    snapshot=inspect_paragraph(source,selection)
    write_json(directory/'paragraph.json',snapshot)
    output,removed=directory/'edited.pdf',directory/'removed.pdf'
    report=edit_paragraph(source,output,snapshot,[dict(start=8,end=23,text='Document Editing')],
        fonts={'s1':{'path':str(font_dir/'arial.ttf')}},x=29.8,first_line_indent=0,max_bottom=194,
        element_snapshot=element,element_relations=relations,removal_output=removed)
    write_json(directory/'report.json',report)
    audit=audit_render(source,output,2,directory/'edit',bounds(report['audit_bbox']))
    removal_audit=audit_render(source,removed,2,directory/'removal',resolved.bbox.tuple())
    after=extraction(output,directory,'edited')
    deleted=extraction(removed,directory,'removed')
    edit=independent_edit_audit(before,after,2,snapshot['text'],report['composed_text'])
    removal=independent_edit_audit(before,deleted,2,snapshot['text'],'')
    font_mapping_audit(output,report)
    original_code_audit(source,report)
    with pymupdf.open(source) as a,pymupdf.open(output) as b,pymupdf.open(removed) as c:
        drawings_equal=all(drawings_fingerprint(a[i])==drawings_fingerprint(b[i])==drawings_fingerprint(c[i]) for i in range(len(a)))
    if not drawings_equal:
        raise ValueError('fixed vectors changed')
    return dict(case='lo_enterprise_fixed_background_edit',status='passed',classification='explicit_background_with_attributed_edit',
        **common_evidence(source,output,element,report),drawings_unchanged=drawings_equal,
        retained_glyph_count=report['retained_glyph_count'],provided_font_glyph_count=report['provided_font_glyph_count'],
        old_lines=report['old_line_count'],new_lines=report['new_line_count'],font_mapping_verified=True,original_codes_preserved=True,
        noop_poppler_changed_pixels=gate['poppler_diff']['all_changed_pixels'],
        poppler_outside_changed_pixels=audit['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        removal_poppler_outside_changed_pixels=removal_audit['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        other_pages_mupdf_equal=audit['outside_page_mupdf_pixels_equal'],independent_edit=edit['passed'],independent_removal=removal['passed'])


def refused_moves(directory, source_snapshot):
    source=ROOT/'evaluations/realpdf/corpus/print_fcc_ms.pdf'
    snapshot=json.loads(source_snapshot.read_text(encoding='utf-8'))
    relations=relations_for(snapshot,{37:'backgrounds',38:'borders',40:'decorates',42:'decorates',44:'decorates'},'fixed-to-element')
    results=[]
    for name,rs,dy in [('unresolved_underline',relations[:-1],180),('outside_page',relations,600),('fixed_footer_collision',relations,390)]:
        output=directory/f'{name}.pdf'
        try:
            move_element(source,output,snapshot,rs,dx=0,dy=dy)
        except PdfError as exc:
            if output.exists():
                raise ValueError('rejection left a published output') from exc
            results.append(dict(case=name,status='rejected',classification='safe_refusal',reason=str(exc)))
        else:
            raise ValueError(f'expected refusal did not occur: {name}')
    return results


def marked_observations(directory):
    results=[]
    for name,page in [('word_takeo_notice',2),('word_osaka_guideline',1),('print_ubiquiti',1)]:
        source=ROOT/'evaluations/realpdf/corpus'/f'{name}.pdf'
        observed=observe_marked_content(source,page)
        write_json(directory/f'{name}.json',observed)
        counts=Counter(s['association']['status'] for s in observed['scopes'])
        results.append(dict(source=name,source_sha256=source_sha(source),page=page,
            complete=observed['complete'],association_counts=dict(counts),
            ownership='not inferred from marked-content membership',editing='not attempted by this observation'))
    return results


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-name',required=True)
    parser.add_argument('--font-dir',type=Path,default=Path('C:/Windows/Fonts'))
    args=parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    directory=BASE/'runs'/args.run_name
    directory.mkdir(parents=True,exist_ok=False)
    environment=dict(engine_sha256={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))},
                     runner_sha256=source_sha(Path(__file__)),pymupdf=pymupdf.VersionBind,mupdf=pymupdf.VersionFitz)
    write_json(directory/'environment.json',environment)
    results=[]
    for name,runner in [('fcc',movement),('lo',lambda d:paragraph(d,args.font_dir))]:
        path=directory/name
        path.mkdir()
        try:
            item=runner(path)
        except Exception as exc:
            item=dict(case=name,status='failed_evaluation',classification='evaluation_failure',reason=str(exc),error_type=type(exc).__name__)
        results.append(item)
        write_json(path/'result.json',item)
        print(json.dumps(item,ensure_ascii=False),flush=True)
    if results[0]['status']=='passed':
        results.extend(refused_moves(directory,directory/'fcc/element.json'))
    observations=marked_observations(directory)
    summary=dict(environment=environment,counts=dict(Counter(r['status'] for r in results)),cases=results,marked_content=observations)
    write_json(directory/'summary.json',summary)
    print(json.dumps(summary['counts']),flush=True)
    return int(any(x['status']=='failed_evaluation' for x in results))


if __name__=='__main__':
    raise SystemExit(main())
