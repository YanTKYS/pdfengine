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
from pathlib import Path
import sys

import pymupdf

from pdfeditor.backend import PdfError
from pdfeditor.explicit_reflow import edit_reflow
from pdfeditor.replay import glyph_observations
from pdfeditor.selection import resolve_selection, source_sha
from pdfeditor.slot_edit import edit_slots
from evaluations.realpdf.evaluate import DEFAULT_POPPLER, DEFAULT_PYPDF, independent_text, pixel_diff, pix_image, poppler_render, write_json


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
    result = {"before": before, "after": after}
    if before.get("rendered") and after.get("rendered"):
        with pymupdf.open(source) as original, pymupdf.open(output) as edited:
            before_image = pix_image(original[page - 1], 144)
            after_image = pix_image(edited[page - 1], 144)
        result["poppler_diff"] = pixel_diff(before_image, after_image, mask=bbox, dpi=144)
        # A glyph's anti-aliased fringe can extend a fraction beyond its
        # vector bbox. The one-point margin is reported separately so a few
        # fringe pixels are not confused with damage to adjacent content.
        margin = (bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1)
        result["poppler_diff_with_1pt_margin"] = pixel_diff(before_image, after_image, mask=margin, dpi=144)
    else:
        result["poppler_diff"] = {"rendered": False}
    with pymupdf.open(source) as original, pymupdf.open(output) as edited:
        result["mupdf_page_pixels"] = original[page - 1].get_pixmap(dpi=144, alpha=False).samples == edited[page - 1].get_pixmap(dpi=144, alpha=False).samples
        result["page_count_equal"] = len(original) == len(edited)
        result["outside_page_text_equal"] = [original[i].get_text() for i in range(len(original)) if i != page - 1] == [edited[i].get_text() for i in range(len(edited)) if i != page - 1]
    return result


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
        try:
            if stage == 4:
                report = edit_reflow(source, output, manifest, replacement, stage1_report=gate)
            else:
                report = edit_slots(source, output, manifest, replacement, stage=stage, stage1_report=gate)
            item.update(status="passed", classification="success", report=report)
            item["render_audit"] = render_audit(source, output, page, directory / f"stage{stage}", tuple(resolved.bbox.tuple()))
            item["independent_text"] = independent_text(DEFAULT_PYPDF, output, directory / f"stage{stage}-extract.json")
            pages = item["independent_text"].get("pages", [])
            item["old_selected_text_remains_in_extraction"] = bool(pages and original and original in "".join(pages))
            item["outside_visual_damage"] = item["render_audit"].get("poppler_diff_with_1pt_margin", {}).get("outside_gt8_pixels", 0) > 0
            if item["outside_visual_damage"]:
                item["status"] = "rejected"
                item["classification"] = "edit_outside_visual_difference"
                item["error"] = "Poppler difference remains outside the selected bbox plus 1 point margin"
            item["output_sha256"] = source_sha(output)
            if not output.exists() or source_sha(source) == item["output_sha256"]:
                item["classification"] = "edit_output_not_distinct"
        except Exception as exc:
            item.update(status="rejected", classification=result_class("rejected", str(exc)), error_type=type(exc).__name__, error=str(exc), output_created=output.exists())
        base["stages"][str(stage)] = item
    return base


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-name", default="stages_final")
    parser.add_argument("--stage1-run", default="stage1_final_v2")
    args = parser.parse_args()
    global STAGE1
    STAGE1 = BASE / "runs" / args.stage1_run
    output_root = BASE / "runs" / args.run_name
    output_root.mkdir(parents=True, exist_ok=False)
    cases = json.loads(SELECTIONS.read_text(encoding="utf-8"))
    stage1 = {x["case"]["id"]: x for x in json.loads((STAGE1 / "results.json").read_text(encoding="utf-8"))}
    results = [run_one(case, stage1[case["id"]], output_root / case["id"]) for case in cases]
    write_json(output_root / "results.json", results)
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
