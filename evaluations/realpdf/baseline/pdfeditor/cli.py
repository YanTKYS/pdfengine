"""Small CLI for inspecting inferred regions and editing one region."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .engine import edit_pdf, inspect_pdf


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Reconstruct and reflow existing PDF text (horizontal Japanese/Latin PoC)")
    sub = result.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="show glyphs, runs, lines, paragraphs and inferred boxes")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--page", type=int, default=1, help="1-based page number")
    inspect.add_argument("--json", type=Path, dest="json_path", help="write full model to a new JSON file")
    edit = sub.add_parser("edit", help="edit and reflow one inferred text box")
    edit.add_argument("input", type=Path)
    edit.add_argument("output", type=Path)
    edit.add_argument("--page", type=int, default=1)
    edit.add_argument("--find", help="unique substring in the inferred region")
    edit.add_argument("--box", dest="box_id", help="region ID from inspect; without --find replaces the whole region")
    replacement = edit.add_mutually_exclusive_group(required=True)
    replacement.add_argument("--text", help="replacement text")
    replacement.add_argument("--text-file", type=Path, help="UTF-8 text; preserves explicit line breaks")
    edit.add_argument("--width", type=float, help="available region width in PDF points")
    edit.add_argument("--max-height", type=float, help="maximum new region height in points")
    edit.add_argument("--font", type=Path, dest="font_path", help="fallback font file when original font cannot encode the edit")
    edit.add_argument("--report", type=Path, help="write layout/font decisions to a new JSON file")
    return result


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.command == "inspect":
            model = inspect_pdf(args.input, args.page)
            if args.json_path:
                write_json(args.json_path, model)
            print(json.dumps({"page": model["page"], "boxes": [
                {key: b[key] for key in ("id", "bbox", "available_width", "width_source", "text", "warnings")}
                for b in model["boxes"]]}, ensure_ascii=False, indent=2))
        else:
            if args.report and (args.report.exists() or args.report.resolve() in {args.input.resolve(), args.output.resolve()}):
                raise ValueError("report must be a new path distinct from input/output")
            text = args.text if args.text is not None else args.text_file.read_text(encoding="utf-8-sig")
            report = edit_pdf(args.input, args.output, page=args.page, find=args.find, replacement=text,
                              box_id=args.box_id, width=args.width, max_height=args.max_height, font_path=args.font_path)
            if args.report:
                write_json(args.report, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"pdfeditor: {exc}", file=sys.stderr)
        return 2
