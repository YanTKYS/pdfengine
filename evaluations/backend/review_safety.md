# Operator replay の独立安全性レビュー

2026-09-06。対象は `pdfeditor/content_stream.py` と `pdfeditor/replay.py` の Stage 1。自動 Paragraph / TextBox 推定の精度とは別に確認した。

## 確認できた構造

- 変更位置は glyph bbox ではなく、解析した `Tj` / `TJ` / `'` / `"` の byte range。再描画は元の font resource と文字コードを使い、元の演算子位置で行う。フォント再埋め込みや `/W` 再計算を行わない。
- 削除する文字は、元の `/Widths` または CID `/W` / `/DW`、`Tc`、`Tw`、`Tz` による変位に相当する数値 `TJ` operand に置換する。これにより同一 text object 内の後続文字の位置を保持する。
- ページ `/Contents` 配列を一つのプログラムとして読む。配列の途中で `q` / `Q` や `TJ` の配列 operand が分割されても、各 member を独立解釈しない。
- 新しい content stream を作り、対象ページの `/Contents` だけを付け替える。別ページが元 stream を共有していても、そのページの stream は変更しない。
- `q` / `Q` の CTM、clip、text state を追跡する。再出力はその場で同じ描画命令の文字列を置き換えるため、対象外の path、clip、graphics state の原バイト列をそのまま維持する。
- 文字と演算子の対応には Unicode・origin・paint type の一意一致を要求する。同位置の fill と stroke が別演算子なら区別できる。一つの `Tr=2` 演算子の片 paint だけを選んだ場合は拒否する。同位置・同文字・同 paint の重複は曖昧として拒否する。
- 出力前には削除後の非選択 glyph、復元後の全 glyph、144 dpi の対象頁画素を照合する。保存後も全頁の画素、対象頁の glyph / font resource 一覧、ページ数、暗号化方式と権限を再確認する。

## 回帰テスト

実行コマンド：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_content_replay.py -q
```

結果：**12 passed**。

| テスト | 確認した結果 |
|---|---|
| literal / hex string、escape、comment | 文字列中の `%` や `TJ` を演算子と誤認しない。operator の byte range が正しい。 |
| inline image、末尾の余剰 operand | 不完全な解析を拒否する。 |
| `TJ` 内の 1 字削除 | 残る文字列は `ACDEF`。削除文字の後ろにある `CDEF` の origin を維持する。 |
| `/Contents` 配列の途中で `TJ` operand を分割 | 一つのプログラムとして認識し、部分削除と復元を完了する。 |
| `q` / `Q` の clip + CTM | 先の clip と 2 倍 CTM が後の文字へ漏れない。 |
| 2 ページで stream 共有 | 第 1 頁の `B` のみ削除し、第 2 頁の `ABC` と画素は保持する。 |
| 同位置の別 fill / stroke 演算子 | fill のみ削除し、stroke の文字と paint を保持する。 |
| 一つの `Tr=2` 演算子 | 片 paint は出力せず拒否。両 paint 選択なら削除・復元できる。 |
| 同位置の同 mode 重複 | 一意対応できず、出力せず拒否する。 |
| quote 演算子 + `Tc` / `Tw` / `Tz` | 空白の部分削除後にも後続行・後続文字の位置を維持する。 |
| 京都の既存 PDF | 148 glyph の 4 行質問を元 CID font のまま復元し、全頁 144 dpi の画素が一致する。 |
| 沖縄の既存 PDF | 本文冒頭 50 glyph の 2 行のみを復元し、日付を含めず、元 CID font のまま全頁画素が一致する。 |

合成 fixture は parser / deletion / sharing の回帰検証用であり、実 PDF の対応率の母数には含めない。実 PDF 2 件の試験は、前フェーズで元 font 再利用時に TextWriter の出力幅が崩れた資料をそのまま使用した。

## 限界と残る確認

- この Stage 1 は、元の文字コードを明示 operand へ再生成する能力の検証である。任意 Unicode から別文字を encode する能力、新しい CID font resource を作る能力、font 代替の品質を実証するものではない。
- 現在の provenance は renderer の paint event と原 operator の直接 ID 対応ではなく、Unicode / origin / paint type の照合。似た重複がある場合に過剰拒否することは許容し、曖昧さを推測で解消しない。
- Form XObject 内の対象、inline image を含む解析対象 stream、text clipping mode、multi-character ligature、未対応 encoding 等は拒否される。Form invocation ごとの cloning と安全な operator provenance を実装するまで、guard を緩めない。
- 画素完全一致の保存 guard は MuPDF 144 dpi である。Poppler と高解像度差分、clip 端での欠損、画像・背景・罫線の削除前後比較は実 PDF 評価 harness で別途確認する。
- 全 graphics state を別表現で再生成する汎用 writer は成立していない。今回は元プログラムの該当位置に戻すことで状態を保持する方式である。異なる位置への reflow や paint order 変更には、より明示的な状態再現が必要になる。

このレビューの範囲で、上記の guard を通って意図せず破損出力を発行する確定バグは見つからなかった。これは PDF 全般に対する安全性の証明ではなく、指定した境界と回帰ケースでの確認結果である。
