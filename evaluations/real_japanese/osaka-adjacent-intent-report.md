# 大阪府Word由来PDF：隣接Span協調編集の自動提案 — PASS

記録日：2026-10-20（Asia/Tokyo）。ブランチ `claude/vibrant-hamilton-2mwfzf`。PR #79マージ後のmain
`4866791e54b66c79309ef0336ed86536c01a3a73` から作り直したブランチで作業した。前回の手動協調編集の評価
（[osaka-adjacent-span-report.md](osaka-adjacent-span-report.md)）と、その出力（`tmp/osaka-adjacent-span/`）は保全し、再実行していない。

## 結論

**PASS — 利用者の入力に相当する「選択したglyph occurrence」と「置換後の文字列」だけから、次の値をすべて導出した。**

- 2つのowner（Span62とSpan63）
- 新しいowner境界（33）
- 空白follower（Span64）とその移動量（+21.12pt）
- 編集矩形
- 固定provider（元の埋め込みfont program）

導出した提案に対する承認を評価コードで模擬し、編集を保存した。再openで `restored`、no-op、再openで `restored` まで確認した。

自動提案から作った編集PDFは、前回の手動協調編集の出力と **SHA-256まで同一**（`79840e33…98c2`）だった。no-opの出力も同一（`16196bfd…8386`）。成功保存は2回（編集・no-op）。

この編集は評価用の成果物であり、大阪府の公式な訂正版ではない。

## 入力（利用者が指定するもの）

| 入力 | 値 |
| --- | --- |
| PDF | `evaluations/realpdf/corpus/word_osaka_guideline.pdf`（SHA `9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe`、評価前後で不変） |
| selection | 1ページ目のglyph occurrence `[149, 150, 151, 152]`（`make_selection`。source SHAとページ観測SHAに束縛される） |
| 選択文字列 | `において` |
| 置換後文字列 | `におきまして` |

UIでは、ドラッグ操作から具体的なglyph IDを得る想定である。評価コードは、ページのglyph列で「において」の描画出現が **1箇所だけ** であることを確認してから、そのoccurrenceをselectionにした（2箇所以上ある場合は、ポインタ位置が必要として停止する）。

APIは文字列検索でoccurrenceを決めない。2つの出現をまとめて渡したselectionは、「連続した1つのoccurrenceではない」として拒否する。

owner番号・MCID・境界33・矩形座標・font resourceは、提案の入力として渡していない。

## 自動検出した値と根拠

| 項目 | 導出結果 | 根拠 |
| --- | --- | --- |
| 先行owner | Span62 / MCID28（P58の4番目の子） | 選択glyphのsource ownership（content streamのscope）→ MCID → ParentTree・backlink（tree-backed）。selectionはこのowner内で連続し、先頭glyphではない |
| 後続owner | Span63 / MCID29 | Span62の親P58の `/K` 順序で次の兄弟。marked-content itemは1つだけ |
| follower | Span64 / MCID30（空白、`/Lang`） | `/K` でさらに次の兄弟で、空白だけのleaf。既存のfollower契約（独立owner、Tmの平行移動のみ、clip・行・content順序）で証明。承認対象に含める |
| 元の境界 → 提案境界 | 33 → 33（「に送」の2文字をSpan63へ） | 後述の候補探索 |
| font | `/F1`（xref5）、MS-Gothic、program SHA `5e5ded28…eddf`、face 0 | 両ownerが描画に使うfont resourceは1つだけ。providerは元の埋め込みprogramをtmpに書き出し、SHAで固定。置換文字のcmap coverageを事前に確認。fallbackはしない |

隣接・親子・content順序・行の連続・ActualText＝描画Unicode・clip・graphics stateの最終的な証明は、既存の `propose_adjacent_span`（`adjacent-span-actualtext-1` 契約）が行う。所有権ガードは変更していない。

## 編集矩形の導出

| 矩形 | x0 | y0 | x1 | y1 |
| --- | --- | --- | --- | --- |
| Span62 | 176.060 | 333.339 | 524.620 | 351.339 |
| Span63 | 70.944 | 351.339 | 524.620 | 369.339 |
| Span64 follower | 144.500 | 351.339 | 524.620 | 369.339 |

- **右端（本文カラム端）524.62pt**：ページ上の可視glyphの最大右端（右寄せの「日」。0.5pt以内に「号」もあり、支持glyphは2つ）。前回の手動評価で使った525ptは使っていない。ページの右余白（595pt）までは使わない
- **改行との整合確認**：Span62の行末522.914pt＋次の文字「付」のadvance 10.56pt＝533.47pt > 524.62pt。Wordが「付」を次の行へ送った事実と矛盾しない。矛盾する場合は、明示的な矩形が必要として拒否する
- **左端**：各ownerの先頭glyph（Span61「、」のbox 0.12ptとの既存の重なりは、元のbbox内に限り許す既存規則の範囲）
- **上下**：他の行のglyphとの間隔の半分（各行±3.72pt）。2つの矩形は y=351.339 で接するが、重ならない
- **同じ行の右側にある他ownerのglyph**：あればその手前で止める（今回は該当なし）
- drawingと交わる場合は拒否する。clip矩形でも切り詰める

**利用者の確認が必要な部分**（提案の `requires_confirmation`）：

- owner対とfollower
- 導出した矩形（カラム端と行帯）
- 境界と移動する文字
- 固定したfont program

推定値は提案（`status: proposed`）として返すだけで、宣言された事実としては扱わない。

## 境界候補の探索

移動文字数を0から順に、既存の `plan_adjacent_span_edit`（読み取り専用）で判定した。上限は16文字で、選択より後ろにあるSpan62の文字数も上限になる。自然な分割で適格な候補が見つかった時点で探索を止める。

| 移動 | Span62 / Span63 | 自然な分割 | 判定 |
| --- | --- | --- | --- |
| 0 | `…参考に送` / `付いたします。` | ○ | 拒否：1行に収まらない |
| 1 | `…参考に` / `送付いたします。` | ○ | 拒否：1行に収まらない |
| 2 | `…参考` / `に送付いたします。` | ○ | **適格**：幅 347.20 / 94.81pt（上限 348.56 / 453.68）、follower +21.12pt |

- 「自然な分割」の根拠：境界がUAX #14の改行可能位置で、かつ日本語の行頭・行末禁則（`pdfeditor.layout` の既存規則）に反しないこと
- 優先順位：適格な候補のうち移動文字数が最少のもの（元の行構造に最も近い）
- 最少の候補が自然な分割でない場合（例：英単語の途中）：最少の候補と自然な分割の候補の両方を `ambiguous` として返し、明示選択がない限り承認しない
- 自然な分割の候補が1つもない場合：拒否する
- 今回は移動2文字の候補が、最少かつ自然な分割なので一意に決まった

## 提案と承認の区別

1. `propose_adjacent_intent`：PDFを書き換えない。内部の適格性判定にだけ読み取り専用の確認済み状態を使い、返す隣接Span提案は `status: proposed` のまま。提案SHA `bde94fe2…5b4c`
2. 評価コード内の承認の模擬：次の8条件がすべて真のときだけ `accept_adjacent_intent` を呼ぶ
   - 状態が `proposed` であること
   - 選んだ候補が適格かつ自然な分割であること
   - 順方向の移動だけであること
   - followerが契約で証明されていること
   - 矩形がカラム端以内であること
   - カラム端が改行と整合すること
   - providerが元のprogramであること
   - 文字数が増えること
3. `accept_adjacent_intent`：同じselectionと置換文字列から提案を再導出し、完全一致した場合だけ、既存の `accept_adjacent_span` で承認する。承認SHA `91df02ee…67b7`
4. `edit_adjacent_intent`：承認オブジェクトがなければ実行しない。保存の前に次を再確認してから、既存の `edit_adjacent_span` を呼ぶ
   - revisionのSHA
   - 提案の再導出結果
   - 承認内容と提案の一致
   - 読み取り専用planの結果が、承認した候補と一致すること

## 実PDFの保存・復元と監査

- 親P58（読み順 `[59…64]` は不変）：50→52文字
  - 編集前：`標記につきまして、国立感染症研究所においてガイドラインが作成されましたので、参考に送付いたします。 `
  - 編集後：`標記につきまして、国立感染症研究所におきましてガイドラインが作成されましたので、参考に送付いたします。 `
- ownerごとのActualText＝描画Unicode
  - Span62：`国立感染症研究所におきましてガイドラインが作成されましたので、参考`
  - Span63：`に送付いたします。`
- 構造グラフで変化したのは、`62:0` と `63:0` のActualTextだけ。2件を除いた構造・MCID・ParentTree・子の順序は一致
- 他の94 scopeは、glyph・code・GID・font・位置・ActualText・envelopeが一致。drawing 4件、元のfont program 4つ、3つのenvelopeの外側のbyteも一致
- follower：owner・association・BDCプロパティ（`/Lang`）・ActualText・glyph identityが不変。変化はTmの1オペランド（+21.12pt）だけ
- 両ownerのenvelopeは、元のclip（`q … re W* n`）から `BT` までを保持
- glyph provenance：残存34、再配置2（「に」「送」：Span62の `/F1` → Span63の `/PRF2`、GID 2143・13286は元と一致）、新規6（「におきまして」：`/PRF1`）
  - 新規・再配置の8 glyphは、保存PDFのsubsetについて、CID→GID・輪郭・hmtx・PDF幅を固定providerと独立に照合した
- 再openはどちらも `restored`。no-opはcontent streamがbyte一致し、全観測値も一致

### MuPDF・Poppler 144dpi

| 比較 | MuPDF | Poppler |
| --- | --- | --- |
| 原本 vs 編集後：導出・承認した3矩形の外側 | 0 pixel | 0 pixel |
| 編集後 vs no-op：ページ全体 | 0 pixel | 0 pixel |

マスクは導出した矩形と交わるraster cellだけで、余白・許容誤差の追加はしていない。差分のbboxは (143,674)–(1046,730)（Popplerは x0=142）。

### SHA-256と実行時間

| 出力 | SHA-256 |
| --- | --- |
| `tmp/osaka-adjacent-intent/edited.pdf` | `79840e338c2097e6712166a651feeccd5242344b93721b1acaf22465aeec98c2`（前回の手動出力と同一） |
| `tmp/osaka-adjacent-intent/edited.json` | `715b33062c4673acfebd0998715f9d3c77d81bde57b1167559f9289411a89e5f` |
| `tmp/osaka-adjacent-intent/noop.pdf` | `16196bfd015d10944dab618c2bf1d4f2e9bc2542e3e10346e09812214d078386`（前回と同一） |
| `tmp/osaka-adjacent-intent/noop.json` | `56259532ddea37e79845eeb1c47db49d3ed95cf2269d62a3ce05f5bf5ce37722` |

sidecarは、承認時のregion（導出値）を記録するため、前回のものとはSHAが異なる。

実行時間は合計49.12秒（実行全体では49.96秒）。

| 処理 | 時間 |
| --- | --- |
| 提案 | 8.19秒 |
| 承認（提案の再導出を含む） | 9.59秒 |
| 編集（提案の再導出・plan再確認・保存） | 17.77秒 |
| 監査 | 5.65秒 |
| no-opと監査 | 7.91秒 |

pypdfのfull-load時に、前回と同じ未到達のobject ID（4・28・33・167）の警告が出た。

環境：Linux、Python 3.13.16、PyMuPDF 1.27.2.3、pypdf 6.10.0、Poppler pdftoppm 24.02.0。

## production変更

- `pdfeditor/adjacent_intent.py`（新規）：`propose_adjacent_intent` / `accept_adjacent_intent` / `edit_adjacent_intent`
  - 担う範囲：ownerとfollowerの発見、矩形・providerの導出、境界候補の探索、提案と承認の分離
  - 協調編集そのものは、既存の `propose_adjacent_span`・`accept_adjacent_span`・`plan_adjacent_span_edit`・`edit_adjacent_span` に委ねる
  - 既存moduleの変更はない

## Focused tests

- `.venv/bin/python -m pytest -q tests/test_adjacent_intent.py`：**16 passed（17.46秒）**
  - 正常系：
    - selectionから先行owner、構造から後続ownerを特定する
    - 適格な境界候補とfollowerを自動で見つける
    - 矩形・fontの根拠を提案に含める
    - 明示承認後に編集し、ActualTextが更新される
    - 保存・再open・no-op
    - 既存の手動協調編集と、content stream・ActualText・描画が一致する
    - 複数の候補があるときは `ambiguous` として、明示選択を求める
  - 拒否系：
    - selection：同じ表示文字列の2つの出現をまとめた指定、連続しない指定、ownerをまたぐ指定、owner先頭からの選択
    - 構造：後続ownerがない、ParentTree・backlink・ActualTextの不正
    - 配置：近くにある構造上無関係なglyph（ペアにもせず、上書きもしない）、followerの不適格（移動せず、容量不足で拒否）、容量不足
    - font：fontの不適格（元のprogramにない文字）
    - 状態：stale revision、提案の改ざん（境界・矩形）、未承認での実行、承認内容の改ざん。いずれもPDF・sidecarを公開しない
- `.venv/bin/python -m pytest -q tests/test_adjacent_span.py tests/test_single_span_actualtext.py tests/test_tagged_page_proposal.py tests/test_transaction.py tests/test_marked_content.py`：**124 passed（116.77秒）**
  - テスト補助関数 `make_source` に、本文・他行の文字列と位置の引数を追加した。既定値は従来どおり

full suite、3ページ評価、前回までの実評価は実行していない。

## 制約と未対応

- 対象は、選択範囲が先行Spanの内部（先頭glyphより後ろ）に完全に含まれ、その親P内の次の兄弟が単一のleaf Spanである場合だけ
  - followerは、その次の兄弟が空白だけのleafで、既存のfollower契約で証明できる場合だけ
- 次の場合は拒否する
  - ownerをまたぐ選択
  - 別段落への移動
  - 3つ以上のownerへの再配分
  - ownerが複数のfont resourceで描画されている場合（providerを明示する必要がある）
- カラム端の根拠は「ページ上の可視glyphの最大右端」と「先行ownerの改行との整合」に限る
  - 余白にある注記などで、この根拠が成り立たない文書では拒否され、明示的な矩形が必要になる
- 容量不足の拒否理由は、既存の `_provider` の文言（`single Span provider qualification failed: …`）のまま表示される

## 再実行

```
.venv/bin/python -m evaluations.real_japanese.osaka_adjacent_intent
```

出力は `tmp/osaka-adjacent-intent/`。`noop.pdf` が存在する場合は実行を拒否する。PDF・font・sidecar・rasterはコミットしていない。
計測値は [osaka-adjacent-intent-summary.json](osaka-adjacent-intent-summary.json) に記録した。
