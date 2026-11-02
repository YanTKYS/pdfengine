"""Practical success with the search and copy of the edited text (issue #116): what passes, and what never does."""
import pymupdf
import pytest

from test_search_copy import AT, BASELINES, EXPECTED, FRAME, LINE1, LINE2, LINE3, NEW, page, run
from prototypes.paragraph_frame import eval_records as er
from prototypes.paragraph_frame import smoke_evaluate
from prototypes.paragraph_frame.approved_audit import _quality
from prototypes.paragraph_frame.search_copy import measure, search_on_target, verified

ONE = '申請書を窓口'  # drawn on line 2 alone


def measures(**x):
    """Measures as search_copy.measure gives them for a replacement on ``lines`` lines, every one passed unless given."""
    lines = x.pop('lines', 2)
    m = dict(search_full=lines == 1, search_per_line=None if lines == 1 else True, extraction_mupdf=True, extraction_poppler=True,
             copy_equivalent=True, clipboard_manual=None, search_full_hits=dict(total=1, on_target=1, elsewhere=0, lines=lines))
    m.update(x)
    return m


# --- the verdict on the measures --------------------------------------------

def test_over_lines_the_four_measures_verify_the_edited_text_without_one_search():
    v = verified(measures(search_full=False))
    assert v['result'] is True and v['route'] == 'lines' and v['search_full'] is False and 'not guaranteed' in v['note']
    # The clipboard is never assumed: it is not read.
    assert v['required'] == ['search_per_line', 'extraction_mupdf', 'extraction_poppler', 'copy_equivalent']


@pytest.mark.parametrize('lines, broken', [
    (2, 'search_per_line'), (2, 'extraction_mupdf'), (2, 'extraction_poppler'), (2, 'copy_equivalent'),
    (1, 'search_full'), (1, 'extraction_poppler'), (1, 'copy_equivalent'),
])
def test_any_required_measure_false_fails(lines, broken):
    v = verified(measures(lines=lines, **{broken: False}))
    assert v['result'] is False and v['failed'] == [broken]


@pytest.mark.parametrize('lines, missing', [(2, 'search_per_line'), (2, 'copy_equivalent'), (1, 'search_full'), (1, 'extraction_mupdf')])
def test_a_required_measure_not_made_is_unverified(lines, missing):
    v = verified(measures(lines=lines, **{missing: None}))
    assert v['result'] is None and v['missing'] == [missing]


def test_no_measures_a_deletion_and_drawn_lines_that_are_not_the_paragraph():
    assert verified(None)['result'] is None and verified(dict(error='ValueError: x'))['result'] is None
    assert verified(dict(reason='no new text to search for (a deletion)'))['result'] == 'not applicable'
    bad = dict(search_full=False, search_per_line=False, extraction_mupdf=False, extraction_poppler=False, copy_equivalent=False,
               reason='the drawn lines are not the expected paragraph: x')
    assert verified(bad)['result'] is False and search_on_target(bad)[0] is False


def test_one_search_over_lines_is_recorded_not_required():
    # A Latin replacement over lines found by one search (MuPDF reads the line break as a space): recorded.
    assert search_on_target(measures(search_full=True))[0] is True and verified(measures(search_full=True))['result'] is True
    assert search_on_target(measures(search_full=False))[0] is None


# --- measured on fixtures: the search check before and after issue #116 ------

def _source(tmp_path):
    return page(tmp_path, [LINE1, LINE2, LINE3], name='source.pdf')


def quality(tmp_path, out, new=ONE, old=None):
    """approved_audit._quality of ``out`` against the source paragraph, ``new`` replacing ``old`` in it."""
    source = _source(tmp_path)
    old = old or new
    m = measure(out, 1, EXPECTED, new, EXPECTED.index(new), FRAME, BASELINES)
    frame = pymupdf.Rect(FRAME)
    return _quality(source, out, 1, dict(replace=(old, new)), frame, frame, [], m)


def test_a_hit_elsewhere_on_the_page_no_longer_passes_search(tmp_path):
    # On its line the replacement is drawn with a gap (one search misses it there); the same words stand elsewhere.
    out = page(tmp_path, [LINE1, '申請書　を窓口で配布しま', LINE3], extra=[(40, 200, ONE)])
    q = quality(tmp_path, out)
    assert q['legacy']['search_mupdf'] is True
    assert q['search_mupdf'] is False and q['search_mupdf_change'] == dict(legacy=True, now=False, why='found elsewhere on the page only')
    assert q['search_copy_check']['result'] is False and q['status'] == 'fail'


def test_a_one_line_replacement_not_found_is_a_failure_not_a_wrap(tmp_path):
    # (As S-L2: one line, a gap inside it.) Before issue #116 it was taken for a wrap: unverified.
    out = page(tmp_path, [LINE1, '申請書　を窓口で配布しま', LINE3])
    q = quality(tmp_path, out)
    assert q['legacy']['search_mupdf'] is None and q['legacy']['status'] == 'unverified'
    assert q['search_mupdf'] is False and q['search_mupdf_change']['why'] == 'not found where it is drawn (one line)'
    assert q['search_copy_check']['failed'][0] == 'search_full' and q['status'] == 'fail'


def test_a_replacement_over_lines_found_on_one_line_only_fails(tmp_path):
    # 補助事業の on line 1 is found; 申請書 on line 2 is drawn with a gap: its part is not found there.
    out = page(tmp_path, [LINE1, '申　請書を窓口で配布しま', LINE3])
    q = quality(tmp_path, out, new=NEW)
    parts = {p['line']: p['found'] for p in q['search_copy']['search_per_line_parts']}
    assert parts == {0: True, 1: False} and q['search_copy_check']['result'] is False and q['status'] == 'fail'


def test_a_wrapped_replacement_drawn_right_passes_line_by_line(tmp_path):
    q = quality(tmp_path, page(tmp_path, [LINE1, LINE2, LINE3]), new=NEW)
    assert q['search_copy']['search_full'] is False and q['search_mupdf'] is None
    assert q['search_copy_check']['result'] is True and q['status'] == 'pass' and q['legacy']['status'] == 'unverified'


@pytest.mark.parametrize('lines', [
    ['本年度は新しい補助事業の', '請申書を窓口で配布しま', LINE3],  # two characters in another order
    ['本年度は新しい補助事業の', '申請を窓口で配布しま', LINE3],    # one missing
])
def test_characters_swapped_or_missing_fail(tmp_path, lines):
    q = quality(tmp_path, page(tmp_path, lines), new=NEW)
    assert q['search_copy_check']['result'] is False and q['status'] == 'fail'


# --- practical success: one rule, its basis per criterion -------------------

def audited(verdict='pass', status='pass', check=True, mode='layout', legacy='unverified'):
    q = dict(status=status, legacy=dict(status=legacy), search_copy=measures(search_full=False))
    if check is not None:
        q['search_copy_check'] = dict(result=check, route='lines')
    return dict(verdict=verdict, mode=mode, quality=q)


def step(layout=True, **x):
    """A saved step as the panel and smoke evaluations record it (an approved panel extension when ``layout``)."""
    s = dict(outcome='saved', failed=['non_text_paint_identical'] if layout else [], safety=not layout, quality=True, structure_failed=[],
             continued_editing=dict(ok=True), links=dict(ok=True), moves=dict(ok=True),
             save=dict(panels_extended=[dict(id='panel-1')]) if layout else dict(follower_translation=12.0),
             approved_audit=audited(mode='layout' if layout else 'faithful'))
    s.update(x)
    return s


def record(x):
    return er.step_record('panel', 'L2', '1', x, 'real')


def test_an_approved_panel_edit_verified_line_by_line_is_a_practical_success():
    r = record(step())
    assert r['mode'] == 'layout' and r['tiers']['practical'] is True
    # The region audit fails by design on the approved change: shown with its role, not turned into a pass.
    region = next(b for b in r['practical_basis'] if b['source'] == 'existing audit: safety')
    assert r['checks']['safety'] is False and region['result'] is False and region['role'] == 'shown'
    assert r['checks']['search_full'] is False and r['checks']['search_copy'] is True and r['checks'].get('approved_quality_legacy') is None


def test_the_old_move_audit_failing_on_a_panel_is_shown_beside_the_approved_audit():
    # (The move audit does not model a panel extension: its protected drawings differ.)
    r = record(step(moves=dict(ok=False)))
    assert r['checks']['moves'] is False and r['tiers']['practical'] is True
    assert next(b for b in r['practical_basis'] if b['criterion'] == 'moves audit')['role'] == 'shown'


@pytest.mark.parametrize('change, expected', [
    (dict(approved_audit=audited(verdict='fail')), False),                     # an id not approved, or a protection failed
    (dict(approved_audit=audited(verdict='unverified')), None),               # a change kind the audit cannot check
    (dict(approved_audit=audited(status='fail', check=False)), False),        # the edited text not verified
    (dict(approved_audit=audited(status='unverified', check=None)), None),    # a required measure not made
    (dict(approved_audit=audited(check=None) | dict(quality=dict(status='pass'))), None),  # an audit made before issue #116
    (dict(structure_failed=['ParentTree']), False),
    (dict(links=dict(ok=False)), False),
])
def test_what_never_makes_a_practical_success(change, expected):
    assert record(step(**change))['tiers']['practical'] is expected


def test_a_faithful_edit_must_also_pass_the_existing_audit():
    assert record(step(layout=False))['tiers']['practical'] is True
    # Without an approved layout change, the region audit failing is a failure, whatever the approved audit says.
    r = record(step(layout=False, safety=False, failed=['glyphs_outside_region_identical']))
    assert r['tiers']['practical'] is False
    assert record(step(layout=False, quality=False))['tiers']['practical'] is False


def test_a_faithful_edit_is_never_counted_as_an_approved_layout_edit():
    # The audit says layout but the save moved nothing approved (or the reverse): faithful, and the region audit counts.
    r = record(step(layout=False, approved_audit=audited(mode='layout')))
    assert r['mode'] == 'faithful'
    r = record(step(approved_audit=audited(mode='faithful')))
    assert r['mode'] == 'faithful' and r['tiers']['practical'] is False


def test_a_refusal_and_a_missing_input_are_never_a_success():
    assert record(dict(outcome='refused', reason='Refused: capacity'))['tiers']['practical'] is False
    r = smoke_evaluate.records({'X': [dict(outcome='input missing')]})[0]
    assert r['tiers']['practical'] is None and r['tiers']['saved'] is None


def test_a_deletion_needs_no_search():
    a = audited(check='not applicable')
    a['quality']['search_copy_check']['route'] = 'deletion'
    r = record(step(approved_audit=a))
    assert r['tiers']['practical'] is True and 'search_copy' not in r['checks']


def test_steps_without_the_approved_audit_keep_the_existing_rule():
    # (chain, decoration, ...: the existing audits; their search check is not measured on the edited lines.)
    x = dict(outcome='saved', failed=[], safety=True, quality=True, structure_failed=[], continued_editing=dict(ok=True))
    r = er.step_record('chain', 'C', '1', x, 'real')
    assert r['tiers']['practical'] is True and 'practical_basis' not in r
    value, basis = er.practical('saved', dict(quality=True, safety=True), False)
    assert value is True and 'anywhere on the page' in basis[1]['note']
