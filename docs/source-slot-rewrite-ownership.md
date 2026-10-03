# Source slot rewrite ownership — design and synthetic evidence

起点: `8c4e904d1e7d51e4ba1d8c7e693eaad816059f0f` (PR #31 merge)。
このPRは現行runtimeの観測と次の実装契約だけを追加する。以下のschema、marker、
canonical rewriteは**提案であり、まだ実装されていない**。

**IMPLEMENTATION READY — 次の実装範囲を、既存shared-flowが許可するtext source slotに限定する。**
Candidate Cを採用する。source由来の非描画化とsuffix復元bridgeは初回分を固定して残し、
current textとempty/style witnessだけを1 slot / 1 owned islandとして更新する。
anchorを再描画するordinary editable/document flowへの展開はこの判断に含めない。
その展開はNOT READYであり、後述の独立したdecoration ownershipとdormant契約が必要。
anchorを失ったままtextだけcanonical化することも、失敗時のordinary rewriteへの暗黙fallbackも認めない。

## 1. 現在のroot causeと再現値

[evaluation](../evaluations/continuation/source_slot_accumulation.py)は既存fixtureとwriterを使う。
[summary](../evaluations/continuation/source-slot-ownership-summary.json)にはpage census、slotのcurrent
event binding、全mutationのkind/owner/start/end/consumed/replacement length/delta、owner別の
消費・出力operator censusを収録した。operator censusはTf/Tm/Tj/TJ、文字列を含むshow、
numeric-only TJ、empty TJを区別する。source slot全体の連続所有spanを捏造していない。
各slotのpage番号でpage全体のbytes/operator数を参照し、slot単位ではmutationの増分を測る。
ここでpainting showはfill-only fixtureの非空string operandの数であり、一般PDFのvisibility判定ではない。

既存PR #31の実測はpage 4 +4,420 bytes/+368 operators、page 5 +14,312/+1,117、
合計 **+18,732/+1,485 per no-op**。今回は実原本を再実行していない。

`test_shared_flow.prepared`のAをLONGへ置換し、Bは同じlogical textを維持する。
`edit_shared_flow`は変更対象でないparagraphのslotも最終allocationに従って再描画する。

| stage | page 1 bytes / operators | page 2 bytes / operators | source全体の増分 bytes / operators |
| --- | ---: | ---: | ---: |
| initial | 175 / 19 | 46 / 6 | — |
| grow | 2,258 / 234 | 961 / 94 | +2,998 / +303 |
| noop1 | 4,433 / 449 | 1,915 / 182 | +3,129 / +303 |
| noop2 | 6,608 / 664 | 2,869 / 270 | +3,129 / +303 |
| noop3 | 8,783 / 879 | 3,823 / 358 | +3,129 / +303 |
| second (SHORT) | 10,182 / 1,006 | 3,888 / 359 | +1,464 / +128 |
| second noop | 11,493 / 1,133 | 3,895 / 360 | +1,318 / +128 |
| empty A | 12,475 / 1,222 | 3,902 / 361 | +989 / +90 |
| empty noop | 13,436 / 1,311 | 3,909 / 362 | +968 / +90 |
| regrow | 15,506 / 1,526 | 4,805 / 450 | +2,966 / +303 |

page 3は全段階bytes不変。no-op各段階はMuPDFで全ページ画素一致。Poppler比較や新形式の
no-op不変性をこのsyntheticから主張しない。すべてのre-editは別Python processでcurrent
PDF/sidecarを開き、前回report、前回のin-memory object、過去PDFを入力に渡さない。
font providerは通常の編集に必要な外部assetとして使う。親の測定processだけが前後のrawを比較する。

grow後のno-opは毎回次の内訳になる。

| slot | page | mutations | delta bytes / operators | 意味 |
| --- | ---: | ---: | ---: | --- |
| slot-0 (A) | 1 | 29 | +2,168 / +214 | 29 current glyph showsを再描画 |
| slot-1 (B, empty) | 1 | 1 | +7 / +1 | insertion `[] TJ`をさらに追加 |
| slot-2 (B) | 2 | 11 | +954 / +88 | 11 current glyph showsを再描画 |

noop1のslot-0先頭mutationは旧offset `[138,147)`の`<0001> Tj` (9 bytes)を
2,020 bytesへ置換し、**+2,011 bytes/+214 operators**。
残り28 showのnumeric TJ化が合計+157 bytes/0 operatorsで、+2,168になる。
先頭replacementには29 Tf /30 Tm /29 Tj /2 numeric TJがある。
元のTf/Tm等はmutation範囲外にあるため、そのまま残る。
page 1のpainting showsは30のまま、Tfは33→62→91→120、numeric TJは4→34→64→94と増える。
空slotは`[] TJ` (5 bytes)を`[] TJ [] TJ ` (12 bytes)へ書き換え、後ろのslotだけへ再bindする。

経路は次のとおり。

1. [`shared_flow.edit_shared_flow`](../pdfeditor/shared_flow.py)は`styles.replacement`でfragment全体の
   replacementを作り、owner=slot IDとして`plan_document_edit`へ渡す。
2. [`editable.plan_document_edit`](../pdfeditor/editable.py)はcurrent snapshotとfonts/layout/relationsを
   `plan_editable`→[`plan_paragraph_edit`](../pdfeditor/paragraph.py)へ渡す。sourceには`_generated_block`がない。
3. [`rewritten_event`](../pdfeditor/content_stream.py)は選択した各PaintCharを幅相当のnumeric TJへ変える。
   同じshow内の対象外文字はhex codeとして残し、既存TJ gapも残す。mutationは各showの
   operand開始からoperator末尾だけをconsumeし、周辺Tf/Tm/Tc/Tz等をconsumeしない。
4. 最初のshowのreplacementだけに`ET q BT <new glyph commands> ET Q BT <restore>`を追加する。
   new glyphは1 glyphごとのTf/Tz/Tc/Ts/fill/Tm/Tj。restoreは旧line matrixのTmと、旧show後の
   text matrixまで進むnumeric TJ。ほかのsource eventsは非描画化だけを行う。
5. `bind_document_edit`→`bind_editable`→`_bind_paragraph`は`ParagraphPlan.emitted_glyphs`と
   `IdentityMap`のemitted anchorsから**今回生成したshow**へphysical selection/logical unitsをbindする。
   次回はそのshowだけがparagraph.eventsになり、以前のnumeric TJ、周囲のglyph prefix、q/BT/restoreが残る。

retainedは「旧operatorをその場で残す」という意味ではない。`ParagraphShaper.shape`の
original providerは元font/code/widthを使うが、writerはそれも新しいglyph commandsへ出力する。
追加probeでは同一show内の`PRE ABC POST`のABCと別BTのDEFだけを選択した。
元showのPRE/POSTと別のKEEPは残り、選択した6 glyphは全て新しいemitted anchorsにbindされ、
fontは`/Regular`のまま。first +573 bytes/+53 operators、noop +538/+53。
`reused_code_glyph_count`は新規入力に対するcode_witnessの数であり、retained glyph数とは別物である。

## 2. 現在証明できるもの、できないもの

| bytes分類 | 現在わかること | 削除の権限 |
| --- | --- | --- |
| A: 元PDFのsource・状態・混在文字 | current snapshot、選択glyph、showのchar mapで今回のconsume対象を限定できる | 選択文字の非描画化だけ。元Tf/Tmや未知のnumeric TJを一括削除できない |
| B: current paragraph output | 保存中はemitted anchors、reopen後はcurrent selection/logical bindingでpainted glyphを特定できる | glyphを編集できるが、周囲の非描画prefixを含む連続範囲の所有は証明しない |
| C: 過去の生成outputの非描画履歴 | 前後reportを保持する評価中なら発生を追跡できる | **current PDF + current sidecarだけではA/Dと区別して削除できない** |
| D: 無関係なpage program | current paint/glyph identity、fixed relations、transactionのuntouched検証 | rewrite対象外として保存する。geometryが近いだけで取り込まない |

現行schemaだけでは不十分。新しいpersistent ownership recordが必要である。
snapshotの`binding_provenance='generated-by-pdfengine'`はlogical Unicode→current glyph対応の出自であり、
全source byte列の著作者情報ではない。numeric TJやPRF aliasの形状もCの証明にならない。

| 現在の永続情報 | reopen後の用途と限界 |
| --- | --- |
| top-level pdf/model SHA、slot/paragraph/region binding、source_snapshot_sha256 | 同じrevisionとcaller-confirmed semantic identityを検証。過去bytesの内訳は復元しない |
| paragraph snapshot、selection、logical.units/style bindings、physical layout | current glyph・style・Unicode境界。glyph間のnonpainting operatorの所有区間ではない |
| previous_model_sha256 (flowとeditable binding) | 直前モデルへのdigest linkだけ。モデル内容やmutationを埋め込まない |
| generated_fonts | slot/paragraph owner、subset/provider等によるfont resourceの所有。content bytesの所有ではない |
| insertion_binding、style_slot_bindings、style recipes、typing_style_id | current empty TJと独立style witnessを再検証。古いempty TJや古いstyle prefixの一覧ではない |
| element snapshot、anchors、relations、owned_paints | current paint identityと明示された関係。text envelopeの全bytesを所有しない |
| creation_binding | generated continuation slotだけの作成証拠。source slotにはない。source authorityとして転用しない |
| mutation_map / byte_edits | report/TransactionResultに返る。通常source sidecarの階層全体を走査しても存在しない |

`IdentityMap.from_records`も旧PDFと新PDFの両方から再構築するAPIであり、reportだけ、まして
current PDF/sidecarだけから旧mappingを復元する手段ではない。
sidecarはcaller-trustedな文書で、checksumは署名ではない。提案もそのtrust boundaryに従い、
悪意あるcallerがPDFとsidecarの全証拠を整合的に偽造した場合の暗号学的著作者証明は主張しない。

## 3. Candidate比較

| 案 | 安全性 / boundedness / rebinding | 判断 |
| --- | --- | --- |
| A: Tf/Tm/numeric TJの形状から掃除 | A/Cを区別できず、suffix cursorやstyle witnessを壊し得る | reject |
| B: 全mutation_map履歴を永続化 | 旧revisionの座標・bytesとmapping chainが必要。reportだけでは不足し、履歴と検証コストがsave回数に比例 | 第一候補にしない。外部undo用途は別問題 |
| C: 初回からstable marked text island | 一度だけsourceを非描画化し、以後current outputだけ全置換。current witnessだけで所有を再検証 | 下記のshared-flow text限定契約を採用 |
| D1: markerなしcurrent emitted range manifest | current-only range/SHA/contextで限定できるが、空slot・外部再保存・境界の意味の診断が弱く、offset/hashだけではauthorshipを与えない | Cより強い根拠がなく不採用 |
| D2: text island＋anchor group別paint island | 文字と装飾の異なるz-order/clipを保てる。range数はsave回数でなくsemantic group数に限定可能 | general editableの将来案。dormant anchor契約を先に確定する必要あり |

一回だけ残すsource residueは許容する。新形式のfresh sourceでは
`fixed first-rewrite residue + bounded current text/style output`とし、save count比例をゼロにする。
既に履歴があるlegacyを明示的に再確認してupgradeした場合、既存の未知の履歴も固定baselineとして残す。
それを「元PDF由来と証明した」とは呼ばず、**削除権限を持たないpre-island bytes**として扱う。

## 4. 推奨contract: canonicalization単位と初回処理

所有はslot identityでもparagraphの代わりでもなく、**slotに従属する別のcurrent physical ownership witness**。
logical paragraphは複数slotを持てるのでparagraph単位の1 islandにはしない。
generated continuation slotは現在のdestination contractを維持し、source islandと二重所有しない。

初回は既存ordinary writerと同じ選択glyphの非描画化を行う。その最初のshowの直後だけを次のようにする。

```text
rewritten original show (unselected glyphs and numeric advance retained)
ET
% pdfengine-source-slot-v1 begin <stable hex identity>
q BT <canonical current glyph commands OR typing [] TJ> ET Q
q BT <dormant style witness [] TJ> ET Q   % only required empty styles
% pdfengine-source-slot-v1 end <same identity>
BT <original line matrix Tm> <numeric TJ restoring original post-show cursor>
original suffix operators
```

外側のET、復元BT/Tm/TJ、他のsource eventsの非描画化は初回bridge/residueとしてislandの外に固定する。
以後のmutationはmarker間のbodyだけ。元source showsをもう一度非描画化せず、旧generated showを
numeric TJへ置き換えることもしない。bodyはcurrent glyph groupと必要なempty/style witnessesを
既存formatterで決定的に再生成する。marker自体を更新しない。

初回active glyphはすべて最初のemitted groupにまとまる。複数source text objectsは後ろのものも
選択文字だけを非描画化し、外側を囲まない。同じshow内の対象外文字はisland外に残る。
既存writerの`_style_witness`出力は別q/BT groupだが、その隣接group群全体を一対のmarkerで囲める。
初回emptyでは既存コードの「source show内にslotを足す」分岐をそのまま使わず、typing slotを
island内の専用groupへ置く。元empty showは一度だけresidueにする。

markerは前後にLFを持つ完全なcomment lineで、token/operand/stringの途中には置かない。
検証はliteral/hex stringやinline image内の似た文字列をmarkerとして数えないlexicalな確認を要する。
page /Contentsを既存engine同様に結合したdecoded programを対象とし、Form内部は対象外。
`require_text_object_split`とnesting auditを再利用し、PDF 1.xでq/QをBT/ET内部へ入れない。

初回とreopen時の追加条件:

- splitは既存showの後。pending path/clipがなく、text clipping Tr 4–7がtext object内にない。
  最初の実装ではactive marked content/compatibility scopeを拒否する。原本の未知の構文を推測で補修しない。
- q/Q scopeは同じ位置で保持し、bodyはentry深度を下回らず、exitで同じ深度に戻る。
  body中の許容operatorはwriterが生成するtext/state groupだけ。path/Do/clip/marked contentを含めない。
- inherited CTM/clip/paint stateを同じ位置で使い、既存writerのinverse-CTM座標変換と`allow_clip`を維持する。
  unsupported clip/CTM/stateはrefuse。continuation destinationの境界authorityやCTM compensationを流用しない。
- q/QはCTM/clip/font/text state/color等を戻すが、PDF 1.xのtext matrixを戻すという証明には使わない。
  body末尾はET。外の固定BTがTm/Tlmをresetし、初回に計算したline Tm＋numeric TJがsuffix cursorを戻す。
  空→regrowでもbridgeは変わらない。初回のsplit検証とsuffix event/state比較が必須。
- no-opではstyle順、数値書式、font alias、glyph順、typing witness順が決定的であること。
  timestamp、save番号、乱数をbodyへ入れない。empty witnessesはlogical style ID順に固定する。

islandが同じpageに複数あれば、全recordを同じinput program SHAで検証してから
`MutationProgram`へ一括追加する。重なり・他slotのglyph・marker衝突を拒否する。
同じ元showを二つのslotがconsumeする初回は、選択glyphが別でも拒否する。operatorを二重所有しない。
schedule順からoffsetを推定せず、各markerの最終位置はtransactionのmapping/anchorsから再bindする。

## 5. 最小persistent record案と既存stateとの関係

次の実装ではshared-flow formatを明示的にversion更新する。現行schemaにrecordを黙って足さない。
新形式のsource slotには`source_output`を必須とし、初回前は`state: uninitialized`とcaller-confirmed
snapshot/revisionへのcreation seed、初回後は下のactive/empty共通recordを持たせる。
新形式からrecordが消えた場合をlegacyやuninitializedと解釈してはならない。

```json
{
  "version": 1,
  "state": "owned",
  "marker_id": "<domain-separated SHA256 of flow identity, slot id and creation seed>",
  "created_from": {"pdf_sha256": "...", "paragraph_snapshot_sha256": "..."},
  "current": {
    "program_sha256": "...",
    "range": [123, 456],
    "block_sha256": "...",
    "entry_context_sha256": "..."
  }
}
```

`range`はbegin markerのLFからend markerの終端LFまでの半開区間、block SHAはその全体。
body rangeは検証済みmarker lineから導く。start/endを単独の権限にしない。
marker IDの入力はversion付きcanonical JSONの`[domain, flow.id, slot_id, created_from]`とする。
slot/paragraph/pageは親slot・region・bindingから必須に取得し、重複fieldは増やさない。
PDF/model SHA、previous_model_sha256は既存の親recordを使う。creation seedは不変、currentは毎回上書きし、
revision配列やmutation historyを蓄積しない。recordサイズはslot/style数に依存し、save回数に依存しない。

version 1が上記operator nesting/body grammarを固定するため、同じ意味の自由記述authority fieldは不要。
entry contextは同じinterpreterのBoundary/Stateから作るcanonical JSON digestとする。
CTM、font alias/size、Tc/Tw/Tz/TL/Ts/Tr、fill/stroke、opacity、clip、other graphics state、q depth、
text/marked/compatibility depth、pending path/clipを含む。xrefと絶対operator offsetを含めない。
clipは`State.clip`の順序を保ち、各recordの`at` (xref/operator ordinal)だけを除き、
`rule`と`path`のoperator/args/CTMを保持する。version 1では既存`allow_clip`で証明できる矩形clipに
限定し、text clipping、Form BBox、未知のclip fieldは拒否する。`State.report()`の`font_xref`も除く。
数値型は有限のJSON numberに統一し、負のzeroはzero、object keyはsort、配列順は保持する。
entry/exit比較は同じ関数で行い、許可されたresource交換後もdigestが同一でなければ拒否する。
font graphの同一性/許可された置換は既存generated_fontsとTransactionで別途検証する。
context digestだけでresourceの再所有やre-targetを認めない。
reopenはdigest照合に加え、entry/exitの状態等価とbody grammarを再検証する。

新形式をbindする保存中に、(a)全current glyphのoperatorがbody内、(b)body内のpainted glyphは
当slotのselectionだけ、(c)全empty/style witnessはbody内、(d)foreign paintなし、を検証する。
`_bind_paragraph`のcurrent semanticsを残し、ownership recordでglyph selectionを置き換えない。
semantic whitespaceはglyphがなくてもlogical.units/typing witnessで維持する。

| 既存機構 | 契約上の扱い |
| --- | --- |
| editable model / paragraph snapshot | current Unicode/glyph/style bindingのまま。新recordはその周囲の完全bodyの所有を追加 |
| shared-flow slot/range/allocation | stable slot/paragraph/region identityを保持。textが別slotへ流れても各islandは自身の場所に残り、不要slotはemptyになる |
| generated continuation slot | source_outputを付けず、既存destination binding/creation_bindingを維持 |
| generated_fonts | source/generated font lifecycleは現行のowner/painted usage/reuse検証に従う。Cの消去からfont削除を推論しない |
| element/relations/owned_paints | source fixed paintはisland外でidentity mappingを継続。logical paint roleからbyte deletion権限へ昇格させない |
| Transaction | 全plan検証、glyph/paint/resources、late failure、PDF/sidecar publication rollbackを維持 |
| MutationProgram | 初回は複数text edits、以後は1 island body replacement。型/overlap規則を変更する必要はない |
| previous_model_sha256 | 最終outputで更新する既存の直前digestだけ。reopen時に旧モデルの取得を要求しない |

source fontのaliasは所有fontではない。islandにoriginal font glyphがあってもsource resourceを勝手に
再targetしない。初回残骸のnumeric TJ/empty witnessによるfont選択を考慮し、既存resource監査を弱めない。
全page mutation後の最終program SHAと各island offsetを記録するため、未編集slotのrecordも再bindする。
font/style証拠、snapshot、ownership record、flow sealが整合してからPDFとsidecarをpublishする。

## 6. anchors、fixed paint、empty

[`AnchoredPaintEdit.plan`](../pdfeditor/anchors.py)はsource underlineのpaint operatorをconsumeし、
最初のpath位置へ新しいrect segment群を`q … f … Q`で挿入する。text mutationの位置とは別であり、
元path constructionは範囲外に残る。`_bind_relations`はemitted paint anchorsから新しいsource IDsへ
関係を移す。fixed backgroundは`IdentityMap.map_path`で元construction/paintを保持する。

追加fixtureはtext 2 source events、underline 2 source paths、fixed背景、unselected KEEPを持つ。
first +2,025 bytes/+192 operators、noop1/noop2は各+2,028/+192。
no-opのtextは+1,784 bytes、decorationは+244 bytes/+20 operators。最初のfの1 byteを
245 bytesへ置換し、残り2 segmentのf→nはbyte/operator delta 0だが古いpath constructionを残す。
textとpaint間には残すべきsource programがあり、その全体を1 marker pairで所有できない。

既存shared-flowの`_validate`は`binding.anchors is not None`、decorates relation、logical
decoration_rangesを既に拒否する。したがって今回推奨する次の実装はこの制限を維持できる。
fixed-to-page background/paintは外側に置き、既存element relationのprovenanceを維持する。
general editable/flow_transactionが扱う`owned_paints`は別semantic所有で、text islandへ取り込まない。

general editableの将来拡張に必要な追加証拠は明確である: (1)anchor groupごとにcurrent generated
path construction/paintだけを一つの連続rangeにできること、(2)entry/exit pending pathとclip/z-orderを
保存できること、(3)empty時にもrange/affinity/styleを保持してregrowできるpersistent dormant anchor、
(4)textとpaintの複数islandを一transactionでrollback/rebindするsynthetic実測。
現在のwriterはanchor付きemptyを拒否し、`_bind_relations`は消えたgroupを保存しないため、
textのempty契約だけを流用してこの穴を埋められない。

text-only sourceのempty bodyにはtyping styleの`[] TJ`を必ず一つ置き、必要なstyleごとの`[] TJ`も
island内に限定する。`insertion_binding`、`style_slot_bindings`、style recipes、typing_style_id、
empty_style_idの対応を`bind_empty`で再bindする。active→empty→empty noop→regrowで同じmarker IDと
created_fromを保つ。active時に不要な過去style groupsはbody全体置換により消える。
logical style数に対するwitness数が上限で、empty保存回数に比例して増やさない。

## 7. Fail-closedとlegacy方針

次の場合はoutput PDF/sidecarの両方をpublishせず、理由を返す。

- marker missing/duplicate/malformed、字句上commentでないmarker、owner/slot/page/seedの不一致。
- program SHA、block SHA、範囲、entry context、現行snapshot/model/PDF revisionの不一致。
- current selected glyphがisland外、foreign glyph/paintがisland内、empty/style witnessが範囲外。
- invalid body grammar、BT/ETまたぎ、q/Q過不足、marked/compatibility/pending path/clip、未証明CTM/clip/state。
- overlapping islands、二重所有、二つのslotによる同一source showのconsume、marker collision。
- 不明なanchor/fixed paint ownership、必要なfont/provider証拠不足、snapshot/resource/physical semanticsの不一致。
- 新形式のsource_output欠落、不明version、不正なuninitialized→owned transition。

owned recordが壊れている場合のordinary rewrite fallbackは拒否する。履歴を再蓄積させるだけでなく、
foreign bytesを「次回は所有済み」と再認定する経路になるためである。

| legacy選択肢 | 判断 |
| --- | --- |
| 従来writeを維持 | 旧schemaの互換modeとして許容。ただしgrowth未解決と明示、新形式のKPIを約束しない |
| 再確認要求 | 新形式を要求するcallerの既定。PDF/current semanticsを再確認しfresh seedを作る |
| 次の編集でupgrade | 再確認を経て通常のselected glyph非描画化が安全にできる場合だけ。既存未知履歴は固定して残し、新しく出すbodyだけ所有 |

自動pattern migrationはしない。新形式からrecordを削除してlegacyへdowngradeしたり、
破損recordを捨てて同じ呼出し内で新しいislandを作り直したりしない。

## 8. 実装対象とacceptance matrix

想定する次の実装箇所は新規`pdfeditor/source_ownership.py` (marker/current witness検証)、
`shared_flow.py` (format/version、検証、dispatch、全slotの最終rebind)、`paragraph.py`
(初回islandとcanonical body、emitted anchors)、`editable.py`/`logical_element.py`
(ownership-aware empty/current binding)。必要なら`content_stream.py`の既存Boundary観測を拡張するが、
第二のPDF interpreterを作らない。`MutationProgram`、generated continuation algorithm、font lifecycle、
public APIの変更はこのcontract成立の前提にしない。いずれも**このPRでは変更していない**。

| 次の実装test | 必須の期待値 |
| --- | --- |
| fresh first source rewrite | selected source glyphだけ非描画化、mixed-showの対象外glyph/周辺operator保持、1 stable islandを作成 |
| noop1/noop2/noop3 | body bytes/SHAが全て一致。唯一の変更対象なら全decoded page program bytesも一致、growth=0 |
| second content change→noop | 現current outputだけへ置換し、secondとそのno-opは同一body。初回residueは不変 |
| active→empty→empty noop→regrow | marker/slot/creation identity不変、empty paint 0、typing/style witness再検証、empty no-op bytes不変 |
| multi-style / empty confirmed Tc/Ts | logical style IDsとrecipes、独立witnessを保持。順序決定的、style countを超えて増殖しない |
| retained original font + generated font | 全selected current glyphはisland内。元code/width/glyph originを照合、source font不正再targetなし |
| multiple source events/text objects | 後方source showも選択文字だけ非描画化、current textは1 bodyに集約、unselected suffixを保持 |
| multiple source slots/same page | 同一input SHAから計画。互いのislandをconsumeしない。計画順を変えても同じ最終programとbinding |
| same source show split between slots | overlapとしてrefuse、partial publicationなし |
| fixed unrelated paint/background/annotations | construction/paint bytes、path identity/relation、renderer結果とoutside glyph不変 |
| anchored decoration / owned paint move | このshared-flow版では明示refuse。ordinary anchored APIへisland flagを透過させない。将来D2拡張には上記4証拠が必要 |
| nonidentity CTM/q scope/known clip | 対応を許すものはentry=exit context、suffix Tm/Tlm、clip/paintを検証。未証明状態はrefuse |
| lexical comments / marked content / text clipping | string中の偽markerを認めない。splitがscope/clipを壊すケースをrefuse |
| late failure after multiple slots/fonts | Transaction検証失敗・sidecar publication失敗で両artifactなし、元PDF/model不変 |
| reopen without previous report/revision | current PDF+sidecar+fontsだけの別processで成功、previous_model_sha256先を取得しない |
| tampered record / stale PDF | offset/hash/owner/page/entry context/selected glyph変造をrefuse。単なるchecksum再sealではphysical矛盾を覆せない |
| foreign generated-looking bytes | PRF名、numeric TJ、似たmarkerだけのsourceを削除しない。foreign paint/glyphの混入を拒否 |
| legacy/no record | 旧modeのままか明示再確認。過去Cの削除・自動owned昇格なし |
| generated continuation coexistence | 既存block/authority/creation binding不変、sourceとgeneratedが互いをconsumeしない |

全positiveケースでglyph/Unicode/style、PDF 1.x nesting、font graph、outside paint、rollbackを既存検証に通す。
新形式のno-opはMuPDF/Popplerで全page pixel差0も要求する。bytesのKPIとvisualのKPIは別々にassertする。

## 9. 今回の検証と独立レビューの論点

evaluationの別process re-editは**12回** (shared 9、retained 1、anchored 2)、initial editable savesは親processで2回。
全stageでmutation外gap/suffixをbyte比較し、length/operator deltaとowner合計を照合した。
raw PDF/sidecar/report/fontはignored tmp内だけ。公開summaryはcompact rowsで数値と必要な短い例だけを含む。

helper testsとroot-cause用focused testsの結果・実行commandは
[評価README](../evaluations/continuation/README.md#source-slot-ownership調査)を参照。
full suite、PR #31 Windows実PDF評価、新形式runtimeの実装/検証は行っていない。

Opus 5.5には次を独立レビューさせる: numeric TJ/restoreの因果とmutation集計、current glyph bindingと
非描画bytes ownershipの区別、初回split bridgeを固定する安全性、context digestの正規化とresource検証の分離、
empty/style witnessの完全包含、legacyの非自動upgrade、anchor拒否を含む実装scopeの妥当性。
shared-flow text限定の実装契約には未決の設計選択を残していない。実装後のacceptance matrixを通るまで
no-op growth解消を実測済みと表現してはならない。general anchored編集は別paint ownership/dormant契約が
未証明で、上記4点を検証する独立evaluationが必要である。

## 10. 独立レビュー結果

Claude Opus 5.5の独立レビューはhead `bd56485075bd5f55c08380ae23658c4807fec426`に対して
**PASS — IMPLEMENTATION READY (shared-flow text source slotに限定)**。runtime/PR内容の修正なし。
reopen時のownershipはcurrent PDF/program/block SHA、body grammar、current glyph/witness包含だけで
再証明でき、offset/markerは位置特定であって権限ではないと確認した。固定bridgeはbody末尾`ET Q`、
外側BT＋line Tm＋numeric TJによりislandの内容に依存せずTm/Tlmを戻し、同一text object内の複数slotでも
合成できる。同一pageの複数islandは一つの`MutationProgram`上でoffsetだけにより順序非依存。
同一source showの二重consumeは既存の一operator一owner規則でも拒否される。font ownershipは
`generated_fonts`/Transactionの別経路のまま、`_validate`のanchor拒否とscope限定もコードと一致した。
summaryの数値を再計算して本文と一致を確認し、helper 4 passed、focused 12 passed。full suite・実PDFは未実行。

実装PRで仕様として固定するnon-blocking事項:

1. marker認識: 既存`operators()`はcommentを捨てるため、ID入りmarkerの出現数1と、marker行が
   既存operator span間のgapにあることで確認する。body内の`%`は拒否。regex parserを追加しない。
2. body grammar: `continuation.BLOCK_OPERATORS`同等 (q Q BT ET Tf Tz Tc Tw Ts Tm Tj TJ g rg k) に固定。
   `cm`、`cs/sc/scn`、`gs`を拒否する。`State.fill`は`cs`を`sc`で上書きし文字列operandで持つため、
   entry digestは色空間を証明しない。
3. `entry_context_sha256`は`current`内でも不変量: save入力/出力とreopenで記録値と再計算値の一致を要求し、
   上書きだけにしない。fill/stroke/otherは文字列のまま、負のzero正規化は数値fieldに限る。
4. acceptance matrix: first rewrite→noop1のbody一致を明示する。計画順入替えの期待は
   `reserve_font_alias`の`/PRF{n}`割当が順序依存なため、ownership/binding/semanticsの同一性とする。
5. v1はactive marked contentを拒否するため、page levelで`BDC`に包まれたtagged PDFは対象外になり得る。
   PR #31実原本page 4/5がv1範囲内かは未検証で、そのKPIを約束しない。

## 11. 実装済みsynthetic contract — 2026-10-03

起点HEADは`9cb6e0704f7caf062cbaf1460f0184409c344753`。上記の調査・レビューは修正前の履歴として保持する。
新規`confirm_shared_flow()`は`pdfengine-shared-flow-2`を返す。v1はopen/editとも従来のordinary source
rewriteを維持し、v1を出力する。自動upgradeや過去のsource residueの削除は行わない。
public APIのsignature、MutationProgram、generated continuation algorithm、font lifecycleは変更していない。

v2のsource slotだけに`source_output`を必須とし、確認時は`uninitialized`、最初の保存成功時に`owned`へ移る。
generated continuation slotには付与しない。実fixtureの初回recordは次の通り。

```json
{
  "version": 1,
  "state": "owned",
  "marker_id": "ffc89ff80b121ef6a99f1cbe45f863bbf3f06ce686845922d11a2694279131e8",
  "created_from": {
    "pdf_sha256": "d652096b7b98c3642746de01b8a9cdd18739ca028c49aea17bd6f234350de355",
    "paragraph_snapshot_sha256": "b939f22e116465e9b06ee46281ff5eea17e5ca37b47acc784c8e852ecd10632e"
  },
  "current": {
    "program_sha256": "79ed64db29e4d0ce2f0d7ded400f9ede91e4ef6281a5446a371009630a3b0886",
    "range": [55, 610],
    "block_sha256": "83c0dc80a96f07eb615ab3e52741405067c7bf28aab517d4fc7ec5f610f5501a",
    "entry_context_sha256": "fe8bcc0ff9ac2d2c66406053385fc72cbfb0ff545b45a0ffd252e80db9cd7d6a"
  }
}
```

marker IDはcanonical JSONの`["pdfengine-source-slot-v1", flow.id, slot_id, created_from]`のSHA-256。
完全な`% pdfengine-source-slot-v1 begin <id>` / `end <id>`行を書き、既存`operators()`のspan間のgapだけで
認識する。literal string内の類似文字列は数えず、foreign/duplicate/missing/malformed markerを拒否する。
rangeはbegin直前のLFからend行末LFまで、hashはそのblock全体、置換範囲は両markerを除いたbodyだけである。

bodyは`q Q BT ET Tf Tz Tc Tw Ts Tm Tj TJ g rg k`のみの、閉じた`q BT ... ET Q`群。
`%`、`cm`、`gs`、色空間operator、path/XObject、marked-content/compatibility、入れ子BT等は拒否し、
既存operator nesting auditとContentPageの解釈結果も検証する。
entry contextはStateのCTM/font alias/size/spacing/paint/other/clipとscopeを使用し、font xrefとclipの`at`を除く。
fill/stroke/otherの文字列は保持、有限数値だけを扱い、数値の負zeroを0へ揃える。object keyをsortし、配列順を保つ。
owned後のentry digestはinput/output/reopenで記録値と一致を要求する。

初回だけ、選択したsource glyphを既存`rewritten_event`で非描画化し、最初のshow後に外側`ET`、island、
既存restore logicの`BT`＋line `Tm`＋post-show cursor用numeric `TJ`を置く。mixed showの非選択glyphと他のoperatorは残す。
以後は同じformatterで作ったcurrent bodyを1 mutation (`source-output-rewrite`)で置換する。
外側residue/bridgeは固定し、過去bodyのnumeric TJ履歴を追加しない。既存のremoval/ink/collision guardと、
1 Transaction・verified save・PDF/sidecarのatomic publicationを通す。

### 保存系列の測定

単一source slotのsynthetic fixture（`tests/test_source_ownership.py::lifecycle`）。全値はdecoded bytes。
body SHAの記号はA=`dcd49a66907cb4c121589dea886a845926e0a6a06aa1e6f55e4e616df866a0a8`、
B=`112f3fcf1cb62fff497c985fc581b2d5620846721291d254a667c19d5021fafa`、
C=`e1f094707af8f0786a2941d6f47cba308cc8896fa400e5ced5b2641daf25c8cf`。

| stage | body bytes / operators | body SHA | page bytes / operators | 前stageからのpage delta |
|---|---:|---|---:|---:|
| first | 360 / 42 | A | 654 / 51 | +614 / +46（初回のみ） |
| noop1 | 360 / 42 | A | 654 / 51 | 0 / 0 |
| noop2 | 360 / 42 | A | 654 / 51 | 0 / 0 |
| noop3 | 360 / 42 | A | 654 / 51 | 0 / 0 |
| second | 224 / 28 | B | 518 / 37 | −136 / −14 |
| second noop | 224 / 28 | B | 518 / 37 | 0 / 0 |
| empty | 77 / 13 | C | 371 / 22 | −147 / −15 |
| empty noop | 77 / 13 | C | 371 / 22 | 0 / 0 |
| regrow | 360 / 42 | A | 654 / 51 | +283 / +29 |

first/noop1/noop2/noop3はbodyだけでなくpage program全bytesが一致する。
emptyのpainting showは0、typing `[] TJ`は1件で同じstyleのwitnessと共用する。
全9stageでmarker/created_from/entry context、island前後のbytesが不変。regrowはfirstと同じbodyへ戻る。
no-op 3回は全pageのMuPDF/Poppler画素も一致した。
[新実装のcompact evidence](../evaluations/continuation/source-output-canonical-summary.json)に全SHAとoperator内訳を記録した。
PR #32の`source-slot-ownership-summary.json`、調査script/testは変更していない。

### 検証範囲

| 項目 | 確認内容 |
|---|---|
| multiple slots | 全recordを同じinput revisionで検証し、commit後に全slotをfinal programへrebind。no-opの全3page bytes不変。計画順入替えはslot/marker identity・Unicode・画素が一致し、各physical bindingをfinal programで検証（font alias順によるbytes差を許容） |
| original/generated font | retained original code＋新fontを一つのbodyで検証。元font resource不変。通常shared flowのgenerated font ownershipは別経路、no-opで増殖なし |
| mixed show/multiple BT | 対象外glyphのcode/origin/font保持。後方eventの選択部分も一度だけ非描画化。同一source showの二重ownerは拒否 |
| CTM/q/clip | 非identity平行移動、q scope、証明済み矩形clipでcontext・no-op bytes・画素を保持 |
| scope/paint | active BMC/BX、pending path/clip、text clippingを拒否。fixed background relation、対象外画像・annotationを保持 |
| tamper | reseal済みrecordの欠落/version/identity/hash/range/context/owner改変を拒否。byte hashを更新したbodyへのcm/path混入、entry context変更も拒否 |
| containment | 全selected glyphとempty/style witnessのbody包含、foreign glyph不在を要求。anchor/decorates/logical decoration_ranges拒否を維持 |
| coexistence/reopen | 同じpageのsourceとgenerated continuationを共存。destination authority不変。current PDF/sidecarとfont assetだけの別processで再編集成功 |
| rollback | final rebind、output context検証、sidecar publicationの失敗で両artifactを公開せず、元PDF/model不変 |

general editableへの展開は**NOT READY**。PR #31原本のWindows評価は依頼どおり実施していない。
page 4/5のactive marked content等を含むeligibilityは未確認で、実PDFの+18,732 bytes/no-opが解消したとは主張しない。

engine digest（`pdfeditor/*.py`のfilename＋NUL＋file bytesを名前順でSHA-256）:

- before: `2e0ad53bdd617280ec390679559113f41a227be9b91cfab02d705f4f08cf2c45`
- after: `d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`

最終test結果と再現commandは[評価README](../evaluations/continuation/README.md#source-output-canonical実装--2026-10-03)に記録する。

## 12. 実装の独立レビュー結果

Claude Opus 5.5の独立レビューはhead `8d72d3b6233ea7ea16328ec56e4f46560730cd47`に対して
**PASS — READY FOR WINDOWS EXTERNAL VALIDATION**。runtime/test/evidenceの修正なし。
ownershipはcurrent PDF＋sidecarだけから、record、program/block SHA、operator span gapのmarker、
`q BT ... ET Q`の構造grammar、glyph/witness包含で再証明され、markerは位置特定であって権限ではない。
所有検証失敗時のordinary rewrite fallbackはなく、v1は自動upgradeしない。
初回とowned rewriteのbodyは同じ`_source_body()`から出るため、first=noop1は構造的に成立する。
marker外のresidue/bridgeは消費されず、restoreはline Tm＋numeric TJでTm/Tlmを明示的に戻す。
entry contextはinput・body・output・reopenで記録値と照合される。矩形clipはcontextの形状条件と、
writerが全glyph inkに行う既存`allow_clip`の組み合わせで証明され、既存契約より弱くない。
同一source showの二重ownerは、v2 confirm成功後にruntimeの`MutationProgram.add`
（one operator cannot have two owners）で拒否されることを再現で確認した。
font ownershipは`generated_fonts`/Transactionの別経路のまま、generated continuationとの共存、
late failureでのatomic rollbackも確認した。full suiteの4 failuresは期待値を更新した4 caseと一致し、
更新後のassertは位置の単調増加から等値へ強まり、authority assertは維持されている。

レビューでの再実行: `tests/test_source_ownership.py`、`tests/test_generated_block_canonical.py`、
期待値を更新したboundary/clip/scopeの3 test関数で**72 passed / 1 skipped**（PopplerがレビューWindows環境に未導入）。
engine digest `d22fb048…`とsummary値の一致を確認した。full suiteとPR #31実原本は再実行していない。

Windows external validation以降で扱うnon-blocking事項:

1. v2 confirmは自分の記録にないsource markerを拒否し、confirmは常にv2を作るため、v2で編集したPDFの
   sidecarを失うとそのpageを再確認できない。安全側だが運用上の制約として記録する。
2. first=noop1はidentity/平行移動CTMでのみ実証。縮小など逆行列が割り切れないCTMでは、Tm基底の12桁書き出しと
   再読込の往復でbodyが一度ずれる可能性がある。実原本検証でbody一致を確認項目に含める。
3. `inventory`は、2 islandのend行とbegin行が直接隣接すると共有LFのためoverlapと判定し、operand間の
   marker風commentは検査しない。現行writerでは起きず、いずれも権限を与えない。
4. `_source_output`は`edit_document(**overrides)`から届き得る。ownerが必要でeditable sidecarに記録も残らないため
   ownershipは成立しないが、shared flow外では明示拒否するとよい。
5. 作成時点でemptyのslotは複数slot testで間接的にのみ通過。Poppler画素比較はWindows検証で再確認する。


## 13. Windows external validation — 2026-10-03

起点・評価runtimeは`9d70d8c6de7736aba194573ea482dcde7ed9e31a` (PR #33 merge)。
新branch `codex/source-output-windows-validation`で、PR #31と同じ未加工LibreOffice PDFと
msmincho.ttc face 1 / times.ttf face 0をSHA照合した。runtime digestは
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`で固定し、`pdfeditor/`は変更しない。

[source-output external evaluator](../evaluations/continuation/source_output_external.py)はPR #31の
`evaluate.py` / `boundary_destination.py`の確認・logical edit・全独立監査をそのまま再利用する。
新しいv2確認を未加工原本から行い、source slotsはuninitializedで開始する。古いv1 sidecarは使用しない。
source ownership判定はruntimeの`initial_context()`、`open_shared_flow()`、`inventory()`、`grammar()`に委ねる。
既存lifecycleのgrow/second/shorten直後にno-op branchを追加し、次のsemantic saveに進む前に検証する。
各no-opはbody・page program・record全体、entry context、prefix/suffix、owner別mutation deltaを照合する。
page 6は従来のgenerated continuation contractと、既存のboundary
`boundary-b848698b464255ff0b2b6f90` (offset 17,602)を使う。

PHASE 1の両系列とも、page 4 / slot-0、page 5 / slot-1は**eligible**。
source event数は11 / 28、first show rangeは[17552,17592] / [71,93]。
両方ともidentity CTM、marked-content/compatibility depth 0で、既存text-object splitと
source-output initial contextが成功した。clip、fill/stroke、q depth等の完全な証拠は
[新summary](../evaluations/continuation/source-output-external-summary.json)に記録する。

**PASS — WINDOWS EXTERNAL VALIDATION**。両系列ともgrow → first-noop → second → second-noop →
shorten/dormant → dormant-noop → regrow → noop1 → noop2 → noop3を通過した。
各系列10回、合計20回のshared-flow verified save（source replay出力は別）でruntime変更なし。

| source page | grow / first-noop / second / second-noop / regrow / noop1–3 body bytes / ops | page bytes / ops | shorten / dormant-noop body bytes / ops | page bytes / ops |
|---|---:|---:|---:|---:|
| 4 / slot-0 | 4,157 / 364 | 31,409 / 1,921 | 269 / 28 | 27,521 / 1,585 |
| 5 / slot-1 | 13,664 / 1,113 | 41,494 / 2,787 | 140 / 23 | 27,970 / 1,697 |

active body SHAはpage 4が`c4ba7309e14464edd4e78b177cb1403a98da93ce2cf8bf4f1e4558a3c3c85d05`、
page 5が`a46ba69a8d492987baf358866730fe4df043ee60d8e211821e6c08b3639cfe34`。
両系列でbodyが一致する（flow別marker identityのため系列間のpage SHA同一は要求しない）。
secondの変更箇所はpage 6側で、source bodyはgrowと同じ。shortenではpage 4に短文、page 5にempty/style witnessを保持する。

全6組のno-op比較でsource body・全page programがbyte identical、record全体も一致した。
全stageでmarker/created_from/entry contextとmarker外prefix/suffixを保持する。
初回source-output-createのmarker外部分は、page 4が前61 bytes (`TJ ET`)＋後53 bytes (`BT Tm TJ`)、
page 5が前30 bytes＋後52 bytes。初回だけのresidue/restoreで、以後はbodyだけを各slot 1件の
`source-output-rewrite`で置換する。各mutationの消費/置換lengthとbyte/operator deltaをpage deltaへ照合した。

| no-opごとのsource増分 | historical PR #31 | current v2（両系列・全no-op） |
|---|---:|---:|
| page 4 | +4,420 bytes / +368 operators | **0 / 0** |
| page 5 | +14,312 bytes / +1,117 operators | **0 / 0** |
| 合計 | +18,732 bytes / +1,485 operators | **0 / 0** |

no-opの全10ページでMuPDF pixel equal、Poppler 144dpi changed pixels 0。
semantic editでは従来のplanned region＋1pt raster margin外のPoppler差分0で、固定pageの画素を維持した。
独立Unicode/CID/GID/W、source/generated glyphのcode/font/originを照合し、保存座標の最大差は
0.0000244140624943pt（従来許容値0.002pt）。source paint・画像・annotation・元font resourcesを維持した。
page 4/5/6のgenerated alias数は1/2/1、Type0と所有font rootは4、font graph objectは24。
no-opで全てreusedとなり、font record/countは増えない。

page 6のgenerated blockはgrow/regrow/noopが3,044 bytes / 259 operators、secondが3,224 / 273、
dormantが310 / 33。それぞれの直後no-opで不変。destination ID、slot ID、creation binding、marker pairと
元page6 prefix/suffix・boundary authorityを保持し、source mutationとのoverlapなし。全保存のnesting違反は0。
page-entry / boundaryの保存glyph原点も一致した。

追加のWindows synthetic probes:

- scale+translation `0.83 0 0 0.91 7.25 11.5 cm`でfirst=noop bodyは661 bytes / 42 operators、
  pageは1,005 bytes / 56 operators。body SHAは`7e8c44dc6ab692f07ea03e71d419b997465195dcd154775da12d684e4dc9ec7f`。
  entry contextと同じ原text object内のKEEP suffixのgraphics state・Tm/Tlm・code/originを保持し、両renderer差分0。
- 作成時emptyはuninitialized→ownedを通り、painting show 0、typing `[] TJ` exactly 1、必要style witness 1。
  first-empty=empty-noop bodyは80 bytes / 13 operators、pageは374 bytes / 22 operators。
  body SHAは`ecdabe4257c99f1d90c653c4af926f72a017c0b02b8c5345ac191edb9a92c7c3`、両renderer差分0でregrow成功。
- ordinary `edit_document(**overrides)`へ`_source_output`を渡してもownerはNoneのままで、
  `source output requires a text-only owning source slot`を返し、PDF/sidecarを公開しない。
- v2 sidecar喪失後、marker付きPDFから同pageをfresh sourceとして再confirmすると、
  `source output marker inventory differs from owned slots`で拒否する。
  **sidecar loss後のautomatic reconfirmは行わない**。markerを削除して回避する運用はこの検証に含めない。
- 現行writerの同page・2 source islandsでは間に91 bytesあり、end/beginは直接隣接しない。
  no-opのpage bytesと両rendererが一致。generic marker parserは変更していない。

[実行環境・command・tests](../evaluations/continuation/README.md#source-output-windows-external-validation--2026-10-03)も参照。
過去のPR #31/#32/#33の証拠は保持し、今回の結果で上書きしない。general editableは引き続き**NOT READY**。
