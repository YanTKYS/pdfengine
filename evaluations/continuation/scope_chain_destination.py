"""Focused PR #17 external evidence on the unmodified LibreOffice page 10.

One depth-2 lifecycle, one active page-entry control, and two negatives.
Shared paragraph, provider, paint, resource and rendering gates remain in
scope_destination/evaluate; only scope-chain-specific evidence lives here.
Raw PDFs, glyphs, states and rasters are kept under runs/.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

from PIL import Image
import pymupdf
import pypdf
from pypdf import PdfReader

from pdfeditor.continuation import slot_id
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow, plan_shared_flow
from evaluations.continuation import evaluate as single, scope_destination as scope
from evaluations.continuation import scope_chain_candidates as candidates, scope_chain_checks as checks
from evaluations.continuation.scope_chain_region import source_region_review
from evaluations.continuation.resources import inventory
from evaluations.elements.evaluate import extraction
from evaluations.flow_transaction.evaluate import refuse
from evaluations.realpdf.evaluate import poppler_render, pixel_diff

STAGES = ('overflow', 'second', 'shorten', 'regrow', 'noop')
EXTRA = '確認した空き領域へ同じ文章の続きを配置し、再編集と保存後の文字位置を確認します。' * 2
DEPENDENCIES = scope.DEPENDENCIES + ['evaluations/continuation/scope_destination.py',
    'evaluations/continuation/scope_chain_candidates.py', 'evaluations/continuation/scope_chain_checks.py',
    'evaluations/continuation/scope_chain_region.py']


def engine_files():
    return {p.name: single.source_sha(p) for p in sorted((single.ROOT / 'pdfeditor').glob('*.py'))}


def start_record():
    engine = engine_files()
    providers, evidence = single.reviewed_providers()
    actual = {k: dict(v, sha256=single.source_sha(Path('C:/Windows/Fonts') / v['filename']))
              for k, v in providers.items()}
    if actual != providers or single.source_sha(single.SOURCE) != single.SOURCE_SHA:
        raise ValueError('source or reviewed providers differ')
    if sys.version_info[:2] != (3, 12) or sys.platform != 'win32':
        raise ValueError('this evidence requires the reviewed Windows / Python 3.12 environment')
    return dict(started=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        git_status=subprocess.check_output(['git', 'status', '--short', '--branch'], text=True).strip(),
        head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        engine_digest=hashlib.sha256(json.dumps(engine, sort_keys=True).encode()).hexdigest(), engine_sha256=engine,
        python=sys.version.split()[0], platform=platform.platform(), pymupdf=pymupdf.VersionBind,
        pypdf=pypdf.__version__, **single.tools(), source_sha256=single.source_sha(single.SOURCE),
        providers=actual, provider_evidence_source=evidence)


def edits_for(name, state, pid, logical):
    if name == 'noop':
        return {}
    change = dict(start=0, end=len(state['paragraphs'][pid]['logical']['text']))
    if name == 'shorten':
        change.update(text='確認。', style_id='body')
    else:
        change['runs'] = [*[dict(text=logical['text'][s['start']:s['end']], style_id=s['style_id'])
                           for s in logical['style_spans']],
                          dict(text=EXTRA.replace('再編集', '追加編集') if name == 'second' else EXTRA, style_id='body')]
    return {pid: dict(edits=[change])}


def planned_fields(report):
    return [[{k: g[k] for k in single.GLYPH_FIELDS} for g in step['report']['glyph_plan']]
            for step in report['steps']]


def save_stage(directory, name, pdf, state, initial, pid, destination, logical, chosen,
               original_text, text_parts, source_program, source_aliases, *, authority='boundary', previous=None):
    started = time.monotonic()
    edits = edits_for(name, state, pid, logical)
    print(name, authority, 'planning', flush=True)
    preview = plan_shared_flow(pdf, state, edits)
    single.write(directory / f'{name}-plan.json', preview)
    out, side = directory / f'{name}.pdf', directory / f'{name}.json'
    planned_at = time.monotonic()
    report = edit_shared_flow(pdf, state, out, side, edits)
    single.write(directory / f'{name}-report.json', report)
    saved_at = time.monotonic()
    print(name, authority, 'saved; checking reopen, state and renderers', flush=True)
    opened = open_shared_flow(out, side)
    if opened['status'] != 'restored':
        raise ValueError(opened['reason'])
    updated, sid = opened['state'], slot_id(destination)
    if report['plan'] != preview or sid not in updated['slots'] or len(updated['slots']) != 3:
        raise ValueError('planned execution or generated slot identity differs')
    if updated['contract_sha256'] != initial['contract_sha256'] or updated['continuation_destinations'] != initial['continuation_destinations']:
        raise ValueError('destination authority / confirmation changed')
    generated = updated['slots'][sid]
    if (generated['occupancy'] is None) != (name == 'shorten'):
        raise ValueError('generated slot did not become active/dormant')
    if set(report['plan']['new_slots']) != ({sid} if name == 'overflow' else set()):
        raise ValueError('generated slot was not reused')
    creation = generated['creation_binding']
    at = chosen['offset'] if authority == 'boundary' else 0
    if creation['mutation']['start'] != at or creation['mutation']['end'] != at:
        raise ValueError('creation witness does not name the selected insertion')
    if sid in state['slots'] and creation != state['slots'][sid]['creation_binding']:
        raise ValueError('creation witness changed')
    position = (checks.order(out, updated, destination, chosen, source_program) if authority == 'boundary'
                else scope.order(out, updated, destination, authority, source_program))
    geometry = checks.glyph_coordinates(out, updated, destination, report)
    nesting = single.operator_nesting(out, scope.EDITED)
    if single.pdf_header(out) != single.pdf_header(single.SOURCE):
        raise ValueError('PDF version changed')
    sizes = scope.resources(out, updated, destination, source_aliases)
    noop = name == 'noop'
    if noop:
        if (updated['paragraphs'] != state['paragraphs'] or updated['generated_fonts'] != state['generated_fonts']
                or any(single.stable_slot(s) != single.stable_slot(updated['slots'][i]) for i, s in state['slots'].items())
                or planned_fields(previous) != planned_fields(report)
                or any(set(v.values()) != {'reused'} for v in report['generated_font_outcome'].values())):
            raise ValueError('no-op changed allocation, glyphs, records or font reuse')
    proof = {}
    if authority == 'boundary':
        text = generated['binding']['paragraph']['text']
        proof = scope.audit(pdf, out, updated, initial, report, directory / (name + '-audit'), original_text, authority,
            noop=noop, owned=state.get('generated_fonts', {}), exact_bounds=True,
            expected_page_text={scope.PAGE: text_parts[0] + text + text_parts[1]})
    finished = time.monotonic()
    result = dict(stage=name, status='passed', restored=True, destination_authority_unchanged=True,
        creation_binding_unchanged=True, slot_id=sid, generated_lines=len(generated['binding']['physical_layout']['lines']),
        new_slots=sorted(report['plan']['new_slots']), insertion=position, glyph_coordinates=geometry,
        operator_nesting=nesting, pdf_header=single.pdf_header(out), **proof,
        resources=dict(sizes, font_outcome=report['generated_font_outcome']),
        seconds=dict(plan=round(planned_at-started, 2), edit=round(saved_at-planned_at, 2),
                     reopen_and_audit=round(finished-saved_at, 2), total=round(finished-started, 2)))
    single.write(directory / f'{name}-checks.json', result)
    print(name, authority, 'passed', result['seconds'], flush=True)
    return out, side, updated, report, result


def representative(directory, chosen, original_text, text_parts, source_program, source_aliases, boundary_report):
    """One overflow save only: compare it with the boundary overflow already audited."""
    started = time.monotonic()
    target = directory / 'page-entry'
    target.mkdir()
    state, pid, destination = scope.prepare('page-entry')
    initial = deepcopy(state)
    pdf, _, _, report, result = save_stage(target, 'overflow', single.SOURCE, state, initial, pid, destination,
        state['paragraphs'][pid]['logical'], chosen, original_text, text_parts, source_program, source_aliases,
        authority='page-entry')
    comparison = compare_representative(directory, boundary_report, report)
    return dict(stage='overflow', saves=1, same_page_bounds_paragraph_text=True,
        **comparison, checks=result, seconds=round(time.monotonic()-started, 2))


def compare_representative(directory, boundary_report, report):
    """Compare the two already-saved overflows; retain renderer disagreements as evidence."""
    target = directory / 'page-entry'
    pdf = target / 'overflow.pdf'
    if planned_fields(report) != planned_fields(boundary_report):
        raise ValueError('page-entry planned glyph fields differ')
    comparisons = {}
    with pymupdf.open(directory / 'overflow.pdf') as a, pymupdf.open(pdf) as b:
        for page in map(int, scope.EDITED):
            pa, pb = a[page-1], b[page-1]
            if pa.get_pixmap(dpi=144, alpha=False).samples != pb.get_pixmap(dpi=144, alpha=False).samples:
                raise ValueError('page-entry MuPDF pixels differ')
            # Region traces include Unicode, GID, saved origin and glyph box.
            trace = lambda p: [(c[0], c[1], c[2], c[3]) for span in p.get_texttrace() for c in span['chars']
                              if pymupdf.Rect(scope.REGION['bounds']).contains(pymupdf.Point(c[2]))]
            if page == scope.PAGE and trace(pa) != trace(pb):
                raise ValueError('page-entry saved glyph origins differ')
            png = target / f'page-{page}.png'
            # Reusing these run artifacts allows reporting a comparison failure
            # without executing any save or lifecycle a second time.
            if not png.exists():
                rendered = poppler_render(single.renderer_paths.DEFAULT_POPPLER, pdf, page, png, dpi=144)
                if not rendered.get('rendered'):
                    raise ValueError('page-entry Poppler failed')
            twin = directory / f'overflow-audit/page-{page}/after.png'
            with Image.open(twin) as x, Image.open(png) as y:
                difference = pixel_diff(x, y, mask=scope.REGION['bounds'], dpi=144)
                equal = x.size == y.size and x.convert('RGB').tobytes() == y.convert('RGB').tobytes()
            comparisons[str(page)] = dict(mupdf_pixels_equal=True, poppler_pixels_equal=equal, poppler_diff=difference,
                                         poppler_png_bytes_equal=twin.read_bytes() == png.read_bytes())
    return dict(status='passed' if all(p['poppler_pixels_equal'] for p in comparisons.values()) else 'failed',
        planned_glyph_fields_equal=list(single.GLYPH_FIELDS), saved_page10_glyph_origins_equal=True,
        pages=comparisons)


def run(directory):
    started = time.monotonic()
    environment = json.loads((directory / 'start.json').read_text(encoding='utf-8'))
    if engine_files() != environment['engine_sha256']:
        raise ValueError('engine changed since the recorded start')
    chosen, inspected = candidates.inspect_and_select(directory)
    region = source_region_review(directory, chosen)
    text_parts = candidates.unicode_parts(directory)
    state, pid, destination = scope.prepare('boundary', boundary_id=chosen['boundary_id'])
    initial = deepcopy(state)
    if single.provider_evidence(state['paragraphs'][pid]['style_registry']) != environment['providers']:
        raise ValueError('actual providers differ from the recorded reviewed providers')
    for key in ('graphics_state_scope', 'graphics_state', 'ctm_compensation', 'clip_constraint'):
        if destination['authority'][key] != chosen[key]:
            raise ValueError('authority differs from the explicitly selected candidate: ' + key)
    single.write(directory / 'initial.json', initial)
    original_text = extraction(single.SOURCE, directory, 'original')['pages']
    source_program = PdfReader(single.SOURCE).pages[scope.PAGE-1].get_contents().get_data()
    source_aliases = {p: {e['alias'] for e in v} for p, v in inventory(single.SOURCE, source=single.SOURCE)['pages'].items()}
    source_nesting = single.operator_nesting(single.SOURCE, scope.EDITED)
    logical = deepcopy(state['paragraphs'][pid]['logical'])
    stages, pdf, previous, overflow_report = [], single.SOURCE, None, None
    preparation_seconds = round(time.monotonic()-started, 2)
    for name in STAGES:
        pdf, side, state, previous, result = save_stage(directory, name, pdf, state, initial, pid, destination,
            logical, chosen, original_text, text_parts, source_program, source_aliases, previous=previous)
        stages.append(result)
        single.write(directory / 'stages.json', stages)
        if name == 'overflow':
            overflow_report = previous
    capacity_started = time.monotonic()
    capacity = refuse(directory, 'capacity', lambda o, j: edit_shared_flow(pdf, state, o, j,
        {pid: dict(edits=[dict(start=0, end=0, text=EXTRA*2, style_id='body')])}))
    if capacity['reason'] != single.CAPACITY_REASON:
        raise ValueError('unexpected capacity refusal reason')
    capacity['seconds'] = round(time.monotonic()-capacity_started, 2)
    negative = [capacity, candidates.pending_refusal(directory), checks.tampered_binding(directory, pdf, side, destination)]
    single.write(directory/'negative-controls.json', negative)
    control = representative(directory, chosen, original_text, text_parts, source_program, source_aliases, overflow_report)
    if engine_files() != environment['engine_sha256'] or single.source_sha(single.SOURCE) != single.SOURCE_SHA:
        raise ValueError('engine/source changed during evaluation')
    environment.update(runner_sha256=single.source_sha(Path(__file__)),
        evaluation_dependencies_sha256={p: single.source_sha(single.ROOT/p) for p in DEPENDENCIES})
    return dict(schema='pdfengine-scope-chain-destination-evaluation-1', status=control['status'],
        source_url=single.URL, source_sha256=single.SOURCE_SHA, environment=environment,
        inspection=inspected, reviewed_boundary=chosen, destination=destination, region_preflight=region,
        source_operator_nesting=source_nesting, stages=stages, negative_controls=negative,
        page_entry_comparison=control, preparation_seconds=preparation_seconds, seconds=round(time.monotonic()-started, 2),
        scope='One unmodified LibreOffice page-10 boundary, translated CTM, two enclosing saves and inherited rectangular clip.',
        omitted=['full pytest suite', 'source replay', 'single external', 'depth-0 external',
                 'multi-destination external', 'depth-1 external', 'second/shorten/regrow/noop page-entry lifecycle', 'noop repetitions'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--prepared-start', action='store_true', help='Use an existing start.json and read-only inspection artifacts only')
    args = parser.parse_args()
    directory = single.BASE / 'runs' / args.run_name
    if args.prepared_start:
        if not (directory/'start.json').exists() or list(directory.glob('*-report.json')) or (directory/'summary.json').exists():
            parser.error('prepared start is missing or this run has already edited a PDF')
    else:
        directory.mkdir(parents=True, exist_ok=False)
        single.write(directory/'start.json', start_record())
    result = run(directory)
    single.write(directory/'summary.json', result)
    if result['status'] != 'passed':
        raise SystemExit('Evaluation failed: see summary.json for the page-entry renderer comparison')
