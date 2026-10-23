"""Text space to page space for the text-state quantities B2 reads and writes.

A glyph is painted through Tf size, Tz, Tc, Tw and Ts in text space, then the
text matrix and the CTM. Producers differ in where they put the scale:
Word and LibreOffice write ``12 Tf`` with an identity matrix, InDesign and
Quartz write ``1 Tf`` with ``Tm 12 0 0 12``, Ghostscript and Antenna House
also scale the CTM, and Quartz figures reflect both Tm and CTM.

When the combined linear map (Tm x CTM) is axis-aligned with positive
scales (sx, sy), the same glyphs are painted by size*sy and horizontal
scale tz*sx/sy under an identity matrix; character spacing, word spacing and
rise scale with sy. All B2 metrics, style identities and new drawing use
these page-space values, so a scale in the matrix and a scale in Tf are one
style. Rotation, shear and reflection that does not cancel are refused.
"""
from .engine import Refused

EPSILON = 1e-6


def linear(event):
    """The text-space to user-space linear part (a, b, c, d) of one text operator."""
    a, b, c, d = event.text_matrix[:4]
    A, B, C, D = event.state.ctm[:4]
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D)


def scales(event):
    """(sx, sy) of an axis-aligned, upright text transform, or a refusal."""
    a, b, c, d = linear(event)
    if abs(b) > EPSILON * max(abs(a), abs(d)) or abs(c) > EPSILON * max(abs(a), abs(d)):
        raise Refused('rotated or sheared text (text matrix x CTM) is not supported')
    if a <= 0 or d <= 0:
        raise Refused('mirrored text (text matrix x CTM) is not supported')
    return a, d


def effective(event):
    """Page-space size, horizontal scale, spacing and rise of the text operator."""
    sx, sy = scales(event)
    s = event.state
    return dict(size=s.size * sy, tz=s.tz * sx / sy, tc=s.tc * sy, tw=s.tw * sy, rise=s.ts * sy, sx=sx, sy=sy)


def size_of(event):
    """Page-space font size, or None when the transform is not supported."""
    try:
        return effective(event)['size']
    except Refused:
        return None


def inverse(matrix):
    """Inverse of an invertible PDF matrix (a, b, c, d, e, f)."""
    a, b, c, d, e, f = matrix
    det = a * d - b * c
    if abs(det) < 1e-12:
        raise Refused('singular CTM at the insertion point')
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    return (ia, ib, ic, id_, -(e * ia + f * ic), -(e * ib + f * id_))
