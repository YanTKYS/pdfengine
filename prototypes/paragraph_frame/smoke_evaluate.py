"""The smoke set of real PDFs: natural edits across generators, with the approved-change audit (issue #108).

  .venv/bin/python -m prototypes.paragraph_frame.smoke_evaluate --phase freeze --fusei <477.pdf> --pdfjs <pdf.js dir>
  .venv/bin/python -m prototypes.paragraph_frame.smoke_evaluate --phase run --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir> [--only <id> ...]
  .venv/bin/python -m prototypes.paragraph_frame.smoke_evaluate --phase audit --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir>
  .venv/bin/python -m prototypes.paragraph_frame.smoke_evaluate --phase collect --root <dir>

The cases are representative edits defined by the implementer (issue #108):
the kind of change a user asks for in a published document (a date, a time,
a telephone number, a name, a phrase added, deleted or corrected). They do not
come from a user's real request; such cases are to be added apart, marked so.

``freeze`` records each case's document (hash, generator), paragraph and
place, and the sources of evaluations/sources.json that are not available
here (input missing; never counted). ``run`` takes each case through
chain_evaluate.run_case (the edit, then the edit back from the saved PDF alone;
candidates approved exactly as the proposals list them: that is the evaluator
standing for a user, not a model of the user's approval) and adds the
approved-change audit (approved_audit) to every saved step. Records follow
eval_records: practical success here is the approved-change audit's pass and
the quality checks; the existing audit's results are kept beside it.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pymupdf

from . import chain_evaluate as ce
from . import eval_records as er
from .fidelity_evaluate import source_of
from .placed_evaluate import block

MANIFEST = Path('evaluations/smoke/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
# (id, document, page, old, new, kind, what, tier). normal: the PR smoke; related: added in the related tier.
CASES = [
    # Microsoft Word (Osaka prefecture notices).
    ('W1', 'word_osaka_fire_notice', 1, '直通：03-3595-2257', '直通：03-3595-2258', 'same length', 'telephone number', 'normal'),
    ('W2', 'word_osaka_fire_notice', 1, '感染症対策を円滑かつ適切に実施いただくよう', '感染症対策を適切に実施いただくよう', 'shorter', 'phrase deleted', 'normal'),
    ('W3', 'word_osaka_guideline', 1, '大阪府健康医療部保健医療室長', '大阪府健康医療部保健医療室長代理', 'longer', 'title added', 'normal'),
    ('W4', 'word_osaka_guideline', 1, '本府保健医療行政の推進に御協力いただき', '本府保健医療行政の推進に格別の御協力をいただき', 'longer', 'phrase added', 'normal'),
    ('W5', 'word_osaka_guideline', 1, '感染症対策課 防疫グループ', '感染症対策課 予防グループ', 'same length', 'department name', 'normal'),
    # LibreOffice Writer.
    ('L1', 'lo_newfeatures_ja', 1, '月下旬にリリース', '月上旬にリリース', 'same length', 'date', 'normal'),
    ('L2', 'lo_newfeatures_ja', 1, 'Calcをより使いやすくするための工夫', 'Calcを使いやすくする工夫', 'shorter', 'phrase deleted', 'normal'),
    ('L3', 'lo_migration_ja', 2, 'オフィススィートは個人の利用でも', 'オフィススイートは個人の利用でも', 'same length', 'spelling corrected', 'normal'),
    ('L4', 'lo_migration_ja', 1, '六つの豊富な機能のアプリケーション', '六つの豊富で強力な機能のアプリケーション', 'longer', 'phrase added', 'related'),
    # Adobe InDesign (Osaka prefecture newsletter 477).
    ('I1', 'osaka_fusei_477', 7, '令和5年の大阪府の自殺者数は1,383人', '令和6年の大阪府の自殺者数は1,383人', 'same length', 'year', 'normal'),
    ('I2', 'osaka_fusei_477', 7, '悩みを抱えている人は、ひとりで悩まず専門機関にご相談ください。', '', 'shorter', 'sentence deleted (a line decoration moves up)', 'related'),
    ('I3', 'osaka_fusei_477', 2, '領収証は必ず受け取り保管してください。', '領収証は必ず受け取り、大切に保管してください。', 'longer', 'phrase added', 'related'),
    ('I4', 'osaka_fusei_477', 5, '提供：ダイキン工業・サントリーHD', '提供：ダイキン工業・サントリーホールディングス', 'longer', 'abbreviation expanded (lines below separated and moved)', 'related'),
    ('I5', 'osaka_fusei_477', 5, '4/13（日）9：00～9：30', '4/13（日）10：00～10：30', 'longer', 'time', 'related'),
    # Antenna House Formatter and pdfTeX (English; other generators).
    ('A1', 'issue17730', 1, 'Linking to an internal destination', 'Linking to an internal destination or an external destination', 'longer',
     'phrase added (the lines below move on approval)', 'related'),
    ('T1', 'tracemonkey', 1, 'statically', 'strongly', 'shorter', 'word replaced', 'related'),
]
# Generators the issue asks for that no available document has.
WANTED = ['Microsoft Word', 'LibreOffice', 'Adobe InDesign', 'Ichitaro (Justsystems)', 'Adobe Illustrator']


def phase_freeze(args):
    from .auto import propose
    sources = json.loads(Path('evaluations/sources.json').read_text())
    cases, documents = [], {}
    for cid, doc, page, old, new, kind, what, tier in CASES:
        path = source_of(doc, args)
        if not path.exists():
            cases.append(dict(id=cid, document=doc, status='input missing'))
            continue
        with pymupdf.open(path) as d:
            meta = d.metadata
        documents[doc] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), producer=meta.get('producer'), creator=meta.get('creator'),
                              source_url=next((s['source_url'] for s in sources if s['id'] == doc), None))
        try:
            p = propose(str(path), page, old, old)
        except Exception as exc:
            # Recorded as a case: the run proposes it again and records the refusal (a result, not a missing input).
            cases.append(dict(id=cid, document=doc, page=page, edit=dict(old=old, new=new), kind=kind, what=what, tier=tier,
                              status='refused at freeze', reason=str(exc)[-200:],
                              origin='representative edit defined by the implementer (issue #108); not a user request'))
            print(cid, 'NOT LOCATED', str(exc)[-120:], flush=True)
            continue
        cases.append(dict(id=cid, document=doc, page=page, paragraph=p['paragraphs'][0], selection=old[:12], lines=len(p['lines']),
                          box=block(path, page, old[:12]), edit=dict(old=old, new=new), kind=kind, what=what, tier=tier,
                          origin='representative edit defined by the implementer (issue #108); not a user request'))
        print(cid, doc, page, len(p['lines']), 'lines:', p['paragraphs'][0][:40], flush=True)
    missing = [dict(id=s['id'], source_url=s['source_url'], reason='not in the corpus here; its host is denied by this environment\'s network policy')
               for s in sources if not Path(s['path']).exists()]
    have = {(d['creator'] or '') + ' ' + (d['producer'] or '') for d in documents.values()}
    manifest = dict(schema='paragraph-frame-smoke-manifest-1',
                    policy=('Representative natural edits across generators, defined by the implementer before running them (issue #108). Every '
                            'case runs the edit and the edit back from the saved PDF alone; candidates are approved as listed. Records: saved, '
                            'practical (approved-change audit pass and quality), re-edit, restore, refused, apart; the existing audit is kept '
                            'beside. A missing input is reported, never counted.'),
                    documents=documents, cases=cases, sources_missing=missing,
                    generators_missing=[g for g in WANTED if not any(g.split(' (')[0].split()[-1] in h for h in have)])
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(len(cases), 'cases;', len(missing), 'sources missing; generators missing:', manifest['generators_missing'])


_chain_step = ce.chain_step


def chain_step(code, start, case, old, new, after, output, sub, grow, pitch):
    """chain_evaluate.chain_step, and the approved-change audit of a saved step."""
    from .approved_audit import audit
    r = _chain_step(code, start, case, old, new, after, output, sub, grow, pitch)
    if r['outcome'] == 'saved':
        try:
            a = audit(start, output, case['page'])
            r['approved_audit'] = dict(verdict=a['verdict'], mode=a['mode'], approval=a['approval'], quality=a['quality'],
                                       approved=[{k: x[k] for k in ('what', 'status', 'reasons')} for x in a['approved']],
                                       protected={k: a['protected'][k] for k in ('status', 'reasons', 'pixels')}, unchecked=a['unchecked'])
        except Exception as exc:  # an audit that cannot run is reported, never a pass
            r['approved_audit'] = dict(verdict='not run', error=f'{type(exc).__name__}: {exc}'[:300])
    return r


def phase_run(args):
    ce.chain_step = chain_step
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    only = set(args.only or [])
    tier = args.tier
    results = {}
    for case in manifest['cases']:
        if only and case['id'] not in only or (not only and tier == 'normal' and case.get('tier') != 'normal'):
            continue
        if case.get('status') == 'input missing':
            results[case['id']] = [dict(outcome='input missing')]
            continue
        source = source_of(case['document'], args)
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['documents'][case['document']]['sha256']:
            results[case['id']] = [dict(outcome='input differs')]
            continue
        if case.get('status') == 'refused at freeze':
            from .auto import propose
            try:
                propose(str(source), case['page'], case['edit']['old'], case['edit']['new'])
                results[case['id']] = [dict(old=case['edit']['old'], new=case['edit']['new'], outcome='not run',
                                            reason='proposed now, refused at freeze: freeze it again to run it')]
            except Exception as exc:
                results[case['id']] = [dict(old=case['edit']['old'], new=case['edit']['new'], outcome='refused', reason=str(exc)[-300:])]
        else:
            results[case['id']] = ce.run_case(Path('.').resolve(), case, source.resolve(), root)
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')
    (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def phase_audit(args):
    """The approved-change audit again over the saved steps of a run (results updated in place)."""
    from .approved_audit import audit
    root = Path(args.root)
    results = json.loads((root / 'results.json').read_text())
    manifest = json.loads(MANIFEST.read_text())
    cases = {c['id']: c for c in manifest['cases']}
    for cid, seq in results.items():
        start = source_of(cases[cid]['document'], args)
        for n, r in enumerate(seq):
            output = root / cid / f'{n + 1}' / 'out.pdf'
            if r.get('outcome') != 'saved' or not output.exists():
                break
            a = audit(start, output, cases[cid]['page'])
            r['approved_audit'] = dict(verdict=a['verdict'], mode=a['mode'], approval=a['approval'], quality=a['quality'],
                                       approved=[{k: x[k] for k in ('what', 'status', 'reasons')} for x in a['approved']],
                                       protected={k: a['protected'][k] for k in ('status', 'reasons', 'pixels')}, unchecked=a['unchecked'])
            print(cid, n + 1, a['verdict'], a['quality']['status'], flush=True)
            start = output
    (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def records(results):
    """Common records (eval_records) of a smoke run; practical success from the approved-change audit."""
    out = []
    for cid, seq in results.items():
        for n, x in enumerate(seq):
            if x.get('outcome') in ('input missing', 'input differs'):
                out.append(dict(evaluation='smoke', case=cid, step=str(n + 1), group='real', mode='faithful', outcome=x['outcome'],
                                reason_class=None, reason=None, failed=None, checks={},
                                tiers=dict(saved=None, practical=None, reedit=None, restore=None, refused=None)))
                continue
            r = er.step_record('smoke', cid, str(n + 1), x, 'real')
            a = x.get('approved_audit') or {}
            if a:
                r['checks']['approved_audit'] = {'pass': True, 'fail': False}.get(a.get('verdict'))
                r['mode'] = a.get('mode', r['mode'])
                r['tiers']['practical'] = (a.get('verdict') == 'pass' and (a.get('quality') or {}).get('status') == 'pass') \
                    if a.get('verdict') in ('pass', 'fail') else None
            out.append(r)
    return out


def phase_collect(args):
    results = json.loads((Path(args.root) / 'results.json').read_text())
    recs = records(results)
    compact = lambda x: {k: v for k, v in x.items() if k not in ('lines', 'frame', 'seconds', 'occurrence', 'chain', 'decorations', 'rounds')}
    out = dict(schema='paragraph-frame-smoke-results-1', manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
               summary=er.summary(recs), records=recs, results={k: [compact(x) for x in seq] for k, seq in results.items()})
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    print(json.dumps(out['summary'], ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('freeze', 'run', 'audit', 'collect'), required=True)
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--only', nargs='*')
    p.add_argument('--tier', choices=('normal', 'related'), default='related', help='normal: the cases marked normal only')
    args = p.parse_args()
    dict(freeze=phase_freeze, run=phase_run, audit=phase_audit, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
