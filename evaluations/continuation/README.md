# 確認済み空き領域へのcontinuation評価

**PR #15 engineでの`q ... Q` scope境界の評価（2026-09-26）**。PR #13〜#15は、confirmed page-program boundaryの条件を順に緩めた（CTMの相殺、矩形clipの継承、1つの`q ... Q` scopeの内側）。そのengine（digest `454686cef09460f76c3daaa064d2765748b74e83c8e6963d323a00cd905b71c5`）で、加工していないLibreOffice原本の1ページ・10ページを`inspect_continuation_boundaries()`で調べ、10ページの新しい候補で系列評価を行った。同じWindows検証環境で、既存の3本も再実行した。engineは変更していない。

- **検査**: PR #15で新しく安全な候補になった境界は、1ページ126、10ページ86である。どれも`q`の深さ1・CTM identityで、ほとんどは証明済みの矩形clipの下にある。identity以外のCTMの境界（1ページ2、10ページ18）はすべて`q`の深さ2にある。CTMの相殺と矩形clipは証明されるが、`nested-graphics-state-save`で拒否される。「深さ1 + CTMの相殺 + 矩形clip」を満たす境界は、原本にはない。
- **10ページのscope境界**（run `scope-pr15-windows-2`）: 本文の`q 0 0.1 595.2 841.8 re W* n ... Q`の内側で、最後の行の`Q`の後、scopeの対応する`Q`の直前にある境界へ、確認済みの空き領域`[55,80,385,120]`のdestinationを置いた。全段階と容量拒否が通った。bindingの`q`・`Q`の改ざんも拒否された。[公開集計](scope-destination-summary.json)はこのrunの集計である。
- **page entryの対照**（run `scope-entry-pr15-windows-2`）: 同じ10ページの領域・編集をpage entryへ入れた。全段階と容量拒否が通り、scope境界と全段階で計画glyph・保存後のglyph・MuPDF画素・Poppler画像が一致した。[対照の公開集計](scope-entry-summary.json)はこのrunの集計である。
- **既存3本の回帰**（run `pr15-regression-windows`・`boundary-pr15-boundary-regression-windows`・`multi-pr15-multi-regression-windows`）: 全段階と容量拒否が通った。PR #12 engineでのrunと、成果物がそれぞれ129件・129件・177件ともbyte単位で一致した。[公開集計](summary.json)・[境界の公開集計](boundary-destination-summary.json)・[2 destinationの公開集計](multi-destination-summary.json)は、これら3 runの集計に置き換えた。

結果は[PR #15 engineでの`q ... Q` scope境界の評価](#pr-15-engineでのq--q-scope境界の評価)にある。単一原本の1ページの1境界と、評価者が確認した1つの空き領域の範囲であり、任意のPDFで`q`の内側へ挿入できることを示すものではない。

**PR #12 engineでの回帰評価（2026-09-26）**。PR #12は、identity以外のCTMを持つ境界で、生成blockを`q N cm BT ... ET Q`として相殺できるようにした。そのengine（digest `383bbd49fddacba074b54d676f468d032c9f27a249af47af56c07a653bb55262`）で、次の3本を同じWindows検証環境で実行した。LibreOffice原本の確認済み境界はCTM identityなので、これは新機能の外部証明ではない。PR #11まで成立していた外部経路をPR #12が壊していないことを確かめる回帰評価である。原本・provider・Poppler・独立pypdf・評価コードはPR #11の評価と同じである。

- **単一destination**（run `ctm-regression-windows`）: 全段階と容量拒否が通った。成果物129件（PDF・sidecar・記録・画像）が、PR #11のrun `failclosed-single-windows`とbyte単位で一致した。
- **確認済み境界**（run `boundary-ctm-boundary-regression-windows`）: 全段階と容量拒否が通った。page entryのrun `ctm-regression-windows`と、生成glyphと画素が一致した。成果物129件が、run `boundary-failclosed-windows`とbyte単位で一致した。
- **同一ページ2 destination**（run `multi-ctm-multi-regression-windows`）: 逐次生成・同時生成・容量拒否が通った。成果物177件が、run `multi-failclosed-windows`とbyte単位で一致した。

3本の集計は、PR #15 engineでの回帰の集計に置き換えた。集計がPR #11と違うのは、engine digestと`continuation.py`・`paragraph.py`のhashだけである。境界の集計では、比較したpage-entry runの名前とengine digestも違う。結果は[PR #12 engineでの回帰評価](#pr-12-engineでの回帰評価)にある。

**確認済みpage-program境界（2026-09-25）**。PR #11の最終engine（digest `59fe6125948d44a732da8c22a18cd619cdc3a34f7d6599c6141250144e481bfd`）で、次の外部評価を行った。このengineは、operatorの入れ子が崩れたpage programで境界候補を出さない（fail closed）。3本の集計は、上記の回帰評価の集計に置き換えた。

- **単一destination**（run `failclosed-single-windows`）: 全段階と容量拒否が通った。成果物129件（PDF・sidecar・記録・画像）が、run `boundary-single-windows`とbyte単位で一致した。
- **確認済み境界**（run `boundary-failclosed-windows`）: 全段階と容量拒否が通った。page entryのrun `failclosed-single-windows`と、生成glyphと画素が一致した。成果物129件が、run `boundary-windows`とbyte単位で一致した。結果は[下記](#確認済みpage-program境界の評価)にある。
- **同一ページ2 destination**（run `multi-failclosed-windows`）: 逐次生成・同時生成・容量拒否が通った。成果物177件（PDF・sidecar・記録・画像）が、run `multi-boundary-windows`とbyte単位で一致した。

その前のengine（digest `fca1e014164c93c6c62b1aad4344d1184760bd5e8f98404b44a453674ee0a731`、入れ子の監査なし）では、run `boundary-single-windows`・`boundary-windows`・`multi-boundary-windows`が通った。`boundary-single-windows`の7保存のPDF・sidecarは、下記のrun `nesting-single-windows`と、`multi-boundary-windows`のPDF・sidecar・記録57件は、run `multi-nesting-windows`とbyte単位で一致した。

**operator nesting正規化後の評価（2026-09-25）**。engine digest `341859e13035bfd6a85f04b33f7fe0c7f95b708cf97b8b13548db771d8f079e4`で、次の2本を同じWindows検証環境で実行し、どちらも全段階と容量拒否が通った。各保存では、編集した4〜6ページがPDF 1.xのoperator nestingを満たすこと、出力のPDF versionが原本と同じ（`%PDF-1.4`）であることも照合した。

- **単一destination**（run `nesting-single-windows`）: allocation・生成slot・font/resource数は以前と同じだった。監査画像は、以前のengineの画像とPNGのbytesまで一致した。公開集計は、上記のrun `failclosed-single-windows`の集計に置き換えた。
- **同一ページ2 destination**（run `multi-nesting-windows`）: 確認済みの6ページ領域を評価者が2つのregionへ明示分割した。逐次生成・同時生成・reopen・re-edit・shorten・regrow・no-opを完走した。公開集計は、上記のrun `multi-failclosed-windows`の集計に置き換えた。

結果は[operator nesting正規化後の再評価](#operator-nesting正規化後の再評価)にある。どちらも単一外部原本の1 paragraphと、評価者が確認した1つの空き領域に対する境界評価である。一般PDFの成功率や、任意のPDFで複数destinationが動くことを示すものではない。PR #8・PR #7・PR #6のengineでの結果は[履歴](#pr-8-engineでの単一destination再評価)として残す。

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
  - operator nesting（[operator_nesting.py](../../pdfeditor/operator_nesting.py)）: 編集した4〜6ページのprogram全体が、PDF 1.xの入れ子規則を満たす。text object内に`q`/`Q`/`cm`がなく、text objectとmarked contentが交差しない。確認済み原本の4〜6ページが規則を満たすことを最初に確かめるので、違反があればpdfengineが書いたものである。
  - PDF version: 出力のheaderが原本と同じ（`%PDF-1.4`）である。
  - PDF byte数、4〜6ページの`/Font`数、ページごとの生成font数、文書全体のType0 font数、所有する生成fontの数とgraph object数、6ページの生成block byte数、aliasごとの結果（added / replaced / reused）を記録する。
- no-op（regrowの後に3回。各回は直前の保存と比べる）:
  - 全ページでMuPDF・Popplerの全画素が一致する。
  - allocation、paragraph・style・destinationの記録、slotのidentity・行geometry・alignment・inline style（tracking・riseを含む）が変わらない。
  - 計画glyphのUnicode・GID・origin・size・advance・code・CID・`W`幅が直前の保存と一致する。
  - font/resource数（`/Font`数、生成font数、Type0数、生成graph object数）が変わらず、全aliasが`reused`（新しいfont objectを書かない）で、生成font記録が変わらない。
- 容量不足: 最終状態へ160字を加えると確認済み容量（約271字）を超え、最終PDF/sidecarを公開せずに拒否する。拒否されたことだけでなく、理由が確認済みregionを使い切ったこと（`paragraphs exceed all explicitly confirmed shared regions`）まで照合する。

## PR #15 engineでの`q ... Q` scope境界の評価

起点は`4ce4689`（PR #15のmerge）。サブエージェントは使用していない。engineは変更していない。原本は加工しておらず、安全条件も緩めていない。

| 項目 | 内容 |
|---|---|
| engine | digest `454686ce…71c5`（45ファイル）。PR #12 engine（`383bbd49…5262`）から変わったのは`continuation.py`・`paragraph.py`・`shared_flow.py`だけ |
| 評価コード | 新規[scope_destination.py](scope_destination.py)（SHA-256 `3c2aa0a976aaba4feda1e7a58da1289bf8680abb5e6a4ece28dcaec5799ba1fc`）。既存の評価コードと依存ファイルは変更していない（hashはPR #12の評価と同じ） |
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、PyMuPDF 1.27.2.3、pypdf 6.10.0 |
| 独立tool | Poppler `pdftoppm` 26.07.0、独立pypdf 6.10.0（Python 3.12.14）。既定pathのまま |
| 原本 | SHA-256 `13665875…a5f3`で一致 |
| provider | body `msmincho.ttc` face 1（`ceb8d745…44c2`）、latin `times.ttf` face 0（`931c5de5…58c5`）。[story_styles公開集計](../story_styles/summary.json)（`c2329afa…c7c2`）と一致 |
| 実行 | 5本と全suiteを並行して実行した。時間は並行実行でのもの |

### 原本の検査（1ページ・10ページ）

LibreOfficeの各ページのprogramは、次の形をしている。

```text
0.1 w
q 0 0.1 595.2 841.8 re W* n        本文group（深さ1、ページ全体の矩形clip）
  q ... BT ... ET Q                 各行の文字（深さ2）
  q 1 0 0 1 x y cm ... S Q          linkの下線（深さ2、平行移動のCTM）
Q
q <図版の矩形> re W* n ... Q        図版group（深さ1、図版の矩形clip）
```

1ページの図版groupは`q 58.4 734.7 202.1 60 re W* n q 202.1 0 0 60 58.5 734.7 cm /Im4 Do Q Q`で、画像は深さ2で、scaleのCTMの下に描かれる。10ページの図版group（CC-BY-SAロゴ）は、深さ1のpathで描かれる。

`inspect_continuation_boundaries(..., include_refused=True)`の結果は次のとおりである。評価コードが公開集計の`inspection`に記録する（候補ごとの序数・offset・前後のoperator・深さ・scope・clip・CTM・相殺・prefix/suffixの描画数を含む）。

| | 1ページ | 10ページ |
|---|---|---|
| 境界（operator数） | 979（980） | 996（997） |
| 安全な候補 | 128（深さ0が2、深さ1が126） | 88（深さ0が2、深さ1が86） |
| 候補のscope（開く`q`〜対応する`Q`の序数） | 本文1〜970が123、図版971〜979が3、scopeなし2 | 本文1〜659が62、ロゴ660〜996が24、scopeなし2 |
| 候補のclip | ページ全体の矩形122、図版の矩形2、なし4 | ページ全体の矩形61、ロゴの矩形23、なし4 |
| 候補のCTM | すべてidentity（`ctm_compensation`を持つ候補は0） | 同左 |
| prefix・suffixの両方に描画がある候補 | 124 | 84 |
| 拒否 | 851（深さ1が10、深さ2が841） | 908（深さ1が314、深さ2が594） |
| 拒否理由（重複あり） | `nested-graphics-state-save` 841、`inside-text-object` 480、`text-rendering-mode` 24、`pending-path` 10、`pending-clip` 2 | `nested-graphics-state-save` 594、`inside-text-object` 401、`pending-path` 320、`text-rendering-mode` 6、`pending-clip` 2 |

- **clip**: 両ページのclipは、どれも1つの矩形と証明された。certified rectangleは、ページ全体が`[0.00109, 0.10109, 595.19891, 841.89891]`（丸めの上界0.00109pt）、1ページの図版が`[58.40090, 47.30090, 260.49910, 107.29910]`、10ページのロゴが`[400.10103, 60.80103, 535.89897, 108.19897]`である。
- **深さ1の拒否**: どれも組み立て中のpath/clip（`pending-path`・`pending-clip`）である。本文・図版groupの`re W*`の途中（各ページ4）と、1ページの脚注の区切り線（6）・10ページのロゴ（310）のpathの途中にある。
- **identity以外のCTM**: どれも深さ2にある。原本には「深さ1・CTMの相殺・矩形clip」を同時に満たす境界がない。

| ページ | 境界 | CTM | 証明されるもの | 拒否理由 |
|---|---|---|---|---|
| 1 | 図版の`cm`の後、`Do`の後（2） | `[202.1 0 0 60 58.5 734.7]` | 相殺`0.00494804537259 0 0 0.0166666666667 -0.289460654296 -12.2450002035 cm`、図版の矩形clip | `nested-graphics-state-save`だけ |
| 10 | 3本の下線の`q 1 0 0 1 x y cm ... S Q`の中（各6、計18） | 平行移動（158.2, 662.8）・（188, 535.3）・（291.2, 484.9） | 相殺`1 0 0 1 -x -y cm`、ページ全体の矩形clip | 12は`nested-graphics-state-save`だけ。`m`・`l`の後の6は`pending-path`も |

**PR #13・#14との比較**（評価コード外の一時スクリプト。commitしていない）: 同じ原本の1ページ・10ページを、PR #13のengine（`399d4f6`、digest `383bbd49…5262`）とPR #14のengine（`2f26666`、digest `e8998aa9…3803`）でも検査し、序数ごとに比べた。

- **PR #13**: 候補は各ページ2つ（深さ0の、先頭の`0.1 w`の後と、本文groupの`Q`と図版groupの`q`の間）。`active-clip`は1ページ971、10ページ988。
- **PR #14**: 候補は同じ2つ。両ページのclipがすべて矩形と証明され、`active-clip`は0になり、clipの新しい拒否理由も出なかった。残る理由は`inside-graphics-state-save`（977・994）である。
- **PR #15**: 新しく安全になった境界は1ページ126、10ページ86で、どれも深さ1・CTM identityである。
  - PR #13での拒否理由は、`inside-graphics-state-save`と`active-clip`（124・84）か、`inside-graphics-state-save`だけ（各2。開く`q`の直後で、clipを設定する前）である。PR #14での拒否理由は、どれも`inside-graphics-state-save`だけだった。
  - 安全でなくなった境界はない。以前の2候補は同じIDのまま残る。
  - 深さ1の境界のIDはscopeの証跡を含むため、同じ位置でもPR #13・#14のIDと異なる（評価した境界は、PR #13・#14では`boundary-b5a70e99266f96b467d4a8ad`）。
- **6ページ**: 境界の回帰（下記）の集計で、候補は2 → 116、拒否は1,638 → 1,524、prefix・suffixの両方に描画がある候補は1 → 112になった。確認済みの境界`boundary-b848698b464255ff0b2b6f90`（深さ0）は変わらない。

### 10ページのscope境界の評価者の指定

[評価コード](scope_destination.py)に固定した。

- **境界**: `boundary-1cb2d3bbed6f7b8618f3d4b1`。offset 10026（序数658）。
  - 直前: 本文の最後の行のgroup（`q 0 0 0 rg BT 155.7 244.1 Td ... ET Q`）の`Q`（[10025,10026)）。直後: 本文scopeの対応する`Q`（[10027,10028)、序数659）。直前・直後のoperatorは、bytesのSHA-256でも固定した。
  - scope: 開く`q`は序数1（[6,7)）、対応する`Q`は序数659。対応する`Q`が戻す状態は、ページの初期状態（CTM identity・clipなし・`w 0.1`）である。
  - clip: 序数2〜4の`0 0.1 595.2 841.8 re W* n`。certified rectangleは`[0.0010867600854683331, 0.10108676008551382, 595.1989132399145, 841.8989132399145]`。`re`・`W*`・`n`のbytesのSHA-256も固定した。
  - CTM: identity。authorityは`ctm_compensation`を持たず、`isolation = q-BT-ET-Q`、`initial_clip = inherited-rectangular-clip`である。
  - 描画: prefixの描画operatorは117、suffixは15（ロゴ）。suffixは文字を描かない。
  - 意味: 6ページで確認済みの境界（本文groupの`Q`とロゴgroupの`q`の間）と同じ位置の、1段内側にあたる。blockは本文の全描画の後、本文scopeの`Q`の前、ロゴの前に描かれる。
- **領域**: `[55,80,385,120]`（x 56.8、幅326、先頭baseline 92）。6ページの確認済み領域と同じgeometryである。10ページでは、ロゴ（bbox x ≥ 400.2）の左、見出し（y ≥ 141.07）の上にあり、MuPDFのbbox logに描画がない。ページ全体のclipの内側である。描画と画像で確かめたうえで評価者が固定した。engineが推定したものではない。
- **保護**: 4・5・10ページのロゴ領域`[398,58,540,111]`。
- **paragraph・provider・編集**: 単一destination評価と同じである。flow順はA（4ページ）→ B（5ページ）→ 10ページ。明示契約による配置であり、文書の読み順として自然な再レイアウトであることは示さない。
- **検出順では選ばない**: 評価コードは、engineがこの境界を同じ証跡・scope・clip・CTMの安全な候補として列挙することを最初に確かめ、違えば停止する。

### 自動照合

単一destination評価の照合を、編集ページを4・5・10ページとして行う（10ページのUnicodeは、境界では本来の文字の後、page entryでは前に生成文字が続く）。次を加える。

- **検査の記録**: 1ページ・10ページの`inspection`（上記）。
- **確認時の拒否**: 次の2つは`confirm_continuation_destination`が拒否する。確認は何も書かない。
  - 10ページの深さ2の境界`boundary-0089cd9553278f1b208a1b8f`（下線`q 1 0 0 1 158.2 662.8 cm ... S`の後、`Q`の前）。相殺とclipは証明済みで、拒否理由が`nested-graphics-state-save`だけであることを先に確かめる。
  - ロゴscopeの候補`boundary-2bdd693036e24d7f4e293546`（序数663、深さ1、ロゴの矩形clip）。同じ領域は`continuation destination extends beyond its inherited rectangular clip`で拒否される。
- **各保存**:
  - destinationの記録全体が確認時と同じ。生成slotの作成mutationは、offset 10026の順序なしzero-length insertion（page entryでは0）である。
  - prefix・block・suffix: prefixとsuffixを連結すると原本の10ページprogramになる。blockの直前・直後のoperatorが、確認した`Q`・対応する`Q`（同じbytes）である。
  - scope: engineのscopeの導出を使わず、評価コード自身の`q`/`Q`のstackで、blockの後の`Q`が開く`q`（[6,7)）を閉じることを確かめる。bindingの`scope`がその2つを指し、開く`q`の終端 ≤ block ≤ 対応する`Q`の先頭であること。
  - 状態（interpreter）: blockの最後の`Q`以外の全operatorが、境界より深く、CTM identity・確認済みのclipの下にあること。blockの`Q`の直後は深さ1で境界と同じ状態、対応する`Q`の直後は深さ0で`restored_state`であること。page entryでは、blockが初期状態で描き、深さ0へ戻ること。
- **改ざん**: 最終保存のsidecarで、bindingの`scope.matching`を内側の`Q`（同じbytes）へ、`scope.opening`を最後の行の`q`へ移し、checksumを付け直してopenする。どちらも`confirmed page-program boundary is not in its confirmed q ... Q scope`で拒否されること。付け直しただけの対照は復元されること。
- **page entryとの比較**（`--page-entry-run`）: 全段階で次が一致すること。
  - 計画glyphの全field。
  - 10ページの保存後のglyph（MuPDFのtext traceの文字・glyph・原点・box）。
  - 10ページのMuPDF 144dpi全画素、Poppler監査画像（編集段階は4・5・10ページ、no-opは全10ページ）。

### 実行

```powershell
.\.venv\Scripts\python.exe -m evaluations.continuation.scope_destination --authority page-entry --run-name <未使用のrun名>
.\.venv\Scripts\python.exe -m evaluations.continuation.scope_destination --run-name <未使用のrun名> --page-entry-run scope-entry-<上のrun名>
```

成果物は`runs/scope-entry-<run名>/`・`runs/scope-<run名>/`に保存する。境界のrunは、page entryのrunが実行中なら完了を待って比べる。必要なもの（原本・provider・Poppler・独立pypdf）は単一destination評価と同じである。

### 結果

| run | 結果 | 時間 |
|---|---|---|
| `scope-pr15-windows-2`（scope境界） | 全段階・no-op 3回・容量拒否・改ざん2件の拒否が通過。page entryの対照と、全段階で計画glyph・保存後のglyph・画素が一致 | 6,264秒 |
| `scope-entry-pr15-windows-2`（page entryの対照） | 全段階・no-op 3回・容量拒否が通過 | 5,770秒 |

| 保存 | block（10ページ、byte範囲） | 対応する`Q` | block内の`q`の深さ | text object（4/5/10ページ） | PDF byte（scope境界 / page entry） |
|---|---|---|---|---|---|
| 原本 | — | [10027,10028) | — | 56 / 83 / 57 | 369,912 |
| overflow | [10026,13070) | [13071,13072) | 2 | 58 / 85 / 58 | 371,059 / 371,051 |
| second | [10026,16338) | [16339,16340) | 2〜3 | 60 / 87 / 60 | 374,294 / 374,280 |
| shorten | [10026,16626) | [16627,16628) | 2〜4 | 62 / 90 / 63 | 365,530 / 365,514 |
| regrow | [10026,19618) | [19619,19620) | 2〜4 | 64 / 92 / 65 | 374,295 / 374,274 |
| no-op 1 | [10026,22706) | [22707,22708) | 2〜5 | 66 / 94 / 67 | 374,526 / 374,509 |
| no-op 2 | [10026,25794) | [25795,25796) | 2〜6 | 68 / 96 / 69 | 374,843 / 374,821 |
| no-op 3 | [10026,28882) | [28883,28884) | 2〜7 | 70 / 98 / 71 | 375,123 / 375,108 |

- **境界とscope**: 全保存で、blockは確認したoffset 10026にある。
  - 直前は確認した`Q`、直後は1 byteの空白を挟んで本文scopeの対応する`Q`である。開く`q`は[6,7)のまま、対応する`Q`はblockの長さだけ後ろへ動く。bindingの`scope`はその2つを指す。
  - prefix（117描画operator）とsuffix（15）を連結すると、原本の10ページprogramとbyte単位で一致した。
  - authorityの`boundary_id`・`graphics_state_scope`・`clip_constraint`は変わらず、`ctm_compensation`はない。作成mutationは、offset 10026の順序なしzero-length insertionである。
- **状態**: blockの`Q`の直後は深さ1で境界と同じ状態（CTM identity・ページ全体のclip）、対応する`Q`の直後は深さ0でページの初期状態だった。block内の全operatorはCTM identity・確認済みのclipの下にある。
- **allocation・slot**: 既存slot 209字（51+158）、生成slot 36字・2行（baseline 92 → 113.6）で、6ページの評価と同じである。生成slotを作るのはoverflowだけで、以後は同じslot・作成証跡を使う。shortenでは生成slotが`occupancy=None`になり、blockは同じ位置に残って文字を描かない。
- **生成glyph**: 全段階で、計画glyphの全fieldがpage entryの対照と一致した。10ページの保存後のglyph（36・38・0・36・36・36・36字）も一致した。
- **画素**: 全段階で、10ページのMuPDF全画素と、Poppler監査画像（編集段階は4・5・10ページ、no-opは全10ページ）がpage entryの対照と一致した。
  - 各保存で、Poppler差分は確認済み領域（1pt余白込み）の外で0画素だった。
  - 編集ページ以外はMuPDF全画素が一致した。
- **font/resource**: 全保存でType0 4、所有する生成font 4（graph object 24）、4/5/10ページの`/Font` 8/9/8である。no-opでは全aliasが`reused`で、生成fontの記録も変わらない。
  - 6ページの評価（`pr15-regression-windows`）の6ページを10ページに読み替えると、全段階でaliasごとの結果（added / replaced / reused）、ページごとの`/Font`数と生成font数、生成block byte数、生成slotの範囲が同じだった。
- **no-op 3回**: 全10ページでMuPDF・Popplerの全画素が一致した。計画glyph 245個の全fieldも一致した。
- **その他の監査**: 各保存で次が通った。
  - 独立pypdfの全ページUnicode、CID/GID/`W`、元font resource、text以外のpaint、画像、annotation。
  - operator nestingの違反0、PDF version `%PDF-1.4`。
- **拒否**:
  - 容量不足は`paragraphs exceed all explicitly confirmed shared regions`で拒否された。`capacity.pdf` / `capacity.json`は作られていない。
  - 確認時の拒否2件と、bindingの`q`・`Q`の改ざん2件は、上記の理由で拒否された。
- **block内の`q`の入れ子**: 再編集したblockは、以前の内容（置き換えた文字の非描画operator）を内側の`q ... Q`に残す。そのため、block内の最大の深さは保存ごとにおおむね1段増える（scope境界で2〜7、page entryで1〜6）。
  - どれもblockの`Q`の前で閉じ、scopeの外へは出ない。
  - これは既存writerの性質で、scope境界は基底の深さを1段加えるだけである。page entryのblockの本体（marker以外）は、6ページの評価のblockとbyte単位で同じだった（下記）。

補助確認として、保存済みの成果物を一時スクリプトで読み直した。これは評価コード外の確認で、スクリプトはcommitしていない。

- **独立な分解器**: pypdfの`ContentStream`で、各保存の10ページを分解した。
  - 先頭の659 operatorと末尾は原本と同じである。blockの最初の`q`はblockの最後の`Q`で閉じ、blockの直後の`Q`は序数1の`q`を閉じて深さ0へ戻る。
- **6ページとの一致**: 全段階の計画glyphの全fieldが、6ページのpage-entry run（`pr15-regression-windows`）と一致した。4・5ページのPoppler監査画像も32枚すべて一致した。
  - blockの本体（markerを除く）は、scope境界・page entryの対照・6ページのrunの3つでbyte単位で同じだった。
- **変化の範囲**: 10ページのPoppler差分の外接矩形は、`[57, 83, 382.5, 115]`pt以内だった。

**目視**: 10ページの上部（`[40,50,550,175]`を300dpi）と、4・5・10ページ全体（100dpi）の切出しを、全段階について確認した。
- scope境界とpage entryの対照の切出し28組は、PNGのbytesまで同じだった。
- 生成の2行は確認済み領域内で、ロゴの左、見出し「LibreOfficeを入手する」の上にある。ロゴ・見出し・本文に変化はない。
- secondは「追加編集」の文面、shortenでは領域に文字がない。regrowとno-op 3はoverflowと同じである。
- 4・5ページの再組版とshortenの`確認。`は、6ページの評価と同じ見た目である。

**最初の試行**（run `scope-pr15-windows`・`scope-entry-pr15-windows`）: 2本ともsecondで評価コードの照合が停止した。
- 原因は評価コード側にあった。blockの文字operatorがすべてblockの最初の深さ（境界で2、page entryで1）にあると仮定していたが、再編集したblockは内側に`q ... Q`を持つ（上記）。
- engineの検証（`_block`）はこの形を認めており、engineの誤りではない。
- 照合を「blockの最後の`Q`以外は境界より深く、CTM・clipが境界と同じ」に直した。保存済みのoverflow・secondで照合が通ることを確かめ、新しいrun名で全系列をやり直した。上記の評価コードのhashは修正後のものである。

### 既存3本の回帰

評価コードは変更していない。

| run | 結果 | 時間 | PR #12 engineのrunとのbyte一致 |
|---|---|---|---|
| `pr15-regression-windows` | 全段階・no-op 3回・容量拒否が通過 | 4,505秒 | `ctm-regression-windows`と129/129件（PDF 9・JSON 32・PNG 88） |
| `boundary-pr15-boundary-regression-windows` | 全段階・no-op 3回・容量拒否が通過。page-entry runと、全段階の計画glyph・Poppler画像が一致 | 4,511秒 | `boundary-ctm-boundary-regression-windows`と129/129件 |
| `multi-pr15-multi-regression-windows` | 逐次8段階・同時2段階・容量拒否が通過。`simultaneous_equals_sequential = true` | 7,133秒 | `multi-ctm-multi-regression-windows`と177/177件（PDF 12・JSON 45・PNG 120） |

- **集計の違い**: PR #12の公開集計との違いは、engine digestと`continuation.py`・`paragraph.py`・`shared_flow.py`のhashである。
  - 境界の集計では、比較したpage-entry runの名前・engine digestと、6ページの検査結果（上記）も違う。
  - それ以外（各段階の検査結果、allocation、境界ID、font/resource量、容量拒否の理由）は同じである。
- **深さ0の経路**: `q`の深さ0の境界とpage entryの出力が、PR #13〜#15で変わっていないことを外部原本で確かめたことになる。

## PR #12 engineでの回帰評価

起点は`44e547b`（PR #12のmerge）。サブエージェントは使用していない。engine・評価コードは変更していない。LibreOffice原本を加工してidentity以外のCTMの境界を作ることはしていない。相殺の機能そのものは、PR #12の合成PDFの試験で確かめたものである。

| 項目 | 内容 |
|---|---|
| engine | digest `383bbd49…5262`（45ファイル）。PR #11の最終engine（`59fe6125…1bfd`）から変わったのは`continuation.py`・`paragraph.py`だけ |
| 評価コード | `evaluate.py` `468f658e…ce3b`、`boundary_destination.py` `5d9a5bf8…bbce`、`multi_destination.py` `a9c65e1b…d701`。依存ファイルのhashも含め、PR #11と同じ |
| 環境 | Windows 11 x64（10.0.26200）、Python 3.12.14、PyMuPDF 1.27.2.3、pypdf 6.10.0 |
| 独立tool | Poppler `pdftoppm` 26.07.0、独立pypdf 6.10.0（Python 3.12.14）。既定pathのまま |
| 原本 | SHA-256 `13665875…a5f3`で一致 |
| provider | body `msmincho.ttc` face 1（`ceb8d745…44c2`）、latin `times.ttf` face 0（`931c5de5…58c5`）。[story_styles公開集計](../story_styles/summary.json)（`c2329afa…c7c2`）と一致 |
| 実行 | 3本と全suiteを並行して実行した。境界は`--page-entry-run ctm-regression-windows`で、同じengineのpage-entry runと比べた |

| run | 結果 | 時間 | PR #11 runとのbyte一致 |
|---|---|---|---|
| `ctm-regression-windows` | 全段階・no-op 3回・容量拒否が通過 | 3,205秒 | `failclosed-single-windows`と129/129件（PDF 9・JSON 32・PNG 88） |
| `boundary-ctm-boundary-regression-windows` | 全段階・no-op 3回・容量拒否が通過。page-entry runと、全段階の計画glyph・Poppler画像が一致 | 3,225秒 | `boundary-failclosed-windows`と129/129件（PDF 9・JSON 32・PNG 88） |
| `multi-ctm-multi-regression-windows` | 逐次8段階・同時2段階・容量拒否が通過。`simultaneous_equals_sequential = true` | 4,376秒 | `multi-failclosed-windows`と177/177件（PDF 12・JSON 45・PNG 120） |

- **集計**: PR #11の公開集計との違いは、engine digestと`continuation.py`・`paragraph.py`のhashだけだった。境界の集計では、比較したpage-entry runの名前とengine digestも違う。次の値はすべてPR #11と同じである。
  - 各段階の検査結果と容量拒否の理由（`paragraphs exceed all explicitly confirmed shared regions`）。
  - allocation（既存slot 209字、生成slot 36字・2行）、slot ID・作成証跡、境界ID `boundary-b848698b464255ff0b2b6f90`（offset 17602）、2 destinationのchain。
  - font/resource量、operator nesting（違反0）、`%PDF-1.4`、region外のPoppler差分0画素。
  - 境界の検査結果（候補2、拒否1,638と理由の内訳）。
- **byte一致**: PDF・sidecar・plan・report・監査画像・抽出文字が、PR #11のrunとbyte単位で同じである。上記の[単一destination](#単一destinationrun-nesting-single-windows)・[2 destination](#同一ページ2-destinationrun-multi-nesting-windows)・[境界](#結果run-boundary-windows2026-09-25)の表の値は、このrunにもそのまま当てはまる。
- **identity経路の形**: 3本の全sidecar（計24）で、destinationのauthorityに`ctm_compensation`はなく、`isolation = q-BT-ET-Q`のままである。6ページのblockはどれも`q BT`で始まり、`cm`を含まない。

補助確認として、評価コード外の一時スクリプト（commitしていない）で、原本の10ページを`inspect_continuation_boundaries(..., include_refused=True)`で調べた。PR #11のengine（`c2a62e2`）とPR #12のengineの結果を比べた。

- **候補**: 全ページで同じ（各2つ）。6ページを含む2〜9ページは、拒否した境界も含めて記録全体が同じだった。
- **違い**: 1ページの2境界と10ページの18境界だけが違った。
  - どれも`q`の内側・有効なclipの下にある。10ページのうち6つは、組み立て中のpathも持つ。
  - PR #11では拒否理由に`nonidentity-ctm`も含んでいた。PR #12ではこれが消え、証明済みの`ctm_compensation`が付く。残る理由で拒否されることは変わらない。
  - CTMは、1ページが`[202.1 0 0 60 58.5 734.7]`、10ページが3種の平行移動である。
- **6ページ**: identity以外のCTMの境界はない。PR #12の資料では公開集計からの推論だったが、原本で確かめた。

**目視**: 次の切出しを作り、PR #11のrunのPDFから同じ条件で作った切出しと比べた。計62枚が、PNGのbytesまで同じだった。

- 単一destination・境界: overflow・second・shorten・regrow・no-op 3の、6ページ領域（300dpi）と4・5ページ（100dpi）。
- 2 destination: 逐次6段階と同時2段階の、6ページ領域（300dpi）、4・5ページ（300dpi）、region間（600dpi）。

6ページの2行は確認済み領域内にあり、ロゴ・本文に変化はなかった。shortenでは5ページのslotと生成先に文字が残っていない。2 destinationでは、`page6-a`・`page6-b`の行がそれぞれのregionにあり、dormantの`page6-b`は空白で、region間の隙間に文字は入らない。

## operator nesting正規化後の再評価

起点は`c4ea5fb`（PR #9のmerge）。サブエージェントは使用していない。

- **engine**: writerを[PDF 1.xのoperator nesting](../../docs/confirmed-continuation.md#pdf-1xのoperator-nesting)に合わせ、出力のPDF versionを元PDFと同じにした（digest `341859e13035bfd6a85f04b33f7fe0c7f95b708cf97b8b13548db771d8f079e4`、45ファイル）。
- **評価コード**: 各保存の照合にoperator nestingとPDF versionを加えた（`evaluate.py` SHA-256 `468f658e…ce3b`、`multi_destination.py` `a9c65e1b…d701`）。
- **環境・入力**: 原本hash、provider照合（story_styles公開集計`c2329afa…c7c2`）、Poppler 26.07.0、独立pypdf 6.10.0は前回と同じである。
- **原本**: 確認済み原本の4〜6ページはPDF 1.xの規則を満たす（text object 56・83・85、違反0、`%PDF-1.4`）。

### 単一destination（run `nesting-single-windows`）

全段階と容量拒否が通った（4,222.67秒）。このrunの集計は、その後の再評価の集計に置き換えた（現行は[冒頭](#確認済み空き領域へのcontinuation評価)）。

- **allocation・生成slot**: allocation（既存slot 209字、生成slot 36字・2行）と生成slot IDは前回と同じである。
  - 作成証跡のdestination契約・owner・kind・offset 0（順序なし）も同じである。
  - 作成mutationの長さとanchorは、blockのbytesが変わったため異なる（3,049 → 3,044 byte）。
  - 単一destinationの従来形式（`page_entry_order`なし）も変わらない。
- **font/resource**: 全保存でType0 4、所有する生成font 4（graph object 24）、4/5/6ページの`/Font` 8/9/8である。全段階のaliasごとの結果（added / replaced / reused）も前回と同じだった。
- **no-op 3回**: 全10ページでMuPDF・Popplerの全画素が一致した。計画glyph 245個の全fieldが一致し、全aliasが`reused`だった。
- **容量拒否**: `paragraphs exceed all explicitly confirmed shared regions`で拒否した。PDF・sidecarは作られていない。
- **operator nesting**: 全保存で4〜6ページの違反は0である。text objectは保存ごとに増える。source再編集とblockの再編集がそれぞれtext objectを3つに分けるためである。
- **PDF version**: 全保存の出力が`%PDF-1.4`（前回までの出力は`%PDF-1.3`）。
- **画像**: 監査画像84枚（各段階の前後）が、前回のrun `pr8-single-windows`の画像とPNGのbytesまで一致した。
- **PDFの差**: PDFとsidecarは、構造が変わったため以前のengineとbyte単位では一致しない。

| 保存 | text object（4/5/6ページ） | 違反 | PDF byte（前回 → 今回） | 生成block byte（前回 → 今回） |
|---|---|---|---|---|
| 原本 | 56 / 83 / 85 | 0 | 369,912 | 0 |
| overflow | 58 / 85 / 86 | 0 | 371,035 → 371,054 | 3,049 → 3,044 |
| second | 60 / 87 / 88 | 0 | 374,256 → 374,281 | 6,305 → 6,312 |
| shorten | 62 / 90 / 91 | 0 | 365,512 → 365,519 | 6,646 → 6,600 |
| regrow | 64 / 92 / 93 | 0 | 374,259 → 374,277 | 9,626 → 9,592 |
| no-op 1 | 66 / 94 / 95 | 0 | 374,492 → 374,510 | 12,702 → 12,680 |
| no-op 2 | 68 / 96 / 97 | 0 | 374,815 → 374,826 | 15,778 → 15,768 |
| no-op 3 | 70 / 98 / 99 | 0 | 375,105 → 375,117 | 18,854 → 18,856 |

補助確認として、保存済みの成果物を一時スクリプトで読み直した。これは評価コード外の確認で、スクリプトはcommitしていない。

- **blockの構造**: pypdfの分解器で、各保存の6ページblockを確かめた。外側の`q ... Q`はblockの最後でだけ閉じ、text object内に`q`/`Q`はなく、`Tm`/`Tj`/`TJ`はtext object内だけにある。
- **ページの入れ子**: engineとは別の分解器で、4〜6ページの入れ子規則を確かめた。数えたtext objectの数はengineの検査と一致した。
- **blockの描画と文字**: blockだけを描いたページのインクは確認済み領域の内側にあり、shortenでは何も描かない。block内の文字はslotの文字と一致した。

目視では、overflow・second・shorten・regrowの4〜6ページを300dpiの切出しで確認した。切出しは前回の切出しとbytesまで同じで、4・5ページのsource再編集と6ページの生成先に位置のずれはなかった。

### 同一ページ2 destination（run `multi-nesting-windows`）

単一destinationの再評価が通った後に実行した。全段階・同時生成・容量拒否が通り、`status = passed`、`simultaneous_equals_sequential = true`になった（4,515.36秒）。このrunの集計は、その後の再評価の集計に置き換えた（現行は[冒頭](#確認済み空き領域へのcontinuation評価)）。

- **前回と同じもの**: 各段階の有効destination・新block・allocation・aliasごとのfont出力（added / replaced / reused）・Type0数（4、b-added以降5）。
  - `page6-b`の作成位置は、直前revisionの`page6-a` blockの終端（1,414）である。chainは全保存でorder 10 → 20だった。
  - `both`は2 blockをoffset 0の同位置ordered insertionで作り、逐次の`b-added`と有効destination・slot ID・allocation・chain順序・所有が一致した。
- **operator nesting**: 全保存で4〜6ページの違反は0である。blockのbindingはすべて`operator_nesting = pdf-1.x-text-objects`を記録した。
- **PDF version**: 全保存の出力が`%PDF-1.4`である。
- **Poppler差分**: 各region（1pt余白込み）の外で、region間の1.5ptを含めて全保存0画素だった。
- **no-op**: 逐次3回と同時系列の1回で、全10ページのMuPDF・Poppler全画素と、計画glyph 245個の全fieldが一致した。全aliasが`reused`だった。
- **容量拒否**: `paragraphs exceed all explicitly confirmed shared regions`で拒否した。PDF・sidecarは作られていない。
- **画像**: 監査画像116枚が、前回のrun `multi-pr8-windows`の画像とPNGのbytesまで一致した。

| 保存 | chain（page-entry順、byte範囲） | text object（4/5/6ページ） | 違反 | PDF byte（前回 → 今回） |
|---|---|---|---|---|
| a-only | a[0,1414) | 58 / 85 / 86 | 0 | 367,069 → 367,088 |
| b-added | a[0,4181) b[4181,4585) | 60 / 87 / 89 | 0 | 377,455 → 377,479 |
| second | a[0,6987) b[6987,7945) | 62 / 89 / 93 | 0 | 379,168 → 379,198 |
| shorten | a[0,8431) b[8431,9604) | 64 / 91 / 98 | 0 | 376,013 → 376,034 |
| regrow | a[0,11198) b[11198,12732) | 66 / 93 / 102 | 0 | 379,071 → 379,089 |
| no-op 1 | a[0,14004) b[14004,15912) | 68 / 95 / 106 | 0 | 379,299 → 379,319 |
| no-op 2 | a[0,16810) b[16810,19092) | 70 / 97 / 110 | 0 | 379,600 → 379,618 |
| no-op 3 | a[0,19616) b[19616,22272) | 72 / 99 / 114 | 0 | 379,897 → 379,923 |
| both（同時） | a[0,2774) b[2774,3178) | 58 / 85 / 87 | 0 | 375,183 → 375,202 |
| both → no-op | a[0,5580) b[5580,6358) | 60 / 87 / 91 | 0 | 377,685 → 377,712 |

補助確認（同じ一時スクリプト、commitしていない）:

- **blockの構造**: 各保存の2 blockは外側の`q ... Q`で閉じ、text object内に`q`/`Q`はなかった。text objectの数は、b-addedの時点で`page6-a` 3・`page6-b` 1で、no-op 3の時点で15・14になった。
- **blockの描画と文字**: blockを1つだけ描いたページのインクは、`page6-a`がy 83〜93.5pt、`page6-b`がy 105〜115ptで、region間の隙間に入らない。dormantの`page6-b`は何も描かない。blockの文字はそれぞれのslotの文字と一致した。
- **所有**: aliasとslotの対応（`/PRF1`は`page6-a`、`/PRF2`は`page6-b`）は全保存で変わらず、同時と逐次でも同じだった。

目視では、逐次・同時の各段階の4〜6ページの300dpi切出しと、6ページの2 regionと隙間の600dpi切出しを確認した。どれも前回の切出しとbytesまで同じだった。行の位置、region間の隙間、dormant時の空白、図版と本来の文字に変化はなかった。

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

**外部原本で評価済み**。PR #8で用意した[評価コード](multi_destination.py)を、Windows検証環境の外部原本で実行した。
- **最初の評価**（2026-09-25、run `multi-pr8-windows`）: 評価コードは変更していない。結果は[下記](#外部原本の結果)。
- **operator nesting正規化後の再評価**（run `multi-nesting-windows`）: 評価コードにnestingとversionの照合を加えた。結果は[再評価の節](#同一ページ2-destinationrun-multi-nesting-windows)。PR #8のsession（Linux container）では原本とproviderがなく実行できなかったため、その時点では合成原本のdry-runだけを行っていた（[下記](#dry-run合成原本外部原本の証跡ではない)。外部原本の証跡ではない）。

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

## 確認済みpage-program境界の評価

[評価コード](boundary_destination.py)は、単一destination評価と同じparagraph・provider・編集・6ページの確認済み領域`[55,80,385,120]`を使う。挿入authorityだけが異なり、page entryではなく、評価者が確認したpage levelの境界に入れる。

### 評価者の指定

- **境界**: `inspect_continuation_boundaries`で6ページを調べた。候補は2つで、prefixとsuffixの両方に描画があるのは次の1つだけだった。評価者はこれを確認し、IDと証跡を評価コードに固定した。
  - ID: `boundary-b848698b464255ff0b2b6f90`
  - 位置: offset 17602。本文のtop-level `q ... Q`の最後の`Q`（序数1303）の直後で、CC-BY-SAロゴのtop-level `q`（序数1304）の直前にある。
  - 証跡: 直前・直後のoperatorのbytesのSHA-256。
  - 描画: prefixの描画operatorは286、suffixは15（ロゴのpath）である。
  - 状態: CTM identity、clipなし、不透明度1、Tr 0、stroke専用の`w 0.1`だけである。
- **検出順では選ばない**: 評価コードは、engineがこの境界を同じ証跡の安全な候補として列挙することを最初に確かめ、違えば停止する。候補一覧の最初の項目を選ぶような動的な選択はしない。
- **意味**: 生成blockは本文（文字と下線のpath）の後、ロゴの前に描かれる。領域は既に確認済みのものを使い、新しい空き領域は仮定しない。
- **Unicodeの順序**: suffixは文字を描かない（評価コードが確かめる）。そのため独立抽出器が読む6ページの文字は、本来の文字の後に生成文字が続く。

### 実行

```powershell
.\.venv\Scripts\python.exe -m evaluations.continuation.boundary_destination --run-name <未使用のrun名> --page-entry-run <同じengineのevaluate.pyのrun名>
```

成果物は`runs/boundary-<run名>/`に保存する。`--page-entry-run`を指定すると、同じengineでpage entryに入れた単一destination評価と比べる。そのrunが実行中なら、完了を待つ。

### 自動照合

単一destination評価の照合をすべて行う。6ページのUnicodeだけは上の順序で照合する。次を加える。

- **確認した境界**: authorityの`boundary_id`が変わらない。作成mutationは、確認した境界のoffsetにある順序なしのzero-length insertionである。
- **prefix / block / suffix**: 保存ごとに6ページのbytesを分け、次を確かめる。
  - prefixとsuffixを連結すると、原本の6ページprogramとbyte単位で一致する。6ページにはsource slotがないためである。
  - blockの直前・直後のoperatorは、確認した`Q`・`q`（同じbytes）である。
  - bindingは自分のmarker対とblock SHA-256を指す。
- **page entryとの比較**: 各段階の計画glyph（Unicode・GID・origin・size・advance・code・CID・`W`幅）と、Poppler 144dpiの監査画像が、page entryのrunと一致する。PDF・sidecarのbytesや、ページ全体の抽出順序は比べない。

### 結果（run `boundary-windows`、2026-09-25）

engine digest `fca1e014…a731`、評価コード`boundary_destination.py`（SHA-256 `5d9a5bf8…bbce`）。原本・provider・Poppler 26.07.0・独立pypdf 6.10.0は単一destination評価と同じで、照合も一致した。全段階と容量拒否が通った（2,940.79秒）。

入れ子の監査を加えた最終engine（digest `59fe6125…1bfd`）で、run `boundary-failclosed-windows`として再実行した（5,609.44秒）。他の評価と並行して実行したため、時間は長い。集計の違いは、engine digest・`continuation.py`のhash・比較したpage-entry runの名前とengine digestだけだった。成果物129件（PDF・sidecar・記録・画像）が、このrunとbyte単位で一致した。PR #12 engineでの回帰評価（run `boundary-ctm-boundary-regression-windows`）でも、成果物129件が再実行とbyte単位で一致した。[公開集計](boundary-destination-summary.json)は回帰評価の集計である。下の値は3つのrunに当てはまる。

| 保存 | block（6ページ、byte範囲） | text object（4/5/6ページ） | 違反 | PDF byte |
|---|---|---|---|---|
| overflow | [17602,20646) | 58 / 85 / 86 | 0 | 371,067 |
| second | [17602,23914) | 60 / 87 / 88 | 0 | 374,313 |
| shorten | [17602,24202) | 62 / 90 / 91 | 0 | 365,553 |
| regrow | [17602,27194) | 64 / 92 / 93 | 0 | 374,318 |
| no-op 1 | [17602,30282) | 66 / 94 / 95 | 0 | 374,552 |
| no-op 2 | [17602,33370) | 68 / 96 / 97 | 0 | 374,862 |
| no-op 3 | [17602,36458) | 70 / 98 / 99 | 0 | 375,147 |

- **境界**: 全保存で、blockは確認したoffset 17602（`Q`の直後、`q`の直前）にある。
  - prefixの描画operatorは286、suffixは15のままである。
  - prefixとsuffixを連結すると、原本の6ページprogramとbyte単位で一致した。
  - authorityの`boundary_id`は変わらない。作成mutationは、そのoffsetにある順序なしのzero-length insertionである。
- **allocation・slot**: 既存slot 209字（51+158）、生成slot 36字・2行で、page entryと同じである。生成slotを作るのはoverflowだけで、以後は同じslot・作成証跡を使う。shortenでは生成slotが`occupancy=None`になり、blockは同じ位置に残る。
- **font/resource**: 全保存でType0 4、所有する生成font 4（graph object 24）、4/5/6ページの`/Font` 8/9/8である。no-opでは全aliasが`reused`だった。
- **no-op 3回**: 全10ページでMuPDF・Popplerの全画素が一致した。計画glyph 245個の全fieldも一致した。
- **監査**: 各保存で次がすべて通った。
  - 編集した4〜6ページのPoppler差分が、確認済み領域（1pt余白込み）の外で0画素。
  - 対象外ページのMuPDF全画素、独立pypdfの全ページUnicode、CID/GID/`W`、元font resource、text以外のpaint、画像、annotation。
  - operator nestingの違反0、PDF version `%PDF-1.4`。
- **容量拒否**: `paragraphs exceed all explicitly confirmed shared regions`で拒否した。PDF・sidecarは作られていない。
- **page entryとの比較**: 同じengineのpage-entry run（`boundary-windows`では`boundary-single-windows`、再実行では`failclosed-single-windows`）と、全段階で次が一致した。
  - 計画glyphの全field。
  - Poppler監査画像。編集段階は4〜6ページ、no-opは全10ページ。
  - 生成文字はページ上の既存の描画と重ならないため、描画順序が違っても画素は同じである。
- **検査結果**: 6ページの候補は2つで、有用なのは確認した1つだけだった。残る1,638の境界は拒否された。拒否理由（重複あり）の内訳は次のとおりである。
  - `q`の内側 1,638、有効なclip 1,632、text object 946
  - 組み立て中のpath 320、描画モード 18、未適用のclip 2

補助確認として、保存済みの成果物を一時スクリプトで読み直した。これは評価コード外の確認で、スクリプトはcommitしていない。

- **blockの構造**: pypdfの分解器で、各保存のblockを確かめた。外側の`q ... Q`で閉じ、text object内に`q`/`Q`はなく、`Tf`は自分のaliasだけを選ぶ。blockの直前・直後は`Q`・`q`である。
- **ページの入れ子**: 4〜6ページの規則を、engineとは別の分解器で満たした。
- **blockの描画と文字**: blockだけを描いたページのインクは`[55,80,385,120]`の内側にあり、shortenでは何も描かない。blockの文字はslotの文字と一致した。

**目視**: overflow・second・shorten・regrow・no-op 3の4〜6ページの300dpi切出しを確認した。どれも、page entryのrunの切出しとbytesまで同じだった。6ページでは、本文の後、ロゴの前に描かれた2行が確認済みの領域内にあり、ロゴ・本文・図版に変化はなかった。shortenでは領域に文字が残っていない。

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
