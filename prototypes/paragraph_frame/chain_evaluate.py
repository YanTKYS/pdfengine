"""Lengthening edits whose growth meets the lines below them: an approved chain of following elements (issue #103).

  .venv/bin/python -m prototypes.paragraph_frame.chain_evaluate --phase freeze --fusei <477.pdf>
  .venv/bin/python -m prototypes.paragraph_frame.chain_evaluate --phase run --label old|new --code <tree> --root tmp/<dir> --fusei <477.pdf>
  .venv/bin/python -m prototypes.paragraph_frame.chain_evaluate --phase collect --roots old=tmp/<a> new=tmp/<b>

``freeze`` (before the implementation) fixes the cases:

* every lengthening edit PR #100 refused (evaluations/paragraph_frame_decoration:
  P7-03, P7-05, P7-06, P8-08, P8-09, P8-11 and C02's PR #92 lengthen);
* the two places of 府政だより477 where a survey of every paragraph found
  room for one more line below the lines that follow it (p2: the enquiry
  lines of the two articles above the advertisement), lengthened by one line
  with the paragraph's own glyphs;
* the generated fixtures F1 (free space below), F2 (a rule right below) and
  F3 (a panel ending right below) of tests/test_paragraph_frame_chain.py.

``run``, per case, with the B2 of ``--code``, two steps: the edit, then the
edit back from the saved PDF alone. Each step

1. proposes, and records the chain candidates (``evidence.chain``) and the
   line decoration candidates (``evidence.decorations``) it lists;
2. saves without approving any (a step that needs one must be refused);
3. when candidates are listed, proposes again approving exactly the listed
   ones, until a proposal lists no new one (at most 3 rounds; every approved
   id was listed by a proposal), and saves approving them. The evaluation
   stands for the user who read them; the old B2 lists no chain candidate.

A saved step records the existing audits unchanged (indesign_evaluate.audit
over the edited paragraphs and their followers: safety and quality kept
apart), structure, continued editing, and audits of the approved moves:
``moves`` (every drawing, image and glyph of a moved element translated
exactly, nothing left behind, every other drawing and image unchanged),
``links`` (targets kept, moved areas translated exactly, others unchanged),
``placement`` (moved elements inside the page, no unmoved ink under them
beyond what was under them before), ``raster`` (the moved elements' areas,
MuPDF, whole-pixel translation). The edit back is compared with the source
(restore_evaluate: glyph origins, paragraph and line breaks, MuPDF and Poppler
text, search, drawings, images, links, structure; pixels reported apart).

A refused step ends its case and never counts as success; the first save and
the edit back are counted apart; fixture successes are reported apart.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

import pymupdf

from .decoration_evaluate import _match, cli, raster
from .fidelity_evaluate import locate, reopen, sha, source_of
from .placed_evaluate import block, classify
from .restore_evaluate import glyphs_against, links_against, paint_against, pixels_against
from .roundtrip_evaluate import compare

MANIFEST = Path('evaluations/paragraph_frame_chain/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
DECORATION = Path('evaluations/paragraph_frame_decoration')
FIXTURES = {'F1': 'free space below the two lines: moved on approval, then moved back',
            'F2': 'a rule right below the enquiry line: refused (the chain meets it)',
            'F3': 'a panel ending right below the lines: refused (the chain would leave it)'}
FIXTURE_CASE = dict(page=1, paragraph='本年度の補助事業について、募集を行います。申請書は窓口で配布します。', selection='本年度の補助',
                    box=[39.5, 48.5, 216.5, 96.0], lines=3, edit=dict(old='申請書は窓口で', new='申請書は窓口と郵送と電子申請とファクスと持参で'))
# Places found by the survey (p2), lengthened by one line with glyphs of the paragraph's own font.
SURVEYED = [
    dict(id='P2-R', document='osaka_fusei_477', page=2, paragraph='領収証は必ず受け取り保管してください。', selection='領収証は必ず',
         edit=dict(old='領収証は必ず受け取り保管してください。', new='領収証は必ず受け取り保管してください。申請書は必ず保管してください。'),
         below='the HP line (icon, search box, button, link) and the enquiry line (icon, text, telephone icon, number); 17 pt above the advertisement frame'),
    dict(id='P2-L', document='osaka_fusei_477', page=2, paragraph='マイナンバーに関するお問い合わせはこちら', selection='マイナンバーに関',
         edit=dict(old='マイナンバーに関するお問い合わせはこちら', new='マイナンバーに関するお問い合わせはこちらマイナンバーに関するお問い合わせ'),
         below='the HP line, the enquiry line and the telephone line; 17 pt above the advertisement frame'),
]


# Found after the freeze by a survey of every paragraph of the real PDFs with the
# implementation (chain.plan over one more line): the places where the chain
# holds. Reported apart from the frozen cases.
SUPPLEMENT = [
    dict(id='S1-issue17730', document='issue17730', page=1, paragraph='Linking to an internal destination', selection='Linking to an internal d',
         edit=dict(old='Linking to an internal destination', new='Linking to an internal destination or an external destination'),
         below='five link lines (underlined, link annotations; the second heading drawn in the third line\'s text object), free space below'),
    dict(id='S2-lo_migration_ja', document='lo_migration_ja', page=1, paragraph='1日本語フォントについては残念ながら同梱されていません。IPA フォントなど、すべてのプラットフォームで利用可',
         selection='日本語フォントについては',
         edit=dict(old='日本語フォントについては', new='日本語フォントについては日本語フォントについては'),
         below='the footnote\'s second line (not detected as its paragraph), free space below'),
]


def phase_supplement(args):
    """Add the cases found after the freeze (marked so) to the manifest."""
    manifest = json.loads(MANIFEST.read_text())
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest['supplement'] = [dict(c, box=block(source_of(c['document'], args), c['page'], c['selection']), lines=1,
                                   origin=f'added after the freeze ({commit}): survey of the real PDFs with the implementation') for c in SUPPLEMENT]
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(len(manifest['supplement']), 'supplementary cases')


def phase_freeze(args):
    deco = json.loads((DECORATION / 'manifest.json').read_text())
    old = json.loads((DECORATION / 'results.json').read_text())['results']['new']
    cases = []
    for c in deco['cases']:
        if 'lengthen' not in c['edits']:
            continue
        step = old[c['id']]['lengthen'][0]
        cases.append(dict({k: c[k] for k in ('id', 'document', 'page', 'paragraph', 'selection', 'box', 'lines')},
                          edit=dict(old=c['edits']['lengthen']['old'], new=c['edits']['lengthen']['new']),
                          origin='PR #100 lengthen refused', pr100=dict(outcome=step['outcome'], reason=(step.get('reason') or '')[-300:])))
    for c in SURVEYED:
        source = source_of(c['document'], args)
        box = block(source, c['page'], c['selection'])
        cases.append(dict(c, box=box, lines=1, origin='survey: room for one more line below the following lines (this freeze)'))
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(
        schema='paragraph-frame-chain-manifest-1', baseline_commit='a7bb571 (main after PR #102)', frozen_at=commit,
        policy=('Frozen before the implementation. Old B2 is main a7bb571; new B2 is this branch. Targets: every lengthening edit PR #100 '
                'refused (C02 and the line-decoration cases) and the two places of the survey with room below the following lines. A '
                'following element moves only when its own candidate id is approved; acknowledge_inference or a line decoration approval '
                'never approves another element. Every step approves exactly the candidates its own proposals list; an approval is never '
                'carried from one step to another. A refusal ends its case and never counts as success; the first save and the edit back '
                'are counted apart; fixture successes are reported apart from real-PDF successes. No frame is widened without a region '
                'shown by the page; nothing is pushed off the page, shrunk, painted over or overlapped to fit.'),
        procedure=['propose; record the chain and line decoration candidates',
                   'save without approving them (a step needing one must be refused)',
                   'propose again approving exactly the listed candidates until no new one is listed (3 rounds at most); save approving them',
                   'reopen the saved PDF alone and edit back the same way'],
        measures=('saved or refused (reason); the existing audits (safety, quality: indesign_evaluate.audit over the edited and following '
                  'paragraphs, unchanged); structure; continued editing; moves (moved elements translated exactly, nothing left, other '
                  'paint unchanged); links; placement (inside the page, no new ink under the moved elements); raster of the moved elements; '
                  'the edit back against the source: glyph origins (0.01 pt, 0.001 pt), line breaks, MuPDF and Poppler text, search, drawings, '
                  'images, links (0.001 pt), structure; pixels at 72 and 150 dpi reported apart'),
        target='at least one real lengthening edit refused before is saved; else the evidence of what stops it',
        documents=deco['documents'], fixtures={k: dict(expected=v, edit=FIXTURE_CASE['edit']) for k, v in FIXTURES.items()}, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(len(cases), 'real cases;', len(FIXTURES), 'fixtures')


def _moved(save):
    return (save.get('placed_objects_moved') or []) + (save.get('decorations_moved') or []) + (save.get('chain_moved') or [])


def moves_audit(source, output, page, save):
    """Approved moves (placed objects, line decorations, chain elements) and the protected paint, checked separately
    (decoration_evaluate.moved_audit over every moved element)."""
    areas = [pymupdf.Rect(m['old']) + (-.5, -.5, .5, .5) for m in _moved(save)]
    delta = save.get('follower_translation') if isinstance(save.get('follower_translation'), (int, float)) else 0.
    inside = lambda r, shift=0.: any((a + (0, shift, 0, shift)).contains(pymupdf.Rect(r)) for a in areas)
    r2 = lambda r, dy=0.: [round(r[0], 2), round(r[1] + dy, 2), round(r[2], 2), round(r[3] + dy, 2)]
    drawing = lambda x, dy=0.: ((x.get('type'), str(x.get('fill')), str(x.get('color')), str(x.get('width'))), r2(x['rect'], dy))
    image = lambda x, dy=0.: ((x.get('width'), x.get('height'), x.get('cs-name')), r2(x['bbox'], dy))
    glyph = lambda c, dy=0.: (c['c'], [round(c['origin'][0], 2), round(c['origin'][1] + dy, 2)])
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        pa, pb = a[page - 1], b[page - 1]
        da, db = pa.get_drawings(), pb.get_drawings()
        ia, ib = pa.get_image_info(xrefs=True), pb.get_image_info(xrefs=True)
        ga = [c for bl in pa.get_text('rawdict')['blocks'] for l in bl.get('lines', []) for s in l['spans'] for c in s['chars']]
        gb = [c for bl in pb.get_text('rawdict')['blocks'] for l in bl.get('lines', []) for s in l['spans'] for c in s['chars']]
    moved_d = [drawing(x, delta) for x in da if inside(x['rect'])]
    moved_g = [glyph(c, delta) for c in ga if inside(c['bbox'])]
    oi, si = [x for x in ia if inside(x['bbox'])], [x for x in ib if inside(x['bbox'], delta)]
    paired = 0
    for x in list(oi):
        hit = next((y for y in si if y.get('digest') == x.get('digest') and _match([image(x, delta)], [image(y)], .02)), None)
        if hit is not None:
            oi.remove(x)
            si.remove(hit)
            paired += 1
    space = lambda images: sorted((x.get('cs-name'), x.get('bpc')) for x in images)
    approved = dict(elements=len(areas), drawings=len(moved_d), images=paired + len(oi), shading_rasters=len(oi), glyphs=len(moved_g),
                    exact=_match(moved_d, [drawing(x) for x in db if inside(x['rect'], delta)], .02) and space(oi) == space(si)
                    and Counter(map(str, moved_g)) == Counter(str(glyph(c)) for c in gb if inside(c['bbox'], delta)))
    approved['nothing_left'] = not areas or not any(inside(x['rect']) and not inside(x['rect'], delta) for x in db)
    keep = lambda items, f, key, shift=0.: Counter(str(f(x)) for x in items if not inside(x[key], shift))
    protected = dict(drawings=keep(da, drawing, 'rect') == keep(db, drawing, 'rect', delta if areas else 0.),
                     images=keep(ia, image, 'bbox') == keep(ib, image, 'bbox', delta if areas else 0.))
    return dict(approved_moves=approved, protected=protected, ok=approved['exact'] and approved['nothing_left'] and all(protected.values()))


def links_audit(source, output, page, save):
    """Every link keeps its target; a moved link's area is its old one translated exactly (0.01 pt)."""
    moved = [pymupdf.Rect(a['old']) for m in _moved(save) for a in m.get('annotations', [])]
    delta = save.get('follower_translation') if isinstance(save.get('follower_translation'), (int, float)) else 0.
    key = lambda l: (l.get('kind'), l.get('uri'), l.get('page'), str(l.get('to')), l.get('file'))
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        la, lb = a[page - 1].get_links(), b[page - 1].get_links()
    pool = list(lb)
    for l in la:
        r = l['from']
        shift = delta if any(max(abs(x - y) for x, y in zip(r, m)) < .01 for m in moved) else 0.
        want = (r.x0, r.y0 + shift, r.x1, r.y1 + shift)
        hit = next((j for j, x in enumerate(pool) if key(x) == key(l) and max(abs(p - q) for p, q in zip(x['from'], want)) <= .01), None)
        if hit is None:
            return dict(links=len(la), moved=len(moved), ok=False)
        pool.pop(hit)
    return dict(links=len(la), moved=len(moved), ok=not pool)


def _ink(path, page):
    """Drawings, images and glyph boxes of a page (page space)."""
    with pymupdf.open(path) as d:
        p = d[page - 1]
        items = [('drawing', pymupdf.Rect(x['rect'])) for x in p.get_drawings()]
        items += [('image', pymupdf.Rect(x['bbox'])) for x in p.get_image_info()]
        items += [('glyph', pymupdf.Rect(c['bbox'])) for b in p.get_text('rawdict')['blocks'] for l in b.get('lines', [])
                  for s in l['spans'] for c in s['chars'] if c['c'].strip()]
        return items, pymupdf.Rect(p.rect)


def placement_audit(source, output, page, save):
    """Moved elements lie inside the page; the unmoved ink under them (not holding them) is no more than was under them before."""
    moved = _moved(save)
    if not moved:
        return None
    def under(items, areas):
        out = Counter()
        for kind, r in items:
            if any((a + (-.5, -.5, .5, .5)).contains(r) or r.contains(a) for a in areas):
                continue
            hit = [r & a for a in areas]
            out[kind] += any(not h.is_empty and h.width > .05 and h.height > .05 for h in hit)
        return out
    old_items, rect = _ink(source, page)
    new_items, _ = _ink(output, page)
    olds, news = [pymupdf.Rect(m['old']) for m in moved], [pymupdf.Rect(m['new']) for m in moved]
    before, after = under(old_items, olds), under(new_items, news)
    inside = all(rect.contains(r) for r in news)
    return dict(inside_page=inside, under_before=dict(before), under_after=dict(after),
                ok=inside and all(after[k] <= before[k] for k in after))


def chain_step(code, start, case, old, new, after, output, sub, grow, pitch):
    """One step: candidates, the unapproved save, and the save approving exactly the listed candidates."""
    from .indesign_evaluate import audit
    from .tagged_evaluate import structure_audit
    occurrence = locate(start, case['page'], old, case['box'], code)
    base = ['--source', str(Path(start).resolve()), '--page', str(case['page']), '--old', old, '--new', new, '--occurrence', str(occurrence)]

    def listed(chain, decorations):
        out = sub / 'proposal-only.json'
        if out.exists():
            out.unlink()
        args = ['propose'] + base + sum((['--chain', i] for i in chain), []) + sum((['--decoration', i] for i in decorations), [])
        rc, reason, _ = cli(code, args, out)
        if rc:
            return None, reason
        ev = json.loads(out.read_text())['evidence']
        return dict(chain=(ev.get('chain') or {}), decorations=(ev.get('decorations') or {})), None

    first, problem = listed([], [])
    record = dict(old=old, new=new, occurrence=occurrence, proposal_refused=problem,
                  chain=(first or {}).get('chain'), decorations=(first or {}).get('decorations'))
    plain = sub / 'unapproved.pdf'
    for p in (plain, plain.with_suffix('.proposal.json'), plain.with_suffix('.save.json')):
        if p.exists():
            p.unlink()
    rc, reason, seconds = cli(code, ['reedit'] + base + ['--acknowledge-inference'], plain)
    record['unapproved'] = dict(outcome='refused' if rc else 'saved', reason=reason if rc else None, seconds=seconds)
    ids = lambda found: ([c['id'] for c in (found or {}).get('chain', {}).get('candidates', [])],
                         [c['id'] for c in (found or {}).get('decorations', {}).get('candidates', [])])
    chain, decorations = ids(first)
    rounds = [dict(chain=chain, decorations=decorations)]
    for _ in range(2):
        if not chain and not decorations:
            break
        again, problem = listed(chain, decorations)
        if again is None:
            rounds.append(dict(refused=problem))
            break
        more_c, more_d = ids(again)
        if set(more_c) <= set(chain) and set(more_d) <= set(decorations):
            break
        chain, decorations = sorted(set(chain) | set(more_c)), sorted(set(decorations) | set(more_d))
        rounds.append(dict(chain=chain, decorations=decorations))
    record['rounds'] = rounds
    if not chain and not decorations:
        result = record['unapproved']
        if result['outcome'] == 'saved':
            for p in (plain, plain.with_suffix('.proposal.json'), plain.with_suffix('.save.json')):
                p.replace(output.parent / p.name.replace('unapproved', 'out'))
    else:
        args = ['reedit'] + base + ['--acknowledge-inference'] + sum((['--chain', i] for i in chain), []) + sum((['--decoration', i] for i in decorations), [])
        rc, reason, seconds = cli(code, args, output)
        result = dict(outcome='refused' if rc else 'saved', reason=reason if rc else None, seconds=seconds)
    record.update(outcome=result['outcome'], seconds=result['seconds'], approved=dict(chain=chain, decorations=decorations))
    if result['outcome'] != 'saved':
        record['reason'] = result['reason']
        record['class'] = classify(result['reason'])
        return record
    proposal = json.loads(output.with_suffix('.proposal.json').read_text())
    save = json.loads(output.with_suffix('.save.json').read_text())
    x0, y0, x1, y1 = case['box']
    added = max(0, len(proposal['lines']) - case['lines'])
    region = [x0 - 3, y0 - 3, x1 + 3, y1 + 3 + max(added, grow) * pitch]
    a = audit(start, output, case['page'], region, old, new, sub, occurrence=occurrence)
    moved = [m for m in (save.get('decorations_moved') or []) + (save.get('chain_moved') or [])]
    record.update(lines=[l['text'] for l in proposal['lines']], grow=added, frame=proposal['evidence']['inferred'].get('frame'),
                  safety=a['safety'], quality=a['quality'], failed=a['failed'],
                  structure_failed=structure_audit(start, output, case['page'], after, [])['failed'],
                  continued_editing=reopen(code, output, case['page'], new, after),
                  save={k: save.get(k) for k in ('follower_translation', 'followers_repainted', 'placed_objects_moved', 'decorations_moved', 'chain_moved')},
                  moves=moves_audit(start, output, case['page'], save), links=links_audit(start, output, case['page'], save),
                  placement=placement_audit(start, output, case['page'], save),
                  raster=raster(start, output, case['page'], moved, save.get('follower_translation') or 0))
    return record


def run_case(code, case, source, root):
    from .tagged_evaluate import structure_audit
    pitch = (case['box'][3] - case['box'][1]) / max(1, case['lines'])
    case = dict(case, box=block(source, case['page'], case.get('selection') or case['paragraph'][:6]))
    old, new = case['edit']['old'], case['edit']['new']
    text = case['paragraph']
    changed = text.replace(old, new, 1)
    seq, prev, grow = [], source, 0
    for n, (a, b, after) in enumerate([(old, new, changed), (new, old, text)]):
        sub = root / case['id'] / f'{n + 1}'
        sub.mkdir(parents=True, exist_ok=True)
        output = sub / 'out.pdf'
        for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
            if p.exists():
                p.unlink()
        try:
            r = chain_step(code, prev, case, a, b, after, output, sub, grow, pitch)
        except SystemExit as exc:
            r = dict(old=a, new=b, outcome='not run', reason=str(exc)[:300])
        if r['outcome'] == 'saved' and n == 1:
            r['restored'] = dict(
                compare=compare(source, output, case['page'], case['box'], text, r['frame'], 2 * pitch, sub / 'vs-source', search=old),
                glyphs=glyphs_against(source, output, case['page']), paint=paint_against(source, output, case['page']),
                links=links_against(source, output, case['page']),
                structure_failed=structure_audit(source, output, case['page'], text, [])['failed'],
                pixels=pixels_against(source, output, case['page']))
        seq.append(r)
        print(case['id'], n + 1, r['outcome'], [len(x.get('chain', [])) + len(x.get('decorations', [])) for x in r.get('rounds', []) if 'chain' in x],
              (r.get('unapproved') or {}).get('outcome'),
              (r.get('reason') or '')[-160:] if r['outcome'] != 'saved' else
              json.dumps(dict(failed=r['failed'], moves=r['moves']['ok'], links=r['links']['ok'], placement=(r['placement'] or {}).get('ok'),
                              raster=(r['raster'] or {}).get('ok'), **{'vs_source_' + k: v.get('ok', v.get('identical')) if isinstance(v, dict) else v
                                                                      for k, v in (r.get('restored') or {}).items() if k != 'compare'})),
              flush=True)
        if r['outcome'] != 'saved':
            break
        prev = output
        grow = max(grow, r['grow'])
    return seq


def fixtures(folder):
    """The generated fixture PDFs (test helpers)."""
    sys.path.insert(0, str(Path('tests').resolve()))
    import test_paragraph_frame_chain as t
    folder.mkdir(parents=True, exist_ok=True)
    return {name: t.fixture(name, folder / name) for name in FIXTURES}


def phase_run(args):
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path(args.code).resolve()
    only = set(args.only or [])
    results = json.loads((root / 'results.json').read_text()) if (root / 'results.json').exists() and only else {}
    made = fixtures(root / 'fixtures')
    for name in manifest['fixtures']:
        if only and name not in only:
            continue
        results[name] = run_case(code, dict(FIXTURE_CASE, id=name, document=name), made[name].resolve(), root)
    for case in manifest['cases'] + manifest.get('supplement', []):
        if only and case['id'] not in only:
            continue
        results[case['id']] = run_case(code, case, source_of(case['document'], args).resolve(), root)
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')
    (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def summary(results, ids):
    s = Counter()
    for k in ids:
        seq = results.get(k)
        if not seq:
            continue
        s['cases'] += 1
        r = seq[0]
        s['with_chain_candidates'] += bool((r.get('chain') or {}).get('candidates'))
        if (r.get('chain') or {}).get('candidates') or (r.get('decorations') or {}).get('candidates'):
            s['unapproved_refused'] += (r.get('unapproved') or {}).get('outcome') == 'refused'
        if r['outcome'] != 'saved':
            s['edit_refused' if r['outcome'] == 'refused' else 'edit_not_run'] += 1
            continue
        s['edit_saved'] += 1
        s['edit_chain_moved'] += bool((r['save'] or {}).get('chain_moved'))
        s['edit_safety'] += bool(r['safety'])
        s['edit_quality'] += bool(r['quality'])
        s['edit_structure'] += not r['structure_failed']
        ce = r.get('continued_editing')
        s['edit_continued_editing'] += bool(ce.get('ok') if isinstance(ce, dict) else ce)
        s['edit_moves_audit'] += bool(r['moves']['ok'])
        s['edit_links_audit'] += bool(r['links']['ok'])
        s['edit_placement'] += bool((r['placement'] or {}).get('ok', True))
        s['edit_raster_identical'] += bool((r['raster'] or {}).get('ok', True))
        if len(seq) < 2:
            continue
        b = seq[1]
        if b['outcome'] != 'saved':
            s['back_refused' if b['outcome'] == 'refused' else 'back_not_run'] += 1
            continue
        s['back_saved'] += 1
        s['back_safety'] += bool(b['safety'])
        s['back_quality'] += bool(b['quality'])
        s['back_moves_audit'] += bool(b['moves']['ok'])
        s['back_links_audit'] += bool(b['links']['ok'])
        x = b['restored']
        s['glyphs_at_source_001'] += x['glyphs']['ok']
        s['glyphs_at_source_0001'] += x['glyphs']['ok'] and not x['glyphs']['beyond_0001']
        s['breaks_kept'] += x['compare']['paragraph']['breaks_kept'] == x['compare']['paragraph']['source_breaks_outside_edit']
        s['text_identical_both'] += all(x['compare']['text_identical'].values())
        s['search_same'] += (x['compare'].get('search_hits') or {}).get('reference') == (x['compare'].get('search_hits') or {}).get('output')
        s['drawings_identical'] += x['paint']['drawings']
        s['images_identical_or_shading_raster'] += x['paint']['images']
        s['links_identical'] += x['links']['ok']
        s['structure_vs_source'] += not x['structure_failed']
        s['pixels_identical'] += x['pixels']['identical']
    return dict(s)


def phase_collect(args):
    manifest = json.loads(MANIFEST.read_text())
    labels = dict(x.split('=', 1) for x in args.roots)
    results = {label: json.loads((Path(root) / 'results.json').read_text()) for label, root in labels.items()}
    compact = lambda r: {k: (v[-300:] if k == 'reason' and v else v.get('ok') if k == 'continued_editing' and isinstance(v, dict) else v)
                         for k, v in r.items() if k not in ('lines', 'frame', 'seconds', 'occurrence')}
    groups = dict(fixtures=list(manifest['fixtures']), real=[c['id'] for c in manifest['cases']],
                  supplement=[c['id'] for c in manifest.get('supplement', [])])
    out = dict(schema='paragraph-frame-chain-results-1', manifest_sha256=sha(MANIFEST),
               tests=json.loads(Path(args.tests_json).read_text()) if args.tests_json else None,
               regression=json.loads(Path(args.regression_json).read_text()) if args.regression_json else None,
               survey=json.loads(Path(args.survey_json).read_text()) if args.survey_json else None,
               summary={label: {g: summary(r, ids) for g, ids in groups.items()} for label, r in results.items()},
               results={label: {k: [compact(x) for x in seq] for k, seq in r.items()} for label, r in results.items()})
    RESULTS.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    for label, gs in out['summary'].items():
        for g, s in gs.items():
            print(label, g, json.dumps(s, ensure_ascii=False))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('freeze', 'supplement', 'run', 'collect'), required=True)
    p.add_argument('--label')
    p.add_argument('--code', default='.')
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--only', nargs='*')
    p.add_argument('--roots', nargs='*')
    p.add_argument('--tests-json')
    p.add_argument('--regression-json')
    p.add_argument('--survey-json')
    args = p.parse_args()
    dict(freeze=phase_freeze, supplement=phase_supplement, run=phase_run, collect=phase_collect)[args.phase](args)


if __name__ == '__main__':
    main()
