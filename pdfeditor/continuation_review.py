"""Read-only review presentation of safe continuation boundaries.

inspect_continuation_boundaries (pdfeditor.continuation) lists each safe
page-program boundary with its own authority. This module arranges those
already-safe candidates for review; it never inspects, selects, ranks or
confirms a boundary, and it never reads destination geometry.

A review group is every safe candidate of one page program that lies between
the same existing paint: equal page, program SHA-256, z-order semantics and
prefix/suffix paint counts, exactly. Nothing else is grouped: not near
ordinals, not similar operators, not similar states. A group is not an
authority equivalence. Candidates of one group may differ in graphics state,
q ... Q scope, CTM compensation and inherited clip; each keeps its own
boundary ID, which remains the only thing a caller may confirm.

A group ID (``review-group-...``) hashes the page, the program SHA-256, the
z-order semantics and both paint counts. It is deterministic for one program
and paint position, differs between revisions (another program SHA-256), and
is neither a boundary ID nor a persistent or confirmable identity.

Each candidate lists its ``review_requirements``: the additional authority
evidence a caller reads before confirming it (scope binding, CTM
compensation, rectangular clip). They are not safety levels; every candidate
here is already safe. ``minimal_authority_review_candidates`` are those of a
group whose (rectangular clip, CTM compensation, q depth) attributes are the
lexicographic minimum (false before true, shallower first); every tie stays,
no other candidate is removed, and it is neither a recommendation nor a
safety ranking.

``authority_digests`` are canonical SHA-256 hashes of each witness, so that
a caller can see which candidates carry different authority without copying
it. They are for presentation only: not authority identities, not security
tokens. The full witnesses stay in the inspector records.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import math
import re

from .backend import PdfError
from .continuation import (CLIP, COMPENSATION, IDENTITY, MAX_SCOPE_DEPTH, SCOPE, SCOPE_CHAIN, SCOPE_THREE,
                           _scope_levels, inspect_continuation_boundaries)


REVIEW_SCHEMA = 'pdfengine-continuation-boundary-review-1'
GROUP_PREFIX = 'review-group-'
# Domain-separates group IDs from boundary IDs and from any later group form.
GROUP_DOMAIN = 'pdfengine-continuation-review-group-1'
GROUP_KEY = ('page', 'program_sha256', 'z_order.semantics', 'z_order.prefix_paint_operators',
             'z_order.suffix_paint_operators')
REVIEW_REQUIREMENTS = ('scope-binding', 'ctm-compensation', 'rectangular-clip')
REVIEW_ATTRIBUTES = ('rectangular_clip', 'ctm_compensation', 'q_depth')
AUTHORITY_WITNESSES = ('graphics_state', 'graphics_state_scope', 'ctm_compensation', 'clip_constraint')
POLICIES = (SCOPE, SCOPE_CHAIN, SCOPE_THREE)
LEVEL_NAMES = (('scope',), ('outer', 'inner'), ('outer', 'middle', 'inner'))
BOUNDARY_ID = re.compile(r'boundary-[0-9a-f]{24}')
SHA256 = re.compile(r'[0-9a-f]{64}')
REVIEW_CONTRACT = dict(
    schema=REVIEW_SCHEMA, read_only=True,
    input='only the safe candidates of inspect_continuation_boundaries(source, page); refused boundaries are never reviewed',
    group_key=list(GROUP_KEY),
    group_meaning='Candidates between the same existing paint of one page program. It infers no graphics state, '
                  'scope, CTM compensation, clip or authority equivalence; candidates of one group are not interchangeable.',
    group_identity=dict(prefix=GROUP_PREFIX, covers=list(GROUP_KEY),
        lifetime='this page program revision only; another program SHA-256 gives another group ID',
        boundary_id=False, confirmable=False, persistent=False),
    review_requirements=dict(zip(REVIEW_REQUIREMENTS, (
        'the enclosing q ... Q scope or chain: each level\'s q and matching Q and the state each Q restores',
        'the recorded inverse CTM and its source/interpreted displacement proof',
        'the inherited rectangular clip: the destination and generated ink must lie inside its certified rectangle'))),
    requirements_meaning='Additional authority evidence a caller reads before confirming; not a safety level.',
    minimal_authority_review=dict(attributes=list(REVIEW_ATTRIBUTES),
        comparison='lexicographic ascending on the attributes themselves: no clip before clip, no compensation '
                   'before compensation, shallower q depth first',
        ties='every candidate equal to the minimum is retained', others='retained in the group, never removed',
        meaning='candidates with the least additional authority evidence to review; not safer, not recommended'),
    authority_digests='canonical SHA-256 of each witness, absence counted as one variant; for presentation only, '
                      'never an authority identity or security token',
    automatic_selection=False, safety_ranking=False, recommendation=False, automatic_confirmation=False,
    geometry_used=False,
    confirmation='only a caller-chosen boundary_id is confirmed; a group ID is never passed to confirmation',
    limitation='A safe candidate has not thereby been reviewed as an empty destination.')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('expected an integer of at least %d' % minimum)
    return value


def _text(value, pattern=None):
    if not isinstance(value, str) or not value.strip() or pattern is not None and not pattern.fullmatch(value):
        raise ValueError('expected a well-formed string')
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('expected a finite number')
    return value


def _operator(value, ordinal):
    if not isinstance(value, dict) or _integer(value['ordinal']) != ordinal:
        raise ValueError('operator ordinal differs from its boundary')
    return _text(value['operator'])


def _scope(candidate, ordinal, depth):
    """Policy and q/Q ordinals, outermost first, of the inspector's scope form for ``depth``."""
    if depth == 0:
        if 'graphics_state_scope' in candidate:
            raise ValueError('page-level candidate has an enclosing scope')
        return None, []
    scope = candidate['graphics_state_scope']
    if not isinstance(scope, dict) or scope.get('depth') != depth or scope.get('policy') != POLICIES[depth - 1]:
        raise ValueError('scope policy or depth differs from the boundary')
    levels = _scope_levels(scope)
    rows = []
    for name, level in zip(LEVEL_NAMES[depth - 1], levels):
        opening, matching = level['opening']['ordinal'], level['matching']['ordinal']
        if not opening <= ordinal < matching:
            raise ValueError('boundary is not between its opening q and matching Q')
        if rows and not rows[-1]['opening_ordinal'] < opening < matching < rows[-1]['matching_ordinal']:
            raise ValueError('scope levels do not nest')
        rows.append(dict(level=name, opening_ordinal=opening, matching_ordinal=matching))
    return POLICIES[depth - 1], rows


def _compensation(candidate, ctm):
    compensated = 'ctm_compensation' in candidate
    if compensated != (ctm != list(IDENTITY)):
        raise ValueError('CTM compensation presence differs from the boundary CTM')
    if compensated:
        value = candidate['ctm_compensation']
        if (not isinstance(value, dict) or value.get('policy') != COMPENSATION or value.get('confirmed_ctm') != ctm
                or not isinstance(value.get('matrix'), list) or len(value['matrix']) != 6
                or not all(isinstance(v, str) and v for v in value['matrix'])
                or value.get('operator') != ' '.join(value['matrix']) + ' cm'
                or not isinstance(value.get('proof'), dict)):
            raise ValueError('malformed CTM compensation')
    return compensated


def _clip(candidate, clip):
    clipped = 'clip_constraint' in candidate
    if not isinstance(clip, list) or bool(clip) != clipped:
        raise ValueError('inherited clip and clip constraint presence differ')
    if clipped:
        value = candidate['clip_constraint']
        rectangle = value.get('rectangle') if isinstance(value, dict) else None
        if (not isinstance(value, dict) or value.get('policy') != CLIP or not isinstance(rectangle, list)
                or len(rectangle) != 4 or not all(isinstance(v, dict) for v in value.get('clips') or [None])
                or len(value['clips']) != len(clip)):
            raise ValueError('malformed clip constraint')
        x0, y0, x1, y1 = map(_number, rectangle)
        if not (x0 < x1 and y0 < y1):
            raise ValueError('empty clip constraint rectangle')
    return clipped


def _review_row(candidate):
    """``(group key, compact row)`` of one already-safe inspector candidate; never a reference into it."""
    try:
        if not isinstance(candidate, dict) or candidate['status'] != 'safe' or candidate['reasons'] != []:
            raise ValueError('expected an already-safe inspector candidate')
        _canonical(candidate)
        page, ordinal = _integer(candidate['page'], 1), _integer(candidate['ordinal'])
        ident = _text(candidate['boundary_id'], BOUNDARY_ID)
        program = _text(candidate['program_sha256'], SHA256)
        z = candidate['z_order']
        if not isinstance(z, dict) or set(z) != {'semantics', 'prefix_paint_operators', 'suffix_paint_operators'}:
            raise ValueError('malformed z_order')
        key = (page, program, _text(z['semantics']), _integer(z['prefix_paint_operators']),
               _integer(z['suffix_paint_operators']))
        scope = candidate['scope']
        depth = _integer(scope['q_depth'])
        if depth > MAX_SCOPE_DEPTH:
            raise ValueError('unsupported q depth')
        if (scope['text_object'] is not False or scope['pending_path'] is not False
                or scope['pending_clip'] is not False or _integer(scope['marked_content_depth']) != 0
                or _integer(scope['compatibility_depth']) != 0):
            raise ValueError('safe status contradicts the structural scope')
        state = candidate['graphics_state']
        if not isinstance(state, dict) or not isinstance(state['ctm'], list) or len(state['ctm']) != 6:
            raise ValueError('malformed graphics state')
        for value in state['ctm']:
            _number(value)
        compensated, clipped = _compensation(candidate, state['ctm']), _clip(candidate, state['clip'])
        policy, levels = _scope(candidate, ordinal, depth)
        previous, following = _operator(candidate['previous'], ordinal), _operator(candidate['next'], ordinal + 1)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise PdfError(f'malformed continuation boundary review input: {exc}') from exc
    present = dict(zip(REVIEW_REQUIREMENTS, (depth != 0, compensated, clipped)))
    row = dict(boundary_id=ident, ordinal=ordinal, previous_operator=previous, next_operator=following,
        operator_context=f'{previous} -> boundary -> {following}', q_depth=depth, scope_policy=policy,
        has_scope_binding=depth != 0, scope_operators=levels, has_ctm_compensation=compensated,
        has_rectangular_clip_constraint=clipped,
        review_requirements=[name for name in REVIEW_REQUIREMENTS if present[name]],
        review_attributes=dict(zip(REVIEW_ATTRIBUTES, (clipped, compensated, depth))),
        authority_digests={name: _digest(candidate[name]) if name in candidate else None
                           for name in AUTHORITY_WITNESSES})
    return key, row


def _group_id(page, program_sha256, semantics, prefix_paint_operators, suffix_paint_operators):
    """The presentation ID of one paint position of one page program; never a boundary ID."""
    return GROUP_PREFIX + _digest([GROUP_DOMAIN, page, program_sha256, semantics,
                                   prefix_paint_operators, suffix_paint_operators])[:24]


def _burden(row):
    return tuple(row['review_attributes'][name] for name in REVIEW_ATTRIBUTES)


def group_continuation_boundary_candidates(candidates):
    """Group already-safe inspector candidates by exact paint position, for review only.

    ``candidates`` are safe records exactly as inspect_continuation_boundaries
    returns them, from one or several pages, with one program per page. Any
    other record, a duplicate boundary ID or page ordinal, two programs of
    one page or inconsistent paint totals is refused (PdfError). The input
    is never mutated or referenced by the result. Groups are ordered by page
    and paint position, candidates by ordinal; the result is independent of
    input order. Nothing is selected, ranked or confirmed.
    """
    grouped, programs, totals, ids, positions = defaultdict(list), {}, {}, set(), set()
    for candidate in candidates:
        key, row = _review_row(candidate)
        page, program, _, prefix, suffix = key
        if row['boundary_id'] in ids or (page, row['ordinal']) in positions:
            raise PdfError('malformed continuation boundary review input: duplicate boundary ID or page ordinal')
        if programs.setdefault(page, program) != program:
            raise PdfError('malformed continuation boundary review input: two programs of one page')
        if totals.setdefault(page, prefix + suffix) != prefix + suffix:
            raise PdfError('malformed continuation boundary review input: inconsistent paint total on one page')
        ids.add(row['boundary_id'])
        positions.add((page, row['ordinal']))
        grouped[key].append(row)
    groups = []
    for key in sorted(grouped):
        rows = sorted(grouped[key], key=lambda row: row['ordinal'])
        least = min(map(_burden, rows))
        minimal = [row for row in rows if _burden(row) == least]
        groups.append(dict(group_id=_group_id(*key), page=key[0], program_sha256=key[1],
            z_order=dict(semantics=key[2], prefix_paint_operators=key[3], suffix_paint_operators=key[4]),
            candidate_count=len(rows), ordinal_min=rows[0]['ordinal'], ordinal_max=rows[-1]['ordinal'],
            boundary_ids=[row['boundary_id'] for row in rows], candidates=rows,
            minimal_authority_review_candidates=[row['boundary_id'] for row in minimal],
            minimal_authority_review_attributes=dict(minimal[0]['review_attributes']),
            distinct_authority_variants={name: len({row['authority_digests'][name] for row in rows})
                                         for name in AUTHORITY_WITNESSES}))
    return groups


def _review(page, inspection):
    """The review of one inspect_continuation_boundaries result of ``page``: its safe candidates only."""
    program = inspection['program_sha256']
    if any(not isinstance(c, dict) or c.get('page') != page or c.get('program_sha256') != program
           for c in inspection['candidates']):
        raise PdfError('continuation boundary review input differs from its inspected page program')
    groups = group_continuation_boundary_candidates(inspection['candidates'])
    return dict(schema=REVIEW_SCHEMA, page=page, program_sha256=program,
        candidate_count=sum(group['candidate_count'] for group in groups), group_count=len(groups),
        groups=groups, contract=deepcopy(REVIEW_CONTRACT))


def review_continuation_boundaries(source, page):
    """Read-only review of the safe continuation boundaries of one page.

    Calls inspect_continuation_boundaries(source, page) and groups its safe
    candidates by exact paint position (group_continuation_boundary_candidates).
    Refused boundaries are never reviewed. Returns the page, the inspected
    program SHA-256, candidate and group counts, the groups and the review
    contract. Selects, ranks and confirms nothing; a caller confirms a
    ``boundary_id`` it chose, never a group ID.
    """
    return _review(page, inspect_continuation_boundaries(source, page))
