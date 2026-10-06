"""Narrow semantic underline runtime (§28): semantic record version 3 on the single owned slot.

Production evidence for the §27 contract: explicit E add (version 2 → 3), D remap,
E remove/recipe, R refusals, the v3 current-body verifier, Transaction-owned underline
paint (insertion, replacement, removal, planned area, obstacles), foreign paint
protection, publication and reopen, MuPDF raster, failure isolation and v1/v2
compatibility. Nothing here adopts source paint: the first underline is always an
explicit `decorations.add`.
"""
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pymupdf
import pytest

from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_paint as paint
from pdfeditor import semantic_publication as publication
from pdfeditor import semantic_writer as writer
from pdfeditor import shared_flow
from pdfeditor import source_ownership as owned
from pdfeditor.anchors import project_range
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.editable import _seal
from pdfeditor.transaction import Transaction
from test_semantic_authority import alternate_font, glyph_font
from test_semantic_layout import make_flow, statement

ROOT = Path(__file__).resolve().parents[1]
RECIPE = {'offset_em': '13/120', 'thickness_em': '7/120'}
OTHER = {'offset_em': '1/10', 'thickness_em': '1/20'}
EDGE = dict(left=2, right=3, delta='6/5', operator='TJ', semantics='confirmed-adjacent-pair',
            boundary_policy='suppress-at-line-end')
PATH_OPERATORS = {'m', 'l', 'h', 'f', 're', 'c', 'v', 'y', 'S', 's', 'B', 'b', 'W', 'n'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def item(start, end, recipe=RECIPE, **extra):
    value = dict(kind='underline', start=start, end=end, start_affinity='outside', end_affinity='outside',
                 recipe=deepcopy(recipe))
    value.update(extra)
    return value


def add(start, end, recipe=RECIPE, **extra):
    return dict(operation='reinterpret', changes={'decorations': {'add': item(start, end, recipe, **extra)}})


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


def small_descent_font():
    """A/B/space font with a 1/10 em descent: a 1/6 em underline recipe no longer fits its line box."""
    return glyph_font('SemanticShallow', {65: 'A', 66: 'B', 32: 'space'}, width=600, box=(0, -100, 500, 600))


def _shallow(data):
    """Patch hhea/OS2 descent of `small_descent_font` to -100 (glyph_font fixes -200)."""
    from io import BytesIO
    from fontTools.ttLib import TTFont
    font = TTFont(BytesIO(data))
    font['hhea'].descent = -100
    font['OS/2'].sTypoDescender, font['OS/2'].usWinDescent = -100, 100
    out = BytesIO()
    font.save(out)
    return out.getvalue()


@pytest.fixture(scope='module')
def ul(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic-underline')
    _, asset = make_flow(root / 'main')
    main = root / 'main'
    font_b = root / 'font-b.ttf'
    font_b.write_bytes(alternate_font())
    shallow = root / 'shallow.ttf'
    shallow.write_bytes(_shallow(small_descent_font()))
    v3 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0',
                                          semantic=statement(asset))
    (main / 'v3.json').write_text(json.dumps(v3))
    steps = {'confirmed': (main / 'rev1.pdf', main / 'v3.json')}
    plans = {}
    order = [
        ('base', 'confirmed', save(), None),                       # version 2, text-only 'A B'
        ('add', 'base', add(0, 3), None),                          # first explicit add: version 3
        ('noop1', 'add', save(), None), ('noop2', 'noop1', save(), None),
        ('edit', 'noop2', edit(1, 1, 'A'), None),                  # 'AA B': inside insertion is included
        ('edit_noop', 'edit', save(), None),
        ('recipe', 'edit_noop', recipe_change(0, 0, 4, OTHER), None),
        ('style', 'edit_noop', reinterpret(font_size='37/3', horizontal_scale='4/5', tracking='1/4'), None),
        ('style_noop', 'style', save(), None),
        ('tw', 'style_noop', reinterpret(word_spacing='6/5'), None),
        ('edge', 'tw', reinterpret(word_spacing='0', edges=[EDGE]), None),
        ('font', 'edge', reinterpret(font=sha(font_b)), font_b),
        ('font_noop', 'font', save(), None),
        ('removed', 'add', remove(0, 0, 3), None),                 # back to text-only bytes, still version 3
        ('removed_noop', 'removed', save(), None),
        ('multi1', 'edit_noop', remove(0, 0, 4), None),
        ('multi2', 'multi1', add(0, 1), None),
        ('multi3', 'multi2', add(3, 4), None),
        ('touching', 'multi3', add(1, 2), None),                   # touching [0,1) [1,2): kept separate
        ('newline', 'base', edit(1, 2, '\n'), None),               # 'A\nB'
        ('newline_add', 'newline', add(0, 3), None),
        ('empty', 'add', edit(0, 3, ''), None),                    # every decoration terminates
        ('regrow', 'empty', edit(0, 0, 'A B'), None),              # never revived
    ]
    results = {}
    for name, parent, request, supplied in order:
        plans[name] = semantic.plan_semantic_transition(*steps[parent], request, asset=supplied)
        result = writer.build_semantic_candidate(*steps[parent], request, workspace=root / 'work', asset=supplied)
        steps[name] = (result['pdf'], result['sidecar'])
        results[name] = result
    return dict(root=root, main=main, asset=asset, font_b=font_b, shallow=shallow, steps=steps, plans=plans,
                results=results)


def opened(ul, name):
    result = semantic.open_semantic_flow(*ul['steps'][name])
    assert result['status'] == 'restored', result.get('reason')
    return result


def state(ul, name):
    return json.loads(Path(ul['steps'][name][1]).read_text())


def body(ul, name):
    pdf, sidecar = ul['steps'][name]
    value = json.loads(Path(sidecar).read_text())
    marker = value['slots']['slot-0']['source_output']['marker_id']
    content = ContentPage(pdf, 1)
    try:
        data = content.streams[-content.page.xref]
        span = owned.inventory(data)[marker]
        return data[span[1]:span[2]]
    finally:
        content.close()


def split(data):
    return paint.body_grammar(data)


def rects(ul, name):
    value = opened(ul, name)
    if value['version'] != semantic.DECORATED_VERSION:
        return []
    sidecar = state(ul, name)
    slot = sidecar['slots']['slot-0']
    font = semantic._asset(sidecar, slot, value['semantic'], value['authority'])
    try:
        _, plan = semantic._derive(sidecar, slot, value['semantic'], font)
    finally:
        font.font.close()
    return paint.rectangles(plan, value['semantic']['style'], value['semantic']['decorations'])


def pixels(pdf):
    with pymupdf.open(pdf) as document:
        return document[0].get_pixmap(dpi=144, alpha=False).samples


def files(*paths):
    return [Path(p).read_bytes() for p in paths]


def candidates(root):
    work = root / 'work'
    return sorted(p.name for p in work.iterdir()) if work.exists() else []


def resealed(value, change):
    value = deepcopy(value)
    value.pop('model_sha256')
    change(value)
    return _seal(value)


# ---------------------------------------------------------------- versioning and lifecycle


def test_confirmation_and_text_only_revisions_stay_version_2(ul):
    for name in ('confirmed', 'base'):
        result = opened(ul, name)
        assert result['version'] == semantic.SEMANTIC_VERSION and 'decorations' not in result['semantic']
    assert split(body(ul, 'base'))[1] == b''
    with pytest.raises(PdfError, match='exactly text, style, font'):
        semantic.confirm_semantic_layout(ul['main'] / 'rev1.pdf', ul['main'] / 'rev1.json', slot_id='slot-0',
                                         semantic=dict(statement(ul['asset']), decorations=[]))


def test_opening_never_upgrades_and_only_the_explicit_add_enters_version_3(ul):
    pdf, sidecar = ul['steps']['base']
    before = Path(sidecar).read_bytes()
    assert opened(ul, 'base')['version'] == 2 and Path(sidecar).read_bytes() == before
    plan = ul['plans']['add']
    assert (plan['classification'], plan['version'], plan['next_version']) == ('E', 2, 3)
    assert set(plan['diff']) == {'decorations'} and plan['next_authority'] == plan['authority']
    for request in (save(), edit(0, 0, 'A'), reinterpret(font_size='13')):
        assert semantic.plan_semantic_transition(pdf, sidecar, request)['next_version'] == 2
    with pytest.raises(PdfError, match='only an explicit decorations.add'):
        semantic.plan_semantic_transition(pdf, sidecar, remove(0, 0, 3))
    added = opened(ul, 'add')
    assert added['version'] == 3 and added['semantic']['decorations'] == [dict(item(0, 3), id='current:0')]
    assert added['authority'] == opened(ul, 'base')['authority']  # not a style reinterpretation


def test_version_3_is_never_left_or_downgraded(ul):
    for name in ('removed', 'removed_noop', 'empty', 'regrow'):
        result = opened(ul, name)
        assert result['version'] == 3 and result['semantic']['decorations'] == []
        assert split(body(ul, name))[1] == b''
    # A version 3 bundle is not a confirmation input (no downgrade request exists).
    with pytest.raises(PdfError, match='starts from a shared-flow v2 owner sidecar'):
        semantic.confirm_semantic_layout(*ul['steps']['add'], slot_id='slot-0', semantic=statement(ul['asset']))


def test_version_3_without_decorations_is_byte_identical_to_the_text_only_body(ul):
    assert body(ul, 'removed') == body(ul, 'base')
    assert pixels(ul['steps']['removed'][0]) == pixels(ul['steps']['base'][0])


def test_canonical_text_then_underline_body(ul):
    text, group = split(body(ul, 'add'))
    assert text == body(ul, 'base')  # the text group stays a byte prefix
    assert group == b'q 0 g\n20 58.7 m 41.6 58.7 l 41.6 58 l 20 58 l h f\nQ\n'
    assert opened(ul, 'add')['island']['underline_rectangles'] == 1
    assert ul['results']['add']['body'] == body(ul, 'add')


@pytest.mark.parametrize('group', [('add', 'noop1', 'noop2'), ('edit', 'edit_noop'), ('style', 'style_noop'),
                                   ('font', 'font_noop'), ('removed', 'removed_noop')])
def test_noop_is_a_canonical_fixed_point(ul, group):
    first = group[0]
    for name in group[1:]:
        assert body(ul, name) == body(ul, first)
        assert [(op.name, op.args) for op in operators(body(ul, name))] == [(op.name, op.args) for op in operators(body(ul, first))]
        assert rects(ul, name) == rects(ul, first)
        a, b = state(ul, first)['slots']['slot-0'], state(ul, name)['slots']['slot-0']
        for key in ('payload', 'current', 'derived'):
            assert a['semantic'][key] == b['semantic'][key]
        assert a['semantic']['version'] == b['semantic']['version']
        assert a['source_output']['current']['block_sha256'] == b['source_output']['current']['block_sha256']
        assert ul['plans'][name]['diff'] == {} and ul['plans'][name]['classification'] == 'D'
        assert pixels(ul['steps'][name][0]) == pixels(ul['steps'][first][0])


def test_edit_remaps_the_range_and_the_rectangle_follows(ul):
    (d,) = opened(ul, 'edit')['semantic']['decorations']
    assert (d['start'], d['end'], d['id']) == (0, 4, 'current:0')
    (old,), (new,) = rects(ul, 'add'), rects(ul, 'edit')
    assert old[0] == new[0] == 20 and new[2] - old[2] == F(36, 5) and old[1::2] == new[1::2]


def test_recipe_change_moves_only_the_vertical_geometry(ul):
    (before,), (after,) = rects(ul, 'edit_noop'), rects(ul, 'recipe')
    assert (before[0], before[2]) == (after[0], after[2])
    assert after[1] == 200 + F(12, 10) and after[3] - after[1] == F(12, 20)  # page y-down baseline 200
    assert opened(ul, 'recipe')['semantic']['decorations'][0]['recipe'] == OTHER
    assert ul['plans']['recipe']['next_authority'] == ul['plans']['recipe']['authority']


def test_style_change_scales_the_em_recipe_and_ignores_horizontal_scale(ul):
    (r,) = rects(ul, 'style')
    size = F(37, 3)
    assert r[1] - 200 == F(13, 120) * size and r[3] - r[1] == F(7, 120) * size
    assert opened(ul, 'style')['semantic']['decorations'][0]['recipe'] == RECIPE


def test_font_change_keeps_the_recipe_and_its_vertical_geometry(ul):
    (a,), (b,) = rects(ul, 'edge'), rects(ul, 'font')
    assert (a[1], a[3]) == (b[1], b[3]) and a[2] != b[2]
    assert opened(ul, 'font')['semantic']['decorations'][0]['recipe'] == RECIPE
    assert float(b[2]) == pytest.approx(41.683333, abs=1e-6)


def test_tw_and_confirmed_edge_give_identical_underline_bytes(ul):
    assert split(body(ul, 'tw'))[1] == split(body(ul, 'edge'))[1]
    assert rects(ul, 'tw') == rects(ul, 'edge')


def test_multiple_and_touching_decorations_share_one_group(ul):
    decorations = opened(ul, 'touching')['semantic']['decorations']
    assert [(d['id'], d['start'], d['end']) for d in decorations] == [
        ('current:0', 0, 1), ('current:1', 1, 2), ('current:2', 3, 4)]
    group = split(body(ul, 'touching'))[1]
    names = [op.name for op in operators(group)]
    assert names.count('q') == 1 and names.count('f') == 3 and names.count('Q') == 1
    assert [r[0] for r in rects(ul, 'touching')] == [20, F(136, 5), F(208, 5)]  # never merged


def test_newline_range_paints_one_rectangle_per_line(ul):
    rs = rects(ul, 'newline_add')
    assert len(rs) == 2 and rs[0][1] < rs[1][1]
    names = [op.name for op in operators(split(body(ul, 'newline_add'))[1])]
    assert names.count('f') == 2


def test_empty_text_terminates_and_regrowth_never_revives(ul):
    assert ul['plans']['empty']['next']['decorations'] == []
    assert opened(ul, 'regrow')['semantic']['decorations'] == []
    assert opened(ul, 'regrow')['semantic']['text'] == 'A B'


def test_d_remap_matches_the_project_range_convention_where_it_applies():
    """Non-crossing edits: §27.7 membership equals anchors.project_range with outside/outside affinity."""
    text = 'AA B'
    for s in range(4):
        for e in range(s + 1, 5):
            if not paint.visible(text[s:e]):
                continue
            decorations = paint.renumber([dict(item(s, e), id='x')])
            for a in range(5):
                for b in range(a, 5):
                    if a < b and (a < s < b or a < e < b):
                        continue  # crossing edit: project_range refuses, §27.7 shrinks (contract matrix)
                    for inserted in ('', 'A', 'B '):
                        after = text[:a] + inserted + text[b:]
                        expected = project_range(text, [dict(start=a, end=b, text=inserted)], s, e,
                                                 start_affinity='outside', end_affinity='outside')
                        result = paint.remap(text, decorations, a, b, inserted)
                        if expected is None or not paint.visible(after[expected[0]:expected[1]]):
                            assert result == []
                        else:
                            assert [(d['start'], d['end']) for d in result] == [expected]


# ---------------------------------------------------------------- R refusals through the production plan


@pytest.fixture(scope='module')
def r_base(ul, tmp_path_factory):
    """'AA B' with current:0 = [0, 2), version 3."""
    root = tmp_path_factory.mktemp('semantic-underline-r')
    result = writer.build_semantic_candidate(*ul['steps']['multi1'], add(0, 2), workspace=root / 'base')
    return result['pdf'], result['sidecar'], root


@pytest.mark.parametrize('request_, reason', [
    (add(3, 4, kind='highlight'), 'unsupported decoration kind'),  # §32: a strikeout add is the v4 route
    (add(3, 4, id='current:7'), 'exactly kind'),
    (add(3, 4, source_id='path-1'), 'exactly kind'),
    (add(3, 4, recipe='auto'), 'offset_em and thickness_em'),
    (add(3, 4, recipe={'offset_em': '13/120'}), 'offset_em and thickness_em'),
    (add(3, 4, recipe={'offset_em': '0.1', 'thickness_em': '7/120'}), 'canonical exact rational'),
    (add(3, 4, recipe={'offset_em': '13/120', 'thickness_em': '0'}), 'exact bounds'),
    (add(3, 4, start_affinity='inside'), 'outside/outside'),
    (add(1, 3), 'overlap'),
    (add(2, 2), 'ordered grapheme boundaries'),
    (add(3, 3), 'ordered grapheme boundaries'),
    (add(2, 3), 'no visible character'),
    (add(3, 4, recipe={'offset_em': '3/20', 'thickness_em': '1/10'}), 'line box'),
    (dict(operation='reinterpret', changes={'decorations': {'add': item(3, 4)}, 'font_size': '13'}), 'its own'),
    (dict(operation='reinterpret', changes={'decorations': {'add': item(3, 4), 'remove': {}}}), 'exactly one'),
    (remove(0, 0, 3), 'not the current decoration'),
    (remove(1, 0, 2), 'not the current decoration'),
    (recipe_change(0, 0, 2, RECIPE), 'must differ'),
    (dict(operation='reinterpret', changes={'decorations': {'revive': dict(id='current:0')}}), 'unsupported decoration action'),
    (dict(operation='reinterpret', changes={'decorations': {'adopt': dict(source_id='path-1')}}), 'unsupported decoration action'),
])
def test_r_refusals_through_the_production_plan(r_base, request_, reason):
    pdf, sidecar, root = r_base
    before, existing = files(pdf, sidecar), candidates(root)
    with pytest.raises(PdfError, match=reason):
        semantic.plan_semantic_transition(pdf, sidecar, request_)
    with pytest.raises(PdfError, match=reason):
        writer.build_semantic_candidate(pdf, sidecar, request_, workspace=root / 'work')
    assert files(pdf, sidecar) == before and candidates(root) == existing


def test_all_space_and_newline_only_ranges_are_refused(ul):
    for name, start, end in (('base', 1, 2), ('newline', 1, 2)):
        with pytest.raises(PdfError, match='no visible character'):
            semantic.plan_semantic_transition(*ul['steps'][name], add(start, end))


def test_font_or_size_change_breaking_the_line_box_is_refused(ul):
    with pytest.raises(PdfError, match='line box'):
        semantic.plan_semantic_transition(*ul['steps']['add'], reinterpret(font=sha(ul['shallow'])),
                                          asset=ul['shallow'])
    with pytest.raises(PdfError, match='line box'):
        writer.build_semantic_candidate(*ul['steps']['add'], reinterpret(font=sha(ul['shallow'])),
                                        workspace=ul['root'] / 'work', asset=ul['shallow'])
    # The same font change without an underline is admitted (the refusal is the underline's).
    plan = semantic.plan_semantic_transition(*ul['steps']['base'], reinterpret(font=sha(ul['shallow'])),
                                             asset=ul['shallow'])
    assert plan['next_version'] == 2


def test_version_1_record_refuses_decoration_requests(ul, tmp_path):
    value = state(ul, 'base')
    v1 = semantic._attach(value, 'slot-0', value['slots']['slot-0']['semantic']['payload'])
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps(v1))
    pdf = ul['steps']['base'][0]
    assert semantic.open_semantic_flow(pdf, path)['version'] == 1
    with pytest.raises(PdfError, match='confirm version 1 as version 2 first'):
        semantic.plan_semantic_transition(pdf, path, add(0, 3))
    plan = semantic.plan_semantic_transition(pdf, path, save())
    assert plan['next_version'] == 1 and 'decorations' not in plan['next']


# ---------------------------------------------------------------- v2/v3 validation split


def test_v2_opener_and_text_only_records_refuse_a_painted_body(ul):
    pdf, sidecar = ul['steps']['add']
    value = state(ul, 'add')
    restored = shared_flow.open_shared_flow(pdf, semantic._project_v2(value))
    assert restored['status'] == 'needs_confirmation' and 'source output body grammar' in restored['reason']
    with pytest.raises(PdfError, match='source output body grammar'):
        owned.validate(pdf, semantic._project_v2(value))
    owned.validate(pdf, semantic._project_v2(value), body_grammar=paint.body_grammar)

    def as_version_2(v):
        record = v['slots']['slot-0']['semantic']
        record['version'] = 2
        record['payload'].pop('decorations')
    reason = semantic.open_semantic_flow(pdf, resealed(value, as_version_2))['reason']
    assert 'source output body grammar' in reason

    def as_version_1(v):
        record = v['slots']['slot-0']['semantic']
        record['version'] = 1
        record['payload'].pop('decorations')
        record.pop('current')
        for key in ('font_resource', 'font_subset_sha256'):
            record['binding'].pop(key)
    assert semantic.open_semantic_flow(pdf, resealed(value, as_version_1))['status'] == 'needs_confirmation'

    def without_authority(v):
        v['slots']['slot-0']['semantic']['current'] = None
    reason = semantic.open_semantic_flow(pdf, resealed(value, without_authority))['reason']
    assert 'version 3 needs explicit current' in reason


def test_v2_grammar_and_v2_call_sites_are_unchanged():
    assert owned.BODY_OPERATORS == frozenset('q Q BT ET Tf Tz Tc Tw Ts Tm Tj TJ g rg k'.split())
    assert not PATH_OPERATORS & owned.BODY_OPERATORS
    import inspect
    assert 'current_body' not in inspect.signature(shared_flow.open_shared_flow).parameters
    for function in (owned.witness, owned.validate, owned.owned_body, owned.rebind):
        assert inspect.signature(function).parameters['body_grammar'].default is None


@pytest.mark.parametrize('name, change', [
    ('range', lambda d: d[0].update(end=2)),
    ('recipe', lambda d: d[0]['recipe'].update(thickness_em='1/20')),
    ('kind', lambda d: d[0].update(kind='strikeout')),
    ('id', lambda d: d[0].update(id='current:5')),
    ('affinity', lambda d: d[0].update(end_affinity='inside')),
    ('missing decoration', lambda d: d.pop()),
    ('extra decoration', lambda d: d.append(dict(item(1, 2), id='current:1'))),
    ('order', lambda d: d.extend([dict(item(0, 1), id='current:1')])),
])
def test_resealed_semantic_decoration_tamper_is_refused(ul, name, change):
    pdf, _ = ul['steps']['add']

    def tamper(v):
        change(v['slots']['slot-0']['semantic']['payload']['decorations'])
    assert semantic.open_semantic_flow(pdf, resealed(state(ul, 'add'), tamper))['status'] == 'needs_confirmation'


def test_stale_and_mismatched_pdf_sidecar_pairs_are_refused(ul):
    # A no-op save rewrites the owned body byte-identically: the PDF is the same revision.
    assert files(ul['steps']['add'][0]) == files(ul['steps']['noop1'][0]) == files(ul['steps']['noop2'][0])
    pairs = [('add', 'base'), ('base', 'add'), ('edit', 'add'), ('add', 'edit'), ('removed', 'add'), ('add', 'removed'),
             ('recipe', 'edit_noop'), ('edit_noop', 'recipe')]
    for pdf_name, sidecar_name in pairs:
        result = semantic.open_semantic_flow(ul['steps'][pdf_name][0], ul['steps'][sidecar_name][1])
        assert result['status'] == 'needs_confirmation', (pdf_name, sidecar_name)


def _rebound(ul, name, pdf):
    """In-memory state whose owner witness describes `pdf` (v3 grammar) — reaches the canonical body check."""
    value = state(ul, name)
    slot = value['slots']['slot-0']
    content = ContentPage(pdf, 1)
    try:
        span = owned.inventory(content.streams[-content.page.xref])[slot['source_output']['marker_id']]
        slot['source_output']['current'] = owned.witness(content, slot['source_output'], span,
                                                         body_grammar=paint.body_grammar)
    finally:
        content.close()
    return value


def _replace_body(ul, name, out, new_body):
    pdf = ul['steps'][name][0]
    marker = state(ul, name)['slots']['slot-0']['source_output']['marker_id']
    with pymupdf.open(pdf) as document:
        xref = document[0].get_contents()[-1]
        data = document.xref_stream(xref)
        _, start, end, _ = owned.inventory(data)[marker]
        document.update_stream(xref, data[:start] + new_body + data[end:])
        document.save(out)
    return out


BODY_TAMPERS = [
    ('geometry', lambda p: p.replace(b'41.6 58 l', b'41.6 57.9 l')),
    ('fill', lambda p: p.replace(b'q 0 g', b'q 1 0 0 rg')),
    ('extra path', lambda p: p.replace(b'Q\n', b'50 58.7 m 60 58.7 l 60 58 l 50 58 l h f\nQ\n')),
    ('missing path', lambda p: b'q 0 g\n20 58.7 m 27.2 58.7 l 27.2 58 l 20 58 l h f\nQ\n'),
    ('re operator', lambda p: b'q 0 g\n20 58 21.6 0.7 re f\nQ\n'),
    ('stroke', lambda p: p.replace(b'h f\n', b'h S\n')),
    ('clip', lambda p: p.replace(b'h f\n', b'h W n\n')),
    ('curve', lambda p: p.replace(b'41.6 58 l', b'41.6 58.5 41.6 58.2 41.6 58 c')),
    ('cm', lambda p: p.replace(b'q 0 g', b'q 1 0 0 1 0 0 cm 0 g')),
    ('ExtGState', lambda p: p.replace(b'q 0 g', b'q /GS0 gs 0 g')),
    ('nested q', lambda p: p.replace(b'q 0 g\n', b'q 0 g\nq\n').replace(b'Q\n', b'Q\nQ\n')),
    ('empty group', lambda p: b'q 0 g\nQ\n'),
    ('comment', lambda p: p.replace(b'Q\n', b'% x\nQ\n')),
    ('nonrectangle', lambda p: p.replace(b'41.6 58 l', b'40 58 l')),
    ('image', lambda p: p.replace(b'Q\n', b'/Im0 Do\nQ\n')),
]


@pytest.mark.parametrize('name, change', BODY_TAMPERS)
def test_current_body_paint_tamper_is_refused_by_the_production_verifier(ul, tmp_path, name, change):
    text, group = split(body(ul, 'add'))
    pdf = _replace_body(ul, 'add', tmp_path / 'tampered.pdf', text + change(group))
    # Unchanged sidecar: the whole-PDF/owner binding refuses.
    assert semantic.open_semantic_flow(pdf, ul['steps']['add'][1])['status'] == 'needs_confirmation'
    # With the owner witness rebound to the tampered bytes, the v3 grammar or the exact canonical body refuses.
    with pytest.raises((PdfError, KeyError)):
        value = _rebound(ul, 'add', pdf)
        slot = value['slots']['slot-0']
        payload, current = slot['semantic']['payload'], slot['semantic']['current']
        font = semantic._asset(value, slot, payload, current)
        try:
            _, plan = semantic._derive(value, slot, payload, font)
            semantic._island(pdf, value, 'slot-0', payload, plan, current, font, payload['decorations'])
        finally:
            font.font.close()


@pytest.mark.parametrize('where', ['paint-first', 'foreign path moved into the owner', 'paint without decorations'])
def test_paint_first_and_foreign_paint_in_the_owner_are_refused(ul, tmp_path, where):
    text, group = split(body(ul, 'add'))
    name, new = {
        'paint-first': ('add', group + text),
        'foreign path moved into the owner': ('add', text + b'q 0 g\n100 100 m 110 100 l 110 99 l 100 99 l h f\nQ\n'),
        'paint without decorations': ('removed', text + group),
    }[where]
    pdf = _replace_body(ul, name, tmp_path / 'tampered.pdf', new)
    with pytest.raises(PdfError):
        value = _rebound(ul, name, pdf)
        slot = value['slots']['slot-0']
        payload, current = slot['semantic']['payload'], slot['semantic']['current']
        font = semantic._asset(value, slot, payload, current)
        try:
            _, plan = semantic._derive(value, slot, payload, font)
            semantic._island(pdf, value, 'slot-0', payload, plan, current, font, payload['decorations'])
        finally:
            font.font.close()


@pytest.mark.parametrize('change', ['drop', 'bounds', 'unproven', 'relation', 'crossing'])
def test_element_paths_in_the_owned_body_must_be_exactly_the_canonical_rectangles(ul, change):
    value = state(ul, 'add')
    slot = value['slots']['slot-0']
    result = opened(ul, 'add')
    span = (None, *result['island']['body_span'], None)
    rs = rects(ul, 'add')
    semantic._owned_paint(slot, span, rs)
    (path,) = [p for p in slot['binding']['element']['paths']
               if span[1] <= p['source']['merged_range'][0] < span[2]]
    assert path['relationship']['hypothesis'] == 'decoration_candidate'  # an inference with no authority
    if change == 'drop':
        slot['binding']['element']['paths'].remove(path)
    elif change == 'bounds':
        path['proof']['paints'][0]['bounds'][2] += .01
    elif change == 'unproven':
        path['proof']['status'] = 'refused'
    elif change == 'relation':
        slot['binding']['relations'].append(dict(source_id=path['source_id'], relation='unrelated',
                                                 behavior='fixed-to-page'))
    else:
        path['source']['merged_range'] = [span[1] - 1, span[1] + 5]
    with pytest.raises(PdfError):
        semantic._owned_paint(slot, span, rs)


# ---------------------------------------------------------------- Transaction ownership


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


@pytest.mark.parametrize('parent, request_, kind', [
    ('base', add(0, 3), 'insert'), ('add', save(), 'replace'), ('edit_noop', recipe_change(0, 0, 4, OTHER), 'replace'),
    ('add', remove(0, 0, 3), 'remove'), ('add', edit(0, 3, ''), 'remove'), ('base', save(), 'none'),
])
def test_transaction_declares_owned_paint(ul, spy, parent, request_, kind):
    writer.build_semantic_candidate(*ul['steps'][parent], request_, workspace=ul['root'] / 'work')
    (plan,) = spy
    if kind == 'insert':
        assert plan.paint_changes == {} and len(plan.paint_insertions) == 1
        (values,) = plan.paint_insertions.values()
        assert len(values) == 1 and values[0]['kind'] == 'fill-path'
    elif kind == 'replace':
        assert plan.paint_insertions == {} and len(plan.paint_changes) == 1
        (values,) = plan.paint_changes.values()
        assert len(values) == 1 and values[0]['kind'] == 'fill-path'
    elif kind == 'remove':
        assert plan.paint_insertions == {} and list(plan.paint_changes.values()) == [None]
    else:
        assert plan.paint_insertions == {} and plan.paint_changes == {}
    assert len(plan.consumed_paths) == (0 if parent == 'base' else 1)


def test_old_owned_paint_is_identified_only_by_proven_canonical_rectangles(ul):
    with Transaction(ul['steps']['touching'][0]) as transaction:
        page = transaction.page(1)
        _, start, end, _ = owned.inventory(page.content.streams[-page.content.page.xref])[
            state(ul, 'touching')['slots']['slot-0']['source_output']['marker_id']]
        old = rects(ul, 'touching')
        ids, indices, seqnos = writer._old_owned_paint(page, start, end, old)
        assert len(ids) == len(indices) == len(seqnos) == 3 and indices == sorted(indices)
        for wrong in (old[:2], old + old[:1], [old[1], old[0], old[2]],
                      [(a, b + F(1, 10), c, d + F(1, 10)) for a, b, c, d in old]):
            with pytest.raises(PdfError):
                writer._old_owned_paint(page, start, end, wrong)


@pytest.mark.parametrize('fault', ['geometry', 'colour', 'undeclared insertion', 'extra paint'])
def test_misdeclared_underline_paint_is_refused_by_the_transaction(ul, monkeypatch, fault):
    real = writer._underline_paints

    def wrong(rects, template):
        values = real(rects, template)
        if fault == 'geometry':
            values[0]['bounds'][1] += .5
            values[0]['geometry'] = [[c[0], *(v + .5 * (i % 2) for i, v in enumerate(c[1:]))] for c in values[0]['geometry']]
        elif fault == 'colour':
            values[0]['components'] = [.5]
        elif fault == 'undeclared insertion':
            return []
        else:
            values.append(deepcopy(values[0]))
        return values
    monkeypatch.setattr(writer, '_underline_paints', wrong)
    before = files(*ul['steps']['base'])
    with pytest.raises(PdfError, match='saved non-text paint differs from the transaction plan'):
        writer.build_semantic_candidate(*ul['steps']['base'], add(0, 3), workspace=ul['root'] / 'work')
    assert files(*ul['steps']['base']) == before


@pytest.mark.parametrize('parent, request_', [('add', remove(0, 0, 3)), ('base', add(0, 3))])
def test_planned_area_needs_old_and_new_rectangles(ul, monkeypatch, parent, request_):
    """Pixel-outside-area protection is active for underline paint: with only the glyph-ink box as the
    planned area, removing (old rectangle) or adding (new rectangle) an underline is refused."""
    real = writer._plan_island

    def narrow(*args, **kwargs):
        plan, lines, data = real(*args, **kwargs)
        new = sum(len(v or []) for v in [*plan.paint_changes.values(), *plan.paint_insertions.values()])
        inks = plan.final_rects[:len(plan.final_rects) - new]
        removed_ink, _ = args[8]
        area = inks[0]
        for r in inks[1:] + removed_ink:
            area = area.union(r)
        plan.affected = area
        return plan, lines, data
    monkeypatch.setattr(writer, '_plan_island', narrow)
    with pytest.raises(PdfError, match='changed pixels outside its planned areas'):
        writer.build_semantic_candidate(*ul['steps'][parent], request_, workspace=ul['root'] / 'work')


def test_planned_area_holds_old_and_new_rectangles(ul, spy):
    writer.build_semantic_candidate(*ul['steps']['edit_noop'], recipe_change(0, 0, 4, OTHER), workspace=ul['root'] / 'work')
    (plan,) = spy
    for r in rects(ul, 'edit_noop') + rects(ul, 'recipe'):
        a, b, c, d = (float(v) for v in r)
        assert plan.affected.contains(type(plan.affected)(a, b, c, d), 1e-9)
    new = [tuple(float(v) for v in r) for r in rects(ul, 'recipe')]
    assert new == [(r.x0, r.y0, r.x1, r.y1) for r in plan.final_rects[-len(new):]]


def test_only_the_islands_own_old_paint_is_excluded_from_obstacles(ul, monkeypatch):
    # The old underline crosses the new B descender ink: without the exclusion the next save is refused.
    real = writer._old_owned_paint
    monkeypatch.setattr(writer, '_old_owned_paint', lambda *a: (*real(*a)[:2], []))
    with pytest.raises(PdfError, match='filled vector'):
        writer.build_semantic_candidate(*ul['steps']['add'], save(), workspace=ul['root'] / 'work')


# ---------------------------------------------------------------- foreign paint protection


def _foreign_flow(root, prefix):
    """`make_flow` with the foreign path explicitly confirmed `unrelated`/`fixed-to-page` (never adopted)."""
    from pdfeditor.attributed import inspect_paragraph
    from pdfeditor.elements import inspect_element
    from pdfeditor.selection import make_selection
    from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow
    from pdfeditor.story_flow import confirm_story
    from test_attributed import source_pdf
    from test_semantic_layout import static_font
    root.mkdir(parents=True, exist_ok=True)
    source = source_pdf(root, prefix + b'BT /Regular 12 Tf 20 200 Td (XY) Tj ET')
    asset = root / 'asset.ttf'
    asset.write_bytes(static_font())
    p = inspect_paragraph(source, make_selection(source, glyph_ids=[0, 1], explicit_width=150))
    (path,) = inspect_element(source, p['selection'])['paths']
    relation = dict(source_id=path['source_id'], relation='unrelated', behavior='fixed-to-page')
    story = confirm_story(source, {'part': dict(page=1, bounds=[18, 40, 200, 220], paragraph=p, paint_relations=[relation],
        layout=dict(x=20, baseline=200, width=150, max_bottom=220, min_line_height=22, first_line_indent=0))},
        paragraph_id='A', chain=['part'], protected_regions={},
        styles={'body': dict(provider=dict(path=str(asset)), provider_relation='substituted')},
        style_assignments={'part': {s['id']: 'body' for s in p['styles']}}, typing_style_id='body')
    state = confirm_shared_flow(source, {'A': story}, flow_id='foreign-flow', paragraph_order=['A'],
        regions={'R': dict(page=1, bounds=[18, 40, 200, 250], x=20, width=150, first_baseline=200)},
        region_order=['R'], slot_regions={'A': {'part': 'R'}},
        paragraph_policies={'A': dict(min_line_height=22, first_line_indent=0, keep_together=False,
            break_before='auto', break_after='auto', empty=dict(kind='reserve-line', ascent=10, descent=3))},
        follows=[], protected_regions={})
    edit_shared_flow(source, state, root / 'rev1.pdf', root / 'rev1.json',
                     {'A': dict(edits=[dict(start=0, end=2, text='A B', style_id='body')])})
    return asset


@pytest.fixture(scope='module')
def foreign(tmp_path_factory):
    """Source with foreign vector paint: far from the text, and one crossing the future underline."""
    root = tmp_path_factory.mktemp('semantic-underline-foreign')
    result = {}
    for name, prefix in (('far', b'q 0 0 1 rg 100 50 10 5 re f Q '),
                         ('near', b'q 0 0 1 rg 22 58.2 4 .3 re f Q ')):
        asset = _foreign_flow(root / name, prefix)
        main = root / name
        v3 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0',
                                              semantic=statement(asset))
        (main / 'v3.json').write_text(json.dumps(v3))
        saved = writer.build_semantic_candidate(main / 'rev1.pdf', main / 'v3.json', save(), workspace=root / 'work')
        result[name] = (saved['pdf'], saved['sidecar'])
    return dict(root=root, steps=result)


def _nontext(pdf):
    from pdfeditor.paint_provenance import interpreted_paints
    return [[round(v, 3) for v in e['bounds']] for e in interpreted_paints(str(pdf), 1)['events']
            if e['kind'] not in ('fill-text', 'stroke-text', 'ignore-text')]


def test_foreign_paint_is_kept_and_never_adopted(foreign):
    pdf, sidecar = foreign['steps']['far']
    foreign_before = _nontext(pdf)
    assert len(foreign_before) == 1
    added = writer.build_semantic_candidate(pdf, sidecar, add(0, 3), workspace=foreign['root'] / 'work')
    after = _nontext(added['pdf'])
    assert after[0] == foreign_before[0] and len(after) == 2  # foreign first, then the owned underline
    removed = writer.build_semantic_candidate(added['pdf'], added['sidecar'], remove(0, 0, 3),
                                              workspace=foreign['root'] / 'work')
    assert _nontext(removed['pdf']) == foreign_before
    assert semantic.open_semantic_flow(removed['pdf'], removed['sidecar'])['semantic']['decorations'] == []
    # Confirmation never infers a decoration from source paint.
    assert 'decorations' not in semantic.open_semantic_flow(pdf, sidecar)['semantic']


def test_foreign_paint_under_a_new_underline_is_an_obstacle(foreign):
    pdf, sidecar = foreign['steps']['near']
    before = files(pdf, sidecar)
    with pytest.raises(PdfError, match='filled vector'):
        writer.build_semantic_candidate(pdf, sidecar, add(0, 3), workspace=foreign['root'] / 'work')
    assert files(pdf, sidecar) == before
    # The same island without the underline is unaffected by that paint.
    writer.build_semantic_candidate(pdf, sidecar, save(), workspace=foreign['root'] / 'work')


def test_ordinary_paths_over_a_painted_island_never_yield_a_reopenable_bundle(ul, tmp_path):
    """O1: the shared-flow v2 editor cannot open a painted revision, and any foreign save changes the
    whole-PDF revision, so the version 3 bundle needs confirmation (fail-closed by staleness)."""
    pdf, sidecar = ul['steps']['add']
    with pytest.raises(PdfError, match='source output body grammar'):
        shared_flow.edit_shared_flow(pdf, semantic._project_v2(state(ul, 'add')), tmp_path / 'v2.pdf',
                                     tmp_path / 'v2.json', {'A': dict(edits=[dict(start=0, end=0, text='A',
                                                                                    style_id='body')])})
    assert not (tmp_path / 'v2.pdf').exists()
    with pymupdf.open(pdf) as document:
        document[0].draw_rect(pymupdf.Rect(100, 20, 110, 25), color=None, fill=(0, 0, 1))
        document.save(tmp_path / 'foreign.pdf')
    assert semantic.open_semantic_flow(tmp_path / 'foreign.pdf', sidecar)['status'] == 'needs_confirmation'
    with pytest.raises(PdfError, match='does not verify'):
        writer.build_semantic_candidate(tmp_path / 'foreign.pdf', sidecar, save(), workspace=tmp_path / 'work')


# ---------------------------------------------------------------- publication, reopen, next revision


def test_publication_reopen_and_next_revision(ul, tmp_path):
    a = publication.publish_semantic_bundle(*ul['steps']['add'], tmp_path / 'bundle-a')
    assert a['verification']['version'] == 3 and a['verification']['island']['underline_rectangles'] == 1
    assert files(a['pdf'], a['sidecar']) == files(*ul['steps']['add'])
    assert pixels(a['pdf']) == pixels(ul['steps']['add'][0])
    a_bytes = files(a['pdf'], a['sidecar'])
    nxt = writer.build_semantic_candidate(a['pdf'], a['sidecar'], edit(1, 1, 'A'), workspace=tmp_path / 'work')
    b = publication.publish_semantic_bundle(nxt['pdf'], nxt['sidecar'], tmp_path / 'bundle-b')
    assert b['verification']['semantic']['decorations'][0]['end'] == 4
    assert files(a['pdf'], a['sidecar']) == a_bytes
    # Owner identity is continuous; the owner witness is rebound to each revision.
    owners = [json.loads(Path(p).read_text())['slots']['slot-0']['source_output']
              for p in (ul['steps']['base'][1], a['sidecar'], b['sidecar'])]
    assert len({(o['marker_id'], json.dumps(o['created_from'])) for o in owners}) == 1
    assert len({o['current']['block_sha256'] for o in owners}) == 3
    # Mixed revisions are refused.
    assert semantic.open_semantic_flow(a['pdf'], b['sidecar'])['status'] == 'needs_confirmation'
    assert semantic.open_semantic_flow(b['pdf'], a['sidecar'])['status'] == 'needs_confirmation'
    # Fresh process reopen (no in-process cache).
    code = ('import json,sys; from pdfeditor import semantic_layout as s; '
            'r=s.open_semantic_flow(sys.argv[1], sys.argv[2]); '
            'print(json.dumps([r["status"], r.get("version"), r.get("semantic", {}).get("decorations")]))')
    for bundle in (a, b):
        out = subprocess.run([sys.executable, '-c', code, str(bundle['pdf']), str(bundle['sidecar'])], cwd=ROOT,
                             check=True, capture_output=True, text=True).stdout
        status, version, decorations = json.loads(out)
        assert (status, version) == ('restored', 3) and decorations == bundle['verification']['semantic']['decorations']


def test_publication_refuses_an_existing_destination_without_touching_it(ul, tmp_path):
    first = publication.publish_semantic_bundle(*ul['steps']['add'], tmp_path / 'bundle')
    before = files(first['pdf'], first['sidecar'])
    with pytest.raises(PdfError, match='never replaced'):
        publication.publish_semantic_bundle(*ul['steps']['removed'], tmp_path / 'bundle')
    assert files(first['pdf'], first['sidecar']) == before


# ---------------------------------------------------------------- raster


def test_mupdf_raster(ul):
    base, added = pixels(ul['steps']['base'][0]), pixels(ul['steps']['add'][0])
    assert added != base                                                     # underline added
    assert pixels(ul['steps']['noop1'][0]) == pixels(ul['steps']['noop2'][0]) == added   # no-op identical
    assert pixels(ul['steps']['recipe'][0]) != pixels(ul['steps']['edit_noop'][0])        # recipe change
    assert pixels(ul['steps']['style'][0]) != pixels(ul['steps']['edit_noop'][0])        # style change
    assert pixels(ul['steps']['font'][0]) != pixels(ul['steps']['edge'][0])              # font change
    assert pixels(ul['steps']['font_noop'][0]) == pixels(ul['steps']['font'][0])
    assert pixels(ul['steps']['removed'][0]) == base                                   # underline removed


def test_poppler_raster_when_available(ul, tmp_path):
    renderer = shutil.which('pdftoppm')
    if renderer is None:
        pytest.skip('Poppler unavailable')
    from PIL import Image

    def rendered(name):
        dest = tmp_path / name
        subprocess.run([renderer, '-singlefile', '-r', '144', '-png', str(ul['steps'][name][0]), str(dest)],
                       check=True, capture_output=True)
        with Image.open(dest.with_suffix('.png')) as image:
            return image.size, image.convert('RGB').tobytes()
    assert rendered('add') == rendered('noop1') == rendered('noop2')
    assert rendered('add') != rendered('base') and rendered('removed') == rendered('base')


# ---------------------------------------------------------------- failure isolation


@pytest.mark.parametrize('point', ['plan', 'derivation', 'serializer', 'transaction', 'owner_rebind',
                                   'semantic_rebind', 'reopen'])
def test_failure_injection_leaves_inputs_and_workspace_untouched(ul, monkeypatch, point):
    pdf, sidecar = ul['steps']['add']
    before, existing = files(pdf, sidecar), candidates(ul['root'])

    def boom(*args, **kwargs):
        raise PdfError('injected ' + point)
    # Derivation and serializer faults are injected into the writer's own view of semantic_paint only,
    # so the current bundle still verifies and the failure is the writer's.
    proxy = SimpleNamespace(**{k: getattr(paint, k) for k in dir(paint) if not k.startswith('__')})
    if point in ('derivation', 'serializer'):
        setattr(proxy, {'derivation': 'rectangles', 'serializer': 'paint_group'}[point], boom)
        monkeypatch.setattr(writer, 'paint', proxy)
    else:
        target = {'plan': (semantic, 'plan_semantic_transition'), 'transaction': (writer, '_underline_paints'),
                  'owner_rebind': (owned, 'rebind'), 'semantic_rebind': (semantic, '_attach'),
                  'reopen': (writer, '_verify_candidate')}[point]
        monkeypatch.setattr(*target, boom)
    with pytest.raises(PdfError):
        writer.build_semantic_candidate(pdf, sidecar, edit(1, 1, 'A'), workspace=ul['root'] / 'work')
    assert files(pdf, sidecar) == before and candidates(ul['root']) == existing


def test_publication_failure_leaves_the_previous_bundle_untouched(ul, tmp_path, monkeypatch):
    previous = publication.publish_semantic_bundle(*ul['steps']['add'], tmp_path / 'previous')
    before = files(previous['pdf'], previous['sidecar'])
    candidate = writer.build_semantic_candidate(previous['pdf'], previous['sidecar'], remove(0, 0, 3),
                                                workspace=tmp_path / 'work')
    real = semantic.open_semantic_flow
    monkeypatch.setattr(semantic, 'open_semantic_flow',
                        lambda *a, **k: dict(status='needs_confirmation', reason='injected'))
    with pytest.raises(PdfError, match='injected'):
        publication.publish_semantic_bundle(candidate['pdf'], candidate['sidecar'], tmp_path / 'next')
    monkeypatch.setattr(semantic, 'open_semantic_flow', real)
    assert not (tmp_path / 'next').exists()
    assert files(previous['pdf'], previous['sidecar']) == before
    assert real(previous['pdf'], previous['sidecar'])['status'] == 'restored'
