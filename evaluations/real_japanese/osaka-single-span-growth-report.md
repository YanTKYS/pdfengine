# 大阪府Word由来PDF：単一Span文字数増加 — REFUSED

記録日：2026-10-20（Asia/Tokyo）。ブランチ `claude/vibrant-hamilton-2mwfzf`。
PR #78マージ後のmain `b1551c0b8b3e1bfa16c2f31ba59bbec8c61de5cd` から開始。
production変更なし。前回のSpan62同文字数置換・削除の評価は再実行していない。

## 結論

**REFUSED — 既存の単一Span契約と承認可能な容量の範囲で、文字数を増やせるSpanが両PDFの1ページ目に存在しない。**
実PDFの保存は0回（長文化編集・no-opとも未実施）。sidecar・144dpi比較・再open検証は保存物がないため未実施。

直接原因：

1. 第一候補 `word_osaka_guideline.pdf`：単一Span契約の形（scope tag `/P`、leaf owner `/Span`、ActualText＝描画Unicode）を満たす本文scopeは33個あるが、
   - 行末にあるのはSpan62（MCID28）だけ。承認済みの右端525.0ptまでの余地は **2.086pt** で、埋め込みF1（MS Gothic subset）の最小可視advance **5.28pt** に満たない。可視glyphを1文字も追加できない。
   - 他の32個は、同じ行の右隣に別ownerのglyph（多くはWordの段落末空白Span）が接している。空きは最大でも **4.26pt**（空白Span自身。本文Spanは0.04pt以下）で、やはり5.28pt未満。
2. 第二候補 `word_osaka_fire_notice.pdf`：本文テキストのownerはすべて `/P`（scope tag `/Span`）で、ActualTextを持たない。単一Span契約の形を満たすscopeは **0個**。leaf `/Span` ownerは空白・数字・ハイフンだけ。

可視glyphを追加するには、次のいずれかが必要になる。

- 右隣ownerの移動（後続ownerの移動）
- 複数Spanへの文字の再配分
- Wordの本文カラム外（右余白）への領域拡大

いずれも今回の停止条件、または禁止事項に該当する。

## 対象PDFとSHA

| PDF | 取得元 | SHA-256（評価前後で不変） |
| --- | --- | --- |
| `evaluations/realpdf/corpus/word_osaka_guideline.pdf` | https://www.pref.osaka.lg.jp/documents/63648/guideline_tsuuchi.pdf | `9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe` |
| `evaluations/realpdf/corpus/word_osaka_fire_notice.pdf` | https://www.pref.osaka.lg.jp/documents/63648/20250327jimurennraku.pdf | `02036efa843b1cbf18e8454583e9a160ed7d635e9d054fb639d6ff1f96ab3afa` |

原本は `evaluations/sources.json` のURLから取得し、SHA一致を確認してから読み取り専用で使用した。加工・変換・再生成はしていない。

## 方法

`evaluations/real_japanese/osaka_span_growth.py`（読み取り専用）で実施した。

1. **観測**：`observe_marked_content` で全scopeを列挙した。各scopeについて、glyph bbox、行数、ActualText一致、同じ行で右隣にある別ownerのglyph、行に重なるvector drawingを記録した。クリップはテキストscopeすべてで `q re W* n` の矩形（ページ全体）だった。
2. **詳細調査（各PDF最大5 Span）**：自然な文字数増加の編集を1件ずつ用意し、矩形2種類でproposal → accept → `plan_single_span_edit` を実行した。providerは、そのSpanの元埋め込みfont program（SHA一致）を明示した。
   - `tight`：x1＝元glyphの右端。新しい容量を与えない。
   - `column`：x1＝観測で正当化できる最大の右端。第一PDFでは前回承認済みの525.0pt（観測した本文カラム右端は524.62pt）。第二PDFでは観測した可視glyphの最大右端547.07pt。ページ余白までは広げていない。
3. 文字サイズ・fontの変更、領域の任意拡大、比較マスクの拡大は行っていない。

## 候補と直接の拒否理由

### 第一候補 `word_osaka_guideline.pdf` 1ページ目（97 scope、591 glyph、drawing 4件）

| MCID / owner | 編集前 → 編集後（文字数） | tight | column |
| --- | --- | --- | --- |
| 28 / Span62 | `国立感染症研究所においてガイドラインが作成されましたので、参考に送` → `…研究所におきましてガイドライン…`（33→35） | proposal拒否：無編集レイアウトが元bbox幅を超える | proposal承認、plan拒否：`Reflowed text exceeds the available vertical space`（1行に収まらない） |
| 23 / Span56 | `日頃より、…御協力いただき、厚く御礼申し上げます。` → `御協力をいただき`（36→37） | 同上 | proposal拒否：`single Span rectangle adds foreign glyph capacity`（右隣Span57の空白glyph） |
| 31 / Span66 | `つきましては、…周知いただきますようお願いいたします。` → `周知していただき`（42→44） | proposal拒否：`unsupported semantic override`（owner66にActualTextがない） | 同左 |
| 29 / Span63 | `付いたします。` → `付させていただきます。`（7→11） | proposal拒否：無編集レイアウトが元bbox幅を超える | proposal拒否：`adds foreign glyph capacity`（右隣Span64の空白glyph） |

Span62の容量の内訳：

- 承認済み矩形 `(176.05999755859375, 335, 525, 351)`、幅348.940pt
- 無編集のレイアウト幅は346.854pt。残りは2.086pt
- F1埋め込みprogram：MS Gothic subset、cmap 153項目、upem 256。可視outlineを持つ最小advanceは128 units（`!`）で、10.56ptでは5.28pt
- Span62はWord上で次行のSpan63（`付いたします。`）と一語（「送付」）でつながっている。文字を後ろへ送るには複数Spanへの再配分が必要になり、これは停止条件に該当する
- 525ptを超える右側にはforeign contentがない。しかし、そこはWordの本文カラム外（右寄せ行の右端は524.14〜524.62pt）である。「見た目の余白」を理由にした拡大にあたるため承認しない

詳細調査は4 Spanにとどめた。観測表の時点で、ほかの適格形Spanも右隣との空きが0.04pt以下（空白Spanでも4.26pt）であり、可視glyph 1文字（5.28pt）が入らないことが確定していたためである。

### 第二候補 `word_osaka_fire_notice.pdf` 1ページ目（47 scope、403 glyph）

| MCID / owner | 編集前 → 編集後（文字数） | 拒否理由（tight・column共通） |
| --- | --- | --- |
| 23 / P45 | `日に発生した林野火災に係る感染症対策等について` → `に関する`（23→24） | `unsupported envelope or properties`（scope tag `/Span`、owner `/P`、2 MCIDを持つ） |
| 26 / P50 | `標記災害に際して、…場合は、…` → `場合には`（75→76） | `source exceeds the approved rectangle`（2行のscope） |
| 28 / P52 | `なお、…今後、別添に…` → `今後とも、`（66→68） | 同上（2行） |
| 30 / P54 | `また、…対策については…` → `につきましては`（70→72） | 同上（2行） |
| 40 / P63 | `直通：` → `直通電話：`（3→5） | `unsupported envelope or properties` |

第二PDFは、本文が `/P` 直下のMCIDで、leaf Span ActualTextを持たない構造である。既存の単一Span契約は適用できない。適用するにはStructureTreeの変更か、新しいowner契約が必要になり、停止条件に該当する。

## 監査項目の状態

| 項目 | 状態 |
| --- | --- |
| 原本SHA不変 | 確認済み（評価前後とも上表のとおり） |
| font証明（新glyphのGID・outline・advance） | 未実施。planが容量で拒否されたため、新glyphの配置まで進んでいない |
| ActualText・構造owner監査 | 観測のみ実施。単一Span契約の拒否理由は上表のとおり |
| foreign content・クリップ | 観測のみ実施。クリップはページ全体の矩形1つ。第一PDFのvector drawingは下線3本と枠1つ（y=528〜702）で、候補行とは重ならない |
| MuPDF・Poppler 144dpi比較 | 未実施（保存物なし） |
| 保存・`restored`・no-op | 未実施（保存0回） |
| PDF・sidecarのSHA | 該当なし |

## 実行時間

- 評価スクリプトは合計9.75秒（第一PDF 8.08秒、第二PDF 1.67秒）
- プロセス全体（`time`、import込み）は10.23秒
- 環境：Linux、Python 3.13.16、PyMuPDF 1.27.2.3、pypdf 6.10.0、fontTools 4.64.0

## テスト

production変更がないため、テストは実行していない。full suite・focused testsとも未実行。

## 観察事項（未修正）

- `propose_single_span` は、矩形が無編集レイアウト幅に満たない場合に `PdfError` ではなく `LayoutError`（ValueError）を送出する。`plan_single_span_edit` では、同じ容量不足が `single Span provider qualification failed: …` と報告される（`_provider` が ValueError を包むため）。安全側の拒否であり、結果に影響しないため今回は修正していない。

## 再実行

```
.venv/bin/python -m evaluations.real_japanese.osaka_span_growth
```

出力は `tmp/osaka-single-span-growth/summary.json`（全scopeの観測表とprobe結果）。抽出したfontも同じディレクトリに置く。PDF・font・tmp成果物はコミットしていない。
