"""Lengthening edits whose following lines are drawn in one text object with other text: the line separated from it (issue #105).

  PYTHONPATH=<old tree> .venv/bin/python -m prototypes.paragraph_frame.separate_evaluate --phase edits --fusei <477.pdf> --pdfjs <dir> --survey <dir> --geometry <dir> --edits <edits.json>
  .venv/bin/python -m prototypes.paragraph_frame.separate_evaluate --phase freeze --fusei <477.pdf> --pdfjs <dir> --edits <edits.json> --geometry <dir>
  .venv/bin/python -m prototypes.paragraph_frame.separate_evaluate --phase run --label old|new --code <tree> --root tmp/<dir> --fusei <477.pdf> --pdfjs <dir>
  .venv/bin/python -m prototypes.paragraph_frame.separate_evaluate --phase collect --roots old=tmp/<a> new=tmp/<b>

``freeze`` (before the implementation) fixes the cases from the PR #104
survey (chain_survey over every paragraph of the real PDFs): the 340 places
where the chain was refused because a following line is drawn together with
content beyond it. Each is first measured geometrically (chain_geometry: the
chain planned as if every element were its own drawing):

* ``fits``: moved together, the growth has room (43 places);
* ``capacity``: the paragraph would still not fit (a C02-like limit);
* ``obstacle``: an element would meet protected content;
* ``columns``: an element would pass the shared bottom of side-by-side columns.

Only ``fits`` can be helped by separating drawings. For each of them the old
B2 (main 11a9aa7) looked for a lengthening by one line with the paragraph's
own text (the selection followed by the paragraph's first characters, about
one line); the places where it found one and its chain was refused for the
shared drawing are the real cases (``cases``); those without one are listed
with their reason (``no_edit``: no legal line break at the original font
size, a missing glyph, no added line) and not run. The nine cases of the
PR #104 chain evaluation are run again (``reevaluated``): their limits are
capacity and protected content, not drawing units. The generated fixtures
G1-G6 of tests/test_paragraph_frame_separate.py are run apart.

``run``: chain_evaluate.run_case (the edit, then the edit back from the saved
PDF alone; candidates approved exactly as listed, never carried over), and
for every saved step a ``kept`` audit of what is not moved and not edited:
glyphs (character, origin at 0.01 pt), MuPDF and Poppler words (text and
order, box at 0.01 pt), drawings, and pixels at 72 and 150 dpi outside the
edited paragraph and the moved elements' old and new areas; and the moved
elements' words (MuPDF, Poppler) in the same order, translated.
"""
import argparse
from collections import Counter
import glob
import json
from pathlib import Path
import subprocess
import sys

import pymupdf

from . import chain_evaluate as ce
from .fidelity_evaluate import sha, source_of
from .placed_evaluate import block

MANIFEST = Path('evaluations/paragraph_frame_separate/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
FIXTURES = {'G1': 'the heading first, then the line, in one text object: separated on approval, then moved back',
            'G2': 'the line first, then the heading placed by Td, a box on the line before the text object: separated, then moved back',
            'G3': 'one TJ draws the line and the heading: refused (no operator is cut)',
            'G4': 'marked content in the text object: refused',
            'G5': 'text after the line continues from where it ends: refused',
            'G6': 'a clip around the text object ending right below the line: refused'}
FIXTURE_CASE = dict(page=1, paragraph='本年度の補助事業について、募集を行います。申請書は窓口で配布します。', selection='本年度の補助',
                    box=[39.5, 48.5, 216.5, 96.0], lines=3, edit=dict(old='申請書は窓口で', new='申請書は窓口と郵送と電子申請とファクスと持参で'))
SHARED = 'a following paragraph is drawn together with content beyond its line'


def geometry_class(reason):
    if reason == 'fits':
        return 'fits'
    if reason.startswith('capacity'):
        return 'capacity'
    if 'shared bottom' in reason:
        return 'columns'
    if 'would meet protected' in reason:
        return 'obstacle'
    return 'other'


def edit_class(reasons):
    text = ' '.join(reasons)
    if 'no legal line break' in text:
        return 'capacity'
    if 'missing source glyph' in text:
        return 'missing glyph'
    if 'lines 1 needed None' in text:
        return 'no added line'
    return 'other'


def phase_edits(args):
    """For each place that fits, a lengthening by about one line with the paragraph's own text (run with the old B2)."""
    from .auto import propose
    from .engine import Refused
    out = []
    for f in sorted(glob.glob(str(Path(args.geometry) / '*.json'))):
        key = Path(f).stem
        doc, page = key.rsplit('-', 1)
        source = str(source_of(doc, args))
        survey = json.loads((Path(args.survey) / Path(f).name).read_text())
        for r in json.loads(Path(f).read_text()):
            if r.get('geometric') != 'fits':
                continue
            sel = next(x['selection'] for x in survey if x['text'][:40] == r['text'][:40])
            row = dict(document=doc, page=int(page), paragraph_head=r['text'][:40], selection=sel)
            try:
                p = propose(source, int(page), sel, sel)
                text = p['paragraphs'][0]
                longest = max(len(l['text']) for l in p['lines'])
                tried = []
                for n in (longest, longest + 4, max(8, longest // 2)):
                    new = sel + text[:n]
                    try:
                        q = propose(source, int(page), sel, new)
                    except Refused as e:
                        tried.append(str(e)[:120])
                        continue
                    ch = q['evidence'].get('chain') or {}
                    if len(q['lines']) > len(p['lines']) and ch.get('needed'):
                        row.update(old=sel, new=new, paragraph=text, lines=len(p['lines']), grown_lines=len(q['lines']),
                                   old_chain=ch.get('unavailable'), old_candidates=len(ch.get('candidates', [])))
                        break
                    tried.append(f'lines {len(q["lines"])} needed {ch.get("needed")}')
                else:
                    row['no_edit'] = tried
            except Refused as e:
                row['no_edit'] = [str(e)[:160]]
            out.append(row)
            print(json.dumps({k: (v[:60] if isinstance(v, str) else v) for k, v in row.items() if k != 'paragraph'}, ensure_ascii=False), flush=True)
    Path(args.edits).write_text(json.dumps(out, ensure_ascii=False, indent=1))


def phase_freeze(args):
    rows = [r for f in sorted(glob.glob(str(Path(args.geometry) / '*.json'))) for r in json.loads(Path(f).read_text())]
    classes = Counter(geometry_class(r.get('geometric') or '') for r in rows)
    edits = json.loads(Path(args.edits).read_text())
    cases, no_edit = [], []
    for n, e in enumerate(edits):
        if 'new' not in e:
            no_edit.append(dict(document=e['document'], page=e['page'], paragraph=e['paragraph_head'], constraint=edit_class(e['no_edit']),
                                reason=e['no_edit'][0][:160]))
            continue
        if SHARED not in (e.get('old_chain') or ''):
            continue
        source = source_of(e['document'], args)
        case = dict(id=f"R{len(cases) + 1:02d}-{e['document']}-p{e['page']}", document=e['document'], page=e['page'], paragraph=e['paragraph'],
                    selection=e['selection'], lines=e['lines'], edit=dict(old=e['old'], new=e['new']),
                    origin='PR #104 survey: chain refused for a shared drawing; room for the growth', old_chain=e['old_chain'])
        case['box'] = block(source, case['page'], case['selection'])
        cases.append(case)
    chain = json.loads(ce.MANIFEST.read_text())
    reevaluated = [dict(c, origin='PR #104 chain evaluation (' + c['origin'] + ')') for c in chain['cases']]
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    docs = sorted({c['document'] for c in cases + reevaluated})
    manifest = dict(
        schema='paragraph-frame-separate-manifest-1', baseline_commit='11a9aa7 (main after PR #104)', frozen_at=commit,
        policy=('Frozen before the implementation. Old B2 is main 11a9aa7; new B2 is this branch. Targets: the lengthening edits of the '
                'PR #104 survey whose chain was refused only because a following line is drawn in one text object with other text and '
                'whose growth has room once the elements move (geometry: fits). Places limited by capacity, protected content or the '
                'columns are counted apart and never reported as solved by separation. A following element moves only when its own '
                'candidate id is approved; acknowledge_inference or another approval never approves it; an approval is never carried '
                'from one step to another. A refusal ends its case and never counts as success; the first save and the edit back are '
                'counted apart; fixture successes are reported apart from real-PDF successes. Nothing is rasterised, painted over or '
                'reflowed; no other line moves.'),
        procedure=['propose; record the chain candidates (and what each separates)',
                   'save without approving them (a step needing one must be refused)',
                   'propose again approving exactly the listed candidates until no new one is listed (3 rounds at most); save approving them',
                   'reopen the saved PDF alone and edit back the same way'],
        measures=('saved or refused (reason); the chain evaluation\'s audits unchanged (safety and quality kept apart, structure, continued '
                  'editing, moves, links, placement, raster); kept: glyphs, MuPDF and Poppler words (text, order, 0.01 pt), drawings and '
                  'pixels (72, 150 dpi) outside the edited paragraph and the moved areas unchanged; the moved elements\' words in the same '
                  'order; the edit back against the source (glyph origins, line breaks, MuPDF and Poppler text, search, drawings, images, '
                  'links, structure, pixels at 72 and 150 dpi)'),
        target='at least one real lengthening edit newly saved by separating a following line, the other lines unchanged; else the reason per case',
        survey=dict(refused_for_shared_drawing=len(rows), geometry=dict(classes),
                    fits=dict(cases=len(cases), no_edit=dict(Counter(x['constraint'] for x in no_edit)))),
        documents={d: dict(sha256=sha(source_of(d, args))) for d in docs},
        fixtures={k: dict(expected=v, edit=FIXTURE_CASE['edit']) for k, v in FIXTURES.items()},
        cases=cases, no_edit=no_edit, reevaluated=reevaluated)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(len(cases), 'real cases;', len(no_edit), 'without an edit;', len(reevaluated), 'reevaluated;', len(FIXTURES), 'fixtures;', dict(classes))


# --- kept: what is neither edited nor moved --------------------------------

def _poppler_words(path, page):
    run = subprocess.run(['pdftotext', '-f', str(page), '-l', str(page), '-bbox', str(path), '-'], capture_output=True, check=True)
    import re
    out = []
    for m in re.finditer(rb'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', run.stdout):
        out.append((tuple(float(v) for v in m.groups()[:4]), m.group(5).decode()))
    return out


def _mupdf_words(path, page):
    with pymupdf.open(path) as d:
        return [(tuple(w[:4]), w[4]) for w in d[page - 1].get_text('words')]


def kept_audit(source, output, page, edited_source, edited_output, moved):
    """What neither the edit nor an approved move touches, unchanged; the moved words in order."""
    pad = lambda r, v=.5: pymupdf.Rect(r) + (-v, -v, v, v)
    old, new = [pad(m['old']) for m in moved], [pad(m['new']) for m in moved]
    away_a, away_b = [pad(edited_source, 3)] + old, [pad(edited_output, 3)] + new
    off = lambda r, areas: not any(pymupdf.Rect(r).intersects(a) for a in areas)
    r2 = lambda r: tuple(round(v, 2) for v in r)
    out = {}
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        pa, pb = a[page - 1], b[page - 1]
        chars = lambda p, areas: Counter((c['c'], round(c['origin'][0], 2), round(c['origin'][1], 2))
                                         for bl in p.get_text('rawdict')['blocks'] for l in bl.get('lines', []) for s in l['spans']
                                         for c in s['chars'] if off(c['bbox'], areas))
        ga, gb = chars(pa, away_a), chars(pb, away_b)
        out['glyphs'] = dict(compared=sum(ga.values()), identical=ga == gb)
        draw = lambda p, areas: Counter((x.get('type'), str(x.get('fill')), str(x.get('color')), r2(x['rect'])) for x in p.get_drawings()
                                        if off(x['rect'], areas))
        out['drawings'] = draw(pa, away_a) == draw(pb, away_b)
        pixels = {}
        for dpi in (72, 150):
            m = pymupdf.Matrix(dpi / 72, dpi / 72)
            xa, xb = pa.get_pixmap(matrix=m, alpha=False), pb.get_pixmap(matrix=m, alpha=False)
            for r in away_a + away_b:
                box = (pad(r, 2) * m).irect
                xa.set_rect(box, (255, 255, 255))
                xb.set_rect(box, (255, 255, 255))
            pixels[str(dpi)] = sum(x != y for x, y in zip(xa.samples, xb.samples))
        out['pixels'] = dict(differing_samples=pixels, identical=not any(pixels.values()))
    delta = lambda r, k: (pymupdf.Rect(r) + (0, k, 0, k))
    for name, words in (('mupdf', _mupdf_words), ('poppler', _poppler_words)):
        wa, wb = words(source, page), words(output, page)
        kept_a = [(r2(r), t) for r, t in wa if off(r, away_a)]
        kept_b = [(r2(r), t) for r, t in wb if off(r, away_b)]
        moved_a = [t for r, t in wa if any(o.contains(pymupdf.Rect(r)) for o in old)]
        moved_b = [t for r, t in wb if any(n.contains(pymupdf.Rect(r)) for n in new)]
        out['words_' + name] = dict(kept=len(kept_a), kept_identical=kept_a == kept_b, moved=len(moved_a), moved_in_order=moved_a == moved_b)
    out['ok'] = (out['glyphs']['identical'] and out['drawings'] and out['pixels']['identical']
                 and all(out['words_' + n]['kept_identical'] for n in ('mupdf', 'poppler')))
    return out


def _separated(record):
    return [dict(id=c['id'], separated=c['separated']) for c in ((record.get('chain') or {}).get('candidates') or []) if c.get('separated')]


_chain_step = ce.chain_step


def chain_step(code, start, case, old, new, after, output, sub, grow, pitch):
    """chain_evaluate.chain_step, and the kept audit and the separations of a saved step."""
    r = _chain_step(code, start, case, old, new, after, output, sub, grow, pitch)
    r['separated'] = _separated(r)
    if r['outcome'] != 'saved':
        return r
    save = json.loads(output.with_suffix('.save.json').read_text())
    x0, y0, x1, y1 = case['box']
    edited = [x0, y0, x1, y1 + max(r['grow'], grow) * pitch]
    r['kept'] = kept_audit(start, output, case['page'], case['box'], edited, ce._moved(save))
    return r


def fixtures(folder):
    sys.path.insert(0, str(Path('tests').resolve()))
    import test_paragraph_frame_separate as t
    folder.mkdir(parents=True, exist_ok=True)
    return {name: t.fixture(name, folder / name) for name in FIXTURES}


def phase_run(args):
    ce.chain_step = chain_step
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path(args.code).resolve()
    only = set(args.only or [])
    path = root / 'results.json'
    results = json.loads(path.read_text()) if path.exists() and only else {}
    made = fixtures(root / 'fixtures')
    for name in manifest['fixtures']:
        if only and name not in only:
            continue
        results[name] = ce.run_case(code, dict(FIXTURE_CASE, id=name, document=name), made[name].resolve(), root)
    for case in manifest['cases'] + manifest['reevaluated']:
        if only and case['id'] not in only:
            continue
        results[case['id']] = ce.run_case(code, case, source_of(case['document'], args).resolve(), root)
        path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')
    path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def summary(results, ids):
    s = Counter(ce.summary(results, ids))
    for k in ids:
        seq = results.get(k) or []
        if not seq:
            continue
        s['with_separation_candidates'] += bool(seq[0].get('separated'))
        for n, r in enumerate(seq):
            if r['outcome'] != 'saved':
                continue
            step = 'edit' if n == 0 else 'back'
            s[step + '_separated'] += bool(r.get('separated'))
            kept = r.get('kept') or {}
            s[step + '_kept_glyphs'] += bool((kept.get('glyphs') or {}).get('identical'))
            s[step + '_kept_drawings'] += bool(kept.get('drawings'))
            s[step + '_kept_pixels'] += bool((kept.get('pixels') or {}).get('identical'))
            s[step + '_kept_words_both'] += all((kept.get('words_' + x) or {}).get('kept_identical') for x in ('mupdf', 'poppler'))
            s[step + '_moved_words_in_order_both'] += all((kept.get('words_' + x) or {}).get('moved_in_order') for x in ('mupdf', 'poppler'))
    return dict(s)


def phase_collect(args):
    manifest = json.loads(MANIFEST.read_text())
    labels = dict(x.split('=', 1) for x in args.roots)
    results = {label: json.loads((Path(root) / 'results.json').read_text()) for label, root in labels.items()}
    compact = lambda r: {k: (v[-300:] if k == 'reason' and v else v.get('ok') if k == 'continued_editing' and isinstance(v, dict) else v)
                         for k, v in r.items() if k not in ('lines', 'frame', 'seconds', 'occurrence')}
    groups = dict(fixtures=list(manifest['fixtures']), real=[c['id'] for c in manifest['cases']],
                  reevaluated=[c['id'] for c in manifest['reevaluated']])
    out = dict(schema='paragraph-frame-separate-results-1', manifest_sha256=sha(MANIFEST),
               tests=json.loads(Path(args.tests_json).read_text()) if args.tests_json else None,
               regression=json.loads(Path(args.regression_json).read_text()) if args.regression_json else None,
               summary={label: {g: summary(r, ids) for g, ids in groups.items()} for label, r in results.items()},
               results={label: {k: [compact(x) for x in seq] for k, seq in r.items()} for label, r in results.items()})
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    for label, gs in out['summary'].items():
        for g, s in gs.items():
            print(label, g, json.dumps(s, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('edits', 'freeze', 'run', 'collect'), required=True)
    p.add_argument('--label')
    p.add_argument('--code', default='.')
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--edits')
    p.add_argument('--geometry')
    p.add_argument('--survey')
    p.add_argument('--only', nargs='*')
    p.add_argument('--roots', nargs='*')
    p.add_argument('--tests-json')
    p.add_argument('--regression-json')
    args = p.parse_args()
    dict(edits=phase_edits, freeze=phase_freeze, run=phase_run, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
