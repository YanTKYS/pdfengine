"""Read-only boundary review: exact paint-position groups, revision-aware IDs, no selection."""
from copy import deepcopy
from itertools import permutations

import pytest

from pdfeditor import continuation_review as review
from pdfeditor.backend import PdfError
from pdfeditor.continuation import (CLIP, COMPENSATION, SCOPE, SCOPE_CHAIN, SCOPE_CHAIN_CONTRACT, SCOPE_CONTRACT,
                                    SCOPE_THREE, SCOPE_THREE_CONTRACT, Z_ORDER, confirm_continuation_destination,
                                    inspect_continuation_boundaries)
from pdfeditor.continuation_review import (REVIEW_CONTRACT, group_continuation_boundary_candidates,
                                           review_continuation_boundaries)
from evaluations.continuation.boundary_review import group_candidates, group_statistics
from test_boundary_destination import build
from test_scope_three_boundary import triple

PROGRAM = 'a' * 64
CONTRACTS = ((SCOPE, SCOPE_CONTRACT), (SCOPE_CHAIN, SCOPE_CHAIN_CONTRACT), (SCOPE_THREE, SCOPE_THREE_CONTRACT))


def op(name, ordinal):
    return dict(ordinal=ordinal, operator=name, start=10 * ordinal, end=10 * ordinal + 1, sha256=name * 8)


def candidate(number, *, page=1, prefix=1, suffix=4, depth=0, clip=False, compensation=False, program=PROGRAM):
    """A safe record in the inspector's exact form (scope levels opened at ordinals 0, 2, 4)."""
    ordinal = 10 + number
    ctm = [1, 0, 0, 1, 20.5, 0] if compensation else [1, 0, 0, 1, 0, 0]
    state = dict(clip=[{'rule': 'W', 'path': []}] if clip else [], ctm=ctm, fill=['g', ['0']])
    value = dict(page=page, ordinal=ordinal, boundary_id=f'boundary-{number:024x}', program_sha256=program,
        offset=10 * ordinal + 1, status='safe', reasons=[],
        z_order=dict(semantics=Z_ORDER, prefix_paint_operators=prefix, suffix_paint_operators=suffix),
        previous=op('cm', ordinal), next=op('BT', ordinal + 1),
        scope=dict(q_depth=depth, text_object=False, marked_content_depth=0, compatibility_depth=0,
                   pending_path=False, pending_clip=False), graphics_state=state, context={})
    if depth:
        levels = [dict(opening=op('q', 2 * i), matching=op('Q', 90 - 2 * i), restored_state={'fill': ['g', ['0']]})
                  for i in range(depth)]
        policy, contract = CONTRACTS[depth - 1]
        scope = dict(policy=policy, depth=depth, contract=contract)
        if depth == 1:
            scope.update(levels[0])
        else:
            scope.update(zip(('outer', 'inner') if depth == 2 else ('outer', 'middle', 'inner'), levels))
        value['graphics_state_scope'] = scope
    if clip:
        value['clip_constraint'] = dict(policy=CLIP, rectangle=[0, 0, 100, 100], clips=[{'rule': 'W'}])
    if compensation:
        matrix = ['1', '0', '0', '1', '-20.5', '0']
        value['ctm_compensation'] = dict(policy=COMPENSATION, confirmed_ctm=ctm, matrix=matrix,
                                         operator=' '.join(matrix) + ' cm', proof={})
    return value


def ids(rows):
    return [row['boundary_id'] for row in rows]


# -- grouping -------------------------------------------------------------------

def test_same_paint_position_is_one_group_keeping_every_authority_distinct():
    items = [candidate(1), candidate(2, depth=1), candidate(3, depth=2, clip=True, compensation=True)]
    [group] = group_continuation_boundary_candidates(items)
    assert group['group_id'].startswith('review-group-') and group['page'] == 1
    assert group['program_sha256'] == PROGRAM and group['z_order']['prefix_paint_operators'] == 1
    assert group['candidate_count'] == 3 and (group['ordinal_min'], group['ordinal_max']) == (11, 13)
    assert group['boundary_ids'] == ids(items) == ids(group['candidates'])
    rows = group['candidates']
    assert rows[0]['operator_context'] == 'cm -> boundary -> BT'
    assert (rows[0]['previous_operator'], rows[0]['next_operator']) == ('cm', 'BT')
    assert rows[2]['scope_operators'] == [dict(level='outer', opening_ordinal=0, matching_ordinal=90),
                                          dict(level='inner', opening_ordinal=2, matching_ordinal=88)]
    assert group['distinct_authority_variants'] == dict(graphics_state=2, graphics_state_scope=3,
                                                        ctm_compensation=2, clip_constraint=2)
    assert group['minimal_authority_review_candidates'] == [items[0]['boundary_id']]
    assert group['minimal_authority_review_attributes'] == dict(rectangular_clip=False, ctm_compensation=False, q_depth=0)


def test_paint_crossing_page_and_semantics_separate_groups():
    items = [candidate(1), candidate(2, prefix=2, suffix=3), candidate(3, page=2), candidate(4)]
    items[-1]['z_order']['semantics'] = 'another-explicit-paint-semantics'
    groups = group_continuation_boundary_candidates(items)
    assert [g['candidate_count'] for g in groups] == [1, 1, 1, 1]
    assert len({g['group_id'] for g in groups}) == 4
    assert [(g['page'], g['z_order']['prefix_paint_operators']) for g in groups] == [(1, 1), (1, 2), (1, 1), (2, 1)]


def test_group_id_is_revision_aware_and_deterministic():
    """Same page, paints and candidate structure: only the program differs, so only the group ID does."""
    items = [candidate(1), candidate(2, depth=1), candidate(3, clip=True)]
    [first] = group_continuation_boundary_candidates(items)
    [again] = group_continuation_boundary_candidates(deepcopy(items))
    other = [candidate(1, program='b' * 64), candidate(2, depth=1, program='b' * 64),
             candidate(3, clip=True, program='b' * 64)]
    [second] = group_continuation_boundary_candidates(other)
    assert first == again
    assert first['group_id'] != second['group_id']
    strip = lambda g: {k: v for k, v in g.items() if k not in ('group_id', 'program_sha256')}
    assert strip(first) == strip(second)
    # Nor does a group ID coincide with another page's same paint position.
    [elsewhere] = group_continuation_boundary_candidates([candidate(1, page=2)])
    assert elsewhere['group_id'] != first['group_id']


def test_input_order_independent_and_input_never_mutated_or_shared():
    items = [candidate(4, depth=1), candidate(2), candidate(3, prefix=2, suffix=3), candidate(1, depth=3, clip=True)]
    before = deepcopy(items)
    expected = group_continuation_boundary_candidates(items)
    for ordering in permutations(items):
        assert group_continuation_boundary_candidates(ordering) == expected
    assert items == before
    expected[0]['candidates'][0]['scope_operators'].append({'changed': True})
    expected[0]['candidates'][0]['review_requirements'].append('changed')
    expected[0]['z_order']['semantics'] = 'changed'
    assert items == before and group_continuation_boundary_candidates(items) != expected


def test_empty_input():
    assert group_continuation_boundary_candidates([]) == []


# -- presentation -----------------------------------------------------------------

@pytest.mark.parametrize('depth,policy,levels', [
    (0, None, []), (1, SCOPE, ['scope']), (2, SCOPE_CHAIN, ['outer', 'inner']),
    (3, SCOPE_THREE, ['outer', 'middle', 'inner'])])
def test_depth_presentation(depth, policy, levels):
    [group] = group_continuation_boundary_candidates([candidate(1, depth=depth)])
    [row] = group['candidates']
    assert (row['q_depth'], row['scope_policy'], row['has_scope_binding']) == (depth, policy, depth != 0)
    assert [level['level'] for level in row['scope_operators']] == levels
    assert [(l['opening_ordinal'], l['matching_ordinal']) for l in row['scope_operators']] == \
        [(2 * i, 90 - 2 * i) for i in range(depth)]
    assert row['review_requirements'] == (['scope-binding'] if depth else [])
    assert row['review_attributes'] == dict(rectangular_clip=False, ctm_compensation=False, q_depth=depth)
    assert (row['authority_digests']['graphics_state_scope'] is None) == (depth == 0)


@pytest.mark.parametrize('depth,clip,compensation,requirements', [
    (0, False, False, []),
    (0, True, False, ['rectangular-clip']),
    (0, False, True, ['ctm-compensation']),
    (1, True, False, ['scope-binding', 'rectangular-clip']),
    (2, False, True, ['scope-binding', 'ctm-compensation']),
    (3, True, True, ['scope-binding', 'ctm-compensation', 'rectangular-clip'])])
def test_review_requirements_and_attributes(depth, clip, compensation, requirements):
    [group] = group_continuation_boundary_candidates([candidate(1, depth=depth, clip=clip, compensation=compensation)])
    [row] = group['candidates']
    assert row['review_requirements'] == requirements
    assert (row['has_rectangular_clip_constraint'], row['has_ctm_compensation']) == (clip, compensation)
    assert row['review_attributes'] == dict(rectangular_clip=clip, ctm_compensation=compensation, q_depth=depth)
    digests = row['authority_digests']
    assert ((digests['clip_constraint'] is not None, digests['ctm_compensation'] is not None) == (clip, compensation))
    assert set(row) >= {'boundary_id', 'ordinal', 'previous_operator', 'next_operator', 'operator_context'}
    assert not set(row) & {'graphics_state', 'graphics_state_scope', 'clip_constraint', 'ctm_compensation', 'context'}


def test_minimal_review_set_keeps_every_tie_and_removes_nothing():
    items = [candidate(1, clip=True), candidate(2, depth=1, compensation=True),
             candidate(3, depth=3), candidate(4, depth=2), candidate(5, depth=2)]
    items[-1]['graphics_state']['fill'] = ['rg', ['1', '0', '0']]
    items[-1]['graphics_state_scope']['inner']['restored_state'] = {'fill': ['g', ['1']]}
    [group] = group_continuation_boundary_candidates(items)
    assert group['minimal_authority_review_candidates'] == [items[3]['boundary_id'], items[4]['boundary_id']]
    assert group['minimal_authority_review_attributes'] == dict(rectangular_clip=False, ctm_compensation=False, q_depth=2)
    assert group['boundary_ids'] == ids(items) and group['candidate_count'] == 5
    # Equal review attributes are not equal authority.
    tied = group['candidates'][3:]
    assert tied[0]['authority_digests']['graphics_state'] != tied[1]['authority_digests']['graphics_state']
    assert tied[0]['authority_digests']['graphics_state_scope'] != tied[1]['authority_digests']['graphics_state_scope']
    assert group['distinct_authority_variants']['graphics_state_scope'] == 5
    # Attribute order: no clip first, then no compensation, then depth.
    [group] = group_continuation_boundary_candidates([candidate(1, clip=True), candidate(2, depth=3, compensation=True)])
    assert group['minimal_authority_review_candidates'] == [candidate(2)['boundary_id']]


# -- fail closed --------------------------------------------------------------------

@pytest.mark.parametrize('change', [
    lambda c: c.update(status='refused'), lambda c: c.update(reasons=['pending-path']), lambda c: c.pop('reasons'),
    lambda c: c.pop('z_order'), lambda c: c.update(z_order=None), lambda c: c['z_order'].pop('semantics'),
    lambda c: c['z_order'].update(semantics=''), lambda c: c['z_order'].update(prefix_paint_operators=True),
    lambda c: c['z_order'].update(suffix_paint_operators=-1), lambda c: c['z_order'].update(extra=1),
    lambda c: c.update(page=True), lambda c: c.update(page=0), lambda c: c.update(ordinal=1.5),
    lambda c: c.update(boundary_id=''), lambda c: c.update(boundary_id='review-group-' + '0' * 24),
    lambda c: c.update(program_sha256='not-a-digest'),
    lambda c: c['scope'].update(pending_path=True), lambda c: c['scope'].update(text_object=1),
    lambda c: c['scope'].update(marked_content_depth=1), lambda c: c['scope'].update(q_depth=True),
    lambda c: c['scope'].update(q_depth=4), lambda c: c['next'].update(ordinal=999),
    lambda c: c['previous'].update(operator=''),
    lambda c: c.update(clip_constraint={}), lambda c: c['graphics_state'].update(clip=[{}]),
    lambda c: c.update(ctm_compensation=None), lambda c: c['graphics_state'].update(ctm=[2, 0, 0, 2, 0, 0]),
    lambda c: c.update(graphics_state_scope={}), lambda c: c['graphics_state'].update(ctm=[float('nan')] * 6),
    lambda c: c['context'].update(value=float('inf')),
])
def test_malformed_page_level_records_fail_closed(change):
    value = candidate(1)
    change(value)
    with pytest.raises(PdfError):
        group_continuation_boundary_candidates([value])


@pytest.mark.parametrize('change', [
    lambda c: c.pop('graphics_state_scope'),
    lambda c: c['graphics_state_scope'].update(depth=3),
    lambda c: c['graphics_state_scope'].update(policy=SCOPE),
    lambda c: c['graphics_state_scope'].update(contract='another contract'),
    lambda c: c['graphics_state_scope'].update(middle={}),
    lambda c: c['graphics_state_scope']['inner'].pop('restored_state'),
    lambda c: c['graphics_state_scope']['inner']['opening'].pop('sha256'),
    lambda c: c['graphics_state_scope']['inner'].update(opening=op('Q', 2)),
    lambda c: c['graphics_state_scope']['inner']['matching'].update(ordinal=5),
    lambda c: c['graphics_state_scope']['outer']['matching'].update(ordinal=3),
    lambda c: c['graphics_state_scope']['inner'].update(opening=op('q', 0)),
    lambda c: c['clip_constraint'].update(policy='another-clip'),
    lambda c: c['clip_constraint'].update(rectangle=[0, 0, 0, 100]),
    lambda c: c['clip_constraint'].update(clips=[]),
    lambda c: c.pop('clip_constraint'),
    lambda c: c['ctm_compensation'].update(policy='another'),
    lambda c: c['ctm_compensation'].update(operator='1 0 0 1 0 0 cm'),
    lambda c: c['ctm_compensation'].update(confirmed_ctm=[1, 0, 0, 1, 0, 0]),
    lambda c: c['ctm_compensation'].update(proof=float('nan')),
    lambda c: c.pop('ctm_compensation'),
])
def test_malformed_scope_clip_and_compensation_fail_closed(change):
    value = candidate(1, depth=2, clip=True, compensation=True)
    group_continuation_boundary_candidates([deepcopy(value)])
    change(value)
    with pytest.raises(PdfError):
        group_continuation_boundary_candidates([value])


@pytest.mark.parametrize('kind', ['duplicate', 'ordinal', 'program', 'paint_total'])
def test_ambiguous_inputs_fail_closed(kind):
    a, b = candidate(1, depth=2), candidate(2, depth=2)
    if kind == 'duplicate':
        b['boundary_id'] = a['boundary_id']
    elif kind == 'ordinal':
        b = deepcopy(a)
        b['boundary_id'] = 'boundary-' + 'f' * 24
    elif kind == 'program':
        b['program_sha256'] = 'b' * 64
    else:
        b['z_order']['suffix_paint_operators'] += 1
    with pytest.raises(PdfError):
        group_continuation_boundary_candidates([a, b])


# -- the convenience API on a real inspection ---------------------------------------

def test_review_uses_only_the_inspectors_safe_candidates_and_confirms_nothing(tmp_path):
    source = build(tmp_path, b'0 0 5 5 re f', triple('fractional-clip'))
    inspected = inspect_continuation_boundaries(source, 2, include_refused=True)
    before = deepcopy(inspected)
    found = review_continuation_boundaries(source, 2)
    assert inspect_continuation_boundaries(source, 2, include_refused=True) == before
    assert set(found) == {'schema', 'page', 'program_sha256', 'candidate_count', 'group_count', 'groups', 'contract'}
    assert (found['page'], found['program_sha256']) == (2, inspected['program_sha256'])
    safe = [c['boundary_id'] for c in inspected['candidates']]
    reviewed = [ident for group in found['groups'] for ident in group['boundary_ids']]
    assert sorted(reviewed) == sorted(safe) and found['candidate_count'] == len(safe)
    assert not set(reviewed) & {c['boundary_id'] for c in inspected['refused']} and inspected['refused']
    assert found['group_count'] == len(found['groups']) < len(safe)
    assert found['groups'] == group_continuation_boundary_candidates(inspected['candidates'])
    rows = [row for group in found['groups'] for row in group['candidates']]
    assert {row['q_depth'] for row in rows} == {0, 1, 2, 3}
    assert any(row['has_ctm_compensation'] for row in rows) and any(row['has_rectangular_clip_constraint'] for row in rows)
    contract = found['contract']
    assert contract == REVIEW_CONTRACT and contract is not REVIEW_CONTRACT
    assert contract['automatic_selection'] is contract['safety_ranking'] is contract['recommendation'] is False
    assert contract['automatic_confirmation'] is contract['geometry_used'] is False
    assert contract['group_identity'] == dict(prefix='review-group-', covers=list(review.GROUP_KEY),
        lifetime=contract['group_identity']['lifetime'], boundary_id=False, confirmable=False, persistent=False)
    # A group ID is never a boundary ID: confirmation refuses it.
    group = found['groups'][0]
    with pytest.raises(PdfError, match='not a safe page-level candidate'):
        confirm_continuation_destination(source, destination_id='d', paragraph_id='p', region_id='r', page=2,
            bounds=[1, 1, 2, 2], insertion='confirmed-page-program-boundary',
            graphics_state='confirmed-boundary-state', boundary=group['group_id'])


def test_review_refuses_candidates_of_another_page_or_program():
    inspection = dict(program_sha256=PROGRAM, candidates=[candidate(1)])
    assert review._review(1, inspection)['group_count'] == 1
    with pytest.raises(PdfError):
        review._review(2, inspection)
    with pytest.raises(PdfError):
        review._review(1, dict(inspection, program_sha256='b' * 64))


# -- the historical PR #22 format as a wrapper ---------------------------------------

def test_historical_wrapper_renames_the_formal_groups():
    items = [candidate(1, clip=True), candidate(2, depth=1, compensation=True), candidate(3, depth=2),
             candidate(4, prefix=2, suffix=3)]
    formal = group_continuation_boundary_candidates(items)
    historical = group_candidates(items)
    assert [g['boundary_ids'] for g in historical] == [g['boundary_ids'] for g in formal]
    assert [g['least_constrained_candidates'] for g in historical] == \
        [g['minimal_authority_review_candidates'] for g in formal]
    assert historical[0]['group_id'].startswith('paint-group-')
    assert historical[0]['distinct_witness_counts'] == dict(graphics_state_sha256=3, scope_sha256=3,
        compensation_sha256=2, clip_constraint_sha256=2)
    row = historical[0]['candidates'][0]
    assert row['page_level'] and row['inherited_clip'] and row['clip_constraint_sha256'] is not None
    stats = group_statistics(historical)
    assert (stats['candidates'], stats['groups'], stats['singleton_groups']) == (4, 2, 1)
    assert stats['least_constrained_candidates'] == 2
