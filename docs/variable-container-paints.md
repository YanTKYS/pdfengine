# 可変コンテナと共有paint

## 仮説の分離と判断

前段階の[final layout planning](planned-document-transactions.md)により、FCC注記のA/Bを長短入れ替えする変更は、安全な順序で実行できた。一方、両方の内容が増えて固定bottom 155ptを超える変更は残っていた。これを今回の主要な障害とした。全業務PDFについての統計的な順位ではない。

仮説Bの「全ての逐次順序が失敗するが最終配置は安全」という実例は、この証拠からは成立していない。順序の問題を解いた前段階を繰り返すことや、guardを外した一般compound writerの導入は選ばなかった。ページ境界も今回の注記枠の制約を解く理由にはならない。

仮説Aでは、背景が矩形でも外枠が複合pathであることを前段階で確認した。今回は外枠の**伸縮する部分の構造**を調べ、4つのcubic cornerを含む2つのsubpathが、指定帯域を垂直な直線だけで横切ることを確認した。これに対応する一般的な形状契約を作った。PDF名、Producer、固定座標に応じたbackend分岐はない。

## 意味、layout、geometryの契約

`pdfengine-variable-container-1`は、既存の`pdfengine-document-1`をそのrevisionの固定領域として内部に保持する。外側に次の情報を持ち、保存後にも再検証する。

| 層 | 情報と意味 |
|---|---|
| semantic owner | container identity、明示したchildren。共有paintはcontainerに属し、一つのparagraphに所有させない |
| fixed children | 内容・x・先頭baselineを維持する子要素。今回のFrench見出しH |
| layout policy | 上・左右辺固定、下辺可動、初期最小bottom、最大bottom、最後のbaselineから下辺への明示gap、overflow拒否 |
| available region | paint全体に対してcallerが確認した最大領域。本文の使用可能領域とは別 |
| paint role/owner | backgroundsまたはborders、container ID、revision-bound source ID |
| paint geometry | `vertical-straight-band-v1`、明示した帯域、証明済みsource pathとpaint event |
| paint anchor | `preserve-offset-to-container-bottom`を明示選択。上・左右辺と、選択時のpaint下端／container下端のoffsetを保持 |

下辺は `max(min_bottom, max(child.last_baseline) + baseline_bottom_gap)` とする。glyphが0でもparagraphの先頭baselineを確保する既存の意味を維持する。左右・上のpaddingも、ink端からのpaddingもunknownであり、baseline gapをpaddingとして報告しない。測定した文字extentがこの領域に収まらない場合は拒否する。

paintの下端offsetは確認されたanchorの初期値を観測して保存する。観測値からresize方向、可動範囲、帯域、gap、ownershipを選ぶことはない。次のようにrole、geometry model、anchorを個別に渡す。

```python
{
    "source_id": reviewed_path_id,
    "role": "borders",
    "geometry_model": "vertical-straight-band-v1",
    "band": [confirmed_band_top, confirmed_band_bottom],
    "bottom_anchor": "preserve-offset-to-container-bottom",
}
```

## 直線帯域の証明

帯域を `[u,v]`、下辺変位を `d` とする。yの変換は上部で恒等、下部で平行移動、帯域内で直線補間する。

```
y <= u : y
u < y < v : u + (y-u) * (v-u+d)/(v-u)
y >= v : y+d
```

`v-u+d`が正であることを要求する。さらに、元pathの頂点とcurveの制御点は帯域外にあり、帯域を横切る接続は垂直な直線だけでなければならない。曲線の制御点の凸包が上下どちらかに収まることを確認する。開いたsubpath、斜め接続、帯域内の頂点、帯域を横切るcurve、帯域の潰れは拒否する。

この条件では、上下のcapは剛体のまま、垂直接続の長さだけが変わる。変換は連続で向きを保つため、穴やnonzero/even-oddの塗り規則も保てる。単純矩形に限らず、条件を満たす複合fillへ適用できる。一方、rounded rectangle、decorative border、cubic path一般へ適用できるという意味ではない。gradient、画像背景、stroke、透明paintは現在のresize対象に含めない。

sourceの数値・描画順・graphics stateを保持し、下部の座標だけを元CTMの座標系で平行移動する。上部の数値は再丸めしない。元paint operatorを`n`で消費し、同じ状態で変更後の構築命令と元の`f`/`f*`を出力する。面積ベースredactionを使わない。source pathからpaintへの一意な対応は既存のcounterfactual proofを要求する。

対応範囲は、構築途中にstate変更がなく、pending clipや特殊marked contentを持たないrootの不透明solid fill。fillのcolor、opacity、clip、group、layer、CTM、塗り規則を検証する。既知のactive clipに収まることも必要で、clipを解除しない。

## 衝突とsource mutation

変更の影響は下部capと垂直辺の延長部分を囲む帯に限定できる。1ptの確認余裕を持たせ、その帯が既存glyph、固定vector、image、link、annotation、widgetに交差しないことを確認する。文字が残っている帯を縮めることは許可しない。所有paint同士以外を、所有者やbboxの推測で除外しない。

各paintにsource provenanceと変換後geometryを与え、全paint eventの計画を作る。保存後に全glyph、font resources、全paintとscope、ページ数・権限、帯域外の画素、対象外ページを比較する。さらに変位0の再構築を事前に検証する。画素一致だけが許可根拠ではない。

一つのframeを構成する背景と外枠は、同じresize保存で更新する。それぞれのsource operatorとpaint効果を個別に証明する。複数paragraphに対する一般compound mutationやguardを省略する仕組みを導入したものではない。

既存のglyph geometry certificateは、後続paragraphを移動する従来の場所で使う。container ownershipやresize意味の根拠にはしない。

## 計画、逐次実行、再保存

1. 明示した最大領域で既存shaperにより全変更後の配置を計算する。
2. 論理baselineと確認gapからcontainer下辺を求め、文字extentと各paintの形状・最大領域を確認する。
3. 伸長ではpaintを先に伸ばしてから文字を編集する。縮小では文字を編集してからpaintを縮める。
4. 各操作後に全要素のglyph/code witness、空slot、paint source IDを検証してbindingを更新する。
5. 最終relation、固定child、edge/anchor policy、下辺と実paintが一致した場合にだけPDFとsidecarを公開する。

既存の逐次writerとbatch planを利用する。途中に衝突がある経路は拒否する。frameの使用可能領域を書換えるのは外側で確認したresize policyに基づく処理であり、観測bboxを利用可能幅・高さへ置き換える処理ではない。失敗時は最終PDF/sidecarを公開しない。2ファイルのOSクラッシュに対する原子的保存は未保証。

## 同文再編集で見つかったmetricsの問題

初期実装では行のdescentから下辺を求めたため、文字の見た目が同じでも、生成時と保存後の観測値の違いで枠が約0.75pt縮んだ。PyMuPDF 1.27.2.3のローカル実装`jm_trace_text_span`では、trace bboxに使う縦metricsを`asc * fsize / (asc - dsc)`等で正規化している。したがってfont programの`hhea`値をそのままfont sizeへ換算した高さと同じではない。

containerのanchorを安定した論理baselineと明示gapへ変更した。さらに、retained glyphの行組みmetricsには埋込font programの`hhea`値も含め、元の保守的なtrace boundsから小さくしない修正をした。これにより、新しい文字が保存後にretainedへ変わっただけで行間が縮むことを防ぐ。実glyphのadvance、GID、元コードを別fontの値で置き換えてはいない。未知fontの元trace fallbackも維持する。

元PDF一般の同文描画gateは引き続きoperator replayで行う。異なるlayout policyを与えた任意の原本reflowをno-opと扱うことはない。

## 外部PDFの検証範囲

再現コードと集計は[`evaluations/variable_container`](../evaluations/variable_container/README.md)。FCCの原本、A/Bのsource replay証跡、確認済み幅・元gapを引き継ぐ。French本文Hを固定childとして加え、共有paintのownerをcontainerにする。

今回のcaller-confirmed入力は、content bottom 155–220pt、paint領域 `[563,64,775,222]`、baselineから下辺へのgap 4pt、paint帯域100–150pt。この値は評価者が原本に対して確認した入力であり、engine内のPDF固有条件ではない。最小値を元の枠寸法に保つため、全文削除時もFrench本文と元の注記枠を残す。

原本のpaint no-opをMuPDF・Poppler・独立Unicode extractionで確認する。長文化と枠伸長、短文化と枠縮小、A/B双方の空状態、双方再入力、生成後の同文再編集を保存・再openする。各段階で独立した全ページ文字列、CID/GID/幅、固定French child、予定した2つのpaint以外の全nontext paint、画像、領域外画素を照合する。

2026-09-12の最終実行では次の5段階が成功した。各行は直前の保存PDFと復元したsidecarから実行する。

| 段階 | 内容 | 下辺の変位 | content bottom | 結果 |
|---|---|---:|---:|---|
| v1 | Aを6行へ長文化、Bを追従移動 | +23.521699pt | 178.521699pt | 共有paint伸長→文字編集 |
| v2 | Aを1行へ短文化、Bを戻す | −23.521699pt | 155pt | 文字編集→共有paint縮小 |
| v3 | A/B双方を空にする | 0pt | 155pt | 固定Hと元の最小枠を保持 |
| v4 | 空のAへ6行、Bへ新しい文章を再入力 | +23.521699pt | 178.521699pt | 空slotから復元して伸長 |
| v5 | 生成後のA/Bを同文で再編集 | 0pt | 178.521699pt | 行間・枠寸法とも維持 |

全5段階でPoppler 144dpiの領域外差分は0画素、MuPDFの領域外画素も一致した。v5は両レンダラーで全画素が一致した。独立pypdf抽出による全ページUnicode照合は、空白を正規化した文字列で行う。fontのCID/GID/`W`、予定した共有背景・外枠以外のpaint、画像、Hの内容とanchorも全段階で一致した。伸長・空状態・再入力後のページ画像を目視し、French本文、外枠の角、下部の図表・画像が保持されることも確認した。

固定containerのままで同じ長文を入れる操作、最大領域超過、固定Hの編集、共有paintをAに所有させる操作、曲線を横切る帯域指定の5負例は、すべてPDFとsidecarを作らず拒否した。以下の探索時拒否はこの5負例と別に記録する。原本は1ページなので、外部PDFの対象外ページ保持の実測例を増やしたとはしない。複数ページ保持、image/annotation/clip、曲線cap・穴、失敗後の未公開、生成後の行間保持は合成PDFの回帰テストで補う。

最終coreと評価runnerのSHA-256一致を確認し、外部評価後にcoreを変更せず全体回帰を1回実行した。結果は **453 passed / 2 skipped（476.09秒）**。新規21テストを含む。skipは既存と同じ、任意の暗号providerがない環境でのAES-128/AES-256保存テスト。修正中の影響範囲テストは60 passedであり、初期の同文再編集による枠縮小失敗を修正した後の結果である。

探索時、末尾文 `Use the approved label.` のdescenderがBのfont bboxと重なり、文字writerは安全に拒否した。原本のbaseline gap 9.837677ptは変更していない。新しいinkの下端166.790247ptと、Bのfont bbox上端166.251465ptが交差する例である。これは実輪郭の交差を確定した結果ではなく、既存のcomposed-text guardの保守的な境界。これを緩めず拒否を記録し、主評価の末尾文を同じ行数の `Confirm the final document.` とした。伸縮の成功を任意の文章がこの狭いgapで入る保証とはしない。

## 次の構造課題

今回扱うのは、明示された一つの可変containerと、形状証明が成立する共有frameである。次に必要なのは、伸びたcontainerに別のcontainerが追従する契約と、伸縮帯域を持たない装飾や共有paintの幾何モデルの切り分けである。未知の形状を一律scaleしない。

既存の文字衝突検査やsource font metricsにも保守的な拒否が残る。最終配置が安全でもどの逐次順序も成立しない実例が確認できた場合は、その証拠に基づいてcompound mutationを別に判断する。

後続の[論理文章と物理領域列](logical-story-flow.md)では、最大領域に収まらない内容の継続先を扱うため、一本のUnicode列と固定container列を分離した。可変枠の伸長を必須の先行処理にはせず、既存の固定領域間flowを独立して検証している。
