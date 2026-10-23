"""Compare the PR #87-#90 regression runs with their committed results and with the regression PR #90 recorded.

  .venv/bin/python -m prototypes.paragraph_frame.tagged_regression <regression.json>

Reads the runs of generalization_evaluate (tmp/reg87), continuity_evaluate
(tmp/reg88), column_flow_evaluate (tmp/reg89) and indesign_evaluate
(tmp/reg90, samples with --survey tmp/id-survey-final), each run with the same
arguments as in its PR. The output is passed to tagged_evaluate collect
(--regression-json).
"""
import json
import sys
from pathlib import Path

load = lambda p: json.loads(Path(p).read_text())
out = {}
diffs = []

# PR #87 (generalization) against the reference recorded by PR #88/#89.
ref = load('evaluations/paragraph_frame_continuity/results.json')['regression']
gen = {}
for k, v in ref.items():
    initial = load(f'tmp/reg87/{k}.audit.json')
    reedit = load(f'tmp/reg87/{k}.lifecycle.audit.json')
    row = dict(initial=initial['outcome'], initial_failed=initial['failed'], reedit=reedit['outcome'], reedit_failed=reedit['failed'])
    row['same_as_committed'] = (row['initial'], row['initial_failed'], row['reedit'], row['reedit_failed']) == (
        v['initial']['quality'], v['initial']['failed'], v['pdf_alone_reedit']['quality'], v['pdf_alone_reedit']['failed'])
    if not row['same_as_committed']:
        diffs.append(('generalization', k, row, v['initial'], v['pdf_alone_reedit']))
    gen[k] = row
out['generalization'] = gen

# PR #88 (continuity): sequences and new requests.
cref = load('evaluations/paragraph_frame_continuity/results.json')
seq = load('tmp/reg88/sequences.json')
cs = {}
for k, chain in cref['sequences'].items():
    got = seq[k]['steps']
    key = lambda s: (s['outcome'], (s.get('audit') or {}).get('outcome'), (s.get('audit') or {}).get('failed'), s.get('planned_lines'))
    same = [key(a) == key(b) for a, b in zip(chain['steps'], got)] + [len(chain['steps']) == len(got)]
    cs[k] = dict(steps=[(s.get('audit') or {}).get('outcome') or s['outcome'] for s in got], same_as_committed=all(same))
    if not all(same):
        diffs.append(('continuity sequence', k, [key(s) for s in got], [key(s) for s in chain['steps']]))
out['continuity_sequences'] = cs
new = load('tmp/reg88/new.json')
cn = {}
for k, v in cref['new'].items():
    a, b = v['B2'], new[k]['B2']
    key = lambda s: (s['edit'], s.get('lines'), (s.get('audit') or {}).get('outcome'), (s.get('audit') or {}).get('failed'), (s.get('reedit') or {}).get('outcome') if isinstance(s.get('reedit'), dict) else s.get('reedit'))
    cn[k] = dict(edit=b['edit'], audit=(b.get('audit') or {}).get('outcome'), same_as_committed=key(a) == key(b))
    if key(a) != key(b):
        diffs.append(('continuity new', k, key(b), key(a)))
out['continuity_new'] = cn

# PR #89 (column flow).
fref = load('evaluations/paragraph_frame_column_flow/results.json')
fseq, fsing, frec = load('tmp/reg89/sequences.json'), load('tmp/reg89/singles.json'), load('tmp/reg89/recognition.json')
ff = {}
def tiers(step):
    audit = step.get('audit')
    saved = step['outcome'] == 'saved'
    return dict(saved=saved, safety=bool(audit and audit['safety']) if saved else None, quality=bool(audit and audit['quality']) if saved else None,
                continued_editing=step.get('continued_editing', {}).get('ok') if saved and 'continued_editing' in step else None)
for k, chain in fref['sequences'].items():
    got = fseq[k]['steps']
    rows = [(s['outcome'], s['as_expected'], tiers(s)) for s in got]
    want = [(s['outcome'], s['as_expected'], s['tiers']) for s in chain['steps']]
    ff[k] = dict(steps=[r[0] for r in rows], as_expected=all(r[1] for r in rows), same_as_committed=rows == want)
    if rows != want:
        diffs.append(('column sequence', k, rows, want))
fs = {}
for k, v in fref['singles'].items():
    s = fsing[k]
    row = (s['outcome'], s['as_expected'], tiers(s))
    fs[k] = dict(outcome=s['outcome'], as_expected=s['as_expected'], same_as_committed=row == (v['outcome'], v['as_expected'], v['tiers']))
    if not fs[k]['same_as_committed']:
        diffs.append(('column single', k, row, (v['outcome'], v['as_expected'], v['tiers'])))
pairs = {k: [(p['from_column'], p['to_column'], p['expected'], p['got'], p['correct']) for p in v['pairs']] for k, v in frec.items()}
want_pairs = {k: [tuple(p) for p in v] for k, v in fref['recognition']['pairs'].items()}
out['column_flow'] = dict(sequences=ff, singles=fs, recognition_same_as_committed=pairs == want_pairs,
                          recognition_correct=sum(p[4] for v in pairs.values() for p in v), recognition_total=sum(len(v) for v in pairs.values()))
if pairs != want_pairs:
    diffs.append(('recognition', pairs, want_pairs))
# PR #90 (InDesign): frozen requests and the same sample (same survey).
iref = load('evaluations/paragraph_frame_indesign/results.json')
ireq, isam = load('tmp/reg90/requests.json'), load('tmp/reg90/samples.json')
ir = {}
for k, v in iref['requests'].items():
    g = ireq[k]
    got = (g['outcome'], g['as_expected'], (g.get('audit') or {}).get('failed', []))
    want = (v['outcome'], v['as_expected'], v.get('failed', []))
    ir[k] = dict(outcome=g['outcome'], failed=got[2], same_as_committed=got == want)
    if got != want:
        diffs.append(('indesign request', k, got, want))
saved = [s for s in isam['results'] if s['outcome'] == 'saved']
key = lambda s: (s['document'], s['page'], s['selection'])
got_failed = {key(s): s['audit']['failed'] for s in saved if s['audit']['failed']}
want_failed = {key(s): s['failed'] for s in iref['samples']['failed']}
got_refused = {key(s): s['reason'] for s in isam['results'] if s['outcome'] == 'refused'}
want_refused = {key(s): s['reason'] for s in iref['samples']['refused']}
totals = dict(sampled=isam['sampled'], saved=len(saved), safety=sum(s['audit']['safety'] for s in saved), quality=sum(s['audit']['quality'] for s in saved))
want_totals = {k: iref['samples'][k] for k in totals}
out['indesign'] = dict(requests=ir, samples=dict(totals, committed=want_totals, same_as_committed=(totals == want_totals and got_failed == want_failed and got_refused == want_refused),
                                                 newly_failed=[list(k) + [v] for k, v in got_failed.items() if want_failed.get(k) != v],
                                                 newly_passed=[list(k) for k in want_failed if k not in got_failed and k not in got_refused],
                                                 newly_refused=[list(k) + [v] for k, v in got_refused.items() if k not in want_refused]))
if not out['indesign']['samples']['same_as_committed']:
    diffs.append(('indesign samples', totals, want_totals, out['indesign']['samples']['newly_failed'], out['indesign']['samples']['newly_passed'], out['indesign']['samples']['newly_refused']))
# Against the regression recorded by PR #90 (main 2aefe3e's own state).
rec = load('evaluations/paragraph_frame_indesign/results.json')['regression']
vs90 = []
for k, v in rec['continuity_sequences'].items():
    if v['steps'] != out['continuity_sequences'][k]['steps']:
        vs90.append(('continuity sequence', k, out['continuity_sequences'][k]['steps'], v['steps']))
for k, v in rec['continuity_new'].items():
    mine = out['continuity_new'][k]
    if (v['edit'], v['audit']) != (mine['edit'], mine['audit']):
        vs90.append(('continuity new', k, (mine['edit'], mine['audit']), (v['edit'], v['audit'])))
for k, v in rec['generalization'].items():
    mine = out['generalization'][k]
    if (v['initial'], v['initial_failed'], v['reedit'], v['reedit_failed']) != (mine['initial'], mine['initial_failed'], mine['reedit'], mine['reedit_failed']):
        vs90.append(('generalization', k, mine, v))
for part in ('sequences', 'singles'):
    for k, v in rec['column_flow'][part].items():
        mine = out['column_flow'][part][k]
        if {x: v[x] for x in v if x != 'same_as_committed'} != {x: mine[x] for x in mine if x != 'same_as_committed'}:
            vs90.append(('column ' + part, k, mine, v))
out['differences_from_pr90_recorded_regression'] = [list(map(str, d)) for d in vs90]
print('vs PR #90 recorded regression:', len(vs90))
for d in vs90:
    print(json.dumps(d, ensure_ascii=False, default=str)[:600])
out['differences'] = len(diffs)
Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
for d in diffs:
    print(json.dumps(d, ensure_ascii=False, default=str)[:1500])
print('differences', len(diffs))
