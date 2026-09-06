"""Independent TextWriter width diagnostic; does not modify production or originals."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pymupdf
from fontTools.ttLib import TTFont


ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent


def run_variant(data: bytes, text: str, size: float, label: str) -> dict:
    font = pymupdf.Font(fontbuffer=data)
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=110)
    writer = pymupdf.TextWriter(page.rect)
    writer.append((40, 50), text, font=font, fontsize=size)
    writer.write_text(page)
    binary = doc.tobytes(garbage=3, deflate=True)
    (OUT / f"font_writer_{label}.pdf").write_bytes(binary)
    check = pymupdf.open(stream=binary, filetype="pdf")
    trace = [c for s in check[0].get_texttrace() for c in s["chars"]]
    expected_x = 40.0
    rows = []
    for index, char in enumerate(text):
        if index >= len(trace):
            break
        glyph = trace[index]
        rows.append({"character": char, "planned_x": expected_x,
                     "actual_x": glyph[2][0], "origin_error": glyph[2][0] - expected_x,
                     "font_advance": font.text_length(char, fontsize=size),
                     "actual_next_origin_delta": trace[index + 1][2][0] - glyph[2][0]
                     if index + 1 < len(trace) else None,
                     "trace_bbox_width": glyph[3][2] - glyph[3][0]})
        expected_x += font.text_length(char, fontsize=size)
    fonts = []
    for entry in check[0].get_fonts():
        top = check.xref_object(entry[0])
        kind, descendant = check.xref_get_key(entry[0], "DescendantFonts")
        xref = int(descendant.strip("[] ").split()[0])
        fonts.append({"top": top, "descendant": check.xref_object(xref)})
    check[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).save(OUT / f"font_writer_{label}.png")
    return {"font_flags": font.flags, "planned_width": font.text_length(text, fontsize=size),
            "trace_count": len(trace), "text_length": len(text),
            "max_origin_error_pt": max(abs(r["origin_error"]) for r in rows),
            "glyphs": rows, "output_font_objects": fonts}


def main() -> None:
    results = {}
    for source, xref in (("word_kyoto_questions", 19), ("word_okinawa_procurement", 22)):
        document = pymupdf.open(ROOT / "corpus" / f"{source}.pdf")
        data = document.extract_font(xref)[3]
        report = json.loads((ROOT / "runs" / "baseline" / f"{source}--noop" / "result.json").read_text(encoding="utf8"))["report"]
        text = report["lines"][0]["text"][:30]
        tt = TTFont(io.BytesIO(data))
        fixed_pitch_before = tt["post"].isFixedPitch
        tt["post"].isFixedPitch = 0
        changed = io.BytesIO()
        tt.save(changed)
        results[source] = {"source_font_xref": xref, "source_post_isFixedPitch": fixed_pitch_before,
                           "font_program_only": run_variant(data, text, report["font_size"], source + "_original"),
                           "experimental_post_isFixedPitch_zero": run_variant(changed.getvalue(), text, report["font_size"], source + "_clear_fixed_pitch")}
        saved = pymupdf.open(ROOT / "runs" / "baseline" / f"{source}--noop" / "edited.pdf")
        font = pymupdf.Font(fontbuffer=data)
        expected = []
        for line in report["lines"]:
            x = line["x"]
            for char in line["text"]:
                expected.append((char, x, line["baseline"]))
                x += font.text_length(char, fontsize=report["font_size"]) + report["tracking"]
        # The baseline writer appends the edited text as its last content stream.
        actual = [char for span in saved[0].get_texttrace() for char in span["chars"]][-len(expected):]
        errors = [{"character": char, "planned_origin": [x, y], "actual_origin": list(glyph[2]),
                   "x_error_pt": glyph[2][0] - x, "y_error_pt": glyph[2][1] - y}
                  for (char, x, y), glyph in zip(expected, actual)]
        results[source]["baseline_saved_output"] = {
            "tracking": report["tracking"], "expected_count": len(expected), "actual_count": len(actual),
            "unicode_order_matches": [ord(row[0]) for row in expected] == [row[0] for row in actual],
            "max_x_error_pt": max(abs(row["x_error_pt"]) for row in errors),
            "max_y_error_pt": max(abs(row["y_error_pt"]) for row in errors),
            "glyphs": errors,
        }
    (OUT / "font_writer_probe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps({key: {"source_post_isFixedPitch": val["source_post_isFixedPitch"],
                           "original_error": val["font_program_only"]["max_origin_error_pt"],
                           "experimental_error": val["experimental_post_isFixedPitch_zero"]["max_origin_error_pt"]}
                      for key, val in results.items()}))


if __name__ == "__main__":
    main()
