"""External read-only check of review_continuation_geometry on the unmodified real PDF.

PR #25 annotates the formal boundary review with caller-provided bounds:
page-level emptiness (require_empty, once per review) and each candidate's
inherited clip fit (clip_contains). This evaluator calls only the public API
on page 10 of the untouched LibreOffice source and checks, against the PR #24
formal summary, that the structural review is unchanged by the annotation.

Scenario A uses the evaluator-fixed empty area [55,80,385,120] of page 10,
recorded since the PR #15 scope evaluation (README, scope_destination.py).
Scenario B is a negative control inside the logo's fill paint; its bbox log
and drawing evidence is recorded, not searched for. Scenario C (clip-free and
clipped candidates passing while other clipped candidates fail, for the same
bounds) is read from Scenario A's single result when that result shows it
naturally; no coordinates are searched and no second geometry call is made.

Read-only: no PDF written, no confirmation, renderer or lifecycle. Geometry is
never re-derived here; only API results are counted. Raw reviews stay under
runs/ (Git-ignored).
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

from pdfeditor import continuation, continuation_review
from pdfeditor.continuation import inspect_continuation_boundaries
from pdfeditor.continuation_review import review_continuation_boundaries, review_continuation_geometry
from evaluations.continuation import evaluate as single
from evaluations.continuation.boundary_review_formal import engine
from evaluations.continuation.depth_three_inspection import write


EXPECTED_HEAD = '4890f94081f0fd2b2cf9f84598c64c6e06852835'
EXPECTED_ENGINE = 'd3a995b05b7dff246875ebbaa849159675926657f699a9a38304f487b95dec29'
PR24 = single.BASE / 'boundary-review-formal-summary.json'
# REVIEW_CONTRACT as merged in PR #23 (pinned in tests/test_continuation_geometry_review.py).
PR23_REVIEW_CONTRACT_SHA256 = '23f1a00f6ba901357229ae55190c4778849ef5e3e62ba238c857f52bfccb3320'
PAGE = 10
SCENARIO_A = dict(bounds=[55, 80, 385, 120], provenance=(
    'Evaluator-fixed empty area of page 10 used since the PR #15 scope evaluation: README "10ページのscope境界" and '
    'region description (left of the logo, x >= 400.2; above the heading, y >= 141.07; no MuPDF bbox-log paint), '
    'scope_destination.py / scope_chain_candidates.py REGION page 10, and the PR #18 depth-2 record '
    '("require_empty passed, inside the clip"). Not derived here.'))
SCENARIO_B = dict(bounds=[520, 70, 530, 80], provenance=(
    'Negative control: a 10 x 10 pt box inside the bbox of the logo\'s gray background fill path '
    '[400.8,61.2,535.8,107.2] (and of its black outline fill [400.2,60.7,536.1,108.2]) on page 10. '
    'Chosen by hand from the bbox log and drawings recorded below; not a destination.'))
GEOMETRY_ROW_KEYS = {'destination_empty', 'clip_check_required', 'bounds_inside_inherited_clip', 'checks_passed'}
FORBIDDEN = ('best_geometry_candidate', 'recommended_candidate', 'preferred_candidate')


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def rows(review):
    return [row for group in review['groups'] for row in group['candidates']]


def identity(review):
    """Everything the structural contract fixes about membership and order."""
    return dict(group_ids=[g['group_id'] for g in review['groups']],
                boundary_ids=[g['boundary_ids'] for g in review['groups']],
                candidate_order=[r['boundary_id'] for r in rows(review)],
                minimal=[g['minimal_authority_review_candidates'] for g in review['groups']])


def stripped(review):
    groups = deepcopy(review['groups'])
    for group in groups:
        del group['geometry_checks_passed_count'], group['geometry_checks_failed_count']
        for row in group['candidates']:
            del row['geometry']
    return groups


def paint_evidence(bounds):
    """Existing paint whose MuPDF bbox-log or drawing box meets ``bounds`` (selection rationale only)."""
    box = pymupdf.Rect(*bounds)
    with pymupdf.open(single.SOURCE) as doc:
        page = doc[PAGE - 1]
        log = [dict(kind=kind, bbox=[round(v, 2) for v in rect]) for kind, rect in page.get_bboxlog()
               if box.intersects(pymupdf.Rect(rect))]
        drawings = [dict(type=d['type'], rect=[round(v, 2) for v in d['rect']],
                         fill=[round(v, 4) for v in d['fill']] if d.get('fill') else None, items=len(d['items']))
                    for d in page.get_drawings() if box.intersects(d['rect'])]
        images = [[round(v, 2) for v in info['bbox']] for info in page.get_image_info()
                  if box.intersects(pymupdf.Rect(info['bbox']))]
    return dict(bbox_log=log, drawings=drawings, images=images)


class Counters:
    """Counts engine calls made during one geometry review; wraps, never replaces, the engine functions."""

    def __init__(self):
        self.counts = dict(require_empty=0, inspect=0, clip_contains=0, confirmation=0, pdf_writes=0, renders=0)
        self.saved = []

    def wrap(self, owner, name, key, forbid=False):
        original = getattr(owner, name)

        def wrapped(*args, **kwargs):
            self.counts[key] += 1
            if forbid:
                raise RuntimeError(f'{name} must not be called by a read-only geometry review')
            return original(*args, **kwargs)
        self.saved.append((owner, name, original))
        setattr(owner, name, wrapped)

    def __enter__(self):
        self.wrap(continuation_review, 'require_empty', 'require_empty')
        self.wrap(continuation_review, '_inspect', 'inspect')
        self.wrap(continuation_review, 'clip_contains', 'clip_contains')
        self.wrap(continuation, 'confirm_continuation_destination', 'confirmation', forbid=True)
        for name in ('save', 'ez_save', 'saveIncr', 'write'):
            if hasattr(pymupdf.Document, name):
                self.wrap(pymupdf.Document, name, 'pdf_writes')
        self.wrap(pypdf.PdfWriter, 'write', 'pdf_writes')
        self.wrap(pymupdf.Page, 'get_pixmap', 'renders')
        return self

    def __exit__(self, *exc):
        for owner, name, original in reversed(self.saved):
            setattr(owner, name, original)


def scenario(name, spec, baseline, structural, evidence=None):
    tick = time.monotonic()
    with Counters() as counters:
        found = review_continuation_geometry(single.SOURCE, PAGE, spec['bounds'])
    seconds = time.monotonic() - tick
    geometry, all_rows = found['geometry'], rows(found)
    passed = [g['geometry_checks_passed_count'] for g in found['groups']]
    clipped = [r for r in all_rows if r['geometry']['clip_check_required']]
    result = dict(page=PAGE, bounds=found['bounds'], bounds_provenance=spec['provenance'],
        candidate_count=found['candidate_count'], group_count=found['group_count'],
        destination_empty=geometry['destination_empty'], empty_check_error=geometry['empty_check_error'],
        clip_checked_candidates=geometry['clip_checked_candidates'],
        clip_inside_candidates=sum(r['geometry']['bounds_inside_inherited_clip'] for r in clipped),
        clip_outside_candidates=sum(not r['geometry']['bounds_inside_inherited_clip'] for r in clipped),
        clip_free_candidates=len(all_rows) - len(clipped),
        checks_passed_candidates=geometry['checks_passed_candidates'],
        checks_failed_candidates=geometry['checks_failed_candidates'],
        groups_all_pass=sum(p == g['candidate_count'] for p, g in zip(passed, found['groups'])),
        groups_mixed=sum(0 < p < g['candidate_count'] for p, g in zip(passed, found['groups'])),
        groups_all_fail=sum(p == 0 for p in passed),
        engine_calls=dict(counters.counts), seconds=round(seconds, 3))
    if evidence is not None:
        result['paint_evidence'] = evidence
    contract = found['contract']
    checks = dict(
        program_matches_pr24=found['program_sha256'] == baseline['program_sha256'],
        counts_match_pr24=(found['candidate_count'], found['group_count'])
            == (baseline['safe_candidates'], baseline['groups']),
        counts_match_structural=(found['candidate_count'], found['group_count'])
            == (structural['candidate_count'], structural['group_count']),
        membership_order_and_minimal_match_structural=identity(found) == identity(structural),
        no_candidate_removed=len(all_rows) == found['candidate_count'] == structural['candidate_count'],
        row_geometry_is_exactly_the_contract=all(set(r['geometry']) == GEOMETRY_ROW_KEYS for r in all_rows),
        checks_passed_is_empty_and_clip=all(r['geometry']['checks_passed'] is
            (r['geometry']['destination_empty'] and r['geometry']['bounds_inside_inherited_clip']) for r in all_rows),
        emptiness_common_to_every_candidate=all(r['geometry']['destination_empty'] is geometry['destination_empty']
                                                for r in all_rows),
        clip_check_only_with_clip_constraint=all(r['geometry']['clip_check_required'] is
            r['has_rectangular_clip_constraint'] for r in all_rows),
        clip_free_never_refused_by_clip=all(r['geometry']['bounds_inside_inherited_clip'] for r in all_rows
                                            if not r['geometry']['clip_check_required']),
        group_counts_consistent=all(g['geometry_checks_passed_count'] + g['geometry_checks_failed_count']
                                    == g['candidate_count'] for g in found['groups']),
        totals_consistent=geometry['checks_passed_candidates'] + geometry['checks_failed_candidates']
            == found['candidate_count'],
        one_inspection_one_emptiness_check=counters.counts['inspect'] == 1 and counters.counts['require_empty'] == 1,
        clip_contains_only_for_clip_candidates=counters.counts['clip_contains'] == len(clipped),
        read_only_calls=counters.counts['confirmation'] == counters.counts['pdf_writes'] == counters.counts['renders'] == 0,
        contract_flags=contract['read_only'] is contract['geometry_used'] is True and all(
            contract[k] is False for k in ('generated_ink_evaluated', 'automatic_selection', 'safety_ranking',
                                           'recommendation', 'automatic_confirmation')),
        no_selection_fields=not any(k in found or any(k in g for g in found['groups']) for k in FORBIDDEN))
    return found, result, checks


def run(directory):
    started = time.monotonic()
    directory = Path(directory)
    if directory.exists():
        raise ValueError('refusing to repeat or overwrite a run')
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=single.ROOT, text=True).strip()
    status = subprocess.check_output(['git', 'status', '--porcelain'], cwd=single.ROOT, text=True)
    source_before = single.source_sha(single.SOURCE)
    before = engine()
    start = dict(head=head, git_status_clean_except_evaluator=all(
            line.endswith('evaluations/continuation/geometry_review_external.py') for line in status.splitlines()),
        head_matches=head == EXPECTED_HEAD, engine_matches=before['engine_digest'] == EXPECTED_ENGINE,
        source_matches=source_before == single.SOURCE_SHA)
    if not all(start.values()):
        raise SystemExit(f'start state differs from the expected PR #25 state: {start}')
    pr24 = json.loads(PR24.read_text(encoding='utf-8'))
    baseline = next(p for p in pr24['pages'] if p['page'] == PAGE)
    directory.mkdir(parents=True)

    # Clip rectangles of the safe candidates, read once, to decide whether Scenario C exists naturally.
    tick = time.monotonic()
    inspection = inspect_continuation_boundaries(single.SOURCE, PAGE)
    clip_of = {c['boundary_id']: tuple(c['clip_constraint']['rectangle']) if 'clip_constraint' in c else None
               for c in inspection['candidates']}
    inspection_seconds = time.monotonic() - tick

    # The one structural comparison call.
    tick = time.monotonic()
    structural = review_continuation_boundaries(single.SOURCE, PAGE)
    structural_seconds = time.monotonic() - tick
    write(directory / 'structural-review-page-10.json', structural)

    evidence_b = paint_evidence(SCENARIO_B['bounds'])
    found_a, result_a, checks_a = scenario('A', SCENARIO_A, baseline, structural)
    checks_a.update(destination_empty=result_a['destination_empty'] is True and result_a['empty_check_error'] is None,
                    stripped_groups_equal_structural_review=stripped(found_a) == structural['groups'])
    found_b, result_b, checks_b = scenario('B', SCENARIO_B, baseline, structural, evidence_b)
    checks_b.update(destination_not_empty=result_b['destination_empty'] is False
                        and isinstance(result_b['empty_check_error'], str) and bool(result_b['empty_check_error']),
                    no_candidate_passes=result_b['checks_passed_candidates'] == 0,
                    paint_evidence_present=bool(evidence_b['bbox_log']))
    write(directory / 'geometry-review-a.json', found_a)
    write(directory / 'geometry-review-b.json', found_b)

    # Scenario C from Scenario A's rows, joined with the inspected clip rectangles (no geometry recomputed).
    rectangles = {}
    for row in rows(found_a):
        key = clip_of[row['boundary_id']]
        entry = rectangles.setdefault(key, dict(rectangle=list(key) if key else None, candidates=0,
                                                checks_passed=0, bounds_inside_inherited_clip=0))
        entry['candidates'] += 1
        entry['checks_passed'] += row['geometry']['checks_passed']
        entry['bounds_inside_inherited_clip'] += row['geometry']['bounds_inside_inherited_clip']
    a_rows = rows(found_a)
    natural = (result_a['destination_empty']
               and any(not r['geometry']['clip_check_required'] and r['geometry']['checks_passed'] for r in a_rows)
               and any(r['geometry']['clip_check_required'] and r['geometry']['checks_passed'] for r in a_rows)
               and any(r['geometry']['clip_check_required'] and not r['geometry']['checks_passed'] for r in a_rows))
    result_c = dict(natural_clip_differential_case=natural, executed=natural,
        method='read from the single Scenario A review (same page, same bounds); no second geometry call and no '
               'coordinate search', page=PAGE, bounds=result_a['bounds'], bounds_provenance='Scenario A',
        clip_rectangles=sorted(rectangles.values(), key=lambda e: (e['rectangle'] is not None, e['rectangle'] or [])),
        clip_free_passing=sum(not r['geometry']['clip_check_required'] and r['geometry']['checks_passed'] for r in a_rows),
        clipped_passing=sum(r['geometry']['clip_check_required'] and r['geometry']['checks_passed'] for r in a_rows),
        clipped_failing_outside_clip=sum(r['geometry']['clip_check_required']
                                         and not r['geometry']['bounds_inside_inherited_clip'] for r in a_rows),
        groups_mixed=result_a['groups_mixed'])
    checks_c = dict(inspected_program_matches=inspection['program_sha256'] == structural['program_sha256'],
                    every_candidate_joined=set(clip_of) == {r['boundary_id'] for r in a_rows},
                    clip_rectangle_presence_matches_clip_check=all(
                        (clip_of[r['boundary_id']] is not None) is r['geometry']['clip_check_required'] for r in a_rows))

    structural_contract = dict(schema=structural['schema'],
        contract_sha256=continuation_review._digest(structural['contract']),
        matches_pr23=continuation_review._digest(structural['contract']) == PR23_REVIEW_CONTRACT_SHA256,
        geometry_used=structural['contract']['geometry_used'])
    after = engine()
    source_after = single.source_sha(single.SOURCE)
    checks = dict(scenario_a=checks_a, scenario_b=checks_b, scenario_c=checks_c,
        structural=dict(pr23_contract_unchanged=structural_contract['matches_pr23'] and not structural_contract['geometry_used'],
                        pr24_totals_are_3016_to_834=(pr24['totals']['candidates'], pr24['totals']['groups']) == (3016, 834),
                        pr24_status_complete=pr24['status'] == 'complete'),
        source_unchanged=source_before == source_after == single.SOURCE_SHA,
        engine_unchanged=before == after)
    flat = [v for group in checks.values() for v in (group.values() if isinstance(group, dict) else [group])]
    result = dict(schema='pdfengine-geometry-review-external-1',
        status='complete' if all(flat) else 'mismatch',
        head=head, engine_digest=before['engine_digest'], source=str(single.SOURCE.relative_to(single.ROOT)).replace('\\', '/'),
        source_sha256=source_before,
        environment=dict(python=sys.version.split()[0], platform=platform.platform(), pymupdf=pymupdf.VersionBind,
                         pypdf=pypdf.__version__, engine_digest=before['engine_digest'],
                         continuation_review_py_sha256=before['engine_sha256']['continuation_review.py'],
                         continuation_py_sha256=before['engine_sha256']['continuation.py']),
        start=start,
        api='pdfeditor.continuation_review.review_continuation_geometry',
        pr24_structural_baseline=dict(summary=str(PR24.relative_to(single.ROOT)).replace('\\', '/'),
            summary_sha256=single.source_sha(PR24), status=pr24['status'],
            totals=dict(candidates=pr24['totals']['candidates'], groups=pr24['totals']['groups']),
            page=dict(page=PAGE, program_sha256=baseline['program_sha256'], safe_candidates=baseline['safe_candidates'],
                      groups=baseline['groups']),
            structural_contract=structural_contract,
            note='Geometry review adds a separate API; the formal structural review (3016 -> 834) is unchanged.'),
        scenario_a=result_a, scenario_b=result_b, scenario_c=result_c,
        checks=checks, source_unchanged=checks['source_unchanged'], engine_unchanged=checks['engine_unchanged'],
        validation=dict(geometry_review_calls=2, structural_review_calls=1, inspection_calls=1, pages=[PAGE],
            pdf_writes=0, confirmations=0, renderer_evaluations=0, lifecycle_executions=0,
            page_wide_geometry_scan=False, full_suite_run=False, engine_changed=False),
        seconds=dict(clip_inspection=round(inspection_seconds, 3), structural_review=round(structural_seconds, 3),
                     scenario_a=result_a['seconds'], scenario_b=result_b['seconds'],
                     total=round(time.monotonic() - started, 3)))
    write(directory / 'geometry-review-external-summary.json', result)
    print(json.dumps(dict(status=result['status'], a={k: result_a[k] for k in (
        'destination_empty', 'clip_checked_candidates', 'clip_inside_candidates', 'checks_passed_candidates',
        'checks_failed_candidates', 'groups_all_pass', 'groups_mixed', 'groups_all_fail')},
        b={k: result_b[k] for k in ('destination_empty', 'empty_check_error', 'checks_passed_candidates')},
        c={k: result_c[k] for k in ('natural_clip_differential_case', 'clip_free_passing', 'clipped_passing',
                                    'clipped_failing_outside_clip')},
        checks=checks, seconds=result['seconds']), indent=2, ensure_ascii=False), flush=True)
    if result['status'] != 'complete':
        raise SystemExit('geometry review external validation did not complete')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    if Path(args.run).name != args.run or args.run in ('.', '..'):
        parser.error('--run must be a new directory name')
    run(single.BASE / 'runs' / args.run)
