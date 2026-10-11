"""Real Japanese continuation into text space released by jointly owned followers."""
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
from pdfeditor.continuation import confirm_continuation_destination, slot_id, inspect_continuation_boundaries
from pdfeditor.continuation_review import review_continuation_geometry
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.shaped_font import ShapedFont
from .owned_body_edit import SOURCE, URL, SHA, audit
from .continuation_edit import FONT_SHA, write, chars, codes

FIND = '望むタイプでもあります。'
LONG = '望むタイプでもあります。試行の結果を共有し、利用者の疑問や改善の提案を記録することで、次の移行に役立てます。'
LONG += '担当者は実際の文書を使って操作を試し、変更の理由と利用方法を説明します。得られた意見を整理し、部門間で知識を共有することで、組織全体の移行を支援します。'
SECOND = ('変化を肯定的', '新しい変化を肯定的')
GROUPS = [dict(id='A', parts=[dict(page=8, paragraph_id='P1')])] + [
    dict(id=chr(65+i), parts=[dict(page=9, paragraph_id=f'P{i}')]) for i in range(1,8)]
RELATIONSHIP = ('Page 8 finishes the independent paragraph about innovators with むタイプでもあります。. '
    'Page 9 starts the independent paragraph アーリーアダプターは. These are consecutive adopter-category '
    'explanations within the communication strategy section. Page 9 continues the adopter categories and '
    'their communication-strategy conclusion in seven complete paragraphs, all explicitly selected. '
    'No unselected text is interposed at the seam; the next section on page 10 stays foreign.')
BREAK = dict(before='A', after='B', minimum_baseline_gap=26.4,
             placement='source-region-floor-and-same-page-follow')


def audit_original_noop(source, pdf, initial, state):
    count = 0; errors = []
    with pymupdf.open(source) as before, pymupdf.open(pdf) as after:
        for page in (8, 9):
            a, b = chars(before[page-1]), chars(after[page-1])
            ac, bc = codes(source, page), codes(pdf, page)
            for sid, slot in initial['slots'].items():
                if initial['regions'][slot['region_id']]['page'] != page: continue
                old = slot['binding']['paragraph']['selection']['glyph_ids']
                new = state['slots'][sid]['binding']['paragraph']['selection']['glyph_ids']
                assert len(old) == len(new)
                for i, j in zip(old, new):
                    assert ac[i] == bc[j] and a[i][:2] == b[j][:2]
                    error = max(abs(a[i][2][k] - b[j][2][k]) for k in (0, 1))
                    assert error < .002
                    errors.append(error); count += 1
    return dict(glyphs=count, source_codes_resources_programs_gids_equal=True, max_origin_error_pt=max(errors))


@proof_session
def run(output, font):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA and source_sha(font) == FONT_SHA
    pages = []
    for number, ids in [(8, range(24, 28)), (9, range(1, 24))]:
        kwargs = dict(line_ids=[f'p{number}-l{i}' for i in ids], font_candidates=[font],
                      include_embedded_fonts=True, font_cache=output / 'fonts')
        observed = propose_page_flow(SOURCE, number, **kwargs)
        pages.append(propose_page_flow(SOURCE, number, **kwargs,
            new_text={style['id']: LONG + SECOND[1] for style in observed['styles']}))
    region = dict(page=9, bounds=[56.79999923706055, 110, 540, 174],
        x=56.79999923706055, width=483.20000076293945, first_baseline=122)
    review = review_continuation_geometry(SOURCE, 9, region['bounds']); write(output / 'review.json', review)
    boundary = next(c for c in inspect_continuation_boundaries(SOURCE, 9)['candidates'] if c['ordinal'] == 0)
    confirmation = dict(destination_id='A-vacated', paragraph_id='A', region_id='vacated-9', page=9,
        bounds=region['bounds'], insertion='confirmed-page-program-boundary',
        graphics_state='confirmed-boundary-state', boundary=boundary['boundary_id'])
    try: confirm_continuation_destination(SOURCE, **confirmation)
    except PdfError as exc: empty_refusal = str(exc)
    else: raise AssertionError('occupied B region was authorized as empty')
    proposal = propose_owned_flow(SOURCE, pages, paragraphs=GROUPS, relationship=RELATIONSHIP,
        page_breaks=[BREAK], vacated_continuations=[dict(region_id='vacated-9', before_region='page-9',
            region=region, release_paragraphs=['B'], confirmation=confirmation)])
    write(output / 'proposal.json', proposal)
    accepted = accept_owned_flow_report(SOURCE, proposal, provider_choices={str(page['binding']['page']):
        {s['id']: dict(sha256=FONT_SHA, font_index=0, relation='substituted') for s in page['styles']} for page in pages})
    write(output / 'acceptance.json', accepted['receipt'])
    initial = state = accepted['state']; write(output / 'initial.json', state)
    dest = state['continuation_destinations']['A-vacated']
    originals = {pid: p['logical']['text'] for pid, p in state['paragraphs'].items()}
    expected = dict(originals); source = SOURCE; provider = ShapedFont(font)
    result = dict(verdict='NOT VALIDATED', source=dict(url=URL, sha256=SHA, paragraphs=originals),
        grouping=GROUPS, relationship=RELATIONSHIP, regions=state['regions'], acceptance=accepted['receipt'],
        page_break=BREAK, boundary=boundary, independent_empty_refusal=empty_refusal,
        provider=dict(sha256=FONT_SHA, face=0, appearance='explicit new-glyph substitution; source equivalence not claimed'), stages=[])
    # Original no-op: all source glyphs, including followers, stay in place.
    noop_pdf, noop_model = output / '0-noop.pdf', output / '0-noop.json'
    edit_shared_flow(SOURCE, state, noop_pdf, noop_model, {})
    noop_opened = open_shared_flow(noop_pdf, noop_model)
    assert noop_opened['status'] == 'restored'
    result['original_noop_identity'] = audit_original_noop(SOURCE, noop_pdf, initial, noop_opened['state'])
    with pymupdf.open(SOURCE) as a, pymupdf.open(noop_pdf) as b:
        assert [p.get_pixmap(dpi=144).samples for p in a] == [p.get_pixmap(dpi=144).samples for p in b]
    result['original_noop_raster_equal'] = True
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
            assert len(first['lines']) == 4 and len(continued['lines']) >= 2
            gap = follower['lines'][0]['baseline'] - continued['lines'][-1]['baseline']
            assert abs(gap - 26.4) < .002 and shift > 20
            assert any(y['baseline'] >= 142 for y in continued['lines']), 'only original blank prefix used'
            witness = next(s for s in report['steps'] if s['slot_id'] == slot_id(dest))['report']['vacated_space']
            assert witness['owners'][0]['source_owner'] == 'slot-1'
            checked['release_transaction_witness'] = witness
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
                old_boxes = [pymupdf.Rect(old[i][3]) for i in old_ids]
                used = [pymupdf.Rect(r) for r in continued['ink_bounds']]
                covered = [i for i,box in zip(old_ids,old_boxes) if any(box.intersects(r) for r in used)]
                assert covered, 'continuation did not use any original B glyph space'
                checked['original_B_glyphs_in_continued_ink'] = covered
                current_ids = state['slots'][slot_id(dest)]['binding']['paragraph']['selection']['glyph_ids']
                actual_covered = [i for i in old_ids if any(pymupdf.Rect(old[i][3]).intersects(
                    pymupdf.Rect(new[j][3])) for j in current_ids)]
                assert actual_covered
                assert all(not pymupdf.Rect(new[j][3]).intersects(pymupdf.Rect(new[k][3]))
                           for j in current_ids for k in new_ids)
                checked['original_B_glyphs_intersecting_actual_new_A'] = actual_covered
                checked['A_B_final_glyph_bounds_disjoint'] = True
                checked['former_empty_prefix'] = dict(bounds=[region['bounds'][0],110,region['bounds'][2],130],
                    capacity_lines=1, actual_continued_lines=len(continued['lines']))
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
    result.update(verdict='REAL-WORLD VACATED-SPACE REFLOW VALIDATED — PASS', original_sha_unchanged=True)
    write(output / 'summary.json', result); print(result['verdict'], flush=True)
    return result


if __name__ == '__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--font', type=Path, required=True)
    args = parser.parse_args(); run(args.output, args.font)
