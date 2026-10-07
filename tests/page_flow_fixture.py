"""Word-like ordinary Japanese A4 page for the B1 page-proposal tests.

The font is generated with fontTools (no third-party binary): one simple
outline per character the fixture uses, 1000 units per em. The PDF embeds a
GID-preserving subset (tagged like a producer subset); the full font is the
installed-font candidate. Body text wraps at the A4 / 30 mm margin measure
(40 full-width characters at 10.5 pt), with a one-character first-line indent
and spacing after each paragraph.
"""
from io import BytesIO
from pathlib import Path

import pymupdf
from fontTools import subset as ft_subset
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from pdfeditor.layout import _safe_boundary

PAGE = (595.28, 841.89)
MARGIN = 85.04                       # 30 mm
SIZE = 10.5
PITCH = 18.0
SPACE_AFTER = 9.0
TITLE = '補助事業募集のお知らせ'
FOOTER = '－１－'
PARAGRAPHS = (
    '本年度の補助事業について、下記のとおり募集を行います。対象となる事業者におかれましては、募集要項の内容を確認の'
    'うえ、所定の期限までに手続を行ってください。',
    '申請書を提出してください。',
    'なお、提出期限は令和七年十月十五日とし、期限を過ぎたものは受け付けません。不明な点がありましたら、担当課まで'
    'お問い合わせください。',
)
FIRST_EDIT = ('申請書', '各種申請書')
SECOND_EDIT = ('提出してください。', '必要書類一式とあわせて、担当窓口へ持参または郵送により提出してください。')
FAMILY = 'PdfengineFixtureMincho'


def characters():
    text = TITLE + FOOTER + ''.join(PARAGRAPHS) + ''.join(FIRST_EDIT) + ''.join(SECOND_EDIT) + '再'
    return sorted(set(text))


def build_font(*, family=FAMILY, advance=1000, drop=(), salt=0):
    chars = [c for c in characters() if c not in drop]
    names = ['.notdef'] + [f'uni{ord(c):04X}' for c in chars]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({ord(c): n for c, n in zip(chars, names[1:])})
    glyphs = {}
    for index, name in enumerate(names):
        pen = TTGlyphPen(None)
        inset = 80 + (index * 7 + salt) % 60
        pen.moveTo((inset, -60)); pen.lineTo((1000 - inset, -60)); pen.lineTo((1000 - inset, 780))
        pen.lineTo((inset, 780)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({n: (advance, 80) for n in names})
    builder.setupHorizontalHeader(ascent=880, descent=-120)
    builder.setupNameTable(dict(familyName=family, styleName='Regular', uniqueFontIdentifier=family + '-1',
                                fullName=family, psName=family))
    builder.setupOS2(sTypoAscender=880, sTypoDescender=-120, usWinAscent=880, usWinDescent=120, fsType=0)
    builder.setupPost(); builder.setupMaxp()
    builder.font['head'].created = builder.font['head'].modified = 2082844800
    out = BytesIO(); builder.save(out)
    return out.getvalue()


def subset_font(full, text, tag='ABCDEF'):
    font = TTFont(BytesIO(full))
    options = ft_subset.Options()
    options.retain_gids = True
    options.name_IDs = ['*']
    subsetter = ft_subset.Subsetter(options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    for record in font['name'].names:
        if record.nameID in (1, 4, 6):
            record.string = tag + '+' + str(record.toUnicode())
    out = BytesIO(); font.save(out)
    return out.getvalue()


def wrap(text, indent=1, per_line=40):
    lines, cursor, first = [], 0, True
    while cursor < len(text):
        width = per_line - (indent if first else 0)
        end = min(len(text), cursor + width)
        assert end == len(text) or _safe_boundary(text, cursor, end), (text[cursor:end], text[end:end + 1])
        lines.append(text[cursor:end]); cursor, first = end, False
    return lines


def layout(paragraphs=PARAGRAPHS, *, first_baseline=160.0):
    """[(x, baseline, size, text)] for title, body and footer (page top-left origin)."""
    result = [((PAGE[0] - len(TITLE) * 14) / 2, 120.0, 14, TITLE)]
    baseline = first_baseline
    for text in paragraphs:
        for i, line in enumerate(wrap(text)):
            result.append((MARGIN + (SIZE if i == 0 else 0), baseline, SIZE, line))
            baseline += PITCH
        baseline += SPACE_AFTER
    result.append(((PAGE[0] - len(FOOTER) * SIZE) / 2, 800.0, SIZE, FOOTER))
    return result


def make_page(path, full_font, *, lines=None, tagged=False, drawings=(), rotate=0, matrix=None):
    lines = layout() if lines is None else lines
    used = ''.join(line[3] for line in lines)
    embedded = subset_font(full_font, used)
    cmap = TTFont(BytesIO(full_font)).getBestCmap()
    gid_of = {c: TTFont(BytesIO(full_font)).getGlyphID(cmap[ord(c)]) for c in set(used)}
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE[0], height=PAGE[1])
    page.insert_font(fontname='F1', fontbuffer=embedded)
    page.insert_text((MARGIN, 100), used[0], fontname='F1')
    xref = page.get_contents()[0]
    body = []
    for index, (x, baseline, size, text, *spacing) in enumerate(lines):
        codes = '<' + ''.join(f'{gid_of[c]:04X}' for c in text) + '>'
        tm = matrix(x, PAGE[1] - baseline) if matrix else f'1 0 0 1 {x:g} {PAGE[1] - baseline:g}'
        tc = f'{spacing[0]:g} Tc ' if spacing else ('0 Tc ' if any(len(l) > 4 for l in lines) else '')
        ops = f'BT /F1 {size:g} Tf {tc}{tm} Tm 0 g {codes} Tj ET'
        body.append(f'/P <</MCID {index}>> BDC {ops} EMC' if tagged else ops)
    for x0, y0, x1, y1 in drawings:
        body.append(f'q 0.8 g {x0:g} {PAGE[1] - y1:g} {x1 - x0:g} {y1 - y0:g} re f Q')
    doc.update_stream(xref, ('\n'.join(body) + '\n').encode())
    if rotate:
        page.set_rotation(rotate)
    path = Path(path)
    doc.save(path)
    doc.close()
    return path


def write_fonts(root, **variants):
    """Write the full candidate font (and named variants) into root; return their paths."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, options in dict(full={}, **variants).items():
        path = root / f'{name}.ttf'
        path.write_bytes(build_font(**options))
        paths[name] = path
    return paths
