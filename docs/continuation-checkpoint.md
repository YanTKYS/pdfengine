# Continuation開発の再開地点 — 2026-09-24

**現状**: 外部原本の系列評価まで完了した。その後、再保存で生成fontが累積する問題を修正し、再評価した。さらに同一ページの複数destinationへ拡張し、その最終engineで、単一destinationの再評価と同一ページ2 destinationの評価を外部原本で完了した（2026-09-25）。その後、writerがtext object内に`q`/`Q`を出していた問題を直し、出力のPDF versionを元PDFと同じにして、両方の外部評価をやり直した。さらに、callerが確認したpage levelの安全なoperator境界を、2つ目の挿入authorityにした。結果は「外部原本評価の完了」「生成fontの寿命」「同一ページの複数destination」「PR #8の外部原本評価」「PDF operator nestingの正規化」「確認済みpage-program境界」の節を参照。以下の各節は、その時点の記録として残す。

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

以下はすべて最終engine（digest `fca1e014…a731`）の結果である。

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

## 再評価の手順

Windows検証環境で[評価README](../evaluations/continuation/README.md)の手順を実行する。

1. 最新commitを取得し、engine digestと評価コードの`runner_sha256`を公開集計の値と比べる。
2. 原本（SHA-256 `1366587531…a5f3`）、`msmincho.ttc` face 1、`times.ttf`、Poppler、独立pypdfを揃える。
3. 未使用のrun名で`python -m evaluations.continuation.evaluate --run-name <name>`を実行する。
4. overflowのallocation（既存slot 209字、生成slot 36字・2行）を確認し、画像を目視する。
5. engineを修正した場合は、継続テスト、関連テスト、外部評価、全suiteを最終コードで再実行し、公開集計を更新する。
6. 同一ページ2 destinationの評価は、未使用のrun名で`python -m evaluations.continuation.multi_destination --run-name <name>`を実行する。`a-only`で`page6-a`だけ、`b-added`で`page6-b`が加わることと、page-entry順序を確認し、画像を目視する。すべて通った場合だけ`multi-destination-summary.json`として公開する。単一destinationの評価を先に通してから実行する（2026-09-25の評価はこの順で行った）。

外部PDF・派生PDF/PNG・font・本文/glyphログは引き続き公開しない。
