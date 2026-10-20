# 編集対象の正規化と編集経路の統合 — 編集対応範囲の評価

記録日：2026-10-20（Asia/Tokyo）。ブランチ `claude/vibrant-hamilton-2mwfzf`。PR #82マージ後のmain `b5081cc` から作業した。

評価ケース `edit-coverage-cases.json` は、評価を実行する前に単独でcommitした（`dda73f7`）。今後のPRも同じファイルで再評価し、結果を比較できる。

## 結論

- **ケースA（Windowsで拒否された操作）：成功**。「つきまして」→「おきまして」は2つのowner（Span59「標記につ」、Span60「きまして」）にまたがる選択だが、変わる文字は「つ→お」だけで、Span59の末尾に収まる。単一Span経路で、元のフォント・構造・周辺描画を保ったまま保存し、再openは `restored` だった。
- **ケースB（PR #81・#82の成功例）：成功を維持**。共通API経由では、変更が「い→きまし」に縮み、元のglyphを3つ多く残す（PR #81の34個に対して37個）。ActualText・構造はPR #81の出力と完全に一致した。従来の `adjacent_intent` API経由の出力は、PR #81とバイト単位で一致したままである。
- **固定14ケース**：成功6、拒否8、監査失敗0。拒否の主な理由は、元の埋め込みフォント（subset）にない文字（4件）、変化が複数ownerにまたがる（1件）、owner端の削除（2件）、ActualTextのないowner（1件）。

## ケースAの技術障壁と対処

| 障壁 | 根拠 | 対処 |
| --- | --- | --- |
| 選択が2つのownerにまたがる | 選択した全glyphを1つのownerに属させようとしていた | **正規化**：前後で一致する書記素を元のまま残し、実際に変わる「つ」だけを編集対象にする。根拠は文字列ではなく、glyph occurrenceとmarked-contentのscope・owner |
| 単一Span編集は内部の編集だけ（`0 < start ≤ end < len`） | owner端への挿入はどちらのownerに属すか曖昧になる、という理由 | owner先頭・末尾の文字の**置換**（`start < end`、置換文字列が空でない）だけを許可した。置換は自ownerの文字の中で完結するので、その曖昧さがない。端への挿入・端の削除・owner全体の削除は、従来どおり拒否する（テストで保証） |
| Wordの字間の詰め（`[<標記に>11<つ>] TJ`、0.116pt）と、Span60「き」の箱との0.008ptの重なり | 置換glyphは公称advanceで並ぶため0.116ptはみ出す。単一Spanの矩形は他ownerの箱に入れない | **元の間隔の保持**：同じ文字数・同じスタイルの置換で、置換glyphと元glyphの公称幅が一致する場合だけ、置換位置の元の間隔（詰めを含む）を使う（`EditUnit.slot`、`ParagraphShaper._slot_advance`）。幅が違えば従来どおり公称advanceを使う。位置・ink・衝突・clipの検証は変えていない |
| 元のglyph範囲（bbox）ぴったりの矩形でも、無編集のレイアウトが収まらない | MuPDFのtrace bboxは単精度で、倍精度のレイアウト幅と約1e-5pt食い違う | 「他ownerの容量を取り込まない」検査に、単精度の誤差分（0.001pt。既存のink containment検査と同じ値）だけの許容を入れた。単一Span経路の矩形は、元のglyph範囲＋0.001ptである |

## 正規化と経路の選択（`pdfeditor/edit_intent.py`）

1. `normalize_edit`
   - 選択は、ページ上で連続した1つのglyph occurrenceであること
   - 前後で一致する書記素を除き、残った変更範囲をglyphとownerに対応させる
   - 純粋な挿入は、直前の選択glyph（選択の先頭なら直後のglyph）の置換として扱う
   - 変わる文字が複数のowner、またはtree-backedでないownerにまたがる場合は、理由を付けて拒否する
2. **単一Span経路**：次の前提を、それぞれ既存APIで証明する
   - leaf Span契約（`propose_single_span`）
   - 編集位置の契約
   - 元のfont program（SHAで固定）とcoverage
   - そのowner自身の範囲での容量（`plan_single_span_edit`）
3. **隣接Span経路**：単一Span経路が**容量不足だけ**を理由に拒否した場合に限り、正規化した選択と置換で `propose_adjacent_intent` を呼ぶ。構造・フォント・位置による拒否は、別経路を試さずにそのまま拒否する。経路を順に試して成功したものを採用する方式ではない
4. 承認（`accept_edit_intent`）と保存（`edit_edit_intent`）は、同じrevisionで正規化と経路判定を再導出し、提案と完全一致した場合だけ、その経路の既存API（`accept_single_span`/`edit_single_span`、`accept_adjacent_intent`/`edit_adjacent_intent`）で実行する。`open_edit_result` は、sidecarのschemaに応じた再openを行う
5. 段落再組版（B2・shared flow）経路は、大阪府PDFでは前提を満たさない。本文のownerがleaf `/Span` で、B2が扱う「leaf `/P` 直下のMCID束」ではないためである。今回は経路に組み込んでいない

GUI（`examples/adjacent_span_session.py`）は共通APIに接続した。選択→提案→承認→保存の流れは変えていない。提案の表示には、実際の変更、経路、選択範囲のowner、経路ごとの矩形を加えた。

## 評価ケースと結果（`edit_coverage.py`、`--root tmp/edit-coverage-2`）

| ID | 種類 | 選択 → 置換 | 選択のowner（MCID） | 正規化後の変更 | 経路 | 結果 | 拒否理由・保存後 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | owner越え選択・同じ文字数 | つきまして → おきまして | 25, 26 | Span59「つ→お」（末尾） | 単一Span | **成功** | 「標記にお」 |
| B | 改行先へ送る長文化 | において → におきまして | 28 | 「い→きまし」 | 隣接Span | **成功** | 「…におきまして…参考」／「に送付いたします。」、follower +21.12pt |
| C | owner内・同じ文字数 | 作成されましたので → 作成されていたので | 28 | 「まし→てい」 | 単一Span | **成功** | 元の間隔を保持 |
| D | owner内・短文化 | 作成されましたので → 作成されたので | 28 | 「まし→（削除）」 | 単一Span | **成功** | 「…作成されたので、参考に送」 |
| E | owner先頭の置換 | 標記 → 本件 | 25 | 「標記→本件」（先頭） | — | 拒否 | 元のfont programに「件」がない（fallbackしない） |
| F | 変化が2ownerにまたがる | につきまして、 → に関して、 | 25, 26, 27 | 「つきま→関」 | — | 拒否 | 変わる文字が2つのownerにまたがる |
| G | ActualTextのないowner | つきまして → おきまして（glyph 182） | 31 | 「つ→お」（先頭） | — | 拒否 | 単一Span契約：ActualTextがない（`unsupported semantic override`） |
| H | owner末尾の削除 | につ → に | 25 | 「つ→（削除）」 | — | 拒否 | 端の削除は未対応 |
| I | 挿入による長文化 | 参考に → 参考までに | 28 | 「考→考まで」 | 隣接Span | **成功** | 「…参考まで」／「に送付いたします。」 |
| J | 段落の最後のownerの長文化 | いたします → させていただきます | 29 | 「いたし→させていただき」 | — | 拒否 | 「せ」がない |
| K | 行の途中のownerの長文化 | 御協力いただき → 御協力をいただき | 23 | 「力→力を」 | — | 拒否 | 「を」がない（fontを満たしても容量の証明が必要） |
| L | 文中・同じ文字数 | 御礼 → お礼 | 23 | 「御→お」 | 単一Span | **成功** | 「…厚くお礼申し上げます。」 |
| M | タイトルの長文化 | 届出の質向上 → 届出の品質向上 | 17 | 「の→の品」 | — | 拒否 | 「品」がない |
| N | owner越え選択・owner末尾の短文化 | 標記につきまして → 標記につき | 25, 26 | Span60「まして→（削除）」（末尾） | — | 拒否 | 端の削除は未対応 |

成功した6件の監査結果は、すべて次のとおりだった。

- 変更したownerのActualText＝描画Unicode、owner単位の期待テキストと一致
- 構造で変化したのは、変更したownerのActualTextだけ
- 他ownerのglyph・code・GID・font・位置・envelopeが完全一致
- 元のfont program・drawingが不変
- followerの意味情報が不変
- MuPDF・Poppler 144dpiで、承認矩形の外側の差分0 pixel
- 再openは `restored`

| ID | 出力PDF SHA-256 | sidecar SHA-256 | 変化した構造 | 差分bbox（144dpi） |
| --- | --- | --- | --- | --- |
| A | `e2a2fbf3bcec518c…d5c3` | `3a97344a73eddae3…55ad` | `59:0` | (228,675)–(246,694) |
| B | `d09e33e15e4519c5…06d7` | `2160b216eace6151…bcb4` | `62:0`, `63:0` | (143,674)–(1046,730) |
| C | `49b9be801d8f6de0…9e06` | `987b26ffb94232ef…86ff` | `62:0` | (837,675)–(877,694) |
| D | `fde1b163ddbfcbbf…a4f9e` | `ed74144e1277853f…23e7` | `62:0` | (837,674)–(1045,694) |
| I | `996d738ca79d7a65…c6f4` | `bae7ea96e5a963e3…3601` | `62:0`, `63:0` | (143,674)–(1045,730) |
| L | `8b15215779246132…240c` | `10887c4e1ae99241…8613` | `56:0` | (731,639)–(751,658) |

完全なSHA、正規化の詳細、経路ごとの拒否理由、provenanceは [edit-coverage-results.json](edit-coverage-results.json) にある。原本SHA `9070ff5c…145fe` は評価前後で不変。

**1回目の実行**（`tmp/edit-coverage`）では、Lが監査失敗になった。原因は評価スクリプトの誤りで、期待テキストを文字列置換で作ったため、編集位置（「御礼」の「御」）ではなく、最初の「御」（「御協力」）を置き換えていた。エンジンの結果は正しく、他の監査項目はすべて通っていた。期待テキストを正規化した編集位置から組み立てる形に直し、全ケースを新しいディレクトリで再評価した結果が上の表である。

## ケースBの互換性

| 比較 | 結果 |
| --- | --- |
| `propose_adjacent_intent` → 承認 → 保存（従来API） | SHA `79840e33…98c2`。PR #81・#82の出力と**一致** |
| 共通API（正規化あり）とPR #81の出力 | ActualText（Span62・Span63）と構造グラフは完全一致。元の `/F1` glyphは34→37個。ページ差分はMuPDF 2,386 / Poppler 2,259 pixel |

共通APIの差分bbox (543,674)–(1046,694) は、Span62の承認矩形の内側に収まる。「に」「お」「て」を元のglyphのまま、Wordの詰め付きで残すため、同じ行の後続の文字がPR #81の出力より詰まった位置になることによる差である。編集領域の外側の差分は0 pixel。

## GUIでの確認（Xvfb）

`osaka_adjacent_gui.py --mode gui` に `--anchor/--selected/--replacement` を加え、ケースAとケースBをGUI操作で実行した。

| ケース | 経路 | 再open | 出力 |
| --- | --- | --- | --- |
| A | 単一Span | restored | PDF・sidecarのSHA-256が評価基盤の出力と一致 |
| B | 隣接Span | restored | PDF・sidecarのSHA-256が評価基盤の出力と一致 |

ケースAの提案画面には、「実際の変更: 「つ」→「お」」「選択範囲のowner: MCID25、MCID26」「経路: 単一Span」と、Span59の矩形が表示された。人による実ディスプレイでの操作は未実施である。

## テスト

`.venv/bin/python -m pytest -q`（Python 3.13）で、次の12ファイルを実行した：**230 passed（255.69秒）**。

| ファイル | 内容 |
| --- | --- |
| `tests/test_edit_intent.py`（新規10件） | 正規化（前後の保持、owner越え選択の単一owner化、挿入の付け先、変化が2ownerにまたがる場合の拒否、同一文字列・非連続の拒否）／Wordの詰めを再現したfixtureでのowner末尾の置換（元の位置に置かれ、後続は動かない）／`keep_spacing` を外すと容量不足になる対照実験／容量不足時だけ隣接経路へ進むこと／フォント拒否では別経路を試さないこと／改ざん・経路の書き換え・stale・未承認の拒否 |
| `tests/test_single_span_actualtext.py`（8件追加） | owner先頭・末尾・先頭2文字の置換の成功と、端の挿入・端の削除・owner全体の削除が拒否されたままであること |
| `tests/test_adjacent_gui_session.py`（1件追加、既存を新APIへ） | owner越えのドラッグで単一Span経路になり、保存・再openまで進むこと |
| `test_adjacent_span`・`test_adjacent_intent`（span系） | 回帰 |
| `test_attributed`・`test_attributed_boundaries`・`test_retained_source_positions`・`test_spacing`・`test_witnessed_source_line_positions` | プランナーのadvance計算の回帰 |
| `test_tagged_page_proposal`（B2）・`test_transaction` | 回帰 |

`attributed.py` を直接importするテストは41ファイルある。今回の変更は `keep_spacing` が付いたときだけ働き、このキーを付けるのは `span_edit` だけなので、上の範囲に絞った。full suiteは実行していない。

## 次の障壁

1. **フォントのcoverage**（E・J・K・M）：Wordの埋め込みsubsetには、文書で使った153文字しかない。元programにない文字を描くには、利用者が明示的に承認した別のproviderが必要になる。暗黙のfallbackは今後もしない（→[代替フォントによる不足文字の解消](#代替フォントによる不足文字の解消)でEは解消。J・K・Mは容量・構造が残る）
2. **複数ownerにまたがる変更**（F）：変わる文字が隣接する2つ以上のownerにまたがる。ownerごとのActualTextへの分配と、ownerをまたぐ再組版の設計が必要で、独立した設計課題である
3. **owner端の削除**（H・N）：端の削除はownerの範囲を変える。後続ownerとの間隔や、空になったownerの扱いの証明が必要
4. **ActualTextのないowner**（G）：Span66のようにActualTextを持たないleaf Spanは、意味の同時更新ができないため対象外
5. **行の途中のownerの長文化**（K。fontを満たした場合）：同じ行の後続owner（空白Span）を移動する契約が、単一Span経路にはない
6. **処理時間**：隣接Span経路では、提案〜保存に約60秒かかる。共通APIで経路判定を再導出したうえで、内側の `adjacent_intent` も自分で再導出するためである。安全性を保ったまま重複を減らすことが次の性能課題
7. **同じownerの再編集**：生成glyphを含むownerを、PDFだけから再提案すると「複数のfont resource」で拒否される。sidecarのfont bindingを提案に渡す接続が必要

## 代替フォントによる不足文字の解消

記録日：2026-10-20（Asia/Tokyo）。PR #83マージ後のmain `146cf66` から作業した。評価ケース `edit-coverage-cases.json` は変更していない。結果は `edit-coverage-results.json` の `substitute_evaluation` に追記した。

### 結論

- **ケースE（標記→本件、「件」が元フォントにない）：成功**。利用者が代替フォント（BIZ UDゴシック）を明示的に承認すると、「件」だけを代替フォントで、「本」は元のMSゴシックのprogramで描き、保存・再open（`restored`）・再編集まで完了した。
- **代替フォントなしの結果は変わらない**。14件すべてで、結果・拒否理由・出力PDFとsidecarのSHA-256がPR #83の記録と一致した（`tmp/edit-coverage-3`）。
- **代替フォントあり**：成功7（+E）、拒否7、監査失敗0（`tmp/edit-coverage-substitute`）。J・K・Mは代替フォントで不足文字は解消するが、単一Spanの容量不足で拒否される。代替フォントを隣接Span経路では使わないため、そこで止まる。
- 同じ位置に同じ文字数で置き換えたEでは、元の字間（WordのTJの詰めを含む）をそのまま使った。縮小・矩形の拡張はしていない。

### しくみ（`span_edit.py`・`edit_intent.py`）

| 要件 | 実装 |
| --- | --- |
| 暗黙のfallbackをしない | 代替フォントは `substitute=dict(path, sha256, font_index, relation='substituted')` で明示的に渡したときだけ使う。渡さなければ、従来どおり「元のprogramに◯がない」で拒否し、不足文字を `routes['single-span']['missing']` に示す |
| 不足文字だけに使う | 新しい文字ごとに元のprogramで整形し、1つの実glyphにならない文字だけを代替フォントのrunにする。元のprogramにある文字は元のprogram。元のprogramですべて描ける場合に代替フォントを渡すと拒否する |
| 代替フォントの資格 | 既存の `page_proposal._coverage_candidates`（埋め込み許可、実際のHarfBuzz整形、可視glyph）と `ShapedFont`（TrueType輪郭、`fsType`、face番号）を再利用する。加えて、1文字1glyph（run内と単独で同じGID）、cmapが同じglyphを指すこと、可視輪郭、正の字幅を検査する。SHA-256が承認値と違う、元のprogramそのもの、face番号が範囲外、のいずれも拒否する |
| 元と同一視しない | 提案と承認に `relation='substituted'`、SHA-256、face番号、family、適用文字、注意書き（見た目が異なる場合がある）を記録する。page proposalの `relation='substituted'` と同じ語を使う |
| 変更しない文字 | 既存の単一Span経路のまま：元のglyphはcode・GID・resource・位置を保持し、元のfont programは変更しない。新しいglyphだけが新しいresource（元programのsubsetと代替フォントのsubsetは別resource）になる |
| 字間 | 字間を元のまま保つ `keep_spacing` を、runが複数ある置換にも広げた（`attributed.apply_edits`）。条件は従来と同じ：同じ文字数・同じスタイルで、新旧glyphの公称幅が一致すること。幅が違えば公称advanceで組む（テストで確認） |
| sidecar | `font_binding.generated`（元programのsubset）とは別に、`font_binding.substitutes` に代替resourceを記録する。文字ごとに、GID・字幅・unitsPerEm・輪郭のdigestを持つ |
| 再open | 再openのたびに、描かれている代替glyphを埋め込みsubsetから読み、Unicode・GID・輪郭・字幅・PDF上の幅を記録と照合する。subsetのSHA-256、`relation`、元programとの区別（代替resourceを `generated` として記録し直すと拒否）も検査する |
| 再編集 | 2回目以降の編集で不足文字があれば、代替フォントの再承認が必要（埋め込み済みの代替resourceを新しい文字に流用しない）。不足文字のない再編集では代替フォントを受け取らず、既存の代替glyphは記録のまま検証する |
| 経路 | 代替フォントは単一Span経路だけで使う。単一Spanが容量不足で拒否した場合、代替フォント付きの提案は隣接Span経路へ進まず、その理由を示して拒否する（隣接Span契約は両ownerで1つの元programを固定するため） |

### 使った代替フォント

| 項目 | 値 |
| --- | --- |
| フォント | BIZ UDゴシック Regular（`BIZUDGothic-Regular.ttf`、[google/fonts](https://github.com/google/fonts/tree/main/ofl/bizudgothic)） |
| ライセンス | SIL Open Font License 1.1。リポジトリにはコミットしていない |
| SHA-256 | `709fcd41e3209fb765da750472f55ccdf925653e9fa7e1eb007cb65c8f749c75`（face 0） |
| 埋め込み許可 | OS/2 `fsType` = 0（制限なし） |
| 不足文字の字幅 | 件・せ・を・品とも2048/2048 em（元のMSゴシックの全角と同じ1000/1000） |

本文がMSゴシックなので、ゴシック体を選んだ。BIZ UD明朝（SHA-256 `468ee6d9…`、以前の沖縄県PDFの評価で使ったもの）でも、Eは保存・再open（`restored`）まで成功した（診断。固定評価には含めていない）。

### 代替フォントなし／ありの比較

| ID | 代替なし（PR #83と同じ） | 代替あり | 不足文字 | 代替ありでの理由・保存後 |
| --- | --- | --- | --- | --- |
| A | 成功 | 成功 | — | 同じ出力（SHA一致） |
| B | 成功 | 成功 | — | 同じ出力（SHA一致） |
| C | 成功 | 成功 | — | 同じ出力（SHA一致） |
| D | 成功 | 成功 | — | 同じ出力（SHA一致） |
| **E** | 拒否（「件」がない） | **成功** | 件 | 「本件につ」。「本」は元program、「件」は代替フォント |
| F | 拒否 | 拒否 | — | 理由同じ（2ownerにまたがる） |
| G | 拒否 | 拒否 | — | 理由同じ（ActualTextなし） |
| H | 拒否 | 拒否 | — | 理由同じ（端の削除） |
| I | 成功 | 成功 | — | 同じ出力（SHA一致） |
| J | 拒否（「せ」がない） | 拒否 | せ | 単一Spanの容量不足。代替フォント付きでは隣接Span経路へ進まない |
| K | 拒否（「を」がない） | 拒否 | を | 同上 |
| L | 成功 | 成功 | — | 同じ出力（SHA一致） |
| M | 拒否（「品」がない） | 拒否 | 品 | 同上 |
| N | 拒否 | 拒否 | — | 理由同じ（端の削除） |

代替フォントは、不足文字で拒否されたケース（E・J・K・M）だけに渡した（GUIで利用者が選ぶのと同じ条件）。それ以外のケースは代替フォントなしと同じ処理で、出力もSHA-256で一致した。

### ケースEの監査

| 検査 | 結果 |
| --- | --- |
| ActualTextと描画文字 | Span59のActualTextも描画も「本件につ」 |
| 構造 | 変わった構造オブジェクトはSpan59（`59:0`）のActualTextだけ。StructureTree・ParentTree・MCID・ownerは不変 |
| 変更したglyph | 「本」：`/PRF1`（元programのsubset、`generated`）GID 7055。「件」：`/PRF2`（BIZ UDゴシックのsubset、`substitutes`）GID 2825。記録したGID・輪郭digest・字幅（2048/2048）と一致 |
| 変更しないglyph | 「につ」はcode・GID・font objectが同じ（`unchanged_glyph_identity`）。位置の差は最大7.6e-6pt（traceの単精度の丸め。評価後に元PDFと出力のtraceを直接比較して確認） |
| 新しいglyphの位置 | 「本」「件」は元の「標」「記」と同じ原点（81.504, 346.130）・（92.064, 346.130） |
| 元のフォント | ページ上の元のfont program（F1〜F4）のSHA-256は不変 |
| 他owner・図形 | すべて不変 |
| 矩形外の画素（144dpi） | MuPDF 0、Poppler 0。変化は2文字のセル内だけ |
| 再open | `restored`（`open_edit_result`、contract `single-span`） |
| 再編集 | 保存したPDFとsidecarから、「本件」を元のprogramで「標記」に戻した。再openは `restored`、ActualTextと描画は「標記につ」、ページ全体の画素は元PDFと一致（MuPDF・Popplerとも差0） |

### J・K・Mの残る障壁

代替フォントを承認すると、フォントの拒否はなくなり、単一Spanの容量（「Reflowed text exceeds the available vertical space」）で拒否される。代替フォントを隣接Span経路で使わないことが、そこで止まる直接の理由である。ただし、不足文字を元programにある文字に置き換えた診断（固定ケースではない）でも、隣接Span経路は構造の理由で拒否した。

| ID | 診断の置換 | 隣接Span経路の拒否理由 |
| --- | --- | --- |
| J | させて→**さし**ていただきます | 先行ownerの行末が句読点・空白なので、カラム端の根拠にならない（矩形の明示が必要） |
| K | 御協力**の**いただき | 同上 |
| M | 届出の**出**質向上 | 先行ownerが「1行で折り返し、次の行が後続owner」の形でない（タイトル） |

フォントだけを理由にJ・K・Mが拒否されているわけではない。隣接Span経路で代替フォントを使えるようにしても、これらは成功しない。したがって今回は隣接Span経路を広げていない。

### GUI（Xvfb、`tmp/edit-coverage-gui-E-substitute`）

`osaka_adjacent_gui.py --mode gui --selected 標記 --replacement 本件 --anchor 132 --substitute <BIZUDGothic-Regular.ttf>` で、Tkアプリを操作した。

1. 「標記」をドラッグし、「本件」を入力して「編集案を確認」：拒否。「元のフォントに「件」がありません。自動で別のフォントは使いません。」と表示され、「代替フォントを選ぶ…」が押せるようになった（それまでは押せない）
2. 「代替フォントを選ぶ…」でフォントファイルを選択（テストではファイル選択ダイアログを置き換えた）：「代替: BIZUDGothic-Regular.ttf（「件」だけ）」
3. 「編集案を確認」：元フォント（xref 5 MS-Gothic、SHA-256）、元フォントにない文字「件」、代替フォント（ファイル名・face・SHA-256・relation=substituted）、代替フォントで描く文字「件」（1 glyph）、見た目が異なる場合がある旨が表示された
4. 承認後に置換文字列を変えると、承認と代替フォントの選択がともに取り消された。戻して提案→フォント選択→承認→保存
5. 保存：再open `restored`。出力PDFとsidecarは、評価スクリプトのケースEとSHA-256で一致した

人による実ディスプレイでの操作、Windowsでの確認は未実施である。

### テスト（今回）

| ファイル | 内容 |
| --- | --- |
| `tests/test_single_span_substitute.py`（新規26件） | 不足文字だけが代替フォントになること（全部不足・混在の2通り）／元のglyphのcode・GID・resource不変／ActualText／同じ字幅で元の位置を保つこと／字幅が違えば公称advance／no-op・不足文字なしの再編集・代替glyphの置換・再承認しての追加（2つ目の代替resource）／拒否：未承認、relation違い、改ざん（bytes変更）、SHA違い、埋め込み禁止（`fsType`=2）、glyphがない、元programを代替として渡す、不要な代替、余分なキー、face番号、容量（いずれも出力なし、理由を照合）／再open時の拒否：GID・輪郭・字幅・Unicode・relationの改ざん、代替resourceを `generated` として記録、記録の削除／埋め込みsubsetの輪郭を書き換えたPDF |
| `tests/test_edit_intent.py`（3件追加） | 不足文字の報告と明示の代替での成功（元の位置、後続ownerは不動）／不要な代替は拒否、容量不足では隣接経路へ進まない／提案の代替情報の改ざんと、承認後のフォントbytes変更の拒否 |
| `tests/test_adjacent_gui_session.py`（1件追加） | 不足文字の報告→フォント選択→提案の表示→承認→保存・再open。不足文字がないときのフォント選択の拒否、置換文字列の変更で選択が取り消されること |

上記に、`span_edit` をimportする既存テスト（`test_single_span_actualtext`・`test_adjacent_span`・`test_adjacent_intent`）、`test_attributed`・`test_attributed_boundaries`・`test_story_styles`（runsを使う編集）・`test_generated_fonts` を加えた10ファイルを `-q` で実行し、**203 passed（263.76秒）**だった。評価スクリプトと並行して動かしたため、時間は単独実行より長い。その後、GUIの表示ラベル1行を変えて`test_adjacent_gui_session.py` を再実行し、**14 passed（47.14秒）**だった。さらに `edit_intent._single_route` の重複したフォント解析をまとめた後、`test_edit_intent.py` を再実行し、**13 passed（20.78秒）**だった（評価とGUIの実行はこの整理の前。提案に記録する値は変わらない）。

`attributed.py` の変更は、`runs` と `keep_spacing` の両方が付いた編集だけに働く。`keep_spacing` を付けるのは `span_edit` だけなので、`attributed` をimportする41ファイルすべては実行していない。full suiteは実行していない。

### 未実施・既知の点

- full suite、Windows・人による操作、外部の別PDFでの評価は未実施
- J・K・M：上記のとおり、単一Spanの容量と隣接Span経路の構造条件が残る障壁
- 共通APIからの再編集：代替resourceを含むownerをPDFだけから再提案すると、従来の生成glyphと同じく「複数のfont resource」で拒否される。今回の再編集はsidecarの状態から `edit_single_span` を直接呼んで確認した（前節の障壁7と同じ）
- 単一Span経路の容量不足の理由に「provider qualification failed:」が前置される（PR #83以前からの表示。拒否の判定には影響しない。代替なしの結果を変えないため、今回は直していない）

## 再評価

```
.venv/bin/python -m evaluations.real_japanese.edit_coverage --root tmp/<new directory>
.venv/bin/python -m evaluations.real_japanese.edit_coverage --root tmp/<new directory> --substitute <font.ttf>
```

`--substitute` は、不足文字で拒否されたケースだけを、そのフォントを `relation='substituted'` で承認して提案し直す。保存できたケースは、sidecarから元の文字列へ戻す再編集も行う。

ケースの追加は、IDを増やす形で行い、既存ケースは変更しない。PDF・font・sidecar・rasterはコミットしない。
