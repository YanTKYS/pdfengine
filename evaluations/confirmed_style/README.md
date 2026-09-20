# PR B: confirmed tracking の実PDF往復編集

既存 `evaluations/spacing` の大阪府公開 Word PDFを再利用した。[集計](summary.json)に source / engine / runner / font の SHA-256を保存する。原本、保存PDF、画像、生の文字列・glyphログは公開しない。

## 対象と結果

`word_osaka_fire_notice` p1、glyph 143–189（本文の最初の1行）を手動指定。幅488 pt、下端300 pt。2 style があり、論理 offset 39–41 の2文字だけが `Tc=-0.12`。その style に tracking=-0.12 pt、baseline shift=0 pt を明示確認した。他の style は `0 Tc / observed_source` のままである。

段落全体の spacing は unknown のまま。確認したのは全 glyph の `Tc` が一致する一つの inline style だけであり、unknown を0に変換していない。

| 段階 | 操作 | 結果 |
|---|---|---|
| source replay | 元operatorで同文再描画 | MuPDF / Poppler の対象ページ全画素一致 |
| v0 | 確認値を `Tc` / `Ts` に持つ同文保存＋sidecar | 全画素一致、再open成功 |
| v1 | 対象2文字を「資料」に置換 | 保存・再open・logical Unicode復元成功 |
| v2 | 「資料」を「文書」に再編集 | 保存・再open・確認済み style 復元成功 |
| v3 | 再編集結果の同文保存 | MuPDF / Poppler の対象ページ全画素一致 |

各保存で、独立 pypdf 抽出による一か所の置換、別ページの不変性、対象外paint / image / annotation / 既存font、CID / GID / `/W`、保持元code、mutation identityを既存の評価方法で検証する。編集後の対象外画素差分は許可領域+1 ptの外で0。元ページとv2のPoppler画像も目視し、文字の重なりや周辺の変化がないことを確認した。

新glyphには MS Gothic TTC face 0 を明示供給する。元subsetへのglyph追加やfont名だけによる再利用とは扱わない。実PDFのriseは0であり、nonzero rise（上・下方向）とscale変換は合成fixtureの保存・再open・再編集・no-opで検証する。

## 残した拒否

- 明示確認なしの候補への新文字入力は、1行選択でも元の2行選択でも拒否する。
- 最初はglyph 182–183の2文字だけを指定したが、同文保存時点で隣接する未選択glyphとの衝突検査に拒否された。2文字を含む元の1行全体を正しい編集範囲として指定し直した。衝突guardは変更していない。確認した字間だけで、任意の極小範囲が安全に編集可能になるわけではない。
- 未確認属性、値の不一致、unknown style、確認済み属性の全削除による空段落化は拒否する。詳細は [入力・witness契約](../../docs/confirmed-inline-style.md)。

## 再現

[既存評価環境](../../docs/realpdf-evaluation.md)、`evaluations/sources.json` の原本、`C:/Windows/Fonts/msgothic.ttc` を使用する。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_style_confirmation.py tests/test_empty_element.py tests/test_destination_style.py -q
.venv\Scripts\python.exe -u -m evaluations.confirmed_style.evaluate --run-name <新しい名前>
```

`runs/<名前>/summary.json` に結果を保存し、既存runは上書きしない。engine hashは実行前後で一致を要求する。

## テスト

- targeted: **47 passed**（確認処理23、空段落18、destination style 6）。
- repository suite: **584 passed / 2 skipped / 0 failures / 0 errors**。39 moduleを重複のない4群に分け、rootのpytestを直列実行した。PR Aの563 test IDを全て維持し、23ケースを追加。
- skipは基準実行と同じ AES-128 / AES-256 provider未導入の2件。
- tracking、上下方向のrise、両者併用、CTM/Tz変換、反復編集、no-op、未確認candidate、unknown、片方だけの確認、改ざんsidecar、意図的なwriter witness欠落、空段落の偽確認を検証した。

全suite中はengineを固定し、実行前後と実PDF評価のhash一致を確認する。JUnit SHA-256と全module一覧を集計に保存した。非一様な縦横倍率を与えた合成fixtureは、確認前の経路でも領域外画素差分に拒否されたため、その既存拒否を回帰テストとして残した。
