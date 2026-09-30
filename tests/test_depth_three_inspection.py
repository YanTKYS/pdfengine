"""Small inventory checks on dictionaries/operator names; no PDF/evaluation runs."""
from copy import deepcopy

import pytest

from evaluations.continuation.depth_three_inspection import refusal_inventory, summarize_page


def sample():
    names = [b'q', b'q', b'q', b'q', b'f', b'Q', b'Q', b'Q', b'Q']
    depths = [1, 2, 3, 4, 4, 3, 2, 1]
    rows = [dict(ordinal=i, scope=dict(q_depth=d, text_object=False),
                 status='refused' if d == 4 else 'safe',
                 reasons=['nested-graphics-state-save'] if d == 4 else []) for i, d in enumerate(depths)]
    found = dict(page=1, program_sha256='test', operators=len(names),
                 candidates=[r for r in rows if r['status'] == 'safe'],
                 refused=[r for r in rows if r['status'] == 'refused'],
                 refused_boundaries=2, refusal_reasons={'nested-graphics-state-save': 2})
    return found, [([], name) for name in names]


def test_depth_three_and_deeper_are_counted_in_the_correct_partition():
    found, ops = sample()
    report, rows = summarize_page(found, ops)
    assert report['operators'] == 9 and report['boundaries'] == 8
    assert report['max_q_depth'] == 4
    assert report['q_depth_boundary_counts'] == {'0': 0, '1': 2, '2': 2, '3': 2, '4': 2}
    assert report['depth_3'] == dict(boundaries=2, safe=2, refused=0, refusal_reason_counts={})
    assert report['depth_4_plus'] == dict(boundaries=2, safe=0, refused=2,
                                        refusal_reason_counts={'nested-graphics-state-save': 2})
    assert refusal_inventory(rows)['categories']['q_depth_4_plus'] == 2


def test_overlapping_reasons_are_not_added_as_distinct_boundaries():
    rows = [dict(status='refused', scope=dict(q_depth=1, text_object=False),
                 reasons=['pending-path', 'pending-clip']),
            dict(status='refused', scope=dict(q_depth=2, text_object=True),
                 reasons=['inside-text-object', 'text-rendering-mode']),
            dict(status='refused', scope=dict(q_depth=1, text_object=False), reasons=['pending-path'])]
    report = refusal_inventory(rows)
    assert report['refused_boundaries'] == 3
    assert report['reason_counts'] == {'pending-path': 2, 'pending-clip': 1,
                                      'inside-text-object': 1, 'text-rendering-mode': 1}
    assert report['sole_reason_counts'] == {'pending-path': 1}
    assert report['outside_text_object_reason_counts'] == {'pending-path': 2, 'pending-clip': 1}
    assert sum(row['boundaries'] for row in report['reason_combinations']) == 3
    assert report['categories']['extgstate'] == report['categories']['transparency'] == 0


@pytest.mark.parametrize('change', ['count', 'ordinal', 'depth', 'reason', 'partition', 'raw_ops'])
def test_inconsistent_inspection_is_rejected(change):
    found, ops = deepcopy(sample())
    if change == 'count':
        found['operators'] += 1
    elif change == 'ordinal':
        found['candidates'][1]['ordinal'] = found['candidates'][0]['ordinal']
    elif change == 'depth':
        found['candidates'][0]['scope']['q_depth'] = 2
    elif change == 'reason':
        found['refusal_reasons']['nested-graphics-state-save'] += 1
    elif change == 'partition':
        found['candidates'][0]['status'] = 'refused'
    else:
        ops[-1] = ([], b'n')
    with pytest.raises(ValueError):
        summarize_page(found, ops)
