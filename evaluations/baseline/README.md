# 評価の基準と3段階の実行

Issue #108。評価の入口は `prototypes/paragraph_frame/eval_hub.py`。各評価器（`*_evaluate.py`）と各PRの結果ファイル（`evaluations/paragraph_frame_*/results.json`）は変えずに残し、ここでは共通の記録と1つの基準で比べる。

## ファイル

| ファイル | 内容 |
|---|---|
| `baseline.json` | 基準。評価ごとの記録（1行1記録）、評価したコード、評価器のファイルのハッシュ、入力PDFのハッシュ、所要時間 |
| `changes.json` | 基準の変更の履歴：日付、コード、理由、変わった記録（前・後）、追加・削除、設計判断の要否 |
| `impact.json` | 変更したファイル → 評価の対応表（`direct`：通常、`related`：関連、`milestone`：節目を勧める） |

記録の形（`eval_records.py`）：`evaluation, case, step, group (real | fixture | sample), mode (faithful | layout), outcome, reason_class, failed, checks, tiers`。`tiers` は保存・実用・再編集・復元・拒否を分けて持ち、測っていない項目は `null`（未検証）にする。`layout` は、明示承認した移動（配置物・行の装飾・連鎖の要素）を保存した手。

## 使い方

```
# 変更から、focused tests と評価を選ぶ（対応表にないファイルも示す）
.venv/bin/python -m prototypes.paragraph_frame.eval_hub select --since origin/main

# 通常（スモーク＋直接影響する評価）／関連／節目
.venv/bin/python -m prototypes.paragraph_frame.eval_hub run --tier normal --since origin/main --root tmp/hub/<name> \
    --fusei <477.pdf> --pdfjs <pdf.js dir>
.venv/bin/python -m prototypes.paragraph_frame.eval_hub run --tier milestone --root tmp/hub/<name> --fusei <477.pdf> --pdfjs <pdf.js dir> \
    --id-survey tmp/id-survey-final --tg-survey tmp/tg-survey

# 基準と比べる（実行した評価だけ）。<root>/compare.json に書く
.venv/bin/python -m prototypes.paragraph_frame.eval_hub compare --root tmp/hub/<name>
```

- 入力（477、pdf.js のPDF、調査の出力）がない、またはマニフェストのハッシュと違う評価は実行せず、「input missing」「input differs」と示す。成功には数えない。
- 調査の出力 `tmp/id-survey-final`・`tmp/tg-survey` は、`indesign_survey.py`（PR #90）と tagged の調査（PR #91）で作る。リポジトリには入れていない。
- 各評価の所要時間は `<root>/timings.json` に残る。

## 基準を変えるとき

```
.venv/bin/python -m prototypes.paragraph_frame.eval_hub baseline --root tmp/hub/<name> --reason "<理由>" [--design-decision]
```

- 実行した評価の記録だけを置き換え、ほかの評価の記録は残す。変わった記録（前・後）・追加・削除と理由を `changes.json` に足す。
- 期待の実質的な変更（固定した評価の合否が変わる）は、設計判断が必要（`--design-decision` を付け、PR の「判断事項」に書く）。測定誤りの修正なら、そう書く（`docs/development-rules.md`）。
- 黙って基準を書き換えない。古い基準は git の履歴に残る。

## 最初の基準

Issue #105 の節目の全評価（`dd3ea7e`、main `e402b82` とコードは同一）の出力を、`eval_hub` の形に並べて作った。PR #90〜#104 が段階的に記録してきた回帰（各 `results.json` の `regression`）は、この1つの基準に置き換える。各PRの結果ファイルは変えていない。
