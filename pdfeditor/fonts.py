"""Font selection and measurement, isolated from document reconstruction."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
import unicodedata

from fontTools.ttLib import TTFont, TTLibError
import pymupdf

from .model import TextBox


class FontError(ValueError):
    pass


def normalized_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"^[A-Z]{6}\+", "", name).lower())


def validate_simple_text(text: str) -> None:
    """This writer handles horizontal Japanese/Latin without contextual shaping."""
    for char in text:
        cp = ord(char)
        if char == "\n":
            continue
        if (unicodedata.category(char) in {"Mn", "Mc", "Me", "Cc", "Cf", "Cs"}
                or unicodedata.bidirectional(char) in {"R", "AL", "AN"}
                or 0x900 <= cp <= 0x109F or 0x1780 <= cp <= 0x17FF):
            raise FontError(f"U+{cp:04X} requires shaping/control handling outside this PoC's horizontal Japanese/Latin writer")


def missing_characters(font: pymupdf.Font, text: str) -> list[str]:
    return sorted({c for c in text if c != "\n" and not font.has_glyph(ord(c), fallback=False)})


def embedding_restriction(data: bytes) -> str | None:
    try:
        with TTFont(io.BytesIO(data), lazy=True) as tt:
            flags = tt["OS/2"].fsType if "OS/2" in tt else 0
            if flags & 0x2:
                return "font OS/2 fsType restricts embedding"
            if flags & 0x200:
                return "font OS/2 fsType permits bitmap embedding only"
            if flags & 0x4 and not flags & 0x8:
                return "font OS/2 fsType allows preview/print embedding but not editable embedding"
    except TTLibError:
        pass  # CFF/Type1 are validated by MuPDF's font loader and coverage check.
    return None


@dataclass
class ResolvedFont:
    font: pymupdf.Font
    source: str
    original_name: str
    preserved: bool
    reasons: list[str]

    def report(self) -> dict:
        return {"original": self.original_name, "selected": self.font.name,
                "source": self.source, "preserved": self.preserved, "reasons": self.reasons}


def resolve_font(document: pymupdf.Document, page_number: int, original_name: str,
                 text: str, font_path: Path | None = None) -> ResolvedFont:
    reasons: list[str] = []
    desired = normalized_name(original_name)
    matches = [entry for entry in document.get_page_fonts(page_number, full=True)
               if normalized_name(entry[3]) == desired]
    for entry in matches:
        xref = entry[0]
        try:
            _, _, _, data = document.extract_font(xref)
            if not data:
                reasons.append(f"xref {xref}: font program is not embedded")
                continue
            restriction = embedding_restriction(data)
            if restriction:
                reasons.append(f"xref {xref}: {restriction}")
                continue
            font = pymupdf.Font(fontbuffer=data)
            missing = missing_characters(font, text)
            if missing:
                reasons.append(f"xref {xref}: embedded subset/CMap cannot encode "
                               + ", ".join(f"U+{ord(c):04X}" for c in missing[:12]))
                continue
            if not font.is_writable:
                reasons.append(f"xref {xref}: extracted font is not writable by TextWriter")
                continue
            return ResolvedFont(font, f"embedded:{xref}", original_name, True, reasons)
        except (ValueError, RuntimeError) as exc:
            reasons.append(f"xref {xref}: cannot reload embedded font ({exc})")

    if not matches:
        reasons.append("no unambiguous matching font resource was found")
    # The 14 standard PDF fonts have known metrics without a font program.
    for code in ("helv", "hebo", "heit", "hebi", "tiro", "tibo", "tiit", "tibi",
                 "cour", "cobo", "coit", "cobi", "symb", "zadb"):
        font = pymupdf.Font(code)
        if normalized_name(font.name) == desired and not missing_characters(font, text):
            return ResolvedFont(font, f"base14:{code}", original_name, True, reasons)

    if font_path is not None:
        data = font_path.read_bytes()
        restriction = embedding_restriction(data)
        if restriction:
            raise FontError(restriction)
        font = pymupdf.Font(fontbuffer=data)
        source = f"provided:{font_path.name}"
    else:
        font = pymupdf.Font("cjk")
        source = "bundled:Droid Sans Fallback Regular"
    missing = missing_characters(font, text)
    if missing:
        raise FontError("replacement font has no glyphs for "
                        + ", ".join(f"U+{ord(c):04X}" for c in missing[:12]))
    if not font.is_writable:
        raise FontError("replacement font is not writable")
    reasons.append("using a complete replacement font; original face identity cannot be guaranteed")
    return ResolvedFont(font, source, original_name, False, reasons)


def infer_tracking(box: TextBox, font: pymupdf.Font, size: float, preserved: bool) -> tuple[float, list[str]]:
    if not preserved:
        return 0.0, ["tracking reset to zero because the original font metrics are unavailable"]
    residuals = []
    for line in box.lines:
        glyphs = line.glyphs
        for glyph in glyphs:
            if glyph.source_order >= 0 and not glyph.text.isspace():
                intrinsic = font.text_length(glyph.text, fontsize=size)
                if abs(glyph.advance - intrinsic) > max(.12, size * .025):
                    raise FontError("PDF glyph widths differ from the embedded font metrics; horizontal scaling or custom widths require a richer writer")
        for a, b in zip(glyphs, glyphs[1:]):
            if a.source_order < 0 or b.source_order < 0 or a.text.isspace() or b.text.isspace() or b.origin[0] <= a.origin[0]:
                continue
            residuals.append(b.origin[0] - a.origin[0] - font.text_length(a.text, fontsize=size))
    if not residuals:
        return 0.0, []
    spacing = median(residuals)
    if max(abs(r - spacing) for r in residuals) > max(0.12, size * 0.025):
        raise FontError("nonuniform TJ spacing or horizontal scaling cannot be faithfully reproduced by this writer")
    return (0.0 if abs(spacing) < 0.025 else spacing), []


@dataclass
class FontMetrics:
    font: pymupdf.Font
    size: float
    tracking: float = 0.0

    def measure(self, text: str) -> float:
        return self.font.text_length(text, fontsize=self.size) + self.tracking * max(0, len(text) - 1)

    @property
    def ascender(self) -> float:
        return self.font.ascender * self.size

    @property
    def descender(self) -> float:
        return self.font.descender * self.size
