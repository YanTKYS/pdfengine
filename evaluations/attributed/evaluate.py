"""Frozen human selections for source-linked styled editing of external PDFs.

The engine receives ordinary Unicode edits and confirmed geometry. Document
identifiers and coordinates are evaluation inputs, never backend exceptions.
Full source text, generated PDFs, and rendered images stay in ignored runs/.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import pymupdf
from pypdf import PdfReader

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, patch_streams
from pdfeditor.layout import LayoutError
from pdfeditor.paragraph import edit_paragraph
from pdfeditor.pdf_save import program_pdf_bytes
from pdfeditor.selection import make_selection, resolve_selection, source_sha
from evaluations.backend.followup import independent_edit_audit, render_audit, require_render_audit
from evaluations.realpdf.evaluate import (DEFAULT_PYPDF, independent_text, write_json,
                                         drawings_fingerprint, image_fingerprints)

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
CASES = [
    dict(id="lo_migration_short", source="lo_migration_ja", page=1, ids=list(range(1088,1200)),
         width=482, x=56.8, indent=10.5, bottom=674, font="msmincho.ttc", face=1,
         edits=[dict(start=12,end=14,text="申請書類"),dict(start=40,end=112,text="",style_id="s1")]),
    dict(id="lo_migration_long", source="lo_migration_ja", page=1, ids=list(range(1088,1200)),
         width=482, x=56.8, indent=10.5, bottom=674, font="msmincho.ttc", face=1,
         edits=[dict(start=12,end=14,text="申請書類")]),
    dict(id="lo_newfeatures_append", source="lo_newfeatures_ja", seeded=True,
         width=532, bottom=170, font="msmincho.ttc", face=1,
         edits=[dict(start=68,end=68,text="新しい機能を試す前に、設定内容と保存先を確認してください。変更後はプレビューで表示と改行位置を確認し、必要な資料を保存してください。")]),
    dict(id="lo_enterprise_bold", source="lo_enterprise_en", page=2, ids=list(range(24)),
         width=226, x=29.8, indent=0, bottom=194, font="arial.ttf", face=0,
         edits=[dict(start=8,end=23,text="Document Editing")]),
    dict(id="word_takeo_mixed", source="word_takeo_notice", page=2, ids=list(range(455,470)),
         width=279, x=299.81, indent=0, bottom=502, font="meiryob.ttc", face=0,
         edits=[dict(start=3,end=5,text="減災")]),
    dict(id="print_ubiquiti_mixed", source="print_ubiquiti", page=1, ids=list(range(17)),
         width=200, x=25.51171875, indent=0, bottom=26, font="arial.ttf", face=0,
         edits=[dict(start=0,end=9,text="2026/9/08")]),
    dict(id="print_canvia_italic", source="print_canvia", page=3,
         ids=list(range(3,147)), width=468,x=90,indent=0,bottom=94,
         font="arial.ttf",face=0,change=("home","main")),
]


def font_mapping_audit(output, report):
    reader = PdfReader(output)
    if reader.is_encrypted:
        reader.decrypt("")
    fonts = reader.pages[report['selection']['page']-1]['/Resources']['/Font']
    checked = retained = 0
    for glyph in report['glyph_plan']:
        root = fonts[glyph['font_resource']].get_object()
        if glyph['source_index'] is not None:
            retained += 1
            continue  # Original graph/code/GID verification also runs below.
        child = root['/DescendantFonts'][0].get_object()
        mapping, widths, cid = child['/CIDToGIDMap'].get_data(), child['/W'], glyph['cid']
        assert int.from_bytes(mapping[2*cid:2*cid+2],'big') == glyph['glyph_id']
        assert int(widths[0]) == 1
        assert abs(float(widths[1][cid-1])-glyph['nominal_pdf_width']) < 1e-5
        assert root['/ToUnicode'].get_data()
        checked += 1
    return {'passed':True,'new_glyph_occurrences':checked,'retained_glyph_occurrences':retained}


def original_code_audit(source, report):
    content = ContentPage(source,report['selection']['page'])
    try:
        chars = {i:(event,char) for event in content.selected_events(set(report['selection']['glyph_ids']))
                 for char in event.chars for i in char.source_orders}
        for glyph in report['glyph_plan']:
            if glyph['source_index'] is None:
                continue
            event,char = chars[glyph['source_index']]
            assert glyph['code'] == char.code.hex()
            assert glyph['font_resource'] == event.state.font.name
        return {'passed':True,'policy':'original resource alias and encoded bytes retained; saved GIDs verified by backend'}
    finally:
        content.close()


def run_case(spec, directory, font_dir, seeded):
    directory.mkdir(parents=True)
    source = ROOT/'evaluations/realpdf/corpus'/f"{spec['source']}.pdf"
    item = {'case':spec['id'],'source':spec['source'],'source_sha256':source_sha(source),
            'status':'pending','spec':spec}
    output = directory/'edited.pdf'
    phase = 'source_preflight'
    try:
        if spec.get('seeded'):
            selection = dict(seeded[spec['source']]['selection'])
            selection['explicitly_supplied_width'] = spec['width']
        else:
            selection = make_selection(source,page=spec['page'],glyph_ids=spec['ids'],explicit_width=spec['width'])
        resolved = resolve_selection(source,selection)
        content = ContentPage(source,selection['page'])
        try:
            selected = set(selection['glyph_ids'])
            events = content.selected_events(selected)
            replay = patch_streams(content,events,selected,remove=False)[-content.page.xref]
        finally:
            content.close()
        replayed = directory/'source-replayed.pdf'
        replayed.write_bytes(program_pdf_bytes(source,selection['page']-1,replay))
        (directory/'source-replay').mkdir()
        audit = render_audit(source,replayed,selection['page'],directory/'source-replay',resolved.bbox.tuple())
        item['source_replay'] = audit
        require_render_audit(audit)
        if audit['poppler_diff']['all_changed_pixels'] != 0 or not audit['mupdf_page_pixels']:
            raise ValueError('source no-op was not visually identical; edit not attempted')
        before = independent_text(DEFAULT_PYPDF,source,directory/'source-text.json')
        replay_text = independent_text(DEFAULT_PYPDF,replayed,directory/'replay-text.json')
        if before.get('error') or replay_text.get('error') or before['pages'] != replay_text['pages']:
            raise ValueError('source no-op changed independent extraction')
        item['source_extraction_equal'] = True
        phase = 'edit'
        snapshot = inspect_paragraph(source,selection)
        write_json(directory/'paragraph.json',snapshot)
        edits = spec.get('edits')
        if edits is None:
            old,new = spec['change']
            start = snapshot['text'].index(old)
            edits = [dict(start=start,end=start+len(old),text=new)]
        fonts = {style['id']:{'path':str(font_dir/spec['font']),'font_index':spec['face']}
                 for style in snapshot['styles']}
        report = edit_paragraph(source,output,snapshot,edits,fonts=fonts,
            x=spec.get('x'),first_line_indent=spec.get('indent'),max_bottom=spec['bottom'],
            removal_output=directory/'removed.pdf')
        item['report'] = report
        write_json(directory/'report.json',report)
        phase = 'saved_output_audit'
        bounds = tuple(report['audit_bbox'][k] for k in ('x0','y0','x1','y1'))
        item['render_audit'] = render_audit(source,output,selection['page'],directory,bounds)
        require_render_audit(item['render_audit'])
        if item['render_audit']['poppler_diff_with_1pt_margin']['outside_changed_pixels'] != 0:
            raise ValueError('any-channel Poppler pixels changed outside the one-point margin')
        after = independent_text(DEFAULT_PYPDF,output,directory/'edited-text.json')
        removed = independent_text(DEFAULT_PYPDF,directory/'removed.pdf',directory/'removed-text.json')
        item['independent_edit'] = independent_edit_audit(before,after,selection['page'],snapshot['text'],report['composed_text'])
        item['independent_removal'] = independent_edit_audit(before,removed,selection['page'],snapshot['text'],'')
        item['font_mapping'] = font_mapping_audit(output,report)
        item['original_codes'] = original_code_audit(source,report)
        with pymupdf.open(source) as a,pymupdf.open(output) as b:
            item['drawings_unchanged'] = all(drawings_fingerprint(a[i])==drawings_fingerprint(b[i]) for i in range(len(a)))
            item['images_unchanged'] = all(image_fingerprints(a[i])==image_fingerprints(b[i]) for i in range(len(a)))
        if not item['drawings_unchanged'] or not item['images_unchanged']:
            raise ValueError('non-text objects changed')
        if source_sha(source) != item['source_sha256']:
            raise ValueError('source changed')
        item.update(status='passed',classification='original_font_retention_and_partial_substitution',output_sha256=source_sha(output))
    except Exception as exc:
        refusal = phase != 'saved_output_audit' and isinstance(exc,(PdfError,LayoutError)) and not output.exists()
        item.update(status='rejected' if refusal else 'failed_evaluation',
                    classification='safe_refusal' if refusal else 'evaluation_failure',
                    error=str(exc),error_type=type(exc).__name__,phase=phase,output_created=output.exists())
    write_json(directory/'result.json',item)
    return item


def compact(item):
    report = item.get('report',{})
    return {**{key:item.get(key) for key in ('case','source','source_sha256','output_sha256','status','classification','error','phase')},
        'styles':report.get('styles'), 'fonts':report.get('fonts'),
        'old_characters':len(report.get('before','')),'new_characters':len(report.get('after','')),
        'old_lines':report.get('old_line_count'),'new_lines':report.get('new_line_count'),
        'retained_glyph_count':report.get('retained_glyph_count'),
        'provided_font_glyph_count':report.get('provided_font_glyph_count'),
        'source_replay_poppler_changed_pixels':item.get('source_replay',{}).get('poppler_diff',{}).get('all_changed_pixels'),
        'poppler_outside_changed_pixels':item.get('render_audit',{}).get('poppler_diff_with_1pt_margin',{}).get('outside_changed_pixels'),
        'other_pages_mupdf_equal':item.get('render_audit',{}).get('outside_page_mupdf_pixels_equal'),
        **{k:item.get(k,{}).get('passed') for k in ('independent_edit','independent_removal','font_mapping','original_codes')},
        'drawings_unchanged':item.get('drawings_unchanged'),'images_unchanged':item.get('images_unchanged')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-name',required=True)
    parser.add_argument('--font-dir',type=Path,default=Path('C:/Windows/Fonts'))
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    directory = BASE/'runs'/args.run_name
    directory.mkdir(parents=True,exist_ok=False)
    seeded = {c['id']:c for c in json.loads((ROOT/'evaluations/backend/selections.json').read_text(encoding='utf-8'))}
    environment = {'engine_sha256':{p.name:source_sha(p) for p in (ROOT/'pdfeditor').glob('*.py')},
                   'runner_sha256':source_sha(Path(__file__)),'pymupdf':pymupdf.VersionBind}
    write_json(directory/'environment.json',environment)
    results = []
    for spec in CASES:
        item = run_case(spec,directory/spec['id'],args.font_dir,seeded)
        results.append(item)
        write_json(directory/'results.json',results)
        print(json.dumps({k:item.get(k) for k in ('case','status','error')},ensure_ascii=False),flush=True)
    summary = {'environment':environment,'counts':dict(Counter(x['status'] for x in results)),
               'cases':[compact(x) for x in results]}
    write_json(directory/'summary.json',summary)
    print(json.dumps(summary['counts']))
    return int(any(x['status']=='failed_evaluation' for x in results))


if __name__ == '__main__':
    raise SystemExit(main())
