# Confirmed tracking / rise（PR B）

2026-09-20。[review3](fable5.1-review3.md) の PR B を、PR A `b60c324` から実装した。alignment、行揃え、continuation slot、identity / transaction の再設計は含まない。

## 入力契約

`plan_paragraph` / `edit_paragraph` / `write_editable` は、style ID ごとの `paragraph_style` を受け取る。`edit_document` でも override として渡せるが、保存後の確認値は自動で検証・復元するので再入力は不要。

```python
write_editable(source, output, sidecar, snapshot, edits,
    fonts={"s1": {"path": "C:/Windows/Fonts/msgothic.ttc", "font_index": 0}},
    paragraph_style={
        "s1": {"tracking": -0.12, "baseline_shift": 0.0}
    },
    width=488, max_bottom=300)
```

値は有限な page point。`tracking` は横方向、`baseline_shift` は正が下方向。片方だけの確認も可能で、未指定の属性は確認済みに昇格しない。確認された値と各属性の provenance を `explicitly_confirmed` で保存する。style ID ごとに指定するため、複数 style の段落へ一つの値を無条件に適用しない。

source style への入力は**観測値の確認**であり、任意の書式変更ではない。選択されたその style の全 painted glyph の `SourceUnit.event` と照合し、値が0.001 ptを超えて違う場合や style の tracking が `unknown` の場合は拒否する。一定 nonzero の `candidate` は caller が値を渡して初めて確認済みになる。候補の数値が一致するだけでは自動確認しない。

`render_styles` の destination recipe でも、nonzero tracking / rise は同じ ID の明示確認を要求する。確認値と recipe の値が一致し、font provider・size・scale・fill が検証可能であることが条件。source 側の event、char、resource、code は書き換えない。

## PDF witness

writer は glyph ごとに独立した `Tm` と一文字の `Tj` を出す既存構造を維持する。

- 確認した tracking を、その glyph の matrix と `Tz` で逆変換して `Tc` に出力する。
- 確認した baseline shift を matrix の縦倍率と符号で逆変換して `Ts` に出力する。
- `Tc` は文字を描いた後の advance に作用する。次の glyph は独立した `Tm` で配置するため、layout で計算した tracking と二重に加算されない。
- `Ts` は現在の glyph 自体を移動するので、既に座標へ含まれている確認済み rise を `Tm` の位置から引く。描画後の origin は元の計画のままである。
- 未確認の属性は従来どおり `0 Tc` / `0 Ts`。`Tw` は0のまま。保持 glyph の advance と末尾補正は元の `SourceUnit.event.state` を使う。

## 保存と再open

`editable._bind_paragraph` は mutation identity が返す glyph と元の計画を対応させ、resource alias / encoded code と、PDFから読んだ有効 `Tc` / `Ts` を照合する。size / scale / fill / color の比較と、transaction の GID / origin / font / security / 対象外paint・画素検査も維持する。視覚的には同じでも `Tc` / `Ts` の witness が違えば、PDFとsidecarを公開しない。

確認情報は `paragraph.logical.style_confirmations` に保存する。新 font と元 font の併存で物理 style ID が分かれた場合も、実際に出力した glyph の identity を使って対応させる。異なる確認 provenance が一つの物理 style に潰れる場合は拒否する。

再openでは、現在のPDFの各 glyph の `Tc` / `Ts` を再観測して全て照合した後にだけ論理値を復元する。PDF hash と snapshot の全体比較も従来どおり行う。sidecar のchecksumを再計算して値を改ざんしても、PDF witness と一致しなければ拒否する。PDFだけを `inspect_paragraph` した結果は nonzero tracking の `candidate` のままであり、sidecarがなければ確認し直す。

## 制限

- 未確認 candidate と unknown style の新 glyph 入力は引き続き拒否する。段落全体の spacing が unknown でも、個別 style の全 glyph が同じ値を観測する場合は、その style だけを確認できる。
- 全削除による空段落には描画 glyph の style witness がないため、確認済み属性を持つ空段落の永続保存は拒否する。sidecarだけに確認済み rise を書いても復元しない。従来の未確認 typing recipe は既存契約のまま。
- 元の no-op が安全検査を通らない場合は確認を与えても許可しない。合成した異方性 transform の例は確認前後とも領域外画素差分で拒否される。この既存境界は今回拡張していない。
- 新しい provenance を含む snapshot と旧 sidecar は一致しないため、旧モデルは再確認が必要。migration や alignment 推定は追加していない。

実PDFの選択範囲、往復編集、拒否例、テスト結果は [評価記録](../evaluations/confirmed_style/README.md) に記載する。
