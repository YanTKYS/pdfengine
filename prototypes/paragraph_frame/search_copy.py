"""Search and copy of an edited paragraph's new text, measured apart (issue #114).

A replacement broken over lines cannot be found by one search (PDF readers
search within a line), and a check made on the whole page's text without
spaces finds a string wherever it is. Here each property is measured on its
own, tied to the edited paragraph's drawn lines, and never counted when it
was not measured:

* ``search_full``: MuPDF's native search (``search_for``) finds the whole
  replacement, as written (spaces kept), covering it on every edited line it
  is drawn on (MuPDF reads a line break as a space: a Latin replacement over
  lines can be found as one; CJK text over lines is not). A hit elsewhere on
  the page is reported (``search_full_hits.elsewhere``), never counted.
* ``search_per_line``: when the replacement is drawn over several lines,
  each line's part of it (as the expected text splits it at the drawn line
  breaks) is found by search on that line, covering the replacement's glyphs
  there, the lines consecutive and the parts continuous (the first part runs
  to its line's end, the next starts at its line's start). Not the same
  measure as ``search_full``; ``None`` when the replacement is on one line.
* ``extraction_mupdf`` / ``extraction_poppler``: the text the two
  extractors give for the paragraph's lines is the expected paragraph, line
  breaks read as the paragraph joins them (no break, or the word space the
  paragraph has there); nothing else is normalised. A difference only in
  whitespace is reported apart (``whitespace_only``), not passed.
* ``copy_equivalent``: what both extractors give for the area where the
  replacement is drawn (its glyphs on each line), joined as above, is the
  replacement. This is copying reconstructed by PDF extraction, not a
  clipboard; ``clipboard_manual`` stays ``None`` unless done by hand.

Every failure says why. A part that fails because the source page draws the
same text the same way (a positional gap read as a space by the extractor,
not made by the edit) is marked ``as_in_source``; it is still a failure.
``as_in_source`` is ``True`` only when each difference corresponds to one of
the source's: the same kind and text, between the same glyphs drawn at the
same places. A failure of the same category elsewhere is not that; it is
``None`` (``as_in_source_note``: 由来未確認), never counted as the source's.
"""
import re
import subprocess
from collections import Counter
from difflib import SequenceMatcher

import pymupdf

PAD = 1.0
UNCONFIRMED = '由来未確認'


def _chars(page, frame, baselines):
    """The drawn characters inside ``frame`` (page space) on the given baselines: per line, in x order."""
    rows = [[] for _ in baselines]
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                for c in s['chars']:
                    x, y = c['origin']
                    if not (frame.x0 - PAD <= x <= frame.x1 + PAD and frame.y0 - PAD <= y <= frame.y1 + PAD):
                        continue
                    k = min(range(len(baselines)), key=lambda k: abs(baselines[k] - y))
                    if abs(baselines[k] - y) <= 1.5:
                        rows[k].append(dict(c=c['c'], bbox=pymupdf.Rect(c['bbox']), x=x, y=y))
    for r in rows:
        r.sort(key=lambda c: c['x'])
    return rows


def align(expected, rows):
    """(expected index -> (line, column) of its drawn character, the join of each line break, or a refusal).

    Characters must match one to one in order; a drawn space the expected text lacks (a positional gap) and an
    expected space no glyph draws (a line join) are skipped. ``joins[k]`` is ' ' when the expected text has
    whitespace where line k ends and line k + 1 starts (drawn or not), else ''."""
    drawn = [(k, n, c['c']) for k, r in enumerate(rows) for n, c in enumerate(r)]
    where, joins = {}, [''] * max(0, len(rows) - 1)
    i = j = 0
    while i < len(expected) and j < len(drawn):
        k, n, ch = drawn[j]
        if expected[i] == ch:
            where[i] = (k, n)
            i += 1
            j += 1
        elif ch.isspace():
            j += 1
        elif expected[i].isspace():
            if n == 0 and k > 0:
                joins[k - 1] = ' '
            i += 1
        else:
            return None, None, f'drawn {ch!r} where {expected[i]!r} is expected (at {i}: {expected[max(0, i - 6):i + 6]!r})'
    rest = expected[i:].strip()
    if rest:
        return None, None, f'not drawn: {rest[:20]!r}'
    # Characters drawn after the expected text ends (only spaces may follow it).
    extra = ''.join(ch for _, _, ch in drawn[j:])
    if extra.strip():
        k, n, _ = next(d for d in drawn[j:] if not d[2].isspace())
        return None, None, f'drawn but not expected: {extra.strip()[:20]!r} (line {k}, after {expected[max(0, len(expected) - 6):]!r})'
    # Whitespace between a line's last character and the next line's first: the paragraph's join there.
    ends = {}
    for e, (k, n) in where.items():
        if expected[e].isspace():
            continue
        lo, hi = ends.get(k, (e, e))
        ends[k] = (min(lo, e), max(hi, e))
    for k in range(len(joins)):
        if k in ends and k + 1 in ends and ends[k + 1][0] > ends[k][1] + 1 and not expected[ends[k][1] + 1:ends[k + 1][0]].strip():
            joins[k] = ' '
    return where, joins, None


def _poppler(path, page, rect, pad=PAD):
    """pdftotext of the line area ``rect`` (page space; its crop is set at 720 dpi: to 0.1 pt). pdftotext keeps
    a glyph whose middle is in the crop: the crop takes the line's full height."""
    r = pymupdf.Rect(rect) + (-pad, -PAD, pad, PAD)
    run = subprocess.run(['pdftotext', '-r', '720', '-f', str(page), '-l', str(page), '-x', str(int(r.x0 * 10)), '-y', str(int(r.y0 * 10)),
                          '-W', str(int(r.width * 10 + 1)), '-H', str(int(r.height * 10 + 1)), '-enc', 'UTF-8', str(path), '-'],
                         capture_output=True)
    return run.stdout.decode() if run.returncode == 0 else None


def _mupdf(page, rect, baseline):
    """MuPDF's text of one line: its characters on ``baseline`` whose middle lies in ``rect``, in MuPDF's order."""
    out = []
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                for c in s['chars']:
                    mid = (c['bbox'][0] + c['bbox'][2]) / 2
                    if abs(c['origin'][1] - baseline) <= 1.5 and rect.x0 <= mid <= rect.x1:
                        out.append(c['c'])
    return ''.join(out)


def _read(path, page, rows, joins, read):
    """A paragraph's lines (``rows``, see _chars) read one by one by ``read(line box, baseline)``, joined as the paragraph joins them."""
    order = [k for k in range(len(rows)) if rows[k]]
    return _joined([_line(read(_union([c['bbox'] for c in rows[k]]), sum(c['y'] for c in rows[k]) / len(rows[k]))) for k in order], joins)


def _line(raw):
    """One line's extracted text: its line break and page break removed, nothing else."""
    return None if raw is None else re.sub('[\n\x0c]', '', raw)


def _joined(texts, joins):
    """Lines' extracted texts joined as the paragraph joins them: at each line break, the spaces around it read as
    the paragraph's own whitespace there (one space) or as nothing. No other whitespace is touched."""
    if any(t is None for t in texts):
        return None
    out = ''
    for k, t in enumerate(texts):
        if k:
            out = out.rstrip(' ') + joins[k - 1]
            t = t.lstrip(' ')
        out += t
    return out


def _compare(got, want):
    """``got`` against ``want``; the paragraph's leading and trailing whitespace (no ink) is not compared."""
    if got is None:
        return dict(ok=None, reason='extractor unavailable')
    got, want = got.strip(), want.strip()
    if got == want:
        return dict(ok=True)
    at = next((k for k, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
    return dict(ok=False, whitespace_only=''.join(got.split()) == ''.join(want.split()),
                got=got[max(0, at - 8):at + 12], want=want[max(0, at - 8):at + 12], at=at)


def _glyph(c):
    """A drawn character and where it is drawn (a rawdict char, or a _chars row entry)."""
    x, y = c['origin'] if 'origin' in c else (c['x'], c['y'])
    return c['c'], round(x, 2), round(y, 2)


def _differences(got, expected, where, rows):
    """The differences of extracted ``got`` from ``expected`` (both stripped, as _compare does): per difference, its
    kind, the extracted and the expected text, and the drawn glyphs (see _glyph) of the nearest expected
    non-whitespace character before and after it (None at the paragraph's ends)."""
    if got is None:
        return None
    g, w, lead = got.strip(), expected.strip(), len(expected) - len(expected.lstrip())
    def glyph(j):
        if j is None or j + lead not in where:
            return None
        k, n = where[j + lead]
        return _glyph(rows[k][n])
    out = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, g, w, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        before = next((j for j in range(j1 - 1, -1, -1) if not w[j].isspace()), None)
        after = next((j for j in range(j2, len(w)) if not w[j].isspace()), None)
        out.append((tag, g[i1:i2], w[j1:j2], glyph(before), glyph(after)))
    return out


def _union(rects):
    out = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        out |= r
    return out


def measure(output, page_number, expected, replacement, at, frame, baselines, source=None, before=None):
    """The search and copy measures of ``replacement``, drawn at ``expected[at:]`` in the paragraph whose lines
    are drawn on ``baselines`` inside ``frame`` (page space) of ``output``. ``source``: the PDF before the edit,
    ``before``: its paragraph there (dict(text, frame, baselines)): a failure there too is marked
    ``as_in_source`` (still a failure)."""
    out = dict(search_full=None, search_per_line=None, extraction_mupdf=None, extraction_poppler=None, copy_equivalent=None,
               clipboard_manual=None)
    frame = pymupdf.Rect(frame)
    with pymupdf.open(output) as d:
        page = d[page_number - 1]
        rows = _chars(page, frame, baselines)
        where, joins, why = align(expected, rows)
        if why:
            out.update(search_full=False, search_per_line=False, extraction_mupdf=False, extraction_poppler=False, copy_equivalent=False,
                       reason='the drawn lines are not the expected paragraph: ' + why)
            return out
        span = [where[i] for i in range(at, at + len(replacement)) if i in where]
        if not replacement.strip() or not span:
            out['reason'] = 'no new text to search for (a deletion)'
            return out
        lines = sorted({k for k, _ in span})
        # A space the extractors read inside the replacement that the expected text lacks: a positional gap
        # between two glyphs. ``from_source``: both glyphs stand where the source page drew them (not the edit's).
        gaps = []
        held = None
        if source is not None:
            with pymupdf.open(source) as s:
                held = {_glyph(c) for b in s[page_number - 1].get_text('rawdict')['blocks']
                        for l in b.get('lines', []) for sp in l['spans'] for c in sp['chars']}
        for k in lines:
            cols = sorted(n for kk, n in span if kk == k)
            for a, b in zip(cols, cols[1:]):
                between = rows[k][a + 1:b]
                if between and all(c['c'].isspace() for c in between):
                    pair = (rows[k][a], rows[k][b])
                    # (The two glyphs and the drawn whitespace between them, each at the same place in the source.)
                    gaps.append(dict(line=k, between=pair[0]['c'] + pair[1]['c'],
                                     from_source=None if held is None else all(_glyph(c) in held for c in (pair[0], *between, pair[1]))))
        out['gaps'] = gaps
        boxes = {k: _union([rows[k][n]['bbox'] for kk, n in span if kk == k]) for k in lines}
        on_line = lambda hit, k: abs((hit.y0 + hit.y1) / 2 - (boxes[k].y0 + boxes[k].y1) / 2) < boxes[k].height / 2
        covers = lambda hit, k: on_line(hit, k) and hit.x0 <= boxes[k].x0 + 1 and hit.x1 >= boxes[k].x1 - 1
        # --- search_full: the whole replacement, as written, on the paragraph's lines.
        # (MuPDF's search reads a line break as a space: a Latin replacement over lines may be found as one,
        # its hit a box on each line. Found when every line's part of it is covered.)
        hits = page.search_for(replacement)
        mine = [h for h in hits if any(covers(h, k) for k in lines)]
        out['search_full'] = all(any(covers(h, k) for h in hits) for k in lines)
        out['search_full_hits'] = dict(total=len(hits), on_target=len(mine), elsewhere=len(hits) - len(mine), lines=len(lines))
        # --- search_per_line: each line's part, found on its line, consecutive and continuous.
        if len(lines) > 1:
            parts, ok, why = [], True, []
            if lines != list(range(lines[0], lines[-1] + 1)):
                ok = False
                why.append('the replacement is drawn on lines that are not consecutive')
            for k in lines:
                idx = [i for i in range(at, at + len(replacement)) if i in where and where[i][0] == k]
                text = expected[idx[0]:idx[-1] + 1]
                found = [h for h in page.search_for(text) if covers(h, k)]
                first, last = min(n for kk, n in span if kk == k), max(n for kk, n in span if kk == k)
                ink = [n for n, c in enumerate(rows[k]) if c['c'].strip()]
                continuous = (k == lines[-1] or last >= ink[-1]) and (k == lines[0] or first <= ink[0])
                part = dict(line=k, text=text, found=bool(found), continuous=continuous)
                if not found:
                    drawn = ''.join(c['c'] for c in rows[k][first:last + 1])
                    if drawn != text:
                        part['drawn'] = drawn
                    if source is not None:
                        # The source's only when the part is drawn as written but for gaps, each the source's
                        # own (the same glyphs and whitespace at the same places); else not established.
                        mine = [g for g in gaps if g['line'] == k]
                        same = (drawn != text and ''.join(drawn.split()) == ''.join(text.split()) and bool(mine)
                                and all(g['from_source'] for g in mine))
                        part['as_in_source'] = True if same else None
                        if not same:
                            part['as_in_source_note'] = UNCONFIRMED
                parts.append(part)
                if not (found and continuous):
                    ok = False
            out['search_per_line'] = ok
            out['search_per_line_parts'] = parts
            if why:
                out['search_per_line_reason'] = '; '.join(why)
        # --- extraction: the paragraph's lines, each read on its own, joined as the paragraph joins them.
        mupdf_read = lambda pg: (lambda box, y: _mupdf(pg, box, y))
        poppler_read = lambda path: (lambda box, y: _poppler(path, page_number, box))
        out['extraction_mupdf_detail'] = _compare(_read(output, page, rows, joins, mupdf_read(page)), expected)
        out['extraction_poppler_detail'] = _compare(_read(output, page, rows, joins, poppler_read(output)), expected)
        if before is not None and source is not None:
            # The source's own paragraph read the same way: a difference it shows too is the source's.
            # Each difference of the output (its kind and text, and the drawn glyphs either side of it, where they
            # are drawn) must be one the source shows too; a failure of the same category is not enough.
            with pymupdf.open(source) as s:
                sp = s[page_number - 1]
                srows = _chars(sp, pymupdf.Rect(before['frame']), before['baselines'])
                swhere, sjoins, swhy = align(before['text'], srows)
                for name, read, own in (('mupdf', mupdf_read(sp), mupdf_read(page)), ('poppler', poppler_read(source), poppler_read(output))):
                    detail = out[f'extraction_{name}_detail']
                    if detail['ok'] is not False:
                        continue
                    if swhy:
                        detail.update(as_in_source=None, as_in_source_note=f'{UNCONFIRMED}: the source paragraph is not drawn as its text ({swhy})')
                        continue
                    mine = _differences(_read(output, page, rows, joins, own), expected, where, rows)
                    theirs = _differences(_read(source, sp, srows, sjoins, read), before['text'], swhere, srows)
                    left = Counter(theirs or [])
                    left.subtract(mine or [])
                    same = bool(mine) and all(a or b for *_, a, b in mine) and min(left.values(), default=0) >= 0
                    detail['as_in_source'] = True if same else None
                    if not same:
                        detail['as_in_source_note'] = UNCONFIRMED
        out['extraction_mupdf'] = out['extraction_mupdf_detail']['ok']
        out['extraction_poppler'] = out['extraction_poppler_detail']['ok']
        # --- copy_equivalent: the replacement's own glyphs on each line, as both extractors read them.
        def piece(read):
            texts = [_line(read(boxes[k], sum(rows[k][n]['y'] for kk, n in span if kk == k) / sum(1 for kk, _ in span if kk == k))) for k in lines]
            return _joined(texts, [joins[k] for k in lines[:-1]])
        mu = _compare(piece(mupdf_read(page)), replacement)
        # (The crop stays inside the replacement's glyph boxes: a neighbour's middle never enters it.)
        po = _compare(piece(lambda box, y: _poppler(output, page_number, box, -1.)), replacement)
        out['copy_equivalent_detail'] = dict(mupdf=mu, poppler=po)
        out['copy_equivalent'] = None if mu['ok'] is None or po['ok'] is None else (mu['ok'] and po['ok'])
    return out


def target(proposal):
    """(expected paragraph, replacement, its index there, frame, line baselines) of a saved edit's proposal."""
    para, expected = proposal['paragraphs'][0], proposal['expected']
    old, new = proposal['selection'], proposal['replacement']
    found = [at for at in range(len(para) - len(old) + 1)
             if para[at:at + len(old)] == old and expected == para[:at] + new + para[at + len(old):]]
    if not found:
        raise ValueError('the proposal does not say where its replacement is')
    return expected, new, found[0], proposal['evidence']['inferred']['frame'], [l['baseline'] for l in proposal['lines']]


def before(proposal):
    """The edited paragraph as the source draws it: dict(text, frame, baselines) (measure's ``before``)."""
    lines = proposal['evidence']['observed']['lines'][:proposal['source_lines']]
    box = _union([pymupdf.Rect(l['bbox']) for l in lines])
    return dict(text=proposal['paragraphs'][0], frame=list(box + (-1, -1, 1, 1)), baselines=[l['baseline'] for l in lines])


# What "the edited text's search and extraction are verified" requires (issue #116, option (b) of issue #114):
# on one line, the whole replacement found by one search where it is drawn; over lines, each line's part found on
# its line (a single search over the whole replacement is then recorded, not required: it is not guaranteed).
# Both extractions and the copy equivalent always. The real clipboard is never assumed.
REQUIRED = {'one line': ('search_full', 'extraction_mupdf', 'extraction_poppler', 'copy_equivalent'),
            'lines': ('search_per_line', 'extraction_mupdf', 'extraction_poppler', 'copy_equivalent')}


def verified(m):
    """Whether the measures ``m`` (see measure) verify the edited text's search and extraction.

    dict(result True | False | None | 'not applicable', route, required, failed, missing, search_full, note).
    Any required measure false: False (a gap the source has too is no excuse: ``as_in_source`` is not read here);
    any null or the measures not made: None (unverified); a deletion has no new text to search: not applicable."""
    if not isinstance(m, dict) or 'error' in m:
        return dict(result=None, route=None, required=[], failed=[], missing=['search_copy'],
                    note='the measures were not made' + (f": {m['error']}" if isinstance(m, dict) else ''))
    if str(m.get('reason', '')).startswith('no new text'):
        return dict(result='not applicable', route='deletion', required=[], failed=[], missing=[], note=m['reason'])
    lines = (m.get('search_full_hits') or {}).get('lines')
    if lines is None:
        # The drawn lines are not the expected paragraph (measure refused to align them): every measure is false.
        req = REQUIRED['one line'] + ('search_per_line',)
        return dict(result=False if any(m.get(k) is False for k in req) else None, route=None, required=list(req),
                    failed=[k for k in req if m.get(k) is False], missing=[k for k in req if m.get(k) is None], note=m.get('reason'))
    route = 'one line' if lines == 1 else 'lines'
    req = REQUIRED[route]
    failed = [k for k in req if m.get(k) is False]
    missing = [k for k in req if m.get(k) is None]
    result = False if failed else None if missing else True
    note = None
    if result is True and route == 'lines' and m.get('search_full') is not True:
        note = ('verified line by line: each line\'s part is found on its line; one search for the whole replacement is not '
                'found (search_full false), and is not guaranteed')
    return dict(result=result, route=route, required=list(req), failed=failed, missing=missing, search_full=m.get('search_full'),
                note=note)


def search_on_target(m):
    """The existing search check (approved_audit ``search_mupdf``) measured on the edited lines: (value, why).

    One line: whether one search finds the replacement where it is drawn (a hit elsewhere on the page does not
    count). Over lines: True when one search covers every line's part, else None (a single search is not
    required over lines: see verified). Drawn lines that are not the expected paragraph: False. No measure: None."""
    if not isinstance(m, dict) or 'error' in m:
        return None, 'the measures were not made'
    hits = m.get('search_full_hits')
    if hits is None:
        return False, m.get('reason') or 'not measured on the edited lines'
    if hits['lines'] == 1:
        if m['search_full']:
            return True, 'found where it is drawn'
        return False, ('found elsewhere on the page only' if hits['elsewhere'] else 'not found where it is drawn (one line)')
    if m['search_full']:
        return True, f"found by one search over its {hits['lines']} lines"
    return None, f"drawn over {hits['lines']} lines: one search does not find it (see the per-line search)"
