"""The rectangular clip of a text box, extended down with its paragraph on explicit approval (issue #118).

A Word text box clips its text to the box's inner rectangle: every text show
inside it runs under ``q <x y w h> re W* n … Q`` (the same rectangle set once
for the whole box and once more per show). A paragraph there cannot grow past
the clip's bottom: the added line would not show. This module finds a
**candidate extension** of that clip and describes it with its evidence; B2
extends it only when the user approves the candidate by its id
(``auto.propose(..., clips=[id])``). When the extended clip passes the bottom
of the box's panel, the panel's own extension (panel.candidate) is needed too
and must be approved by its own id; either alone is refused.

A candidate is the clip that stops the paragraph's growth when

* every instance of it is one ``re`` path set as the clip (``W`` or ``W*``,
  then ``n``), under an axis-aligned CTM, in the page's own content stream
  the edit rewrites (not a Form XObject); every instance of the same
  rectangle in that stream is extended (the rule ``W``/``W*`` is kept);
* it holds text only: no fill, stroke, image, shading or form is drawn under
  any instance, and no text but the paragraph's reaches past its bottom (the
  extension would show it);
* every other clip the paragraph is drawn under is a rectangle holding the
  extended one (a clip that would cut it, or a clip that is not one
  rectangle, refuses it);
* the band it adds (its bottom moved down by the paragraph's growth, the least
  the text needs: the clip's bottom margin is kept) lies on the page and
  meets nothing but the box's own panel.

Only the bottom moves: the top, the sides, the rule and the operators' order
stay; the ``re`` operands are rewritten in place. The id is a digest of the
page, the stream, the operators' bytes, the rectangle and the extension, so a
changed PDF or another edit is no longer approved.
"""
import hashlib

import pymupdf

from .engine import Refused
from .form_write import clip_rect
from . import panel

TOL = .05
PAINT = {'S', 's', 'f', 'F', 'f*', 'B', 'B*', 'b', 'b*', 'Do', 'sh', 'BI', 'EI'}


def instances(c, program):
    """Every single-rectangle clip set in ``program``: dict(at (the clip's id in the state), rect (page), rule, start, end, args, ctm)."""
    seq = c.stream_boundaries[program]
    page = pymupdf.Matrix(c.page.transformation_matrix)
    out = []
    for k in range(3, len(seq)):
        if (seq[k].operator.name == 'n' and seq[k - 1].operator.name in ('W', 'W*') and seq[k - 2].operator.name == 're'
                and not seq[k - 3].pending_path):
            op, ctm = seq[k - 2].operator, seq[k - 2].state.ctm
            out.append(dict(at=(program[0], seq[k].ordinal), rect=panel._rect([float(v) for v in op.args], ctm, page), rule=seq[k - 1].operator.name,
                            start=op.start, end=op.end, args=[float(v) for v in op.args], ctm=ctm,
                            axis=abs(ctm[1]) < 1e-9 and abs(ctm[2]) < 1e-9))
    return out


def _same(a, b, tol=TOL):
    return max(abs(p - q) for p, q in zip(a, b)) <= tol


def _held(c, program, ats):
    """(text events, paint operators) drawn under any of the clips ``ats`` in ``program``."""
    events = [ev for ev in c.events if (ev.stream_xref, ev.invocation) == program
              and any(tuple(x.get('at') or ()) in ats for x in ev.state.clip)]
    seq = c.stream_boundaries[program]
    paints = [seq[k].operator for k in range(1, len(seq)) if seq[k].operator.name in PAINT
              and any(tuple(x.get('at') or ()) in ats for x in seq[k - 1].state.clip)]
    return events, paints


def _glyphs(c, events):
    return [c.actual[i]['bbox'] for ev in events for ch in ev.chars for i in ch.source_orders if c.actual[i]['unicode'].strip()]


def _binding(c, program, ids, bottom):
    """(the clip rectangle that stops the paragraph at ``bottom``, the paragraph's events), or a refusal."""
    if program[1]:
        raise Refused('the text is drawn in a Form XObject')
    own = [ev for ev in c.events if any(i in ids for ch in ev.chars for i in ch.source_orders)]
    if {(ev.stream_xref, ev.invocation) for ev in own} != {program}:
        raise Refused('the paragraph is drawn in more than one content stream')
    page = c.page.transformation_matrix
    rects = []
    for ev in own:
        for x in ev.state.clip:
            if x.get('rule') not in ('W', 'W*'):
                raise Refused(f"the paragraph's text is clipped by a {x.get('rule')}")
            box = clip_rect(x, page)
            if box is None:
                raise Refused("the paragraph's text is clipped by a path that is not one rectangle")
            rects.append((box, tuple(x['at'])))
    stop = [b for b, _ in rects if abs(b.y1 - bottom) < .6]
    if not stop:
        raise Refused('no rectangular clip stops the paragraph')
    rect = stop[0]
    stack = lambda ev: [clip_rect(x, page) for x in ev.state.clip]
    if not all(any(_same(b, rect) for b in stack(ev)) for ev in own):
        raise Refused("the paragraph's text is not all drawn under the clip that stops it")
    return rect, own, rects


def candidate(c, page_number, program, ids, bottom, dy, obstacles, recorded=None, context=()):
    """The extension of the clip stopping the paragraph (glyph ``ids``) at ``bottom`` by ``dy`` (page y, down), or a refusal.

    ``obstacles``: the proposal's observed obstacles. ``recorded``: an extension stored by an earlier save (a
    shrink goes back no higher than the clip was before it). ``context``: what names the edit; it enters the id."""
    rect, own, rects = _binding(c, program, ids, bottom)
    old = pymupdf.Rect(rect)
    new = pymupdf.Rect(old.x0, old.y0, old.x1, old.y1 + dy)
    if dy < 0 and (recorded is None or new.y1 < recorded['old'][3] - TOL):
        raise Refused('a clip is made smaller only back to where an earlier approved extension took it from')
    if new.height <= 0:
        raise Refused('the clip would vanish')
    if new.y1 > c.page.rect.y1:
        raise Refused('the extended clip would leave the page')
    found = [x for x in instances(c, program) if _same(x['rect'], old)]
    ats = {x['at'] for x in found}
    if not all(at in ats for b, at in rects if _same(b, old)):
        raise Refused('the clip is not set by one rectangle path in the content stream')
    if not all(x['axis'] for x in found):
        raise Refused('the clip is set under a rotated or skewed CTM')
    # Every other clip the paragraph is drawn under holds the extended one.
    for b, at in rects:
        if at not in ats and not (b + (-TOL, -TOL, TOL, TOL)).contains(new):
            raise Refused(f'the extended clip would be cut by another clip ending at {b.y1:.2f}')
    # What the clip holds: text only, and nothing but the paragraph past its old bottom.
    events, paints = _held(c, program, ats)
    if paints:
        raise Refused(f"the clip holds a drawing or an image ({paints[0].name}): the extension would show more of it")
    mine = {id(ev) for ev in own}
    hidden = [b for b in _glyphs(c, [ev for ev in events if id(ev) not in mine]) if b[3] > old.y1 + TOL]
    if hidden:
        raise Refused(f'text drawn under the clip reaches past its bottom at {[round(v, 1) for v in hidden[0]]}: the extension would show it')
    # The band it adds (or removes) meets nothing but the box's panel (a container holding it) or the panel's paint.
    band = pymupdf.Rect(old.x0, min(old.y1, new.y1), old.x1, max(old.y1, new.y1))
    for o in obstacles:
        box = pymupdf.Rect(o['bounds'])
        if o['kind'] == 'container' and (box + (TOL, TOL, -TOL, -TOL)).contains(old):
            continue
        if o['kind'] in ('vector-border', 'filled-vector', 'vector') and any((pymupdf.Rect(b) + (-1, -1, 1, 1)).contains(box) and pymupdf.Rect(b).contains(old)
                                                                             for b in [x['bounds'] for x in obstacles if x['kind'] == 'container']):
            # The edge of the box's own panel outline: the panel's extension moves it (panel.candidate checks its band).
            continue
        if box.height < .1:
            box.y0, box.y1 = box.y0 - .1, box.y1 + .1
        if box.width < .1:
            box.x0, box.x1 = box.x0 - .1, box.x1 + .1
        inter = box & band
        if not inter.is_empty and inter.width > .01 and inter.height > .01:
            raise Refused(f"the extended clip would meet {o['kind']} at {[round(v, 1) for v in box]}")
    data = c.streams[program[0]]
    ident = hashlib.sha256('|'.join([str(page_number), str(program[0]), hashlib.sha256(data).hexdigest()] + [str(v) for v in context]
                                     + [f"{x['start']}:{x['end']}:" + data[x['start']:x['end']].hex() for x in found]
                                     + [repr([round(v, 3) for v in old]), repr(round(dy, 3))]).encode()).hexdigest()[:16]
    return dict(id='clip-extension-' + ident, page=page_number, old=[round(v, 3) for v in old], new=[round(v, 3) for v in new],
                extension=round(dy, 3), edges='bottom only (top, sides, rule and order kept)', rule=sorted({x['rule'] for x in found}),
                operators=[dict(offset=x['start'], operator=data[x['start']:x['end']].decode('latin1')) for x in found],
                holds=dict(text_shows=len(events), paragraph_shows=len(own), drawings=0), band=[round(v, 3) for v in band],
                reason=('the paragraph grows past the bottom of the clip its text is drawn under (a text box)' if dy > 0
                        else 'the paragraph is shorter: the clip goes back'),
                if_not_approved='refused: the added lines would not show' if dy > 0 else 'the clip stays as it is',
                _program=program, _items=[(x['start'], x['end'], x['args'], x['ctm']) for x in found])


def public(found):
    return panel.public(found)


def edits(c, found):
    """(start, end, replacement) of each ``re`` operator of the approved extension: only the bottom edge moves."""
    return panel.edits(c, found)


def drawn(c, found):
    """What the approved extension leaves drawn, for its record: the rewritten operators and the rule."""
    return dict(operators=sorted({r.decode('latin1') for _, _, r in edits(c, found)}), rule=found['rule'])


def verify(c, ids, stored):
    """'drawn as approved' when the paragraph's text is drawn under the stored extended clip as that save left it:
    one rectangle path, its operators among the recorded ones, the stored rule, none of the old rectangle left,
    holding text only; else why not."""
    if not all(k in stored for k in ('operators', 'rule', 'new', 'old')):
        return 'not drawn as approved: the record does not say how the clip was set'
    programs = {(ev.stream_xref, ev.invocation) for ev in c.events if any(i in ids for ch in ev.chars for i in ch.source_orders)}
    if len(programs) != 1:
        return 'not drawn as approved: the paragraph is not drawn in one content stream'
    program = next(iter(programs))
    try:
        rect, own, rects = _binding(c, program, ids, stored['new'][3])
    except Refused as exc:
        return 'not drawn as approved: ' + str(exc)
    if not _same(rect, stored['new']):
        return 'not drawn as approved: the clip is not the approved rectangle'
    data = c.streams[program[0]]
    found = [x for x in instances(c, program) if _same(x['rect'], rect)]
    if any(data[x['start']:x['end']].decode('latin1') not in stored['operators'] for x in found):
        return 'not drawn as approved: its operators changed'
    if sorted({x['rule'] for x in found}) != stored['rule']:
        return 'not drawn as approved: its rule changed'
    if any(_same(x['rect'], stored['old']) for x in instances(c, program)):
        return 'not drawn as approved: the clip is also set where it was before'
    events, paints = _held(c, program, {x['at'] for x in found})
    if paints:
        return 'not drawn as approved: it holds a drawing or an image'
    return 'drawn as approved'
