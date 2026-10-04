"""Design experiments, not acceptance of a future runtime layout authority."""
import json
from fractions import Fraction as F
import subprocess
import sys

import pytest

from evaluations.anchors.layout_authority import cell, edit, plan, semantic_style
from evaluations.anchors.layout_observation import lifecycle, source_case, ink_counterexample


@pytest.fixture(scope='module')
def observed(tmp_path_factory):
    root = tmp_path_factory.mktemp('layout-lifecycle')
    result = lifecycle(root, 'identity')
    raw = json.loads((root / 'identity' / 'physical-observations.json').read_text(encoding='utf-8'))
    return result, raw


def glyph(stage, offset):
    return next(g for g in stage['glyphs'] if g['start'] == offset)


def test_known_space_provider_transition_is_not_metric_drift(observed):
    _, raw = observed
    a, b = glyph(raw['first'], 3), glyph(raw['noop1'], 3)
    assert a['paint_advance'] == b['paint_advance'] == 7.199999999999999
    assert a['width_1000em'] == b['width_1000em'] == 600
    assert a['following_provider'] == 'new' and b['following_provider'] == 'original'
    assert a['authority'] == 'metric' and b['authority'] == 'trace-difference'
    assert a['advance'] == 7.199999999999999 and b['advance'] == 7.200000762939453


def test_source_line_and_deleted_adjacency_are_independent_triggers(observed):
    _, raw = observed
    a, b = glyph(raw['first'], 14), glyph(raw['noop1'], 14)
    assert a['provider'] == b['provider'] == 'original'
    assert a['same_source_line'] is False and b['same_source_line'] is True
    a, b = glyph(raw['change'], 3), glyph(raw['change_noop1'], 3)
    assert a['source_contiguous'] is False and b['source_contiguous'] is True
    assert a['advance'] != b['advance']


def test_retained_trace_difference_changes_at_new_absolute_position(observed):
    _, raw = observed
    a, b = glyph(raw['first'], 16), glyph(raw['noop1'], 16)
    assert a['authority'] == b['authority'] == 'trace-difference'
    assert a['source_contiguous'] and b['source_contiguous']
    assert a['advance'] != b['advance']
    assert a['trace_pair'] != b['trace_pair']


def test_growth_and_reflow_have_real_noop_comparisons(observed):
    result, raw = observed
    assert len(raw['growth']['text']) > len(raw['change_noop3']['text'])
    assert result['active_ranges']['growth'][0][1] > result['active_ranges']['change_noop3'][0][1]
    assert 'growth->growth_noop' in result['comparisons']
    assert 'reflow->reflow_noop' in result['comparisons']
    assert raw['growth']['text'] == raw['reflow']['text']
    assert raw['growth']['lines'] != raw['reflow']['lines']
    assert all(a['within_0_002'] for a in result['accuracy'].values())


@pytest.mark.parametrize('name,state,text', [
    ('default', b'', b'(A B C ) Tj'),
    ('Tc', b'.4 Tc', b'(A B C ) Tj'),
    ('Tw', b'1.2 Tw', b'(A B C ) Tj'),
    ('Tz', b'80 Tz', b'(A B C ) Tj'),
    ('TJ', b'', b'[(A) -100 ( B C )] TJ'),
    ('Tm', b'', b'(A) Tj 1 0 0 1 30 200 Tm ( B C ) Tj')])
def test_spacing_line_end_and_source_positioning(tmp_path, name, state, text):
    result = source_case(tmp_path, name, state, text)
    last = result['first']['glyphs'][-1]
    assert last['authority'] == 'metric-minus-Tc'
    assert result['first']['omitted_logical_offsets'] == [5]
    assert result['source_decimal_oracle_max_origin_delta'] <= .002
    assert all(a['within_0_002'] for a in result['accuracy'].values())
    if name in ('TJ', 'Tm'):
        assert result['source_nominal_only_max_gap_error'] > 1
    if name == 'Tc':
        assert last['paint_advance'] > last['advance']
    if name in ('Tc', 'Tw', 'Tz'):
        assert any(g[name] not in (0, 100) for g in result['first']['glyphs'])


def test_advance_unification_does_not_fix_trace_ink_wrap(tmp_path):
    result = ink_counterexample(tmp_path)
    assert result['fixed_advance'] == 7.2
    assert not result['width_exact'] and not result['allocation_exact']
    assert result['cases'][0]['boundary_allocation'] == [[0, 1], [1, 2]]
    assert result['cases'][1]['boundary_allocation'] == [[0, 2]]
    for case in result['cases']:
        assert case['width_variants']['14.399999'] == [[0, 1], [1, 2]]
        assert case['width_variants']['14.400001'] == [[0, 2]]


def record(text='AB '):
    return dict(cells=[cell(c) for c in text], x='20', baseline='60', width='100', leading='16')


def test_candidate_exact_rational_spacing_and_trailing_whitespace():
    r = record('A B ')
    r['cells'] = [cell(c, tc='.4', tw='1.2', tz='80', scale='.83') for c in 'A B ']
    glyphs = plan(r)['glyphs']
    assert len(glyphs) == 3
    assert F(glyphs[0]['advance']) == F('7.6') * F('.8') * F('.83')
    assert F(glyphs[1]['advance']) == F('8.8') * F('.8') * F('.83')
    assert F(glyphs[2]['advance']) == F('7.2') * F('.8') * F('.83')


def test_candidate_wrap_uses_exact_input_not_tolerance():
    r = record()
    assert len(plan(dict(r, width='14.399999'))['lines']) == 2
    assert len(plan(dict(r, width='14.4'))['lines']) == 1
    assert len(plan(dict(r, width='14.400001'))['lines']) == 1


def test_candidate_edit_is_current_only_and_fresh_process_exact():
    original = record('ONE TWO THREE ')
    changed = edit(original, 4, 8, [cell(c) for c in 'EXTRA '])
    assert ''.join(c['text'] for c in changed['cells']) == 'ONE EXTRA THREE '
    assert set(changed) == set(original)
    expected = plan(changed)
    for _ in range(2):
        result = subprocess.run([sys.executable, '-m', 'evaluations.anchors.layout_authority'],
            input=json.dumps(changed), text=True, capture_output=True, check=True)
        assert json.loads(result.stdout) == expected


def test_semantic_style_ignores_resource_rename_but_not_meaning():
    a = dict(id='s0', font_resource='/Regular', font_xref=4, font_name='Courier',
        font_size=12, horizontal_scale=1, tracking=0, baseline_shift=0, fill=['g', [0]])
    b = dict(a, id='s8', font_resource='/NewAlias', font_xref=20)
    b['fill'] = ['g', ['0.0']]
    b['baseline_shift'] = -0.0
    assert semantic_style(a, 'proven-font') == semantic_style(b, 'proven-font')
    for key, value in [('font_size', 13), ('horizontal_scale', .8), ('tracking', .4), ('baseline_shift', 1)]:
        assert semantic_style(a, 'proven-font') != semantic_style(dict(b, **{key: value}), 'proven-font')
    assert semantic_style(a, 'font-A') != semantic_style(b, 'font-B')


@pytest.mark.parametrize('option,reason', [
    ({'alignment': 'center'}, 'NON_LEFT'), ({'positioning': ['TJ -100']}, 'POSITIONING')])
def test_candidate_explicitly_refuses_unresolved_intent(option, reason):
    with pytest.raises(ValueError, match=reason):
        plan(dict(record(), **option))
