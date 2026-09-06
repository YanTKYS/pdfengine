"""Coordinate selection, layout and output without coupling the layout to PDF APIs."""

from __future__ import annotations

from dataclasses import asdict
import math
import os
from pathlib import Path
import tempfile

import pymupdf

from .backend import PdfError, extract_page, replace_region, validate_collisions, validate_editable
from .fonts import FontMetrics, infer_tracking, resolve_font, validate_simple_text
from .layout import layout_text


def inspect_pdf(input_path: str | Path, page: int = 1) -> dict:
    with pymupdf.open(input_path) as document:
        if document.needs_pass:
            raise PdfError("encrypted PDF requires decryption before this PoC can inspect it")
        return extract_page(document, page - 1).to_dict()


def edit_pdf(input_path: str | Path, output_path: str | Path, *, page: int = 1,
             find: str | None = None, replacement: str, box_id: str | None = None,
             width: float | None = None, max_height: float | None = None,
             font_path: str | Path | None = None) -> dict:
    source, destination = Path(input_path).resolve(), Path(output_path).resolve()
    if source == destination or (destination.exists() and os.path.samefile(source, destination)):
        raise PdfError("input and output must be different files; the original is never modified")
    if destination.exists():
        raise PdfError("output already exists; choose a new path")
    for name, value in (("width", width), ("max_height", max_height)):
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise PdfError(f"{name} must be positive and finite")
    replacement = replacement.replace("\r\n", "\n").replace("\r", "\n")
    validate_simple_text(replacement)
    if find == "":
        raise PdfError("find must not be empty")
    if find is None and box_id is None:
        raise PdfError("specify find or box_id")
    with pymupdf.open(source) as document:
        if document.needs_pass:
            raise PdfError("encrypted PDF requires decryption before editing")
        if not document.is_pdf:
            raise PdfError("input must be PDF")
        model = extract_page(document, page - 1)
        candidates = [b for b in model.boxes if (box_id is None or b.id == box_id)
                      and (find is None or find in b.text)]
        if len(candidates) != 1:
            raise PdfError(f"expected one text region, found {len(candidates)}; inspect and choose a box_id")
        box = candidates[0]
        if find is not None and box.text.count(find) != 1:
            raise PdfError("target text occurs more than once in the region; replace the complete box by id")
        validate_editable(document[page - 1], box)
        edited = box.text.replace(find, replacement, 1) if find is not None else replacement
        validate_simple_text(edited)
        style = box.glyphs[0].style
        resolved = resolve_font(document, page - 1, style.font, edited,
                                Path(font_path) if font_path else None)
        tracking, warnings = infer_tracking(box, resolved.font, style.size, resolved.preserved)
        metrics = FontMetrics(resolved.font, style.size, tracking)
        line_height = max(box.line_height, metrics.ascender - metrics.descender + .2)
        selected_width = width if width is not None else (box.available_width or box.bbox.width)
        baseline = box.lines[0].baseline
        max_bottom = model.height if max_height is None else min(model.height, baseline - metrics.ascender + max_height)
        layout = layout_text(edited, x=box.bbox.x0, baseline=baseline, width=selected_width,
                             line_height=line_height, ascender=metrics.ascender,
                             descender=metrics.descender, measure=metrics.measure, max_bottom=max_bottom)
        validate_collisions(document[page - 1], model, box, layout, metrics)
        replace_region(document, page - 1, model, box, layout, metrics)
        report = {
            "schema_version": 1, "input": str(source), "output": str(destination), "page": page,
            "box_id": box.id, "before": box.text, "after": edited,
            "old_line_count": len(box.lines), "new_line_count": len(layout.lines),
            "old_bbox": asdict(box.bbox), "new_bbox": asdict(layout.bbox),
            "width": selected_width, "width_source": "explicit" if width is not None else box.width_source,
            "font_size": style.size, "tracking": tracking, "line_height": line_height,
            "font": resolved.report(), "lines": [asdict(line) for line in layout.lines],
            "warnings": model.warnings + box.warnings + warnings,
            "page_reflow": "not performed; collisions cause an error before saving",
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".pdfeditor-", suffix=".pdf", dir=destination.parent)
        os.close(descriptor)
        try:
            document.save(temporary, garbage=3, deflate=True)
            with pymupdf.open(temporary) as check:
                if len(check) != len(document):
                    raise PdfError("output page count verification failed")
                check[page - 1].get_pixmap(matrix=pymupdf.Matrix(.5, .5))
            # Hard-link creation atomically refuses to overwrite an existing destination.
            os.link(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return report
