# pdfengine — 既存PDF本文の局所再組版

既存PDFの文字を人が選び、確認した領域内で文章を置き換える研究エンジンです。旧文字はContent Streamの対象コードを除去して消し、編集可能なテキストとして再描画します。画像化や白塗りは使いません。

元フォントを忠実に保持する経路、選択範囲全体を指定fontで組み直す経路、**未変更区間の元font・書式を残し、編集区間だけ指定fontで描く経路**を持っています。自動推定は選択候補を作り、編集範囲・利用可能幅は人が確定できます。

文字に付随する背景・罫線・下線については、元の描画命令とrendererのpaintを対応付け、人が所属と振る舞いを確定するモデルを持ちます。文字と図形の一式移動、固定背景内の編集に加え、確認した文字範囲の下線を文章変更と折返しに追従させられます。

## 現在できること

| 経路 | 用途 | フォントと配置 |
|---|---|---|
| `replay` | 選択範囲の同文再描画と旧文字除去の検証 | 元コード・resource・graphics stateを保持 |
| `edit-selected` | 元resourceで表現できる文字の置換・短文化・明示幅での再組版 | 元fontを維持。符号化可能性・幅などを検査 |
| `compose-selected` | 元subsetにない漢字・英数字・記号を含む置換、長文化・短文化 | 利用者指定fontを埋め込み、HarfBuzzの実glyph配置で再組版 |
| `inspect-paragraph` → `edit-paragraph` | 書式を持つ文章の一部を変更、追加、削除 | 元Unicode区間とstyleを対応付け、未変更部分の元resource・コード・GIDを保持。変更部分だけ明示fontでshape |
| `inspect-element` → `move-element` | 所属を確認した文字・背景・罫線・下線を一式で移動 | 元のtext/path命令・座標token・fontを保持。描画順、clip、固定物との衝突を検査 |
| `inspect-anchors` → `edit-paragraph --anchors` | 文字範囲に属する下線を長文化・短文化・折返しに追従 | 元の下線paintを除去し、同じpaint状態で各行の実advanceに沿って再生成。背景・外枠は固定 |
| `edit-paragraph --editable-state` → `edit-document` | 確認した文章・改行・装飾範囲・領域を保存し、同じ意味で再編集 | 通常のPDFとSHA-256で結び付いたsidecar。生成した折返しを論理改行へ変換しない |

書式を保持する経路では、LibreOffice本文の英字12pt・日本語10.5ptを残した2行→1行、元の68文字を残して66文字を追加する2行→3行、仮想プリンタPDFの通常体・斜体を残す部分置換を確認しました。[書式付き編集の評価と境界](docs/attributed-editing.md)を参照してください。全文font代替経路でのWord・Chrome等の結果は [composition.md](docs/composition.md) にあります。指定fontで描く部分は、元書体と同一とは限りません。

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

## 元の書式を残して一部を編集する

```powershell
.\.venv\Scripts\python.exe -m pdfeditor inspect-paragraph input.pdf --selection selection.json --json paragraph.json
.\.venv\Scripts\python.exe -m pdfeditor edit-paragraph input.pdf edited.pdf --paragraph paragraph.json --edits changes.json --max-bottom 500 --report edit.json --removal-output removed.pdf
```

`paragraph.json` でUnicode列・書式区間・行の結合を確認します。`changes.json` は例えば次の形式です。位置は**変更前のUnicode列の0始まり、終端を含まない区間**であり、PDF glyph IDではありません。下記は確認済みの5〜8番目の文字を変更する例です。

```json
{
  "edits": [{"start": 5, "end": 9, "text": "新しい本文", "font_id": "body"}],
  "fonts": {"body": {"path": "fonts/body.ttf", "font_index": 0}}
}
```

書式は元区間から継承します。複数書式にまたがる置換には、snapshot内の `style_id` を明示します。`start == end` は挿入、空文字は削除です。複数編集も変更前の位置で指定します。新fontを使う範囲は編集区間であり、同じfont名やsubset coverageから自動選択しません。相対fontパスは `changes.json` の置かれたディレクトリを基準にします。

利用可能幅はselectionの値か `--width` が必要です。`--x`、`--first-line-indent`、`--min-line-height` で配置を指定できます。`--max-bottom` はページ左上原点の下端y座標で、`compose-selected --max-height` の高さ指定とは異なります。元の物理行の結合は `inspect-paragraph --line-joiner none|space|newline` で選びます。合成する空白にも必要に応じてfont指定が必要です。snapshot自体を変更する場合は、selectionや結合方法を直して作り直します。

## 背景・罫線との関係を確定する

```powershell
.\.venv\Scripts\python.exe -m pdfeditor inspect-element input.pdf --selection selection.json --json element.json
.\.venv\Scripts\python.exe -m pdfeditor move-element input.pdf moved.pdf --element element.json --relations relations.json --dy 180 --report move.json --removal-output removed.pdf
```

`element.json` はsource位置、実際のpaint、推定された関係、marked contentの観測を分離して記録します。確認したpathの実際の `source_id` を使い、`relations.json` を作ります。以下のIDは形式を示す例です。

```json
{
  "relations": [
    {"source_id": "path-...", "relation": "backgrounds", "behavior": "fixed-to-element"},
    {"source_id": "path-...", "relation": "decorates", "behavior": "fixed-to-element"}
  ]
}
```

関係は `backgrounds` / `borders` / `decorates` / `unrelated`、振る舞いは `fixed-to-element` / `fixed-to-page` です。候補を自動確定せず、元命令との対応が曖昧な重複paintも拒否します。新しい要素APIは非回転ページに限定し、移動には完全な `Tj` / `TJ` の選択が必要です。移動前に同じ位置での再構築を検査し、移動後も元font・全paint・領域外画素を照合します。

ページ背景を固定した本文編集では、背景を `backgrounds` / `fixed-to-page` として、`edit-paragraph` に `--element element.json --relations relations.json` を追加します。元の描画順と実形状による包含が証明できる背景だけを許可します。[要素モデル・技術選定・実PDF評価](docs/element-ownership.md)を参照してください。

## 下線を文章の変更に追従させる

```powershell
.\.venv\Scripts\python.exe -m pdfeditor inspect-anchors input.pdf --paragraph paragraph.json --element element.json --json candidates.json
# candidates.jsonの候補を確認し、下記形式のanchors.jsonを別途作る
.\.venv\Scripts\python.exe -m pdfeditor edit-paragraph input.pdf edited.pdf --paragraph paragraph.json --edits changes.json --element element.json --anchors anchors.json --max-bottom 500 --report edit.json --removal-output removed.pdf
```

```json
{
  "paragraph_sha256": "paragraph.jsonのsnapshot_sha256",
  "element_sha256": "element.jsonのsnapshot_sha256",
  "underlines": [{
    "source_ids": ["path-...", "path-..."],
    "range": [0, 30],
    "start_affinity": "outside",
    "end_affinity": "inside"
  }],
  "fixed_relations": [
    {"source_id": "path-...", "relation": "backgrounds", "behavior": "fixed-to-page"}
  ]
}
```

`range` は確認した元Unicode列の区間です。候補の幾何や近さから所属を自動確定しません。境界位置へ挿入する文字を下線の内側に含めるかは `inside` / `outside` で指定し、省略時の `reject` は境界挿入を拒否します。境界をまたぐ置換には範囲の再確認が必要です。区間全体の削除では下線も除去します。

現在の対象は、source対応が一意な不透明・単色・矩形fillの横下線です。元の色・太さ・baselineからの距離を保持し、行ごとに独立したpaintとして保存します。外部生成PDFで長文化、短文化、保存後の再編集を検証しています。[結果と制約](docs/anchored-decoration.md)を参照してください。背景・外枠の伸縮、他段落の追従移動は行いません。

PDFだけから再編集する場合はsnapshotを作り直し、**折返し位置の空白と段落境界を確認**してください。PDFから元のsoft wrapとhard breakは自動復元できません。例えば英語の物理行を空白なしで連結すると、単語が連結されたまま再組版されます。`--line-joiner space` と明示的な改行編集を使えますが、単語途中の折返し等は別途確認が必要です。

## 編集の意味を保存して再編集する

```powershell
# 確認したparagraphと変更を、PDFと編集用sidecarの組として保存する
.\.venv\Scripts\python.exe -m pdfeditor edit-paragraph input.pdf first.pdf --paragraph paragraph.json --edits changes.json --editable-state first.edit.json --max-bottom 500

# 保存した論理文字列と境界を確認する。physical lineの再結合は不要
.\.venv\Scripts\python.exe -m pdfeditor open-editable first.pdf --state first.edit.json --json reopened.json

# 次の変更位置は、保存した論理文字列のUnicode位置で指定する
.\.venv\Scripts\python.exe -m pdfeditor edit-document first.pdf second.pdf --state first.edit.json --state-output second.edit.json --edits next-changes.json --report second-report.json
```

初回に下線や固定背景を確認した場合は、従来の `--element` と `--anchors` または `--relations` も渡します。その関係、利用可能幅・下端、baseline、論理文字列をsidecarへ保存し、次回に引き継ぎます。PDFとsidecarは一緒に保持してください。通常のPDF viewerはPDFだけで表示できます。

明示した改行はhard breakとして保存します。段落境界を区別する場合、変更JSONの `"boundary_kinds": {"13": "paragraph_boundary"}` のように、**変更後**の文字列の改行開始位置を指定します。保持された境界は次の編集で位置を更新し、CRLFも一つの境界として扱います。自動折返しは別の生成結果です。

sidecarがない、壊れている、PDFが外部ツールで保存し直された場合、`open-editable` は `needs_confirmation` と物理観測を返します。`edit-document` はその状態で編集せず、再確認を要求します。fontの指定はファイルhashとともに引き継ぎ、移動・変更されたfontには次の変更JSONで明示的な指定が必要です。sidecarは確認済みの編集用入力として扱い、checksumは作成者の署名ではありません。

[意味保持の設計・外部再保存・実PDF評価](docs/persistent-editing-semantics.md)に成立範囲を記録しています。現在は一つの選択要素と固定領域を保存するモデルです。

## 忠実性と幅の契約

`observed_content_width`、`inferred_available_width`、`explicitly_supplied_width`を分離しています。推定できない幅はunknownです。`compose-selected`には既知の利用可能幅が必要で、観測文字幅を暗黙の編集幅にしません。

新規font経路と書式付き経路は、変更前の元resourceによる同文再描画と除去を毎回検査してから組版します。保存後にはUnicode、GID、origin、サイズ・色・opacity、既存font、対象外glyph、対象外ページ・領域外画素、暗号化・権限を照合します。fontToolsが作ったsubsetの輪郭・幅も、HarfBuzzで使ったfontと比較します。

CLIの自動検証はMuPDFによるものです。**CLIで保存できたことだけで別レンダラーでの成功とはしません。** 実PDF評価では変更前のno-opをPopplerで先に検証し、編集後もPopplerの画素比較、独立pypdf抽出、削除中間PDF、CIDToGIDMapと`/W`、図形・画像を検査します。[評価手順](docs/composition.md#再現方法)を参照してください。

元fontを保持する旧経路は `replay` → 独立検証済みの証跡 → `edit-selected --stage 2|3|4 --stage1-report ...` です。CLIの `replay` 報告だけでは後続gateを開きません。[元resource経路の評価](docs/backend-evaluation.md)に契約を記載しています。

## 現在の設計が扱う範囲

書式付き経路は、横書き・不透明fillで、font・サイズ・色・字間・横倍率・baseline shiftを区間ごとに保持します。clipやinline属性以外の描画状態は共通である必要があります。元文字と新fontの境界を越える結合文字は、完全なgraphemeを一つのfontで置換する指定を要求します。保持区間と変更区間をまたぐkerningや合字の再形成は未対応です。

新しいglyphを置く範囲はactive clipと周囲の文字・画像・図形で制限されます。後続段落や表を移動して場所を作る処理はまだありません。複雑なpathやForm内文字などは、描画単位の意味を安全に扱えるまで拒否する範囲が残ります。複雑な多glyph cluster・双方向組版も現在のUnicode復元契約を拡張する必要があります。

## 実装と検証資料

- `content_stream.py` / `selection.py`: 元operatorと人が確定するglyph範囲
- `shaped_font.py` / `composition.py`: HarfBuzz、fontTools、新規CID fontと局所再組版
- `attributed.py` / `paragraph.py` / `rich_layout.py`: 元Unicode区間、書式、保持・新規glyph provider、書式ごとのmetricsによる行組み
- `layout.py`: PDF非依存の改行・配置。候補文字列全体の実測幅を受け取る
- `pdf_save.py`: resource追加、共有辞書の分離、暗号化を保持する保存adapter
- `paint_provenance.py` / `marked_content.py` / `paint_geometry.py` / `elements.py`: sourceと解釈済みpaintの対応、active scope、実形状の包含、明示的な所属と局所移動
- `anchors.py`: 確認したUnicode範囲の編集後への投影、行単位の装飾計画、source paintの局所置換と照合
- `editable.py`: 物理glyphへの検証済みbindingと論理文書のsidecar、改行・領域・装飾関係の保持、失効時の確認用fallback
- [書式付き編集](docs/attributed-editing.md)、[全文font代替](docs/composition.md)、[paint観測の技術比較](docs/paint-observation-options.md)、[内部モデルと初期設計](docs/architecture.md)

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

公開対象はコード・テスト・評価スクリプト・集計です。外部PDF、font、派生PDF/PNG、生の抽出ログはローカル保持です。取得元・SHA-256と再現方法は [PUBLICATION.md](PUBLICATION.md) にあります。

独自コードのライセンスは [AGPL-3.0-only](LICENSE) です。PyMuPDF/MuPDF、HarfBuzz等の依存条件は [ライセンス方針](docs/licensing.md)を参照してください。
