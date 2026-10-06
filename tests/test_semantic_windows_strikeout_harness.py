"""Tests of the harness's strikeout mode (§33): sequencing, evidence, report, refusals, failures.

These run the harness on synthetic local fixtures. They test the harness itself and are not Windows
evidence: a result is Windows evidence only when `result.json` says `windows_execution: true` and it was
produced by the documented Windows run.
"""
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from evaluations.semantic_lifecycle import windows_validation as harness
from test_semantic_windows_harness import LIFECYCLE, inputs, main, prepared, result, run_args, sha, statuses  # noqa: F401
from test_semantic_windows_underline_harness import UNDERLINE, underline_args

ROOT = Path(__file__).resolve().parents[1]
STRIKEOUT = ['preflight', 'baseline', 'confirm', 'text_baseline', 'add', 'noop_1', 'noop_2', 'edit_remap', 'recipe',
             'style', 'font', 'publish_a', 'remove_from_bundle_a', 'underline_add', 'mixed_strikeout_add', 'mixed_noop',
             'mixed_strikeout_remove', 'publish_b', 'v3_to_v4_control', 'negatives', 'refusals', 'tamper', 'continuity',
             'text_only_control', 'raster_mupdf', 'raster_poppler', 'inputs_preserved']
REVISIONS = ['confirmed', 's00-text-baseline', 's01-add', 's02-noop-1', 's03-noop-2', 's04-edit-remap', 's05-recipe',
             's06-style', 's07-font-b', 'bundle-a', 's08-remove-from-bundle-a', 's09-underline-add',
             's10-mixed-strikeout-add', 's11-mixed-noop', 's12-mixed-strikeout-remove', 'bundle-b',
             'v3c-01-underline-add', 'v3c-02-strikeout-add', 'control-01-edit', 'control-02-style', 'control-03-font-b']
STRIKE = {'offset_em': '-3/10', 'thickness_em': '1/20'}
UL = {'offset_em': '13/120', 'thickness_em': '7/120'}


def strikeout_args(prepared, out, **kwargs):
    return run_args(prepared, out, **kwargs) + ['--mode', 'strikeout']


def details(value, stage):
    return next(s for s in value['stages'] if s['id'] == stage)['details']


def item(kind, start, end, recipe, n):
    return dict(id=f'current:{n}', kind=kind, start=start, end=end, start_affinity='outside', end_affinity='outside',
                recipe=recipe)


@pytest.fixture(scope='module')
def strikeout(prepared):
    before = inputs(prepared)
    out = prepared['root'] / 'out-strikeout'
    code = main(*strikeout_args(prepared, out))
    return dict(code=code, out=out, result=result(out), before=before)


def ev(value, name):
    return value['revisions'][name]['decoration_evidence']


# --- lifecycle --------------------------------------------------------------------------------------------------------

def test_strikeout_synthetic_lifecycle_passes(strikeout):
    value = strikeout['result']
    assert strikeout['code'] == 0 and value['verdict'] == 'PASS'
    status = statuses(value)
    assert list(status) == STRIKEOUT
    assert all(status[s] == 'PASS' for s in STRIKEOUT if s != 'raster_poppler')
    assert status['raster_poppler'] == ('PASS' if shutil.which('pdftoppm') else 'SKIPPED')
    assert list(value['revisions']) == REVISIONS
    assert value['plan'] == harness.DEFAULT_STRIKEOUT_PLAN
    assert value['plan']['strikeout_add']['recipe'] == STRIKE


def test_v2_to_v4_is_the_explicit_strikeout_add(strikeout):
    value = strikeout['result']
    add = details(value, 'add')
    assert add['version_transition'] == [2, 4] and add['classification'] == 'E' and add['paint_transition'] == 'insert'
    assert add['revision']['transition'] == dict(parent='s00-text-baseline', classification='E', version=2,
                                                 next_version=4, authority_preserved=True)
    assert add['decorations'] == [item('strikeout', 0, 3, STRIKE, 0)] and add['text_group_unchanged']
    assert ev(value, 's00-text-baseline')['decorations'] is None  # version 2 has no decorations field
    # Exactly the §32.3 bytes on the synthetic font: 3.6 → 3.0 pt above the baseline, crossing A and B.
    assert ev(value, 's01-add')['rectangles_pdf'] == [['20', '63.6', '41.6', '63']]
    versions = [ev(value, n)['semantic_version'] for n in REVISIONS]
    assert versions == [2, 2] + [4] * 14 + [3, 4, 2, 2, 2]


def test_physical_separation_is_read_from_the_bytes(strikeout):
    value = strikeout['result']
    (row,) = details(value, 'add')['separation']
    assert row == dict(kind='strikeout', upper='63.6', lower='63', baseline='60', upper_above_baseline=True,
                       lower_above_baseline=True)
    for name in REVISIONS:
        d = ev(value, name)
        assert d['rectangle_sides'] == ['above' if k == 'strikeout' else 'at_or_below' for k in d['kinds'] or []], name


def test_two_noops_are_byte_stable(strikeout):
    value = strikeout['result']
    for stage in ('noop_1', 'noop_2'):
        d = details(value, stage)
        assert d['classification'] == 'D' and d['paint_transition'] == 'replace' and d['pdf_bytes_required']
        assert all(d[k] for k in ('pdf_bytes_identical', 'operator_sequence_identical', 'owner_block_identical',
                                  'semantic_identical', 'decoration_evidence_identical'))
    r = value['revisions']
    assert r['s01-add']['pdf_sha256'] == r['s02-noop-1']['pdf_sha256'] == r['s03-noop-2']['pdf_sha256']


def test_edit_remap_uses_the_production_rule(strikeout):
    d = details(strikeout['result'], 'edit_remap')
    assert d['edit'] == dict(start=1, end=1, text='A', before_text='A B', after_text='AA B')
    assert (d['before_ranges'], d['after_ranges']) == ([[0, 3]], [[0, 4]])
    assert d['after_rectangles'] == [['20', '63.6', '48.8', '63']]


def test_recipe_style_and_font(strikeout):
    value = strikeout['result']
    recipe, style, font = details(value, 'recipe'), details(value, 'style'), details(value, 'font')
    assert all(recipe[k] for k in ('text_unchanged', 'text_group_unchanged', 'horizontal_endpoints_unchanged',
                                   'vertical_geometry_changed', 'kind_kept', 'version_4', 'authority_preserved',
                                   'recipe_applied'))
    assert ev(value, 's05-recipe')['rectangles_pdf'] == [['20', '63', '48.8', '62.4']]
    assert style['decorations_preserved'] and style['em_geometry_max_error_pt'] == 0
    assert set(style['diff']) == {'style.font_size', 'style.tracking'}
    assert all(font[k] for k in ('provider_is_font_b', 'generated_font_is_font_b', 'recipe_preserved',
                                 'range_preserved', 'vertical_geometry_unchanged', 'endpoints_on_new_glyphs',
                                 'horizontal_endpoints_follow_font', 'version_4'))
    assert font['em_geometry_max_error_pt'] == 0


def test_publication_a_bundle_reuse_and_remove(strikeout):
    value = strikeout['result']
    a, remove = details(value, 'publish_a'), details(value, 'remove_from_bundle_a')
    assert a['candidate'] == 's07-font-b' and a['fresh_process'] == 'restored' and a['version'] == 4
    assert all(a[k] for k in ('pdf_identical', 'sidecar_identical', 'version_restored', 'decorations_restored',
                              'owner_restored', 'provider_restored', 'canonical_body',
                              'candidate_published_raster_identical'))
    assert remove['input'] == ['artifacts/bundle-a/document.pdf', 'artifacts/bundle-a/shared-flow.json']
    assert remove['revision']['transition']['parent'] == 'bundle-a' and remove['paint_transition'] == 'remove'
    assert all(remove[k] for k in ('decorations_empty', 'version_stays_4', 'paint_group_absent', 'text_group_unchanged'))


def test_mixed_version_4_state_and_its_noop(strikeout):
    value = strikeout['result']
    ul, mixed, noop, rm = (details(value, s) for s in ('underline_add', 'mixed_strikeout_add', 'mixed_noop',
                                                       'mixed_strikeout_remove'))
    assert ul['decorations'] == [item('underline', 0, 1, UL, 0)] and ul['stays_version_4']
    assert mixed['decorations'] == [item('underline', 0, 1, UL, 0), item('strikeout', 3, 4, STRIKE, 1)]
    assert all(mixed[k] for k in ('version_4', 'one_decorations_list', 'canonical_ids', 'one_paint_group',
                                  'paint_order_follows_decorations', 'underline_below_strikeout_above',
                                  'underline_rectangle_unchanged', 'text_group_unchanged'))
    assert [r['kind'] for r in mixed['separation']] == ['underline', 'strikeout']
    assert all(noop[k] for k in ('operator_sequence_identical', 'owner_block_identical', 'semantic_identical',
                                 'decoration_evidence_identical')) and not noop['pdf_bytes_required']
    assert all(rm[k] for k in ('version_stays_4', 'underline_only', 'underline_record_kept',
                               'same_body_as_underline_only_state', 'text_group_unchanged'))
    assert rm['removed']['kind'] == 'strikeout' and rm['revision']['transition']['next_version'] == 4


def test_publication_b_fresh_reopen_and_bundle_a_immutable(strikeout):
    value = strikeout['result']
    b = details(value, 'publish_b')
    assert b['candidate'] == 's12-mixed-strikeout-remove' and b['fresh_process'] == 'restored'
    assert b['bundle_a_unchanged'] and b['underline_only'] and b['no_strikeout'] and b['version'] == 4
    assert b['decorations'] == [item('underline', 0, 1, UL, 0)]


def test_v3_to_v4_route_control(strikeout):
    value = strikeout['result']
    c = details(value, 'v3_to_v4_control')
    assert all(c[k] for k in ('v2_to_v3', 'v3_to_v4', 'underline_exact', 'mixed_canonical', 'mixed_sides',
                              'no_v3_rewrite'))
    assert c['decorations'] == [item('underline', 0, 1, UL, 0), item('strikeout', 2, 3, STRIKE, 1)]
    assert value['revisions']['v3c-01-underline-add']['transition']['next_version'] == 3
    assert value['revisions']['v3c-02-strikeout-add']['transition']['version'] == 3


def test_expected_refusals_overlap_sign_separation_stale_pairs_and_tamper(strikeout):
    value = strikeout['result']
    assert set(details(value, 'negatives')['expected_refusals']) == {
        'A.pdf+B.json', 'B.pdf+A.json', 'mixed.pdf+underline-only.json', 'underline-only.pdf+mixed.json'}
    rules = details(value, 'refusals')['expected_refusals']
    assert 'physically above its baseline' in rules['strikeout separation (-1/1000000000 em)']['planner']
    assert 'strikeout recipe is outside' in rules['strikeout with a positive offset']['writer']
    assert 'underline recipe is outside' in rules['underline with a negative offset']['planner']
    assert all('overlap' in rules[k]['planner'] for k in ('overlap: strikeout on the underline range',
                                                          'overlap: strikeout nesting the underline'))
    tamper = details(value, 'tamper')['expected_refusals']
    assert 'underline recipe is outside' in tamper['resealed kind change (strikeout → underline)']
    assert 'strikeout recipe is outside' in tamper['resealed recipe sign change']
    assert 'PDF revision changed' in tamper['paint geometry tamper']
    assert 'not the canonical text and decoration body' in tamper[
        'paint geometry tamper, owner witness rebound (production island check)']
    assert not any((strikeout['out'] / 'artifacts' / 'refusals').glob('*/*'))  # nothing written by refused requests


def test_raster_expectations(strikeout):
    value = strikeout['result']
    mupdf = details(value, 'raster_mupdf')
    assert mupdf['dpi'] == 144 and mupdf['control'] == 'control-03-font-b'
    assert all(v for k, v in mupdf.items() if isinstance(v, bool)) and len(
        [k for k, v in mupdf.items() if isinstance(v, bool)]) == 12
    assert details(value, 'text_only_control')['text_group_identical_to_removed'] is True


def test_inputs_are_unchanged(strikeout, prepared):
    assert inputs(prepared) == strikeout['before'] and statuses(strikeout['result'])['inputs_preserved'] == 'PASS'


# --- schema and report ------------------------------------------------------------------------------------------------

def test_result_schema_is_additive(strikeout, prepared):
    value = strikeout['result']
    assert value['schema_version'] == harness.SCHEMA_VERSION == 1
    assert (value['mode'], value['validation_kind'], value['strikeout_runtime'], value['underline_runtime']) == (
        'strikeout', 'semantic-strikeout-lifecycle', True, False)
    assert value['strikeout_disclaimer'] == harness.STRIKEOUT_DISCLAIMER and 'underline_disclaimer' not in value
    assert value['windows_execution'] is (platform.system() == 'Windows')
    keys = {'semantic_version', 'decorations', 'decoration_count', 'ranges', 'recipes', 'rectangle_count',
            'rectangles_pdf', 'paint_fill', 'text_tm_y', 'text_tm_x', 'body_sha256', 'body_split', 'text_body_sha256',
            'paint_body_sha256', 'paint_body_size', 'kinds', 'rectangle_sides', 'provider_sha256',
            'raster_mupdf_sha256'}
    for name, revision in value['revisions'].items():
        assert set(revision['decoration_evidence']) == keys and 'underline' not in revision, name
        assert {'owner', 'semantic', 'island', 'pdf_sha256', 'sidecar_sha256'} <= set(revision)
        assert revision['decoration_evidence']['body_sha256'] == revision['island']['sha256']
    text = (strikeout['out'] / 'result.json').read_text(encoding='utf-8')
    assert str(prepared['root']) not in text and json.dumps(str(prepared['root']))[1:-1] not in text


def test_report_strikeout_section(strikeout):
    report = (strikeout['out'] / 'report.md').read_text(encoding='utf-8')
    assert harness.STRIKEOUT_DISCLAIMER in report and '## Strikeout lifecycle' in report
    assert '**Verdict:** `PASS`' in report and '**Semantic version transition:** [2, 4]' in report
    assert '| `s01-add` | E 2→4 | 4 | strikeout [0,3) | 20 63.6 41.6 63 | above |' in report
    assert 'underline [0,1), strikeout [3,4)' in report and 'at_or_below, above' in report
    assert 'not the canonical text and decoration body' in report and '## Underline lifecycle' not in report
    if platform.system() != 'Windows':
        assert report.count('NO — not Windows evidence') == 2


# --- compatibility ----------------------------------------------------------------------------------------------------

def test_text_and_underline_modes_are_unchanged(prepared, tmp_path):
    text = tmp_path / 'text'
    assert main(*run_args(prepared, text)) == 0
    value = result(text)
    assert list(statuses(value)) == LIFECYCLE and 'strikeout_runtime' not in value
    assert all('decoration_evidence' not in r for r in value['revisions'].values())
    underline = tmp_path / 'underline'
    assert main(*underline_args(prepared, underline)) == 0
    value = result(underline)
    assert list(statuses(value)) == UNDERLINE and 'strikeout_runtime' not in value
    assert value['plan'] == harness.DEFAULT_UNDERLINE_PLAN
    assert all('decoration_evidence' not in r and 'underline' in r for r in value['revisions'].values())
    assert '## Strikeout lifecycle' not in (underline / 'report.md').read_text(encoding='utf-8')


# --- failures and optional stages -------------------------------------------------------------------------------------

def test_stage_failure_keeps_partial_results(prepared, tmp_path, monkeypatch):
    real = harness.decoration_evidence
    calls = []

    def drifting(*args):
        value = real(*args)
        calls.append(1)
        if not value['decorations']:
            return value
        # Every decorated revision reports its rectangles below the baseline: the strikeout side check fails.
        return dict(value, rectangle_sides=['at_or_below'] * len(value['rectangle_sides']))
    monkeypatch.setattr(harness, 'decoration_evidence', drifting)
    before = inputs(prepared)
    out = tmp_path / 'fail'
    assert main(*strikeout_args(prepared, out)) == 1
    value = result(out)
    status = statuses(value)
    assert value['verdict'] == 'FAIL' and status['add'] == 'FAIL' and status['publish_a'] == 'SKIPPED'
    assert status['inputs_preserved'] == 'PASS' and inputs(prepared) == before
    assert 's01-add' in value['revisions']  # evidence up to the failure is retained
    assert 'rectangle sides' in next(s for s in value['stages'] if s['id'] == 'add')['error']
    assert '**FAIL**' in (out / 'report.md').read_text(encoding='utf-8')
    assert 'Failure' in (out / 'logs' / 'harness.log').read_text(encoding='utf-8')


def test_poppler_is_optional(prepared, tmp_path, monkeypatch):
    real = shutil.which
    monkeypatch.setattr(harness.shutil, 'which', lambda name: None if name == 'pdftoppm' else real(name))
    out = tmp_path / 'no-poppler'
    assert main(*strikeout_args(prepared, out)) == 0
    value = result(out)
    assert value['verdict'] == 'PASS' and statuses(value)['raster_poppler'] == 'SKIPPED'
    assert '| Poppler 144 dpi | SKIPPED |' in (out / 'report.md').read_text(encoding='utf-8')


@pytest.mark.skipif(shutil.which('pdftoppm') is None, reason='Poppler (pdftoppm) unavailable')
def test_poppler_expectations_when_available(strikeout):
    poppler = details(strikeout['result'], 'raster_poppler')
    assert poppler['dpi'] == 144 and all(v for k, v in poppler.items() if isinstance(v, bool))


def test_missing_font_b_is_incomplete(prepared, tmp_path):
    out = tmp_path / 'no-font-b'
    assert main(*strikeout_args(prepared, out, font_b=False)) == harness.EXIT['INCOMPLETE']
    value = result(out)
    status = statuses(value)
    assert status['font'] == 'SKIPPED' and status['publish_b'] == 'PASS' and status['raster_mupdf'] == 'PASS'
    assert details(value, 'publish_a')['candidate'] == 's06-style'
    assert details(value, 'raster_mupdf')['font_differs_from_previous'] is None


def test_libreoffice_page_shape_is_refused_in_strikeout_mode(prepared, tmp_path):
    before = inputs(prepared, 'loprep')
    out = tmp_path / 'lo'
    assert main(*strikeout_args(prepared, out, prep='loprep')) == harness.EXIT['UNSUPPORTED_TARGET']
    value = result(out)
    status = statuses(value)
    assert value['verdict'] == 'UNSUPPORTED_TARGET' and status['confirm'] == 'REFUSED'
    assert all(status[s] == 'SKIPPED' for s in STRIKEOUT[3:-1]) and inputs(prepared, 'loprep') == before


def test_plan_override_refusal_is_recorded_and_the_plan_file_is_preserved(prepared, tmp_path):
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps(dict(strikeout_recipe={'offset_em': '1/10', 'thickness_em': '1/20'})))  # wrong sign
    before = sha(plan)
    out = tmp_path / 'refused'
    assert main(*strikeout_args(prepared, out, extra=('--plan', plan))) == harness.EXIT['REFUSED']
    value = result(out)
    assert statuses(value)['recipe'] == 'REFUSED' and 'strikeout recipe is outside' in next(
        s for s in value['stages'] if s['id'] == 'recipe')['error']
    assert details(value, 'inputs_preserved')['sha256']['plan'] == before == sha(plan)


def test_cli_strikeout_mode_in_a_fresh_process(prepared, tmp_path):
    out = tmp_path / 'cli'
    done = subprocess.run([sys.executable, '-m', 'evaluations.semantic_lifecycle.windows_validation',
                           *map(str, strikeout_args(prepared, out))], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr[-2000:]
    assert 'verdict: PASS (exit 0)' in done.stdout and result(out)['mode'] == 'strikeout'
