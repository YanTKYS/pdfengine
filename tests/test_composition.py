"""End-to-end composition regressions; corpus results are evaluated separately."""
from io import BytesIO
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, FloatObject

from pdfeditor.backend import PdfError
from pdfeditor.composition import compose_selected
from pdfeditor.model import WidthUnknownError
from pdfeditor.replay import glyph_observations
from pdfeditor.selection import make_selection
from pdfeditor.shaped_font import ShapedFont


@pytest.fixture(scope="module")
def font():
    return ShapedFont(pymupdf.Font("cjk").buffer)


def source_pdf(tmp_path, program=None):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'),
        NameObject('/BaseFont'):NameObject('/Courier'), NameObject('/Encoding'):NameObject('/WinAnsiEncoding'),
        NameObject('/FirstChar'):NumberObject(32), NameObject('/LastChar'):NumberObject(126),
        NameObject('/Widths'):ArrayObject([NumberObject(600)]*95)}))
    program = program or b'BT /F1 12 Tf 1 0 0 1 20 150 Tm (OLD) Tj 160 0 Td (KEEP) Tj ET'
    stream = DecodedStreamObject(); stream.set_data(program)
    page = writer.add_blank_page(300,200)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.add_blank_page(300,200)
    path = tmp_path/'source.pdf'
    writer.write(path)
    return path


def text(path):
    return ''.join(PdfReader(path).pages[0].extract_text().split())


def test_new_japanese_and_symbols_wrap_and_remain_editable(tmp_path, font):
    source = source_pdf(tmp_path)
    before = source.read_bytes()
    manifest = make_selection(source, glyph_ids=[0,1,2], explicit_width=100)
    output, removed = tmp_path/'edited.pdf', tmp_path/'removed.pdf'
    replacement = '提出期限は2026年9月です。※申請書類を提出してください。'
    report = compose_selected(source, output, manifest, replacement, font_source=font,
                              max_height=120, removal_output=removed)
    assert report['new_line_count'] > report['old_line_count'] == 1
    assert text(output) == ''.join(replacement.split())+'KEEP'
    assert text(removed) == 'KEEP'
    assert source.read_bytes() == before
    with pymupdf.open(output) as doc:
        ids = [i for i,g in enumerate(glyph_observations(doc[0])) if g['font'] != 'Courier']
    again = make_selection(output, glyph_ids=ids, explicit_width=100)
    second = tmp_path/'second.pdf'
    shorter = compose_selected(output, second, again, '受付完了。', font_source=font, max_height=120)
    assert shorter['new_line_count'] == 1
    assert text(second) == '受付完了。KEEP'


@pytest.mark.parametrize('operator', [b"(OLD) '", b'0.4 0.2 (OLD) "'])
def test_quote_implicit_newline_executes_once_and_following_relative_text_stays_put(tmp_path, font, operator):
    source = source_pdf(tmp_path, b'BT /F1 12 Tf 18 TL 1 0 0 1 20 168 Tm '+operator+b' 160 0 Td (KEEP) Tj ET')
    with pymupdf.open(source) as doc:
        expected = glyph_observations(doc[0])[3:]
    output = tmp_path/'quote.pdf'
    compose_selected(source, output, make_selection(source, glyph_ids=[0,1,2], explicit_width=100),
                     '提出期限', font_source=font, max_height=100)
    with pymupdf.open(output) as doc:
        actual = [g for g in glyph_observations(doc[0]) if g['font']=='Courier']
    for a, e in zip(actual, expected):
        assert a['origin'] == pytest.approx(e['origin'], abs=.001)
    assert ''.join(g['unicode'] for g in actual) == 'KEEP'


def test_large_shortening_removes_both_old_lines(tmp_path, font):
    source = source_pdf(tmp_path, b'BT /F1 12 Tf 18 TL 1 0 0 1 20 150 Tm (ORIGINAL) Tj T* (SECOND LINE) Tj ET')
    output = tmp_path/'shorter.pdf'
    manifest = make_selection(source, glyph_ids=list(range(19)), explicit_width=180)
    report = compose_selected(source, output, manifest, '完了', font_source=font)
    assert (report['old_line_count'], report['new_line_count']) == (2,1)
    assert text(output) == '完了'


def test_unknown_width_and_neighbor_collision_publish_nothing(tmp_path, font):
    source = source_pdf(tmp_path)
    output = tmp_path/'rejected.pdf'
    with pytest.raises(WidthUnknownError):
        compose_selected(source, output, make_selection(source,glyph_ids=[0,1,2]), '提出',font_source=font)
    assert not output.exists()
    with pytest.raises(PdfError, match='collides'):
        compose_selected(source, output, make_selection(source,glyph_ids=[0,1,2],explicit_width=260),
                         '提出期限の詳細については担当者に確認してください。',font_source=font)
    assert not output.exists()


def test_shaped_kerning_and_composed_accent_survive_pdf_roundtrip(tmp_path):
    font_path=Path('C:/Windows/Fonts/arial.ttf')
    if not font_path.exists():pytest.skip('Arial required for this real shaping fixture')
    font=ShapedFont(font_path)
    source=source_pdf(tmp_path, b'BT /F1 12 Tf 1 0 0 1 20 150 Tm (OLD) Tj ET')
    output=tmp_path/'kerning.pdf'
    report=compose_selected(source,output,make_selection(source,glyph_ids=[0,1,2],explicit_width=250),
                            'AVATAR office e\u0301',font_source=font)
    assert any(abs(g['advance']-g['nominal_advance'])>.001 for g in report['glyph_plan'])
    assert text(output)=='AVATARofficee\u0301'


@pytest.mark.parametrize('visible_space', [False, True])
def test_only_proven_empty_space_can_be_crossed(tmp_path, font, visible_space):
    writer = PdfWriter()
    page = writer.add_blank_page(300, 200)
    run = font.shape('OLD ')
    if visible_space:
        # Keep Unicode SPACE, deliberately paint the O outline at its position.
        run = replace(run, glyphs=(*run.glyphs[:3], replace(run.glyphs[0], text=' ', cluster=3)))
    resource = font.resource([run])
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({
        NameObject('/F1'):resource.build(writer)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 1 0 0 1 20 150 Tm <'+b''.join(resource.code(g).hex().encode() for g in run.glyphs)+b'> Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    source = tmp_path/'space.pdf'; writer.write(source)
    output = tmp_path/'result.pdf'
    selection = make_selection(source, glyph_ids=[0,1,2], explicit_width=150)
    if visible_space:
        with pytest.raises(PdfError, match='collides'):
            compose_selected(source, output, selection, '申請書類提出', font_source=font)
        assert not output.exists()
    else:
        with pymupdf.open(source) as doc:
            space = glyph_observations(doc[0])[-1]
        compose_selected(source, output, selection, '申請書類提出', font_source=font)
        with pymupdf.open(output) as doc:
            actual = next(g for g in glyph_observations(doc[0]) if g['unicode'] == ' ')
        assert actual['unicode'] == ' '
        assert actual['origin'] == pytest.approx(space['origin'], abs=.001)


def test_unselected_type3_survives_object_renumbering(tmp_path, font):
    base = source_pdf(tmp_path)
    writer = PdfWriter(clone_from=base)
    # Unreachable objects force the Type3 reference to change during cloning.
    for _ in range(8):
        writer._add_object(DictionaryObject())
    glyph = DecodedStreamObject(); glyph.set_data(b'600 0 0 0 500 700 d1 0 0 500 700 re f')
    type3 = writer._add_object(DictionaryObject({
        NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type3'),
        NameObject('/FontBBox'):ArrayObject([NumberObject(n) for n in (0,0,500,700)]),
        NameObject('/FontMatrix'):ArrayObject([FloatObject(n) for n in (.001,0,0,.001,0,0)]),
        NameObject('/CharProcs'):DictionaryObject({NameObject('/A'):writer._add_object(glyph)}),
        NameObject('/Encoding'):DictionaryObject({NameObject('/Differences'):ArrayObject([NumberObject(65),NameObject('/A')])}),
        NameObject('/FirstChar'):NumberObject(65), NameObject('/LastChar'):NumberObject(65),
        NameObject('/Widths'):ArrayObject([NumberObject(600)]), NameObject('/Resources'):DictionaryObject()}))
    page = writer.pages[0]
    page['/Resources']['/Font'][NameObject('/Logo')] = type3
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data()+b' BT /Logo 12 Tf 1 0 0 1 240 40 Tm (A) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    source = tmp_path/'type3.pdf'; writer.write(source)
    output = tmp_path/'composed.pdf'
    report = compose_selected(source, output, make_selection(source, glyph_ids=[0,1,2], explicit_width=120),
                              '申請期限', font_source=font)
    assert report['source_replay_mupdf_passed']
    assert report['mupdf_outside_pixels_equal']
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        assert glyph_observations(a[0])[-1]['font'] != glyph_observations(b[0])[-1]['font']
        assert a[0].get_pixmap(clip=(230,140,270,180)).samples == b[0].get_pixmap(clip=(230,140,270,180)).samples


@pytest.mark.parametrize('foreground', [False, True])
def test_background_fill_and_foreground_cover_have_different_paint_order(tmp_path, font, foreground):
    fill = b'q .9 g 15 130 45 40 re f Q '
    text_program = b'BT /F1 12 Tf 1 0 0 1 20 150 Tm (OLD) Tj ET '
    source = source_pdf(tmp_path, text_program+fill if foreground else fill+text_program)
    output = tmp_path/'background.pdf'
    manifest = make_selection(source, glyph_ids=[0,1,2], explicit_width=150)
    if foreground:
        with pytest.raises(PdfError, match='filled vector'):
            compose_selected(source, output, manifest, '申請書類提出', font_source=font)
        assert not output.exists()
    else:
        compose_selected(source, output, manifest, '申請書類提出', font_source=font)
        with pymupdf.open(source) as a, pymupdf.open(output) as b:
            assert a[0].get_drawings() == b[0].get_drawings()


def test_new_space_inherits_tw_even_when_source_selection_has_no_space(tmp_path, font):
    source = source_pdf(tmp_path, b'BT /F1 12 Tf 10 Tw 1 0 0 1 20 150 Tm (OLD) Tj ET')
    output = tmp_path/'word-spacing.pdf'
    report = compose_selected(source, output, make_selection(source, glyph_ids=[0,1,2], explicit_width=150),
                              'T T', font_source=font)
    space = next(g for g in report['glyph_plan'] if g['unicode'] == ' ')
    assert report['word_spacing'] == 10
    assert space['advance'] - space['nominal_advance'] == pytest.approx(10)
