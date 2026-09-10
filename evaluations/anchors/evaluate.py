"""Real PDF reflow of confirmed Unicode decoration ranges, including reopen.

Source choices and semantic hard breaks are evaluator inputs. No document IDs,
coordinates, phrases or producer names are special-cased by the engine.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys

from PIL import Image
import pymupdf

from pdfeditor.anchors import inspect_anchors
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage,patch_streams
from pdfeditor.elements import inspect_element
from pdfeditor.layout import LayoutError
from pdfeditor.paragraph import edit_paragraph
from pdfeditor.pdf_save import program_pdf_bytes
from pdfeditor.replay import glyph_observations
from pdfeditor.selection import make_selection,resolve_selection,source_sha
from evaluations.attributed.evaluate import font_mapping_audit,original_code_audit
from evaluations.backend.followup import independent_edit_audit
from evaluations.elements.evaluate import audit_render,extraction,no_op_gate,bounds
from evaluations.realpdf.evaluate import write_json,image_fingerprints

ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent


def words(text):
    return ' '.join(text.split())


def review(source,selection,directory,*,line_joiner='',expected_text=None):
    paragraph=inspect_paragraph(source,selection,line_joiner=line_joiner)
    if expected_text is not None and words(paragraph['text'])!=words(expected_text):
        raise ValueError('reviewed line joining does not recover the confirmed logical text')
    element=inspect_element(source,selection)
    candidates=inspect_anchors(source,paragraph,element)
    # The evaluator confirms one contiguous, underlined French paragraph.
    groups=candidates['suggested_groups']
    if len(groups)!=1:
        raise ValueError('expected one reviewable underline group in the selected real PDF')
    ids=set(groups[0]['source_ids'])
    fixed=[]
    for item in element['paths']:
        if item['source_id'] in ids:continue
        if item['relationship'].get('hypothesis')=='background_candidate':
            fixed.append(dict(source_id=item['source_id'],relation='backgrounds',behavior='fixed-to-page'))
    spec=dict(paragraph_sha256=paragraph['snapshot_sha256'],element_sha256=element['snapshot_sha256'],
        underlines=[dict(source_ids=groups[0]['source_ids'],range=groups[0]['range'])],fixed_relations=fixed)
    for name,value in [('paragraph',paragraph),('element',element),('candidates',candidates),('anchors',spec)]:
        write_json(directory/f'{name}.json',value)
    return paragraph,element,spec


def replay_gate(source,selection,directory):
    content=ContentPage(source,selection['page'])
    try:
        chosen=set(selection['glyph_ids'])
        data=patch_streams(content,content.selected_events(chosen),chosen,remove=False)[-content.page.xref]
    finally:content.close()
    replayed=directory/'no-op.pdf'
    replayed.write_bytes(program_pdf_bytes(source,selection['page']-1,data))
    return no_op_gate(source,replayed,selection['page'],directory,resolve_selection(source,selection).bbox.tuple())


def underline_pixels(path,segments):
    """Independently require a visible red line across every planned advance."""
    result=[]
    with Image.open(path) as original:
        image=original.convert('RGB')
        for segment in segments:
            x0,y0,x1,y1=bounds(segment['bounds'])
            columns=range(math.ceil(x0*2)+2,math.floor(x1*2)-2)
            rows=range(math.floor(y0*2),math.ceil(y1*2))
            checks=[any((lambda c:c[0]>180 and c[1]<110 and c[2]<110)(image.getpixel((x,y))) for y in rows) for x in columns]
            if not checks or not all(checks):
                raise ValueError('Poppler did not paint the complete planned underline segment')
            result.append(dict(columns_checked=len(checks),all_columns_painted=True))
    return result


def run_case(name,source,selection,directory,make_edits,font_dir,*,line_joiner='',expected_text=None):
    directory.mkdir(parents=True)
    item=dict(case=name,source_sha256=source_sha(source),status='pending')
    output=directory/'edited.pdf'
    phase='review'
    try:
        p,e,s=review(source,selection,directory,line_joiner=line_joiner,expected_text=expected_text)
        gate,before=replay_gate(source,selection,directory)
        edits=make_edits(p,s)
        write_json(directory/'edits.json',dict(edits=edits))
        phase='edit'
        report=edit_paragraph(source,output,p,edits,
            fonts={style['id']:{'path':str(font_dir/'arial.ttf')} for style in p['styles']},
            element_snapshot=e,anchor_spec=s,max_bottom=155,removal_output=directory/'removed.pdf')
        write_json(directory/'report.json',report)
        phase='saved_output_audit'
        if words(' '.join(line['text'] for line in report['lines']))!=words(report['after']):
            raise ValueError('visual line joining changes reviewed word boundaries')
        audit=audit_render(source,output,selection['page'],directory/'edit',bounds(report['audit_bbox']))
        removal=audit_render(source,directory/'removed.pdf',selection['page'],directory/'removal',bounds(report['audit_bbox']))
        after=extraction(output,directory,'edited');deleted=extraction(directory/'removed.pdf',directory,'removed')
        independent_edit_audit(before,after,selection['page'],p['text'],report['composed_text'])
        independent_edit_audit(before,deleted,selection['page'],p['text'],'')
        font_mapping_audit(output,report);original_code_audit(source,report)
        pixels=underline_pixels(directory/'edit/after.png',report['anchors']['segments'])
        with pymupdf.open(source) as a,pymupdf.open(output) as b:
            images_equal=all(image_fingerprints(a[i])==image_fingerprints(b[i]) for i in range(len(a)))
        if not images_equal or source_sha(source)!=item['source_sha256']:
            raise ValueError('source or fixed images changed')
        item.update(status='passed',classification='range_anchored_underline_reflow',output_sha256=source_sha(output),
            old_lines=report['old_line_count'],new_lines=report['new_line_count'],
            old_underline_paths=report['anchors']['source_paths_replaced'],new_underline_segments=len(report['anchors']['segments']),
            retained_glyphs=report['retained_glyph_count'],provided_glyphs=report['provided_font_glyph_count'],fonts=report['fonts'],
            noop_poppler_changed_pixels=gate['poppler_diff']['all_changed_pixels'],
            poppler_outside_changed_pixels=audit['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
            removal_outside_changed_pixels=removal['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
            poppler_underlines=pixels,independent_edit=True,independent_removal=True,font_mapping=True,
            nontext_paint_plan=report['anchors']['nontext_paint_plan_verified'],images_unchanged=images_equal,
            reviewed_line_joiner=line_joiner,word_boundaries_in_layout=True,
            reopened_logical_text_confirmed=expected_text is not None,
            other_pages_mupdf_equal=audit['outside_page_mupdf_pixels_equal'])
    except Exception as exc:
        safe=phase=='edit' and isinstance(exc,(PdfError,LayoutError)) and not output.exists()
        item.update(status='rejected' if safe else 'failed_evaluation',classification='safe_refusal' if safe else 'evaluation_failure',
                    error_type=type(exc).__name__,error=str(exc),phase=phase,output_created=output.exists())
    write_json(directory/'result.json',item)
    print(json.dumps(item,ensure_ascii=False),flush=True)
    return item


def breaks(p,s):
    end=s['underlines'][0]['range'][1]
    return [dict(start=end,end=p['text'].index('Adhesive'),text='\n'),
            dict(start=p['text'].index('Ex :'),end=p['text'].index('Ex :'),text='\n')]


def long_edits(p,s):
    start=p['text'].index('UL')+2
    return [dict(start=start,end=start,text=' certifiés et vérifiés')]+breaks(p,s)


def short_edits(p,s):
    end=s['underlines'][0]['range'][1]
    return [dict(start=0,end=end,text='Consulter la liste des composants UL.')]+breaks(p,s)


def reopen_selection(source,report):
    # Match the previously verified output glyph plan, rather than treating a
    # bounding box as ownership or assuming unchanged extraction order.
    with pymupdf.open(source) as doc:observed=glyph_observations(doc[0])
    ids=[]
    for wanted in report['glyph_plan']:
        matches=[i for i,g in enumerate(observed) if g['unicode']==wanted['unicode'] and g['glyph_id']==wanted['glyph_id']
                 and max(abs(a-b) for a,b in zip(g['origin'],wanted['origin']))<.002]
        if len(matches)!=1:raise ValueError('reopened glyph plan is ambiguous')
        ids.append(matches[0])
    return make_selection(source,glyph_ids=ids,explicit_width=196)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True)
    parser.add_argument('--font-dir',type=Path,default=Path('C:/Windows/Fonts'))
    args=parser.parse_args();sys.stdout.reconfigure(encoding='utf-8')
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True,exist_ok=False)
    environment=dict(engine_sha256={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))},
        runner_sha256=source_sha(Path(__file__)),pymupdf=pymupdf.VersionBind,mupdf=pymupdf.VersionFitz)
    source=ROOT/'evaluations/realpdf/corpus/print_fcc_ms.pdf'
    selection=make_selection(source,glyph_ids=list(range(257,509)),explicit_width=196)
    results=[]
    for name,fn in [('grow',long_edits),('shorten',short_edits),('overflow',lambda p,s:[dict(start=63,end=63,text=' confirmation '*80)]),
                    ('cross_anchor_boundary',lambda p,s:[dict(start=110,end=125,text='Changed')])]:
        results.append(run_case(name,source,selection,directory/name,fn,args.font_dir))
        write_json(directory/'results.json',results)
    if results[0]['status']=='passed':
        edited=directory/'grow/edited.pdf'
        report=json.loads((directory/'grow/report.json').read_text(encoding='utf-8'))
        try:
            selected=reopen_selection(edited,report)
            def shorten_again(p,s):
                start=p['text'].index(' certifiés et vérifiés')
                return [dict(start=start,end=start+len(' certifiés et vérifiés'),text='')]+breaks(p,s)
            # Physical line breaks do not carry the original hard/soft-break
            # semantics. The evaluator reviews spaces and restores hard breaks;
            # do not silently concatenate lines or infer this inside the engine.
            results.append(run_case('reopen_shorten',edited,selected,directory/'reopen',shorten_again,args.font_dir,
                                    line_joiner=' ',expected_text=report['after']))
        except Exception as exc:
            results.append(dict(case='reopen_shorten',status='failed_evaluation',classification='evaluation_failure',error=str(exc)))
    summary=dict(environment=environment,counts=dict(Counter(r['status'] for r in results)),cases=results,
                 scope='One external PDF; explicit whole-callout selection, fixed frame and confirmed logical underline range.')
    write_json(directory/'summary.json',summary);print(json.dumps(summary['counts']),flush=True)
    return int(any(r['status']=='failed_evaluation' for r in results))


if __name__=='__main__':raise SystemExit(main())
