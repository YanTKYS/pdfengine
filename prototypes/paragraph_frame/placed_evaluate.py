"""Inline placed objects of following paragraphs that move with them (issue #97).

  .venv/bin/python -m prototypes.paragraph_frame.placed_evaluate --phase freeze --survey tmp/fx-survey --fusei <477.pdf>
  .venv/bin/python -m prototypes.paragraph_frame.placed_evaluate --phase run --label old|new --code <tree> --root tmp/<dir> --fusei <477.pdf>
  .venv/bin/python -m prototypes.paragraph_frame.placed_evaluate --phase collect --roots old=tmp/<a> new=tmp/<b>

``freeze`` (before the implementation) takes from the survey of
follower_survey.py every paragraph whose following paragraphs hold an inline
placed object, with two edits of its longest text run free of its own placed
objects: ``lengthen`` (the run followed by about one more line of its own
characters) and ``shorten`` (about one line of the run removed). The
generated fixtures (the placed objects of tests/test_paragraph_frame_placed_follower.py)
are listed by name and witness.

``run`` saves each edit with the B2 of ``--code`` and, when saved, undoes it
from the saved PDF alone. Each step records the outcome (a refusal with its
reason and class), the existing audits (roundtrip_evaluate.step:
safety/quality, structure, continued editing) and the placed audit:

* ``approved_moves``: every drawing, image and glyph inside an object the
  writer reports moved (save ``placed_objects_moved``) is found translated by
  exactly the follower translation, and nothing of it is left at its old place;
* ``protected``: every other drawing, image, link and annotation of the page is
  unchanged.

The region the existing audits allow to change is the edited paragraph and
its following paragraphs as detected in the source (an approved edit moves
them), with the added lines below.

The undo is compared with the source (roundtrip_evaluate.compare). A refused
step ends its case; refusals never count as success.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

import pymupdf

from .fidelity_evaluate import sha, source_of
from .roundtrip_evaluate import compare, step

MANIFEST = Path('evaluations/paragraph_frame_placed/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
GROW = ('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で')
# Generated fixtures: name -> (placed_follower keyword arguments, edits, expected).
FIXTURES = {
    'F1': (dict(), dict(lengthen=GROW, shorten=('申請書は窓口で配布し', '申請書は配布し')), 'moves'),
    'F2': ('shared-form', dict(lengthen=GROW), 'moves; the shared Form XObject is unchanged'),
    'F3': (dict(after=b'q .5 g 150 181 60 14 re f Q\n'), dict(lengthen=GROW), 'refused: paint on its line (a search box)'),
    'F4': (dict(head=b'1 0 0 rg '), dict(lengthen=GROW), 'refused: sets state used after it'),
    'F5': (dict(wrap=b'0 0 m 300 0 l 0 300 l h'), dict(lengthen=GROW), 'refused: nonrectangular clip'),
}


def runs(text, marks):
    """Runs of ``text`` without a character of ``marks``: (start, end)."""
    out, s = [], 0
    for n, ch in enumerate(text + '\0'):
        if ch in marks or ch == '\0':
            if n > s:
                out.append((s, n))
            s = n + 1
    return out


def edits_of(text, marks, lines):
    """The lengthen and shorten edits of a paragraph: its last run of at least
    8 characters free of its own placed objects (else its longest), about one
    line (the paragraph's characters per line, plus one) added or removed."""
    rs = runs(text, marks)
    long = [r for r in rs if r[1] - r[0] >= 8]
    a, b = long[-1] if long else max(rs, key=lambda r: r[1] - r[0])
    run = text[a:b]
    per_line = -(-len(text) // max(1, lines)) + 1
    added = (run * (per_line // len(run) + 1))[:per_line]
    cut = min(per_line, len(run) - 2)
    return dict(lengthen=(run, run + added), shorten=(run, run[:1] + run[1 + cut:]))


def phase_freeze(args):
    from .auto_detect import detect
    source = Path(args.fusei)
    cases = []
    for page in (7, 8):
        survey = json.loads((Path(args.survey) / f'osaka_fusei_477-{page}.json').read_text())
        for row in survey:
            if row['outcome'] != 'detected' or not any(f['fixed'] for f in row['followers']):
                continue
            c, ps, spec, at, ev = detect(str(source), page, row['selection'])
            try:
                p = ps[0]
                marks = {c.actual[i]['unicode'] for i in p.fixed}
                box = pymupdf.Rect(c.actual[p.ids[0]]['bbox'])
                for i in p.ids:
                    if c.actual[i]['unicode'].strip():
                        box |= pymupdf.Rect(c.actual[i]['bbox'])
                lines = len(p.baselines)
                followers = [dict(text=f.text[:40], placed_objects=len([s for s in f.scopes if s['kind'] == 'fixed']),
                                  tags=sorted({s.get('tag') for s in f.scopes if s['kind'] == 'fixed'})) for f in ps[1:]]
                cases.append(dict(id=f'P{page}-{len(cases) + 1:02d}', document='osaka_fusei_477', page=page, paragraph=p.text.strip(),
                                  selection=row['selection'], box=[round(v, 2) for v in box], lines=lines, own_placed_objects=len(p.fixed),
                                  followers=followers, edits=edits_of(p.text.strip(), marks, lines)))
            finally:
                c.close()
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(
        schema='paragraph-frame-placed-manifest-1', baseline_commit='85fe006 (main after PR #96)', frozen_at=commit,
        policy=('Frozen before the implementation. Old B2 is main 85fe006; new B2 is this branch. Real cases: every paragraph of the '
                'survey (36 pages of the PR #91-#96 sources) whose following paragraphs hold an inline placed object. A saved edit is '
                'undone from the saved PDF alone. A refusal ends its case and never counts as success; a refusal by an unrelated rule '
                '(capacity, the edited paragraph\'s own objects) is recorded as such.'),
        measures=('per step: saved or refused (reason, class); safety and quality (indesign_evaluate.audit), structure, continued editing; '
                  'placed audit: approved moves translated exactly and nothing left behind, protected drawings/images/links/annotations '
                  'unchanged; undo against the source (roundtrip_evaluate.compare)'),
        survey=dict(pages=len(list(Path(args.survey).glob('*-*.json'))), script='prototypes/paragraph_frame/follower_survey.py'),
        documents=dict(osaka_fusei_477=dict(sha256=sha(source))),
        fixtures={k: dict(construction=f'placed_follower(**{v[0]!r})' if isinstance(v[0], dict) else '_shared_form', edits=v[1], expected=v[2])
                  for k, v in FIXTURES.items()},
        cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(len(cases), 'cases;', len(FIXTURES), 'fixtures')


def fixtures(folder):
    """The generated fixture PDFs (test helpers) and their witnesses."""
    sys.path.insert(0, str(Path('tests').resolve()))
    import test_paragraph_frame_placed_follower as t
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, (kw, _, _) in FIXTURES.items():
        sub = folder / name
        sub.mkdir(exist_ok=True)
        path = t._shared_form(sub) if kw == 'shared-form' else t.source_with(sub, t.placed_follower(**kw))
        out[name] = path
    return out


def fixture_case(name, path, edits):
    with pymupdf.open(path) as d:
        text = d[0].get_text()
    return dict(id=name, document=name, page=1, paragraph='本年度の補助事業について、募集を行います。申請書は窓口で配布します。',
                box=[39.5, 48.5, 216.5, 96.0], lines=3, edits=edits, witness=text.replace('\n', ''))


def placed_audit(source, output, page, save):
    """The approved moves and the protected paint, checked separately."""
    moved = save.get('placed_objects_moved') or []
    delta = save.get('follower_translation') if isinstance(save.get('follower_translation'), (int, float)) else 0.
    boxes = [pymupdf.Rect(u['old']) + (-.5, -.5, .5, .5) for u in moved]
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        pa, pb = a[page - 1], b[page - 1]
        rect = lambda r: tuple(round(v, 2) for v in r)
        shift = lambda r: (r[0], round(r[1] + delta, 2), r[2], round(r[3] + delta, 2))
        own = lambda r: any(x.contains(pymupdf.Rect(r)) for x in boxes)

        def split(items, key):
            mine, other = Counter(), Counter()
            for x in items:
                (mine if own(key(x)) else other)[(rect(key(x)), str(x.get('fill')), str(x.get('color')), x.get('type'))] += 1
            return mine, other
        dm, do = split(pa.get_drawings(), lambda x: x['rect'])
        im, io = split(pa.get_image_info(xrefs=True), lambda x: x['bbox'])
        after_d = Counter((rect(x['rect']), str(x.get('fill')), str(x.get('color')), x.get('type')) for x in pb.get_drawings())
        after_i = Counter((rect(x['bbox']), str(x.get('fill')), str(x.get('color')), x.get('type')) for x in pb.get_image_info(xrefs=True))
        expected_d = do + Counter({(shift(k[0]),) + k[1:]: n for k, n in dm.items()})
        expected_i = io + Counter({(shift(k[0]),) + k[1:]: n for k, n in im.items()})
        glyphs = lambda p: Counter((c['c'], round(c['origin'][0], 2), round(c['origin'][1], 2)) for bl in p.get_text('rawdict')['blocks']
                                   for l in bl.get('lines', []) for s in l['spans'] for c in s['chars'] if own(c['bbox']))
        moved_glyphs = glyphs(pa)
        found = Counter((c['c'], round(c['origin'][0], 2), round(c['origin'][1], 2)) for bl in pb.get_text('rawdict')['blocks']
                        for l in bl.get('lines', []) for s in l['spans'] for c in s['chars'])
        glyphs_ok = all(found[(ch, x, round(y + delta, 2))] >= n for (ch, x, y), n in moved_glyphs.items())
        links = lambda p: sorted(rect(l['from']) for l in p.get_links())
        annots = lambda p: sorted((a.type[1], rect(a.rect)) for a in p.annots())
        approved = dict(objects=len(moved), drawings=sum(dm.values()), images=sum(im.values()), glyphs=sum(moved_glyphs.values()),
                        exact=all(after_d[k] >= n for k, n in Counter({(shift(k[0]),) + k[1:]: n for k, n in dm.items()}).items())
                        and all(after_i[k] >= n for k, n in Counter({(shift(k[0]),) + k[1:]: n for k, n in im.items()}).items()) and glyphs_ok,
                        nothing_left=all(after_d[k] <= do[k] for k in dm) and all(after_i[k] <= io[k] for k in im))
        protected = dict(drawings=after_d == expected_d, images=after_i == expected_i, links=links(pa) == links(pb),
                         annotations=annots(pa) == annots(pb))
    return dict(approved_moves=approved, protected=protected,
                ok=(not moved or (approved['exact'] and approved['nothing_left'])) and all(protected.values()))


def classify(reason):
    r = reason or ''
    for key, label in (('inline placed object of a following paragraph', 'placed object of a follower (new rule)'),
                       ('following paragraph with an inline placed object cannot move', 'placed object of a follower (old rule)'),
                       ('capacity', 'capacity'), ('inline placed object would move', 'own placed object would move'),
                       ('removes an inline placed object', 'own placed object removed')):
        if key in r:
            return label + (': ' + r.split('Refused: ')[-1][:120] if 'new rule' in label else '')
    return 'other: ' + r.split('Refused: ')[-1][:120]


def block(source, page, selection):
    """Page bounds of the edited paragraph and its following paragraphs (they
    move with an approved edit), as detected in the source."""
    from .auto_detect import detect
    c, ps, spec, at, ev = detect(str(source), page, selection)
    try:
        boxes = [pymupdf.Rect(c.actual[i]['bbox']) for p in ps for i in p.ids if c.actual[i]['unicode'].strip()]
        out = boxes[0]
        for r in boxes[1:]:
            out |= r
        return [round(v, 2) for v in out]
    finally:
        c.close()


def run_case(code, case, source, root):
    rows = {}
    pitch = (case['box'][3] - case['box'][1]) / max(1, case['lines'])
    # The audits' allowed region: the edited paragraph and the following
    # paragraphs its edit moves (plus the added lines, roundtrip_evaluate.step).
    case = dict(case, box=block(source, case['page'], case.get('selection') or case['paragraph'][:6]))
    for kind, (old, new) in case['edits'].items():
        text = case['paragraph']
        changed = text.replace(old, new, 1)
        folder = root / case['id'] / kind
        seq, prev, grow = [], source, 0
        for n, (a, b, before, after) in enumerate([(old, new, text, changed), (new, old, changed, text)]):
            sub = folder / f'{n + 1}'
            sub.mkdir(parents=True, exist_ok=True)
            output = sub / 'out.pdf'
            for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
                if p.exists():
                    p.unlink()
            try:
                r = step(code, prev, case, a, b, before, after, output, sub, grow, pitch)
            except SystemExit as exc:  # the selection is not found in the PDF it starts from
                r = dict(old=a, new=b, outcome='not run', reason=str(exc)[:300])
            if r['outcome'] == 'saved':
                saved = json.loads(output.with_suffix('.save.json').read_text())
                r['save']['placed_objects_moved'] = saved.get('placed_objects_moved')
                r['placed'] = placed_audit(prev, output, case['page'], saved)
                if n == 1:
                    r['vs_source'] = compare(source, output, case['page'], case['box'], text, r['frame'], 2 * pitch, sub / 'vs-source', search=old)
            elif r['outcome'] == 'refused':
                r['class'] = classify(r.get('reason'))
            seq.append(r)
            print(case['id'], kind, n + 1, r['outcome'], r.get('class') or r.get('reason') if r['outcome'] != 'saved' else (r['failed'], r['placed']['ok']), flush=True)
            if r['outcome'] != 'saved':
                break
            prev = output
            grow = max(grow, r['grow'])
        rows[kind] = seq
    return rows


def phase_run(args):
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path(args.code).resolve()
    only = set(args.only or [])
    results = json.loads((root / 'results.json').read_text()) if (root / 'results.json').exists() and only else {}
    made = fixtures(root / 'fixtures')
    for name, spec in manifest['fixtures'].items():
        if only and name not in only:
            continue
        case = fixture_case(name, made[name], spec['edits'])
        results[name] = run_case(code, case, made[name].resolve(), root)
        results[name]['witness'] = case['witness']
    for case in manifest['cases']:
        if only and case['id'] not in only:
            continue
        results[case['id']] = run_case(code, case, source_of(case['document'], args).resolve(), root)
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')
    (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def summary(results, ids):
    s = Counter()
    for k in ids:
        for kind, seq in results.get(k, {}).items():
            if kind == 'witness':
                continue
            for n, r in enumerate(seq):
                s['steps'] += 1
                if r['outcome'] == 'not run':
                    s['not_run'] += 1
                    continue
                if r['outcome'] != 'saved':
                    s['refused'] += 1
                    s['refused: ' + r['class'].split(':')[0]] += 1
                    continue
                s['saved'] += 1
                s['saved_moving_objects'] += bool(r['save'].get('placed_objects_moved'))
                s['safety'] += bool(r['safety'])
                s['quality'] += bool(r['quality'])
                s['structure'] += not r['structure_failed']
                ce = r.get('continued_editing')
                s['continued_editing'] += bool(ce.get('ok') if isinstance(ce, dict) else ce)
                s['placed_audit'] += r['placed']['ok']
                # The generic audit requires all non-text paint unchanged; the
                # placed audit splits it into approved moves and protected paint.
                s['passed_with_placed_audit'] += set(r['failed']) <= {'non_text_paint_identical'} and r['placed']['ok']
                if n == 1:
                    s['undo_page_identical'] += r['vs_source']['page_changed'] == 0
    return dict(s)


def phase_collect(args):
    manifest = json.loads(MANIFEST.read_text())
    labels = dict(x.split('=', 1) for x in args.roots)
    results = {label: json.loads((Path(root) / 'results.json').read_text()) for label, root in labels.items()}
    compact = lambda r: {k: (v[-300:] if k == 'reason' else v.get('ok') if k == 'continued_editing' and isinstance(v, dict) else v)
                         for k, v in r.items() if k not in ('lines', 'frame', 'seconds', 'occurrence')}
    groups = dict(fixtures=list(manifest['fixtures']), real=[c['id'] for c in manifest['cases']])
    out = dict(schema='paragraph-frame-placed-results-1', manifest_sha256=sha(MANIFEST),
               tests=json.loads(Path(args.tests_json).read_text()) if args.tests_json else None,
               regression=json.loads(Path(args.regression_json).read_text()) if args.regression_json else None,
               summary={label: {g: summary(r, ids) for g, ids in groups.items()} for label, r in results.items()},
               results={label: {k: {kind: (seq if kind == 'witness' else [compact(x) for x in seq]) for kind, seq in rows.items()}
                                for k, rows in r.items()} for label, r in results.items()})
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    for label, gs in out['summary'].items():
        for g, s in gs.items():
            print(label, g, json.dumps(s, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('freeze', 'run', 'collect'), required=True)
    p.add_argument('--label')
    p.add_argument('--code', default='.')
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--survey')
    p.add_argument('--only', nargs='*')
    p.add_argument('--roots', nargs='*')
    p.add_argument('--tests-json')
    p.add_argument('--regression-json')
    args = p.parse_args()
    dict(freeze=phase_freeze, run=phase_run, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
