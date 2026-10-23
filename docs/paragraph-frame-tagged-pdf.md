# タグ付きPDFの編集：意味構造を保つ段落編集

2026-10-23。PR #90 マージ後の main `2aefe3e` を起点とする。評価条件は、実装前の commit `8e51d21` で [manifest](../evaluations/paragraph_frame_tagged/manifest.json) に固定した。結果は [results.json](../evaluations/paragraph_frame_tagged/results.json)。

## 結論

@@CONCLUSION@@

## 1. 拒否原因の分類

PR #90 の全段落調査で、意味構造を理由に拒否された段落は135件だった。拒否理由の文言だけでなく、段落を描くマーク付きコンテンツと構造ツリーの実際の形を、段落ごとに記録して分類した（`prototypes/paragraph_frame/tagged_diagnose.py`）。135件は1つの原因ではなく、次の7つの構造に分かれた。

| 件数 | 文書（生成元） | PR #90 の拒否理由 | 実際の構造 |
|---|---|---|---|
| 44 | issue14297（InDesignで配置したPDF） | mixed or nested marked-content scope | 構造ツリー（StructTreeRoot）がないのに、`/Figure <</MCID n>>` が残っている。その内側に `/PlacedPDF`。MCIDは意味を持たない |
| 27 | 府政だより477号（InDesign） | paragraph crosses a marked-content container | 段落の行の中に、配置されたアイコン（「問」など）が `/PlacedPDF` として描かれている。アイコンは自分のクリップとパスを持つ |
| 22 | 160F-2019（Acrobat Distiller 5） | unbound marked content | 表のセル `/TD` がMCIDを直接持つ。構造要素に `/Type` がないため、MCIDとの対応が認識されなかった |
| 15 | 府政だより477号 | replacement-text marked content requires a span adapter | 段落の中の空白1文字が、`/Span <</ActualText <U+0007>>> BDC` で囲まれている（InDesignの「先頭文字スタイルの終了」などの特殊文字） |
| 13 | 160F-2019 | mixed or nested marked-content scope | 1つのセルのMCIDが、見た目では2つ以上の段落を描いている |
| 11 | 160F-2019 | unbound marked content | 同じ行に並んだ複数のセルの文字が、1つの段落として推定されていた |
| 3 | word_osaka_guideline（Word） | multiple semantic paragraph parents | 複数の `/P` 要素の行が、1つの段落として推定されていた |

単純な P/Span 構造（Wordの `/P` と `/Span` 葉要素、ActualText と Lang 付き）は PR #88 で対応済みで、この135件には含まれない。

新たに見つけたタグ付きPDF（pdf.js のテストPDF）でも同じ調査をした。

| 文書（生成元） | 構造 | main の拒否理由 |
|---|---|---|
| prefilled_f1040（Adobe LiveCycle Designer） | `/P` が `/Div` の下にある。構造ツリーに `/IDTree` がある | 2ページ159段落のうち121段落が「IDTree references require richer semantic adapter」、37段落が「mixed or nested marked-content scope」 |
| issue16500（PDFTron）、zero_descent（iText） | `/P` が `/TD` の下にある | 一部は編集可 |
| issue11656（Word） | `/TagSuspect` の入れ子 | 拒否 |
| issue6782（Aspose.Words） | `/Part` の下の `/P` | 拒否（unsupported paragraph child） |

@@DIAG_TABLE@@

## 2. 設計：描画の書き換えと構造の更新の分離

`prototypes/paragraph_frame/semantics.py` が、段落を描くマーク付きコンテンツと構造ツリーについて、元のPDFだけから次を決める。描画の書き換え（`auto_writer.py`）は、この決定に従ってストリームを組み立てるだけで、構造ツリーそのものは書き換えない（変えるのは編集した要素の `/ActualText` だけ）。

### 2.1 マーク付きコンテンツの扱い

段落の文字を描く各シーケンス（BDC/BMC … EMC）を、次のいずれかに分類する。どれにも当てはまらないものは拒否する。

| 分類 | 条件 | 扱い |
|---|---|---|
| 容器（container） | 段落の全体を囲み、段落以外も描く。MCIDのないもの（`/PlacedPDF`、レイヤー、アーティファクト）、構造ツリーのない文書のMCID、段落以外も描くMCID（表のセル） | BDC/EMC をそのまま残し、新しい文字をその内側に挿入する。構造要素の内容は変わらない |
| 再出力（mcid） | 段落の内側にあるMCID | 古い文字と一緒に BDC/EMC を外し、新しい文字の周りに同じ BDC を書き直す。編集で文字がなくなったMCIDも、空のシーケンスとして元の順序の位置に残す（構造要素の内容項目を失わない） |
| 再出力（plain） | 段落の内側にある、意味を持たないシーケンス | 同上 |
| 原子（atom） | 段落の内側にある `/ActualText`・`/Alt`・`/E` 付きのシーケンス（置き換え文字列） | 元のグリフをそのまま囲む場合だけ書き直す。編集でグリフがすべて消えれば一緒に消える。一部だけを変える編集は拒否する |
| 固定（fixed） | 段落の内側にある、文字以外も描くシーケンス（クリップ・パス・画像を持つインライン配置物） | 演算子を一切変えない。再組版後も元の位置にあることを確かめ（ずれる場合は拒否）、内容の順序も「前の文字 → 配置物 → 後の文字」に保つ |

拒否するもの：段落の境界をまたぐシーケンス、再出力するシーケンスの入れ子、Form XObject 内のMCID、置き換え文字列を持つシーケンスが段落を囲む場合。

### 2.2 構造ツリーの条件

再出力するMCIDは、次を満たす場合だけ扱う。

- MCIDを持つ要素は、ブロック要素（`P`、`H`、`H1`〜`H6`、`TD`、`TH`、`LBody`、`Lbl`、`Caption`、`BlockQuote`、`Note`、`Title`、`TOCI`、`Div`、`Sect`、`Art`、`Part`、`Document`、`Index`）か、ブロック要素の下のインライン要素（`Span`、`Quote`、`Code`、`Em`、`Strong`、`Sub`）。RoleMap をたどって判定する。
- 段落のMCIDは、1つのブロック要素の内容（直接のMCIDと、インライン葉要素のMCID）と、順序も含めて完全に一致する。段落の文字と要素の文字が同じものであることを保証するため。
- 要素が持つ項目は `/Type /S /P /K /Pg /Lang /A /C /T /ID /R /ActualText` だけ。`/Alt`・`/E`（代替説明・略語の展開）は、文字を変えると内容と食い違うため拒否する。
- 段落の中にリンク（`/OBJR`）がある場合と、別のストリームを指すMCR（`/Stm`）は拒否する。
- `/IDTree` は拒否しない。B2は構造要素を作ることも消すこともなく、`/ID` も変えないため、IDTree はそのまま正しい。
- `/ActualText` は、元の値が描かれた文字と一致する場合だけ扱い、編集後の文字に書き換える（葉要素ごと、およびブロック要素自身）。
- `/Lang` などの属性は変えない。

### 2.3 文字の対応づけ

複数のシーケンスにまたがる段落では、編集前後の文字列を1文字ずつ対応づける（difflib、autojunkなし）。変わらない文字は元のシーケンス（Span）に残り、変わった部分は、置き換えられた文字のシーケンスに入る（挿入なら直前の文字のシーケンス）。新しい文字が原子や固定のシーケンスに入ることはない。シーケンスが1つ以下の段落では、従来どおり前後の共通部分だけを残す（既存の結果を変えないため）。

### 2.4 公開前の証明

保存の前に、次を確かめる。1つでも満たさなければ公開しない。

- 構造ツリーの正規形（要素をルートからの位置で、ページを番号で、注釈をページと位置で表す）を、元のPDFと保存後のPDFで比べる。違ってよいのは、書き換えた要素の `/ActualText` だけ。ParentTree、IDTree、RoleMap、ClassMap、要素の子と順序、`/Lang` などはすべて同じでなければならない。
- 元のPDFで成り立っていた意味構造の検査（MCIDが構造ツリーに結び付く、MCIDが重複しない、ActualText が描かれた文字と一致する、ParentTree の逆方向の対応）は、保存後も成り立つ。元のPDFの別の場所にある既存の不整合は、編集を止める理由にしない（従来は、ページのどこかに不整合があると、すべての編集を拒否していた）。

### 2.5 実PDFの調査で必要になった、構造以外の共通処理

@@COMMON@@

## 3. 実PDFでの評価

### 3.1 意味構造が原因で拒否されていた135段落

@@SURVEY@@

### 3.2 固定した編集要求（17件、実際の文字列変更）

@@REQUESTS@@

### 3.3 新たに提案できる段落の保存監査（実際の文字列変更）

@@SAMPLES@@

## 4. 既存機能への影響

@@REGRESSION@@

## 5. 次に解消すべき技術的障壁

@@NEXT@@

## 6. テストと再現

@@TESTS@@
