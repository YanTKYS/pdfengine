"""The evaluation entry: change -> tests and evaluations, common records, comparison with the baseline (issue #108)."""
import json
from types import SimpleNamespace

from prototypes.paragraph_frame import eval_hub as hub
from prototypes.paragraph_frame import eval_records as er


def test_a_change_selects_its_evaluations_by_tier():
    direct, related, milestone, uncovered = hub.impact(['prototypes/paragraph_frame/decoration.py', 'prototypes/paragraph_frame/new_module.py'])
    assert direct == ['decoration', 'separate'] and {'chain', 'restore', 'placed'} <= set(related) and not milestone
    # A file the map does not cover is reported, never silently skipped.
    assert uncovered == ['prototypes/paragraph_frame/new_module.py']
    assert hub.impact(['pdfeditor/content_stream.py'])[2] == ['pdfeditor/content_stream.py']
    assert hub.impact(['docs/x.md', 'CLAUDE.md'])[:3] == ([], [], [])


def test_focused_tests_follow_the_imports():
    # decoration is reached through auto: every test of auto is a focused test of it (CLAUDE.md).
    tests = hub.focused_tests(['prototypes/paragraph_frame/decoration.py'])
    assert {'tests/test_paragraph_frame_decoration.py', 'tests/test_paragraph_frame_chain.py', 'tests/test_paragraph_frame_separate.py'} <= set(tests)
    assert 'tests/test_eval_hub.py' in hub.focused_tests(['tests/test_eval_hub.py'])


def test_a_missing_input_is_not_run():
    args = SimpleNamespace(fusei=None, pdfjs='/nowhere', id_survey='/nowhere', tg_survey='/nowhere')
    problem, _ = hub.inputs_of('chain', args)
    assert problem == 'input missing: fusei'


def step(**x):
    return dict(dict(outcome='saved', failed=[], safety=True, quality=True, structure_failed=[], continued_editing=dict(ok=True)), **x)


def test_tiers_never_count_what_was_not_measured():
    r = er.step_record('e', 'C', '1', step(), 'real')
    assert r['tiers'] == dict(saved=True, practical=True, reedit=True, restore=None, refused=False) and r['mode'] == 'faithful'
    # No quality measured: practical success is unverified, not a success.
    r = er.step_record('e', 'C', '1', step(quality=None, safety=None), 'real')
    assert r['tiers']['practical'] is None
    # An approved move fails the region audit by design; the move audits stand for protection.
    moved = step(safety=False, failed=['non_text_paint_identical'], moves=dict(ok=True), links=dict(ok=True), save=dict(chain_moved=[{}]))
    r = er.step_record('e', 'C', '1', moved, 'real')
    assert r['mode'] == 'layout' and r['tiers']['practical'] is True and r['failed'] == ['non_text_paint_identical']
    moved['moves'] = dict(ok=False)
    assert er.step_record('e', 'C', '1', moved, 'real')['tiers']['practical'] is False
    r = er.step_record('e', 'C', '1', dict(outcome='refused', reason='prototypes.paragraph_frame.engine.Refused: capacity: x'), 'real')
    assert r['tiers']['refused'] and r['tiers']['practical'] is False and r['reason_class'] == 'capacity'


def test_comparison_reports_what_changed():
    base = er.case_records('chain', {'C1': [step(), step()], 'C2': [step()]})
    now = er.case_records('chain', {'C1': [step(), step(quality=False)], 'C3': [step()]})
    diff = er.compare(base, now)
    assert diff['compared'] == 2 and [c['record'] for c in diff['changed']] == [['chain', 'C1', '2']]
    assert diff['changed'][0]['checks'] == {'quality': [True, False]}
    # C2 was not run now (a tier running part of an evaluation): reported as such, not as a missing step.
    assert diff['only_baseline'] == [] and diff['cases_not_run'] == ['chain:C2'] and diff['only_current'] == [['chain', 'C3', '1']]
    gone = er.compare(base, er.case_records('chain', {'C1': [step()], 'C2': [step()]}))
    assert gone['only_baseline'] == [['chain', 'C1', '2']]


def test_the_baseline_and_the_map_are_well_formed():
    base = json.loads(hub.BASELINE.read_text())
    keys = {'evaluation', 'case', 'step', 'group', 'mode', 'outcome', 'reason_class', 'failed', 'checks', 'tiers'}
    assert base['records'] and all(keys <= set(r) for r in base['records'])
    assert len({er.key(r) for r in base['records']}) == len(base['records'])
    assert set(base['evaluations']) == {'smoke', 'chain', 'separate', 'decoration', 'restore', 'placed', 'multiedit', 'roundtrip', 'fidelity',
                                        'generalization', 'continuity', 'column_flow', 'indesign', 'tagged'}
    rules = json.loads(hub.IMPACT.read_text())['rules']
    assert all(set(r.get('direct', [])) | set(r.get('related', [])) <= set(hub.EVALUATIONS) for r in rules)


def test_practical_success_needs_every_measured_protection():
    # Structure or links measured and failed: no practical success, whatever the region audit says.
    assert er.step_record('e', 'C', '1', step(structure_failed=['ParentTree']), 'real')['tiers']['practical'] is False
    assert er.step_record('e', 'C', '1', step(links=dict(ok=False)), 'real')['tiers']['practical'] is False
    moved = step(safety=False, moves=dict(ok=True), links=dict(ok=False), save=dict(chain_moved=[{}]))
    assert er.step_record('e', 'C', '1', moved, 'real')['tiers']['practical'] is False
    # The PR #87-#91 records: an audit's outcome says safety and quality; a sample's three checks all count.
    reg = er.regression_records(dict(
        generalization={'A': dict(initial='success', initial_failed=[], reedit='safety-audit-failed', reedit_failed=['x'])},
        tagged=dict(samples=dict(edits=[dict(document='d', page=1, selection='s', kind='same', outcome='saved', failed=[],
                                             safety=True, quality=True, structure=False, continued_editing=dict(ok=True))]))))
    tiers = {(r['case'], r['step']): r['tiers']['practical'] for r in reg}
    assert tiers == {('A', 'initial'): True, ('A', 'reedit'): False, ('d:1:s', 'same'): False}


def test_a_change_of_tier_mode_or_group_alone_is_reported():
    base = er.case_records('chain', {'C1': [step()]})
    now = [dict(r, tiers=dict(r['tiers'], practical=False)) for r in base]
    diff = er.compare(base, now)
    assert [c['record'] for c in diff['changed']] == [['chain', 'C1', '1']] and diff['changed'][0]['tiers'] == {'practical': [True, False]}
    now = [dict(r, mode='layout', group='fixture') for r in base]
    c = er.compare(base, now)['changed'][0]
    assert c['mode'] == ['faithful', 'layout'] and c['group'] == ['real', 'fixture']
