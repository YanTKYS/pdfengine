"""Line decorations that move with a following paragraph on explicit approval (issue #99).

A following paragraph whose line carries more than its text and its inline
placed objects (an InDesign "HP" line: the icon, then a search box drawn as a
white field, rules, a shaded button, its label and a pointer, with a link
annotation over it) cannot move alone: what stays behind breaks the line
(placed_move refuses it). Position alone never proves that the box is the
paragraph's. This module only finds a **candidate group**, describes it with
its evidence, and B2 moves it when the user approves that candidate by its id
(``auto.propose(..., decorations=[id])``); ``acknowledge_inference`` never
approves one.

A candidate is the contiguous drawing that

* starts with the paragraph's first placed object (its placed_move unit) or
  text object, holds every glyph of the paragraph and no other glyph, and
  continues with whole items (a path and its paint, a ``q … Q`` block, a text
  object, a marked-content sequence, an XObject) as long as each is drawn
  inside the paragraph's line (its column, 2 pt above and below);
* is in the page's own content (not in a Form XObject), outside text objects,
  paths and clips at both ends, with balanced ``q``/``Q`` and marked content;
* at its own level draws, opens and closes those items and sets graphics or
  text state; it neither changes the CTM nor clips nor shades there. The
  state it leaves is set again after it by replaying, in order, the state
  operators it used at its own level, so ``q 1 0 0 1 tx ty cm <group> Q
  <replay>`` changes nothing but its position;
* leaves nothing else on the paragraph's line in its column, under a
  rectangular clip that holds it moved, with no other paint or text where it
  is or where it goes (a background must hold both);
* carries the link annotations over it: each lies inside the group, is
  listed by this page only and is moved by the same translation (its
  ``/Rect`` and ``/QuadPoints``); its target, action, flags and appearance
  stay as they are. Any other annotation on the line refuses it.

The id is a digest of the page, the operators' bytes, the translation and the
annotations, so a changed PDF or a changed group is no longer approved.
"""
import hashlib
import json

import pymupdf
from pypdf.generic import ArrayObject, FloatObject, NameObject

from .engine import Refused
from .form_write import _page_rect, clip_rect
from . import placed_move

PATH, PAINT = placed_move.PATH, placed_move.PAINT
SHOW = {'Tj', 'TJ', "'", '"'}
TEXT = {'Tm', 'Td', 'TD', 'T*'} | SHOW
# State that later content reads, replayed after the group in the same order.
REPLAY = {'g', 'rg', 'k', 'cs', 'sc', 'scn', 'G', 'RG', 'K', 'CS', 'SC', 'SCN', 'gs', 'w', 'J', 'j', 'M', 'd', 'ri', 'i',
          'Tc', 'Tw', 'Tz', 'TL', 'Tf', 'Tr', 'Ts'}
MARKS = {'BDC', 'BMC', 'EMC', 'MP', 'DP', 'BX', 'EX'}
TOL = 2.


def _item_end(seq, k):
    """Index of the last operator of the item starting at ``k``."""
    n = seq[k].operator.name
    if n == 'q':
        d0, j = seq[k - 1].q_depth, k
        while seq[j].q_depth > d0:
            j += 1
        return j
    if n == 'BT':
        j = k
        while seq[j].text_object:
            j += 1
        return j
    if n in ('BDC', 'BMC'):
        m0, j = seq[k - 1].marked_content_depth, k
        while seq[j].marked_content_depth > m0:
            j += 1
        # InDesign's placed object: BDC q … EMC Q (its Q closes it).
        while seq[j].q_depth > seq[k - 1].q_depth and j + 1 < len(seq) and seq[j + 1].operator.name == 'Q':
            j += 1
        return j
    if n in PATH:
        j = k
        while seq[j].operator.name not in PAINT:
            j += 1
        return j
    return k


def _points_rect(points, page):
    xs, ys = [p.x for p in points], [p.y for p in points]
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


def _path_rect(path, page):
    """Page bounds of a recorded path (operators with their CTM)."""
    points = []
    for op in path:
        a, m = op['args'], pymupdf.Matrix(*op['ctm']) * pymupdf.Matrix(page)
        if op['operator'] == 're':
            x, y, w, h = a
            points += [pymupdf.Point(x, y) * m, pymupdf.Point(x + w, y + h) * m, pymupdf.Point(x + w, y) * m, pymupdf.Point(x, y + h) * m]
        else:
            points += [pymupdf.Point(a[i], a[i + 1]) * m for i in range(0, len(a) - 1, 2)]
    return _points_rect(points, page) if points else None


def _width(state):
    try:
        return float(str(state.other.get('w', '[1]')).strip('[]').split(',')[0])
    except ValueError:
        return 1.


def _unlinked(c, origin):
    """The glyph drawn at ``origin`` (0.01 pt) that no code of the page is linked to, if exactly one."""
    if not hasattr(c, '_linked_glyphs'):
        c._linked_glyphs = {i for ev in c.events for ch in ev.chars for i in ch.source_orders}
    found = [i for i, g in enumerate(c.actual) if i not in c._linked_glyphs
             and abs(g['origin'][0] - origin[0]) < .01 and abs(g['origin'][1] - origin[1]) < .01]
    return found if len(found) == 1 else []


def _paint(c, seq, k, e, page, resources, program):
    """Painted page bounds and glyph ids of the item ``seq[k..e]`` (a refusal when unbounded)."""
    painted, glyphs, path = [], set(), []
    starts = {}
    for ev in c.events:
        if ev.stream_xref == program[0] and ev.invocation == program[1]:
            starts.setdefault(ev.operator.start, []).append(ev)
    for j in range(k, e + 1):
        b, before = seq[j], seq[j - 1].state
        n = b.operator.name
        if n in PATH:
            path.append(j)
        elif n in PAINT:
            if n != 'n':
                ops = [dict(operator=seq[i].operator.name, args=[float(v) for v in seq[i].operator.args], ctm=list(seq[i - 1].state.ctm)) for i in path]
                r = _path_rect(ops, page)
                if r is not None:
                    if n in ('S', 's', 'B', 'B*', 'b', 'b*'):
                        m = pymupdf.Matrix(*before.ctm)
                        grow = _width(before) * max(abs(m.a) + abs(m.c), abs(m.b) + abs(m.d)) / 2
                        r = r + (-grow, -grow, grow, grow)
                    painted.append(r)
            path = []
        elif n == 'sh':
            # A shading fills the clip; it is bounded only by a clip the item sets itself.
            own = [x for x in before.clip if 'path' in x and seq[k].ordinal <= x['at'][1] <= b.ordinal]
            if not own:
                raise Refused('a line decoration shades without a clip of its own')
            painted.append(_path_rect(own[-1]['path'], page))
        elif n == 'Do':
            xo = resources.get('/XObject', {}).get(b.operator.args[0])
            xo = xo.get_object() if xo is not None else None
            if xo is None:
                raise Refused('a line decoration draws a missing XObject')
            if xo.get('/Subtype') == '/Form':
                x0, y0, x1, y1 = map(float, xo['/BBox'])
                m = pymupdf.Matrix(*map(float, xo.get('/Matrix', (1, 0, 0, 1, 0, 0)))) * pymupdf.Matrix(*before.ctm) * pymupdf.Matrix(page)
            else:
                x0, y0, x1, y1 = 0, 0, 1, 1
                m = pymupdf.Matrix(*before.ctm) * pymupdf.Matrix(page)
            painted.append(_points_rect([pymupdf.Point(x, y) * m for x in (x0, x1) for y in (y0, y1)], page))
        elif n in SHOW:
            for ev in starts.get(b.operator.start, []):
                for ch in ev.chars:
                    # A glyph the observation did not link to its code (a tab
                    # read as a space) is the one at its origin, linked to no other.
                    for i in ch.source_orders or _unlinked(c, ch.origin):
                        glyphs.add(i)
                        painted.append(pymupdf.Rect(c.actual[i]['bbox']))
    return placed_move._union(painted), glyphs


def _check_level(seq, lo, hi, depth, program_bytes):
    """Operators at the group's own level: the replay of the state it leaves, or a refusal."""
    replay = []
    for j in range(lo, hi + 1):
        b = seq[j]
        n = b.operator.name
        if n == 'BDC' and '/MCID' in str(b.operator.args[1:]):
            raise Refused('a line decoration holds structured marked content')
        level = b.q_depth if n == 'Q' else seq[j - 1].q_depth
        if level != depth:
            continue
        if n in ('cm', 'W', 'W*'):
            raise Refused(f'a line decoration changes the CTM or the clip at its own level ({n})')
        if n == 'sh':
            raise Refused('a line decoration shades at its own level')
        if n in REPLAY:
            replay.append(program_bytes[b.operator.start:b.operator.end])
        elif n not in PATH | PAINT | TEXT | MARKS | {'q', 'Q', 'BT', 'ET', 'Do'}:
            raise Refused(f'a line decoration uses {n} at its own level')
    return b'\n'.join(replay)


def _annotations(c, page_number, region, bounds, page, others=()):
    """Annotations over the line: links inside the group, or a refusal.

    ``others``: areas of other elements moved with it (chain); an annotation
    inside one of them, or mostly over another line, is not this line's."""
    reader = c.reader
    page_obj = reader.pages[page_number - 1]
    annots = page_obj.get('/Annots')
    out = []
    if annots is None:
        return out
    counts = {}
    for p in reader.pages:
        for ref in (p.get('/Annots') or []):
            if hasattr(ref, 'idnum'):
                counts[ref.idnum] = counts.get(ref.idnum, 0) + 1
    for ref in annots.get_object():
        a = ref.get_object()
        x0, y0, x1, y1 = map(float, a['/Rect'])
        rect = _page_rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), (1, 0, 0, 1, 0, 0), page)
        if not rect.intersects(region) or any((pymupdf.Rect(r) + (-TOL, -TOL, TOL, TOL)).contains(rect) for r in others):
            continue
        if others and (rect & region).height < .5 * rect.height:
            continue
        if a.get('/Subtype') != '/Link' or '/Popup' in a or '/IRT' in a:
            raise Refused(f"the line of a following paragraph carries a {a.get('/Subtype')} annotation")
        if not (bounds + (-TOL, -TOL, TOL, TOL)).contains(rect):
            raise Refused('a link over the line reaches past its line decoration')
        if not hasattr(ref, 'idnum') or counts.get(ref.idnum) != 1:
            raise Refused('a link over the line is not this page\'s own')
        quads = [float(v) for v in a.get('/QuadPoints', [])]
        for i in range(0, len(quads) - 1, 2):
            p = pymupdf.Point(quads[i], quads[i + 1]) * pymupdf.Matrix(page)
            if not (bounds + (-TOL, -TOL, TOL, TOL)).contains(p):
                raise Refused('a link\'s QuadPoints reach past its line decoration')
        action = a.get('/A')
        uri = action.get_object().get('/URI') if action is not None and '/URI' in action.get_object() else None
        out.append(dict(xref=ref.idnum, rect=list(rect), pdf_rect=[x0, y0, x1, y1], quads=quads, uri=str(uri) if uri is not None else None))
    return out


def group(c, paragraph, page_number, delta, frames, obstacles, page, units=(), taken=(), moving=(), slot=None):
    """The candidate line decoration of following ``paragraph`` moved by ``delta`` (page y), or a refusal.

    ``taken``: glyphs of the other paragraphs the edit draws (the edited one,
    the other followers); a group never carries them. Other glyphs drawn on
    the line (a search box's label) are carried with it and listed.
    ``moving``: page areas of other content moved by the same translation (the
    other followers' placed objects); it is no obstacle to the group.
    ``slot``: the line's height up to its neighbours (page y0, y1; chain): what
    is drawn there is drawn on the line too."""
    program = next(((s['stream_xref'], tuple(tuple(i) for i in s['invocation'])) for s in paragraph.scopes if s['kind'] == 'fixed'), None)
    if program is None:
        # A line without a placed object (chain): the one program drawing its text.
        drawn = {(ev.stream_xref, ev.invocation) for ev in c.events if any(i in set(paragraph.ids) for ch in ev.chars for i in ch.source_orders)}
        program = next(iter(drawn)) if len(drawn) == 1 else None
    if program is None or program[1]:
        raise Refused('a line decoration is moved only in the page\'s own content')
    seq = c.stream_boundaries[program]
    data = c.streams[program[0]]
    resources = c.reader.pages[page_number - 1]['/Resources'].get_object()
    index = {b.operator.start: n for n, b in enumerate(seq) if n}
    ids = set(paragraph.ids)
    # Seeds: the paragraph's placed objects and the text objects that draw its glyphs.
    seeds = [(index[u['start']], index[placed_move.operator_start(c, program, u['end'])]) for u in units]
    for ev in c.events:
        if (ev.stream_xref, ev.invocation) == program and any(i in ids for ch in ev.chars for i in ch.source_orders):
            j = index[ev.operator.start]
            lo = j
            while seq[lo - 1].text_object:
                lo -= 1
            seeds.append((lo, _item_end(seq, lo)))
    if not seeds:
        raise Refused('a following paragraph draws nothing to move')
    lo, hi = min(s for s, _ in seeds), max(e for _, e in seeds)
    starts = {s: e for s, e in seeds}
    texts = [pymupdf.Rect(c.actual[i]['bbox']) for i in ids if c.actual[i]['unicode'].strip()]
    band = placed_move._union(texts + [pymupdf.Rect(u['bounds']) for u in units])
    column = next((f for f in map(pymupdf.Rect, frames) if f.x0 < band.x1 and band.x0 < f.x1), band)
    # The line spans its column's panel (the background holding it) when there
    # is one about the column's width: a search button may reach past the text.
    panels = [pymupdf.Rect(o['bounds']) for o in obstacles if o['kind'] == 'container' and pymupdf.Rect(o['bounds']).contains(band)]
    panel = min(panels, key=lambda r: r.get_area(), default=None)
    if panel is not None and panel.width < 2 * column.width:
        column = pymupdf.Rect(min(column.x0, panel.x0), column.y0, max(column.x1, panel.x1), column.y1)
    region = pymupdf.Rect(column.x0 - TOL, band.y0 - TOL, column.x1 + TOL, band.y1 + TOL)
    if slot is not None:
        region = pymupdf.Rect(region.x0, min(region.y0, slot[0]), region.x1, max(region.y1, slot[1]))
    if seq[lo - 1].text_object or seq[lo - 1].pending_path or seq[lo - 1].pending_clip:
        raise Refused('a line decoration starts inside a text object or a path')
    depth = seq[lo - 1].q_depth
    # The items between the seeds, then the items after them drawn on the line.
    painted, glyphs = [], set()
    k = lo
    while k <= hi:
        e = starts.get(k) or _item_end(seq, k)
        rect, g = _paint(c, seq, k, e, page, resources, program)
        if rect is not None and not region.contains(rect):
            raise Refused('a following paragraph is drawn together with content beyond its line')
        painted.append(rect)
        glyphs |= g
        hi, k = max(hi, e), e + 1
    k = hi + 1
    while k < len(seq):
        n = seq[k].operator.name
        if n in REPLAY:
            k += 1
            continue
        if n not in PATH | {'q', 'BT', 'BDC', 'BMC', 'Do'}:
            break
        e = _item_end(seq, k)
        try:
            rect, g = _paint(c, seq, k, e, page, resources, program)
        except Refused:
            break
        if rect is None or not region.contains(rect) or g & set(taken):
            break
        painted.append(rect)
        glyphs |= g
        hi, k = e, e + 1
    if not ids <= glyphs or glyphs & set(taken):
        raise Refused('a line decoration does not hold its paragraph\'s glyphs alone')
    carried = glyphs - ids
    # The line holds the text the group carries too.
    band = placed_move._union([band] + [pymupdf.Rect(c.actual[i]['bbox']) for i in carried if c.actual[i]['unicode'].strip()])
    region = pymupdf.Rect(column.x0 - TOL, band.y0 - TOL, column.x1 + TOL, band.y1 + TOL)
    if slot is not None:
        region = pymupdf.Rect(region.x0, min(region.y0, slot[0]), region.x1, max(region.y1, slot[1]))
    end = seq[hi]
    if end.q_depth != depth or end.text_object or end.pending_path or end.pending_clip or end.marked_content_depth != seq[lo - 1].marked_content_depth:
        raise Refused('a line decoration is not a balanced drawing')
    replay = _check_level(seq, lo, hi, depth, data)
    # What each item paints; paint over one of them within the line is the
    # group's too (an image inside its XObject, reported unclipped). The stage
    # verifies that each of them moved exactly.
    rects = [pymupdf.Rect(r) for r in painted if r is not None]
    for o in obstacles:
        box = pymupdf.Rect(o['bounds'])
        if o['kind'] not in ('container', 'foreign-text') and region.contains(box) and any(box.intersects(r) for r in rects):
            rects.append(box)
    bounds = placed_move._union(rects)
    # Annotations over the line: its text's band, as for a following paragraph's line.
    line = pymupdf.Rect(column.x0 - TOL, band.y0 - TOL, column.x1 + TOL, band.y1 + TOL)
    annotations = _annotations(c, page_number, line, bounds, page, others=moving if slot is not None else ())
    whole = placed_move._union([bounds] + [pymupdf.Rect(a['rect']) for a in annotations])
    new = whole + (0, delta, 0, delta)
    rects += [pymupdf.Rect(a['rect']) for a in annotations]
    own = lambda box: any((r + (-.5, -.5, .5, .5)).contains(box) for r in rects)
    # Nothing else stays on the line in its column; nothing else is where it is or goes.
    across = pymupdf.Rect(column.x0, band.y0 + .5, column.x1, band.y1 - .5)
    together = [pymupdf.Rect(r) + (-.5, -.5, .5, .5) for r in moving]
    for o in obstacles:
        box = pymupdf.Rect(o['bounds'])
        if any(r.contains(box) for r in together):
            continue
        if o['kind'] == 'container':
            if (box.intersects(whole) or box.intersects(new)) and not (box.contains(whole) and box.contains(new)):
                raise Refused('a line decoration would leave or enter a background')
            continue
        if o['kind'] != 'foreign-text' and own(box) or o['kind'] == 'foreign-text' and any(box == pymupdf.Rect(c.actual[i]['bbox']) for i in carried):
            continue
        if box.intersects(across) and not any(t.contains(box) for t in texts):
            raise Refused('the line of a following paragraph holds content (' + o['kind'] + ') outside its line decoration')
        hit = (box & new)
        if not hit.is_empty and hit.width > .05 and hit.height > .05:
            raise Refused('a line decoration would meet protected ' + o['kind'])
    for clip in seq[lo - 1].state.clip:
        area = clip_rect(clip, page)
        if area is None or not area.contains(new) or not area.contains(whole):
            raise Refused('the clip of a line decoration does not hold it moved')
    t = placed_move.local_shift(seq[lo - 1].state.ctm, page, delta)
    pdf = placed_move.local_shift((1, 0, 0, 1, 0, 0), page, delta)
    start, stop = seq[lo].operator.start, end.operator.end
    evidence = dict(page=page_number, paragraph=paragraph.text[:40], operators=[start, stop], operator_count=hi - lo + 1,
                    bytes_sha256=hashlib.sha256(data[start:stop]).hexdigest(), bounds=[round(v, 2) for v in whole],
                    moved_to=[round(v, 2) for v in new], translation=round(delta, 3),
                    drawings=sum(seq[j].operator.name in PAINT and seq[j].operator.name != 'n' for j in range(lo, hi + 1)),
                    shadings=sum(seq[j].operator.name == 'sh' for j in range(lo, hi + 1)),
                    xobjects=sorted({str(seq[j].operator.args[0]) for j in range(lo, hi + 1) if seq[j].operator.name == 'Do'}),
                    glyphs=len(ids), carried_text=''.join(c.actual[i]['unicode'] for i in sorted(carried)), placed_objects=len(units), replayed_state=[seq[j].operator.name for j in range(lo, hi + 1)
                                                                            if seq[j].operator.name in REPLAY and seq[j - 1].q_depth == depth],
                    annotations=[dict(xref=a['xref'], subtype='/Link', rect=[round(v, 2) for v in a['rect']], uri=a['uri']) for a in annotations],
                    adjacent=sorted({o['kind'] for o in obstacles if o['kind'] != 'container'
                                     and pymupdf.Rect(o['bounds']).intersects(new + (-3, -3, 3, 3)) and not own(pymupdf.Rect(o['bounds']))}))
    evidence['id'] = 'line-decoration-' + hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return dict(evidence=evidence, program=program, start=start, end=stop, bounds=whole, old=list(whole), new=list(new), state=seq[lo - 1].state,
                glyphs=sorted(glyphs), translation=t, pdf_translation=pdf, replay=replay, annotations=annotations)


def move_annotations(writer, lookup, groups):
    """Translate the approved groups' links (Rect, QuadPoints); everything else of them stays."""
    for g in groups:
        dx, dy = g['pdf_translation']
        for a in g['annotations']:
            obj = writer.get_object(lookup[a['xref']])
            x0, y0, x1, y1 = a['pdf_rect']
            obj[NameObject('/Rect')] = ArrayObject([FloatObject(round(v, 4)) for v in (x0 + dx, y0 + dy, x1 + dx, y1 + dy)])
            if a['quads']:
                q = a['quads']
                obj[NameObject('/QuadPoints')] = ArrayObject([FloatObject(round(q[i] + (dx if i % 2 == 0 else dy), 4)) for i in range(len(q))])
