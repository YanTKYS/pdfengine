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
changed in outcome, reason class, failing checks, checks, success tiers,
mode and group.
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
# Approved layout changes a save records (an approved panel extension, issue #112, is one too).
MOVED = ('placed_objects_moved', 'decorations_moved', 'chain_moved', 'panels_extended')


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
    approved = x.get('approved_audit') or None
    if approved:
        checks.update(_approved_checks(approved))
        # Approved layout changes need both: the save made them and the audit's change set holds them. A faithful
        # edit (the following paragraphs' own translation included) is never counted as an approved layout edit.
        layout = layout and approved.get('mode') == 'layout'
    value, basis = practical(outcome, checks, layout, approved)
    restore = None
    if restored:
        parts = [checks[k] for k in ('restored_glyphs', 'restored_pixels', 'restored_text')]
        restore = None if any(p is None for p in parts) else all(parts)
    out = dict(evaluation=evaluation, case=case, step=step, group=group, mode='layout' if layout else 'faithful',
               outcome=outcome, reason_class=_class(x), reason=(x.get('reason') or '')[-120:] or None,
               failed=sorted(failed) if isinstance(failed, list) else None,
               checks={k: v for k, v in checks.items() if v is not None},
               tiers=dict(saved=saved if outcome in ('saved', 'refused') else None, practical=value,
                          reedit=checks['reedit'] if saved else None, restore=restore, refused=outcome == 'refused'))
    if approved:
        out['practical_basis'] = basis
    return out


SEARCH_MEASURES = (('search_full', 'search_full'), ('search_per_line', 'search_per_line'), ('search_extraction_mupdf', 'extraction_mupdf'),
                   ('search_extraction_poppler', 'extraction_poppler'), ('copy_equivalent', 'copy_equivalent'))


MEASURES_KEPT = ('search_full', 'search_full_hits', 'search_per_line', 'extraction_mupdf', 'extraction_poppler', 'copy_equivalent',
                 'clipboard_manual', 'gaps', 'reason')


def compact_audit(a):
    """An approved-change audit as an evaluation's committed results keep it: the search and copy measures without
    their per-line and per-extractor details (in the run's own results)."""
    q = (a or {}).get('quality')
    if not isinstance(q, dict) or not isinstance(q.get('search_copy'), dict):
        return a
    m = q['search_copy']
    return dict(a, quality=dict(q, search_copy={k: m[k] for k in MEASURES_KEPT if k in m}))


def _approved_checks(a):
    """The checks of an approved-change audit (approved_audit, as an evaluation recorded it): its verdict, its quality
    (and the quality before issue #116), the search and copy measures and whether they verify the edited text."""
    status = lambda v: {'pass': True, 'fail': False}.get(v)
    q = a.get('quality') or {}
    out = dict(approved_audit=status(a.get('verdict')), approved_quality=status(q.get('status')),
               natural_placement=(q.get('placement') or {}).get('result'))
    if 'legacy' in q:
        out['approved_quality_legacy'] = status(q['legacy'].get('status'))
    m = q.get('search_copy') or {}
    for name, key in SEARCH_MEASURES:
        out[name] = m.get(key)
    check = q.get('search_copy_check') or {}
    out['search_copy'] = check.get('result') if check.get('result') in (True, False) else None
    return out


# The existing audit's safety checks (indesign_evaluate.audit); its other checks are quality's.
SAFETY = {'raster_outside_region_mupdf', 'raster_outside_region_poppler', 'glyphs_outside_region_identical', 'non_text_paint_identical',
          'original_font_programs_kept', 'other_pages_and_their_forms_unchanged', 'page_count'}
# Protection measured apart from the region audit: structure, links, and the approved moves' own audits.
PROTECTION = ('structure', 'links', 'moves', 'placement', 'raster', 'kept', 'placed')
MOVE_AUDITS = ('moves', 'links', 'placement', 'kept', 'placed')


def practical_success(outcome, checks, layout):
    """Practical success from the existing audits alone (a step without the approved-change audit): see practical."""
    return practical(outcome, checks, layout)[0]


def practical(outcome, checks, layout, approved=None):
    """Practical success (docs/development-rules.md, docs/practical-search.md): (True | False | None, basis).

    ``basis`` lists each criterion with where its evidence comes from, its result and its role: ``required``
    (must be True; None leaves practical success unverified), ``must not fail`` (False makes it False) or
    ``shown`` (reported, not counted: the existing region audit of a step that saved approved layout changes
    fails by design, the approved-change audit stands for it there; the quality before issue #116).

    With the approved-change audit (``approved``, as an evaluation recorded it): its verdict (approval, the
    approved changes made as approved, the protection of everything else), its quality (which includes the
    edited text's search and extraction, search_copy.verified, and the natural placement of its lines) and the
    structure and link audits, each required (not measured: unverified). A faithful
    step must also not fail the existing region audit, its quality or the move audits. Without it: the
    existing quality and the protection of what was not approved (the existing safety audit, or for a step that
    saved approved moves the move audits), as before issue #116; its search check counts a hit anywhere on the
    page and is not measured on the edited lines (shown in the basis)."""
    basis = []

    def add(criterion, source, result, role, note=None):
        basis.append(dict(criterion=criterion, source=source, result=result, role=role, **({'note': note} if note else {})))

    saved = True if outcome == 'saved' else False if outcome == 'refused' else None
    add('saved', f'outcome ({outcome})', saved, 'required')
    if not saved:
        return saved, basis
    if approved is not None:
        q = approved.get('quality') or {}
        status = lambda v: {'pass': True, 'fail': False}.get(v)
        add('approval, approved changes, protection', 'approved_audit', status(approved.get('verdict')), 'required',
            None if approved.get('verdict') in ('pass', 'fail') else f"verdict {approved.get('verdict')}")
        add('quality', 'approved_audit.quality', status(q.get('status')), 'required')
        place = q.get('placement')
        add('natural placement', 'approved_audit.quality.placement', (place or {}).get('result'), 'required',
            f"leading: {place.get('leading_basis')}" if place else 'not measured (an audit made before issue #116)')
        check = q.get('search_copy_check')
        if check is None:
            add('search and copy of the edited text', 'search_copy.verified', None, 'required', 'not measured (an audit made before issue #116)')
        else:
            add('search and copy of the edited text', 'search_copy.verified', check.get('result'),
                'shown' if check.get('result') == 'not applicable' else 'required', check.get('note') or check.get('route'))
        if 'legacy' in q:
            add('quality before issue #116', 'approved_audit.quality.legacy', status(q['legacy'].get('status')), 'shown',
                'search_mupdf counted a hit anywhere on the page')
        # Semantic structure and links (PR #117 review): measured, not only unfailed. An audit not made leaves
        # practical success unverified; a PDF without tags or links passes when the audit ran and found no change.
        for k in ('structure', 'links'):
            add(k, f'{k} audit', checks.get(k), 'required', None if checks.get(k) is not None else 'not measured')
        own = 'shown' if layout else 'must not fail'
        note = ('not counted on an approved layout step: it compares a region the approved changes enter (it fails there by '
                'design); the approved-change audit stands for it') if layout else None
        add('protection (region)', 'existing audit: safety', checks.get('safety'), own, note)
        add('quality (region)', 'existing audit: quality', checks.get('quality'), own, note)
        for k in ('moves', 'placement', 'raster', 'kept', 'placed'):
            if checks.get(k) is not None:
                add(f'{k} audit', f'existing {k} audit', checks.get(k), own,
                    'the approved-change audit checks the approved changes exactly (panels included)' if layout else None)
        if any(b['result'] is False and b['role'] != 'shown' for b in basis):
            return False, basis
        if any(b['result'] is not True and b['role'] == 'required' for b in basis):
            return None, basis
        return True, basis
    add('quality', 'existing audit: quality', checks.get('quality'), 'required',
        'its search check counts a hit anywhere on the page (not measured on the edited lines)')
    for k in PROTECTION:
        if checks.get(k) is not None:
            add(k, f'{k} audit', checks.get(k), 'must not fail')
    if checks.get('quality') is False or any(checks.get(k) is False for k in PROTECTION):
        value = False
    else:
        moves = [checks[k] for k in MOVE_AUDITS if checks.get(k) is not None]
        protected = True if checks.get('safety') is True or layout and moves else False if checks.get('safety') is False else None
        value = False if protected is False else None if checks.get('quality') is None or protected is None else True
        add('protection', 'existing audit: safety' + (', or the move audits (approved moves)' if layout else ''), protected, 'required',
            'the region audit fails by design on approved moves' if layout and checks.get('safety') is False else None)
    return value, basis


def _audit_outcome(outcome):
    """(safety, quality) from an audit's outcome: success, quality-audit-failed (safety passed) or safety-audit-failed."""
    if outcome == 'success':
        return True, True
    if outcome == 'quality-audit-failed':
        return True, False
    if outcome == 'safety-audit-failed':
        return False, None
    return None, None


def _split(failed):
    """(safety, quality) from the existing audit's failing checks."""
    if failed is None:
        return None, None
    return not any(f in SAFETY for f in failed), not any(f not in SAFETY for f in failed)


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
    def add(evaluation, case, step, outcome, failed=None, group='real', **checks):
        checks = {k: v for k, v in checks.items() if v is not None}
        out.append(dict(evaluation=evaluation, case=case, step=step, group=group, mode='faithful', outcome=outcome, reason_class=None,
                        reason=None, failed=failed, checks=checks,
                        tiers=dict(saved=outcome == 'saved' if outcome in ('saved', 'refused') else None,
                                   practical=practical_success(outcome, checks, False), reedit=checks.get('reedit') if outcome == 'saved' else None,
                                   restore=None, refused=outcome == 'refused')))
    for k, v in (reg.get('generalization') or {}).items():
        for step in ('initial', 'reedit'):
            safety, quality = _audit_outcome(v[step])
            add('generalization', k, step, 'saved', v[f'{step}_failed'], group='fixture', safety=safety, quality=quality)
    for k, v in (reg.get('continuity_sequences') or {}).items():
        for n, s in enumerate(v['steps']):
            safety, quality = _audit_outcome(s)
            add('continuity', k, f'sequence/{n + 1}', 'saved', group='fixture', safety=safety, quality=quality)
    for k, v in (reg.get('continuity_new') or {}).items():
        safety, quality = _audit_outcome(v.get('audit'))
        add('continuity', k, 'new', v['edit'], group='fixture', safety=safety, quality=quality)
    for part in ('sequences', 'singles'):
        for k, v in ((reg.get('column_flow') or {}).get(part) or {}).items():
            steps = v.get('steps') or [v.get('outcome')]
            for n, s in enumerate(steps):
                add('column_flow', k, f'{part}/{n + 1}', s, as_expected=v.get('as_expected'))
    for k, v in ((reg.get('indesign') or {}).get('requests') or {}).items():
        safety, quality = _split(v.get('failed')) if v['outcome'] == 'saved' else (None, None)
        add('indesign', k, 'request', v['outcome'], v.get('failed'), safety=safety, quality=quality)
    totals = (reg.get('indesign') or {}).get('samples') or {}
    if totals:
        add('indesign', 'samples', 'totals', 'saved', group='sample',
            **{f'n_{x}': totals.get(x) for x in ('sampled', 'saved', 'safety', 'quality')})
    for k, v in ((reg.get('tagged') or {}).get('requests') or {}).items():
        safety, quality = _split(v.get('failed')) if v['outcome'] == 'saved' else (None, None)
        add('tagged', k, 'request', v['outcome'], v.get('failed'), safety=safety, quality=quality,
            structure=not v.get('structure_failed') if v['outcome'] == 'saved' else None, reedit=v.get('continued_editing'))
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
    # Everything a record says: outcome, reason, failing checks, checks, success tiers, mode and group.
    sig = lambda r: (r['outcome'], r['reason_class'], r['failed'], r['checks'], r['tiers'], r['mode'], r['group'])
    changed = []
    for k in sorted(set(base) & set(now)):
        if sig(base[k]) != sig(now[k]):
            a, b = base[k], now[k]
            changed.append(dict(record=list(k), outcome=[a['outcome'], b['outcome']], reason_class=[a['reason_class'], b['reason_class']],
                                failed=[a['failed'], b['failed']],
                                checks={c: [a['checks'].get(c), b['checks'].get(c)] for c in sorted(set(a['checks']) | set(b['checks']))
                                        if a['checks'].get(c) != b['checks'].get(c)},
                                tiers={t: [a['tiers'].get(t), b['tiers'].get(t)] for t in sorted(set(a['tiers']) | set(b['tiers']))
                                       if a['tiers'].get(t) != b['tiers'].get(t)},
                                **{x: [a[x], b[x]] for x in ('mode', 'group') if a[x] != b[x]}))
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
