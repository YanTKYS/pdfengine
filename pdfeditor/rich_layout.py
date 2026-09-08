"""PDF-independent line layout with per-glyph metrics and source text offsets.

The shaping callback receives offsets into the original Unicode string. It
shapes each complete candidate, so contextual advances and repeated text with
different formatting are not confused. Coordinates are points, y down.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
import re

from uniseg.graphemecluster import grapheme_cluster_boundaries
from uniseg.linebreak import line_break_boundaries

from .layout import LayoutError, _EPSILON, _NO_START, _SPACES, _safe_boundary
from .model import Rect


@dataclass(frozen=True)
class InlineGlyph:
    text: str
    advance: float
    ink: Rect | None
    ascent: float
    descent: float
    payload: object = None


@dataclass(frozen=True)
class PlacedInlineGlyph:
    glyph: InlineGlyph
    x: float
    baseline: float
    start: int
    end: int

    @property
    def origin(self) -> tuple[float, float]:
        return self.x, self.baseline

    @property
    def ink(self) -> Rect | None:
        ink = self.glyph.ink
        if ink is None:
            return None
        return Rect(self.x + ink.x0, self.baseline + ink.y0,
                    self.x + ink.x1, self.baseline + ink.y1)


@dataclass(frozen=True)
class AttributedLine:
    text: str
    start: int
    end: int
    x: float
    baseline: float
    width: float
    ascent: float
    descent: float
    inset: float
    glyphs: list[PlacedInlineGlyph]


@dataclass(frozen=True)
class AttributedLayout:
    lines: list[AttributedLine]
    bbox: Rect
    glyphs: list[PlacedInlineGlyph]


@dataclass(frozen=True)
class _Measured:
    glyphs: list[InlineGlyph]
    width: float
    inset: float
    ascent: float
    descent: float


def layout_attributed(
    text: str,
    *,
    shape: Callable[[int, int], list[InlineGlyph]],
    x: float,
    baseline: float,
    width: float,
    min_line_height: float,
    max_bottom: float | None,
    empty_ascent: float,
    empty_descent: float,
    first_line_indent: float = 0.0,
) -> AttributedLayout:
    """Fit attributed text within an explicitly supplied horizontal region.

    ``InlineGlyph.ink`` is relative to its own baseline origin. Glyph ascent
    and descent are nonnegative distances, not normalized font ratios. Actual
    ink extending beyond those metrics also contributes to the line extents.
    Negative left side bearings are inset, and right overhangs count toward
    fitting width. Adjacent baselines are separated by at least the preceding
    descent plus the following ascent, or ``min_line_height`` if larger.

    Unicode opportunities, grapheme emergency breaks, Japanese kinsoku and
    trailing-space removal follow :mod:`pdfeditor.layout`. A newline forces a
    line break; CRLF and CR also work without changing source offsets. Every
    blank authored line uses the supplied empty-line metrics. Empty input has
    no lines. ``bbox`` encloses occupied line widths and their vertical metrics
    (including ink), rather than expanding to the entire available width.
    ``first_line_indent`` reserves space at the left of the first visual line
    only; subsequent lines, including lines after a hard break, use full width.
    """
    if not isinstance(text, str):
        raise LayoutError("Text must be a Unicode string.")
    numbers = (x, baseline, width, min_line_height, empty_ascent, empty_descent,
               first_line_indent)
    if not all(isfinite(value) for value in numbers):
        raise LayoutError("Layout coordinates and font metrics must be finite.")
    if width <= 0 or min_line_height <= 0:
        raise LayoutError("Text box width and minimum line height must be positive.")
    if first_line_indent < 0 or first_line_indent >= width:
        raise LayoutError("First-line indentation must be nonnegative and smaller than the width.")
    if empty_ascent < 0 or empty_descent < 0 or empty_ascent + empty_descent <= 0:
        raise LayoutError("Empty-line ascent and descent must define a positive height.")
    if max_bottom is not None and not isfinite(max_bottom):
        raise LayoutError("The bottom limit must be finite.")
    if not callable(shape):
        raise LayoutError("A shaping callback is required.")
    if not text:
        top = baseline - empty_ascent
        return AttributedLayout([], Rect(x, top, x, top), [])

    cache: dict[tuple[int, int], _Measured] = {}

    def measured(start: int, end: int) -> _Measured:
        key = start, end
        if key in cache:
            return cache[key]
        if start == end:
            result = _Measured([], 0.0, 0.0, empty_ascent, empty_descent)
            cache[key] = result
            return result
        glyphs = list(shape(start, end))
        if any(not isinstance(glyph, InlineGlyph) for glyph in glyphs):
            raise LayoutError("Shaping must return InlineGlyph values.")
        if any(not isinstance(glyph.text, str) or not glyph.text for glyph in glyphs):
            raise LayoutError("Every shaped glyph must retain its Unicode text.")
        if "".join(glyph.text for glyph in glyphs) != text[start:end]:
            raise LayoutError("Shaped glyph text does not match the requested source range.")
        pen = left = right = ascent = descent = 0.0
        for glyph in glyphs:
            metrics = glyph.advance, glyph.ascent, glyph.descent
            if not all(isfinite(value) and value >= 0 for value in metrics):
                raise LayoutError("Glyph advances and metrics must be finite and nonnegative.")
            if glyph.ascent + glyph.descent <= 0:
                raise LayoutError("Glyph ascent and descent must define a positive height.")
            ascent = max(ascent, glyph.ascent)
            descent = max(descent, glyph.descent)
            ink = glyph.ink
            if ink is not None:
                if (not isinstance(ink, Rect)
                        or not all(isfinite(value) for value in ink.tuple())
                        or ink.x1 < ink.x0 or ink.y1 < ink.y0):
                    raise LayoutError("Glyph ink bounds must be finite and ordered.")
                left = min(left, pen + ink.x0)
                right = max(right, pen + ink.x1)
                ascent = max(ascent, -ink.y0)
                descent = max(descent, ink.y1)
            pen += glyph.advance
            right = max(right, pen)
            if not isfinite(pen) or not isfinite(right - left):
                raise LayoutError("The shaped line width exceeds finite coordinates.")
        result = _Measured(glyphs, right - left, -left, ascent, descent)
        cache[key] = result
        return result

    lines: list[AttributedLine] = []
    placed: list[PlacedInlineGlyph] = []

    def append(start: int, end: int, metrics: _Measured) -> None:
        line_baseline = baseline
        if lines:
            previous = lines[-1]
            line_baseline = previous.baseline + max(
                min_line_height, previous.descent + metrics.ascent)
        if not isfinite(line_baseline + metrics.descent):
            raise LayoutError("The composed line exceeds finite coordinates.")
        if max_bottom is not None and line_baseline + metrics.descent > max_bottom + _EPSILON:
            raise LayoutError("Reflowed text exceeds the available vertical space.")
        line_x = x + (first_line_indent if not lines else 0.0)
        pen, offset = line_x + metrics.inset, start
        line_glyphs: list[PlacedInlineGlyph] = []
        for glyph in metrics.glyphs:
            next_offset = offset + len(glyph.text)
            item = PlacedInlineGlyph(glyph, pen, line_baseline, offset, next_offset)
            line_glyphs.append(item)
            pen += glyph.advance
            offset = next_offset
        lines.append(AttributedLine(text[start:end], start, end, line_x, line_baseline,
                                    metrics.width, metrics.ascent, metrics.descent,
                                    metrics.inset, line_glyphs))
        placed.extend(line_glyphs)

    def wrap(start: int, end: int) -> None:
        forced = text[start:end]
        if not forced:
            append(start, end, measured(start, end))
            return
        if forced.lstrip(_SPACES) and forced.lstrip(_SPACES)[0] in _NO_START:
            raise LayoutError("A forced line begins with prohibited Japanese closing punctuation.")
        legal = list(line_break_boundaries(forced))
        graphemes = list(grapheme_cluster_boundaries(forced))
        cursor = 0
        while cursor < len(forced):
            available_width = width - (first_line_indent if not lines else 0.0)
            chosen: tuple[int, int, _Measured] | None = None
            for boundaries in (legal, graphemes):
                for boundary in boundaries:
                    if boundary <= cursor or not _safe_boundary(forced, cursor, boundary):
                        continue
                    trimmed = boundary
                    while trimmed > cursor and forced[trimmed - 1] in _SPACES:
                        trimmed -= 1
                    metrics = measured(start + cursor, start + trimmed)
                    # Shaping may be contextual: a longer candidate can be
                    # narrower. Do not stop at the first overflowing one.
                    if metrics.width <= available_width + _EPSILON:
                        chosen = boundary, trimmed, metrics
                if chosen is not None:
                    break
            if chosen is None:
                raise LayoutError(
                    "The text box is too narrow for a grapheme or an unbreakable Japanese punctuation group.")
            boundary, trimmed, metrics = chosen
            append(start + cursor, start + trimmed, metrics)
            cursor = boundary
            while cursor < len(forced) and forced[cursor] in _SPACES:
                cursor += 1

    offset = 0
    for newline in re.finditer(r"\r\n|\r|\n", text):
        wrap(offset, newline.start())
        offset = newline.end()
    wrap(offset, len(text))
    bbox = Rect(min(line.x for line in lines),
                min(line.baseline - line.ascent for line in lines),
                max(line.x + line.width for line in lines),
                max(line.baseline + line.descent for line in lines))
    return AttributedLayout(lines, bbox, placed)
