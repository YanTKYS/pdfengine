"""External-PDF comparison for new-font composition; inputs are reviewable data.

This is deliberately independent of the old Stage numbering. A successful
operation must pass saved glyph checks, independent extraction and Poppler.
No source PDF/font program or full extraction is included in public summaries.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys

from pypdf import PdfReader
import pymupdf

from pdfeditor.composition import compose_selected
from pdfeditor.content_stream import ContentPage, patch_streams
from pdfeditor.pdf_save import program_pdf_bytes
from pdfeditor.selection import resolve_selection, source_sha
from pdfeditor.shaped_font import ShapedFont
from evaluations.backend.followup import independent_edit_audit, render_audit, require_render_audit
from evaluations.realpdf.evaluate import DEFAULT_PYPDF, independent_text, write_json, drawings_fingerprint, image_fingerprints

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent

# Explicitly reviewed regions, not inferred widths. Bottoms stop before the
# next paragraph/table/image. Each replacement is an evaluator input, never an
# engine branch for a specific PDF. Font changes are always reported.
CASES = [
    dict(id="word_takeo_notice", font="YuGothR.ttc", face=0, width=358, bottom=218, change=("協定", "協議")),
    dict(id="lo_migration_ja", font="msmincho.ttc", face=1, width=492, bottom=369, change=("病院", "施設")),
    dict(id="print_canvia", font="arialbd.ttf", face=0, width=260, bottom=167, change=("Login", "Admin")),
    dict(id="word_niigata_hearing", font="msmincho.ttc", face=0, width=230, bottom=162, change=("概要", "要約")),
    dict(id="lo_enterprise_en", font="arial.ttf", face=0, width=190, bottom=195, change=("ISO", "IEC")),
    dict(id="print_fcc_ms", font="arial.ttf", face=0, width=189, bottom=118, change=("liste", "laste")),
    dict(id="print_fcc_chrome", font="NotoSansJP-VF.ttf", face=0, axes={"wght":400}, width=500, bottom=410,
         text="Meural Canvasの設定を確認してください。新しい作品を登録する前に、接続先とアカウント情報を確認します。提出期限は2026年9月30日です。※ABC-123、英数字と記号を含む説明を追加できます。設定が完了したら、画面に表示される作品と明るさを確認してください。"),
    dict(id="lo_newfeatures_ja", font="msmincho.ttc", face=1, width=532, bottom=170,
         text="LibreOfficeの新しい機能を紹介します。提出期限は2026年9月30日です。申請書類と添付資料を確認し、担当窓口へ提出してください。※英数字ABC-123と新しい漢字を含む文章を入力できます。変更後はプレビューで改行位置と文字の表示を確認してください。"),
    dict(id="word_kyoto_questions", font="msgothic.ttc", face=0, width=420, bottom=231,
         text="質問 新規認定店には、案内資料とポスターを送付します。"),
    dict(id="word_osaka_fire_notice", font="msgothic.ttc", face=0, width=480, bottom=315,
         text="申請書類は9月30日までに提出してください。受付後に内容を確認します。"),
    dict(id="print_ubiquiti", font="NotoSansJP-VF.ttf", face=0, axes={"wght":400}, width=200, bottom=70,
         text="更新: 2026年9月7日。設置前に対応機器と接続手順を確認してください。※英数字ABC-123と記号を含む説明を追加しました。"),
    dict(id="print_ubiquiti", trial="within_clip", font="NotoSansJP-VF.ttf", face=0,
         axes={"wght":400}, width=200, bottom=70, text="更新: 2026/09/08"),
    dict(id="word_osaka_symposium", font="msmincho.ttc", face=0, width=100, bottom=70, text="受付"),
]


def font_mapping_audit(path, report):
    reader = PdfReader(path)
    if reader.is_encrypted:
        reader.decrypt("")
    root = reader.pages[report["selection"]["page"]-1]["/Resources"]["/Font"][report["font"]["resource"]]
    descendant = root["/DescendantFonts"][0].get_object()
    mapping = descendant["/CIDToGIDMap"].get_data()
    widths = descendant["/W"]
    assert int(widths[0]) == 1
    for glyph in report["glyph_plan"]:
        cid = glyph["cid"]
        assert int.from_bytes(mapping[cid*2:cid*2+2], "big") == glyph["glyph_id"]
        assert abs(float(widths[1][cid-1])-glyph["pdf_nominal_width"]) < 1e-5
    return {"passed":True, "glyph_occurrences_checked":len(report["glyph_plan"]), "width_contract":"nominal hmtx in /W; contextual advances in positioned glyph origins"}


def run_case(spec, selection_case, directory, font_dir, font_cache):
    source = ROOT / selection_case["source"]
    manifest = deepcopy(selection_case["selection"])
    manifest["explicitly_supplied_width"] = spec["width"]
    directory.mkdir(parents=True)
    item = {"case":spec["id"], "trial":spec.get("trial", "primary"), "spec":spec,
            "source_sha256":source_sha(source), "status":"pending"}
    auditing = False
    output = directory/"edited.pdf"
    try:
        resolved = resolve_selection(source, manifest)
        original = "".join(g.text for g in sorted(resolved.glyphs,key=lambda g:g.source_order))
        replacement = spec.get("text")
        if replacement is None:
            old, new = spec["change"]
            if old not in original:
                raise ValueError("declared source phrase was not found")
            replacement = original.replace(old,new,1)
        item.update(original=original,replacement=replacement,manifest=manifest)
        # Run the source-resource no-op through Poppler before allowing any
        # new-font edit. This also supports common-paint ranges whose source
        # font changes mid-line, which the older Stage 1 CLI refuses.
        content=ContentPage(source,manifest["page"])
        try:
            selected=set(manifest["glyph_ids"])
            events=content.selected_events(selected)
            replay=patch_streams(content,events,selected,remove=False)[-content.page.xref]
        finally:
            content.close()
        replayed=directory/"source-replayed.pdf"
        replayed.write_bytes(program_pdf_bytes(source,manifest["page"]-1,replay))
        replay_audit=render_audit(source,replayed,manifest["page"],directory/"source-replay",resolved.bbox.tuple())
        item["source_replay_audit"]=replay_audit
        require_render_audit(replay_audit)
        if replay_audit["poppler_diff"]["all_changed_pixels"] != 0:
            raise ValueError("source no-op changed Poppler pixels; composition was not attempted")
        before=independent_text(DEFAULT_PYPDF,source,directory/"source-text.json")
        replayed_text=independent_text(DEFAULT_PYPDF,replayed,directory/"replay-text.json")
        if before.get("error") or replayed_text.get("error") or before.get("pages") != replayed_text.get("pages"):
            raise ValueError("source no-op changed independent extraction; composition was not attempted")
        key=(spec["font"],spec["face"],tuple(sorted(spec.get("axes",{}).items())))
        if key not in font_cache:
            font_cache[key]=ShapedFont(font_dir/spec["font"],font_index=spec["face"],variations=spec.get("axes"))
        font=font_cache[key]
        size=resolved.glyphs[0].style.size
        max_height=spec["bottom"]-(resolved.lines[0].baseline-font.ascender*size)
        report=compose_selected(source,output,manifest,replacement,font_source=font,
                                max_height=max_height,removal_output=directory/"removed.pdf")
        item["report"]=report
        auditing=True
        bounds=tuple(report["audit_bbox"][k] for k in ("x0","y0","x1","y1"))
        item["render_audit"]=render_audit(source,output,manifest["page"],directory,bounds)
        require_render_audit(item["render_audit"])
        after=independent_text(DEFAULT_PYPDF,output,directory/"edited-text.json")
        removed=independent_text(DEFAULT_PYPDF,directory/"removed.pdf",directory/"removed-text.json")
        item["independent_edit"]=independent_edit_audit(before,after,manifest["page"],original,replacement)
        # A repeated header on an unedited page must remain. Verify removal
        # as exactly one target-page replacement with an empty string, with
        # every other page unchanged, not global absence across the document.
        item["independent_removal"]=independent_edit_audit(before,removed,manifest["page"],original,"")
        item["font_mapping"]=font_mapping_audit(output,report)
        with pymupdf.open(source) as a,pymupdf.open(output) as b:
            item["drawings_unchanged"]=all(drawings_fingerprint(a[i])==drawings_fingerprint(b[i]) for i in range(len(a)))
            item["images_unchanged"]=all(image_fingerprints(a[i])==image_fingerprints(b[i]) for i in range(len(a)))
        if not item["drawings_unchanged"] or not item["images_unchanged"]:
            raise ValueError("nontext objects changed")
        if source_sha(source)!=item["source_sha256"]:
            raise ValueError("source PDF changed")
        item.update(status="passed",classification="font_substitution_success",output_sha256=source_sha(output))
    except Exception as exc:
        item.update(status="failed_evaluation" if auditing else "rejected",
                    classification="evaluation_failure" if auditing else "safe_refusal",
                    error_type=type(exc).__name__,error=str(exc),output_created=output.exists())
    write_json(directory/"result.json",item)
    return item


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--run-name",required=True)
    parser.add_argument("--font-dir",type=Path,default=Path("C:/Windows/Fonts"))
    args=parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    root=BASE/"runs"/args.run_name
    root.mkdir(parents=True,exist_ok=False)
    selections={c["id"]:c for c in json.loads((ROOT/"evaluations/backend/selections.json").read_text(encoding="utf-8"))}
    previous={r["case"]:r for r in json.loads((ROOT/"evaluations/backend/runs/stages_audit_v5/results.json").read_text(encoding="utf-8"))}
    write_json(root/"environment.json",{"engine_sha256":{p.name:source_sha(p) for p in (ROOT/"pdfeditor").glob("*.py")},
        "runner_sha256":source_sha(Path(__file__)),"font_dir":str(args.font_dir),"pymupdf":pymupdf.VersionBind})
    results=[];cache={}
    for spec in CASES:
        trial_name=spec["id"]+("_"+spec["trial"] if "trial" in spec else "")
        item=run_case(spec,selections[spec["id"]],root/trial_name,args.font_dir,cache)
        results.append(item)
        write_json(root/"results.json",results)
        print(json.dumps({"case":spec["id"],"trial":item["trial"],"status":item["status"],"error":item.get("error"),
              "lines":[item.get("report",{}).get("old_line_count"),item.get("report",{}).get("new_line_count")]},ensure_ascii=False),flush=True)
    summary=[]
    for item in results:
        report=item.get("report",{})
        summary.append({"case":item["case"],"trial":item["trial"],"status":item["status"],"classification":item["classification"],
            "source_sha256":item["source_sha256"],"output_sha256":item.get("output_sha256"),
            "previous_stage1":previous[item["case"]]["stage1_status"],"previous_stage2":previous[item["case"]]["stages"]["2"]["status"],
            "old_characters":len(item.get("original","")),"new_characters":len(item.get("replacement","")),
            "old_lines":report.get("old_line_count"),"new_lines":report.get("new_line_count"),
            "font_substituted":report.get("font_substituted"),"error":item.get("error"),
            "font":report.get("font"),
            "source_replay_poppler_changed_pixels":item.get("source_replay_audit",{}).get("poppler_diff",{}).get("all_changed_pixels"),
            "new_unicode_codepoints":sorted({f"U+{ord(c):04X}" for c in set(item.get("replacement",""))-set(item.get("original",""))}),
            "poppler_outside_gt8_pixels":item.get("render_audit",{}).get("poppler_diff_with_1pt_margin",{}).get("outside_gt8_pixels"),
            "poppler_outside_changed_pixels":item.get("render_audit",{}).get("poppler_diff_with_1pt_margin",{}).get("outside_changed_pixels"),
            "independent_edit_passed":item.get("independent_edit",{}).get("passed"),
            "independent_removal_passed":item.get("independent_removal",{}).get("passed"),
            "drawings_unchanged":item.get("drawings_unchanged"),"images_unchanged":item.get("images_unchanged")})
    write_json(root/"summary.json",{"counts":dict(Counter(i["status"] for i in results)),"cases":summary})
    print(json.dumps(dict(Counter(i["status"] for i in results))))


if __name__=="__main__":main()
