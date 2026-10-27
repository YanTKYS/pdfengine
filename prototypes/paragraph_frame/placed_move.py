"""Inline placed objects that move with a following paragraph (issue #97).

A following paragraph is translated rigidly when the edited paragraph above
it grows or shrinks (auto_writer). Its text is redrawn; an inline placed
object in it (semantics: ``fixed``, a plain marked-content sequence that
paints, such as InDesign's ``/PlacedPDF`` icon at a line start) keeps its
operators. It moves with its paragraph only when the operators that draw it
are proven to be its own and translating them changes nothing else:

* the **unit** is the sequence (BDC … EMC), the paths painted right before
  its BDC that lie inside what the sequence itself draws or clips (an icon's
  background drawn just before it), and, when the sequence opened a ``q``
  it closes after its EMC (``BDC q … EMC Q``), the operators up to that ``Q``;
* the unit is in the program the edit rewrites (the page, or the one Form
  XObject invocation, copy-on-write) and outside any text object, with no
  path or clip pending before it;
* at the unit's own nesting level it only builds and paints paths, opens and
  closes its sequence and its ``q``, and paints XObjects (``Do``): it sets no
  state (colour, ExtGState, text state, CTM, clip) that later content reads,
  so ``q … Q`` around it changes nothing but its position;
* the only text it draws is the paragraph's fixed glyphs, all of them;
* the paragraph's lines hold nothing else in their column that would stay
  behind (a search box beside an icon, a link, an image): only its text and
  its units are there;
* the inherited clip is rectangular and holds the moved unit, which stays in
  the height of its column's approved frame (it moves vertically, so where it
  reached past the frame's side it still does) and meets no protected paint
  or foreign text where it did not before.

It is then written as ``q 1 0 0 1 tx ty cm <unit> Q``, the same page
translation as its paragraph's text. An XObject it paints is not changed
(other invocations of a shared XObject are unaffected). Anything else is
refused, as before.
"""
import pymupdf
from .engine import Refused
from .form_write import _page_rect, clip_rect

PATH = {'m', 'l', 'c', 'v', 'y', 'h', 're'}
PAINT = {'f', 'F', 'f*', 'S', 's', 'B', 'B*', 'b', 'b*', 'n'}
# At the unit's own level: building and painting paths, its sequence, its q/Q, XObjects.
LEVEL = PATH | PAINT | {'BDC', 'BMC', 'EMC', 'q', 'Q', 'Do'}
TEXT = {'BT', 'ET', 'Tj', 'TJ', "'", '"'}


def _bounds(op, state, page):
    """Page rectangle of a path construction operator's points, or None."""
    a = [float(v) for v in op.args]
    if op.name == 're':
        x, y, w, h = a
        return _page_rect(min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h), state.ctm, page)
    if op.name in ('m', 'l'):
        return _page_rect(a[0], a[1], a[0], a[1], state.ctm, page)
    if op.name in ('c', 'v', 'y'):
        xs, ys = a[0::2], a[1::2]
        return _page_rect(min(xs), min(ys), max(xs), max(ys), state.ctm, page)
    return None


def _union(rects):
    rects = [r for r in rects if r is not None]
    if not rects:
        return None
    out = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        out |= r
    return out


def unit(c, paragraph, scope, program, page):
    """The movable unit of one fixed ``scope`` of ``paragraph``: its byte range
    in ``program`` (stream xref, invocation), page bounds, the state before it
    and the glyphs it draws, or a refusal."""
    where = (scope['stream_xref'], tuple(tuple(i) for i in scope['invocation']))
    if where != program:
        raise Refused('an inline placed object of a following paragraph is drawn by another content program')
    seq = c.stream_boundaries[program]
    index = {b.operator.start: n for n, b in enumerate(seq)}
    begin, close = scope['begin_byte_range'][0], scope['end_byte_range'][0]
    if begin not in index or close not in index:
        raise Refused('an inline placed object of a following paragraph is not delimited at top level')
    i0, i1 = index[begin], index[close]
    state_before = lambda n: seq[n - 1].state if n else seq[n].state
    depth_before = lambda n: seq[n - 1].q_depth if n else 0
    of = lambda lo, hi: {i for e in c.events if e.stream_xref == where[0] and e.invocation == where[1] and lo <= e.operator.start < hi
                         for ch in e.chars for i in ch.source_orders}
    # What the sequence itself draws or clips bounds what it owns.
    inside = of(seq[i0].operator.start, seq[i1].operator.end)
    own = _union([_bounds(seq[k].operator, state_before(k), page) for k in range(i0 + 1, i1) if seq[k].operator.name in PATH]
                 + [pymupdf.Rect(c.actual[i]['bbox']) for i in inside if c.actual[i]['unicode'].strip()])
    if own is None:
        raise Refused('an inline placed object of a following paragraph draws nothing measurable')
    # Whole painted paths right before the BDC, inside what the sequence draws: its background.
    run, k = [], i0 - 1
    while k >= 0 and seq[k].operator.name in PATH | PAINT:
        run.insert(0, k)
        k -= 1
    paths, current = [], []
    for n in run:
        current.append(n)
        if seq[n].operator.name in PAINT:
            paths.append(current)
            current = []
    if current:
        raise Refused('an inline placed object of a following paragraph follows an unpainted path')
    start = i0
    for path in reversed(paths):
        area = _union([_bounds(seq[n].operator, state_before(n), page) for n in path])
        if area is None or not (own + (-.5, -.5, .5, .5)).contains(area):
            break
        start = path[0]
    if start and (seq[start - 1].text_object or seq[start - 1].pending_path or seq[start - 1].pending_clip):
        raise Refused('an inline placed object of a following paragraph lies inside a text object or a path')
    depth = depth_before(start)
    # The q the sequence opened is closed after its EMC: the unit runs to that Q.
    end = i1
    while seq[end].q_depth > depth:
        end += 1
        if end >= len(seq) or seq[end].operator.name != 'Q':
            raise Refused('an inline placed object of a following paragraph shares its graphics state with later content')
    if seq[end].q_depth != depth or seq[end].text_object or seq[end].pending_path or seq[end].pending_clip:
        raise Refused('an inline placed object of a following paragraph is not a balanced drawing')
    # At its own level the unit sets nothing that later content reads.
    for n in range(start, end + 1):
        name = seq[n].operator.name
        level = seq[n].q_depth if name == 'Q' else depth_before(n)
        if level != depth:
            continue
        if name not in LEVEL:
            raise Refused(f'an inline placed object of a following paragraph sets state used after it ({name})')
        if (name in ('BDC', 'BMC') and n != i0) or (name == 'EMC' and n != i1):
            raise Refused('an inline placed object of a following paragraph crosses other marked content')
    drawn = of(seq[start].operator.start, seq[end].operator.end)
    if not drawn <= set(paragraph.fixed):
        raise Refused('an inline placed object of a following paragraph draws other text')
    bounds = _union([own] + [_bounds(seq[n].operator, state_before(n), page) for n in range(start, end + 1) if seq[n].operator.name in PATH])
    return dict(start=seq[start].operator.start, end=seq[end].operator.end, bounds=bounds, state=state_before(start), glyphs=sorted(drawn))


def plan(c, paragraph, program, delta, frames, obstacles, page, moving=()):
    """The units of a following ``paragraph`` translated by ``delta`` (page y),
    each with ``prefix``/``suffix`` bytes and old/new page bounds, or a refusal.

    ``moving``: page areas of other content moved by the same translation (an
    approved line decoration); it is no obstacle to these units."""
    out = []
    # What is drawn on the paragraph's lines moves with it: other text or paint
    # there (a search box beside an icon) would stay behind, apart from it.
    units = [unit(c, paragraph, scope, program, page) for scope in paragraph.scopes if scope['kind'] == 'fixed']
    owned = [u['bounds'] + (-.5, -.5, .5, .5) for u in units]
    # Units of the paragraph and other moved content move together: none is in another's way.
    together = owned + [pymupdf.Rect(r) + (-.5, -.5, .5, .5) for r in moving]
    texts = [pymupdf.Rect(c.actual[i]['bbox']) for i in paragraph.ids if c.actual[i]['unicode'].strip()]
    band = _union(texts + [u['bounds'] for u in units])
    for o in obstacles:
        box = pymupdf.Rect(o['bounds'])
        if o['kind'] == 'container' or any(r.contains(box) for r in owned):
            continue
        # Its column: the frame above or around it at its horizontal position.
        column = next((f for f in map(pymupdf.Rect, frames) if f.x0 < band.x1 and band.x0 < f.x1), band)
        across = pymupdf.Rect(column.x0, band.y0 + .5, column.x1, band.y1 - .5)
        if box.intersects(across) and not any(f.contains(box) for f in texts):
            raise Refused('the line of an inline placed object of a following paragraph holds other content (' + o['kind'] + ') that does not move with it')
    for u in units:
        old, new = u['bounds'], u['bounds'] + (0, delta, 0, delta)
        for clip in u['state'].clip:
            area = clip_rect(clip, page)
            if area is None:
                raise Refused('an inline placed object of a following paragraph is under a nonrectangular clip')
            if not area.contains(new):
                raise Refused('the clip of an inline placed object of a following paragraph does not hold it moved')
        # It moves vertically in its column: the frame it overlaps must hold it in height.
        if not any(f.x0 < new.x1 and new.x0 < f.x1 and f.y0 - .5 <= new.y0 and new.y1 <= f.y1 + .5 for f in map(pymupdf.Rect, frames)):
            raise Refused('capacity: an inline placed object of a following paragraph would leave the approved frame')
        for o in obstacles:
            if o['kind'] == 'container':
                continue
            box = pymupdf.Rect(o['bounds'])
            if any(r.contains(box) for r in together):
                continue  # paint that moves with it
            overlap = new & box
            if not overlap.is_empty and overlap.width > .05 and overlap.height > .05 and (overlap & old).get_area() < overlap.get_area() - 1e-6:
                raise Refused('an inline placed object of a following paragraph would collide with protected ' + o['kind'])
        m = pymupdf.Matrix(*u['state'].ctm) * pymupdf.Matrix(page)
        inv = ~m
        t = pymupdf.Point(0, delta) * inv - pymupdf.Point(0, 0) * inv
        u.update(old=list(old), new=list(new), translation=(t.x, t.y))
        out.append(u)
    return out


def operator_end(c, program, start):
    """End of the operator of ``program`` that starts at ``start``."""
    return next(b.operator.end for b in c.stream_boundaries[program] if b.operator.start == start)


def operator_start(c, program, end):
    """Start of the operator of ``program`` that ends at ``end``."""
    return next(b.operator.start for b in c.stream_boundaries[program] if b.operator.end == end)


def _norm(value, dx=0., dy=0.):
    """A drawing value as plain data to 0.01 pt, translated by (dx, dy) in page coordinates."""
    if isinstance(value, pymupdf.Point):
        return ('P', round(value.x + dx, 2), round(value.y + dy, 2))
    if isinstance(value, pymupdf.Rect):
        return ('R', round(value.x0 + dx, 2), round(value.y0 + dy, 2), round(value.x1 + dx, 2), round(value.y1 + dy, 2))
    if isinstance(value, pymupdf.Quad):
        return ('Q',) + tuple(_norm(p, dx, dy) for p in (value.ul, value.ur, value.ll, value.lr))
    if isinstance(value, dict):
        return tuple(sorted((k, _norm(v, dx, dy)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_norm(v, dx, dy) for v in value)
    if isinstance(value, float):
        return round(value, 4)
    return value


def _inside(rect, units):
    """The shift of the moved unit whose original bounds hold ``rect``, or None."""
    rect = pymupdf.Rect(rect)
    for u in units:
        if (pymupdf.Rect(u['old']) + (-.5, -.5, .5, .5)).contains(rect):
            return u['shift']
    return None


def paint_key(drawing, units=()):
    """A drawing (PyMuPDF get_drawings) as compared after an edit; inside a
    moved unit it is expected translated with it."""
    shift = _inside(drawing['rect'], units) if units else None
    dx, dy = shift or (0., 0.)
    return _norm(drawing, dx, dy)


def image_key(image, units=()):
    """An image (get_image_info) as compared after an edit, translated inside a moved unit."""
    shift = _inside(image['bbox'], units) if units else None
    dx, dy = shift or (0., 0.)
    out = {}
    for k, v in image.items():
        if k == 'bbox':
            x0, y0, x1, y1 = v
            v = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        elif k == 'transform':
            a, b, c, d, e, f = v
            v = (a, b, c, d, e + dx, f + dy)
        out[k] = v
    return _norm(out)


def _split(value):
    """A normalized drawing or image as its structure (numbers left out) and its numbers."""
    numbers = []

    def walk(x):
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            numbers.append(float(x))
            return '#'
        if isinstance(x, tuple):
            return tuple(walk(y) for y in x)
        return x
    return walk(value), numbers


def same_paint(expected, found, tol=.02):
    """Two lists of normalized drawings or images are the same up to ``tol`` in every number
    (a translation by a non-representable amount rounds either way at 0.01 pt)."""
    pools = {}
    for k in found:
        s, n = _split(k)
        pools.setdefault(s, []).append(n)
    for k in expected:
        s, n = _split(k)
        pool = pools.get(s, [])
        hit = next((j for j, m in enumerate(pool) if len(m) == len(n) and all(abs(a - b) <= tol for a, b in zip(n, m))), None)
        if hit is None:
            return False
        pool.pop(hit)
    return all(not v for v in pools.values())
