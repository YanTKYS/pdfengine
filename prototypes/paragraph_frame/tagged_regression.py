"""Compare the PR #87-#90 regression runs with their committed results and with the regressions PR #90, PR #91, PR #92, PR #94, PR #96, PR #98, PR #100, PR #102 and PR #104 recorded.

  .venv/bin/python -m prototypes.paragraph_frame.tagged_regression <regression.json> [<tagged root> <477.pdf> <pdf.js dir>]

Reads the runs of generalization_evaluate (tmp/reg87), continuity_evaluate
(tmp/reg88), column_flow_evaluate (tmp/reg89) and indesign_evaluate
(tmp/reg90, samples with --survey tmp/id-survey-final), each run with the same
arguments as in its PR. The output is passed to tagged_evaluate collect
(--regression-json). With a tagged root (tagged_evaluate requests and samples,
--survey tmp/tg-survey), the PR #91 requests and sampled edits are compared
with PR #91's committed results too; a sample that fails the safety audit
gets PR #91's auxiliary ``outside_changes`` beside it.
"""
import json
import os
import sys
from pathlib import Path

# Where the runs are (eval_hub runs them elsewhere): PDFENGINE_REG_ROOT, default tmp.
ROOT = os.environ.get('PDFENGINE_REG_ROOT', 'tmp')

load = lambda p: json.loads(Path(p).read_text())
out = {}
diffs = []

# PR #87 (generalization) against the reference recorded by PR #88/#89.
ref = load('evaluations/paragraph_frame_continuity/results.json')['regression']
gen = {}
for k, v in ref.items():
    initial = load(f'{ROOT}/reg87/{k}.audit.json')
    reedit = load(f'{ROOT}/reg87/{k}.lifecycle.audit.json')
    row = dict(initial=initial['outcome'], initial_failed=initial['failed'], reedit=reedit['outcome'], reedit_failed=reedit['failed'])
    row['same_as_committed'] = (row['initial'], row['initial_failed'], row['reedit'], row['reedit_failed']) == (
        v['initial']['quality'], v['initial']['failed'], v['pdf_alone_reedit']['quality'], v['pdf_alone_reedit']['failed'])
    if not row['same_as_committed']:
        diffs.append(('generalization', k, row, v['initial'], v['pdf_alone_reedit']))
    gen[k] = row
out['generalization'] = gen

# PR #88 (continuity): sequences and new requests.
cref = load('evaluations/paragraph_frame_continuity/results.json')
seq = load(f'{ROOT}/reg88/sequences.json')
cs = {}
for k, chain in cref['sequences'].items():
    got = seq[k]['steps']
    key = lambda s: (s['outcome'], (s.get('audit') or {}).get('outcome'), (s.get('audit') or {}).get('failed'), s.get('planned_lines'))
    same = [key(a) == key(b) for a, b in zip(chain['steps'], got)] + [len(chain['steps']) == len(got)]
    cs[k] = dict(steps=[(s.get('audit') or {}).get('outcome') or s['outcome'] for s in got], same_as_committed=all(same))
    if not all(same):
        diffs.append(('continuity sequence', k, [key(s) for s in got], [key(s) for s in chain['steps']]))
out['continuity_sequences'] = cs
new = load(f'{ROOT}/reg88/new.json')
cn = {}
for k, v in cref['new'].items():
    a, b = v['B2'], new[k]['B2']
    key = lambda s: (s['edit'], s.get('lines'), (s.get('audit') or {}).get('outcome'), (s.get('audit') or {}).get('failed'), (s.get('reedit') or {}).get('outcome') if isinstance(s.get('reedit'), dict) else s.get('reedit'))
    cn[k] = dict(edit=b['edit'], audit=(b.get('audit') or {}).get('outcome'), same_as_committed=key(a) == key(b))
    if b.get('audit_implicit_joins'):
        cn[k]['audit_implicit_joins'] = b['audit_implicit_joins']['outcome']
    if key(a) != key(b):
        diffs.append(('continuity new', k, key(b), key(a)))
out['continuity_new'] = cn

# PR #89 (column flow).
fref = load('evaluations/paragraph_frame_column_flow/results.json')
fseq, fsing, frec = load(f'{ROOT}/reg89/sequences.json'), load(f'{ROOT}/reg89/singles.json'), load(f'{ROOT}/reg89/recognition.json')
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
ireq, isam = load(f'{ROOT}/reg90/requests.json'), load(f'{ROOT}/reg90/samples.json')
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
        vs90.append(('continuity new', k, (mine['edit'], mine['audit'], mine.get('audit_implicit_joins')), (v['edit'], v['audit'])))
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
# Against the regression recorded by PR #91 (main 8c60fe3's own state).
rec91 = load('evaluations/paragraph_frame_tagged/results.json')['regression']
vs91 = []
strip = lambda v: {x: v[x] for x in v if x != 'same_as_committed'}
for part in ('generalization', 'continuity_sequences', 'continuity_new'):
    for k, v in rec91[part].items():
        mine = {x: out[part][k].get(x) for x in strip(v)}
        if strip(v) != mine:
            vs91.append((part, k, dict(out[part][k]), strip(v)))
for part in ('sequences', 'singles'):
    for k, v in rec91['column_flow'][part].items():
        if strip(v) != strip(out['column_flow'][part][k]):
            vs91.append(('column ' + part, k, strip(out['column_flow'][part][k]), strip(v)))
for k, v in rec91['indesign']['requests'].items():
    if strip(v) != strip(ir[k]):
        vs91.append(('indesign request', k, strip(ir[k]), strip(v)))
mine = out['indesign']['samples']
for x in ('sampled', 'saved', 'safety', 'quality', 'newly_failed', 'newly_passed', 'newly_refused'):
    if mine[x] != rec91['indesign']['samples'][x]:
        vs91.append(('indesign samples', x, mine[x], rec91['indesign']['samples'][x]))
out['differences_from_pr91_recorded_regression'] = [list(map(str, d)) for d in vs91]
print('vs PR #91 recorded regression:', len(vs91))
for d in vs91:
    print(json.dumps(d, ensure_ascii=False, default=str)[:900])
# PR #91 (tagged PDFs): frozen requests and the same 52 sampled edits, when
# their run is given (tagged_evaluate --phase requests and samples).
if len(sys.argv) > 2:
    from types import SimpleNamespace as sources
    from .indesign_evaluate import source_of
    from .tagged_evaluate import outside_changes, survey_table
    tg = Path(sys.argv[2])
    tref = load('evaluations/paragraph_frame_tagged/results.json')
    treq, tsam = load(tg / 'requests.json'), load(tg / 'samples.json')
    tr = {}
    for k, v in tref['requests'].items():
        for phase in ('frozen', 'substitute'):
            if phase not in v:
                continue
            g, w = treq[k].get(phase) or {}, v[phase]
            got = (g.get('outcome'), (g.get('audit') or {}).get('failed', []), (g.get('structure') or {}).get('failed', []), (g.get('continued_editing') or {}).get('ok'))
            want = (w.get('outcome'), w.get('failed', []), w.get('structure_failed', []), (w.get('continued_editing') or {}).get('ok'))
            tr[f'{k}:{phase}'] = dict(outcome=got[0], failed=got[1], structure_failed=got[2], continued_editing=got[3], same_as_committed=got == want,
                                      committed=None if got == want else dict(outcome=want[0], failed=want[1], structure_failed=want[2], continued_editing=want[3]))
    key = lambda s: (s['document'], s['page'], s['selection'], s['kind'])
    want_refused = {key(s): s['reason'] for s in tref['samples']['refused']}
    want_failed = {key(s): s['failed'] for s in tref['samples']['failed']}
    rows = survey_table(os.environ.get('PDFENGINE_TG_SURVEY', 'tmp/tg-survey'))
    boxes = {(r['document'], r['page'], r['selection']): r['bbox'] for r in rows}
    samples = []
    for n, s in enumerate(tsam['results']):
        row = dict(document=s['document'], page=s['page'], selection=s['selection'], kind=s['kind'], outcome=s['outcome'], reason=s.get('reason'))
        if s['outcome'] == 'saved':
            row.update(failed=s['audit']['failed'], safety=s['audit']['safety'], quality=s['audit']['quality'], structure=s['structure']['passed'],
                       continued_editing=s['continued_editing'], supplementary=s['supplementary'])
            if not s['audit']['safety']:
                # The auxiliary evaluation PR #91 reports beside a failed safety audit.
                folder = tg / 'samples' / f'{n // 2:03d}-{s["kind"]}'
                out_pdf = folder / 'out.pdf'
                proposal, save = load(out_pdf.with_suffix('.proposal.json')), load(out_pdf.with_suffix('.save.json'))
                x0, y0, x1, y1 = boxes[(s['document'], s['page'], s['selection'])]
                pitch = max(4.0, (y1 - y0) / max(1, len(proposal['lines'])))
                region = [x0 - 3, y0 - 3, x1 + 3, y1 + 3 + (pitch if s['kind'] == 'lengthen' else 0)]
                source = sources(fusei=Path(sys.argv[3]), pdfjs=Path(sys.argv[4]))
                row['outside_changes'] = outside_changes(str(source_of(s['document'], source)), str(out_pdf), s['page'], region, proposal, save, folder)
        k = key(s)
        was = ('refused', want_refused[k]) if k in want_refused else ('saved', want_failed.get(k, []))
        now = ('refused', row['reason']) if row['outcome'] == 'refused' else ('saved', row['failed'])
        row['same_as_committed'] = (was[0], was[1] if was[0] == 'saved' else None) == (now[0], now[1] if now[0] == 'saved' else None)
        row['committed'] = None if row['same_as_committed'] else dict(outcome=was[0], detail=was[1])
        samples.append(row)
    saved = [r for r in samples if r['outcome'] == 'saved']
    totals = dict(edits=len(samples), saved=len(saved), safety=sum(r['safety'] for r in saved), quality=sum(r['quality'] for r in saved),
                  structure=sum(r['structure'] for r in saved), continued_editing=sum(bool(r['continued_editing'].get('ok')) for r in saved))
    out['tagged'] = dict(requests=tr, samples=dict(totals=totals, committed={x: tref['samples'][x] for x in totals}, edits=samples))
    print('PR #91 requests differing:', [k for k, v in tr.items() if not v['same_as_committed']])
    print('PR #91 samples:', totals, 'committed', out['tagged']['samples']['committed'])
    for r in samples:
        if not r['same_as_committed']:
            print(' ', json.dumps({x: r.get(x) for x in ('document', 'selection', 'kind', 'outcome', 'reason', 'failed', 'committed', 'outside_changes')}, ensure_ascii=False)[:700])
# Against the regressions recorded by PR #92 (main 870709f's own state),
# PR #94 (main 2e2ec8b's), PR #96 (main bb9fd08's), PR #98 (main d1207db's), PR #100 (main 6b68494's), PR #102 (main 8d22172's)
# and PR #104 (main a7bb571's), when present.
for pr, recorded in (('92', Path('evaluations/paragraph_frame_fidelity/results.json')), ('94', Path('evaluations/paragraph_frame_roundtrip/results.json')),
                     ('96', Path('evaluations/paragraph_frame_multiedit/results.json')), ('98', Path('evaluations/paragraph_frame_placed/results.json')),
                     ('100', Path('evaluations/paragraph_frame_decoration/results.json')), ('102', Path('evaluations/paragraph_frame_restore/results.json')),
                     ('104', Path('evaluations/paragraph_frame_chain/results.json'))):
    rec92 = load(recorded).get('regression') if recorded.exists() else None
    if not rec92:
        continue
    vs92 = []
    for part in ('generalization', 'continuity_sequences', 'continuity_new'):
        for k, v in rec92[part].items():
            mine = {x: out[part][k].get(x) for x in v if x != 'same_as_committed'}
            if {x: v[x] for x in v if x != 'same_as_committed'} != mine:
                vs92.append((part, k, mine, {x: v[x] for x in v if x != 'same_as_committed'}))
    for part in ('sequences', 'singles'):
        for k, v in rec92['column_flow'][part].items():
            if strip(v) != strip(out['column_flow'][part][k]):
                vs92.append(('column ' + part, k, strip(out['column_flow'][part][k]), strip(v)))
    for k, v in rec92['indesign']['requests'].items():
        if strip(v) != strip(out['indesign']['requests'][k]):
            vs92.append(('indesign request', k, strip(out['indesign']['requests'][k]), strip(v)))
    for x in ('sampled', 'saved', 'safety', 'quality', 'newly_failed', 'newly_passed', 'newly_refused'):
        if out['indesign']['samples'][x] != rec92['indesign']['samples'][x]:
            vs92.append(('indesign samples', x, out['indesign']['samples'][x], rec92['indesign']['samples'][x]))
    if 'tagged' in out and 'tagged' in rec92:
        for k, v in rec92['tagged']['requests'].items():
            if strip(v) != strip(out['tagged']['requests'][k]):
                vs92.append(('tagged request', k, strip(out['tagged']['requests'][k]), strip(v)))
        if out['tagged']['samples']['totals'] != rec92['tagged']['samples']['totals']:
            vs92.append(('tagged samples', out['tagged']['samples']['totals'], rec92['tagged']['samples']['totals']))
        before = {(r['document'], r['page'], r['selection'], r['kind']): (r['outcome'], r.get('failed')) for r in rec92['tagged']['samples']['edits']}
        for r in out['tagged']['samples']['edits']:
            k = (r['document'], r['page'], r['selection'], r['kind'])
            if before.get(k) != (r['outcome'], r.get('failed')):
                vs92.append(('tagged sample', k, (r['outcome'], r.get('failed')), before.get(k)))
    out[f'differences_from_pr{pr}_recorded_regression'] = [list(map(str, d)) for d in vs92]
    print(f'vs PR #{pr} recorded regression:', len(vs92))
    for d in vs92:
        print(json.dumps(d, ensure_ascii=False, default=str)[:900])
out['differences'] = len(diffs)
Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
for d in diffs:
    print(json.dumps(d, ensure_ascii=False, default=str)[:1500])
print('differences', len(diffs))
