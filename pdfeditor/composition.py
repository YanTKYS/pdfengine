"""Compose a human-selected region with a supplied font and shaped glyph runs.

Selection provenance and removal remain tied to the source operators. The new
text uses its own font/code/width graph and is not constrained to source slots.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
import math
import re
from io import BytesIO

import pymupdf
from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen

from .backend import PdfError
from .content_stream import ContentPage, PaintChar, multiply, number, patch_streams, serialized_event, translate
from .explicit_reflow import allow_clip, matrix_operator
from .layout import layout_text
from .model import Rect
from .pdf_save import font_fingerprints, program_pdf_bytes, publish_program
from .replay import compare_glyphs, ensure_destination, glyph_observations
from .selection import resolve_selection, source_sha
from .shaped_font import ShapedFont


def _observations(page):
    observations = glyph_observations(page)
    aliases = {}
    for resource in page.get_fonts(full=True):
        if resource[2] == "Type3":
            aliases.setdefault(resource[0], set()).add(resource[4])
    for glyph in observations:
        match = re.fullmatch(r"Type3 \((\d+) \d+ R\)", glyph["font"])
        if match and int(match[1]) in aliases:
            # MuPDF synthesizes this display name from an object number. Full
            # saving renumbers references; resource aliases and pixels must
            # still match. This never makes selected Type3 text editable.
            glyph["font"] = "Type3 resources " + repr(sorted(aliases[int(match[1])]))
    return observations


def _empty_space_indices(content, observations):
    """Ignore a whitespace obstacle only when its embedded outline is empty.

    Unicode whitespace alone is insufficient: a PDF can map it to visible ink.
    Unknown programs, Type3, and ambiguous provenance remain obstacles.
    """
    fonts, empty = {}, set()
    for event in content.events:
        if event.error or event.state.font.subtype not in {"/TrueType", "/Type0"}:
            continue
        for char in event.chars:
            for index in char.source_orders:
                glyph = observations[index]
                if not glyph["unicode"].isspace() or glyph["glyph_id"] < 0:
                    continue
                xref = event.state.font.xref
                if xref not in fonts:
                    try:
                        data = content.document.extract_font(xref)[3]
                        font = TTFont(BytesIO(data)) if data else None
                        fonts[xref] = font if font is not None and "glyf" in font else None
                    except Exception:
                        fonts[xref] = None
                font = fonts[xref]
                if font is not None:
                    try:
                        glyphs = font.getGlyphSet()
                        pen = BoundsPen(glyphs)
                        glyphs[font.getGlyphName(glyph["glyph_id"])].draw(pen)
                        if pen.bounds is None:
                            empty.add(index)
                    except Exception:
                        pass  # Cannot prove an empty outline; keep the obstacle.
    for font in fonts.values():
        if font is not None:
            font.close()
    return empty


def _signature(g):
    copy = dict(g)
    # Bucket stable properties, then compare geometry with an actual tolerance.
    # Decimal rounding is not a distance test at a bucket boundary.
    for key in ("origin", "bbox", "size", "advance_bbox"):
        copy.pop(key)
    return json.dumps(copy, sort_keys=True)


def _pixels_equal(before, after, mask=None):
    a, b = before.get_pixmap(dpi=144, alpha=False), after.get_pixmap(dpi=144, alpha=False)
    if (a.width, a.height, a.n) != (b.width, b.height, b.n):
        return False
    first, second = a.samples, b.samples
    if mask is None:
        return first == second
    x0, y0 = max(0, math.floor(mask.x0 * 2) - 2), max(0, math.floor(mask.y0 * 2) - 2)
    x1, y1 = min(a.width, math.ceil(mask.x1 * 2) + 2), min(a.height, math.ceil(mask.y1 * 2) + 2)
    stride = a.width * a.n
    for y in range(a.height):
        start = y * stride
        spans = ((0, stride),) if not y0 <= y < y1 else ((0, x0 * a.n), (x1 * a.n, stride))
        if any(first[start + lo:start + hi] != second[start + lo:start + hi] for lo, hi in spans):
            return False
    return True


def _canonical(value):
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items() if k != "at"}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def _state_contract(events):
    first = min(events, key=lambda e: e.operator.start)
    state = first.state
    matrix = multiply(first.text_matrix, state.ctm)
    if abs(matrix[1]) > 1e-5 or abs(matrix[2]) > 1e-5 or matrix[0] <= 0 or matrix[3] <= 0:
        raise PdfError("composition requires positive, axis-aligned horizontal text")
    keys = ("size", "tc", "tw", "tz", "tr", "fill", "stroke", "opacity", "stroke_opacity")
    for e in events:
        if e.state.tr != 0 or e.state.opacity != 1 or e.state.stroke_opacity != 1:
            raise PdfError("composition currently requires opaque fill text")
        if any(getattr(e.state, k) != getattr(state, k) for k in keys):
            raise PdfError("select a range with a common size, spacing and paint style")
        if _canonical(e.state.clip) != _canonical(state.clip):
            raise PdfError("selected text has different active clipping regions")
        if any(abs(a - b) > 1e-5 for a, b in zip(multiply(e.text_matrix, e.state.ctm)[:4], matrix[:4])):
            raise PdfError("selected text has different effective text scales")
        for key, value in e.state.other.items():
            if key.startswith("ExtGState:") and isinstance(value, dict):
                if value.get("/SMask", "/None") != "/None" or value.get("/BM", "/Normal") != "/Normal":
                    raise PdfError("soft masks and blend modes require separate composition support")
            if key == "marked_content" and any(token in str(value) for token in ("/ActualText", "/OC")):
                raise PdfError("ActualText or optional-content semantics cannot be safely rewritten")
    return first, state, matrix


def _check_obstacles(content, selected, resolved, ink, layout_bounds):
    original = glyph_observations(content.page)
    empty_spaces = _empty_space_indices(content, original)
    # An empty logical element has no observed paint order. Without a
    # confirmed relation, every overlapping fill remains an obstacle.
    first_paint = min((content.actual[i]["span"]["seqno"] for i in selected),default=-1)
    for rect in ink:
        for i, old in enumerate(original):
            if i not in selected and i not in empty_spaces and rect.intersects(Rect(*old["bbox"]), .1):
                raise PdfError("composed text collides with an unselected glyph")
        for image in content.page.get_image_info():
            if rect.intersects(Rect(*image["bbox"]), .01):
                raise PdfError("composed text intersects an image")
        for drawing in content.page.get_drawings():
            bounds = Rect(*drawing["rect"])
            rectangle = len(drawing["items"]) == 1 and drawing["items"][0][0] == "re"
            if drawing.get("fill") is not None:
                # A filled rectangle behind the selected text remains a
                # background when the confirmed region crosses its edge.
                # A later fill (e.g. a popup covering text) is an obstacle.
                background = (rectangle and bounds.contains(resolved.bbox)
                              and drawing.get("seqno", first_paint) < first_paint)
                if not background and rect.intersects(bounds, .01):
                    raise PdfError("composed text intersects a filled vector")
            if drawing.get("color") is not None:
                t = max(float(drawing.get("width", 1)) / 2, .2)
                edges = ([Rect(bounds.x0-t, bounds.y0-t, bounds.x1+t, bounds.y0+t),
                          Rect(bounds.x0-t, bounds.y1-t, bounds.x1+t, bounds.y1+t),
                          Rect(bounds.x0-t, bounds.y0, bounds.x0+t, bounds.y1),
                          Rect(bounds.x1-t, bounds.y0, bounds.x1+t, bounds.y1)] if rectangle else [bounds])
                if any(rect.intersects(edge, .01) for edge in edges):
                    raise PdfError("composed text intersects a vector border/path")
    affected = resolved.bbox.union(layout_bounds)
    for link in content.page.get_links():
        if affected.intersects(Rect(*link["from"])):
            raise PdfError("composition intersects a link")
    for item in list(content.page.annots() or []) + list(content.page.widgets() or []):
        if affected.intersects(Rect(*item.rect)):
            raise PdfError("composition intersects an annotation or widget")


def compose_selected(source, output, manifest, replacement, *, font_source, font_index=0,
                     variations=None, max_height=None, line_height=None, removal_output=None):
    output = ensure_destination(output, source)
    if removal_output:
        removal_output = ensure_destination(removal_output, source)
        if output == removal_output:
            raise PdfError("output and removal checkpoint must be distinct")
    resolved = resolve_selection(source, manifest)
    width = resolved.widths.require_available_width()
    content = ContentPage(source, resolved.page)
    try:
        selected = set(manifest["glyph_ids"])
        events = content.selected_events(selected)
        first, state, matrix = _state_contract(events)
        original = _observations(content.page)
        untouched = [g for i, g in enumerate(original) if i not in selected]
        pno = resolved.page - 1
        virtual = -content.page.xref
        removed = patch_streams(content, events, selected, remove=True)[virtual]
        replayed = patch_streams(content, events, selected, remove=False)[virtual]
        # Check the removal and source replay on this operation, rather than
        # trusting a caller-set boolean from a previous edit's report.
        with pymupdf.open(stream=program_pdf_bytes(source, pno, replayed), filetype="pdf") as check:
            if not compare_glyphs(original, _observations(check[pno]))["passed"]:
                raise PdfError("source replay changed original glyphs")
            if not all(_pixels_equal(content.document[i], check[i]) for i in range(len(check))):
                raise PdfError("source replay failed pixel equivalence")
        removed_bytes = program_pdf_bytes(source, pno, removed)
        with pymupdf.open(stream=removed_bytes, filetype="pdf") as check:
            removal_audit = compare_glyphs(untouched, _observations(check[pno]))
            if not removal_audit["passed"]:
                raise PdfError("removing selected operators changed unselected glyphs")

        font = font_source if isinstance(font_source, ShapedFont) else ShapedFont(font_source, font_index=font_index, variations=variations)
        x_scale = matrix[0] * state.size * state.tz / 100 / font.upem
        y_scale = matrix[3] * state.size / font.upem
        tracking = state.tc * matrix[0] * state.tz / 100
        word_modes = {e.state.font.unit for e in events}
        if state.tw and len(word_modes) != 1:
            raise PdfError("selected fonts have different effective word-spacing semantics")
        if state.tw and state.font.unit == 1 and state.font.decode(b" ")[0][1] != " ":
            raise PdfError("source word-spacing code is not a Unicode space")
        # PDF Tw applies to a one-byte 0x20, including a newly introduced
        # space when the original selected text happened to contain none.
        word_space = state.tw * matrix[0] * state.tz / 100 if state.font.unit == 1 else 0
        def advance(g):
            return g.advance * x_scale + (word_space if g.text == " " else 0)
        def run_width_and_inset(text):
            run = font.shape(text)
            pen = minimum = maximum = 0.0
            for g in run.glyphs:
                bounds = font.ink(g.gid)
                if bounds is not None:
                    minimum = min(minimum, pen + (g.x_offset + bounds[0]) * x_scale)
                    maximum = max(maximum, pen + (g.x_offset + bounds[2]) * x_scale)
                pen += advance(g) + tracking
            maximum = max(maximum, pen - tracking if run.glyphs else 0)
            return maximum - minimum, -minimum
        def measure(text):
            return run_width_and_inset(text)[0]
        size = state.size * matrix[3]
        ascender, descender = font.ascender * size, font.descender * size
        x = min(g.origin[0] for g in resolved.lines[0].glyphs)
        baseline = resolved.lines[0].baseline
        observed_leading = min((b.baseline - a.baseline for a, b in zip(resolved.lines, resolved.lines[1:])
                               if b.baseline > a.baseline), default=size * 1.4)
        leading = line_height if line_height is not None else max(observed_leading, ascender - descender)
        if leading < ascender - descender - .001:
            raise PdfError("line height is smaller than the provided font's vertical metrics")
        bottom = content.page.rect.height
        if max_height is not None:
            if not math.isfinite(max_height) or max_height <= 0:
                raise PdfError("maximum height must be positive and finite")
            bottom = min(bottom, baseline - ascender + max_height)
        text = replacement.replace("\r\n", "\n").replace("\r", "\n")
        layout = layout_text(text, x=x, baseline=baseline, width=width, line_height=leading,
                             ascender=ascender, descender=descender, measure=measure, max_bottom=bottom)
        runs = [font.shape(line.text) for line in layout.lines]
        resource = font.resource(runs)
        existing = content.pdf_page["/Resources"].get_object().get("/Font", {})
        alias_index = 1
        while f"/PEF{alias_index}" in existing:
            alias_index += 1
        alias = f"/PEF{alias_index}"
        commands = [b" q ", alias.encode(), b" ", number(state.size), b" Tf 0 Tc 0 Tw 0 Ts "]
        inverse = ~(pymupdf.Matrix(*state.ctm) * content.page.transformation_matrix)
        plan, ink = [], []
        for line, run in zip(layout.lines, runs):
            # A negative side bearing is real ink, not extra available width.
            # Fit it inside the confirmed region and include it in line width.
            pen = line.x + run_width_and_inset(line.text)[1]
            for g in run.glyphs:
                gx, gy = pen + g.x_offset * x_scale, line.baseline - g.y_offset * y_scale
                point = pymupdf.Point(gx, gy) * inverse
                tm = (*first.text_matrix[:4], point.x, point.y)
                commands.extend((matrix_operator(tm), b"<" + resource.code(g).hex().encode() + b"> Tj "))
                plan.append({"unicode": g.text, "glyph_id": g.gid, "origin": [gx, gy],
                             "advance": advance(g), "nominal_advance": font.nominal_width(g.gid) * x_scale,
                             "pdf_nominal_width": font.nominal_width(g.gid) * 1000 / font.upem,
                             "offset": [g.x_offset * x_scale, -g.y_offset * y_scale], "cid": int.from_bytes(resource.code(g), "big")})
                bounds = font.ink(g.gid)
                if bounds is not None:
                    a, b, c, d = bounds
                    rect = Rect(gx + a*x_scale, gy - d*y_scale, gx + c*x_scale, gy - b*y_scale)
                    if not Rect(x, baseline-ascender, x+width, bottom).contains(rect, .001):
                        raise PdfError("glyph outline extends outside the confirmed composition region")
                    if not Rect(*content.page.rect).contains(rect, .001):
                        raise PdfError("composed glyph is outside the page")
                    allow_clip(state, rect, content.page.transformation_matrix)
                    ink.append(rect)
                pen += advance(g) + tracking
        # Insertion follows the first removal operator. Thus a quote's implicit
        # T* and spacing changes execute exactly once, before we insert text.
        delta_advance = sum(a.advance if isinstance(a, PaintChar) else -float(a)/1000*state.size*state.tz/100 for a in first.atoms)
        after_matrix = translate(first.text_matrix, delta_advance, 0)
        delta = multiply(after_matrix, tuple(~pymupdf.Matrix(*first.line_matrix)))
        if abs(delta[5]) > .001 or any(abs(a-b) > 1e-5 for a,b in zip(delta[:4], (1,0,0,1))):
            raise PdfError("cannot restore both original text matrices after composition")
        commands.extend((b"Q ", matrix_operator(first.line_matrix), b"["+number(-delta[4]/(state.size*state.tz/100)*1000)+b"] TJ "))
        data = content.streams[virtual]
        for event in sorted(events, key=lambda e:e.operator.start, reverse=True):
            new = serialized_event(event, selected, remove=True)
            if event is first:
                new += b"".join(commands)
            data = data[:event.operator.start] + new + data[event.operator.end:]
        ink_bounds = ink[0] if ink else Rect(x, baseline, x, baseline)
        for rect in ink[1:]:
            ink_bounds = ink_bounds.union(rect)
        _check_obstacles(content, selected, resolved, ink, ink_bounds)
        affected = resolved.bbox.union(ink_bounds)
        old_fonts = font_fingerprints(content.document, pno)
        expected_text = "".join(line.text for line in layout.lines)
        def verify(document):
            if len(document) != len(content.document) or document.permissions != content.document.permissions or document.metadata.get("encryption") != content.document.metadata.get("encryption"):
                raise PdfError("composition changed page count or security")
            remaining = defaultdict(list)
            for g in untouched:
                remaining[_signature(g)].append(g)
            new, kept = [], []
            for g in _observations(document[pno]):
                key = _signature(g)
                match = next((i for i, old in enumerate(remaining[key])
                              if compare_glyphs([old], [g])["passed"]), None)
                if match is not None:
                    remaining[key].pop(match)
                    kept.append(g)
                else:
                    new.append(g)
            if any(remaining.values()) or not compare_glyphs(untouched, kept)["passed"]:
                raise PdfError("composition changed unselected glyphs")
            if "".join(g["unicode"] for g in new) != expected_text:
                raise PdfError("composed Unicode does not match the shaped line plan")
            painted = [g for g in new if g["glyph_id"] >= 0]
            if len(painted) != len(plan):
                raise PdfError("composed glyph count does not match shaping")
            for actual, wanted in zip(painted, plan):
                if actual["glyph_id"] != wanted["glyph_id"] or max(abs(a-b) for a,b in zip(actual["origin"], wanted["origin"])) > .002:
                    raise PdfError("saved glyph ID or origin differs from HarfBuzz position plan")
                if abs(actual["size"]-size) > .002 or actual["paint_type"] != 0 or actual["color"] != original[min(selected)]["color"] or actual["opacity"] != state.opacity:
                    raise PdfError("composed font size or paint state changed")
            current_fonts = font_fingerprints(document, pno)
            if [f for f in current_fonts if f[0][3] != alias[1:]] != old_fonts:
                raise PdfError("an existing font resource changed")
            added = [f for f in current_fonts if f[0][3] == alias[1:]]
            if len(added) != 1 or added[0][1] != hashlib.sha256(resource.program).hexdigest():
                raise PdfError("new embedded font program differs from the shaping subset")
            if not all(_pixels_equal(content.document[i], document[i], affected if i==pno else None) for i in range(len(document))):
                raise PdfError("composition changed pixels outside the selected and newly painted region")
        publish_program(source, pno, data, output, verify, font_builders={alias:resource.build})
        if removal_output:
            publish_program(source, pno, removed, removal_output,
                            lambda doc: _require_removal(untouched, doc[pno]))
        return {"schema_version":1, "backend":"harfbuzz-truetype-composition", "source_sha256":source_sha(source),
                "selection":manifest, "output":str(output), "before":"".join(g.text for line in resolved.lines for g in line.glyphs),
                "after":replacement, "composed_text":expected_text, "font_substituted":True,
                "font":{"provided_name":font.name, "source_sha256":font.source_sha256, "instance_sha256":font.instance_sha256,
                        "subset_sha256":hashlib.sha256(resource.program).hexdigest(), "font_index":font.font_index,
                        "variations":font.variations, "original_fonts":sorted({g["font"] for i,g in enumerate(original) if i in selected}),
                        "resource":alias, "policy":"Explicit provided font replaces the selected range; face identity is not inferred from its name."},
                "widths":asdict(resolved.widths), "font_size":size, "tracking":tracking,
                "word_spacing":word_space, "line_height":leading,
                "old_line_count":len(resolved.lines), "new_line_count":len(layout.lines),
                "lines":[asdict(line) for line in layout.lines], "new_bbox":asdict(ink_bounds), "audit_bbox":asdict(affected),
                "glyph_plan":plan, "removal_audit":removal_audit, "source_replay_mupdf_passed":True,
                "mupdf_outside_pixels_equal":True, "existing_fonts_preserved":True,
                "positioning":"HarfBuzz cluster advances/offsets set each Tm; CID /W holds the same font's nominal hmtx widths.",
                "independent_renderer_verified":False}
    finally:
        content.close()


def _require_removal(expected, page):
    if not compare_glyphs(expected, _observations(page))["passed"]:
        raise PdfError("saved removal checkpoint changed unselected text")
