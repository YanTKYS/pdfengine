"""B2: a line decoration (a search box and its link) moves with its following paragraph on explicit approval (issue #99)."""
import re
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject, TextStringObject
from test_paragraph_frame_placed_follower import FOLLOWER, FOLLOWING
from test_paragraph_frame_tagged import drawn_text, tagged
from prototypes.paragraph_frame.auto import approve, propose, save
from prototypes.paragraph_frame.audit import painting
from prototypes.paragraph_frame.engine import Refused

GROW = ('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で')
SHRINK = ('申請書は窓口で配布し', '申請書は配布し')
# A search box on the following paragraph's line, after its text (InDesign's
# HP line): a white field with a colour set at its own level and a rule.
BOX = b'1 g 130 181 60 14 re f\nq .5 G 1 w 130 181.5 m 190 181.5 l S Q\n'
LINK = [130, 181, 190, 195]
# Text drawn after the line, outside the frame: it reads the fill colour the line leaves.
LATER = b'\nBT 1 0 0 1 250 30 Tm /Fixture 11 Tf [<04a8>]TJ ET\n'


def decorated(tmp_path, name='decorated.pdf', items=BOX, link=LINK, before=b'', between=b''):
    """Line 4 (the following paragraph): an inline placed object (its
    background, then ``BMC q … EMC Q``), ``between``, its text, then
    ``items``; a link annotation over ``link`` (PDF space); ``before`` is
    drawn first on the page."""
    def change(data):
        m = FOLLOWER.search(data[3])
        x, y, first, rest = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
        line = (f'.9 g {x - 1:g} {y - 3:g} 13 14 re f\n/PlacedPDF BMC q {x - 1:g} {y - 3:g} 13 14 re W n BT 0 g 1 0 0 1 {x:g} {y:g} Tm '
                f'/Fixture 11 Tf [<').encode() + first + b'>]TJ ET\nEMC Q\n' + between + f'BT 0 g 1 0 0 1 {x + 12:g} {y:g} Tm /Fixture 11 Tf [<'.encode() + rest + b'>]TJ ET\n'
        data[3] = data[3][:m.start()] + line + items + data[3][m.end():] + LATER
        data[0] = before + data[0]
        return data
    source, _ = tagged(tmp_path, [], lines=FOLLOWING, tree=False, edit_streams=change, name='plain-' + name)
    if link is None:
        return source
    w = PdfWriter(clone_from=PdfReader(source))
    p = w.pages[0]
    action = DictionaryObject({NameObject('/S'): NameObject('/URI'), NameObject('/URI'): TextStringObject('https://example.org/search')})
    annot = DictionaryObject({NameObject('/Type'): NameObject('/Annot'), NameObject('/Subtype'): NameObject('/Link'),
                              NameObject('/Rect'): ArrayObject([FloatObject(v) for v in link]),
                              NameObject('/Border'): ArrayObject([NumberObject(0)] * 3), NameObject('/A'): action})
    p[NameObject('/Annots')] = ArrayObject([w._add_object(annot)])
    out = tmp_path / name
    w.write(out)
    return out


def fixture(name, folder):
    """The generated PDFs of the decoration evaluation (decoration_evaluate.FIXTURES)."""
    folder.mkdir(parents=True, exist_ok=True)
    return dict(
        D1=lambda: decorated(folder),
        D2=lambda: decorated(folder, items=b'0 0 1 rg 130 181 60 14 re f\n'),
        D3=lambda: decorated(folder, link=[130, 181, 280, 195]),
        D4=lambda: decorated(folder, between=b'30 160 240 40 re W n\n'),
        D5=lambda: decorated(folder, before=b'q .2 g 120 182 4 12 re f Q\n'),
    )[name]()


def candidates(source, old, new):
    return propose(str(source), 1, old, new)['evidence'].get('decorations', {})


def edit(source, old, new, out, decorations=()):
    proposal = propose(str(source), 1, old, new, decorations=decorations)
    return proposal, save(str(source), approve(str(source), proposal, acknowledge_inference=True), str(out))


def links(path):
    with pymupdf.open(path) as d:
        return [(l['uri'], [round(v, 3) for v in l['from']]) for l in d[0].get_links()]


def box(path):
    """The white field's page bounds."""
    return next(pymupdf.Rect(x['rect']) for x in painting(str(path), 1)['drawings'] if x.get('fill') == (1.0, 1.0, 1.0) and x['rect'].width > 50)


def later(path):
    """Colour and origin of the text drawn after the line."""
    with pymupdf.open(path) as d:
        return [(s['color'], [round(v, 3) for v in s['origin']]) for b in d[0].get_text('dict')['blocks'] for l in b.get('lines', [])
                for s in l['spans'] if s['origin'][0] > 240]


def test_a_decoration_is_listed_and_refused_until_approved(tmp_path):
    source = decorated(tmp_path)
    found = candidates(source, *GROW)
    (c,) = found['candidates']
    assert c['id'].startswith('line-decoration-') and c['glyphs'] == 7 and c['placed_objects'] == 1
    assert c['annotations'] == [dict(xref=c['annotations'][0]['xref'], subtype='/Link', rect=[130.0, 105.0, 190.0, 119.0],
                                     uri='https://example.org/search')]
    assert c['replayed_state'][:1] == ['g'] and c['translation'] > 10
    # acknowledge_inference alone never approves it.
    with pytest.raises(Refused, match=f"line decoration {c['id']} moves with it only when approved explicitly"):
        edit(source, *GROW, tmp_path / 'out.pdf')


@pytest.mark.parametrize('old,new', [GROW, SHRINK])
def test_an_approved_decoration_moves_with_its_paragraph(tmp_path, old, new):
    source = decorated(tmp_path)
    (c,) = candidates(source, old, new)['candidates']
    out = tmp_path / 'out.pdf'
    proposal, result = edit(source, old, new, out, decorations=[c['id']])
    assert proposal['decorations'] == [c['id']]
    delta = result['follower_translation']
    (moved,) = result['decorations_moved']
    assert moved['id'] == c['id'] and abs(moved['translation'][1] + delta) < 1e-6
    # The field, the link's clickable area and its target move by the paragraph's translation.
    assert box(out) == box(source) + (0, delta, 0, delta)
    ((uri, before),), ((uri2, after),) = links(source), links(out)
    assert uri2 == uri and all(abs(a - b) < 1e-3 for a, b in zip(after, [before[0], before[1] + delta, before[2], before[3] + delta]))
    assert drawn_text(out) == drawn_text(source).replace(old, new)
    # What follows the line reads the state the line left.
    assert later(out) == later(source)


def test_saved_pdf_alone_moves_it_back(tmp_path):
    source = decorated(tmp_path)
    (c,) = candidates(source, *GROW)['candidates']
    grown, back = tmp_path / 'grown.pdf', tmp_path / 'back.pdf'
    edit(source, *GROW, grown, decorations=[c['id']])
    found = candidates(grown, GROW[1], GROW[0])
    (d,) = found['candidates']
    edit(grown, GROW[1], GROW[0], back, decorations=[d['id']])
    assert box(back) == box(source) and links(back) == links(source) and drawn_text(back) == drawn_text(source)
    with pymupdf.open(source) as a, pymupdf.open(back) as b:
        assert a[0].get_pixmap().samples == b[0].get_pixmap().samples


def test_the_state_the_line_leaves_is_set_again(tmp_path):
    source = fixture('D2', tmp_path)
    (c,) = candidates(source, *GROW)['candidates']
    out = tmp_path / 'out.pdf'
    edit(source, *GROW, out, decorations=[c['id']])
    data = PdfReader(out).pages[0].get_contents().get_data()
    # The fill colours its text objects and its field set, and its font, in order.
    assert b'130 181 60 14 re f\nQ\n0 g\n/Fixture 11 Tf\n0 0 1 rg' in data
    assert later(out) == later(source)


def test_only_a_listed_unchanged_candidate_is_approved(tmp_path):
    source = decorated(tmp_path)
    (c,) = candidates(source, *GROW)['candidates']
    with pytest.raises(Refused, match='not a candidate of this edit'):
        propose(str(source), 1, *GROW, decorations=['line-decoration-0000000000000000'])
    # The same approval on a PDF whose box changed after it was read.
    changed = decorated(tmp_path, name='changed.pdf', items=BOX.replace(b'60 14 re f', b'61 14 re f'))
    with pytest.raises(Refused, match='not a candidate of this edit'):
        propose(str(changed), 1, *GROW, decorations=[c['id']])


@pytest.mark.parametrize('name,reason', [
    ('D3', 'reaches past its line decoration'),
    # A clip set at the line's own level would hold after it.
    ('D4', r'changes the CTM or the clip at its own level \(W\)'),
    # Paint drawn elsewhere on the line stays behind.
    ('D5', 'outside its line decoration'),
])
def test_unprovable_decorations_are_not_offered(tmp_path, name, reason):
    source = fixture(name, tmp_path)
    found = candidates(source, *GROW)
    assert not found['candidates'] and re.search(reason, found['unavailable'][0]['reason'])
    with pytest.raises(Refused, match='holds other content'):
        edit(source, *GROW, tmp_path / 'out.pdf')
