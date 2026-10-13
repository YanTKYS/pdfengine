"""Sequential ownership extensions on one restored, edited history (never re-accepted from the original)."""
from copy import deepcopy
from pathlib import Path

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.continuation import slot_id
from pdfeditor.owned_flow_proposal import propose_owned_flow_extension, accept_owned_flow_report
from pdfeditor.page_proposal import _Analysis, propose_page_flow, accept_page_flow, replace_in_flow, _seal
from pdfeditor.post_edit_extension import FOLLOWER_PLACEMENT
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import _contract, _reseal, edit_shared_flow, open_shared_flow
from pdfeditor.source_ownership import immutable
from test_cross_page_owned_flow import make_source, ROW
from test_next_page_extension import LONG, PLACEMENT, RELEASE, exterior_equal, propose, request, saved_case

LONGER = LONG + ROW[:24]  # needs a second continuation line: C moves into D
# The caller-confirmed release region ends above D's original first line (top 118.5 pt). The second
# extension adopts D at its current 18 pt gap below the moved C, so after shortening D sits 8.5 pt higher
# than in the original; a region reaching 116 pt would then contain the unreleased D (refused below).
CLEAR = dict(RELEASE, bounds=[40, 50, 206, 105])
D_SOURCE = 126.9  # D's first baseline as written by the fixture; D has never moved before its adoption


def approved(floor, gap=18.0, **change):
    """The caller's explicit placement policy for D: a page-2 floor and a minimum gap after C."""
    edge = dict(before='C', after='D', minimum_baseline_gap=gap, baseline_floor=floor, placement=FOLLOWER_PLACEMENT)
    return dict(region_id='page-2', incoming=dict(edge, **change))


def current_d(page2):
    return page2['paragraphs'][0]['lines'][0]['baseline']


def refused(source, state, root, pid, old, new, style='style-1', match='exceed all'):
    pdf, side = Path(root) / 'refused.pdf', Path(root) / 'refused.json'
    with pytest.raises(PdfError, match=match):
        edit_shared_flow(source, state, pdf, side, replace_in_flow(state, old, new, paragraph_id=pid, style_id=style))
    assert not pdf.exists() and not side.exists()


def save(source, state, root, name, changes):
    pdf, side = Path(root) / f'{name}.pdf', Path(root) / f'{name}.json'
    report = edit_shared_flow(source, state, pdf, side, changes)
    opened = open_shared_flow(pdf, side); assert opened['status'] == 'restored', opened
    return pdf, opened['state'], report


def pixel_identical(a, b):
    with pymupdf.open(a) as x, pymupdf.open(b) as y:
        return [p.get_pixmap().samples for p in x] == [p.get_pixmap().samples for p in y]


def kept(before, after, skip=()):
    """Existing owners, markers, generated slots, destinations and fonts are carried unchanged."""
    for sid, slot in before['slots'].items():
        if sid in skip:
            continue
        assert after['slots'][sid]['binding']['paragraph'] == slot['binding']['paragraph']
        assert after['slots'][sid].get('source_output') == slot.get('source_output')
        assert after['slots'][sid].get('creation_binding') == slot.get('creation_binding')
    for key in ('continuation_destinations', 'destination_bindings', 'generated_fonts'):
        for ident, value in before.get(key, {}).items():
            assert after[key][ident] == value, (key, ident)


def grown_case(root, release):
    """Saved A+B edit -> next page adopts C (released for B) -> growth saved; D is still unowned."""
    original, source, base, _, font = saved_case(root)
    page = propose_page_flow(source, 2, font_candidates=[font], line_ids=['p2-l1', 'p2-l2'])
    state1 = accept_owned_flow_report(source, propose(source, base, page, [dict(id='C', parts=[dict(page=2, paragraph_id='P1')])],
                                                      **request(region=release)))['state']
    c = next(s for s in state1['slots'] if state1['slots'][s]['paragraph_id'] == 'C')
    destination = state1['continuation_destinations']['B-next']; generated = slot_id(destination)
    assert set(destination['vacated_space']['owners']) == {c}
    grown, grown_state, report = save(source, state1, root, 'grown',
                                      replace_in_flow(state1, '期限', LONG, paragraph_id='P1', style_id='style-1'))
    fragments = report['plan']['fragments']
    assert fragments[generated]['lines'][0]['baseline'] == 62 and fragments[c]['lines'][0]['baseline'] > 80
    # The next growth is blocked by the still-unowned D below C.
    refused(grown, grown_state, root, 'P1', LONG, LONGER)
    # The second extension observes D on the saved grown revision.
    c_last = grown_state['slots'][c]['occupancy']['last_baseline']
    ids = [l['id'] for l in _Analysis(grown, 2).lines if c_last < l['baseline'] < 200]
    page2 = propose_page_flow(grown, 2, font_candidates=[font], line_ids=ids)
    return original, source, grown, grown_state, state1, c, generated, page2


def propose_d(grown, grown_state, page2, append='page-2'):
    return propose_owned_flow_extension(grown, grown_state, [page2], paragraphs=[dict(id='D', parts=[dict(page=2, paragraph_id='P1')])],
        relationship='D is the instruction that follows C on page 2.', append_to_region=append)


def two_extensions(root, release, append='page-2'):
    """Saved A+B edit -> next page adopts C (released for B) -> growth saved -> same page appends D.

    `append` is the append_to_region request, or a callable building it from D's current B1 observation.
    """
    original, source, grown, grown_state, state1, c, generated, page2 = grown_case(root, release)
    proposal = propose_d(grown, grown_state, page2, append(page2) if callable(append) else append)
    with pytest.raises(PdfError, match='another PDF revision'):
        accept_owned_flow_report(source, proposal)
    accepted = accept_owned_flow_report(grown, proposal)
    return original, source, grown, grown_state, state1, accepted, c, generated


def test_next_page_then_same_page_extension_on_one_edited_history(tmp_path):
    original, source, grown, grown_state, state1, accepted, c, generated = two_extensions(tmp_path, CLEAR)
    sha = source_sha(original); state = accepted['state']
    assert accepted['receipt']['reproduction']['status'] == 'reproduced'
    kept(grown_state, state)
    d = next(s for s in state['slots'] if state['slots'][s]['paragraph_id'] == 'D')
    assert state['source_adoptions'][d]['pdf_sha256'] == source_sha(grown)
    assert state['source_adoptions'][c] == grown_state['source_adoptions'][c]
    # New owners never join the existing continuation's release set.
    assert set(state['continuation_destinations']['B-next']['vacated_space']['owners']) == {c}
    d_floor = state['slots'][d]['occupancy']['baseline']
    noop, state, _ = save(grown, state, tmp_path, 'noop', {})
    assert pixel_identical(grown, noop)
    creation = state['slots'][generated]['creation_binding']; previous = noop
    stages = [('P1', LONG, LONGER), ('P2', '再確認', '必要な再確認'), ('P1', LONGER, '期限'), ('P1', '期限', LONGER), (None,)]
    for index, stage in enumerate(stages):
        changes = {} if stage[0] is None else replace_in_flow(state, *stage[1:], paragraph_id=stage[0], style_id='style-1')
        pdf, new, report = save(previous, state, tmp_path, f'stage-{index}', changes)
        fragments = report['plan']['fragments']
        if index in (0, 3):
            assert len(fragments[generated]['lines']) == 2
            assert fragments[d]['lines'][0]['baseline'] > d_floor  # the newly owned D actually moves
        if index == 2:
            assert new['slots'][generated]['occupancy'] is None
        for sid in (c, d, 'slot-0', 'slot-1'):
            assert immutable(new['slots'][sid]['source_output']) == immutable(state['slots'][sid]['source_output'])
        assert new['slots'][generated]['creation_binding'] == creation
        assert new['continuation_destinations'] == state['continuation_destinations']
        assert exterior_equal(previous, pdf, new)
        if stage[0] is None:
            assert pixel_identical(previous, pdf)
        state, previous = new, pdf
    with pymupdf.open(previous) as doc:
        assert doc[1].get_text().count('FIXED CONTENT') == 1
    assert source_sha(original) == sha


def test_newly_owned_follower_cannot_enter_an_existing_release_region(tmp_path):
    """With the 116 pt release region, shortening would pull D (gap 18 pt) to 118.4 pt, inside the region
    that only C may occupy: the edit refuses and publishes nothing; the release set is not widened."""
    _, _, grown, grown_state, _, accepted, c, generated = two_extensions(tmp_path, RELEASE)
    state = accepted['state']
    assert set(state['continuation_destinations']['B-next']['vacated_space']['owners']) == {c}
    refused(grown, state, tmp_path, 'P1', LONG, '期限', match='collides with an unselected glyph')


def test_approved_follower_floor_keeps_d_out_of_the_release_region(tmp_path):
    """The same 116 pt release region, but the caller approves D's placement: never above its current (original)
    first baseline, and at least 18 pt after C. The no-op moves nothing; shortening keeps D at the floor instead of
    pulling it into the release region; regrowth pushes D at the approved gap. Every stage saves and reopens."""
    def approve(page2):
        assert current_d(page2) == pytest.approx(D_SOURCE, abs=1e-4)
        return approved(current_d(page2))
    original, source, grown, grown_state, _, accepted, c, generated = two_extensions(tmp_path, RELEASE, approve)
    sha = source_sha(original); state = accepted['state']
    assert accepted['receipt']['reproduction']['status'] == 'reproduced'
    incoming = accepted['receipt']['extension']['incoming']
    # The current witness (compressed 18 pt gap) and the approved policy are recorded separately.
    assert incoming['current']['baseline_gap'] == pytest.approx(18.0, abs=1e-4)
    assert incoming['extension_noop_baseline'] == incoming['current']['first_follower_baseline'] == incoming['baseline_floor']
    kept(grown_state, state)
    d = next(s for s in state['slots'] if state['slots'][s]['paragraph_id'] == 'D')
    edge = next(e for e in state['follows'] if e['after'] == 'D')
    floor = edge['baseline_floor']['baseline']
    assert edge['baseline_floor']['region_id'] == 'page-2' and edge['minimum_baseline_gap'] == 18.0
    assert state['source_adoptions'][d]['pdf_sha256'] == source_sha(grown)
    # The approved placement grants no release authority: B-next still releases only C.
    assert set(state['continuation_destinations']['B-next']['vacated_space']['owners']) == {c}
    noop, state, _ = save(grown, state, tmp_path, 'noop', {})
    assert pixel_identical(grown, noop) and state['slots'][d]['occupancy']['baseline'] == floor
    creation = state['slots'][generated]['creation_binding']; previous = noop
    stages = [('P1', LONG, '期限'), ('P1', '期限', LONGER), ('P2', '再確認', '必要な再確認'), ('P1', LONGER, '期限'), (None,)]
    for index, stage in enumerate(stages):
        changes = {} if stage[0] is None else replace_in_flow(state, *stage[1:], paragraph_id=stage[0], style_id='style-1')
        pdf, new, report = save(previous, state, tmp_path, f'stage-{index}', changes)
        fragments = report['plan']['fragments']
        if index in (0, 3):
            # PR #72 rule: D at C's last baseline + 18 = 118.4 pt, inside the release region (refused above).
            assert new['slots'][generated]['occupancy'] is None and fragments[c]['lines'][0]['baseline'] == 80
            assert fragments[d]['lines'][0]['baseline'] == floor
            assert min(box[1] for box in fragments[d]['ink_bounds']) > RELEASE['bounds'][3]
        if index == 1:
            assert len(fragments[generated]['lines']) == 2
            c_last = fragments[c]['lines'][-1]['baseline']
            assert fragments[d]['lines'][0]['baseline'] == pytest.approx(c_last + 18.0, abs=.002)
            assert fragments[d]['lines'][0]['baseline'] > floor
        for sid in (c, d, 'slot-0', 'slot-1'):
            assert immutable(new['slots'][sid]['source_output']) == immutable(state['slots'][sid]['source_output'])
        assert new['slots'][generated]['creation_binding'] == creation
        assert new['continuation_destinations'] == state['continuation_destinations']
        assert new['follows'] == state['follows']
        assert exterior_equal(previous, pdf, new)
        if stage[0] is None:
            assert pixel_identical(previous, pdf)
        state, previous = new, pdf
    # The approved floor never creates capacity: growth beyond the confirmed regions still refuses.
    refused(previous, state, tmp_path, 'D', '確認して', '確認して' + ROW * 12)
    with pymupdf.open(previous) as doc:
        assert doc[1].get_text().count('FIXED CONTENT') == 1
    assert source_sha(original) == sha


def test_low_approved_floor_still_refuses_the_release_region_collision(tmp_path):
    """A floor below D's current position is consistent with the no-op (the 18 pt gap binds there), but it grants
    no paint authority: shortening pulls D to 118.4 pt inside the release region, and the edit refuses."""
    _, _, grown, _, _, accepted, _, _ = two_extensions(tmp_path, RELEASE, lambda page2: approved(110.0))
    noop, state, _ = save(grown, accepted['state'], tmp_path, 'noop', {})
    assert pixel_identical(grown, noop)
    refused(noop, state, tmp_path, 'P1', LONG, '期限', match='collides with an unselected glyph')


def test_follower_placement_policy_refusals(tmp_path):
    original, source, grown, grown_state, state1, c, _, page2 = grown_case(tmp_path, RELEASE)
    first = current_d(page2)

    def refuses(append, match):
        with pytest.raises(PdfError, match=match):
            propose_d(grown, grown_state, page2, append)
    for gap in (float('nan'), float('inf'), -18.0, 0):
        refuses(approved(first, gap), 'positive and finite')
    for floor in (float('nan'), float('-inf'), -1.0):
        refuses(approved(floor), 'positive and finite')
    # Unapproved policies: another placement kind, another edge, or a bare/extended request.
    refuses(approved(first, placement='source-region-floor-and-same-page-follow'), 'explicitly approved')
    refuses(approved(first, before='P2'), 'explicitly approved')
    refuses(dict(region_id='page-2', incoming=dict(approved(first)['incoming'], source_break={})), 'explicitly approved')
    refuses(dict(region_id='page-2'), r'exactly \{region_id, incoming\}')
    # Release ownership cannot ride on an append request.
    refuses(dict(approved(first), release_paragraphs=['D']), r'exactly \{region_id, incoming\}')
    # Policies contradicting the current PDF would move D in the extension no-op.
    refuses(approved(first, 26.5), 'would move it')         # the original 26.5 pt C->D gap pushes D down
    refuses(approved(first + 3), 'would move it')           # a floor below the current D
    refuses(approved(110.0, 10.0), 'would move it')         # raises D
    # Capacity: the floor lies inside the extended region, below its first baseline and above its bottom.
    refuses(approved(70.0), 'outside the extended source region capacity')
    refuses(approved(300.0), 'outside the extended source region capacity')
    # Stale revision: a base state from the earlier revision, or acceptance on another revision.
    with pytest.raises(PdfError, match='requires confirmation'):
        propose_d(grown, state1, page2, approved(first))
    proposal = propose_d(grown, grown_state, page2, approved(first))
    for stale in (original, source):
        with pytest.raises(PdfError, match='another PDF revision'):
            accept_owned_flow_report(stale, proposal)
    # Modified review evidence, or a request that no longer matches its review.
    forged = deepcopy(proposal); forged['edited_extension']['review']['incoming']['baseline_floor'] = first + 10
    with pytest.raises(PdfError, match='review differs'):
        accept_owned_flow_report(grown, _seal(forged))
    forged = deepcopy(proposal); forged['edited_extension']['review']['incoming']['current']['baseline_gap'] = 26.5
    with pytest.raises(PdfError, match='review differs'):
        accept_owned_flow_report(grown, _seal(forged))
    forged = deepcopy(proposal); forged['append_to_region']['incoming']['minimum_baseline_gap'] = 26.5
    with pytest.raises(PdfError, match='would move it'):
        accept_owned_flow_report(grown, _seal(forged))
    state = accept_owned_flow_report(grown, proposal)['state']
    assert set(state['continuation_destinations']['B-next']['vacated_space']['owners']) == {c}
    # A floor is bound to the follower's own source region and never to a page-break edge.
    for edit in (lambda e: e['after'] == 'D' and e['baseline_floor'].update(region_id='release-2'),
                 lambda e: e['after'] == 'C' and e.update(baseline_floor=dict(region_id='page-2', baseline=90.0))):
        tampered = deepcopy(state)
        for edge in tampered['follows']:
            edit(edge)
        tampered['contract_sha256'] = _contract(tampered)
        opened = open_shared_flow(grown, _reseal(tampered))
        assert opened['status'] == 'needs_confirmation', opened


def page_request(page, before, after, destination, *, region_bottom=None):
    region = dict(page=page, bounds=[40, 50, 206, 116], x=40, width=166, first_baseline=62)
    request = dict(region_id=f'page-{page}', page_break=dict(before=before, after=after, minimum_baseline_gap=26.5,
        placement=PLACEMENT), vacated_continuation=dict(region_id=f'release-{page}', region=region,
            release_paragraphs=[after], confirmation=dict(destination_id=destination, paragraph_id=before,
                region_id=f'release-{page}', page=page, bounds=region['bounds'], insertion='before-page-program',
                graphics_state='isolated-pdf-initial-state')))
    if region_bottom is not None:
        request['page_overrides'] = dict(region_bottom=region_bottom)
    return request


def test_three_page_sequential_next_page_extensions(tmp_path):
    original, font = make_source(tmp_path, rows=[
        [(ROW[:30], 270, True), ('申請期限を確認。', 291.6, False)],
        [(ROW[:30], 80, True), ('必要書類を提出してください。', 100.4, False)],
        [(ROW[:30], 80, True), ('書類を確認してください。', 100.4, False)]], fixed_pages={0, 2})
    sha = source_sha(original)
    first = accept_page_flow(original, propose_page_flow(original, 1, font_candidates=[font], line_ids=['p1-l2', 'p1-l3']))
    saved, base, _ = save(original, first, tmp_path, 'saved',
                          replace_in_flow(first, '確認', '再確認', paragraph_id='P1', style_id='style-1'))
    assert base['flow']['regions'] == ['R1']
    # Page 2: adopt B, released for A's continuation; the caller bounds B's region.
    page2 = propose_page_flow(saved, 2, font_candidates=[font], line_ids=['p2-l1', 'p2-l2'])
    state1 = accept_owned_flow_report(saved, propose_owned_flow_extension(saved, base, [page2],
        paragraphs=[dict(id='B', parts=[dict(page=2, paragraph_id='P1')])],
        relationship='B continues the application instructions of A on page 2.',
        next_page=page_request(2, 'P1', 'B', 'A-next', region_bottom=125)))['state']
    b = next(s for s in state1['slots'] if state1['slots'][s]['paragraph_id'] == 'B')
    first_generated = slot_id(state1['continuation_destinations']['A-next'])
    grown, grown_state, report = save(saved, state1, tmp_path, 'grown',
                                      replace_in_flow(state1, '期限', LONG, paragraph_id='P1', style_id='style-1'))
    fragments = report['plan']['fragments']
    assert fragments[first_generated]['lines'][0]['baseline'] == 62 and fragments[b]['lines'][0]['baseline'] == 88.5
    growth = ('必要書類', '必要書類' + ROW[:36])
    refused(grown, grown_state, tmp_path, 'B', *growth)
    # Page 3, from the saved grown revision: adopt C with its own release destination for B.
    page3 = propose_page_flow(grown, 3, font_candidates=[font], line_ids=['p3-l1', 'p3-l2'])
    proposal = propose_owned_flow_extension(grown, grown_state, [page3], paragraphs=[dict(id='C', parts=[dict(page=3, paragraph_id='P1')])],
        relationship='C is the next instruction after B, on page 3.', next_page=page_request(3, 'B', 'C', 'B-next'))
    review = proposal['edited_extension']['review']
    assert review['page_boundary']['previous_page'] == 2 and review['page_break']['source_break'] == dict(
        before_region='page-2', after_region='page-3')
    with pytest.raises(PdfError, match='another PDF revision'):
        accept_owned_flow_report(saved, proposal)
    state = accept_owned_flow_report(grown, proposal)['state']
    kept(grown_state, state)
    assert state['flow']['regions'] == ['R1', 'release-2', 'page-2', 'release-3', 'page-3']
    c = next(s for s in state['slots'] if state['slots'][s]['paragraph_id'] == 'C')
    second_generated = slot_id(state['continuation_destinations']['B-next'])
    # Each continuation releases only its own explicitly adopted owner.
    assert set(state['continuation_destinations']['A-next']['vacated_space']['owners']) == {b}
    assert set(state['continuation_destinations']['B-next']['vacated_space']['owners']) == {c}
    assert state['source_adoptions'][c]['pdf_sha256'] == source_sha(grown)
    noop, state, _ = save(grown, state, tmp_path, 'noop', {})
    assert pixel_identical(grown, noop)
    first_creation = state['slots'][first_generated]['creation_binding']; previous = noop; second_creation = None
    stages = [('B', *growth), ('P1', LONG, LONG.replace('期限', '提出期限')), ('P1', LONG.replace('期限', '提出期限'), '期限'),
              ('P1', '期限', LONG), (None,)]
    for index, stage in enumerate(stages):
        changes = {} if stage[0] is None else replace_in_flow(state, *stage[1:], paragraph_id=stage[0], style_id='style-1')
        pdf, new, report = save(previous, state, tmp_path, f'stage-{index}', changes)
        fragments = report['plan']['fragments']
        if index == 0:
            # B continues on page 3; the uninitialized adopted C is released in the same Transaction.
            assert fragments[second_generated]['lines'][0]['baseline'] == 62
            assert fragments[c]['lines'][0]['baseline'] == 88.5
            witness = next(s for s in report['steps'] if s['slot_id'] == second_generated)['report']['vacated_space']
            assert [o['source_owner'] for o in witness['owners']] == [c]
        if index == 2:
            assert new['slots'][first_generated]['occupancy'] is None
        for sid in ('slot-0', b, c):
            assert immutable(new['slots'][sid]['source_output']) == immutable(state['slots'][sid]['source_output'])
        assert new['slots'][first_generated]['creation_binding'] == first_creation
        if new['slots'][second_generated].get('creation_binding') is not None:
            second_creation = second_creation or new['slots'][second_generated]['creation_binding']
            assert new['slots'][second_generated]['creation_binding'] == second_creation
        assert new['continuation_destinations'] == state['continuation_destinations']
        assert exterior_equal(previous, pdf, new)
        if stage[0] is None:
            assert pixel_identical(previous, pdf)
        state, previous = new, pdf
    with pymupdf.open(previous) as doc:
        assert [doc[n].get_text().count('FIXED CONTENT') for n in (0, 2)] == [1, 1]
    assert source_sha(original) == sha
