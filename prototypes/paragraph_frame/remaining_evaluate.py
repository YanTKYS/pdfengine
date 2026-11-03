"""The lengthening edits still refused on the current main, with every kind of candidate approved (issue #120).

  .venv/bin/python -m prototypes.paragraph_frame.remaining_evaluate --phase run --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir> [--only <id> ...]
  .venv/bin/python -m prototypes.paragraph_frame.remaining_evaluate --phase collect --roots main=<dir> new=<dir>

The cases are the 25 of the separate evaluation (evaluations/paragraph_frame_separate,
frozen for PR #106: R02, R13, R18 saved, 22 refused then). ``run`` takes each
through the edit and the edit back from the saved PDF alone with this tree's B2,
approving exactly the candidates its proposal lists of every kind (chain
elements, line decorations, panel and clip extensions: panel_evaluate.chain_step
as the clip evaluation runs it). A refused step is diagnosed from its proposal:
what stops the paragraph's frame (the protected paint, container, clip or page
edge at its bottom), how far the growth needs to go, and why each kind of
candidate is not offered. A missing input is never a success; a case not run is
never counted as refused.
"""
import argparse
import hashlib
import json
from pathlib import Path

from . import eval_records as er
from .fidelity_evaluate import source_of

SEPARATE = Path('evaluations/paragraph_frame_separate/manifest.json')
OUT = Path('evaluations/paragraph_remaining')
RESULTS = OUT / 'results.json'


def diagnose(source, case, old, new):
    """Why a step is refused, from its proposal (no approval): the frame's bottom and what stops it, the growth the
    edit needs, and each kind of candidate (offered, or why not)."""
    from .auto import propose
    from .engine import Refused
    try:
        p = propose(str(source), case['page'], old, new)
    except Refused as exc:
        return dict(proposal='refused', reason=str(exc)[-300:])
    ev = p['evidence']
    frame = ev['inferred']['frame']
    rows = ev['observed']['lines'][:p['source_lines']]
    deepest = max(r['bbox'][3] for r in rows)
    growth = p['lines'][-1]['baseline'] - rows[-1]['baseline']
    # What lies at the frame's bottom within its width: the paint, container or clip that stops it.
    stop = sorted((o for o in ev['observed']['obstacles'] if o['bounds'][0] < frame[2] and o['bounds'][2] > frame[0]
                   and abs(o['bounds'][1] - frame[3]) < 3 or (o['kind'] == 'container' and abs(o['bounds'][3] - frame[3]) < 1)),
                  key=lambda o: abs(o['bounds'][1] - frame[3]))
    kinds = {}
    for k in ('chain', 'decorations', 'panels', 'clips'):
        x = ev.get(k)
        if x is None:
            continue
        kinds[k] = dict(candidates=len(x.get('candidates') or x.get('units') or []), unavailable=x.get('unavailable'))
    return dict(proposal='made', frame=[round(v, 2) for v in frame], frame_bottom=ev['inferred'].get('frame_bottom'),
                source_lines=p['source_lines'], proposed_lines=len(p['lines']), growth=round(growth, 2),
                needs_to=round(deepest + max(growth, 0), 2), stops=[dict(kind=o['kind'], bounds=[round(v, 2) for v in o['bounds']],
                                                                           **({'drawn': o['drawn']} if o.get('drawn') else {})) for o in stop[:3]],
                containers=[[round(v, 2) for v in b] for b in ev['conditions'].get('containers', [])], candidates=kinds)


def phase_run(args):
    from . import chain_evaluate as ce
    from . import clip_evaluate
    from . import rows_evaluate as rows
    manifest = json.loads(SEPARATE.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path('.').resolve()
    only = set(args.only or [])
    path = root / 'results.json'
    results = json.loads(path.read_text()) if path.exists() and only else {}
    for case in manifest['cases']:
        if only and case['id'] not in only:
            continue
        source = source_of(case['document'], args)
        if not source.exists():
            results[case['id']] = [dict(outcome='input missing')]
            continue
        source = source.resolve()
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['documents'][case['document']]['sha256']:
            results[case['id']] = [dict(outcome='input differs')]
            continue
        ce.block = lambda source, page, selection, box=case['box']: box
        ce.locate = rows.make_locate(code)
        ce.chain_step = clip_evaluate.chain_step
        try:
            seq = ce.run_case(code, case, source, root)
        except Exception as exc:  # an evaluator error ends this case only, and is reported
            seq = [dict(old=case['edit']['old'], new=case['edit']['new'], outcome='not run', reason=f'evaluator error: {type(exc).__name__}: {exc}'[:300])]
        start = source
        for n, x in enumerate(seq):
            if x.get('outcome') == 'refused':
                x['diagnosis'] = diagnose(start, case, x['old'], x['new'])
            start = root / case['id'] / f'{n + 1}' / 'out.pdf'
        results[case['id']] = seq
        path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')
        print(case['id'], [x.get('outcome') for x in seq], (seq[-1].get('reason') or '')[-140:], flush=True)


def records(results):
    from . import smoke_evaluate
    out = []
    for cid, seq in results.items():
        for n, x in enumerate(seq):
            if x.get('outcome') in ('input missing', 'input differs'):
                out.append(dict(evaluation='remaining', case=cid, step=str(n + 1), group='real', mode='faithful', outcome=x['outcome'],
                                reason_class=None, reason=None, failed=None, checks={},
                                tiers=dict(saved=None, practical=None, reedit=None, restore=None, refused=None)))
                continue
            r = smoke_evaluate.records({cid: [x]})[0]
            r.update(evaluation='remaining', step=str(n + 1))
            out.append(r)
    return out


def phase_collect(args):
    """The runs (``--roots label=dir``, e.g. main= and new=) as records, compact results and, per case, what changed."""
    compact = lambda x: {k: er.compact_audit(v) if k == 'approved_audit' else v for k, v in x.items()
                         if k not in ('frame', 'seconds', 'occurrence', 'rounds', 'lines', 'chain', 'decorations')}
    runs = {}
    for label, root in (x.split('=', 1) for x in args.roots):
        results = json.loads((Path(root) / 'results.json').read_text())
        recs = records(results)
        runs[label] = dict(summary=er.summary(recs), records=recs, results={k: [compact(x) for x in seq] for k, seq in results.items()})
    out = dict(schema='paragraph-remaining-results-1', cases_from=str(SEPARATE), manifest_sha256=hashlib.sha256(SEPARATE.read_bytes()).hexdigest(),
               runs=runs)
    if {'main', 'new'} <= set(runs):
        out['comparison'] = er.compare(runs['main']['records'], runs['new']['records'])
        out['cases'] = {cid: dict(main=[x.get('outcome') for x in runs['main']['results'][cid]], new=[x.get('outcome') for x in seq],
                                  reason_main=(runs['main']['results'][cid][-1].get('reason') or '')[-160:] or None,
                                  reason_new=(seq[-1].get('reason') or '')[-160:] or None)
                        for cid, seq in runs['new']['results'].items()}
    OUT.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    print(json.dumps({k: v['summary'] for k, v in runs.items()}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('run', 'collect'), required=True)
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--only', nargs='*')
    p.add_argument('--roots', nargs='*', help='collect: label=dir of each run (main=, new=)')
    args = p.parse_args()
    dict(run=phase_run, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
