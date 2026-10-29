"""Common records of the paragraph-frame evaluations, their success tiers and their comparison (issue #108).

Every evaluation keeps its own runner and results file. Its results are read
here into one record per step:

  dict(evaluation, case, step, group (real | fixture | sample), mode (faithful | layout),
       outcome (saved | refused | not run | input missing), reason_class, reason,
       failed (the failing checks of the existing audits), checks {name: True | False | None},
       tiers {saved, practical, reedit, restore, refused: True | False | None})

``checks`` holds what the evaluation measured, as it measured it (None: not
measured). ``tiers`` follows docs/development-rules.md: practical success
needs the quality checks and the protection of what was not approved (the
existing safety audit, or for approved moves the move audits); a tier whose
evidence was not measured is None (unverified), never True. A step is
``layout`` when it saved explicitly approved moves (placed objects, line
decorations, chain elements); otherwise ``faithful`` (the following
paragraphs' own translation included).

``compare`` matches records by (evaluation, case, step) and reports what
changed in outcome, reason class, failing checks and checks.
"""
from collections import Counter
import json
from pathlib import Path

# Evaluation -> its manifest (fixtures are reported apart from real PDFs).
MANIFESTS = {
    'chain': 'evaluations/paragraph_frame_chain/manifest.json',
    'separate': 'evaluations/paragraph_frame_separate/manifest.json',
    'decoration': 'evaluations/paragraph_frame_decoration/manifest.json',
    'restore': 'evaluations/paragraph_frame_restore/manifest.json',
    'placed': 'evaluations/paragraph_frame_placed/manifest.json',
    'multiedit': 'evaluations/paragraph_frame_multiedit/manifest.json',
    'roundtrip': 'evaluations/paragraph_frame_roundtrip/manifest.json',
    'fidelity': 'evaluations/paragraph_frame_fidelity/manifest.json',
}
MOVED = ('placed_objects_moved', 'decorations_moved', 'chain_moved')


def _ok(v):
    """A measured check as True / False, or None (absent)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, dict):
        for k in ('ok', 'identical', 'exact'):
            if k in v:
                return bool(v[k])
        return None
    if isinstance(v, list):
        return not v
    return None


def _class(step):
    if step.get('class'):
        return step['class']
    reason = step.get('reason') or ''
    if not reason:
        return None
    if step.get('outcome') == 'not run':
        return 'not run'
    text = reason.split('Refused: ')[-1]
    for key in ('capacity', 'collides with protected', 'would meet protected', 'drawn together', 'leave or enter a background',
                'holds content', 'missing source glyph', 'cannot locate', 'marked-content'):
        if key in text:
            return key
    return 'other'


def step_record(evaluation, case, step, x, group):
    """The common record of one step ``x`` (an evaluation's own step dict)."""
    save = x.get('save') or {}
    audit = x.get('audit') or {}
    failed = x.get('failed')
    if failed is None and isinstance(audit.get('checks'), dict):
        failed = sorted(k for k, v in audit['checks'].items() if v is False)
    restored = x.get('restored') or {}
    compare = restored.get('compare') or {}
    checks = dict(
        safety=x.get('safety') if 'safety' in x else audit.get('safety'),
        quality=x.get('quality') if 'quality' in x else audit.get('quality'),
        structure=(not x['structure_failed']) if isinstance(x.get('structure_failed'), list) else None,
        reedit=_ok(x.get('continued_editing')),
        moves=_ok(x.get('moves')), links=_ok(x.get('links')), placement=_ok(x.get('placement')),
        raster=_ok(x.get('raster')), kept=_ok(x.get('kept')), placed=_ok(x.get('placed')),
        restored_glyphs=_ok(restored.get('glyphs')), restored_pixels=_ok(restored.get('pixels')),
        restored_text=(all(compare['text_identical'].values()) if isinstance(compare.get('text_identical'), dict) else None),
        restored_links=_ok(restored.get('links')),
    )
    for k in ('fidelity', 'round_trip', 'identity'):
        if isinstance(x.get(k), (bool, dict)):
            checks[k] = _ok(x[k])
    layout = any(save.get(k) for k in MOVED)
    outcome = x.get('outcome')
    saved = outcome == 'saved'
    move_audits = [checks[k] for k in ('moves', 'links', 'placement', 'kept', 'placed') if checks[k] is not None]
    if not saved:
        practical = False if outcome == 'refused' else None
    elif checks['quality'] is None:
        practical = None
    elif checks['safety'] is True:
        practical = bool(checks['quality'])
    elif layout and move_audits:
        # Approved moves fail the region audit by design: the move audits stand for protection.
        practical = bool(checks['quality']) and all(move_audits)
    elif checks['safety'] is False:
        practical = False
    else:
        practical = None
    restore = None
    if restored:
        parts = [checks[k] for k in ('restored_glyphs', 'restored_pixels', 'restored_text')]
        restore = None if any(p is None for p in parts) else all(parts)
    return dict(evaluation=evaluation, case=case, step=step, group=group, mode='layout' if layout else 'faithful',
                outcome=outcome, reason_class=_class(x), reason=(x.get('reason') or '')[-120:] or None,
                failed=sorted(failed) if isinstance(failed, list) else None,
                checks={k: v for k, v in checks.items() if v is not None},
                tiers=dict(saved=saved if outcome in ('saved', 'refused') else None, practical=practical,
                           reedit=checks['reedit'] if saved else None, restore=restore, refused=outcome == 'refused'))


def _steps(value, path=()):
    """(path, step dict) of an evaluation's nested results (a step is a dict with an outcome)."""
    if isinstance(value, dict) and 'outcome' in value:
        yield '/'.join(path), value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _steps(v, path + (str(k),))
    elif isinstance(value, list):
        for n, v in enumerate(value):
            yield from _steps(v, path + (str(n + 1),))


def fixtures_of(evaluation):
    path = MANIFESTS.get(evaluation)
    if not path or not Path(path).exists():
        return set()
    return set(json.loads(Path(path).read_text()).get('fixtures', {}))


def case_records(evaluation, results):
    """Records of an evaluation whose results are {case: nested steps}."""
    fixtures = fixtures_of(evaluation)
    out = []
    for case, value in results.items():
        group = 'fixture' if case in fixtures or case.split('-')[0] in fixtures else 'real'
        for step, x in _steps(value):
            out.append(step_record(evaluation, case, step or '1', x, group))
    return out


def regression_records(reg):
    """Records of the PR #87-#91 runs, from tagged_regression's output."""
    out = []
    add = lambda evaluation, case, step, outcome, failed=None, group='real', **checks: out.append(dict(
        evaluation=evaluation, case=case, step=step, group=group, mode='faithful', outcome=outcome, reason_class=None, reason=None,
        failed=failed, checks={k: v for k, v in checks.items() if v is not None},
        tiers=dict(saved=outcome == 'saved' if outcome in ('saved', 'refused') else None,
                   practical=(None if outcome != 'saved' else checks.get('quality')) if outcome != 'refused' else False,
                   reedit=checks.get('reedit'), restore=None, refused=outcome == 'refused')))
    for k, v in (reg.get('generalization') or {}).items():
        for step in ('initial', 'reedit'):
            add('generalization', k, step, 'saved', v[f'{step}_failed'], group='fixture', quality=v[step] == 'success')
    for k, v in (reg.get('continuity_sequences') or {}).items():
        for n, s in enumerate(v['steps']):
            add('continuity', k, f'sequence/{n + 1}', 'saved', group='fixture', quality=s == 'success')
    for k, v in (reg.get('continuity_new') or {}).items():
        add('continuity', k, 'new', v['edit'], group='fixture', safety=(v['audit'] == 'success') if v.get('audit') else None)
    for part in ('sequences', 'singles'):
        for k, v in ((reg.get('column_flow') or {}).get(part) or {}).items():
            steps = v.get('steps') or [v.get('outcome')]
            for n, s in enumerate(steps):
                add('column_flow', k, f'{part}/{n + 1}', s, as_expected=v.get('as_expected'))
    for k, v in ((reg.get('indesign') or {}).get('requests') or {}).items():
        add('indesign', k, 'request', v['outcome'], v.get('failed'), quality=(not v.get('failed')) if v['outcome'] == 'saved' else None)
    totals = (reg.get('indesign') or {}).get('samples') or {}
    if totals:
        add('indesign', 'samples', 'totals', 'saved', group='sample',
            **{f'n_{x}': totals.get(x) for x in ('sampled', 'saved', 'safety', 'quality')})
    for k, v in ((reg.get('tagged') or {}).get('requests') or {}).items():
        add('tagged', k, 'request', v['outcome'], v.get('failed'), structure=not v.get('structure_failed') if v['outcome'] == 'saved' else None,
            reedit=v.get('continued_editing'))
    for r in ((reg.get('tagged') or {}).get('samples') or {}).get('edits') or []:
        ce = r.get('continued_editing')
        add('tagged', f"{r['document']}:{r['page']}:{r['selection']}", r['kind'], r['outcome'], r.get('failed'), group='sample',
            safety=r.get('safety'), quality=r.get('quality'), structure=r.get('structure'), reedit=ce.get('ok') if isinstance(ce, dict) else None)
    return out


def key(r):
    return (r['evaluation'], r['case'], r['step'])


def compare(baseline, current, evaluations=None):
    """What changed between the baseline records and the current ones (for ``evaluations``, else those present now)."""
    evaluations = set(evaluations or {r['evaluation'] for r in current})
    base = {key(r): r for r in baseline if r['evaluation'] in evaluations}
    now = {key(r): r for r in current if r['evaluation'] in evaluations}
    sig = lambda r: (r['outcome'], r['reason_class'], r['failed'], r['checks'])
    changed = []
    for k in sorted(set(base) & set(now)):
        if sig(base[k]) != sig(now[k]):
            a, b = base[k], now[k]
            changed.append(dict(record=list(k), outcome=[a['outcome'], b['outcome']], reason_class=[a['reason_class'], b['reason_class']],
                                failed=[a['failed'], b['failed']],
                                checks={c: [a['checks'].get(c), b['checks'].get(c)] for c in sorted(set(a['checks']) | set(b['checks']))
                                        if a['checks'].get(c) != b['checks'].get(c)}))
    # A case not run now (a tier runs part of an evaluation, the smoke set) is not a missing step.
    ran = {k[:2] for k in now}
    gone = sorted(set(base) - set(now))
    return dict(evaluations=sorted(evaluations), compared=len(set(base) & set(now)), changed=changed,
                only_baseline=[list(k) for k in gone if k[:2] in ran], only_current=[list(k) for k in sorted(set(now) - set(base))],
                cases_not_run=sorted({f'{k[0]}:{k[1]}' for k in gone if k[:2] not in ran}))


def summary(records):
    """Counts per evaluation, group and mode: steps and each tier (True / False / unverified)."""
    out = {}
    for r in records:
        s = out.setdefault(r['evaluation'], {}).setdefault(f"{r['group']}:{r['mode']}", Counter())
        s['steps'] += 1
        s[r['outcome'] or 'unknown'] += 1
        for tier, v in r['tiers'].items():
            s[f'{tier}:{"yes" if v else "no" if v is False else "unverified"}'] += 1
    return {e: {g: dict(c) for g, c in gs.items()} for e, gs in out.items()}
