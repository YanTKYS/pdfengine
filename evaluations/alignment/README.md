# PR C: 明示確認したjustifyの実PDF再編集

大阪府公開のWord由来PDF `word_osaka_fire_notice` p1、glyph 143–217の本文2行を手動指定した。元資料・PDF・PNG・生のUnicode/glyphログは公開せず、コードと[集計](summary.json)を保存する。

## 確認と結果

明示領域はx=53.88 pt、width=493.2 pt、右端547.08 pt、first-line indent=10.584 pt、bottom=314 pt。`justify / character` を確認し、元の2文字だけにあるtracking=-0.12 ptもPR BのAPIで確認した。その他のtrackingは観測済み0。riseは0。

| 段階 | 行数 | 非最終行の右端 | Poppler全変更画素 | 対象外変更画素 |
|---|---:|---:|---:|---:|
| 元operatorのsource replay | 2 | 元のまま | 0 | 0 |
| v0 同文再組版・再open | 2 | 547.0800195 pt | 18,996 | 0 |
| v1 短文化・再open | 1 | 最終行なので配分なし | 15,965 | 0 |
| v2 長文化・再編集・再open | 2 | 547.0800195 pt | 15,959 | 0 |
| v3 生成結果の同文保存 | 2 | 547.0800195 pt | 0 | 0 |

右端誤差は約0.0000196 pt。最終行はraggedのまま。元paragraphの改行位置は47文字目だったが、名目幅による同文再組版では45文字目になった。元exporterのspacingへ無理に戻さず、確認済み領域とjustify契約を満たすことを検証した。v0は全画素一致ではない。生成結果v3のno-opはMuPDF / Poppler 144dpiで全画素一致した。

各保存で既存の独立pypdf抽出、一か所の置換、対象外ページ、drawing / image / annotation / font fingerprint、CID / GID / `/W`、保持元code、mutation identity、tracking / rise witnessを確認する。対象外画素は許可領域+1 ptの外で比較する。元ページ、v0、v2のPoppler画像を目視し、周辺本文・宛先括弧・連絡先罫線が保持され、文字の重なりがないことも確認した。

新glyphにはMS Gothic TTC face 0を明示供給し、保持glyphは元resource / codeを使う。font名の一致だけでcoverageを認める処理はない。

## 観測上の限界

**この原本のalignment候補はunknownであり、明確なjustify候補を検出した例ではない。** 初行indentとexporter由来の不均一spacingがある。既存corpusのWord / LibreOffice計9ページで、左端が揃う隣接本文行を限定調査したが、現classifierが明確なjustifyとする範囲は得られなかった。今回、推定器や閾値は変更しない。

目視した本文範囲と右余白を評価者が確認し、paragraph_layoutを明示した上でbackendを評価した。したがって確認したのは「人間が行揃え・幅を指定したときの再編集」であり、「PDFだけから元のjustify意図を復元できる」ことではない。誤った範囲や幅を自動修復する機能でもない。

実PDFではcharacter justifyのみ、rise=0を検証した。word justify、非ゼロrise、明示改行、複数領域・ページを跨ぐflow、geometry不一致拒否は合成fixtureで検証する。一定しないinline tracking、明確な候補との矛盾、配分可能gapなし、glyphのcontextual offset、領域超過等はsafe refusalとして残る。

## 再現

```powershell
.venv\Scripts\python.exe -m pytest tests/test_alignment.py tests/test_alignment_flow.py tests/test_rich_layout.py tests/test_style_confirmation.py tests/test_spacing.py tests/test_destination_style.py -q
.venv\Scripts\python.exe -u -m evaluations.alignment.evaluate --run-name <新しい名前>
```

原本は `evaluations/sources.json` のSHA-256と一致させる。既存[評価環境](../../docs/realpdf-evaluation.md)と `C:/Windows/Fonts/msgothic.ttc` を用いる。新しいrun directoryだけを作成し、既存結果を上書きしない。

入力・永続化・flowの契約は [confirmed-alignment.md](../../docs/confirmed-alignment.md) に記載する。テスト集計とengine / runner / font / JUnitのhashは集計に保存する。

## 検証結果

- targeted: **110 passed**。flowのsemantic整合性を追加で強化した後の5ケースもすべて成功。
- repository suite: **614 passed / 2 skipped / 0 failures / 0 errors**。41 moduleを重複のない4群としてrootで直列実行した。
- skipはPR Bと同じAES-128 / AES-256 provider未導入の2件。
- full suiteと最終実PDF評価のengine hashを照合し、既存586 test ID（584成功・2skip）の保持と30ケース追加を確認する。最終結果のPNGも目視済みrunと比較する。

全体の再実行は `.venv\Scripts\python.exe -m pytest -q`。集計のbatch別module一覧を使って直列分割してもよい。
