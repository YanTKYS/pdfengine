"""Fixed, reviewed selections for this corpus; not engine exception rules."""
import json
from pathlib import Path

BASE=Path(__file__).resolve().parent
# source, short-box, multi-box. None means the document has no editable
# multiline prose; its vector/raster diagrams are outside the OCR-free scope.
SELECTIONS=[
    ("word_osaka_fire_notice","p1-b8","p1-b11"),
    ("word_osaka_guideline","p1-b3","p1-b7"),
    ("word_wakayama_guidelines","p1-b4","p1-b11"),
    ("word_takeo_notice","p1-b5","p2-b3"),
    ("lo_migration_ja","p1-b10","p1-b4"),
    ("lo_newfeatures_ja","p1-b10","p1-b3"),
    ("lo_enterprise_en","p1-b9","p1-b5"),
    ("print_canvia","p1-b3","p1-b7"),
    ("print_ubiquiti","p1-b3",None),
    ("print_fcc_ms","p1-b9","p1-b2"),
    ("print_fcc_chrome","p1-b10","p1-b6"),
    ("word_kyoto_questions","p1-b3","p1-b4"),
    ("word_niigata_hearing","p1-b3","p1-b1"),
    ("word_okinawa_procurement","p1-b5","p1-b4"),
    ("word_osaka_symposium","p1-b9","p1-b13"),
]


def main():
    cases=[]
    for source,short,multi in SELECTIONS:
        english=source.startswith("print_") or source=="lo_enterprise_en"
        long_text=("Please review the application documents by October 15, 2026. Confirm the Wi-2026 identifier and submit the additional information."
                   if english else "申請書類の提出期限は2026年10月15日です。ID: Wi-2026を確認し、追加資料と一緒に提出してください。")
        compact="Approved." if english else "確認しました。"
        for operation,target,text in [("noop",multi or short,None),("expand",short,long_text),("shrink",multi,compact)]:
            if target is None:
                continue
            cases.append({"id":f"{source}--{operation}","source":source,"page":int(target.split("-")[0][1:]),
                          "box":target,"operation":operation,"text":text,
                          "width_mode":"default", "note":"Fixed selection before edit; flawed inference is retained for evaluation, not silently corrected."})
    # Controlled width sensitivity: this is an explicit experiment, not a
    # claim that the supplied width can be inferred from the source PDF.
    for source,box,text,width in [
        ("word_takeo_notice","p1-b5","報道機関のご担当者各位",220),
        ("word_niigata_hearing","p1-b3","１ 素案の概要と説明会について",230),
        ("lo_newfeatures_ja","p1-b10","新たに追加された関数と設定項目",280),
        ("print_canvia","p1-b3","Login and account password",280),
    ]:
        for mode in ["default","explicit"]:
            case={"id":f"{source}--width-{mode}","source":source,"page":1,"box":box,
                  "operation":"width_sensitivity","text":text,"width_mode":mode,
                  "note":"Same short line and replacement; explicit width is an analyst-supplied sensitivity parameter, not ground-truth recovered width."}
            if mode=="explicit":
                case["width"]=width
            cases.append(case)
    # Same-position fill+stroke text is deliberately tested as an overlap case.
    cases.append({"id":"word_osaka_fire_notice--overlap","source":"word_osaka_fire_notice","page":1,
                  "box":"p1-b9","operation":"overlap","text":"林野火災への対応について","width_mode":"default"})
    (BASE/"cases.json").write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"{len(cases)} fixed cases")


if __name__=="__main__":
    main()
