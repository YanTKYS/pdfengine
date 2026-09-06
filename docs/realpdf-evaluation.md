# 外部生成PDFによる本文再編集・再レイアウト評価

評価期間: 2026-09-05〜06。対象: 公開済みの外部生成PDF 15件、58ページ。修正前の固定53試行、同じ53試行の修正後再評価、原因を確認する追加8試行を分けて記録する。

現時点で「一般的な業務文書PDFを安全に再編集できる」とは判断できない。53回の固定編集試行のうち保存できたのは5回だが、そのうち元フォントを再利用した同文再描画2回で文字の重なりを目視確認した。文字抽出と編集領域外の画像比較が通っても、編集領域内の描画は壊れ得る。残る3回はすべてフォント代替であり、1回では保存時に暗号化と権限も失われた。

原因を切り分け、描画後の文字位置の照合と保存時の暗号化保持だけを一般的な小修正として加えた。同じ53試行の再評価では、文字重なりの2回は安全な拒否へ変わり、保存は3回、拒否は50回となった。元fontを正しく描けるようになったわけではない。

一方、旧文字を実際に除去して周辺を保持する仕組みは、保存できた5試行で再描画前の文字集合検査を通過し、対象外ページも画素単位で一致した。方式全体が成立しないという結果ではない。現在の境界は、**文字対応・描画状態・推定範囲・書き出すフォントの計測と描画の一致を、対象ごとに確認できる局所編集**にある。文書の生成元だけで対応可否を判定することはできない。

## 評価範囲と再現性

原本はエンジンで作らず、自治体の公開通知・公募文書、The Document Foundationの資料、外部配布された製品文書から収集した。OCRによる画像本文の編集は行っていない。図中の文字がvector輪郭になっている資料は、文字オブジェクトがない境界例として残した。

全58ページに対してGlyph、paint span、Run、Line、Paragraph、TextBox、font resource、画像、図形、clipを機械調査した。意味上の段落・表セル・見出しの目視レビューは、大阪2件と和歌山の1ページ目、LibreOffice 3件の1ページ目、Canviaの1ページ目、STidの1ページ目、Ubiquitiの2ページ目、武雄の2ページ目、Meuralと新潟の1ページ目に実施した。元のWord/ODT/HTMLにある段落情報は入手しておらず、意味構造の正解は原本の見た目と文章構成からの判断である。残るページの推定件数を、意味構造の正解件数とは扱わない。

固定ケースは各資料の同文再描画、短文の長文化、複数行の短文化を基本とし、幅の既定値/明示値を比較する4組8試行と、同位置fill/stroke見出しの1試行を加えた。Ubiquitiには複数行本文がないため短文化を実施していない。成功しそうなBoxへの差替えやPDF別の例外処理はしていない。日本語の長文化文は英数字を混ぜた「申請書類の提出期限は2026年10月15日です。ID: Wi-2026を確認し、追加資料と一緒に提出してください。」を使用した。

53は独立した53文書ではなく、同じ15原本の選択領域・操作の組合せ数である。公開文書からの便宜的な標本であり、母集団の成功率を推定する調査ではない。

| 証跡 | 内容 |
|---|---|
| [sources_word.json](../evaluations/realpdf/sources_word.json)、[sources_lo.json](../evaluations/realpdf/sources_lo.json)、[sources_print.json](../evaluations/realpdf/sources_print.json) | 出典URL、取得日時、SHA-256、metadata、生成元の根拠、フォント調査 |
| [inventory](../evaluations/realpdf/inventory) | 全ページの観測モデルと件数 |
| [review_word.json](../evaluations/realpdf/review_word.json)、[review_lo.json](../evaluations/realpdf/review_lo.json)、[review_print.json](../evaluations/realpdf/review_print.json)、[review_remaining.json](../evaluations/realpdf/review_remaining.json) | 原本の目視と推定構造の照合、Boxごとの根拠 |
| [cases.json](../evaluations/realpdf/cases.json) | 事前固定した53操作 |
| [baseline/results.json](../evaluations/realpdf/runs/baseline/results.json)、[guarded/results.json](../evaluations/realpdf/runs/guarded/results.json) | 修正前/後の同じ53操作。拒否理由、font probe、除去監査、保存後差分 |
| [supplemental/results.json](../evaluations/realpdf/runs/supplemental/results.json) | 新漢字・英数字・記号の実描画6操作、過結合リスト2操作 |
| [document_matrix.csv](../evaluations/realpdf/document_matrix.csv) | 15原本それぞれに対する依頼13項目の結果と評価限界 |
| [case_outcomes.csv](../evaluations/realpdf/case_outcomes.csv)、[final_assessment.json](../evaluations/realpdf/final_assessment.json) | 修正前/後/追加の各操作を、目視・意味範囲・保存設定も含めて分類した最終一覧 |
| [baseline/environment.json](../evaluations/realpdf/runs/baseline/environment.json) | 実行版とエンジンのファイルハッシュ |

## 原本ごとの結果

「取得」は原本全体の正確な論理文書復元を意味しない。「拒否」はその固定操作で出力せず原本を保持したことを表す。推定の誤認識がある文書でも、後段の保守的な検査で拒否される場合があるため、両方を併記する。生成元の「確認」はPDF metadataからの確認であり、原本の制作工程を完全に追跡した証明ではない。

| ID / 原本 | 頁 | 生成元の根拠 | 修正前→後 保存/固定試行 | 判定と主要な境界 |
|---|---:|---|---:|---|
| word_osaka_fire_notice / [大阪・林野火災通知](https://www.pref.osaka.lg.jp/documents/63648/20250327jimurennraku.pdf) | 1 | Creator/ProducerともWord Microsoft 365 | 0→0/4 | 本文6行3段落は妥当。太字見出しのfill/strokeを二重文字化、字間を広げた「事務連絡」を分断。不均一字間・clip・paint状態で拒否。 |
| word_osaka_guideline / [大阪・届出ガイドライン通知](https://www.pref.osaka.lg.jp/documents/63648/guideline_tsuuchi.pdf) | 1 | Creator/ProducerともWord LTSC | 0→0/3 | 本文4行は妥当だが視覚上3段落を2段落へ結合。添付資料/参考/URLも過結合。編集はclipで拒否。 |
| word_wakayama_guidelines / [和歌山・公募実施要領](https://www.pref.wakayama.lg.jp/prefg/020400/d00220063_d/fil/jissiyouryou.pdf) | 6 | Microsoft Print To PDF確認。Word原稿はTitleから推定 | 0→0/3 | 見出し・本文・番号付き項目を過結合し、ぶら下げ継続行を別Boxへ分断。mixed-styleまたは周辺衝突で拒否。 |
| word_takeo_notice / [武雄・公開講座通知](https://www.city.takeo.lg.jp/uploads/20240116press01.pdf) | 2 | Quartz/macOS 14.2.1確認。Word原稿はTitleから推定 | 0→0/5 | 本文8行のGlyph/Run/Line/Boxは妥当だが、内容上3区切りを2段落へ結合。日付・会場・別ラベルも過結合。既定幅/明示幅ともclipで拒否。 |
| lo_migration_ja / [LibreOfficeへの移行](https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf) | 10 | Writer / LibreOffice 4.0確認 | 0→0/3 | 本文4行1段落は妥当だが日英の字体/サイズが異なる。subset TTにUnicode cmapがなく元フォント再利用不可。mixed-style/clipで拒否。 |
| lo_newfeatures_ja / [LibreOffice 4.1新機能](https://wiki.documentfoundation.org/images/b/b7/LO4.1_NewFeatures.pdf) | 8 | Writer / LibreOffice 4.0確認 | 0→0/5 | 単一書式の本文2行は妥当。表の別セルや見出しを過結合。subset TTの再利用不可。幅変更を含む全試行をclipで拒否。 |
| lo_enterprise_en / [LibreOffice企業向け案内](https://wiki.documentfoundation.org/images/3/36/EN_Enterprise.pdf) | 2 | Draw / LibreOffice 5.1確認 | 0→0/3 | 3列は分離できるが列内の項目を過結合。Software/office内に疑似空白。ligatureのglyph対応・零面積問題と幅不足で拒否。 |
| print_canvia / [Canvia手順書](https://fccid.io/2AUDT-CANVIAART/User-Manual/User-manual-4452118.pdf) | 8 | Skia/PDF m77確認。Creator空欄のためブラウザ由来は推定 | 0→0/5 | 文字単位に細かい描画から行は復元。a/bや2/3の項目を結合し、継続行を分断。clipまたはlink衝突で拒否。 |
| print_ubiquiti / [U6 Mesh Pro設置ガイド](https://fccid.io/SWXU6MESHP/User-Manual/Quick-Installation-Guide-7315317.pdf) | 3 | Chrome 120 / Skia m120確認 | 0→0/2 | 抽出できるのは主にヘッダー/フッター。2ページ目の本文ラベルはvector輪郭で本文編集対象がない。周辺文字の試行はclipで拒否。 |
| print_fcc_ms / [STidラベル管理票](https://fccid.io/OVNAC4/Label/Label-Sample-2-3606396.pdf) | 1 | Microsoft Print To PDF確認。Excel原稿はTitleから推定 | 0→0/3 | 黄色背景の注記7行は復元。表の別セルを結合し、実在する枠の幅を認識しない。clipまたは幅不足で拒否。 |
| print_fcc_chrome / [Meural Canvasガイド](https://fccid.io/PY319200443/Users-Manual/Users-Manual-4365158.pdf) | 8 | Chrome 74 / Skia m74確認。ただし最初5頁と後半3頁で構成が異なる混在資料 | 0→0/3 | 導入文2行と見出しの推定は妥当。popup背面の本文と閉じるiconを結合する別箇所あり。clip/幅不足で拒否。RC4保持はこの原本では未検証。 |
| word_kyoto_questions / [京都・質問回答](https://www.city.kyoto.lg.jp/kankyo/cmsfiles/contents/0000311/311254/kaitou_hp.pdf) | 2 | Microsoft Print To PDF確認。Word原稿はTitleから推定 | 2→1/3 | 修正前の同文再描画で文字重なりが発生、修正後は拒否。短文化は代替フォントで保存。選択Boxは質問全文ではなく継続部分であり、段落編集範囲に注意。長文化は衝突拒否。 |
| word_niigata_hearing / [新潟・説明会公聴会公告](https://kenpo.pref.niigata.lg.jp/bn/R06_10/1008_t78/t78_20241008i30673.pdf) | 3 | PScript5 / Distiller 17確認。Word原稿はTitleから推定 | 1→1/5 | 独立短見出しは妥当。冒頭や別節で見出し・本文・日付を過結合。明示幅230ptのfont代替は保存、既定幅は衝突拒否。RC4/権限は修正後保持。 |
| word_okinawa_procurement / [沖縄・共同調達公告](https://www.pref.okinawa.lg.jp/_res/projects/default_project/_page_/001/035/156/01_koukoku.pdf) | 2 | JUST PDF 5確認。Word原稿はTitleから推定 | 2→1/3 | 修正前の同文再描画で文字重なり、修正後は拒否。短文化は代替フォントで保存するが、本文と日付が同じBoxなので日付も置換される。長文化は衝突拒否。 |
| word_osaka_symposium / [大阪・シンポジウム通知](https://www.pref.osaka.lg.jp/documents/63648/jimurennraku_1.pdf) | 1 | Ghostscript 9.27確認。原稿アプリ不明 | 0→0/3 | 780/780 GlyphがU+FFFD。文字位置はあってもUnicodeを復元できず、全操作を拒否。画像PDFとは別の文字対応失敗。 |

Glyph/Run/Line/Paragraph/TextBoxの各判定、フォント、文字数変更、1行→複数行、複数行→1行、混在文、除去、周辺保持、別レンダラーの13項目は[CSV](../evaluations/realpdf/document_matrix.csv)に分けて記録した。未保存の操作について、保存後の除去・表示を成功とは記していない。

## 保存・拒否・破損の内訳

| 結果 | 回数 | 解釈 |
|---|---:|---|
| 保存、元fontを再利用、領域内の字間破損あり | 2 | 京都/沖縄のno-op。編集結果破損。旧文字除去と領域外保持の成功とは別判定 |
| 保存、font代替 | 3 | 京都/沖縄の短文化、新潟の明示幅変更。文字は目視で読める。京都/沖縄は編集範囲の意味上の問題、新潟は修正前に保存設定の問題 |
| 安全に拒否 | 48 | 出力なし、原本SHA不変。うち過剰に保守的な拒否や推定誤りに起因する拒否を含む |

48回の最初の拒否理由は、clip 26、mixed-style 6、周辺衝突5、Unicode/ligature 5、幅不足3、不均一字間1、link/annotation衝突1、paint状態1である。最初の検査で止まるため、その理由を解消すれば成功するとは限らない。

保存できた5回すべてで、MuPDFによる全ページ画像比較とPopplerによる対象ページ画像比較は編集マスク外の差分0、対象外文字の署名保持、画像digest/配置保持、図形の形状属性保持を確認した。pypdfでも新文字列を抽出できた。しかし、京都と沖縄のno-opはPoppler画像の目視で字間の圧縮・重なりが判明した。baselineの機械ラベル`structural_checks_passed`はこれらの限定的な検査の通過を表し、**総合成功ラベルではない**。総合判定は[操作別の最終分類](../evaluations/realpdf/case_outcomes.csv)を参照する。破損は[京都の保存前](../evaluations/realpdf/runs/baseline/word_kyoto_questions--noop/before.png)と[保存後](../evaluations/realpdf/runs/baseline/word_kyoto_questions--noop/after.png)で確認できる。

baselineでは、短い1行を自然な複数行へ増やして保存できた例はない。京都のno-opで計画行数が4→5になったのは空白行の再生成を含み、長文化の成立例には数えない。複数行→1行は京都/沖縄の2回で保存できたが、選択Boxが本来の文章単位と一致するか、font代替が許容できるかは別条件となる。日英数字混在の日本語長文化文もすべて拒否されており、混在文の一般的な再組版能力はこの評価では成立確認に至っていない。

## TextBoxの幅は何から決めているか

通常は、推定した文字群の`bbox.width`を既定の組版幅にする。`available_width=null`、`width_source=observed-line-extent`は「利用可能な枠幅を復元した」という意味ではない。短い1行には幅が不確実という警告を出すが、実際のレイアウト計算にはその観測幅を使う。右側の空白、隣接段落の右端、同じ列の本文幅、ページ余白から利用可能幅を補完する処理はない。

例外は、文字を囲む単一の線付き`re`矩形を検出できた場合である。左右の内側余白が対称という仮定で幅を求める。大阪の連絡先枠ではこの処理が働き、林野火災通知は155.130pt、ガイドライン通知は156.240ptを得る。ただし連絡先の部署名・電話番号の明示改行はParagraph推定で結合されており、幅の検出だけで編集が妥当になるわけではない。

MeuralのEmail入力欄の見た目を持つBoxでも単一のborder矩形を検出し、観測文字幅30.016ptに対して利用可能幅164.655ptを得た。ただし、フォーム構造を復元したものではなく対称余白を仮定した結果であり、このBox自体の編集実行は追加していない。

STidの黄色い注記枠は、背景がfill-only矩形、縁が複数要素のfill-only図形であり、この検出条件に合わない。目視で枠があっても利用可能幅はnullのままである。PDFの罫線表現を十分に解釈していないことが原因であり、STid専用処理を加えるべき問題ではない。

新潟の「１ 素案の概要」は観測幅71.22pt。置換後の「１ 素案の概要と説明会について」は既定幅だと折返しが増え周辺文字へ衝突して拒否されるが、明示230ptでは1行で保存できた。この230ptは評価者が与えた感度分析の値で、元文書から正しい枠幅を復元した結果ではない。武雄、LibreOffice新機能、Canviaでも既定/明示幅を対にして試したが、両方ともclip検査で止まった。

したがって懸念は実在する。モデルは不確実性を表示しているものの、**観測文字列幅を実際の組版幅として使うため、短文の長文化に狭すぎる幅を選び得る**。inferenceの改善に加えて、幅が不明なケースを自動編集可能と扱わない契約が必要である。

## subset fontと必要Glyph

評価ではフォント名の一致だけを見ず、PDFから取り出したfont bytes、font内のUnicode cmap、PyMuPDFの`Font.has_glyph`による実際の符号化可否を調べた。各固定対象に対し、元文、元文+新漢字「麒麟鬱」、元文+英数字「 Wi-2026 A9」、元文+記号「 ※①€」の4種類をprobeした。probeはfont選択だけの診断であり、後段の拒否を越えて保存できたことを意味しない。mixed-style Boxでは先頭スタイルのfontで文字列全体を診断しているため、その結果を文書内の全fontの能力へ一般化しない。

| 資料群 / 選択font | 元文のcoverage | 新漢字 | 英数字追加 | 記号追加 |
|---|---|---|---|---|
| 大阪2件、和歌山、武雄、京都、沖縄 | 選択resourceで可 | 不足→代替 | 少なくとも一部不足→代替 | 少なくとも一部不足→代替 |
| LibreOffice 3件 | 埋込fontのUnicode cmapがなく、そのまま再利用不可 | 代替 | 代替 | 代替 |
| Canvia / STid / Meuralの選択font | 可 | 不足→代替 | 可 | 少なくとも一部不足→代替 |
| Ubiquitiの選択ヘッダー、新潟の選択領域 | 元文でも代替 | 代替 | 代替 | 代替 |
| Ghostscript通知 | Unicode復元自体が失敗 | 有効な再利用評価不能 | 同左 | 同左 |

「subset」はfont名の6文字prefixだけでは決められない。genericな`CIDFont+F1`という名前でも必要なUnicodeが欠け、prefixがなくても実質的に限定されたfont resourceがある。また、PDF側のToUnicodeで既存の文字列を抽出できることと、取り出したfont programをUnicodeから引き直して新文字を描けることは別である。LibreOffice資料は前者ができても後者ができない実例となった。

京都/沖縄ではさらに、coverage検査が通った元fontを使ったno-opが描画破損を起こした。fontの取得・名前・必要Glyphの有無に加え、レイアウトに用いた字幅と実際にPDFへ出力する字幅の一致が必要である。原因は取り出したMS Minchoの`post.isFixedPitch=1`に対し、TextWriterが新しいCIDFontの`/W`を`[0 65535 500]`と生成することだった。計測と元PDFでは漢字が1000/emなのに、出力では500/emで送られる。京都の計画位置からの最大x誤差は195.367pt、沖縄は226.901pt。空のPDFへの書出しでも再現し、redactionや元のclipは原因ではなかった。修正後は実際の出力Glyph列・個数・原点を計画と照合し、この不整合を保存前に拒否する。font metadataを無条件に書き換えて通す処理は加えていない。[原因の実測](../evaluations/realpdf/diagnostics/font_writer_findings.md)

## 旧文字を除去する操作と影響

baseline backendは選択Glyphのbbox中心に0.1×0.1ptのredaction注釈を作り、`fill=False, cross_out=False`、`apply_redactions(images=0, graphics=0, text=0)`を実行する。この`text=0`は文字の削除を行う設定であり、画像とvector graphicsは変更しない設定である。白い矩形で覆って旧文字を隠す方式ではない。削除後に新しいTextWriter出力を追加する。

削除対象は文字オブジェクトのIDではなく、redaction矩形と文字bboxの交差で決まる。完全に重なった別文字がある場合にも影響し得るため、削除直後・再描画前に再度Glyphを取得し、元の非選択Glyphの`(Unicode, x座標, y座標)`の多重集合と照合する。座標は小数2桁へ丸める。予期しない残存/消失があれば保存せず拒否する。重なりへの一般的な安全性はこの照合と事前のpaint検査に依存し、redaction自体が選択文字だけをID指定で消しているわけではない。

実際に保存した5試行の再描画前監査はすべて一致した。同文再描画では最終PDFに同じ文字が当然現れるため、最終抽出の文字列検索だけで旧文字の残留を判定できない。再描画前の集合照合と、保存後の抽出・描画確認を分ける必要がある。baselineの主ハーネスのpypdf検査は保存後の新文字列の存在確認だったため、追加の保存後監査で旧Boxの文字列もMuPDFとpypdfの両方から検索した。修正後の京都/沖縄の短文化2回と追加Glyph置換6回の計8保存で、旧Boxの文字列が存在しないことを両方の抽出器で確認した。新潟は置換後の文字列に元の短見出しをprefixとして含むため、旧文字列の検索はtrueになる。これは旧オブジェクトの残留を示さず、削除後・再描画前のGlyph集合照合で切り分ける。同文再描画も同様である。[保存後監査](../evaluations/realpdf/runs/guarded/postsave_audit.json)・[追加試行の保存後監査](../evaluations/realpdf/runs/supplemental/postsave_audit.json)。過去revisionや未参照streamまでのフォレンジックな消去保証を行った評価ではない。

拒否された48試行には、通常のguardを維持した編集とは別に、コピーを使って**除去だけ**を実行する隔離probeを行った。probeでは出力PDFを保存せず、編集成功数に加えていない。41回は非選択Glyph・画像・図形の構造検査を通過した。7回の失敗は次の通り。

| 除去のみprobeの失敗 | 回数 | 観測と解釈 |
|---|---:|---|
| LibreOffice企業案内のno-op/短文化 | 2 | 零面積ligature glyphを安全に指定できず、適用前に停止 |
| 新潟のno-op/短文化 | 2 | Glyph数は同じだが座標の丸め値594.53→594.52等が変わり集合不一致。実際の文字消失と断定できず、数値精度に敏感な監査の境界 |
| Ghostscript通知の全操作 | 3 | 選択Glyphの除去が不完全。no-opは非選択予定156に対して残存692、長文化は754に対して779。通常の編集はこのprobeより前にUnicode未対応で拒否 |

大阪の重なり見出しはfill/strokeの2描画を論理的な二重文字として取得していた。通常の編集はpaint状態検査で拒否し、隔離除去probeは両描画を選択した状態で照合に通った。「重なった他の未選択文字も常に保持できる」という証明にはならない。

## 保存後の周辺差分と別レンダラー

保存した5試行では、MuPDF 96dpiで全ページを描画し、編集対象外ページは全画素を比較した。対象ページは旧Boxと新Boxの和集合をfont sizeの半分ずつ拡張した矩形を変更許容マスクとし、その外側を比較した。差分は完全一致と、各色の差が8を超える画素数の両方を記録した。別実装のPoppler 96dpiでは対象ページの保存前/後を描画して同じ比較を行った。画像・図形・対象外文字の構造検査も併用した。

5試行すべてで、両レンダラーのマスク外画素差分は完全一致基準でも0。MuPDFの対象外ページも全画素一致した。これはこの解像度・このマスク・この標本における周辺保持の証拠であり、マスク内の他要素、極小の差異、印刷時の色管理、全PDF viewerでの表示、アクセシビリティ構造までの保証ではない。特にマスクはBox全体の幅を含むため、編集領域内の描画品質は別途評価する必要がある。実際にno-op 2試行の字間破損をこの画素基準だけでは検出できなかった。

修正前の新潟の保存では画像と文字の見た目に領域外差分がない一方、`Standard V2 R3 128-bit RC4`、permissions `-1324`が、暗号化なし、permissions `-4`へ変わった。PDFの保存同等性は画像差分だけでは測れない。PyMuPDFの保存時には暗号化保持を明示する必要があるため、`encryption=PDF_ENCRYPT_KEEP`を明示する小修正を適用した。修正後の同じ試行ではRC4およびpermissions `-1324`を保持した。[PyMuPDFの保存・暗号化設定](https://pymupdf.readthedocs.io/en/latest/document.html#Document.save)

## 問題の原因と対応層

| 問題 | 主原因 | 現在の設計内 | inference改善 | backend変更 | アーキテクチャ変更 |
|---|---|---|---|---|---|
| 短い1行で狭すぎる幅 | 観測幅と利用可能幅が別なのに同じレイアウト入力へ落ちる | 幅の不確実性を明示して範囲を限定可能 | 共通右端・列・複合罫線の推定で改善余地 | 抽出図形の表現拡充が有用 | 絶対的な正解幅はPDFだけでは不明。推定確度と明示範囲の契約が必要 |
| 箇条書き/日付/表セルの過結合と分断 | 左端と近接中心のクラスタリング | 選択候補を制限可能 | 番号/ぶら下げ/セル境界/意味改行のモデル改善 | 必須ではない | 表全体の再組版やページreflowを扱うなら拡張が必要 |
| fill/strokeの二重文字 | 論理文字と描画イベントが未分離 | 現在は拒否 | 同位置paint pairの関連付け | paint状態を保持して再描画する必要 | 描画モデルの拡張が必要 |
| mixed-style本文 | Box全体を単一fontで置換するwriter | 現在は拒否 | 分離するだけでは段落全体編集にならない | style対応のwriterが必要 | 置換前後の文字とstyleの対応付けが必要 |
| 他の図形に隠れた文字を可視扱い、閉じるiconを本文と結合 | Meuralでpopup背面本文も取得し、paint順序による遮蔽を選択モデルへ反映しない | clip等の別guardで現在は拒否 | icon/本文の分離に改善余地 | 描画順序と遮蔽を扱う必要 | 文字bboxと可視フラグだけのモデルでは不十分 |
| clipのあるページを広く拒否 | clipの作用範囲と新文字の描画状態を復元していない | guard維持で安全拒否 | inferenceだけでは解決しない | graphics state / clip scope対応が必要 | 範囲を限定したpaintモデル拡張。無条件にguardを外すべきではない |
| subsetで新漢字がない | 必要Glyphがfont programに含まれない/Unicodeから到達できない | 全Glyphを持つfontへの代替は可能 | 解決しない | 既存CID対応の再利用は改善余地 | 本当にないGlyphの同一font再現には元font等の外部資源が必要 |
| 元fontのcoverageが通っても字間破損 | 等幅fontのTextWriter出力`/W`が500/em、計測1000/emと不一致 | 出力位置の照合を追加し破損保存を拒否する小修正済み | 解決しない | 正しい再利用には幅生成またはwriter変更が必要 | 拒否防止だけなら不要。対応拡大はbackendの再検討 |
| 除去判定の座標丸め差 | 再解釈による微小な位置変化と固定丸め | 小修正の余地はあるが、近接別文字を混同しない条件が必要 | 解決しない | 署名照合の精度/対応付け検証 | 完全なオブジェクト指定削除には別の操作方式が必要 |
| 保存時の暗号化/権限消失 | saveの既定値に依存 | 暗号化保持の保存引数を明示して修正済み | 不要 | 保存後に同等性も検査 | 不要 |
| vector輪郭の本文ラベル | そもそもPDF text objectではない | 対象外 | Glyph推定では解決しない | text backendの変更だけでは不十分 | source文書取得または別の形状/意味認識が必要。今回対象外 |
| GhostscriptのUnicode未復元 | 現行抽出経路で文字符号対応を復元できない | 現在は拒否 | 行/段落推定前の問題 | CMap/encoding回復経路が必要 | 回復に足る情報がなければ外部資料が必要 |

この資料群では、clipを局所的に判定できないことが最も多い拒否理由だった。ただし先にclip guardを緩めると、既に見つかった段落境界の誤りやfont出力の破損が表面化する可能性がある。対応範囲を広げる順序は、描画と保存の同等性を検査できるようにすること、誤った編集範囲を成功扱いしないこと、作用範囲を持つpaint状態を扱うこと、とするのが妥当である。

## 再実行

作業ディレクトリを`D:\codex\pdfengine`として実行する。`--run-name`は新しい名前を指定する。既存baselineと原本は上書きしない。依存実行環境の場所が異なる場合は`--poppler`と`--pypdf-python`を指定する。

```powershell
.venv\Scripts\python.exe -m evaluations.realpdf.evaluate inventory --engine baseline
.venv\Scripts\python.exe -m evaluations.realpdf.evaluate run --engine baseline --run-name baseline-repeat
.venv\Scripts\python.exe -m evaluations.realpdf.evaluate run --engine current --run-name current-repeat
```

最終の既存・回帰テストは131件すべて通過した。破損時の非保存、原本の不変、RC4/AESの暗号化・権限・owner password保持もテストした。原本15件のSHA-256は取得manifestおよびinventoryと一致し、凍結baseline/現行コードのhashも各評価時の記録と一致した。

実行環境はPython 3.12.14、PyMuPDF 1.27.2.3。別レンダラーはPoppler 26.07.0、独立した文字抽出はpypdf 6.10.0。原本SHA-256は各manifestとresultの`input_unchanged`で追跡する。baselineに保存された破損PDFは失敗の再現証跡であり、完成品として利用するPDFではない。

## 修正後と追加実験から分かった安全な編集の範囲

修正は二つに限った。書き込んだGlyphのUnicode列・個数・原点を計画と照合して不整合なら保存前に拒否することと、保存で元の暗号化を保持すること。位置の許容差は既存のmetrics検査と同じ`max(0.12pt, font size × 0.025)`であり、pixel完全一致を保証するものではない。この新guardはUnicode列・個数・描画原点を検証し、字形の輪郭そのものやインクが占める領域、推定された段落の正しさまでは保証しない。推定アルゴリズム、幅の推定方法、clip/mixed-style等の既存guardは緩めていない。

固定53試行の再評価は3保存・50拒否となった。修正前に48回拒否した操作の結果は変わらず、破損した2回だけが`writer_geometry`で拒否へ変わった。3保存はUnicode・字形位置・暗号化/権限・周辺差分の検査を通り、代替フォントの文字は目視でも読める。しかし京都の短文化は質問の先頭行を残して継続部分だけを変更し、沖縄は本文と同時に日付も変更する。**Box単位の局所描画として成立することと、文章の編集意図を正しく推定できたことは異なる。** この2回を自動段落編集の成功とはしない。

新潟の明示幅変更は、選択が独立した1行の見出しで、変更した文字数は異なるが結果も1行である。代替フォントと評価者指定幅を許容する条件下では、旧文字の除去、実描画、周辺・対象外ページ保持、保存設定保持を確認できた。今回示せた成功範囲はこのような手動で意味範囲と幅を確認できる局所編集である。同じ文書全体、同じProducerの他PDF、長い本文の自動reflowへは一般化しない。

追加8試行は、失敗原因が判明してから行った診断なので、固定53試行とは別母数とする。

| 追加試行 | 結果 | 判定 |
|---|---|---|
| 京都/沖縄の各Boxを「麒麟鬱」に変更、計2回 | font代替で保存。新文字抽出、計画とのGlyph位置一致、周辺差分0 | 元subsetにない漢字は代替で描ける。Boxの意味範囲の問題は残る |
| 同Boxを「ID: Wi-2026 A9」に変更、計2回 | font代替で保存。同じ構造・画像検査を通過 | 英数字の実描画確認。元font維持の成功ではない |
| 同Boxを「確認 Wi-2026 ※①€」に変更、計2回 | font代替で保存。同じ構造・画像検査を通過 | 日本語/英数字/記号混在の局所描画は成立。自然な文章段落の再構築は示していない |
| 和歌山p1-b15の同文再描画/短文化、計2回 | 単一書式の事前検査を通るが、不均一字間で拒否 | 番号付き項目の過結合は残る。誤った意味範囲を認識して拒否したわけではなく、後段の別guardに止められた |

追加6保存はPopplerの描画もすべて目視し、新漢字・英数字・記号が読め、重なりや欠落字形が見られないことを確認した。実際のGlyph原点最大誤差は0.094pt未満で、単なるfont名一致ではなく出力のUnicodeと位置を照合した。これらも京都/沖縄の同じ誤った意味範囲を保った試行であり、6件の一般的な業務段落編集成功という意味ではない。

旧文字除去の追加診断では、明示的に未選択とした同位置strokeの32Glyphがfillと一緒に消え、監査で拒否された。現行の位置redactionで任意の重複文字を個別に削除することはできない。さらにSTidの黄色背景・罫線・ロゴは残ったものの、144dpiの比較では旧Boxの2pt外にも42画素の変化があった。粗い図形ハッシュが一致しても、最大約0.000031ptのvector座標差と描画境界の変化を見落とし得る。これらはguardを通った保存編集の成功例ではなく、コピー上の除去のみの診断である。[除去診断の全結果](../evaluations/realpdf/diagnostics/removal_visual/README.md)

新潟の除去のみの拒否は、全1053非選択Glyphの文字・個数・書式が保たれ、最大座標差が0.000122pt、144dpi領域外差分が0と分かった。594.525024pt→594.524902ptのような変化を小数2桁に丸めると違う値になるためである。過剰拒否と分類したが、近接文字や重複描画を混同しない照合への変更は今回行っていない。

現段階では「編集範囲と利用可能幅が確認できる、単一書式の水平文字を、実描画が検証できるフォントで局所置換する」範囲を試験対象として継続できる。表、箇条書き、本文と日付、リンク付き本文、clipを伴う文字、元subset fontを保持する変更、1行から複数行への自動拡張は、生成元にかかわらず別の未解決条件を持つ。guardの通過だけを利用者の編集意図の保証としないことが、この評価から得られた重要な境界である。
