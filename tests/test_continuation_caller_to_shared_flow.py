"""Acceptance: a destination from the caller workflow feeds the shared-flow continuation unchanged.

inspection -> structural review -> geometry review -> the caller's own
boundary choice -> confirmation request -> explicit confirmation ->
confirm_shared_flow -> plan_shared_flow -> edit_shared_flow -> open_shared_flow,
through public APIs only. The destination is not built by the flow() test
helper (which chooses and confirms directly); lifecycle, tampering and
multi-destination cases stay in test_boundary_destination.py.
"""
from copy import deepcopy
import hashlib

import pymupdf

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.continuation import (BOUNDARY, CREATE, confirm_continuation_destination, inspect_continuation_boundaries,
                                    slot_id)
from pdfeditor.continuation_review import (build_continuation_boundary_confirmation_request,
                                           review_continuation_boundaries, review_continuation_geometry)
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow, plan_shared_flow
from test_boundary_destination import (DESTINATION_PAGE, PAINT_A, PAINT_B, REGION, SOURCE_PAGE, _story, build,
                                       first_operator, last_operator, split)
from test_continuation import LONG, change
from test_operator_nesting import assert_saved_revision

GENERATED = 'generated-from-confirmed-continuation-destination'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(review):
    return [row for group in review['groups'] for row in group['candidates']]


def test_caller_confirmed_boundary_destination_carries_an_overflow_through_shared_flow(tmp_path):
    # Page 1: paragraph A ("ABCD"). Page 2: paint A (a fill), a boundary, paint B (text TAIL).
    source = build(tmp_path, SOURCE_PAGE, DESTINATION_PAGE)
    font = tmp_path / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    original = sha(source)

    # 1-3. Inspection, structural review and geometry review of the caller's bounds.
    inspection = inspect_continuation_boundaries(source, 2)
    assert inspection['candidates']
    review = review_continuation_boundaries(source, 2)
    assert review['program_sha256'] == inspection['program_sha256']
    assert sorted(r['boundary_id'] for r in rows(review)) == sorted(c['boundary_id'] for c in inspection['candidates'])
    geometry = review_continuation_geometry(source, 2, REGION['bounds'])
    assert geometry['geometry']['destination_empty'] is True

    # 4. The caller's decision, written here: the page-level boundary right after paint A's fill, before TAIL.
    matches = [r['boundary_id'] for r in rows(geometry) if r['q_depth'] == 0
               and r['operator_context'] == 'f -> boundary -> BT']
    assert len(matches) == 1
    chosen = matches[0]

    # 5-6. Confirmation request, then explicit confirmation with its confirm_kwargs unchanged.
    request = build_continuation_boundary_confirmation_request(geometry, boundary_id=chosen, destination_id='dest-R2',
                                                               paragraph_id='A', region_id='R2')
    destination = confirm_continuation_destination(source, **request['confirm_kwargs'])
    assert destination['authority']['position'] == BOUNDARY and destination['authority']['boundary_id'] == chosen
    assert destination['bounds'] == REGION['bounds']
    assert (destination['destination_id'], destination['paragraph_id'], destination['region_id']) == ('dest-R2', 'A', 'R2')
    assert destination['source_program_sha256'] == request['review_program_sha256']
    confirmed = deepcopy(destination)

    # 7. Shared flow receives the confirmed record as is: authority held, no generated slot yet.
    paragraph = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(4)), explicit_width=150))
    regions = {'R1': dict(page=1, bounds=[18, 40, 173, 78], x=20, width=150, first_baseline=60),
               'R2': dict(REGION, page=2)}
    state = confirm_shared_flow(source, {'A': _story(source, font, paragraph)}, flow_id='flow', paragraph_order=['A'],
        regions=regions, region_order=list(regions), slot_regions={'A': {'original': 'R1'}},
        paragraph_policies={'A': dict(min_line_height=22, first_line_indent=5, keep_together=False,
                                      break_before='auto', break_after='auto',
                                      empty=dict(kind='reserve-line', ascent=10, descent=3))},
        follows=[], protected_regions={}, continuation_destinations={destination['destination_id']: destination})
    sid = slot_id(destination)
    assert destination == confirmed and state['continuation_destinations'] == {'dest-R2': confirmed}
    witness = state['destination_bindings']['dest-R2']
    assert witness['program_sha256'] == destination['source_program_sha256']
    assert witness['boundary']['offset'] == destination['authority']['boundary']['offset']
    assert 'start' not in witness and 'end' not in witness      # no block exists yet
    assert sid not in state['slots'] and list(state['slots']) == ['slot-0']

    # 8. A text that fits: the confirmed destination alone creates nothing.
    fit = plan_shared_flow(source, state, change(state, 'AB'))
    assert fit['new_slots'] == {} and set(fit['fragments']) == {'slot-0'}

    # 9. An overflow: exactly this destination's slot, owned as confirmed, continuing the same logical text.
    changes = change(state, LONG)
    plan = plan_shared_flow(source, state, changes)
    assert set(plan['new_slots']) == {sid}
    new = plan['new_slots'][sid]
    assert (new['paragraph_id'], new['region_id'], new['destination_id'], new['creation_provenance']) == \
        ('A', 'R2', 'dest-R2', GENERATED)
    assert plan['schedule'] == ['slot-0', sid]
    head, tail = plan['fragments']['slot-0'], plan['fragments'][sid]
    assert head['range'][0] == 0 and head['range'][1] == tail['range'][0] and tail['range'][1] == len(LONG)
    assert head['text'] + ' ' + tail['text'] == LONG and '\n' not in plan['paragraphs']['A']['text']
    assert tail['layout']['first_line_indent'] == 0

    # Everything so far was read-only.
    assert sha(source) == original

    # 10. The one mutating stage: a single save, reopened and verified.
    output, model = tmp_path / 'overflow.pdf', tmp_path / 'overflow.json'
    assert not output.exists() and not model.exists()
    report = edit_shared_flow(source, state, output, model, changes)
    assert report['plan'] == plan and report['saves'] == 1 and sha(source) == original
    opened = open_shared_flow(output, model)
    assert opened['status'] == 'restored'
    saved = opened['state']
    assert_saved_revision(source, output, saved)
    assert list(saved['slots']) == ['slot-0', sid]
    slot = saved['slots'][sid]
    assert (slot['paragraph_id'], slot['region_id'], slot['destination_id'], slot['creation_provenance']) == \
        ('A', 'R2', 'dest-R2', GENERATED)
    assert saved['slots']['slot-0']['range'][1] == slot['range'][0] and slot['range'][1] == len(LONG)
    assert saved['paragraphs']['A']['logical']['text'] == LONG
    assert saved['physical_breaks'] == [dict(paragraph_id='A', offset=slot['range'][0], before='slot-0', after=sid,
        kind='page_break', provenance='generated-from-confirmed-shared-flow', logical_break_inserted=False)]

    # The block sits at the caller's boundary, not at the page entry.
    assert saved['continuation_destinations']['dest-R2'] == confirmed
    offset = confirmed['authority']['boundary']['offset']
    mutation = slot['creation_binding']['mutation']
    assert mutation['kind'] == CREATE and mutation['start'] == mutation['end'] == offset
    assert mutation['owner'] == sid
    prefix, block, suffix = split(output, saved, 'dest-R2')
    assert len(prefix) == offset > 0 and prefix == DESTINATION_PAGE[:offset]
    assert last_operator(prefix) == 'f' and first_operator(suffix) == 'BT'
    assert PAINT_A in prefix and PAINT_B in suffix and block
