# Anchored editable paint ownership — design and observation

2026-10-03 / base `0e468ff7fd3abd76612b5de6cffd2444f8135a2f` (PR #34 merge).

**NOT READY.** 推奨単位は **anchor groupごとの独立したpaint island**。
現行runtimeで生成pathの再bindはできるが、current PDF＋current editable sidecarだけでは
生成bytesのauthorship/envelope所有権を再証明できない。特にpaintゼロのgroupを保持する経路がなく、
非identity CTMでは同じno-opでも生成paint payloadが変化した。以下は実装候補を具体化した設計であり、
§12の未証明事項が閉じるまでimplementation-readyへ昇格させない。

このPRはruntime、editable schema、marker/path writer、anchor planner、Transaction、MutationProgram、
shared-flow source_output、continuation、public APIを変更しない。未来のownership verifierも評価側に実装しない。
対象は現在のhorizontal solid-fill underlineだけ。stroke、任意vector、image、XObject、tagged PDF一般対応は対象外。

## 1. 再現方法と測定の意味

[観測コード](../evaluations/anchors/paint_ownership.py)と
[compact summary](../evaluations/anchors/paint-ownership-summary.json)が証拠。
既存`test_attributed.source_pdf`のCourier/Courier-Bold fixtureと既存anchor選択を利用した。
初回は親process、以後19 successful saves＋empty refusal 1件は別processで行う。
workerが読むprovenance入力はcurrent PDF/current sidecar(s)/font assetsのみ。
previous PDF、previous report、mutation mapはworker引数にもsidecarにもなく、observerだけが保存前後を比較する。
これはinput dependencyの検証であり、workerをOSの別sandboxへ隔離したとの主張ではない。

```powershell
$env:PATH='C:\Users\agri0\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin;'+$env:PATH
.venv\Scripts\python.exe -m evaluations.anchors.paint_ownership --work evaluations/anchors/runs/paint-ownership-NEW --output evaluations/anchors/paint-ownership-summary.json
```

`--work`は新規directory。raw PDF/font/report/rasterはignored。Windows Python 3.12.14、
PyMuPDF 1.27.2.3、Poppler 26.07.0を使用。runtime digestは
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`でPR #34と一致。
digest算法はsorted `pdfeditor/*.py`についてfilename＋NUL＋file bytesをSHA-256へ順次投入する。

`text_token_bytes`はBT/ET、text state/position/show各operatorのparser span、
`path_token_bytes`はpath construction/paint/end各operatorのspanの合計。
間のwhitespace、q/Q、color/state等を除くため両者の和はpage bytesにならない。
paint payloadは今回のdecoration mutation内の`q`から末尾までを観測したもの。
**どの数値もpersistent ownership rangeを推定するためには使わない。**
mutation deltasは保存前後の全未変更gap/suffixをbyte比較し、page bytes/operatorsとの加算一致を検査する。

### 主系列

firstは`ONE TWO THREE FOUR`の`TWO`を`FIVE SEVEN`へ変更。changeは`FIVE `を削除。

| revision | page bytes / ops | text token bytes | path token bytes | current underline paints | n | mutation count | text delta bytes / ops | decoration delta bytes / ops |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| initial | 200 / 23 | 119 | 51 | 2 source | 0 | — | — | — |
| first | 2,225 / 215 | 1,647 | 279 | 3 | 2 | 4 | +1,773 / +172 | +252 / +20 |
| noop1 | 4,253 / 407 | 3,186 | 499 | 3 | 5 | 26 | +1,784 / +172 | +244 / +20 |
| noop2 | 6,281 / 599 | 4,725 | 719 | 3 | 8 | 26 | +1,784 / +172 | +244 / +20 |
| noop3 | 8,309 / 791 | 6,264 | 939 | 3 | 11 | 26 | +1,784 / +172 | +244 / +20 |
| change | 9,998 / 949 | 7,578 | 1,091 | 2 | 14 | 26 | +1,519 / +144 | +170 / +14 |
| change noop | 11,662 / 1,107 | 8,869 | 1,241 | 2 | 16 | 21 | +1,496 / +144 | +168 / +14 |
| empty | refused | — | — | — | — | no publication | — | — |
| empty noop / regrow | unreachable | — | — | — | — | empty revision absent | — | — |

ordinary mutation kindは`text-edit`と`decoration`、ownerは`None`。
no-opはtext 23件＋decoration 3件で、painted fill数はbackground込み4のまま。
current element path数も4のまま。`n`で終わる旧pathはpaint catalogのpath IDを持たず、
snapshotから消えていても、構築命令とq/Qはpage programに残る。
current anchor path token bytesはfirst 228、noop1–3は220、change 152、change noop 150。
fixed backgroundは17 bytesで不変。どのcurrent paintにもbindされないpath token bytesは
34→262→482→702→922→1,074と増える（clip fixtureではsourceの非描画clip構築も含むため、
このinventory値だけをgenerated orphanと呼ばない）。

### 補助系列（first→noop1）

| case | page増分 bytes / ops | decoration増分 bytes / ops | 観測 |
|---|---:|---:|---|
| 2 disjoint underline groups | +1,517 / +146 | +176 / +16 | 2 group、別mutation位置 |
| 2 text styles | +1,502 / +144 | +170 / +14 | Courier/Courier-Bold、同じfill、1 anchor group |
| 2 underline styles | +1,537 / +146 | +176 / +16 | black .7 / blue .9、2 text styles、2 anchor groups |
| 2 editable paragraphs / same page | +1,440 / +143 | +178 / +16 | 2 ordinary plansを1 Transactionへ、2 sidecarsをfinal PDFへbind |
| unrelated path between text/underline | +1,511 / +144 | +170 / +14 | marker-looking comments付きforeign rectangleを保持 |
| outer q + line-state settings | +1,511 / +144 | +170 / +14 | w/d/J/j/M/Gを継承、q/Q balance保持 |
| CTM `.83 0 0 .91 7.25 11.5` | +1,967 / +144 | +252 / +14 | noop3では+1,969 / +144、paint payloadも変化 |
| inherited rectangular clip | +1,511 / +144 | +170 / +14 | source `re W n`を維持、writerはclipを追加しない |

29 successful saved stages（初回10＋re-edit19）。16 no-ops×2 pagesでMuPDF/Poppler 144 dpi比較が全て0差分。
全mutationの未変更gapとpage 2 programは不変。source backgroundのpaint fingerprintは主系列全保存で一致。
KEEPを含む9系列は、original sourceと各saved revisionのKEEP glyphs/text matrices/semantic stateも一致した。
比較時はfont xrefとclipの物理`at`位置を除き、CTM/clip geometry/line state等を保持して照合した。
複数paragraph fixtureの保存は既存`plan_editable`/`plan_document_edit`、`Transaction`、
`bind_editable`/`bind_document_edit`を利用する。複数sidecarを評価用listに束ねただけで、
新しいmulti-document publication APIやそのatomicityを実装・証明したものではない。

## 2. path/operator単位のroot cause

1. [`AnchoredPaintEdit.__init__`](../pdfeditor/anchors.py)はcurrent sourceから候補を再検査する。
   1 fill event、1 rectangle、opaque、baseline近傍、textと同色、textより後のpaintという条件。
   これは**候補のgeometryとexplicit relation**の証拠であり、pdfengine作者証明ではない。
2. `plan()`はgroup source pathsをterminal operatorの`merged_range`でsortし、
   **各paint terminalだけ**を`decoration` mutationで`n`へ置換する。source constructionはconsumeしない。
   counterfactual proofの一般形には`h n`もあるが、現在のunderlineはfill-onlyで`n`。
3. group最初のterminalのreplacementだけが`n q <rectangle segments> Q`。
   後続source terminalsは`n`だけ。group内の新segmentsは一つのcontiguous payloadになる。
   別groupは別位置で、間にはsource text/paint/stateがあり得る。paragraph全体を一つのrangeにはできない。
4. rectangleはlayout glyphのpage-coordinate x/baseline/advanceとgroup offset/thicknessから作る。
   `_rect_commands`はtemplate paint matrixのtranslationを引き、`_local_translation`のfloat64逆変換で
   local coordinatesへ戻して`m l l l h`を出す。terminalは元pathのfill operatorを再利用する。
5. `Transaction.commit()`は一つのinput revisionに対する全planを検査し、programをapplyして一度save、
   glyphs/nontext paints/fontsをverifyする。各paint直前の`paint:k` anchorが最終位置を与える。
6. `IdentityMap.emitted_paths()`はその位置をfinal paint catalogへ照合する。
   `path-id`の元はPDF SHA/page/invocation/program SHA/terminal byte rangeなのでrevision間で変わる。
   `map_path()`はunchanged constructionの全bytesとterminalを確認してfixed source successorを追う。
   両者は**同一saveのmapping**。previous revisionなしに履歴を復元する仕組みではない。
7. `bind_editable()`→`inspect_element()`→`_bind_relations()`はfinal PDFの全page pathsを再inspectし、
   fixedは`map_path`、generatedは`emitted_paths`で新source IDsへbindする。
   saved underlinesにはsource_ids/range/start/end affinityだけが残る。
8. reopenでparagraph/element/current PDF SHAが合うことは検証できる。次editで同じterminal置換を繰り返すため、
   旧生成rectangleの`m/l/h`、`n`、q/Qは残る。今回の正例では旧fillは残らず、**非描画residue**が増える。

例（概念図、sourceのwhitespaceは省略）:

```text
source:   re f
first:    re n q m l l l h f Q
noop:     re n q m l l l h n q m l l l h f Q Q
```

`re ...`まで含めて消す権限をcurrent source_idから逆算してはならない。
過去に生成されたが現在relationを失ったpaintが別PDFで本当に描画されたまま残っていても、
そのgeometryを根拠にcleanupしてはならない。このfixtureで観測したDは旧**非描画bytes**である。

## 3. A–Eの分離とcurrent persistent evidence

| category | 現在の根拠 | 現在可能な操作 / 限界 |
|---|---|---|
| A paragraph text | paragraph snapshot、logical units、styles、glyph IDs | 既存ordinary text edit。paint envelopeは所有しない |
| B fixed/unrelated source paint | element counterfactual proof、fixed relation、current path ID | 保持・successor検証。pdfengine生成paintへの昇格は禁止 |
| C current generated decoration | 保存中のemitted anchors、保存後のunderlines source IDs | current member paintを特定できる。q/Q/construction全体の作者・所有は未証明 |
| D older generated output | observerのbefore/after mutation記録でのみ由来を追える | current sidecarには旧path/q/Q/nのsemantic ownerなし。reopen後cleanup禁止 |
| E unknown paint | element未確認path、foreign marker-looking comment | 不明。似たshape/nameだけでowned扱いしない |

| persistent field | 証明すること | 証明しないこと |
|---|---|---|
| `pdf_sha256`, model checksum | current revisionとcaller-trusted editing documentの整合 | cryptographic作者署名、任意に再sealする攻撃者への信頼 |
| paragraph snapshot / SHA / selection / logical units | current text/style/glyph binding | paint byte range |
| element snapshot / SHA | current全page path catalog・counterfactual paint対応 | 全pathsがparagraph所有であること |
| path `source_id` | current terminalの一意識別。IDはrevisionに依存 | stable semantic group、生成者、wrapper所有 |
| anchors paragraph/element SHA | current snapshot同士の対応 | creation履歴 |
| `underlines` | source IDsとUnicode range/affinitiesというconfirmed relation | zero-paint identity、stable style template、envelope |
| `fixed_relations` | source pathをfixed-to-pageとして保持する意図 | generated/owned paintであること |
| `relations` | ordinary explicit relations（anchor系列ではnull） | author record |
| `logical_element` | paragraph ID/region/alignment | anchor ID。独立作成ではどちらも既定`paragraph-1`になり得る |
| `previous_model_sha256` | 直前model digestへの参照値 | 旧model/paint/historyの中身、authorship chain検証 |
| fonts / text empty style recipes | font assetsとtext再生成に必要なstyle | underline offset/thickness/fill recipe、paint復元権限 |

current checksumは偶発破損検出。将来も「trusted sidecar＋正規writerが作ったcreation record」という
信頼境界を明示する。markerと自己申告の`generated-by-pdfengine`文字列だけではauthor proofにならない。

### current-only ownership分類

分類は**pdfengineが生成したpaint envelopeの削除・置換権限**について行う。
`PROVEN FOREIGN`は当該paint ownerのauthority外という意味で、製品名や歴史上の作者を断定しない。

| object | 分類 | 理由 |
|---|---|---|
| generated underline path | **AMBIGUOUS** | current anchor memberはproven、pdfengine作者/envelopeは未証明 |
| generated underline paint terminal | **AMBIGUOUS** | explicit anchor＋counterfactualで既存のterminal編集は許可できるが、過去の生成者は不明 |
| fixed source background | **PROVEN FOREIGN** to generated anchor ownership | fixed relationとして保護し、generated groupには含めない。source origin自体はfixture履歴で観測 |
| old generated decoration after change | **AMBIGUOUS** | 本系列は非描画residue。旧IDsは消失、current sidecarに作者/連続rangeなし |
| dormant/empty anchor identity | **AMBIGUOUS / absent** | emptyは拒否、部分削除groupは消失。権利のpersistent witnessが存在しない |
| text empty/style witness | **PROVEN FOREIGN** to paint envelope | textのbindingでありpaint recipeではない。このanchor系列ではempty witness自体に到達しない |
| marker-looking foreign path | **AMBIGUOUS** authorship; outside confirmed group | commentもsource shapeも所有権を与えない。future well-formed forged markerも同じ |

current v2にはpaint envelopeとしての **PROVEN OWNEDは0**。
保存中のemitted-path証拠と、reopen後のpersistent作者証拠を混同しない。

## 4. Dormantの観測

全削除は`paragraph.plan_paragraph_edit`の
`empty element paint relations require explicit dormant decoration/ownership semantics`
で止まる。PDF/sidecarとも未公開。拒否を外さず、empty noop/regrowはunreachableと記録した。

別系列はdisjoint groups `[0,7]`/`[8,18]`を持つ。最初のrangeだけ削除するとparagraphにはtextが残り、
`project_range()`がそのgroupを`None`へ投影する。plannerは該当fillを`n`へ変え、
`_bind_relations()`は`output_range is None`のgroupを**skip**する。
残るrangeは`[1,11]`。no-op後も1 group、先頭へ`ONE `を再挿入しても残存groupが`[5,15]`へ移るだけ。
old groupのstyle/affinity/identityは保存されない。paint数が再び増えるのは残ったgroupの2行へのreflowであり、
消えたgroupの復活ではない。

marker pairだけではUnicode range、affinity、recipeを保持できない。
sidecar recordだけではゼロpaint時の安全なstream location/contextを証明できない。
**両方**が必要。text island IDだけに結び付ける案ではordinary editableに存在しないtext islandを要求し、
複数paint位置/異なるscopeも表現できない。

## 5. 候補比較

以下の各列は提案導入後の能力。現行markerなしbindingを自動昇格してよいという意味ではない。
A/C等の文字はここでの比較行名で、依頼文の二つの候補リストを統合している。

| 案 | current-only / authorship | completeness / exclusivity | dormant | multi-group / segments / overlap | reopen / size | legacy / fail closed / complexity |
|---|---|---|---|---|---|---|
| source_id/path mapping only | relationのみ、作者なし | terminalだけ、wrapper外のresidue不明 | 不可 | current segments可、group永久IDなし、重複は既存拒否 | reopen可、current-size | 現行legacy維持のみ。ownedとは呼べない / low |
| mutation history persistence | previous PDFも必要、recordだけでは不足 | 全revision再生なら監査可、current-only条件違反 | 別semantic設計が必要 | history順依存、互いのmutation競合が複雑 | save数で増加 | rollback/replay authorityが拡大 / high、reject |
| text source-output内にpaint | scope/owner guard違反 | textと離れたpaint間のforeign bytesを巻き込む | paint recipeは別途必要 | z-order/CTM/clipが変わる | ordinaryにはtext islandなし | PR #33を流用しない / reject |
| paragraph単位paint island | 新recordなら可能 | source位置が分散、移動しない限り1 rangeにできない | recipe record必須 | 別group contextとsource間隔を失う | O(paragraph)だが巨大range危険 | fixed paint取込み不可、非連続spanなら実質group案 / high |
| **anchor-group paint island** | trusted creation＋current marker/record | body grammar＋全current IDsの双方向包含 | pair＋recipe＋range identity | 1 group内segments contiguous、別group独立。overlap拒否 | O(current groups＋segments)、履歴なし | 明示creationのみ、invalid ownedは拒否 / medium-high、推奨 |
| markerなしcurrent emitted envelope record | current range/hash＋creationは必要 | checked grammar/containmentがあれば候補 | 空range位置を外部offsetだけで扱い脆い | group単位recordを結局要する | O(groups)、rebindごとrange更新 | discoverability/tamper inventoryが弱い。marker節約の利益小 / 不採用 |

fixed paintはどの案でも所有対象へ取り込まない。
group案は複数mutationのschedule順に位置を依存させず、同じinput上でdisjoint範囲を検証して一括applyする。
全paintを最初のparagraph位置へ集める方法はz-order変更の新証明を必要とし、既存architectureより安全とはいえない。

## 6. 推奨contract（未実装、§12のgate付き）

### 単位・identity・creation

1 confirmed underline group＝1 immutable `anchor_id`＝1 marker pair。
paragraph IDはcaller logical identityを使うが、既定名だけで一意性を仮定しない。
creation seedは初回のcurrent PDF SHA、paragraph snapshot SHA、element snapshot SHA、
confirmed source path IDsのsort済み集合、初期range、両affinity、kind=`underline`。
source IDs/SHAは**creation provenance**として固定し、後のrange/geometry/indexから再計算しない。
seedの元source IDsがoffset由来でも、markerを現在のabsolute offset/xrefから作る設計ではない。

canonical JSONはUTF-8、sorted keys、compact separators、NaN/Infinity禁止、number/stringを勝手に相互変換しない。
`anchor_id = SHA256(domain 'pdfengine-anchor-creation-v1' + NUL + logical_element_id + NUL + canonical(seed))`。
`marker_id = SHA256(domain 'pdfengine-paint-v1' + NUL + logical_element_id + NUL + anchor_id + NUL + SHA256(canonical(seed)))`。
timestamp/random/save counter/group index/xref/current offset/geometry単独は禁止。
同一seedの二重登録はduplicateとして拒否する。異なるparagraphの同一既定IDはseedで区別し、
同一paragraph/groupを別sidecarに重複登録した場合は、同時transactionのregistryで衝突拒否する。

### Markerとbody

```text
% pdfengine-paint-v1 begin <64 lowercase hex marker_id>\n
q <rectangle> ... <rectangle> Q\n
% pdfengine-paint-v1 end <same marker_id>\n
```

canonical dormant bodyは`q Q\n`。少なくともpairは残り、state/recipeも維持する。
commentは既存parserのoperator間gapにある独立した完全なlineとしてのみ検出する。
文字列、hex string、inline image等の内部をregex探索しない。missing/duplicate/reversed/nested/malformed pairは拒否。
source_output/continuationとdomainを分離する。commentが存在するだけではownerにならない。
unclaimed valid paint markerを自動採用せず、owned-modeのpage inventoryとrecord不一致として拒否する。

実writerに必要なoperatorは **`q Q m l h f F f*`だけ**。
active grammarは`q (x y m x y l x y l x y l h fill)+ Q`、fillは`f`/`F`/`f*`。
`F`はsource aliasとして現writerが再出力し得るのでreaderで認識し、将来canonical writerは`f`へ正規化する案。
各rectangleはfinite numbersのみ、4 verticesがrecipe/layoutから期待されるrectのinverse-CTM像と一致すること。
token集合だけのallowlistでは不十分。operand数・順序・paint数・closed path・positive boundsまで検証する。
`n`はinitial source terminalのconsumeとして**island外**に一度だけ残す。
bodyに`n/re/c/w/d/J/j/M/color/gs/Do/sh/cm/W/W*/BT/ET/BMC/BDC/EMC/BX/EX`を許可しない。
現writerはline/color/opacityを設定せず、template entry stateを継承する。
一般path grammarやstroke writerを先回りして許可しない。

### 四つの証明

- **Authorship:** supported writerによる明示creationのimmutable seed、domain付きidentity、trusted current sidecar、
  current PDF/program/block hash、marker inventoryが一致する。legacy関係から「以前も生成したはず」と推測しない。
- **Completeness:** current groupの全source IDsがisland内部で、constructionからterminalまで完全包含される。
  bodyの全paintを再解釈して全current segmentsと1対1対応させる。外部paintに同じgroupをbindしたら拒否。
  dormantはsource IDs/segmentsが0で、bodyもzero-paint。歴史上のforeign/orphan paints全てを同定する主張ではない。
- **Exclusivity:** bodyにはgrammarが許すgroup rectangleしかなく、foreign text/paint/marked scopesやfixed IDsがない。
  他group/paragraph/font/text witnessのauthorityと重複しない。page全体のelement.pathsをowned扱いしない。
- **Isolation:** path-free page-level entry、balanced q/Q、全pathを内部fillでconsumeしてpath-free exit。
  graphics-state等価とsource successor preservationを別途検証する。q/Qはcurrent pathを保存・復元しない点が重要。

初回は現在の明示source terminal消費だけを行い、新しく出力するpaint payloadを所有する。
初回以前のconstruction/q/Q/nや既存foreign paintを取り込まない。以後はpair内部bodyだけを1 mutationで置換し、
旧terminal単位のconsume＋再挿入を繰り返さない。marker bytesは不変、最終range/SHA/IDsはsaveごとに再bindする。

## 7. Entry context、CTM、clip

`State.report()`はCTM、fill/stroke、opacity/stroke_opacity、text state、clipと`other`を持つ。
w/d/J/j/M/ri/iは`other`、ExtGStateも`other`に保持される。boundaryにはq depth、BT/ET、marked/compatibility、
pending path/clipがある。marker entryはBT/ET外、marked/compatibility depth=0、pending path/clipなし。
inherited q depthは許可するがentryとexitの一致を要求する。

recordのentry digestはCTM、clipのrule/path/そのCTM、fill/stroke、両opacity、全text-stateと
line width/dash/cap/join/miter/ri/iを含む`other`、q depth/各scope状態を固定する。
font xref、clipの`at`など物理位置だけは除外。font resource identityとfont evidenceは既存font検証経路を併用する。
string evidenceをfloatへ変換しない。finite数値、numeric negative zero正規化、unknown field拒否を明記する。
既存source ownershipのcontext helperは参考にするが、private `_source_output`をordinaryへ通す理由にはならない。

q/Qはbody内で変更したgraphics stateを戻す。しかしinherited context自体の権限、empty current path、
painted結果の完全包含はq/Qだけでは保証しない。v1ではnon-default opacity、patterns、soft mask、
blend/transparency group、layers、ExtGState経由の未知状態を拒否する。
初期案では**ExtGState継承も全拒否**とし、明示w/d/J/j/M/G等はstate一致の範囲で許可する。
rendererの見た目だけで未知stateを許可する拡張は別PR。

CTM初期scopeはfinite・invertible・positive axis-aligned scale＋translation。
rotate/shear/reflectionは拒否し、今回の調査で証明していない能力を増やさない。
glyph/layout geometryはpage coordinates、path emissionはinverse template matrixによるlocal coordinates。
clipはpage cropとsupported source rectangle clipの共通領域を使用し、island内で変更しない。
既存`_check_clip`→`contains_fill`はparagraph ink boundsと各decoration rectを検査する。
v1は各clipが単一`re W/W* n`、axis-alignedであることまで要求し、曲線/compound/text clipは拒否する。

**数値安定性は未証明。** CTM系列のpayloadはfirst/noop1/noop2が251 bytes、noop3が253 bytesで、
4つ全てSHAが異なる。第2segmentのtopは82.24299621582031 → 82.24298095703125 →
82.24298095703125 → 82.24296569824219となった。画素一致はbyte/geometry canonical性の証明にならない。
identity CTMの主系列でもfirst payload251→noop1 243 bytes、change169→noop167 bytesだった。
`_candidates`のrendered bounds→median offset/thickness再推定と、glyph advance→inverse CTM→number formattingを
合成したround tripが固定点でない。したがってmarkerを付けるだけの実装ではKPIに届かない。
recipeをcreation時に固定し、次回の幾何から再推定しない案を優先するが、text layout側のadvance変化も含めた証明が必要。

## 8. 最小persistent record案

**future schema案**。現行`pdfengine-editable-2`は変更しない。新能力は新schema versionとして導入する候補。
`anchors.underlines[*]`に`anchor_id`を追加し、active/dormantでも同じentryを保持する。
以下はfieldの形であり、実際に生成済みのrecordではない。

```json
{
  "paint_outputs": {
    "<anchor_id>": {
      "version": 1,
      "state": "active",
      "marker_id": "<sha256>",
      "logical_element_id": "<confirmed paragraph id>",
      "created_from": {
        "pdf_sha256": "<creation revision>",
        "paragraph_sha256": "<creation snapshot>",
        "element_sha256": "<creation snapshot>",
        "source_ids": ["<initial confirmed paths>"],
        "range": [0, 18],
        "start_affinity": "reject",
        "end_affinity": "reject",
        "kind": "underline"
      },
      "recipe": {
        "coordinate_space": "page",
        "offset": "<canonical finite decimal>",
        "thickness": "<positive canonical finite decimal>",
        "fill_rule": "nonzero",
        "revival": "explicit"
      },
      "current": {
        "page": 1,
        "program_sha256": "<current merged page program>",
        "range": [100, 400],
        "block_sha256": "<complete pair plus body bytes>",
        "entry_context_sha256": "<current semantic context>"
      }
    }
  }
}
```

current range含むpairとblock SHAはlocator/integrity check。ownership authorityはcreation identity、grammar、
context、雙方向paint containmentとの積であり、range/hash単独でない。rangeはfinal revisionで更新する。
mutable Unicode range/affinities/current source IDsは対応する`underlines`に一箇所だけ保存する。
recipeのfill color/opacityはentry digestとcurrent PDFがwitnessで、重複するcolor/style historyは持たない。
text style IDはglyph shapingの独立authority。paint recipeをfont style IDで代用しない。
created_fromは固定1回、currentは上書き1個。history配列なし、sizeはcurrent group/segments数に比例する。

dormant案では同じanchor entryに`source_ids: []`、collapsed Unicode range `[p,p]`を残す。
消失時のpはedit mappingによる削除区間先頭の出力位置、paragraph全削除なら0。
offset/thickness/fill_rule/affinities/marker_id/created_fromは消さない。
dormant noopは同じ`q Q\n`を維持し、active groupのsource paint候補探索へ流さない。
再挿入したtextに自動で過去groupを結び付けず、explicit revival requestが既存anchor_idと新しい非空rangeを指定する案。
同一点に複数dormant groupがある場合もindexやgeometryで選択せず、ID指定を要求する。
active rangeと重なる復活、cluster途中、crossing editsは拒否。nonempty rangeだがzero visible segmentsとなる
whitespace-only decorationはv1ではrefuseし、dormantと黙って同一視しない。
このrevival requestのpublic surfaceとzero-paint plannerへの接続は§12の未確定事項である。

## 9. Transaction / final rebind / publication

1. current PDF hashを固定し、全editable snapshot/anchor/paint recordを同じ**input revision**で検証する。
   page内全paint islandの重複、group/paragraph identityの衝突、fixed ID混入をここで拒否する。
2. text mutationと各group body mutationを同じoffset座標でplanする。途中のtext rewrite結果からpaintを再証明しない。
   ordinary text plannerの`_source_output` owner guardは維持する。一般text ownershipはこの設計のscope外。
3. `MutationProgram`が異なるowner間のoverlap/同一source消費を拒否し、1 Transactionでsave/verifyする。
   paragraph-level transaction ownerとは別にgroup ID/recordを管理し、kindだけをauthorityにしない。
4. final PDFで全paragraphをbindし、全islandを再検証してcurrent ranges/program/block/context SHAを更新する。
   emitted paint anchors→final IDsが全segmentsと一致すること、fixed pathsが`map_path`で保持されることを検査する。
   dormantは0 pathsでもmarker＋entry/context＋recipeでbindする専用経路が必要。
5. 全rebindが成功してからsidecarをseal、`open_editable`相当の再検証を行い、temp artifactsだけを公開候補にする。
6. `write_editable`の既存pair publicationへ接続する。late paint rebind failureならtemporary PDFだけで失敗する。

現行`_publish`はhard linkの2件目が例外なら自分が作った1件目をunlinkする**exception atomicity**。
OS crash between linksまでtwo-file atomicにする機構ではない。PDFだけ残ればsidecarなしのreopenは再確認が必要。
今回の新testはTransaction saved temp PDFの存在を確認した後、`IdentityMap.emitted_paths`で失敗を注入し、
公開PDF/sidecarが両方なくtempdirも除去されたことを検査する。既存2件目link失敗testも実行する。

## 10. Legacy / fail closed

legacy v2は現行anchored rewriteのまま。新contractを明示reconfirmした場合のみ、**今から出力する**payloadへ
creation record/pairを作る。old source_ids/geometryを自動owned昇格しない。古い非描画residueは残す。
旧sidecarを見失ってPDFを再confirmしても、過去のmarkerを引き継ぐ権限は生まれない。

owned recordの検証失敗後にordinary anchored rewriteへfallbackしてはならない。
fallbackするとunknown bytesの保護やdormant identityを黙って失う。以下は全て未公開のままrefuseする。

- marker missing/duplicate/reversed/malformed/nested、owner/domain/creation identity不一致、unclaimed paint marker。
- current PDF/program/block/context SHA不一致、stale paragraph/element snapshot、record range不一致。
- grammar/finite numbers/operand count不一致、foreign path/text/paint挿入、body paintとbound IDsの非全単射。
- group current pathがbody外、constructionが境界をまたぐ、fixed source paint混入、island同士/他mutationとoverlap。
- unknown relation、duplicate source path ownership、overlapping semantic ranges、dormant identity/recipe欠落。
- unsupported CTM/clip/ExtGState、pending path/clip、BT/marked/compatibility scope、entry/exit state不一致。
- legacy/new record混在の曖昧な部分移行、unsupported version、必要font/resourceの不足、final rebind失敗。

## 11. 次実装PRのacceptance matrix

**bytes KPI:** paint island bodyはfirst→noop1/2/3、change→noop、dormant→noop、regrow→noopでbyte identical。
各marker/creation identity不変、no-op paint mutation net増分0 bytes/0 operators、paint由来の新residue増分0。
一般textは依然ordinary rewriteなので**whole-page no-op増分0は今回のKPIではない**。
text mutation deltaを分離し、page増分＝text delta＋paint delta＋明示されたその他deltaを毎回照合する。
source initial residueは初回だけ固定、以後byte保持。sidecarにはrevision historyを増やさない。

**renderer KPI:** MuPDF＋利用可能なPopplerでno-op全page pixel差0。content editはexpected glyph/paint/rangeと、
fixed/foreign paints・他paragraph・他pageの保持を独立検証する。画素一致だけで所有を認めない。

| test | 必須acceptance |
|---|---|
| first creation | 明示確認source terminalsだけconsume、new payloadだけowned、source residue/foreign保持 |
| first→3 noops / second edit→noop | body bytes/hash/marker/recipe固定、paint増分0、renderer差0 |
| full delete→dormant→noop→regrow | same ID/recipe、zero pathsでreopen、explicit revivalで新rangeへ。例外時両出力なし |
| partial group deletion / same-caret groups | 消えたgroupのidentityを残す、default挿入は復活させない、ID指定でのみ復活 |
| multiple groups / segments / two paragraphs | 各group 1 island、全current IDs双方向包含、plan orderを反転して同じ結果 |
| multiple styles / fonts | text style変更でpaint identityを変えない、違うfill contextsは拒否/別group、font authority独立 |
| background / intervening unrelated path | fixed/foreign bytesとpaint保持、group islandが間の内容を包まない |
| q / line-state / suffix paint | entry=exit、w/d/J/j/M/stroke/fill/opacityの継承、後続source paint不変 |
| CTM scale/translation | 0.83/0.91 fixtureを含めbody bytesとgeometry安定、singular/shear等は未公開refusal |
| rectangular clip | inherited clip/ink containment、内側にclip生成なし、unsupported/text/curve clip拒否 |
| fresh process / no old report/PDF | current PDF＋sidecar＋fontsだけを別directoryで渡し、各状態で同じ権限判定 |
| marker tamper | missing/duplicate/reversed/fake valid marker/owner mismatchを拒否。strings内markerは無視 |
| block/program/context tamper | 個別hash・state変更、再sealされた不整合recordでも拒否 |
| foreign injection / outside owned path | 同じgrammarのforeign rectもexpected segment全単射で拒否、source fixed ID混入拒否 |
| island overlap / competing owner | planning前拒否、same group double claimを拒否 |
| legacy / reconfirm | legacy growth維持、新出力だけcreation、old orphan cleanup禁止、owned failure fallback禁止 |
| late failure / pair publication | temp save後のpaint rebind失敗、2件目link失敗とも公開pairなし、existing destinationsを消さない |

## 12. 未証明事項と判定を変えるための調査

| blocker | なぜ必要か | 次のfixture/evidence | 調べるcode path / 決定事項 |
|---|---|---|---|
| B1 canonical geometry recipe | current writerはidentity/scale CTMともfirst/noopでpayloadが変化。marker単独ではcanonicalにならない | 同じtext/styleを維持してfirst/noop×3、content change/noop、empty/regrow後のrect decimals/advance/recipeを比較。rounding位置を決定しbyte固定点を示す | `anchors._candidates`→median offset/thickness→layout advances→`_rect_commands`→`elements._local_translation`→`content_stream.number`。recipe固定だけでtext round trip由来の差まで解消するか未証明 |
| B2 zero-paint planner/revival authority | 現在はsource_ids必須、empty拒否、消失group skip。marker/record追加だけでは再開不能 | full empty＋部分delete＋同じcaretに2 dormant groups＋whitespace-only。explicit ID復活、空noop、failures、current-only reopenのモデルを固める | `paragraph` empty guard、`logical_element.EmptyParagraph`/style recipes、`AnchoredPaintEdit.__init__` candidate依存、`project_range`のNone、`editable._bind_relations` skip。revival requestのAPIとglyph-free template入力の責務を確定 |
| B3 isolated dormant boundary | active counterfactual paint証明はあるが、zero-paint islandの境界/context証明は存在しない | q/rectclip/CTM/line-state下でactive→dormant→regrow、後続foreign paint保持。pending path/clip/marked/unknown ExtGStateは負例 | `ContentPage.boundaries`とpath state、`source_ownership.context`の再利用可能な部分を分離。paint専用boundary validatorの責務を定める |

次のOpusレビューでは、current memberとauthor/envelopeを取り違えていないか、Dを残留**paint**と誤報していないか、
group単位以外で安全に小さくできるか、B1のrounding/recipe方針、B2のexplicit復活と複数caret、
B3のpath-free境界・ExtGState初期拒否、trusted sidecarの信頼境界、legacy reconfirmと例外atomicityを重点確認する。

**最終判定: NOT READY。** group island＋persistent dormant recordが最も狭い有力案だが、
この観測はfuture ownershipの安全性を実装済み・実証済みとはしない。§12を解決する設計/fixture作業を先行させる。

## 13. Focused verification

追加testsはtoken accounting、decoration mutationの範囲とgap保存、current empty refusal、
late paint rebind rollback、公開summaryのcoverage/CTM不安定性を検証する。
既存anchor/editableと必要なpath mapping/transaction testsだけを実行する。full suite、実LibreOffice原本は未実行。
実行command・最終結果は[評価README](../evaluations/anchors/README.md)を参照。

## 14. 独立レビュー結果

reviewer: Claude Opus 5.5 / reviewed head `ef43c729c9875ebbb0980a899e0ab68344a2f60a`

**PASS — NOT READY CONFIRMED.** current analysis、anchor-group paint island案、B1–B3の整理は正しい。
ただし実装前blockerとして**B4（page単位marker inventoryと複数editable sidecarの協調）を追加**する。
このPRはIMPLEMENTATION READYへ変更しない。runtime/evaluator/test logicはレビューで変更していない。

### 確認した結論

- **member vs authorship:** `_candidates`/`AnchoredPaintEdit`はgeometry・色・seqnoで候補を出すだけ、
  `emitted_paths`/`map_path`は同一save内のmapping、`_bind_relations`は`source_ids`/range/affinityだけを保存する。
  path IDはPDF SHA/program SHA/terminal byte range由来でrevisionごとに変わる。current persistent evidenceは
  「現在のrelation member」以上を示さず、q/path/fill/Q envelopeの作者証明にはならない。AMBIGUOUS分類は正しい。
- **residue:** 旧generated pathはterminalが`n`化された非描画residueで、current sidecarからownerが消えた後は
  runtimeからAMBIGUOUS。observerの来歴知識を権限にしない分類は正しい。
- **数値:** 主系列200/23→2,225/215→4,253/407→6,281/599→8,309/791、no-op +2,028/+192
  = text +1,784/+172 + decoration +244/+20、change +1,689/+158、change noop +1,664/+158をsummaryで照合した。
  identity CTMでもpayloadはfirst 251 bytes→noop1–3 243 bytes（1回で収束）、CTM系列は毎回変化する。

### B1 — canonical geometry（実装前blocker、閉じられる）

原因分析は正しい。`interpreted_paints`はMuPDF device上のfloat32 path/matrixを返し、`_candidates`が
そのrendered boundsとMuPDF traceのglyph originからoffset/thicknessを毎save再推定する。これがlayout baselineに足され、
`_local_translation`のfloat64逆変換と`number()`の12桁出力を経て次saveの入力になる。この**feedback loop**が
固定点にならない（82.24299621582031→…はfloat32 ULP単位の移動）。recipe固定だけでは不十分で、次のcontractが必要:

1. owned/dormantではrendered paint geometry、MuPDF trace、`_candidates`をpaint座標の入力に使わない。
   paint geometryの唯一のauthorityはrecipe＋今回のplanned layout（page座標）。
2. recipeのoffset/thicknessはcreation時に一度だけ有限decimal文字列へ正規化して固定する（負zeroなし、指数表記なし）。
3. planned layoutの入力（confirmed layout、logical text、style size/Tz/rise、font advances）がsave間で固定点であることを
   glyph planのorigin/advanceのbyte一致で示す。ordinary text rewrite経由で値が往復する場合も同じ。
4. local座標変換はMuPDFのfloat32 template matrixではなく、content_stream interpreterのCTM（source decimal由来、
   必要ならPR #19のexact rational方式）を使い、出力を決められたdecimal規則で一度だけ量子化する。
5. toleranceではなく、同一入力→同一bytesの決定性と、入力に前回出力が還流しないことでbyte canonicalityを作る。

必要evidence: identity CTMとscale＋translation CTM（0.83/0.91を含む）でfirst==noop1==noop2==noop3、
change==change-noop、regrow==regrow-noopのpaint body bytes/SHA/operatorsが一致すること。renderer一致は別KPI。

### B2 — zero-paint planner / explicit revival（推奨contractでは実装前blocker）

現行runtimeでは全削除は拒否、部分削除はgroupが`project_range`→`None`、`_bind_relations`でskipされ消失する。
dormant identityを保持する推奨contractではB2は必須。同じcaretに2 dormant groupがあり得る以上、
geometry/range/styleから自動選択してはならず、explicit revivalは必要。同一caret 2 groupは必須fixture。
最小request案（次設計PRで確定、今回は未実装）:
`{"anchor_revivals": [{"anchor_id": "<64 hex>", "range": [a, b], "start_affinity": "...", "end_affinity": "..."}]}`。
対象はdormantの既存anchorだけ、rangeは編集後Unicodeのgrapheme境界、他active rangeと非重複、whitespace-onlyは拒否。
collapse point `p`は診断情報に留め、復活の権限にしない（物理位置はmarkerが持つ）。
text insertion witness/empty-style recordはpaint authorityに流用しない。dormant planはtemplate paintが無いため、
色・clip・matrixをentry boundary Stateから得るglyph-free経路が要る。

### B3 — dormant boundary（推奨contractでは実装前blocker、案Aで閉じられる）

案A（初回active island位置をdormant中もそのまま保持）を推奨する。markerは消費されず、位置の権限は
creation時のsource terminal consume（island前の`n`）とmarker pairが持ち、rebindはinventoryで行う。
B（text位置へ移動）とC（page-entry等）はz-order・CTM・clipを変えるため不採用、Dは案Aで不要。
q/Qはcurrent pathを保存しないので、path-free entry/exitとpending clipなしは必要条件。grammarで全subpathを
body内fillで閉じることと合わせて十分。entry digestはcreation時から不変量として入力/出力/reopenで照合する。
ExtGStateの全拒否は妥当（Stateは`/ca`/`/CA`しか解釈せず、SMask/BM等はopacity=1の報告でも存在し得る）。
clipは形状正規化をsource-outputの`context()`から流用し、各rectの包含は既存`_check_clip`で証明する。

### 追加blocker B4 — page単位inventoryと複数sidecar

§5/§6の「unclaimed valid paint markerは拒否」はshared-flowのような1 sidecar/pageでは妥当だが、
ordinary editableはparagraphごとにsidecarを持つ。paragraph Aを単独保存するとBのsidecarはPDF SHA不一致で再確認が要り、
再確認ではBの旧markerを引き継げない（§10）。結果としてAもBも相手のmarkerをunclaimedとして拒否し続け、
同一pageの2 paragraphが恒久的に編集不能になり得る。次設計PRで、(a)全paint ownershipをpage単位manifestに集約、
(b)同一pageの全sidecarを常に同時transactionで扱う、(c)他element markerを権限なしで許容しつつ重複・overlapを拒否する
inventory scope、のいずれかと再確認policyを確定し、交互単独保存・stale sidecar再確認fixtureで示す必要がある。

fill/style、group identity、z-order、resource ownership、transaction順序には追加blockerはない。
fill色はentry contextが継承元で、black .7/blue .9は別groupかつ別entry contextとして区別される。
現行`MutationProgram`/`Transaction`で、全ownershipをinput revisionで検証→plan→commit→final rebind→seal→publishの
順序は実現できる。mutation ownerにはanchor_idを診断用に入れてよいが、persistent authorityにはしない。

### Non-blocking

1. B2/B3は「dormant identityを保持する」推奨contractの前提。group消失時に自分の所有block（marker含む）と
   recordを丸ごと削除し、全削除は現行どおり拒否、復活なし、という狭いv1ならB3は不要。次設計PRでどちらかを明示する。
2. entry digestへのtext state・line stateの包含はfill描画には不要で保守的（false rejectの可能性）。
   paint用context subsetを明文化する。
3. `marker_id`は`anchor_id`（既に`logical_element_id`を含む）と重複入力。`current.page`はparagraph pageと一致検証する。
4. `F`→`f`正規化はcreation時の1回だけbyteを変える。fill_ruleはrecipeから決定的に出す。
5. `_publish`の説明（exception rollbackであり、2 link間のOS crash atomicityではない）は正確。

### 再実行と未実行

focused: `tests/test_paint_ownership_observation.py`、`tests/test_anchors.py`、`tests/test_editable.py`、
`tests/test_transaction.py`の2 testで**34 passed**（138.48s）。full suiteと外部LibreOffice原本は実行していない。

### 次PRのscope

runtime実装ではなく、B1–B4を閉じるdesign/evidence PR。B1のbyte固定点evidence（identity/scale CTM、
change/regrow）、B2のrevival request形とdormant planner責務、B3の案A境界・context証明、
B4のinventory scopeと複数sidecar fixtureを含める。dormantを持たない狭いv1を選ぶ場合はその理由とB2/B3の扱いを記す。

## 15. B1–B4 design gates — 2026-10-04

Base: `fdba3dcb44b0fe4010a3ea1fc986b3aac83da2e6`、PR #35 merge確認後のmain。
§1–14、PR #35 evaluator/summary、Opusレビューは変更していない。

**Chosen scope: NARROW V1。最終判定: NOT READY。** B2/B3のdormant機能は契約から明示除外し、
B4はPDF document全体で1つの独立paint ownerに限定して閉じる。B1ではpaint feedbackを切った後も
**current planned glyph advance自体がfirst/change後のreopenで変わる**ことを実測した。
これを新blocker **B1-L（planned layout authority）**とする。画素差0や小さな誤差をもってREADYにはしない。
次PRはpaint runtime実装ではなく、B1-Lだけを対象とするlayout authority設計・evidenceが最小scopeである。

[新evaluator](../evaluations/anchors/paint_contract.py)、[pure formatter](../evaluations/anchors/canonical_geometry.py)、
[read-only boundary分析](../evaluations/anchors/paint_boundary_analysis.py)、
[新summary](../evaluations/anchors/paint-contract-summary.json)を参照。
prototypeはPDF/markerを生成・挿入しない。pure policy modelの`validated=True`は外部で完全証明済みという
**前提**であり、ownership verifierの代用品ではない。renderer結果も現行runtimeで保存したPDFの結果である。

### 15.1 FULLとNARROW V1の選択

| scope | 得られる機能 | 必要な追加authority | 結論 |
|---|---|---|---|
| FULL | active/dormant/explicit revival、独立した複数owner | zero-paint template、collapsed ranges、same-caret識別、revival request、全sidecar協調/rebase | 現行APIの範囲を大きく拡張。v1には採用しない |
| NARROW V1 | active underlineのbounded rewrite、group終了 | 完全なactive block所有、終了のatomicなrecord削除、単一owner inventory | **採用**。現在のempty拒否とgroup消失semanticsに近く、機能追加を抑える |

NARROW V1の規範:

- 1 anchor group＝1 independent active paint island。複数group/複数line segmentsは同じparagraph owner内で許可。
- PDF document全体で**1 independently managed paint-owning editable**。page単位だけの制限では不十分（§15.5）。
- 全paragraph emptyは現在と同じく拒否する。nonempty rangeだがvisible segmentゼロも拒否し、
  whitespace-onlyをdormantやownership終了へ暗黙変換しない。
- `project_range`がNoneになるgroupだけは、検証済み**complete block（marker pair含む）**とrecordを同transactionで終了。
- dormant state/body/recipe retention/automatic revival/explicit revivalは全て**非対応**。
  `q Q`は許可bodyではない。revival requestはunknown optionとして明示拒否する。
- 文字を再挿入しても装飾は戻らない。新underlineを扱うなら、別途明示確認できる現在のsource underlineから
  **新creation**を開始する。任意のunderlineを描く新APIも追加しない。終了したanchor IDをcallerが指定して再利用できない。
- 旧nonpainting residue、source construction、source wrapper、fixed/foreign paintは終了時にも触らない。

FULLのstate machine/API案を併記して実装者に選択させない。§6–8のdormant案は歴史的候補であり、
この節のNARROW V1仕様を次のscopeとする。ただしB1-Lが閉じるまで実装着手を勧めない。

### 15.2 B1: paint feedbackを切った実験と新blocker

現行の二つのloopを区別する。

1. paint: current rendered path → MuPDF float32 bounds/matrix → `_candidates` → median offset/thickness
   → layout baseline → `_rect_commands` → `_local_translation` → `number()` → current rendered path。
2. text/layout: planned positions/advances → PDF text output → MuPDF trace → retained glyphsのadjacent origin差
   → 次の`ParagraphShaper.shape()`/layout。line末尾・新旧provider境界では別のadvance式を使う。

prototypeは1を切り、初回group recipeを固定して、各revisionの**その時点のglyph plan**からrectangleを作る。
current underline bounds、旧generated rectangle、path source IDをformatterの入力にしない。
glyph planから行ごとにrangeと交差するglyphを取り、境界whitespaceを除き、
`left = first.origin.x`、`right = last.origin.x + last.advance`、`baseline = line.baseline`を得る。
このfixtureはrise/shape x-offset=0。将来runtimeはreportから復元せず、既存plannerと同じ
`PlacedGlyph.x/advance`とline baselineを直接使い、riseやshaper offsetをunderline位置へ重複加算しない。

| 入力authority | 現行code path / 注意 |
|---|---|
| line allocation | `layout_attributed`が測ったadvancesとavailable width、alignmentで決定。今回の全comparisonでは一致 |
| glyph origin / advance | `ParagraphShaper.shape`→layout→paragraph glyph_plan。**下表のとおり完全一致しない** |
| font metrics | retainedは`FontCodec`のWidths/W等と`PaintChar.advance`、場合によりadjacent trace差へ置換。new providerはshaped-font metrics |
| font size / Tz | `SourceParagraph`はstate.size、text×CTM matrix、state.tzからstyle size/horizontal_scaleを算出 |
| rise | style baseline_shift、placed glyph offset。underlineはline baselineを基準にしてriseを足さない |
| alignment | この実験はleft。non-leftのnominal width/tracking経路は別なので同じ結論と決め付けない |
| page coordinates | layout x/baseline/advancesはunrotated page coordinates。保存local座標やtemplate device matrixをauthorityにしない |

同じsemantic textを保つ隣接save間で、glyph intervalsごとにorigin/advanceのraw JSON値を比較した。
firstは`TWO`→`FIVE SEVEN`、changeは`FIVE `削除。identity/scaleの両方で同じ編集を行った。

| case / comparison | max origin delta (pt) | max advance delta (pt) | changed advances | line allocation | prototype bytes equal |
|---|---:|---:|---:|---|---|
| identity first→noop1 | 0.000003051758 | 0.000003814697 | 5 | equal | **no** |
| identity noop1→noop2→noop3 | 0 | 0 | 0 | equal | yes |
| identity change→change-noop | 0.000002288818 | 0.000003814697 | 4 | equal | **no** |
| scale first→noop1 | 0.000005320460 | 0.000006512739 | 13 | equal | **no** |
| scale noop1→noop2→noop3 | 0 | 0 | 0 | equal | yes |
| scale change→change-noop | 0.000005674362 | 0.000007629395 | 3 | equal | **no** |

first→noop1のstyle IDsは両fixtureで変わる。`_bind_paragraph`がretained resourceとnew fontの混在を
output physical stylesへsplitするためであり、IDのrenaming自体をfont意味の変更とは主張しない。
noop1–3/change→noopではstyle ID列は一致している。それでもchange→noopのgeometryは一致しない。

特にidentity fixtureのUnicode offset 3のspaceは、first/noop1ともCourier width=600、Tf=12、Tz=100、
Tc=Ts=0、`PaintChar.advance=7.199999999999999`で一致する。一方、**planned advance**は
`7.199999999999999`→`7.200000762939453`となる。
`ParagraphShaper.shape`のleft alignment分岐で「次unitも同じsource lineの連続retained glyph」なら
`following.observation.origin.x - observation.origin.x`を使うためである。
firstでは次のFがnew provider、reopen後は両方retainedになり、計算経路が変わる。
delete/reflowでもline adjacencyと末尾扱いが変わる。同じlogical/style/font inputから計画値を安定に得る契約がまだない。

#### Numeric prototypeの固定した規則

- recipeはcreation時に一度だけoffset/thicknessを **1e-6 pt、round-half-even**へ量子化し、
  canonical decimal stringとして固定する。正のthickness、finiteのみ、absolute value≤1e9、negative zeroなし、
  指数表記なし、locale/save回数非依存。fill ruleは`nonzero`/`evenodd`。
- current planned coordinatesはround-trip decimal spellingでexact rationalへ変換する。
  **layoutそのものをgridへsnapして差を隠さない**。全source-decimal CTM計算はrationalのまま行う。
- local vertex出力時だけ1e-6 local unitへhalf-even量子化する。quotient/remainderによるexact rational roundingを使い、
  Decimal精度設定によるdouble roundingも避ける。量子化でwidth/heightが0になるrectは拒否する。
- これは狭い実験規則。任意のinput driftを吸収できる保証ではなく、B1-Lを解決する代わりにはならない。
  gridを粗くして既知fixtureを一致させても、half-grid境界やline-wrap境界では保証にならない。

同じrecipe＋同じplanned rows＋同じexact contextはfresh processでもexact same bytes。
しかし実際のreopen入力を使ったgateは以下の結果である（full SHA/body/coordsは新summary）。

| prototype | first | noop1 / noop2 / noop3 | change | change-noop | ops |
|---|---:|---:|---:|---:|---:|
| identity | 208 bytes, `faa375f5…` | 208, `e3d99b63…` | 138, `d822899e…` | 140, `bab30ecf…` | 20 active / 14 after change |
| scale+translation | 170, `c25094b4…` | 168, `58a9f59d…` | 168, `737c8963…` | 168, `fe3537d0…` | 14 |

8 comparisons×2 pagesは**現行runtime PDF**のMuPDF/Poppler 144 dpi差0。prototype bytesはPDFへ挿入しておらず、
prototype renderer equalityを実証したとは言わない。0.002ptはglyph/render accuracy用であり、byte一致判定には一切使わない。

### 15.3 CTMとpaint context

`ContentPage.state.ctm`はsource decimalを完全保持していない。
`content_stream.multiply()`は`pymupdf.Matrix`を使用するため、実測Mは
`[0.8299999833106995,0,0,0.9100000262260437,7.25,11.5]`だった。
PR #19で導入された`continuation.source_ctms()`は既存operator parserが示したcm spanから元のdecimal tokenを読み、
q/Q stackをrationalで合成する。実測Sは`[83/100,0,0,91/100,29/4,23/2]`。
paint prototypeは**Sを使用**し、MやMuPDF paint template matrixを逆変換authorityにしない。

v1はrotation=0、UserUnit=1、MediaBox原点0、CropBox=MediaBox、positive axis-aligned Sのみに限定する。
page高さH、S=(a,0,0,d,e,f)なら、page point (x,y)のlocal座標は
`((x-e)/a, (H-y-f)/d)`。source decimal→rational合成→rational inverse→local decimal量子化を一回だけ行う。
shear/rotation/reflection/singular/不明なcm spellingは拒否する。
将来のruntime gateはSによる結果と実renderer Mによる結果の差も既存PR #19相当の0.002pt accuracy boundで検証する。
このaccuracy gateをcanonicality gateと統合してはならない。

paint用contextはtext source-outputの全Stateをコピーしない。以下を規範とする。

| 分類 | fields / 規則 |
|---|---|
| MUST MATCH | exact source-decimal CTM S、page frame、clip authority（0または1つ）、device fill operator/components、rendering intent evidence、q depth |
| MUST BE DEFAULT / absent | opacity=stroke opacity=1、active ExtGState evidenceなし、page `/Group`なし、DefaultGray/RGB/CMYK overrideなし |
| MUST BE SAFE | pending path=false、pending clip=false、text object=false、marked depth=compatibility depth=0、parser/nesting errorsなし |
| IGNORED in entry digest, must be preserved by execution | stroke color、line width/dash/cap/join/miter、flatness。bodyはstraight fill-onlyなので描画意味に使わず、state operatorを出さないq/Q bodyは値を変えない |
| IGNORED text-only | font resource/xref、Tf/Tz/Tc/Tw/TL/Ts/Tr、text/line matrices。bodyにtextがなく、paragraph planner/font verificationは別authority。textをpaint contextへ混ぜない |
| locator only | content xref、physical stream index、byte offsets、clip `at`。source operatorを特定してexact semanticsを読むために使い、context identityには入れない |

fill color/opacityのauthorityは**entry context**。recipeには重複保存しない。
black .7とblue .9は別group/entry fill＋別recipe thicknessとして保持する。
fill ruleだけはrecipeで決まり、canonical creation時から`f`または`f*`を出す。source `F`はcreation時に`f`へ正規化し、
owned grammarには`F`を許可しない。owned bodyにFが入っていればcanonical grammar mismatchで拒否する。

ExtGStateは`State.opacity==1`だけでは安全といえない。`ContentPage._walk`が`gs`処理時に
`other['ExtGState:<name>']=state_object(resource)`を保存し、ca/CA以外もresource evidenceとして残す。
今回`ca=CA=1, BM=Multiply` fixtureはopacity=1のままだが、active ExtGState evidenceで拒否できた。
entryに1つでもExtGState evidenceがあればv1は全拒否。page `/Group`とdefault color-space overrideはState外なので、
`content.pdf_page`/Resourcesを直接検査する。未知operator/parser errorを見なかったことにして進めない。

clipは0または1つのproven source rectangular path clipのみ。rule W/W*、single re、positive axis-aligned clip CTM、
finite operands、source clip correspondenceを検証する。既存PR #19 clip proofと`_check_clip/contains_fill`で、
paragraph inkと各生成rectangleがclip内であることを確認する。compound/curve/text clipは拒否。
island内ではclipを書かない。read-only analyzerの`eligible_for_further_proof`はこの包含証明や所有権まで行う値ではない。

boundary fixtureはplain/q-line-state/scale/single-rectangle clipの4正例と、pending path、pending clip、compound/curve clip、
marked、compatibility、text object、active ExtGState、page group、default-color overrideの10負例。
これらは本PRのsource fixtureを読むだけで、future marker parserを先行実装していない。

### 15.4 B2/B3: active ownership終了の契約

active→activeは同じmarker pair内のbodyを置換する。所有単位も初回位置も変えない。
初回は既存counterfactual guardに従ってconfirmed source terminalだけを`n`へ消費し、
group最初のterminal位置に新blockを出す。他source terminalも既存どおり`n`へ消費する。
source construction/wrapperはblock外のまま。paragraph末尾/page entryへ集約しないため既存のpaint placementを保つ。

active→terminatedは、編集前のvalid owned recordから**complete block range**を証明してから削除する。
blockの定義はwriterが新規に所有した開始newline、begin line、body、end lineと終端newlineまで。
既存source whitespaceをrangeへ拡張しない。active rewriteはbodyだけ、terminationはcomplete blockを消費する。
両方式とも事前に同じprogram/block hash、marker identity、entry/exit、grammar、segment全単射、foreign不在を検証する。

削除前bodyはpath-free entry→全pathをfillでconsume→path-free exit、entry=exit graphics state。
したがってbodyとcomment pair全体を除いたときもsource suffixには同じstateが渡る。
**q/Qがpathを復元するから安全なのではない**。入出力pathが空で、body内に他path/stateの作用がないことが必要。
initial source `n`、他group、fixed paint、過去の非描画residueはcomplete block外なので保持する。

同じTransactionでrecord/underlines entryを削除し、final rebindで消えたmarker/segmentsが0であることを検査する。
他groupは再bindする。最後のgroup終了時はpaint owner recordも終了し、空groupの所有権を残さない。
current paragraph/text/editable modelは通常のsemantic stateとして残る。
全paragraph emptyはこの遷移より先に拒否する。削除要求でpartial成功を公開しない。

純粋modelではvalid ownershipを前提に、complete owned bytesだけの除去、record同時終了、元state非変更、
未証明削除拒否、全empty拒否、old-ID revival拒否を検査した。
B2のzero-paint plannerとB3のdormant location authorityは「後で実装」ではなく、**NARROW V1の非機能**とする。

### 15.5 B4: single independently managed owner per PDF

`open_editable`はparagraph pageではなく**PDF file全体のSHA**を照合する。
また`inspect_element`はpage全pathを含み、各path IDもPDF SHA/program/range由来。
他ownerのmarkerをprotected-otherとして無視するだけでは、Bのparagraph selectionやpage-global element snapshotを更新できない。

| B4案 | current-only安全性 / 運用負担 | 採否 |
|---|---|---|
| document/page manifest | 全ownersを1 revisionへsealできる。document schema、public coordination API、publication単位の変更が必要 | v1では不採用 |
| all sidecars coordinated | `plan_document_edit`＋1 Transaction＋全bindで現在も同時更新の基盤はある。callerは毎回siblings全件が必要、欠落検出registryも必要 | 独立ordinary APIとは別product contract。v1では不採用 |
| scoped inventory＋safe rebase | 他markerの非干渉だけではtext/fixed relationsの古いID、font/resource、global snapshotのrefreshを証明できない | historyなしの新semantic matching authorityを増やすため不採用 |
| 1 owner/page | 同page問題を避けても、別pageのsaveで全PDF SHAが変わり他modelがstaleになる | 不十分 |
| **1 owner/PDF document** | all paint-domain inventoryが1 current modelに収まる。独立ownerの交互編集を明示的に禁止できる | **採用**。最小の安全制限 |

実測current-runtime fixtureはA/Bを同じPDFへ同時bindした後、Aだけno-op saveした。
B reopenは`needs_confirmation: PDF revision differs from the editable model`。
BのPDF hashだけを書き換えて再sealしても`selection source SHA-256 does not match`。
古いBによるeditも未公開で拒否、current Aはreopen/再saveできた。
これはsafe rebaseが数学的に不可能という証明ではない。現在のAPIに必要なauthorityがないというcode/evidenceであり、
markerだけからcurrent B textの一意対応・保持されたmeaning・fixed relationを再構築する方式は設計していない。

NARROWのnormative sequence:

| stage | 結果 |
|---|---|
| confirm A/B as ordinary legacy observations | 許可。両方をpaint-owningとして登録することは不可 |
| explicitly create owned A | documentにpaint-domain blockがなく、current Aが完全検証できれば許可 |
| create owned B while A exists | **DOCUMENT_OWNER_INVENTORY_MISMATCH**、planning前拒否 |
| edit A | current A＋全paint inventory一致なら許可 |
| reopen old B | **STALE_OWNER_MODEL**。他者保存で古い意味を使うことをpolicyとして拒否 |
| edit B / fresh owned confirm B | old Bではstale拒否、freshでもA blockがunclaimedなのでowned creation拒否 |
| reopen current A → edit A | 許可。拒否したB operationはPDFを変えない |

これはchecksumエラーに偶然依存するpolicyではない。owned entry pointは所有者数/inventory/whole-PDF revisionを
**API contractのpreflight**として検証し、上記named refusalで止める。
legacy ordinary編集は従来のrevision-bound挙動を維持するため、別のlegacy/external APIでPDFを書き換えること自体を
全世界的に禁止できるとは主張しない。その場合current owned modelがstaleになり、owned editingを再開できない。
operational consequenceは明示的であり、暗黙rebase/fallback/cleanupで「回復」しない。
callerが独立ownersの交互保存を必要とするならcoordinated document modelを別scopeとして設計する。

#### Inventory / reconfirmation

paint-domain inventoryはdocument全pageのmerged top-level programsを既存operator lexerで走査する。
完全なstandalone paint comment lineのみ認識し、string等の内部を検索しない。
inline image等で完全inventoryを得られなければowned modeを拒否する。
Form内markerはv1のcreation先にならず、top-level paint authorityとしてclaimできない。
domainは`pdfengine-paint-v1`。source-slot/continuationのmarkerはinventory collisionに数えないが、
そのbyte rangesやauthorityへoverlapするmutationは禁止する。

| current situation | classification / policy |
|---|---|
| current valid record＋inventoryが完全一致 | OWNED BY CURRENT。各groupを個別に再証明して編集 |
| valid-looking other paint marker | PROTECTED OTHER/UNKNOWNという診断のみ。触らず、single-owner policyによりowned operation全体を拒否 |
| marker only / completely lost sidecar | FOREIGN/UNKNOWN。auto-claim、fresh ownership adoption、marker cleanup禁止 |
| stale sidecar | STALE OWNER。semantic refresh/rebase非対応、旧recordを新PDFへ移植しない |
| duplicate/malformed/overlapping pair | INVALID/COLLIDING。未公開で拒否 |
| current legacy model、paint markerなし | 従来処理を維持。明示的owned creation開始だけ許可 |
| legacy model＋unknown paint marker | legacy分析/観測は可、owned promotionは不可。旧権限を再確認したことにしない |
| other ownership domain only | paint inventoryから除外。現行ordinary `_source_output` guardはそのまま |

### 15.6 Persistent contractとidentity（B1-L以外の推奨固定事項）

new capabilityはfuture editable versionとして導入し、v2を自動昇格しない。
最小導入surfaceは**current legacy editable modelからの明示owned creation**とする。
current modelを持たない初回source編集は既存ordinary confirmation/writeを先に行い、
その後のcurrent confirmed modelをcreation入力にする。raw path/shapeだけの作者推定は行わない。
この初回source residueは追加の一回限りのものとして許容し、旧bytesの掃除へ拡張しない。

```text
paint_ownership = {
  version: 1,
  owner_created_from_model_sha256: <immutable creation input model SHA>,
  owner_id: H("pdfengine-paint-owner-v1", logical_element.id, owner_created_from_model_sha256),
  groups: {
    <anchor_id>: {
      created_from: {model_sha256, source_ids: sorted initial IDs, range: initial Unicode range},
      recipe: {offset: canonical decimal, thickness: canonical decimal, fill_rule},
      marker_id: H("pdfengine-paint-v1" + NUL + anchor_id),
      current: {page, block_range, program_sha256, block_sha256, entry_context_sha256}
    }
  }
}
```

`anchor_id = SHA256(canonical JSON({domain:"pdfengine-anchor-v1", owner:owner_id, kind:"underline",
model:created_from.model_sha256, source_ids:sorted IDs, range:initial range}))`。
owner IDのHもUTF-8 sorted-key compact JSON、domain/field名を含める。
marker IDにはlogical ownerを再度入れず、既にownerを含むanchor IDだけをdomain-separated hashする。
body/geometry、current source ID successor、save counter、timestamp、random、xref、offsetはidentityの更新入力にしない。
current Unicode range/affinities/source IDsは`underlines`の対応するanchor_idに一箇所だけ置く。
kindはv1でunderlineのみ、activeだけなので`state`/`revival`/dormant locatorは不要。

current modelはtrusted editing documentで、checksumは署名ではない。immutable creation seedと正規writerが
作ったrecordを信頼の起点にし、current marker/hash/grammar/context/containmentで一貫性を再証明する。
callerが全recordを悪意で偽造・再sealする攻撃まで作者を証明する設計ではない。

group終了後にcallerからold anchor_idを受け取るentry pointは設けない。
新creationはcurrent confirmation model SHA＋現在の明示source IDs/rangeからIDを作るため、
終了したgroupのcreation seedは再利用しない。pure modelも別creation revisionでIDが変わることを確認した。
任意の過去PDF/model pairへのrevision rollbackを、current-onlyでglobal anti-replayできるとは主張しない。
history配列/tombstone配列は不要。created_fromは1回だけ固定、currentは上書き、終了groupは削除する。

### 15.7 Grammar、current-only verification、atomicity

canonical active bodyはexact formatterによる`q\n (x y m\n x y l\n x y l\n x y l\n h\n fill\n)+ Q\n`。
fill=`f`または`f*`。`re/c/n/S/B/F/color/cm/gs/clip/text/marked`等、不要operatorは許可しない。
少なくとも1 segment。operand count、finite/canonical numeric spelling、rect形状、閉path、順序、grammarを検証する。
allowed operator集合だけの検査で終わらせない。

reopenはcurrent PDF＋current model＋fontsだけで次を一括検証する。

1. whole-PDF revision、model/schema、logical owner identity、creation identity、document-wide inventory。
2. 各blockのcomplete marker/range/program/block SHA、context、scope、grammar、生成segmentの全単射。
   current source IDsはcontainment locatorとして使い、次geometryへ還流させない。
3. 全current group segmentsがbodyに完全包含、全body paintがそのgroupに属すること。
   fixed source IDs、foreign path/text/style witness、他island authorityが範囲内にないこと。
4. 上記を全て同じinput revisionで終えてからtext＋paint body rewrite/complete terminationをplanし、1 Transactionへ。
   intermediate text bytesからpaint所有を再証明しない。
5. final PDFで全glyph/path/fixed relationsをrebindし、range/hashを更新、終了marker/record不在を検査、model seal/再open。
   結果全てが検証されるまで公開しない。

entry digestはcreation時の意味を不変量にする一方、program/block/rangeはfinal revisionで更新する。
text-only stateをdigestに含めないため、textのfont resource追加など無関係な変化でpaint contextを再定義しない。
source `_source_output`をordinaryへ流用せず、paint ownershipを独立させる。
既存`write_editable`のtemporary save→bind→seal→pair publicationの構造を使える。
late failureはtemp内で完結し、2件目link例外も既存のrollbackで自分の1件目だけを除去する。
OS crash between linksまでのtwo-file atomicityは現行機構にない。

### 15.8 Fail-closed / implementation acceptance matrix

| 条件・test | 要求結果 |
|---|---|
| first creation | current legacy confirmation＋paint inventory空、source terminalだけconsume、新blockだけowned |
| first→noop1/2/3、change→noop | same semantic stateでbody bytes/SHA/operators/segment coords exact equality。**B1-Lにより現在未達** |
| geometry identity / scale+translation | recipe固定、exact S、planned authority固定点、accuracyとcanonicalityを別gateにする |
| multiline / multiple groups / styles | groupごと元位置、色はentry、thickness/ruleはrecipe。全segmentsとIDsが全単射 |
| fixed background / intervening foreign path | 元bytes/paint保持。source constructionもowned blockへ取り込まない |
| q / line-state / source suffix | path-free entry/exit、同じpaint context、ignored stateにも副作用なし |
| single rectangle clip | source correspondenceと全generated rect包含。compound/curve/text/unknown clip拒否 |
| multiple paragraphs same/different page | second independent paint ownerはplanning前のpolicy refusal。stale siblingを自動rebaseしない |
| fresh-process reopen | current PDF/model/fontsのみ。previous report/PDF/mutation historyなし |
| full paragraph empty | output pairなしでFULL_EMPTY_REFUSED。partial group終了を先に公開しない |
| group deletion | complete block＋underlines entry＋group recordの一括終了、foreign gaps/初回residueを保持 |
| last group deletion | paint owner recordも終了。空body/dormant権限を作らない |
| reinsert / old revival | text再挿入はundecorated。old ID revival拒否、新確認sourceからのcreationだけ新ID |
| missing/duplicate/malformed marker、anchor mismatch | 拒否、recordへの自動採用なし |
| block/program/context tamper | 再sealしたmodelでもsemantic/containment不一致は拒否。自己署名で権限が増えるとしない |
| foreign segment inside / owned segment outside / fixed ID inside | 双方向containment mismatch、未公開で拒否 |
| overlapping blocks/mutations | whole-input preflightで拒否、orderで解決しない |
| unsupported CTM/page/clip/state/ExtGState | finite/positive/explicit scopeを満たさなければ拒否 |
| stale owner / unknown other marker / lost sidecar | B4 named policy refusal。legacy anchored rewriteへfallbackしない |
| legacy | 現行behavior維持、explicit creation以後だけowned。旧residue cleanup禁止 |
| late rollback | temp save後paint rebind失敗、record削除失敗、pair publication失敗で公開pairなし |

paint-only no-op KPI: delta **0 bytes / 0 operators**、新paint residue 0、marker/anchor identity不変。
全page増分0は要求しない。ordinary text growthを別計上し、page delta=text delta＋paint deltaとして照合する。
renderer KPI: MuPDF＋available Poppler diff0、fixed/foreign/source suffix保持。
prototypeの同一入力決定性、current planned inputsの安定性、renderer accuracyの3 gateを相互に代用しない。

### 15.9 残blockerと次PR

| exact unresolved question | safety/canonicalityへの影響 | code path | minimal next evidence |
|---|---|---|---|
| **B1-L:** 保存前後でretained/new/line-end間のadvance authorityをどう統一し、同じlogical/style/font/layout入力を固定点にするか | paintがglyphから離れる、line-wrap boundaryが変わる、markerだけ入れてもno-op bodyが変わる。現在のplanを信用してREADYとできない | `ParagraphShaper.shape`のleft adjacent-trace分岐／末尾Tc調整／new shaped provider→`layout_attributed`→text serialization→`_bind_paragraph`→`SourceParagraph` | offset3 spaceの同じ600/12/100 metrics反例をまず解消。identity/0.83–0.91 CTMでfirst/change→noop×3、provider境界、line末尾、widthをwrap境界付近にしたfixture。origin/advance/line/style意味のexact比較とglyph accuracy≤0.002ptを別々に示す |

recipe＋paint CTM feedbackの除去、numeric formatterの決定性は確認できたが、B1全体は閉じていない。
**次PR scope:** canonical paragraph layout authorityのdesign/evidenceに限定する。
nominal font width＋明示tracking/word spacingとsource positioning intentをどう保つか、retained/source advancesを
明示されたlogical geometryとして保持する必要があるかを比較する。観測noiseを丸めるだけ、旧paint bodyの再利用、
初回だけ隠れたnormalization saveを挟む方法は「同じsemantic入力の固定点」の証明にしない。
runtimeのtext-layout変更が必要なら別途承認された実装PRへ切り出し、今回のpaint contractへ隠して入れない。

B2はdormant非対応＋complete termination、B3はactive boundary/isolation＋削除時state等価、
B4はdocument単一owner＋rebase非対応というscopeで決定した。
**NOT READY**は未完の実装を隠すラベルではなく、実測で残ったB1-Lのためである。
次のOpusレビュー対象は、この因果関係、scope reductionの運用制約、完全block削除の権限、
context subset、global inventory、失効modelの扱いとB1-Lの最小追加証拠。

### 15.10 検証

新design/evidence tests 6＋既存focused tests 5＝**11 passed**。
geometryは12 current-runtime saves/10 fresh-process re-edits、8 dual-renderer no-op comparisons。
sidecar fixtureは1 coordinated initial save＋A単独2 saves、B stale refusal（未公開）。
read-only boundary 14 cases。pure identity/inventory/termination/refusal casesも新summaryへ保存した。
runtime digestは`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`で不変。
full suite、外部LibreOffice原本、future owned-PDF writerは実行・実装していない。

### 15.11 Independent review — Claude Opus 5.5

- reviewed HEAD: `5e60936959400f8865eac3673f158faa38bf5624`（レビュー開始時のPR #36 head。この記録を追加するcommitとは別）
- base: `fdba3dcb44b0fe4010a3ea1fc986b3aac83da2e6`（main。PR #35 head `7920e37` を含むmerge commitであることを確認）
- **verdict: PASS WITH NON-BLOCKING NOTES — DESIGN/EVIDENCE ONLY; RUNTIME NOT READY**
- B1-L: **未解決（OPEN）**。runtime implementation: **NOT READY**。

差分はdocs/evaluations/testsの8 files、追加のみ（削除行0）。`pdfeditor/`、schema、writer、Transaction/MutationProgramは無変更、
runtime digestは`d22fb048…5dea`のまま。§1–14、PR #35 summary/evaluator/Opusレビューは変更されていない。
このPRにはCI check runが設定されていない。

**検証**（Linux cloud、Python 3.12.3、PyMuPDF 1.27.2.3、Poppler pdftoppm 24.02.0、`requirements.lock.txt`）:
README記載のfocused tests **11 passed**。`paint_contract` evaluatorを一時出力先で再実行した
（tracked summaryは上書きしていない。Windows固定の`DEFAULT_POPPLER`だけをscratch wrapperで`/usr/bin/pdftoppm`へ差し替え、
repo fileは変更していない）。生成summaryはcommit済み`paint-contract-summary.json`と**全fieldで完全一致（差分0）**。
prototype SHA、glyph origin/advance、renderer結果を含めてWindows記録が再現したので、B1-Lはplatform固有の現象ではない。
full suite: 未実行。外部Windows/LibreOffice原本validation: 未実行（本PRの要件外）。

#### 論点別の結論

- **A. NARROW V1:** 安全な縮小。§15.1は機能を「後で実装」ではなく契約上の非機能として除外している。具体的には、
  dormant/`q Q` body、revival requestをunknown optionとして拒否、full empty拒否、visible segment 0拒否、
  `project_range`=Noneのgroupだけcomplete block＋recordを同時終了、old ID再利用不可、再挿入textは装飾なし。
  §6–8/§11のFULL案は残るが、§15.1が「歴史的候補でありNARROWが次scope」と明記しているので、実装対象は曖昧でない（N7）。
- **B. B4 document-single-owner:** 正しい。`source_sha`はfile全体のbytesをhashし（`selection.py:15`）、
  `open_editable`はそのSHA不一致を拒否する（`editable.py:52`）。そのためpage単位の制限では足りないことはcodeから直接言える。
  A/B evidenceでは、hashだけを差し替えても`selection source SHA-256`で失敗し、marker inventoryだけではselection/snapshotをrefreshできない。
  §15.5は「数学的に不可能」とは主張していない。unknown/stale/foreign markerを自動adoption・rebase・cleanupしない規定もある。
  運用上は強い制限だが、v1の安全側の制限として成立する。
- **C. complete block termination:** 契約は十分。範囲はwriterが所有するnewline/marker/body/markerに限定され、
  source whitespace、initial `n`、construction/wrapper、fixed/foreign paint、residueを含まない。
  current revisionで行う再証明（marker、program/block SHA、context、grammar、全単射、foreign不在）、
  path-free entry/exit、「q/Qがpathを復元するから」ではないという根拠、同一Transactionでのrecord削除、partial publication禁止を確認した。
- **D. paint context / grammar:** 過不足なし。exact S、clip（0または1つのrect）、fill operator/components、q depthはMUST MATCH。
  ExtGState evidence、page `/Group`、DefaultGray/RGB/CMYK、pending path/clip、marked、compatibility、text objectは拒否する。
  stroke-only/text-only stateはdigest外だが、bodyが副作用を持たないので保持される。text clip（Tr≥4）は
  `state.clip`へpathなしで積まれるため、analyzerのclip条件で拒否されることをcodeで確認した（fixtureはない）。
  `q (m l l l h f|f*)+ Q`は現行writer（`anchors.py` `_rect_commands`のm/l/h）由来で、active-only scopeでは閉じている。
  creation時の`F`→`f`と、owned bodyで`F`を拒否する判断も妥当。
- **E. B1-L因果:** 結論は**正しい**。ただし説明は網羅的ではない（N1）。`ParagraphShaper.shape`（`paragraph.py:175-187`）のleft分岐は、
  次unitがretained・同じsource line・source offsetが連続という条件を満たすときに、metric advanceをMuPDF trace origin差へ置き換える。
  offset 3 spaceはfirst/noop1ともwidth 600/Tf 12/Tz 100/Tc=Ts=0、`PaintChar.advance=7.199999999999999`。
  firstでは次の`F`がnew provider `s0`なのでmetric値を使い、noop1では`F`がretainedになるので`48.8000031−41.6000023`（float32）=`7.200000762939453`を使う。
  これはstyle ID renameでもpaint geometry誤差でもない。
- **F. canonical formatter:** recipe固定、1e-6 grid、exact rationalのhalf-even（tieとNaN/±Inf/過大値も確認）、
  negative zeroなし、exponentなし、exact S、planned layoutはround-trip decimalのまま扱いsnapしない、をcodeとtestで確認した。
  2e-6 ptの入力差でもbytesが変わることがtestされており、tolerance gateとは分離されている。
  PDFへ挿入されたowned writerだという過剰主張はない。実測でもgridはdriftを隠していない（例: `71.909377`→`71.909378`）。
- **G. 次PR scope:** canonical paragraph layout authority design/evidenceが妥当。paint runtime implementationへ進む根拠はまだない。

#### Blocking findings

なし。

#### Non-blocking findings

1. **B1-Lのtrigger列挙が不完全。** 実測driftには三つの経路がある:
   (i) new→retained provider遷移（offset 3）、
   (ii) reflow/削除によるsource line・source連続性の変化（identity first→noop1 index 14は元source line 1末尾のspaceで、
   firstでは`following.line`不一致のためmetric、reflow後は同じlineになりtrace。change→noop index 3は`FIVE `削除で
   `original_offsets`の連続性が切れたためmetricになったもので、new providerが原因ではない）、
   (iii) retained同士のtrace差はabsolute x位置のfloat32量子化に依存し、位置が動くと値が変わる
   （index 16 `H`は両stageで隣接retainedなのに`7.200000762939453`→`7.1999969482421875`）。
   次PRはprovider境界の統一だけでなく、trace差そのものをadvance authorityにするかどうかを扱う必要がある。
2. **legacy mutationとpaint-domain blockのoverlap規定がない。** §15.5でoverlap禁止を明示しているのはsource-slot/continuation markerだけ。
   owner sidecarを失った・staleになった後、同じparagraphをlegacy再確認して編集すると、`_candidates`がowned fillを
   source underlineとして`n`化し、marker pair内部を書き換え得る。fail-closed（後でINVALID）ではあるが、runtime前に
   「paint-domain block rangeへ重なるordinary mutationは拒否」をacceptance matrixへ加えること。
3. **creation recipeのauthorityが未規定。** prototypeのrecipeは現行runtimeのMuPDF float32由来のgroup offsetである
   （identity `1.300003/0.699997`。source `.7`からは`1.3/.7`。scaleは`1.182999/0.636997`）。
   S同様、source path decimal operand＋exact Sから導出すると規定するのが一貫している。noop canonicalityには影響しない。
4. terminationのacceptance matrixに、block除去後のoperator列が「除去前−block」と一致するという
   lexical/token境界不変条件を明示gateとして加えると良い（final rebindでも検出は可能）。
5. B4のcross-page主張はcode事実（whole-file SHA）に基づく。runtime fixtureはsame-pageだけで、symbolic testはpage名のラベルだけである。
   summaryの`sidecars.narrow_sequence`は実測値の隣に置かれたnormative/symbolicな列なので、そう分かるlabelが望ましい。
6. 既存clip proofのCTM（`_clip_source_ctms`はbinary64 parse値を合成）とpaint vertexのdecimal Sは別authorityである。
   runtimeでは、同じSに対してclip包含を証明するか、差のboundを明記すること。
7. §6–8/§11のFULL/dormant記述には前方参照がない（§1–14不変の方針による）。runtime PRは§15だけを仕様として引用すること。
8. evaluatorは`DEFAULT_POPPLER`がWindows pathに固定されており、Linuxでの再現にはwrapperが必要だった（focused testsには影響しない）。
9. 次PR matrixには、active range内への挿入（range growth）→noop、non-left alignmentの扱い（scope外と明記するか、対象にするか）、
   first→noop1のphysical style ID splitを踏まえた「semantic style」比較の定義も含めること。

#### 次PRとして妥当な最小scope

canonical paragraph layout authority design/evidence（runtime paint実装・tolerance緩和・粗いquantization・hidden normalization saveなし）。
最低限、以下を扱う:

- retained / new / retained-new境界、source line変化（reflow）、削除によるsource非連続、line-end Tc処理、
  explicit tracking/word spacing、wrap境界付近のwidth
- identity CTMとscale+translation CTM（0.83/0.91）
- first→noop×3、change→noop×3、range growth→noop
- origin/advance/line allocation/semantic styleのexact比較と、glyph accuracy≤0.002ptを別gateとして示すこと
- N1(iii)のtrace差position依存の扱いを必須とする。text-layout runtime変更が必要なら、別途承認された実装PRへ切り出す。
