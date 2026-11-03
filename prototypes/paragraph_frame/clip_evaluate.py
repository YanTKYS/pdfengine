"""Word text boxes: the text clip extended down on explicit approval, with its panel (issue #118).

  .venv/bin/python -m prototypes.paragraph_frame.clip_evaluate --phase freeze
  .venv/bin/python -m prototypes.paragraph_frame.clip_evaluate --phase run --root <dir>
  .venv/bin/python -m prototypes.paragraph_frame.clip_evaluate --phase tamper --root <dir>
  .venv/bin/python -m prototypes.paragraph_frame.clip_evaluate --phase collect --root <dir>

``freeze`` (before the implementation) records each case's input (hash),
page, selection and edit, the text box as the page draws it (the fill and
outline of its panel, every rectangular clip holding its text: operator,
rule, q depth, how many text shows each holds), the lines of the edited
paragraph, what lies below the box, and why the save is refused now. The
expected outcome is written by hand in CASES.

``run``, per case, with this tree's B2: the edit, then the edit back from the
saved PDF alone. Each step lists the candidates its proposal offers (clip and
panel extensions, chain elements, line decorations), tries the save without
any approval, with each kind of candidate alone, then with exactly the listed
ones (panel_evaluate.chain_step with the clips); an approval is never carried
to the next step. A saved step keeps the existing audit beside the
approved-change audit. Missing inputs are never a success.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pymupdf

from pdfeditor.content_stream import operators

from . import eval_records as er
from .fidelity_evaluate import source_of

MANIFEST = Path('evaluations/paragraph_clip/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'

CASES = [
    # Microsoft Word: a text box (its fill and outline two `re` operators); every text show inside it sets the
    # box's inner rectangle as its clip (`re W* n`, in its own q/Q, inside an outer q/Q with the same clip).
    dict(id='W1', group='target', document='word_osaka_fire_notice', page=1, selection='直通：03-3595-2257',
         edit=dict(old='直通：03-3595-2257', new='直通：03-3595-2257 直通：03-3595-2257'), panel=[373.75, 428.2, 544.0, 497.65],
         expect='saved on approving the clip extension and the panel extension of its text box (bottom only, by the growth); '
                'refused without either; the edit back from the saved PDF alone saved'),
    dict(id='W2', group='target', document='word_osaka_guideline', page=1, selection='06-4397-3205',
         edit=dict(old='06-4397-3205', new='06-4397-3205 06-6944-9156'), panel=[352.3, 638.0, 521.2, 701.75],
         expect='as W1'),
]


def _box(path, page, panel):
    """The text box as the page draws it: the panel's fill and outline, the rectangular clips inside it."""
    with pymupdf.open(path) as d:
        p = d[page - 1]
        data = b''.join(d.xref_stream(x) for x in p.get_contents())
        height = p.rect.height
        below = pymupdf.Rect(panel[0] - 2, panel[3], panel[2] + 2, panel[3] + 60)
        under = dict(text=[[round(v, 2) for v in b['bbox']] for b in p.get_text('dict')['blocks'] if pymupdf.Rect(b['bbox']).intersects(below)],
                     drawings=[[round(v, 2) for v in x['rect']] for x in p.get_drawings() if x['rect'].intersects(below)],
                     images=[[round(v, 2) for v in x['bbox']] for x in p.get_image_info() if pymupdf.Rect(x['bbox']).intersects(below)],
                     annotations=[[round(v, 2) for v in a.rect] for a in p.annots()],
                     links=[[round(v, 2) for v in link['from']] for link in p.get_links() if pymupdf.Rect(link['from']).intersects(below)])
    ops = list(operators(data))
    page_rect = lambda x, y, w, h: [round(x, 2), round(height - y - h, 2), round(x + w, 2), round(height - y, 2)]
    inside = lambda r: r[0] >= panel[0] - .5 and r[1] >= panel[1] - .5 and r[2] <= panel[2] + .5 and r[3] <= panel[3] + .5
    depth, paints, clips, open_clips = 0, [], [], []
    for k, op in enumerate(ops):
        if op.name == 'q':
            depth += 1
        elif op.name == 'Q':
            depth -= 1
            open_clips = [c for c in open_clips if c['depth'] <= depth]
        elif op.name == 're' and k + 1 < len(ops):
            r = page_rect(*map(float, op.args))
            nxt = ops[k + 1].name
            if not inside(r):
                continue
            if nxt in ('W', 'W*') and k + 2 < len(ops) and ops[k + 2].name == 'n':
                c = dict(rect=r, rule=nxt, depth=depth, offset=op.start, operator=data[op.start:op.end].decode('latin1'), shows=0)
                clips.append(c)
                open_clips.append(c)
            elif nxt in ('f', 'f*', 'F', 'S', 's', 'B', 'B*'):
                paints.append(dict(rect=r, paint=nxt, depth=depth, offset=op.start, operator=data[op.start:op.end].decode('latin1')))
        elif op.name in ('Tj', 'TJ', "'", '"'):
            for c in open_clips:
                c['shows'] += 1
    rects = sorted({tuple(c['rect']) for c in clips})
    return dict(paints=paints, clips=dict(rectangles=[list(r) for r in rects], count=len(clips), rules=sorted({c['rule'] for c in clips}),
                                          depths=sorted({c['depth'] for c in clips}), shows=[c['shows'] for c in clips],
                                          first=clips[0] if clips else None),
                below_60pt=under)


def phase_freeze(args):
    from .auto import approve, propose, save
    from .engine import Refused
    documents, cases = {}, []
    work = Path(args.root or 'tmp/clip-freeze')
    work.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        path = source_of(case['document'], args)
        if not path.exists():
            cases.append(dict(case, status='input missing'))
            continue
        documents[case['document']] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        out = dict(case)
        p = propose(str(path), case['page'], case['edit']['old'], case['edit']['new'])
        out['paragraph'] = p['paragraphs'][0]
        out['source_lines'] = [dict(text=l['text'], baseline=l['baseline'], bbox=l['bbox']) for l in p['evidence']['observed']['lines'][:p['source_lines']]]
        out['proposed_lines'] = [dict(text=l['text'], baseline=round(l['baseline'], 3)) for l in p['lines']]
        out['frame'] = [round(v, 2) for v in p['evidence']['inferred']['frame']]
        out['leading_basis'] = p['evidence']['inferred']['leading_basis']
        out['panels_evidence'] = p['evidence'].get('panels')
        try:
            save(str(path), approve(str(path), p, acknowledge_inference=True), str(work / f"{case['id']}.pdf"))
            out['now'] = dict(outcome='saved')
        except Refused as exc:
            out['now'] = dict(outcome='refused', reason=str(exc)[-300:])
        out['box'] = _box(path, case['page'], case['panel'])
        cases.append(out)
        print(case['id'], out['now'], out['box']['clips']['count'], out['box']['clips']['rectangles'], flush=True)
    import subprocess
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(schema='paragraph-clip-manifest-1', frozen_at=rev,
                    policy=('Frozen before the implementation (issue #118). A case saved without approving its clip extension, or with '
                            'only one of the clip and panel extensions approved, is a failure; a refusal, a missing input or a fixture is '
                            'never counted as a real-PDF success. Practical success follows eval_records.practical (issue #116).'),
                    documents=documents, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str) + '\n')


PARTS = (('chain', '--chain'), ('decorations', '--decoration'), ('panels', '--panel'), ('clips', '--clip'))


def chain_step(code, start, case, old, new, after, output, sub, grow, pitch):
    """panel_evaluate.chain_step approving the clip extensions too; besides, the save with each kind of the listed
    clip and panel candidates approved alone (each must be refused when both are listed)."""
    from . import panel_evaluate
    from .decoration_evaluate import cli
    r = panel_evaluate.chain_step(code, start, case, old, new, after, output, sub, grow, pitch, parts=PARTS)
    ids = {k: [c['id'] for c in ((r.get(k) or {}).get('candidates') or [])] for k in ('clips', 'panels')}
    occurrence = r.get('occurrence', 0)
    base = ['--source', str(Path(start).resolve()), '--page', str(case['page']), '--old', old, '--new', new, '--occurrence', str(occurrence)]
    partial = {}
    for name, flags in (('clip only', [x for i in ids['clips'] for x in ('--clip', i)]), ('panel only', [x for i in ids['panels'] for x in ('--panel', i)])):
        if not flags or not (ids['clips'] and ids['panels']):
            continue
        out = sub / ('partial-' + name.replace(' ', '-') + '.pdf')
        for p in (out, out.with_suffix('.proposal.json'), out.with_suffix('.save.json')):
            if p.exists():
                p.unlink()
        rc, reason, _ = cli(code, ['reedit'] + base + ['--acknowledge-inference'] + flags, out)
        partial[name] = dict(outcome='refused' if rc else 'saved', reason=reason if rc else None)
    r['partial'] = partial
    return r


def run_case(code, case, source, root):
    from . import chain_evaluate as ce
    from . import panel_evaluate
    from . import rows_evaluate as rows
    ce.block = lambda source, page, selection: case['box']
    ce.locate = rows.make_locate(code)
    ce.chain_step = chain_step
    return ce.run_case(code, case, source, root)


def phase_run(args):
    manifest = json.loads(MANIFEST.read_text())
    panel_cases = {c['id']: c for c in json.loads(Path('evaluations/paragraph_panel/manifest.json').read_text())['cases']}
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path('.').resolve()
    results = {}
    for case in manifest['cases']:
        if case.get('status') == 'input missing':
            results[case['id']] = [dict(outcome='input missing')]
            continue
        source = source_of(case['document'], args).resolve()
        if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['documents'][case['document']]['sha256']:
            results[case['id']] = [dict(outcome='input differs')]
            continue
        # (The selection's box and line count as the panel evaluation froze them for the same edit.)
        case = dict(case, box=panel_cases[case['id']]['box'], lines=panel_cases[case['id']]['lines'])
        try:
            results[case['id']] = run_case(code, case, source, root)
        except Exception as exc:  # an evaluator error ends this case only, and is reported
            results[case['id']] = [dict(old=case['edit']['old'], new=case['edit']['new'], outcome='not run',
                                        reason=f'evaluator error: {type(exc).__name__}: {exc}'[:300])]
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def _tampered(path, out, n, change, rect):
    """``path`` with the ``n``-th (0-based) instance of its extended clip (page rectangle ``rect``) changed: removed,
    resized (1 pt taller), or set by the other rule; or None when it has no such instance."""
    import re
    with pymupdf.open(path) as d:
        xref = d[0].get_contents()[0]
        data = d.xref_stream(xref)
        height = d[0].rect.height
        found = list(re.finditer(rb'(\S+) (\S+) (\S+) (\S+) re(\s+)(W\*?)(\s+)n', data))
        def page(m):
            x, y, w, h = map(float, m.group(1, 2, 3, 4))
            return [x, height - y - h, x + w, height - y]
        target = [m for m in found if max(abs(p - q) for p, q in zip(page(m), rect)) <= .05]
        if len(target) <= n:
            return None
        m = target[n]
        if change == 'removed':
            new = b''
        elif change == 'resized':
            new = b' '.join(m.group(1, 2, 3)) + b' ' + str(float(m.group(4)) + 1).encode() + b' re' + m.group(5) + m.group(6) + m.group(7) + b'n'
        else:
            new = m.group(0).replace(m.group(6), b'W' if m.group(6) == b'W*' else b'W*')
        d.update_stream(xref, data[:m.start()] + new + data[m.end():])
        d.save(out)
    return out


def _instances(path, rect):
    """The instances of the clip ``rect`` (page) set in ``path`` (as rule strings, in stream order)."""
    from .approved_audit import _clips
    with pymupdf.open(path) as d:
        return [eo for r, eo in _clips(d[0]) if max(abs(p - q) for p, q in zip(r, rect)) <= .05]


def phase_tamper(args):
    """The saved first steps with one instance of their extended clip changed (PR #119 review): the approved-change
    audit must fail and the saved PDF alone must not take its record as approved (stale)."""
    import shutil
    from .approved_audit import audit
    from .auto import propose
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    results = json.loads((root / 'results.json').read_text())
    out = {}
    for case in manifest['cases']:
        step = (results.get(case['id']) or [{}])[0]
        if step.get('outcome') != 'saved':
            continue
        source = source_of(case['document'], args).resolve()
        saved = root / case['id'] / '1' / 'out.pdf'
        extended = json.loads(saved.with_suffix('.save.json').read_text())['clips_extended'][0]['new']
        rows = {}
        for change in ('kept', 'removed', 'resized', 'rule'):
            path = saved if change == 'kept' else _tampered(saved, root / case['id'] / '1' / f'tampered-{change}.pdf', 1, change, extended)
            if path is None:
                rows[change] = dict(outcome='not run', reason='no second instance')
                continue
            for suffix in ('.proposal.json', '.save.json'):
                if path != saved:
                    shutil.copy(saved.with_suffix(suffix), path.with_suffix(suffix))
            a = audit(source, path, case['page'])
            clip = next((x for x in a['approved'] if x['what'] == 'clip'), {})
            conditions = propose(str(path), case['page'], case['edit']['new'], case['edit']['old'])['evidence']['conditions']
            rows[change] = dict(instances=(len(_instances(path, extended))), audit=a['verdict'], clip=clip.get('status'),
                                reasons=clip.get('reasons', [])[:2], reopen=conditions['status'][:200])
            print(case['id'], change, rows[change], flush=True)
        out[case['id']] = rows
    (root / 'tamper.json').write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')


def records(results, manifest):
    from . import smoke_evaluate
    groups = {c['id']: c['group'] for c in manifest['cases']}
    out = []
    for cid, seq in results.items():
        for n, x in enumerate(seq):
            if x.get('outcome') in ('input missing', 'input differs'):
                continue
            r = smoke_evaluate.records({cid: [x]})[0]
            r.update(evaluation='clip', step=str(n + 1), group='real' if groups[cid] == 'target' else 'real-' + groups[cid])
            for k in ('clips', 'panels'):
                if x.get(k) is not None:
                    r['checks'][k[:-1] + '_candidates'] = len((x.get(k) or {}).get('candidates') or [])
            if x.get('unapproved'):
                r['checks']['unapproved_refused'] = x['unapproved']['outcome'] == 'refused'
            for name, v in (x.get('partial') or {}).items():
                r['checks'][name.replace(' ', '_') + '_refused'] = v['outcome'] == 'refused'
            out.append(r)
    return out


def phase_collect(args):
    manifest = json.loads(MANIFEST.read_text())
    results = json.loads((Path(args.root) / 'results.json').read_text())
    recs = records(results, manifest)
    compact = lambda x: {k: er.compact_audit(v) if k == 'approved_audit' else v for k, v in x.items()
                         if k not in ('frame', 'seconds', 'occurrence', 'rounds')}
    tamper = Path(args.root) / 'tamper.json'
    out = dict(schema='paragraph-clip-results-1', manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
               summary=er.summary(recs), records=recs, results={k: [compact(x) for x in seq] for k, seq in results.items()},
               **({'tamper': json.loads(tamper.read_text())} if tamper.exists() else {}))
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    print(json.dumps(out['summary'], ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('freeze', 'run', 'tamper', 'collect'), required=True)
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    args = p.parse_args()
    dict(freeze=phase_freeze, run=phase_run, tamper=phase_tamper, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
