"""Tagged-PDF editing evaluation against the frozen manifest.

  .venv/bin/python -m prototypes.paragraph_frame.tagged_evaluate --root tmp/<new> --fusei <477.pdf> --pdfjs <dir> \
      --phase requests|samples|collect [--survey tmp/tg-survey] [--pr90-survey tmp/id-survey-final] [--tests-json f] [--regression-json f]

``requests`` runs every manifest request in its own process from the PDF of
the previous step only, as frozen (no substitute font). A request refused
only because a glyph is missing from the embedded subset is run once more
with an explicitly approved substitute font; that run is reported separately
and never replaces the frozen outcome. ``samples`` saves two real text
changes (delete one character; repeat one character) on paragraphs that main
refused for marked-content or structure reasons and the change proposes.
Every save is audited for safety and quality (indesign_evaluate.audit: both
renderers, region, extraction) and for structure: the structure tree is
unchanged except the ActualText of the edited elements, every ActualText
equals its drawn text, and the paragraph's MCIDs draw the expected text.
Requests edit the frozen paragraph (``locate``). Beside the audit, never in
place of it, ``supplementary`` reports the text inside the allowed region by
both renderers and, as a control, the audit's line-grid checks applied to the
source itself. Continued editing selects the new text with enough context to
be unique on the page.
``collect`` writes the compact results; generated PDFs stay under ``--root``.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import pymupdf
from pypdf import PdfReader
from . import semantics
from .audit import semantic
from .auto import propose
from .engine import Refused
from .indesign_evaluate import audit, bare, chars, classify, dump, inside, load, reopen, sha, survey_table

MANIFEST = Path('evaluations/paragraph_frame_tagged/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
STRUCTURE = ('mixed or nested marked-content scope', 'unbound marked content', 'crosses a marked-content', 'span adapter',
             'semantic paragraph parents', 'IDTree', 'semantic association', 'structure', 'marked content', 'ActualText',
             'paragraph child', 'span child', 'leaf semantics', 'paragraph semantics', 'scope semantics')


def source_of(doc, args):
    if doc == 'osaka_fusei_477':
        return args.fusei
    if doc.startswith(('word_', 'lo_')):
        return Path('evaluations/realpdf/corpus') / f'{doc}.pdf'
    return args.pdfjs / f'{doc}.pdf'


def locate(source, page, old, box):
    """The occurrence of ``old`` whose paragraph is the frozen one (its first
    line lies in the frozen box), counted as the engine counts matches. The
    frozen request names its paragraph by its first words and box; the first
    match on the page may be another paragraph."""
    from pdfeditor.content_stream import ContentPage
    from pdfeditor.marked_content import observe_marked_content
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import group_text, observe
    c = ContentPage(str(source), page)
    try:
        _, _, groups = observe(c, observe_marked_content(str(source), page), _Analysis(str(source), page))
        k = 0
        for g in groups:
            text = group_text(c, g)[0]
            x0, y0, x1, y1 = g[0]['bbox']
            inside_box = box[0] - 3 <= (x0 + x1) / 2 <= box[2] + 3 and box[1] - 3 <= (y0 + y1) / 2 <= box[3] + 3
            at = 0
            while (pos := text.find(old, at)) >= 0:
                if inside_box:
                    return k
                k += 1
                at = pos + 1
        return 0
    finally:
        c.close()


def edit(source, page, old, new, output, substitute=None, occurrence=0):
    command = [sys.executable, '-m', 'prototypes.paragraph_frame.generalization_cli', 'reedit', '--source', str(source), '--page', str(page),
               '--old', old, '--new', new, '--output', str(output), '--acknowledge-inference', '--occurrence', str(occurrence)]
    if substitute:
        command += ['--substitute-json', str(substitute)]
    started = time.perf_counter()
    run = subprocess.run(command, capture_output=True)
    seconds = round(time.perf_counter() - started, 2)
    if run.returncode:
        last = [l for l in run.stderr.decode().splitlines() if l.strip()]
        return dict(outcome='refused', reason=last[-1] if last else 'unknown', seconds=seconds, published=Path(output).exists())
    return dict(outcome='saved', proposal=load(Path(output).with_suffix('.proposal.json')), save=load(Path(output).with_suffix('.save.json')),
                seconds=seconds, pdf_sha256=sha(output))


def mcid_text(path, page):
    """{MCID: (drawn text, owner ActualText or None)} of a page."""
    from pdfeditor.content_stream import ContentPage
    from pdfeditor.marked_content import observe_marked_content
    marks = observe_marked_content(str(path), page)
    c = ContentPage(str(path), page)
    try:
        out = {}
        for s in marks['scopes']:
            a = s['association']
            if a.get('mcid') is None or s['invocation']:
                continue
            lo, hi = s['content_byte_range']
            text = ''.join(ch.text for e in c.events if e.stream_xref == s['stream_xref'] and not e.invocation and lo <= e.operator.start < hi for ch in e.chars)
            owner = c.reader.get_object(a['owner_reference']['xref']) if a.get('owner_reference') else {}
            out[a['mcid']] = (text, str(owner['/ActualText']) if '/ActualText' in owner else None)
        return out
    finally:
        c.close()


def sequences(path, page):
    """Multiset of marked-content sequences (tag and properties without MCID) of a page program."""
    from pdfeditor.marked_content import observe_marked_content
    out = {}
    for s in observe_marked_content(str(path), page)['scopes']:
        props = s['properties'] if isinstance(s['properties'], dict) else {}
        key = s['tag'] + json.dumps({k: v for k, v in props.items() if k not in ('/MCID',)}, sort_keys=True, default=str)
        out[key] = out.get(key, 0) + 1
    return out


def structure_audit(source, output, page, expected_text, mcids):
    """Structure checks of one save; ``mcids`` are the paragraph's MCIDs in the source (may be empty)."""
    checks, details = {}, {}
    before, after = semantics.canonical(PdfReader(str(source))), semantics.canonical(PdfReader(str(output)))
    changed, other = semantics.changes(before, after)
    checks['structure_tree_unchanged_except_actualtext'] = not other
    details['actualtext_changed_elements'] = len(changed)
    sb, sa = semantic(str(source), page)['checks'], semantic(str(output), page)['checks']
    checks['semantic_checks_not_worse'] = not any(sb.get(k, True) and not v for k, v in sa.items())
    if before is not None:
        rows = mcid_text(output, page)
        checks['actualtext_equals_drawn_text'] = all(at is None or at == text for text, at in rows.values())
        checks['mcids_kept'] = set(mcid_text(source, page)) == set(rows)
        if mcids:
            drawn = bare(''.join(rows[m][0] for m in mcids if m in rows))
            checks['paragraph_mcids_draw_the_new_text'] = bare(expected_text) in drawn
    seq_b, seq_a = sequences(source, page), sequences(output, page)
    # Atoms (replacement-text spans) may disappear with their glyphs; nothing appears.
    checks['no_new_marked_content'] = all(seq_a.get(k, 0) <= v for k, v in seq_b.items()) and set(seq_a) <= set(seq_b)
    details['sequences_removed'] = {k: v - seq_a.get(k, 0) for k, v in seq_b.items() if seq_a.get(k, 0) != v}
    return dict(checks=checks, details=details, passed=all(checks.values()), failed=[k for k, v in checks.items() if not v])


def reopen_unique(output, page, new, paragraph):
    """Continued editing from the saved PDF alone, selecting the new text with
    enough of its paragraph around it to be unique on the page (a short
    replacement such as 'If' otherwise selects another paragraph)."""
    at = paragraph.find(new)
    if at < 0:
        return reopen(output, page, new)
    for grow in range(0, len(paragraph) + 1, 4):
        selection = paragraph[max(0, at - grow):at + len(new) + grow].strip()
        try:
            p = propose(str(output), page, selection, selection)
        except Refused as exc:
            if 'not found' in str(exc):
                continue
            return dict(ok=False, reason=str(exc), selection=selection)
        if p['evidence']['selection_match_count'] == 1:
            status = p['evidence']['conditions']['status']
            return dict(ok=status == 'applied', status=status, selection=selection)
    # The whole paragraph is also part of another one: the occurrence whose
    # paragraph is the edited paragraph.
    bare_paragraph = bare(paragraph)
    for k in range(64):
        try:
            p = propose(str(output), page, new, new, occurrence=k)
        except Refused as exc:
            if 'not found' in str(exc):
                break
            continue
        if bare(p['paragraphs'][0]) == bare_paragraph:
            status = p['evidence']['conditions']['status']
            return dict(ok=status == 'applied', status=status, selection=new, occurrence=k)
    return reopen(output, page, new)


def region_text(path, page, region):
    """Text of a region by both renderers (MuPDF clip, Poppler crop)."""
    with pymupdf.open(path) as d:
        mupdf = d[page - 1].get_text(clip=pymupdf.Rect(region))
    x0, y0, x1, y1 = region
    run = subprocess.run(['pdftotext', '-f', str(page), '-l', str(page), '-x', str(int(x0)), '-y', str(int(y0)), '-W', str(int(x1 - x0 + 1)),
                          '-H', str(int(y1 - y0 + 1)), '-enc', 'UTF-8', str(path), '-'], capture_output=True, check=True)
    return dict(mupdf=mupdf, poppler=run.stdout.decode('utf-8'))


def supplementary(source, output, page, region, old, new, lines=None):
    """Checks reported beside the audit, never in place of it.

    ``region_extraction_*``: the text inside the allowed region changes by the
    replacement only (the whole-page checks also see reading-order changes
    elsewhere on the page). ``source_control_*``: the audit's line-grid checks
    applied to the source itself; a check the source already fails is not
    evidence about the edit.
    """
    checks = {}
    before, after = region_text(source, page, region), region_text(output, page, region)
    for renderer in ('mupdf', 'poppler'):
        tb, ta = bare(before[renderer]), bare(after[renderer])
        checks[f'region_extraction_{renderer}'] = bare(old) in tb and ta == tb.replace(bare(old), bare(new), 1)
    if lines:
        glyphs = [g for g in chars(source, page) if inside(g['bbox'], region) and g['c'].strip()]
        baselines = sorted({round(l['baseline'], 1) for l in lines})
        ys = sorted({g['origin'][1] for g in glyphs})
        checks['source_control_baselines_on_the_paragraph_grid'] = all(any(abs(y - b) <= .2 for b in baselines) for y in ys)
        lefts = {round(min(g['origin'][0] for g in glyphs if abs(g['origin'][1] - y) < .2), 1) for y in ys}
        firsts = {round(l['bbox'][0], 1) for l in lines}
        checks['source_control_left_edges_are_line_starts'] = all(any(abs(x - s) <= .3 for s in firsts) for x in lefts)
    return checks


def substitute(root):
    path = root / 'substitute.ttf'
    if not path.exists():
        path.write_bytes(pymupdf.Font('cjk').buffer)
    record = dict(path=str(path.resolve()), sha256=sha(path), font_index=0, relation='substituted')
    (root / 'substitute.json').write_text(json.dumps(record))
    return root / 'substitute.json'


def run_request(r, source, folder, sub=None):
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / 'out.pdf'
    for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
        if p.exists():
            p.unlink()
    occurrence = locate(source, r['page'], r['old'], r['box'])
    result = edit(source, r['page'], r['old'], r['new'], output, sub, occurrence)
    record = dict(outcome=result['outcome'], seconds=result['seconds'], occurrence=occurrence)
    if result['outcome'] == 'refused':
        record.update(reason=result['reason'], reason_class=classify(result['reason']), published=result['published'])
        return record, None
    save = result['save']
    record.update(lines=[l['text'] for l in result['proposal']['lines']], font_counts=result['proposal']['font_counts'],
                  insertion_inside_text_object=save.get('insertion_inside_text_object'), timing={k: round(v, 2) for k, v in save['timing'].items()})
    record['audit'] = audit(source, output, r['page'], r['allowed_region'], r['old'], r['new'], folder, lines=r['lines'], occurrence=occurrence)
    mcids = [s['mcid'] for s in r['structure'] if s.get('status') == 'tree_backed']
    record['structure'] = structure_audit(source, output, r['page'], r['expected_text'], mcids)
    record['supplementary'] = supplementary(source, output, r['page'], r['allowed_region'], r['old'], r['new'], r['lines'])
    record['continued_editing'] = reopen_unique(output, r['page'], r['new'], r['expected_text'])
    return record, output


def phase_requests(root, manifest, args):
    results, outputs = {}, {}
    sub = substitute(root)
    for r in manifest['requests']:
        rows = {}
        for mode, chain in (('frozen', outputs), ('substitute', outputs)):
            key = (mode, r['after'])
            if mode == 'substitute':
                frozen = rows['frozen']
                prior_sub = r['after'] and ('substitute', r['after']) in outputs
                if not ('missing source glyph' in (frozen.get('reason') or '') or prior_sub):
                    break
            source = outputs.get(key) if r['after'] else source_of(r['document'], args)
            if source is None:
                rows[mode] = dict(outcome='not run', reason='the previous step was not saved')
                continue
            record, output = run_request(r, source, root / 'requests' / mode / r['id'], sub if mode == 'substitute' else None)
            rows[mode] = record
            if output is not None:
                outputs[(mode, r['id'])] = output
        rows['frozen']['as_expected'] = rows['frozen']['outcome'] == r['expected_outcome']
        results[r['id']] = dict(document=r['document'], purpose=r['purpose'], expected=r['expected_outcome'], expected_reason=r['expected_reason'],
                                after=r['after'], **rows)
        print(r['id'], {m: (v['outcome'], v.get('reason') or (v.get('audit') or {}).get('failed'), (v.get('structure') or {}).get('failed'))
                        for m, v in rows.items()}, flush=True)
    dump(root / 'requests.json', results)


def structural_refusal(reason):
    return any(k in (reason or '') for k in STRUCTURE)


def phase_samples(root, manifest, args):
    rows = survey_table(args.survey)
    pr90 = {d for d in json.loads(Path('evaluations/paragraph_frame_indesign/manifest.json').read_text())['survey']['pages']}
    pr90 |= {c.split(':')[0] for c in json.loads(Path('evaluations/paragraph_frame_indesign/manifest.json').read_text())['survey']['control_corpus']}
    newly = [r for r in rows if r['main'] == 'refused' and r['new'] == 'proposed' and (structural_refusal(r['main_reason']) or r['document'] not in pr90)]
    sample = []
    for doc in sorted({r['document'] for r in newly}):
        mine = [r for r in newly if r['document'] == doc]
        step = max(1, len(mine) // 6)
        sample += mine[::step][:6]
    results = []
    for n, r in enumerate(sample):
        source = source_of(r['document'], args)
        sel = r['selection']
        k = max(i for i, ch in enumerate(sel) if ch.strip())
        for kind, new in (('shorten', sel[:k] + sel[k + 1:]), ('lengthen', sel[:k + 1] + sel[k] + sel[k + 1:])):
            folder = root / 'samples' / f'{n:03d}-{kind}'
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / 'out.pdf'
            for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
                if p.exists():
                    p.unlink()
            result = edit(source, r['page'], sel, new, output)
            record = dict(document=r['document'], page=r['page'], selection=sel, new=new, kind=kind, main_reason=r['main_reason'],
                          outcome=result['outcome'], seconds=result['seconds'])
            if result['outcome'] == 'refused':
                record.update(reason=result['reason'], reason_class=classify(result['reason']))
            else:
                x0, y0, x1, y1 = r['bbox']
                pitch = max(4.0, (y1 - y0) / max(1, len(result['proposal']['lines'])))
                region = [x0 - 3, y0 - 3, x1 + 3, y1 + 3 + (pitch if kind == 'lengthen' else 0)]
                record['audit'] = audit(source, output, r['page'], region, sel, new, folder)
                expected = result['proposal']['expected']
                record['structure'] = structure_audit(source, output, r['page'], expected, [])
                record['supplementary'] = supplementary(source, output, r['page'], region, sel, new)
                record['continued_editing'] = reopen_unique(output, r['page'], new, expected)
            results.append(record)
            print(n, kind, r['document'], r['page'], record['outcome'], record.get('reason') or (record.get('audit') or {}).get('failed'),
                  (record.get('structure') or {}).get('failed'), flush=True)
    dump(root / 'samples.json', dict(newly=len(newly), sampled=len(sample), results=results))


def outside_changes(source, output, page, region, proposal, save, folder):
    """What changed outside the allowed region of a saved sample.

    ``own_text_in_frame``: no glyph outside the region was removed or moved,
    and every new glyph and changed pixel outside it lies in the frame the
    proposal declared and the user approved (a re-wrapped line may run past
    the paragraph's old box; an added line lies below it).
    ``declared_follower_moves``: the glyphs that changed are those of the
    following paragraphs the proposal declared, translated rigidly by the
    declared distance. Reported beside the safety audit, never in place of it.
    """
    from .audit import pixels
    frames = proposal['evidence']['inferred'].get('frames') or [proposal['evidence']['inferred']['frame']]
    key = lambda g: (g['c'], g['size'], g['font'], g['color'])
    before = Counter((key(g), g['origin']) for g in chars(source, page) if not inside(g['bbox'], region))
    after_glyphs = [g for g in chars(output, page) if not inside(g['bbox'], region)]
    after = Counter((key(g), g['origin']) for g in after_glyphs)
    gone, new = before - after, after - before
    new_boxes = [g['bbox'] for g in after_glyphs if (key(g), g['origin']) in new]
    in_frame = all(any(inside(b, f) for f in frames) for b in new_boxes)
    raster = pixels(str(source), str(output), folder / 'pixels-frame', dict(page=page, frames=[region] + frames))
    raster = {k: sum(v['outside_pixels'] for v in pages) for k, pages in raster.items()}
    out = dict(removed_or_moved_outside=sum(gone.values()), new_outside=sum(new.values()), new_outside_in_frame=in_frame,
               pixels_outside_region_and_frame=raster)
    out['own_text_in_frame'] = not gone and in_frame and not any(raster.values())
    delta = save.get('follower_translation')
    if gone and isinstance(delta, (int, float)):
        lines = proposal['evidence']['observed']['lines'][proposal['source_lines']:]
        boxes = [pymupdf.Rect(l['bbox']) for l in lines]
        declared = all(any(b.contains(pymupdf.Point((o[0]), o[1] - .1)) for b in boxes) for (_, o) in gone.elements())
        shifted = sorted((k, (o[0], round(o[1] + delta, 1))) for k, o in gone.elements())
        landed = sorted(new.elements())
        rigid = len(shifted) == len(landed) and all(a[0] == b[0] and abs(a[1][0] - b[1][0]) <= .1 and abs(a[1][1] - b[1][1]) <= .15
                                                    for a, b in zip(shifted, landed))
        out['declared_follower_moves'] = declared and rigid
    return out


def track_pr90(previous, current):
    """Where the marked-content refusals of the PR #90 survey went.

    A refused paragraph is found again by its selection; when the new
    inference divides the area differently (table cells, list items), by the
    paragraphs drawn over its box on the same page.
    """
    key = lambda r: (r['document'], r['page'], r['selection'])
    refused = [r for r in previous if r['new'] == 'refused' and structural_refusal(r['new_reason'])]
    index = {key(r): r for r in current}
    meets = lambda a, b: not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
    out = dict(total=len(refused), same_paragraph_proposed=0, redivided_proposed=0, refused={}, not_surveyed=0, rows=[])
    for r in refused:
        hit = index.get(key(r))
        if hit is not None:
            outcome = 'proposed' if hit['new'] == 'proposed' else 'refused'
            reason = None if outcome == 'proposed' else re.sub(r'^Refused: ', '', hit['new_reason'] or '')[:80]
            out['same_paragraph_proposed'] += outcome == 'proposed'
            match = 'selection'
        else:
            over = [c for c in current if c['document'] == r['document'] and c['page'] == r['page'] and meets(c['bbox'], r['bbox'])]
            surveyed = [c for c in over if c['new'] != 'skipped']
            if any(c['new'] == 'proposed' for c in surveyed):
                outcome, reason = 'proposed', None
                out['redivided_proposed'] += 1
            elif surveyed:
                outcome = 'refused'
                reason = re.sub(r'^Refused: ', '', surveyed[0]['new_reason'] or '')[:80]
            else:
                outcome, reason = 'not surveyed (shorter than the survey minimum)', None
                out['not_surveyed'] += 1
            match = 'position'
        if outcome == 'refused':
            out['refused'][reason] = out['refused'].get(reason, 0) + 1
        out['rows'].append(dict(document=r['document'], page=r['page'], selection=r['selection'], pr90_reason=r['new_reason'],
                                match=match, outcome=outcome, reason=reason))
    return out


def phase_collect(root, manifest, args):
    requests = load(root / 'requests.json')
    samples = load(root / 'samples.json')
    rows = survey_table(args.survey)
    surveyed = [r for r in rows if r['new'] != 'skipped']
    pr90_docs = set(json.loads(Path('evaluations/paragraph_frame_indesign/manifest.json').read_text())['survey']['pages'])
    pr90_docs |= {c.split(':')[0] for c in json.loads(Path('evaluations/paragraph_frame_indesign/manifest.json').read_text())['survey']['control_corpus']}

    def tally(subset):
        out = dict(paragraphs=len(subset), main_proposed=sum(r['main'] == 'proposed' for r in subset),
                   new_proposed=sum(r['new'] == 'proposed' for r in subset),
                   newly=sum(r['main'] == 'refused' and r['new'] == 'proposed' for r in subset),
                   lost=sum(r['main'] == 'proposed' and r['new'] != 'proposed' for r in subset))
        return out
    pr90 = [r for r in surveyed if r['document'] in pr90_docs]
    structural = [r for r in pr90 if r['main'] == 'refused' and structural_refusal(r['main_reason'])]
    extended = [r for r in surveyed if r['document'] not in pr90_docs]
    by_doc = {}
    for r in surveyed:
        d = by_doc.setdefault(r['document'], dict(paragraphs=0, main_proposed=0, new_proposed=0, newly=0, lost=0, new_refusals={}))
        d['paragraphs'] += 1
        d['main_proposed'] += r['main'] == 'proposed'
        d['new_proposed'] += r['new'] == 'proposed'
        d['newly'] += r['main'] == 'refused' and r['new'] == 'proposed'
        d['lost'] += r['main'] == 'proposed' and r['new'] != 'proposed'
        if r['new'] == 'refused':
            reason = re.sub(r'^Refused: ', '', r['new_reason'] or '')[:70]
            d['new_refusals'][reason] = d['new_refusals'].get(reason, 0) + 1
    # The marked-content refusals of the PR #90 survey: where they went.
    went = {}
    for r in structural:
        key = 'proposed' if r['new'] == 'proposed' else 'refused: ' + re.sub(r'^Refused: ', '', r['new_reason'] or '')[:70]
        went[key] = went.get(key, 0) + 1

    def tiers(v):
        saved = v['outcome'] == 'saved'
        return dict(saved=saved, safety=v['audit']['safety'] if saved else None, quality=v['audit']['quality'] if saved else None,
                    structure=v['structure']['passed'] if saved else None,
                    continued_editing=(v.get('continued_editing') or {}).get('ok') if saved else None)

    def compact(v):
        if v['outcome'] != 'saved':
            return {k: v.get(k) for k in ('outcome', 'reason', 'reason_class', 'published', 'seconds', 'occurrence')}
        return dict(outcome='saved', seconds=v['seconds'], occurrence=v.get('occurrence'), lines=v['lines'], font_counts=v['font_counts'],
                    failed=v['audit']['failed'], supplementary=v.get('supplementary'),
                    structure_failed=v['structure']['failed'], structure_details=v['structure']['details'],
                    outside_pixels=v['audit']['details']['outside_pixels'], continued_editing=v['continued_editing'], tiers=tiers(v), timing=v['timing'])
    compact_requests = {k: dict({x: v[x] for x in ('document', 'purpose', 'expected', 'expected_reason', 'after')},
                                frozen=dict(compact(v['frozen']), as_expected=v['frozen']['as_expected']),
                                **({'substitute': compact(v['substitute'])} if 'substitute' in v else {}))
                        for k, v in requests.items()}
    saved = [s for s in samples['results'] if s['outcome'] == 'saved']
    # Outside-region changes of the saved samples: declared follower moves or not.
    bbox = {(r['document'], r['page'], r['selection']): r['bbox'] for r in rows}
    for i, s in enumerate(samples['results']):
        if s['outcome'] != 'saved' or s['audit']['safety']:
            continue
        folder = root / 'samples' / f'{i // 2:03d}-{s["kind"]}'
        proposal, save = load(folder / 'out.proposal.json'), load(folder / 'out.save.json')
        assert proposal['selection'] == s['selection'] and proposal['replacement'] == s['new'], folder
        x0, y0, x1, y1 = bbox[(s['document'], s['page'], s['selection'])]
        pitch = max(4.0, (y1 - y0) / max(1, len(proposal['lines'])))
        region = [x0 - 3, y0 - 3, x1 + 3, y1 + 3 + (pitch if s['kind'] == 'lengthen' else 0)]
        s['outside_changes'] = outside_changes(source_of(s['document'], args), folder / 'out.pdf', s['page'], region, proposal, save, folder)
    result = dict(
        schema='paragraph-frame-tagged-results-1', manifest_sha256=sha(MANIFEST), tests=load(args.tests_json) if args.tests_json else None,
        regression=load(args.regression_json) if args.regression_json else None, requests=compact_requests,
        survey=dict(main_commit='2aefe3e', pr90_population=tally(pr90), extended_tagged=tally(extended),
                    main_structural_refusals=dict(total=len(structural), newly_proposable=sum(r['new'] == 'proposed' for r in structural), went=went),
                    pr90_survey_refusals=track_pr90(survey_table(args.pr90_survey), rows),
                    documents=by_doc,
                    lost=[dict(document=r['document'], page=r['page'], selection=r['selection'], reason=r['new_reason'])
                          for r in surveyed if r['main'] == 'proposed' and r['new'] != 'proposed']),
        samples=dict(newly=samples['newly'], sampled=samples['sampled'], edits=len(samples['results']), saved=len(saved),
                     safety=sum(s['audit']['safety'] for s in saved), quality=sum(s['audit']['quality'] for s in saved),
                     structure=sum(s['structure']['passed'] for s in saved),
                     continued_editing=sum(bool((s.get('continued_editing') or {}).get('ok')) for s in saved),
                     supplementary={k: sum(bool(s['supplementary'].get(k)) for s in saved)
                                    for k in ('region_extraction_mupdf', 'region_extraction_poppler')},
                     refused=[dict(document=s['document'], page=s['page'], selection=s['selection'], kind=s['kind'], reason=s['reason'])
                              for s in samples['results'] if s['outcome'] == 'refused'],
                     safety_failed_with_own_text_in_frame=sum(bool((s.get('outside_changes') or {}).get('own_text_in_frame')) for s in saved),
                     safety_failed_with_declared_follower_moves=sum(bool((s.get('outside_changes') or {}).get('declared_follower_moves')) for s in saved),
                     failed=[dict(document=s['document'], page=s['page'], selection=s['selection'], kind=s['kind'], failed=s['audit']['failed'],
                                  structure_failed=s['structure']['failed'], supplementary=s['supplementary'],
                                  outside_changes=s.get('outside_changes'), continued_editing=s.get('continued_editing'))
                             for s in saved if s['audit']['failed'] or s['structure']['failed']],
                     seconds=[s['seconds'] for s in samples['results']]))
    dump(RESULTS, result)
    print(json.dumps(result['survey']['main_structural_refusals'], ensure_ascii=False)[:600])
    print(json.dumps({k: v for k, v in result['survey']['pr90_survey_refusals'].items() if k != 'rows'}, ensure_ascii=False))
    print(json.dumps({k: v for k, v in result['samples'].items() if k not in ('seconds', 'refused', 'failed')}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--fusei', type=Path, required=True)
    parser.add_argument('--pdfjs', type=Path, required=True)
    parser.add_argument('--phase', required=True, choices=['requests', 'samples', 'collect'])
    parser.add_argument('--survey', type=Path, default=Path('tmp/tg-survey'))
    parser.add_argument('--pr90-survey', type=Path, default=Path('tmp/id-survey-final'))
    parser.add_argument('--tests-json', type=Path)
    parser.add_argument('--regression-json', type=Path)
    args = parser.parse_args()
    manifest = load(MANIFEST)
    for doc, spec in manifest['documents'].items():
        if sha(source_of(doc, args)) != spec['sha256']:
            raise SystemExit(f'{doc}: source hash differs from the manifest')
    args.root.mkdir(parents=True, exist_ok=True)
    dict(requests=phase_requests, samples=phase_samples, collect=phase_collect)[args.phase](args.root, manifest, args)


if __name__ == '__main__':
    main()
