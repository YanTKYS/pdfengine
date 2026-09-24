# 既存ページ上の確認済みcontinuation destination

**開発途中のチェックポイント（2026-09-24）**。利用者の最短commit指示により、この時点では全suiteと外部PDFの全編集系列を完了していない。新規APIは検証中として扱う。[再開地点と検証状況](continuation-checkpoint.md)を参照。

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

## Transactionと評価

最終allocationを先に確定し、既存operatorのmutationと新しいblockの挿入を同じ`Transaction`へ登録する。保存は一回。glyph/font/paint/領域外画素を検証し、mutation mapで全fragmentをbindingして再openできた後だけPDFとsidecarを公開する。binding・検証・公開途中の例外は自分が公開したファイルをrollbackする。プロセス停止を含む二ファイルのOS-level atomic replaceまでは保証しない。

回帰は[tests/test_continuation.py](../tests/test_continuation.py)、外部原本の系列評価は[evaluations/continuation](../evaluations/continuation/README.md)にある。元PDFの同文operator replayと、明示providerで再組版した出力のno-opは別々に評価する。page-entryのpaint順序は明示契約であり、任意のPDF抽出器の読み順をparagraph意味順へ変える仕組みではない。

次の最小の構造障壁は、同一ページの複数destinationや、先頭以外の描画境界へ独立した挿入権限を与えることである。必要なのは境界ごとのstate/clip/paint順序の証跡と複数挿入の順序契約であり、新規ページの自動生成ではない。
