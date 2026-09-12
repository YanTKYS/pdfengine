# 可変共有containerの外部PDF評価

Microsoft Print to PDF由来のFCC注記を使う。原本、派生PDF/PNG、font、sidecar、raw glyph/paint reportは公開しない。取得元とSHA-256、確認したpolicy、集計を`summary.json`に記録する。

```powershell
.\.venv\Scripts\python.exe -m evaluations.variable_container.evaluate --run-name new-run
```

既存のFCC原本、Windows Arial、Poppler・独立pypdf評価環境を使用する。原本A/Bのsource replayは`ink_collision/summary.json`の証跡を引き継ぎ、新しいpaint再構築のno-opを別に検証する。

H（French本文）を固定child、A/Bを編集可能な子として確認する。黄色い背景と複合外枠はcontainer所有。A/Bのparagraph所有物にはしない。上下のcapを保てる直線帯域と、下辺anchor・最大領域・baseline gapは評価者指定である。

伸長、縮小、双方空、双方再入力、生成後no-opを検証する。各保存でPoppler 144dpiとMuPDFの領域外画素、独立全ページUnicode、font mapping、予定した2つのpaint以外のnontext paint、画像、固定child、policyの復元を確認する。元の固定container、最大領域超過、固定child編集、共有paintのparagraph所有、曲線を横切る帯域指定は負例にする。

2026-09-12の最終実行は5段階すべて成功、5負例すべて安全に拒否。枠の下辺は155ptから178.521699ptへ伸長し、短文化時に155ptへ戻る。全段階で領域外差分0、生成後no-opではMuPDF・Popplerとも全画素差分0だった。原本は1ページであり、外部の対象外ページ検証は今回追加していない。

公開した`summary.json`は最終実行の集計に、評価日・目視確認・過去の探索時拒否・回帰テスト結果を付記したもの。`environment.engine_sha256`と`runner_sha256`により、実行したコードを照合できる。生の証跡はgit対象外の`runs/<run-name>/`に保存する。

外部評価後、同じcoreでの全体回帰は453 passed / 2 skipped（476.09秒）。新規21テストを含む。2件のskipは従来からの任意AES provider未導入によるもの。

狭い元gapでdescenderが後続行のfont bboxに掛かった探索時の拒否も、[設計資料](../../docs/variable-container-paints.md)へ記録する。この評価は一つの外部原本に対する連続した編集であり、一般的な業務PDF全体の成功率ではない。
