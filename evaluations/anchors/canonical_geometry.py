"""Pure rectangle formatter experiment, not a PDF/marker writer or verifier.

Inputs are a fixed creation recipe, this edit's planned rectangles and an exact
source-decimal CTM. No renderer, path IDs, previous output or PDF is accepted.
"""
from decimal import Decimal, InvalidOperation
from fractions import Fraction


SCALE = 1000000


def decimal(value):
    if isinstance(value, bool):
        raise ValueError('boolean geometry')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid decimal geometry') from exc
    if not result.is_finite() or abs(result) > Decimal('1000000000'):
        raise ValueError('nonfinite or excessive geometry')
    return result


def token(value):
    """One fixed 1e-6 unit grid; half-even, no exponent, locale or negative zero."""
    value = value if isinstance(value, Fraction) else Fraction(decimal(value))
    if abs(value) > 1000000000:
        raise ValueError('excessive geometry')
    scaled = abs(value) * SCALE
    units, remainder = divmod(scaled.numerator, scaled.denominator)
    if 2*remainder > scaled.denominator or (2*remainder == scaled.denominator and units % 2):
        units += 1
    if not units:
        return '0'
    integer, fraction = divmod(units, SCALE)
    text = str(integer) + ('.'+str(fraction).zfill(6).rstrip('0') if fraction else '')
    return ('-' if value < 0 else '') + text


def recipe(offset, thickness, fill_rule='nonzero'):
    offset, thickness = token(offset), token(thickness)
    if decimal(thickness) <= 0 or fill_rule not in ('nonzero', 'evenodd'):
        raise ValueError('unsupported underline recipe')
    return dict(offset=offset, thickness=thickness, fill_rule=fill_rule)


def format_body(style, planned, source_ctm, *, page_height):
    """planned rows: [left, right, baseline] in unrotated page coordinates.

    This experiment deliberately keeps actual planned float values (converted
    by their round-trip decimal spelling). It does NOT snap the layout to hide
    upstream instability. Local serialized vertices alone are quantized.
    Page frame is restricted to origin zero, UserUnit=1 and rotation zero.
    """
    a, b, c, d, e, f = map(Fraction, source_ctm)
    if b or c or a <= 0 or d <= 0:
        raise ValueError('only positive axis-aligned CTM supported')
    if not planned:
        raise ValueError('NARROW V1 has no dormant body')
    offset, thickness = (Fraction(decimal(style[k])) for k in ('offset', 'thickness'))
    if thickness <= 0 or style['fill_rule'] not in ('nonzero', 'evenodd'):
        raise ValueError('invalid recipe')
    height = Fraction(decimal(page_height))
    body, segments = ['q\n'], []
    for left, right, baseline in planned:
        left, right, baseline = (Fraction(decimal(x)) for x in (left, right, baseline))
        if right <= left:
            raise ValueError('nonpositive planned width')
        top, bottom = baseline + offset, baseline + offset + thickness
        page = ((left, top), (right, top), (right, bottom), (left, bottom))
        vertices = [(token((x-e)/a), token((height-y-f)/d)) for x, y in page]
        if vertices[0][0] == vertices[1][0] or vertices[0][1] == vertices[3][1]:
            raise ValueError('quantization collapsed a rectangle')
        segments.append(vertices)
        for index, (x, y) in enumerate(vertices):
            body.append(f'{x} {y} {"m" if index == 0 else "l"}\n')
        body.append('h\n' + ('f*' if style['fill_rule'] == 'evenodd' else 'f') + '\n')
    body.append('Q\n')
    return ''.join(body).encode('ascii'), segments
