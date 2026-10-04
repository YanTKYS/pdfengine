"""Exact current-state measurement for the narrow semantic layout scope.

One provider-independent rule: font-unit metrics from the confirmed static
TrueType asset, exact rational page-space style, left alignment, explicit
newlines, confirmed adjacent positioning edges. No renderer observation, trace
bbox or float32 matrix is an input. Pure: no PDF reading or writing.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction as F

import fontTools
import uharfbuzz

from .backend import PdfError

CHARACTERS = frozenset('AB \n')
MAX_TEXT = 256
STYLE_KEYS = frozenset({'font_size', 'horizontal_scale', 'rise', 'tracking', 'word_spacing', 'spacing_intent'})
EDGE_KEYS = frozenset({'left', 'right', 'delta', 'delta_y', 'operator', 'semantics', 'boundary_policy'})


def font_policy():
    """Pinned narrow font/shaping policy; a library change refuses old records."""
    return dict(collection_index=0, variations={}, features={'kern': False},
                shaping='one-ASCII-codepoint/nominal-hmtx', shaping_version=uharfbuzz.version_string(),
                outline='simple-unhinted-TT/recordingPen-v1', outline_library_version=fontTools.version)


def rational(value):
    if not isinstance(value, str):
        raise PdfError('semantic numbers must be exact rational strings')
    try:
        n = F(value)
    except (ValueError, ZeroDivisionError):
        raise PdfError('semantic number is not an exact rational') from None
    if abs(n) > 10**9:
        raise PdfError('semantic numeric scope exceeded')
    return n


def require_static_tt(font):
    """Static, unhinted, simple-glyph TrueType only."""
    raw = font.font
    if 'glyf' not in raw or 'fvar' in raw or any(t in raw for t in ('fpgm', 'prep', 'cvt ')) or font.variations:
        raise PdfError('semantic layout needs a static unhinted TrueType asset')


def nominal_glyph(char, font):
    cmap = font.font.getBestCmap()
    if ord(char) not in cmap:
        raise PdfError('semantic font asset cannot map a current character')
    gid = font.font.getGlyphID(cmap[ord(char)])
    glyph = font.font['glyf'][font.order[gid]]
    if gid == 0 or glyph.isComposite() or (getattr(glyph, 'program', None) and glyph.program.getBytecode()):
        raise PdfError('semantic layout needs simple unhinted non-.notdef glyphs')
    ink = font.ink(gid)
    if ink is None and char != ' ':
        raise PdfError('visible semantic glyph has no outline')
    return dict(text=char, font_identity=font.source_sha256, glyph_identity=str(gid), upem=str(font.upem),
                width=str(font.nominal_width(gid)), ascent=str(font.font['hhea'].ascent),
                descent=str(-font.font['hhea'].descent), ink=list(map(str, ink)) if ink else None)


def measurement(glyph, style, *, terminal=False, edge='0'):
    """Single tuple (advance, offsets, ink, ascent, descent) for one current glyph."""
    upem, width, asc, desc = (rational(glyph[k]) for k in ('upem', 'width', 'ascent', 'descent'))
    size, scale, rise, tracking, word = (rational(style[k]) for k in
        ('font_size', 'horizontal_scale', 'rise', 'tracking', 'word_spacing'))
    if min(upem, width, size, scale) <= 0 or min(asc, desc) < 0 or asc + desc <= 0:
        raise PdfError('nonpositive semantic metric')
    sx, sy = size * scale / upem, size / upem
    ink = None
    if glyph['ink'] is not None:
        a, b, c, d = (rational(v) for v in glyph['ink'])
        ink = [a * sx, -d * sy + rise, c * sx, -b * sy + rise]
    advance = width * sx + (word if glyph['text'] == ' ' else 0) + (0 if terminal else tracking + rational(edge))
    if advance <= 0:
        raise PdfError('nonpositive semantic advance')
    return dict(text=glyph['text'], advance=advance, y_offset=rise, ink=ink,
                ascent=max(F(0), asc * sy - rise), descent=max(F(0), desc * sy + rise))


def validate_edges(text, edges):
    """Confirmed adjacent pairs only; nothing is inferred from geometry."""
    seen = set()
    for e in edges:
        if (not isinstance(e, dict) or set(e) - EDGE_KEYS or e.get('semantics') != 'confirmed-adjacent-pair'
                or e.get('boundary_policy') != 'suppress-at-line-end' or e.get('operator') not in ('TJ', 'Tm')):
            raise PdfError('positioning edge intent is unresolved')
        left, right = e.get('left'), e.get('right')
        if (type(left) is not int or type(right) is not int or not 0 <= left < right < len(text)
                or right != left + 1 or left in seen or '\n' in (text[left], text[right])
                or rational(e.get('delta_y', '0')) != 0):
            raise PdfError('positioning edge is not a confirmed same-line adjacent pair')
        rational(e['delta'])
        seen.add(left)


def edit_edges(edges, start, end, inserted):
    """Endpoint delete or insertion between endpoints drops an edge; earlier edits remap it."""
    shift, kept = inserted - (end - start), []
    for edge in edges:
        if start <= edge['right'] and end > edge['left'] or start == end == edge['right']:
            continue
        edge = deepcopy(edge)
        if edge['left'] >= end:
            edge['left'] += shift
            edge['right'] += shift
        kept.append(edge)
    return kept


def layout(text, style, glyph_metrics, region, empty, edges):
    """Exact left layout. Returns lines, emitted glyphs and omitted current offsets."""
    x, baseline, width, leading = (rational(region[k]) for k in ('x', 'baseline', 'width', 'leading'))
    if width <= 0 or leading <= 0:
        raise PdfError('nonpositive semantic region')
    asc_empty, desc_empty = rational(empty['ascent']), rational(empty['descent'])
    deltas = {e['left']: e['delta'] for e in edges}

    def measure(start, stop):
        if start == stop:
            return [], F(0), F(0), asc_empty, desc_empty
        tuples = [measurement(glyph_metrics[i], style, terminal=i == stop - 1, edge=deltas.get(i, '0'))
                  for i in range(start, stop)]
        pen = left = right = asc = desc = F(0)
        for t in tuples:
            asc, desc = max(asc, t['ascent']), max(desc, t['descent'])
            if t['ink'] is not None:
                a, b, c, d = t['ink']
                left, right = min(left, pen + a), max(right, pen + c)
                asc, desc = max(asc, -b), max(desc, d)
            pen += t['advance']
            right = max(right, pen)
        return tuples, right - left, -left, asc, desc

    lines, emitted = [], []

    def append(start, stop, metrics):
        nonlocal baseline
        tuples, span, inset, asc, desc = metrics
        if lines:
            baseline += max(leading, lines[-1]['descent'] + asc)
        pen = x + inset
        for i, t in enumerate(tuples, start):
            emitted.append(dict(offset=i, text=t['text'], origin=[str(pen), str(baseline + t['y_offset'])],
                                advance=str(t['advance'])))
            pen += t['advance']
        lines.append(dict(start=start, end=stop, width=span, baseline=baseline, ascent=asc, descent=desc))

    start = 0
    for stop in [i for i, c in enumerate(text) if c == '\n'] + [len(text)]:
        cursor = start
        if cursor == stop:
            append(cursor, stop, ([], F(0), F(0), asc_empty, desc_empty))
        while cursor < stop:
            choices = []
            for candidate in range(cursor + 1, stop + 1):
                trimmed = candidate
                while trimmed > cursor and text[trimmed - 1] == ' ':
                    trimmed -= 1
                metrics = measure(cursor, trimmed)
                if metrics[1] <= width:
                    choices.append((candidate == stop or text[candidate - 1] == ' ', candidate, trimmed, metrics))
            if not choices:
                raise PdfError('semantic layout region is too narrow')
            _, candidate, trimmed, metrics = ([c for c in choices if c[0]] or choices)[-1]
            append(cursor, trimmed, metrics)
            cursor = candidate
            while cursor < stop and text[cursor] == ' ':
                cursor += 1
        start = stop + 1
    painted = {g['offset'] for g in emitted}
    omitted = [dict(offset=i, kind='newline' if c == '\n' else 'trimmed-space', style='body')
               for i, c in enumerate(text) if i not in painted]
    if any(text[o['offset']] not in ' \n' for o in omitted):
        raise PdfError('semantic layout would omit a visible character')
    return dict(lines=[{k: str(v) if isinstance(v, F) else v for k, v in line.items()} for line in lines],
                emitted=emitted, omitted=omitted)
