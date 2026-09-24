# Continuation開発の再開地点 — 2026-09-24

利用者の「最短の区切りでコミット」指示による途中保存。起点は`02a526ff2781ae80551f2ad4367f6f8d50c2b430`。サブエージェントは使用していない。

## 実装済み

- 明示destination契約、ページ先頭の初期graphics state、独立した生成slotと所有関係。
- 既存shaper/font writerと単一Transactionによる生成・再編集。
- marker/program digestとmutation mapによるbinding、shorten/dormant/regrow。
- 非描画Tc/Ts証跡とtext matrixの復元、保守的なtext paint範囲の衝突判定。
- API・設計資料、回帰テスト、既存LibreOffice原本の評価コード。

## 途中保存時の検証状況

以下は`52a63d0`作成時点の記録である。最終結果は次節を参照。

最初の追加回帰21件は成功（546.82秒）。4 alignment、tracking/rise付き生成→再編集→短文化→再長文化→no-op、および拒否・公開失敗を含む。ただし、その後のguard補強前の結果であり、最終コードの全件成功とは扱わない。

text matrix復元修正後、後続の未選択文字を保つテストと同一ページ生成の2件が成功（25.91秒）。最後のpaint envelope補強後、これら2件とpaint範囲・部分所有の回帰が成功（3 passed / 23 deselected、21.90秒）。ローカル証跡は`tmp/continuation-checkpoint.xml`。

全suiteは実行開始したが、レビューで見つかった安全性修正のため停止。`tmp/continuation-full-verified.log`は11%までの途中ログで、完走結果ではない。過去の614 passed / 2 skippedを今回の結果として流用しない。

外部原本は`evaluations/realpdf/corpus/lo_migration_ja.pdf`。4/5ページのsource replayを実行し、245文字を既存slotへ209文字、6ページの新slotへ36文字・2行とする計画まで到達した。`evaluations/continuation/runs/verified/`は途中成果物であり、overflow以降の保存系列・独立renderer監査は未完了。公開`summary.json`を作成していない。

## 再開セッションの結果 — 2026-09-24

起点は`52a63d0`。Linux x64 container、Python 3.12.3、lockfileの版（PyMuPDF 1.27.2.3、pypdf 6.10.0、uharfbuzz 0.55.0、fontTools 4.64.0、Pillow 12.3.0、reportlab 4.5.1、pytest 9.1.1）で実行した。以下はすべて同じ最終engineの結果である。engineは`pdfeditor/*.py`の44ファイルで、評価コードと同じファイル名→SHA-256のmapをkey順JSONにしたdigestは`a20828d95176ea6b8473654f144b74d88b2579f5c89a20d6063643b31fec52e0`。`.gitattributes`の`* -text`により、Windows checkoutでも同じbytesになる。

| 検証 | 結果 |
|---|---|
| `tests/test_continuation.py` | 26 passed（260.41秒）。4 alignmentの生成→second→shorten→regrow→no-op、拒否7種、空き確認6種、late failure 3種を含む |
| 全suite `python -m pytest -q -o cache_dir=... --junitxml=...` | 643件中625 passed / 18 skipped / 0 failed（879.16秒、RSS記録用の外部pluginのみ追加）。`02a526f`で収集した616 IDは全て存在し、新規は継続26件と下記の回帰1件 |
| 外部原本の継続評価 | **未実行**。理由は下記 |

skip 18件はすべて環境要因である。Windows font（Arial / Noto Sans JP）不在11、外部corpus未取得5、pypdf AES provider（cryptography / pycryptodome）不在2。lockfileはAES providerを含まない。

### 修正した問題

`ShapedFont`の`ink / outline / shape`はclass単位の`lru_cache`だった。cache keyの`self`を通じて、font bytes、fontTools table、HarfBuzz faceを最大8192件分、プロセス内に保持していた。shaperは操作ごとにproviderを開くので、lifecycleテスト1件で約1.6GBが解放されずに残った。そのため全suiteは15GB制限のcontainerでOOM停止した。memoizationをinstance単位へ移した。key、上限、同一instance内での再利用は変わらない。`ShapedFont`は同一性でhashするため、instance間でcacheを共有していたわけでもない。回帰`test_memoized_shaping_does_not_keep_used_fonts_alive`は修正前に失敗し、修正後は成功する。修正後の常駐は全suiteの実行中も約290MBだった。

### 環境上の注意

- Python 3.11では、lockfileのPyMuPDF 1.27.2.3の`Page.get_texttrace()`が呼出しごとに`None`の参照数を3、`get_bboxlog()`が約1減らす。長い処理では`Fatal Python error: none_dealloc`でabortする。PyMuPDFだけの最小再現でも起きる。3.12以降は`None`がimmortalなため起きない。上の結果は3.12で取得した。
- `rich_layout.wrap`は、文脈依存shapingのため各行で全候補境界を計測し、結果を保持する。容量超過の拒否テスト（約1,600文字）は一時的に約4.3GBを使う。拒否結果は正しいため、今回は変更していない。

### 外部原本評価が未完了の理由

- 原本host `wiki.documentfoundation.org`とweb archiveへの接続が、このcontainerのnetwork policyで拒否された。
- 評価providerの`msmincho.ttc` face 1と`times.ttf`、およびPoppler / pypdfの既定pathはWindows検証環境を前提とし、このcontainerにはない。代替fontで実行すると評価者が確認したprovider判断が変わるため、実行していない。
- engineが変わったため、以前の`runs/verified`も最終engineの証跡ではない。公開`summary.json`は作成していない。

## 最短の再開手順

1. Windows検証環境で最新commitを取得し、上のengine digestと一致することを確認する。
2. 新しいrun名で`python -m evaluations.continuation.evaluate --run-name <name>`を実行する。元PDF no-op、overflow、reopen、second、shorten、regrow、最終no-opを完了し、画像も目視確認する。
3. 同じengine digestの成功結果だけを`evaluations/continuation/summary.json`へ記録し、資料の「検証中」表示を更新する。
4. 評価のためにengineを修正した場合は、継続テストと全suiteを再実行する。

最終コードについて未完了の全体結果を先取りしない。外部PDF・派生PDF/PNG・font・本文/glyphログは引き続き公開しない。
