"""Generated multi-column documents exported by LibreOffice's PDF writer.

The reachable real corpus has no same-page column continuation, so these
documents provide genuinely threaded columns: a Writer section with two
unbalanced (newspaper) columns on a short page. LibreOffice itself fills
column 1 to the page bottom and continues the same paragraph in column 2,
which stays partly empty. Variants break the columns at a paragraph boundary
or start column 2 with another article's heading or a drawing. They are generated evidence and are reported separately from
real PDFs. Output depends on the LibreOffice version, which is recorded.

  .venv/bin/python -m prototypes.paragraph_frame.column_fixtures --out tmp/column-fixtures
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from xml.sax.saxutils import escape

NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
      'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
      'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
      'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
      'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"')

JA = dict(
    language='ja', country='JP', font='IPAGothic', size='10.5pt', align='justify', indent='1em',
    title='地域防災訓練の実施について',
    body=[
        '本市では、大規模な地震や風水害に備えるため、毎年秋に地域防災訓練を実施しています。今年度は市内の全小学校区で同じ日に訓練を行い、避難所の開設から運営までを住民の皆さんと一緒に確認します。',
        '訓練当日は、午前九時に防災行政無線で訓練開始をお知らせします。参加される方は、各地域で指定された一時避難場所に集合してください。集合後は自主防災組織の班長の案内に従い、安否確認や初期消火、応急手当の訓練に順番に参加していただきます。避難所では、受付の手順や間仕切りの設置、備蓄物資の配布方法を実際に体験できます。',
        '小さなお子さんや高齢の方、障がいのある方も安心して参加できるよう、各会場に相談窓口を設けます。車いすの貸出しや手話通訳が必要な方は、事前に危機管理課までご連絡ください。',
        '雨天の場合は内容を一部変更して実施します。中止する場合は、前日の午後五時までに市のホームページと防災メールでお知らせします。',
        '訓練の終了後には、参加者アンケートを実施します。いただいたご意見は、来年度の訓練計画や地域ごとの避難所運営マニュアルの見直しに活用します。回答は会場の受付のほか、市のホームページからも行えます。',
        '各家庭でも、非常持出品の点検や家族との連絡方法の確認をお願いします。飲料水や食料は最低三日分、できれば一週間分を目安に備蓄し、定期的に入れ替えるようにしてください。',
        'また、自宅周辺の危険な場所や避難経路を地図で確認しておくと、いざというときに落ち着いて行動できます。ハザードマップは市役所の窓口と市のホームページで入手できます。',
    ])

# Paragraph 5 ends exactly at the bottom of column 1: the column break falls
# on a paragraph boundary, so untagged geometry alone cannot prove a thread.
_BOUNDARY = JA['body'][:4] + [JA['body'][4].replace('回答は会場の受付のほか、市のホームページからも行えます。', '回答は会場の受付で行えます。')]
JA_BOUNDARY = dict(JA, body=_BOUNDARY + JA['body'][5:])
# Column 2 holds another article (heading first) or starts with a drawing.
JA_HEADING = dict(JA, body=_BOUNDARY + [('heading', '図書館からのお知らせ')] + JA['body'][5:])
JA_IMAGE = dict(JA, body=_BOUNDARY + [('rect', ('7.5cm', '2.6cm'))] + JA['body'][5:])

EN = dict(
    language='en', country='US', font='Liberation Serif', size='10.5pt', align='justify', indent='1.2em',
    title='Community Library Renovation',
    body=[
        'The central library will close for renovation from the first of June. During the closure, the mobile library will visit each district twice a week, and returns can be made at any branch.',
        'The renovation adds a quiet study room, a larger children\'s corner and a meeting space that local groups can book free of charge. New shelving will make the collection easier to browse, and the entrance will be rebuilt so that wheelchair users and families with prams can enter without steps. Wireless access will also be improved on every floor.',
        'Library cards remain valid throughout the work. Holds placed before the closure can be collected at the branch of your choice, and the online catalogue stays available at all times.',
        'The library expects to reopen in the autumn. Updates will be posted on the website and at every branch.',
        'Volunteers who would like to help with the reopening events are welcome to register at any branch desk or through the online form before the end of August.',
    ])


def _paragraph(item):
    if isinstance(item, str):
        return f'<text:p text:style-name="Body">{escape(item)}</text:p>'
    kind, value = item
    if kind == 'heading':  # a different article starts at the top of the next column
        return f'<text:p text:style-name="Heading">{escape(value)}</text:p>'
    if kind == 'rect':  # a filled drawing at the top of the next column
        return ('<text:p text:style-name="Break"><draw:rect draw:style-name="gr1" text:anchor-type="as-char" '
                f'svg:width="{value[0]}" svg:height="{value[1]}"/></text:p>')
    raise ValueError(kind)


def fodt(doc):
    paragraphs = ''.join(_paragraph(p) for p in doc['body'])
    face = escape(doc['font'])
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<office:document {NS} office:version="1.3" office:mimetype="application/vnd.oasis.opendocument.text">
<office:font-face-decls><style:font-face style:name="{face}" svg:font-family="'{face}'"/></office:font-face-decls>
<office:styles>
 <style:default-style style:family="paragraph"><style:paragraph-properties fo:hyphenation-ladder-count="no-limit"/>
  <style:text-properties style:font-name="{face}" style:font-name-asian="{face}" fo:font-size="{doc['size']}" style:font-size-asian="{doc['size']}"
   fo:language="{doc['language']}" fo:country="{doc['country']}" style:language-asian="ja" style:country-asian="JP" fo:hyphenate="false"/></style:default-style>
</office:styles>
<office:automatic-styles>
 <style:style style:name="Body" style:family="paragraph"><style:paragraph-properties fo:text-align="{doc['align']}" fo:text-indent="{doc['indent']}" fo:margin-top="0cm" fo:margin-bottom="0.15cm"/></style:style>
 <style:style style:name="Title" style:family="paragraph"><style:paragraph-properties fo:margin-bottom="0.4cm"/><style:text-properties fo:font-size="16pt" style:font-size-asian="16pt" fo:font-weight="bold" style:font-weight-asian="bold"/></style:style>
 <style:style style:name="Heading" style:family="paragraph"><style:paragraph-properties fo:break-before="column" fo:margin-bottom="0.2cm"/><style:text-properties fo:font-size="13pt" style:font-size-asian="13pt" fo:font-weight="bold" style:font-weight-asian="bold"/></style:style>
 <style:style style:name="Break" style:family="paragraph"><style:paragraph-properties fo:break-before="column" fo:margin-bottom="0.2cm"/></style:style>
 <style:style style:name="gr1" style:family="graphic"><style:graphic-properties draw:fill="solid" draw:fill-color="#9fb8d0" draw:stroke="none"/></style:style>
 <style:style style:name="Sect1" style:family="section"><style:section-properties text:dont-balance-text-columns="true"><style:columns fo:column-count="2" fo:column-gap="0.9cm"/></style:section-properties></style:style>
 <style:page-layout style:name="pm1"><style:page-layout-properties fo:page-width="21cm" fo:page-height="15cm" fo:margin-top="2cm" fo:margin-bottom="2cm" fo:margin-left="2.2cm" fo:margin-right="2.2cm"/></style:page-layout>
</office:automatic-styles>
<office:master-styles><style:master-page style:name="Standard" style:page-layout-name="pm1"/></office:master-styles>
<office:body><office:text>
<text:p text:style-name="Title">{escape(doc['title'])}</text:p>
<text:section text:style-name="Sect1" text:name="Columns">{paragraphs}</text:section>
</office:text></office:body></office:document>
'''


def export(doc, out, *, tagged):
    out.parent.mkdir(parents=True, exist_ok=True)
    options = json.dumps({'UseTaggedPDF': {'type': 'boolean', 'value': 'true' if tagged else 'false'}})
    with tempfile.TemporaryDirectory() as work:
        source = Path(work) / (out.stem + '.fodt')
        source.write_text(fodt(doc), encoding='utf-8')
        subprocess.run(['soffice', '--headless', f'-env:UserInstallation=file://{work}/profile', '--convert-to',
                        f'pdf:writer_pdf_Export:{options}', '--outdir', work, str(source)], check=True, capture_output=True)
        (Path(work) / (out.stem + '.pdf')).replace(out)
    return hashlib.sha256(out.read_bytes()).hexdigest()


DOCUMENTS = {'gen_columns_ja': (JA, False), 'gen_columns_ja_tagged': (JA, True),
             'gen_columns_en': (EN, False), 'gen_columns_en_tagged': (EN, True),
             'gen_columns_ja_boundary': (JA_BOUNDARY, False), 'gen_columns_ja_boundary_tagged': (JA_BOUNDARY, True),
             'gen_columns_ja_heading': (JA_HEADING, False), 'gen_columns_ja_image': (JA_IMAGE, False)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    version = subprocess.run(['soffice', '--version'], capture_output=True, text=True).stdout.strip()
    result = {name: dict(path=str(args.out / f'{name}.pdf'), sha256=export(doc, args.out / f'{name}.pdf', tagged=tagged), tagged=tagged)
              for name, (doc, tagged) in DOCUMENTS.items()}
    print(json.dumps(dict(libreoffice=version, documents=result), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
