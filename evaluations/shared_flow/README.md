# 独立した二段落の共有flow評価

既存コーパスのLibreOffice由来`MigrationLibreOffice-ja.pdf`を使用する。前段階の混合段落165文字と、その後続段落140文字に別のparagraph IDを与え、確認済み4/5ページの領域容量を共有する。

```powershell
.\.venv\Scripts\python.exe -m evaluations.shared_flow.evaluate --run-name new-run
```

Windows `msmincho.ttc` face 1、`times.ttf`、既存のPopplerと独立pypdf環境を使う。前段階の`evaluations/story_styles/runs/final/initial.json`、同ディレクトリの`B-source-replay.pdf`、`evaluations/story_flow/runs/final-lo/A-source-replay.pdf`が必要。同一のsource modelとoperator replayをhash照合して再利用し、新たに確認する後続段落だけsource replayを追加実行する。

評価順序は、A短文化によるBの前詰め、A長文化によるBの後方再配置、A全削除と空段落の占有、A再入力とBの独立offset編集、同文再編集。BのUnicode・書式は最初の三段階で完全に不変とし、最終段階は両rendererで全10ページの画素一致を要求する。

各保存でparagraph identity・style registry/spans・typing・境界・spacing/break policy・allocation・実際のCID/GID/`W`とprovider hash、destination contextを照合する。対象ページ全文の独立pypdf抽出は空白正規化あり、厳密なauthored Unicodeはparagraph modelで別に照合する。対象外8ページは毎回MuPDF全画素と独立抽出を比較し、Popplerは途中で対象2ページと対象外1ページ、最終は全10ページを比較する。全ページの固定nontext paint、画像、annotation、元font resourceも照合する。

spacing不明、paragraph identityの統合、provider変更、共有領域の容量不足は出力を作らず拒否する負例とする。原本・派生PDF/PNG・font・生のparagraph/glyph/sidecarは公開しない。

今回の評価は一つの原本と人間が指定した二段落・既存slotの範囲に限る。新page/slot作成、semantic decoration、zero-heightの空paragraphは含まない。[設計と判断](../../docs/shared-paragraph-flow.md)を参照。

最初の`runs/final`はv1成功後、v2の空slot再bindingで安全に拒否された。clipの形状ではなく、完全保存で変わったpage object番号をgraphics state変更と扱っていたことが原因だった。共通backendでclip終端命令のbyte provenanceを照合する修正を行い、修正後の系列は`runs/verified`へ分けて保存する。元の失敗ログを残し、修正前v1を修正後系列の成功数へ加算しない。関連する回帰は`tests/test_empty_clip_rebind.py`にある。

`runs/verified`は5保存・4安全拒否で完了し、最終同文保存はMuPDF・Popplerとも全10ページの全画素が一致した。[集計](summary.json)に証跡を記録する。共通修正後の関連テスト21件は成功。ユーザー指定の区切りでcommitするため、全repository suiteの再実行は次回へ残す。
