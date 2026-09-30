"""A caller-chosen boundary of a geometry review, handed to explicit confirmation.

build_continuation_boundary_confirmation_request only turns a geometry review
record and the caller's boundary_id into confirm arguments. The tests choose
each boundary themselves, by an explicit criterion, as a caller would; the
builder never chooses, confirms or reads the source.
"""
from copy import deepcopy
import hashlib
import math

import pytest

from pdfeditor import continuation as destinations
from pdfeditor import continuation_review as review
from pdfeditor.backend import PdfError
from pdfeditor.continuation import BOUNDARY, BOUNDARY_STATE, confirm_continuation_destination
from pdfeditor.continuation_review import (GEOMETRY_CONTRACT, REQUEST_CONTRACT, REQUEST_SCHEMA, REVIEW_CONTRACT,
                                           build_continuation_boundary_confirmation_request,
                                           review_continuation_boundaries, review_continuation_geometry)
from test_boundary_destination import REGION, build
from test_continuation_geometry_review import BELOW_CLIP, ON_PAINT, PR23_REVIEW_CONTRACT_SHA256, rows, structural
from test_scope_three_boundary import pick, triple

IDENTITIES = dict(destination_id='reviewed-boundary', paragraph_id='paragraph-A', region_id='next-region')
# GEOMETRY_CONTRACT as merged in PR #25 (canonical JSON SHA-256).
PR25_GEOMETRY_CONTRACT_SHA256 = 'e9e2c74371e7b428eba58d9bb88ada348b72069b34d2f9e40cdb21f0b86cadc7'


@pytest.fixture
def source(tmp_path):
    return build(tmp_path, b'0 0 5 5 re f', triple('fractional-clip'))


@pytest.fixture
def inside(source):
    """A geometry review where every candidate passes (REGION is empty and inside the clip)."""
    return review_continuation_geometry(source, 2, REGION['bounds'])


def request(found, ident, **identities):
    return build_continuation_boundary_confirmation_request(found, boundary_id=ident, **{**IDENTITIES, **identities})


def chosen(found, test):
    """The one boundary the test itself picks with an explicit criterion."""
    matches = [row['boundary_id'] for row in rows(found) if test(row)]
    assert len(matches) == 1, matches
    return matches[0]


def not_minimal(found):
    """A (clipped, depth 3, compensated) candidate outside its group's minimal set, chosen by ordinal."""
    minimal = {i for g in found['groups'] for i in g['minimal_authority_review_candidates']}
    outside = [row for row in rows(found) if row['boundary_id'] not in minimal]
    assert outside
    return outside[-1]['boundary_id']


def test_a_caller_selected_passing_boundary_becomes_confirm_arguments_that_confirm_accepts(source, inside):
    ident = pick(source)['boundary_id']  # the caller's own choice: depth 3, after f, before BT
    before = deepcopy(inside)
    built = request(inside, ident)
    assert inside == before
    group = next(g for g in inside['groups'] if ident in g['boundary_ids'])
    row = next(r for r in group['candidates'] if r['boundary_id'] == ident)
    assert set(built) == {'schema', 'page', 'bounds', 'review_program_sha256', 'group_id', 'boundary_id',
                          'candidate_review', 'confirm_kwargs', 'contract'}
    assert built['schema'] == REQUEST_SCHEMA
    assert (built['page'], built['bounds'], built['review_program_sha256'], built['group_id'], built['boundary_id']) \
        == (inside['page'], inside['bounds'], inside['program_sha256'], group['group_id'], ident)
    assert built['candidate_review'] == dict(ordinal=row['ordinal'], operator_context=row['operator_context'],
        q_depth=3, review_requirements=row['review_requirements'], review_attributes=row['review_attributes'],
        geometry=row['geometry'])
    assert built['confirm_kwargs'] == dict(IDENTITIES, page=2, bounds=REGION['bounds'], insertion=BOUNDARY,
                                           graphics_state=BOUNDARY_STATE, boundary=ident)
    assert 'page_entry_order' not in built['confirm_kwargs']
    assert built['confirm_kwargs']['bounds'] is not inside['bounds']
    # The test, not the builder, confirms.
    destination = confirm_continuation_destination(source, **built['confirm_kwargs'])
    assert destination['authority']['boundary_id'] == ident and destination['bounds'] == REGION['bounds']
    assert destination['source_program_sha256'] == built['review_program_sha256']


def test_a_passing_boundary_outside_the_minimal_set_is_requestable(source, inside):
    ident = not_minimal(inside)
    built = request(inside, ident)
    assert built['boundary_id'] == ident
    assert all(ident not in g['minimal_authority_review_candidates'] for g in inside['groups'])
    assert confirm_continuation_destination(source, **built['confirm_kwargs'])['authority']['boundary_id'] == ident


def test_a_failing_geometry_is_refused_distinguishing_emptiness_from_clip(source):
    occupied = review_continuation_geometry(source, 2, ON_PAINT)
    below = review_continuation_geometry(source, 2, BELOW_CLIP)
    free = chosen(below, lambda r: r['q_depth'] == 0 and r['operator_context'] == 'f -> boundary -> q')
    clipped = pick(source)['boundary_id']
    with pytest.raises(PdfError, match='not empty page space'):
        request(occupied, free)
    with pytest.raises(PdfError, match='outside its inherited clip'):
        request(below, clipped)
    # The same bounds leave a clip-free candidate requestable.
    assert request(below, free)['candidate_review']['geometry']['checks_passed'] is True


@pytest.mark.parametrize('ident, message', [
    (None, 'must select a boundary_id explicitly'), ('', 'not a boundary ID'), (7, 'not a boundary ID'),
    ('boundary-' + '0' * 24, 'not a candidate of this geometry review'), ('boundary-XYZ', 'not a boundary ID')])
def test_the_boundary_is_required_and_must_be_one_of_the_review(inside, ident, message):
    with pytest.raises(PdfError, match=message):
        request(inside, ident)
    with pytest.raises(PdfError, match='select a boundary_id explicitly'):
        build_continuation_boundary_confirmation_request(inside, **IDENTITIES)


def test_a_group_id_is_refused(inside):
    group = next(g for g in inside['groups'] if g['candidate_count'] > 1)
    with pytest.raises(PdfError, match='group ID is not a boundary ID'):
        request(inside, group['group_id'])


@pytest.mark.parametrize('name', ['destination_id', 'paragraph_id', 'region_id'])
@pytest.mark.parametrize('value', [None, '', 1, b'id', ['id']])
def test_identities_are_non_empty_strings(source, inside, name, value):
    with pytest.raises(PdfError, match='identities are required'):
        request(inside, pick(source)['boundary_id'], **{name: value})


def _row(found, index=0):
    return found['groups'][index]['candidates'][0]


def _multi(found):
    return next(g for g in found['groups'] if g['candidate_count'] > 1)


def _duplicate(found):
    group = _multi(found)
    group['candidates'][1]['boundary_id'] = group['boundary_ids'][1] = group['boundary_ids'][0]


TAMPERING = {
    'structural review': lambda f: f.update(schema=review.REVIEW_SCHEMA),
    'extra key': lambda f: f.update(extra=1),
    'automatic selection': lambda f: f['contract'].update(automatic_selection=True),
    'automatic confirmation': lambda f: f['contract'].update(automatic_confirmation=True),
    'recommendation': lambda f: f['contract'].update(recommendation=True),
    'generated ink evaluated': lambda f: f['contract'].update(generated_ink_evaluated=True),
    'not read-only': lambda f: f['contract'].update(read_only=False),
    'geometry unused': lambda f: f['contract'].update(geometry_used=False),
    'page zero': lambda f: f.update(page=0),
    'page bool': lambda f: f.update(page=True),
    'page text': lambda f: f.update(page='2'),
    'program': lambda f: f.update(program_sha256='x' * 64),
    'bounds nan': lambda f: f.update(bounds=[math.nan, 90, 173, 220]),
    'bounds bool': lambda f: f.update(bounds=[True, 90, 173, 220]),
    'bounds short': lambda f: f.update(bounds=[18, 90, 173]),
    'bounds inverted': lambda f: f.update(bounds=[173, 90, 18, 220]),
    'bounds tuple': lambda f: f.update(bounds=tuple(f['bounds'])),
    'summary bounds': lambda f: f['geometry'].update(bounds=[18, 90, 174, 220]),
    'top-level bounds': lambda f: f.update(bounds=[18, 90, 174, 220]),
    'summary emptiness': lambda f: f['geometry'].update(destination_empty=False),
    'error without failure': lambda f: f['geometry'].update(empty_check_error='occupied'),
    'candidate count': lambda f: f.update(candidate_count=f['candidate_count'] + 1),
    'group count': lambda f: f.update(group_count=f['group_count'] - 1),
    'group id': lambda f: f['groups'][0].update(group_id='review-group-' + '0' * 24),
    'group page': lambda f: f['groups'][0].update(page=3),
    'group order': lambda f: f['groups'].reverse(),
    'duplicate boundary': _duplicate,
    'boundary_ids order': lambda f: _multi(f)['boundary_ids'].reverse(),
    'boundary_ids missing': lambda f: _multi(f)['boundary_ids'].pop(),
    'candidate removed': lambda f: _multi(f)['candidates'].pop(),
    'geometry missing': lambda f: _row(f).pop('geometry'),
    'geometry extra': lambda f: _row(f)['geometry'].update(selected=True),
    'geometry not bool': lambda f: _row(f)['geometry'].update(checks_passed=1),
    'checks contradict': lambda f: _row(f)['geometry'].update(bounds_inside_inherited_clip=False),
    'row emptiness': lambda f: _row(f)['geometry'].update(destination_empty=False, checks_passed=False),
    'clip check flag': lambda f: _row(f)['geometry'].update(clip_check_required=not _row(f)['geometry'][
        'clip_check_required']),
    'group counts': lambda f: f['groups'][0].update(geometry_checks_passed_count=0),
    'totals': lambda f: f['geometry'].update(checks_passed_candidates=0),
    'minimal outside group': lambda f: f['groups'][0].update(minimal_authority_review_candidates=['boundary-' + 'a' * 24]),
    'nan anywhere': lambda f: _row(f).update(ordinal=math.nan),
}


@pytest.mark.parametrize('name', sorted(TAMPERING))
def test_malformed_or_tampered_reviews_fail_closed(source, inside, name):
    ident = pick(source)['boundary_id']
    request(inside, ident)
    tampered = deepcopy(inside)
    TAMPERING[name](tampered)
    with pytest.raises(PdfError, match='malformed continuation geometry review'):
        request(tampered, ident)


def test_the_builder_is_pure_and_never_confirms_or_reads_the_source(source, inside, monkeypatch):
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    ident = pick(source)['boundary_id']
    for owner, name in ((destinations, 'confirm_continuation_destination'), (destinations, '_boundary_authority'),
                        (destinations, 'require_empty'), (destinations, 'require_inside_clip'),
                        (destinations, 'inspect_continuation_boundaries'), (destinations, 'ContentPage'),
                        (review, 'ContentPage'), (review, '_inspect'), (review, 'require_empty'),
                        (review, 'clip_contains'), (review, 'inspect_continuation_boundaries')):
        monkeypatch.setattr(owner, name, lambda *a, **k: pytest.fail(f'the builder called {name}'))
    built = request(inside, ident)
    assert built['contract'] == REQUEST_CONTRACT and built['contract'] is not REQUEST_CONTRACT
    contract = built['contract']
    assert contract['read_only'] is contract['caller_selected_boundary'] is True
    assert (contract['automatic_selection'] is contract['recommendation'] is contract['safety_ranking']
            is contract['automatic_confirmation'] is contract['source_revalidated']
            is contract['generated_ink_evaluated'] is False)
    for misleading in ('ready', 'safe_to_write', 'valid_final_destination', 'authority', 'destination'):
        assert misleading not in built
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_existing_review_apis_are_unchanged(source, inside):
    plain = review_continuation_boundaries(source, 2)
    assert plain['contract'] == REVIEW_CONTRACT and review._digest(REVIEW_CONTRACT) == PR23_REVIEW_CONTRACT_SHA256
    assert inside['contract'] == GEOMETRY_CONTRACT
    assert review._digest(GEOMETRY_CONTRACT) == PR25_GEOMETRY_CONTRACT_SHA256
    assert structural(inside) == plain['groups']
    assert set(inside) == set(review.GEOMETRY_KEYS) and set(inside['geometry']) == set(review.GEOMETRY_SUMMARY_KEYS)


def test_a_stale_review_still_builds_but_confirmation_rejects_the_changed_source(tmp_path):
    path = build(tmp_path, b'0 0 5 5 re f', triple('fractional-clip'))
    stale = review_continuation_geometry(path, 2, REGION['bounds'])
    ident = pick(path)['boundary_id']
    build(tmp_path, b'0 0 5 5 re f', triple('fractional-clip', source='before'))  # a new revision, same path
    built = request(stale, ident)
    assert built['review_program_sha256'] == stale['program_sha256']
    assert review_continuation_boundaries(path, 2)['program_sha256'] != built['review_program_sha256']
    with pytest.raises(PdfError, match='not a safe page-level candidate'):
        confirm_continuation_destination(path, **built['confirm_kwargs'])
