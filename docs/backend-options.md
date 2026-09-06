# Backend 技術選定：同文再描画を先に成立させる

今回の結論は、**TextWriter と位置 redaction を再描画の中心から外し、元 font resource・文字コード・描画順序を保持して対象 text operator を変更する**こと。PyMuPDF の抽出・検証は継続利用できる。保存処理は別途評価し、現 PoC では pypdf 6.10.0 の full clone を使う。Python から別言語へ全面移行する必要性は、今回の結果からは認められない。

これは一般 PDF 全体への対応完了を意味しない。元コードを戻す no-op と、新規 Unicode を既存 subset に追加する処理は能力が異なる。後者には encoding/CID/実 glyph/幅辞書を一体として扱う font writer が必要であり、フォント名や Unicode coverage の確認だけでは足りない。

## 独立した writer 比較実験

実 PDF の京都質問票と沖縄公告から、変更せず抽出した MS Mincho の subset font program を使用した。各 30 文字を空の診断ページの同じ origin・サイズに置いた。比較基準は、元の font resource と元 glyph に対応するコードを直接使う content stream。今回の 2 font は Identity-H かつ CID-to-GID が identity であり、この条件を他の font へ一般化しない。

この実験では **font writer だけ**を比較している。原ページの旧文字除去、元の文字間隔、clip、背景、対象外ページ保持まで通過したという意味ではない。実ページの Track A 評価は別の Stage 1 結果を参照する。

| 書込経路 | 京都最大 origin 誤差 pt | 沖縄最大 origin 誤差 pt | 基準との MuPDF / Poppler 差分 px（京都、沖縄） |
|---|---:|---:|---|
| 元 resource + 元コードを直接 Tj | 0.000065 | 0.000077 | 比較基準 |
| TextWriter + 元 font bytes | 153.125484 | 153.117005 | 5388 / 5413、6424 / 6447 |
| Shape.insert_text + 既存 resource 名 | 0.000065 | 0.000077 | 0 / 0、0 / 0 |
| Shape.insert_text + fontbuffer 新規埋込 | 0.000065 | 0.000077 | 0 / 0、0 / 0 |
| TextWriter 出力後に使用 CID の /W を明示 | 0.000065 | 0.000108 | 0 / 0、0 / 0 |

PyMuPDF 1.27.2.3、各レンダラー 144 dpi、RGB 完全一致による比較。全経路で Unicode 30 文字と glyph ID は一致した。したがって Unicode・glyph 数・glyph ID の一致も、origin を検証する代わりにはならない。Poppler 画像を目視し、TextWriter の重なりと対照経路の正常な字送りを確認した。

再現スクリプトは [options_writer.py](../evaluations/backend/experiments/options_writer.py)、全 glyph と出力 font 辞書は [results.json](../evaluations/backend/experiments/options_writer_results/results.json)。同ディレクトリの PDF/PNG は診断資料であり、破損する TextWriter 対照を意図的に含む。

### 同じ font bytes なのに幅が変わる理由

元 font program の `post.isFixedPitch=1`、MuPDF font の `mono=1` という状態で、TextWriter が作る CIDFontType2 の幅は `/W [0 65535 500]` となった。一方、計測 API と元 PDF の全角 glyph の幅は 1000/em。輪郭は同じでも PDF に書かれた字送りが半分になり、文字が重なる。前フェーズでは font metadata の fixed-pitch フラグだけを外すと問題が消える診断も行っているが、元 font program を無条件改変する製品方針にはしない。

今回、Shape の新規埋込経路は別の幅辞書を生成し、既存 resource を指定する経路は元の幅を保持した。明示 `/W` の対照でも使用 CID に 1000/em を与えると正常になった。**必要な置換対象は font 自体ではなく、文字コードと幅辞書を決める書込経路**である。ただし全角のみの 30 文字で得た結果であり、この実験単独では可変幅 Latin・複雑な CMap・未収録漢字への対応を証明していない。

Shape は `fontname="/既存resource名"` による既存 font 使用を公式に提供する。しかし文字列からコードへの変換と、任意の既存 graphics state への復帰を自動保証する API ではない。[PyMuPDF Shape](https://pymupdf.readthedocs.io/en/latest/shape.html)

## 候補の比較

| 構成 | 確認できたこと | 残る問題・採否 |
|---|---|---|
| TextWriter | Unicode と glyph は合っても `/W` が誤る実例を再現 | 現状態では忠実再描画の既定 writer にしない。生成後検査による拒否は残す |
| PyMuPDF Shape / insert_text | 上記 2 font の幅問題は回避。新 font の単純横書き候補 | 元 Tc/Tw/Tz/TJ、clip、色空間、描画順序は別に保持する必要がある。単純な API 置換だけでは Track A 全体は解決しない |
| 元 resource + 元コード + 局所 content 変更 | 同文を同じ font/glyph/幅/state で描画しやすい。対象外命令の再生成を避けられる | 今回の主方式。code→Unicode と source operator の対応が曖昧、共有 Form、text clipping 等では拒否または追加実装が必要 |
| 新 Type0/CIDFont と `/W` の明示生成 | 実験で計測と出力を一致させられた | Unicode→code→CID→GID、ToUnicode、font program、DW/W、文字間隔を一つの契約で作る。未対応文字を元 subset から作ることはできない |
| MuPDF 低レベル PDFProcessor | operator/resource 単位の callback があり、state 追跡の拡張先になる | 独立 writer の実装試験は未実施。既存 TextWriter と同じ PDF device へ再描画するだけでは幅問題を引き継ぐ可能性がある |
| pypdf | 既存オブジェクトを読み、対象 content のみ変更して full clone 保存できる | renderer ではない。今回の CMap・暗号化保持等には固定版の内部 API 依存があり、adapter と回帰試験で隔離が必要 |
| pikepdf / qpdf | token の raw bytes を保持する編集 API が明確 | parser/serializer・保存 adapter の有力な置換候補。font shaping や glyph の描画位置を完成させるライブラリではない。実行比較は未実施 |
| Java PDFBox | font code、glyph displacement、text rendering matrix、graphics state を扱う解釈器がある | 独立した state/font 解釈器が必要になった場合の候補。言語変更だけで fidelity は保証されず、同じ実 PDF 検証が必要。実行比較は未実施 |

TextWriter は位置と font を蓄積し PDF へ出力する高水準 API。実装中の `write_text` は `pdf_new_pdf_device` と `fz_fill_text` を使って新しい resource を生成しており、同じ処理を低レベル API から呼ぶだけで別 writer になったとは扱わない。[TextWriter 公式仕様](https://pymupdf.readthedocs.io/en/latest/textwriter.html)、[PyMuPDF 実装](https://github.com/pymupdf/PyMuPDF/blob/main/src/__init__.py)

MuPDF PDFProcessor は `q/Q`、`cm`、`W/W*`、text state、text show、ExtGState、Form 呼出し、resource の push/pop を分けて通知する。これは対象文字に作用する state を得る構造として適している。Python の導入環境でも `PdfProcessor2`、`pdf_process_contents` 等の binding 存在は確認したが、callback と source byte offset の対応付けを完成させたわけではない。[MuPDF PDFProcessor](https://mupdf.readthedocs.io/en/1.27.1/reference/javascript/types/PDFProcessor.html)

pikepdf 公式は解析用 parser と編集用 token filter を区別し、parse/unparse では細部が失われるため編集・再構築への注意を明示している。採用するなら、全ページを parse/unparse するより token の原 bytes を保持する方式が目的に合う。[pikepdf Content streams](https://pikepdf.readthedocs.io/en/latest/api/filters.html)。qpdf にも token filter と page/form content 処理 API がある。[qpdf QPDFObjectHandle](https://github.com/qpdf/qpdf/blob/main/include/qpdf/QPDFObjectHandle.hh)

PDFBox 3.0.7 の実装では `showGlyph` に font/code/displacement/text rendering matrix が渡され、text state の字間・単語間隔・横倍率を使って送りを更新する。clip を含む graphics state と resource scope の追跡を独立実装へ移す際の候補になる。[PDFBox PDFStreamEngine 3.0.7](https://github.com/apache/pdfbox/blob/3.0.7/pdfbox/src/main/java/org/apache/pdfbox/contentstream/PDFStreamEngine.java)

## 保存も独立した backend として評価する

content を変更しない再保存対照で、Poppler 144 dpi の元画像との完全差分は、MuPDF 保存で京都 3 px、STid 1276 px、Canvia 0 px、pypdf 保存で 3 文書とも 0 px だった。[保存対照結果](../evaluations/backend/experiments/save_controls/results.json)

したがって content が同じことだけでは十分ではない。resource 内の数値の直列化・精度も画面に影響しうる。今回の主 PoC は原 resource を保持する pypdf full clone 保存を選んだ。これも任意の暗号化方式、署名、破損 PDF まで保存同等性を保証する評価ではない。暗号化と権限、未選択ページ、未到達の旧 content を含めて別途検証する。

## 旧文字を削除する方式

| 方式 | 識別単位 | 安全性と今回の判断 |
|---|---|---|
| 局所 redaction | 文字 bbox と小矩形の交差 | 元実装で同位置 fill/stroke を一緒に消す例がある。選択 paint event だけの削除には不十分。背景・画像を消さない設定も text event の識別は解決しない |
| 対象 text operator の変更 | content stream、operator、文字列 token 内コード範囲 | 主方式。選択コードを空送りへ置換し、後続文字の text matrix を維持できる。異なる位置の選択と同位置の別 paint を区別できる |
| text object / paint event 再構築 | BT/ET または描画 event | state を完全保存できれば有力。BT/ET は意味的な文章単位ではなく、複数文や別領域を含みうる。text clip は ET 以後へ影響する |
| ページ content の部分再生成 | 保存した state と描画順序を持つ局所命令群 | 将来の新規文字・折返し用。非選択命令の byte 区間をそのまま残す。境界で text matrix、graphics state、path、marked content が一致する必要がある |
| ページを display list から全面再出力 | 視覚描画 event | 字形を描けても元 resource、構造、注釈、blend、clip が変化しうる。今回の目的には変更範囲が大きく、主方式にしない |

text show 命令を単に削除すると、同じ BT 内の後続文字まで左へずれる。横書きでは、元 PDF の幅、font size、Tc/Tw/Tz、既存 TJ 数値に基づく送りを残す必要がある。削除中間 PDF を再抽出し、選択文字がなく、非選択 glyph とその origin が保たれたことを確認する。同文を戻した最終 PDF だけの抽出では「削除せず残した」実装を検出できない。

削除は表示上の隠蔽と区別する。白い矩形や `Tr=3` で不可視化してもコードは残る。incremental save は旧 revision を残しうるため、機密除去まで求める場合の既定保存としない。今回の full clone でも、未到達 stream や ActualText 等に旧文字を残さない検証が必要である。

## clip / graphics state をどの単位で保持するか

必要なのは、ページに clip が「あるか」ではなく、**選択 text show がどの state stack の下で実行されたか**である。`q/Q`、CTM、現在の path、clip の適用時点、text matrix と text line matrix、Tc/Tw/Tz/TL/Ts/Tf/Tr、fill/stroke 色空間と値、線幅・join・miter、ExtGState の alpha/blend/soft mask、Form の resource と Matrix/BBox を追跡する。

再描画を元 text show と同じ命令位置に差し戻し、周囲の命令を保持すれば、同じ clip と state が自然に作用する。ページ末尾へ append して似た設定を復元するより、取りこぼす state が少ない。対象に無関係な `q ... W ... Q` 内の clip を理由に、別 scope の文字まで拒否する必要もなくなる。

ただし path clip の bbox が一致しても clip の形は同じとは限らない。text rendering mode 4–7 は文字自体が clip を作り、文字削除が後続 graphics を変える。soft mask、透明 group、pattern、Type3、Form 内での描画など未追跡の状態は拒否を維持する。scope を読めたことと再現を検証したことを区別する。

共有 Form の stream を直接書き換えると、他ページ・他位置の同じ Form まで変わる。Form 対応を進めるなら、呼出し階層を含む paint event ID を持ち、対象 invocation だけを copy-on-write する必要がある。今回のページ直下の operator 対応から無条件に拡張しない。

## 次に置換・拡張する境界

1. **選択モデル**は意味推定と分離し、元 PDF hash、ページ、line/run/glyph、operator とコード区間への provenance を保持する。見かけの bbox だけを編集対象の唯一の識別子にしない。
2. **解釈器**は元 PDF のコード・幅・state を正として計測する。`font.text_length` を元 PDF の `/W` の代わりにしない。複雑な CMap/Form/graphics state へ進む際は MuPDF PDFProcessor または PDFBox を adapter の内側で比較する。
3. **writer**は no-op では元コードと resource を使う。Stage 2 以降の新 glyph は、完全 font の明示埋込と `/W`/ToUnicode 生成を別経路とし、元 subset にない文字を同じ font 名だけで受け入れない。
4. **stream 編集・保存**は対象 bytes の局所変更と原 resource の保持を契約にする。現在の限定 parser と pypdf 内部 API が拡張の障害になった場合、qpdf/pikepdf token filter と保存 adapter を先に置換する。
5. **検証**は Unicode、glyph、origin、幅、state、旧文字除去の中間状態、保存後の両 renderer を維持する。Stage 1 が失敗した selection は、後続 Stage の評価母数へ含めない。

現時点で最も根拠の強い構成は、**選択・layout は既存 Python モデル、既存文字は resource を保持した operator 編集、新文字は計測と幅辞書を一致させる専用 writer、保存は独立 adapter、MuPDF と Poppler で検証**という分離である。言語変更はこの境界ごとに判断でき、推定モデルまで捨てる必要はない。
