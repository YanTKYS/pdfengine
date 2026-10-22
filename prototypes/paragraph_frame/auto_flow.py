"""Same-page column flow: columns, continuation evidence and multi-column reflow.

A PDF keeps no text-frame threads (unless article beads exist), so the flow is
inferred from what the page proves:

* column blocks: same-size paragraph groups stacked in one horizontal range;
* a link A -> B between neighbouring blocks requires the same body font, size,
  colour and line pitch, the same measure, B directly to the right of A and
  starting on A's first line, A at least as deep as B, B starting with body
  text, no protected paint above B in its column, and paint order A then B
  with no other text in between;
* evidence classes:
    - determined: one tagged /P spans the break, consecutive tagged /P siblings,
      or (untagged) a paragraph split at a full last line that does not end a
      sentence;
    - candidate: everything else that matches; it is used only after the user
      approves the link by its id;
    - rejected: anything that fails the checks above, or a tagged structure
      that does not connect the blocks.

Reflow re-breaks only the edited paragraph. Every following line of the flow
keeps its glyphs, line breaks and offset in its column and moves as a unit
(the columns share one measure), like a frame-threaded layout engine. Regions
never grow: the shared bottom is the deepest original line of the flow,
capped by protected paint in each column. Text that does not fit is refused;
nothing is shrunk or moved onto other content.
"""
from statistics import median
from uniseg.linebreak import line_break_boundaries
from .engine import Refused
from .auto_layout import fit, balance_tail, _justification, _squeezed

SENTENCE_END = '。．.!?！？」』）)'
DESCENT = .3  # em below a baseline that new ink may use


def style_of(c, rows):
    """Most common (font resource, size, fill) of the rows' glyphs."""
    ids = {i for r in rows for i in r['glyph_ids']}
    counts = {}
    for e in c.events:
        n = sum(1 for ch in e.chars for i in ch.source_orders if i in ids and ch.text.strip())
        if n:
            key = (e.state.font.name, round(e.state.size * abs(e.text_matrix[3] or 1), 2), e.state.fill[0], tuple(round(float(v), 3) for v in e.state.fill[1]))
            counts[key] = counts.get(key, 0) + n
    return max(counts, key=counts.get) if counts else None


def blocks_of(groups):
    """Column blocks: indices of same-size paragraph groups stacked in one column."""
    blocks = []
    for gi, g in enumerate(groups):
        size = g[0]['size']
        for block in reversed(blocks):
            last = groups[block[-1]]
            lo, hi = min(r['x'] for r in last), max(r['pen_end'] for r in last)
            if (abs(last[0]['size'] - size) < .1 and min(r['x'] for r in g) < hi and lo < max(r['pen_end'] for r in g)
                    and 0 < g[0]['baseline'] - last[-1]['baseline'] <= 3 * size and abs(min(r['x'] for r in g) - lo) <= 2 * size):
                block.append(gi)
                break
        else:
            blocks.append([gi])
    return blocks


def _pitch(gaps):
    """Line pitch: gaps near the smallest one (a group may hold a paragraph gap)."""
    return median(g for g in gaps if g <= 1.15 * min(gaps)) if gaps else None


def describe(c, groups, block):
    rows = [r for gi in block for r in groups[gi]]
    gaps = [b['baseline'] - a['baseline'] for gi in block for a, b in zip(groups[gi], groups[gi][1:])]
    breaks = [groups[b][0]['baseline'] - groups[a][-1]['baseline'] for a, b in zip(block, block[1:])]
    continuation = [r['x'] for gi in block for r in groups[gi][1:]] or [r['x'] for r in rows]
    return dict(left=min(continuation), right=max(r.get('right', r['pen_end']) for r in rows), top=rows[0]['baseline'],
                bottom=max(r['baseline'] for r in rows), ink_bottom=max(r['bottom'] for r in rows), size=rows[0]['size'],
                pitch=_pitch(gaps), gap=median(breaks) if breaks else None,
                style=style_of(c, rows), first_style=style_of(c, groups[block[0]]), last_style=style_of(c, groups[block[-1]]),
                ids=sorted(i for r in rows for i in r['glyph_ids']), rows=len(rows))


def _next_sibling(reader, a, b):
    """Whether tagged paragraph b directly follows paragraph a under the same parent."""
    pa, pb = reader.get_object(a), reader.get_object(b)
    parent = pa.raw_get('/P')
    if parent is None or pb.raw_get('/P') is None or parent.idnum != pb.raw_get('/P').idnum:
        return False
    kids = parent.get_object()['/K']
    kids = [k.idnum for k in (kids if isinstance(kids, list) else [kids]) if hasattr(k, 'idnum')]
    return a in kids and b in kids and kids.index(b) == kids.index(a) + 1


def link(c, groups, blocks, info, a, b, parent_of, tagged, obstacles):
    """Evidence for block a continuing in block b, or a rejection reason."""
    A, B = info[a], info[b]
    if A['style'] != B['style'] or abs(A['size'] - B['size']) > .1:
        return dict(status='rejected', reason='different body font, size or colour')
    if B['first_style'] != A['style']:
        return dict(status='rejected', reason='the next column starts with a heading or differently styled text')
    if A['pitch'] and B['pitch'] and abs(A['pitch'] - B['pitch']) > .05 * A['pitch']:
        return dict(status='rejected', reason='different line pitch')
    width_a, width_b = A['right'] - A['left'], B['right'] - B['left']
    if abs(width_a - width_b) > .5 * A['size']:
        return dict(status='rejected', reason='different column width')
    if abs(B['top'] - A['top']) > .5 * (A['pitch'] or A['size']):
        return dict(status='rejected', reason='the columns do not start on the same first line')
    if A['bottom'] < B['bottom'] - .5:
        return dict(status='rejected', reason='the first column is not filled to the flow bottom')
    above = [o['kind'] for o in obstacles if o['kind'] != 'container' and o['bounds'][0] < B['right'] and o['bounds'][2] > B['left']
             and o['bounds'][3] > A['top'] - 2 * A['size'] and o['bounds'][1] < B['top'] - B['size']]
    if above:
        return dict(status='rejected', reason='protected paint (' + ', '.join(sorted(set(above))) + ') above the text of the next column')
    lo, hi = max(A['ids']), min(B['ids'])
    own = set(A['ids']) | set(B['ids'])
    if hi < lo or any(c.actual[i]['unicode'].strip() and i not in own for i in range(lo + 1, hi)):
        return dict(status='rejected', reason='other text is painted between the columns')
    last, first = groups[blocks[a][-1]], groups[blocks[b][0]]
    if tagged:
        pa, pb = parent_of(last), parent_of(first)
        if pa is not None and pa == pb:
            return dict(status='determined', evidence='one tagged paragraph spans the column break', split=True)
        if pa is not None and pb is not None and _next_sibling(c.reader, pa, pb):
            return dict(status='determined', evidence='consecutive tagged paragraphs under the same parent', split=False)
        return dict(status='rejected', reason='the tagged structure does not connect the columns')
    tail, head = last[-1], first[0]
    text = ''.join(c.actual[i]['unicode'] for i in tail['glyph_ids']).rstrip()
    full = tail.get('right', tail['pen_end']) >= A['right'] - .5 * A['size']
    if full and text and text[-1] not in SENTENCE_END:
        return dict(status='determined', evidence='paragraph split: full last line without a sentence end', split=True)
    return dict(status='candidate', split=False,
                evidence='column order, style, measure and paint order match; the break may be a paragraph boundary')


def find_flows(c, groups, parent_of, tagged, obstacles, approved=()):
    """Flows of linked column blocks. ``approved`` names candidate links the user accepted.

    Returns (flows, report, blocks, info). A flow is dict(blocks, info, links).
    """
    blocks = blocks_of(groups)
    info = [describe(c, groups, b) for b in blocks]
    links, report = {}, []
    for a in range(len(blocks)):
        if info[a]['rows'] < 2:
            continue
        right = sorted((b for b in range(len(blocks)) if b != a and info[b]['left'] >= info[a]['right'] - .5
                        and info[b]['left'] - info[a]['right'] <= 5 * info[a]['size']
                        and info[b]['top'] <= info[a]['bottom'] and info[b]['ink_bottom'] >= info[a]['top'] - 2 * info[a]['size']),
                       key=lambda b: (info[b]['left'], info[b]['top']))
        if not right:
            continue
        b = right[0]
        result = link(c, groups, blocks, info, a, b, parent_of, tagged, obstacles)
        ident = f'column {a}->{b}'
        result.update(id=ident, from_x=[round(info[a]['left'], 1), round(info[a]['right'], 1)],
                      to_x=[round(info[b]['left'], 1), round(info[b]['right'], 1)],
                      next_text=''.join(c.actual[i]['unicode'] for i in info[b]['ids'][:16]))
        if result['status'] == 'candidate' and ident in approved:
            result.update(status='approved', evidence=result['evidence'] + '; explicitly approved')
        report.append(result)
        if result['status'] in ('determined', 'approved'):
            links[a] = (b, result)
    flows = []
    targets = {b for b, _ in links.values()}
    for a in range(len(blocks)):
        if a in targets or a not in links:
            continue
        chain, evidence = [a], []
        while chain[-1] in links and links[chain[-1]][0] not in chain:
            b, result = links[chain[-1]]
            chain.append(b)
            evidence.append(result)
        flows.append(dict(blocks=[blocks[k] for k in chain], info=[info[k] for k in chain], links=evidence))
    return flows, report, blocks, info


def regions(infos, obstacles, page_height):
    """One region per column; the shared bottom is the deepest original line, never grown."""
    bottom = max(i['bottom'] for i in infos)
    result = []
    for i in infos:
        limit = min(bottom, page_height - i['size'])
        for o in obstacles:
            x0, y0, x1, y1 = o['bounds']
            if o['kind'] != 'container' and x0 < i['right'] and x1 > i['left'] and y0 > i['ink_bottom'] + .1:
                limit = min(limit, y0 - DESCENT * i['size'])
        result.append(dict(left=round(i['left'], 3), right=round(i['right'], 3), top=round(i['top'], 3), bottom=round(limit, 3)))
    return result


def region_of(regions_, row):
    """Index of the region whose measure contains the row start."""
    hits = [k for k, r in enumerate(regions_) if r['left'] - 2 * row['size'] <= row['x'] < r['right']]
    if len(hits) != 1:
        raise Refused('a line of the flow lies outside its columns')
    return hits[0]


def place(tokens, text, flow, *, align='start'):
    """Re-break the edited paragraph into the flow and move every following line as a unit.

    ``flow`` holds regions, pitch, gap, the target's first row and the rows of
    the following paragraphs. Returns (lines of the edited paragraph, moves of
    following paragraphs as lists of (row, dx, dy, region)).
    """
    R, pitch, gap = flow['regions'], flow['pitch'], flow['gap']
    breaks = set(line_break_boundaries(text)) | {len(tokens)}
    first = flow['first_row']
    region, y = region_of(R, first), first['baseline']
    indent = first['x'] - R[region]['left']
    full = 'capacity: the column flow is full; the approved columns on this page cannot hold the text (no font shrinking, no region growth)'

    def advance(step):
        nonlocal region, y
        y += step
        if y > R[region]['bottom'] + .01:
            region += 1
            if region >= len(R):
                raise Refused(full)
            y = R[region]['top']

    spans, places, start = [], [], 0
    while start < len(tokens):
        if spans:
            advance(pitch)
        x = R[region]['left'] + (indent if not spans else 0)
        ends = fit(tokens, text, breaks, start, R[region]['right'] - x)
        if not ends:
            raise Refused('capacity: no legal line break inside the column width at the original font size')
        spans.append((start, max(ends)))
        places.append((region, y, x))
        start = max(ends)
    if len(spans) > 1 and places[-1][0] == places[-2][0]:
        spans = balance_tail(tokens, text, breaks, spans, R[places[-1][0]]['right'] - R[places[-1][0]]['left'])
    lines = []
    for n, ((a, b), (r, baseline, x)) in enumerate(zip(spans, places)):
        width = R[r]['right'] - x
        extra = _justification(tokens[a:b], width) if align == 'justify' and n < len(spans) - 1 else _squeezed(tokens[a:b], width)
        placed = []
        for k, token in enumerate(tokens[a:b]):
            placed.append(dict(token, x=x, y=baseline))
            x += token['advance'] + extra.get(k, 0)
        lines.append(dict(text=text[a:b], baseline=baseline, tokens=placed, region=r, justified=bool(extra)))
    moves = []
    for paragraph in flow['followers']:
        rows = []
        for n, row in enumerate(paragraph):
            old = region_of(R, row)
            advance(pitch if n else gap)
            dx, dy = R[region]['left'] - R[old]['left'], y - row['baseline']
            if abs(dy) < .01 and old == region:
                dx = dy = 0.
                y = row['baseline']
            rows.append((row, dx, dy, region))
        moves.append(rows)
    return lines, moves
