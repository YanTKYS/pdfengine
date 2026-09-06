"""MuPDF adapter: observed glyphs in, isolated PDF text replacement out."""

from __future__ import annotations

from collections import Counter

import pymupdf

from .inference import infer_boxes
from .model import Glyph, Obstacle, PageModel, Rect, Style, TextBox


class PdfError(ValueError):
    pass


def _rgb(color: tuple) -> tuple[float, float, float]:
    if len(color) == 1:
        return (color[0],) * 3
    if len(color) == 4:
        c, m, y, k = color
        return 1 - min(1, c + k), 1 - min(1, m + k), 1 - min(1, y + k)
    return tuple(color[:3])


def extract_page(document: pymupdf.Document, page_number: int) -> PageModel:
    if not 0 <= page_number < len(document):
        raise PdfError(f"page must be between 1 and {len(document)}")
    page = document[page_number]
    glyphs: list[Glyph] = []
    warnings: list[str] = []
    for span in page.get_texttrace():
        direction = tuple(span["dir"])
        style = Style(span["font"], float(span["size"]), _rgb(span["color"]))
        for codepoint, gid, origin, bbox in span["chars"]:
            rect = Rect(*bbox)
            glyphs.append(Glyph(chr(codepoint), rect, tuple(origin),
                                rect.width if abs(direction[0]) > .9 else rect.height,
                                style, direction, gid, len(glyphs),
                                span["type"] == 0 and span["opacity"] >= .999
                                and not span.get("layer") and span.get("wmode", 0) == 0))
    if page.rotation:
        warnings.append("page has display rotation; observation coordinates are unrotated")
    boxes = infer_boxes(glyphs, page_number + 1, page.cropbox.width, page.cropbox.height)
    obstacles: list[Obstacle] = []
    for drawing in page.get_drawings():
        rect = Rect(*drawing["rect"])
        if (drawing.get("color") is not None and len(drawing.get("items", [])) == 1
                and drawing["items"][0][0] == "re"):
            for box in boxes:
                if rect.contains(box.bbox) and rect.width < page.cropbox.width * .98:
                    inset = max(2.0, box.bbox.x0 - rect.x0)
                    candidate = rect.x1 - inset - box.bbox.x0
                    if candidate >= box.bbox.width - .1 and (box.available_width is None or candidate < box.available_width):
                        box.available_width = candidate
                        box.width_source = "enclosing-rectangle-with-symmetric-inset"
                        box.warnings = [w for w in box.warnings if not w.startswith("Single-line text box width")]
                        box.warnings.append("Frame width assumes symmetric horizontal insets; --width can override it.")
        if drawing.get("fill") is not None:
            obstacles.append(Obstacle(rect, "filled-vector"))
        if drawing.get("color") is not None:
            # A rectangular border constrains its edges; its empty interior is usable.
            border = max(float(drawing.get("width", 1)) / 2, .3)
            items = drawing.get("items", [])
            if len(items) == 1 and items[0][0] == "re":
                for edge in (Rect(rect.x0-border, rect.y0-border, rect.x1+border, rect.y0+border),
                             Rect(rect.x0-border, rect.y1-border, rect.x1+border, rect.y1+border),
                             Rect(rect.x0-border, rect.y0, rect.x0+border, rect.y1),
                             Rect(rect.x1-border, rect.y0, rect.x1+border, rect.y1)):
                    obstacles.append(Obstacle(edge, "vector-border"))
            else:
                obstacles.append(Obstacle(Rect(rect.x0-border, rect.y0-border,
                                               rect.x1+border, rect.y1+border), "vector"))
    for info in page.get_image_info():
        obstacles.append(Obstacle(Rect(*info["bbox"]), "image"))
    return PageModel(page_number + 1, page.cropbox.width, page.cropbox.height,
                     glyphs, boxes, obstacles, warnings)


def validate_editable(page: pymupdf.Page, box: TextBox) -> None:
    if page.rotation:
        raise PdfError("rotated pages are inspectable but editing is not yet supported")
    if not box.glyphs:
        raise PdfError("selected region contains no text")
    if any(abs(g.direction[0] - 1) > .001 or abs(g.direction[1]) > .001 for g in box.glyphs):
        raise PdfError("rotated, vertical or transformed text cannot be edited by this writer")
    if any(not g.visible for g in box.glyphs):
        raise PdfError("invisible, stroked, transparent or optional-content text requires a richer paint model")
    if any(g.text == "\ufffd" or g.glyph_id == -1 for g in box.glyphs):
        raise PdfError("unmapped Unicode/ligature glyphs require CMap/shaping recovery before editing")
    styles = {(g.style.font, round(g.style.size, 2), g.style.color) for g in box.glyphs}
    if len(styles) != 1:
        raise PdfError("mixed styles in the selected region require style-aware edit mapping; choose a uniform region")
    for annotation in page.annots() or []:
        if annotation.type[0] == pymupdf.PDF_ANNOT_REDACT:
            raise PdfError("page contains pending redactions; applying them would change unrelated content")


def validate_collisions(page: pymupdf.Page, model: PageModel, box: TextBox, layout, metrics) -> None:
    selected = {g.source_order for g in box.glyphs if g.source_order >= 0}
    bounds = Rect(0, 0, model.width, model.height)
    affected = box.bbox.union(layout.bbox)
    # Annotation appearances can also be returned as drawings. Explain the
    # semantic conflict before reporting their appearance as a vector collision.
    for annotation in page.annots() or []:
        if affected.intersects(Rect(*annotation.rect)):
            raise PdfError("edited region intersects an annotation")
    for link in page.get_links():
        if affected.intersects(Rect(*link["from"])):
            raise PdfError("edited region intersects a link")
    for widget in page.widgets() or []:
        if affected.intersects(Rect(*widget.rect)):
            raise PdfError("edited region intersects a form widget")
    for drawing in page.get_drawings(extended=True):
        if drawing["type"] == "clip":
            items = drawing.get("items", [])
            full_page_rectangle = (len(items) == 1 and items[0][0] == "re"
                                   and Rect(*items[0][1]).contains(Rect(*page.rect), .01))
            if not full_page_rectangle:
                raise PdfError("page contains clipping paths; preserving text clipping requires a richer paint model")
        if drawing["type"] == "group" and (drawing.get("opacity", 1) < .999
                                            or drawing.get("blendmode", "Normal") != "Normal"):
            raise PdfError("page contains a transparency group requiring paint-state preservation")
    for line in layout.lines:
        if not line.text:
            continue
        rect = Rect(line.x, line.baseline - metrics.ascender,
                    line.x + line.width, line.baseline - metrics.descender)
        if not bounds.contains(rect, .02):
            raise PdfError("new text would extend outside the page")
        for glyph in model.glyphs:
            if glyph.source_order not in selected and rect.intersects(glyph.bbox, .1):
                raise PdfError("new text collides with another text region; page-level reflow is required")
        for obstacle in model.obstacles:
            if obstacle.kind in {"filled-vector", "image"} and obstacle.bbox.contains(box.bbox, 1):
                # Preserve an existing background, but never grow beyond its bounds.
                if not obstacle.bbox.contains(rect, .02):
                    raise PdfError("new text extends outside its existing background")
                continue
            if rect.intersects(obstacle.bbox, .05):
                raise PdfError(f"new text collides with {obstacle.kind}; page-level reflow is required")


def _text_signature(glyph: Glyph) -> tuple:
    return glyph.text, round(glyph.origin[0], 2), round(glyph.origin[1], 2)


def replace_region(document: pymupdf.Document, page_number: int, model: PageModel,
                   box: TextBox, layout, metrics) -> None:
    page = document[page_number]
    selected = {g.source_order for g in box.glyphs if g.source_order >= 0}
    untouched = Counter(_text_signature(g) for g in model.glyphs if g.source_order not in selected)
    # Small interior rectangles hit the source glyph without painting a white patch.
    # MuPDF deletes a whole character if its text bbox overlaps the rectangle.
    for glyph in box.glyphs:
        if glyph.source_order < 0:
            continue
        r = glyph.bbox
        if r.width <= 0 or r.height <= 0:
            raise PdfError("zero-area glyph cannot be safely selected for removal")
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        page.add_redact_annot(pymupdf.Rect(cx-.05, cy-.05, cx+.05, cy+.05), fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0, text=0)
    remaining = extract_page(document, page_number)
    if Counter(_text_signature(g) for g in remaining.glyphs) != untouched:
        raise PdfError("text removal affected adjacent glyphs or failed to remove selected glyphs; no output saved")
    writer = pymupdf.TextWriter(page.rect)
    for line in layout.lines:
        if not line.text:
            continue
        if metrics.tracking == 0:
            writer.append((line.x, line.baseline), line.text, font=metrics.font, fontsize=metrics.size)
        else:
            x = line.x
            for char in line.text:
                writer.append((x, line.baseline), char, font=metrics.font, fontsize=metrics.size)
                x += metrics.font.text_length(char, fontsize=metrics.size) + metrics.tracking
    if any(line.text for line in layout.lines):
        writer.write_text(page, color=box.glyphs[0].style.color, overlay=True)
