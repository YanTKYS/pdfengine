# 輪郭を根拠にした文字移動の許可

明示したrelationが成立した後の直接の障害として、移動groupのfont bboxと未選択文字のfont bboxの重なりを選んだ。前段階のFCC実PDFでは、Aの短文化後にBを確認済みbaseline gapへ戻す際、約0.443ptのfont bbox重なりだけで拒否された。固定領域には収まり、共有背景も伸縮する必要がなかった。最終位置自体が拒否理由だったため、逐次writerの順番を変えるだけでは解決しない。

| 仮説 | 今回の判断 |
|---|---|
| font bboxとglyph inkの違い | 実測した拒否を直接説明する。今回の中心課題 |
| container・装飾の可変寸法 | 別の重要な問題だが、この配置には不要。paddingやresize policyを追加しない |
| 逐次状態だけの衝突 | 今回の反例ではない。一般solverやcompound mutationの導入は見送る |
| provenanceの性能 | 継続的な制約だが、今回backend交換を必要とする精度問題はない |

## 事前certificate

`pdfeditor.ink_collision`は、元PDFの描画状態とglyph形状を読む独立した幾何層である。文章の所有者や後続関係を推定せず、与えられた文字集合の平行移動が他の文字から離れていることだけを証明する。

1. MuPDFのtext paint callbackから実際のfont instance、GID、文字原点、text matrix、CTM、描画状態を得る。通常のpaint観測と全eventを比較する。
2. rendererが使用したfont bytesのSHA-256が、そのページの埋込resourceから取り出したfontと一致することを要求する。同じfont名だけでは許可しない。
3. static TrueTypeのglyph輪郭をMuPDFとfontToolsで読み、変換後のBezier制御点を囲む矩形の和を取る。曲線を横切らないという近似判定ではなく、輪郭全体を含む上界を使う。
4. 元fontに静的な1bit bitmap strikeがあれば、全strikeのglyph metricsから求めた描画矩形も含める。bitmap pixelsの見た目を判定根拠にしない。
5. 各包絡を0.501pt拡張し、選択文字と未選択文字の全組合せについて、包絡が厳密に離れていることを要求する。接触・重なり・不明は許可しない。
6. PDF SHA、変位、outline証拠hash、比較数と最小余白を、保存前に作ったcertificateとして移動reportへ残す。

これは輪郭の交差を完全判定するsolverではない。文字内の穴、斜線間の空隙、複数曲線の入り組んだ非接触を包絡矩形が表せない場合は拒否が残る。font bboxを全処理で実ink bboxへ置換する変更でもない。

0.501ptは各形状につける物理的なclearance方針であり、144dpiでの1pixel相当と数値上の余裕を持つ。任意の低解像度表示や異なるhintingに対する画素非接触まで保証しない。保存後のMuPDF・Poppler比較は、この事前幾何判定とは別の確認である。

MuPDFの[font実装](https://github.com/ArtifexSoftware/mupdf/blob/1.27.2/source/fitz/font.c)を確認し、outline取得と通常のraster描画が別経路であることを前提にした。FreeType由来の輪郭だけに依存せず、埋込programの輪郭・bitmap metricsを合わせて囲む。synthetic bold/italicや代替fontの許可は今回増やしていない。

## 維持したguard

新しい許可は、通常のgroup bbox検査が文字との衝突を報告した**文字だけのrigid move**で使用する。ページ全体のtext-to-outline対応が一意に成立しなければ、その許可は出さない。遠い文字であっても未知のfontや描画状態があると、現段階ではrefinementが不成立になる。

- 対応するのは横書き・不透明fill・静的な埋込TrueType。stroke、fill+stroke、透明文字、Type3、代替・合成・可変・color font、未知のbitmap、特殊group/layer等はunknownとする。
- clippingでglyphが見えなくなっていても、衝突証明ではclipping前の全形状を囲む。移動先のclip保持検査も継続する。
- bitmapの非対応形式や輪郭読取失敗を、空glyphとして扱わない。Unicode空白でも実glyphに形があれば障害物になる。
- vector/image/annotationへの衝突、元text operatorの完全選択、source/paint provenance、固定背景の順序と包含、対象外glyphとfont資源、no-op、保存後画素のguardは維持する。
- owned paintを伴う移動groupには新しい文字だけのcertificateを適用しない。文字形状が離れていても、背景が別の文字を覆うことは許可しない。

## Relation・計画・保存との接続

`follows`は従来どおり先行paragraphの最終baselineと後続の先頭baselineを結ぶ契約である。collision policyがgapを勝手に広げたり、containerを伸ばしたりすることはない。利用可能幅・bounds・所有関係も変更しない。

document層が関係から変位を計画し、移動backendがその変位について保存前にcertificateを作り、通常のoperator mutationと検証を実行する。各revisionで再証明するため、sidecarに保存された関係は衝突許可の代わりにはならない。

今回は逐次writerのまま対象の配置が成立した。最終配置が安全でも途中状態だけが衝突する例、複数paragraphを同時に変更する例に対しては、別のplanning/transaction層が必要になり得る。今回のcertificateは、その層で使用できる形状根拠であり、同時配置を実装したものではない。

## 付随するsource offset修正

新しいテストで、改行で終わる複数の`Contents`を連結すると、pypdfの実際の合成bytesと従来の想定offsetが一致しない場合を確認した。物理streamから合成streamへの対応は、save adapter自身が各componentへ付ける区切りを取得する方式へ変更した。元bytesが変形されていないことと最終programの一致検査は残す。PDF名やProducerによる分岐はない。

## 評価

再現手順と集計は[`evaluations/ink_collision`](../evaluations/ink_collision/README.md)。原本・選択・幅・共有背景・followsを前段階から引き継ぎ、原本operator no-opの既存証跡を再利用する。変更したFCCの境界を再評価し、未変更の高コストな外部PDF corpusを一律再実行しない。

FCCの仮想プリンタ由来PDFで、A長文化 → A短文化 → B全文削除 → 空Bを伴うA長文化 → A全文削除 → A再入力 → B再入力 → A no-opの8段階が成功した。A/B両方が空になった後も、ID、follows、固定コンテナを保持して再編集できた。g1のPDF bytesは前段階の成功出力と同一であり、今回の新しい許可が必要だったのはg2の短文化である。

g2では32文字のBを35.521198pt上へ戻した。従来のbbox検査では15文字が衝突候補だったが、12,771組のglyph包絡を比較し、各包絡に0.501ptを加えた後も軸方向の最小分離余裕1.354450ptを確認できた。前述の0.443ptはfont bboxの重なりであり、実inkの接触を示していなかった。変更文字は明示したArial full font、未変更のBは元font resource/code/GIDを保持する。

| 検証 | 結果 |
|---|---|
| 全8段階のUnicode・font・固定paint・画像 | 成功。独立pypdf抽出とcode/GID/width照合を含む |
| 全8段階のPoppler 144dpi領域外差分 | 0 pixel（既存の1pt audit margin） |
| 全8段階のMuPDF領域外画素 | 一致。原本は1ページのため対象外ページ比較は空集合 |
| 最終生成paragraphのno-op | MuPDF・Popplerとも対象ページ全画素一致 |
| 関係なし・領域overflow・外部保存 | すべて安全に拒否、PDF/sidecar未作成 |
| g2からさらに2pt上げる／Aと同じbaselineへ移す | 余白付き包絡の非接触を証明できず拒否、出力未作成 |

短文化後と最終再入力後のPopplerページ画像を目視確認し、黄色い共有背景、フランス語の下線、図面、ロゴ、下部の表が保持されていることを確認した。画像比較の成功から描画範囲や所有関係を逆に推定したものではない。

最終レビューで、certificateのPDF SHAをmutationのsnapshot SHAと明示比較するguardを追加した。影響するg2だけを最終coreで再実行し、8段階実行時のg2とPDF bytesが一致すること、再open、独立renderer検証を確認した。その他の段階は既存実行を再利用し、実行coreのhashを区別して記録した。

最終回帰は **417 passed / 2 skipped（218.26秒）**。skipは前段階と同じ任意AES provider不足による保存テスト2件。新しいテストには実接触、clearance不足、stroke/透明文字、曖昧な対応、未知の輪郭、Unicode空白に可視glyphを割り当てた例、clip越え、bitmap metrics、変換後の座標、固定vector/owned paintへの誤適用防止、別revisionの証明拒否を含む。変換座標の具体的な期待値を追加したテストも個別に再確認した。

次の構造課題は、文字と装飾を含む可変寸法の所有境界と、複数要素の最終配置をまとめて検証する契約である。包絡だけでは解決しない拒否と、未知の描画状態による拒否も区別して残す。
