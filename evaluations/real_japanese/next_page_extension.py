"""Extend a saved page-8-only edited flow onto existing page-9 owners; never replay from the original."""
import argparse
from copy import deepcopy
import logging
from pathlib import Path

from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import _Analysis, propose_page_flow, accept_page_flow_report, replace_in_flow
from pdfeditor.owned_flow_proposal import propose_owned_flow_extension, accept_owned_flow_report
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.continuation import confirm_continuation_destination, inspect_continuation_boundaries, slot_id
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from pdfeditor.source_ownership import immutable
from pdfeditor.shaped_font import ShapedFont
from .extended_joint_edit import FIND, LONG, SECOND
from .owned_body_edit import SOURCE, URL, SHA, audit
from .continuation_edit import FONT_SHA, write
from .vacated_space_edit import audit_original_noop
from .post_edit_extension import SHORT, pixel_equal, poppler

# A small first edit still fits A's last page-8 line; SHORT needs a fifth line.
INITIAL = FIND + '試行の結果を共有します。'
REGION = dict(page=9, bounds=[56.79999923706055, 110, 540, 174], x=56.79999923706055,
              width=483.20000076293945, first_baseline=122)
RELATIONSHIP = ('Page 8 ends the independent innovator paragraph with 望むタイプでもあります。 '
    'Page 9 starts the next adopter-category paragraph アーリーアダプターは within the same '
    'communication-strategy section. Only that complete paragraph is adopted; C-H, the page-10 section, '
    'headings, logos and drawings stay foreign.')
# Explicit same-page gap chosen by the caller (PR #68 policy); page-8/page-9 y values are not subtracted.
BREAK = dict(before='A', after='B', minimum_baseline_gap=26.4, placement='source-region-floor-and-same-page-follow')


def provider(page):
    return {s['id']: dict(sha256=FONT_SHA, font_index=0, relation='substituted') for s in page['styles']}


def page_proposal(pdf, number, ids, output, font):
    kw = dict(line_ids=[f'p{number}-l{i}' for i in ids], font_candidates=[font],
              include_embedded_fonts=True, font_cache=output / 'fonts')
    observed = propose_page_flow(pdf, number, **kw)
    return propose_page_flow(pdf, number, **kw, new_text={s['id']: LONG + SECOND[1] for s in observed['styles']})


@proof_session
def run(output, font):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA and source_sha(font) == FONT_SHA
    first = page_proposal(SOURCE, 8, range(24, 28), output, font)
    limited = accept_page_flow_report(SOURCE, first, provider_choices=provider(first))
    write(output / 'limited-acceptance.json', limited)
    state0 = limited['state']  # owns only the complete page-8 innovator paragraph A
    saved, side = output / 'saved.pdf', output / 'saved.json'
    report = edit_shared_flow(SOURCE, state0, saved, side,
        replace_in_flow(state0, FIND, INITIAL, paragraph_id='P1', style_id='style-1'))
    write(output / 'bootstrap.json', dict(initial=INITIAL, report=report))
    opened = open_shared_flow(saved, side); assert opened['status'] == 'restored'; base = opened['state']
    assert base['flow']['regions'] == ['R1'] and base['regions']['R1']['page'] == 8
    refusals = {}
    blocked, bside = output / 'blocked.pdf', output / 'blocked.json'
    try: edit_shared_flow(saved, base, blocked, bside, replace_in_flow(base, INITIAL, SHORT, paragraph_id='P1', style_id='style-1'))
    except PdfError as exc: refusals['before_extension'] = str(exc)
    else: raise AssertionError('page-8-only edited state accepted cross-page growth')
    assert not blocked.exists() and not bside.exists()
    print('edited capacity REFUSE', refusals['before_extension'], flush=True)
    page = page_proposal(saved, 9, range(1, 4), output, font)
    boundary = next(c for c in inspect_continuation_boundaries(saved, 9)['candidates'] if c['ordinal'] == 0)
    confirmation = dict(destination_id='A-next', paragraph_id='P1', region_id='release-9', page=9,
        bounds=REGION['bounds'], insertion='confirmed-page-program-boundary',
        graphics_state='confirmed-boundary-state', boundary=boundary['boundary_id'])
    try: confirm_continuation_destination(saved, **confirmation)
    except PdfError as exc: refusals['occupied_region_as_empty'] = str(exc)
    else: raise AssertionError('occupied B text was authorized as empty space')
    request = dict(region_id='page-9', page_break=dict(BREAK, before='P1'),
        vacated_continuation=dict(region_id='release-9', region=REGION, release_paragraphs=['B'], confirmation=confirmation))
    proposal = propose_owned_flow_extension(saved, base, [page], paragraphs=[dict(id='B', parts=[dict(page=9, paragraph_id='P1')])],
        relationship=RELATIONSHIP, next_page=request)
    write(output / 'proposal.json', proposal)
    try: accept_owned_flow_report(SOURCE, proposal)
    except PdfError as exc: refusals['stale_revision'] = str(exc)
    else: raise AssertionError('proposal accepted on another revision')
    accepted = accept_owned_flow_report(saved, proposal, provider_choices={'9': provider(page)})
    write(output / 'accepted.json', accepted)
    state = accepted['state']; assert open_shared_flow(saved, state)['status'] == 'restored'
    assert state['paragraphs']['P1'] == base['paragraphs']['P1']
    for sid, old in base['slots'].items():
        assert state['slots'][sid]['binding'] == old['binding'] and state['slots'][sid]['source_output'] == old['source_output']
    assert state['generated_fonts'] == base['generated_fonts']
    generated = slot_id(state['continuation_destinations']['A-next'])
    adopted = {sid: dict(paragraph=s['paragraph_id'], source_output=s['source_output'], selection=s['binding']['paragraph']['selection'])
               for sid, s in state['slots'].items() if sid not in base['slots']}
    assert all(a['selection']['source_sha256'] == base['pdf_sha256'] for a in adopted.values())
    initial = deepcopy(state)
    # Independent original-text reference for the audit only; never passed to an editing API.
    reference = deepcopy(initial)
    reference['slots']['slot-0']['binding']['paragraph']['text'] = ''.join(
        l['text'] for l in _Analysis(SOURCE, 8).lines if l['id'] in [f'p8-l{i}' for i in range(24, 28)])
    result = dict(verdict='NOT VALIDATED', source=dict(url=URL, sha256=SHA), initial_saved_revision=base['pdf_sha256'],
        initial_replacement=INITIAL, cross_page_replacement=SHORT, refusals=refusals, acceptance=accepted['receipt'],
        preserved_existing_slots=list(base['slots']), additional_current_owners=adopted, generated_slot=generated, stages=[])
    shaped = ShapedFont(font); source = saved
    expected = {p: s['logical']['text'] for p, s in state['paragraphs'].items()}
    stages = [('extension-noop', {}), ('grow', {'P1': (INITIAL, SHORT)}), ('second', {'B': SECOND}),
              ('shorten', {'P1': (SHORT, FIND), 'B': SECOND[::-1]}), ('regrow', {'P1': (FIND, SHORT)}), ('noop', {})]
    for i, (name, edits) in enumerate(stages):
        changes = {}
        for pid, edit in edits.items():
            changes.update(replace_in_flow(state, *edit, paragraph_id=pid, style_id='style-1'))
            expected[pid] = expected[pid].replace(*edit)
        pdf, model = output / f'{i}-{name}.pdf', output / f'{i}-{name}.json'
        report = edit_shared_flow(source, state, pdf, model, changes); write(output / f'{i}-report.json', report)
        opened = open_shared_flow(pdf, model); assert opened['status'] == 'restored'; state = opened['state']
        for sid, slot in initial['slots'].items():
            assert immutable(slot['source_output']) == immutable(state['slots'][sid]['source_output'])
        assert state['continuation_destinations'] == initial['continuation_destinations']
        assert {p: s['logical']['text'] for p, s in state['paragraphs'].items()} == expected
        checked = audit(source, pdf, reference, state, report, shaped)
        if not edits:
            pixel_equal(source, pdf); checked['whole_document_mupdf_pixel_identical'] = True
        if i == 0:
            checked['identity'] = audit_original_noop(saved, pdf, initial, state)
            poppler(output, saved, pdf, state, whole=True); checked['whole_document_poppler_pixel_identical'] = True
        fragments = report['plan']['fragments']
        if name in ('grow', 'regrow'):
            assert fragments[generated]['lines'][0]['baseline'] == REGION['first_baseline']
            assert fragments['slot-1']['lines'][0]['baseline'] > initial['slots']['slot-1']['occupancy']['baseline']
        if name == 'grow':
            witness = next(s for s in report['steps'] if s['slot_id'] == generated)['report']['vacated_space']
            assert [o['source_owner'] for o in witness['owners']] == ['slot-1']
            checked['release_transaction_witness'] = witness
            creation = state['slots'][generated]['creation_binding']
            try: edit_shared_flow(pdf, state, output / 'over.pdf', output / 'over.json',
                                  replace_in_flow(state, SHORT, LONG, paragraph_id='P1', style_id='style-1'))
            except PdfError as exc: refusals['capacity_after_extension'] = str(exc)
            else: raise AssertionError('growth beyond the accepted page-9 capacity succeeded')
            assert not (output / 'over.pdf').exists() and not (output / 'over.json').exists()
        if name in ('shorten',): assert state['slots'][generated]['occupancy'] is None
        elif name != 'extension-noop':
            assert state['slots'][generated]['occupancy'] is not None
            assert state['slots'][generated]['creation_binding'] == creation
        result['stages'].append(dict(name=name, reopen='restored', audit=checked, follows=state['follows'],
            occupancy={s: v['occupancy'] for s, v in state['slots'].items()}))
        write(output / 'progress.json', result)
        print(name, 'restored', [(p['paragraph'], p['page'], len(p['lines'])) for p in checked['placements']], flush=True)
        source = pdf
    poppler(output, SOURCE, source, state)
    shaped.font.close(); assert source_sha(SOURCE) == SHA
    result.update(verdict='REAL-WORLD CROSS-PAGE POST-EDIT EXTENSION VALIDATED — PASS',
        outside_poppler_changed_pixels=0, original_sha_unchanged=True)
    write(output / 'summary.json', result); print(result['verdict'], flush=True)


if __name__ == '__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--font', type=Path, required=True)
    args = p.parse_args(); run(args.output, args.font)
