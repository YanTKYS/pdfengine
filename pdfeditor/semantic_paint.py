"""Semantic underline paint for one owned semantic slot (pure; no PDF access).

Contract: `docs/anchored-paint-ownership.md` §27 (semantic underline contract).
Semantic record version 3 adds `payload.decorations`; the underline lives in
the slot's existing owned source-output body, after the text group, under
the same marker pair. Nothing here reads a PDF, mutates bytes or publishes.

* decorations: current-state only, `current:i` positional labels, logical
  `[start, end)` code-point ranges on grapheme boundaries, outside/outside
  boundary affinity, sorted, disjoint (touching kept separate), each with a
  visible character and an exact em-relative recipe;
* D remap (`remap`), E add/remove/recipe (`reinterpret`), R refusals;
* geometry (`rectangles`) is a pure function of the exact semantic plan, the
  current style and the stored recipe; font tables are never authority;
* one canonical serializer (`paint_group`) and the v3 current-body grammar
  (`body_grammar`): one canonical text group, then at most one underline group.
"""
from copy import deepcopy
from fractions import Fraction as F

from uniseg.graphemecluster import grapheme_cluster_boundaries

from .backend import PdfError
from .content_stream import operators
from .operator_nesting import audit
from . import semantic_island as island
from . import semantic_measure as measure
from . import source_ownership as owned

KIND = 'underline'
OUTSIDE = 'outside'
KEYS = frozenset({'id', 'kind', 'start', 'end', 'start_affinity', 'end_affinity', 'recipe'})
ADD_KEYS = KEYS - {'id'}
REFERENCE_KEYS = frozenset({'id', 'start', 'end'})
RECIPE_KEYS = frozenset({'offset_em', 'thickness_em'})
ACTIONS = ('add', 'remove', 'recipe')
FILL_ARITY = {'g': 1, 'rg': 3, 'k': 4}
RECT_SHAPE = (('m', 2), ('l', 2), ('l', 2), ('l', 2), ('h', 0), ('f', 0))


def _exact(value):
    if not isinstance(value, str) or str(measure.rational(value)) != value:
        raise PdfError('semantic values must use canonical exact rational spelling')
    return measure.rational(value)


def visible(text):
    """A decoration needs at least one character that is neither space nor newline."""
    return any(c not in ' \n' for c in text)


def renumber(decorations):
    """Canonical order by (start, end); `id` is the position label `current:i` (never an identity)."""
    ordered = sorted(decorations, key=lambda d: (d['start'], d['end']))
    return [dict(d, id=f'current:{i}') for i, d in enumerate(ordered)]


def recipe(value):
    """(offset_em, thickness_em) of an explicit exact recipe; never inferred."""
    if not isinstance(value, dict) or set(value) != RECIPE_KEYS:
        raise PdfError('underline recipe needs exactly offset_em and thickness_em')
    offset, thickness = _exact(value['offset_em']), _exact(value['thickness_em'])
    if not (0 <= offset <= 1 and 0 < thickness <= 1):
        raise PdfError('underline recipe is outside its exact bounds')
    return offset, thickness


def validate(text, decorations):
    """Schema of `payload.decorations` against the current semantic text (§27.5)."""
    if not isinstance(decorations, list):
        raise PdfError('decorations must be an explicit list')
    limits = {0, len(text), *grapheme_cluster_boundaries(text)}
    previous = 0
    for index, d in enumerate(decorations):
        if not isinstance(d, dict) or set(d) != KEYS:
            raise PdfError('decoration needs exactly id, kind, start, end, affinities and recipe')
        if d['id'] != f'current:{index}':
            raise PdfError('decoration id is not its current canonical position')
        if d['kind'] != KIND:
            raise PdfError('unsupported decoration kind')
        if d['start_affinity'] != OUTSIDE or d['end_affinity'] != OUTSIDE:
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
        recipe(d['recipe'])


def remap(text, decorations, start, end, inserted):
    """D: map every decoration through one semantic edit `{start, end, text}` (§27.7).

    Membership follows the `anchors.project_range` convention with outside/outside
    affinity (insertion at an edge stays outside, a replacement inside the range,
    including exactly the range, is a member; positions are re-derived from the
    surviving members). Where `project_range` refuses an edit crossing an edge,
    §27.7 shrinks instead: the deleted part leaves and the inserted text is
    outside, because a D edit never refuses because of a decoration. No members or
    no visible character left terminates the decoration (no dormant state).
    """
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
        if sorted(members) != list(range(lo, hi)):
            raise PdfError('edited decoration is no longer one contiguous range')
        if visible(after[lo:hi]):
            kept.append(dict(deepcopy(d), start=lo, end=hi))
    result = renumber(kept)
    validate(after, result)
    return result


def reinterpret(text, decorations, action):
    """E: exactly one explicit add/remove/recipe action; returns the next decorations.

    `remove` and `recipe` name `(id, start, end)` of the current state
    (compare-and-swap), so a stale label refuses. `add` always creates a new
    decoration; nothing is revived and the caller never chooses an ID.
    """
    if not isinstance(action, dict) or len(action) != 1:
        raise PdfError('exactly one decoration action per request')
    (verb, item), = action.items()
    current = deepcopy(decorations)
    if verb == 'add':
        if not isinstance(item, dict) or set(item) != ADD_KEYS:
            raise PdfError('underline add needs exactly kind, start, end, affinities and recipe')
        current.append(dict(deepcopy(item), id='new'))
    elif verb in ('remove', 'recipe'):
        keys = REFERENCE_KEYS | ({'recipe'} if verb == 'recipe' else set())
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


def rectangles(plan, style, decorations):
    """Exact page y-down rectangles `(left, upper, right, lower)` (§27.8, §27.13).

    Decorations in canonical order, then lines top to bottom; per line the
    painted glyphs of the range without that line's boundary spaces. The upper
    edge is the glyph baseline (line baseline + rise) plus `font_size · offset_em`;
    the thickness is `font_size · thickness_em`. Every rectangle must lie in its
    line box exactly.
    """
    size = F(style['font_size'])
    rects = []
    for d in decorations:
        offset, thickness = (value * size for value in recipe(d['recipe']))
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


def paint_group(rects, fill, page_top):
    """The one canonical underline group serialization (empty when nothing is painted, §27.14)."""
    if not rects:
        return b''
    op, values = fill
    if op not in FILL_ARITY or len(values) != FILL_ARITY[op]:
        raise PdfError('underline needs the text device gray/RGB/CMYK fill')
    d, top = island.decimal, F(page_top)
    out = 'q ' + ' '.join(d(F(str(v))) for v in values) + f' {op}\n'
    for left, upper, right, lower in rects:
        x0, x1, y0, y1 = d(left), d(right), d(top - upper), d(top - lower)
        if F(x0) >= F(x1) or F(y1) >= F(y0):
            raise PdfError('zero-area underline after the output decimal policy')
        out += f'{x0} {y0} m {x1} {y0} l {x1} {y1} l {x0} {y1} l h f\n'
    return (out + 'Q\n').encode('ascii')


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def body_grammar(body):
    """v3 current-body grammar (§27.3): one text group, then at most one underline group.

    The text group is checked by the unchanged v2 `source_ownership.grammar`.
    Returns (text group bytes, paint group bytes). The grammar is structural
    only; meaning comes from the exact canonical comparison of the verifier.
    """
    if b'%' in body:
        raise PdfError('comments are not allowed in source output body')
    ops = list(operators(body))
    if audit(body)['violations']:
        raise PdfError('invalid v3 source output body grammar')
    closes = [i for i, op in enumerate(ops) if op.name == 'Q']
    if not closes:
        raise PdfError('invalid v3 source output body grammar')
    split = ops[closes[0] + 1].start if closes[0] + 1 < len(ops) else len(body)
    text, paint = body[:split], body[split:]
    owned.grammar(text)
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
