"""Read-only geometry review: caller bounds annotated on the formal boundary review.

The synthetic page 2 holds page-level, depth 1, 2 and 3 safe candidates;
depth 2 and 3 inherit a rectangular clip and depth 3 a compensated CTM. All
emptiness and clip results come from the real require_empty / clip_contains.
"""
from copy import deepcopy
import hashlib
import math

import pytest

from pdfeditor import continuation as destinations
from pdfeditor import continuation_review as review
from pdfeditor.backend import PdfError
from pdfeditor.continuation import inspect_continuation_boundaries
from pdfeditor.continuation_review import (GEOMETRY_CONTRACT, GEOMETRY_SCHEMA, REVIEW_CONTRACT, REVIEW_SCHEMA,
                                           review_continuation_boundaries, review_continuation_geometry)
from test_boundary_destination import REGION, build
from test_scope_three_boundary import triple

INSIDE_CLIP = REGION['bounds']      # empty page space inside the depth 2/3 clip
BELOW_CLIP = [18, 20, 173, 60]      # empty page space, beyond the clip's top edge (page space, y down)
ON_PAINT = [225, 125, 235, 135]     # crosses the inner-suffix path paint (no glyph)
OUTSIDE_PAGE = [300, 250, 330, 270]
GEOMETRY_KEYS = ('geometry_checks_passed_count', 'geometry_checks_failed_count')
# REVIEW_CONTRACT as merged in PR #23 (canonical JSON SHA-256); the structural review must not change.
PR23_REVIEW_CONTRACT_SHA256 = '23f1a00f6ba901357229ae55190c4778849ef5e3e62ba238c857f52bfccb3320'


@pytest.fixture
def source(tmp_path):
    return build(tmp_path, b'0 0 5 5 re f', triple('fractional-clip'))


def rows(found):
    return [row for group in found['groups'] for row in group['candidates']]


def structural(found):
    """The geometry review with only its geometry annotations removed."""
    groups = deepcopy(found['groups'])
    for group in groups:
        for key in GEOMETRY_KEYS:
            del group[key]
        for row in group['candidates']:
            del row['geometry']
    return groups


def clip_rectangle(source):
    rectangles = {tuple(c['clip_constraint']['rectangle'])
                  for c in inspect_continuation_boundaries(source, 2)['candidates'] if 'clip_constraint' in c}
    assert len(rectangles) == 1
    return rectangles.pop()


def test_empty_bounds_pass_without_clip_and_fail_only_outside_a_clip(source):
    found = review_continuation_geometry(source, 2, BELOW_CLIP)
    assert found['geometry']['destination_empty'] is True and found['geometry']['empty_check_error'] is None
    clipped = [row for row in rows(found) if row['has_rectangular_clip_constraint']]
    free = [row for row in rows(found) if not row['has_rectangular_clip_constraint']]
    assert clipped and free
    for row in free:
        assert row['geometry'] == dict(destination_empty=True, clip_check_required=False,
                                       bounds_inside_inherited_clip=True, checks_passed=True)
    for row in clipped:
        assert row['geometry'] == dict(destination_empty=True, clip_check_required=True,
                                       bounds_inside_inherited_clip=False, checks_passed=False)
    assert found['geometry']['checks_passed_candidates'] == len(free)
    assert found['geometry']['checks_failed_candidates'] == found['geometry']['clip_checked_candidates'] == len(clipped)


def test_bounds_inside_the_clip_pass_for_clip_candidates_too(source):
    found = review_continuation_geometry(source, 2, INSIDE_CLIP)
    assert found['geometry']['destination_empty'] is True
    assert all(row['geometry']['checks_passed'] for row in rows(found))
    assert any(row['geometry']['clip_check_required'] for row in rows(found))
    assert all(g['geometry_checks_failed_count'] == 0 and g['geometry_checks_passed_count'] == g['candidate_count']
               for g in found['groups'])


@pytest.mark.parametrize('bounds, message', [(ON_PAINT, 'intersects'),
                                             (OUTSIDE_PAGE, 'inside its existing page')])
def test_occupied_or_off_page_bounds_fail_every_candidate_and_remove_none(source, bounds, message):
    found = review_continuation_geometry(source, 2, bounds)
    assert found['geometry']['destination_empty'] is False and message in found['geometry']['empty_check_error']
    assert structural(found) == review_continuation_boundaries(source, 2)['groups']
    assert found['candidate_count'] == len(inspect_continuation_boundaries(source, 2)['candidates'])
    for group in found['groups']:
        assert group['geometry_checks_passed_count'] == 0
        assert group['geometry_checks_failed_count'] == group['candidate_count'] == len(group['boundary_ids'])
        assert [row['boundary_id'] for row in group['candidates']] == group['boundary_ids']
    for row in rows(found):
        assert row['geometry']['destination_empty'] is False and row['geometry']['checks_passed'] is False


@pytest.mark.parametrize('side', range(4))
def test_bounds_on_the_clip_edge_are_inside_and_any_step_beyond_is_not(source, side):
    x0, y0, x1, y1 = clip_rectangle(source)
    # An empty box that reaches exactly one edge of the certified rectangle.
    edge = [[x0, 90, 173, 220], [18, y0, 173, 220], [225, 97, x1, 115], [18, 90, 173, y1]][side]
    beyond = list(edge)
    beyond[side] = math.nextafter(edge[side], -math.inf if side < 2 else math.inf)
    on = review_continuation_geometry(source, 2, edge)
    off = review_continuation_geometry(source, 2, beyond)
    for found, inside in ((on, True), (off, False)):
        assert found['geometry']['destination_empty'] is True
        for row in rows(found):
            clipped = row['has_rectangular_clip_constraint']
            assert row['geometry']['bounds_inside_inherited_clip'] is (inside or not clipped)
            assert row['geometry']['checks_passed'] is (inside or not clipped)


@pytest.mark.parametrize('bounds', [INSIDE_CLIP, BELOW_CLIP, ON_PAINT, OUTSIDE_PAGE])
def test_geometry_only_annotates_the_unchanged_structural_review(source, bounds):
    plain = review_continuation_boundaries(source, 2)
    found = review_continuation_geometry(source, 2, bounds)
    # Depth 0-3, compensation, clip presentation, order and minimal sets are exactly the review's.
    assert structural(found) == plain['groups']
    assert (found['page'], found['program_sha256'], found['candidate_count'], found['group_count']) == \
        (plain['page'], plain['program_sha256'], plain['candidate_count'], plain['group_count'])
    assert {row['q_depth'] for row in rows(found)} == {0, 1, 2, 3}
    compensated = [row for row in rows(found) if row['has_ctm_compensation']]
    assert compensated
    for row in compensated:
        # A compensated CTM plays no part in geometry: only emptiness and the clip do.
        assert row['geometry']['checks_passed'] is (row['geometry']['destination_empty']
                                                    and row['geometry']['bounds_inside_inherited_clip'])
    assert [g['minimal_authority_review_candidates'] for g in found['groups']] == \
        [g['minimal_authority_review_candidates'] for g in plain['groups']]


def test_minimal_authority_review_candidates_keep_geometry_failures(source):
    found = review_continuation_geometry(source, 2, BELOW_CLIP)
    failing = {row['boundary_id'] for row in rows(found) if not row['geometry']['checks_passed']}
    kept = [ident for g in found['groups'] for ident in g['minimal_authority_review_candidates'] if ident in failing]
    assert kept


def test_existing_review_api_keeps_the_pr23_contract(source):
    plain = review_continuation_boundaries(source, 2)
    assert set(plain) == {'schema', 'page', 'program_sha256', 'candidate_count', 'group_count', 'groups', 'contract'}
    assert plain['schema'] == REVIEW_SCHEMA == 'pdfengine-continuation-boundary-review-1'
    assert plain['contract'] == REVIEW_CONTRACT and plain['contract']['geometry_used'] is False
    assert review._digest(REVIEW_CONTRACT) == PR23_REVIEW_CONTRACT_SHA256
    assert not any('geometry' in row for row in rows(plain))
    assert not any(key in group for group in plain['groups'] for key in GEOMETRY_KEYS)


def test_schema_and_contract(source):
    found = review_continuation_geometry(source, 2, (18, 90, 173.5, 220))
    assert set(found) == {'schema', 'page', 'program_sha256', 'bounds', 'candidate_count', 'group_count', 'groups',
                          'geometry', 'contract'}
    assert found['schema'] == GEOMETRY_SCHEMA and found['bounds'] == [18, 90, 173.5, 220]
    assert found['contract'] == GEOMETRY_CONTRACT and found['contract'] is not GEOMETRY_CONTRACT
    contract = found['contract']
    assert contract['read_only'] is contract['geometry_used'] is True
    assert (contract['automatic_selection'] is contract['safety_ranking'] is contract['recommendation']
            is contract['automatic_confirmation'] is contract['generated_ink_evaluated'] is False)
    assert contract['destination_empty']['calls_per_review'] == 1
    assert contract['destination_empty']['owned_glyph_exemption'] is False
    assert contract['structural_review']['schema'] == REVIEW_SCHEMA
    assert 'planned glyph ink' in contract['not_evaluated'] and 'capacity' in contract['not_evaluated']
    for forbidden in ('best_geometry_candidate', 'recommended_candidate', 'preferred_candidate'):
        assert forbidden not in found and all(forbidden not in g for g in found['groups'])


@pytest.mark.parametrize('bounds', [
    [math.nan, 90, 173, 220], [18, math.inf, 173, 220], [18, 90, -math.inf, 220], [True, 90, 173, 220],
    [18, 90, 173, False], [18, 90, 173], [18, 90, 173, 220, 230], [], [18, 90, 18, 220], [18, 90, 173, 90],
    [173, 90, 18, 220], [18, 220, 173, 90], ['18', 90, 173, 220], [None, 90, 173, 220], None, 5,
    {'x0': 18}, '18 90 173 220'])
def test_malformed_bounds_fail_closed(source, bounds, monkeypatch):
    monkeypatch.setattr(review, 'ContentPage', lambda *a: pytest.fail('page opened for malformed bounds'))
    with pytest.raises(PdfError, match='malformed continuation geometry review bounds'):
        review_continuation_geometry(source, 2, bounds)


def test_read_only_confirms_nothing_and_leaves_the_pdf_unchanged(source, monkeypatch):
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    for name in ('confirm_continuation_destination', '_boundary_authority', 'require_inside_clip'):
        monkeypatch.setattr(destinations, name, lambda *a, **k: pytest.fail('geometry review confirmed'))
    for bounds in (INSIDE_CLIP, BELOW_CLIP, ON_PAINT):
        review_continuation_geometry(source, 2, bounds)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in source.parent.iterdir()) == [source.name]


def test_one_page_one_inspection_one_emptiness_check(source, monkeypatch):
    calls = dict(page=0, inspect=0, empty=0, clip=0)

    def counting(name, function):
        def wrapped(*args, **kwargs):
            calls[name] += 1
            return function(*args, **kwargs)
        return wrapped

    monkeypatch.setattr(review, 'ContentPage', counting('page', review.ContentPage))
    monkeypatch.setattr(review, '_inspect', counting('inspect', review._inspect))
    monkeypatch.setattr(review, 'require_empty', counting('empty', review.require_empty))
    monkeypatch.setattr(review, 'clip_contains', counting('clip', review.clip_contains))
    monkeypatch.setattr(review, 'inspect_continuation_boundaries', lambda *a, **k: pytest.fail('second inspection'))
    monkeypatch.setattr(destinations, 'inspect_continuation_boundaries', lambda *a, **k: pytest.fail('second inspection'))
    found = review_continuation_geometry(source, 2, BELOW_CLIP)
    clipped = sum(row['has_rectangular_clip_constraint'] for row in rows(found))
    assert found['candidate_count'] > clipped > 0
    assert calls == dict(page=1, inspect=1, empty=1, clip=clipped)
