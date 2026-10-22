"""B2 on scaled text matrices/CTMs, Form XObject text (copy-on-write) and marked-content containers."""
import hashlib
import re
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, IndirectObject, NameObject
from test_paragraph_frame_continuity import binding, page_with
from prototypes.paragraph_frame.auto import approve, propose, save
from prototypes.paragraph_frame.audit import painting
from prototypes.paragraph_frame.engine import Refused

LINES = [(40, 60, '本年度の補助事業について、募集を'), (40, 76, '行います。申請書は窓口で配布し'), (40, 92, 'ます。'), (40, 130, '後続段落です。')]
H = 300


def rewrite(path, out, transform):
    """Rewrite every content stream of page 1 with ``transform(data) -> data``."""
    w = PdfWriter(clone_from=PdfReader(path))
    page = w.pages[0]
    contents = page['/Contents']
    streams = contents if isinstance(contents, ArrayObject) else [contents]
    for ref in streams:
        stream = ref.get_object()
        stream.set_data(transform(stream.get_data()))
    w.write(out)
    return out


def matrix(kind):
    """Write each line as ``1 Tf`` under a scaled text matrix (and CTM)."""
    def tm(match):
        x, y = float(match.group(1)), float(match.group(2))
        return {'scaled': f'11 0 0 11 {x} {y} Tm', 'ctm': f'22 0 0 22 {2 * x} {2 * y} Tm',
                'mirrored': f'11 0 0 -11 {x} {H - y} Tm', 'rotated': f'0 11 -11 0 {x} {y} Tm'}[kind].encode()

    def transform(data):
        data = re.sub(rb'1 0 0 1 ([\d.]+) ([\d.]+) Tm', tm, data)
        data = data.replace(b'/Fixture 11 Tf', b'/Fixture 1 Tf')
        prefix = {'ctm': b'q 0.5 0 0 0.5 0 0 cm\n', 'mirrored': b'q 1 0 0 -1 0 300 cm\n'}.get(kind, b'q\n')
        return prefix + data + b'\nQ\n'
    return transform


def edit(source, old, new, out, **options):
    proposal = propose(source, 1, old, new, **options)
    result = save(source, approve(source, proposal, acknowledge_inference=True), out)
    return proposal, result


def spans(path):
    with pymupdf.open(path) as d:
        return [(round(s['origin'][0], 2), round(s['origin'][1], 2), round(s['size'], 2), s['text'])
                for b in d[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']]


@pytest.mark.parametrize('kind', ['scaled', 'ctm', 'mirrored'])
def test_scaled_text_matrix_and_ctm_edit_like_identity(tmp_path, kind):
    plain, _ = page_with(tmp_path, LINES, height=H)
    source = rewrite(plain, tmp_path / f'{kind}.pdf', matrix(kind))
    # The rewritten source draws the same glyphs at the same size and place.
    assert spans(source) == spans(plain)
    reference, out = tmp_path / 'reference.pdf', tmp_path / 'out.pdf'
    expected, _ = edit(plain, '申請書は窓口で', '申請書は各窓口で', reference)
    proposal, result = edit(source, '申請書は窓口で', '申請書は各窓口で', out)
    # Same inferred paragraph, page-space size and line breaks as the identity PDF.
    assert proposal['lines'] == expected['lines']
    assert [s['size'] for s in proposal['source_styles']] == [11.0]
    assert spans(out) == spans(reference)
    assert result['insertion_ctm'] == ([1, 0, 0, 1, 0, 0] if kind == 'scaled' else [0.5, 0, 0, 0.5, 0, 0] if kind == 'ctm' else [1, 0, 0, -1, 0, 300])
    assert painting(str(out), 1)['drawings'] == painting(str(source), 1)['drawings']
    # The saved PDF alone is edited again with the stored conditions.
    again, _ = edit(out, '申請書は各窓口で', '申請書は窓口で', tmp_path / 'again.pdf')
    assert again['evidence']['conditions']['status'] == 'applied'


@pytest.mark.parametrize('tm, ctm, expected', [
    ((9.6, 0, 0, 9.6), (1, 0, 0, 1), (9.6, 9.6)),
    ((12, 0, 0, -12), (0.43, 0, 0, -0.43), (5.16, 5.16)),
    ((0.85, 0, 0, 13.92), (1, 0, 0, 1), (0.85, 13.92)),
    ((0, 11, -11, 0), (1, 0, 0, 1), 'rotated or sheared'),
    ((11, 0, 2, 11), (1, 0, 0, 1), 'rotated or sheared'),
    ((11, 0, 0, -11), (1, 0, 0, 1), 'mirrored'),
])
def test_text_space_accepts_only_upright_axis_aligned_scales(tm, ctm, expected):
    from types import SimpleNamespace
    from prototypes.paragraph_frame import text_space
    event = SimpleNamespace(text_matrix=tm + (0, 0), state=SimpleNamespace(ctm=ctm + (0, 0), size=1, tz=100, tc=0, tw=0, ts=0))
    if isinstance(expected, str):
        with pytest.raises(Refused, match=expected):
            text_space.scales(event)
    else:
        assert text_space.scales(event) == pytest.approx(expected)
        assert text_space.effective(event)['size'] == pytest.approx(expected[1])
        assert text_space.effective(event)['tz'] == pytest.approx(100 * expected[0] / expected[1])


def test_rotated_text_stays_refused(tmp_path):
    plain, _ = page_with(tmp_path, LINES, height=H)
    source = rewrite(plain, tmp_path / 'rotated.pdf', matrix('rotated'))
    # Rotated lines do not form a horizontal paragraph; nothing is edited.
    with pytest.raises(Refused):
        propose(source, 1, '申請書は窓口で', '申請書は各窓口で')


def _form_page(tmp_path, *, nested=False, container=None):
    """The paragraph drawn by a Form XObject painted twice (or through an outer Form)."""
    plain, _ = page_with(tmp_path, LINES[:3], height=H)
    r = PdfReader(plain)
    page = r.pages[0]
    body = b''.join(ref.get_object().get_data() for ref in page['/Contents'])
    if container:
        body = container[0] + b'\n' + body + b'\nEMC\n'
    w = PdfWriter(clone_from=r)
    p = w.pages[0]
    font = p['/Resources']['/Font']
    form = DecodedStreamObject()
    form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                 NameObject('/BBox'): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(300), FloatObject(300)]),
                 NameObject('/Resources'): DictionaryObject({NameObject('/Font'): font})})
    form.set_data(body)
    inner = w._add_object(form)
    xobjects = DictionaryObject({NameObject('/Inner'): inner})
    paints = b'q 1 0 0 1 0 0 cm /Inner Do Q\nq 1 0 0 1 0 -150 cm /Inner Do Q\n'
    if nested:
        outer = DecodedStreamObject()
        outer.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                      NameObject('/BBox'): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(300), FloatObject(300)]),
                      NameObject('/Resources'): DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/Inner'): inner})})})
        outer.set_data(b'q 1 0 0 1 0 0 cm /Inner Do Q\n')
        xobjects[NameObject('/Outer')] = w._add_object(outer)
        paints = b'q 1 0 0 1 0 0 cm /Outer Do Q\nq 1 0 0 1 0 -150 cm /Inner Do Q\n'
    p['/Resources'][NameObject('/XObject')] = xobjects
    contents = DecodedStreamObject()
    contents.set_data(paints)
    p[NameObject('/Contents')] = w._add_object(contents)
    out = tmp_path / 'form.pdf'
    w.write(out)
    return out


@pytest.mark.parametrize('nested', [False, True])
def test_shared_form_is_copied_on_write_for_one_invocation(tmp_path, nested):
    source = _form_page(tmp_path, nested=nested)
    texts = [t for *_, t in spans(source)]
    assert texts.count('本年度の補助事業について、募集を') == 2
    out = tmp_path / 'out.pdf'
    _, result = edit(source, '申請書は窓口で', '申請書は各窓口で', out, occurrence=0)
    assert result['drawn_in'] == 'Form XObject (copy-on-write)'
    assert [c['form'] for c in result['form_clones']] == (['/Inner', '/Outer'] if nested else ['/Inner'])
    after = spans(out)
    top = [t for x, y, s, t in after if y < 150]
    bottom = [(x, y, s, t) for x, y, s, t in after if y >= 150]
    assert ''.join(top).replace(' ', '') == '本年度の補助事業について、募集を行います。申請書は各窓口で配布します。'
    # The other painting of the shared Form is unchanged, and so is the Form object.
    assert bottom == [(x, y, s, t) for x, y, s, t in spans(source) if y >= 150]
    r_in, r_out = PdfReader(source), PdfReader(out)
    inner = r_in.pages[0]['/Resources']['/XObject']['/Inner'].get_object().get_data()
    assert r_out.pages[0]['/Resources']['/XObject']['/Inner'].get_object().get_data() == inner
    assert painting(str(out), 1)['drawings'] == painting(str(source), 1)['drawings']


def test_container_marked_content_is_kept_around_new_text(tmp_path):
    source = _form_page(tmp_path, container=(b'/PlacedPDF <</Note 1>> BDC',))
    out = tmp_path / 'out.pdf'
    edit(source, '申請書は窓口で', '申請書は各窓口で', out, occurrence=0)
    from pdfeditor.marked_content import observe_marked_content
    from pdfeditor.content_stream import ContentPage
    c = ContentPage(str(out), 1)
    try:
        scopes = [s for s in observe_marked_content(str(out), 1)['scopes'] if s['tag'] == '/PlacedPDF']
        # Each painting keeps its container, and every drawn glyph of the edited
        # painting is inside it.
        assert len(scopes) == 2
        for s in scopes:
            lo, hi = s['content_byte_range']
            inv = tuple(tuple(i) for i in s['invocation'])
            drawn = ''.join(ch.text for e in c.events if e.invocation == inv and lo <= e.operator.start < hi for ch in e.chars)
            assert drawn.replace(' ', '') in ('本年度の補助事業について、募集を行います。申請書は各窓口で配布します。',
                                              '本年度の補助事業について、募集を行います。申請書は窓口で配布します。')
    finally:
        c.close()


def test_replacement_text_marked_content_is_refused(tmp_path):
    source = _form_page(tmp_path, container=(b'/Span <</ActualText (x)>> BDC',))
    with pytest.raises(Refused, match='replacement-text marked content'):
        propose(source, 1, '申請書は窓口で', '申請書は各窓口で')


def test_named_fill_colour_space_is_selected_again(tmp_path):
    plain, _ = page_with(tmp_path, LINES, height=H)
    source = rewrite(plain, tmp_path / 'named.pdf', lambda d: d.replace(b'BT\n', b'/DeviceRGB cs 0 0 1 sc\nBT\n'))
    out = tmp_path / 'out.pdf'
    edit(source, '申請書は窓口で', '申請書は各窓口で', out)
    with pymupdf.open(out) as d:
        colours = {s['color'] for b in d[0].get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans']}
    assert colours == {0x0000ff}


@pytest.mark.parametrize('moves', ['absolute', 'relative'])
def test_column_in_one_text_object_keeps_reading_order(tmp_path, moves):
    """Like InDesign: one BT block draws the whole column; the edit is spliced in as ET … BT Tm."""
    lines = LINES + [(40, 170, '最後の段落になります。')]
    plain, _ = page_with(tmp_path, lines, height=H)
    w = PdfWriter(clone_from=PdfReader(plain))
    page = w.pages[0]
    body = b''.join(ref.get_object().get_data() for ref in page['/Contents'])
    body = re.sub(rb'\s*ET\s*Q\s*q\s*BT\s*', b'\n', body)
    assert body.count(b'BT') == 1
    if moves == 'relative':
        origins = [(float(x), float(y)) for x, y in re.findall(rb'1 0 0 1 ([\d.]+) ([\d.]+) Tm', body)]
        steps = iter([None] + [(x - a, y - b) for (a, b), (x, y) in zip(origins, origins[1:])])

        def td(match):
            step = next(steps)
            return match.group(0) if step is None else f'{step[0]:g} {step[1]:g} Td'.encode()
        body = re.sub(rb'1 0 0 1 [\d.]+ [\d.]+ Tm', td, body)
        assert body.count(b'Tm') == 1
    contents = DecodedStreamObject()
    contents.set_data(body)
    page[NameObject('/Contents')] = w._add_object(contents)
    source = tmp_path / 'column.pdf'
    w.write(source)
    out = tmp_path / 'out.pdf'
    _, result = edit(source, '後続段落です。', '次の段落です。', out)
    assert result['insertion_inside_text_object'] is True

    def words(path):
        with pymupdf.open(path) as d:
            return d[0].get_text().replace(' ', '').split()
    assert words(out) == [t.replace('後続段落', '次の段落') for t in words(source)]
    from prototypes.paragraph_frame.audit import extract
    poppler = [re.sub(r'\s+', '', extract(path, 1, tmp_path)['poppler']) for path in (source, out)]
    assert poppler[1] == poppler[0].replace('後続段落', '次の段落')
    # The new line is one kerned TJ run in the original font, like the source.
    data = PdfReader(out).pages[0].get_contents().get_data()
    runs = re.findall(rb'Tz 1 0 0 1 \S+ \S+ Tm (\[[^\]]*\])TJ', data)
    assert [run.count(b'<') for run in runs] == [len('次の段落です。')]
    assert painting(str(out), 1)['drawings'] == painting(str(source), 1)['drawings']
    again, _ = edit(out, '次の段落です。', '後続段落です。', tmp_path / 'again.pdf')
    assert again['evidence']['conditions']['status'] == 'applied'


def test_indirect_font_dictionary_keeps_font_references(tmp_path):
    """A new font joins an indirect /Font dictionary; the original fonts stay referenced, not inlined."""
    plain, font = page_with(tmp_path, LINES, height=H)
    with pymupdf.open(plain) as d:
        d.subset_fonts()
        d.save(tmp_path / 'subset.pdf')
    w = PdfWriter(clone_from=PdfReader(tmp_path / 'subset.pdf'))
    resources = w.pages[0]['/Resources']
    resources[NameObject('/Font')] = w._add_object(DictionaryObject(dict(resources['/Font'])))
    source = tmp_path / 'indirect.pdf'
    w.write(source)
    out = tmp_path / 'out.pdf'
    # 各 is not in the embedded subset: the approved substitute adds a font resource.
    proposal, _ = edit(source, '申請書は窓口で', '申請書は各窓口で', out, substitute=binding(font))
    assert proposal['font_counts'].get('approved-substitute') == 1
    fonts = PdfReader(out).pages[0]['/Resources']['/Font']
    assert all(isinstance(fonts.raw_get(k), IndirectObject) for k in fonts)
    assert painting(str(out), 1)['fonts'].items() >= painting(str(source), 1)['fonts'].items()
