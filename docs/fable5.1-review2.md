まず main の状態を確認し、判断根拠となる箇所をコードで確認します。

main は PR #1 のマージ後（782a703）で、Phase 1 の基盤が入っていることを確認しました。以下はコードを根拠にした判断です。

## 候補の依存関係

**alignment と tracking は同じ問題です。** 現状のコードでは両者が結合しています。

- `attributed.SourceParagraph` は style を `(font, size, scale, tracking, word_spacing, rise, fill, color)` で同定します。Word 由来の両端揃え本文は行ごとに `Tw` や `TJ` 調整で字間を変えるため、この鍵では1段落が行数分の style に分裂し、story / shared flow の `_style` 同質性検査（`STYLE_KEYS` に tracking と word_spacing を含む）で拒否されます。行揃え由来の spacing が inline style に混入しているのが根本です。
- `ParagraphShaper.shape` は保持 glyph の advance を隣接 glyph の origin 差から取るため、両端揃え段落を再組版すると旧行の調整字間が新しい行に持ち越されます。左揃えではカーニング保持として正しい挙動ですが、両端揃えでは崩れます。
- 書き手は `q 0 Tc 0 Tw 0 Ts` で glyph ごとに `Tm/Tj` を出力し、tracking と rise を座標へ焼き込みます。そのため保存後の PDF は style を witness せず、`editable._bind_paragraph` と `destination_style` が nonzero の tracking / word spacing / rise を persistence 拒否します。
- alignment は `logical_element.alignment=dict(value='left', provenance='generated_layout_policy')` の placeholder があり、`open_editable` と `document_flow._validate` が left 以外を拒否、`rich_layout.layout_attributed` に揃えの概念はありません。

**run 単位出力は基盤問題ではなく最適化です。** glyph ごとの `Tm` は任意の揃え・字間を表現でき、Transaction の検証も origin 単位です。style の witness は glyph ごとの `Tj` の前に `Tc`/`Ts` の状態値を出すだけで回復でき（単一 glyph の `Tj` では位置に影響しない）、run 化は不要です。先送りします。

**非 Identity-H CMap は encoding / writer の独立問題です。** `FontCodec` の `unit=2` 固定と `encode_available` の逆引き、`Tw` の 1 byte 0x20 適用が変更点で、layout と identity には触れません。利用頻度は高いが独立なので後回しにできます。

**Form XObject は transaction の小さな拡張です。** `text_ref` と `map_path` が `invocation` を拒否し、`PageTransaction` は page program 1本だけを持ちます。stream ごとの `MutationProgram` を持たせ、identity に stream 要素を加える追加であり、段落モデルには依存しません。Phase 2 の先頭には置きません。

## 次に実装する作業（1つ）

**段落の inline style と行揃え由来 spacing の分離（alignment の観測・確認・再現、tracking の style としての保持）**

1. **なぜ最初か**: 上記のとおり両端揃え段落は現在の logical model で誤って多 style に分類され、再組版で旧字間を持ち越します。この分離を後回しにすると、CMap 対応や Form XObject 対応で作った style 登録・shaper・sidecar をもう一度直すことになります。自治体の Word 由来 PDF は本文が両端揃えのため、最終目的への影響も最大です。

2. **変更対象**: `attributed.py`（style 鍵から行揃え由来 spacing を除外し、行ごとの Tc/Tw/TJ 差と左右端を `layout_suggestion` の alignment 候補として観測）、`rich_layout.py`（confirmed alignment による行内 x 補正と、両端揃え時の余白配分。最終行とハード改行前の行は配分しない）、`paragraph.py`（両端揃え確認時は保持 glyph の advance を隣接 origin 差ではなく名目 advance にする。生成 glyph の前に `Tc`/`Ts` を出力し PDF が style を witness する）、`editable.py` / `destination_style.py` / `story_flow.py` / `shared_flow.py` / `document_flow.py`（left 固定と nonzero spacing 拒否の解除、confirmed alignment の保存と検証）。Transaction と identity map は変更しません。

3. **データモデル変更**: `SourceStyle` から行揃え由来の spacing を分離し、段落全体で一定の tracking だけを style に残す。snapshot に `alignment_candidates`（値・根拠・要確認）を追加。`logical_element.alignment` を `{left, center, right, justify}` と `provenance='explicitly_confirmed'` で保持。sidecar schema はフィールド追加のみ。

4. **完了条件**: 行ごとに `Tw` または `TJ` 調整で両端揃えされた2行の合成 PDF と、実 PDF 評価の1件について、(a) 1つの logical style と `justify` 候補として観測される、(b) 確認後の長文化で全行の右端が元の右端に一致し最終行だけ不揃いになる、(c) 同幅の no-op 再組版で対象領域の画素が一致する（一致しない場合は拒否として報告）、(d) 一定 tracking を持つ段落を2回続けて編集でき persistence が拒否されない、(e) 既存の左揃えテストが全て通る。

5. **次の作業へ残すもの**: 非 Identity-H CMap（`FontCodec` の可変長 code と predefined CMap）、Form XObject 内テキスト（stream ごとの `MutationProgram`）、run 単位 `TJ` 出力、縦書き。Phase 2 は「2a 揃えと spacing の分離（本作業）→ 2b 符号化境界（CMap と Form XObject）→ 2c 出力正規化（任意）」の順で分解できます。
