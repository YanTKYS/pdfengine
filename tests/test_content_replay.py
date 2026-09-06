"""Safety regressions for byte-provenance replay (synthetic fixtures are not corpus evidence)."""
from pathlib import Path

import pymupdf
import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
)

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.replay import glyph_observations, no_op_replay
from pdfeditor.selection import make_selection


def _pdf(tmp_path, programs, *, shared=False):
    """Courier's actual 600-unit widths make the PDF width table authoritative."""
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Courier'),
        NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
        NameObject('/FirstChar'): NumberObject(32),
        NameObject('/LastChar'): NumberObject(126),
        NameObject('/Widths'): ArrayObject([NumberObject(600) for _ in range(95)]),
    }))
    refs = []
    for data in programs:
        stream = DecodedStreamObject()
        stream.set_data(data)
        refs.append(writer._add_object(stream))
    for _ in range(2 if shared else 1):
        page = writer.add_blank_page(300, 200)
        page[NameObject('/Resources')] = DictionaryObject({
            NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
        })
        page[NameObject('/Contents')] = refs[0] if len(refs) == 1 else ArrayObject(refs)
    source = tmp_path / 'source.pdf'
    with source.open('wb') as handle:
        writer.write(handle)
    return source


def _replay(tmp_path, source, ids):
    before = source.read_bytes()
    output, removed = tmp_path / 'replayed.pdf', tmp_path / 'removed.pdf'
    report = no_op_replay(source, output, make_selection(source, glyph_ids=ids),
                          removal_output=removed)
    assert source.read_bytes() == before
    assert report['removal_audit']['passed']
    assert report['replay_audit']['passed']
    with pymupdf.open(source) as original, pymupdf.open(output) as replayed:
        for index in range(len(original)):
            assert original[index].get_pixmap(dpi=144).samples == replayed[index].get_pixmap(dpi=144).samples
    return output, removed, report


def test_operator_lexer_retains_spans_and_does_not_parse_string_contents_as_operators():
    data = b'% ignored BT\nBT /F1 12 Tf (A\\(B\\)\\\\C\\101\\\r\nD % TJ) Tj [<4546> -125 (G)] TJ ET'
    parsed = list(operators(data))
    assert [op.name for op in parsed] == ['BT', 'Tf', 'Tj', 'TJ', 'ET']
    assert bytes(parsed[2].args[0]) == b'A(B)\\CAD % TJ'
    assert bytes(parsed[3].args[0][0]) == b'EF'
    assert data[parsed[2].start:parsed[2].end].endswith(b') Tj')
    assert int(parsed[3].args[0][1]) == -125


def test_operator_lexer_rejects_inline_images_and_incomplete_operands():
    with pytest.raises(PdfError, match='inline image'):
        list(operators(b'q BI /W 1 /H 1 ID x EI Q'))
    with pytest.raises(PdfError, match='trailing operands'):
        list(operators(b'BT /F1 12'))


def test_partial_tj_deletion_preserves_remaining_origins_and_trailing_text(tmp_path):
    source = _pdf(tmp_path, [b'BT /F1 12 Tf 1 0 0 1 20 150 Tm [(AB) -100 (CD)] TJ (EF) Tj ET'])
    _, removed, _ = _replay(tmp_path, source, [1])
    with pymupdf.open(source) as original, pymupdf.open(removed) as doc:
        expected = [g for i, g in enumerate(glyph_observations(original[0])) if i != 1]
        actual = glyph_observations(doc[0])
        assert ''.join(g['unicode'] for g in actual) == 'ACDEF'
        assert [g['origin'] for g in actual] == [g['origin'] for g in expected]
        assert b'<42>' not in b''.join(doc.xref_stream(x) for x in doc[0].get_contents())


def test_contents_array_is_one_program_even_when_array_operand_is_split(tmp_path):
    source = _pdf(tmp_path, [
        b'q BT /F1 12 Tf 1 0 0 1 20 150 Tm [',
        b'(A) 125',
        b'(BC)] TJ ET Q',
    ])
    content = ContentPage(source, 1)
    try:
        assert not content.errors
        assert len(content.selected_events({0, 1, 2})) == 1
        assert ''.join(c.text for c in content.events[0].chars) == 'ABC'
    finally:
        content.close()
    _replay(tmp_path, source, [1])


def test_q_q_clip_and_matrix_scope_do_not_leak_to_later_text(tmp_path):
    source = _pdf(tmp_path, [
        b'q 0 0 300 200 re W n 2 0 0 2 0 0 cm '
        b'BT /F1 12 Tf 1 0 0 1 10 50 Tm (AB) Tj ET Q '
        b'BT /F1 12 Tf 1 0 0 1 20 50 Tm (CD) Tj ET'
    ])
    content = ContentPage(source, 1)
    try:
        first, second = content.selected_events({0, 1, 2, 3})
        assert len(first.state.clip) == 1
        assert first.state.ctm == (2, 0, 0, 2, 0, 0)
        assert second.state.clip == ()
        assert second.state.ctm == (1, 0, 0, 1, 0, 0)
    finally:
        content.close()
    _replay(tmp_path, source, [2])


def test_shared_content_stream_is_cloned_for_target_page_only(tmp_path):
    source = _pdf(tmp_path, [b'BT /F1 12 Tf 1 0 0 1 20 150 Tm (ABC) Tj ET'], shared=True)
    _, removed, _ = _replay(tmp_path, source, [1])
    with pymupdf.open(source) as original, pymupdf.open(removed) as doc:
        assert 'B' not in doc[0].get_text()
        assert 'ABC' in doc[1].get_text()
        assert doc[0].get_contents() != doc[1].get_contents()
        assert original[1].get_pixmap().samples == doc[1].get_pixmap().samples


def test_separate_fill_and_stroke_operators_at_same_position_are_individually_removable(tmp_path):
    source = _pdf(tmp_path, [
        b'BT /F1 12 Tf 1 0 0 1 20 150 Tm 0 Tr (A) Tj '
        b'1 0 0 1 20 150 Tm 1 Tr (A) Tj ET'
    ])
    _, removed, _ = _replay(tmp_path, source, [0])
    with pymupdf.open(removed) as doc:
        actual = glyph_observations(doc[0])
        assert len(actual) == 1
        assert actual[0]['unicode'] == 'A'
        assert actual[0]['paint_type'] == 1


def test_one_operator_fill_and_stroke_requires_both_paints(tmp_path):
    source = _pdf(tmp_path, [b'BT /F1 12 Tf 1 0 0 1 20 150 Tm 2 Tr (A) Tj ET'])
    selection = make_selection(source, glyph_ids=[0])
    output = tmp_path / 'must-not-exist.pdf'
    with pytest.raises(PdfError, match='both fill and stroke'):
        no_op_replay(source, output, selection)
    assert not output.exists()
    _replay(tmp_path, source, [0, 1])


def test_indistinguishable_duplicate_same_mode_paints_fail_closed(tmp_path):
    source = _pdf(tmp_path, [
        b'BT /F1 12 Tf 1 0 0 1 20 150 Tm (A) Tj '
        b'1 0 0 1 20 150 Tm (A) Tj ET'
    ])
    output = tmp_path / 'must-not-exist.pdf'
    with pytest.raises(PdfError, match='provenance'):
        no_op_replay(source, output, make_selection(source, glyph_ids=[0]))
    assert not output.exists()


def test_text_spacing_quote_operator_and_later_line_positions_survive_partial_removal(tmp_path):
    source = _pdf(tmp_path, [
        b'BT /F1 12 Tf 80 Tz 2 Tc 3 Tw 16 TL 1 0 0 1 20 150 Tm '
        b'(A B) Tj 3 2 (C D) " (EF) \' ET'
    ])
    _replay(tmp_path, source, [1, 4])


@pytest.mark.parametrize('name,ids', [
    ('word_kyoto_questions', list(range(41, 189))),
    ('word_okinawa_procurement', list(range(41, 91))),
])
def test_real_subset_cid_widths_replay_without_reembedding_font(tmp_path, name, ids):
    source = Path(__file__).resolve().parents[1] / 'evaluations/realpdf/corpus' / f'{name}.pdf'
    if not source.exists():
        pytest.skip('external corpus not available')
    _, _, report = _replay(tmp_path, source, ids)
    assert report['font_resources_identical']
    assert all(e['state']['font_xref'] for e in report['events'])
