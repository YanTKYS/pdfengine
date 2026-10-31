"""Paragraph and row inference: R13 (moved lines and a grown paragraph on reopen) and S2 (a footnote's hanging indent) (issue #110).

  .venv/bin/python -m prototypes.paragraph_frame.rows_evaluate --phase freeze --fusei <477.pdf> --pdfjs <pdf.js dir>
  .venv/bin/python -m prototypes.paragraph_frame.rows_evaluate --phase run --label old|new --code <tree> --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir>
  .venv/bin/python -m prototypes.paragraph_frame.rows_evaluate --phase collect --roots old=<dir> new=<dir>

``freeze`` (before the implementation) records each case's input (hash),
page, selection and edits, and what the B2 at the freeze observes in the
source: the rows near the selection (x, baseline, size, text, glyph IDs and
origins), the paragraph groups, the detected paragraph (first-line x,
continuation x, line pitch) and the chain candidates. The expected appearance
of each case is written by hand in CASES; nothing of it enters the engine.

``run``, per case, with the B2 of ``--code``: the edit, then the edit back
from the saved PDF alone (chain_evaluate.run_case: candidates are listed, a
save without approving them must be refused, the listed ones are approved;
an approval is never carried from one step to the next). Each saved step
keeps the existing audits (indesign_evaluate.audit: safety and quality apart)
and the approved-change audit (approved_audit) side by side, and adds:

* ``rows``: the paragraph groups of the saved page near the edit, as the
  B2 under evaluation observes them (raw, without the stored records), and
  whether a group joins the edited paragraph's lines with lines the edit
  moved (``moved_apart``);
* ``reselect``: the paragraph the B2 finds for the new text in the saved PDF
  alone (with the stored records), and whether its lines are exactly the
  edited paragraph's;
* ``geometry``: the x and baselines of the edited paragraph's lines, against
  the case's expected first-line x, continuation x and pitch.

The occurrence of the selection is the one whose paragraph (as the engine
finds it, with the stored records) starts in the frozen box: the earlier
evaluators' raw grouping cannot find a paragraph that only a stored record
names (PR #106, R13). Negative cases are observations only: the groups the
B2 finds there must stay as frozen. Extra cases are counted apart from R13
and S2; a refusal or a missing input is never a success.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from . import chain_evaluate as ce
from . import eval_records as er
from . import smoke_evaluate
from .fidelity_evaluate import source_of

MANIFEST = Path('evaluations/paragraph_rows/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'

CASES = [
    dict(id='R13', group='target', document='osaka_fusei_477', page=4, selection='D-25 ペ', edit=dict(old='D-25 ペ', new='D-25 ペD-25 ペルー'),
         expect=dict(
             first=('saved on approving the chain candidate of the lines below (D-26, D-27); refused without it'),
             saved=('the grown paragraph is found again from the saved PDF alone, its lines exactly the edited ones; the moved lines (D-26, D-27) '
                    'are never grouped with the paragraph\'s new lines'),
             back=('the edit back lists the moved lines as a chain candidate again (they moved on approval; moving them back needs approval), '
                   'refused without it; saved approving it; every glyph of the page at its source origin (0.01 pt), drawings, links, text and '
                   'pixels as in the source'),
             appearance='the list keeps one item per line; the moved items keep their x and their pitch (10.3 pt)')),
    dict(id='S2', group='target', document='lo_migration_ja', page=1, selection='日本語フォントについては',
         edit=dict(old='日本語フォントについては', new='日本語フォントについては日本語フォントについては'),
         expect=dict(
             first='saved; the footnote number stays at its x, the added and moved text continues at the hanging indent',
             geometry=dict(first_x=56.8, continuation_x=73.8, pitch=13.8, tolerance=.1),
             saved='the footnote is one paragraph from the saved PDF alone',
             back='the edit back from the saved PDF alone restores the footnote (glyph origins 0.01 pt, text, pixels)',
             appearance='footnote number at 56.8; every following line at 73.8, 13.8 pt apart')),
    # Extra cases, counted apart. Positive: another footnote of the same writer.
    dict(id='X1', group='extra', document='lo_migration_ja', page=6, selection='訳者は把握していません。',
         edit=dict(old='訳者は把握していません。', new='訳者は把握していません。訳者は把握していません。'),
         expect=dict(first='saved; added text at the hanging indent', geometry=dict(first_x=56.8, continuation_x=73.8, pitch=13.8, tolerance=.1),
                     back='restored')),
    # Negative (observation only): rows that a hanging-indent rule could join, and must not.
    dict(id='N1', group='negative', document='osaka_fusei_477', page=2, near=[505, 690, 735, 730],
         expect=dict(groups='unchanged: the ※ row is drawn across a table\'s two columns (a gutter inside the row); its next row is not joined')),
    dict(id='N2', group='negative', document='osaka_fusei_477', page=7, near=[405, 0, 600, 900],
         expect=dict(groups='unchanged: ※ notes, one per line, each starting at the same x')),
    dict(id='N3', group='negative', document='tracemonkey', page=1, near=[40, 0, 600, 800],
         expect=dict(groups='unchanged: a numbered heading (1.Introduction) and the paragraph below it')),
    dict(id='N4', group='negative', document='osaka_fusei_477', page=4, near=[365, 640, 620, 760],
         expect=dict(groups='unchanged: list items (D-xx name), one per line')),
]

# Run in the code tree under evaluation (old or new B2), so its own observation is measured.
OBSERVE = '''
import json,sys
sys.path.insert(0,{code!r})
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import _Analysis
from prototypes.paragraph_frame.auto_detect import group_text, observe, detect
pdf,page,box,selection={pdf!r},{page},{box!r},{selection!r}
out={{}}
c=ContentPage(pdf,page)
try:
    _,_,groups=observe(c,observe_marked_content(pdf,page),_Analysis(pdf,page))
    near=lambda r:box[0]-1<=r['x']<=box[2]+1 and box[1]-1<=r['baseline']<=box[3]+1
    out['groups']=[[dict(text=''.join(c.actual[i]['unicode'] for i in r['glyph_ids']),x=round(r['x'],2),baseline=round(r['baseline'],2),size=round(r['size'],2),
                         glyphs=[[c.actual[i]['unicode'],c.actual[i]['gid'],round(c.actual[i]['origin'][0],3),round(c.actual[i]['origin'][1],3)] for i in r['glyph_ids']])
                    for r in g] for g in groups if any(near(r) for r in g)]
finally:c.close()
if selection:
    found=[]
    for k in range(12):
        try:c,ps,spec,start,ev=detect(pdf,page,selection,occurrence=k)
        except Exception as exc:
            if 'occurrence not found' not in str(exc):found.append(dict(occurrence=k,refused=str(exc)[-300:]))
            break
        try:
            lines=ev['observed']['lines'];n=len(ps[0].ids)
            own=[l for l in lines if set(l['glyph_ids'])<=set(ps[0].ids)]
            found.append(dict(occurrence=k,lines=[dict(text=l['text'],x=round(l['x'],2),baseline=round(l['baseline'],2)) for l in own],
                              first_x=round(spec.get('first_x',spec.get('frame',[0])[0]),2),left=round(spec.get('left',0),2),line_height=round(spec.get('line_height') or 0,2),
                              conditions=ev['conditions']['status'],first_bbox=[round(v,2) for v in own[0]['bbox']] if own else None,
                              chain_rows=[r['text'] for r in spec.get('chain_rows',[])][:4],followers=len(ps)-1))
        finally:c.close()
    out['paragraphs']=found
print(json.dumps(out,ensure_ascii=False))
'''


def observation(code, pdf, page, box, selection=None):
    """Groups near ``box`` and the paragraphs found for ``selection``, by the B2 of ``code``."""
    run = subprocess.run([sys.executable, '-c', OBSERVE.format(code=str(code), pdf=str(Path(pdf).resolve()), page=page, box=list(box), selection=selection)],
                         capture_output=True, text=True, cwd=code)
    lines = run.stdout.strip().splitlines()
    if run.returncode or not lines:
        return dict(error=run.stderr.strip()[-400:])
    return json.loads(lines[-1])


def make_locate(code_of):
    def locate(source, page, old, box, code):
        """The occurrence whose paragraph (as the engine finds it, stored records applied) starts in ``box``."""
        found = observation(code, source, page, box, old).get('paragraphs') or []
        for p in found:
            b = p.get('first_bbox')
            if b and box[0] - 3 <= (b[0] + b[2]) / 2 <= box[2] + 3 and box[1] - 3 <= (b[1] + b[3]) / 2 <= box[3] + 3:
                return p['occurrence']
        raise SystemExit(f'cannot locate {old!r} in the frozen paragraph of {source} with {code}: {json.dumps(found, ensure_ascii=False)[-300:]}')
    return locate


def phase_freeze(args):
    code = Path('.').resolve()
    documents, cases = {}, []
    for case in CASES:
        path = source_of(case['document'], args)
        if not path.exists():
            cases.append(dict(case, status='input missing'))
            continue
        documents[case['document']] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        if case['group'] == 'negative':
            seen = observation(code, path, case['page'], case['near'])
            cases.append(dict(case, observed=dict(groups=[[r['text'] for r in g] for g in seen['groups']])))
            print(case['id'], len(seen['groups']), 'groups', flush=True)
            continue
        from .placed_evaluate import block
        box = block(path, case['page'], case['selection'])
        x0, y0, x1, y1 = box
        seen = observation(code, path, case['page'], [x0 - 20, y0 - 20, x1 + 20, y1 + 60], case['selection'])
        para = seen['paragraphs'][0]
        from .auto import propose
        p = propose(str(path), case['page'], case['edit']['old'], case['edit']['old'])
        cases.append(dict(case, box=box, paragraph=p['paragraphs'][0], lines=len(para['lines']),
                          observed=dict(groups=seen['groups'], paragraph=para,
                                        chain=[dict(id=c['id'], line=c.get('line') or c.get('text')) for c in (propose(str(path), case['page'], case['edit']['old'], case['edit']['new'])['evidence'].get('chain') or {}).get('candidates', [])])))
        print(case['id'], box, para['first_x'], para['left'], para['line_height'], [l['text'][:20] for l in para['lines']], flush=True)
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(schema='paragraph-rows-manifest-1', frozen_at=rev,
                    policy=('Frozen before the implementation (issue #110). R13 and S2 are the targets; X cases are extra positives and N cases '
                            'negatives (observation only), all counted apart. Each edit runs the edit, then the edit back from the saved PDF alone; '
                            'candidates are listed, a save without approving them must be refused, and the listed ones are approved; an approval is '
                            'never carried from one step to the next. Saved, practical (existing audit and approved-change audit side by side, '
                            'quality, structure, links), re-edit, restore and refusal are recorded apart. A lengthened paragraph need not be '
                            'pixel-identical; search that is not verified is no practical success; a fixture is never a real-PDF success. No PDF, '
                            'string, coordinate or page enters the engine.'),
                    procedure=['freeze: the observation of the B2 at the freeze (rows, groups, paragraph, chain candidates)',
                               'run old (main) and new: edit; reopen the saved PDF alone (raw groups, the paragraph of the new text, geometry); '
                               'edit back; compare with the source',
                               'negative: the groups near the place, by old and new'],
                    documents=documents, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')


def moved_apart(groups, lines, moved):
    """No group (its rows' texts) holds both a line of the edited paragraph and
    an element the edit moved (its text, as the save record names it: the
    element's rows joined)."""
    return not any(set(g) & set(lines) and any(m and m in ''.join(g) for m in moved) for g in groups)


def geometry(rows, expect):
    if not rows or not expect:
        return None
    t = expect['tolerance']
    pitches = [round(b['baseline'] - a['baseline'], 3) for a, b in zip(rows, rows[1:])]
    ok = (abs(rows[0]['x'] - expect['first_x']) <= t and all(abs(r['x'] - expect['continuation_x']) <= t for r in rows[1:])
          and all(abs(p - expect['pitch']) <= t for p in pitches))
    return dict(ok=ok, x=[r['x'] for r in rows], pitches=pitches)


def run_case(code, case, source, root):
    # The frozen box names the paragraph (the B2 under evaluation may find it larger or smaller).
    ce.block = lambda source, page, selection: case['box']
    ce.locate = make_locate(code)
    ce.chain_step = smoke_evaluate.chain_step
    smoke_evaluate._chain_step = _chain_step
    seq = ce.run_case(code, case, source, root)
    old, new = case['edit']['old'], case['edit']['new']
    for n, r in enumerate(seq):
        if r['outcome'] != 'saved':
            continue
        output = root / case['id'] / f'{n + 1}' / 'out.pdf'
        text = new if n == 0 else old
        x0, y0, x1, y1 = case['box']
        seen = observation(code, output, case['page'], [x0 - 20, y0 - 20, x1 + 20, y1 + 80], text)
        moved = [m.get('line') for m in (r.get('save') or {}).get('chain_moved') or []]
        found = [p for p in seen.get('paragraphs', []) if 'lines' in p]
        para = found[0] if found else None
        r['rows'] = dict(groups=[[x['text'] for x in g] for g in seen.get('groups', [])],
                         moved=moved)
        r['rows']['moved_apart'] = moved_apart(r['rows']['groups'], r.get('lines', []), moved) if moved else None
        r['reselect'] = dict(found=para is not None, lines=[l['text'] for l in para['lines']] if para else None,
                             exact=para is not None and [l['text'] for l in para['lines']] == r.get('lines'),
                             conditions=para and para['conditions'], chain_rows=para and para['chain_rows'], error=seen.get('error'))
        r['geometry'] = geometry(para['lines'] if para else None, case['expect'].get('geometry') if n == 0 else None)
        print(case['id'], n + 1, json.dumps(dict(moved_apart=r['rows']['moved_apart'], reselect=r['reselect']['exact'], geometry=r['geometry']), ensure_ascii=False), flush=True)
    return seq


_chain_step = ce.chain_step


def phase_run(args):
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path(args.code).resolve()
    only = set(args.only or [])
    path = root / 'results.json'
    results = json.loads(path.read_text()) if path.exists() and only else {}
    for case in manifest['cases']:
        if only and case['id'] not in only:
            continue
        if case.get('status') == 'input missing':
            results[case['id']] = [dict(outcome='input missing')]
            continue
        source = source_of(case['document'], args).resolve()
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['documents'][case['document']]['sha256']:
            results[case['id']] = [dict(outcome='input differs')]
            continue
        if case['group'] == 'negative':
            seen = observation(code, source, case['page'], case['near'])
            groups = [[r['text'] for r in g] for g in seen.get('groups', [])]
            results[case['id']] = [dict(outcome='observed', groups=groups, unchanged=groups == case['observed']['groups'], error=seen.get('error'))]
            print(case['id'], 'unchanged' if groups == case['observed']['groups'] else 'CHANGED', flush=True)
        else:
            try:
                results[case['id']] = run_case(code, case, source, root)
            except Exception as exc:  # an evaluator error ends this case only, and is reported
                results[case['id']] = [dict(old=case['edit']['old'], new=case['edit']['new'], outcome='not run', reason=f'evaluator error: {type(exc).__name__}: {exc}'[:300])]
        path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def records(results, manifest):
    groups = {c['id']: c['group'] for c in manifest['cases']}
    out = []
    for cid, seq in results.items():
        group = 'real' if groups[cid] == 'target' else 'real-' + groups[cid]
        for n, x in enumerate(seq):
            if x.get('outcome') == 'observed':
                out.append(dict(evaluation='rows', case=cid, step='observe', group=group, mode='faithful', outcome='observed', reason_class=None, reason=None,
                                failed=[] if x['unchanged'] else ['groups changed'], checks=dict(groups_unchanged=x['unchanged']),
                                tiers=dict(saved=None, practical=None, reedit=None, restore=None, refused=None)))
                continue
            r = smoke_evaluate.records({cid: [x]})[0] if x.get('outcome') not in ('input missing', 'input differs') else None
            if r is None:
                continue
            r.update(evaluation='rows', step=str(n + 1), group=group)
            rows = x.get('rows') or {}
            if rows.get('moved') or (rows.get('groups') and (x.get('save') or {}).get('chain_moved')):
                moved = rows.get('moved') or [m.get('line') for m in x['save']['chain_moved']]
                r['checks']['moved_apart'] = moved_apart(rows['groups'], x.get('lines', []), moved)
            if x.get('reselect'):
                r['checks']['reselect_exact'] = x['reselect']['exact']
            if x.get('geometry'):
                r['checks']['geometry'] = x['geometry']['ok']
            out.append(r)
    return out


def phase_collect(args):
    manifest = json.loads(MANIFEST.read_text())
    runs = dict(x.split('=', 1) for x in args.roots)
    compact = lambda x: {k: v for k, v in x.items() if k not in ('frame', 'seconds', 'occurrence', 'decorations', 'rounds')}
    out = dict(schema='paragraph-rows-results-1', manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), runs={})
    for label, root in runs.items():
        results = json.loads((Path(root) / 'results.json').read_text())
        recs = records(results, manifest)
        out['runs'][label] = dict(summary=er.summary(recs), records=recs, results={k: [compact(x) for x in seq] for k, seq in results.items()})
    if {'old', 'new'} <= set(runs):
        out['comparison'] = er.compare(out['runs']['old']['records'], out['runs']['new']['records'])
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    print(json.dumps({k: v['summary'] for k, v in out['runs'].items()}, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('freeze', 'run', 'collect'), required=True)
    p.add_argument('--label')
    p.add_argument('--code', default='.')
    p.add_argument('--root')
    p.add_argument('--roots', nargs='*')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--only', nargs='*')
    args = p.parse_args()
    dict(freeze=phase_freeze, run=phase_run, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
