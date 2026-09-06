"""Stages 2-4 on the independently gated manual selections.

This runner never promotes a Stage 1 rejection. It records successful edits and
safe refusals separately, renders both MuPDF and Poppler, and keeps the source
PDF immutable. Replacement strings are reviewable inputs below; they are not
PDF-specific engine branches.
"""
from __future__ import annotations

from collections import Counter
import csv
import json
import math
from pathlib import Path
import sys

from PIL import Image
import pymupdf

from pdfeditor.backend import PdfError
from pdfeditor.explicit_reflow import edit_reflow
from pdfeditor.selection import resolve_selection, source_sha
from pdfeditor.slot_edit import edit_slots
from evaluations.realpdf.evaluate import DEFAULT_POPPLER, DEFAULT_PYPDF, independent_text, pixel_diff, poppler_render, write_json


BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent.parent
SELECTIONS = BASE / "selections.json"
STAGE1 = BASE / "runs" / "stage1_final"
OUTPUT = BASE / "runs" / "stages_final"

# These are human-reviewable test strings. The replacement is deliberately
# supplied to the backend as Unicode; acceptance still requires the original
# PDF code map and width checks to pass.
STAGE2_PHRASES = {
    "word_osaka_fire_notice": ("別紙", "別添"),
    "word_osaka_guideline": ("送付", "添付"),
    "word_wakayama_guidelines": ("代表", "応募"),
    "word_takeo_notice": ("協定", "協議"),
    "lo_migration_ja": ("病院", "施設"),
    "lo_newfeatures_ja": ("注目", "機能"),
    "lo_enterprise_en": ("ISO", "IEC"),
    "print_canvia": ("Login", "Admin"),
    "print_fcc_ms": ("liste", "laste"),
    "print_fcc_chrome": ("Canvas", "Chrome"),
    "word_kyoto_questions": ("発送", "送付"),
    "word_niigata_hearing": ("概要", "要約"),
    "word_okinawa_procurement": ("募集", "公募"),
}

STAGE4_TEXT = {
    # The width is supplied in the frozen manual manifest. This is an explicit
    # width proof, not a claim that the observed two-line width is available.
    "word_kyoto_questions": "質問 新規認定店への送付資料として、ポスターを制作及び印刷します。",
    "word_niigata_hearing": "１ 素案の概要 概要 概要 概要 概要 概要 概要",
    "word_okinawa_procurement": "沖縄県次世代型校務支援システム県域共同調達業務について、次のとおり企画提案を公募する。",
}


def selected_text(source: Path, manifest: dict) -> str:
    resolved = resolve_selection(source, manifest)
    return "".join(g.text for g in sorted(resolved.glyphs, key=lambda g: g.source_order))


def fallback_same_count(text: str) -> str:
    chars = list(text)
    for i in range(len(chars)):
        if chars[i].isspace():
            continue
        for j in range(i + 1, len(chars)):
            if not chars[j].isspace() and chars[i] != chars[j]:
                chars[i], chars[j] = chars[j], chars[i]
                return "".join(chars)
    return text


def stage2_text(case_id: str, original: str) -> tuple[str, str]:
    old_new = STAGE2_PHRASES.get(case_id)
    if old_new and old_new[0] in original:
        return original.replace(*old_new, 1), f"review phrase {old_new[0]!r}->{old_new[1]!r}"
    replacement = fallback_same_count(original)
    return replacement, "same-count source-glyph substitution fallback"


def result_class(status: str, error: str | None = None) -> str:
    if status == "passed":
        return "success"
    if status == "skipped":
        return "stage_gate_skip"
    message = error or ""
    if any(x in message.lower() for x in ("font resource cannot encode", "notdef", "unicode")):
        return "font_coverage_or_unicode_rejection"
    if any(x in message.lower() for x in ("collides", "intersects", "outside", "vertical space")):
        return "safe_layout_rejection"
    if "advance" in message.lower() or "slot" in message.lower():
        return "safe_width_or_slot_rejection"
    return "safe_rejection"


def render_audit(source: Path, output: Path, page: int, directory: Path, bbox: tuple[float, ...]) -> dict:
    before_png = directory / "before.png"
    after_png = directory / "after.png"
    before = poppler_render(DEFAULT_POPPLER, source, page, before_png, dpi=144)
    after = poppler_render(DEFAULT_POPPLER, output, page, after_png, dpi=144)
    result = {"before": before, "after": after, "renderer": "Poppler", "dpi": 144,
              "mask_bbox_pt": list(bbox)}
    if before.get("rendered") and after.get("rendered"):
        # Read the actual independent renderer outputs, never MuPDF pixmaps.
        with Image.open(before_png) as before_image, Image.open(after_png) as after_image:
            result["poppler_diff"] = pixel_diff(before_image, after_image, mask=bbox, dpi=144)
            # The separately reported one-point margin covers raster fringes.
            margin = (bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1)
            result["poppler_diff_with_1pt_margin"] = pixel_diff(before_image, after_image, mask=margin, dpi=144)
    else:
        result["poppler_diff"] = {"rendered": False}
    with pymupdf.open(source) as original, pymupdf.open(output) as edited:
        def same_pixels(a, b):
            first, second = a.get_pixmap(dpi=144, alpha=False), b.get_pixmap(dpi=144, alpha=False)
            return (first.width, first.height, first.n, first.samples) == (second.width, second.height, second.n, second.samples)
        result["mupdf_page_pixels"] = 1 <= page <= min(len(original), len(edited)) and same_pixels(original[page - 1], edited[page - 1])
        result["page_count_equal"] = len(original) == len(edited)
        result["outside_page_text_equal"] = [original[i].get_text() for i in range(len(original)) if i != page - 1] == [edited[i].get_text() for i in range(len(edited)) if i != page - 1]
        result["outside_page_mupdf_pixels_equal"] = len(original) == len(edited) and all(
            same_pixels(original[i], edited[i])
            for i in range(len(original)) if i != page - 1)
    return result


class EvaluationFailure(ValueError):
    """An output exists, but the evidence does not establish a passing edit."""


def require_render_audit(audit: dict) -> None:
    if not all(audit.get(label, {}).get("rendered") is True for label in ("before", "after")):
        raise EvaluationFailure("independent renderer failed; no visual pass can be established")
    difference = audit.get("poppler_diff_with_1pt_margin", {})
    if difference.get("size_changed") is not False:
        raise EvaluationFailure("independent renderer image dimensions changed or the difference is unavailable")
    outside = difference.get("outside_gt8_pixels")
    if type(outside) is not int or outside < 0:
        raise EvaluationFailure("independent renderer outside-region pixel count is unavailable")
    if outside:
        raise EvaluationFailure("Poppler difference remains outside the allowed edit bounds plus 1 point margin")
    if audit.get("page_count_equal") is not True:
        raise EvaluationFailure("saved page count changed or was not verified")
    if audit.get("outside_page_text_equal") is not True:
        raise EvaluationFailure("text on unedited pages changed or was not verified")
    if audit.get("outside_page_mupdf_pixels_equal") is not True:
        raise EvaluationFailure("MuPDF pixels on unedited pages changed or were not verified")


def edit_audit_bounds(resolved, report: dict, stage: int) -> tuple[float, ...]:
    old = resolved.bbox.tuple()
    if stage != 4:
        return old
    # Only the successful Stage 4 backend's verified text bounds may enlarge
    # the mask. The available width or full page is never used as a mask.
    new = report.get("new_bbox")
    if report.get("stage") != 4 or not isinstance(new, dict):
        raise EvaluationFailure("Stage 4 report lacks verified new text bounds")
    try:
        values = tuple(float(new[k]) for k in ("x0", "y0", "x1", "y1"))
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationFailure("invalid Stage 4 new text bounds") from exc
    if not all(math.isfinite(v) for v in values) or values[0] > values[2] or values[1] > values[3]:
        raise EvaluationFailure("invalid Stage 4 new text bounds")
    return (min(old[0], values[0]), min(old[1], values[1]),
            max(old[2], values[2]), max(old[3], values[3]))


def independent_edit_audit(before: dict, after: dict, page: int, original: str, replacement: str) -> dict:
    """Verify one selected occurrence was replaced, allowing unchanged copies.

    Full-page extraction has no object IDs. Accept any ONE matching occurrence,
    while the backend's glyph-provenance checks establish the selected identity.
    The exact normalized page comparison also works when replacement contains
    the old text, when the edit is a no-op, or when other old copies remain.
    """
    for label, result in (("source", before), ("output", after)):
        pages = result.get("pages")
        if result.get("error") or not isinstance(pages, list) or not pages or not all(isinstance(p, str) for p in pages):
            raise EvaluationFailure(f"independent {label} text extraction failed or returned no pages")
    before_pages, after_pages = before["pages"], after["pages"]
    if len(before_pages) != len(after_pages) or not 1 <= page <= len(before_pages):
        raise EvaluationFailure("independent extraction page counts do not match")
    if any(a != b for i, (a, b) in enumerate(zip(before_pages, after_pages), 1) if i != page):
        raise EvaluationFailure("independent extraction changed text on an unedited page")
    normalize = lambda text: "".join(text.split())
    old, new = normalize(original), normalize(replacement)
    source_page, output_page = normalize(before_pages[page - 1]), normalize(after_pages[page - 1])
    if not old:
        raise EvaluationFailure("empty normalized selection cannot establish an extraction control")
    occurrences = []
    start = 0
    while (index := source_page.find(old, start)) >= 0:
        occurrences.append(index)
        start = index + 1
    if not occurrences:
        raise EvaluationFailure("selected text is absent from independent source extraction; control failed")
    matched = any(source_page[:i] + new + source_page[i + len(old):] == output_page for i in occurrences)
    if not matched:
        raise EvaluationFailure("independent output text does not equal a single intended replacement")
    return {"passed": True, "normalization": "remove whitespace from both target-page strings and both selection strings",
            "source_occurrences": len(occurrences), "old_text_present_in_output": old in output_page,
            "replacement_contains_original": old in new,
            "policy": "one occurrence replaced in the complete normalized target page; other pages exactly unchanged"}


def run_one(case: dict, stage1_result: dict, directory: Path) -> dict:
    source = PROJECT / case["source"]
    manifest = case["selection"]
    page = manifest["page"]
    base = {"case": case["id"], "source": str(source), "source_sha256": source_sha(source),
            "stage1_status": stage1_result["status"], "stages": {}}
    if stage1_result["status"] != "passed":
        for stage in (2, 3, 4):
            base["stages"][str(stage)] = {"status": "skipped", "classification": "stage_gate_skip", "reason": stage1_result.get("error")}
        return base
    gate = json.loads((STAGE1 / case["id"] / "gate.json").read_text(encoding="utf-8"))
    original = selected_text(source, manifest)
    resolved = resolve_selection(source, manifest)
    directory.mkdir(parents=True, exist_ok=True)
    source_extraction = None
    for stage in (2, 3, 4):
        if stage == 2:
            replacement, rationale = stage2_text(case["id"], original)
        elif stage == 3:
            replacement, rationale = (original[:-2], "shorten by removing the final two observed characters") if len(original) > 3 else ("", "shorten to empty")
        else:
            if manifest.get("explicitly_supplied_width") is None or case["id"] not in STAGE4_TEXT:
                base["stages"][str(stage)] = {"status": "skipped", "classification": "width_unknown_rejection", "reason": "explicitly_supplied_width is absent"}
                continue
            replacement, rationale = STAGE4_TEXT[case["id"]], "explicit manual width reflow"
        output = directory / f"stage{stage}.pdf"
        item = {"status": "pending", "stage": stage, "original": original, "replacement": replacement,
                "rationale": rationale, "selection_widths": {"observed_content_width": resolved.widths.observed_content_width,
                "inferred_available_width": resolved.widths.inferred_available_width,
                "explicitly_supplied_width": resolved.widths.explicitly_supplied_width}}
        auditing = False
        try:
            if stage == 4:
                report = edit_reflow(source, output, manifest, replacement, stage1_report=gate)
            else:
                report = edit_slots(source, output, manifest, replacement, stage=stage, stage1_report=gate)
            item["report"] = report
            auditing = True
            if not output.is_file() or source_sha(source) == source_sha(output):
                raise EvaluationFailure("backend did not produce a distinct saved output")
            bounds = edit_audit_bounds(resolved, report, stage)
            item["render_audit"] = render_audit(source, output, page, directory / f"stage{stage}", bounds)
            outside = item["render_audit"].get("poppler_diff_with_1pt_margin", {}).get("outside_gt8_pixels")
            item["outside_visual_damage"] = outside > 0 if type(outside) is int else None
            require_render_audit(item["render_audit"])
            if source_extraction is None:
                source_extraction = independent_text(DEFAULT_PYPDF, source, directory / "source-extract.json")
            item["independent_source_text"] = source_extraction
            item["independent_text"] = independent_text(DEFAULT_PYPDF, output, directory / f"stage{stage}-extract.json")
            item["independent_edit_audit"] = independent_edit_audit(source_extraction, item["independent_text"], page, original, replacement)
            item["old_selected_text_remains_in_extraction"] = item["independent_edit_audit"]["old_text_present_in_output"]
            item["output_sha256"] = source_sha(output)
            if source_sha(source) != base["source_sha256"]:
                raise EvaluationFailure("source PDF changed during evaluation")
            item.update(status="passed", classification="success")
        except Exception as exc:
            item.update(status="failed_evaluation" if auditing else "rejected",
                        classification="evaluation_failure" if auditing else result_class("rejected", str(exc)),
                        error_type=type(exc).__name__, error=str(exc), output_created=output.exists())
        base["stages"][str(stage)] = item
    return base


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", default="stages_final")
    parser.add_argument("--stage1-run", default="stage1_audit_v3")
    args = parser.parse_args()
    global STAGE1
    STAGE1 = BASE / "runs" / args.stage1_run
    output_root = BASE / "runs" / args.run_name
    output_root.mkdir(parents=True, exist_ok=False)
    cases = json.loads(SELECTIONS.read_text(encoding="utf-8"))
    stage1 = {x["case"]["id"]: x for x in json.loads((STAGE1 / "results.json").read_text(encoding="utf-8"))}
    write_json(output_root / "environment.json", {
        "pymupdf": pymupdf.VersionBind, "stage1_run": args.stage1_run,
        "evaluation_sha256": {"followup.py": source_sha(Path(__file__)),
                              "evaluate.py": source_sha(PROJECT / "evaluations/realpdf/evaluate.py")},
        "engine_sha256": {p.name: source_sha(p) for p in (PROJECT / "pdfeditor").glob("*.py")},
    })
    results = []
    for case in cases:
        directory = output_root / case["id"]
        result = run_one(case, stage1[case["id"]], directory)
        results.append(result)
        write_json(directory / "result.json", result)
        write_json(output_root / "results.json", results)
        print(json.dumps({"case": case["id"], "stages": {
            stage: {"status": item["status"], "error": item.get("error")}
            for stage, item in result["stages"].items()}}, ensure_ascii=False), flush=True)
    summary = Counter()
    rows = []
    for result in results:
        for stage, item in result["stages"].items():
            summary[f"stage{stage}:{item['status']}"] += 1
            rows.append({"case": result["case"], "stage": stage, "stage1_status": result["stage1_status"],
                         "status": item["status"], "classification": item.get("classification"),
                         "error": item.get("error", ""), "replacement": item.get("replacement", "")})
    with (output_root / "results.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    write_json(output_root / "summary.json", dict(summary))
    print(json.dumps(dict(summary), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
