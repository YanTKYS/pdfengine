"""Recover editable geometry from glyph observations, independently of PDF I/O.

The heuristics record the observed width without inventing an available width:
a PDF does not tell us the author's intended text-frame width. Unicode word boundaries are UAX #29 segments,
not a Japanese morphological analysis. Drawing order is never reading order.
"""

from __future__ import annotations

from statistics import median
import unicodedata

from uniseg.wordbreak import word_boundaries

from .model import Glyph, Line, Paragraph, Rect, Run, TextBox, Word


def _bounds(glyphs: list[Glyph]) -> Rect:
    result = glyphs[0].bbox
    for glyph in glyphs[1:]:
        result = result.union(glyph.bbox)
    return result


def _size(line: Line) -> float:
    return median(g.style.size for g in line.glyphs)


def _cjk(character: str) -> bool:
    codepoint = ord(character)
    return (0x2E80 <= codepoint <= 0xA4CF
            or 0xAC00 <= codepoint <= 0xD7AF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0xFF00 <= codepoint <= 0xFFEF
            or 0x20000 <= codepoint <= 0x323AF)


def _word_character(character: str) -> bool:
    return unicodedata.category(character)[0] in "LN" and not _cjk(character)


def _separator(left: str, right: str) -> str:
    """Recover a soft wrap without adding spaces between Japanese characters."""
    if not left or not right:
        return ""
    if left[-1].isspace() or right[0].isspace():
        return " "
    if (_word_character(right[0]) and
            (_word_character(left[-1]) or left[-1] in ",;:!?.)]}\"'\u201d\u2019")):
        return " "
    return ""


def _join_lines(lines: list[Line]) -> str:
    text = lines[0].text.strip()
    for previous, current in zip(lines, lines[1:]):
        text += _separator(previous.text, current.text) + current.text.strip()
    return text


def _make_words(glyphs: list[Glyph]) -> list[Word]:
    text = "".join(g.text for g in glyphs)
    # A ligature may decode to multiple Unicode characters. Every character in
    # that decoded span maps back to the same observed glyph rectangle.
    character_boxes = [g.bbox for g in glyphs for _ in g.text]
    boundaries = list(word_boundaries(text))
    result = []
    for start, stop in zip(boundaries, boundaries[1:]):
        word = text[start:stop]
        if not word or word.isspace():
            continue
        bbox = character_boxes[start]
        for rect in character_boxes[start + 1:stop]:
            bbox = bbox.union(rect)
        result.append(Word(word, bbox))
    return result


def _make_line(original: list[Glyph], direction: tuple[float, float] = (1.0, 0.0),
               *, infer_spaces: bool = True) -> Line:
    glyphs: list[Glyph] = []
    for glyph in original:
        if infer_spaces and glyphs and glyphs[-1].text and glyph.text:
            previous = glyphs[-1]
            gap = glyph.bbox.x0 - previous.bbox.x1
            threshold = max(0.7, min(previous.style.size, glyph.style.size) * 0.20)
            if (gap > threshold and not previous.text[-1].isspace()
                    and not glyph.text[0].isspace()
                    and _separator(previous.text, glyph.text)):
                glyphs.append(Glyph(
                    " ", Rect(previous.bbox.x1, min(previous.bbox.y0, glyph.bbox.y0),
                              glyph.bbox.x0, max(previous.bbox.y1, glyph.bbox.y1)),
                    (previous.bbox.x1, previous.origin[1]), gap,
                    previous.style, source_order=-1))
        glyphs.append(glyph)
    runs: list[Run] = []
    for glyph in glyphs:
        if runs and runs[-1].style == glyph.style:
            runs[-1].glyphs.append(glyph)
            runs[-1].bbox = runs[-1].bbox.union(glyph.bbox)
        else:
            runs.append(Run([glyph], glyph.style, glyph.bbox))
    return Line(runs, _bounds(original), median(g.origin[1] for g in original),
                direction, _make_words(glyphs))


def _horizontal_lines(glyphs: list[Glyph], *, infer_spaces: bool = True) -> list[Line]:
    # Clustering by baseline rather than bounding-box top preserves mixed-size
    # runs. Sorting by y then x makes this invariant to content-stream order.
    baselines: list[list[Glyph]] = []
    for glyph in sorted(glyphs, key=lambda g: (g.origin[1], g.origin[0], g.source_order)):
        candidate = None
        nearest = float("inf")
        for group in reversed(baselines):
            baseline = median(g.origin[1] for g in group)
            distance = abs(glyph.origin[1] - baseline)
            tolerance = max(0.65, min(glyph.style.size, median(g.style.size for g in group)) * 0.15)
            if distance <= tolerance and distance < nearest:
                candidate, nearest = group, distance
            # All older clusters are farther away. Allow a generous font-size
            # range before stopping so mixed font runs remain eligible.
            if glyph.origin[1] - baseline > max(6.0, glyph.style.size * 0.5):
                break
        if candidate is None:
            baselines.append([glyph])
        else:
            candidate.append(glyph)

    lines: list[Line] = []
    for group in baselines:
        group.sort(key=lambda g: (g.origin[0], g.bbox.x0, g.source_order))
        size = median(g.style.size for g in group)
        pieces: list[list[Glyph]] = [[]]
        right_edge: float | None = None
        for glyph in group:
            # An inter-column gutter must not become a huge inferred word gap.
            if right_edge is not None and glyph.bbox.x0 - right_edge > size * 2.2:
                pieces.append([])
            pieces[-1].append(glyph)
            right_edge = max(right_edge if right_edge is not None else glyph.bbox.x1,
                             glyph.bbox.x1)
        lines.extend(_make_line(piece, infer_spaces=infer_spaces) for piece in pieces)
    return sorted(lines, key=lambda line: (line.baseline, line.bbox.x0))


def _alignment_cost(previous: Line, current: Line, first: Line) -> float | None:
    size = max(_size(previous), _size(current))
    overlap = min(previous.bbox.x1, current.bbox.x1) - max(previous.bbox.x0, current.bbox.x0)
    if overlap <= 0:
        return None
    left = min(abs(current.bbox.x0 - previous.bbox.x0), abs(current.bbox.x0 - first.bbox.x0))
    right = abs(current.bbox.x1 - previous.bbox.x1)
    # Permit a one-em first-line indent. Right-aligned text is accepted only
    # with substantial overlap to avoid joining neighboring columns.
    if left <= size * 1.35:
        return left / size
    if right <= size * 0.65 and overlap >= min(previous.bbox.width, current.bbox.width) * 0.65:
        return 1.0 + right / size
    return None


def _line_groups(lines: list[Line]) -> list[list[Line]]:
    groups: list[list[Line]] = []
    for line in lines:
        candidates: list[tuple[float, int]] = []
        size = _size(line)
        for index, group in enumerate(groups):
            previous = group[-1]
            previous_size = _size(previous)
            if min(size, previous_size) / max(size, previous_size) < 0.82:
                continue
            gap = line.baseline - previous.baseline
            if not max(1.0, size * 0.5) < gap <= min(size, previous_size) * 2.6:
                continue
            cost = _alignment_cost(previous, line, group[0])
            if cost is not None:
                candidates.append((gap / size + cost, index))
        if candidates:
            _, index = min(candidates)
            groups[index].append(line)
        else:
            groups.append([line])
    return groups


def _paragraphs(lines: list[Line]) -> tuple[list[Paragraph], float]:
    size = median(_size(line) for line in lines)
    gaps = [b.baseline - a.baseline for a, b in zip(lines, lines[1:])]
    # Use the lower half of measured gaps so paragraph spacing does not inflate
    # ordinary leading. Do not clamp to font size: double-spaced text is valid.
    # With only two lines, a paragraph gap versus leading is underdetermined.
    ordinary_gaps = sorted(gaps)
    if ordinary_gaps:
        line_height = median(ordinary_gaps[:max(1, (len(ordinary_gaps) + 1) // 2)])
    else:
        line_height = size * 1.4
    left = min(line.bbox.x0 for line in lines)
    widest = max(line.bbox.width for line in lines)
    parts: list[list[Line]] = [[lines[0]]]
    for previous, current in zip(lines, lines[1:]):
        gap_break = current.baseline - previous.baseline > line_height * 1.3
        indent_break = (current.bbox.x0 - left >= size * 0.75
                        and previous.bbox.x0 - left < size * 0.5)
        short_terminal = (previous.bbox.width < widest * 0.72
                          and previous.text.rstrip().endswith(("。", "！", "？", ".", "!", "?")))
        if gap_break or indent_break or short_terminal:
            parts.append([])
        parts[-1].append(current)
    return [Paragraph(part, _join_lines(part)) for part in parts], line_height


def infer_boxes(glyphs: list[Glyph], page: int, page_width: float, page_height: float) -> list[TextBox]:
    """Infer reading structure without trusting extraction/drawing order.

    ``page`` is one-based, matching inspection reports and CLI page numbers.
    Coordinates are page points in a top-left origin. Frame dimensions are
    accepted as part of the backend-neutral API, but never used to invent a
    wider editable area. Nonhorizontal observations survive in warning boxes.
    """
    if page < 1:
        raise ValueError("Page number must be one-based and positive.")
    if page_width <= 0 or page_height <= 0:
        raise ValueError("Page width and height must be positive.")
    if not glyphs:
        return []
    horizontal: list[Glyph] = []
    unsupported: list[Glyph] = []
    for glyph in glyphs:
        if abs(glyph.direction[0] - 1.0) <= 0.001 and abs(glyph.direction[1]) <= 0.001:
            horizontal.append(glyph)
        else:
            unsupported.append(glyph)

    boxes: list[TextBox] = []
    for lines in _line_groups(_horizontal_lines(horizontal)):
        paragraphs, line_height = _paragraphs(lines)
        bbox = lines[0].bbox
        for line in lines[1:]:
            bbox = bbox.union(line.bbox)
        warnings = []
        if len(lines) == 1:
            warnings.append("Single-line text box width is uncertain; use an explicit width for wider reflow.")
        boxes.append(TextBox("", page, bbox, paragraphs, line_height, warnings=warnings))
    for glyph in unsupported:
        line = _make_line([glyph], glyph.direction)
        boxes.append(TextBox("", page, glyph.bbox, [Paragraph([line], glyph.text)],
                             glyph.style.size * 1.4,
                             warnings=["Rotated or vertical text is retained but cannot use horizontal reflow."]))
    boxes.sort(key=lambda box: (box.bbox.y0, box.bbox.x0,
                              min(g.source_order for g in box.glyphs)))
    for index, box in enumerate(boxes):
        box.id = f"p{page}-b{index + 1}"
    return boxes
