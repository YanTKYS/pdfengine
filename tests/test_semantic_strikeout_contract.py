"""Semantic strikeout contract (§31): read-only design evidence. No strikeout runtime.

Everything named `*_v4` is a prototype in this file only. It fixes the contract the next
(implementation) PR must meet, on top of the Windows-validated semantic underline (version 3):

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

Prototypes reuse the production functions wherever the contract does not change them
(`semantic_paint.paint_group`, `body_grammar`, `remap` membership, `rectangles` line splitting) and
are shown equal to them on underline-only input. Inputs are the stored sidecar, the current asset and
the page frame; PDFs are only written to `tmp_path` copies. `pdfeditor/` is not modified.
"""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path

import pymupdf
import pytest
from uniseg.graphemecluster import grapheme_cluster_boundaries

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
KINDS_V3 = frozenset({'underline'})
KINDS_V4 = frozenset({'underline', 'strikeout'})


def deco(kind, start, end, recipe=None, index=0):
    recipe = recipe or (STRIKE if kind == 'strikeout' else UNDERLINE)
    return dict(id=f'current:{index}', kind=kind, start=start, end=end, start_affinity='outside',
                end_affinity='outside', recipe=dict(recipe))


def numbered(*items):
    """Canonical decorations from (kind, start, end[, recipe]) tuples."""
    return paint.renumber([deco(*item) for item in items])


# ---------------------------------------------------------------- prototype (version 4 contract)


def recipe_v4(value, kind):
    """Kind-specific exact bounds; the recipe keys are the version 3 keys."""
    if not isinstance(value, dict) or set(value) != paint.RECIPE_KEYS:
        raise PdfError('decoration recipe needs exactly offset_em and thickness_em')
    offset, thickness = paint._exact(value['offset_em']), paint._exact(value['thickness_em'])
    if not 0 < thickness <= 1:
        raise PdfError('decoration thickness is outside its exact bounds')
    if kind == 'underline' and not 0 <= offset <= 1:
        raise PdfError('underline offset must satisfy 0 <= offset_em <= 1')
    if kind == 'strikeout' and not -1 <= offset < 0:
        raise PdfError('strikeout offset must satisfy -1 <= offset_em < 0')
    return offset, thickness


def validate_v4(text, decorations, kinds=KINDS_V4):
    """`semantic_paint.validate` with a version-dependent kind set and kind-specific recipe bounds."""
    if not isinstance(decorations, list):
        raise PdfError('decorations must be an explicit list')
    limits = {0, len(text), *grapheme_cluster_boundaries(text)}
    previous = 0
    for index, d in enumerate(decorations):
        if not isinstance(d, dict) or set(d) != paint.KEYS:
            raise PdfError('decoration needs exactly id, kind, start, end, affinities and recipe')
        if d['id'] != f'current:{index}':
            raise PdfError('decoration id is not its current canonical position')
        if d['kind'] not in kinds:
            raise PdfError('unsupported decoration kind')
        if d['start_affinity'] != 'outside' or d['end_affinity'] != 'outside':
            raise PdfError('v1 boundary affinity is outside/outside')
        s, e = d['start'], d['end']
        if (type(s) is not int or type(e) is not int or not 0 <= s < e <= len(text)
                or s not in limits or e not in limits):
            raise PdfError('decoration range must be ordered grapheme boundaries inside the text')
        if not paint.visible(text[s:e]):
            raise PdfError('decoration range has no visible character')
        if s < previous:
            raise PdfError('decorations overlap or are not in canonical order')
        previous = e
        recipe_v4(d['recipe'], d['kind'])


def remap_v4(text, decorations, start, end, inserted):
    """The production §27.7 membership rule, unchanged and kind-independent; only validation differs."""
    shift = len(inserted) - (end - start)
    after = text[:start] + inserted + text[end:]
    kept = []
    for d in decorations:
        s, e = d['start'], d['end']
        members = [i for i in range(s, e) if i < start] + [i + shift for i in range(s, e) if i >= end]
        inside = (s <= start and end <= e) if start < end else s < start < e
        if inside:
            members += range(start, start + len(inserted))
        if not members:
            continue
        lo, hi = min(members), max(members) + 1
        if paint.visible(after[lo:hi]):
            kept.append(dict(deepcopy(d), start=lo, end=hi))
    result = paint.renumber(kept)
    validate_v4(after, result)
    return result


def reinterpret_v4(text, decorations, action, kinds=KINDS_V4):
    """The production E actions (add/remove/recipe, (id, start, end) CAS) with version 4 validation."""
    if not isinstance(action, dict) or len(action) != 1:
        raise PdfError('exactly one decoration action per request')
    (verb, item), = action.items()
    current = deepcopy(decorations)
    if verb == 'add':
        if not isinstance(item, dict) or set(item) != paint.ADD_KEYS:
            raise PdfError('decoration add needs exactly kind, start, end, affinities and recipe')
        current.append(dict(deepcopy(item), id='new'))
    elif verb in ('remove', 'recipe'):
        keys = paint.REFERENCE_KEYS | ({'recipe'} if verb == 'recipe' else set())
        if not isinstance(item, dict) or set(item) != keys:
            raise PdfError('decoration reference needs exactly id, start and end')
        match = [d for d in current if d['id'] == item['id']]
        if not match or (match[0]['start'], match[0]['end']) != (item['start'], item['end']):
            raise PdfError('decoration reference is not the current decoration at that id and range')
        if verb == 'remove':
            current.remove(match[0])
        elif item['recipe'] == match[0]['recipe']:
            raise PdfError('recipe change must differ from the current recipe')
        else:
            match[0]['recipe'] = deepcopy(item['recipe'])  # validated against the referenced kind below
    else:
        raise PdfError('unsupported decoration action: ' + str(verb))
    result = paint.renumber(current)
    validate_v4(text, result, kinds)
    return result


def rectangles_v4(plan, style, decorations):
    """`semantic_paint.rectangles` with `recipe_v4`; returns (kind, glyph baseline, rect) per rectangle."""
    size = F(style['font_size'])
    items = []
    for d in decorations:
        offset, thickness = (value * size for value in recipe_v4(d['recipe'], d['kind']))
        for line in plan['lines']:
            glyphs = [g for g in plan['emitted']
                      if line['start'] <= g['offset'] < line['end'] and d['start'] <= g['offset'] < d['end']]
            while glyphs and glyphs[0]['text'] == ' ':
                glyphs.pop(0)
            while glyphs and glyphs[-1]['text'] == ' ':
                glyphs.pop()
            if not glyphs:
                continue
            baselines = {F(g['origin'][1]) for g in glyphs}
            if len(baselines) != 1:
                raise PdfError('decoration line has more than one glyph baseline')
            baseline = baselines.pop()
            upper = baseline + offset
            left = F(glyphs[0]['origin'][0])
            right = F(glyphs[-1]['origin'][0]) + F(glyphs[-1]['advance'])
            if not (F(line['baseline']) - F(line['ascent']) <= upper
                    and upper + thickness <= F(line['baseline']) + F(line['descent'])):
                raise PdfError('decoration exceeds its line box')
            if not left < right:
                raise PdfError('decoration has no positive width')
            items.append((d['kind'], baseline, (left, upper, right, upper + thickness)))
    return items


def paint_group_v4(items, fill, top):
    """The production serializer, after the physical kind-separation rule.

    A strikeout's serialized upper edge must be strictly above its serialized glyph baseline. An
    underline (offset ≥ 0) is at or below it by construction (the decimal policy is monotone), so an
    underline and a strikeout never serialize the same rectangle.
    """
    d, top = island.decimal, F(top)
    for kind, baseline, (_, upper, _, _) in items:
        if kind == 'strikeout' and not F(d(top - upper)) > F(d(top - baseline)):
            raise PdfError('strikeout is not physically above its baseline after the output decimal policy')
    return paint.paint_group([rect for _, _, rect in items], fill, top)


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
    items = rectangles_v4(rev['plan'], rev['payload']['style'], decorations)
    return paint_group_v4(items, rev['fill'], rev['top'])


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


def test_current_runtime_refuses_strikeout_everywhere(bundles, tmp_path):
    """Implementation-before state: no strikeout is accepted by any production path today."""
    with pytest.raises(PdfError, match='unsupported decoration kind'):
        paint.validate('A B', [deco('strikeout', 0, 1)])
    with pytest.raises(PdfError, match='exact bounds'):
        paint.recipe(STRIKE)
    for name in ('v2', 'v3'):
        b = bundles[name]
        before = (Path(b['pdf']).read_bytes(), Path(b['sidecar']).read_bytes())
        with pytest.raises(PdfError, match='unsupported decoration kind'):
            semantic.plan_semantic_transition(b['pdf'], b['sidecar'], add('strikeout', 0, 1))
        with pytest.raises(PdfError, match='unsupported decoration kind'):
            writer.build_semantic_candidate(b['pdf'], b['sidecar'], add('strikeout', 0, 1), workspace=tmp_path)
        assert (Path(b['pdf']).read_bytes(), Path(b['sidecar']).read_bytes()) == before
    assert not any(tmp_path.iterdir())
    slot = deepcopy(json.loads(Path(bundles['v3']['sidecar']).read_text())['slots']['slot-0'])
    slot['semantic']['version'] = 4
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
    with pytest.raises(PdfError, match='strikeout offset'):  # the recipe is checked against the referenced kind
        reinterpret_v4(text, two, {'recipe': dict(id='current:1', start=3, end=4, recipe=dict(UNDERLINE))})
    with pytest.raises(PdfError, match='underline offset'):
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
    ({'add': dict(kind='strikeout', start=3, end=4, start_affinity='outside', end_affinity='outside', recipe=dict(UNDERLINE))}, 'strikeout offset'),
    ({'add': dict(kind='underline', start=3, end=4, start_affinity='outside', end_affinity='outside', recipe=dict(STRIKE))}, 'underline offset'),
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
