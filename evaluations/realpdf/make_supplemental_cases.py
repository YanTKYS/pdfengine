"""Targeted follow-ups to baseline findings; kept outside the fixed 53 cases."""
from evaluations.realpdf.evaluate import BASE, write_json


def main():
    cases = []
    for source in ["word_kyoto_questions", "word_okinawa_procurement"]:
        for probe, text in [("kanji", "麒麟鬱"), ("ascii", "ID: Wi-2026 A9"),
                            ("symbols_mixed", "確認 Wi-2026 ※①€")]:
            cases.append({"id":f"{source}--glyph-{probe}", "source":source,
                          "page":1, "box":"p1-b4", "operation":"glyph_replacement",
                          "text":text, "width_mode":"default",
                          "note":"Follow-up after baseline: exercise actual font substitution rendering. The known incorrect semantic scope of this box is retained and is not counted as automatic editing success."})
    for operation,text in [("noop",None),("shrink","確認しました。")]:
        cases.append({"id":f"word_wakayama_guidelines--merged-list-{operation}",
                      "source":"word_wakayama_guidelines", "page":1,"box":"p1-b15",
                      "operation":operation, "text":text,"width_mode":"default",
                      "note":"Follow-up to human inference review: same-style box merges list (3) and the first line of (4), with continuation outside this box. No engine guard is bypassed."})
    write_json(BASE/"supplemental_cases.json", cases)


if __name__=="__main__":
    main()
