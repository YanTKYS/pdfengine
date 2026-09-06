"""Combine immutable measurements with explicit human semantic judgments."""
import csv
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

from evaluations.realpdf.evaluate import BASE, write_json


def main():
    rows, summaries = [], {}
    for run in ["baseline", "guarded", "supplemental"]:
        root=BASE/"runs"/run
        results=json.loads((root/"results.json").read_text(encoding="utf-8"))
        audits={a["id"]:a for a in json.loads((root/"postsave_audit.json").read_text(encoding="utf-8"))}
        for r in results:
            case=r["case"]
            labels=[]
            if r["status"]=="rejected":
                labels=["安全に拒否"]
                observation=r["error"]
            else:
                a=audits[case["id"]]
                if not a["written_geometry"]["passed"]:
                    labels.append("編集結果破損")
                if not a["security"]["preserved"]:
                    labels.append("保存設定破損")
                if not r["report"]["font"]["preserved"]:
                    labels.append("フォント代替")
                if case["source"]=="word_kyoto_questions":
                    labels += ["推定範囲不一致", "レイアウト不自然"]
                    observation="質問の先頭行が選択Box外に残る。選択Boxの文字置換は検証できても質問段落全体の自然な編集ではない。"
                elif case["source"]=="word_okinawa_procurement":
                    labels.append("推定範囲不一致")
                    observation="Boxが公告本文と日付を結合する。Box全体置換では日付まで消えるため自動段落編集成功と数えない。"
                else:
                    observation="明示幅230pt・フォント代替による見出し1行の局所編集。原稿font保持や自動幅推定の成功を意味しない。"
                if a["written_geometry"]["passed"] and a["security"]["preserved"]:
                    labels.insert(0,"選択Boxの描画・除去・保存検査通過")
                    if case["source"]=="word_niigata_hearing":
                        labels.insert(0,"条件付き成功")
            rows.append({"run":run,"case_id":case["id"],"source":case["source"],
                         "page":case["page"],"box":case["box"],"operation":case["operation"],
                         "status":r["status"],"classification":"; ".join(labels),
                         "rejection_category":r.get("rejection_category",""),"observation":observation,
                         "source_unchanged":r["input_unchanged"],
                         "evidence":str((root/case["id"]/"result.json").relative_to(BASE))})
        summaries[run]={"cases":len(results),"written":sum(r["status"]=="written" for r in results),
                        "rejection_counts":dict(Counter(r["rejection_category"] for r in results if r["status"]=="rejected")),
                        "all_inputs_unchanged":all(r["input_unchanged"] for r in results)}
    with (BASE/"case_outcomes.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)
    write_json(BASE/"final_assessment.json",{"measurement_runs":summaries,
        "human_review_basis":"Poppler saved-page images for all written guarded and supplemental cases, baseline two corrupted noops and remaining three saved cases; inference review files. Labels are intersecting, not exclusive success-rate bins.",
        "case_outcomes":rows})

    # Contact sheet from independently rendered Poppler PNGs, for final visual QA.
    previews=[]
    root=BASE/"runs"/"supplemental"
    for r in json.loads((root/"results.json").read_text(encoding="utf-8")):
        if r["status"]!="written":continue
        with Image.open(root/r["case"]["id"]/"after.png") as im:
            mask=r["visual_mask_pt"]
            crop=im.crop((int(mask[0]*96/72),int(mask[1]*96/72)-6,
                          int(mask[2]*96/72)+1,int(mask[3]*96/72)+6)).convert("RGB")
        tile=Image.new("RGB",(680,190),"#eeeeee")
        ImageDraw.Draw(tile).text((8,6),r["case"]["id"],fill="black")
        tile.paste(crop,(8,27));previews.append(tile)
    sheet=Image.new("RGB",(1360,570),"white")
    for i,tile in enumerate(previews):sheet.paste(tile,((i%2)*680,(i//2)*190))
    sheet.save(BASE/"review_assets"/"supplemental_poppler_contact.png")
    print(json.dumps(summaries,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
