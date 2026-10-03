# Continuation開発の再開地点 — 2026-09-24

**生成blockの累積の解消（PR #29後、2026-09-30）**: `382af05b137cca00dde33d1bee2dcd2235f747e0`で、合成lifecycleの生成blockは保存ごとに増えていた。page entryと境界の両方で、grow 3,682 → noop1 15,534 → noop2 19,446 → noop3 23,358 byteで、1回ごとに+3,912 byte・+375 operatorである。原因は`edit_shared_flow`が既存generated slotを`plan_document_edit`（source paragraphと同じ書き換え）で再編集していたことである。旧glyphを非描画のまま残し、新しいgroupをtext objectに差し込んでいた。所有を証明できるgenerated blockに限り、marker間のbodyを現在のfragmentのcanonical blockへ1つのmutationで置き換えるようにした。no-op 1〜3回のblockは3,682 byteのまま、growと同じbytesになった。marker・slot・creation binding・rebindは変わらない。source slotとlegacy bindingは対象外で、source slot側（合成で+2,052 byte/no-op）の累積は残る。[契約と結果](confirmed-continuation.md#生成fontの寿命)。

**caller workflowからshared flowへの接続（PR #28後、2026-09-30）**: `eb10e3fd2de2161f2110f6033187baa234067160`から、runtime codeを変えずに1本のintegration acceptanceを追加した（[test_continuation_caller_to_shared_flow.py](../tests/test_continuation_caller_to_shared_flow.py)）。6段階のcaller workflowで確認したboundary destinationを、無加工で`confirm_shared_flow`へ渡した。渡した直後はslot未生成で、収まる変更では`new_slots`が空だった。overflowではそのdestinationのslotだけが計画された。`edit_shared_flow`で1回保存して`open_shared_flow`で復元し、論理改行なし・先頭indent 0・callerが選んだboundaryの位置へのblock挿入を確認した。PDFを変更したのは`edit_shared_flow`だけである。contract gapはなかった。

**caller workflowの固定（PR #27後、2026-09-30）**: `d317db61561c99985b53efadefc55690d7acac0e`から、runtime codeを変えずに6段階（inspection → structural review → geometry review → caller decision → confirmation request → explicit confirmation）を正式なcaller導線として固定した。public APIだけを使うacceptance test [test_continuation_caller_workflow.py](../tests/test_continuation_caller_workflow.py)を追加した（正の経路1本と、空き不成立・clip不成立・古いreview・group ID・未選択の5つの負の経路）。docsにはcaller workflow・public API・schemaの一覧をまとめた。書き写し・schema変換・source検証の責務・ID取り違え・reviewとconfirmの間のgapは見つからず、facadeは追加していない。[caller workflow](confirmed-continuation.md#caller-workflow)。

**callerが選んだboundaryのconfirmation request（PR #26後、2026-09-30）**: `294b32c5b8de8ad1ee3058e8f4e74f725fced2db`から、`build_continuation_boundary_confirmation_request(geometry_review, *, boundary_id, destination_id, paragraph_id, region_id)`を`pdfeditor/continuation_review.py`に追加した。geometry review recordと、callerが選んだboundary IDを検証し、`confirm_continuation_destination(source, **request["confirm_kwargs"])`の引数にするpure APIである。boundaryを選ばない。confirmもsourceの再検証もしない（`source_revalidated: false`）。選ばれた候補のgeometryが不成立ならfail closedにする。既存のreview 2 API・`continuation.py`・編集経路は未変更。合成pageのtestsのみ実施し、実PDFの追加検証は不要とした（PDF geometryを新たに解釈しないため）。[契約](confirmed-continuation.md#callerが選んだboundaryのconfirmation-request)。

**geometry reviewの実原本検証（PR #25後、2026-09-30）**: PR #25の`review_continuation_geometry`を、未加工原本の10ページでWindowsにて1回だけ検証し、完了した。HEAD `4890f94081f0fd2b2cf9f84598c64c6e06852835`、engine digest `d3a995b05b7dff246875ebbaa849159675926657f699a9a38304f487b95dec29`、原本SHA-256一致。既知の空き領域`[55,80,385,120]`は空きで、274候補中251件が通過し、ロゴclipの23件だけがclip外で不成立だった（候補固有clip差の自然ケース）。ロゴfill内のnegative controlは全件不成立。どちらでも候補数・group数（274/76）・順序・minimal集合はPR #24どおりで、structural contractは不変。confirm・PDF書き込み・renderは0回。engineは未変更、full suiteは未実行。[結果](../evaluations/continuation/README.md#geometry-reviewpr-24後) / [機械可読summary](../evaluations/continuation/geometry-review-external-summary.json)。

**読み取り専用geometry review（PR #24後、2026-09-30）**: `5eefcdedcbb16b89a09feac6a0ca512bd9b2bfd2`から、`review_continuation_geometry(source, page, bounds)`を`pdfeditor/continuation_review.py`に追加した。callerが指定したboundsについて、空き（既存`require_empty`を1回）と各候補の継承clip適合（既存`clip_contains`）を、既存reviewの候補に注記する。候補の削除・並べ替え・選択・推奨・confirmはしない。生成ink・layout・容量は評価しない。`review_continuation_boundaries`と編集経路は変更していない。合成pageのtestsのみ実施。原本での外部検証は次のWindows PRで行う。[契約](confirmed-continuation.md#読み取り専用のgeometry-review)。

**正式review APIの実原本確認（PR #23後、2026-09-30）**: PR #23で正式read-only API化した`review_continuation_boundaries`・`group_continuation_boundary_candidates`を、PR #23時点では未実行だった未加工原本10ページで、Windowsにて1回だけformal validationした。HEAD `562093a7d9715a777bc0963bdd095fa8f3b9aace`、engine digest `74d61f36c74ed1593c17812cb66b025799fc0b6c2a76e70eab4ae2e658088c4f`、原本SHA-256一致。3,016候補→834 group（singleton 81・複数753・最大6・minimal 896）とpage別countをPR #22どおり再現し、inspector recordのSHA-256も10ページすべてPR #22と一致、11 checksすべてtrue。engine・evaluatorは未変更、full suite未実行。[結果](../evaluations/continuation/README.md#10ページscanboundary_review_formalpy) / [機械可読summary](../evaluations/continuation/boundary-review-formal-summary.json)。

**最新の候補整理（PR #21後、2026-09-30）**: `b2f437a399f2a42d4bb31125ad7c388dc12055a3`から、engineを変更せずread-only grouping prototypeを追加した。同一page・z-order semantics・prefix/suffix paint数で、未加工原本の3,016 safe候補を834 groupへ整理（初期レビュー単位72.35%減、全候補保持）。singleton 81、複数候補753、最大6件。clipなし→compensationなし→浅いq depthの明示比較では776 groupが1件、58 groupは同率を複数保持する。同じpaint positionでもauthorityは交換可能ではなく、安全性順位・自動選択・confirmではない。次段階の読み取り専用group/presentation API候補と判断したが、人のレビュー時間・geometryは未評価。safeはempty確認済みを意味しない。[統計・実例・次のAPI範囲](../evaluations/continuation/README.md#safe-boundaryの描画位置別レビュー--2026-09-30) / [機械可読summary](../evaluations/continuation/boundary-review-summary.json)。

原本・reviewed provider・engine digest `24b7dfb30a79b25e1c0ee7c5bb16a16aedd4de35722d2bf3c738e678648a196a`を照合し、全10ページを各1回走査（inspection計11.798秒、検証・raw保存込み22.406秒）。pure helper tests **40 passed / 1.14秒**。`pdfeditor/`未変更、full suite・external lifecycle・renderer評価は未実行、PDF出力0。PR #21 helperの今後の集計は`text_rendering_mode: 84 / other: 0`とし、historical summaryは変更していない。


**最新の原本調査（PR #20後、2026-09-30）**: 未加工LibreOffice原本の全10ページは最大q depth 2で、自然なdepth 3・depth 4以上の境界は0件だった。focused評価は実施していない。主な拒否はtext object内部・pending pathで、次の検討候補を[全ページ調査](#pr-20後の原本全ページ調査)に記録した。depth 4を自動的な次課題にはしない。

**現行の最大scope depthは3**。PR #19 merge `d5234fe68264adc44918756325746982ab251c5b`から、専用のouter/middle/inner authorityと6 operatorの追跡を追加した。depth 0・1・2の形式、CTM・clip proofは維持する。depth 4以上は拒否し、今回は外部実PDF評価を実施しない。[今回の実装と検証](#depth-3専用scopeの追加)。

**PR #19で解消済みのCTM renderer差**: 十進source operandのexact CTM Sからinverse Nを作り、N×S・N×Mを従来の0.002pt上界で個別に証明する方式へ修正した。端数平行移動の深さ0・1・2でMuPDF・Poppler回帰を確認し、同じLibreOffice実境界のfocused評価でPoppler差は**5,868→0画素**となった。scope/clipの契約は維持し、旧compensated authorityは`needs_confirmation`とする。[数値モデル・修正・新しい評価](../evaluations/continuation/README.md#source-ctm-compensation)。

**PR #18の追加評価（2026-09-27）**: PR #17 engineの深さ2・CTM相殺・矩形clipを加工していないLibreOffice原本で評価した。系列と全状態復帰は通過したが、page-entry対照とのPoppler画素一致は未達（生成1行目5,868画素差）。engineは未変更。[結果・再現条件・原因・修正候補](../evaluations/continuation/README.md#pr-17-engineの深さ2実境界評価--2026-09-27)。以下は各時点の履歴である。

**現状**: 外部原本の系列評価まで完了した。その後、再保存で生成fontが累積する問題を修正し、再評価した。さらに同一ページの複数destinationへ拡張し、その最終engineで、単一destinationの再評価と同一ページ2 destinationの評価を外部原本で完了した（2026-09-25）。その後、writerがtext object内に`q`/`Q`を出していた問題を直し、出力のPDF versionを元PDFと同じにして、両方の外部評価をやり直した。さらに、callerが確認したpage levelの安全なoperator境界を、2つ目の挿入authorityにした。レビュー指摘を受けて、operatorの入れ子が崩れたpage programでは境界候補を出さないようにした（fail closed）。その後、page levelの確認済み境界でCTMだけを1段緩め、逆行列で相殺できるCTMの境界に限りblockをpage座標で描くようにした（合成PDFのみで確認。外部原本の評価はしていない）。結果は「外部原本評価の完了」「生成fontの寿命」「同一ページの複数destination」「PR #8の外部原本評価」「PDF operator nestingの正規化」「確認済みpage-program境界」「page level境界でのCTMの相殺」の節を参照。以下の各節は、その時点の記録として残す。

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
| 外部原本の継続評価 | **未実行**（このsessionの時点）。理由は下記。後にWindowsで実行した（末尾の節） |

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
- **providerの固定**: 実際に使うproviderのfile名・face・SHA-256・variationsを、`evaluations/story_styles/summary.json`の公開証跡と照合する。一致しなければ編集前に停止する。同名fontではなく、以前の評価者が確認したものと同じfont bytesとfaceであることを保証するためである。集計には、実際に使ったproviderと照合元集計のhashを記録する。
- **容量拒否の理由**: 拒否されたことに加え、理由が確認済みregionを使い切ったこと（`paragraphs exceed all explicitly confirmed shared regions`）まで照合する。

### 評価コードのdry-run（証跡ではない）

修正後の評価コードを、合成の7ページPDFで最後まで動かした。置き換えたのは入力（原本・座標・provider）とWindows固定の外部tool pathだけで、段階処理・監査・拒否は評価コードそのものである。全段階と容量拒否が約72〜79秒で通った。期待providerのhashまたはfaceを変えると編集前に停止し、拒否理由を差し替えると最後に停止することも確認した。外部原本の代わりにはならないため、集計にも資料の結果にも使わず、成果物もcommitしていない。

同じdry-runで、生成blockが保存ごとに2,327Bから9,487Bへ増えることを確認した。既存writerは、置き換えた文字のoperatorを削除せず、非描画の`[-1000] TJ`へ書き換えてtext matrixの状態を保つ。この増加はその設計によるもので、source slotの再編集でも同じように起きる。画素・Unicode・照合には影響しないため、変更していない。

## 外部原本評価の完了 — 2026-09-24（`d33d236`）

起点はPR #5のmerge commit `d33d236`。サブエージェントは使用していない。Windows 11 x64（10.0.26200）、Python 3.12.14、lockfileの版で、評価READMEの手順どおりにrun `final-windows`を実行した。

- **開始時の確認**: engine digest `a20828d9…52e0`（44ファイル）と`runner_sha256` `3eb35b38…535b`が一致した。
- **入力**: 原本hashが一致した。取得済みの同一bytesを使い、別版は使っていない。`msmincho.ttc` face 1・`times.ttf`は、評価コードがstory_styles公開集計と照合して一致した。
- **tool**: Poppler 26.07.0と独立pypdf 6.10.0は既定pathに存在し、評価契約とpathを変えずに実行できた。
- **結果**: source no-op、overflow、reopen、second、shorten、regrow、final no-opと容量拒否がすべて通った（1,846秒）。
- **allocation**: overflowは既存slot 209字、生成slot 36字・2行で、以前の計画と一致した。regrowはoverflowと同じallocation・同じ生成slotへ戻った。
- **目視**: 対象画像を確認し、欠陥は見つからなかった。
- **公開**: runの`summary.json`（SHA-256 `6ec6bdd1…6135`）を`evaluations/continuation/summary.json`として公開した。
- **engine**: 評価で問題が見つからなかったため、engine・評価コードは変更していない。全suiteは再実行しておらず、上記の625 passed / 18 skippedは同じengineのLinux上の結果である。

詳細と目視上の注記（原本の和欧間隔を再組版で再現しないこと、生成先が評価者の明示した配置であること）は[評価README](../evaluations/continuation/README.md#pr-6の結果)にある。

### 観測した性質（未変更）

保存ごとに、6ページの生成blockは3,049 → 6,305 → 6,646 → 9,626 → 12,702Bと増えた。PDFも371KBから570KBへ増えた。blockの増加は、置き換えた文字を非描画の`TJ`として残す既存writerの設計による。PDFの増加は主に、保存ごとに新しいsubset fontを埋め込み、以前の生成fontをfileに残すことによる。Type0 fontは4から17個になり、no-op保存でも増える。画素・Unicode・照合には影響しない。性能・容量の最適化は今回の範囲外のため、変更していない。

## 生成fontの寿命 — 2026-09-25（`f7fa600`）

起点はPR #6のmerge commit `f7fa600`。サブエージェントは使用していない。PR #6の外部評価で、生成fontが保存ごとに累積することが分かった（Type0 4→17、PDF 371KB→570KB、no-opでも増加）。

### 原因

実PDFのobject・resource・programで確かめた。

- 保存ごとに、空いている`/PRFn`へ新しいsubsetを追加していた。
- 古いaliasは描画には使われていない。ただし、置き換えた生成glyphの`/PRFn size Tf … Tm`が非描画の数値`TJ`とともにprogramに残り、dormant slotの`[] TJ`挿入文脈とTc/Ts witnessも生成aliasを`Tf`で選ぶ。そのため古いaliasは最終programから参照され続け、font graphは到達可能だった。
- 例えばPR #6のno-opの5ページでは、`/PRF1`〜`/PRF6`が非描画の`Tf`からだけ参照され、描画は`/PRF7`・`/PRF8`だった。
- sidecarには、styleが描くaliasとproviderはあったが、「pdfengineが生成した」ことやsubset hashの記録はなかった。

### 修正

- **`pdfeditor/shared_flow.py`**: 生成fontの所有記録`generated_fonts`をsidecarに持つ。記録を作るのはそのsubsetを書いた保存だけで、open時にpage resourceのbytesと照合する。
- **`pdfeditor/transaction.py`**: 記録のあるaliasだけを再利用候補にする。条件は、そのaliasで描かれるglyphがすべて計画で消費され、保持codeにも使われないことである。commit時に全計画で再検証する。Transactionの元font指紋照合は、置き換えたaliasだけを除外する。
- **`pdfeditor/pdf_save.py`**: page-local辞書のalias先を置き換えるのは、保存直前にFontFile2 hashを再照合できた場合だけである。書込値が同じなら既存objectを残す。旧graphは既存の到達可能性GCで落ちる。
- **`pdfeditor/paragraph.py`**: 予約時に、消費glyph・保持alias・provider identityを渡し、生成fontの記録を返す。
- **engine digest**: `f23c2f08d3e4407180b63d0a888187199ca9d6c765b6d1c7d2cc5eb2c2a0b4c0`（44ファイル）。
- **削除しないもの**: 元PDFのresourceや記録のないfontは削除しない。aliasも削除しない。生成aliasは非描画operatorから参照され続けるため、外すと未定義resourceになる。

### 検証（Windows 11 x64、Python 3.12.14、lockfileの版）

| 検証 | 結果 |
|---|---|
| `tests/test_continuation.py` | 26 passed（426.18秒） |
| `tests/test_generated_fonts.py`（新規） | 6 passed（186.22秒） |
| 全suite `python -m pytest -q` | 649件中642 passed / 7 skipped / 0 failed（2,728.79秒、外部評価と並行） |
| 外部原本 run `font-lifecycle-windows-3` | 全段階・no-op 3回・容量拒否が通過（2,868秒）。runner SHA-256 `dd6d5329…f402` |

上の2つは最終engineの結果である。その直前、docstringだけが異なるengine（`8fa3691a…b1b3`）でも、全suite（642 passed / 7 skipped、2,056.46秒）と外部原本run `-2`が通った。run `-2`と`-3`のPDFはbyte単位で同一だった。

- **新規の回帰**（`tests/test_generated_fonts.py`）:
  - grow → second → shorten → regrow → no-op 3回の各段階で、alias・`/Font`数・Type0数・所有graph数・記録を照合する。
  - secondでは旧subsetが文書から消える。shortenではdormant slotの`Tf`参照とmarkerを保つ。
  - 共有・継承resourceと、記録のない`/PRF1`（pdfengineと同じ構造のType0）を含む原本では、どれも置き換えない。
  - 予約規則を単体で確認する（証跡なし、未消費glyph、保持codeでは再利用しない）。
  - 改ざんした記録ではsidecarを復元しない。再照合失敗・記録作成失敗でPDF・sidecarを公開しない。
  - 再利用を無効にすると、このテストはsecondの段階で失敗する。
- **全suiteの実行条件**: この環境では既定の一時directory（`%TEMP%\pytest-of-agri0`）へ書けないため、`--basetemp`をrepo内の`tmp/`へ指定した。
- **skip 7件**: 未取得の他外部corpus 5件と、AES provider不在2件。Windows fontは揃っているため、以前のfont不在skipはない。
- **外部評価の最初の試行**（run `font-lifecycle-windows`）: 評価コードの監査がsecondで停止した。原因は、置き換えた生成aliasを元fontとして比較した評価側の前提である。除外条件を「直前revisionの記録で所有を証明し、この保存が置き換えたalias」に限って修正し、新しいrun名で再実行した。

### 残る累積と次の障壁

- **fontとresource**: 保存回数に比例して増えない。no-opは新しいfont objectを書かない。
- **page program**: 非描画operatorが保存ごとに一定量（外部原本で展開後+21,784 byte、圧縮後約300 byte）増える。
- **dormant alias**: 直前のsubsetを保持する（aliasごとに1つ）。
- **次の最小の構造障壁**: 置き換えられた生成glyph単位（`Tf … Tm [-n] TJ`）の所有と不使用を証明し、text matrixとmutation map・markerの対応を保ったまま除去する契約である。これが成立すれば、非描画operatorだけが参照する生成aliasも外せる。

## 同一ページの複数destination — 2026-09-25（`be00ece`）

起点はPR #7のmerge commit `be00ece`。サブエージェントは使用していない。「1ページにつき1 destination」の制約を外し、同じpage-entry authorityを、互いに独立した複数の順序付きdestinationへ拡張した。挿入authorityは`before-page-program` / `isolated-pdf-initial-state`のままで、page program途中・既存BT/ET内部・既存graphics stateへの挿入には進んでいない。契約は[同一ページの複数destination](confirmed-continuation.md#同一ページの複数destination)にある。

### 変更

- **`pdfeditor/continuation.py`**:
  - destination契約に`page_entry_order`を加えた。複数destinationのページでは必須・ページ内で一意とする。
  - page-entry chainの検証を加えた。markerが一意であること、blockが連続すること、order順であること、各blockが`q BT ... ET Q`として閉じ、許可したoperatorだけを含むこと、block内の文字とfontがそのslotの所有であること、を確かめる。
  - 新blockの挿入境界の計算と、bytesによる再照合を加えた。
  - marker・mutation mapによるblockの再bindingと、ページ単位の再open検証を加えた。
- **`pdfeditor/mutation.py`**: 明示的な順序を持つ`confirmed-continuation-create`のzero-length insertionに限り、同じoffsetを共有できる。
  - byte順はorderで決まる。`apply` / `position` / `anchor` / `map_offset`、記録、`from_records`はこの順序で定義した。
  - 他のmutationのoverlap・接触の拒否は変えていない。
- **`pdfeditor/paragraph.py`**: 作成mutationを検証済みの境界に置き、orderを付ける。font aliasの予約にowner slotを渡す。
- **`pdfeditor/transaction.py`**:
  - 生成fontのaliasは、記録上の所有slotと同じslotのplanだけが再利用できる。commit時にも照合する。
  - 生成glyphの順序照合は、適用後programでの位置で並べる。
- **`pdfeditor/shared_flow.py`**: 確認・計画・保存後bindingをページ単位のchainで行う。
- **engine digest**: `564da875826429f82fc018f6f856d10af191970f473d4bc0b7c0c481040e44a1`（44ファイル）。

### 検証（Linux x64 container、Python 3.12.3、lockfileの版）

以下は同じ最終engine（上記digest）の結果である。

| 検証 | 結果 |
|---|---|
| `tests/test_multi_destination.py`（新規） | 16 passed（全suite内で370.3秒） |
| `tests/test_mutation.py` | 9 passed（順序付きinsertionの3件を追加） |
| `tests/test_continuation.py` / `tests/test_generated_fonts.py` | 26 / 6 passed |
| 全suite `python -m pytest -q` | 668件中650 passed / 18 skipped / 0 failed（1,422.70秒） |
| 単一destinationの互換 | PR #7のengine（`be00ece`）と最終engineで同じ単一destinationの系列（tracking/rise付き、grow → second → shorten → regrow → no-op）を同じpathで実行した。5保存すべてでPDFとsidecarがbyte単位で一致した |
| 2 destination評価コードのdry-run | 合成原本で全段階・同時生成・容量拒否が通過（約6分）。MuPDF、Poppler 24.02.0、別interpreterのpypdf 6.10.0を使った（[評価README](../evaluations/continuation/README.md#dry-run合成原本外部原本の証跡ではない)）。外部原本の証跡ではない |
| 外部原本 | このsessionでは**未実施**（下記）。後にWindowsで実行した（[PR #8の外部原本評価](#pr-8の外部原本評価--2026-09-254a1220b)） |

- **skip 18件**: すべて環境要因である。Windows font（Arial / Noto Sans JP）不在11、外部corpus未取得5、pypdf AES provider不在2。
- **全suiteの実行条件**: `--junitxml`と`-p no:cacheprovider`だけを加えた。この結果を得る前に、同じengineで途中版の全suite（650 passed / 18 skipped）も通っている。
- **Poppler**: dry-runのため、このcontainerへaptで導入した（`pdftoppm` 24.02.0）。

### 外部原本評価が未実施だった理由（このsession）

- 原本の取得元host（`wiki.documentfoundation.org`）とweb archiveへの接続が、このcontainerのnetwork policyで拒否された（403）。
- 評価provider（`msmincho.ttc` face 1、`times.ttf`）がない。別の取得元・別PDF・代替fontでは実行していない。
- 2 destinationの評価コード[multi_destination.py](../evaluations/continuation/multi_destination.py)は用意した。評価者の分割（確認済み`[55,80,385,120]`内の`[55,80,385,100]`と`[55,101.5,385,120]`、order 10/20）と系列は[評価README](../evaluations/continuation/README.md#同一ページ2-destinationの評価)にある。Windows検証環境で実行する。

### 次の最小の構造障壁

page-program先頭以外のcontent-stream境界へ、独立した挿入authorityを与えることである。page-entryでは初期graphics stateが仕様で決まり、各blockがそれを閉じるだけで状態を証明できた。途中の境界では、その位置で有効なCTM・clip・ExtGState・text stateと、後続paintとの順序を、その境界ごとの証跡として確認する必要がある。

## PR #8の外部原本評価 — 2026-09-25（`4a1220b`）

起点はPR #8のmerge commit `4a1220b`。サブエージェントは使用していない。新しい構造機能は実装していない。PR #8の最終engineを、評価済みの外部LibreOffice PDFと確認済みWindows fontで評価した。engine（`pdfeditor/*.py`）と評価コードは変更していない。

### 開始時の確認

| 項目 | 結果 |
|---|---|
| HEAD | `4a1220b048c0826394a068e3b646f27963b11770`。作業ツリーはclean |
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、PyMuPDF 1.27.2.3、pypdf 6.10.0 |
| engine digest | `564da875826429f82fc018f6f856d10af191970f473d4bc0b7c0c481040e44a1`（44ファイル）。PR #8の値と一致 |
| 評価コード | `evaluate.py` `dd6d5329…f402`、`multi_destination.py` `7c01283e…a0df` |
| 原本 | `lo_migration_ja.pdf`、SHA-256 `13665875…a5f3`で一致。ローカルにある取得済みのbytesを使った |
| provider | body `msmincho.ttc` face 1（`ceb8d745…44c2`）、latin `times.ttf` face 0（`931c5de5…58c5`）。評価コードがstory_styles公開集計（`c2329afa…c7c2`）と照合して一致 |
| 独立tool | Poppler `pdftoppm` 26.07.0、独立pypdf 6.10.0（Python 3.12.14）。既定pathのまま |

### 結果

| 評価 | run | 結果 |
|---|---|---|
| 単一destination | `pr8-single-windows` | 全段階・no-op 3回・容量拒否が通過（3,927秒）。7保存のPDF・sidecarが、PR #7 engineのrun `font-lifecycle-windows-3`とbyte単位で一致 |
| 同一ページ2 destination | `multi-pr8-windows` | 逐次8段階・同時2段階・容量拒否が通過（3,622秒）。`status = passed` |

- **単一destinationの後方互換**: destination・bindingに`page_entry_order`がない。作成mutationはoffset 0で順序を持たない従来形式のままだった。allocationは既存slot 209字、生成slot 36字・2行で、以前と同じである。
- **2 destination**:
  - a-onlyでは`page6-a`だけが生成された。b-addedでは`page6-b`が`page6-a`の終端へ加わった（order 10 → 20）。
  - secondは既存の2 blockを再利用した。shortenでは`page6-b`だけがdormantになり、regrowで同じslot・blockへ戻った。
  - no-op 3回で、順序・identity・作成証跡・allocation・計画glyph・font/resource数・画素が変わらなかった。
  - 同時生成（both）はoffset 0の同位置ordered insertionで同じchainになり、逐次生成とallocation・chain順序・所有が一致した。
- **監査**: MuPDF・Poppler・独立pypdfの監査がすべて通った。Popplerの変更は、各region（1pt余白込み）の外で全保存0画素だった。region間の1.5ptも含む。
- **容量拒否**: 理由は`paragraphs exceed all explicitly confirmed shared regions`で、PDF・sidecarは作られなかった。
- **目視**: 両runの対象画像を確認し、欠陥はなかった。2 destinationの6ページの画像は、単一destinationの対応する画像とbyte単位で同じだった。
- **公開**:
  - 単一destinationのrunの集計を`summary.json`として公開した。以前の集計との違いはengine digestとengine hashだけである。
  - 2 destinationのrunの集計を`multi-destination-summary.json`として公開した。
- **engine**: 問題が見つからなかったため変更していない。engineを変えていないので、全suiteは再実行していない。PR #8の650 passed / 18 skippedは、同じengineのLinux上の結果である。

詳細は[評価README](../evaluations/continuation/README.md#pr-8-engineでの単一destination再評価)と[2 destinationの結果](../evaluations/continuation/README.md#外部原本の結果)にある。

### 観測した性質（未変更）

- **text object内のq/Q**: 生成blockは`q BT q 0 Tc 0 Tw 0 Ts … Q ET Q`の形で、text object内に`q`/`Q`を含む。
  - これはsource slotの再編集と共用するglyph writer（`paragraph.py`）に由来する。再編集した4・5ページでも、既存のtext object内に同じ`q … Q`が入る。原本にはない。PR #8以前からの性質である。
  - ISO 32000-1の図9（graphics objects）では、text object内で使えるoperatorに特殊graphics state（`q`/`Q`/`cm`）は含まれない。
  - MuPDF・Poppler・pypdfは受理し、画素・抽出・照合に影響はなかった。このsessionでは変更していない。
  - 後に修正した（[PDF operator nestingの正規化](#pdf-operator-nestingの正規化--2026-09-25c4ea5fb)）。
- **生成blockの増加**: 置き換えた文字の非描画operatorにより、生成blockは保存ごとに増える（`page6-a`は1,419 → 19,537 byte）。単一destinationと同じ既存writerの性質である。

### 次の最小の構造障壁

変わらない。page-program先頭以外のcontent-stream境界へ、独立した挿入authorityを与えることである。途中の境界では、その位置で有効なCTM・clip・ExtGState・text stateと、後続paintとの順序を、境界ごとの証跡として確認する必要がある。

## PDF operator nestingの正規化 — 2026-09-25（`c4ea5fb`）

起点はPR #9のmerge commit `c4ea5fb`。サブエージェントは使用していない。新しい編集機能は加えていない。PR #9の外部評価で観測した「text object内の`q`/`Q`」を修正した。pdfengineが生成・再編集するcontent streamを、出力PDFのversionのoperator nesting規則に合わせた。page-program先頭以外の挿入authorityには進んでいない。契約は[PDF 1.xのoperator nesting](confirmed-continuation.md#pdf-1xのoperator-nesting)にある。

### 開始時の確認

| 項目 | 結果 |
|---|---|
| HEAD | `c4ea5fb65f2bc60bb1e835a8fe82ed988521dcc8`。作業ツリーはclean |
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、PyMuPDF 1.27.2.3、pypdf 6.10.0 |
| engine digest（開始時） | `564da875…44a1`（44ファイル）。PR #8・#9と同じ |
| 外部原本のversion | `%PDF-1.4`（catalogに`/Version`なし）。4〜6ページはPDF 1.xの入れ子規則を満たす（text object 56・83・85、違反0） |
| 保存後のversion | 常に`%PDF-1.3`。pypdfの`PdfWriter(clone_from=...)`が既定のheaderを書くためで、1.4の透明度groupを持つ原本でも1.3に下がっていた |
| 意図するversion | 元PDFと同じ。この変更でheaderを保つようにした |

### 仕様上の契約

- **PDF 1.x**: PDF Reference 1.4の4.1節Figure 4.1（ISO 32000-1の8.2節Figure 9も同じ）で、text object内に置けるのは一般graphics state・色・text state・text位置・text表示・marked contentのoperatorである。特殊graphics state（`q`・`Q`・`cm`）はpage記述レベルだけに置ける。marked-content sequenceとtext objectはそれぞれ正しく入れ子にする（PDF Reference 9.5節）。
- **PDF 2.0**: ISO 32000-2（errata適用後）では、text object内の`q`/`Q`も許され、そこでは`Tm`/`Tlm`も保存・復元される。
- **選択**: pdfengineは元のversionを保つため、1.xの形で書く。versionを上げて現在のbytesを通す方法は取らない。rendererの受理は仕様の代わりにしない。

### root cause

paragraph writer（`paragraph.py`）は、新しい文字のtext state（font・size・Tz・Tc・Tw・Ts・fill）を`q ... Q`で隔離し、選択した最初のtext operatorの直後、つまり既存text objectの内側へ挿入していた。

- **source paragraphの再編集**: `BT ... [書き換えたoperator] q ... Q Tm TJ ... ET`になっていた。`Q`の後の`Tm`と`TJ`は、PDF 1.xの`q`/`Q`が保存しない`Tm`/`Tlm`を明示的に戻していた。
- **生成block**: 同じglyph writerの出力を包み、`q BT q ... Q ET Q`になっていた。
- **dormant styleのwitness**: `q Tf Tz Tc Ts Tm [] TJ Q`をtext object内に置いていた。
- **同じ原因の他のwriter**: `compose_selected`（`q ... Q`）、`edit_reflow`（`q ... Q`）、要素の平行移動（text operatorを`q 1 0 0 1 dx dy cm ... Q`で包む）も同じだった。pathを包む`q ... Q`（下線・path移動・paint resize）はpage記述レベルにあり、規則に合っている。
- **調査**: このほかにpdfengineがtext object内へ出すoperatorは`Tf`・`Tz`・`Tc`・`Tw`・`Ts`・`Tm`・`Tj`・`TJ`・`g`・`rg`・`k`で、どれも1.xでtext object内に置ける。

### 変更

- **`pdfeditor/operator_nesting.py`**（新規）: 既存の`operators()`を使う狭い検査。
  - `audit()`: text objectの入れ子、`q`/`Q`の対応、text object内の特殊graphics stateとpage記述レベル専用のoperator、marked contentとtext objectの交差を報告する。
  - `text_object_split()` / `require_text_object_split()`: 編集位置でtext objectを閉じて開き直せるかを判定する。そのtext object内で開いた`q`・marked content・`BX`が開いたままの場合と、clipping描画モード（Tr 4〜7）の文字がある場合は拒否する。
- **`pdfeditor/paragraph.py`**:
  - source再編集では、書き換えたoperatorの後に`ET`、新しい文字と各witnessを独立した`q BT ... ET Q`、`BT`、元のline matrixの`Tm`と数値`TJ`を置く。
  - 生成blockは`q BT ... ET Q`にした。
- **`pdfeditor/composition.py`**: 同じ分割にした。
- **`pdfeditor/explicit_reflow.py`**: 状態を変えない`q`/`Q`を除いた。挿入が直前のoperatorに連結しないよう、先頭に空白を置いた。
- **`pdfeditor/elements.py`**: text-moveを`ET q cm BT <復元> op ET Q BT <復元>`にした。`'`/`"`は、残したoperatorが`T*`を行うため1行上の行列から開き直す。
- **`pdfeditor/continuation.py`**:
  - blockの検証をoperatorの構造で行う。外側の`q ... Q`はblockの最後でだけ閉じる。text objectの入れ子がない。`Tm`/`Tj`/`TJ`はtext object内だけにある。text object内に`q`/`Q`はない。
  - 新しいbindingは`operator_nesting = pdf-1.x-text-objects`を記録する。記録のない旧bindingのblockは旧規則で検証する。
- **`pdfeditor/pdf_save.py`**: 元PDFのheader（version）を保つ。
- **engine digest**: `341859e13035bfd6a85f04b33f7fe0c7f95b708cf97b8b13548db771d8f079e4`（45ファイル）。

### text / line matrixの復元

- **状態の復元**: 分割の直前、source text objectの中で書き換えたoperatorを実行する。text表示operatorはgraphics stateを変えない。そのため`q`の時点の状態は、選択した最初のoperatorの状態と同じである。`Q`は、font・size・Tc・Tw・Tz・TL・Ts・Tr・fill/strokeの色と色空間・CTM・clip・ExtGStateをそのまま戻す。
- **Tm / Tlmの復元**: 開き直した`BT`は`Tm`と`Tlm`だけを単位行列にする。直後の`Tm`で両方を元のline matrixにし、数値だけの`TJ`で`Tm`を元のoperatorの後の位置へ進める。
  - `TJ`の数値は`Q`で戻った元のsizeとTzで換算する。`Tc`/`Tw`は数値だけの`TJ`に影響しない。
  - `'`と`"`は、書き換えたoperatorの中で`T*`を実行した後の行列を使う。
- **確認**: 合成PDFの回帰（`test_source_rewrite_isolates_new_text_and_keeps_following_cursor_and_state`）で、非既定のTc・Tw・Tz・Ts・TL・fill・strokeの後に編集し、後続のoperatorを比べた。対象は`Tj`・`Td`・`T*`・`'`・`"`・`TJ`で、次がすべて一致した。
  - text matrix・line matrix。
  - font・size・Tc・Tw・Tz・Ts・TL・Tr・色・CTM。
  - code、glyph origin、trace上の観測。
- **精度**: 復元する`TJ`は12桁で書くため、位置は約1e-5 pt以内で一致する。以前のwriterと同じ方法である。外部原本では、全保存の監査画像が以前のengineの画像と同じbytesだった。

### 旧形式の出力

実際に、PR #9のengine（`c4ea5fb`のworktree）で合成PDFのshared flowを保存した。その出力（`fits`・`grow`・`shorten`）を最終engineで扱った。

- **検査**: どれもtext object内の`q`/`Q`で違反した。source slotのページと生成blockの両方である。
- **open**: `open_shared_flow`は`restored`になった。bindingに記録がないため、blockを旧規則で検証する。
- **再保存**: no-op・再編集とも、`cannot isolate new text: a graphics-state save ... opened inside this text object is still open`で拒否し、PDF・sidecarを公開しなかった。
- **所有の証明**:
  - 生成blockの`q`/`Q`は、marker対・binding・block hash・作成証跡でpdfengineの所有を証明できる。
  - source slotの再編集で入った`q`/`Q`は、sidecarに対応する記録（mutation mapなど）が保存されていない。byteの形から推測することはしない。
  - shared flowの保存はparagraphのすべてのslotを書き直す。そのため、blockだけを正規化しても保存は成立しない。
  - 旧形式の書き換え（migration）は行わず、旧形式はopen専用とした。準拠した出力が必要なら元PDFから編集し直す。この変更後のengineが元PDFから作る出力は、何回保存しても規則を満たす。

### 検証（Windows 11 x64、Python 3.12.14、lockfileの版）

以下はすべて最終engine（digest `341859e1…79e4`）の結果である。

| 検証 | 結果 |
|---|---|
| `tests/test_operator_nesting.py`（新規） | 30 passed |
| `tests/test_multi_destination.py`・`test_mutation.py`・`test_continuation.py`・`test_generated_fonts.py`と上記 | 87件中86 passed（1,763.58秒）。失敗1件は、単一destinationのbindingのkey集合を固定する試験で、`operator_nesting`が加わったためである。期待値を更新し、追加した改ざんvariantとともに再実行して通った |
| 全suite `python -m pytest -q` | 698件中691 passed / 7 skipped / 0 failed（5,239.87秒、1回の実行） |
| 外部原本 単一destination（run `nesting-single-windows`） | 全段階・no-op 3回・容量拒否が通過（4,222.67秒） |
| 外部原本 同一ページ2 destination（run `multi-nesting-windows`） | 逐次8段階・同時2段階・容量拒否が通過（4,515.36秒）。`simultaneous_equals_sequential = true` |

- **全suiteの条件**: 既定の一時directoryへ書けないため、`--basetemp`をrepo内の`tmp/`にした。`--junitxml`と`-p no:cacheprovider`を加えた。
- **skip 7件**: 未取得の外部corpus 5件と、AES provider不在2件で、どれも環境要因である。
- **試験の追加**（`tests/test_operator_nesting.py`）:
  - 検査の単体試験: 違反の種類と、分割できる位置・できない位置。
  - source paragraphの再編集: 後続のoperatorのcursor・状態・glyphが変わらない（前節）。
  - 分割できない場合の拒否: text object内で開いた`q`、`BDC`、clip文字。いずれも何も公開しない。
  - dormant styleのwitness: 独立した`q BT ... ET Q`の中にある。
  - PDF version: `%PDF-1.4`・`%PDF-1.7`の原本からの保存で、versionが変わらない。
  - `compose_selected`（`Tj`・`'`・`"`）と要素の平行移動の出力が規則を満たし、後続の文字が動かない。
  - blockの検証: 新形式、旧形式、閉じ方の誤り。
- **lifecycle試験への組み込み**: `test_continuation.py`と`test_multi_destination.py`の保存helperは、保存した全revisionについて次を確かめる。
  - 全ページが規則を満たす。
  - PDF versionが変わらない。
  - 生成blockのbindingが`operator_nesting`を記録する。
  - これにより、次の既存試験が入れ子の回帰を兼ねる: 4 alignmentの系列、tracking/rise、shorten→dormant→regrow、2 destination（同時・逐次・逆順、3 block）、no-op 3回、生成fontの寿命。
  - `test_multi_destination.py`の改ざん試験には、block内のtext objectへ`q`/`Q`を入れたvariantを加えた。
- **補助の走査**: 試験が残したPDF 878個を`audit()`で走査した。違反があったのは次だけで、pdfengineのwriterの出力にはなかった。
  - 意図的に不正な試験原本。
  - `tests/test_mutation.py`がmutation mapの試験のために手書きした入力。
- **外部原本**: 評価コードは、各保存で編集した4〜6ページの入れ子とPDF versionを照合し、集計に記録する。詳細は[評価README](../evaluations/continuation/README.md#operator-nesting正規化後の再評価)にある。
  - 原本・provider・Poppler・独立pypdfは前回と同じで、照合も一致した。
  - **両run**: 全保存で入れ子の違反は0で、出力は`%PDF-1.4`だった。前回まで出力は`%PDF-1.3`だった。
  - **単一destination**: allocation（209字＋36字・2行）、生成slot ID、font/resource数、aliasごとのfont出力は前回と同じだった。no-op 3回で全10ページの画素と計画glyph 245個が一致し、容量拒否の理由も一致した。
  - **2 destination**: 次が前回と同じだった。
    - 有効destination・新block・allocation・font出力。
    - 作成位置。`page6-b`は直前revisionの`page6-a`の終端（1,414）に作られた。
    - chain順序（10 → 20）。
    - 同時と逐次の一致。
    - 各region外（region間の1.5ptを含む）の差分0。
  - **画像**: 監査画像は、単一の84枚、2 destinationの116枚がすべて、前回のrunの画像とPNGのbytesまで一致した。writerの構造は変わったが、描画は1画素も変わっていない。PDF・sidecarのbytesは、構造が変わったため一致しない。
- **目視**: 次の切出しを確認した。どれも前回の切出しとbytesまで同じで、位置のずれ、region間の隙間への侵入、dormantの`page6-b`の文字残りはなかった。
  - 4・5ページのsource再編集、6ページの生成先、shorten、regrow（300dpi）。
  - 2 destinationの隙間（600dpi）。
- **公開**: 2つのrunの集計を`summary.json`と`multi-destination-summary.json`として公開した。

### 残る問題

- **text knockout**: 分割はtext objectを増やす。透明度groupのtext knockout（ExtGState `/TK`）では、同じtext object内の文字同士の扱いが変わりうる。新しい文字は保護検査で既存の文字と重ならないため、pdfengineの出力では差が出ない。この点は明示的には扱っていない。
- **分割の拒否**: 次のsourceでは、以前は編集できた位置を、今回から拒否する。
  - text object内で`q`や`BDC`を開いたままの位置（非準拠のsourceやtagged PDFの一部）。
  - clip文字を含むtext object。
- **test_mutationの入力**: `tests/test_mutation.py`はmutation mapの単体試験のために、text object内の`q ... cm ... Q`を手書きで作る。writerの出力ではないので変更していない。
- **生成blockの増加**: 非描画operatorの累積は変わらない。分割のため、text objectの数も保存ごとに2つ増える。

### 次の最小の構造障壁

変わらない。page-program先頭以外のcontent-stream境界へ、独立した挿入authorityを与えることである。その境界で有効なCTM・clip・ExtGState・text stateと後続paintとの順序を、境界ごとの証跡として確認する必要がある。今回の分割は、既存text objectの中で新しい文字を隔離する位置を1.xに合わせたもので、任意の境界への挿入権限ではない。

## 確認済みpage-program境界 — 2026-09-25（`ed0cb92`）

起点はPR #10のmerge commit `ed0cb92`。サブエージェントは使用していない。continuationの挿入authorityを、page entry（offset 0）に加えて、callerが明示的に確認したpage levelの安全なoperator境界へ広げた。契約は[確認済みpage-program境界](confirmed-continuation.md#確認済みpage-program境界)にある。任意のbyte offset、`q`の内側、有効なclipの下、identity以外のCTM、任意のExtGState、text object・marked content・Form XObjectの内側には進んでいない。

### 開始時の確認

| 項目 | 結果 |
|---|---|
| HEAD | `ed0cb92d3191a83611e7af6cd0852fa7fa99db43`。作業ツリーはclean |
| engine digest（開始時） | `341859e1…79e4`（45ファイル）。PR #10の値と一致 |
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、lockfileの版 |

### 変更

- **`pdfeditor/content_stream.py`**: `_walk`はtop-level page programのoperatorごとに、その直後の`State`とscopeを`Boundary`として記録する。scopeは`q`の深さ、text object、marked-contentと`BX`の深さ、組み立て中のpath、未適用のclipである。解釈は従来の1つだけで、記録を加えただけである。
- **`pdfeditor/continuation.py`**:
  - `inspect_continuation_boundaries()`で、page levelの境界を列挙し、安全性を判定する。
  - `confirm_continuation_destination(..., insertion='confirmed-page-program-boundary', graphics_state='confirmed-boundary-state', boundary=...)`で、候補を1つ確認する。
  - 境界authorityをrevisionごとに検証する（`_boundary_value`）。
  - `chain()`はpage-entryの順序付けだけに使い、境界destinationは別に並べる。
  - page-entryの検証、binding形式、snapshotは変えていない。
- **`pdfeditor/shared_flow.py`**: 未使用の境界を、保存ごとのmutation mapで写す。
- **`pdfeditor/paragraph.py`**: 境界の作成mutationは順序を持たない。
- **`pdfeditor/mutation.py`**: 変更なし。
- **engine digest**: `fca1e014164c93c6c62b1aad4344d1184760bd5e8f98404b44a453674ee0a731`（45ファイル）。

### LibreOffice原本の検査

`inspect_continuation_boundaries`で10ページすべてを調べた。

- **候補**: 各ページの候補は2つだけだった。
  - 先頭の`0.1 w`の直後。prefixに描画がない。
  - 本文のtop-level `q ... Q`とCC-BY-SAロゴのtop-level `q ... Q`の間。
- **拒否**: 残りの境界は、`q`の内側・有効なclip・text object・組み立て中のpath/clip・描画モードのいずれかで拒否した。
- **採用した境界**: 6ページでprefixとsuffixの両方に描画がある候補は1つだった（`boundary-b848698b464255ff0b2b6f90`）。
  - offsetは17602で、`Q`（序数1303）の直後、`q`（序数1304）の直前にある。
  - prefixの描画operatorは286、suffixは15（ロゴ）である。
  - 状態はCTM identity、clipなし、stroke専用の`w 0.1`だけである。
  - 評価者はこれを確認し、IDと証跡を評価コードに固定した。
  - 空き領域は、既に確認済みの`[55,80,385,120]`をそのまま使った。

### 検証（Windows 11 x64、Python 3.12.14）

以下は、PR作成時のengine（digest `fca1e014…a731`）の結果である。レビュー指摘の修正後の再検証は、[下記](#レビュー指摘-入れ子が崩れたprogramのfail-closedpr-11)にある。

| 検証 | 結果 |
|---|---|
| `tests/test_boundary_destination.py`（新規） | 27 passed |
| `test_continuation.py`・`test_multi_destination.py`・`test_operator_nesting.py`・`test_mutation.py`・`test_generated_fonts.py` | 87 passed（1,429.75秒） |
| 外部原本 単一destination（run `boundary-single-windows`） | 全段階・no-op 3回・容量拒否が通過（2,923.23秒）。集計はengine digest以外PR #10と同じ。7保存のPDF・sidecarはPR #10のrun `nesting-single-windows`とbyte単位で一致 |
| 外部原本 確認済み境界（run `boundary-windows`） | 全段階・no-op 3回・容量拒否が通過（2,940.79秒）。page-entry runと、全段階の計画glyphとPoppler画像が一致 |
| 外部原本 同一ページ2 destination（run `multi-boundary-windows`） | 逐次8段階・同時2段階・容量拒否が通過（4,655.47秒）。集計はengine digest以外PR #10と同じ。PDF・sidecar・記録57件がPR #10のrun `multi-nesting-windows`とbyte単位で一致 |
| 全suite `python -m pytest -q` | 718 passed, 7 skipped（4,676.96秒） |

- **新しい試験**（`tests/test_boundary_destination.py`）:
  - A. 候補の列挙: 安全な境界だけが候補になる。拒否理由は、text object・`q`・marked content・`BX`・path・未適用のclip・CTM・clip・ExtGState・`ri`・Trである。
  - B. callerの明示確認と、誤ったIDや拒否された境界の拒否。geometryは境界を選ばない。
  - C〜I. 非zero offsetでの作成・reopen・second・shorten・regrow・no-op 3回。prefix/block/suffixの順序、描画operatorの順序、作成証跡、生成font、画素を確かめる。
  - J・K. 同じtransactionでの、境界より前・後のsource slotの変更。prefixが伸びるとblockはそれに合わせて移り、suffixの変更ではoffsetが変わらない。
  - L. 境界に触れるmutationの拒否。直後operatorが変わると証跡で拒否する。
  - M. sidecar（ID・証跡・状態・scope・offset・近傍）とprogram（marker・block位置・近傍operator）の改ざんの拒否。
  - 同じoffsetでのCTM・clip・ExtGState・`q`・marked contentの改ざんの拒否。
  - N. page entryとの共存と、生成fontの分離。
  - O. late failureのrollback。
  - page entryと境界で、同じ文字・同じ画素になること。
- **PR #10形式の互換**: PR #10のengine（`ed0cb92`のworktree）で作った出力を、最終engineで扱った。対象は単一destinationのdormant状態と、2 destinationのchainである。
  - openはrestoredになった。no-op保存は全aliasが`reused`で成功した。
  - page-entryの編集（regrow・shorten）も成功した。
  - 各revisionで、入れ子・version・bindingの記録を確かめた。
- **補助確認**: 一時スクリプト（commitしていない）で、境界runの保存済み成果物を読み直した。
  - pypdfの分解器で、blockの構造、ページの入れ子、block直前・直後の`Q`・`q`を確かめた。
  - blockだけを描いたページのインクが確認済み領域の内側にあること、shortenで描画しないこと、blockの文字がslotと一致することを確かめた。
- **目視**: 境界runの4〜6ページの300dpi切出しは、page entryのrunの切出しとbytesまで同じだった。6ページでは、2行が本文の後・ロゴの前に確認済み領域内で描かれ、ロゴ・本文に変化はなかった。

### 残る未対応の状態

次の境界は候補にならない。

- `q`の内側
- 有効なclipの下
- identity以外のCTM
- ExtGState・`ri`・`i`
- text object・marked content・`BX ... EX`の内側
- 組み立て中のpathや未適用のclip

1つの境界に複数のdestinationを順序付きで入れることも、まだ扱っていない。

### 次の最小の構造障壁

page entryと同じ状態を証明できない境界である。identity以外のCTM、有効なclip、ExtGStateを持つ境界を、どこまで安全に扱えるかを示す必要がある。例えば、CTMの逆変換で同じpage座標に描けること、destinationがclipの内側に収まることの証明である。

### レビュー指摘: 入れ子が崩れたprogramのfail closed（PR #11）

- **問題**: `ContentPage._walk`はtext objectの内外をboolで数える。そのため、構造が不正なprogramで、text objectの外に見える境界を安全な候補として出していた。
  - 入れ子の`BT ... BT ... ET`: 内側の`ET`で外に出たと数える。
  - `BT`のない`ET`、閉じていない`BT`: 数え方は崩れないが、programは不正である。
  - 修正前は、この3例で`0 0 5 5 re f`の直後などが`safe`になった。
- **変更**（`pdfeditor/continuation.py`だけ）: 既存の`operator_nesting.audit`をpage program全体に使う。scopeの数え方を二重に実装しない。
  - 違反が1つでもあれば、`inspect_continuation_boundaries()`はそのページのすべての境界を`invalid-operator-nesting`で拒否する。候補は出ない。確認もできない。
  - revisionごとの境界の検証（`_boundary_value`）も同じ監査を行う。前後のoperator・scope・状態が同じでも、入れ子が崩れていれば拒否する。
  - `ContentPage`・MutationProgram・page entry・binding形式は変えていない。
- **LibreOffice原本**: 10ページすべてで監査の違反は0だった。候補は修正前と同じ各2つで、6ページの確認済み境界`boundary-b848698b464255ff0b2b6f90`もそのまま候補に残る。
- **追加試験**（`tests/test_boundary_destination.py`、6件）:
  - `test_a_program_whose_operators_do_not_nest_has_no_candidates`: 入れ子の`BT`・`BT`のない`ET`・閉じていない`BT`の3例で、候補が0件になる。すべての境界に`invalid-operator-nesting`が付く。それ以外の条件は満たす境界の拒否理由が`invalid-operator-nesting`だけであり、その境界は確認もできない。
  - `test_a_revision_whose_operators_do_not_nest_is_not_the_same_authority`: 生成済みrevisionのprogramを同じ長さで改ざんし、同じ3例にする。境界の前後・scope・状態は同じままでも、証跡の検証で拒否し、openは`needs_confirmation`になる。
  - 6件とも、修正前のengineでは失敗し、修正後は成功した。

#### 修正後の検証（Windows 11 x64、Python 3.12.14）

以下はすべて修正後の最終engine（digest `59fe6125948d44a732da8c22a18cd619cdc3a34f7d6599c6141250144e481bfd`、45ファイル）の結果である。外部評価と試験を並行して実行したため、時間は単独実行より長い。

| 検証 | 結果 |
|---|---|
| `tests/test_boundary_destination.py` | 33 passed（842.89秒）。修正前の27件と追加6件 |
| `test_continuation.py`・`test_multi_destination.py`・`test_operator_nesting.py`・`test_mutation.py`・`test_generated_fonts.py` | 87 passed（4,408.76秒） |
| 全suite `python -m pytest -q` | 724 passed, 7 skipped（7,199.91秒）。失敗・エラー0 |
| 外部原本 単一destination（run `failclosed-single-windows`） | 全段階・no-op 3回・容量拒否が通過（5,628.55秒）。集計はengine digestと`continuation.py`のhash以外、修正前の公開集計と同じ。成果物129件（PDF・sidecar・記録・画像）が、run `boundary-single-windows`とbyte単位で一致 |
| 外部原本 確認済み境界（run `boundary-failclosed-windows`） | 全段階・no-op 3回・容量拒否が通過（5,609.44秒）。page-entry run `failclosed-single-windows`と、計画glyph・Poppler画像が一致。集計はengine digest・hash・比較runの名前以外同じ。成果物129件がrun `boundary-windows`とbyte単位で一致 |
| 外部原本 同一ページ2 destination（run `multi-failclosed-windows`） | 逐次8段階・同時2段階・容量拒否が通過（3,787.93秒）。集計はengine digestと`continuation.py`のhash以外、修正前の公開集計と同じ。成果物177件がrun `multi-boundary-windows`とbyte単位で一致 |

- **公開集計**: 3つのrunの集計を`summary.json`・`boundary-destination-summary.json`・`multi-destination-summary.json`として公開した。
- **外部評価の結果**: 修正前と変わらない。LibreOffice原本は入れ子の監査に違反がなく、確認済み境界もそのまま候補に残るためである。

## page level境界でのCTMの相殺 — 2026-09-25（`c2a62e2`）

起点はPR #11のmerge commit `c2a62e2`。サブエージェントは使用していない。confirmed page-program boundaryに残る制約のうち、CTMだけを1段緩めた。`q`の深さ0・clipなし・ExtGStateなし等の条件は維持したまま、境界のCTMが有限で、逆行列で安全に相殺できることを証明できる場合に限り、continuation destinationを許可する。契約は[identity以外のCTMの相殺](confirmed-continuation.md#identity以外のctmの相殺)にある。identity以外のCTMを一般に扱えるようにしたものではない。

### 開始時の確認

| 項目 | 結果 |
|---|---|
| HEAD | `c2a62e268978b95a3a6c092307b29fa0b0e1130e`（PR #11のmerge）。作業ツリーはclean |
| engine digest（開始時） | `59fe6125…81bfd`（45ファイル）。PR #11の最終engineと一致 |
| 環境 | Linux（クラウド環境）、Python 3.12.3、lockfileの版（PyMuPDF 1.27.2.3、pypdf 6.10.0ほか） |

### 方式

```text
existing prefix              CTM = M
q
  N cm                       N = Mの逆行列（authorityに記録した値）
  BT ... generated text ... ET
Q
existing suffix              CTM = M
```

- **相殺**: blockの`q`の直後、text objectの外に`N cm`を1回だけ置く。block内はpage entryと同じ座標になり、writerはpage-entryと同じglyph位置・Tmを書く。blockの`Q`がMを戻すので、suffixへ相殺は漏れない。
- **identityの境界**: 従来どおり`q BT ... ET Q`。authority（`ctm_compensation`を持たない）もblockも変えていない。
- **証明**（`compensation()`）: Mが有限で、行列式が0でない（有理数で厳密に計算）。Nは厳密な逆行列を有効数字12桁に丸め、指数表記なしで書く。page box（CropBox）の4隅で、pとp·N·Mの距離の上界（厳密な残差と、binary32で8段の丸めの上界）が0.002以下。Mには、interpreterのCTM（MuPDFと同じbinary32合成）と、operandから厳密に合成したCTMの両方を使う。
- **拒否**: `nonfinite-ctm`、`singular-ctm`、`numerically-unstable-ctm`（値の範囲外か上界超過）。従来の`nonidentity-ctm`はなくなった。
- **authority**: `ctm_compensation`に、confirmed CTM・逆行列（書くoperandと`N cm`のbytes）・policy（`inverse-ctm-inside-block-save`）・証明を記録する。`isolation = q-cm-BT-ET-Q`。boundary ID・前後operatorの証跡・scope・state・source program・mutation mapによるrebindは従来どおり。
- **再検証**: open・保存のたびに、境界のCTMとprogramのoperandから`ctm_compensation`全体を再導出して記録と比べる。blockの`cm`は、記録した`operator`とbytesが同じものが1つだけ。block内の文字のCTMは、MにNを合成した値。blockの`q ... Q`は末尾でだけ閉じるので、`Q`の直後はCTMがM、`q`の深さ0に戻る（試験でも直接確かめる）。

### 変更

- **`pdfeditor/continuation.py`**:
  - `compensation()`・`source_ctms()`・`block_ctm()`を加えた。
  - 候補の列挙（`_inspect`）と確認（`_boundary_authority`）で、identity以外のCTMの境界に`ctm_compensation`を付ける。相殺を証明できない場合は拒否理由にする。
  - revisionごとの検証（`_boundary_value`）で相殺を再導出する。`_block`は、記録した`N cm`だけを`q`の直後に認める。`_validate_destination`は、文字のCTMを`block_ctm()`と比べる。
- **`pdfeditor/paragraph.py`**: 作成blockの先頭を、相殺があるときだけ`q N cm BT`にする。
- **変えていないもの**: `content_stream.py`（interpreter）、`mutation.py`、`shared_flow.py`、page-entryの検証・binding形式、生成fontの寿命。
- **engine digest**: `383bbd49fddacba074b54d676f468d032c9f27a249af47af56c07a653bb55262`（45ファイル）。

### 検証（Linux、Python 3.12.3）

| 検証 | 結果 |
|---|---|
| `tests/test_ctm_compensation.py`（新規） | 39 passed |
| `tests/test_boundary_destination.py` | 36 passed。PR #11の33件と、拒否例の追加3件 |
| `test_continuation.py`・`test_multi_destination.py`・`test_operator_nesting.py`・`test_mutation.py`・`test_generated_fonts.py` | 87 passed（PR #11と同数） |
| 全suite `python -m pytest -q` | 755 passed, 18 skipped, 0 failed（2,379.63秒、単一プロセス） |

- **全suiteの件数**: PR #11のWindowsでの731件（724 passed・7 skipped）に、新規42件（39件と拒否例3件）を加えた773件である。
- **skip**: 18件はすべて環境によるもので、continuation関連の試験にはない。Windowsの Arial（9件）、Noto Sans JP（2件）、外部corpus（5件）、AES provider（2件）である。
- **実行方法**: 検証用のvenvに入れた`pytest-xdist`（repoの依存にはない）は、全suiteでは`-p no:xdist`で無効にした。個別の試験は4並列で実行した。

- **新しい試験**（`tests/test_ctm_compensation.py`、39件）:
  - 証明: translation・異方scale・30°回転・skew・y反転で、逆行列が1つの`cm`として書け、厳密な残差が1e-9未満、上界が許容値の半分以下であること。特異（3例）・非有限（2例）・不安定（ill-conditioned・大きなtranslation・範囲外の逆行列）の拒否。binary32で同じ値になるoperandの違いも、証明で区別すること。
  - 候補: 5種のCTMの境界が候補になり、`ctm_compensation`を持つこと。identityの境界は持たないこと。同じCTMでも、有効なclip・`q`の内側・ExtGState・marked contentは従来どおり拒否すること。
  - 座標と画素: 5種のCTMで、生成glyphのplan・page座標・画素が、同じページのpage-entry版と一致すること。別ページのidentity境界版とも、glyphのpage座標が一致すること。
  - suffix: 相殺blockの`Q`の直後がCTM M・`q`深さ0で、suffixの文字のCTMもMであること。suffixとprefixの文字・path・領域外の画素が元PDFと同じであること。対照として、逆行列を`q`/`Q`なしで境界に置くと、suffixの文字とpathが動くこと。
  - lifecycle（translation・scale・回転）: fits → grow → second → shorten → regrow → no-op 3回。各保存でreopen、authority・作成証跡の不変、prefix/block/suffix、blockの`cm`が1つであること、入れ子の違反0、block内とblock直後のCTM、生成fontの所有、no-opの画素・glyph plan・font再利用を確かめる。
  - 改ざん: sidecarの10種（confirmed CTM、記録したCTM・行列・operator・証明・source CTM・policy・isolation、相殺の削除、別CTMの一貫した偽造）と、programの5種（逆行列のoperand、逆行列の削除、2つ目の`cm`、binary32未満のsource operandの変更、sourceのCTM変更）を拒否すること。identityのblockに`cm`を入れた改ざんも拒否すること。block検証器の単体試験。
  - 共存: 同じページで、page-entry destinationまたはidentityの境界と、相殺した境界が独立に動くこと（grow・shorten・regrow・no-op）。
  - rollback: rebind・commitの遅い失敗で何も公開しないこと。
- **既存試験の変更**（`tests/test_boundary_destination.py`）: 意図した挙動の変化に合わせて2件を直した。
  - 候補の列挙: `2 0 0 2 0 0 cm`の境界は候補になる（従来は`nonidentity-ctm`）。`q`の内側・特異・ill-conditionedの例を加えた。
  - 同じoffsetでの状態の改ざん: `1 0 0 1 5 5 cm`に変えた元PDFでは、その境界は候補になるが、boundary IDが違う別のauthorityで、`ctm_compensation`を持つことを確かめる（従来は候補にならないことを確かめていた）。生成済みrevisionでの拒否は変わらない。
- **mutationによる確認**: engineに欠陥を1つずつ入れ、新しい試験が失敗することを確かめた（commitしていない）。相殺の再導出を外す、blockの`cm`のbytesを照合しない、writerが逆行列を`q`の外に置く、の3つである。

### 外部評価

今回は実施していない。LibreOffice原本で確認済みの有用な境界はCTM identityで、今回の変更の対象外である。identity以外のCTMを示すために原本を書き換えたり、不自然な候補を作ったりはしていない。クラウド環境には、評価が使うWindowsのfont（`C:/Windows/Fonts/msmincho.ttc`・`times.ttf`）とPoppler（`pdftoppm`）がない。代替fontや代替PDFでの評価もしていない。既存のsingle・boundary・multiの回帰評価は、必要ならWindows環境で別に行う。

- identity CTMの境界では、候補・authority・block・bindingの形はPR #11と同じである。
- 6ページの公開集計（`boundary-destination-summary.json`）の拒否理由には`nonidentity-ctm`が含まれていない。つまり、そのページにはidentity以外のCTMの境界がなく、候補と拒否理由の集計は変わらないと見込む。これは公開集計からの推論で、原本では確かめていない。

その後、Windows環境で3本の回帰評価を行い、両方の見込みを原本で確かめた（[下記](#pr-12後のwindows外部回帰--2026-09-2644e547b)）。

### 残る未対応の状態

- `q`の内側、有効なclipの下、ExtGState（不透明度・blend・soft mask）
- 特異・非有限・数値的に不安定なCTM
- text object・marked content・`BX ... EX`・Form XObjectの内側
- 1つの境界への複数destination

### 次の最小の構造障壁

有効なclipの下の境界である。page levelのclipは、blockの`q ... Q`では外せない（戻す先の状態がblockの外にない）。そのため、destinationと生成glyphのinkがclipの内側に収まることを、clipのpathとCTMから証明する必要がある。矩形clipから始めるのが最小である。

## PR #12後のWindows外部回帰 — 2026-09-26（`44e547b`）

起点はPR #12のmerge commit `44e547b`。サブエージェントは使用していない。PR #12はidentity以外のCTMの相殺を加えたが、LibreOffice原本の確認済み境界はCTM identityである。そのため、これは新機能の外部証明ではない。PR #11まで成立していた外部経路をPR #12が壊していないことを、Windows環境で確かめた。原本を加工してidentity以外のCTMの境界を作ることはしていない。

### 開始時の確認

| 項目 | 結果 |
|---|---|
| HEAD | `44e547bbce121fb52445130e7f33c2a29bbff175`（PR #12のmerge）。作業ツリーはclean |
| engine digest | `383bbd49fddacba074b54d676f468d032c9f27a249af47af56c07a653bb55262`（45ファイル）。PR #12の値と一致。PR #11の最終engine（`59fe6125…1bfd`）との違いは`continuation.py`・`paragraph.py`だけ |
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、PyMuPDF 1.27.2.3、pypdf 6.10.0、uharfbuzz 0.55.0、fontTools 4.64.0 |
| 独立tool | Poppler `pdftoppm` 26.07.0、独立pypdf 6.10.0（Python 3.12.14）。既定pathのまま |
| 原本 | `lo_migration_ja.pdf` SHA-256 `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`で一致 |
| provider | `msmincho.ttc` face 1 `ceb8d745001f56b61ce768d84172d35bdf68e498423c9320dcb22e7c900944c2`、`times.ttf` face 0 `931c5de5c70401d9324d5014c123802b4fb753000360ceb2f56c589403cd58c5`。story_styles公開集計（`c2329afa…c7c2`）と一致 |
| 評価コード | PR #11から変更なし（runner・依存ファイルのhashが公開集計と同じ） |

### 結果

engine・評価コードは変更していない。3本の外部評価と全suiteを並行して実行した。

| 検証 | 結果 |
|---|---|
| 全suite `python -m pytest -q` | 766 passed, 7 skipped, 0 failed（5,109.32秒、外部評価と並行）。PR #11の731件にPR #12の新規42件を加えた773件。skipは7件でPR #11と同数 |
| 外部原本 単一destination（run `ctm-regression-windows`） | 全段階・no-op 3回・容量拒否が通過（3,205秒）。成果物129件がPR #11のrun `failclosed-single-windows`とbyte単位で一致 |
| 外部原本 確認済み境界（run `boundary-ctm-boundary-regression-windows`） | 全段階・no-op 3回・容量拒否が通過（3,225秒）。page-entry run `ctm-regression-windows`と、全段階の計画glyph・Poppler画像が一致。成果物129件がrun `boundary-failclosed-windows`とbyte単位で一致 |
| 外部原本 同一ページ2 destination（run `multi-ctm-multi-regression-windows`） | 逐次8段階・同時2段階・容量拒否が通過（4,376秒）。`simultaneous_equals_sequential = true`。成果物177件がrun `multi-failclosed-windows`とbyte単位で一致 |

- **PR #11評価との差分**: 3つの集計の違いは、engine digestと`continuation.py`・`paragraph.py`のhashだけだった。境界の集計では、比較したpage-entry runの名前とengine digestも違う。次はすべて同じである。
  - lifecycle全段階とno-op 3回、容量拒否とその理由。
  - allocation、slot identity、境界identity（`boundary-b848698b464255ff0b2b6f90`、offset 17602）、page-entry chain。
  - 生成font/resourceの量とaliasごとの結果、operator nesting（違反0）、PDF version（`%PDF-1.4`）。
  - MuPDF・Popplerの照合、region外のPoppler差分0画素、独立pypdfのUnicode、CID/GID/`W`。
  - source paint・画像・annotation・元font、reopen。
- **byte一致**: PDF・sidecar・plan・report・監査画像・抽出文字の計435件が、PR #11の最終runとbyte単位で一致した。一致しない成果物はなかった。
  - identity CTMの経路でPR #12が出力形式を変えていないことと合う。authorityは`ctm_compensation`を持たず（全sidecar 24件）、`isolation = q-BT-ET-Q`のままである。blockは`q BT`で始まり、`cm`を含まない。
  - 置き換える前の公開集計3件は、それぞれPR #11の最終runの`summary.json`とbyte単位で同じだった。そのため、この比較は公開集計との比較でもある。
- **境界の検査**: 評価コード外の一時スクリプト（commitしていない）で、原本の10ページについて、`inspect_continuation_boundaries(..., include_refused=True)`の全記録をPR #11のengineとPR #12のengineで比べた。
  - 候補は全ページで同じだった。6ページを含む2〜9ページは、拒否した境界も含めて記録全体が同じだった。
  - 違いは、1ページの2境界と10ページの18境界だけである。どれも`q`の内側・有効なclipの下にある。PR #11の`nonidentity-ctm`が拒否理由から消え、証明済みの`ctm_compensation`が付いた。どれも残る理由で拒否される。PR #12の意図どおりの変化である。
  - 6ページにはidentity以外のCTMの境界がない。PR #12で公開集計から推論したことを、原本で確かめた。
- **目視**: 次の切出しを確認した。計62枚が、PR #11のrunのPDFから作った切出しとPNGのbytesまで同じだった。
  - 単一destinationと境界の各段階の、4〜6ページ。
  - 2 destinationの逐次・同時の各段階の、4〜6ページと6ページのregion間（600dpi）。
  - 6ページの生成行は確認済み領域内にあり、ロゴ・本文・図版に変化はなかった。shortenとdormantの領域は空白だった。
- **環境上の注意**: sandboxから既定のpytest一時directory（`%TEMP%\pytest-of-<user>`）を読めなかった。最初の全suiteは、fixtureの準備でerrorになったため中断した。一時directoryだけを作業用directoryへ移し（`--basetemp`）、同じsuiteを最初から実行した。試験・engineは変えていない。

### 変更

- engineと評価コード: 変更なし。不具合は見つからなかった。
- 公開集計: `summary.json`・`boundary-destination-summary.json`・`multi-destination-summary.json`を、今回の3 runの集計に置き換えた。
- 資料: [評価README](../evaluations/continuation/README.md#pr-12-engineでの回帰評価)と、この資料。README本体は変えていない。記述（CTMの相殺は合成PDFのみで確認）がそのまま正しいためである。

### 次の最小の構造障壁

PR #12と同じく、有効なclipの下の境界である。原本の1ページ・10ページには、identity以外のCTMを持つ境界がある。どれも`q`の内側で、有効なclipの下にある。

## page level境界での矩形clipの継承 — 2026-09-26（`399d4f6`）

起点はPR #13のmerge commit `399d4f6`。サブエージェントは使用していない。confirmed page-program boundaryに残る制約のうち、有効なclipだけを1段緩めた。有効なclipを1つのpage矩形として証明でき、destination全体と生成glyphのinkがその矩形の内側に完全に収まる場合に限り、continuation destinationとして許可する。`q`の深さ0、text object・marked content・`BX ... EX`の外、組み立て中のpath/clipなし、ExtGStateなし、不透明度1、operator nestingの違反なしは維持した。契約は[矩形clipの継承](confirmed-continuation.md#矩形clipの継承)にある。clipを一般に扱えるようにしたものではない。

### 開始時の確認

| 項目 | 結果 |
|---|---|
| HEAD | `399d4f63dafaee87ae34ffa1dcaee15899cc166c`（PR #13のmerge）。作業ツリーはclean |
| engine digest（開始時） | `383bbd49fddacba074b54d676f468d032c9f27a249af47af56c07a653bb55262`（45ファイル）。PR #12・#13の値と一致 |
| 環境 | Linux（クラウド環境）、Python 3.12.3、lockfileの版（PyMuPDF 1.27.2.3、pypdf 6.10.0、fontTools 4.64.0ほか） |

### 方式

```text
existing prefix                  clip = C（page levelで確定済み）、CTM = M
q
  [N cm]                         PR #12の逆行列（Mがidentity以外のときだけ）
  BT ... generated text ... ET   clip = Cのまま
Q
existing suffix                  clip = C、CTM = M
```

- **継承**: blockは既存のclipを変えない。`W`・`W*`・`n`・pathを書かず、clipの解除・再構築・拡大をしない。blockの形はPR #12と同じで、`_block`の許可operatorも変えていない。
- **証明**（`clip_constraint()`）: 各clipが、連続した`re`・`W`/`W*`・`n`による1つの`re` subpathで、そのCTMに回転・skewがないこと。interpreterのCTM（binary32）とoperandから厳密に合成したCTMの両方で、page座標の矩形を有理数で求める。全clipのintersectionをbinary32の丸めの上界だけ縮め、binary64へ内向きに丸めたものがcertified rectangleである。
- **拒否**: `nonrectangular-clip`・`rotated-clip`・`text-clip`・`empty-clip`・`unproven-clip`。従来の一律の`active-clip`はなくなった。
- **包含**:
  - 確認時: destination bounds ⊆ certified rectangle（厳密、許容値なし）。
  - 生成時・再編集時: 計画した各glyphの輪郭のink boxを1.002pt広げたものが内側にあること。1ptは描画系のpaint envelope（MuPDFのbbox logは輪郭のboxを72dpiで1単位広げる）、0.002ptは保存したglyph原点の許容値である。
  - 保存後: 生成文字のpaint envelope（bbox log）が内側にあること。
- **authority**: `clip_constraint`に、certified rectangle、clipごとのrule・operand・2つのCTM・設定したoperatorのbytesのSHA-256・厳密な矩形・丸めの上界、証明の方式とモデル、包含の規則と余白を記録する。`initial_clip = inherited-rectangular-clip`。`graphics_state.clip`は、ruleとpathだけを持ち、位置（`at`）を持たない。
- **再検証**: open・保存のたびに`clip_constraint`・`initial_clip`・`graphics_state_contract`を再導出して記録と比べる。block内の文字のclipは、確認済みのclipと同じでなければならない。

### 変更

- **`pdfeditor/continuation.py`**:
  - `clip_state()`・`clip_constraint()`・`clip_contains()`・`require_inside_clip()`を加えた。
  - 候補の列挙（`_inspect`）と確認（`_boundary_authority`）で、証明できたclipの境界に`clip_constraint`を付ける。証明できない場合は拒否理由にする。
  - `confirm_continuation_destination`でdestination boundsの包含を求める。
  - revisionごとの検証（`_boundary_value`）でclipの制約を再導出する。`_validate_destination`は、destinationの包含、文字のclip、保存した文字のpaint envelopeを検証する。
- **`pdfeditor/shared_flow.py`**: 計画（`_plan`）で、生成slotの各glyphのinkの包含を求める。作成でも再編集でも適用する。
- **`pdfeditor/paragraph.py`**: 作成blockのwriterでも、同じinkの包含を求める。
- **変えていないもの**: `content_stream.py`（interpreter）、`mutation.py`、`transaction.py`、blockの形と`_block`、page-entryの検証・binding形式、marker・rebind、生成fontの寿命、`require_empty()`。
- **engine digest**: `e8998aa927707ecb1f7292ffec85dde1a315992c64e500b5c5c3caba2b313803`（45ファイル）。

### 検証（Linux、Python 3.12.3）

| 検証 | 結果 |
|---|---|
| `tests/test_clip_boundary.py`（新規） | 46 passed |
| `tests/test_boundary_destination.py` | 37 passed（PR #12の36件と、拒否例の追加1件） |
| `tests/test_ctm_compensation.py` | 39 passed（件数は同じ。拒否例1件を差し替え） |
| 全suite `python -m pytest -q`（`01186d4`） | 802 passed, 18 skipped, 0 failed（2,477.78秒、単一process） |

- **全suiteの件数**: PR #13までの773件に、新規47件（`test_clip_boundary.py`の46件と拒否例1件）を加えた820件である。開始時のmain（`399d4f6`）の全suiteは、同じ環境で755 passed, 18 skipped, 0 failed（2,049.30秒）だった。skip 18件はmainと同じで、すべて環境によるもの（Windowsのfont、外部corpus、AES provider）である。continuation関連の試験にはない。
- **新しい試験**（`tests/test_clip_boundary.py`、46件）:
  - 候補: 矩形clipの境界が`clip_constraint`を持つ候補になること。記録（rule・operand・CTM・operatorのbytes・厳密な矩形・丸めの上界・certified rectangle・余白）と、`graphics_state.clip`が位置を持たないこと。19種の境界で、証明できるclip・拒否するclip（複数subpath・多角形・曲線・回転・skew・90°回転・交わらない矩形・幅0・`re W f`・pathより前の`W`・text clip）と、clipが矩形でも従来どおり拒否する状態（`q`・ExtGState・marked content・`BX`）。矩形clipの下の組み立て中のpath/clip。
  - 確認: certified rectangleの4辺上は受け付け、外へ1 ulp、clip自体の辺、1pt外はすべて拒否すること。boundsを変えてもauthorityが同じこと。clipで隠れて見えない既存paint（画素は同じ）も障害物のままであること。
  - lifecycle（identity、clipの後の回転、scaleの下のclip）: fits → grow → second → shorten → regrow → no-op 3回。reopen、authority・`clip_constraint`・作成証跡の不変、prefix/block/suffix、blockに`W`・`W*`・`n`・`re`がないこと、入れ子の違反0。block内の文字（CTMが`block_ctm()`、clipが確認済みのもの）、blockの`Q`の直後とsuffixの文字（元のCTMとclip、`q`深さ0）、生成文字のpaint envelopeの包含、生成fontの所有、no-opの画素・glyph plan・font再利用。
  - 配置（identity、translation・reflection・scaleの下のclip、clipの後の回転・skew）: 生成glyphのplan・page座標・画素が、同じページのpage-entry版と一致すること。clipが同じpage矩形のままで、clipの後に回転・skewするページでは制約全体がidentityのページと同じこと。prefix・suffixの文字・path・領域外の画素が元PDFと同じこと。対照として、clipを外したPDFでは領域外の画素が変わり、境界の証跡も拒否すること。
  - ink: 包含判定が厳密で、余白1.002ptを使うこと（単体）。LSBが0の`A`で始まる行が、certified rectangleの辺上または1pt内側にあると、計画・保存とも拒否し何も公開しないこと。1.01pt内側なら書け、保存後のpaint envelopeが辺から0.01ptに収まること。計画の判定を外してもwriterが拒否すること。再編集で辺に達する場合の拒否と、前のrevisionの維持。保存後のpaint envelopeが外へ出た場合の拒否（bbox logを差し替えた対照。clipのない境界では拒否しない）。
  - 改ざん: sidecarの14種（rectangle・rule・operand・CTM・operatorのbytes・証明・余白・policy・制約の削除・`initial_clip`・contract・`graphics_state.clip`のrule・path・削除）と、programの5種（`W`→`W*`、geometry、2つ目のclip、clipの削除、block内の`re W n`）の拒否。
  - 位置の移動: 同じページのpage-entry destinationのblockがclipのoperatorの位置を動かしても、clipの境界は同じauthorityで、文字は確認済みのclipの下にあること（grow・shorten・regrow・no-op）。同じページのsource slotをclipより前で書き直す場合も同じで、blockはprefixの伸びに合わせて動くこと（grow・second・no-op）。
  - rollback: rebind・commitの遅い失敗で何も公開しないこと。
  - clipのない境界: identityと相殺の境界で、authorityの鍵・`initial_clip`・contractの文言・binding・blockの形がPR #11・#12と同じであること。
- **既存試験の変更**: 意図した挙動の変化に合わせて3件を直した。
  - `test_boundary_destination.py`の候補の列挙: `0 0 320 260 re W n`の境界は候補になる（従来は`active-clip`）。複数subpathの拒否例を加えた。
  - 同じoffsetでの状態の改ざん: clipを加えた元PDFでは、その境界は候補になるが、boundary IDが違う別のauthorityで、`clip_constraint`を持つことを確かめる。生成済みrevisionでの拒否は変わらない。
  - `test_ctm_compensation.py`の「CTM以外は拒否」: 矩形clipの例を、複数subpathのclip（`nonrectangular-clip`）に差し替えた。
- **clipのない境界の出力**: 評価コード外の一時スクリプト（commitしていない）で、PR #13のengineと今回のengineの出力を比べた。identityと相殺（回転）の境界で、確認 → grow → second → shorten → regrow → no-opを保存し、3ページの`inspect_continuation_boundaries(..., include_refused=True)`の記録も比べた。PDF 15件・sidecar 10件ほか計28ファイルが、byte単位で一致した。
- **mutationによる確認**: engineに欠陥を1つずつ入れ、新しい試験が失敗することを確かめた（commitしていない）。10種すべてで失敗した。
  - 確認時の包含を外す、計画のinkの判定を外す、writerのinkの判定を外す、描画系の余白1ptを外す。
  - 再導出の照合を外す、回転の下の矩形を受け付ける、保存後のpaint envelopeの判定を外す。
  - 丸めの上界で縮めない、`re W n`の連続を確かめない、複数subpathを受け付ける。
- **実行方法**: 検証用のvenvに入れた`pytest-xdist`（repoの依存にはない）は、個別の試験の4並列実行にだけ使った。全suiteは`python -m pytest -q`を単一processで実行した。

### 外部評価

今回は実施していない。完了条件にもしていない。

- **原本の境界**: PR #13で確認したLibreOffice原本の有効なclipの下の境界は、`q`の内側にある。
  - 6ページ: 公開集計では、拒否した1,638境界のすべてが`q`の内側（`inside-graphics-state-save` 1,638）で、`active-clip`の1,632境界もその中にある。候補2つはclipを持たない。
  - 1ページ・10ページ: PR #13の検査で違いが出た境界は、どれも`q`の内側・有効なclipの下だった。
  - 少なくとも6ページでは、`q`の内側を許可しない限り、新機能の対象になる境界はない。これは公開集計の値から言えることで、今回のengineで原本を検査してはいない。他のページは確かめていない。
- **原本の加工**: 評価用の候補を作るために原本を書き換えることはしていない。
- **環境**: クラウド環境には、評価が使うWindowsのfont（`C:/Windows/Fonts/msmincho.ttc`・`times.ttf`）とPoppler（`pdftoppm`）がない。
- **既存の外部経路への影響の見込み**: 確認済みの境界（`boundary-b848698b464255ff0b2b6f90`）はclipを持たないので、authority・blockは変わらない。上の一時スクリプトで確かめたように、clipのない境界の出力はbyte単位で同じである。境界の検査を原本でやり直すと、拒否理由の`active-clip`は新しい理由に分かれる。証明できた矩形clipの境界では、clipの理由が消えて`inside-graphics-state-save`等だけが残る。候補は変わらないと見込む。これも原本では確かめていない。公開集計はPR #13のrunのまま更新していない。

### 残る未対応の状態

- `q`の内側、ExtGState（不透明度・blend・soft mask）
- 多角形・曲線・複数subpath・回転やskewの下のclip、text clip
- 特異・非有限・数値的に不安定なCTM
- text object・marked content・`BX ... EX`・Form XObjectの内側
- 1つの境界への複数destination

### 次の最小の構造障壁

`q`の内側の境界である。LibreOffice原本の有効なclipの下の境界は、どれも`q ... Q`の内側にある。そこでは、境界の状態（CTM・clip）を決める外側の`q`と、それを閉じる`Q`の組を証跡として固定する必要がある。blockがその`Q`より前で自分の状態を閉じること、scopeの`Q`がblockの後もsuffixの状態を戻すことも示す必要がある。CTMの相殺と矩形clipの継承は、その内側でもそのまま使える見込みである。

## 1つの`q ... Q` scope内の確認済み境界 — 2026-09-26（`2f26666`）

起点はPR #14のmerge commit `2f26666`。サブエージェントは使用していない。confirmed page-program boundaryに残る制約のうち、`q ... Q` scopeの内側を最小範囲で1段緩めた。`q`の深さ1で、1つの明確なscopeの内側にあり、境界の状態がCTMの相殺・矩形clip等の既存の契約で扱え、生成blockがそのscopeの対応する`Q`より前で必ず閉じる場合だけ許可する。`q`の深さ2以上は拒否する。契約は[1つの`q ... Q` scopeの内側](confirmed-continuation.md#1つのq--q-scopeの内側)にある。任意のgraphics-state stackを扱えるようにしたものではない。

### 開始時の確認

| 項目 | 結果 |
|---|---|
| HEAD | `2f26666`（PR #14のmerge）。前回のブランチはmerge済みの履歴だけだったので、同じ名前のブランチをmainから作り直した |
| engine digest（開始時） | `e8998aa927707ecb1f7292ffec85dde1a315992c64e500b5c5c3caba2b313803`（45ファイル）。PR #14の値と一致 |
| 環境 | Linux（クラウド環境）、Python 3.12.3、lockfileの版 |

### 方式

```text
opening q                              page level
  ...                                  scopeのprefix
  confirmed boundary                   q depth 1
  marker q [N cm] BT ... ET Q marker   境界の状態だけを保存・復元する
  ...                                  scopeのsuffix（境界と同じ状態）
matching Q                             scopeの前の状態を戻す
```

- **証明**（`enclosing_scope()`）: top-level operatorの`q`/`Q`の入れ子（`graphics_scopes()`）で、境界の後に開いている`q`が1つだけ（interpreterの深さとも一致）で、対応する`Q`が境界の後にあること。開く`q`・その直前・対応する`Q`がtext object・marked content・`BX ... EX`・組み立て中のpath/clipの外にあること（scopeが交差しない）。対応する`Q`の直後の状態が、`q`の直前の状態（`restored_state`）と同じであること。
- **拒否**: `nested-graphics-state-save`（深さ2以上）、`unproven-graphics-state-scope`（交差・不整合）。従来の一律の`inside-graphics-state-save`はなくなった。境界の状態の条件（CTM・clip・ExtGState・不透明度・描画モード・text object・marked content・`BX ... EX`・組み立て中のpath/clip・入れ子）は変えていない。
- **authority**: `graphics_state_scope`に、policy（`one-enclosing-graphics-state-save`）、深さ1、開く`q`と対応する`Q`の確認時の序数・範囲・bytesのSHA-256、`restored_state`、contractを記録する。scope内の境界の`boundary_id`は、開く`q`・対応する`Q`の序数・終端・SHA-256も含めて作る。`graphics_state_contract`は、scope内の状態であることと、blockがscopeの`Q`より前で閉じることを明記する。`q`の深さ0の境界のauthority・ID・bindingは変えていない。
- **binding**: revisionごとに、そのrevisionでの開く`q`・対応する`Q`の範囲（`scope`）を記録する。
- **同じscopeの判定**: `q`・`Q`のbytesはどのscopeでも同じなので、bytesや現在のoffsetだけでは判定しない。
  - 保存では、前のbindingの`q`・`Q`の位置をその保存のmutation mapで写し（`carried_scope()`）、新しいrevisionの構造が境界の周りに置く`q`・`Q`と一致させる。mutationが`q`か`Q`を消費すれば、写し先がないので拒否する。
  - openでは、bindingの位置と構造を一致させる。確認時のprogramでは、authorityの記録とも一致させる。
  - 記録（位置を除く）と再導出も、毎回比べる。
- **scopeを越えない保証**: blockの検証は自己完結した1つの`q ... Q`だけを認め、scopeはblockの開始位置で終わるoperatorから求めるので、対応する`Q`は入れ子の上で必ずblockの後ろにある。さらに、開く`q`の終端 ≤ block ≤ 対応する`Q`の先頭を、revisionごとに明示的に確かめる。
- **CTMの相殺・矩形clipとの共存**: scope内の`cm`・`re W n`は、境界の状態として既存の証明をそのまま使う。blockの`Q`がscope内の状態を戻し、scopeの`Q`がscopeの前の状態を戻す。

### 変更

- **`pdfeditor/continuation.py`**:
  - `graphics_scopes()`・`enclosing_scope()`・`carried_scope()`を加えた。
  - 候補の列挙と確認で、scope内の境界に`graphics_state_scope`を付ける。証明できない場合は拒否理由にする。`boundary_id`・`graphics_state_contract`にscopeを反映する。
  - revisionごとの検証（`_boundary_value`）で、scopeを再導出して記録・位置と比べ、blockがscopeの内側にあることを確かめる。bindingに`scope`を加える。
  - `page_witness`・`witness`・`validate_destinations`・`ContinuationParagraph`で、scopeの位置を受け渡す。
- **`pdfeditor/shared_flow.py`**: 保存後の再bindingで、前のbindingのscopeの位置をmutation mapで写す。
- **変えていないもの**: `content_stream.py`（interpreter）、`mutation.py`（`MutationProgram`の規則）、`operator_nesting.py`、`paragraph.py`（writer）、blockの形と`_block`、CTMの相殺と矩形clipの証明、page-entryの検証・binding、生成fontの寿命、`require_empty()`。
- **engine digest**: `454686cef09460f76c3daaa064d2765748b74e83c8e6963d323a00cd905b71c5`（45ファイル）。

### 検証（Linux、Python 3.12.3）

| 検証 | 結果 |
|---|---|
| `tests/test_scope_boundary.py`（新規） | 35 passed |
| `tests/test_boundary_destination.py` | 38 passed（深さ2の拒否例を追加） |
| `tests/test_ctm_compensation.py`・`tests/test_clip_boundary.py` | 39 passed・46 passed（件数は同じ。拒否例を深さ2に差し替え） |
| continuation・multi・nesting・generated fonts・mutationを含む関連8ファイル | 210 passed |
| 全suite `python -m pytest -q`（`fdc5917`） | 838 passed, 18 skipped, 0 failed（3,072.70秒、単一process） |

- **全suiteの件数**: PR #14までの820件に、新規36件（`test_scope_boundary.py`の35件と拒否例1件）を加えた856件である。skip 18件はPR #14と同じで、すべて環境によるもの（Windowsのfont、外部corpus、AES provider）である。
- **新しい試験**（`tests/test_scope_boundary.py`、35件）:
  - 候補: 深さ1の境界が`graphics_state_scope`を持つ候補になること。開く`q`・対応する`Q`の位置とbytes、`restored_state`。scope内の境界がすべて同じ記録を持ち、scopeの外の境界は持たないこと。
  - 11種の境界: 証明できるscope（1つ、閉じた兄弟scopeの後、scope内で閉じたmarked content・`BX`）、拒否するscope（深さ2、marked content・`BX`との交差）、scope内でも拒否する状態（ExtGState、特異なCTM、多角形のclip、描画モード）。`q`/`Q`が釣り合わないページ（閉じない`q`、`q`のない`Q`）は扱わないこと。
  - authority: scope・相殺・矩形clipの記録とcontractの文言。scopeの前の境界はPR #11の形のままであること。
  - lifecycle（identity、相殺、矩形clip、相殺と矩形clip）: fits → grow → second → shorten → regrow → no-op 3回。reopen、authority・作成証跡の不変、bindingの`q`・`Q`がprogramの`q`・`Q`で、互いに対応し、blockがその間にあること。対応する`Q`はblockの伸びだけ動くこと。blockに`W`・`W*`・`n`・`re`がないこと、入れ子の違反0。block内（`block_ctm()`と確認済みのclip）、blockの`Q`の直後（深さ1、境界と同じ状態）、scope内のsuffixの文字、対応する`Q`の直後（深さ0、`restored_state`）、scopeの後の文字（identityのCTM・clipなし・scopeの前の色）。生成fontの所有、no-opの画素・glyph plan・font再利用。
  - 配置: 4種とも、生成glyphのplan・page座標・画素がpage-entry版と一致し、scopeの内外の既存の描画と領域外の画素が元PDFと同じこと。scopeの最後の境界（次が対応する`Q`）では、blockの直後が対応する`Q`になり、その後の状態が戻ること。
  - 改ざん: sidecarの13種（開く`q`の序数・bytes、対応する`Q`の終端、深さ、`restored_state`、policy、contract、scopeの記録の削除、`graphics_state_contract`、boundaryの深さ、bindingの`q`・`Q`の位置、bindingのscopeの削除）。programの8種（別の`q`、別の`Q`、境界の後の`Q q`による対応の変更、境界の前の`Q q`による別のscope、深さ2、深さ0、釣り合わない`Q`、対応する`Q`の後ろへのblockの移動）。対照として、手を加えないrevisionは同じbindingになること。
  - 再binding: paragraph Aのsource slotがscopeの前・prefix・suffix・後にある場合の、grow・second・no-op。`q`はscopeの前の書き直しでだけ動き、blockと対応する`Q`の間はsuffixの書き直しでだけ広がること。scopeの端（`q`か`Q`、単独でも前後を含めても）を消費するmutationは写し先がなく拒否すること（単体）。
  - rollback: rebind・commitの遅い失敗で何も公開しないこと。
- **既存試験の変更**: 意図した挙動の変化に合わせた。`test_boundary_destination.py`の候補の列挙（深さ1の2例は候補になる、深さ2の拒否例を追加）と、同じoffsetでの状態の改ざん（`q ... Q`で囲んだ元PDFの境界は別のboundary IDと`graphics_state_scope`を持つ別authority）。`test_ctm_compensation.py`・`test_clip_boundary.py`のscope内の拒否例を深さ2に差し替えた。`test_clip_boundary.py`の候補の選択を、scopeの外の境界に限った（隠れたpaintのscope内にも候補ができたため）。
- **`q`の深さ0の経路の出力**: 評価コード外の一時スクリプト（commitしていない）で、PR #14のengineと今回のengineの出力を比べた。identity・相殺・矩形clip・矩形clipと相殺の4種の境界で、確認 → grow → second → shorten → regrow → no-opを保存した。5ページの`inspect_continuation_boundaries(..., include_refused=True)`の記録も比べた。PDF 29件・sidecar 20件ほか計54ファイルが、byte単位で一致した。
- **mutationによる確認**: engineに欠陥を1つずつ入れた（commitしていない）。9種のうち8種で新しい試験が失敗した。
  - 失敗したもの: bindingの位置の照合を外す、mutation mapで写さない、深さ2を受け付ける、交差を受け付ける、記録の照合を外す、IDからscopeを外す、scopeの拒否理由を落とす、深さ0の境界にもscopeを記録する。
  - 失敗しなかったもの: 開く`q`の終端 ≤ block ≤ 対応する`Q`の先頭の明示的な確認だけである。blockの検証とscopeの導出が同じことを既に保証するため、到達できない防御として残した。
- **実行方法**: 検証用のvenvに入れた`pytest-xdist`（repoの依存にはない）は、個別の試験の並列実行にだけ使った。全suiteは`python -m pytest -q`を単一processで実行した。

### 外部評価

今回は実施していない。完了条件にもしていない。

- **次の評価の対象**: PR #13・#14で確認したLibreOffice原本の1ページ・10ページには、`q`の内側で有効なclipの下の境界がある。今回の実装で、これらが実際に安全な候補になるかは、次のWindows環境での作業で評価する。
- **原本の加工**: クラウド環境で原本を加工した代替評価はしていない。
- **既存の外部経路**: 確認済みの境界（`boundary-b848698b464255ff0b2b6f90`）は、`Q`と`q`の間の`q`の深さ0の境界である。authority・ID・blockは変わらない。上の比較のとおり、深さ0の経路の出力はbyte単位で同じである。境界の検査を原本でやり直すと、`inside-graphics-state-save`は新しい理由に分かれ、深さ1で他の条件も満たす境界は候補になりうる。原本では確かめていない。公開集計は更新していない。

### 残る未対応の状態

- `q`の深さ2以上、marked content・`BX ... EX`と交差するscope
- ExtGState（不透明度・blend・soft mask）
- 多角形・曲線・複数subpath・回転やskewの下のclip、text clip
- 特異・非有限・数値的に不安定なCTM
- text object・marked content・`BX ... EX`・Form XObjectの内側
- 1つの境界への複数destination

### 次の構造障壁

外部原本での確認が先である。1ページ・10ページの`q`の内側で有効なclipの下の境界が、深さ1・矩形clip・相殺できるCTMの条件を満たして候補になるかを、Windows環境で評価する。構造の障壁としては、`q`の深さ2以上（入れ子のscopeの連なりを、各段の`q`と対応する`Q`の証跡として固定する）と、ExtGStateが残る。

## PR #15後のWindows実PDF評価 — 2026-09-26（`4ce4689`）

起点はPR #15のmerge commit `4ce468939892d452e91420ff61baa331988a2923`。サブエージェントは使用していない。engineの機能は追加していない。PR #13〜#15で緩めた条件（CTMの相殺、矩形clipの継承、深さ1の`q ... Q` scope）について、加工していないLibreOffice原本の1ページ・10ページで安全な候補が実際に生まれるかを`inspect_continuation_boundaries()`で調べた。10ページに新しい候補と明確な空き領域があったので、そこで系列評価を行った。安全条件は緩めていない。

### 開始時の確認

| 項目 | 結果 |
|---|---|
| HEAD | `4ce468939892d452e91420ff61baa331988a2923`（PR #15のmerge）。作業ツリーはclean |
| engine digest | `454686cef09460f76c3daaa064d2765748b74e83c8e6963d323a00cd905b71c5`（45ファイル）。PR #15の値と一致 |
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、PyMuPDF 1.27.2.3、pypdf 6.10.0 |
| 独立tool | Poppler `pdftoppm` 26.07.0、独立pypdf 6.10.0（Python 3.12.14）。既定pathのまま |
| 原本 | `lo_migration_ja.pdf` SHA-256 `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`で一致 |
| provider | `msmincho.ttc` face 1 `ceb8d745001f56b61ce768d84172d35bdf68e498423c9320dcb22e7c900944c2`、`times.ttf` face 0 `931c5de5c70401d9324d5014c123802b4fb753000360ceb2f56c589403cd58c5`。story_styles公開集計（`c2329afa…c7c2`）と一致 |

### 原本の検査

詳細は[評価README](../evaluations/continuation/README.md#原本の検査1ページ10ページ)にある。

| | 1ページ | 10ページ |
|---|---|---|
| 安全な候補（PR #13 → #14 → #15） | 2 → 2 → 128 | 2 → 2 → 88 |
| PR #15で新しく安全になった境界 | 126（深さ1・CTM identity。矩形clipの下124、clipの設定前2） | 86（同。矩形clipの下84、clipの設定前2） |
| `ctm_compensation`を持つ候補 | 0 | 0 |
| identity以外のCTMの境界 | 2（深さ2、`nested-graphics-state-save`） | 18（深さ2。12は`nested-graphics-state-save`だけ、6は`pending-path`も） |

- **CTMの相殺・矩形clip**: identity以外のCTMの20境界すべてで、相殺と矩形clipは証明される。残る拒否理由は`q`の深さ2である。
  - LibreOfficeは、各行・下線・画像をそれぞれの`q`で囲み、その`q`を本文・図版groupの`q`（ページ全体または図版の矩形clip）の中に置く。平行移動・scaleの`cm`は内側の`q`にしかない。
  - そのため、原本には「深さ1・CTMの相殺・矩形clip」を同時に満たす境界がない。
- **PR #14の効果**: 両ページのclipは、どれも1つの矩形と証明された（`active-clip`は1ページ971・10ページ988 → 0）。それでも候補が増えなかったのは、clipの下の境界がすべて`q`の内側にあったためである。
- **PR #15の効果**: 深さ1の境界が候補になった。安全でなくなった境界はない。以前の2候補（深さ0）は同じIDのまま残る。

### 10ページの系列評価

新しい評価コード[scope_destination.py](../evaluations/continuation/scope_destination.py)を加えた。評価者が固定したのは次のとおりである。

- **境界**: `boundary-1cb2d3bbed6f7b8618f3d4b1`（offset 10026）。本文の`q 0 0.1 595.2 841.8 re W* n ... Q`の内側で、最後の行のgroupの`Q`の後、対応する`Q`の直前にある。
  - scope: 開く`q`は序数1、対応する`Q`は序数659。
  - clip: certified rectangle `[0.0010867600854683331, 0.10108676008551382, 595.1989132399145, 841.8989132399145]`。
  - CTM: identity（相殺なし）。
- **領域**: `[55,80,385,120]`。6ページの確認済み領域と同じgeometryで、10ページのロゴの左、見出しの上の空き領域である。bbox logと描画で確かめた。
- **対照**: 同じ領域・編集をpage entryへ入れるrun。

| 検証 | 結果 |
|---|---|
| scope境界（run `scope-pr15-windows-2`） | overflow → reopen → second → shorten → regrow → no-op 3回 → 容量拒否が通過（6,264秒）。bindingの`q`・`Q`の改ざん2件も拒否された |
| page entryの対照（run `scope-entry-pr15-windows-2`） | 同じ系列が通過（5,770秒）。scope境界と、全段階で計画glyph・保存後のglyph・MuPDF画素・Poppler画像が一致 |
| 確認時の拒否 | 10ページの深さ2の境界（相殺・clipは証明済み）は`nested-graphics-state-save`、ロゴscopeの候補は`continuation destination extends beyond its inherited rectangular clip` |
| 単一destination（run `pr15-regression-windows`） | 通過（4,505秒）。成果物129件がPR #12 engineのrun `ctm-regression-windows`とbyte単位で一致 |
| 確認済み境界（run `boundary-pr15-boundary-regression-windows`） | 通過（4,511秒）。成果物129件がrun `boundary-ctm-boundary-regression-windows`とbyte単位で一致。6ページの候補は2 → 116 |
| 同一ページ2 destination（run `multi-pr15-multi-regression-windows`） | 通過（7,133秒）。成果物177件がrun `multi-ctm-multi-regression-windows`とbyte単位で一致 |
| 全suite `python -m pytest -q` | 849 passed, 7 skipped, 0 failed（12,427.08秒、外部評価と並行）。856件はPR #15の件数と同じ。skip 7件は外部corpus 5件とAES provider 2件で、どれも環境によるもの |

- **scopeの追跡**: 全保存で、blockは確認したoffset 10026にある。直前は確認した`Q`、直後は対応する`Q`である。
  - 開く`q`は[6,7)のまま、対応する`Q`はblockの長さだけ動き（[13071,13072) → [28883,28884)）、bindingの`scope`がその2つを指す。
  - blockの`Q`の直後は深さ1で境界と同じ状態、対応する`Q`の直後は深さ0でページの初期状態だった。
- **その他**: 詳細は[評価README](../evaluations/continuation/README.md#pr-15-engineでのq--q-scope境界の評価)にある。
  - allocation・生成glyph・font/resource量・生成block byte数は、6ページの評価と同じだった。
  - operator nestingの違反は0、PDF versionは`%PDF-1.4`、region外のPoppler差分は0画素、容量拒否で成果物は作られなかった。
- **目視**: 10ページの上部（300dpi）と4・5・10ページ（100dpi）を全段階で確認した。生成の2行はロゴの左・見出しの上の確認済み領域内にあり、図版・見出し・本文に変化はない。shortenでは領域が空白である。scope境界とpage entryの切出しは28組すべてPNGのbytesまで同じだった。
- **最初の試行**: 評価コードの照合の誤り（再編集したblockの内側の`q ... Q`を想定していなかった）で、2本ともsecondで停止した。engineの誤りではない。照合を直し、新しいrun名で全系列をやり直した。
- **全suiteの実行条件**: sandboxから既定のpytest一時directoryを読めなかった。最初の実行は、全件がfixtureの準備でerrorになった（227 passed、629 errors、失敗した試験はない）。`--basetemp`をrepo内の`tmp/`へ移し、同じsuiteを最初から実行した。`--junitxml`と`-p no:cacheprovider`を加えた。

### 変更

- engine: 変更なし。engineの不具合は見つからなかった。
- 評価コード: [scope_destination.py](../evaluations/continuation/scope_destination.py)を追加した。既存の評価コードは変更していない。
- 公開集計: `scope-destination-summary.json`・`scope-entry-summary.json`を追加した。`summary.json`・`boundary-destination-summary.json`・`multi-destination-summary.json`は、今回の回帰の3 runの集計に置き換えた。
- 資料: 評価README、[確認済みcontinuation](confirmed-continuation.md)、README本体の記述を、深さ1のscopeと矩形clipが外部原本でも確かめられたこと、CTMの相殺は合成PDFのみで確認したままであることに合わせた。

### 残る問題

- **CTMの相殺は外部原本で未確認**: 原本のidentity以外のCTMは、どれも`q`の深さ2にある。
- **block内の`q`の入れ子**: 再編集のたびに、block内の最大の深さがおおむね1段増える（10ページのscope境界で2〜7、page entryで1〜6、6ページも同じ）。
  - 置き換えた文字の非描画operatorを内側の`q ... Q`に残す、既存writerの累積と同じ原因である。
  - PDF 1.xの実装上の目安（`q`の入れ子28段）に、再保存を20数回重ねると近づく。scope境界は基底を1段深くする。
  - 今回の範囲ではengineを変更していない。

### 次の最小の構造障壁

外部原本でCTMの相殺を使うには、`q`の深さ2の境界が必要である。本文・図版groupの`q`と、各行・下線・画像の`q`という、入れ子のscopeの連なりを、各段の`q`と対応する`Q`の証跡として固定することになる。ExtGState（不透明度・blend・soft mask）も残る。

## 2段の`q ... Q` scope chain内の確認済み境界 — 2026-09-27（`b21380c`）

起点はPR #16のmerge commit `b21380cf82c319a710701b05bce3d4096f2418ef`。サブエージェントは使用していない。confirmed page-program boundaryの`q ... Q` scope対応を、深さ1から深さ2へ最小範囲で1段だけ広げた。最大2段の明確に証明された`q ... Q` scope chainの内側で、既存のCTM・clip等の安全条件を満たす境界だけを扱う。任意のgraphics-state stackを扱えるようにしたものではない。契約は[2段の`q ... Q` scope chainの内側](confirmed-continuation.md#2段のq--q-scope-chainの内側)にある。

### 開始時の確認

| 項目 | 結果 |
|---|---|
| HEAD | `b21380cf82c319a710701b05bce3d4096f2418ef`（PR #16のmerge）。作業ツリーはclean |
| engine digest（開始時） | `454686cef09460f76c3daaa064d2765748b74e83c8e6963d323a00cd905b71c5`（45ファイル）。PR #15・#16の値と一致 |
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、PyMuPDF 1.27.2.3、pypdf 6.10.0 |

### 背景

PR #16の外部原本の検査で、LibreOffice原本のidentity以外のCTMの境界（1ページ2、10ページ18）は、どれも`q`の深さ2にあった。相殺と矩形clipは証明されるが、`nested-graphics-state-save`で拒否されていた。今回はこの理由だけを1段緩めた。ExtGState等へは広げていない。

### 方式

```text
outer q
  ...
  inner q
    ...
    confirmed boundary                 q depth 2
    marker q [N cm] BT ... ET Q marker blockは境界の状態だけを保存・復元する
    ...
  inner matching Q                     外側のscopeの中の状態へ戻す
  ...
outer matching Q                       chainの前の状態へ戻す
```

- **証跡**（`enclosing_scope()`）:
  - top-level operatorの`q`/`Q` stack（`graphics_scopes()`）で、境界の後に開いている`q`がちょうど2つであること（interpreterの深さとも一致）。
  - 外側の`q` < 内側の`q` ≤ 境界 < 内側の`Q` < 外側の`Q`であること。各`q`の対応する`Q`はstackから求める。
  - 各段の`q`（とその直前）・`Q`がtext object・marked content・`BX ... EX`・組み立て中のpath/clipの外にあること。
  - 各段の`Q`の直後の状態が、その`q`の直前の状態（`restored_state`）と同じであること。
  - 深さ3以上は`nested-graphics-state-save`、段の交差などは`unproven-graphics-state-scope`で拒否する。
- **authority**: 深さ2の`graphics_state_scope`は、`policy = two-nested-graphics-state-saves`、`depth = 2`、`outer`・`inner`（それぞれ`opening`・`matching`の序数・範囲・bytesのSHA-256と`restored_state`）、`contract`を持つ。
- **boundary ID**: source境界の証跡に、policyと、外側・内側の順に開く`q`・対応する`Q`の序数・終端・SHA-256を加えて作る。内側だけ・外側だけ違う場合も、`q`と`Q`の対応が違う場合も、別のIDになる。
- **revisionごとのbinding**: bindingの`scope`は`{outer: {opening, matching}, inner: {opening, matching}}`で、そのrevisionでの4つの位置を持つ。
  - 保存では、前のbindingの4つの位置を、その保存のmutation mapでそれぞれ写す（`carried_scope()`）。新しいrevisionの構造が置く4つの位置と一致しなければならない。
  - 4つのどれかをmutationが消費すれば、写し先がないので拒否する。別のscopeへ乗り換えない。
  - openでは、bindingの位置と構造、確認時のprogramではauthorityの記録と比べる。位置を除く証跡（bytes・深さ・戻す状態・policy・contract）も毎回再導出して比べる。
  - blockがchainの外へ出ないことを、revisionごとに明示的に確かめる（外側の`q`の終端 ≤ 内側の`q`の先頭、内側の`q`の終端 ≤ block ≤ 内側の`Q`の先頭、内側の`Q`の終端 ≤ 外側の`Q`の先頭）。
- **記録の形の選択**: `outer`・`inner`を持つ記録だけを2段として読む。記録の`depth`で形を選ばない。深さは他の証跡と同じく、再導出した値と比べる。
- **CTM・clip**: どちらの段で設定されたCTM・clipも、境界の状態としてPR #12の相殺・PR #14の矩形clipの証明でそのまま扱う。新しい処理は加えていない。

### 変更

- **`pdfeditor/continuation.py`**:
  - `enclosing_scope()`を最大2段へ広げた。`MAX_SCOPE_DEPTH`・`SCOPE_CHAIN`・`SCOPE_CHAIN_CONTRACT`を加えた。
  - `_scope_levels()`・`_scope_operators()`・`_within()`を加え、`_scope_witness()`・`_scope_location()`・`carried_scope()`・`boundary_id()`・`_graphics_contract()`・`_boundary_value()`を2段に対応させた。
- **変えていないもの**: `content_stream.py`（interpreter）、`mutation.py`、`operator_nesting.py`、`paragraph.py`（writer）、`shared_flow.py`、blockの形と`_block`、CTMの相殺と矩形clipの証明、page-entryの検証・binding、生成fontの寿命、`require_empty()`。
- **engine digest**: `f68d40dde550db069158863c65f61171d3787d67a5c784cd3d2e43d09f211c6a`（45ファイル）。

### 深さ0・1との互換

- **形**: 深さ1の記録（`opening`・`matching`・`restored_state`を直接持つ形）、ID、binding、contractの文言は変えていない。`outer`・`inner`の形を使うのは深さ2だけで、既存の深さ1のsidecarを変換することはない。
- **byte比較**（評価コード外の一時スクリプト。commitしていない）: 同じ試験fixtureから、深さ0（identity・相殺・矩形clip・相殺と矩形clip）、深さ1（4種と、scopeの最後の境界）、page entryの計10系列を作った。各系列で確認 → grow → second → shorten → regrow → no-opを保存し、PR #16のmain（`b21380c`）のengineと今回のengineの出力を、同じ出力先で比べた。
  - 計210ファイルがbyte単位で一致した。PDF 60件（元PDF 10・保存50）、sidecar 50件、report 50件、確認時のflow記録10件、選んだ候補の記録10件、検査記録20件、font 10件である。
  - sidecarは出力先のpathを含むので、2つのengineの出力は同じ出力先で作って比べた。
- **外部原本の検査記録**: LibreOffice原本の1・6・10ページの`inspect_continuation_boundaries(..., include_refused=True)`で、深さ0・1の境界の記録（計976）はPR #16のmainとすべて同じだった。
  - 深さ2の境界は、1ページ357、6ページ255、10ページ186が新しく候補になった。
  - identity以外のCTMの境界は、1ページの2と10ページの12が候補になり、10ページの6は`pending-path`で拒否されたままである。
  - これは検査の記録の比較で、外部原本での系列評価ではない。

### 検証（Windows 11、Python 3.12.14）

| 検証 | 結果 |
|---|---|
| `tests/test_scope_chain_boundary.py`（新規） | 38 passed（下の関連7ファイルの実行に含む） |
| 関連7ファイル（新規、scope、boundary、CTM、clip、continuation、multi destination）、最終engine | 239 passed, 0 failed（4,865.31秒、単一process） |
| 全suite `python -m pytest -q --basetemp=tmp/pytest`（最終engine、`e7f399e`のcode） | 888 passed, 7 skipped, 0 failed（4,981.12秒、単一process、他の実行と並行しない）。895件はPR #16までの856件に、新規38件と`test_boundary_destination.py`の深さ2の例1件を加えたもの。skip 7件は外部corpus 5件とAES provider 2件で、PR #16のWindows実行と同じ |

- **新しい試験**（`tests/test_scope_chain_boundary.py`、38件）:
  - 候補と記録: 深さ2の境界が`outer`・`inner`の両段を持つ候補になること。各段の`q`・`Q`の位置・bytes・stack上の対応、`restored_state`（内側の`Q`は外側のscopeの状態、外側の`Q`はpage levelの状態）。内側のscopeの深さ2の境界がすべて同じchainを持ち、外側のscopeの境界は深さ1の形、page levelの境界はscopeの記録を持たないこと。
  - 13種の境界: 証明できるchain（2段、外側に閉じた兄弟scope、外側で閉じたmarked content・`BX`）、拒否するもの（深さ3、marked content・`BX`と交差する段）、chain内でも拒否する状態（ExtGStateを外側・内側で設定、特異なCTM、多角形のclip、描画モード、text clip）。
  - boundary ID: 基準のchainと、内側の`q`・内側の`Q`・外側の`q`・外側の`Q`・`Q`の対応のどれか1つだけが違う5つのchain、同じ位置の深さ1・page levelの記録が、すべて別のIDになること。
  - authorityとbinding: `graphics_state_scope`・contractの文言・bindingの`{outer, inner}`。
  - lifecycle（identity、相殺、矩形clip、外側の矩形clipと内側の相殺）: fits → grow → second → shorten → regrow → no-op 3回。reopen、authority・作成証跡の不変、2つの`q`は動かず2つの`Q`はblockの伸びだけ動くこと、4つの位置の順序とstack上の対応、blockに`W`・`W*`・`n`・`re`（相殺がなければ`cm`も）がないこと、入れ子の違反0、生成fontの所有、no-opの画素・glyph plan・font再利用。
  - 状態の復元（ContentPageの状態で直接確かめる）: blockの`Q`の直後は深さ2で境界の状態、内側のsuffixの文字も同じCTM・clip。内側の`Q`の直後は深さ1で外側のscopeの状態（fill・stroke・線幅、CTM identity、外側のclip）、外側のsuffixの文字も同じ。外側の`Q`の直後は深さ0でpage levelの状態、その後の文字はidentity・clipなし・pageのfill。
  - 配置（上の4種と、LibreOffice原本と同じ形の外側の矩形clip・内側の平行移動）: 生成glyphの計画のpage座標、保存後のglyph原点、画素が、同じgeometryのpage-entry版と一致すること。chainの内外の既存の描画と領域外の画素が元PDFと同じこと。
  - 内側のscopeの最後の境界: blockの直後が内側の対応する`Q`になり、その後の状態が戻ること。
  - 改ざん: sidecarのauthorityの16種（4つの`q`・`Q`の序数・bytes・終端、段の入れ替え、各段の`restored_state`、policy、contract、深さ1・3、段の削除、記録の削除、`graphics_state_contract`、boundaryの深さ）と、bindingの8種（4つの位置、段の入れ替え、深さ1の形、削除、再封印だけの対照）。programの13種（内側・外側の`q`・`Q`の移動、`Q q`による内側・外側の対応の変更、`Q q`による別の内側・外側のscope、深さ3・深さ1、釣り合わない`Q`、内側・外側の対応する`Q`の後ろへのblockの移動）。
  - 再binding: source slotがchainの前・外側のprefix・内側のprefix・内側のsuffix・外側のsuffix・chainの後にある場合の、grow・no-op。4つの位置のうち、書き直したslotより後ろのものだけが動き、同じchainへbindingすること。4つのどれかを消費するmutationは写し先がなく拒否すること（単体）。
  - rollback: rebind・commitの遅い失敗で何も公開しないこと。
- **既存試験の変更**: 意図した挙動の変化に合わせて4件を直した。`q q ... Q Q`（深さ2）は候補になるので、拒否例を深さ3（`q q q ... Q Q Q`）に差し替えた（`test_boundary_destination.py`・`test_clip_boundary.py`・`test_ctm_compensation.py`・`test_scope_boundary.py`）。`test_boundary_destination.py`には深さ2が候補になる例を加えた。
- **mutationによる確認**（評価コード外の一時スクリプト。engineのfileは変えず、process内で差し替えた）: 5種の欠陥で、新しい試験が失敗した。
  - 深さ3を受け付ける、IDから外側の段を外す、外側の段をmutation mapで写さない、bindingから外側の段を外す、内側の`restored_state`を比べない。
  - 最後の1つでは、改ざんしたsidecarは作成証跡のdigestでも拒否されるが、試験は拒否の理由まで照合するので、scopeの照合が欠けたことを検出した。
- **実行方法**: 開発中は新しい試験と関連試験だけを実行した。全suiteは、engineが最終形になった後に1回だけ実行した。`pytest-xdist`は使っていない。

### 外部評価

今回は実施していない（指示どおり）。既存の単一destination・境界・2 destination・scopeの外部評価も再実行していない。PR #16の原本の深さ2の境界は、実装の目標を決める根拠として使った。上の検査記録の比較は、互換の確認のためのもので、系列評価ではない。

### 検証の所要時間

開発中の検証は、最終engineの関連7ファイルを1回と、途中の部分実行だけにした。時間はどれも実測である（並行は最大2 process）。

| 作業 | 時間 | 結果 |
|---|---|---|
| mainのengineでの互換用の出力 | 10.6分 | 10系列と、LibreOffice原本3ページの検査記録 |
| 既存のscope・boundaryの試験（変更途中） | 21.5分 | 1件失敗。深さ1の改ざんの拒否理由の文言が変わっていた。記録の形を`outer`・`inner`の有無で選ぶよう直した |
| 新しい試験（変更途中） | 15.5分 | 37 passed・1 failed。試験の誤り（保存のループで元PDFを進めていなかった）を直した |
| 失敗した試験の再実行・mutationによる確認 | 約3分 | 該当試験とmutation 5種 |
| 新しいengineでの互換用の出力と、同じ出力先でのmainの出力 | 14.2分・13.9分 | 210ファイルがbyte一致 |
| 関連7ファイル（最終engine） | 81.1分 | 239 passed |
| 全suite（最終engine、1回） | 83.0分 | 888 passed, 7 skipped |

### 残る未対応の状態

- `q`の深さ3以上、marked content・`BX ... EX`と交差するscope
- ExtGState（不透明度・blend・soft mask）
- 多角形・曲線・複数subpath・回転やskewの下のclip、text clip
- 特異・非有限・数値的に不安定なCTM
- text object・marked content・`BX ... EX`・Form XObjectの内側、組み立て中のpath/clip
- 1つの境界への複数destination

### 次の作業と構造障壁

この時点での次の作業は、Windows環境でPR #16の原本の深さ2の境界を直接評価することだった。2026-09-27に実施し、系列は通ったがPoppler同値で未達となった。次は幾何許容値内のCTM残差が生むrenderer差を別のengine修正作業で扱う。候補になった1ページ・10ページのidentity以外のCTMの境界（深さ2・相殺・矩形clip）で、同じページの確認済みの空き領域へ系列評価を行う。構造の障壁としては、`q`の深さ3以上とExtGStateが残る。10ページの下線の`m`・`l`の後の6境界は、組み立て中のpathの途中なので候補にならない（挿入位置として不適切で、緩める対象ではない）。

## 再評価の手順

Windows検証環境で[評価README](../evaluations/continuation/README.md)の手順を実行する。

1. 最新commitを取得し、engine digestと評価コードの`runner_sha256`を公開集計の値と比べる。
2. 原本（SHA-256 `1366587531…a5f3`）、`msmincho.ttc` face 1、`times.ttf`、Poppler、独立pypdfを揃える。
3. 未使用のrun名で`python -m evaluations.continuation.evaluate --run-name <name>`を実行する。
4. overflowのallocation（既存slot 209字、生成slot 36字・2行）を確認し、画像を目視する。
5. engineを修正した場合は、継続テスト、関連テスト、外部評価、全suiteを最終コードで再実行し、公開集計を更新する。
6. 同一ページ2 destinationの評価は、未使用のrun名で`python -m evaluations.continuation.multi_destination --run-name <name>`を実行する。`a-only`で`page6-a`だけ、`b-added`で`page6-b`が加わることと、page-entry順序を確認し、画像を目視する。すべて通った場合だけ`multi-destination-summary.json`として公開する。単一destinationの評価を先に通してから実行する（2026-09-25の評価はこの順で行った）。

外部PDF・派生PDF/PNG・font・本文/glyphログは引き続き公開しない。

## Source CTM basisによるPR #18描画差の修正 — 2026-09-28

起点は`d33b3bf899a0958643b2c975403cd638e8774a3c`。pypdfのparsed binary64値と、ContentPageが合成したbinary32 CTM Mを区別し、既存operator spanから十進`cm`だけをexact rationalへ復元するSモデルを追加した。NはSの逆を既存12桁でserializedし、N×S・N×Mの両方を従来の0.002pt上界で証明する。clipの旧モデルと境界IDは維持し、旧compensated authorityは`needs_confirmation`となる。

engine digestは`fd346b9a1fe37aec8e8c8246e7ed5dbc0a858bdb5bf18c524019261e76878b88`。

| 検証 | 結果・所要時間 |
|---|---|
| 開発中のCTM＋新規回帰 | 51 passed / 1 fixture failure、991.20秒。深さ1の選択対象が2つになるfixtureを修正 |
| 修正後のfractional translation 3件＋scope-chain描画5組合せ | 8 passed、206.06秒。新NはMuPDF・Poppler一致、旧N対照はPoppler各3,499画素差 |
| focused実原本評価 | 567.03秒。boundary overflow＋reopenとpage-entry overflowを各1回。Poppler 5,868→0画素、4 binding・全状態復帰・glyph・領域外差分を確認 |
| 最終full suiteの1回の試行 | 480 passed後に実行プロセスが消失。最終成功結果まで約58.1分。最終summary・JUnitなし |
| 未完了分だけの続行 | 426 passed / 2 skipped、2,600.02秒。同じengineで残り428件を確認し、成功済み480件は再実行しなかった |
| 全908件の集計 | **906 passed / 2 skipped / 0 failed**。単一processでの全suite完走ではない。skipはAES provider不在2件。CTM＋新規52件、Poppler回帰は実行・成功 |

[原因・数値契約・検証の詳細](../evaluations/continuation/README.md#source-ctm-compensation)と[新しい公開集計](../evaluations/continuation/ctm-source-destination-summary.json)を参照。PR #18のfailed summaryは変更していない。実PDFのpage-entry PDF・sidecarは旧出力とbyte一致した。全suiteの初回と続行を含め、重い処理を並列実行していない。

証明しているのはpage-space displacementの上界であり、任意rendererの画素一致ではない。深さ3以上、ExtGState、証明できないclip、pending path等の拒否は維持する。非描画operatorの累積整理にも進んでいない。

## Depth 3専用scopeの追加

起点はPR #19 merge `d5234fe68264adc44918756325746982ab251c5b`。最大scope depthを2から3へ1段だけ拡張した。depth 4以上は`nested-graphics-state-save`で拒否する。上の「次はCTM renderer差」という記述はPR #18時点の履歴であり、その差はPR #19で解消済みである。

engineの変更は`pdfeditor/continuation.py`のscope関連に限定した。CTMのsource operand復元、Nのserialization、N×S/N×Mの上界、clip proof、安全条件の関数群はPR #19と同じsource bytesである。他のengineファイル・writerは変更していない。

- **専用authority**: `policy = three-nested-graphics-state-saves`、`depth = 3`、`outer`・`middle`・`inner`。各levelにopening q・matching Q（ordinal、start/end、bytesのSHA-256）とrestored_stateを持つ。contractもdepth 3専用。depth 1の直接形、depth 2のouter/inner形は維持する。
- **identityとbinding**: IDに専用policyと6 operatorのidentityを順番に入れる。bindingもouter/middle/innerの各opening/matchingを持つ。確認元のoperator identity、各revisionの実stackの対応・順序・状態、直前bindingからMutationProgramで写した6範囲を照合する。operatorを消費するmutationは、同一bytes・op anchorのある置換でも拒否する。
- **不正な形**: depth/policy/contractとlevel集合の不一致、middle欠落、depth 2へのmiddle追加、levelやmatching Qの入れ替え、depth 2への偽装を拒否する。確認元の順序も検査し、全levelの状態とbytesが同一で、IDを再計算した改ざんでも拒否する。
- **状態復帰**: generated Q → depth 3のconfirmed state、inner Q → depth 2、middle Q → depth 1、outer Q → depth 0。`_state()`の全15field（CTM/clip、fill/stroke、other、opacity、font/text state）を比較する。
- **組合せ**: identity、fractional translation、rectangular clip、fractional translation＋clip。middleでclip、innerで`1 0 0 1 158.2 662.8 cm`を設定する。NはPR #19の`1 0 0 1 -158.2 -662.8 cm`、toleranceは0.002ptのまま。
- **synthetic系列**: reopen、second、shorten、regrow、no-op 1回、font所有と再利用、nesting違反0、rollback。source slotをouter q前・各3段のprefix/suffix・outer Q後の8箇所に置き、source編集でずれる6 operatorを追跡する。4組合せでpage-entryとの計画glyph・保存origin・MuPDF画素を比較する。
- **外部評価**: 今回は実施していない。Windows LibreOffice、single、depth 0・1・2の外部系列も再実行せず、自然なdepth 3候補の調査や原本の加工もしていない。PR #18のfailed summaryとPR #19のfocused summaryを保持する。

検証のraw log・JUnit・test開始/完了ID・process exit・engine digestは`tmp/depth-three/`に保存する。full suiteはengine最終形で1回だけ起動し、異常終了した場合は最後のtestとprocess/system情報を記録する。pytest-xdistとサブエージェントは使用しない。

関連テストの初回は2026-09-29 00:16 JST頃に記録が途切れた。最後の完了は`test_the_block_in_a_scope_draws_where_page_entry_does[clip]`、次の`[compensated-clip]`はsetup完了までである。再開時にはpytest・監視processとも残っておらず、exit code・最終JUnitは取得できなかった。Windowsは00:16:39にlogoffとsleepを記録しているが、対応するPython/pytestのApplication Errorは見つからず、終了原因は確定できない。この時点でfull suiteは未起動だった。

初回の成功53件は繰り返さず、fixture不備で失敗したsource編集8件と未完了分だけの計196件を続行した。sourceの8位置のfixtureは、editable sourceのinline stateをローカルな`q ... Q`内で既定値に戻し、各scopeに設定した異なるfont/text stateと分離して修正した。engineの安全条件は緩和していない。開発初期の別のfixture修正では、壊したq/Qがwitness検証より前のContentPage構築で拒否される場合も期待した例外として確認するようにした。

最終検証は2026-09-29に完了した。集計・入力系列のhash・実行環境・中断時の情報は[depth-three-validation.json](depth-three-validation.json)に保存した。

| 検証 | 結果・所要時間 |
| --- | --- |
| 新しいdepth 3 synthetic regression | 38件成功。4組合せの描画と全状態復帰、8位置のsource編集、6 operatorのidentity・改ざん・消費拒否、系列・rollbackを確認 |
| 関連7ファイル | 249 passed / 0 skipped。初回成功53件＋未完了分196 passed（3,991.13秒）。初回記録は約18分時点で途切れ、続行は約66.5分 |
| PR #19とのdepth 0・1・2互換比較 | confirmed → grow → second → no-op。9組の保存PDF/sidecarと3組の確認時PDF/状態snapshotがbyte一致。authority・boundary ID・binding・page program・generated blockも一致し、旧sidecarを新engineでreopenできた。比較元生成126.09秒、変更後生成・比較146.44秒 |
| 最終full suiteの1回の実行 | **945 passed / 2 skipped / 0 failed**、7,832.78秒（約130.5分）。2026-09-29 19:51〜22:01 JST、中断なし、exit code 0。947件を1回のsessionで収集・実行し、開始時と終了時のengine digestは一致 |

full suiteは`.venv\Scripts\python.exe -m pytest -q --basetemp=tmp/pytest --junitxml=tmp/depth-three/full.xml`で実行した。skip 2件はAES-128・AES-256のprovider不足によるもので、今回のscope回帰はskipしていない。既存source CTMのPoppler回帰も関連実行・全件実行の両方で通過した。外部評価の再実行は行っていない。

最終engine digest（`pdfeditor/*.py`のファイル名別SHA-256をsortしたJSONのSHA-256）は`24b7dfb30a79b25e1c0ee7c5bb16a16aedd4de35722d2bf3c738e678648a196a`。Python 3.12.14、PyMuPDF 1.27.2.3、pypdf 6.10.0、Windows 11で検証した。重い検証は関連テストの続行 → byte比較 → full suiteの順に実行し、full suiteは再実行していない。

次の最小の構造障壁はdepth 4。ExtGState、未証明clip、pending path、text object・marked content・BX/EX・Form XObject内も未対応のままである。非描画operatorの累積整理には進んでいない。


## PR #20後の原本全ページ調査

起点は`44c4ac991dd841c6231a9e24ea891747fa471bfb`。engine digestはPR #20と同じ`24b7dfb30a79b25e1c0ee7c5bb16a16aedd4de35722d2bf3c738e678648a196a`で、`pdfeditor/`は変更していない。原本SHAは`13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`、reviewed providerのMS明朝・Timesも既存評価と同じhashだった。

`inspect_continuation_boundaries(..., include_refused=True)`を各ページ1回だけ実行した。元のdecoded /ContentsのSHAとpypdfが独立に数えた各operator後のq depthも一致した。全10ページとも最大depthは2。14,475 operators間の14,465境界はdepth 0が20、depth 1が3,789、depth 2が10,656で、depth 3・4以上は0件。safeは3,016、refusedは11,449だった。

**PR #20のdepth 3対応はsyntheticで成立しているが、この外部原本には自然なdepth 3境界が存在しない。** 条件Aで完了し、focused評価・destination選定・MuPDF/Poppler比較・4段階の状態復帰・negative controlは対象なし。疑似PDFや加工原本は作っていない。page /Contents境界の調査であり、Form XObject内部へ契約を広げるものでもない。

| 拒否理由 | 境界数（重複あり） | その理由だけの境界数 |
| --- | ---: | ---: |
| text object内部 | 8,575 | 8,505 |
| pending path | 2,860 | 2,840 |
| pending clip | 20 | 0（全てpending pathと重複） |
| text rendering mode（全てTr 2） | 84 | 14（残り70はtext object内部と重複） |
| ExtGState・transparency・未証明/非矩形clip・marked content・BX/EX・CTM proof failure・depth 4以上 | 各0 | 各0 |

pending pathの2,860件は162の連続区間で、全てpath完了直後にsafe境界があった。Tr 2だけが理由の14件は全てET直後・Q直前で、Q後は同じprefix/suffix paint数のsafe境界になる。境界数は独立した描画object数ではなく、safe candidateも空き領域の承認ではない。

今後の候補は、(1) 完了済みpath・text・scopeの既存safe境界を選ぶ支援、(2) 完了前の挿入が本当に必要な場合のpending-path契約調査、(3) 既存Q後の代替境界と比較したTr 2隔離契約の必要性調査。path描画を越えればz-orderが変わるので、領域と境界の明示確認が必要である。今回は実装しない。この原本ではdepth 4やExtGStateの頻度は0であり、上のPR #20時点の「次の最小構造障壁」を、そのまま次の開発優先度とはしない。

全ページ走査26.8秒、helper単体確認8 passed（1.46秒）。full pytest suiteは再実行せず、PR #20の945 passed / 2 skippedは過去の結果として保持する。single/depth 0/1/2の外部系列も再実行していない。[公開summary](../evaluations/continuation/depth-three-inspection-summary.json)に全ページhistogram・環境・input hash・重複集計・scope上限とfocused評価未実施を記録し、全境界dumpは`evaluations/continuation/runs/depth-three-inspection-windows/`へ置いた。

## PR #30後のWindows実原本canonical block検証 — 2026-10-01

起点`8178462d84ade7241b536a533756149a5fb39754`、engine digest `dddbdc19f611fcbef0001e93c821955924d5341c25b63401c37c8e5d267a2a4c`。PR #30時点の「実PDFでの外部検証は未実施」は当時の履歴として保持し、その後の結果をここに追記する。未加工LibreOffice原本・Windows既存providerの指定SHAはすべて一致し、`pdfeditor/`と原本を変更していない。

page-entryと既存のconfirmed page-program boundaryの正式7段階を通過。各系列で**grow = regrow = noop1 = noop2 = noop3のgenerated block bytesが完全一致**した。activeは3,044 bytes /259 operators /36 painting shows、secondは3,224 /273 /38、dormantは310 /33 /0。activeのtext objectは1つ、dormantはtyping slot＋body/latin witnessの3つで、nesting違反0。slot/destination/creation binding/markerを保持し、boundaryのprefix 17,602 bytes・suffix 8,714 bytesも原本のままである。

**source slot側は未解決**。no-opごとに4ページ+4,420 bytes/+368 operators、5ページ+14,312 bytes/+1,117 operators、合計+18,732 bytes/+1,485 operatorsが残る。6ページgenerated分とその他は0。mutation owner別のlength deltaをpage単位のdecoded program length deltaと照合しており、span全体のbyte-by-byte attributionではない。圧縮PDF sizeや単なる残差から分類していない。

両系列のno-op 3回は全10ページでMuPDF/Poppler pixel diff 0。独立Unicode・glyph plan/CID/GID/W・source paint/画像/annotation/元fontを既存監査で照合し、Type0 4・所有root 4・font graph 24・ページ別所有alias 1/2/1を維持した。公開historical evidenceは書き換えず、PR本文との数値差の原因を未検証のまま断定しない。

grow直後・dormant中の追加no-opも両系列でbytes不変・全ページ画素一致。保存glyph原点の直接照合は最大約0.0000244141ptで、no-op間と2 destination間のdigestも一致した。残っていた旧growの部分rawとの全10ページ比較も両rendererで一致するが、その旧rawにはengine digest・完了summary・所有font記録がない。旧page-entryとboundaryの抽出順差に関する比較設定の初回失敗と修正は、summaryの`evaluation_attempts`に記録した。

正式系列の所要時間はpage-entry 1,937.078秒、boundary 2,462.328秒。helper小test 4 passed（最終確認4.71秒）、full suiteは実行していない。追加監査は10月1〜2日に実施した。詳細・追加no-op・過去rawとの比較・環境hashは[評価README](../evaluations/continuation/README.md#pr-30-generated-block-canonicalizationのwindows実原本検証--2026-10-01)と[公開summary](../evaluations/continuation/generated-block-canonical-summary.json)を参照。

Claude Opus 5.5の[独立レビュー](https://github.com/YanTKYS/pdfengine/pull/31#pullrequestreview-5391061158)はhead `946a94adeb74fbccf6f801a247ebf886d0388fe2`に対して**PASS**。evaluation code・公開summary・既存helperとの整合、generated/source累積の解釈、renderer/glyph/font/authorityの証拠とhistorical comparisonの制限を確認した。engine contractの再実装はなく、Draft → Readyは可との結論。Windows raw評価・helper testはレビューで再実行していない。

## Source slot ownership contractの調査 — 2026-10-02

起点`8c4e904d1e7d51e4ba1d8c7e693eaad816059f0f`。runtime/schemaの変更なし。
既存shared-flow syntheticのgrow→noop1→noop2→noop3で、sourceは毎回page 1 +2,175 bytes/+215 operators、
page 2 +954/+88 (合計+3,129/+303)を再現。second→noop、empty→noop→regrow、retained source fontと
部分show/複数BT、anchor別paintの累積も測定した。current PDF/sidecarでの別process reopenは成功するが、
sourceの過去mutation_mapは永続化されず、古いgenerated nonpainting bytesを元sourceと区別して削除する証拠はない。

**IMPLEMENTATION READY (既存shared-flowのtext source slotに限定)**。
初回source非描画化とsuffix復元bridgeを固定して残し、新しく出すtext/empty/style witnessだけを
stable marker islandとcurrent-only ownership recordで所有する契約を推奨する。
新形式の不整合はfail closed、legacyは従来modeか明示再確認であり、自動migration/旧履歴掃除はしない。
anchor付きgeneral editableへの展開はNOT READYで、別paint islandとdormant anchorの証明が必要。
既存shared-flowのanchor拒否を維持する。source累積が既に解消されたという意味ではない。

新helper 4 passed、root-cause focused 12 passed。full suite・実原本再評価は未実行。
[設計とacceptance matrix](source-slot-rewrite-ownership.md)、
[synthetic summary](../evaluations/continuation/source-slot-ownership-summary.json)、
[実行方法](../evaluations/continuation/README.md#source-slot-ownership調査)を参照。

Claude Opus 5.5の独立レビューはhead `bd56485075bd5f55c08380ae23658c4807fec426`に対して
**PASS — IMPLEMENTATION READY (shared-flow text source slotに限定)**。current-only ownership再証明、
固定bridgeのTm/Tlm復元、複数slotの順序非依存、font ownership分離、legacy/fail-closed、anchor scopeを
コードと照合した。summary数値を再計算し、helper 4 passed・focused 12 passedを再確認した。
marker認識・body grammar・entry digest不変量・matrix補足・v1 scopeの5点は実装PRで固定する
non-blocking事項として[設計§10](source-slot-rewrite-ownership.md#10-独立レビュー結果)に記録した。

## Source slot ownershipの実装 — 2026-10-03

起点`9cb6e0704f7caf062cbaf1460f0184409c344753`。新規shared-flow v2のtext source slotを、
初回だけ固定するsource residue/bridgeと、current text/style bodyのstable marker islandに分離した。
current PDF＋sidecarからmarker・grammar・glyph/witness包含・entry contextを再証明し、owned bodyだけを
1 mutationで置換する。v1はv1のordinary rewriteを維持し、自動upgradeしない。
content ownershipは既存generated font/continuation authorityと独立している。

synthetic first/noop1/noop2/noop3はbody 360 bytes/42 operators、page 654 bytes/51 operatorsで一致し、
no-op growthは0。second→noop、empty→noop→regrowも固定bridgeとidentityを保ち、MuPDF/Popplerでno-op画素一致。
複数slotのfinal rebind、retained font、CTM/q/矩形clip、scope/tamper拒否、continuation共存、rollbackを検証した。
詳細・実record・SHAは[実装結果](source-slot-rewrite-ownership.md#11-実装済みsynthetic-contract--2026-10-03)、
command/test結果は[評価README](../evaluations/continuation/README.md#source-output-canonical実装--2026-10-03)を参照。
PR #32のhistorical evidenceは保持する。general editableはNOT READY。
PR #31実原本は依頼どおり未評価で、page 4/5のsource-output-v1 eligibilityは未確認。

Claude Opus 5.5の独立レビューはhead `8d72d3b6233ea7ea16328ec56e4f46560730cd47`に対して
**PASS — READY FOR WINDOWS EXTERNAL VALIDATION**。current-only ownership再証明、gap限定marker、
構造grammar、単一formatter、固定bridge、entry context不変量、runtimeでのsame-show拒否、font ownership分離、
v1非upgrade、continuation共存、rollbackと、更新した4 test caseの妥当性をコードと照合した。
新規・更新testを再実行し72 passed / 1 skipped（Poppler未導入）。non-blocking 5件は
[設計§12](source-slot-rewrite-ownership.md#12-実装の独立レビュー結果)に記録した。


## PR #33後のWindows source-output external validation — 2026-10-03

起点・評価HEAD `9d70d8c6de7736aba194573ea482dcde7ed9e31a`。
**PASS — WINDOWS EXTERNAL VALIDATION**。PR #31と同一SHAのLibreOffice原本・両fontを使用し、
page 4/5はruntimeのinitial_contextで両系列ともeligibleだった。元PDFからfresh shared-flow v2を確認し、
既存page-entry / confirmed boundaryの2系列を各10 saveで検証した。
first→first-noop、second→second-noop、dormant→dormant-noop、regrow→noop1→noop2→noop3の
source body/page programはbyte identical。source no-op増分は両pageとも0 bytes / 0 operatorsで、
PR #31の合計+18,732/+1,485から解消した（このexact input/scenarioの範囲）。
全10pageのMuPDF/Poppler no-op差分0、glyph/resource/font・continuation authority・nesting監査も通過した。
scale CTM、creation-time empty、private flag拒否、sidecar-loss再confirm拒否、非隣接markerの補足も通過。
focusedは73 passed、最終helper 9 passed（unique 75、skipなし）。full suiteは依頼どおり未実行。
engine digestは`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`で不変。
collection末尾のcp932表示エラー1件だけ修正し、経緯をsummaryへ保存した。runtime変更・PDF再実行なし。
[測定値と制限](source-slot-rewrite-ownership.md#13-windows-external-validation--2026-10-03)、
[再現command](../evaluations/continuation/README.md#source-output-windows-external-validation--2026-10-03)、
[compact evidence](../evaluations/continuation/source-output-external-summary.json)を参照。
general editableは引き続きNOT READY。


## General anchored paint ownership design — 2026-10-03

Base `0e468ff7fd3abd76612b5de6cffd2444f8135a2f` (PR #34). **NOT READY**.
Current ordinary anchored no-ops add 2,028 bytes / 192 operators per save:
text +1,784/+172, decoration +244/+20. Current fills remain three underlines
plus the fixed background; old path construction, n and q/Q remain unowned.
Full empty is refused; deleting one disjoint anchor range drops its group
identity, and reinsertion does not restore it. Nonidentity CTM payloads change
on every no-op despite zero MuPDF/Poppler pixel difference.
Recommend separate anchor-group paint islands plus persistent dormant recipe
records. Current path relations do not prove authorship or byte-envelope
ownership. Canonical geometry, zero-paint revival and dormant boundary proof
remain gates; ordinary _source_output guard and legacy behavior stay intact.
Ten synthetic series: 29 successful saves, 19 fresh-process re-edits, one
empty refusal, 16 dual-renderer no-op comparisons passed. Six helper/guard
tests plus 28 existing focused tests passed; no full suite/external original.
Runtime digest remains d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea.
[Contract, classification and acceptance matrix](anchored-paint-ownership.md),
[reproduction](../evaluations/anchors/README.md),
[compact evidence](../evaluations/anchors/paint-ownership-summary.json).

Claude Opus 5.5の独立レビューはhead `ef43c729c9875ebbb0980a899e0ab68344a2f60a`に対して
**PASS — NOT READY CONFIRMED**。member/authorship分離、非描画residueのAMBIGUOUS分類、group island案、
B1（float32 rendered geometry→再推定のfeedback loop）、B2（explicit revival）、B3（初回位置保持で閉じ得る）を確認し、
追加blocker **B4（page単位marker inventoryと複数editable sidecar）**を記録した。focused 34 passed、
full suite・外部原本は未実行。次PRはB1–B4を閉じるdesign/evidence PR。
[レビュー詳細](anchored-paint-ownership.md#14-独立レビュー結果)。

## Anchored paint contract scope reduction — 2026-10-04

Base `fdba3dcb44b0fe4010a3ea1fc986b3aac83da2e6` (PR #35 merge verified).
**NARROW V1 / NOT READY**. Select active group islands only: full empty stays
refused; a vanished group terminates its verified complete block and record;
no dormant/revival. Limit independent paint ownership to **one editable per
PDF document**, since whole-PDF SHA invalidates sibling sidecars even across
pages. No automatic rebase/adoption of unknown markers.

Fixed creation recipe + exact source-decimal CTM removes the paint feedback
loop, but planned glyph origins/advances themselves change on first/change
to noop. New blocker **B1-L**: the left-aligned retained-glyph shaper substitutes
adjacent MuPDF trace origin differences for metric-based advances. Even the
same Courier 600-width/Tf12/Tz100 space changes planned advance after reopen.
Both identity and scale CTM prototype byte gates fail at first/change→noop;
noop1–3 are stable. Eight current-runtime MuPDF/Poppler comparisons have zero
pixel differences, which does not establish byte canonicality.

Pure numeric/policy models, 14 read-only boundary probes, current A/B stale
sidecar evidence, explicit scope/record/context/termination/fail-closed and
acceptance matrices are recorded in [design §15](anchored-paint-ownership.md#15-b1b4-design-gates--2026-10-04).
Next scope is B1-L layout-authority design/evidence, not paint runtime implementation.
11 focused tests passed, runtime unchanged, no full suite or external original.
[New summary](../evaluations/anchors/paint-contract-summary.json) and
[reproduction](../evaluations/anchors/README.md#pr-35-design-gates--2026-10-04).
