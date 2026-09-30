"""Pure grouping tests; no PDFs, geometry, confirmations or renderer calls."""
from copy import deepcopy
from itertools import permutations

import pytest

from evaluations.continuation.boundary_review import group_candidates, group_statistics
from pdfeditor.continuation import SCOPE, SCOPE_CHAIN, SCOPE_THREE, Z_ORDER


def candidate(number, *, page=1, prefix=1, suffix=4, depth=0, clip=False, compensation=False):
    ordinal = 10 + number
    state = dict(clip=[{'rectangle': [0, 0, 100, 100]}] if clip else [],
                 ctm=[1, 0, 0, 1, 20 if compensation else 0, 0], fill=['g', ['0']])
    value = dict(page=page, ordinal=ordinal, boundary_id=f'boundary-{number:024x}', program_sha256='a' * 64,
        status='safe', reasons=[], z_order=dict(semantics=Z_ORDER, prefix_paint_operators=prefix, suffix_paint_operators=suffix),
        previous=dict(operator='cm', ordinal=ordinal), next=dict(operator='BT', ordinal=ordinal+1),
        scope=dict(q_depth=depth, text_object=False, marked_content_depth=0, compatibility_depth=0,
                   pending_path=False, pending_clip=False), graphics_state=state)
    if depth:
        levels = [dict(opening=dict(operator='q', ordinal=2*i), matching=dict(operator='Q', ordinal=90-2*i),
                       restored_state={'fill': ['g', ['0']]}) for i in range(depth)]
        scope = dict(policy=(SCOPE, SCOPE_CHAIN, SCOPE_THREE)[depth-1], depth=depth, contract='fixture contract')
        if depth == 1:
            scope.update(levels[0])
        else:
            scope.update(zip(('outer', 'inner') if depth == 2 else ('outer', 'middle', 'inner'), levels))
        value['graphics_state_scope'] = scope
    if clip:
        value['clip_constraint'] = dict(rectangle=[0, 0, 100, 100])
    if compensation:
        value['ctm_compensation'] = dict(operator='1 0 0 1 -20 0 cm')
    return value


def test_same_paint_position_groups_different_depth_and_preserves_authority_attributes():
    items = [candidate(1), candidate(2, depth=1), candidate(3, depth=2, clip=True, compensation=True)]
    groups = group_candidates(items)
    assert len(groups) == 1
    group = groups[0]
    assert group['candidate_count'] == 3 and (group['ordinal_min'], group['ordinal_max']) == (11, 13)
    assert group['boundary_ids'] == [item['boundary_id'] for item in items]
    rows = group['candidates']
    assert [row['q_depth'] for row in rows] == [0, 1, 2]
    assert [row['scope_policy'] for row in rows] == [None, SCOPE, SCOPE_CHAIN]
    assert [row['has_scope_binding'] for row in rows] == [False, True, True]
    assert rows[2]['has_ctm_compensation'] and rows[2]['has_rectangular_clip_constraint']
    assert rows[2]['scope_operators'] == [dict(level='outer', opening_ordinal=0, matching_ordinal=90),
                                       dict(level='inner', opening_ordinal=2, matching_ordinal=88)]
    assert rows[0]['operator_context'] == 'cm -> boundary -> BT'
    assert len({row['graphics_state_sha256'] for row in rows}) == 2
    assert group['distinct_witness_counts']['scope_sha256'] == 3
    assert group['least_constrained_candidates'] == [items[0]['boundary_id']]


def test_paint_crossing_page_and_semantics_are_separate_groups():
    items = [candidate(1), candidate(2, prefix=2, suffix=3), candidate(3, page=2), candidate(4)]
    items[-1]['z_order']['semantics'] = 'another-explicit-paint-semantics'
    groups = group_candidates(items)
    assert len(groups) == 4 and len({group['group_id'] for group in groups}) == 4
    assert group_statistics(groups)['singleton_groups'] == 4


def test_lexicographic_review_attributes_keep_all_ties_and_are_not_state_equivalence():
    items = [candidate(1, clip=True), candidate(2, depth=1, compensation=True),
             candidate(3, depth=3), candidate(4, depth=2), candidate(5, depth=2)]
    items[-1]['graphics_state']['fill'] = ['rg', ['1', '0', '0']]
    items[-1]['graphics_state_scope']['inner']['restored_state'] = {'fill': ['g', ['1']]}
    groups = group_candidates(items)
    assert groups[0]['least_constrained_candidates'] == [items[3]['boundary_id'], items[4]['boundary_id']]
    assert groups[0]['candidates'][3]['graphics_state_sha256'] != groups[0]['candidates'][4]['graphics_state_sha256']
    assert groups[0]['candidates'][3]['scope_sha256'] != groups[0]['candidates'][4]['scope_sha256']
    stats = group_statistics(groups)
    assert stats['candidates'] == 5 and stats['groups'] == 1
    assert stats['group_size_distribution'] == {'5': 1} and stats['maximum_group_size'] == 5
    assert stats['least_constrained_singleton_groups'] == 0 and stats['least_constrained_tied_groups'] == 1


def test_order_independence_no_input_mutation_and_detached_output():
    items = [candidate(4, depth=1), candidate(2), candidate(3, prefix=2, suffix=3), candidate(1)]
    before = deepcopy(items)
    expected = group_candidates(items)
    for ordering in permutations(items):
        assert group_candidates(ordering) == expected
    assert items == before
    assert {ident for group in expected for ident in group['boundary_ids']} == {row['boundary_id'] for row in before}
    expected[0]['candidates'][0]['scope_operators'].append({'changed': True})
    expected[0]['boundary_ids'].clear()
    assert items == before


def test_empty_input_and_mixed_singleton_statistics():
    assert group_candidates([]) == []
    assert group_statistics([])['maximum_group_size'] == 0
    groups = group_candidates([candidate(1), candidate(2, depth=1), candidate(3, prefix=2, suffix=3)])
    stats = group_statistics(groups)
    assert stats['group_size_distribution'] == {'1': 1, '2': 1}
    assert stats['singleton_groups'] == stats['multiple_candidate_groups'] == 1
    assert stats['least_constrained_singleton_groups'] == 2
    assert stats['multiple_candidate_groups_with_single_least'] == 1


@pytest.mark.parametrize('change', [
    lambda c: c.pop('z_order'), lambda c: c.update(z_order=None),
    lambda c: c['z_order'].pop('semantics'), lambda c: c['z_order'].update(semantics=''),
    lambda c: c['z_order'].update(prefix_paint_operators=True),
    lambda c: c['z_order'].update(suffix_paint_operators=-1),
    lambda c: c['z_order'].update(extra=1), lambda c: c.update(page=True),
    lambda c: c.update(ordinal=1.5), lambda c: c.update(boundary_id=''),
    lambda c: c.update(status='refused'), lambda c: c.update(reasons=['pending-path']),
    lambda c: c['scope'].update(pending_path=True), lambda c: c['scope'].update(q_depth=True),
    lambda c: c['scope'].update(q_depth=4), lambda c: c['next'].update(ordinal=999),
    lambda c: c.update(clip_constraint={}), lambda c: c.update(ctm_compensation=None),
    lambda c: c['graphics_state'].update(clip=[{}]),
    lambda c: c.update(graphics_state_scope={}),
    lambda c: c['graphics_state'].update(ctm=[float('nan')]),
])
def test_malformed_records_fail_closed(change):
    value = candidate(1)
    change(value)
    with pytest.raises(ValueError):
        group_candidates([value])


@pytest.mark.parametrize('kind', ['duplicate', 'ordinal', 'program', 'paint_total', 'scope_form', 'scope_pair'])
def test_ambiguous_or_misleading_inputs_fail_closed(kind):
    a, b = candidate(1, depth=2), candidate(2, depth=2)
    if kind == 'duplicate':
        b['boundary_id'] = a['boundary_id']
    elif kind == 'ordinal':
        b = deepcopy(a)
        b['boundary_id'] = 'another-id'
    elif kind == 'program':
        b['program_sha256'] = 'b' * 64
    elif kind == 'paint_total':
        b['z_order']['suffix_paint_operators'] += 1
    elif kind == 'scope_form':
        b['graphics_state_scope']['middle'] = b['graphics_state_scope']['inner']
    else:
        b['graphics_state_scope']['outer']['matching']['ordinal'] = 3
    with pytest.raises(ValueError):
        group_candidates([a, b])
