# Spacing と inline style の分離（PR A）

2026-09-20。最新 main `532dc1b` の stable identity / single transaction と spacing 分類を引き継ぎ、[review3](../../docs/fable5.1-review3.md) の PR A を検証した。公開集計は [summary.json](summary.json)。原本・保存 PDF・画像・生の抽出結果は公開しない。

## 何を改善したか

行ごとの `Tw` を書式の違いと解釈してしまう構造を除いた。合成した3行の `Tw` 配分段落が一つの inline style になり、style 上書きを指定せず全体置換→保存→再編集→同文保存を通過する。`TJ` 配分も一つの style のまま観測する。これは両端揃えを再現したという結果ではない。新しい配置は従来どおり左揃えである。

非ゼロ `Tc` の未確認候補や行間で異なる `Tc` は、論理 tracking を `None` とし、新 glyph を挿入しない。保持文字の advance はそれぞれの source event から取得する。行内で `Tc` が変わる場合は、異なる style として残す。

## 実 PDF

既存 corpus の SHA-256 と手動確認済み範囲を使用した。自動 Paragraph 推定の精度はこの評価の対象外。

| 原本 / 範囲 | 観測 | 今回の結果 |
|---|---|---|
| `word_osaka_fire_notice` p1 / glyph 143–217 / 明示幅480 pt | MS Gothic、同一行に `Tc=0` と `-0.12`、2 style、段落 tracking / alignment は unknown | source replay は両レンダラーで画素一致。非ゼロ候補への新漢字入力は安全に拒否。2 style を一つへ誤統合しない |
| `word_okinawa_procurement` p2 / glyph 912–940 / 明示幅468 pt | 日本語本文と番号後の Arial 空白、計2 style、両方 `Tc=0`、alignment は unknown | source replay → 同文保存(v0) → 本文短文化(v1) → 再編集(v2) → 同文保存(v3) が成功 |

沖縄は番号と別 font の空白を保持し、論理 offset 4 以降の日本語本文を置換した。v1 は「内容を確認してください。」、v2 は「内容」から「書類」への変更。新 glyph の font は MS Mincho TTC face 0 を明示指定し、元 subset に新漢字を追加したとは扱わない。font ファイルの SHA-256 は集計に残した。

最初の評価スクリプトは沖縄の番号後の Arial 空白まで全体置換し、既存の「複数 style をまたぐ編集には明示 style 指定が必要」という guard に拒否された。評価範囲を本文だけに修正した。エンジンの guard は変更していない。

### 独立監査

- source replay および v0 / v3 の同文保存: 対象ページを MuPDF と Poppler 144 dpi で描画し、全画素一致。
- v1 / v2: Poppler の変更画素はそれぞれ3887 / 564。許可領域+1 pt外はどちらも0画素、別ページの MuPDF 画像も一致。
- 各保存で独立 pypdf 抽出を比較し、対象ページは空白正規化後に指定した一か所の置換だけ、別ページは完全一致。編集後は元の選択文章が抽出結果に残らない。logical Unicode は sidecar 復元時に完全一致。
- drawings・image・annotation・既存 font resource は各保存前後で一致。新 font の CID / GID / `/W`、保持文字の元 resource / code を監査。identity は mutation map を使用。
- 原本と v2 の Poppler 対象ページ全体を目視確認し、対象の短い文章、隣の箇条書きと表の配置に破損がないことを確認した。

この2原本では、合成 `Tw` fixture と同じ偽の style 分裂を解消した実例は得られていない。実 PDF について証明したのは、既存のゼロ tracking の往復編集を壊さないことと、実際の行内差を誤統合しないことである。実 PDF 全般の両端揃え成功率には換算しない。

## 再現

通常の project 依存に加えて、[既存評価](../../docs/realpdf-evaluation.md)と同じ Poppler と独立 pypdf 環境、`evaluations/sources.json` の2原本、`C:/Windows/Fonts/msmincho.ttc` が必要。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_spacing.py tests/test_attributed.py tests/test_destination_style.py -q
.venv\Scripts\python.exe -u -m evaluations.spacing.evaluate --run-name <新しい名前>
```

`runs/<名前>/summary.json` に source / engine / runner / font の SHA-256 と結果を保存する。既存 run は上書きしない。engine は実行前後の hash 一致で固定する。

## 回帰検証

変更前の最新 main は全 suite で **551 passed / 2 skipped**。変更後は全38 test module を重複のない4群に分け、root の直列 pytest で **561 passed / 2 skipped / 0 failures / 0 errors**。元の553 test IDを全て保持し、spacing の10ケースを追加した。2 skip は AES-128 / AES-256 の暗号化 provider 未導入によるもので、基準実行と同じである。集計には各群のmodule一覧とJUnit SHA-256を保存した。

分割実行の一時スクリプトは4群目の開始前に進捗ラベルの添字ミスで停止した。engine hash と完了した3群の結果を確認し、未実行の4群目だけを再開した。エンジンのテスト失敗ではない。検証済みの群を再実行していない。

## 残る障壁

非ゼロ／不明 tracking の非空段落は永続保存時に拒否を維持する。現在の writer が `0 Tc / 0 Ts` に正規化するため、論理の tracking / rise と再観測が一致しない。次は確認された tracking / rise の PDF witness と再編集契約が必要。alignment の確認・両端配分、continuation slot の追加は今回扱っていない。旧 snapshot / sidecar は新しい spacing 契約で再確認を要する。
