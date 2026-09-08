"""Read-only probe of MuPDF paint order and clipping callbacks.

This evaluation is not an editing backend or a proof of path containment. It
records bounds, not the complete geometry of clip paths, glyphs, or masks.
No PDF text, image data, or font program is included in its JSON output.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import pymupdf

mupdf = pymupdf.mupdf
PAINTS = ("fill_path", "stroke_path", "fill_text", "stroke_text", "ignore_text",
          "fill_shade", "fill_image", "fill_image_mask")
SCOPES = ("clip_path", "clip_stroke_path", "clip_text", "clip_stroke_text",
          "clip_image_mask", "pop_clip", "begin_group", "end_group",
          "begin_mask", "end_mask", "begin_tile", "end_tile")


def bounds(rect):
    return [rect.x0, rect.y0, rect.x1, rect.y1]


class PaintObserver(mupdf.FzDevice2):
    """Observe renderer callbacks; never retain borrowed native pointers."""

    def __init__(self):
        super().__init__()
        self.events, self.clips, self.errors = [], [], []
        self.seqno = 0
        for name in PAINTS + SCOPES:
            getattr(self, "use_virtual_" + name)()

    def push(self, name, rect=None, rule=None):
        item = dict(event=name, before_paint=self.seqno, depth=len(self.clips),
                    bounds=rect, even_odd=rule)
        self.clips.append(item)
        self.events.append(item)

    def clip_path(self, ctx, path, even_odd, ctm, scissor):
        self.push("clip_path", bounds(mupdf.ll_fz_bound_path(path, None, ctm)), bool(even_odd))

    def clip_stroke_path(self, ctx, path, stroke, ctm, scissor):
        self.push("clip_stroke_path", bounds(mupdf.ll_fz_bound_path(path, stroke, ctm)))

    def clip_text(self, ctx, text, ctm, scissor):
        self.push("clip_text", bounds(mupdf.ll_fz_bound_text(text, None, ctm)))

    def clip_stroke_text(self, ctx, text, stroke, ctm, scissor):
        self.push("clip_stroke_text", bounds(mupdf.ll_fz_bound_text(text, stroke, ctm)))

    def clip_image_mask(self, ctx, image, ctm, scissor):
        self.push("clip_image_mask", bounds(mupdf.ll_fz_transform_rect(mupdf.fz_unit_rect, ctm)))

    def pop_clip(self, ctx):
        self.events.append(dict(event="pop_clip", before_paint=self.seqno, depth=len(self.clips)))
        if self.clips:
            self.clips.pop()
        else:
            self.errors.append("pop_clip without an observed clip")

    def paint(self, name, rect):
        self.events.append(dict(event=name, seqno=self.seqno, bounds=rect,
                                clips=[dict(clip) for clip in self.clips]))
        self.seqno += 1

    def fill_path(self, ctx, path, even_odd, ctm, *args):
        self.paint("fill_path", bounds(mupdf.ll_fz_bound_path(path, None, ctm)))

    def stroke_path(self, ctx, path, stroke, ctm, *args):
        self.paint("stroke_path", bounds(mupdf.ll_fz_bound_path(path, stroke, ctm)))

    def fill_text(self, ctx, text, ctm, *args):
        self.paint("fill_text", bounds(mupdf.ll_fz_bound_text(text, None, ctm)))

    def stroke_text(self, ctx, text, stroke, ctm, *args):
        self.paint("stroke_text", bounds(mupdf.ll_fz_bound_text(text, stroke, ctm)))

    def ignore_text(self, ctx, text, ctm):
        self.paint("ignore_text", bounds(mupdf.ll_fz_bound_text(text, None, ctm)))

    def fill_image(self, ctx, image, ctm, *args):
        self.paint("fill_image", bounds(mupdf.ll_fz_transform_rect(mupdf.fz_unit_rect, ctm)))

    def fill_image_mask(self, ctx, image, ctm, *args):
        self.paint("fill_image_mask", bounds(mupdf.ll_fz_transform_rect(mupdf.fz_unit_rect, ctm)))

    def fill_shade(self, ctx, shade, ctm, *args):
        self.paint("fill_shade", bounds(mupdf.ll_fz_bound_shade(shade, ctm)))

    def begin_group(self, ctx, bbox, cs, isolated, knockout, blendmode, alpha):
        self.events.append(dict(event="begin_group", before_paint=self.seqno, bounds=bounds(bbox),
                                isolated=bool(isolated), knockout=bool(knockout),
                                blendmode=blendmode, opacity=alpha))

    def end_group(self, ctx):
        self.events.append(dict(event="end_group", before_paint=self.seqno))

    def begin_mask(self, *args):
        self.events.append(dict(event="begin_mask", before_paint=self.seqno))

    def end_mask(self, *args):
        self.push("mask")  # Its pixels/transfer function are deliberately unknown.

    def begin_tile(self, *args):
        self.events.append(dict(event="begin_tile", before_paint=self.seqno))
        return 0

    def end_tile(self, ctx):
        self.events.append(dict(event="end_tile", before_paint=self.seqno))


def observe(source, page_number=1):
    source = Path(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with pymupdf.open(source) as document:
        if document.needs_pass:
            raise ValueError("an authenticated document is required; this probe accepts no password")
        if not 1 <= page_number <= len(document):
            raise ValueError("page is outside this document")
        page = document[page_number - 1]
        rotation = page.rotation
        try:
            # get_bboxlog/get_texttrace use unrotated page coordinates too.
            if rotation:
                page.set_rotation(0)
            observer = PaintObserver()
            mupdf.fz_run_page(page.this, observer, mupdf.FzMatrix(), mupdf.FzCookie())
            mupdf.fz_close_device(observer)
            reference = page.get_bboxlog()
            trace = page.get_texttrace()
        finally:
            if rotation:
                page.set_rotation(rotation)
        paints = [event for event in observer.events if event["event"] in PAINTS]
        errors = list(observer.errors)
        if len(reference) != len(paints):
            errors.append("paint count differs from get_bboxlog")
        max_error = 0.0
        for i, (paint, expected) in enumerate(zip(paints, reference)):
            kind = "fill-imgmask" if paint["event"] == "fill_image_mask" else paint["event"].replace("_", "-")
            if kind != expected[0]:
                errors.append(f"paint {i}: kind differs from get_bboxlog")
            distance = max(abs(a - b) for a, b in zip(paint["bounds"], expected[1]))
            max_error = max(max_error, distance)
            if distance > 1e-5:
                errors.append(f"paint {i}: bounds differ from get_bboxlog")
        trace_kinds = {0: "fill_text", 1: "stroke_text", 3: "ignore_text"}
        for span in trace:
            i = span["seqno"]
            if not 0 <= i < len(paints) or paints[i]["event"] != trace_kinds.get(span["type"]):
                errors.append(f"texttrace seqno {i} does not identify the corresponding text paint kind")
        if observer.clips:
            errors.append("clip stack is nonempty after page traversal")
        if source_hash != hashlib.sha256(source.read_bytes()).hexdigest():
            errors.append("source bytes changed")
        return dict(schema_version=1, source_sha256=source_hash, page=page_number,
                    pymupdf=pymupdf.VersionBind, mupdf=pymupdf.VersionFitz,
                    probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    passed=not errors, errors=errors, paint_events=len(paints),
                    bboxlog_events=len(reference), texttrace_spans=len(trace),
                    max_bbox_error_pt=max_error, remaining_clip_depth=len(observer.clips),
                    event_counts=dict(Counter(e["event"] for e in observer.events)),
                    form_references=len(page.get_xobjects()), events=observer.events,
                    scope="Ordered observation only; bounds do not prove filled geometry, visibility, or editability.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--page", type=int, default=1, help="One-based page number")
    parser.add_argument("--output", type=Path, help="New JSON report; excludes source text and binary assets")
    parser.add_argument("--details", action="store_true", help="Include individual callback bounds and clip stacks")
    args = parser.parse_args(argv)
    if args.output and (args.output.resolve() == args.source.resolve() or args.output.exists()):
        parser.error("output must be a new file distinct from the source")
    report = observe(args.source, args.page)
    if not args.details:
        report.pop("events")
    data = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
    else:
        print(data, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
