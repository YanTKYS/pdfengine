"""Focused collector/gating tests; synthetic lifecycle probes run separately."""
from copy import deepcopy

import pytest

from evaluations.continuation import source_output_external as external


def row():
    return dict(record=dict(version=1, marker_id='a', created_from={'pdf_sha256':'b'},
        current=dict(entry_context_sha256='c', program_sha256='d', range=[1,10], block_sha256='e')),
        body=dict(byte_length=5, sha256='f', operator_count=4),
        page_program=dict(byte_length=20, sha256='g', operator_count=8),
        prefix_sha256='h', suffix_sha256='i')


@pytest.mark.parametrize('field', ['body', 'page_program', 'prefix_sha256', 'suffix_sha256'])
def test_equal_lengths_do_not_hide_changed_bytes(field):
    before = row()
    after = deepcopy(before)
    if isinstance(after[field], dict):
        after[field]['sha256'] = 'changed'
    else:
        after[field] = 'changed'
    checks = external.compare_source_rows({'slot':before}, {'slot':after}, noop=True)
    assert not all(checks['slot'].values())


def test_semantic_change_allows_body_and_current_hash_but_not_context_drift():
    before = row()
    after = deepcopy(before)
    after['body']['sha256'] = 'new text'
    after['record']['current']['block_sha256'] = 'new block'
    checks = external.compare_source_rows({'slot':before}, {'slot':after}, noop=False)
    assert all(checks['slot'].values())
    after['record']['current']['entry_context_sha256'] = 'new state'
    assert not external.compare_source_rows({'slot':before}, {'slot':after}, noop=False)['slot']['entry_context']


def test_noneligible_gate_prevents_any_lifecycle_or_directory_creation(tmp_path, monkeypatch):
    external.single.write(tmp_path/'eligibility.json', {'status':'not-eligible'})
    monkeypatch.setattr(external.single, 'run', lambda *_: pytest.fail('mutation lifecycle started'))
    with pytest.raises(ValueError, match='prohibited by eligibility gate'):
        external.formal(tmp_path, 'page-entry')
    assert not (tmp_path/'page-entry').exists()


def test_runtime_change_after_eligibility_stops_before_mutation(tmp_path, monkeypatch):
    external.single.write(tmp_path/'eligibility.json', {'status':'eligible'})
    monkeypatch.setattr(external, 'runtime_digest', lambda: 'different engine')
    monkeypatch.setattr(external.single, 'run', lambda *_: pytest.fail('mutation lifecycle started'))
    with pytest.raises(ValueError, match='runtime/source changed'):
        external.formal(tmp_path, 'page-entry')
    assert not (tmp_path/'page-entry').exists()


def test_pass_requires_both_scenarios_and_every_supplemental():
    from evaluations.continuation.source_output_collect import final_status
    scenarios={n:dict(status='passed') for n in ('page-entry','boundary')}
    probes={n:dict(status='passed') for n in ('scaled-ctm','creation-empty','operational','adjacent')}
    assert final_status('eligible',scenarios,probes)[0]=='passed'
    assert final_status('eligible',{'page-entry':scenarios['page-entry']},probes)[0]=='failed'
    assert final_status('not-eligible',scenarios,probes)[0]=='not-eligible'
    scenarios['boundary']['status']='incomplete'
    assert final_status('eligible',scenarios,probes)[0]=='failed'
    scenarios['boundary']['status']='passed'
    probes['scaled-ctm']['status']='failed'
    assert final_status('eligible',scenarios,probes)[0]=='failed'


def test_compact_encoding_preserves_every_measurement(tmp_path):
    from evaluations.continuation.source_output_collect import write_compact
    value=dict(scenarios={'page-entry':dict(lifecycle_stages=[dict(stage='first', source_slots={'slot':row()})])})
    path=tmp_path/'summary.json'
    write_compact(path,value)
    assert external.read(path)==value
