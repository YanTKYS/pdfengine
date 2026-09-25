# 確認済み空き領域へのcontinuation評価

**外部原本で評価済み（2026-09-25、PR #8の最終engine）**。engine digest `564da875826429f82fc018f6f856d10af191970f473d4bc0b7c0c481040e44a1`で、次の2本を同じWindows検証環境で実行し、どちらも全段階と容量拒否が通った。

- **単一destination**（run `pr8-single-windows`）: PR #7以前から成立していた系列を再実行した。7保存のPDF・sidecarが、PR #7のengineのrun `font-lifecycle-windows-3`とbyte単位で一致した。[公開集計](summary.json)はこのrunの集計である。結果は[下記](#pr-8-engineでの単一destination再評価)。
- **同一ページ2 destination**（run `multi-pr8-windows`）: 確認済みの6ページ領域を評価者が2つのregionへ明示分割した。逐次生成・同時生成・reopen・re-edit・shorten・regrow・no-opを完走した。[2 destinationの公開集計](multi-destination-summary.json)はこのrunの集計である。結果は[下記](#外部原本の結果)。

どちらも単一外部原本の1 paragraphと、評価者が確認した1つの空き領域に対する境界評価である。一般PDFの成功率や、任意のPDFで複数destinationが動くことを示すものではない。PR #7・PR #6のengineでの結果は[履歴](#pr-7-engineの結果)として残す。

既存corpusのLibreOffice移行資料を使用する。対象は前段階と同じ4/5ページの混合書式paragraph、生成先は目視確認した6ページ上部左側の空き領域`[55,80,385,120]`。右上の図版は固定・保護する。領域・描画順序の判断は評価者の明示指定であり、engineによる意味推定ではない。

## 実行に必要なもの

既存評価と同じWindows検証環境を前提とする。代替font、別PDF、別rendererでは実行しない。fontは再配布しない。

| 項目 | 必要なもの |
|---|---|
| Python | 3.12以上。`requirements.lock.txt`と`pip install -e .` |
| 原本 | `evaluations/realpdf/corpus/lo_migration_ja.pdf`、SHA-256 `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`（[取得元](https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf)）。hashが異なれば停止する |
| 本文provider | `C:/Windows/Fonts/msmincho.ttc`のface 1 |
| Latin provider | `C:/Windows/Fonts/times.ttf` |
| Poppler | `evaluations/realpdf/evaluate.py`の`DEFAULT_POPPLER`（`pdftoppm.exe`） |
| 独立抽出 | 同じく`DEFAULT_PYPDF`のPython（pypdf導入済み） |

## 実行

```powershell
.\.venv\Scripts\python.exe -m evaluations.continuation.evaluate --run-name <未使用のrun名>
```

既存のrun名は拒否される。PDF・PNG・本文・glyphログは`runs/<run名>/`だけに保存し、Gitには入れない。

## 自動照合

- 開始時: Poppler・独立pypdfの存在と原本hashを確認する。providerは、[story_styles公開集計](../story_styles/summary.json)で評価者が確認したものと、file名・face・SHA-256・variationsまで一致しなければ編集前に停止する。同名fontであることだけでは通さない。
- source no-op: 4/5ページのsource slotの同文replayが、MuPDF全画素・Poppler全画素・独立Unicodeで元PDFと一致する。
- 各保存（overflow、second、shorten、regrow、no-op 3回）:
  - 事前planと実行planが一致する。
  - `open_shared_flow`で復元でき、確認契約hashが不変である。
  - slotは既存2つと生成1つの計3つ。生成slotを作るのはoverflowだけで、以降は作成証跡を変えずに同じslotを使う。
  - shortenでは生成slotが`occupancy=None`になり、文字を描かない。
- 各保存の監査:
  - 編集した4〜6ページで、Poppler差分が確認済み領域（1pt余白込み）の外に出ない。
  - それ以外のページはMuPDF全画素が一致する。
  - 独立pypdfで全ページのUnicodeを照合する。
  - CID/GID/`W`とfont対応を確認する。
  - 元font resource、text以外のpaint、画像、annotationが変わらない。font指紋の比較で除外するのは、この保存の計画glyphが使うaliasと、直前revisionの検証済み記録（`generated_fonts`）がpdfengine生成と証明し、この保存が置き換えたaliasだけである。
  - font inventory（[resources.py](resources.py)）: 各ページの`/Font`は、元PDFと同じaliasで同じ書込値の元fontと、記録で所有を証明した生成fontだけからなる。記録のないfontが1つでもあれば停止する。
  - PDF byte数、4〜6ページの`/Font`数、ページごとの生成font数、文書全体のType0 font数、所有する生成fontの数とgraph object数、6ページの生成block byte数、aliasごとの結果（added / replaced / reused）を記録する。
- no-op（regrowの後に3回。各回は直前の保存と比べる）:
  - 全ページでMuPDF・Popplerの全画素が一致する。
  - allocation、paragraph・style・destinationの記録、slotのidentity・行geometry・alignment・inline style（tracking・riseを含む）が変わらない。
  - 計画glyphのUnicode・GID・origin・size・advance・code・CID・`W`幅が直前の保存と一致する。
  - font/resource数（`/Font`数、生成font数、Type0数、生成graph object数）が変わらず、全aliasが`reused`（新しいfont objectを書かない）で、生成font記録が変わらない。
- 容量不足: 最終状態へ160字を加えると確認済み容量（約271字）を超え、最終PDF/sidecarを公開せずに拒否する。拒否されたことだけでなく、理由が確認済みregionを使い切ったこと（`paragraphs exceed all explicitly confirmed shared regions`）まで照合する。

## PR #8 engineでの単一destination再評価

起点は`4a1220b`（PR #8のmerge）。サブエージェントは使用していない。engine・評価コードは変更していない。PR #8でMutationProgramとcontinuationを拡張した後も、従来の単一destination経路が変わっていないことを外部原本で確かめた。

| 項目 | 内容 |
|---|---|
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、lockfileの版（PyMuPDF 1.27.2.3 / pypdf 6.10.0） |
| 独立tool | Poppler `pdftoppm` 26.07.0、独立pypdf 6.10.0（Python 3.12.14）。既定pathのまま |
| engine | digest `564da875…44a1`（44ファイル）。PR #8の値と一致 |
| 評価コード | `evaluate.py` SHA-256 `dd6d5329…f402`（PR #7から不変） |
| 原本・provider | 原本SHA-256 `13665875…a5f3`。providerはstory_styles公開集計（`c2329afa…c7c2`）とfile名・face・SHA-256・variationsまで一致 |
| 所要時間 | 3,927秒（全段階と容量拒否） |

- **結果**: source no-op、overflow、reopen、second、shorten、regrow、no-op 3回、容量拒否がすべて通った。
  - 集計のうち`environment`以外（source replay、各段階のallocation・監査・resource量、容量拒否の理由）は、PR #7のengineでの集計と完全に一致した。
  - 違いはengine digestと、PR #8で変わった5ファイル（`continuation.py`、`mutation.py`、`paragraph.py`、`shared_flow.py`、`transaction.py`）のhashだけである。
- **allocation**: overflowは既存slot 209字（51+158）、生成slot `continuation-0ca851c7…`へ36字・2行。regrowは同じallocation・同じ生成slotへ戻った。
- **byte一致**: 7保存（overflow〜no-op 3）のPDFとsidecarが、PR #7のengineのrun `font-lifecycle-windows-3`とbyte単位で一致した。Poppler 144dpiの監査画像もすべて一致した。
- **従来形式**:
  - 各保存のsidecarで、destinationとbindingは`page_entry_order`を持たない。
  - 生成slotの作成mutationは`start = end = 0`で、`insertion_order`を持たない（順序付きinsertionではない）。
  - 生成blockはprogramのoffset 0にあり、その後ろに元の6ページprogramがbyte単位でそのまま続く。
  - 計画にだけ、新しい`page_entry`記録`{order: null, offset: 0, preceding: [], following: []}`が加わる。sidecarには保存されない。
- **no-op 3回**: 全10ページでMuPDF・Popplerの全画素が一致した。記録・slot・計画glyph 245個の全fieldが一致し、全aliasが`reused`だった。Type0 4、所有する生成font 4（graph object 24）、4/5/6ページの`/Font` 8/9/8は変わらない。
- **容量拒否**: `paragraphs exceed all explicitly confirmed shared regions`で拒否した。`capacity.pdf` / `capacity.json`は作られていない。
- **目視**: overflow・second・shorten・regrowの4〜6ページとno-opの全10ページを確認した。Poppler 144dpiの監査画像に加え、4〜6ページの編集領域は300dpiの切出しでも見た。欠陥はなく、観察内容はPR #6時点の目視と同じだった。

補助確認として、保存済みの成果物を一時スクリプトで読み直した。これは評価コード外の確認で、スクリプトはcommitしていない。

- 各保存の6ページblockをpypdfで分解し、`q BT … ET Q`が閉じること、許可したoperatorだけを含むこと、`Tf`がslot所有のaliasだけを選ぶことを確認した。
- blockだけを描くページ複製をMuPDF・Popplerで描画した。インクは確認済み領域（1pt余白込み）の内側にあり、shortenでは何も描かない。
- 独立pypdfで抽出したblockの文字は、そのslotの文字と一致した。

## PR #7 engineの結果

run `font-lifecycle-windows-3`（2026-09-25）。起点は`f7fa600`（PR #6のmerge）。engineは生成fontの寿命を修正した（[契約](../../docs/confirmed-continuation.md#生成fontの寿命)）。engine digestは`f23c2f08d3e4407180b63d0a888187199ca9d6c765b6d1c7d2cc5eb2c2a0b4c0`、評価コードは`dd6d53295ac82b96baee723bf41e2b11044453f452e31783a44a7b47d3b4f402`である。このrunの集計（SHA-256 `3ebcddd3…c09c`）は、PR #8 engineでの再評価の集計に置き換えた。環境、原本hash、provider照合は下のPR #6時点と同じである。全段階と容量拒否は2,868秒で、全suiteと並行して実行した。下表の「現行」の値は、PR #8 engineの再評価でもbyte単位で同じである。

これに先立つrun `font-lifecycle-windows-2`は、docstringだけが異なるengine（digest `8fa3691a…b1b3`）で同じ結果だった。最終engineのrun `-3`では、全段階の記録が一致し、7保存のPDFはbyte単位で同一だった。

| 段階 | 結果 |
|---|---|
| source no-op | 4/5ページで、MuPDF全画素一致、Poppler変更0画素、独立Unicode一致 |
| overflow | 既存slot 209字（51+158）、生成slot 36字・2行。PR #6と同じallocation・画素差分 |
| second / shorten / regrow | PR #6と同じallocation・生成slot・作成証跡。shortenでは生成slotが`occupancy=None`、regrowはoverflowと同じallocation |
| no-op 1〜3 | 全10ページでMuPDF・Popplerの全画素が直前と一致。記録・slot・計画glyph 245個の全fieldが一致し、全aliasが`reused` |
| 容量拒否 | `paragraphs exceed all explicitly confirmed shared regions`で拒否。PDF・sidecarは作られない |

各保存の監査（独立pypdfの全ページUnicode、CID/GID/`W`とfont対応、元font resource、text以外のpaint、画像、annotation、保護領域、対象外ページ）はすべて通った。

### resource量（PR #6との比較）

PR #6の値は、保持していた同runのPDFを同じinventoryで測った。

| 保存 | PDF byte（PR #6 → 現行） | Type0 font | 4/5/6ページの`/Font`数 | 生成block byte |
|---|---|---|---|---|
| 原本 | 369,912 | 0 | 7 / 7 / 7 | 0 |
| overflow | 371,035 → 371,035 | 4 → 4 | 8/9/8 → 8/9/8 | 3,049 |
| second | 436,561 → 374,256 | 8 → 4 | 9/11/9 → 8/9/8 | 6,305 |
| shorten | 441,277 → 365,512 | 9 → 4 | 10/11/9 → 8/9/8 | 6,646 |
| regrow | 505,975 → 374,259 | 13 → 4 | 11/13/10 → 8/9/8 | 9,626 |
| no-op 1 | 570,054 → 374,492 | 17 → 4 | 12/15/11 → 8/9/8 | 12,702 |
| no-op 2 | — → 374,815 | 4 | 8/9/8 | 15,778 |
| no-op 3 | — → 375,105 | 4 | 8/9/8 | 18,854 |

- **生成fontの数**: 現行では全保存で、所有する生成fontが4個（4ページ1、5ページ2、6ページ1）、そのgraph objectが24個である。
- **aliasの扱い**: secondでは、文字の変わった5ページのbody・6ページのaliasを置き換え、変わらない4ページとlatinは既存objectを残した。shortenで文字を描かなくなった5・6ページのaliasは、非描画operatorから参照されるため直前のsubsetのまま残り、regrowで置き換えた。
- **no-opでの増加**: no-opごとのPDF増加（+233・+323・+290 byte）は、page programの圧縮後の増加（+308・+323・+290 byte）とほぼ一致する。展開後のprogramは保存ごとに一定の+21,784 byte（4ページ+4,408、5ページ+14,300、6ページ+3,076）増える。旧glyphの非描画operatorが残るためで、font・resourceとは別の累積である。

最初の試行（run `font-lifecycle-windows`）では、engineの保存と検証は通ったが、評価コードの監査がsecondで停止した。原因は評価コード側にあった。保存前の全font指紋が保存後も同じという旧前提で比較しており、置き換えた生成aliasも「元font」として扱っていた。監査が除外できるのは、直前revisionの検証済み記録で所有を証明し、この保存が置き換えたaliasだけに改めた。保持した成果物で再照合すると、この条件では通り、所有証跡を渡さなければ同じ理由で拒否した。そのうえで新しいrun名で全系列を再実行した。

### 目視

run `-3`のPoppler 144dpi画像は、各段階でPR #6 runの画像と全画素一致した。対象はoverflow・second・shorten・regrowの4〜6ページと、no-opの全10ページで、MuPDFでも全10ページが一致した。PR #6 runの画像は下記のとおり目視で確認済みである。no-op 3の5・6ページはrun `-2`の画像も直接確認し、同じ配置であることを見た（run `-2`と`-3`はPDFがbyte単位で同一）。

## 同一ページ2 destinationの評価

**外部原本で評価済み（2026-09-25、run `multi-pr8-windows`）**。PR #8で用意した[評価コード](multi_destination.py)を変更せず、Windows検証環境の外部原本で実行した。結果は[下記](#外部原本の結果)にある。PR #8のsession（Linux container）では原本とproviderがなく実行できなかったため、その時点では合成原本のdry-runだけを行っていた（[下記](#dry-run合成原本外部原本の証跡ではない)。外部原本の証跡ではない）。

### 評価者の指定

- **領域**: 単一destination評価で空きと確認した6ページの`[55,80,385,120]`だけを使う。これを互いに交差しない2つのregionへ分ける。範囲外を新たに空きとは仮定しない。
  - `page6-a`: `[55,80,385,100]`、先頭baseline 92。1行分。
  - `page6-b`: `[55,101.5,385,120]`、先頭baseline 113.6。1行分。
- **順序**: flow順は`A → B → page6-a → page6-b`。page-entry orderは`page6-a = 10`、`page6-b = 20`。どちらも評価者の明示指定であり、engineが読み順を推定したものではない。
- **保護・provider・paragraph**: 単一destination評価と同じ（右上の図版を保護、story_styles公開集計と一致するprovider）。
- 分割regionが保守的な空き判定を通らない場合や、activationが下記と異なる場合は、評価は停止する。確認範囲の外へregionを動かして通すことはしない。

### 系列

| 系列 | 段階 |
|---|---|
| 逐次 | source no-op → `a-only`（60字追加。6ページは約16字・1行で`page6-a`だけ）→ `b-added`（80字追加。単一評価と同じ36字が`page6-a`と`page6-b`へ）→ `second` → `shorten`（`a-only`の文面へ戻し、`page6-b`だけdormant）→ `regrow` → no-op 3回 → 容量拒否 |
| 同時 | `both`（同じ80字で2 blockを1 transactionで初回生成）→ no-op |

### 自動照合

単一destination評価の照合をすべて行い、次を加える。

- **activation**: 各段階で有効なdestinationと、新しく作られたblockが期待どおりである。
- **挿入境界**: `b-added`の新blockは直前の`page6-a` blockの終端に入り、`both`の2 blockはoffset 0に確認済みorder順で入る。作成mutationのowner・orderを照合する。
- **作成証跡**: 作成後の各slotの作成証跡は変わらない。
- **page-entry chain**: 保存PDFのbytesから検証する。生成blockがoffset 0から隙間なく並び、確認済みorder順で、各bindingが自分のmarker対とblock SHA-256を指す。
- **Poppler差分**: 領域の外接矩形だけでなく、各regionの外（1pt余白込み）で0画素である。2つのregionの間も含む。
- **独立抽出**: 期待するUnicodeは、生成blockの文字をpage-entry順に並べ、その後にページ本来の文字を続けたものとする。独立抽出器はcontent stream順に読むためである。
- **font**: 6ページの生成fontがslotごとに1つずつあり、所有slotが入れ替わらない。
- **no-op**: 上記に加え、page-entry順序が変わらない。
- **同時と逐次の一致**: `both`と`b-added`で、chain順序とallocationが一致する。

### 実行

```powershell
.\.venv\Scripts\python.exe -m evaluations.continuation.multi_destination --run-name <未使用のrun名>
```

成果物は`runs/multi-<run名>/`に保存する。すべて成功し、画像の目視（`sequential/*-audit`と`simultaneous/*-audit`の4〜6ページ、no-opの全ページ）で欠陥がない場合だけ、`summary.json`を`evaluations/continuation/multi-destination-summary.json`として公開する。

### 外部原本の結果

run `multi-pr8-windows`（2026-09-25）。単一destinationの再評価（run `pr8-single-windows`）が通った後に、同じ環境・engine（digest `564da875…44a1`）・原本・providerで実行した。

- **評価コード**: `multi_destination.py`（SHA-256 `7c01283ea1d1d409dfaffb8f176d5a1b04942cefd1c2d95b49da9c3dc5b8a0df`）を変更していない。
- **結果**: 全段階・同時生成・容量拒否が通り、`status = passed`になった（3,622秒）。
- **公開集計**: runの`summary.json`（SHA-256 `850bd9ac…a8ec`）を[multi-destination-summary.json](multi-destination-summary.json)として公開した。

| 段階 | 新block | 有効 | 6ページのallocation | chain（page-entry順、byte範囲） | Type0 | 6ページfont出力 |
|---|---|---|---|---|---|---|
| a-only | page6-a | a | a 16字・1行 | a[0,1419) | 4 | PRF1 added |
| b-added | page6-b | a, b | a 33字、b 3字 | a[0,4174) b[4174,4583) | 5 | PRF1 replaced, PRF2 added |
| second | — | a, b | a 33字、b 5字 | a[0,6968) b[6968,7919) | 5 | 両方replaced |
| shorten | — | a | a 16字、b dormant | a[0,8400) b[8400,9628) | 5 | PRF1 replaced |
| regrow | — | a, b | a 33字、b 3字 | a[0,11155) b[11155,12732) | 5 | 両方replaced |
| no-op 1〜3 | — | a, b | 不変 | 順序不変（a → b） | 5 | 全alias reused |
| both（同時） | page6-a, page6-b | a, b | a 33字、b 3字 | a[0,2779) b[2779,3188) | 5 | 両方added |
| both → no-op | — | a, b | 不変 | 順序不変（a → b） | 5 | 全alias reused |

- **source slot**: 全段階で既存slotは209字（4ページ51字、5ページ158字）だった。b-added・regrow・bothの6ページ36字（a 33字＋b 3字、baseline 92と113.6）は、単一destination評価のoverflowと同じ36字・2行である。
- **a-only**: `page6-a`だけが生成・有効になった。`page6-b`にはmarkerもblockもない。
- **b-added**:
  - 既存の`page6-a` blockの後ろに`page6-b`が入り、chainはorder 10 → 20になった。
  - `page6-b`の作成mutationは`start = 1419`で、直前revisionの`page6-a` blockの終端と一致する。`insertion_order = 20`、ownerは`page6-b`のslotである。
  - `page6-a`の作成証跡（`start = 0`、order 10）は変わらない。
- **second**: 新blockを作らず、既存の2 blockを再利用した。
- **shorten**: `page6-a`だけが有効で、`page6-b`は`occupancy=None`（dormant）になった。`page6-b`のblock・marker・slot ID・orderはchainの2番目に残り、文字を描かない。
- **regrow**: 同じ`page6-b`のslot・blockへ戻り、新blockは作らない。allocationはb-addedと一致した。
- **no-op**（逐次3回と同時系列の1回）:
  - 全10ページでMuPDF・Popplerの全画素が一致した（計40ページ）。
  - chain順序、slot identity、作成証跡、allocation、計画glyph 245個の全fieldが直前と一致した。
  - font/resource数（Type0 5、所有する生成font 5・graph object 30、4/5/6ページの`/Font` 8/9/9）は変わらない。
  - 全aliasが`reused`で、生成fontの記録も変わらない。
- **同時生成（both）**: 1回の保存で2 blockを初回生成した。
  - 2つの作成mutationはどちらも`start = 0`の同位置ordered insertionで、`insertion_order`は10と20、ownerはそれぞれのslotである。
  - 保存後のprogramは`page6-a` block → `page6-b` block → 元のpage programの順になった。
- **同時と逐次の一致**: `both`と`b-added`で、次が一致した。
  - 有効destination、slot ID、allocation、page-entry chainの順序、bindingのorder。
  - destinationの所有（各slotのdestination・paragraph・region・作成provenance）。
  - PDFのbyte列は、生成履歴が違うため比較していない。逐次の`page6-a` blockはa-onlyの非描画operatorを含む。`page6-b` blockは両方で同じSHA-256だった。
- **page-entry chain**（各保存のbytesから）:
  - 生成blockはoffset 0から隙間なく連続し、確認済みorder順（10 → 20）で元のpage programより前にある。
  - marker各1組で、blockのSHA-256はbindingと一致した。
  - engineの再open検証で、各blockが独立した`q BT … ET Q`であること、block内の文字とfontがそのslotの所有であることも確認された。
- **Poppler差分**: 全保存で、各region（1pt余白込み）の外の変更は0画素だった。2つのregionの間（1.5pt）も含む。
- **独立抽出**: 全ページのUnicodeが一致した。6ページは`page6-a`の生成文字 → `page6-b`の生成文字 → 本来の文字の順である。これはcontent stream順であり、flowの論理順ではない。
- **生成font**: 6ページの生成fontはslotごとに1つある（`/PRF1`が`page6-a`、`/PRF2`が`page6-b`）。全保存を通じて所有slotは入れ替わらず、別slotのaliasを使わなかった。no-opは新しいfont objectを書かず、元fontは変わらない。
- **容量拒否**: 逐次系列の最終状態へ160字を加えると、`paragraphs exceed all explicitly confirmed shared regions`で拒否された。`capacity.pdf` / `capacity.json`は作られていない。

| 保存 | PDF byte | Type0 | 4/5/6ページの`/Font`数 | 生成block byte（a / b） |
|---|---|---|---|---|
| 原本 | 369,912 | 0 | 7 / 7 / 7 | 0 / 0 |
| a-only | 367,069 | 4 | 8 / 9 / 8 | 1,419 / 0 |
| b-added | 377,455 | 5 | 8 / 9 / 9 | 4,174 / 409 |
| second | 379,168 | 5 | 8 / 9 / 9 | 6,968 / 951 |
| shorten | 376,013 | 5 | 8 / 9 / 9 | 8,400 / 1,228 |
| regrow | 379,071 | 5 | 8 / 9 / 9 | 11,155 / 1,577 |
| no-op 1 / 2 / 3 | 379,299 / 379,600 / 379,897 | 5 | 8 / 9 / 9 | 13,949 / 1,939 → 19,537 / 2,663 |
| both | 375,183 | 5 | 8 / 9 / 9 | 2,779 / 409 |
| both → no-op | 377,685 | 5 | 8 / 9 / 9 | 5,573 / 771 |

- **Type0の数**: 単一destinationの4に対し5になる。6ページの生成fontがslot単位で所有されるため、2 destinationでは6ページに2つある。保存を重ねても増えない。
- **生成blockの増加**: 保存ごとに増える。置き換えた文字の非描画operatorを残す既存writerの性質で、単一destinationと同じである。

#### 目視

次の各段階を、Poppler 144dpiの監査画像と、4〜6ページの編集領域の300dpi切出しで確認した。

- 逐次: a-only・b-added・second・shorten・regrow・no-op 1〜3
- 同時: both・no-op

6ページは、2つのregionの枠を重ねた600dpiの切出しでも確認した。no-opは全10ページを見た。

- **欠陥**: なかった。文字の重なり、行ずれ、region間の隙間への侵入、`page6-a` / `page6-b`の混線、図版への侵入、欠落、fontの違和感、不自然なpaint順序は見られない。
- **shorten**: `page6-b`に文字が残っていない。
- **本来の文字**: 6ページの本来の文字は変わらない。
- **単一destinationとの一致**: b-added・regrow・no-op・bothの6ページの監査画像は、単一destination評価のoverflow・regrow・no-opの6ページとbyte単位で同じ画像だった。secondの5・6ページも単一destinationのsecondと同じである。確認済み領域を2 destinationへ分けても、同じ行が同じ位置に描かれる。
- **同時と逐次**: 同時系列の監査画像は、逐次系列の対応する画像（6ページはb-added）と同じだった。

補助確認として、保存済みの成果物を一時スクリプトで読み直した。これは評価コード外の確認で、スクリプトはcommitしていない。

- **chainの構造**: 各保存の6ページprogramをpypdfで分解した。
  - 各blockは閉じた`q BT … ET Q`で、許可したoperatorだけを含む。
  - `Tf`はそのslotが所有するaliasだけを選ぶ。
  - chainの後ろには元の6ページprogramがbyte単位でそのまま続く。
- **blockごとの描画**: blockを1つだけ描くページ複製を、MuPDFとPopplerで描画した。
  - インクは自分のregion（1pt余白込み）の内側にある。`page6-a`はy 83〜93.5pt、`page6-b`はy 105〜115ptで、1.5ptの隙間には入らない。
  - dormantの`page6-b`は何も描かない。
- **blockごとの文字**: 独立pypdfで抽出した各blockの文字は、そのslotの文字と一致した。
- **所有の一致**: aliasとslotの対応は全保存で変わらなかった。`both`と`b-added`でも同じだった。

### dry-run（合成原本。外部原本の証跡ではない）

[synthetic_multi.py](synthetic_multi.py)は、同じ系列・監査・拒否を合成の6ページPDFで実行する。

- **合成原本**: 4・5ページにparagraphのsource fragment、4〜6ページの保護領域に固定図形、6ページに同じ2 regionを置く。
- **置き換えたもの**: 入力（原本・story・provider）と、外部toolのpathだけである。
  - Poppler: `/usr/bin/pdftoppm` 24.02.0
  - 独立pypdf: 別interpreterのPython 3.13.12 + pypdf 6.10.0
- **実行環境**: Linux x64 container、Python 3.12.3、PyMuPDF 1.27.2.3。engine digest `564da875…44a1`。
- **結果**: 全段階と容量拒否が通り、`status = passed`になった（約6分）。集計は公開していない。

| 段階 | 新block | 有効 | chain（page-entry順、byte範囲） | Type0 | 生成font（6ページ） | 6ページfont出力 |
|---|---|---|---|---|---|---|
| a-only | page6-a | a | a[0,1114) | 3 | 1 | PRF1 added |
| b-added | page6-b | a, b | a[0,3521) b[3521,3830) | 4 | 2 | PRF1 replaced, PRF2 added |
| second | — | a, b | a[0,5946) b[5946,6676) | 4 | 2 | 両方replaced |
| shorten | — | a | a[0,7021) b[7021,7957) | 4 | 2 | PRF1 replaced |
| regrow | — | a, b | a[0,9428) b[9428,10613) | 4 | 2 | 両方replaced |
| no-op 1〜3 | — | a, b | 順序不変 | 4 | 2 | 全alias reused |
| both（同時） | page6-a, page6-b | a, b | a[0,2464) b[2464,2773) | 4 | 2 | 両方added |

- **no-op**: 3回とも全6ページでMuPDF・Popplerの全画素が一致した。計画glyph 363個の全fieldが一致し、font/resource数も変わらなかった。
- **各region外の差分**: 全保存で、Popplerの各region外（1pt余白込み）の差分は0画素だった。
- **同時と逐次**: 同時生成（`both`）のchain順序とallocationは、逐次の`b-added`と一致した。
- **容量拒否**: `paragraphs exceed all explicitly confirmed shared regions`で拒否され、出力は作られなかった。
- **生成blockの増加**: 保存ごとに増える。置き換えた文字の非描画operatorを残す既存writerの性質で、単一destinationと同じである。

## PR #6の結果

起点は`d33d236`（PR #5のmerge）。engine・評価コードは変更していない。

| 項目 | 内容 |
|---|---|
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、lockfileの版（PyMuPDF 1.27.2.3 / pypdf 6.10.0 / uharfbuzz 0.55.0 / fontTools 4.64.0） |
| 独立tool | Poppler `pdftoppm` 26.07.0、独立pypdf 6.10.0（Python 3.12.14）。どちらも`evaluations/realpdf/evaluate.py`の既定pathにあり、pathは変更していない |
| 原本 | SHA-256 `13665875…a5f3`で一致 |
| provider | body `msmincho.ttc` face 1、latin `times.ttf` face 0。file名・face・SHA-256・variationsが[story_styles公開集計](../story_styles/summary.json)（SHA-256 `c2329afa…c7c2`）と一致 |
| 所要時間 | 1,846秒（全段階と容量拒否） |

| 段階 | 結果 |
|---|---|
| source no-op | 4/5ページの同文replayで、MuPDF全画素一致、Poppler変更0画素、独立Unicode一致 |
| overflow | 既存slotへ209字（4ページ51字、5ページ158字）、生成slot `continuation-0ca851c7b7e027846e4cfcdb`へ36字・2行（baseline 92→113.6）。以前の計画と一致した |
| reopen | 5保存すべてで`open_shared_flow`がrestoredになり、確認契約hashは不変、slotは3つ |
| second | 生成slotを新規に作らず、同じslotと作成証跡のまま38字・2行を描く。4ページはMuPDF・Popplerとも全画素不変 |
| shorten | 全文を`確認。`へ置き換えた。5ページslotと生成slotは`occupancy=None`になり、文字を描かない。生成slotのmarker blockは6ページprogram先頭に残る |
| regrow | allocation（範囲・occupancy）がoverflowと一致し、新規slotはない。Popplerの4〜6ページとMuPDFの全10ページがoverflowの出力と同じ画像になった |
| final no-op | 全10ページでMuPDF・Popplerの全画素が一致した。paragraph・style・destinationの記録、slotのidentity・allocation・行geometry・alignment・inline styleが直前のregrowと一致。計画glyph 245個のUnicode・GID・origin・size・advance・code・CID・`W`幅も一致した |
| 容量拒否 | 160字の追加を`paragraphs exceed all explicitly confirmed shared regions`で拒否した。`capacity.pdf` / `capacity.json`は作られていない |

各保存の監査はすべて通った。編集した4〜6ページではPoppler差分が確認済み領域（1pt余白込み）の外で0画素、それ以外のページはMuPDF全画素一致。全ページの独立Unicode、CID/GID/`W`とfont対応、元font resource、text以外のpaint、画像、annotationも照合した。

### 目視

overflow・second・shorten・regrowの4〜6ページと、no-opの全10ページについて、Poppler 144dpiの`after.png`を確認した。5・6ページの編集領域は300dpiの切出しでも確認した。文字の重なり、行ずれ、不自然な文字位置、図版への侵入、文字欠落、別paragraphの破損、想定外のpaint順序はなかった。生成slotの文字は、再組版したsource slotと同じ書体・大きさ・行送りで描かれている。shortenでは5ページ領域と6ページの生成先に文字が残っていない。

以下は、今回の範囲では欠陥として扱わない性質である。

- 原本では`LibreOffice`と`への`の間にLibreOfficeの和欧間隔（約2.6pt）がある。これはglyph位置によるもので、U+0020は描かれていない（MuPDFの`rawdict`は間隔から空白を合成する）。明示providerで再組版した行にはこの間隔がない。論理文章はstory_styles評価と同じで、文字は失われていない。
- 6ページの生成先は評価者が選んだ上部左側の空き領域である。5ページ末尾の別paragraph（6ページの`コシステム…`へ続く）より前に置かれる。これは明示契約による配置であり、文書の読み順として自然な再レイアウトであることを示すものではない。

## 実行後に確認すること

1. `summary.json`の`environment.engine_digest`と`runner_sha256`が、冒頭の現行値と一致することを確認する。
2. overflowのallocationを以前の計画（既存slot 209字、生成slot 36字・2行）と比べる。差があれば原因を調べ、過去値へ合わせるためのコード変更はしない。
3. `runs/<run名>/`の各`*-audit/page-<n>/after.png`を目視する。対象はoverflow・second・shorten・regrowの4〜6ページと、no-opの全ページ。文字の重なり、行ずれ、不自然な余白、図版への侵入、欠落、別paragraphの破損、想定外のpaint順序を見る。
4. 各段階の`resources`で、no-opの間にType0・`/Font`・生成fontの数が変わらないこと、生成blockの増加がpage programだけに現れることを確認する。
5. すべて成功した場合だけ、`runs/<run名>/summary.json`を`evaluations/continuation/summary.json`へ置き、資料の結果を更新する。

公開集計には、原本URL/hash、engine・評価コード・helperのhash、実行環境、実際に使ったproviderのfile名・face・hashと照合元集計のhash、各段階と拒否の検査結果、各保存のresource量だけを含める。これは単一外部原本での境界評価であり、一般PDFの成功率ではない。
