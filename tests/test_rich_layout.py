"""Independent layout contracts, using deliberate contextual shape callbacks."""

from math import inf, nan

import pytest

from pdfeditor.layout import LayoutError
from pdfeditor.model import Rect
from pdfeditor.rich_layout import InlineGlyph, layout_attributed


def _glyph(text, advance=1.0, *, ascent=8.0, descent=2.0, ink=None, payload=None):
    return InlineGlyph(text, advance, ink, ascent, descent, payload)


def _layout(text, shape=None, **changes):
    arguments = dict(shape=shape or (lambda start, end: [_glyph(c) for c in text[start:end]]),
                     x=10, baseline=20, width=10, min_line_height=12,
                     max_bottom=200, empty_ascent=8, empty_descent=2)
    arguments.update(changes)
    return layout_attributed(text, **arguments)


def test_complete_candidates_keep_context_and_ligature_unicode():
    text = "office next"
    calls = []

    def shape(start, end):
        candidate = text[start:end]
        calls.append((start, end))
        if candidate == "office":
            return [_glyph("o", 2), _glyph("ffi", 3, payload="ligature"), _glyph("c", 2), _glyph("e", 2)]
        return [_glyph(character, 2) for character in candidate]

    result = _layout(text, shape, width=10)
    assert [line.text for line in result.lines] == ["office", "next"]
    assert [line.width for line in result.lines] == [9, 8]
    ligature = result.glyphs[1]
    assert (ligature.start, ligature.end, ligature.glyph.text) == (1, 4, "ffi")
    assert ligature.glyph.payload == "ligature"
    assert (0, 6) in calls and (7, 11) in calls
    assert len(calls) == len(set(calls))


def test_repeated_words_with_different_formatting_use_original_offsets():
    text = "aa aa"

    def shape(start, end):
        return [_glyph(text[index], 3 if index < 2 else 6, payload=index)
                for index in range(start, end)]

    result = _layout(text, shape, width=12)
    assert [(line.start, line.end, line.width) for line in result.lines] == [(0, 2, 6), (3, 5, 12)]
    assert [glyph.glyph.payload for glyph in result.glyphs] == [0, 1, 3, 4]


def test_context_can_make_later_candidate_fit_after_earlier_overflow():
    text = "A B"

    def shape(start, end):
        candidate = text[start:end]
        return [_glyph(candidate, 8 if candidate == text else 12)]

    result = _layout(text, shape, width=8)
    assert [line.text for line in result.lines] == [text]
    assert result.lines[0].width == 8


def test_sidebearings_count_toward_width_and_negative_left_is_inset():
    text = "ab"

    def shape(start, end):
        return [_glyph(text[index], 5, ink=Rect(-2, -7, 7, 2)) for index in range(start, end)]

    result = _layout(text, shape, width=14)
    line = result.lines[0]
    assert line.width == 14 and line.inset == 2
    assert [glyph.x for glyph in line.glyphs] == [12, 17]
    assert line.glyphs[0].ink == Rect(10, 13, 19, 22)
    assert line.glyphs[-1].ink.x1 == 24
    assert result.bbox == Rect(10, 12, 24, 22)
    wrapped = _layout(text, shape, width=9)
    assert [line.text for line in wrapped.lines] == ["a", "b"]
    with pytest.raises(LayoutError, match="too narrow"):
        _layout(text, shape, width=8)


def test_mixed_sizes_use_max_metrics_and_neighboring_line_extents():
    text = "aB\nc\nd"
    metrics = {"a": (8, 2), "B": (20, 5), "c": (8, 2), "d": (16, 4)}

    def shape(start, end):
        return [_glyph(c, ascent=metrics[c][0], descent=metrics[c][1]) for c in text[start:end]]

    result = _layout(text, shape, min_line_height=10)
    assert [(line.ascent, line.descent) for line in result.lines] == [(20, 5), (8, 2), (16, 4)]
    assert [line.baseline for line in result.lines] == [20, 33, 51]
    assert result.bbox == Rect(10, 0, 12, 55)
    assert result.lines[0].baseline + result.lines[0].descent == result.lines[1].baseline - result.lines[1].ascent


def test_ink_outside_font_metrics_contributes_to_line_height_and_bottom_limit():
    text = "a\nb"

    def shape(start, end):
        return [_glyph(c, ink=Rect(0, -10, 1, 7)) for c in text[start:end]]

    result = _layout(text, shape, min_line_height=10, max_bottom=44)
    assert [line.baseline for line in result.lines] == [20, 37]
    assert result.bbox.y1 == 44
    with pytest.raises(LayoutError, match="vertical space"):
        _layout(text, shape, min_line_height=10, max_bottom=43.99)


def test_hard_breaks_blank_lines_and_trimmed_spaces_keep_source_offsets():
    text = "aa  bb\r\n\ncc\r"
    result = _layout(text, width=2)
    assert [(line.text, line.start, line.end) for line in result.lines] == [
        ("aa", 0, 2), ("bb", 4, 6), ("", 8, 8), ("cc", 9, 11), ("", 12, 12)]
    assert [line.baseline for line in result.lines] == [20, 32, 44, 56, 68]
    assert result.lines[2].glyphs == []
    assert result.lines[2].ascent == 8 and result.lines[2].descent == 2
    assert all(text[g.start:g.end] == g.glyph.text for g in result.glyphs)


def test_japanese_breaks_share_kinsoku_rules():
    result = _layout("あいう、えお", width=3)
    assert [line.text for line in result.lines] == ["あい", "う、え", "お"]
    result = _layout("あ「いう」え", width=3)
    assert [line.text for line in result.lines] == ["あ「い", "う」え"]
    assert all(not line.text.startswith("、") and not line.text.endswith("「") for line in result.lines)
    with pytest.raises(LayoutError, match="prohibited Japanese"):
        _layout("あ\n、い", width=3)


def test_emergency_break_never_splits_extended_grapheme():
    text = "e\u0301x"
    calls = []

    def shape(start, end):
        calls.append(text[start:end])
        if text[start:end] == text:
            return [_glyph("e\u0301", 2), _glyph("x", 1)]
        return [_glyph(text[start:end], 2 if text[start:end] == "e\u0301" else 1)]

    result = _layout(text, shape, width=2)
    assert [line.text for line in result.lines] == ["e\u0301", "x"]
    assert "e" not in calls and "\u0301" not in calls
    with pytest.raises(LayoutError, match="too narrow"):
        _layout(text, shape, width=1)


@pytest.mark.parametrize("change", [dict(width=0), dict(width=nan), dict(min_line_height=-1),
                                   dict(baseline=inf), dict(max_bottom=nan),
                                   dict(empty_ascent=-1), dict(empty_ascent=0, empty_descent=0)])
def test_invalid_region_and_metrics_refused(change):
    with pytest.raises(LayoutError):
        _layout("x", **change)


@pytest.mark.parametrize("glyph", [
    _glyph("x", -1), _glyph("x", nan), _glyph("x", inf),
    _glyph("x", ascent=-1), _glyph("x", descent=nan),
    _glyph("x", ink=Rect(0, 0, -1, 1)), _glyph("x", ink=Rect(0, 0, 1, inf)),
    _glyph("x", ascent=0, descent=0), _glyph("wrong"), _glyph(""),
])
def test_invalid_shape_output_refused(glyph):
    with pytest.raises(LayoutError):
        _layout("x", lambda start, end: [glyph])


def test_empty_input_has_no_lines_and_does_not_call_shaper():
    def should_not_shape(start, end):
        raise AssertionError("Empty lines must not be shaped.")

    result = _layout("", should_not_shape)
    assert result.lines == [] and result.glyphs == []
    assert result.bbox == Rect(10, 12, 10, 12)
    blanks = _layout("\n", should_not_shape)
    assert [line.text for line in blanks.lines] == ["", ""]


def test_first_visual_line_indent_reduces_width_and_moves_origin_only_once():
    result = _layout("ab cd ef", width=5, first_line_indent=2)
    assert [(line.text, line.x, line.width) for line in result.lines] == [
        ("ab", 12, 2), ("cd ef", 10, 5)]
    assert result.lines[0].glyphs[0].origin == (12, 20)
    assert result.lines[1].glyphs[0].origin == (10, 32)
    assert result.bbox == Rect(10, 12, 15, 34)
    single = _layout("ab", width=5, first_line_indent=2)
    assert single.bbox == Rect(12, 12, 14, 22)


def test_first_line_indent_does_not_restart_after_hard_or_blank_line():
    result = _layout("\nab\ncd", width=4, first_line_indent=3)
    assert [(line.text, line.x) for line in result.lines] == [("", 13), ("ab", 10), ("cd", 10)]
    with pytest.raises(LayoutError, match="too narrow"):
        _layout("x", width=4, first_line_indent=3.5)


@pytest.mark.parametrize("indent", [-1, 10, 11, nan, inf])
def test_invalid_first_line_indent_refused(indent):
    with pytest.raises(LayoutError):
        _layout("x", first_line_indent=indent)
