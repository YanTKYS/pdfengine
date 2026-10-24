"""Selection-driven observation and conservative horizontal-column inference.

No evaluation geometry enters this module. Occupied right edges are evidence,
not an assertion that a PDF stores an author's text frame.
"""
import unicodedata
from statistics import median
import pymupdf
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import _Analysis
from . import auto_conditions as conditions
from . import auto_flow as flowing
from .engine import Paragraph, Refused, _check_inbound
from . import form_write
from . import semantics
from . import text_space

LATIN_END = ',.;:!?)]\'"%'
LATIN_START = '(["\''


def row_text(c, row):
    return ''.join(c.actual[i]['unicode'] for i in row['glyph_ids'])


def group_text(c, group):
    """Logical paragraph text of visual rows, with implicit Latin line-join spaces.

    A line break between two Latin words is a word space that has no glyph.
    A line-final hyphen between Latin letters may be soft or hard; it is kept
    as drawn and reported, never removed or joined by guesswork.
    """
    text, joins, hyphens = '', [], []
    for n, row in enumerate(group):
        line = row_text(c, row)
        if n and text and line:
            prev, first = text[-1], line[0]
            if (prev == '-' and len(text) > 1 and text[-2].isascii() and text[-2].isalpha()
                    and first.isascii() and first.isalpha()):
                hyphens.append(group[n - 1]['glyph_ids'][-1])
            elif (prev.isascii() and first.isascii() and (prev.isalnum() or prev in LATIN_END)
                  and (first.isalnum() or first in LATIN_START)):
                joins.append(len(text))
                text += ' '
        text += line
    return text, joins, hyphens


GUTTER = 1.8  # em: an ink gap this wide on one baseline separates columns
NARROW_GUTTER = .9  # em: separates columns when the paint order also jumps


def split_gutters(c, row):
    """Pieces of an analysis row separated by a column gutter.

    Rows of side-by-side columns can share a baseline and be merged by the
    page analysis. Justified word spaces stay far below the gutter width; a
    narrower gap (an ideographic space is one em) separates columns only when
    the right glyph is not the next glyph painted after the left one.
    """
    order = sorted(row['glyph_ids'], key=lambda i: c.actual[i]['origin'][0])
    inked = [i for i in order if c.actual[i]['unicode'].strip()]

    def jump(a, b):
        return b < a or any(c.actual[i]['unicode'].strip() for i in range(a + 1, b))

    def gap(a, b):
        return c.actual[b]['bbox'][0] - c.actual[a]['bbox'][2]
    cuts = [(c.actual[a]['bbox'][2] + c.actual[b]['bbox'][0]) / 2 for a, b in zip(inked, inked[1:])
            if gap(a, b) > GUTTER * row['size'] or (gap(a, b) > NARROW_GUTTER * row['size'] and jump(a, b))]
    if not cuts:
        return [row]
    pieces = [[] for _ in range(len(cuts) + 1)]
    for i in order:
        pieces[sum(c.actual[i]['origin'][0] > x for x in cuts)].append(i)
    result = []
    for n, ids in enumerate(p for p in pieces if p):
        boxes = [c.actual[i]['bbox'] for i in ids]
        visible = [i for i in ids if c.actual[i]['unicode'].strip()] or ids
        last = n == len(cuts)
        result.append(dict(row, id=row['id'] + (f'.{n}' if n else ''), text=''.join(c.actual[i]['unicode'] for i in ids),
                           glyph_ids=sorted(ids), x=c.actual[ids[0]]['origin'][0], starts=[c.actual[i]['origin'][0] for i in ids],
                           right=row['right'] if last else c.actual[visible[-1]]['bbox'][2],
                           pen_end=row['pen_end'] if last else c.actual[ids[-1]]['bbox'][2],
                           bbox=[min(b[0] for b in boxes), row['bbox'][1], max(b[2] for b in boxes), row['bbox'][3]]))
    return result


def split_blocks(c, row, block):
    """Pieces of a row whose glyphs belong to different structure block elements (table cells side by side).

    ``block`` maps glyph IDs to their block element. A row is split only when
    each element's glyphs are contiguous along the line; blank or unmarked
    glyphs stay with the piece before them.
    """
    order = sorted(row['glyph_ids'], key=lambda i: c.actual[i]['origin'][0])
    owners = [block.get(i) for i in order]
    named = [o for o in owners if o is not None]
    runs = [o for k, o in enumerate(named) if not k or o != named[k - 1]]
    if len(runs) < 2 or len(runs) != len(set(runs)):
        return [row]
    pieces, current = [], None
    for i, o in zip(order, owners):
        if o is not None and o != current:
            pieces.append([]);current = o
        if not pieces:
            pieces.append([])
        pieces[-1].append(i)
    result = []
    for n, ids in enumerate(pieces):
        boxes = [c.actual[i]['bbox'] for i in ids]
        visible = [i for i in ids if c.actual[i]['unicode'].strip()] or ids
        last = n == len(pieces) - 1
        result.append(dict(row, id=row['id'] + f'.b{n}', text=''.join(c.actual[i]['unicode'] for i in ids),
                           glyph_ids=sorted(ids), x=c.actual[ids[0]]['origin'][0], starts=[c.actual[i]['origin'][0] for i in ids],
                           right=row['right'] if last else c.actual[visible[-1]]['bbox'][2],
                           pen_end=row['pen_end'] if last else c.actual[ids[-1]]['bbox'][2],
                           bbox=[min(b[0] for b in boxes), row['bbox'][1], max(b[2] for b in boxes), row['bbox'][3]]))
    return result


def merge_offset_rows(c, rows, block):
    """Rows of one visual line drawn on slightly different baselines (a marker or a smaller
    glyph 1 pt lower) joined into one row; each glyph keeps its own baseline offset.

    Two rows join when their baselines differ (by more than 0.05 pt, at most 0.35 em of the
    smaller size), they touch without overlapping (gap from -0.05 em of rounding to 0.5 em),
    and they belong to the same structure element (or none). Rows on one baseline were
    separated by an earlier stage (column gutters, structure elements) and stay apart.
    """
    out = []
    owners = lambda row: {block.get(i) for i in row['glyph_ids']}

    def beside(m, r):
        em = min(m['size'], r['size'])
        gap = max(r['bbox'][0] - m['bbox'][2], m['bbox'][0] - r['bbox'][2])
        return -.05 * em <= gap <= .5 * max(m['size'], r['size'])

    for r in rows:
        mate = next((m for m in out if .05 < abs(m['baseline'] - r['baseline']) <= .35 * min(m['size'], r['size'])
                     and beside(m, r) and owners(m) == owners(r)), None)
        if mate is None:
            out.append(r)
            continue
        main = mate if len(mate['glyph_ids']) >= len(r['glyph_ids']) else r
        ids = sorted(mate['glyph_ids'] + r['glyph_ids'], key=lambda i: c.actual[i]['origin'][0])
        merged = dict(main, glyph_ids=ids, text=''.join(c.actual[i]['unicode'] for i in ids),
                      x=min(mate['x'], r['x']), pen_end=max(mate['pen_end'], r['pen_end']),
                      starts=[c.actual[i]['origin'][0] for i in ids],
                      bbox=[min(mate['bbox'][0], r['bbox'][0]), min(mate['bbox'][1], r['bbox'][1]),
                            max(mate['bbox'][2], r['bbox'][2]), max(mate['bbox'][3], r['bbox'][3])])
        for key in ('top', 'bottom', 'right'):
            if key in mate and key in r:
                merged[key] = (min if key == 'top' else max)(mate[key], r[key])
        out[out.index(mate)] = merged
    return out


def clean_rows(c, rows):
    """Analysis rows split at column gutters, with independent blank-only paints at a row start removed."""
    result = []
    for r in (piece for row in rows for piece in split_gutters(c, row)):
        r = dict(r)
        ids = r['glyph_ids']
        inked = [c.actual[i]['origin'][0] for i in ids if c.actual[i]['unicode'].strip()]
        if not inked:
            continue
        first = min(inked)
        r['glyph_ids'] = [i for i in ids if c.actual[i]['unicode'].strip() or c.actual[i]['origin'][0] > first + .001]
        # The row starts at its first kept glyph, not at a removed leading blank.
        r['x'] = min(c.actual[i]['origin'][0] for i in r['glyph_ids'])
        result.append(r)
    return result


def _overlap(a, b):
    return a['x'] < b['pen_end'] and b['x'] < a['pen_end']


def _first_word_fits(c, prev, row, right):
    """Whether the next row's first Latin word would have fit on the previous row.

    If it would, the previous row ended its paragraph deliberately. Only
    applied to Latin words: CJK lines may end early for line-breaking rules.
    """
    word=[]
    for i in row['glyph_ids']:
        ch=c.actual[i]['unicode']
        if ch.isspace():break
        word.append(i)
    if not word or not all(c.actual[i]['unicode'].isascii() and c.actual[i]['unicode'].isalnum() for i in word):return False
    width=c.actual[word[-1]]['bbox'][2]-row['x']
    return prev['pen_end']+.25*prev['size']+width<right-.5*prev['size']


def _marker(c, row):
    """The row's first glyph when it is a list marker (a symbol such as ● • ■ ▶ ・ ※)."""
    first = next((c.actual[i]['unicode'] for i in row['glyph_ids'] if c.actual[i]['unicode'].strip()), '')
    return first if first and (unicodedata.category(first) == 'So' or first in '•・‣⁃◦※') else None


def source_rows(c, rows):
    """The drawn lines of a paragraph for local layout (auto_local).

    Each line: its glyph IDs, baseline, start x (first glyph origin), pen end,
    and whether its break is the author's: the next line's first unit (a Latin
    word, or one CJK character) would have fit after it within the
    paragraph's own widest line, so a writer that wraps did not break there.
    """
    own_right = max(r['pen_end'] for r in rows)
    out = []
    for k, r in enumerate(rows):
        hard = False
        if k + 1 < len(rows):
            nxt = rows[k + 1]['glyph_ids']
            text = [c.actual[i]['unicode'] for i in nxt]
            first = 0
            if text and text[0].isascii() and text[0].isalnum():
                while first + 1 < len(text) and text[first + 1].strip() and text[first + 1].isascii():
                    first += 1
            unit = c.actual[nxt[first]]['bbox'][2] - c.actual[nxt[0]]['origin'][0] if nxt else 0
            space = .25 * r['size'] if text and text[0].isascii() else 0
            hard = own_right - r['pen_end'] > unit + space + .05
        out.append(dict(ids=list(r['glyph_ids']), baseline=r['baseline'], x=c.actual[r['glyph_ids'][0]]['origin'][0],
                        pen_end=r['pen_end'], size=r['size'], hard=hard))
    return out


def right_to_left(text):
    """Whether text has right-to-left letters (Hebrew, Arabic …).

    Glyphs are laid out left to right in drawing order; right-to-left
    scripts need bidirectional reordering and line breaking.
    """
    return any(unicodedata.bidirectional(ch) in ('R', 'AL') for ch in text)


def _numeric(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


def qualify(c, marks, ids, rows):
    chosen=set(ids)
    owned={i for e in c.events for ch in e.chars for i in ch.source_orders}
    if not chosen<=owned:
        reasons=sorted({e.error for e in c.events if e.error}) or ['trace glyph without a content-stream character']
        raise Refused('paragraph glyphs lack content-stream provenance: '+'; '.join(reasons))
    # Marked content and structure: kept containers, re-emitted sequences and
    # fixed inline objects (semantics.analyse); anything unprovable is refused.
    scopes,parent,containers,fixed=semantics.analyse(c,marks,ids)
    # A fixed inline object is neither removed nor redrawn: only the text
    # drawn around it must qualify.
    drawn=[i for i in ids if i not in set(fixed)];chosen=set(drawn)
    events=[e for e in c.events if any(chosen.intersection(ch.source_orders) for ch in e.chars)]
    # Every glyph is painted once. Its paint order may differ from reading
    # order (a line-start marker painted after its line): the new drawing is
    # written in reading order, and marked-content order is checked by the writer.
    if sorted(i for e in events for ch in e.chars for i in ch.source_orders if i in chosen)!=sorted(drawn):
        raise Refused('paragraph glyphs are not each painted once')
    for e in events:
        # Text in a Form XObject is rewritten in a per-invocation clone (form_write).
        # Only opaque, normal-blend text without a soft mask is reproduced.
        if e.state.tr!=0 or not form_write.drawn_neutral(e.state):
            raise Refused('non-fill or transparent selected text unsupported')
        # Scaled text matrices and CTMs are page-space sizes (text_space);
        # rotation, shear and uncancelled reflection stay refused.
        text_space.scales(e)
    sizes={i:text_space.effective(e)['size'] for e in events for ch in e.chars for i in ch.source_orders}
    for e in events:
        if e.state.ts!=0:raise Refused('nonzero text rise requires a baseline-shift adapter')
        # A named fill colour space (cs + sc/scn) is re-selected when drawing;
        # patterns (non-numeric scn operands) stay unsupported.
        named=e.state.fill[0] in ('sc','scn') and e.state.fill_space and all(_numeric(v) for v in e.state.fill[1])
        if (e.state.fill[0] not in ('g','rg','k') and not named) or e.state.tz<=0:
            raise Refused('unsupported source color or horizontal scale')
        if any(len(ch.source_orders)!=1 or len(ch.text)!=1 for ch in e.chars if chosen.intersection(ch.source_orders)):
            raise Refused('one-code/one-Unicode/one-occurrence witness required')
    if fixed:
        # Its size is the drawn size of its glyphs (kept as they are).
        sizes.update({i:text_space.effective(e)['size'] for e in c.events for ch in e.chars for i in ch.source_orders if i in set(fixed)})
    text,joins,hyphens=group_text(c,rows)
    if right_to_left(text):
        raise Refused('right-to-left text requires a bidirectional layout adapter')
    return Paragraph(text,ids,[sizes[i] for i in ids],[r['baseline'] for r in rows],scopes,parent,tuple(joins),tuple(hyphens),containers,fixed)


def _column_bottom(selected, others, blocks, infos, report):
    """Shared bottom of the columns beside the selected paragraph's block.

    Side-by-side columns of the same body style (font, size, colour) form one
    column grid and end on one line: a paragraph that is not in a linked flow
    may not grow below it into the page margin. A differently styled column
    (e.g. white text on an image panel) is an independent frame and keeps the
    single-column capacity (next protected paint or page edge).
    """
    index = next((k for k, g in enumerate(others) if g is selected), None)
    block = next((b for b, members in enumerate(blocks) if index in members), None)
    if block is None:
        return None, dict(neighbours=0, links=[])
    me = infos[block]
    beside = [i for b, i in enumerate(infos) if b != block and i['style'] == me['style']
              and i['top'] <= me['bottom'] and i['bottom'] >= me['top'] and (i['left'] >= me['right'] - .5 or i['right'] <= me['left'] + .5)]
    links = [r for r in report if r['id'].startswith(f'column {block}->')]
    if not beside:
        return None, dict(neighbours=0, links=links)
    return max([me['bottom']] + [i['bottom'] for i in beside]), dict(neighbours=len(beside), links=links)


def _flow_detect(c, marks, page, flow, selected, start, paint, report, records, record_status, flow_stale, match_count, stale_reason=None):
    """Observation for an edit inside a linked column flow (see auto_flow)."""
    paragraphs = flow['paragraphs']
    t = next(k for k, g in enumerate(paragraphs) if g is selected)
    target = qualify(c, marks, [i for r in selected for i in r['glyph_ids']], selected)
    followers, unmovable = [], None
    for g in paragraphs[t + 1:]:
        try:
            followers.append(qualify(c, marks, [i for r in g for i in r['glyph_ids']], g))
        except Refused as exc:
            unmovable = str(exc)
            break
    rows = [r for g in paragraphs for r in g]
    ids = {i for r in rows for i in r['glyph_ids']}
    size = median(r['size'] for r in rows)
    obstacles = [dict(o) for o in paint]
    containers = []
    for o in obstacles:
        if o['kind'] in ('filled-vector', 'image', 'vector') and all(pymupdf.Rect(o['bounds']).contains(pymupdf.Rect(r['bbox'])) for r in rows):
            o['kind'] = 'container'
            containers.append(o['bounds'])
    obstacles.extend(dict(kind='foreign-text', bounds=list(g['bbox'])) for i, g in enumerate(c.actual) if i not in ids and g['unicode'].strip())
    regions = [dict(r) for r in flow['regions']]
    first = flowing.region_of(regions, selected[0])
    # Current protected paint below each column's text bounds its region
    # (a stored region is re-checked; it never grows).
    for k, r in enumerate(regions):
        inked = [x['bottom'] for x in rows if flowing.region_of(regions, x) == k]
        floor = max(inked) if inked else r['top']
        for o in obstacles:
            x0, y0, x1, y1 = o['bounds']
            if o['kind'] != 'container' and x0 < r['right'] and x1 > r['left'] and y0 > floor + .1:
                r['bottom'] = round(min(r['bottom'], y0 - flowing.DESCENT * size), 3)
    top = min(min(r['top'] for r in selected) - .5, selected[0]['baseline'] - size - .5)
    frames = [[r['left'] - .5, top if k == first else r['top'] - size - .5, r['right'] + .5, r['bottom'] + flowing.DESCENT * size]
              for k, r in enumerate(regions) if k >= first]
    frame = [min(f[0] for f in frames), min(f[1] for f in frames), max(f[2] for f in frames), max(f[3] for f in frames)]
    if flow['stored']:
        align = flow['align']
        state = dict(status='applied', applied=True, target=flow['record'], stale=flow_stale, records=records)
    else:
        def right_of(row):
            return regions[flowing.region_of(regions, row)]['right']
        inner = [r for g in paragraphs for r in g[:-1]]
        flush = bool(inner) and all(abs(r.get('right', r['pen_end']) - right_of(r)) < .75 for r in inner)
        text = ''.join(group_text(c, g)[0] for g in paragraphs)
        cjk = any(ord(ch) >= 0x2E80 for ch in text)
        align = 'justify-cjk' if flush and cjk else 'justify' if flush and ' ' in text else 'start'
        status = 'stale: ' + stale_reason if flow_stale else record_status if not records else 'no stored flow for this paragraph'
        state = dict(status=status, applied=False, target=None, stale=flow_stale, records=records)
    links = flow['links']
    gap_basis = 'observed: median gap between paragraphs of the flow' if flow.get('gap_observed', True) else 'unobserved: line pitch'
    provenance = dict(regions='approved: stored flow' if flow['stored'] else 'observed: column blocks of the flow (left = continuation x, right = maximum ink end, bottom = deepest original line)',
                      pitch='approved: stored flow' if flow['stored'] else 'observed: median within-paragraph line pitch', gap='approved: stored flow' if flow['stored'] else gap_basis,
                      links='approved: stored flow' if flow['stored'] else '; '.join(f"{l['id']}: {l['status']} ({l['evidence']})" for l in links))
    values = dict(regions=regions, pitch=flow['pitch'], gap=flow['gap'], align=align)
    origin = records[flow['record']]['origin'] if flow['stored'] else dict(values=values, provenance=provenance)
    evidence = dict(observed=dict(lines=[{k: r[k] for k in ('id', 'text', 'glyph_ids', 'x', 'pen_end', 'baseline', 'size', 'bbox')} for r in rows],
                                  semantic_parents=[p.parent for p in [target] + followers], obstacles=obstacles),
                    inferred=dict(frame=frame, frames=frames, regions=regions, pitch=flow['pitch'], gap=flow['gap'], alignment=align,
                                  baseline=selected[0]['baseline'], target_paragraph=t, flow_paragraphs=len(paragraphs),
                                  baseline_policy='the edited paragraph is re-broken; every following line keeps its glyphs and line breaks and moves as a unit'),
                    column_flow=dict(mode='flow', stored=flow['stored'], links=links, report=report, regions=regions),
                    conditions=dict(status=state['status'], applied=state['applied'], provenance=provenance, origin=origin,
                                    approval_count=records[flow['record']]['approval']['count'] if flow['stored'] else 0,
                                    stale_records=len(flow_stale), containers=containers, implicit_line_join_spaces=len(target.joins), line_end_hyphens=len(target.hyphens)),
                    confidence=dict(paragraph='approved: stored flow matched every drawn line' if flow['stored'] else 'high: ordered P children' if target.parent else 'medium: indentation, spacing and style continuity',
                                    continuation='approved: stored flow' if flow['stored'] else 'high: ' + '; '.join(l['evidence'] for l in links)),
                    selection_match_count=match_count,
                    warnings=['Column regions are inferred capacities, not stored author frames.',
                              'Following lines of the flow may move between columns as units; protected content never moves.'],
                    manual_geometry_inputs=0)
    if any(l['status'] == 'approved' for l in links):
        evidence['warnings'].append('A candidate continuation was used because it was explicitly approved.')
    if state['status'].startswith('stale'):
        evidence['warnings'].append('Stored approved conditions were not applied: ' + state['status'])
    if unmovable:
        evidence['warnings'].append('A following paragraph of the flow cannot be moved: ' + unmovable)
    if target.hyphens:
        evidence['warnings'].append('Line-final hyphens are kept as drawn; reflow that moves them is refused.')
    if match_count > 1:
        evidence['warnings'].append('Multiple string matches exist; approve the displayed occurrence and paragraph explicitly.')
    spec = dict(page=page, mode='flow', frame=frame, frames=frames, align=align, baseline=selected[0]['baseline'],
                flow=dict(regions=regions, pitch=flow['pitch'], gap=flow['gap'], first_row=selected[0], followers=paragraphs[t + 1:],
                          movable=len(followers), unmovable=unmovable, paragraphs=paragraphs, target=t, links=links),
                stored=state, values=values, provenance=provenance, origin=origin)
    return c, [target] + followers, spec, start, evidence


def page_rows(c, marks, analysis):
    """Drawn rows of a page and the block structure element of each glyph.

    Detection and the stored approved records (auto_writer) use these same
    rows, so a record written after an edit is found again on reopen.
    """
    # Preserve meaningful spaces while dropping independent blank-only paints.
    rows=clean_rows(c,[dict(l) for l in analysis.lines if l['text'].strip()])
    # The structure element that a glyph's text belongs to: a block element
    # (paragraph, heading, table cell, list body …) or the block parent of an
    # inline leaf. A drawn row that joins several of them is split.
    parent_by_id={}
    for s in marks['scopes']:
        a=s['association']
        if a['status']=='tree_backed' and not s['invocation']:
            owner=c.reader.get_object(a['owner_reference']['xref']); pr=semantics.block_of(c.reader,owner)
            if pr is not None:
                for e in c.events:
                    if e.stream_xref==s['stream_xref'] and not e.invocation and s['content_byte_range'][0]<=e.operator.start<s['content_byte_range'][1]:
                        for ch in e.chars:
                            for i in ch.source_orders:parent_by_id[i]=pr.indirect_reference.idnum
    rows=merge_offset_rows(c,[piece for r in rows for piece in split_blocks(c,r,parent_by_id)],parent_by_id)
    return rows,parent_by_id


def observe(c,marks,analysis):
    """Analysis rows, their semantic /P parents and paragraph groups of one page."""
    rows,parent_by_id=page_rows(c,marks,analysis)
    # Paint style of each inked glyph: a row that shares no font and fill with
    # the previous row (a white label on a coloured box) does not continue it.
    paint={i:(e.state.font.name if e.state.font else None,e.state.fill) for e in c.events for ch in e.chars for i in ch.source_orders if ch.text.strip()}
    def style(row):return {paint[i] for i in row['glyph_ids'] if i in paint}
    groups=[]
    for r in rows:
        ids=r['glyph_ids']; parents={parent_by_id.get(i) for i in ids}; parent=next(iter(parents)) if len(parents)==1 else None
        # The predecessor is the latest row of the same column (horizontal
        # overlap), so columns sharing baselines are not interleaved.
        candidate=next((g for g in reversed(groups) if _overlap(g[-1],r)),None)
        new=True
        if candidate is not None:
            prev=candidate[-1]; prevparents={parent_by_id.get(i) for i in prev['glyph_ids']}
            gap=r['baseline']-prev['baseline']; size=r['size']
            # One structure element continues, unless the row is placed apart
            # in it: another size, and not starting at a continuation x (a
            # note marker under a title in one table cell).
            if parent is not None:new=prevparents!={parent} or (abs(size-prev['size'])>=.1 and not -1.6*size<=r['x']-prev['x']<.2*size)
            else:
                same_size=abs(size-prev['size'])<.1
                # Continuations start at the same or a slightly smaller x;
                # an indented new line or paragraph gap starts a paragraph.
                new=not (same_size and 0<gap<2.25*size and -1.6*size<=r['x']-prev['x']<.2*size)
                gaps=[b['baseline']-a['baseline'] for a,b in zip(candidate,candidate[1:])]
                # A gap clearly wider than the group's established line pitch,
                # or a short line whose successor's first word would have fit,
                # ends the paragraph.
                if not new and gaps and gap>1.3*median(gaps):new=True
                if not new and _first_word_fits(c,prev,r,max(x['pen_end'] for x in candidate+[r])):new=True
                if not new and style(prev) and style(r) and not style(prev)&style(r):new=True
            # A row opening with the marker of the paragraph's first row, at its
            # x, is the next list item.
            if not new and _marker(c,r) and _marker(c,r)==_marker(c,candidate[0]) and abs(r['x']-candidate[0]['x'])<.2*r['size']:new=True
        if new:groups.append([r])
        else:candidate.append(r)
    return rows,parent_by_id,groups


def recognize(source,page,continuation=()):
    """Column blocks and continuation evidence of a page, independent of a selection.

    Used to list candidate links for explicit approval and to evaluate
    recognition on pages whose text cannot be edited.
    """
    c=ContentPage(source,page)
    try:
        marks=observe_marked_content(source,page);analysis=_Analysis(source,page)
        rows,parent_by_id,groups=observe(c,marks,analysis)
        paint=[dict(o) for o in analysis.obstacles]
        def parent_of(group):
            found={parent_by_id.get(i) for r in group for i in r['glyph_ids'] if c.actual[i]['unicode'].strip()}
            return next(iter(found)) if len(found)==1 else None
        tagged=c.reader.trailer['/Root'].get('/StructTreeRoot') is not None
        flows,report,blocks,infos=flowing.find_flows(c,groups,parent_of,tagged,paint,approved=tuple(continuation))
        columns=[dict(id=k,x=[round(i['left'],1),round(i['right'],1)],top=round(i['top'],2),bottom=round(i['bottom'],2),lines=i['rows'],
                      first_text=''.join(c.actual[g]['unicode'] for g in i['ids'][:16])) for k,i in enumerate(infos)]
        return dict(page=page,tagged=tagged,interpretation_errors=[str(e) for e in c.errors],columns=columns,links=report,
                    flows=[[columns[blocks.index(b)]['id'] for b in f['blocks']] for f in flows])
    finally:c.close()


def detect(source,page,selection,occurrence=0,stored=True,continuation=()):
    """``continuation`` lists candidate column links (ids from the proposal) the user approved."""
    c=ContentPage(source,page)
    try:
        if c.errors:raise Refused('incomplete source interpretation: '+str(c.errors))
        # Any MediaBox origin is mapped through the page transformation; a
        # rotated page is not supported.
        if c.page.rotation:raise Refused('rotated page')
        marks=observe_marked_content(source,page)
        if not marks['complete']:raise Refused('incomplete marked-content observation')
        analysis=_Analysis(source,page)
        rows,parent_by_id,groups=observe(c,marks,analysis)
        # Approved conditions stored by an earlier save name their paragraph by
        # its exact drawn lines. A verified record defines the paragraph rows.
        records,record_status=conditions.read(c,page) if stored else ([],'not consulted')
        paint=[dict(o) for o in analysis.obstacles]
        for link in c.page.get_links():paint.append(dict(kind='annotation',bounds=list(link['from'])))
        for annot in (c.page.annots() or []):paint.append(dict(kind='annotation',bounds=list(annot.rect)))
        # A stored approved column flow names every line of the flow and the
        # paragraph each belongs to; it survives columns emptied by an edit.
        stored_flow,flow_stale,stale_reason=None,[],None
        for n,record in enumerate(records):
            if record.get('kind')!='flow' or stored_flow is not None:continue
            found=conditions.match(c,record,rows)
            if found is None:
                if conditions.overlaps(record,rows):flow_stale.append(n);stale_reason='the drawn flow differs from the stored approved flow'
                continue
            mine={id(r) for r in found};flow_ids={i for r in found for i in r['glyph_ids']}
            intruders=conditions.flow_intrusions(c,record,flow_ids,paint)
            if intruders:
                flow_stale.append(n);stale_reason='protected paint entered an approved column region ('+', '.join(sorted(set(intruders)))+')';continue
            paragraphs={}
            for line,r in zip(record['lines'],found):paragraphs.setdefault(line['paragraph'],[]).append(r)
            position=min(k for k,g in enumerate(groups) if any(id(r) in mine for r in g))
            groups=[[r for r in g if id(r) not in mine] for g in groups]
            stored_groups=[paragraphs[k] for k in sorted(paragraphs)]
            groups=groups[:position]+stored_groups+groups[position:];groups=[g for g in groups if g]
            stored_flow=dict(record=n,paragraphs=stored_groups,regions=record['regions'],pitch=record['conditions']['pitch'],gap=record['conditions']['gap'],
                             align=record['conditions']['align'],links=record['links'],stored=True)
        owner={}
        for n,record in enumerate(records):
            if record.get('kind')=='flow':continue
            found=conditions.match(c,record,rows)
            if found is None:continue
            ids={id(r) for r in found};position=min(k for k,g in enumerate(groups) if any(id(r) in ids for r in g))
            groups=[[r for r in g if id(r) not in ids] for g in groups]
            groups.insert(position,sorted(found,key=lambda r:r['baseline']));groups=[g for g in groups if g]
            owner[n]=ids
        def record_of(group):return next((n for n,ids in owner.items() if {id(r) for r in group}==ids),None)
        def parent_of(group):
            found={parent_by_id.get(i) for r in group for i in r['glyph_ids'] if c.actual[i]['unicode'].strip()}
            return next(iter(found)) if len(found)==1 else None
        tagged=c.reader.trailer['/Root'].get('/StructTreeRoot') is not None
        fixed={id(g) for g in (stored_flow['paragraphs'] if stored_flow else [])}
        others=[g for g in groups if id(g) not in fixed]
        inferred,report,blocks,infos=flowing.find_flows(c,others,parent_of,tagged,paint,approved=tuple(continuation))
        flows=[stored_flow] if stored_flow else []
        for flow in inferred:
            gaps=[i['gap'] for i in flow['info'] if i['gap']];pitches=[i['pitch'] for i in flow['info'] if i['pitch']]
            # A group whose first rows had no established pitch may have absorbed
            # the next paragraph; the flow's pitch separates them again.
            paragraphs=[]
            for k,block in enumerate(flow['blocks']):
                for j,gi in enumerate(block):
                    g=others[gi];parts=[[g[0]]]
                    for a,b in zip(g,g[1:]):
                        if b['baseline']-a['baseline']>1.3*median(pitches):parts.append([b])
                        else:parts[-1].append(b)
                    if len(parts)>1:
                        at=next(n for n,x in enumerate(groups) if x is g);groups[at:at+1]=parts
                    else:parts=[g]
                    for n,part in enumerate(parts):
                        # A paragraph split by the column break is one logical paragraph.
                        if k and not j and not n and flow['links'][k-1]['split']:
                            merged=paragraphs[-1]+part;groups[next(m for m,x in enumerate(groups) if x is paragraphs[-1])]=merged
                            groups=[x for x in groups if x is not part];paragraphs[-1]=merged
                        else:paragraphs.append(part)
            flows.append(dict(paragraphs=paragraphs,regions=flowing.regions(flow['info'],paint,c.page.rect.height),pitch=median(pitches),
                              gap=median(gaps) if gaps else median(pitches),gap_observed=bool(gaps),links=flow['links'],stored=False))
        texts=[group_text(c,g)[0] for g in groups]
        matches=[]
        for n,t in enumerate(texts):
            at=0
            while selection and (pos:=t.find(selection,at))>=0:matches.append((n,pos));at=pos+1
        if not isinstance(occurrence,int) or occurrence<0 or occurrence>=len(matches):raise Refused('selected string occurrence not found in one inferred paragraph')
        target,start=matches[occurrence]; selected=groups[target]; size=median(r['size'] for r in selected)
        flow=next((f for f in flows if any(g is selected for g in f['paragraphs'])),None)
        if flow is not None:
            return _flow_detect(c,marks,page,flow,selected,start,paint,report,records,record_status,flow_stale,len(matches),stale_reason)
        column_bottom,neighbours=_column_bottom(selected,others,blocks,infos,report)
        left=min(r['x'] for r in selected); first=selected[0]['x']
        # A single-line paragraph borrows the observed continuation edge from
        # nearby same-size body rows, never from a manifest. A paragraph of
        # several lines shows its own continuation edge.
        nearby=[r for r in rows if len(selected)==1 and abs(r['size']-size)<.1 and abs(r['baseline']-selected[0]['baseline'])<8*size and first-1.6*size<=r['x']<=first+.1 and _overlap(r,selected[0])]
        left=min([left]+[r['x'] for r in nearby]); followers=[];follower_records=[]
        previous=selected;column=dict(x=min(r['x'] for r in selected),pen_end=max(r['pen_end'] for r in selected))
        for group in groups[target+1:]:
            r=group[0]
            if not _overlap(r,column):continue
            gap=r['baseline']-previous[-1]['baseline']
            if abs(r['size']-size)>.1 or abs(r['x']-first)>.2*size or not 0<gap<2.8*size:break
            followers.append(group);follower_records.append(record_of(group));previous=group
        # The target must qualify. A follower that cannot be qualified (and every
        # later one) stays protected foreign content; it bounds the capacity.
        target_paragraph=qualify(c,marks,[i for r in selected for i in r['glyph_ids']],selected)
        follower_paragraphs=[];unmovable=None
        for group in followers:
            try:follower_paragraphs.append(qualify(c,marks,[i for r in group for i in r['glyph_ids']],group))
            except Refused as exc:unmovable=str(exc);break
        followers=followers[:len(follower_paragraphs)];follower_records=follower_records[:len(follower_paragraphs)]
        allgroups=[selected]+followers; allrows=[r for g in allgroups for r in g]
        deltas=[b['baseline']-a['baseline'] for g in allgroups for a,b in zip(g,g[1:])]
        leading=median(deltas) if deltas else min([b['baseline']-a['baseline'] for a,b in zip(allrows,allrows[1:])] or [size*1.5])
        right=max(r['pen_end'] for r in allrows)
        top=min(min(r['top'] for r in selected)-.5,selected[0]['baseline']-max(r['size'] for r in selected)-.5)
        ids={i for r in allrows for i in r['glyph_ids']}; last=max(r['bottom'] for r in allrows)
        obstacles=[dict(o) for o in paint]
        # Paint that already contains every original line of these paragraphs
        # (page background, a panel or an image behind the text) is the
        # paragraphs' container: new ink may overlap it but must stay inside it.
        containers=[]
        for o in obstacles:
            # A stroked outline around every line (a box border) is a frame of
            # the paragraph too: its interior is not painted.
            if o['kind'] in ('filled-vector','image','vector') and all(pymupdf.Rect(o['bounds']).contains(pymupdf.Rect(r['bbox'])) for r in allrows):
                o['kind']='container';containers.append(o['bounds'])
        obstacles.extend(dict(kind='foreign-text',bounds=list(g['bbox'])) for i,g in enumerate(c.actual) if i not in ids and g['unicode'].strip())
        basis='median within-paragraph observed gaps' if deltas else 'peer paragraph gap' if len(allrows)>1 else 'unobserved: 1.5 em default'
        provenance=dict(left='observed: minimum continuation x' if len(selected)>1 else 'inferred: nearby same-size continuation edge' if nearby else 'observed: first-line x',
                        right='inferred: maximum occupied pen extent',first_x='observed: first line x',line_height=basis,
                        bottom='inferred: next protected paint, annotation or page edge')
        state=dict(status=record_status if not records else 'no record for this paragraph',records=records,target=None,followers=follower_records,applied=False,stale=[])
        n=record_of(selected)
        if n is None:
            state['stale']=[k for k,record in enumerate(records) if conditions.overlaps(record,selected)]
            if state['stale']:state['status']='stale: the drawn paragraph differs from the stored approved record'
        else:
            record=records[n];state['target']=n
            hits=conditions.intrusions(record,selected,obstacles)
            if hits:state.update(status='stale: protected paint entered the approved width ('+', '.join(sorted(set(hits)))+')',stale=[n],target=None)
            else:
                approved=record['conditions'];left,right,leading=approved['left'],approved['right'],approved['line_height']
                basis='approved condition stored in the PDF (first approved as: '+record['origin']['provenance']['line_height']+')'
                provenance={k:'approved: stored record verified against the drawn paragraph' for k in ('left','right','line_height')}|dict(first_x='observed: first line x',bottom='min(stored approved bottom, current protected paint)')
                state.update(status='applied',applied=True)
        below=[o['bounds'][1] for o in obstacles if o['kind']!='container' and o['bounds'][1]>last+.1 and o['bounds'][0]<right and o['bounds'][2]>left]
        bottom=min(below+[c.page.rect.height])-1
        if state['applied']:bottom=min(bottom,records[state['target']]['conditions']['bottom'])
        for box in containers:bottom=min(bottom,box[3])
        # Existing paints intersecting a proposed frame remain protected; the
        # writer checks actual new ink, not just this envelope.
        frame=[left-.5,top,right+.5,bottom]
        # The paragraph's own ink is always replaceable: the frame contains every
        # original glyph box of the edited paragraphs (overhangs, tall marks).
        inked=[c.actual[i]['bbox'] for i in ids if c.actual[i]['unicode'].strip()]
        if inked:frame=[min(frame[0],min(b[0] for b in inked)),min(frame[1],min(b[1] for b in inked)),max(frame[2],max(b[2] for b in inked)),max(frame[3],max(b[3] for b in inked))]
        for box in containers:frame=[max(frame[0],box[0]),max(frame[1],box[1]),min(frame[2],box[2]),min(frame[3],box[3])]
        # Ink outside the clip the text is drawn under (a table cell, a text
        # box) would not show: rectangular clips bound the frame too.
        own={i for r in selected for i in r['glyph_ids']}-set(target_paragraph.fixed)
        for e in c.events:
            if not any(i in own for ch in e.chars for i in ch.source_orders):continue
            for clip in e.state.clip:
                box=form_write.clip_rect(clip,c.page.transformation_matrix)
                if box is not None:frame=[max(frame[0],box.x0),max(frame[1],box.y0),min(frame[2],box.x1),min(frame[3],box.y1)]
        paragraphs=[target_paragraph]+follower_paragraphs
        values=dict(left=left,right=right,first_x=first,line_height=leading,bottom=bottom)
        origin=records[state['target']]['origin'] if state['applied'] else dict(values=values,provenance=provenance)
        evidence=dict(observed=dict(lines=[{k:r[k] for k in ('id','text','glyph_ids','x','pen_end','baseline','size','bbox')} for r in allrows],semantic_parents=[p.parent for p in paragraphs],obstacles=obstacles),
            inferred=dict(frame=frame,left=left,right=right,first_x=first,baseline=selected[0]['baseline'],line_height=leading,leading_basis=basis,paragraph_gap=[g[0]['baseline']-h[-1]['baseline'] for h,g in zip(allgroups,allgroups[1:])],baseline_policy='existing target line baselines are observed and retained; added lines use the approved or inferred leading'),
            conditions=dict(status=state['status'],applied=state['applied'],provenance=provenance,origin=origin,
                            approval_count=records[state['target']]['approval']['count'] if state['applied'] else 0,
                            stale_records=len(state['stale']),containers=containers,implicit_line_join_spaces=len(paragraphs[0].joins),line_end_hyphens=len(paragraphs[0].hyphens)),
            confidence=dict(paragraph='approved: stored record matched every drawn line' if state['applied'] else 'high: ordered P children' if paragraphs[0].parent else 'medium: indentation, spacing and style continuity',
                            right='approved: stored condition' if state['applied'] else 'medium: maximum occupied pen extent; author frame absent',bottom='medium: next protected paint/annotation or page edge',top='medium: observed line boxes plus one-em ascender allowance; staged ink and raster checked'),
            selection_match_count=len(matches),warnings=['Occupied width is an inferred capacity, not a stored author frame.','Following same-column paragraphs may translate; protected content never moves.'],manual_geometry_inputs=0)
        if not deltas and not state['applied']:evidence['warnings'].append('No within-paragraph leading is observable; approve the reported inferred/default leading.')
        if state['status'].startswith('stale'):evidence['warnings'].append('Stored approved conditions were not applied: '+state['status'])
        if flow_stale:evidence['warnings'].append('A stored approved column flow was not applied: '+stale_reason)
        if record_status.startswith(('unreadable','page modified')) or 'malformed' in record_status:evidence['warnings'].append('Stored conditions: '+record_status)
        if unmovable:evidence['warnings'].append('Following paragraph stays fixed and bounds the capacity: '+unmovable)
        if paragraphs[0].hyphens:evidence['warnings'].append('Line-final hyphens are kept as drawn; reflow that moves them is refused.')
        if len(matches)>1:evidence['warnings'].append('Multiple string matches exist; approve the displayed occurrence and paragraph explicitly.')
        # Lines centred on the first line, starting well inside it, are a
        # centred paragraph: reflow would align them at the start.
        centre=lambda r:(r['x']+r['pen_end'])/2
        if len(selected)>1 and all(r['x']-selected[0]['x']>2*size and abs(centre(r)-centre(selected[0]))<size for r in selected[1:]):
            raise Refused('centred paragraph requires a centre-alignment adapter')
        # A space-separated (non-CJK) paragraph whose non-final lines all end at
        # the right edge is justified; reflow keeps that alignment. CJK text
        # keeps its observed per-character tracking instead.
        # The edge is the frame's right edge (approved, or the widest line of
        # the paragraph and its followers): a paragraph whose lines stop short
        # of a wider follower is not justified to it.
        flush=len(selected)>1 and all(abs(r['pen_end']-right)<.75 for r in selected[:-1])
        justified=flush and ' ' in paragraphs[0].text and all(ord(ch)<0x2E80 for ch in paragraphs[0].text)
        cjk=flush and not justified and any(ord(ch)>=0x2E80 for ch in paragraphs[0].text)
        align='justify' if justified else 'justify-cjk' if cjk else 'start'
        if state['applied']:align=records[state['target']]['conditions'].get('align',align)
        evidence['inferred']['alignment']=dict(justify='justify: non-final lines end at the right edge',start='start',
            **{'justify-cjk':'justify (CJK): non-final lines end at the right edge; observed per-glyph advances are kept'})[align]
        evidence['column_flow']=dict(mode='single-column',column_bottom=column_bottom,**neighbours)
        if column_bottom is not None:evidence['warnings'].append('Side-by-side columns share a bottom; this paragraph is not in a linked flow and may not grow below it.')
        spec=dict(page=page,frame=frame,column_bottom=column_bottom,candidates=[l['id'] for l in neighbours['links'] if l['status']=='candidate'],rejected_links=[f"{l['id']}: {l['reason']}" for l in neighbours['links'] if l['status']=='rejected'],left=left,right=right,first_x=first,align=align,baseline=selected[0]['baseline'],line_height=leading,line_offsets=[r['baseline']-selected[0]['baseline'] for r in selected],
                  stored=state,values=values,provenance=provenance,origin=origin,
                  # The drawn lines of the edited paragraph: local layout keeps
                  # what the edit does not change where it is (auto_local).
                  source_rows=source_rows(c,selected))
        return c,paragraphs,spec,start,evidence
    except Exception:c.close();raise
