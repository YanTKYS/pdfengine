"""PoC of another route (issue #122): re-typeset the clicked paragraph with the PDF library.

  .venv/bin/python -m experiments.route_poc.retypeset <in.pdf> <out.pdf> --page N --at X,Y --old TEXT --new TEXT [--approve-growth] [--approve-font]

A user clicks in a paragraph, selects text and types. Here that is the click
point, the selected text (``--old``, the occurrence under the click) and the
typed text (``--new``). Nothing else is given: no operator, glyph id, frame,
font file or candidate id.

1. The paragraph is read from the page's text layout (MuPDF): the block
   holding the click, cut at a font-size change, a gap wider than 1.6 line
   pitches, or after a line ending 1.5 em short of the block's right edge (a
   hard line break). Its logical text joins the lines (a space between Latin words,
   none in Japanese, none after a hyphen).
2. The selected occurrence (the one holding the clicked glyph) is replaced.
3. The paragraph's glyphs are removed by redaction with text removal only
   (drawings and images kept): the old text is gone, nothing is painted over.
4. The new paragraph is laid out (left edge, first-line start, width and line
   pitch as drawn; Latin breaks at spaces, Japanese at any character with the
   basic line-start rules) and written as text with the paragraph's own
   embedded font when it has every glyph needed, else a fallback font
   (``--approve-font``: a visible change, approved per edit). Size and colour
   are the paragraph's.
5. A paragraph that grows needs ``--approve-growth`` and room: nothing drawn
   (text, drawing, image, annotation) in the band it grows into, and still
   inside every filled or stroked rectangle that held it (a panel, a box).
   Otherwise it is refused, saying what is in the way. This PoC never moves
   other content.

What it does not do (by design of the PoC): move following content, extend
panels or clips, keep the paragraph's marked content (tags) or its unedited
glyphs as they were drawn, or keep kerning and letter spacing.
"""
import argparse
import json
from pathlib import Path
import re
import time

import pymupdf

NO_START = set('、。，．・：；？！）」』】〕｝〉》ーぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ,.;:!?)]}')
NO_END = set('（「『【〔｛〈《([{')


class Refused(Exception):
    pass


def is_cjk(ch):
    return ord(ch) >= 0x2E80


def _paragraph(page, at):
    """(lines, block) of the paragraph under the click: lines as dict(text, bbox, origin_y, chars, spans)."""
    d = page.get_text('rawdict')
    point = pymupdf.Point(at)
    for b in d['blocks']:
        if b.get('type') != 0:
            continue
        lines = []
        for l in b['lines']:
            chars = [c for s in l['spans'] for c in s['chars']]
            if not chars or abs(l['dir'][0] - 1) > 1e-3:
                continue
            lines.append(dict(bbox=pymupdf.Rect(l['bbox']), chars=chars, spans=l['spans'], origin_y=chars[0]['origin'][1],
                              size=max(s['size'] for s in l['spans'])))
        hit = next((n for n, l in enumerate(lines) if (l['bbox'] + (-1, -1, 1, 1)).contains(point)), None)
        if hit is None:
            continue
        lines.sort(key=lambda l: (round(l['origin_y'], 1), l['bbox'].x0))
        hit = next(n for n, l in enumerate(lines) if (l['bbox'] + (-1, -1, 1, 1)).contains(point))
        # Merge lines on one baseline (a line drawn in pieces), then cut at a size change or a wide gap.
        rows = []
        for l in lines:
            if rows and abs(rows[-1]['origin_y'] - l['origin_y']) < .6 * l['size']:
                r = rows[-1]
                r['chars'] += l['chars']
                r['spans'] += l['spans']
                r['bbox'] |= l['bbox']
            else:
                rows.append(dict(l, chars=list(l['chars']), spans=list(l['spans'])))
        for r in rows:
            r['chars'].sort(key=lambda c: c['origin'][0])
        row = next(n for n, r in enumerate(rows) if (r['bbox'] + (-1, -1, 1, 1)).contains(point))
        pitches = [b2['origin_y'] - a['origin_y'] for a, b2 in zip(rows, rows[1:])]
        pitch = sorted(pitches)[len(pitches) // 2] if pitches else None
        right = max(r['bbox'].x1 for r in rows)
        def same(a, b2):
            # A line ending well short of the block's right edge ends its paragraph (a hard line break).
            return (abs(a['size'] - b2['size']) < .6 and (pitch is None or b2['origin_y'] - a['origin_y'] < 1.6 * pitch)
                    and a['bbox'].x1 >= right - 1.5 * a['size'])
        lo = row
        while lo > 0 and same(rows[lo - 1], rows[lo]):
            lo -= 1
        hi = row
        while hi + 1 < len(rows) and same(rows[hi], rows[hi + 1]):
            hi += 1
        return rows[lo:hi + 1], b
    raise Refused('no text line under the click')


def _text(rows):
    """Logical text of the rows, and per character its (row, index in row) (None for an inserted join space)."""
    out, where = '', []
    for k, r in enumerate(rows):
        t = ''.join(c['c'] for c in r['chars']).rstrip()
        if k and out:
            a, b = out[-1], t[:1]
            if a == '-' or not b:
                pass
            elif not (is_cjk(a) or is_cjk(b)) and a != ' ':
                out += ' '
                where.append(None)
        for n, ch in enumerate(t):
            out += ch
            where.append((k, n))
    return out, where


def _style(rows):
    """(font name, size, colour, font flags) most glyphs of the paragraph are drawn in."""
    count = {}
    for r in rows:
        for s in r['spans']:
            key = (s['font'], round(s['size'], 2), s['color'], s['flags'])
            count[key] = count.get(key, 0) + len(s['chars'])
    return max(count, key=count.get)


def _font(doc, page, name, text, approve_font):
    """(pymupdf.Font, how chosen): the paragraph's embedded font when it has every glyph, else a fallback."""
    for f in page.get_fonts(full=True):
        if f[3].split('+')[-1] == name.split('+')[-1]:
            try:
                ext = doc.extract_font(f[0])
                if ext[-1]:
                    font = pymupdf.Font(fontbuffer=ext[-1])
                    if all(font.has_glyph(ord(ch)) for ch in set(text) if ch.strip()):
                        return font, f'embedded ({name})'
            except Exception:
                pass
    fallback = pymupdf.Font('cjk') if any(is_cjk(ch) for ch in text) else pymupdf.Font('helv')
    if not approve_font:
        raise Refused(f'the paragraph\'s font ({name}) lacks glyphs of the new text: a fallback font ({fallback.name}) needs approval')
    return fallback, f'fallback ({fallback.name}) for {name}'


def _wrap(text, font, size, first_width, width):
    """Lines of ``text`` within the widths (Latin at spaces, Japanese anywhere but before/after the line-start rules)."""
    lines, cur = [], ''
    pieces = re.findall(r'[A-Za-z0-9\-\./:@_%#,]+|\s+|.', text)
    for p in pieces:
        limit = first_width if not lines else width
        trial = cur + p
        if font.text_length(trial.rstrip(), size) <= limit + .01 or not cur.strip():
            cur = trial
            continue
        if p.isspace():
            lines.append(cur.rstrip())
            cur = ''
            continue
        if p[0] in NO_START and cur:
            # Keep a closing mark on its line, bring the last character down instead.
            lines.append(cur[:-1].rstrip())
            cur = cur[-1] + p
            continue
        if cur and cur[-1] in NO_END:
            lines.append(cur[:-1].rstrip())
            cur = cur[-1] + p
            continue
        lines.append(cur.rstrip())
        cur = p.lstrip()
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def _occupied(page, rect, skip, holders=()):
    """What is drawn in ``rect`` (page) besides the paragraph's own glyphs (``skip``: their bboxes) and the rectangles
    holding it (``holders``: a panel or box, checked apart)."""
    hits = []
    for b in page.get_text('rawdict')['blocks']:
        if b.get('type') == 1 and pymupdf.Rect(b['bbox']).intersects(rect):
            hits.append(('image', b['bbox']))
        for l in b.get('lines', []):
            for s in l['spans']:
                for c in s['chars']:
                    r = pymupdf.Rect(c['bbox'])
                    if c['c'].strip() and r.intersects(rect) and not any(abs(r.x0 - q.x0) < .01 and abs(r.y0 - q.y0) < .01 for q in skip):
                        inter = r & rect
                        if inter.width > .2 and inter.height > .2:
                            hits.append(('text', list(r)))
    for x in page.get_drawings():
        r = x['rect']
        inter = r & rect
        if not inter.is_empty and inter.width > .2 and inter.height > .2 and not r.contains(rect) and not any(r.contains(h) for h in holders):
            hits.append(('drawing', list(r)))
    for a in page.annots():
        if a.rect.intersects(rect):
            hits.append(('annotation', list(a.rect)))
    for link in page.get_links():
        if pymupdf.Rect(link['from']).intersects(rect):
            hits.append(('link', list(link['from'])))
    return hits


def edit(source, output, page_number, at, old, new, approve_growth=False, approve_font=False):
    start = time.perf_counter()
    doc = pymupdf.open(source)
    try:
        page = doc[page_number - 1]
        rows, _ = _paragraph(page, at)
        text, where = _text(rows)
        # The occurrence holding the clicked glyph.
        point = pymupdf.Point(at)
        clicked = None
        for i, w in enumerate(where):
            if w is not None and (pymupdf.Rect(rows[w[0]]['chars'][w[1]]['bbox']) + (-.5, -.5, .5, .5)).contains(point):
                clicked = i
        # The selection as the user sees it: whitespace the extraction reads in a positional gap is not typed.
        bare = [(i, ch) for i, ch in enumerate(text) if not ch.isspace()]
        flat = ''.join(ch for _, ch in bare)
        want = ''.join(old.split())
        spans = [(bare[m.start()][0], bare[m.start() + len(want) - 1][0] + 1) for m in re.finditer(re.escape(want), flat)] if want else []
        if not spans:
            raise Refused('the selected text is not in the clicked paragraph')
        a, b = min(spans, key=lambda s: 0 if clicked is not None and s[0] <= clicked < s[1] else 1 + abs(s[0] - (clicked or 0)))
        at_char = a
        expected = text[:a] + new + text[b:]
        name, size, colour, flags = _style(rows)
        font, how = _font(doc, page, name, expected, approve_font)
        left = min(r['bbox'].x0 for r in rows)
        first_x = rows[0]['chars'][0]['origin'][0]
        right = max(r['bbox'].x1 for r in rows)
        pitch = (rows[-1]['origin_y'] - rows[0]['origin_y']) / (len(rows) - 1) if len(rows) > 1 else 1.5 * size
        # (The width a line may take: the paragraph's own, at least the new text's widest word.)
        lines = _wrap(expected, font, size, right - first_x, right - left)
        baselines = [rows[0]['origin_y'] + k * pitch for k in range(len(lines))]
        old_rect = pymupdf.Rect(rows[0]['bbox'])
        for r in rows:
            old_rect |= r['bbox']
        # (Growth in lines: the new paragraph's last baseline against the old one's.)
        new_rect = pymupdf.Rect(left, old_rect.y0, right, max(old_rect.y1, old_rect.y1 + baselines[-1] - rows[-1]['origin_y']))
        grows = len(lines) > len(rows)
        own = [pymupdf.Rect(c['bbox']) for r in rows for c in r['chars']]
        approvals = []
        if grows:
            band = pymupdf.Rect(left, old_rect.y1, right, new_rect.y1)
            hits = _occupied(page, band, own, [old_rect])
            if hits:
                raise Refused(f'the paragraph would grow over {hits[0][0]} at {[round(v, 1) for v in hits[0][1]]} ({len(lines)} lines for '
                              f'{len(rows)}): moving what follows is not done by this PoC')
            # Still inside what held it (a panel, a box).
            for x in page.get_drawings():
                r = x['rect']
                if r.contains(old_rect) and not r.contains(new_rect) and (x.get('fill') is not None or x.get('color') is not None) and r.width < page.rect.width * .95:
                    raise Refused(f'the paragraph would leave the box or panel at {[round(v, 1) for v in r]} ({len(lines)} lines for {len(rows)})')
            if not approve_growth:
                raise Refused(f'the paragraph grows down by {new_rect.y1 - old_rect.y1:.1f} pt into empty space: needs approval')
            approvals.append(f'grows down by {new_rect.y1 - old_rect.y1:.1f} pt into empty space')
        if how.startswith('fallback'):
            approvals.append(how)
        # Remove the paragraph's glyphs (text only: drawings and images stay).
        for r in own:
            page.add_redact_annot(r + (.05, .05, -.05, -.05), fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        tw = pymupdf.TextWriter(page.rect)
        for k, (line, y) in enumerate(zip(lines, baselines)):
            tw.append((first_x if k == 0 else left, y), line, font=font, fontsize=size)
        rgb = tuple(((colour >> s) & 255) / 255 for s in (16, 8, 0))
        tw.write_text(page, color=rgb)
        doc.save(output, garbage=1, deflate=True)
        return dict(outcome='saved', paragraph=text, expected=expected, replacement=new, at=at_char, lines=lines,
                    baselines=[round(y, 3) for y in baselines], frame=[round(v, 3) for v in new_rect], old_area=[round(v, 3) for v in old_rect],
                    font=how, size=size, approvals=approvals, seconds=round(time.perf_counter() - start, 2))
    except Refused as exc:
        return dict(outcome='refused', reason=str(exc), seconds=round(time.perf_counter() - start, 2))
    finally:
        doc.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('source')
    p.add_argument('output')
    p.add_argument('--page', type=int, required=True)
    p.add_argument('--at', required=True, help='click point X,Y (page, y down)')
    p.add_argument('--old', required=True)
    p.add_argument('--new', required=True)
    p.add_argument('--approve-growth', action='store_true')
    p.add_argument('--approve-font', action='store_true')
    a = p.parse_args()
    print(json.dumps(edit(a.source, a.output, a.page, [float(v) for v in a.at.split(',')], a.old, a.new, a.approve_growth, a.approve_font),
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
