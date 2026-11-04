"""The PDF side of the ParagraphIR PoC (issue #124): import once, write back once per save.

Only this module reads or writes PDF operators. It reuses pdfeditor's content
parser (``ContentPage``: operator byte ranges, per-glyph provenance matched to
MuPDF's text trace, font codecs) and its glyph removal (``rewritten_event``:
removed glyphs become TJ displacement, so the other glyphs a shared operator
draws stay where they were).

import_paragraph(pdf, page, click, old) -> ParagraphIR
    The paragraph under the click, from the page's glyphs (not from operator
    boundaries): baseline rows, cut into segments at wide gaps, chained to the
    lines above/below in the same style and pitch; centered blocks are one
    paragraph with hard breaks; left/justified blocks end at a line ending
    short of the block's right edge. Per character: text, style, spacing to
    the next character, provenance (glyph order, line, code, origin).
    Relations: the drawing holding the paragraph (container), what is drawn
    around it (obstacles), what lies right below (followers), the drawings on
    each line (decorations) and each line's marked content (structure).

write(ir, source, output, approve_growth=False) -> dict
    Lays the IR out. Source lines whose layout changed are removed glyph by
    glyph; the IR's lines are drawn with the original font resources and
    codes, right after the text object of the source line they stand in,
    inside the same marked content (tags and optional content kept), in the
    page's default space. Refused (nothing written) when a line would need a
    code the font resource lacks, grow without approval, leave its container,
    meet something drawn, move a line's decoration, or when the paragraph is
    drawn in a Form XObject or a marked-content sequence closes inside the
    text object. The result is saved to a temporary file and audited against
    the source; only an audited file is renamed onto the output.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import statistics

import pymupdf

from pdfeditor.content_stream import ContentPage, multiply, number, rewritten_event
from pdfeditor.replay import set_page_program

from .ir import Frame, IRChar, ParagraphIR, Refused, SourceRef, Style, is_cjk


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# -- import ------------------------------------------------------------------------------------------------------

def _glyphs(cp):
    """Every glyph the page program draws, with the state it is drawn in."""
    out = []
    for ei, e in enumerate(cp.events):
        m = multiply(e.text_matrix, e.state.ctm)
        for c in e.chars:
            order = c.source_orders[0] if len(c.source_orders) == 1 else None
            bbox = pymupdf.Rect(cp.actual[order]['bbox']) if order is not None else None
            ok = not e.error and not e.invocation and order is not None and e.stream_xref == -cp.page.xref
            why = (e.error or ('drawn in a Form XObject' if e.invocation else None)
                   or ('no unique glyph provenance' if order is None else None))
            out.append(dict(event=ei, e=e, c=c, order=order, text=c.text, x=c.origin[0], y=c.origin[1], adv=c.advance * m[0],
                            matrix=tuple(round(v, 6) for v in m[:4]), size=e.state.size * abs(m[3]) or e.state.size, bbox=bbox,
                            ok=ok, why=why))
    return out


def _segments(glyphs):
    """Rows of glyphs on one baseline, cut where the gap is wider than 1 em (another column or item)."""
    rows = []
    for g in sorted(glyphs, key=lambda g: (g['y'], g['x'])):
        if g['bbox'] is None:
            continue
        if rows and abs(rows[-1][0]['y'] - g['y']) < .25 * max(g['size'], 1):
            rows[-1].append(g)
        else:
            rows.append([g])
    segs = []
    for row in rows:
        row.sort(key=lambda g: g['x'])
        cur = [row[0]]
        for g in row[1:]:
            p = cur[-1]
            if g['x'] - (p['x'] + p['adv']) > 1.0 * max(p['size'], g['size']):
                segs.append(cur)
                cur = [g]
            else:
                cur.append(g)
        segs.append(cur)
    out = []
    for s in segs:
        ink = [g for g in s if g['text'].strip()] or s
        style = Counter((g['e'].state.font.basefont if g['e'].state.font else None, round(g['size'], 1), str(g['e'].state.fill))
                        for g in ink).most_common(1)[0][0]
        box = pymupdf.Rect(ink[0]['bbox'])
        for g in ink:
            box |= g['bbox']
        lead = (ink[0]['e'].state.font.basefont if ink[0]['e'].state.font else None, round(ink[0]['size'], 1), str(ink[0]['e'].state.fill))
        out.append(dict(glyphs=s, y=statistics.median(g['y'] for g in s), x0=ink[0]['x'], x1=ink[-1]['x'] + ink[-1]['adv'],
                        box=box, style=style, size=style[1], marker=lead != style))
    return out


def _block(segs, hit):
    """The lines chained to the clicked one: same dominant style, overlapping, at a steady pitch."""
    def step(cur, direction, pitch):
        cands = [s for s in segs if (s['y'] - cur['y']) * direction > .5 * cur['size'] and s['style'] == cur['style']
                 and min(s['x1'], cur['x1']) - max(s['x0'], cur['x0']) > 0]
        if not cands:
            return None
        nxt = min(cands, key=lambda s: abs(s['y'] - cur['y']))
        d = abs(nxt['y'] - cur['y'])
        # Nothing else in the same style may lie between them.
        if d > 2.2 * cur['size'] or (pitch and abs(d - pitch) > .2 * pitch):
            return None
        return nxt, d
    lines, pitch = [hit], None
    for direction in (1, -1):
        cur, p = hit, pitch
        while True:
            got = step(cur, direction, p)
            if not got:
                break
            cur, p = got
            pitch = pitch or p
            lines.append(cur)
    return sorted(lines, key=lambda s: s['y']), pitch


def _structure(cp):
    """Per top-level text operator (by byte start): the marked-content sequences open around it."""
    stack, out = [], {}
    for b in cp.boundaries:
        op = b.operator
        if op.name in ('BDC', 'BMC'):
            stack.append((op.name, [str(a) for a in op.args]))
        elif op.name == 'EMC' and stack:
            stack.pop()
        out[op.start] = list(stack)
    return out


def _plain_blend(blend):
    """Normal blending without a soft mask (overprint settings are inherited where the text is redrawn: checked
    there)."""
    return blend.get('/BM', '/Normal') in ('/Normal', '/Compatible') and blend.get('/SMask', '/None') == '/None'


def import_paragraph(path, page_number, at, old=None):
    """ParagraphIR of the paragraph under ``at`` (page space), and the selection [a, b) of ``old`` in it."""
    cp = ContentPage(str(path), page_number)
    try:
        page = cp.page
        glyphs = _glyphs(cp)
        segs = _segments(glyphs)
        point = pymupdf.Point(at)
        hit = next((s for s in segs if (s['box'] + (-1, -1, 1, 1)).contains(point)), None)
        if hit is None:
            raise Refused('no text line under the click')
        block, pitch = _block(segs, hit)
        if len(block) >= 2:
            centers = [(s['x0'] + s['x1']) / 2 for s in block]
            # (A line-end punctuation mark may be set narrower: 0.35 em.)
            centered = max(centers) - min(centers) < .35 * block[0]['size'] and max(s['x0'] for s in block) - min(s['x0'] for s in block) > 1.0
        else:
            centered = False
        if centered:
            lines = block
            alignment = 'center'
        else:
            right = max(s['x1'] for s in block)
            short = lambda s: s['x1'] < right - 1.5 * s['size']
            # A paragraph ends after a line ending short of the block's right edge, or before a line that starts
            # with a marker (a run in another style than the line's: a bullet, a badge, a number).
            k = block.index(hit)
            lo = k
            while lo > 0 and not short(block[lo - 1]) and not block[lo]['marker']:
                lo -= 1
            hi = k
            while hi + 1 < len(block) and not short(block[hi]) and not block[hi + 1]['marker']:
                hi += 1
            lines = block[lo:hi + 1]
            ends = [s['x1'] for s in lines[:-1]]
            alignment = 'justify' if len(ends) >= 2 and max(ends) - min(ends) < .6 or (len(ends) == 1 and abs(ends[0] - right) < .6 and len(lines) > 1) else 'left'
        bad = [g for s in lines for g in s['glyphs'] if not g['ok']]
        if bad:
            raise Refused(f"the paragraph's glyphs cannot be owned: {bad[0]['why']} ({bad[0]['text']!r})")
        structure = _structure(cp)
        styles, keys, chars, source_lines = {}, {}, [], []
        for k, s in enumerate(lines):
            gs = s['glyphs']
            start = len(chars)
            for n, g in enumerate(gs):
                st = g['e'].state
                if st.ts or st.tr not in (0,) or st.opacity != 1 or not _plain_blend(st.blend) or st.fill_space is not None \
                        or abs(g['matrix'][1]) > 1e-6 or abs(g['matrix'][2]) > 1e-6 or st.fill[0] not in ('k', 'rg', 'g'):
                    raise Refused(f'a glyph of the paragraph is drawn in a state this PoC does not redraw '
                                  f'(rise, render mode, opacity/blend, colour space or rotation): {g["text"]!r}')
                key = (st.font.name, st.font.xref, st.size, g['matrix'], st.fill, st.tc, st.tw, st.tz)
                if key not in keys:
                    keys[key] = f's{len(keys)}'
                    styles[keys[key]] = Style(st.font.name, st.font.xref, st.font.basefont, st.size, g['matrix'], st.fill,
                                              st.tc, st.tw, st.tz, st.tr, 0, 0, codec=st.font)
                sty = styles[keys[key]]
                sty.ascent = max(sty.ascent, g['y'] - g['bbox'].y0)
                sty.descent = max(sty.descent, g['bbox'].y1 - g['y'])
                nxt = gs[n + 1] if n + 1 < len(gs) else None
                ref = SourceRef(g['order'], k, g['c'].code, (g['x'], g['y']),
                                tuple((n, tuple(args)) for n, args in structure.get(g['e'].operator.start, [])))
                if nxt is None:
                    chars.append(IRChar(g['text'], keys[key], g['adv'], 'glyph', ref))
                    continue
                gap = nxt['x'] - (g['x'] + g['adv'])
                if gap > .15 * g['size'] and g['text'] != ' ' and nxt['text'] != ' ':
                    chars.append(IRChar(g['text'], keys[key], g['adv'], 'glyph', ref))
                    chars.append(IRChar(' ', keys[key], gap, 'gap'))
                else:
                    chars.append(IRChar(g['text'], keys[key], nxt['x'] - g['x'], 'glyph', ref))
            source_lines.append(dict(start=start, end=len(chars), baseline=s['y'], x0=s['x0'], x1=s['x1'],
                                     orders=[g['order'] for g in gs], box=list(s['box']),
                                     marked=structure.get(gs[-1]['e'].operator.start, [])))
            if k + 1 < len(lines):
                last, first = chars[-1].text, lines[k + 1]['glyphs'][0]['text']
                if last != '-' and not (is_cjk(last) or is_cjk(first)) and last != ' ':
                    chars.append(IRChar(' ', chars[-1].style, 0.0, 'join'))
        gaps = [c.adv for c in chars if c.kind == 'gap']
        min_gap = min(gaps) if gaps else .25 * lines[0]['size']
        size = lines[0]['size']
        gap_width = statistics.median(gaps) if gaps else .25 * size
        for c in chars:
            if c.kind == 'join':
                c.adv = gap_width
        baselines = [s['y'] for s in lines]
        f = Frame(left=min(s['x0'] for s in lines[1:]) if len(lines) > 1 else lines[0]['x0'], first_x=lines[0]['x0'],
                  # Left-aligned: the right edge of the block of lines it stands in (its column), not its own lines'.
                  right=(statistics.median(s['x1'] for s in lines[:-1]) if alignment == 'justify'
                         else max(s['x1'] for s in (block if alignment == 'left' else lines))),
                  baselines=baselines, pitch=pitch or 1.5 * size, alignment=alignment,
                  center=statistics.mean((s['x0'] + s['x1']) / 2 for s in lines), hard_breaks=alignment == 'center',
                  max_width=max(s['x1'] - s['x0'] for s in lines))
        # A frame the PDF carries for exactly these glyphs (saved by write()) replaces the inferred one.
        state = 'none'
        mine = [(g['text'], g['x'], g['y']) for s in lines for g in s['glyphs'] if g['text'].strip()]
        records = _read_state(cp.document, page)
        bound = next((r for r in records if _bound(r, mine)), None)
        if bound:
            for k2, v in bound['frame'].items():
                setattr(f, k2, v)
            state = 'frame read from the PDF (bound to the drawn glyphs)'
            if bound.get('text') == ''.join(c.text for c in chars) and bound.get('history'):
                by_font = {(st.font, st.size): key for key, st in styles.items()}
                history = []
                for h in bound['history']:
                    removed = []
                    for r in h['removed']:
                        anchor = chars[max(h['at'] - 1, 0)].style
                        removed.append(IRChar(r['text'], by_font.get((r['font'], r['size']), anchor), r['adv'], r['kind'], None))
                    history.append(dict(at=h['at'], inserted=h['inserted'], text=h['text'], old=h['old'], removed=removed))
                state += '; edit history read from the PDF'
        elif records:
            state = 'a state in the PDF, not bound to these glyphs: ignored'
        box = pymupdf.Rect(lines[0]['box'])
        for s in lines:
            box |= s['box']
        own = {g['order'] for s in lines for g in s['glyphs']}
        relations = _relations(page, glyphs, own, box, lines)
        relations['pitch_basis'] = 'observed' if pitch else 'unobserved: 1.5 em default'
        relations['state_in_pdf'] = state
        ir = ParagraphIR(source=dict(path=str(path), sha256=_sha(path), page=page_number), chars=chars, styles=styles,
                         frame=f, relations=relations, source_lines=source_lines, gap_width=gap_width, min_gap=min_gap)
        if bound and 'edit history' in state:
            ir.history = history
        if old is not None:
            clicked = None
            for i, c in enumerate(chars):
                if c.src is not None and (cp.actual[c.src.order]['bbox'] and pymupdf.Rect(cp.actual[c.src.order]['bbox']) + (-.5, -.5, .5, .5)).contains(point):
                    clicked = i
            ir.selection = ir.find(old, clicked)
        return ir
    finally:
        cp.close()


def _relations(page, glyphs, own, box, lines):
    drawings = [d for d in page.get_drawings() if d.get('fill') is not None or d.get('color') is not None]
    holders = [d['rect'] for d in drawings if d['rect'].contains(box)]
    container = min((r for r in holders if r.width < page.rect.width * .95), key=lambda r: r.get_area(), default=None)
    decorations = {}
    for k, s in enumerate(lines):
        decorations[k] = [list(d['rect']) for d in drawings if d['rect'].intersects(s['box']) and not d['rect'].contains(box)]
    obstacles = [dict(kind='text', bounds=list(g['bbox']), text=g['text']) for g in glyphs
                 if g['order'] not in own and g['bbox'] is not None and g['text'].strip()]
    obstacles += [dict(kind='drawing', bounds=list(d['rect'])) for d in drawings if not d['rect'].contains(box)]
    # An image the whole paragraph lies on is its background (like a drawing holding it), not in its way.
    obstacles += [dict(kind='image', bounds=list(i['bbox'])) for i in page.get_image_info() if not pymupdf.Rect(i['bbox']).contains(box)]
    obstacles += [dict(kind='annotation', bounds=list(a.rect)) for a in page.annots()]
    obstacles += [dict(kind='link', bounds=list(l['from'])) for l in page.get_links()]
    below = [o for o in obstacles if o['bounds'][1] >= box.y1 - .5 and o['bounds'][0] < box.x1 and o['bounds'][2] > box.x0]
    nearest = min((o['bounds'][1] for o in below), default=None)
    followers = [o for o in below if nearest is not None and o['bounds'][1] - nearest < 1]
    return dict(container=list(container) if container else None, holders=[list(r) for r in holders],
                decorations={k: v for k, v in decorations.items() if v}, obstacles=obstacles,
                followers=[dict(kind=o['kind'], bounds=[round(v, 2) for v in o['bounds']], **({'text': o['text']} if 'text' in o else {}))
                           for o in followers[:6]],
                structure=[s['glyphs'][-1]['e'].operator.start for s in lines], paragraph_box=list(box))


# -- the IR's layout state carried in the PDF ----------------------------------------------------------------------

STATE_KEY = 'PieceInfo/PdfengineParagraphIR/Private'
FRAME_FIELDS = ('left', 'first_x', 'right', 'alignment', 'pitch', 'center', 'max_width', 'hard_breaks')


def _read_state(doc, page):
    kind, value = doc.xref_get_key(page.xref, STATE_KEY)
    if kind != 'xref':
        return []
    try:
        return json.loads(doc.xref_stream(int(value.split()[0]))).get('paragraphs', [])
    except Exception:
        return []


def _bound(record, glyphs):
    """Whether ``glyphs`` (text, x, y) are exactly the glyphs the record was saved with (0.15 pt)."""
    keys = record.get('glyphs') or []
    if len(keys) != len(glyphs):
        return False
    pool = sorted(keys)
    for t, x, y in sorted(glyphs):
        hit = next((n for n, k in enumerate(pool) if k[0] == t and abs(k[1] - x) <= .15 and abs(k[2] - y) <= .15), None)
        if hit is None:
            return False
        pool.pop(hit)
    return True


def _write_state(doc, page, ir, lines):
    """Record the IR's frame with the glyphs the saved paragraph is drawn with (the binding), in the page's
    PieceInfo; a record bound to glyphs no longer drawn is dropped."""
    glyphs = [[ir.chars[i].text, round(l.xs[i - l.start], 2), round(l.baseline, 2)] for l in lines
              for i in range(l.start, l.end) if ir.chars[i].kind == 'glyph' and ir.chars[i].text.strip()]
    old = _read_state(doc, page)
    f = ir.frame
    # The edit history: what each edit took out (text, spacing, font), so typing it back from this PDF alone
    # gives the same characters with the same spacing.
    history = [dict(at=h['at'], inserted=h['inserted'], text=h['text'], old=h['old'],
                    removed=[dict(text=c.text, kind=c.kind, adv=c.adv, font=ir.styles[c.style].font, size=ir.styles[c.style].size)
                             for c in h['removed']]) for h in ir.history]
    record = dict(frame={k: getattr(f, k) for k in FRAME_FIELDS}, lines=len(lines), glyphs=glyphs, text=ir.text, history=history,
                  source=dict(sha256=ir.source['sha256']))
    keep = [r for r in old if not set(map(tuple, r.get('glyphs', []))) & set(map(tuple, glyphs))]
    data = json.dumps(dict(schema='paragraph-ir-state-1', paragraphs=keep + [record]), ensure_ascii=False).encode()
    xref = doc.get_new_xref()
    doc.update_object(xref, '<<>>')
    doc.update_stream(xref, data)
    entry = f'<</LastModified (D:20261104000000Z) /Private {xref} 0 R>>'
    # Other applications' PieceInfo entries stay as they are.
    if doc.xref_get_key(page.xref, 'PieceInfo')[0] == 'dict':
        doc.xref_set_key(page.xref, 'PieceInfo/PdfengineParagraphIR', entry)
    else:
        doc.xref_set_key(page.xref, 'PieceInfo', f'<</PdfengineParagraphIR {entry}>>')
    doc.xref_set_key(page.xref, 'LastModified', '(D:20261104000000Z)')


# -- write back --------------------------------------------------------------------------------------------------

def _fill(fill):
    op, vals = fill
    return (' '.join(str(v) for v in vals) + f' {op}').encode()


def _run_bytes(ir, line, to_pdf, event, start, end, offset):
    """Operators drawing the characters [start, end) of ``line`` inside the open text object, right before
    ``event``: per style run, the run's font, spacing and colour, a move to its first glyph and one TJ with the
    displacement each next glyph needs to stand where the layout put it. A run in the event's own text space is
    reached by Td from the line matrix (``offset``: how far the line matrix has been moved so far, in text space),
    so that the line matrix can be moved back exactly; any other run sets Tm. Returns (bytes, offset, absolute)."""
    out = []
    lm = pymupdf.Matrix(*event.line_matrix) * pymupdf.Matrix(*event.state.ctm)
    inv_ctm = ~pymupdf.Matrix(*event.state.ctm)
    absolute = False
    i = start
    while i < end:
        c = ir.chars[i]
        if c.kind != 'glyph':
            i += 1
            continue
        st = ir.styles[c.style]
        run = []
        while i < end and (ir.chars[i].kind != 'glyph' or ir.chars[i].style == c.style):
            if ir.chars[i].kind == 'glyph':
                run.append(i)
            i += 1
        p = pymupdf.Point(line.xs[run[0] - line.start], line.baseline) * to_pdf
        a, b, cc, d = st.matrix
        out.append(_fill(st.fill) + b' /' + st.font.lstrip('/').encode() + b' ' + number(st.size) + b' Tf ' + number(st.tc) + b' Tc '
                   + number(st.tw) + b' Tw ' + number(st.tz) + b' Tz')
        if max(abs(x - y) for x, y in zip((a, b, cc, d), (lm.a, lm.b, lm.c, lm.d))) < 1e-4 and not absolute:
            q = p * ~lm
            out.append(number(q.x - offset[0]) + b' ' + number(q.y - offset[1]) + b' Td')
            offset = (q.x, q.y)
        else:
            tm = pymupdf.Matrix(a, b, cc, d, p.x, p.y) * inv_ctm
            out.append(b' '.join(number(v) for v in (tm.a, tm.b, tm.c, tm.d, tm.e, tm.f)) + b' Tm')
            absolute = True
        items, pen = [], None
        for n in run:
            ch = ir.chars[n]
            code = ch.src.code if ch.src is not None and ch.style == c.style else st.advance(ch.text)[0]
            x = line.xs[n - line.start]
            if pen is not None and abs(x - pen) > 1e-4:
                items.append(number(-(x - pen) / a / (st.size * st.tz / 100) * 1000))
            items.append(b'<' + code.hex().encode() + b'>')
            width = st.codec.decode(code)[0][2]
            pen = x + (width / 1000 * st.size + st.tc + (st.tw if code == b' ' else 0)) * st.tz / 100 * a
        out.append(b'[' + b' '.join(items) + b'] TJ')
    return b'\n'.join(out), offset, absolute


def _restore(event, offset, absolute):
    """Operators putting the text state back to what ``event`` starts in: colour, font, spacing; the line matrix
    moved back by the Td offset (exact), or, after an absolute Tm, set again (as parsed); then the text matrix's
    horizontal offset from the line matrix, by displacement."""
    st = event.state
    lm, tm = pymupdf.Matrix(*event.line_matrix), pymupdf.Matrix(*event.text_matrix)
    out = [_fill(st.fill) + b' /' + st.font.name.lstrip('/').encode() + b' ' + number(st.size) + b' Tf ' + number(st.tc) + b' Tc '
           + number(st.tw) + b' Tw ' + number(st.tz) + b' Tz']
    if absolute:
        out.append(b' '.join(number(v) for v in event.line_matrix) + b' Tm')
    elif offset != (0.0, 0.0):
        out.append(number(-offset[0]) + b' ' + number(-offset[1]) + b' Td')
    off = tm * ~lm
    if abs(off.f) > 1e-4 or abs(off.a - 1) > 1e-4 or abs(off.d - 1) > 1e-4:
        raise Refused('the text matrix is not a horizontal offset from the line matrix')
    if abs(off.e) > 1e-6:
        out.append(b'[' + number(-off.e * 1000 / (st.size * st.tz / 100)) + b'] TJ')
    return b'\n'.join(out)


def plan(ir, lines, approve_growth=False):
    """What a save of ``lines`` changes, or Refused: the source lines removed, the lines drawn, the approvals."""
    removed = {k for k in range(len(ir.source_lines)) if not any(l.retained and l.source_line == k for l in lines)}
    drawn = [l for l in lines if not l.retained]
    rel = ir.relations
    para = pymupdf.Rect(rel['paragraph_box'])
    approvals = []
    for l in drawn:
        box = l.box(ir)
        if box is None:
            continue
        box = pymupdf.Rect(box)
        k = l.source_line
        moved = k is None or abs(l.baseline - ir.source_lines[k]['baseline']) > .01
        if moved and k is not None and k in rel['decorations']:
            raise Refused(f'line {k + 1} would move away from its decoration {rel["decorations"][k][0]}: moving drawings is not done by this PoC')
        if not (para + (-.5, -.5, .5, .5)).contains(box):
            if rel['container'] and not pymupdf.Rect(rel['container']).contains(box):
                raise Refused(f'the paragraph would leave its container {[round(v, 1) for v in rel["container"]]}')
            if not approve_growth:
                raise Refused(f'the paragraph grows beyond its area (to {[round(v, 1) for v in box]}): needs approval')
            approvals.append(f'line {lines.index(l) + 1} drawn at {[round(v, 1) for v in box]}, outside the paragraph\'s area, inside its container')
        decos = [pymupdf.Rect(r) for r in rel['decorations'].get(k, [])] if not moved else []
        inner = box + (.2, .2, -.2, -.2)
        for o in rel['obstacles']:
            r = pymupdf.Rect(o['bounds'])
            if r.intersects(inner) and (r & inner).get_area() > .05 and not any(d.contains(r) or abs(d.x0 - r.x0) + abs(d.y0 - r.y0) < .01 for d in decos):
                raise Refused(f'line {lines.index(l) + 1} would meet {o["kind"]} {o.get("text", "")!r} at {[round(v, 1) for v in o["bounds"]]}: '
                              'moving what is in the way is not done by this PoC')
    return removed, drawn, approvals


def write(ir, source, output, approve_growth=False):
    """Save the IR's paragraph into ``source`` as ``output`` (see the module doc). dict(outcome, ...)."""
    if _sha(source) != ir.source['sha256']:
        raise Refused('the PDF is not the one the IR was imported from')
    lines = ir.layout()
    removed, drawn, approvals = plan(ir, lines, approve_growth)
    page_number = ir.source['page']
    cp = ContentPage(str(source), page_number)
    try:
        orders = {o for k in removed for o in ir.source_lines[k]['orders']}
        try:
            events = cp.selected_events(orders) if orders else []
        except Exception as exc:
            raise Refused(f'the source glyphs cannot be removed: {exc}')
        virtual = -cp.page.xref
        if any(e.stream_xref != virtual for e in events):
            raise Refused('the paragraph is drawn outside the page program')
        data = cp.streams[virtual]
        to_pdf = ~cp.page.transformation_matrix
        structure = _structure(cp)
        edits = [(e.operator.start, e.operator.end, rewritten_event(e, orders, remove=True)[0]) for e in events]
        # Each drawn line goes after the text object of the source line it stands in (or the last removed one).
        by_line = {}
        for e in events:
            for c in e.chars:
                for o in c.source_orders:
                    for k in removed:
                        if o in ir.source_lines[k]['orders']:
                            by_line.setdefault(k, []).append(e)
        # Each drawn line is cut into segments by the marked content its characters came from (a new character takes
        # its neighbour's); a segment is drawn right before the first operator that drew its source glyphs, inside
        # the same text object and marked content, and the text state is put back for that operator.
        event_of = {o: e for e in events for c in e.chars for o in c.source_orders}
        inserts = {}
        for l in drawn:
            k = l.source_line if l.source_line in removed else max((r for r in removed if l.source_line is None or r <= l.source_line), default=None)
            if k is None:
                raise Refused('a drawn line has no removed source line to stand in')
            fallback = min(by_line[k], key=lambda e: e.operator.start)
            anchors = [event_of.get(ir.chars[i].src.order) if ir.chars[i].src is not None else None for i in range(l.start, l.end)]
            for n in range(len(anchors)):
                if anchors[n] is None and n:
                    anchors[n] = anchors[n - 1]
            for n in reversed(range(len(anchors))):
                if anchors[n] is None:
                    anchors[n] = anchors[n + 1] if n + 1 < len(anchors) else fallback
            stack = lambda e: tuple((x, tuple(a)) for x, a in structure.get(e.operator.start, []))
            n = 0
            while n < len(anchors):
                m = n
                while m + 1 < len(anchors) and stack(anchors[m + 1]) == stack(anchors[n]):
                    m += 1
                first = min((anchors[i] for i in range(n, m + 1)), key=lambda e: e.operator.start)
                if first.operator.name not in ('Tj', 'TJ', "'", '"'):
                    raise Refused(f'unexpected text operator {first.operator.name}')
                slot = inserts.setdefault(first.operator.start, dict(event=first, parts=[], offset=(0.0, 0.0), absolute=False))
                body, slot['offset'], absolute = _run_bytes(ir, l, to_pdf, first, l.start + n, l.start + m + 1, slot['offset'])
                slot['absolute'] = slot['absolute'] or absolute
                if body:
                    slot['parts'].append(body)
                n = m + 1
        for pos, slot in list(inserts.items()):
            inserts[pos] = [b'\n' + b'\n'.join(slot['parts']) + b'\n' + _restore(slot['event'], slot['offset'], slot['absolute']) + b'\n']
        edits += [(pos, pos, b''.join(parts)) for pos, parts in inserts.items()]
        # An insertion and the rewritten operator start at the same byte: the insertion goes first.
        for start, end, new in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
            data = data[:start] + new + data[end:]
        area = [pymupdf.Rect(ir.source_lines[k]['box']) for k in removed] + [pymupdf.Rect(l.box(ir)) for l in drawn if l.box(ir)]
        target = Path(output)
        temp = target.with_name(f'.{target.name}.{os.getpid()}.unaudited')
        with pymupdf.open(str(source)) as doc:
            set_page_program(doc, page_number - 1, data)
            if drawn:
                _write_state(doc, doc[page_number - 1], ir, lines)
            doc.save(str(temp), garbage=1, deflate=True)
        try:
            checked = audit(cp, str(temp), page_number, orders, ir, drawn, area)
            if checked['failed']:
                return dict(outcome='refused', unsafe=True, reason='unsafe, not written: ' + '; '.join(checked['failed']), audit=checked)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return dict(outcome='saved', removed_lines=sorted(removed), drawn_lines=len(drawn), retained_lines=sum(l.retained for l in lines),
                    lines=[ir.text[l.start:l.end] for l in lines], baselines=[round(l.baseline, 3) for l in lines],
                    approvals=approvals, audit=checked, events_patched=len(events),
                    shared_operators=sum(1 for e in events if any(not set(c.source_orders) <= orders for c in e.chars)))
    finally:
        cp.close()


def _trace(page):
    out = []
    for span in page.get_texttrace():
        for c in span['chars']:
            out.append((chr(c[0]), round(c[2][0], 1), round(c[2][1], 1)))
    return out


def mcids(doc, page):
    data = b''.join(doc.xref_stream(x) for x in page.get_contents())
    return Counter(int(m) for m in re.findall(rb'/MCID\s+(\d+)', data))


def audit(cp, candidate, page_number, orders, ir, drawn, area):
    """Before publishing: what the candidate changed besides the IR's lines."""
    out = {}
    src = [(cp.actual[i]['unicode'], round(cp.actual[i]['origin'][0], 1), round(cp.actual[i]['origin'][1], 1)) for i in range(len(cp.actual))]
    kept = Counter(g for i, g in enumerate(src) if i not in orders)
    want = Counter((ir.chars[i].text, round(l.xs[i - l.start], 1), round(l.baseline, 1)) for l in drawn
                   for i in range(l.start, l.end) if ir.chars[i].kind == 'glyph')
    with pymupdf.open(cp.source) as a, pymupdf.open(candidate) as b:
        pa, pb = a[page_number - 1], b[page_number - 1]
        got = Counter(_trace(pb))
        near = lambda g, pool: any(abs(g[1] - h[1]) <= .15 and abs(g[2] - h[2]) <= .15 and g[0] == h[0] for h in pool)
        lost = [g for g in (kept - got) if not near(g, got.keys())]
        missing = [g for g in (want - got) if not near(g, got.keys())]
        extra = [g for g in (got - kept - want) if not near(g, kept.keys()) and not near(g, want.keys())]
        drawn_key = lambda p: Counter((x.get('type'), str(x.get('fill')), str(x.get('color')), tuple(round(v, 1) for v in x['rect'])) for x in p.get_drawings())
        out.update(glyphs_lost=lost[:10], new_text_missing=missing[:10], glyphs_added=extra[:10],
                   drawings=drawn_key(pa) == drawn_key(pb),
                   images=sorted(str(x['bbox']) for x in pa.get_image_info()) == sorted(str(x['bbox']) for x in pb.get_image_info()),
                   annotations=[(x.type, tuple(x.rect)) for x in pa.annots()] == [(x.type, tuple(x.rect)) for x in pb.annots()],
                   links=[(tuple(x['from']), x.get('uri'), x.get('page')) for x in pa.get_links()] == [(tuple(x['from']), x.get('uri'), x.get('page')) for x in pb.get_links()],
                   other_pages=len(a) == len(b) and all(a[k].read_contents() == b[k].read_contents() for k in range(len(a)) if k != page_number - 1),
                   mcids=mcids(a, pa) == mcids(b, pb))
        ma, mb = pa.get_pixmap(alpha=False), pb.get_pixmap(alpha=False)
        for r in area:
            box = (r + (-1, -1, 1, 1)).irect
            ma.set_rect(box, (255, 255, 255))
            mb.set_rect(box, (255, 255, 255))
        out['pixels_outside'] = sum(x != y for x, y in zip(ma.samples, mb.samples))
    out['failed'] = ([f'glyphs not of the edited lines lost: {len(lost)}'] if lost else []) + \
                    ([f'IR text not drawn where laid out: {len(missing)}'] if missing else []) + \
                    ([f'glyphs added elsewhere: {len(extra)}'] if extra else []) + \
                    [k for k in ('drawings', 'images', 'annotations', 'links', 'other_pages', 'mcids') if out[k] is False] + \
                    ([f"pixels outside the edited lines: {out['pixels_outside']}"] if out['pixels_outside'] else [])
    return out
