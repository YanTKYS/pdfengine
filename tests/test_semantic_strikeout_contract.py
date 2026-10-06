"""Semantic strikeout contract (§31), now run against the production runtime (§32).

PR #53 fixed the contract with in-file prototypes; §32 implements it in the existing
`semantic_paint` / `semantic_layout` / `semantic_writer`, so the `*_v4` names below are thin
aliases of the production functions (no second implementation). The contract:

* versioning: semantic record version 4 = version 3 + `kind = strikeout`; version 3 keeps
  `underline` only; the first explicit `decorations.add` of a strikeout (from version 2 or 3) is the
  only route to version 4; nothing is upgraded on open/save and nothing is downgraded;
* schema: the version 3 decoration record unchanged, `kind in {underline, strikeout}`, the same recipe
  keys with kind-specific offset bounds (underline 0 ≤ offset ≤ 1, strikeout −1 ≤ offset < 0);
* geometry: the §27.8 rule unchanged (`upper = glyph baseline + size·offset_em`, page y-down), so a
  negative offset is above the baseline; font tables are never authority;
* physical body: the §27.3 rectangle grammar and the one canonical serializer, text group first,
  one paint group, decorations in canonical order then lines;
* mixed kinds: one sorted, disjoint decoration list across kinds (touching allowed, overlap refused).

`next_version` stays a contract table; `test_semantic_strikeout.py` checks the production planner
against every row. Inputs are the stored sidecar, the current asset and the page frame; PDFs are only
written to `tmp_path` copies. End-to-end lifecycle evidence lives in `test_semantic_strikeout.py`.
"""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path

import pymupdf
import pytest

from pdfeditor import semantic_island as island
from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_paint as paint
from pdfeditor import semantic_writer as writer
from pdfeditor import source_ownership as owned
from pdfeditor.backend import PdfError
from pdfeditor.composition import _check_obstacles, _pixels_equal
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.editable import _seal
from pdfeditor.elements import inspect_element
from pdfeditor.paint_provenance import _load_catalog, interpreted_paints, prove_path_paint
from pdfeditor.selection import make_selection, resolve_selection
from test_paint_reassessment import chain  # noqa: F401  (module fixture reused)
from test_semantic_underline_contract import as_rects, derive, inks, painted, revision

UNDERLINE = {'offset_em': '13/120', 'thickness_em': '7/120'}   # the §27/§28 validation recipe
STRIKE = {'offset_em': '-3/10', 'thickness_em': '1/20'}        # caller-confirmed evidence recipe (not a standard)
KINDS_V3 = paint.UNDERLINE_KINDS
KINDS_V4 = paint.MIXED_KINDS


def deco(kind, start, end, recipe=None, index=0):
    recipe = recipe or (STRIKE if kind == 'strikeout' else UNDERLINE)
    return dict(id=f'current:{index}', kind=kind, start=start, end=end, start_affinity='outside',
                end_affinity='outside', recipe=dict(recipe))


def numbered(*items):
    """Canonical decorations from (kind, start, end[, recipe]) tuples."""
    return paint.renumber([deco(*item) for item in items])


# ---------------------------------------------------------------- production (version 4), §32
# The §31 prototypes are replaced by the production functions they specified; only `next_version`
# stays a contract-level table here and is compared with the production planner in
# `test_semantic_strikeout.py`.


def recipe_v4(value, kind):
    return paint.recipe(value, kind)


def validate_v4(text, decorations, kinds=KINDS_V4):
    return paint.validate(text, decorations, kinds)


def remap_v4(text, decorations, start, end, inserted):
    return paint.remap(text, decorations, start, end, inserted, KINDS_V4)


def reinterpret_v4(text, decorations, action, kinds=KINDS_V4):
    return paint.reinterpret(text, decorations, action, kinds)


def rectangles_v4(plan, style, decorations):
    """(kind, glyph baseline, rect) per rectangle (the production rectangle items)."""
    return paint._items(plan, style, decorations)


def next_version(version, request):
    """Version rule: a pure function of the current version and the explicit request; open/save keep."""
    if request['operation'] != 'reinterpret' or 'decorations' not in request.get('changes', {}):
        return version
    if version == 1:
        raise PdfError('a decoration request needs semantic version 2, 3 or 4; confirm version 1 as version 2 first')
    (verb, item), = request['changes']['decorations'].items()
    if verb == 'add':
        if item.get('kind') == 'strikeout':
            return 4
        return 3 if version in (2, 3) else 4      # an underline add never moves a record to version 4
    if version == 2:
        raise PdfError('semantic version 2 has no decoration to reference')
    return version


def canonical(rev, decorations):
    return paint.serialize(rev['plan'], rev['payload']['style'], decorations, rev['fill'], rev['top'])


def rects(rev, decorations):
    return [r for _, _, r in rectangles_v4(rev['plan'], rev['payload']['style'], decorations)]


def add(kind, start, end, recipe=None, **extra):
    item = dict(kind=kind, start=start, end=end, start_affinity='outside', end_affinity='outside',
                recipe=dict(recipe or (STRIKE if kind == 'strikeout' else UNDERLINE)))
    item.update(extra)
    return dict(operation='reinterpret', changes={'decorations': {'add': item}})


# ---------------------------------------------------------------- fixtures: production v2 / v3 bundles


@pytest.fixture(scope='module')
def bundles(chain, tmp_path_factory):
    """Production version 2 (save3) and version 3 (explicit underline add) bundles; read-only afterwards."""
    root = tmp_path_factory.mktemp('strikeout-contract')
    v2 = chain['save3']
    result = writer.build_semantic_candidate(v2['pdf'], v2['sidecar'], add('underline', 0, 3), workspace=root)
    v3 = dict(pdf=result['pdf'], sidecar=result['sidecar'])
    assert semantic.open_semantic_flow(v3['pdf'], v3['sidecar'])['version'] == 3
    return dict(v2=v2, v3=v3)


# ---------------------------------------------------------------- 1. the current runtime refuses strikeout


def test_production_accepts_strikeout_only_through_version_4(bundles):
    """§32 implemented state (was the implementation-before probe of PR #53): the version 3 defaults
    still refuse a strikeout; a strikeout add on version 2 or 3 plans version 4; version 5 is unknown."""
    with pytest.raises(PdfError, match='unsupported decoration kind'):
        paint.validate('A B', [deco('strikeout', 0, 1)])  # the version 3 kind set is the default
    with pytest.raises(PdfError, match='exact bounds'):
        paint.recipe(STRIKE)  # the default kind is underline
    paint.validate('A B', [deco('strikeout', 0, 1)], paint.MIXED_KINDS)
    v2, v3 = bundles['v2'], bundles['v3']
    plan = semantic.plan_semantic_transition(v2['pdf'], v2['sidecar'], add('strikeout', 0, 1))
    assert (plan['version'], plan['next_version'], plan['classification']) == (2, 4, 'E')
    with pytest.raises(PdfError, match='overlap'):  # the v3 → v4 route keeps one disjoint list ([0,3) is underlined)
        semantic.plan_semantic_transition(v3['pdf'], v3['sidecar'], add('strikeout', 0, 1))
    slot = deepcopy(json.loads(Path(bundles['v3']['sidecar']).read_text())['slots']['slot-0'])
    slot['semantic']['version'] = 4
    semantic._record(slot)
    slot['semantic']['version'] = 5
    with pytest.raises(PdfError, match='no valid semantic payload'):
        semantic._record(slot)


def test_version_3_cannot_absorb_a_strikeout(bundles):
    """Backward compatibility: a resealed v3 sidecar claiming a strikeout, or an underline recipe above the
    baseline, is refused by the unchanged v3 runtime; v3 keeps its Windows-validated meaning."""
    pdf, value = bundles['v3']['pdf'], json.loads(Path(bundles['v3']['sidecar']).read_text())

    def resealed(change):
        v = deepcopy(value)
        v.pop('model_sha256')
        change(v['slots']['slot-0']['semantic']['payload']['decorations'][0])
        return _seal(v)
    as_strikeout = semantic.open_semantic_flow(pdf, resealed(lambda d: d.update(kind='strikeout', recipe=dict(STRIKE))))
    assert as_strikeout['status'] == 'needs_confirmation' and 'unsupported decoration kind' in as_strikeout['reason']
    above = semantic.open_semantic_flow(pdf, resealed(lambda d: d.update(recipe=dict(STRIKE))))
    assert above['status'] == 'needs_confirmation' and 'exact bounds' in above['reason']


# ---------------------------------------------------------------- 2. versioning


@pytest.mark.parametrize('version, request_, expected', [
    (1, add('strikeout', 0, 1), 'REFUSE'),
    (1, add('underline', 0, 1), 'REFUSE'),
    (2, add('underline', 0, 1), 3),
    (2, add('strikeout', 0, 1), 4),
    (2, dict(operation='reinterpret', changes={'decorations': {'remove': dict(id='current:0', start=0, end=1)}}), 'REFUSE'),
    (3, add('underline', 2, 3), 3),
    (3, dict(operation='reinterpret', changes={'decorations': {'remove': dict(id='current:0', start=0, end=1)}}), 3),
    (3, add('strikeout', 2, 3), 4),
    (4, add('underline', 2, 3), 4),
    (4, add('strikeout', 2, 3), 4),
    (4, dict(operation='reinterpret', changes={'decorations': {'remove': dict(id='current:0', start=0, end=1)}}), 4),
    *[(v, dict(operation=op), v) for v in (1, 2, 3, 4) for op in ('reopen', 'save')],
    *[(v, dict(operation='edit', start=0, end=0, text='A'), v) for v in (1, 2, 3, 4)],
])
def test_version_migration_matrix(version, request_, expected):
    if expected == 'REFUSE':
        with pytest.raises(PdfError):
            next_version(version, request_)
    else:
        assert next_version(version, request_) == expected


@pytest.mark.parametrize('name, version, request_', [
    ('v2', 2, add('underline', 0, 1)), ('v2', 2, dict(operation='save')),
    ('v2', 2, dict(operation='edit', start=0, end=0, text='A')),
    ('v3', 3, dict(operation='reinterpret', changes={'decorations': {'remove': dict(id='current:0', start=0, end=3)}})),
    ('v3', 3, dict(operation='save')), ('v3', 3, dict(operation='edit', start=1, end=1, text='A')),
])
def test_matrix_rows_that_exist_today_match_the_production_plan(bundles, name, version, request_):
    """The v2/v3 underline rows of the matrix are the shipped behaviour, unchanged by version 4."""
    b = bundles[name]
    plan = semantic.plan_semantic_transition(b['pdf'], b['sidecar'], request_)
    assert plan['version'] == version and plan['next_version'] == next_version(version, request_)


def test_strikeout_from_version_2_needs_no_underline_first():
    """v2 → v4 by the first explicit strikeout add: the same mechanics as v2 → v3, decided by the kind."""
    text = 'A B'
    added = reinterpret_v4(text, [], {'add': dict(kind='strikeout', start=0, end=3, start_affinity='outside',
                                                  end_affinity='outside', recipe=dict(STRIKE))})
    assert added == [deco('strikeout', 0, 3)] and next_version(2, add('strikeout', 0, 3)) == 4
    # The same state reached from version 3 (underline removed first) is the same version 4 payload.
    assert next_version(3, add('strikeout', 0, 3)) == 4


# ---------------------------------------------------------------- 3. schema and recipe bounds


def test_strikeout_reuses_the_version_3_schema_and_recipe_keys():
    validate_v4('AA B', [deco('strikeout', 0, 4)])
    assert set(deco('strikeout', 0, 4)) == paint.KEYS and set(STRIKE) == paint.RECIPE_KEYS
    for extra in ('color', 'z_index', 'source_id'):
        with pytest.raises(PdfError, match='exactly id, kind'):
            validate_v4('AA B', [dict(deco('strikeout', 0, 4), **{extra: 1})])
    for kind in ('highlight', 'overline', 'Strikeout', None):
        with pytest.raises(PdfError, match='unsupported decoration kind'):
            validate_v4('AA B', [dict(deco('strikeout', 0, 4), kind=kind)])
    with pytest.raises(PdfError, match='unsupported decoration kind'):
        validate_v4('AA B', [deco('strikeout', 0, 4)], kinds=KINDS_V3)  # version 3 stays underline-only


@pytest.mark.parametrize('kind, offset, thickness, ok', [
    ('underline', '0', '1/20', True), ('underline', '1', '1/20', True), ('underline', '-1/10', '1/20', False),
    ('strikeout', '-1', '1/20', True), ('strikeout', '-3/10', '1/20', True), ('strikeout', '-1/1000', '1/20', True),
    ('strikeout', '0', '1/20', False), ('strikeout', '1/10', '1/20', False), ('strikeout', '-11/10', '1/20', False),
    ('strikeout', '-3/10', '0', False), ('strikeout', '-3/10', '11/10', False), ('strikeout', '-3/10', '-1/20', False),
    ('strikeout', '-0.3', '1/20', False), ('strikeout', '-6/20', '1/20', False),
])
def test_kind_specific_recipe_bounds(kind, offset, thickness, ok):
    recipe = {'offset_em': offset, 'thickness_em': thickness}
    if ok:
        recipe_v4(recipe, kind)
    else:
        with pytest.raises(PdfError):
            recipe_v4(recipe, kind)
    with pytest.raises(PdfError, match='offset_em and thickness_em'):
        recipe_v4('auto', kind)
    with pytest.raises(PdfError, match='offset_em and thickness_em'):
        recipe_v4({'offset_em': offset}, kind)


def test_underline_only_prototype_equals_production(chain):
    """Reuse: on underline-only input the prototype is the shipped validation, geometry and remap."""
    rev = revision(chain, 'edit_noop')
    decorations = numbered(('underline', 0, 1), ('underline', 3, 4))
    paint.validate('AA B', decorations)
    validate_v4('AA B', decorations)
    assert rects(rev, decorations) == paint.rectangles(rev['plan'], rev['payload']['style'], decorations)
    assert canonical(rev, decorations) == paint.paint_group(
        paint.rectangles(rev['plan'], rev['payload']['style'], decorations), rev['fill'], rev['top'])
    text = 'AA B'
    for s in range(4):
        for e in range(s + 1, 5):
            if not paint.visible(text[s:e]):
                continue
            for a in range(5):
                for b in range(a, 5):
                    for inserted in ('', 'B', 'A '):
                        d = [deco('underline', s, e)]
                        assert remap_v4(text, d, a, b, inserted) == paint.remap(text, d, a, b, inserted)


# ---------------------------------------------------------------- 4. geometry, line box, physical separation


def test_negative_offset_is_above_the_baseline_and_crosses_the_glyphs(chain):
    rev = revision(chain, 'save3')  # 'A B', 12 pt, baseline 200 (page y-down)
    (kind, baseline, (left, upper, right, lower)), = rectangles_v4(rev['plan'], rev['payload']['style'],
                                                                   [deco('strikeout', 0, 3)])
    assert (baseline, upper, lower) == (200, 200 - F(18, 5), 200 - 3)  # 3.6 pt and 3.0 pt above the baseline
    assert (left, right) == (20, F(208, 5))
    strike = as_rects([(left, upper, right, lower)])[0]
    assert all(strike.intersects(ink) for ink in inks(rev))  # it crosses A and B (no space ink)
    assert canonical(rev, [deco('strikeout', 0, 3)]) == b'q 0 g\n20 63.6 m 41.6 63.6 l 41.6 63 l 20 63 l h f\nQ\n'


def test_line_box_is_exact_for_negative_offsets(chain):
    rev = revision(chain, 'save3')  # line ascent 0.6 em, descent 0.2 em
    for size in ('12', '37/3', '24'):
        plan, payload = derive(rev, font_size=size)
        rectangles_v4(plan, payload['style'], [deco('strikeout', 0, 3, {'offset_em': '-3/5', 'thickness_em': '1/20'})])
        with pytest.raises(PdfError, match='line box'):   # top overflow
            rectangles_v4(plan, payload['style'], [deco('strikeout', 0, 3, {'offset_em': '-31/50', 'thickness_em': '1/20'})])
        rectangles_v4(plan, payload['style'], [deco('strikeout', 0, 3, {'offset_em': '-1/20', 'thickness_em': '1/4'})])
        with pytest.raises(PdfError, match='line box'):   # bottom overflow from a strikeout
            rectangles_v4(plan, payload['style'], [deco('strikeout', 0, 3, {'offset_em': '-1/20', 'thickness_em': '3/10'})])


def test_strikeout_and_underline_never_serialize_the_same_rectangle(chain):
    rev = revision(chain, 'save3')
    with pytest.raises(PdfError, match='not physically above its baseline'):
        canonical(rev, [deco('strikeout', 0, 3, {'offset_em': '-1/1000000000', 'thickness_em': '1/20'})])
    under = canonical(rev, [deco('underline', 0, 3, {'offset_em': '0', 'thickness_em': '1/20'})])
    strike = canonical(rev, [deco('strikeout', 0, 3, {'offset_em': '-1/1000', 'thickness_em': '1/20'})])
    assert under != strike
    top = F(rev['top'])
    for o in ('-3/5', '-3/10', '-1/20', '-1/1000'):
        (_, base, r), = rectangles_v4(rev['plan'], rev['payload']['style'], [deco('strikeout', 0, 3, {'offset_em': o, 'thickness_em': '1/20'})])
        assert F(island.decimal(top - r[1])) > F(island.decimal(top - base))
    for o in ('0', '1/20', '1/10'):
        (_, base, r), = rectangles_v4(rev['plan'], rev['payload']['style'], [deco('underline', 0, 3, {'offset_em': o, 'thickness_em': '1/20'})])
        assert F(island.decimal(top - r[1])) <= F(island.decimal(top - base))


# ---------------------------------------------------------------- 5. style, font, rise, scale, Tw/edge, lines


def test_vertical_geometry_follows_size_and_rise_only(chain):
    base, scaled = revision(chain, 'edit_noop'), revision(chain, 'scaled')  # 12 → 37/3 pt, scale 4/5, tracking 1/4
    for rev in (base, scaled):
        size = F(rev['payload']['style']['font_size'])
        (_, baseline, (_, upper, _, lower)), = rectangles_v4(rev['plan'], rev['payload']['style'], [deco('strikeout', 0, 4)])
        assert (upper - baseline, lower - upper) == (F(-3, 10) * size, F(1, 20) * size)
    plan, payload = derive(base, rise='-1')
    (_, b1, (_, u1, _, l1)), = rectangles_v4(plan, payload['style'], [deco('strikeout', 0, 4)])
    (_, b0, (_, u0, _, l0)), = rectangles_v4(base['plan'], base['payload']['style'], [deco('strikeout', 0, 4)])
    assert (b1 - b0, u1 - u0, l1 - l0) == (-1, -1, -1)


def test_endpoints_follow_tracking_tw_edges_and_font(chain):
    width = lambda units, size, scale=F(1): F(units, 1000) * size * scale  # noqa: E731
    size, scale, track = F(37, 3), F(4, 5), F(1, 4)
    a = width(600, size, scale)
    expected = {'scaled': 20 + 3 * (a + track) + a, 'tw': 20 + 3 * (a + track) + F(6, 5) + a,
                'edge': 20 + 3 * (a + track) + F(6, 5) + a,
                'font_b': 20 + 3 * (width(500, size, scale) + track) + F(6, 5) + width(500, size, scale)}
    for name, right in expected.items():
        (r,) = rects(revision(chain, name), [deco('strikeout', 0, 4)])
        assert (r[0], r[2]) == (20, right), name
    tw, edge = revision(chain, 'tw'), revision(chain, 'edge')
    for ranges in ([('strikeout', 0, 4)], [('strikeout', 0, 1), ('underline', 3, 4)], [('underline', 0, 2), ('strikeout', 2, 4)]):
        assert canonical(tw, numbered(*ranges)) == canonical(edge, numbered(*ranges))
    before, after = revision(chain, 'edge_noop'), revision(chain, 'font_b')  # font A → B: vertical unchanged
    (r0,), (r1,) = rects(before, [deco('strikeout', 0, 4)]), rects(after, [deco('strikeout', 0, 4)])
    assert (r0[1], r0[3]) == (r1[1], r1[3]) and r0[2] != r1[2]


def test_newline_and_wrap_split_one_strikeout_into_line_rectangles(chain):
    rev = revision(chain, 'save3')
    plan, payload = derive(rev, text='AB\nBA ')
    items = rectangles_v4(plan, payload['style'], [deco('strikeout', 1, 6)])
    assert len(items) == 2 and items[0][2][1] < items[1][2][1]
    assert items[0][2][0] == 20 + F(36, 5) and items[1][2][2] == 20 + 2 * F(36, 5)  # newline and trailing space not painted


@pytest.mark.parametrize('group', [
    ('save1', 'save2', 'save3'), ('edit', 'edit_noop'), ('scaled', 'scaled_noop1', 'scaled_noop2'),
    ('tw', 'tw_noop'), ('edge', 'edge_noop'), ('font_b', 'font_b_noop'),
])
def test_canonical_bytes_are_a_noop_fixed_point(chain, group):
    def bodies(name):
        rev = revision(chain, name)
        n = len(rev['payload']['text'])
        return [canonical(rev, d) for d in ([deco('strikeout', 0, n)], [deco('underline', 0, n)],
                                            numbered(('underline', 0, 1), ('strikeout', n - 1, n)))]
    results = [bodies(name) for name in group]
    assert all(r == results[0] for r in results)


# ---------------------------------------------------------------- 6. mixed kinds, overlap, touching, D, E, R


def test_mixed_disjoint_kinds_share_one_group_in_range_order(chain):
    rev = revision(chain, 'edit_noop')  # 'AA B'
    decorations = numbered(('strikeout', 3, 4), ('underline', 0, 1))
    assert [(d['id'], d['kind'], d['start']) for d in decorations] == [('current:0', 'underline', 0),
                                                                       ('current:1', 'strikeout', 3)]
    validate_v4('AA B', decorations)
    group = canonical(rev, decorations)
    names = [op.name for op in operators(group)]
    assert names.count('q') == 1 and names.count('f') == 2 and names[1] == 'g'
    first, second = rects(rev, decorations)
    assert first[0] < second[0] and first[1] > 200 and second[1] < 200  # underline below, strikeout above


@pytest.mark.parametrize('ranges', [
    [('underline', 0, 3), ('strikeout', 0, 3)],   # same range, different kinds
    [('underline', 0, 3), ('strikeout', 2, 4)],   # partial overlap
    [('strikeout', 0, 4), ('underline', 1, 2)],   # nesting
])
def test_overlap_is_refused_across_kinds(ranges):
    with pytest.raises(PdfError, match='overlap'):
        validate_v4('AA B', numbered(*ranges))
    first, *rest = ranges
    current = numbered(first)
    for kind, a, b in rest:
        with pytest.raises(PdfError, match='overlap'):
            reinterpret_v4('AA B', current, {'add': dict(kind=kind, start=a, end=b, start_affinity='outside',
                                                         end_affinity='outside', recipe=dict(STRIKE if kind == 'strikeout' else UNDERLINE))})


def test_touching_kinds_stay_separate(chain):
    rev = revision(chain, 'edit_noop')
    decorations = numbered(('underline', 0, 1), ('strikeout', 1, 2))
    validate_v4('AA B', decorations)
    (u, s) = rects(rev, decorations)
    assert u[2] == s[0] and len(rects(rev, decorations)) == 2  # adjacent, never merged


def test_d_remap_is_kind_independent():
    text = 'AA B'
    for s in range(4):
        for e in range(s + 1, 5):
            if not paint.visible(text[s:e]):
                continue
            for a in range(5):
                for b in range(a, 5):
                    for inserted in ('', 'B', 'A '):
                        strike = remap_v4(text, [deco('strikeout', s, e)], a, b, inserted)
                        under = paint.remap(text, [deco('underline', s, e)], a, b, inserted)
                        assert [(d['start'], d['end']) for d in strike] == [(d['start'], d['end']) for d in under]
                        assert all(d['kind'] == 'strikeout' and d['recipe'] == STRIKE for d in strike)
    mixed = remap_v4(text, numbered(('underline', 0, 1), ('strikeout', 3, 4)), 2, 2, 'B')
    assert [(d['id'], d['kind'], d['start'], d['end']) for d in mixed] == [('current:0', 'underline', 0, 1),
                                                                           ('current:1', 'strikeout', 4, 5)]
    assert remap_v4(text, numbered(('underline', 0, 2), ('strikeout', 3, 4)), 0, 4, '') == []  # empty: all terminate


def test_e_add_remove_recipe_reuse_the_same_actions():
    text = 'AA B'
    one = reinterpret_v4(text, [], {'add': dict(kind='strikeout', start=3, end=4, start_affinity='outside',
                                               end_affinity='outside', recipe=dict(STRIKE))})
    two = reinterpret_v4(text, one, {'add': dict(kind='underline', start=0, end=2, start_affinity='outside',
                                                end_affinity='outside', recipe=dict(UNDERLINE))})
    assert [(d['id'], d['kind']) for d in two] == [('current:0', 'underline'), ('current:1', 'strikeout')]
    lower = {'offset_em': '-1/4', 'thickness_em': '1/20'}
    changed = reinterpret_v4(text, two, {'recipe': dict(id='current:1', start=3, end=4, recipe=lower)})
    assert changed[1]['recipe'] == lower and changed[1]['kind'] == 'strikeout'
    with pytest.raises(PdfError, match='strikeout recipe is outside'):  # the recipe is checked against the referenced kind
        reinterpret_v4(text, two, {'recipe': dict(id='current:1', start=3, end=4, recipe=dict(UNDERLINE))})
    with pytest.raises(PdfError, match='underline recipe is outside'):
        reinterpret_v4(text, two, {'recipe': dict(id='current:0', start=0, end=2, recipe=dict(STRIKE))})
    removed = reinterpret_v4(text, changed, {'remove': dict(id='current:0', start=0, end=2)})
    assert [(d['id'], d['kind'], d['start']) for d in removed] == [('current:0', 'strikeout', 3)]


@pytest.mark.parametrize('action, reason', [
    ({'add': dict(kind='highlight', start=3, end=4, start_affinity='outside', end_affinity='outside', recipe=dict(STRIKE))}, 'unsupported decoration kind'),
    ({'add': dict(kind='strikeout', start=3, end=4, start_affinity='outside', end_affinity='outside', recipe=dict(STRIKE), id='current:9')}, 'exactly kind'),
    ({'add': dict(kind='strikeout', start=3, end=4, start_affinity='inside', end_affinity='outside', recipe=dict(STRIKE))}, 'outside/outside'),
    ({'add': dict(kind='strikeout', start=1, end=3, start_affinity='outside', end_affinity='outside', recipe=dict(STRIKE))}, 'overlap'),
    ({'add': dict(kind='strikeout', start=3, end=4, start_affinity='outside', end_affinity='outside', recipe=dict(STRIKE), source_id='p1')}, 'exactly kind'),
    ({'adopt': dict(source_id='p1')}, 'unsupported decoration action'),
    ({'add': dict(kind='strikeout', start=3, end=4, start_affinity='outside', end_affinity='outside', recipe='auto')}, 'offset_em and thickness_em'),
    ({'add': dict(kind='strikeout', start=3, end=4, start_affinity='outside', end_affinity='outside')}, 'exactly kind'),
    ({'add': dict(kind='strikeout', start=3, end=4, start_affinity='outside', end_affinity='outside', recipe=dict(UNDERLINE))}, 'strikeout recipe is outside'),
    ({'add': dict(kind='underline', start=3, end=4, start_affinity='outside', end_affinity='outside', recipe=dict(STRIKE))}, 'underline recipe is outside'),
    ({'add': dict(kind='strikeout', start=2, end=3, start_affinity='outside', end_affinity='outside', recipe=dict(STRIKE))}, 'no visible character'),
    ({'remove': dict(id='current:0', start=0, end=3)}, 'not the current decoration'),   # stale CAS
    ({'revive': dict(id='current:0')}, 'unsupported decoration action'),
])
def test_r_refusals(action, reason):
    current = reinterpret_v4('AA B', [], {'add': dict(kind='strikeout', start=0, end=2, start_affinity='outside',
                                                      end_affinity='outside', recipe=dict(STRIKE))})
    with pytest.raises(PdfError, match=reason):
        reinterpret_v4('AA B', current, action)


def test_line_box_violation_is_an_r_refusal(chain):
    rev = revision(chain, 'save3')
    with pytest.raises(PdfError, match='line box'):
        canonical(rev, [deco('strikeout', 0, 3, {'offset_em': '-7/10', 'thickness_em': '1/20'})])


# ---------------------------------------------------------------- 7. physical body, z-order, ownership, Transaction


def test_production_v3_grammar_and_serializer_carry_a_strikeout_group(chain, tmp_path):
    rev = revision(chain, 'save3')
    group = canonical(rev, [deco('strikeout', 0, 3)])
    assert paint.body_grammar(rev['body'] + group) == (rev['body'], group)
    with pytest.raises(PdfError, match='source output body grammar'):
        owned.grammar(rev['body'] + group)  # the v2 grammar stays text-only
    pdf = painted(rev, tmp_path / 'strike.pdf', group)
    content = ContentPage(pdf, 1)
    try:
        span = owned.inventory(content.streams[-content.page.xref])[rev['marker']]
        witness = owned.witness(content, rev['slot']['source_output'], span, body_grammar=paint.body_grammar)
    finally:
        content.close()
    assert set(witness) == set(rev['slot']['source_output']['current'])  # the unchanged owner witness shape
    assert witness['entry_context_sha256'] == rev['slot']['source_output']['current']['entry_context_sha256']


def test_text_then_strikeout_is_pixel_equivalent_and_keeps_the_text_prefix(chain, tmp_path):
    rev = revision(chain, 'save3')
    group = canonical(rev, [deco('strikeout', 0, 3)])
    text_first = painted(rev, tmp_path / 'text-first.pdf', group)
    paint_first = painted(rev, tmp_path / 'paint-first.pdf', group, first=True)
    pixels = lambda pdf: pymupdf.open(pdf)[0].get_pixmap(dpi=144, alpha=False).samples  # noqa: E731
    assert pixels(text_first) == pixels(paint_first)  # one opaque fill colour: source-over commutes
    assert pixels(text_first) != pixels(rev['pdf'])   # the strikeout is visible
    with pymupdf.open(text_first) as one, pymupdf.open(paint_first) as two:
        trace = lambda page: [(c[0], tuple(round(v, 4) for v in c[2])) for s in page.get_texttrace() for c in s['chars']]  # noqa: E731
        assert trace(one[0]) == trace(two[0])
    with pymupdf.open(text_first) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    _, start, end, _ = owned.inventory(data)[rev['marker']]
    assert data[start:end] == rev['body'] + group
    with pytest.raises(PdfError):  # paint-first is not the v3/v4 body grammar's order
        paint.body_grammar(group + rev['body'])


def test_owned_strikeout_paths_are_proven_one_to_one(chain, tmp_path):
    rev = revision(chain, 'edit_noop')
    decorations = numbered(('underline', 0, 1), ('strikeout', 3, 4))
    pdf = painted(rev, tmp_path / 'mixed.pdf', canonical(rev, decorations),
                  before=b' q 0 0 1 rg 100 50 10 5 re f Q', after=b'q 1 0 0 rg 120 50 10 5 re f Q\n')
    catalog, _ = _load_catalog(str(pdf), 1)
    with pymupdf.open(pdf) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    _, start, end, _ = owned.inventory(data)[rev['marker']]
    events = interpreted_paints(str(pdf), 1)['events']
    inside = []
    for path in sorted(catalog['paths'], key=lambda p: p['merged_range'][0]):
        proof = prove_path_paint(str(pdf), 1, path['id'], catalog=catalog)
        assert proof['status'] == 'proven' and len(proof['paint_indices']) == 1
        a, b = path['merged_range']
        if start <= a and b <= end:
            inside.append([round(v, 3) for v in events[proof['paint_indices'][0]]['bounds']])
    assert inside == [[round(float(v), 3) for v in r] for r in rects(rev, decorations)]
    element = inspect_element(pdf, make_selection(pdf, glyph_ids=[0, 1, 2, 3], explicit_width=150))
    owned_paths = [p for p in element['paths'] if start <= p['source']['merged_range'][0] < end]
    assert len(owned_paths) == 2  # classified by the owner (count/order/bounds), never by the inferred hint


def test_transaction_obstacles_reuse_for_strikeout(chain, tmp_path):
    rev = revision(chain, 'save3')
    glyph_inks = inks(rev)
    bounds = glyph_inks[0]
    for ink in glyph_inks[1:]:
        bounds = bounds.union(ink)
    strike = as_rects(rects(rev, [deco('strikeout', 0, 3)]))

    def check(pdf, ink, exclude=frozenset()):
        selection = make_selection(pdf, glyph_ids=[0, 1, 2], explicit_width=150)
        content = ContentPage(pdf, 1)
        try:
            _check_obstacles(content, {0, 1, 2}, resolve_selection(pdf, selection), ink, bounds,
                             exclude_paint_seqnos=exclude)
        finally:
            content.close()
    check(rev['pdf'], glyph_inks + strike)  # crossing the island's own glyphs is not an obstacle
    old = painted(rev, tmp_path / 'old.pdf', canonical(rev, [deco('strikeout', 0, 3)]))
    with pytest.raises(PdfError, match='filled vector'):
        check(old, glyph_inks)  # its own old strikeout crosses the new glyph ink
    seqnos = frozenset(e['seqno'] for e in interpreted_paints(str(old), 1)['events'] if e['kind'] == 'fill-path')
    check(old, glyph_inks, seqnos)  # excluded only by its proven seqno
    # Foreign paint in the inter-glyph gap: no glyph ink hits it, the new strikeout does.
    foreign = painted(rev, tmp_path / 'foreign.pdf', b'', before=b' q 0 0 1 rg 29 63.2 3 0.3 re f Q')
    check(foreign, glyph_inks)
    with pytest.raises(PdfError, match='filled vector'):
        check(foreign, glyph_inks + strike)  # foreign paint under a new strikeout stays an obstacle


PAIRS = [  # (old revision, old decorations, new revision, new decorations): affected = inks ∪ old ∪ new rectangles
    ('save3', [('strikeout', 0, 3)], 'edit', [('strikeout', 0, 4)]),
    ('edit_noop', [('strikeout', 0, 4)], 'scaled', [('strikeout', 0, 4)]),
    ('edge_noop', [('strikeout', 0, 4)], 'font_b', [('strikeout', 0, 4)]),
    ('save3', [('strikeout', 0, 3)], 'save3', []),
    ('save3', [], 'save3', [('strikeout', 0, 3)]),
    ('save3', [('strikeout', 0, 3)], 'save3', [('strikeout', 0, 3, {'offset_em': '-1/4', 'thickness_em': '1/20'})]),
    ('save3', [('underline', 0, 1)], 'save3', [('underline', 0, 1), ('strikeout', 2, 3)]),
]


@pytest.mark.parametrize('old_name, old_d, new_name, new_d', PAIRS)
def test_old_and_new_rectangles_bound_every_changed_pixel(chain, tmp_path, old_name, old_d, new_name, new_d):
    old, new = revision(chain, old_name), revision(chain, new_name)
    od, nd = numbered(*old_d) if old_d else [], numbered(*new_d) if new_d else []
    old_rects, new_rects = as_rects(rects(old, od)), as_rects(rects(new, nd))
    a = painted(old, tmp_path / 'old.pdf', canonical(old, od))
    b = painted(new, tmp_path / 'new.pdf', canonical(new, nd))
    glyphs = inks(old) + inks(new)
    with pymupdf.open(a) as first, pymupdf.open(b) as second:
        assert _pixels_equal(first[0], second[0], glyphs + old_rects + new_rects)
