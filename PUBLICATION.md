# 公開リポジトリに含むもの

このリポジトリはエンジン、テスト、サンプル生成コード、評価スクリプト、評価報告と集計を公開する。
独自コードは [AGPL-3.0-only](LICENSE) で公開する。依存ライブラリと外部資料の条件は [ライセンス方針](docs/licensing.md) に整理した。
外部から取得した PDF 原本、そこから作成した PDF・PNG、全文抽出や glyph の詳細を含む生の評価ログは同梱しない。
これらのファイルは作業環境のローカルに保持している。報告中の `corpus`、`runs`、`output` 等へのリンクには、ローカルで生成・参照する資料も含まれる。

入力原本の取得元 URL、保存先、SHA-256 は [evaluations/sources.json](evaluations/sources.json) に記録した。
実 PDF 評価を再実行する場合は原本をそれぞれの配布元から取得し、保存先とハッシュを照合する。
取得元での公開は再配布許諾を意味しないため、この Git 履歴には原本・派生画像を含めない。

生成できる A--E のサンプルは `python -m examples.demo --output-dir output/demo-next` で作成できる。
外部 corpus のない環境では、対応する実 PDF 回帰テストは skip する。報告にある全件結果は元のローカル評価環境で実施した結果である。

新規font組版の現行評価は [composition.md](docs/composition.md) と [公開集計](evaluations/composition/summary.json) を参照。完全なfontは別途用意し、評価スクリプトの `--font-dir` へ指定する。フォント名・face index・variation軸・明示領域は評価入力であり、エンジン内にPDF別の分岐はない。新規組版のPDF、削除checkpoint、PNG、glyph計画、原文は公開集計へ含めない。

元resource経路の範囲と制限は [backend 評価](docs/backend-evaluation.md)、技術選定は [backend 比較](docs/backend-options.md) を参照。

書式を残す部分編集は [attributed-editing.md](docs/attributed-editing.md) と [公開集計](evaluations/attributed/summary.json) に記録する。評価入力は `evaluations/attributed/evaluate.py` の既知範囲・Unicode編集・明示領域であり、全文抽出、snapshot、glyph計画、PDF/PNGは公開しない。paint観測の比較も [専用資料](docs/paint-observation-options.md) に整理している。

sourceとpaintの対応、背景・装飾の所属、要素移動は [element-ownership.md](docs/element-ownership.md) と [公開集計](evaluations/elements/summary.json) に記録する。`evaluations/elements/evaluate.py` が確定した範囲・関係と独立検証を再現する。詳細なelement snapshotには元のpath座標等が含まれるため、原本・派生PDF/PNG・全文・生のsnapshotは引き続き公開しない。

文字範囲に追従する下線の評価は [anchored-decoration.md](docs/anchored-decoration.md) と [公開集計](evaluations/anchors/summary.json) に記録する。`evaluations/anchors/evaluate.py` が外部PDFの確認済み範囲、境界編集、行結合の確認、独立した描画・抽出検査を再現する。公開するのは集計・hash・コードであり、anchor候補に含む元の文字列やpath座標、確認済みsnapshot、派生PDF/PNGは含めない。
