# pdfengine — 既存PDF本文の局所再組版

既存PDFの文字を人が選び、確認した領域内で文章を置き換える研究エンジンです。旧文字はContent Streamの対象コードを除去して消し、編集可能なテキストとして再描画します。画像化や白塗りは使いません。

現在は、元フォントを忠実に保持する経路と、指定した完全なフォントから新しい文字を描く経路を持っています。自動推定は選択候補を作り、編集範囲・利用可能幅は人が確定できます。

## 現在できること

| 経路 | 用途 | フォントと配置 |
|---|---|---|
| `replay` | 選択範囲の同文再描画と旧文字除去の検証 | 元コード・resource・graphics stateを保持 |
| `edit-selected` | 元resourceで表現できる文字の置換・短文化・明示幅での再組版 | 元fontを維持。符号化可能性・幅などを検査 |
| `compose-selected` | 元subsetにない漢字・英数字・記号を含む置換、長文化・短文化 | 利用者指定fontを埋め込み、HarfBuzzの実glyph配置で再組版 |

新しい組版経路では、Word由来の新漢字への変更、LibreOffice由来の2行→3行、Chrome/Skia由来の2行→4行、Word由来の4行→1行を実PDFで確認しました。**指定fontへの代替としての成功**であり、元の書体と同一という意味ではありません。[実PDFごとの結果と技術境界](docs/composition.md)を参照してください。

## インストール

Python 3.11以上。検証環境はWindows x64 / Python 3.12です。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

macOS/Linuxでは `.venv/bin/python` を使います。この版の実PDF評価はWindowsで行っています。

## 範囲を確認して編集する

```powershell
# Line / Run / glyphの候補と座標を観測する（ページは1始まり）
.\.venv\Scripts\python.exe -m pdfeditor observe input.pdf --page 1 --json catalog.json

# 観測したIDを指定する。数値はPDF point。幅は利用可能領域を確認して入力する
.\.venv\Scripts\python.exe -m pdfeditor select input.pdf selection.json --line p1-l3 --line p1-l4 --width 420

# 前後Lineを追加・除外した選択を別ファイルへ保存できる
.\.venv\Scripts\python.exe -m pdfeditor select input.pdf corrected.json --from-selection selection.json --add-line p1-l5 --exclude-line p1-l3

# 完全なTrueType fontを指定し、UTF-8の文章を折り返して新規PDFへ出力する
.\.venv\Scripts\python.exe -m pdfeditor compose-selected input.pdf edited.pdf --selection selection.json --font path/to/font.ttf --text-file replacement.txt --max-height 120 --report edit.json --removal-output removed.pdf
```

`--run p1-l3-r1` や `--glyph 42` でも選択できます。selectionは原本SHA-256・ページ観測・glyph IDに結び付き、別原本への流用を拒否します。編集後に続けて編集するときは、出力PDFを再観測して新しいselectionを作成します。

`--font` は選択範囲全体へ適用する明示的なフォント指定です。TrueType collectionは `--font-index`、可変fontは `--font-variation wght=400` などを指定できます。指定しない軸はfontの既定値です。名前から同一書体やweightを推測しません。必要なglyph・輪郭がなければ暗黙fallbackせず拒否します。fontは利用者が用意し、リポジトリには同梱しません。

`--max-height` は新fontのascenderから求める文章上端からの高さです。省略時もページ端・周辺要素・clipを検査します。`--line-height` はbaseline間隔で、元の行間と新fontの上下metricsを基準にした既定値を上書きできます。サイズを自動縮小して収めることはありません。

出力PDF・診断PDF・JSONは新規パスのみ受け付けます。原本と既存出力は上書きしません。保存にはhard linkを作成できるファイルシステムが必要です。

## 忠実性と幅の契約

`observed_content_width`、`inferred_available_width`、`explicitly_supplied_width`を分離しています。推定できない幅はunknownです。`compose-selected`には既知の利用可能幅が必要で、観測文字幅を暗黙の編集幅にしません。

新規font経路は、変更前の元resourceによる同文再描画と除去を毎回検査してから組版します。保存後にはUnicode、GID、origin、サイズ・色・opacity、既存font、対象外glyph、対象外ページ・領域外画素、暗号化・権限を照合します。fontToolsが作ったsubsetの輪郭・幅も、HarfBuzzで使ったfontと比較します。

CLIの自動検証はMuPDFによるものです。**CLIで保存できたことだけで別レンダラーでの成功とはしません。** 実PDF評価では変更前のno-opをPopplerで先に検証し、編集後もPopplerの画素比較、独立pypdf抽出、削除中間PDF、CIDToGIDMapと`/W`、図形・画像を検査します。[評価手順](docs/composition.md#再現方法)を参照してください。

元fontを保持する旧経路は `replay` → 独立検証済みの証跡 → `edit-selected --stage 2|3|4 --stage1-report ...` です。CLIの `replay` 報告だけでは後続gateを開きません。[元resource経路の評価](docs/backend-evaluation.md)に契約を記載しています。

## 現在の設計が扱う範囲

選択範囲内の横書き・共通サイズと字間・不透明fillを中心に扱います。元fontが途中で変わる範囲でも、ほかの描画条件が共通なら、明示した一つのfontへ組み直せます。太字・色・サイズなどを区間ごとに保持するrich text再組版は、次に必要なモデル拡張です。

新しいglyphを置く範囲はactive clipと周囲の文字・画像・図形で制限されます。後続段落や表を移動して場所を作る処理はまだありません。複雑なpathやForm内文字などは、描画単位の意味を安全に扱えるまで拒否する範囲が残ります。複雑な多glyph cluster・双方向組版も現在のUnicode復元契約を拡張する必要があります。

## 実装と検証資料

- `content_stream.py` / `selection.py`: 元operatorと人が確定するglyph範囲
- `shaped_font.py` / `composition.py`: HarfBuzz、fontTools、新規CID fontと局所再組版
- `layout.py`: PDF非依存の改行・配置。候補文字列全体の実測幅を受け取る
- `pdf_save.py`: resource追加、共有辞書の分離、暗号化を保持する保存adapter
- [現在の技術選定・実PDF評価](docs/composition.md)、[内部モデルと初期設計](docs/architecture.md)、[従来writer比較](docs/backend-options.md)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

公開対象はコード・テスト・評価スクリプト・集計です。外部PDF、font、派生PDF/PNG、生の抽出ログはローカル保持です。取得元・SHA-256と再現方法は [PUBLICATION.md](PUBLICATION.md) にあります。

独自コードのライセンスは [AGPL-3.0-only](LICENSE) です。PyMuPDF/MuPDF、HarfBuzz等の依存条件は [ライセンス方針](docs/licensing.md)を参照してください。
