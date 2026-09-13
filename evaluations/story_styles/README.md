# 複数書式storyの実PDF評価

前回のLibreOffice由来`MigrationLibreOffice-ja.pdf`で、4ページ末尾から5ページ冒頭に続く165文字の元paragraph全体を選択する。MS-PMincho 10.5ptとTimes New Roman 12ptの二つのlogical styleを、明示した既存領域列で再配置する。

```powershell
.\.venv\Scripts\python.exe -m evaluations.story_styles.evaluate --run-name new-run
```

既存のコーパス、Windows `msmincho.ttc` face 1と`times.ttf`、Poppler、独立pypdf環境を使う。元PDF・font・派生PDF/PNG・sidecar・抽出ログはgit対象外。出力先`runs/<run-name>`は新しい名前が必要。

4ページのsource範囲は前回と同一で、当時のsnapshot・replay program・関連コードhashを照合して既存の独立renderer証跡を再利用する。そのため、前回のローカル`evaluations/story_flow/runs/final-lo`証跡が必要。5ページは範囲を広げており、新しいsource operator replayを両rendererと独立Unicode抽出で検証する。

評価順序は、混合書式の置換、styleとpage境界をまたぐ一回の範囲編集、短文化、空状態、複数styleでの再入力、同文再組版。Latin styleが物理page境界をまたぐことを明示的に確認し、global style spanをpage境界で分割しない。source fontの保持とproviderによる再生成を別に数え、storyの全描画glyphが対応するstyleの明示providerから来ることを照合する。

各保存でlogical Unicode・paragraph ID・style registry/provenance・style spans・typing style・fragment ranges・destination context・CID/GID/`W`・元resourceを検証する。固定の文字・paint・画像・注釈・page/container policyも比較する。対象外8ページは毎回MuPDF全画素と独立抽出が一致することを確認し、Popplerは途中の各段階で対象2ページと対象外1ページ、最後に全10ページを比較する。

対象ページの独立Unicode比較はページ全文を対象とし、空白を正規化する。authored Unicodeの厳密な保存は論理modelとsource glyph bindingで別に検証する。曖昧なstyle境界編集、容量超過、誤ったdestination style binding、font provider変更を負例とする。

この評価は一つの原本・確認済みの一段落であり、任意の業務PDFの成功率ではない。装飾rangeの跨領域再生成、複数paragraphの容量共有、新規page生成は含めない。設計と限界は[論理書式と配置先binding](../../docs/attributed-story-flow.md)へ記録する。

2026-09-14の`final`実行は**6保存成功・5負例の安全拒否**。最終の同文再組版は、再入力済みPDFに対してMuPDFとPoppler 144dpiで全10ページの画素が一致した。元PDFのoperator replayとは別の基準であり、providerによる再組版が元font programと同一であると主張するものではない。[公開集計](summary.json)に各保存のhash、style spans、物理range、providerと元fontの証跡、拒否理由を記録する。

同じengineコードに対する全回帰suiteは**504 passed / 2 skipped（775.57秒）**。skipはpypdf用AES provider未導入による既存2件である。新しい24件にはsource code保持との混在、異なるCTM・size・fill・横倍率、style identity、空状態からのtyping、従来のpaint guardを含む。
