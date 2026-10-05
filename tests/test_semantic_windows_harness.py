"""Cloud-side tests of the external validation harness (CLI, sequencing, result/report, failure recording).

These tests exercise the harness on synthetic local fixtures. They are not Windows or LibreOffice evidence.
"""
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from array import array

from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables.ttProgram import Program

from evaluations.semantic_lifecycle import windows_validation as harness

ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE = ['preflight', 'baseline', 'confirm', 'edit', 'style', 'font', 'noop', 'publish_a', 'edit_from_bundle_a',
             'font_back', 'publish_b', 'negatives', 'continuity', 'raster_mupdf', 'raster_poppler', 'inputs_preserved']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(*argv):
    return harness.main([str(a) for a in argv])


@pytest.fixture(scope='module')
def prepared(tmp_path_factory):
    root = tmp_path_factory.mktemp('harness')
    assert main('prepare-synthetic', '--output-dir', root / 'src') == 0
    assert main('prepare-synthetic', '--output-dir', root / 'lo', '--libreoffice-shape') == 0
    assert main('prepare-fonts', '--output-dir', root / 'fonts') == 0
    fonts = root / 'fonts'
    assert main('prepare', '--source-pdf', root / 'src' / 'source.pdf', '--spec', root / 'src' / 'spec.json',
                '--font-a', fonts / 'font-a.ttf', '--output-dir', root / 'prep') == 0
    assert main('prepare', '--source-pdf', root / 'lo' / 'source.pdf', '--spec', root / 'lo' / 'spec.json',
                '--font-a', fonts / 'font-a.ttf', '--output-dir', root / 'loprep') == 0
    return dict(root=root, fonts=fonts, prep=root / 'prep', loprep=root / 'loprep')


def run_args(prepared, out, *, prep='prep', font_b=True, extra=()):
    args = ['run', '--pdf', prepared[prep] / 'document.pdf', '--sidecar', prepared[prep] / 'shared-flow.json',
            '--font-a', prepared['fonts'] / 'font-a.ttf', '--output-dir', out, *extra]
    if font_b:
        args += ['--font-b', prepared['fonts'] / 'font-b.ttf']
    return args


def result(out):
    return json.loads((Path(out) / 'result.json').read_text(encoding='utf-8'))


def statuses(value):
    return {s['id']: s['status'] for s in value['stages']}


def inputs(prepared, prep='prep'):
    paths = [prepared[prep] / 'document.pdf', prepared[prep] / 'shared-flow.json', prepared['fonts'] / 'font-a.ttf',
             prepared['fonts'] / 'font-b.ttf']
    return {p: sha(p) for p in paths}


# --- successful synthetic lifecycle --------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def passed(prepared):
    before = inputs(prepared)
    out = prepared['root'] / 'out-pass'
    code = main(*run_args(prepared, out))
    return dict(code=code, out=out, result=result(out), before=before)


def test_successful_synthetic_lifecycle(passed, prepared):
    value = passed['result']
    assert passed['code'] == 0 and value['verdict'] == 'PASS'
    status = statuses(value)
    assert list(status) == LIFECYCLE
    assert all(status[s] == 'PASS' for s in LIFECYCLE if s != 'raster_poppler')
    assert status['raster_poppler'] == ('PASS' if shutil.which('pdftoppm') else 'SKIPPED')
    revisions = value['revisions']
    assert list(revisions) == ['confirmed', 'candidate-01-edit', 'candidate-02-style', 'candidate-03-font-b',
                               'candidate-04-noop', 'bundle-a', 'candidate-05-edit-from-bundle-a',
                               'candidate-06-font-a', 'bundle-b']
    font_a, font_b = sha(prepared['fonts'] / 'font-a.ttf'), sha(prepared['fonts'] / 'font-b.ttf')
    assert [revisions[n]['semantic']['font_sha256'] for n in ('confirmed', 'bundle-a', 'bundle-b')] == [font_a, font_b,
                                                                                                         font_a]
    assert revisions['bundle-a']['semantic']['provenance'] == 'caller-confirmed-current-semantic'
    assert revisions['bundle-b']['semantic']['text'] == 'BAA B' and revisions['bundle-b']['semantic']['tracking'] == '1/4'
    assert len({r['owner']['marker_id'] for r in revisions.values()}) == 1
    assert revisions['candidate-04-noop']['island'] == revisions['candidate-03-font-b']['island']
    assert revisions['bundle-a']['pdf_sha256'] == revisions['candidate-04-noop']['pdf_sha256']
    assert revisions['bundle-a']['sidecar_sha256'] == revisions['candidate-04-noop']['sidecar_sha256']


def test_result_json_records_environment_inputs_and_evidence(passed, prepared):
    value = passed['result']
    assert value['schema_version'] == harness.SCHEMA_VERSION and value['executed_at'] and value['finished_at']
    assert value['windows_execution'] is (platform.system() == 'Windows')
    env = value['environment']
    assert {'os', 'python', 'pymupdf', 'fonttools', 'uharfbuzz', 'pypdf', 'poppler', 'libreoffice', 'repository'} <= set(env)
    assert env['repository']['runtime_digest'] == harness.runtime_digest()
    assert value['inputs']['pdf']['sha256'] == passed['before'][prepared['prep'] / 'document.pdf']
    assert value['inputs']['pdf']['page_count'] == 1 and value['inputs']['pdf']['libreoffice_claimed'] is False
    assert value['inputs']['fonts']['font_b']['sha256'] == sha(prepared['fonts'] / 'font-b.ttf')
    assert value['target']['slot_id'] == 'slot-0' and value['target']['sidecar_schema'] == 'pdfengine-shared-flow-2'
    owner = value['revisions']['bundle-b']['owner']
    assert set(owner) == {'marker_id', 'created_from_sha256', 'program_sha256', 'block_sha256', 'range', 'pdf_sha256'}
    semantic = value['revisions']['bundle-b']['semantic']
    assert {'text', 'font_sha256', 'provider_sha256', 'font_size', 'tracking', 'rise', 'horizontal_scale',
            'word_spacing', 'edges', 'provenance'} <= set(semantic)
    stage = {s['id']: s for s in value['stages']}
    assert stage['continuity']['details']['owner_marker_constant'] is True
    assert stage['negatives']['details']['expected_refusals'].keys() == {'A.pdf+B.json', 'B.pdf+A.json'}
    assert stage['publish_b']['details']['bundle_a_unchanged'] is True
    assert stage['publish_a']['details']['fresh_process'] == 'restored'
    # No absolute local paths in the evidence: inputs by file name, artifacts relative to the output directory.
    text = (passed['out'] / 'result.json').read_text(encoding='utf-8')
    # Check both the raw and the JSON-escaped spelling (Windows paths contain backslashes).
    assert str(prepared['root']) not in text and json.dumps(str(prepared['root']))[1:-1] not in text


def test_report_and_artifact_layout(passed):
    out = passed['out']
    report = (out / 'report.md').read_text(encoding='utf-8')
    assert harness.DISCLAIMER in report and '**Verdict:** `PASS`' in report
    if platform.system() != 'Windows':
        assert 'NO — not Windows evidence' in report
    assert '| `publish_b` | yes | **PASS** |' in report
    assert sorted(p.name for p in (out / 'artifacts' / 'bundle-a').iterdir()) == ['document.pdf', 'shared-flow.json']
    assert sorted(p.name for p in (out / 'artifacts' / 'bundle-b').iterdir()) == ['document.pdf', 'shared-flow.json']
    assert {p.name for p in (out / 'artifacts' / 'raster').glob('mupdf-*.png')} == {
        'mupdf-baseline.png', 'mupdf-changed.png', 'mupdf-noop.png', 'mupdf-bundle_a.png', 'mupdf-bundle_b.png'}
    assert (out / 'logs' / 'harness.log').read_text(encoding='utf-8').count('stage ') >= 2 * len(LIFECYCLE)


def test_original_inputs_are_unchanged(passed, prepared):
    assert inputs(prepared) == passed['before']
    assert {s['id']: s['status'] for s in passed['result']['stages']}['inputs_preserved'] == 'PASS'


# --- refusals, failures, incompleteness ----------------------------------------------------------------------------

def test_libreoffice_page_shape_is_an_expected_refusal_not_a_failure(prepared):
    before = inputs(prepared, 'loprep')
    out = prepared['root'] / 'out-lo'
    assert main(*run_args(prepared, out, prep='loprep')) == harness.EXIT['UNSUPPORTED_TARGET'] == 3
    value = result(out)
    status = statuses(value)
    assert value['verdict'] == 'UNSUPPORTED_TARGET'
    assert status['baseline'] == 'PASS' and status['confirm'] == 'REFUSED'
    assert all(status[s] == 'SKIPPED' for s in LIFECYCLE[3:-1]) and status['inputs_preserved'] == 'PASS'
    confirm = next(s for s in value['stages'] if s['id'] == 'confirm')
    assert 'unclipped' in confirm['error']
    assert '**REFUSED**' in (out / 'report.md').read_text(encoding='utf-8') and inputs(prepared, 'loprep') == before


def test_stage_failure_is_recorded_and_partial_results_are_kept(prepared, monkeypatch):
    calls = []
    real = harness.island
    def drifting(pdf, state, sid):
        value = real(pdf, state, sid)
        calls.append(1)
        return dict(value, size=value['size'] + len(calls))  # the no-op check sees a changed body
    monkeypatch.setattr(harness, 'island', drifting)
    out = prepared['root'] / 'out-fail'
    assert main(*run_args(prepared, out)) == harness.EXIT['FAIL'] == 1
    value = result(out)
    status = statuses(value)
    assert value['verdict'] == 'FAIL' and status['noop'] == 'FAIL'
    assert status['publish_a'] == 'SKIPPED' and status['inputs_preserved'] == 'PASS'
    assert 'no-op save changed the canonical owned body' in next(s for s in value['stages'] if s['id'] == 'noop')['error']
    assert 'candidate-03-font-b' in value['revisions']  # evidence up to the failure is retained
    assert '**FAIL**' in (out / 'report.md').read_text(encoding='utf-8')
    assert 'Failure' in (out / 'logs' / 'harness.log').read_text(encoding='utf-8')


def test_unexpected_exception_is_a_failure_not_a_refusal(prepared, monkeypatch):
    from pdfeditor import semantic_publication
    def broken(*args, **kwargs):
        raise OSError('injected disk failure')
    monkeypatch.setattr(semantic_publication, 'publish_semantic_bundle', broken)
    out = prepared['root'] / 'out-oserror'
    assert main(*run_args(prepared, out)) == 1
    stage = next(s for s in result(out)['stages'] if s['id'] == 'publish_a')
    assert stage['status'] == 'FAIL' and 'OSError: injected disk failure' in stage['error']


def test_missing_font_b_makes_the_validation_incomplete(prepared):
    out = prepared['root'] / 'out-incomplete'
    assert main(*run_args(prepared, out, font_b=False)) == harness.EXIT['INCOMPLETE'] == 4
    value = result(out)
    status = statuses(value)
    assert value['verdict'] == 'INCOMPLETE' and status['font'] == 'SKIPPED' and status['font_back'] == 'SKIPPED'
    assert status['publish_b'] == 'PASS'  # the rest of the lifecycle still ran


def test_missing_input_file_is_a_usage_error_and_writes_nothing(prepared, tmp_path):
    out = tmp_path / 'never'
    args = run_args(prepared, out, font_b=False) + ['--font-b', tmp_path / 'missing.ttf']
    assert main(*args) == harness.EXIT['USAGE'] == 2
    assert not out.exists()


def test_missing_provider_asset_is_refused_before_any_mutation(prepared, tmp_path):
    copy = tmp_path / 'copy'
    shutil.copytree(prepared['prep'], copy)
    from pdfeditor.document_flow import _reseal
    from pdfeditor.shared_flow import _contract
    sidecar = json.loads((copy / 'shared-flow.json').read_text(encoding='utf-8'))
    # Point every provider reference at a missing file in the parsed JSON (a string replace misses
    # JSON-escaped Windows paths), then reseal it consistently: the slot bindings, the confirmed
    # contract and the model checksum. The refusal must then come from the missing asset alone.
    original, gone = str((prepared['fonts'] / 'font-a.ttf').resolve()), str(tmp_path / 'gone.ttf')
    replaced = []

    def swap(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == 'path' and value == original:
                    node[key] = gone
                    replaced.append(key)
                else:
                    swap(value)
        elif isinstance(node, list):
            for value in node:
                swap(value)

    swap(sidecar)
    assert replaced and original not in json.dumps(sidecar)  # the provider path really was replaced
    for slot in sidecar['slots'].values():
        slot['binding'] = _reseal(slot['binding'])
    sidecar['contract_sha256'] = _contract(sidecar)
    sidecar = _reseal(sidecar)
    (copy / 'shared-flow.json').write_text(json.dumps(sidecar, indent=2), encoding='utf-8')
    out = tmp_path / 'out'
    code = main('run', '--pdf', copy / 'document.pdf', '--sidecar', copy / 'shared-flow.json',
                '--font-a', prepared['fonts'] / 'font-a.ttf', '--font-b', prepared['fonts'] / 'font-b.ttf',
                '--output-dir', out)
    assert code == 3
    value = result(out)
    status = statuses(value)
    assert status['baseline'] == 'REFUSED' and status['edit'] == 'SKIPPED'
    reason = next(stage['error'] for stage in value['stages'] if stage['id'] == 'baseline')
    # Not an integrity, contract or binding-seal refusal: the resealed sidecar is otherwise valid.
    assert not any(word in reason for word in ('checksum', 'contract', 'identities', 'differs from its'))
    assert 'gone.ttf' in reason or 'No such file' in reason or 'unavailable' in reason, reason


@pytest.mark.parametrize('case', ['wrong_slot', 'mismatched_pdf'])
def test_invalid_target_is_refused_without_mutation(prepared, tmp_path, case):
    if case == 'wrong_slot':
        args = run_args(prepared, tmp_path / 'out', extra=('--slot-id', 'slot-9'))
    else:
        args = ['run', '--pdf', prepared['loprep'] / 'document.pdf', '--sidecar', prepared['prep'] / 'shared-flow.json',
                '--font-a', prepared['fonts'] / 'font-a.ttf', '--font-b', prepared['fonts'] / 'font-b.ttf',
                '--output-dir', tmp_path / 'out']
    assert main(*args) == 3
    value = result(tmp_path / 'out')
    assert value['verdict'] == 'UNSUPPORTED_TARGET' and statuses(value)['baseline'] == 'REFUSED'
    assert not (tmp_path / 'out' / 'artifacts' / 'bundle-a').exists()


def test_output_directory_collision_is_refused_and_untouched(prepared, tmp_path):
    out = tmp_path / 'existing'
    out.mkdir()
    (out / 'keep.txt').write_text('keep')
    assert main(*run_args(prepared, out)) == 2
    assert sorted(p.name for p in out.iterdir()) == ['keep.txt']


def test_already_confirmed_input_is_reopened_not_reconfirmed(passed, prepared, tmp_path):
    confirmed = passed['out'] / 'artifacts' / 'confirmed' / 'shared-flow.json'
    out = tmp_path / 'out'
    code = main('run', '--pdf', prepared['prep'] / 'document.pdf', '--sidecar', confirmed,
                '--font-a', prepared['fonts'] / 'font-a.ttf', '--font-b', prepared['fonts'] / 'font-b.ttf',
                '--output-dir', out)
    assert code == 0
    confirm = next(s for s in result(out)['stages'] if s['id'] == 'confirm')
    assert confirm['details']['mode'] == 'input already shared-flow-3'


def test_plan_file_overrides_requests_and_refusal_is_recorded(prepared, tmp_path):
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps(dict(style=dict(font_size='100'))))  # exceeds the confirmed region: runtime refuses
    out = tmp_path / 'out'
    assert main(*run_args(prepared, out, extra=('--plan', plan))) == 3
    value = result(out)
    assert value['verdict'] == 'REFUSED' and statuses(value)['style'] == 'REFUSED'
    assert 'region' in next(s for s in value['stages'] if s['id'] == 'style')['error']


# --- fonts and the CLI entry point ---------------------------------------------------------------------------------

def test_font_preparation_is_deterministic_and_dehints(prepared, tmp_path):
    assert main('prepare-fonts', '--output-dir', tmp_path / 'again') == 0
    for name in ('font-a.ttf', 'font-b.ttf'):
        assert sha(tmp_path / 'again' / name) == sha(prepared['fonts'] / name)
    hinted = TTFont(prepared['fonts'] / 'font-a.ttf')  # make an installed-font-like hinted asset
    for tag in ('fpgm', 'prep'):
        hinted[tag] = newTable(tag)
        hinted[tag].program = Program()
        hinted[tag].program.fromBytecode(b'')
    hinted['cvt '] = newTable('cvt ')
    hinted['cvt '].values = array('h', [0])
    source = tmp_path / 'hinted.ttf'
    hinted.save(source)
    with pytest.raises(Exception):
        harness.check_font(source)  # the runtime refuses a hinted asset
    assert main('prepare-fonts', '--output-dir', tmp_path / 'derived', '--from-a', source, '--from-b', source) == 0
    derived = TTFont(tmp_path / 'derived' / 'font-a.ttf')
    assert not {'fpgm', 'prep', 'cvt '} & set(derived.keys())
    record = json.loads((tmp_path / 'derived' / 'fonts.json').read_text(encoding='utf-8'))
    assert record['font-a.ttf']['derived_from'] == 'hinted.ttf' and record['font-a.ttf']['source_sha256'] == sha(source)


def test_cli_entry_point_runs_in_a_fresh_process(prepared, tmp_path):
    out = tmp_path / 'out'
    done = subprocess.run([sys.executable, '-m', 'evaluations.semantic_lifecycle.windows_validation',
                           *map(str, run_args(prepared, out))], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr[-2000:]
    assert 'verdict: PASS (exit 0)' in done.stdout
    assert result(out)['verdict'] == 'PASS'


def test_evidence_files_are_utf8_independent_of_the_locale(passed):
    """Regression (Windows cp932 locale): evidence is UTF-8 bytes and decodes without the locale codec."""
    for name in ('result.json', 'report.md'):
        data = (passed['out'] / name).read_bytes()
        text = data.decode('utf-8')
        assert text.encode('utf-8') == data
    assert '\u2014' in (passed['out'] / 'report.md').read_text(encoding='utf-8')
