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


## 16. B1-L canonical layout authority evidence — 2026-10-04

**Verdict: NOT READY. Design/evidence only; no layout or paint runtime implementation.**
PR #36 was verified merged at `b97c6b8dbba33176f25554ae35618a2ae2568941`, the starting
main for branch `codex/canonical-layout-authority`. Sections 1–15, including the
Opus review in §15.11, are historical evidence and remain unchanged.

### 16.1 Root causes and the authority boundary

The complete path is `ContentPage` → `SourceParagraph` → `apply_edits` →
`ParagraphShaper.shape` → `rich_layout.layout_attributed` → paragraph glyph
serialization → `_bind_paragraph` → reopened `SourceParagraph`.

| Value / path | Meaning and present behavior |
|---|---|
| Font `/Widths` or `/W`, supplied font instance / hmtx / HarfBuzz integers | Potential semantic metric input, provided font identity, instance, shaping policy and source intent are established. A subset resource alias or glyph number alone is not a font identity. |
| Source `Tf`, `Tc`, `Tw`, `Tz`, `Ts`, `Tm`, numeric `TJ`, CTM | Source positioning facts. Whether spacing is logical inline spacing or a line-specific adjustment requires a scope rule/confirmation. Exact source decimal operands are preferable to reconstructed float32 matrices. |
| `PaintChar.advance` | `(width/1000*Tf + Tc + (Tw for single-byte space))*Tz/100`, evaluated in binary64. Numeric TJ separately advances Tm by `-TJ/1000*Tf*Tz/100`; it is not in PaintChar.advance. |
| Retained, left-aligned glyph | Starts from PaintChar.advance times horizontal source matrix. If the next retained unit is on the same source line with consecutive original logical offsets, replaces that advance with the difference of absolute trace origins. At candidate line end removes Tc. |
| New provider, left-aligned glyph | HarfBuzz advance times size/scale/upem plus confirmed logical tracking, with tracking removed at candidate line end. Source Tw/TJ is not independently represented by this expression. Shaping run boundaries currently depend on retained/new providers. |
| Trace origin / bbox, physical source line, current glyph ID and resource/style ID | Physical observations/bindings. Serialization, float32 transforms and reflow can change them without a requested semantic change. They must not redefine logical metric authority. |
| `layout_attributed` | Shapes each candidate slice, trims trailing spaces, considers pen advances **and ink overhang/inset**, and combines ascent/descent for baselines. Width fit currently permits `1e-7`; that is not an exact canonicality comparison. |
| Serializer | Each glyph receives a Tm; `pymupdf.Point`/Matrix transformations use float32, then operands are formatted with `.12g`. Tw is reset; source adjacency is reconstructed in the resulting program. |
| `_bind_paragraph` / reopen | Rebinds Unicode offsets to current glyphs, verifies styles with `_close`, reconstructs styles and lines from current physical witnesses, and carries supplied-font recipes. It does not persist a canonical logical advance/ink record. |

L1 is provider transition: at identity offset 3 the following F changes from new
to retained, switching metric to trace. PaintChar.advance stays
`7.199999999999999`, while the plan becomes `7.200000762939453`.
L2 is adjacency/line transition: offset 14's next retained glyph moves onto the
same source line after reflow; after deleting `FIVE `, offset 3's original
logical offsets are discontinuous until rebind. Neither requires a new glyph.
L3 is retained-retained trace instability: offset 16 uses consecutive retained
trace differences in both stages but changes from `7.200000762939453` to
`7.1999969482421875` as absolute origins change.

**Additional isolated blocker B1-L-M:** replacing all advances by the same
constant does not remove physical observation from the rest of layout. The
unembedded Courier fallback `source_ink` subtracts origin from trace bbox.
With `AB`, both advances forced to exactly the same Python value `7.2`, the
measured two-glyph width is `14.400000762939452` at source x=20 and `14.4` at
source x=100 or 200. Re-layout all three at the **same** target x=20 and width
`14.400000381469727`: the first breaks into two lines, the others fit one.
The difference exceeds the existing fit epsilon. No provider transition,
spacing edit, or font change is involved. This is a read-only shaping probe,
not a modified runtime writer or a claimed candidate PDF roundtrip.

Vertical metrics also consume trace bbox (plus embedded hhea maxima); the
present probe proves the ink/width counterexample, not every possible vertical
failure. A design must choose canonical ink, ascent/descent and empty-line
metrics as well as advances before claiming stable line allocation/baselines.
Scale lifecycle additionally shows reconstructed `font_size` and
`horizontal_scale` drifting separately, even on some geometry-stable no-ops.
Those exact semantic-value differences are not physical style-ID renames.

### 16.2 Candidate comparison and decision

| Candidate | Evidence / decision |
|---|---|
| A: retain adjacent trace differences | **Rejected.** L1/L2 select different branches; L3 is position-dependent even with the same branch. Later no-ops becoming stable does not validate first→noop. |
| B: nominal/shaped metrics + Tf/Tz/Tc/Tw/tracking | **Insufficient alone.** Numeric TJ fixture adds 1.2 pt to an edge (0.996 under scale); explicit Tm adds 2.8 pt (2.324 under scale). Nominal-only loses these, well beyond 0.002 pt. Source spacing intent must be represented or refused. Canonical advances still leave B1-L-M. |
| C: creation-time logical geometry record | Useful component if it stores current logical metrics/intent rather than absolute source positions. Freezing observed advances alone preserves observation noise, does not cover ink/style/line-end rules, and does not prove a current PDF matches the record. **Not selected as a complete authority.** |
| D: source intent + supplied font shaping, lowered to one logical representation | **Preferred direction, not an accepted complete contract.** Both must enter the same provider-independent metric/spacing/ink representation before candidate measurement. The experiment proves only exact arithmetic/determinism of preconfirmed ASCII advance cells. Font/subset equivalence, full measurement and current-only binding are unresolved. |

The provisional D representation contains current Unicode intervals/cluster
identity, stable font instance identity, metric numerator/upem, logical
size/horizontal scale/rise, confirmed inter-glyph tracking/word spacing, and
separate explicit positioning intent. A complete proposal must add canonical
ink bounds, ascent/descent, shaping features/version and cluster/run boundary
rules. Every candidate line must apply the same rules irrespective of the
current retained/new classification. Reflow recomputes origin/allocation;
source line identity never selects a different advance authority.

A current record is O(current text + current styles/fonts), not an append-only
history. Delete removes its cells/incident positioning edges; insert constructs
new cells from a verified current font recipe. No previous PDF/report, old
absolute x/y, earlier line allocation or hidden normalization save is an input.
A future binding gate must prove current Unicode/code/outline/width/style and
current placement against the record. Whole-PDF hashes and self-sealed JSON
alone do not prove this. An arbitrary logical record cannot authorize unrelated
paint or silently adopt a changed font. That verifier is **not implemented**.

### 16.3 Explicit narrow scope and refusals

**Left alignment only.** Right/center/justify are outside this authority
experiment; they have distinct spacing/gap rules in current runtime and need
separate design evidence. Current runtime support is unchanged.

The pure candidate accepts preconfirmed printable ASCII, one glyph per
codepoint, zero shaping offsets, positive horizontal metrics, and a supplied
exact layout region/leading. Retained nominal metrics and new integer shaped
metrics can both produce the same rational cells. It suppresses tracking at
line end and omits trailing-space paint while retaining those logical cells.
Exact rational calculations are not a snap grid. Width changes of 0.000001 pt
can legitimately change allocation; identical width/input must not.

Tc is admitted only as confirmed inline tracking. Tw needs confirmed logical
word-space intent; observed line distribution must not silently become typing
style. Numeric TJ and independent Tm/Td positioning are **explicitly refused
by the candidate** pending an edge-intent contract. In particular, whether an
edge survives reflow, is suppressed at line end, or disappears when either
endpoint is deleted/inserted between is not inferred from nearby geometry.
The observations retain these cases to demonstrate why B cannot replace them
silently. Current runtime can preserve their physical gaps; that is not a
canonical intent contract. Variable line spacing, contextual/ligature shaping,
multi-codepoint glyphs, arbitrary fonts without a verified metric program,
rotation/shear, vertical writing and non-left alignment remain outside the
prototype. This is a research subset, not a declaration that its whole runtime
implementation is ready.

### 16.4 Semantic comparison

Physical glyph ID, source index/line, resource alias, provider and style ID are
recorded for diagnosis, never compared as semantic style equality. The
semantic comparison includes font meaning, size, horizontal scale, tracking,
rise, fill, and whether word-spacing intent is still unconfirmed. Numeric
representations such as 0, -0.0 and decimal string "0" normalize to the same
rational; **distinct values are never rounded together**. Unknown tracking is
not zero. The controlled fixtures identify Courier/WinAnsi/600 by construction
and supplied fonts by original asset SHA (including current-sidecar font
recipes), not by subset prefix. This is a fixture lineage premise, **not a
general font equivalence proof**; current glyph codes/IDs and physical font
names remain separate evidence. A production semantic font identity needs
instance/variation/features and verified subset glyph equivalence.

Raw per-stage rows record offsets, glyph identity/code, semantic font/style,
provider, source offset/line, next-provider/same-line/continuity, font width or
integer shaped metrics/upem, PaintChar.advance, Tc/Tw/Tz/Tf, source matrix,
planned origin/advance, line baseline, ink/ascent/descent and bounds authority.
Nonpainted logical whitespace offsets are listed explicitly. The observer
re-shapes each final line and requires exact agreement with its saved plan.

### 16.5 Evidence matrix and gate interpretation

| Matrix item | Actual evidence / acceptance distinction |
|---|---|
| retained→retained, new→retained, retained→new/mixed boundary | Identity and scaled initial replacement plus insertion; L1 and L3 named rows retained. Provider classifications are observed, not assumed equal. |
| consecutive/discontinuous offsets; same/different source lines | Offset 14 first→noop, offset 3 delete→noop; source adjacency recorded before each save. |
| first→noop1→noop2→noop3 | Fresh processes, current PDF/model/font only; first→noop fails exact geometry in both CTMs. |
| change/delete→noop1→noop2→noop3 | `FIVE ` deletion; first no-op fails in both CTMs, later plans stable. |
| active-range growth→noop | Insert ` EXTRA` inside the active underline range; range grows from [0,20] to [0,26]. Identity geometry exact, scaled geometry not exact. |
| reflow→noop | Explicit width changes from 80 to 65; changed allocation is intentional, following no-op exact in both CTMs. |
| line middle/end/trailing whitespace | Spacing fixtures use `A B C `; final logical space omitted, last painted glyph uses line-end handling. Tc explicitly confirmed. |
| near wrap / tiny width changes | Pure candidate and actual `layout_attributed` probes at 14.399999/14.4/14.400001. B1-L-M additionally holds target width fixed while source observation changes. No near-boundary PDF save is claimed. |
| default / Tc / Tw / Tz / numeric TJ / explicit Tm | Six source programs under each of identity and exact .83/.91 + 7.25/11.5. Each first→noop saved/reopened. |
| semantic style | Compare actual values after numeric representation normalization; style-ID splits alone cannot cause failure. Scaled size/scale drift remains a failure. |
| candidate determinism | First/change/growth/reflow preconfirmed logical inputs replayed three times in fresh processes; exact plans match. **Not** a PDF lifecycle/binding proof. |
| accuracy | Every actual save compares planned origins to saved MuPDF trace via current bound glyph IDs; source spacing additionally compares independent exact-decimal fixture positions to source trace. Bound ≤0.002 pt, separately from exactness. |

The lifecycle evidence comprises 24 saves / 22 fresh-process re-edits; spacing
adds 24 saves / 12 fresh-process re-edits. All 48 saved-placement accuracy checks
pass ≤0.002 pt. No raster equality is used to promote a failing exact gate.
The new evaluator does not run Poppler or LibreOffice. PR #36's dual-renderer
observations remain historical evidence, not a new validation claim.

Identity first→noop max origin/advance deltas:
`3.051757815342171e-06` / `3.814697265625e-06` pt.
Scaled first→noop: `5.320459607105477e-06` / `6.51273876517422e-06` pt.
Scaled growth→noop: `3.992579877376556e-06` / `4.08664345741272e-06` pt.
All are **canonicality failures**, despite their accuracy being acceptable.
Source TJ and manual Tm lose approximately 1.2 and 2.8 pt respectively under B;
that is a source-intent accuracy failure, independent of fixed-point concerns.
See the separate [compact summary](../evaluations/anchors/layout-authority-summary.json)
and [reproduction](../evaluations/anchors/README.md#b1-l-layout-authority--2026-10-04).

### 16.6 Remaining gate and minimum next scope

**NOT READY** is the intended success-B result. No complete authority is
selected. B1-L-M is a concrete counterexample to advance-only closure, and
scaled semantic style reconstruction is an additional unresolved input loop.
Next scope is a small **layout measurement/verification design**: choose
provider-independent ink/ascent/descent and exact size/scale authority; define
current-PDF font/metric binding, with refusal when a source metric program is
unavailable. Reproduce the fixed-advance AB counterexample and source/new mixed
case under that full representation. Keep TJ/Tm refused unless a current-only
edge policy is explicitly closed. Only then assess a separate layout runtime
implementation PR. Paint runtime is not ready.

No `pdfeditor/`, public API, schema, serializer, marker parser or paint writer
changes. Runtime digest remains
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.
The §15.11 future paint obligations remain mandatory: reject ordinary/legacy
mutation overlap with paint blocks, derive creation recipe from source decimal
path + exact S, prove termination operator stream = before minus owned block,
add cross-page runtime fixture, and unify clip proof CTM with paint S authority.
No duplicate TODO file or implementation is introduced here.


### 16.7 Validation

New design tests 17 + existing focused paragraph/rich-layout/spacing/style
confirmation tests 85 = **102 passed**. After tightening semantic numeric
representation normalization, the six affected pure candidate/style tests
passed again. The final evaluator also requires its re-shaped advance to
match each actual report exactly. Full suite: not run (design/evidence scope).
External Windows/LibreOffice original validation: not run. The evaluator and
focused tests ran locally on Windows, Python 3.12.14 / PyMuPDF 1.27.2.3.


Future design acceptance gates (not yet passed as a whole):

- Same semantic input must reproduce exact origins, advances, allocation,
  baselines and semantic styles for **every** no-op edge in §16.5, including
  first/change/growth. A changed width/edit is compared only to its own next
  no-op, not required to retain the preceding allocation.
- A provider-independent full measurement tuple (advance, offset, ink,
  ascent/descent) must close B1-L-M at both near-boundary widths and both CTMs.
  A source without a provable canonical metric source must be refused rather
  than silently importing a fresh trace box into that tuple.
- Source intent accuracy and planned-to-saved placement must separately meet
  ≤0.002 pt. Current-only font/style/Unicode/position binding must fail on
  changed witnesses even if a sidecar is resealed. General binding is still
  unimplemented and unproven in this experiment.
- Unconfirmed positioning intent, unsupported shaping and non-left alignment
  must be explicit scope refusals in that future proposal; no fallback to A.

Measured maximum saved-origin discrepancy across the 48 saves is
`0.0000152587890625` pt. Maximum independent source-decimal fixture discrepancy
is `0.0000053405761732960855` pt. Both pass the accuracy bound and neither
alters the NOT READY canonicality verdict.

### 16.8 Independent review — Claude Opus 5.5

- reviewed HEAD: `74309256d620c85079fe676fa02273ad575065a8`（レビュー開始時のPR #37 head。この記録を追加するcommitとは別）
- base: `b97c6b8dbba33176f25554ae35618a2ae2568941`（main。PR #36 head `d0dfd08` を含むmerge commitであることを確認）
- **verdict: PASS WITH NON-BLOCKING NOTES — DESIGN/EVIDENCE ONLY; NOT READY**
- layout runtime: **NOT READY**。paint runtime: **NOT READY**。B1-L（B1-L-Mを含む）: **OPEN**。

差分は7 files、追加のみ（削除行0）。`pdfeditor/`、schema、serializer、paint/marker実装は無変更。
runtime digestは`d22fb048…5dea`のまま。PR #37にはCI check runが設定されていない。

**検証**（Linux cloud、Python 3.12.3、PyMuPDF 1.27.2.3、`requirements.lock.txt`）:

- README記載のfocused tests: **102 passed**。
- evaluator再実行: `layout_observation`をscratch出力先で再実行した（tracked summaryは上書きしていない）。
  tracked `layout-authority-summary.json`との差分は`python`のversion表記（3.12.14→3.12.3）の1 fieldだけで、
  それ以外の全値が完全一致した。L1–L3とB1-L-Mは、Windows固有でもplatform依存でもない。
- full suite: 未実行。外部Windows/LibreOffice原本validation: 未実行（要件外）。

#### 論点別の結論

- **L1/L2/L3:** 3つとも独立したtriggerとして成立する。testsは実lifecycleをLinux上でliveに再実行してassertしている。
  - L1（offset 3）: width 600/Tf 12/Tz 100/`PaintChar.advance=7.199999999999999`は不変のまま、`metric`→`trace-difference`に切り替わる。
  - L2: offset 14はproviderが両stageとも`original`で、`same_source_line`がFalse→Trueになる。change offset 3は
    `source_contiguous`がFalse→Trueになる。どちらもproviderの変化を伴わない。
  - L3: offset 16は両stageとも`trace-difference`かつ連続しているが、`trace_pair`の位置が変わってadvanceが変わる。
  - これらは`paragraph.py`のleft分岐とcodeが一致する。
- **B1-L-M:** 現行runtime構造に由来するfixed-point blockerとして**成立**する。evaluator bug、fixture error、
  toleranceだけの問題、provider遷移の別表現のいずれでもない。
  - `layout_attributed.measured()`は`width = right − left`を計算する（`rich_layout.py:161-184`）。
    `left = min(0, pen+ink.x0)`、`right = max(pen, pen+ink.x1)`で、ascent/descentもink y extentとのmaxを取る。
    ink insetはglyph origin（`pen = line_x + metrics.inset`）にも効く。fit判定は`width ≤ available + 1e-7`。
  - `source_ink()`はfont programを読めないsource glyphについて、`trace bbox − trace origin`をinkとして返す（`paragraph.py:159`）。
  - Courier `AB`でadvanceを両方`7.2`に固定しても、ink x1はsource x=20で`7.200000762939453`、
    x=100/200で`7.1999969482421875`。target width `14.400000381469727`では2 lines対1 lineになった。
    **width 14.4ちょうどでも**、x=20だけが2 linesになる。
  - どのepsilonを選んでもnoise幅のbandが残り、そのband内ではsource位置によってallocationが決まる。
    したがってtoleranceの調整では閉じない。
  - 「advanceを揃えれば解決する」という単純化は、このcode/evidenceで否定されている。
- **Authority A–D:**
  - A（adjacent trace）のrejectはL1–L3から妥当。
  - B単独が不十分という評価も妥当。TJ 1.2 pt（=100/1000×12）、Tm 2.8 pt（=30−27.2）、scale時はそれぞれ×0.83で、算術どおり。
  - Cをcomplete authorityとしないことも妥当。advanceの記録だけではB1-L-Mもcurrent bindingも閉じない。
  - Dは「preferred direction、未採用」と正しく位置付けられている。retained/newで同じmeasurement ruleを使う方向、
    traceをlogical authorityへ暗黙昇格しないこと、source positioning intentの保存/確認、font/subset/shaping verificationが未解決であることを明記している。
- **scope/refusal:** 研究用design/evidence scopeとして妥当。left-only、printable ASCII、1 glyph/codepoint、zero offset、
  confirmed Tc/Twに限定している。TJ/Tmは`SOURCE_POSITIONING_INTENT_UNRESOLVED`、non-leftは`NON_LEFT_OUT_OF_SCOPE`で明示的に拒否し、
  「将来対応」として曖昧に残していない。「runtimeが物理gapを保持できる」ことと「intentを確定できている」ことも区別されている。
- **pure candidate:** exact rationalでgrid snapやtoleranceは使っていない。fresh processで3回とも一致した。
  advance-only、rich_layout非再現、PDF binding/lifecycleの証明ではない、という点をdocstring・summary premise・§16が明記している。誤昇格はない。
- **canonicality vs accuracy:** 分離は維持されている。48 savesすべてがaccuracy ≤0.002 pt（最大`1.52587890625e-05`）だが、
  first/change（両CTM）とscaled growth→noopはcanonicality failureとして扱われている。
  後続のno-opが安定することをもってB1-Lを閉じてはいない。
- **paint obligations:** §15.11の5項目は§16.6で失われずに追跡されている（今回は未実装で正しい）。

#### Blocking findings

なし。

#### Non-blocking findings

1. **scaled semantic styleは「ratchet」である。** ctm lifecycleで再構成`font_size`は
   first `10.920000314712524`→noop1 `10.919999599456787`→noop2 `10.91999888420105`→noop3 `10.919998168945312`→change `10.919997453689575`→change_noop1 `10.919996738433838`と、
   saveごとに約7.15e-7ずつ**6回連続で単調に減少**し、その後に止まる。horizontal_scaleも同様に増加する。
   geometryが一致するnoop1→noop2→noop3でも進むので、geometry gateだけでは検出できない。
   また、firstの値自体がfloat32 CTM由来で、exactな10.92ではない。§16.1の「some geometry-stable no-ops」は控えめな表現なので、
   次PRでは独立した名前付きblocker（style reconstruction loop）とsemantic gateとして扱うこと。
2. **spacingの個別結果がdocにない。** summaryには次の結果がある。docの§16.5へ明記すること:
   - Tz_scaled/Tm_scaledでは、編集なしのfirst→noopでgeometryが不一致（L3）。
   - Tw（identity）ではsemantic不一致。serializerがTwをresetするため、source Tw intentがfirst saveで物理位置へ変換され、
     `unconfirmed-source-adjustment`→`none-observed`となる。
   - scaled 6 caseはすべてsemantic不一致。
3. **B1-L-Mの適用範囲。** 対象はtrace bbox fallback（unembedded、またはfontToolsで読めないprogram）である。
   Courierのtrace bboxはadvance box＋float32 noiseにすぎない。embeddedのTT/OTFはoutline boundsなので位置に依存しない。
   一方、ascent/descentはhheaが上回らない限り、全retained glyphでtrace由来になる。次PRは`bounds_source`別に列挙すること。
   unembedded base-14については、拒否以外に標準AFM metricsをcanonical sourceにする選択肢も比較すると良い。
4. **候補の補足。**
   - (E) PR #19方式のexact operator算術（decimal Tf/Tm/TJ/Widths）でtrace observationを置き換える案は、Dのsource intent入力層として明記すると良い。
   - (F) write側でfloat32表現可能値へsnapする案は、grid snapとして明示的にrejectしておくこと。
   - 現在のprovisional scope（TJ/Tm拒否）では、Dのmetric式はBと一致する。Dの差分はintent確認とfont/metric bindingにあることを明示すること。
5. pure candidateは「positive horizontal metrics」と書いているが、`plan()`は`a < 0`だけを拒否しており、0 advanceを通す。
   `cell()`もmetric>0を検証していない。zero-offset/単一codepointの検査はevaluatorの`supplied()`にしかなく、`plan()`自体にはない。
6. evaluatorは`verdict='NOT READY'`を固定値で書いている。gate結果から導出する方が証拠性が高い（PR #36の方式）。
7. B1-L-Mはread-only probeであり、境界付近でのPDF roundtripではない（PRもそう明記している）。
   次PRでは、境界付近のfirst→noop roundtripか、新authorityの下での安定性を示すこと。
8. growth/reflowのno-op比較は1回だけで、×3ではない。

#### 次PRとして妥当な最小scope

**layout measurement / verification design**（design/evidenceのみ。layout/paint runtime変更は含めない）。PR提案の項目に同意する:

- provider-independentなcanonical ink/ascent/descent
- exact font size/horizontal scale authority
- current-only font/metric binding
- source/new mixed measurement
- metric programを証明できない場合のrefusal
- TJ/Tm intentのrefusal

これに加えて、以下を扱う:

- N1のstyle ratchetをsemantic gateで閉じること
- N2のTw intent喪失
- N3の`bounds_source`別のB1-L-M範囲
- 両CTMでの境界付近roundtrip

layout runtime implementationへ進むのは、このdesignを独立レビューで通した後に別PRで判断する。paint runtimeはその後である。


## 17. Canonical measurement and current-only verification — 2026-10-04

**Result: NOT READY — DESIGN/EVIDENCE ONLY.** Layout runtime and paint runtime
remain NOT READY. Starting main is `8f9bf6e9e5288b73046ff0c769fa337cde4c537e`,
with PR #37 verified merged. Branch: `codex/canonical-measurement-contract`.
Sections 1–16, their evaluators/summaries and independent reviews are preserved.

### 17.1 Blockers, including two stronger physical counterexamples

| Blocker | Cause, code path and consequence |
|---|---|
| M1 / B1-L-M | `ParagraphShaper.source_ink` uses trace bbox minus origin when a program is unavailable. The §16 fixed-advance AB counterexample is reproduced. This fallback cannot be canonical semantic ink. |
| M2 / **B1-L-V: vertical normalization mismatch** | New glyphs use supplied hhea metrics; retained glyphs use the maximum of trace vertical extents and embedded hhea. Even readable embedded outlines do not remove this provider asymmetry. A synthetic TT font with upem=1000, hhea=600/-200 and width=600 changes ascent/descent from 7.2/2.4 to 9/3 after reopen at 12 pt. Two-line baseline changes from 69.6 to 72, a **2.4 pt no-op displacement**. |
| M3 / **B1-L-S: semantic style reconstruction loop** | `SourceParagraph` derives size=`Tf*M.d`, horizontal scale=`M.a/M.d*Tz/100`, tracking=`Tc*M.a*Tz/100`, rise=`-Ts*M.d`, where M comes through float32 matrix composition. The serializer applies another float32 inverse/basis transformation, then `_bind_paragraph` accepts close reconstructed values. On scaled saves, size monotonically changes even during geometry-stable no-ops. |
| M4 / **B1-L-I: spacing intent witness gap** | The paragraph serializer starts with `0 Tw` and positions individual glyphs. Current active glyph positions do not uniquely identify authored Tw versus a confirmed TJ/Tm edge. `_bind_paragraph` retains Unicode/style bindings but no canonical word-spacing intent field. Physical gap equality cannot re-establish that intent. |

The embedded font is synthetic and generated locally, not a substituted system
font. Its rectangles and hhea deliberately expose the normalization difference.
Both `provided_outline` and `embedded_outline` have available program bounds;
the vertical failure is independent of the Base-14 ink fallback. Under scaled
CTM, first→noop origin displacement is about **1.223999 pt** in this fixture.
The exact font metrics need not agree with MuPDF's normalized trace advance box.
That box is a physical observation, not a second semantic line-height authority.

The actual near-boundary PDF roundtrip also strengthens §16's read-only result.
At source x=100, output target x=20, width **14.399997329711914**, first save
fits AB on one line; reopen→noop places B on a second line, **16 pt** below.
The width is the midpoint of measured first/noop widths in the ordinary 14.4
probe. It is a diagnostic input, not an epsilon correction or a proposed fix.
At 14.4 and ±0.000001 the actual fixture retains allocation while its ink tuple
still changes. Thus a boundary probe must record measurements as well as lines.

### 17.2 Bounds-source authority matrix

| Bounds class | Advance | Ink | Vertical / empty-line metrics | Font identity | Current-only verification |
|---|---|---|---|---|---|
| A: readable embedded outline | Proven code mapping plus explicit `/Widths`/`/W`; must match selected hmtx authority or carry a separately confirmed override | Program outline under one pinned extraction algorithm, then exact logical scale/rise; never trace-relative bbox | Choose one declared font metric policy, provisionally hhea; include ink y extents once. Explicit empty-line recipe from a designated font/style | Program/instance and used glyph semantics; subset name alone insufficient | Check code→CID→GID/Unicode, widths, upem, hhea, outline and descriptor. Current subset SHA need not equal original asset SHA. |
| B: supplied known asset | Shaped/nominal integer metrics from the confirmed instance/features | The same asset outline rule as A | Same vertical rule as A, including empty-line recipe | Asset SHA + collection index/variation instance + shaping policy/version | Reprove current subset's used glyph mappings/metrics/outlines against the current asset; do not trust a sidecar path/name alone. |
| C1: unembedded Base-14 using trace | Position-dependent trace differences | Position-dependent advance-box fallback | Renderer-normalized trace values | Name/resource does not identify an actual renderer program | **Reject as canonical measurement authority.** |
| C2: unembedded Base-14 using AFM | Pinned AFM widths plus exact source spacing | Pinned per-glyph AFM bounds, if present | Explicit pinned AFM/line metric policy; missing fields must refuse | AFM SHA/version/encoding is a metric identity, not proof of the renderer's substituted outline | Canonical arithmetic is possible, but this experiment has no renderer/program binding or accuracy proof for AFM ink. **Not selected.** |
| C3: unembedded Base-14 refused | None | None | None | No proven canonical program | **Selected narrow refusal** until a separately verified AFM/renderer policy exists. Courier remains a negative evidence fixture. |
| D: unreadable/unknown program | Source width alone is insufficient | Unknown | Unknown | Unproven | **FONT_METRIC_AUTHORITY_UNPROVEN**. No promotion of trace data into a logical record. |

The AFM alternative is real: AFM defines character widths and bounding boxes,
while Ascender/Descender and character bounds can be optional. Missing fields
therefore require an explicit policy rather than a trace fallback. See
[Adobe AFM specification §7](https://adobe-type-tools.github.io/font-tech-notes/pdfs/5004.AFM_Spec.pdf).
AFM supplies metric data; accepting it as proof of an unembedded renderer's
particular outlines would be an additional assumption, not established here.

The experiment demonstrates a static TrueType rectangle font. It does not
claim general CFF/cubic extrema, variation instancing, hinting, collection-font
mapping or arbitrary OpenType shaping proof. Those require a pinned canonical
algorithm and expanded witnesses, or explicit refusal. A readable program
avoids M1's trace fallback but does not, by itself, close M2/M3/font binding.

### 17.3 Authority candidates A–F

| Candidate | Decision |
|---|---|
| A: current trace authority | Rejected by L1–L3, M1 and M2. Absolute-position-dependent differences/boxes cannot define semantic metrics. |
| B: nominal/shaped metrics plus spacing | Necessary metric ingredient, insufficient alone. It does not identify spacing intent, guarantee font binding, or select consistent vertical/empty metrics. |
| C: creation-time logical record | Useful only as a bounded **current** metric/style/intent record. Do not freeze trace boxes or stale absolute coordinates; do not retain a revision history. |
| D: common logical measurement representation | Preferred structure. Both source and supplied paths lower to the same tuple/rule before layout. With unresolved positioning refused, its metric arithmetic resembles B; its additional obligations are intent confirmation and font/metric binding. |
| E: exact source operator arithmetic | Preferred source-input layer for D. Decimal Tf/Tm/Tz/Tc/Tw/Ts/TJ and rational CTM arithmetic preserve facts without MuPDF reconstruction. Exact operator facts do not by themselves establish authorial editing semantics. |
| F: writer-side float32 normalization | Rejected. Snapping logical values to a renderer grid changes semantic inputs, is position/renderer dependent, and does not preserve addition/subtraction. `float32(float32(20)+float32(7.2))-float32(20)` differs from `float32(7.2)`. Hidden repeated saves and first-save exceptions remain forbidden. |

The E experiment parses original **literal decimal tokens**, not
`Fraction(str(pypdf.FloatObject))`. It has a restricted ASCII grammar for
q/Q/cm, BT/ET, one font alias, Tf/Tm/Td/Tc/Tw/Tz/Ts and simple Tj/TJ strings.
It rejects malformed adjacency, escapes/nesting, font switching, unknown
operators, rotation/shear, missing widths and nonpositive advances. `/Widths`
and `/W` values enter as separately witnessed rational strings: this is **not**
a general font-dictionary lexical extractor. All fixture widths are the exact
integer 600; arbitrary dictionary extraction remains a verification obligation.

### 17.4 Canonical full measurement tuple and coordinate conventions

Provisional logical input, for each current Unicode glyph interval:

```
semantic_font_identity / instance / shaping policy
logical glyph identity (not transient PDF code or trace GID)
font-unit width, upem, outline bounds, selected ascent/descent
exact page-space font_size, horizontal_scale, rise
confirmed inline tracking, authored word_spacing_intent
confirmed positioning edges and boundary policy
```

Measurement derives a single tuple `(advance, x_offset, y_offset, ink,
ascent, descent, semantic_style)` using rational arithmetic. Provider is
ignored by the model. For positive horizontal layout, `sx=size*scale/upem`,
`sy=size/upem`; font bounds `(a,b,c,d)` become
`(a*sx, -d*sy+rise, c*sx, -b*sy+rise)` relative to the **line baseline origin**.
Ascent/descent start at `max(0,hheaAsc*sy-rise)` /
`max(0,-hheaDesc*sy+rise)`; line measurement also includes the ink y extents.
It never takes a maximum with a freshly observed trace bbox.

The plan records `baseline_origin` separately from painted `origin`, whose y
is baseline plus rise. Ink is already rise-shifted relative to baseline; do not
add rise twice. Tracking and an adjacent-pair edge are suppressed at the final
painted glyph of a line. A confirmed word-space term applies only to authored
ASCII space. Trailing logical spaces remain records but are not painted.

Width is the union of advance extents and ink, including left inset/right
overhang. Next baseline is previous baseline plus the maximum of confirmed
minimum leading and previous descent plus next ascent. Empty lines use an
explicit confirmed rational metric recipe produced from a designated current
font/style, not a nearby trace or an inferred nearest style. Binding that
nonpainted recipe is part of the open font/empty-style contract.

Scope: left only; printable ASCII, one glyph/codepoint, zero shaping offsets,
plus explicit newlines. Input glyph metrics require positive width/upem/size/
horizontal scale; zero or negative resulting advances refuse. Ascent/descent
may individually be zero but must be nonnegative with positive sum. Zero-size,
missing metrics, nonfinite/oversized numbers and invalid ink bounds refuse.
No ink is accepted only for space with `empty_outline_verified`; missing ink
for a visible character refuses. This model is not Unicode rich_layout, a PDF
writer, a schema, a sidecar implementation or a runtime verifier.

### 17.5 One-way semantic style authority

At initial confirmation, exact source operands/CTM or explicit user logical
values define rational page-space size, scale, rise and spacing. Example:
Tf=12, Tz=100, diagonal CTM .83/.91 gives **size=10.92 exactly** and horizontal
scale=.83/.91. Ts=2 gives rise=-1.82. With Tz=80 and Tc=.4, tracking=.2656.
These are not the first float32 observations, which already contain error.

Store the chosen current logical values, font instance/policy and confirmation
meaning. Source operand witnesses are creation facts, not an accumulating
history. On reopen, validate the same record against current physical witnesses;
do not replace its values by inverse calculations from physical matrices.
CTM belongs to the renderer adapter/coordinate proof. Exact logical values
flow into placement/serialization; physical measurements flow only into
verification/accuracy/refusal. A changed CTM/context is a binding change to
validate or refuse, not permission to reinterpret the logical style.

M3 evidence retains the ratchet sequence approximately
10.920000314712524 → 10.919999599456787 → 10.91999888420105 →
10.919998168945312. Equal geometry does not make those semantic values equal.
Physical resource/style ID renames are excluded from semantic comparison;
exact numeric representation normalization (0 versus -0, decimal versus
rational spelling) is permitted, tolerance merging is not.

### 17.6 Spacing intent and positioning edges

Keep authored Tc tracking, authored Tw word spacing, new-text logical tracking,
nominal space width, current physical gap, justification gap, numeric TJ and
manual Tm/Td positioning as distinct facts. Justification is outside left-only
scope. Tw must persist as confirmed logical word-space intent, including its
application to future inserted spaces; it cannot be recovered from gap size.

Exact lowering identifies source edge deltas without assigning editing intent.
For `A B` at Tf12/width600, Tw=1.2, a TJ=-100 before B, and Tm x=35.6 before B
all give the same three exact origins. Their logical intentions differ. This
proves non-uniqueness of the glyph-position projection, **not** equality of the
entire PDFs; residual inactive operators may still differ. They are not an
automatic authority to attach old spacing intent to current active glyphs.

The pure resolved candidate requires explicit confirmation of
`confirmed-adjacent-pair / suppress-at-line-end`. It stores boundaries, rational
horizontal delta and that policy. While adjacent on a line the edge survives
reflow; when split across lines it is suppressed. Deleting either endpoint or
inserting between them removes the edge. Edits before both endpoints remap
both indices. Moving/range-splitting across paragraphs has no admitted policy:
refuse until a confirmed rule exists. Vertical edge deltas, unconfirmed intent
or nonadjacent endpoints refuse **SOURCE_POSITIONING_INTENT_UNRESOLVED** (or
an explicit invalid-edge error). No TJ/Tm authorial meaning is inferred merely
because a source operator or an equal gap is observed.

A current record would be O(current glyphs + styles/fonts + live edges), with
no stale absolute positions or history array. That logical record prevents
intent loss in the pure model. Proving its meaning belongs to the current PDF
still needs the witness contract below; the model's success is not that proof.

### 17.7 Current-only font and semantic verification contract

A future verifier must consume only the current PDF, current logical record,
current supplied assets and explicit confirmations. It must either establish
all required correspondences or refuse, without changing the canonical inputs:

1. Bind current paragraph/code ranges and Unicode intervals bijectively,
   including repeated glyphs, omitted whitespace and empty-style witnesses.
2. Resolve font resources/descriptor, Encoding/CMap/ToUnicode and code→CID→GID
   from current objects. A subsetless font name is insufficient.
3. Identify the current asset/instance by SHA, collection index, variations,
   upem and pinned shaping features/version. Distinguish embedded program hash
   from full asset hash: valid subsets legitimately have different bytes.
4. Compare used outlines, hmtx, selected vertical metrics, PDF widths and
   descriptor meaning. Reject incompatible widths rather than silently choosing
   between PDF `/W` and font hmtx. Compare decomposed outlines under one pinned
   representation; arbitrary glyph numbering is not a semantic identity.
5. Verify exact logical style and spacing/edge intent through an explicit
   current semantic witness/confirmation contract. Expected serialized placement
   and ≤0.002 pt accuracy are necessary but cannot alone prove intent identity.
6. Verify expected line allocation/baseline/origin/advance relationships and
   the current rendering context. Mismatch, missing asset/metric program,
   unsupported shaping or ambiguous association must refuse before publication.

Read-only evidence compares current synthetic PDF code/CID/GID, ToUnicode,
widths, hmtx, upem, hhea, descriptor metrics and decomposed used outlines against
the current known asset at first and noop1. Those samples match even though
subset-program SHA differs from asset SHA. This is **partial witness evidence**,
not a general binding verifier. The separate symbolic `verification_candidate`
rejects each of 11 changed/missing witness fields and name-only input; it does
not authenticate where those input fields came from.

**Open contract:** full association/shaping and nonpainted/empty-style witnesses
are not closed by the sampled glyphs. More importantly, the exact current
semantic witness carrying Tw/edge intent has not been selected. Merely adding
an unchecked sidecar field fails the user's requirement. Next design must
choose and test either a lossless current PDF semantic witness bound to the
record, or a separately justified explicit-confirmation authority plus current
binding. Do not claim that geometry comparison distinguishes Tw from TJ/Tm.
These are design obligations, not a claim that runtime verifier implementation
must be included in this PR.

### 17.8 Evidence matrix, gates and accuracy

| Evidence | Coverage / result |
|---|---|
| Actual lifecycle | Courier + supplied CJK and embedded + supplied synthetic TT, each identity/scaled. First, change, growth, reflow each followed by noop1/2/3: **64 saves, 60 fresh re-edits**. |
| Bounds | Trace fallback, provided outline, embedded outline; read-only unknown unembedded program refuses canonical authority; AFM is compared but not adopted. |
| Style | Combined Tc=.4/Tw=1.2/Tz=80/Ts=2, both CTMs, first→noop×3: **8 saves**. Separate identity Tw first→noop adds **2 saves**. Scaled size/scale ratchet and identity Tw loss remain visible. |
| Boundary | Below/exact/above nominal 14.4 (scaled 11.952), both CTMs, plus observed noise-band midpoint: **14 saves**. All seven roundtrips saved; noise-band allocation changes. |
| E operator candidate | Tj, Tc, Tw, Tz, Ts, numeric TJ, manual Tm under both CTMs; exact token arithmetic and explicit unresolved edge semantics. |
| Full tuple candidate | First/change/growth/reflow × both CTMs × 3 fresh processes = **24 deterministic replays**. Same input produces exact style/tuple/lines/baselines/origins/advances. Boundary, empty-line and rise/spacing probes also recorded. This is not a current-PDF candidate roundtrip. |
| Edge candidate | Confirmed TJ/Tm pair, insertion/deletion, line-end suppression and Tw distinguished from equal positioned origins; unresolved intent refused. |
| Verification model | Current used-glyph samples plus 11 independent symbolic witness mutations, missing fields/name-only rejection. Full binding remains unproven. |

Total **88 actual saves**. Maximum planned-to-saved MuPDF origin discrepancy is
`0.0000152587890625` pt; all 88 pass ≤0.002 pt. However **no-op preservation
accuracy fails** for the new vertical/boundary counterexamples (2.4/16 pt).
These gates answer different questions: the writer accurately saves a plan
that changed incorrectly on a no-op. Do not describe these cases as visually
stable or use plan-to-saved accuracy to conceal their canonicality failure.
No new raster equality, Poppler or external-original validation is claimed.

Final verdict is derived from explicit gates, not a fixed label:

| Gate | Status / scope |
|---|---|
| exact style authority | PASS for preconfirmed candidate inputs only |
| ink/vertical authority | PASS for the known synthetic metric program candidate; Base-14/unknown not admitted |
| font binding | OPEN: partial current glyph samples are not complete association/empty-style proof |
| spacing intent | OPEN: current persisted semantic witness contract absent |
| candidate current-only lifecycle canonicality | OPEN: pure replay is not authenticated PDF→record rebind |

`verdict(gates)` returns DESIGN READY only if all five required design gates
are true. Tests prove that an accuracy PASS cannot override any failed gate.
Unchanged runtime failures are diagnostic evidence, not by themselves a rule
that would prevent a design from ever being READY before implementation.

Future acceptance requires exact semantic style, **full** tuple, allocation,
baselines, planned origins and advances on every same-state lifecycle edge.
Semantic edits/width changes may change layout; their following no-ops may not.
Reject unsupported/currently unproven inputs, and separately require placement
accuracy ≤0.002 pt and no-op preservation. Before claiming READY, demonstrate
current-only font/intent/empty-style correspondence, including tampering or
resealing attempts, under the narrowed admitted scope.

### 17.9 Validation and minimum next scope

**27 new design/evidence tests + 94 focused regressions = 121 passed.**
After the coordinate/grammar refinements, the 22 affected pure/probe tests also
passed again. The final focused rerun passed all 121 tests; the two strengthened
style-ratchet tests then passed separately. Full suite: not run. External Windows/LibreOffice original
validation: not run. Local Windows, Python 3.12.14, PyMuPDF 1.27.2.3.
Physical saves from `runs/measurement-02` were retained while pure candidate,
witness and gate summaries were reassessed; no hidden extra save or old report
was supplied as an input to runtime re-edits. The single clean reproduction
command runs both layers from scratch.

Runtime digest unchanged:
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.
No pdfeditor/, schema, public API, serializer, paint writer or marker parser
changes. Raw PDFs/fonts/full reports stay ignored; only the separate compact
[measurement summary](../evaluations/anchors/measurement-authority-summary.json)
is tracked. [Reproduction](../evaluations/anchors/README.md#canonical-measurement--2026-10-04).

**NOT READY.** Preferred direction is D+E plus a common canonical ink/vertical
metric rule, with Base-14/unknown refused and F rejected. The newly isolated
vertical normalization failure and actual boundary roundtrip establish stronger
consequences than sub-micro-point drift. The next minimal task is **current-only
semantic witness / font-and-empty-style association design** for the supported
static-TT subset, with Tw-versus-edge ambiguity and positive/negative binding
fixtures. It must show that the canonical record survives verified rebind,
not merely that a JSON plan is deterministic. Layout runtime implementation
requires that design gate; paint runtime follows separately and remains blocked.

The five paint obligations remain tracked in §15.11/§16.6: ordinary/legacy
mutation overlap refusal; source-decimal path + exact S creation recipe;
termination lexical gate; cross-page fixture; unified clip/paint CTM authority.
This PR does not implement them or create duplicate TODOs.

### 17.10 Independent review — Claude Opus 5.5

- Reviewed HEAD: `9c68d31c4bcbaad5fb4fc205b24a8d1360441a19`; base `8f9bf6e9e5288b73046ff0c769fa337cde4c537e`
  (PR #37 merge commit, verified merged). This record is a separate commit on top of the reviewed HEAD.
- **Verdict: PASS WITH NON-BLOCKING NOTES — DESIGN/EVIDENCE ONLY; NOT READY.**
  Layout runtime **NOT READY**. Paint runtime **NOT READY**. No blocking findings.
- Focused tests (README command): **121 passed** (184.34 s). `measurement_observation` rerun into a scratch
  directory reproduced a summary **identical** to the tracked `measurement-authority-summary.json`
  (0 differences, same local Windows platform metadata); the tracked file was not overwritten.
  Full suite: not run. External Windows/LibreOffice original validation: not run.
  No `pdfeditor/`, serializer, schema, API, verifier or paint writer change.

**Counterexamples.** B1-L-M is reproduced (`source_ink` trace bbox fallback). B1-L-V follows directly from
`ParagraphShaper.shape`: new glyphs use `ShapedFont.ascender/descender` (hhea/upem) × size, retained glyphs take
`max(trace bbox extent, hhea × size/upem)`. The `max` runs even when `embedded_outline` bounds are readable, so the
asymmetry is a rule selected by provider, not only the Base-14 fallback; MuPDF's em-normalized trace box
(600/−200 → 9/3 at 12 pt) explains 7.2/2.4 → 9/3 and the 2.4 pt baseline move. The near-boundary actual roundtrip
(first 1 line → noop 2 lines, B moves 16 pt) is a valid diagnostic: every plan is saved within 0.0000152587890625 pt,
so writer accuracy passes while the plan itself changes on reopen. B1-L-S: `SourceParagraph` derives size/scale/
tracking/rise from a pymupdf (float32) `multiply(text_matrix, ctm)`; the serializer writes a float32 inverse basis and
`_bind_paragraph` accepts close values, giving the recorded monotonic size ratchet. B1-L-I: the serializer emits
`0 Tw` and per-glyph `Tm`, so active positions do not determine Tw versus TJ/Tm intent.

**Candidates.** D (common logical measurement) as the structure and E (exact source decimal tokens → `Fraction`,
not `Fraction(str(float))`) as its source-input layer are sound. E's restricted ASCII grammar refuses font switches,
rotation/shear, unknown operators, missing/nonpositive widths, escapes and nesting, and is not claimed as a general
parser; `/Widths` remain an external witness. F (float32 snapping) is correctly rejected: it rewrites semantic inputs,
stays position/renderer dependent and is not closed under addition. CTM is kept as renderer-adapter context, separate
from semantic style; resource/ID renames are separated from numeric drift.

**Bounds matrix / tuple.** The A/B/C1–C3/D classification is appropriate: readability alone is not admission, subset
names are not identity, subset bytes legitimately differ from the asset SHA, and glyph correspondence plus
hmtx/hhea/outline/Unicode checks are required. Refusing Base-14 in this narrow scope is acceptable. The pure tuple is
internally consistent: one rule for all providers; ink is rise-shifted once relative to `baseline_origin`;
ascent/descent take hhea and ink y extents without trace input; tracking and edges are suppressed at the trimmed line
end; trailing spaces stay records but are not painted; empty lines use an explicit recipe. The edge policy (adjacent
only, removed by endpoint delete or insertion between, preserved while adjacent, suppressed at line end, cross-
paragraph/vertical/nonadjacent refused) does not over-infer authorial intent.

**Binding, witness, gates.** `verification_candidate()` is correctly described as comparison of supplied witness data,
not extraction/authentication; current-subset samples are partial evidence. Font binding, spacing intent and
current-only lifecycle canonicality are correctly OPEN, and `verdict()` cannot be overridden by accuracy. The five
paint obligations from §15.11/§16.6 remain tracked.

**Non-blocking notes.**

1. Gates mix determinism and admissibility. Record three layers separately: model determinism (closed for the pure
   candidate), input admissibility (style/ink-vertical PASS only for preconfirmed or synthetic inputs), and
   authenticated current binding (OPEN).
2. Semantic witness: because the current serializer gives identical bytes for Tw intent and a confirmed edge, a record
   that swaps those intents cannot be caught by physical comparison. The next design must choose between
   (a) intent-encoding canonical serialization in an owned region, verified by exact re-serialization from the current
   record (a lossless current PDF witness), and (b) explicit confirmation held in the caller-trusted record and bound
   to current physical positions, stated as *not* physically witnessed. A free-form PDF-internal marker alone adds no
   authority.
3. Empty lines and trimmed trailing spaces have no painted witness. Either give them a nonpainting physical witness
   (as source-output empty/style `[] TJ` witnesses do) or treat them under explicit-confirmation authority; this belongs
   to the next scope together with default/paragraph style association.
4. F's rejection does not exclude a renderer-independent fixed *output* decimal rule applied only at serialization and
   never fed back into logical inputs; that is compatible with D and differs from snapping semantic values.
5. An AFM contract for Base-14 may later be admissible for advances and line metrics; ink accuracy against the
   renderer's substituted outline would still need separate evidence. Refusal now is fine.

**Next minimal scope.** Current-only semantic witness / font-and-empty-style association design for the static-TT
subset: choose the intent witness (note 2); font association via code→CID→GID→Unicode and asset glyph equivalence;
repeated glyphs, omitted whitespace and empty/nonpainted style association; Tw-versus-edge ambiguity; stale, tampered
and resealed record refusal; positive and negative binding fixtures; and proof that the canonical record survives a
verified PDF→record rebind. Layout runtime implementation should not start before that gate closes; paint runtime
remains blocked behind layout.


## 18. Current semantic witness binding — 2026-10-04

**Result: NOT READY — DESIGN/EVIDENCE ONLY.** Layout runtime and paint runtime
remain NOT READY. Starting main `f749bb893ac883e235ce73d5ea1aa2a5bdc1fddd`
includes merged PR #38 and its §17.10 independent review. Branch:
`codex/current-semantic-witness-binding`. Sections 1–17, their evidence and
reviews remain byte-for-byte preserved.

### 18.1 Three different claims and the trust boundary

| Layer | What it establishes | What it does not establish |
|---|---|---|
| A: model determinism | Equal exact canonical input gives equal full measurement and layout | That the input is admissible or belongs to this PDF |
| B: input admissibility | Metrics come from a supported confirmed asset, style/intent are explicitly confirmed, scope/policies validate | That an untrusted record is an authentic confirmation |
| C: authenticated current binding | Independently trusted semantic confirmation plus freshly extracted current program/font/interval evidence agree | That an arbitrary signing helper constitutes a production user-confirmation or publication policy |

This experiment advances C with actual PDF extraction, not just dict comparison.
It also exposes **B1-L-C: confirmation-to-publication authority gap**. A test
signer can authenticate any input handed to it, including an intent swap that
leaves PDF bytes unchanged. Authentication of an issuer's message is not proof
that the issuer was authorized to confirm that meaning. A production issuer
must distinguish a user's new semantic decision from merely recalculating a
hash. That issuance/transition contract is still open; adding a secret to an
unchecked sidecar is not the resolution.

The selected experiment's trust root is an **independent caller's explicit
confirmation**, represented by a test HMAC authority. Its key and target
(document handle, paragraph, page, audience) enter through a trusted test-runner
channel, outside the PDF/record/receipt bundle. The random key is never published
in the summary. This is a controlled fixture trust assumption, not a production
key store, signature format, authentication service or evidence of a real user
confirmation. A caller that loads the key/target from the same untrusted bundle
would violate the premise and must not claim authenticated binding.

The receipt binds a domain, independently selected current document/paragraph,
exact semantic-record digest, exact current PDF digest and supplied asset digest.
The record includes current interval/disposition/font/style/edge policy and
current PDF SHA. Its optional public self-hash is diagnostic and is deliberately
excluded from authentication; recomputing it grants nothing. Hashes bind bytes
*after* trust is established, and are not trust roots. Subsetless font names,
marker names, a PDF hash or an application-generated JSON file likewise do not
establish authority. Source decimal operators are facts about placement/state;
assigning future-edit Tw/edge meaning still requires confirmation.

### 18.2 Witness options and selected direction

| Option | Benefit | Independent authority and limitations | Decision |
|---|---|---|---|
| A: canonical intent-encoding PDF region | Re-serialization could distinguish Tw/edge/empty-style meanings from current PDF bytes | Requires externally authorized ownership, exact semantic grammar, current byte/context binding and atomic updates. A forged canonical region can be internally consistent; its marker cannot authorize itself. | Viable future alternative, not selected for this experiment |
| B: explicit confirmation + physical binding | Keeps exact meaning outside lossy physical geometry; supports nonpainted intervals | Intent is **confirmed, not physically witnessed**. Needs an independent trusted confirmation channel; absence or loss of that channel refuses. | Selected for scoped read-only positive evidence |
| C: hybrid confirmation + partial owned witness | Can put interval/style anchors in PDF while external confirmation authenticates their intent | Still needs A's ownership and B's issuance rules; partially witnessed fields must be labeled individually | Deferred; no reason yet to add a second ownership domain |
| D: current record + exact output expectation alone | Detects foreign edits to the expected program/font/context | Tw and edge records can generate identical bytes. It does not authenticate intent or nonpainted logical state. | Necessary physical check, rejected as a standalone trust root |

Selection B is a conditional direction, not a claim that every caller-trusted
record is trustworthy by declaration. Without a valid independently issued
current confirmation, there is no fallback to marker adoption, name matching,
nearest-glyph matching, geometric inference or public resealing.

### 18.3 Static-TT current font association

The admitted experiment is deliberately narrow: one complete synthetic page,
one direct content stream, one `/F` Type0 / Identity-H / CIDFontType2 resource,
static simple unhinted TrueType, one body style, left layout and A/B/space/newline.
Maximum 256 current characters. No Forms, page rotation, page boxes other than
the fixed MediaBox, marked content, inherited style ambiguity, optional content,
annotations, blend state, arbitrary clipping, variable/collection fonts, composites,
CFF, unsupported shaping or mixed styles are admitted. This is a strict fixture
language, not a general PDF verifier. Whole-page equality grants no mutation right.

| Association | Current extraction / exact obligation |
|---|---|
| Asset and instance | Independently confirmed asset SHA; collection index 0, no variations; nominal one-codepoint shaping, kern=false, HarfBuzz version, outline algorithm and fontTools version pinned in policy |
| Resource / descriptor | Resolve current `/F`, descendant and FontDescriptor. Check exact permitted dictionaries/types, descriptor names internally consistent, flags, hhea-derived ascent/descent, explicit bbox/CapHeight/StemV policy |
| Encoding / code / CID | Identity-H only; two-byte codes; signed codebook must be a bijection and cover emitted and default/nonpainted font characters |
| CID / GID / Unicode | Read current CIDToGIDMap; nonzero in-range GID; exact ToUnicode grammar and mapping; current embedded cmap must map that Unicode to that current glyph |
| Metrics | Equal upem, hhea and hmtx (advance and bearing) against confirmed asset glyph; PDF `/W` equals exact 1000/upem-scaled hmtx. Fixed `/DW` is checked, not used to invent missing widths |
| Outline | Decomposed recording-pen sequence of each used/default glyph equals the asset glyph's sequence; no hint programs or composites in scope. Empty space is explicitly proven empty |
| Identity across subset | Compare mapped semantics, **not numerical GID equality or font-file SHA equality**. Current subset bytes may differ from full asset bytes. Descriptor bbox follows the confirmed full asset; a subset may have a smaller global bbox |

Actual positive evidence renumbers A from asset GID 1 to current GID 3 and space
from 3 to 1, and still passes. Development negatives also showed why outline
and width equality alone are inadequate: the synthetic `.notdef` and A have the
same outline/metrics. A CID→GID 0 substitution initially escaped those checks;
nonzero GID plus current cmap/Unicode correspondence now rejects it. This is
recorded as a strengthened association condition, not hidden as a passing test.
An embedded program lacking the required cmap is outside this narrow proof.

The current extractor reads the actual saved PDF using pypdf/fontTools and checks
fresh MuPDF origins separately. A full-file hash alone is never its font proof.
Negative fixtures deliberately reissue *test* receipts over invalid PDFs to show
that even an authentic test message cannot bypass font/program validation.

### 18.4 Repeated occurrences and bounded logical intervals

Each current character has exactly one interval `[i,i+1)` and current ordinal
identity `current:i`. Emitted glyphs bind in verified canonical program order to
exact `<code> Tj` byte spans, not to nearest positions or matching Unicode/GID.
The complete expected program, including each Tm and control state, must match
before those spans can be used. Repeated `AAAA` therefore has four different
intervals/spans even though all four codes and glyph identities are equal.
Swapping two equal-glyph drawing commands with their different positions is
rejected even when the visible result is unchanged. Swapping interval records
also fails the bijection or independent confirmation.

Ordinal identities are **current-state identities, not permanent historical IDs**.
Insert/delete/reflow creates a newly confirmed current mapping. A deleted and
reinserted A must never inherit a stale occurrence binding; it gets the current
interval and a newly validated program span. An exactly identical net current
state needs no deletion history. Physical trace IDs and PDF object numbers are
not semantic identities; GID renumbering and current byte spans are reproven.
The positive reflow fixture demonstrates a fresh current mapping, not an
implemented authenticated edit transition from the earlier fixture.

The record holds only current glyphs, styles/fonts, codebook, intervals, omitted
interval dispositions and live edges: O(current text + styles + fonts + edges).
No old geometries, trace snapshots, revision arrays or migration chain. Unknown
record/model fields, including added history, refuse. Receipts are one current
statement, not a signature history. The evaluator's evidence history is outside
all candidate runtime inputs.

### 18.5 Whitespace and empty-style association

| Logical case | Authority and physical association |
|---|---|
| Interior empty-outline space | Current font cmap/code/width and verified empty outline; still an emitted Tj interval |
| Authored trailing / line-end trimmed space | Signed current interval and explicit `trimmed-space` disposition computed from the exact plan; no invented painted witness |
| Tw-target space | Confirmed exact word-spacing intent plus its current space interval; not inferred from physical distance |
| Newline | Signed current newline interval; explicit structural omission, not a fabricated glyph |
| Empty line | Designated body style and its asset-derived exact ascent/descent, minimum leading and canonical paragraph plan |
| Fully empty fragment / all-space fragment | Explicit body default/typing style, current `/F` resource and nonpainting style anchor; no nearest glyph required |

All current offsets must partition into emitted intervals and explicit nonpainted
intervals with no duplicates or visible omissions. The receipt authenticates
nonpainted meaning; the PDF does not contain trailing-space/newline intent in
this option. A change only to those record fields is caught by the independent
receipt, including after public self-hash recomputation.

The fixture emits one exact default-state `[] TJ` anchor under q/BT and a full
style/matrix declaration. It physically witnesses the current render-adapter
font/size/scale/context, **not** the number of omitted spaces, Tw intent, empty
line count, logical style name or authorial meaning. All lines use the explicitly
confirmed `body` style; preceding/following glyph style is irrelevant. Authored
and inserted empty lines use body. Mixed-style boundaries and per-line authored
empty-style overrides refuse until a range/style association contract exists.

### 18.6 Nonpainting witnesses, ownership and domain conflicts

| Carrier | What it can witness | Why it cannot authorize itself |
|---|---|---|
| Nonpainting `[] TJ` | Current text state and a lexical program location | Empty glyph output does not identify a semantic interval/style unless independently bound |
| Marked content | Explicit structural grouping / named properties | Tag or MCID presence does not prove ownership/authorship |
| Owned metadata/object | Canonical exact semantic payload could encode intent | Must independently bind object reference, bytes, owner and current render region |
| Canonical comment/marker | A locator within an audited grammar | Foreign content can copy the same string; never promote by marker match |
| Explicit record-only meaning | Full exact logical intent/nonpainted state under Option B | Valid only with independent confirmation plus current physical proof; self-hash is insufficient |

PDF's text-showing and marked-content syntax supplies carriers, not this
application's authority model. See [ISO 32000-1 §§9.4.3 and 14.6](https://developer.adobe.com/document-services/docs/assets/35e4369068f86065372c18787171a17e/PDF_ISO_32000-1.pdf).
The existing `paragraph._style_witness` / `logical_element.slot_binding` paths
already use empty TJ slots, while `source_ownership` checks their containment.
This experiment does not extend, adopt or replace those runtime contracts.

**Selected B adds no PDF semantic-ownership domain.** The fixture's whole-page
program is read-only evidence. A future runtime edit must separately obtain
source-output ownership; verification is not write authorization. If A/hybrid
is later selected, its minimum contract is: externally authorized stable owner
identity; current decoded program/object and exact lexical begin/end spans;
closed canonical grammar and balanced termination; external current revision
binding; no foreign/legacy promotion; stale/duplicate/spoof marker refusal;
exclusive mutation spans; and PDF plus record/receipt publication in the same
Transaction. Comments or objects discovered in an arbitrary PDF cannot seed
that owner by themselves.

Do not create a nested independently mutable semantic block inside a paint or
source-output block. Prefer a single source-output owner for any future empty
style anchor, with the semantic record referring to its proven current span.
Ordinary/legacy edits overlapping an owned source/paint span must refuse unless
the owning transaction explicitly coordinates the replacement. Combining these
domains is a future contract task, not proven by the isolated whole-page fixture.

### 18.7 Edit semantics and publication boundary

The following are proposed semantic transitions, not runtime capabilities:

| Operation | Current semantic update / admissibility |
|---|---|
| Insert, delete, replace, range growth | Rebuild current intervals/dispositions and exact plan; remove deleted meaning and obsolete edges; no stale span reuse |
| Reflow | Keep confirmed logical style/intent; regenerate current allocation and byte-span binding; edge suppressed when endpoints split across lines |
| Newline insert/delete | Body style applies explicitly; regenerate empty-line metrics and omissions; no nearest-style inference |
| Entire body style change | Requires explicit new semantic confirmation, including font/size/rise/tracking/Tw/default metrics |
| Edge endpoint delete / insertion between endpoints | Drop the edge, as §17's confirmed adjacent-pair policy specifies; never silently transfer it |
| Paragraph split/join, mixed styles, cross-paragraph/vertical edge | Refuse; no implied authority transfer or style inheritance |
| Unsupported/foreign PDF mutation | Refuse current receipt; do not refresh its PDF hash and call that a confirmation |

Future publication must validate the old current bundle under its existing
trusted authority; apply an authorized semantic edit; derive exact new layout
and candidate bytes; independently validate the new current physical relation;
then issue the new confirmation under an explicit permitted transition and
publish PDF/record/receipt atomically. Failure must expose neither half a bundle
nor a receipt for uncommitted bytes. Unchanged read-only reopen reuses the same
receipt and freshly verifies the same current PDF. No history is needed for
that check. Proposed edit transition approval is distinct from self-resealing.

**Still OPEN:** how a production caller proves a first confirmation and delegates
specified future edits to the issuer, and how that issuer participates in the
existing Transaction/source-output publication boundary. `issue_test_confirmation`
intentionally has no user authorization state machine; it is an oracle for known
fixtures. Calling it on arbitrary current bytes cannot satisfy this obligation.
B1-L-C's safety consequence is that an unrestricted issuer can endorse Tw→edge
without a user decision even though all physical checks pass. Its canonicality
consequence is a different future-space/edit rule with unchanged current geometry.
Minimum next evidence: a separate pure authorization/publication state machine
that accepts explicit confirmation/allowed edits, refuses bare hash refresh and
foreign domain reuse, and injects failures before/after each atomic-publication
step. This is a **design/evidence** task; implementing a runtime key store or
verifier is not necessary to close the design gate.

### 18.8 Tamper/reseal and current-only lifecycle evidence

Actual synthetic current PDFs cover repeated glyphs, interior/trailing space,
blank lines, fully empty and all-space fragments, Tw, a confirmed adjacent edge,
reflow, valid GID renumbering and fractional/nonterminating logical size. Nine
saved fixtures each bind and then reopen in three fresh processes: **27 exact
fresh-process authenticated rebinds**. Workers receive only the current PDF,
record/receipt, current asset and separate test-authority input/target; no prior
PDF, prior plan or hidden save. Extraction→admissibility→canonical layout→exact
program expectation→physical accuracy is repeated. Same semantic/measurement/
plan/witness results hold. This is read-only current-revision evidence, not a
runtime save/noop/edit implementation.

**30 negative cases refuse**, including record font/Tw/edge/empty-style/interval/
omission edits, each publicly resealed; wrong asset/key, copied document/paragraph,
stale PDF, PDF-hash-only refresh, forged rehashed receipt, changed Unicode,
CID/GID, widths, outline, marker, physical style/empty anchor/position and repeated
command order, plus the Tw→edge swap. Font/program negatives carry freshly issued
*test* receipts to isolate physical checks from receipt rejection. Those test
issuances are deliberately not production authorization evidence.

For `A B`, confirmed Tw=1.2 and a confirmed adjacent edge of 1.2 create **identical
fixture PDF bytes**, not merely equal glyph positions. Physical-only extraction
accepts the swapped record. The original independent receipt rejects it even
after self-reseal. A newly authorized explicit confirmation could intentionally
change that intent; a hash refresh must not be able to authorize it. Option B
therefore authenticates confirmed intent but **does not physically witness it**.

All nine plan→saved origin checks pass ≤0.002 pt; maximum
`0.000006103515630684342` pt. Accuracy cannot override any failed binding gate.
No raster/Poppler equality or external-original validation is claimed.

### 18.9 Serialization and final gates

Logical values remain exact rational strings. A fixed renderer-independent
output rule emits at most six decimal places, nearest/ties-to-even, no exponent
and no negative zero. For example logical `37/3` produces output `12.333333` but
remains `37/3` on all rebinds; `1/3` outputs `0.333333`. Output text is only a
physical expectation checked against the logical authority. Neither decimal
spelling nor MuPDF float32 output is fed back into semantic values. A placement
accuracy failure must refuse or require a different explicitly designed output
policy, never snap the logical input. This differs from §17's rejected F.

| Gate | Result |
|---|---|
| A model determinism | PASS for exact fresh-process current fixture replays |
| B input admissibility | PASS within the explicit static-TT/body-style/confirmed-intent fixture scope |
| C current font / interval / nonpainted-style / intent receipt / revision / read-only rebind | PASS conditional on independently trusted test confirmations |
| C production confirmation issuance | OPEN: test signing is not authorized semantic transition proof |
| C atomic edit publication contract | OPEN: source-output/Transaction/receipt boundary not yet established |

`three_layer_verdict` requires every required subgate in all three nonempty layers
to be true. Measured fixture gates derive from evidence; the two open design
obligations are explicitly false. Tests exercise hypothetical fully closed gates
and each layer's failure: the verdict is not a fixed NOT READY string and an
extra accuracy PASS cannot override it. **Final: NOT READY.**

Validation: **29 new design tests + 142 focused regressions = 171 passed**.
The 29 new tests were rerun after final scope checks.
Validation and reproduction are recorded in the [evaluation README](../evaluations/anchors/README.md#current-semantic-binding--2026-10-04)
and [separate summary](../evaluations/anchors/semantic-binding-summary.json).
Runtime digest remains
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.
No pdfeditor/, serializer, schema, public API, runtime verifier or paint changes.
Full suite and external original validation are not run. Existing paint obligations
remain in §15.11/§16.6: overlap refusal, source-decimal path + exact S recipe,
termination lexical gate, cross-page fixture and unified clip/paint CTM authority.
No duplicate TODOs or implementation changes were introduced.

### 18.10 Independent review — Claude Opus 5.5

- Reviewed HEAD: `218698957c61f3cb36aadca65971d31882959a60`; base `f749bb893ac883e235ce73d5ea1aa2a5bdc1fddd`
  (PR #38 merge commit, verified merged). This record is a separate commit on top of the reviewed HEAD.
- **Verdict: PASS WITH NON-BLOCKING NOTES — DESIGN/EVIDENCE ONLY; NOT READY.** No blocking findings.
  Layout runtime **NOT READY**; paint runtime **NOT READY**.
- Focused tests (README command): **170 passed, 1 skipped** (482.53 s; the skip is `test_source_ownership.py::test_poppler_noop_pixels`, Poppler is not installed in the review environment; the PR records 171 passed with Poppler). `semantic_binding_observation` rerun into a scratch
  directory: summary **identical** to the tracked `semantic-binding-summary.json` (0 differences; the random test key
  and receipt tags are not in the summary). 9 positive fixtures, 27 fresh-process rebinds all `reopen_exact`; 30/30
  negatives refused; maximum plan→saved origin error 6.10e-6 pt. Full suite and external Windows/LibreOffice
  validation: not run. No `pdfeditor/`, serializer, schema, API, Transaction, verifier or paint change.

**Three layers.** A (determinism), B (admissibility) and C (authenticated current binding) are correctly separated;
`three_layer_verdict` needs every subgate in all three layers, and accuracy is not a gate input, so neither A, B nor a
placement PASS can override a C failure.

**Option B and physical binding.** The key/target enter `bind()` only as caller arguments; the self-hash is excluded
from the message; PDF hash, marker or font names never grant authority. Physical validation is independent of the
receipt: the nine physical negatives carry freshly issued test receipts and still refuse in `extract_current`.
Binding is current-only: exact expected program equality, Type0/Identity-H, exact ToUnicode, CIDToGIDMap with nonzero
GID, embedded cmap→same glyph, upem/hhea/hmtx, decomposed outline and integer `/W` against the asset; no previous PDF,
plan or trace is read. GID renumbering (A 1→3, space 3→1) passes, as it should. The `.notdef` strengthening (nonzero
GID + current cmap + ToUnicode) is sufficient within this symbolic-flag, unhinted, simple-glyph scope; I found no
remaining outline+width+cmap misbinding there. Repeated `AAAA` is bound by full-program equality followed by ordered
show spans; order swaps refuse, and nothing falls back to nearest matching. The regex span scan is acceptable only
because it runs on already-verified canonical bytes. `current:i` is a structural current-state identity: stale IDs
cannot survive insert/delete, and delete→reinsert cannot revive one.

**Whitespace, empty style, `[] TJ`.** Nonpainted intervals (trimmed space, newline, empty line, empty and all-space
fragments) carry meaning only in the confirmed record; empty metrics come from the designated body font × exact size,
independent of neighbours; mixed styles refuse. `[] TJ` is correctly limited to render-adapter state, not counts,
Tw or authorial intent.

**Tw/edge ambiguity.** Reproduced: the two fixtures produce byte-identical PDFs, physical-only matching accepts the
swapped record, and the original confirmation refuses it after reseal. This proves intent is not physically
recoverable from these bytes. Tamper/reseal, copied/foreign target, wrong asset/key, stale PDF, hash-only refresh and
rehashed receipt all refuse; public resealing creates no authority.

**B1-L-C and the trust model (main judgement).** B1-L-C is real, but it is an *authorization invariant*, not an
authentication-infrastructure problem: semantic fields must change only through enumerated transitions requested by
the current caller. pdfengine does not need user accounts, PKI, a persistent key store or a network service.
Recommended model, P4 (hybrid of P1/P2):

- Trust boundary = the pdfengine API caller (P1), as in the source-output and paint designs.
- Initial confirmation = an explicit caller statement of the semantic state.
- Each mutation call is itself the capability for its transition (P2): `old verified bundle + explicit edit request →
  new state`. No persistent secret is needed.
- Persistent semantic record = caller-trusted sidecar bound to the current PDF SHA, document/paragraph target and asset,
  with an integrity checksum for accidental damage, and physically re-verified on every reopen. This is required to keep
  meaning across save/reopen. It is not caller authorization.
- Persistent authorization receipts and cryptographic signatures are **not required**. P3 is an optional deployment
  extension when the sidecar store is writable by untrusted parties; it lives outside the engine. Because a receipt binds
  the PDF SHA, every publication would need re-issuance; an engine-held key would only be a self-seal.

Transition classes for the next design. *No-op reopen*: no new authority, physical re-verification only.
*Derived safe, automatic inside an explicit edit call*: text insert/delete/replace in body style; recompute
intervals, omissions and empty metrics; reflow/width change; drop an edge on endpoint delete or insertion between;
suppress it at line end. *Semantic reinterpretation, needs an explicit field in the request*: Tw↔edge, adding edges,
style/size/rise/tracking/Tw change, font/asset change, empty-style change. *Refuse*: split/join, mixed styles,
cross-paragraph or vertical edges.

Threat model: in scope are accidental stale records, copied sidecars, foreign PDFs, manual/accidental modification and
internal record corruption (PDF SHA + target + checksum + physical verification + closed transitions). Out of scope:
a malicious privileged caller, a compromised machine, stolen keys.

**Atomic publication.** The proposed order (verify old bundle → authorized transition → canonical layout → candidate
PDF → new physical verification by full reopen/extraction → new record bound to the candidate SHA → pair publish) is
right. Use the existing `Transaction` and `_publish`, and keep source-output ownership as the only write authority;
semantic verification grants no mutation right. Inject failures after layout, after candidate save, at physical
verification, at record sealing and at second-link publication; each must leave the old pair intact and publish
neither or both. That is exception atomicity, not crash atomicity.

**Non-blocking notes.**

1. Replace persistent HMAC receipts by P4 in the next design. Rename the gate `production_confirmation_issuance` to
   "semantic transition authority" so it does not imply identity infrastructure.
2. Under P4 the resealed Tw→edge negative becomes a trusted-caller assertion; state this threat boundary explicitly
   instead of implying cryptographic detection.
3. Runtime physical binding must be scoped to the owned source-output body (exact re-serialization of the island), not
   whole-page equality, and must use `operators()` spans rather than a regex. This combines B with the existing
   ownership domain and makes Option A or a new hybrid domain unnecessary.
4. The engine must never sign or confirm its own output; derived records are authorized by the current edit call, not
   by an engine-held key.
5. Define no-op save semantics: when only PDF bytes change (re-serialization), the record's PDF binding updates under
   the edit call's authority while the semantic payload stays byte-identical.

**Next minimal scope.** A pure authorization/publication state-machine design under P4: initial confirmation, no-op
reopen, derived safe transitions, explicit reinterpretation, refusals, island-scoped physical verification,
pair publication with failure injection, and positive/negative fixtures, including refusal of bare hash refresh. If it
closes, the next step can be **DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR** for the static-TT/body-style scope.
Paint runtime remains behind layout and its five §15.11/§16.6 obligations.


## 19. Authorized layout publication under P4 — 2026-10-04

**Result: DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR**, only for the
narrow contract below. **Layout runtime NOT READY; paint runtime NOT READY.**
Starting main `1f5a7f0d70d4365eb0d6fac7cbebd647a3dd6ace` includes PR #39 and its
§18.10 review. Branch `codex/authorized-layout-publication`. Sections 1–18 and
all historical review/evidence artifacts are preserved.

### 19.1 B1-L-C resolution: caller/request authority, not authentication

Select **P4 = P1 trusted API caller + P2 explicit mutation call**. Initial
confirmation is the caller's explicit statement of the complete semantic state.
Every edit call authorizes only its enumerated transition from a freshly verified
current bundle. No persistent HMAC key, signature, receipt, login, PKI, account,
certificate or external/network authorization service is needed or added.
The §18 HMAC harness remains historical evidence; the new model does not call it.

Persist current semantics, intervals/dispositions, exact font/style/Tw/edges,
empty/default style, asset identity, document/paragraph identity, PDF binding and
owner identifiers. Persist an integrity checksum/version, **not authorization**.
The checksum, current SHA, document ID and owner ID detect accidental mismatch;
none permits semantic reinterpretation or grants ownership by itself. New state
is justified by the explicit request plus the deterministic transition function,
not by the fact that the engine constructed or sealed it.

In scope: stale/copy/foreign bundles, accidental record damage, manual PDF
modification, unsupported transitions, unexpected engine-side semantic drift,
partial publication and foreign ownership overlap. Out of scope: a malicious
trusted caller, compromised process/machine, hostile administrator, stolen OS
credentials or cryptographic storage attackers. No authentication infrastructure
is introduced to solve those excluded threats.

This distinction changes the §18 reseal claim: a privileged caller can supply a
self-consistently resealed alternative Tw/edge record with identical physical
bytes. P4 does **not** cryptographically detect that assertion. The caller-trusted
record store is a precondition. Ordinary corruption fails checksum/binding; an
engine-side candidate rewrite fails the captured old-state/request diff gate.
Do not promise rejection of every externally forged, physically equivalent
record while simultaneously placing that privileged forger outside the boundary.

### 19.2 Persistent payload, derived data and transient capabilities

The model's semantic payload holds current text, one explicit body-style label,
exact style values, confirmed font SHA/policy, live edges and layout region.
Derived current glyph metrics, interval ordinals, omitted whitespace/newline and
empty metrics are rebuilt from that payload and the supplied font asset. They
are persisted and checked for exact equality on reopen. Current PDF SHA, target,
owner and checksum belong to the binding envelope. All are bounded by current
text/styles/fonts/edges; no prior plans, revisions, traces or receipt chain.

`verify_current` checks version/target/checksum/current PDF SHA, recomputes derived
data and physically verifies the current PDF before returning an immutable
`Verified` value. The pure model captures PDF/record/asset bytes and uses an
in-process sentinel to prevent accidental direct dict invocation. This is not a
security token against a hostile Python caller and is never persisted.
`Authorized` captures that verified input, the immutable request and exact
permitted new payload. Mutation of a caller's dict after capture cannot alter it.
Runtime integration must also guard source revision changes while planning, as
`Transaction.commit` already does. Caller serialization/exclusive access is a
scope precondition, not an invitation to overwrite a concurrently edited source.

### 19.3 N / D / E / R and the request shape

| Class | Operation / permitted effect | Authority |
|---|---|---|
| N | Reopen: recompute current binding, keep semantic payload unchanged | No new semantic authorization; caller-trusted current record plus physical re-verification |
| D | Explicit save, body insert/delete/replace/growth, width reflow, newline and trailing-space edits | The current mutation call; exact deterministic policies only |
| E | Tw, edge add/remove/delta or Tw↔edge, font/asset, size, scale, rise, tracking, single body-style semantic label rename | Each changed semantic field must appear in `changes` |
| R | Split/join, mixed styles, vertical/nonadjacent/cross-paragraph edges, arbitrary inheritance, unsupported fonts/shaping/context, foreign ownership overlap | Refuse even if another gate or accuracy passes |

Pure request grammar (not a new runtime public API):

```
{operation: "save"}
{operation: "edit", start: int, end: int, text: string}
{operation: "reflow", width: exact_rational_string}
{operation: "reinterpret", changes: {
  word_spacing?, edges?, font?, font_size?, horizontal_scale?,
  rise?, tracking?, body_style_id?
}}
```

`font` names the explicitly supplied replacement asset SHA; replacement bytes
must be passed separately and must match. Supplying another asset without a
font-change request refuses. Unknown request fields/operations refuse. There is
no record-replacement or bare `hash_refresh` operation. An initial `confirm`
statement is a separate full semantic declaration, not an edit fallback after
old verification fails. Caller confirmation does not auto-create a source owner.

`authorize_transition(old_verified, request)` cannot accept an unverified dict.
It applies the closed operation policy, validates scope and returns exact expected
new semantics. `check_diff` compares the candidate with that exact expectation
and reports changed paths such as text, style.word_spacing, font.sha, edges,
body_style_id and region.width. Thus even an otherwise admissible/accurate new
state refuses if it differs from the requested transition. Derived intervals,
omissions and metrics are separately recomputed, not caller-overridden fields.

### 19.4 Spacing, empty-state and font transition policies

Ordinary text edits retain Tw, font and style unchanged. New spaces receive the
existing confirmed Tw. Deleting an edge endpoint or inserting between endpoints
drops that edge; preceding edits remap its current indices. Reflow retains the
same adjacent-pair meaning but suppresses its contribution at a line end. It
never infers a replacement Tw from positions. Tw→edge explicitly sets both
word_spacing and edges; edge→Tw explicitly removes the edge and sets Tw.
Edge additions/delta changes require an explicit full current edge list.

Newline insertion/deletion, trailing spaces, all-space and fully empty paragraphs
recompute current dispositions/empty metrics in the same declared body style.
No nearest glyph is consulted. `body_style_id` is only a caller-supplied semantic
label for the single style, not the identity of a physical/derived slot. An explicit
rename changes that label alone. Interval, omitted, default/typing and empty-style
associations always use the canonical `"body"` slot; the rename changes neither
style values nor derived metrics nor PDF bytes. Actual font/style value changes
are separate explicit requests applying to that one shared slot. Independent
empty-line style or mixed body/empty styles remain refused. Exact font/style changes also regenerate
empty metrics from the selected asset hhea policy. This deliberately supports a
single body style, not arbitrary style inheritance.

Same-font text edits are D. Asset/font replacement is E and must pass current
used-glyph/font verification with the new asset. A foreign change to the old
PDF font fails old verification before authorization; a record-only alteration
fails integrity or captured semantic diff. Runtime font retargeting must retain
`PageTransaction.own_fonts` and whole-alias-use checks: an alias shared with
foreign glyphs cannot be replaced merely because semantic font change is allowed.

### 19.5 Semantic permission and physical ownership stay separate

The model's `check_owner` is an explicit contract adapter, not an implemented
runtime owner verifier. A semantic request never grants a byte span. Runtime
integration must independently call current source-output identity/inventory,
`owned_body`/`witness`/containment and context validation before creating a
`Mutation(kind=source-output-rewrite, owner=...)`. `MutationProgram` and Transaction
retain overlap, source identity, foreign glyph/paint and font-resource checks.
No marker discovery, shape similarity or successful semantic verification can
promote foreign/legacy bytes to owned content.

The ready scope inherits the prototype's A/B/space/newline repertoire (maximum
256 characters), simple unhinted static TT and one body style. Initially require
an unrotated page with identity entry CTM, default opaque graphics context and no
clipping/Form invocation. Other contexts and broader shaping need separate proof.
Supplied assets remain caller inputs checked by SHA on every reopen; missing assets
refuse. They are not a third publication artifact or silently copied from staging.

**Verification target is the existing owned island, not the page.** Require the
exact current owner body span, closed q/BT…ET/Q grammar, parsed operator spans,
entry/exit context equality, current record/glyph/empty-slot containment and exact
canonical re-serialization of that body. Markers are locators only. All mutations
stay within the owned span; outside bytes/paint/resource uses remain governed by
Transaction. Empty/nonpainting operators must be contained in that same owner.
Do not create a second semantic ownership domain nested inside paint/source output.

A real read-only probe embeds a canonical repeated-AAAA body between existing
source-output markers with foreign rectangle programs before and after it.
It calls `owned.witness`, `owned.owned_body`, `operators()` and `MutationProgram`:
four distinct parsed show spans bind; before/after foreign bytes remain intact;
stale context, foreign containment and overlapping foreign mutation refuse.
The owner is a fixture grant from the trusted construction context, not auto-adopted
from its marker. Existing source-ownership regressions cover the wider identity/
inventory machinery. No regex scan is proposed for the runtime adapter.

The transition PDF fixtures still use §18's whole-page strict extractor as an
independent candidate-physical oracle. The owned-island probe is **separate**;
this PR does not claim an integrated island layout writer/verifier. The design
handoff requires moving those same exact font/code/Unicode/metric/outline checks
into the existing owner's proven current body, preserving source-output context
and Transaction's checks for the rest of the page. This is an implementation
obligation with a defined adapter boundary, not whole-page write authority.

### 19.6 S0–S9 publication machine

| State | Input → output | Failure / externally visible effects |
|---|---|---|
| S0 VerifyOld | Current PDF + trusted record/assets/target → immutable verified state | Any mismatch refuses; old pair unchanged |
| S1 AuthorizeTransition | Verified state + explicit request + separate owner proof → allowed semantic transition | No guessing or record adoption; old pair unchanged |
| S2 BuildSemanticState | Closed transition → exact new payload + semantic diff | Unrequested diff refuses; memory only |
| S3 BuildCanonicalLayout | Admissible new state → exact full tuple/layout | Unsupported metrics/overflow refuses; memory only |
| S4 BuildCandidatePDF | Owner-scoped program/resource plan → private temporary PDF | Transaction writes privately and checks old revision/foreign content |
| S5 VerifyCandidatePhysicalBinding | Actual reopened candidate PDF + expected semantics/assets → current physical proof | Stale/wrong candidate refuses, even if built by the engine |
| S6 BuildCandidateRecord | New payload + derived intervals + current owner/binding/SHA → private record | No authorization receipt; no stale byte spans |
| S7 SealIntegrity | Current record → checksummed pair, fully reverified | Seal is integrity only; staging paths must not leak into persistent references |
| S8 PublishPair | Verified private PDF+record → one new public bundle directory | Directory rename is commit point; no partial public pair |
| S9 Committed | Complete new pair → caller result | No rollback after commit; late reporting error leaves complete new pair |

Class N has no publication. A Class D no-op save may reserialize to different PDF
bytes: keep semantic payload byte-identical, verify the candidate and update only
current binding/envelope fields under the save call's authority. The model appends
a harmless final PDF newline to demonstrate changed SHA plus unchanged exact
semantics and successful fresh verification. Bare hash refresh still has no API
path: old verification, authorization and new physical verification cannot be skipped.
An externally resealed, physically equivalent record supplied by a privileged
trusted caller is the excluded assertion discussed in §19.1, not evidence that
an internal hash-refresh endpoint is safe.

Exact rational inputs flow one way to the fixed output-decimal grammar. Neither
serialized decimals nor renderer float matrices become the next semantic input.
Accuracy ≤0.002 pt remains a separate physical check and cannot repair failed
semantic authorization, ownership or publication.

### 19.7 Existing Transaction/_publish audit and the selected publication contract

`Transaction.commit` verifies source SHA, plan conflicts, foreign glyph/paint/
font/pixel preservation, builds one candidate via `publish_program`, and returns
current identity maps. `write_editable` already calls it inside a temporary output
workspace, binds/verifies the sidecar, then calls `_publish` for PDF+record.
`ensure_destination` prevents overwriting the source/existing destinations.
`publish_program` verifies the encoded PDF before linking a temporary file.
These are reusable components; Transaction alone does not publish a semantic pair.

`editable._publish` links targets sequentially and unlinks its own earlier links
if an Exception occurs. It does not switch two arbitrary public paths atomically.
Ordinary second-link failures roll back, but an additional unlink failure leaves
one public target behind. Both orders are reproduced against the **unmodified**
function. This is a real boundary limit, not a new runtime fix in this PR.

**Selected narrow solution:** publish to a new, nonexisting bundle directory on
one supported local filesystem. Stage the two fixed children privately in the
same parent, run `_publish` only inside that private directory, verify both, and
rename the complete directory to its final name. Final artifacts remain exactly
PDF + semantic record; the container directory is not a third receipt/manifest.
Do not copy across volumes or fall back to two public paths. Existing destination,
symlink/alias ambiguity, independently writable output paths, concurrent publishers
and unsupported filesystem rename semantics refuse this narrow contract.

The caller has exclusive access during publication. The public namespace contains
only committed bundle directories; readers never discover/adopt private staging
paths. Before the rename, the old pair remains current. After it succeeds, both
new files are visible together. Private cleanup may fail and leave garbage; it
must not advertise a current bundle or alter the old one. A failure reported after
the rename has a complete new pair: inspect/verify the known target before retrying,
never automatically repeat the semantic edit into another destination.

Python documents same-filesystem rename behavior and Windows destination-exists
refusal; success atomicity is explicitly specified as a POSIX requirement. See
[`os.rename`](https://docs.python.org/3/library/os.html#os.rename).
This experiment exercises the proposed commit boundary on local Windows. Future
implementation must confirm its supported filesystem's directory-rename guarantee;
no claim is made for arbitrary network filesystems. This is **exception atomicity
of the public pair**, not power-loss/OS-crash durability, fsync protocol, lock-free
concurrent publication or secure deletion of private temporaries.

The legacy two-arbitrary-path APIs retain their current behavior; this design
must be introduced as a narrow bundle-publication adapter/opt-in contract, never
silently claimed for all existing `_publish` calls. Runtime implementation remains
separate. If that output restriction is unavailable, the strict publication gate
is NOT READY for that deployment; do not substitute public rollback assumptions.

### 19.8 Evidence, gates and implementation handoff

The evidence evaluator separates synthetic current-PDF transition checks, the
actual source-output/operator probe, the pure publication model, actual existing
`_publish` failures and proposed directory-publication filesystem probes.
There is no authentication key, receipt or user-account component in the new flow.

- 17 requested transition cases, each repeated independently, compare exact new
  pairs and physically reverify generated static-TT PDFs. Additional edge cases
  cover endpoint deletion, between-endpoint insertion, suppression and edge→Tw;
  explicit Tw→edge on A B retains identical PDF bytes while changing only requested
  meaning. Explicit alternate font bytes are also verified.
- No-op reopen preserves current state; no-op save with changed PDF bytes preserves
  exact semantic payload and updates its verified binding.
- 15 evaluator negatives include unverified old state, hash_refresh, unsupported
  operations, unrequested Tw/font/default changes, stale/corrupt/foreign bundles,
  ownership/overlap failures and a wrong generated candidate. Tests additionally
  cover copied sidecars and unauthorized edge changes.
- 13 pre-commit failure injections cover every S0–S8 boundary and both file orders.
  All leave the old pair intact with no model-public targets. A post-commit exception
  test leaves a complete new pair.
- Six actual `_publish` probes expose ordinary rollback and its unlink-failure
  limitation. Ten proposed directory probes cover both orders, second-link failure,
  rollback failure, before-rename and after-rename failure: no half public pair;
  all old pairs remain intact. A private orphan is explicitly recorded when cleanup
  fails, never mislabeled as successful cleanup.

| Required gate | Evidence / scoped result |
|---|---|
| A model determinism | Exact independent repeated transitions / PASS |
| B input admissibility | Static-TT/body-style and candidate physical checks / PASS |
| C current_binding | S0 integrity/target/SHA/derived/current physical checks / PASS |
| C semantic_transition_authority | Captured old state, closed caller requests, exact semantic diff / PASS |
| C physical_mutation_ownership | Separate existing owner/parser/containment/overlap proof direction / PASS for the design adapter |
| C candidate_reverification | Real candidate reopen; wrong candidate refuses / PASS |
| C atomic_pair_publication | Private pair + one public directory commit, injected failures / PASS under the stated output/filesystem scope |

`verdict(gates)` requires all named subgates in all three layers. The publication
gate requires all ten directory probes: both successful orders publish the complete
new pair with expected contents; before-rename, second-link and rollback-unlink
failures leave old current and no new public files; after-rename errors leave the
complete new pair. All rows require old intact, no half pair and no early visibility.
A publisher that never publishes cannot pass. Ownership requires body equality,
foreign prefix/suffix preservation, entry/exit proof, four ordered nonoverlapping
parsed show spans inside the exact body span, and overlap/stale-context/foreign-
containment refusal. Missing evidence fails these gates. An accuracy
PASS cannot override authorization or publication failure. **B1-L-C is closed at
the design level under P4 and the selected directory-publication scope.** The
public-two-link rollback limit is addressed by that restricted publication design,
not by claiming the current runtime already has the new behavior.

Next minimal scope is a **separate narrow layout implementation PR**: introduce
exact semantic state/closed request transitions; adapt font/measurement binding to
the proven source-output island using parsed operators; generate and verify one
candidate with existing Transaction; persist current-only semantics; and implement
the restricted private-pair/directory-publication adapter with the same failure
matrix. Preserve legacy behavior and refuse unsupported contexts/styles/fonts.
Independent review should confirm this design verdict before implementation starts.
Paint implementation does not follow automatically: the five §15.11/§16.6 paint
obligations remain, without duplicate TODOs.

No pdfeditor/, runtime serializer, public API/schema, Transaction, runtime verifier
or paint writer changes. Runtime digest unchanged:
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.
Full suite and external-original validation are not run. See the separate
[summary](../evaluations/anchors/publication-summary.json) and
[reproduction/tests](../evaluations/anchors/README.md#authorized-layout-publication--2026-10-04).

Validation: **55 new design tests + 111 focused regressions = 166 passed**.
One Poppler-only test was deliberately deselected; full suite and external-original
validation were not run. Final evidence is the clean `runs/publication-04` run.


PR #40 small-change revision (2026-10-04): publication READY now requires every
expected outcome in both orders, including complete new contents on success and
after-rename failure. Missing evidence and a never-publishing implementation fail.
Ownership READY includes body/context/foreign-byte preservation, parsed spans and
all three refusal probes. `body_style_id` is a semantic label only; its explicit
rename leaves the canonical "body" associations, metrics and PDF bytes unchanged.
The normal, empty and all-space cases are tested.
Validation after revision: **89 passed = 60 publication design + 29 semantic binding
regressions**. The earlier 111 focused-regression result remains historical; the
remaining runtime regressions were not rerun for these evaluator/docs-only fixes.
Evidence regenerated in `runs/publication-05`; runtime digest unchanged. Full suite
and external originals not run. Independent Opus review remains the next step;
layout and paint runtime remain NOT READY.

### 19.9 Independent review — Claude Opus 5.5

- Reviewed HEAD: `f1e91ca5d6f61b5824a5ca2c23901cc6b663a3da`; base `1f5a7f0d70d4365eb0d6fac7cbebd647a3dd6ace`
  (PR #39 merge commit, verified merged). This record is a separate commit on top of the reviewed HEAD.
- **Verdict: REQUEST CHANGES — one blocking design gap (B1-L-O below); otherwise the design holds.**
  The §19 claim *DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR* is **not supported yet**.
  Layout runtime is not implemented and is **NOT READY**; paint runtime **NOT READY**.
- Tests: the README current command (publication design + semantic binding + transaction/mutation/editable/
  source-ownership regressions, Poppler deselected): **171 passed, 1 deselected (422.57 s; 60 design + 111 regressions, which also re-runs the 111 not rerun after the PR revision)**. `publication_observation` rerun into a scratch
  directory reproduced the tracked `publication-summary.json` with **0 differences** (17 positives, 15/15 negatives
  refused, 13 failure injections, 6 `_publish` and 10 directory probes, same gate values). Runtime digest
  `d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea` unchanged; no `pdfeditor/` diff.
  Full suite and external Windows/LibreOffice validation: not run.

**What holds (PASS-level findings).**

- *P4 trust model:* consistent. There is no caller authentication, receipt, key or account; the checksum is integrity-only;
  `authorize_transition` needs a `Verified` value; semantic change comes only from the closed request grammar; the
  privileged-caller reseal is explicitly out of scope (§19.1); the engine does not re-approve its own output (S5 reopens the
  candidate, and the wrong-candidate negative refuses).
- *Initial confirmation:* it is a separate full statement, physically verified before sealing; there is no marker or
  sidecar adoption, and no owner is auto-created.
- *Persistent record:* current-state bounded; there is no receipt or layout history and no hash-refresh operation; the
  record is not a change authority.
- *N/D/E/R:* I found no hidden meaning change in D. A space inserted inheriting confirmed Tw follows §17; edge
  remap/drop/suppression follows the adjacent-pair policy. `body_style_id` is verified as a pure label (diff
  `body_style_id` only; canonical `"body"` slot, metrics and bytes unchanged). Keeping it in E is acceptable, because it
  makes the label change explicit at no cost.
- *Semantic diff gate:* `check_diff` requires canonical equality with the authorized expectation, and the tampered
  Tw/font/label cases refuse. Exact rational spelling (`str(Fraction)`) is representation only.
- *Candidate re-verification and no-op save:* the save request authorizes only the binding update, after old
  verification and a fresh candidate reopen. This is distinct from a bare `hash_refresh`, which has no path.
- *Existing `_publish` limit:* reproduced. A second-link failure rolls back, but second-link plus rollback-unlink failure
  leaves a half public pair in both orders. It is correctly not called atomic.
- *Directory publication:* private children, verification, a same-parent non-existing target and one rename as the commit
  point are sound for the stated scope (exclusive caller, local filesystem, no overwrite, exception atomicity only).
  `publication_gate` requires all ten rows, including successful complete contents and after-rename completion. A
  never-publishing implementation and missing evidence fail. Private garbage is correctly separated from public state.
  After-rename failure with inspect-before-retry is correct.
- *Transaction reuse:* source SHA check, `MutationProgram` overlap, foreign glyph/paint/font checks, one candidate save
  and identity maps are reusable. `publish_program` can write into the private staging directory.
- *Accuracy:* it remains separate; it is not an input to any authorization/ownership/publication gate.

**Blocking finding B1-L-O: the runtime surface and the owner record that must be published with it are undefined.**

- Where: §19.2/§19.5/§19.7 (artifacts "exactly PDF + semantic record"); the evaluator record `owner={'domain','id'}`
  and `check_owner` comparing IDs only.
- Issue: §19 makes the existing owned source-output island the write authority. In the runtime, source-output ownership
  exists **only** in shared-flow v2 sidecars (`slots[*].source_output` with marker_id, created_from and the mutable
  `current` program/range/block/context witness). `_source_output` is reachable only from `edit_shared_flow`; ordinary
  editable has no owner, and PR #33 deliberately keeps it closed. §19 names neither the target surface nor where the
  owner's `current` witness lives after a save. Every save rebinds that witness. Publishing only PDF + semantic record
  would leave the shared-flow sidecar stale, so the next open refuses. Avoiding that needs either a third artifact
  (contradicting the pair gate) or embedding (a shared-flow schema change). A shared-flow paragraph can also span several
  slots and regions, while the semantic record assumes one paragraph and one region.
- Why design, not implementation: this decides which persistent record carries write-authority evidence, how many
  artifacts the atomic publication commits, and which schema version changes. Those are exactly the authority and
  publication boundaries the design phase settles; an implementer would otherwise invent them.
- Safety impact: fail-closed (stale owner witness → refusal), but no safe positive lifecycle is defined, and the
  `physical_mutation_ownership` and `atomic_pair_publication` gates are evaluated against an owner record that does not
  correspond to the runtime one. The island probe self-computes the owner `current` from the same PDF under a fixture
  grant, so it proves adapter mechanics, not record co-binding.
- Minimum resolution (small design addendum, no runtime):
  1. Choose the surface. Recommended: a shared-flow v2 text source slot that is already `owned`, whose paragraph has
     exactly one slot and no continuation destination.
  2. Define the composition. Recommended: embed the semantic payload in that slot under a new shared-flow schema
     version, so the bundle stays PDF + one sidecar, with `source_output.current` and the semantic record sealed and
     published together.
  3. State that the canonical layout writer replaces `_source_body` for that slot only, and that the region comes from
     the slot's confirmed region.
  4. Make the ownership and publication gates require the persisted owner witness to be rebound in the published sidecar.
  5. Add one evidence probe: a real shared-flow v2 slot reopened from its stored record (not self-witnessed), a
     transition, a candidate, rebinding of both records, and refusal of a stale owner witness.
  Once that addendum passes review, the remaining work is implementation.

**Non-blocking notes.**

1. `island_probe` hardcodes `entry_exit_proven=True`. It is implied by `owned.witness` succeeding, but should be
   derived from its entry/exit comparison.
2. The directory contract has no in-place "current" pointer. Consumers find the newest bundle through caller state, and
   any later "latest" pointer update lies outside the atomicity claim. Document this.
3. Gate naming: `current_binding` is derived from the negative set. Also require a positive stored-record reopen.
4. Implementation split once READY: (L1) semantic state + transitions + read-only island-scoped verifier; (L2) canonical
   island writer + Transaction + owner/semantic rebind; (L3) bundle-directory publication adapter + full failure
   matrix. Each lands behind refusal of unsupported contexts.
5. The narrow scope (A/B/space/newline, ≤256 chars, static unhinted TT, one body style, left, identity CTM, unclipped
   opaque context) is small enough. Do not widen it before the first implementation is validated.

**Next scope.** The B1-L-O addendum above. After it is reviewed, DESIGN READY can be reconsidered and layout
implementation can start, preferably split into L1–L3. Paint runtime stays behind validated layout and the five
§15.11/§16.6 obligations.

### 19.10 B1-L-O resolution — semantic state inside the shared-flow owner sidecar

History preserved: §19 claimed DESIGN READY, and §19.9 (Claude Opus 5.5) answered **REQUEST CHANGES** for B1-L-O,
the undefined runtime surface and owner-record co-publication. This section closes only B1-L-O. It does not
rewrite §19 or §19.9, and it leaves P4 unchanged (trusted caller, explicit request, checksum for integrity only,
no HMAC/PKI/receipts, malicious privileged caller out of scope). It is design/evidence only: there is no
`pdfeditor/` change, and no runtime schema, opener or writer.

**Selected runtime surface.** A shared-flow v2 **text source slot** whose `source_output.state == "owned"`, in a
flow with exactly one paragraph, exactly one source slot, one confirmed region and one body style; no
continuation destination, destination binding or generated continuation slot; within the existing narrow
static-TT/A-B-space-newline/left/identity-context scope. General editable and multi-slot shared flow remain out of
scope. Any failed condition refuses (`scope()`).

**Schema and artifacts.** The proposed `pdfengine-shared-flow-3` keeps **two persistent artifacts: the PDF and one
shared-flow sidecar**. This supersedes the §19 wording "PDF + semantic record": there is no separate semantic file
and no third artifact. The single slot keeps its existing `source_output` record and gains
`slots[slot_id].semantic = {version, payload, derived, binding}`. The whole state is sealed by the existing
`model_sha256` digest (integrity only).

| Field class | Fields |
|---|---|
| Immutable creation identity | `flow.id`, slot `paragraph_id`/`region_id`/`source_snapshot_sha256`, `source_output.version`/`marker_id`/`created_from`, `contract_sha256` |
| Mutable current owner witness | `source_output.state`, `source_output.current` (program/range/block/entry-context SHA) |
| Semantic authority | `semantic.payload`: text, exact style (incl. Tw intent), font asset SHA + policy, positioning edges, `body_style_id`, `region_id` |
| Derived / current physical binding | `pdf_sha256`, slot `binding`, `generated_fonts`, `physical_breaks`, `semantic.derived` (intervals, omissions, empty metrics), `semantic.binding` |
| Integrity | `model_sha256`. `previous_model_sha256` keeps its existing digest-link role and is not history. |

`source_output.created_from` is owner creation provenance, not semantic content; the semantic payload never
re-derives owner identity. Everything is current-state bounded: no old semantic records, edit, owner-witness or
confirmation history, and no previous ranges or layout snapshots.

**Co-binding.** `semantic.binding = {slot_id, paragraph_id, region_id, pdf_sha256, marker_id,
owner_program_sha256, owner_block_sha256}` must equal the slot identity, the sidecar `pdf_sha256` and the slot's
`source_output.marker_id`/`current`. Ownership is re-proven only by the **unmodified runtime v2 validator** on a
derived, never-persisted v2 projection (semantic removed, v2 schema, resealed). That validator runs
`record_identity`, marker inventory, grammar, context witness and containment through `source_ownership.validate`.
The semantic payload is then checked against the same revision:

- text against the paragraph logical text and the slot binding;
- style against the confirmed style registry;
- font SHA against the island's `generated_fonts` provider SHA;
- derived intervals, omissions and empty metrics, recomputed from the payload;
- the island's emitted characters against the derived plan;
- entry/exit context derived from `owned.context`, not hardcoded.

Owner witness and semantic payload are therefore valid only together, for one current PDF.

**Region authority.** `payload.region_id` must equal the slot's region. Layout values come from
`state['regions'][region_id]` (x, width, first_baseline, bounds) and the paragraph's confirmed `min_line_height`.
Nothing is inferred. A change that needs a different region is refused (`REGION_CHANGE_REQUIRES_CONFIRMED_REGION`).

**Writer target.** The future L2 writer rewrites only that single owned slot's `_source_body`-equivalent body,
between its existing markers, through `MutationProgram`/`Transaction`. It never touches foreign prefix/suffix,
other page content or other slots, and never falls back to a whole-page writer. In this evidence the existing
runtime `_source_body` writer stands in for the canonical writer. Canonical placement remains the §18/§19 fixtures'
responsibility, and Tw≠0 or edges are admitted only once the L2 writer can serialize them
(`TW_EDGE_REQUIRE_L2_WRITER`).

**Opt-in only.** `initial_confirmation` needs an explicit `confirm-semantic-layout` request with a full payload over
an already-owned, runtime-validated v2 slot. Opening a v2 sidecar never creates a semantic payload, and the runtime
v2 opener rejects the v3 schema. No owner is created or adopted.

**Candidate lifecycle (consistent with `edit_shared_flow`).**

1. Verify the old PDF + sidecar: integrity, scope, the runtime owner validation on the projection, and semantic
   co-binding.
2. Express the slot as the §19 P4 `Verified` input and `authorize_transition` the explicit request.
3. Run the existing `edit_shared_flow` on the projection. It performs the owned-body rewrite, Transaction commit,
   slot binding, `source_ownership.rebind`, generated fonts, `pdf_sha256`, physical breaks, reseal and validate,
   writing into private build staging.
4. Freshly reopen the candidate with the runtime validator.
5. Attach the authorized payload; `check_diff` it against the authorization.
6. Reseal the complete v3 sidecar.
7. Freshly validate PDF + sidecar.
8. Stage exactly `document.pdf` + `shared-flow.json` privately and publish them as one new bundle directory.
9. Freshly reopen the published bundle in new processes.

General shared-flow editing is not given this capability.

**Publication.** The §19 directory contract is kept with the two artifacts renamed `document.pdf` and
`shared-flow.json`. The complete new sidecar is finished and validated in private staging before the one rename.
New PDF + old sidecar, old PDF + new sidecar, or a semantic-only file is never a public current bundle. The
contract publishes a **new unique bundle directory only**. No latest/current pointer, symlink or index is maintained
or claimed atomic; the caller receives and manages the new bundle path.

**Evidence** (`semantic_owner_observation.py`, `semantic-owner-summary.json`; real single-slot shared-flow fixture
saved to disk and reloaded before every lifecycle):

- **14 positives:** stored reopen, stored owner witness, stored semantic payload, no-op reopen, authorized text edit
  (diff text only, class D), candidate PDF, owner rebind (same marker/created_from, new current, same entry
  context), semantic update, combined v3 reseal, fresh candidate validation, bundle publication (exactly two files,
  no pointer), fresh published reopen in 3 new processes (identical), and no-op save with byte-identical payload.
- **20 negatives refuse:**
  - old owner + new PDF;
  - new semantic + old owner (two variants);
  - new owner + old semantic;
  - copied owner record;
  - wrong slot, paragraph or region;
  - second slot, continuation, generated slot, unowned slot;
  - marker spoof, stale context, foreign glyph in the island;
  - semantic-only self-seal, unrequested candidate semantics, implicit confirmation;
  - v2 opener rejects v3, and v2 reopen never auto-upgrades.
- **6 isolated owner refusals** call `source_ownership.validate` directly, after refreshing all hashes, so that the
  intended defect (not an incidental PDF-hash mismatch) produces the refusal: duplicate marker, witness mismatch ×3,
  foreign glyph, creation identity mismatch.
- **Publication matrix:** the real candidate pair in both orders × none/second-link/rollback-unlink/before-rename/
  after-rename. Every row satisfies the §19 gate (no half pair, no early visibility, old bundle intact and still
  opening); private leftovers after rollback failures are recorded as garbage, not as current bundles.
- The §19 `island_probe` now derives `entry_exit_proven` from the context comparison (regenerated
  `publication-summary.json`, unchanged values).

| Required gate | Result |
|---|---|
| A model_determinism.exact_derived | PASS (repeated fresh reopens identical) |
| B input_admissibility.scoped_physical / scope_conditions | PASS (all scope refusals) |
| C current_binding | PASS (positive stored reopen + all negatives) |
| C semantic_transition_authority | PASS (authorized D diff; self-seal/unrequested/implicit refused) |
| C stored_owner_binding | PASS (stored owned witness; copied owner isolated refusal) |
| C physical_mutation_ownership | PASS (stored slot, owned, current witness, exact span, containment, derived entry/exit, candidate rebind, isolated stale/spoof/context refusals) |
| C candidate_reverification | PASS |
| C owner_semantic_rebind | PASS (rebind + update; both one-sided stale states refused) |
| C fresh_bundle_reopen | PASS |
| C atomic_pair_publication | PASS (two-artifact matrix) |

**B1-L-O is closed at the design level.** Under the selected single owned slot surface and the restricted
bundle-directory contract, this section re-asserts **DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR**. Layout
runtime is **not implemented** and remains NOT READY until L1–L3 land and are validated. Paint runtime remains
**NOT READY** behind validated layout and the five §15.11/§16.6 obligations.

Recommended implementation split (none implemented here):

- **L1:** semantic payload schema (`pdfengine-shared-flow-3`, opt-in) + transition model + read-only stored-owner/
  island verifier.
- **L2:** canonical island writer for the single owned slot + Transaction integration + owner/semantic rebind.
- **L3:** bundle-directory publication adapter + failure matrix + end-to-end reopen.

Validation: 19 new B1-L-O design tests plus PR #40 publication design, semantic binding, source-ownership, shared-flow, transaction, mutation and editable regressions: **211 passed, 1 deselected** (Poppler-only). A second evaluator run reproduced `semantic-owner-summary.json` byte-for-byte. Runtime digest unchanged
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`. Full suite and external originals were not
run. An independent review of this resolution is recommended before L1 starts.


## 20. L1 runtime: shared-flow semantic layout state — 2026-10-04

Starting main `b14bc96b528955bd4a12bd4c8968e661a02a161e` (PR #40 merged at `704b396`; B1-L-C and B1-L-O closed,
DESIGN READY). This section records the **L1 implementation**; §1–§19 are preserved. L1 makes the semantic
authority of §19.10 an actual runtime schema that can be confirmed, opened read-only and transitioned into an
authorized next semantic state. **It does not write PDFs**: no canonical island writer, no candidate save, no owner
rebind and no bundle publication (those are L2/L3). Paint runtime remains NOT READY.

### 20.1 What L1 adds

| Module | Responsibility |
|---|---|
| `pdfeditor/semantic_measure.py` | Pure exact measurement for the narrow scope: pinned font policy, static-TT checks, nominal glyph metrics from the confirmed asset, one provider-independent measurement rule, exact left layout with explicit newlines inside the confirmed region's horizontal and vertical capacity, confirmed adjacent edges and the §17 edge edit policy. No renderer or PDF input. |
| `pdfeditor/semantic_layout.py` | `pdfengine-shared-flow-3` schema, explicit confirmation, read-only open/verification, closed N/D/E/R transitions, exact semantic diff. |

Public API (minimal, no writer/save/publication):

- `confirm_semantic_layout(source, model, *, slot_id, semantic) -> state`: the trusted caller's complete semantic
  statement over an already owned v2 slot. The v2 sidecar is validated first by the unmodified `open_shared_flow`.
  No owner is created and nothing is inferred from PDF geometry. Returns a v3 state that has already passed a full
  `_verify`. The caller persists it, and the PDF is unchanged.
- `open_semantic_flow(source, model) -> {status: 'restored', state, slot_id, paragraph_id, region_id, semantic, derived,
  owner, island} | {status: 'needs_confirmation', reason}`: read-only. Repeated opens return identical semantic, owner and
  derived values and write nothing.
- `plan_semantic_transition(source, model, request, *, asset=None) -> plan`: verifies the current bundle, applies the
  closed request policy and returns `classification`, `current`, `next`, `next_derived`, an exact `diff`,
  `executable_now` and `requires`.
- `require_authorized_payload(plan, candidate)`: refuses any candidate payload that differs from `plan['next']`.

### 20.2 Runtime schema and field authority

v3 is the v2 state with `schema: "pdfengine-shared-flow-3"` and, in the single owned slot only,
`semantic = {version: 1, payload, derived, binding}`. Persistent artifacts remain the PDF + this one sidecar.

| Class | Fields |
|---|---|
| Immutable creation identity | `flow.id`, slot `paragraph_id`/`region_id`/`source_snapshot_sha256`, `source_output.marker_id`/`created_from`, `contract_sha256` (unchanged v2 contract) |
| Mutable current owner witness | `source_output.state`, `source_output.current` |
| Semantic authority | `payload`: `text`, exact `style` (font_size, horizontal_scale, rise, tracking, word_spacing, spacing_intent), `font` (asset SHA + pinned policy), `edges`, `body_style_id`, `region_id` |
| Derived / current binding | `derived` (intervals `current:i`, omitted trailing spaces/newlines, empty ascent/descent), `binding` (slot/paragraph/region IDs, `pdf_sha256`, `marker_id`, owner program/block SHA), `pdf_sha256` |
| Integrity | `model_sha256` (existing digest; not authorization) |

Values are canonical exact rational strings (a non-canonical spelling such as `12.0` refuses). Unknown fields, history,
receipts and prior revisions refuse.

### 20.3 Verification order (no second owner verifier)

1. Model checksum.
2. Scope: exactly one paragraph, one source slot (no `destination_id`/`creation_provenance`), one confirmed region,
   one body style, left alignment, no continuation destinations/bindings, `source_output.state == "owned"`.
3. **Owner, reused unchanged:** an in-memory v2 projection (semantic removed, schema v2, resealed, never persisted)
   is passed to `shared_flow.open_shared_flow`. Thus `source_ownership.validate` (`record_identity`, inventory,
   grammar, context witness, containment), PDF revision, contract, generated fonts and placement are exactly the v2
   rules. v2 and v3 cannot diverge.
4. Payload shape and `binding` == current slot identity + `source_output.current` + `pdf_sha256`.
5. Text == paragraph logical text == slot binding text. Style == the confirmed body style (font_size,
   horizontal_scale, tracking, rise = −baseline_shift). Tw ≠ 0 or edges refuse, because no current writer can
   witness them (L2).
6. Font: the payload SHA equals the confirmed registry provider SHA and the supplied asset bytes. The asset must be
   static, unhinted, simple-glyph TrueType with the pinned policy. Island text must be painted only through
   `generated_fonts` aliases owned by the slot whose provider SHA is the semantic asset; no subset or GID equality
   is required.
7. Region from `state['regions'][region_id]` (x, width, first_baseline, and top/bottom from `bounds`) and the
   paragraph `min_line_height`, as exact rationals. Every line, including empty lines, must satisfy
   `baseline - ascent >= top` and `baseline + descent <= bottom` exactly (no tolerance), and must fit the width.
   Derived state is recomputed and must be equal.
8. Island: `owned.witness` equals `current`; entry and exit contexts are derived and equal; the entry context is
   identity CTM, unclipped, opaque, no ExtGState, and default black fill/stroke (DeviceGray 0, as in the PDF initial
   graphics state); the island's emitted characters equal the exact plan.

### 20.4 Transitions (read-only)

| Class | Requests | L1 result |
|---|---|---|
| N | `reopen` | No change; `executable_now=True` (nothing to publish) |
| D | `save`, `edit {start,end,text}` (insert/delete/replace/growth/newline/trailing space), `reflow {width}` equal to the confirmed region width | Next payload/derived with the §17 edge policy (endpoint delete or insertion between drops an edge; earlier edits remap). A different width is a region change and refuses. `executable_now=False`, `requires` = L2 writer + authorized publication. |
| E | `reinterpret {changes}` over word_spacing, edges, font (needs the explicitly supplied asset), font_size, horizontal_scale, rise, tracking, body_style_id | Authorized next semantic state and diff, not executable in L1 |
| R | split, join, mixed_style, vertical/cross-paragraph/nonadjacent edges, region_reassignment, hash_refresh, replace_record, unknown | Refuse |

Semantic change comes only from the request. There is no inference from geometry, no hash-refresh API and no
record-replacement path. `require_authorized_payload` refuses any unrequested diff (Tw, font, label, edges).

### 20.5 Evidence

`tests/test_semantic_layout.py` (58 tests) builds a real single-slot shared flow (owned after the first save, then a
second revision) only from runtime APIs. It covers:

- explicit confirmation; no implicit upgrade (the v2 opener still opens v2, never adds semantics, and rejects v3);
  unowned and non-v2 confirmation refused; unwitnessed Tw/edges and non-canonical values refused;
- stored reopen ×3 identical and byte-untouched; owner, text, style, font, region, derived and binding tampering
  refused even after reseal;
- stale owner (current PDF + old `source_output.current`) refused; stale semantic refused, including a forged current
  binding (finally refused by text binding); old owner against a new PDF refused;
- every scope refusal with its specific reason; copied owner refused;
- duplicate marker, marker spoof, foreign glyph and stale context refused, also isolated through
  `source_ownership.validate`;
- D/E/N/R transitions and unauthorized-diff refusal;
- region capacity: wrap inside the region passes; a long insert, extra empty lines, font-size growth and a rise above
  the top are refused before any next state is authorized; a non-default (red) entry fill refuses confirmation.

Full suite (rerun after the region-capacity and entry-paint fixes): **1,416 passed, 8 skipped, 0 failed** (four file-balanced shards of the whole `tests/` tree, run in parallel; skips: 1 Poppler-unavailable renderer check, 2 AES-provider-unavailable save tests, 5 external-corpus tests not downloaded). No existing test was changed or removed. External Windows/LibreOffice validation was not run: L1 is a read-only state layer, adds no PDF
output and claims no rendering result. Runtime digest after L1: `68fc3a4085a566bdd389227e9664e8b8f46505225768d6d0bd8aadacd71359a5`.

### 20.6 L2 obligations

L2 implements the canonical island writer for this single owned slot (replacing only its `_source_body`-equivalent
body), Transaction integration, `source_ownership.rebind` together with the semantic payload/binding update, and
candidate re-verification through `open_semantic_flow`. It also admits Tw/edge execution once the writer
serializes and witnesses them, and adds region/registry changes only with an explicit confirmed contract. L3 adds
the bundle-directory publication adapter and failure matrix. Paint runtime stays behind validated layout and the
five §15.11/§16.6 obligations.


## 21. L2 runtime: canonical semantic island writer — 2026-10-04

Starting main `9f50cf0b5ea8ff4656bf87d511b8b227f8abf556` (PR #41, L1 merged). §20 is preserved. L2 connects an
authorized semantic transition to an actual PDF mutation of the single owned source slot. Candidates are private
(PDF + shared-flow-3 sidecar under a caller workspace). **No public publication, pointer, receipt or history** (L3).
Paint runtime remains NOT READY.

**Result: L2 NOT COMPLETE — one recorded blocker, B-L2-S.** The writer, Transaction integration, owner and semantic
rebind, candidate verification, no-op stability, Tw and Tw↔edge are implemented and verified. Explicit style-value and
font-asset reinterpretations are refused by design until a registry-change contract exists (§21.6).
*(Later: §21.8 closes B-L2-S; L2 COMPLETE. This paragraph is kept as the PR #42 record.)*

### 21.1 Runtime surface and API

| Module | Responsibility |
|---|---|
| `pdfeditor/semantic_island.py` | Pure canonical body serializer: the exact plan becomes `q BT … ET Q` with the fixed decimal policy; reads the alias/codebook back for exact re-serialization |
| `pdfeditor/semantic_writer.py` | `build_semantic_candidate(source, model, request, *, workspace, asset=None)` returns `{directory, pdf, sidecar, plan, verification, body}` |
| `pdfeditor/semantic_layout.py` | Unchanged policy. The island check now reports `canonical` and admits Tw ≠ 0 or edges only when the island is the exact canonical body. |

The scope is unchanged from L1 (one paragraph, one owned slot, one region, one body style, left, A/B/space/newline,
≤256 characters, static TT, identity/unclipped/opaque/default-black entry, exact region capacity).

### 21.2 Authority chain (no new authority)

1. `open_semantic_flow(source, model)` must be `restored` (owner via the unmodified v2 validator, semantic co-binding,
   island).
2. `plan_semantic_transition` is the only transition policy. `require_authorized_payload(plan, payload)` pins the writer
   to `plan['next']`. The writer recomputes the exact plan with L1 `_derive` and refuses any difference from
   `plan['next_derived']`.
3. Class N needs no candidate. Class D and E over `word_spacing`/`edges`/`body_style_id` are writable. Other E
   transitions refuse (B-L2-S).
4. Inside `Transaction(source)`: the source SHA must equal the verified revision. The slot's existing generated fonts
   are registered with `own_fonts`. `source_ownership.owned_body` proves the byte span. One
   `Mutation(start, end, body, kind='source-output-rewrite', owner=slot_id)` replaces only the body between the markers.
   The font comes from the confirmed asset through `ShapedFont.resource` and `reserve_font_alias(owner=slot_id)`, so only
   the slot's own releasable alias is re-targeted and foreign aliases are untouched. `commit` keeps its source-revision,
   overlap, foreign glyph/paint, font-fingerprint and pixel checks and saves once.
5. After commit, `bind_document_edit` rebinds the slot through the identity map. Then `styles.bind_fragment`, slot
   range/render_end/occupancy, style binding, the shared-flow logical record (`styles.project`, as `edit_shared_flow`
   does), `source_ownership.rebind` (same marker_id/created_from/entry context, new current witness),
   `_generated_fonts`, `pdf_sha256`, `physical_breaks` and `previous_model_sha256` (digest link only) are updated. A
   source change during commit refuses.
6. `semantic_layout._attach` seals the authorized payload with derived state and a binding to the candidate revision.
   The sidecar is written to disk and reopened with `open_semantic_flow`. Its semantic state must equal `plan['next']`
   and its derived state `plan['next_derived']`. The island must be canonical, its bytes must equal the serialized body,
   its glyph order must equal the plan, and the MuPDF origins must be within ≤0.002 pt (an additional check after the
   exact checks).

Any failure removes the private candidate directory. Source PDF and sidecar are never modified.

### 21.3 Canonical grammar and semantic → operator mapping

```
q BT /<slot alias> <size> Tf <scale×100> Tz <tracking/scale> Tc 0 Tw <−rise> Ts <fill> <g|rg|k>
1 0 0 1 <x> <top − baseline> Tm <code> Tj        one per emitted glyph, plan order
(empty: 1 0 0 1 <region x> <top − first_baseline> Tm [] TJ)
ET Q
```

Only source-output body operators are used; there is no `%`, cm, gs or color space. Positions come from the exact plan.
The positions carry Tw (space advance) and confirmed edges (pair advance, suppressed at a line end), so intent remains
confirmed rather than physically witnessed (Option B). Tc witnesses tracking (equal to the confirmed registry tracking),
Ts witnesses rise and Tz scale. Decimal spelling is the §18/§19 output policy: at most 6 places, ties to even, no
exponent, no negative zero. It is never read back as semantic input.

The font codebook is fixed: sorted {used chars} ∪ {A, space}, so codes are stable for a stable character set. The fill
is the confirmed registry fill. Repeated glyphs each get their own `Tm`/`Tj` in plan order; operators are never reused
or reordered. Trimmed spaces and newlines are not painted. An empty body keeps a typing `[] TJ` witness that only
witnesses the style adapter.

### 21.4 Evidence (`tests/test_semantic_writer.py`, 32 tests; L1 + L2 90 passed)

- **Only the owned body changes:** the page prefix and suffix around the markers are byte-identical. The owner keeps
  marker_id, created_from and entry context and gets a new current witness.
- **No-op stability:** save1 = save2 = save3 island bytes and operator counts, MuPDF 144 dpi pixels identical, each
  reopened canonical.
- **Text edit:** `A B` → `AA B` → no-op (same body).
- **Tw:** 0 → 6/5 moves only the glyph after the space (34.4 → 35.6), and it persists through a no-op.
- **Tw→edge and edge→Tw:** physically identical bodies, distinct records, both verify.
- **Edge policy in actual candidates:** endpoint delete drops the edge, insertion between drops it, an earlier edit
  remaps it, and at a line end the right endpoint starts the next line at the region x.
- **Empty and regrow:** empty → no-op stable → regrow returns the original body. Newline and trailing space produce the
  derived omissions, and a trailing space leaves the body unchanged. A `body_style_id` rename keeps the physical body.
- **Refusals:** reopen (nothing to write), split, hash_refresh, region change, region overflow, nonadjacent edge, stale
  owner, stale semantic, serializer tamper, writing outside the owned body, marker spoof, foreign glyph, context
  change, and a source revision race.
- **Failure injection** at plan, serializer, mutation, commit, rebind, semantic bind and candidate re-verification:
  inputs are byte-identical and no candidate is left in the workspace.
- **Fresh-process reopen** of edit, no-op, Tw→edge and label candidates.

Full suite: **1,448 passed, 8 skipped, 0 failed** (four file-balanced parallel shards of the whole `tests/` tree; skips: 1 Poppler unavailable, 2 AES provider unavailable, 5 external corpus not downloaded). No existing test was changed or removed. Poppler is unavailable here. External Windows/LibreOffice validation was not run: there is no
prepared single-owned-slot target, and no new adaptation was started. Runtime digest: `d9e69d513cc20cb1bf3060051372e11452eb8a04f7c003df751b1a8a9fb2bf40`.

### 21.5 L2 completion check

All criteria hold except **font/style actual transitions**:

- the writer exists, rewrites only the owned body, reuses L1 transition authority and Transaction, and guards the
  source revision;
- candidates are private; owner rebind and semantic rebind succeed; the sidecar is resealed and passes a fresh
  `open_semantic_flow`;
- no-op output is stable; Tw and edge output reopen correctly;
- scope refusals are preserved, inputs are unchanged on failure, and no L3 publication was added.

### 21.6 Blocker B-L2-S — style/font reinterpretation needs a registry-change contract

The v3 verifier reuses the v2 validator unchanged. In v2, `story_styles.validate_registry` binds each registry
attribute (font_size, horizontal_scale, tracking, baseline_shift, fill) and the reflow provider to immutable source
observations. `validate_fragment` requires the slot's physical inline attributes and provider recipe to equal that
registry, and L1 requires the semantic style to equal it too. A candidate that paints the new size, scale, rise or
tracking, or a new font asset, therefore cannot pass owner validation, and changing the registry would break the
source-witness rule. Closing this needs a design decision, not more writer code: an explicit, caller-confirmed
registry-change contract for the owned slot. For example, a v3 registry entry whose provenance is the confirmed
semantic style rather than source observations, validated against the semantic payload and the canonical island,
while the immutable source witnesses stay as creation evidence. Until then the writer refuses these E transitions
(`B-L2-S`), and L1 planning still computes them read-only.

### 21.7 L3 obligation

L3 publishes a verified candidate pair (`document.pdf` + `shared-flow.json`) through private staging, one new bundle
directory and one rename, with the PR #40 failure matrix and an end-to-end public reopen. It adds no latest/current
pointer. *(Later: §22 implements this boundary; L3 COMPLETE.)*

### 21.8 B-L2-S resolution — current semantic style/font authority — 2026-10-04

Starting main `77bb93e705307c8a52be09cc8116936dfebc12f3` (PR #42 merged). §21.1–§21.7, including the B-L2-S record in
§21.6, are preserved as the historical L2 state. This section closes B-L2-S.

**Root cause.** Shared-flow v2 has one style authority: the registry entry, whose attributes and reflow provider are
bound to immutable source observations (`validate_registry`), and whose values the current fragment must equal
(`validate_fragment`, provider recipe). The v3 verifier ran that validator on a projection with the semantic record
removed, and L1 `_registry_binding` also required the semantic style to equal the registry. "Source observation" and
"current semantic authority" were the same field, so 12 pt → explicit 13 pt failed whichever of the two was kept.

**Selected model: three separate layers, one new field, no source rewrite.**

| Layer | Fields | Changes when |
|---|---|---|
| A. Creation evidence | `paragraphs[p].style_registry` (attributes, `source_observations`, `reflow_provider`, `provider_relation`), `source_model_sha256`, `source_snapshot_sha256`, `source_output.created_from`, `contract_sha256` | never (immutable; still validated by the unchanged `validate_registry` and the contract digest) |
| B. Current semantic authority | `slots[s].semantic.payload` (style values, font SHA + pinned policy) and the new `slots[s].semantic.current = {provenance, provider: {path, sha256}}` | initial confirmation (`source-confirmed`), then only an explicit E change of `font_size`/`horizontal_scale`/`rise`/`tracking`/`font` (`caller-confirmed-current-semantic`) |
| C. Current physical binding | canonical owned island, `slots[s].binding` (+ `fonts` recipe = current provider), `style_binding`, `generated_fonts[page][alias]`, owner witness, `semantic.binding` (+ `font_resource`, `font_subset_sha256`) | every candidate revision (current only, no history) |

Generated physical fonts keep the existing `generated-by-pdfengine` provenance; source observations keep
`observed_source`. The three provenances are never merged.

**Schema.** `pdfengine-shared-flow-3` is unchanged; the semantic record goes from `version: 1` to `version: 2`:
`{version, payload, current, derived, binding}`, with the binding adding `font_resource` and `font_subset_sha256`. The
source registry schema is unchanged. Stored version 1 records still open under their PR #41/#42 rules, are never
upgraded by opening, and keep the old refusal for style/font reinterpretation (message `B-L2-S: semantic version 1 …`).
The only upgrade path is explicit: `confirm_semantic_layout` given the verified version 1 bundle and exactly its stored
payload re-seals it as version 2 `source-confirmed` (any other payload refuses). A version 1 record with a nonzero
`rise` is refused by that path (see the rise sign contract below). No public API was added; the internal
additions are `semantic_layout.current_registry/current_confirmations/codebook/font_resource/island_font/
require_authorized_authority` and `shared_flow._open_current_flow`.

**Projection contract.** A version 2 record is verified as:

1. integrity, scope, payload shape, current-authority shape (`provider.sha256` = payload font SHA);
2. layer A and the owner: the v2 `_validate` on the semantic-free projection. For `source-confirmed` this is exactly
   v2. For `caller-confirmed-current-semantic`, `_open_current_flow` passes an in-memory current-style adapter, used
   **only** for `validate_fragment`: current values and the current provider as separate fields with
   `caller-confirmed-current-semantic` provenance; fill/observed color stay the source-confirmed values. The
   registry, its observations, the contract digest, `source_ownership.validate` and generated-font records are
   validated unchanged. The adapter is never persisted. v2 call sites are untouched (`current_styles=None`);
3. co-binding to the owner witness and this PDF revision;
4. `_registry_binding`: `source-confirmed` (and version 1) style/font must equal the registry; caller-confirmed
   authority is not compared with source observations;
5. exact derivation from the current asset (`current.provider`), then the island: caller-confirmed authority requires
   the exact canonical body; whenever the island is canonical, the slot's generated font record must name the current
   asset identity and the deterministic canonical subset rebuilt from that asset must have the same SHA, BaseFont and
   codes (so glyph IDs) as the island; every island text operator selects that one slot-owned alias;
6. the binding's `font_resource`/`font_subset_sha256` must equal the island's generated font.

**Rise sign contract (per record version).** Semantic `rise` and registry `baseline_shift` are both page y-down
offsets: origin = baseline + rise, the canonical island writes Ts = −rise, and Ts 1 is observed as baseline_shift −1.
So for version 2, source-confirmed rise must **equal** the registry baseline_shift; the fixture with source Ts 1
confirms rise −1 (rise +1 refuses), and its canonical saves write `1 Ts`, observe baseline_shift −1 and stay
byte-stable. Version 1 records keep the rule they were sealed under in PR #41/#42, `rise == −baseline_shift`, so a
stored version 1 bundle over a nonzero source baseline_shift still reopens unchanged. Evidence: on the same bundle, PR
#42 main seals and reopens `rise = +1` over baseline_shift −1, the first PR #43 commit (`a994d7d`, which applied the
version 2 rule to version 1) refused it, and the fixed code reopens it as version 1; the test fixture's version 1
record is byte-identical to the one PR #42 seals. The legacy and version 2 meanings of a nonzero stored rise are
opposite, so the explicit version 1 → 2 upgrade **refuses** a nonzero rise instead of re-sealing it (which would
silently flip its meaning) or converting it (which would rewrite the caller's stored statement). Supplying the
converted value instead is a reinterpretation of the stored payload and also refuses. Such a slot is confirmed as
version 2 from its shared-flow v2 owner sidecar. Rise 0 records upgrade as before.

**Writer.** `plan_semantic_transition` returns `next_authority` (and `version`). The writer pins it with
`require_authorized_authority` next to `require_authorized_payload`, writes through the PR #42 canonical serializer and
Transaction with the current-style view (adapter values, explicit tracking/rise confirmations, current provider), binds
the fragment to that view, and seals version 2 with the island's generated font. No writer-specific transition rule
was added. `_writable` no longer refuses style/font for version 2.

**Lifecycle evidence** (`tests/test_semantic_authority.py`, actual candidates, every one reopened from disk):

| Case | Result |
|---|---|
| font_size 12 → 13 → no-op → no-op | semantic 13; `/PRF1 13 Tf`; physical style 13.0; MuPDF trace size 13; bodies, operator counts and pixels identical through no-ops; text edit afterwards keeps the authority |
| horizontal_scale 1 → 4/5 | `80 Tz`; B at x 31.52 (= 20 + 2 × 7.2 × 4/5) |
| rise 0 → −1 | `1 Ts`; physical baseline_shift −1; every origin 1 pt up; empty metrics 41/5 / 7/5; inside the region |
| tracking 0 → 1/4 | `0.25 Tc`; origins 20, 27.45, 34.9 |
| combined 37/3, 4/5, −1, 1/4 | `12.333333 Tf 80 Tz 0.3125 Tc 0 Tw 1 Ts`; diff exactly the four fields; stable through two no-ops |
| font A → B → no-op → no-op | payload SHA B; authority provider B; generated record provider B, `+SemanticAlternate` subset; placement uses B's 500-unit advances (20, 26, 32); source registry still A; only `/PRF1` re-targeted, other resources unchanged |
| font A → B → A | new explicit transition: provider A, still `caller-confirmed-current-semantic`; body, subset and pixels equal the A base |
| style + Tw / style + edge | `word_spacing 6/5` kept under 13 pt + 1/4 tracking (B at 37.3); edge record kept under scale/rise, no-op stable |
| empty | font B, then 13 pt on the `[] TJ` witness; empty metrics follow; no-op stable; regrow paints 13 pt B |
| fresh process | size, combined, font, rollback, empty, edge+style reopen identically |
| raster | MuPDF 144 dpi: each style/font change differs from base, no-ops pixel-identical. Poppler 24.02 (`pdftoppm`, 144 dpi): no-ops pixel-identical, changes differ, rollback equals base |

All-space and newline-only text paint nothing and are refused by the existing persistent binding (`bind_empty`, PR
#42). The refusal is unchanged on main, with or without a style/font transition, the reason is identical, and no
candidate is left; current authority (plan `next_authority`) is still carried. This is an L2 scope limit, not B-L2-S.

**Negative evidence (all refuse):** source evidence tampering after transitions (registry attribute, observations,
both, registry provider rewritten to B, provider relation, `created_from`, `source_model_sha256`); current semantic
tampering (size 14, unrequested tracking, provenance flipped to source-confirmed, unknown provenance, missing
`current`, version downgrade, provider path pointing at A, payload + provider claiming A over a B island, a sidecar
claiming 13 pt over the unchanged 12 pt island without a request); physical tampering with owner witness re-sealed
(Tf, Tc, Ts, Tz, Tf back to 12, glyph code); co-binding (stale semantic from another revision, rollback semantic, old
`generated_fonts`, old subset SHA in the binding, stale semantic with forged current binding); provider record
tampering (identity, missing record, other slot); current asset bytes replaced; writer drift (serializer adds
tracking, adapter adds tracking, subset from another asset); version 1 records sealed with the version 2 rise sign;
the version 1 → 2 upgrade of a nonzero legacy rise, stored or converted; authority drift in `require_authorized_authority`; an
asset with a non-font request, a font SHA without its asset, an asset that cannot map `B`; initial confirmation with
any style or font other than the source-confirmed values. Transaction font ownership is unchanged and separate from
source observations: a font change re-targets only the slot's own releasable alias through `reserve_font_alias(owner=
slot)` with the registered `own_fonts` records; source/shared/inherited/foreign aliases are never re-targeted, a
reservation needs ownership evidence and released glyphs, and a tampered record is not ownership
(`tests/test_generated_fonts.py`, unchanged).

**Tests changed.** The two PR #42 tests that asserted the B-L2-S refusal now assert it for a version 1 record (where it
still applies); no other existing test changed.

**Review follow-up (rise sign and version 1).** The first PR #43 commit applied the version 2 equality rule to
version 1 records too, which contradicted "version 1 records open under their original rules". Fixed as described in
the rise sign contract above, with three tests on a source with Ts 1 (`tests/test_semantic_authority.py`, `risen`
fixture): the version 1 nonzero-shift bundle reopens; version 2 source-confirmed rise −1 holds and saves physically;
the nonzero legacy rise is not upgraded silently. Two of them fail on `a994d7d` and pass now.

**Validation** (after the review follow-up, `ca8a6cd`). `tests/test_semantic_authority.py`: 70 passed; L1 + L2 +
authority + generated fonts + shared flow: 187 passed. Full suite (whole `tests/` tree, pytest-xdist 4 workers,
`--dist loadfile`, Python 3.12.3 on Linux): **1,505 passed, 21 skipped, 0 failed** (first commit: 1,502 passed). Skips
are environment-only: Windows Arial / Noto Sans JP not present (11), AES provider unavailable (2), external corpus not
downloaded (5), Poppler at the Windows runtime path required by `test_source_ctm_compensation` (3; the system
`pdftoppm` used above is a different binary). External Windows/LibreOffice validation was not run: no prepared
single-owned-slot target exists and the blocker was an authority/schema problem. Runtime digest (SHA-256 over sorted
`pdfeditor/*.py` name + NUL + bytes): `45e6913da2b5cb4da385aee3f2de71c0ade6b0a46dc977994edceaf1fd32fd50` (PR #42:
`d9e69d51…`).

**Verdict: B-L2-S CLOSED. L2 COMPLETE.** Style values and the font asset are written to actual candidates and pass a
fresh reopen, no-ops are stable, source creation evidence is retained and still validated, current semantic
authority is explicit, current physical binding is verified, unauthorized drift refuses, and nothing is published.

**Remaining L3 scope.** Verified candidate PDF + verified shared-flow-3 sidecar → private staging → one complete new
bundle directory → one rename → public reopen, with the PR #40 failure matrix and no latest/current pointer. Not
implemented here. Paint runtime remains NOT READY.

## 22. L3 runtime: atomic semantic bundle publication — 2026-10-05

Starting main `14f651579fdb1a3c4f09c7d0a923f014adb32717` (PR #43 merged: B-L2-S CLOSED, L2 COMPLETE). §19 (design)
and §21 (L2) are preserved. L3 implements the §19.7 selected contract for the L2 candidate pair. It is a publication
**boundary**, not a new authority: it does not re-seal, rebind or reinterpret anything, and it does not re-implement
semantic verification. Paint runtime remains NOT READY.

### 22.1 Runtime surface

`pdfeditor/semantic_publication.py`: `publish_semantic_bundle(pdf, sidecar, destination)` returns `{directory, pdf,
sidecar, sha256, verification}`. `verification` is the public reopen evidence: `status` (always `restored` on
return), `slot_id`, `version`, `semantic`, `authority`, `owner`, `island`. The bundle has two fixed children,
`document.pdf` and `shared-flow.json` (the L2 candidate names). The directory is a container, not a third artifact:
there is no manifest, receipt, index, history or latest pointer. The caller chooses the destination name; nothing
else names revisions. No L1/L2 module changed.

### 22.2 Publication contract

```
destination: new name, parent is an existing non-symlink directory, nothing at the name (lexists)
read candidate PDF + sidecar bytes once
staging = mkdtemp(prefix=".pdfengine-staging-", dir=parent)          same parent ⇒ same filesystem
write document.pdf, shared-flow.json  (open "xb", write, flush, fsync, close); fsync(staging dir)*
open_semantic_flow(staging/document.pdf, staging/shared-flow.json) == restored
staged bytes still equal the read bytes (SHA-256); destination still absent
os.rename(staging, destination)                                      the single publication step
open_semantic_flow(destination/document.pdf, destination/shared-flow.json) == restored   right after the rename
published bytes equal the verified staged bytes
fsync(parent)*                                                       durability, after verification
→ success
(* where the platform can open directories; skipped on Windows)
```

- **Trust boundary (TOCTOU).** The bytes that are verified are the staged bytes, and the same directory is renamed.
  A change to the candidate file after it was read cannot reach the bundle. A change to the staging files after
  verification refuses.
- **Verification is delegated.** Staged and public checks are both the unchanged `open_semantic_flow` (integrity,
  scope, creation evidence through the v2 validator, owner witness, semantic/current authority, canonical island,
  generated-font subset/provider binding, provider asset SHA). Each call is its own outermost proof session, so the
  public reopen shares no cached evidence with the staging check.
- **No rewrite.** Published bytes equal staged bytes, which equal the candidate bytes. `pdf_sha256`, the owner
  witness, the authority, the source registry, provider paths, generated-font records and the semantic binding are
  untouched. The L2 sidecar holds no candidate-directory path (only the provider asset path), so the pair is
  relocatable as-is. Assets are not packaged: a missing or changed provider asset is refused by the existing
  validator.
- **Immutable destination.** An existing file, an empty or non-empty directory, a previously published bundle, or a
  destination that appears during publication is refused; nothing at that name is replaced, moved or deleted.
  Re-publishing the same bundle to its own name refuses. Names starting with the staging/withdrawn prefixes are
  refused.
- **Failure before the rename** (PDF write, sidecar write after the PDF, directory sync, verification, tampering,
  stale owner, authority/semantic mismatch, generated-font binding mismatch, collision, the rename itself, any other
  exception): the public namespace is unchanged and the staging directory is removed. If that cleanup fails, the
  original exception is raised with a note naming the private leftover; the leftover only ever has the staging
  prefix.
- **Failure after the rename.** The public reopen is part of success. If it is not `restored`, raises, or the
  published bytes differ, the bundle is **withdrawn by one rename** to `.pdfengine-withdrawn-<name>-<random>` in the
  same parent, kept intact (not deleted) for inspection, and `PdfError` names that path. If the withdrawal rename
  also fails, `PdfError` states that the public path is **not a verified bundle**. Success is never returned for a
  bundle that did not reopen. This narrows §19.6 S9 ("a late reporting error leaves the complete new pair"): a
  failed public reopen is not a reporting error but a bundle that does not verify, so it does not stay public. Since
  the bytes are identical to the verified staging pair, such a failure means the environment changed between the
  two checks (for example the provider asset). Readers that open it during that window get the same refusal from
  `open_semantic_flow`.
- **Every state after the rename is named** (review follow-up on PR #44). The public reopen runs immediately after
  the rename; the parent directory sync runs only afterwards, so semantic verification and directory durability are
  separate outcomes:

  | After `os.rename(staging, destination)` | Outcome |
  |---|---|
  | reopen `restored`, bytes equal, parent sync OK | success, result returned |
  | reopen `restored`, bytes equal, parent sync fails | `PublishedSyncError` (a `PdfError`): the **verified, complete bundle is public** at the destination; `.result` is the full publication result; durability is not confirmed. It is not withdrawn. |
  | reopen fails or bytes differ; withdrawal rename OK; sync OK | `PdfError` "withdrawn to `<quarantine>`" |
  | reopen fails; withdrawal rename OK; sync fails | `PdfError` "withdrawn to `<quarantine>`; the directory sync after the withdrawal failed" (the quarantine path is still reported, the destination is free) |
  | reopen fails; withdrawal rename fails | `PdfError` "`<destination>` is NOT verified and could not be withdrawn" |

  Power-loss durability stays outside the guarantee; a sync failure never turns a verified bundle into an
  unverified one, and an unverified bundle is never reported as success.

**Scope (unchanged from §19.7).** This is exception atomicity of the public pair on one local filesystem with an
exclusive publisher. It is not concurrent-publisher locking, power-loss/OS-crash durability or secure deletion. One
POSIX caveat is recorded: `rename(2)` replaces an *empty* directory that another process creates in the instant after
the final existence check; the exclusive-publisher assumption excludes that race (Windows `os.rename` refuses any
existing destination). Python's standard library has no no-replace rename. Windows behaviour was not executed here;
the operations used (`mkdtemp`, `open('xb')`, `fsync` on files, `os.rename` of a closed directory, `rmtree`) are
the ones §19.7 selected for Windows, and directory fsync is skipped there.

### 22.3 Evidence (`tests/test_semantic_publication.py`, 41 tests)

| Area | Result |
|---|---|
| Happy path | L2 candidates (save, 12→13 pt, font A→B) publish. Exactly **one** `os.rename` runs, from `.pdfengine-staging-*` in the same parent to the destination. `open_semantic_flow` runs on the staged pair, then on the public path, never on the candidate path. The final directory holds exactly `document.pdf` and `shared-flow.json`, and the result reports `restored`. |
| Bytes | Staged bytes (captured at the rename) = public bytes = candidate bytes for the PDF and the sidecar. The sidecar is JSON-identical, the candidate is unchanged, and the provider path is unchanged. |
| Fresh process | Published save, size and font bundles reopen `restored` and canonical in a new Python process, with the same payload and authority. |
| Collision | An existing bundle, empty directory or file at the destination is refused, and every byte under the parent is unchanged. Re-publishing a bundle to its own name is refused. A destination created after verification is refused. Reserved and invalid names are refused, and a missing parent is refused. |
| Staged verification (before the rename) | Each of these is refused: PDF bytes tampered; PDF tampered with every SHA in the sidecar resealed; sidecar edited without a reseal; stale owner witness; authority flipped to source-confirmed; unrequested tracking; generated-font binding subset; generated-font record provider; source registry attribute; missing provider asset. Tampering inside staging is refused. A staging change after verification is refused. A candidate changed after it was read does not reach the bundle. In every case the parent stays empty. |
| Failure injection before the rename | Failures injected at the PDF write, the sidecar write (after the PDF), the directory sync, verification and the rename itself all leave the parent equal to its previous contents, with an existing bundle intact and no staging left. A cleanup failure keeps the original exception and adds a note, and only a staging-prefixed private leftover remains. |
| After the rename | A failed public reopen withdraws the bundle by one rename into quarantine (both files kept) and raises an error naming the quarantine path. The public path is confirmed to be the one reopened. If the withdrawal also fails, an explicit "NOT verified" error is raised. Public bytes changed right after the rename are withdrawn, not reported as success. |
| Directory sync after a rename (review follow-up) | When the parent sync fails after the publish rename, the public reopen has already run, so the result is `PublishedSyncError`. It carries the `restored` result, and the bundle stays public, complete, byte-identical and reopenable. When the withdrawal rename succeeds but its sync fails, the error still names the quarantine, the destination is free, and both files are kept. On the reviewed head `ea7800d`, both cases escaped as a raw `OSError`, before the public reopen and without the quarantine path respectively. |

Mutation check: disabling the staged-verification test fails 12 tests, and skipping the public reopen check fails 2.

**Validation** (after the review follow-up, `8bb8245`). Full suite (whole `tests/` tree, pytest-xdist 4 workers,
`--dist loadfile`, Python 3.12.3 on Linux): **1,546 passed, 21 skipped, 0 failed** (PR #43: 1,505 + 41 new; first
L3 commit: 1,544). The skips are the same 21 environment-only skips (Windows Arial / Noto Sans JP 11, AES provider 2,
external corpus 5, Windows-path Poppler 3); none is new. Windows and external renderer validation were not run.
Runtime digest (SHA-256 over sorted `pdfeditor/*.py` name + NUL + bytes): `c6252fa73f5e37e8de61fa36b990e04c67d6d72b039b488a988e356488c0e3d4` (PR #43: `45e6913d…`; the only
runtime change is the new `semantic_publication.py`).

### 22.4 L3 verdict

**L3 COMPLETE.** The full path runs on real files:

1. an L2 verified candidate is read once;
2. it is placed in a private staging directory in the destination parent;
3. the staged pair passes a fresh `open_semantic_flow`;
4. the complete directory is published by one rename;
5. the public bundle passes a fresh reopen from its public path, in this process and in a new process.

There is no partial public pair on any tested failure, no overwrite, and no sidecar rewrite. L1/L2 validators and
writers are unchanged.

Out of scope and not started: paint runtime (NOT READY), multi-paragraph/multi-slot, region contracts, asset
packaging/archives, revision databases, undo/autosave, concurrency, network or cloud publication.

## 23. L1–L3 integrated lifecycle validation — 2026-10-05

Starting main `5e83d0f6703db14b7e74f0dc832c13877289f182` (PR #44 merged: L3 COMPLETE). §20–§22 are preserved.
Objective: run L1 (state/verifier), L2 (canonical writer + current style/font authority) and L3 (publication) as one
runtime lifecycle, using **published bundles as the next revision's only input**, and fix only what reproduces.
Scope is unchanged (one paragraph, one owned slot, one region, one body style, static TT, A/B/space/newline, ≤256
characters, left, default context, semantic record version 2, local bundles). No new API, authority, session
manager or history store; only `confirm_semantic_layout`, `open_semantic_flow`, `plan_semantic_transition`,
`build_semantic_candidate` and `publish_semantic_bundle` are used. Paint runtime remains NOT READY.

### 23.1 Lifecycle (`tests/test_semantic_lifecycle.py`)

```
rev1 (v2) ─confirm→ "A B" 12pt A ─edit→ "AA B" ─E→ 13pt, tracking 1/4 ─E→ font B ─E→ Tw 6/5 ─save→ r5
r5 ─publish→ bundle-A ─(bundle-A/document.pdf + shared-flow.json as input)→ save (no-op) │ edit "AA B A"
   ─E→ Tw 0 + edges on both spaces ─E→ font A ─save→ r9 ─publish→ bundle-B ─fresh process reopen
```

| Check | Result |
|---|---|
| Current state per revision | Every revision (12 incl. both bundles) reopens `restored` with the exact text, size, tracking, Tw, edges, font SHA, provider and provenance expected for it; all candidates are canonical. |
| Published bundle as next source | `build_semantic_candidate(bundle-A/document.pdf, bundle-A/shared-flow.json, …)` produces the next revisions; no staging copy or candidate path is used. A copy of bundle A in another directory opens and produces the same next body (path independence: `pdf_sha256`, owner witness, generated fonts, binding, registry, provider and island are path-free). |
| Restart simulation / fresh processes | Bundle A and bundle B reopen in a new process with the same payload, authority and owner. A new process opens bundle A, plans the edit (class D, exact diff) and builds the candidate from disk only; its body and semantics equal the in-process revision. |
| Immutability | Bundle A is byte-identical after five candidates and bundle B were built from it, after every failure test and after tampering copies. Destinations are distinct (`bundle-A`, `bundle-B`). |
| Byte identity | Candidate PDF/sidecar = published PDF/sidecar for r5→A and r9→B; the publication SHA-256 equals the candidate's. |
| No-op across publication | Owned body r4 = r5 (no-op) = bundle A = a no-op candidate from bundle A, same operator count; r8 = r9 = bundle B. |
| Owner continuity | `marker_id` and `created_from` are identical in every revision; at every revision the stored `current` equals the witness recomputed from that revision's PDF, `pdf_sha256` equals the file SHA and the semantic binding names the same block; each semantic change has a new witness. |
| Authority continuity | `source-confirmed` until the first explicit style change; `caller-confirmed-current-semantic` from then on, through a later text edit, no-ops and two publications (13 pt / 1/4 never return to 12 pt / 0). |
| Creation evidence | Registry (attributes, observations, provider A, relation), `source_model_sha256`, `source_snapshot_sha256`, `created_from` and `contract_sha256` equal the original v2 values in all revisions. |
| Font | A → B (bundle A, later edit) → A (bundle B): current provider and the single generated record (`/PRF1`) follow; the registry stays A. |
| Tw / edge | Tw 6/5 published in A; Tw→edges (both spaces) gives the same owned body and pixels with distinct semantic records; edges published in B. |
| Provider asset | A bundle whose current provider asset is removed refuses to open or edit; restoring the same bytes at the recorded path restores it. No packaging. |

### 23.2 Multi-revision negative evidence and failure isolation

- Mixed pairs `A.pdf + B.json` and `B.pdf + A.json` refuse in `open_semantic_flow`, `build_semantic_candidate` and
  `publish_semantic_bundle` (no destination created).
- Bundle B resealed with bundle A's `source_output.current`, semantic record, semantic record with B's binding forged,
  current authority only, `generated_fonts` or slot binding: all refuse.
- Creation evidence tampered after the twelfth revision (attribute, provider SHA, observations, `created_from`,
  `source_model_sha256`, `source_snapshot_sha256`): all refuse.
- A copy of published bundle A with appended PDF bytes, an unsealed sidecar edit or a resealed semantic edit cannot be
  planned or edited; publication is not trust.
- Injected failures while building from bundle A (plan, serializer, Transaction commit, owner rebind, semantic bind,
  candidate reopen): bundle A byte-identical, no candidate left.
- Publication of B failing at staged verification or at the rename: no destination, bundles A and B byte-identical.
- Public-reopen failure: the destination does not exist, the quarantine is not adopted (building from the
  destination path fails; the quarantine name is refused as a destination).
- `PublishedSyncError`: `.result` names a public, verified bundle byte-identical to B; it is used as the next source
  and edits successfully.

### 23.3 Boundedness

A 15-step mixed lifecycle from bundle B (save, edit, save, scale, save, Tw, save, edge, save, font B, save, font A,
three saves) reopens every step canonical with the same marker, caller-confirmed authority, the version 2 key set and
unchanged creation evidence, then publishes and reopens in a fresh process. The trailing no-ops have identical PDF
size, sidecar size, owned-body size and operator count and an identical sidecar structure; the font change before
them has the same body and operator count. There is always exactly one generated font record; the sidecar's
top-level keys never grow and `previous_model_sha256` is one digest, not a list. Sizes follow the text, not the
revision count (an exploratory 16-step chain kept the PDF at 4.0–4.1 KB, 17 xref objects and three page fonts).

### 23.4 Runtime bug found and fixed (one)

The long lifecycle reproduced a fail-closed liveness bug in the L2 writer. A rewrite planned its Transaction pixel
area as `resolved.bbox ∪ new ink`, where `resolved.bbox` is MuPDF's renderer box of the **old** glyphs. That box
uses normalized ascender/descender (0.75/0.25 em) and, under `Tz`, a horizontally scaled size: for font B at 13 pt
with `80 Tz` it ended at y 192.2 while font B's outline (700/1000 em, above its 600 ascent) reaches y 190.9.
Removing it changed pixels outside the planned area, so `Transaction` refused a legitimate transition (minimal
reproduction: font B → 13 pt + scale 4/5 → font A; the same switch at scale 1 passed only by antialiasing margin).
Fix (`semantic_writer.py`, nothing else): the planned area also covers the **exact ink of the island being replaced**,
derived from the verified current semantic state and its current asset (the same outline formula as the new ink,
now one helper `_ink_rects`). The pixel check, foreign-glyph/paint/font checks and obstacle checks are unchanged;
only removed ink is added to the planned area. Regression test:
`test_removed_old_ink_beyond_the_renderer_box_is_inside_the_planned_area` (fails before, passes after).

### 23.5 Raster

MuPDF 144 dpi: no-op and publication identical (r4 = r5 = bundle A; r8 = r9 = bundle B); Tw → edge identical; every
semantic change differs. Poppler 24.02 (`pdftoppm`, 144 dpi): r4 = r5 = bundle A, r9 = bundle B, A ≠ B.

### 23.6 Windows (static) and external validation

Publication code was reviewed for Windows paths: directory fsync is skipped on `nt`; staged files use `open('xb')`
and `os.fsync`; an existing destination makes `os.rename` raise before publication (cleanup path). On Linux, no file
handle into staging is open at the rename and none into the bundle afterwards (checked through `/proc/self/fd` after
`open_semantic_flow`), which is the precondition for a Windows directory rename. This is not a Windows execution;
Windows behaviour is not claimed as verified. External Windows/LibreOffice validation was not run (no prepared
single-owned-slot target; not required for this lifecycle).

### 23.7 Validation and verdict

`tests/test_semantic_lifecycle.py`: 47 passed. Focused suites (lifecycle, semantic layout, writer, authority,
publication, generated fonts, source ownership, shared flow): 329 passed. Full suite (whole `tests/` tree,
pytest-xdist 4 workers, `--dist loadfile`, Python 3.12.3 on Linux): **1,593 passed, 21 skipped, 0 failed**
(PR #44: 1,546 + 47 new). The skips are the same 21 environment-only skips (Windows Arial / Noto Sans JP 11, AES
provider 2, external corpus 5, Windows-path Poppler 3); none is new, no existing test changed. Runtime digest
(SHA-256 over sorted `pdfeditor/*.py` name + NUL + bytes): `dd4fff7b3e70fb65804fc7a82fdd1f2253bca8b52d47079ec265df4395aeb84e` (PR #44: `c6252fa7…`; the only runtime change is
the §23.4 writer fix).

**Verdict: NARROW SEMANTIC LIFECYCLE VALIDATED.** Published bundles serve as the next revision's input across
revisions without breaking semantic authority, physical ownership, creation evidence or the publication boundary,
within the stated narrow scope. This does **not** mean general PDF editing, multi-slot/multi-paragraph support or
paint runtime readiness. Paint runtime remains NOT READY. Remaining scope (not started): external Windows/LibreOffice
validation of a prepared single-owned-slot target, any scope expansion, paint runtime.

## 24. Windows external semantic lifecycle validation preparation — 2026-10-05

Starting main `a68e2f3167696470af59e9466c071a5a11385913` (PR #45 merged: NARROW SEMANTIC LIFECYCLE VALIDATED; Windows
static review only). §20–§23 are preserved.

> **Windows execution has not been performed by this PR. LibreOffice execution has not been performed by this PR.**
> Synthetic cloud runs test the harness only; none of them is Windows or LibreOffice evidence.

### 24.1 Purpose and claims

This PR prepares, from the cloud side, everything needed so that one Windows session can produce reproducible
evidence for the §20–§23 lifecycle on an external target.

**What the cloud preparation shows:**
- the harness's CLI, stage sequencing, `result.json`/`report.md` generation, artifact layout, failure/refusal
  recording, input preservation and exit codes (17 tests);
- the expected outcome for a LibreOffice-shaped page, rehearsed on a synthetic page with the same structure.

**What it does not show:** any Windows filesystem, rename, font or renderer behaviour; any LibreOffice-generated PDF.
The container's LibreOffice is `libreoffice-core` only (no Writer), so no LibreOffice PDF was produced here.

No runtime code changed. The harness only calls `confirm_semantic_layout`, `open_semantic_flow`,
`plan_semantic_transition`, `build_semantic_candidate` and `publish_semantic_bundle`. For `prepare`, it also calls
`inspect_paragraph`, `confirm_story`, `confirm_shared_flow` and `edit_shared_flow`.

### 24.2 Harness and inputs

`evaluations/semantic_lifecycle/windows_validation.py`: the subcommands, preparation spec, statuses, exit codes and
`result.json` schema are documented in [its README](../evaluations/semantic_lifecycle/README.md). The subcommands:

| Subcommand | What it does |
|---|---|
| `prepare-synthetic` | Writes an in-scope control source plus spec. `--libreoffice-shape` instead wraps the text in LibreOffice's `0.1 w q … re W* n q BT … ET Q Q` page group. |
| `prepare-fonts` | Writes deterministic synthetic fonts, or unhinted A/B/space subsets of installed fonts. Hinting is removed with fontTools; the result is checked by the runtime's own `require_static_tt` and `nominal_glyph`. Derived fonts stay local. |
| `prepare` | Builds a shared-flow v2 owner sidecar for exactly one source slot from an explicit, caller-confirmed spec (page, glyph IDs, region, layout, empty metrics, initial text). Nothing is inferred. |
| `run` | Runs the lifecycle (see 24.3). |

Target requirements:
- one paragraph, one owned source slot, one region, one body style, no continuation;
- static unhinted simple TrueType;
- text limited to A/B/space/newline;
- an identity, unclipped, opaque, default-black entry context;
- a valid current owner.

The harness checks none of this itself: the runtime refuses a target that does not meet it, and the harness records
that refusal as `UNSUPPORTED_TARGET`. It never adapts a target.

### 24.3 Lifecycle stages and evidence

The stages and their prerequisites:

```
preflight → baseline → confirm → edit → style → font → noop → publish_a → edit_from_bundle_a (bundle A as input)
  → font_back → publish_b → negatives (A.pdf+B.json, B.pdf+A.json must refuse) → continuity → raster_mupdf
  → raster_poppler (optional) → inputs_preserved (always)
```

Every revision records:
- **owner:** marker, created_from digest, program and block SHA, range, PDF SHA;
- **semantic state:** text, font and provider SHA, size, tracking, rise, scale, Tw, edges, provenance, canonical;
- **owned body:** SHA, size and operator count.

The stages check:
- **noop:** the body, size and operator count are unchanged.
- **Publications:** exactly two artifacts, candidate bytes equal published bytes, and a fresh-process reopen.
- **publish_b:** bundle A is unchanged.
- **continuity:** creation-evidence digests (registry, observations, provider identity, source model and snapshot,
  created_from, contract) are equal at baseline and final, and the owner marker and created_from are constant.
- **Raster:** MuPDF and Poppler at 144 dpi, no-op and candidate-vs-published comparisons, five PNGs.
- **inputs_preserved:** input SHA-256 values before and after the run.

`result.json` is written after every stage, so a FAIL still leaves the partial evidence, `report.md` and the log.

### 24.4 Synthetic results in the cloud (harness only)

| Run (Linux, not Windows) | Verdict | Exit | Notes |
|---|---|---|---|
| in-scope synthetic control | `PASS` | 0 | All 16 stages pass, including Poppler 24.02, fresh-process reopen of both bundles, and mixed-pair refusals. |
| LibreOffice page shape (`--libreoffice-shape`) | `UNSUPPORTED_TARGET` | 3 | `prepare` (v2 ownership) succeeds; `confirm` is refused with "semantic layout needs an identity, unclipped, default black opaque entry context"; later stages are SKIPPED; inputs are preserved. |
| hinted asset | refused by `check_font` | — | The dehinted derivative passes. |

Expected Windows outcome for an **unmodified LibreOffice PDF**: `UNSUPPORTED_TARGET` at `confirm`. That is a correct
refusal of an out-of-scope entry context (LibreOffice's page-wide clip group and `0.1 w`), not a failure. The harness
does not and must not adapt the PDF. A `PASS` on such a PDF would mean its structure differs from the recorded
LibreOffice page shape, and must be investigated before it is recorded.

### 24.5 Windows execution procedure (next evidence PR)

Use a clean checkout of this PR's merge commit, then set up the environment as in the README (Python 3.12, lockfile).

1. **In-scope control on Windows.** This exercises the Windows publication path: `os.rename`, no directory fsync, and
   the Windows fonts.
   - Run `prepare-fonts --from-a C:\Windows\Fonts\arial.ttf --from-b C:\Windows\Fonts\times.ttf`.
   - Then `prepare-synthetic` → `prepare` → `run`, with the output directory at
     `evaluations\semantic_lifecycle\runs\windows-synthetic-<date>`.
   - Expected: `PASS`, exit 0.
2. **LibreOffice original.**
   - In Writer, make a new document whose first line is `XY` in Arial 12 pt, left-aligned, with no other content.
     Export it as PDF (default options).
   - Run `python -m pdfeditor observe lo.pdf --page 1 --json catalog.json` and read the glyph IDs and baseline of the
     `XY` line. Write `spec.json` with a region that contains the line and stays clear of other content.
   - Then `prepare` (font A from step 1) → `run`, with the output directory at
     `evaluations\semantic_lifecycle\runs\windows-libreoffice-<date>`.
   - Expected: `UNSUPPORTED_TARGET` at `confirm`, exit 3. Record the actual outcome whatever it is.
3. **Commit for the evidence PR:** `result.json` and `report.md` of both runs. Fonts, PDFs, PNGs and logs stay local;
   their hashes are already in `result.json`.
   - Do not rerun a run into the same directory.
   - Do not edit `result.json` by hand.
   - Report `FAIL`s as they are.

The earlier Windows original (`lo_migration_ja.pdf`, CJK, msmincho.ttc) is not reused here: its text and its hinted
TTC face are outside the semantic scope.

### 24.6 Validation

- `tests/test_semantic_windows_harness.py`: 17 passed. The tests cover the PASS lifecycle, the result/report schema,
  layout, input preservation, the LibreOffice-shape refusal, stage failure with partial results, a non-`PdfError`
  failure, INCOMPLETE, a missing input, a missing provider asset, invalid targets, output collision, an
  already-confirmed input, a plan refusal, font determinism and dehinting, and the CLI in a fresh process.
- Full-suite numbers are recorded in §24.7.
- Runtime digest unchanged from PR #45: `dd4fff7b3e70fb65804fc7a82fdd1f2253bca8b52d47079ec265df4395aeb84e`.

### 24.7 Full suite and readiness

**Full suite** (whole `tests/` tree, pytest-xdist 4 workers, `--dist loadfile`, Python 3.12.3 on Linux):
**1,610 passed, 21 skipped, 0 failed** (PR #45 had 1,593; this PR adds 17). The 21 skips are the same
environment-only ones as before, and none is new:

| Skip reason | Count |
|---|---|
| Windows Arial / Noto Sans JP absent | 11 |
| AES provider unavailable | 2 |
| external corpus not downloaded | 5 |
| Windows-path Poppler | 3 |

No GitHub CI was added, and no Windows runner was added.

**WINDOWS EXTERNAL VALIDATION READY:**
- the harness exists, and its synthetic tests pass;
- the inputs, the commands, the result schema, report generation, failure recording and input preservation are
  documented;
- no Windows PASS is claimed;
- the full suite passes.

Windows execution status: **NOT RUN**. LibreOffice execution status: **NOT RUN**. Paint runtime remains NOT READY.


## 25. Windows semantic lifecycle external evidence — 2026-10-05

This section records the first Windows execution of the §24 harness. §20–§24 are kept as historical records. There
are no runtime, harness, scope or contract changes. The only code change is a Windows-portability fix in the harness's
own test file (§25.4).

### 25.1 Environment

| Item | Value |
|---|---|
| OS | Microsoft Windows 11 Home 10.0.26200 (AMD64), locale code page cp932 |
| Python / PyMuPDF / fontTools / uharfbuzz (HarfBuzz) / pypdf | 3.12.14 / 1.27.2.3 / 4.64.0 / 0.55.0 (14.2.1) / 6.10.0 |
| Poppler | `pdftoppm` 26.07.0, already present on the machine; put on `PATH` for the session only, nothing installed |
| LibreOffice | **not installed** (no `soffice` on `PATH` or in the standard install folders) |
| Starting main | `ce81efd8b84e2e5dd161c83e62f247959ef31ab8` (PR #46 merged) |
| Evidence commit | `452ba772526424b5f7365c1efea88b582337cf20` (main + the harness-test fix), `dirty: false` |
| Runtime digest | `dd4fff7b3e70fb65804fc7a82fdd1f2253bca8b52d47079ec265df4395aeb84e`, the same as #46 |

**Fonts** are local unhinted A/B/space subsets made by `prepare-fonts` (not committed):

| Font | Derived from | Subset SHA-256 | Source SHA-256 |
|---|---|---|---|
| A | `C:\Windows\Fonts\arial.ttf` | `ab1a678f8f5ddf565ac313e203f7fde276cc75a10cee7d68b223025eaf8317f2` | `b3658eadae55e682b5f69eb64c439c1ecc8f196c0bb8d4756d145d13bc86476a` |
| B | `C:\Windows\Fonts\times.ttf` | `bf5783fb6fded1696e9350a1cd7a611cf4398c941cc17819a906ce8c7a30c9bc` | `931c5de5c70401d9324d5014c123802b4fb753000360ceb2f56c589403cd58c5` |

The subsets were byte-identical across two independent runs.

### 25.2 Windows synthetic control — PASS

Run `evaluations/semantic_lifecycle/runs/windows-synthetic-20261005/`: `windows_execution: true`, **verdict `PASS`,
exit 0**.

All 15 required stages passed:

- preflight, baseline, confirm, edit, style, font, noop;
- publish_a, edit_from_bundle_a, font_back, publish_b;
- negatives, continuity, raster_mupdf, inputs_preserved.

`raster_poppler` (optional) also passed.

- **Publication on Windows:** private staging → staged verification → `os.rename` of the directory → public reopen.
  Bundles A and B each hold exactly `document.pdf` + `shared-flow.json`, candidate and published bytes are identical,
  and a fresh process reopens each as `restored`. Bundle A was unchanged after bundle B was published. Windows skips
  the directory fsync by design (§22), and the run does not count that as a failure. Refusal of an existing
  destination is covered on Windows by `test_semantic_publication.py` (existing destination, republish, a destination
  appearing after verification, and name rules) in the full suite below.
- **Published bundle as the next revision:** `edit_from_bundle_a` edited bundle A's PDF + sidecar
  (`AA B` → `BAA B`) and then published bundle B.
- **Fonts:** A → B → A (`font`, `font_back` diffs on `font.sha`).
- **Semantic provenance:** `source-confirmed` until the style change, then `caller-confirmed-current-semantic`.
- **Negatives:** the mixed pairs A.pdf + B.json and B.pdf + A.json are both refused ("shared flow PDF revision changed").
- **Continuity:** owner `marker_id` and `created_from` stay constant, and the creation-evidence digests stay
  constant.
- **No-op:** the owned body is byte-stable (18 operators).
- **Raster:** MuPDF and Poppler both show identical no-op rasters and identical candidate/published rasters, and the
  semantic change differs from the baseline.
- **Inputs:** all input SHA-256 values are unchanged.

The first attempt, before the test fix, ran the same harness code and also gave `PASS`, exit 0. Its output was not
committed. After the fix the run was repeated from scratch (fonts, synthetic source, preparation, run), as §24
requires.

### 25.3 LibreOffice original — NOT PERFORMED

LibreOffice is not installed on this machine. Installing it was not part of this run, and the owner chose to record
this validation as not performed. The only LibreOffice-produced original on the machine, `lo_migration_ja.pdf`
(Producer "LibreOffice 4.0"), was **not** used, because §24.5 excludes it (CJK text, hinted TTC face outside the
semantic scope). No LibreOffice result is claimed. In particular this is neither "safely refused" nor "validated".
The §24.5 step 2 procedure remains the next action once LibreOffice is available.

### 25.4 Bug found: harness test portability on Windows (fixed here; harness and runtime unchanged)

The first Windows run of the focused suites gave 4 failures in `tests/test_semantic_windows_harness.py`. None
was in the runtime or in the harness itself.

- **Three tests** read `report.md` / `result.json` / `harness.log` with the locale codec (cp932). The harness writes
  them as UTF-8, and the em dash in the report failed to decode.
- **The missing-provider test** replaced the raw provider path string in the sidecar text. On Windows the JSON stores
  that path with escaped backslashes, so nothing was replaced and the run passed. Its guard compared against a
  re-serialized dict, so it could not catch this.

Fix (test file only):

- read evidence as UTF-8;
- replace every provider reference in the parsed JSON, assert that the replacement happened, and reseal
  consistently (slot bindings, confirmed contract, model checksum), so that the refusal can only come from the
  missing asset. The test asserts a baseline `REFUSED` whose reason is the missing file (`[Errno 2] No such file`)
  and is not a checksum, contract or binding-seal mismatch (added after review);
- also check the JSON-escaped absolute path;
- add `test_evidence_files_are_utf8_independent_of_the_locale`.

The harness tests then gave 18 passed on Windows (17 earlier + 1 new). Runtime fix PR needed: **no**.

### 25.5 Tests on Windows

- Focused (harness, layout, writer, authority, publication, lifecycle), before the fix: 259 passed, 4 failed (above),
  2 skipped (Poppler not on `PATH`).
- Harness after the fix: 18 passed. After the review fix (consistent reseal in the missing-provider test): 18 passed
  again, and the Windows full suite was rerun with the same totals.
- Full suite (whole `tests/` tree, four file-balanced parallel shards, Poppler on `PATH`): **1,625 passed, 7 skipped, 0 failed**. The skips are environment-only: 2 AES provider unavailable and 5 external corpus not downloaded. The Linux-only skips (Windows Arial / Noto Sans JP absent, Windows-path Poppler) ran and passed here. The total, 1,632, equals Linux 1,610 + 21 skipped + the 1 new test.

### 25.6 Verdict

- **WINDOWS NARROW SEMANTIC LIFECYCLE VALIDATED.** `windows_execution` is true; the synthetic run passed; publication
  A/B passed; the published bundle was reused for the next edit; fresh reopen passed; continuity passed; the MuPDF
  raster check passed; inputs were preserved; and no unexpected runtime FAIL occurred.
- **LibreOffice original: NOT PERFORMED** (LibreOffice unavailable). No LibreOffice semantic editing claim.
- Evidence: `evaluations/semantic_lifecycle/runs/windows-synthetic-20261005/result.json` and `report.md`. PDFs, fonts,
  PNGs and logs stay local; their SHA-256 values are in `result.json`.
- Next: run §24.5 step 2 on a machine with LibreOffice Writer. Paint runtime remains NOT READY.

## 26. Paint runtime blocker reassessment after L1–L3 — 2026-10-05

Starting main `b4a45c39aa279a5442404f307a6fdbe048c2d288` (PR #47 merged 2026-10-05: WINDOWS NARROW SEMANTIC
LIFECYCLE VALIDATED; LibreOffice original NOT PERFORMED). §1–§25 are preserved. **This section is design/evidence
only:** there is no paint runtime, no `pdfeditor/` change, no schema or grammar change. The runtime digest is unchanged:
`dd4fff7b3e70fb65804fc7a82fdd1f2253bca8b52d47079ec265df4395aeb84e`.

Question: which of the blockers that stopped paint runtime in #35–#37 (§12, §14, §15.9/§15.11, §16.6) are resolved by
the L1–L3 semantic lifecycle, which remain, and is a narrow paint implementation now safe to start?

**Answer in one line:** the *layout* blockers are resolved, but only on the semantic surface. That surface has no paint
model, and it currently refuses paint. Paint runtime stays **NOT READY**. The remaining blockers are new
surface/authority decisions (§26.3), not layout drift.

### 26.1 The structural fact: the paint contract and the validated layout live on different surfaces

| | Ordinary editable (§1–§15 paint contract) | Shared-flow v3 single owned slot (L1–L3) |
|---|---|---|
| Paint model | `anchors.py` (`_candidates`, `AnchoredPaintEdit`), editable v2 `anchors.underlines` | none |
| Layout authority | `ParagraphShaper` with trace-difference advances, trace ink and float32 style reconstruction (unchanged, B1-L open) | `semantic_measure` exact nominal metrics, one provider-independent rule, exact region capacity |
| Owner | none (PR #33 keeps `_source_output` closed for ordinary) | `source_output` owner witness, rebound every revision |
| Publication | `_publish` two-path exception rollback | L3 bundle directory, one rename |
| Paint admission | anchored rewrite (legacy growth, §1) | **refused:** `shared_flow.py:217-220` (`binding.anchors` must be `None`, no `decorates` relation), `shared_flow.py:243` (`decoration_ranges == []`), `source_ownership.BODY_OPERATORS` = text operators only (`grammar()` rejects `re f`, `test_source_ownership.py::test_body_grammar_rejects_foreign_operators_and_nested_groups`), semantic payload has no decoration field |

Consequences:

- Nothing in L1–L3 changed the ordinary editable runtime. **Every §15/§16 blocker is still open there**, exactly as
  recorded.
- On the semantic surface the layout blockers are closed. There is nothing to paint, though, and source text with an
  underline cannot enter that surface at all (`test_source_ownership.py::test_text_only_scope_remains_closed`).
- A narrow paint implementation therefore has to choose a surface first. Porting L1–L3 to ordinary editable is a
  second layout implementation, so it is rejected for v1. The semantic surface is the only candidate.

### 26.2 Blocker mapping

Status is given for the semantic surface. The ordinary editable surface keeps its §15/§16 status unchanged.

| Blocker (origin) | L1–L3 element that answers it | Status |
|---|---|---|
| **B1** paint feedback loop: rendered bounds → `_candidates` → median offset/thickness → `_rect_commands` (§2, §14) | The exact plan is `_derive(payload, asset, region)`. A recipe fixed at creation never reads rendered paint. | **Resolved by construction** for derived geometry. Evidence in §26.4. |
| **B1** numeric formatter / output decimal rule (§15.2) | `semantic_island.decimal` (one way, ≤6 places, ties-to-even, no exponent, no −0) | **Resolved**, reusable |
| **B1-L** L1/L2/L3 advance authority (§15.9, §16.1) | `semantic_measure` nominal metrics; no trace, no retained/new split | **Resolved** in scope |
| **B1-L-M** trace ink fallback (§16.1) | Static simple TT outlines only; Base-14 and unknown programs refused | **Resolved** in scope |
| **B1-L-V** vertical asymmetry (§17.1) | One hhea + ink rule for all glyphs and empty lines | **Resolved** in scope |
| **B1-L-S** style ratchet (§17.1) | Exact rational payload flows one way; physical values are verification only (§17.5, §20) | **Resolved** |
| **B1-L-I** Tw / positioning intent (§17.6) | Option B: confirmed, not physically witnessed (§18, §21.3) | **Resolved** under P4, with that stated limit |
| **B1-L-C** confirmation authority (§18) | P4: trusted caller plus explicit request (§19.1) | **Resolved** |
| **B1-L-O** runtime surface / owner co-publication (§19.9) | shared-flow v3, two artifacts (§19.10, §20) | **Resolved** |
| **B-L2-S** style/font reinterpretation (§21.6) | Current semantic authority layer (§21.8) | **Resolved** |
| **B2** zero-paint / revival (§12, §15.4) | none; NARROW V1 made it a non-feature | **Still excluded.** The text surface supports empty → regrow, so paint must define termination on empty (P-EMPTY, §26.3). |
| **B3** dormant boundary / active isolation (§12, §15.4) | The island entry context is proven each revision: identity CTM, unclipped, opaque, no ExtGState, default black (§20.3 step 8) | **Partial.** Context proof exists for the text island. The paint group's own entry = exit isolation depends on P-SURF. |
| **B4** single owner per PDF / inventory (§14, §15.5) | One flow, one owned slot, whole-PDF SHA bound; the v3 opener validates the whole sidecar | **Dissolves** if paint is owned by the slot's existing owner (option A, §26.3), because no second marker inventory exists. **Open** if a separate paint marker domain is added. |
| **O1** legacy/ordinary mutation overlapping paint (§15.11 N2) | `MutationProgram` owner overlap inside one plan; any foreign save changes the whole-PDF SHA, so the semantic bundle reopens `needs_confirmation` | **Partial.** Fail-closed by staleness. An explicit pre-mutation refusal by ordinary/anchored paths over an owned island is not evidenced, so a test is needed. |
| **O2** creation recipe from source decimal path + exact S (§15.11 N3) | identity CTM only (S = identity); no source underline can exist on this surface | **Open, and its form changes.** With no source path to derive from, the recipe must be a caller-confirmed exact value (P-REC). Source adoption is a separate capability (P-ADOPT). |
| **O3** termination lexical gate (§15.11 N4) | Exact canonical re-serialization of the whole owned body is already checked (`_island` → `canonical`) | **Subsumed** under option A: termination is re-serialization without the group, and is verified byte-exact. **Open** under option B. |
| **O4** cross-page fixture (§15.11 N5) | Single slot on one page; whole-PDF SHA binding | **Open (low).** The policy holds by scope; the refusal fixture is still absent. |
| **O5** clip proof CTM vs paint S (§15.11 N6) | Unclipped, identity-CTM entry context required | **Dissolved by scope** (clips refused) |
| External original (§24.5) | Windows synthetic PASS; LibreOffice **NOT PERFORMED** | Not a paint blocker by itself. No real-world source has gone through the semantic lifecycle. |

### 26.3 Remaining and new blockers

| ID | Question that must be decided before implementation | Why it is design, not implementation |
|---|---|---|
| **P-SURF** paint ownership domain on the semantic slot | **A:** paint lives inside the slot's source-output body, after the text group, as `q (x y m x y l x y l x y l h f)+ Q`. **B:** a sibling `pdfengine-paint-v1` island (§15 grammar and marker pair) owned by the same slot record. **C:** ordinary editable (rejected, §26.1). | A puts path operators into the actual owned body, and the v2 path rejects that PDF itself, not only the sidecar. `semantic_layout._project_v2()` only removes the semantic field and restores the v2 schema; it hands the **same current PDF** to `shared_flow._validate`, which always ends in `source_ownership.validate` (`shared_flow.py:266-267`). That runs `witness` → `grammar()` on the actual body (`source_ownership.py:258`), and `grammar()` rejects `m/l/h/f/re`. Stripping paint from the projected sidecar therefore cannot make a painted PDF pass the unchanged v2 validator. A needs a **v3-only current-body grammar/witness validator** (§26.6). B keeps v2 untouched but brings back a second domain, marker inventory, B4 and the termination lexical gate (O3). This decides write authority and the verifier boundary. **Recommendation: A**, versioned: one owner, one exact re-serialization covering text and paint, no new marker domain, so B4 and O3 dissolve. |
| **P-SEM** decoration semantic authority | `payload.decorations` (range, affinities, kind `underline`, recipe) and its N/D/E/R classes. **D:** remap on edit and growth inside the range; shrink on deletion; terminate the group when its range collapses. **E:** add, remove or change the recipe. **R:** revival, reuse of an old ID, overlap, mixed kinds. Identity is current-state only (like `current:i`), with no `anchor_id` history, because option A needs no marker. | This is the same class of decision as B1-L-C/B-L2-S: which fields may change, and under which request. |
| **P-REC** recipe authority | Caller-confirmed exact rational offset/thickness: absolute pt or em-relative? The evidence (§26.4) shows an absolute offset keeps y = 58.7 after 12 → 37/3 pt. Fill: the entry context is default black, but the canonical body sets the source-confirmed registry fill inside the body (§21.3), so text can already be colored. The natural rule is **underline fill = current text fill**, emitted by the paint group itself, since the text group's `Q` restores the entry fill. A black-only v1 is acceptable only as an explicit new scope gate that also requires the text fill to be black, not as a consequence of the entry context. | It defines what a style change means for paint (E-class side effects) and what must be confirmed. |
| **P-ADOPT** source underline adoption | Shared-flow refuses source decorations, so an existing underline can neither be kept anchored nor consumed. Consuming it means mutating foreign bytes outside the owned span. | **v1 should refuse it explicitly.** v1 then means "caller-declared underline on semantic text", not "keep an existing source underline anchored". Adoption is a later, separate authority. |
| **P-EMPTY** empty text with decorations | §15.1 refused full empty for a paint-owning paragraph. On the semantic surface, empty → regrow already works for text. Under A, termination is record removal plus re-serialization, so refusal is unnecessary. | It must be stated explicitly: either terminate all groups with no dormant state, or refuse. |
| **P-VER** verifier/Transaction obligations (implementation once P-SURF is decided) | The canonical island check covers text and paint. Transaction declares island paint as owned (`paint_changes`). The planned pixel area includes the removed old rectangles (§23.4 lesson). Creation evidence still passes the v2 contract, and the current body passes the v3 grammar/witness validator (§26.6). Underline vs glyph placement accuracy ≤0.002 pt stays a separate gate. | Not a design question once A/B is chosen; listed so that it is not lost. |

The §15.8 acceptance matrix still applies where it is surface-independent: bytes KPI, fresh-process, tamper/reseal,
foreign injection, late rollback. Rows tied to ordinary editable or legacy reconfirmation become refusals on the
semantic surface: source terminal consumption, legacy growth, scale CTM and clip.

### 26.4 Evidence (`tests/test_paint_reassessment.py`, 10 tests; 11 after §26.6)

A prototype formatter **inside the test** derives one rectangle per line over the painted glyphs of a range. It trims
boundary spaces (§15.2 rule), uses the line baseline, a fixed exact recipe (`offset 13/10`, `thickness 7/10`) and
`semantic_island.decimal`. Its only inputs are the stored sidecar (`_derive` over the payload, the current asset and
the confirmed region) and the current page frame (MediaBox top, the constant the island writer reads). It reads no
rendered path, trace, earlier PDF or report. It is not an owned writer, grammar, verifier or accuracy proof.

It runs over a real L2 chain built from runtime APIs: save×3, edit `A B` → `AA B`, no-op, an explicit 37/3 pt + scale
4/5 + tracking 1/4 with two no-ops, Tw 6/5 → no-op, Tw → edge → no-op, and font A → B → no-op.

| Check | Result |
|---|---|
| No-op fixed point (6 groups × 3 ranges, including first → noop and the scaled style) | Byte-identical in every group. This is the comparison that failed in §15.2 and §16.5 (there with identity and 0.83/0.91 CTMs; this surface admits the identity CTM only, and "scaled" here is a 4/5 horizontal-scale style). |
| Semantic change | Base, growth and scaled bodies are all different. Tw 6/5 moves the end from 44.43 to 45.63; font B gives 41.683333. |
| Tw vs confirmed edge | Identical paint, as for text (§21.4) |
| Current-only | A fresh process with only the sidecar, asset and page frame (MediaBox top read from the current PDF by the parent and passed in) reproduces the bytes for three revisions |
| Read-only | Every revision still reopens `restored` and canonical |
| Code facts | `BODY_OPERATORS` has no path operators; `grammar()` rejects a paint group after the text group |

Sample (base `A B`, 12 pt): `q 20 58.7 m 41.6 58.7 l 41.6 58 l 20 58 l h f Q`. At 37/3 pt the y stays 58.7, which is
the P-REC question.

**Interpretation.** On this surface the fixed point holds *by construction*: the plan is a pure function of the
semantic payload, the asset and the region, so equal semantic state gives equal geometry. The test shows that real
stored candidates satisfy this, and that no physical observation is needed. It does not show that such paint can be
owned, verified or published. That is P-SURF/P-SEM/P-VER.

Validation: `tests/test_paint_reassessment.py` **10 passed** (Windows, Python 3.12.14); **11 passed** after the §26.6 probe. Full suite: not run (no runtime
change). The runtime digest is unchanged.

### 26.5 Verdict and next scope

- **Layout-side paint blockers** (B1 loop and formatter, B1-L L1–L3, B1-L-M, B1-L-V, B1-L-S, B1-L-I, B1-L-C, B1-L-O,
  B-L2-S): **resolved on the shared-flow v3 semantic surface** within its narrow scope. On the ordinary editable
  surface they remain open.
- **B2 excluded; B3 partial; B4/O3 dissolve under option A; O1 partial; O2 changes into P-REC; O4 open (low); O5
  dissolved by scope.**
- **New blockers:** P-SURF, P-SEM, P-REC and P-EMPTY are design decisions. P-ADOPT is a recommended explicit refusal;
  P-VER covers implementation obligations.
- **Paint runtime: NOT READY.** The cause is no longer layout drift. The paint ownership domain and the decoration
  semantics on the only surface with a validated layout are undefined. Prior reviews required this class of decision
  (B1-L-O, B-L2-S) to be closed at the design level before implementation.

**Next minimal scope (one design/evidence PR):** "semantic underline contract" on the single owned slot.

- Choose P-SURF A and design the §26.6 split: v2 creation-evidence validation reused unchanged, plus a v3-only current
  source-output body grammar/witness validator. v2 schema and behaviour are not relaxed.
- Define `payload.decorations` and its N/D/E/R classes (P-SEM).
- Fix P-REC (recommendation: em-relative or explicitly re-confirmed on a size change; underline fill = current text
  fill, or an explicit black-only scope gate on the text fill).
- State P-EMPTY (terminate) and refuse P-ADOPT.
- Add read-only probes: a candidate body with a paint group accepted by the prototype v3 body validator and refused by
  the unchanged v2 path (`source_ownership.validate` on the same PDF); creation evidence still validated by the v2
  contract; tamper/reseal of the decorations; removed-ink planned area.

Once that is reviewed, a **narrow paint implementation PR** (decorations + canonical text+paint island writer +
verifier, scope unchanged otherwise) is the next step. Do not start it before that review.

### 26.6 Review follow-up: v2 cannot validate a painted body; v3 needs its own current-body validator

The PR #48 review found that the earlier next-scope wording ("projection with paint stripped validates through the
unchanged v2 validator") contradicts the runtime. Under option A the painted body is in the actual PDF. The v2 path
inspects that PDF: `_project_v2` → `shared_flow._validate` → `source_ownership.validate` → `witness` → `grammar()`,
which rejects path operators. Only the sidecar is projected; the PDF is not. The wording is corrected above.
New evidence `test_unchanged_v2_path_rejects_a_painted_actual_body` inserts the prototype paint group after the text
group of a real owned body. `source_ownership.validate` refuses with `invalid source output body grammar`, before
any witness comparison. The unpainted body passes the same grammar.

Contract to design in the next PR (option A stays the first candidate):

- **Layer A creation evidence** (registry, source observations, `contract_sha256`, `created_from`, marker identity,
  inventory) keeps the existing v2 contract and code.
- **Current source-output body** of the v3 slot is checked by a v3-only grammar/witness validator: text groups as
  today, followed by the paint group grammar, exact canonical re-serialization of text and paint, entry = exit context,
  and glyph containment unchanged.
- Natural shapes:
  - split `shared_flow` validation so that v2 keeps calling `source_ownership.validate` exactly as today, and v3
    injects its current-body validator;
  - or make two explicit stages, creation-evidence validation and current-body ownership validation.
- **v2 schema and behaviour are not relaxed.** A v2 sidecar never admits path operators, and the v2 opener still
  rejects v3.
- **Not a first candidate:** building a virtual paint-stripped PDF and passing it to the v2 validator. That would have
  to reconstruct the PDF SHA, owner program/block witness and context for bytes that were never published.

Non-blocking corrections from the same review:

- The fresh-process probe is described as "sidecar + asset + page frame": the parent reads the MediaBox top from the
  current PDF and passes it in (test docstring aligned).
- P-REC fill: underline fill = current text fill is the natural rule; black-only needs an explicit text-fill scope
  gate (§26.3).

The verdict is unchanged: **paint runtime NOT READY.** The next PR is the semantic underline contract.

## 27. Semantic underline contract — 2026-10-05

Starting main `55f2c8633bf68c764966a732580507ff5d6d6a3b` (PR #48 merged: paint runtime NOT READY, P-SURF option A
first candidate, v2 cannot validate a painted body). §1–§26 are preserved. **This section is design/evidence only:**
no paint runtime, no `pdfeditor/` change, no production writer, grammar or schema change. Runtime digest unchanged:
`dd4fff7b3e70fb65804fc7a82fdd1f2253bca8b52d47079ec265df4395aeb84e`.

**Verdict: SEMANTIC UNDERLINE CONTRACT READY.** Every §26.3 decision (P-SURF, P-SEM, P-REC, P-EMPTY, P-ADOPT) is
closed below and every P-VER obligation is named, with evidence in `tests/test_semantic_underline_contract.py`.
Next PR: **narrow semantic underline runtime implementation** (§27.20). It is not started here.

Terms used in this section (the two "versions" are different things):

| Term | Meaning |
|---|---|
| shared-flow v2 | `pdfengine-shared-flow-2` sidecar; opened only by `shared_flow.open_shared_flow` |
| shared-flow v3 | `pdfengine-shared-flow-3` sidecar; opened only by `semantic_layout.open_semantic_flow` |
| semantic record version N | `slots[s].semantic.version` inside a shared-flow v3 sidecar: 1 (PR #41/#42), 2 (§21.8), **3 (this contract)** |
| v3 current-body validator | the body check used **only** for semantic record version 3 |

### 27.1 P-SURF: option A adopted

The underline lives inside the existing owned source-output body of the single semantic slot, after the text group,
under the same marker pair. No paint marker domain is added.

```
% pdfengine-source-slot-v1 begin <marker_id>
q BT /PRF1 12 Tf 100 Tz 0 Tc 0 Tw 0 Ts 0 g            text group (unchanged L2 canonical island)
1 0 0 1 20 60 Tm <0002> Tj
…
ET Q
q 0 g                                                  underline group (only when ≥ 1 rectangle)
20 58.7 m 27.2 58.7 l 27.2 58 l 20 58 l h f
34.4 58.7 m 41.6 58.7 l 41.6 58 l 34.4 58 l h f
Q
% pdfengine-source-slot-v1 end <marker_id>
```

Why A: one owner (`marker_id`, `created_from`, witness, rebind and publication unchanged); no second marker inventory
(B4 does not come back); termination is re-serialization without the group, checked byte-exact (O3 does not come
back); text + paint are one canonical body regenerated from one semantic state.

**Search for a reason A cannot work.** Each runtime gate a painted owned body meets was exercised
(`test_only_the_named_gates_refuse_a_painted_writer_candidate`, in-process patches only, undone after the test;
individual gates also have their own probes):

| Gate | Painted body today | Under this contract |
|---|---|---|
| `source_ownership.grammar` (via `witness`) | refuses (`invalid source output body grammar`) | v2 unchanged; semantic version 3 injects the v3 body grammar (§27.2) |
| `inventory`, `record_identity`, entry/exit `context`, `containment` | accept the painted body (probe) | reused unchanged |
| rest of `shared_flow._validate` (contract, registry + observations, `open_editable` slot binding, generated fonts, placement, breaks) | accepts the painted writer candidate | reused unchanged |
| `Transaction._verify` non-text paint plan | refuses undeclared paint (`saved non-text paint differs …`) | the plan declares owned island paint (§27.17) |
| writer obstacle check (`_check_obstacles`) | refuses the next save: the island's own old underline is a "filled vector" crossing the new B descender ink | exclude old owned island paint only (§27.17) |
| slot binding `element` (`inspect_element`) | records the owned path as inferred `decoration_candidate`, `requires_confirmation` | owner paint is classified by the owner, never as a relation (§27.15) |
| `semantic_layout._island` canonical check | text-only | exact canonical text + paint body (§27.15) |

Once the v3 grammar is injected and the paint is declared, the unchanged shared-flow/semantic stack accepts the painted
candidate and `open_semantic_flow` reports it `restored` and canonical; with the real v2 grammar back, the same
candidate refuses. No gate showed a reason for option B; every refusal is a named implementation obligation.

### 27.2 v2 / v3 validation split

**Layer A — immutable creation evidence (existing v2 code, unchanged):** source observations, source style registry
and provider (`validate_registry`), `source_snapshot_sha256`, `source_model_sha256`, `created_from`, `marker_id =
identity(flow, slot, created_from)`, `contract_sha256`, flow/slot/region identity, source-creation provenance. It
never asks for a text-only *current* body.

**Current fragment binding (existing v2 code, unchanged, rebound every revision):** `open_editable` on the slot
binding (paragraph snapshot, element snapshot equality), `style_binding`, generated fonts, placement. These observe
the current PDF but compare it with what the same revision stored, so they accept a painted body (evidence above).

**Layer B — semantic version 3 current-body ownership:** marker inventory (exactly one pair = the owned slot), marker
identity, current body span, `program/block/range/entry` witness, exit context = entry context, glyph containment
(all existing `source_ownership` functions), with the **v3 body grammar** in place of the text-only grammar; then in
`semantic_layout`: exact canonical text + paint body, element-path classification and the current semantic binding.

**Internal shape for the implementation PR** (follows the B-L2-S `current_styles` precedent; no v2 line is copied):

```
source_ownership.witness(content, record, span, *, body_grammar=grammar)
source_ownership.validate(source, state, *, body_grammar=grammar)
source_ownership.owned_body(content, record, snapshot, *, body_grammar=grammar)
source_ownership.rebind(content, record, expected_context, *, body_grammar=grammar)
shared_flow._validate(source, value, current_styles=None, current_body=None)        # passes body_grammar only if set
shared_flow._open_current_flow(source, model, current_styles, current_body=None)   # internal to shared-flow-3
semantic_paint.body_grammar(body)          # new pure module; the text group is checked by source_ownership.grammar
semantic_layout._verify: version 3  →  _open_current_flow(…, current_body=semantic_paint.body_grammar)
```

- `grammar` and `BODY_OPERATORS` are not edited; v2 call sites pass nothing, so their behaviour is byte-identical.
- `open_shared_flow` never takes `current_body`: the v2 opener cannot accept paint, with any sidecar.
- Semantic versions 1 and 2 pass `current_body=None`: text-only, as today.
- Conceptually this is `validate_creation_evidence` + `validate_current_body`; physically `_validate` stays one
  traversal and only its last ownership call is parameterized. Splitting `_validate` into two functions would move
  ~100 lines of v2 validation and invite v2 drift for no gain.

**Not adopted:** a virtual paint-stripped PDF passed to v2 (§26.6): its PDF SHA, program/block witness and context
would describe bytes that were never published.

**Compatibility (all required):** the v2 opener never accepts paint; no v2 schema or grammar change; no v2 sidecar
auto-upgrade; a painted PDF with a v2 projection is refused by v2 (`test_unchanged_v2_grammar_rejects_the_same_painted_body`);
only semantic version 3 uses the v3 body grammar. Today's runtime also refuses a `decorations` field in a version 2
payload and any version 3 record (`test_current_runtime_refuses_decorations_and_a_version_3_record`).

### 27.3 v3 current-body grammar (normative)

```
body         := text_group [paint_group]            paint_group present iff ≥ 1 rectangle
text_group   := exactly one  q BT … ET Q  accepted by source_ownership.grammar
paint_group  := "q" fill rect+ "Q"
fill         := n "g" | n n n "rg" | n n n n "k"
rect         := x0 yu "m" x1 yu "l" x1 yl "l" x0 yl "l" "h" "f"          x0 < x1, yl < yu
```

Allowed in the paint group: `q Q g rg k m l h f` only. Refused: `re`, `S s B b` and every stroke, `f* F`, `n`, `W W*`
(clips), curves `c v y`, `cm`, `gs`/ExtGState, `w`, images/XObjects/inline images, shading, nested `q`, an empty
group, comments, any path that is not one axis-aligned rectangle. The grammar is structural only; meaning comes from
the exact canonical comparison (§27.15). Probes: accepted canonical body; 15 body tampers refused (geometry, fill,
extra/missing/reordered rectangle, `re`, stroke, `cm`, ExtGState, nested `q`, curve, clip, empty group, comment,
foreign path moved into the owner), and paint-before-text refused.

### 27.4 Canonical body order: text group, then paint group

| Criterion | text → paint | paint → text |
|---|---|---|
| Normal underline visual (MuPDF 144 dpi) | identical pixels | identical pixels |
| Z-order | same opaque fill, Normal blend, no transparency in scope: source-over of one colour is commutative, so overlap (B descender × underline) composites the same | same |
| Owned-body canonicality | text group stays a byte prefix: island anchors (`glyph:n`, `slot`) and stored witness byte ranges are unchanged; `decorations = []` gives exactly the L2 body | every anchor and stored text-event byte range shifts by the paint length, which depends on decorations |
| Existing source-output semantics | L2 body + appended group; PR #48 prototype appended after text | reorders an already verified body |

**Decision: text group, then paint group.** The order is part of the canonical contract; paint-first is refused.
Evidence: `test_text_then_paint_is_pixel_equivalent_and_keeps_the_text_group_a_byte_prefix` (the underline crosses
the B descender ink, so overlap is actually exercised; no other glyph is touched; text trace identical).

### 27.5 P-SEM: `payload.decorations` (semantic record version 3)

```json
"decorations": [
  {"id": "current:0", "kind": "underline", "start": 0, "end": 1,
   "start_affinity": "outside", "end_affinity": "outside",
   "recipe": {"offset_em": "13/120", "thickness_em": "7/120"}},
  {"id": "current:1", "kind": "underline", "start": 2, "end": 3,
   "start_affinity": "outside", "end_affinity": "outside",
   "recipe": {"offset_em": "13/120", "thickness_em": "7/120"}}
]
```

- Exactly these keys; current state only; no history, no tombstones, no `anchor_id`.
- `kind`: `underline` only.
- `start`/`end`: logical offsets into `payload.text` (Unicode code points, the semantic text's own indices), start
  inclusive, end exclusive, `0 ≤ start < end ≤ len(text)`, both grapheme-cluster boundaries (the same rule
  `shared_flow` uses). Never physical glyph indices.
- The range contains at least one visible character (not space, not newline) (§27.10).
- List sorted by `(start, end)`; ranges disjoint; touching (`end == next.start`) allowed and never merged; overlap
  and nesting refused.
- `recipe`: canonical exact rationals (the existing `_exact` spelling rule), `0 ≤ offset_em ≤ 1`,
  `0 < thickness_em ≤ 1`, plus the line-box rule (§27.8).
- Affinities: the existing anchored-decoration vocabulary (`inside`/`outside`, `anchors.project_range`); v1 admits
  only `outside`/`outside` (§27.7).

### 27.6 Identity and range semantics

- `id` = `current:i`, `i` = position in the canonical order, as for `derived.intervals`. It is a current-state label,
  not an identity. It is checked (`id == current:index`) but never chosen by a caller; a request naming a decoration
  must also name its exact current `[start, end)` (compare-and-swap), so a stale label from an earlier state refuses.
  Nothing revives a removed decoration: there is no revival request and no tombstone; `add` always creates a new one.
- **Semantic range vs painted geometry.** The logical range keeps its boundary spaces. Painted geometry excludes, per
  line, the spaces at the start and end of that line's part of the range (PR #48 rule); interior spaces are covered.
- **Newline.** A range may cross newlines; each line gets its own rectangle; a newline is never painted and lines are
  never joined. Trailing spaces omitted by layout are not painted. Soft wraps split the same way (lines come from the
  exact plan). Evidence: `test_newline_and_wrap_split_rectangles_per_line_without_painting_the_newline`.
- Stated limit (same kind as Tw vs edge, §21.4): ranges that differ only in trimmed boundary spaces paint identical
  bytes, so that difference is confirmed, not physically witnessed
  (`test_trimmed_boundary_spaces_are_not_physically_witnessed`).

### 27.7 Transitions and affinity

**N** (`reopen`): decorations exact; nothing written.

**D** (`save`, `reflow` at the confirmed width, `edit {start, end, text}`): `save`/`reflow` keep decorations. An edit
maps every decoration independently:

```
retained = old members < edit.start, and old members ≥ edit.end shifted by len(text) − (end − start)
inserted text is a member iff   edit.start < edit.end:  start ≤ edit.start and edit.end ≤ end   (replacement inside, incl. exactly the range)
                                edit.start = edit.end:  start < edit.start < end              (strict interior insertion)
new range = [min, max + 1) of the members; no members → terminated; no visible character → terminated
```

| Case (text `AA B`) | Result |
|---|---|
| insert before the range | shifted |
| insert strictly inside | included |
| insert at `start` (start affinity `outside`) | shifted, not included |
| insert at `end` (end affinity `outside`) | unchanged, not included |
| delete inside / delete either endpoint / delete across start | shrinks |
| delete the whole range | terminated |
| replace exactly the range | kept over the new text (existing `project_range` convention) |
| replace across start or end | deleted part shrinks; the inserted text is outside |
| edit after the range | unchanged |
| range left with only spaces/newlines | terminated |
| two touching decorations | stay separate; never merged |
| text becomes empty | every decoration terminated |

The map is monotone, so disjointness and order survive; IDs are re-derived. A D edit never refuses because of a
decoration. **Affinity is fixed in v1: `start_affinity = outside` (an insertion exactly at `start` goes before the
range), `end_affinity = outside` (an insertion exactly at `end` goes after it).** An underline grows only when text is
typed inside it; extending it at either edge needs an explicit E change. (`inside` is reserved vocabulary, refused.)

**E** (`reinterpret`), exactly one action per request, never combined with style/font/edge/label changes:

```
{"operation": "reinterpret", "changes": {"decorations": {"add":    {kind, start, end, start_affinity, end_affinity, recipe}}}}
{"operation": "reinterpret", "changes": {"decorations": {"remove": {id, start, end}}}}
{"operation": "reinterpret", "changes": {"decorations": {"recipe": {id, start, end, recipe}}}}   recipe must differ
```

**R** (refused): revival or restore of a removed decoration; reuse of an old ID or a stale `(id, range)`; any kind but
`underline`; caller-chosen IDs; overlap/nesting; a range with no visible character; a zero-length range; non-canonical
or out-of-bounds recipe; `recipe: "auto"` or any missing recipe (no hidden inference); `source_id`/path/geometry
fields, or an `adopt` action (source underline adoption, arbitrary physical path import); affinity other than
`outside`; a decoration action mixed with other changes; a decoration request on a semantic version 1 record; any
transition whose geometry breaks the line-box rule (§27.8), including a font/style E change. Evidence:
`test_d_remap_policy` (17 cases), `test_e_add_remove_and_recipe_change`, `test_e_refusals` (17 cases).

### 27.8 P-REC: exact em-relative recipe

**Authority:** the stored, caller-confirmed recipe `{offset_em, thickness_em}`, exact rationals relative to the
current semantic `font_size`. Geometry (page y-down, exact):

```
y_upper   = glyph baseline (line baseline + rise) + font_size · offset_em
thickness = font_size · thickness_em
x_left    = origin of the first painted glyph of the range on that line
x_right   = origin + exact advance of the last painted glyph of the range on that line
```

**Line-box rule (R):** each rectangle must satisfy `line.baseline − line.ascent ≤ y_upper` and
`y_upper + thickness ≤ line.baseline + line.descent` exactly. The line box is already inside the confirmed region,
which is inside the page. Because a line's descent is at least `hhea descent · size/upem + rise`, the rule holds
whenever `offset_em + thickness_em ≤ descent/upem`, independently of size and rise; it is still checked exactly per
rectangle (`test_line_box_containment_is_exact_and_size_invariant`: 1/4 em refused and 1/5 em admitted at 12, 37/3
and 24 pt, with a 1/5 em descent).

| Change | Effect on the underline |
|---|---|
| `font_size` | recipe unchanged; offset and thickness scale exactly with size (`test_em_recipe_follows_font_size_rise_and_ignores_horizontal_scale`) |
| `horizontal_scale` | vertical offset/thickness not scaled; endpoints follow the scaled advances |
| `rise` | follows the glyph baseline (rise −1 moves the rectangle by exactly −1) |
| `tracking` | endpoints follow; a range ending mid-line includes its last glyph's tracking; the line-terminal glyph has none (as in the plan) |
| `word_spacing` (Tw) / confirmed edges | endpoints follow exact positions; Tw 6/5 and the equivalent edge give identical bytes (`test_tw_and_confirmed_edge_give_identical_underline_bytes`) |
| font asset A → B | recipe unchanged, vertical geometry unchanged, endpoints follow B's advances (`test_font_change_keeps_the_recipe_and_its_vertical_geometry`); refused if B's line box cannot hold it |

Exact endpoint arithmetic, independent of the plan, matches for base, scaled, Tw, edge and font B (`AA B` at 37/3
pt, scale 4/5, tracking 1/4: right edge 44.43; with Tw or edge 45.63; with font B 41.683333)
(`test_endpoints_follow_tracking_tw_edges_and_font`).

Font tables (`post.underlinePosition/underlineThickness`, OS/2) are **never** semantic authority. A caller tool may
offer them as an initial proposal; the stored recipe is what the runtime uses, and a font change never re-derives it.

**Values used in the evidence:** `offset_em = 13/120`, `thickness_em = 7/120` — a caller-confirmed default candidate
for this narrow v1 only, not a standard. At 12 pt they are the PR #48 prototype's 13/10 pt and 7/10 pt
(`test_em_recipe_at_12pt_reproduces_the_pr48_prototype_coordinates`); `offset + thickness = 1/6 em` fits the 1/5 em
descent of both test fonts. Why em-relative and not absolute: an absolute offset kept y = 58.7 after 12 → 37/3 pt
(§26.4), so a size change would silently need a recipe re-confirmation; em-relative needs none, has no float feedback
and no renderer observation.

### 27.9 Fill authority

Underline fill = **current text fill**: `story_styles.properties(source registry entry)['fill']`, the value the
canonical text group already writes (§21.3). Creation evidence binds that registry fill to source observations; the
current-style adapter copies `fill`/`observed_color` from the source registry for both provenances (§21.8), so the
current text fill and the creation fill are the same value and no transition changes it. The paint group sets the
fill itself (the text group's `Q` restores the entry context), spelled exactly like the text group's fill, and never
depends on the entry context. Fills other than DeviceGray/RGB/CMYK are already refused by the island writer. There is
no underline colour, and no colour reinterpretation (out of scope); underline follows the text-fill scope.

### 27.10 P-EMPTY, collapse, all-space and newline-only

- Text becomes empty → **every decoration terminates**: `decorations = []`, no paint group, no dormant state.
  Regrowth never revives (`test_empty_text_terminates_decorations_and_regrow_never_revives`); only an explicit E add
  underlines again. B2 is not reintroduced.
- A range collapsing to `start == end` terminates; zero-length decorations are never stored.
- **All-space / newline-only: refused, not kept.** A decoration must contain a visible character: E add over only
  spaces/newlines refuses; a D edit that leaves only spaces/newlines in a range terminates it
  (`test_all_space_and_newline_only_ranges_are_refused_not_dormant`). Consequently every decoration paints at least one
  rectangle (a visible character is never omitted by the layout), so there is **no nonpainting decoration and no
  nonpainting witness** to design.

### 27.11 P-ADOPT refused; initial creation

v1 underline = an underline the caller explicitly declares on the semantic owned slot. Refused: detecting a source
underline path, adopting by geometry match, adopting a PDF `decorates` relation, migrating an old anchored underline,
moving a foreign path into the owned body. Shared-flow still refuses source decorations, so such a source never
reaches this surface; a foreign path placed into the owned body is refused by the canonical check; requests carrying
source/path fields or an `adopt` action are refused. `confirm_semantic_layout` keeps producing semantic version 2
without decorations; nothing is inferred from the source appearance at confirm or open. The first underline is
always an explicit E `add`.

### 27.12 Versioning, migration and enablement

- `semantic.version = 3`: version 2 keys `{version, payload, current, derived, binding}` unchanged; payload keys =
  version 2 keys + `decorations`; `derived` and `binding` keys unchanged. The shared-flow schema string stays
  `pdfengine-shared-flow-3` (precedent: §21.8 added version 2 the same way). Version 2 is not implicitly extended.
- Open never upgrades and never writes `decorations`. Version 1 and 2 records keep their rules and text-only bodies.
- The only path from version 2 to 3 is the first explicit `decorations.add` request itself: the plan reports
  `version 2 → next_version 3` and the diff names `decorations`; `require_authorized_payload` pins it. No separate
  enable request (it would publish a revision with no semantic change). Version 1 must first take the existing explicit
  1 → 2 confirmation.
- A version 3 record stays version 3 (also with `decorations = []`); there is no downgrade request. Its body is then
  text-only, byte-identical to version 2. A resealed version 3 `[]` → version 2 sidecar is semantically equal and
  indistinguishable, as for every P4 integrity-only checksum; it cannot hide paint, because a body with paint then
  fails the version 2 text-only grammar.
- Current authority (`semantic.current`) is unchanged by decoration requests (they are not style reinterpretations).

### 27.13 Multiple decorations and paint group structure

Several disjoint underlines are allowed (sorted by `(start, end)`; touching kept separate; overlap refused). One paint
group (structure A) holds all rectangles: decorations in canonical order, then lines top to bottom, one rectangle per
line per decoration, one `f` per rectangle (so each rectangle is one interpreted paint event). No group per
decoration: fewer operators, one fill, one place for the canonical check.

### 27.14 Canonical paint serialization

- Fill line: `q <fill operands> <op>\n`, operands spelled as in the text group.
- Rectangle line: `x0 yu m x1 yu l x1 yl l x0 yl l h f\n` with PDF `yu = page_top − y_upper`, `yl = yu − thickness`
  (page top = MediaBox top, the island writer's constant).
- Numbers: `semantic_island.decimal` (≤ 6 places, ties to even, no exponent, no −0); exact rationals flow one way.
- Order as §27.13; `Q\n` closes; nothing else; no redundant operator, no empty group.
- A rectangle whose serialized width or height is 0 refuses (no zero-area rectangle).
- Same semantic state ⇒ byte-identical body: across first → no-op, scale, Tw, edge and font no-ops, for full, partial
  and multiple ranges (`test_canonical_underline_bytes_are_a_noop_fixed_point`).

### 27.15 P-VER: semantic version 3 current-body verifier (implementation contract)

In order, all exact, any failure → `needs_confirmation`:

1. integrity, scope (§20.3), semantic record version 3 shape, payload incl. `decorations` (§27.5), current authority;
2. Layer A + fragment binding: unchanged `shared_flow._validate` via `_open_current_flow(…, current_body=v3 grammar)`;
3. marker inventory = exactly the owned slot's marker; `record_identity`; current body span; witness
   (`program_sha256`, `range`, `block_sha256`, `entry_context_sha256`) recomputed with the v3 grammar = stored
   `source_output.current`; exit context = entry context; entry context identity/unclipped/opaque/default black;
4. glyph containment (text events only, unchanged);
5. co-binding: `semantic.binding` = slot identity + owner witness + `pdf_sha256` + generated font (unchanged rules);
6. exact derivation from the current asset; island text equals the plan; generated-font checks unchanged;
7. body = canonical text group (existing `canonical_body`) **followed by** the canonical paint group from
   `rectangles(plan, style, decorations)` and the text fill — exact bytes; this fixes rectangle count, order,
   geometry, fill and text/paint operator order;
8. element classification: paths of the slot binding's `element` snapshot whose `source.merged_range` lies in the
   owned body are exactly the canonical rectangles (count, order, proven, one paint index each, bounds ≤ 0.002 pt);
   no `relations`/`anchors` entry names them; any inferred hypothesis on them has no authority
   (`test_v2_element_observation_infers_a_relation_for_owned_paint`).

Prototype of steps 3, 4, 7: `verify_current_body` in the test file; semantic tamper (range, recipe, kind, id, order,
affinity, missing/extra decoration) and body tamper (§27.3) refuse.

**Witness:** `source_output.current` is kept as is. The paint is inside `[begin, end]`, so `block_sha256` (and
`program_sha256`) already cover it: a one-coordinate change gives a different block SHA
(`test_v3_grammar_accepts_the_canonical_text_and_underline_body`). No paint-specific witness field is added.

### 27.16 Cross-page (O4) and scope

Decorations belong to the single owned slot on one page; the semantic scope already refuses continuation
destinations, multiple slots and regions. Every rectangle lies in its line box, inside the confirmed region, inside
the page. Any state or request whose decoration would need another slot, region or page refuses. No cross-page
surface is opened.

### 27.17 P-VER: Transaction ownership (implementation contract)

Underline paint is owned through the same `Transaction` and is not exempt from any check:

- **Owned old paint** = the catalog paths whose operator lies inside the verified owned body span, each proven by
  `prove_path_paint` to one paint index; their count, order and bounds must equal the canonical rectangles of the
  verified current state (`test_owned_paint_is_identified_by_the_catalog_between_foreign_paint`: foreign before,
  owned in order, foreign after; owned indices disjoint from foreign ones).
- **Declared changes:** old owned indices → replaced (first index → the new paint list, others → `None`, as anchored
  decoration plans do) or removed. When the old island has no paint and the new one has (first add), `paint_changes`
  cannot express an insertion; the PR adds an additive `Plan.paint_insertions` (anchor = the old island's last glyph
  event, which exists because E add needs a visible glyph and does not change the text). Existing plans are
  unaffected (empty by default).
- **Planned area:** `affected = resolved bbox ∪ new ink ∪ removed old ink ∪ old rectangles ∪ new rectangles`, old
  rectangles taken from the verified current semantic state (exact, like `_removed_ink`), not renderer bounds;
  `final_rects` += new rectangles. Evidence: with that mask no pixel differs for growth, size + scale + tracking, Tw,
  Tw → edge, font change, remove, add and shrink; without the old rectangles remove and shrink fail, without the new
  ones add fails (`test_old_and_new_rectangles_bound_every_changed_pixel`).
- **Obstacles:** new rectangles are checked like new ink (foreign glyphs, images, filled vectors, vector borders,
  links, annotations). Only the island's own old paint indices are excluded (by their proven seqnos). Foreign paint
  near the island stays an obstacle exactly where it is hit
  (`test_existing_obstacle_check_would_treat_old_owned_paint_as_foreign`).
- **Unchanged:** foreign paint protection (non-text paint list equality), glyph protection, image/vector protection,
  pixel-outside-area check, font ownership, source revision guard.
- **Ownership boundary:** only the canonical underline inside the owned body is owned. Any underline-like path,
  vector, image or background outside the markers is foreign, whatever it looks like. O1 (ordinary/anchored edits
  over a painted island) stays fail-closed by the whole-PDF SHA; the PR adds a test that such an edit never yields a
  reopenable version 3 bundle.
- **Rebind:** `source_ownership.rebind(…, body_grammar=v3)`; `marker_id`, `created_from` and entry context unchanged.

### 27.18 Publication

Unchanged L3 (§22): `publish_semantic_bundle` delegates to `open_semantic_flow`, which dispatches version 3 to the
v3 verifier. Persistent artifacts stay the PDF + one shared-flow sidecar (paint state is in the same sidecar); no third
sidecar, manifest or paint record.

### 27.19 Negative matrix required in the implementation PR

Each refuses on actual candidates, also after resealing the sidecar where that applies: decoration range, recipe,
kind, ID/order, affinity tamper; current-body paint tamper (geometry, fill, extra path, missing path, reorder,
paint-before-text, `re`/stroke/clip/cm/ExtGState/curve/nested `q`/empty group); stale owner (current PDF + old
witness); stale semantic (old decorations over a new body and the reverse); resealed sidecar claiming different paint;
foreign paint moved into the owned body; foreign underline outside the markers never adopted; source underline
adoption request; v2 opener on the painted PDF with a v2 projection; version 1/2 record over a painted body; version
3 downgrade attempts that hide paint; decoration request on version 1; mixed decoration + style request; line-box
violation after a font or size change; old ID reuse and revival requests; Transaction: undeclared owned paint,
foreign paint change, pixels outside the planned area, obstacle hit by a new rectangle.

### 27.20 Evidence, validation and verdict

`tests/test_semantic_underline_contract.py`, **91 tests** (read-only; tmp copies only; the one writer probe uses
in-process `monkeypatch` that is undone): v3 grammar accepts the canonical text + underline body; unchanged v2 grammar
and opener refuse it; current runtime refuses `decorations` and version 3; canonical no-op fixed point (6 groups ×
3 range sets); 12 pt continuity with PR #48; em recipe vs size/rise/scale; exact endpoints for tracking/Tw/edge/font;
Tw = edge bytes; font change keeps the recipe; line-box rule; D remap (17); empty termination; newline/wrap; E add/
remove/recipe; E refusals (17); all-space/newline refusal; semantic tamper (8); body tamper (15); trimmed-space limit;
paint-first refused; removed/new area (8 pairs, with necessity); z-order; owned vs foreign paint identification;
obstacle obligation; element-relation obligation; runtime gate probe.

Validation (Linux, PyMuPDF 1.27.2.3, pytest-xdist 4 workers, `--dist loadfile`): new file **91 passed**; focused
(`test_semantic_underline_contract`, `test_paint_reassessment`, `test_source_ownership`, `test_semantic_layout`,
`test_semantic_writer`) **246 passed**. Full suite (whole `tests/` tree, collected before the last probe,
`test_only_the_named_gates_refuse_a_painted_writer_candidate`, was added): **1,712 passed, 21 skipped, 0 failed**;
the skips are the same 21 environment-only skips as §22.3. No existing test changed. Runtime digest unchanged
(`dd4fff7b…`). Windows and Poppler were not run (no runtime or rendering-path change).

**Verdict: SEMANTIC UNDERLINE CONTRACT READY.** Closed: P-SURF (A), v2/v3 validator split, body order (text → paint),
paint grammar, decorations schema, range semantics, affinity (outside/outside), recipe units (exact em), fill
(current text fill), empty behaviour (terminate), source adoption (refused), semantic version (3, explicit add),
multiple decorations (disjoint, one group). Remaining blockers: none. Narrow paint implementation may start.

**Next PR — narrow semantic underline runtime implementation (scope).** Same narrow scope as L1–L3 (one paragraph,
one owned slot, one region, one body style, left, static TT, A/B/space/newline, ≤ 256 characters, default context):

1. semantic record version 3 schema + `decorations` validation; explicit version 2 → 3 by the first `add`;
2. N/D/E/R decoration policy in `plan_semantic_transition` (§27.7) and authorized-payload pinning;
3. `semantic_paint` pure module: geometry, canonical paint serialization, v3 body grammar;
4. `source_ownership`/`shared_flow` `body_grammar`/`current_body` injection (v2 defaults unchanged);
5. v3 verifier steps (§27.15) incl. element classification; witness unchanged;
6. canonical text + underline writer; Transaction owned paint (replace/remove/insert), planned area, obstacle
   exclusion of own old paint only;
7. rebind, candidate reopen, L3 publication unchanged, fresh-process reopen;
8. the §27.19 negative matrix and a MuPDF raster check (no-op pixel-stable; add/remove/size/font changes differ).

Out of scope there: ordinary editable paint, source underline adoption, colours, strike/overline/other kinds,
cross-page, multiple slots, transparency, ink-skipping.

## 28. Narrow semantic underline runtime implementation — 2026-10-06

Starting main `e793f30a59c8798779e71b4ce6c1d3088d83708c` (PR #49 merged: SEMANTIC UNDERLINE CONTRACT READY). §1–§27
are preserved as historical records. **This section implements §27 as written; it does not redesign it.** Runtime
digest: `dd4fff7b3e70fb65804fc7a82fdd1f2253bca8b52d47079ec265df4395aeb84e` → `4b6f9925159160c9ad63cee19f082728bfadf8c70548678fd89d40a3665659e3`.

**Verdict: NARROW SEMANTIC UNDERLINE RUNTIME COMPLETE** (§28.12). This covers the caller-declared underline on the
single owned semantic slot only. It is not paint runtime in general, not arbitrary vector editing, not source
underline editing and not Acrobat-like editing.

### 28.1 Scope

Implemented, on the existing L1–L3 surface (one paragraph, one owned slot, one region, one body style, left, static
TT, A/B/space/newline, ≤ 256 characters, identity/unclipped/opaque/default-black entry context):

| Area | Implementation |
|---|---|
| Semantic record version 3 + `payload.decorations` | `semantic_layout` (`DECORATED_VERSION`, `_payload(…, version)`, `_record`, `_attach(…, version)`) |
| N/D/E/R | `semantic_layout._next_payload` / `_next_decorations`, `plan_semantic_transition` (`next_version`, line-box refusal before any write) |
| Pure underline module | **new** `pdfeditor/semantic_paint.py`: schema validation, D remap, E actions, exact geometry, the one canonical serializer, the v3 body grammar |
| v2/v3 validation split | `source_ownership.witness/validate/owned_body/rebind(…, body_grammar=None)`, `shared_flow._validate/_open_current_flow(…, current_body=None)` |
| v3 verifier | `semantic_layout._verify` → `_open_current_flow(…, current_body=semantic_paint.body_grammar)`, `_island(…, decorations)`, `_owned_paint` |
| Canonical text + underline writer | `semantic_writer._plan_island` (body = L2 text group + `paint_group`) |
| Transaction | `Plan.paint_insertions` (new, additive), `PageTransaction.expected_paints`; `paint_changes` for owned replacement/removal; `consumed_paths` |
| Obstacles | `composition._check_obstacles(…, exclude_paint_seqnos=frozenset())` |
| Owner/semantic rebind, publication | unchanged code paths, v3 grammar injected for version 3 only; L3 unchanged |

Not implemented (out of scope, all refused or absent): ordinary editable paint, source underline adoption, arbitrary
path adoption or migration, any kind but `underline`, underline colour, strikeout/highlight/borders, cross-page,
multiple slots, dormant state, revival, legacy paint migration, transparency, LibreOffice adaptation.

### 28.2 Semantic record version 3

- `semantic.version = 3`; record keys equal version 2 (`version, payload, current, derived, binding`); payload keys
  = version 2 keys + `decorations`. The shared-flow schema string stays `pdfengine-shared-flow-3`.
- `decorations` exactly as §27.5: `{id, kind, start, end, start_affinity, end_affinity, recipe{offset_em,
  thickness_em}}`, `id = current:i` (checked, never chosen), `kind = underline`, `outside/outside`, logical `[start,
  end)` code-point offsets on grapheme boundaries, ≥ 1 visible character, sorted, disjoint (touching kept), canonical
  exact rationals with `0 ≤ offset_em ≤ 1`, `0 < thickness_em ≤ 1`. No history, no tombstone, no extra sidecar field,
  no new sidecar.
- **Version rule.** `confirm_semantic_layout` still produces version 2 and refuses a `decorations` field. Opening
  never upgrades. The plan reports `version` and `next_version`; `next_version = 3` only when the current record is
  version 3 or the request is the explicit `decorations.add` on version 2 (`remove`/`recipe` on version 2 refuse:
  "only an explicit decorations.add enters version 3"). Version 1 refuses every decoration request ("confirm version 1
  as version 2 first"). A version 3 record stays version 3 with `decorations = []`; its body is then byte-identical to
  the version 2 text-only body. A version 3 bundle is not a confirmation input, so there is no downgrade path.
- Current style/font authority (`semantic.current`) is unchanged by decoration requests.

### 28.3 Transitions

- **N** (`reopen`): decorations exact; no write.
- **D** (`save`, `reflow`, `edit`): `save`/`reflow` keep decorations. `edit` maps each decoration through
  `semantic_paint.remap`, the §27.7 rule: members are retained positions plus inserted text when the edit is a
  replacement inside the range (including exactly the range) or a strict interior insertion; edge insertions are
  outside; crossing edits shrink and the inserted text is outside; no members or no visible character →
  terminated; IDs re-derived. For every non-crossing edit the result equals `anchors.project_range(…, outside,
  outside)` (exhaustive test over `AA B`); crossing edits are where §27.7 deliberately differs (`project_range`
  refuses, a D edit never refuses because of a decoration).
- **E** (`reinterpret` with only `decorations`): exactly one of `add {kind, start, end, start_affinity,
  end_affinity, recipe}`, `remove {id, start, end}`, `recipe {id, start, end, recipe}`; `(id, start, end)` is a
  compare-and-swap reference; a recipe change must differ. Mixed with any other change → refused.
- **R**: revival/`revive`, `adopt`, `source_id`/path fields, caller-chosen ID, stale or old `(id, range)`, other
  kinds, `inside` affinity, overlap/nesting, zero-length, no visible character (all-space, newline-only), missing
  or `auto` recipe, non-canonical/out-of-bounds recipe, mixed request, version 1 request, and any next state whose
  underline breaks the exact line-box rule (also a font or size E change: a 1/10 em-descent font is refused for a
  1/6 em recipe, admitted without the underline).
- **Empty:** text → empty terminates every decoration (`[]`, no paint group, no dormant state); regrowth never
  revives; only an explicit `add` underlines again.

### 28.4 Canonical body and grammar

```
% pdfengine-source-slot-v1 begin <marker_id>
q BT /PRF1 12 Tf 100 Tz 0 Tc 0 Tw 0 Ts 0 g      L2 canonical text group (unchanged bytes, still a prefix)
1 0 0 1 20 60 Tm <0002> Tj …
ET Q
q 0 g                                             one underline group, only when ≥ 1 rectangle
20 58.7 m 41.6 58.7 l 41.6 58 l 20 58 l h f       one rectangle per decoration per line, one f each
Q
% pdfengine-source-slot-v1 end <marker_id>
```

- Geometry (`semantic_paint.rectangles`, exact rationals, page y-down): `upper = glyph baseline (incl. rise) +
  font_size·offset_em`, `thickness = font_size·thickness_em`, `left` = first painted glyph origin, `right` = last
  painted glyph origin + exact advance (tracking, Tw, confirmed edges and glyph positions come from the exact plan);
  per-line boundary spaces trimmed; newline never painted; `horizontal_scale` never scales the vertical geometry;
  font tables are never read. Each rectangle must satisfy the exact line-box rule.
- Fill = the current text fill, spelled like the text group's fill and set by the group itself (`q <fill> … Q`);
  there is no underline colour field.
- Serialization (`semantic_paint.paint_group`, the only serializer): `semantic_island.decimal`; zero-area after
  decimals refused; decorations in canonical order, then lines top to bottom; no group when there is nothing to paint.
- v3 grammar (`semantic_paint.body_grammar`): exactly one text group accepted by the unchanged
  `source_ownership.grammar`, then at most one `q (g|rg|k) (x0 yu m x1 yu l x1 yl l x0 yl l h f)+ Q` group of
  axis-aligned rectangles. Refused: `re`, strokes, clips, curves, `cm`, `gs`, images/XObjects, nested `q`, empty
  group, comments, non-rectangles, paint before text.

### 28.5 v2/v3 validation split

- `source_ownership.grammar` and `BODY_OPERATORS` are unchanged. `witness`, `validate`, `owned_body` and `rebind` take
  a keyword-only `body_grammar=None`; `source_ownership.injected()` passes the keyword **only** when a v3 grammar is
  injected, so every v2 call site calls exactly as before (an existing test that replaces `witness` with a
  positional-only stub still passes unchanged).
- `shared_flow._validate`/`_open_current_flow` take `current_body=None` and hand it to `source_ownership.validate`.
  `open_shared_flow` has no such parameter: the v2 opener never accepts paint, with any sidecar.
- `semantic_layout._verify` injects `semantic_paint.body_grammar` only for semantic record version 3. Versions 1 and 2
  stay text-only. No virtual paint-stripped PDF is built.

### 28.6 v3 verifier (order of §27.15)

1. integrity (`model_sha256`), scope, record version 3 shape (explicit current authority required), payload incl.
   `decorations`, current authority;
2. Layer A + fragment binding: the unchanged `shared_flow._validate` through `_open_current_flow(…, current_body=v3)`,
   which also runs (3) marker inventory = exactly the owned marker, `record_identity`, the witness recomputed with the
   v3 grammar (`program_sha256`, `range`, `block_sha256`, `entry_context_sha256`; unchanged shape — the block SHA
   covers the paint), exit context = entry context, and (4) glyph containment;
5. co-binding (`semantic.binding`) unchanged;
6. exact derivation from the current asset, island text = plan, generated-font checks unchanged; entry context
   identity/unclipped/opaque/default black;
7. body = canonical text group **followed by** the canonical underline group — exact bytes, otherwise "semantic
   version 3 body is not the canonical text and underline body";
8. element classification (`_owned_paint`): the slot binding's element snapshot (re-inspected and compared with the
   current PDF by `open_editable`) has, inside the owned body, exactly the canonical rectangles in order — proven,
   one fill paint each, bounds ≤ 0.002 pt; no path crosses the body edge; no relation names them. The observation
   still records an inferred `decoration_candidate`; it has no authority.

### 28.7 Writer and Transaction

- **Owned old paint** (`semantic_writer._old_owned_paint`, version 3 bodies only; version 1/2 writes keep their exact
  path): the catalog paths inside the verified owned body must equal, in count and order, the canonical rectangles of
  the verified current state, each proven by `prove_path_paint` to one fill paint with bounds ≤ 0.002 pt; their
  drawings must be found at the proven seqnos. Anything else inside the body refuses. Owned path IDs are declared
  `consumed_paths`, so no relation can be carried onto them.
- **Replacement/removal:** old owned indices → `paint_changes` (first index → the new paint list, others → `None`;
  all `None` on removal).
- **First underline:** `Plan.paint_insertions = {index: [paints]}` — new, additive, empty by default; `index` is the
  old island's last glyph paint (found by its trace seqno). `PageTransaction.expected_paints` inserts after that
  event; two plans inserting at one anchor, or an anchor outside the observation, refuse.
- **Expected paint values** are predicted, never observed from the output: geometry/bounds from the exact rectangles;
  CTM, clips, groups, layers, colour space, components, opacity and colour parameters from the island's own glyph
  paint (same entry context, same fill operator values). A wrong geometry, colour, a missing or an extra paint each
  refuse with "saved non-text paint differs from the transaction plan".
- **Planned area:** `resolved bbox ∪ new ink ∪ new rectangles ∪ removed ink ∪ old rectangles` (old rectangles from the
  verified current state); `final_rects` += new rectangles. With only the glyph-ink box as the area, adding and
  removing an underline both refuse ("changed pixels outside its planned areas").
- **Obstacles:** new rectangles are checked like new ink (foreign glyphs, images, filled vectors, vector borders,
  links/annotations via the widened layout bounds). Only the island's own proven old underline drawings are excluded
  (`exclude_paint_seqnos`); without that exclusion the next save is refused by its own old underline crossing the
  B descender ink.
- **Rebind:** `source_ownership.rebind(…, body_grammar=v3)` for version 3; `marker_id`, `created_from`, entry context
  unchanged; then `semantic._attach(…, version=plan['next_version'])` and the fresh-from-disk candidate check (now
  also `version == next_version`).
- Unchanged: foreign paint list equality, glyph protection, image/vector protection, pixel-outside-area check, font
  ownership, source revision guard, L3 publication.

### 28.8 Ownership boundary

Only the canonical underline inside the owned markers is owned. Foreign paint keeps its bytes and order (a foreign
rectangle before the island stays first through add and remove), and a foreign rectangle under a new underline is an
obstacle ("filled vector"). Source paint is never detected, inferred, matched, adopted or migrated: confirmation and
opening never produce decorations; the first underline is always the explicit E `add`; a foreign path moved into the
owned body is refused by the canonical check. O1: the shared-flow v2 editor cannot open a painted revision (grammar),
and a foreign save changes the whole-PDF revision, so the version 3 bundle needs confirmation.

### 28.9 Publication and reopen

L3 is unchanged: `publish_semantic_bundle` stages, verifies through `open_semantic_flow` (which dispatches version
3), renames once and reopens. Candidate and published bytes are identical; a published bundle is the next revision's
input; mixed PDF/sidecar pairs are refused; a fresh process reopens each bundle as `restored`, version 3, with the
same decorations. A no-op save rewrites the owned body byte-identically, so the no-op PDF is the same revision.

### 28.10 Changes to existing tests

`tests/test_semantic_underline_contract.py` (91 probes) now runs on the production code: the in-file prototypes
(`validate`, `remap`, `reinterpret`, `rectangles`, `paint_group`, `body_grammar`, `verify_current_body`) are replaced
by `semantic_paint`, `semantic_layout._next_decorations`, the production witness with the injected v3 grammar and
`semantic_layout.canonical_body`. Two probes asserted the pre-implementation state and were rewritten for the
implemented state: `test_current_runtime_refuses_decorations_and_a_version_3_record` →
`test_decorations_exist_only_in_semantic_version_3` (v2 payload still refuses `decorations`; version 3 is a known
record that requires them; version 4 refused), and `test_only_the_named_gates_refuse_a_painted_writer_candidate`
(monkeypatched writer) → `test_the_production_runtime_passes_every_gate_for_a_painted_candidate` (real add → save →
edit; v2 path still refuses). The obstacle probe additionally shows the seqno exclusion. No other existing test
changed.

### 28.11 Tests and validation

New `tests/test_semantic_underline.py`, **106 tests**: lifecycle (v2 text-only reopen, explicit add → version 3,
add, D remap, style, font, recipe, remove, multiple/touching, newline, empty/regrow, no-op fixed point incl. operators
and sidecar semantic fields, Tw = edge bytes); version rules and no downgrade; `project_range` equivalence;
20-case R matrix through both the plan and the writer; line-box refusal on a font change; version 1 refusal;
v2/v3 split (v2 opener, v2/v1 records over a painted body, version 3 without current authority, unchanged
signatures); 8 resealed semantic tampers;
stale/mismatched pairs; 15 body tampers (geometry, fill, extra/missing path, `re`, stroke, clip, curve, `cm`,
ExtGState, nested `q`, empty group, comment, non-rectangle, image) and paint-first / foreign path / paint without
decorations through the production `_island`; 5 element-classification tampers; Transaction declarations
(insertion, replacement, removal, none), owned-paint identification, 4 mis-declared paint faults, planned area
(necessity and containment), obstacle exclusion; foreign paint kept / obstacle / never adopted; O1; publication,
next revision, fresh process; MuPDF and Poppler raster; 7 failure-injection points + publication failure.

Validation (Linux, Python 3.13.16, PyMuPDF 1.27.2.3, Poppler `pdftoppm` 24.02.0, pytest-xdist 4 workers):

- new file **106 passed**; contract file **91 passed**;
- focused (underline, contract, semantic writer/layout/authority/publication/lifecycle, source ownership, paint
  reassessment, transaction, shared flow, composition): **547 passed, 1 skipped**;
- full suite on the final HEAD (whole `tests/` tree, `--dist loadfile`): **1,821 passed, 19 skipped, 0 failed (1,715 baseline + 106 new; the 19 skips are the same environment-only skips as the baseline: Windows Arial/Noto Sans JP absent, external corpus not downloaded, one Windows-path Poppler regression)**. Baseline on main
  `e793f30` in the same environment: 1,715 passed, 19 skipped, 0 failed.
- **MuPDF 144 dpi:** text-only baseline ≠ underline added; add = no-op 1 = no-op 2; recipe, style and font changes
  differ; font no-op identical; underline removed = text-only baseline; candidate = published (bytes identical).
  **Poppler 144 dpi** (available here): add = no-op 1 = no-op 2; add ≠ baseline; removed = baseline.

### 28.12 Completion criteria

| Criterion | Evidence |
|---|---|
| explicit add works | `add` revision, `test_opening_never_upgrades_and_only_the_explicit_add_enters_version_3` |
| v2 → v3 only by add | same; `remove` on version 2 refused; confirm/open never produce decorations |
| D remapping | edit revision, contract D matrix (17), `project_range` equivalence |
| E add/remove/recipe | add, recipe, removed, multi revisions |
| refusal matrix | 20 R cases (plan + writer), contract E refusals (17), all-space/newline, line box, version 1 |
| canonical paint writer | canonical body test, Tw = edge, multiple/newline |
| current verifier | tamper matrices (semantic, body, element), stale pairs |
| Transaction ownership | declaration, identification, mis-declaration, planned area, obstacle tests |
| foreign paint protected | foreign kept/obstacle/never adopted, O1 |
| no-op stable | fixed-point tests (5 groups), raster |
| publication / published reopen / next revision | publication test (incl. fresh process) |
| tamper refuses | resealed semantic, body and element tampers |
| full suite 0 failed | §28.11 |

**NARROW SEMANTIC UNDERLINE RUNTIME COMPLETE.**

### 28.13 Remaining boundaries and next step

- Stated limits carried over from §27: ranges differing only in trimmed boundary spaces paint identical bytes
  (confirmed, not physically witnessed), as Tw vs edge (§21.4).
- The planned-area necessity check with a glyph-ink box cannot show a recipe-only change, because the B descender
  makes that box cover the whole descent band; the per-rectangle mask evidence of §27.17
  (`test_old_and_new_rectangles_bound_every_changed_pixel`) covers it.
- **WINDOWS UNDERLINE VALIDATION NOT RUN.** §25 validated the text-only semantic lifecycle on Windows; it does not
  cover version 3. **LibreOffice: not addressed** (separate axis, §24.5/§25.3 unchanged).
- Next recommended PR: **Windows semantic underline validation** — extend the §24 harness with underline stages
  (explicit add, no-op ×2, edit remap, recipe, remove, publish A/B, reopen from the published bundle, MuPDF/Poppler
  raster) and run it once on Windows. No runtime scope change.

## 29. Windows semantic underline validation preparation — 2026-10-06

Starting main `ae82d16ca5e45dd1342cede8404fbf565b9412e8` (PR #50 merged: NARROW SEMANTIC UNDERLINE RUNTIME COMPLETE).
§1–§28 are preserved.

> **Windows underline execution has not been performed by this PR.** Cloud runs (Linux, synthetic fixtures) test the
> harness only and are not Windows evidence. LibreOffice is not part of this validation.

**Runtime changes: none.** Runtime digest `4b6f9925159160c9ad63cee19f082728bfadf8c70548678fd89d40a3665659e3`, the
same as PR #50. Semantic version 3, the decoration schema, the paint grammar, the Transaction and ownership are
unchanged.

### 29.1 Harness extension

The §24 harness (`evaluations/semantic_lifecycle/windows_validation.py`) is extended, not replaced. `run --mode
underline` reuses its preparation (`prepare-fonts`, `prepare-synthetic`, `prepare`), environment capture, input SHA
preservation, partial `result.json` after every stage, `report.md`, L3 publication, fresh-process reopen, MuPDF and
Poppler rendering, PASS/REFUSED/SKIPPED/FAIL statuses, verdicts and exit codes. **`run` without `--mode` is the
text-only lifecycle exactly as before** (same 16 stages, same plan, same report; `result.json` only gains the
additive `mode`, `validation_kind` and `underline_runtime` keys). Commands, stages and the schema are in the
[harness README](../evaluations/semantic_lifecycle/README.md#underline-mode-run---mode-underline-docs-29).

### 29.2 Underline lifecycle

```
preflight → baseline → confirm (version 2) → text_baseline (L2 save, version 2, text-only)
  → add (explicit decorations.add: E, 2 → 3) → noop_1 → noop_2 → edit_remap (D) → recipe (E) → style (E)
  → font (E, font A → B) → publish_a → remove_from_bundle_a (input = bundle-a/*, E remove, stays 3)
  → publish_b → negatives → tamper → continuity → text_only_control → raster_mupdf → raster_poppler (optional)
  → inputs_preserved (always)
```

The default validation recipe is the §27/§28 recipe (`13/120`, `7/120`). Each built revision records its transition
(`classification`, `version`, `next_version`, authority preserved) and the observed paint transition (`insert`,
`replace`, `remove`, `none`) from the parent's and the candidate's underline groups. That is output evidence: the
harness does not reach into `Plan`.

Per revision, `revisions{name}.underline` records the semantic version, decorations (count, ranges, recipes), the
written rectangles, the paint fill, the text-group `Tm` coordinates, the body SHA, the text-group and paint-group SHAs
(split by the runtime's own v3 body grammar; `paint_body_sha256 = null` when there is no underline group) and the
MuPDF 144 dpi raster SHA. The owner witness and provider SHA stay in the existing `owner`/`semantic` records.

Checks (all exact; any mismatch is FAIL):

- **add:** version 2 → 3, E, current authority unchanged, `current:0` with the requested range and recipe, text group
  unchanged.
- **no-op ×2:** owned body, operators, rectangles, decorations, semantic payload/authority/derived, owner block SHA and
  raster identical. The PDF bytes are identical too (recorded).
- **edit remap:** decorations equal the §27.7 remap (default `[0,3)` → `[0,4)`; the rectangle's right edge 41.6 → 48.8).
- **recipe:** the text and x endpoints are unchanged, the y geometry changes, the authority is unchanged.
- **style:** the decorations are unchanged, only `style.*` changes, and the em geometry from the written bytes is
  exact (`Tm y − yu = rise + size·offset_em`, `yu − yl = size·thickness_em`, within the 1e-6 output decimal; measured
  error 0).
- **font:**
  - the provider and the generated font are font B;
  - the recipe and the y geometry are unchanged;
  - the left edges lie on the new glyph origins.
- **publications:**
  - candidate and published bytes are identical;
  - fresh-process reopen succeeds;
  - version and decorations are restored;
  - the raster is identical;
  - bundle A is unchanged after bundle B.
- **remove:** the input is `artifacts/bundle-a/*`; version stays 3; `decorations = []`; there is no underline group;
  the text group is unchanged.
- **raster (MuPDF 144 dpi):**
  - baseline ≠ add;
  - add = no-op 1 = no-op 2;
  - recipe, style and font each ≠ previous (for the default plan, each moves painted geometry by ≥ 0.1 pt; a custom
    plan that moves no pixel FAILs, it never passes silently);
  - removed = the text-only control (the same edit/style/font requests on version 2, never decorated);
  - candidate A = bundle A, candidate B = bundle B.
- **raster (Poppler, optional):** the same identities and baseline ≠ add.
- **expected refusals:**
  - mixed A.pdf+B.json and B.pdf+A.json;
  - on copies, a resealed decoration-recipe tamper ("not the canonical text and underline body");
  - a paint-geometry tamper ("PDF revision changed").

### 29.3 Synthetic cloud results (harness only, not Windows evidence)

Linux, Python 3.13.16, PyMuPDF 1.27.2.3, Poppler `pdftoppm` 24.02.0, synthetic fonts and the synthetic control:

| Run | Verdict | Exit | Notes |
|---|---|---|---|
| `run --mode underline`, in-scope control | `PASS` | 0 | **UNDERLINE HARNESS PASS**: all 21 stages pass incl. Poppler; 15 revisions; ≈ 6.5 s |
| `run --mode underline`, LibreOffice page shape | `UNSUPPORTED_TARGET` | 3 | `confirm` refused (unclipped entry context); later stages SKIPPED; inputs preserved |
| `run` (text mode), in-scope control | `PASS` | 0 | the unchanged 16-stage text lifecycle |
| `run` (text mode), LibreOffice page shape | `UNSUPPORTED_TARGET` | 3 | unchanged expected refusal |

Recorded synthetic values:

| Revision | Range | Rectangle (PDF `x0 yu x1 yl`) |
|---|---|---|
| add | `[0,3)` | `20 58.7 41.6 58` |
| edit | `[0,4)` | `20 58.7 48.8 58` |
| recipe `1/10`, `1/20` | `[0,4)` | `20 58.8 48.8 58.2` |
| style 13 pt, tracking 1/4 | `[0,4)` | `20 58.7 51.95 58.05` |
| font B | `[0,4)` | `20 58.7 46.75 58.05` |
| remove / bundle B | none | (text group identical to the text-only control's) |

### 29.4 Tests and validation

- **New** `tests/test_semantic_windows_underline_harness.py`: 20 tests.
  - The underline lifecycle, v2 → v3, the no-ops, the remap, the recipe, style and font, publication A,
    bundle-A reuse, remove, publication B and the fresh-process reopen.
  - Expected refusals and tamper, the raster expectations, input preservation, the schema additions and the report
    section.
  - Text-mode compatibility (no `--mode` and `--mode text`), an unknown mode as a usage error, a partial FAIL with
    retained evidence, Poppler optional, missing font B → INCOMPLETE, the LibreOffice shape → UNSUPPORTED_TARGET, a
    plan-override refusal and the CLI in a fresh process.
- The existing `tests/test_semantic_windows_harness.py` (18 tests) is unchanged and passes.
- Focused: **323 passed** (`test_semantic_windows_harness`, `test_semantic_windows_underline_harness`, `test_semantic_underline`, `test_semantic_underline_contract`, `test_semantic_publication`, `test_semantic_lifecycle`).
- Full suite on the final HEAD: **1,841 passed, 19 skipped, 0 failed** (PR #50: 1,821 passed; +20 new; the 19 skips are the same environment-only skips: Windows Arial / Noto Sans JP absent, external corpus not downloaded, one Windows-path Poppler regression).

### 29.5 Windows procedure (next evidence PR)

On Windows, only the synthetic control is run. LibreOffice stays a separate axis (§24.5 step 2, §25.3).

1. Clean checkout of this PR's merge commit.
2. Environment setup (Python 3.12, lockfile).
3. `prepare-fonts` (Arial/Times A/B/space unhinted subsets).
4. `prepare-synthetic`.
5. `prepare`.
6. `run --mode underline`.
7. Check `result.json`/`report.md`: `windows_execution: true`, `mode: underline`.
8. Commit only `result.json` and `report.md` under
   `evaluations/semantic_lifecycle/runs/windows-underline-synthetic-<date>/`.

The exact PowerShell command is in the README (array splat, `& $P @H …`). Expected outcome: `PASS`, exit 0. It is
**not claimed** until it has run, and any other outcome is recorded as it is.

### 29.6 Verdict

**WINDOWS SEMANTIC UNDERLINE VALIDATION READY:**
- the harness is extended;
- the text-only mode regression passes;
- the underline synthetic lifecycle passes;
- the result/report schema works;
- publication A/B, bundle reuse, failure recording and input preservation work in the harness;
- the Windows command is documented;
- the full suite passes;
- no Windows PASS is claimed.

Windows underline execution: **NOT RUN**. LibreOffice: **not part of this validation**.


## 30. Windows semantic underline external validation — 2026-10-06

Starting main `769a0a9f4b4b497d1c157ece1129167d1f8ec838` (PR #51 merged 2026-10-06: WINDOWS SEMANTIC UNDERLINE
VALIDATION READY). §1–§29 are preserved. This is the first Windows execution of the §29 `run --mode underline`
lifecycle. **This section is evidence only. Runtime changes: none. Harness, expected values, fixtures and tolerances
are unchanged.**

### 30.1 Environment

| Item | Value |
|---|---|
| OS | Windows 11 Home 10.0.26200 (AMD64), `Windows-11-10.0.26200-SP0`, cp932 locale |
| Python | 3.12.14 (MSC v.1944, 64 bit), fresh `.venv` from `requirements.lock.txt` + `pip install -e ".[test]"` |
| PyMuPDF / fontTools / uharfbuzz (HarfBuzz) / pypdf / pytest | 1.27.2.3 / 4.64.0 / 0.55.0 (14.2.1) / 6.10.0 / 9.1.1 |
| Poppler | `pdftoppm` 26.07.0. It was already installed on the machine (the §25 binary) and was added to `PATH` for this session only; nothing was installed. |
| LibreOffice | unavailable; not part of this validation (§29.5) |
| Checkout | clean worktree at `769a0a9`; `result.json` records `dirty: false` |
| Runtime digest | `4b6f9925159160c9ad63cee19f082728bfadf8c70548678fd89d40a3665659e3`, before the run, after the run and after the full suite (unchanged) |

### 30.2 Command (exactly the §29.5 / README sequence)

```powershell
$P = ".\.venv\Scripts\python.exe"
$H = "-m", "evaluations.semantic_lifecycle.windows_validation"
$W = "tmp\semantic-underline-windows"
$RUN = "evaluations\semantic_lifecycle\runs\windows-underline-synthetic-$(Get-Date -Format yyyyMMdd)"
& $P @H prepare-fonts --output-dir "$W\fonts" --from-a C:\Windows\Fonts\arial.ttf --from-b C:\Windows\Fonts\times.ttf
& $P @H prepare-synthetic --output-dir "$W\src"
& $P @H prepare --source-pdf "$W\src\source.pdf" --spec "$W\src\spec.json" --font-a "$W\fonts\font-a.ttf" --output-dir "$W\prep"
& $P @H run --mode underline --pdf "$W\prep\document.pdf" --sidecar "$W\prep\shared-flow.json" `
  --font-a "$W\fonts\font-a.ttf" --font-b "$W\fonts\font-b.ttf" --output-dir $RUN
```

Neither the work directory nor the run directory existed before. All four commands exited 0, and the harness ran once.

| Input | SHA-256 |
|---|---|
| font A (unhinted A/B/space subset of `arial.ttf`, source `b3658ead…6476a`) | `ab1a678f8f5ddf565ac313e203f7fde276cc75a10cee7d68b223025eaf8317f2` |
| font B (same, from `times.ttf`, source `931c5de5…d58c5`) | `bf5783fb6fded1696e9350a1cd7a611cf4398c941cc17819a906ce8c7a30c9bc` |
| synthetic source PDF | `05d24a2bda0181ea8da694ad73470e53cb8814a6cee18fa13d39d95caf76f652` |
| prepared `document.pdf` / `shared-flow.json` | `037893f6…f9971` / `6637f79c…e43f0` |

The font subsets are byte-identical to the §25 Windows run.

### 30.3 Result

Run: `evaluations/semantic_lifecycle/runs/windows-underline-synthetic-20261006/`.

- **Verdict `PASS`, exit 0**, executed 10:06:32 → 10:06:50 UTC.
- `windows_execution: true`, `mode: underline`, `validation_kind: semantic-underline-lifecycle`,
  `underline_runtime: true`.
- Target: slot `slot-0`, page 1, `pdfengine-shared-flow-2`, generated font `/PRF1`.

All 21 stages **PASS**: preflight, baseline, confirm, text_baseline, add, noop_1, noop_2, edit_remap, recipe, style,
font, publish_a, remove_from_bundle_a, publish_b, negatives, tamper, continuity, text_only_control, raster_mupdf,
raster_poppler (optional; it ran and passed) and inputs_preserved.

| Stage | Recorded evidence |
|---|---|
| add | Request `reinterpret` / `decorations.add`; classification **E**; version **2 → 3** (`version 2`, `next_version 3`); authority preserved (source-confirmed); decoration `current:0`, `[0,3)`, affinities outside/outside, recipe `13/120` / `7/120`. The text group SHA is unchanged (`ea99442e…` = text_baseline). Paint transition `insert`. |
| noop_1 / noop_2 | D, 3 → 3. PDF bytes, owned body, operator count, owner block SHA, semantic payload + current authority + derived state, decorations, rectangles and the MuPDF raster are all identical. Both no-ops give PDF `7b271768…` = add. |
| edit_remap | D, text `A B` → `AA B`, decoration **`[0,3)` → `[0,4)`**. Rectangle `20.017578 58.7 39.359375 58` → `20.017578 58.7 47.363281 58` (see §30.4). |
| recipe | E, recipe `13/120,7/120` → `1/10,1/20`. Text and text group unchanged; horizontal endpoints unchanged; vertical geometry changed (`58.8 / 58.2`); authority preserved; version 3. |
| style | E. The semantic diff is exactly `style.font_size` 12 → 13 and `style.tracking` 0 → 1/4. Decorations preserved; em geometry error 0.0 pt. Authority becomes `caller-confirmed-current-semantic`, as §21.8 specifies for an explicit style change. |
| font | E. Diff exactly `font.sha` A → B. Provider and generated font are font B; recipe and ranges preserved; vertical geometry unchanged (`58.7 / 58.05`); endpoints on the new glyph origins; right edge 50.393555 → 51.447266; version 3. |
| publish_a | Candidate `u07-font-b` → `artifacts/bundle-a` = exactly `document.pdf` + `shared-flow.json`. Staged and public reopen `restored`; candidate bytes = published bytes (PDF and sidecar); fresh process `restored`; version 3; decoration restored; candidate/published MuPDF raster identical. |
| remove_from_bundle_a | Input is **`artifacts/bundle-a/document.pdf` + `artifacts/bundle-a/shared-flow.json`** (not the candidate). E `decorations.remove`, version stays 3, `decorations = []`, paint group absent, text group unchanged (`398918b4…`). |
| publish_b | Bytes identical, staged/public/fresh reopen `restored`, version 3, `decorations = []`, text-only physical body, **bundle A unchanged**. |
| negatives | Refused: A.pdf + B.json → `source output ownership: shared flow PDF revision changed`; B.pdf + A.json → the same reason |
| tamper | Refused: resealed decoration recipe tamper → `semantic version 3 body is not the canonical text and underline body`; paint geometry tamper → `source output ownership: shared flow PDF revision changed` |
| continuity | Creation evidence (registry, observations, provider, `source_model_sha256`, `source_snapshot_sha256`, `created_from`, `contract_sha256`) constant; owner marker and `created_from` constant over 12 revisions |
| text_only_control | The same edit/style/font requests on version 2, never decorated: its final text group equals the removed revision's |
| raster_mupdf (144 dpi) | baseline ≠ add; add = noop 1 = noop 2; recipe, style and font each ≠ previous; removed = text-only control; candidate A = bundle A; candidate B = bundle B |
| raster_poppler (144 dpi) | baseline ≠ add; add = noop 1 = noop 2; candidate A = bundle A; candidate B = bundle B; removed = text-only control |
| inputs_preserved | The prepared PDF, sidecar, font A and font B SHA-256 values are the same at the end as at the start |

Final bundle state: semantic version 3, `decorations = []`, paint group absent.

### 30.4 Windows geometry vs the cloud synthetic values

§29.2/§29.3 quote the cloud synthetic font's rectangle edges (`41.6 → 48.8`, left edge 20). The Windows run uses
the Arial/Times subsets, so the numbers differ. The harness checks the §27.7 range remap and the §27.8 geometry rule,
not those literal values. The Windows values follow §27.8 exactly from font A's metrics (upem 2048; advances A 1366,
space 569, B 1366; A's left side bearing −3):

- left edge = first glyph origin = 20 + 3/2048 · 12 = **20.017578** (the layout inset of A's negative bearing);
- `A B` right edge = origin + 1366 + 569 + 1366 units at 12 pt = **39.359375**;
- `AA B` right edge = **47.363281**.

These were recomputed independently from the font tables. The vertical values (58.7 / 58, then 58.8 / 58.2 for the
recipe) equal the cloud ones, because the recipe is em-relative and the size is the same. This is a fixture-font
difference, not a deviation.

### 30.5 Full suite on Windows

Whole `tests/` tree in four file-balanced parallel shards, with Poppler on `PATH`, after the validation run (the run
directory was not touched): **1,853 passed, 7 skipped, 0 failed**. Skips: 2 AES provider unavailable, 5 external corpus
not downloaded (both environment-only). Total 1,860 = Linux 1,841 passed + 19 skipped (§29.4). The Windows-only
Arial/Noto/Poppler tests that are skipped on Linux ran here and passed. There are no Windows-specific failures.

### 30.6 Evidence committed and verdict

- Committed: `evaluations/semantic_lifecycle/runs/windows-underline-synthetic-20261006/result.json` and `report.md`,
  as generated, with no hand edits. Both were checked for usernames, home directories and absolute local paths: none
  are present.
- Not committed: PDFs, fonts, PNGs, `logs/harness.log`, bundles, `tmp/`, `.venv`.

**WINDOWS NARROW SEMANTIC UNDERLINE LIFECYCLE VALIDATED.** All 21 stages pass on Windows, including the optional
Poppler stage, with exit 0. The authority, ownership, Transaction and publication contracts hold as in the cloud run:

- the explicit v2 → v3 add;
- canonical no-ops;
- the remap;
- the em recipe, style and font transitions;
- publication A/B through `os.rename`, with bundle A reused as the next input and left unchanged;
- the remove that keeps version 3;
- mixed-pair and tamper refusals;
- creation-evidence continuity;
- dual-renderer raster expectations;
- input preservation.

Scope is unchanged: the narrow single owned slot. This is not general paint support. LibreOffice is still a separate,
not-performed axis (§25.3), and source underline adoption is still refused (§27.11).

## 31. Semantic strikeout contract — 2026-10-06

Starting main `778b1587c329eb7d8c23b055c5841fdef8664ce8` (PR #52 merged: WINDOWS NARROW SEMANTIC UNDERLINE LIFECYCLE
VALIDATED). §1–§30 are preserved. **This section is design/evidence only.** There is no strikeout runtime and no
`pdfeditor/` change. The runtime digest is unchanged: `4b6f9925159160c9ad63cee19f082728bfadf8c70548678fd89d40a3665659e3`.

**Verdict: SEMANTIC STRIKEOUT CONTRACT READY** (§31.16). Evidence: `tests/test_semantic_strikeout_contract.py`.

### 31.1 Why strikeout, and what it does not bring

A strikeout is the underline's physical model with a different vertical position:
- one horizontal, axis-aligned, opaque rectangle per decoration per line;
- filled with the current text fill;
- exact em-relative geometry from a caller-confirmed recipe;
- generated owned paint inside the existing owned body;
- current semantic authority.

It needs no z-order below the text, no transparency and no blend state, so highlight-class questions are not opened.
It crosses the glyphs, so the z-order and obstacle questions are checked explicitly (§31.7, §31.10).

### 31.2 Versioning: semantic record version 4 (option B)

| | A: extend version 3 with `strikeout` | **B: new version 4** |
|---|---|---|
| Frozen contract | changes the meaning of a Windows-validated version (§30) | version 3 keeps `kind = underline` only |
| Backward compatibility | an old v3 reader would see an unknown kind inside a valid v3 record | old v3 records are untouched; v3 code keeps refusing `strikeout` (evidence: a resealed v3 claiming a strikeout, or an underline recipe above the baseline, is refused today) |
| Explicit upgrade | none, so the change is silent | only the first explicit strikeout `add` enters version 4 |
| Reopen determinism | the meaning of v3 depends on the code version | the version names the decoration vocabulary |
| Sidecar meaning | ambiguous across releases | exact |
| Future kinds | every kind re-opens v3 | the next kind takes the next version under the same rule |

**Decision: B.** Version 4 = version 3 record keys and payload keys, with `kind ∈ {underline, strikeout}`. Nothing
else is added: no sidecar field, no schema string change (`pdfengine-shared-flow-3`), no new sidecar.

**Migration matrix** (evidence: `test_version_migration_matrix`; rows that exist today are compared with the
production planner in `test_matrix_rows_that_exist_today_match_the_production_plan`):

| Current | Request | Result |
|---|---|---|
| v1 | any decoration request (underline or strikeout) | **REFUSE** (confirm v1 → v2 first, unchanged) |
| v2 | underline `add` | **UPGRADE → v3** (unchanged, the shipped route) |
| v2 | strikeout `add` | **UPGRADE → v4** |
| v2 | `remove` / `recipe` | **REFUSE** (nothing to reference) |
| v3 | underline `add` / `remove` / `recipe` | **KEEP v3** |
| v3 | strikeout `add` | **UPGRADE → v4** (existing underlines carried unchanged) |
| v3 | strikeout `remove` / `recipe` | **REFUSE** (no strikeout exists; stale CAS) |
| v4 | underline or strikeout `add` / `remove` / `recipe` | **KEEP v4** (also when the last strikeout is removed) |
| v1–v4 | open, `reopen`, `save`, `reflow`, `edit`, style/font E | **KEEP** (never an upgrade or downgrade) |

- **v2 → v4 directly:** the version is decided by the kind of the first explicit add, by the same mechanics as
  v2 → v3. Nobody has to add an underline first (`test_strikeout_from_version_2_needs_no_underline_first`).
- **An underline add never moves a record to v4.** The Windows-validated v2 → v3 route is unchanged, so an
  underline-only state can be v3 or, after a strikeout was removed, v4. A v4 record with only underlines has the same
  body bytes as the v3 one. Re-sealing it as v3 is semantically equal and cannot hide a strikeout, because a v3 record
  refuses the strikeout kind and an underline recipe cannot describe a rectangle above the baseline (§31.6).
- No automatic v3 → v4, no open/save upgrade, no downgrade, no direct decoration on v1.

### 31.3 Schema

Exactly the §27.5 record:

```json
{"id": "current:1", "kind": "strikeout", "start": 3, "end": 4,
 "start_affinity": "outside", "end_affinity": "outside",
 "recipe": {"offset_em": "-3/10", "thickness_em": "1/20"}}
```

The rules are those of §27.5, unchanged:
- the same keys, exactly;
- `current:i` positional IDs that are checked and never chosen;
- logical `[start, end)` grapheme-boundary ranges with at least one visible character;
- outside/outside affinity;
- sorted and disjoint, with touching allowed.

Kinds: version 3 accepts `underline` only, version 4 accepts `underline` and `strikeout`. Anything else is refused,
including `highlight`, `overline`, case variants and extra keys such as `color`, `z_index` or `source_id`.

### 31.4 Recipe and bounds

The recipe keys stay `offset_em`, `thickness_em`, as canonical exact rationals. The bounds depend on the kind:

| Kind | `offset_em` | `thickness_em` |
|---|---|---|
| underline | `0 ≤ offset ≤ 1` (unchanged) | `0 < t ≤ 1` |
| strikeout | `−1 ≤ offset < 0` | `0 < t ≤ 1` |

- A recipe change is validated against the **referenced** decoration's kind. A strikeout cannot take an underline
  recipe, and the reverse is also refused.
- A kind change is not an action: it is a `remove` followed by an `add`.
- The evidence recipe `{−3/10, 1/20}` is a caller-confirmed candidate for the evidence, not a standard. At 12 pt it
  spans 3.6 → 3.0 pt above the baseline and crosses both synthetic glyph boxes (0 → 7.2 pt).
- Font tables (OS/2 strikeout position/size, renderer heuristics) are **never** authority. A tool may propose a value
  from them; the stored recipe is what the runtime uses.

### 31.5 Geometry

The §27.8 rule is unchanged for both kinds (page y-down, exact):

```
upper     = glyph baseline (line baseline + rise) + font_size · offset_em      (offset < 0 → above the baseline)
thickness = font_size · thickness_em
left/right = first painted glyph origin / last painted glyph origin + advance (per line, boundary spaces trimmed)
```

**Line-box rule (R):** `baseline − ascent ≤ upper` and `upper + thickness ≤ baseline + descent`, checked exactly per
rectangle. With the synthetic 0.6 em ascent:
- `−3/5` is admitted and `−31/50` is refused (top overflow);
- `{−1/20, 1/4}` is admitted and `{−1/20, 3/10}` is refused (a strikeout crossing the baseline into the descent);
- both hold at 12, 37/3 and 24 pt.

Evidence for the transitions:
- **Size:** offset and thickness scale exactly with the size.
- **Rise:** −1 moves the baseline and the rectangle by exactly −1.
- **Horizontal scale:** never scales the vertical geometry.
- **Endpoints:** they follow tracking, Tw, confirmed edges and the font B advances (e.g. 37/3 pt, scale 4/5,
  tracking 1/4: right edge `20 + 3(a + 1/4) + a`). Tw and the equivalent edge give identical bytes.
- **Font A → B:** keeps the vertical geometry.
- **Newline / wrap:** split one strikeout into per-line rectangles. The newline and trailing spaces are not painted.

### 31.6 Physical separation of the kinds

The semantic bounds alone are not enough, because serialization rounds to 6 places: a strikeout at `−1/10⁹ em` would
write the same `yu` as an underline at offset 0. Rule (R): **a strikeout's serialized upper edge must be strictly
above its serialized glyph baseline.**

An underline (offset ≥ 0) is at or below the baseline by construction, since the decimal policy is monotone. So an
underline and a strikeout can never serialize the same rectangle, and the physical bytes identify the kind of every
rectangle relative to its line's baseline.

Evidence (`test_strikeout_and_underline_never_serialize_the_same_rectangle`):
- `−1/10⁹` is refused;
- `−1/1000` is admitted and differs from an underline at offset 0;
- every admitted strikeout is strictly above, and every underline is at or below.

### 31.7 Body grammar, order and paint group

- **Grammar:** the §27.3 rectangle grammar and the one serializer (`semantic_paint.paint_group`) are reused unchanged.
  The production `body_grammar` already accepts `text group + strikeout group`, and the v2 grammar still refuses it.
  There is no strikeout-specific PDF operator.
- **Order: text group, then paint group** (unchanged). Evidence `test_text_then_strikeout_is_pixel_equivalent_and_keeps_the_text_prefix`:
  - the strikeout crosses the A and B glyph inks;
  - text-first and paint-first are **pixel-identical** at MuPDF 144 dpi (one opaque fill colour: source-over
    commutes);
  - the text traces are identical;
  - the strikeout differs from the text-only page;
  - text-first keeps the text group a byte prefix.

  Paint-first stays refused by the grammar.
- **One paint group** for all kinds. A second group would add nothing: the owner, block SHA, Transaction and
  canonical check already cover any number of rectangles in one group. It would only add a second grammar slot and
  ordering rule.
- **Rectangle order:** decorations in canonical order (sorted by `(start, end)`, i.e. `current:i`), then lines top to
  bottom, one `f` per rectangle. Kinds need no tie-break, because disjoint non-empty ranges never share `(start, end)`.
  Mixed example `underline [0,1)`, `strikeout [3,4)` gives `current:0` underline below the baseline, then `current:1`
  strikeout above it, in one `q 0 g … Q`.

### 31.8 Mixed kinds, overlap, touching

- **One** sorted, disjoint decoration list across kinds. Mixed disjoint kinds are allowed; touching ranges are allowed
  and never merged (`underline [0,1)` + `strikeout [1,2)` gives adjacent rectangles).
- **Overlap across kinds is refused:** same range, partial overlap and nesting, in validation and in E `add`. So
  "underline and strikeout on the same characters" is out of scope. It needs an overlap/stacking model (layers,
  z-index, per-character decoration sets), which is a recorded **future blocker** and is not introduced here.

### 31.9 Transitions and empty lifecycle

- **N:** all decorations exact.
- **D:** the production §27.7 membership rule is kind-independent. The evidence is exhaustive over `AA B`:
  - every edit gives the same ranges for a strikeout as `semantic_paint.remap` gives for an underline;
  - kinds and recipes are carried;
  - mixed kinds remap independently.

  There is no strikeout-specific remap. Empty text terminates every decoration.
- **E:** the same `add` / `remove` / `recipe` actions, one per request, never mixed with other changes. The `(id,
  start, end)` CAS is unchanged and does **not** include `kind`:
  - within one revision, a positional ID plus its exact range already identify exactly one decoration, because ranges
    are disjoint across kinds;
  - a stale request across revisions is the same hazard class as for underline alone (remove then re-add on the same
    range), already bounded by the revision the caller supplies;
  - adding `kind` would fork the version 3 request shape for no gain.
- **R:**
  - unknown kind, caller ID, `inside` affinity, overlap;
  - source/`adopt`/path fields, `revive`;
  - inferred (`auto`) or missing recipe;
  - wrong offset sign for the kind, non-canonical or out-of-bounds values;
  - line-box violation, physical-separation violation;
  - no visible character, zero-length range;
  - stale CAS;
  - a version-incompatible request (see the matrix).
- **Empty:** collapse or no visible character terminates; there is no dormant state and no revival (§27.10 unchanged).

### 31.10 Ownership, verifier, Transaction, publication (reuse)

- **Owner witness:** unchanged. The strikeout group is inside `[begin, end]`, so the block and program SHA cover it.
  The production witness with the injected v3 body grammar accepts a strikeout body with the unchanged witness shape.
  No new witness field or marker domain.
- **Verifier:** the §27.15 / §28.6 steps apply unchanged, with version 4 selecting the kind set:
  - decorations → exact canonical rectangles → exact body bytes;
  - element classification (count, order, proven, one fill paint each, bounds ≤ 0.002 pt, no relation).

  Evidence: in a mixed underline + strikeout body between foreign paint, the catalog paths inside the owned body are
  proven one-to-one, in order, to the canonical rectangles of both kinds.
- **Transaction:** unchanged machinery:
  - `paint_insertions`, `paint_changes`, `consumed_paths`;
  - exact old/new rectangles;
  - the planned area (glyph inks ∪ old ∪ new rectangles bounds every changed pixel for strikeout add, remove, recipe,
    edit, size/scale/tracking, font and mixed-kind add);
  - obstacles.

  The strikeout crosses the island's **own** glyphs, which are selected and are not obstacles. Its own old strikeout
  is excluded only by its proven seqno; without that exclusion the old strikeout crossing the new ink is refused.
- **Foreign paint:** still an obstacle exactly where it is hit. A foreign rectangle in the inter-glyph gap passes the
  glyph-only check and is refused once the new strikeout crosses it. Foreign vectors, images, glyphs and pixels
  outside the mask are not relaxed.
- **Source adoption: refused.** Horizontal lines in a source PDF are never detected, classified, inferred, adopted or
  migrated. A foreign path at a strikeout position stays foreign. The first strikeout is always an explicit `add`.
- **Publication:** L3 unchanged (`open_semantic_flow` dispatches the version).

### 31.11 Evidence (`tests/test_semantic_strikeout_contract.py`, 92 tests)

The prototypes in the test file:
- `recipe_v4`, `validate_v4`, `remap_v4`, `reinterpret_v4`, `rectangles_v4`;
- `paint_group_v4`, which wraps the production serializer;
- `next_version`.

On underline-only input they equal the production `validate`, `rectangles`, `paint_group` and `remap` (exhaustive
remap check).

| Area | Tests |
|---|---|
| implementation-before state | production refuses strikeout in `validate`, `recipe`, plan and writer (v2 and v3 bundles, no candidate written) and refuses a version 4 record; v3 cannot absorb a strikeout (resealed kind / recipe) |
| versioning | 23-row migration matrix; 6 rows checked against the production planner; v2 → v4 without an underline |
| schema / bounds | schema reuse, extra keys and unknown kinds refused, 14 recipe-bound cases, `auto` / missing recipe |
| geometry | negative offset above the baseline crossing the glyphs (exact bytes `20 63.6 m 41.6 63.6 l 41.6 63 l 20 63 l h f`), line-box top/bottom overflow at 3 sizes, physical separation |
| style / font | size and rise only for the vertical geometry; scale ignored; endpoints vs tracking/Tw/edge/font B; Tw = edge bytes for 3 range sets; font A → B vertical unchanged |
| lines / no-op | newline/wrap per-line rectangles; canonical bytes fixed across 6 no-op groups × (strikeout, underline, mixed) |
| mixed / D / E / R | one group in range order; overlap refused (3 shapes); touching kept separate; kind-independent D remap; E add/remove/recipe with kind-checked recipes; 13 R refusals; line-box R |
| body / z-order / ownership / Transaction | production v3 grammar + unchanged witness shape; text-first = paint-first pixels, text prefix; owned paths proven one-to-one in a mixed body between foreign paint; obstacles (own glyphs, own old paint by seqno, foreign gap paint); planned area for 7 transition pairs |

Validation (Linux, Python 3.13.16, PyMuPDF 1.27.2.3):
- new file: **92 passed**;
- focused: **386 passed** (`test_semantic_strikeout_contract`, `test_semantic_underline_contract`, `test_semantic_underline`, `test_paint_reassessment`, `test_semantic_writer`, `test_source_ownership`);
- full suite on the final HEAD: **1,933 passed, 19 skipped, 0 failed** (main: 1,841 passed; +92 new; the 19 skips are the same environment-only skips: Windows Arial / Noto Sans JP absent, external corpus not downloaded, one Windows-path Poppler regression);
- runtime digest unchanged.

### 31.12 Backward compatibility (all required)

- **v1, v2:** behaviour unchanged.
- **v3 underline bundles:** open, save and every underline transition stay version 3, with byte-identical behaviour.
  The Windows-validated §30 lifecycle keeps its meaning, and no v3 sidecar is rewritten.
- **v3 code:** keeps refusing the strikeout kind.
- **Shared-flow v2:** the opener and grammar are unchanged.

### 31.13 Final contract

| # | Item | Decision |
|---|---|---|
| 1 | semantic version | 4 (v3 stays underline-only) |
| 2 | v2 → strikeout | explicit strikeout `add` → v4 |
| 3 | v3 → strikeout | explicit strikeout `add` → v4, underlines carried |
| 4 | schema | the §27.5 record, unchanged |
| 5 | kinds | v3 `{underline}`, v4 `{underline, strikeout}` |
| 6 | recipe | `{offset_em, thickness_em}`, caller-confirmed exact rationals |
| 7 | offset bounds | underline `[0, 1]`, strikeout `[−1, 0)`, plus serialized strict separation for strikeout |
| 8 | thickness bounds | `(0, 1]` for both |
| 9 | geometry | §27.8 rule unchanged, line-box rule exact |
| 10 | fill | current text fill, set by the group; no colour field |
| 11 | body order | text group, then paint group |
| 12 | paint groups | one |
| 13 | rectangle order | canonical decoration order (`current:i`), then lines |
| 14 | mixed kinds | allowed when disjoint |
| 15 | overlap | refused across kinds (future blocker: stacking model) |
| 16 | touching | allowed, never merged |
| 17 | N/D/E/R | §31.9 |
| 18 | empty | terminate, no dormant state, no revival |
| 19 | adoption | refused |
| 20 | verifier | §27.15 steps, kind set by version |
| 21 | owner witness | unchanged |
| 22 | Transaction | unchanged |
| 23 | publication | unchanged |
| 24 | next scope | §31.15 |

### 31.14 Remaining limits (stated, not blockers)

- Underline and strikeout on overlapping characters is out of scope (stacking model, §31.8).
- Ranges differing only in trimmed boundary spaces paint identical bytes (§27.6, unchanged).
- Windows has validated version 3 only. A version 4 runtime needs its own Windows run (harness mode) after
  implementation.

### 31.15 Next PR: narrow semantic strikeout runtime (version 4) — exact scope

The same narrow scope as §28: one paragraph, one owned slot, one region, one body style, left, static TT,
A/B/space/newline, default context.

1. **Payload:** semantic record version 4 (`DECORATED_V4 = 4`); `_record` and `_payload` accept it; the kind set is
   selected by version (v3 `{underline}`, v4 `{underline, strikeout}`).
2. **`semantic_paint`, generalized in place (no rename to `semantic_decoration`):**
   - `recipe(value, kind)` with the §31.4 bounds;
   - `validate(text, decorations, kinds)`;
   - kind-aware `rectangles`;
   - the strikeout strict-separation check before `paint_group`;
   - the `remap` and `reinterpret` validation take the kind set.
3. **`plan_semantic_transition`:** the §31.2 matrix and `next_version`. A strikeout `add` from v2/v3 → 4; an
   underline add keeps the v2 → v3 route; v3 refuses strikeout references; v1 refuses.
4. **v4 verifier:** `body_grammar` injected for versions 3 and 4; canonical body and element classification
   unchanged.
5. **Writer:**
   - rectangles of all kinds;
   - old/new rectangles from the verified state;
   - `_old_owned_paint` and `_plan_island` for versions 3 and 4;
   - rebind with the grammar for versions 3 and 4.
6. **Transaction:** unchanged machinery; tests for strikeout insert/replace/remove, the planned area, the own-glyph
   crossing, own old strikeout exclusion and foreign gap paint.
7. **Tests:**
   - lifecycle v2 → v4 and v3 → v4 (underlines carried), then no-op ×2, edit remap, recipe, style, font, remove,
     mixed disjoint, touching;
   - overlap / R refusals;
   - physical separation;
   - tamper (semantic and body), v3 refusing v4 content, version matrix through the production planner;
   - publication A/B and reopen;
   - MuPDF/Poppler raster;
   - failure isolation;
   - v1/v2/v3 compatibility (all existing tests unchanged).
8. Then a Windows strikeout validation (harness mode) as a separate PR.

Out of scope there: overlap/stacking, highlight, transparency, colours, other kinds, source adoption, cross-page,
multiple slots.

### 31.16 Verdict

**SEMANTIC STRIKEOUT CONTRACT READY.** All of the following are settled and evidenced:
- versioning (v4, explicit routes, no implicit change);
- geometry authority (the §27.8 rule, caller recipe, no font tables);
- kind-specific recipe bounds and physical separation;
- canonical order (text → one group → `current:i` → lines, z-order evidence);
- the mixed-kind policy (disjoint, touching kept, overlap refused);
- v3 backward compatibility (v3 refuses strikeout; routes unchanged);
- verifier, owner witness, Transaction and publication reuse;
- no source adoption;
- 92 evidence tests pass.

The next PR is the narrow semantic strikeout runtime (§31.15). It is not started here.

## 32. Narrow semantic strikeout runtime implementation — 2026-10-06

Starting main `beb567498ef0354337ecb9171dfac6736af724d7` (PR #53 merged: SEMANTIC STRIKEOUT CONTRACT READY). §1–§31
are preserved. **This section implements §31 as written.** Runtime digest: `4b6f9925…59e3` → `98e71404148d8e69779103a27922c40a65c8f04fed04b2d5adbb8a40d34c23d6`.

**Verdict: NARROW SEMANTIC STRIKEOUT RUNTIME COMPLETE** (§32.10). This covers the caller-declared strikeout on the
single owned semantic slot, alone or disjoint from underlines. It is not paint runtime in general, not overlapping
decorations, not highlight, and not source strikeout editing.

### 32.1 Implementation (three modules, generalized in place)

| Module | Change |
|---|---|
| `semantic_paint` (not renamed) | `STRIKEOUT`, `UNDERLINE_KINDS` (v3, the default everywhere), `MIXED_KINDS` (v4). `recipe(value, kind)` applies the kind-specific bounds. `validate`, `remap` and `reinterpret` take an explicit `kinds` parameter, with no global mode. `_items` gives (kind, glyph baseline, rectangle) and `rectangles` is unchanged for callers. New: `separation(plan, style, decorations, page_top)` (§31.6) and `serialize` (separation, then the one `paint_group`). The range semantics (remap membership, E actions, CAS) are not duplicated. |
| `semantic_layout` | `DECORATED_VERSION = 3` is kept. New `MIXED_DECORATION_VERSION = 4` and `DECORATION_KINDS = {3: UNDERLINE_KINDS, 4: MIXED_KINDS}`. `_record`, `_payload` and `_verify` accept 4 with its kind set. `_next_version` implements the §31.2 matrix. `plan_semantic_transition` reports `next_version`, `next_underline_rectangles` (underline only, its v3 meaning) and the new `next_decoration_rectangles`, and refuses line-box and separation violations before any write (MediaBox top read from the current PDF). |
| `semantic_writer` | Every v3 path now applies to versions 3 and 4: old rectangles from the verified current state, `_old_owned_paint`, owned-body grammar, new rectangles, rebind grammar. Serialization runs `paint.separation`, then `paint.paint_group`. |
| `transaction`, `source_ownership`, `shared_flow`, `composition`, `semantic_publication` | unchanged |

Schema string `pdfengine-shared-flow-3` unchanged. Persistent artifacts: PDF + `shared-flow.json`.

### 32.2 Versions and migration

| | Behaviour |
|---|---|
| v1 | unchanged; every decoration request refused ("confirm version 1 as version 2 first") |
| v2 | unchanged; underline `add` → v3 (unchanged route); **strikeout `add` → v4**; references refused |
| v3 | unchanged; underline-only; underline actions keep v3; **strikeout `add` → v4** with the underlines carried exactly; strikeout references refused; a resealed v3 claiming a strikeout, or a v4 relabelled v3, is refused (`unsupported decoration kind`) |
| v4 | every underline/strikeout action keeps v4; removing the last strikeout keeps v4; no downgrade |
| all | open, reopen, save, reflow, edit, style and font E keep the version |

The production planner is checked against **every row** of the §31.2 matrix (33 cases on production v1/v2/v3/v4
bundles), and each decoration row agrees with the §31 contract table.

### 32.3 Geometry, separation, body

- **Recipe:** underline `0 ≤ offset ≤ 1`, strikeout `−1 ≤ offset < 0`, thickness `0 < t ≤ 1`, canonical exact
  rationals. A recipe change is checked against the referenced decoration's kind.
- **Geometry:** the §27.8 rule unchanged. The v2 → v4 add on `A B` at 12 pt writes exactly
  `q 0 g\n20 63.6 m 41.6 63.6 l 41.6 63 l 20 63 l h f\nQ\n` (3.6 → 3.0 pt above the baseline, crossing A and B).
  - size, rise and recipe are the only vertical inputs: 37/3 pt scales exactly; rise −1 moves by exactly −1;
    horizontal scale 4/5 is ignored;
  - endpoints follow tracking/Tw/edges/font B;
  - a recipe change keeps x;
  - font A → B keeps y.
- **Separation:** `−1/10⁹` is refused by the planner and the writer, and nothing is written. `−1/1000` is admitted.
- **Body:** the L2 text group, then one paint group with both kinds in `current:i` order (the v3 → v4 example writes
  the underline rectangle, then the strikeout rectangle). There is no new operator, group or marker domain. The
  newline case gives one rectangle per line.

### 32.4 Mixed decorations and N/D/E/R

- **Mixed lifecycle on `AA B`:**
  - underline `[0,1)` + strikeout `[1,2)` (touching, adjacent rectangles, never merged);
  - remove the underline, then add it again (body byte-identical to before);
  - remove the strikeout, then add a strikeout at `[3,4)`.

  Everything stays v4 after the first strikeout.
- **Overlap** (same range, partial, nesting) is refused across kinds.
- **D:** remap is kind-independent. The v3 → v4 edit keeps the underline at `[0,1)` (insert at its end is outside)
  and shifts the strikeout `[2,3)` → `[3,4)`. An empty edit terminates every decoration and keeps v4.
- **E:** add/remove/recipe with the unchanged `(id, start, end)` CAS (no `kind`).
- **R (22 cases through the planner and the writer, inputs untouched, no candidate written):**
  - unknown kind, caller ID, `inside` affinity;
  - overlap (4 shapes);
  - `source_id`, adopt, revive;
  - `auto` / missing recipe, wrong offset sign (both kinds), out-of-bounds thickness;
  - line-box overflow, separation failure;
  - no visible character, stale CAS;
  - kind-checked recipe changes (both directions);
  - mixed request.

  Plus a v3 strikeout reference and v1 strikeout.

### 32.5 Verifier, owner witness, writer, Transaction

- **Verifier:** the §27.15 steps for versions 3 and 4, with only the kind set differing. A resealed v4 is refused for
  kind swap, recipe sign, recipe value, kind order, range, missing and extra. On the production verifier
  (`_island`, owner witness rebound to the tampered bytes), body tampers are refused: geometry, rectangle order,
  missing, extra, a strikeout rewritten at underline height, paint-first. Stale PDF/sidecar pairs are refused.
- **Owner witness:** the shape is unchanged; the block SHA binds the mixed body.
- **Transaction:** reused unchanged.

  | Case | Declaration |
  |---|---|
  | first strikeout | `paint_insertions` (1) |
  | no-op | replacement (1 → 1) |
  | removal | `None` |
  | v3 → v4 | the old underline paint is replaced by 2 paints |
  | mixed removal | `[1, None]`, with 2 `consumed_paths` |

  - The planned area contains every old and new rectangle.
  - With only the glyph-ink box as the area, a strikeout add/remove is refused: it runs over the space between A
    and B, outside every glyph ink.
- **Own glyphs:** the strikeout crosses the island's own A/B ink and is admitted (they are selected).
- **Own old strikeout:** excluded **only** by its proven seqno; without the exclusion the next save is refused.
- **Foreign paint:**
  - a confirmed `unrelated`/`fixed-to-page` foreign rectangle in the inter-glyph gap at strikeout height refuses the
    strikeout add (`filled vector`), while a save and an underline add (below the baseline) are admitted;
  - far foreign paint keeps its bytes and order through add and remove;
  - nothing is adopted.

### 32.6 Publication, no-op, raster

- **Publication:** bundle A (v3 → v4 mixed) is published; the next revision from bundle A removes the strikeout;
  bundle B is v4, underline-only.
  - candidate and published bytes are identical;
  - bundle A is unchanged;
  - mixed pairs are refused;
  - a fresh process reopens both with version 4, decorations, provider, owner marker and a canonical body.
- **No-op (8 groups):** strikeout-only, after edit/style/font, v3 → v4 mixed, touching mixed, removed and
  underline-only v4. In each, these are identical: owned body, operators, semantic payload/authority/derived,
  version, owner block SHA, rectangles and MuPDF raster. The PDF bytes are identical too. The exception is the first
  save after a font change, which compacts the PDF once (also on plain v2 text-only islands: 4062 → 4045 bytes, the
  same fonts). The following no-ops are then byte-identical.
- **MuPDF 144 dpi:**
  - text-only ≠ strikeout; strikeout = no-op 1 = no-op 2;
  - recipe, style and font each ≠ previous;
  - strikeout removed = text-only;
  - removing the strikeout from the mixed body changes pixels; mixed ≠ underline-only ≠ text-only;
  - candidate = published.
- **Poppler 144 dpi:** strikeout = no-ops, ≠ text-only, removed = text-only, mixed ≠ underline-only.

### 32.7 Failure isolation

Failures are injected at 9 points:
- the planner;
- recipe validation;
- the writer's geometry, separation and serializer;
- the Transaction paint declaration;
- owner rebind, semantic rebind and candidate reopen.

Each one leaves the source PDF, the sidecar and the workspace unchanged. A publication failure leaves the previous
bundle unchanged and creates no destination.

### 32.8 Changes to existing tests (contract-mandated)

§31.2 turns a strikeout `add` on v2/v3 into the v4 route, and version 4 into a known record. Three existing tests
asserted the pre-§31 state and were adjusted minimally:

- the "other decoration kinds" refusal case in `test_semantic_underline.py` and `test_semantic_underline_contract.py`
  now uses `highlight` (still unknown in every version). A strikeout add on v3 is the upgrade route, refused only by
  its recipe;
- `test_decorations_exist_only_in_semantic_version_3` → `…_versions_3_and_4` (v4 accepted, v5 refused).

No v3 expected value, geometry or byte assertion changed.

`test_semantic_strikeout_contract.py` now calls the production functions: its `*_v4` names are thin aliases. Its
implementation-before probe became `test_production_accepts_strikeout_only_through_version_4`, and the recipe
messages are the production ones. Its `next_version` table stays a contract table and is checked against the
production planner.

### 32.9 Tests and validation

- **New `tests/test_semantic_strikeout.py`, 116 tests:**
  - routes, the matrix (33), v3 compatibility, v1;
  - geometry, separation, style/rise/scale/font, Tw/edge for strikeout and mixed bodies, newline;
  - mixed/touching/remove/re-add, remap, R (22);
  - no-op (8 groups), resealed v4 tampers (7), v4→v3 relabel, body tampers (6), stale pairs;
  - Transaction declarations (5), affected area, planned-area necessity (2), own glyphs and own old strikeout, foreign
    gap and far paint;
  - publication with fresh process, MuPDF, Poppler;
  - failure isolation (9 + publication);
  - v2/v3 write paths.
- **Contract file:** 92 tests on production code.
- **Focused:** @@FOCUSED32@@.
- **Full suite on the final HEAD:** @@FULL32@@.

### 32.10 Completion

All criteria hold, each with production evidence:
- v2 → v4, v3 → v4, v4 reopen (fresh process);
- strikeout geometry, physical separation;
- mixed disjoint decorations, touching, overlap refusal;
- N/D/E/R;
- Transaction, foreign paint protection;
- publication, tamper refusal, no-op stability;
- v3 compatibility;
- full suite 0 failed.

**NARROW SEMANTIC STRIKEOUT RUNTIME COMPLETE.**

### 32.11 Remaining scope and next step

- Out of scope as before: overlap/stacking (underline and strikeout on the same characters), highlight,
  transparency, colours, other kinds, source adoption, multiple slots, cross-page.
- **WINDOWS STRIKEOUT VALIDATION NOT YET PERFORMED.** §30 validated version 3 only.
- **Next PR:** Windows semantic strikeout validation. Extend the §29 harness with a strikeout mode (v2 → v4 strikeout
  add, no-op ×2, edit, recipe, style, font, publish A, then from bundle A: v4 mixed with an underline, remove, publish
  B, negatives/tamper, raster with a text-only control). Run it in the cloud as a harness check, then once on Windows
  in a separate evidence PR.
