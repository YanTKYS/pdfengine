"""Compare writers using unchanged real embedded font programs on scratch pages.

This is a font/writer isolation experiment, not an end-to-end editing success.
Source documents are read only. The gold scratch stream explicitly reuses the
original resource and character codes (only Identity-H/identity-GID cases here).
Run: .venv/Scripts/python.exe evaluations/backend/experiments/options_writer.py
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pymupdf
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).with_name("options_writer_results")
POPPLER = Path.home()/".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe"
SOURCES = [("word_kyoto_questions", 19), ("word_okinawa_procurement", 22)]


def image_difference(a, b):
    if a.size != b.size:
        return {"same_size": False}
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB"))
    return {"same_size": True,
            "changed_pixels": sum(max(p) > 0 for p in diff.getdata()),
            "changed_pixels_over_8": sum(max(p) > 8 for p in diff.getdata())}


def descendant(document, font_xref):
    kind, value = document.xref_get_key(font_xref, "DescendantFonts")
    return int(re.fullmatch(r"\[\s*(\d+)\s+0\s+R\s*\]", value)[1])


def variant(source, original_xref, text, size, method):
    doc = pymupdf.open(ROOT/"evaluations/realpdf/corpus"/(source+".pdf"))
    font = pymupdf.Font(fontbuffer=doc.extract_font(original_xref)[3])
    page = doc.new_page(width=600, height=110)
    doc.xref_set_key(page.xref, "Resources", f"<< /Font << /Src {original_xref} 0 R >> >>")
    if method == "original_resource_codes":
        # Both tested originals have Identity-H and identity CID-to-GID mapping.
        glyph_codes = "".join(f"{font.has_glyph(ord(c), fallback=False):04X}" for c in text)
        xref = doc.get_new_xref()
        doc.update_object(xref, "<< >>")
        doc.update_stream(xref, f"q BT /Src {size:.8f} Tf 1 0 0 1 40 60 Tm <{glyph_codes}> Tj ET Q".encode())
        page.set_contents(xref)
    elif method == "shape_original_resource":
        page.insert_text((40, 50), text, fontname="/Src", fontsize=size)
    elif method == "shape_new_embedding":
        page.insert_font(fontname="Probe", fontbuffer=font.buffer)
        page.insert_text((40, 50), text, fontname="Probe", fontsize=size)
    else:
        writer = pymupdf.TextWriter(page.rect)
        writer.append((40, 50), text, font=font, fontsize=size)
        writer.write_text(page)
        if method == "textwriter_explicit_W":
            entries = [entry for entry in page.get_fonts() if entry[4] != "Src"]
            assert len(entries) == 1
            xref = descendant(doc, entries[0][0])
            assert doc.xref_get_key(entries[0][0], "Encoding")[1] == "/Identity-H"
            assert doc.xref_get_key(xref, "CIDToGIDMap")[1] in {"/Identity", "null"}
            widths = {font.has_glyph(ord(c), fallback=False): font.text_length(c, fontsize=1000) for c in text}
            doc.xref_set_key(xref, "W", "["+" ".join(f"{gid} [{width:.8f}]" for gid, width in sorted(widths.items()))+"]")
    # Keep only the scratch page. Original corpus bytes are never rewritten.
    doc.select([doc.page_count-1])
    stem = source+"--"+method
    path = OUT/(stem+".pdf")
    doc.save(path, garbage=3, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    doc.close()
    with pymupdf.open(path) as check:
        trace = [c for s in check[0].get_texttrace() for c in s["chars"]]
        x = 40.0
        rows = []
        for char, glyph in zip(text, trace):
            rows.append({"character":char, "expected_gid":font.has_glyph(ord(char), fallback=False),
                         "actual_unicode":glyph[0], "actual_gid":glyph[1], "planned_x":x,
                         "actual_origin":list(glyph[2]), "font_advance":font.text_length(char, fontsize=size),
                         "actual_bbox_width":glyph[3][2]-glyph[3][0]})
            x += font.text_length(char, fontsize=size)
        objects = []
        for entry in check[0].get_fonts():
            objects.append({"entry":entry, "font":check.xref_object(entry[0])})
            value = check.xref_get_key(entry[0], "DescendantFonts")[1]
            match = re.fullmatch(r"\[\s*(\d+)\s+0\s+R\s*\]", value)
            if match:
                objects[-1]["descendant"] = check.xref_object(int(match[1]))
        check[0].get_pixmap(matrix=pymupdf.Matrix(2,2), alpha=False).save(OUT/(stem+"--mupdf.png"))
    subprocess.run([str(POPPLER), "-f", "1", "-l", "1", "-singlefile", "-r", "144", "-png", str(path), str(OUT/(stem+"--poppler"))], check=True, capture_output=True)
    return {"method":method, "glyph_count":len(trace), "expected_count":len(text),
            "unicode_equal":len(trace)==len(text) and all(ord(c)==g[0] for c,g in zip(text,trace)),
            "gids_equal":len(trace)==len(text) and all(r["actual_gid"]==r["expected_gid"] for r in rows),
            "max_origin_error_pt":max(abs(r["actual_origin"][0]-r["planned_x"]) for r in rows),
            "glyphs":rows, "font_objects":objects, "output":str(path.relative_to(ROOT))}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    for source, xref in SOURCES:
        old = json.loads((ROOT/"evaluations/realpdf/runs/baseline"/(source+"--noop")/"result.json").read_text(encoding="utf-8"))["report"]
        text, size = old["lines"][0]["text"][:30], old["font_size"]
        for method in ("original_resource_codes", "textwriter", "shape_original_resource", "shape_new_embedding", "textwriter_explicit_W"):
            try:
                result = variant(source, xref, text, size, method)
                for renderer in ("mupdf", "poppler"):
                    gold = Image.open(OUT/(source+"--original_resource_codes--"+renderer+".png"))
                    actual = Image.open(OUT/(source+"--"+method+"--"+renderer+".png"))
                    result[renderer+"_vs_original_resource"] = image_difference(gold, actual)
            except Exception as exc:
                result = {"method":method, "error":repr(exc)}
            result.update({"source":source, "source_font_xref":xref, "text":text, "size":size})
            results.append(result)
            print(json.dumps({k:v for k,v in result.items() if k not in {"glyphs","font_objects","text"}}, ensure_ascii=False))
    (OUT/"results.json").write_text(json.dumps({"pymupdf_version":pymupdf.VersionBind, "results":results}, ensure_ascii=False, indent=2),encoding="utf-8")


if __name__ == "__main__":
    main()
