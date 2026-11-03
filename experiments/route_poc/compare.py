"""The comparison table of the route PoC against the current approach (issue #122).

  .venv/bin/python -m experiments.route_poc.compare --poc <root>/results.json

Reads the fixed cases (cases.json: the current approach's committed result of
each edit), the committed evaluation results for the candidates the current
approach had approved, and the PoC's run (evaluate.py). Writes
experiments/route_poc/comparison.json (compact: no PDF, no path) and prints
the table and the counts.

Per approach and case: step 1 (the edit) and step 2 (the edit back from the
saved PDF alone). saved = step 1 saved; practical = step 1 practical;
re-edit = step 2 saved; restore = step 2 restores the source; refused = step 1
refused. A check not made is null and never counted as a success.
User operations = click + select + type (3) + one per approval given.
"""
import argparse
import json
from pathlib import Path

from .freeze import CHOSEN

CASES = Path('experiments/route_poc/cases.json')
OUT = Path('experiments/route_poc/comparison.json')
SOURCES = dict(panel=('evaluations/paragraph_panel/results.json', lambda d: d['runs']['new']['results']),
               clip=('evaluations/paragraph_clip/results.json', lambda d: d['results']),
               remaining=('evaluations/paragraph_remaining/results.json', lambda d: d['runs']['new']['results']),
               smoke=('evaluations/smoke/results.json', lambda d: d['results']))


def _approved(kind, sid):
    """Per step, the candidate ids the current approach's evaluation approved (None: not recorded)."""
    path, get = SOURCES[kind]
    seq = get(json.loads(Path(path).read_text())).get(sid) or []
    out = []
    for x in seq:
        a = x.get('approved') if isinstance(x, dict) else None
        out.append(None if a is None else [i for v in a.values() for i in (v or [])])
    return out


def _current(case, kind, sid):
    steps = case['current']['steps']
    approved = _approved(kind, sid)
    rows = []
    for n, s in enumerate(steps):
        ids = approved[n] if n < len(approved) else None
        rows.append(dict(outcome=s['outcome'], mode=s['mode'], practical=s['tiers']['practical'], restore=s['tiers']['restore'],
                         approvals=ids, operations=None if ids is None else 3 + len(ids),
                         not_practical_because=s['not_practical_because'], reason=(s['reason'] or '')[-160:] or None))
    return rows


def _poc(seq):
    rows = []
    for s in seq:
        c = s.get('checks') or {}
        failed = []
        if c:
            p = c['protection']
            failed += [f'protection: {k}' for k in ('glyphs', 'drawings', 'images', 'annotations', 'links', 'other_pages', 'pixels') if p[k] is False]
            if c['marked_content']['result'] is False:
                failed.append('marked content: new text untagged on a tagged page' if c['marked_content']['tagged'] else 'marked content: MCIDs lost')
            if c['search_copy']['result'] is False:
                failed.append('search and copy: ' + ', '.join(c['search_copy']['failed']))
            if c['font_kept'] is False:
                failed.append('font: ' + s['font'])
            if c['placement']['result'] is False:
                failed.append('placement')
            if c['text_replaced'] is False:
                failed.append('text not replaced')
        rows.append(dict(outcome=s['outcome'], unapproved=s.get('unapproved'), approvals=s.get('approvals'),
                         operations=s.get('operations'), practical=c.get('practical') if c else (False if s['outcome'] == 'refused' else None),
                         failed=failed or None, search_copy=c.get('search_copy', {}).get('result') if c else None,
                         pixel_counts=c['protection']['pixel_counts'] if c else None, lines=s.get('lines'),
                         restore=s.get('restore'), reason=s.get('reason'), seconds=s.get('seconds')))
    return rows


def tiers(steps):
    """Case-level tiers from the step rows (None: not reached or not measured)."""
    one = steps[0] if steps else {}
    two = steps[1] if len(steps) > 1 else None
    restore = None
    if two and two['outcome'] == 'saved':
        r = two.get('restore')
        restore = r['result'] if isinstance(r, dict) else r
    return dict(saved=one.get('outcome') == 'saved', practical=one.get('practical') is True,
                practical_both=one.get('practical') is True and bool(two) and two.get('practical') is True,
                reedit=None if one.get('outcome') != 'saved' else bool(two) and two['outcome'] == 'saved',
                restore=restore, refused=one.get('outcome') == 'refused')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--poc', required=True, help='the results.json evaluate.py wrote')
    args = p.parse_args()
    cases = {c['id']: c for c in json.loads(CASES.read_text())['cases']}
    poc = json.loads(Path(args.poc).read_text())
    table = {}
    for cid, kind, sid, _ in CHOSEN:
        cur, new = _current(cases[cid], kind, sid), _poc(poc.get(cid, []))
        table[cid] = dict(kind=cases[cid]['kind'], current=dict(steps=cur, tiers=tiers(cur)), poc=dict(steps=new, tiers=tiers(new)))
    counts = {a: {t: sum(1 for x in table.values() if x[a]['tiers'][t] is True) for t in ('saved', 'practical', 'practical_both', 'reedit', 'restore', 'refused')}
              for a in ('current', 'poc')}
    for a in counts:
        counts[a]['unverified'] = sum(1 for x in table.values() for s in x[a]['steps'] if s['outcome'] == 'saved' and s['practical'] is None)
        counts[a]['operations_saved_step1'] = sorted(x[a]['steps'][0]['operations'] for x in table.values()
                                                     if x[a]['steps'] and x[a]['steps'][0]['outcome'] == 'saved' and x[a]['steps'][0]['operations'] is not None)
    OUT.write_text(json.dumps(dict(schema='route-poc-comparison-1', cases_frozen_at=json.loads(CASES.read_text())['frozen_at'],
                                   counts=counts, cases=table), ensure_ascii=False, indent=1) + '\n')
    mark = lambda v: '—' if v is None else '○' if v else '×'
    print('| case | kind | current: saved / practical / re-edit / restore | ops | PoC: saved / practical / re-edit / restore | ops | PoC: why not practical / refused |')
    print('|---|---|---|---|---|---|---|')
    for cid, x in table.items():
        c, n = x['current'], x['poc']
        why = n['steps'][0]['reason'] if n['steps'][0]['outcome'] != 'saved' else '; '.join(n['steps'][0]['failed'] or []) or '—'
        if len(n['steps']) > 1 and n['steps'][1]['outcome'] != 'saved':
            why += f"; edit back refused: {n['steps'][1]['reason']}"
        ops = lambda s: '—' if not s or s[0]['outcome'] != 'saved' or s[0]['operations'] is None else s[0]['operations']
        print(f"| {cid} | {x['kind']} | {' / '.join(mark(c['tiers'][t]) if c['tiers']['saved'] or t == 'saved' else '—' for t in ('saved', 'practical', 'reedit', 'restore'))} "
              f"| {ops(c['steps'])} | {' / '.join(mark(n['tiers'][t]) if n['tiers']['saved'] or t == 'saved' else '—' for t in ('saved', 'practical', 'reedit', 'restore'))} "
              f"| {ops(n['steps'])} | {why} |")
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == '__main__':
    main()
