# Confirmed paragraph alignment（PR C）

2026-09-21。PR B `839a669` の続きとして、明示確認した行揃えをlayoutと永続再編集へ接続する。source observation、Stable Identity、Single Transactionは変更しない。

## 入力と意味の保存

```python
write_editable(source, output, sidecar, snapshot, edits,
    fonts=reviewed_font_recipes,
    paragraph_layout={"alignment": "justify", "justify_policy": "character"},
    width=493.2, x=53.88, first_line_indent=10.584, max_bottom=314)
```

`plan_paragraph` / `edit_paragraph` / `write_editable` は `paragraph_layout` を受け取る。`alignment` は `left` / `right` / `center` / `justify`。justifyだけは `justify_policy="word"` または `"character"` も必須。他の属性や `unknown` は拒否する。tracking/rise用の `paragraph_style` とは独立している。

確認値は `logical_element.alignment = {value, provenance: "explicitly_confirmed", ...}` に保存する。無指定では従来の `left / generated_layout_policy`。再編集では保存済み値を自動で渡す。PDFだけを観測した結果は候補であり、確定値に昇格させない。

確認時はsource snapshotの候補を調べる。正の根拠を持つright / center / justify候補と異なる確認は拒否する。unknownは根拠不足として受け入れ得る。classifierのleft候補は明示改行でも生じるfallbackなので、他のalignmentとの明確な矛盾とは扱わない。幅は人間が明示し、観測文字幅で代用しない。

## 配置方式

- left: 保持glyphの隣接origin差と元の末尾補正を維持する。
- right / center: 名目幅で折り返し、空き幅をそれぞれ左側へ全量／半量加える。
- justify: soft wrapした非最終行に空き幅を均等配分する。段落末、authored hard break直前、空行には配分しない。

word policyは論理Unicode中のASCII spaceの連続列を一つの単語間隔として扱い、その最後のspaceの後へ配分する。文字間を勝手に単語間と解釈しない。character policyは非空白glyph間のgrapheme境界へ配分し、cluster内部は分割しない。日本語／英語を推測してpolicyを切り替えない。配分可能なgapがないsoft lineは、空き幅を埋めずに成功扱いせず拒否する。

line widthは既存layoutと同じ、advanceとinkのoverhangを含む占有幅。負の左bearingはinsetする。内部glyphのoverhangにより均等配分で右端を合わせられない場合も拒否する。

非leftの保持glyphは元fontの `/W` / `/Widths` による名目advanceと論理trackingを使う。観測済み0または明示確認した値だけが利用でき、candidate / unknownは0にしない。元のTw、TJ、per-line Tc、repositionはsource witnessとして残すが、新しいadvanceへ加えない。

新glyphも非leftでは同じ名目幅の契約とし、HarfBuzzのkerningを無効にする。その他のcontextual placement offsetが残る場合は拒否する。これにより、新glyphが保存後に保持glyphへ変わっただけで字間が変わることを防ぐ。leftのshaping契約は維持する。

## 保存後PDFの検証

保存時は既存transactionによるglyph / font / paint / 対象外画素検証を実施する。さらに保存したPDFを読み直し、identityで結びつけた論理Unicodeの各位置、現在のfont幅、確認済みtracking/rise、明示幅、hard breakからレイアウトを再計算する。

再計算した全glyphのoriginをPDF実測値と0.002 pt以内で照合し、描画glyph集合・順序・line allocation、line x / width / baselineを確認する。line reportだけを証拠にしない。soft wrapで描画されなかった空白の計測に必要な場合は、保存済みfont recipeもhash確認して使う。

`physical_layout.alignment_contract` は生成時の契約との食い違いを検出する補助記録。実際の幾何検証が必要であり、checksumを付け直すだけでは矛盾したgeometryを通せない。ただし、同じ画素配置に複数の意味が成立する場合（幅ぴったりの一行など）、PDFから著者の意図を認証することはできない。sidecarはcallerが管理するsemantic authorityであり、署名付きの改ざん証明ではない。

空のlogical paragraphは検証済み非描画slotを維持する。alignmentを保持して再入力に使うが、存在しない行のedgeを検証済みとは扱わない。PR Bの非ゼロtracking/riseを含む空段落の拒否は残る。

## flowとの接続

`confirm_story(..., paragraph_layout=...)` はstoryのlogical paragraphへalignmentを保存する。source import時は各fragmentの候補との矛盾を検査し、生成後は各fragmentのPDF geometryを検証する。shared flowはimportしたstoryのalignmentを引き継ぐ。document flowでは各element specの `paragraph_layout` を利用でき、計測と書込の両方へ伝える。

既存のrange / render_endからsoft continuationを判定する。領域末でも次のfragmentへ続くsoft lineはjustifyし、消費したUnicodeにCR/LFがあれば配分しない。内部引数 `_paragraph_continues` と保存値はflowの論理範囲から再検証する。新しいslot・ページ・identity規則は追加しない。

## 評価範囲

合成PDFでは4 alignment、word/character配分、明示改行、確認済みtracking/rise、元Tw/TJの除外、保存・再open・再編集、no-op、geometry不一致とsidecar変更の拒否を検証する。word justifyを同じ配置で表現できるsource fixtureでは同文保存の全画素一致も確認する。

実PDFは [評価記録](../evaluations/alignment/README.md) を参照。候補を明確に分類できなかった外部文書を、classifier成功とは数えない。
