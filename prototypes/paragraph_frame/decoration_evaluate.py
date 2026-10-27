"""Line decorations moved with a following paragraph on explicit approval (issue #99).

  .venv/bin/python -m prototypes.paragraph_frame.decoration_evaluate --phase freeze --fusei <477.pdf>
  .venv/bin/python -m prototypes.paragraph_frame.decoration_evaluate --phase run --label old|new --code <tree> --root tmp/<dir> --fusei <477.pdf>
  .venv/bin/python -m prototypes.paragraph_frame.decoration_evaluate --phase collect --roots old=tmp/<a> new=tmp/<b>

``freeze`` (before the implementation) takes the real-PDF steps that PR #98
refused for content on the line of a following paragraph's placed object
(the "HP" search box, an image) or for a collision of the moved object, with
their frozen edits (evaluations/paragraph_frame_placed), and C02's lengthen of
the layout-fidelity evaluation (PR #92), refused for the same reason. Steps
refused for capacity or a missing glyph are not targets (the issue keeps
them refused). The generated fixtures are listed by name and expectation.

``run``, per step, with the B2 of ``--code``:

1. propose, and record the line-decoration candidates the proposal lists
   (``evidence.decorations.candidates``: page, region, operators, drawings,
   glyphs, annotations, translation, adjacent content);
2. save without approving any of them: a step that needs one must be refused;
3. save approving exactly the listed candidates (``--decoration <id>``; the
   evaluation stands for the user who read them); the old B2 has no such
   approval and stops at 2;
4. when saved, undo it from the saved PDF alone, approving the candidates the
   undo's own proposal lists.

Each saved step records the existing audits (indesign_evaluate.audit over the
edited and following paragraphs, structure, continued editing), the placed
audit of PR #98 (approved moves exact and nothing left; every other drawing,
image, link and annotation unchanged), and the decoration audit:

* ``links``: every link of the page keeps its target; a moved link's
  clickable area is its old one translated exactly, every other is unchanged;
* ``raster``: rendered at a scale where the translation is a whole number of
  pixels, the moved group's area in the output equals its old area in the
  source (MuPDF), pixel for pixel.

The undo is compared with the source (roundtrip_evaluate.compare). A refused
step ends its case and never counts as success; a fixture success is reported
apart from the real PDFs.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time

import pymupdf

from .fidelity_evaluate import locate, reopen, sha, source_of
from .placed_evaluate import block, classify, placed_audit
from .roundtrip_evaluate import compare

MANIFEST = Path('evaluations/paragraph_frame_decoration/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
PLACED = Path('evaluations/paragraph_frame_placed')
FIDELITY = Path('evaluations/paragraph_frame_fidelity')
GROW = ('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で')
SHRINK = ('申請書は窓口で配布し', '申請書は配布し')
# Generated fixtures (tests/test_paragraph_frame_decoration.py): name -> (edits, expectation).
FIXTURES = {
    'D1': (dict(lengthen=GROW, shorten=SHRINK), 'a search box with a link on the line of a placed object: refused unapproved, moved approved'),
    'D2': (dict(lengthen=GROW), 'the box drawn with a colour set at its own level: moved, the colour replayed after it'),
    'D3': (dict(lengthen=GROW), 'a link reaching past the group: refused'),
    'D4': (dict(lengthen=GROW), 'a clip set at the group level: refused'),
    'D5': (dict(lengthen=GROW), 'other text on the line beyond the box: refused'),
}


def phase_freeze(args):
    placed = json.loads((PLACED / 'manifest.json').read_text())
    results = json.loads((PLACED / 'results.json').read_text())['results']['new']
    cases = []
    for case in placed['cases']:
        edits = {}
        for kind, (old, new) in case['edits'].items():
            first = results[case['id']][kind][0]
            reason = first.get('class') or ''
            if first['outcome'] == 'refused' and reason.startswith('placed object of a follower (new rule)'):
                edits[kind] = dict(old=old, new=new, pr98=reason.split(': ', 1)[1])
        if edits:
            cases.append(dict({k: case[k] for k in ('id', 'document', 'page', 'paragraph', 'selection', 'box', 'lines')}, edits=edits,
                              origin='paragraph_frame_placed (PR #98)'))
    fid = json.loads((FIDELITY / 'manifest.json').read_text())
    c02 = next(c for c in fid['cases'] if c['id'] == 'C02')
    lengthen = next(e for e in c02['edits'] if e['kind'] == 'lengthen')
    pr98 = json.loads((PLACED / 'results.json').read_text())['regression']['fidelity_pr92']
    cases.append(dict(id='C02-92', document=c02['document'], page=c02['page'], paragraph=c02['paragraph'], selection=c02['anchor'], box=c02['box'],
                      lines=len(c02['lines']), edits=dict(lengthen=dict(old=lengthen['old'], new=lengthen['new'],
                                                                        pr98='the line of an inline placed object of a following paragraph holds other '
                                                                             'content (filled-vector) that does not move with it')),
                      origin='paragraph_frame_fidelity C02 (PR #92); refused for its HP line since PR #98 (' + str(pr98['edit_differences'][1]) + ')'))
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(
        schema='paragraph-frame-decoration-manifest-1', baseline_commit='3c81178 (main after PR #98)', frozen_at=commit,
        policy=('Frozen before the implementation. Old B2 is main 3c81178; new B2 is this branch. Targets: every real-PDF step PR #98 refused '
                'for content on the line of a following paragraph\'s placed object or for its collision, and C02\'s PR #92 lengthen. '
                'Capacity and missing-glyph refusals stay refused and are not targets. A decoration moves only when its candidate id is '
                'approved explicitly; acknowledge_inference alone never approves one. A refusal ends its case and never counts as success; '
                'fixture successes are reported apart from real-PDF successes.'),
        procedure=['propose; record the listed decoration candidates', 'save without approving them (a step needing one must be refused)',
                   'save approving exactly the listed candidates', 'undo from the saved PDF alone, approving the candidates its proposal lists'],
        measures=('saved or refused (reason); the existing audits (safety, quality: indesign_evaluate.audit over the edited and following '
                  'paragraphs), structure, continued editing; placed audit (approved moves exact and nothing left, protected paint unchanged); '
                  'links (targets kept, moved clickable areas translated exactly, others unchanged); raster of the moved group (MuPDF, whole-pixel '
                  'translation, pixel for pixel); undo against the source'),
        documents=placed['documents'], fixtures={k: dict(edits=v[0], expected=v[1]) for k, v in FIXTURES.items()}, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(len(cases), 'cases;', sum(len(c['edits']) for c in cases), 'steps;', len(FIXTURES), 'fixtures')


def cli(code, args, output):
    started = time.perf_counter()
    run = subprocess.run([sys.executable, '-m', 'prototypes.paragraph_frame.generalization_cli'] + args + ['--output', str(Path(output).resolve())],
                         capture_output=True, cwd=code)
    last = [l for l in run.stderr.decode().splitlines() if l.strip()]
    return run.returncode, (last[-1] if last else ''), round(time.perf_counter() - started, 2)


def candidates_of(code, source, page, old, new, occurrence, folder):
    """The decoration candidates a proposal lists (none for a B2 without them, or a refused proposal)."""
    out = folder / 'proposal-only.json'
    if out.exists():
        out.unlink()
    rc, reason, _ = cli(code, ['propose', '--source', str(Path(source).resolve()), '--page', str(page), '--old', old, '--new', new,
                               '--occurrence', str(occurrence)], out)
    if rc:
        return [], reason
    proposal = json.loads(out.read_text())
    return (proposal['evidence'].get('decorations') or {}).get('candidates', []), None


def raster(source, output, page, moved, delta):
    """The moved groups' old area (source) against their new area (output), at a whole-pixel translation (MuPDF)."""
    if not moved or not delta:
        return None
    zoom = max(1, round(abs(delta) * 3)) / abs(delta)
    out = []
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        for m in moved:
            old = pymupdf.Rect(m['old']) + (-1, -1, 1, 1)
            new = old + (0, delta, 0, delta)
            pa = a[page - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=old, alpha=False)
            pb = b[page - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=new, alpha=False)
            same = (pa.width, pa.height) == (pb.width, pb.height)
            differing = sum(x != y for x, y in zip(pa.samples, pb.samples)) if same else None
            out.append(dict(region=[round(v, 2) for v in old], size_matches=same, differing_samples=differing, samples=len(pa.samples)))
    return dict(groups=out, ok=all(g['size_matches'] and g['differing_samples'] == 0 for g in out))


def links_audit(source, output, page, save):
    """Every link keeps its target; a moved link's area is its old one translated exactly."""
    moved = {round(a['old'][0], 2): a for a in (save.get('decorations_moved') or []) for a in a.get('annotations', [])}
    delta = save.get('follower_translation') if isinstance(save.get('follower_translation'), (int, float)) else 0.
    key = lambda l: (l.get('kind'), l.get('uri'), l.get('page'), str(l.get('to')), l.get('file'))
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        la, lb = a[page - 1].get_links(), b[page - 1].get_links()
    expected = Counter()
    for l in la:
        r = l['from']
        shift = delta if round(r.x0, 2) in moved else 0.
        expected[key(l) + (round(r.x0, 2), round(r.y0 + shift, 2), round(r.x1, 2), round(r.y1 + shift, 2))] += 1
    found = Counter(key(l) + (round(l['from'].x0, 2), round(l['from'].y0, 2), round(l['from'].x1, 2), round(l['from'].y1, 2)) for l in lb)
    return dict(links=len(la), moved=len(moved), ok=expected == found and len(la) == len(lb))


def approved_step(code, start, case, old, new, before, after, output, sub, grow, pitch, approve_listed):
    """One step: candidates, the unapproved save, and the approved save with its audits."""
    from .indesign_evaluate import audit
    from .tagged_evaluate import structure_audit
    occurrence = locate(start, case['page'], old, case['box'], code)
    listed, problem = candidates_of(code, start, case['page'], old, new, occurrence, sub)
    base = ['reedit', '--source', str(Path(start).resolve()), '--page', str(case['page']), '--old', old, '--new', new, '--acknowledge-inference',
            '--occurrence', str(occurrence)]
    record = dict(old=old, new=new, occurrence=occurrence, candidates=listed, proposal_refused=problem)
    plain = sub / 'unapproved.pdf'
    for p in (plain, plain.with_suffix('.proposal.json'), plain.with_suffix('.save.json')):
        if p.exists():
            p.unlink()
    rc, reason, seconds = cli(code, base, plain)
    record['unapproved'] = dict(outcome='refused' if rc else 'saved', reason=reason if rc else None, seconds=seconds)
    if not listed or not approve_listed:
        result = record['unapproved']
        if result['outcome'] == 'saved':
            for p in (plain, plain.with_suffix('.proposal.json'), plain.with_suffix('.save.json')):
                p.replace(output.parent / p.name.replace('unapproved', 'out'))
    else:
        args = base + sum((['--decoration', c['id']] for c in listed), [])
        rc, reason, seconds = cli(code, args, output)
        result = dict(outcome='refused' if rc else 'saved', reason=reason if rc else None, seconds=seconds, approved=[c['id'] for c in listed])
    record.update(outcome=result['outcome'], seconds=result['seconds'], approved=result.get('approved', []))
    if result['outcome'] != 'saved':
        record['reason'] = result['reason']
        record['class'] = classify(result['reason'])
        return record
    proposal = json.loads(output.with_suffix('.proposal.json').read_text())
    save = json.loads(output.with_suffix('.save.json').read_text())
    x0, y0, x1, y1 = case['box']
    added = max(0, len(proposal['lines']) - case['lines'])
    region = [x0 - 3, y0 - 3, x1 + 3, y1 + 3 + max(added, grow) * pitch]
    a = audit(start, output, case['page'], region, old, new, sub, occurrence=occurrence)
    record.update(lines=[l['text'] for l in proposal['lines']], grow=added, frame=proposal['evidence']['inferred'].get('frame'),
                  safety=a['safety'], quality=a['quality'], failed=a['failed'],
                  structure_failed=structure_audit(start, output, case['page'], after, [])['failed'],
                  continued_editing=reopen(code, output, case['page'], new, after),
                  save={k: save.get(k) for k in ('follower_translation', 'followers_repainted', 'placed_objects_moved', 'decorations_moved')},
                  placed=placed_audit(start, output, case['page'], save), links=links_audit(start, output, case['page'], save),
                  raster=raster(start, output, case['page'], save.get('decorations_moved') or [], save.get('follower_translation') or 0))
    return record


def run_case(code, case, source, root, approve_listed):
    rows = {}
    pitch = (case['box'][3] - case['box'][1]) / max(1, case['lines'])
    case = dict(case, box=block(source, case['page'], case.get('selection') or case['paragraph'][:6]))
    for kind, edit in case['edits'].items():
        old, new = edit['old'], edit['new']
        text = case['paragraph']
        changed = text.replace(old, new, 1)
        seq, prev, grow = [], source, 0
        for n, (a, b, before, after) in enumerate([(old, new, text, changed), (new, old, changed, text)]):
            sub = root / case['id'] / kind / f'{n + 1}'
            sub.mkdir(parents=True, exist_ok=True)
            output = sub / 'out.pdf'
            for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
                if p.exists():
                    p.unlink()
            try:
                r = approved_step(code, prev, case, a, b, before, after, output, sub, grow, pitch, approve_listed)
            except SystemExit as exc:  # the selection is not found in the PDF it starts from
                r = dict(old=a, new=b, outcome='not run', reason=str(exc)[:300])
            if r['outcome'] == 'saved' and n == 1:
                r['vs_source'] = compare(source, output, case['page'], case['box'], text, r['frame'], 2 * pitch, sub / 'vs-source', search=old)
            seq.append(r)
            print(case['id'], kind, n + 1, r['outcome'], len(r.get('candidates') or []), r.get('unapproved', {}).get('outcome'),
                  r.get('class') or r.get('reason') if r['outcome'] != 'saved' else (r['failed'], r['placed']['ok'], r['links']['ok'],
                                                                                      (r['raster'] or {}).get('ok')), flush=True)
            if r['outcome'] != 'saved':
                break
            prev = output
            grow = max(grow, r['grow'])
        rows[kind] = seq
    return rows


def fixtures(folder):
    """The generated fixture PDFs (test helpers)."""
    sys.path.insert(0, str(Path('tests').resolve()))
    import test_paragraph_frame_decoration as t
    folder.mkdir(parents=True, exist_ok=True)
    return {name: t.fixture(name, folder / name) for name in FIXTURES}


def phase_run(args):
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path(args.code).resolve()
    only = set(args.only or [])
    approve_listed = args.label != 'old'
    results = json.loads((root / 'results.json').read_text()) if (root / 'results.json').exists() and only else {}
    made = fixtures(root / 'fixtures')
    for name, spec in manifest['fixtures'].items():
        if only and name not in only:
            continue
        case = dict(id=name, document=name, page=1, paragraph='本年度の補助事業について、募集を行います。申請書は窓口で配布します。', selection='本年度の補助',
                    box=[39.5, 48.5, 216.5, 96.0], lines=3, edits={k: dict(old=o, new=n) for k, (o, n) in spec['edits'].items()})
        results[name] = run_case(code, case, made[name].resolve(), root, approve_listed)
    for case in manifest['cases']:
        if only and case['id'] not in only:
            continue
        results[case['id']] = run_case(code, case, source_of(case['document'], args).resolve(), root, approve_listed)
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')
    (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def summary(results, ids):
    s = Counter()
    for k in ids:
        for kind, seq in results.get(k, {}).items():
            for n, r in enumerate(seq):
                s['steps'] += 1
                s['with_candidates'] += bool(r.get('candidates'))
                if r.get('candidates'):
                    s['unapproved_refused'] += (r.get('unapproved') or {}).get('outcome') == 'refused'
                if r['outcome'] == 'not run':
                    s['not_run'] += 1
                    continue
                if r['outcome'] != 'saved':
                    s['refused'] += 1
                    s['refused: ' + r['class'].split(':')[0] + ((': ' + r['class'].split(': ', 2)[-1][:70]) if 'rule' in r['class'] else '')] += 1
                    continue
                s['saved'] += 1
                s['saved_moving_decorations'] += bool(r['save'].get('decorations_moved'))
                s['safety'] += bool(r['safety'])
                s['quality'] += bool(r['quality'])
                s['structure'] += not r['structure_failed']
                ce = r.get('continued_editing')
                s['continued_editing'] += bool(ce.get('ok') if isinstance(ce, dict) else ce)
                s['placed_audit'] += r['placed']['ok']
                s['links_audit'] += r['links']['ok']
                s['raster_audit'] += bool(r['raster'] and r['raster']['ok'])
                # The existing audit requires all non-text paint unchanged; the placed
                # and links audits split it into the approved moves and the rest.
                s['passed_with_move_audits'] += set(r['failed']) <= {'non_text_paint_identical'} and r['placed']['ok'] and r['links']['ok']
                if n == 1:
                    s['undo_page_identical'] += r['vs_source']['page_changed'] == 0
    return dict(s)


def phase_collect(args):
    manifest = json.loads(MANIFEST.read_text())
    labels = dict(x.split('=', 1) for x in args.roots)
    results = {label: json.loads((Path(root) / 'results.json').read_text()) for label, root in labels.items()}
    compact = lambda r: {k: (v[-300:] if k == 'reason' and v else v.get('ok') if k == 'continued_editing' and isinstance(v, dict) else v)
                         for k, v in r.items() if k not in ('lines', 'frame', 'seconds', 'occurrence')}
    groups = dict(fixtures=list(manifest['fixtures']), real=[c['id'] for c in manifest['cases']])
    out = dict(schema='paragraph-frame-decoration-results-1', manifest_sha256=sha(MANIFEST),
               tests=json.loads(Path(args.tests_json).read_text()) if args.tests_json else None,
               regression=json.loads(Path(args.regression_json).read_text()) if args.regression_json else None,
               summary={label: {g: summary(r, ids) for g, ids in groups.items()} for label, r in results.items()},
               results={label: {k: {kind: [compact(x) for x in seq] for kind, seq in rows.items()} for k, rows in r.items()}
                        for label, r in results.items()})
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
