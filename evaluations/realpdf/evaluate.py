"""Evaluate the frozen/current engine against original external PDFs.

No document-specific exception is introduced into the engine. Explicit width
experiments are named separately; no guard is disabled for production edits.
Removal-only probes use in-memory copies, never claim full edit support, and
are recorded separately from guarded end-to-end attempts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import unicodedata
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw
import pymupdf

BASE = Path(__file__).resolve().parent
PROJECT = BASE.parent.parent
RUNTIME = Path("C:/Users/agri0/.cache/codex-runtimes/codex-primary-runtime/dependencies")
DEFAULT_POPPLER = RUNTIME / "native/poppler/Library/bin/pdftoppm.exe"
DEFAULT_PYPDF = RUNTIME / "python/python.exe"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


def signature(g):
    return g.text, round(g.origin[0], 2), round(g.origin[1], 2)


def image_fingerprints(page):
    return sorted((tuple(round(v, 3) for v in item["bbox"]), item["digest"].hex())
                  for item in page.get_image_info(hashes=True))


def drawings_fingerprint(page):
    def clean(v):
        if isinstance(v, float):
            return round(v, 3)
        if isinstance(v, dict):
            return {k: clean(value) for k, value in v.items() if k not in {"seqno"}}
        if isinstance(v, (list, tuple, pymupdf.Rect, pymupdf.Point, pymupdf.Quad)):
            return [clean(value) for value in v]
        return v
    return hashlib.sha256(json.dumps(clean(page.get_drawings()), sort_keys=True).encode()).hexdigest()


def pix_image(page, dpi=96):
    p = page.get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB)
    return Image.frombytes("RGB", (p.width, p.height), p.samples)


def pixel_diff(before, after, mask=None, dpi=96):
    if before.size != after.size:
        return {"size_changed": True}
    difference = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
    channels = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    total_hist = maximum.histogram()
    result = {"size_changed": False, "all_changed_pixels": sum(total_hist[1:]),
              "all_gt8_pixels": sum(total_hist[9:]), "all_bbox_px": maximum.getbbox()}
    if mask:
        maximum = maximum.copy()
        draw = ImageDraw.Draw(maximum)
        draw.rectangle([int(mask[0]*dpi/72), int(mask[1]*dpi/72),
                        int(mask[2]*dpi/72+.999), int(mask[3]*dpi/72+.999)], fill=0)
    hist = maximum.histogram()
    result.update({"outside_changed_pixels": sum(hist[1:]), "outside_gt8_pixels": sum(hist[9:]),
                   "outside_bbox_px": maximum.getbbox()})
    return result


def poppler_render(executable, pdf, page, output, dpi=96):
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(executable), "-png", "-r", str(dpi), "-f", str(page), "-l", str(page),
               "-singlefile", str(pdf), str(output.with_suffix(""))]
    result = subprocess.run(command, capture_output=True, timeout=90)
    return {"returncode": result.returncode, "stderr": result.stderr.decode("utf-8", errors="replace")[:4000],
            "rendered": result.returncode == 0 and output.exists()}


def independent_text(python, pdf, output):
    result = subprocess.run([str(python), str(BASE / "independent_extract.py"), str(pdf), str(output)],
                            capture_output=True, timeout=90)
    if output.exists():
        return json.loads(output.read_text(encoding="utf-8"))
    return {"error": result.stderr.decode("utf-8", errors="replace")[:2000]}


def written_geometry(report, page, font):
    """Audit appended output against planned positions, not text presence only."""
    expected = []
    for line in report["lines"]:
        x = line["x"]
        for char in line["text"]:
            expected.append((ord(char), x, line["baseline"]))
            x += font.text_length(char, fontsize=report["font_size"]) + report["tracking"]
    all_chars = [c for span in page.get_texttrace() for c in span["chars"]]
    actual = all_chars[-len(expected):] if expected else []
    x_errors = [abs(a[2][0]-e[1]) for a,e in zip(actual,expected)]
    y_errors = [abs(a[2][1]-e[2]) for a,e in zip(actual,expected)]
    codes_match = [a[0] for a in actual] == [e[0] for e in expected]
    tolerance = max(.12, report["font_size"]*.025)
    return {"unicode_sequence_match": codes_match, "glyphs": len(actual),
            "max_x_error_pt": max(x_errors,default=0), "max_y_error_pt": max(y_errors,default=0),
            "tolerance_pt": tolerance,
            "passed": codes_match and max(x_errors+y_errors,default=0)<=tolerance}


def security_state(document):
    return {"encryption": document.metadata.get("encryption"), "permissions": document.permissions}


def inventory(files, backend, destination):
    summaries = []
    for path in files:
        started = time.monotonic()
        with pymupdf.open(path) as doc:
            item = {"id": path.stem, "path": str(path.relative_to(PROJECT)), "sha256": sha(path),
                    "metadata": doc.metadata, "pages": [], "fonts": []}
            seen = set()
            for number, page in enumerate(doc):
                model = backend.extract_page(doc, number)
                trace = page.get_texttrace()
                raw = page.get_text("dict")
                lines = [l for block in raw["blocks"] if block["type"] == 0 for l in block["lines"]]
                detail = {"page": number+1, "size": [model.width, model.height],
                          "glyphs": len(model.glyphs), "unmapped": sum(g.text == "\ufffd" for g in model.glyphs),
                          "paint_spans": len(trace), "single_glyph_paint_spans": sum(len(s["chars"]) == 1 for s in trace),
                          "reference_extractor_lines": len(lines), "runs": sum(len(l.runs) for b in model.boxes for l in b.lines),
                          "lines": sum(len(b.lines) for b in model.boxes), "paragraphs": sum(len(b.paragraphs) for b in model.boxes),
                          "drawings": len(page.get_drawings()), "images": len(page.get_image_info()),
                          "clips": sum(d["type"] == "clip" for d in page.get_drawings(extended=True)),
                          "boxes": [{"id": b.id, "bbox": b.bbox.tuple(), "available_width": b.available_width,
                                     "width_source": b.width_source, "lines": len(b.lines),
                                     "runs": sum(len(l.runs) for l in b.lines), "paragraphs":len(b.paragraphs),
                                     "font_names": sorted({g.style.font for g in b.glyphs}),
                                     "text": b.text, "warnings": b.warnings} for b in model.boxes]}
                item["pages"].append(detail)
                for entry in page.get_fonts(full=True):
                    if entry[0] in seen:
                        continue
                    seen.add(entry[0])
                    data = doc.extract_font(entry[0])[3]
                    item["fonts"].append({"xref":entry[0], "type":entry[2], "basefont":entry[3],
                                          "encoding":entry[5], "embedded_bytes":len(data),
                                          "subset_name":bool(re.match(r"^[A-Z]{6}\+",entry[3]))})
        write_json(destination / f"{path.stem}.json", item)
        summaries.append({"id":path.stem, "pages":len(item["pages"]), "glyphs":sum(p["glyphs"] for p in item["pages"]),
                          "unmapped":sum(p["unmapped"] for p in item["pages"]), "elapsed":round(time.monotonic()-started,2)})
        print(json.dumps(summaries[-1]), flush=True)
    write_json(destination / "summary.json", summaries)


def reject_category(message):
    for phrase, category in [
        ("mixed styles", "mixed_style"), ("nonuniform", "nonuniform_spacing"),
        ("writer glyph positions", "writer_geometry"),
        ("glyph widths differ", "custom_glyph_width"), ("clipping", "clipping"),
        ("transparency", "paint_state"), ("invisible", "paint_state"),
        ("unmapped", "unicode_mapping"), ("requires shaping", "shaping"),
        ("no glyphs", "missing_glyph"), ("matching", "selection"),
        ("collides", "collision"), ("outside its", "collision"), ("intersects", "annotation"),
        ("vertical space", "overflow"), ("too narrow", "width_too_narrow"),
        ("removal affected", "removal_integrity"), ("rotated", "rotation")]:
        if phrase in message:
            return category
    return "other"


def run_case(case, output_root, engine, backend, fonts, layout_module, poppler, pypdf_python):
    source = BASE / "corpus" / f"{case['source']}.pdf"
    result = {"case":case, "input_sha256":sha(source), "classification":[], "status":"pending"}
    directory = output_root / case["id"]
    directory.mkdir(parents=True, exist_ok=False)
    output = directory / "edited.pdf"
    with pymupdf.open(source) as doc:
        model = backend.extract_page(doc, case["page"]-1)
        box = next(b for b in model.boxes if b.id == case["box"])
        text = box.text if case["operation"] == "noop" else case["text"]
        result["selected_box"] = {"id":box.id, "text":box.text, "bbox":box.bbox.tuple(),
                                  "line_count":len(box.lines), "paragraphs":len(box.paragraphs),
                                  "runs":sum(len(l.runs) for l in box.lines), "available_width":box.available_width,
                                  "width_source":box.width_source, "warnings":box.warnings}
        write_json(directory / "model.json", model.to_dict())
        selected = {g.source_order for g in box.glyphs if g.source_order >= 0}
        expected = Counter(signature(g) for g in model.glyphs if g.source_order not in selected)
        original_images = image_fingerprints(doc[case["page"]-1])
        original_drawings = drawings_fingerprint(doc[case["page"]-1])
        # This independent font diagnostic runs even when paint/style guards refuse editing.
        result["font_probes"] = {}
        for probe_name, probe in [("original",box.text),("new_kanji",box.text+"麒麟鬱"),
                                   ("alphanumeric",box.text+" Wi-2026 A9"),("symbols",box.text+" ※①€")]:
            try:
                resolved = fonts.resolve_font(doc, case["page"]-1, box.glyphs[0].style.font, probe)
                result["font_probes"][probe_name] = resolved.report()
            except Exception as exc:
                result["font_probes"][probe_name] = {"error":str(exc)}

    audit = {}
    original_extract = backend.extract_page
    def capture_removal(document, number):
        remaining = original_extract(document, number)
        actual = Counter(signature(g) for g in remaining.glyphs)
        audit.update({"expected_untouched":sum(expected.values()), "actual_remaining":sum(actual.values()),
                      "exact_signature_match":expected == actual,
                      "unexpected_remaining":list((actual-expected).elements())[:30],
                      "lost_untouched":list((expected-actual).elements())[:30],
                      "images_unchanged":image_fingerprints(document[number]) == original_images,
                      "drawings_unchanged":drawings_fingerprint(document[number]) == original_drawings})
        return remaining

    try:
        with patch.object(backend, "extract_page", capture_removal):
            report = engine.edit_pdf(source, output, page=case["page"], box_id=case["box"],
                                     replacement=text, width=case.get("width"))
        result.update({"status":"written", "report":report, "removal_audit":dict(audit)})
    except Exception as exc:
        result.update({"status":"rejected", "error_type":type(exc).__name__, "error":str(exc),
                       "rejection_category":reject_category(str(exc)), "removal_audit":dict(audit)})
        result["classification"].append("safe_rejection" if not output.exists() and sha(source)==result["input_sha256"] else "corruption")

    # Isolate the existing removal primitive for blocked documents. Never save
    # this diagnostic PDF or bypass any guard in the end-to-end edit results.
    if not audit:
        try:
            with pymupdf.open(source) as doc, patch.object(backend, "extract_page", capture_removal):
                empty = layout_module.LayoutResult([], box.bbox, box.line_height)
                backend.replace_region(doc, case["page"]-1, model, box, empty, None)
            result["removal_only_probe"] = {"status":"passed", **audit}
        except Exception as exc:
            result["removal_only_probe"] = {"status":"failed", "error":str(exc), **audit}
    if result["status"] == "written":
        report = result["report"]
        old, new = box.bbox, report["new_bbox"]
        # Documented mask: enclosing union of old/new regions plus half an em
        # for ink overhang. Also report exact whole-page differences separately.
        pad = box.glyphs[0].style.size * .5
        mask = [min(old.x0,new["x0"])-pad,min(old.y0,new["y0"])-pad,
                max(old.x1,new["x1"])+pad,max(old.y1,new["y1"])+pad]
        result["visual_mask_pt"] = mask
        comparisons = []
        with pymupdf.open(source) as original, pymupdf.open(output) as edited:
            result["security"] = {"before":security_state(original), "after":security_state(edited),
                                  "preserved":security_state(original)==security_state(edited)}
            selected_font = fonts.resolve_font(original,case["page"]-1,box.glyphs[0].style.font,text)
            result["written_geometry"] = written_geometry(report,edited[case["page"]-1],selected_font.font)
            result["page_count_preserved"] = len(original) == len(edited)
            for index in range(len(original)):
                before, after = pix_image(original[index]),pix_image(edited[index])
                difference = pixel_diff(before, after, mask if index+1==case["page"] else None)
                comparisons.append({"page":index+1, **difference})
            result["mupdf_all_pages_diff"] = comparisons
            current = backend.extract_page(edited, case["page"]-1)
            outside = Counter(signature(g) for g in current.glyphs)
            result["untouched_text_signatures_retained"] = not (expected-outside)
            result["images_preserved"] = image_fingerprints(edited[case["page"]-1]) == original_images
            result["drawings_preserved"] = drawings_fingerprint(edited[case["page"]-1]) == original_drawings
            result["new_text_mupdf"] = normalize(text) in normalize(edited[case["page"]-1].get_text())
        old_image, new_image = directory/"before.png", directory/"after.png"
        before_render = poppler_render(poppler, source, case["page"], old_image)
        after_render = poppler_render(poppler, output, case["page"], new_image)
        result["poppler"] = {"before":before_render,"after":after_render}
        if before_render["rendered"] and after_render["rendered"]:
            with Image.open(old_image) as before,Image.open(new_image) as after:
                result["poppler"]["diff"] = pixel_diff(before,after,mask)
                ImageChops.difference(before.convert("RGB"),after.convert("RGB")).save(directory/"difference.png")
        extracted = independent_text(pypdf_python, output, directory/"pypdf.json")
        result["pypdf"] = {"library":extracted.get("library"), "error":extracted.get("error"),
                           "new_text_present": normalize(text) in normalize(extracted.get("pages",[""]*case["page"])[case["page"]-1]) if len(extracted.get("pages",[]))>=case["page"] else False}
        if not report["font"]["preserved"]:
            result["classification"].append("font_substitution")
        invalid = (not result["new_text_mupdf"] or not result["untouched_text_signatures_retained"]
                   or not result["images_preserved"] or not result["drawings_preserved"]
                   or not result["written_geometry"]["passed"] or not result["security"]["preserved"]
                   or not result["pypdf"]["new_text_present"]
                   or any(d.get("outside_gt8_pixels",1)>0 for d in comparisons)
                   or result["poppler"].get("diff",{}).get("outside_gt8_pixels",1)>0)
        result["classification"].append("corruption_or_visual_change" if invalid else "structural_checks_passed")
    result["input_unchanged"] = sha(source) == result["input_sha256"]
    result["output_exists"] = output.exists()
    write_json(directory/"result.json",result)
    print(json.dumps({"id":case["id"],"status":result["status"],"categories":result["classification"],
                      "error":result.get("error"),"lines":result.get("report",{}).get("new_line_count")},ensure_ascii=False),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("command",choices=["inventory","run"])
    parser.add_argument("--engine",choices=["baseline","current"],default="baseline")
    parser.add_argument("--cases",type=Path,default=BASE/"cases.json")
    parser.add_argument("--run-name",default="baseline")
    parser.add_argument("--poppler",type=Path,default=DEFAULT_POPPLER)
    parser.add_argument("--pypdf-python",type=Path,default=DEFAULT_PYPDF)
    args=parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.engine=="baseline":
        sys.path.insert(0,str(BASE/"baseline"))
    from pdfeditor import backend,engine,fonts,layout
    if args.command=="inventory":
        inventory(sorted((BASE/"corpus").glob("*.pdf")),backend,BASE/"inventory")
        return
    cases=json.loads(args.cases.read_text(encoding="utf-8"))
    output_root=BASE/"runs"/args.run_name
    output_root.mkdir(parents=True,exist_ok=False)
    fingerprint={path.name:sha(path) for path in Path(backend.__file__).parent.glob("*.py")}
    write_json(output_root/"environment.json",{"python":sys.version,"pymupdf":pymupdf.VersionBind,
                                               "backend_path":backend.__file__,"engine_sha256":fingerprint})
    results=[]
    for case in cases:
        results.append(run_case(case,output_root,engine,backend,fonts,layout,args.poppler,args.pypdf_python))
        write_json(output_root/"results.json",results)


if __name__=="__main__":
    main()
