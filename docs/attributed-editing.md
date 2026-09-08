# 元の書式と新しい文字を混在させる本文編集

評価日: 2026-09-09。公開集計は [summary.json](../evaluations/attributed/summary.json)、固定した実PDF入力は [evaluate.py](../evaluations/attributed/evaluate.py)。結果は利用可能幅と編集範囲を人が確認した局所編集の評価である。

## 優先課題を選んだ根拠

今回の最大のギャップは、**編集前の文章のどの区間をどの書式で残すかを表現できず、数文字の変更でも選択文章全体を指定fontへ置き換えていたこと**と判断した。元subsetへの追加文字不足は前段のHarfBuzz/CID writerで解消できたが、全文代替では元の強調・斜体・サイズ差を捨てるか、共通style guardで止まる。

これは失敗ケースだけの問題ではない。前段で成功した長文化も、元fontを全文で代替する成功だった。自然な本文編集へ近づくには、元PDFのUnicode・glyph・書式と、利用者の挿入・削除を結び付ける編集モデルが必要になる。

並行してpaint拒否も再調査した。MuPDF device callbackで15原本・1,124 paintの種類、順序、bounds、clip stackを観測できた。一方、背景と装飾の区別や、下線がどの文章に属するかはrendererだけでは決まらない。bbox guardを緩める作業を先に進めても、成功時の書式喪失を解決しないため、今回は区間編集を実装の中心にした。[paint比較と実測](paint-observation-options.md)に採否理由を記録した。

## 採用したモデルと配置

```text
source PDF + human selection
  → source-bound Unicode snapshot + style intervals
  → edits expressed in original Unicode offsets
  → retained source codes / explicitly supplied shaped runs
  → line layout using each glyph's actual metrics
  → original text operator removal + positioned text operators
  → saved glyph, font, extraction and renderer verification
```

`attributed.py` は元Unicode位置、glyph由来、style区間を持つ。`paragraph.py` は元コードを使うproviderと完全fontをshapeするproviderを接続する。`rich_layout.py` はPDFに依存せず、候補文字列の**位置区間**をshape callbackへ渡す。同じ文章が別書式で繰り返されても、文字列検索で区間を取り違えない。

| 情報 | 契約 |
|---|---|
| 編集位置 | 変更前のUnicode code pointによる `[start,end)`。grapheme途中の編集は拒否。複数編集は重複しない元位置で指定 |
| 書式 | 元font resource、実効縦サイズ、横倍率、Tc/Tw由来の字間、baseline shift、device fill色 |
| 未変更区間 | 元のfont resource・encoded bytes・GIDを使用。選択範囲内の位置はreflowに応じて変わる |
| 変更区間 | 指定fontをHarfBuzzでshapeし、fontToolsでsubset。変更区間全体を指定fontで描き、coverageによる文字単位の暗黙fallbackはしない |
| 境界の送り | 元から隣接する保持glyphは同一source line上のorigin差を維持。それ以外は元PDFの幅とtext state、またはHarfBuzzのadvanceを使用 |
| 新fontの幅 | CIDFontType2の `/W` は同じfontのnominal hmtx。HarfBuzzのcontextual advanceとoffsetはglyphごとの絶対 `Tm` で実現 |
| 改行 | unisegの改行候補、grapheme緊急分割、既存の日本語禁則。行末空白は組版時に除く |
| 行間 | 元baseline間隔または明示値を最低値とし、前行descent＋次行ascent、実inkが重ならないよう配置 |
| 幅 | observed / inferred / explicitを分離。unknownからobservedへの暗黙変換をしない |
| 字下げ | 観測した行左端から初行字下げを提案し、利用者が確認・上書き。提案は利用可能幅の推定ではない |

新規runの計測と保存に既存のHarfBuzz/fontTools/CID writerを再利用した。fontの読込やPDF描画状態の解釈を別言語で書き直しても、source区間と編集意図の対応は得られないため、今回は言語・依存ライブラリを変更していない。元resource忠実経路と全文font代替経路も別用途として維持する。

## Graphics stateと旧文字の除去

対象operator内の文字コードを取り除き、送りを数値TJへ変換して、後続の元text matrixを保つ。旧文字の上へ重ねる方法ではない。新しい文字は先頭の対象eventの位置で、そのclipと共通の描画状態の内側へ挿入する。挿入後はtext matrixとline matrixを復元する。

font・サイズ・色・横倍率をglyphごとに設定し、Tc/Tw/Tsによる効果を配置へ反映する。`Ts`を含んだ観測originをそのままbase baselineに使うとriseを二重適用するため、snapshotはriseを引いたbase baselineを別に保持する。また、縦方向のemとMuPDFの横倍率込みtrace sizeは異なる量として検証する。CTM 0.75、Tf 16、Tz 80では縦サイズ12pt、trace size9.6ptになる。

異なるclip、opacity、blend/mask、非device色、横書き以外などは対象外である。inline属性以外の追跡stateが区間ごとに異なる場合も拒否する。overprintやtransferを先頭eventの設定へ黙って統一しない。現在のinterpreterはExtGStateとmarked contentを保守的な履歴として記録するため、同等な状態を異なる命令列で表現したPDFも拒否し得る。

MCID等のmarked contentをまたぐ再配置は、表示以外にtagged PDFの文章構造へ影響する。これはfont/色の区間と同一視できない。今回のWord例はこの境界で止めた。タグのownershipや構造木の再構成を実装せずにguardを解除していない。

## 実PDFの結果

7試行すべてで、編集前の元operatorによるno-opはMuPDF/Popplerの対象ページ全画素と独立抽出が一致した。その後の書式付き編集は次の結果になった。**このno-opは元operatorの再構築の検証であり、改行を組み直した文章が原本と全画素一致するという主張ではない。**

| 原本・評価範囲 | 操作と結果 | 元glyph / 新font glyph |
|---|---|---:|
| LibreOffice移行資料、英字12pt＋日本語10.5ptの2行 | 新漢字を含む変更と後半削除。**2行→1行成功**、サイズ差と初行字下げを保持 | 38 / 4 |
| 同じ段落・同じ確認領域 | 2文字増の変更。改行候補の制約により3行が必要になり、下端を超えるため**安全拒否** | 保存なし |
| LibreOffice新機能資料の本文 | 元68文字を保持し66文字追加。**2行→3行成功** | 68 / 66 |
| LibreOffice企業向け資料、Bold＋Regular見出し | **安全拒否**。背景が4本のlineからなるfill pathで、現guardは単一 `re` の背景として証明できない | 保存なし |
| Word武雄資料、太字・通常体・字間の変わる見出し | **安全拒否**。選択内でMCIDを含むmarked-content履歴が変わり、inline以外の状態が共通でない | 保存なし |
| Ubiquiti仮想プリンタPDF、Arial＋Microsoft YaHeiの日付欄 | **部分置換成功**。漢字と時刻を元resourceで維持、日付を指定Arialで描く。狭いclip内に収まる | 8 / 9 |
| Canvia仮想プリンタPDF、通常体＋斜体の2行 | **部分置換成功**。通常体中の4文字を変更し、斜体とGautami内のU+200Bも元コードで維持 | 138 / 4 |

Canviaの144文字選択から、組版で行末空白2文字が除かれるため保存計画は142 glyph。空白を除く独立全文比較と、保存計画に対する厳密Unicode/GID比較を区別する。日本語本文の短文化で下流段落との空きは残る。対象段落の不要行を除去した成功であり、下流段落を押し上げるflow編集ではない。

4成功すべてについて以下を確認した。

- 元operatorのno-opを先にPoppler 144dpiで検証し、対象ページの変更画素0。MuPDFも全ページ一致。
- 保存glyphのUnicode、GID、origin、実効サイズ、色、opacity、元font保持を計画と照合。元encoded bytesとresource aliasも原本へ再照合。
- 新CIDToGIDMapと `/W` を実ファイルで読んで照合。ToUnicodeの存在と独立した全文抽出を確認。
- pypdf別プロセスによる対象ページ全文が、意図した1か所の置換と一致。削除中間PDFは同じ範囲を空にした全文と一致。他ページは正確に一致。
- Popplerの実PNG同士で、元文字と新inkのunion＋1ptの外側の変更画素は**全チャンネル差分0**。MuPDFでも編集領域外・他ページ画素を照合。
- 全ページの図形・画像fingerprintを照合。原本SHA不変。成功した4出力のPoppler描画を目視し、折返し、字体差、斜体、隣接画像を確認。

判定は4成功・3安全拒否。6種類の原本に対する手動選択の技術検証であり、一般文書の成功率を推定する標本ではない。評価スクリプトの入力誤りや独立検証失敗は `failed_evaluation` とし、安全拒否へ集計しない。

## 新しく成立したことと残る境界

短い文章の全文代替から、**元の文章と書式を残したまま部分編集し、その混在したglyph列を再組版する**ところへ進んだ。元subsetへ新しい漢字を追加する必要も、変更していない斜体を通常体のfontで描き直す必要もない。

一方、保持区間と新font区間はshaping境界になる。保持した `e` へ新しい結合アクセントだけを追加すると、完全なclusterをshapeできないため拒否する。利用者は `e` を含むgrapheme全体を置換する。普通の欧文でも境界をまたぐkerning・合字は再形成しない。自然さをさらに上げるには、source対応を保ったrun単位のreshapingと、そのfontを利用できるかの明示判断が必要になる。

現source decoderは1code→1Unicode文字を主に扱う。新font出力が複数Unicode→1glyphの合字になった場合、検索・抽出できても、再観測して再編集する段階で拒否する場合がある。CJKの長文化出力を再観測し、追加fontを増やさず短文化できることは統合テストで確認したが、全clusterの再編集成立には広げない。強いsuperscriptは行推定が別行と判断し得るので、任意のbaseline構造の自動復元も未成立である。

paintについては丸角背景や穴を持つ複合path、下線・リンク、Form invocationの所有関係が残る。単純な矩形を4本のlineで表現した背景も拒否する現象は、PDF固有の問題ではなく、実形状と描画順に基づくモデルが不足している例である。成熟MuPDF deviceを観測adapterとし、元operatorのbyte provenanceと対応付ける構成に進む価値が高い。必要なのはPython全体の置換ではなく、限定interpreterの観測責務を成熟rendererへ移し、source編集責務を分離することだ。

今回のsource-linkedモデルはその先でも利用できる。ただし、ページ全体のflowをこのwriterの座標移動だけで拡張するべきではない。次の大きな構造課題は、**文字・装飾・背景・タグ・固定要素がどの編集対象に属するかと、そのpaint順・clipを表すモデル**である。その後に、可動要素・anchor・後続関係・改ページ制約を持つ文書flowへ接続する。

## 再現方法

原本は [sources.json](../evaluations/sources.json) の配布元とSHA-256で準備する。初回は元resource評価の手動selectionを生成する。fontは評価入力で指定された完全なものを用意する。元PDF、font、派生PDF/PNG、生のglyphや全文ログは公開しない。

```powershell
.\.venv\Scripts\python.exe -m evaluations.backend.seed_ranges
.\.venv\Scripts\python.exe -m evaluations.attributed.evaluate --run-name my_attributed_run --font-dir C:/Windows/Fonts
.\.venv\Scripts\python.exe -m pytest -q
```

本評価のローカル出力は `evaluations/attributed/runs/reviewed_v2`。公開集計はengine各モジュールとrunnerのSHA-256を含む。同名runは上書きしない。CLI単独の検証はMuPDFのみで、独立renderer検証済みとは報告しない。

旧経路の回帰は `evaluations/backend/runs/attributed_regression`、`attributed_stages_regression` と `evaluations/composition/runs/attributed_regression` に分けて保持する。元resourceのStage 1は13成功・2拒否、後続はStage 2の6成功・Stage 3の13成功・Stage 4の2成功を維持した。全文font代替も7成功・6拒否で従来結果を維持した。これらの証跡と新経路の結果を混ぜて成功率にしない。

全テストは **312成功・2skip**。skipは既存の暗号化試験で、AES用providerがない環境によるもの。新しい試験は、色・サイズ・太字の保持、混在styleの同文再構成全画素一致、長文化→短文化→再編集、CTM/Tz/Ts、結合文字境界、overprintの差、CLIの入力・出力保護を含む。

ライセンスはAGPL-3.0-onlyを維持し、新しい依存ライブラリを導入していない。
