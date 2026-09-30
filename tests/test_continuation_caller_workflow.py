"""Acceptance: the caller workflow that confirms a continuation destination.

inspection -> structural review -> geometry review -> caller decision ->
confirmation request -> explicit confirmation, through public module APIs
only. The test itself is the caller: it chooses the boundary by an explicit
criterion; no engine helper chooses. The path ends at the confirmed
destination (no shared flow, writer or lifecycle here).
"""
from copy import deepcopy
import hashlib

import pytest

from pdfeditor import continuation
from pdfeditor.backend import PdfError
from pdfeditor.continuation import inspect_continuation_boundaries
from pdfeditor.continuation_review import (build_continuation_boundary_confirmation_request,
                                           review_continuation_boundaries, review_continuation_geometry)
from test_boundary_destination import build
from test_scope_three_boundary import triple

PAGE = 2
BOUNDS = [18, 90, 173, 220]         # empty page space, inside the depth 2/3 clip of the fixture
OCCUPIED = [225, 125, 235, 135]     # crosses an existing path paint
BELOW_CLIP = [18, 20, 173, 60]      # empty page space, beyond the depth 2/3 clip
IDENTITIES = dict(destination_id='reviewed-boundary', paragraph_id='paragraph-A', region_id='next-region')


@pytest.fixture
def source(tmp_path):
    """Page 2: page-level, depth 1, 2 and 3 safe boundaries; clip at depth 2/3, compensated CTM at depth 3."""
    return build(tmp_path, b'0 0 5 5 re f', triple('fractional-clip'))


@pytest.fixture
def confirmations(monkeypatch):
    """Counts every confirm_continuation_destination call made through the public module."""
    calls = []
    original = continuation.confirm_continuation_destination

    def spy(*args, **kwargs):
        calls.append(kwargs.get('boundary'))
        return original(*args, **kwargs)
    monkeypatch.setattr(continuation, 'confirm_continuation_destination', spy)
    return calls


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(review):
    return [row for group in review['groups'] for row in group['candidates']]


def caller_choice(review, *, q_depth, operator_context):
    """The caller's own decision: the one boundary with this q depth and operator context."""
    found = [row['boundary_id'] for row in rows(review)
             if row['q_depth'] == q_depth and row['operator_context'] == operator_context]
    assert len(found) == 1, found
    return found[0]


def test_inspection_to_explicit_confirmation(source, confirmations):
    original = sha(source)

    # 1. Inspection: individual safe boundaries with their authority; no geometry, nothing confirmed.
    inspection = inspect_continuation_boundaries(source, PAGE)
    safe = [c['boundary_id'] for c in inspection['candidates']]
    assert safe and all(ident.startswith('boundary-') for ident in safe)
    assert all(c['status'] == 'safe' and 'geometry' not in c for c in inspection['candidates'])
    assert 'bounds' not in inspection and confirmations == []

    # 2. Structural review: only those safe candidates, grouped by paint position; selects nothing.
    review = review_continuation_boundaries(source, PAGE)
    assert review['program_sha256'] == inspection['program_sha256']
    assert review['candidate_count'] == len(safe)
    assert sorted(r['boundary_id'] for r in rows(review)) == sorted(safe)
    group_ids = [g['group_id'] for g in review['groups']]
    assert all(i.startswith('review-group-') for i in group_ids) and not set(group_ids) & set(safe)
    assert review['contract']['automatic_selection'] is False and review['contract']['geometry_used'] is False

    # 3. Geometry review of the caller's bounds: annotation only, same membership.
    geometry = review_continuation_geometry(source, PAGE, BOUNDS)
    assert geometry['bounds'] == BOUNDS and geometry['geometry']['destination_empty'] is True
    assert [g['group_id'] for g in geometry['groups']] == group_ids
    assert [g['boundary_ids'] for g in geometry['groups']] == [g['boundary_ids'] for g in review['groups']]
    assert [g['minimal_authority_review_candidates'] for g in geometry['groups']] == \
        [g['minimal_authority_review_candidates'] for g in review['groups']]
    assert all(set(r['geometry']) == {'destination_empty', 'clip_check_required', 'bounds_inside_inherited_clip',
                                      'checks_passed'} for r in rows(geometry))
    contract = geometry['contract']
    assert contract['generated_ink_evaluated'] is contract['automatic_selection'] is False
    assert contract['automatic_confirmation'] is False and confirmations == []

    # 4. Caller decision, written here: the depth-3 boundary between a fill and the next text object.
    chosen = caller_choice(geometry, q_depth=3, operator_context='f -> boundary -> BT')
    row = next(r for r in rows(geometry) if r['boundary_id'] == chosen)
    assert row['has_rectangular_clip_constraint'] and row['has_ctm_compensation']
    assert row['geometry']['checks_passed'] is True

    # 5. Confirmation request: hands over exactly that choice; confirms nothing, changes nothing.
    request = build_continuation_boundary_confirmation_request(geometry, boundary_id=chosen, **IDENTITIES)
    assert request['boundary_id'] == request['confirm_kwargs']['boundary'] == chosen
    assert request['contract']['source_revalidated'] is False
    assert request['confirm_kwargs'] == dict(IDENTITIES, page=PAGE, bounds=BOUNDS,
        insertion='confirmed-page-program-boundary', graphics_state='confirmed-boundary-state', boundary=chosen)
    assert 'page_entry_order' not in request['confirm_kwargs']
    assert sha(source) == original and confirmations == []

    # 6. Explicit confirmation by the caller, against the current source.
    handed = deepcopy(request)
    destination = continuation.confirm_continuation_destination(source, **request['confirm_kwargs'])
    assert confirmations == [chosen] and request == handed
    assert destination['authority']['boundary_id'] == chosen and destination['bounds'] == BOUNDS
    assert destination['page'] == PAGE and destination['provenance'] == 'explicitly_confirmed'
    assert destination['source_program_sha256'] == request['review_program_sha256']
    assert destination['source_pdf_sha256'] == original == sha(source)
    assert {k: destination[k] for k in IDENTITIES} == IDENTITIES
    # A request is not a destination, and a destination is not a request.
    assert not {'schema', 'confirm_kwargs', 'contract'} & set(destination)
    assert not {'authority', 'provenance', 'source_pdf_sha256'} & set(request)


# -- negative workflow paths ---------------------------------------------------------

def test_occupied_bounds_stop_at_the_request(source, confirmations):
    geometry = review_continuation_geometry(source, PAGE, OCCUPIED)
    chosen = caller_choice(geometry, q_depth=3, operator_context='f -> boundary -> BT')
    with pytest.raises(PdfError, match='not empty page space'):
        build_continuation_boundary_confirmation_request(geometry, boundary_id=chosen, **IDENTITIES)
    assert confirmations == []


def test_a_clip_misfit_stops_only_that_candidate(source, confirmations):
    geometry = review_continuation_geometry(source, PAGE, BELOW_CLIP)
    clipped = caller_choice(geometry, q_depth=3, operator_context='f -> boundary -> BT')
    clip_free = caller_choice(geometry, q_depth=0, operator_context='f -> boundary -> q')
    with pytest.raises(PdfError, match='outside its inherited clip'):
        build_continuation_boundary_confirmation_request(geometry, boundary_id=clipped, **IDENTITIES)
    request = build_continuation_boundary_confirmation_request(geometry, boundary_id=clip_free, **IDENTITIES)
    assert request['confirm_kwargs']['boundary'] == clip_free and request['bounds'] == BELOW_CLIP
    assert confirmations == []


def test_a_stale_review_builds_a_request_that_confirmation_refuses(tmp_path, confirmations):
    path = build(tmp_path, b'0 0 5 5 re f', triple('fractional-clip'))
    geometry = review_continuation_geometry(path, PAGE, BOUNDS)
    chosen = caller_choice(geometry, q_depth=3, operator_context='f -> boundary -> BT')
    build(tmp_path, b'0 0 5 5 re f', triple('fractional-clip', source='before'))  # new revision, same path
    request = build_continuation_boundary_confirmation_request(geometry, boundary_id=chosen, **IDENTITIES)
    with pytest.raises(PdfError, match='not a safe page-level candidate'):
        continuation.confirm_continuation_destination(path, **request['confirm_kwargs'])
    assert confirmations == [chosen]


def test_the_caller_must_choose_a_boundary_not_a_group(source, confirmations):
    geometry = review_continuation_geometry(source, PAGE, BOUNDS)
    group = next(g for g in geometry['groups'] if g['candidate_count'] > 1)
    with pytest.raises(PdfError, match='group ID is not a boundary ID'):
        build_continuation_boundary_confirmation_request(geometry, boundary_id=group['group_id'], **IDENTITIES)
    with pytest.raises(PdfError, match='select a boundary_id explicitly'):
        build_continuation_boundary_confirmation_request(geometry, **IDENTITIES)
    assert confirmations == []
