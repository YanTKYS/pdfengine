"""Narrow semantic strikeout runtime (§32): semantic record version 4 on the single owned slot.

Production evidence for the §31 contract through the real planner, writer, Transaction, verifier and
publication: v2 → v4 and v3 → v4 by an explicit strikeout `add`, v4 lifecycles (strikeout only and
mixed with underline), the full §31.2 migration matrix, kind-aware recipes, the physical separation
rule, overlap/touching, N/D/E/R, tampering, own-glyph crossing and foreign paint, raster, failure
isolation and v1/v2/v3 compatibility. Nothing here adopts source paint.
"""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_paint as paint
from pdfeditor import semantic_publication as publication
from pdfeditor import semantic_writer as writer
from pdfeditor import source_ownership as owned
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import operators
from test_semantic_authority import alternate_font
from test_semantic_layout import make_flow, statement
from test_semantic_strikeout_contract import next_version as contract_next_version
from test_semantic_underline import (_foreign_flow, _nontext, _rebound, _replace_body, body, candidates, files,
                                     opened, pixels, resealed, split, state)

ROOT = Path(__file__).resolve().parents[1]
UNDERLINE = {'offset_em': '13/120', 'thickness_em': '7/120'}
STRIKE = {'offset_em': '-3/10', 'thickness_em': '1/20'}
LOWER = {'offset_em': '-1/4', 'thickness_em': '1/20'}
EDGE = dict(left=2, right=3, delta='6/5', operator='TJ', semantics='confirmed-adjacent-pair',
            boundary_policy='suppress-at-line-end')


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def item(kind, start, end, recipe=None, **extra):
    value = dict(kind=kind, start=start, end=end, start_affinity='outside', end_affinity='outside',
                 recipe=deepcopy(recipe or (STRIKE if kind == 'strikeout' else UNDERLINE)))
    value.update(extra)
    return value


def add(kind, start, end, recipe=None, **extra):
    return dict(operation='reinterpret', changes={'decorations': {'add': item(kind, start, end, recipe, **extra)}})


def remove(index, start, end):
    return dict(operation='reinterpret', changes={'decorations': {'remove': dict(id=f'current:{index}', start=start,
                                                                                  end=end)}})


def recipe_change(index, start, end, recipe):
    return dict(operation='reinterpret', changes={'decorations': {'recipe': dict(
        id=f'current:{index}', start=start, end=end, recipe=dict(recipe))}})


def save():
    return dict(operation='save')


def edit(start, end, text):
    return dict(operation='edit', start=start, end=end, text=text)


def reinterpret(**changes):
    return dict(operation='reinterpret', changes=changes)


@pytest.fixture(scope='module')
def so(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic-strikeout')
    _, asset = make_flow(root / 'main')
    main = root / 'main'
    font_b = root / 'font-b.ttf'
    font_b.write_bytes(alternate_font())
    v2 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0',
                                          semantic=statement(asset))
    (main / 'v2.json').write_text(json.dumps(v2))
    steps = {'confirmed': (main / 'rev1.pdf', main / 'v2.json')}
    plans = {}
    order = [
        ('base', 'confirmed', save(), None),                               # v2 text-only 'A B'
        # Lifecycle A: v2 → v4 by an explicit strikeout add
        ('s_add', 'base', add('strikeout', 0, 3), None),
        ('s_noop1', 's_add', save(), None), ('s_noop2', 's_noop1', save(), None),
        ('s_edit', 's_noop2', edit(1, 1, 'A'), None),                      # 'AA B': [0,3) → [0,4)
        ('s_edit_noop', 's_edit', save(), None),
        ('s_recipe', 's_edit_noop', recipe_change(0, 0, 4, LOWER), None),
        ('s_style', 's_edit_noop', reinterpret(font_size='37/3', horizontal_scale='4/5', tracking='1/4'), None),
        ('s_style_noop', 's_style', save(), None),
        ('s_rise', 's_edit_noop', reinterpret(rise='-1'), None),
        ('s_tw', 's_style_noop', reinterpret(word_spacing='6/5'), None),
        ('s_edge', 's_tw', reinterpret(word_spacing='0', edges=[EDGE]), None),
        ('s_font', 's_edge', reinterpret(font=sha(font_b)), font_b),
        ('s_font_noop', 's_font', save(), None), ('s_font_noop2', 's_font_noop', save(), None),
        ('s_removed', 's_add', remove(0, 0, 3), None),                     # stays v4, text-only body
        ('s_removed_save', 's_removed', save(), None),
        ('newline', 'base', edit(1, 2, '\n'), None),
        ('s_newline', 'newline', add('strikeout', 0, 3), None),
        # Lifecycle B: v3 underline → v4 by a disjoint strikeout add
        ('u_add', 'base', add('underline', 0, 1), None),
        ('b_strike', 'u_add', add('strikeout', 2, 3), None),
        ('b_noop', 'b_strike', save(), None),
        ('b_edit', 'b_noop', edit(1, 1, 'A'), None),                       # underline kept [0,1), strikeout → [3,4)
        ('b_remove_strike', 'b_edit', remove(1, 3, 4), None),              # underline-only, still v4
        ('b_save', 'b_remove_strike', save(), None),
        # Mixed lifecycle on 'AA B': touching, remove each kind, add again
        ('m_text', 'base', edit(1, 1, 'A'), None),
        ('m_u', 'm_text', add('underline', 0, 1), None),
        ('m_touch', 'm_u', add('strikeout', 1, 2), None),
        ('m_touch_noop', 'm_touch', save(), None),
        ('m_tw', 'm_touch', reinterpret(word_spacing='6/5'), None),
        ('m_edge', 'm_tw', reinterpret(word_spacing='0', edges=[EDGE]), None),
        ('m_rm_u', 'm_touch', remove(0, 0, 1), None),
        ('m_readd_u', 'm_rm_u', add('underline', 0, 1), None),
        ('m_rm_s', 'm_readd_u', remove(1, 1, 2), None),
        ('m_readd_s', 'm_rm_s', add('strikeout', 3, 4), None),
    ]
    for name, parent, request, supplied in order:
        plans[name] = semantic.plan_semantic_transition(*steps[parent], request, asset=supplied)
        result = writer.build_semantic_candidate(*steps[parent], request, workspace=root / 'work', asset=supplied)
        steps[name] = (result['pdf'], result['sidecar'])
    return dict(root=root, main=main, asset=asset, font_b=font_b, steps=steps, plans=plans)


def version(so, name):
    return opened(so, name)['version']


def kinds(so, name):
    return [(d['kind'], d['start'], d['end']) for d in opened(so, name)['semantic'].get('decorations', [])]


def rects(so, name):
    value = opened(so, name)
    sidecar = state(so, name)
    slot = sidecar['slots']['slot-0']
    font = semantic._asset(sidecar, slot, value['semantic'], value['authority'])
    try:
        _, plan = semantic._derive(sidecar, slot, value['semantic'], font)
    finally:
        font.font.close()
    return paint._items(plan, value['semantic']['style'], value['semantic'].get('decorations', []))


# ---------------------------------------------------------------- versions and routes


def test_v2_to_v4_by_an_explicit_strikeout_add(so):
    plan = so['plans']['s_add']
    assert (plan['classification'], plan['version'], plan['next_version']) == ('E', 2, 4)
    assert set(plan['diff']) == {'decorations'} and plan['next_authority'] == plan['authority']
    assert plan['next_decoration_rectangles'] == 1 and plan['next_underline_rectangles'] == 0
    assert version(so, 'base') == 2 and version(so, 's_add') == 4
    assert opened(so, 's_add')['semantic']['decorations'] == [dict(item('strikeout', 0, 3), id='current:0')]
    assert split(body(so, 's_add')) == (body(so, 'base'), b'q 0 g\n20 63.6 m 41.6 63.6 l 41.6 63 l 20 63 l h f\nQ\n')


def test_v3_to_v4_carries_the_underline_exactly(so):
    plan = so['plans']['b_strike']
    assert (plan['version'], plan['next_version']) == (3, 4) and version(so, 'u_add') == 3
    under = opened(so, 'u_add')['semantic']['decorations'][0]
    mixed = opened(so, 'b_strike')['semantic']['decorations']
    assert mixed[0] == under and mixed[1] == dict(item('strikeout', 2, 3), id='current:1')
    group = split(body(so, 'b_strike'))[1]
    assert group == (b'q 0 g\n20 58.7 m 27.2 58.7 l 27.2 58 l 20 58 l h f\n'
                     b'34.4 63.6 m 41.6 63.6 l 41.6 63 l 34.4 63 l h f\nQ\n')  # one group, current:i order
    assert split(body(so, 'u_add'))[0] == split(body(so, 'b_strike'))[0]


def test_version_4_is_never_left_or_downgraded(so):
    for name in ('s_removed', 's_removed_save', 'b_remove_strike', 'b_save', 'm_rm_s', 's_edit', 's_style', 's_font'):
        assert version(so, name) == 4, name
    assert kinds(so, 's_removed') == [] and body(so, 's_removed') == body(so, 'base')
    assert kinds(so, 'b_save') == [('underline', 0, 1)] and split(body(so, 'b_save'))[1].count(b' f\n') == 1
    with pytest.raises(PdfError, match='starts from a shared-flow v2 owner sidecar'):
        semantic.confirm_semantic_layout(*so['steps']['s_add'], slot_id='slot-0', semantic=statement(so['asset']))


def _v1(so, tmp_path):
    value = state(so, 'base')
    v1 = semantic._attach(value, 'slot-0', value['slots']['slot-0']['semantic']['payload'])
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps(v1))
    return so['steps']['base'][0], path


MATRIX = [  # (bundle, request, expected next version or REFUSE)
    ('v1', add('strikeout', 0, 1), 'REFUSE'), ('v1', add('underline', 0, 1), 'REFUSE'),
    ('v2', add('underline', 0, 1), 3), ('v2', add('strikeout', 0, 1), 4), ('v2', remove(0, 0, 1), 'REFUSE'),
    ('v2', recipe_change(0, 0, 1, STRIKE), 'REFUSE'),
    ('v3', add('underline', 3, 4), 3), ('v3', remove(0, 0, 1), 3), ('v3', recipe_change(0, 0, 1, LOWER), 'REFUSE'),
    ('v3', recipe_change(0, 0, 1, {'offset_em': '1/10', 'thickness_em': '1/20'}), 3),
    ('v3', add('strikeout', 3, 4), 4), ('v3', remove(1, 3, 4), 'REFUSE'),
    ('v4', add('underline', 3, 4), 4), ('v4', add('strikeout', 3, 4), 4), ('v4', remove(1, 1, 2), 4),
    ('v4', remove(0, 0, 1), 4), ('v4', recipe_change(1, 1, 2, LOWER), 4),
    *[(v, request, int(v[1])) for v in ('v1', 'v2', 'v3', 'v4')
      for request in (dict(operation='reopen'), save(), edit(0, 0, 'A'), reinterpret(font_size='13'))],
]


@pytest.mark.parametrize('bundle, request_, expected', MATRIX)
def test_full_migration_matrix_through_the_production_planner(so, tmp_path, bundle, request_, expected):
    """§31.2 on production bundles: v1 (legacy), v2 'AA B', v3 'AA B' + underline [0,1), v4 touching."""
    pdf, sidecar = {'v1': lambda: _v1(so, tmp_path), 'v2': lambda: so['steps']['m_text'],
                    'v3': lambda: so['steps']['m_u'], 'v4': lambda: so['steps']['m_touch']}[bundle]()
    number = int(bundle[1])
    if expected == 'REFUSE':
        with pytest.raises(PdfError):
            semantic.plan_semantic_transition(pdf, sidecar, request_)
        return
    plan = semantic.plan_semantic_transition(pdf, sidecar, request_)
    assert (plan['version'], plan['next_version']) == (number, expected)
    if request_['operation'] == 'reinterpret' and 'decorations' in request_['changes']:
        assert plan['next_version'] == contract_next_version(number, request_)


def test_version_3_stays_underline_only(so):
    pdf, value = so['steps']['u_add'][0], state(so, 'u_add')

    def as_strikeout(v):
        d = v['slots']['slot-0']['semantic']['payload']['decorations'][0]
        d.update(kind='strikeout', recipe=dict(STRIKE))
    reason = semantic.open_semantic_flow(pdf, resealed(value, as_strikeout))['reason']
    assert 'unsupported decoration kind' in reason
    for request in (save(), edit(1, 1, 'A'), add('underline', 2, 3)):
        assert semantic.plan_semantic_transition(*so['steps']['u_add'], request)['next_version'] == 3


def test_version_1_refuses_strikeout(so, tmp_path):
    pdf, path = _v1(so, tmp_path)
    assert semantic.open_semantic_flow(pdf, path)['version'] == 1
    with pytest.raises(PdfError, match='confirm version 1 as version 2 first'):
        semantic.plan_semantic_transition(pdf, path, add('strikeout', 0, 1))


# ---------------------------------------------------------------- geometry, separation, style, font


def test_strikeout_geometry_is_above_the_baseline_and_crosses_the_glyphs(so):
    (kind, baseline, (left, upper, right, lower)), = rects(so, 's_add')
    assert (kind, baseline, left, upper, right, lower) == ('strikeout', 200, 20, 200 - F(18, 5), F(208, 5), 197)


def test_physical_separation_is_enforced_before_any_write(so, tmp_path):
    tiny = {'offset_em': '-1/1000000000', 'thickness_em': '1/20'}
    before = files(*so['steps']['base'])
    with pytest.raises(PdfError, match='not physically above its baseline'):
        semantic.plan_semantic_transition(*so['steps']['base'], add('strikeout', 0, 3, tiny))
    with pytest.raises(PdfError, match='not physically above its baseline'):
        writer.build_semantic_candidate(*so['steps']['base'], add('strikeout', 0, 3, tiny), workspace=tmp_path)
    assert files(*so['steps']['base']) == before and not any(tmp_path.iterdir())
    plan = semantic.plan_semantic_transition(*so['steps']['base'], add('strikeout', 0, 3, {'offset_em': '-1/1000',
                                                                                         'thickness_em': '1/20'}))
    assert plan['next_version'] == 4


def test_style_rise_scale_and_font_follow_the_em_recipe(so):
    (_, b, (_, u, _, l)), = rects(so, 's_style')  # 37/3 pt, scale 4/5, tracking 1/4
    assert (u - b, l - u) == (F(-3, 10) * F(37, 3), F(1, 20) * F(37, 3))
    (_, b0, (_, u0, _, l0)), = rects(so, 's_edit_noop')
    (_, b1, (_, u1, _, l1)), = rects(so, 's_rise')
    assert (b1 - b0, u1 - u0, l1 - l0) == (-1, -1, -1)
    (_, _, before), = rects(so, 's_edge')
    (_, _, after), = rects(so, 's_font')
    assert (before[1], before[3]) == (after[1], after[3]) and before[2] != after[2]
    assert opened(so, 's_font')['semantic']['decorations'][0]['recipe'] == STRIKE
    (_, _, recipe), = rects(so, 's_recipe')
    (_, _, edited), = rects(so, 's_edit_noop')
    assert (recipe[0], recipe[2]) == (edited[0], edited[2]) and recipe[1] != edited[1]


def test_tw_and_edge_give_identical_bytes_for_strikeout_and_mixed_bodies(so):
    assert split(body(so, 's_tw'))[1] == split(body(so, 's_edge'))[1]
    assert split(body(so, 'm_tw'))[1] == split(body(so, 'm_edge'))[1]


def test_newline_splits_a_strikeout_per_line(so):
    items = rects(so, 's_newline')
    assert len(items) == 2 and items[0][2][1] < items[1][2][1]
    assert split(body(so, 's_newline'))[1].count(b' f\n') == 2


# ---------------------------------------------------------------- mixed, touching, overlap, D, E, R


def test_mixed_lifecycle_touching_remove_and_add_again(so):
    assert kinds(so, 'm_touch') == [('underline', 0, 1), ('strikeout', 1, 2)]
    (u, s) = [r for _, _, r in rects(so, 'm_touch')]
    assert u[2] == s[0] and u[1] > 200 > s[1]  # touching, never merged; underline below, strikeout above
    assert kinds(so, 'm_rm_u') == [('strikeout', 1, 2)]
    assert kinds(so, 'm_readd_u') == [('underline', 0, 1), ('strikeout', 1, 2)]
    assert body(so, 'm_readd_u') == body(so, 'm_touch')
    assert kinds(so, 'm_rm_s') == [('underline', 0, 1)] and kinds(so, 'm_readd_s') == [('underline', 0, 1),
                                                                                      ('strikeout', 3, 4)]
    assert [opened(so, n)['version'] for n in ('m_u', 'm_touch', 'm_rm_u', 'm_rm_s', 'm_readd_s')] == [3, 4, 4, 4, 4]


def test_edit_remaps_mixed_kinds_independently(so):
    assert kinds(so, 's_edit') == [('strikeout', 0, 4)]
    assert kinds(so, 'b_edit') == [('underline', 0, 1), ('strikeout', 3, 4)]
    assert so['plans']['b_edit']['classification'] == 'D' and so['plans']['b_edit']['next_version'] == 4
    plan = semantic.plan_semantic_transition(*so['steps']['m_touch'], edit(0, 4, ''))
    assert plan['next']['decorations'] == [] and plan['next_version'] == 4


@pytest.mark.parametrize('request_, reason', [
    (add('highlight', 3, 4), 'unsupported decoration kind'),
    (add('strikeout', 3, 4, id='current:7'), 'exactly kind'),
    (add('strikeout', 3, 4, start_affinity='inside'), 'outside/outside'),
    (add('strikeout', 0, 1), 'overlap'),            # same range as the underline
    (add('underline', 1, 2), 'overlap'),            # same range as the strikeout
    (add('strikeout', 0, 4), 'overlap'),            # nesting
    (add('strikeout', 1, 3), 'overlap'),            # partial
    (add('strikeout', 3, 4, source_id='path-1'), 'exactly kind'),
    (dict(operation='reinterpret', changes={'decorations': {'adopt': dict(source_id='p')}}), 'unsupported decoration action'),
    (dict(operation='reinterpret', changes={'decorations': {'revive': dict(id='current:0')}}), 'unsupported decoration action'),
    (add('strikeout', 3, 4, recipe='auto'), 'offset_em and thickness_em'),
    (add('strikeout', 3, 4, recipe={'offset_em': '-3/10'}), 'offset_em and thickness_em'),
    (add('strikeout', 3, 4, recipe=UNDERLINE), 'strikeout recipe is outside its exact bounds'),
    (add('underline', 3, 4, recipe=STRIKE), 'underline recipe is outside its exact bounds'),
    (add('strikeout', 3, 4, recipe={'offset_em': '-3/10', 'thickness_em': '11/10'}), 'exact bounds'),
    (add('strikeout', 3, 4, recipe={'offset_em': '-7/10', 'thickness_em': '1/20'}), 'line box'),
    (add('strikeout', 3, 4, recipe={'offset_em': '-1/1000000000', 'thickness_em': '1/20'}), 'physically above'),
    (add('strikeout', 2, 3), 'no visible character'),
    (remove(1, 1, 3), 'not the current decoration'),                       # stale CAS
    (recipe_change(1, 1, 2, UNDERLINE), 'strikeout recipe is outside'),    # recipe checked against the kind
    (recipe_change(0, 0, 1, STRIKE), 'underline recipe is outside'),
    (dict(operation='reinterpret', changes={'decorations': {'add': item('strikeout', 3, 4)}, 'font_size': '13'}), 'its own'),
])
def test_r_refusal_matrix_through_planner_and_writer(so, tmp_path, request_, reason):
    pdf, sidecar = so['steps']['m_touch']  # 'AA B': underline [0,1), strikeout [1,2)
    before = files(pdf, sidecar)
    with pytest.raises(PdfError, match=reason):
        semantic.plan_semantic_transition(pdf, sidecar, request_)
    with pytest.raises(PdfError, match=reason):
        writer.build_semantic_candidate(pdf, sidecar, request_, workspace=tmp_path)
    assert files(pdf, sidecar) == before and not any(tmp_path.iterdir())


def test_v3_strikeout_reference_is_refused(so):
    with pytest.raises(PdfError, match='not the current decoration'):
        semantic.plan_semantic_transition(*so['steps']['m_u'], remove(1, 1, 2))


# ---------------------------------------------------------------- no-op, tamper


@pytest.mark.parametrize('group', [('s_add', 's_noop1', 's_noop2'), ('s_edit', 's_edit_noop'), ('s_style', 's_style_noop'),
                                   ('s_font', 's_font_noop', 's_font_noop2'), ('b_strike', 'b_noop'),
                                   ('m_touch', 'm_touch_noop'),
                                   ('s_removed', 's_removed_save'), ('b_remove_strike', 'b_save')])
def test_noop_is_a_canonical_fixed_point(so, group):
    first = group[0]
    for name in group[1:]:
        # PDF bytes: the first save after a font change compacts the PDF once (also without decorations,
        # pre-existing); from then on the no-op PDF is byte-identical.
        reference = group[1] if first == 's_font' else first
        if name != reference:
            assert files(so['steps'][name][0]) == files(so['steps'][reference][0])
        assert body(so, name) == body(so, first)
        assert [(o.name, o.args) for o in operators(body(so, name))] == [(o.name, o.args) for o in operators(body(so, first))]
        a, b = state(so, first)['slots']['slot-0'], state(so, name)['slots']['slot-0']
        assert a['semantic']['version'] == b['semantic']['version']
        for key in ('payload', 'current', 'derived'):
            assert a['semantic'][key] == b['semantic'][key]
        assert a['source_output']['current']['block_sha256'] == b['source_output']['current']['block_sha256']
        assert rects(so, name) == rects(so, first) and pixels(so['steps'][name][0]) == pixels(so['steps'][first][0])


@pytest.mark.parametrize('name, change', [
    ('kind swap', lambda d: d[1].update(kind='underline')),
    ('recipe sign', lambda d: d[1]['recipe'].update(offset_em='3/10')),
    ('recipe value', lambda d: d[1]['recipe'].update(offset_em='-1/4')),
    ('kind order', lambda d: d.reverse()),
    ('range', lambda d: d[1].update(start=0)),
    ('missing', lambda d: d.pop()),
    ('extra', lambda d: d.append(dict(item('strikeout', 3, 4), id='current:2'))),
])
def test_resealed_v4_semantic_tamper_is_refused(so, name, change):
    pdf = so['steps']['m_touch'][0]

    def tamper(v):
        change(v['slots']['slot-0']['semantic']['payload']['decorations'])
    assert semantic.open_semantic_flow(pdf, resealed(state(so, 'm_touch'), tamper))['status'] == 'needs_confirmation'


def test_v4_to_v3_relabel_is_refused(so):
    def as_v3(v):
        v['slots']['slot-0']['semantic']['version'] = 3
    reason = semantic.open_semantic_flow(so['steps']['m_touch'][0], resealed(state(so, 'm_touch'), as_v3))['reason']
    assert 'unsupported decoration kind' in reason


MIXED = b'q 0 g\n20 58.7 m 27.2 58.7 l 27.2 58 l 20 58 l h f\n27.2 63.6 m 34.4 63.6 l 34.4 63 l 27.2 63 l h f\nQ\n'


@pytest.mark.parametrize('label, change', [
    ('geometry', lambda g: g.replace(b'34.4 63 l', b'34.4 62.9 l')),
    ('order', lambda g: b''.join([g.splitlines(keepends=True)[i] for i in (0, 2, 1, 3)])),
    ('missing', lambda g: b''.join([g.splitlines(keepends=True)[i] for i in (0, 1, 3)])),
    ('extra', lambda g: g.replace(b'Q\n', b'40 63.6 m 45 63.6 l 45 63 l 40 63 l h f\nQ\n')),
    ('kind as geometry', lambda g: g.replace(b'27.2 63.6 m 34.4 63.6 l 34.4 63 l 27.2 63 l',
                                             b'27.2 58.7 m 34.4 58.7 l 34.4 58 l 27.2 58 l')),
    ('paint first', None),
])
def test_v4_body_tamper_is_refused_by_the_production_verifier(so, tmp_path, label, change):
    text, group = split(body(so, 'm_touch'))
    assert group == MIXED
    new = group + text if change is None else text + change(group)
    pdf = _replace_body(so, 'm_touch', tmp_path / 'tampered.pdf', new)
    assert semantic.open_semantic_flow(pdf, so['steps']['m_touch'][1])['status'] == 'needs_confirmation'
    with pytest.raises((PdfError, KeyError)):
        value = _rebound(so, 'm_touch', pdf)
        slot = value['slots']['slot-0']
        payload, current = slot['semantic']['payload'], slot['semantic']['current']
        font = semantic._asset(value, slot, payload, current)
        try:
            _, plan = semantic._derive(value, slot, payload, font)
            semantic._island(pdf, value, 'slot-0', payload, plan, current, font, payload['decorations'], 4)
        finally:
            font.font.close()


def test_stale_pairs_are_refused(so):
    for pdf_name, sidecar_name in (('s_add', 'base'), ('base', 's_add'), ('b_strike', 'u_add'), ('u_add', 'b_strike'),
                                   ('m_touch', 'm_rm_u'), ('s_removed', 's_add')):
        assert semantic.open_semantic_flow(so['steps'][pdf_name][0], so['steps'][sidecar_name][1])['status'] == \
            'needs_confirmation', (pdf_name, sidecar_name)


# ---------------------------------------------------------------- Transaction, obstacles, foreign paint


@pytest.fixture
def spy(monkeypatch):
    seen = []
    real = writer._plan_island

    def plan_island(*args, **kwargs):
        result = real(*args, **kwargs)
        seen.append(result[0])
        return result
    monkeypatch.setattr(writer, '_plan_island', plan_island)
    return seen


@pytest.mark.parametrize('parent, request_, insert, changes, consumed', [
    ('base', add('strikeout', 0, 3), 1, None, 0),                   # first add: insertion
    ('s_add', save(), 0, [1], 1),                                   # replacement
    ('s_add', remove(0, 0, 3), 0, [None], 1),                       # removal
    ('u_add', add('strikeout', 2, 3), 0, [2], 1),                   # v3 → v4: old underline replaced by two paints
    ('m_touch', remove(0, 0, 1), 0, [1, None], 2),                  # mixed: two old paints, one remains
])
def test_transaction_reuse(so, spy, parent, request_, insert, changes, consumed):
    writer.build_semantic_candidate(*so['steps'][parent], request_, workspace=so['root'] / 'work')
    (plan,) = spy
    assert sum(len(v) for v in plan.paint_insertions.values()) == insert
    assert [None if v is None else len(v) for v in plan.paint_changes.values()] == (changes or [])
    assert len(plan.consumed_paths) == consumed


def test_affected_area_holds_old_and_new_rectangles(so, spy):
    writer.build_semantic_candidate(*so['steps']['m_touch'], recipe_change(1, 1, 2, LOWER), workspace=so['root'] / 'work')
    (plan,) = spy
    old = [r for _, _, r in rects(so, 'm_touch')]
    value = opened(so, 'm_touch')['semantic']
    new_state = deepcopy(value)
    new_state['decorations'][1]['recipe'] = dict(LOWER)
    sidecar = state(so, 'm_touch')
    slot = sidecar['slots']['slot-0']
    font = semantic._asset(sidecar, slot, value, opened(so, 'm_touch')['authority'])
    try:
        _, derived = semantic._derive(sidecar, slot, new_state, font)
    finally:
        font.font.close()
    for r in old + paint.rectangles(derived, new_state['style'], new_state['decorations']):
        a, b, c, d = (float(v) for v in r)
        assert plan.affected.contains(type(plan.affected)(a, b, c, d), 1e-9)


@pytest.mark.parametrize('parent, request_', [('s_add', remove(0, 0, 3)), ('base', add('strikeout', 0, 3))])
def test_planned_area_needs_the_strikeout_rectangles(so, monkeypatch, parent, request_):
    """With only the glyph-ink box as the planned area, adding or removing a strikeout is refused: the
    strikeout runs over the space between A and B, outside every glyph ink."""
    real = writer._plan_island

    def narrow(*args, **kwargs):
        plan, lines, data = real(*args, **kwargs)
        new = sum(len(v or []) for v in [*plan.paint_changes.values(), *plan.paint_insertions.values()])
        inks = plan.final_rects[:len(plan.final_rects) - new]
        area = inks[0]
        for r in inks[1:] + args[8][0]:
            area = area.union(r)
        plan.affected = area
        return plan, lines, data
    monkeypatch.setattr(writer, '_plan_island', narrow)
    with pytest.raises(PdfError, match='changed pixels outside its planned areas'):
        writer.build_semantic_candidate(*so['steps'][parent], request_, workspace=so['root'] / 'work')


def test_own_glyph_crossing_is_allowed_and_own_old_strikeout_is_excluded_only_by_seqno(so, monkeypatch):
    assert all(any(_cross(r, ink) for ink in _inks(so, 's_add')) for _, _, r in rects(so, 's_add'))
    real = writer._old_owned_paint
    monkeypatch.setattr(writer, '_old_owned_paint', lambda *a: (*real(*a)[:2], []))
    with pytest.raises(PdfError, match='filled vector'):
        writer.build_semantic_candidate(*so['steps']['s_add'], save(), workspace=so['root'] / 'work')


def _inks(so, name):
    value = opened(so, name)
    sidecar = state(so, name)
    slot = sidecar['slots']['slot-0']
    font = semantic._asset(sidecar, slot, value['semantic'], value['authority'])
    try:
        _, plan = semantic._derive(sidecar, slot, value['semantic'], font)
        return writer._ink_rects(font, value['semantic']['style'], plan['emitted'])
    finally:
        font.font.close()


def _cross(rect, ink):
    left, upper, right, lower = (float(v) for v in rect)
    return left < ink.x1 and ink.x0 < right and upper < ink.y1 and ink.y0 < lower


@pytest.fixture(scope='module')
def foreign(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic-strikeout-foreign')
    result = {}
    for name, prefix in (('gap', b'q 0 0 1 rg 29 63.2 3 .3 re f Q '), ('far', b'q 0 0 1 rg 100 50 10 5 re f Q ')):
        asset = _foreign_flow(root / name, prefix)
        main = root / name
        v2 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0',
                                              semantic=statement(asset))
        (main / 'v2.json').write_text(json.dumps(v2))
        saved = writer.build_semantic_candidate(main / 'rev1.pdf', main / 'v2.json', save(), workspace=root / 'work')
        result[name] = (saved['pdf'], saved['sidecar'])
    return dict(root=root, steps=result)


def test_foreign_gap_paint_under_a_new_strikeout_is_refused(foreign):
    pdf, sidecar = foreign['steps']['gap']
    before = files(pdf, sidecar)
    with pytest.raises(PdfError, match='filled vector'):
        writer.build_semantic_candidate(pdf, sidecar, add('strikeout', 0, 3), workspace=foreign['root'] / 'work')
    assert files(pdf, sidecar) == before
    writer.build_semantic_candidate(pdf, sidecar, save(), workspace=foreign['root'] / 'work')
    # The same gap paint is not hit by an underline (below the baseline): the add is admitted.
    writer.build_semantic_candidate(pdf, sidecar, add('underline', 0, 3), workspace=foreign['root'] / 'work')


def test_foreign_paint_is_kept_and_never_adopted(foreign):
    pdf, sidecar = foreign['steps']['far']
    before = _nontext(pdf)
    added = writer.build_semantic_candidate(pdf, sidecar, add('strikeout', 0, 3), workspace=foreign['root'] / 'work')
    assert _nontext(added['pdf'])[0] == before[0] and len(_nontext(added['pdf'])) == 2
    removed = writer.build_semantic_candidate(added['pdf'], added['sidecar'], remove(0, 0, 3),
                                              workspace=foreign['root'] / 'work')
    assert _nontext(removed['pdf']) == before
    assert 'decorations' not in semantic.open_semantic_flow(pdf, sidecar)['semantic']


# ---------------------------------------------------------------- publication, fresh reopen, raster


def test_publication_reopen_and_next_revision(so, tmp_path):
    a = publication.publish_semantic_bundle(*so['steps']['b_strike'], tmp_path / 'bundle-a')
    assert a['verification']['version'] == 4 and files(a['pdf'], a['sidecar']) == files(*so['steps']['b_strike'])
    assert pixels(a['pdf']) == pixels(so['steps']['b_strike'][0])
    a_bytes = files(a['pdf'], a['sidecar'])
    nxt = writer.build_semantic_candidate(a['pdf'], a['sidecar'], remove(1, 2, 3), workspace=tmp_path / 'work')
    b = publication.publish_semantic_bundle(nxt['pdf'], nxt['sidecar'], tmp_path / 'bundle-b')
    assert b['verification']['version'] == 4 and [d['kind'] for d in b['verification']['semantic']['decorations']] == [
        'underline']
    assert files(a['pdf'], a['sidecar']) == a_bytes
    assert semantic.open_semantic_flow(a['pdf'], b['sidecar'])['status'] == 'needs_confirmation'
    code = ('import json,sys; from pdfeditor import semantic_layout as s; r=s.open_semantic_flow(sys.argv[1], sys.argv[2]); '
            'print(json.dumps([r["status"], r.get("version"), r.get("semantic", {}).get("decorations"), '
            '(r.get("authority") or {}).get("provider", {}).get("sha256"), (r.get("owner") or {}).get("marker_id"), '
            '(r.get("island") or {}).get("canonical")]))')
    for bundle in (a, b):
        out = subprocess.run([sys.executable, '-c', code, str(bundle['pdf']), str(bundle['sidecar'])], cwd=ROOT,
                             check=True, capture_output=True, text=True).stdout
        status, version_, decorations, provider, marker, canonical = json.loads(out)
        v = bundle['verification']
        assert (status, version_, canonical) == ('restored', 4, True)
        assert decorations == v['semantic']['decorations'] and provider == v['authority']['provider']['sha256']
        assert marker == v['owner']['marker_id']


def test_mupdf_raster(so):
    base, added = pixels(so['steps']['base'][0]), pixels(so['steps']['s_add'][0])
    assert added != base
    assert pixels(so['steps']['s_noop1'][0]) == pixels(so['steps']['s_noop2'][0]) == added
    assert pixels(so['steps']['s_recipe'][0]) != pixels(so['steps']['s_edit_noop'][0])
    assert pixels(so['steps']['s_style'][0]) != pixels(so['steps']['s_edit_noop'][0])
    assert pixels(so['steps']['s_font'][0]) != pixels(so['steps']['s_edge'][0])
    assert pixels(so['steps']['s_removed'][0]) == base                          # removal = text-only control
    assert pixels(so['steps']['b_remove_strike'][0]) != pixels(so['steps']['b_edit'][0])
    assert pixels(so['steps']['b_strike'][0]) not in (pixels(so['steps']['u_add'][0]), base)  # mixed visible


def test_poppler_raster_when_available(so, tmp_path):
    renderer = shutil.which('pdftoppm')
    if renderer is None:
        pytest.skip('Poppler unavailable')
    from PIL import Image

    def rendered(name):
        dest = tmp_path / name
        subprocess.run([renderer, '-singlefile', '-r', '144', '-png', str(so['steps'][name][0]), str(dest)],
                       check=True, capture_output=True)
        with Image.open(dest.with_suffix('.png')) as image:
            return image.size, image.convert('RGB').tobytes()
    assert rendered('s_add') == rendered('s_noop1') == rendered('s_noop2')
    assert rendered('s_add') != rendered('base') and rendered('s_removed') == rendered('base')
    assert rendered('b_strike') != rendered('u_add')


# ---------------------------------------------------------------- failure isolation, compatibility


@pytest.mark.parametrize('point', ['plan', 'recipe', 'geometry', 'separation', 'serializer', 'transaction',
                                   'owner_rebind', 'semantic_rebind', 'reopen'])
def test_failure_injection_leaves_inputs_and_workspace_untouched(so, monkeypatch, point):
    pdf, sidecar = so['steps']['b_strike']
    before, existing = files(pdf, sidecar), candidates(so['root'])

    def boom(*args, **kwargs):
        raise PdfError('injected ' + point)
    proxy = SimpleNamespace(**{k: getattr(paint, k) for k in dir(paint) if not k.startswith('__')})
    writer_view = {'geometry': 'rectangles', 'separation': 'separation', 'serializer': 'paint_group'}
    if point in writer_view:  # the writer's own view of semantic_paint only
        setattr(proxy, writer_view[point], boom)
        monkeypatch.setattr(writer, 'paint', proxy)
    elif point == 'recipe':  # recipe validation everywhere (the current bundle no longer verifies)
        monkeypatch.setattr(paint, 'recipe', boom)
    else:
        target = {'plan': (semantic, 'plan_semantic_transition'), 'transaction': (writer, '_underline_paints'),
                  'owner_rebind': (owned, 'rebind'), 'semantic_rebind': (semantic, '_attach'),
                  'reopen': (writer, '_verify_candidate')}[point]
        monkeypatch.setattr(*target, boom)
    with pytest.raises(PdfError):
        writer.build_semantic_candidate(pdf, sidecar, edit(1, 1, 'A'), workspace=so['root'] / 'work')
    assert files(pdf, sidecar) == before and candidates(so['root']) == existing


def test_publication_failure_leaves_the_previous_bundle_untouched(so, tmp_path, monkeypatch):
    previous = publication.publish_semantic_bundle(*so['steps']['s_add'], tmp_path / 'previous')
    before = files(previous['pdf'], previous['sidecar'])
    candidate = writer.build_semantic_candidate(previous['pdf'], previous['sidecar'], remove(0, 0, 3),
                                                workspace=tmp_path / 'work')
    real = semantic.open_semantic_flow
    monkeypatch.setattr(semantic, 'open_semantic_flow', lambda *a, **k: dict(status='needs_confirmation', reason='injected'))
    with pytest.raises(PdfError, match='injected'):
        publication.publish_semantic_bundle(candidate['pdf'], candidate['sidecar'], tmp_path / 'next')
    monkeypatch.setattr(semantic, 'open_semantic_flow', real)
    assert not (tmp_path / 'next').exists() and files(previous['pdf'], previous['sidecar']) == before


def test_version_2_and_3_writes_keep_their_exact_paths(so):
    """No v4 artefact leaks into v2/v3: their bodies hold no strikeout and their versions never change."""
    for name in ('base', 'newline', 'm_text'):
        assert version(so, name) == 2 and split(body(so, name))[1] == b''
    assert version(so, 'u_add') == 3 and b'63.6' not in body(so, 'u_add')
