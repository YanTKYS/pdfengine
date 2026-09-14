リポジトリを読み込み、実装・データ構造・テストを確認してから監査結論をまとめます。

調査はほぼ完了です。依存ライブラリをインストールし、監査結論を裏付ける主要テストだけを実行します。

## 1. 結論

**判定: B（一部の基盤設計を今変更すれば到達可能）**

下位層は最終目的の方向に合っており再利用できます。content_stream.py の byte-range provenance、paint_provenance.py の source↔renderer 対応証明、attributed.py / rich_layout.py の「元glyph保持＋新glyph shaping」混在組版、editable.py の revision 拘束 sidecar がそれです。

一方、flow 層（document_flow / story_flow / shared_flow / variable_container / flow_transaction）は、段落1つ分の guarded writer を逐次適用し、毎段階で全PDFを再保存・再検証・再バインドする構造です。identity が revision hash に結び付くため、この上にさらに機能を積むと作り直しになります。ここを今変えれば、既存資産を捨てずに到達できます。

## 2. 現在のアーキテクチャ

- **観測**: PyMuPDF `get_texttrace()` の文字列挙が glyph の正体で、`source_order`（trace 内 index）が glyph ID。
- **由来**: `ContentPage._walk` が pypdf tokenizer で content stream を自前解釈し、`TextEvent`（stream xref、operator byte range、`PaintChar(code, advance, source_orders)`）を作る。trace とは origin の 0.015pt 一致で対応付け、曖昧なら fail closed。path は `paint_provenance` が byte range 目録を作り、当該 operator を `n` に置き換えた counterfactual 再描画で renderer paint と一意対応を証明する。
- **選択**: 人が glyph ID 集合を確定。selection は原本 SHA とページ観測 SHA に拘束。
- **論理**: `SourceParagraph` = Unicode 列 + style span + source unit（code・観測）。編集は Unicode offset。
- **組版**: `layout_attributed` は単一段落・固定 x/width・ハード改行・禁則・first-line indent。`ParagraphShaper` が retained glyph（元 code と実測 advance）と新 glyph（HarfBuzz + 供給 font）を混ぜる。
- **書き戻し**: 元 Tj/TJ 内の選択文字を負の TJ 変位に置換して除去し、先頭 event 直後に `q … 1文字ずつ Tm/Tj … Q` と line matrix 復元を挿入。pypdf で全保存、MuPDF で glyph・font・画素を検証。
- **意味保存**: editable sidecar（PDF SHA 拘束）。flow 層は sidecar を入れ子にした JSON 契約と逐次 `edit_document` で実現。

## 3. 重要な設計上の発見

**Source provenance**: 粒度は十分です（byte range、code、operator 種、Form invocation、clip の位置）。問題は identity の形です。glyph ID は trace index、path ID は revision ごとの digest なので、1回の変更で全 ID が失効し、`document_flow._rebind` が `zip(old_paths, new_paths)` の順序一致と幾何一致で再結合しています。anchors は path 数が変わるため、全 flow 層が `anchors is not None` を拒否しています。装飾追従と flow が排他になっている根本原因はここです。

**Paint ownership**: 明示確認・fail closed の方針は正しいです。ただし所有単位が「paint operator 1つ」で、複数 subpath を1命令で塗る表罫線は分割できません（anchors は `len(cells)!=1` を除外、paint_resize は `len(paints)!=1` を拒否）。自治体で多い表組み文書ではここで止まります。

**Text flow**: 「flow → regions → paragraphs → slots」の概念は shared_flow に存在します。しかし実行形が問題です。
- fragment ごとに `edit_document` → 全PDF保存 → 全ページ 144dpi 画素比較 → 全 path の counterfactual 再証明 → 全 slot の rebind。
- 各層が 300〜450 行の独自 schema と validator を持ち、variable_container ⊃ document ⊃ editable ⊃ paragraph ⊃ selection と入れ子。新機能を足すごとに全層を触る必要があります。
- 行揃えは `alignment='left'` 固定。Word 由来の両端揃え本文を再組版すると前後段落と見た目が変わります。

**書き手**: 新 glyph を1文字ずつ Tm/Tj で出力し `0 Tc 0 Tw 0 Ts` に固定。run 構造が失われ、tracking を持つ元 style は persist を拒否しています（`editable._bind_paragraph`）。

**座標系・特殊レイアウト**: rich_layout、anchors、paint_resize が横書き前提（x/baseline/ascent/descent、bottom-variable）。inline/block 軸の抽象がないため、縦書き対応時に layout・writer・anchors・container を同時改修することになります。

**符号化・構造の境界**: Identity-H のみ対応。非埋め込み MS 明朝 + 90ms-RKSJ-H / UniJIS-UCS2-H は拒否され、Form XObject 内テキストも拒否。自治体 PDF の相当数がここで弾かれます。

**検証基盤の依存**: PyMuPDF 低レベル device の Python subclass と pypdf 私的 API に依存。後述のとおり Python 3.11 では native 側の参照カウント不良で長い flow が abort します。

## 4. 今直すべきもの

- **安定 identity と mutation map**: glyph/path の identity を「stream 内 operator 序数（`byte_edits` で写像）+ code/geometry witness」に統一し、`_rebind` の zip 依存を廃止する。`_map_offset` と byte_edits は既にあるので土台は揃っています。
- **単一トランザクション**: fragment ごとの全PDF保存・全検証をやめ、「計画 → 全 byte mutation を1つの program へ適用 → 1回保存・1回検証」にする。schedule と guard の考え方は残せます。
- **path provenance の1パス化**: `_walk` が path と CTM を記録済みなので、自前幾何と device event の fingerprint 照合で対応付け、counterfactual は監査用オプションに落とす。

次の段階までに: subpath 単位の所有、alignment の観測と再現、inline/block 軸抽象、Form XObject 内編集、非 Identity-H CMap。

## 5. 今は触らなくてよいもの

- 1文字ずつの Tm/Tj 出力（run 単位 TJ 化は identity 統一後で十分）
- inference.py の行・段落推定（候補生成としては現状で可）
- engine.py / backend.py / slot_edit.py / explicit_reflow.py の旧経路（隔離済みの評価資産）
- sidecar の JSON 形式自体（内容の identity だけ差し替える）

## 6. 推奨ロードマップ

1. **Identity と単一トランザクション**: 論理モデルをメモリ上で1つに統合し、sidecar はその序列化にする。完了条件は既存 flow テストが1回保存で通り、anchors と flow を同時に使えること。
2. **段落モデルの完成**: alignment、tracking 保持、run 単位出力、非 Identity-H、Form XObject 内テキスト。完了条件は Word・一太郎系の実 PDF で no-op 再組版が画素同等になること。
3. **候補生成の追加**: 現在の「全部人が確認」に、column/region/follows/背景所有の候補提案を加える（inspect_anchors 方式）。完了条件は実 PDF で確認作業が候補選択で済むこと。
4. **細粒度の装飾・表**: subpath 分割と container policy による罫線・背景の伸縮。完了条件は表セル内本文の長文化で罫線が追従すること。
5. **縦書き・回転・新規 continuation**: inline/block 軸抽象の導入後。完了条件は縦書き公文書の一段落編集。

## 7. 実施した変更

なし。第4節の3項目はいずれも最小修正では成立しない基盤再設計で、部分適用はかえって無駄になります。リポジトリは無変更で、検証用 venv は scratchpad にだけ作りました。

## 8. 検証結果

**環境問題（要注意）**: Linux / Python 3.11 / PyMuPDF 1.27.2.3 では、長い flow テストが `Fatal Python error: none_dealloc` で abort します。純 PyMuPDF の再現で、document の open/close 1回ごとに None の参照カウントが約 1.8 減ります。3.12 では None が不死化されるため再現せず、README の検証環境と一致します。`requires-python` を 3.12 以上にするか、upstream 報告を推奨します。

**テスト**: 変更なしのため全体を1回実行。3.12 では1プロセス実行が OOM で kill されたため2分割しました。

| 実行 | 結果 | 時間 | ピーク RSS |
|---|---|---|---|
| 3.12: story_flow, story_styles, variable_container | 62 passed | 222 s | 9.8 GB |
| 3.12: 残り 13 モジュール | 189 passed, 12 skipped | 19 s | 185 MB |
| 3.11: 全モジュールを個別プロセスで実行 | 全 pass（1件は abort のため 3.12 で確認） | | |

失敗は 0 件です。flow 層のメモリ・時間の突出は、第3節の「毎段階で全PDF再保存・全 path 再証明」構造の実測値でもあります。
