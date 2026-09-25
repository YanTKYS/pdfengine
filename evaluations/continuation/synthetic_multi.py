"""Dry run of multi_destination.py on a synthetic stand-in; never evidence for the external PDF.

It exercises the same editing series, audits and refusals (MuPDF, Poppler,
independent pypdf, fonts, paint, images, annotations, page-entry chain) on a
generated six-page PDF whose pages 4-6 mimic the reviewed geometry: a
paragraph fragment on pages 4 and 5, a fixed figure at the protected area,
and the reviewed page-6 area split into the same two regions. Only the input
(source, story, provider) and the external tool paths differ from the real
run. Results go to runs/synthetic-<name>/ and are not published.

    python -m evaluations.continuation.synthetic_multi --run-name <name> \\
        --poppler /usr/bin/pdftoppm --pypdf /path/to/other/python
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

import pymupdf
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.continuation import confirm_continuation_destination
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.shared_flow import confirm_shared_flow
from pdfeditor.story_flow import confirm_story
import evaluations.backend.followup as renderer_paths
import evaluations.elements.evaluate as extractor_paths
from evaluations.continuation import multi_destination as multi
from evaluations.continuation import evaluate as single


HEIGHT = 842
# Pages 4 and 5 hold the paragraph's two source fragments, as in the reviewed PDF.
FRAGMENTS = {'A': dict(page=4, bounds=[66, 758, 540, 785], x=66, baseline=772, width=471, bottom=785,
                       lines=['Synthetic stand-in paragraph starts near the bottom of page four and']),
             'B': dict(page=5, bounds=[55, 130, 540, 194], x=55, baseline=145, width=482, bottom=194,
                       lines=['continues on page five for three lines, so that the confirmed source slots',
                              'hold the original text while the added Japanese sentences overflow into',
                              'the reviewed empty area on page six, first one region then both regions.'])}


def _font(writer, name):
    return writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject(name), NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
        NameObject('/FirstChar'): NumberObject(32), NameObject('/LastChar'): NumberObject(126),
        NameObject('/Widths'): ArrayObject([NumberObject(600)] * 95)}))


def source_pdf(path):
    writer = PdfWriter()
    fonts = DictionaryObject({NameObject('/F1'): _font(writer, '/Courier')})
    for number in range(1, 7):
        page = writer.add_blank_page(595, HEIGHT)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): fonts})
        program = []
        for fragment in FRAGMENTS.values():
            if fragment['page'] == number:
                for i, line in enumerate(fragment['lines']):
                    y = HEIGHT - (fragment['baseline'] + 21.6 * i)
                    program.append(f'BT /F1 10.5 Tf 1 0 0 1 {fragment["x"]} {y:.3f} Tm ({line}) Tj ET')
        if number >= 4:
            # A fixed figure at the protected area and a later paragraph that stays fixed.
            x0, y0, x1, y1 = multi.PROTECTED
            program.append(f'0.2 0.4 0.8 rg {x0} {HEIGHT - y1} {x1 - x0} {y1 - y0} re f 0 g')
            program.append(f'BT /F1 10.5 Tf 1 0 0 1 55 {HEIGHT - 400} Tm (Fixed text on page {number}.) Tj ET')
        else:
            program.append(f'BT /F1 12 Tf 1 0 0 1 55 {HEIGHT - 100} Tm (Page {number}) Tj ET')
        stream = DecodedStreamObject()
        stream.set_data(' '.join(program).encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return path


def prepare(directory):
    source = source_pdf(directory / 'synthetic-source.pdf')
    font = directory / 'provider.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    specs = {}
    for ident, f in FRAGMENTS.items():
        # The fragment is painted first on its page: its glyphs are the first ones.
        count = sum(len(line) for line in f['lines'])
        p = inspect_paragraph(source, make_selection(source, f['page'], glyph_ids=list(range(count)),
                                                     explicit_width=f['width']))
        specs[ident] = dict(page=f['page'], bounds=f['bounds'], paragraph=p, paint_relations=[],
            layout=dict(x=f['x'], baseline=f['baseline'], width=f['width'], max_bottom=f['bottom'],
                        min_line_height=21.6, first_line_indent=0))
    story = confirm_story(source, specs, paragraph_id='synthetic-paragraph', chain=['A', 'B'],
        styles={'body': dict(provider=dict(path=str(font)), provider_relation='confirmed_reflow_provider')},
        style_assignments={'A': {'s0': 'body'}, 'B': {'s0': 'body'}}, typing_style_id='body',
        protected_regions={str(i): [dict(role='fixed', bounds=multi.PROTECTED)] for i in (4, 5)})
    pid = story['logical']['id']
    regions = {ident: dict(page=c['page'], bounds=c['bounds'], x=c['layout']['x'], width=c['layout']['width'],
                           first_baseline=c['layout']['baseline']) for ident, c in story['containers'].items()}
    regions.update(json.loads(json.dumps(multi.SPLIT)))
    confirmed = {}
    for region, spec in multi.SPLIT.items():
        d = confirm_continuation_destination(source, destination_id='reviewed-' + region, paragraph_id=pid,
            region_id=region, page=spec['page'], bounds=spec['bounds'], insertion='before-page-program',
            graphics_state='isolated-pdf-initial-state', page_entry_order=multi.PAGE_ENTRY_ORDER[region])
        confirmed[d['destination_id']] = d
    state = confirm_shared_flow(source, {pid: story}, flow_id='synthetic-multi-continuation', paragraph_order=[pid],
        regions=regions, region_order=list(regions), slot_regions={pid: {'A': 'A', 'B': 'B'}},
        paragraph_policies={pid: dict(min_line_height=21.6, first_line_indent=0, keep_together=False,
            break_before='auto', break_after='auto', empty=dict(kind='reserve-line', ascent=8.4, descent=2.1))},
        follows=[], protected_regions={str(i): [dict(role='fixed', bounds=multi.PROTECTED)] for i in (4, 5, 6)},
        continuation_destinations=confirmed)
    return source, state, pid, {d['region_id']: d for d in confirmed.values()}


def run(directory, poppler, pypdf_python):
    # The only environment substitution: where the independent tools live.
    renderer_paths.DEFAULT_POPPLER = Path(poppler)
    extractor_paths.DEFAULT_PYPDF = Path(pypdf_python)
    environment = single.tools()
    source, state, pid, dests = prepare(directory)
    sequential, simultaneous, refused = multi.compare_series(directory, state, pid, dests, source)
    engine = {p.name: source_sha(p) for p in sorted((multi.ROOT / 'pdfeditor').glob('*.py'))}
    return dict(schema='pdfengine-multi-destination-dry-run-1', status='passed', evidence='synthetic stand-in; not the external PDF',
        sequential=sequential, simultaneous=simultaneous, negative_controls=[refused],
        environment=dict(engine_digest=hashlib.sha256(json.dumps(engine, sort_keys=True).encode()).hexdigest(),
            multi_runner_sha256=source_sha(Path(multi.__file__)), runner_sha256=source_sha(Path(__file__)),
            python=sys.version.split()[0], platform=platform.platform(), pymupdf=pymupdf.VersionBind, **environment))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--poppler', required=True)
    parser.add_argument('--pypdf', required=True, help='a separate Python interpreter with pypdf installed')
    args = parser.parse_args()
    directory = multi.BASE / 'runs' / ('synthetic-' + args.run_name)
    directory.mkdir(parents=True)
    single.write(directory / 'summary.json', run(directory, args.poppler, args.pypdf))
