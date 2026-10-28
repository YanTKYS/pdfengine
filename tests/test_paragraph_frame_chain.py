"""B2: the lines below a growing paragraph that its growth would meet move with it on explicit approval (issue #103)."""
import re
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
from test_paragraph_frame_tagged import LINES, drawn_text, edit, tagged
from prototypes.paragraph_frame import auto_conditions as conditions
from prototypes.paragraph_frame.audit import painting
from prototypes.paragraph_frame.auto import propose
from prototypes.paragraph_frame.engine import Refused
from pdfeditor.content_stream import ContentPage

ROW = re.compile(rb'q\nBT\n1 0 0 1 ([\d.]+) ([\d.]+) Tm\n/Fixture 11 Tf \[<([0-9a-f]{4})([0-9a-f]+)>\]TJ\nET\nQ')
# Two lines below the paragraph, indented (no following paragraph of it): an
# "HP" line (an inline placed icon, its text, a search box with a link) and an
# enquiry line (an icon drawn as a filled box, then its text).
CHAINED = LINES[:3] + [(52, 112, '検索窓口です。'), (52, 128, '問合せ先は窓口です。')]
GROW = ('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で')
SEARCH = [140, 185, 200, 199]
# The enquiry line indented further: two elements.
SEPARATE = CHAINED[:4] + [(64, 128, '問合せ先は窓口です。')]


def _icon(data, line, items=b'', between=b''):
    m = ROW.search(data[line])
    x, y, first, rest = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
    drawn = (f'.9 g {x - 1:g} {y - 3:g} 13 14 re f\n/PlacedPDF BMC q {x - 1:g} {y - 3:g} 13 14 re W n BT 0 g 1 0 0 1 {x:g} {y:g} Tm '
             f'/Fixture 11 Tf [<').encode() + first + b'>]TJ ET\nEMC Q\n' + between + f'BT 0 g 1 0 0 1 {x + 12:g} {y:g} Tm /Fixture 11 Tf [<'.encode() + rest + b'>]TJ ET\n'
    data[line] = data[line][:m.start()] + drawn + items + data[line][m.end():]


BOX = b'1 g 140 185 60 14 re f\nq .5 G 1 w 140 185.5 m 200 185.5 l S Q\n'


def chained(tmp_path, name='chained.pdf', before=b'', link=SEARCH, lines=CHAINED, clip=None, between=b'', box=BOX):
    """``lines`` with the HP line's search ``box`` (and its link over ``link``, PDF
    space, if any) and the enquiry line's icon; ``before`` is drawn first on the page,
    ``clip`` (a clip path) encloses the HP line, ``between`` is drawn inside it
    between its icon and its text."""
    def change(data):
        _icon(data, 3, box, between)
        _icon(data, 4)
        if clip:
            data[3] = b'q ' + clip + b' W n\n' + data[3] + b'\nQ\n'
        data[0] = before + data[0]
        return data
    source, _ = tagged(tmp_path, [], lines=lines, tree=False, edit_streams=change, name='plain-' + name)
    if link is None:
        return source
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


def chain_of(source, old=GROW[0], new=GROW[1]):
    return propose(str(source), 1, old, new)['evidence'].get('chain')


def below(path, y=98):
    """Glyph origins and drawings below ``y`` (page), with the links."""
    with pymupdf.open(path) as d:
        p = d[0]
        glyphs = sorted((c['c'], round(c['origin'][0], 3), round(c['origin'][1], 3)) for b in p.get_text('rawdict')['blocks']
                        for l in b.get('lines', []) for s in l['spans'] for c in s['chars'] if c['origin'][1] > y and c['origin'][0] > 50)
        links = [(l['uri'], [round(v, 3) for v in l['from']]) for l in p.get_links()]
    drawings = sorted((x.get('type'), str(x.get('fill')), tuple(round(v, 3) for v in x['rect'])) for x in painting(str(path), 1)['drawings'] if x['rect'].y0 > y)
    return glyphs, drawings, links


def shifted(found, dy):
    glyphs, drawings, links = found
    return (sorted((c, x, round(y + dy, 3)) for c, x, y in glyphs), sorted((t, f, (r[0], round(r[1] + dy, 3), r[2], round(r[3] + dy, 3))) for t, f, r in drawings),
            [(u, [r[0], round(r[1] + dy, 3), r[2], round(r[3] + dy, 3)]) for u, r in links])


def test_the_chain_is_listed_and_refused_until_approved(tmp_path):
    source = chained(tmp_path)
    found = chain_of(source)
    (c,) = found['candidates']
    assert found['needed'] and c['id'].startswith('following-element-') and c['translation'] == found['translation'] > 10
    assert c['placed_objects'] == 2 and c['annotations'][0]['uri'] == 'https://example.org/search' and c['reached'] == 'the growth reaches it'
    # acknowledge_inference alone never approves it, nor a line decoration's approval.
    with pytest.raises(Refused, match=f"following elements {c['id']} move with it only when approved explicitly"):
        edit(source, *GROW, tmp_path / 'out.pdf')
    with pytest.raises(Refused, match='line decoration is not a candidate of this edit'):
        propose(str(source), 1, *GROW, decorations=[c['id']])


def test_an_approved_chain_moves_with_the_paragraph(tmp_path):
    source = chained(tmp_path)
    (c,) = chain_of(source)['candidates']
    out = tmp_path / 'out.pdf'
    proposal, result = edit(source, *GROW, out, chain=[c['id']])
    assert proposal['chain'] == [c['id']] and proposal['evidence']['inferred']['frame'][3] == 116
    delta = result['follower_translation']
    (moved,) = result['chain_moved']
    assert moved['id'] == c['id'] and abs(moved['translation'][1] + delta) < 1e-6 and moved['glyphs'] == 17
    # The lines' glyphs, icons, search box and link move by the paragraph's translation.
    glyphs, drawings, links = below(out, 98 + delta)
    assert (glyphs, drawings, links) == shifted(below(source), delta)
    assert drawn_text(out) == drawn_text(source).replace(*GROW)
    # The paragraph's new line is where the lines were.
    with pymupdf.open(out) as d:
        assert any(abs(l['bbox'][1] - 96.5) < 1 and l['bbox'][0] < 41 for b in d[0].get_text('dict')['blocks'] for l in b.get('lines', []))


def test_every_element_of_a_chain_is_approved_together(tmp_path):
    source = chained(tmp_path, lines=SEPARATE)
    a, b = chain_of(source)['candidates']
    with pytest.raises(Refused, match='move only together; approve every one: ' + b['id']):
        propose(str(source), 1, *GROW, chain=[a['id']])
    _, result = edit(source, *GROW, tmp_path / 'out.pdf', chain=[a['id'], b['id']])
    assert [m['id'] for m in result['chain_moved']] == [a['id'], b['id']]


# The paragraph near the bottom of the page: the line below would leave it.
LOW = [(x, y + 170, t) for x, y, t in LINES[:3]] + [(52, 278, '検索窓口です。'), (64, 294, '問合せ先は窓口です。')]


@pytest.mark.parametrize('name,options,reason', [
    ('F2', None, 'would meet protected filled-vector'),
    ('F3', None, 'would leave or enter a background'),
    ('clip', dict(clip=b'30 180 240 30 re', lines=SEPARATE), 'does not hold it moved'),
    ('between', dict(between=b'0 g 30 20 4 4 re f\n'), 'drawn together with content beyond its line'),
    ('low', dict(lines=LOW, link=None, box=b''), 'would leave the page'),
])
def test_a_chain_that_cannot_move_is_refused(tmp_path, name, options, reason):
    source = fixture(name, tmp_path) if options is None else chained(tmp_path, **options)
    found = chain_of(source)
    assert found['needed'] and reason in found['unavailable']
    with pytest.raises(Refused, match='capacity'):
        edit(source, *GROW, tmp_path / 'out.pdf', chain=[c['id'] for c in found['candidates']])


def test_saved_pdf_alone_moves_it_back(tmp_path):
    source = chained(tmp_path)
    (c,) = chain_of(source)['candidates']
    grown, plain, back = tmp_path / 'grown.pdf', tmp_path / 'plain.pdf', tmp_path / 'back.pdf'
    edit(source, *GROW, grown, chain=[c['id']])
    found = chain_of(grown, GROW[1], GROW[0])
    (d,) = found['candidates']
    # The edit back lists its own candidate: the first approval is not carried over.
    assert d['id'] != c['id'] and not found['needed'] and d['reached'] == 'attached below the shrinking paragraph'
    with pytest.raises(Refused, match='not a candidate of this edit'):
        propose(str(grown), 1, GROW[1], GROW[0], chain=[c['id']])
    # Not approved, the lines stay where they are.
    edit(grown, GROW[1], GROW[0], plain)
    assert below(plain, 110) == below(grown, 110)
    edit(grown, GROW[1], GROW[0], back, chain=[d['id']])
    assert below(back) == below(source) and drawn_text(back) == drawn_text(source)
    with pymupdf.open(source) as a, pymupdf.open(back) as b:
        assert a[0].get_pixmap().samples == b[0].get_pixmap().samples


def test_the_moved_lines_are_recorded(tmp_path):
    # The saved PDF names the moved lines where they are (auto_conditions.moved, issue #101).
    source = chained(tmp_path)
    (c,) = chain_of(source)['candidates']
    out = tmp_path / 'out.pdf'
    edit(source, *GROW, out, chain=[c['id']])
    page = ContentPage(str(out), 1)
    try:
        texts = [r['text'] for m in conditions.read_moved(page, 1) for r in conditions.resolve_moved(page, m) or []]
    finally:
        page.close()
    assert '検索窓口です。' in ''.join(texts) and '問合せ先は窓口です。' in ''.join(texts)
