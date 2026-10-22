"""Column-flow evaluation against the frozen manifest.

  .venv/bin/python -m prototypes.paragraph_frame.column_flow_evaluate --root tmp/<new> \
      --fusei <path to 477.pdf> --phase fixtures|sequences|singles|recognition|survey|collect

Every edit runs propose → approve → save in its own process from the PDF of
the previous step only (``generalization_cli reedit``). Audits read the
manifest oracle (expected texts, allowed movement, observed columns) and the
PDFs with PyMuPDF/Poppler; they never use engine geometry. Four results are
kept separate per step: saved, safety, quality and continued editing.
Generated PDFs, rasters and detailed audit JSON stay under ``--root``.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import pymupdf
from . import column_fixtures as fixtures
from .audit import extract, painting, pixels, semantic
from .auto import propose
from .auto_detect import recognize
from .column_flow_preflight import witness
from .engine import Refused
from .generalization_audit import structural

MANIFEST = Path('evaluations/paragraph_frame_column_flow/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
KINSOKU_START = '、。，．・：；？！ー」』）］｝〉》ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ々'
KINSOKU_END = '「『（［｛〈《'


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bare(text):
    return re.sub(r'\s+', '', text)


def undrawn(doc):
    """Characters of the generator text that the PDF does not draw, as (with, without) contexts.

    LibreOffice 24.2 omitted some line-final punctuation of justified Japanese
    lines. The frozen witness (drawn text) is the oracle for those characters;
    the same contexts are removed from every expected text.
    """
    from difflib import SequenceMatcher
    lines, _ = body(doc, [dict(l, styles=[]) for l in doc['witness']])
    drawn = ''.join(bare(l['text']) for l in lines)
    source = bare(''.join(doc['paragraphs']))
    result = []
    for op, a0, a1, b0, b1 in SequenceMatcher(None, source, drawn, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        if op != 'delete':
            raise ValueError(f'witness differs from the fixture text beyond omissions: {op} {source[a0:a1]!r} {drawn[b0:b1]!r}')
        result.append((source[a0 - 4:a1 + 4], source[a0 - 4:a0] + source[a1:a1 + 4]))
    return result


def as_drawn(texts, omissions):
    result = []
    for text in texts:
        for with_, without in omissions:
            text = text.replace(with_, without)
        result.append(text)
    return result


def source_path(root, manifest, name, fusei):
    doc = manifest['documents'][name]
    if doc['kind'] == 'generated':
        return root / 'fixtures' / f'{name}.pdf'
    return Path(fusei) if name == 'osaka_fusei_477' else Path(doc['path'])


def cli(*args):
    started = time.perf_counter()
    run = subprocess.run([sys.executable, '-m', 'prototypes.paragraph_frame.generalization_cli', *map(str, args)], capture_output=True)
    return run, time.perf_counter() - started


def links_of(source, page):
    out = Path(str(source) + '.columns.json')
    if out.exists():
        out.unlink()
    run, _ = cli('columns', '--source', source, '--page', page, '--output', out)
    if run.returncode:
        raise RuntimeError(run.stderr.decode()[-400:])
    return load(out)


def engine_link(source, page, doc, ident):
    """Map a manifest link ``column a->b`` (body columns of the oracle) to the engine id."""
    a, b = (int(x) for x in ident.split()[1].split('->'))
    cols = doc['columns']
    for link in links_of(source, page)['links']:
        if abs(link['from_x'][0] - cols[a]['x0']) < 1.5 and abs(link['to_x'][0] - cols[b]['x0']) < 1.5:
            return link['id']
    return None


def edit(source, page, old, new, output, continuation=()):
    """propose → approve(acknowledge) → save in a separate process."""
    args = ['reedit', '--source', source, '--page', page, '--old', old, '--new', new, '--output', output, '--acknowledge-inference']
    for ident in continuation:
        args += ['--continuation', ident]
    run, seconds = cli(*args)
    if run.returncode:
        last = [l for l in run.stderr.decode().splitlines() if l.strip()]
        return dict(outcome='refused', reason=last[-1] if last else 'unknown', seconds=seconds, published=Path(output).exists())
    return dict(outcome='saved', proposal=load(Path(output).with_suffix('.proposal.json')), save=load(Path(output).with_suffix('.save.json')),
                seconds=seconds, pdf_sha256=sha(output))


def drawn_lines(path, page):
    """Drawn lines with style: text, ink x0/x1, baseline, font, size, colour (PyMuPDF, independent of the engine).

    x1 is the end of the last non-space glyph: a drawn word space at a line
    end has no ink.
    """
    result = []
    with pymupdf.open(path) as d:
        for b in d[page - 1].get_text('rawdict')['blocks']:
            for line in b.get('lines', []):
                spans = [s for s in line['spans'] if ''.join(ch['c'] for ch in s['chars']).strip()]
                text = ''.join(ch['c'] for s in line['spans'] for ch in s['chars'])
                if not spans:
                    continue
                ink = [ch['bbox'] for s in spans for ch in s['chars'] if ch['c'].strip()]
                result.append(dict(text=text.strip(), x0=round(min(b[0] for b in ink), 2), x1=round(max(b[2] for b in ink), 2),
                                   baseline=round(spans[0]['origin'][1], 2), size=round(spans[0]['size'], 2),
                                   styles=sorted({(s['font'], round(s['size'], 2), s['color']) for s in spans})))
    return result


def column_of(doc, line):
    for k, col in enumerate(doc['columns']):
        x0, _, x1, _ = col['allowed']
        if x0 <= line['x0'] and line['x0'] < x1:
            return k
    return None


def body(doc, lines, size=10.5):
    """Body lines of the flow in reading order (column, baseline) and the rest."""
    flow = [l for l in lines if abs(l['size'] - size) < .05 and column_of(doc, l) is not None]
    flow.sort(key=lambda l: (column_of(doc, l), l['baseline']))
    rest = [l for l in lines if l not in flow]
    return flow, rest


def assign(flow, paragraphs):
    """Paragraph index of each flow line by cumulative non-space characters, or None if misaligned."""
    ends, total = [], 0
    for p in paragraphs:
        total += len(bare(p))
        ends.append(total)
    result, seen, k = [], 0, 0
    for line in flow:
        start = seen
        seen += len(bare(line['text']))
        while k < len(ends) and start >= ends[k]:
            k += 1
        if k >= len(ends) or seen > ends[k]:
            return None
        result.append(k)
    return result if seen == total else None


def audit_step(root, doc, source, output, page, before, after, target, *, tagged, folder):
    """Safety and quality checks of one saved step (oracle: manifest texts and columns)."""
    checks, details = {}, {}
    lb, la = drawn_lines(source, page), drawn_lines(output, page)
    fb, rb = body(doc, lb)
    fa, ra = body(doc, la)
    pb, pa = assign(fb, before), assign(fa, after)
    checks['source_matches_previous_expectation'] = pb is not None
    checks['paragraph_alignment'] = pa is not None
    # Safety.
    checks['outside_content_identical'] = rb == ra
    fixed_b = [l for l, p in zip(fb, pb or []) if p < target]
    fixed_a = [l for l, p in zip(fa, pa or []) if p < target]
    checks['preceding_paragraphs_identical'] = pb is not None and pa is not None and fixed_b == fixed_a
    first_col = min((column_of(doc, l) for l, p in zip(fb, pb or []) if p >= target), default=0)
    deepest = max(c['bottom'] for c in doc['columns'])
    allowed = [c['allowed'] for k, c in enumerate(doc['columns']) if k >= first_col]
    checks['moved_lines_inside_allowed_columns'] = all(
        column_of(doc, l) is not None and column_of(doc, l) >= first_col and l['x1'] <= doc['columns'][column_of(doc, l)]['allowed'][2]
        and l['baseline'] <= deepest + .01 for l, p in zip(fa, pa or []) if p >= target)
    pb_, pa_ = painting(str(source), page), painting(str(output), page)
    checks['non_text_paint_identical'] = all(pb_[k] == pa_[k] for k in ('drawings', 'images', 'links', 'annots'))
    checks['original_font_programs_kept'] = all(pa_['fonts'].get(k) == v for k, v in pb_['fonts'].items())
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        checks['page_count'] = len(a) == len(b)
    mask = dict(page=page, frame=[min(r[0] for r in allowed), min(r[1] for r in allowed), max(r[2] for r in allowed), max(r[3] for r in allowed)])
    raster = pixels(str(source), str(output), folder / 'pixels', mask)
    details['outside_pixels'] = {k: sum(v['outside_pixels'] for v in rows) for k, rows in raster.items()}
    checks['raster_outside_allowed_columns_mupdf'] = details['outside_pixels']['mupdf'] == 0
    checks['raster_outside_allowed_columns_poppler'] = details['outside_pixels']['poppler'] == 0
    if tagged:
        sb, sa = semantic(str(source), page), semantic(str(output), page)
        checks['semantic_internal'] = all(sa['checks'].values())
        checks['structure_tree_unchanged'] = structural(sb['tree']) == structural(sa['tree'])
    safety = [k for k in ('outside_content_identical', 'preceding_paragraphs_identical', 'moved_lines_inside_allowed_columns', 'non_text_paint_identical',
                          'original_font_programs_kept', 'page_count', 'raster_outside_allowed_columns_mupdf', 'raster_outside_allowed_columns_poppler',
                          'semantic_internal', 'structure_tree_unchanged', 'source_matches_previous_expectation') if k in checks]
    # Quality.
    expected = ''.join(after)
    checks['logical_text_exact'] = bare(''.join(l['text'] for l in fa)) == bare(expected)
    if ' ' in expected:
        checks['latin_words_exact'] = ' '.join(l['text'] for l in fa).split() == ' '.join(after).split()
    styles_b = {s for l in fb for s in map(tuple, l['styles'])}
    checks['styles_unchanged'] = {s for l in fa for s in map(tuple, l['styles'])} <= styles_b
    lefts = {column_of(doc, l): {x['x0'] for x in fb if column_of(doc, x) == column_of(doc, l)} | {doc['columns'][column_of(doc, l)]['x0']} for l in fa}
    checks['left_edges_observed'] = all(any(abs(l['x0'] - x) < .2 for x in lefts[column_of(doc, l)]) for l in fa)
    pitch = sorted({round(b['baseline'] - a['baseline'], 2) for a, b, p, q in zip(fb, fb[1:], pb or [], (pb or [])[1:])
                    if p == q and column_of(doc, a) == column_of(doc, b)})
    gaps = sorted({round(b['baseline'] - a['baseline'], 2) for a, b, p, q in zip(fb, fb[1:], pb or [], (pb or [])[1:])
                   if p != q and column_of(doc, a) == column_of(doc, b)})
    steps = [(round(b['baseline'] - a['baseline'], 2), p == q) for a, b, p, q in zip(fa, fa[1:], pa or [], (pa or [])[1:])
             if column_of(doc, a) == column_of(doc, b)]
    details.update(source_pitch=pitch, source_gaps=gaps, output_steps=sorted(set(steps)))
    checks['line_pitch_and_paragraph_gaps'] = bool(pitch) and all(
        any(abs(s - x) <= .05 for x in (pitch if same else gaps or pitch)) for s, same in steps)
    tops = {k: min(l['baseline'] for l in fb if column_of(doc, l) == k) for k in {column_of(doc, l) for l in fb}}
    checks['column_tops_kept'] = all(abs(min(l['baseline'] for l in fa if column_of(doc, l) == k) - y) < .05
                                     for k, y in tops.items() if any(column_of(doc, l) == k for l in fa))
    checks['kinsoku'] = all(l['text'][0] not in KINSOKU_START and l['text'][-1] not in KINSOKU_END for l in fa)
    texts_b = {renderer: bare(t) for renderer, t in extract(str(source), page, folder / 'text-before').items() if renderer != 'poppler_stderr'}
    texts_a = {renderer: bare(t) for renderer, t in extract(str(output), page, folder / 'text-after').items() if renderer != 'poppler_stderr'}
    for renderer in ('mupdf', 'poppler'):
        flow_b = bare(''.join(before))
        checks[f'extraction_{renderer}'] = flow_b in texts_b[renderer] and texts_a[renderer] == texts_b[renderer].replace(flow_b, bare(expected))
    with pymupdf.open(output) as d:
        p = d[page - 1]
        target_lines = [l['text'] for l, k in zip(fa, pa or []) if k == target]
        checks['search_each_target_line_mupdf'] = bool(target_lines) and all(p.search_for(t) for t in target_lines)
    if tagged:
        checks['tagged_paragraph_text'] = tagged_paragraphs(output, page) == [bare(x) for x in after]
    quality = [k for k in checks if k not in safety]
    return dict(checks=checks, details=details, safety=all(checks[k] for k in safety), quality=all(checks[k] for k in quality),
                failed=[k for k, v in checks.items() if not v])


def tagged_paragraphs(path, page):
    """Drawn text of each body /P (in structure order) through its MCIDs, whitespace removed."""
    s = semantic(str(path), page)
    drawn = {r['mcid']: r['drawn'] for r in s['rows'] if r['mcid'] is not None}

    def mcids(node):
        if isinstance(node, list):
            return [m for x in node for m in mcids(x)]
        if isinstance(node, dict):
            return [node['mcid']] if 'mcid' in node else mcids(node.get('/K', []))
        return []
    result = []

    def walk(node):
        if isinstance(node, list):
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            if node.get('/S') in ('/P', '/Standard'):
                result.append(bare(''.join(drawn.get(m, '') for m in mcids(node.get('/K', [])))))
            else:
                walk(node.get('/K', []))
    walk(s['tree'])
    return result[1:]  # the title paragraph is outside the flow


def reopen(output, page, new, *, flow=True):
    """Continued editing: a proposal from the saved PDF alone uses the stored approved flow
    (or, for a single-column edit, the stored paragraph conditions)."""
    try:
        p = propose(str(output), page, new[-12:], new[-12:])
    except Refused as exc:
        return dict(ok=False, reason=str(exc))
    found = p['evidence'].get('column_flow', {})
    applied = p['evidence']['conditions']['status'] == 'applied'
    ok = applied and (found.get('mode') == 'flow' and found.get('stored') is True if flow else found.get('mode') == 'single-column')
    return dict(ok=ok, mode=found.get('mode'), stored=found.get('stored'), status=p['evidence']['conditions']['status'])


def phase_fixtures(root, manifest):
    out = root / 'fixtures'
    run = subprocess.run([sys.executable, '-m', 'prototypes.paragraph_frame.column_fixtures', '--out', str(out)], capture_output=True, check=True)
    generated = json.loads(run.stdout)
    result = {}
    for name, doc in manifest['documents'].items():
        if doc['kind'] != 'generated':
            continue
        same = witness(out / f'{name}.pdf', 1) == doc['witness']
        result[name] = dict(sha256=generated['documents'][name]['sha256'], sha256_at_freeze=doc['sha256_at_freeze'], witness_matches=same,
                            libreoffice=generated['libreoffice'], libreoffice_at_freeze=doc['libreoffice'])
    dump(root / 'fixtures.json', result)
    print(json.dumps({k: v['witness_matches'] for k, v in result.items()}))
    if not all(v['witness_matches'] for v in result.values()):
        raise SystemExit('regenerated fixture differs from the frozen witness')
    return result


def phase_sequences(root, manifest, fusei):
    results = {}
    for n, chain in enumerate(manifest['sequences']):
        name = chain['document']
        doc = manifest['documents'][name]
        folder = root / 'sequences' / f'S{n + 1}-{name}'
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        omissions = undrawn(doc)
        current, texts, steps = source_path(root, manifest, name, fusei), as_drawn(doc['paragraphs'], omissions), []
        for k, step in enumerate(chain['steps']):
            output = folder / f'step{k + 1}.pdf'
            mapped = [engine_link(current, 1, doc, ident) for ident in step['approve_continuation']]
            result = edit(current, 1, step['old'], step['new'], output, [m for m in mapped if m])
            record = dict(step=k + 1, purpose=step['purpose'], expected_outcome=step['expected_outcome'], approve_continuation=step['approve_continuation'],
                          engine_continuation_ids=mapped, outcome=result['outcome'], seconds=round(result['seconds'], 2))
            if result['outcome'] == 'refused':
                record.update(reason=result['reason'], published=result['published'],
                              candidates=[l['id'] for l in links_of(current, 1)['links'] if l['status'] == 'candidate'])
                record['as_expected'] = step['expected_outcome'] == 'refused' and not result['published']
                steps.append(record)
                continue
            proposal = result['proposal']
            flow = proposal['evidence'].get('column_flow', {})
            expected = as_drawn(step['expected_flow_text'], omissions)
            audit = audit_step(root, doc, current, output, 1, texts, expected, step['paragraph'], tagged=doc['tagged'], folder=folder / f'step{k + 1}')
            record.update(mode=flow.get('mode'), stored_flow_used=flow.get('stored'), links=[(l['id'], l['status']) for l in flow.get('links', [])],
                          moved_paragraphs=len(proposal.get('moved_paragraphs') or []), moved_lines=sum(len(m['lines']) for m in proposal.get('moved_paragraphs') or []),
                          lines_changing_column=sum(1 for m in proposal.get('moved_paragraphs') or [] for l in m['lines'] if l['region'] != l['to_region']),
                          stored=result['save']['stored_conditions'], pdf_sha256=result['pdf_sha256'], audit=audit)
            record['continued_editing'] = reopen(output, 1, step['new'])
            record['as_expected'] = step['expected_outcome'] == 'flow' and flow.get('mode') == 'flow'
            dump(folder / f'step{k + 1}.audit.json', audit)
            steps.append(record)
            current, texts = output, expected
        results[f'S{n + 1}'] = dict(document=name, kind=doc['kind'], tagged=doc['tagged'], undrawn_in_source=omissions, steps=steps)
        print(f'S{n + 1}', name, [(s['outcome'], s.get('audit', {}).get('failed')) for s in steps])
    dump(root / 'sequences.json', results)
    return results


def phase_singles(root, manifest, fusei):
    results = {}
    for case in manifest['singles']:
        doc = manifest['documents'][case['document']]
        folder = root / 'singles' / case['id']
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        source = source_path(root, manifest, case['document'], fusei)
        output = folder / 'out.pdf'
        mapped = [engine_link(source, doc['page'], doc, ident) for ident in case.get('approve_continuation', [])]
        result = edit(source, doc['page'], case['old'], case['new'], output, [m for m in mapped if m])
        record = dict(id=case['id'], document=case['document'], kind=doc['kind'], purpose=case['purpose'], expected_outcome=case['expected_outcome'],
                      outcome=result['outcome'], seconds=round(result['seconds'], 2),
                      approve_continuation=case.get('approve_continuation', []), engine_continuation_ids=mapped)
        if result['outcome'] == 'refused':
            record.update(reason=result['reason'], published=result['published'])
            record['as_expected'] = (case['expected_outcome'] == 'refused' and not result['published']
                                     and case.get('expected_reason', '') in result['reason'])
        else:
            flow = result['proposal']['evidence'].get('column_flow', {})
            record.update(mode=flow.get('mode'), links=[(l['id'], l['status'], l.get('reason')) for l in flow.get('links', [])])
            if doc['kind'] == 'generated':
                paragraphs = as_drawn(doc['paragraphs'], undrawn(doc))
                after = paragraphs[:case['paragraph']] + [paragraphs[case['paragraph']].replace(case['old'], case['new'])] + paragraphs[case['paragraph'] + 1:]
                record['audit'] = audit_step(root, doc, source, output, 1, paragraphs, after, case['paragraph'], tagged=doc['tagged'], folder=folder)
                moved = result['proposal'].get('moved_paragraphs') or []
                record['moved_paragraphs'] = len(moved)
                record['as_expected'] = case['expected_outcome'] == 'saved-in-column' and not moved
            else:
                record['audit'] = audit_real_fit(doc, source, output, case, folder)
                record['as_expected'] = case['expected_outcome'] == 'saved-in-column' and flow.get('mode') == 'single-column'
        if record['outcome'] == 'saved':
            record['continued_editing'] = reopen(output, doc['page'], case['new'], flow=record['mode'] == 'flow')
        results[case['id']] = record
        print(case['id'], record['outcome'], record.get('reason') or record.get('audit', {}).get('failed'))
    dump(root / 'singles.json', results)
    return results


def audit_real_fit(doc, source, output, case, folder):
    """Real in-column edit: only the edited column may change (oracle: observed column of the edit)."""
    page = doc['page']
    lb, la = drawn_lines(source, page), drawn_lines(output, page)
    col = next(c for c in doc['columns'] if any(case['old'].split()[0] in l['text'] and c['x0'] - 1 <= l['x0'] < c['x1'] for l in lb))
    inside = lambda l: col['x0'] - 1 <= l['x0'] < col['x1']
    checks = dict(other_columns_identical=[l for l in lb if not inside(l)] == [l for l in la if not inside(l)])
    raster = pixels(str(source), str(output), folder / 'pixels', dict(page=page, frame=col['allowed']))
    outside = {k: sum(v['outside_pixels'] for v in rows) for k, rows in raster.items()}
    checks.update(raster_outside_column_mupdf=outside['mupdf'] == 0, raster_outside_column_poppler=outside['poppler'] == 0)
    pb, pa = painting(str(source), page), painting(str(output), page)
    checks['non_text_paint_identical'] = all(pb[k] == pa[k] for k in ('drawings', 'images', 'links', 'annots'))
    texts = extract(str(output), page, folder / 'text')
    for renderer in ('mupdf', 'poppler'):
        checks[f'extraction_{renderer}'] = bare(case['new']) in bare(texts[renderer])
    safety = ['other_columns_identical', 'raster_outside_column_mupdf', 'raster_outside_column_poppler', 'non_text_paint_identical']
    return dict(checks=checks, details=dict(outside_pixels=outside), safety=all(checks[k] for k in safety),
                quality=all(v for k, v in checks.items() if k not in safety), failed=[k for k, v in checks.items() if not v])


def oracle_columns(doc):
    """Body columns of a real page from the frozen witness (x ranges of the most common size)."""
    if doc.get('columns'):
        return [(c['x0'], c['x1']) for c in doc['columns']]
    sizes = {}
    for l in doc['witness']:
        sizes[l['size']] = sizes.get(l['size'], 0) + 1
    size = max(sizes, key=sizes.get)
    xs = sorted(l['x0'] for l in doc['witness'] if l['size'] == size)
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] > 3 * size:
            clusters.append([x])
        else:
            clusters[-1].append(x)
    clusters = [c for c in clusters if len(c) >= 5]
    return [(min(c), max(l['x1'] for l in doc['witness'] if l['size'] == size and min(c) - 1 <= l['x0'] <= max(c) + 1)) for c in clusters]


def phase_recognition(root, manifest, fusei):
    results = {}
    for name, expected in manifest['recognition'].items():
        doc = manifest['documents'][name]
        source = source_path(root, manifest, name, fusei)
        found = recognize(str(source), doc['page'])
        cols = oracle_columns(doc)

        def col(x):
            return next((k for k, (x0, x1) in enumerate(cols) if x0 - 6 <= x < x1), None)
        pairs = []
        for e in expected:
            hits = [l for l in found['links'] if col(l['from_x'][0]) == e['from_column'] and col(l['to_x'][0]) == e['to_column']]
            statuses = sorted({'determined' if l['status'] in ('determined', 'approved') else l['status'] for l in hits})
            got = 'determined' if 'determined' in statuses else 'candidate' if 'candidate' in statuses else 'not-linked'
            pairs.append(dict(e, got=got, links=[(l['id'], l['status'], l.get('reason') or l.get('evidence')) for l in hits], correct=got == e['expected']))
        wrong = [l for l in found['links'] if l['status'] in ('determined', 'candidate') and not any(
            col(l['from_x'][0]) == e['from_column'] and col(l['to_x'][0]) == e['to_column'] and e['expected'] != 'not-linked' for e in expected)]
        results[name] = dict(kind=doc['kind'], pairs=pairs, unexpected_links=[(l['id'], l['status']) for l in wrong],
                             links_examined=len(found['links']), rejected=sum(l['status'] == 'rejected' for l in found['links']))
        print(name, [(p['expected'], p['got']) for p in pairs], 'unexpected', results[name]['unexpected_links'])
    dump(root / 'recognition.json', results)
    return results


def phase_survey(root, manifest, fusei):
    """Recognition on every page of the reachable real corpus: links that would enable a flow."""
    sources = [Path(s['path']) for s in load('evaluations/sources.json') if Path(s['path']).exists()] + [Path(fusei)]
    result = {}
    for path in sources:
        with pymupdf.open(path) as d:
            count = len(d)
        pages = {}
        for page in range(1, count + 1):
            try:
                found = recognize(str(path), page)
            except Exception as exc:  # unreadable pages are reported, not skipped silently
                pages[page] = dict(error=type(exc).__name__)
                continue
            pages[page] = dict(links=[dict(id=l['id'], status=l['status'], reason=l.get('reason'), evidence=l.get('evidence'), from_x=l['from_x'],
                                           to_x=l['to_x'], next_text=l['next_text']) for l in found['links']])
        result[path.name] = dict(sha256=sha(path), pages=pages)
        statuses = [l['status'] for p in pages.values() for l in p.get('links', [])]
        print(path.name, count, {s: statuses.count(s) for s in set(statuses)})
    dump(root / 'survey.json', result)
    return result


def phase_collect(root, manifest, regression, tests):
    sequences, singles = load(root / 'sequences.json'), load(root / 'singles.json')
    recognition, survey, fixtures_ = load(root / 'recognition.json'), load(root / 'survey.json'), load(root / 'fixtures.json')

    def tiers(step):
        audit = step.get('audit')
        saved = step['outcome'] == 'saved'
        return dict(saved=saved, safety=bool(audit and audit['safety']) if saved else None, quality=bool(audit and audit['quality']) if saved else None,
                    continued_editing=step.get('continued_editing', {}).get('ok') if saved and 'continued_editing' in step else None)
    compact_sequences = {}
    for k, chain in sequences.items():
        compact_sequences[k] = dict(document=chain['document'], kind=chain['kind'], tagged=chain['tagged'], steps=[
            dict(step=s['step'], purpose=s['purpose'], expected=s['expected_outcome'], outcome=s['outcome'], as_expected=s['as_expected'],
                 **({'reason': s['reason'], 'published': s['published'], 'candidates': s['candidates']} if s['outcome'] == 'refused' else
                    dict(mode=s['mode'], stored_flow_used=s['stored_flow_used'], links=s['links'], moved_paragraphs=s['moved_paragraphs'],
                         moved_lines=s['moved_lines'], lines_changing_column=s['lines_changing_column'], failed=s['audit']['failed'], outside_pixels=s['audit']['details']['outside_pixels'],
                         continued_editing=s['continued_editing'])),
                 tiers=tiers(s), approve_continuation=s['approve_continuation'], engine_continuation_ids=s['engine_continuation_ids'], seconds=s['seconds'])
            for s in chain['steps']])
    compact_singles = {k: dict(document=v['document'], kind=v['kind'], purpose=v['purpose'], expected=v['expected_outcome'], outcome=v['outcome'],
                               as_expected=v['as_expected'], **({'reason': v['reason'], 'published': v['published']} if v['outcome'] == 'refused' else
                                                                dict(mode=v['mode'], links=v['links'], failed=v['audit']['failed'],
                                                                     outside_pixels=v['audit']['details']['outside_pixels'], continued_editing=v['continued_editing'])),
                               tiers=tiers(v), seconds=v['seconds'], approve_continuation=v['approve_continuation'],
                               engine_continuation_ids=v['engine_continuation_ids']) for k, v in singles.items()}
    statuses = [l['status'] for doc in survey.values() for p in doc['pages'].values() for l in p.get('links', [])]
    flagged = [dict(file=name, page=page, **l) for name, doc in survey.items() for page, p in doc['pages'].items() for l in p.get('links', [])
               if l['status'] in ('determined', 'candidate')]
    steps = [s for chain in compact_sequences.values() for s in chain['steps']] + list(compact_singles.values())

    def count(kind, tier):
        rows = [s for s in steps if s['tiers']['saved'] and (kind is None or kind_of(s, compact_sequences, compact_singles) == kind)]
        return sum(1 for s in rows if s['tiers'][tier]), len(rows)
    result = dict(
        schema='paragraph-frame-column-flow-results-1', manifest_sha256=sha(MANIFEST), manifest_revision=manifest.get('revision', 1),
        tests=tests, regression=regression,
        fixtures=fixtures_, sequences=compact_sequences, singles=compact_singles,
        recognition=dict(pairs={k: [(p['from_column'], p['to_column'], p['expected'], p['got'], p['correct']) for p in v['pairs']] for k, v in recognition.items()},
                         unexpected_links={k: v['unexpected_links'] for k, v in recognition.items() if v['unexpected_links']},
                         correct=sum(p['correct'] for v in recognition.values() for p in v['pairs']), total=sum(len(v['pairs']) for v in recognition.values())),
        survey=dict(files=len(survey), pages=sum(len(d['pages']) for d in survey.values()),
                    errors=sum(1 for d in survey.values() for p in d['pages'].values() if 'error' in p),
                    links={s: statuses.count(s) for s in sorted(set(statuses))}, determined_or_candidate=flagged,
                    rejection_reasons=sorted({(l['reason']) for d in survey.values() for p in d['pages'].values() for l in p.get('links', []) if l.get('reason')})),
        summary=dict(steps=len(steps), as_expected=sum(1 for s in steps if s['as_expected']),
                     saved=sum(1 for s in steps if s['tiers']['saved']),
                     refused=sum(1 for s in steps if s['outcome'] == 'refused'),
                     tiers={tier: dict(zip(('passed', 'of'), count(None, tier))) for tier in ('safety', 'quality', 'continued_editing')},
                     generated_only={tier: dict(zip(('passed', 'of'), count('generated', tier))) for tier in ('safety', 'quality', 'continued_editing')},
                     real={tier: dict(zip(('passed', 'of'), count('real', tier))) for tier in ('safety', 'quality', 'continued_editing')}))
    dump(RESULTS, result)
    print(json.dumps(result['summary'], ensure_ascii=False, indent=1))
    return result


def kind_of(step, sequences, singles):
    for chain in sequences.values():
        if step in chain['steps']:
            return chain['kind']
    return next(v['kind'] for v in singles.values() if v is step)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--fusei', type=Path, required=True)
    parser.add_argument('--phase', required=True, choices=['fixtures', 'sequences', 'singles', 'recognition', 'survey', 'collect'])
    parser.add_argument('--regression-json', type=Path)
    parser.add_argument('--tests-json', type=Path)
    args = parser.parse_args()
    manifest = load(MANIFEST)
    if args.fusei.exists() and sha(args.fusei) != manifest['documents']['osaka_fusei_477']['sha256']:
        raise SystemExit('fusei 477 hash differs from the manifest')
    args.root.mkdir(parents=True, exist_ok=True)
    if args.phase == 'fixtures':
        phase_fixtures(args.root, manifest)
    elif args.phase == 'sequences':
        phase_sequences(args.root, manifest, args.fusei)
    elif args.phase == 'singles':
        phase_singles(args.root, manifest, args.fusei)
    elif args.phase == 'recognition':
        phase_recognition(args.root, manifest, args.fusei)
    elif args.phase == 'survey':
        phase_survey(args.root, manifest, args.fusei)
    else:
        phase_collect(args.root, manifest, load(args.regression_json) if args.regression_json else None,
                      load(args.tests_json) if args.tests_json else None)


if __name__ == '__main__':
    main()
