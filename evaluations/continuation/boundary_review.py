"""Pure presentation grouping of already-safe inspector candidates.

Groups mean only the same page and existing paint position. They neither
identify an authority nor make candidates interchangeable. No geometry,
confirmation, renderer, PDF writer or candidate selection is involved.

The optional least-constrained set minimizes (clip present, compensation
present, q depth), lexicographically, retaining every tie. This describes
review work, not safety: no clip removes an inherited containment constraint;
no compensation removes inverse/model-proof review; shallower scope means
fewer q/Q identities and restored states to check. It is not a recommendation.
"""
from collections import Counter, defaultdict
import hashlib
import json

from pdfeditor.continuation import SCOPE, SCOPE_CHAIN, SCOPE_THREE


REVIEW_CRITERIA = ['has_rectangular_clip_constraint', 'has_ctm_compensation', 'q_depth']
REVIEW_MEANING = [
    'Absent clip: no inherited rectangular containment constraint to review.',
    'Absent compensation: no inverse and source/interpreted CTM proof to review.',
    'Shallower q depth: fewer enclosing q/Q bindings and restored states to review.',
]


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('expected a nonnegative integer' if not minimum else 'expected a positive integer')
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('expected a nonempty string')
    return value


def _operator(value, ordinal):
    if not isinstance(value, dict) or _integer(value['ordinal']) != ordinal:
        raise ValueError('operator ordinal differs from boundary context')
    return _text(value['operator'])


def _scope(candidate, depth):
    if depth == 0:
        if 'graphics_state_scope' in candidate:
            raise ValueError('page-level candidate has an enclosing authority')
        return None, [], None
    if depth not in (1, 2, 3):
        raise ValueError('unsupported scope depth')
    scope = candidate['graphics_state_scope']
    policy = (SCOPE, SCOPE_CHAIN, SCOPE_THREE)[depth - 1]
    if not isinstance(scope, dict) or type(scope['depth']) is not int or scope['depth'] != depth or scope['policy'] != policy:
        raise ValueError('scope policy/depth differs')
    names = ['scope'] if depth == 1 else ['outer', 'inner'] if depth == 2 else ['outer', 'middle', 'inner']
    expected = {'policy', 'depth', 'contract'} | ({'opening', 'matching', 'restored_state'} if depth == 1 else set(names))
    if set(scope) != expected:
        raise ValueError('malformed scope form')
    _text(scope['contract'])
    levels = []
    for name in names:
        level = scope if depth == 1 else scope[name]
        if not isinstance(level, dict) or not isinstance(level['restored_state'], dict):
            raise ValueError('malformed scope level')
        q, Q = level['opening'], level['matching']
        opening, matching = _integer(q['ordinal']), _integer(Q['ordinal'])
        if q['operator'] != 'q' or Q['operator'] != 'Q' or not opening <= candidate['ordinal'] < matching:
            raise ValueError('malformed scope pair')
        if levels and not levels[-1]['opening_ordinal'] < opening < matching < levels[-1]['matching_ordinal']:
            raise ValueError('malformed scope nesting')
        levels.append(dict(level=name, opening_ordinal=opening, matching_ordinal=matching))
    return policy, levels, _digest(scope)


def _compact(candidate):
    try:
        if not isinstance(candidate, dict) or candidate['status'] != 'safe' or candidate['reasons'] != []:
            raise ValueError('expected an already-safe inspector candidate')
        page, ordinal = _integer(candidate['page'], 1), _integer(candidate['ordinal'])
        ident, program = _text(candidate['boundary_id']), _text(candidate['program_sha256'])
        z = candidate['z_order']
        if not isinstance(z, dict) or set(z) != {'semantics', 'prefix_paint_operators', 'suffix_paint_operators'}:
            raise ValueError('malformed z_order')
        key = (page, _text(z['semantics']), _integer(z['prefix_paint_operators']), _integer(z['suffix_paint_operators']))
        scope = candidate['scope']
        depth = _integer(scope['q_depth'])
        if (scope['text_object'] is not False or scope['pending_path'] is not False
                or scope['pending_clip'] is not False or _integer(scope['marked_content_depth']) != 0
                or _integer(scope['compatibility_depth']) != 0):
            raise ValueError('safe label contradicts structural scope')
        state = candidate['graphics_state']
        if not isinstance(state, dict) or not isinstance(state['clip'], list):
            raise ValueError('malformed graphics state')
        clipped, compensated = 'clip_constraint' in candidate, 'ctm_compensation' in candidate
        if bool(state['clip']) != clipped:
            raise ValueError('inherited clip and constraint presence differ')
        for field in ('clip_constraint', 'ctm_compensation'):
            if field in candidate and (not isinstance(candidate[field], dict) or not candidate[field]):
                raise ValueError('malformed additional authority')
        policy, levels, scope_sha = _scope(candidate, depth)
        previous, following = _operator(candidate['previous'], ordinal), _operator(candidate['next'], ordinal + 1)
        row = dict(boundary_id=ident, ordinal=ordinal, previous_operator=previous, next_operator=following,
            operator_context=f'{previous} -> boundary -> {following}', q_depth=depth,
            page_level=depth == 0, has_scope_binding=depth != 0, scope_policy=policy, scope_operators=levels,
            has_ctm_compensation=compensated, has_rectangular_clip_constraint=clipped, inherited_clip=clipped,
            graphics_state_sha256=_digest(state), scope_sha256=scope_sha,
            compensation_sha256=_digest(candidate['ctm_compensation']) if compensated else None,
            clip_constraint_sha256=_digest(candidate['clip_constraint']) if clipped else None)
        return key, program, row
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'malformed boundary review input: {exc}') from exc


def group_candidates(candidates):
    """Return deterministic groups, copying only presentation fields; never mutate input.

Only one source program per page may be supplied. Group IDs hash precisely
the requested grouping key and are local to that inspected input revision;
they are not boundary IDs and must never be passed to confirmation.
"""
    grouped, programs, paint_totals = defaultdict(list), {}, {}
    ids, positions = set(), set()
    for candidate in candidates:
        key, program, row = _compact(candidate)
        page = key[0]
        if row['boundary_id'] in ids or (page, row['ordinal']) in positions:
            raise ValueError('duplicate boundary identity or page ordinal')
        if page in programs and programs[page] != program:
            raise ValueError('cannot mix source programs on one page')
        if page in paint_totals and paint_totals[page] != key[2] + key[3]:
            raise ValueError('inconsistent total paint count on one page')
        ids.add(row['boundary_id'])
        positions.add((page, row['ordinal']))
        programs[page], paint_totals[page] = program, key[2] + key[3]
        grouped[key].append(row)
    result = []
    for key in sorted(grouped):
        rows = sorted(grouped[key], key=lambda row: (row['ordinal'], row['boundary_id']))
        burden = lambda row: tuple(row[field] for field in REVIEW_CRITERIA)
        least = min(map(burden, rows))
        result.append(dict(page=key[0], group_id='paint-group-' + _digest(list(key))[:24],
            program_sha256=programs[key[0]], z_order=dict(semantics=key[1], prefix_paint_operators=key[2], suffix_paint_operators=key[3]),
            candidate_count=len(rows), ordinal_min=rows[0]['ordinal'], ordinal_max=rows[-1]['ordinal'],
            boundary_ids=[row['boundary_id'] for row in rows], candidates=rows,
            least_constrained_candidates=[row['boundary_id'] for row in rows if burden(row) == least],
            distinct_witness_counts={field: len({row[field] for row in rows}) for field in
                ('graphics_state_sha256', 'scope_sha256', 'compensation_sha256', 'clip_constraint_sha256')}))
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
