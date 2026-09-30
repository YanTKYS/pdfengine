# 既存ページ上の確認済みcontinuation destination

## caller workflow

既存ページの空き領域へparagraphのcontinuationを置く場合、callerは次の6段階でdestinationを確認する。段階ごとに別のmodule APIを使う。engineはboundaryを選ばない。

```
inspection → structural review → geometry review → caller decision → confirmation request → explicit confirmation
```

| # | 段階 | 入力 → 出力 | 誰が決めるか | 保証しないこと |
|---|---|---|---|---|
| 1 | inspection `inspect_continuation_boundaries(source, page)` | source・page → safe候補（authority付き）と、refused境界の件数・理由の集計。個々のrefused境界が必要なら`include_refused=True` | engineが安全条件を判定する | destinationの空き・geometry。選択 |
| 2 | structural review `review_continuation_boundaries(source, page)` | source・page → safe候補を同じpaint位置ごとにまとめたgroup | engineが完全一致でまとめるだけ | 選択・順位・authorityの同一性。geometry |
| 3 | geometry review `review_continuation_geometry(source, page, bounds)` | source・page・callerのbounds → 2と同じgroupに、空きと候補ごとの継承clip適合の注記 | boundsはcallerが決める。engineは既存の検査結果を表示する | 生成glyph inkの包含・layout・容量。選択（候補を削除・並べ替えしない） |
| 4 | caller decision（callerのコード） | 2・3の結果 → boundary ID 1件 | **caller**。minimal集合・通過候補・並び順から自動では決まらない | — |
| 5 | confirmation request `build_continuation_boundary_confirmation_request(geometry, boundary_id=..., destination_id=..., paragraph_id=..., region_id=...)` | 3のrecord・4のboundary ID・identities → `confirm_kwargs` | callerの選択をそのまま運ぶ。選んだ候補のgeometryが不成立なら拒否する | sourceの再検証（`source_revalidated: false`）。confirm |
| 6 | explicit confirmation `confirm_continuation_destination(source, **request["confirm_kwargs"])` | current source・`confirm_kwargs` → confirmed destination | callerが呼ぶ。engineがcurrent sourceでauthority・clip・空きを再検証する | 生成する文章が収まること（容量・ink・layoutは`confirm_shared_flow`以降の計画と保存時の検査） |

- **review ≠ selection**: 1〜3は観測・整理・注記だけである。どのboundaryを使うかは4でcallerが決める。
- **request ≠ confirmation**: 5はconfirmの引数を作るだけで、sourceを読まない。確認済みdestinationを返すのは6だけである。古いreviewから作ったrequestは、sourceが変わっていれば6で拒否される。
- **confirm ≠ generated text fits**: 6はboundsとboundary authorityを確認する。生成する文章の容量・ink・layoutは、その後の`confirm_shared_flow`・計画・保存の各段階がそれぞれ検査する。

page・boundsは、3の結果から5の`confirm_kwargs`へbuilderが運ぶので、callerが書き写す必要はない。sourceはgeometry reviewにもrequestにも入らない。callerが保持し、6でcurrent sourceとして明示的に渡す。confirm引数への変換は5が行い、current sourceの再検証は6だけが行う（requestの`source_revalidated: false`と同じ分担）。group IDとboundary IDはprefixが異なり、5と6はどちらもgroup IDを拒否する。この分担でgapがないため、6段階をまとめるwrapperは設けていない。page entry（`before-page-program`）へのdestinationは、1〜5を使わずに6を直接呼ぶ（[契約](#契約)）。

### public API

| module | API | 位置付け |
|---|---|---|
| `pdfeditor.continuation` | `inspect_continuation_boundaries(source, page, *, include_refused=False)` | 1. inspection |
| `pdfeditor.continuation` | `confirm_continuation_destination(source, *, destination_id, paragraph_id, region_id, page, bounds, insertion, graphics_state, page_entry_order=None, boundary=None)` | 6. explicit confirmation（page entryにも使う） |
| `pdfeditor.continuation_review` | `review_continuation_boundaries(source, page)` | 2. structural review |
| `pdfeditor.continuation_review` | `review_continuation_geometry(source, page, bounds)` | 3. geometry review |
| `pdfeditor.continuation_review` | `build_continuation_boundary_confirmation_request(geometry_review, *, boundary_id, destination_id, paragraph_id, region_id)` | 5. confirmation request |
| `pdfeditor.continuation_review` | `group_continuation_boundary_candidates(candidates)` | advanced pure API。手元のinspector safe候補（複数ページ可）を2と同じ規則でまとめる。通常のcallerは2を使えばよい。公開済みの契約として維持する |

`pdfeditor`パッケージ直下からの再exportはしない。他のflow系と同じく、module単位で使う。

### schema

| record | 識別 | 性質 |
|---|---|---|
| boundary inspection record | 1の返却（`schema`欄なし） | current programのread-only観測。boundary IDはそのprogram固有。永続保存形式ではなく、6が同じ判定をやり直す |
| structural review | `pdfengine-continuation-boundary-review-1` | read-only presentation。group IDはそのprogram revisionの中だけで有効。永続でもconfirm可能でもない |
| geometry review | `pdfengine-continuation-boundary-geometry-review-1` | read-only presentation（callerのbounds付き）。永続保存形式ではない |
| confirmation request | `pdfengine-continuation-boundary-confirmation-request-1` | confirmへのhandoff。authorityではなく、sourceも再検証していない |
| confirmed destination | 6の返却（`schema`欄なし） | current source authority。確認時のPDF・page programのSHA-256に固定される。`confirm_shared_flow`へ渡され、flowのstateとsidecarに保存される（永続） |

以下は、各段階の技術契約と拡張の履歴である。

**現行scope上限はdepth 3**。PR #19のsource-decimal CTM契約を維持し、depth 3専用のouter/middle/inner形を追加した。depth 0・1・2のserialized形式は維持する。詳細は[3段の契約](#3段のq--q-scope-chainの内側)。以下の冒頭は各拡張時点の履歴である。

**外部PDF評価済み（2026-09-24）**。Windows環境で、対象の外部LibreOffice PDFの1 paragraphについて系列評価を完了した。内容は、確認済みの6ページdestinationを使った`overflow → reopen → re-edit → shorten → regrow → no-op`と、容量拒否である。その後、再保存で生成fontが累積する問題を修正し（[下記](#生成fontの寿命)）、同じ系列をno-op 3回まで拡げて再評価した。そのengineでの全suiteは642 passed / 7 skipped / 0 failed（Windows）である。確認したのは単一原本・単一destinationの範囲であり、任意のPDFで自然な再レイアウトができることは示していない。経緯は[再開地点と検証状況](continuation-checkpoint.md)を参照。

その後、同じpage-entry authorityを**同一ページの複数destination**へ拡張した（[下記](#同一ページの複数destination)）。合成PDFの回帰とdry-runに加え、**外部原本でも評価済み（2026-09-25）**である。拡張後の最終engineで、同じ外部原本について次の2本を実行し、どちらも通った。

- **単一destination**: 上記の系列を再実行した。7保存のPDF・sidecarが拡張前のengineとbyte単位で一致した。
- **同一ページ2 destination**: 確認済みの6ページ領域を評価者が2 destinationへ明示分割した。逐次生成・同時生成・reopen・re-edit・shorten・regrow・no-opを完走した。

その後、writerがtext object内に`q`/`Q`を出していた問題を直した（[下記](#pdf-1xのoperator-nesting)）。その最終engineで両方の外部評価をやり直した。全保存が入れ子の規則を満たし、出力は原本と同じ`%PDF-1.4`だった。描画は以前のengineと画素単位で同じだった。

確認済みpage-program境界を加えた最終engineでも、両方の外部評価をやり直した。PDF・sidecarは、上記の再評価とbyte単位で同じだった。

その後、page levelの確認済み境界に残る制約のうち、CTMだけを1段緩めた（[下記](#identity以外のctmの相殺)）。clip等の他の条件が安全で、境界のCTMを安全に逆変換できる場合に限り、生成blockをpage座標へ相殺して描く。合成PDFの回帰だけで確認しており、外部原本での評価はしていない。LibreOffice原本で確認済みの境界はCTM identityで、この変更の対象ではない。

さらに、有効なclipを1段緩めた（[下記](#矩形clipの継承)）。境界のclipを1つのpage矩形として証明でき、destination全体と生成glyphのinkがその矩形の内側に完全に収まる場合に限り、生成blockは既存のclipをそのまま継承して描く。clipを解除・再構築・拡大することはない。これも合成PDFの回帰だけで確認している。LibreOffice原本の有効なclipの下の境界は、どれも`q`の内側にあるため、この変更だけでは候補にならない。

続いて、`q ... Q` scopeの内側の境界を1段緩めた（[下記](#1つのq--q-scopeの内側)）。`q`の深さ1で、1つの明確なscopeの内側にあり、境界の状態がCTMの相殺・矩形clip等の既存の契約で扱える場合に限る。生成blockは、そのscopeの対応する`Q`より前で必ず閉じる。`q`の深さ2以上は拒否する。これも合成PDFの回帰だけで確認している。

**外部原本での確認（2026-09-26）**。その後、加工していないLibreOffice原本の1ページ・10ページを、PR #15のengineの`inspect_continuation_boundaries()`で調べた（[評価](../evaluations/continuation/README.md#pr-15-engineでのq--q-scope境界の評価)）。
- **新しい候補**: 1ページ126、10ページ86。どれも`q`の深さ1・CTM identityで、ほとんどは証明済みの矩形clip（ページ全体の`0 0.1 595.2 841.8 re W* n`、または図版の矩形）の下にある。
- **identity以外のCTM**: 1ページ2、10ページ18の境界がある。どれも相殺と矩形clipは証明されるが、`q`の深さ2にあり、`nested-graphics-state-save`で拒否される。原本には「深さ1・CTMの相殺・矩形clip」を同時に満たす境界はない。
- **系列評価**: 10ページの新しい候補の1つ（本文scopeの対応する`Q`の直前、ページ全体の矩形clipの下）で、確認済みの空き領域へ系列評価を行い、page entryの対照と生成glyph・画素が一致した。

深さ1の`q ... Q` scopeと矩形clipの継承は、外部原本でも確かめたことになる。CTMの相殺は、合成PDFでのみ確認したままである。

その後、`q`の深さ2を1段だけ緩めた（[下記](#2段のq--q-scope-chainの内側)）。最大2段の明確に証明された`q ... Q` scope chain（外側のscopeの中に内側のscopeが1つ）の内側で、既存のCTM・clip等の安全条件を満たす境界を扱う。外側・内側の両方の`q`と対応する`Q`、各`Q`が戻す状態をauthorityとし、4つの`q`/`Q`をrevisionごとにmutation mapで追跡する。`q`の深さ3以上は拒否する。実装時点では合成PDFだけで確認した。その後、PR #18で外部原本の系列と状態復帰を確認し、残ったPoppler差をsource operand基準の相殺で解消した（[下記](#実原本で確認した範囲と残る描画差)）。

現行の結果は[評価](../evaluations/continuation/README.md)、[公開集計](../evaluations/continuation/summary.json)、[2 destinationの公開集計](../evaluations/continuation/multi-destination-summary.json)にある。示したのは確認済みの1つの外部LibreOffice PDF、確認済みの1つの空き領域の範囲であり、任意のPDFで複数destinationが動くことは示していない。

`confirm_shared_flow`は、元glyphを持つsource slotと別に、callerが確認した空き領域への生成権限を受け取る。配置計画が実際にそこへ到達した場合だけ、同じparagraphのgenerated slotを作る。source slotの所有者を付け替えず、既存のshaper、line breaker、tracking/rise、alignment、font provider、CID/GID/`W` writerを共用する。

## 契約

```python
from pdfeditor.continuation import confirm_continuation_destination

destination = confirm_continuation_destination(
    source,
    destination_id="reviewed-extra-space",
    paragraph_id="paragraph-A",
    region_id="next-region",
    page=6,
    bounds=reviewed_regions["next-region"]["bounds"],
    insertion="before-page-program",
    graphics_state="isolated-pdf-initial-state",
    page_entry_order=10,  # 同じページに複数destinationがある場合は必須
)
flow = confirm_shared_flow(
    source, reviewed_stories,
    # 通常のflow/region/paragraph/保護領域の明示契約
    **reviewed_flow_arguments,
    continuation_destinations={destination["destination_id"]: destination},
)
```

destinationは`destination_id / paragraph_id / region_id / page / bounds / provenance`に加え、`authority`、確認時PDF hash、canonical page program hashを保持する。`authority`には挿入位置、z-order、初期graphics state、page transform/CropBox、透明度group、font policyを記録する。region geometryだけを渡す呼出しや、不明な描画状態は拒否する。

挿入authorityは2つある。

- **page entry**（`before-page-program`）: page program先頭で、既存の全描画より前に描く。同じページの複数destinationは、このauthorityを順序付きのpage-entry chainとして共有する（[下記](#同一ページの複数destination)）。
- **確認済みpage-program境界**（`confirmed-page-program-boundary`）: callerが候補一覧から選んだ、page levelの安全な1つのoperator境界に描く。確認したprefixの全描画より後、確認したsuffixの全描画より前である（[下記](#確認済みpage-program境界)）。

destination boundsは確認済みshared region全体と一致させる。どちらも任意の描画contextから状態を推測するAPIではなく、callerが明示的に選ぶ限定的な描画policyである。任意のcontent-stream位置、既存BT/ET内部、既存graphics stateの内側への挿入権限ではない。

先頭の初期CTMはidentity、clipはページのCropBox、不透明度は1、fill/strokeは初期値、blend/maskは初期状態。独立した`q BT ... ET Q`で状態を閉じ、必要なdevice fill、font、size、Tz、Tc、Ts、Tmを既存writerが設定する。既存paragraphのfont resourceやclipは参照しない。各論理styleに確認済みproviderが必要で、生成resourceは対象ページへ独立に追加する。

ページ自身のgroupは、未指定か、明示device色空間を持つisolated・non-knockout透明度groupのみ扱い、その辞書を証跡に含める。回転、UserUnit変更、Default色空間の上書き、不明なgroup/解析エラーは確認不能として拒否する。既存content内部のclipは生成objectより後に実行されるため借用しない。保存後も生成文字のCTM・clip・paint stateと挿入範囲を再照合する。

## 容量・所有・衝突

| 概念 | 意味 |
|---|---|
| source slot | 既存paragraphとregionに属するglyph/operatorの編集権限 |
| confirmed destination | 特定paragraphが既存page/regionへ新しいtext objectを作る権限。まだslotでもpaintでもない |
| generated slot | destinationを実際に使用したときに生まれる物理fragment。独立ID・owner・destination IDを持つ |
| capacity | regionの明示幅・baseline・bottomとparagraph policyで計算した有限の行容量 |

destination全域の空きを確認する。glyph、画像、path、shading等の非text paint、annotation、widget、linkが重なる場合は拒否する。外接矩形による判定は保守的な拒否方向に使い、pathの内側や背景を勝手に空きへ変換しない。別paragraphの現在の文字は、それが同じtransactionで退く予定でも空きとみなさない。生成後の検査で除外するのはそのgenerated slot自身のglyphだけである。同じページの別destinationが生成したglyphも、除外せず障害物として扱う。

regionはページ内で、保護領域および他regionと交差してはならない。同じページのdestination同士も交差を拒否する（辺が接するだけなら既存`Rect`契約どおり交差ではない。そのうえでpaint envelopeの保守的な判定が適用される）。最終計画間のglyph ink衝突もTransactionで検査する。全確認済み容量を超えれば拒否し、font/leading/widthやpage数を変えない。

## Persistenceと再編集

生成IDはdestination ID、paragraph ID、region ID、pageのdigestから定まる。slotには`creation_provenance = generated-from-confirmed-continuation-destination`、生成時mutation record、確認契約hash、物理editable bindingを記録する。確認契約はsource slotとdestinationの権限を固定し、後から生成されたslotの数には依存しない。

挿入blockにはID由来の一意な開始・終了comment markerを置く。`destination_bindings`はdestinationごとに、現在revisionのprogram digest、block自身のdigest、範囲、確認済みpage-entry orderを保持する。最初のglyph対応はmutation anchorから解決し、再保存ではbyte mutation mapで開始・終了markerの対応を照合する。slot内のglyphがそのblock内にあること、他slotのglyphを含まないことを検証する。xref番号やgeometryだけでは再bindingしない。

長文化→短文化で不要になったgenerated slotは文字を除去し、`occupancy=None`のdormant状態にする。非描画`[] TJ`の挿入context、destinationとslot IDは残る。確認済みtracking/riseにはstyleごとの非描画Tc/Ts witnessを保存し、再open時に数値とprogram証跡を検証する。これはpaintされたglyphが存在するという主張ではない。再長文化は同じslotへ描画する。

region境界はUnicodeへ改行を追加しない。style spansとparagraph IDは一本のまま、first-line indentは最初のvisual lineだけに適用する。justifyの末尾行処理は既存のparagraph continuation policyを引き継ぐ。

## 同一ページの複数destination

以前は1ページにつき1 destinationに限っていた。現在は、同じページに互いに交差しない複数の確認済みdestinationを置ける。例えば同じparagraphが`source slot → destination-A → destination-B`と続く場合や、`paragraph-A → destination-A`、`paragraph-B → destination-B`と別々のparagraphが続く場合である。各destinationは、destination ID、paragraph owner、region、geometry、generated slot ID、作成provenance、marker block、binding、font所有、reopen identityを独立に持つ。挿入authorityは引き続き`before-page-program`と`isolated-pdf-initial-state`だけである。

### page-entry chainと順序契約

同じページのgenerated blockは、original page programの前に並ぶ順序付きの**page-entry chain**を作る。

```text
page entry
|- generated block (order 10)   q BT ... ET Q
|- generated block (order 20)   q BT ... ET Q
original page program
```

- **順序**: 各destinationは`page_entry_order`（非負整数）を持つ。複数destinationのページでは全destinationに必須で、ページ内で一意でなければならない。dict順、slot生成順、activation順、flowのregion順は使わない。flowの読み順と描画順は別の契約であり、engineは読み順を推定しない。
- **保存と再検証**: orderはdestination契約の一部としてsidecarに保存し、shared flowの確認契約hashと、generated slotの作成証跡（`destination_contract_sha256`）に含まれる。orderの変更は既存confirmationの変更として扱い、再open時に`needs_confirmation`になる。
- **単一destination**: 1ページに1つだけなら`page_entry_order`を省略できる。その場合は従来と同じ契約（offset 0、順序なしの作成mutation、同じbinding形式）になる。orderの有無が混在するページは拒否する。
- **独立性**: 各blockは独立した`q BT ... ET Q`で、PDF初期graphics stateから始まり自分の状態を閉じる。他blockのtext state、font resource、clipを引き継がず借用しない。

### 挿入境界

新しいblockは、そのrevisionで検証済みのchainから求めた境界に入る。境界は「orderが小さい生成済みblockのうち最後のものの終端」、なければoffset 0である。orderが大きい生成済みblockはその後ろに残る。例えばorderが`A < B < C`でAだけが既存なら、Bは`A | B | original`となる境界（Aの終端）に入る。Bだけが既存でAを後から作る場合（逆順activation）は、Aはoffset 0に入り`A | B`になる。同時に複数の新blockを作る場合、同じ境界に入るblockは次の順序付きinsertionで並ぶ。計画時の境界（先行・後続blockのslot IDとoffset）はsnapshotに入り、writerは保存前にprogramのbytesから再照合する。

### MutationProgramの同位置insertion

`MutationProgram`は、zero-length insertionが他mutationの境界に触れることを拒否する。同じoffsetでの順序が慣習であってprovenanceではないためである。この規則は維持し、例外を一つだけ追加した。

- **許可する唯一の組**: 同じoffsetにある2つのzero-length insertionで、どちらも`confirmed-continuation-create`、owner（slot ID）を持ち、明示的な`insertion_order`が異なり、ownerも異なるもの。
- **引き続き拒否**: 順序のないinsertion同士、順序付きinsertionと通常insertionまたは置換mutationとの接触、同じorderや同じownerの重複、zero-lengthでない・ownerのない・別kindのmutationにorderを付けること。one source byte/operator = one ownerは変わらない。
- **byte順**: 同じ境界ではorderの昇順に並び、追加順に依存しない。`apply()`、`position()`/`anchor()`（先行するmutationの長さ変化の総和）、`map_offset()`（source offsetは同じ境界の全insertionの後ろへ写る）は、この順序で定義する。
- **記録**: mutation recordとbyte edit（`edits()`）はorder・kind・ownerを保持する。同じ境界ではこれらがbytesの位置を決めるためである。`from_records()`と`IdentityMap`はorder順に再構築し、JSON往復後も同じprogramを再現する。
- **Transaction**: 生成glyphの順序照合も、mutationの開始offsetではなく適用後programでの位置で並べる。

### 各blockの証跡

- **作成provenance**: 各generated slotは独立した作成mutationを持つ。`kind = confirmed-continuation-create`、`owner = slot ID`、`insertion_order = page_entry_order`、`block_start = 0`、`block_end = 長さ`である。複数blockを一つのmutationにまとめない。
- **witness**: 以前の`data.startswith(begin)`という1block前提をやめた。各destinationについて、自分のbegin/end markerがそれぞれ1回だけあることを確かめ、そのblock自身の範囲を取る。block SHA-256は他blockのprefixを含まない。
- **chain全体の検証**（再openごと）:
  - 生成済みblockがoffset 0から隙間なく連続し、確認済みorder順に並び、original page programより前にある。block間に未知のbytesはない。
  - 未生成のdestinationにはmarkerがない。
  - 各block本体は`q BT ... ET Q`で始まり終わる。外側の`q ... Q`はblockの最後でだけ閉じる。text objectは入れ子にならず、すべて閉じる。`Tm`・`Tj`・`TJ`はtext object内だけにある。`q Q BT ET Tf Tz Tc Tw Ts Tm Tj TJ g rg k`以外のoperatorや、別のcomment/markerを含まない。
  - text object内に`q`/`Q`はない（[PDF 1.xのoperator nesting](#pdf-1xのoperator-nesting)）。bindingはこの規則で書いたことを`operator_nesting`に記録する。
  - block内の文字はそのslotのglyphだけで、各glyphは初期graphics state（CTM identity、clipなし、不透明度1、Tr 0）で描かれる。
  - block内の`Tf`が選ぶaliasは、sidecarの`generated_fonts`がそのslotの所有と記録したものだけである（記録を持たない旧版sidecarでは、この照合を行えない）。
  - 同じページのdestination bounds同士が交差しない。
- **再binding**: 既存blockは、前revisionのbegin/end marker先頭のsource offsetをmutation mapで写した位置と、保存後に見つかったmarkerの位置が一致することで追跡する。新blockは作成mutationのanchorで追跡する。隣のblockが伸びたり、前に新blockが入ったりして絶対offsetが変わっても、正しいblockへbindingする。

### 生成fontの所有

`reserve_font_alias`は、sidecar記録のslotと計画のowner slotが一致するaliasだけを再利用する。同じtransactionでglyphが消費されていても、別slotのaliasは使わない。その別slotの非描画operatorやdormant witnessが、まだ`Tf`でそのaliasを選んでいるためである。commit時にも、置き換えたaliasを書いたplanのownerを記録と照合する。同じpage transaction内の複数planが同じaliasを予約することも、従来どおり拒否する。

### dormant / regrow

短文化で使わなくなったdestinationのslotは、従来どおりdormantになる。blockとmarker、chain内の位置は残る。再長文化では同じslot・同じblock・同じorderへ戻り、新しいblockは作らない。

### 回帰と評価

- **回帰**: [tests/test_multi_destination.py](../tests/test_multi_destination.py)で次を確認する。
  - 同時初回生成、逐次生成、逆順activation、3 destinationで既存chainへ2 blockを同時に入れる場合。
  - 1 paragraphのA→B continuation（left・justify、tracking/rise）と、2 paragraphの所有分離。
  - shorten→dormant→regrow、各保存後の再open、no-op 3回（block順序・slot identity・marker・生成font数・画素）。
  - order・marker・chainの改ざん、destination同士・保護領域・固定paint・他destinationの生成glyphとの交差、後段failure時のrollback。
- **MutationProgram**: 同位置insertionの規則は[tests/test_mutation.py](../tests/test_mutation.py)で確認する。
- **外部評価**（2026-09-25）: [評価コード](../evaluations/continuation/multi_destination.py)は、単一destination評価で確認済みの6ページ領域`[55,80,385,120]`を評価者が2つのregionへ分割する（`[55,80,385,100]` order 10、`[55,101.5,385,120]` order 20）。範囲外を新たに空きとは仮定しない。この評価コードをWindows検証環境の外部原本で実行し、全段階が通った（[結果](../evaluations/continuation/README.md#外部原本の結果)）。
  - **逐次生成**: `page6-a`だけの生成 → 既存blockの後ろへの`page6-b`追加（作成位置は直前revisionの`page6-a`の終端）→ 再編集 → `page6-b`のdormant化 → 同じslot・blockへの復帰 → no-op 3回。
  - **同時生成**: offset 0に同位置ordered insertionで2 blockを作り、order 10 → 20 → 元のpage programの順になった。逐次生成とallocation・chain順序・所有が一致した。
  - **監査**: 各region外（region間の1.5ptを含む）のPoppler差分は全保存で0画素だった。6ページの生成fontはslotごとに所有され、入れ替わらなかった。
  - **dry-run**: 合成原本で最後まで動かした結果も[評価README](../evaluations/continuation/README.md#同一ページ2-destinationの評価)に残す。

## 確認済みpage-program境界

page entry以外にも、callerが確認したpage levelのoperator境界を挿入authorityにできる。目的は、既存の描画順序の中で生成blockの位置を明示することである。

```text
confirmed prefix（既存の描画を含む）
generated block   marker q BT ... ET Q marker
confirmed suffix（既存の描画を含む）
```

任意の途中位置へ書けるようにするものではない。境界が安全であることを証明でき、その境界自体をcallerが明示的に確認した場合だけ使える。

### 候補の列挙

```python
from pdfeditor.continuation import (
    confirm_continuation_destination, inspect_continuation_boundaries)

found = inspect_continuation_boundaries(source, page=6)
# found["candidates"]: 安全な境界。callerが1つを選んで確認する
destination = confirm_continuation_destination(
    source, destination_id="reviewed-boundary", paragraph_id="paragraph-A",
    region_id="next-region", page=6, bounds=reviewed_bounds,
    insertion="confirmed-page-program-boundary",
    graphics_state="confirmed-boundary-state",
    boundary=reviewed_boundary_id)
```

- **対象**: `inspect_continuation_boundaries(source, page, include_refused=False)`は、結合したpage `/Contents` programのtop-level operator同士の間の境界を列挙する。offset 0（page entry）と、最後のoperatorの後は含まない。operatorやoperandの途中、inline image・Form XObjectの内部には境界がない。
- **証跡**: 各境界の状態と入れ子は、pageを編集するときと同じ`ContentPage`の解釈で得る。`_walk`は、top-level operatorごとに直後の`State`とscopeを記録する（`Boundary`）。別のinterpreterは作っていない。`operator_nesting`は構文上の入れ子を、境界の検査はその位置で有効な描画状態とscopeを扱う。
- **候補の記録**: page、`boundary_id`、program SHA-256、offset、operatorの序数、直前・直後のoperatorの証跡を持つ。operatorの証跡は、名前・範囲・operandを含むbytesのSHA-256である。さらに、scope・graphics state・z-order（prefix/suffixの描画operator数と意味）・pageのcontext（group・transform・範囲）・`status`・拒否理由を持つ。
- **`boundary_id`**: page、program SHA-256、直前operatorの序数・終端・SHA-256、直後operatorのSHA-256から作る。確認時のsource programに固有で、以後はauthorityに固定される。
- **geometryからは選ばない**: callerが候補を1つ選ぶ。engineはdestinationの位置から境界を推定しない。

### 読み取り専用のboundary review

safe候補は1ページに数百あることがある。`pdfeditor.continuation_review`は、それを**同じ既存paintの間**ごとにまとめて表示する読み取り専用APIである。境界を選ばない。順位を付けない。確認もしない。

```python
from pdfeditor.continuation import (
    confirm_continuation_destination, inspect_continuation_boundaries)
from pdfeditor.continuation_review import review_continuation_boundaries

inspection = inspect_continuation_boundaries(source, page=6)  # 1. 個々のsafe境界とauthority
review = review_continuation_boundaries(source, page=6)       # 2. 同じpaint位置ごとの表示
# 3. callerが自分でboundary_idを1つ選び、明示的に確認する（group IDは渡せない）
destination = confirm_continuation_destination(
    source, destination_id="reviewed-boundary", paragraph_id="paragraph-A",
    region_id="next-region", page=6, bounds=reviewed_bounds,
    insertion="confirmed-page-program-boundary",
    graphics_state="confirmed-boundary-state",
    boundary=reviewed_boundary_id)
```

| 段階 | 役割 |
|---|---|
| inspection | 安全な個々の境界と、そのauthority（state・scope・CTM相殺・clip）を列挙する |
| review | inspectionのsafe境界だけを、同じpaint位置ごとに表示用に整理する。refused境界は含めない |
| confirm | callerがboundary IDを明示的に選んだ後だけ行う |

callerが指定したboundsの空き・clip適合を併記する場合は、下の[geometry review](#読み取り専用のgeometry-review)を使う。このreviewの出力は変わらない。

- **API**: `review_continuation_boundaries(source, page)`は内部で`inspect_continuation_boundaries(source, page)`を1回呼び、safe候補だけを使う。`group_continuation_boundary_candidates(candidates)`は、すでに持っているinspectorのsafe候補（複数ページ可、1ページ1 program）を整理するpure関数で、入力を変更・参照共有しない。入力順に依存しない。`inspect_continuation_boundaries()`の返却schema・boundary ID・順序・authority・拒否理由・confirmは変わらない。
- **返却**: `schema`、`page`、`program_sha256`、`candidate_count`、`group_count`、`groups`、`contract`。
- **group**: page・program SHA-256・`z_order.semantics`・prefix/suffixの描画operator数が**完全一致**する候補だけをまとめる。近いordinal・似たoperator・似たstateではまとめない。groupは「同じ既存paintの間」を意味するだけで、authorityの同一性や候補の交換可能性を意味しない。同じgroupでもgraphics state・`q ... Q` scope・CTM相殺・継承clipは異なり得る。各groupは`group_id`、paint位置、`candidate_count`、ordinalの範囲、全`boundary_ids`、各候補、`minimal_authority_review_candidates`、`distinct_authority_variants`を持つ。
- **group ID**: `review-group-`＋page・program SHA-256・z-orderの意味・2つのpaint数（とschema用のdomain文字列）のSHA-256先頭24桁。同じprogram・同じpaint位置なら常に同じIDになる。別revisionでpaint数が偶然同じでも、programが違えば別IDになる。**boundary IDではなく、confirmに渡せず、revisionをまたぐ永続IDでもない**。そのprogramのreview表示の中でだけ使う。
- **候補の表示**: inspector recordを複写せず、`boundary_id`、ordinal、直前・直後のoperator、`operator_context`（例: `cm -> boundary -> BT`）、`q_depth`、`scope_policy`、scope bindingの有無と各段の`q`/`Q`のordinal、CTM相殺・矩形clip constraintの有無を持つ。完全なauthorityはinspector recordにある。
- **`review_requirements`**: callerがauthorityを確認するときに追加で読む証跡。`scope-binding`（各段の`q`・対応する`Q`・復帰状態）、`ctm-compensation`（逆行列とsource/interpretedの変位証明）、`rectangular-clip`（継承矩形への包含条件）の順に、該当するものを並べる。例: `[]`、`["scope-binding", "rectangular-clip"]`。安全性の段階ではない。ここにある候補はすべてsafeである。
- **authorityの差**: 各候補の`authority_digests`は、graphics state・scope・CTM相殺・clip constraintそれぞれのcanonical JSONのSHA-256（ないものは`null`）。groupの`distinct_authority_variants`は、その種類数（「なし」も1種と数える）。hashは表示上の差の識別用で、authority identityやsecurity tokenではない。
- **`minimal_authority_review_candidates`**: groupの中で、`review_attributes`（`rectangular_clip`・`ctm_compensation`・`q_depth`）を、clipなし→相殺なし→浅いq depthの辞書式で比べた最小の候補。整数scoreには変換せず、属性そのものを各候補と`minimal_authority_review_attributes`に残す。**同率はすべて残し、他の候補は削除しない**。追加で確認するauthority証跡が少ない候補というだけで、安全性の点数・推奨・自動選択ではない。
- **contract**: `contract`は上の意味を記録し、`automatic_selection`・`safety_ranking`・`recommendation`・`automatic_confirmation`・`geometry_used`がいずれも`false`である。safe候補は、それだけでは空き領域として確認されていない。
- **fail closed**: safe以外・理由付き・z-orderの不正・boundary IDやpage/ordinalの重複・1ページに異なるprogram・ページ内でpaint総数の不一致・q深さ4以上・inspectorと異なるscope形式・`q`/`Q`の対応や入れ子の破綻・clip stateとclip constraintの不一致・CTMと相殺の不一致や不正な相殺・NaNなどcanonical JSONにできない値は`PdfError`で拒否する。
- **実原本**: PR #22の[prototype](../evaluations/continuation/README.md#safe-boundaryの描画位置別レビュー--2026-09-30)は、この正式APIのthin wrapperになった。未加工LibreOffice原本10ページの3,016 safe候補→834 groupの再確認手順は[評価README](../evaluations/continuation/README.md#正式read-only-apiでの再確認)にある。
- **回帰**: [tests/test_continuation_review.py](../tests/test_continuation_review.py)。

### 読み取り専用のgeometry review

callerが**自分で決めたbounds**について、「pageとして空いているか」と「各safe候補が継承するclipに収まるか」を、上のreviewに併記するだけの読み取り専用APIである。boundsから境界を選ばない・推定しない。順位を付けない。候補を絞らない。確認もしない。

```python
from pdfeditor.continuation import (
    confirm_continuation_destination, inspect_continuation_boundaries)
from pdfeditor.continuation_review import (
    build_continuation_boundary_confirmation_request,
    review_continuation_boundaries, review_continuation_geometry)

inspection = inspect_continuation_boundaries(source, page=6)   # 1. inspection
review = review_continuation_boundaries(source, page=6)        # 2. structural review
geometry = review_continuation_geometry(                       # 3. geometry review
    source, page=6, bounds=caller_bounds)

# 4. caller decision: callerがreviewを読み、boundary_idを自分で1つ決める。
#    engineは決めない（minimal集合・通過候補・並び順からの自動選択はしない）。
chosen_boundary_id = ...

request = build_continuation_boundary_confirmation_request(   # 5. confirm引数への受け渡し
    geometry, boundary_id=chosen_boundary_id,
    destination_id="reviewed-boundary", paragraph_id="paragraph-A",
    region_id="next-region")

destination = confirm_continuation_destination(                # 6. 明示的なconfirm
    source, **request["confirm_kwargs"])
```

| 段階 | 役割 |
|---|---|
| inspection | 安全な個々の境界と、そのauthorityを列挙する |
| structural review | safe境界を同じpaint位置ごとに整理する（`review_continuation_boundaries`、geometryは使わない） |
| geometry review | callerが指定したboundsについて、空きとclip適合を各候補に注記する（`review_continuation_geometry`） |
| caller decision | callerがboundary IDを自分で選ぶ。engineのどのAPIも選ばない |
| confirmation request | 選ばれた1件を`confirm_continuation_destination`の引数にするだけ（下の[confirmation request](#callerが選んだboundaryのconfirmation-request)）。confirmはしない |
| caller confirmation | callerが`confirm_continuation_destination`を明示的に呼ぶ。current sourceを再検証し、確認済みdestinationを返す唯一の段階 |

- **API**: `review_continuation_geometry(source, page, bounds)`は1つの`ContentPage`でpageを**1回だけ**inspectionし（`review_continuation_boundaries`を経由した二重scanはしない）、同じinspectionから既存のstructural reviewを作り、geometryを加えて返す。`review_continuation_boundaries()`の返却・schema・contract（`geometry_used: false`を含む）は変わらない。
- **bounds**: page空間の`[x0, y0, x1, y1]`。4要素、有限の数（boolは不可）、`x0 < x1`、`y0 < y1`でなければ`PdfError`で拒否する（pageは開かない）。形式が正しいboundsがpage外・固定paintと交差する場合は例外にせず、`destination_empty: false`として返す。
- **空き判定はpage・bounds共通**: `pdfeditor.continuation.require_empty(content, bounds)`を**1回だけ**呼ぶ。confirmと同じ判定で、新しい規則は作らない。owned glyphの除外は渡さない（既存の生成destinationの再編集は対象外）。結果は全候補で共通である。失敗時は`require_empty`の`PdfError`のmessageを`geometry.empty_check_error`に残す。
- **clip適合だけが候補固有**: 候補のinspector recordに`clip_constraint`があるときだけ、その正式なconstraintで`clip_contains(clip_constraint, bounds)`を呼ぶ。authority digestからclipを作り直したり、constraintを変更・再証明したりしない。clipのない候補はclipで拒否されない（`clip_check_required: false`、`bounds_inside_inherited_clip: true`）。矩形の辺ちょうどは内側で、少しでも越えれば外側になる（既存契約どおり）。
- **候補ごとの`geometry`**: `destination_empty`、`clip_check_required`、`bounds_inside_inherited_clip`、`checks_passed`。`checks_passed`は正確に`destination_empty AND bounds_inside_inherited_clip`だけを意味する。推奨・選択・確認・最終layout成功の保証ではない。
- **候補を削除しない**: geometryが不成立の候補もgroupと`boundary_ids`に残し、groupと候補の順序も変えない。geometryは注記であり、filterではない。groupには`geometry_checks_passed_count`・`geometry_checks_failed_count`の集計だけを加える。通過候補だけのgroup、再順位付け、1件の自動選択は作らない。
- **`minimal_authority_review_candidates`は不変**: authority確認量の比較でありgeometryとは別の軸なので、geometryで再計算しない。geometry不成立の候補が含まれていても残す。callerは両方の情報を見られる。
- **返却**: `schema`（`pdfengine-continuation-boundary-geometry-review-1`）、`page`、`program_sha256`、`bounds`、`candidate_count`、`group_count`、`groups`、`geometry`（`bounds`、`destination_empty`、`empty_check_error`と候補数の集計）、`contract`。
- **contract**: `read_only`・`geometry_used`が`true`、`automatic_selection`・`safety_ranking`・`recommendation`・`automatic_confirmation`・`generated_ink_evaluated`が`false`。`geometry_used: true`は「callerが指定したboundsの適合を表示する」という意味で、「boundsから境界を選ぶ」という意味ではない。
- **評価しないもの**: 生成glyphのink、`INK_MARGIN`を含むglyphのclip包含、paragraph layout、容量、font、writer、renderer、lifecycle。**boundsがclip内でも、生成inkがclip内に収まるとは限らない**。最終的なlayout・容量・ink包含は、confirmと、その後のplanがそれぞれの検査で判定する。
- **回帰**: [tests/test_continuation_geometry_review.py](../tests/test_continuation_geometry_review.py)。深さ0〜3・CTM相殺・矩形clipを含む合成pageで、実際の`require_empty`・`clip_contains`を通す。未加工LibreOffice原本での外部検証は、次のWindows専用PRで行う。

### callerが選んだboundaryのconfirmation request

`build_continuation_boundary_confirmation_request(geometry_review, *, boundary_id, destination_id, paragraph_id, region_id)`は、geometry reviewのrecordと、callerがそこから選んだboundary IDを、`confirm_continuation_destination(source, **request["confirm_kwargs"])`の引数へ変換するだけのpure APIである。sourceを受け取らない。inspection・`require_empty`・`clip_contains`・confirmのいずれも呼ばない。**requestの生成はconfirmではない**。確認済みdestinationを返すのは`confirm_continuation_destination()`だけである。

- **caller選択が必須**: `boundary_id`を省略すると`PdfError`になる。group ID（`review-group-...`）・boundary ID形式でない値・reviewにない値も拒否する。builderはminimal集合・geometry通過候補・groupの代表・q depth・並び順のいずれからも選ばない。scoreも作らない。
- **minimal集合とは独立**: 選ばれた候補が`minimal_authority_review_candidates`に含まれている必要はない。minimal集合は推奨候補ではない。
- **geometryの前提**: 選ばれた1件の`geometry.checks_passed`がfalseなら、requestを作らず`PdfError`にする。messageでは、page-levelの空き（`destination_empty`）の不成立か、継承clip（`bounds_inside_inherited_clip`）の不成立かを区別する。これは、callerが選んだ1件について既知のconfirm前提が満たされていないためのfail closedであり、候補のfilterや推薦ではない。
- **reviewの検証**: 入力をそのまま信用しない。schema、contract（geometry reviewの`GEOMETRY_CONTRACT`と完全一致。selection・confirmation・read-only・`generated_ink_evaluated`の改ざんを拒否）、page、program SHA-256、bounds、geometry summaryとboundsの一致、空き判定とerrorの整合、candidate/group数、group ID（page・program・paint位置から再計算して照合）、groupの並び、`boundary_ids`と候補行の一致、候補の順序、boundary IDの重複、minimal集合がgroup内にあること、各候補の`geometry`のkeyと型、`checks_passed = destination_empty AND bounds_inside_inherited_clip`、page-levelの空きとの整合、clipのない候補がclipで不成立になっていないこと、groupと全体の集計、を確認する。recordとしての整合を見るだけで、geometryを再計算しない。
- **identities**: `destination_id`・`paragraph_id`・`region_id`は、confirmと同じく空でない文字列である。IDを自動生成しない。
- **返却**: `schema`（`pdfengine-continuation-boundary-confirmation-request-1`）、`page`、`bounds`、`review_program_sha256`、`group_id`（選ばれた候補のgroup）、`boundary_id`、`candidate_review`（ordinal・operator context・`q_depth`・`review_requirements`・`review_attributes`・`geometry`）、`confirm_kwargs`、`contract`。完全なauthorityは複写しない。
- **`confirm_kwargs`**: `destination_id`、`paragraph_id`、`region_id`、`page`、`bounds`、`insertion`（既存定数`BOUNDARY` = `confirmed-page-program-boundary`）、`graphics_state`（`BOUNDARY_STATE` = `confirmed-boundary-state`）、`boundary`（callerが選んだboundary ID）。`page_entry_order`は含めない。
- **`review_program_sha256`と`source_revalidated: false`**: geometry reviewが見たprogramのSHA-256を、表示と古いreviewの診断のためにだけ残す。builderはcurrent sourceと照合しない。sourceがreview後に変わっていても、requestは組み立てられる。そのboundary authorityが今も成立するかは、confirmがcurrent sourceを再inspectionして判定し、成立しなければ既存契約どおり拒否する。
- **contract**: `read_only`・`caller_selected_boundary`が`true`。`automatic_selection`・`recommendation`・`safety_ranking`・`automatic_confirmation`・`source_revalidated`・`generated_ink_evaluated`が`false`。requestが作れても、生成glyph ink・`INK_MARGIN`・paragraph layout・容量・font・writer・renderer・lifecycleの成功や、confirmが受理することは保証しない。
- **回帰**: [tests/test_continuation_confirmation_request.py](../tests/test_continuation_confirmation_request.py)。合成pageで、requestをtest側が明示的にconfirmへ渡すと受理されること、minimal集合外の候補も受理されること、sourceを変えるとconfirmが拒否すること、builderがconfirm・inspection・geometry検査を呼ばずPDFを変えないこと、改ざんされたreviewを拒否することを確認する。

### 安全な境界の条件

境界で次をすべて満たすものだけが候補になる。生成blockがpage entryと同じpage座標・同じ見た目で描けることを証明できる場合に限る。

- **programの入れ子**: page program全体が、[operator nesting](#pdf-1xのoperator-nesting)の`audit`で違反なしである。違反の例は、入れ子の`BT ... BT ... ET`、`BT`のない`ET`、閉じていない`BT`、対応しない`q`/`Q`やmarked content、text object内のpage記述レベルのoperatorである。違反が1つでもあれば、scopeを信用できないので、そのページのどの境界も候補にしない（fail closed）。scopeの判定は、入れ子が正しいことを前提にしている。
- **scope**: `q`の深さ0（page levelの状態が閉じている）、深さ1で1つの明確な`q ... Q` scopeの内側（[下記](#1つのq--q-scopeの内側)）、深さ2で2段の明確に証明されたscope chainの内側（[下記](#2段のq--q-scope-chainの内側)）、または深さ3の専用chain（[下記](#3段のq--q-scope-chainの内側)）にある。text object、marked-content sequence、`BX ... EX`の外で、組み立て中のpathや未適用の`W`/`W*`がない。
- **graphics state**: 不透明度とstroke不透明度が1、text描画モードが0。ExtGStateと`ri`・`i`の設定はない。CTMはidentityか、blockが逆行列で相殺できることを証明できるもの（[下記](#identity以外のctmの相殺)）。clipはない（page entryと同じくCropBoxだけ）か、1つのpage矩形であることを証明できるもの（[下記](#矩形clipの継承)）。
- **stroke専用の値は許す**: `w`・`J`・`j`・`M`・`d`はstrokeにしか効かない。blockはTr 0の塗りの文字だけを描くので、既定値でなくてよい。
- **blockが自分で設定する値**: font・size・Tz・Tc・Tw・Ts・fill（`g`/`rg`/`k`で色空間ごと）は、blockが自分で設定し、外側の`q ... Q`で元に戻す。そのため境界での値は問わない。strokeの色やTLは、blockが使わない。
- **拒否**: 迷う状態は拒否する。拒否理由は次のとおりである。
  - `invalid-operator-nesting`（ページのすべての境界に付く）
  - `nested-graphics-state-save`（`q`の深さ4以上。PR #15から深さ2を扱うまでは深さ2以上）、`unproven-graphics-state-scope`（scopeがmarked content等と交差する）。PR #15までは`q`の深さ1以上を一律に`inside-graphics-state-save`としていた
  - `inside-text-object`、`inside-marked-content`、`inside-compatibility-section`
  - `pending-path`、`pending-clip`
  - `nonfinite-ctm`、`singular-ctm`、`numerically-unstable-ctm`（相殺を証明できないCTM）
  - `nonrectangular-clip`、`rotated-clip`、`text-clip`、`empty-clip`、`unproven-clip`（矩形と証明できないclip。以前は一律に`active-clip`だった）
  - `transparency`、`text-rendering-mode`、`extgstate`、`graphics-state-side-effect`

### authorityとz-order

- **authority**: 確認したdestinationの`authority`は次を持つ。
  - `position = confirmed-page-program-boundary`、`boundary_id`、確認時の`source_program_sha256`。
  - `boundary`: offset・序数・直前/直後operatorの証跡・scope。
  - `graphics_state`: 境界の状態の証跡。
  - `graphics_state_contract`、`z_order`、pageのcontext、`initial_clip = page-crop-box`、`isolation = q-BT-ET-Q`、font policy。
  - CTMがidentity以外の境界だけ、`ctm_compensation`を持ち、`isolation = q-cm-BT-ET-Q`になる（[下記](#identity以外のctmの相殺)）。identityの境界のauthorityはPR #11と同じである。
  - clipのある境界だけ、`clip_constraint`を持ち、`initial_clip = inherited-rectangular-clip`になる（[下記](#矩形clipの継承)）。clipのない境界のauthorityは、PR #11・#12と同じである。
  - `q ... Q` scopeの内側の境界だけ、`graphics_state_scope`を持つ（[下記](#1つのq--q-scopeの内側)）。`q`の深さ0の境界のauthority・bindingは、PR #14と同じである。
  - 深さ2の境界の`graphics_state_scope`は、`outer`・`inner`の2段を持つ（[下記](#2段のq--q-scope-chainの内側)）。深さ0・1の境界のauthority・binding・IDは、PR #15と同じである。
- **z-order**: `z_order.semantics = after-all-paint-of-the-confirmed-prefix-before-all-paint-of-the-confirmed-suffix`。確認時のprefixとsuffixの描画operator数も記録する。前面・背面ではなく、callerが選んだ境界そのものが描画順序の契約である。
- **空き判定**: page entryと同じ`require_empty()`を使う。prefix・suffixの描画との重なりも許さない。重なりを使ったlayer編集ではなく、挿入順序だけをoffset 0以外へ広げる。
- **1境界1destination**: 1つの境界は1つのdestinationだけが持つ。page-entry orderは持たない。同じ境界への複数の順序付き挿入は、まだ扱わない。

### bindingとrebind

- **bindingの内容**: 各revisionのbindingは、境界のoffsetと、直前・直後operatorの範囲を持つ。blockがあれば、その範囲・block SHA-256・`operator_nesting`も持つ。
- **再検証**: open・保存のたびに、境界で終わるoperatorと次のsource operatorが確認時と同じ（名前とbytes）ことを確かめる。その位置のscopeと状態が確認時と同じで、今も安全であることも確かめる。page program全体の入れ子が正しいことも、毎回確かめる。offsetやxrefの一致だけでは同じ境界とみなさない。
- **blockの追跡**: 自分の一意なmarkerで見つける。保存をまたぐときは、既存blockはmarkerとmutation map、新blockは作成mutationのanchorで追跡する。
- **未使用の境界の追跡**: 保存ごとに、その保存のmutation mapで写す（`map_offset`）。消費されたoffsetには後継がない。
- **検証の例**:
  - prefix側やsuffix側の無関係なmutation、block自身の再編集で長さが変わっても、同じ境界を追跡する。
  - 同じoffsetでも、CTM・clip・ExtGState・`q`・marked contentが変われば、同じauthorityとして扱わない。
  - 境界の前後のoperator・scope・状態が同じでも、programの入れ子が崩れていれば（入れ子の`BT`、`BT`のない`ET`、閉じていない`BT`）同じauthorityとして扱わない。

### MutationProgram

変更していない。境界のblockは、順序を持たない通常のzero-length insertion（`confirmed-continuation-create`）である。

- **拒否**: 同じtransactionで境界に触れるmutationは、既存の規則で拒否する。直前operatorの置換や、同じoffsetへの別の挿入がこれにあたる。
- **直後operatorの変更**: insertionに触れない変更でも、次のrevisionの証跡が一致しないため保存が失敗し、何も公開しない。
- **page-entryの順序付きinsertionの特例**: 広げていない。

### page entryとの共存

同じページにpage-entry destinationと境界destinationがあってもよい。

- page-entry chainは従来どおりoffset 0からの連続prefixとして検証し、境界のblockは別のauthorityとして検証する。
- 生成fontは、slotごとの所有記録で分かれる。互いのaliasを使わない。

### identity以外のCTMの相殺

**範囲**: page levelの確認済み境界で、clip等の他の条件が安全で、CTMを安全に逆変換できる場合に限り、生成blockをpage座標へ相殺して描く。identity以外のCTMを一般に扱えるようにしたものではない。ExtGStateは、CTMにかかわらず従来どおり拒否する。clipは矩形と証明できるものだけを継承し（[下記](#矩形clipの継承)）、`q`の内側は最大3段の明確なscope chainだけを扱う（[下記](#1つのq--q-scopeの内側)、[2段](#2段のq--q-scope-chainの内側)）。

```text
confirmed prefix                     CTM = M
marker q N cm BT ... ET Q marker     N = source operand CTM Sの逆をserializedした値
confirmed suffix                     CTM = M（blockのQが戻す）
```

- **形式**: `N cm`は、blockの`q`の直後、text objectの外に1回だけ置く（PDF 1.x。`cm`は`BT ... ET`の中に置けない）。blockの`Q`がMを戻すので、suffixの既存の描画は元のCTMのままである。identityの境界のblockは、従来どおり`q BT ... ET Q`で、`cm`を持たない。
- **writer**: 生成glyphの位置・Tmは、page entryと同じpage座標で計算する。glyph planはpage-entry版と同じになる。

**逆行列と証明**（`compensation()`）

- **M**: 境界の確認済みCTM（authorityの`graphics_state.ctm`）。interpreterは、MuPDFと同じくbinary32で行列を合成する。
- **S**: PDFが記述した十進`cm` operandを有理数にして合成したCTM。pypdf 6.10.0の`FloatObject`はbinary64なので、`float()`・`str()`を経由して復元しない。既存`operators()`が確定したbyte spanから`cm`の6数値だけを読み、例えば`158.2`を`791/5`として保持する。PDFの符号・小数点・空白・commentを扱い、数値として証明できないtokenは拒否する。汎用parserやclipの数値解釈は追加・変更しない。
- **有限・可逆**: S・Mの成分が有限で、両方の行列式を有理数で厳密に計算し、0でないこと。
- **N**: Sの厳密な逆行列を、既存`_written()`で有効数字12桁に丸める。PDFの数値には指数表記がないので、固定小数表記で書く。証明には、実際に書くoperandそのものを使う。同じ入力は同じoperator bytesになる。
- **値の範囲**: N・S・Mの0でない成分は、binary32の正規数で、PDFの整数の範囲（2^31−1以下）に収まること。
- **identityへ戻ることの証明**: TをS・Mのそれぞれとして、page box（CropBox、user space）の4隅pについて、pとp·N·Tの距離の上界を個別に求める。
  - 厳密な残差: 有理数で計算した|p·(N·T − I)|。
  - 丸めの上界: γ·(|p|·|N|·|T|)（成分ごとの絶対値）。γ = 8u/(1−8u)、u = 2^−24。既存のbinary32の保守的な丸め上界を両モデルに適用する。合成（operand・積・和）と、合成したCTMを点に適用するときの丸め、計8段を含む。
  - **どちらか一方でも**上界が0.002を超えれば拒否する。最大値も記録する。0.002は変更せず、保存したrevisionで生成glyphの原点を検証する許容値と同じである。
  - これはpage-space displacementの証明であり、任意rendererでの画素一致の数学的保証ではない。MuPDF・Poppler一致は個別の回帰実証で確認する。
- **上界の目安**: 上界はページの大きさにほぼ比例する。合成試験の320×260ptページでは、translation・異方scale・30°回転・skew・反転のいずれも0.001以下である。A0程度の大きなページでは、回転を含むCTMが上界を超えて拒否されうる（保守的な上界のため）。
- **拒否**: 成分が有限でない（`nonfinite-ctm`）、特異（`singular-ctm`）、値の範囲外か上界超過（`numerically-unstable-ctm`）。

**authorityの記録**（`ctm_compensation`）

- `policy = inverse-ctm-inside-block-save`、`inverse_basis = source-decimal-operands`、`confirmed_ctm`（M）、`matrix`（Nとして書くoperand）、`operator`（`N cm`のbytes）。
- `proof.source_ctm`: Sの6成分を整数または`numerator/denominator`の文字列で正確に保存する。`models.source`と`models.interpreted`に、書かれたNと各CTMの合成結果（`residual`、6成分）と個別の`displacement_bound`を持つ。page box、丸めのモデル（binary32・u・段数）、最大上界、許容値も記録する。
- `graphics_state_contract`は、blockが記録した逆行列でCTMを相殺することを明記する。

**再検証**

- open・保存のたびに、境界の確認済みCTMとprogramのoperandから`ctm_compensation`全体を再導出し、記録と一致しなければ拒否する。記録を読み戻して使うことはしない。
- binary32 CTMの逆を使った過去のcompensated authorityは、operatorが偶然同じでも新しい証跡と異なるため`needs_confirmation`となる。黙ってmigrationしない。identity・compensationなし・page-entryのauthorityと境界IDの計算は変更しない。
- CTMが変わった境界は、offset・前後のoperatorが同じでも同じauthorityではない。binary32で同じ値になる変更でも、operandが変われば証明が変わるので拒否する。
- blockは`q N cm BT`で始まる。`cm`は、authorityの`operator`とbytesが同じものが1つだけで、ほかの`cm`は許さない。identityのblockは`cm`を持たない。PDF 1.xの入れ子以前の形式（legacy）のbindingでは、相殺を認めない。
- block内の文字は、MにNを合成したCTM（interpreterの値）で描かれていること。blockの`q ... Q`は末尾でだけ閉じる（`_block`）ので、`Q`の直後はCTMがM、`q`の深さが挿入前の値（page levelなら0）に戻る。これは試験でも直接確かめる。
- bindingとrebind（marker、mutation map、作成mutationのanchor）は変えていない。

### 矩形clipの継承

**範囲**: page levelの確認済み境界で、有効なclipを1つのpage矩形として証明でき、destination全体と生成glyphのinkがその内側に収まる場合に限り、生成blockは既存のclipを継承して描く。任意のpath clipを扱えるようにしたものではない。ExtGState等は、clipにかかわらず従来どおり拒否する。`q`の内側は、最大3段の明確なscope chainだけを扱う（[下記](#1つのq--q-scopeの内側)、[2段](#2段のq--q-scope-chainの内側)）。

```text
confirmed prefix                       clip = C（page levelで確定済み）、CTM = M
marker q [N cm] BT ... ET Q marker     clip = Cのまま。blockはW・W*・nを書かない
confirmed suffix                       clip = C、CTM = M
```

- **継承する理由**: page levelのclipは、blockの`q ... Q`では外せない。clipのない状態へ戻す先が、blockの外にないためである。blockはclipを解除・再構築・拡大せず、そのまま継承する。
- **blockの形**: 変えていない。`q BT ... ET Q`か、相殺があれば`q N cm BT ... ET Q`である。`W`・`W*`・`n`・pathはblockの許可operatorにないので、`_block`が`foreign operator`として拒否する。

**矩形の証明**（`clip_constraint()`）

clip authorityは従来どおり、pypdfでbinary64に読み込んだoperandの有理数合成（`_clip_source_ctms()`）を使う。compensation専用の十進source modelと分離し、今回の修正だけで既存clip authorityを変えない。

- **対象**: 境界で有効なclipの各要素が、次をすべて満たすこと。複数の要素はintersectionをとる。
  - `W`または`W*`のpath clipで、pathが`x y w h re`の1 subpathだけ。1つの矩形では、nonzeroとeven-oddは同じ領域になる。
  - page levelの連続した3つのoperator（`re`、`W`/`W*`、`n`）で設定されている。間に`cm`や別のpathはない。
  - `re`の時点のCTMに回転・skewがない（b = c = 0）。interpreterのCTM（binary32）と、operandから厳密に合成したCTMの両方で確かめる。
  - operandとCTMの成分が有限である。0でない値は、binary32の正規数で、PDFの整数の範囲に収まる（CTMの相殺と同じ）。
- **矩形**: 各clipについて、2つのCTMそれぞれで、page座標（page transform込み）の矩形を有理数で厳密に求める。そのintersectionを、binary32の丸めの上界だけ各辺で内側へ縮め、binary64へ内向きに丸める。
  - 丸めの上界: γ·((|x|+|w|)·(|t_a|+|t_b|) + (|y|+|h|)·(|t_c|+|t_d|) + |t_e| + |t_f|)。tはCTMとpage transformの合成、γはCTMの相殺と同じ8段（u = 2^−24）である。
  - 得た矩形（certified rectangle）は、どの描画系のclipにも含まれる。320×260ptの合成ページでは、上界は約0.0003ptである。
- **拒否**:
  - `nonrectangular-clip`: 複数のsubpath、`m`/`l`/`h`の多角形（形が矩形でも）、曲線。
  - `rotated-clip`: 回転・skew・90°回転の下の`re`。境界のCTM自体は相殺できても拒否する。
  - `text-clip`: text描画モード4〜7によるclip。
  - `empty-clip`: 面積のない矩形や、交わらない複数の矩形。
  - `unproven-clip`: 連続した`re W n`の形でないもの（pathより前の`W`、`re W f`など）、非有限・範囲外の値。

**authorityの記録**（`clip_constraint`）

- `policy = inherited-rectangular-clip`と、`rectangle`（certified rectangle、page座標）。
- `clips`: clipごとに次を持つ。
  - rule、`re`のoperand、interpreterのCTMとoperandから合成したCTM。
  - clipを設定したoperator（`re`・`W`/`W*`・`n`）の名前と、bytesのSHA-256。
  - 2つのCTMでの厳密な矩形と、丸めの上界。
- `proof`: 方式（`intersection-of-single-re-clips-under-ctms-without-rotation-or-skew`）、page transform、丸めのモデル（binary32・u・段数）、上界。
- `containment`: destinationとinkの包含の規則と、inkの余白（下記）。
- **関連する記録**:
  - `graphics_state.clip`には、各clipのruleとpath（operandとCTM）を記録する。どの位置のoperatorで設定したかは記録しない。位置はprefixの編集で動くためで、設定したoperatorはbytesで証跡する。
  - `initial_clip = inherited-rectangular-clip`。`graphics_state_contract`は、blockが確認済みの矩形clipを継承して変えないことを明記する。
- **候補**: candidateの時点で`clip_constraint`を持つ。destinationがその矩形内に収まることは、確認時の明示的な制約である。境界はgeometryから選ばない。

**destinationとinkの包含**

- **確認時**（`confirm_continuation_destination`）: destination bounds ⊆ certified rectangle。有理数で厳密に比べ、許容値は置かない。辺に接するのは内側で、外へ1 ulpでも出れば拒否する。
- **生成時・再編集時**（shared flowの計画と、作成blockのwriter）: 生成glyphごとの輪郭のink box（page座標）を、各辺で`INK_MARGIN` = 1.002pt広げても、certified rectangleの内側にあること。
  - 1pt: 描画系のpaint envelope。MuPDFのbbox logは、文字のpaint範囲を、glyphの輪郭のboxから72dpiのdeviceの1単位（1pt）広げて記録する（glyph cacheの位置精度の余裕）。`require_empty()`が既存のpaintを読むのも、このenvelopeである。
  - 0.002pt: 保存したrevisionで、生成glyphの原点を検証する許容値。CTMの相殺の上界も、これ以下である。
  - 計画の段階で拒否するので、何も書かない。再編集なら前のrevisionがそのまま残る。
- **保存後**（open・保存のたびの`_validate_destination`）: 保存したrevisionで、生成slotの各文字のpaint envelope（MuPDFのbbox log）が、certified rectangleの内側にあること。空のenvelope（空白のglyph）は何も描かない。計画の判定は、この保存後の判定を予測したものである。
- **空き判定**: `require_empty()`は緩めていない。clipに隠れて見えない既存のpaintも、障害物のままである。bbox log・`get_drawings()`・text traceは、clipで隠れたpaintも記録する。

**CTMの相殺との共存**

- clipは、設定した時点のCTMでpage空間に固定される。blockの`N cm`はCTMだけを相殺し、確定済みのclipは変えない。
- clipを`cm`より前（identityのCTM）で設定した場合は、境界のCTMが回転・skewでも、clipを矩形として証明できる。clipを`cm`の後で設定した場合は、そのCTMに回転・skewがないことが必要である。
- block内の文字のCTMは`block_ctm()`で、clipは確認済みのものと同じ。blockの`Q`の直後とsuffixの文字では、CTMとclipが元のままである。試験ではinterpreterで直接確かめる。

**再検証**

- open・保存のたびに、境界のclipとprogramから`clip_constraint`全体を再導出し、記録と比べる。`initial_clip`と`graphics_state_contract`も、再導出した値と比べる。記録を読み戻して使うことはしない。
- clipのrule・geometry・CTM・数、設定したoperatorのbytesのどれかが変われば、offsetと前後のoperatorが同じでも同じauthorityではない。
- 位置だけが動く場合は、同じauthorityである。例えば、同じページのpage-entry blockやprefixの編集で、clipのoperatorの位置が変わる場合である。
- block内の文字は、確認済みのclipの下で描かれていること（`clip_state`で比べる）。

### 1つの`q ... Q` scopeの内側

**範囲**: `q`の深さ1で、1つの明確な`q ... Q` scopeの内側にある境界に限り、確認済み境界として扱う。境界の状態には、CTMの相殺・矩形clip等のこれまでの契約をそのまま使う。任意のgraphics-state stackを扱えるようにしたものではない。深さ2は[2段のscope chain](#2段のq--q-scope-chainの内側)として別に扱い、`q`の深さ4以上は拒否する。

```text
opening q                              scopeを開く（page level）
  ...                                  scopeのprefix
  confirmed boundary                   q depth 1
  marker q [N cm] BT ... ET Q marker   blockは自分の状態だけを保存・復元する
  ...                                  scopeのsuffix（境界と同じ状態）
matching Q                             scopeの前の状態を戻す（従来どおり）
existing suffix
```

- **blockの形**: 変えていない。blockの`q ... Q`は境界の状態だけを保存・復元し、scopeの対応する`Q`より前で閉じる。scopeの`Q`が戻す状態（scopeの前の状態）を、blockが戻す必要はない。scopeを開く・閉じる・保存することもない。
- **状態**: `q`の深さ0の境界と同じ条件を、scope内の境界の状態に課す。CTMはidentityか証明済みの相殺、clipはなしか証明済みの矩形。ExtGState・不透明度・blendはなし。text object・marked content・`BX ... EX`の外で、組み立て中のpath/clipもない。operatorの入れ子も正しいこと。

**scopeの証明**（`enclosing_scope()`）

- **構造**: page programのtop-level operatorを`q`/`Q`の入れ子で読み（`graphics_scopes()`）、境界の直前のoperatorの後で開いている`q`を求める。1つだけであること（深さ1）、その`q`に対応する`Q`があること、境界がその間にあること。interpreterの`q`の深さとも一致すること。
- **page levelのscope**: 開く`q`（とその直前）と対応する`Q`で、text object・marked content・`BX ... EX`・組み立て中のpath/clipがないこと。scopeがmarked contentなどと交差しないことを示す。
- **戻す状態**: `q`の直前の状態（`restored_state`）を記録し、対応する`Q`の直後の状態がそれと同じであることを、interpreterで確かめる。
- **拒否**: `nested-graphics-state-save`（深さ4以上。深さ2は[下記](#2段のq--q-scope-chainの内側)）、`unproven-graphics-state-scope`（scopeがmarked content・`BX ... EX`と交差する、構造とinterpreterの深さが合わないなど）。`q`/`Q`が釣り合わないページは、従来どおりページ全体を扱わない。

**authorityの記録**（`graphics_state_scope`）

- `policy = one-enclosing-graphics-state-save`、`depth = 1`、`contract`（blockがscopeの`Q`より前で閉じ、scopeを保存・復元・終了しないこと）。
- `opening`・`matching`: 開く`q`と対応する`Q`の、確認時の序数・範囲・bytesのSHA-256。
- `restored_state`: 対応する`Q`が戻す状態。
- `boundary_id`は、scope内の境界では開く`q`・対応する`Q`の序数・終端・SHA-256も含めて作る。`q`の深さ0の境界のIDは変えていない。
- `graphics_state_contract`は、scopeの内側の状態であることと、blockがscopeの`Q`より前で閉じることを明記する。
- candidateの時点で`graphics_state_scope`を持つ。境界はgeometryから選ばない。

**同じscopeの追跡**

`q`と`Q`のbytesは、どのscopeでも同じである。そのため、bytesの一致や現在のoffsetだけでは同じscopeとみなさない。次をすべて求める。

- **binding**: revisionごとのbindingに、そのrevisionでの開く`q`と対応する`Q`の範囲（`scope`）を記録する。
- **保存**: 前のrevisionのbindingの`q`・`Q`の位置を、その保存のmutation mapで写す（`carried_scope()`）。新しいrevisionの構造が境界の周りに置く`q`・`Q`の位置と、一致しなければならない。
  - mutationが`q`か`Q`を消費する場合（scopeの端をまたぐ置換）は、写し先がないので保存を拒否する。
  - `MutationProgram`の規則は変えていない。
- **open**: bindingが示す位置と、そのrevisionの構造が一致すること。確認時のprogramでは、authorityの記録（序数・範囲・bytes）とも一致すること。
- **証跡**: 構造から再導出したscopeが、記録と同じであること（位置を除く）。bytes、深さ、戻す状態、contractを比べる。
- **scopeの外へ出ない保証**:
  - blockの検証（`_block`）は、自己完結した1つの`q ... Q`だけを認める。
  - scopeは、blockの開始位置で終わるoperatorから求める。そのため、入れ子の上で対応する`Q`は必ずblockの後ろにある。
  - さらに、開く`q`の終端 ≤ block（未使用の境界ではその位置）≤ 対応する`Q`の先頭を、revisionごとに明示的に確かめる。
- **例**: 次の場合は、同じauthorityではない。
  - 開く`q`が別の`q`に替わる（bytes・深さ・状態が同じでも）。
  - 対応する`Q`が別の`Q`に替わる、境界の後の`Q q`で対応が変わる。
  - 境界の前の`Q q`で別のscopeへ移る。
  - 深さが0か2になる、`Q`が消えて入れ子が崩れる、blockが対応する`Q`の後ろへ移る。
- **動いてよい場合**: scopeの前・scopeのprefix・scopeのsuffix・scopeの後にあるsource slotを同じtransactionで書き直すと、offsetや序数は動く。それでも同じscopeへbindingする。

**CTMの相殺・矩形clipとの共存**

- scope内の`cm`や`re W n`は、境界の状態のCTM・clipとして、これまでの証明（`compensation()`・`clip_constraint()`）でそのまま扱う。
- blockの`N cm`はblockの`q`の直後にだけあり、blockの`Q`がscope内の状態（CTM M・clip C）を戻す。scopeの`Q`がscopeの前の状態を戻す。
- 試験では、次をinterpreterで直接確かめる。
  - blockの`Q`の直後は`q`の深さ1で、境界と同じ状態。scope内のsuffixの文字も同じCTM・clip。
  - 対応する`Q`の直後は深さ0で、`restored_state`と同じ状態。scopeの後の文字はidentityのCTM・clipなし・scopeの前の色。

### 2段の`q ... Q` scope chainの内側

**範囲**: `q`の深さ2で、最大2段の明確に証明された`q ... Q` scope chain（外側のscopeの中に内側のscopeが1つ）の内側にあり、既存のCTM・clip等の安全条件を満たす境界に限り、確認済み境界として扱う。任意のgraphics-state stackを扱えるようにしたものではない。`q`の深さ4以上は拒否する。CTMはPR #19のsource-decimal相殺、clipはPR #14の矩形clipの契約をそのまま使う。深さ3は[専用の追加形](#3段のq--q-scope-chainの内側)として扱う。新しいCTM・clipの処理は加えていない。

```text
outer q                                外側のscopeを開く（page level）
  ...                                  外側のprefix（例: fill・stroke・clipの変更）
  inner q                              内側のscopeを開く
    ...                                内側のprefix（例: CTM・clipの変更）
    confirmed boundary                 q depth 2
    marker q [N cm] BT ... ET Q marker blockは境界の状態だけを保存・復元する
    ...                                内側のsuffix（境界と同じ状態）
  inner matching Q                     外側のscopeの中の状態へ戻す
  ...                                  外側のsuffix
outer matching Q                       chainの前（page level）の状態へ戻す
existing suffix
```

- **理由**: PR #16の外部原本の検査で、LibreOffice原本のidentity以外のCTMの境界（1ページ2、10ページ18）は、どれも深さ2にあった。相殺と矩形clipは証明されるが、`nested-graphics-state-save`で拒否されていた。LibreOfficeは、clipを持つ本文・図版groupの`q`の中で、各行・下線・画像を`q [cm] ... Q`で囲むためである。
- **blockの形**: 変えていない。blockの`q ... Q`は境界の状態だけを保存・復元し、内側の対応する`Q`より前で閉じる。chainのどの`q`・`Q`も、blockが開く・閉じる・保存することはない。
- **状態**: 深さ0・1の境界と同じ条件を、境界の状態に課す。CTMやclipを設定したのが外側の段でも内側の段でも、境界の状態として既存の証明（`compensation()`・`clip_constraint()`）で扱う。

**chainの証明**（`enclosing_scope()`）

- **構造**: 境界の直前のoperatorの後で開いている`q`がちょうど2つで、interpreterの`q`の深さとも一致すること。それぞれに対応する`Q`があること。
  - 外側の`q` < 内側の`q` ≤ 境界 < 内側の`Q` < 外側の`Q`であること。
  - 各`q`と対応する`Q`は、programの`q`/`Q` stack（`graphics_scopes()`）で対応を求める。bytesや位置の一致で対応させることはしない。
- **各段**: 開く`q`（とその直前）と対応する`Q`で、text object・marked content・`BX ... EX`・組み立て中のpath/clipがないこと。どの段もmarked contentなどと交差しないことを示す。
- **戻す状態**: 各段の`q`の直前の状態（`restored_state`）を記録し、対応する`Q`の直後の状態がそれと同じであることをinterpreterで確かめる。内側の`Q`は外側のscopeの中の状態を、外側の`Q`はchainの前の状態を戻す。
- **拒否**: `nested-graphics-state-save`（深さ4以上）、`unproven-graphics-state-scope`（どちらかの段がmarked content・`BX ... EX`と交差するなど）。

**authorityの記録**（深さ2の`graphics_state_scope`）

- `policy = two-nested-graphics-state-saves`、`depth = 2`、`outer`・`inner`、`contract`（blockが内側の`Q`より前で閉じ、どちらのscopeも保存・復元・終了しないこと）。
- `outer`・`inner`は、それぞれ`opening`・`matching`（確認時の序数・範囲・bytesのSHA-256）と`restored_state`を持つ。
- `boundary_id`は、source境界の証跡に加えて、policyと、外側・内側の順に開く`q`・対応する`Q`の序数・終端・SHA-256を含めて作る。内側だけ・外側だけが違う場合も、`q`と`Q`の対応が違う場合も、別のIDになる。
- `graphics_state_contract`は、2段のchainの内側の状態であることと、blockが内側のscopeの`Q`より前で閉じることを明記する。
- **深さ0・1との互換**: 深さ1の記録（`opening`・`matching`・`restored_state`を直接持つ形）、ID、binding（`scope`が`opening`・`matching`を直接持つ形）、contractの文言は変えていない。`outer`・`inner`の形を使うのは深さ2だけである。既存の深さ1のsidecarを変換することはない。

**同じchainの追跡**

- **binding**: revisionごとのbindingの`scope`に、`outer`・`inner`それぞれの開く`q`と対応する`Q`の範囲を記録する。
- **保存**: 前のrevisionの4つの位置を、その保存のmutation mapでそれぞれ写す（`carried_scope()`）。新しいrevisionの構造が境界の周りに置く4つの位置と、すべて一致しなければならない。
  - mutationが4つのどれかを消費する場合は、写し先がないので保存を拒否する。別のscopeへ乗り換えることはない。
- **open**: bindingの4つの位置と構造が一致すること。確認時のprogramでは、authorityの記録（序数・範囲・bytes）とも一致すること。
- **証跡**: 構造から再導出したchainが、位置を除いて記録と同じであること（bytes・深さ・戻す状態・policy・contract）。
- **chainの外へ出ない保証**: 外側の`q`の終端 ≤ 内側の`q`の先頭、内側の`q`の終端 ≤ block ≤ 内側の`Q`の先頭、内側の`Q`の終端 ≤ 外側の`Q`の先頭を、revisionごとに確かめる。
- **例**: 次の場合は、同じauthorityではない。
  - 内側・外側の`q`か`Q`が、同じbytesの別のものに替わる。
  - `Q q`で、どちらかの段の対応が変わる、または境界が別の段へ移る。
  - 深さが1か3になる、`Q`が消えて入れ子が崩れる、blockが内側・外側の対応する`Q`の後ろへ移る。
- **動いてよい場合**: chainの前、外側のprefix、内側のprefix・suffix、外側のsuffix、chainの後にあるsource slotを同じtransactionで書き直すと、4つの位置は動く。それでも同じchainへbindingする。

**CTMの相殺・矩形clipとの共存**

- chain内の`cm`・`re W n`は、どちらの段にあっても、境界の状態のCTM・clipとして既存の証明でそのまま扱う。LibreOffice原本と同じく、外側の段で矩形clip、内側の段で`cm`を設定する形も含む。
- blockの`N cm`はblockの`q`の直後にだけある。blockの`Q`は境界の状態（CTM M・clip C）を、内側の`Q`は外側のscopeの中の状態を、外側の`Q`はchainの前の状態を戻す。
- 試験では、この3つの`Q`の直後の状態と、各位置の文字の状態を、interpreterで直接確かめる。

### 3段の`q ... Q` scope chainの内側

最大scope depthは**3**。depth 3だけ次の追加形を使い、depth 1の直接形、depth 2の`outer`・`inner`形は変更しない。

```text
graphics_state_scope:
  policy: three-nested-graphics-state-saves
  depth: 3
  outer:  {opening, matching, restored_state}
  middle: {opening, matching, restored_state}
  inner:  {opening, matching, restored_state}
  contract: 3段専用の契約文
```

各opening/matchingはoperator名・ordinal・start/end・bytesのSHA-256を持つ。実際のq/Q stackとbyte位置から、`outer q < middle q < inner q < boundary < inner Q < middle Q < outer Q`と各対応を再導出する。boundary IDには専用policyとouter/middle/inner順の6 operatorのordinal・end・SHA-256を入れる。depth 0・1・2のID入力は同じである。

revision bindingの`scope`も`outer`・`middle`・`inner`を持ち、各段のopening/matchingのstart/endを保存する。各6範囲を直前revisionから`MutationProgram`で個別に写し、現在programから再導出した位置・対応・nestingと照合する。1つでもmutationが消費すれば拒否する。同一bytesを`op` anchorで再出力しても別operatorであり、rebindしない。blockは必ずinner openingの後、inner matching Qの前に収める。

authorityのdepth・policy・contract・level集合は3つの明示形のどれかと一致する必要がある。middle欠落、depth 2にmiddle追加、depth 3をdepth 2形へ偽装、level交換、対応変更は拒否する。JSONのkeyの列挙順には依存しない。

| 復元点 | q depth | 比較する状態 |
|---|---|---|
| generated blockのQ後 | 3 | confirmed boundaryのgraphics_state |
| inner Q後 | 2 | innerのrestored_state（innerへ入る直前） |
| middle Q後 | 1 | middleのrestored_state（middleへ入る直前） |
| outer Q後 | 0 | outerのrestored_state（page state） |

比較は`_state()`の全15fieldで行う。syntheticでは各段でfill/stroke・width等のother・font/text stateを変え、middleで矩形clip、innerで端数平行移動を設定する。opacityも含めて比較するが、安全条件は従来どおり不透明であり、ExtGStateは許可しない。

PR #19のS/N/M・N×S/N×M・0.002pt上界と、PR #14のclip proofは変更しない。identity、端数平行移動、矩形clip、両方の4組合せについて、同じpage-entry対照と計画glyph・保存origin・MuPDF画素を比較する。既存source CTMのPoppler回帰も関連試験として実行する。

[tests/test_scope_three_boundary.py](../tests/test_scope_three_boundary.py)は、reopen・second・shorten・regrow・no-op 1回、font所有と再利用、nesting、rollback、6 operatorの改ざん、別scope・対応変更、block移動を検証する。source slotをouter q前、各段のprefix/suffix、outer Q後の8箇所に置き、同じ6 operatorを追跡する。

外部実PDFの調査・評価は今回実施しない。PR #18のrenderer差はPR #19で解消済みであり、履歴のfailed記録は維持する。次の最小の構造障壁はdepth 4である。ExtGState、未証明clip、pending path、text object・marked content・BX/EX・Form XObject内も引き続き拒否する。検証結果・互換比較・所要時間は[checkpointの現行節](continuation-checkpoint.md#depth-3専用scopeの追加)に記録する。

### 実原本で確認した範囲と残る描画差

2026-09-27、PR #17のengineを変更せず、LibreOffice原本10ページのordinal62（下線`S`直後、inner `Q`直前）で、depth 2・CTM相殺・矩形clipの系列を実行した。全5保存とreopen、4つのbinding、生成Q/inner Q/outer Q後の全比較可能状態、glyph座標・clip内ink・font所有・nesting・領域外画素は通った。

一方、同じpage-entry overflowとの比較は、glyph plan・保存origin・MuPDF画素が一致したものの、Poppler 144dpiで生成1行目の5,868画素が不一致だった。binary32 CTMの逆を原本の十進operandに合成した微小残差が原因で、0.002ptの幾何上界はrasterの完全一致を保証しない。保存済み出力だけの診断で原本operandの逆に変えると差は0になったが、engine修正や一般化した証明は行っていない。[評価詳細](../evaluations/continuation/README.md#pr-17-engineの深さ2実境界評価--2026-09-27)・[失敗を含む集計](../evaluations/continuation/scope-chain-destination-summary.json)を参照。

**PR #18後の修正と再評価**: 上記の失敗記録を保持したうえで、[source operand基準の相殺](#identity以外のctmの相殺)へ変更した。同じ原本・境界・領域でboundary overflow＋reopenとpage-entry overflowを各1回だけ実施し、計画glyph・保存origin・MuPDF全画素・Poppler全画素が一致した。10ページのPoppler差は**5,868→0画素**、領域外差分も0。4 binding・全状態復帰・clip authorityを維持し、page-entryのPDFとsidecarは旧出力とbyte一致した。fractional translationの深さ0・1・2では新Nで両rendererが一致し、旧Nに戻す対照でPoppler差を検出する。[新しい集計](../evaluations/continuation/ctm-source-destination-summary.json)・[原因と検証条件](../evaluations/continuation/README.md#source-ctm-compensation)を参照。これは対象caseでの描画回帰実証であり、任意rendererでの画素一致の証明ではない。

### 対応範囲

| 状態 | 対応 |
|---|---|
| page entry（`before-page-program`） | 対応 |
| 確認済みの安全なpage level境界（CTM identity） | 対応 |
| 同上で、CTMがidentity以外だが、逆行列での相殺を証明できるもの | 対応（`q N cm BT ... ET Q`。source CTMの逆と両モデルの上界を証明。PR #18後のfocused実原本評価でPoppler差0） |
| 同上で、有効なclipを1つのpage矩形と証明でき、destinationと生成inkがその内側に収まるもの（CTMはidentityか相殺できるもの） | 対応（clipを継承。実原本で相殺との組合せの系列・包含を確認し、source CTM基準のfocused評価でPoppler差0） |
| 1つの明確な`q ... Q` scopeの内側（深さ1）で、状態が上の条件を満たすもの | 対応（blockはscopeの`Q`より前で閉じる。外部原本の10ページで、矩形clip・CTM identityの境界を確認） |
| 最大2段の明確に証明された`q ... Q` scope chainの内側（深さ2）で、状態が上の条件を満たすもの | 対応（blockは内側の`Q`より前で閉じる。実原本で系列・全状態復帰を確認し、source CTM基準のfocused評価でPoppler差0） |
| 3段の明確に証明されたscope chain（深さ3）で、同じ安全条件を満たすもの | 対応（専用outer/middle/inner authority、6 operatorを追跡。syntheticで確認、今回の外部評価なし） |
| 特異・非有限・数値的に不安定なCTM | 未対応（拒否） |
| 多角形・曲線・複数subpath・回転やskewの下の矩形・text clip・面積のないclip | 未対応（拒否） |
| `q`の深さ4以上、marked content等と交差するscope、任意のExtGState | 未対応（拒否） |
| text object・marked content・`BX ... EX`・Form XObjectの内側 | 未対応（拒否） |
| operatorの入れ子が崩れたpage program | 未対応（ページ全体を拒否） |

### 回帰と評価

- **回帰**: [tests/test_boundary_destination.py](../tests/test_boundary_destination.py)で次を確認する。
  - 候補の列挙と拒否理由、callerの明示確認。入れ子が崩れたprogramでは候補を出さないこと。
  - 非zero offsetでの作成、reopen、second、shorten、regrow、no-op 3回。prefix/block/suffixの順序と、描画operatorの順序も確かめる。
  - 同じtransactionでのprefix側・suffix側の変更。
  - 境界に触れるmutationの拒否、sidecarとprogramの改ざん、同じoffsetでの状態の改ざん、入れ子を崩す改ざん。
  - page entryとの共存、late failureのrollback。
  - page entryと境界で同じ文字・同じ画素になること。
- **CTMの相殺**: [tests/test_ctm_compensation.py](../tests/test_ctm_compensation.py)で次を確認する。
  - translation・scale・回転・skew・反転の証明と、特異・非有限・不安定なCTMの拒否。
  - 同じCTMでも、矩形と証明できないclip・`q`・ExtGState・marked contentの境界は従来どおり拒否すること。
  - 生成glyphのpage座標・画素がpage-entry版と一致すること。suffixの既存の描画（文字・path・画素）が元のCTMのまま変わらないこと。逆行列がsuffixへ漏れた場合に検出できること（対照）。
  - reopen・second・shorten・regrow・no-op 3回、生成fontの所有、operator nestingの違反0。
  - CTM・逆行列・証明の改ざん（sidecarとprogram）の拒否。page entry・identityの境界との共存。late failureのrollback。
- **矩形clipの継承**: [tests/test_clip_boundary.py](../tests/test_clip_boundary.py)で次を確認する。
  - 候補と`clip_constraint`の記録。1つの矩形・`W*`・複数の矩形のintersection・scaleの下の矩形は候補になる。複数subpath・多角形・曲線・回転/skew/90°回転・text clip・面積なし・`re W f`などは拒否する。`q`・ExtGState・marked content・`BX`・組み立て中のpath/clipは、clipが矩形でも従来どおり拒否する。
  - destinationの包含。certified rectangleの辺は内側、外へ1 ulp・clip自体の辺・1ptは拒否する。boundsを変えてもauthorityは同じ。clipに隠れた既存paintも障害物のままである。
  - lifecycle（identity、clipの後の回転、scaleの下のclip）: fits → grow → second → shorten → regrow → no-op 3回。reopen、authority・作成証跡の不変、blockに`W`・`W*`・`n`・pathがないこと、block内・blockの`Q`の直後・suffixの文字のCTMとclip、生成文字のpaint envelopeの包含、生成fontの所有、入れ子の違反0、no-opの画素・glyph plan・font再利用。
  - 6種のCTMの配置で、生成glyphのplan・page座標・画素がpage-entry版と一致すること。clipが同じpage矩形のままで、suffixの描画と領域外の画素が元PDFと同じであること。対照として、clipを外すと領域外の画素が変わり、境界の証跡も拒否すること。
  - inkの包含: 辺上と余白内のinkは計画・保存とも拒否して何も公開しないこと。余白の外なら書けること。計画の判定を外してもwriterが拒否すること。再編集で辺に達するinkの拒否。保存後のpaint envelopeが外へ出た場合の拒否（対照）。
  - sidecarの14種・programの5種の改ざんの拒否。page-entry blockや、clipより前のsource slotの書き直しがclipのoperatorの位置を動かしても、同じauthorityであること。late failureのrollback。clipのない境界のauthority・binding・blockが以前と同じ形であること。
- **`q ... Q` scopeの内側**: [tests/test_scope_boundary.py](../tests/test_scope_boundary.py)で次を確認する。
  - 候補と`graphics_state_scope`の記録。深さ1のscope・閉じた兄弟scopeの後・scope内で閉じたmarked contentは候補になる。深さ4、marked content・`BX`と交差するscopeは拒否する（深さ2・3は下記）。ExtGState・特異なCTM・多角形のclip・描画モードはscope内でも拒否する。`q`/`Q`が釣り合わないページは扱わない。
  - lifecycle（identity、相殺、矩形clip、相殺と矩形clip）: fits → grow → second → shorten → regrow → no-op 3回。reopen、authority・作成証跡の不変、`q`と対応する`Q`の間のblock、blockの`Q`の直後・scope内のsuffix・対応する`Q`の直後・scopeの後の状態、入れ子の違反0、生成fontの所有、no-opの画素・glyph plan・font再利用。4種とも、生成glyphのplan・page座標・画素がpage-entry版と一致する。scopeの最後の境界では、blockの直後が対応する`Q`になる。
  - 改ざん: sidecarの13種（scopeの記録・boundaryの深さ・contract・bindingの位置）と、programの8種（別の`q`・別の`Q`・`Q q`による対応や所属の変更・深さ2・深さ0・釣り合わない`Q`・対応する`Q`の後ろへのblockの移動）の拒否。
  - source slotがscopeの前・prefix・suffix・後にある場合の、同じtransactionでの書き直し。scopeの端を消費するmutationの拒否（単体）。late failureのrollback。scopeの外の境界の形の維持。
- **2段の`q ... Q` scope chain**: [tests/test_scope_chain_boundary.py](../tests/test_scope_chain_boundary.py)で次を確認する。
  - 候補と記録（`outer`・`inner`、各段の`q`・`Q`とstack上の対応、`restored_state`）。証明できるchainと、拒否するもの（深さ4、marked content・`BX`と交差する段）。ExtGState・特異なCTM・多角形のclip・描画モード・text clipは、chain内でも拒否する。
  - boundary IDが、内側・外側の段と`q`/`Q`の対応を含むこと。
  - lifecycle（identity、相殺、矩形clip、外側の矩形clipと内側の相殺）: fits → grow → second → shorten → regrow → no-op 3回。4つの`q`/`Q`の位置と対応、blockの`Q`・内側の`Q`・外側の`Q`の直後の状態、入れ子の違反0、生成fontの所有、no-opの画素・glyph plan・font再利用。
  - 配置: 上の4種と、LibreOffice原本と同じ形（外側の矩形clip・内側の平行移動）で、生成glyphの計画のpage座標・保存後の原点・画素がpage-entry版と一致すること。
  - 改ざん: sidecarのauthority 16種・bindingの7種と、programの13種の拒否。
  - source slotがchainの前後や各段のprefix・suffixにある場合の再binding。4つのどれかを消費するmutationの拒否（単体）。late failureのrollback。chainの外の境界の形の維持。
- **外部評価**: [評価コード](../evaluations/continuation/boundary_destination.py)は、LibreOffice原本の6ページで評価者が確認した境界を使う。この境界は、本文の`q ... Q`とCC-BY-SAロゴの`q ... Q`の間にある。結果は[評価README](../evaluations/continuation/README.md#確認済みpage-program境界の評価)にある。
- **外部評価（scope・clip）**: [評価コード](../evaluations/continuation/scope_destination.py)は、同じ原本の10ページで評価者が確認した深さ1の境界を使う。本文の`q 0 0.1 595.2 841.8 re W* n ... Q`の内側で、最後の行の`Q`の後、対応する`Q`の直前にあり、ページ全体の矩形clipを継承する。1ページ・10ページの検査結果、確認時の拒否（深さ2・図版のclip）、bindingの`q`・`Q`の改ざんの拒否、page entryの対照との比較も記録する。結果は[評価README](../evaluations/continuation/README.md#pr-15-engineでのq--q-scope境界の評価)にある。

## 生成fontの寿命

以前の実装は、保存ごとに新しいsubsetを空いている`/PRFn`へ追加し、古いaliasを残していた。PR #6の外部評価では、Type0 fontが4個から17個に、PDFが371KBから570KBに増えた。no-op保存でも増えていた。

古いaliasが残ったのは、resource辞書から外さなかったからだけではない。writerは再編集時、置き換えた文字の表示operatorだけを非描画の数値`TJ`へ書き換え、各生成glyphの`/PRFn size Tf … Tm`は残す。dormant slotの`[] TJ`挿入文脈とTc/Ts witnessも、生成aliasを`Tf`で選ぶ。そのため古いaliasは最終page programから参照され続け、font graph全体が到達可能なままだった。

現在は、shared flowの再保存で次の契約を守る。

- **所有の証跡**: sidecarの`generated_fonts`に、pageとaliasごとの記録を残す。記録するのは、pdfengineが埋め込んだsubsetのFontFile2 SHA-256、BaseFont、provider（元file・instanceのSHA-256、face、variations）、slot・paragraph、作成revisionである。記録を作るのはそのsubsetを書いた保存だけで、以後はpage resourceが同じbytesを保つ間だけ引き継ぐ。open時にpage resourceと照合し、一致しなければ復元しない。
- **再利用と置換**: 候補になるのは記録のあるaliasだけである。そのaliasで描かれるglyphがすべて今回の計画で消費され、保持するcodeにも使われない場合に限り、新しいsubsetの名前として使う。書込値が同じgraphなら既存objectをそのまま残し（no-op）、異なれば置き換える。置き換えた旧graphはpage-local辞書から外れ、既存の到達可能性GCで落ちる。この条件はcommit時に全計画について再検証し、保存直前にも既存objectのFontFile2 hashを再照合する。
- **元resourceの保護**: 記録のないfontは候補にならない。元PDFのfont、別producerのfont、`/PRF`で始まっても記録のないfont、旧版sidecarで作られたfontが該当する。aliasは削除しない。共有・継承されたresource辞書も直接は変更せず、page-localの複製だけを変える。元fontの指紋が変われば、Transactionが保存を拒否する。
- **aliasを削除しない理由**: 生成aliasは非描画operatorから参照され続けるため、resource辞書から外すと未定義resourceの参照になる。dormant slotのaliasは直前のsubsetを保持したまま残る。残るのはaliasごとに1つで、保存回数には比例しない。次に文字を描く保存で置き換わる。glyphを持たないsubsetへの差し替えは行っていない。これは容量の最適化であり、寿命の正しさとは別である。
- **残る累積**: 生成block内の非描画operator（旧glyphの`Tf … Tm [-n] TJ`）は、保存ごとに増える。fontとは独立した課題である。marker・mutation map・rebindingに関わるため、今回は変更していない。

記録を持たない他の保存経路（単独paragraph編集、editable、story flow）は、従来どおり空いているaliasへ追加する。

外部原本の系列で、Type0 fontは全保存で4個のままだった。所有する生成fontは4ページ1、5ページ2、6ページ1である。no-op 3回は新しいfont objectを書かず、全ページの画素・記録・計画glyphも不変だった。PDFはno-op 1で374,492 byte（PR #6では570,054 byte）になった。その後の増加（1回あたり約300 byte）は、page program内の非描画operatorによる。同一ページ2 destinationの外部原本評価では、6ページの生成fontがslotごとに1つ（計2つ）になり、Type0は5個だった。所有slotは全保存で入れ替わらず、no-opでは増えなかった。

## PDF 1.xのoperator nesting

pdfengineが書くcontent streamは、出力PDFのversionのoperator nesting規則に従う。出力のversionは元PDFのheaderと同じにする。以前はpypdfの既定により、出力は常に`%PDF-1.3`になっていた。

- **規則**: text object（`BT ... ET`）内に置けるのは、一般graphics state・色・text state・text位置・text表示・marked contentのoperatorだけである（PDF Reference 1.3〜1.7の4.1節Figure 4.1、ISO 32000-1の8.2節Figure 9）。
  - 特殊graphics state（`q`・`Q`・`cm`）はpage記述レベルに置く。
  - marked-content sequenceとtext objectは、それぞれ正しく入れ子にする（PDF Reference 9.5節、ISO 32000-1の14.6節）。
  - PDF 2.0ではtext object内の`q`/`Q`も許され、そこでは`Tm`/`Tlm`も保存・復元される。pdfengineは元のversionを保つので1.xの形で書く。versionを2.0へ上げて既存のbytesを通すことはしない。
- **原因**: paragraph writerは、新しい文字のtext state（font・size・Tz・Tc・Tw・Ts・fill）を`q ... Q`で隔離し、それを既存text object内の編集位置へ挿入していた。生成blockは`q BT q ... Q ET Q`、dormant styleのwitnessは`q ... [] TJ Q`だった。MuPDF・Poppler・pypdfは受理していたが、PDF 1.xの規則には合わない。
- **source paragraphの再編集**: 選択した最初のoperatorの直後でsource text objectを閉じる（`ET`）。新しい文字と各witnessを、それぞれ独立した`q BT ... ET Q`としてpage記述レベルに置き、text objectを開き直す（`BT`）。
  - `Q`が、編集位置のgraphics state（font・size・Tc・Tw・Tz・TL・Ts・Tr・色・CTM・clip）をそのまま戻す。
  - 新しい`BT`は`Tm`と`Tlm`だけを単位行列に戻す。直後の`Tm`で両方を元のline matrixにし、数値だけの`TJ`で`Tm`を元のoperatorの後の位置へ進める。数値は`Q`で戻った元のsizeとTzで換算する。この復元は、以前のwriterが`Q`の後に置いていたものと同じである。
- **分割できない場合**: text objectを閉じて開き直せないときは拒否し、出力は作らない。
  - 編集位置で、そのtext object内で開いた`q`・marked-content sequence・`BX`が閉じていない場合。閉じると交差する。
  - そのtext objectにclipping描画モード（Tr 4〜7）の文字がある場合。clipは`ET`でまとめて適用されるためである。
- **生成block**: `marker q BT ... ET Q marker`で、text object内に`q`/`Q`はない。再編集では上と同じ分割がblockの`q ... Q`の内側で起き、block内に複数のtext objectができる。
- **同じ原因の他のwriter**:
  - `compose_selected`は同じ分割で隔離する。
  - `edit_reflow`の`q`/`Q`は、状態を変えない`Tm`・`Tj`だけを囲んでいたため取り除いた。
  - 要素の平行移動（text-move）は、text operatorを`q cm ... Q`で包んでいた。これを、text objectを閉じて`q cm BT ... ET Q`の中で描き、前後で`Tm`/`Tlm`を復元する形にした。
  - pathを動かす`q ... Q`はpage記述レベルにあるため、変更していない。
- **検査**: [operator_nesting.py](../pdfeditor/operator_nesting.py)は、pdfengineが書くoperatorに限った狭い検査で、汎用のPDF validatorではない。既存の`operators()`で分解し、次を調べる。
  - text objectの入れ子と、`q`/`Q`の対応。
  - text object内の特殊graphics stateと、page記述レベル専用のoperator。
  - marked contentとtext objectの交差。
- **旧形式の出力**: この変更より前にpdfengineが書いたPDFは、text object内に`q`/`Q`を含む。
  - bindingに`operator_nesting`がない生成blockは、旧規則（text object内の`q`/`Q`を許す）で検証する。旧sidecarは引き続き開ける。
  - 生成blockはmarker・binding・hashでpdfengineの所有を証明できる。一方、source slotを再編集した`q`/`Q`にはsidecarに所有の記録がなく、byteの形から所有を推測することはしない。
  - shared flowの保存はparagraphのすべてのslotを書き直す。そのため旧形式の出力の再保存は、source slotの旧`q`の内側で分割が必要になって拒否され、何も公開しない。旧形式を書き換える正規化はしていない。準拠した出力が必要なら、元PDFから編集し直す。
  - この変更後のengineが元PDFから作る出力は、何回保存しても上の規則を満たす。

## Transactionと評価

最終allocationを先に確定し、既存operatorのmutationと新しいblockの挿入を同じ`Transaction`へ登録する。保存は一回。glyph/font/paint/領域外画素を検証し、mutation mapで全fragmentをbindingして再openできた後だけPDFとsidecarを公開する。binding・検証・公開途中の例外は自分が公開したファイルをrollbackする。プロセス停止を含む二ファイルのOS-level atomic replaceまでは保証しない。

回帰は[tests/test_continuation.py](../tests/test_continuation.py)、外部原本の系列評価は[evaluations/continuation](../evaluations/continuation/README.md)にある。元PDFの同文operator replayと、明示providerで再組版した出力のno-opは別々に評価する。外部原本では、regrowが同じ生成slotへ戻り、final no-opで全10ページがMuPDF・Popplerとも全画素一致した。page-entryのpaint順序は明示契約であり、任意のPDF抽出器の読み順をparagraph意味順へ変える仕組みではない。

同一ページの複数destinationと、その順序契約、確認済みのpage level境界、page level境界でのCTMの相殺と矩形clipの継承、1つの`q ... Q` scopeの内側の境界は上記で扱った。LibreOffice原本の1ページ・10ページでは、深さ1・矩形clipの境界が候補になり、10ページで系列評価を行った。原本でidentity以外のCTMを持つ境界は、どれも各行・下線・画像の`q`が本文や図版の`q`の中にある深さ2で、残る拒否理由は`nested-graphics-state-save`だけである（10ページの6つは組み立て中のpathも持つ）。その後、この深さ2を、最大2段の明確に証明されたscope chainとして1段だけ緩めた（[上記](#2段のq--q-scope-chainの内側)。実装時は合成PDFで確認）。PR #18で原本10ページの系列・状態復帰を確認し、その後、source operandを基準にした逆行列と両モデルの上界証明で、残ったPoppler差をfocused評価の0画素まで解消した。構造の障壁としては、`q`の深さ4以上とExtGState（不透明度・blend・soft mask）が残る。新規ページの自動生成ではない。
