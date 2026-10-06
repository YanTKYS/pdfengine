"""Cloud-side tests of the harness's underline mode (§29): sequencing, evidence, report, failures.

These run the harness on synthetic local fixtures (Linux). They test the harness itself and are not
Windows evidence: Windows underline execution has not been performed by the PR that added this mode.
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

ROOT = Path(__file__).resolve().parents[1]
UNDERLINE = ['preflight', 'baseline', 'confirm', 'text_baseline', 'add', 'noop_1', 'noop_2', 'edit_remap', 'recipe',
             'style', 'font', 'publish_a', 'remove_from_bundle_a', 'publish_b', 'negatives', 'tamper', 'continuity',
             'text_only_control', 'raster_mupdf', 'raster_poppler', 'inputs_preserved']
REVISIONS = ['confirmed', 'u00-text-baseline', 'u01-add', 'u02-noop-1', 'u03-noop-2', 'u04-edit-remap', 'u05-recipe',
             'u06-style', 'u07-font-b', 'bundle-a', 'u08-remove-from-bundle-a', 'bundle-b', 'control-01-edit',
             'control-02-style', 'control-03-font-b']


def underline_args(prepared, out, **kwargs):
    return run_args(prepared, out, **kwargs) + ['--mode', 'underline']


def details(value, stage):
    return next(s for s in value['stages'] if s['id'] == stage)['details']


@pytest.fixture(scope='module')
def underline(prepared):
    before = inputs(prepared)
    out = prepared['root'] / 'out-underline'
    code = main(*underline_args(prepared, out))
    return dict(code=code, out=out, result=result(out), before=before)


# --- lifecycle --------------------------------------------------------------------------------------------------------

def test_underline_synthetic_lifecycle_passes(underline):
    value = underline['result']
    assert underline['code'] == 0 and value['verdict'] == 'PASS'
    status = statuses(value)
    assert list(status) == UNDERLINE
    assert all(status[s] == 'PASS' for s in UNDERLINE if s != 'raster_poppler')
    assert status['raster_poppler'] == ('PASS' if shutil.which('pdftoppm') else 'SKIPPED')
    assert list(value['revisions']) == REVISIONS
    assert value['plan'] == harness.DEFAULT_UNDERLINE_PLAN
    assert value['plan']['underline_add']['recipe'] == {'offset_em': '13/120', 'thickness_em': '7/120'}


def test_v2_to_v3_transition_is_the_explicit_add(underline):
    value = underline['result']
    add = details(value, 'add')
    assert add['version_transition'] == [2, 3] and add['classification'] == 'E' and add['paint_transition'] == 'insert'
    assert add['revision']['transition']['authority_preserved'] is True
    assert add['decorations'] == [dict(id='current:0', kind='underline', start=0, end=3, start_affinity='outside',
                                       end_affinity='outside', recipe={'offset_em': '13/120', 'thickness_em': '7/120'})]
    revisions = value['revisions']
    assert [revisions[n]['underline']['semantic_version'] for n in REVISIONS] == [2, 2] + [3] * 10 + [2, 2, 2]
    assert revisions['u00-text-baseline']['underline']['decorations'] is None  # version 2 has no decorations field
    assert revisions['u01-add']['underline']['text_body_sha256'] == revisions['u00-text-baseline']['underline']['text_body_sha256']


def test_two_noops_are_stable(underline):
    value = underline['result']
    for stage in ('noop_1', 'noop_2'):
        d = details(value, stage)
        assert d['classification'] == 'D' and d['paint_transition'] == 'replace'
        assert all(d[k] for k in ('pdf_bytes_identical', 'operators_identical', 'owner_block_identical',
                                  'semantic_identical', 'underline_identical'))
    r = value['revisions']
    assert r['u01-add']['pdf_sha256'] == r['u02-noop-1']['pdf_sha256'] == r['u03-noop-2']['pdf_sha256']
    assert (r['u01-add']['underline']['raster_mupdf_sha256'] == r['u02-noop-1']['underline']['raster_mupdf_sha256']
            == r['u03-noop-2']['underline']['raster_mupdf_sha256'])


def test_edit_remaps_the_decoration_range(underline):
    d = details(underline['result'], 'edit_remap')
    assert d['classification'] == 'D' and d['revision']['transition']['next_version'] == 3
    assert (d['before_ranges'], d['after_ranges']) == ([[0, 3]], [[0, 4]])
    assert d['before_rectangles'] == [['20', '58.7', '41.6', '58']]
    assert d['after_rectangles'] == [['20', '58.7', '48.8', '58']]


def test_recipe_change(underline):
    d = details(underline['result'], 'recipe')
    assert d['classification'] == 'E' and d['revision']['transition']['next_version'] == 3
    assert all(d[k] for k in ('text_unchanged', 'text_group_unchanged', 'horizontal_endpoints_unchanged',
                              'vertical_geometry_changed', 'authority_preserved', 'recipe_applied'))


def test_style_and_font_changes(underline):
    value = underline['result']
    style, font = details(value, 'style'), details(value, 'font')
    assert style['decorations_preserved'] is True and style['em_geometry_max_error_pt'] == 0
    assert set(style['diff']) == {'style.font_size', 'style.tracking'}
    assert all(font[k] for k in ('provider_is_font_b', 'recipe_preserved', 'vertical_geometry_unchanged',
                                 'endpoints_on_new_glyphs', 'generated_font_is_font_b'))
    assert font['right_endpoints'][0] != font['right_endpoints'][1] and font['em_geometry_max_error_pt'] == 0
    assert value['revisions']['u07-font-b']['semantic']['version'] == 3


def test_publication_a_bundle_reuse_remove_and_publication_b(underline):
    value = underline['result']
    a, remove, b = details(value, 'publish_a'), details(value, 'remove_from_bundle_a'), details(value, 'publish_b')
    assert a['candidate'] == 'u07-font-b' and a['candidate_bytes_identical'] and a['fresh_process'] == 'restored'
    assert a['version'] == 3 and a['decorations'][0]['end'] == 4 and a['candidate_published_raster_identical']
    assert remove['input'] == ['artifacts/bundle-a/document.pdf', 'artifacts/bundle-a/shared-flow.json']
    assert remove['revision']['transition']['parent'] == 'bundle-a' and remove['paint_transition'] == 'remove'
    assert all(remove[k] for k in ('decorations_empty', 'version_stays_3', 'paint_group_absent', 'text_group_unchanged'))
    assert b['bundle_a_unchanged'] and b['text_only_body'] and b['fresh_process'] == 'restored'
    assert (b['version'], b['decorations']) == (3, [])
    final = value['revisions']['bundle-b']['underline']
    assert (final['semantic_version'], final['decorations'], final['paint_body_sha256'], final['paint_body_size']) == (
        3, [], None, 0)


def test_expected_refusals_and_tamper(underline):
    value = underline['result']
    assert set(details(value, 'negatives')['expected_refusals']) == {'A.pdf+B.json', 'B.pdf+A.json'}
    tamper = details(value, 'tamper')['expected_refusals']
    assert 'canonical text and underline body' in tamper['resealed decoration recipe tamper']
    assert 'PDF revision changed' in tamper['paint geometry tamper']
    assert sorted(p.name for p in (underline['out'] / 'artifacts' / 'tamper').iterdir()) == [
        'decoration-tamper.json', 'geometry-tamper.pdf']


def test_raster_expectations(underline):
    value = underline['result']
    mupdf = details(value, 'raster_mupdf')
    assert mupdf['dpi'] == 144 and mupdf['control'] == 'control-03-font-b'
    assert all(mupdf[k] for k in ('baseline_differs_from_add', 'add_equals_noop_1_and_2', 'recipe_differs_from_previous',
                                  'style_differs_from_previous', 'font_differs_from_previous',
                                  'removed_equals_text_only_control', 'candidate_a_equals_bundle_a',
                                  'candidate_b_equals_bundle_b'))
    assert details(value, 'text_only_control')['text_group_identical_to_removed'] is True
    assert len(list((underline['out'] / 'artifacts' / 'raster').glob('mupdf-*.png'))) == 12


def test_inputs_are_unchanged(underline, prepared):
    assert inputs(prepared) == underline['before'] and statuses(underline['result'])['inputs_preserved'] == 'PASS'


# --- schema and report ------------------------------------------------------------------------------------------------

def test_result_schema_additions(underline, prepared):
    value = underline['result']
    assert value['schema_version'] == harness.SCHEMA_VERSION == 1
    assert (value['mode'], value['validation_kind'], value['underline_runtime']) == (
        'underline', 'semantic-underline-lifecycle', True)
    assert value['underline_disclaimer'] == harness.UNDERLINE_DISCLAIMER
    assert value['windows_execution'] is (platform.system() == 'Windows')
    keys = {'semantic_version', 'decorations', 'decoration_count', 'ranges', 'recipes', 'rectangle_count',
            'rectangles_pdf', 'paint_fill', 'text_tm_y', 'text_tm_x', 'body_sha256', 'body_split', 'text_body_sha256',
            'paint_body_sha256', 'paint_body_size', 'raster_mupdf_sha256'}
    for name, revision in value['revisions'].items():
        assert set(revision['underline']) == keys, name
        assert {'owner', 'semantic', 'island', 'pdf_sha256', 'sidecar_sha256'} <= set(revision)  # unchanged keys
        assert revision['underline']['body_sha256'] == revision['island']['sha256']
    added = value['revisions']['u01-add']['underline']
    assert added['rectangle_count'] == 1 and added['paint_fill'] == ['g', '0'] and added['paint_body_size'] > 0
    assert set(value['revisions']['u01-add']['transition']) == {'parent', 'classification', 'version', 'next_version',
                                                                'authority_preserved'}
    text = (underline['out'] / 'result.json').read_text(encoding='utf-8')
    assert str(prepared['root']) not in text and json.dumps(str(prepared['root']))[1:-1] not in text


def test_report_underline_section(underline):
    report = (underline['out'] / 'report.md').read_text(encoding='utf-8')
    assert harness.DISCLAIMER in report and harness.UNDERLINE_DISCLAIMER in report
    assert '## Underline lifecycle' in report and '**Verdict:** `PASS`' in report
    assert '**Semantic version transition:** [2, 3]' in report
    assert '| `u01-add` | E 2→3 | 3 | [0,3) | 20 58.7 41.6 58 |' in report
    assert '| `bundle-b` |  → | 3 | none | — | `none` |' in report
    assert 'resealed decoration recipe tamper' in report and 'decorations [], paint group absent' in report
    if platform.system() != 'Windows':
        assert report.count('NO — not Windows evidence') == 2


# --- text mode compatibility ------------------------------------------------------------------------------------------

def test_run_without_mode_is_the_unchanged_text_lifecycle(prepared, tmp_path):
    out = tmp_path / 'text'
    assert main(*run_args(prepared, out)) == 0
    value = result(out)
    assert list(statuses(value)) == LIFECYCLE and value['plan'] == harness.DEFAULT_PLAN
    assert (value['mode'], value['validation_kind'], value['underline_runtime']) == (
        'text', 'semantic-text-lifecycle', False)
    assert 'underline_disclaimer' not in value and all('underline' not in r for r in value['revisions'].values())
    assert '## Underline lifecycle' not in (out / 'report.md').read_text(encoding='utf-8')
    explicit = tmp_path / 'text-explicit'
    assert main(*run_args(prepared, explicit, extra=('--mode', 'text'))) == 0
    assert list(statuses(result(explicit))) == LIFECYCLE


def test_unknown_mode_is_a_usage_error(prepared, tmp_path):
    with pytest.raises(SystemExit) as error:
        main(*run_args(prepared, tmp_path / 'never', extra=('--mode', 'paint')))
    assert error.value.code == 2 and not (tmp_path / 'never').exists()


# --- failures, refusals, optional stages ------------------------------------------------------------------------------

def test_stage_failure_keeps_partial_results(prepared, tmp_path, monkeypatch):
    real = harness.underline_evidence
    calls = []

    def drifting(*args):
        value = real(*args)
        calls.append(1)
        # The no-op check sees a changed rectangle list on every revision after the first.
        return dict(value, rectangles_pdf=value['rectangles_pdf'] + [['drift', str(len(calls))]])
    monkeypatch.setattr(harness, 'underline_evidence', drifting)
    before = inputs(prepared)
    out = tmp_path / 'fail'
    assert main(*underline_args(prepared, out)) == 1
    value = result(out)
    status = statuses(value)
    assert value['verdict'] == 'FAIL' and status['noop_1'] == 'FAIL' and status['publish_a'] == 'SKIPPED'
    assert status['inputs_preserved'] == 'PASS' and inputs(prepared) == before
    assert 'not a canonical no-op' in next(s for s in value['stages'] if s['id'] == 'noop_1')['error']
    assert 'u02-noop-1' in value['revisions']  # evidence up to the failure is retained
    assert '**FAIL**' in (out / 'report.md').read_text(encoding='utf-8')
    assert 'Failure' in (out / 'logs' / 'harness.log').read_text(encoding='utf-8')


def test_poppler_is_optional(prepared, tmp_path, monkeypatch):
    real = shutil.which
    monkeypatch.setattr(harness.shutil, 'which', lambda name: None if name == 'pdftoppm' else real(name))
    out = tmp_path / 'no-poppler'
    assert main(*underline_args(prepared, out)) == 0
    value = result(out)
    assert value['verdict'] == 'PASS' and statuses(value)['raster_poppler'] == 'SKIPPED'
    assert '| Poppler 144 dpi | SKIPPED |' in (out / 'report.md').read_text(encoding='utf-8')


def test_missing_font_b_is_incomplete(prepared, tmp_path):
    out = tmp_path / 'no-font-b'
    assert main(*underline_args(prepared, out, font_b=False)) == harness.EXIT['INCOMPLETE']
    value = result(out)
    status = statuses(value)
    assert status['font'] == 'SKIPPED' and status['publish_b'] == 'PASS' and status['raster_mupdf'] == 'PASS'
    assert details(value, 'publish_a')['candidate'] == 'u06-style'
    assert details(value, 'raster_mupdf')['font_differs_from_previous'] is None
    assert details(value, 'raster_mupdf')['control'] == 'control-02-style'


def test_libreoffice_page_shape_is_refused_in_underline_mode(prepared, tmp_path):
    before = inputs(prepared, 'loprep')
    out = tmp_path / 'lo'
    assert main(*underline_args(prepared, out, prep='loprep')) == harness.EXIT['UNSUPPORTED_TARGET']
    value = result(out)
    status = statuses(value)
    assert value['verdict'] == 'UNSUPPORTED_TARGET' and status['confirm'] == 'REFUSED'
    assert all(status[s] == 'SKIPPED' for s in UNDERLINE[3:-1]) and inputs(prepared, 'loprep') == before


def test_plan_override_refusal_is_recorded(prepared, tmp_path):
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps(dict(underline_recipe={'offset_em': '3/20', 'thickness_em': '1/10'})))  # 1/4 em
    out = tmp_path / 'refused'
    assert main(*underline_args(prepared, out, extra=('--plan', plan))) == harness.EXIT['REFUSED']
    value = result(out)
    assert statuses(value)['recipe'] == 'REFUSED' and 'line box' in next(
        s for s in value['stages'] if s['id'] == 'recipe')['error']


def test_cli_underline_mode_in_a_fresh_process(prepared, tmp_path):
    out = tmp_path / 'cli'
    done = subprocess.run([sys.executable, '-m', 'evaluations.semantic_lifecycle.windows_validation',
                           *map(str, underline_args(prepared, out))], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr[-2000:]
    assert 'verdict: PASS (exit 0)' in done.stdout and result(out)['mode'] == 'underline'
