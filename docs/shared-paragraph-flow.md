# 独立した段落による共有flow

実行基盤は、その後[安定identityと単一transaction](stable-identity-transaction.md)へ移行した。現在の`edit_shared_flow`は全slotのplanを一つのtransactionで保存・検証し、mutation mapでbindingする。以下の逐次書込と`runs/verified`の記述は導入時の設計・評価記録であり、現mainの実行方式や再評価結果とは区別する。

## 最大障壁の再判断

[混合書式story](attributed-story-flow.md)のLibreOffice原本では、一段落全体の書式を保った再組版と再編集が成立した。一方、段落を短くすると後続段落が固定位置に残った。今回はこの証拠を引き継ぎ、**独立した複数段落が、確認済みの領域容量を共有できないこと**を対象範囲での最大障壁と判断した。業務PDF全体の障害頻度を順位付けした判断ではない。

semantic decorationは有力な残課題だが、この二段落には移動すべき意味上の装飾がなく、今回確認した空きの原因を解消しない。新規continuationやpageも、既存領域内の段落関係を先に定義する必要がある。[最終配置transaction](planned-document-transactions.md)の「最終配置の計測とsource mutationを分離し、途中guardも通す」という考え方を利用する。同じページ上の`follows`によるrigid moveへ、ページをまたぐ独立paragraphの配分を無理に組み込まない。

## Identityとcapacityの分離

`shared_flow.py`の`pdfengine-shared-flow-1`は次を分離する。

| データ | 役割 |
|---|---|
| `paragraphs` | paragraph IDごとのUnicode、authored hard breaks、style registry/spans、typing style。文字列を連結しない |
| `flow` | callerが指定したparagraph順序とregion順序。共有するのは容量であり、paragraph identityではない |
| `paragraph_boundaries` | callerが指定した前後paragraph IDの関係。Unicodeへ改行を挿入しない |
| `regions` | 既存page上の明示した利用可能bounds・width・x・先頭baseline。paddingやheaderは自動推定しない |
| `paragraph_policies` / `follows` | 段落内行間・indent・空状態・break・段落間隔の確認済み方針 |
| `slots` | 既存のsource fragment／空slotと、そのparagraph・regionへの明示した対応。font/clip/paint contextの根拠 |
| `occupancy` | 最終配置で、そのparagraphがそのregionに占める行の範囲。空paragraphと未使用slotを区別 |

取込みには、前段階の検証可能なschema 2のstoryをparagraphごとに渡す。元storyのhashとsource snapshotのhashを残し、共有regionへの対応とpolicyは新しく明示する。現在のgeometryが新policyを既に満たしているとは認定せず、初期状態を`observed-source-uncomposed`として保存する。最初の編集時に新policyに従って最終配置する。

同じ`body`というstyle IDでも、AのregistryとBのregistryは別である。配置先のresource名、元font名、見た目の類似によってparagraph間のstyleを統合しない。元fontの観測と再組版providerの区別は前段階を維持し、今回の共有flowでも選択した全paragraphの文字を各自の明示providerから再生成する。無編集の後続paragraphも、Unicodeと書式identityを保持して再組版する。

## 確認済みのspacing / break policy

現在対応する契約は限定する。unknownな間隔や空段落の意味を補完するdefaultsは設けない。

- `minimum_baseline_gap`は先行paragraphの最終baselineから後続の先頭baselineまでの、callerが確認した最小距離。実際の距離は、この値と「前行のdescent＋次行のascent」の大きい方とする。
- regionをまたぐ場合の間隔は`reset-to-region-baseline`を明示する。前pageの距離を次pageの座標へ足さず、新regionの確認済み先頭baselineへ配置する。
- `min_line_height`と`first_line_indent`はparagraphごとに指定する。indentを適用するのはそのparagraphの最初の視覚行だけで、page/regionのcontinuationでは0とする。
- `keep_together`はcaller指定の真偽値。trueなら全文が一つのregionに収まる必要があり、残り容量に収まらなければ次regionで計測する。
- `break_before` / `break_after`は`auto`または`next-region`。段落間で両方が指定されても一回だけregionを進める。
- 空paragraphは、caller指定の`reserve-line`、ascent、descentを持つ一つの非描画行として配置する。identityとtyping styleは残り、後続paragraphとの間隔にも参加する。zero-heightやspacing-onlyへ暗黙変換しない。

実PDF評価では、元の二段落の区切りを目視で確認した評価者が、26.5ptを今後の最小baseline間隔として指定する。元のbaseline差に近い値を選んだことと、engineがbbox gapから一般的なmarginを推定することを区別する。region先頭では間隔を持ち越さない方針と、空paragraphのascent 8.4pt / descent 2.1ptも評価者の確認値である。

## 全paragraphの最終配置を先に計画する

`plan_shared_flow`はparagraph IDごとの編集要求を受ける。offsetはそれぞれのparagraphの入力Unicode列に対するもの。一つのparagraphの長さが変わっても、Bの編集offsetはAの長さに依存しない。

1. 各paragraphの編集を独立して投影し、変更後Unicode・style spans・typing styleを確定する。
2. paragraph順序、gap、break policyを使って、共有regionの残り容量へ配置する。
3. 既存shaperで変更後の全文を計測し、完全な行単位でregionへ割り当てる。soft wrap / page breakをlogical hard breakへ変換しない。
4. 各fragmentを下位paragraph plannerでも計測し、最終の行位置・width・ascent・descentが一致することを確認する。
5. 描画されるglyph inkと現在の他slotのsource glyph envelopeから、先に退く必要があるslotを求め、逐次書込順を計画する。
6. 各slotを既存writerで変更し、すべての途中guardを通す。最後に全paragraphのbinding、policy、fragment範囲と実際の配置を検証して公開する。

前段階の固定storyと同様、fragmentはparagraph内の`range`と`render_end`を持つ。境界の非描画空白は論理列に残る。page/region境界はparagraph ID付きの物理配置情報であり、paragraph境界とは別の配列に保存する。

計画はpaint permissionではない。依存関係の検査はsource bboxを使う保守的な順序決定なので、cycleの拒否だけで「全順序で不可能」と証明したとは扱わない。一般solver、順列探索、compound writerは導入しない。

## Source mutationとdestination binding

各source slotはparagraphとregionの組に一意に属する。一つの組に複数の挿入contextがある場合は今回統合しない。最終配置先に当該paragraphの確認済みslotがなければ、配置前に拒否する。別paragraphのslotやfont resourceを借りたり、透明なtext objectを無根拠に新設したりしない。

空になった**物理slot**は容量を消費しない。全文削除されても存在する**論理paragraph**は確認済みの1行分を消費する。この二つを区別するため、前者の`occupancy`はnull、後者は非描画行のmetricsを持つ。どちらも既存の空slot bindingでgraphics stateを検証し、後の再入力に使う。

`DestinationParagraph`でparagraph自身のstyleを配置先contextへ結び、`edit_document`が対象glyphのtext operatorだけを変更する。別slotは現在のPDF revision、同一glyph/code/width witnessとbyte mutation mapで再bindingする。元resource、未選択glyph、paint、image、annotation、clipへのguardを解除しない。元glyph codeの再使用数を0として記録し、provider由来のCID/GID/`W`と計測の一致を検証する。

共有領域に含めなかった第三のparagraph等は固定のままである。`follows`は下にある文章すべてを移動する許可でも、背景・罫線の所有やresize許可でもない。

中間PDFとsidecarは一時領域に置く。出力の不一致や後半slotの拒否では最終成果を公開せず、公開途中の例外では自分が公開したPDFを削除する。2ファイル保存はOSクラッシュに対する同時原子性までは持たない。

### 実PDFで判明した空slotのclip参照問題

最初の実PDF試行は短文化に成功したが、伸長の途中で空slotのgraphics stateが変わったとして拒否された。保存前後の状態を比較すると、clipのrule、path、CTMは同一で、`at`の仮想page参照だけが`-59`から`-71`へ変わっていた。これはclipの変更ではなく、完全保存によるPDF object番号の変更だった。失敗したtransactionの最終PDFとsidecarは公開されていない。

`document_flow._rebind`を共通原因に対して修正した。clipの証跡は`[仮想page xref, operator ordinal]`なので、番号を単純に無視せず、元のpath終端命令のbyte位置を既知のtext mutation mapで保存後へ写す。その命令が編集で消費されずbyte単位で一致し、保存後の同じ位置の命令へ対応する場合だけ参照を更新する。そのうえで順序付きclip列のrule・path・CTMと、空slotの残りのgraphics stateを従来どおり照合する。Form内やtext clippingなど、この対応を確認できないclipは拒否する。

回帰ではpage object番号とoperator ordinalの両方を変える保存、空段落の復元と再入力を確認する。形状・rule・scopeの変更、誤ったbyte map、clip証跡の消費、別streamへの参照は許可しない。実際の失敗時PDFでも、修正後の再bindingとeditable modelの復元を確認した。共通backendが変わったため、その前の短文化成功を最終結果へ混ぜず、修正を固定した状態で対象の編集系列を再実行する。

## API

```python
flow = confirm_shared_flow(
    source, {"paragraph-A": story_a, "paragraph-B": story_b},
    flow_id="body", paragraph_order=["paragraph-A", "paragraph-B"],
    regions=reviewed_regions, region_order=["page-1-body", "page-2-body"],
    slot_regions=reviewed_existing_fragment_regions,
    paragraph_policies=confirmed_paragraph_policies,
    follows=[{
        "before": "paragraph-A", "after": "paragraph-B",
        "minimum_baseline_gap": 26.5,
        "region_start": "reset-to-region-baseline",
    }],
    protected_regions=reviewed_fixed_page_regions,
)
report = edit_shared_flow(source, flow, output_pdf, output_model, {
    "paragraph-A": {"edits": [
        {"start": a_start, "end": a_end, "runs": replacement_runs},
    ]},
    "paragraph-B": {"edits": [
        {"start": 0, "end": 2, "text": "担当", "style_id": "body"},
    ]},
})
```

`reviewed_regions`の各値は`{page,bounds,x,width,first_baseline}`。`slot_regions`はparagraph ID → 取込みstoryのfragment ID → region IDのmapping。`paragraph_policies`はparagraph IDごとに上記六属性を明示する。実行可能な入力例は[評価コード](../evaluations/shared_flow/evaluate.py)と[回帰テスト](../tests/test_shared_flow.py)にある。

## 評価の境界

対象は前回のLibreOffice原本4/5ページの混合段落165文字と、5ページの次段落140文字。前者は既存二slot、後者は既存一slotを持つ。4ページは1行分、5ページは二段落を含む明示領域を共有する。5ページのその次の段落と、全ページの画像・paint・annotationを固定する。

前段階で同一source範囲のno-opを確認した二slotは、replay program・artifact・関連コードhashを照合して証跡を再利用する。新しく加えた後続段落はsource operator replayを新たにMuPDF・Poppler・独立抽出で検証する。新しい編集の各保存については、paragraph別Unicode/style/typing、paragraph boundary、spacing、fragment allocation、destination context、CID/GID/`W`、対象外ページ・領域を検証する。

同文再編集の画素一致は、providerで再組版した直前のPDFに対する一致である。元PDFのoperator replayと、providerによる再組版のfont輪郭同等性を混同しない。今回も単一原本の評価であり、任意PDFに対する成功率ではない。

## 検証結果と今回の区切り

修正後の`runs/verified`はengineコードを固定したまま完了した。[公開集計](../evaluations/shared_flow/summary.json)にhashと検証範囲を保存する。

| 操作 | A文字数 | B文字数 | 結果 |
|---|---:|---:|---|
| A短文化 | 10 | 140 | Bを5ページ先頭へ前詰め、Unicode・書式不変 |
| A伸長 | 185 | 140 | Aを4/5ページへ配分、Bを下方へ再配置 |
| A全削除 | 0 | 140 | 空段落のidentity・typing・指定占有を保持 |
| A再入力＋B独立編集 | 185 | 140 | Aの混合書式を復元、B自身のoffsetで置換 |
| 同文保存 | 185 | 140 | MuPDF・Popplerで全10ページの全画素一致 |

全5保存で対象外要素、独立抽出、CID/GID/`W`の検証が通過した。spacing不明、paragraph identity統合、provider変更、容量不足の4件は最終出力を作らず拒否した。画像の目視確認はv1の4/5ページ、v2の5ページ、v3の4ページ、v4の5ページで行い、文字重なりや周辺破損は見られなかった。

共有flow関連の87ケースは共通clip修正前に確認済み（初回86成功・テストhelper再帰1失敗を修正し、該当lifecycleと保存を強化したbreak policyの3ケースが成功）。共通clip修正後は`test_document_flow.py`と`test_empty_clip_rebind.py`の21ケースが成功した。ユーザーの最短区切りでのcommit指示に従い、**全repository suiteの再実行は未実施**として引き継ぐ。以前の全体結果を今回の全体成功として扱わない。

次の構造障壁は、既存のparagraph所有slotだけでは届かない配置先へ継続するための、容量と描画contextの明示的な契約である。現在はmissing slotとcapacity不足で拒否する。新しいslot/pageを作る場合は、resource・clip・固定paint・page構造のprovenanceを定義する必要がある。今回の二段落に装飾移動は不要であり、全逐次順序が失敗する証拠もないため、semantic decorationやcompound mutationの追加には進んでいない。
