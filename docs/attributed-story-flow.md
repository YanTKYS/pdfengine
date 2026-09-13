# 論理書式と配置先の描画binding

## 最大障壁の判断

[前段階](logical-story-flow.md)で、単一書式の一文は同じparagraph IDのまま複数ページへ継続できた。一方、同じLibreOffice原本で後続文まで範囲を広げると、MS-PMincho 10.5ptとTimes New Roman 12ptを含むため取込みを拒否した。短くした一文と固定された後続文との間には空きが残った。これは、今回の範囲で自然な段落編集を妨げる具体的な証拠である。

そこで今回は、一つの論理段落の書式範囲を配置先が変わっても保持する境界を優先した。業務PDF全体の障害頻度の順位を測定した判断ではない。装飾rangeの継続、複数paragraphの容量共有、新規slot/pageにも価値はあるが、どれを先に追加してもこの原本の混合書式拒否は解消しない。全逐次mutationが不可能という証拠も増えておらず、compound writerを導入しない。

## 三つのidentityを分離する

`pdfengine-story-flow-2`は既存のflow契約に次を加える。単一providerの旧schema 1も引き続き受け付ける。

| データ | 根拠と役割 |
|---|---|
| logical paragraph | 同じparagraph ID、一本のUnicode列、authored hard break |
| logical style registry | callerが確認したstyle ID、観測属性、その根拠、再組版provider、独立したtyping style |
| logical style spans | 全体Unicode offsetに対するstyle ID。fragment/page境界では分割しない |
| source observations | 元PDF revision・page・source style ID・font resource・font program hash・snapshot/context hash。意味上のstyle IDにはしない |
| physical style binding | 現在のPDF revision・pageのsource style IDと、logical style IDの対応。glyph/font検証後に生成する |

callerは各containerの観測style IDをどのlogical styleへ対応させるか明示する。resource名やsubset名から自動的に同一styleと判断しない。複数の観測を一つのIDへ対応させる指定でも、font labelと数値属性が異なれば拒否する。等しい名前だけを同一性の根拠にはせず、callerの対応指定と全source witnessを保存する。

font size、fill、horizontal scale、tracking、word spacing、rise、観測colorには観測由来とwitness一覧を付ける。fillの数値tokenは論理属性で数値へ正規化するが、元の表記はsource observationsに残る。weight・italic・scriptは名前から推測せずunknown。任意のlanguage宣言はcallerのmetadataとして保持するだけで、今回のshaper設定を変更するものではない。

registryの属性を自由に書き換えるAPIは今回追加しない。確認済みstyleをUnicode区間へ適用する操作を扱う。元の観測と異なる属性値をmodelへ直接入れると検証で拒否する。

## Source fontとprovider

元PDFのfont resourceは元文字の解釈・除去・対象外文字の維持に使う。再組版providerはcallerがstyleごとに指定した完全fontのpath・face・variation・SHA-256であり、別の概念である。providerとの関係には`confirmed_reflow_provider`または`substituted`を明示する。名前が似ていることをcoverageや輪郭の証明にはしない。

今回のstory経路は、配置先をまたぐ文字を含めて、**全選択文字をstyle別の明示providerで再生成する**。その選択をmodelと計画に明示する。未変更文字だから元codeを別pageへコピーすることはしない。元resourceの保存と元glyph codeの再使用を区別し、storyの元code保持数は0として検証する。

下位の既存paragraph writerには元code保持経路が残る。今回追加した書式付きreplacement runと組み合わせても、範囲外のsource codeを保持するテストを実施する。つまり保持と再生成の仕組み自体を統一してしまったわけではなく、storyの配置方針として再生成を選んでいる。

## Destination context

`destination_style.py`が、検証済みsource paragraphを包む一時的なadapterを作る。元snapshot・削除対象glyph・operator・clip・non-inline graphics stateはそのままsource側の権限として使う。追加styleは`logical:<id>`の別namespaceに置き、source font resource/xrefを持つstyleであるかのように装わない。

追加styleのsizeと横倍率を、正規化したinline basisへ設定する。既存writerがこれを配置先CTMとpage座標へ変換し、配置先のresource辞書へ新しいfont subsetを追加する。source pageのresourceやclipをコピーする方式ではない。各保存の報告にはdestination event・clip・other stateのhashとCTMを残す。

元operator replay、対象glyphだけの除去、非選択glyph、font program/subset/GID/`W`、周囲のpaint・画像・注釈、active clip、領域外画素のguardを引き続き通す。新しいstyleがsource側に存在しなくても、destinationのpaint権限が広がることはない。

横書き・正の軸変換・不透明device fillの既存制約を維持する。回転pageは追加adapterで拒否する。さらに、現在の永続bindingが正規化された`Tc/Tw/Ts`を観測する制約から、新しいdestination styleの非ゼロtracking・word spacing・riseを拒否する。値を0へ変えて通すことはしない。size・fill・横倍率と複数fontの組合せを先に扱う。

## Unicode編集とstyle projection

編集は全体Unicode offsetへ一回適用し、その結果のtext・style spansを先に作ってからflowする。無変更区間のstyle IDはそのまま移し、同一styleの隣接spanは結合する。境界がgraphemeの途中へ入る結果は拒否する。

| 編集入力 | 方針 |
|---|---|
| 一つのstyle内の置換 | `style_id`省略時はそのstyleを継承 |
| 複数styleをまたぐ非空置換 | `style_id`または書式付き`runs`を明示 |
| style境界への挿入 | 両側のstyleが異なるときは明示入力が必要 |
| 書式付き`runs` | 一回の範囲置換の中で複数styleを指定可能 |
| 全文削除 | style registryは残し、spanは空にする。確認済みtyping styleを保持 |
| 空状態への挿入 | style省略時は保存済みtyping styleを使用 |

`typing_style_id`は取込み時に明示し、編集時にも明示的に変更できる。最後に描かれていたfontから推定しない。inactiveなstyleもregistryに残るため、全削除後に複数styleで再入力できる。

各fragmentには全体rangeと局所描画rangeを持たせ、局所style spansは全体spanの切片としてのみ作る。保存後はglyph planのglobal style ID・providerと、実際のglyph/resource・inline属性を対応させる。局所source style IDが変わっても、全体のstyle IDは変わらない。描かれない末尾空白やhard breakの扱いは前段階の`render_end`契約を維持する。

下位の非空paragraph bindingには、描かれないstyleの文字を復元する際、同じfragment内のsource glyph witnessを必要とする制約が残る。空白だけのstyleが内部のsoft wrapですべて描かれなくなる場合などを、別styleへ暗黙に変更して通すことはしない。全文が空の場合は独立したstyle recipeと検証済みslotを使う別経路がある。

## API

```python
story = confirm_story(
    source, reviewed_containers,
    paragraph_id="body-paragraph", chain=["A", "B"],
    protected_regions=reviewed_fixed_page_regions,
    styles={
        "body": {"provider": confirmed_body_font,
                 "provider_relation": "confirmed_reflow_provider"},
        "latin": {"provider": confirmed_latin_font,
                  "provider_relation": "confirmed_reflow_provider"},
    },
    style_assignments=reviewed_source_style_assignments,
    typing_style_id="body",
)
edit_story(source, story, output, sidecar, [{
    "start": confirmed_start, "end": confirmed_end,
    "runs": [
        {"text": "本文と", "style_id": "body"},
        {"text": "PDF 2026", "style_id": "latin"},
    ],
}])
```

`reviewed_source_style_assignments`は、例えば`{"A":{"s0":"body"},"B":{"s0":"body","s1":"latin"}}`の形式。観測結果のIDに対してcallerが確認する対応であり、固定値をengineへ埋め込まない。

## 今回の実PDF評価範囲

前回と同じ10ページのLibreOffice原本を使い、4ページ末尾51glyphと5ページ冒頭114glyphの、完結した元paragraph全体165文字を対象とする。MS-PMinchoの複数subset観測をcallerが`body`へ対応させ、Times New Romanを`latin`とする。4ページは前回と同じ固定1行領域、5ページは元paragraph全体を含む固定3行領域まで使用を確認する。

前回と同一の4ページsource範囲のreplay証跡は、snapshot・program・関連コードhashを照合して再利用する。範囲を広げた5ページは新たにoperator replayと独立renderer・Unicodeを確認する。混合書式の置換、styleとpage境界をまたぐ一回の範囲編集、短文化、全削除、複数styleでの再入力、同文再組版を評価する。

対象外ページ・文字・paint・画像・注釈と、明示したcontainer/page policyは固定する。ページ全体の自動reflow、可変containerとの組合せ、新規slot/pageは今回の対象外である。

### 結果（2026-09-14）

[集計と実行コードのhash](../evaluations/story_styles/summary.json)と[再現手順](../evaluations/story_styles/README.md)を公開する。原本・派生PDF・font・PNG・sidecarはローカル証跡として保持し、公開物には含めない。

| 保存 | 編集 | Unicode長 | A / Bの全体range | 判定 |
|---|---|---:|---|---|
| v1 | 混合書式の全文置換 | 185 | `[0,77)` / `[77,185)` | 成功。latin spanがpage境界を横断 |
| v2 | 書式境界とpage境界をまたぐ一回のrich置換 | 201 | `[0,68)` / `[68,201)` | 成功。新しい境界でも同じstyle ID |
| v3 | 両styleを含む短文化 | 14 | `[0,14)` / `[14,14)` | 成功。Bが空状態へ移行 |
| v4 | 全文削除、typing styleをlatinへ明示変更 | 0 | `[0,0)` / `[0,0)` | 成功。両styleのregistryを保持 |
| v5 | 空状態から両styleで再入力 | 185 | `[0,77)` / `[77,185)` | 成功。bodyも再び利用可能 |
| v6 | 同文・同書式で再組版 | 185 | `[0,77)` / `[77,185)` | 成功。両rendererで全10ページの画素一致 |

6保存すべてで、対象ページ全文の独立pypdf抽出、対象外8ページのUnicode・MuPDF全画素、CID/GID/`W`・provider hash・destination context・元font resource・固定paint/画像/注釈を照合した。対象ページの独立抽出は空白正規化ありで、厳密なauthored Unicodeとstyle spansは論理modelで別に照合する。対象領域外のPoppler 144dpi差分は1ptの境界余裕で0画素。Popplerの対象外ページ比較は途中の各保存で1ページ、最終保存で8ページすべてを行った。

元PDFのno-op operator replayは両対象範囲で成立した。Aは前段階と同一の証跡1件を照合して再利用し、拡張したBは新たに検証した。v6の画素一致は**明示providerで組み直したv5とv6の一致**であり、元PDFとprovider再組版のfont輪郭同等性を表すものではない。

負例は5件ともPDF・sidecarを作らず安全に拒否した。理由は、複数styleをまたぐ置換の書式未指定、異なるstyle境界への挿入の書式未指定、全明示領域の容量超過、配置先のstyle対応不整合、provider hashの不整合である。単に例外発生を数えるのでなく、対応する理由と出力不在を確認した。

v1・v4・v6の4/5ページのPoppler画像を目視した。全削除で対象段落が消え、再入力で日本語10.5ptと英語12ptがページをまたいで戻った。重なりや周囲の文字・画像の破損は確認されなかった。後続段落までの空きは残り、以下の次期障壁の根拠になる。

追加した24件の回帰テストは、異なるCTM間の再binding、size/fill/横倍率、同一font属性でもcallerが分けたstyle IDの維持、元code保持とprovider glyphの混在、style/page境界編集、全削除後の暗黙typingと複数style再入力を対象とする。clip・画像・注釈・vectorの衝突は従来どおり拒否する。共通のUnicode編集処理とparagraph writerを変更したため、既存経路も含む全suiteを一回実行した。**504 passed / 2 skipped、775.57秒**で、skipはpypdf用AES provider未導入による既存2件。実績と追加テストのhashを公開集計の`regression`へ記録した。

この評価は処理時間や長大な段落の性能を保証しない。探索時の過大な容量超過fixtureは中断し、同じ容量超過を成立させる短いfixtureへ変更した。guardの緩和やPDF固有の例外追加は行っていない。

## 装飾と次の構造境界

semantic decoration rangeの思想は維持するが、storyとの接続は今回行わない。decorates関係やlogical decoration rangesは引き続き拒否する。下位APIでも、書式付きreplacement runsと従来のanchor projectionを暗黙に混ぜず、明示的に拒否する。

今回の境界を越えた後には、logical decoration rangeから複数fragmentのphysical paintを再構築する問題と、複数paragraphが領域容量を共有する問題が残る。新規pageを生成してもこれらの意味関係は自動的には得られない。

実PDFの初回編集と再入力後の最終出力では、別書式の部分も同じ段落へ組み込まれ、前回の段落内部の空きはなくなった。一方、後続段落は固定のため、対象段落の行数が減ると段落間の空きが増える。この原本で次に具体化した障壁は、paragraph identityと境界を保ちながら複数paragraphが同じflow領域の容量を共有し、確認済みspacing/break policyで配置することである。複数文章を一本のUnicode列へ単純連結する方式にはしない。
