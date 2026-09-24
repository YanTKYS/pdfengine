# 確認済み空き領域へのcontinuation評価

**未実行**。最終engine（digest `a20828d95176ea6b8473654f144b74d88b2579f5c89a20d6063643b31fec52e0`）で外部原本の編集系列はまだ実行しておらず、公開集計も作成していない。状況は[再開地点](../../docs/continuation-checkpoint.md)を参照。

既存corpusのLibreOffice移行資料を使用する。対象は前段階と同じ4/5ページの混合書式paragraph、生成先は目視確認した6ページ上部左側の空き領域`[55,80,385,120]`。右上の図版は固定・保護する。領域・描画順序の判断は評価者の明示指定であり、engineによる意味推定ではない。

## 実行に必要なもの

既存評価と同じWindows検証環境を前提とする。代替font、別PDF、別rendererでは実行しない。fontは再配布しない。

| 項目 | 必要なもの |
|---|---|
| Python | 3.12以上。`requirements.lock.txt`と`pip install -e .` |
| 原本 | `evaluations/realpdf/corpus/lo_migration_ja.pdf`、SHA-256 `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`（[取得元](https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf)）。hashが異なれば停止する |
| 本文provider | `C:/Windows/Fonts/msmincho.ttc`のface 1 |
| Latin provider | `C:/Windows/Fonts/times.ttf` |
| Poppler | `evaluations/realpdf/evaluate.py`の`DEFAULT_POPPLER`（`pdftoppm.exe`） |
| 独立抽出 | 同じく`DEFAULT_PYPDF`のPython（pypdf導入済み） |

## 実行

```powershell
.\.venv\Scripts\python.exe -m evaluations.continuation.evaluate --run-name <未使用のrun名>
```

既存のrun名は拒否される。PDF・PNG・本文・glyphログは`runs/<run名>/`だけに保存し、Gitには入れない。

## 自動照合

- 開始時: Poppler・独立pypdfの存在と原本hashを確認し、provider fileのhashとfaceを記録する。
- source no-op: 4/5ページのsource slotの同文replayが、MuPDF全画素・Poppler全画素・独立Unicodeで元PDFと一致する。
- 各保存（overflow、second、shorten、regrow、no-op）:
  - 事前planと実行planが一致する。
  - `open_shared_flow`で復元でき、確認契約hashが不変である。
  - slotは既存2つと生成1つの計3つ。生成slotを作るのはoverflowだけで、以降は作成証跡を変えずに同じslotを使う。
  - shortenでは生成slotが`occupancy=None`になり、文字を描かない。
- 各保存の監査:
  - 編集した4〜6ページで、Poppler差分が確認済み領域（1pt余白込み）の外に出ない。
  - それ以外のページはMuPDF全画素が一致する。
  - 独立pypdfで全ページのUnicodeを照合する。
  - CID/GID/`W`とfont対応を確認する。
  - 元font resource、text以外のpaint、画像、annotationが変わらない。
- final no-op:
  - 全ページでMuPDF・Popplerの全画素が一致する。
  - allocation、paragraph・style・destinationの記録、slotのidentity・行geometry・alignment・inline style（tracking・riseを含む）が変わらない。
  - 計画glyphのUnicode・GID・origin・size・advance・code・CID・`W`幅が直前のregrowと一致する。
- 容量不足: 最終状態へ160字を加えると確認済み容量（約271字）を超え、最終PDF/sidecarを公開せずに拒否する。

## 実行後に確認すること

1. `summary.json`の`environment.engine_digest`が上のdigestと、`runner_sha256`が`c2c279db3ee58d6a4594481e73a0280bbb78be98f35f75a9df8d75ec50503537`と一致することを確認する。
2. overflowのallocationを以前の計画（既存slot 209字、生成slot 36字・2行）と比べる。差があれば原因を調べ、過去値へ合わせるためのコード変更はしない。
3. `runs/<run名>/`の各`*-audit/page-<n>/after.png`を目視する。対象はoverflow・second・shorten・regrowの4〜6ページと、no-opの全ページ。文字の重なり、行ずれ、不自然な余白、図版への侵入、欠落、別paragraphの破損、想定外のpaint順序を見る。
4. すべて成功した場合だけ、`runs/<run名>/summary.json`を`evaluations/continuation/summary.json`へ置き、資料の「検証中」を更新する。

公開集計には、原本URL/hash、engine・評価コード・helperのhash、実行環境、provider fileのhash/face、各段階と拒否の検査結果だけを含める。これは単一外部原本での境界評価であり、一般PDFの成功率ではない。
