# Acrobat-like 編集アーキテクチャ比較PoC

実編集：2026-10-20、継続編集・最終監査：2026-10-21。Aの基準はPR #85マージ後のmain
`8fa868f`。原本・4要求・許可領域は実行前のcommit `35b06b6`で固定した。

## 1. 判断

**B採用：段落・フレーム再構築を次の開発基盤として段階採用し、Aを精密な部分編集経路として残す。**
今回確認できたBの利点は、大阪府PDFの複数ownerをまたぐ2要求、局所的な意味構造更新、
PDF単体からの再編集である。LibreOfficeの本文編集はAでも実行できたため、既存方式の全面破棄は勧めない。

これは製品完成や本番投入の承認ではない。**初回4要求の品質監査はBが3成功/1失敗、
Aが1成功/1失敗/2拒否**。両方式のDは長い置換文字列全体のMuPDFネイティブ検索に失敗した。
描画・除去・意味構造・領域外保護の検査は、保存された全6結果で合格した。
Dの検索未達を成功に読み替えていない。

Bにも大きな制約がある。今回は人が指定した2段落の領域・字下げ・行間と、明示承認した
Droid Sans Fallbackへの**段落全体の代替**を使った。大阪の後続段落は末尾「す。」だけの行が増え、
LibreOfficeでは明朝体がゴシック系に変わった。自動段落発見、元の書体の維持、任意PDF対応を実証したものではない。

[固定manifest](../evaluations/architecture_poc/manifest.json)、
[機械可読な集計](../evaluations/architecture_poc/results.json)、
[個別監査](../evaluations/architecture_poc/audits/)、
[実装・実行手順](../prototypes/paragraph_frame/README.md)を添付する。

## 2. 比較条件と実装

| 条件 | 固定内容 |
| --- | --- |
| 大阪府 | `word_osaka_guideline.pdf` p1、P58/P65、glyph 132–224。許可frame `[70,334,530,424]`、右端524.5、行間18pt |
| LibreOffice | `lo_migration_ja.pdf` p9、指定本文と直後の段落、glyph 802–1031。frame `[55,512,541,710]`、右端539、行間21pt、段落間baseline gap26.5pt |
| 対象外 | 大阪の添付見出し、両原本の他段落・画像・図形・リンク等。次ページへ拡張しない |
| フォント | 両方式に同じDroid Sans Fallback、SHA `ee38813ea00c3e32add4268fff7fff9e39417b4913cb13be2415164a47807cc2`、明示的な`substituted`承認。Aは元glyphを保持してよい |
| 独立性 | 各要求を原本から実行。4文字列・事前frame・外側raster maskは実行結果を見て変更していない |
| 原本 | URL/SHAは`evaluations/sources.json`。終了時も2原本のSHA不変。preflightの再観測manifestはcommit済みmanifestとbyte一致 |

大阪frame内の対象外文字はglyph225/226の独立した空白のみ。移動後の本文と同じ行に来ても、
それらを消費せず、文字・code・GID・位置・paint stateを独立照合した。不可視の空白を本文の一部として
再取得しない処理が必要だった。対象外の図形・画像まで矩形削除する実装は使っていない。

**A**は既存`propose_edit_intent`に加え、公開page-flow proposal/acceptance、
`replace_in_flow`、`edit_shared_flow`を使用した。代替フォントと変更後文字を全logical styleに明示して
acceptanceまで進めた。フォント候補を見つけられない初回提案だけで拒否と判定していない。
read-onlyのline ID観測adapterを使ったが、Aに新しいowner・機能・guard回避を追加していない。

**B**は独立ディレクトリ`prototypes/paragraph_frame/`に実装した。Python、PyMuPDF、pypdf、
HarfBuzz、fontTools、unisegを使用する。視覚的な行順・字下げから2つの論理段落を復元し、
置換後の文字・元の文字サイズ・字幅・UAX14改行機会・行間・領域制約から再組版する。
段落内に何個のownerがあるかは編集境界にしない。

既存の低レベルprovenance interpreterと検証付きTrueType subset builderは再利用した。
選択した元文字のTj/TJを数値advanceへ置き換え、後続のtext stateを保ったまま実際の文字描画を除去する。
大阪では選択Pを残し、その子Span群を1つへ統合、古いMCIDのParentTree slotをnullにし、
新しい描画・ActualText・読み順を同じwriter/saveで更新する。元のタグ全体の削除、白塗り、
別PDF保存後のタグ修復は行っていない。

元ActualTextと描画の不一致、親Kの順序/重複、外部から選択Spanへの未知の参照、IDTree、
複雑なmarked-content property、言語の混在、非矩形clip等は拒否する。挿入位置のclipが
事前frameを含むことと、各新glyphのinkがframe内にあることを検査する。

別言語や新repositoryへの移行は不要だった。今回の障壁は実行言語ではなく、編集単位と状態契約だった。
ただしBも限定interpreterとTrueType writerに依存するため、未知のForm/CFF/縦書き等への一般化は未実証である。
**Cは未検証**。評価用content-editor API/ライセンスが提供されず、Apryse Python bindingも利用可能でなかった。

## 3. 4要求の結果

「安全性」は文字実除去、正確な描画テキスト、foreign保護、意味構造、再open、二重rendererの検査を指す。
「品質合格」はその上で今回の抽出・検索・禁則・行衝突検査をすべて満たすことを指し、見た目の完全一致は意味しない。

| 要求（変更せず実行） | A | B | 再組版の観測 |
| --- | --- | --- | --- |
| A：大阪「につきまして、」→「に関して、」 | 拒否 | 保存・安全性合格・品質合格 | B対象2行→2行、後続1行→2行。末尾「す。」の孤立は品質改善課題 |
| B：大阪「標記につきまして、」→「標記のガイドラインにつきまして、内容をご確認いただけるよう」 | 拒否 | 保存・安全性合格・品質合格 | B対象50文字→70文字（末尾空白込み）、2行→2行。後続先頭baselineは382.13のまま |
| C：LO「依然こそこそと、隠れたやり方で」→「依然として」 | 保存・安全性合格・品質合格 | 保存・安全性合格・品質合格 | A対象3行→2行、後続−21.6pt。B対象3行→3行、後続先頭594.6のまま |
| D：LO「抵抗します。」→「抵抗します。このため、移行の意義を丁寧に説明し、実際の作業を通じて疑問を解消することが重要です。」 | 保存・安全性合格・**品質監査失敗** | 保存・安全性合格・**品質監査失敗** | 両方式3行→4行。後続A +20.4pt、B +21pt。全置換文のMuPDF検索が0件 |

要求Bは固定文言を自然に折り返しても2行に収まった。旧レビュー§8の「長文化は少なくとも1行増える」
という期待はBについて満たしていない。成功を演出するために幅を狭くしたり、文言を長くしたりしていない。
実際の行増加と後続段落移動は要求Dで確認した。Bの指定文言自体の「…いただけるよう国立…」という
つながりも編集要求のまま保存した。

Aの大阪拒否は、`edit_intent`の実変更が要求Aで2owner、要求Bで3ownerに分かれること、
page-flowの「1つのtree-backed owner」契約と異なるgraphics state、外国空白行とのregion衝突判定による。
フォント不足だけの拒否ではない。Bは大阪で93glyph/9show operator、LOで230glyph/83show operatorを消費した。
AのLOは元glyphを保持するため、Bより原本の書体・短文化の行数に有利だった。

### 検索未達の切り分け

Dの新しい全文は、両rendererの抽出・コピー用文字列と実描画の文字順では正しい。
「実際の作業を通じて」「疑問を解消」等の行内句は検索できるが、「通じて疑問」等の日本語行境界を
空白なしでまたぐMuPDF `search_for`は見つからない。原本LOでも同じ種類の境界
「占め、他のメンバーが」は0件で、行境界に空白または改行を入れた検索はヒットした。
したがってB固有の旧字残存やPDF破損という証拠ではなく、readerのCJK行境界検索制約が有力である。
ただし利用者の「全文で検索できる」品質要件は残るため、Dを合格件数には含めない。

## 4. 保存結果の監査

保存された初回6結果と継続編集10結果について、writerの返却値とは別に以下を検査した。

- 描画glyphの全文と順序が期待どおりで、余分な元文字の描画・検索テキストがない。Bは元fontの対象glyphが残ればforeign censusで失敗する。Aは既存glyph保持を許すが、出力全文は厳密に照合する。
- MuPDF/Popplerの全対象ページ抽出。改行の両端だけを整え、内部空白を全消去しない。原本Popplerがglyphなしで推論する`16% `の空白は「原本抽出を保持する」別候補として記録し、正しい論理段落への再構築も許容する。
- 対象外glyphの文字・code・GID・font名・位置・bbox・サイズ・paint、元font program SHA、図形、画像、リンク、注釈の保全。
- 大阪の全MCIDの一意性、tree-backed association、parent backlink、ParentTree逆参照、ActualTextと描画の一致。選択Pの子以外の意味木は不変。LOは元どおりタグなしを維持し、タグ付き契約を課していない。
- **全ページ**MuPDF/Poppler 144dpiで事前frame外の差分0。大阪1ページ、LO10ページを対象とし、他ページにはmaskをかけていない。
- 非ゼロglyph、可視paint、行のbbox非交差、禁則。別agentが4つのB結果の本文cropも目視し、欠落・明白な衝突がないことを確認した。短い尾行の美観を自動禁則合格と混同していない。

原本に4/15個のdrawing、0/1個のimage、3/0個のlinkがある対象ページについて、それらの保全も検査した。
二重レンダリングだけをPDF/UA認証や任意PDFの完全整合性証明とは呼ばない。

独立レビューで指摘されたActualText、順序、参照、clipのguardを追加した後、既存の初回PDFを再生成せず
`qualify.py`で原本・保存PDFを再観測した。4件とも最終guardを通り、計画文字列/行位置は初回保存記録と一致した。
監査器のP65比較と原本推論空白の扱いを修正した後も、保存を繰り返さずread-onlyで再監査した。

## 5. 継続編集

全て初回PDFを閉じ、**別Python process**で開き直して次の再置換を行った。
要求A「に関して、→について、」、B「内容をご確認→内容を再確認」、C「依然として→なおも」、
D「丁寧に説明→十分に説明」。さらに別processで変更なし保存を行った。

| 対象 | A：PDF単体 | A：sidecarあり | B：PDF単体 | B：sidecarあり |
| --- | --- | --- | --- | --- |
| 大阪 A/B | 初回拒否のため対象外 | 同左 | 2/2再編集・監査合格、no-op全頁差分0 | 同左 |
| LO C/D | 2/2拒否 | 2/2再編集・監査合格、no-op全頁差分0 | 2/2再編集・監査合格、no-op全頁差分0 | 同左 |

AのPDF単体も新revisionのline/glyphを再観測し、明示代替を指定して公開acceptanceまで実行した。
拒否は両件とも`shared-flow-confirmation: source output marker inventory differs from owned slots`。
試験adapterの引数不足や、単にsidecarを渡さなかったという判定ではない。

BのPDF単体は元のPDFやglyph/owner ID、元の本文を必要としない。同じframe・字下げ等の幾何条件と
代替fontは再指定しているため、「PDFを開くだけで完全自動復元」とは言わない。
sidecarありではPDF SHA・再観測した本文・幾何条件も照合する。
D再編集の検索検査は短い「十分に説明」を対象とし、初回Dの長文検索未達が解消したという意味ではない。
Bのno-opはPDF serializerによる変更なし保存、Aは既存shared-flow no-op経路である。

## 6. 処理時間と実装負担

秒。反復のない探索測定であり、cold/warmを統制したベンチマークではない。初回B測定は追加guard導入前で、
最終コードの性能保証には使わない。最終guardとの計画一致はread-onlyで検証した。

| 要求 | A edit-intent | A flow提案・承認・保存（合算） | B解析 | B組版/font準備 | B保存・readback | B合計 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 0.286 | 3.573（拒否） | 0.166 | 0.339 | 0.674 | 1.178 |
| B | 0.276 | 3.177（拒否） | 0.174 | 0.319 | 0.696 | 1.189 |
| C | 0.334 | 92.476 | 0.181 | 0.314 | 0.681 | 1.176 |
| D | 0.309 | 94.592 | 0.210 | 0.363 | 0.770 | 1.343 |

Aのproposal/accept/save個別時間とRSSは未計測。検証時間はJSONに別記したが、最終read-only監査は
既存raster cacheを使用している。初回の全ページ描画込み時間と比較しない。
Bの別process再編集は1.14–1.58秒、A sidecar再編集58.39/64.73秒、A no-op55.40/63.38秒だった。
B no-opは0.016–0.034秒だが、serializerのみなのでAの再計画no-opと処理内容が違う。
これらから一般的な速度倍率や安定したp95は算出していない。

環境はPython3.12.14、PyMuPDF1.27.2.3、pypdf6.10.0、HarfBuzz binding0.55.0、
fontTools4.64.0、uniseg0.10.1、Poppler26.05.0。新規B engineは約340行、
比較・監査・再現補助を含むPythonは約1,000行、focused tests約140行。
既存のprovenance/font部品が支える実装なので、この行数を独立PDF編集SDKの開発工数とはみなさない。
新しい外部ライブラリは追加していない。MuPDF/既存コードのAGPL等の条件も変わらない。

focused testsは`tests/test_paragraph_frame_poc.py`の**12 passed / 1.81s**。
跨Span統合、ParentTree旧slotのnull化、外側兄弟の意味/描画保全、ActualText不一致、
未知参照・言語・marked property拒否、容量不足時未公開、stale sidecar、PDF単体再編集、禁則を含む。
製品コード変更がないためfull suiteと108ケースbenchmarkは実行していない。

## 7. 採用範囲と次の優先事項

Bの最重要な仮説「元owner分割に編集範囲を従属させず、局所retagとforeign保護を同時に成立させる」は
今回の大阪2要求で成立した。LOのA成功、短文化の行数、元書体保持はAの有用性を示した。
**Bを新しい論理段落モデルの基盤、Aを適合する小変更の経路として縮小維持**する。
provenance、font検証、独立監査は再利用し、ownerの組合せごとの専用経路を主戦略として増やす開発は抑える。
現行pdfengineの全面破棄、新言語への全面移植、SDKへの転換を支持する実測根拠はまだない。

次の最優先は、変更範囲を必要な段落に絞り、元fontが使えるrunと不足glyphの代替を分けること。
これにより未変更followerの書体・行数を保ち、日本語の短い尾行や比例字幅を改善する。
続いて日本語行境界を扱うアプリ側検索/コピーと、他viewerでの挙動を検証し、Dの未達を閉じる。
その上で、未使用producerを含むholdout、複数列/表/ページ越え、複雑なタグ・Formを段階的に追加する。

旧レビューの採用中止基準にあるforeign/意味/除去の失敗は今回ない。残った失敗は原本・Aにも存在する
reader検索品質であり、それを区別した開発基盤としての採用判断である。**4要求全品質合格、製品品質、
自動一般PDF編集を達成したとは結論しない**。これらを次段階の明示的な受入条件にする。

## 8. 公開と再現上の制約

Bは同じwriterで本文と構造を作り、staged readback後に単一PDFを同一filesystem上のrenameで公開する。
sidecarはその後に書くため、2ファイルbundleのクラッシュ原子性やfsync耐久性は保証しない。
計画/保存前検証で失敗すれば新PDFを出さず、独立監査失敗のcandidateはlocal評価artifactに留める。

生成PDF・外部font・レンダリング画像・大きなsidecarはcommitしていない。
実行手順とtext-only JSONに原本/出力SHA、本文、各検査、全頁差分、継続編集、時間を記録した。
このPRはPoCと評価資料を追加するもので、既存GUIや製品APIの動作を変更しない。
