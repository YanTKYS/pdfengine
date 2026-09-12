# 外部PDFの複数編集transaction評価

`print_fcc_ms.pdf`（Microsoft Print to PDF由来）の確認済みA/Bと固定の注記枠を使う。取得元・原本SHAは`summary.json`に記録する。原本、font、派生PDF/PNG、sidecar、生のglyph証跡は再配布しない。

前提：[`document_flow`](../document_flow/README.md)と[`ink_collision`](../ink_collision/README.md)で生成・検証した`g1.pdf`/`g1.json`が必要。g1 PDF SHAは`207b4e68a3e2c25cb493917e4531c23f5385edf4ceb445aed236fe8ee6b0981d`。再利用する原本no-opと既存backendの最終hashを検査する。再実行目的だけで原本の全評価を繰り返さない。

```powershell
.\.venv\Scripts\python.exe -m evaluations.flow_transaction.evaluate --lifecycle evaluations/document_flow/runs/ink_final --run-name new-run
```

Windows Arialの明示font recipeと、既存のPoppler/独立pypdf評価環境を使用する。公開済み集計はローカル実測の結果であり、利用者のfontや環境が違う場合は元と同じ表示を保証しない。

- 同じ入力revisionにA/B両方のUnicode編集を指定する。
- プレビュー計画と実行計画を照合し、短くなる側から既存writerを実行する。
- A短文化/B長文化、逆方向、双方空、双方再入力、双方no-opを保存・再openする。
- 各保存後にPoppler 144dpiとMuPDFの領域外画素、独立した全ページtext extraction、font code/GID/幅、固定nontext paint、画像を確認する。
- 入力順の先頭だけを単独実行して失敗する例、最終overflow、関係なし、未実装resize、外部保存を負例にする。

詳細な判断・契約・未対応範囲は[設計資料](../../docs/planned-document-transactions.md)。frameのsource geometryに曲線が含まれるという観測を、resize許可へ読み替えない。

今回の実測は5 batch成功、6対照を安全に拒否。全5保存で領域外差分0、固定paint・画像・独立全ページ抽出が一致し、最後の両paragraph no-opは両rendererの全画素が一致した。外部原本は1件、各batchは同じPDFの連続したrevisionであり、5種類の原本に対する成功率ではない。
