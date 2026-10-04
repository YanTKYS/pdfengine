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
