# 明示した複数要素の評価

外部PDFを変更せずにA/Bの範囲を確認し、固定領域と`A → B`を宣言する。
元のtext operatorのno-opをMuPDF・Popplerで確認してから、上位document APIで編集する。
評価者が指定するglyph ID、幅、領域、font、固定paintは`evaluate.py`の`CASES`に置く。
engineには文書名や生成元別の分岐を追加しない。

| ケース | 確認範囲 | 利用可能領域と関係 |
|---|---|---|
| `word_okinawa` | 2ページ目の番号付きparagraph (7)、(8) | 幅468pt、領域 `[70,694,540,768]`、元の先頭/最終baseline間隔を明示確認 |
| `fcc` | 赤い英文本文と最終の例示行 | 幅196pt、領域 `[565,66,772,155]`。黄色い背景、外枠、フランス語の下線を固定 |
| `word_niigata` | 最終の住所と担当部署の行 | 暗号化PDFのため、plaintext sidecarを作るimportで拒否 |

各成功ケースで、A長文化 → A短文化 → B全文削除 → 空Bを伴うA長文化 → A全文削除 → A再入力 → B再入力 → A no-opを試す。
途中で拒否されたケースはそこで止め、後続を成功扱いにしない。
最後に関係なし、領域overflow、外部再保存の負例を確認する。

全文置換の書式は評価者が先頭styleと明示確認する。元の番号後の空白だけが別fontである場合も、置換後のその空白は確認した新styleとなる。
未変更のBは元font resource・文字コード・GIDを保持して移動する。
全文削除後の再入力には明示したfull-font recipeを使用する。

```powershell
.\.venv\Scripts\python.exe -m evaluations.document_flow.evaluate --case word_okinawa --run-name word-local
.\.venv\Scripts\python.exe -m evaluations.document_flow.evaluate --case fcc --run-name fcc-local
.\.venv\Scripts\python.exe -m evaluations.document_flow.evaluate --case word_niigata --run-name encrypted-local
```

原本の取得元とhashは`../sources.json`、集計は`summary.json`。
入力PDFは既存の`evaluations/realpdf/corpus`に置く。run名は未使用のものを指定する。
`--reuse-gates <前回runディレクトリ>`では、同じ選択のdecoded operator program、保存済みPoppler比較画像、MuPDF全ページ一致を再確認し、元PDFのno-opの再生成を省ける。
PDFそのものが異なる場合には利用できない。

今回の結果は、沖縄県PDFが8段階成功、FCC PDFが長文化・B追従成功後に短文化を安全に拒否、新潟県PDFが暗号化sidecar方針で取込拒否。3原本の境界検証であり、一般PDF全体の成功率ではない。最終回帰は397 passed / 2 skipped（任意AES providerなし）。FCCの生成時と最終coreでの再open・拒否確認は、集計内で実行環境を分けている。

生のPDF、PNG、glyph/paint report、sidecarは`runs/`に残す。公開対象には含めない。
成功時だけでなく拒否時の`progress.json`、`summary.json`と、既に成功した段階の`stages.json`を残す。
安全な拒否は評価結果として終了し、評価器の異常・不正な出力作成は失敗として扱う。

検証では、対象範囲外のPoppler 144dpi全channel差分（1pt margin）、MuPDFの対象外画素と対象外ページ、独立pypdfの全ページ抽出、font code/GID/width、固定非text paint、画像を比較する。
glyph削除後の挿入は逆方向の削除比較でも全ページ抽出を照合する。
最終no-opは対象ページ全画素を両rendererで比較する。

所有背景を持つB、両要素の空状態、3要素chain、保存のrollback、証拠再利用の不変性は単体テストでも検証する。
この外部PDFセットでBの所有pathを動かす複数要素の例は未評価であり、固定共有背景の成功と区別する。
既存の単一group移動による実PDFの所有paint検証は`../elements/`にある。
