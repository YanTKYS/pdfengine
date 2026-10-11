"""Unmodified LibreOffice pages 8–9: independent paragraphs with an explicit source page break."""
import argparse
import json
import logging
from pathlib import Path
import shutil
import subprocess

import pymupdf
from PIL import Image, ImageChops, ImageDraw

from pdfeditor.backend import PdfError
from pdfeditor.owned_flow_proposal import propose_owned_flow, accept_owned_flow_report
from pdfeditor.page_proposal import propose_page_flow, replace_in_flow
from pdfeditor.proof_session import proof_session
from pdfeditor.continuation import confirm_continuation_destination, slot_id
from pdfeditor.continuation_review import review_continuation_geometry, build_continuation_boundary_confirmation_request
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.shaped_font import ShapedFont
from .owned_body_edit import SOURCE, URL, SHA, audit
from .continuation_edit import FONT_SHA, write, chars

FIND = '望むタイプでもあります。'
LONG = '望むタイプでもあります。試行の結果を共有し、利用者の疑問や改善の提案を記録することで、次の移行に役立てます。'
SECOND = ('変化を肯定的', '新しい変化を肯定的')
GROUPS = [dict(id=pid, parts=[dict(page=n, paragraph_id='P1')]) for pid, n in [('A', 8), ('B', 9)]]
RELATIONSHIP = ('Page 8 finishes the independent paragraph about innovators with むタイプでもあります。. '
    'Page 9 starts the independent paragraph アーリーアダプターは. These are consecutive adopter-category '
    'explanations within the communication strategy section. No unselected text is interposed at the seam.')
BREAK = dict(before='A', after='B', minimum_baseline_gap=26.4,
             placement='source-region-floor-and-same-page-follow')


@proof_session
def run(output, font):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA and source_sha(font) == FONT_SHA
    pages = []
    for number, ids in [(8, range(24, 28)), (9, range(1, 4))]:
        kwargs = dict(line_ids=[f'p{number}-l{i}' for i in ids], font_candidates=[font],
                      include_embedded_fonts=True, font_cache=output / 'fonts')
        observed = propose_page_flow(SOURCE, number, **kwargs)
        pages.append(propose_page_flow(SOURCE, number, **kwargs,
            new_text={style['id']: LONG + SECOND[1] for style in observed['styles']}))
    region = dict(page=9, bounds=[56.79999923706055, 110, 538.2000007629395, 130],
        x=56.79999923706055, width=481.4000015258789, first_baseline=122)
    review = review_continuation_geometry(SOURCE, 9, region['bounds']); write(output / 'review.json', review)
    boundary = next(c for group in review['groups'] for c in group['candidates'] if c['ordinal'] == 0)
    assert boundary['geometry']['checks_passed']
    request = build_continuation_boundary_confirmation_request(review, boundary_id=boundary['boundary_id'],
        destination_id='A-prefix', paragraph_id='A', region_id='prefix-9')
    dest = confirm_continuation_destination(SOURCE, **request['confirm_kwargs'])
    proposal = propose_owned_flow(SOURCE, pages, paragraphs=GROUPS, relationship=RELATIONSHIP,
        page_breaks=[BREAK], page_overrides={'9': {'region_top': 130}},
        continuations=[dict(region_id='prefix-9', before_region='page-9', region=region, destination=dest)])
    write(output / 'proposal.json', proposal)
    accepted = accept_owned_flow_report(SOURCE, proposal, provider_choices={str(page['binding']['page']):
        {s['id']: dict(sha256=FONT_SHA, font_index=0, relation='substituted') for s in page['styles']} for page in pages})
    write(output / 'acceptance.json', accepted['receipt'])
    initial = state = accepted['state']; write(output / 'initial.json', state)
    originals = {pid: p['logical']['text'] for pid, p in state['paragraphs'].items()}
    expected = dict(originals); source = SOURCE; provider = ShapedFont(font)
    result = dict(verdict='NOT VALIDATED', source=dict(url=URL, sha256=SHA, paragraphs=originals),
        grouping=GROUPS, relationship=RELATIONSHIP, regions=state['regions'], acceptance=accepted['receipt'],
        page_break=BREAK, boundary=boundary,
        provider=dict(sha256=FONT_SHA, face=0, appearance='explicit new-glyph substitution; source equivalence not claimed'), stages=[])
    edits = [{'A': (FIND, LONG)}, {'B': SECOND}, {'A': (LONG, FIND), 'B': SECOND[::-1]}, {'A': (FIND, LONG)}, {}]
    for index, (name, replacements) in enumerate(zip(('grow', 'second', 'shorten', 'regrow', 'noop'), edits), 1):
        changes = {}
        for pid, edit in replacements.items():
            changes.update(replace_in_flow(state, *edit, paragraph_id=pid,
                style_id='page-8:style-1' if pid == 'A' else 'page-9:style-1'))
            expected[pid] = expected[pid].replace(*edit)
        pdf, model = output / f'{index}-{name}.pdf', output / f'{index}-{name}.json'
        report = edit_shared_flow(source, state, pdf, model, changes); write(output / f'{index}-report.json', report)
        opened = open_shared_flow(pdf, model); assert opened['status'] == 'restored', opened.get('reason')
        state = opened['state']
        assert {pid: p['logical']['text'] for pid, p in state['paragraphs'].items()} == expected
        checked = audit(source, pdf, initial, state, report, provider)
        first, continued, follower = [report['plan']['fragments'][sid] for sid in ('slot-0', slot_id(dest), 'slot-1')]
        assert first['text'] + continued['text'] == expected['A'] and follower['text'] == expected['B']
        shift = follower['lines'][0]['baseline'] - 142
        gap = None
        if name == 'shorten':
            assert expected == originals and len(first['lines']) == 4 and not continued['lines'] and shift == 0
            assert state['slots'][slot_id(dest)]['occupancy'] is None
        else:
            assert len(first['lines']) == 4 and len(continued['lines']) == 1
            gap = follower['lines'][0]['baseline'] - continued['lines'][-1]['baseline']
            assert abs(gap - 26.4) < .002 and abs(shift - 6.4) < .002
        if name == 'grow':
            old_ids = initial['slots']['slot-1']['binding']['paragraph']['selection']['glyph_ids']
            new_ids = state['slots']['slot-1']['binding']['paragraph']['selection']['glyph_ids']
            with pymupdf.open(SOURCE) as a, pymupdf.open(pdf) as b:
                old, new = chars(a[8]), chars(b[8])
                assert len(old_ids) == len(new_ids) == 146
                error = max(abs(new[j][2][axis] - old[i][2][axis] - (shift if axis == 1 else 0))
                            for i, j in zip(old_ids, new_ids) for axis in (0, 1))
                assert error < .002
                checked['unchanged_follower_translation_error_pt'] = error
        if name == 'noop':
            with pymupdf.open(source) as a, pymupdf.open(pdf) as b:
                assert [p.get_pixmap(dpi=144).samples for p in a] == [p.get_pixmap(dpi=144).samples for p in b]
        result['stages'].append(dict(stage=name, reopen='restored', edits=replacements,
            follower_push_down_pt=shift, paragraph_baseline_gap_pt=gap, audit=checked))
        write(output / 'progress.json', result)
        print(name, 'restored', 'push', shift, [(p['paragraph'], p['page'], len(p['lines'])) for p in checked['placements']], flush=True)
        source = pdf
    refused_pdf, refused_model = output / 'refused.pdf', output / 'refused.json'
    try:
        edit_shared_flow(source, state, refused_pdf, refused_model,
            replace_in_flow(state, LONG, LONG * 3, paragraph_id='A', style_id='page-8:style-1'))
    except PdfError as exc:
        result['over_capacity_refusal'] = str(exc)
    else:
        raise AssertionError('unowned region or foreign heading overwritten')
    assert not refused_pdf.exists() and not refused_model.exists() and source_sha(SOURCE) == SHA
    if shutil.which('pdftoppm'):
        for name, path in [('before', SOURCE), ('after', source)]:
            subprocess.run(['pdftoppm', '-f', '8', '-l', '9', '-scale-to', '1600', '-png', str(path), str(output / name)],
                           check=True, capture_output=True)
        with pymupdf.open(SOURCE) as doc:
            for number in (8, 9):
                a = Image.open(output / f'before-{number:02}.png').convert('RGB')
                b = Image.open(output / f'after-{number:02}.png').convert('RGB')
                diff = ImageChops.difference(a, b)
                for region in (r for r in state['regions'].values() if r['page'] == number):
                    mask = (pymupdf.Rect(region['bounds']) * pymupdf.Matrix(a.width / doc[number-1].rect.width, a.height / doc[number-1].rect.height)).irect
                    ImageDraw.Draw(diff).rectangle((mask.x0, mask.y0, mask.x1-1, mask.y1-1), fill=(0, 0, 0))
                assert diff.getbbox() is None
        result['poppler_outside_changed_pixels'] = {'8': 0, '9': 0}
    provider.font.close()
    result.update(verdict='REAL-WORLD CROSS-PAGE PARAGRAPH REFLOW VALIDATED — PASS', original_sha_unchanged=True)
    write(output / 'summary.json', result); print(result['verdict'], flush=True)
    return result


if __name__ == '__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--font', type=Path, required=True)
    args = parser.parse_args(); run(args.output, args.font)
