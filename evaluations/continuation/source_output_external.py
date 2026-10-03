"""Windows source-output validation using the reviewed PR #31 inputs/helpers.

Eligibility is a read-only gate. Never migrate old sidecars, relax runtime
guards, or start the real-PDF mutation lifecycle after a refused source slot.
Raw source bindings, PDFs and renderer output remain in ignored runs/.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

import pymupdf
import pypdf

from pdfeditor import source_ownership as owned
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.logical_element import paragraph_from_snapshot
from pdfeditor.operator_nesting import require_text_object_split
from evaluations.continuation import evaluate as single
from evaluations.continuation import boundary_destination as boundary
from evaluations.continuation.generated_block_canonical import PROVIDERS
from evaluations.continuation.source_slot_accumulation import runtime_digest
from evaluations.continuation.source_slot_accumulation import counts, reconcile
from evaluations.continuation import canonical_metrics as metrics
from evaluations.continuation import generated_block_canonical as prior
from pdfeditor.shared_flow import open_shared_flow

BASE = '9d70d8c6de7736aba194573ea482dcde7ed9e31a'
ENGINE = 'd22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea'


def environment():
    value = dict(base_head=BASE,
        evaluated_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        git_status=subprocess.check_output(['git', 'status', '--short'], text=True),
        engine_digest=runtime_digest(), windows=platform.platform(), python=sys.version.split()[0],
        pymupdf=pymupdf.VersionBind, pypdf=pypdf.__version__, **single.tools(),
        pdftoppm=str(single.renderer_paths.DEFAULT_POPPLER),
        source_sha256=single.source_sha(single.SOURCE),
        providers=[dict(path=p, face=f, sha256=single.source_sha(p)) for p, f, _ in PROVIDERS])
    if (value['evaluated_head'] != BASE or value['engine_digest'] != ENGINE
            or value['source_sha256'] != single.SOURCE_SHA
            or [v['sha256'] for v in value['providers']] != [h for _, _, h in PROVIDERS]
            or platform.system() != 'Windows'):
        raise ValueError('exact HEAD/runtime/source/provider/Windows preconditions differ')
    return value


def scan_slot(source, state, sid):
    """Report interpreter evidence; runtime alone decides split/eligibility."""
    slot = state['slots'][sid]
    page = state['regions'][slot['region_id']]['page']
    content = ContentPage(source, page)
    try:
        paragraph = paragraph_from_snapshot(source, slot['binding']['paragraph'], content=content)
        first = paragraph.first.operator
        point = next(b for b in content.boundaries if b.operator.end == first.end)
        graphics = point.state
        split = dict(allowed=True, refusal=None)
        try:
            require_text_object_split(content, first.end)
        except PdfError as exc:
            split = dict(allowed=False, refusal=str(exc))
        result = dict(eligible=True, digest=None, refusal=None)
        try:
            result['digest'] = owned.initial_context(content, paragraph)
        except PdfError as exc:
            result.update(eligible=False, refusal=str(exc))
        return dict(slot_id=sid, paragraph_id=slot['paragraph_id'], page=page,
            source_event_count=len(paragraph.events), first_source_show_range=[first.start, first.end],
            text_object_split=split, q_depth=point.q_depth, ctm=list(graphics.ctm),
            Tr=graphics.tr, opacity=graphics.opacity, stroke_opacity=graphics.stroke_opacity,
            fill=graphics.fill, stroke=graphics.stroke,
            marked_content_depth=point.marked_content_depth, compatibility_depth=point.compatibility_depth,
            pending_path=point.pending_path, pending_clip=point.pending_clip,
            clip_count=len(graphics.clip), clips=graphics.clip,
            source_output=slot['source_output'], initial_context=result,
            eligible=result['eligible'], refusal_reason=result['refusal'])
    finally:
        content.close()


def eligibility(root):
    root.mkdir()
    started = time.monotonic()
    start = environment()
    single.write(root / 'start.json', start)
    scenarios = {}
    for name, prepare in [('page-entry', single.prepare), ('boundary', boundary.prepare)]:
        state, pid, destination = prepare()
        if state['schema'] != 'pdfengine-shared-flow-2':
            raise ValueError('fresh confirmation did not produce shared-flow v2')
        if any(s['source_output']['state'] != 'uninitialized' for s in state['slots'].values()):
            raise ValueError('initial source slots are already owned')
        single.write(root / (name + '-initial.json'), state)
        slots = [scan_slot(single.SOURCE, state, sid) for sid in state['slots']]
        if sorted(s['page'] for s in slots) != [4, 5]:
            raise ValueError('reviewed source pages changed')
        scenarios[name] = dict(schema=state['schema'], paragraph_id=pid, destination=destination,
            source_slots=slots, eligible=all(s['eligible'] for s in slots),
            generated_slots_initially_present=0, mutation_lifecycle_run=False)
        print(name, [(s['page'], s['eligible'], s['refusal_reason']) for s in slots], flush=True)
    if runtime_digest() != ENGINE or single.source_sha(single.SOURCE) != start['source_sha256']:
        raise ValueError('runtime or source changed during eligibility scan')
    result = dict(schema='pdfengine-source-output-external-1',
        status='eligible' if all(s['eligible'] for s in scenarios.values()) else 'not-eligible',
        start=start, scenarios=scenarios, engine_unchanged=True, source_unchanged=True,
        elapsed_seconds=round(time.monotonic()-started, 3), evaluation_attempts=[],
        real_pdf_saves=0)
    single.write(root / 'eligibility.json', result)
    return result


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source_metrics(pdf, state):
    """Collect only runtime-validated islands; never infer an owned span."""
    opened = open_shared_flow(pdf, state)
    if opened['status'] != 'restored':
        raise ValueError(opened['reason'])
    result = {}
    for sid, slot in state['slots'].items():
        if slot.get('destination_id') is not None:
            if 'source_output' in slot:
                raise ValueError('generated slot acquired source ownership')
            continue
        record = slot['source_output']
        page = state['regions'][slot['region_id']]['page']
        data = metrics.program(pdf, page)
        a, b, c, d = owned.inventory(data)[record['marker_id']]
        body = data[b:c]
        owned.grammar(body)
        extra = counts(body)
        result[sid] = dict(page=page, record=record, marker_begin_count=1, marker_end_count=1,
            marker_range=[a, d], body_range=[b, c], block_bytes=d-a, body=metrics.census(body),
            numeric_only_TJ=extra['nonpainting_numeric_TJ'], empty_TJ=extra['empty_TJ'],
            page_program=metrics.census(data), prefix_sha256=owned.sha(data[:a]), suffix_sha256=owned.sha(data[d:]))
    return result


def compare_source_rows(before, after, *, noop):
    checks = {}
    for sid, row in after.items():
        old = before[sid]
        checks[sid] = dict(identity=owned.immutable(old['record']) == owned.immutable(row['record']),
            entry_context=old['record']['current']['entry_context_sha256'] == row['record']['current']['entry_context_sha256'],
            prefix=old['prefix_sha256'] == row['prefix_sha256'], suffix=old['suffix_sha256'] == row['suffix_sha256'])
        if noop:
            checks[sid].update(body=old['body'] == row['body'], page=old['page_program'] == row['page_program'],
                               record=old['record'] == row['record'])
    return checks


def collect_saved(directory, name, before, pdf, state, report, destination, scenario, *, noop):
    rows = source_metrics(pdf, state)
    attribution = metrics.mutation_breakdown(before, pdf, state, report)
    mutations = {}
    for page in (4, 5, 6):
        edits = report['mutation_map'].get(str(page), report['mutation_map'].get(page, {})).get('mutations', [])
        changes = reconcile(metrics.program(before, page), metrics.program(pdf, page), edits)
        mutations[str(page)] = [{k: v for k, v in r.items() if k not in
            ('consumed_example', 'replacement_prefix', 'consumed', 'replacement')} for r in changes]
    checks = {}
    if name != 'overflow':
        checks = compare_source_rows(read(directory / 'overflow-source.json')['source_slots'], rows, noop=False)
    if noop:
        oldstate = read(Path(before).with_suffix('.json'))
        checks = compare_source_rows(source_metrics(before, oldstate), rows, noop=True)
    generated = metrics.measure(pdf, state, destination['destination_id'])
    if scenario == 'boundary':
        boundary.order(pdf, state, destination, metrics.program(single.SOURCE, 6))
    else:
        data = metrics.program(pdf, 6)
        if generated['start'] != 0 or data[generated['end']:] != metrics.program(single.SOURCE, 6):
            raise ValueError('page-entry source suffix changed')
    row = dict(stage=name, source_slots=rows, page_program=attribution, mutations=mutations,
        invariants=checks, generated_continuation=generated,
        saved_coordinates=prior.saved_coordinates(pdf, state, destination, report),
        operator_nesting=single.operator_nesting(pdf, single.EDITED))
    single.write(directory / (name + '-source.json'), row)
    if not all(all(v.values()) for v in checks.values()):
        raise ValueError(name + ': source body/page/record/context/bridge stability failed')
    for sid in rows:
        page = str(rows[sid]['page'])
        kinds = [m['kind'] for m in mutations[page] if m['owner'] == sid]
        if (name == 'overflow' and (kinds.count(owned.CREATE) != 1 or any(k not in (owned.CREATE, 'text-edit') for k in kinds))
                or name != 'overflow' and kinds != [owned.REWRITE]):
            raise ValueError('source mutation kind/count is not canonical')
    if noop and any(not v['program_bytes_equal'] for v in attribution.values()):
        raise ValueError('no-op changed a decoded page program')
    return row


def formal(root, scenario):
    """Reuse PR #31's complete lifecycle/audits; interpose immediate no-op branches."""
    from unittest.mock import patch
    from pdfeditor.shared_flow import edit_shared_flow
    gate = read(root / 'eligibility.json')
    if gate['status'] != 'eligible':
        raise ValueError('real-PDF mutation prohibited by eligibility gate')
    if runtime_digest() != ENGINE or single.source_sha(single.SOURCE) != single.SOURCE_SHA:
        raise ValueError('runtime/source changed after eligibility')
    directory = root / scenario
    directory.mkdir()
    initial = read(root / (scenario + '-initial.json'))
    opened = open_shared_flow(single.SOURCE, initial)
    if opened['status'] != 'restored':
        raise ValueError(opened['reason'])
    pid = initial['flow']['paragraphs'][0]
    destination = next(iter(initial['continuation_destinations'].values()))
    module = single if scenario == 'page-entry' else boundary
    last_stage = 'initial'
    def save(before, state, pdf, side, changes):
        nonlocal last_stage
        # These branches run after the previous formal audit and before the
        # next semantic save. They never replace the formal series' inputs.
        base = Path(before).stem
        if base in ('overflow', 'second', 'shorten'):
            name = {'overflow':'grow-noop', 'second':'second-noop', 'shorten':'dormant-noop'}[base]
            last_stage = name
            prior.supplemental_noop(directory, scenario, base, name)
            probe = directory / name
            collect_saved(directory, name, before, probe / 'saved.pdf', read(probe / 'saved.json'),
                read(probe / 'report.json'), destination, scenario, noop=True)
        last_stage = Path(pdf).stem
        report = edit_shared_flow(before, state, pdf, side, changes)
        single.write(directory / (last_stage + '-report.json'), report)
        collect_saved(directory, last_stage, before, pdf, read(side), report, destination, scenario,
                      noop=last_stage.startswith('noop'))
        return report
    started = time.monotonic()
    try:
        # This is the freshly confirmed v2 sidecar from Phase 1, never a v1 migration.
        with patch.object(module, 'prepare', lambda: (deepcopy(initial), pid, deepcopy(destination))), \
                patch.object(module, 'edit_shared_flow', save):
            evidence = (single.run(directory) if scenario == 'page-entry' else
                        boundary.run(directory, root.name + '/page-entry'))
        evidence['elapsed_seconds'] = round(time.monotonic()-started, 3)
        single.write(directory / 'summary.json', evidence)
        return evidence
    except Exception as exc:
        single.write(directory / 'failure.json', dict(stage=last_stage, failure=repr(exc),
            elapsed_seconds=round(time.monotonic()-started, 3), runtime_unchanged=runtime_digest()==ENGINE))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--phase', choices=['eligibility', 'page-entry', 'boundary'], default='eligibility')
    args = parser.parse_args()
    directory = single.BASE / 'runs' / args.run
    try:
        evidence = eligibility(directory) if args.phase == 'eligibility' else formal(directory, args.phase)
    except Exception as exc:
        if directory.exists():
            single.write(directory / (args.phase + '-failure.json'), dict(stage=args.phase, failure=repr(exc)))
        raise
