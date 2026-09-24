# 確認済み空き領域へのcontinuation評価

現時点は[途中チェックポイント](../../docs/continuation-checkpoint.md)。評価コードは追加済みだが、外部原本の編集系列は最終engineでまだ実行しておらず、成功集計も公開していない。既定のPoppler・pypdf・font pathはWindows検証環境を前提とする。

既存corpusのLibreOffice移行資料を使用する。対象は前段階と同じ4/5ページの混合書式paragraph、生成先は目視確認した6ページ上部左側の空き領域`[55,80,385,120]`。右上の図版は固定・保護する。領域・描画順序の判断は評価者の明示指定であり、engineによる意味推定ではない。

```powershell
.\.venv\Scripts\python.exe -m evaluations.continuation.evaluate --run-name final
```

原本は既存の`evaluations/realpdf/corpus/lo_migration_ja.pdf`を使う。SHA-256が異なる場合は停止する。本文providerは既存評価と同じ`msmincho.ttc`のface 1、Latinは`times.ttf`で、ファイルは再配布しない。

系列はsource no-op replay、overflow、再open、second edit、shorten、regrow、最終no-op。MuPDF/Poppler 144dpi、独立pypdf全ページUnicode、既存font/paint/image/annotation、CID/GID/`W`、slot identityとallocationを照合する。最終no-opは両rendererで全10ページを比較する。容量不足は最終PDF/sidecarを公開せず拒否する。

PDF・PNG・本文・glyphログはignored `runs/`だけへ保存する。公開集計には原本URL/hashと実行コードhash、検査結果のみを含める。これは単一外部原本での境界評価であり、一般PDFの成功率ではない。
