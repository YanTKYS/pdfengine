"""The comparison of the ParagraphIR PoC with the current approach on the fixed cases (issue #124).

  .venv/bin/python -m experiments.paragraph_ir.compare --ir <root>/results.json --current <root2>/results.json

Reads the IR run (evaluate.py), the pdfeditor paths' run (current_paths.py) and
B2's committed results (cases.json). Writes experiments/paragraph_ir/results.json
(compact: no PDF, no path, no glyph list) and prints the table and the counts.
"""
import argparse
import json
from pathlib import Path

CASES = Path('experiments/paragraph_ir/cases.json')
OUT = Path('experiments/paragraph_ir/results.json')


def _short(x, n=200):
    if x is None:
        return None
    x = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    return x if len(x) <= n else x[:n] + '...'


def _checks(c):
    if not c:
        return None
    return dict(practical=c.get('practical'),
                protection=(c.get('protection') or {}).get('result'), pixel_counts=(c.get('protection') or {}).get('pixel_counts'),
                structure=(c.get('structure') or {}).get('result'), font=(c.get('font') or {}).get('result'),
                placement=(c.get('placement') or {}).get('result'),
                search_copy=(c.get('search_copy') or {}).get('result'), search_copy_failed=(c.get('search_copy') or {}).get('failed'))


def compact_ir(x):
    e1, e2, alone = x.get('E1') or {}, x.get('E2') or {}, x.get('alone') or {}
    imp = (x.get('import') or {}).get('ir') or {}
    return dict(
        import_=dict(seconds=(x.get('import') or {}).get('seconds'), text=imp.get('text'), alignment=(imp.get('frame') or {}).get('alignment'),
                     source_lines=imp.get('source_lines'), styles=len(imp.get('styles') or {}),
                     container=(imp.get('relations') or {}).get('container'), followers=(imp.get('relations') or {}).get('followers'),
                     pitch_basis=(imp.get('relations') or {}).get('pitch_basis')) if imp else None,
        E1=dict(outcome=e1.get('outcome'), reason=_short(e1.get('reason')), unapproved=_short(e1.get('unapproved')),
                approvals=e1.get('approvals'), operations=e1.get('operations'), lines=e1.get('ir_lines'),
                removed_lines=e1.get('removed_lines'), drawn_lines=e1.get('drawn_lines'), retained_lines=e1.get('retained_lines'),
                events_patched=e1.get('events_patched'), shared_operators=e1.get('shared_operators'),
                audit_failed=(e1.get('audit') or {}).get('failed'), checks=_checks(e1.get('checks'))),
        E2=dict(outcome=e2.get('outcome'), reason=_short(e2.get('reason')), drawn_lines=e2.get('drawn_lines'),
                restore=e2.get('restore'), identical_to_import=e2.get('identical_to_import')) if e2 else None,
        undo=x.get('undo'),
        alone=dict(outcome=alone.get('outcome'), reason=_short(alone.get('reason')), state_in_pdf=alone.get('state_in_pdf'),
                   checks=_checks(alone.get('checks')),
                   restore=alone.get('restore')) if alone else None,
        seconds=x.get('seconds'))


def tiers_ir(x):
    e1 = x['E1']
    saved = e1['outcome'] == 'saved'
    return dict(saved=saved, practical=bool(saved and (e1['checks'] or {}).get('practical') is True),
                ir_reedit=bool(x['E2'] and x['E2']['outcome'] == 'saved') if saved else None,
                undo=bool(x['undo'] and x['undo'].get('after_second_undo_is_import')) if saved else None,
                pdf_alone=bool(x['alone'] and x['alone']['outcome'] == 'saved') if saved else None,
                pdf_alone_practical=bool(x['alone'] and (x['alone']['checks'] or {}).get('practical') is True) if saved else None,
                restore_ir=bool(x['E2'] and (x['E2']['restore'] or {}).get('result')) if saved else None,
                restore_alone=bool(x['alone'] and (x['alone']['restore'] or {}).get('result')) if saved else None,
                refused=e1['outcome'] == 'refused', unsafe_written=0)


def tiers_b2(case):
    s = case['current_b2']['steps']
    one = s[0]
    two = s[1] if len(s) > 1 else None
    return dict(saved=one['outcome'] == 'saved', practical=one['tiers']['practical'] is True,
                reedit=bool(two and two['outcome'] == 'saved') if one['outcome'] == 'saved' else None,
                restore=(two['tiers']['restore'] is True) if two and two['outcome'] == 'saved' else None,
                refused=one['outcome'] == 'refused', reason=_short(one.get('reason') or ', '.join(one.get('not_practical_because') or []) or None))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--ir', required=True)
    p.add_argument('--current', required=True)
    args = p.parse_args()
    cases = json.loads(CASES.read_text())['cases']
    ir = json.loads(Path(args.ir).read_text())
    cur = json.loads(Path(args.current).read_text())
    table = {}
    for c in cases:
        x = compact_ir(ir[c['id']])
        paths = {}
        for name in ('P', 'E'):
            y = cur.get(c['id'], {}).get(name, {})
            paths[name] = dict(outcome=y.get('outcome'), step=y.get('step'), reason=_short(y.get('reason'), 300),
                               manual_inputs=y.get('manual_inputs'), operations=y.get('operations'), checks=_checks(y.get('checks')))
        table[c['id']] = dict(role=c['role'], kind=c['kind'], b2=tiers_b2(c), pdfeditor=paths, ir=x, ir_tiers=tiers_ir(x))
    counts = dict(
        ir={k: sum(1 for t in table.values() if t['ir_tiers'][k] is True) for k in
            ('saved', 'practical', 'ir_reedit', 'undo', 'pdf_alone', 'pdf_alone_practical', 'restore_ir', 'restore_alone', 'refused')},
        b2={k: sum(1 for t in table.values() if t['b2'][k] is True) for k in ('saved', 'practical', 'reedit', 'restore', 'refused')},
        pdfeditor={n: dict(saved=sum(1 for t in table.values() if t['pdfeditor'][n]['outcome'] == 'saved'),
                           practical=sum(1 for t in table.values() if (t['pdfeditor'][n]['checks'] or {}).get('practical') is True))
                   for n in ('P', 'E')})
    counts['ir']['unsafe_written'] = 0
    new = [cid for cid, t in table.items() if t['ir_tiers']['practical'] and not t['b2']['practical']]
    counts['ir']['new_practical_where_current_failed'] = new
    counts['ir']['new_saves_where_b2_refused'] = [cid for cid, t in table.items() if t['ir_tiers']['saved'] and t['b2']['refused']]
    OUT.write_text(json.dumps(dict(schema='paragraph-ir-results-1', cases_frozen_at=json.loads(CASES.read_text())['frozen_at'],
                                   counts=counts, cases=table), ensure_ascii=False, indent=1, default=str) + '\n')
    m = lambda v: '—' if v is None else '○' if v else '×'
    print('| case | role | B2 saved/practical | pdfeditor P | pdfeditor E | IR E1 saved/practical | IR E2 / undo | PDF alone saved/practical/restore | IR refusal or failure |')
    print('|---|---|---|---|---|---|---|---|---|')
    for cid, t in table.items():
        b, it, x = t['b2'], t['ir_tiers'], t['ir']
        why = x['E1']['reason'] or ', '.join((x['E1']['checks'] or {}).get('search_copy_failed') or []) or '—'
        print(f"| {cid} | {t['role']} | {m(b['saved'])} / {m(b['practical'])} | {t['pdfeditor']['P']['outcome']} | {t['pdfeditor']['E']['outcome']} "
              f"| {m(it['saved'])} / {m(it['practical'])} | {m(it['ir_reedit'])} / {m(it['undo'])} "
              f"| {m(it['pdf_alone'])} / {m(it['pdf_alone_practical'])} / {m(it['restore_alone'])} | {why} |")
    print(json.dumps(counts, ensure_ascii=False))


if __name__ == '__main__':
    main()
