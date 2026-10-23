"""Freeze the tagged-PDF editing evaluation before it is implemented.

Real tagged PDFs (and one with marked content but no structure tree) whose
paragraphs B2 refused for marked-content or structure reasons in the PR #90
survey, or whose structure shapes it did not cover:

* prefilled_f1040.pdf (Adobe LiveCycle Designer ES 8.2): /P under /Div with an
  /IDTree; one structure element per drawn label;
* 160F-2019.pdf (Acrobat Distiller 5.0.5): table cells (/TD) that own their
  MCIDs directly; structure elements without /Type; one MCID per cell that
  may draw several visual paragraphs;
* word_osaka_fire_notice.pdf (Microsoft Word): /Span leaves with ActualText
  and /Lang under /P;
* issue14297.pdf (InDesign): /Figure MCIDs but no StructTreeRoot;
* 府政だより 477号 (InDesign): unmarked /Span ActualText marker sequences and
  inline placed objects inside paragraphs.

Each request records, from the source PDF only (main's paragraph analysis and
PyMuPDF), the paragraph text and box, the drawn lines, the region where
pixels may change, and the structure facts of the paragraph (MCIDs, owner
elements, roles, ActualText and Lang). The survey protocol compares, per
paragraph, whether B2 proposes an unchanged-text edit on main and on the
change, for the PR #90 population and an extended tagged population.

  .venv/bin/python -m prototypes.paragraph_frame.tagged_preflight --fusei <477.pdf> --pdfjs <dir> --out <manifest>
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import pymupdf

DOCS = {
    'prefilled_f1040': dict(producer='Adobe LiveCycle Designer ES 8.2', source='pdf.js test/pdfs'),
    '160F-2019': dict(producer='Acrobat Distiller 5.0.5 (Windows)', source='pdf.js test/pdfs'),
    'word_osaka_fire_notice': dict(producer='Microsoft Word for Microsoft 365', source='evaluations/realpdf/corpus'),
    'issue14297': dict(producer='Adobe InDesign (placed PDF figure)', source='pdf.js test/pdfs'),
    'osaka_fusei_477': dict(producer='Adobe PDF Library 16.0 / Adobe InDesign 16.4 (Windows)', source='PR #89 download'),
}

# id, document, page, paragraph start (unique prefix), old, new, after, expected, reason, purpose, grow
REQUESTS = [
    ('L-same', 'prefilled_f1040', 1, 'Unemployment compensation', 'compensation', 'remuneration', None, 'saved', None,
     'LiveCycle /P under /Div with an IDTree: same-length replacement', False),
    ('L-reedit', 'prefilled_f1040', 1, 'Unemployment remuneration', 'remuneration', 'compensation', 'L-same', 'saved', None,
     'PDF-alone re-edit: restore the original word', False),
    ('L-short', 'prefilled_f1040', 1, 'Head of household (with qualifying person)', '(See instructions.) If', 'If', None, 'saved', None,
     'shorten a four-line paragraph of one structure element', False),
    ('L-wrap', 'prefilled_f1040', 1, 'Make sure the SSN(s) above', 'are correct.', 'are correct and complete.', None, 'saved', None,
     'lengthen a two-line paragraph; the lines re-wrap', True),
    ('L-cross', 'prefilled_f1040', 1, '9 aOrdinary dividends', 'aOrdinary', 'a Ordinary', None, 'refused', 'edit crosses structure elements',
     'the selection spans two /P elements drawn on one line', False),
    ('D-same', '160F-2019', 1, 'montant net exonéré', 'exonéré', 'exempté', None, 'saved', None,
     'Distiller /TD owning its MCID (no /Type): same-length replacement', False),
    ('D-reedit', '160F-2019', 1, 'montant net exempté', 'exempté', 'exonéré', 'D-same', 'saved', None,
     'PDF-alone re-edit: restore the original word', False),
    ('D-short', '160F-2019', 1, 'cette transmission ne peut se faire', 'ne peut se faire que', 'se fait uniquement', None, 'saved', None,
     'shorten the text of a table cell', False),
    ('D-shared', '160F-2019', 1, "Certificat de salaire, de retenue", 'Certificat de salaire', 'Attestation de salaire', None, 'saved', None,
     'one MCID draws this and another visual paragraph: the MCID stays one sequence', False),
    ('W-span', 'word_osaka_fire_notice', 1, '直通：03-3595-2257', '2257', '2258', None, 'saved', None,
     'Word /Span leaf with ActualText and /Lang: same-length replacement inside one span', False),
    ('W-cross', 'word_osaka_fire_notice', 1, '直通：03-3595-2257', '03-3595', '06-6941', None, 'saved', None,
     'replacement across three /Span leaves; unchanged characters stay in their spans', False),
    ('W-reedit', 'word_osaka_fire_notice', 1, '直通：06-6941-2257', '06-6941', '03-3595', 'W-cross', 'saved', None,
     'PDF-alone re-edit across the spans: restore the original', False),
    ('W-grow', 'word_osaka_fire_notice', 1, 'なお、標記災害により', '円滑かつ適切に', '円滑かつ適切かつ迅速に', None, 'saved', None,
     'Word /P with one /Span: lengthen a two-line paragraph', True),
    ('I-same', 'issue14297', 1, 'Adjusted balance at beginning of year', 'beginning', 'inception', None, 'saved', None,
     'MCIDs without a structure tree (orphan /Figure) as a container: same-length replacement', False),
    ('I-short', 'issue14297', 1, 'Cumulative effects of changes', 'changes in accounting policies', 'policy changes', None, 'saved', None,
     'MCIDs without a structure tree: shorten', False),
    ('F-marker', 'osaka_fusei_477', 2, '診療履歴に基づいた', '良い医療', '良質な医療', None, 'saved', None,
     'InDesign paragraph with an unmarked /Span ActualText marker sequence: lengthen', False),
    ('F-icon', 'osaka_fusei_477', 2, '問 マイナンバー総合フリーダイヤル', 'フリーダイヤル', 'フリーダイアル', None, 'saved', None,
     'inline placed object (/PlacedPDF icon) at the line start: same-length replacement after it', False),
]

SURVEY = {
    'pr90_population': 'the 23 pages of the PR #90 survey (evaluations/paragraph_frame_indesign/manifest.json)',
    'extended_tagged': {'prefilled_f1040': [1, 2], 'issue16500': [1], 'issue6782': [1], 'issue11656': [1], 'zero_descent': [1],
                        'pr20043': [1], 'issue16553': [1], 'issue14627': [1], 'issue16263': [1], 'issue14130': [1],
                        'issue15262': [1], 'issue8702': [1, 2]},
    'protocol': ('Same as PR #90: every paragraph group of the page is proposed with an unchanged-text edit (shortest unique '
                 'prefix of at least 6 characters); main (2aefe3e) and the change are compared per paragraph. Refusal '
                 'reasons are classified, and the structure of each refused paragraph is recorded with tagged_diagnose.'),
    'outcome_tiers': 'proposed (survey), saved, safety audit, quality audit, PDF-alone re-edit; never merged into one count',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source(doc, args):
    if doc == 'osaka_fusei_477':
        return args.fusei
    if doc.startswith('word_'):
        return Path('evaluations/realpdf/corpus') / f'{doc}.pdf'
    return args.pdfjs / f'{doc}.pdf'


def drawn_lines(path, page, box):
    with pymupdf.open(path) as d:
        out = []
        for b in d[page - 1].get_text('dict')['blocks']:
            for line in b.get('lines', []):
                text = ''.join(s['text'] for s in line['spans'])
                x0, y0, x1, y1 = line['bbox']
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if text.strip() and box[0] - 1 <= cx <= box[2] + 1 and box[1] - 1 <= cy <= box[3] + 1:
                    out.append(dict(text=text, bbox=[round(v, 2) for v in line['bbox']], baseline=round(line['spans'][0]['origin'][1], 2)))
        return out


def structure(path, page, ids, c, marks):
    """MCIDs, owner elements and their semantics for the glyphs ``ids`` (source facts)."""
    chosen = set(ids)
    rows = []
    for s in marks['scopes']:
        lo, hi = s['content_byte_range']
        hits = {i for e in c.events if e.stream_xref == s['stream_xref'] and e.invocation == tuple(tuple(x) for x in s['invocation'])
                and lo <= e.operator.start < hi for ch in e.chars for i in ch.source_orders}
        if not hits & chosen:
            continue
        a = s['association']
        row = dict(tag=s['tag'], mcid=a.get('mcid'), status=a['status'], properties=sorted(s['properties']) if isinstance(s['properties'], dict) else [],
                   glyphs_in_paragraph=len(hits & chosen), glyphs=len(hits))
        if a.get('owner_reference'):
            owner = c.reader.get_object(a['owner_reference']['xref'])
            row.update(owner=a['owner_reference']['xref'], roles=[x['role'].get('mapped') for x in a['ancestors']][:4],
                       actual_text=str(owner['/ActualText']) if '/ActualText' in owner else None,
                       lang=str(owner['/Lang']) if '/Lang' in owner else None)
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fusei', type=Path, required=True)
    parser.add_argument('--pdfjs', type=Path, required=True)
    parser.add_argument('--out', type=Path, default=Path('evaluations/paragraph_frame_tagged/manifest.json'))
    args = parser.parse_args()
    from pdfeditor.content_stream import ContentPage
    from pdfeditor.marked_content import observe_marked_content
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import group_text, observe
    documents = {}
    for doc, info in DOCS.items():
        path = source(doc, args)
        documents[doc] = dict(info, sha256=sha(path), path='(scratch download, not committed)' if info['source'] != 'evaluations/realpdf/corpus' else str(path))
    requests = []
    cache = {}
    for rid, doc, page, start, old, new, after, expected, reason, purpose, grow in REQUESTS:
        path = source(doc, args)
        if after is None:
            if (doc, page) not in cache:
                marks = observe_marked_content(str(path), page)
                c = ContentPage(str(path), page)
                rows, _, groups = observe(c, marks, _Analysis(str(path), page))
                cache[(doc, page)] = (c, marks, groups)
            c, marks, groups = cache[(doc, page)]
            found = [(g, group_text(c, g)[0]) for g in groups]
            matches = [(g, t) for g, t in found if t.startswith(start)]
            if len(matches) != 1:
                raise SystemExit(f'{rid}: {len(matches)} paragraphs start with {start!r}')
            g, text = matches[0]
            if text.count(old) != 1:
                raise SystemExit(f'{rid}: selection occurs {text.count(old)} times in the paragraph')
            ids = [i for r in g for i in r['glyph_ids']]
            box = [min(r['bbox'][0] for r in g), min(r['bbox'][1] for r in g), max(r['bbox'][2] for r in g), max(r['bbox'][3] for r in g)]
            pitch = (g[1]['baseline'] - g[0]['baseline']) if len(g) > 1 else g[0]['size'] * 1.2
            region = [box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3 + (1.5 * pitch if grow else 0)]
            record = dict(paragraph=text, box=[round(v, 2) for v in box], lines=drawn_lines(path, page, box),
                          allowed_region=[round(v, 2) for v in region], structure=structure(path, page, ids, c, marks))
        else:
            prior = next(r for r in requests if r['id'] == after)
            text = prior['paragraph'].replace(prior['old'], prior['new'], 1)
            if not text.startswith(start) or text.count(old) != 1:
                raise SystemExit(f'{rid}: the re-edit selection does not follow from {after}')
            record = dict(paragraph=text, box=prior['box'], lines=None, allowed_region=prior['allowed_region'], structure=prior['structure'])
        requests.append(dict(id=rid, document=doc, page=page, old=old, new=new, after=after, expected_outcome=expected,
                             expected_reason=reason, purpose=purpose, expected_text=record['paragraph'].replace(old, new, 1), **record))
    for c, _, _ in cache.values():
        c.close()
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(schema='paragraph-frame-tagged-manifest-1', baseline_commit='2aefe3e (main after PR #90)', frozen_at=commit,
                    policy=('Frozen before implementation. Expected outcomes follow the page layout and structure, not the engine. '
                            'A request whose outcome differs is reported as such; refusals never count as success.'),
                    documents=documents, requests=requests, survey=SURVEY)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(len(requests), 'requests')


if __name__ == '__main__':
    main()
