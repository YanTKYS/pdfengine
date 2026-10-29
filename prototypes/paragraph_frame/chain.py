"""Following elements that move with a growing paragraph on explicit approval (issue #103).

A paragraph that grows by a line pushes its following paragraphs down
(auto_writer). The lines below them that are not following paragraphs (an
InDesign article's "HP" line with its search box and link, its enquiry line
with icons and a telephone number) are protected: they bound the inferred
frame, and the growth is refused for capacity. Position alone never proves
that such a line belongs with the paragraph. This module finds the **chain**
of following elements the growth would meet, describes each with its
evidence, and B2 moves them, by the same translation as the following
paragraphs, only when the user approves every one by its id
(``auto.propose(..., chain=[ids])``); ``acknowledge_inference`` or a line
decoration's approval never approves one.

The chain, for a translation ``delta`` of the paragraphs' bottom:

* starts with the lines below the paragraphs in their column (auto_detect:
  ``chain_rows``) whose top lies within ``|delta|`` of what moves already,
  and grows with each line it takes, nearest first;
* takes a line as one element: the contiguous drawing that holds its glyphs
  and the items drawn on it (its placed objects, icons, a search box and the
  link over it: decoration.group, with the same proofs and refusals);
* fails when a line cannot be such an element, or when what the moved
  elements would meet is not a line (a rule, a panel's edge, an image, a
  heading box): the growth stays refused for capacity, with the reason.

The frame then reaches down to the next protected paint below the paragraphs
that is not an element of the chain, or to where the first element moved,
never past the container or clip that bounds it (no region the page does not
show). When a paragraph shrinks, the lines attached below it the same way are
listed as optional candidates: approved, they move back up with it (the edit
back of an approved chain from the saved PDF alone). When the page records
the lines earlier edits moved, only those are listed.
"""
import hashlib
import json

import pymupdf

from .engine import Refused
from . import decoration
from . import placed_move

EPS = .01


def _rect(r):
    return pymupdf.Rect(r)


def plan(c, paragraphs, spec, evidence, delta, followers=()):
    """The chain for the translation ``delta`` (page y), as
    dict(needed, candidates, units, unavailable, frame_bottom), or None when
    nothing below the paragraphs is reached.

    ``followers``: page areas of the following paragraphs' moved content (it
    moves by the same translation and is no obstacle)."""
    if spec.get('mode') == 'flow' or not delta or not spec.get('chain_rows'):
        return None
    frame = _rect(spec['frame'])
    page = c.page.transformation_matrix
    obstacles = evidence['observed']['obstacles']
    last = spec['last_bottom']
    grow = delta > 0
    # Growth reaches the lines below only past the frame's bottom.
    needed = grow and last + delta > frame.y1 + EPS
    if grow and not needed:
        return None
    zone = abs(delta)
    taken = {i for p in paragraphs for i in p.ids}
    lines = _lines(spec['chain_rows'])
    # A line's height reaches to its neighbours' text (its slot): what its
    # drawing holds between them is drawn on it (a search box above its text).
    # Across, it reaches an em past its text or the frame (a search button's
    # pointer may stand out of the column).
    for n, l in enumerate(lines):
        above = lines[n - 1]['bottom'] if n else min(last, l['top'])
        below = lines[n + 1]['top'] if n + 1 < len(lines) else l['bottom'] + 2 * decoration.TOL
        em = max(x['size'] for x in l['rows'])
        l['slot'] = (min(above, l['top']), max(below, l['bottom']))
        l['extent'] = pymupdf.Rect(min([frame.x0] + [x['x'] for x in l['rows']]) - em, l['slot'][0],
                                   max([frame.x1] + [x['pen_end'] for x in l['rows']]) + em, l['slot'][1])
    units, used, reach, unavailable = [], set(), last, None
    # Shrinking a page whose earlier edits recorded the lines they moved
    # (auto_conditions, PieceInfo): only those lines are attached below; a line
    # that was not moved (one a separation left in place) ends the chain.
    marks = None
    if not grow:
        from .auto_conditions import read_moved
        marks = {}
        for m in read_moved(c, spec['page']):
            for r in m['rows']:
                for g in r['glyphs']:
                    marks.setdefault(g[0], []).append((g[2], g[3]))
        marks = marks or None
    recorded = lambda l: all(any(abs(x - c.actual[i]['origin'][0]) < .01 and abs(y - c.actual[i]['origin'][1]) < .01
                                 for x, y in marks.get(c.actual[i]['unicode'], ())) for i in l['ids'] if c.actual[i]['unicode'].strip())
    # Lines are taken nearest first while one lies within reach of what moves.
    # An element is a line, or the lines below it its drawing holds too (a text
    # object drawing several lines): they move as one drawing.
    while not unavailable:
        hit = next((l for l in lines if id(l) not in used and l['top'] < reach + zone - EPS), None)
        if hit is None or marks is not None and not recorded(hit):
            break
        element = [hit]
        used.add(id(hit))
        while True:
            try:
                g = _element(c, spec, element, lines, followers, taken, delta, frame, obstacles, page)
                break
            except Refused as exc:
                after = lines.index(element[-1]) + 1
                if (any(m in str(exc) for m in MERGE) and after < len(lines) and id(lines[after]) not in used and len(element) < 8
                        and (marks is None or recorded(lines[after]))):
                    element.append(lines[after])
                    used.add(id(lines[after]))
                    continue
                unavailable = f"a line below cannot move with the paragraph ({''.join(l['text'] for l in element)[:24]}): {exc}"
                break
        if unavailable:
            break
        units.append(g)
        reach = max(reach, g['row']['bottom'], _rect(g['bounds']).y1)
    if not units and not unavailable and not grow:
        return None
    # Every element where it moves meets nothing that stays (exact areas).
    own = [_rect(u['old']) + (-.5, -.5, .5, .5) for u in units] + [_rect(r) + (-.5, -.5, .5, .5) for r in followers]
    for u in units if not unavailable else ():
        new = _rect(u['new'])
        # It stays on the page, above the shared bottom of side-by-side columns.
        if not c.page.rect.contains(new):
            unavailable = f"a following element ({u['row']['text'][:24]}) would leave the page"
        elif spec.get('column_bottom') is not None and max(x['baseline'] for x in u['row']['rows']) + delta > spec['column_bottom'] + EPS:
            unavailable = f"a following element ({u['row']['text'][:24]}) would pass the shared bottom of the side-by-side columns"
        if unavailable:
            units = units[:units.index(u)]
            break
        for o in obstacles:
            box = decoration._solid(o['bounds'])
            if o['kind'] == 'container' or any(a.contains(box) for a in own):
                continue
            hit = new & box
            if not hit.is_empty and hit.width > .05 and hit.height > .05:
                unavailable = f"a following element ({u['row']['text'][:24]}) would meet protected {o['kind']} at {[round(v, 1) for v in box]}"
                units = units[:units.index(u)]
                break
        if unavailable:
            break
    if unavailable and not grow:
        # Shrinking: what can follow up to the first line that cannot is offered.
        unavailable = None
    if not units and not grow:
        return None
    # The frame reaches down to the next protected paint below the paragraphs
    # that does not move, or to where the first element moved; never past its limit.
    own = [_rect(u['old']) + (-.5, -.5, .5, .5) for u in units] + [_rect(r) + (-.5, -.5, .5, .5) for r in followers]
    stays = sorted((o for o in obstacles if o['kind'] != 'container' and _rect(o['bounds']).y0 > last + .1 and _rect(o['bounds']).x0 < frame.x1
                    and _rect(o['bounds']).x1 > frame.x0 and not any(a.contains(_rect(o['bounds'])) for a in own)), key=lambda o: _rect(o['bounds']).y0)
    bottom = min([_rect(o['bounds']).y0 for o in stays] + [_rect(u['new']).y0 for u in units] + [c.page.rect.height]) - 1
    bottom = min(bottom, spec['frame_limit'])
    if grow and not unavailable and last + delta > bottom + EPS:
        # What stops it is no line below (a rule, a panel's edge, a heading box), or the frame's limit.
        stop = stays[0] if stays and _rect(stays[0]['bounds']).y0 - 1 <= bottom + EPS else None
        what = f"protected {stop['kind']} at {[round(v, 1) for v in stop['bounds']]}" if stop else 'the bottom of its container or clip'
        unavailable = (f'capacity: moved with its following elements, the paragraph would still meet {what}' if units else
                       f'capacity: the growth meets {what}, which is no line below the paragraph')
    if not units:
        return dict(needed=needed, candidates=[], units=[], unavailable=unavailable, frame_bottom=None)
    candidates = []
    for n, u in enumerate(units):
        ev = dict(u['evidence'], order=n + 1, line=u['row']['text'][:40], kind='following element',
                  reached=('the growth reaches it' if grow else 'attached below the shrinking paragraph'),
                  frame_bottom=[round(spec['frame'][3], 2), round(max(bottom, spec['frame'][3]), 2)])
        ev.pop('id', None)
        ev['id'] = 'following-element-' + hashlib.sha256(json.dumps(ev, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        candidates.append(ev)
        u['evidence'] = ev
    return dict(needed=needed, candidates=candidates, units=units, unavailable=unavailable, frame_bottom=max(bottom, spec['frame'][3]))


# Refusals of a line whose drawing holds the next line too: they move as one element.
MERGE = ('drawn together with content beyond its line', 'not a balanced drawing', "does not hold its paragraph's glyphs alone",
         'drawn in parts that do not move as one drawing')


def _lines(rows):
    """The lines below, nearest first: the groups drawn across each (a gutter
    splits an "HP" line into its icon and its search box's label)."""
    lines = []
    for r in rows:
        h = max(r['bottom'] - r['top'], .1)
        line = next((l for l in lines if min(l['bottom'], r['bottom']) - max(l['top'], r['top']) > .5 * min(h, max(l['bottom'] - l['top'], .1))), None)
        if line is None:
            lines.append(dict(parts=[r], top=r['top'], bottom=r['bottom']))
        else:
            line['parts'].append(r)
            line.update(top=min(line['top'], r['top']), bottom=max(line['bottom'], r['bottom']))
    for l in lines:
        l['parts'].sort(key=lambda r: min(x['x'] for x in r['rows']))
        l.update(ids=[i for r in l['parts'] for i in r['ids']], rows=sorted((x for r in l['parts'] for x in r['rows']), key=lambda x: (x['baseline'], x['x'])),
                 text=''.join(r['text'] for r in l['parts']))
    return sorted(lines, key=lambda l: l['top'])


def _element(c, spec, element, lines, followers, taken, delta, frame, obstacles, page):
    """One element of the chain (decoration.group over its lines' glyphs), or a refusal."""
    ids = [i for l in element for i in l['ids']]
    rows = [x for l in element for x in l['rows']]
    slot = (element[0]['slot'][0], element[-1]['slot'][1])
    extent = element[0]['extent']
    for l in element[1:]:
        extent = extent | l['extent']
    p = spec['movable'](ids, rows)
    placed = _units(c, p)[1] if p.fixed else []
    # Its own glyphs are no obstacle to it (the paragraphs' are not listed either).
    mine = {tuple(round(v, 3) for v in c.actual[i]['bbox']) for i in ids}
    seen = [o for o in obstacles if not (o['kind'] == 'foreign-text' and tuple(round(v, 3) for v in o['bounds']) in mine)]
    # The other lines move with it or are checked where they stay (plan, second pass).
    others = [l for l in lines if l not in element]
    moving = [_rect(r) for r in followers] + [l['extent'] for l in others]
    column = pymupdf.Rect(extent.x0, frame.y0, extent.x1, max(frame.y1, slot[1]))
    g = decoration.group(c, p, spec['page'], delta, [column], seen, page, units=placed,
                         taken=set(taken) | {i for l in others for i in l['ids']}, moving=moving, slot=slot)
    if not set(ids) <= set(g['glyphs']):
        raise Refused('the line is drawn in parts that do not move as one drawing')
    row = dict(text=''.join(l['text'] for l in element), rows=rows, ids=ids, top=element[0]['top'], bottom=max(l['bottom'] for l in element), paragraph=p)
    return dict(g, row=row)


def _units(c, paragraph):
    """The placed_move units of a line and their program, or a refusal."""
    program = next((s['stream_xref'], tuple(tuple(i) for i in s['invocation'])) for s in paragraph.scopes if s['kind'] == 'fixed')
    return program, [placed_move.unit(c, paragraph, s, program, c.page.transformation_matrix) for s in paragraph.scopes if s['kind'] == 'fixed']


def describe(found):
    """The chain's evidence for the proposal."""
    return dict(translation=round(found['units'][0]['evidence']['translation'], 3) if found['units'] else None,
                needed=found['needed'], candidates=found['candidates'],
                frame_bottom=None if found['frame_bottom'] is None else round(found['frame_bottom'], 2),
                **({'unavailable': found['unavailable']} if found['unavailable'] else {}))
