"""B2: an inline placed object of a following paragraph moves with its text (issue #97)."""
import re
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject
from test_paragraph_frame_tagged import LINES, drawn_text, edit, tagged
from prototypes.paragraph_frame.audit import painting
from prototypes.paragraph_frame.engine import Refused

FOLLOWER = re.compile(rb'q\nBT\n1 0 0 1 ([\d.]+) ([\d.]+) Tm\n/Fixture 11 Tf \[<([0-9a-f]{4})([0-9a-f]+)>\]TJ\nET\nQ')


def placed_follower(head=b'', inside=b'', tail=b'', after=b'', wrap=None, line=3):
    """The first glyph of the following paragraph (``line``) as an inline placed
    object like InDesign's icons: its background painted right before its
    sequence, a q opened in the sequence and closed after its EMC.

    ``head`` is drawn right after its BMC, ``inside`` in its q after the glyph,
    ``tail`` between its EMC and that Q, ``after`` after the object; ``wrap``
    (clip path) encloses the whole line in a clip."""
    def change(data):
        m = FOLLOWER.search(data[line])
        x, y, first, rest = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
        box = f'{x - 1:g} {y - 3:g} 13 14 re'.encode()
        placed = (b'q .9 g ' + box + b' f\n/PlacedPDF BMC ' + head + b'q ' + box + b' W n BT 0 g 1 0 0 1 ' + f'{x:g} {y:g}'.encode()
                  + b' Tm /Fixture 11 Tf [<' + first + b'>]TJ ET ' + inside + b'\nEMC ' + tail + b'Q Q\n' + after
                  + f'q BT 1 0 0 1 {x + 12:g} {y:g} Tm /Fixture 11 Tf [<'.encode() + rest + b'>]TJ ET Q')
        if wrap:
            placed = b'q ' + wrap + b' W n\n' + placed + b'\nQ'
        data[line] = data[line][:m.start()] + placed + data[line][m.end():]
        return data
    return change


# The following paragraph half a line below the edited one.
FOLLOWING = LINES[:3] + [(40, 116, '後続段落です。')]


def source_with(tmp_path, change=None, name='placed.pdf'):
    source, _ = tagged(tmp_path, [], lines=FOLLOWING, tree=False, edit_streams=change or placed_follower(), name=name)
    return source


def icon(path):
    """Page bounds of the placed object's background and of its glyph."""
    with pymupdf.open(path) as d:
        page = d[0]
        background = next(x['rect'] for x in page.get_drawings() if x.get('fill') and abs(x['rect'].width - 13) < .01)
        glyph = next(c['bbox'] for b in page.get_text('rawdict')['blocks'] for l in b.get('lines', []) for s in l['spans']
                     for c in s['chars'] if c['c'] == '後')
        return pymupdf.Rect(background), pymupdf.Rect(glyph)


def follower_baseline(path):
    with pymupdf.open(path) as d:
        return next(c['origin'][1] for b in d[0].get_text('rawdict')['blocks'] for l in b.get('lines', []) for s in l['spans']
                    for c in s['chars'] if c['c'] == '続')


@pytest.mark.parametrize('old,new,lines', [('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で', 1), ('申請書は窓口で配布し', '申請書は配布し', -1)])
def test_placed_object_moves_with_its_following_paragraph(tmp_path, old, new, lines):
    source = source_with(tmp_path)
    out = tmp_path / 'out.pdf'
    proposal, result = edit(source, old, new, out)
    delta = result['follower_translation']
    assert (delta > 0) == (lines > 0) and abs(delta) > 10
    (bg0, g0), (bg1, g1) = icon(source), icon(out)
    # The background, the clipped glyph and the rest of the line move together, exactly.
    assert bg1 == bg0 + (0, delta, 0, delta)
    assert all(abs(a - b) < 1e-3 for a, b in zip(g1, g0 + (0, delta, 0, delta)))
    assert abs(follower_baseline(out) - follower_baseline(source) - delta) < 1e-3
    # Its operators are kept, translated as one unit.
    before = PdfReader(source).pages[0].get_contents().get_data()
    after = PdfReader(out).pages[0].get_contents().get_data()
    unit = re.search(rb'[\d.]+ [\d.]+ 13 14 re f\n/PlacedPDF BMC.*?EMC Q', before, re.S).group(0)
    assert after.count(unit) == 1 and re.search(rb'q 1 0 0 1 0 -?[\d.]+ cm\n' + re.escape(unit) + rb'\nQ', after)
    assert drawn_text(out) == drawn_text(source).replace(old, new)
    # Nothing else is painted differently.
    others = lambda p: sorted((round(x['rect'].x0, 2), round(x['rect'].y0, 2)) for x in painting(p, 1)['drawings'] if abs(x['rect'].width - 13) > .01)
    assert others(out) == others(source)


def test_saved_pdf_alone_moves_it_back(tmp_path):
    source = source_with(tmp_path)
    grown, back = tmp_path / 'grown.pdf', tmp_path / 'back.pdf'
    edit(source, '申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で', grown)
    proposal, _ = edit(grown, '申請書は窓口と郵送と電子申請とファクスと持参で', '申請書は窓口で', back)
    assert proposal['evidence']['conditions']['status'] == 'applied'
    (bg0, g0), (bg2, g2) = icon(source), icon(back)
    assert all(abs(a - b) < 1e-3 for a, b in zip(bg2, bg0)) and all(abs(a - b) < 1e-3 for a, b in zip(g2, g0))
    assert abs(follower_baseline(back) - follower_baseline(source)) < 1e-3
    assert drawn_text(back) == drawn_text(source)


def _shared_form(tmp_path):
    """The placed object paints a Form XObject that the page paints elsewhere too."""
    source = source_with(tmp_path, placed_follower(inside=b'/Mark Do'), name='form-src.pdf')
    w = PdfWriter(clone_from=PdfReader(source))
    p = w.pages[0]
    form = DecodedStreamObject()
    form.set_data(b'1 0 0 rg 0 0 4 4 re f')
    form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                 NameObject('/BBox'): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(4), FloatObject(4)])})
    ref = w._add_object(form)
    res = p['/Resources']
    res[NameObject('/XObject')] = DictionaryObject({NameObject('/Mark'): ref})
    # The object's own invocation is placed by the CTM of its sequence; the other one is elsewhere.
    contents = p['/Contents']
    data = [c.get_object().get_data() for c in contents]
    m = FOLLOWER.search(data[3]) or re.search(rb'1 0 0 1 ([\d.]+) ([\d.]+) Tm /Fixture 11 Tf \[<[0-9a-f]{4}>\]TJ ET /Mark Do', data[3])
    x, y = float(m.group(1)), float(m.group(2))
    data[3] = data[3].replace(b'ET /Mark Do', f'ET q 1 0 0 1 {x + 7:g} {y - 2:g} cm /Mark Do Q'.encode())
    data[0] = b'q 1 0 0 1 200 250 cm /Mark Do Q\n' + data[0]
    for n, c in enumerate(contents):
        s = DecodedStreamObject()
        s.set_data(data[n])
        contents[n] = w._add_object(s)
    out = tmp_path / 'form.pdf'
    w.write(out)
    return out


def mark_form(path):
    with pymupdf.open(path) as d:
        return d.xref_stream(next(x[0] for x in d[0].get_xobjects() if x[1] == 'Mark'))


def test_a_shared_form_painted_by_the_object_is_not_changed(tmp_path):
    source = _shared_form(tmp_path)
    out = tmp_path / 'out.pdf'
    _, result = edit(source, '申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で', out)
    delta = result['follower_translation']
    marks = lambda p: sorted((round(x['rect'].x0, 2), round(x['rect'].y0, 2)) for x in painting(p, 1)['drawings'] if abs(x['rect'].width - 4) < .01)
    own, other = marks(source)  # the object's own invocation is beside its glyph, left of the other one
    # The other invocation stays; the object's own moves with it.
    assert marks(out) == [(own[0], round(own[1] + delta, 2)), other]
    assert mark_form(out) == mark_form(source) == b'1 0 0 rg 0 0 4 4 re f'


GROW = ('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で')


def test_state_inside_its_own_q_and_after_it_is_kept(tmp_path):
    # A colour set inside its q is restored by it; one set after it is not part of it.
    source = source_with(tmp_path, placed_follower(inside=b'1 0 0 rg', after=b'0 0 1 rg\n'))
    edit(source, *GROW, tmp_path / 'out.pdf')
    (bg0, _), (bg1, _) = icon(source), icon(tmp_path / 'out.pdf')
    assert bg1.y0 - bg0.y0 > 10


@pytest.mark.parametrize('change,reason', [
    # A colour set at the object's own level would be read by what follows it.
    (placed_follower(head=b'1 0 0 rg '), r'sets state used after it \(rg\)'),
    # Text drawn in its q after its sequence is not the object's.
    (placed_follower(tail=b'BT 0 g 1 0 0 1 200 184 Tm /Fixture 11 Tf [<04a8>]TJ ET '), 'shares its graphics state with later content'),
    # Paint on the paragraph's line that does not move with it (a search box).
    (placed_follower(after=b'q .5 g 150 181 60 14 re f Q\n'), r'holds other content \(filled-vector\)'),
    (placed_follower(wrap=b'0 0 m 300 0 l 0 300 l h'), 'nonrectangular clip'),
    # A clip around the line only: moved, the object would be clipped away.
    (placed_follower(wrap=b'30 175 200 25 re'), 'does not hold it moved'),
])
def test_unprovable_placed_objects_are_refused(tmp_path, change, reason):
    source = source_with(tmp_path, change)
    with pytest.raises(Refused, match=reason):
        edit(source, *GROW, tmp_path / 'out.pdf')


def test_moved_objects_are_recorded(tmp_path):
    source = source_with(tmp_path)
    _, result = edit(source, *GROW, tmp_path / 'out.pdf')
    (moved,) = result['placed_objects_moved']
    assert moved['glyphs'] == 1 and moved['translation'] == [0, -16]
    assert pymupdf.Rect(moved['new']) == pymupdf.Rect(moved['old']) + (0, 16, 0, 16)

