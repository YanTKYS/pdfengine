"""Practical success with the search and copy measures (issue #116): what was recorded before, and after.

  .venv/bin/python -m prototypes.paragraph_frame.practical_evaluate --phase freeze
  .venv/bin/python -m prototypes.paragraph_frame.practical_evaluate --phase collect
  .venv/bin/python -m prototypes.paragraph_frame.practical_evaluate --phase split --smoke <the smoke run's folder>

``freeze`` (before the evaluators change) copies, from the committed results,
what each case was judged so far: the 11 real PDFs of the search and copy
evaluation (evaluations/search_copy), the panel cases of PR #113 (L2, L6, L8
saved on approval; W1, W2 refused; the negatives N22, N07) and the smoke set.
Per case: the existing search check (``search_mupdf``) and quality status of
the approved-change audit, the search and copy measures, the existing
(region) audit, the approved-change audit's verdict, and the success tiers.
It also writes the table of what practical success requires (CRITERIA).

``collect`` reads the committed results refreshed by the runs made after the
change and writes the same fields beside the frozen ones, with what changed
and why (the criteria that keep a step from practical success).

``split`` divides the baseline change that ``eval_hub baseline`` just
appended to evaluations/baseline/changes.json into the measurement fixes and
the product contract change (option (b)), each record with why it changed.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

OUT = Path('evaluations/practical_search')
BEFORE, AFTER = OUT / 'before.json', OUT / 'after.json'

# What practical success requires (docs/practical-search.md): each criterion, where its evidence comes from, and how
# a missing or failed one counts. Written before the evaluators change.
CRITERIA = [
    dict(criterion='saved', evidence='the step\'s outcome', fail='refused', unverified='not run, input missing'),
    dict(criterion='approval', evidence='approved_audit.approval (the save is this proposal\'s; every moved element and panel '
                                        'approved by it, every approved one changed)', fail='an id not approved or not this proposal\'s',
         unverified='audit not run'),
    dict(criterion='approved changes as approved', evidence='approved_audit.approved (translations, panel extensions, the new text '
                                                            'in its frame)', fail='a change not made exactly as approved',
         unverified='a change kind the audit cannot check'),
    dict(criterion='protection of what was not approved', evidence='approved_audit.protected (glyphs, drawings, images, annotations, pixels, '
                                                                   'other pages, fonts) for every step it audits; the existing region audit '
                                                                   '(safety) besides for a faithful step; for a layout step the region '
                                                                   'audit fails by design and is shown, not counted',
         fail='any change outside the approved areas', unverified='not audited'),
    dict(criterion='structure and links', evidence='structure_audit, links_audit', fail='a tag or link changed', unverified='(not required '
                                                                                                                        'when not measured)'),
    dict(criterion='quality (extraction of the page, sizes and colours)', evidence='approved_audit.quality (and the existing quality '
                                                                                   'for a faithful step)', fail='any check false',
         unverified='a check not made'),
    dict(criterion='search and copy of the edited text', evidence='search_copy (issue #114): one line: search_full, extraction_mupdf, '
                                                                  'extraction_poppler, copy_equivalent; over lines: search_per_line, '
                                                                  'extraction_mupdf, extraction_poppler, copy_equivalent (search_full '
                                                                  'recorded, not required); a deletion: not applicable',
         fail='any required measure false (a gap of the source too: as_in_source does not pass it)',
         unverified='a required measure null, or the measures not made'),
]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _record(r):
    return dict(outcome=r['outcome'], mode=r['mode'], tiers=r['tiers'], checks=r['checks'], failed=r['failed'])


def _audit(x):
    a = x.get('approved_audit') or {}
    q = a.get('quality') or {}
    return dict(verdict=a.get('verdict'), quality_status=q.get('status'), search_mupdf=q.get('search_mupdf', 'absent'),
                search_wrapped_lines_text=q.get('search_wrapped_lines_text'))


def snapshot(smoke, panel, search_copy):
    """The judgement of every case: from smoke and panel results (records and per-step results) and search_copy results."""
    out = dict(search_copy={}, panel={}, smoke={})
    for cid, r in search_copy['results'].items():
        q = r.get('quality_existing') or {}
        m = r.get('measures') or {}
        out['search_copy'][cid] = dict(outcome=r.get('outcome'), verdict=r.get('verdict'), mode=r.get('mode'),
                                       search_mupdf=q.get('search_mupdf', 'absent'), quality_status=q.get('status'),
                                       legacy=q.get('legacy'), search_copy_check=q.get('search_copy_check'),
                                       measures={k: m.get(k) for k in ('search_full', 'search_per_line', 'extraction_mupdf', 'extraction_poppler',
                                                                       'copy_equivalent', 'clipboard_manual')},
                                       lines=(m.get('search_full_hits') or {}).get('lines'), practical=r.get('practical'))
    for name, results, records in (('panel', panel['results'], panel['records']), ('smoke', smoke['results'], smoke['records'])):
        recs = {(r['case'], r['step']): r for r in records}
        for cid, seq in results.items():
            for n, x in enumerate(seq):
                r = recs.get((cid, str(n + 1)))
                if r is None:
                    continue
                out[name][f'{cid}/{n + 1}'] = dict(**_record(r), approved_audit=_audit(x), basis=r.get('practical_basis'))
    return out


def phase_freeze(args):
    smoke = json.loads(Path('evaluations/smoke/results.json').read_text())
    panel = json.loads(Path('evaluations/paragraph_panel/results.json').read_text())['runs']['new']
    search_copy = json.loads(Path('evaluations/search_copy/results.json').read_text())
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    OUT.mkdir(parents=True, exist_ok=True)
    out = dict(schema='practical-search-before-1', frozen_at=rev,
               sources={p: _sha(p) for p in ('evaluations/smoke/results.json', 'evaluations/paragraph_panel/results.json',
                                             'evaluations/search_copy/results.json')},
               policy=('Frozen before the evaluators change (issue #116): the judgement recorded so far, kept as it was. Practical success '
                       'after the change is compared with it case by case; a measured false is never hidden, a missing measure never '
                       'counted as a pass.'),
               criteria=CRITERIA, cases=snapshot(smoke, panel, search_copy))
    BEFORE.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    c = out['cases']
    print(len(c['search_copy']), 'search_copy cases,', len(c['panel']), 'panel steps,', len(c['smoke']), 'smoke steps')


def _why(entry):
    """The criteria that keep a step from practical success (failed or not measured), from its basis."""
    basis = entry.get('basis') or []
    return [f"{b['criterion']}: {b['result']}" + (f" ({b['note']})" if b.get('note') else '') for b in basis
            if b['role'] != 'shown' and (b['result'] is False or b['role'] == 'required' and b['result'] is not True)]


def phase_collect(args):
    """The judgement after the change (the committed results, refreshed by the runs), beside the frozen one."""
    smoke = json.loads(Path('evaluations/smoke/results.json').read_text())
    panel = json.loads(Path('evaluations/paragraph_panel/results.json').read_text())['runs']['new']
    search_copy = json.loads(Path('evaluations/search_copy/results.json').read_text())
    before = json.loads(BEFORE.read_text())
    now = snapshot(smoke, panel, search_copy)
    changes = []
    fields = dict(search_copy=('search_mupdf', 'quality_status', 'verdict', 'mode'),
                  panel=('outcome', 'mode', 'tiers'), smoke=('outcome', 'mode', 'tiers'))
    for group, keys in fields.items():
        for cid in sorted(set(before['cases'][group]) | set(now[group])):
            a, b = before['cases'][group].get(cid), now[group].get(cid)
            if a is None or b is None:
                changes.append(dict(group=group, case=cid, only='after' if a is None else 'before'))
                continue
            diff = {k: [a.get(k), b.get(k)] for k in keys if a.get(k) != b.get(k)}
            if group != 'search_copy' and a['approved_audit'] != b['approved_audit']:
                diff['approved_audit'] = [a['approved_audit'], b['approved_audit']]
            if diff:
                changes.append(dict(group=group, case=cid, **diff, **({'not_practical_because': _why(b)} if group != 'search_copy'
                                                                     and b['tiers']['practical'] is not True else {})))
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    out = dict(schema='practical-search-after-1', code=rev, before=str(BEFORE), cases=now, changes=changes)
    AFTER.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    for c in changes:
        print(json.dumps(c, ensure_ascii=False, default=str)[:300])


CHANGES = Path('evaluations/baseline/changes.json')
MEASUREMENT = ('measurement fix (issue #116): the existing search check measured on the edited lines (a hit elsewhere on the page no '
               'longer passes; a one-line replacement not found is false, not a wrap); natural placement measured on the edited lines '
               '(the smoke steps never measured it); the measures, placement and practical basis recorded beside each step')
CONTRACT = ('product contract change (issue #116, option (b) of issue #114, approved by the issue): the edited text\'s search and '
            'extraction required for practical success: one line, one search where it is drawn; over lines, each line\'s part on its '
            'line; both extractions of the paragraph and the copy equivalent always. search_full stays recorded')


def tags_of(x):
    """Why an approved-audited step's record changed (issue #116): measurement fixes and the contract change (option (b))."""
    q = ((x or {}).get('approved_audit') or {}).get('quality') or {}
    check, legacy, tags = q.get('search_copy_check') or {}, q.get('legacy') or {}, []
    if q.get('search_mupdf_change'):
        tags.append(f"measurement: search_mupdf {q['search_mupdf_change']['legacy']} -> {q['search_mupdf_change']['now']} "
                    f"({q['search_mupdf_change']['why']})")
    if (q.get('placement') or {}).get('result') is not True and q.get('placement'):
        tags.append(f"measurement: placement {q['placement']['result']} {q['placement']['checks']}")
    if check.get('result') is True and check.get('route') == 'lines' and legacy.get('status') != 'pass':
        tags.append('contract (b): verified line by line (one search over lines not required)')
    if check.get('result') is False:
        tags.append(f"contract (b): {', '.join(check['failed'])} false")
    return tags


def phase_split(args):
    """Split the baseline change ``eval_hub baseline`` just appended into the measurement fixes and the contract change."""
    changes = json.loads(CHANGES.read_text())
    last = changes.pop()
    results = json.loads((Path(args.smoke) / 'results.json').read_text())
    measured, contract = [], []
    for c in last['changed']:
        _, case, step = c['record']
        x = (results.get(case) or [None] * int(step))[int(step) - 1] if case in results else None
        tags = tags_of(x)
        if not c['tiers']:
            tags.append('record only: practical success unchanged (checks added or a null check no longer stored)')
        (contract if any(t.startswith('contract') for t in tags) else measured).append(dict(c, why=tags))
    for reason, changed, decision in ((MEASUREMENT, measured, False), (CONTRACT, contract, 'issue #116 option (b): approved by the issue')):
        changes.append(dict(last, reason=reason, changed=changed, design_decision_needed=decision,
                            counts=dict(changed=len(changed), practical=dict(Counter(f"{c['tiers']['practical'][0]} -> {c['tiers']['practical'][1]}"
                                                                                    for c in changed if 'practical' in c['tiers'])))))
    CHANGES.write_text(json.dumps(changes, ensure_ascii=False, indent=1, default=str) + '\n')
    for e in changes[-2:]:
        print(e['reason'][:60], e['counts'])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('freeze', 'collect', 'split'), required=True)
    p.add_argument('--smoke')
    p.add_argument('--panel')
    p.add_argument('--search-copy')
    args = p.parse_args()
    dict(freeze=phase_freeze, collect=phase_collect, split=phase_split)[args.phase](args)


if __name__ == '__main__':
    main()
