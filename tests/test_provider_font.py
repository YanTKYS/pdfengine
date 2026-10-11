"""The subset test provider is the built-in CJK font for every covered character."""
import pymupdf

from pdfeditor.shaped_font import ShapedFont
from provider_font import cjk_provider_bytes, covered


def test_subset_provider_keeps_metrics_flags_and_every_covered_glyph():
    data = cjk_provider_bytes()
    assert data == cjk_provider_bytes.__wrapped__()
    full, small = ShapedFont(pymupdf.Font('cjk').buffer), ShapedFont(data)
    for name in ('upem', 'name', 'ascender', 'descender', 'no_subset'):
        assert getattr(small, name) == getattr(full, name), name
    for table, field in (('OS/2', 'fsType'), ('OS/2', 'usWinAscent'), ('OS/2', 'usWinDescent'),
                         ('OS/2', 'sTypoAscender'), ('OS/2', 'sTypoDescender'), ('OS/2', 'sTypoLineGap'),
                         ('hhea', 'lineGap'), ('head', 'unitsPerEm')):
        assert getattr(small.font[table], field) == getattr(full.font[table], field), (table, field)
    big, mapped = full.font.getBestCmap(), small.font.getBestCmap()
    wanted = [cp for cp in covered() if cp in big]
    assert len(wanted) > 1000 and set(mapped) == set(wanted)
    for cp in wanted:
        a, b = full.font.getGlyphID(big[cp]), small.font.getGlyphID(mapped[cp])
        assert (small.nominal_width(b), small.outline(b), small.ink(b)) == (
            full.nominal_width(a), full.outline(a), full.ink(a)), hex(cp)
    text = 'One two  三四 本文ABC※「テスト」、。①→ ＡＢ'
    assert [(g.advance, g.x_offset, g.y_offset, g.cluster) for g in small.shape(text).glyphs] == [
        (g.advance, g.x_offset, g.y_offset, g.cluster) for g in full.shape(text).glyphs]
