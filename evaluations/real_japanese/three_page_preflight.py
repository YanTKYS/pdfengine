"""Measure a real-document boundary blocker before claiming three-page reflow.

The unchanged Wakayama corpus has consecutive presentation rules (4) and (5)
on PDF pages 4 and 5.  Its page number remains foreign.  This probe edits and
reopens rule (4), then attempts the current-revision next-page extension.  It
must not reinterpret the footer, headings or the later disqualification section
as body owners merely to obtain a passing three-page example.

A successful probe is NOT a successful three-page evaluation.  Its JSON verdict
always says NOT VALIDATED; unexpected acceptance is an error needing review.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import logging
from pathlib import Path
import platform
import shutil
import subprocess
import time

import pymupdf
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader

from pdfeditor.backend import PdfError
from pdfeditor.continuation import inspect_continuation_boundaries
from pdfeditor.owned_flow_proposal import propose_owned_flow_extension
from pdfeditor.page_proposal import _Analysis, accept_page_flow_report, propose_page_flow, replace_in_flow
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.source_ownership import immutable
from evaluations.story_flow.evaluate import annotation_fingerprint
from .continuation_edit import FONT_SHA, chars, codes, write


SOURCE = Path(__file__).resolve().parents[1] / 'realpdf/corpus/word_wakayama_guidelines.pdf'
URL = 'https://www.pref.wakayama.lg.jp/prefg/020400/d00220063_d/fil/jissiyouryou.pdf'
SHA = 'ccd4d63d09331ff6c6636ba6f2cc4c3398a8ff714abefe727cbd0051a6d5aa7c'
FIND = 'できない。'
INITIAL = 'できません。'
GROWTH = (INITIAL + '応募者は事前に必要な手順を確認してください。'
          '説明の内容について質問がある場合には、担当者に確認してください。'
          '当日の進行が円滑になるように、必要な準備を行ってください。')


def proposal(source, number, line_ids, output, font):
    return propose_page_flow(source, number, line_ids=line_ids, font_candidates=[font],
        include_embedded_fonts=True, font_cache=output / 'fonts', new_text={'style-1': GROWTH})


def snapshot(state):
    return {sid: dict(paragraph=s['paragraph_id'], page=state['regions'][s['region_id']]['page'],
                     characters=len(s['binding']['paragraph']['text']), range=s['range'],
                     occupancy=s['occupancy']) for sid, s in state['slots'].items()}


def raster_and_foreign(before, after, state, output, *, whole=False):
    """Independent all-page foreign-content and two-renderer pixel checks."""
    regions = {}
    for r in state['regions'].values():
        regions.setdefault(r['page'], []).append(pymupdf.Rect(r['bounds']))
    old_reader, new_reader = PdfReader(before), PdfReader(after)
    checks = []
    output.mkdir()
    with pymupdf.open(before) as old, pymupdf.open(after) as new:
        assert len(old) == len(new) == 6
        for number, (a, b) in enumerate(zip(old, new), 1):
            owned_boxes = regions.get(number, [])
            boxes = [] if whole else owned_boxes
            outside = lambda page: [c for c in chars(page)
                if not any(r.contains(pymupdf.Point(c[2])) for r in owned_boxes)]
            assert outside(a) == outside(b), ('foreign glyphs', number)
            drawings = lambda page: [{k: v for k, v in d.items() if k != 'seqno'} for d in page.get_drawings()]
            links = lambda page: [{k: v for k, v in d.items() if k != 'xref'} for d in page.get_links()]
            assert drawings(a) == drawings(b) and links(a) == links(b)
            assert a.get_image_info(hashes=True) == b.get_image_info(hashes=True)
            assert annotation_fingerprint(old_reader, number) == annotation_fingerprint(new_reader, number)
            x, y = a.get_pixmap(dpi=144), b.get_pixmap(dpi=144)
            diff = ImageChops.difference(Image.frombytes('RGB', (x.width, x.height), x.samples),
                                        Image.frombytes('RGB', (y.width, y.height), y.samples))
            for box in boxes:
                rect = (box * pymupdf.Matrix(2, 2)).irect
                ImageDraw.Draw(diff).rectangle((rect.x0, rect.y0, rect.x1 - 1, rect.y1 - 1), fill=0)
            assert diff.getbbox() is None, ('MuPDF pixels', number)
            for label, path in (('before', before), ('after', after)):
                subprocess.run(['pdftoppm', '-f', str(number), '-l', str(number), '-singlefile',
                    '-scale-to', '1600', '-png', str(path), str(output / label)], check=True, capture_output=True)
            with Image.open(output / 'before.png') as xi, Image.open(output / 'after.png') as yi:
                x, y = xi.convert('RGB'), yi.convert('RGB')
                diff = ImageChops.difference(x, y)
                for box in boxes:
                    rect = (box * pymupdf.Matrix(x.width / a.rect.width, x.height / a.rect.height)).irect
                    ImageDraw.Draw(diff).rectangle((rect.x0, rect.y0, rect.x1 - 1, rect.y1 - 1), fill=0)
                assert diff.getbbox() is None, ('Poppler pixels', number)
            checks.append(dict(page=number, mupdf_changed_pixels=0, poppler_changed_pixels=0,
                               foreign_glyphs_drawings_images_links_annotations_equal=True))
    return dict(scope='whole-document' if whole else 'outside-confirmed-regions', pages=checks)


@proof_session
def run(output, font):
    start = time.monotonic()
    started = datetime.now(timezone.utc).isoformat()
    assert source_sha(SOURCE) == SHA and source_sha(font) == FONT_SHA
    assert shutil.which('pdftoppm'), 'Poppler is required for this probe'
    output.mkdir(parents=True, exist_ok=False)
    first = proposal(SOURCE, 4, ['p4-l37'], output, font)
    write(output / 'first-proposal.json', first)
    accepted = accept_page_flow_report(SOURCE, first, provider_choices={
        'style-1': dict(sha256=FONT_SHA, font_index=0, relation='substituted')})
    write(output / 'first-accepted.json', accepted)
    state0 = accepted['state']
    pdf, side = output / 'saved.pdf', output / 'saved.json'
    report = edit_shared_flow(SOURCE, state0, pdf, side,
        replace_in_flow(state0, FIND, INITIAL, paragraph_id='P1', style_id='style-1'))
    write(output / 'saved-report.json', report)
    opened = open_shared_flow(pdf, side)
    assert opened['status'] == 'restored', opened.get('reason')
    state = opened['state']
    print('page-4 edit saved and restored', flush=True)
    raster = raster_and_foreign(SOURCE, pdf, state, output / 'saved-raster')
    noop, noop_side = output / 'noop.pdf', output / 'noop.json'
    noop_report = edit_shared_flow(pdf, state, noop, noop_side, {})
    write(output / 'noop-report.json', noop_report)
    restored = open_shared_flow(noop, noop_side)
    assert restored['status'] == 'restored', restored.get('reason')
    noop_state = restored['state']
    for sid in state['slots']:
        assert immutable(state['slots'][sid]['source_output']) == immutable(noop_state['slots'][sid]['source_output'])
    assert state['generated_fonts'] == noop_state['generated_fonts']
    assert codes(pdf, 4) == codes(noop, 4), 'no-op glyph codes/font resources/programs changed'
    with pymupdf.open(pdf) as a, pymupdf.open(noop) as b:
        before_chars, after_chars = chars(a[3]), chars(b[3])
        assert [c[:2] for c in before_chars] == [c[:2] for c in after_chars], 'no-op Unicode/GIDs changed'
        origin_drift = max(abs(x[2][k] - y[2][k]) for x, y in zip(before_chars, after_chars) for k in (0, 1))
    noop_raster = raster_and_foreign(pdf, noop, noop_state, output / 'noop-raster', whole=True)
    source, state = noop, noop_state
    blocked_pdf, blocked_side = output / 'capacity-refused.pdf', output / 'capacity-refused.json'
    try:
        edit_shared_flow(source, state, blocked_pdf, blocked_side,
            replace_in_flow(state, INITIAL, GROWTH, paragraph_id='P1', style_id='style-1'))
    except PdfError as exc:
        capacity = str(exc)
    else:
        raise AssertionError('growth unexpectedly fits; select and review a new actual capacity probe')
    assert 'exceed all explicitly confirmed shared regions' in capacity, capacity
    assert not blocked_pdf.exists() and not blocked_side.exists()
    page = proposal(source, 5, ['p5-l1'], output, font)
    write(output / 'next-page-proposal.json', page)
    assert page['binding']['source_sha256'] == source_sha(source)
    # Caller-reviewed continuation for rule (4), releasing only the next rule (5).
    region = dict(page=5, bounds=[96.72000122070312, 45, 498.6000061035156, 80],
                  x=96.72000122070312, width=401.8800048828125, first_baseline=50)
    boundary = next(c for c in inspect_continuation_boundaries(source, 5)['candidates'] if c['ordinal'] == 0)
    confirmation = dict(destination_id='A-next', paragraph_id='P1', region_id='release-5', page=5,
        bounds=region['bounds'], insertion='confirmed-page-program-boundary',
        graphics_state='confirmed-boundary-state', boundary=boundary['boundary_id'])
    request = dict(region_id='page-5', page_break=dict(before='P1', after='B', minimum_baseline_gap=26.4,
        placement='source-region-floor-and-same-page-follow'), vacated_continuation=dict(region_id='release-5',
        region=region, release_paragraphs=['B'], confirmation=confirmation))
    write(output / 'requested-next-page.json', request)
    original_state = deepcopy(state)
    try:
        propose_owned_flow_extension(source, state, [page], paragraphs=[dict(id='B', parts=[dict(page=5, paragraph_id='P1')])],
            relationship='Wakayama presentation cautions: rule ④ is followed by rule ⑤ on the next page; '
                         'page numbers, headings, and later sections remain foreign.', next_page=request)
    except PdfError as exc:
        refusal = str(exc)
    else:
        raise AssertionError('boundary behavior changed; this refusal probe cannot certify three-page success')
    assert refusal == 'page seam skips unselected text after the last confirmed paragraph', refusal
    assert state == original_state
    assert not state.get('continuation_destinations', {})
    tail = state['slots']['slot-0']['occupancy']['last_baseline']
    bounds = state['regions']['R1']['bounds']
    obstructing = [dict(id=l['id'], text=l['text'], baseline=l['baseline'], bounds=l['bbox'], glyph_ids=l['glyph_ids'])
        for l in _Analysis(source, 4).lines if l['baseline'] > tail and l['bbox'][0] < bounds[2] and l['bbox'][2] > bounds[0]]
    assert any(l['text'].strip() == '4' for l in obstructing)
    assert source_sha(SOURCE) == SHA
    result = dict(verdict='REAL-WORLD THREE-PAGE SEQUENTIAL REFLOW — NOT VALIDATED',
        outcome='current-revision next-page proposal REFUSED; no extension was authorized',
        source=dict(url=URL, sha256=SHA, pages=6), font_sha256=FONT_SHA,
        environment=dict(python=platform.python_version(), platform=platform.platform(),
                         pymupdf=pymupdf.VersionBind, mupdf=pymupdf.VersionFitz,
                         poppler=subprocess.run(['pdftoppm', '-v'], capture_output=True, text=True, check=True).stderr.splitlines()[0]),
        started_utc=started, elapsed_seconds=time.monotonic() - start,
        source_owner_before=snapshot(state0), source_owner_after=snapshot(state),
        saved_sha256=source_sha(pdf), current_sha256=source_sha(source),
        saved_reopen='restored', noop_reopen='restored', saved_audit=raster, noop_audit=noop_raster,
        noop_codes_gids_resources_programs_equal=True, noop_generated_font_ownership_equal=True,
        noop_source_output_identity_equal=True, noop_max_origin_drift_pt=origin_drift,
        capacity_refusal=capacity, capacity_pdf_and_sidecar_absent=True,
        extension_refusal=refusal, obstructing_foreign_lines=obstructing,
        next_page_observation_sha256=page['binding']['source_sha256'],
        requested_release_owners=['B'], granted_destinations=state.get('continuation_destinations', {}),
        state_unchanged_after_refusal=True, original_sha_unchanged=True,
        three_page_reflow_executed=False,
        unrun=['first-extension acceptance/growth', 'second extension', 'page-6 continuation',
               'independent release authorities', 'three-page shorten/regrow lifecycle'])
    write(output / 'summary.json', result)
    print(result['verdict'], refusal, flush=True)
    return result


if __name__ == '__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--font', type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.font)
