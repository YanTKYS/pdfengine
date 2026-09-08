# Paint観測の技術比較

評価日: 2026-09-08。これは描画状態を取得する方式の評価であり、paint編集機能の追加ではない。再現コードは [observe_device.py](../evaluations/paint/observe_device.py)。productionのguardは変更していない。

## 今回の優先課題との関係

新規fontによる局所組版の拒否原因には、丸角背景・複数subpathのbbox過大評価がある。しかし、実形状を取得する方法が存在しないわけではない。MuPDFはすでに描画順とclipを解釈している。一方、文字列を変更した際に書式・強調・下線等をどこへ引き継ぐかは、rendererから得られる幾何情報だけでは決まらない。

そのため今回は、書式を持つ文章の編集を主課題とし、paint側では成熟した実装を利用できることを読み取り専用の実測で確認した。成功件数を増やすために背景やclipのguardを解除する判断ではない。

## 利用できる既存API

| 方式 | 取得できる情報 | 残る境界 |
|---|---|---|
| `Page.get_drawings(extended=True)` | Bezier・矩形・quad、fill rule、色・opacity、描画順、clip/group階層 | textは階層の要素として出力されず、clipにseqnoがない。単独では任意の文字へのclip対応付けが不足 |
| `Page.get_bboxlog()` + `get_texttrace()` | path・text・image・shadeの共通描画順、textのseqno・glyph情報 | bboxは実際の塗り領域や遮蔽の証明ではない |
| MuPDF `FzDevice2` callback | textと非textのpaint、clip push/pop、group等を一つの順序で観測 | 元PDFのbyte range、operator、Form invocationの識別子を提供しない |
| PDFium object API | page/Formのobject列挙、削除、再生成等 | 本評価では文書調査のみ。文章や装飾のownershipは別モデルが必要 |

PyMuPDFの[描画取得API](https://pymupdf.readthedocs.io/en/latest/page.html#Page.get_drawings)はpathの構成要素とclip/group階層を説明している。[bboxlog API](https://pymupdf.readthedocs.io/en/latest/functions.html#Page.get_bboxlog)は、リストのindexを描画・texttraceのseqnoと対応付けている。

低レベルの[MuPDF device interface](https://mupdf.readthedocs.io/en/1.28.2/_static/generated/c/html/device_8h_source.html)にはpath/text/imageの描画・clip、group、mask等のcallbackがある。今回は既存依存のPyMuPDF 1.27.2.3に含まれるbindingsで実測した。参照した公開ドキュメントの版と実測版は同一ではない。新しい依存ライブラリは導入していない。

[PDFium公式ヘッダー](https://pdfium.googlesource.com/pdfium/%2B/main/public/fpdf_edit.h)でもobject操作を確認したが、移植の互換性・保存忠実性を実測したわけではなく、今回の採用根拠にはしていない。

## 実PDFでの観測結果

既存の手動selectionに対応する15原本の対象ページを走査した。合計 **1,124 paint event** について、callbackの種類・順序・boundsが`get_bboxlog()`と一致した。最大bounds差は **0 pt**。`get_texttrace()`の各seqnoも該当するtext paint種別を指し、15ページすべてでclip stackが空に戻った。これは同じMuPDF内部の観測API間の整合性確認であり、Poppler等による独立した描画検証ではない。

| 原本ID | Paint event数 |
|---|---:|
| word_osaka_fire_notice | 82 |
| word_osaka_guideline | 108 |
| word_wakayama_guidelines | 42 |
| word_takeo_notice | 189 |
| lo_migration_ja | 125 |
| lo_newfeatures_ja | 110 |
| lo_enterprise_en | 63 |
| print_canvia | 64 |
| print_ubiquiti | 10 |
| print_fcc_ms | 52 |
| print_fcc_chrome | 65 |
| word_kyoto_questions | 38 |
| word_niigata_hearing | 3 |
| word_okinawa_procurement | 166 |
| word_osaka_symposium | 7 |

原本のURL・SHA-256は [sources.json](../evaluations/sources.json)。対象ページは [seed_ranges.py](../evaluations/backend/seed_ranges.py) によって再現できる既存のselectionと同じ。Unicode復元不能な原本でもpaintを観測できたが、その事実はtext operatorの安全な削除を可能にしない。

## 拒否証跡が示すモデル上の違い

- **Takeoの丸角背景**: fillのseqnoは6、対象文字は7/8。8個のBezier/lineで構成され、opacityは約0.702。背景としての順序は分かるが、bbox包含をそのまま実形状の包含と扱えない。透明度を無視した背景色による白塗りも適切でない。
- **FCCの複数矩形**: seqno 21/22のpathはページにまたがる合成bboxを持つが、個々の矩形は対象本文に一つも交差していない。構成要素を使えば、この過大な衝突範囲を解消できる。
- **FCCのcompound border**: seqno 38は丸角の外周と内周を含む。穴を持つfillを、外側bboxで全面的な障害物と扱うと誤る。fill ruleを含む幾何モデルが必要になる。
- **FCCの文字直後の線**: seqno 40/42/44は各text line直後の薄いfill pathで、下線と考えられる。これは描画順と位置からの推測であり、PDFに下線属性があるという意味ではない。長文化後にも元の位置のまま残すことが自然とは限らず、文章と装飾の関係を扱う必要がある。
- **Ubiquitiのheader clip**: 対象text callbackに作用するclip boundsは `(0, 14.25, 105, 24.75)`。ページ上の余白に関係なく、複数行化がこのclipを越えるという既存の拒否は正しい。
- **ページ全体clip**: Osaka Fire等では対象文字のclipがページ全体に相当した。clipがページに存在することと、そのclipが新しい組版を制限することを分けて判断できる。

LO新機能とChromeの対象ページには、それぞれ8/6個のForm参照があった。MuPDFの観測はそれらを含め正常だったが、対象Form invocationだけをcloneして編集する検証はしていない。

## 再現方法と適用範囲

```powershell
.\.venv\Scripts\python.exe -m evaluations.paint.observe_device `
  evaluations/realpdf/corpus/print_ubiquiti.pdf --page 1 `
  --output tmp/paint-observation.json --details
```

`--page`は1始まり。入力PDFを保存・変更せず、出力は新規ファイルだけを受け付ける。`--details`を省略すると集計のみになる。source SHA、probe SHA、PyMuPDF/MuPDF版を記録し、照合不一致は`passed: false`と終了コード1になる。JSONには原文・画像・font programを含めない。詳細な幾何ログはローカル証跡として扱う。

このprobeが保存するclip情報は**boundsと種類、作用するpaint順**であり、実際のpath geometry・mask pixels・transfer functionの完全な複製ではない。boundsが一致しても、穴やstroke clipを含む描画同等性の証明にはならない。画像mask、text clip、pattern tile等のcallbackを受け取る入口はあるが、このcorpusでそれらすべての正しさや編集能力を評価したわけではない。

将来はMuPDF deviceをpaint観測のadapterとして利用し、元operatorのbyte provenanceを扱う既存層と接続する価値がある。rendererが消費した状態の観測と、対象source operatorだけを書き換える能力は別の契約にする。clip/group/paintの観測が整っても、背景・装飾・固定要素・flow要素の区別は文書モデル側の課題として残る。
