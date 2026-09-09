# source・paint・要素の所属を分けた局所編集

この段階で埋める穴は、文字と図形を同じページ座標で観測できても、**どのsource命令を変更し、その図形を文章と一緒に動かしてよいかが別問題のまま**だったことである。文字のsubset coverage、原コードの再利用、書式付き部分編集は既存経路へ残し、source provenanceと利用者が確定する所属・振る舞いを接続した。

実装は `paint_provenance.py`、`marked_content.py`、`paint_geometry.py`、`elements.py` と、既存の `paragraph.py` / CLIへの接続からなる。全ページreflowや、下にある物を一律に押す処理は実装していない。

## 仮説と責務

| 情報 | 決める層 | 証拠と意味 |
|---|---|---|
| source operator | pypdfのdecoded streamと既存lexer | 原本SHA、ページ、Contents xref、decoded byte range、operator index、Form invocation。圧縮ファイル内の物理byte offsetではない |
| 実際のpaint | MuPDF `FzDevice2` | 解釈後のpath、glyph、matrix、clip stack、fill rule、色、opacity、stroke、image、group/layer。sourceの位置や意味を勝手に付けない |
| source→paint対応 | counterfactual検査 | 一つのsource paint命令だけを消費する変更によって、一意なpaint bundleだけが消え、他paintとscopeが全て保たれること |
| marked content | 独立したactive scope stackとStructure Tree照合 | BMC/BDC/EMC、MCID、ParentTree、構造要素の親とK backlink。所属の手掛かりであり、移動許可ではない |
| 要素の所属 | 候補提示＋呼び出し側の明示関係 | `backgrounds` / `borders` / `decorates` / `unrelated` と `fixed-to-element` / `fixed-to-page` |
| 行組版とfont | 既存paragraph / original-code / HarfBuzz provider | 元書式・未変更glyphを保持し、変更区間を明示fontでshape。観測幅を利用可能幅に変換しない |
| 将来のflow | 今回は未確定の契約 | order、anchor、available region、follows、page break。縦位置だけで順序・追従関係を決めない |

`inspect-element` のJSONは観測と候補を記録する。geometry・描画順から付けた `background_candidate` 等は `inferred`、利用者のrelationsは `explicitly_supplied` である。source対応の `proven` はこの区別と独立する。候補のbboxが大きいからという理由で図形の所有者を確定しない。

## rendererとsourceをどう結んだか

成熟したPDF interpreterを全面的にPythonで再実装する方法は採らない。MuPDFのdevice callbackを用い、textはglyph単位に分解して比較する。pathを一つ消しただけでrendererが隣り合うtext bufferをまとめることがあるため、callbackの個数だけでは同等性を判定できない。

対象sourceの `f` / `S` / `B` 等を `n` へ置き換え、pathを消費する。暗黙に閉じる `s` / `b` / `b*` は `h n` とする。これにより、paintを消してもclipへの作用を保持する。原本、無変更でのfull-save、変更版をそれぞれ解釈し、次を要求する。

1. 無変更full-saveのpaint・scope列が原本と一致する。
2. 変更版は、想定したfill / stroke、またはfill＋strokeの連続bundleだけが欠ける。
3. 欠けた位置が一意で、残りのglyph・path・image・clip・group/layerが一致する。

同一paintが重複して一意性を証明できない場合は拒否する。対応不明のrenderer pathも `unlinked_path_paints` として残し、選択文字に重なる場合は無関係な固定物へ暗黙分類せず編集を拒否する。soft mask、shade、text clip、pattern等でfingerprintが未実装の場合も証明しない。image maskによるclipは画像sampleのhash・matrix・scissorを観測するが、移動先をそのclipが包含する証明には使わない。

現在のcounterfactualはページ全体のpaint列を比較するため、対象外に未対応paintがあってもpath proofが成立しない場合がある。対象scopeだけの完全なprovenanceを得るadapterへの課題であり、その部分を比較から除外して成功扱いにはしない。

これは実装した観測項目内の検査であり、任意のPDFに対する形式証明ではない。編集後のglyph・resource検査とMuPDF画像比較、実PDF評価のPoppler比較を併用する。注釈はpage contentと別に扱い、編集前後の領域にあるlink・annotation・widgetは移動しない。

### adapter選定

| 候補 | この段階の判断 |
|---|---|
| `get_drawings()` / `get_bboxlog()`のみ | 観測は有用だが、source operatorへの一意な対応は得られない |
| MuPDF `FzDevice2` | 解釈済みgeometry/state観測に採用。source書換え位置を返すAPIではない |
| MuPDF `PdfProcessor2` / filter processor | ローカルbindingでoperator callbackを確認した。ただしcallbackだけでは元decoded byte rangeが得られず、任意processorへの完全な転送・scope付き書換えの契約は未成立 |
| source lexer＋counterfactual | 今回の限定的なprovenance検証に採用。原本pathを個別にfull-save・再解釈するので大きな文書には高コスト |
| source位置付きのMuPDF processor adapter | 今後の有力候補。必要ならC/C++側でsource event ID、invocation、完全state、出力processorを接続する。上位のparagraph / font / ownershipモデルは保持できる |

PythonとPyMuPDFを守るための選択ではない。現在の局所編集にはsource命令を保持する経路が使える。Formの呼び出し単位の複製、途中でCTMが変わるpath、groupを含む再構築へ進む場合は、現在の限定writerを拡張し続けるよりprocessor adapterを置換する方が有望である。

## 元の精度を保持した要素移動

`move-element` は、選択された完全な `Tj` / `TJ` と、利用者が `fixed-to-element` としたpathを元のpaint位置で書き直す。文字コード・font resource・書式・描画順・path座標tokenを保持し、局所CTMの平行移動を加える。背景のサイズ変更、underlineの文字範囲への再付与はしない。

開発中の初版はrendererのpath座標からpathを再生成した。Microsoft Print to PDFの実例で、no-opは一致したが、180pt移動後のPoppler比較では上辺の414画素に最大32/255の濃度差が出た。文字と下線、移動元の除去は一致していたが、移動成功へ集計しなかった。

座標tokenの保持だけでは417画素の差が残った。ページ全体の平行移動を比較用に作ると元cropと一致したため、単なるrendererの平行移動特性ではない。局所移動量をfloat32の逆変換点同士の差で計算したことで、240が239.999992になる桁落ちを確認した。移動量は位置と独立したvectorとして、CTMの線形成分のみをPython float64で逆変換するよう修正した。許容差は広げていない。

あわせて、**renderer geometryは照合・包含判定に使い、source座標の再保存精度を決める根拠にはしない**方式へ修正した。元のpath構築命令をそのまま移動したCTM下で再実行する。構築途中に別のstate / paint命令が入るpathは、単一CTMで正しく戻せる保証がないため拒否する。PDF名による分岐はない。

実際の移動前にも、移動量ゼロでの再構築を保存し、全ページのMuPDF画素一致を要求する。保存後は計画したglyph origin / bbox / GID、元font resource graph、全paintとscope、ページ数・security、編集前後の領域外画素を検査する。診断用の除去PDFは、選択文字と所属pathだけを元命令から除去し、残存glyphとpaint・scopeを検査する。

新APIの座標と画像maskは非回転ページに限定する。回転ページは座標系を正規化するまで拒否する。元の領域内に未選択文字があるグループ、移動先での固定物との衝突、clipを越える配置、layer / transparency group、実Structure Treeに結び付く対象、特殊marked propertiesは拒否する。clipの存在だけでは拒否せず、対象paintに有効なclipを検査する。

## 固定背景内の書式付き編集

`edit-paragraph --element ... --relations ...` は既存の書式付き編集へ、明示的に固定された背景関係を渡す。defaultの障害物guardは変更していない。

複数の軸平行矩形subpathは、実際のnonzero / even-odd ruleに従ってcellへ分解できる。これにより大きなbboxを持つページ罫線の中を、塗られていないことが確かめられる場合だけ空間として扱える。曲線や一般polygonはunknownであり、bboxの内側を空白と見なさない。

背景の例外は、source対応が証明され、対象文字より前に描かれ、元の文字領域と新しい各inkを実形状が包含し、呼び出し側が `backgrounds` / `fixed-to-page` と明示したfillだけに限る。文字の後ろから描かれたpopup等を、単に背景と指定して通すことはできない。画像、未選択文字、link/annotationとの衝突検査も維持する。

この経路では `fixed-to-element` の装飾を伴うreflowを拒否する。文章が変わったときにunderlineや罫線のどこを伸ばすかは、glyph範囲へのanchorが必要な別問題である。

## 実PDF評価と再現

最新の実行結果は [公開集計](../evaluations/elements/summary.json) に保存する。原本の取得元とSHA-256は [sources.json](../evaluations/sources.json)、評価者が確定したglyph範囲・path・振る舞いは [evaluate.py](../evaluations/elements/evaluate.py) にある。

```powershell
.\.venv\Scripts\python.exe -m evaluations.elements.evaluate --run-name local_elements
```

| 原本・対象 | 確定した関係・編集 | 検査 |
|---|---|---|
| Microsoft Print to PDF、FCC資料の注記 | 252glyph、7 text operator、背景・複合罫線・3本の下線の5 path operatorを一式として180pt移動 | source再構築no-opをMuPDF/Popplerで確認後、元font・原コード・paint計画・除去・移動crop・領域外・移動間の空白を照合 |
| LibreOffice資料p2の書式付き見出し | ページ背景を固定し、本文部分を変更。元9glyphを保持し、16glyphをArialで追加 | 元の太字を保持、背景・図形・画像不変、元code/new CID・GID・W、pypdfの置換・除去、Poppler領域外を照合 |
| 同FCC資料の否定試験 | underlineの関係未確定、ページ外、固定footerとの衝突 | 保存前の安全拒否を成功編集と別に集計 |
| Word武雄・大阪、Ubiquiti資料 | active marked scopeとStructure Treeの読み取り | 編集成功へ加算しない |

実PDFの2つの人手選択に対する検証であり、一般業務文書の成功率を表す標本ではない。APIのMuPDF内検査だけでは `independent_renderer_verified` をtrueにしない。実PDFrunnerが別途Poppler 144dpi、別Pythonのpypdfを使って確認する。入力誤りや独立検証失敗を安全拒否へ集計しない。

2026-09-10の `elements_release` は **2成功・3安全拒否**。FCC注記では13個のsource path全ての対応が証明され、選択した5個を移動した。Popplerのno-op、移動crop、領域外、移動元と除去PDF、移動間の空白は全て**変更画素0**。LibreOfficeでは4個のpath全てを対応付け、背景を含む全図形・画像を維持した。両ケースのpypdfによる置換／除去と対象外ページのMuPDF画素比較も通過した。完成したPoppler画像を目視し、FCCの文字・3本の下線・外枠の相対配置と、LibreOfficeの太字保持・本文との非衝突を確認した。

LibreOfficeの追加glyphに使ったfontはArialMT（face 0、元font file SHA-256 `b3658eadae55e682b5f69eb64c439c1ecc8f196c0bb8d4756d145d13bc86476a`）。元の太字fontをArialへ一括変更してはいない。

## MCIDについて分かったこと

武雄資料にはMCID付きBDCがあるが、catalogに実Structure Treeがなく、対象ページのStructParentsもない。従来の履歴差による拒否を「有効なtagged構造を壊す」と断定する根拠はなかった。今回の観測は `orphan_mcid` と区別する。選択見出しのactive scope自体も複数に分かれており、履歴をactive stackへ替えるだけで一つの意味範囲と確定できるわけではない。

大阪・Ubiquiti資料は実Structure Treeを持つ。`tree_backed` はParentTree参照、MCID/page/Form namespace、構造要素の親とK backlinkの照合までを指す。これも「この背景はこの段落の所有物」という証明ではない。実構造のBBox等を更新するwriterがない段階で、その対象を移動させない。

この読み取り試験では、武雄p2は169 scopeが `orphan_mcid`、大阪通知p1は97 scope、Ubiquiti p1は1 scopeが `tree_backed` となった。active stackは全て閉じており、参照不整合はunknownへ分ける。共有Formは合成試験で2 invocationを別IDとして観測し、元streamの一括変更を拒否する。

## 回帰試験

全体試験は **335成功・2skip**。その後、回転ページの座標系guardを1試験追加し、影響する要素編集・provenance・marked contentの **24試験全て成功**を確認した。skipは既存AES試験のprovider不足による。新規試験は、重複paintの曖昧性、Contents境界をまたぐsource位置、共有Form、ParentTree backlink、clip内の移動とclip越えの拒否、矩形の穴とfill rule、未確定所属、固定物との衝突、完全でないグループ、後から描かれるfill、source精度、CLIの出力保護を含む。

旧実PDF経路は、Stage 1が13成功・2拒否、Stage 2が6成功・7拒否・2skip、Stage 3が13成功・2skip、Stage 4が2成功・1拒否・12skip。全文font代替は7成功・6拒否、書式付き部分編集は4成功・3拒否だった。成功件数と拒否理由を以前の集計と照合し、従来の境界を維持した。

再実行コマンドは次のとおり。旧公開集計は当時の結果として保持する。

```powershell
.\.venv\Scripts\python.exe -m evaluations.backend.stage1 --run-name elements_regression
.\.venv\Scripts\python.exe -m evaluations.backend.followup --stage1-run elements_regression --run-name elements_stages_regression
.\.venv\Scripts\python.exe -m evaluations.composition.evaluate --run-name elements_regression
.\.venv\Scripts\python.exe -m evaluations.attributed.evaluate --run-name elements_regression
```

## 次の穴とflowへの接続

現在は、**意味範囲と振る舞いが確認された文字＋path群を、元sourceへ戻せる限定的な編集単位**ができた。幅は従来通りobserved / inferred / explicitly suppliedを分離し、unknownを観測幅へ変換しない。

次の中心課題は、装飾・罫線のanchorをUnicode区間や段落へ結び付け、文章変更時の伸縮を定義することと、複数要素のflow関係を明示することである。`fixed-to-page` の日付・footer、要素に追従する背景、後続本文を区別し、明示関係ごとに移動候補を作る必要がある。今回のrigid moveはその候補をsourceへ適用する部品であり、flow plannerそのものではない。

現構成を維持するのはparagraph / font provider / width契約とsource-bound selection。置換候補は、source位置のないrenderer callbackを補う高コストのcounterfactual adapterと、単一CTMに限るpath writerである。Form共有、複雑なclip/group、tagged layout属性、改ページ・表全体の組版は未対応として残す。
