"""PDF-independent observations and inferred document structure (points, y down)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math


class WidthUnknownError(ValueError):
    """Reflow was requested without evidence for an available composing width."""


@dataclass(frozen=True)
class WidthConstraint:
    """Keep occupied geometry separate from available composing space.

    Observed width is useful for preview and comparison, never as an implicit
    fallback for layout. A caller that wants reflow must obtain an inferred or
    explicitly confirmed width first. Source-preserving no-op replay does not
    need to call ``require_available_width``.
    """

    observed_content_width: float
    inferred_available_width: float | None = None
    explicitly_supplied_width: float | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.observed_content_width) or self.observed_content_width < 0:
            raise ValueError("observed content width must be nonnegative and finite")
        for name in ("inferred_available_width", "explicitly_supplied_width"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"{name} must be positive and finite")

    @property
    def source(self) -> str:
        if self.explicitly_supplied_width is not None:
            return "explicit"
        if self.inferred_available_width is not None:
            return "inferred"
        return "unknown"

    def require_available_width(self) -> float:
        if self.explicitly_supplied_width is not None:
            return self.explicitly_supplied_width
        if self.inferred_available_width is not None:
            return self.inferred_available_width
        raise WidthUnknownError(
            "available width is unknown; supply an explicit width before reflow "
            "(observed content width is not an available width)")

    def with_explicit(self, width: float | None) -> WidthConstraint:
        return WidthConstraint(self.observed_content_width,
                               self.inferred_available_width, width)


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def intersects(self, other: Rect, tolerance: float = 0.0) -> bool:
        return (self.x0 < other.x1 - tolerance and self.x1 > other.x0 + tolerance
                and self.y0 < other.y1 - tolerance and self.y1 > other.y0 + tolerance)

    def contains(self, other: Rect, tolerance: float = 0.0) -> bool:
        return (self.x0 - tolerance <= other.x0 and self.y0 - tolerance <= other.y0
                and self.x1 + tolerance >= other.x1 and self.y1 + tolerance >= other.y1)

    def union(self, other: Rect) -> Rect:
        return Rect(min(self.x0, other.x0), min(self.y0, other.y0),
                    max(self.x1, other.x1), max(self.y1, other.y1))

    def tuple(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0, self.x1, self.y1


@dataclass(frozen=True)
class Style:
    font: str
    size: float
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Glyph:
    text: str
    bbox: Rect
    origin: tuple[float, float]
    advance: float
    style: Style
    direction: tuple[float, float] = (1.0, 0.0)
    glyph_id: int | None = None
    source_order: int = 0
    visible: bool = True


@dataclass
class Run:
    glyphs: list[Glyph]
    style: Style
    bbox: Rect

    @property
    def text(self) -> str:
        return "".join(g.text for g in self.glyphs)


@dataclass
class Word:
    text: str
    bbox: Rect


@dataclass
class Line:
    runs: list[Run]
    bbox: Rect
    baseline: float
    direction: tuple[float, float] = (1.0, 0.0)
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def glyphs(self) -> list[Glyph]:
        return [g for r in self.runs for g in r.glyphs]


@dataclass
class Paragraph:
    lines: list[Line]
    text: str


@dataclass
class TextBox:
    id: str
    page: int
    bbox: Rect
    paragraphs: list[Paragraph]
    line_height: float
    width_source: str = "unknown"
    warnings: list[str] = field(default_factory=list)
    available_width: float | None = None
    explicitly_supplied_width: float | None = None

    @property
    def observed_content_width(self) -> float:
        return self.bbox.width

    @property
    def inferred_available_width(self) -> float | None:
        # ``available_width`` remains an API compatibility alias for measured
        # frame inference. It must never be populated from bbox.width.
        return self.available_width

    @inferred_available_width.setter
    def inferred_available_width(self, width: float | None) -> None:
        self.available_width = width

    @property
    def widths(self) -> WidthConstraint:
        return WidthConstraint(self.observed_content_width,
                               self.inferred_available_width,
                               self.explicitly_supplied_width)

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.paragraphs)

    @property
    def lines(self) -> list[Line]:
        return [line for p in self.paragraphs for line in p.lines]

    @property
    def glyphs(self) -> list[Glyph]:
        return [g for line in self.lines for g in line.glyphs]


@dataclass
class Obstacle:
    bbox: Rect
    kind: str


@dataclass
class PageModel:
    page: int
    width: float
    height: float
    glyphs: list[Glyph]
    boxes: list[TextBox]
    obstacles: list[Obstacle] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        for box, output in zip(self.boxes, data["boxes"]):
            output["text"] = box.text
            output.update(asdict(box.widths))
            for paragraph, para_output in zip(box.paragraphs, output["paragraphs"]):
                for line, line_output in zip(paragraph.lines, para_output["lines"]):
                    line_output["text"] = line.text
        return data
