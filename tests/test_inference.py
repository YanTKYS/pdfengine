"""Geometry tests use observations only, without opening or generating a PDF."""

import random

import pytest

from pdfeditor.inference import infer_boxes
from pdfeditor.model import Glyph, Rect, Style


REGULAR = Style("Example", 12)
BOLD = Style("Example-Bold", 12)


def glyph_line(text, x=40, baseline=60, style=REGULAR, first_order=0, advance=None):
    glyphs = []
    for index, character in enumerate(text):
        width = advance if advance is not None else (6 if character.isascii() else 12)
        glyphs.append(Glyph(character, Rect(x, baseline - style.size, x + width, baseline + 2),
                            (x, baseline), width, style, source_order=first_order + index))
        x += width
    return glyphs


def boxes(glyphs):
    return infer_boxes(glyphs, page=1, page_width=600, page_height=800)


def test_shuffled_glyph_order_recovers_japanese_soft_wrap_and_source_identity():
    observed = glyph_line("申請書類の提出期限は") + glyph_line("10月15日までです。", baseline=76.8, first_order=100)
    expected_ids = {id(glyph) for glyph in observed}
    random.Random(32).shuffle(observed)
    result = boxes(observed)
    assert len(result) == 1
    assert result[0].text == "申請書類の提出期限は10月15日までです。"
    assert len(result[0].lines) == 2
    assert result[0].line_height == pytest.approx(16.8)
    assert {id(glyph) for glyph in result[0].glyphs if glyph.source_order >= 0} == expected_ids


def test_drawing_order_does_not_merge_columns_or_independent_blocks():
    observed = (glyph_line("左側本文の最初の行")
                + glyph_line("左側本文の続きです", baseline=76.8, first_order=100)
                + glyph_line("右側の本文です", x=320, first_order=200)
                + glyph_line("右側の続きです", x=320, baseline=76.8, first_order=300)
                + glyph_line("離れた下の文章", baseline=200, first_order=400))
    random.Random(8).shuffle(observed)
    result = boxes(observed)
    assert len(result) == 3
    assert result[0].text == "左側本文の最初の行左側本文の続きです"
    assert result[1].text == "右側の本文です右側の続きです"
    assert result[2].text == "離れた下の文章"
    assert result[0].bbox.x1 < result[1].bbox.x0


def test_paragraph_spacing_is_preserved_but_soft_wraps_are_removed():
    observed = (glyph_line("第一段落の最初の文章")
                + glyph_line("第一段落の続きです。", baseline=76.8, first_order=100)
                + glyph_line("第二段落の最初の文章", baseline=102, first_order=200)
                + glyph_line("第二段落の続きです。", baseline=118.8, first_order=300))
    result = boxes(observed)
    assert len(result) == 1
    assert len(result[0].paragraphs) == 2
    assert result[0].text == "第一段落の最初の文章第一段落の続きです。\n\n第二段落の最初の文章第二段落の続きです。"
    assert result[0].line_height == pytest.approx(16.8)


def test_first_line_indentation_is_a_paragraph_boundary():
    observed = (glyph_line("最初の段落はここから", x=52)
                + glyph_line("本文は次の行に続く。", baseline=76.8, first_order=100)
                + glyph_line("次の段落の字下げです", x=52, baseline=93.6, first_order=200)
                + glyph_line("こちらも本文が続く。", baseline=110.4, first_order=300))
    result = boxes(observed)
    assert len(result) == 1
    assert [len(paragraph.lines) for paragraph in result[0].paragraphs] == [2, 2]


def test_size_change_separates_heading_and_preserves_runs_within_body():
    heading = glyph_line("見出し", style=Style("Example-Bold", 20))
    regular = glyph_line("金額は", baseline=82, first_order=100)
    bold = glyph_line("100", x=76, baseline=82, style=BOLD, first_order=200)
    final = glyph_line("円です。", x=94, baseline=82, first_order=300)
    result = boxes(heading + final + bold + regular)
    assert len(result) == 2
    line = result[1].lines[0]
    assert line.text == "金額は100円です。"
    assert [run.style for run in line.runs] == [REGULAR, BOLD, REGULAR]
    assert [run.text for run in line.runs] == ["金額は", "100", "円です。"]
    assert "100" in [word.text for word in line.words]


def test_missing_english_space_is_inferred_and_soft_wrap_preserves_word_boundary():
    observed = (glyph_line("Submit") + glyph_line("the", x=81, first_order=100)
                + glyph_line("application", baseline=76.8, first_order=200))
    result = boxes(observed)
    assert result[0].text == "Submit the application"
    synthetic = [glyph for glyph in result[0].glyphs if glyph.source_order == -1]
    assert len(synthetic) == 1
    assert synthetic[0].text == " "
    assert [word.text for word in result[0].lines[0].words] == ["Submit", "the"]


def test_japanese_tracking_does_not_invent_spaces():
    observed = glyph_line("申") + glyph_line("請", x=56, first_order=100)
    assert boxes(observed)[0].text == "申請"


def test_single_line_observed_width_is_not_page_width():
    result = boxes(glyph_line("期限"))[0]
    assert result.bbox.width == 24
    assert result.width_source == "unknown"
    assert result.observed_content_width == 24
    assert result.inferred_available_width is None
    assert result.explicitly_supplied_width is None
    assert any("uncertain" in warning for warning in result.warnings)


def test_unsupported_direction_survives_without_joining_horizontal_text():
    vertical = Glyph("縦", Rect(300, 30, 312, 42), (312, 30), 12,
                     REGULAR, (0, 1), source_order=100)
    result = boxes(glyph_line("横書き") + [vertical])
    retained = next(box for box in result if box.text == "縦")
    assert retained.glyphs == [vertical]
    assert retained.lines[0].direction == (0, 1)
    assert any("Rotated or vertical" in warning for warning in retained.warnings)


def test_empty_input_and_invalid_page():
    assert boxes([]) == []
    with pytest.raises(ValueError, match="positive"):
        infer_boxes([], 1, 0, 800)
    with pytest.raises(ValueError, match="one-based"):
        infer_boxes([], 0, 600, 800)


def test_page_number_and_box_identifiers_use_one_based_contract():
    result = infer_boxes(glyph_line("本文"), page=5, page_width=600, page_height=800)
    assert result[0].page == 5
    assert result[0].id == "p5-b1"
    assert boxes(glyph_line("本文"))[0].id == "p1-b1"


def test_slightly_rotated_text_is_marked_consistently_with_writer():
    rotated = Glyph("傾", Rect(300, 30, 312, 42), (300, 42), 12,
                    REGULAR, (0.99999, 0.005), source_order=100)
    result = boxes([rotated])
    assert result[0].lines[0].direction == rotated.direction
    assert any("Rotated or vertical" in warning for warning in result[0].warnings)


def test_ligature_unicode_span_has_observed_word_bounds():
    ligature = Glyph("ffi", Rect(40, 48, 53, 62), (40, 60), 13, REGULAR)
    line = boxes([ligature])[0].lines[0]
    assert [(word.text, word.bbox) for word in line.words] == [("ffi", ligature.bbox)]


def test_baseline_jitter_and_mixed_font_size_remain_one_line():
    observed = (glyph_line("Total") + glyph_line("100", x=70, baseline=60.7,
                                               style=Style("Example-Bold", 13), first_order=100))
    result = boxes(observed)
    assert len(result) == 1
    assert len(result[0].lines) == 1
    assert result[0].text == "Total100"


def test_double_spaced_body_preserves_measured_leading():
    observed = (glyph_line("文章の最初の行です")
                + glyph_line("文章の二番目の行は", baseline=84, first_order=100)
                + glyph_line("ここまで続きます。", baseline=108, first_order=200))
    result = boxes(observed)
    assert len(result) == 1
    assert len(result[0].paragraphs) == 1
    assert result[0].line_height == pytest.approx(24)
