# 複数の論理要素と明示した追従関係

この段階の構造的な不足は、glyphが消えた後の段落の存続から、**別の段落との関係をPDFの再保存をまたいで維持すること**へ移った。従来の単一paragraph sidecarには、Aを長くした結果Bを動かしてよいという根拠がない。下にある文章を自動で動かせば、表セル、日付、図のラベルまで変更する危険がある。

`pdfeditor.document_flow` は既存の再描画・移動backendの上に論理documentを置く。利用者が確認したID、固定コンテナ、`follows`、paint所有関係を保持する。PDF解析から取り込んだ物理bindingは、それらの意味をPDFへ対応付ける証拠として扱う。

## 判断と実装範囲

| 候補 | 今回の判断 |
|---|---|
| 複数logical element | 採用。IDはdocumentの辞書キーであり、glyph数・文字列・座標から生成し直さない |
| 明示したfollows | 採用。1ページ、1固定領域、1回に1段落を編集。循環なし・各要素の先行要素は最大1つ |
| Container | 利用者指定のusable bounds、children、固定寸法、overflow拒否を保持。paddingはunknown |
| 全ページflow | 未実装。領域拡張、周囲の未選択paragraph、改ページ、表全体の再組版は扱わない |
| backend / 言語変更 | 今回の不足には不要。既存のsource operator writer、HarfBuzz、paint単位の検証を再利用 |
| source applicationへの復帰 | 今回の障害を解決する必須条件ではない。依存を追加しない |

## 意味と物理binding

```
document / exact PDF SHA-256
  container: usable bounds, explicit children, padding unknown
  elements:
    A: identity + paragraph binding + extent + explicit owned paints
    B: identity + paragraph binding + extent + explicit owned paints
  follows: A.last_baseline + confirmed gap = B.first_baseline
```

各bindingは従来の`pdfengine-editable-2`を使う。非空では元コード・GID・glyph occurrenceを、空では既存の非描画`[] TJ` slotと独立した書式recipeを保持する。上位のparagraph identity、contained-by、follows、paint所有関係に空専用の意味型を増やしていない。

extentには**最終baseline**を使うという明示した方針を採った。gapは最終baselineから次要素の先頭baselineまでの距離。空のparagraphは先頭baselineを一つ確保する。これはglyph bboxの高さやCSSのmarginではなく、今回対応する組版契約である。空を完全に詰める、空でも複数行を確保する、異なる段落間隔を適用する方針は未対応。

入力時のbaselineはsource observation、width/bounds/関係はcaller confirmation、再組版後の行はgeneratedとして出所を分ける。既存の高さ差から関係を自動生成しない。評価者が「この2段落を結ぶ」と判断してから、現状の間隔を確認値として渡すことはできる。

各要素に独立した明示widthが必要で、observed content widthを利用可能幅へ代入しない。コンテナboundsは利用可能領域の直接指定であり、元PDFに同じsemantic boxが存在したという主張ではない。

## 変更の計画と保存

1. PDF hash、各paragraph/paint binding、ID、重複選択、所有paint、グラフ、コンテナを検証する。
2. `plan_paragraph`が実writerと同じ`ParagraphShaper`・`layout_attributed`で最終baselineを計算する。描画を許可する処理ではない。
3. 長文化なら明示した子孫を末尾から移動して場所を空け、Aを編集する。短文化ならAを編集してから子孫を先頭から移動する。
4. 各操作で元コードno-op、対象外glyph、paint plan、font、clip、対象外画素など従来のguardを実行する。writerの行計画が先行計測と一致することも確認する。
5. 操作ごとにすべての既知要素のbindingを更新し、最終PDFで関係とコンテナを再検証する。
6. 検証済みの通常PDFとsidecarを新しい出力先へ公開する。失敗時は自分が公開したファイルを取り除く。

API実行時の描画検証はMuPDFを使用する。Popplerと独立した全ページtext extractionは外部PDF評価コードで追加実施する。APIの成功だけで未知のPDFに対する全renderer同等性を保証するものではない。

一時的な段階では関係式が成立しないことがあるが、各PDF操作の安全検査は継続する。逐次操作の途中で衝突する経路は、最終配置だけなら可能でも拒否する。自由な多要素移動のsolverではない。

複数ファイルのOSクラッシュに対する原子的保存は保証しない。PDFだけ残った場合、それ自体は通常PDFとして使用できるが、正しいsidecarなしに以前の意味で編集を継続しない。

複数要素の検証では、同じrevisionのpath削除証明を何度も要求する。沖縄県PDFの対象ページには89個のpathがあり、これが評価時間の主な要因になった。`proof_session`は一回のimport/edit/openの内部だけ、PDF全体のSHA-256と全入力が同じ証拠を再利用する。別revision、別入力、次の操作では再検証する。返す辞書は独立したcopyで、catalog/renderer observationは各4件、element snapshotは8件、path proofは256件の上限を持つ。入力の変更、戻り値の改変、曖昧な重複paintが再利用で受理されないことをテストした。新しいrevisionに必要なcounterfactual自体は省略しておらず、対話操作の速度に達したとは評価していない。

## 要素を見失わないための証拠

未編集要素は、Unicode、GID、font、size、fill、opacity、origin/bbox等が期待する不変値または明示した平行移動と一致する、唯一のglyph occurrenceへ結び直す。元font resource名・文字コード・`/W`幅も比較する。近い位置の文字を選び直す処理ではない。複数候補があると拒否する。

PDF全体の再保存でobject numberが変わることがある。font xref番号の一致は要求しないが、下位writerのfont dictionary/program fingerprint検証と、上位のresource/code/width/書式の比較は維持する。

空のslotはwriterが返すbyte mutation mapから位置を追跡する。別要素の変更がslotを飲み込む場合は拒否する。更新後のslotのgraphics stateは、許可した平行移動以外変化していないことを検証する。

pathは元の描画順とsource operator、証明状態、全paintの値を比較して追跡する。所有paintだけが計画した平行移動を持ち、それ以外は不変でなければならない。曖昧な重複paintをこの対応付けで救済することはない。

## Paint所有と背景

`owned_paints`はcaller-confirmedな所有関係。`paint_relations`の`fixed-to-page`は段落の文字編集中、そのpaintを伸縮しないという下位backendの指定である。上位のrigid moveでは所有paintに限り`fixed-to-element`を渡す。二つの意味を区別する。

固定の共有背景はAとBの双方から参照できるが、どちらにも所有されていなければ動かない。所有paintは複数要素で共有できない。paintの任意伸長、backgroundの内容量に合わせた拡張は未実装。

文字だけを移動する場合、元font bboxが別行と重なるだけでは拒否しない。完全に選択したtext operatorを移動し、対象外glyphとpaintの不変を検査するため、位置ベースredactionとは条件が異なる。一方、背景などを持つ移動groupが未選択文字を含む場合の拒否は維持している。移動**先**のglyph/path/image/clip/annotation検査も解除していない。

下線の文字範囲追従は既存単一paragraph経路で継続して使用できるが、この複数要素APIではanchor specificationをまだ統合していない。特に空になった装飾をどう保持するかは別の意味契約が必要である。

## API

`confirm_document(source, elements, container_id=..., bounds=..., page=..., follows=...)`
はsource PDFを変更せず、明示確認した入力からdocument modelを返す。各elements項目は次の形を取る。

```python
{
    "paragraph": reviewed_paragraph_snapshot,
    "layout": {"width": 196, "max_bottom": 155},
    "fonts": {"s0": {"path": "C:/Windows/Fonts/arial.ttf"}},
    "paint_relations": reviewed_fixed_paint_relations,
    "owned_paints": [],
}
```

`paragraph`は`inspect_paragraph`の結果を確認したもの。任意のPDFで同じ数値やglyph IDを使えるという意味ではない。必要なline joining、style、width、container、paint relationは入力ごとに確認する。

`edit_flow(source, model, output, model_output, element_id, edits)`
は論理IDとUnicode区間で編集する。再選択や関係の再推定はしない。`open_document(source, model)`が`restored`の場合のみ進み、外部保存・破損時は`needs_confirmation`となる。sidecar checksumは署名ではなく、caller-trustedな編集用データの整合性検査である。

## 評価と次の境界

再現コードは`evaluations/document_flow/evaluate.py`。source PDF・glyph log・sidecar・PNGは公開せず、取得元とSHA-256、評価入力、集計結果を公開する。単体テストには所有背景を持つBの追従、A/B双方が空になるライフサイクル、3要素chain、関係なし・循環・重複所有・領域overflow・固定物衝突・外部保存・公開途中の失敗を含む。

実PDFの結果と残る拒否理由は`evaluations/document_flow/summary.json`に記録する。source operator no-opと、新fontでの文章変更後のno-opは区別して報告する。元subsetと同じ名前のfontを指定しただけで元描画同一と見なさない。

| 外部原本 | 結果 | 確認した境界 |
|---|---|---|
| 沖縄県・Word由来 | 8段階すべて成功、3負例を安全に拒否 | 長短変更、空Bの移動、A/B双方の空状態、再入力、生成後no-op |
| FCC・仮想プリンタ由来 | 長文化とB追従は成功、次の短文化を安全に拒否 | 狭いbaseline gapで移動先のfont bboxが重なる |
| 新潟県・Word由来 | 原本A/B no-op成功、document取込を安全に拒否 | 暗号化PDFに対するplaintext sidecar保存方針 |

沖縄県PDFでは2ページ目の番号付きparagraph (7) をA、(8) をBとして確認した。明示幅468ptでAが1行から2行へ自動折返しすると、Bだけが14.784pt下がり、短文化で戻る。Bを空にして同じ移動を行い、次にA/B双方を空にし、両方を元のIDで再入力できた。各保存後に関係・コンテナ・Unicode内容を復元し、独立pypdf抽出で旧文章の除去を確認した。全8段階でPoppler 144dpiの編集範囲外差分は0、MuPDFの対象外画素・対象外ページ、固定path、画像も不変だった。最終の生成済みparagraphのno-opは両rendererで対象ページ全画素が一致した。長文化・双方空・最終再入力のページ画像も目視確認した。

変更文字には評価者が確認したMS Minchoのfull fontを使い、置換全体のstyleを明示した。元の番号後のArial空白もこの指定に従うため、未変更原本と全書式が同一という評価ではない。未変更のBを動かす際は元resource・code・GID・幅を維持する。関係を除いた入力、コンテナoverflow、外部で再保存したPDFは、いずれも新しいPDF/sidecarを作らず拒否した。

この外部PDFセットでは、B所有の背景pathを伴う複数要素追従は未評価。所有背景の移動と空状態の組合せは単体テストで、従来の実PDFによる単一group移動は`evaluations/elements`で別に検証している。固定背景を保持した今回の成功と同一視しない。

最終coreを固定した回帰テストは **397 passed / 2 skipped（312.34秒）**。skipは任意の暗号providerがない環境でのAES-128/AES-256保存テストである。FCCのg1生成は操作内証拠cacheと移動後provenance表記の追加前のcoreで行い、最終coreでそのPDF/sidecarの再openと同じg2拒否を再確認した。新潟県の取込拒否も前のcoreの実測として、各実行のhashを集計に残す。沖縄県の全8段階は最終coreでの結果であり、異なる実行を一つの再実行結果として扱わない。

`edit_flow(..., edits=[])`もparagraphの組版処理を通るため、未正規化の原本全般に対する画素no-opではない。原本の同文描画には`replay`経路を検証し、今回の最終no-opは生成後の同じ文章・同じ組版条件を対象とする。

FCC仮想プリンタPDFでは、英文本文Aを4行へ増やし、例示行Bだけを14.402pt下げる編集が成立した。共有の黄色い背景、外枠、フランス語の下線、図・画像は固定のまま。次の短文化ではBの移動先がAのfont bboxと交差し、PDFとsidecarを公開せず拒否した。確認したbaseline gapは9.837677pt。Aの観測bbox下端107.492386ptに対し、短文化時のBの想定bbox上端は107.049465ptとなる。この約0.443ptの重なりを実際の輪郭の衝突と判定したわけではなく、現行の保守的なbbox検査の境界である。無関係な文字を動かしたり、移動先のguardを解除して通してはいない。

探索時には、変更後のfont bboxに対して狭すぎた評価者指定領域と、近接する固定下線の関係未指定でも拒否された。最終評価では、目視確認した共有背景内の領域と固定paintを明示している。これらをbackend破損として数えず、確認入力不足と区別した。

新潟県Word PDFの候補は暗号化されており、従来のplaintext sidecar拒否が適用された。これは文字再描画能力の不足とは別の保存方針による拒否で、暗号化を解除して評価を通す変更はしていない。

次の判断材料は、明示relationを持っていても解けない障害の内訳である。密な文書でのfont bboxと実inkの違い、文字と装飾の可変寸法、共有containerを伸縮するときの所有境界、逐次writerでは成立しない同時配置を分けて考える。今回の成立をそのままページ全体reflowの成立とは扱わない。
