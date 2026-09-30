"""Read-only inventory of natural page-program boundaries in the reviewed PDF.

This helper never writes a PDF or confirms a destination. A safe depth-3
boundary requires a separate visual/geometry review before focused evaluation.
Raw inspector records stay under runs/; only compact statistics are published.
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
from evaluations.continuation.scope_chain_destination import engine_files, start_record


BASE_HEAD = '44c4ac991dd841c6231a9e24ea891747fa471bfb'
ENGINE = '24b7dfb30a79b25e1c0ee7c5bb16a16aedd4de35722d2bf3c738e678648a196a'
CATEGORIES = {
    'extgstate': {'extgstate'},
    'pending_path': {'pending-path'},
    'pending_clip': {'pending-clip'},
    'unproven_or_nonrectangular_clip': {'unproven-clip', 'nonrectangular-clip'},
    'marked_content': {'inside-marked-content'},
    'text_object': {'inside-text-object'},
    'text_rendering_mode': {'text-rendering-mode'},
    'bx_ex': {'inside-compatibility-section'},
    'transparency': {'transparency'},
    'ctm_proof_failure': {'singular-ctm', 'nonfinite-ctm', 'numerically-unstable-ctm',
                          'unproven-ctm', 'unproven-ctm-compensation'},
}


def write(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def histogram(rows):
    counts = Counter(row['scope']['q_depth'] for row in rows)
    return {str(depth): counts[depth] for depth in range(max(4, max(counts, default=0)) + 1)}


def reasons(rows):
    return dict(sorted(Counter(reason for row in rows for reason in set(row['reasons'])).items()))


def refusal_inventory(rows):
    """Counts are boundary counts; reason/category labels may overlap."""
    refused = [row for row in rows if row['status'] == 'refused']
    groups = {name: [row for row in refused if labels.intersection(row['reasons'])]
              for name, labels in CATEGORIES.items()}
    groups['q_depth_4_plus'] = [row for row in refused if row['scope']['q_depth'] >= 4]
    classified = set().union(*CATEGORIES.values())
    groups['other'] = [row for row in refused if
        set(row['reasons']) - classified - ({'nested-graphics-state-save'} if row['scope']['q_depth'] >= 4 else set())]
    combinations = Counter(tuple(sorted(set(row['reasons']))) for row in refused)
    return dict(refused_boundaries=len(refused), reason_counts=reasons(refused),
        sole_reason_counts=reasons([row for row in refused if len(set(row['reasons'])) == 1]),
        outside_text_object_reason_counts=reasons([row for row in refused if not row['scope']['text_object']]),
        categories={name: len(values) for name, values in groups.items()},
        reason_combinations=[dict(reasons=list(key), boundaries=count)
                             for key, count in sorted(combinations.items(), key=lambda item: (-item[1], item[0]))])


def summarize_page(found, raw_operations):
    safe, refused = found['candidates'], found['refused']
    rows = sorted(safe + refused, key=lambda row: row['ordinal'])
    if len(rows) != found['operators'] - 1 or len(refused) != found['refused_boundaries']:
        raise ValueError('boundary/operator totals disagree')
    if [row['ordinal'] for row in rows] != list(range(len(rows))):
        raise ValueError('missing or duplicate boundary ordinal')
    if any(row['status'] != 'safe' or row['reasons'] for row in safe):
        raise ValueError('invalid safe partition')
    if any(row['status'] != 'refused' or not row['reasons'] for row in refused):
        raise ValueError('invalid refused partition')
    if reasons(refused) != found['refusal_reasons']:
        raise ValueError('reason counts disagree with engine inventory')
    # Independently count q/Q from pypdf's original decoded /Contents.
    depth, depths = 0, []
    for _, operator in raw_operations:
        depth += (operator == b'q') - (operator == b'Q')
        if depth < 0:
            raise ValueError('unbalanced original q/Q')
        depths.append(depth)
    if depth or len(depths) != found['operators']:
        raise ValueError('original operator count or q/Q balance differs')
    if depths[:-1] != [row['scope']['q_depth'] for row in rows]:
        raise ValueError('original q/Q depths differ from inspector')
    special = lambda minimum, maximum: [row for row in rows
        if minimum <= row['scope']['q_depth'] <= maximum]
    depth3 = special(3, 3)
    deeper = special(4, max(depths, default=0))
    counts = lambda values: dict(boundaries=len(values), safe=sum(row['status'] == 'safe' for row in values),
                               refused=sum(row['status'] == 'refused' for row in values), refusal_reason_counts=reasons(values))
    return dict(page=found['page'], program_sha256=found['program_sha256'], operators=found['operators'],
        boundaries=len(rows), safe_candidates=len(safe), refused_boundaries=len(refused),
        max_q_depth=max(depths, default=0), q_depth_boundary_counts=histogram(rows),
        q_depth_safe_counts=histogram(safe), q_depth_refused_counts=histogram(refused),
        refusal_reason_counts=reasons(refused), depth_3=counts(depth3), depth_4_plus=counts(deeper),
        original_pypdf_depths_match=True), rows


def inspect_all(directory):
    started = time.monotonic()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'inspection.json').exists() or list(directory.glob('page-*.json')):
        raise ValueError('refusing to overwrite or repeat a completed/partial inspection')
    start = directory / 'start.json'
    environment = json.loads(start.read_text(encoding='utf-8')) if start.exists() else start_record()
    if not start.exists():
        write(start, environment)
    before = engine_files()
    digest = hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
    if environment['engine_digest'] != digest or digest != ENGINE:
        raise ValueError('not the reviewed PR #20 engine')
    if single.source_sha(single.SOURCE) != single.SOURCE_SHA:
        raise ValueError('original PDF differs')
    for provider in environment['providers'].values():
        if single.source_sha(Path('C:/Windows/Fonts') / provider['filename']) != provider['sha256']:
            raise ValueError('reviewed provider differs')
    reader = PdfReader(single.SOURCE)
    if len(reader.pages) != 10:
        raise ValueError('expected the ten-page original')
    pages, all_rows, raw_files = [], [], []
    for page in range(1, len(reader.pages) + 1):
        tick = time.monotonic()
        found = inspect_continuation_boundaries(single.SOURCE, page, include_refused=True)
        original = reader.pages[page - 1].get_contents()
        if hashlib.sha256(original.get_data()).hexdigest() != found['program_sha256']:
            raise ValueError('inspector did not use the original decoded program')
        summary, rows = summarize_page(found, original.operations)
        path = directory / f'page-{page:02d}.json'
        write(path, found)
        raw_files.append(dict(path=path.name, sha256=single.source_sha(path)))
        summary['seconds'] = round(time.monotonic() - tick, 3)
        pages.append(summary)
        all_rows.extend(rows)
        print(json.dumps(summary), flush=True)
    depth3 = [row for row in all_rows if row['scope']['q_depth'] == 3]
    safe3 = [row for row in depth3 if row['status'] == 'safe']
    result = dict(schema='pdfengine-depth-three-inspection-1', status='inspection_complete',
        environment=environment, reviewed_base=BASE_HEAD, source='evaluations/realpdf/corpus/lo_migration_ja.pdf',
        source_sha256=single.SOURCE_SHA, engine_digest=digest,
        boundary_definition='Between consecutive operators of the original merged page /Contents; page entry and after-final positions excluded; Form XObject interiors are not page-program boundaries.',
        count_definition='Each reason/category counts distinct refused boundaries; labels can overlap. Histograms include explicit zero bins through depth 4.',
        pages=pages,
        totals=dict(pages=len(pages), operators=sum(page['operators'] for page in pages), boundaries=len(all_rows),
            safe_candidates=sum(page['safe_candidates'] for page in pages),
            refused_boundaries=sum(page['refused_boundaries'] for page in pages),
            max_q_depth=max(page['max_q_depth'] for page in pages), q_depth_boundary_counts=histogram(all_rows),
            q_depth_safe_counts=histogram([row for row in all_rows if row['status'] == 'safe']),
            q_depth_refused_counts=histogram([row for row in all_rows if row['status'] == 'refused'])),
        depth_3=dict(boundaries=len(depth3), safe_candidates=len(safe3), refused=len(depth3)-len(safe3),
            refusal_reason_counts=reasons(depth3), details=depth3),
        depth_4_plus=dict(boundaries=sum(row['scope']['q_depth'] >= 4 for row in all_rows),
            safe_candidates=sum(row['scope']['q_depth'] >= 4 and row['status'] == 'safe' for row in all_rows),
            refusal_reason_counts=reasons([row for row in all_rows if row['scope']['q_depth'] >= 4])),
        next_barriers=refusal_inventory(all_rows),
        focused_evaluation=dict(performed=False, status='pending_candidate_review' if safe3 else 'not_applicable',
            reason='safe_depth_3_requires_visual_and_geometry_review' if safe3 else
                   'no_natural_depth_3_boundary' if not depth3 else 'all_depth_3_boundaries_refused',
            page=None, boundary_id=None, destination=None, mupdf=None, poppler=None,
            state_restoration=None, negative_control=None),
        integrity=dict(engine_unchanged=before == engine_files(), source_unchanged=single.source_sha(single.SOURCE) == single.SOURCE_SHA,
            providers_unchanged=all(single.source_sha(Path('C:/Windows/Fonts') / p['filename']) == p['sha256']
                for p in environment['providers'].values()), all_original_programs_and_depths_match=True),
        validation=dict(full_pytest_rerun=False, prior_external_evaluations_rerun=False,
                        pdfs_written=0, source_modified=False, subagents_used=False),
        raw_evidence=dict(directory=str(directory.relative_to(single.ROOT)).replace('\\', '/'), files=raw_files),
        inspector_sha256=single.source_sha(Path(__file__)), seconds=round(time.monotonic()-started, 3))
    if not all(result['integrity'].values()):
        raise ValueError('inputs or engine changed')
    write(directory / 'inspection.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, help='new run directory name, without path separators')
    args = parser.parse_args()
    if Path(args.run).name != args.run or args.run in ('.', '..'):
        parser.error('--run must be a directory name')
    inspect_all(single.BASE / 'runs' / args.run)
