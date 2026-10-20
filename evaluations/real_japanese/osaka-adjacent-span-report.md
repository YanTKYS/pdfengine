# 大阪府Word由来PDF：隣接Span協調編集（Span62 → Span63）— PASS

記録日：2026-10-20（Asia/Tokyo）。ブランチ `claude/vibrant-hamilton-2mwfzf`。
PR #78マージ後のmain `b1551c0b8b3e1bfa16c2f31ba59bbec8c61de5cd` と、単一Spanの限界を測定した
`7f7d626`（[osaka-single-span-growth-report.md](osaka-single-span-growth-report.md)、
`osaka_span_growth.py`）の上で作業した。過去の評価は再実行していない。

## 結論

**PASS — 実在するWord由来PDFで、親P58の本文が2文字増える編集を、2つの本文ownerへの文字再配置と
2件のActualText更新を1つのTransactionで保存し、`restored`・no-op・`restored`まで確認した。**

成功保存は2回（長文化編集、保存済みrevisionからのno-op）。編集後の監査のうち1回は、評価スクリプト自身の比較の誤りで中断した。保存はやり直さず、保存済みの成果物から監査を再開した（後述）。

この編集は評価用の成果物であり、大阪府の公式な訂正版ではない。

## 対象とowner

- PDF：`evaluations/realpdf/corpus/word_osaka_guideline.pdf`（[原本URL](https://www.pref.osaka.lg.jp/documents/63648/guideline_tsuuchi.pdf)）
- 原本SHA-256（評価前後で不変）：`9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe`
- 1ページ目、親構造 `P58`（子：Span59, 60, 61, 62, 63, 64）

| 役割 | owner / MCID | 編集前 | 編集後 | 文字数 |
| --- | --- | --- | --- | --- |
| 本文1（first） | Span62 / MCID28 | `国立感染症研究所においてガイドラインが作成されましたので、参考に送` | `国立感染症研究所におきましてガイドラインが作成されましたので、参考` | 33→33 |
| 本文2（second） | Span63 / MCID29 | `付いたします。` | `に送付いたします。` | 7→9 |
| 空白follower | Span64 / MCID30 | `" "`（ActualText `" "`、BDC `/Lang (en-US)`） | 同左（位置のみ +21.12pt） | 1→1 |

各ownerのActualTextは、編集後の描画Unicodeと一致した。

P58全体（子ownerのActualTextを読み順59→64で連結したもの）は次のとおり。

- 編集前（50文字）：`標記につきまして、国立感染症研究所においてガイドラインが作成されましたので、参考に送付いたします。 `
- 編集後（52文字）：`標記につきまして、国立感染症研究所におきましてガイドラインが作成されましたので、参考に送付いたします。 `

読み順（P58の `/K`）は `[59, 60, 61, 62, 63, 64]` のまま変わらない。Span59〜61には所有権を広げていない。

## 編集と境界

- 論理編集：Span62+Span63の連結本文の `[8,12)`「において」を「におきまして」に置換（+2文字）
- 明示した新しい境界：33（Span62の編集後の長さ）
- 境界の移動量：2文字。Span62末尾の「に送」がSpan63の先頭へ移る（first → second の順方向のみ）

最初の分割候補を、そのまま採用した。根拠は次のとおり。

- Span62の33文字は1行に収まる。レイアウト幅347.202pt ≤ 承認幅348.940pt
- Span62に34文字以上を残すと、どの可視glyphも収まらない。前回の測定で、Span62の残り容量2.086pt < 最小可視advance 5.28pt
- 「参考／に送付」は語の切れ目で、1文字だけ送る「参考に／送付」より自然

Span63は1行（幅94.808pt）で、行頭位置 x=70.944 は不変。

承認した矩形（拡大・縮小なし）：

- Span62：`(176.05999755859375, 335, 525, 351)`（前回承認と同じ右端）
- Span63：`(70.94400024414062, 353, 525, 369)`
- Span64 follower：`(144.5, 353, 525, 369)`

右端525ptは、観測した本文カラムの右端524.62pt（右寄せの「日」）による。ページ余白までは広げていない。2行目にはSpan63・Span64以外のglyphもdrawingもない。

## Span64の扱い

指示どおり、まずSpan64を動かさずに試した。

| Span63の矩形 | 結果 |
| --- | --- |
| 列右端525まで（followerを承認しない） | proposalで拒否：`single Span rectangle adds foreign glyph capacity`（Span64の空白glyphを他ownerの容量として取り込むため） |
| Span64の手前（x1=144.632）まで | proposalで拒否：無編集レイアウトが元bbox幅を超え、1行に収まらない |

空白を見えないものとして重ねることは、既存の容量検査が拒否する。そのためSpan64を、本文を書き換えないfollowerとして明示承認した。followerに求めた条件は次のとおり。

- `/Span` leaf、tree-backed、backlink検証済み、MCIDとownerが1対1、P58の次の兄弟（index 5）
- content streamでもSpan63の直後（間は空白文字のみ）
- 描画Unicodeは空白のみで、ActualTextと一致
- scopeは `q re W* n BT Tf Tm g G TJ ET Q EMC`、Tmは1回で平行移動のみ、CTMは単位行列、矩形クリップ1つ
- 移動量は、Span63の編集後と無編集時のレイアウト幅の差（+21.12pt＝10.56pt×2）。元のペン位置との関係（−0.13pt）を保つ
- 変更はTm第5オペランドの1箇所だけ（`144.5` → `165.62`）。BDC・`/Lang`・MCID・ActualText・glyph code・GID・fontは不変
- 移動先は、承認矩形内・クリップ内・ページ内にあり、他ownerのglyph box・drawingと重ならない。Span63の新しいinkとも重ならない（Transactionの最終状態チェック）
- 他のownerへの追従の連鎖はない（followerは1つまで、次の兄弟だけ）

## Glyph provenance・font証明

| 区分 | 件数 | 内容 |
| --- | --- | --- |
| retained（元ownerに残る既存glyph） | 34 | Span62の27字、Span63の7字。元の `/F1`（xref5）のcode・GID・font programを維持 |
| relocated（owner境界を越えて再配置） | 2 | 「に」「送」。元はSpan62（`/F1`、code `085F`/`33E6`、GID 2143/13286）。Span63の `/PRF2` で再描画し、GIDは元と一致 |
| inserted（新規置換） | 6 | 「におきまして」。Span62の `/PRF1` |

relocatedの記録には、元owner（first）と元offset（31・32）を残している。元ownerに属したままのglyphとしては扱っていない。

- provider：元の埋め込み `/F1` program。SHA `5e5ded281865f30d096f0f2b8b55969b1f80ea9396cc4e98af9acba406f2eddf`、face 0、instance `7443a3982f11668db9d24914395658256a23cbc72724de176b1ce84108388df5`
- `/PRF1`（xref173）subset：`1ad9d06f57c216ca4765c331b851ab9f1f665e8b1e6657494b8b3a48ce49d5ab`。CID 0001–0006、GID 2143, 2110, 2113, 2162, 2123, 2138
- `/PRF2`（xref179）subset：`a244d729670d488e2174ccc106912ff280629b838f09d13b520bc41ee44b260b`。CID 0001–0002、GID 2143, 13286

新規・再配置の8 glyphすべてについて、保存PDFのsubsetから次の点を独立に照合した。

- CID code → GID
- 輪郭（DecomposingRecordingPen）がproviderと完全一致
- hmtx advance 256/256 units、PDF幅1000
- provider shapingのGID

再配置glyphは、さらに元glyphのUnicode・GIDとも一致した。元のfont program 4つ（xref5・12・14・17）はbyte一致で残っている。

sidecarには、owner別のfont binding（元resource・program SHA・生成subsetの記録）を保存した。再openのたびに、描画されている生成glyphのUnicode・GID・輪郭・advanceをproviderと照合する。

## ActualText・構造ownerの監査

- 構造グラフ全体のうち、変化したのはSpan62（`62:0`）とSpan63（`63:0`）のActualTextだけ。この2件を除いた構造グラフは、byte単位で一致した
- MCID・ParentTree・backlink・`/K` の順序は不変。97 MCIDはすべてtree-backedかつ一意
- 他の94 scopeは、owner・association・ActualText・glyph（code・GID・font・位置）・envelopeが完全一致。後続glyphの通し番号とbyte位置は+2ずれる
- follower（Span64）は、owner・association・BDCプロパティ・ActualText・glyph identityが不変で、位置だけが+21.12pt
- 保存時のcompare-and-set：2件のActualTextの更新前の値と、両方を除いた構造が一致した場合に限り、両方を同時に更新する。直列化したPDFでも再確認する
- 公開前の検証：一時PDFから、両owner・親子関係・followerを再観測して照合する

## Foreign content・クリップ

- 各ownerのenvelopeは、元の `q 0.000008871 0 595.32 841.92 re W* n` から `BT` までのbyteを保持し、`ET Q EMC` で終わる。クリップwitnessは不変
- 3つのenvelopeの外側のcontent stream byte、envelope間の空白byte、drawing 4件（下線3本・枠1つ）はすべて一致
- 書き換えたのは、Span62・Span63の選択text operator（TJ）と、Span64のTm 1箇所だけ

## Transactionの更新対象

1つのTransactionに3つのplanを含めた。

- Span62の段落plan（role `first`）
- Span63の段落plan（role `second`）
- Span64のfollower plan（role `follower`）

`adjacent_span_update` は、次の条件を満たす場合だけ、1件の結合更新（contract `adjacent-span-actualtext-1`）を作る。

- 3つのplanが同一の結合witnessに束ねられている
- Transaction内にそれ以外のplanがない
- followerの有無が承認内容と一致する
- 現在のrevisionで結合witnessを再取得すると、承認時と一致する

単一Spanの保存は、従来どおり1 plan・1 ActualTextに限られる。2つの契約を同じ保存で混在させることも拒否する。removal-onlyのcheckpointも拒否する。

## 144dpi比較

| 比較 | MuPDF | Poppler |
| --- | --- | --- |
| 原本 vs 編集後：承認した3矩形の外側 | 0 pixel | 0 pixel |
| 編集後 vs no-op：ページ全体 | 0 pixel | 0 pixel |

マスクは、承認した3矩形と交わるraster cellだけで、余白や許容誤差は加えていない。差分のbboxは144dpiで (143,674)–(1046,730)（MuPDF。Popplerは x0=142）で、矩形の内側にある。no-opはcontent streamがbyte一致し、`observe` の全観測値も一致した。

## 保存・復元

| 操作 | 再open | SHA-256 |
| --- | --- | --- |
| 長文化編集 `tmp/osaka-adjacent-span/edited.pdf` | restored | `79840e338c2097e6712166a651feeccd5242344b93721b1acaf22465aeec98c2` |
| sidecar `edited.json` | — | `013466725e4c17adcf4cb47aaa702fbf7aa4ce06170371f15d658e427bad3782` |
| no-op `tmp/osaka-adjacent-span/noop.pdf` | restored | `16196bfd015d10944dab618c2bf1d4f2e9bc2542e3e10346e09812214d078386` |
| sidecar `noop.json` | — | `a70d39d47dce7c13191068370a80b56101742ebb0033e551ece87fa59d419dac` |

pypdfのfull-load時に、未定義のobject ID 4・28・33・167の警告が出た。PR #78のレポートと同じIDで、出力のtrailerから到達しないものである。

## 実行時間と再開の経緯

- 1回目（18.53秒）：proposal・plan・編集保存までは成功した。直後の監査で、他ownerの比較が中断した。原因は評価スクリプトの誤りで、glyphが2つ増えたことで後続glyphの通し番号（`ids`）が+2ずれるのに、比較対象に含めていた。各ownerの観測値に差はなかった
- 2回目（20.33秒）：`ids` を比較から外し、保存済みの `edited.pdf` / `edited.json` から監査を再開した（`edited_audit_resumed_without_resave`）。read-only planが保存前の `plan.json` と一致することを確認し、そのplanのprovenance・移動量で監査した。保存は行っていない
  - 内訳：qualification 9.51秒、編集後の監査 2.85秒、no-opと監査 7.97秒
- 環境：Linux、Python 3.13.16、PyMuPDF 1.27.2.3、pypdf 6.10.0、Poppler pdftoppm 24.02.0

## production変更

- `pdfeditor/adjacent_span.py`（新規）
  - 契約：schema `pdfengine-adjacent-span-actualtext-1`
  - 提供する機能：proposal・承認・`_split`（境界とprovenance）・owner別の段落plan・`FollowerPlan`・保存・再open
- `pdfeditor/marked_content.py`
  - `adjacent_span_structure`：両ownerにそれぞれ既存の `leaf_span_structure` を課したうえで、次を証明する
    - 同じ親であること
    - `/K` で連続していること
    - content stream上で連続していること
    - 2つ目が次の行にあること
  - followerの構造証明
  - `structure_objects` で、ActualTextを除外するownerを複数指定できるようにした（tuple）
- `pdfeditor/source_ownership.py`：結合witness、結合更新、保存物の検証
- `pdfeditor/transaction.py`：`adjacent_span_witness`・`adjacent_span_role` と、commit時の結合更新
- `pdfeditor/pdf_save.py`：結合更新のときに限り、2件のcompare-and-setを行う。単一Spanは1件のまま
- `pdfeditor/span_edit.py`：`_observe` に `follower` 引数（既定は空）。承認されたfollowerのglyphだけを、2つ目のownerの容量チェックから除外する

StructureTree・ParentTreeの再構築、MCIDの新規割当て、別のwriterの追加は行っていない。

## Focused tests

- `.venv/bin/python -m pytest -q tests/test_adjacent_span.py`：**28 passed（15.98秒）**
  - 正常系：再配置、増加を伴う置換、2件のActualText、連結本文、provenance、followerの移動、保存・再open・再編集（2回目のfollower移動を含む）・no-op（MuPDF・Poppler）・構造と兄弟順序の保持
  - 拒否系：
    - owner関係：親が違う、隣接していない、順序が逆
    - follower：空白でない、次の兄弟でない、矩形が他ownerの容量を取り込む
    - 構造：ActualTextの不一致、ParentTree・backlinkの不正、followerの意味情報の追加
    - 分割・font：逆方向の移動、新規文字が境界を越える、境界上の編集、2 ownerにまたがる編集、coverage不足、provider未指定、容量不足、境界の未指定
    - followerの位置：承認なしでの重なり、移動先のforeign glyph、クリップ外への配置
    - 状態：stale revision、改ざんした状態
    - 部分適用：片方だけのActualText、1 ownerだけの更新、意味検証の失敗、sidecarの部分公開
    - Transaction：planの過不足、witnessの不一致
- `.venv/bin/python -m pytest -q tests/test_single_span_actualtext.py tests/test_marked_content.py tests/test_tagged_page_proposal.py tests/test_transaction.py tests/test_pdf_save.py tests/test_source_ownership.py`：**177 passed, 2 skipped（131.48秒）**。skipはAES providerがない環境のため

full suite、3ページ編集、前回までの実評価は実行していない。

## 制約と未対応

- 対象は1つの親P内の、連続する2つの本文leaf Spanと、最大1つの空白follower。文字の移動は1つ目から2つ目への順方向だけで、移動先の見た目（size・倍率・rise・色）とfont programが一致する場合に限る
- 編集は、どちらか一方のownerの内部に収まる1件だけ。ownerの境界上の編集、ownerの改行（wrap）、任意の複数ownerへの再配分、別段落への移動はすべて拒否する
- 容量不足の拒否メッセージは、既存の `_provider` が `LayoutError` を包むため、`single Span provider qualification failed: …` と表示される（前回からの既知事項）

## 再実行

```
.venv/bin/python -m evaluations.real_japanese.osaka_adjacent_span
```

出力は `tmp/osaka-adjacent-span/`。`noop.pdf` が存在する場合は実行を拒否する。PDF・font・sidecar・rasterはコミットしていない。
計測値は [osaka-adjacent-span-summary.json](osaka-adjacent-span-summary.json) に記録した。
