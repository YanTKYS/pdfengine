# 既存ページ上の確認済みcontinuation destination

**外部PDF評価済み（2026-09-24）**。Windows環境で、対象の外部LibreOffice PDFの1 paragraphについて系列評価を完了した。内容は、確認済みの6ページdestinationを使った`overflow → reopen → re-edit → shorten → regrow → no-op`と、容量拒否である。その後、再保存で生成fontが累積する問題を修正し（[下記](#生成fontの寿命)）、同じ系列をno-op 3回まで拡げて再評価した。最終engineでの全suiteは642 passed / 7 skipped / 0 failed（Windows）である。結果は[評価](../evaluations/continuation/README.md#現行の結果)と[公開集計](../evaluations/continuation/summary.json)にある。確認したのは単一原本・単一destinationの範囲であり、任意のPDFで自然な再レイアウトができることは示していない。経緯は[再開地点と検証状況](continuation-checkpoint.md)を参照。

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
)
flow = confirm_shared_flow(
    source, reviewed_stories,
    # 通常のflow/region/paragraph/保護領域の明示契約
    **reviewed_flow_arguments,
    continuation_destinations={destination["destination_id"]: destination},
)
```

destinationは`destination_id / paragraph_id / region_id / page / bounds / provenance`に加え、`authority`、確認時PDF hash、canonical page program hashを保持する。`authority`には挿入位置、z-order、初期graphics state、page transform/CropBox、透明度group、font policyを記録する。region geometryだけを渡す呼出しや、不明な描画状態は拒否する。

今回の挿入位置は**page program先頭、既存描画より背面**。1ページにつき1つのdestinationがその境界を所有し、destination boundsは確認済みshared region全体と一致させる。これは任意の描画contextから状態を推測するAPIではなく、callerが明示的に選ぶ限定的な描画policyである。

先頭の初期CTMはidentity、clipはページのCropBox、不透明度は1、fill/strokeは初期値、blend/maskは初期状態。独立した`q BT ... ET Q`で状態を閉じ、必要なdevice fill、font、size、Tz、Tc、Ts、Tmを既存writerが設定する。既存paragraphのfont resourceやclipは参照しない。各論理styleに確認済みproviderが必要で、生成resourceは対象ページへ独立に追加する。

ページ自身のgroupは、未指定か、明示device色空間を持つisolated・non-knockout透明度groupのみ扱い、その辞書を証跡に含める。回転、UserUnit変更、Default色空間の上書き、不明なgroup/解析エラーは確認不能として拒否する。既存content内部のclipは生成objectより後に実行されるため借用しない。保存後も生成文字のCTM・clip・paint stateと挿入範囲を再照合する。

## 容量・所有・衝突

| 概念 | 意味 |
|---|---|
| source slot | 既存paragraphとregionに属するglyph/operatorの編集権限 |
| confirmed destination | 特定paragraphが既存page/regionへ新しいtext objectを作る権限。まだslotでもpaintでもない |
| generated slot | destinationを実際に使用したときに生まれる物理fragment。独立ID・owner・destination IDを持つ |
| capacity | regionの明示幅・baseline・bottomとparagraph policyで計算した有限の行容量 |

destination全域の空きを確認する。glyph、画像、path、shading等の非text paint、annotation、widget、linkが重なる場合は拒否する。外接矩形による判定は保守的な拒否方向に使い、pathの内側や背景を勝手に空きへ変換しない。別paragraphの現在の文字は、それが同じtransactionで退く予定でも空きとみなさない。生成後の検査で除外するのはそのgenerated slot自身のglyphだけである。

regionはページ内で、保護領域および他regionと交差してはならない。最終計画間のglyph ink衝突もTransactionで検査する。全確認済み容量を超えれば拒否し、font/leading/widthやpage数を変えない。

## Persistenceと再編集

生成IDはdestination ID、paragraph ID、region ID、pageのdigestから定まる。slotには`creation_provenance = generated-from-confirmed-continuation-destination`、生成時mutation record、確認契約hash、物理editable bindingを記録する。確認契約はsource slotとdestinationの権限を固定し、後から生成されたslotの数には依存しない。

挿入blockにはID由来の一意な開始・終了comment markerを置く。`destination_bindings`は現在revisionのprogram digest、block digest、範囲を保持する。最初のglyph対応はmutation anchorから解決し、再保存ではbyte mutation mapによる終了位置の対応も照合する。slot内のglyphがそのblock内にあること、他slotのglyphを含まないことを検証する。xref番号やgeometryだけでは再bindingしない。

長文化→短文化で不要になったgenerated slotは文字を除去し、`occupancy=None`のdormant状態にする。非描画`[] TJ`の挿入context、destinationとslot IDは残る。確認済みtracking/riseにはstyleごとの非描画Tc/Ts witnessを保存し、再open時に数値とprogram証跡を検証する。これはpaintされたglyphが存在するという主張ではない。再長文化は同じslotへ描画する。

region境界はUnicodeへ改行を追加しない。style spansとparagraph IDは一本のまま、first-line indentは最初のvisual lineだけに適用する。justifyの末尾行処理は既存のparagraph continuation policyを引き継ぐ。

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

外部原本の系列で、Type0 fontは全保存で4個のままだった。所有する生成fontは4ページ1、5ページ2、6ページ1である。no-op 3回は新しいfont objectを書かず、全ページの画素・記録・計画glyphも不変だった。PDFはno-op 1で374,492 byte（PR #6では570,054 byte）になった。その後の増加（1回あたり約300 byte）は、page program内の非描画operatorによる。

## Transactionと評価

最終allocationを先に確定し、既存operatorのmutationと新しいblockの挿入を同じ`Transaction`へ登録する。保存は一回。glyph/font/paint/領域外画素を検証し、mutation mapで全fragmentをbindingして再openできた後だけPDFとsidecarを公開する。binding・検証・公開途中の例外は自分が公開したファイルをrollbackする。プロセス停止を含む二ファイルのOS-level atomic replaceまでは保証しない。

回帰は[tests/test_continuation.py](../tests/test_continuation.py)、外部原本の系列評価は[evaluations/continuation](../evaluations/continuation/README.md)にある。元PDFの同文operator replayと、明示providerで再組版した出力のno-opは別々に評価する。外部原本では、regrowが同じ生成slotへ戻り、final no-opで全10ページがMuPDF・Popplerとも全画素一致した。page-entryのpaint順序は明示契約であり、任意のPDF抽出器の読み順をparagraph意味順へ変える仕組みではない。

次の最小の構造障壁は、同一ページの複数destinationや、先頭以外の描画境界へ独立した挿入権限を与えることである。必要なのは境界ごとのstate/clip/paint順序の証跡と複数挿入の順序契約であり、新規ページの自動生成ではない。
