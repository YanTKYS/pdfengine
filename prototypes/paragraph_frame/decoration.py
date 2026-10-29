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

from pdfeditor.content_stream import source_translate

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


def _item_start(seq, k):
    """Index of the first operator of the item ending at ``k``, or None."""
    n = seq[k].operator.name
    j = k
    if n == 'Q':
        j -= 1
        while j > 0 and not (seq[j].operator.name == 'q' and seq[j - 1].q_depth == seq[k].q_depth):
            j -= 1
    elif n == 'ET':
        while j > 0 and seq[j].operator.name != 'BT':
            j -= 1
    elif n == 'EMC':
        j -= 1
        while j > 0 and not (seq[j].operator.name in ('BDC', 'BMC') and seq[j - 1].marked_content_depth == seq[k].marked_content_depth):
            j -= 1
    elif n in PAINT:
        while j > 1 and seq[j - 1].operator.name in PATH:
            j -= 1
    elif n != 'Do':
        return None
    # InDesign's placed object: BDC q … EMC Q.
    if n == 'Q' and j > 1 and seq[j - 1].operator.name in ('BDC', 'BMC') and _item_end(seq, j - 1) == k:
        return j - 1
    return j if j > 0 and _item_end(seq, j) == k else None


def _solid(r):
    """A rule's area (a stroke reported without height or width) as a thin band."""
    r = pymupdf.Rect(r)
    if r.height < .1:
        r.y0, r.y1 = r.y0 - .1, r.y1 + .1
    if r.width < .1:
        r.x0, r.x1 = r.x0 - .1, r.x1 + .1
    return r


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


POSITION = {'Td', 'TD', 'T*', 'Tm'}


def _before(ev):
    """The text matrix (source arithmetic) before the showing operator ``ev``."""
    shift = 0.
    for atom in ev.atoms:
        if not isinstance(atom, float):
            a, b, c, d, e, f = atom.source_text_matrix
            return (a, b, c, d, e - shift * a, f - shift * b)
        shift += -atom / 1000 * ev.state.size * ev.state.tz / 100
    return None


def _color(state):
    """Operators that set ``state``'s fill colour again, or a refusal."""
    op, args = state.fill
    if any(str(v).startswith('/') for v in args):
        raise Refused('a separated line is filled with a pattern')
    out = b' '.join(str(v).encode() for v in args) + b' ' + op.encode()
    if op in ('sc', 'scn'):
        if not state.fill_space:
            raise Refused('a separated line is filled in an unnamed colour space')
        out = state.fill_space.encode() + b' cs ' + out
    return out


def _extgstate(resources, name):
    """The entries ExtGState ``name`` sets, or a refusal for one that cannot be set again elsewhere.

    A soft mask is fixed in the CTM at its gs, and a font set by gs is not
    the text state's: neither is set again under a translation."""
    gs = (resources.get('/ExtGState') or {}).get(name)
    if gs is None:
        raise Refused(f'a text object holding a line uses an unknown ExtGState {name}')
    gs = gs.get_object()
    if '/Font' in gs or str(gs.get('/SMask', '/None')) != '/None':
        raise Refused(f'a text object holding a line sets a soft mask or a font by ExtGState {name}')
    return {str(k): str(v) for k, v in gs.items() if k != '/Type'}


def separate(c, seq, bt, et, program, ids, taken, region, page, delta, data, resources):
    """The showing operators of the text object ``seq[bt..et]`` that draw the
    line's glyphs (``ids``), drawn on their own at their place, or a refusal.

    A text object that draws the line and other text (an InDesign text frame
    drawing a telephone number and the page's header) cannot move as a whole.
    Its operators that draw only glyphs of the line are taken out of it and
    drawn again, as they are (codes, kerning), in a text object of their own
    where they were in the content: ``q <translation> cm BT <ExtGState, font,
    spacing, scale, rise, mode, colour, text matrix> <operator> … ET Q``,
    right before the text object (the line is its first text), right after
    it (its last text), or between ``ET`` and ``BT <line matrix> Tm`` that
    end it and begin it again there. What stays draws as before:

    * each operator draws only glyphs of the line, or none of them (no
      operator is cut);
    * after a taken operator, the next showing operator that stays is placed
      by Td, TD, T*, Tm (or is ' or "): it reads the line matrix, which a
      showing operator never changes;
    * nothing in the text object changes the graphics state the separated
      text needs again after it (no q, Q, cm, inline image) and no text
      clips (rendering modes 4-7); a fill colour set in it is set again; an
      ExtGState set in it (InDesign: transparency, overprint) is set again
      by its name when it holds no soft mask and no font, and the entries
      the line read are exactly those it then reads;
    * marked content in the text object holds no MCID, and either none of
      the separated operators or (an ActualText span of a tab) only them: it
      moves with them, its other operators staying;
    * the separated text fills (mode 0) or is invisible (mode 3) in a device
      or named colour space."""
    if seq[bt].operator.name != 'BT' or seq[et].operator.name != 'ET':
        raise Refused('a separated line is not a text object')
    events = {}
    for ev in c.events:
        if (ev.stream_xref, ev.invocation) == program and seq[bt].operator.start < ev.operator.start < seq[et].operator.start:
            events.setdefault(ev.operator.start, []).append(ev)
    shown, mine, glyphs, rects, shows = [], [], set(), [], []
    # ExtGState entries set in the text object before each operator, and the gs operators setting them.
    entries, states, applied, open_, spans = {}, {}, [], [], {}
    # The line matrix before each operator (source arithmetic, as interpreted).
    lm, lms = (1, 0, 0, 1, 0, 0), {}
    for k in range(bt + 1, et):
        n = seq[k].operator.name
        lms[k] = lm
        args = seq[k].operator.args
        if n == 'Tm':
            lm = tuple(map(float, args))
        elif n in ('Td', 'TD'):
            lm = source_translate(lm, float(args[0]), float(args[1]))
        elif n in ('T*', "'", '"'):
            lm = source_translate(lm, 0, -seq[k - 1].state.tl)
        if n in ('q', 'Q', 'cm', 'BI', 'Do', 'sh', 'BX', 'EX'):
            raise Refused(f'a text object holding a line changes state the line needs ({n})')
        if n == 'gs':
            entries = dict(entries, **_extgstate(resources, str(seq[k].operator.args[0])))
            applied.append(k)
            continue
        if n in ('BDC', 'BMC'):
            if n == 'BDC' and '/MCID' in str(seq[k].operator.args[1:]):
                raise Refused('a text object holding a line holds structured marked content')
            open_.append(k)
            spans[k] = [None, []]
        elif n == 'EMC':
            if not open_:
                raise Refused('a text object holding a line closes marked content it did not open')
            spans[open_.pop()][0] = k
        if n not in SHOW:
            continue
        for b in open_:
            spans[b][1].append(k)
        states[k] = (entries, list(applied), list(open_))
        evs = events.get(seq[k].operator.start, [])
        # (A glyph no extracted glyph matches, such as a space drawn by its own code, is placed below by where it is drawn.)
        if len(evs) != 1 or evs[0].error not in (None, 'ambiguous or unmatched glyph provenance'):
            raise Refused('a text-showing operator of a separated line is not interpreted once')
        ev = evs[0]
        if ev.state.tr >= 4:
            raise Refused('a text object holding a line clips with its text')
        own, free = [], []
        for ch in ev.chars:
            found = ch.source_orders or [i for i in _unlinked(c, ch.origin) if i in ids]
            own += [(i, i in ids) for i in found]
            if not found:
                free.append(pymupdf.Point(ch.origin))
        line = [i for i, m in own if m]
        if line and (len(line) != len(own) or any(i in taken for i, _ in own)):
            raise Refused('a text-showing operator draws glyphs of the line and other text')
        shows.append((k, ev, line, bool(own) and not line, free))
        if line:
            glyphs |= set(line)
            rects += [pymupdf.Rect(c.actual[i]['bbox']) for i in line]
    if not glyphs:
        raise Refused('a separated text object draws no glyph of the line')
    # Glyphs drawn without a match on a baseline of the line's glyphs, across
    # its column, are the line's.
    rect = placed_move._union(rects)
    baselines = {c.actual[i]['origin'][1] for i in glyphs}
    for k, ev, line, other, free in shows:
        on = [region.x0 <= p.x <= region.x1 and any(abs(p.y - y) < .05 for y in baselines) for p in free]
        mine_ = bool(line) or (bool(free) and all(on) and not other)
        if not all(on) if mine_ else any(on):
            raise Refused('a text-showing operator draws glyphs of the line and other text')
        if mine_ and len(states[k][2]) > 1:
            raise Refused('a separated line is drawn in nested marked content of its text object')
        shown.append((k, mine_))
        if mine_:
            mine.append((k, ev))
    if open_:
        raise Refused('marked content of a text object holding a line is not closed in it')
    # Marked content holding the line's text (an ActualText span of a tab)
    # holds nothing else and moves with it; its other operators (the span's
    # Tm) stay where they are.
    taken_ops = {k for k, _ in mine}
    moving = {b: e for b, (e, inside) in spans.items() if set(inside) & taken_ops}
    if any(not set(spans[b][1]) <= taken_ops for b in moving):
        raise Refused('a separated line shares marked content with other text of its text object')
    for k, ev in mine:
        if ev.state.tr not in (0, 3):
            raise Refused('a separated line is stroked')
        if ev.state.font is None or _before(ev) is None:
            raise Refused('a separated text-showing operator has no font or no glyph')
        if seq[k].operator.name == '"':
            raise Refused('a separated line sets its spacing with "')
        # The next showing operator that stays is placed from the line matrix.
        nxt = next(((j, m) for j, m in shown if j > k), None)
        while nxt is not None and nxt[1]:
            nxt = next(((j, m) for j, m in shown if j > nxt[0]), None)
        if nxt is not None:
            j = nxt[0]
            if seq[j].operator.name not in ("'", '"') and not any(seq[x].operator.name in POSITION for x in range(k + 1, j)):
                raise Refused('text after a separated line continues from where the line ends')
    if not region.contains(rect):
        raise Refused('a separated line is drawn beyond its line')
    for clip in seq[bt - 1].state.clip:
        area = clip_rect(clip, page)
        if area is None or not area.contains(rect) or not area.contains(rect + (0, delta, 0, delta)):
            raise Refused('the clip of a separated line does not hold it moved')
    t = placed_move.local_shift(seq[bt - 1].state.ctm, page, delta)
    # The line is drawn again where it keeps its place among the text
    # object's text (extraction order): right before the text object when
    # it comes first, right after it when it comes last; between text that
    # stays, the text object is ended before it and begun again after it at
    # the line matrix of that place (the text state outlives ET).
    staying = [j for j, m in shown if not m]
    runs = []
    for k, ev in mine:
        if runs and not any(runs[-1][-1][0] < j < k for j in staying):
            runs[-1].append((k, ev))
        else:
            runs.append([(k, ev)])
    edits, places = [], []
    for run in runs:
        first, last = run[0][0], run[-1][0]
        if not any(j < first for j in staying):
            at, now, read, place = seq[bt].operator.start, seq[bt - 1].state, {}, 'before'
        elif not any(j > last for j in staying):
            at, now, read, place = seq[et].operator.end, seq[et].state, entries, 'after'
        else:
            at, now, read, place = seq[first].operator.start, seq[first - 1].state, states[first][0], 'within'
        places.append(place)
        drawn, current = [], None
        # What the line's operator read differently from the state where it is
        # drawn again is set again.
        for k, ev in run:
            op = seq[k].operator
            raw = data[op.start:op.end]
            show = raw[:-1].rstrip() + b' Tj' if op.name == "'" else raw
            st = ev.state
            sets = []
            # The ExtGStates the line read, set again by name (the last one alone when it is enough).
            want, before, _ = states[k]
            if read != want:
                for tail in (before[-1:], before):
                    got = read
                    for j in tail:
                        got = dict(got, **_extgstate(resources, str(seq[j].operator.args[0])))
                    if got == want:
                        break
                else:
                    raise Refused('a separated line reads ExtGState entries set after it')
                sets += [str(seq[j].operator.args[0]).encode() + b' gs' for j in tail]
                read = got
            if (st.font.name if st.font else None) != (now.font.name if now.font else None) or float(st.size) != float(now.size):
                sets.append(st.font.name.encode() + b' ' + _num(st.size) + b' Tf')
            for name in ('tc', 'tw', 'tz', 'ts', 'tr'):
                if float(getattr(st, name)) != float(getattr(now, name)):
                    sets.append((str(getattr(st, name)).encode() if name == 'tr' else _num(getattr(st, name))) + b' ' + name.capitalize().encode())
            if st.fill != now.fill or st.fill_space != now.fill_space:
                sets.append(_color(st))
            now = st
            span = states[k][2][0] if states[k][2] else None
            if span != current:
                if current is not None:
                    drawn.append(b'EMC')
                if span is not None:
                    op = seq[span].operator
                    drawn.append(data[op.start:op.end])
                current = span
            drawn.append(b' '.join(sets + [b' '.join(_num(v) for v in _before(ev)) + b' Tm']) + b'\n' + show)
        if current is not None:
            drawn.append(b'EMC')
        payload = b'q 1 0 0 1 ' + _num(t[0]) + b' ' + _num(t[1]) + b' cm\nBT\n' + b'\n'.join(drawn) + b'\nET\nQ'
        # A ' taken out still moves the line matrix down (T*).
        keep = lambda k: b'T*' if seq[k].operator.name == "'" else b''
        for k, _ in run[1:] if place == 'within' else run:
            edits.append((seq[k].operator.start, seq[k].operator.end, keep(k)))
        for b in sorted({x for k, _ in run for x in states[k][2]}):
            edits += [(seq[b].operator.start, seq[b].operator.end, b''), (seq[moving[b]].operator.start, seq[moving[b]].operator.end, b'')]
        if place == 'before':
            edits.append((at, at, payload + b'\n'))
        elif place == 'after':
            edits.append((at, at, b'\n' + payload))
        else:
            restart = b'ET\n' + payload + b'\nBT\n' + b' '.join(_num(v) for v in lms[first]) + b' Tm\n'
            edits.append((seq[first].operator.start, seq[first].operator.end, restart + keep(first)))
    moved = b''.join(data[seq[k].operator.start:seq[k].operator.end] for k, _ in mine)
    return dict(text_object=[seq[bt].operator.start, seq[et].operator.end], edits=edits, drawn=places, translation=t,
                glyphs=glyphs, rect=rect, operators=len(mine), kept=sum(not m for _, m in shown),
                bytes_sha256=hashlib.sha256(moved).hexdigest())


def _items(seq, a, b):
    """Indices of the items starting in ``seq[a..b]`` (state operators alone are items too)."""
    k = a
    while k <= b:
        yield k
        k = _item_end(seq, k) + 1


def _on_line(c, seq, a, b, page, resources, program, region, taken):
    """Whether the item ``seq[a..b]`` draws within ``region`` and no glyph in ``taken`` (state alone does)."""
    try:
        rect, g = _paint(c, seq, a, b, page, resources, program)
    except Refused:
        return False
    return (rect is None and not g) or (rect is not None and region.contains(rect) and not g & set(taken))


def _runs(c, seq, k, step, page, resources, program, region, taken):
    """Runs of items drawn on the line from ``seq[k]`` going ``step`` (-1, 1):
    items wholly off the line are passed over (they stay); a state operator
    joins a run; anything else (an item partly on the line, the end of the
    enclosing q block) ends the walk."""
    runs, run = [], None
    while 0 < k < len(seq):
        name = seq[k].operator.name
        if name in REPLAY:
            k += step
            continue
        if step < 0:
            a, b = _item_start(seq, k), k
        else:
            a, b = k, (_item_end(seq, k) if name in PATH | {'q', 'BT', 'BDC', 'BMC', 'Do'} else None)
        if a is None or b is None:
            break
        try:
            rect, g = _paint(c, seq, a, b, page, resources, program)
        except Refused:
            break
        if rect is not None and region.contains(rect) and not g & set(taken):
            run = [a, b] if run is None else [min(run[0], a), max(run[1], b)]
        elif rect is not None and not rect.intersects(region) and not g & set(taken):
            if run:
                runs.append(run)
            run = None
        else:
            break
        k = a - 1 if step < 0 else b + 1
    if run:
        runs.append(run)
    return runs


def _num(v):
    return format(float(v), '.12g').encode()


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
    seeds = list(dict.fromkeys(seeds))
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
    # A line of a chain drawn in a text object with other text: its operators
    # are separated from it (separate); the rest of the line moves as drawn.
    splits = []
    if slot is not None:
        kept = []
        for s0, e0 in seeds:
            if seq[s0].operator.name == 'BT':
                rect, _ = _paint(c, seq, s0, e0, page, resources, program)
                if rect is not None and not region.contains(rect):
                    splits.append(separate(c, seq, s0, e0, program, ids, set(taken), region, page, delta, data, resources))
                    continue
            # A text object alone in its own q (state, cm) … Q is that item.
            j = s0 - 1
            while j > 0 and seq[j].operator.name in REPLAY | {'cm'}:
                j -= 1
            if seq[j].operator.name == 'q':
                q_end = _item_end(seq, j)
                if q_end > e0 and all(seq[x].operator.name in REPLAY for x in range(e0 + 1, q_end)):
                    s0, e0 = j, q_end
            kept.append((s0, e0))
        seeds = kept
    seeds.sort()
    starts = {s: e for s, e in seeds}
    # The ranges drawn as they are, each moved by q cm … Q: one, or (a line of
    # a chain) one per run of seeds drawn without other content between them.
    ranges = []
    for s0, e0 in seeds:
        if ranges and (slot is None or all(_on_line(c, seq, x, _item_end(seq, x), page, resources, program, region, taken)
                                           for x in _items(seq, ranges[-1][1] + 1, s0 - 1))):
            ranges[-1][1] = max(ranges[-1][1], e0)
        else:
            ranges.append([s0, e0])
    if slot is not None:
        # What the line draws before and after them, past what is drawn
        # wholly off the line (a text object of other text kept in place).
        if ranges:
            before, after = ranges[0][0] - 1, ranges[-1][1] + 1
        else:
            before = index[splits[0]['text_object'][0]] - 1
            after = index[placed_move.operator_start(c, program, splits[-1]['text_object'][1])] + 1
        ranges = sorted(_runs(c, seq, before, -1, page, resources, program, region, taken) + ranges
                        + _runs(c, seq, after, 1, page, resources, program, region, taken))
    # The items between the seeds, then the items after them drawn on the line.
    painted, glyphs = [x['rect'] for x in splits], {i for x in splits for i in x['glyphs']}
    for n, rng in enumerate(ranges):
        lo, hi = rng
        if seq[lo - 1].text_object or seq[lo - 1].pending_path or seq[lo - 1].pending_clip:
            raise Refused('a line decoration starts inside a text object or a path')
        k = lo
        while k <= hi:
            e = starts.get(k) or _item_end(seq, k)
            rect, g = _paint(c, seq, k, e, page, resources, program)
            if rect is not None and not region.contains(rect):
                raise Refused('a following paragraph is drawn together with content beyond its line')
            painted.append(rect)
            glyphs |= g
            hi, k = max(hi, e), e + 1
        if n == len(ranges) - 1 and slot is None:
            k = hi + 1
            while k < len(seq):
                name = seq[k].operator.name
                if name in REPLAY:
                    k += 1
                    continue
                if name not in PATH | {'q', 'BT', 'BDC', 'BMC', 'Do'}:
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
        rng[1] = hi
    if not ids <= glyphs or glyphs & set(taken):
        raise Refused('a line decoration does not hold its paragraph\'s glyphs alone')
    carried = glyphs - ids
    # The line holds the text the group carries too.
    band = placed_move._union([band] + [pymupdf.Rect(c.actual[i]['bbox']) for i in carried if c.actual[i]['unicode'].strip()])
    region = pymupdf.Rect(column.x0 - TOL, band.y0 - TOL, column.x1 + TOL, band.y1 + TOL)
    if slot is not None:
        region = pymupdf.Rect(region.x0, min(region.y0, slot[0]), region.x1, max(region.y1, slot[1]))
    replays = []
    for lo, hi in ranges:
        depth, end = seq[lo - 1].q_depth, seq[hi]
        if end.q_depth != depth or end.text_object or end.pending_path or end.pending_clip or end.marked_content_depth != seq[lo - 1].marked_content_depth:
            raise Refused('a line decoration is not a balanced drawing')
        replays.append(_check_level(seq, lo, hi, depth, data))
    wrapped = bool(ranges)
    lo, hi = ranges[0] if wrapped else (None, None)
    depth = seq[lo - 1].q_depth if wrapped else None
    end = seq[hi] if wrapped else None
    replay = replays[0] if wrapped else b''
    # What each item paints; paint over one of them within the line is the
    # group's too (an image inside its XObject, reported unclipped). The stage
    # verifies that each of them moved exactly.
    rects = [pymupdf.Rect(r) for r in painted if r is not None]
    area = _solid if slot is not None else pymupdf.Rect
    for o in obstacles:
        box = area(o['bounds'])
        # (A line of a chain takes images only: other paint over it is drawn apart.)
        if (o['kind'] == 'image' if slot is not None else o['kind'] not in ('container', 'foreign-text')) and region.contains(box) and any(box.intersects(r) for r in rects):
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
        box = area(o['bounds'])
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
    for clip in [x for a, _ in ranges for x in seq[a - 1].state.clip]:
        area = clip_rect(clip, page)
        if area is None or not area.contains(new) or not area.contains(whole):
            raise Refused('the clip of a line decoration does not hold it moved')
    t = placed_move.local_shift(seq[lo - 1].state.ctm, page, delta) if wrapped else splits[0]['translation']
    pdf = placed_move.local_shift((1, 0, 0, 1, 0, 0), page, delta)
    start, stop = (seq[lo].operator.start, end.operator.end) if wrapped else (None, None)
    span = [j for a, b in ranges for j in range(a, b + 1)]
    evidence = dict(page=page_number, paragraph=paragraph.text[:40], operators=[start, stop], operator_count=len(span),
                    bytes_sha256=hashlib.sha256(data[start:stop] if wrapped else b'').hexdigest(), bounds=[round(v, 2) for v in whole],
                    moved_to=[round(v, 2) for v in new], translation=round(delta, 3),
                    drawings=sum(seq[j].operator.name in PAINT and seq[j].operator.name != 'n' for j in span),
                    shadings=sum(seq[j].operator.name == 'sh' for j in span),
                    xobjects=sorted({str(seq[j].operator.args[0]) for j in span if seq[j].operator.name == 'Do'}),
                    glyphs=len(ids), carried_text=''.join(c.actual[i]['unicode'] for i in sorted(carried)), placed_objects=len(units), replayed_state=[seq[j].operator.name for j in span
                                                                            if seq[j].operator.name in REPLAY and seq[j - 1].q_depth == seq[next(a for a, b in ranges if a <= j <= b) - 1].q_depth],
                    annotations=[dict(xref=a['xref'], subtype='/Link', rect=[round(v, 2) for v in a['rect']], uri=a['uri']) for a in annotations],
                    adjacent=sorted({o['kind'] for o in obstacles if o['kind'] != 'container'
                                     and pymupdf.Rect(o['bounds']).intersects(new + (-3, -3, 3, 3)) and not own(pymupdf.Rect(o['bounds']))}))
    if len(ranges) > 1:
        evidence['ranges'] = [[seq[a].operator.start, seq[b].operator.end] for a, b in ranges]
    if splits:
        # Separated from text objects that draw other text too: what is taken out and what stays.
        evidence['separated'] = [dict(text_object=x['text_object'], operators_taken=x['operators'], operators_staying=x['kept'],
                                      drawn=x['drawn'], bytes_sha256=x['bytes_sha256'], text=''.join(c.actual[i]['unicode'] for i in sorted(x['glyphs'])),
                                      bounds=[round(v, 2) for v in x['rect']]) for x in splits]
    evidence['id'] = 'line-decoration-' + hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return dict(evidence=evidence, program=program, start=start, end=stop, bounds=whole, old=list(whole), new=list(new), state=seq[lo - 1].state if wrapped else None,
                glyphs=sorted(glyphs), translation=t, pdf_translation=pdf, replay=replay, annotations=annotations, splits=splits,
                ranges=[dict(start=seq[a].operator.start, end=seq[b].operator.end, translation=placed_move.local_shift(seq[a - 1].state.ctm, page, delta),
                             replay=r) for (a, b), r in zip(ranges, replays)])


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
