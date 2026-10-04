"""Provisional, PDF-independent logical advance experiment, NOT runtime layout.

All numbers are exact rational strings. This intentionally does not claim to
solve ink bounds, vertical metrics, contextual shaping or current-PDF binding.
No trace coordinate, provider, physical resource or historic revision is input.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction as F
import json
import sys


def semantic_style(style, font_identity):
    """A caller-proven font identity is required; a style ID/name is not proof."""
    # Normalize numeric *representations* only, never close-but-distinct values.
    numeric = lambda v: str(F(str(v))) if v is not None else None
    return dict(font=font_identity, **{k: numeric(style[k]) for k in
        ('font_size', 'horizontal_scale', 'tracking', 'baseline_shift')},
        fill=[style['fill'][0], [numeric(v) for v in style['fill'][1]]])


def cell(text, *, metric='600', upem='1000', size='12', scale='1',
         tc='0', tw='0', tz='100', font='fixture:Courier/WinAnsi/600'):
    if len(text) != 1 or not 32 <= ord(text) <= 126:
        raise ValueError('experiment supports single printable ASCII glyphs only')
    horizontal = F(scale) * F(tz) / 100
    return dict(text=text, font=font,
        base=str(F(metric) / F(upem) * F(size) * horizontal),
        tracking=str(F(tc) * horizontal),
        word=str(F(tw) * horizontal if text == ' ' else F(0)))


def edit(record, start, end, inserted):
    result = deepcopy(record)
    if not 0 <= start <= end <= len(result['cells']):
        raise ValueError('invalid edit interval')
    # No historical cells/coordinates survive a deletion.
    result['cells'][start:end] = deepcopy(inserted)
    return result


def plan(record):
    """Advance-only ASCII greedy wrap, deliberately separate from rich_layout.

    Tracking is an inter-glyph gap, suppressed at line end. Word space remains
    on an interior space; trailing spaces have logical cells but no paint.
    Manual TJ/Tm intent is refused until an edge survival policy is confirmed.
    """
    if record.get('alignment', 'left') != 'left':
        raise ValueError('NON_LEFT_OUT_OF_SCOPE')
    if record.get('positioning'):
        raise ValueError('SOURCE_POSITIONING_INTENT_UNRESOLVED')
    cells = record['cells']
    width, x, y, leading = map(F, (record['width'], record['x'], record['baseline'], record['leading']))
    if width <= 0 or leading <= 0:
        raise ValueError('invalid region')
    lines, glyphs = [], []
    cursor = 0
    while cursor < len(cells):
        choices = []
        for stop in range(cursor + 1, len(cells) + 1):
            end = stop
            while end > cursor and cells[end - 1]['text'] == ' ':
                end -= 1
            advances = [F(c['base']) + F(c['word']) +
                        (F(c['tracking']) if i + cursor + 1 < end else 0)
                        for i, c in enumerate(cells[cursor:end])]
            if any(a < 0 for a in advances):
                raise ValueError('negative advance')
            if sum(advances) <= width:
                legal = stop == len(cells) or cells[stop - 1]['text'] == ' '
                choices.append((legal, stop, end, advances))
        if not choices:
            raise ValueError('too narrow')
        legal = [c for c in choices if c[0]]
        _, stop, end, advances = (legal or choices)[-1]
        pen = x
        for offset, advance in zip(range(cursor, end), advances):
            c = cells[offset]
            glyphs.append(dict(start=offset, end=offset + 1, text=c['text'], font=c['font'],
                origin=[str(pen), str(y)], advance=str(advance)))
            pen += advance
        lines.append(dict(start=cursor, end=end, baseline=str(y), width=str(pen - x)))
        cursor = stop
        while cursor < len(cells) and cells[cursor]['text'] == ' ':
            cursor += 1
        y += leading
    return dict(lines=lines, glyphs=glyphs)


if __name__ == '__main__':
    print(json.dumps(plan(json.load(sys.stdin)), sort_keys=True, separators=(',', ':')))
