# Phase 2a 統合前レビュー: 行レイアウト由来 spacing を SourceStyle から分離する変更境界

2026-09-14。前提は [fable5.1-review2.md](fable5.1-review2.md) と [spacing-classification.md](spacing-classification.md)（PR #2、成立済み）。本書は実装せず、既存コードの責務境界から「壊さずに分離できる線」と最初の実装 PR を確定する。

## 1. `SourceStyle` が兼ねている責務

`attributed.SourceStyle` の各 field の実際の消費者を追った結果、3 つの責務が 1 つの dataclass に同居している。

| 責務 | field | 消費者 |
|---|---|---|
| A. 論理 inline style の同一性 | `size`, `horizontal_scale`, `tracking`, `word_spacing`, `rise`, `color`, `font_name`（鍵は `attributed.py` の `digest([...])`） | `snapshot()` の `spans`、`apply_edits` の `style_id`、`editable._bind_paragraph` の export 比較、`story_flow._style` / `story_styles` の同質性検査、`destination_style` の recipe |
| B. 元 PDF の描画状態（witness） | `event.state`（`Tf` size、`Tz`、fill、font xref/name）、`matrix` | `paragraph.py` の writer prefix（`Tf`/`Tz`/fill/`Tm` basis）、`source_ink` の font xref、`story_styles` の `paint_context_sha256`、`anchors` の fill 比較 |
| C. 組版 metrics | `size`, `horizontal_scale`, `tracking`, `word_spacing`, `rise` | `ParagraphShaper.shape` の新 glyph advance（`glyph.advance*sx + tracking + word_spacing`）、保持 glyph の末尾補正（`advance -= style.tracking`）、rise の offset と ascent/descent |

`tracking` と `word_spacing` は A と C の両方に使われているが、B（replay witness）には使われていない。source replay と removal 検証は `patch_streams` が元 bytes と元状態をそのまま再出力するため、`SourceStyle` の値に依存しない（`paragraph.plan_paragraph_edit` の replay / removed program）。no-op 検証も `compare_glyphs` と画素比較で、style 値を見ない。したがって **識別鍵から外しても replay・removal・no-op の witness は壊れない**。

壊れるのは A と C の消費者であり、そこが変更境界になる。

## 2. 「識別から外す」と「観測として捨てる」の区別

元 PDF の `Tc` / `Tw` は 3 か所に既に存在し、`SourceStyle` から外しても失われない。

- `SourceUnit.char`（`PaintChar.advance` と `pdf_width`）: glyph ごとの実 advance と名目幅。
- `paragraph.events` の `TextEvent.state`: glyph ごとの `Tc` / `Tw` / `Ts` / `Tz`。`SourceUnit` は現状 event への参照を持たないため、`event` field を追加する（`mapping[index] = (style_id, char)` の箇所で event も渡す）。
- `spacing.observe_spacing`: 行ごとの分解と候補。

観測値の置き場所は **`SourceUnit`（glyph 単位）と snapshot の `spacing`（行単位）** とし、`SourceStyle` には置かない。新しい型階層は作らない。

## 3. 値の責務（observed / candidate / confirmed）

| 値 | 置き場所 | provenance | 使い方 |
|---|---|---|---|
| glyph ごとの観測 `Tc` / `Tw` / advance | `SourceUnit.char`, `SourceUnit.event.state` | `observed_source` | 左揃えの保持 glyph advance、末尾補正、証拠計算。捨てない |
| 行ごとの分解と分布 | snapshot `spacing.lines` | `observed_source` | 候補生成の根拠。review 用 |
| tracking 候補 | snapshot `spacing.tracking`（`requires_confirmation`） | `inferred_consistent_tracking` / `observed_source`(0) / `unknown` | 確認入力の既定候補。writer には直接使わない |
| alignment 候補 | snapshot `spacing.alignment_candidates` | `alignment_candidate` / `unknown` | 同上 |
| 確定 tracking | `SourceStyle.tracking` + `SourceStyle.tracking_provenance`、sidecar `logical_element.tracking` | `explicitly_confirmed`、0 のときのみ `observed_source` | 新 glyph advance、両端揃え時の保持 glyph 基準、PDF への witness（次 PR） |
| 確定 alignment | sidecar `logical_element.alignment` | `explicitly_confirmed`（left 既定は `generated_layout_policy` のまま） | 保持 glyph advance の切替、layout（後続 PR） |
| 生成 layout の spacing | 保存 PDF の glyph 位置（`Tm` 単位） | `generated-by-pdfengine` | 再観測では `uniform_reposition` / `none` になり候補を生まない。確定値は sidecar が権威 |

`SourceStyle.word_spacing` は inline style ではないため field ごと廃止する（`Tw` は行の仕組み）。`rise` は inline のまま残す（`Ts` は上付き等の文字属性で、両端揃えは `Ts` を変えない）。

## 4. `SourceParagraph` の同一性規則

鍵を `(font xref, font name, size, scale, rise, fill, color)` にすると、行ごとに `Tw` / `TJ` が異なる本文は 1 style になる。一方で「本当に異なる tracking」を誤って統合しないための規則を、観測だけから次のとおり決める。

- 鍵で併合した style について、物理行ごとに `Tc` の集合を取る。**同じ行の中で `Tc` が 2 値以上**なら、それは行揃えでは起きない inline の変化なので `Tc` 値で style を分割する（`observed_source`）。
- 行内では 1 値だが行間で異なる場合は併合を維持し、`spacing.tracking` を `unknown` とする。
- 全行で 1 値なら `inferred_consistent_tracking`（0 なら `observed_source`）。

この規則は spacing classifier の再設計ではなく、その出力を style 分割の判断に使うだけである。`Tw` は分割に使わない（単語間隔の inline 意図は観測から証明できない）。

## 5. 保持 glyph advance の切替を持つ層

`paragraph.ParagraphShaper.shape` が持つ。理由: origin 差か名目かの選択は「元 glyph をどう再利用するか」であり、source の観測（`SourceUnit`）と論理の確定値（alignment, tracking）の両方を知るのはこの層だけ。`rich_layout` は `InlineGlyph.advance` を受け取るだけで source を知らず、`attributed` は layout を知らない。

規則: confirmed alignment が `left`（既定）なら現行どおり隣接 origin 差（カーニングと `TJ` を保持）、`justify` / `center` / `right` なら `nominal + confirmed tracking`。末尾補正は `style.tracking` ではなく `unit.event.state.tc` から取る（併合後の style は単一の `Tc` を保証しないため）。

## 6. persistence

1 回目で成功し 2 回目で分裂・拒否される構造を避けるため、次の順で witness を揃える。

- 現状: writer は `q 0 Tc 0 Tw 0 Ts` を出し tracking と rise を座標へ焼き込む。再観測すると style の `tracking` / `baseline_shift` が 0 になり、`editable._bind_paragraph` の export 比較で拒否される（`destination_style` も nonzero を拒否）。
- 鍵変更だけでは、この拒否は変わらない（`_bind_paragraph` は 'tracking' を比較し続ける）。regressions は起きないが、tracking 付き段落の persistence は依然不可。
- 解消は writer が確定 tracking を `Tc`、rise を `Ts` として glyph の `Tj` の前に出力すること（glyph 単位 `Tj` では位置に影響しないので witness 専用）。再観測では全 glyph が同じ `Tc` を持つ 1 style となり、`inferred_consistent_tracking` の候補値が sidecar の確定値と一致することを検証して再編集する。生成 layout の spacing は `uniform_reposition` として現れるだけで候補を生まない。
- 確定 alignment は PDF から再導出しない。sidecar の `explicitly_confirmed` が権威で、再観測は矛盾検査（確定 tracking と候補の不一致）だけに使う。

## 7. 変更してはいけない source witness

- `paragraph.events` と `patch_streams` / `rewritten_event` による replay・removal の bytes。
- `SourceStyle.event.state` の `Tf` size、`Tz`、fill、font xref / name と `matrix`（writer prefix と `source_ink`）。
- `SourceUnit.char.code`、`observation['glyph_id']`、`observation['font']`、`code_witness`（`_bind_paragraph` と transaction の retained font witness）。
- `story_styles` の `paint_context_sha256`（event の report。`Tc` を含むが witness としては現状維持）。
- `spacing.observe_spacing` の分解式と分類。
- Phase 1 の identity map と transaction。

## 8. 変更するモジュール

| モジュール | 変更 |
|---|---|
| `attributed.py` | 鍵から `tracking` / `word_spacing` を除去、行内 `Tc` 変化による分割、`SourceUnit.event`、`SourceStyle.word_spacing` 廃止、`tracking_provenance` 追加、`snapshot()` に `spacing` |
| `paragraph.py` | 新 glyph advance から `word_spacing` を除去、末尾補正を `unit.event.state.tc` に、`tracking` は確定値のみ（未確認の nonzero 候補で新 glyph を要求する編集は拒否） |
| `logical_element.py` | recipe の検証項目から `word_spacing` を除去 |
| `destination_style.py` | `PROPERTIES` から `word_spacing` を除去（nonzero tracking / rise の拒否は次 PR まで維持） |
| `editable.py`, `story_flow.py`, `story_styles.py` | export 比較と `STYLE_KEYS` から `word_spacing` を除去 |
| `rich_layout.py` | 変更なし |

snapshot と sidecar の digest は変わるため、既存 sidecar は `needs_confirmation` へ戻る。migration layer は作らない。

## 9. migration の順序

1. **PR A: style identity から行 spacing を外す**（本書の対象。writer の出力は変えない）。
2. PR B: writer が確定 tracking / rise を `Tc` / `Ts` で witness し、`paragraph_style` の確認入力と sidecar 保存、`_bind_paragraph` / `destination_style` の nonzero 受け入れ。
3. PR C: alignment の確認と `rich_layout` の揃え・両端配分、`ParagraphShaper` の保持 glyph 切替、flow の left 固定解除。

## 10. PR A の完了条件

- 行ごとに `Tw` または `TJ` で両端揃えされた合成段落（`tests/test_spacing.py` の fixture 相当）が `inspect_paragraph` で 1 style になり、snapshot に `spacing` の候補が載る。
- 同じ行の途中で `Tc` が変わる段落は 2 style のままである。
- `Tc` が全行で一定の段落は 1 style で、`spacing.tracking` が `inferred_consistent_tracking`、`requires_confirmation=True` になる。
- 未確認の nonzero tracking 候補を持つ style へ新 glyph を挿入する編集は拒否し、削除のみ・保持 glyph のみの編集は通る。
- `tests/test_attributed.py`、`test_editable.py`、`test_destination_style.py`、`test_story_styles.py`、`test_shared_flow.py`、`test_document_flow.py` が通る（既存 fixture は `Tc` 0 または一定で、分裂しない）。
- replay / removal / no-op 検証、transaction の witness、identity map に変更がない。
