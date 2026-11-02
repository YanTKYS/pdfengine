"""Approved panel extension: a lengthened paragraph whose rectangular background panel grows down on approval (issue #112).

  .venv/bin/python -m prototypes.paragraph_frame.panel_evaluate --phase freeze --fusei <477.pdf> --pdfjs <pdf.js dir>
  .venv/bin/python -m prototypes.paragraph_frame.panel_evaluate --phase run --label old|new --code <tree> --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir>
  .venv/bin/python -m prototypes.paragraph_frame.panel_evaluate --phase audit --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir>
  .venv/bin/python -m prototypes.paragraph_frame.panel_evaluate --phase collect --roots old=<dir> new=<dir>

``freeze`` (before the implementation) records each case's input (hash),
page, selection and edit, what the B2 at the freeze proposes and why its save
is refused, and the rectangles drawn around the paragraph (operators' kind,
fill, stroke, width, bounds) with what lies below them. The expected outcome
is written by hand in CASES; nothing of it enters the engine.

``run``, per case, with the B2 of ``--code``: the edit, then the edit back
from the saved PDF alone. Each step proposes and records every candidate it
lists (panel extensions, chain elements, line decorations), saves without
approving any (a step that needs one must be refused), then approves exactly
the listed candidates (3 rounds at most) and saves. An approval is never
carried from one step to the next. A saved step keeps the existing audit
(indesign_evaluate.audit: safety and quality apart) beside the approved-change
audit (approved_audit), and adds ``panel``: the rectangles drawn where the
panel was, before and after (kind, colours, width, bounds), against the
approved extension. Negative cases must stay refused (the reason is kept).
``audit`` makes the approved-change audit again over a run's saved steps
(results updated in place; the saved PDFs are not touched): a changed audit,
not a changed edit.
A refusal or a missing input is never a success; fixtures are not counted
here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pymupdf

from . import chain_evaluate as ce
from . import eval_records as er
from . import rows_evaluate as rows
from . import smoke_evaluate
from .decoration_evaluate import cli
from .fidelity_evaluate import source_of

MANIFEST = Path('evaluations/paragraph_panel/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'

CASES = [
    # LibreOffice Writer: a figure's caption in a white rectangle (one 're f*') under the image, free space below.
    dict(id='L2', group='target', document='lo_newfeatures_ja', page=1, selection='複数ページフィールド選択',
         edit=dict(old='複数ページフィールド選択', new='複数ページフィールド選択複数ページフィールド選択'),
         expect='saved on approving the panel extension of its caption rectangle (down by the added line, top, sides, colour and order '
                'kept); refused without it; the edit back from the saved PDF alone saved'),
    dict(id='L6', group='extra', document='lo_newfeatures_ja', page=3, selection='枠の背景に設定されたグラデーション',
         edit=dict(old='枠の背景に設定されたグラデーション', new='枠の背景に設定されたグラデーション枠の背景に設定されたグラデーション'),
         expect='as L2'),
    dict(id='L8', group='extra', document='lo_newfeatures_ja', page=4, selection='MSIME2012の予測変換表示',
         edit=dict(old='MSIME2012の予測変換表示', new='MSIME2012の予測変換表示MSIME2012の予測変換表示'),
         expect='as L2'),
    # Microsoft Word: a text box (fill and outline as two 're' operators); its text is clipped to the box inside.
    dict(id='W1', group='clip', document='word_osaka_fire_notice', page=1, selection='直通：03-3595-2257',
         edit=dict(old='直通：03-3595-2257', new='直通：03-3595-2257 直通：03-3595-2257'),
         expect='refused: the text is clipped to the text box; extending the clip is not approved by issue #112 (decision)'),
    dict(id='W2', group='clip', document='word_osaka_guideline', page=1, selection='06-4397-3205',
         edit=dict(old='06-4397-3205', new='06-4397-3205 06-6944-9156'),
         expect='refused: as W1'),
    # Negative (the PR #106 edits R22, R07): a rectangle inside another background ending right below it, a rounded panel.
    dict(id='N22', group='negative', document='osaka_fusei_477', page=8, selection='番号、住所、',
         edit=dict(old='番号、住所、', new='番号、住所、番号、住所、氏名（様付き）をご記入ください。'),
         expect='refused: the lines below would leave their background; the panel is inside another one ending 5.6 pt below it'),
    dict(id='N07', group='negative', document='osaka_fusei_477', page=3, selection='その他、船・',
         edit=dict(old='その他、船・', new='その他、船・その他、船・自転車などでの'),
         expect='refused: the background is a rounded path, not a rectangle'),
]

RECTS = '''
import json,sys
sys.path.insert(0,{code!r})
import pymupdf
pdf,page,box={pdf!r},{page},{box!r}
with pymupdf.open(pdf) as d:
    p=d[page-1];R=pymupdf.Rect(box)
    near=[x for x in p.get_drawings() if x['rect'].intersects(R)]
    out=[dict(type=x['type'],items=[i[0] for i in x['items']],fill=x.get('fill'),color=x.get('color'),width=x.get('width'),
              rect=[round(v,2) for v in x['rect']]) for x in near]
print(json.dumps(out))
'''


def rects(code, pdf, page, box):
    """The drawings intersecting ``box`` (page space), as the B2 of ``code`` sees the page (PyMuPDF)."""
    run = subprocess.run([sys.executable, '-c', RECTS.format(code=str(code), pdf=str(Path(pdf).resolve()), page=page, box=list(box))],
                         capture_output=True, text=True, cwd=code)
    lines = [l for l in run.stdout.strip().split('\n') if l.startswith('[')]
    return json.loads(lines[-1]) if lines else dict(error=run.stderr[-300:])


def chain_step(code, start, case, old, new, after, output, sub, grow, pitch):
    """chain_evaluate.chain_step with the panel extensions a proposal lists (``--panel``), and the approved-change audit."""
    from .indesign_evaluate import audit
    from .tagged_evaluate import structure_audit
    occurrence = ce.locate(start, case['page'], old, case['box'], code)
    base = ['--source', str(Path(start).resolve()), '--page', str(case['page']), '--old', old, '--new', new, '--occurrence', str(occurrence)]
    parts = (('chain', '--chain'), ('decorations', '--decoration'), ('panels', '--panel'))

    def listed(approved):
        out = sub / 'proposal-only.json'
        if out.exists():
            out.unlink()
        rc, reason, _ = cli(code, ['propose'] + base + sum(([flag, i] for (k, flag) in parts for i in approved[k]), []), out)
        if rc:
            return None, reason
        ev = json.loads(out.read_text())['evidence']
        return {k: (ev.get(k) or {}) for k, _ in parts}, None

    ids = lambda found: {k: [c['id'] for c in (found or {}).get(k, {}).get('candidates', [])] for k, _ in parts}
    first, problem = listed({k: [] for k, _ in parts})
    record = dict(old=old, new=new, occurrence=occurrence, proposal_refused=problem, **{k: (first or {}).get(k) for k, _ in parts})
    plain = sub / 'unapproved.pdf'
    for p in (plain, plain.with_suffix('.proposal.json'), plain.with_suffix('.save.json')):
        if p.exists():
            p.unlink()
    rc, reason, seconds = cli(code, ['reedit'] + base + ['--acknowledge-inference'], plain)
    record['unapproved'] = dict(outcome='refused' if rc else 'saved', reason=reason if rc else None, seconds=seconds)
    approved = ids(first)
    rounds = [dict(approved)]
    for _ in range(2):
        if not any(approved.values()):
            break
        again, problem = listed(approved)
        if again is None:
            rounds.append(dict(refused=problem))
            break
        more = ids(again)
        if all(set(more[k]) <= set(approved[k]) for k, _ in parts):
            break
        approved = {k: sorted(set(approved[k]) | set(more[k])) for k, _ in parts}
        rounds.append(dict(approved))
    record['rounds'] = rounds
    if not any(approved.values()):
        result = record['unapproved']
        if result['outcome'] == 'saved':
            for p in (plain, plain.with_suffix('.proposal.json'), plain.with_suffix('.save.json')):
                p.replace(output.parent / p.name.replace('unapproved', 'out'))
    else:
        rc, reason, seconds = cli(code, ['reedit'] + base + ['--acknowledge-inference'] + sum(([flag, i] for (k, flag) in parts for i in approved[k]), []), output)
        result = dict(outcome='refused' if rc else 'saved', reason=reason if rc else None, seconds=seconds)
    record.update(outcome=result['outcome'], seconds=result['seconds'], approved=approved)
    if result['outcome'] != 'saved':
        record['reason'] = result['reason']
        record['class'] = ce.classify(result['reason'])
        return record
    proposal = json.loads(output.with_suffix('.proposal.json').read_text())
    save = json.loads(output.with_suffix('.save.json').read_text())
    x0, y0, x1, y1 = case['box']
    added = max(0, len(proposal['lines']) - case['lines'])
    region = [x0 - 3, y0 - 3, x1 + 3, y1 + 3 + max(added, grow) * pitch]
    a = audit(start, output, case['page'], region, old, new, sub, occurrence=occurrence)
    record.update(lines=[l['text'] for l in proposal['lines']], grow=added, frame=proposal['evidence']['inferred'].get('frame'),
                  safety=a['safety'], quality=a['quality'], failed=a['failed'],
                  structure_failed=structure_audit(start, output, case['page'], after, [])['failed'],
                  continued_editing=ce.reopen(code, output, case['page'], new, after),
                  save={k: save.get(k) for k in ('follower_translation', 'followers_repainted', 'placed_objects_moved', 'decorations_moved',
                                                 'chain_moved', 'panels_extended')},
                  moves=ce.moves_audit(start, output, case['page'], save), links=ce.links_audit(start, output, case['page'], save),
                  placement=ce.placement_audit(start, output, case['page'], save), raster=None)
    around = [x0 - 40, y0 - 10, x1 + 40, y1 + 40]
    record['panel'] = dict(before=rects(code, start, case['page'], around), after=rects(code, output, case['page'], around),
                           approved=[c for c in ((proposal['evidence'].get('panels') or {}).get('candidates') or []) if c['id'] in approved['panels']])
    try:
        from .approved_audit import audit as approved_audit
        r = approved_audit(start, output, case['page'])
        record['approved_audit'] = dict(verdict=r['verdict'], mode=r['mode'], approval=r['approval'], quality=r['quality'],
                                        approved=[{k: x[k] for k in ('what', 'status', 'reasons')} for x in r['approved']],
                                        protected={k: r['protected'][k] for k in ('status', 'reasons', 'pixels')}, unchecked=r['unchecked'])
    except Exception as exc:  # an audit that cannot run is reported, never a pass
        record['approved_audit'] = dict(verdict='not run', error=f'{type(exc).__name__}: {exc}'[:300])
    return record


def phase_freeze(args):
    from .auto import approve, propose, save
    from .placed_evaluate import block
    code = Path('.').resolve()
    documents, cases = {}, []
    scratch = Path(args.root or 'tmp/panel-freeze')
    scratch.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        path = source_of(case['document'], args)
        if not path.exists():
            cases.append(dict(case, status='input missing'))
            continue
        documents[case['document']] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        box = block(path, case['page'], case['selection'])
        p = propose(str(path), case['page'], case['edit']['old'], case['edit']['old'])
        observed = dict(paragraph=p['paragraphs'][0], lines=len(p['lines']), containers=p['evidence']['conditions']['containers'],
                        frame=[round(v, 2) for v in p['evidence']['inferred']['frame']])
        out = scratch / f"{case['id']}.pdf"
        if out.exists():
            out.unlink()
        try:
            q = propose(str(path), case['page'], case['edit']['old'], case['edit']['new'])
            brief = lambda x: x and dict(needed=x.get('needed'), candidates=[c['id'] for c in x.get('candidates') or []], unavailable=x.get('unavailable'))
            observed['edit'] = dict(lines=[l['text'] for l in q['lines']], chain=brief(q['evidence'].get('chain')), decorations=brief(q['evidence'].get('decorations')))
            save(str(path), approve(str(path), q, acknowledge_inference=True), str(out))
            observed['edit']['outcome'] = 'saved'
        except Exception as exc:
            observed.setdefault('edit', {}).update(outcome='refused', reason=str(exc)[-300:])
        x0, y0, x1, y1 = box
        # The drawings holding the paragraph (its backgrounds) and those right below it.
        para = pymupdf.Rect(box)
        observed['drawings'] = [d for d in rects(code, path, case['page'], [x0 - 40, y0 - 10, x1 + 40, y1 + 40])
                                if pymupdf.Rect(d['rect']).contains(para) or d['rect'][1] >= y1 - 1]
        cases.append(dict(case, box=box, paragraph=observed['paragraph'], lines=observed['lines'], observed=observed))
        print(case['id'], box, observed['edit'].get('outcome'), (observed['edit'].get('reason') or '')[-120:], flush=True)
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    survey = json.loads(Path(args.survey).read_text()) if args.survey else None
    manifest = dict(schema='paragraph-panel-manifest-1', frozen_at=rev,
                    policy=('Frozen before the implementation (issue #112). Targets: lengthening edits refused because the paragraph would '
                            'leave the rectangular panel holding it, with free space below the panel. A panel extension is a layout change: '
                            'it is made only when the proposal lists it and its own id is approved for this edit (acknowledge_inference, '
                            'a chain id or an earlier approval never approves it). Only the panel\'s bottom moves, by the least the text '
                            'needs; its top, sides, colours, line style, transparency and drawing order stay. A missing input, a refusal or '
                            'a fixture is never a real-PDF success; negative cases must stay refused.'),
                    procedure=['freeze: what the B2 at the freeze proposes and why its save is refused; the drawings around the paragraph',
                               'run old (main) and new: propose, save unapproved (must be refused when an approval is needed), approve '
                               'exactly the listed candidates and save; edit back from the saved PDF alone the same way'],
                    survey=survey, documents=documents, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')


def run_case(code, case, source, root):
    ce.block = lambda source, page, selection: case['box']
    ce.locate = rows.make_locate(code)
    ce.chain_step = chain_step
    return ce.run_case(code, case, source, root)


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
        try:
            results[case['id']] = run_case(code, case, source, root)
        except Exception as exc:  # an evaluator error ends this case only, and is reported
            results[case['id']] = [dict(old=case['edit']['old'], new=case['edit']['new'], outcome='not run', reason=f'evaluator error: {type(exc).__name__}: {exc}'[:300])]
        path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def phase_audit(args):
    """The approved-change audit again over the saved steps of a run (each step's source: the case's input, then the step before)."""
    from .approved_audit import audit as approved_audit
    manifest = json.loads(MANIFEST.read_text())
    cases = {c['id']: c for c in manifest['cases']}
    root = Path(args.root)
    results = json.loads((root / 'results.json').read_text())
    for cid, seq in results.items():
        start = source_of(cases[cid]['document'], args).resolve()
        for n, x in enumerate(seq):
            output = root / cid / f'{n + 1}' / 'out.pdf'
            if x.get('outcome') != 'saved' or not output.exists():
                break
            r = approved_audit(start, output, cases[cid]['page'])
            x['approved_audit'] = dict(verdict=r['verdict'], mode=r['mode'], approval=r['approval'], quality=r['quality'],
                                       approved=[{k: y[k] for k in ('what', 'status', 'reasons')} for y in r['approved']],
                                       protected={k: r['protected'][k] for k in ('status', 'reasons', 'pixels')}, unchecked=r['unchecked'])
            print(cid, n + 1, r['verdict'], r['quality']['status'], r['quality']['search_copy_check']['result'], flush=True)
            start = output
    (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def records(results, manifest):
    groups = {c['id']: c['group'] for c in manifest['cases']}
    out = []
    for cid, seq in results.items():
        group = 'real' if groups[cid] == 'target' else 'real-' + groups[cid]
        for n, x in enumerate(seq):
            if x.get('outcome') in ('input missing', 'input differs'):
                continue
            r = smoke_evaluate.records({cid: [x]})[0]
            r.update(evaluation='panel', step=str(n + 1), group=group)
            if x.get('panels') is not None:
                r['checks']['panel_candidates'] = len((x.get('panels') or {}).get('candidates') or [])
            if x.get('unapproved'):
                r['checks']['unapproved_refused'] = x['unapproved']['outcome'] == 'refused'
            out.append(r)
    return out


def phase_collect(args):
    manifest = json.loads(MANIFEST.read_text())
    runs = dict(x.split('=', 1) for x in args.roots)
    compact = lambda x: {k: v for k, v in x.items() if k not in ('frame', 'seconds', 'occurrence', 'rounds')}
    out = dict(schema='paragraph-panel-results-1', manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), runs={})
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
    p.add_argument('--phase', choices=('freeze', 'run', 'audit', 'collect'), required=True)
    p.add_argument('--label')
    p.add_argument('--code', default='.')
    p.add_argument('--root')
    p.add_argument('--roots', nargs='*')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--survey', help='the panel survey (JSON) kept with the manifest')
    p.add_argument('--only', nargs='*')
    args = p.parse_args()
    dict(freeze=phase_freeze, run=phase_run, audit=phase_audit, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
