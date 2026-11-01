"""Search and copy of edited text in real PDFs, measured apart (issue #114).

  .venv/bin/python -m prototypes.paragraph_frame.search_copy_evaluate --phase freeze --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir>
  .venv/bin/python -m prototypes.paragraph_frame.search_copy_evaluate --phase run --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir>
  .venv/bin/python -m prototypes.paragraph_frame.search_copy_evaluate --phase collect --root <dir>

``freeze`` (before the measures are written) fixes the cases: the three
lengthening edits of PR #113 (L2, L6, L8: saved on approving their panel
extension; search unverified) and the saved first steps of the smoke set
(evaluations/smoke) on other pages and writers, from one-line replacements to
replacements broken over two or three lines, with punctuation, Latin and
CJK, half and full width. Each case keeps its input digest, page, edit, the
judgement recorded so far (saved, the existing quality and its search check,
practical success, re-edit, restore), and the lines its saved output draws
(text, baseline, start and end x), so a run can tell a changed output from
a changed measure.

``run`` saves each edit again with the B2 of this tree, approving exactly the
candidates its proposal lists (panel_evaluate.chain_step), and records the
measures of search_copy beside the existing quality (approved_audit). The
measures are reported, not turned into practical success: which of them
practical success requires is a design decision (issue #114).

Generated fixtures (tests/test_search_copy.py) hold the negative cases; they
are never counted as real-PDF results.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pymupdf

from . import chain_evaluate as ce
from . import panel_evaluate
from . import rows_evaluate as rows
from .fidelity_evaluate import source_of

MANIFEST = Path('evaluations/search_copy/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
# (id, group, from (manifest, case)). target: the issue's three; extra: the smoke set's saved first steps.
CASES = [('L2', 'target', 'paragraph_panel', 'L2'), ('L6', 'target', 'paragraph_panel', 'L6'), ('L8', 'target', 'paragraph_panel', 'L8'),
         ('S-W2', 'extra', 'smoke', 'W2'), ('S-L1', 'extra', 'smoke', 'L1'), ('S-L2', 'extra', 'smoke', 'L2'), ('S-L4', 'extra', 'smoke', 'L4'),
         ('S-I1', 'extra', 'smoke', 'I1'), ('S-I4', 'extra', 'smoke', 'I4'), ('S-I5', 'extra', 'smoke', 'I5'), ('S-A1', 'extra', 'smoke', 'A1')]
NEGATIVES = ['a character of the replacement missing', 'two characters in another order', 'a similar character (末 for 未)',
             'the replacement drawn elsewhere on the page only', 'the first line\'s part right, the next line\'s wrong',
             'the two parts on lines that are not consecutive', 'glyphs drawn without a Unicode mapping (readable, not searchable)',
             'a glyph mapped to another character (copied garbled)']


def _lines(path, page, frame, baselines):
    """What ``path`` draws on the paragraph's lines: per line, text, baseline, start and end x (MuPDF, rawdict)."""
    out = []
    with pymupdf.open(path) as d:
        chars = [(c['c'], c['origin'], c['bbox']) for b in d[page - 1].get_text('rawdict')['blocks'] for l in b.get('lines', [])
                 for s in l['spans'] for c in s['chars']]
    f = pymupdf.Rect(frame) + (-1, -1, 1, 1)
    for y in baselines:
        row = sorted((c for c in chars if f.contains(pymupdf.Point(c[1])) and abs(c[1][1] - y) <= 1.5), key=lambda c: c[1][0])
        out.append(dict(text=''.join(c[0] for c in row), baseline=round(y, 2), x0=round(row[0][2][0], 2) if row else None,
                        x1=round(row[-1][2][2], 2) if row else None))
    return out


def _case(cid, source_manifest, source_id):
    m = json.loads(Path(f'evaluations/{source_manifest}/manifest.json').read_text())
    c = next(x for x in m['cases'] if x['id'] == source_id)
    return m, c


def _judgement(source_manifest, source_id):
    """The judgement recorded so far for the case's first and second steps (common records)."""
    res = json.loads(Path(f'evaluations/{source_manifest}/results.json').read_text())
    recs = res['runs']['new']['records'] if 'runs' in res else res['records']
    out = {}
    for r in recs:
        if r['case'] == source_id:
            out[r['step']] = dict(outcome=r['outcome'], mode=r['mode'], tiers=r['tiers'],
                                  approved_quality=r['checks'].get('approved_quality'), approved_audit=r['checks'].get('approved_audit'))
    return out


def step(code, source, case, root):
    """Save the case's edit approving exactly the candidates its proposal lists; the output, or the refusal."""
    sub = root / case['id']
    sub.mkdir(parents=True, exist_ok=True)
    output = sub / 'out.pdf'
    for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
        if p.exists():
            p.unlink()
    ce.locate = rows.make_locate(code)
    paragraph = case['paragraph']
    after = paragraph.replace(case['edit']['old'], case['edit']['new'], 1)
    r = panel_evaluate.chain_step(code, source, case, case['edit']['old'], case['edit']['new'], after, output, sub, 0, 12)
    return r, output


def phase_freeze(args):
    root = Path(args.root)
    code = Path('.').resolve()
    documents, cases = {}, []
    for cid, group, src, sid in CASES:
        m, c = _case(cid, src, sid)
        path = source_of(c['document'], args)
        if not path.exists():
            cases.append(dict(id=cid, group=group, origin=f'{src}:{sid}', status='input missing'))
            continue
        documents[c['document']] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        case = dict(id=cid, group=group, origin=f'{src}:{sid}', document=c['document'], page=c['page'], selection=c.get('selection'),
                    paragraph=c['paragraph'], box=c['box'], lines=c.get('lines', 1), edit=c['edit'], judgement=_judgement(src, sid))
        r, output = step(code, path.resolve(), case, root)
        if r['outcome'] != 'saved':
            case.update(saved=False, reason=r.get('reason'))
        else:
            p = json.loads(output.with_suffix('.proposal.json').read_text())
            case.update(saved=True, approved=r['approved'], expected=p['expected'], replacement=p['replacement'],
                        frame=[round(v, 2) for v in p['evidence']['inferred']['frame']],
                        drawn=_lines(output, c['page'], p['evidence']['inferred']['frame'], [l['baseline'] for l in p['lines']]),
                        output_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
        cases.append(case)
        print(cid, case.get('saved'), [l['text'] for l in case.get('drawn', [])], flush=True)
    import subprocess
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(schema='search-copy-manifest-1', frozen_at=rev,
                    policy=('Frozen before the measures are written (issue #114). Each measure is true, false or null (not measured) and '
                            'tied to the edited paragraph\'s drawn lines; a hit elsewhere on the page, a whitespace-only match or a part '
                            'found by search on its own line never stands for a whole search. Practical success is not changed here: which '
                            'measures it requires is a design decision. Fixtures (negative cases) are counted apart from real PDFs.'),
                    measures=dict(search_full='MuPDF search_for of the whole replacement (spaces kept) hits the paragraph\'s lines where it is drawn',
                                  search_per_line='each line\'s part of a replacement drawn over lines is found by search on that line, lines '
                                                  'consecutive, parts continuous; null for one line',
                                  extraction_mupdf='MuPDF text of the paragraph\'s lines, line breaks joined as the paragraph joins them, equals the '
                                                   'expected paragraph (no other normalisation; whitespace-only differences reported apart)',
                                  extraction_poppler='the same with pdftotext (cropped to the lines)',
                                  copy_equivalent='both extractors read the area where the replacement is drawn as the replacement (copying '
                                                  'reconstructed by PDF extraction, not a clipboard)',
                                  clipboard_manual='a real clipboard check; null unless made by hand'),
                    negatives=NEGATIVES, documents=documents, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')


def phase_run(args):
    from .approved_audit import audit
    from . import search_copy
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    code = Path('.').resolve()
    results = {}
    for case in manifest['cases']:
        if case.get('status') == 'input missing':
            results[case['id']] = dict(outcome='input missing')
            continue
        source = source_of(case['document'], args).resolve()
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['documents'][case['document']]['sha256']:
            results[case['id']] = dict(outcome='input differs')
            continue
        r, output = step(code, source, case, root)
        if r['outcome'] != 'saved':
            results[case['id']] = dict(outcome=r['outcome'], reason=r.get('reason'))
            continue
        p = json.loads(output.with_suffix('.proposal.json').read_text())
        expected, new, at, frame, baselines = search_copy.target(p)
        drawn = _lines(output, case['page'], frame, baselines)
        a = audit(source, output, case['page'])
        results[case['id']] = dict(outcome='saved', same_output=drawn == case.get('drawn'), drawn=drawn,
                                   quality_existing=a['quality'], verdict=a['verdict'], mode=a['mode'],
                                   measures=search_copy.measure(output, case['page'], expected, new, at, frame, baselines, source))
        m = results[case['id']]['measures']
        print(case['id'], {k: m[k] for k in ('search_full', 'search_per_line', 'extraction_mupdf', 'extraction_poppler', 'copy_equivalent')},
              'existing', a['quality']['status'], flush=True)
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')
    (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def phase_collect(args):
    results = json.loads((Path(args.root) / 'results.json').read_text())
    keys = ('search_full', 'search_per_line', 'extraction_mupdf', 'extraction_poppler', 'copy_equivalent', 'clipboard_manual')
    groups = {c['id']: c['group'] for c in json.loads(MANIFEST.read_text())['cases']}
    summary = {}
    for cid, r in results.items():
        for k in keys:
            v = (r.get('measures') or {}).get(k, 'not run' if r.get('outcome') != 'saved' else None)
            summary.setdefault(groups[cid], {}).setdefault(k, {}).setdefault(str(v).lower(), 0)
            summary[groups[cid]][k][str(v).lower()] += 1
    out = dict(schema='search-copy-results-1', manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), summary=summary, results=results)
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    print(json.dumps(summary, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('freeze', 'run', 'collect'), required=True)
    p.add_argument('--root', required=True)
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    args = p.parse_args()
    dict(freeze=phase_freeze, run=phase_run, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
