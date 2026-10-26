"""Several edits in one paragraph, in either order, undone one by one (issue #95).

  .venv/bin/python -m prototypes.paragraph_frame.multiedit_evaluate --phase freeze
  .venv/bin/python -m prototypes.paragraph_frame.multiedit_evaluate --phase run --label old|new --code <tree> --root tmp/<dir> \
      --fusei <477.pdf> --pdfjs <dir> [--only C06 C09]
  .venv/bin/python -m prototypes.paragraph_frame.multiedit_evaluate --phase collect --roots old=tmp/<a> new=tmp/<b>

``freeze`` (run before the implementation) takes, from the frozen paragraphs
of the layout-fidelity evaluation (PR #92), every pair of its edits (same
length, shorten, lengthen, lengthen with a wrap) at disjoint places of which
each stays selectable once after the other, and records whether the two touch
a common source line. The pair PR #94 evaluated as its path is marked.

``run`` saves, with the B2 of ``--code``, each step proposed from the PDF the
previous step saved, alone:

* forward: A, then B, then undo A, then undo B;
* reverse: B (B alone), then A, then undo B;

and records the existing audits of each step (roundtrip_evaluate.step) and,
engine-independently (PyMuPDF and Poppler only, roundtrip_evaluate.compare):

* ``path_b``: A, B, undo A against B alone (PR #94's path);
* ``path_a``: B, A, undo B against A alone (the first forward step);
* ``order``: A then B against B then A;
* ``full_return``: A, B, undo A, undo B against the source.

A refused step ends its sequence and the comparisons that need it are not
made; refusals never count as success.
"""
import argparse
import itertools
import json
from pathlib import Path
import subprocess

from .fidelity_evaluate import sha, source_of
from .roundtrip_evaluate import FIDELITY, KINDS, compare, step

MANIFEST = Path('evaluations/paragraph_frame_multiedit/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
ROUNDTRIP = Path('evaluations/paragraph_frame_roundtrip/manifest.json')


def line_ranges(case):
    """Character ranges of the source lines in the paragraph text."""
    out, at = [], 0
    for line in case['lines']:
        text = line['text'].rstrip()
        i = case['paragraph'].find(text, at)
        if i < 0:
            raise SystemExit(f"{case['id']}: line not found in the paragraph")
        out.append((i, i + len(text)))
        at = i + len(text)
    return out


def phase_freeze(args):
    fid = json.loads(FIDELITY.read_text())
    baseline = {c['id']: c['path'] for c in json.loads(ROUNDTRIP.read_text())['cases']}
    cases = []
    for c in fid['cases']:
        p = c['paragraph']
        edits = [e for e in c['edits'] if e['kind'] in KINDS]
        ranges = line_ranges(c)
        lines_of = lambda s, e: {k for k, (a, b) in enumerate(ranges) if a < e and s < b}
        follows = lambda x, y: (p.replace(x['old'], x['new'], 1).count(y['old']) == 1
                                and p.replace(x['old'], x['new'], 1).replace(y['old'], y['new'], 1).count(x['new']) == 1)
        pairs = []
        for a, b in itertools.combinations(edits, 2):
            pa, pb = p.find(a['old']), p.find(b['old'])
            if not (pa + len(a['old']) <= pb or pb + len(b['old']) <= pa) or not follows(a, b) or not follows(b, a):
                continue
            la, lb = lines_of(pa, pa + len(a['old'])), lines_of(pb, pb + len(b['old']))
            path = baseline.get(c['id'])
            pairs.append(dict(id=f"{a['kind']}+{b['kind']}", a=dict(name=a['kind'], old=a['old'], new=a['new']),
                              b=dict(name=b['kind'], old=b['old'], new=b['new']), lines=dict(a=sorted(la), b=sorted(lb)),
                              relation='same-line' if la & lb else 'other-lines',
                              pr94_path=bool(path and (path['a']['old'], path['b']['old']) == (a['old'], b['old']))))
        if pairs:
            cases.append(dict(id=c['id'], document=c['document'], page=c['page'], purpose=c['purpose'], paragraph=p, box=c['box'],
                              lines=len(c['lines']), pairs=pairs, origin='layout-fidelity manifest (PR #92)'))
    documents = {c['document']: dict(sha256=sha(source_of(c['document'], args))) for c in cases}
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(schema='paragraph-frame-multiedit-manifest-1', baseline_commit='2e2ec8b (main after PR #94)', frozen_at=commit,
                    policy=('Frozen before the implementation. Old B2 is main 2e2ec8b; new B2 is this branch. Every pair of the fixed '
                            'edits at disjoint places, valid in both orders. Each step is proposed from the PDF the previous step saved, '
                            'alone. A refused step ends its sequence; refusals never count as success.'),
                    sequences='forward: A, B, undo A, undo B; reverse: B, A, undo B',
                    measures=('per step: saved, safety and quality (indesign_evaluate.audit against the previous PDF), structure, continued '
                              'editing; path_b: A,B,undo A against B alone; path_a: B,A,undo B against A alone; order: A,B against B,A; '
                              'full_return: A,B,undo A,undo B against the source; each: paragraph glyphs at the reference origin (0.01 pt), '
                              'font and size, lines and breaks, glyphs outside the paragraph, page glyphs, page text of MuPDF and Poppler'),
                    documents=documents, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    pairs = [q for c in cases for q in c['pairs']]
    print(len(cases), 'cases;', len(pairs), 'pairs;', sum(q['relation'] == 'same-line' for q in pairs), 'same-line;',
          sum(q['pr94_path'] for q in pairs), 'PR #94 paths')


def run_sequence(code, case, source, plan, folder, pitch):
    """Steps ``plan`` [(old, new, before, after)] from ``source``; the saved
    records and output paths of the steps made (a refusal ends it)."""
    seq, outputs, prev, grow = [], [], source, 0
    for n, (old, new, before, after) in enumerate(plan):
        sub = folder / f'{n + 1}'
        sub.mkdir(parents=True, exist_ok=True)
        output = sub / 'out.pdf'
        for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
            if p.exists():
                p.unlink()
        r = step(code, prev, case, old, new, before, after, output, sub, grow, pitch)
        seq.append(r)
        print(case['id'], folder.parent.name, folder.name, n + 1, r['outcome'], r.get('failed') or r.get('reason', '')[-90:], flush=True)
        if r['outcome'] != 'saved':
            break
        grow = max(grow, r['grow'])
        outputs.append(output)
        prev = output
    return seq, outputs, grow


def phase_run(args):
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path(args.code).resolve()
    only = set(args.only or [])
    results = json.loads((root / 'results.json').read_text()) if (root / 'results.json').exists() and only else {}
    for case in manifest['cases']:
        if only and case['id'] not in only:
            continue
        source = source_of(case['document'], args).resolve()
        pitch = (case['box'][3] - case['box'][1]) / max(1, case['lines'])
        rows = {}
        for pair in case['pairs']:
            a, b, text = pair['a'], pair['b'], case['paragraph']
            ta = text.replace(a['old'], a['new'], 1)
            tab = ta.replace(b['old'], b['new'], 1)
            tb = text.replace(b['old'], b['new'], 1)
            forward = [(a['old'], a['new'], text, ta), (b['old'], b['new'], ta, tab), (a['new'], a['old'], tab, tb), (b['new'], b['old'], tb, text)]
            reverse = [(b['old'], b['new'], text, tb), (a['old'], a['new'], tb, tab), (b['new'], b['old'], tab, ta)]
            base = root / case['id'] / pair['id']
            fseq, fout, fgrow = run_sequence(code, case, source, forward, base / 'forward', pitch)
            rseq, rout, rgrow = run_sequence(code, case, source, reverse, base / 'reverse', pitch)
            grow = max(fgrow, rgrow) * pitch
            frame = lambda seq: next((r['frame'] for r in seq if r.get('frame')), None)
            got = dict(forward=fseq, reverse=rseq)
            if len(fout) >= 3 and rout:
                got['path_b'] = compare(rout[0], fout[2], case['page'], case['box'], tb, frame(fseq), grow, base / 'path_b')
            if len(rout) >= 3 and fout:
                got['path_a'] = compare(fout[0], rout[2], case['page'], case['box'], ta, frame(rseq), grow, base / 'path_a')
            if len(fout) >= 2 and len(rout) >= 2:
                got['order'] = compare(fout[1], rout[1], case['page'], case['box'], tab, frame(fseq), grow, base / 'order')
            if len(fout) == 4:
                got['full_return'] = compare(source, fout[3], case['page'], case['box'], text, frame(fseq), grow, base / 'full_return',
                                             search=b['old'])
            print(case['id'], pair['id'], {k: (v['paragraph']['kept'], v['paragraph']['unchanged'], v['page_changed'])
                                           for k, v in got.items() if k in ('path_b', 'path_a', 'order', 'full_return')}, flush=True)
            rows[pair['id']] = got
        results[case['id']] = rows
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


COMPARISONS = ('path_b', 'path_a', 'order', 'full_return')


def summary(results, manifest, select=lambda pair: True):
    """Totals of one label over the pairs ``select`` accepts."""
    pairs = {(c['id'], q['id']): q for c in manifest['cases'] for q in c['pairs']}
    s = dict(pairs=0, steps=0, saved=0, refused=0, safety=0, quality=0, structure=0, continued_editing=0)
    for k in COMPARISONS:
        s.update({f'{k}_made': 0, f'{k}_unchanged': 0, f'{k}_kept': 0, f'{k}_breaks': 0, f'{k}_breaks_kept': 0, f'{k}_restyled': 0,
                  f'{k}_outside_identical': 0, f'{k}_page_identical': 0, f'{k}_text_identical_both': 0})
    for case, rows in results.items():
        for pid, got in rows.items():
            if not select(pairs[(case, pid)]):
                continue
            s['pairs'] += 1
            for r in got['forward'] + got['reverse']:
                s['steps'] += 1
                if r['outcome'] != 'saved':
                    s['refused'] += 1
                    continue
                s['saved'] += 1
                s['safety'] += bool(r['safety'])
                s['quality'] += bool(r['quality'])
                s['structure'] += not r['structure_failed']
                ce = r.get('continued_editing')
                s['continued_editing'] += bool(ce.get('ok') if isinstance(ce, dict) else ce)
            for k in COMPARISONS:
                if k not in got:
                    continue
                t, p = got[k], got[k]['paragraph']
                s[f'{k}_made'] += 1
                s[f'{k}_unchanged'] += p['unchanged']
                s[f'{k}_kept'] += p['kept']
                s[f'{k}_breaks'] += p['source_breaks_outside_edit']
                s[f'{k}_breaks_kept'] += p['breaks_kept']
                s[f'{k}_restyled'] += p['restyled']
                s[f'{k}_outside_identical'] += t['outside_changed'] == 0
                s[f'{k}_page_identical'] += t['page_changed'] == 0
                s[f'{k}_text_identical_both'] += all(t['text_identical'].values())
    return s


def compact(got):
    step = lambda r: {k: (v[-300:] if k == 'reason' else v.get('ok') if k == 'continued_editing' and isinstance(v, dict) else v)
                      for k, v in r.items() if k not in ('lines', 'frame', 'seconds', 'save', 'occurrence')}
    return {k: [step(r) for r in v] if k in ('forward', 'reverse') else v for k, v in got.items()}


def phase_collect(args):
    manifest = json.loads(MANIFEST.read_text())
    labels = dict(x.split('=', 1) for x in args.roots)
    results = {label: json.loads((Path(root) / 'results.json').read_text()) for label, root in labels.items()}
    groups = dict(all=lambda q: True, pr94_path=lambda q: q['pr94_path'], same_line=lambda q: q['relation'] == 'same-line',
                  other_lines=lambda q: q['relation'] == 'other-lines')
    out = dict(schema='paragraph-frame-multiedit-results-1', manifest_sha256=sha(MANIFEST),
               tests=json.loads(Path(args.tests_json).read_text()) if args.tests_json else None,
               regression=json.loads(Path(args.regression_json).read_text()) if args.regression_json else None,
               summary={label: {g: summary(r, manifest, f) for g, f in groups.items()} for label, r in results.items()},
               results={label: {case: {pid: compact(got) for pid, got in rows.items()} for case, rows in r.items()} for label, r in results.items()})
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    for label, groups_ in out['summary'].items():
        for g, s in groups_.items():
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
