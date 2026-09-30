"""Measure read-only boundary grouping once across the unchanged real PDF.

All inspector records and all groups stay under runs/. The compact report
contains statistics and deterministic coverage examples, never a chosen
boundary, geometry-derived recommendation or confirmation.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

from pypdf import PdfReader

from pdfeditor.continuation import inspect_continuation_boundaries
from evaluations.continuation import evaluate as single
from evaluations.continuation.boundary_review import group_candidates, group_statistics, REVIEW_CRITERIA, REVIEW_MEANING
from evaluations.continuation.depth_three_inspection import ENGINE, refusal_inventory, summarize_page, write
from evaluations.continuation.scope_chain_destination import engine_files, start_record


HISTORICAL = single.BASE / 'depth-three-inspection-summary.json'
HELPERS = ['boundary_review.py', 'boundary_review_evaluate.py', 'depth_three_inspection.py']


def representative_examples(groups):
    # Prefer a mixed group for each attribute so the authority distinction is
    # visible. Ties use page/paint position, never a candidate/safety score.
    predicates = {
        'page_level': lambda row: row['page_level'],
        'depth_1': lambda row: row['q_depth'] == 1,
        'depth_2': lambda row: row['q_depth'] == 2,
        'ctm_compensation': lambda row: row['has_ctm_compensation'],
        'rectangular_clip': lambda row: row['has_rectangular_clip_constraint'],
    }
    selected, absent = {}, []
    for label, matches in predicates.items():
        present = [g for g in groups if any(matches(row) for row in g['candidates'])]
        if not present:
            absent.append(label)
            continue
        mixed = [g for g in present if any(not matches(row) for row in g['candidates'])]
        chosen = (mixed or present)[0]  # groups already sorted by page/paint position
        entry = selected.setdefault(chosen['group_id'], dict(group=chosen, covers=[]))
        entry['covers'].append(label)
    examples = []
    witness_fields = ['graphics_state_sha256', 'scope_sha256', 'compensation_sha256', 'clip_constraint_sha256']
    for entry in selected.values():
        group = entry['group']
        variants = {field: {} for field in witness_fields}
        rows = []
        for row in group['candidates']:
            variant_ids = []
            for field in witness_fields:
                value = row[field]
                if value is None:
                    variant_ids.append(None)
                else:
                    mapping = variants[field]
                    variant_ids.append(mapping.setdefault(value, len(mapping) + 1))
            rows.append([row['boundary_id'], row['ordinal'], row['previous_operator'], row['next_operator'],
                row['q_depth'], row['scope_policy'], row['has_scope_binding'], row['has_ctm_compensation'],
                row['has_rectangular_clip_constraint'],
                [[level['opening_ordinal'], level['matching_ordinal']] for level in row['scope_operators']], *variant_ids])
        examples.append(dict(covers=entry['covers'], **{k: group[k] for k in
            ('page', 'group_id', 'program_sha256', 'z_order', 'candidate_count', 'ordinal_min', 'ordinal_max',
             'least_constrained_candidates', 'distinct_witness_counts')},
            candidate_columns=['boundary_id', 'ordinal', 'previous_operator', 'next_operator', 'q_depth',
                'scope_policy', 'has_scope_binding', 'has_ctm_compensation', 'has_rectangular_clip_constraint',
                'scope_q_Q_ordinals_outermost_first', 'graphics_state_variant', 'scope_variant',
                'compensation_variant', 'clip_variant'], candidates=rows))
    return dict(selection='One group per present attribute, preferring mixed values, then page/paint-position order; duplicate groups combined. These are coverage examples, not recommended choices.',
        variant_meaning='Local labels compare exact witness hashes within each example only; full witnesses remain in raw inspector records. Equal review attributes do not establish authority equivalence.',
        absent_types=absent, groups=examples)


def run(directory):
    started = time.monotonic()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if list(directory.glob('page-*.json')) or (directory / 'groups.json').exists():
        raise ValueError('refusing to repeat or overwrite a full/partial scan')
    start = directory / 'start.json'
    env = json.loads(start.read_text(encoding='utf-8')) if start.exists() else start_record()
    if not start.exists():
        write(start, env)
    before = engine_files()
    if (before != env['engine_sha256'] or env['engine_digest'] != ENGINE
            or single.source_sha(single.SOURCE) != single.SOURCE_SHA):
        raise ValueError('engine or source differs from reviewed input')
    for provider in env['providers'].values():
        if single.source_sha(Path('C:/Windows/Fonts') / provider['filename']) != provider['sha256']:
            raise ValueError('provider differs')
    historical_sha = single.source_sha(HISTORICAL)
    historical = json.loads(HISTORICAL.read_text(encoding='utf-8'))
    reader = PdfReader(single.SOURCE)
    if len(reader.pages) != 10:
        raise ValueError('expected the ten-page original')
    pages, all_safe, all_refused, raw_files = [], [], [], []
    inspection_seconds = 0
    for number in range(1, 11):
        tick = time.monotonic()
        found = inspect_continuation_boundaries(single.SOURCE, number, include_refused=True)
        elapsed = time.monotonic() - tick
        inspection_seconds += elapsed
        original = reader.pages[number - 1].get_contents()
        if hashlib.sha256(original.get_data()).hexdigest() != found['program_sha256']:
            raise ValueError('inspector program differs from original')
        checked, _ = summarize_page(found, original.operations)
        previous = historical['pages'][number - 1]
        if any(checked[field] != previous[field] for field in
               ('page', 'program_sha256', 'operators', 'boundaries', 'safe_candidates', 'refused_boundaries')):
            raise ValueError('counts/program differ from PR #21')
        path = directory / f'page-{number:02d}.json'
        write(path, found)
        raw_files.append(dict(path=path.name, sha256=single.source_sha(path)))
        all_safe.extend(found['candidates'])
        all_refused.extend(found['refused'])
        pages.append(dict(page=number, program_sha256=found['program_sha256'],
            operators=found['operators'], boundaries=checked['boundaries'],
            refused_boundaries=len(found['refused']), inspection_seconds=round(elapsed, 3)))
        print(f'page {number}: {len(found["candidates"])} safe, {len(found["refused"])} refused', flush=True)
    tick = time.monotonic()
    groups = group_candidates(all_safe)
    grouping_seconds = time.monotonic() - tick
    stats = group_statistics(groups)
    if stats['candidates'] != 3016 or len(all_refused) != 11449:
        raise ValueError('unexpected corpus totals')
    for page in pages:
        page.update(group_statistics([group for group in groups if group['page'] == page['page']]))
    write(directory / 'groups.json', groups)
    raw_files.append(dict(path='groups.json', sha256=single.source_sha(directory / 'groups.json')))
    current_refusals = refusal_inventory(all_refused)
    if current_refusals['categories']['text_rendering_mode'] != 84 or current_refusals['categories']['other'] != 0:
        raise ValueError('known rendering-mode refusals were not explicitly categorized')
    integrity = dict(engine_unchanged=before == engine_files(),
        source_unchanged=single.source_sha(single.SOURCE) == single.SOURCE_SHA,
        providers_unchanged=all(single.source_sha(Path('C:/Windows/Fonts') / p['filename']) == p['sha256']
                                for p in env['providers'].values()),
        historical_summary_unchanged=single.source_sha(HISTORICAL) == historical_sha,
        all_page_programs_and_counts_match_pr21=True)
    if not all(integrity.values()):
        raise ValueError('input or historical evidence changed')
    result = dict(schema='pdfengine-boundary-review-1', status='measurement_complete', environment=env,
        source='evaluations/realpdf/corpus/lo_migration_ja.pdf', source_sha256=single.SOURCE_SHA, engine_digest=ENGINE,
        contract=dict(group_key=['page', 'z_order.semantics', 'z_order.prefix_paint_operators', 'z_order.suffix_paint_operators'],
            meaning='Same existing paint position only; no authority, state, scope, clip or CTM equivalence is inferred.',
            group_identity='SHA-256 of the canonical grouping key (first 24 hex digits), local to this inspected input revision; never a confirmation ID.',
            least_constrained_criteria=REVIEW_CRITERIA, criteria_meaning=REVIEW_MEANING,
            comparison='Lexicographic ascending (false before true, shallower depth first); every exact tie retained.',
            safety_ranking=False, automatic_selection=False, automatic_confirmation=False, geometry_used=False,
            limitation='A safe candidate has not thereby been reviewed as an empty destination.'),
        totals=dict(stats, operators=sum(page['operators'] for page in pages),
            boundaries=sum(page['boundaries'] for page in pages), refused_boundaries=len(all_refused)),
        pages=pages, examples=representative_examples(groups), current_refusal_categories=current_refusals['categories'],
        historical_summary=dict(path=str(HISTORICAL.relative_to(single.ROOT)).replace('\\', '/'), sha256=historical_sha,
            note='PR #21 historical other:84 is preserved; new tallies explicitly label text_rendering_mode:84 and other:0.'),
        integrity=integrity, seconds=dict(inspection=round(inspection_seconds, 3), grouping=round(grouping_seconds, 3),
            scan_and_artifacts=round(time.monotonic()-started, 3)),
        validation=dict(full_suite_rerun=False, external_lifecycle_rerun=False, renderer_evaluation_rerun=False,
            pdfs_written=0, inspection_calls=10, subagents_used=False),
        helper_sha256={name: single.source_sha(single.BASE / name) for name in HELPERS},
        raw_evidence=dict(directory=str(directory.relative_to(single.ROOT)).replace('\\', '/'), files=raw_files))
    write(directory / 'review-summary.json', result)
    print(json.dumps(dict(totals=stats, seconds=result['seconds'])), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    if Path(args.run).name != args.run or args.run in ('.', '..'):
        parser.error('--run must be a new directory name')
    run(single.BASE / 'runs' / args.run)
