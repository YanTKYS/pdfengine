"""Provider font for tests that pass a new-glyph font to the engine by path.

The engine opens a path provider anew for each plan, edit and reopen, and
``ShapedFont`` parses, re-serializes and maps the whole program each time.
PyMuPDF's built-in CJK fallback has 50,483 glyphs (3.5 MB), so lifecycle tests
spent most of their time re-reading it. Those tests check layout, ownership and
persistence, not large-font handling. They get a deterministic subset of the
same font: every retained glyph keeps its advance and outline, and the font
keeps its metrics, names and embedding flags (``test_provider_font.py``).

Covered: Latin, punctuation and symbols, kana, CJK symbols, full-width forms,
and every other character written in a test module. A character outside that
set is missing from the subset, as it would be from a real font. Tests of the
full font itself keep ``pymupdf.Font('cjk')``.
"""
from functools import cache
from io import BytesIO
from pathlib import Path

import pymupdf
from fontTools import subset
from fontTools.ttLib import TTFont

BLOCKS = (range(0x20, 0x250), range(0x2000, 0x2C00), range(0x3000, 0x3100), range(0xFF00, 0xFFF0))


def covered():
    written = {ord(c) for p in Path(__file__).parent.glob('*.py') for c in p.read_text(encoding='utf-8') if ord(c) > 0x7E}
    return sorted(written.union(*BLOCKS))


@cache
def cjk_provider_bytes():
    font = TTFont(BytesIO(pymupdf.Font('cjk').buffer), recalcTimestamp=False)
    options = subset.Options()
    options.layout_features = ['*']
    options.name_IDs = ['*']
    options.name_languages = ['*']
    options.name_legacy = True
    options.legacy_kern = True
    options.hinting = True
    options.notdef_outline = True
    options.drop_tables = []
    options.recalc_bounds = False
    options.recalc_timestamp = False
    options.prune_unicode_ranges = False
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=covered())
    subsetter.subset(font)
    out = BytesIO()
    font.save(out)
    return out.getvalue()
