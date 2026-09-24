# 確認済み空き領域へのcontinuation評価

**実行済み（2026-09-24、run `final-windows`）**。最終engine（digest `a20828d95176ea6b8473654f144b74d88b2579f5c89a20d6063643b31fec52e0`）と評価コード（SHA-256 `3eb35b38946906e4aef460c26a2263a4ce1612c3e2012f32bb2097d4e2ac535b`）で、外部原本の編集系列と容量拒否がすべて通った。自動照合と画像の目視の後、[公開集計](summary.json)を作成した。結果は[下記](#結果--2026-09-24)。単一外部原本の1 paragraphに対する境界評価であり、一般PDFの成功率ではない。

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
- 各保存（overflow、second、shorten、regrow、no-op）:
  - 事前planと実行planが一致する。
  - `open_shared_flow`で復元でき、確認契約hashが不変である。
  - slotは既存2つと生成1つの計3つ。生成slotを作るのはoverflowだけで、以降は作成証跡を変えずに同じslotを使う。
  - shortenでは生成slotが`occupancy=None`になり、文字を描かない。
- 各保存の監査:
  - 編集した4〜6ページで、Poppler差分が確認済み領域（1pt余白込み）の外に出ない。
  - それ以外のページはMuPDF全画素が一致する。
  - 独立pypdfで全ページのUnicodeを照合する。
  - CID/GID/`W`とfont対応を確認する。
  - 元font resource、text以外のpaint、画像、annotationが変わらない。
- final no-op:
  - 全ページでMuPDF・Popplerの全画素が一致する。
  - allocation、paragraph・style・destinationの記録、slotのidentity・行geometry・alignment・inline style（tracking・riseを含む）が変わらない。
  - 計画glyphのUnicode・GID・origin・size・advance・code・CID・`W`幅が直前のregrowと一致する。
- 容量不足: 最終状態へ160字を加えると確認済み容量（約271字）を超え、最終PDF/sidecarを公開せずに拒否する。拒否されたことだけでなく、理由が確認済みregionを使い切ったこと（`paragraphs exceed all explicitly confirmed shared regions`）まで照合する。

## 結果 — 2026-09-24

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

1. `summary.json`の`environment.engine_digest`が上のdigestと、`runner_sha256`が`3eb35b38946906e4aef460c26a2263a4ce1612c3e2012f32bb2097d4e2ac535b`と一致することを確認する。
2. overflowのallocationを以前の計画（既存slot 209字、生成slot 36字・2行）と比べる。差があれば原因を調べ、過去値へ合わせるためのコード変更はしない。
3. `runs/<run名>/`の各`*-audit/page-<n>/after.png`を目視する。対象はoverflow・second・shorten・regrowの4〜6ページと、no-opの全ページ。文字の重なり、行ずれ、不自然な余白、図版への侵入、欠落、別paragraphの破損、想定外のpaint順序を見る。
4. すべて成功した場合だけ、`runs/<run名>/summary.json`を`evaluations/continuation/summary.json`へ置き、資料の結果を更新する。

公開集計には、原本URL/hash、engine・評価コード・helperのhash、実行環境、実際に使ったproviderのfile名・face・hashと照合元集計のhash、各段階と拒否の検査結果だけを含める。これは単一外部原本での境界評価であり、一般PDFの成功率ではない。
