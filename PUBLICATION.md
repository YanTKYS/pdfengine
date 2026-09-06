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

現在の範囲と制限は [backend 評価](docs/backend-evaluation.md)、技術選定は [backend 比較](docs/backend-options.md) を参照。
