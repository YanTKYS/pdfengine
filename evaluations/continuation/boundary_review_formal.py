"""Re-check PR #22 on the unchanged real PDF through the formal read-only review API.

Each of the ten pages is inspected exactly once (include_refused=True). That
record is serialized as PR #22 serialized it and compared with the published
raw-evidence SHA-256, so the inspector output (candidates, boundary IDs,
order, authority, refusals) is shown unchanged. The same safe candidates are
reviewed by pdfeditor.continuation_review (the code path of
review_continuation_boundaries after its one inspection) and grouped by the
historical PR #22 prototype, loaded from its Git blob (commit e82a9cc) and
checked against its recorded helper SHA-256. Membership, candidate IDs,
paint positions, review attributes and minimal sets must agree; the
prototype groups must reproduce the published groups.json SHA-256.

Read-only: no PDF, geometry, confirmation or renderer. Raw records stay under
runs/ (Git-ignored); the compact report is written there too.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

import pymupdf
import pypdf

from pdfeditor.continuation import inspect_continuation_boundaries
from pdfeditor import continuation_review
from pdfeditor.continuation_review import group_continuation_boundary_candidates
from evaluations.continuation import evaluate as single
from evaluations.continuation import boundary_review as wrapper
from evaluations.continuation.depth_three_inspection import ENGINE as PR22_ENGINE, write


PR22 = single.BASE / 'boundary-review-summary.json'
PR22_COMMIT = 'e82a9ccbbef56487dcb19b957b841a876ba2897b'
PROTOTYPE = 'evaluations/continuation/boundary_review.py'
EXPECTED = dict(candidates=3016, groups=834, singleton_groups=81, multiple_candidate_groups=753, maximum_group_size=6)


def serialized_sha(value):
    """SHA-256 of the bytes depth_three_inspection.write would store (as PR #22 stored its raw evidence)."""
    return hashlib.sha256((json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')).hexdigest()


def engine():
    files = {p.name: single.source_sha(p) for p in sorted((single.ROOT / 'pdfeditor').glob('*.py'))}
    previous = {k: v for k, v in files.items() if k != 'continuation_review.py'}
    digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return dict(engine_digest=digest(files), engine_sha256=files, pr22_files_digest=digest(previous))


def load_prototype(expected_sha):
    data = subprocess.check_output(['git', 'show', f'{PR22_COMMIT}:{PROTOTYPE}'], cwd=single.ROOT)
    if hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError('historical prototype differs from its recorded SHA-256')
    spec = importlib.util.spec_from_loader('pr22_boundary_review', loader=None)
    module = importlib.util.module_from_spec(spec)
    exec(compile(data, f'{PR22_COMMIT[:7]}:{PROTOTYPE}', 'exec'), module.__dict__)
    return module


def comparable_formal(group):
    return dict(page=group['page'], z_order=group['z_order'], boundary_ids=group['boundary_ids'],
        attributes=[[row['boundary_id'], row['review_attributes']['rectangular_clip'],
                     row['review_attributes']['ctm_compensation'], row['review_attributes']['q_depth']]
                    for row in group['candidates']],
        minimal=group['minimal_authority_review_candidates'])


def comparable_prototype(group):
    return dict(page=group['page'], z_order=group['z_order'], boundary_ids=group['boundary_ids'],
        attributes=[[row['boundary_id'], row['has_rectangular_clip_constraint'], row['has_ctm_compensation'],
                     row['q_depth']] for row in group['candidates']],
        minimal=group['least_constrained_candidates'])


def run(directory):
    started = time.monotonic()
    directory = Path(directory)
    if directory.exists():
        raise ValueError('refusing to repeat or overwrite a scan')
    published = json.loads(PR22.read_text(encoding='utf-8'))
    raw = {f['path']: f['sha256'] for f in published['raw_evidence']['files']}
    if single.source_sha(single.SOURCE) != single.SOURCE_SHA:
        raise ValueError('source differs from the reviewed original')
    before = engine()
    if before['pr22_files_digest'] != PR22_ENGINE:
        raise ValueError('an existing engine file differs from the PR #22 engine')
    prototype = load_prototype(published['helper_sha256']['boundary_review.py'])
    directory.mkdir(parents=True)
    reviews, safe, pages, inspection_seconds, review_seconds = [], [], [], 0.0, 0.0
    for number in range(1, 11):
        tick = time.monotonic()
        found = inspect_continuation_boundaries(single.SOURCE, number, include_refused=True)
        inspected = time.monotonic() - tick
        tick = time.monotonic()
        reviewed = continuation_review._review(number, found)
        reviewing = time.monotonic() - tick
        inspection_seconds, review_seconds = inspection_seconds + inspected, review_seconds + reviewing
        inspector_sha = serialized_sha(found)
        write(directory / f'review-page-{number:02d}.json', reviewed)
        reviews.append(reviewed)
        safe.extend(found['candidates'])
        pages.append(dict(page=number, program_sha256=found['program_sha256'], safe_candidates=len(found['candidates']),
            refused_boundaries=len(found['refused']), groups=reviewed['group_count'],
            inspector_record_sha256=inspector_sha,
            inspector_record_matches_pr22=inspector_sha == raw[f'page-{number:02d}.json'],
            inspection_seconds=round(inspected, 3), review_seconds=round(reviewing, 3)))
        print(f'page {number}: {len(found["candidates"])} safe -> {reviewed["group_count"]} groups', flush=True)
    formal = [group for page in reviews for group in page['groups']]
    tick = time.monotonic()
    pooled = group_continuation_boundary_candidates(safe)
    pooled_seconds = time.monotonic() - tick
    historical = prototype.group_candidates(safe)
    wrapped = wrapper.group_candidates(safe)
    stats = prototype.group_statistics(historical)
    sizes = [group['candidate_count'] for group in formal]
    totals = dict(candidates=sum(sizes), groups=len(formal), singleton_groups=sizes.count(1),
        multiple_candidate_groups=sum(size > 1 for size in sizes), maximum_group_size=max(sizes),
        minimal_authority_review_candidates=sum(len(g['minimal_authority_review_candidates']) for g in formal),
        groups_with_one_minimal_candidate=sum(len(g['minimal_authority_review_candidates']) == 1 for g in formal))
    ids = [group['group_id'] for group in formal]
    checks = dict(
        inspector_records_match_pr22=all(page['inspector_record_matches_pr22'] for page in pages),
        prototype_groups_match_published_groups_json=serialized_sha(historical) == raw['groups.json'],
        historical_wrapper_bytes_match_prototype=serialized_sha(wrapped) == serialized_sha(historical),
        pooled_pure_helper_matches_per_page_reviews=pooled == formal,
        formal_membership_attributes_and_minimal_match_prototype=
            [comparable_formal(g) for g in formal] == [comparable_prototype(g) for g in historical],
        expected_totals=all(totals[k] == v for k, v in EXPECTED.items()),
        prototype_totals_match_published=stats == {k: published['totals'][k] for k in stats},
        minimal_candidates_match_published=totals['minimal_authority_review_candidates']
            == published['totals']['least_constrained_candidates'],
        group_ids_unique_and_revision_scoped=len(set(ids)) == len(ids) and all(i.startswith('review-group-') for i in ids),
        source_unchanged=single.source_sha(single.SOURCE) == single.SOURCE_SHA,
        engine_unchanged_during_scan=engine() == before)
    result = dict(schema='pdfengine-boundary-review-formal-1', status='complete' if all(checks.values()) else 'mismatch',
        source='evaluations/realpdf/corpus/lo_migration_ja.pdf', source_sha256=single.SOURCE_SHA,
        environment=dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=single.ROOT, text=True).strip(),
            python=sys.version.split()[0], platform=platform.platform(), pymupdf=pymupdf.VersionBind,
            pypdf=pypdf.__version__, **before),
        api=['pdfeditor.continuation_review.review_continuation_boundaries',
             'pdfeditor.continuation_review.group_continuation_boundary_candidates'],
        totals=totals, pages=pages, checks=checks,
        compared_with=dict(summary=str(PR22.relative_to(single.ROOT)).replace('\\', '/'),
            summary_sha256=single.source_sha(PR22), prototype=f'{PR22_COMMIT}:{PROTOTYPE}',
            compared=['group membership', 'candidate IDs', 'paint position', 'review attributes', 'minimal set'],
            not_compared='group IDs: formal IDs cover the program SHA-256, PR #22 IDs did not'),
        seconds=dict(inspection=round(inspection_seconds, 3), review=round(review_seconds, 3),
            pooled_grouping=round(pooled_seconds, 3), total=round(time.monotonic() - started, 3)),
        validation=dict(inspection_calls=10, pdfs_written=0, renderer_evaluation=False, confirmations=0))
    write(directory / 'formal-review-summary.json', result)
    print(json.dumps(dict(status=result['status'], totals=totals, checks=checks, seconds=result['seconds'],
                          engine_digest=before['engine_digest']), indent=2), flush=True)
    if result['status'] != 'complete':
        raise SystemExit('formal review differs from PR #22 evidence')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    if Path(args.run).name != args.run or args.run in ('.', '..'):
        parser.error('--run must be a new directory name')
    run(single.BASE / 'runs' / args.run)
