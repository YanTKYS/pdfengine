# 最終配置計画と複数編集transaction

## 今回の判断

輪郭による衝突証明の後に実測で確認できた障害は、**複数paragraphが固定領域の空間を配分し直す変更を、一つの最終配置として扱えないこと**だった。FCCの仮想プリンタPDFで、Aを短くしてBを長くすれば現在の枠内に収まる。しかしBだけを先に編集すると、現在の低いbaselineで組版して領域外になる。逆方向の変更でも、Aだけを先に増やすと、まだ短くしていないBを押し出して拒否される。

これは今回の対象で確認した主要な障害であり、一般的な業務PDF全体の最大障害を統計的に確定したという意味ではない。前段階の[輪郭による衝突証明](ink-collision-certificates.md)と[明示関係](explicit-document-flow.md)の成功・拒否は引き継ぎ、変更していない外部corpusやbackendは再評価していない。

| 候補 | 引き継いだ証拠・今回の観測 | 判断 |
|---|---|---|
| glyphの再描画とbbox衝突 | FCCの8段階編集が成立済み | 今回は既存backendを変更しない |
| 可変コンテナ | 今回もA/B両方を長くすると固定領域を超える | 必要な範囲は残る。空間配分で解ける拒否と分離する |
| 背景・外枠のresize | 黄色い背景は単純矩形だが、外枠は4つのcubic曲線と2つのsubpathを持つfill。共有背景には未編集のフランス語も載る | 外枠を矩形としてscaleしたり、Aだけの所有物にしたりする根拠がない |
| 複数編集の最終計画 | 個別操作が拒否されても、A/Bの最終baselineと内容量を同時に計測すれば既存枠に収まる | 今回の実装対象 |
| compound source mutation | 短文化で場所を空けてから長文化すれば、すべての途中guardを維持できる | この例では不要。安全な順序がない例は未解決 |
| ページ境界・改ページ | 障害は同じページの黄色い注記枠内 | ページflowを導入する根拠にはならない |

## 4層の分離

1. **semantic relation**：確認済みの論理ID、`contained_by`、`follows`、paint所有。座標からの追加推定はしない。
2. **layout policy**：既存modelの固定コンテナ、overflow拒否、明示幅、baseline gap、行間、空paragraphの先頭baseline確保。paddingはunknownのまま。
3. **final layout plan**：全変更後の文字列を同じshaperで計測し、関係を満たす最終baselineと最終行を計算する。入力SHA・model SHA・編集入力hash・計画hashを持つ独立した`pdfengine-flow-plan-1`を返す。
4. **source mutation**：計画した順序で既存の`edit_flow`を実行する。元operator削除、新glyph描画、未編集glyphのrigid moveを個別に証明する。

`follows`が背景のresize許可を兼ねることはない。既存の`pdfengine-document-1`を変更せず、計画は別の一時的なartifactとした。確認済みの旧sidecarを移行・再推定する必要はない。

## 計画と実行

`plan_flow(source, model, changes)`は読取専用。変更ごとに現在位置で組版してから並べ直すのではなく、先行要素の**変更後**の最終baselineから後続の最初のbaselineを決め、その位置で組版する。変更しない要素は元のbaseline間の高さを保つ。rootsの位置、幅、利用可能bottomは固定である。

各identityの変更後の高さと変更前の高さを比較し、短くなるもの、同じ高さのもの、長くなるものの順に処理する。同じ分類内では`follows`の逆順とする。`follows`の向きを逆転させるのではなく、下位要素から処理する実行順の規則である。各prefixについても論理extentと所有paintの固定コンテナ内配置を確認し、成立しなければ書込前に拒否する。

これは限られた順序規則であり、全順列探索や一般的なconstraint solverではない。最終extentが収まっても、途中のvector、glyph、image、annotation、clip等の検査に失敗する場合は拒否する。`collision_status`は計画の段階では`not_certified`であり、line幅やbaseline計算だけでは描画を許可しない。既存のink certificateは、従来どおり各revisionの文字だけの移動条件を満たす場合に使う。

`edit_flow_batch`はcallerが渡した計画を描画許可として受け取らず、正しいPDF・model・font recipeから再計算する。各identityへの編集は一回で、offsetはすべてbatch開始時の論理Unicode列に対するもの。先行操作で物理glyph IDが変わっても、既存の一意なglyph/code witnessと空slotのbyte mutation mapでbindingを更新する。他の要素の文字列を勝手に編集しないため、未実行のUnicode offsetは変わらない。

各操作後に全identityのbaseline・最終baselineを事前のprefix計画と比較する。最後に全変更のUnicode列と各行のstart/end/baseline/width/ascent/descentが最終計測と一致すること、元のcontainerとfollowsが保持されること、全bindingが復元可能なことを確認する。

中間PDFとsidecarは一時ディレクトリに置き、検証終了後に最終PDF・document sidecarを公開する。途中の拒否、計画とwriterの不一致、公開途中の例外では自分が作った公開ファイルを除去する。最終sidecarの`previous_model_sha256`はbatch入力modelを指す。各中間操作の証跡は返却reportに残る。OSクラッシュに対する2ファイル同時の原子的保存は保証しない。

## Paintの扱いとresizeの未実装境界

既存の所有paintは、そのownerの先頭baselineの変位だけrigid moveする。背景の幅・高さ・曲率・線幅を変更しない。共有または所有者未指定のpaintは固定され、明示した固定背景relationから所有権を導出しない。所有paintを動かす既存の未選択文字包含guardも残る。

FCCの黄色い背景・外枠・フランス語下線は今回すべて固定で、A/Bどちらにも所有させていない。背景を伸ばして枠を置き去りにする処理もない。合成テストでは、B所有の背景のrigid moveを含むbatchと、所有を外した固定背景がAの成長領域を塞いで拒否される場合を確認する。

今後の可変コンテナには、少なくともcontainer自身のpaint owner、未編集の子要素、paint role、source geometry、固定辺・可動辺、各辺と内容のanchor、明示した余白・利用可能領域・overflow方針を別々に確認する契約が必要になる。現時点でこれらの不明値をbboxから埋めない。とくに曲線を含む複合枠は、corner/edge部分の対応と塗り規則を保つ再生成仕様を要求する。`resize`等の未実装policyをbatch入力へ渡すと拒否する。

## 外部PDF評価

再現コード・集計は[`evaluations/flow_transaction`](../evaluations/flow_transaction/README.md)。前段階のFCC原本と原本A/B no-op証跡、実PDFから生成した検証済みg1を使用する。g1はAが4行、Bが1行の状態。初期原本からの履歴と今回の実行はhashで区別し、原本の再編集を新たに一から実行したとは報告しない。

評価する流れは、A1行/B3行 → A4行/B1行 → A/B全文削除 → A1行/B3行再入力 → 両paragraphのno-op。単独B長文化、単独A長文化、最終領域不足、関係の未指定、resize指定、外部保存も対照にする。

各batchでMuPDFとPoppler 144dpiの領域外画素、独立pypdfの全ページUnicode列、CID/GID/`/W`、固定nontext paint、画像を比較する。A/Bが空の場合にも、最初に独立抽出で確認した一意なA/B連続範囲の前後全文と、現在の両paragraphの全文が完全に一致することを要求する。単に旧文字列が見つからないという検査にはしない。正規化は既存評価と同じ空白除去であり、空白量・geometryの確認は別のglyph/paint検査が担う。

原本は1ページなので、今回の外部PDFに対象外ページの実測はない。対象外ページ保持は2ページの合成テストと既存のWord実PDF評価で確認する。API内のrenderer検査はMuPDFであり、Popplerと独立抽出は評価スクリプトの追加検査である。

実測は**5 batch成功／6対照を安全に拒否**。10件のparagraph編集要求を、13個の検証付き下位操作（編集またはrigid move）として処理した。これは13個のPDF operatorという意味ではない。

| 段階 | 最終状態 | 実行順 | 結果 |
|---|---|---|---|
| b1 | A 1行、B 3行 | A短文化 → B上移動 → B長文化 | 成功 |
| b2 | A 4行、B 1行 | B短文化 → B下移動 → A長文化 | 成功 |
| b3 | A/Bとも空 | A削除 → B上移動 → B削除 | 成功 |
| b4 | A/B再入力、A 1行、B 3行 | A再入力 → B再入力 | 成功 |
| b5 | 両paragraphのno-op | B → A | MuPDF・Popplerとも全画素一致 |

全段階でPoppler 144dpiの領域外差分は0 pixel、MuPDFの領域外画素も一致し、固定nontext paintと画像、独立した全ページ文字列の照合が通った。今回の長文例は明示改行を含む。固定幅の既存shaperを使用しており、新しい改行アルゴリズムの検証とは区別する。b1とb3のページ画像を目視し、変更後文章と空状態の双方で共有背景・外枠・フランス語と下線・周辺の図面・表・画像が保持されることを確認した。

b1ではAの高さが35.521198pt減り、Bは115.319702ptのbaselineから3行へ増えて最終baselineが144.995221ptになる。Bだけを先に長くすると現在の150.840900ptから組版するため、指定bottom 155ptを超えて拒否される。b2ではAが35.521198pt増え、Bが29.675518pt減る。Bを短くしてから移動すれば、最終baselineは150.840900ptで同じ枠内に収まる。単独A成長の拒否理由は固定vectorとの衝突であり、そのguardはbatchでも残した。

| 対照 | 拒否理由 | 公開PDF/sidecar |
|---|---|---|
| Bだけ先に長文化 | 現在位置ではvertical space不足 | なし |
| Aだけ先に長文化 | まだ長いBの移動が固定vectorへ衝突 | なし |
| A/B両方を長文化 | 最終配置でもvertical space不足 | なし |
| followsなし | Aが未選択のBへ衝突 | なし |
| resize policy指定 | 未実装の入力契約 | なし |
| 外部保存後に旧modelを使用 | PDF revision不一致 | なし |

最終coreでの回帰は **432 passed / 2 skipped（377.92秒）**。skipは前段階と同じ、任意AES providerがない環境でのAES-128/AES-256保存テスト。新規15テストには順序を逆にした変更、空状態・再入力・no-op、3要素chainと未変更Cの描画不変、保持文字を含む部分編集、所有paintと固定paint、関係なし、文字・vector・clip・annotation衝突、入力revision/font変更、途中失敗、最終行計測の不一致、公開失敗のrollbackを含む。全外部評価を実行した後にcoreを変えず、この回帰を一回実施した。

## 次の構造課題

今回の結果が示すのは、最終計画を上位に置き、全中間操作が安全な順序を選べる範囲では、既存の逐次writerが引き続き使用できるということ。backendや言語の置換を必要とする証拠はこの変更では得られていない。

次に残るのは、確認された可変containerとその共有paintのサイズ方針、および最終配置が安全でも今回の順序で全中間検査を通せない例の切り分けである。後者でcompound mutationを導入する場合も、個々の削除・追加paintと最終graphics stateの根拠を計画段階で必要とする。全画素比較だけで許可する設計へは進めない。
