"""Restore, from the saved PDF alone, an edit that moved a line decoration (issue #101).

  .venv/bin/python -m prototypes.paragraph_frame.restore_evaluate --phase freeze
  .venv/bin/python -m prototypes.paragraph_frame.restore_evaluate --phase run --label old|new --code <tree> --root tmp/<dir> --fusei <477.pdf>
  .venv/bin/python -m prototypes.paragraph_frame.restore_evaluate --phase collect --roots old=tmp/<a> new=tmp/<b>

``freeze`` (before the implementation) takes the real-PDF edits PR #100 saved
with an approved line decoration (evaluations/paragraph_frame_decoration:
P7-03, P7-05, P7-06, P7-07 shorten) and the generated fixtures D1 and D2.
PR #100 restored 1 of the 4 real edits (P7-07) from the saved PDF alone.

``run``, per case, with the B2 of ``--code``:

1. the edit, approving exactly the decoration candidates its proposal lists
   (decoration_evaluate.approved_step), saved;
2. the restoring edit proposed from that saved PDF alone, its candidates
   recorded; saved without approving them (a step needing one must be
   refused); saved approving exactly the candidates it lists.

Each saved step records the existing audits and the move audits of PR #100.
The restored PDF is compared with the source PDF:

* ``glyphs``: every glyph of the page (character, font, size) at its source
  origin; counts beyond 0.01 pt and beyond 0.001 pt and the largest offset;
* ``paragraph``: unchanged glyphs at their origin and the source line breaks
  (roundtrip_evaluate.compare), page text of MuPDF and Poppler, search hits;
* ``paint``: every drawing unchanged (0.01 pt); images unchanged (a shading
  raster by count, colour space and area to 0.75 pt);
* ``links``: every link annotation's target, /Rect and /QuadPoints as in the
  source (0.001 pt);
* ``structure``: tagged_evaluate.structure_audit against the source;
* ``pixels`` (reported apart): MuPDF renderings at 72 and 150 dpi, samples
  differing from the source and the largest difference.

A refused step ends its case and never counts as success; a fixture success
is reported apart from the real PDFs.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

import pymupdf
from pypdf import PdfReader

from .fidelity_evaluate import sha, source_of
from .decoration_evaluate import approved_step, fixtures
from .placed_evaluate import block
from .roundtrip_evaluate import compare

MANIFEST = Path('evaluations/paragraph_frame_restore/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
DECORATION = Path('evaluations/paragraph_frame_decoration')
REAL = ('P7-03', 'P7-05', 'P7-06', 'P7-07')
FIXTURE_EDITS = {'D1': ('lengthen', 'shorten'), 'D2': ('lengthen',)}


def phase_freeze(args):
    deco = json.loads((DECORATION / 'manifest.json').read_text())
    results = json.loads((DECORATION / 'results.json').read_text())['results']['new']
    cases = []
    for c in deco['cases']:
        if c['id'] not in REAL:
            continue
        steps = results[c['id']]['shorten']
        cases.append(dict({k: c[k] for k in ('id', 'document', 'page', 'paragraph', 'selection', 'box', 'lines')},
                          edit=dict(old=c['edits']['shorten']['old'], new=c['edits']['shorten']['new']),
                          pr100=dict(edit=steps[0]['outcome'], restore=steps[1]['outcome'] if len(steps) > 1 else 'not run',
                                     restore_reason=(steps[1].get('reason') or '')[-200:] if len(steps) > 1 else None)))
    fixture_cases = {k: dict(edits={e: deco['fixtures'][k]['edits'][e] for e in v}, expected=deco['fixtures'][k]['expected'])
                     for k, v in FIXTURE_EDITS.items()}
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(
        schema='paragraph-frame-restore-manifest-1', baseline_commit='cc758cb (main after PR #100)', frozen_at=commit,
        policy=('Frozen before the implementation. Old B2 is main cc758cb; new B2 is this branch. Targets: the 4 real-PDF edits PR #100 saved '
                'with an approved line decoration; PR #100 restored 1 of them (P7-07) from the saved PDF alone. Every step approves exactly the '
                'decoration candidates its own proposal lists; an approval is never carried from one step to another. A refusal ends its case '
                'and never counts as success; fixture successes are reported apart from real-PDF successes.'),
        procedure=['edit, approving the listed candidates; save', 'reopen the saved PDF alone; propose the restoring edit; record its candidates',
                   'save the restoring edit without approving them (a step needing one must be refused)',
                   'save it approving exactly the listed candidates'],
        measures=('per step: saved or refused (reason), the existing audits, structure, continued editing, the move audits of PR #100; restored '
                  'PDF against the source: glyph origins (0.01 pt and 0.001 pt, largest offset), unchanged paragraph glyphs and source line breaks, '
                  'page text (MuPDF, Poppler), search hits, drawings (0.01 pt), images (shading rasters by count, colour space and area), link '
                  'targets with /Rect and /QuadPoints (0.001 pt), structure; pixels at 72 and 150 dpi reported apart'),
        target='at least 2 more real restorations than PR #100 (3 of 4 or more)',
        documents=deco['documents'], fixtures=fixture_cases, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(len(cases), 'real cases;', len(fixture_cases), 'fixtures;', 'PR #100 restored', sum(c['pr100']['restore'] == 'saved' for c in cases))


def _glyphs(path, page):
    with pymupdf.open(path) as d:
        return [(c['c'], s['font'], round(s['size'], 3), c['origin']) for b in d[page - 1].get_text('rawdict')['blocks']
                for l in b.get('lines', []) for s in l['spans'] for c in s['chars']]


def glyphs_against(source, output, page):
    """Every glyph of the page at its source origin: unmatched counts and the largest offset."""
    a, b = _glyphs(source, page), _glyphs(output, page)
    pool = {}
    for ch, font, size, o in b:
        pool.setdefault((ch, font, size), []).append(o)
    worst, beyond, beyond_fine, missing = 0., 0, 0, 0
    for ch, font, size, o in a:
        cands = pool.get((ch, font, size), [])
        if not cands:
            missing += 1
            continue
        j = min(range(len(cands)), key=lambda k: abs(cands[k][0] - o[0]) + abs(cands[k][1] - o[1]))
        p = cands.pop(j)
        d = max(abs(p[0] - o[0]), abs(p[1] - o[1]))
        worst = max(worst, d)
        beyond += d > .01
        beyond_fine += d > .001
    extra = sum(len(v) for v in pool.values())
    return dict(glyphs=len(a), missing=missing, extra=extra, beyond_001=beyond, beyond_0001=beyond_fine, largest=round(worst, 5),
                ok=not missing and not extra and not beyond)


def paint_against(source, output, page):
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        da, db = a[page - 1].get_drawings(), b[page - 1].get_drawings()
        ia, ib = a[page - 1].get_image_info(xrefs=True), b[page - 1].get_image_info(xrefs=True)
    key = lambda x: (x.get('type'), str(x.get('fill')), str(x.get('color')), str(x.get('width')), tuple(round(v, 2) for v in x['rect']))
    drawings = Counter(map(key, da)) == Counter(map(key, db))
    ikey = lambda x: (x.get('digest'), tuple(round(v, 2) for v in x['bbox']))
    rest_a, rest_b = Counter(map(ikey, ia)), Counter(map(ikey, ib))
    left_a, left_b = list((rest_a - rest_b).elements()), list((rest_b - rest_a).elements())
    # What is left on either side is a shading raster: same count, colour space and area to 0.75 pt.
    raster = lambda items, which: sorted((x.get('cs-name'), x.get('bpc')) for x in which if ikey(x) in items)
    near = len(left_a) == len(left_b) and all(any(max(abs(p - q) for p, q in zip(x[1], y[1])) <= .75 for y in left_b) for x in left_a)
    return dict(drawings=drawings, images_same=not left_a and not left_b, shading_rasters=len(left_a),
                images=not left_a and not left_b or (near and raster(left_a, ia) == raster(left_b, ib)))


def links_against(source, output, page):
    """Link targets, /Rect and /QuadPoints of the page's annotations (PDF space, 0.001 pt)."""
    def annots(path):
        p = PdfReader(str(path)).pages[page - 1]
        out = []
        for ref in (p.get('/Annots') or []):
            a = ref.get_object()
            action = a.get('/A')
            uri = str(action.get_object().get('/URI')) if action is not None and '/URI' in action.get_object() else None
            out.append((str(a.get('/Subtype')), uri, [float(v) for v in a['/Rect']], [float(v) for v in a.get('/QuadPoints', [])]))
        return sorted(out, key=lambda x: (x[0], str(x[1]), x[2]))
    a, b = annots(source), annots(output)
    same = len(a) == len(b) and all(x[0] == y[0] and x[1] == y[1] and len(x[3]) == len(y[3])
                                    and all(abs(p - q) <= .001 for p, q in zip(x[2] + x[3], y[2] + y[3])) for x, y in zip(a, b))
    return dict(annotations=len(a), ok=same)


def pixels_against(source, output, page):
    out = {}
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        for dpi in (72, 150):
            pa, pb = a[page - 1].get_pixmap(dpi=dpi, alpha=False), b[page - 1].get_pixmap(dpi=dpi, alpha=False)
            diff = [abs(x - y) for x, y in zip(pa.samples, pb.samples) if x != y]
            out[str(dpi)] = dict(differing_samples=len(diff), largest=max(diff, default=0), samples=len(pa.samples))
    out['identical'] = all(v['differing_samples'] == 0 for v in out.values())
    return out


def run_case(code, case, source, root, approve):
    from .tagged_evaluate import structure_audit
    pitch = (case['box'][3] - case['box'][1]) / max(1, case['lines'])
    case = dict(case, box=block(source, case['page'], case.get('selection') or case['paragraph'][:6]))
    old, new = case['edit']['old'], case['edit']['new']
    text = case['paragraph']
    changed = text.replace(old, new, 1)
    seq, prev, grow = [], source, 0
    for n, (a, b, before, after) in enumerate([(old, new, text, changed), (new, old, changed, text)]):
        sub = root / case['id'] / f'{n + 1}'
        sub.mkdir(parents=True, exist_ok=True)
        output = sub / 'out.pdf'
        for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
            if p.exists():
                p.unlink()
        try:
            r = approved_step(code, prev, case, a, b, before, after, output, sub, grow, pitch, approve)
        except SystemExit as exc:
            r = dict(old=a, new=b, outcome='not run', reason=str(exc)[:300])
        if r['outcome'] == 'saved' and n == 1:
            r['restored'] = dict(
                compare=compare(source, output, case['page'], case['box'], text, r['frame'], 2 * pitch, sub / 'vs-source', search=old),
                glyphs=glyphs_against(source, output, case['page']), paint=paint_against(source, output, case['page']),
                links=links_against(source, output, case['page']),
                structure_failed=structure_audit(source, output, case['page'], text, [])['failed'],
                pixels=pixels_against(source, output, case['page']))
        seq.append(r)
        print(case['id'], n + 1, r['outcome'], len(r.get('candidates') or []), (r.get('unapproved') or {}).get('outcome'),
              (r.get('reason') or '')[-120:] if r['outcome'] != 'saved' else
              json.dumps({k: v.get('ok', v.get('identical')) if isinstance(v, dict) else v for k, v in (r.get('restored') or {}).items() if k != 'compare'}),
              flush=True)
        if r['outcome'] != 'saved':
            break
        prev = output
        grow = max(grow, r['grow'])
    return seq


def phase_run(args):
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path(args.code).resolve()
    only = set(args.only or [])
    # Both B2s (PR #100 and this branch) take the explicit decoration approval.
    approve = True
    results = json.loads((root / 'results.json').read_text()) if (root / 'results.json').exists() and only else {}
    made = fixtures(root / 'fixtures')
    for name, spec in manifest['fixtures'].items():
        for kind, (o, n) in spec['edits'].items():
            key = f'{name}-{kind}'
            if only and key not in only and name not in only:
                continue
            case = dict(id=key, document=name, page=1, paragraph='本年度の補助事業について、募集を行います。申請書は窓口で配布します。', selection='本年度の補助',
                        box=[39.5, 48.5, 216.5, 96.0], lines=3, edit=dict(old=o, new=n))
            results[key] = run_case(code, case, made[name].resolve(), root, approve)
    for case in manifest['cases']:
        if only and case['id'] not in only:
            continue
        results[case['id']] = run_case(code, case, source_of(case['document'], args).resolve(), root, approve)
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')
    (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def summary(results, ids):
    s = Counter()
    for k in ids:
        seq = results.get(k)
        if not seq:
            continue
        s['cases'] += 1
        s['edit_saved'] += seq[0]['outcome'] == 'saved'
        if len(seq) < 2:
            continue
        r = seq[1]
        s['restore_with_candidates'] += bool(r.get('candidates'))
        if r.get('candidates'):
            s['restore_unapproved_refused'] += (r.get('unapproved') or {}).get('outcome') == 'refused'
        if r['outcome'] != 'saved':
            s['restore_refused' if r['outcome'] == 'refused' else 'restore_not_run'] += 1
            continue
        s['restored'] += 1
        s['restore_safety'] += bool(r['safety'])
        s['restore_quality'] += bool(r['quality'])
        s['restore_structure'] += not r['structure_failed']
        ce = r.get('continued_editing')
        s['restore_continued_editing'] += bool(ce.get('ok') if isinstance(ce, dict) else ce)
        s['restore_move_audit'] += bool(r['placed']['ok'])
        s['restore_links_audit'] += bool(r['links']['ok'])
        x = r['restored']
        s['glyphs_at_source_001'] += x['glyphs']['ok']
        s['glyphs_at_source_0001'] += x['glyphs']['ok'] and not x['glyphs']['beyond_0001']
        s['breaks_kept'] += x['compare']['paragraph']['breaks_kept'] == x['compare']['paragraph']['source_breaks_outside_edit']
        s['text_identical_both'] += all(x['compare']['text_identical'].values())
        s['search_same'] += (x['compare'].get('search_hits') or {}).get('reference') == (x['compare'].get('search_hits') or {}).get('output')
        s['drawings_identical'] += x['paint']['drawings']
        s['images_identical'] += x['paint']['images_same']
        s['images_identical_or_shading_raster'] += x['paint']['images']
        s['links_identical'] += x['links']['ok']
        s['structure_vs_source'] += not x['structure_failed']
        s['pixels_identical'] += x['pixels']['identical']
    return dict(s)


def phase_collect(args):
    manifest = json.loads(MANIFEST.read_text())
    labels = dict(x.split('=', 1) for x in args.roots)
    results = {label: json.loads((Path(root) / 'results.json').read_text()) for label, root in labels.items()}
    compact = lambda r: {k: (v[-300:] if k == 'reason' and v else v.get('ok') if k == 'continued_editing' and isinstance(v, dict) else v)
                         for k, v in r.items() if k not in ('lines', 'frame', 'seconds', 'occurrence')}
    groups = dict(fixtures=[f'{k}-{e}' for k, v in manifest['fixtures'].items() for e in v['edits']], real=[c['id'] for c in manifest['cases']])
    out = dict(schema='paragraph-frame-restore-results-1', manifest_sha256=sha(MANIFEST),
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
    p.add_argument('--phase', choices=('freeze', 'run', 'collect'), required=True)
    p.add_argument('--label')
    p.add_argument('--code', default='.')
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--only', nargs='*')
    p.add_argument('--roots', nargs='*')
    p.add_argument('--tests-json')
    p.add_argument('--regression-json')
    args = p.parse_args()
    dict(freeze=phase_freeze, run=phase_run, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
