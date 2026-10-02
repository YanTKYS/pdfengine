"""PR #30 real-PDF evidence: existing formal lifecycles plus canonical metrics.

Run sequentially on the reviewed Windows inputs. --collect finishes an
existing run whose two unmodified formal evaluators have already completed;
it never reruns or overwrites their evidence. Raw data stays under runs/.
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

import pymupdf
import pypdf

from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from evaluations.continuation import boundary_destination as boundary
from evaluations.continuation import canonical_metrics as metrics
from evaluations.continuation import evaluate as single
from evaluations.continuation import scope_chain_checks

HEAD = '8178462d84ade7241b536a533756149a5fb39754'
ENGINE = 'dddbdc19f611fcbef0001e93c821955924d5341c25b63401c37c8e5d267a2a4c'
PROVIDERS = [('C:/Windows/Fonts/msmincho.ttc', 1, 'ceb8d745001f56b61ce768d84172d35bdf68e498423c9320dcb22e7c900944c2'),
             ('C:/Windows/Fonts/times.ttf', 0, '931c5de5c70401d9324d5014c123802b4fb753000360ceb2f56c589403cd58c5')]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def engine_digest():
    files = {p.name: single.source_sha(p) for p in sorted((single.ROOT / 'pdfeditor').glob('*.py'))}
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def start_evidence():
    value = dict(git_status=subprocess.check_output(['git', 'status', '--short'], text=True),
                 head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                 engine_digest=engine_digest(), environment=dict(platform=platform.platform(),
                 python=sys.version.split()[0], pymupdf=pymupdf.VersionBind, pypdf=pypdf.__version__, **single.tools()),
                 source_sha256=single.source_sha(single.SOURCE),
                 providers=[dict(path=p, face=f, sha256=single.source_sha(p)) for p, f, _ in PROVIDERS])
    if (value['head'] != HEAD or value['engine_digest'] != ENGINE or value['source_sha256'] != single.SOURCE_SHA
            or [p['sha256'] for p in value['providers']] != [h for _, _, h in PROVIDERS]
            or platform.system() != 'Windows'):
        raise ValueError('reviewed HEAD/engine/source/provider/Windows preconditions differ')
    return value


def glyphs(report):
    return [[{k: g[k] for k in single.GLYPH_FIELDS} for g in step['report']['glyph_plan']]
            for step in report['steps']]


def saved_coordinates(pdf, state, destination, report):
    result = scope_chain_checks.glyph_coordinates(pdf, state, destination, report)
    return {key: value for key, value in result.items() if not key.endswith('_samples')}


def supplemental_noop(directory, scenario, base_stage, name):
    """A branch from grow or shorten; the formal lifecycle remains unchanged."""
    outdir = directory / name
    before, side = directory / f'{base_stage}.pdf', directory / f'{base_stage}.json'
    inputs = {p.name: single.source_sha(p) for p in (before, side)}
    if outdir.exists():
        proof = read(outdir / 'checks.json')
        if (proof.get('input_sha256') != inputs or
                proof.get('output_sha256') != {n: single.source_sha(outdir / n)
                                              for n in ('saved.pdf', 'saved.json', 'report.json')}):
            raise ValueError('existing supplemental evidence cannot be safely resumed')
        return
    outdir.mkdir()
    opened = open_shared_flow(before, side)
    if opened['status'] != 'restored':
        raise ValueError(opened['reason'])
    state = opened['state']
    out, outside = outdir / 'saved.pdf', outdir / 'saved.json'
    report = edit_shared_flow(before, state, out, outside, {})
    single.write(outdir / 'report.json', report)
    reopened = open_shared_flow(out, outside)
    if reopened['status'] != 'restored':
        raise ValueError(reopened['reason'])
    updated = reopened['state']
    initial = read(directory / 'initial.json')
    original = single.extraction(single.SOURCE, outdir, 'original')['pages']
    audit = boundary.audit if scenario == 'boundary' else single.audit
    proof = audit(before, out, updated, initial, report, outdir / 'audit', original, noop=True,
                  owned=state.get('generated_fonts', {}))
    if (state['paragraphs'] != updated['paragraphs'] or state['continuation_destinations'] != updated['continuation_destinations']
            or state['generated_fonts'] != updated['generated_fonts']
            or any(single.stable_slot(slot) != single.stable_slot(updated['slots'][sid]) for sid, slot in state['slots'].items())
            or glyphs(read(directory / f'{base_stage}-report.json')) != glyphs(report)
            or any(set(v.values()) != {'reused'} for v in report['generated_font_outcome'].values())):
        raise ValueError('supplemental no-op changed semantics/glyphs/fonts')
    for ident in state['continuation_destinations']:
        if metrics.block(before, state, ident) != metrics.block(out, updated, ident):
            raise ValueError(f'{name}: first no-op migrated a freshly canonical block')
    proof.update(block_bytes_equal=True, glyph_fields_equal=True, font_records_equal=True,
                 slot_identity_style_allocation_equal=True, generated_font_outcome=report['generated_font_outcome'],
                 operator_nesting=single.operator_nesting(out, single.EDITED), input_sha256=inputs,
                 output_sha256={n: single.source_sha(outdir / n) for n in ('saved.pdf', 'saved.json', 'report.json')})
    single.write(outdir / 'checks.json', proof)
    print(scenario, name, 'passed', flush=True)


def measure_scenario(directory, scenario):
    formal = read(directory / 'summary.json')
    if formal['status'] != 'passed' or formal['environment']['engine_digest'] != ENGINE:
        raise ValueError('formal evaluator did not pass on this engine')
    ident = formal['destination']['destination_id']
    rows, blocks, states = [], {}, {}
    before = single.SOURCE
    creation = None
    for name in single.STAGES:
        pdf = directory / f'{name}.pdf'
        state = states[name] = read(directory / f'{name}.json')
        report = read(directory / f'{name}-report.json')
        value = metrics.measure(pdf, state, ident)
        blocks[name] = metrics.block(pdf, state, ident)
        sid = value['slot_id']
        creation = creation or state['slots'][sid]['creation_binding']
        if state['slots'][sid]['creation_binding'] != creation or state['continuation_destinations'][ident] != formal['destination']:
            raise ValueError('creation binding or destination authority changed')
        if scenario == 'boundary':
            boundary.order(pdf, state, formal['destination'], metrics.program(single.SOURCE, 6))
        elif value['start'] != 0 or metrics.program(pdf, 6)[value['end']:] != metrics.program(single.SOURCE, 6):
            raise ValueError('page-entry block moved or its source suffix changed')
        stage = next(row for row in formal['stages'] if row['stage'] == name)
        rows.append(dict(stage='grow' if name == 'overflow' else name, raw_stage=name, blocks=[value],
                         page_program=metrics.mutation_breakdown(before, pdf, state, report),
                         font_inventory=stage['resources'], renderer_checks=stage['renderers'],
                         saved_glyph_coordinates=saved_coordinates(pdf, state, formal['destination'], report),
                         glyph_unicode_cid_gid_w=stage['all_page_unicode'] and stage['cid_gid_w'],
                         source_paint_images_annotations_original_fonts=stage['all_nontext_paint_images_annotations_original_fonts'],
                         operator_nesting=stage['operator_nesting']))
        before = pdf
    equality = {name: blocks[name] == blocks['regrow'] for name in ('noop1', 'noop2', 'noop3')}
    if not all(equality.values()):
        raise ValueError('active no-op block bytes differ from regrow')
    origins = [r['saved_glyph_coordinates']['saved_glyph_origins_sha256'] for r in rows
               if r['stage'] in ('regrow', 'noop1', 'noop2', 'noop3')]
    if len(set(origins)) != 1:
        raise ValueError('active no-op saved glyph origins differ')
    dormant = next(row['blocks'][0] for row in rows if row['stage'] == 'shorten')
    if not (dormant['painting_text_show_count'] == 0 and dormant['insertion_binding_present']
            and dormant['style_slot_bindings_present'] and dormant['generated_font_aliases']):
        raise ValueError('dormant block lost its insertion/style/font witness')
    probes = {}
    for base, name in [('overflow', 'grow-noop'), ('shorten', 'dormant-noop')]:
        probe = directory / name
        state = read(probe / 'saved.json')
        value = metrics.measure(probe / 'saved.pdf', state, ident)
        if metrics.block(probe / 'saved.pdf', state, ident) != blocks[base]:
            raise ValueError('supplemental no-op block equality failed')
        if state['slots'][value['slot_id']]['creation_binding'] != creation:
            raise ValueError('supplemental no-op creation binding changed')
        if name == 'dormant-noop' and any(value[k] != dormant[k] for k in
                ('typing_style_id', 'style_witness_ids', 'generated_font_aliases')):
            raise ValueError('dormant no-op changed typing style, witnesses or owned fonts')
        if scenario == 'boundary':
            boundary.order(probe / 'saved.pdf', state, formal['destination'], metrics.program(single.SOURCE, 6))
        probes[name] = dict(blocks=[value], checks=read(probe / 'checks.json'),
                           saved_glyph_coordinates=saved_coordinates(probe / 'saved.pdf', state,
                               formal['destination'], read(probe / 'report.json')),
                           page_program=metrics.mutation_breakdown(directory / f'{base}.pdf', probe / 'saved.pdf', state,
                                                                  read(probe / 'report.json')))
        baseline = next(r for r in rows if r['raw_stage'] == base)['saved_glyph_coordinates']
        if probes[name]['saved_glyph_coordinates']['saved_glyph_origins_sha256'] != baseline['saved_glyph_origins_sha256']:
            raise ValueError('supplemental no-op saved glyph origins differ')
    return dict(status='passed', destination_id=ident, slot_id=dormant['slot_id'], destination=formal['destination'],
                lifecycle_stages=rows, supplemental_noops=probes,
                noop_stability=dict(regrow_to_noop1_noop2_noop3_byte_equality=equality,
                                    grow_equals_regrow=blocks['overflow'] == blocks['regrow'],
                                    first_grow_noop_byte_equal=True, dormant_noop_byte_equal=True,
                                    saved_glyph_origins_equal=True,
                                    byte_equality_also_implies_sha_length_and_all_operator_counts_equal=True),
                identity=dict(destination_authority_equal=True, creation_binding_equal=True, marker_pair_equal=True,
                              original_page6_outside_block_equal=True,
                              boundary_prefix_suffix_equal=True if scenario == 'boundary' else None),
                source_replay=formal['source_replay'], negative_controls=formal['negative_controls'],
                pdf_header=formal['source_pdf_header'],
                page_entry_comparison=formal.get('page_entry_comparison'),
                formal_elapsed_seconds=formal.get('elapsed_seconds'))


def historical(scenarios, root):
    result = {}
    for scenario, filename in [('page-entry', 'summary.json'), ('boundary', 'boundary-destination-summary.json')]:
        path = single.BASE / filename
        past = read(path)
        old = {r['stage']: r for r in past['stages']}
        current = {r['stage']: r for r in read(root / scenario / 'summary.json')['stages']}
        comparisons = []
        for row in scenarios[scenario]['lifecycle_stages']:
            previous = old[row['raw_stage']]
            keys = single.RESOURCE_COUNTS
            comparisons.append(dict(stage=row['stage'],
                historical_block_bytes=previous['resources']['generated_block_bytes'],
                current_block_bytes=row['blocks'][0]['byte_length'],
                font_inventory_counts_equal=all(previous['resources'][k] == row['font_inventory'][k] for k in keys),
                allocation_equal=previous['allocation'] == current[row['raw_stage']]['allocation']))
        result[scenario] = dict(path='evaluations/continuation/' + filename, sha256=single.source_sha(path),
            engine_digest=past['environment']['engine_digest'], comparisons=comparisons,
            historical_raw_glyphs_and_renderer_pixels='unavailable for these published page-entry/boundary runs',
            historical_page_program_lengths='not present in published compact summary',
            authority_contract_equal=past['destination'] == scenarios[scenario]['destination'])
        if (not result[scenario]['authority_contract_equal'] or
                not all(row['font_inventory_counts_equal'] and row['allocation_equal'] for row in comparisons)):
            raise ValueError(f'{scenario}: unexplained historical authority/allocation/font inventory difference')
    return result


def historical_raw(root):
    """A surviving pre-contract grow PDF, not a complete historical run.

    Its engine digest is unavailable. Preserve this limitation and file hashes;
    do not pretend it is the published full page-entry/boundary evidence.
    """
    past = single.BASE / 'runs' / 'verified'
    names = ['overflow.pdf', 'overflow.json', 'overflow-report.json', 'initial.json']
    if not all((past / name).exists() for name in names):
        return dict(status='unavailable')
    directory = root / 'historical-verified-regrow'
    directory.mkdir(exist_ok=True)
    oldstate = read(past / 'overflow.json')
    newstate = read(root / 'page-entry' / 'regrow.json')
    initial = read(past / 'initial.json')
    if (initial['pdf_sha256'] != single.SOURCE_SHA
            or oldstate['continuation_destinations'] != newstate['continuation_destinations']
            or glyphs(read(past / 'overflow-report.json')) != glyphs(read(root / 'page-entry' / 'regrow-report.json'))):
        raise ValueError('historical grow and current regrow have different source/authority/glyphs')
    visuals = {}
    for scenario in ('page-entry', 'boundary'):
        visuals[scenario] = {}
        for page in range(1, 11):
            v = single.audit_render(past / 'overflow.pdf', root / scenario / 'regrow.pdf', page,
                                    directory / scenario / f'page-{page}', (0, 0, 595, 842), noop=True,
                                    # Boundary deliberately changes page 6's extraction order.
                                    # Every page still gets the strict noop pixel check.
                                    edited_pages={6})
            visuals[scenario][str(page)] = dict(mupdf_equal=v['mupdf_page_pixels'],
                                    poppler_changed_pixels=v['poppler_diff']['all_changed_pixels'])
    ident = next(iter(oldstate['continuation_destinations']))
    result = dict(status='passed', path='evaluations/continuation/runs/verified/overflow.pdf',
                  evidence_sha256={n: single.source_sha(past / n) for n in names},
                  historical_engine_digest=None, historical_complete_summary_available=False,
                  historical_operator_nesting_record=oldstate['destination_bindings'][ident].get('operator_nesting'),
                  historical_block_bytes=len(metrics.block(past / 'overflow.pdf', oldstate, ident)),
                  current_stage='both scenarios/regrow', all_glyph_fields_equal=True,
                  authority_equal_to_page_entry=True,
                  boundary_page6_text_order='intentionally after the source body; verified by the formal independent extraction audit',
                  renderers=visuals,
                  extension='each scenario noop1/2/3 equals its regrow by full-page formal audits')
    single.write(directory / 'checks.json', result)
    return result


def run(root, collect=False):
    if not collect:
        root.mkdir()
        single.write(root / 'start.json', start_evidence())
        for name, runner in [('page-entry', single.run),
                             ('boundary', lambda d: boundary.run(d, root.name + '/page-entry'))]:
            directory = root / name
            directory.mkdir()
            stamp = time.monotonic()
            evidence = runner(directory)
            evidence['elapsed_seconds'] = round(time.monotonic() - stamp, 3)
            single.write(directory / 'summary.json', evidence)
    started = time.monotonic()
    # Verify inputs again even when collecting pre-existing formal evidence.
    start_evidence()
    scenarios = {}
    for name in ('page-entry', 'boundary'):
        directory = root / name
        if read(directory / 'summary.json')['status'] != 'passed':
            raise ValueError('formal run incomplete')
        for base, probe in [('overflow', 'grow-noop'), ('shorten', 'dormant-noop')]:
            supplemental_noop(directory, name, base, probe)
        scenarios[name] = measure_scenario(directory, name)
        single.write(root / (name + '-metrics.json'), scenarios[name])
    for left, right in zip(scenarios['page-entry']['lifecycle_stages'], scenarios['boundary']['lifecycle_stages']):
        if left['saved_glyph_coordinates']['saved_glyph_origins_sha256'] != right['saved_glyph_coordinates']['saved_glyph_origins_sha256']:
            raise ValueError('boundary and page-entry saved glyph origins differ')
    original = read(root / 'start.json')
    if single.source_sha(single.SOURCE) != original['source_sha256'] or engine_digest() != original['engine_digest']:
        raise ValueError('source or engine changed')
    historical_pixels = historical_raw(root)
    if single.source_sha(single.SOURCE) != original['source_sha256'] or engine_digest() != original['engine_digest']:
        raise ValueError('source or engine changed during final comparison')
    attempts = [read(p) for p in sorted(root.glob('collection-attempt-*.json'))]
    return dict(schema='pdfengine-generated-block-canonical-external-1', status='passed',
                start=original, scenarios=scenarios, historical_comparison=historical(scenarios, root),
                historical_partial_raw_comparison=historical_pixels,
                source_unchanged=True, engine_unchanged=True,
                timings=dict(collection_and_supplemental_seconds=round(time.monotonic() - started, 3),
                    prior_collection_attempt_seconds=[a['elapsed_seconds'] for a in attempts],
                    formal_series_seconds={k:v['formal_elapsed_seconds'] for k,v in scenarios.items()}),
                evaluation_attempts=attempts,
                tests=dict(helper_command='python -m pytest -q evaluations/continuation/test_canonical_metrics.py',
                           execution='recorded separately; this runner does not invoke pytest', full_suite_run=False),
                limitations=['one reviewed external LibreOffice source and two existing destinations',
                             'source-slot accumulation remains; only its mutation-owned deltas are measured',
                             'historical complete page-entry/boundary runs unavailable; partial verified grow baseline has no engine digest'],
                independent_review=dict(requested_model='Claude Opus 5.5', status='pending-unavailable'),
                evaluator_sha256={p.name:single.source_sha(p) for p in
                                  [Path(__file__), Path(metrics.__file__), Path(scope_chain_checks.__file__)]})


def compact_summary(evidence):
    """Keep block measurements; summarize only proven unchanged page programs."""
    result = deepcopy(evidence)

    def renderers(values):
        return dict(pages_checked=sorted(map(int, values)),
                    mupdf_all_checked_pages_equal=all(v['mupdf_equal'] for v in values.values()),
                    poppler_changed_pixels_total=sum(v['poppler_changed_pixels'] for v in values.values()),
                    poppler_outside_changed_pixels_total=sum(v.get('poppler_outside_changed_pixels', 0) for v in values.values()),
                    changed_pages={p: v for p, v in values.items()
                                   if not v['mupdf_equal'] or v['poppler_changed_pixels']})

    for scenario in result['scenarios'].values():
        rows = scenario['lifecycle_stages'] + list(scenario['supplemental_noops'].values())
        for row in rows:
            programs = row['page_program']
            others = {p: v for p, v in programs.items() if p not in single.EDITED}
            if any(not v['program_bytes_equal'] or v['delta_bytes'] != 0 or v['before_operators'] != v['after_operators']
                   for v in others.values()):
                raise ValueError('cannot omit a changed page from the compact summary')
            row['unchanged_other_pages'] = sorted(map(int, others))
            row['page_program'] = {p: v for p, v in programs.items() if p in single.EDITED}
            if 'renderer_checks' in row:
                row['renderer_checks'] = renderers(row['renderer_checks'])
            else:
                row['checks']['renderers'] = renderers(row['checks']['renderers'])
    historical = result.get('historical_partial_raw_comparison', {})
    if historical.get('status') == 'passed':
        historical['renderers'] = {name: renderers(rows) for name, rows in historical['renderers'].items()}
    result['representation'] = 'compact; full measurements and raw artifacts remain in the ignored run directory'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    parser.add_argument('--collect', action='store_true')
    parser.add_argument('--publish', action='store_true', help='write the compact public summary after all checks pass')
    args = parser.parse_args()
    root = single.BASE / 'runs' / args.run
    try:
        evidence = run(root, args.collect)
        single.write(root / 'canonical-summary.json', evidence)
        if args.publish:
            single.write(single.BASE / 'generated-block-canonical-summary.json', compact_summary(evidence))
    except Exception as exc:
        if root.exists():
            single.write(root / 'canonical-failure.json', dict(status='failed', error=repr(exc)))
        raise
