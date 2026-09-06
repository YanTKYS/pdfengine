# 埋め込み MS Mincho の描画幅不整合

対象は `word_kyoto_questions--noop` と `word_okinawa_procurement--noop` の変更前エンジン出力。どちらも glyph coverage、Unicode 再抽出、編集領域外の画像比較を通過したが、本文の文字が水平方向に重なる。京都は tracking=0、沖縄は tracking=0.060241pt であり、tracking 分岐だけに起因する問題ではない。

## 実測

| 項目 | 京都 | 沖縄 |
|---|---:|---:|
| 書き直した glyph 数 | 106 | 58 |
| Unicode 列の一致 | 完全一致 | 完全一致 |
| 計画 origin からの最大 x 誤差 | 195.367107pt | 226.901455pt |
| 最大 y 誤差 | 0.000015pt | 0.000031pt |
| 元 subset の post.isFixedPitch | 1 | 1 |

元フォントを `Font(fontbuffer=...)` でロードすると `mono=1, cjk=0` となる。漢字に対する `Font.text_length` は 1000/em、元 PDF の glyph advance も同等である。一方、TextWriter が新規埋め込みした CIDFontType2 の `/W` は **`[0 65535 500]`** である。これにより PDF の文字送りは 500/em となり、1000/em 幅の漢字の輪郭が重なる。京都の実測 origin 差は font size 10.560375pt の半分、5.28019pt であった。

空の PDF に同じフォント bytes を書くだけでも再現するため、元 PDF の paint state、redaction、保存時の既存ページ加工が原因ではない。

診断実験として、メモリ内の font program の `post.isFixedPitch` だけを 0 に変更すると、必要な文字幅を含む `/W` が生成される。30文字の scratch PDF で origin 最大誤差は以下のように変わった。

| font bytes | 京都 | 沖縄 |
|---|---:|---:|
| 元 bytes | 153.125484pt | 153.117005pt |
| post.isFixedPitch=0 の診断実験 | 0.000065pt | 0.000108pt |

この実験は原因切り分け用である。元 PDF／元フォントファイル／production engine は変更していない。日本語の等幅フォントには全角・半角の双方が含まれるため、元フォントにこのフラグがあること自体を PDF 不正と分類しない。

## 対応の範囲

現在の設計内の小修正として、TextWriter が実際に書いた Unicode 列・glyph 数・origin を layout 計画と照合し、不一致なら保存前に拒否できる。coverage/name/`is_writable` だけでは安全性を判定できない。`/W` の単位丸めによる微小誤差があるため、許容差は明記する必要がある。

この guard は破損の防止であり、元フォント再利用への対応そのものではない。正しく再利用するには backend の幅生成改善、または計測と書き出し幅が一致する writer が必要となる。元フォントの metadata を無条件に改変する方式は推奨しない。

再現スクリプト: `font_writer_probe.py`。全 glyph 実測・生成フォント辞書: `font_writer_probe.json`。`font_writer_*_original.pdf/.png` は不具合再現、`font_writer_*_clear_fixed_pitch.pdf/.png` は診断上の対照実験であり、正式な編集成果物ではない。京都の両 PNG を目視し、元 bytes の重なりと対照実験の正常な文字間隔を確認した。
