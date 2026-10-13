"""Approved follower spacing on one edited history (LibreOffice pages 8–9).

The PR #72 history is rebuilt: A on page 8 is edited and saved, the next-page path adopts B with a
release-only continuation, and A's growth moves B down, compressing the unowned B→C gap from 26.4 pt to
about 20.0 pt. The same-page extension then appends C–H with an explicitly approved placement policy for C:
a page-9 baseline floor and a minimum gap after B. The current witness (C's current baseline and gap) and
the approved policy are reviewed separately. The extension no-op must move nothing. Shortening A must
return B to its source floor without pulling C above the approved floor. Regrowth, a second edit and the
final no-op must all save and reopen.
"""
import argparse
from copy import deepcopy
import logging
from pathlib import Path

from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import _Analysis, accept_page_flow_report, replace_in_flow
from pdfeditor.post_edit_extension import FOLLOWER_PLACEMENT
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.continuation import slot_id
from pdfeditor.owned_flow_proposal import propose_owned_flow_extension
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from pdfeditor.source_ownership import immutable
from pdfeditor.shaped_font import ShapedFont
from .extended_joint_edit import FIND, LONG, SECOND, RELATIONSHIP as FOLLOWERS_RELATIONSHIP
from .owned_body_edit import SOURCE, URL, SHA, audit
from .continuation_edit import FONT_SHA, write
from .vacated_space_edit import audit_original_noop
from .post_edit_extension import SHORT, pixel_equal, poppler
from .next_page_extension import INITIAL, REGION, provider, page_proposal
from .sequential_extension import STYLE, refuse, first_extension, second_extension

PR72_SHORTENED_C = 202.800012  # C's first baseline after shortening under the PR #72 current-gap rule
ORIGINAL_GAP = 26.4             # the caller's reading of the original B→C spacing; refused below as a gap


def refuse_change(source, state, output, name, changes):
    """Any edit that must refuse and publish neither the PDF nor the sidecar."""
    pdf, side = output / f'{name}.pdf', output / f'{name}.json'
    try:
        edit_shared_flow(source, state, pdf, side, changes)
    except PdfError as exc:
        reason = str(exc)
    else:
        raise AssertionError(f'{name}: unsafe edit succeeded')
    assert not pdf.exists() and not side.exists()
    return reason


@proof_session
def run(output, font):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA and source_sha(font) == FONT_SHA
    refusals = {}
    # 1. Own only A on page 8, edit and save (the PR #72 history).
    first = page_proposal(SOURCE, 8, range(24, 28), output, font)
    limited = accept_page_flow_report(SOURCE, first, provider_choices=provider(first))
    state0 = limited['state']
    saved, side = output / 'saved.pdf', output / 'saved.json'
    edit_shared_flow(SOURCE, state0, saved, side, replace_in_flow(state0, FIND, INITIAL, paragraph_id='P1', style_id=STYLE))
    opened = open_shared_flow(saved, side); assert opened['status'] == 'restored'; base = opened['state']
    # 2. First extension (next page), then A grows onto page 9 and B moves down; saved and reopened.
    state1 = first_extension(output, font, saved, base, refusals)['state']
    grown, grown_side = output / 'grown.pdf', output / 'grown.json'
    edit_shared_flow(saved, state1, grown, grown_side, replace_in_flow(state1, INITIAL, SHORT, paragraph_id='P1', style_id=STYLE))
    opened = open_shared_flow(grown, grown_side); assert opened['status'] == 'restored'; grown_state = opened['state']
    generated = slot_id(grown_state['continuation_destinations']['A-next'])
    b_last = grown_state['slots']['slot-1']['occupancy']['last_baseline']
    print('first extension grow restored; B last baseline', b_last, flush=True)
    refusals['before_second_extension'] = refuse(grown, grown_state, output, 'blocked-1', SHORT, LONG)

    # 3. Second extension with an explicitly approved placement policy for C.
    witness = {}

    def approve(page):
        c_now = page['paragraphs'][0]['lines'][0]
        witness.update(first_line=c_now['text'], first_baseline=c_now['baseline'], last_owned_baseline=b_last,
                       gap=c_now['baseline'] - b_last)
        # Policy: C never rises above its current page-9 baseline, and keeps at least the current gap when B
        # pushes it. A policy using the original 26.4 pt gap would move C in the no-op; it is refused below.
        incoming = dict(before='B', after='C', minimum_baseline_gap=witness['gap'],
                        baseline_floor=c_now['baseline'], placement=FOLLOWER_PLACEMENT)
        contradictory = dict(region_id='page-9', incoming=dict(incoming, minimum_baseline_gap=ORIGINAL_GAP))
        try:
            propose_owned_flow_extension(grown, grown_state, [page], paragraphs=[dict(id=chr(67 + i),
                parts=[dict(page=9, paragraph_id=p['id'])]) for i, p in enumerate(page['paragraphs'])],
                relationship=FOLLOWERS_RELATIONSHIP, append_to_region=contradictory)
        except PdfError as exc:
            refusals['original_gap_policy_moves_noop'] = str(exc)
        else:
            raise AssertionError('a policy that moves C in the extension no-op was proposed')
        return dict(region_id='page-9', incoming=incoming)
    accepted2, second_lines = second_extension(output, font, grown, grown_state, refusals, approve)
    state = accepted2['state']; assert open_shared_flow(grown, state)['status'] == 'restored'
    review = accepted2['receipt']['extension']['incoming']
    assert review['extension_noop_baseline'] == review['current']['first_follower_baseline']
    # Audit only (never passed to an editing API): C has not moved since the original PDF.
    original_c = [l['baseline'] for l in _Analysis(SOURCE, 9).lines if l['text'] == witness['first_line']]
    for pid in ('P1', 'B'): assert state['paragraphs'][pid] == grown_state['paragraphs'][pid]
    for sid, old in grown_state['slots'].items():
        assert state['slots'][sid]['binding']['paragraph'] == old['binding']['paragraph']
        assert state['slots'][sid].get('source_output') == old.get('source_output')
        assert state['slots'][sid].get('creation_binding') == old.get('creation_binding')
    for key in ('continuation_destinations', 'destination_bindings', 'generated_fonts'):
        assert state[key] == grown_state[key]
    release_owners = sorted(state['continuation_destinations']['A-next']['vacated_space']['owners'])
    assert release_owners == ['slot-1']
    edge = next(e for e in state['follows'] if e['after'] == 'C')
    assert edge['baseline_floor'] == dict(region_id='page-9', baseline=witness['first_baseline'])
    c = next(s for s in state['slots'] if state['slots'][s]['paragraph_id'] == 'C')
    initial = deepcopy(state)
    reference = deepcopy(initial)
    reference['slots']['slot-0']['binding']['paragraph']['text'] = ''.join(
        l['text'] for l in _Analysis(SOURCE, 8).lines if l['id'] in [f'p8-l{i}' for i in range(24, 28)])
    del reference['slots'][generated]
    result = dict(verdict='NOT VALIDATED', source=dict(url=URL, sha256=SHA), grown_revision=grown_state['pdf_sha256'],
        refusals=refusals, second_acceptance=accepted2['receipt'], witness=witness, original_c_baselines=original_c,
        approved_follow=edge, release_owners=release_owners, second_selection_lines=second_lines,
        pr72_shortened_c=PR72_SHORTENED_C, stages=[])
    shaped = ShapedFont(font); source = grown
    expected = {p: s['logical']['text'] for p, s in state['paragraphs'].items()}
    stages = [('extension-noop', {}), ('shorten', {'P1': (SHORT, FIND)}), ('regrow', {'P1': (FIND, LONG)}),
              ('second', {'B': SECOND}), ('noop', {})]
    creation = grown_state['slots'][generated]['creation_binding']
    noop_first = {}
    for i, (name, edits) in enumerate(stages):
        changes = {}
        for pid, edit in edits.items():
            changes.update(replace_in_flow(state, *edit, paragraph_id=pid, style_id=STYLE))
            expected[pid] = expected[pid].replace(*edit)
        pdf, model = output / f'{i}-{name}.pdf', output / f'{i}-{name}.json'
        report = edit_shared_flow(source, state, pdf, model, changes); write(output / f'{i}-report.json', report)
        opened = open_shared_flow(pdf, model); assert opened['status'] == 'restored'; state = opened['state']
        for sid, slot in initial['slots'].items():
            if sid != generated:
                assert immutable(slot['source_output']) == immutable(state['slots'][sid]['source_output'])
        assert state['slots'][generated]['creation_binding'] == creation
        assert state['continuation_destinations'] == initial['continuation_destinations']
        assert state['follows'] == initial['follows']
        assert {p: s['logical']['text'] for p, s in state['paragraphs'].items()} == expected
        checked = audit(source, pdf, reference, state, report, shaped)
        if not edits:
            pixel_equal(source, pdf); checked['whole_document_mupdf_pixel_identical'] = True
        fragments = report['plan']['fragments']
        firsts = {state['slots'][s]['paragraph_id']: f['lines'][0]['baseline']
                  for s, f in fragments.items() if f['lines'] and state['slots'][s].get('destination_id') is None}
        if i == 0:
            assert state['generated_fonts'] == initial['generated_fonts']
            checked['identity'] = audit_original_noop(grown, pdf, initial, state)
            poppler(output, grown, pdf, state, whole=True); checked['whole_document_poppler_pixel_identical'] = True
            assert firsts['C'] == witness['first_baseline']; noop_first = firsts
        if name == 'shorten':
            assert state['slots'][generated]['occupancy'] is None and firsts['B'] == 142.0
            # C stays at the approved floor; D–H keep their no-op positions.
            assert firsts['C'] == edge['baseline_floor']['baseline']
            assert all(firsts[p] == noop_first[p] for p in 'CDEFGH')
        if name in ('regrow', 'second'):
            b_end = fragments['slot-1']['lines'][-1]['baseline']
            assert abs(firsts['C'] - (b_end + edge['minimum_baseline_gap'])) <= .002 and firsts['C'] > noop_first['C']
        result['stages'].append(dict(name=name, reopen='restored', audit=checked, first_baselines=firsts,
            b_last=fragments['slot-1']['lines'][-1]['baseline'],
            occupancy={s: v['occupancy'] for s, v in state['slots'].items()}))
        write(output / 'progress.json', result)
        print(name, 'restored', firsts, flush=True)
        source = pdf
    # Unsafe edit after the lifecycle: H, the last paragraph, cannot grow beyond the confirmed capacity.
    h = state['paragraphs']['H']['logical']['text']
    refusals['capacity_after_lifecycle'] = refuse_change(source, state, output, 'capacity',
        replace_in_flow(state, h[-4:], h[-4:] + LONG * 4, paragraph_id='H', style_id=STYLE))
    poppler(output, SOURCE, source, state)
    shaped.font.close(); assert source_sha(SOURCE) == SHA
    result.update(verdict='REAL-WORLD FOLLOWER SPACING POLICY VALIDATED — PASS',
                  outside_poppler_changed_pixels=0, original_sha_unchanged=True)
    write(output / 'summary.json', result); print(result['verdict'], flush=True)


if __name__ == '__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--font', type=Path, required=True)
    args = p.parse_args(); run(args.output, args.font)
