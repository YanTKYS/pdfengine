"""Width-based, PDF-independent horizontal paragraph layout.

Coordinates and font metrics are in points, with y increasing down the page.
Unicode line-break opportunities come from UAX #14 (uniseg). Only an otherwise
unbreakable token uses emergency grapheme breaks; Japanese prohibited line
starts and ends remain prohibited in that case.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite

from uniseg.graphemecluster import grapheme_cluster_boundaries
from uniseg.linebreak import line_break_boundaries

from .model import Rect


class LayoutError(ValueError):
    """The requested content cannot be safely laid out in the target region."""


@dataclass(frozen=True)
class LayoutLine:
    text: str
    x: float
    baseline: float
    width: float
    paragraph_index: int = 0


@dataclass(frozen=True)
class LayoutResult:
    lines: list[LayoutLine]
    bbox: Rect
    line_height: float


# Strict Japanese kinsoku tailoring also protects emergency token breaks.
# Whitespace between Latin words is removed at composed line endings.
# Non-breaking spaces are deliberately excluded from this whitespace set.
_SPACES = " \t\u3000"
_NO_START = frozenset(
    "、。，．・：；？！!?,.:;)]}｝〕〉》」』】〙〗〟’”｠»"
    "ヽヾゝゞ々ーｰァィゥェォッャュョヮヵヶぁぃぅぇぉっゃゅょゎゕゖ"
    "ㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ"
)
_NO_END = frozenset("([{｛〔〈《「『【〘〖〝‘“｟«")
_EPSILON = 1e-7


def _safe_boundary(text: str, start: int, end: int) -> bool:
    """Apply strict Japanese line-head/line-tail rules to visible characters."""
    left = text[start:end].rstrip(_SPACES)
    right = text[end:].lstrip(_SPACES)
    return not (left and left[-1] in _NO_END or right and right[0] in _NO_START)


def _wrap(text: str, width: float, measure: Callable[[str], float]) -> list[tuple[str, float]]:
    if not text:
        return [("", 0.0)]
    if text.lstrip(_SPACES) and text.lstrip(_SPACES)[0] in _NO_START:
        raise LayoutError("A forced line begins with prohibited Japanese closing punctuation.")

    legal = list(line_break_boundaries(text))
    graphemes = list(grapheme_cluster_boundaries(text))
    result: list[tuple[str, float]] = []
    start = 0
    while start < len(text):
        chosen: tuple[int, str, float] | None = None
        # Prefer ordinary Unicode opportunities: a Latin word stays together
        # whenever it can fit on an empty line.
        for end in legal:
            if end <= start:
                continue
            candidate = text[start:end].rstrip(_SPACES)
            measured = measure(candidate)
            if measured > width + _EPSILON:
                break
            if _safe_boundary(text, start, end):
                chosen = end, candidate, measured
        if chosen is None:
            # A single word can be wider than the box. Emergency wrapping is
            # at extended grapheme boundaries, never at a code-point count.
            for end in graphemes:
                if end <= start:
                    continue
                candidate = text[start:end].rstrip(_SPACES)
                measured = measure(candidate)
                if measured > width + _EPSILON:
                    break
                if _safe_boundary(text, start, end):
                    chosen = end, candidate, measured
        if chosen is None:
            raise LayoutError(
                "The text box is too narrow for a grapheme or an unbreakable Japanese punctuation group."
            )
        end, line, measured = chosen
        result.append((line, measured))
        start = end
        while start < len(text) and text[start] in _SPACES:
            start += 1
    return result


def layout_text(
    text: str,
    *,
    x: float,
    baseline: float,
    width: float,
    line_height: float,
    ascender: float,
    descender: float,
    measure: Callable[[str], float],
    max_bottom: float | None = None,
) -> LayoutResult:
    """Reflow text using actual measured advances and a fixed region width.

    ``ascender`` is a positive distance above the first baseline; ``descender``
    is a negative distance below it. Neither is a normalized font ratio.
    A single newline forces a line break. Two newlines separate paragraphs
    with an empty line, preserving authored paragraph spacing. Empty input
    has no lines and zero height. The returned bbox retains the region width
    and grows or shrinks vertically to the newly composed line count.

    ``measure`` measures complete candidate strings, so its implementation
    can account for proportional advances and font-specific spacing. It must
    return finite, nonnegative widths in points. Greedy line fitting assumes
    adding a grapheme does not reduce the measured advance.
    """
    if not isinstance(text, str):
        raise LayoutError("Text must be a Unicode string.")
    numbers = (x, baseline, width, line_height, ascender, descender)
    if not all(isfinite(value) for value in numbers):
        raise LayoutError("Layout coordinates and font metrics must be finite.")
    if width <= 0 or line_height <= 0:
        raise LayoutError("Text box width and line height must be positive.")
    if ascender < 0 or descender > 0 or ascender - descender <= 0:
        raise LayoutError("Ascender must be nonnegative and descender nonpositive, in points.")
    if max_bottom is not None and not isfinite(max_bottom):
        raise LayoutError("The bottom limit must be finite.")

    top = baseline - ascender
    if text == "":
        return LayoutResult([], Rect(x, top, x + width, top), line_height)

    widths: dict[str, float] = {"": 0.0}

    def measured(candidate: str) -> float:
        if candidate not in widths:
            value = measure(candidate)
            if not isfinite(value) or value < 0:
                raise LayoutError("Font measurement returned an invalid width.")
            widths[candidate] = value
        return widths[candidate]

    lines: list[LayoutLine] = []

    def append(line: str, line_width: float, paragraph_index: int) -> None:
        line_baseline = baseline + len(lines) * line_height
        if max_bottom is not None and line_baseline - descender > max_bottom + _EPSILON:
            raise LayoutError("Reflowed text exceeds the available vertical space.")
        lines.append(LayoutLine(line, x, line_baseline, line_width, paragraph_index))

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for paragraph_index, paragraph in enumerate(normalized.split("\n\n")):
        if paragraph_index:
            append("", 0.0, paragraph_index - 1)
        for forced_line in paragraph.split("\n"):
            for line, line_width in _wrap(forced_line, width, measured):
                append(line, line_width, paragraph_index)

    bottom = lines[-1].baseline - descender
    return LayoutResult(lines, Rect(x, top, x + width, bottom), line_height)
