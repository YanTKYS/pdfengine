# 隣接Span編集の最小GUI PoC — 大阪府Word由来PDF

記録日：2026-10-20（Asia/Tokyo）。ブランチ `claude/vibrant-hamilton-2mwfzf`。PR #81マージ後のmain
`aa620ef` から作り直したブランチで作業した。production（`pdfeditor/`）の変更はない。

## 結論

**PASS** — PDF画面上の文字選択から、提案の確認、明示承認、保存、生成PDFの表示、再open（`restored`）まで、GUIから操作できた。

実PDFの編集結果（PDF・sidecar）は、PR #81の出力とSHA-256まで一致した。判断はすべて既存の `propose_adjacent_intent`・`accept_adjacent_intent`・`edit_adjacent_intent` が行い、GUIは所有権・矩形・フォント・境界を一切補正しない。

**GUIの確認範囲**：この環境には実ディスプレイがない。そのため、仮想Xサーバー（Xvfb）上でTkアプリを実際に起動し、canvasへのマウスイベント・Entryへの入力・ボタン押下をプログラムから発生させて操作した。各段階のスクリーンショットは目視で確認した。

人が実ディスプレイとマウスで操作した確認は未実施である。ファイル選択ダイアログ（「PDFを開く」と保存先の選択）も、自動操作では保存先を注入して迂回しており、未確認である。

## 実装

| ファイル | 内容 |
| --- | --- |
| `examples/adjacent_span_session.py` | UIから独立した操作ロジック（Tk不要、headlessでテストできる） |
| `examples/adjacent_span_gui.py` | Tkinter＋PyMuPDFのGUI本体 |
| `evaluations/real_japanese/osaka_adjacent_gui.py` | 実PDF評価（`--mode session` / `--mode gui`） |
| `tests/test_adjacent_gui_session.py` | 操作ロジックのheadlessテスト（10件） |

**`examples/adjacent_span_session.py`**

- **座標変換**：画面pixel ÷ zoom ＝ PDF point（左上原点）。回転したページと、cropboxがmediaboxと異なるページは拒否する
- **glyphの特定**：正式な選択観測（`inspect_selection_source`）の `source_order` と `bbox` を使う。点を含むglyphが0個、または2個以上（カーニングで重なる箱の隙間、同じ位置に重ねて描かれた複製glyph）の場合は拒否し、前回の選択も消す
- **選択**：押した点と離した点のglyphの間にある、content順のglyph列を `make_selection` にする（原本SHA・ページ観測SHAに束縛される）。文字列検索による選択は行わない
- **提案・承認・保存**：既存の3つのAPIをそのまま呼ぶ
  - 保存前に、出力PDF・sidecar・元PDFが互いに別のファイルであること、出力先が既存でないこと、元PDFがディスク上で変わっていないことを確認する
  - 保存後は `open_adjacent_span` で再openした結果を返す
- **無効化**：選択や置換文字列が変わると、世代番号を進めて提案・承認・保存結果を破棄する。worker threadの処理中に入力が変わった場合、その結果は採用しない
- **表示用データ**（`review`）：提案から、表示用の値と、ページに重ねる図形（先行・後続・followerの矩形、改行窓、カラム端の裏付け行末）だけを取り出す

**`examples/adjacent_span_gui.py`**

- ページを1.5倍で描画し、ドラッグで選択する。選択したglyphをハイライトし、選択文字列とglyph番号を表示する
- 「編集案を確認」：提案の内容を表示し、矩形と裏付けになった行末をページ上に色付きの枠で重ねる。拒否された場合は、理由だけを表示する
- 境界候補が複数ある場合はラジオボタンで選ぶ。「この内容で承認」→「PDFを保存」
- 提案・承認・保存はworker threadで実行する。処理中は全ボタン・入力欄・選択操作を無効にし、「処理中」を表示する（多重実行の防止）
- 保存後は生成PDFを表示し、「元PDFを表示／生成PDFを表示」で切り替えられる。再openの結果、PDF・sidecarのSHAを表示する

## 操作フロー（実PDFで確認したもの）

1. `python -m examples.adjacent_span_gui evaluations/realpdf/corpus/word_osaka_guideline.pdf`
2. 「において」の先頭glyphから末尾glyphまでドラッグする → 「「において」（glyph 149〜152）」と表示される
3. 「におきまして」を入力し、「編集案を確認」を押す。表示された内容は次のとおり（`2-proposal.png`）
   - 編集対象：MCID28（構造要素62）、後続owner：MCID29（63）
   - 境界33、再配置「に送」、follower MCID30 を+21.12pt移動
   - 移動0・1文字は不採用（理由も表示）
   - フォント：xref 5 MS-Gothic（program SHA）
   - カラム右端524.62pt：改行窓 522.91〜533.47、裏付けは「号」524.14pt・「日」524.62pt
   - 3つの矩形の座標と確認事項
4. 「この内容で承認」→「PDFを保存」→ 生成PDFが表示され、「保存しました。再openの結果: restored」（`3-saved.png`）

## テスト

| 実行 | 結果 |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_adjacent_gui_session.py tests/test_adjacent_intent.py`（Python 3.13） | **33 passed（33.19秒）** |
| 同じ `tests/test_adjacent_gui_session.py` を、GUI用のPython 3.12環境で | **10 passed（13.66秒）** |

`test_adjacent_gui_session.py` の内容：

- 全glyphについて、画面座標 → PDF座標 → glyph IDの往復が一致する
- ドラッグ選択の表示文字列が、selectionのglyphと一致し、原本SHAに束縛される。逆方向のドラッグでも同じ選択になる
- 箱の境界上の点、同じ位置に重ねて描かれた複製glyph、空白部分は拒否し、選択を消す
- ownerをまたぐ選択は、エンジンが拒否する
- 手順の順番違反を拒否する：選択前の提案、置換文字列なし、提案前の承認、承認前の保存。承認後に入力を変えると承認が無効化され、保存できない。いずれも出力は生成されない
- REFUSED（容量不足）の場合、出力を生成しない
- 改ざんした提案（矩形の拡大）、改ざんした承認（境界の変更）、提案後にディスク上で差し替えられた元PDFは、いずれも拒否し、出力を生成しない
- worker threadの処理中に入力が変わった場合、結果を破棄する
- 正常系：提案（owner・follower・候補・カラム根拠・重ね描き図形）→ 承認 → 既存ファイルへの保存を拒否 → 保存 → 再open `restored`。直接API経由の出力と、PDF・sidecarのSHAが一致する
- 回転したページを拒否する

`pdfeditor/` に変更はないため、その他のproductionテストは実行していない（CLAUDE.mdの方針）。full suiteも未実行。

## 実PDF評価

対象は `evaluations/realpdf/corpus/word_osaka_guideline.pdf`（SHA `9070ff5c…145fe`、評価前後で不変）、編集は「において」→「におきまして」。比較対象は、PR #81の評価出力 `tmp/osaka-adjacent-intent-quality/` である。

| 実行 | 出力PDF SHA-256 | sidecar SHA-256 | PR #81との一致 | 再open | 提案 / 承認 / 保存（秒） |
| --- | --- | --- | --- | --- | --- |
| `--mode session`（Tkなし、Python 3.13） | `79840e33…98c2` | `715b3306…9e5f` | PDF・sidecarとも一致 | restored | 6.90 / 6.69 / 17.31 |
| `--mode gui`（Xvfb、Python 3.12＋Tk 8.6） | `79840e33…98c2` | `715b3306…9e5f` | PDF・sidecarとも一致 | restored | 7.93 / 8.11 / 19.55 |
| `--mode gui`、拒否動作つき | `79840e33…98c2` | `715b3306…9e5f` | PDF・sidecarとも一致 | restored | 8.12 / 23.56※ / 19.50 |

※ 承認の時間には、承認を無効化した後の再提案と再承認を含む。

- 完全なSHA：PDF `79840e338c2097e6712166a651feeccd5242344b93721b1acaf22465aeec98c2`、sidecar `715b33062c4673acfebd0998715f9d3c77d81bde57b1167559f9289411a89e5f`
- 編集後のテキスト：Span62「国立感染症研究所におきましてガイドラインが作成されましたので、参考」、Span63「に送付いたします。」、follower +21.12pt
- GUIでの各段階のボタン状態（拒否動作つきの実行で記録）：

  | 段階 | 状態 |
  | --- | --- |
  | 起動直後 | 提案・承認・保存のボタンは無効 |
  | 空白部分をクリック | 「選択できません: no glyph at this point」と表示し、選択なし |
  | 選択・入力後 | 提案ボタンだけが有効 |
  | 提案の処理中 | 全ボタンと入力欄が無効 |
  | 提案後 | 承認ボタンが有効 |
  | 承認後 | 保存ボタンが有効 |
  | 承認後に置換文字列を変更 | 承認・保存のボタンが無効になり、提案・承認が破棄される |
  | 保存後 | 生成PDFを表示し、全操作が無効（「元PDFを表示」で戻れる） |

- スクリーンショット（コミットしない）：`tmp/osaka-adjacent-gui-gui/1-selected.png`・`2-proposal.png`・`3-saved.png`、`tmp/osaka-adjacent-gui-gui-2/` の同名ファイル

## 環境

- headless：Linux、Python 3.13.16、PyMuPDF 1.27.2.3、pypdf 6.10.0
- GUI：この環境のPython 3.13にはtkinterがなかったため、Ubuntuの `python3-tk`（Python 3.12.3用、Tk 8.6）を導入し、Python 3.12の別venv（依存パッケージは `requirements.lock.txt` と同じ版）で実行した。Xvfbの画面は1600×1000×24bit
- 評価に必要な原本・tmpの出力・スクリーンショット・venvはコミットしていない

計測値は [osaka-adjacent-gui-summary.json](osaka-adjacent-gui-summary.json) に記録した。

## 残る問題

- 人が実ディスプレイ上で操作した確認と、ファイル選択ダイアログの確認は未実施（Xvfb上のイベント駆動による確認のみ）
- 処理時間：提案・承認がそれぞれ約7〜8秒、保存が約17〜20秒。処理中は操作できない
- 隣接glyphの箱がカーニングで重なる細い隙間（約0.1pt）をクリックすると、曖昧として拒否される。glyphの内側をクリックする必要がある
- Tkのcanvasには透明度がないため、矩形は枠線で表示し、選択と裏付けの行末だけを網掛けで表示している
- 扱うのは1ページ目だけ
