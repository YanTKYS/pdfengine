# PDF本文の再編集・再レイアウトエンジン PoC

公開版はコード・テスト・評価スクリプト・報告と集計を含みます。外部 PDF と派生 PDF/PNG・生ログはローカル保持です。入力の取得元と、報告内のローカル証跡リンクについては [PUBLICATION.md](PUBLICATION.md) を参照してください。

既存PDFから文字・グリフの配置を取得し、行・段落・テキスト領域を推定する独立したプロトタイプです。実PDFの忠実性評価では、推定範囲と再描画backendを分離し、元の文字コード・font resource・`/W`を保持した局所編集を優先します。既存の `idontlovepdf` / `idontlovepdf-engine` のコード・設計は使用していません。

Python + PyMuPDF + pypdf + uniseg + fontToolsを採用しました。対象領域の旧文字だけをtext operator単位で除去し、Stage 1では同じoperator位置へ元コードを戻します。明示幅があるStage 4だけ局所reflowを許可します。画像化や白塗りの重ね書きではありません。PDF解析とレイアウト処理は別モジュールです。

**検証結果: 自動テスト179件成功、AES暗号化2件は依存provider未導入のためskip。** 外部生成PDF 15件・58ページを評価し、手動範囲13件でStage 1 no-opが合格しました。任意のPDFをAcrobatと同等に編集できる段階ではありません。

## 外部生成PDFの評価

[初期の実PDF評価報告](docs/realpdf-evaluation.md)に加え、現行backendの[Track A/B結果](docs/backend-evaluation.md)を作成しました。Word、LibreOffice、Chrome/Skia、仮想プリンタ等の15原本について、[症例別backend一覧](evaluations/backend/backend_matrix.csv)、[Stage 1証跡](evaluations/backend/runs/stage1_final_v2/results.json)、[Stage 2--4証跡](evaluations/backend/runs/stages_final_v4/results.json)を参照できます。

修正前の固定53試行では5件保存しましたが、元subset fontを再利用した2件で文字が重なり、別の1件で暗号化・権限設定が失われました。実描画の文字位置を保存前に検査し、暗号化を保持する小修正後は、同じ53試行が3件保存・50件拒否となりました。修正前の破損出力は比較証跡として凍結保存しています。

保存できても推定段落が正しいとは限りません。質問文の途中でBoxが分かれる、本文と日付が結合されるなどの誤認識が残ります。今回確認できた範囲は、**意味上の編集範囲を人が確認し、元fontが必要glyphを持つ局所編集**です。短い1行の自動幅推定、表・箇条書き、未知のCMap、元subsetにない文字の同font出力は安全に拒否します。

再現方法と原本のURL・SHA-256、修正前後の結果、MuPDF/Popplerの画像比較、pypdfによる再抽出、除去単独の診断は評価報告から参照できます。追加8試行は新漢字・英数字・記号の実描画と誤結合を調べた診断で、固定53試行とは別に集計しています。

## すぐ実行する

この作業フォルダーには依存導入済みの `.venv` があります。PowerShellでプロジェクト直下から実行できます。

```powershell
# 認識した領域を一覧表示する（ページは1始まり）
.\.venv\Scripts\pdfeditor.exe inspect output/pdf/original.pdf --page 2

# 対象を含む領域全体を再レイアウトして、新しいPDFへ出力する
.\.venv\Scripts\pdfeditor.exe edit output/pdf/original.pdf output/pdf/my-edit.pdf --page 2 --find "申請期限は9月10日です。" --text "申請書類の提出期限は10月15日までです。必要事項を記入して提出してください。" --report output/pdf/my-edit.json

# 単体・統合テスト
.\.venv\Scripts\python.exe -m pytest -q
```

`python -m pdfeditor` でも同じCLIを利用できます。既存出力を上書きしないため、再実行時は別の出力名を指定してください。元PDFは変更しません。

## 手動選択を使うbackend PoC

推定Boxをそのまま編集対象にせず、観測したLine/Run/glyphを確認してsource-bound manifestを作成できます。

```powershell
# Line/Run/glyph候補を観測
.\.venv\Scripts\pdfeditor.exe observe input.pdf --page 1 --json catalog.json

# Line/Run/glyphを選び、利用可能幅は確認できたときだけ入力
.\.venv\Scripts\pdfeditor.exe select input.pdf selection.json --line p1-l5 --run p1-l6-r1 --width 425

# MuPDFによるStage 1再描画。後続段階には独立Poppler証跡が必要
.\.venv\Scripts\pdfeditor.exe replay input.pdf replayed.pdf --selection selection.json --removal-output removed.pdf --report replay.json

# 独立検証済みのStage 1 gateを使ったStage 2/3/4
.\.venv\Scripts\pdfeditor.exe edit-selected input.pdf edited.pdf --selection selection.json --stage 2 --stage1-report stage1-gate.json --text "変更後の文章" --report edit.json
```

`inferred_available_width`が不明なまま長文化することはありません。Stage 1 gateは同一source SHA、ページ観測、glyph/state、Poppler画像、pypdf抽出の全条件を満たす必要があります。

## 別環境へのインストール

Python 3.11以上が必要です。検証環境はWindows x64 / Python 3.12 / PyMuPDF 1.27.2.3です。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

macOS / Linuxでは `.venv/bin/python`、`.venv/bin/pdfeditor` を使います。今回その環境での実行は未検証です。出力確定には同じディレクトリ内でのhard linkを用いるため、対応するファイルシステムが必要です。生成用フォントはMuPDF同梱のDroid Sans Fallbackを使用でき、OSの日本語フォントを別途用意しなくてもサンプルを動かせます。

## サンプルと確認結果

入力は [original.pdf](output/pdf/original.pdf) の5ページです。各出力PDFは元の5ページを保持し、表の対象ページだけを編集しています。比較時には同じページを開いてください。

| ケース | 対象ページ | 確認内容 | 結果 | 出力 |
| --- | ---: | --- | --- | --- |
| A | 1 | 「申請」→「申請書類」 | 2文字から4文字、1行を維持 | [case-A.pdf](output/pdf/case-A.pdf) |
| B | 2 | 短い申請期限文を長文化 | 1行→3行 | [case-B.pdf](output/pdf/case-B.pdf) |
| C | 3 | 複数行の文章を短文化 | 3行→1行、旧行を除去 | [case-C.pdf](output/pdf/case-C.pdf) |
| D | 4 | 日本語・Wi・WWW・iii・数字の混在 | 比例幅で1行→2行 | [case-D.pdf](output/pdf/case-D.pdf) |
| E | 5 | 新たな漢字を含む文章へ変更 | 同じ埋め込みフォントと12 ptを維持 | [case-E.pdf](output/pdf/case-E.pdf) |

各PDFと同名のJSONに、変更前後の文章、行数、各行の幅・baseline、領域bbox、行間、字間、フォント保持・代替理由を保存しています。集計は [evaluation.json](output/pdf/evaluation.json)、評価方法と数値は [docs/evaluation.md](docs/evaluation.md) にあります。

主サンプルの260 ptは初期A--Eで明示した評価値です。短い一行の印字範囲だけから、本来の編集枠幅は決められません。現行の手動selectionでは、枠のないPDFの幅は `unknown` として保持し、長文化やStage 4には `--width` による確認値を要求します。既存の囲み罫線や背景は別要素として衝突検査の対象です。

再生成は新しいフォルダーへ行います。

```powershell
.\.venv\Scripts\python.exe -m examples.demo --output-dir output/demo-next
```

## 領域と文章を指定する

```powershell
# Glyph / Run / Word / Line / Paragraph / TextBoxと座標を新規JSONへ保存
.\.venv\Scripts\pdfeditor.exe inspect input.pdf --page 1 --json model.json

# inspectで確認したIDの領域全体を置換
.\.venv\Scripts\pdfeditor.exe edit input.pdf output.pdf --page 1 --box p1-b2 --text "新しい文章" --width 240

# UTF-8ファイルの明示改行・空行を維持し、高さを制限
.\.venv\Scripts\pdfeditor.exe edit input.pdf output.pdf --find "対象の文章" --text-file replacement.txt --width 240 --max-height 120

# 元フォントが利用できないときの代替フォントを指定
.\.venv\Scripts\pdfeditor.exe edit input.pdf output.pdf --find "対象の文章" --text "変更後の文章" --font path/to/font.ttf
```

`--find` は一つの推定領域内で一意な部分文字列です。指定部分の変更後、周囲の文章も含む領域全体を組み直します。複数候補がある場合は `--box` と組み合わせます。`--box` だけなら領域全体を置換します。IDはその入力・ページを解析した結果で、編集後も同じIDが維持される契約ではありません。

寸法はPDF pointです（72 pt = 1 inch）。`--max-height` は新しい文章の上端からの最大高さです。指定しなくてもページ外・周辺要素との衝突を検査します。折返しのためにフォントサイズを勝手に縮小しません。入力した単一改行は強制改行、空行は段落間隔です。元PDFの同一段落の物理的な折返しは結合し直します。

`--font` は元フォントの欠字・未埋込等がある場合の代替指定です。元フォントを維持できる場合は元を優先します。フォントの暗黙fallbackは事前のglyph coverage確認で防ぎ、代替した場合は理由をJSONに残します。

## 構成

```text
pdfeditor/
  model.py       PDF非依存の観測・編集モデル
  inference.py   描画順に依存しない行・単語・段落・領域推定
  layout.py      実測幅 + UAX #14 + 禁則処理による再組版
  fonts.py       フォント選択、coverage、metrics、一定字間の推定
  backend.py     既存の推定編集経路（legacy）
  content_stream.py  text operator、font code、graphics stateの限定解釈
  replay.py      Stage 1 no-opと除去中間証跡
  slot_edit.py   Stage 2/3の元slot編集
  explicit_reflow.py Stage 4の明示幅reflow
  pdf_save.py    resource/暗号化を保持する保存adapter
  engine.py      legacy推定編集を接続する処理
  cli.py         inspect / edit / observe / select / replay / edit-selected
examples/demo.py  レイアウトエンジンを使わず原文PDFを作る評価例
tests/             PDF非依存テスト、実フォントテスト、実PDF統合テスト
```

`layout_text()` はPDFオブジェクトを受け取らず、文字列・領域寸法・幅計測関数から行と座標を返します。バックエンド差替え時にも段落モデルと組版ロジックを再利用できます。[方式比較・採用理由・内部モデル・発展設計](docs/architecture.md) を参照してください。

## 評価上重要な制約

- 段落と枠幅はgeometryからの推定です。日本語のWordはUnicode境界で、形態素解析ではありません。短い行、表、複雑な段組では `inspect` で推定結果を確認する必要があります。
- 現在の再描画は同一書式・横書き・左揃えです。字下げ・右揃え・両端揃えは保持しません。複雑なshapingを要する文字や混在書式は拒否する範囲を設けています。
- 領域が伸びて別の文章・図形・画像へ衝突すると保存を止めます。ページ全体の移動・改ページは設計までで、実装していません。
- 回転、特殊な字幅、text clip、透明グループ、未知CMap、Form内の対象等は保守的に拒否します。クリップは対象text eventへ作用するscopeを追跡し、安全に矩形として検証できる場合だけ許可します。元PDFのあらゆる描画状態を検出・再現する保証はありません。
- 元subsetに必要glyphがない場合は同じfont名のまま受け入れません。Stage 2は元font code mapに存在する文字だけ、Stage 4は元fontのcode/widthを使える文字だけを許可します。font代替や新規font埋込みは別backend評価が必要です。
- 必要Glyphがあっても、書出し後のUnicode列や文字原点が計画と一致しなければ保存を拒否します。この検査は字形の完全同一性や推定段落の意味上の正しさを保証しません。

これらは機能の羅列ではなく、今回の局所再描画方式が成立する範囲を決める条件です。ページ全体のリフロー、表、縦書き、図形回避、HarfBuzzによるshaping、編集UIへ進むためのモデルと書込backendの拡張方針を [architecture.md](docs/architecture.md) に記録しています。

## 依存ライブラリの条件

PyMuPDF / MuPDFはAGPL v3または商用ライセンスです。製品への組込み・配布・サービス提供はその条件を前提に判断する必要があります。許諾型ライセンスの構成が必要ならPDFium/PDFBoxへの差替えを比較候補として整理済みです。fontTools/unisegのコードの条件と、入力PDFに含まれるフォントの条件は別です。公式根拠は [比較資料のライセンス節](docs/architecture.md#ライセンス上の選定条件) にあります。
