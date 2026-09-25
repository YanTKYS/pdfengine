# Continuation開発の再開地点 — 2026-09-24

**現状**: 外部原本の系列評価まで完了した。その後、再保存で生成fontが累積する問題を修正し、再評価した。さらに同一ページの複数destinationへ拡張し、その最終engineで、単一destinationの再評価と同一ページ2 destinationの評価を外部原本で完了した（2026-09-25）。結果は「外部原本評価の完了」「生成fontの寿命」「同一ページの複数destination」「PR #8の外部原本評価」の節を参照。以下の各節は、その時点の記録として残す。

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
  - MuPDF・Poppler・pypdfは受理し、画素・抽出・照合に影響はなかった。今回は変更していない。
- **生成blockの増加**: 置き換えた文字の非描画operatorにより、生成blockは保存ごとに増える（`page6-a`は1,419 → 19,537 byte）。単一destinationと同じ既存writerの性質である。

### 次の最小の構造障壁

変わらない。page-program先頭以外のcontent-stream境界へ、独立した挿入authorityを与えることである。途中の境界では、その位置で有効なCTM・clip・ExtGState・text stateと、後続paintとの順序を、境界ごとの証跡として確認する必要がある。

## 再評価の手順

Windows検証環境で[評価README](../evaluations/continuation/README.md)の手順を実行する。

1. 最新commitを取得し、engine digestと評価コードの`runner_sha256`を公開集計の値と比べる。
2. 原本（SHA-256 `1366587531…a5f3`）、`msmincho.ttc` face 1、`times.ttf`、Poppler、独立pypdfを揃える。
3. 未使用のrun名で`python -m evaluations.continuation.evaluate --run-name <name>`を実行する。
4. overflowのallocation（既存slot 209字、生成slot 36字・2行）を確認し、画像を目視する。
5. engineを修正した場合は、継続テスト、関連テスト、外部評価、全suiteを最終コードで再実行し、公開集計を更新する。
6. 同一ページ2 destinationの評価は、未使用のrun名で`python -m evaluations.continuation.multi_destination --run-name <name>`を実行する。`a-only`で`page6-a`だけ、`b-added`で`page6-b`が加わることと、page-entry順序を確認し、画像を目視する。すべて通った場合だけ`multi-destination-summary.json`として公開する。単一destinationの評価を先に通してから実行する（2026-09-25の評価はこの順で行った）。

外部PDF・派生PDF/PNG・font・本文/glyphログは引き続き公開しない。
