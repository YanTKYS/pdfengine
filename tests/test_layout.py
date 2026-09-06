"""Pure layout checks: no PDF reader, writer, or fixture is needed."""

import unicodedata

import pytest

from pdfeditor.layout import LayoutError, layout_text
from pdfeditor.model import Rect


def variable_measure(text: str) -> float:
    """A proportional test font: 10pt CJK, 8pt W, 2pt i, 3pt space."""
    return sum(
        0 if unicodedata.combining(char) else
        3 if char == " " else
        2 if char == "i" else
        8 if char == "W" else
        10 if ord(char) > 255 else 5
        for char in text
    )


def layout(text: str, **overrides):
    options = dict(
        x=20.0, baseline=30.0, width=50.0, line_height=14.0,
        ascender=9.0, descender=-3.0, measure=variable_measure,
    )
    options.update(overrides)
    return layout_text(text, **options)


def texts(result):
    return [line.text for line in result.lines]


def test_short_replacement_fits_without_a_fixed_character_limit():
    assert texts(layout("申請")) == ["申請"]
    result = layout("申請期限")
    assert texts(result) == ["申請期限"]
    assert result.lines[0].width == 40
    assert result.lines[0].x == 20


def test_longer_text_wraps_and_shorter_text_reclaims_height():
    longer = layout("申請書類提出期限変更確認")
    shorter = layout("申請")
    assert len(longer.lines) == 3
    assert longer.bbox == Rect(20, 21, 70, 61)
    assert shorter.bbox == Rect(20, 21, 70, 33)
    assert shorter.bbox.height < longer.bbox.height


def test_exact_fit_uses_one_line():
    result = layout("申請期限日")
    assert len(result.lines) == 1
    assert result.lines[0].width == 50


def test_mixed_script_uses_proportional_metrics():
    narrow = layout("申請 iiiiii", width=35)
    wide = layout("申請 WWWWWW", width=35)
    assert len(narrow.lines) == 1
    assert len(wide.lines) == 3
    for result in (narrow, wide):
        assert all(line.width <= 35 for line in result.lines)
        assert all(line.width == variable_measure(line.text) for line in result.lines)


def test_measurement_receives_whole_candidates_for_contextual_advances():
    # A simple ligature fixture: "fi" takes 6pt instead of two 5pt advances.
    def contextual_measure(text):
        return len(text) * 5 - text.count("fi") * 4

    result = layout("ffi", width=11, measure=contextual_measure)
    assert texts(result) == ["ffi"]
    assert result.lines[0].width == 11


def test_word_breaks_precede_emergency_breaks():
    result = layout("alpha beta gamma", width=45)
    assert texts(result) == ["alpha", "beta", "gamma"]
    assert all(not line.text.endswith(" ") for line in result.lines)


def test_an_overlong_latin_token_can_wrap():
    result = layout("WWWWWW", width=17)
    assert texts(result) == ["WW", "WW", "WW"]


def test_combining_characters_are_not_split_from_their_base():
    result = layout("A\u0301B\u0301C\u0301", width=5)
    assert texts(result) == ["A\u0301", "B\u0301", "C\u0301"]


def test_emoji_zwj_sequence_is_not_split():
    family = "\U0001f469\u200d\U0001f469\u200d\U0001f467"
    result = layout(family + family, width=50)
    assert texts(result) == [family, family]
    with pytest.raises(LayoutError, match="too narrow"):
        layout(family, width=49)


def test_japanese_closing_punctuation_never_begins_an_automatic_line():
    result = layout("申請書類、提出期限。", width=40)
    assert texts(result) == ["申請書", "類、提出", "期限。"]


def test_japanese_opening_punctuation_never_ends_an_automatic_line():
    result = layout("申請「期限」確認", width=30)
    assert texts(result) == ["申請", "「期", "限」確", "認"]
    assert all(not line.text.endswith("「") for line in result.lines)
    assert all(not line.text.startswith("」") for line in result.lines)
    assert "".join(texts(result)) == "申請「期限」確認"


def test_emergency_breaks_preserve_kinsoku():
    result = layout("WWWW、申請", width=27)
    assert texts(result) == ["WWW", "W、", "申請"]
    with pytest.raises(LayoutError, match="too narrow"):
        layout("申、", width=10)
    with pytest.raises(LayoutError, match="closing punctuation"):
        layout("、申請")


def test_small_kana_and_prolonged_sound_mark_do_not_start_a_line():
    result = layout("キャリアサービス", width=30)
    assert all(not line.text.startswith(("ャ", "ー")) for line in result.lines)
    assert "".join(texts(result)) == "キャリアサービス"


def test_forced_newline_stays_in_the_same_paragraph():
    result = layout("申請\n期限")
    assert texts(result) == ["申請", "期限"]
    assert [line.paragraph_index for line in result.lines] == [0, 0]
    assert [line.baseline for line in result.lines] == [30, 44]


def test_blank_line_preserves_paragraph_structure_and_spacing():
    result = layout("申請\n期限\n\n確認")
    assert texts(result) == ["申請", "期限", "", "確認"]
    assert [line.paragraph_index for line in result.lines] == [0, 0, 0, 1]
    assert result.lines[-1].baseline == 72


def test_windows_newlines_are_normalized():
    assert texts(layout("申請\r\n期限\r確認")) == ["申請", "期限", "確認"]


def test_empty_text_has_zero_drawn_lines_and_zero_height():
    result = layout("")
    assert result.lines == []
    assert result.bbox == Rect(20, 21, 70, 21)


def test_bottom_limit_uses_descent_and_accepts_exact_fit():
    assert layout("申請", max_bottom=33).bbox.y1 == 33
    with pytest.raises(LayoutError, match="vertical space"):
        layout("申請", max_bottom=32.9)
    with pytest.raises(LayoutError, match="vertical space"):
        layout("申請書類提出期限変更確認", max_bottom=50)


@pytest.mark.parametrize("overrides", [
    {"width": 0}, {"width": -1}, {"line_height": 0}, {"ascender": -1},
    {"descender": 1}, {"baseline": float("nan")}, {"width": float("inf")},
    {"max_bottom": float("nan")},
])
def test_invalid_geometry_is_rejected(overrides):
    with pytest.raises(LayoutError):
        layout("申請", **overrides)


@pytest.mark.parametrize("invalid", [-1, float("nan"), float("inf")])
def test_invalid_font_measurement_is_rejected(invalid):
    with pytest.raises(LayoutError, match="measurement"):
        layout("申請", measure=lambda _: invalid)
