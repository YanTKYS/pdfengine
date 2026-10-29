"""B2: a line below a growing paragraph drawn in one text object with other text is separated from it on explicit approval (issue #105)."""
import re
from test_paragraph_frame_tagged import LINES, tagged

ROW = re.compile(rb'q\nBT\n1 0 0 1 ([\d.]+) ([\d.]+) Tm\n/Fixture 11 Tf \[<([0-9a-f]+)>\]TJ\nET\nQ')
# The paragraph, an indented line below it (no following paragraph of it) and
# a heading at the top of the page; the line and the heading are drawn in one
# text object (SHARED).
SHARED = LINES[:3] + [(52, 112, '問合せ先は窓口です。'), (40, 24, '本年度の補助事業')]
GROW = ('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で')


def shared(tmp_path, name='shared.pdf', order='heading-first', inside=b'', after=b'', icon=False, clip=None, mixed=False, parts=1, move='Td',
           start=b'', lines=SHARED):
    """SHARED with the line (stream 3) and the heading (stream 4) in one text object.

    ``order``: 'heading-first' (Tm, heading, Tm, line) or 'line-first' (Tm,
    line, Td to the heading, heading). ``inside`` is written in the text
    object between the two, ``after`` right after the line's operator (HEAD:
    the heading's first glyph code);
    ``icon`` paints a box right before the text object, on the line ('placed':
    as InDesign places an object, BMC q … EMC Q); ``clip``
    (a clip path) encloses the text object; ``mixed`` draws the heading and the
    line with one TJ (a kerning jump between them); ``parts``: TJ operators
    drawing the line; ``move``: how the heading is placed after the line
    ('line-first': Td, TD, T* or ' with the leading); ``start`` is written at
    the start of the text object."""
    def change(data):
        line, head = ROW.search(data[3]), ROW.search(data[4])
        lx, ly, lc = float(line.group(1)), float(line.group(2)), line.group(3)
        hx, hy, hc = float(head.group(1)), float(head.group(2)), head.group(3)
        def show(codes, n=1):
            cut = [len(codes) // 4 * k // n * 4 for k in range(n + 1)]
            return b''.join(b'[<' + codes[a:b] + b'>]TJ\n' for a, b in zip(cut, cut[1:]))
        dx, dy = hx - lx, hy - ly
        # (T* and ' go down by the leading from the line's start: the heading at the line's x.)
        lead = f'{-dy:g} TL ' if move in ('T*', "'") else ''
        place = {'Td': f'{dx:g} {dy:g} Td\n', 'TD': f'{dx:g} {dy:g} TD\n', 'T*': 'T*\n'}.get(move)
        after_ = after.replace(b'HEAD', hc[:4])
        if mixed:
            body = f'1 0 0 1 {lx:g} {ly:g} Tm\n'.encode() + b'[<' + lc + b'> -6000 <' + hc + b'>]TJ\n'
        elif order == 'heading-first':
            body = f'1 0 0 1 {hx:g} {hy:g} Tm\n'.encode() + show(hc) + inside + f'1 0 0 1 {lx:g} {ly:g} Tm\n'.encode() + show(lc, parts) + after_
        else:
            body = (f'{lead}1 0 0 1 {lx:g} {ly:g} Tm\n'.encode() + show(lc, parts) + after_ + inside
                    + (b'<' + hc + b"> '\n" if move == "'" else place.encode() + show(hc)))
        text = b'BT\n/Fixture 11 Tf\n' + start + body + b'ET\n'
        if clip:
            text = b'q ' + clip + b' W n\n' + text + b'Q\n'
        box = f'.9 g {lx - 2:g} {ly - 3:g} 8 12 re f\n'.encode() if icon else b''
        if icon == 'placed':
            box = b'/PlacedPDF BMC q\n' + box + b'EMC Q\n'
        data[3] = data[3][:line.start()] + b'q\n' + box + text + b'Q' + data[3][line.end():]
        data[4] = data[4][:head.start()] + data[4][head.end():]
        return data
    source, _ = tagged(tmp_path, [], lines=lines, tree=False, edit_streams=change, name=name)
    return source


def fixture(name, folder):
    """The generated PDFs of the separation evaluation (separate_evaluate)."""
    folder.mkdir(parents=True, exist_ok=True)
    return dict(
        # The heading first, then the line: the line is the text object's last operator.
        G1=lambda: shared(folder),
        # The line first, then the heading placed by Td; a box on the line drawn before the text object.
        G2=lambda: shared(folder, order='line-first', icon=True),
        # One TJ draws the line and the heading: no operator is cut.
        G3=lambda: shared(folder, mixed=True),
        # Marked content in the text object between the heading and the line.
        G4=lambda: shared(folder, inside=b'/Tag BMC EMC\n'),
        # A glyph of the heading continues from where the line ends (raised by Ts, no positioning).
        G5=lambda: shared(folder, order='line-first', after=b'200 Ts\n[<HEAD>]TJ\n0 Ts\n'),
        # A clip around the text object that ends right below the line.
        G6=lambda: shared(folder, clip=b'0 182 300 118 re'),
    )[name]()


# --- tests ------------------------------------------------------------------

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject
from test_paragraph_frame_tagged import drawn_text, edit
from prototypes.paragraph_frame.auto import propose
from prototypes.paragraph_frame.engine import Refused

LINE, HEAD = '問合せ先は窓口です。', '本年度の補助事業'


def chain_of(source, old=GROW[0], new=GROW[1]):
    return propose(str(source), 1, old, new)['evidence'].get('chain')


def glyphs(path, text):
    """Origins of the glyphs of the page line drawing ``text`` (MuPDF)."""
    with pymupdf.open(path) as d:
        for b in d[0].get_text('rawdict')['blocks']:
            for l in b.get('lines', []):
                chars = [c for s in l['spans'] for c in s['chars']]
                if ''.join(c['c'] for c in chars).replace(' ', '') == text:
                    return [(c['c'], round(c['origin'][0], 3), round(c['origin'][1], 3)) for c in chars]
    return None


def pixels(path, clip, dpi=144):
    with pymupdf.open(path) as d:
        return d[0].get_pixmap(clip=clip, dpi=dpi, alpha=False).samples


def _spanned(path):
    """The first glyph codes shown inside the page's BDC … EMC, or None."""
    with pymupdf.open(path) as d:
        m = re.search(rb'BDC\n(?:(?!EMC).)*?\[<([0-9a-f]{8})', d[0].read_contents(), re.S)
    return m and m.group(1)


def in_form(source, out):
    """``source`` with its shared text object drawn by a Form XObject."""
    w = PdfWriter(clone_from=PdfReader(source))
    page = w.pages[0]
    for ref in page['/Contents']:
        stream = ref.get_object()
        data = stream.get_data()
        at = data.find(b'BT\n/Fixture 11 Tf\n')
        if at < 0:
            continue
        end = data.index(b'ET\n', at) + 3
        form = DecodedStreamObject()
        form.set_data(data[at:end])
        form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                     NameObject('/BBox'): ArrayObject([FloatObject(v) for v in page.mediabox]),
                     NameObject('/Resources'): page['/Resources'].get_object()})
        resources = page['/Resources'].get_object()
        resources[NameObject('/XObject')] = DictionaryObject({NameObject('/Fs0'): w._add_object(form)})
        stream.set_data(data[:at] + b'/Fs0 Do\n' + data[end:])
    w.write(out)
    return out


def test_a_line_drawn_with_other_text_is_separated_on_approval(tmp_path):
    source = shared(tmp_path)
    found = chain_of(source)
    (c,) = found['candidates']
    (x,) = c['separated']
    assert found['needed'] and x['operators_taken'] == 1 and x['operators_staying'] == 1 and x['text'] == LINE
    # Not approved (acknowledge_inference alone), the edit is refused.
    with pytest.raises(Refused, match='move with it only when approved explicitly'):
        edit(source, *GROW, tmp_path / 'plain.pdf')
    out = tmp_path / 'out.pdf'
    _, result = edit(source, *GROW, out, chain=[c['id']])
    delta = result['follower_translation']
    # The line moves by the paragraph's translation; the heading drawn with it stays, glyph for glyph and pixel for pixel.
    assert glyphs(out, LINE) == [(ch, x, round(y + delta, 3)) for ch, x, y in glyphs(source, LINE)]
    assert glyphs(out, HEAD) == glyphs(source, HEAD)
    assert pixels(out, pymupdf.Rect(30, 10, 200, 40)) == pixels(source, pymupdf.Rect(30, 10, 200, 40))
    assert drawn_text(out) == drawn_text(source).replace(*GROW)


@pytest.mark.parametrize('move', ['Td', 'TD', 'T*', "'"])
def test_text_after_the_line_placed_from_the_line_matrix_stays(tmp_path, move):
    source = shared(tmp_path, order='line-first', move=move, icon=True)
    (c,) = chain_of(source)['candidates']
    assert c['separated'][0]['operators_taken'] == 1
    out = tmp_path / 'out.pdf'
    _, result = edit(source, *GROW, out, chain=[c['id']])
    delta = result['follower_translation']
    assert glyphs(out, HEAD) == glyphs(source, HEAD)
    assert glyphs(out, LINE) == [(ch, x, round(y + delta, 3)) for ch, x, y in glyphs(source, LINE)]
    # The line keeps its place before the heading in the content (extraction order).
    with pymupdf.open(source) as a, pymupdf.open(out) as b:
        order = lambda p: [w[4] for w in p.get_text('words') if w[4] in (LINE, HEAD)]
        assert order(b[0]) == order(a[0]) == [LINE, HEAD]


@pytest.mark.parametrize('after', [b'1 0 0 1 200 276 Tm\n[<HEAD>]TJ\n', b'148 88 Td\n[<HEAD>]TJ\n', b'148 88 TD\n[<HEAD>]TJ\n'])
def test_a_line_between_other_text_is_drawn_at_its_place(tmp_path, after):
    # The heading, the line, then a glyph of the heading placed absolutely or
    # from the line matrix: the text object ends before the line and begins
    # again after it at that line matrix.
    source = shared(tmp_path, after=after)
    (c,) = chain_of(source)['candidates']
    assert c['separated'][0]['drawn'] == ['within'] and c['separated'][0]['operators_staying'] == 2
    out = tmp_path / 'out.pdf'
    _, result = edit(source, *GROW, out, chain=[c['id']])
    delta = result['follower_translation']
    assert glyphs(out, LINE) == [(ch, x, round(y + delta, 3)) for ch, x, y in glyphs(source, LINE)]
    first = lambda path: sorted((round(ch['origin'][0], 3), round(ch['origin'][1], 3)) for b in pymupdf.open(path)[0].get_text('rawdict')['blocks']
                                for l in b.get('lines', []) for sp in l['spans'] for ch in sp['chars'] if ch['c'] == HEAD[0])
    assert first(out) == first(source) and len(first(source)) == 3
    with pymupdf.open(source) as a, pymupdf.open(out) as b:
        order = lambda p: [w[4] for w in p.get_text('words') if w[4] in (LINE, HEAD, HEAD[0])]
        assert order(b[0]) == order(a[0]) == [HEAD, LINE, HEAD[0]]


def test_a_placed_icon_before_the_text_object_moves_with_the_line(tmp_path):
    source = shared(tmp_path, order='line-first', icon='placed')
    (c,) = chain_of(source)['candidates']
    out = tmp_path / 'out.pdf'
    _, result = edit(source, *GROW, out, chain=[c['id']])
    delta = result['follower_translation']
    assert glyphs(out, HEAD) == glyphs(source, HEAD)
    box = pymupdf.Rect(45, 100, 65, 118)
    with pymupdf.open(source) as a, pymupdf.open(out) as b:
        (x,) = [d['rect'] for d in a[0].get_drawings() if box.contains(d['rect'])]
        assert [d['rect'] for d in b[0].get_drawings() if d['rect'].y0 > x.y0 + 1] == [x + (0, delta, 0, delta)]


def test_a_line_drawn_by_several_operators_moves_whole(tmp_path):
    source = shared(tmp_path, parts=3)
    (c,) = chain_of(source)['candidates']
    assert c['separated'][0]['operators_taken'] == 3
    out = tmp_path / 'out.pdf'
    _, result = edit(source, *GROW, out, chain=[c['id']])
    assert glyphs(out, LINE) == [(ch, x, round(y + result['follower_translation'], 3)) for ch, x, y in glyphs(source, LINE)]
    assert glyphs(out, HEAD) == glyphs(source, HEAD)


def test_what_is_drawn_off_the_line_between_its_parts_stays(tmp_path):
    # The HP line's icon, a mark far below the line, then the line's text: the
    # mark is passed over (it stays) and the line moves in two parts.
    from test_paragraph_frame_chain import chained
    source = chained(tmp_path, between=b'0 g 30 20 4 4 re f\n')
    found = chain_of(source)
    (c,) = found['candidates']
    assert len(c['ranges']) == 2
    out = tmp_path / 'between.pdf'
    edit(source, *GROW, out, chain=[c['id']])
    assert pixels(out, pymupdf.Rect(25, 275, 40, 285)) == pixels(source, pymupdf.Rect(25, 275, 40, 285))


def with_extgstates(source, out, **states):
    """``source`` with ExtGStates (name: entries) in its page's resources; an
    entry 'SMask' of True is a luminosity soft mask."""
    w = PdfWriter(clone_from=PdfReader(source))
    page = w.pages[0]
    found = DictionaryObject()
    for name, entries in states.items():
        d = DictionaryObject({NameObject('/Type'): NameObject('/ExtGState')})
        for k, v in entries.items():
            if k == 'SMask':
                group = DecodedStreamObject()
                group.set_data(b'1 g 0 0 300 300 re f')
                group.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                              NameObject('/BBox'): ArrayObject([FloatObject(v) for v in (0, 0, 300, 300)]),
                              NameObject('/Group'): DictionaryObject({NameObject('/S'): NameObject('/Transparency'), NameObject('/CS'): NameObject('/DeviceGray')})})
                v = DictionaryObject({NameObject('/S'): NameObject('/Luminosity'), NameObject('/G'): w._add_object(group)})
            elif isinstance(v, (int, float)):
                v = FloatObject(v)
            else:
                v = NameObject(v)
            d[NameObject('/' + k)] = v
        found[NameObject('/' + name)] = w._add_object(d)
    page['/Resources'].get_object()[NameObject('/ExtGState')] = found
    w.write(out)
    return out


def test_marked_content_beside_the_line_stays_in_its_text_object(tmp_path):
    # An ActualText span of the heading's text in the text object: the line is not in it.
    source = shared(tmp_path, inside=b'/Span <</ActualText (x)>> BDC EMC\n')
    (c,) = chain_of(source)['candidates']
    out = tmp_path / 'out.pdf'
    edit(source, *GROW, out, chain=[c['id']])
    assert glyphs(out, HEAD) == glyphs(source, HEAD) and out.read_bytes().count(b'/ActualText') == source.read_bytes().count(b'/ActualText')


@pytest.mark.parametrize('order', ['heading-first', 'line-first'])
def test_marked_content_of_the_line_alone_moves_with_it(tmp_path, order):
    # A span holding the line's operator (InDesign: an ActualText span of a tab drawn as a space).
    span = b'/Span <</Lang (ja)>> BDC\n'
    options = dict(inside=span, after=b'EMC\n') if order == 'heading-first' else dict(start=span, after=b'EMC\n')
    source = shared(tmp_path, order=order, **options)
    (c,) = chain_of(source)['candidates']
    out = tmp_path / 'out.pdf'
    _, result = edit(source, *GROW, out, chain=[c['id']])
    assert glyphs(out, HEAD) == glyphs(source, HEAD)
    assert glyphs(out, LINE) == [(ch, x, round(y + result['follower_translation'], 3)) for ch, x, y in glyphs(source, LINE)]
    # The span is drawn once, around the moved line.
    with pymupdf.open(out) as d:
        data = d[0].read_contents()
    assert data.count(b'/Lang') == 1 and data.count(b'BDC') == data.count(b'EMC') == 1 and _spanned(out) == _spanned(source)


@pytest.mark.parametrize('order, start, inside', [
    # The line read GA (half opacity) set before it; drawn again before the text object, GA is set again.
    ('line-first', b'/GA gs\n', b'/GB gs\n'),
    # The line read GA set after the heading, the last state of the text object.
    ('heading-first', b'/GB gs\n', b'/GA gs\n'),
])
def test_an_extgstate_the_line_reads_is_set_again(tmp_path, order, start, inside):
    plain = shared(tmp_path, order=order, start=start, inside=inside)
    source = with_extgstates(plain, tmp_path / 'gs.pdf', GA=dict(ca=.5, CA=.5, BM='/Normal'), GB=dict(ca=1, CA=1, BM='/Normal'))
    (c,) = chain_of(source)['candidates']
    out = tmp_path / 'out.pdf'
    _, result = edit(source, *GROW, out, chain=[c['id']])
    delta = result['follower_translation']
    assert glyphs(out, HEAD) == glyphs(source, HEAD)
    line = pymupdf.Rect(40, 100, 200, 120)
    # The moved line paints as it did (half opacity), a whole number of pixels lower.
    zoom = round(delta * 2) / delta
    with pymupdf.open(source) as a, pymupdf.open(out) as b:
        m = pymupdf.Matrix(zoom, zoom)
        before = a[0].get_pixmap(matrix=m, clip=line, alpha=False).samples
        after = b[0].get_pixmap(matrix=m, clip=line + (0, delta, 0, delta), alpha=False).samples
    assert before == after


@pytest.mark.parametrize('options, reason', [
    (dict(mixed=True), 'draws glyphs of the line and other text'),
    # Marked content holding the heading and the line in the text object.
    (dict(start=b'/Tag BMC\n', after=b'EMC\n'), 'shares marked content with other text of its text object'),
    (dict(inside=b'/Span <</MCID 7>> BDC EMC\n'), 'holds structured marked content'),
    (dict(order='line-first', after=b'200 Ts\n[<HEAD>]TJ\n0 Ts\n'), 'continues from where the line ends'),
    (dict(clip=b'0 182 300 118 re'), 'does not hold it moved'),
    (dict(inside=b'1 Tr\n'), 'is stroked'),
    # Text that clips is not a line's text at all (refused before any separation).
    (dict(inside=b'4 Tr\n'), 'has no glyph drawn by a content-stream operator'),
    # A soft mask is fixed where it is set: not set again under the translation.
    (dict(start=b'/GM gs\n'), 'sets a soft mask or a font by ExtGState /GM'),
    # The line, drawn last, reads what was set before the text object; GA set after it would hold after it.
    (dict(start=b'', after=b'/GA gs\n'), 'reads ExtGState entries set after it'),
])
def test_a_line_that_cannot_be_separated_is_refused(tmp_path, options, reason):
    source = shared(tmp_path, **options)
    if any(b' gs' in v for v in options.values() if isinstance(v, bytes)):
        source = with_extgstates(source, tmp_path / 'gs.pdf', GA=dict(ca=.5), GM=dict(SMask=True), GO=dict(OP='/true'))
    found = chain_of(source)
    assert found['needed'] and not found['candidates'] and reason in found['unavailable']
    with pytest.raises(Refused, match='capacity'):
        edit(source, *GROW, tmp_path / 'out.pdf')


def test_a_text_object_in_a_form_xobject_is_not_separated(tmp_path):
    source = in_form(shared(tmp_path), tmp_path / 'form.pdf')
    found = chain_of(source)
    assert found['needed'] and not found['candidates'] and "only in the page's own content" in found['unavailable']


def test_a_separation_is_approved_by_its_own_id_only(tmp_path):
    source = shared(tmp_path)
    (c,) = chain_of(source)['candidates']
    with pytest.raises(Refused, match='line decoration is not a candidate of this edit'):
        propose(str(source), 1, *GROW, decorations=[c['id']])
    with pytest.raises(Refused, match='not a candidate of this edit'):
        propose(str(source), 1, *GROW, chain=['following-element-0000000000000000'])


def test_saved_pdf_alone_moves_the_separated_line_back(tmp_path):
    for name, options in (('first', {}), ('last', dict(order='line-first', icon=True))):
        (tmp_path / name).mkdir()
        source = shared(tmp_path / name, **options)
        (c,) = chain_of(source)['candidates']
        grown, back = tmp_path / name / 'grown.pdf', tmp_path / name / 'back.pdf'
        edit(source, *GROW, grown, chain=[c['id']])
        found = chain_of(grown, GROW[1], GROW[0])
        (d,) = found['candidates']
        # The moved line is drawn in a text object of its own now: nothing to separate.
        assert d['id'] != c['id'] and not d.get('separated')
        edit(grown, GROW[1], GROW[0], back, chain=[d['id']])
        assert glyphs(back, LINE) == glyphs(source, LINE) and glyphs(back, HEAD) == glyphs(source, HEAD)
        assert pixels(back, None, 72) == pixels(source, None, 72) and drawn_text(back) == drawn_text(source)


def test_the_edit_back_lists_only_the_lines_that_moved(tmp_path):
    # A line below the separated one that the growth does not reach: it stays,
    # and the edit back (which would reach it) does not list it.
    source = shared(tmp_path, lines=SHARED + [(52, 154, '受付は平日です。')])
    (c,) = chain_of(source)['candidates']
    grown, back = tmp_path / 'grown.pdf', tmp_path / 'back.pdf'
    edit(source, *GROW, grown, chain=[c['id']])
    assert glyphs(grown, '受付は平日です。') == glyphs(source, '受付は平日です。')
    (d,) = chain_of(grown, GROW[1], GROW[0])['candidates']
    assert d['line'] == LINE
    edit(grown, GROW[1], GROW[0], back, chain=[d['id']])
    assert pixels(back, None, 72) == pixels(source, None, 72) and drawn_text(back) == drawn_text(source)
