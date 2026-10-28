"""B2: the lines below a growing paragraph that its growth would meet move with it on explicit approval (issue #103)."""
import re
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
from test_paragraph_frame_tagged import LINES, tagged

ROW = re.compile(rb'q\nBT\n1 0 0 1 ([\d.]+) ([\d.]+) Tm\n/Fixture 11 Tf \[<([0-9a-f]{4})([0-9a-f]+)>\]TJ\nET\nQ')
# Two lines below the paragraph, indented (no following paragraph of it): an
# "HP" line (an inline placed icon, its text, a search box with a link) and an
# enquiry line (an icon drawn as a filled box, then its text).
CHAINED = LINES[:3] + [(52, 112, '検索窓口です。'), (52, 128, '問合せ先は窓口です。')]
GROW = ('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で')
SEARCH = [140, 185, 200, 199]


def _icon(data, line, items=b''):
    m = ROW.search(data[line])
    x, y, first, rest = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
    drawn = (f'.9 g {x - 1:g} {y - 3:g} 13 14 re f\n/PlacedPDF BMC q {x - 1:g} {y - 3:g} 13 14 re W n BT 0 g 1 0 0 1 {x:g} {y:g} Tm '
             f'/Fixture 11 Tf [<').encode() + first + b'>]TJ ET\nEMC Q\n' + f'BT 0 g 1 0 0 1 {x + 12:g} {y:g} Tm /Fixture 11 Tf [<'.encode() + rest + b'>]TJ ET\n'
    data[line] = data[line][:m.start()] + drawn + items + data[line][m.end():]


def chained(tmp_path, name='chained.pdf', before=b'', link=SEARCH):
    """CHAINED with the HP line's search box (and its link over ``link``, PDF
    space) and the enquiry line's icon; ``before`` is drawn first on the page."""
    def change(data):
        _icon(data, 3, b'1 g 140 185 60 14 re f\nq .5 G 1 w 140 185.5 m 200 185.5 l S Q\n')
        _icon(data, 4)
        data[0] = before + data[0]
        return data
    source, _ = tagged(tmp_path, [], lines=CHAINED, tree=False, edit_streams=change, name='plain-' + name)
    w = PdfWriter(clone_from=PdfReader(source))
    action = DictionaryObject({NameObject('/S'): NameObject('/URI'), NameObject('/URI'): TextStringObject('https://example.org/search')})
    annot = DictionaryObject({NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Link'),
                              NameObject('/Rect'): ArrayObject([FloatObject(v) for v in link]),
                              NameObject('/Border'): ArrayObject([NumberObject(0)] * 3), NameObject('/A'): action})
    w.pages[0][NameObject('/Annots')] = ArrayObject([w._add_object(annot)])
    out = tmp_path / name
    w.write(out)
    return out


def fixture(name, folder):
    """The generated PDFs of the chain evaluation (chain_evaluate)."""
    folder.mkdir(parents=True, exist_ok=True)
    return dict(
        # Free space below the two lines.
        F1=lambda: chained(folder),
        # A rule right below the enquiry line: the moved lines would meet it.
        F2=lambda: chained(folder, before=b'q 0 g 30 157 240 1 re f Q\n'),
        # A panel behind the paragraph and the lines that ends right below them.
        F3=lambda: chained(folder, before=b'q .95 g 36 160 186 110 re f Q\n'),
    )[name]()
