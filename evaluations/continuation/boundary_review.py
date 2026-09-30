"""Historical PR #22 grouping format, now a thin wrapper over the formal API.

The grouping, validation and review attributes are those of
pdfeditor.continuation_review; this module only renames them into the
PR #22 prototype's output (``paint-group-...`` IDs from the paint-position key
alone, ``least_constrained_candidates``, ``distinct_witness_counts``) so the
published PR #22 evidence can be reproduced. New callers use
review_continuation_boundaries / group_continuation_boundary_candidates.

The PR #22 bytes of this helper are recorded in boundary-review-summary.json
(helper_sha256) and remain in Git history (commit e82a9cc).
"""
from collections import Counter
import hashlib
import json

from pdfeditor.continuation_review import group_continuation_boundary_candidates


REVIEW_CRITERIA = ['has_rectangular_clip_constraint', 'has_ctm_compensation', 'q_depth']
REVIEW_MEANING = [
    'Absent clip: no inherited rectangular containment constraint to review.',
    'Absent compensation: no inverse and source/interpreted CTM proof to review.',
    'Shallower q depth: fewer enclosing q/Q bindings and restored states to review.',
]
WITNESSES = dict(graphics_state='graphics_state_sha256', graphics_state_scope='scope_sha256',
                 ctm_compensation='compensation_sha256', clip_constraint='clip_constraint_sha256')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _historical_row(row):
    digests = row['authority_digests']
    return dict(boundary_id=row['boundary_id'], ordinal=row['ordinal'], previous_operator=row['previous_operator'],
        next_operator=row['next_operator'], operator_context=row['operator_context'], q_depth=row['q_depth'],
        page_level=row['q_depth'] == 0, has_scope_binding=row['has_scope_binding'], scope_policy=row['scope_policy'],
        scope_operators=row['scope_operators'], has_ctm_compensation=row['has_ctm_compensation'],
        has_rectangular_clip_constraint=row['has_rectangular_clip_constraint'],
        inherited_clip=row['has_rectangular_clip_constraint'],
        **{historical: digests[name] for name, historical in WITNESSES.items()})


def group_candidates(candidates):
    """PR #22 group records from the formal groups; paint-group IDs are local to the input revision."""
    result = []
    for group in group_continuation_boundary_candidates(candidates):
        z = group['z_order']
        key = [group['page'], z['semantics'], z['prefix_paint_operators'], z['suffix_paint_operators']]
        rows = [_historical_row(row) for row in group['candidates']]
        result.append(dict(page=group['page'], group_id='paint-group-' + _digest(key)[:24],
            program_sha256=group['program_sha256'], z_order=z, candidate_count=group['candidate_count'],
            ordinal_min=group['ordinal_min'], ordinal_max=group['ordinal_max'], boundary_ids=group['boundary_ids'],
            candidates=rows, least_constrained_candidates=group['minimal_authority_review_candidates'],
            distinct_witness_counts={historical: group['distinct_authority_variants'][name]
                                     for name, historical in WITNESSES.items()}))
    return result


def group_statistics(groups):
    sizes = Counter(group['candidate_count'] for group in groups)
    single_choice = sum(len(group['least_constrained_candidates']) == 1 for group in groups)
    return dict(candidates=sum(size * count for size, count in sizes.items()), groups=len(groups),
        group_size_distribution={str(size): sizes[size] for size in sorted(sizes)},
        singleton_groups=sizes[1], multiple_candidate_groups=sum(count for size, count in sizes.items() if size > 1),
        maximum_group_size=max(sizes, default=0), least_constrained_singleton_groups=single_choice,
        least_constrained_tied_groups=len(groups) - single_choice,
        least_constrained_candidates=sum(len(group['least_constrained_candidates']) for group in groups),
        multiple_candidate_groups_with_single_least=sum(group['candidate_count'] > 1 and
            len(group['least_constrained_candidates']) == 1 for group in groups))
