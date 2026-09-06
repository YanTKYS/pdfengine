# 実 PDF backend 評価（Track A / B）

この文書は、既存 PDF の意味範囲推定と、文字を削除して元の位置へ再描画する backend を分けて評価した結果である。評価対象は `evaluations/realpdf/corpus` の外部生成 PDF 15 件（Word、LibreOffice、ブラウザ印刷、仮想プリンタ、subset/CID、日本語・英数字、図形・表を含む文書）である。対象範囲は `evaluations/backend/selections.json` に固定し、選択の SHA-256 とページ観測ハッシュを検証してから編集する。

## 結果

症例別の結果は [backend_matrix.csv](../evaluations/backend/backend_matrix.csv)、評価コード修正後の Stage 1 証跡は [stage1_audit_v3/results.json](../evaluations/backend/runs/stage1_audit_v3/results.json)、後続段階は [stages_audit_v5/results.json](../evaluations/backend/runs/stages_audit_v5/results.json) に保存した。原本と詳細証跡はローカル保持であり、公開範囲は [PUBLICATION.md](../PUBLICATION.md) を参照。

| 段階 | 件数 | 判定 |
| --- | ---: | --- |
| Stage 1 同文 no-op | 13/15 | MuPDF glyph/state、Poppler 144 dpi、pypdf 抽出を全て確認。2 件は編集せず拒否 |
| Stage 2 同じ文字数 | 6 成功 / 7 安全拒否 / 2 gate skip | 成功例は各1～2字、合計10字の同幅置換。一般的な文章置換の成功率ではない |
| Stage 3 末尾削除 | 13 成功 / 2 gate skip | 全件で末尾2字を削除。中間削除・大幅短文化・再折返しの評価ではない |
| Stage 4 明示幅 reflow | 2 成功 / 1 安全拒否 / 12 width 不明またはgate skip | 成功2件は短文化による複数行→1行。長文化・1行→複数行の成功例はまだ0件 |

Stage 1 の成功条件は抽出文字列だけではない。選択前後で Unicode 列、glyph ID、font resource、origin、bbox、font size、CTM、text/line matrix、Tc/Tw/Tz/TL/Ts/Tr、fill/stroke、opacity、clip の scope、追跡した graphics state、PDF `/W` advance を比較した。旧文字は対象 operator から除去した中間 PDFを保存し、MuPDF glyph検査に加えて pypdfで選択 Unicode列が抽出に残っていないことを確認した。削除検索は双方の空白を同じ方法で正規化し、原本では検索列が検出できることも必須にした。同文再描画のpypdf比較は空白を正規化せず、全ページの抽出結果を比較する。元 PDFの全ページは変更前後で MuPDF 144 dpiが一致し、対象ページはPoppler 144 dpiの全画素一致になった。

このno-opは、元の文字コードと描画状態を保持して対象operatorを再構成する検証である。任意Unicode列から同じ見た目を新規生成するfont writerが完成したことや、対象文書の任意範囲に対応できることを意味しない。

Stage 1 の13件は元の font resource、元の文字コード、元の `/W`/`/DW` を再利用した。新しい subset 文字が coverage に無い場合は代替 font を勝手に選ばず拒否した。Takeo の `U+8B70`、LibreOffice migration の `U+65BD/U+8A2D`、Canvia の `U+0041/U+006D`、Niigata の `U+7D04` がこの分類である。大阪シンポジウムは ToUnicode/Unicode復元不能で Stage 1 から拒否した。Ubiquiti の日付は複数 font/paint style が一つの選択範囲に混在するため拒否した。

Stage 2 は既存 font 内で同じ文字数に置換する。日本語の同幅 glyph の置換は成功したが、可変幅英字で slot advance が変わる場合は、利用可能幅が明示されていないため拒否した。Stage 3 は末尾2字を削除して元 slot と後続 cursor を保持する評価で、成功例では対象外 glyph の origin が変わらなかった。Stage 2/3の成功例は、選択bboxに1 ptのanti-alias fringe marginを加えた範囲の外でPopplerの8階調超画素差分が0だった。閾値以下の差分まで全て0という意味ではない。

Stage 4の幅は京都425 pt、沖縄468 pt、新潟230 ptを人手指定した。京都は147→33字・4→1行、沖縄は49→43字・2→1行で成功した。長文化候補の新潟7→25字は周辺glyphとの衝突で拒否した。明示幅があることだけで長文化できるとは結論しない。新しいtextは元resourceのcodeと`/W`から測定し、元のtext/line matrixに復帰してから後続operatorを実行する。画像、annotation、filled vector、罫線、未許可clipとの衝突は保存前の検査対象である。

## 評価コードの訂正

初回公開時のStage 2～4 runnerはPoppler PNGを生成していたが、`poppler_diff`の計算にはMuPDF画像を使っていた。実際のPoppler PNGを読むよう修正し、描画・抽出の失敗や不完全な証跡を`failed_evaluation`として合格から除外した。Stage 4の比較範囲は元bboxと検証済みの新bboxを合わせ、正当な文字の拡張を領域外破損と誤判定しないようにした。

Stage 1の削除検索にも空白正規化の非対称性があり、抽出失敗や検索対象が元から検出不能の場合を合格条件から排除できていなかった。双方の正規化、原本での検出、削除PDFの抽出成功・ページ数一致を必須にした。修正前の証跡は比較用に保持するが、現行の集計には再実行した証跡を用いる。合成テストでこれらの評価バグを検証し、実PDFの対応率とは別に扱う。

修正後も件数はStage 1が13件、Stage 2/3/4が6/13/2件の成功で変わらなかった。後続段階の21出力すべてで、対象ページの許可編集範囲＋1 pt外はPopplerの8階調超差分が0、未編集ページはMuPDF 144 dpiの寸法・全画素が一致した。Popplerの全ページ比較は今回実施していない。自動テストは207件成功、AES暗号化の2件は依存provider未導入でskipした。

ローカル原本と選択manifestを用いた再実行コマンドは次のとおり。出力フォルダーが存在する場合は新しいrun名を指定する。

```powershell
.\.venv\Scripts\python.exe -m evaluations.backend.stage1 --run-name stage1_audit_v3
.\.venv\Scripts\python.exe -m evaluations.backend.followup --run-name stages_audit_v5 --stage1-run stage1_audit_v3
.\.venv\Scripts\python.exe -m pytest -q
```

## subset fontの追加診断

Stage 2のcoverage拒否4件について、選択paint eventのfont resourceと埋込font programを照合した。[診断結果と次のwriter評価条件](subset-font-boundary.md)に詳細を記録した。CanviaではUnicode cmapと`has_glyph(fallback=False)`が非ゼロGIDを返しても、対応する輪郭が空だった。武雄では符号化不能を確認できたが、Unicode未対応の輪郭が残っているため「要求文字の輪郭自体が存在しない」とは断定しない。

現行の4件はcode map検査で拒否されており、これらの空glyphを許可した結果ではない。必要文字を受け入れる範囲を広げる際は、GID非ゼロの確認だけでは足りない。

## TextBox 幅と選択モデル

`observed_content_width`（観測 glyph の bbox）、`inferred_available_width`（推定できた組版幅）、`explicitly_supplied_width`（利用者が確認した幅）を `WidthConstraint` で分離した。単一の短い行の観測幅を利用可能幅へ暗黙変換しない。推定不能な幅は `unknown` のままとし、長文化・Stage 4を拒否する。CLI/APIは `observe` で Line/Run/glyph の候補を表示し、`select` で line/run/glyphを追加・除外し、幅を明示入力できる。Paragraph/TextBox推定は候補提示に留まり、選択 provenance の代わりにはしない。

## graphics state と旧文字除去

ページ上に clip が存在するだけでは拒否しない。`q/Q`、`cm`、clip path、text state、ExtGState、marked content、Form 呼出しを追跡し、選択 text event の state として記録する。対象 operator を元の byte 位置で変更するので、元の clip と描画順序がそのまま適用される。ただし text rendering mode 4--7、非矩形 clip、soft mask/blend、Type3、未知 CMap、Form 内の対象 invocationは能力を証明できないため拒否を維持する。

旧文字除去は bbox redaction ではなく、選択された `Tj`/`TJ`/quote operator の code を除去または空送りへ変換する。fill と stroke が同じ operator にある場合は両方を選択しない限り拒否し、同位置の別 operator は個別に扱う。共有 Contents stream は対象ページだけ copy-on-write で差し替える。中間 removal PDFを再抽出するため、白塗りや不可視化を削除成功とは数えない。

## 共通原因と次の境界

「同じ文章を戻せない」原因は文書固有の例外ではなく、次の4つに整理できる。

1. **writer の幅契約**: TextWriter は subset font の輪郭・glyph IDが同じでも、生成した CID `/W` が元PDFの `/W` と異なり、文字が重なる実例があった。元コードを直接 operatorへ戻す方式で解消した。
2. **font coverage/Unicode**: 同じ font 名でも必要 code/glyph が存在するとは限らない。元 resource の code map、ToUnicode、CID width、実 glyph を確認し、未収録文字は拒否する。新 glyphを作るなら完全な font writer（font program、CID、ToUnicode、`/W`、埋込み）を別backendとして実装する必要がある。
3. **graphics state scope**: ページ全体の clip 有無ではなく、対象 paint eventへ作用する state を追跡する必要がある。安全に復元できない clip、透明、text clip、Formは拒否する。
4. **保存の再直列化**: MuPDFの編集なし保存でも resource数値の再直列化によってPoppler画素差分が発生した。現在は対象 streamだけ変更し、pypdf full clone保存で元 resource値、共有 object、暗号化、到達可能 objectを回帰検証している。保存 adapter の詳細は [backend-options.md](backend-options.md) と [test_pdf_save.py](../tests/test_pdf_save.py) にある。

現時点で安全に言える範囲は、**人間が正しい単一 style の横書き範囲を指定し、元 font が必要 glyph を持ち、同文または同じ slot幅の短い置換で、対象に作用する state が追跡可能な業務文書本文**である。自動 TextBox 推定、長文化、ページ全体 reflow、表組み、OCR、縦書きはこの結論に含めない。推定モデルを捨てる必要はなく、編集前の候補提示と明示範囲の補正に使う。
