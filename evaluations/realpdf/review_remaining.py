"""Record the scoped human visual review of three remaining source pages."""
from pathlib import Path
import hashlib
import json

import pymupdf

ROOT = Path(__file__).resolve().parent


def observation(source, page, boxes, status, levels, evidence, conclusion):
    return {"document_id": source, "page": page, "box_ids": boxes, "status": status,
            "levels": levels, "evidence": evidence, "conclusion": conclusion}


observations = [
    observation("word_takeo_notice", 2, ["p2-b3"], "partly_correct", ["glyph", "run", "line", "paragraph", "textbox"], [
        "写真上の淡い角丸背景内にある本文だけを選択。215 Glyph、8 Line、各行1 Run。欠落Unicode/gid=-1は0。目視した本文の文字列と8行の区切りが一致し、別の見出し・日時・写真はTextBoxに含まれない。",
        "内容は、連携協定（2行）、前年度からのラボ活動（3行）、今年度の公開講座（3行）の3まとまり。推定Paragraphは2で、最後の『今年度の最後の武雄ラボは』から始まる区切りが前の活動説明と結合する。",
        "段落の3まとまりという判定は内容と原本改行からのレビュー判断。全行x=27.552pt、行間17.28ptであり、作者が保存した段落タグによる確定ではない。",
        "available_width=null、width_source=observed-line-extent。背景の角丸矩形から利用可能幅を取得しているわけではない。画像と透明な背景が近接・重複する装飾ページで、全ページの158 clipping pathsが現在の編集guardを拒否させる。"
    ], "Glyph/Run/Lineと本文全体の範囲は正しい。Paragraphの作者意図は未確定だが内容区切りを1つ失う。編集成功と分類しない。"),
    observation("word_takeo_notice", 2, ["p2-b7"], "overmerge", ["line", "paragraph", "textbox"], [
        "原本は左に大きな『1月25日 木』、右に時刻と会場、その下に別の緑背景ラベル『第1部』を配置する。",
        "p2-b7は時刻、日付、会場、第1部を1 TextBoxに統合。推定Lineは『14:30～16:30』『1月25日木武雄市役所1階ホール』『第１部』の3本になり、別ブロックの読み順が崩れる。",
        "中央・右の会場と左の大きな日付は同じbaseline付近でも同一文章行ではない。8 Runに書式差は残るが、編集範囲として一括置換してよい根拠にはならない。"
    ], "異なる文字サイズと背景ラベルが近接する配置でLine/TextBoxの意味的誤結合。"),
    observation("word_takeo_notice", 2, ["p2-b13"], "overmerge", ["paragraph", "textbox"], [
        "顔写真の右のプロフィールは見出し『■講師プロフィール』と本文7行。8 Lineの行抽出は原本と一致する。",
        "1 Paragraphに見出しと本文を連結している。異なるフォントは各LineのRunに保持されており、文字取得の失敗ではない。写真の画素はGlyphとして取得していない。"
    ], "写真隣接の文字取得と行分離は可能だが見出し・本文の意味分離は不足。"),
    observation("print_fcc_chrome", 1, ["p1-b6"], "correct", ["glyph", "run", "line", "paragraph", "textbox"], [
        "原本の導入文は2行で、1行目は『...and control』、2行目は『your Canvas.』。83 Glyph、2 Line、2 Run、1 Paragraphがこれと一致する。",
        "見出し『Meural Canvas』や離れた目次を含まない。NunitoSans-SemiBoldの単一書式として抽出される。必要文字のglyph取得と文中スペースの連結に目視上の欠落は見つからない。"
    ], "この選択範囲では基礎推定は正しい。31 clipping pathsのあるページで、編集拒否と推定成否は分ける。"),
    observation("print_fcc_chrome", 1, ["p1-b10"], "correct", ["glyph", "run", "line", "paragraph", "textbox"], [
        "見出し『Getting started』15 Glyphを単独の1 Run/Line/Paragraph/TextBoxとして取得。上の目次や下のStep 1を含まない。",
        "観測幅は157.193ptでavailable_width=null。右側の空白は利用可能なテキスト枠の証拠として扱われず、幅不確実の警告が出る。"
    ], "見出し範囲は正しい。短い観測文字列の幅と利用可能幅の区別は未解決。"),
    observation("print_fcc_chrome", 1, ["p1-b11", "p1-b12", "p1-b13"], "occlusion_and_icon_overmerge", ["glyph", "run", "line", "textbox"], [
        "原本の下部は登録ポップアップがStep 1本文の上を覆っている。作者の原本からこの重なりが存在する。",
        "p1-b11は覆われた本文3行も取得し、通常の可視Glyphとして扱う。テキスト抽出ができることと画面上の可視性は一致しない。",
        "ポップアップの閉じる×アイコンはicomoonのU+E95Dとして取得され、本文行末『...remove the Meural』に連結される。見かけ上近い座標を同じLineに統合した誤認識。",
        "登録文の見出しp1-b12と説明p1-b13は別Boxだが、p1-b11の背面本文と座標領域が重なる。単純な文字bbox・visibleフラグでは前後のレイヤー関係を表現できない。"
    ], "occlusion/paint順序を扱うbackend側の情報が必要。GlyphのUnicode取得だけでは安全な選択対象を決められない。"),
    observation("print_fcc_chrome", 1, ["p1-b14"], "correct_with_width_assumption", ["glyph", "run", "line", "textbox"], [
        "ポップアップ内のEmailプレースホルダーを1行で取得。目視で入力欄の矩形border内に存在する。",
        "このBoxだけavailable_width=164.654648pt、width_source=enclosing-rectangle-with-symmetric-inset。観測文字幅30.016ptより広い幅を認識する実PDF例。左右の対称余白は推定であり、入力可能欄の構造情報を取得したものではない。"
    ], "矩形からの幅推定が実PDFでも働く例。ただしこの範囲は編集実行を追加評価していない。"),
    observation("word_niigata_hearing", 1, ["p1-b1"], "overmerge", ["paragraph", "textbox"], [
        "原本の冒頭は太い見出し1行、明朝体本文2行、日付1行。107 Glyph/4 Run/4 Lineで文字と行の取得は一致する。",
        "推定TextBoxはこの3要素を一括選択し、2 Paragraphのうち最初のParagraphに見出し＋本文を連結する。MS-Gothic/MS-Minchoの書式差はRunに保持される。"
    ], "Glyph/Run/Lineは正しく、Paragraph/TextBox範囲は過大。現在のmixed style guardが保存前に拒否する。"),
    observation("word_niigata_hearing", 1, ["p1-b3"], "correct", ["glyph", "run", "line", "paragraph", "textbox"], [
        "小見出し『１ 素案の概要』を単独の1 Run/Line/Paragraph/TextBoxとして取得。下の説明行p1-b4と混ざらない。Glyph数8には末尾の原本スペースを含む。",
        "観測bbox幅71.219429pt、available_width=null。原本には右方の大きな空白があるが、そこを利用可能幅としては推定しない。",
        "ページ1には抽出上のdrawing/image/clipがなく、本文と直下の説明行の衝突が拡張時の主な周辺制約になる。"
    ], "短い見出しの認識は正しい。幅は観測文字列に限定されるため、明示幅との比較が有効な対象。"),
    observation("word_niigata_hearing", 1, ["p1-b5"], "overmerge", ["paragraph", "textbox"], [
        "原本の独立した節『２ 説明会』と『３ 公聴会』が同じBoxに入り、後者の最初の小見出し『(1) 公聴会の日時』でBoxが終わる。日時の値は次のp1-b6へ分かれる。",
        "推定の第3 Paragraphは『(2)説明会の開催場所』、会場の値、『３ 公聴会』を連結する。行自体は7本とも原本と一致する。",
        "全行同じMS-Mincho書式のため、mixed styleだけでは意味的範囲の誤結合を検出できない。"
    ], "箇条書き・見出しと値の関係はinference改善が必要。このBoxの新たな編集実行は本レビューでは行っていない。"),
]

documents = []
for source, page in (("word_takeo_notice", 2), ("print_fcc_chrome", 1), ("word_niigata_hearing", 1)):
    inventory = json.loads((ROOT / "inventory" / f"{source}.json").read_text(encoding="utf8"))
    model = next(p for p in inventory["pages"] if p["page"] == page)
    documents.append({"id": source, "page": page, "source_sha256": inventory["sha256"],
                      "render_path": f"evaluations/realpdf/review_assets/{source}_p{page}.png",
                      **{k: model[k] for k in ("glyphs", "runs", "lines", "paragraphs", "paint_spans", "single_glyph_paint_spans", "drawings", "images", "clips")},
                      "boxes": len(model["boxes"])})

payload = {
    "schema_version": 1, "reviewed_on": "2026-09-06",
    "scope": "武雄第2頁、Meural/Chrome第1頁、新潟第1頁の選択済み編集領域と、同じ頁の代表的な難しい構造のみ。全頁・全文の正確性を保証しない。原本の推定レビューであり編集後の成否判定ではない。",
    "method": {"renderer": f"PyMuPDF {pymupdf.VersionBind}", "render_scale": 1.5,
               "visual_review": "PNG 3件をview_imageで目視し、inventoryとextract_pageの各Line/Run/Paragraph/TextBoxを照合済み。",
               "inference_sha256": hashlib.sha256((ROOT.parents[1] / "pdfeditor" / "inference.py").read_bytes()).hexdigest(),
               "limitations": "意味のまとまりと作者の段落タグは区別した。抽出器が返したpaint span数はPDF text-show operator数とは同一視しない。画像や隠れた文字を含む全文のglyph正解率は算出していない。"},
    "documents": documents, "observations": observations,
}
(ROOT / "review_remaining.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf8")
print(f"Wrote {len(documents)} scoped page reviews and {len(observations)} observations.")
