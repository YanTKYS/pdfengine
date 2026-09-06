# ライセンス方針

Copyright (C) 2026 YanTKYS

pdfengineの独自コードは、**GNU Affero General Public License version 3のみ（SPDX: `AGPL-3.0-only`）**で公開します。ライセンス全文はルートの[LICENSE](../LICENSE)に収録しています。

現在の実装はPyMuPDF/MuPDFに依存しており、これらもAGPLまたは商用ライセンスで提供されています。将来、許諾型ライセンスのbackendへ置換した場合は、プロジェクトのライセンス方針を再検討する可能性があります。

依存ライブラリ、評価に使用した外部PDF、埋め込みフォントには、それぞれの条件が引き続き適用されます。プロジェクトのライセンス表示によって、第三者の著作物の条件を変更するものではありません。

`LICENSE`は[GNU公式のAGPL v3全文](https://www.gnu.org/licenses/agpl-3.0.txt)を変更せず収録しています。適用する版は`AGPL-3.0-only`であり、後続版を自動的に含める指定はしていません。

## 評価環境で確認した依存先

下表はローカルのdist-infoと同梱ライセンスを確認した版です。`pyproject.toml`ではpypdfのみ完全固定で、ほかは許容バージョン範囲を指定しています。以下は主な実行時依存の整理であり、推移的依存・同梱資産の全一覧ではありません。

| ライブラリ・確認版 | 同梱表示 | 一次資料 |
|---|---|---|
| PyMuPDF 1.27.2.3 | GNU AGPL 3.0 または Artifex Commercial License | [PyMuPDF公式](https://pymupdf.readthedocs.io/en/latest/faq/index.html)、[MuPDF公式](https://mupdf.readthedocs.io/en/latest/license.html) |
| pypdf 6.10.0 | BSD-3-Clause | [当該版LICENSE](https://github.com/py-pdf/pypdf/blob/6.10.0/LICENSE) |
| fontTools 4.64.0 | MIT | [公式文書](https://fonttools.readthedocs.io/en/stable/) |
| uniseg 0.10.1 | MIT | [公式ライセンス](https://uniseg-py.readthedocs.io/en/stable/license.html) |

fontToolsの`LICENSE.external`には、テスト用フォント等に対するSIL Open Font Licenseの記載もあります。ライブラリ本体の表示だけで、同梱データの条件を置き換えません。

## 現在のbackendと将来の置換

現在の構成はPyMuPDF/MuPDFを使用します。独自コードのAGPL公開とあわせて、依存先の表示や適用条件も保持します。PyMuPDF/MuPDFにはArtifexによる商用ライセンスの選択肢もありますが、このプロジェクト自体に商用ライセンスを付与するという意味ではありません。[Artifexのライセンス方針](https://artifex.com/licensing)

`backend.py`への分離、別プロセス化、別言語への移植という設計変更だけで、依存先の条件がなくなるとは判断しません。一方、将来PyMuPDF/MuPDFへの依存を独立したbackendへ置換した場合は、その時点のコードの由来・依存先・配布形態をもとに新構成を評価できます。現行構成や過去に配布した版の扱いとは分けて考えます。

## 外部PDFとフォント

評価用に取得した外部PDFと、その中のフォントprogramは独自コードではありません。公開URLから取得できることや、技術的に抽出・再埋め込みできることは、再配布や用途変更の許諾を意味しません。リポジトリを公開する際は、コードの公開対象と評価資料の公開対象を分け、取得元の条件を確認します。

本書は実装上の選択を整理したものです。実際に配布・サービス提供する際の適用範囲は、その構成をもとに法務または権利者へ確認します。
