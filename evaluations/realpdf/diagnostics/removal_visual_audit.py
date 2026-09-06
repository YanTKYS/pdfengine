"""Isolated removal diagnostics: not guarded edits and never production PDFs.

Runs the existing backend's exact removal primitive with an empty layout.
It does not change validation or suppress the primitive's integrity exception.
Rasters can still be inspected after an exception because mutations are only
in memory. This is deliberate evidence collection, not accepted edit output.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw
import pymupdf

ROOT = Path(__file__).resolve().parent
# Freeze the evaluated primitive even if production code changes afterward.
sys.path.insert(0, str(ROOT.parent / "baseline"))
from pdfeditor import backend
from pdfeditor.layout import LayoutResult
from evaluations.realpdf.evaluate import (
    drawings_fingerprint, image_fingerprints, pixel_diff, pix_image, sha,
    signature, write_json,
)

CORPUS = ROOT.parent / "corpus"
OUTPUT = ROOT / "removal_visual"
DPI = 144


def exact_graphics(value):
    if isinstance(value, dict):
        return {k: exact_graphics(v) for k,v in value.items() if k != "seqno"}
    if isinstance(value, (list, tuple, pymupdf.Rect, pymupdf.Point, pymupdf.Quad)):
        return [exact_graphics(v) for v in value]
    return value


def leaf_diffs(before, after, path=""):
    if isinstance(before, dict) and isinstance(after,dict) and before.keys() == after.keys():
        return [r for k in before for r in leaf_diffs(before[k],after[k],f"{path}/{k}")]
    if isinstance(before,list) and isinstance(after,list) and len(before)==len(after):
        return [r for i,(a,b) in enumerate(zip(before,after)) for r in leaf_diffs(a,b,f"{path}/{i}")]
    if before != after:
        result = {"path":path,"before":before,"after":after}
        if isinstance(before,(float,int)) and isinstance(after,(float,int)):
            result["absolute_delta"] = abs(before-after)
        return [result]
    return []


def nearest_audit(expected, actual):
    """Match same-Unicode observations by closest origin, then report drift.

    This audit is descriptive; the production signature comparison remains
    untouched. No source/after glyph is used more than once.
    """
    available = defaultdict(list)
    for index, glyph in enumerate(actual):
        available[glyph.text].append((index, glyph))
    used, matches, missing = set(), [], []
    for original in expected:
        candidates = [(max(abs(original.origin[k] - glyph.origin[k]) for k in (0, 1)), index, glyph)
                      for index, glyph in available[original.text] if index not in used]
        if not candidates:
            missing.append({"text": original.text, "origin": original.origin})
            continue
        distance, index, after = min(candidates, key=lambda item: item[0])
        if distance > .25:
            missing.append({"text": original.text, "origin": original.origin})
            continue
        used.add(index)
        matches.append({"text": original.text, "before_origin": original.origin,
                        "after_origin": after.origin, "origin_max_delta_pt": distance,
                        "bbox_max_delta_pt": max(abs(a-b) for a,b in zip(original.bbox.tuple(), after.bbox.tuple())),
                        "font_unchanged": original.style.font == after.style.font,
                        "size_delta_pt": abs(original.style.size-after.style.size),
                        "paint_unchanged": original.visible == after.visible,
                        "signature_changed": signature(original) != signature(after)})
    return {"same_unicode_counts": Counter(g.text for g in expected) == Counter(g.text for g in actual),
            "expected_count": len(expected), "actual_count": len(actual), "matched_within_quarter_pt": len(matches),
            "unmatched_expected": missing, "unmatched_actual": [{"text":g.text,"origin":g.origin} for i,g in enumerate(actual) if i not in used],
            "max_origin_delta_pt": max((m["origin_max_delta_pt"] for m in matches), default=0),
            "max_bbox_delta_pt": max((m["bbox_max_delta_pt"] for m in matches), default=0),
            "all_fonts_unchanged": all(m["font_unchanged"] for m in matches),
            "max_size_delta_pt": max((m["size_delta_pt"] for m in matches), default=0),
            "all_paint_unchanged": all(m["paint_unchanged"] for m in matches),
            "signature_changed_count": sum(m["signature_changed"] for m in matches),
            "signature_changes": [m for m in matches if m["signature_changed"]][:100]}


def visible_fill_only(box):
    paragraphs = []
    for para in box.paragraphs:
        lines = []
        for line in para.lines:
            runs = [replace(run, glyphs=[g for g in run.glyphs if g.visible]) for run in line.runs]
            runs = [run for run in runs if run.glyphs]
            if runs:
                lines.append(replace(line, runs=runs))
        if lines:
            paragraphs.append(replace(para, lines=lines))
    return replace(box, paragraphs=paragraphs)


def one_case(source, box_id, fill_only=False):
    path = CORPUS / f"{source}.pdf"
    page_number = int(box_id.split("-")[0][1:]) - 1
    name = f"{source}--{box_id}" + ("--fill-only" if fill_only else "")
    out = OUTPUT / name
    out.mkdir(parents=True, exist_ok=True)
    record = {"id":name, "source":source, "box":box_id, "page":page_number+1,
              "probe":"existing removal primitive only; no layout/redraw; no production guards bypassed in edits",
              "in_memory_only":True, "pdf_saved":False, "fill_only_subset":fill_only,
              "source_sha256":sha(path), "dpi":DPI,
              "backend_path":str(Path(backend.__file__).relative_to(ROOT.parent.parent.parent)),
              "backend_sha256":sha(Path(backend.__file__)), "pymupdf_version":pymupdf.VersionBind}
    with pymupdf.open(path) as doc:
        model = backend.extract_page(doc, page_number)
        box = next(b for b in model.boxes if b.id == box_id)
        if fill_only:
            box = visible_fill_only(box)
        selected = {g.source_order for g in box.glyphs if g.source_order >= 0}
        expected = [g for g in model.glyphs if g.source_order not in selected]
        record.update({"selected_text":box.text,"selected_bbox_pt":box.bbox.tuple(),
                       "selected_glyphs":len(selected),"original_glyphs":len(model.glyphs),
                       "selected_visible_fill_glyphs":sum(g.visible for g in box.glyphs),
                       "selected_nonfill_glyphs":sum(not g.visible for g in box.glyphs)})
        before_images, before_drawings = image_fingerprints(doc[page_number]), drawings_fingerprint(doc[page_number])
        before_exact_graphics = exact_graphics(doc[page_number].get_drawings())
        record.update({"original_images":len(before_images),"original_drawings":len(doc[page_number].get_drawings())})
        before = pix_image(doc[page_number], DPI)
        before.save(out / "before.png")
        remaining = None
        original_extract = backend.extract_page
        def capture(document, page_number):
            nonlocal remaining
            remaining = original_extract(document, page_number)
            return remaining
        try:
            with patch.object(backend, "extract_page", capture):
                backend.replace_region(doc, page_number, model, box, LayoutResult([], box.bbox, box.line_height), None)
            record["primitive_status"] = "passed"
        except Exception as exc:
            record.update({"primitive_status":"rejected", "error":str(exc)})
        after = pix_image(doc[page_number], DPI)
        after.save(out / "after-removal-only.png")
        ImageChops.difference(before, after).save(out / "difference.png")
        if remaining is None:
            remaining = backend.extract_page(doc, page_number)
        record["exact_signature_match"] = Counter(signature(g) for g in expected) == Counter(signature(g) for g in remaining.glyphs)
        record["remaining_audit"] = nearest_audit(expected, remaining.glyphs)
        record["images_unchanged"] = before_images == image_fingerprints(doc[page_number])
        record["drawings_unchanged"] = before_drawings == drawings_fingerprint(doc[page_number])
        graphic_diffs = leaf_diffs(before_exact_graphics,exact_graphics(doc[page_number].get_drawings()))
        record["drawings_exact_numeric_comparison"] = {"leaf_differences":len(graphic_diffs),
            "max_absolute_numeric_delta":max((d.get("absolute_delta",0) for d in graphic_diffs),default=0),
            "differences":graphic_diffs[:100]}
        record["visual_tight_bbox"] = pixel_diff(before,after,box.bbox.tuple(),DPI)
        mask = [box.bbox.x0-2,box.bbox.y0-2,box.bbox.x1+2,box.bbox.y1+2]
        record["visual_2pt_padded_bbox"] = pixel_diff(before,after,mask,DPI)
        record["visual_mask_2pt"] = mask
        diff_max = ImageChops.difference(before,after).split()
        diff_max = ImageChops.lighter(ImageChops.lighter(diff_max[0],diff_max[1]),diff_max[2])
        d = ImageDraw.Draw(diff_max)
        d.rectangle([int(mask[0]*DPI/72),int(mask[1]*DPI/72),int(mask[2]*DPI/72+.999),int(mask[3]*DPI/72+.999)],fill=0)
        record["outside_max_channel_difference"] = diff_max.getextrema()[1]
        crop = [max(0,int((box.bbox.x0-20)*DPI/72)),max(0,int((box.bbox.y0-20)*DPI/72)),
                min(before.width,int((box.bbox.x1+20)*DPI/72)),min(before.height,int((box.bbox.y1+20)*DPI/72))]
        left,right = before.crop(crop),after.crop(crop)
        montage = Image.new("RGB",(left.width*2,left.height+24),"white")
        montage.paste(left,(0,24)); montage.paste(right,(left.width,24))
        d = ImageDraw.Draw(montage)
        d.text((3,4),"Original",fill="black"); d.text((left.width+3,4),"Isolated removal only",fill="black")
        montage.save(out / "comparison-crop.png")
        # Unedited pages must stay bit-identical when rasterized even after
        # rewriting the edited page's content streams in memory.
        with pymupdf.open(path) as original:
            record["other_pages"] = [{"page":i+1,**pixel_diff(pix_image(original[i],DPI),pix_image(doc[i],DPI),dpi=DPI)}
                                      for i in range(len(doc)) if i != page_number]
        record["extracted_remaining_text"] = doc[page_number].get_text()
    record["original_file_unchanged"] = sha(path)==record["source_sha256"]
    write_json(out / "result.json",record)
    print(json.dumps({"case":name,"status":record["primitive_status"],
                      "outside_changed_pixels":record["visual_2pt_padded_bbox"]["outside_changed_pixels"],
                      "max_origin_delta":record["remaining_audit"]["max_origin_delta_pt"],
                      "signature_changed":record["remaining_audit"]["signature_changed_count"],
                      "images_unchanged":record["images_unchanged"],"drawings_unchanged":record["drawings_unchanged"]}),flush=True)
    return record


def main():
    results = [one_case(source,box,fill) for source,box,fill in [
        ("print_fcc_ms","p1-b2",False),
        ("print_canvia","p1-b7",False),
        ("print_canvia","p2-b6",False),
        ("word_osaka_fire_notice","p1-b9",False),
        ("word_osaka_fire_notice","p1-b9",True),
        ("word_niigata_hearing","p1-b1",False),
    ]]
    write_json(OUTPUT / "results.json",results)


if __name__ == "__main__":
    main()
