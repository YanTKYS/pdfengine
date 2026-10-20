# 隣接Span自動提案：カラム推定の厳密化と高速化 — PASS

記録日：2026-10-20（Asia/Tokyo）。ブランチ `claude/vibrant-hamilton-2mwfzf`。PR #80マージ後のmain
`906e4fd040ecc7d50d8e28c28e1108451b813e8a` から作り直したブランチで作業した。PR #79・#80の成果物（`tmp/osaka-adjacent-span/`、`tmp/osaka-adjacent-intent/`）は上書きしていない。

## 結論

- **カラム推定**：ページ全体の最大右端は、もう根拠にしない。編集対象段落自身の改行から決まる窓 [L, U) を必須の根拠とし、その内側で2行以上の行末が裏付ける場合だけ、L より右へ広げる
  - ページ番号・本文右側の注記・別カラムは、窓の外にあるため根拠にならない
  - 改行を証明できない場合（行末の句読点、同じ行の続き）は、矩形の明示確認が必要として拒否する
- **高速化**：同じ環境での改善前後の比較（各6回）

  | 処理 | 改善前 | 改善後 | 短縮 |
  | --- | --- | --- | --- |
  | 提案 | 8.13秒 | 5.75秒 | −29% |
  | 承認 | 10.00秒 | 5.71秒 | −43% |
  | 編集 | 17.29秒 | 12.96秒 | −25% |

  承認・保存時の確認は1つも省略していない。省いたのは、同じ操作の中で同じrevisionに対して重複していた証明だけである。
- **大阪府PDFでの回帰**：導出結果（Span62／Span63、follower Span64、境界33、矩形）はPR #80と同一。編集PDF・sidecar・no-op PDF・no-op sidecarの4つとも、PR #80の出力とSHA-256が一致し、編集PDFとno-op PDFは手動協調編集の出力とも一致した。`restored`・no-op・`restored`、承認領域外差分0、no-op全ページ差分0

## 1. 改善前の問題

PR #80の `_regions` は、カラム端に**ページ上の全可視glyphの最大右端**を採用していた。

この最大値は、ページ番号・余白の注記・右側の別カラムに引っ張られる。それを抑えていたのは、先行ownerの改行との整合確認（L + 次の文字のadvance > 最大右端）だけだった。

- 注記などが窓の外にあると、整合確認で拒否されてしまう（本来は無視すべき要素で、有効な提案が拒否される）
- 窓の中にある1本だけの注記は、そのまま根拠になる（裏付けがない）

## 2. 新しいカラム推定の根拠と拒否条件

`pdfeditor/adjacent_intent.py` の `_column_edge`。

1. **改行窓**：先行ownerは後続ownerへ改行しているので、カラム端は [L, U) にある
   - L：先行ownerの行末
   - U：L + 後続ownerの先頭文字のadvance（Wordが入れられなかった幅）
2. **裏付け**：先行ownerの行以外で、可視glyphの行末が窓の内側にある行を数える。行頭・行末禁則でぶら下がりうる句読点 `、。，．,.` で終わる行は数えない
   - 2行以上の裏付けがあれば、その最大値をカラム端とする（`corroborated line ends`）
   - 1行以下なら L に留める（`wrapped line end only`）
   - U 以上にある行末（ページ番号・注記・別カラム）は、決して根拠にしない（`ignored_beyond_window` に最大5件を記録）
3. **拒否条件**（いずれも「明示的な矩形が必要」として拒否する）：
   - 先行ownerの最後の文字が、ぶら下がりうる句読点か空白（改行がカラム端を示さない）
   - 先行ownerの行で、窓の内側に別のglyphが始まる（同じ行が続いている）
   - 先行ownerが1行でない、または後続ownerが次の行にない
   - 後続ownerの現在の右端が、導出したカラム端を超える
   - 導出した矩形にdrawingが入る
   - 裏付けのないカラム端（L）で、どの境界も適格にならない（メッセージ：`… the column edge is not corroborated; an explicit rectangle is required`）
4. 同じ行の他ownerのglyph（右側にある別カラムを含む）の手前で止める処理、clipによる切り詰め、上下の帯（他の行との間隔の半分）は従来どおり

既存のsource ownership・衝突・clip・ActualText・follower契約の検証には手を加えていない。最終的な証明は、引き続き `propose_adjacent_span` / `plan_adjacent_span_edit` が行う。

**大阪府PDFでの適用結果**：

- 窓は [522.914, 533.474)
- 裏付けは、文書番号の行「号」524.14pt と日付の行「日」524.62pt の2行（どちらも右寄せの見出し行）
- 「…お願いいたします。」の行（522.92、「。」）は句読点で終わるので数えない
- 段落記号の空白（524.5〜529.78）は可視glyphでないので数えない
- カラム端は524.62pt で、PR #80と同じ値

**この2行が根拠になっていることは、利用者の確認対象である**（`requires_confirmation`）。2行の裏付けがなければカラム端は L＝522.914pt に留まり、Span62の必要幅347.20pt > 上限346.85pt となって拒否される。閾値は大阪府PDF向けに調整していない。可変の値は「2行以上」と「句読点の集合」だけで、窓の上限は文書自身の改行から決まる。

## 3. 誤認防止fixture（`tests/test_adjacent_intent.py`）

基本fixtureは次のとおり。

- P1：先行の他owner、本文1（x=100〜268、16文字）、本文2、空白follower
- P2：右寄せ行（〜270）、右寄せ日付行（〜270）
- 改行窓は [268, 278.5)

| fixture | 結果 |
| --- | --- |
| 通常の単一カラム | 裏付け2行 → カラム端270、境界16、follower +21 で提案 |
| 本文右側のページ番号（右端570.5） | 窓の外として無視。カラム端270、提案は同じ |
| 本文より右の右寄せ注記（右端300） | 無視。カラム端270 |
| 同じ行の右側にある別カラム（x=290から） | 容量に含めない（矩形の右端 ≤ 270）。提案は同じ |
| 裏付け行が1本だけ | 根拠にしない。カラム端は改行端の268に留まる |
| 行末が「、」 | 拒否（`hanging punctuation`） |
| 窓の内側（x=274）で他カラムのglyphが始まる | 拒否（`continues the first owner`） |
| 裏付けがなく、末尾の文字の置換で容量が足りない | 拒否（`not corroborated`） |
| 2行目の移動先に、構造上無関係なforeign glyph | 拒否（follower移動先が承認矩形を出る） |
| 既存の提案・承認の契約 | 維持。ambiguous時の明示選択、改ざん・stale・未承認の拒否、手動編集とのcontent stream一致 |

## 4. 性能計測とボトルネック

`evaluations/real_japanese/osaka_adjacent_intent_timing.py` で計測した。改善前のコード（`906e4fd` のgit worktree）と改善後のコードを、同じ環境・同じスクリプトで計測している。

- 1セット：提案→承認→編集（毎回、原本から新しいtmpディレクトリへ保存）を3回、その後cProfile付きで1回
- 改善前は before-1・before-2、改善後は after-1・after-2 の各2セット

計測用の保存はすべて `tmp/osaka-adjacent-intent-timing/` にあり、評価結果としては扱わない。12回すべての編集PDFは、SHA `79840e33…98c2` で同一だった。

改善前のcProfileで、時間の大半を占めていたもの（呼び出し回数）：

| 処理 | 提案 | 承認 | 編集 |
| --- | --- | --- | --- |
| `ContentPage` の解析（`_walk`） | 27 | 33 | 58 |
| `observe_marked_content` | 34 | 42 | 74 |
| `_observe_pair`（各ownerのleaf証明＋結合証明） | 4 | 5 | 8 |
| `plan_adjacent_span_edit` | 3 | 3 | 4 |
| `propose_adjacent_span` | 1 | **2** | 1 |
| `inspect_paragraph` | 8 | 10 | 16 |

重複の原因は次の3つだった。

- 境界候補ごとの `plan_adjacent_span_edit` が、毎回同じrevisionの `_restore`（`_observe_pair`）と、followerの基準幅を求める別Transactionを繰り返していた
- 承認時に、提案の再導出（`propose_adjacent_span` を含む）と、`accept_adjacent_span`（内部で再び `propose_adjacent_span`）が二重に証明していた
- 編集時に、提案の再導出の後で、同じ境界の読み取り専用planをもう一度実行していた

StructureTreeの解析（`structure_objects`）、font programの抽出（`extract_font`、合計0.1秒未満）、shapingは主要因ではなかった。

## 5. 実装した高速化

1. **`iter_adjacent_span_plans`**（`pdfeditor/adjacent_span.py`、読み取り専用）
   - 1回の呼び出しの中で、モデルの `_restore`（現在のrevisionの証明）を1回だけ行う
   - そのうえで境界ごとに、分割・provider検証・新しいTransaction・容量・clip・衝突・follower・provenanceの証明を、それぞれ独立に行う
   - followerの基準幅（後続ownerの無編集時の行）は、同じ呼び出しの中で1回だけ計算する
   - 既存の `plan_adjacent_span_edit` は、これを1境界で呼ぶだけになった（挙動・例外は同じ）
2. **承認・編集時の再導出（`_derive(..., claimed=…)`）**
   - 提示された隣接Span提案が、再導出したowner（selection・矩形）・followerと完全に一致することを確かめる
   - そのうえで `accept_adjacent_span` を**1回だけ**実行し、再提案と比較する（従来の二重実行の片方を削除）
   - 証明済みのfollowerを提案から落とすことは、follower契約が拒否する場合を除いて拒否する
3. **編集前の重複plan**を削除した。再導出した提案に、同じrevision・同じモデルで計画した選択境界の結果（分割・follower移動量）が含まれ、それを承認内容と照合している
4. `ContentPage` を1回の導出の中で共有した（`_discover`・矩形・provider・followerの位置）

改善後の呼び出し回数：

| 処理 | 提案 | 承認 | 編集 |
| --- | --- | --- | --- |
| `ContentPage` の解析 | 16 | 16 | 40 |
| `observe_marked_content` | 20 | 20 | 51 |
| `_observe_pair` | 2 | 2 | 5 |
| `propose_adjacent_span` | 1 | 1 | 1 |
| `inspect_paragraph` | 4 | 4 | 10 |

キャッシュ基盤や永続キャッシュは導入していない。共有しているのは、1回のAPI呼び出しの中にあるローカル変数だけである。

## 6. 改善前後の時間比較（同じ環境、壁時計時間、秒）

| セット | 提案 平均±SD | 承認 平均±SD | 編集 平均±SD |
| --- | --- | --- | --- |
| before-1（`906e4fd`） | 8.40 ± 0.15 | 10.20 ± 0.31 | 17.74 ± 0.28 |
| before-2（`906e4fd`） | 7.86 ± 0.10 | 9.79 ± 0.18 | 16.83 ± 0.11 |
| after-1（本ブランチ） | 5.67 ± 0.11 | 5.66 ± 0.11 | 12.91 ± 0.46 |
| after-2（本ブランチ） | 5.83 ± 0.23 | 5.77 ± 0.21 | 13.02 ± 0.32 |
| **改善前6回** | **8.13 ± 0.32** | **10.00 ± 0.32** | **17.29 ± 0.53** |
| **改善後6回** | **5.75 ± 0.18** | **5.71 ± 0.16** | **12.96 ± 0.36** |
| 短縮 | −29.3% | −42.8% | −25.0% |

編集の残り約13秒のうち約7秒は、`edit_adjacent_span` 内部の保存前後の検証である（`_restore`、保存後の再観測、`_restore`（保存物）、公開前の意味構造検証、Transactionの検証）。安全性条件そのものなので、今回は変えていない。

これ以上の短縮には、`_observe`（leaf証明・`inspect_paragraph`・`resolve_selection`）が、同じ1回の操作の中でページを再解析する構造を見直す必要がある。複数moduleにまたがる変更になるため、今回は実装していない。

## 7. 安全性検証が維持されている証拠

| 確認 | 改善後の所在 |
| --- | --- |
| source SHA・revisionの一致 | 承認・編集で `pdf_sha256` を照合。`edit_adjacent_span` の `_restore` と、Transactionの計画中の変更検出 |
| selection・ownershipの一致 | 承認・編集のたびに `_discover`（selectionのSHA・ページ観測SHA）を再実行。`accept_adjacent_span` による隣接Span提案の再証明 |
| owner・MCID・親子構造・ActualText | `accept_adjacent_span`（leaf証明・結合witness・無編集の再現とpixel証明）を承認・編集で各1回。境界の判定とeditでの `_restore` |
| 承認された矩形と境界 | 再導出した矩形・候補を、提案と完全に比較（改ざんテスト：矩形の拡大、内側の提案の矩形改ざん、follower削除、境界の改ざん） |
| font providerの同一性 | 再導出したprovider（program SHA）と承認内容を照合。`_qualify` / `_provider` のSHA・face・単一glyphの検証 |
| 新glyphと元glyphの証明 | 境界ごとの `_provenance`。保存時のfont binding（`edit_adjacent_span` は変更なし） |
| followerの配置・衝突 | 境界ごとの `FollowerPlan.check` と、Transactionの最終状態の重なり検査 |
| 保存後の意味構造の検証、PDF・sidecarの公開前検証 | `edit_adjacent_span` と、Transactionの `verify_adjacent_span_output`（変更なし） |

省略した確認はない。削除したのは、同じ操作の中で同じrevisionに対して二重に走っていた `propose_adjacent_span`、境界ごとに繰り返していた同一モデルの `_restore`、再導出と重複していた編集前のplanだけである。

## 8. Focused tests

- `.venv/bin/python -m pytest -q tests/test_adjacent_intent.py tests/test_adjacent_span.py tests/test_single_span_actualtext.py tests/test_tagged_page_proposal.py tests/test_transaction.py tests/test_marked_content.py`：**147 passed（131.36秒）**
  - `test_adjacent_intent.py` は23件。カラム誤認防止7件と、内側の隣接Span提案の改ざん検査を追加した
  - テスト補助関数 `make_source` に `extra`（追加の行）を加えた。既定値は従来どおり

full suite、3ページ評価、過去の実評価は実行していない。

## 9. 実PDFでの結果（`--root tmp/osaka-adjacent-intent-quality`）

- 入力：1ページ目の「において」（唯一の描画出現、glyph `[149..152]`）、置換文字列「におきまして」
- 導出結果：
  - owner：Span62／MCID28 → Span63／MCID29（親P58）
  - follower：Span64／MCID30
  - 境界：33（「に送」を移動）
  - 候補：移動0・1文字は1行に収まらず拒否、2文字が適格かつ自然な分割
  - follower移動量：+21.12pt
  - 矩形：Span62 (176.060, 333.339, 524.620, 351.339)、Span63 (70.944, 351.339, 524.620, 369.339)、follower (144.5, 351.339, 524.620, 369.339)。PR #80と同一
- 承認条件（8項目すべて真）を確かめてから承認を模擬し、保存・`restored`・no-op・`restored`
- 監査：
  - 親P58：50→52文字
  - 2件のActualTextが同時に更新された
  - 構造の変化は `62:0` / `63:0` のActualTextだけ
  - MCID・子の順序は不変。他の94 scope・drawing 4件・元のfont programも不変
  - followerの意味情報は不変で、変化はTmの平行移動だけ
  - provenance：残存34、再配置2、新規6
  - 新規・再配置の8 glyphについて、font（CID・GID・輪郭・advance）を独立に照合
- MuPDF・Poppler 144dpi：承認領域外の差分は0 pixel、no-opはページ全体で0 pixel
- 過去の出力との一致：

  | 比較対象 | 編集PDF（content stream・SHA） | no-op PDFのSHA | sidecar |
  | --- | --- | --- | --- |
  | 手動協調編集（PR #79） | 一致 | 一致 | — |
  | 自動提案（PR #80） | 一致 | 一致 | 2つとも一致 |

  sidecarが一致するのは、導出した矩形がPR #80と同一だからである。SHAが変化した出力はなかった。
- SHA-256：

  | 出力 | SHA-256 |
  | --- | --- |
  | 原本 | `9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe`（不変） |
  | `edited.pdf` | `79840e338c2097e6712166a651feeccd5242344b93721b1acaf22465aeec98c2` |
  | `edited.json` | `715b33062c4673acfebd0998715f9d3c77d81bde57b1167559f9289411a89e5f` |
  | `noop.pdf` | `16196bfd015d10944dab618c2bf1d4f2e9bc2542e3e10346e09812214d078386` |
  | `noop.json` | `56259532ddea37e79845eeb1c47db49d3ed95cf2269d62a3ce05f5bf5ce37722` |

- 実行時間（評価スクリプト）：合計38.44秒（PR #80の評価は49.12秒）

  | 処理 | 時間 |
  | --- | --- |
  | 提案 | 5.94秒 |
  | 承認 | 6.10秒 |
  | 編集 | 13.26秒 |
  | 監査 | 5.08秒 |
  | no-opと監査 | 8.05秒 |

  性能の比較には、第6節の同一環境での計測を使う。

環境：Linux、Python 3.13.16、PyMuPDF 1.27.2.3、pypdf 6.10.0、Poppler pdftoppm 24.02.0。

## 10. 未解決事項・制約

- 裏付けは「改行窓の内側にある、2行以上の可視glyphの行末」。大阪府PDFでは右寄せの見出し行（文書番号・日付）がこれに当たる。同じ版面の右余白に合わせた行だという解釈は、利用者の確認事項として提案に残している
- 1本のbaselineに複数カラムの文字が並ぶページでは、行末が窓の外に出るため、裏付けとして数えない（安全側）
- 編集時の残り約7秒は、`edit_adjacent_span` の検証そのもの。`_observe` 系が1回の操作の中でページを再解析する構造は、複数moduleにまたがる変更が必要なため今回は見送った

## 再実行

```
.venv/bin/python -m evaluations.real_japanese.osaka_adjacent_intent --root tmp/<new directory>
PYTHONPATH=<code tree> .venv/bin/python evaluations/real_japanese/osaka_adjacent_intent_timing.py --label <new label>
```

計測値は [osaka-adjacent-intent-quality-summary.json](osaka-adjacent-intent-quality-summary.json) に記録した（評価のsummaryと、4セットの計測・呼び出し回数）。
PDF・font・sidecar・raster・tmp成果物はコミットしていない。
