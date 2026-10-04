"""Canonical owned-island body for one semantic slot (pure; no PDF access).

Input authority is only the exact semantic plan, the confirmed fill, the
slot font alias/codebook and the page top. Exact rationals flow one way into a
fixed decimal spelling (at most six places, ties to even, no exponent, no
negative zero); serialized decimals never become semantic input. Positions
carry Tw and confirmed edges; Tc carries tracking; Ts carries rise.
"""
from fractions import Fraction as F

from .backend import PdfError
from .content_stream import operators


def decimal(value):
    n = F(value)
    if abs(n) > 10**6:
        raise PdfError('canonical island value is outside the output range')
    units = round(n * 10**6)
    whole, tail = divmod(abs(units), 10**6)
    sign = '-' if units < 0 else ''
    return sign + str(whole) + ('.' + f'{tail:06d}'.rstrip('0') if tail else '')


def _name(alias):
    if not isinstance(alias, str) or not alias.startswith('/') or not alias[1:].isalnum():
        raise PdfError('canonical island needs a plain font alias')
    return alias


def body(style, emitted, *, alias, codes, fill, page_top, origin):
    """Canonical `q BT … ET Q` body and anchors (glyph:n / slot)."""
    op, values = fill
    if op not in ('g', 'rg', 'k') or len(values) != {'g': 1, 'rg': 3, 'k': 4}[op]:
        raise PdfError('canonical island needs a device gray/RGB/CMYK fill')
    size, scale = F(style['font_size']), F(style['horizontal_scale'])
    rise, tracking = F(style['rise']), F(style['tracking'])
    top = F(page_top)
    head = (f"q BT {_name(alias)} {decimal(size)} Tf {decimal(scale * 100)} Tz {decimal(tracking / scale)} Tc "
            f"0 Tw {decimal(-rise)} Ts {' '.join(decimal(F(str(v))) for v in values)} {op}\n")
    data, anchors = head, {}
    if emitted:
        for n, glyph in enumerate(emitted):
            x, y = (F(v) for v in glyph['origin'])
            line = f"1 0 0 1 {decimal(x)} {decimal(top - (y - rise))} Tm "
            anchors[f'glyph:{n}'] = len(data) + len(line)
            data += line + f"<{codes[glyph['text']]:04X}> Tj\n"
    else:
        x, baseline = (F(v) for v in origin)
        line = f"1 0 0 1 {decimal(x)} {decimal(top - baseline)} Tm "
        anchors['slot'] = len(data) + len(line)
        data += line + '[] TJ\n'
    data += 'ET Q\n'
    return data.encode('ascii'), anchors


def island_codes(body_bytes, events):
    """Alias and char→code map actually used by an island (for exact re-serialization)."""
    tf = [op for op in operators(body_bytes) if op.name == 'Tf']
    if len(tf) != 1:
        raise PdfError('canonical island has exactly one font selection')
    codes = {}
    for event in events:
        for char in event.chars:
            code = int.from_bytes(char.code, 'big')
            if codes.setdefault(char.text, code) != code:
                raise PdfError('one character uses two codes in the island')
    return str(tf[0].args[0]), codes
