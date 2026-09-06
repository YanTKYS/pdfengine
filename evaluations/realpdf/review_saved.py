"""Reassess saved evidence without rewriting PDFs or baseline case results."""
import argparse
import json

import pymupdf

from evaluations.realpdf.evaluate import BASE, normalize, security_state, write_json, written_geometry
from pdfeditor.fonts import resolve_font


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_name")
    args = parser.parse_args()
    root = BASE / "runs" / args.run_name
    cases = json.loads((root / "results.json").read_text(encoding="utf-8"))
    audits = []
    for result in cases:
        if result["status"] != "written":
            continue
        case, report = result["case"], result["report"]
        with pymupdf.open(BASE / "corpus" / f"{case['source']}.pdf") as original, pymupdf.open(root / case["id"] / "edited.pdf") as edited:
            resolved = resolve_font(original, case["page"]-1, report["font"]["original"], report["after"])
            geometry = written_geometry(report, edited[case["page"]-1], resolved.font)
            extracted = json.loads((root/case["id"]/"pypdf.json").read_text(encoding="utf-8"))
            old = normalize(report["before"])
            removal = {"noop":normalize(report["before"])==normalize(report["after"]),
                       "old_text_is_substring_of_replacement":old in normalize(report["after"]),
                       "old_box_text_present_mupdf":old in normalize(edited[case["page"]-1].get_text()),
                       "old_box_text_present_pypdf":old in normalize(extracted["pages"][case["page"]-1]),
                       "predraw_counter_matched":result["removal_audit"]["exact_signature_match"]}
            security = {"before":security_state(original), "after":security_state(edited),
                        "preserved":security_state(original)==security_state(edited)}
        audits.append({"id":case["id"], "written_geometry":geometry, "security":security,
                       "text_removal":removal})
    write_json(root / "postsave_audit.json", audits)
    print(json.dumps(audits, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
