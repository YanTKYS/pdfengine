"""Inspect inferred regions or explicitly select source glyphs for staged editing."""

from __future__ import annotations

import argparse
from dataclasses import asdict
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
    observe = sub.add_parser("observe", help="catalog observed Line/Run/glyph IDs without selecting an inferred Box")
    observe.add_argument("input", type=Path)
    observe.add_argument("--page", type=int, default=1, help="1-based page number")
    observe.add_argument("--json", type=Path, dest="json_path", help="write the complete catalog to a new JSON file")

    select = sub.add_parser("select", help="create a source-bound selection manifest and preview its exact glyph range")
    select.add_argument("input", type=Path)
    select.add_argument("output", type=Path, help="new selection JSON path")
    select.add_argument("--page", type=int, help="1-based page; defaults to 1 or the existing selection's page")
    select.add_argument("--from-selection", type=Path, help="correct an existing manifest; unchanged glyphs remain selected")
    select.add_argument("--line", "--add-line", dest="line_ids", action="append", default=[], help="observed Line ID; repeat to add lines")
    select.add_argument("--run", dest="run_ids", action="append", default=[], help="observed Run ID; repeat to add runs")
    select.add_argument("--glyph", dest="glyph_ids", type=int, action="append", default=[], help="observed source-order glyph ID; repeat to add glyphs")
    select.add_argument("--exclude-line", dest="exclude_line_ids", action="append", default=[], help="remove this Line after additions; repeat as needed")
    select.add_argument("--width", type=float, help="explicitly confirmed available width in PDF points; unknown if omitted")

    replay = sub.add_parser("replay", help="Stage 1 no-op replay of a manual selection; verifies MuPDF only",
                            description="Replay original glyph codes at the original operators. The CLI verifies MuPDF; a separate independent-renderer Stage 1 proof is required before edit-selected.")
    replay.add_argument("input", type=Path)
    replay.add_argument("output", type=Path)
    replay.add_argument("--selection", type=Path, required=True, help="source-bound selection JSON")
    replay.add_argument("--removal-output", type=Path, help="optional new PDF showing the actual removal checkpoint")
    replay.add_argument("--report", type=Path, help="new JSON report; MuPDF verification alone does not open the later-stage gate")

    selected = sub.add_parser("edit-selected", help="replace a manual selection after an independently verified Stage 1")
    selected.add_argument("input", type=Path)
    selected.add_argument("output", type=Path)
    selected.add_argument("--selection", type=Path, required=True, help="the exact selection used for Stage 1")
    selected.add_argument("--stage", type=int, choices=(2, 3, 4), required=True,
                          help="2: same glyph count; 3: shorten original slots; 4: reflow within explicit width")
    selected.add_argument("--stage1-report", type=Path, required=True,
                          help="evaluation proof for this exact source/selection, including independent renderer checks")
    selected_text = selected.add_mutually_exclusive_group(required=True)
    selected_text.add_argument("--text", help="replacement text")
    selected_text.add_argument("--text-file", type=Path, help="UTF-8 text; preserves explicit line breaks")
    selected.add_argument("--report", type=Path, help="new JSON report of the staged edit")
    compose = sub.add_parser("compose-selected", help="recompose a confirmed region with an explicitly supplied font")
    compose.add_argument("input", type=Path)
    compose.add_argument("output", type=Path)
    compose.add_argument("--selection", type=Path, required=True)
    compose.add_argument("--font", type=Path, required=True, dest="font_path", help="explicit replacement font; preserves neither face identity nor the original subset codes")
    compose.add_argument("--font-index", type=int, default=0, help="face index in a TrueType collection")
    compose.add_argument("--font-variation", action="append", default=[], metavar="TAG=VALUE", help="explicit variation axis, e.g. wght=400; otherwise use font defaults")
    compose.add_argument("--width", type=float, help="confirmed available width; overrides the selection width")
    compose.add_argument("--max-height", type=float, help="confirmed maximum height from the new font's top")
    compose.add_argument("--line-height", type=float, help="explicit baseline interval; otherwise retain observed spacing when possible")
    compose_text = compose.add_mutually_exclusive_group(required=True)
    compose_text.add_argument("--text")
    compose_text.add_argument("--text-file", type=Path)
    compose.add_argument("--report", type=Path)
    compose.add_argument("--removal-output", type=Path)
    paragraph = sub.add_parser("inspect-paragraph", help="review source-linked Unicode and style intervals")
    paragraph.add_argument("input", type=Path)
    paragraph.add_argument("--selection", type=Path, required=True)
    paragraph.add_argument("--json", type=Path, required=True, dest="json_path")
    paragraph.add_argument("--line-joiner", choices=("none", "space", "newline"), default="none",
                           help="how selected physical lines form logical text; review the exported text")
    rich = sub.add_parser("edit-paragraph", help="edit Unicode intervals while retaining unedited source fonts/styles")
    rich.add_argument("input", type=Path)
    rich.add_argument("output", type=Path)
    rich.add_argument("--paragraph", type=Path, required=True)
    rich.add_argument("--edits", type=Path, required=True, help="JSON object with edits list and supplied fonts mapping")
    rich.add_argument("--width", type=float)
    rich.add_argument("--x", type=float)
    rich.add_argument("--first-line-indent", type=float)
    rich.add_argument("--max-bottom", type=float, help="confirmed bottom coordinate in PDF points, y down")
    rich.add_argument("--min-line-height", type=float)
    rich.add_argument("--report", type=Path)
    rich.add_argument("--removal-output", type=Path)
    rich.add_argument("--element", type=Path, help="source-bound paint/element snapshot")
    rich.add_argument("--relations", type=Path, help="JSON object with explicit relations list")
    rich.add_argument("--anchors", type=Path, help="confirmed Unicode decoration anchors and fixed relations")
    rich.add_argument("--editable-state", type=Path, help="save revision-bound logical text, roles and region in a sidecar")
    reopen = sub.add_parser("open-editable", help="restore bound editing semantics, or return observations for confirmation")
    reopen.add_argument("input", type=Path)
    reopen.add_argument("--state", type=Path)
    reopen.add_argument("--page", type=int, default=1)
    reopen.add_argument("--json", type=Path, required=True, dest="json_path")
    document = sub.add_parser("edit-document", help="edit a saved logical document without re-inferring line joins or ownership")
    document.add_argument("input", type=Path)
    document.add_argument("output", type=Path)
    document.add_argument("--state", type=Path, required=True)
    document.add_argument("--state-output", type=Path, required=True)
    document.add_argument("--edits", type=Path, required=True)
    document.add_argument("--width", type=float)
    document.add_argument("--max-bottom", type=float)
    document.add_argument("--report", type=Path)
    document.add_argument("--removal-output", type=Path)
    anchors = sub.add_parser("inspect-anchors", help="propose source-bound decoration ranges for confirmation")
    anchors.add_argument("input", type=Path)
    anchors.add_argument("--paragraph", type=Path, required=True)
    anchors.add_argument("--element", type=Path, required=True)
    anchors.add_argument("--json", type=Path, required=True, dest="json_path")
    element = sub.add_parser("inspect-element", help="observe source paint provenance and possible ownership")
    element.add_argument("input", type=Path)
    element.add_argument("--selection", type=Path, required=True)
    element.add_argument("--json", type=Path, required=True, dest="json_path")
    move = sub.add_parser("move-element", help="move confirmed text and vector members together; MuPDF verification only")
    move.add_argument("input", type=Path)
    move.add_argument("output", type=Path)
    move.add_argument("--element", type=Path, required=True)
    move.add_argument("--relations", type=Path, required=True)
    move.add_argument("--dx", type=float, default=0)
    move.add_argument("--dy", type=float, default=0)
    move.add_argument("--report", type=Path)
    move.add_argument("--removal-output", type=Path)
    return result


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def check_new_outputs(outputs: list[Path | None], inputs: list[Path | None]) -> None:
    """Check all destination collisions before publishing any staged artifact."""
    destinations = [path.resolve() for path in outputs if path is not None]
    protected = {path.resolve() for path in inputs if path is not None}
    if len(destinations) != len(set(destinations)):
        raise ValueError("output, report, and removal paths must be distinct")
    if any(path in protected or path.exists() for path in destinations):
        raise ValueError("outputs must be new paths distinct from input and selection/proof files")


def replacement_text(args) -> str:
    return args.text if args.text is not None else args.text_file.read_text(encoding="utf-8-sig")


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
                {key: b[key] for key in ("id", "bbox", "available_width", "observed_content_width",
                                        "inferred_available_width", "explicitly_supplied_width",
                                        "width_source", "text", "warnings")}
                for b in model["boxes"]]}, ensure_ascii=False, indent=2))
        elif args.command == "observe":
            from .selection import inspect_selection_source
            catalog = inspect_selection_source(args.input, args.page)
            if args.json_path:
                check_new_outputs([args.json_path], [args.input])
                write_json(args.json_path, catalog)
            print(json.dumps(catalog, ensure_ascii=False, indent=2))
        elif args.command == "select":
            from .selection import make_selection, resolve_selection
            check_new_outputs([args.output], [args.input, args.from_selection])
            existing = read_json(args.from_selection) if args.from_selection else None
            if existing is not None:
                resolve_selection(args.input, existing)
                if args.page is not None and args.page != existing["page"]:
                    raise ValueError("a corrected selection must retain its original page")
            page = args.page if args.page is not None else (existing["page"] if existing else 1)
            glyph_ids = (existing["glyph_ids"] if existing else []) + args.glyph_ids
            width = args.width if args.width is not None else (existing.get("explicitly_supplied_width") if existing else None)
            manifest = make_selection(args.input, page, line_ids=args.line_ids, run_ids=args.run_ids,
                                      glyph_ids=glyph_ids, exclude_line_ids=args.exclude_line_ids,
                                      explicit_width=width)
            resolved = resolve_selection(args.input, manifest)
            write_json(args.output, manifest)
            print(json.dumps({"selection": manifest, "preview": {
                "bbox": asdict(resolved.bbox), "lines": [line.text for line in resolved.lines],
                "selected_glyphs": len(resolved.glyphs), "widths": asdict(resolved.widths)}},
                ensure_ascii=False, indent=2))
        elif args.command == "replay":
            from .replay import no_op_replay
            check_new_outputs([args.output, args.report, args.removal_output], [args.input, args.selection])
            report = no_op_replay(args.input, args.output, read_json(args.selection),
                                  removal_output=args.removal_output)
            report.update({"stage1_passed": False, "poppler_stage1_passed": False,
                           "stage1_validation_scope": "MuPDF only. Independent renderer, text, and paint-state validation must complete before Stages 2-4; this CLI report alone is not a passing gate proof."})
            if args.report:
                write_json(args.report, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "edit-selected":
            check_new_outputs([args.output, args.report], [args.input, args.selection, args.stage1_report, args.text_file])
            manifest, proof = read_json(args.selection), read_json(args.stage1_report)
            text = replacement_text(args)
            if args.stage == 4:
                from .explicit_reflow import edit_reflow
                report = edit_reflow(args.input, args.output, manifest, text, stage1_report=proof)
            else:
                from .slot_edit import edit_slots
                report = edit_slots(args.input, args.output, manifest, text, stage=args.stage, stage1_report=proof)
            if args.report:
                write_json(args.report, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "inspect-paragraph":
            from .attributed import inspect_paragraph
            check_new_outputs([args.json_path], [args.input, args.selection])
            report = inspect_paragraph(args.input, read_json(args.selection),
                line_joiner={"none":"", "space":" ", "newline":"\n"}[args.line_joiner])
            write_json(args.json_path, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "edit-paragraph":
            from .paragraph import edit_paragraph
            if bool(args.element)!=(bool(args.relations) or bool(args.anchors)) or (args.relations and args.anchors):
                raise ValueError("--element requires exactly one of --relations or --anchors")
            changes = read_json(args.edits)
            fonts = changes.get("fonts", {})
            if not isinstance(fonts, dict) or not isinstance(changes.get("edits"), list):
                raise ValueError("edits JSON needs an edits list and a fonts object")
            for spec in fonts.values():
                if not isinstance(spec, dict) or not isinstance(spec.get("path"), str):
                    raise ValueError("each supplied font needs a path")
                path = Path(spec["path"])
                spec["path"] = str(path if path.is_absolute() else (args.edits.parent/path).resolve())
            check_new_outputs([args.output, args.report, args.removal_output, args.editable_state],
                [args.input, args.paragraph, args.edits, args.element, args.relations, args.anchors, *(Path(s["path"]) for s in fonts.values())])
            extra={}
            if args.editable_state:
                from .editable import write_editable
                extra={'model_output':args.editable_state,'boundary_kinds':changes.get('boundary_kinds'),
                       'empty_style_id':changes.get('empty_style_id')}
                edit_paragraph=write_editable
            report = edit_paragraph(args.input, args.output, snapshot=read_json(args.paragraph), edits=changes["edits"],
                fonts=fonts, width=args.width, x=args.x, first_line_indent=args.first_line_indent,
                max_bottom=args.max_bottom, min_line_height=args.min_line_height, removal_output=args.removal_output,
                element_snapshot=read_json(args.element) if args.element else None,
                element_relations=read_json(args.relations).get("relations") if args.relations else None,
                anchor_spec=read_json(args.anchors) if args.anchors else None,**extra)
            if args.report:
                write_json(args.report, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "open-editable":
            from .editable import open_editable
            check_new_outputs([args.json_path],[args.input,args.state])
            report=open_editable(args.input,args.state,page=args.page)
            write_json(args.json_path,report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
        elif args.command == "edit-document":
            from .editable import edit_document
            changes=read_json(args.edits);fonts=changes.get('fonts',{})
            if not isinstance(fonts,dict) or not isinstance(changes.get('edits'),list):
                raise ValueError('edits JSON needs an edits list and a fonts object')
            for spec in fonts.values():
                if not isinstance(spec,dict) or not isinstance(spec.get('path'),str):
                    raise ValueError('each supplied font needs a path')
                path=Path(spec['path'])
                spec['path']=str(path if path.is_absolute() else (args.edits.parent/path).resolve())
            check_new_outputs([args.output,args.state_output,args.report,args.removal_output],
                              [args.input,args.state,args.edits,*(Path(s['path']) for s in fonts.values())])
            overrides={k:getattr(args,k) for k in ('width','max_bottom','removal_output') if getattr(args,k) is not None}
            report=edit_document(args.input,args.state,args.output,args.state_output,changes['edits'],fonts=fonts,
                                 boundary_kinds=changes.get('boundary_kinds'),empty_style_id=changes.get('empty_style_id'),**overrides)
            if args.report:write_json(args.report,report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
        elif args.command == "inspect-anchors":
            from .anchors import inspect_anchors
            check_new_outputs([args.json_path],[args.input,args.paragraph,args.element])
            report=inspect_anchors(args.input,read_json(args.paragraph),read_json(args.element))
            write_json(args.json_path,report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
        elif args.command == "inspect-element":
            from .elements import inspect_element
            check_new_outputs([args.json_path],[args.input,args.selection])
            report=inspect_element(args.input,read_json(args.selection))
            write_json(args.json_path,report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
        elif args.command == "move-element":
            from .elements import move_element
            check_new_outputs([args.output,args.report,args.removal_output],[args.input,args.element,args.relations])
            report=move_element(args.input,args.output,read_json(args.element),read_json(args.relations).get("relations"),
                                dx=args.dx,dy=args.dy,removal_output=args.removal_output)
            if args.report:
                write_json(args.report,report)
            print(json.dumps(report,ensure_ascii=False,indent=2))
        elif args.command == "compose-selected":
            from .composition import compose_selected
            check_new_outputs([args.output, args.report, args.removal_output], [args.input, args.selection, args.text_file, args.font_path])
            manifest = read_json(args.selection)
            if args.width is not None:
                manifest["explicitly_supplied_width"] = args.width
            variations = {}
            for item in args.font_variation:
                tag, value = item.split("=", 1)
                if tag in variations:
                    raise ValueError("font variation axis supplied twice")
                variations[tag] = float(value)
            report = compose_selected(args.input, args.output, manifest, replacement_text(args),
                font_source=args.font_path, font_index=args.font_index, variations=variations,
                max_height=args.max_height, line_height=args.line_height, removal_output=args.removal_output)
            if args.report:
                write_json(args.report, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "edit":
            if args.report and (args.report.exists() or args.report.resolve() in {args.input.resolve(), args.output.resolve()}):
                raise ValueError("report must be a new path distinct from input/output")
            text = replacement_text(args)
            report = edit_pdf(args.input, args.output, page=args.page, find=args.find, replacement=text,
                              box_id=args.box_id, width=args.width, max_height=args.max_height, font_path=args.font_path)
            if args.report:
                write_json(args.report, report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"pdfeditor: {exc}", file=sys.stderr)
        return 2
