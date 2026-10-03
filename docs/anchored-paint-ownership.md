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
