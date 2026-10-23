"""Freeze the InDesign-style text support evaluation before it is evaluated.

Real PDFs whose text B2 refused because of a non-identity text matrix or CTM,
or because the text lies in a Form XObject:

* 府政だより 477号 p7 (Adobe InDesign 16.4): ``1 Tf`` with ``Tm 9.567 0 0 9.567``;
  the page header is drawn in a page-local Form XObject;
* tracemonkey.pdf p2 (pdfTeX paper with a Quartz figure): figure labels in a
  Form XObject, ``Tm 12 0 0 -12`` under ``cm 0.431 0 0 -0.431``;
* issue17730.pdf p1 (Antenna House Formatter): CTM and text matrix scaling;
* issue7014.pdf p1 (Quartz PDFContext, TeX): scaled text matrix with ligatures.

For each request the manifest records the drawn lines of the paragraph
(PyMuPDF, independent of the engine), the expected text, the expected outcome
from the page layout, and the region where pixels may change. A survey
protocol counts, per paragraph of every surveyed page, whether B2 proposes an
unchanged-text edit, before (main) and after the change.

  .venv/bin/python -m prototypes.paragraph_frame.indesign_preflight --fusei <477.pdf> --pdfjs <dir> --out <manifest>
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import pymupdf

PDFJS = 'https://raw.githubusercontent.com/mozilla/pdf.js/master/test/pdfs/'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def lines(path, page):
    with pymupdf.open(path) as d:
        result = []
        for b in d[page - 1].get_text('dict')['blocks']:
            for line in b.get('lines', []):
                text = ''.join(s['text'] for s in line['spans'])
                if text.strip():
                    result.append(dict(text=text, bbox=[round(v, 2) for v in line['bbox']], baseline=round(line['spans'][0]['origin'][1], 2),
                                       size=round(line['spans'][0]['size'], 2)))
        return result


def paragraph(path, page, first, last, *, right=None, below=0.):
    """Drawn lines from the line starting with ``first`` to the line containing ``last`` (same column)."""
    drawn = lines(path, page)
    start = [l for l in drawn if l['text'].startswith(first)]
    if len(start) != 1:
        raise SystemExit(f'{first!r}: {len(start)} start lines')
    x0 = start[0]['bbox'][0]
    column = sorted((l for l in drawn if abs(l['bbox'][0] - x0) < 8 and l['baseline'] >= start[0]['baseline']), key=lambda l: l['baseline'])
    chosen = []
    for l in column:
        chosen.append(l)
        if last in l['text']:
            break
    else:
        raise SystemExit(f'{last!r} not found below {first!r}')
    x1 = right if right is not None else max(l['bbox'][2] for l in chosen)
    region = [round(min(l['bbox'][0] for l in chosen) - 2, 2), round(min(l['bbox'][1] for l in chosen) - 2, 2), round(x1 + 2, 2),
              round(max(l['bbox'][3] for l in chosen) + 2 + below, 2)]
    return dict(lines=[{k: l[k] for k in ('text', 'baseline', 'bbox')} for l in chosen], allowed_region=region)


def request(ident, document, page, old, new, outcome, purpose, para, *, reason=None, kind='paragraph', after=None):
    return dict(id=ident, document=document, page=page, old=old, new=new, expected_outcome=outcome, expected_reason=reason,
                purpose=purpose, kind=kind, after=after, **para)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fusei', type=Path, required=True)
    parser.add_argument('--pdfjs', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    fusei = args.fusei
    pdfjs = {n: args.pdfjs / n for n in ('tracemonkey.pdf', 'issue17730.pdf', 'issue7014.pdf', 'issue14117.pdf', 'issue14297.pdf',
                                         '160F-2019.pdf', 'canvas.pdf')}
    documents = dict(
        osaka_fusei_477=dict(path='(scratch download, not committed)', sha256=sha(fusei), source_url='https://www.pref.osaka.lg.jp/documents/63847/477.pdf',
                             producer='Adobe PDF Library 16.0 / Adobe InDesign 16.4 (Windows)', pages=[7],
                             structure='1 Tf with Tm 9.567 0 0 9.567 on the page; page header in a page-local Form XObject (/Fm9)'),
        **{n.removesuffix('.pdf'): dict(path='(scratch download, not committed)', sha256=sha(p), source_url=PDFJS + n,
                                        producer=pymupdf.open(p).metadata.get('producer'), corpus='mozilla/pdf.js test/pdfs (real-world documents)')
           for n, p in pdfjs.items()})
    documents['tracemonkey']['structure'] = 'figure on p2 is a Form XObject (/Im1): Tm 12 0 0 -12 under cm 0.431 0 0 -0.431 (reflections cancel)'
    documents['issue17730']['structure'] = 'Antenna House: CTM scale with scaled Tm'
    documents['issue7014']['structure'] = 'Quartz PDFContext: 1 Tf with scaled Tm; TeX ligatures'
    f = lambda first, last, **kw: paragraph(fusei, 7, first, last, **kw)
    t = lambda first, last, **kw: paragraph(pdfjs['tracemonkey.pdf'], 2, first, last, **kw)
    a = lambda first, last, **kw: paragraph(pdfjs['issue17730.pdf'], 1, first, last, **kw)
    q = lambda first, last, **kw: paragraph(pdfjs['issue7014.pdf'], 1, first, last, **kw)
    requests = [
        request('F-same', 'osaka_fusei_477', 7, '悩みを抱えている人は', '悩みを持っている人は', 'saved', 'InDesign scaled Tm: same-length replacement',
                f('令和5年の大阪府の自殺者数は', 'を聴いて寄り添いましょう。')),
        request('F-same-reedit', 'osaka_fusei_477', 7, '悩みを持っている人は', '悩みを抱えている人は', 'saved', 'PDF-alone re-edit: restore the original text',
                f('令和5年の大阪府の自殺者数は', 'を聴いて寄り添いましょう。'), after='F-same'),
        request('F-short', 'osaka_fusei_477', 7, '損耗などの補修や修繕の費用を', '修繕の費用を', 'saved', 'InDesign scaled Tm: shorten',
                f('賃貸住宅の退去の際に', 'ぜひご活用ください。')),
        request('F-wrap', 'osaka_fusei_477', 7, '深刻な状況です。', '深刻な状況が続いています。', 'saved',
                'InDesign scaled Tm: longer text re-wraps the following lines (the last line has room)',
                f('令和5年の大阪府の自殺者数は', 'を聴いて寄り添いましょう。')),
        request('F-grow-line', 'osaka_fusei_477', 7, '原状回復をめぐるトラブルが発生しています。', '原状回復をめぐるトラブルが発生しています。退去の前に契約書の内容を確認してください。',
                'refused', 'growth needs a new line, but the label box below the paragraph is protected', f('賃貸住宅の退去の際に', 'ぜひご活用ください。'),
                reason='capacity'),
        request('F-form', 'osaka_fusei_477', 7, '3月1日', '3月7日', 'saved', 'text in the page-local Form XObject /Fm9 (page header)',
                f('令和7年（2025年）3月1日', '令和7年（2025年）3月1日'), kind='form'),
        request('T-form', 'tracemonkey', 2, 'Bytecodes', 'Bytecode', 'saved', 'figure label in Form XObject /Im1 under reflected Tm and CTM',
                t('Bytecodes', 'Bytecodes'), kind='form'),
        request('T-form-reedit', 'tracemonkey', 2, 'Bytecode', 'Bytecodes', 'saved', 'PDF-alone re-edit of the Form text', t('Bytecode', 'Bytecode'),
                kind='form', after='T-form'),
        request('A-same', 'issue17730', 1, 'two types of links', 'two kinds of links', 'saved', 'Antenna House CTM+Tm: same-length replacement',
                a('There are two types of links', 'or a website.', right=533.7)),
        request('A-short', 'issue17730', 1, 'Its destination may be either an external file or a website.', 'Its destination may be a file or a website.', 'saved',
                'Antenna House: shorten', a('There are two types of links', 'or a website.', right=533.7)),
        request('A-grow-line', 'issue17730', 1, 'or a website.', 'or a website. The link may point to a page number in the same document.', 'saved',
                'Antenna House: growth adds a line above the next heading', a('There are two types of links', 'or a website.', right=533.7, below=14.0)),
        request('A-reedit', 'issue17730', 1, 'two kinds of links', 'two types of links', 'saved', 'PDF-alone re-edit after A-same',
                a('There are two types of links', 'or a website.', right=533.7),
                after='A-same'),
        request('Q-ligature', 'issue7014', 1, 'to emit generic code', 'to emit general code', 'refused',
                'Quartz scaled Tm, but the paragraph contains TeX ligatures (fi) whose provenance is one glyph for two characters',
                q('Dynamic languages such as JavaScript are more', 'that identi'), reason='ligature'),
    ]
    survey = dict(
        pages={'osaka_fusei_477': list(range(1, 9)), 'tracemonkey': [1, 2], 'issue17730': [1, 2], 'issue7014': [1], 'issue14117': [1],
               'issue14297': [1], '160F-2019': [1], 'canvas': [1]},
        control_corpus=['word_osaka_guideline:1', 'word_osaka_fire_notice:1', 'word_osaka_symposium:1', 'lo_newfeatures_ja:1', 'lo_enterprise_en:2',
                        'lo_migration_ja:1'],
        protocol=('Every paragraph group of the page (prototypes.paragraph_frame.auto_detect.observe) is proposed with an unchanged-text edit: '
                  'the selection is the shortest prefix of the paragraph text (at least 6 characters) that occurs once on the page; '
                  'the replacement equals the selection. Outcome: proposed, or the refusal reason class. The same protocol runs on main '
                  '(f17055d) and on the change; newly proposable groups are then saved and audited on a fixed sample.'),
        reason_classes=dict(transform='text matrix/CTM', form='Form XObject', encoding='ToUnicode, ligature or encoding',
                            glyphs='missing source glyph', layout='capacity, paragraph or semantics', other='other'))
    manifest = dict(schema='paragraph-frame-indesign-1', baseline_commit=subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip(),
                    policy=dict(frozen_before_implementation=True, no_case_replacement=True,
                                inputs='PDF path, page, selected text, replacement. No oracle geometry, no manual coordinates.',
                                success='saved alone is not success: safety (raster outside the allowed region unchanged in MuPDF and Poppler, protected glyphs, paint, fonts, Form objects), quality (text, extraction, search, style, metrics) and PDF-alone re-edit are judged separately',
                                generated='generated PDFs (shared and nested Form XObjects) are tests only; they are not counted as real-PDF success',
                                network='pref.osaka.lg.jp now answers with a WAF error page (HTTP 408) and most hosts are denied by the session policy (HTTP 403); real PDFs come from the PR #89 download and the mozilla/pdf.js test corpus on raw.githubusercontent.com'),
                    documents=documents, requests=requests, survey=survey)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(args.out, len(requests), 'requests')


if __name__ == '__main__':
    main()
