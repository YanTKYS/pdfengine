"""InDesign-style text support evaluation against the frozen manifest.

  .venv/bin/python -m prototypes.paragraph_frame.indesign_evaluate --root tmp/<new> --fusei <477.pdf> --pdfjs <dir> \
      --phase requests|samples|collect [--survey tmp/id-survey]

``requests`` runs every manifest request in its own process from the PDF of
the previous step only, then audits it with PyMuPDF/Poppler against the
manifest oracle (allowed region, drawn paragraph lines). ``samples`` saves an
unchanged-text edit of paragraphs that main refused and the change proposes
(survey results), and audits the same way. ``collect`` writes the compact
results. Generated PDFs, rasters and detailed audits stay under ``--root``.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import pymupdf
from pypdf import PdfReader
from .audit import extract, painting, pixels
from .auto import propose
from .engine import Refused

MANIFEST = Path('evaluations/paragraph_frame_indesign/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'
CLASSES = [('transform', r'text matrix|rotated|mirrored|non-axis'),
           ('form', r'Form XObject|invocation'),
           ('encoding', r'Unicode|ligature|ToUnicode|Identity-H|Widths|provenance|witness|one-code|occurrence not found|code mapping|line-join space|positional gap'),
           ('glyphs', r'missing source glyph|substitute'),
           ('marked-content', r'marked-content|scope|semantic|ActualText|span adapter|unbound marked'),
           ('paint', r'non-fill|transparent|source color|horizontal scale|text rise'),
           ('layout', r'capacity|paragraph|frame|hyphen|column|insertion|clip|graphics state|collides|paint order|reading order|line break')]


def dump(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bare(text):
    return re.sub(r'\s+', '', text)


def classify(reason):
    for name, pattern in CLASSES:
        if re.search(pattern, reason or ''):
            return name
    return 'other'


def source_of(name, args):
    return args.fusei if name == 'osaka_fusei_477' else args.pdfjs / f'{name}.pdf'


def edit(source, page, old, new, output):
    args = [sys.executable, '-m', 'prototypes.paragraph_frame.generalization_cli', 'reedit', '--source', str(source), '--page', str(page),
            '--old', old, '--new', new, '--output', str(output), '--acknowledge-inference']
    started = time.perf_counter()
    run = subprocess.run(args, capture_output=True)
    seconds = round(time.perf_counter() - started, 2)
    if run.returncode:
        last = [l for l in run.stderr.decode().splitlines() if l.strip()]
        return dict(outcome='refused', reason=last[-1] if last else 'unknown', seconds=seconds, published=Path(output).exists())
    return dict(outcome='saved', proposal=load(Path(output).with_suffix('.proposal.json')), save=load(Path(output).with_suffix('.save.json')),
                seconds=seconds, pdf_sha256=sha(output))


def chars(path, page):
    """Drawn glyphs: (char, origin, size, font, colour) and bbox (PyMuPDF rawdict)."""
    with pymupdf.open(path) as d:
        result = []
        for b in d[page - 1].get_text('rawdict')['blocks']:
            for line in b.get('lines', []):
                for s in line['spans']:
                    for ch in s['chars']:
                        result.append(dict(c=ch['c'], origin=(round(ch['origin'][0], 1), round(ch['origin'][1], 1)), size=round(s['size'], 2),
                                           font=s['font'], color=s['color'], bbox=ch['bbox']))
        return result


def inside(bbox, region):
    x, y = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def programs(path, skip_page):
    """Digests of every page program and Form XObject reachable from pages other than ``skip_page``."""
    r = PdfReader(path)
    result = {}
    for n, page in enumerate(r.pages):
        if n == skip_page - 1:
            continue
        contents = page.get_contents()
        result[(n, ())] = hashlib.sha256(contents.get_data() if contents is not None else b'').hexdigest()
        stack = [((), page['/Resources'].get_object(), 0)]
        while stack:
            prefix, resources, depth = stack.pop()
            for name, ref in (resources.get('/XObject') or {}).items():
                form = ref.get_object()
                if form.get('/Subtype') == '/Form' and depth < 8:
                    path_ = prefix + (str(name),)
                    result[(n, path_)] = hashlib.sha256(form.get_data()).hexdigest()
                    stack.append((path_, form.get('/Resources', resources).get_object(), depth + 1))
    return result


def audit(source, output, page, region, old, new, folder, *, lines=None):
    """Safety and quality of one saved edit; oracle: allowed region and the replacement."""
    checks, details = {}, {}
    raster = pixels(str(source), str(output), folder / 'pixels', dict(page=page, frame=region))
    details['outside_pixels'] = {k: sum(v['outside_pixels'] for v in rows) for k, rows in raster.items()}
    checks['raster_outside_region_mupdf'] = details['outside_pixels']['mupdf'] == 0
    checks['raster_outside_region_poppler'] = details['outside_pixels']['poppler'] == 0
    before, after = chars(source, page), chars(output, page)
    keep = lambda glyphs: sorted((g['c'], g['origin'], g['size'], g['font'], g['color']) for g in glyphs if not inside(g['bbox'], region))
    checks['glyphs_outside_region_identical'] = keep(before) == keep(after)
    pb, pa = painting(str(source), page), painting(str(output), page)
    checks['non_text_paint_identical'] = all(pb[k] == pa[k] for k in ('drawings', 'images', 'links', 'annots'))
    checks['original_font_programs_kept'] = all(pa['fonts'].get(k) == v for k, v in pb['fonts'].items())
    checks['other_pages_and_their_forms_unchanged'] = programs(source, page) == programs(output, page)
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        checks['page_count'] = len(a) == len(b)
    safety = list(checks)
    texts_b = extract(str(source), page, folder / 'text-before')
    texts_a = extract(str(output), page, folder / 'text-after')
    for renderer in ('mupdf', 'poppler'):
        tb, ta = bare(texts_b[renderer]), bare(texts_a[renderer])
        checks[f'extraction_{renderer}'] = bare(old) in tb and ta == tb.replace(bare(old), bare(new), 1)
    with pymupdf.open(output) as d:
        hits = d[page - 1].search_for(new)
        details['search_hits'] = len(hits)
        drawn = [''.join(s['text'] for s in l['spans']) for b in d[page - 1].get_text('dict')['blocks'] for l in b.get('lines', [])]
    crosses = not any(new in l for l in drawn)
    checks['search_mupdf'] = bool(hits) or crosses
    details['search_crosses_reader_line'] = crosses
    styles_b = {(g['font'], g['size'], g['color']) for g in before if inside(g['bbox'], region) and g['c'].strip()}
    styles_a = {(g['font'], g['size'], g['color']) for g in after if inside(g['bbox'], region) and g['c'].strip()}
    details['styles'] = dict(before=sorted(map(str, styles_b)), after=sorted(map(str, styles_a)))
    checks['size_and_colour_kept'] = {(s, c) for _, s, c in styles_a} <= {(s, c) for _, s, c in styles_b}
    if lines:
        baselines = sorted({round(l['baseline'], 1) for l in lines})
        pitch = min((b - a for a, b in zip(baselines, baselines[1:])), default=None)
        out_lines = sorted({g['origin'][1] for g in after if inside(g['bbox'], region) and g['c'].strip()})
        allowed = baselines + ([round(baselines[-1] + pitch, 1)] if pitch else [])
        checks['baselines_on_the_paragraph_grid'] = all(any(abs(y - b) <= .2 for b in allowed) for y in out_lines)
        lefts = sorted({round(min(g['origin'][0] for g in after if abs(g['origin'][1] - y) < .2 and inside(g['bbox'], region)), 1) for y in out_lines})
        src_lefts = {round(min(g['origin'][0] for g in before if abs(g['origin'][1] - y) < .2 and inside(g['bbox'], region)), 1)
                     for y in {g['origin'][1] for g in before if inside(g['bbox'], region) and g['c'].strip()}}
        checks['left_edges_observed'] = all(any(abs(x - s) <= .3 for s in src_lefts) for x in lefts)
        details.update(baselines_after=out_lines, lefts_after=lefts)
    quality = [k for k in checks if k not in safety]
    return dict(checks=checks, details=details, safety=all(checks[k] for k in safety), quality=all(checks[k] for k in quality),
                failed=[k for k, v in checks.items() if not v])


def reopen(output, page, new):
    try:
        p = propose(str(output), page, new, new)
        return dict(ok=p['evidence']['conditions']['status'] == 'applied', status=p['evidence']['conditions']['status'])
    except Refused as exc:
        return dict(ok=False, reason=str(exc))


def phase_requests(root, manifest, args):
    results, outputs = {}, {}
    for r in manifest['requests']:
        folder = root / 'requests' / r['id']
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / 'out.pdf'
        if output.exists():
            output.unlink()
        source = outputs[r['after']] if r['after'] else source_of(r['document'], args)
        result = edit(source, r['page'], r['old'], r['new'], output)
        record = dict(id=r['id'], document=r['document'], kind=r['kind'], purpose=r['purpose'], expected=r['expected_outcome'],
                      expected_reason=r['expected_reason'], outcome=result['outcome'], seconds=result['seconds'], after=r['after'])
        if result['outcome'] == 'refused':
            record.update(reason=result['reason'], reason_class=classify(result['reason']), published=result['published'])
        else:
            save = result['save']
            record.update(drawn_in=save.get('drawn_in'), form_clones=save.get('form_clones'), insertion_ctm=save.get('insertion_ctm'),
                          lines=[l['text'] for l in result['proposal']['lines']], font_counts=result['proposal']['font_counts'],
                          source_styles=[dict(size=round(s['size'], 3), horizontal_scale=round(s['horizontal_scale'], 2), resource=s['resource'])
                                         for s in result['proposal']['source_styles']],
                          timing={k: round(v, 2) for k, v in save['timing'].items()})
            record['audit'] = audit(source, output, r['page'], r['allowed_region'], r['old'], r['new'], folder, lines=r['lines'])
            record['continued_editing'] = reopen(output, r['page'], r['new'])
            outputs[r['id']] = output
        record['as_expected'] = record['outcome'] == r['expected_outcome']
        results[r['id']] = record
        print(r['id'], record['outcome'], record.get('reason') or record.get('audit', {}).get('failed'), flush=True)
    dump(root / 'requests.json', results)


def survey_table(folder):
    """Per paragraph: outcome on main and on the change (paired by page and selection)."""
    rows = []
    for new in sorted(Path(folder).glob('new-*.json')):
        main = new.with_name('main-' + new.name[4:])
        if not main.exists():
            continue
        doc, page = new.stem[4:].rsplit('-', 1)
        before = {(r.get('selection') or r['text']): r for r in load(main)}
        for r in load(new):
            key = r.get('selection') or r['text']
            b = before.get(key)
            rows.append(dict(document=doc, page=int(page), selection=key, bbox=r['bbox'], main=b['outcome'] if b else 'absent',
                             main_reason=b.get('reason') if b else None, new=r['outcome'], new_reason=r.get('reason'),
                             main_class=classify(b.get('reason')) if b and b['outcome'] == 'refused' else None,
                             new_class=classify(r.get('reason')) if r['outcome'] == 'refused' else None))
    return rows


def phase_samples(root, manifest, args):
    rows = survey_table(args.survey)
    newly = [r for r in rows if r['main'] == 'refused' and r['new'] == 'proposed']
    sample = [r for r in newly if r['document'] == 'osaka_fusei_477' and r['page'] == 7]
    for doc in sorted({r['document'] for r in newly} - {'osaka_fusei_477'}):
        sample += [r for r in newly if r['document'] == doc][:5]
    results = []
    for n, r in enumerate(sample):
        folder = root / 'samples' / f'{n:03d}'
        folder.mkdir(parents=True, exist_ok=True)
        output = folder / 'out.pdf'
        if output.exists():
            output.unlink()
        source = source_of(r['document'], args) if r['document'] in manifest['documents'] else Path('evaluations/realpdf/corpus') / f"{r['document']}.pdf"
        result = edit(source, r['page'], r['selection'], r['selection'], output)
        record = dict(document=r['document'], page=r['page'], selection=r['selection'], main_reason=r['main_reason'], outcome=result['outcome'],
                      seconds=result['seconds'])
        if result['outcome'] == 'refused':
            record.update(reason=result['reason'], reason_class=classify(result['reason']))
        else:
            x0, y0, x1, y1 = r['bbox']
            # Unchanged text may re-wrap inside the paragraph; nothing outside it may change.
            record['audit'] = audit(source, output, r['page'], [x0 - 3, y0 - 3, x1 + 3, y1 + 3], r['selection'], r['selection'], folder)
            record['drawn_in'] = result['save'].get('drawn_in')
        results.append(record)
        print(n, r['document'], r['page'], record['outcome'], record.get('reason') or record.get('audit', {}).get('failed'), flush=True)
    dump(root / 'samples.json', dict(newly=len(newly), sampled=len(sample), results=results))


def phase_collect(root, manifest, args):
    requests = load(root / 'requests.json')
    samples = load(root / 'samples.json')
    rows = survey_table(args.survey)
    surveyed = [r for r in rows if r['new'] != 'skipped']
    by_doc = {}
    for r in surveyed:
        d = by_doc.setdefault(r['document'], dict(paragraphs=0, main_proposed=0, new_proposed=0, newly=0, lost=0, main_refusals={}, new_refusals={}))
        d['paragraphs'] += 1
        d['main_proposed'] += r['main'] == 'proposed'
        d['new_proposed'] += r['new'] == 'proposed'
        d['newly'] += r['main'] == 'refused' and r['new'] == 'proposed'
        d['lost'] += r['main'] == 'proposed' and r['new'] != 'proposed'
        if r['main'] == 'refused':
            d['main_refusals'][r['main_class']] = d['main_refusals'].get(r['main_class'], 0) + 1
        if r['new'] == 'refused':
            d['new_refusals'][r['new_class']] = d['new_refusals'].get(r['new_class'], 0) + 1
    lost = [dict(document=r['document'], page=r['page'], selection=r['selection'], reason=r['new_reason']) for r in surveyed
            if r['main'] == 'proposed' and r['new'] != 'proposed']

    def tiers(r):
        saved = r['outcome'] == 'saved'
        return dict(saved=saved, safety=r['audit']['safety'] if saved else None, quality=r['audit']['quality'] if saved else None,
                    continued_editing=r.get('continued_editing', {}).get('ok') if saved else None)
    compact_requests = {k: dict({x: v[x] for x in ('document', 'kind', 'purpose', 'expected', 'expected_reason', 'outcome', 'as_expected', 'seconds', 'after')},
                                **({'reason': v['reason'], 'reason_class': v['reason_class'], 'published': v['published']} if v['outcome'] == 'refused' else
                                   dict(drawn_in=v['drawn_in'], form_clones=v['form_clones'], insertion_ctm=v['insertion_ctm'], lines=v['lines'],
                                        source_styles=v['source_styles'], failed=v['audit']['failed'], outside_pixels=v['audit']['details']['outside_pixels'],
                                        search_hits=v['audit']['details']['search_hits'], continued_editing=v['continued_editing'], timing=v['timing'])),
                                tiers=tiers(v)) for k, v in requests.items()}
    sample_saved = [s for s in samples['results'] if s['outcome'] == 'saved']
    result = dict(
        schema='paragraph-frame-indesign-results-1', manifest_sha256=sha(MANIFEST), tests=load(args.tests_json) if args.tests_json else None,
        regression=load(args.regression_json) if args.regression_json else None,
        requests=compact_requests,
        survey=dict(main_commit='f17055d', documents=by_doc,
                    totals=dict(paragraphs=len(surveyed), main_proposed=sum(r['main'] == 'proposed' for r in surveyed),
                                new_proposed=sum(r['new'] == 'proposed' for r in surveyed),
                                newly_proposable=sum(r['main'] == 'refused' and r['new'] == 'proposed' for r in surveyed), lost=len(lost)),
                    lost=lost),
        samples=dict(newly=samples['newly'], sampled=samples['sampled'], saved=len(sample_saved),
                     safety=sum(s['audit']['safety'] for s in sample_saved), quality=sum(s['audit']['quality'] for s in sample_saved),
                     refused=[dict(document=s['document'], page=s['page'], selection=s['selection'], reason=s['reason'], reason_class=classify(s['reason']))
                              for s in samples['results'] if s['outcome'] == 'refused'],
                     failed=[dict(document=s['document'], page=s['page'], selection=s['selection'], failed=s['audit']['failed'])
                             for s in sample_saved if s['audit']['failed']],
                     seconds=[s['seconds'] for s in samples['results']]))
    dump(RESULTS, result)
    print(json.dumps(result['survey']['totals']), json.dumps({k: v for k, v in result['samples'].items() if k not in ('seconds',)}, ensure_ascii=False)[:600])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--fusei', type=Path, required=True)
    parser.add_argument('--pdfjs', type=Path, required=True)
    parser.add_argument('--phase', required=True, choices=['requests', 'samples', 'collect'])
    parser.add_argument('--survey', type=Path, default=Path('tmp/id-survey'))
    parser.add_argument('--tests-json', type=Path)
    parser.add_argument('--regression-json', type=Path)
    args = parser.parse_args()
    manifest = load(MANIFEST)
    for name, doc in manifest['documents'].items():
        if sha(source_of(name, args)) != doc['sha256']:
            raise SystemExit(f'{name}: source hash differs from the manifest')
    args.root.mkdir(parents=True, exist_ok=True)
    dict(requests=phase_requests, samples=phase_samples, collect=phase_collect)[args.phase](args.root, manifest, args)


if __name__ == '__main__':
    main()
