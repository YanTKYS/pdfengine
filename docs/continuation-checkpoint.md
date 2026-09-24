# Continuation開発の再開地点 — 2026-09-24

利用者の「最短の区切りでコミット」指示による途中保存。起点は`02a526ff2781ae80551f2ad4367f6f8d50c2b430`。サブエージェントは使用していない。

## 実装済み

- 明示destination契約、ページ先頭の初期graphics state、独立した生成slotと所有関係。
- 既存shaper/font writerと単一Transactionによる生成・再編集。
- marker/program digestとmutation mapによるbinding、shorten/dormant/regrow。
- 非描画Tc/Ts証跡とtext matrixの復元、保守的なtext paint範囲の衝突判定。
- API・設計資料、回帰テスト、既存LibreOffice原本の評価コード。

## 検証状況

最初の追加回帰21件は成功（546.82秒）。4 alignment、tracking/rise付き生成→再編集→短文化→再長文化→no-op、および拒否・公開失敗を含む。ただし、その後のguard補強前の結果であり、最終コードの全件成功とは扱わない。

text matrix復元修正後、後続の未選択文字を保つテストと同一ページ生成の2件が成功（25.91秒）。最後のpaint envelope補強後、これら2件とpaint範囲・部分所有の回帰が成功（3 passed / 23 deselected、21.90秒）。ローカル証跡は`tmp/continuation-checkpoint.xml`。

全suiteは実行開始したが、レビューで見つかった安全性修正のため停止。`tmp/continuation-full-verified.log`は11%までの途中ログで、完走結果ではない。過去の614 passed / 2 skippedを今回の結果として流用しない。

外部原本は`evaluations/realpdf/corpus/lo_migration_ja.pdf`。4/5ページのsource replayを実行し、245文字を既存slotへ209文字、6ページの新slotへ36文字・2行とする計画まで到達した。`evaluations/continuation/runs/verified/`は途中成果物であり、overflow以降の保存系列・独立renderer監査は未完了。公開`summary.json`を作成していない。

## 最短の再開手順

1. 現在のHEADと変更状態を確認する。既存資料を最初から調べ直さない。
2. `tests/test_continuation.py`全件を実行し、追加guardを含むlifecycleを確認する。
3. 新しいrun名で`python -m evaluations.continuation.evaluate --run-name <name>`を実行する。元PDF no-op、overflow、reopen、second、shorten、regrow、最終no-opを完了し、画像も目視確認する。
4. `python -m pytest -q -o cache_dir=tmp/pytest-cache-continuation-full --junitxml=tmp/continuation-full-final.xml`を完走させる。既存616 test IDの保持も以前のJUnitと照合する。
5. 最終engine hashと一致する検証結果だけを公開集計へ記録し、資料の途中表示を更新してcommitする。

最終コードについて未完了の全体結果を先取りしない。外部PDF・派生PDF/PNG・font・本文/glyphログは引き続き公開しない。
