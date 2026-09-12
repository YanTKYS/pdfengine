# 輪郭に基づく衝突境界の評価

前段階のFCC仮想プリンタPDFでは、明示した同じfollows gapへBを戻す短文化がfont bboxで拒否された。
同じ原本・選択・幅・固定paint・関係を使い、`document_flow`の8段階を実行する。
原本のoperator no-opは既存証跡を照合して再利用できる。

```powershell
.\.venv\Scripts\python.exe -m evaluations.document_flow.evaluate --case fcc --run-name ink-local --reuse-gates evaluations/document_flow/runs/fcc_movement
.\.venv\Scripts\python.exe -m evaluations.ink_collision.evaluate --lifecycle evaluations/document_flow/runs/ink-local --run-name boundary-local
```

初めて評価する環境では`--reuse-gates`を省略する。入力PDFのURL・SHA-256と確認値は既存`document_flow/evaluate.py`の`CASES`にある。
Windows Arialのfull-font recipeは文章置換用で、未変更Bは元fontのコードを使い続ける。

第2コマンドは保存済みの結果と実行coreの一致を確認し、g2で使われた事前geometry certificateを集計する。
最終レビューで追加したcertificateとmutationのPDF SHA一致guardについては、影響するg2だけを最終coreで再実行し、先のg2とPDF bytesが一致することと独立renderer検証を確認する。8段階を実行したcoreと最終coreのhashは分けて記録する。
g2のBをさらに2pt上げる余白不足と、Aと同じbaselineへ置く負例を追加し、出力が作られないことを確認する。
Unicode/font/paint/画像/領域外画素とPoppler 144dpiによる独立検証は、第1コマンドの各段階で行う。
最終no-opは両rendererの対象ページ全画素を比較する。

生のPDF、sidecar、glyph情報、paint情報、PNGはignored `runs/`内に留める。
公開する`summary.json`はURL、hash、数値、分類と限定条件だけを含む。
既存のWord PDFなど、衝突境界が変わらない高コストの評価は機械的に再実行しない。
