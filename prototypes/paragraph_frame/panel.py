"""A rectangular background panel that grows down with its paragraph on explicit approval (issue #112).

A paragraph drawn on a panel (a filled rectangle holding every line of it)
cannot grow past the panel's bottom: the frame stops there. This module finds
a **candidate extension** and describes it with its evidence; B2 extends the
panel only when the user approves that candidate by its id
(``auto.propose(..., panels=[id])``). ``acknowledge_inference``, a chain id or
an approval of an earlier edit never approves it.

A candidate is the panel holding the paragraph whose bottom stops its
growth, when

* it is drawn in the page's own content stream that the edit rewrites, by
  one fill of a path that is a single ``re`` (``f``, ``f*``, ``F``, or ``B``,
  ``B*`` with its outline), under an axis-aligned CTM, outside text objects,
  in a device colour, opaque, with the normal blend and no soft mask; the
  outlines of the same rectangle (``re`` stroked alone, ``S``) belong to it.
  Any other subpath, a curve, a pattern, a transparency, a Form XObject or
  a second fill of the rectangle refuses it;
* it is drawn before the paragraph's text (under it), and its clips hold it
  extended;
* the extension (its bottom moved down by the paragraph's growth, the
  least the text needs: the panel's bottom margin is kept) lies on the page,
  and the band it adds meets nothing: no text, drawing, image or annotation
  but a background holding the whole extended panel.

Only the bottom moves: the top, the sides, the colours, the line width and
style, the drawing order and the operators stay; the ``re`` operands are
rewritten in place. The id is a digest of the page, the operators' bytes,
the rectangle and the extension, so a changed PDF or another edit is no
longer approved.
"""
import hashlib

import pymupdf

from pdfeditor.content_stream import number

from .engine import Refused
from .form_write import clip_rect

FILL = {'f', 'F', 'f*', 'B', 'B*'}
TOL = .05


def _page_matrix(c):
    return pymupdf.Matrix(c.page.transformation_matrix)


def _rect(args, ctm, page):
    x, y, w, h = args
    m = pymupdf.Matrix(*ctm) * page
    pts = [pymupdf.Point(x, y) * m, pymupdf.Point(x + w, y + h) * m]
    return pymupdf.Rect(min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))


def _width(state):
    try:
        return float(str(state.other.get('w', '[1]')).strip('[]').split(',')[0])
    except ValueError:
        return 1.


def rectangles(c, program):
    """The single-rectangle paths painted in ``program``: dict(rect (page), op (the ``re``), paint, state, ordinal, start, end)."""
    seq = c.stream_boundaries[program]
    page = _page_matrix(c)
    out, path = [], []
    for k in range(1, len(seq)):
        b = seq[k]
        n = b.operator.name
        if n in ('m', 'l', 'c', 'v', 'y', 'h', 're'):
            path.append(k)
        elif n in ('n', 'S', 's', 'f', 'F', 'f*', 'B', 'B*', 'b', 'b*'):
            if len(path) == 1 and seq[path[0]].operator.name == 're' and n != 'n' and not seq[k - 1].pending_clip:
                r = seq[path[0]]
                state = seq[path[0] - 1].state
                out.append(dict(rect=_rect([float(v) for v in r.operator.args], state.ctm, page), op=r, paint=n, state=seq[k - 1].state,
                                ordinal=k, start=r.operator.start, end=b.operator.end, text_object=seq[k].text_object, ctm=state.ctm))
            path = []
    return out


def _plain(state):
    """Why a fill or outline with this state cannot be extended, or None."""
    a, b, cc, d, *_ = state.ctm
    if abs(b) > 1e-9 or abs(cc) > 1e-9:
        return 'drawn under a rotated or skewed CTM'
    if state.fill_space is not None or state.fill[0] not in ('g', 'rg', 'k'):
        return 'filled with a colour space or pattern'
    if state.opacity != 1 or state.stroke_opacity != 1:
        return 'transparent'
    if any(v not in ('/Normal', '/Compatible', '/None') for k, v in state.blend.items() if k in ('/BM', '/SMask')):
        return 'blended or soft-masked'
    return None


def _group(c, program, container):
    """(the fill, its outlines) drawing the rectangle ``container`` (page bounds of a painted obstacle), or a refusal."""
    found = rectangles(c, program)
    painted = lambda x: x['rect'] + ((-_width(x['state']) / 2,) * 2 + (_width(x['state']) / 2,) * 2 if x['paint'] not in ('f', 'F', 'f*') else (0, 0, 0, 0))
    hits = [x for x in found if max(abs(p - q) for p, q in zip(painted(x), container)) <= .6]
    if not hits:
        raise Refused('the background is not one rectangle drawn in the page content that the edit rewrites')
    geometry = hits[0]['rect']
    same = [x for x in found if max(abs(p - q) for p, q in zip(x['rect'], geometry)) <= TOL]
    fills = [x for x in same if x['paint'] in FILL]
    strokes = [x for x in same if x['paint'] in ('S', 's')]
    if len(fills) != 1:
        raise Refused('the background rectangle is filled %s times' % len(fills))
    if [x for x in same if x not in fills + strokes]:
        raise Refused('the background rectangle is painted in another way')
    for x in fills + strokes:
        if x['text_object']:
            raise Refused('the background rectangle is drawn in a text object')
        why = _plain(x['state'])
        if why:
            raise Refused('the background rectangle is ' + why)
    return fills[0], strokes


def owned(o, found):
    """Whether the observed obstacle ``o`` is the panel's own paint: its fill, or an edge of its outline."""
    box, old = pymupdf.Rect(o['bounds']), pymupdf.Rect(found['old'])
    half = found.get('_half', 0.)
    if max(abs(p - q) for p, q in zip(box, old)) <= .6 or max(abs(p - q) for p, q in zip(box, old + (-half, -half, half, half))) <= .6:
        return True
    return o['kind'] == 'vector-border' and (old + (-half - .05, -half - .05, half + .05, half + .05)).contains(box)


def candidate(c, page_number, program, container, dy, obstacles, text_start, recorded=None, context=()):
    """The extension of the panel ``container`` (page bounds) by ``dy`` (page y, down) as approvable evidence, or a refusal.

    ``obstacles``: the proposal's observed obstacles (protected paint, foreign
    text, annotations, containers). ``text_start``: the first operator offset
    of the paragraph's text in ``program`` (the panel is drawn before it).
    ``recorded``: an extension stored by an earlier save (a shrink goes back
    no higher than the panel was before it). ``context``: what names the edit
    (the source digest, the selection, the replacement, the occurrence); it
    enters the id."""
    if program[1]:
        raise Refused('the background is drawn in a Form XObject')
    fill, strokes = _group(c, program, container)
    if fill['start'] > text_start:
        raise Refused('the background rectangle is drawn over the paragraph')
    old = pymupdf.Rect(fill['rect'])
    half = max([_width(s['state']) / 2 for s in strokes + ([fill] if fill['paint'] in ('B', 'B*') else [])] + [0.])
    new = pymupdf.Rect(old.x0, old.y0, old.x1, old.y1 + dy)
    if dy < 0 and (recorded is None or new.y1 < recorded['old'][3] - TOL):
        raise Refused('a background is made smaller only back to where an earlier approved extension took it from')
    if new.height <= 0:
        raise Refused('the background would vanish')
    page = c.page.rect
    if new.y1 + half > page.y1:
        raise Refused('the extended background would leave the page')
    for x in [fill] + strokes:
        for clip in x['state'].clip:
            box = clip_rect(clip, c.page.transformation_matrix)
            if box is None:
                raise Refused('the background is drawn under a clip that is not a rectangle')
            if not (box + (-TOL, -TOL, TOL, TOL)).contains(new + (-half, -half, half, half)):
                raise Refused('the extended background would be cut by its clip')
    # The band the extension adds (or removes): nothing but a background holding the extended panel.
    band = pymupdf.Rect(old.x0 - half, min(old.y1, new.y1) - half, old.x1 + half, max(old.y1, new.y1) + half)
    outer = new + (-half, -half, half, half)
    probe = dict(old=list(old), _half=half)
    for o in obstacles:
        box = pymupdf.Rect(o['bounds'])
        if max(abs(p - q) for p, q in zip(box, container)) <= .6 or owned(o, probe):
            continue
        # (A rule has no height or width: it is a thin band.)
        if box.height < .1:
            box.y0, box.y1 = box.y0 - .1, box.y1 + .1
        if box.width < .1:
            box.x0, box.x1 = box.x0 - .1, box.x1 + .1
        if any(max(abs(p - q) for p, q in zip(box, x['rect'] + (-_width(x['state']) / 2,) * 2 + (_width(x['state']) / 2,) * 2)) <= .6 for x in strokes):
            continue
        inter = box & band
        if inter.is_empty or inter.width <= .01 or inter.height <= .01:
            continue
        if o['kind'] in ('container', 'filled-vector', 'image', 'vector') and (box + (TOL, TOL, -TOL, -TOL)).contains(outer):
            continue
        raise Refused(f"the extended background would meet {o['kind']} at {[round(v, 1) for v in box]}")
    items = [fill] + strokes
    data = c.streams[program[0]]
    ident = hashlib.sha256('|'.join([str(page_number), str(program[0]), hashlib.sha256(data).hexdigest()] + [str(v) for v in context]
                                     + [f"{x['start']}:{x['end']}:" + data[x['start']:x['end']].hex() for x in items]
                                     + [repr([round(v, 3) for v in old]), repr(round(dy, 3))]).encode()).hexdigest()[:16]
    colour = lambda s: [s[0], [float(v) for v in s[1]]]
    return dict(id='panel-extension-' + ident, page=page_number, old=[round(v, 3) for v in old], new=[round(v, 3) for v in new],
                extension=round(dy, 3), edges='bottom only (top, sides, colours, line style, order kept)',
                operators=[dict(offset=x['op'].operator.start, operator=data[x['op'].operator.start:x['op'].operator.end].decode('latin1'),
                                paint=x['paint']) for x in items],
                style=dict(fill=colour(fill['state'].fill), outline=[dict(colour=colour(s['state'].stroke), width=_width(s['state'])) for s in strokes]),
                band=[round(v, 3) for v in band],
                reason='the paragraph grows past the bottom of the panel holding it' if dy > 0 else 'the paragraph is shorter: the panel goes back',
                if_not_approved='refused: the paragraph would leave its background' if dy > 0 else 'the panel stays as it is',
                _program=program, _half=half, _items=[(x['op'].operator.start, x['op'].operator.end, [float(v) for v in x['op'].operator.args], x['ctm']) for x in items])


def public(found):
    """``found`` without the writer's private fields."""
    return {k: v for k, v in found.items() if not k.startswith('_')}


def edits(c, found):
    """(start, end, replacement) of each ``re`` operator of the approved extension ``found``: only its bottom edge moves."""
    page = _page_matrix(c)
    out = []
    dy = found['extension']
    for start, end, (x, y, w, h), ctm in found['_items']:
        m = pymupdf.Matrix(*ctm) * page
        # The page-space move of the bottom edge, in the rectangle's own space (an axis-aligned CTM).
        dv = dy / m.d
        y_page, yh_page = (pymupdf.Point(x, y) * m).y, (pymupdf.Point(x, y + h) * m).y
        if y_page > yh_page:
            y, h = y + dv, h - dv
        else:
            h = h + dv
        out.append((start, end, b' '.join(number(v) for v in (x, y, w, h)) + b' re'))
    return out


def expected_drawings(drawings, found):
    """``drawings`` (PyMuPDF) as they are after the approved extension ``found``: the panel's drawings with their rectangle extended."""
    old, new = pymupdf.Rect(found['old']), pymupdf.Rect(found['new'])
    out, hit = [], 0
    for d in drawings:
        items = d.get('items') or []
        if len(items) == 1 and items[0][0] == 're' and max(abs(p - q) for p, q in zip(items[0][1], old)) <= TOL:
            grow = d['rect'] - items[0][1]
            d = dict(d, rect=pymupdf.Rect(new.x0 + grow.x0, new.y0 + grow.y0, new.x1 + grow.x1, new.y1 + grow.y1),
                     items=[('re', pymupdf.Rect(new), *items[0][2:])])
            hit += 1
        out.append(d)
    if not hit:
        raise Refused('the approved background is not drawn where it was proposed')
    return out
