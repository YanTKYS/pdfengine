"""Round trips of B2 edits: edit, save, reopen the saved PDF alone, undo (issue #93).

  .venv/bin/python -m prototypes.paragraph_frame.roundtrip_evaluate --phase freeze --fusei <477.pdf> --pdfjs <dir>
  .venv/bin/python -m prototypes.paragraph_frame.roundtrip_evaluate --phase run --label old|new --code <tree> --root tmp/<dir> \
      --fusei <477.pdf> --pdfjs <dir>
  .venv/bin/python -m prototypes.paragraph_frame.roundtrip_evaluate --phase collect --roots old=tmp/<a> new=tmp/<b>

``freeze`` (run before the implementation) writes the manifest from the
frozen paragraphs of the layout-fidelity evaluation (PR #92) and the PR #88
continuity sequences that move following paragraphs:

* cycle: the edit E, its undo U, E again, U again, each step saved and the
  next one proposed from that saved PDF alone;
* path: edit A, then edit B elsewhere in the paragraph, then undo A; compared
  with B alone applied to the source.

``run`` saves every step with the B2 of ``--code`` (each in its own process)
and records the existing audits of each step against the PDF it was made
from (indesign_evaluate.audit, tagged_evaluate.structure_audit, continued
editing), and, engine-independently (PyMuPDF and Poppler only):

* after each undo, against the source PDF: the paragraph's unchanged glyphs
  at their source origin (0.01 pt), font and size, lines and line breaks
  (fidelity_evaluate.fidelity); every glyph of the page outside the
  paragraph (followers, foreign text) identical; the whole page's glyphs
  identical; the page text of both renderers identical; search hits;
* after the second E, against the first: the same paragraph measures;
* path: the final PDF against B alone.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import time
import pymupdf

from .fidelity_evaluate import edit, fidelity, locate, reopen, sha, source_of

MANIFEST = Path('evaluations/paragraph_frame_roundtrip/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
FIDELITY = Path('evaluations/paragraph_frame_fidelity/manifest.json')
CONTINUITY = Path('evaluations/paragraph_frame_continuity/manifest.json')
KINDS = ('same', 'shorten', 'lengthen', 'lengthen-wrap')


def page_glyphs(path, page):
    """(char, origin rounded to 0.01 pt, font, size) of every drawn glyph of the page, and its box."""
    with pymupdf.open(path) as d:
        out = []
        for b in d[page - 1].get_text('rawdict')['blocks']:
            for line in b.get('lines', []):
                for s in line['spans']:
                    for ch in s['chars']:
                        if ch['c'].strip():
                            out.append(((ch['c'], round(ch['origin'][0], 2), round(ch['origin'][1], 2), s['font'], round(s['size'], 2)), ch['bbox']))
        return out


def inside(bbox, region):
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def page_text(path, page, folder):
    from .audit import extract
    folder.mkdir(parents=True, exist_ok=True)
    return extract(str(path), page, folder)


def compare(reference, output, page, box, paragraph, frame, grow, folder, search=None):
    """Engine-independent comparison of ``output`` with ``reference`` (see module doc)."""
    region = [box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3 + max(0., grow)]
    if frame:
        region = [min(region[0], frame[0] - 1), region[1], max(region[2], frame[2] + 1), region[3]]
    f = fidelity(str(reference), str(output), page, box, '', '', paragraph, frame, grow)
    a, b = page_glyphs(reference, page), page_glyphs(output, page)
    outside_a = Counter(k for k, bb in a if not inside(bb, region))
    outside_b = Counter(k for k, bb in b if not inside(bb, region))
    all_a, all_b = Counter(k for k, _ in a), Counter(k for k, _ in b)
    ta, tb = page_text(reference, page, folder / 'text-reference'), page_text(output, page, folder / 'text-output')
    bare = lambda s: ''.join(s.split())
    out = dict(paragraph=dict((k, f[k]) for k in ('unchanged', 'kept', 'moved', 'restyled', 'max_delta', 'source_lines', 'output_lines',
                                                  'source_breaks_outside_edit', 'breaks_kept')),
               outside_changed=sum((outside_a - outside_b).values()) + sum((outside_b - outside_a).values()),
               page_changed=sum((all_a - all_b).values()) + sum((all_b - all_a).values()),
               text_identical={r: ta[r] == tb[r] for r in ('mupdf', 'poppler')},
               text_identical_ignoring_whitespace={r: bare(ta[r]) == bare(tb[r]) for r in ('mupdf', 'poppler')})
    if search:
        with pymupdf.open(reference) as d1, pymupdf.open(output) as d2:
            out['search_hits'] = dict(reference=len(d1[page - 1].search_for(search)), output=len(d2[page - 1].search_for(search)))
    return out


def phase_freeze(args):
    fid = json.loads(FIDELITY.read_text())
    cases = []
    for c in fid['cases']:
        firsts = [e for e in c['edits'] if e['kind'] in KINDS]
        cycles = [dict(name=e['kind'], old=e['old'], new=e['new']) for e in firsts]
        # Path: the first two edits (in KINDS order) at disjoint places, the
        # second still selectable once and unchanged after the first.
        path = None
        for i, a in enumerate(firsts):
            for b in firsts[i + 1:]:
                pa, pb = c['paragraph'].find(a['old']), c['paragraph'].find(b['old'])
                disjoint = pa + len(a['old']) <= pb or pb + len(b['old']) <= pa
                after = c['paragraph'].replace(a['old'], a['new'], 1)
                if disjoint and after.count(b['old']) == 1 and after.replace(b['old'], b['new'], 1).count(a['new']) == 1:
                    path = dict(a=dict(name=a['kind'], old=a['old'], new=a['new']), b=dict(name=b['kind'], old=b['old'], new=b['new']))
                    break
            if path:
                break
        cases.append(dict(id=c['id'], document=c['document'], page=c['page'], purpose=c['purpose'], paragraph=c['paragraph'],
                          box=c['box'], lines=len(c['lines']), cycles=cycles, path=path, origin='layout-fidelity manifest (PR #92)'))
    cont = json.loads(CONTINUITY.read_text())
    from .auto import propose
    for q in cont['sequences'].values() if isinstance(cont['sequences'], dict) else cont['sequences']:
        doc = cont['documents'][q['document']]
        shorten, restore = q['steps'][0], q['steps'][1]
        paragraph = restore['expected_target']
        p = propose(doc['path'], doc['page'], shorten['old'], shorten['old'])
        lines = p['evidence']['observed']['lines'][:p['source_lines']]
        box = [round(min(l['bbox'][0] for l in lines), 2), round(min(l['bbox'][1] for l in lines), 2),
               round(max(l['bbox'][2] for l in lines), 2), round(max(l['bbox'][3] for l in lines), 2)]
        if p['paragraphs'][0] != paragraph:
            raise SystemExit(f"{q['id']}: paragraph differs from the continuity manifest")
        cases.append(dict(id=q['id'], document=doc['id'], page=doc['page'], purpose=q['purpose'], paragraph=paragraph, box=box,
                          lines=len(lines), cycles=[dict(name='shorten-to-one-line', old=shorten['old'], new=shorten['new'])], path=None,
                          followers=len(p['paragraphs']) - 1, origin='continuity manifest (PR #88) ' + q['id']))
    documents = {}
    for c in cases:
        documents.setdefault(c['document'], dict(sha256=sha(source_of(c['document'], args))))
    import subprocess
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(schema='paragraph-frame-roundtrip-manifest-1', baseline_commit='870709f (main after PR #92)', frozen_at=commit,
                    policy=('Frozen before the implementation. Old B2 is main 870709f; new B2 is this branch. Each step is proposed from '
                            'the PDF the previous step saved, alone. A refused step ends its sequence; refusals never count as success.'),
                    measures=('per step: saved, safety and quality (indesign_evaluate.audit against the previous PDF, unchanged), structure '
                              '(tagged_evaluate.structure_audit), continued editing; after each undo against the source: paragraph glyphs at '
                              'their source origin (0.01 pt), font and size, lines and breaks, glyphs outside the paragraph identical, page '
                              'glyphs identical, page text of MuPDF and Poppler identical, search hits; second edit against the first; path '
                              'result against the single edit'),
                    documents=documents, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(len(cases), 'cases;', sum(len(c['cycles']) for c in cases), 'cycles;', sum(1 for c in cases if c['path']), 'paths')


def step(code, start, case, old, new, expected_before, expected_after, output, folder, prior_grow, pitch):
    """One saved step and its audits against the PDF it was made from."""
    from .indesign_evaluate import audit
    from .tagged_evaluate import structure_audit
    occurrence = locate(start, case['page'], old, case['box'], code)
    result = edit(code, start, case['page'], old, new, output, occurrence)
    record = dict(old=old, new=new, outcome=result['outcome'], seconds=result['seconds'], occurrence=occurrence)
    if result['outcome'] != 'saved':
        record['reason'] = result['reason']
        return record
    proposal, save = result['proposal'], result['save']
    x0, y0, x1, y1 = case['box']
    grow = max(0, len(proposal['lines']) - case['lines'])
    region = [x0 - 3, y0 - 3, x1 + 3, y1 + 3 + max(grow, prior_grow) * pitch]
    a = audit(start, output, case['page'], region, old, new, folder, occurrence=occurrence)
    record.update(lines=[l['text'] for l in proposal['lines']], layout=proposal.get('layout'), grow=grow,
                  frame=proposal['evidence']['inferred'].get('frame'), safety=a['safety'], quality=a['quality'], failed=a['failed'],
                  structure_failed=structure_audit(start, output, case['page'], expected_after, [])['failed'],
                  continued_editing=reopen(code, output, case['page'], new, expected_after),
                  conditions=proposal['evidence']['conditions']['status'], save={k: save.get(k) for k in ('follower_translation', 'followers_repainted',
                                                                                                             'kept_glyphs', 'layout')})
    return record


def phase_run(args):
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path(args.code).resolve()
    only = set(args.only or [])
    results = json.loads((root / 'results.json').read_text()) if (root / 'results.json').exists() and only else {}
    for case in manifest['cases']:
        if only and case['id'] not in only:
            continue
        source = source_of(case['document'], args).resolve()
        rows = {}
        pitch = (case['box'][3] - case['box'][1]) / max(1, case['lines'])
        for cyc in case['cycles']:
            seq, prev, text, grow = [], source, case['paragraph'], 0
            forward = text.replace(cyc['old'], cyc['new'], 1)
            plan = [(cyc['old'], cyc['new'], text, forward), (cyc['new'], cyc['old'], forward, text)] * 2
            outputs = []
            for n, (old, new, before, after) in enumerate(plan):
                folder = root / case['id'] / cyc['name'] / f'{n + 1}'
                folder.mkdir(parents=True, exist_ok=True)
                output = folder / 'out.pdf'
                for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
                    if p.exists():
                        p.unlink()
                r = step(code, prev, case, old, new, before, after, output, folder, grow, pitch)
                if r['outcome'] != 'saved':
                    seq.append(r)
                    print(case['id'], cyc['name'], n + 1, r['outcome'], r.get('reason', '')[-90:], flush=True)
                    break
                grow = max(grow, r['grow'])
                if n % 2 == 1:
                    r['round_trip'] = compare(source, output, case['page'], case['box'], case['paragraph'], r['frame'], grow * pitch,
                                              folder / 'vs-source', search=cyc['old'])
                if n == 2:
                    r['repeat'] = compare(outputs[0], output, case['page'], case['box'], forward, r['frame'], grow * pitch,
                                          folder / 'vs-first')
                seq.append(r)
                outputs.append(output)
                prev = output
                rt = r.get('round_trip') or r.get('repeat') or {}
                print(case['id'], cyc['name'], n + 1, 'saved', r['failed'], rt.get('paragraph', {}).get('kept'), rt.get('paragraph', {}).get('unchanged'),
                      rt.get('page_changed'), flush=True)
            rows[cyc['name']] = seq
        if case.get('path'):
            a, b = case['path']['a'], case['path']['b']
            text = case['paragraph']
            t1 = text.replace(a['old'], a['new'], 1)
            t2 = t1.replace(b['old'], b['new'], 1)
            t3 = t2.replace(a['new'], a['old'], 1)
            plan = [(a['old'], a['new'], text, t1), (b['old'], b['new'], t1, t2), (a['new'], a['old'], t2, t3)]
            seq, prev, grow = [], source, 0
            for n, (old, new, before, after) in enumerate(plan):
                folder = root / case['id'] / 'path' / f'{n + 1}'
                folder.mkdir(parents=True, exist_ok=True)
                output = folder / 'out.pdf'
                for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
                    if p.exists():
                        p.unlink()
                r = step(code, prev, case, old, new, before, after, output, folder, grow, pitch)
                seq.append(r)
                if r['outcome'] != 'saved':
                    print(case['id'], 'path', n + 1, r['outcome'], r.get('reason', '')[-90:], flush=True)
                    break
                grow = max(grow, r['grow'])
                prev = output
            if all(r['outcome'] == 'saved' for r in seq) and len(seq) == 3:
                folder = root / case['id'] / 'path' / 'single'
                folder.mkdir(parents=True, exist_ok=True)
                single = folder / 'out.pdf'
                for p in (single, single.with_suffix('.proposal.json'), single.with_suffix('.save.json')):
                    if p.exists():
                        p.unlink()
                ref = step(code, source, case, b['old'], b['new'], text, text.replace(b['old'], b['new'], 1), single, folder, 0, pitch)
                result = dict(steps=seq, single=ref)
                if ref['outcome'] == 'saved':
                    result['against_single'] = compare(single, prev, case['page'], case['box'], t3, ref['frame'], max(grow, ref['grow']) * pitch,
                                                       folder / 'vs-single')
                print(case['id'], 'path', 'saved', (result.get('against_single') or {}).get('paragraph'), flush=True)
                rows['path'] = result
            else:
                rows['path'] = dict(steps=seq)
        results[case['id']] = rows
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def summary(results):
    """Totals of one label."""
    s = Counter()
    for case, rows in results.items():
        for name, seq in rows.items():
            if name == 'path':
                s['paths'] += 1
                if 'against_single' in seq:
                    p = seq['against_single']
                    s['paths_completed'] += 1
                    s['path_unchanged'] += p['paragraph']['unchanged']
                    s['path_kept'] += p['paragraph']['kept']
                    s['path_page_identical'] += p['page_changed'] == 0
                continue
            s['cycles'] += 1
            for n, r in enumerate(seq):
                s['steps'] += 1
                if r['outcome'] != 'saved':
                    s['refused' if r['outcome'] == 'refused' else 'not_saved'] += 1
                    continue
                s['saved'] += 1
                s['safety'] += bool(r['safety'])
                s['quality'] += bool(r['quality'])
                s['structure'] += not r['structure_failed']
                s['continued_editing'] += bool((r.get('continued_editing') or {}).get('ok'))
                if 'round_trip' in r:
                    t = r['round_trip']
                    s['undos'] += 1
                    s['undo_unchanged'] += t['paragraph']['unchanged']
                    s['undo_kept'] += t['paragraph']['kept']
                    s['undo_breaks'] += t['paragraph']['source_breaks_outside_edit']
                    s['undo_breaks_kept'] += t['paragraph']['breaks_kept']
                    s['undo_same_lines'] += t['paragraph']['source_lines'] == t['paragraph']['output_lines']
                    s['undo_restyled'] += t['paragraph']['restyled']
                    s['undo_outside_identical'] += t['outside_changed'] == 0
                    s['undo_page_identical'] += t['page_changed'] == 0
                    s['undo_text_identical_both'] += all(t['text_identical'].values())
                    s['undo_text_identical_ignoring_whitespace_both'] += all(t['text_identical_ignoring_whitespace'].values())
                    s['undo_search_same'] += t['search_hits']['reference'] == t['search_hits']['output']
                    if n == 3:
                        s['second_undos'] += 1
                        s['second_undo_page_identical'] += t['page_changed'] == 0
                if 'repeat' in r:
                    t = r['repeat']
                    s['repeats'] += 1
                    s['repeat_unchanged'] += t['paragraph']['unchanged']
                    s['repeat_kept'] += t['paragraph']['kept']
                    s['repeat_page_identical'] += t['page_changed'] == 0
    return dict(s)


def phase_collect(args):
    labels = dict(x.split('=', 1) for x in args.roots)
    results = {label: json.loads((Path(root) / 'results.json').read_text()) for label, root in labels.items()}
    out = dict(schema='paragraph-frame-roundtrip-results-1', manifest_sha256=sha(MANIFEST),
               tests=json.loads(Path(args.tests_json).read_text()) if args.tests_json else None,
               regression=json.loads(Path(args.regression_json).read_text()) if args.regression_json else None,
               summary={label: summary(r) for label, r in results.items()}, results=results)
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    for label, s in out['summary'].items():
        print(label, json.dumps(s, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('freeze', 'run', 'collect'), required=True)
    p.add_argument('--label')
    p.add_argument('--code', default='.')
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--only', nargs='*')
    p.add_argument('--roots', nargs='*')
    p.add_argument('--tests-json')
    p.add_argument('--regression-json')
    args = p.parse_args()
    started = time.perf_counter()
    {'freeze': phase_freeze, 'run': phase_run, 'collect': phase_collect}[args.phase](args)
    print('seconds', round(time.perf_counter() - started, 1))


if __name__ == '__main__':
    main()
