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

1. **フォントのcoverage**（E・J・K・M）：Wordの埋め込みsubsetには、文書で使った153文字しかない。元programにない文字を描くには、利用者が明示的に承認した別のproviderが必要になる。暗黙のfallbackは今後もしない
2. **複数ownerにまたがる変更**（F）：変わる文字が隣接する2つ以上のownerにまたがる。ownerごとのActualTextへの分配と、ownerをまたぐ再組版の設計が必要で、独立した設計課題である
3. **owner端の削除**（H・N）：端の削除はownerの範囲を変える。後続ownerとの間隔や、空になったownerの扱いの証明が必要
4. **ActualTextのないowner**（G）：Span66のようにActualTextを持たないleaf Spanは、意味の同時更新ができないため対象外
5. **行の途中のownerの長文化**（K。fontを満たした場合）：同じ行の後続owner（空白Span）を移動する契約が、単一Span経路にはない
6. **処理時間**：隣接Span経路では、提案〜保存に約60秒かかる。共通APIで経路判定を再導出したうえで、内側の `adjacent_intent` も自分で再導出するためである。安全性を保ったまま重複を減らすことが次の性能課題
7. **同じownerの再編集**：生成glyphを含むownerを、PDFだけから再提案すると「複数のfont resource」で拒否される。sidecarのfont bindingを提案に渡す接続が必要

## 再評価

```
.venv/bin/python -m evaluations.real_japanese.edit_coverage --root tmp/<new directory>
```

ケースの追加は、IDを増やす形で行い、既存ケースは変更しない。PDF・font・sidecar・rasterはコミットしない。
