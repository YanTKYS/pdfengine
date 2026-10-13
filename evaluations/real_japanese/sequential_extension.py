"""Two sequential ownership extensions on one edited history; never re-accept from the original.

Page 8 owns only the innovator paragraph A. After a saved edit, the PR #71 path adopts the next-page
paragraph B with a release-only continuation; A grows onto page 9 and B moves. From that saved,
restored revision the PR #70 path then appends C–H to the same page-9 region, and the growth that C
blocked succeeds. Every step starts from the previous saved revision.
"""
import argparse
from copy import deepcopy
import logging
from pathlib import Path

import pymupdf

from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import _Analysis, accept_page_flow_report, propose_page_flow, replace_in_flow
from pdfeditor.owned_flow_proposal import propose_owned_flow_extension, accept_owned_flow_report
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.continuation import confirm_continuation_destination, inspect_continuation_boundaries, slot_id
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from pdfeditor.source_ownership import immutable
from pdfeditor.shaped_font import ShapedFont
from .extended_joint_edit import FIND, LONG, SECOND, RELATIONSHIP as FOLLOWERS_RELATIONSHIP
from .owned_body_edit import SOURCE, URL, SHA, audit
from .continuation_edit import FONT_SHA, write, chars
from .vacated_space_edit import audit_original_noop
from .post_edit_extension import SHORT, pixel_equal, poppler
from .next_page_extension import INITIAL, REGION, RELATIONSHIP, BREAK, provider, page_proposal

STYLE = 'style-1'


def refuse(source, state, output, name, old, new):
    """An edit that must refuse and publish neither the PDF nor the sidecar."""
    pdf, side = output / f'{name}.pdf', output / f'{name}.json'
    try:
        edit_shared_flow(source, state, pdf, side, replace_in_flow(state, old, new, paragraph_id='P1', style_id=STYLE))
    except PdfError as exc:
        reason = str(exc)
    else:
        raise AssertionError(f'{name}: growth beyond the owned capacity succeeded')
    assert not pdf.exists() and not side.exists()
    return reason


def first_extension(output, font, saved, base, refusals):
    """PR #71 path on the saved page-8-only edit: adopt B with a release-only continuation."""
    page = page_proposal(saved, 9, range(1, 4), output, font)
    boundary = next(c for c in inspect_continuation_boundaries(saved, 9)['candidates'] if c['ordinal'] == 0)
    confirmation = dict(destination_id='A-next', paragraph_id='P1', region_id='release-9', page=9,
        bounds=REGION['bounds'], insertion='confirmed-page-program-boundary',
        graphics_state='confirmed-boundary-state', boundary=boundary['boundary_id'])
    try: confirm_continuation_destination(saved, **confirmation)
    except PdfError as exc: refusals['first_occupied_region_as_empty'] = str(exc)
    else: raise AssertionError('occupied B text was authorized as empty space')
    request = dict(region_id='page-9', page_break=dict(BREAK, before='P1'),
        vacated_continuation=dict(region_id='release-9', region=REGION, release_paragraphs=['B'], confirmation=confirmation))
    proposal = propose_owned_flow_extension(saved, base, [page], paragraphs=[dict(id='B', parts=[dict(page=9, paragraph_id='P1')])],
        relationship=RELATIONSHIP, next_page=request)
    write(output / 'first-proposal.json', proposal)
    try: accept_owned_flow_report(SOURCE, proposal)
    except PdfError as exc: refusals['first_stale_revision'] = str(exc)
    else: raise AssertionError('first proposal accepted on another revision')
    accepted = accept_owned_flow_report(saved, proposal, provider_choices={'9': provider(page)})
    write(output / 'first-accepted.json', accepted)
    return accepted


def second_extension(output, font, grown, state1, refusals, append='page-9'):
    """PR #70 path on the saved grown revision: append C–H to the same page-9 region.

    `append` is the append_to_region request, or a callable building it from the reviewed page proposal.
    """
    lines = _Analysis(grown, 9).lines
    b_last = state1['slots']['slot-1']['occupancy']['last_baseline']
    ids = [l['id'] for l in lines if b_last + 10 < l['baseline'] < 650]
    kw = dict(line_ids=ids, font_candidates=[font], include_embedded_fonts=True, font_cache=output / 'fonts')
    observed = propose_page_flow(grown, 9, **kw)
    page = propose_page_flow(grown, 9, **kw, new_text={s['id']: LONG + SECOND[1] for s in observed['styles']})
    groups = [dict(id=chr(67 + i), parts=[dict(page=9, paragraph_id=p['id'])]) for i, p in enumerate(page['paragraphs'])]
    proposal = propose_owned_flow_extension(grown, state1, [page], paragraphs=groups,
        relationship=FOLLOWERS_RELATIONSHIP, append_to_region=append(page) if callable(append) else append)
    write(output / 'second-proposal.json', proposal)
    for label, stale in (('second_stale_revision_original', SOURCE), ('second_stale_revision_first_saved', output / 'saved.pdf')):
        try: accept_owned_flow_report(stale, proposal)
        except PdfError as exc: refusals[label] = str(exc)
        else: raise AssertionError('second proposal accepted on another revision')
    accepted = accept_owned_flow_report(grown, proposal, provider_choices={'9': provider(page)})
    write(output / 'second-accepted.json', accepted)
    return accepted, ids


@proof_session
def run(output, font):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA and source_sha(font) == FONT_SHA
    refusals = {}
    # 1. Own only A on page 8, edit and save.
    first = page_proposal(SOURCE, 8, range(24, 28), output, font)
    limited = accept_page_flow_report(SOURCE, first, provider_choices=provider(first))
    write(output / 'limited-acceptance.json', limited)
    state0 = limited['state']
    saved, side = output / 'saved.pdf', output / 'saved.json'
    edit_shared_flow(SOURCE, state0, saved, side, replace_in_flow(state0, FIND, INITIAL, paragraph_id='P1', style_id=STYLE))
    opened = open_shared_flow(saved, side); assert opened['status'] == 'restored'; base = opened['state']
    assert base['flow']['regions'] == ['R1']
    refusals['before_first_extension'] = refuse(saved, base, output, 'blocked-0', INITIAL, SHORT)
    print('before first extension REFUSE', refusals['before_first_extension'], flush=True)
    # 2. First extension (next page) and 3. growth onto page 9, saved and 4. reopened.
    accepted1 = first_extension(output, font, saved, base, refusals)
    state1 = accepted1['state']
    assert state1['flow']['regions'] == ['R1', 'release-9', 'page-9']
    grown, grown_side = output / 'grown.pdf', output / 'grown.json'
    report = edit_shared_flow(saved, state1, grown, grown_side,
                              replace_in_flow(state1, INITIAL, SHORT, paragraph_id='P1', style_id=STYLE))
    write(output / 'grown-report.json', report)
    opened = open_shared_flow(grown, grown_side); assert opened['status'] == 'restored'; grown_state = opened['state']
    generated = slot_id(grown_state['continuation_destinations']['A-next'])
    assert grown_state['slots'][generated]['occupancy']['baseline'] == REGION['first_baseline']
    assert grown_state['slots']['slot-1']['occupancy']['baseline'] > state1['slots']['slot-1']['occupancy']['baseline']
    first_witness = next(s for s in report['steps'] if s['slot_id'] == generated)['report']['vacated_space']
    assert [o['source_owner'] for o in first_witness['owners']] == ['slot-1']
    print('first extension grow restored', flush=True)
    # 5. The next growth is blocked by the unowned C below B.
    refusals['before_second_extension'] = refuse(grown, grown_state, output, 'blocked-1', SHORT, LONG)
    print('before second extension REFUSE', refusals['before_second_extension'], flush=True)
    # 6. Second extension (same page) from the grown revision; 7. identities are kept.
    accepted2, second_lines = second_extension(output, font, grown, grown_state, refusals)
    state = accepted2['state']; assert open_shared_flow(grown, state)['status'] == 'restored'
    for pid in ('P1', 'B'): assert state['paragraphs'][pid] == grown_state['paragraphs'][pid]
    for sid, old in grown_state['slots'].items():
        assert state['slots'][sid]['binding']['paragraph'] == old['binding']['paragraph']
        assert state['slots'][sid].get('source_output') == old.get('source_output')
        assert state['slots'][sid].get('creation_binding') == old.get('creation_binding')
    for key in ('continuation_destinations', 'destination_bindings', 'generated_fonts'):
        assert state[key] == grown_state[key]
    # The release authority stays exactly B: new owners never join an existing release set.
    release_owners = sorted(state['continuation_destinations']['A-next']['vacated_space']['owners'])
    assert release_owners == ['slot-1']
    assert state['source_adoptions']['slot-1'] == grown_state['source_adoptions']['slot-1']
    additions = {sid: dict(paragraph=s['paragraph_id'], source_output=s['source_output'],
                           selection=s['binding']['paragraph']['selection'], adoption=state['source_adoptions'][sid])
                 for sid, s in state['slots'].items() if sid not in grown_state['slots']}
    assert additions and all(a['selection']['source_sha256'] == grown_state['pdf_sha256']
                             and a['adoption']['pdf_sha256'] == grown_state['pdf_sha256'] for a in additions.values())
    initial = deepcopy(state)
    # Independent original-text reference for the audit only; never passed to an editing API.
    reference = deepcopy(initial)
    reference['slots']['slot-0']['binding']['paragraph']['text'] = ''.join(
        l['text'] for l in _Analysis(SOURCE, 8).lines if l['id'] in [f'p8-l{i}' for i in range(24, 28)])
    del reference['slots'][generated]
    result = dict(verdict='NOT VALIDATED', source=dict(url=URL, sha256=SHA), first_saved_revision=base['pdf_sha256'],
        grown_revision=grown_state['pdf_sha256'], initial_replacement=INITIAL, first_growth=SHORT, second_growth=LONG,
        refusals=refusals, first_acceptance=accepted1['receipt'], second_acceptance=accepted2['receipt'],
        first_release_witness=first_witness, generated_slot=generated, release_owners=release_owners,
        second_selection_lines=second_lines, additional_current_owners=additions, stages=[])
    shaped = ShapedFont(font); source = grown
    expected = {p: s['logical']['text'] for p, s in state['paragraphs'].items()}
    stages = [('extension-noop', {}), ('grow', {'P1': (SHORT, LONG)}), ('second', {'B': SECOND}),
              ('shorten', {'P1': (LONG, FIND), 'B': SECOND[::-1]}), ('regrow', {'P1': (FIND, LONG)}), ('noop', {})]
    creation = grown_state['slots'][generated]['creation_binding']
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
        assert {p: s['logical']['text'] for p, s in state['paragraphs'].items()} == expected
        checked = audit(source, pdf, reference, state, report, shaped)
        if not edits:
            pixel_equal(source, pdf); checked['whole_document_mupdf_pixel_identical'] = True
        if i == 0:
            assert state['generated_fonts'] == initial['generated_fonts']
            checked['identity'] = audit_original_noop(grown, pdf, initial, state)
            poppler(output, grown, pdf, state, whole=True); checked['whole_document_poppler_pixel_identical'] = True
        fragments = report['plan']['fragments']
        c = next(s for s in state['slots'] if state['slots'][s]['paragraph_id'] == 'C')
        if name in ('grow', 'regrow'):
            assert len(fragments[generated]['lines']) == 2
            assert fragments[c]['lines'][0]['baseline'] > initial['slots'][c]['occupancy']['baseline']
            # B's planned ink crosses the capacity bound that refused before the second extension.
            assert max(b[3] for b in fragments['slot-1']['ink_bounds']) > grown_state['regions']['page-9']['bounds'][3]
        if name == 'grow':
            with pymupdf.open(grown) as old, pymupdf.open(pdf) as new:
                a, b = chars(old[8]), chars(new[8])
                previous_c = initial['slots'][c]['binding']['paragraph']['selection']['glyph_ids']
                current_b = state['slots']['slot-1']['binding']['paragraph']['selection']['glyph_ids']
                used = [g for g in previous_c if any(pymupdf.Rect(a[g][3]).intersects(pymupdf.Rect(b[j][3])) for j in current_b)]
            assert used
            checked['newly_owned_C_glyph_bounds_used_by_B'] = used
            witness = next(s for s in report['steps'] if s['slot_id'] == generated)['report'].get('vacated_space')
            checked['release_transaction_witness'] = witness
        if name == 'shorten': assert state['slots'][generated]['occupancy'] is None
        else: assert state['slots'][generated]['occupancy'] is not None
        result['stages'].append(dict(name=name, reopen='restored', audit=checked, follows=state['follows'],
            occupancy={s: v['occupancy'] for s, v in state['slots'].items()},
            ink_bottom_B=max(b[3] for b in fragments['slot-1']['ink_bounds'])))
        write(output / 'progress.json', result)
        print(name, 'restored', [(p['paragraph'], p['page'], len(p['lines'])) for p in checked['placements']], flush=True)
        source = pdf
    poppler(output, SOURCE, source, state)
    shaped.font.close(); assert source_sha(SOURCE) == SHA
    result.update(verdict='REAL-WORLD SEQUENTIAL OWNERSHIP EXTENSION VALIDATED — PASS',
                  outside_poppler_changed_pixels=0, original_sha_unchanged=True)
    write(output / 'summary.json', result); print(result['verdict'], flush=True)


if __name__ == '__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--font', type=Path, required=True)
    args = p.parse_args(); run(args.output, args.font)
