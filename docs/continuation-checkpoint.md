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

## 外部評価の実行確認 — 2026-09-24（`beaecad`）

起点はPR #4のmerge commit `beaecad`。`pdfeditor/*.py`は変えておらず、engine digestは上の`a20828d9…52e0`と一致した。

| 確認項目 | 結果 |
|---|---|
| Python / PyMuPDF / MuPDF / pypdf | 3.12.3 / 1.27.2.3 / 1.27.2 / 6.10.0（Linux x64 container） |
| Poppler | `pdftoppm` 24.02.0（container内） |
| 原本`lo_migration_ja.pdf` | 不在。取得元hostはnetwork policyで403。別の取得元や別PDFは使っていない |
| `msmincho.ttc` face 1 / `times.ttf` | 不在。代替fontは使っていない |

このため外部原本の編集系列は**実行していない**。`summary.json`は作成せず、「検証中」を維持する。

### 評価コードの修正

変更は`evaluations/continuation/evaluate.py`だけで、engineは変えていない。原本、provider、face、hash、領域、保護範囲、既存の照合は変えていない。

- **容量拒否の入力**: `extra*20`（1,600字追加）を`extra*2`（160字追加）にした。`_layout`は各regionで残り全文を組み、行ごとに全改行候補を計測する。日本語はほぼ全文字が改行候補になるため、組版量は文字数の3乗で増える。合成PDFで150・300・450字を拒否させた実測は3.5・12.8・39.1秒、ピーク214・564・1,435MBだった。原本の行幅に当てはめると、`extra*20`は1回の組版で約2,400万glyph、約25GBになる。この拒否は評価の最後に実行されるため、メモリ不足になると全段階が成功しても集計が書かれない。160字追加でも最終245字は確認済み容量（約271字）を3行以上超え、同じ拒否経路を通る。
- **段階ごとの明示照合**:
  - 生成slotを作るのはoverflowだけで、作成証跡はその後も変わらない。
  - shortenでは生成slotが文字を描かない。
  - final no-opでは、paragraph・style・destinationの記録、slotのidentity・範囲・行geometry・alignment・inline style、計画glyph（Unicode・GID・origin・size・advance・code・CID・`W`幅）が直前のregrowと一致する。
  - これまでは画素一致による間接的な保証だけだった。
- **集計の記録**: engine digest、評価helperのhash、Python・platform、Poppler・独立pypdfの版、providerのfile・face・hashを加えた。Popplerの存在は、版表示の文字列で判定する（`-v`で99を返すbuildがあるため）。

### 評価コードのdry-run（証跡ではない）

修正後の評価コードを、合成の7ページPDFで最後まで動かした。置き換えたのは入力（原本・座標・provider）とWindows固定の外部tool pathだけで、段階処理・監査・拒否は評価コードそのものである。全段階と容量拒否が79秒で通った。外部原本の代わりにはならないため、集計にも資料の結果にも使わず、成果物もcommitしていない。

同じdry-runで、生成blockが保存ごとに2,327Bから9,487Bへ増えることを確認した。既存writerは、置き換えた文字のoperatorを削除せず、非描画の`[-1000] TJ`へ書き換えてtext matrixの状態を保つ。この増加はその設計によるもので、source slotの再編集でも同じように起きる。画素・Unicode・照合には影響しないため、変更していない。

## 最短の再開手順

Windows検証環境で[評価README](../evaluations/continuation/README.md)の手順を実行する。

1. 最新commitを取得し、engine digest `a20828d9…52e0`と評価コードの`runner_sha256` `c2c279db3ee58d6a4594481e73a0280bbb78be98f35f75a9df8d75ec50503537`を確認する。
2. 原本（SHA-256 `1366587531…a5f3`）、`msmincho.ttc` face 1、`times.ttf`、Poppler、独立pypdfを揃える。
3. 未使用のrun名で`python -m evaluations.continuation.evaluate --run-name <name>`を実行する。
4. overflowのallocationを以前の計画（既存slot 209字、生成slot 36字・2行）と比べ、画像を目視する。
5. すべて成功した場合だけ、そのrunの`summary.json`を`evaluations/continuation/summary.json`へ置き、資料の「検証中」を更新する。
6. 評価のためにengineを修正した場合は、継続テスト、関連テスト、外部評価、全suiteを最終コードで再実行する。

最終コードについて未完了の全体結果を先取りしない。外部PDF・派生PDF/PNG・font・本文/glyphログは引き続き公開しない。
