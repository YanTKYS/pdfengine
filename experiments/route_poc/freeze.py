"""Fix the comparison cases before the PoC exists (issue #122).

  .venv/bin/python -m experiments.route_poc.freeze --fusei <477.pdf> --pdfjs <pdf.js dir>

Writes experiments/route_poc/cases.json: 16 edits of real PDFs chosen from the
committed evaluations (successes and failures of the current approach; short,
same-length and longer; Japanese and English; Word, LibreOffice, InDesign,
Antenna House, pdfTeX; plain paragraphs, panels, clips, side images, rounded
backgrounds, shared text operators). Per case: the input digest, page, the text
the user selects and the text typed, a click point inside the selected text,
and the current approach's committed result for the same edit (B2 with every
listed candidate approved), with where it comes from.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pymupdf

OUT = Path('experiments/route_poc/cases.json')

# (id, source evaluation, its case id, why it is in the set).
CHOSEN = [
    ('L2', 'panel', 'L2', 'LibreOffice caption on a white panel, longer (+1 line): current saved, practical'),
    ('L8', 'panel', 'L8', 'LibreOffice caption on a panel, longer: current saved, not practical (source gap)'),
    ('W1', 'clip', 'W1', 'Word text box (clip + outline), longer: current saved, practical'),
    ('W2c', 'clip', 'W2', 'Word text box, longer: current saved, not practical (letter-spaced FAX)'),
    ('R02', 'remaining', 'R02-issue17730-p2', 'English, following lines in a shared drawing (chain): current saved, practical'),
    ('R03', 'remaining', 'R03-lo_newfeatures_ja-p1', 'LibreOffice paragraph beside a screenshot, longer: current saved (edit back refused), not practical'),
    ('R04', 'remaining', 'R04-lo_newfeatures_ja-p1', 'LibreOffice bulleted line beside a screenshot: current saved, not practical'),
    ('R14', 'remaining', 'R14-osaka_fusei_477-p5', 'InDesign text beside a photo: current saved, not practical'),
    ('R07', 'remaining', 'R07-osaka_fusei_477-p3', 'InDesign text on a rounded background: current refused'),
    ('R10', 'remaining', 'R10-osaka_fusei_477-p4', 'InDesign map legend, next line in the same text operator: current refused'),
    ('R22', 'remaining', 'R22-osaka_fusei_477-p8', 'InDesign nested background, lines below on it: current refused'),
    ('S-L1', 'smoke', 'L1', 'LibreOffice same length (a date): current saved, not practical (source gap)'),
    ('S-I1', 'smoke', 'I1', 'InDesign same length (a year): current saved, practical'),
    ('S-W2', 'smoke', 'W2', 'Word shorter (a phrase deleted): current saved, practical'),
    ('S-A1', 'smoke', 'A1', 'Antenna House English longer, the lines below move: current saved, practical'),
    ('S-T1', 'smoke', 'T1', 'pdfTeX English, a word replaced (shorter): current refused'),
]
MANIFESTS = dict(panel='evaluations/paragraph_panel/manifest.json', clip='evaluations/paragraph_clip/manifest.json',
                 remaining='evaluations/paragraph_frame_separate/manifest.json', smoke='evaluations/smoke/manifest.json')


def _records(kind):
    if kind == 'panel':
        return json.loads(Path('evaluations/paragraph_panel/results.json').read_text())['runs']['new']['records'], 'evaluations/paragraph_panel/results.json (runs.new)'
    if kind == 'clip':
        return json.loads(Path('evaluations/paragraph_clip/results.json').read_text())['records'], 'evaluations/paragraph_clip/results.json'
    if kind == 'remaining':
        return json.loads(Path('evaluations/paragraph_remaining/results.json').read_text())['runs']['new']['records'], 'evaluations/paragraph_remaining/results.json (runs.new)'
    return json.loads(Path('evaluations/smoke/results.json').read_text())['records'], 'evaluations/smoke/results.json'


def click(path, page, old, box=None):
    """A point inside the occurrence of ``old`` the case edits: a search hit of its start inside the case's box (the
    selection's paragraph as its evaluation froze it), else the box's first line, else the first hit on the page."""
    with pymupdf.open(path) as d:
        hits = [h for k in (6, 3, 2) for h in d[page - 1].search_for(old[1:k + 1] if old[0] in '•・' else old[:k])]
    if box is not None:
        inside = [h for h in hits if pymupdf.Rect(box).intersects(h)]
        if inside:
            hits = inside
        else:
            return [round(box[0] + 4, 2), round(box[1] + 4, 2)]
    return [round((hits[0].x0 + hits[0].x1) / 2, 2), round((hits[0].y0 + hits[0].y1) / 2, 2)] if hits else None


def main():
    from prototypes.paragraph_frame.fidelity_evaluate import source_of
    p = argparse.ArgumentParser()
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    args = p.parse_args()
    where = SimpleNamespace(fusei=args.fusei or '', pdfjs=args.pdfjs or '')
    cases = []
    for cid, kind, sid, why in CHOSEN:
        m = json.loads(Path(MANIFESTS[kind]).read_text())
        c = next(x for x in m['cases'] if x['id'] == sid)
        path = source_of(c['document'], where)
        recs, origin = _records(kind)
        steps = sorted((r for r in recs if r['case'] == sid), key=lambda r: r['step'])
        current = [dict(step=r['step'], outcome=r['outcome'], mode=r['mode'], tiers=r['tiers'],
                        not_practical_because=[f"{b['criterion']}: {b['result']}" for b in r.get('practical_basis', [])
                                               if b['role'] != 'shown' and b['result'] is not True] or None,
                        reason=r.get('reason')) for r in steps]
        case = dict(id=cid, why=why, document=c['document'], page=c['page'], old=c['edit']['old'], new=c['edit']['new'],
                    kind=('longer' if len(c['edit']['new']) > len(c['edit']['old']) else 'shorter' if len(c['edit']['new']) < len(c['edit']['old'])
                          else 'same length'),
                    user_operations='click in the text, select the old text, type the new text',
                    current=dict(approach='B2 (prototypes/paragraph_frame), every listed candidate approved', origin=origin, steps=current))
        if not path.exists():
            case.update(status='input missing')
        else:
            case.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), click=click(path, c['page'], c['edit']['old'], c['box'] if isinstance(c.get('box'), list) else c.get('panel')))
        cases.append(case)
        print(cid, case.get('status', 'ok'), case['kind'], [(s['step'], s['outcome'], s['tiers']['practical']) for s in current], flush=True)
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    OUT.write_text(json.dumps(dict(schema='route-poc-cases-1', frozen_at=rev,
                                   policy=('Fixed before the PoC is written (issue #122): no case is added or removed afterwards. The current '
                                           'approach is taken at its best automatic path (B2 with every listed candidate approved), from the '
                                           'committed evaluation of the same edit. Saved, practical, re-edit and restore are counted apart; a '
                                           'check not made is unverified, never a success.'),
                                   cases=cases), ensure_ascii=False, indent=1) + '\n')


if __name__ == '__main__':
    main()
