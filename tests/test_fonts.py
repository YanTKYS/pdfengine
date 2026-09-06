"""Real font metrics and selection tests with in-memory PDF resources."""

from dataclasses import replace
import io

from fontTools import subset
from fontTools.ttLib import TTFont
import pymupdf
import pytest

from pdfeditor.fonts import (
    FontError,
    FontMetrics,
    embedding_restriction,
    infer_tracking,
    missing_characters,
    normalized_name,
    resolve_font,
    validate_simple_text,
)
from pdfeditor.model import Glyph, Line, Paragraph, Rect, Run, Style, TextBox


@pytest.fixture(scope="module")
def cjk_font():
    return pymupdf.Font("cjk")


@pytest.fixture(scope="module")
def latin_font():
    return pymupdf.Font("helv")


@pytest.fixture(scope="module")
def japanese_subset(cjk_font):
    """A real TrueType subset whose cmap contains only the original text."""
    with TTFont(io.BytesIO(cjk_font.buffer)) as font:
        subsetter = subset.Subsetter()
        subsetter.populate(text="申請 ")
        subsetter.subset(font)
        output = io.BytesIO()
        font.save(output)
        return output.getvalue()


def with_embedding_flags(data, flags):
    with TTFont(io.BytesIO(data)) as font:
        font["OS/2"].fsType = flags
        output = io.BytesIO()
        font.save(output)
        return output.getvalue()


def make_box(font, text="ABCD", *, tracking=0.0, residuals=None, synthetic_spaces=False):
    size = 12
    style = Style(font.name, size)
    glyphs = []
    x = 20.0
    for index, char in enumerate(text):
        advance = font.text_length(char, fontsize=size)
        bounds = Rect(x, 30 - font.ascender * size, x + advance, 30 - font.descender * size)
        glyphs.append(Glyph(
            char, bounds, (x, 30), advance, style,
            source_order=-1 if synthetic_spaces and char == " " else index,
        ))
        extra = residuals[index] if residuals is not None and index < len(residuals) else tracking
        x += advance + extra
    bounds = Rect(glyphs[0].bbox.x0, glyphs[0].bbox.y0, glyphs[-1].bbox.x1, glyphs[-1].bbox.y1)
    line = Line([Run(glyphs, style, bounds)], bounds, 30)
    return TextBox("test", 0, bounds, [Paragraph([line], text)], 16)


def test_actual_cjk_font_measurements_are_proportional(cjk_font):
    metrics = FontMetrics(cjk_font, 12)
    assert metrics.measure("W") > metrics.measure("i")
    assert metrics.measure("申") == pytest.approx(12)
    assert metrics.measure("申請Wi2026") == pytest.approx(
        sum(metrics.measure(char) for char in "申請Wi2026")
    )
    assert metrics.measure("申請iiii") < metrics.measure("申請WWWW")


def test_size_scales_width_and_font_extents(cjk_font):
    small = FontMetrics(cjk_font, 10)
    large = FontMetrics(cjk_font, 20)
    assert large.measure("申請Wi") == pytest.approx(small.measure("申請Wi") * 2)
    assert small.ascender == pytest.approx(cjk_font.ascender * 10)
    assert small.descender == pytest.approx(cjk_font.descender * 10)
    assert large.ascender == pytest.approx(small.ascender * 2)
    assert large.descender == pytest.approx(small.descender * 2)
    assert small.ascender > 0
    assert small.descender < 0


def test_tracking_is_applied_only_between_characters(cjk_font):
    plain = FontMetrics(cjk_font, 12)
    tracked = FontMetrics(cjk_font, 12, tracking=0.7)
    assert tracked.measure("") == 0
    assert tracked.measure("申") == pytest.approx(plain.measure("申"))
    assert tracked.measure("申請Wi") == pytest.approx(plain.measure("申請Wi") + 3 * 0.7)


def test_coverage_check_does_not_allow_mupdf_implicit_fallback(latin_font):
    # MuPDF can measure missing CJK through its fallback, but selection must
    # check the actual font before accepting it for deterministic output.
    assert latin_font.text_length("申請", fontsize=12) > 0
    assert set(missing_characters(latin_font, "申請A\n申")) == {"申", "請"}
    assert missing_characters(latin_font, "Wi2026\n") == []


def test_base14_resource_keeps_known_font_identity():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_font(fontname="helv")
        selected = resolve_font(document, 0, "Helvetica", "Application 2026")
        assert selected.preserved
        assert selected.source == "base14:helv"
        assert selected.font.name == "Helvetica"


def test_complete_embedded_font_is_reused(cjk_font):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_font(fontname="original", fontbuffer=cjk_font.buffer)
        resource = document.get_page_fonts(0, full=True)[0]
        selected = resolve_font(document, 0, resource[3], "申請期限は10月15日です。")
        assert selected.preserved
        assert selected.source == f"embedded:{resource[0]}"
        assert missing_characters(selected.font, "申請期限は10月15日です。") == []
        assert selected.report()["preserved"] is True


def test_embedded_font_with_missing_glyphs_triggers_reported_fallback(latin_font):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_font(fontname="originalLatin", fontbuffer=latin_font.buffer)
        resource = document.get_page_fonts(0, full=True)[0]
        selected = resolve_font(document, 0, resource[3], "申請期限")
        assert not selected.preserved
        assert selected.source.startswith("bundled:")
        assert missing_characters(selected.font, "申請期限") == []
        assert any("cannot encode" in reason for reason in selected.reasons)
        assert any("U+7533" in reason for reason in selected.reasons)
        assert selected.report()["original"] == resource[3]


def test_real_japanese_subset_preserves_available_text_and_falls_back_for_new_glyphs(japanese_subset):
    subset_font = pymupdf.Font(fontbuffer=japanese_subset)
    assert missing_characters(subset_font, "申請") == []
    assert missing_characters(subset_font, "申請期限") == ["期", "限"]
    assert subset_font.has_glyph(ord("期"), fallback=False) == 0
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_font(fontname="originalSubset", fontbuffer=japanese_subset)
        resource = document.get_page_fonts(0, full=True)[0]
        preserved = resolve_font(document, 0, resource[3], "申請")
        assert preserved.preserved
        assert preserved.source == f"embedded:{resource[0]}"

        replacement = resolve_font(document, 0, resource[3], "申請期限")
        assert not replacement.preserved
        assert replacement.source.startswith("bundled:")
        assert any("cannot encode" in reason for reason in replacement.reasons)
        assert any("U+671F" in reason for reason in replacement.reasons)
        assert any("U+9650" in reason for reason in replacement.reasons)
        assert missing_characters(replacement.font, "申請期限") == []
        assert replacement.font.has_glyph(ord("期"), fallback=False) != 0


@pytest.mark.parametrize("flags, expected_reason", [
    (0x2, "restricts embedding"),
    (0x200, "bitmap embedding only"),
    (0x4, "preview/print embedding but not editable"),
    (0x8, None),
    (0x100, None),
    (0x108, None),
    (0xC, None),
    (0x0, None),
])
def test_embedding_permissions_distinguish_editable_from_preview_only(japanese_subset, flags, expected_reason):
    font_data = with_embedding_flags(japanese_subset, flags)
    reason = embedding_restriction(font_data)
    if expected_reason is None:
        assert reason is None
    else:
        assert expected_reason in reason


@pytest.mark.parametrize("flags", [0x2, 0x200, 0x4])
def test_restricted_embedded_font_triggers_explained_fallback(japanese_subset, flags):
    font_data = with_embedding_flags(japanese_subset, flags)
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_font(fontname="restricted", fontbuffer=font_data)
        resource = document.get_page_fonts(0, full=True)[0]
        selected = resolve_font(document, 0, resource[3], "申請")
        assert not selected.preserved
        assert selected.source.startswith("bundled:")
        assert any("fsType" in reason for reason in selected.reasons)
        assert missing_characters(selected.font, "申請") == []


@pytest.mark.parametrize("flags", [0x8, 0x100])
def test_editable_and_no_subsetting_fonts_can_be_reused_whole(japanese_subset, flags):
    font_data = with_embedding_flags(japanese_subset, flags)
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_font(fontname="allowed", fontbuffer=font_data)
        resource = document.get_page_fonts(0, full=True)[0]
        selected = resolve_font(document, 0, resource[3], "申請")
        assert selected.preserved
        assert selected.source == f"embedded:{resource[0]}"
        # The selected program retains its fsType; no unrequested subsetting
        # or license-flag rewriting takes place during resolution.
        with TTFont(io.BytesIO(selected.font.buffer)) as font:
            assert font["OS/2"].fsType == flags


def test_unknown_font_fallback_records_missing_resource():
    with pymupdf.open() as document:
        document.new_page()
        selected = resolve_font(document, 0, "UnknownCustomFace", "申請期限")
        assert not selected.preserved
        assert any("matching font resource" in reason for reason in selected.reasons)
        assert any("original face identity" in reason for reason in selected.reasons)


def test_missing_glyph_in_fallback_is_an_error_instead_of_an_empty_box():
    with pymupdf.open() as document:
        document.new_page()
        with pytest.raises(FontError, match="U\\+10FFFF"):
            resolve_font(document, 0, "UnknownCustomFace", "申請\U0010ffff")


def test_provided_font_is_used_when_original_is_unavailable(cjk_font, tmp_path):
    supplied = tmp_path / "replacement.ttf"
    supplied.write_bytes(cjk_font.buffer)
    with pymupdf.open() as document:
        document.new_page()
        selected = resolve_font(document, 0, "UnknownCustomFace", "申請期限", supplied)
        assert selected.source == "provided:replacement.ttf"
        assert not selected.preserved
        assert missing_characters(selected.font, "申請期限") == []


def test_provided_font_with_missing_glyph_is_rejected(latin_font, tmp_path):
    supplied = tmp_path / "latin.cff"
    supplied.write_bytes(latin_font.buffer)
    with pymupdf.open() as document:
        document.new_page()
        with pytest.raises(FontError, match="no glyphs"):
            resolve_font(document, 0, "UnknownCustomFace", "申請期限", supplied)


def test_subset_prefix_and_punctuation_do_not_change_font_matching():
    assert normalized_name("ABCDEF+NotoSansJP-Regular") == normalized_name("Noto Sans JP Regular")
    assert normalized_name("Helvetica-Bold") != normalized_name("Helvetica")


@pytest.mark.parametrize("spacing", [0.0, 0.6, -0.4])
def test_uniform_tracking_is_recovered_from_glyph_origins(latin_font, spacing):
    box = make_box(latin_font, tracking=spacing)
    tracking, warnings = infer_tracking(box, latin_font, 12, preserved=True)
    assert tracking == pytest.approx(spacing)
    assert warnings == []


def test_tracking_noise_below_threshold_is_zeroed(latin_font):
    box = make_box(latin_font, tracking=0.01)
    assert infer_tracking(box, latin_font, 12, preserved=True) == (0.0, [])


def test_nonuniform_spacing_is_rejected(latin_font):
    box = make_box(latin_font, residuals=[0.0, 1.0, 0.0])
    with pytest.raises(FontError, match="nonuniform"):
        infer_tracking(box, latin_font, 12, preserved=True)


def test_custom_pdf_glyph_widths_are_not_mistaken_for_tracking(latin_font):
    box = make_box(latin_font)
    original = box.lines[0].runs[0].glyphs[0]
    box.lines[0].runs[0].glyphs[0] = replace(original, advance=original.advance * 1.2)
    with pytest.raises(FontError, match="PDF glyph widths differ"):
        infer_tracking(box, latin_font, 12, preserved=True)


def test_fallback_font_resets_tracking_with_a_reason(latin_font):
    box = make_box(latin_font, tracking=0.6)
    tracking, warnings = infer_tracking(box, latin_font, 12, preserved=False)
    assert tracking == 0
    assert len(warnings) == 1
    assert "original font metrics are unavailable" in warnings[0]


def test_single_glyph_has_no_tracking_evidence(latin_font):
    box = make_box(latin_font, text="A")
    assert infer_tracking(box, latin_font, 12, preserved=True) == (0.0, [])


def test_explicit_word_spaces_are_excluded_from_tracking(latin_font):
    box = make_box(latin_font, text="ABC DEF", tracking=0.4)
    assert infer_tracking(box, latin_font, 12, preserved=True)[0] == pytest.approx(0.4)


def test_inferred_word_spaces_are_excluded_from_tracking(latin_font):
    box = make_box(latin_font, text="ABC DEF", tracking=0.4, synthetic_spaces=True)
    assert infer_tracking(box, latin_font, 12, preserved=True)[0] == pytest.approx(0.4)


def test_reversed_origin_pair_is_not_tracking_evidence(latin_font):
    box = make_box(latin_font, text="AB")
    original = box.lines[0].runs[0].glyphs[1]
    box.lines[0].runs[0].glyphs[1] = replace(original, origin=(10, 30))
    assert infer_tracking(box, latin_font, 12, preserved=True) == (0.0, [])


def test_horizontal_japanese_latin_and_newline_are_accepted():
    validate_simple_text("申請期限は2026年10月15日です。\nApplication No. 123")


@pytest.mark.parametrize("text", ["A\u0301", "\u0645", "\u0915", "\t", "\u200d"])
def test_scripts_or_controls_requiring_shaping_are_explicitly_rejected(text):
    with pytest.raises(FontError, match="requires shaping/control"):
        validate_simple_text(text)
