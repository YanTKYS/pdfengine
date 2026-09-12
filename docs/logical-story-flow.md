# 一つの論理文章と明示した物理領域列

## 最大障壁の判断

[可変container](variable-container-paints.md)で枠は確認済み最大領域まで伸縮できたが、最大領域超過は残る。外枠形状や逐次順序を変えても、許諾された配置先そのものが増えるわけではない。今回は、内容を次の確認済み領域へ継続する**論理モデルの不在**を選んだ。全業務PDFについての頻度順位を測った判断ではない。

compound mutationが必須となる「全逐次順序が不成立で最終配置のみ安全」という証拠は前段階から増えていない。未知の装飾形状とnested ownershipも別の障壁だが、本評価の外部PDFには必要ない。今回は固定の既存領域列を扱う。新規ページ作成、可変枠をmaxまで伸ばしてからflowする方針、ページ全体reflowを導入しない。

## 論理・物理の分離

`pdfengine-story-flow-1`は次を別々に保持する。

| 層 | 契約 |
|---|---|
| logical | 一つのparagraph ID、一本のUnicode列、論理style span、authored hard break |
| flow chain | callerが明示したcontainer IDの順序と対象paragraph ID。座標で並べ替えない |
| container | page、使用可能領域、幅・baseline・leading、固定サイズ、固定paint policy |
| physical fragment | 全体Unicode range、描画対象の末尾offset、source glyph/コード/slotとのbinding |
| physical break | 境界offset、前後container、同一paragraph ID、観測／生成由来。論理改行は追加しない |
| page policy | 全ページ数、固定の対象外要素、callerが指定した保護領域。headerの自動推定はunknown |

fragmentの下位bindingにも、同じparagraph IDを渡す。既存のeditable writerが使う局所paragraphデータは、全体Unicode列の一部分を表す物理adapterであり、独立した編集identityではない。編集APIは全体offsetだけを受け取るので、ページ境界をまたぐ一回の範囲編集が可能になる。

`confirm_story`の`containers`は次の形式で明示する。`paragraph`は既存の範囲選択・観測APIで得たsnapshot。初期のsnapshotのUnicode列を`chain`順にそのまま連結する。初期に意図する空白やhard breakがある場合は、観測APIのlogical mappingでcallerが確認しておく。

```python
reviewed_containers = {
    "region-A": {
        "page": confirmed_page,
        "bounds": confirmed_usable_bounds,
        "paragraph": reviewed_snapshot,
        "layout": {
            "x": confirmed_x, "baseline": confirmed_baseline,
            "width": confirmed_width, "max_bottom": confirmed_bottom,
            "min_line_height": confirmed_leading, "first_line_indent": 0,
        },
        "paint_relations": reviewed_fixed_paint_relations,
    },
    # region-Bも同じ形式。順序は別引数chainで指定する。
}
```

現在は単一書式に限定する。全containerのfont size、色、tracking、scale、riseを照合し、callerが一つの完全font recipeを再組版用に明示する。論理style spanは一つの`body` spanで、空でもstyle recipeは独立して残る。混合書式への変更、paragraph boundary、跨領域のsemantic decoration rangeは未対応として拒否する。元font resourceを別ページへ移植する方式は使わず、各ページで明示providerから必要subsetを生成する。元resourceは下位writerの検証対象として保持する。

## 改行と省略された空白

container境界を`\n`に置換しない。既存shaperと改行アルゴリズムで各候補行を計測し、現在containerに収まる完全な行の範囲を求める。残りは明示した次containerへ送る。Unicode offsetは初めから終わりまで全体文字列を基準にする。

soft wrapで描かれない末尾空白や、領域境界に一致したauthored hard breakは、全体Unicode列にそのまま残す。fragmentは`range=[start,end]`と`render_end`を別に持つ。`render_end..end`に許すのは描かない空白・CR/LFのみ。次fragmentの開始offsetは`end`である。これにより、ページ末尾の既存改行を下位writerへ渡して余分な空行を追加することも、改行自体を削除することも避ける。範囲の連続性、grapheme境界、全体文字列との一致を保存後に検証する。

空白行だけが次領域に残るケースは、現bindingでは空paragraphと空白行の占有を同一視できないため拒否する。末尾改行を消して通すことはない。

## 計画とsource mutation

1. 全体Unicode範囲への編集を適用する。
2. 宣言順に、固定領域ごとのUnicode range、physical lines、continuationを計画する。
3. 各fragment単独のwriter計測と、全体計画で求めた行を照合する。
4. 既存の`edit_document`でfragmentを順に編集する。source no-op、旧glyph除去、clip、非選択glyph/paint、font resource、領域外画素のguardを維持する。
5. 各保存後、他fragmentのsource glyph・コード・paintを照合する。同じページの空slotはbyte mutationに追従し、別ページではbyte offsetを変更しない。
6. 全fragmentの保存結果、一本のUnicode列、identity、style、flow/page/container policyが一致してからPDFとsidecarを公開する。

計画は衝突証明ではない。各writerが拒否すれば公開しない。既存のparagraph writer、shaper、source再bindingを利用するが、同一ページ内のparagraph `follows`用schedulerを跨ページ用へ無理に流用しない。複数領域の同時source書換えや中間guard解除は導入しない。例外時の公開rollbackは行うが、2ファイルのOSクラッシュ原子性は未保証。

## 外部原本と今回の意味入力

最初に新潟県のWord由来「説明会・公聴会の開催について」で、1ページ目末尾から2ページ目へ続く連絡先blockを確認した。しかし原本は暗号化されており、既存の「暗号化PDFへ平文sidecarを作らない」制約で取込みを拒否した。暗号化を外して評価原本を作る操作やguard解除は行っていない。

成功した原本は、LibreOffice由来の10ページの`MigrationLibreOffice-ja.pdf`。4ページ目末尾の「トレーニングプロセスにおける別の目的は…」から、5ページ目冒頭の「…わかってもらうことです。」まで、一つの完結した文がページをまたいでいる。この87文字の文を、評価者が一つの論理paragraphとして編集する範囲に定める。原本paragraph全体の意味を自動復元したとは主張しない。

4ページ目glyph 1203–1253、5ページ目glyph 0–35はMS-PMincho 10.5ptで、複数subset resourceを使っている。続く`LibreOffice`はTimes New Roman 12ptである。後続文まで含めると現在の単一書式契約に反するため、その候補は別の負例として確認する。単一書式の文を選ぶことはcaller側の選択であり、PDF名や座標によるengine内の例外ではない。

確認した領域は4ページ目`[66,758,540,785]`、幅471pt、baseline 769.5ptと、5ページ目`[55,130,396,151]`、幅339.2pt、baseline 142pt。各1行分の固定領域である。後続のTimes New Romanの文字・次行・他段落を動かす許可は与えない。ページ上部のCC画像を固定領域として明示し、headerの意味は推定しない。再組版providerはWindows `msmincho.ttc`のface 1（MS-PMincho）。元の複数subsetを同じresourceへ改変する方式ではない。

各source範囲のoperator replayと生成後の同文再組版を別々に確認する。主要な評価は、同程度置換とfragment再配分、境界をまたぐ範囲編集、短文化で前ページへ戻る操作、空状態、再入力、同文再編集である。可変枠・複数行container・多段組は今回の外部原本では同時に評価しない。複数行と同一ページ内の領域列は合成PDFで補う。

最初の書込み後、独立評価器が「対象ページ以外はすべて未編集」と仮定していたため、正当に変更した5ページ目を4ページ目の検証で誤って拒否した。backendは変更せず、評価器に明示的な`edited_pages`を追加した。従来の単一ページ呼出しの意味は維持し、各編集ページの局所差分、宣言外の全ページの文字・画素を別に確認する。宣言外ページを変更した負例で検出が維持されることをテストした。成功済みの1段階目のsource mutationは再実行せず、原本・model・出力PDFのrevisionと再計算したplanを照合して独立監査から再開した。

もう一つの評価器の誤判定は、保存後のlink注釈xref番号をそのまま比較していたことによる。原本と保存後で変更されていたのはxrefだけで、URI・矩形・参照先は一致していた。比較を単に省略する代わりに、注釈graphを決定的な局所参照で正規化し、参照先page identity、全辞書項目、appearanceの復号streamを照合するようにした。xrefの変更は許容し、URI・参照先ページ・appearanceの変更は検出するテストを追加した。

目視では、5ページ目で短くなったfragmentの後ろに空きが残る。続く別書式の文は対象外として元位置を保持する契約だからである。これは原本全paragraphの自然な再組版が成立した結果ではない。選択した一つの文のidentity・書式・継続を保つ能力と、対象外の文章を含めた自然な行詰めは区別する。

## 実PDFの保存結果（2026-09-13）

二つの原本範囲のoperator replayは、ともに元glyph・font resourceを保持し、MuPDF/Poppler 144dpiで全画素一致、独立抽出も一致した。その後、次の6回の保存・再openが通った。下表のrangeは一本のUnicode列に対する半開区間で、全段階の論理hard break数は0のままである。

| 操作 | 全体文字数 | 4ページのrange | 5ページのrange | 結果 |
|---|---:|---|---|---|
| v1 同程度置換・再配分 | 76 | `[0,51)` | `[51,76)` | 成功 |
| v2 旧ページ境界をまたぐ一回の範囲編集 | 80 | `[0,53)` | `[53,80)` | 成功 |
| v3 短文化し前ページへ戻す | 27 | `[0,27)` | `[27,27)` | 成功 |
| v4 全文削除 | 0 | `[0,0)` | `[0,0)` | 成功 |
| v5 空状態から再入力 | 76 | `[0,51)` | `[51,76)` | 成功 |
| v6 同文再組版 | 76 | `[0,51)` | `[51,76)` | 全10ページで両rendererの全画素一致 |

v1は87文字から76文字への同程度置換であり、原本からの長文化ではない。v2は物理分割位置をまたぐ編集、v5は空のsource bindingから次ページへの再配置を確認している。

全段階で、対象2ページのPoppler領域外差分は1pt margin付きで0画素、MuPDFの局所writer guardも通った。非選択文字、元font resource、非text paint、画像、注釈graphを維持し、新規subsetのCID/GID/`W`を照合した。対象外8ページのMuPDF全画素と独立抽出は毎回一致した。Popplerは途中の各段階で対象2ページと対象外1ページ、最後の同文再組版で全10ページを確認した。

対象ページの独立pypdf比較は、元のprefix・新しいfragment・元のsuffixを連結した**ページ全文**について空白を正規化して行う。正規化前のauthored Unicodeとhard breakの厳密な保存は論理sidecarとfragment partitionで検証する。したがって、PDF抽出器が返す改行がそのまま論理改行に一致したという評価ではない。

全削除後に対象文字が抽出から消えることを確認し、再入力後も元のlogical IDを維持した。v1・v4・v5の4/5ページ画像を目視した。CC画像、固定文字、後続段落に破損は見られない一方、前述の固定された別書式の文との空きは残る。結果分類は「論理文章の跨領域編集成功・固定隣接文との空きあり」とする。

負例は5件とも安全に拒否し、PDF・sidecarを作らなかった。

| 負例 | 拒否理由・設計上の境界 |
|---|---|
| 確認済み全領域の容量超過 | 新しい領域やpageを勝手に追加しない |
| 後続の別書式文を含む原本paragraph | 単一書式契約を超える。global style registryが必要 |
| 座標から推定した継続関係 | explicit flow contractが必要 |
| 継続先と明示した保護領域の重なり | page policy違反 |
| 以前のrevisionのmodelを現在のPDFへ適用 | source provenanceの不一致 |

取得元・SHA-256・各保存結果・評価コードのhashは[公開集計](../evaluations/story_flow/summary.json)、実行方法は[評価README](../evaluations/story_flow/README.md)に記録する。暗号化Word PDFの取込み拒否は探索時の結果であり、この5件の最終runner負例には加算しない。

最終関連回帰は **139 passed / 0 failed / 0 skipped（244.80秒）**。新しいstory APIと複数ページ・注釈監査に加え、依存するeditable、空要素、rich layout、proof session、document flowの再binding、および従来のStage 1監査を一回まとめて実行した。継続先の画像・vector・注釈・clipとの衝突、領域重複、途中のwriter失敗時に成果物を公開しないことも含む。

既存engineモジュールは変更せず、`story_flow.py`を追加した。前段階commit `1a69aa9`の全体回帰453 passed / 2 skippedは以前の証拠として保持し、今回の139件と合算しない。全体回帰と全実PDFコーパスの再実行は行っていない。公開集計を作る際に、外部評価が記録した全engineファイル・runner・変更した監査helperのSHA-256と現在のファイルを照合した。

## 次の判断

固定の既存container列で一つのidentityを維持できた後には、複数の論理paragraphが同じcontainer列の容量を共有する配置、混合style/decoration rangeの跨領域維持、新しいcontinuation slot/pageの生成が残る。可変containerとの組合せも、伸長優先・早期break等の明示policyを追加してから判断する。

今回の原本で具体的に次の障害になったのは、同じ元paragraph内のMS-PMincho 10.5ptとTimes New Roman 12ptを一つのlogical style registryで保ち、各destinationのsource描画contextへ結び直すこと。この対応とsemantic decoration rangeの維持が進めば、今は固定して残した後続文を同じ論理paragraphとして再組版する範囲を広げられる。新規page作成だけを先に追加しても、この制約は解消しない。
