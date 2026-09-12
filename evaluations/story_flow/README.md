# 論理文章の跨領域flow評価

LibreOffice由来`MigrationLibreOffice-ja.pdf`の4–5ページをまたぐ一つの文を、明示した二つの固定領域で再配置する。原本・派生PDF/PNG・font・sidecar・生のglyph/paintログはgit対象外。公開集計に取得元URL、SHA-256、領域・fontの契約を記録する。

```powershell
.\.venv\Scripts\python.exe -m evaluations.story_flow.evaluate --run-name new-run
```

既存コーパス、Windows `msmincho.ttc` face 1、既存のPopplerと独立pypdf環境を使用する。出力先`runs/<run-name>/`は新しい名前が必要。各source範囲について原本operator replayを行い、元glyph、font resource、MuPDF/Popplerの全画素と独立Unicodeを比較してから編集へ進む。

独立監査だけを修正して1段階目から再開する場合に限り、`--resume-stage-1`を使用できる。初期model・source replay program・出力PDFのSHA・再計算したplanを照合し、既存の1段階目を再監査する。2段階目以降の既存出力は上書きしない。今回も評価器の単一ページ仮定を直した際にこの再開経路を使用した。

一つの文をcallerが一つのlogical paragraphとして確認する。原本paragraphの残りには別のfont/sizeがあり、対象外として保持する。完全な混合書式paragraphを取り込む候補も負例に含める。engineにPDF名や座標による分岐はない。

同程度置換、ページ境界をまたぐ一回のUnicode範囲編集、短文化、空状態、再入力、同文再編集を保存・再openする。各段階で全体logical Unicodeとstyle/identity/flow policy、各fragmentのUnicode range、独立した対象ページ全文、CID/GID/`W`、固定nontext paint、image/annotation、既存font resourcesを比較する。対象外8ページは全段階でMuPDF画素を比較し、Popplerは各段階で1ページ、最終段階で8ページすべてを比較する。

最大容量超過、座標由来flow、保護領域への継続、stale revision、混合書式原本の取込みを負例とする。最初に候補とした新潟県Word PDFは暗号化されており、平文sidecarに関する既存guardで拒否された。この探索結果と選択範囲の制限は[設計資料](../../docs/logical-story-flow.md)に記録する。

この外部評価は各1行の二領域であり、複数行container、同一ページの列、authored hard breakが境界に一致する場合、画像・vector・annotation・clipへの衝突は合成PDFのテストで補う。任意の業務PDFの成功率やページ全体reflowの成立を意味しない。

2026-09-13の結果は、原本replay 2/2、保存・再open 6/6、負例の安全な拒否5/5。最後の同文再組版はMuPDF/Poppler 144dpiで全10ページの全画素が一致した。前後の別書式文を固定したことで残る空きを含め、成功範囲と限界は[公開集計](summary.json)と[設計資料の保存結果](../../docs/logical-story-flow.md#実pdfの保存結果2026-09-13)に記録している。

最終関連回帰は139 passed / 0 skipped / 0 failed（244.80秒）。公開集計の`regression.command`に対象テストと実行コマンドを記録している。既存engineは変更せず、従来の全体回帰結果は以前の証拠として区別した。
