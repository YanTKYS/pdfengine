# subset fontで置換を拒否した理由

Stage 2の7拒否のうち、4件は元font resourceで要求Unicodeを符号化できず、3件は既存slotのadvanceと一致しなかった。この文書は前者4件の読み取り診断であり、新しいfont writerの成功結果ではない。

## 実体を確認した結果

対象は既存の手動選択範囲に結び付くresourceに限定した。評価原本は[取得元とSHA-256](../evaluations/sources.json)で特定できる。PyMuPDF 1.27.2.3とfontTools 4.64.0を使用した。

| 原本ID・要求文字 | ページ / resource / xref | 確認結果 | 現時点の分類 |
|---|---|---|---|
| `word_takeo_notice`・議 | 2 / `/F1` / 80 | ToUnicode・埋込Unicode cmapに要求文字がなく、fallbackを無効にした`has_glyph`も0。非空glyph895個中774個はToUnicode未対応 | 符号化不能。輪郭自体の有無は判定保留 |
| `lo_migration_ja`・施、設 | 1 / `/F1` / 64 | Unicode cmapなし。非Unicode cmap→PDF code→ToUnicodeを照合し、`.notdef`以外の非空254glyphすべてが対応済みで要求文字なし | 確認できるsubsetの利用可能集合に不足 |
| `print_canvia`・A、m | 1 / `/F5` / 45 | Unicode cmapと`has_glyph`はGID36/80を返すが、両方の`loca`長0、`glyf`輪郭0、非composite | GIDはあるが描く輪郭が欠落 |
| `word_niigata_hearing`・約 | 1 / `/TT1` / 19 | cmapなし、CIDToGIDMapはIdentity。`.notdef`以外の非空257glyphすべてToUnicode対応済みで要求文字なし | 確認できるsubsetの利用可能集合に不足 |

この診断は既存マッピングが宣言する文字対応を検査したもので、未対応の輪郭から文字の意味を認識したものではない。武雄の未対応glyphを自動割当する根拠はない。CanviaのようにGIDとUnicode対応があっても空輪郭の場合があるため、フォント名・cmap・GIDのどれか一つだけでcoverageを証明しない。

`TTFont(BytesIO(font_program))`で`cmap`・`glyf`・`loca`を読み、PDF側のToUnicode・Encoding・CIDToGIDMapとの対応を調べた。`pymupdf.Font(fontbuffer=font_program).has_glyph(codepoint, fallback=False)`も併用した。GID0は`.notdef`であり、そこに輪郭があっても要求文字の字形とは扱わない。空白文字の空輪郭は正常な場合があるが、今回のCanviaの要求は可視文字A/mである。この`glyf`検査をCFFやType3へそのまま一般化しない。

抽出したfont programのSHA-256は次のとおり。font program自体は再配布していない。

| 原本ID | SHA-256 |
|---|---|
| `word_takeo_notice` | `e668eb4bfd5faa2bd269537e2fe7905f618eee31063cfd416ffb487157f619c3` |
| `lo_migration_ja` | `8e494f18aeceaf0433fe6bc4627fe545efe44dc006c7c044bee6411486651ada` |
| `print_canvia` | `55fa1a0a98e3ccd2fa008282279e8b542b7372ba161844cbdc1817ba5122afe0` |
| `word_niigata_hearing` | `b738d55046f82edd1c8d4791061c5d3e3851ddb06dba718f06bb9b46d4271c2e` |

## 次のbackend評価で分けること

1. 元resourceにcode・glyph・輪郭・幅が揃う文字は、現在のresource再利用経路を維持する。
2. 輪郭とUnicodeの対応を確実に証明でき、PDF codeだけが不足する場合に限り、code/CID/ToUnicodeの新しい割当を比較対象にできる。今回の武雄ではまだその証明がない。
3. 必要輪郭が不足する場合は、利用条件とcoverageを確認した完全なfont programを別途用意し、別resourceとして埋め込むwriterを評価する。同じfont名だけで同一書体・同一版とは扱わない。別書体を使う場合はfont代替として分類する。

新しいwriterでは、Unicode→PDF code→CID→GID→実輪郭と、font metrics→PDF `/W`→実際のglyph originを一連で検証する。まず元文章を新経路で同じ位置へ描くStage 1を通し、その範囲だけStage 2へ進む。MuPDFとPopplerの表示、pypdfの抽出、旧文字除去、未編集要素も比較する。

ライブラリや言語の変更だけで不足輪郭が補われることはない。現在のcontent stream編集・保存方式を維持しつつ、必要なfont resourceを整合して生成する部分を独立して比較することが次の課題になる。幅不明時の長文化拒否と、観測幅・推定幅・明示幅の分離は維持する。
