"""One entry to the paragraph-frame evaluations: what to run for a change, running it in three tiers, and comparing with one baseline (issue #108).

  .venv/bin/python -m prototypes.paragraph_frame.eval_hub select [--changed <path> ... | --since <git rev>]
  .venv/bin/python -m prototypes.paragraph_frame.eval_hub run --tier normal|related|milestone [--changed ... | --since <rev> | --only <evaluation> ...]
      --root tmp/hub/<name> [--fusei <477.pdf>] [--pdfjs <pdf.js dir>] [--id-survey tmp/id-survey-final] [--tg-survey tmp/tg-survey] [--jobs 3]
  .venv/bin/python -m prototypes.paragraph_frame.eval_hub compare --root tmp/hub/<name>
  .venv/bin/python -m prototypes.paragraph_frame.eval_hub baseline --root tmp/hub/<name> --reason "<why>"

Tiers (docs/development-rules.md):

* ``normal``: the smoke set of real PDFs (smoke_evaluate) and the evaluations
  the impact map (evaluations/baseline/impact.json) lists as *direct* for the
  changed files;
* ``related``: those and the *related* evaluations of the changed files;
* ``milestone``: every evaluation.

Each evaluation is run as in its PR (same module and arguments), in
``<root>/<evaluation>``; ``timings.json`` records how long each took.
An evaluation whose input (a PDF, a survey) is missing or differs from the
hash its manifest froze is not run and is reported as ``input missing`` /
``input differs``: never as a success.

``compare`` reads the runs into common records (eval_records) and compares
them with evaluations/baseline/baseline.json, evaluation by evaluation (only
those run). ``baseline`` replaces the baseline's records of the evaluations
run and appends what changed, and why, to evaluations/baseline/changes.json;
the earlier records stay in git history.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from . import eval_records as er

BASE = Path('evaluations/baseline')
BASELINE, CHANGES, IMPACT = BASE / 'baseline.json', BASE / 'changes.json', BASE / 'impact.json'
PY = sys.executable

ME1 = ['C01', 'C02', 'C03', 'C05', 'C06', 'C12']
ME2 = ['C07', 'C08', 'C09', 'C10', 'C14', 'C15']
RT1 = ['C01', 'C02', 'C03', 'C04']
RT2 = ['C05', 'C06', 'C07', 'C08', 'C09', 'C10', 'C11', 'C12', 'C13', 'C14', 'C15', 'Q1', 'Q2', 'Q3']
# Each evaluation as its PR ran it: parts (folder, module, arguments), the inputs it needs.
NEW = ['--phase', 'run', '--label', 'new', '--code', '.']
EVALUATIONS = {
    'smoke': dict(parts=[('', 'smoke_evaluate', ['--phase', 'run', '--root', '{dir}', '--fusei', '{fusei}', '--pdfjs', '{pdfjs}', '--tier', '{smoke_tier}'])],
                  inputs=[], manifest='evaluations/smoke/manifest.json'),
    'chain': dict(parts=[('', 'chain_evaluate', NEW + ['--root', '{dir}', '--fusei', '{fusei}', '--pdfjs', '{pdfjs}'])], inputs=['fusei', 'pdfjs']),
    'separate': dict(parts=[('', 'separate_evaluate', NEW + ['--root', '{dir}', '--fusei', '{fusei}', '--pdfjs', '{pdfjs}'])], inputs=['fusei', 'pdfjs']),
    'decoration': dict(parts=[('', 'decoration_evaluate', NEW + ['--root', '{dir}', '--fusei', '{fusei}'])], inputs=['fusei']),
    'restore': dict(parts=[('', 'restore_evaluate', NEW + ['--root', '{dir}', '--fusei', '{fusei}'])], inputs=['fusei']),
    'placed': dict(parts=[('', 'placed_evaluate', NEW + ['--root', '{dir}', '--fusei', '{fusei}'])], inputs=['fusei']),
    'multiedit': dict(parts=[(p, 'multiedit_evaluate', NEW + ['--root', '{dir}', '--only', *only, '--fusei', '{fusei}', '--pdfjs', '{pdfjs}'])
                             for p, only in (('1', ME1), ('2', ME2))], inputs=['fusei', 'pdfjs']),
    'roundtrip': dict(parts=[(p, 'roundtrip_evaluate', NEW + ['--root', '{dir}', '--only', *only, '--fusei', '{fusei}', '--pdfjs', '{pdfjs}'])
                             for p, only in (('1', RT1), ('2', RT2))], inputs=['fusei', 'pdfjs']),
    'fidelity': dict(parts=[('', 'fidelity_evaluate', NEW + ['--root', '{dir}', '--fusei', '{fusei}', '--pdfjs', '{pdfjs}'])], inputs=['fusei', 'pdfjs']),
    # PR #87-#91 (generalization, continuity, column flow, InDesign, tagged), read through tagged_regression.
    'regression': dict(parts=[('reg87', 'generalization_evaluate', ['--root', '{dir}', '--phase', p]) for p in ('initial', 'audit', 'lifecycle')]
                       + [('reg88', 'continuity_evaluate', ['--root', '{dir}', '--phase', p]) for p in ('sequences', 'new')]
                       + [('reg89', 'column_flow_evaluate', ['--root', '{dir}', '--fusei', '{fusei}', '--phase', p])
                          for p in ('fixtures', 'sequences', 'singles', 'recognition')]
                       + [('reg90', 'indesign_evaluate', ['--root', '{dir}', '--fusei', '{fusei}', '--pdfjs', '{pdfjs}', '--phase', p,
                                                          '--survey', '{id_survey}']) for p in ('requests', 'samples')]
                       + [('tg-fid', 'tagged_evaluate', ['--root', '{dir}', '--fusei', '{fusei}', '--pdfjs', '{pdfjs}', '--phase', p,
                                                         '--survey', '{tg_survey}']) for p in ('requests', 'samples')],
                       inputs=['fusei', 'pdfjs', 'id_survey', 'tg_survey']),
}
EVALUATED = {'smoke': 'smoke', 'chain': 'chain', 'separate': 'separate', 'decoration': 'decoration', 'restore': 'restore',
             'placed': 'placed', 'multiedit': 'multiedit', 'roundtrip': 'roundtrip', 'fidelity': 'fidelity'}
REGRESSION = ('generalization', 'continuity', 'column_flow', 'indesign', 'tagged')


# --- what a change touches ---------------------------------------------------

def changed_files(args):
    if args.changed:
        return args.changed
    if args.since:
        run = subprocess.run(['git', 'diff', '--name-only', args.since], capture_output=True, text=True, check=True)
        return [l for l in run.stdout.splitlines() if l]
    return []


def impact(files):
    """(direct evaluations, related evaluations, milestone wanted, files the map does not cover)."""
    table = json.loads(IMPACT.read_text())['rules']
    direct, related, milestone, uncovered = set(), set(), [], []
    for f in files:
        hit = [r for r in table if fnmatch.fnmatch(f, r['pattern'])]
        if not hit:
            uncovered.append(f)
        for r in hit:
            direct |= set(r.get('direct', []))
            related |= set(r.get('related', []))
            if r.get('milestone'):
                milestone.append(f)
    return sorted(direct), sorted(related - direct), milestone, uncovered


def _importers(stem):
    """paragraph_frame modules that import ``stem``, directly or through others (and ``stem`` itself)."""
    import ast
    graph = {}
    for f in Path('prototypes/paragraph_frame').glob('*.py'):
        uses = set()
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ''
                if node.level == 1 and mod:
                    uses.add(mod.split('.')[0])
                elif (node.level == 1 and not mod) or mod == 'prototypes.paragraph_frame':
                    uses |= {a.name for a in node.names}
                elif mod.startswith('prototypes.paragraph_frame.'):
                    uses.add(mod.split('.')[2])
        graph[f.stem] = uses
    found, todo = {stem}, [stem]
    while todo:
        m = todo.pop()
        for k, uses in graph.items():
            if m in uses and k not in found:
                found.add(k)
                todo.append(k)
    return found


def focused_tests(files):
    """Test files for the changed modules (CLAUDE.md): their importers (for paragraph_frame, through the
    modules that import them), and the importers of changed test helpers."""
    tests = set()
    for f in files:
        p = Path(f)
        if p.parts[:1] == ('tests',) and p.suffix == '.py':
            tests.add(f)
            pattern = rf'^\s*(from|import) {p.stem}\b'
        elif p.parts[:1] == ('pdfeditor',) and p.suffix == '.py':
            pattern = rf'pdfeditor(\.| import .*){p.stem}\b'
        elif p.parts[:2] == ('prototypes', 'paragraph_frame') and p.suffix == '.py':
            pattern = rf'paragraph_frame(\.| import .*)({"|".join(sorted(_importers(p.stem)))})\b'
        else:
            continue
        run = subprocess.run(['grep', '-lE', pattern] + sorted(str(t) for t in Path('tests').glob('*.py')), capture_output=True, text=True)
        tests |= set(run.stdout.split())
    # A test helper's importers are run too (one level, as CLAUDE.md asks).
    for t in sorted(tests):
        run = subprocess.run(['grep', '-lE', rf'^\s*(from|import) {Path(t).stem}\b'] + sorted(str(x) for x in Path('tests').glob('*.py')),
                             capture_output=True, text=True)
        tests |= set(run.stdout.split())
    return sorted(tests)


def phase_select(args):
    files = changed_files(args)
    direct, related, milestone, uncovered = impact(files)
    out = dict(changed=files, focused_tests=focused_tests(files), normal=['smoke'] + direct, related=related,
               milestone_suggested=milestone, not_in_impact_map=uncovered)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


# --- inputs ------------------------------------------------------------------

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inputs_of(name, args):
    """(problem or None, the input values) of an evaluation: every input given and every document its manifest froze, unchanged."""
    values = dict(fusei=args.fusei, pdfjs=args.pdfjs, id_survey=args.id_survey, tg_survey=args.tg_survey)
    for k in EVALUATIONS[name]['inputs']:
        if not values.get(k) or not Path(values[k]).exists():
            return f'input missing: {k}', values
    manifest = er.MANIFESTS.get(name) or EVALUATIONS[name].get('manifest')
    if manifest and Path(manifest).exists():
        from types import SimpleNamespace
        from .fidelity_evaluate import source_of
        where = SimpleNamespace(fusei=args.fusei or '', pdfjs=args.pdfjs or '')
        for doc, d in (json.loads(Path(manifest).read_text()).get('documents') or {}).items():
            # (A manifest may name its source by a note, '(scratch download, not committed)': the given input stands for it.)
            path = Path(d['path']) if d.get('path') and Path(d['path']).exists() else source_of(doc, where)
            if not path.exists():
                return f'input missing: {doc}', values
            if d.get('sha256') and sha(path) != d['sha256']:
                return f'input differs: {doc}', values
    return None, values


# --- running -----------------------------------------------------------------

def run_one(name, root, args, progress=lambda name, parts: None):
    """Run an evaluation's parts: the phases of one folder in order, the folders at once."""
    spec = EVALUATIONS[name]
    folder = root / name
    problem, values = inputs_of(name, args)
    if problem:
        return dict(evaluation=name, status=problem, seconds=0)
    folder.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH='.', PDFENGINE_REG_ROOT=str(folder), PDFENGINE_TG_SURVEY=str(values.get('tg_survey') or ''))
    started, parts = time.perf_counter(), []

    def part(p):
        sub, module, argv = p
        target = folder / sub if sub else folder
        target.mkdir(parents=True, exist_ok=True)
        fill = dict(values, dir=str(target), smoke_tier='normal' if args.tier == 'normal' else 'related')
        cmd = [PY, '-u', '-m', f'prototypes.paragraph_frame.{module}'] + [a.format(**{k: (v or '') for k, v in fill.items()}) for a in argv]
        t = time.perf_counter()
        with open(target / f'{module}.log', 'a') as log:
            rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env).returncode
        done = dict(part=sub or name, module=module, phase=next((a for a, b in zip(argv[1:], argv) if b == '--phase'), None), returncode=rc,
                    seconds=round(time.perf_counter() - t, 1))
        parts.append(done)
        progress(name, parts)
        return done

    folders = {}
    for p in spec['parts']:
        folders.setdefault(p[0], []).append(p)
    with ThreadPoolExecutor(max_workers=len(folders)) as pool:
        list(pool.map(lambda ps: [part(p) for p in ps], folders.values()))
    if name == 'regression':
        out = folder / 'regression.json'
        cmd = [PY, '-m', 'prototypes.paragraph_frame.tagged_regression', str(out), str(folder / 'tg-fid'), values['fusei'], values['pdfjs']]
        with open(folder / 'tagged_regression.log', 'w') as log:
            parts.append(dict(part='compare', module='tagged_regression', seconds=0,
                              returncode=subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env).returncode))
    failed = [p for p in parts if p['returncode']]
    return dict(evaluation=name, status='failed: ' + ', '.join(p['part'] for p in failed) if failed else 'ran',
                seconds=round(time.perf_counter() - started, 1), parts=parts)


def selected(args):
    if args.only:
        return list(args.only)
    if args.tier == 'milestone':
        return list(EVALUATIONS)
    files = changed_files(args)
    direct, related, _, _ = impact(files)
    return ['smoke'] + direct + (related if args.tier == 'related' else [])


def phase_run(args):
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    names = [n for n in dict.fromkeys(selected(args)) if n in EVALUATIONS]
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(['git', 'status', '--porcelain', '--', 'prototypes', 'pdfeditor'], capture_output=True, text=True).stdout.strip())
    started = time.perf_counter()
    timings = dict(tier=args.tier, changed=changed_files(args), code=commit, uncommitted_code_changes=dirty, evaluations=[], running=names)

    def write():
        # Written as parts finish: a run that is stopped leaves what it measured.
        timings['seconds'] = round(time.perf_counter() - started, 1)
        (root / 'timings.json').write_text(json.dumps(timings, ensure_ascii=False, indent=1, default=str) + '\n')

    def partial(name, parts):
        timings.setdefault('parts_done', {})[name] = list(parts)
        write()

    def one(n):
        r = run_one(n, root, args, partial)
        timings['evaluations'].append(r)
        timings['running'] = [x for x in timings['running'] if x != n]
        write()
        return r

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        runs = list(pool.map(one, names))
    write()
    for r in runs:
        print(r['evaluation'], r['status'], r['seconds'], 's', flush=True)
    print('total', timings['seconds'], 's')


# --- records, comparison, baseline -------------------------------------------

def records_of(root):
    """The common records of the evaluations run in ``root`` (and their statuses)."""
    root = Path(root)
    timings = json.loads((root / 'timings.json').read_text()) if (root / 'timings.json').exists() else dict(evaluations=[])
    status = {r['evaluation']: r['status'] for r in timings['evaluations']}
    out = []
    for name in status:
        folder = root / name
        if name == 'regression':
            if (folder / 'regression.json').exists():
                out += er.regression_records(json.loads((folder / 'regression.json').read_text()))
            continue
        files = sorted(folder.glob('*/results.json')) if len(EVALUATIONS[name]['parts']) > 1 else [folder / 'results.json']
        results = {}
        for f in files:
            if f.exists():
                results.update(json.loads(f.read_text()))
        if name == 'smoke':
            from .smoke_evaluate import records as smoke_records
            out += smoke_records(results)
        else:
            out += er.case_records(name, results)
    return out, status


def evaluated(names):
    """The record evaluations that evaluation runs produce."""
    out = set()
    for n in names:
        out |= set(REGRESSION) if n == 'regression' else {EVALUATED.get(n, n)}
    return out


def phase_compare(args):
    current, status = records_of(args.root)
    base = json.loads(BASELINE.read_text())
    ran = [n for n, s in status.items() if s == 'ran']
    result = er.compare(base['records'], current, evaluated(ran))
    result.update(baseline=dict(created=base['created'], codes=sorted({v['code'] for v in base['evaluations'].values()})), statuses=status,
                  not_compared=sorted(n for n, s in status.items() if s != 'ran'),
                  summary=er.summary(current))
    out = Path(args.root) / 'compare.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str) + '\n')
    print('compared', result['compared'], 'records of', ', '.join(result['evaluations']), '| changed', len(result['changed']),
          '| only in baseline', len(result['only_baseline']), '| only now', len(result['only_current']), '| cases not run', len(result['cases_not_run']))
    for c in result['changed'][:40]:
        print(' ', json.dumps(c, ensure_ascii=False, default=str)[:400])
    if result['not_compared']:
        print('not compared:', {n: status[n] for n in result['not_compared']})
    return result


def phase_baseline(args):
    current, status = records_of(args.root)
    ran = evaluated([n for n, s in status.items() if s == 'ran'])
    old = json.loads(BASELINE.read_text()) if BASELINE.exists() else dict(records=[], evaluations={})
    diff = er.compare(old['records'], current, ran)
    timings = json.loads((Path(args.root) / 'timings.json').read_text())
    # The code the runs were made with (timings.json), and the evaluators' and inputs' versions.
    commit = timings.get('code') or subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    modules = sorted({m for n in status for _, m, _ in EVALUATIONS[n]['parts']} | {'eval_records', 'tagged_regression'})
    blob = lambda m: subprocess.run(['git', 'hash-object', f'prototypes/paragraph_frame/{m}.py'], capture_output=True, text=True).stdout.strip()
    seconds = {r['evaluation']: r['seconds'] for r in timings['evaluations']}
    kept = [r for r in old['records'] if r['evaluation'] not in ran]
    new = dict(schema='paragraph-frame-baseline-1', created=time.strftime('%Y-%m-%d'), note=timings.get('note'),
               evaluations={**old.get('evaluations', {}), **{e: dict(code=commit, source=str(args.root)) for e in sorted(ran)}},
               evaluators={**old.get('evaluators', {}), **{m: blob(m) for m in modules}},
               inputs={**old.get('inputs', {}), **({'osaka_fusei_477': sha(args.fusei)} if args.fusei else {})},
               seconds={**old.get('seconds', {}), **{n: seconds[n] for n in status if status[n] == 'ran'}},
               records=sorted(kept + [r for r in current if r['evaluation'] in ran], key=lambda r: (r['evaluation'], r['case'], r['step'])))
    # One record per line: a baseline change reads as a line diff.
    lines = ',\n'.join(json.dumps(r, ensure_ascii=False, separators=(',', ':'), default=str) for r in new['records'])
    head = json.dumps({k: v for k, v in new.items() if k != 'records'}, ensure_ascii=False, indent=1)
    BASELINE.write_text(head[:-2] + ',\n "records": [\n' + lines + '\n]}\n')
    changes = json.loads(CHANGES.read_text()) if CHANGES.exists() else []
    listed = lambda keys: keys if len(keys) <= 100 else f'{len(keys)} records'
    changes.append(dict(date=new['created'], code=commit, reason=args.reason, evaluations=sorted(ran), compared=diff['compared'],
                        changed=diff['changed'], removed=listed(diff['only_baseline']), added=listed(diff['only_current']),
                        design_decision_needed=args.design_decision))
    CHANGES.write_text(json.dumps(changes, ensure_ascii=False, indent=1, default=str) + '\n')
    print('baseline', commit, 'evaluations', sorted(ran), 'changed', len(diff['changed']), 'added', len(diff['only_current']),
          'removed', len(diff['only_baseline']))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('phase', choices=('select', 'run', 'compare', 'baseline'))
    p.add_argument('--tier', choices=('normal', 'related', 'milestone'), default='normal')
    p.add_argument('--changed', nargs='*')
    p.add_argument('--since')
    p.add_argument('--only', nargs='*')
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--id-survey', default='tmp/id-survey-final')
    p.add_argument('--tg-survey', default='tmp/tg-survey')
    p.add_argument('--jobs', type=int, default=3)
    p.add_argument('--reason')
    p.add_argument('--design-decision', action='store_true', help='the baseline change needs a design decision (recorded)')
    args = p.parse_args()
    dict(select=phase_select, run=phase_run, compare=phase_compare, baseline=phase_baseline)[args.phase](args)


if __name__ == '__main__':
    main()
