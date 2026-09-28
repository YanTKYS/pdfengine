"""Focused PR #18 follow-up: one compensated overflow and one page-entry overflow.

The original failed summary is immutable historical evidence. This evaluator
reuses its selected source/bounds/paragraph and saved-byte/state/render gates;
it does not repeat the boundary lifecycle or earlier external evaluations.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import time

from pypdf import PdfReader

from pdfeditor.continuation import inspect_continuation_boundaries
from evaluations.continuation import evaluate as single, scope_destination as scope
from evaluations.continuation import scope_chain_destination as shared
from evaluations.continuation import scope_chain_candidates as candidates
from evaluations.continuation.resources import inventory
from evaluations.continuation.scope_chain_region import source_region_review
from evaluations.elements.evaluate import extraction

HISTORICAL = single.BASE / 'scope-chain-destination-summary.json'
EXPECTED = '1 0 0 1 -158.2 -662.8 cm'
DEPENDENCIES = shared.DEPENDENCIES + ['evaluations/continuation/scope_chain_destination.py']


def run(directory):
    started = time.monotonic()
    environment = shared.start_record()
    single.write(directory / 'start.json', environment)
    history_sha = single.source_sha(HISTORICAL)
    history = json.loads(HISTORICAL.read_text(encoding='utf-8'))
    if history['status'] != 'failed' or history['page_entry_comparison']['pages']['10']['poppler_diff']['all_changed_pixels'] != 5868:
        raise ValueError('the PR #18 failed evidence was changed')
    inspected = inspect_continuation_boundaries(single.SOURCE, scope.PAGE)
    chosen = next(c for c in inspected['candidates'] if c['boundary_id'] == candidates.REVIEWED_BOUNDARY['boundary_id'])
    if ({k: chosen[k] for k in candidates.REVIEWED_BOUNDARY} != candidates.REVIEWED_BOUNDARY
            or chosen['scope']['q_depth'] != 2 or chosen['scope']['pending_path']):
        raise ValueError('the focused boundary is not the PR #18 reviewed position')
    old = history['destination']['authority']
    for key in ('graphics_state', 'graphics_state_scope', 'clip_constraint'):
        if chosen[key] != old[key]:
            raise ValueError('the existing scope/clip/state authority changed: ' + key)
    compensation = chosen['ctm_compensation']
    if compensation['inverse_basis'] != 'source-decimal-operands' or compensation['operator'] != EXPECTED:
        raise ValueError('fractional translation is not compensated from its source operands')
    if any(p['displacement_bound'] > .002 for p in compensation['proof']['models'].values()):
        raise ValueError('dual-model proof exceeds the unchanged tolerance')
    single.write(directory / 'reviewed-candidate.json', chosen)
    region = source_region_review(directory, chosen)
    state, pid, destination = scope.prepare('boundary', boundary_id=chosen['boundary_id'])
    for key in ('graphics_state', 'graphics_state_scope', 'clip_constraint', 'ctm_compensation'):
        if destination['authority'][key] != chosen[key]:
            raise ValueError('confirmed authority differs from the reviewed candidate: ' + key)
    if single.provider_evidence(state['paragraphs'][pid]['style_registry']) != environment['providers']:
        raise ValueError('actual providers differ from the reviewed start record')
    initial = deepcopy(state)
    single.write(directory / 'initial.json', initial)
    original = extraction(single.SOURCE, directory, 'original')['pages']
    parts = candidates.unicode_parts(directory)
    source_program = PdfReader(single.SOURCE).pages[scope.PAGE-1].get_contents().get_data()
    source_aliases = {p: {e['alias'] for e in v} for p, v in inventory(single.SOURCE, source=single.SOURCE)['pages'].items()}
    nesting = single.operator_nesting(single.SOURCE, scope.EDITED)
    preparation_seconds = round(time.monotonic()-started, 2)
    _, _, _, report, result = shared.save_stage(directory, 'overflow', single.SOURCE, state, initial, pid, destination,
        state['paragraphs'][pid]['logical'], chosen, original, parts, source_program, source_aliases)
    control = shared.representative(directory, chosen, original, parts, source_program, source_aliases, report)
    if (shared.engine_files() != environment['engine_sha256'] or single.source_sha(HISTORICAL) != history_sha
            or single.source_sha(single.SOURCE) != single.SOURCE_SHA):
        raise ValueError('engine, source or historical evidence changed during evaluation')
    environment.update(runner_sha256=single.source_sha(Path(__file__)),
        evaluation_dependencies_sha256={p: single.source_sha(single.ROOT / p) for p in DEPENDENCIES})
    return dict(schema='pdfengine-source-ctm-focused-evaluation-1', run=directory.name, status=control['status'],
        source_url=single.URL, source_sha256=single.SOURCE_SHA, environment=environment,
        historical=dict(summary='evaluations/continuation/scope-chain-destination-summary.json', sha256=history_sha,
                        status='failed', poppler_page10_changed_pixels=5868, preserved=True),
        reviewed_boundary=candidates.REVIEWED_BOUNDARY, destination=destination, region_preflight=region,
        scope_clip_authority_unchanged=True, source_operator_nesting=nesting,
        boundary_overflow=result, page_entry_comparison=control,
        preparation_seconds=preparation_seconds, seconds=round(time.monotonic()-started, 2),
        scope='One unchanged LibreOffice fractional-translation boundary, one overflow per authority; empirical renderer equality, not a theorem about arbitrary renderers.',
        omitted=['PR #18 full lifecycle', 'single/depth0/multi/depth1 external evaluations'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-name', required=True)
    args = parser.parse_args()
    directory = single.BASE / 'runs' / args.run_name
    directory.mkdir(parents=True, exist_ok=False)
    result = run(directory)
    single.write(directory / 'summary.json', result)
    if result['status'] != 'passed':
        raise SystemExit('Focused evaluation failed: see summary.json')
