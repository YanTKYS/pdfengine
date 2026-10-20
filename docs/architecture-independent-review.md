# Adobe Acrobat Proを目標とした独立アーキテクチャ評価

評価日：2026-10-20（Asia/Tokyo）。対象main：`3c862a7a925f27344d295953cae6798f2d198c9a`（PR #84マージ）。
コード・既存の測定記録・公式API/ソースを読む評価であり、新方式・SDKの編集実験は今回実施していない。
既存の実装工数は採否の加点要素にしていない。

## 1. 結論：推奨アーキテクチャ

**B：現行pdfengineを部分的に再設計する。推奨するのは、段落/テキストフレームを編集モデルの中心にした、部分編集＋編集領域再構築のハイブリッド方式である。**

既存のglyph/code保存方式は、小変更の高忠実度な経路として残す。
一般的な本文編集では、元のMCID/Span ownerを変更権限・折返しの最小単位にしない。
元の命令とownerは入力の証拠として読み取り、利用者が承認した段落・領域の全対象文字を実際に除去し、
論理テキスト・スタイル・段落制約から再組版する。変更領域内のタグ・MCID・ActualTextは意味に合わせて更新/局所再構築し、
領域外を保護する。再構築によってPDFとしての意味・検索・読み順を壊してよいという提案ではない。

理由は次の3点である。

1. **現在の編集単位が出力実装の断片に結び付きすぎている。** Wordの1段落が多数のownerに分かれることは普通に起こる。
   同じ見た目/意味の文章が、Spanの切り方やActualTextの有無だけで編集できなくなる。
2. **不足しているのは主に編集モデルと文書復元であり、Pythonの実行速度ではない。**
   Rust/C++へ同じ制約を移植しても編集範囲は増えない。成熟したPDFエンジンを使いつつ、推定と再組版を分離する方が合理的である。
3. **既存資産には再利用する技術的価値がある。** glyph provenance、元文字の確実な除去、フォント証明、独立レンダラー監査は、
   再構築方式にも必要な検証部品である。ただし、現在の状態schema・owner制約を新モデルの仕様にする必要はない。

**採用確度は中程度。** 領域再構築の安全な除去・局所retag・日本語長文化は、この新構成では未実証である。
§8の比較PoCを次の開発の関門とし、owner別の例外実装を増やす開発はそこで一旦止める。
商用SDKが同じ課題を十分な日本語品質・配布条件で解決できるなら、SDK中心のCへ転換する。
SDKの機能名や営業資料だけで、現在のエンジンを全面廃棄する判断はしない。

ゼロから始めても、まず「論理段落モデル＋成熟したPDF backend＋領域再構築＋小変更fast path」を選ぶ。
個人開発で独自の完全PDFインタプリターやAcrobat相当SDKを作る選択は推奨しない。

## 2. 現行pdfengineの技術評価

### 2.1 実装を確認した経路

| 部品 | コード上の責務 | 評価 |
| --- | --- | --- |
| 解析 | [content_stream.py](../pdfeditor/content_stream.py)、[selection.py](../pdfeditor/selection.py) | pypdfによる命令/byte-range解析とMuPDFの観測を対応付ける。曖昧なcode/glyph対応を拒否する点は強い。一方、inline image等は明示的に対象外で、汎用PDFエンジンを全面代替する構成ではない |
| 復元・提案 | [inference.py](../pdfeditor/inference.py)、[page_proposal.py](../pdfeditor/page_proposal.py) | 行/段落/幅・providerを推定し、no-op再現と承認で編集状態を作る。意図したフレーム幅と観測した文字幅は同一ではなく、推定不能時の確認が多い |
| 利用者の編集意図 | [edit_intent.py](../pdfeditor/edit_intent.py) | 前後の共通書記素を残して変更範囲を縮める。選択が複数ownerでも実変更が1ownerなら扱えるが、実変更が複数ownerなら拒否。隣接経路への移行も容量不足時だけ |
| タグ付き局所編集 | [marked_content.py](../pdfeditor/marked_content.py)、[span_edit.py](../pdfeditor/span_edit.py)、[adjacent_span.py](../pdfeditor/adjacent_span.py) | 単一leaf Span、又は限定的な隣接Span関係を証明しActualTextを同時更新する。任意の構造木/段落の編集ではない |
| 再組版 | [paragraph.py](../pdfeditor/paragraph.py)、[rich_layout.py](../pdfeditor/rich_layout.py)、[shared_flow.py](../pdfeditor/shared_flow.py) | 元文字と新glyphを混在させ、禁則/行高/段落間隔/移動/継続を計画。実PDF3ページ履歴で成立する能力はある。普通の未知PDFからこのflowを得る問題とは別 |
| フォント | [shaped_font.py](../pdfeditor/shaped_font.py)、[attributed.py](../pdfeditor/attributed.py) | HarfBuzz、fontTools、TrueType subset、原本code/advance保持、SHA付き代替。新writerはglyf/hmtx/cmapを要求。一般のCFF、Type3、縦書き等を一括して解決していない |
| 所有権 | [source_ownership.py](../pdfeditor/source_ownership.py)、[post_edit_extension.py](../pdfeditor/post_edit_extension.py) | revision/観測/marker/slot/destination/releaseをbinding。未承認領域の混入を防ぐ。一方、文書意図を既存owner/slotへ厳密に結び付けることが適用範囲を限定する |
| 保存 | [transaction.py](../pdfeditor/transaction.py)、[pdf_save.py](../pdfeditor/pdf_save.py)、[editable.py](../pdfeditor/editable.py) | 同一Transactionの除去/配置、資源維持、readback、失敗時の未公開。通常の2ファイル公開はlink順次＋例外rollbackで、クラッシュ時まで2ファイルが不可分とは言えない |
| 別の意味層 | [semantic_writer.py](../pdfeditor/semantic_writer.py)、[semantic_publication.py](../pdfeditor/semantic_publication.py) | canonical island/semantic stateと単一directory renameによるbundle公開が既にある。ただしcritical-pathではT3はA/B/空白/改行の限定経路で、実日本語の一般編集モデルとして完成していない |

これはREADMEだけの評価ではない。[critical path](acrobat-critical-path.md)、
[タグ付き契約](tagged-persistent-editing.md)、主要実装、
`tests/test_edit_intent.py`、`tests/test_single_span_actualtext.py`、`tests/test_single_span_substitute.py`、
`tests/test_tagged_page_proposal.py`、`tests/test_sequential_extension.py`の契約も参照した。
今回はそれらを再実行していない。

### 2.2 実証済みの能力と、実証の範囲

- [PR #76の統合証拠](../evaluations/real_japanese/three-page-final-summary.json)：裁判所97044の選択本文で、
  保存revisionから2回所有範囲を増やし、3ページの長文化・短文化・再編集・no-opが成立している。
  全13ページの二重レンダラー監査もある。**自動で任意PDFを3ページflowにできることの証明ではない。**
- [大阪単一Span置換](../evaluations/real_japanese/osaka-single-span-replacement-report.md)：元glyph保持と新規glyph生成、
  ActualText更新、保存/no-opを実PDFで成立させた。単一ownerと明示領域の限定ケースである。
- [PR #83](https://github.com/YanTKYS/pdfengine/pull/83)：事前固定した大阪府1PDFの14操作で6成功/8拒否。
  `edit-coverage-results.json`のtotalsを直接確認した。230 focused PASSは当該PRの記録値である。
- [PR #84](https://github.com/YanTKYS/pdfengine/pull/84)：同じ14操作で明示代替あり7成功/7拒否、監査失敗0。
  E「標記→本件」だけが追加成功し、「件」に代替を使う。J/K/Mはフォントを解決しても容量/構造で止まる。
  203 focused PASS等は当該PRの記録値であり、重複する再実行件数は合算しない。

6/14=42.9%、7/14=50.0%は**この固定操作集合の達成率**である。
14独立文書でも、無作為抽出した一般PDF標本でもない。一般化した「PDF編集成功率」として使用しない。
同じPDF内のケースは強く相関し、成功数だけでなく拒否層・編集種類別の内訳を読む必要がある。

PR #84では、保存PDFだけから共通APIへ再提案すると複数font resourceで拒否する場合が残り、
成功した再編集はsidecar状態から直接single-Span APIを使っている。
`restored`はpdfengine自身のbindingを満たすという意味で、Acrobatや他製品での再編集、PDF/UA適合の認証ではない。

### 2.3 本質的な強み

- 選択した元文字を実際に消し、周辺命令・glyph・資源を守る検証可能な保存設計。
- extraction上の文字列だけでなく、occurrence/code/GID/CTM/clip/paintを照合するprovenance。
- glyph変更、フォント差、領域外差分、保存後の状態を区別する監査。白塗り重ね描きの擬似編集に陥っていない。
- 元字間/TJの詰めと新glyphの公称幅を区別し、日本語の再組版を実装している。
- 安全に拒否するため、不正な見た目を成功として出しにくい。精密な差分監査器としても価値がある。

### 2.4 現方式のままで改善できること

- 同一revisionの解析、font抽出、shaping、proposal再導出の共有キャッシュ。結果/承認のbindingを維持して重複を減らす。
- 生成font bindingの共通APIへの引継ぎ、拒否理由の層別化、代替font選択のUX。
- bbox単精度とlayout倍精度の境界の整理。既存の局所修正を体系化し、許容差追加を無制限に増やさない。
- CFF等のfont経路を独立した拡張として加える余地。ただしcoverage拡大は段落モデルの問題を解消しない。

記録上のPR #83 case Bは`propose_seconds=11.665`、`saved_seconds=67.260`、Iは11.832/62.466秒。
計測環境・処理区間は当該スクリプトに依存する。単なるページI/O速度ではなく再導出・検証を含む。
プロファイルなしでPythonが主因、又はC++なら何倍速いとは言えない。

### 2.5 大幅な再設計が必要なこと

1. ownerをまたぐ任意の置換と、段落単位の変更範囲計画。
2. Word/Excel/ブラウザの出力断片から、本文・表・固定要素・読み順・フレーム幅を復元するモデル。
3. 原本タグを絶対の配置境界にせず、変更後の意味へ再接続する局所retag writer。
4. 文書flow・ページ容量・段組・表セル・脚注を扱う制約モデルと、曖昧な時だけ確認を求めるUI。
5. 元glyphの永続保持を必須にせず、編集した段落の再描画を許可し、その範囲の忠実性を別基準で評価すること。
6. PDF単体からの再復元とsidecarありの高精度復元を分け、外部編集によるstale状態から回復すること。

現行方式をそのまま積み増しても、対応する契約が増えるほど多数のPDFは扱えるだろう。
**承認済みテンプレート・帳票・限定ワークフローの精密編集には実用化可能性が高い。**
一般的な本文を自由に編集する主経路としては、owner別パターンと再承認が増え、被覆率と保守費の改善が遅いと判断する。

## 3. 代替アーキテクチャの比較

以下の「可能」は既知のAPI能力又は設計上の見込みであり、本リポジトリでの実証とは区別する。

| 方式 | 編集範囲・再組版 | 忠実性・PDF整合性 | 拡張/個人開発/性能/費用 |
| --- | --- | --- | --- |
| A 現行の命令部分編集を主経路に発展 | 証明済みパターンでは強い。未知の出力断片/owner境界ごとに対応が必要 | 元glyphと周辺描画の保持に最も有利。再openは独自状態依存がある | 既知文書には現実的。表・任意flowへの契約追加費用が大きい。現時点の多重検証は重い。AGPL依存あり |
| B 領域/段落再構築 | 復元したフレーム内の自由な置換・折返しに適する。複数ページは別のflow設計が必要 | 編集領域の微細な字間は変わり得る。元文字の完全除去・正しいタグ再生成が難所 | 初期コストは高いがowner分割への依存を減らせる。段組/表は明示IRで発展可能。OSS構成可、性能未測定 |
| C 商用編集SDK中心 | text object編集だけでなくcontent editorを持つ製品は候補。日本語・段落推定・ページ越えの範囲は要実験 | backendの成熟度を買える。元タグ/検索/削除結果の監査は依然必要 | 個人開発の実装負担を最も減らす可能性。対象platform/製品module毎に商用契約。価格・配布権・SDK性能は未確認 |
| D 推奨hybrid | 小変更はA、まとまった文章はB、確信の低い復元は確認又は拒否。backendは交換可能 | 編集領域を段階的に広げて忠実性と自由度を両立する。相互作用の設計は必要 | 長期の制約整理に有利。複数経路の維持負担はあるため、共通IR/監査と限定した入口が必須 |
| E PDF→DOCX/HTML→PDF往復 | 編集後の再flowは得やすいが、原本の意味復元に強く依存 | 文書全体の行/表/改ページ/fontが変わりやすい。局所無変更を保証しにくい | 全文再編集を承認する別モードには有用。局所編集の標準方式には不向き。変換SDKにも費用があり得る |

### 3.1 ライブラリ・SDKは同じものではない

| 技術 | 公式情報で確認できた具体的な能力 | その情報からは言えないこと | ライセンス/費用の判断 |
| --- | --- | --- | --- |
| PDFium | 公開`fpdf_edit.h`にtext objectの`FPDFText_SetText`、page objectの除去/変換、`FPDFPage_GenerateContent`がある | 自動段落復元、日本語禁則、後続段落移動、複数ページstoryの編集エンジンはこのAPIにはない。低レベル編集できることとAcrobat相当は別 | BSD-style本体＋第三者notice。Chromium全体のライセンスと混同しない。独自再組版が必要 |
| MuPDF / PyMuPDF | 公式Page APIにredaction、`apply_redactions`、textbox/HTML挿入がある。既存の解析・レンダラーにも利用中 | redaction＋textboxだけでは元段落・タグ・周辺flowを復元しない。矩形redactionは別ownerも消し得るため標準の選択除去にしない | AGPL又は商用。別プロセス/言語にするだけでは条件は消えない |
| PDFBox | 公式FAQは低レベルpage content APIと高位paragraph/line wrappingの不在を説明している（取得したFAQは2.0向け） | 3.xを含む全機能を評価したという意味ではない。Acrobat型の既存文章編集・自然な再flowを既成機能として見込めない | Apache-2.0。Java/JVMを採る利点はあるが、編集モデルは別途必要 |
| iText Core | 公式READMEはPDF生成/layout、操作、署名、PDF/UA等を列挙。Java/.NET系のPDF構築基盤 | 新規PDF用layout engineが、既存PDFを元文書へ逆変換して再flowする能力を意味しない。追加製品はCoreと分けて確認する | AGPL又は商用。今回の配布条件・見積は取得していない |
| Apryse WebViewer | 公式UI実装にContentEditManager、ContentEdit preset、content-edit font取得経路がある | UIソースは閉じたCoreの組版アルゴリズムを証明しない。日本語全文・ページ越え・タグ保持・headless編集APIは未実証 | 取得したUI LICENSEもactive commercial WebViewer licenseを要求。無料のReact UI＝無料編集SDKではない。module/配布契約は見積対象 |
| Nutrient（旧PSPDFKit） | 公式catalogのcontent-editing font-matching例に`content-editor`、`contentEditingFontMatcher`、customFonts指定がある | 元fontの完全復元や、日本語の長文化・段落連動・複数ページflowはこのサンプルでは証明されない | 商用SDK候補。サンプルの公開ライセンスをSDKの実行/再配布権とみなさない。価格・機能moduleは未確認 |
| Foxit PDF SDK | 公式Web SDK examplesを確認。今回は編集moduleの詳細資料取得が制限され、具体的なparagraph編集APIまで確証できていない | viewerのサンプルだけで本文再flowを実装済みと数えない。PDF SDK/Editor SDK/製品版Editorを混同しない | 商用候補。対象OS・編集module・再配布条件の公式契約確認が必要。今回、数値費用を比較しない |

商用SDKの一次比較は**Apryseのcontent-edit module**を優先する。既存content編集とfont管理の公開接点が確認できるためで、
日本語品質が優れているとの実測判断ではない。Nutrientを第二候補とする。
Foxitも排除しないが、今回取得した情報だけで第一候補にはしない。
許諾型が必須ならPDFium中心に別途検討する。pypdf/QPDFはobject操作/保存の基盤であって、本文の逆組版エンジンではない。

## 4. PDF編集における根本的な技術障壁

| 障壁 | PDF自体の情報欠落/性質 | 現行設計による追加制約 |
| --- | --- | --- |
| 段落・幅・読み順 | PDFは描画用形式。タグ付きなら意味構造がある場合もあるが、Wordのテキストフレームや自動改行規則を必ず保持するわけではない | 観測ownerをそのまま配置容量/編集の単位にし、推定領域より厳しく限定する |
| font subset | 元subsetには新しい文字の輪郭が本当に存在しないことがある。font名から同一programを復元できない | PR #84以前は元programだけを要求。以後も許される代替/形状の契約は限定的 |
| Unicode | ToUnicode欠落/誤り、独自CMap、複数字に1glyph、字形化/画像だけの文章がある | 一意な元code証明/1字1glyphを要求する経路は、より広いPDFの一部を安全に拒否する |
| 再flow | PDF内に元の自動改行・段落境界・表セル関係が存在しないことがある | 原本の改行やMCIDを固定するか、flowを明示承認して初めて移動できる |
| タグ/ActualText | ActualTextは合字やアクセシビリティ上の別表記にも使われ、描画文字と一致しないことが正常な場合もある | 「存在し完全一致」を必須にすると、ActualTextなしの正常な文書も拒否する。これは形式の不可能性ではない |
| graphics | Form共有、clip、透明group、重ね順が文字の見た目を決める | 解釈/所有権で扱えない操作を拒否する範囲が、成熟したbackendより狭い |
| 元ファイル | 複数の作者文書が同じPDFを生むため、元のWord/Excel文書を一意に逆算できない | これは言語/SDK変更でも解消しない。推定・確認・明示制約が必要 |

日本語では禁則、和欧混植、全半角、約物詰め、縦書き/ルビ、CJKフォント代替の字面/字幅差も重要である。
HarfBuzzはglyph shapingを担うが、公式文書でもparagraph、line length、justification全体を解決しないと明記している。
縦書き・ルビ・複雑な表は、最初の実用範囲から外して明示する方がよい。
OCRは画像から文字を推定する別経路で、元のfont/code/意味を復元した証明にはならない。

元のMCID/ownerは**意味への参照と入力のprovenance**であり、利用者が編集できる文字列の恒久的な権限境界である必要はない。
ただし変更領域を広げる承認と、未選択の意味情報を保護する仕組みは必要である。
inline Spanの境界を統合/再作成できても、リンク・強調・言語・ruby等の意味境界まで無断で潰してはいけない。

削除は白塗りではなく対象text-show命令の消費として行う。旧ActualText、hidden text、OCR層、Form内の複製にも注意する。
通常保存で旧表示文字が抽出されないことと、incremental履歴・未使用object・添付・バックアップからの法医学的完全消去は別要件である。
機密削除を宣言するなら、到達可能objectからのfull rewrite/GCと別の消去監査が必要になる。
署名済みPDFの編集は署名検証を変える。暗号化・署名・権限の扱いも、黙って迂回せず製品仕様にする。

## 5. 各方式の編集能力と制約

一般PDFの成功率は現状不明。数値の優劣を作らず、能力を層で評価する。

- **A**：元文字を保持する局所置換は最良の候補。ownerをまたぐ大幅な再flowでは適用契約の管理費が増える。
- **B**：編集領域内の行を作り直せるため、既存のtext object分割に左右されにくい。誤った段落推定・過大な領域消費・retagが主なリスク。
- **C**：既成のcontent editorは復元・font・ライターの開発負担を買える可能性がある。自動跨ページや日本語品質は別に測る。
- **D**：低損失なAと自由度の高いBを共通の意味モデルで選ぶ。誤った経路切替を防ぐため、失敗したguardを単純に無視して別経路へ進む設計にはしない。

### 公平な共通ベンチマーク

**18原本×6操作＝108固定ケース**を目標に、以下の6層から各3原本を登録する。
複数カテゴリに属するPDFは1原本として数え、結果にproducer/tag/レイアウト属性を付けて横断集計する。

| 層 | 候補と不足資料 | 操作上の主眼 |
| --- | --- | --- |
| Wordタグ付き | Osaka guideline/fire notice等、実producerとタグを確認して採用 | 複数Spanをまたぐ本文、ActualTextなし/あり |
| Wordタグなし | corpusのWord系から実観測で選ぶ。なければ公的原本を別途調達 | タグに頼らない復元 |
| LibreOffice | lo_migration_ja等。3原本に満たない場合は追加調達 | 日本語段落/幅/次段落 |
| Excel等の表 | 登録corpusだけで3適格例があるとは確認していない。原本調達が必要 | セル内長文化、罫線/隣セル保護 |
| ブラウザ | print_fcc_chrome等は名称だけで断定せずProducer・実体を確認 | HTML由来の断片化・リンク・段組 |
| 行政/業務の複数段落・ページ | courts_97044、word_okinawa_procurement、別producerの原本等 | 次段落移動、ページ継続。producerの偏りを記録 |

各原本で同文字数置換、短文化、長文化、明示折返し、後続段落移動、複数内部領域をまたぐ置換を登録する。
表ではセル内と別セルをまたぐ要求を区別し、後者の正しい拒否を明示する。
複数ページは行政層で、各操作後の再編集/no-opを含めて確認する。縦書き・スキャンは当初は別の限界集合にする。
**この108ケースは提案であり、今回採取/実行済みではない。**

ケースmanifestは、原本URL/SHA/権利、page、選択occurrence、旧/新全文、期待段落順、許可された移動領域、
保護対象、font選択規則、検索期待値を、どの方式も動かす前に固定する。原本を変換して適格性を改善しない。
同じfontの入手可否・代替承認を全方式へ与え、SDKにも「元のsubsetでは不足する文字」を含める。
タグやownerの手書き宣言を一方だけに与えず、全方式に同じユーザー情報と幾何学的承認を与える。

評価は次を分ける。

1. 変更後全文・自然な行順・禁則・後続配置・検索/コピー・旧文字除去を満たした**利用者タスク成功**。
2. 保護領域の画素/文字/画像/罫線/リンク/注釈、PDF再open、意味構造整合という**安全性合格**。
3. 初回確認/追加確認の回数、必要な手書き指定、PDF単体/sidecarあり再編集という**操作負担**。
4. cold/warmの解析、提案、preview、保存、独立監査を別々に測るp50/p95、peak RSS、対象ページ数。

no-opは同一rendererの前後で全ページpixel-identicalを要求する。MuPDFとPoppler相互の画素一致を要求するのではない。
編集時の領域外は同じ144dpi/事前固定マスクで0差分を要求する。
領域内は再flowして変わるため元glyph位置一致を必須にしない。意図したfont/サイズ/色、形状、読み順、見た目を別採点する。
保護maskは出力差分を見てから拡大しない。移動可能な後続段落は事前に変更領域へ含める。

報告は文書単位/カテゴリ別成功数と操作別成功数、拒否・監査失敗・クラッシュ・要手書きを別集計する。
SDK未入手、font未許諾等は未実行として別欄にし、成功にも安全な拒否にも数えない。
同一原本内の相関を無視した成功率の統計比較はしない。各方式の実装/設定を固定してからholdout原本を開く。

## 6. 推奨方式の技術構成

```text
PDF scene / font / tag observations
          ↓
DocumentIR: story → frame → paragraph → styled runs
          ↕ provenance map（glyph occurrence / object / tag / confidence）
Selection + EditIntent + 承認済み移動領域
          ↓
layout planner（禁則・行高・段落関係・ページ/セル制約）
          ↓
change-set: 消費する元命令 + 新しいglyph/タグ + resource変更
          ↓
一つのPDF writer / staged readback / 独立監査 / publication
```

- **言語**：まずPythonでIRと比較器を作り、MuPDF、HarfBuzz、fontTools等のnative実装を使う。
  細粒度のglyph比較/再解釈がprofileで支配的と判明した時だけC++/Rustへ寄せる。
  C#/.NETやJavaを選ぶ理由は採用SDK/APIと配布環境であり、日本語PDF復元を自動的に解決するためではない。
- **読取り**：MuPDF/PDFium等の成熟scene解析を利用し、必要なsource provenance adapterを付ける。
  現行の限定インタプリターを未知PDFの唯一の真実にしない。タグは有益なヒントで、悪いタグをそのまま真実としない。
- **IR**：作者由来と推定された関係を区別する。semantic span/link/emphasis、固定要素、表セル、footnoteを別に保持。
  観測幅・推定フレーム幅・承認幅も別値にする。低確信の段落/順序はpreviewで確認する。
- **編集**：変更前の構造単位ではなく承認した論理範囲を消費する。fast pathが適合しない時は、
  承認範囲内の領域再構築として**異なる計画を新たに検証**する。元のguardを無効化しない。
- **font**：元programが十分なら利用。subset不足ならSHA/face/埋め込み許可を確認した代替を利用者へ明示。
  段落内の一文字だけのfallbackが不自然なら段落/同一style run全体の代替も選択肢としてpreviewする。
  原本と同じ字形を数学的に保証できない場合は同一fontを装わない。
- **retag**：変更した意味的Pの子Spanを再作成し、MCID/ParentTree/ActualText/読み順を一致させる。
  古いowner IDは外部意味参照がなければ不変要件にしない。未変更兄弟と外部のRoleMap、ID、OBJR、annotation関係等を保護する。
  未知の参照・複雑な意味情報を安全に更新できなければ拒否。単にタグを削除する実装は採用しない。
- **保存**：文書全体を再生成する必要はない。対象content group/resource/tag graphだけ変更し、他page/paintを保存する。
  一つのwriterでstreamと意味情報を更新し、bundleの単一rename等を使う。クラッシュ原子性と例外rollbackを区別する。
- **継続編集**：IRをsidecar又はPDF内のversion付き編集metadataとして保存し、PDF SHA/内容で照合する。
  埋込みmetadataは標準PDF本文/タグの代わりにしない。metadataなしでも再解析して編集できる縮退経路を持つ。
- **性能**：入力revision・page・font・layoutをimmutableな結果としてcacheし、承認はそのhashを検証する。
  全document二重renderはrelease/監査モードへ分離してもよいが、変更領域の検証を省いてよいという意味ではない。

新規repoへの分割はPoCの独立性・schema切替を明瞭にするなら可能。現段階で全コード移植は不要。
AGPL公開を継続するなら現backendを使える。クローズド配布が要件なら、元コードのAGPLとMuPDFの商用条件を含め、
権利者の許諾又は独立実装/別backendを別途選ぶ。言語変更だけでライセンスは変わらない。

## 7. 既存資産の再利用・破棄判断

| 判断 | 対象 | 根拠 |
| --- | --- | --- |
| 再利用 | 原本corpusのmanifest/SHA、二重renderer監査、font/GID/outline/advance検証、保存後readback | どの方式にも必要な結果の証拠になる。既存テストの内部owner期待値までは移植しない |
| adapterとして残す | byte-range provenance、正確なsource除去、Transactionの計画検査、font writer、rich layout、fast path | 実際に使える低レベル部品。新IRをこれらの現在の呼出順へ従属させない |
| 再設計 | owner/MCID中心の編集範囲、paragraph推定と承認、destination容量、sidecar schema/統合入口 | 一般的な編集自由度と現在の契約が衝突する箇所 |
| 主経路から退役 | owner組合せごとに増える専用ルート/文字例外、全処理での重複再導出 | 少数の共通原則に置き換えた方が対応範囲と性能を管理しやすい |
| 参考に留める | T3のcanonical island/厳密意味層 | 局所再構築の発想と公開方式は有用。ただしA/B文字限定の完成状態を実日本語対応として再利用済みと数えない |

原code/GIDの不変は未編集文字に対する強い目標とする。再構築した段落内の全glyphまで必須にすると、
新しいfontやline breakingへの変更を自ら封じる。そこは論理文字・意図した書式・描画品質・検索意味で評価する。

## 8. GPT-6 Astra向け比較PoCの実装仕様

### 8.1 解消する最重要の不確実性

**owner分割に依存せず日本語段落を復元・再組版し、元文字を残さず、局所retagとforeign保護を同時に成立させられるか。**
GUIは作らない。既存の全契約を満たすことをPoCの前提にせず、以下の結果契約を共通にする。

### 8.2 入力と固定する編集

原本は未加工。`evaluations/sources.json`のURL/SHAを使う。

| 原本 | 固定する本文/編集 | 狙い |
| --- | --- | --- |
| Osaka guideline p1、SHA `9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe` | P58の「につきまして、」→「に関して、」（PR #83 Fの実変更複数owner）。別revisionでP58「標記につきまして、」→「標記のガイドラインにつきまして、内容をご確認いただけるよう」。P65をfollowerとして承認 | 短文化と実質的な長文化、跨owner、ActualText/retag、折返し＋follower移動 |
| LibreOffice migration p9、SHA `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3` | 「ラガード、あるいは保守的伝統主義者は…抵抗します。」をA、直後の「変化管理コミュニケーション計画の作成に…蔓延するからです。」をBとして選択。Aの「依然こそこそと、隠れたやり方で」→「依然として」。別revisionで「抵抗します。」→「抵抗します。このため、移行の意義を丁寧に説明し、実際の作業を通じて疑問を解消することが重要です。」 | タグありWordへ過適合しない、日本語flow/後続段落。既存評価とは異なる選択・編集 |

各PDFで2編集要求、**計4要求**。各要求の出力から、変更箇所内の1つの自然な再置換と最終no-opを行う。
計算用の同文字数/短文化/長文化fixtureを追加しても実PDF成功件数には入れない。
PDF内の実テキストとoccurrenceを最初に読み取り専用で確定し、全文/offset/許可範囲をmanifestへcommitしてから各armを動かす。
LibreOfficeの旧文字列と前後段落は原本p9の読取りで確認した。追加文は新たな評価用文章である。

元P58/P65の文字と空間はsceneで確認し、見出し/次sectionまでを勝手に取り込まない。
固定矩形に2行以上の余裕がなければ、文言を成功するまで調整せず容量不足として記録する。
次ページへの拡張はこの最小PoCに入れない。ページ越えの製品要件は108ケース側の次段階で測る。

### 8.3 比較armと実装範囲

- **A baseline**：mainの`propose_edit_intent`/既存paragraph-flow公開API。両armと同じfont・幾何学的承認を与える。
  既存方式で入力不能な場合はその理由を記録する。手書きownerを足してbaselineだけ通すことはしない。
- **B prototype**：Python＋現在のMuPDF/pypdf＋HarfBuzz/fontTools。独立した小さなParagraphIRと一つのregion rewrite adapter。
  現行のnative-code保持はfast pathの参考とし、変更段落は新glyph runとして描画してよい。
  source operator消費と局所StructElem/ParentTree更新を既存の一つのwriterへ計画として渡す。
  仮の白塗り・redactionでforeignまで消す方式、別保存後のtag後処理は禁止する。
- **C SDK spike（評価ライセンス/API入手後のみ）**：Apryse content-edit moduleを同じ入力で利用。
  headlessの既存テキスト編集APIが提供されることを最初に確認する。UI専用しか利用できなければ、
  今回のheadless PoCでは未実行と記録し、低レベルContentReplacerだけで代用して同等能力と称さない。
  SDK不調を補う独自layout engineをこのarmに追加しない。Nutrientを試すなら同じmanifestを使う。

Bで実装するのは横書き1column・通常Span群からなる2段落と局所retagだけ。
Form共有/annotation参照/複雑なActualTextの意味変換が出たら停止条件として報告する。
1人10作業日を初期spikeの上限目安とし、超過時は部分結果・未解決箇所を提出する。
これは工数見積の保証ではなく、全面SDK開発へ拡大させない実験予算である。

### 8.4 成功条件と納品物

- 4要求それぞれで、独立抽出した全文・行順がmanifestどおり。長文化は少なくとも1行増え、followerが許可範囲内で動く。
- 元文字が対象の描画/検索/ActualTextに残らない。単なる削除文字列の全ファイルgrepではなく、occurrenceと到達可能な描画/意味を照合。
- scopeの新旧ID一致は要求しない。変更後の意味的P/Span、MCID一意性、ParentTree双方向参照、読み順、ActualText整合を独立検査する。
- foreignの文字/図形/画像/リンク/注釈と元font資源は保護。新fontは同じ承認規則。edited paragraphの見た目は別比較する。
- 事前固定した領域外のMuPDF/Poppler144dpi差分0、最終no-opは同一rendererで全ページ0。
- 保存PDFを別processで再openして再編集可能。sidecarあり・PDF単体を別結果として報告し、後者も能力を測る。
- 入力SHA不変、失敗時の未公開、stream/structureの単一保存、クラッシュ原子性の仕様を明示。
- 各armのversion/設定/手書き指定/時間/RSSを記録。各requestをcold1回＋warm3回までとし、同じseed/機械で順番を交替。
  少数回のPoCでは個別値・中央値・範囲を報告し、安定したp95を推定したとは扱わない。
  後続の共通ベンチマークでの速度目標は1段落preview p95≤2秒、save p95≤5秒（独立全ページ監査を除く）を仮置きする。
  **製品UXの目標値であって実測値ではない。** 初期PoCが超過しても原因をprofileし、勝手に合格値へ変えない。

納品はmanifest、各armのCLI、4要求の結果表、font/構造/抽出/raster監査、時間内訳、短い採否報告。
SDKライセンスが得られなければCの性能/成功率を創作しない。Bの構造writerが大きくなる場合も、未完を成功にしない。

## 9. PoC結果に基づく採用・中止判断の基準

| 観測結果 | 判断 |
| --- | --- |
| Bが両原本の跨断片変更/長文化を達成し、全安全性条件を満たす | **Bの部分再設計を継続**。次に未使用producerのholdout、表、複数ページへ段階的に進む |
| Bは編集できるがforeign/意味構造/除去の監査が1つでも不合格 | 採用不可。原因が局所的なら修正範囲を提示し、同じケースの成功演出を繰り返さない |
| Bのsource除去/局所retagが大規模なPDF engine再実装を必要とする | 自前rebuild路線を止める。SDK又は別backendのCへ転換する根拠になる |
| Cが4要求を標準APIで安全に達成し、Bより開発負担が低く、日本語holdoutも良好、商用条件が予算内 | **C：SDK中心へ転換**。現エンジンは監査/移行用に退役。SDKの採用契約前に製品platformの条件も確認 |
| Aも同じ入力/承認だけで4要求に成功し、Bに被覆率/操作負担の改善がない | この対象範囲では**A継続を再検討**。現モデルの方が合理的かholdoutで確認し、Bの不要な再設計を止める |
| 全armが元font入手不能/画像化等で止まる | 形式上の限界を製品仕様にする。font代替/OCR等を別承認モードとし、言語変更で解決すると期待しない |

4要求全成功は一般PDF対応の証明ではない。18原本のholdoutを含む共通ベンチマークへ進むための関門である。
既存方式の全面廃止はB/Cの証拠が得られてから行い、未検証の設計案だけで動くfast pathを消さない。

## 10. 判断根拠・参照資料・未検証事項

### リポジトリ内で読んだ証拠

- [現行critical path](acrobat-critical-path.md)、[B2契約](tagged-persistent-editing.md)、[ライセンス方針](licensing.md)。
- [編集14ケース](../evaluations/real_japanese/edit-coverage-cases.json)、[報告](../evaluations/real_japanese/edit-coverage-report.md)、
  [実測JSON](../evaluations/real_japanese/edit-coverage-results.json)。PR #83/#84のbody/変更ファイル/merge commitもGitHub APIで照合。
- [実PDF3ページ統合証拠](../evaluations/real_japanese/three-page-final-summary.json)、
  [Span置換報告](../evaluations/real_japanese/osaka-single-span-replacement-report.md)、[原本manifest](../evaluations/sources.json)。
- 本文§2で列挙した実装/テスト。本文内の結果件数・速度は既存記録であり、今回再実行した測定ではない。

### 取得して内容を確認した公式一次資料

2026-10-20に公式GitHubのraw source/APIを取得。製品の将来仕様や価格を保証するものではない。

1. [PDFium公開編集API](https://github.com/chromium/pdfium/blob/main/public/fpdf_edit.h)、
   [LICENSE](https://github.com/chromium/pdfium/blob/main/LICENSE)：object編集とライセンス。
2. [PyMuPDF Page API原稿](https://github.com/pymupdf/PyMuPDF/blob/main/docs/page.rst)、
   [COPYING](https://github.com/pymupdf/PyMuPDF/blob/main/COPYING)：redaction/挿入APIとAGPL。
3. [PDFBox公式FAQ](https://github.com/apache/pdfbox-docs/blob/master/content/2.0/faq.md)、
   [LICENSE](https://github.com/apache/pdfbox/blob/trunk/LICENSE.txt)：低レベルlayoutの説明/Apache-2.0。
4. [iText公式README](https://github.com/itext/itext-java/blob/develop/README.md)、
   [LICENSE](https://github.com/itext/itext-java/blob/develop/LICENSE.md)：生成layoutとPDF操作、dual licensing。
5. [Apryse ContentEdit preset](https://github.com/ApryseSDK/webviewer-ui/blob/de6fa504d2e1faa13f0085e6074f48b697d740ff/src/components/ModularComponents/PresetButton/buttons/ContentEdit.js)、
   [ContentEditManager接点](https://github.com/ApryseSDK/webviewer-ui/blob/de6fa504d2e1faa13f0085e6074f48b697d740ff/src/core/getContentEditManager.js)、
   [UI LICENSE](https://github.com/ApryseSDK/webviewer-ui/blob/de6fa504d2e1faa13f0085e6074f48b697d740ff/LICENSE)。
6. [Nutrient公式content-editing font-matching例](https://github.com/PSPDFKit/pspdfkit-web-examples-catalog/blob/c62ecef12ef94e54f1db9521518a7da276048156/examples/72-content-editing-font-matching/index.js)：content-editorとfont matcher/customFonts。
7. [Foxit公式Web SDK例](https://github.com/foxitsoftware/FoxitPDFSDKForWeb-examples/tree/main)：viewer統合を確認。本文再flowの成功根拠には使用していない。
8. [HarfBuzz公式usermanual](https://github.com/harfbuzz/harfbuzz/blob/main/docs/usermanual-what-is-harfbuzz.xml)：shapingとparagraph layoutの境界。
9. [pypdf公式text extraction説明](https://github.com/py-pdf/pypdf/blob/main/docs/user/extract-text.md)：抽出の曖昧さ。
   文中の「semantic layerがない」という説明はuntagged一般論として読み、タグ付きPDFにも意味情報が一切ないとは解釈しない。
10. [QPDF公式README](https://github.com/qpdf/qpdf/blob/main/README.md)：PDF変換/構造操作基盤。編集layout engineとしては扱わない。

### 取得制限・未検証

`docs.apryse.com`、`nutrient.io`、`developers.foxit.com`、`pdfbox.apache.org`、iText KB、ReadTheDocs等の一部HTMLページは
環境proxyのCONNECT403で取得不可だった。Apryse公式資料は追加権限での再試行も403だったため、
回避経路を作らず公式GitHubの取得可能な資料へ限定した。取得不可URLの文面を読んだとは記載していない。
Foxitの編集API仕様、各商用SDKの最新module価格/契約、CJK/縦書き/retag/ページ越え能力は未確認である。

この評価ではcommercial SDKをインストール/実行せず、B prototypeも実装せず、benchmark原本も新たに取得していない。
したがって方式別の一般成功率・性能倍率・費用総額は算出できない。
次に必要な証拠は§8の公平な4要求PoCと、SDKの対象platform/API・日本語品質・配布条件である。
