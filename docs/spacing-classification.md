# spacing の分類モデル（Phase 2a 基盤）

2026-09-14に [fable5.1-review2.md](fable5.1-review2.md) の分類モデルを定義し、2026-09-20に末尾の PR A 実装結果を追記した。対象は「段落の inline style と行揃え由来 spacing の分離」。分類は `pdfeditor/spacing.py`、検証は `tests/test_spacing.py` にある。alignment 再現と非ゼロ tracking の確認入力・writer witness は後続の段階とする。

## 分離前のコードが混同していた点

- `attributed.SourceStyle` は `(font, size, scale, tracking, word_spacing, rise, fill, color)` で style を同定する。`tracking` は `Tc`、`word_spacing` は `Tw` の状態値から来るため、行ごとに `Tw` を変える両端揃え段落は行数分の style に分裂する。
- `paragraph.ParagraphShaper.shape` は保持 glyph の advance を隣接 glyph の origin 差から取る。これは `Tc`・`Tw`・`TJ` 調整・operator 間の再配置をすべて含むので、両端揃え行の調整字間が再組版後の行へ持ち越される。
- `content_stream.PaintChar.advance` は `(w/1000·size + Tc + Tw)·Tz/100` で、`TJ` の数値 atom は `event.atoms` に残る。つまり分解に必要な証拠は既に観測されている。

## 証拠の分解

同一物理行の隣接 glyph 間の距離は page 単位で厳密に分解できる。

```
measured = nominal + tc + tw + tj + reposition
```

| 項 | 出所 | provenance |
|---|---|---|
| `nominal` | font の `/Widths` または `/W` × size × 横スケール | `glyph_metrics`（`observed_source` の一種） |
| `tc`, `tw` | その glyph に有効な `Tc` / `Tw` 状態（`Tw` は 1 byte の 0x20 のみ） | `observed_source` |
| `tj` | 次の glyph の直前に書かれた `TJ` 数値 | `observed_source` |
| `reposition` | 残差。operator 間の `Td` / `Tm` による再配置 | `observed_source` |

PoC の7ケース（左揃え+`Tc`、両端揃え+`Tw`、両端揃え+`TJ`、`Tc`+`Tw` 混在、偶然の等幅行、`Td` で単語配置、`Tm` で glyph 個別配置）で、全 gap がこの等式を満たすことを確認した。

## 分類

**Glyph metrics** は `nominal` である。保持 glyph の再組版はこれを基準にし、再組版時に元の gap を持ち越さない。ただしカーニングとしての `TJ` 調整（glyph 対に固有で行に依存しない負または正の小さな値）は現状では両端揃えの `TJ` 配分と区別できない。confirmed alignment が左揃えの場合のみ、従来どおり origin 差を保持する（後述）。

**Inline style** は font、size、横スケール、rise、color と、`Tc` が全行（最終行を含む）で同一である場合のその値だけとする。両端揃えは最終行を伸ばさないため、最終行にも同じ `Tc` があれば行調整ではない。これを `inferred_consistent_tracking` とし、`requires_confirmation=True` の候補として扱う。`0 Tc` は `observed_source` で確認不要。`Tw` は inline style に含めない。行ごとに異なる `Tc`、行内で変わる `Tc` は `unknown`（`requires_confirmation=True`、値なし）とし、tracking を推測しない。再配置で実現された均等字間も tracking とは見なさない。

**Line layout adjustment** は各 gap の `tc − tracking + tw + tj + reposition` である。行ごとに次のとおり分類する。

| 分類 | 条件 |
|---|---|
| `none` | 全 gap の超過が 0 |
| `uniform_char` | 再配置 gap が無く、全 gap で等しく正（`Tc` または `TJ` が operator として書かれている） |
| `uniform_word` | 再配置 gap が無く、**塗られた空白 glyph** の gap だけで等しく正、他は 0 |
| `uniform_reposition` | 再配置 gap があり、その超過が等しく正で、他の gap は 0 |
| `irregular` | それ以外 |

`reposition` は独立した観測事実であり、空白 glyph・`Tc`・`Tw`・`TJ` と意味上同一視しない。単語 gap の根拠は Unicode 空白 glyph だけである。`Td` / `Tm` で単語や glyph を個別配置する exporter の出力は、gap が均等で左右端が揃っていても `uniform_reposition` であり、tracking とも単語間隔とも証明できない。これらは行に属し、再組版で捨てて再計算する。

## alignment candidate

候補であり確定ではない。`requires_confirmation=True` を付け、上位層またはユーザーの `explicitly_confirmed` と分離する。

- `justify`: 最終行を除く行のうち operator で証明された配分（`uniform_char` / `uniform_word` かつ正）を持つ行が 1 行以上あり、それらの右端が一致し、全行の左端が一致し、どの行もその右端を超えない。右端に届かない行は hard break 直前または段落末の可能性があるとして `ragged_lines` に、`uniform_reposition` / `irregular` の行は `unproven_lines` に列挙する。
- `unknown`（再配置）: 左右端が揃っていても、届いた行の配分が再配置だけで実現されている場合。glyph 単位・単語単位で座標を指定する exporter がここに入る。
- `unknown`（偶然の等幅）: 最終行を除く 2 行以上が同じ右端に届くが配分が無い場合。等幅 font の等文字数行はこれに当たる。
- `left` / `right` / `center`: 上記に該当せず、左端のみ一致・右端のみ一致・中央のみ一致の場合。
- 1 行の段落は `unknown`。

## 自動判定できないもの

- alignment そのもの。候補は出せるが、hard break の位置は PDF から分からないため、右端に届かない行が段落末か強制改行かは確認が要る。
- カーニング由来の `TJ` と両端揃え由来の `TJ` の区別。前者は行揃え確認後に「左揃えなら保持、両端揃えなら捨てる」という方針でしか扱えない。
- 単語間を `Td` で配置する exporter の広い gap。現在の行推定（`inference._horizontal_lines`）は 2.2 em を超える gap を段組の区切りとして別行に分割するため、その場合は selection の行確認が先に必要になる。
- 空白 glyph を塗らず `Td` で単語を配置する exporter の gap は再配置として観測され、単語 gap とは判定しない。単語境界が必要なら Unicode 空白の存在か明示確認を要する。

## `SourceStyle` と snapshot の最小変更（Phase 2a 本実装）

1. `SourceStyle` の同一性鍵から `tracking` と `word_spacing` を外す。値は観測として保持し、replay と no-op 検証には従来どおり使う。
2. snapshot に `spacing` を追加する。内容は `observe_spacing` の出力（行ごとの証拠、`tracking` 候補、`alignment_candidates`）。
3. 確認入力として `paragraph_style={'alignment': ..., 'tracking': ...}` を受け、`logical_element.alignment` と `tracking` を `explicitly_confirmed` で保存する。`generated_layout_policy` の left 固定は確認値に置き換える。
4. `ParagraphShaper.shape` の保持 glyph advance を、confirmed alignment が `justify` / `center` / `right` なら `nominal + tracking`、`left` なら従来の origin 差にする。

上記は当初の段階案である。責務と互換性を精査した [review3](fable5.1-review3.md) により、以下の PR A と、後続の確認入力・writer witness・alignment に分割した。

## PR A 実装（2026-09-20）

`SourceStyle.word_spacing` を廃止し、行ごとの `Tw` と `TJ` を inline style の同一性から分離した。同じ font / size / scale / rise / fill の glyph は、行間だけで `Tc` が変わっても一つの style にする。行内で `Tc` が変わる場合は引き続き style を分割する。各 `SourceUnit.event` に元の描画命令を保持し、spacing の分解と保持 glyph の advance・末尾補正は、その glyph 自身の状態と matrix を読む。replay / removal、mutation map、single transaction は変更していない。

論理 tracking は次の契約にした。snapshot の `spacing` は観測証拠であり、候補を自動で組版へ渡さない。

| 観測 | `SourceStyle.tracking` | provenance | 新 glyph |
|---|---|---|---|
| 全 glyph が `0 Tc` | `0.0` | `observed_source` | 既存の安全条件を満たせば許可 |
| 一定の非ゼロ `Tc` | `None` | `candidate` | 明示確認が未実装のため拒否 |
| 行間で異なる `Tc` | `None` | `unknown` | 値を確定できず拒否 |

保持・削除だけの非永続編集は元 advance を使える。現在の writer は字間を位置へ焼き込み、PDF には `0 Tc` を出すため、非ゼロ／不明 tracking の非空段落を sidecar 付きで保存する操作は引き続き拒否する。全削除では空の typing recipe に `None` と provenance を保存できるが、再入力は拒否する。destination style は `0.0` tracking のみ許可し、nonzero rise も未対応のままである。

snapshot の digest が変わるので旧 sidecar は再確認が必要。migration は行わない。生成 layout は従来の左揃えであり、両端揃えの再現を実装したという意味ではない。次の障壁は確定 tracking / rise を PDF に witness して再編集できること、その後に alignment の確認と再配置である。

検証の再現方法と実 PDF 結果は [spacing 評価](../evaluations/spacing/README.md) を参照。
