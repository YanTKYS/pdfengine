# 段落再構築の汎用化・領域推定・元書体保持

2026-10-22。PR #86 merge 後の main `54219cc4d35c1ea4bce6884c2a1fe150f641c92f` を起点とする独立 PoC。製品 API・GUI は変更していない。

## 結論

B2 は **PDF・ページ・選択文字列・置換文字列から段落と使用可能領域を提案し、明示承認後に保存する経路**になった。手動レイアウト指定は B1 の 7 種類から 0 種類へ減った。既存 4 要求と未使用 2 原本の 8 要求、計 12 要求で実保存でき、全文の描画・対象外保護・意味構造の安全監査を通過した。ただし、初回のコピー・検索を含む品質 gate は **8/12** であり、全要求合格ではない。

元の glyph/code/program/style を再利用することで、Osaka の後続段落「す。」の孤立行は解消し、LibreOffice 短文化は B1 の 3 行から 2 行になった。追加原本では、A と B1 が拒否した fire の複数描画命令・direct-P MCID を含む本文を編集できた。新方式を次の開発基盤として継続する根拠は増えたが、一般文書への本番採用を承認する結果ではない。

主な限界は、占有幅から推定した右端が作者の frame を保証しないこと、短文化した単一行 PDF から元の幅・字下げ・行間を復元できないこと、不足文字の承認済み Droid fallback が明朝本文中で太く見えること、reader の行境界検索・推論空白で品質未達が残ることである。

## 固定した評価

[manifest](../evaluations/paragraph_frame_generalization/manifest.json) を実装前の commit `a18019d` で固定した。対象・ページ・全文・期待結果・共通代替 font・評価専用 frame を以後変更していない。4 原本の SHA-256 は最終時点でも一致した。正解 frame、line ID、元 glyph ID は比較監査と A/B1 の入力にのみ使い、B2 API へ渡していない。実装に PDF 名・ページ番号・文字列・glyph ID による特例はない。

| 原本 | 対象 | 要求 |
|---|---|---|
| `word_osaka_guideline` | p1、対象＋後続の 2 段落 | A 短文化、B 長文化。PR #86 と同一 |
| `lo_migration_ja` | p9、対象＋後続の 2 段落 | C 短文化、D 長文化。PR #86 と同一 |
| `word_osaka_fire_notice` | p1、対象＋後続の 3 段落 | F1 同長、F2 短文化、F3 長文化・折返し、F4 複数 show 命令横断 |
| `lo_newfeatures_ja` | p1、導入の 1 段落 | N1 同長、N2 短文化、N3 長文化・折返し、N4 Latin・空白・数字を含む複数 show 命令横断 |

fire と newfeatures は PR #86 の実装では未使用だった。Kyoto 候補への切替や失敗ケースの差替えはしていない。原本・font・生成 PDF・画像は git に含めない。

## 実装と承認契約

`auto_detect.py` は現在の PDF revision から、visual line と描画順、P/Span/MCID、字下げ、サイズ、行間を観測する。タグは段落推定の証拠として使い、Span を恒久的編集境界にしない。untagged 文書では同列・サイズ・字下げ・間隔を使う。対象と後続候補の本文を proposal に表示する。

右端は対象・関連後続行の最大 pen extent、左端は近傍の continuation edge、下端は文字・線・画像・link・annotation の保護境界またはページ端から推定する。観測と推定、confidence、曖昧さを分ける。行 box が実 ink を包含しない場合に備え top には観測 em の余裕を設け、最終 raster でも検査する。幅を足して容量不足を回避することはない。

`auto_style.py` は元 resource の concrete code/GID witness を優先する。同じ Unicode があるだけで別 resource から借りない。次に同一埋め込み program の Unicode shaping を試し、それでも不足する文字のみ SHA-256 で束縛された明示 substitute を使う。program、size、PDF width、Tc/Tw/TJ に由来する送り、色、Tz、取得できる font descriptor を記録する。元 font の太字・斜体は program の再利用で維持するが、不明な metadata を推定済みとは扱わない。

`auto_layout.py` は元幅、既存行の baseline、UAX14 の改行候補を使う。追加行のみ観測 median leading を使い、観測不能なら既定値であると表示して承認を求める。行末の Tc/Tw/TJ の後続送りは glyph の占有幅と区別する。極端に短い最終行は許可された改行位置で前行から再配分し、font 縮小や frame 拡張はしない。

`auto_writer.py` は元 text show を TJ 補償つきで除去し、元 code または検証した新 glyph を配置する。白塗りではない。対象の高さが変わらなければ follower の命令に触れず、高さが変われば元の各 glyph・書式・行間を保って平行移動する。元の ordered scope、MCID、P/Span、Lang、ParentTree を残し、必要な ActualText のみ更新する。削除により空になった scope も PDF 単体再解析で回収する。意味構造の範囲や順序を証明できなければ拒否する。

`auto.py` の `propose → approve(acknowledge_inference=True) → save` は承認時・保存時に再計算し、PDF hash・proposal・font binding の変更を拒否する。frame を引数として受け取らない。承認は 1 回で、推定範囲・follower 移動・不足文字 substitute を含む。

stage で exact foreign glyph/code/style、元 font program、非文字描画、全文、glyph bounds、保護対象との衝突、意味構造、MuPDF の推定 frame 外側画素を検査してから PDF を rename する。既存出力を上書きしない。PDF だけの rename は atomic だが、その後の JSON receipt と一体の crash-atomic bundle ではない。

## 比較結果

「成功」は今回の明示 gate を通った意味であり、全書体・全 reader・自然さの保証ではない。品質未達でも安全性 gate が通った候補と、安全な編集ができず PDF を出さない拒否を区別した。

| 要求 | A 既存公開経路 | B1 手動 frame | B2 | B2 対象行数 原本→出力 | B2 follower |
|---|---|---|---|---:|---|
| A | 拒否 | 成功 | 成功 | 2→2 | 原命令・1 行維持 |
| B | 拒否 | 成功 | 成功 | 2→2 | 原命令・1 行維持 |
| C | 成功 | 成功 | 成功 | 3→2 | 元書体で −20.40 pt |
| D | 検索品質未達 | 検索品質未達 | 成功 | 3→3 | 原命令・3 行維持 |
| F1 | 拒否 | 拒否 | 成功 | 2→2 | 原命令維持 |
| F2 | 拒否 | 拒否 | 成功 | 2→2 | 原命令維持 |
| F3 | 拒否 | 拒否 | 検索品質未達 | 2→3 | 2 段落を元書体で +19.80 pt |
| F4 | 拒否 | 拒否 | 検索品質未達 | 2→2 | 原命令維持 |
| N1 | 成功 | 拒否 | 成功 | 2→2 | なし |
| N2 | 成功 | 拒否 | 成功 | 2→1 | なし |
| N3 | 検索品質未達 | 拒否 | 検索品質未達 | 2→3 | なし |
| N4 | 成功 | 拒否 | コピー・検索品質未達 | 2→2 | なし |

A は `edit_intent` だけで止めず、既存の `page_flow`・明示 provider 選択・acceptance・`edit_shared_flow` を評価した。newfeatures では固定最大 envelope 下端 175 pt が元の線 174.5 pt と接するため、固定 region override による拒否に加えて、既存の **自動 region** 公開経路も試し、全 4 件保存できた。A の機能追加ではない。A の selected range 内の共通 prefix/suffix は承認 substitute で再生成されることがあるため、B2 の normalized changed-middle と契約を区別し、対象外破損と混同しない。

B1 は変更せず、新 fire の 3 段落・newfeatures の 1 段落を旧「ちょうど 2 段落」契約で拒否した。旧 4 要求の成功は維持される。旧 A/B1 の 4 要求は PR #86 の固定出力・監査を再利用した。今回製品コードも B1 engine も変更しておらず、過去の成功保存を繰り返していない。

## 自動認識と書体の改善量

全 12 要求の対象・関連後続の全文は、評価専用 oracle と一致した。B1 に必要だった `frame / left / right / first_x / baseline / line_height / paragraph_gap` の 7 種類は、B2 では手動入力 0 種類になった。これは作者の frame を復元できた意味ではない。たとえば LO の提案下端はページ端 841 pt、固定評価 envelope は 710 pt で異なる。出力の ink と保護監査には後者を固定して使い、推定 API に座標を戻していない。

12 出力の対象本文、計 1,022 glyph の内訳は次のとおり。ASCII の意味ある空白も数え、follower はこの分母に含めない。

| 描画方法 | glyph 数 |
|---|---:|
| 未変更の元 occurrence/code/style 維持 | 819 |
| 同一元 resource の concrete code/GID witness 再利用 | 133 |
| 同一埋め込み program から新 code を生成 | 1 |
| 明示承認 Droid substitute | 69 |

元 program を使う glyph は **953/1,022（93.2%）**。未変更対象 glyph の元 code/style は 819/819 を独立照合した。B1 は対象と follower 全体を fallback で再描画していた。今回 follower は全 884 glyph で元 code/style を保ち、うち 261 glyph は元の行配置を保つ平行移動、残りは原命令のままである。

Osaka follower は B1 の「…いたしま」＋「す。」の 2 行から、元の 1 行へ戻った。LO C は明朝の比例幅を使って 2 行になり、A と同じ行数になった。D は今回の配置では 3 行に収まり、native 検索も通った。この一例から、検索問題の一般解決や特定要因による改善とは断定しない。

独立目視では B/C/D/F3/N3 の表示・保護線との非接触・follower の改善を確認した。一方、D/N3 などの fallback 部分は明朝と比べて太いゴシックに見え、文中で強調のような混在が残る。**元 font 保持率の改善と、自然な代替書体一致の達成は別である。** 共通承認 font を結果に合わせて差し替えてはいない。

## 保存・再編集・安全監査

B2 は全 12 出力を別プロセスから PDF 単体で開き、固定した追加置換を新規 proposal→承認→保存した。元 PDF・元 frame・元 glyph ID・private sidecar を B2 の再編集 API に渡していない。

| 検証 | 結果 |
|---|---|
| 初回保存 | 12/12。安全 gate 全件通過、コピー・検索を含む品質 gate は 8/12 |
| PDF 単体・別プロセス再編集 | 12/12 保存。安全 gate 全件通過、品質 gate は 11/12。F4 の native 検索のみ未達 |
| PDF 単体の無編集保存 | 12/12。MuPDF/Poppler 全ページ差分 0、glyph と意味内容・構造一致 |
| 初回・再編集の対象外描画 | 全件、固定 oracle 外側 MuPDF/Poppler 144 dpi で 0 pixel |
| 原本 | 4/4 SHA-256 不変 |
| 最終 stage 再検証 | 初回 12＋再編集 12 の保存済み候補を read-only `validate_stage` で全件合格 |

再編集の 11/12 は別の固定置換に対する品質結果であり、初回の 4 件の品質未達が解消したという意味ではない。

無編集保存は `PdfWriter` による serialization であり、再組版の no-op 試験ではない。object number は PDF ごとに局所的なので、意味比較は参照番号を正規化し、ActualText・属性・順序・backlink・ParentTree の整合性を比較した。外側 glyph の座標は丸めず比較する。移動 follower の各 glyph の dy 差は float32 再観測の許容差 0.0001 pt 未満であり、実測 spread を JSON に保存している。

A の newfeatures 4 出力も PDF 単体から別プロセスで既存公開経路を試した。固定 region による拒否を除いた自動 region の acceptance でも、`shared-flow-confirmation: source output marker inventory differs from owned slots` により拒否した。アダプタの例外を安全拒否として数えていない。旧 LO の sidecar あり成功は前回証拠を保持するが、新 4 出力の sidecar 継続は今回未検証である。

N2 の短文化後は 1 行しか残らないため、次の B2 proposal の右端は約 566.89→487.46 pt、左端は 28.5→34.1 pt、leading は観測 15.70→既定 16.5 pt へ変わった。新 revision の証拠が失われた結果であり、元 oracle を使って補正していない。再編集は成功したが、元の author frame・字下げ・行間を保存 PDF から安定して復元する課題は残る。観測不能な leading と複数選択候補は proposal に警告を出し、承認が必要である。

検索・コピーでは内部 glyph Unicode 全文と reader の結果を別々に検査した。N1–N3 の数字周辺スペースは原本にも存在し、原本 reader の全文に固定置換を行った結果との完全一致を認める。内部の空白を一律削除してはいない。N4 は選択範囲にその推論空白が含まれ、同じ証明では一致せず、コピー品質未達のままとした。F3/F4/N3 の長い native 検索も未達のままである。各行の文字表示や内部全文が正しいことだけで検索合格にしていない。

## テストと性能の範囲

focused は `tests/test_paragraph_frame_auto.py` と `tests/test_paragraph_frame_poc.py`。承認改ざん・stale revision、元 code と色/Tz/Tc、跨 scope、direct-P、Lang、空 scope の再解析、ActualText/ParentTree、禁則、容量不足時の非公開、Ts の明示拒否を確認した。最終 focused は **22 件、4.08 秒で PASS**。詳細は [results.json](../evaluations/paragraph_frame_generalization/results.json) に記録する。full suite は CLAUDE.md の focused 方針に従い未実行。

初回 B2 の記録された save 呼び出しは 0.70–2.82 秒、12 件合計 19.34 秒だった。これは save 内の再解析・組版・当時の stage を含むが、別の proposal/approval 呼び出しの時間を含まない。初回保存後に stage を強化したため、最終版の end-to-end latency でもない。最終候補は現在の stage で read-only 再検証した。A/B1 の旧測定、font cache、renderer cache の条件も揃っておらず、速度向上率・p95・cold/warm・RSS は主張しない。

## 適用限界と次の判断

現在の対応は horizontal、identity CTM/Tm、Ts=0、1 code・1 Unicode scalar・1 glyph、限定した ordered P/Span の文書である。回転・縦書き・複雑な列・Form 内の対象・透明 text・混在 rise・ligature/複雑な cluster・未知の意味構造などは拒否する。日本語の全書式を扱う実装ではない。

次の主要課題は、保存後にも一貫した frame/段落/読み順の推定、元 subset の Unicode 以外の glyph 対応を安全に広げること、字種・太さ・サイズに合う部分 fallback、複数列や背景との関係、cluster 単位の shaping、reader 間のコピーと行境界検索である。source code witness の再利用だけを一般的な font 生成能力と混同してはいけない。

**段落再構築を主力候補として継続し、A の精密な部分編集も残す。** 今回は元書体と手動指定の制約を減らし、異なる構造の追加原本で編集能力が増えた。一方、品質未達 4 件・PDF 単体での推定不安定性・fallback の見た目が残り、本番の任意 PDF 編集を約束する段階ではない。

## 再現

環境は repository の `.venv`、MuPDF/PyMuPDF、pypdf、HarfBuzz、uniseg、fontTools、Pillow、`pdftoppm`、`pdftotext`。原本は `evaluations/sources.json` / 固定 manifest の URL と SHA-256 に従って corpus の各 path に配置する。font は PyMuPDF 内蔵 CJK の bytes から生成し、manifest の共通 SHA-256 と一致させる。

```bash
.venv/bin/pytest -q tests/test_paragraph_frame_auto.py tests/test_paragraph_frame_poc.py --durations=5
# 必ず新しい root を使う。initial/baseline は既存 PDF を上書きしない。
.venv/bin/python -m prototypes.paragraph_frame.generalization_evaluate --root tmp/generalization-fresh --phase initial
.venv/bin/python -m prototypes.paragraph_frame.generalization_evaluate --root tmp/generalization-fresh --phase baseline
.venv/bin/python -m prototypes.paragraph_frame.generalization_evaluate --root tmp/generalization-fresh --phase audit
.venv/bin/python -m prototypes.paragraph_frame.generalization_evaluate --root tmp/generalization-fresh --phase lifecycle
.venv/bin/python -m prototypes.paragraph_frame.generalization_evaluate --root tmp/generalization-fresh --phase audit-A --cases N1 N2 N3 N4
.venv/bin/python -m prototypes.paragraph_frame.generalization_evaluate --root tmp/generalization-fresh --phase lifecycle-A --cases N1 N2 N3 N4
.venv/bin/python -m prototypes.paragraph_frame.generalization_evaluate --root tmp/generalization-fresh --phase qualify
```

B2 個別の review には `generalization_cli propose / approve / save` を使う。`propose` は `--source --page --old --new --output`、必要なら `--occurrence --substitute-json` を指定する。`approve` は `--source --proposal --acknowledge-inference --output`、`save` は `--source --acceptance --output` を指定する。CLI/API に frame 入力はない。

実験の最終ファイル対応、監査 JSON、元書体内訳、旧証拠の再利用先は [results.json](../evaluations/paragraph_frame_generalization/results.json) と [audits/](../evaluations/paragraph_frame_generalization/audits/) にある。初回候補は保存保持し、実装改善で描画が変わった C/D/F3 のみ別ファイル v2 を作った。最終の行末処理・top・guard 変更では全 12 件の行計画と font 内訳が一致したため、保存を繰り返さず再検証した。
