"""Semantic underline contract (§27): read-only design evidence. No paint runtime.

Everything named `prototype` lives in this file only. It fixes the contract the
next (implementation) PR must meet on the shared-flow v3 single owned slot:

* P-SURF A: the underline group lives inside the existing owned source-output
  body, after the text group (`q BT … ET Q` then `q <fill> (x y m x y l x y l
  x y l h f)+ Q`); no new marker domain.
* P-SEM: `payload.decorations` (semantic record version 3), current-state only,
  `current:i` positional labels, logical [start, end) ranges with outside/outside
  boundary affinity, D remap, E add/remove/recipe, R refusals.
* P-REC: exact em-relative recipe; fill = current text fill.
* P-EMPTY: a decoration that has no visible character left terminates; empty text
  therefore terminates all decorations; nothing is ever revived.
* P-VER: v2 grammar unchanged; v3 current-body grammar + exact canonical
  text+paint body; owned paint declared to the Transaction.

Inputs are the stored sidecar, the current asset and the current page frame
(the same inputs as §26.4); actual PDFs are only written to `tmp_path` copies.
`pdfeditor/` is not modified and no runtime path accepts paint.
"""
from copy import deepcopy
from fractions import Fraction as F

import pymupdf
import pytest
from uniseg.graphemecluster import grapheme_cluster_boundaries

from pdfeditor import semantic_island as island
from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_writer as writer
from pdfeditor import source_ownership as owned
from pdfeditor import story_styles as styles
from pdfeditor import transaction
from pdfeditor.backend import PdfError
from pdfeditor.composition import _check_obstacles, _pixels_equal
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.editable import _seal
from pdfeditor.elements import inspect_element
from pdfeditor.model import Rect
from pdfeditor.operator_nesting import audit
from pdfeditor.paint_provenance import _load_catalog, interpreted_paints, prove_path_paint
from pdfeditor.selection import make_selection, resolve_selection, source_sha
from test_paint_reassessment import RECIPE as PR48_RECIPE
from test_paint_reassessment import chain, page_top, plan_of  # noqa: F401  (module fixture reused)
from test_paint_reassessment import underline as pr48_underline

# Caller-confirmed default candidate for this evidence only (not a standard): at 12 pt it is the
# PR #48 prototype's 13/10 pt offset and 7/10 pt thickness.
RECIPE = {'offset_em': '13/120', 'thickness_em': '7/120'}
KEYS = frozenset({'id', 'kind', 'start', 'end', 'start_affinity', 'end_affinity', 'recipe'})
ADD_KEYS = KEYS - {'id'}
RECIPE_KEYS = frozenset({'offset_em', 'thickness_em'})
FILL_ARITY = {'g': 1, 'rg': 3, 'k': 4}
RECT_SHAPE = (('m', 2), ('l', 2), ('l', 2), ('l', 2), ('h', 0), ('f', 0))


# ---------------------------------------------------------------- prototype P-SEM


def visible(text):
    return any(c not in ' \n' for c in text)


def decoration(start, end, recipe=RECIPE, index=0):
    return dict(id=f'current:{index}', kind='underline', start=start, end=end,
                start_affinity='outside', end_affinity='outside', recipe=dict(recipe))


def renumber(decorations):
    ordered = sorted(decorations, key=lambda d: (d['start'], d['end']))
    return [dict(d, id=f'current:{i}') for i, d in enumerate(ordered)]


def validate(text, decorations):
    """Prototype schema check of `payload.decorations` against the current text."""
    if not isinstance(decorations, list):
        raise PdfError('decorations must be an explicit list')
    limits = {0, len(text), *grapheme_cluster_boundaries(text)}
    previous = 0
    for index, d in enumerate(decorations):
        if not isinstance(d, dict) or set(d) != KEYS:
            raise PdfError('decoration needs exactly id, kind, start, end, affinities and recipe')
        if d['id'] != f'current:{index}':
            raise PdfError('decoration id is not its current canonical position')
        if d['kind'] != 'underline':
            raise PdfError('unsupported decoration kind')
        if d['start_affinity'] != 'outside' or d['end_affinity'] != 'outside':
            raise PdfError('v1 boundary affinity is outside/outside')
        s, e = d['start'], d['end']
        if (type(s) is not int or type(e) is not int or not 0 <= s < e <= len(text)
                or s not in limits or e not in limits):
            raise PdfError('decoration range must be ordered grapheme boundaries inside the text')
        if not visible(text[s:e]):
            raise PdfError('decoration range has no visible character')
        if s < previous:
            raise PdfError('decorations overlap or are not in canonical order')
        previous = e
        recipe = d['recipe']
        if not isinstance(recipe, dict) or set(recipe) != RECIPE_KEYS:
            raise PdfError('underline recipe needs exactly offset_em and thickness_em')
        offset, thickness = (semantic._exact(recipe[k]) for k in ('offset_em', 'thickness_em'))
        if not (0 <= offset <= 1 and 0 < thickness <= 1):
            raise PdfError('underline recipe is outside its exact bounds')


def remap(text, decorations, start, end, inserted):
    """D: one semantic edit {start, end, text}. Returns (next text, next decorations)."""
    n, shift = len(inserted), len(inserted) - (end - start)
    after = text[:start] + inserted + text[end:]
    kept = []
    for d in decorations:
        s, e = d['start'], d['end']
        members = [i for i in range(s, e) if i < start] + [i + shift for i in range(s, e) if i >= end]
        inside = (s <= start and end <= e) if start < end else s < start < e
        if inside:
            members += range(start, start + n)
        if not members:
            continue  # collapsed: terminated, never stored as zero-length
        lo, hi = min(members), max(members) + 1
        assert sorted(members) == list(range(lo, hi))
        if visible(after[lo:hi]):
            kept.append(dict(d, start=lo, end=hi))
        # else: no visible character left -> terminated (no dormant decoration)
    result = renumber(kept)
    validate(after, result)
    return after, result


def reinterpret(text, decorations, changes):
    """E: exactly one decoration action; never mixed with style/font/edge changes."""
    if not isinstance(changes, dict) or set(changes) != {'decorations'}:
        raise PdfError('a decoration change is its own explicit reinterpretation')
    action = changes['decorations']
    if not isinstance(action, dict) or len(action) != 1:
        raise PdfError('exactly one decoration action per request')
    (verb, item), = action.items()
    current = deepcopy(decorations)
    if verb == 'add':
        if not isinstance(item, dict) or set(item) != ADD_KEYS:
            raise PdfError('underline add needs exactly kind, start, end, affinities and recipe')
        current.append(dict(deepcopy(item), id='new'))
    elif verb in ('remove', 'recipe'):
        keys = {'id', 'start', 'end'} | ({'recipe'} if verb == 'recipe' else set())
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
            match[0]['recipe'] = deepcopy(item['recipe'])
    else:
        raise PdfError('unsupported decoration action: ' + str(verb))
    result = renumber(current)
    validate(text, result)
    return result


# ---------------------------------------------------------------- prototype P-REC geometry + serialization


def rectangles(plan, style, decorations):
    """Exact page y-down rectangles: decorations in canonical order, then lines top to bottom."""
    size = F(style['font_size'])
    rects = []
    for d in decorations:
        offset, thickness = (F(d['recipe'][k]) * size for k in ('offset_em', 'thickness_em'))
        for line in plan['lines']:
            glyphs = [g for g in plan['emitted']
                      if line['start'] <= g['offset'] < line['end'] and d['start'] <= g['offset'] < d['end']]
            while glyphs and glyphs[0]['text'] == ' ':
                glyphs.pop(0)
            while glyphs and glyphs[-1]['text'] == ' ':
                glyphs.pop()
            if not glyphs:
                continue
            baselines = {F(g['origin'][1]) for g in glyphs}  # line baseline + rise
            if len(baselines) != 1:
                raise PdfError('underline line has more than one glyph baseline')
            upper = baselines.pop() + offset
            left = F(glyphs[0]['origin'][0])
            right = F(glyphs[-1]['origin'][0]) + F(glyphs[-1]['advance'])
            box_top = F(line['baseline']) - F(line['ascent'])
            box_bottom = F(line['baseline']) + F(line['descent'])
            if not (box_top <= upper and upper + thickness <= box_bottom):
                raise PdfError('underline exceeds its line box')
            if not left < right:
                raise PdfError('underline has no positive width')
            rects.append((left, upper, right, upper + thickness))
    return rects


def paint_group(rects, fill, top):
    """Canonical paint group bytes (empty when there is nothing to paint)."""
    if not rects:
        return b''
    op, values = fill
    if op not in FILL_ARITY or len(values) != FILL_ARITY[op]:
        raise PdfError('underline needs the text device gray/RGB/CMYK fill')
    d, top = island.decimal, F(top)
    out = 'q ' + ' '.join(d(F(str(v))) for v in values) + f' {op}\n'
    for left, upper, right, lower in rects:
        x0, x1, y0, y1 = d(left), d(right), d(top - upper), d(top - lower)
        if F(x0) >= F(x1) or F(y1) >= F(y0):
            raise PdfError('zero-area underline after the output decimal policy')
        out += f'{x0} {y0} m {x1} {y0} l {x1} {y1} l {x0} {y1} l h f\n'
    return (out + 'Q\n').encode('ascii')


# ---------------------------------------------------------------- prototype P-VER current-body grammar/verifier


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def body_grammar(body, text_grammar=owned.grammar):
    """v3 current-body grammar: one canonical text group, then at most one underline group.

    The text group is checked by the unchanged v2 `source_ownership.grammar`. Returns
    (text group bytes, paint group bytes). Canonicity is a separate exact byte check.
    """
    if b'%' in body:
        raise PdfError('comments are not allowed in source output body')
    ops = list(operators(body))
    if audit(body)['violations']:
        raise PdfError('invalid v3 source output body grammar')
    closes = [i for i, op in enumerate(ops) if op.name == 'Q']
    if not closes:
        raise PdfError('v3 body needs a text group')
    split = ops[closes[0] + 1].start if closes[0] + 1 < len(ops) else len(body)
    text, paint = body[:split], body[split:]
    text_grammar(text)  # the unchanged v2 grammar (bound at import time)
    if [op.name for op in operators(text)].count('BT') != 1:
        raise PdfError('v3 body has exactly one text group')
    if not paint:
        return text, paint
    ops = list(operators(paint))
    names = [op.name for op in ops]
    if (len(ops) < 9 or names[0] != 'q' or ops[0].args or names[-1] != 'Q' or ops[-1].args
            or names[1] not in FILL_ARITY or len(ops[1].args) != FILL_ARITY[names[1]]
            or not all(_number(v) for v in ops[1].args) or (len(ops) - 3) % 6):
        raise PdfError('invalid v3 underline group grammar')
    for k in range(2, len(ops) - 1, 6):
        rect = ops[k:k + 6]
        if [(op.name, len(op.args)) for op in rect] != list(RECT_SHAPE) or not all(
                _number(v) for op in rect for v in op.args):
            raise PdfError('invalid v3 underline rectangle grammar')
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = ([F(str(v)) for v in op.args] for op in rect[:4])
        if not (y1 == y0 and x2 == x1 and y3 == y2 and x3 == x0 and x0 < x1 and y2 < y0):
            raise PdfError('v3 underline path is not one axis-aligned rectangle')
    return text, paint


def revision(chain, name):
    state, plan = plan_of(chain[name]['sidecar'])
    slot = next(iter(state['slots'].values()))
    pdf = chain[name]['pdf']
    with pymupdf.open(pdf) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    span = owned.inventory(data)[slot['source_output']['marker_id']]
    return dict(name=name, pdf=pdf, state=state, slot=slot, payload=slot['semantic']['payload'], plan=plan,
                top=page_top(pdf), fill=styles.properties(semantic._source_entry(state, slot))['fill'],
                marker=slot['source_output']['marker_id'], body=data[span[1]:span[2]])


def canonical_paint(rev, decorations):
    return paint_group(rectangles(rev['plan'], rev['payload']['style'], decorations), rev['fill'], rev['top'])


def painted(rev, out, paint, *, first=False, before=b'', after=b''):
    """A tmp copy of the revision with `paint` placed in the owned body (not a published revision)."""
    with pymupdf.open(rev['pdf']) as document:
        xref = document[0].get_contents()[-1]
        data = document.xref_stream(xref)
        start, body_start, body_end, end = owned.inventory(data)[rev['marker']]
        body = data[body_start:body_end]
        new_body = paint + body if first else body + paint
        document.update_stream(xref, data[:start] + before + data[start:body_start] + new_body
                               + data[body_end:end] + after + data[end:])
        document.save(out)
    return out


def verify_current_body(pdf, rev, decorations):
    """Prototype v3 current-body verifier over an actual PDF; returns the unchanged witness shape."""
    content = ContentPage(pdf, 1)
    try:
        data = content.streams[-content.page.xref]
        spans = owned.inventory(data)
        if set(spans) != {rev['marker']}:
            raise PdfError('source output marker inventory differs from the owned slot')
        span = spans[rev['marker']]
        text, paint = body_grammar(data[span[1]:span[2]])
        before = owned.context(owned._boundary(content, span[1]))
        after = owned.context(owned._boundary(content, span[2]))
        if not before == after == rev['slot']['source_output']['current']['entry_context_sha256']:
            raise PdfError('semantic island entry/exit context differs')
        owned.containment(content, rev['slot']['binding']['paragraph'], span)
        validate(rev['payload']['text'], decorations)
        if text != rev['body']:
            raise PdfError('text group is not the canonical text body')
        if paint != canonical_paint(rev, decorations):
            raise PdfError('underline group is not the canonical serialization of the decorations')
        return dict(program_sha256=owned.sha(data), range=[span[0], span[3]],
                    block_sha256=owned.sha(data[span[0]:span[3]]), entry_context_sha256=before)
    finally:
        content.close()


def derive(rev, text=None, **style):
    """Pure exact plan for a modified payload over the same slot, region and asset (no PDF)."""
    payload = deepcopy(rev['payload'])
    if text is not None:
        payload['text'] = text
    payload['style'].update(style)
    font = semantic._asset(rev['state'], rev['slot'], payload, rev['slot']['semantic'].get('current'))
    try:
        return semantic._derive(rev['state'], rev['slot'], payload, font)[1], payload
    finally:
        font.font.close()


def inks(rev):
    font = semantic._asset(rev['state'], rev['slot'], rev['payload'], rev['slot']['semantic'].get('current'))
    try:
        return writer._ink_rects(font, rev['payload']['style'], rev['plan']['emitted'])
    finally:
        font.font.close()


def as_rects(rects):
    return [Rect(float(a), float(b), float(c), float(d)) for a, b, c, d in rects]


def full(rev):
    return [decoration(0, len(rev['payload']['text']))]


# ---------------------------------------------------------------- 1, 2: grammar split


def test_v3_grammar_accepts_the_canonical_text_and_underline_body(chain, tmp_path):
    rev = revision(chain, 'save3')
    decorations = [decoration(0, 1), decoration(2, 3, index=1)]
    paint = canonical_paint(rev, decorations)
    pdf = painted(rev, tmp_path / 'painted.pdf', paint)
    witness = verify_current_body(pdf, rev, decorations)
    # The unchanged witness shape covers the paint: same fields, new block/program SHA.
    assert set(witness) == set(rev['slot']['source_output']['current'])
    assert witness['entry_context_sha256'] == rev['slot']['source_output']['current']['entry_context_sha256']
    assert witness['block_sha256'] != rev['slot']['source_output']['current']['block_sha256']
    assert paint == (b'q 0 g\n20 58.7 m 27.2 58.7 l 27.2 58 l 20 58 l h f\n'
                     b'34.4 58.7 m 41.6 58.7 l 41.6 58 l 34.4 58 l h f\nQ\n')
    # A different paint byte is a different block witness: no paint-specific witness field is needed.
    moved = paint.replace(b'27.2 58.7 l 27.2 58 l', b'27.3 58.7 l 27.3 58 l')
    tampered = painted(rev, tmp_path / 'tampered.pdf', moved)
    with pytest.raises(PdfError, match='canonical serialization'):
        verify_current_body(tampered, rev, decorations)
    with pymupdf.open(tampered) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    start, _, _, end = owned.inventory(data)[rev['marker']]
    assert owned.sha(data[start:end]) != witness['block_sha256']


def test_unchanged_v2_grammar_rejects_the_same_painted_body(chain, tmp_path):
    rev = revision(chain, 'save3')
    paint = canonical_paint(rev, full(rev))
    pdf = painted(rev, tmp_path / 'painted.pdf', paint)
    assert owned.BODY_OPERATORS == frozenset('q Q BT ET Tf Tz Tc Tw Ts Tm Tj TJ g rg k'.split())
    owned.grammar(rev['body'])
    with pytest.raises(PdfError, match='source output body grammar'):
        owned.grammar(rev['body'] + paint)
    body_grammar(rev['body'] + paint)
    # Even with the owner witness and PDF hash resealed to the painted revision, v2 validation refuses.
    state = deepcopy(rev['state'])
    state.pop('model_sha256')
    state['pdf_sha256'] = source_sha(pdf)
    state['slots']['slot-0']['source_output']['current'] = verify_current_body(pdf, rev, full(rev))
    with pytest.raises(PdfError, match='source output body grammar'):
        owned.validate(pdf, _seal(state))
    assert semantic.open_semantic_flow(pdf, rev['state'])['status'] == 'needs_confirmation'


def test_current_runtime_refuses_decorations_and_a_version_3_record(chain):
    """Nothing is implicitly accepted before the implementation: no v2 payload extension, no v3 record."""
    rev = revision(chain, 'save3')
    with pytest.raises(PdfError, match='exactly text, style, font'):
        semantic._payload(dict(rev['payload'], decorations=full(rev)), rev['slot'])
    slot = deepcopy(rev['slot'])
    slot['semantic']['version'] = 3
    with pytest.raises(PdfError, match='no valid semantic payload'):
        semantic._record(slot)


# ---------------------------------------------------------------- 3–6: canonical bytes and the em recipe


@pytest.mark.parametrize('group', [
    ('save1', 'save2', 'save3'), ('edit', 'edit_noop'), ('scaled', 'scaled_noop1', 'scaled_noop2'),
    ('tw', 'tw_noop'), ('edge', 'edge_noop'), ('font_b', 'font_b_noop'),
])
def test_canonical_underline_bytes_are_a_noop_fixed_point(chain, group):
    def bodies(name):
        rev = revision(chain, name)
        n = len(rev['payload']['text'])
        return [canonical_paint(rev, d) for d in ([decoration(0, n)], [decoration(0, 1)],
                                                   [decoration(0, 1), decoration(n - 1, n, index=1)])]
    results = [bodies(name) for name in group]
    assert all(r == results[0] for r in results)
    assert all(body.startswith(b'q 0 g\n') and body.endswith(b' h f\nQ\n') for body in results[0])


def test_em_recipe_at_12pt_reproduces_the_pr48_prototype_coordinates(chain):
    rev = revision(chain, 'save3')
    numbers = lambda data: [str(op.args) for op in operators(data) if op.name in ('m', 'l')]
    old = pr48_underline(rev['plan'], rev['top'], 0, 3, PR48_RECIPE)
    assert numbers(old) == numbers(canonical_paint(rev, full(rev)))


def test_em_recipe_follows_font_size_rise_and_ignores_horizontal_scale(chain):
    base, scaled = revision(chain, 'edit_noop'), revision(chain, 'scaled')  # 12 pt -> 37/3 pt, scale 4/5
    for rev in (base, scaled):
        size = F(rev['payload']['style']['font_size'])
        (left, upper, right, lower), = rectangles(rev['plan'], rev['payload']['style'], full(rev))
        baseline = F(rev['plan']['lines'][0]['baseline'])
        assert upper - baseline == F(13, 120) * size and lower - upper == F(7, 120) * size
    assert F(scaled['payload']['style']['horizontal_scale']) == F(4, 5)  # vertical recipe is not scaled
    # rise -1 (page y-down: 1 pt up) moves the glyph baseline and the underline by exactly -1.
    plan, payload = derive(base, rise='-1')
    (_, upper, _, lower), = rectangles(plan, payload['style'], full(base))
    (_, upper0, _, lower0), = rectangles(base['plan'], base['payload']['style'], full(base))
    assert (upper - upper0, lower - lower0) == (-1, -1)


def test_endpoints_follow_tracking_tw_edges_and_font(chain):
    """Exact endpoint arithmetic independent of the plan: left = first visible origin, right = last origin + advance."""
    width = lambda units, size, scale=F(1): F(units, 1000) * size * scale
    size, scale, track = F(37, 3), F(4, 5), F(1, 4)
    a = width(600, size, scale)                       # 'AA B', font A, 37/3 pt, scale 4/5, tracking 1/4
    expected = {
        'save3': (F(20), 20 + 2 * F(36, 5) + F(36, 5)),
        'scaled': (F(20), 20 + 3 * (a + track) + a),
        'tw': (F(20), 20 + 3 * (a + track) + F(6, 5) + a),
        'edge': (F(20), 20 + 3 * (a + track) + F(6, 5) + a),
        'font_b': (F(20), 20 + 3 * (width(500, size, scale) + track) + F(6, 5) + width(500, size, scale)),
    }
    for name, (left, right) in expected.items():
        rev = revision(chain, name)
        (x0, _, x1, _), = rectangles(rev['plan'], rev['payload']['style'], full(rev))
        assert (x0, x1) == (left, right), name
    # A range ending mid-line keeps the last glyph's tracking: [0, 1) of 'AA B' at tracking 1/4.
    rev = revision(chain, 'scaled')
    (_, _, x1, _), = rectangles(rev['plan'], rev['payload']['style'], [decoration(0, 1)])
    assert x1 == 20 + a + track
    assert float(expected['font_b'][1]) == pytest.approx(41.683333, abs=1e-6)


def test_tw_and_confirmed_edge_give_identical_underline_bytes(chain):
    tw, edge = revision(chain, 'tw'), revision(chain, 'edge')
    assert tw['payload']['style']['word_spacing'] == '6/5' and edge['payload']['edges']
    for ranges in ([(0, 4)], [(0, 3)], [(2, 4)], [(0, 1), (3, 4)]):
        decorations = [decoration(a, b, index=i) for i, (a, b) in enumerate(ranges)]
        assert canonical_paint(tw, decorations) == canonical_paint(edge, decorations)


def test_font_change_keeps_the_recipe_and_its_vertical_geometry(chain):
    """The recipe is stored caller authority; a font asset change never re-derives it from font tables."""
    before, after = revision(chain, 'edge_noop'), revision(chain, 'font_b')
    assert before['payload']['font'] != after['payload']['font']
    (_, upper0, _, lower0), = rectangles(before['plan'], before['payload']['style'], full(before))
    (_, upper1, _, lower1), = rectangles(after['plan'], after['payload']['style'], full(after))
    assert (upper0, lower0) == (upper1, lower1)


def test_line_box_containment_is_exact_and_size_invariant(chain):
    rev = revision(chain, 'save3')  # hhea descent 200/1000 em
    deep = dict(offset_em='3/20', thickness_em='1/10')  # 1/4 em > 1/5 em
    edge = dict(offset_em='1/10', thickness_em='1/10')  # exactly 1/5 em: allowed
    for size in ('12', '37/3', '24'):
        plan, payload = derive(rev, font_size=size)
        with pytest.raises(PdfError, match='line box'):
            rectangles(plan, payload['style'], [decoration(0, 3, deep)])
        rectangles(plan, payload['style'], [decoration(0, 3, edge)])


# ---------------------------------------------------------------- 7, 8: P-SEM transitions and P-EMPTY


@pytest.mark.parametrize('ranges, edit, expected', [
    ([(1, 3)], (0, 0, 'A'), [(2, 4)]),          # insert before: shift
    ([(0, 3)], (1, 1, 'B'), [(0, 4)]),          # insert inside: included
    ([(1, 3)], (1, 1, 'A'), [(2, 4)]),          # insert at start: outside (start affinity)
    ([(0, 2)], (2, 2, 'B'), [(0, 2)]),          # insert at end: outside (end affinity)
    ([(0, 4)], (1, 2, ''), [(0, 3)]),           # delete inside: shrink
    ([(0, 4)], (0, 1, ''), [(0, 3)]),           # delete start endpoint: shrink
    ([(0, 4)], (3, 4, ''), [(0, 3)]),           # delete end endpoint: shrink
    ([(1, 4)], (0, 2, ''), [(0, 2)]),           # delete across start: shrink
    ([(0, 2)], (0, 2, ''), []),                 # delete the whole range: terminate
    ([(0, 2)], (0, 2, 'BA'), [(0, 2)]),         # replace exactly the range: kept (existing convention)
    ([(1, 4)], (0, 2, 'BB'), [(2, 4)]),         # replace across start: inserted text outside
    ([(0, 2)], (1, 3, 'A'), [(0, 1)]),          # replace across end: inserted text outside
    ([(0, 1)], (2, 4, 'A'), [(0, 1)]),          # edit after: unchanged
    ([(0, 3)], (0, 1, ''), [(0, 2)]),           # 'A B' -> ' B'
    ([(1, 3)], (1, 2, ''), []),                 # range 'A ' loses its A -> ' ': no visible character, terminate
    ([(0, 2), (2, 4)], (1, 3, 'A'), [(0, 1), (2, 3)]),  # touching stays separate, never merged
    ([(0, 2), (3, 4)], (0, 4, ''), []),         # empty text: every decoration terminates
])
def test_d_remap_policy(ranges, edit, expected):
    text = 'AA B' if max(b for _, b in ranges) <= 4 else None
    decorations = renumber([decoration(a, b) for a, b in ranges])
    after, result = remap(text, decorations, *edit)
    assert [(d['start'], d['end']) for d in result] == expected
    assert [d['id'] for d in result] == [f'current:{i}' for i in range(len(expected))]


def test_empty_text_terminates_decorations_and_regrow_never_revives(chain):
    text, decorations = 'AA B', renumber([decoration(0, 2), decoration(3, 4)])
    empty, none = remap(text, decorations, 0, 4, '')
    assert (empty, none) == ('', [])
    regrown, still = remap(empty, none, 0, 0, 'AA B')
    assert (regrown, still) == ('AA B', [])  # only an explicit E add can underline again
    # decorations == [] means no paint group: the v3 body is byte-identical to the L2 text-only body.
    rev = revision(chain, 'save3')
    assert canonical_paint(rev, []) == b''
    assert body_grammar(rev['body']) == (rev['body'], b'')


def test_newline_and_wrap_split_rectangles_per_line_without_painting_the_newline(chain):
    rev = revision(chain, 'save3')
    plan, payload = derive(rev, text='AB\nBA ')
    rects = rectangles(plan, payload['style'], [decoration(1, 6)])
    assert len(rects) == 2 and rects[0][1] < rects[1][1]  # one per line, top to bottom, never joined
    first, second = plan['lines']
    assert rects[0][0] == 20 + F(36, 5) and rects[0][2] == 20 + 2 * F(36, 5)   # B only; newline not painted
    assert rects[1][0] == 20 and rects[1][2] == 20 + 2 * F(36, 5)              # 'BA'; trailing space excluded
    assert first['end'] == 2 and second['start'] == 3


# ---------------------------------------------------------------- E / R policy


def add(start, end, **extra):
    item = dict(kind='underline', start=start, end=end, start_affinity='outside', end_affinity='outside',
                recipe=dict(RECIPE))
    item.update(extra)
    return {'decorations': {'add': item}}


def test_e_add_remove_and_recipe_change():
    text = 'AA B'
    one = reinterpret(text, [], add(3, 4))
    two = reinterpret(text, one, add(0, 2))
    assert [(d['id'], d['start'], d['end']) for d in two] == [('current:0', 0, 2), ('current:1', 3, 4)]
    other = dict(offset_em='1/10', thickness_em='1/20')
    changed = reinterpret(text, two, {'decorations': {'recipe': dict(id='current:1', start=3, end=4, recipe=other)}})
    assert changed[1]['recipe'] == other and changed[0] == two[0]
    removed = reinterpret(text, changed, {'decorations': {'remove': dict(id='current:0', start=0, end=2)}})
    assert [(d['id'], d['start'], d['end']) for d in removed] == [('current:0', 3, 4)]


@pytest.mark.parametrize('changes, reason', [
    (add(3, 4, kind='strikeout'), 'unsupported decoration kind'),
    (add(3, 4, id='current:7'), 'exactly kind'),                                   # caller-chosen ID
    (add(3, 4, source_id='path-1'), 'exactly kind'),                               # source path adoption
    (add(3, 4, recipe='auto'), 'offset_em and thickness_em'),                      # hidden recipe inference
    (add(3, 4, recipe=dict(offset_em='0.1', thickness_em='7/120')), 'canonical exact rational'),
    (add(3, 4, recipe=dict(offset_em='13/120', thickness_em='0')), 'exact bounds'),
    (add(3, 4, start_affinity='inside'), 'outside/outside'),
    (add(0, 2), 'overlap'),                                                        # overlaps current:0 [1, 3)
    (add(2, 2), 'ordered grapheme boundaries'),                                    # zero-length
    (add(2, 4), 'overlap'),
    (add(2, 3), 'no visible character'),
    ({'decorations': {'add': dict(kind='underline', start=0, end=1, start_affinity='outside',
                                  end_affinity='outside', recipe=dict(RECIPE))}, 'font_size': '13'}, 'its own'),
    ({'decorations': {'remove': dict(id='current:0', start=0, end=2)}}, 'not the current decoration'),  # stale
    ({'decorations': {'remove': dict(id='current:1', start=1, end=3)}}, 'not the current decoration'),  # old ID
    ({'decorations': {'revive': dict(id='current:0')}}, 'unsupported decoration action'),
    ({'decorations': {'adopt': dict(source_id='path-1')}}, 'unsupported decoration action'),
    ({'decorations': {'recipe': dict(id='current:0', start=1, end=3, recipe=dict(RECIPE))}}, 'must differ'),
])
def test_e_refusals(changes, reason):
    current = reinterpret('AA B', [], add(1, 3))
    with pytest.raises(PdfError, match=reason):
        reinterpret('AA B', current, changes)


def test_all_space_and_newline_only_ranges_are_refused_not_dormant():
    for text, start, end in (('A  B', 1, 3), ('A\nB', 1, 2), ('A \nB', 1, 3)):
        with pytest.raises(PdfError, match='no visible character'):
            reinterpret(text, [], add(start, end))


# ---------------------------------------------------------------- 9: tamper matrix (prototype verifier)


@pytest.fixture(scope='module')
def tamper_base(chain):
    rev = revision(chain, 'save3')
    decorations = [decoration(0, 1), decoration(2, 3, index=1)]
    return rev, decorations, canonical_paint(rev, decorations)


@pytest.mark.parametrize('name, change', [
    ('range', lambda d: (d.pop(), d[0].update(end=3))),
    ('recipe', lambda d: d[0]['recipe'].update(thickness_em='1/20')),
    ('kind', lambda d: d[0].update(kind='strikeout')),
    ('id', lambda d: d[0].update(id='current:5')),
    ('order', lambda d: d.reverse()),
    ('affinity', lambda d: d[0].update(end_affinity='inside')),
    ('missing decoration', lambda d: d.pop()),
    ('extra decoration', lambda d: d.append(decoration(1, 2, index=2))),
])
def test_semantic_decoration_tamper_is_refused(tamper_base, tmp_path, name, change):
    rev, decorations, paint = tamper_base
    pdf = painted(rev, tmp_path / 'painted.pdf', paint)
    verify_current_body(pdf, rev, decorations)
    tampered = deepcopy(decorations)
    change(tampered)
    with pytest.raises(PdfError):
        verify_current_body(pdf, rev, tampered)


@pytest.mark.parametrize('name, body', [
    ('geometry', lambda p: p.replace(b'27.2 58 l', b'27.2 57.9 l')),
    ('fill', lambda p: p.replace(b'q 0 g', b'q 1 0 0 rg')),
    ('extra path', lambda p: p.replace(b'Q\n', b'50 58.7 m 60 58.7 l 60 58 l 50 58 l h f\nQ\n')),
    ('missing path', lambda p: b''.join(p.splitlines(keepends=True)[:2] + p.splitlines(keepends=True)[3:])),
    ('reordered rectangles', lambda p: b''.join([p.splitlines(keepends=True)[i] for i in (0, 2, 1, 3)])),
    ('re operator', lambda p: b'q 0 g\n20 58 7.2 0.7 re f\n34.4 58 7.2 0.7 re f\nQ\n'),
    ('stroke', lambda p: p.replace(b'h f\n', b'h S\n')),
    ('cm', lambda p: p.replace(b'q 0 g', b'q 1 0 0 1 0 0 cm 0 g')),
    ('ExtGState', lambda p: p.replace(b'q 0 g', b'q /GS0 gs 0 g')),
    ('nested q', lambda p: p.replace(b'q 0 g\n', b'q 0 g\nq\n').replace(b'Q\n', b'Q\nQ\n')),
    ('curve', lambda p: p.replace(b'27.2 58 l', b'27.2 58.5 27.2 58.2 27.2 58 c')),
    ('clip', lambda p: p.replace(b'h f\n', b'h W n\n', 1)),
    ('empty group', lambda p: b'q 0 g\nQ\n'),
    ('comment', lambda p: p.replace(b'Q\n', b'% x\nQ\n')),
    ('foreign path moved into the owner', lambda p: b'q 0 g\n100 100 m 110 100 l 110 99 l 100 99 l h f\nQ\n'),
])
def test_current_body_paint_tamper_is_refused(tamper_base, tmp_path, name, body):
    rev, decorations, paint = tamper_base
    tampered = body(paint)
    assert tampered != paint
    pdf = painted(rev, tmp_path / 'tampered.pdf', tampered)
    with pytest.raises((PdfError, KeyError)):  # an unknown resource fails the interpreter itself
        verify_current_body(pdf, rev, decorations)


def test_trimmed_boundary_spaces_are_not_physically_witnessed(tamper_base):
    """Stated limit (like Tw vs edge): ranges differing only in trimmed boundary spaces paint the same bytes."""
    rev, _, _ = tamper_base
    assert canonical_paint(rev, [decoration(2, 3)]) == canonical_paint(rev, [decoration(1, 3)])


def test_paint_before_text_is_not_the_canonical_order(tamper_base, tmp_path):
    rev, decorations, paint = tamper_base
    pdf = painted(rev, tmp_path / 'paint-first.pdf', paint, first=True)
    with pytest.raises(PdfError):
        verify_current_body(pdf, rev, decorations)


# ---------------------------------------------------------------- 10: removed/new paint area


PAIRS = [  # (old revision, old ranges, new revision, new ranges)
    ('save3', 'full', 'edit', 'full'),                 # growth
    ('edit_noop', 'full', 'scaled', 'full'),           # font size + scale + tracking
    ('scaled_noop2', 'full', 'tw', 'full'),            # Tw
    ('tw', 'full', 'edge', 'full'),                    # Tw -> edge (identical)
    ('edge_noop', 'full', 'font_b', 'full'),           # font change
    ('save3', 'full', 'save3', []),                    # E remove
    ('save3', [], 'save3', 'full'),                    # E add
    ('save3', 'full', 'save3', [(2, 3)]),              # range shrink
]


def _state(rev, ranges):
    return full(rev) if ranges == 'full' else [decoration(a, b, index=i) for i, (a, b) in enumerate(ranges)]


@pytest.mark.parametrize('old_name, old_ranges, new_name, new_ranges', PAIRS)
def test_old_and_new_rectangles_bound_every_changed_pixel(chain, tmp_path, old_name, old_ranges, new_name, new_ranges):
    old, new = revision(chain, old_name), revision(chain, new_name)
    old_d, new_d = _state(old, old_ranges), _state(new, new_ranges)
    old_rects = as_rects(rectangles(old['plan'], old['payload']['style'], old_d))
    new_rects = as_rects(rectangles(new['plan'], new['payload']['style'], new_d))
    a = painted(old, tmp_path / 'old.pdf', canonical_paint(old, old_d))
    b = painted(new, tmp_path / 'new.pdf', canonical_paint(new, new_d))
    glyphs = inks(old) + inks(new)
    with pymupdf.open(a) as first, pymupdf.open(b) as second:
        assert _pixels_equal(first[0], second[0], glyphs + old_rects + new_rects)
        if not new_rects:   # removal: the removed rectangles are required in the planned area
            assert not _pixels_equal(first[0], second[0], glyphs + new_rects)
        if not old_rects:   # first add: the new rectangles are required
            assert not _pixels_equal(first[0], second[0], glyphs + old_rects)
        if old_name == new_name and old_rects and new_rects:   # shrink: the old rectangles are required
            assert not _pixels_equal(first[0], second[0], glyphs + new_rects)


# ---------------------------------------------------------------- 11: z-order


def test_text_then_paint_is_pixel_equivalent_and_keeps_the_text_group_a_byte_prefix(chain, tmp_path):
    rev = revision(chain, 'save3')
    paint = canonical_paint(rev, full(rev))
    text_first = painted(rev, tmp_path / 'text-first.pdf', paint)
    paint_first = painted(rev, tmp_path / 'paint-first.pdf', paint, first=True)
    (rect,) = as_rects(rectangles(rev['plan'], rev['payload']['style'], full(rev)))
    b_ink = inks(rev)[-1]
    assert rect.intersects(b_ink)  # B's descender crosses the underline, so the order is actually exercised
    assert all(not rect.intersects(ink) for ink in inks(rev)[:-1])  # no other glyph is touched
    with pymupdf.open(text_first) as one, pymupdf.open(paint_first) as two:
        assert one[0].get_pixmap(dpi=144, alpha=False).samples == two[0].get_pixmap(dpi=144, alpha=False).samples
        trace = lambda page: [(c[0], tuple(round(v, 4) for v in c[2]))
                              for s in page.get_texttrace() for c in s['chars']]
        assert trace(one[0]) == trace(two[0])
    # Text-first keeps the text group (and the island anchors, which are offsets into it) a byte prefix.
    with pymupdf.open(text_first) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    _, start, end, _ = owned.inventory(data)[rev['marker']]
    assert data[start:end] == rev['body'] + paint


# ---------------------------------------------------------------- P-VER: owned vs foreign paint (O1)


def test_owned_paint_is_identified_by_the_catalog_between_foreign_paint(chain, tmp_path):
    rev = revision(chain, 'save3')
    decorations = [decoration(0, 1), decoration(2, 3, index=1)]
    pdf = painted(rev, tmp_path / 'foreign.pdf', canonical_paint(rev, decorations),
                  before=b' q 0 0 1 rg 100 50 10 5 re f Q', after=b'q 1 0 0 rg 120 50 10 5 re f Q\n')
    events = [e for e in interpreted_paints(str(pdf), 1)['events'] if e['kind'] != 'fill-text']
    bounds = [[round(v, 3) for v in e['bounds']] for e in events]
    expected = [[round(float(v), 3) for v in r] for r in rectangles(rev['plan'], rev['payload']['style'], decorations)]
    assert bounds == [[100, 205, 110, 210], *expected, [120, 205, 130, 210]]  # foreign, owned (in order), foreign
    catalog, _ = _load_catalog(str(pdf), 1)
    with pymupdf.open(pdf) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    _, start, end, _ = owned.inventory(data)[rev['marker']]
    inside, outside = [], []
    for path in catalog['paths']:
        proof = prove_path_paint(str(pdf), 1, path['id'], catalog=catalog)
        assert proof['status'] == 'proven' and len(proof['paint_indices']) == 1
        a, b = path['merged_range']
        (inside if start <= a and b <= end else outside).append(proof['paint_indices'][0])
    all_events = interpreted_paints(str(pdf), 1)['events']
    assert [[round(v, 3) for v in all_events[i]['bounds']] for i in inside] == expected
    assert len(outside) == 2 and not set(inside) & set(outside)


def test_existing_obstacle_check_would_treat_old_owned_paint_as_foreign(chain, tmp_path):
    """Obligation: the v3 writer must exclude its own old island paint from obstacles (and nothing else)."""
    rev = revision(chain, 'save3')
    glyph_inks = inks(rev)
    bounds = glyph_inks[0]
    for ink in glyph_inks[1:]:
        bounds = bounds.union(ink)

    def check(pdf, rects):
        selection = make_selection(pdf, glyph_ids=[0, 1, 2], explicit_width=150)
        content = ContentPage(pdf, 1)
        try:
            _check_obstacles(content, {0, 1, 2}, resolve_selection(pdf, selection), rects, bounds)
        finally:
            content.close()

    check(rev['pdf'], glyph_inks)
    owned_paint = painted(rev, tmp_path / 'owned.pdf', canonical_paint(rev, full(rev)))
    with pytest.raises(PdfError, match='filled vector'):
        check(owned_paint, glyph_inks)  # the island's own old underline vs the B descender ink
    # Foreign paint stays an obstacle for a new underline, and only where it is actually hit.
    new_rects = as_rects(rectangles(rev['plan'], rev['payload']['style'], full(rev)))
    near = painted(rev, tmp_path / 'near.pdf', b'', before=b' q 0 0 1 rg 22 58.2 4 0.3 re f Q')
    far = painted(rev, tmp_path / 'far.pdf', b'', before=b' q 0 0 1 rg 100 50 10 5 re f Q')
    check(near, glyph_inks)
    with pytest.raises(PdfError, match='filled vector'):
        check(near, glyph_inks + new_rects)
    check(far, glyph_inks + new_rects)


def test_v2_element_observation_infers_a_relation_for_owned_paint(chain, tmp_path):
    """Obligation: inside the owned span a path is owned by the owner, never an inferred element relation."""
    rev = revision(chain, 'save3')
    pdf = painted(rev, tmp_path / 'painted.pdf', canonical_paint(rev, full(rev)))
    element = inspect_element(pdf, make_selection(pdf, glyph_ids=[0, 1, 2], explicit_width=150))
    (path,) = element['paths']
    assert path['relationship']['hypothesis'] == 'decoration_candidate'
    assert path['relationship']['requires_confirmation'] is True
    with pymupdf.open(pdf) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    _, start, end, _ = owned.inventory(data)[rev['marker']]
    a, b = path['source']['merged_range']
    assert start <= a < b <= end


# ---------------------------------------------------------------- which runtime gates a painted writer candidate hits


def test_only_the_named_gates_refuse_a_painted_writer_candidate(chain, tmp_path, monkeypatch):
    """Design evidence for the v2/v3 split, with in-process patches only (undone after the test).

    The L2 writer is made to append a full-range canonical underline group. Then:
    1. with the v2 grammar call replaced by the prototype v3 grammar, the candidate is refused by
       exactly one more gate, the Transaction non-text paint plan (owned paint is not declared);
    2. once that paint is declared, every other part of the unchanged shared-flow/semantic stack
       (creation evidence, open_editable binding, generated fonts, placement, containment, context,
       exact canonical check) accepts the painted candidate;
    3. that candidate's slot binding records the owned path as an inferred decoration relation;
    4. the next save from it is refused by the obstacle check (its own old underline is a foreign
       "filled vector"), and the next edit by the Transaction paint plan (old paint not declared).
    Each refusal is a named P-VER obligation in §27; nothing else needed to change.
    """
    text_body = island.body

    def body(style, emitted, **kwargs):
        data, anchors = text_body(style, emitted, **kwargs)
        plan = dict(lines=[dict(start=0, end=len(emitted) + 1, baseline=emitted[0]['origin'][1],
                                ascent='1000', descent='1000')], emitted=[dict(g) for g in emitted])
        rects = rectangles(plan, style, [decoration(0, max(g['offset'] for g in emitted) + 1)])
        return data + paint_group(rects, kwargs['fill'], kwargs['page_top']), anchors

    rev = revision(chain, 'save3')
    monkeypatch.setattr(island, 'body', body)
    monkeypatch.setattr(owned, 'grammar', body_grammar)
    with pytest.raises(PdfError, match='saved non-text paint differs from the transaction plan'):
        writer.build_semantic_candidate(rev['pdf'], chain['save3']['sidecar'], dict(operation='save'),
                                        workspace=tmp_path / 'refused')
    verify = transaction.Transaction._verify

    def declared(self, document, kept, new, paints, *args):
        for number in paints:  # stand-in for the Plan declaring its owned island paint
            observed = interpreted_paints(document.tobytes(), number)['events']
            paints[number] = list(paints[number]) + [{k: v for k, v in e.items() if k != 'seqno'}
                                                     for e in observed if e['kind'] == 'fill-path']
        return verify(self, document, kept, new, paints, *args)

    with monkeypatch.context() as patch:
        patch.setattr(transaction.Transaction, '_verify', declared)
        candidate = writer.build_semantic_candidate(rev['pdf'], chain['save3']['sidecar'], dict(operation='save'),
                                                    workspace=tmp_path / 'painted')
        opened = semantic.open_semantic_flow(candidate['pdf'], candidate['sidecar'])
        assert opened['status'] == 'restored' and opened['island']['canonical']
        assert candidate['body'] == rev['body'] + canonical_paint(rev, full(rev))
        (path,) = opened['state']['slots']['slot-0']['binding']['element']['paths']
        assert path['relationship']['hypothesis'] == 'decoration_candidate'
        with pytest.raises(PdfError, match='composed text intersects a filled vector'):
            writer.build_semantic_candidate(candidate['pdf'], candidate['sidecar'], dict(operation='save'),
                                            workspace=tmp_path / 'next')
    with pytest.raises(PdfError, match='saved non-text paint differs from the transaction plan'):
        writer.build_semantic_candidate(candidate['pdf'], candidate['sidecar'],
                                        dict(operation='edit', start=1, end=1, text='A'), workspace=tmp_path / 'edit')
    monkeypatch.undo()
    # With the real (v2) grammar restored, the painted candidate no longer opens.
    refused = semantic.open_semantic_flow(candidate['pdf'], candidate['sidecar'])
    assert refused['status'] == 'needs_confirmation' and 'source output body grammar' in refused['reason']
