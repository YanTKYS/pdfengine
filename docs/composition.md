# 完全なfontからの局所再組版

評価日: 2026-09-08。実装は `shaped_font.py`、`composition.py`、保存adapterのfont resource追加、CLI `compose-selected`。今回の成果は、**元subsetの文字集合と元slotの幅に拘束されず、新しい本文を組版してPDFテキストとして保存できる経路**を成立させたことである。

## 最大のボトルネックと技術選定

手動selectionと明示幅を与えても、旧経路では新しい漢字・英字を符号化できず、符号化できる文字でも元slotとadvanceが違えば拒否していた。実PDFでは「施・設」「約」の輪郭不足と、CanviaのA/mの空輪郭を確認済みだった。推定改善やページreflowを先に進めても、本文を一般的な語句へ変更できない。この**文字を描く能力と配置を元PDFのslotから独立させること**を優先した。[元subsetの診断](subset-font-boundary.md)

| 候補 | 根拠・評価範囲 | 判断 |
|---|---|---|
| 元codeとresourceを再利用 | 同文の描画同等性に強い。新しい輪郭を補えない | 忠実性を優先する既存経路として継続 |
| PyMuPDF TextWriter | 過去の実測で埋込fontのfixed-pitch情報から不整合な`/W`が作られた | 新規経路のwriterに採用しない |
| PyMuPDF Shape / 明示`/W`補正 | 既存の独立writer実験では正しい原点へ描けた | 新規埋込が不可能という結論は誤り。ただしshaping・cluster契約は別に必要 |
| HarfBuzz + fontTools + 明示CID writer | 全文字列のGID/advance/offsetと、埋込program・`/W`を同じfontに結び付けられる | 採用し、実装・実PDF評価まで実施 |
| PDFBox等への保存backend置換 | font埋込APIは候補。今回の主比較は文書調査で、性能・全面互換性の実測ではない | 字形不足自体は別backendでも解消しない。現在の保存adapterで成立したため全面移植しない |

旧writerの定量比較は [backend-options.md](backend-options.md) に分離している。shapingは独自実装せず [HarfBuzz](https://harfbuzz.github.io/shaping-and-shape-plans.html)、fontの静的instance化とsubsetは [fontTools](https://fonttools.readthedocs.io/en/stable/subset/) に委ねた。独自部分は、元operatorを安全に除去する処理、限られたCIDFontType2のresource graph、組版結果をPDF状態へ接続する部分である。

## 二つの描画契約

元font経路は、元code・`/W`・text operatorを維持する。同文no-opでfontを変更しない。

新規font経路は、利用者が指定したfontで選択範囲全体を描く。元のサイズ、最初のbaseline、共通tracking、fillと有効な変換・clipを基にし、行間は観測値と新fontの上下metricsから決める。新fontの負のside bearingはinkの必要幅へ含め、明示枠内へ配置する。元の書体・字形・組版を完全維持する契約ではなく、報告は常に `font_substituted: true` とする。

MS-Minchoなど名前が一致しても、原本subsetと同じ版・同じ輪郭とは判定しない。Noto Sans JPの評価instanceは `wght=400`。入力fontのname tableにThinが残っていても、報告に残した軸値・instance SHA・実輪郭を検査対象にし、表示名からweightを判断しない。

```text
原本 + 人が確定したglyph範囲 + 利用可能幅 + 指定font + 新文
  ├─ 元resourceでno-op → 保存後glyphと全ページ画素を検査
  ├─ 対象コードの除去 → 非対象glyphを検査
  └─ 完全なfontの静的instance
       → HarfBuzzで改行候補全体をshape
       → UAX #14 / 禁則 / 明示幅で行を決め、各確定行をshape
       → fontTools subset（GID・輪郭・hmtxを保持）
       → CID / CIDToGIDMap / ToUnicode / W / FontFile2
       → 元paint位置へ新しいTm/Tjを挿入
       → 保存後検証 → 別ファイルへ確定
```

HarfBuzzのcontextual advanceとfontのnominal advanceは別の量である。PDFの`/W`には同じ静的fontのhmtxを1000/emへ換算して書き、kern・ligature・offsetを含む**実配置は各glyphの絶対Tm**で表す。両者を混同して`/W`へcontext依存値を入れない。保存後のGIDとoriginを計画へ照合し、CIDToGIDMap・`/W`も独立して読む。ArialのAVATARではkernによるadvance差を含め、合成アクセントのUnicode clusterも往復検証した。

CIDは `(GID, Unicode cluster)` ごとに割り当てる。同じGIDを別のUnicode列で使う場合もToUnicodeを取り違えない。現在は単調なLTRの1 cluster→1描画glyphを扱い、複数Unicodeが一つのglyphになる合字・合成文字を含む。複数glyphへのcluster展開や並び替えは、HarfBuzzの能力ではなくこのwriterの抽出・選択契約が不足するため拒否する。

## 旧文字・graphics state・周辺要素

旧文字は対象 `Tj/TJ/'/"` のコードを空送りへ置き換える。位置redactionは使わず、非対象コードと元のカーソル進行を保持する。新しい命令を最初の対象paint直後へ挿入し、`q/Q`とtext matrix・line matrixの復元で後続operatorへの影響を防ぐ。quote operatorの暗黙 `T*` は一度だけ実行する。単語間隔Twは元fontのコード長に応じて解釈し、元の選択文に空白がなくても、新文に追加した空白へ適用する。

既存resourceを上書きせず、新しいFont辞書だけをページ単位に分離して追加する。共有・継承resource、元fontの参照、既存画像・ExtGStateを保持する。新font graphが保存先writerの所有物であることを検査する。保存adapterは到達不能な旧Contentsを除き、暗号化情報・権限も保持する。RC4とowner passwordの回帰は通過し、AESの2テストはcrypto providerがこの環境にないため未実行である。

clipは対象eventのscopeを追跡する。新glyphの実輪郭が有効clipを外れれば拒否する。Ubiquitiのheaderは見かけ上の余白が大きくても、headerだけの狭いclipがあるため複数行化を拒否する。

背景は、選択文字より前に描かれ、元範囲を含む単一の塗り矩形として確認できる場合に扱う。人が確定した領域内では、その矩形の端をまたぐ再配置が可能である。同じ矩形が文字より後に描かれる場合は前景の障害物として拒否する。丸角path・複数subpathはまだ保守的なbbox判定となる。strokeの罫線・画像・非選択文字は別に検査する。

空白のUnicode名だけでは衝突判定から除外しない。元の埋込TrueTypeとGIDから輪郭が空と証明できる空白のみ除外し、その文字operatorと抽出位置自体は保持する。可視の輪郭へ対応付けたSPACEは拒否する回帰を追加した。

MuPDFが生成する `Type3 (xref generation R)` という表示名の参照番号変更は、resource aliasで比較する。選択Type3文字への対応を追加したわけではない。対象外Type3の描画と既存resource保持の検査は継続する。またgeometry比較を単純な小数丸めから実距離の許容誤差へ直し、丸め境界で0.0004 ptの差を過大判定しないようにした。

## 実PDF評価

確定runは `evaluations/composition/runs/final_release`。**12原本に13試行、font代替成功7・安全な拒否6・編集後検証失敗0**。Ubiquitiにはclipを越える長文化とclip内の同一行置換の2試行がある。一般業務文書の無作為標本ではなく、既存の目視確認済み範囲に対する能力・境界の評価である。

| 原本ID / 生成系 | 変更内容 | 今回の結果 | 共通原因・意味 |
|---|---|---|---|
| `word_takeo_notice` / Word | 協定→協議 | 安全拒否 | 丸角の塗りpathを背景と証明できない。glyph不足とは別の障害物モデルの限界 |
| `lo_migration_ja` / LibreOffice | 病院→施設、41→41文字 | font代替成功、1→1行 | 旧subsetで使えなかった施・設を完全なMS-PMinchoから描画 |
| `print_canvia` / 仮想印刷 | Login→Admin、14→14文字 | font代替成功、1→1行 | 旧subsetで空輪郭だったA/mを完全なArial Boldから描画 |
| `word_niigata_hearing` / Word | 概要→要約、7→7文字 | font代替成功、1→1行 | 旧subsetで使えなかった約をMS-Minchoから描画 |
| `lo_enterprise_en` / LibreOffice | ISO→IEC | 安全拒否 | 指定Arialの幅で改行すると、確認した高さに収まらない |
| `print_fcc_ms` / Microsoft Print to PDF | 同程度のラテン文字置換 | 安全拒否 | 複数の塗り矩形を一つのpath bboxで扱うため過大な衝突範囲。付近の別文との余白にも制約がある |
| `print_fcc_chrome` / Chrome・Skia | 英文→日本語・英数字・記号、83→136文字 | font代替成功、2→4行 | Noto Sans JP、元slotの幅に依存しない長文化。77種類の新Unicode |
| `lo_newfeatures_ja` / LibreOffice | 日本語の長文化、68→130文字 | font代替成功、2→3行 | MS-PMincho、見出し・罫線・画像を維持。59種類の新Unicode |
| `word_kyoto_questions` / Word | 質問文の短文化、147→27文字 | font代替成功、4→1行 | 新しい文章を詰めて再配置し、不要な元行を除去 |
| `word_osaka_fire_notice` / Word | 本文の短文化 | 安全拒否 | 範囲内の字間が共通でなく、単一組版書式へ暗黙変換しない |
| `print_ubiquiti` / 仮想印刷 | 日本語を含む64文字へ長文化 | 安全拒否 | 対象headerのactive clip外へ出る |
| 同原本・clip内試行 | 日付headerを14文字へ変更 | font代替成功、1→1行 | Arial/YaHei混在を明示したNoto Sans JPへ統一。元font混在の維持ではない |
| `word_osaka_symposium` / Word | 2文字へ置換 | 安全拒否、組版未着手 | Unicode復元不能、対象operatorを確定できない |

文字数は観測・入力のUnicodeコードポイント数。行頭末尾の空白処理はlayout契約に従う。実PDFでの1行→複数行はこの固定範囲では成功に含めていない。新経路の1→複数行と出力の再編集は自動統合テストで検証し、実PDFの長文化の証拠は上記の2→3、2→4である。

すべての成功例について、**元resourceでのno-opを先に保存し、対象ページのPoppler 144 dpi全画素差分0と独立pypdf抽出一致を確認してから変更**した。Unicode復元不能例はこの段階で止めた。旧Stage 1が元font混在を理由に拒否したUbiquitiも、このsource no-op検査を省略していない。

編集後は次を確認した。

- 保存後Unicode列、GID、origin（計画との差0.002 pt以内）、サイズ、fill、opacity、新埋込program、既存font resource。ToUnicodeは独立pypdf抽出、CIDToGIDMapと`/W`は直接照合。
- 元範囲と新inkの和集合に1 ptを足した領域外で、MuPDF 144 dpi画素が完全一致。Popplerも**差分画素0**（採否閾値はチャンネル差8超が0）。1 ptはraster境界のためで、無制限にmaskを広げない。
- 全対象外ページのMuPDF画素完全一致、独立抽出一致。すべてのページの図形・画像fingerprintを照合。
- 削除中間PDFの対象ページ全文が、原文の1箇所を空文字へ置換した結果と一致し、他ページは変わらない。編集PDFも同様に新文への1箇所置換として検証。
- 原本SHA-256、ページ数、暗号化・権限保持。長文化2例、Canvia見出し、Ubiquiti headerのPoppler画像を目視確認。

反復headerは対象外ページに残る必要がある。このため削除検査には、文書全体での旧文字列不在ではなく、対象ページの変更と他ページ保持を確認する既存の全文比較を使用する。これは重複文字列を黙って削除する許可ではない。旧文字が残る中間結果、他ページも削除した結果は不合格となる。

独立検証の値とsource/font/engineのhashは [公開集計](../evaluations/composition/summary.json) に収録した。原本・出力PDF・PNG・glyph計画はローカル証跡であり、再配布していない。

## 既存経路の回帰

新規font builderを指定しない保存経路の動作は保持した。全自動テスト254件成功・AES 2件skipを確認した。最後の単語間隔修正では、元文に空白がない場合のTw継承を追加し、組版テスト12件を再実行して通過した。

実PDFは `composition_regression` で旧同文no-op 13成功/2拒否を再確認した。`composition_regression_stages` では既存Stage 2の6成功、Stage 3の13成功、Stage 4の2成功と、各拒否・skipが従来どおりだった。合計21編集についてPopplerと独立抽出を再実行した。旧Stageの成功を新規font成功と合算して成功率にはしていない。

## 再現方法

原本のURL・SHA-256は [sources.json](../evaluations/sources.json) にある。原本を規定のcorpusパスへ取得し、ハッシュを照合する。既存selectionがなければ次を実行して候補を生成し、bboxと元ページを確認する。

```powershell
.\.venv\Scripts\python.exe -m evaluations.backend.seed_ranges
.\.venv\Scripts\python.exe -m evaluations.backend.stage1 --run-name stage1_audit_v3
.\.venv\Scripts\python.exe -m evaluations.backend.followup --run-name stages_audit_v5 --stage1-run stage1_audit_v3
.\.venv\Scripts\python.exe -m evaluations.composition.evaluate --run-name my-composition-run --font-dir C:/Windows/Fonts
```

各runは新規名が必要。既に旧評価証跡がある環境では前2評価の再生成は不要。`evaluate.py`のCASESに明示幅・領域下端・font face・variation・置換文がある。MS Mincho、MS Gothic、Yu Gothic、Arial、Noto Sans JPの完全なfontを用意する。Notoの既定weightをそのまま使わず評価仕様の400を適用する。

Popplerと独立pypdf用Pythonの場所は `evaluations.realpdf.evaluate` の `DEFAULT_POPPLER` / `DEFAULT_PYPDF` で指定している。別環境ではその実行ファイルを設定する。原本SHA・エンジンhash・font hashが異なる試行は今回の証跡と同一視しない。新規経路のCLI単独はMuPDF検査までで、ここでの独立評価結果とは分ける。

## 次に必要なアーキテクチャ変更

新文字の描画は、この構成で成立する可能性という段階から、限定した実PDFで成立する段階へ進んだ。HarfBuzz・fontToolsを保ち、元resource経路と新規font経路の二つを使い分ける足場は伸ばせる。Pythonから別言語へ移すこと自体が、次の主要解決策とはならない。

次の中心は**選択範囲を編集可能な書式区間とpaint要素へ分解するモデル**である。文字列位置に結び付くstyle span、元fontを保つ区間と代替する区間、baseline・tracking・paragraph制約を扱い、既存の一つのstyleへ押し込む構成を拡張する必要がある。

そのうえで、pathの実形状・描画順・clipの所有関係・Form invocationを持つ障害物モデルへ進む。現在のbboxによる安全拒否を、guard解除で減らすべきではない。複雑なFormやgraphics stateはcontent interpreterの拡張、または同じpaint情報を提供できる成熟した低レベルbackendへの局所置換が候補になる。

後続段落・図形・表・改ページを自然に動かすには、さらに領域間の制約と依存関係を持つ文書モデルが必要である。今回の局所writerをそのままページ全体reflowへ拡大する設計は採らない。
