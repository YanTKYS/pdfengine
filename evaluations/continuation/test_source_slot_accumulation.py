"""Guard the measurement claims, not a future ownership implementation."""
import json
from pathlib import Path

import pytest

from evaluations.continuation.source_slot_accumulation import counts, reconcile, paths_for_key


def test_census_distinguishes_dead_displacements_empty_slots_and_mixed_shows():
    value = counts(b'BT /F 12 Tf 1 0 0 1 0 0 Tm [-600 12.5] TJ [] TJ [(A) -20] TJ <42> Tj ET')
    assert (value['Tf'], value['Tm'], value['Tj'], value['TJ']) == (1, 1, 1, 3)
    assert (value['painting_show'], value['nonpainting_numeric_TJ'], value['empty_TJ']) == (2, 1, 1)


def test_delta_accounting_checks_untouched_bytes_even_when_total_lengths_match():
    old = b'BT (A) Tj (B) Tj ET'
    record = dict(start=3, end=9, length=9, kind='text-edit', owner='slot-0', anchors={})
    new = b'BT [-600] TJ (B) Tj ET'
    row, = reconcile(old, new, [record])
    assert row['delta'] == 3 and row['operator_delta'] == 0
    with pytest.raises(ValueError, match='suffix'):
        reconcile(old, new.replace(b'(B)', b'(C)'), [record])
    with pytest.raises(ValueError, match='gap'):
        reconcile(old, new.replace(b'BT', b'q '), [record])


def test_nested_mutation_history_search_does_not_mistake_hash_links_for_history():
    value = {'slots': {'one': {'binding': {'previous_model_sha256': 'deadbeef'}}}}
    assert paths_for_key(value, 'mutation_map') == []
    value['slots']['one']['binding']['mutation_map'] = []
    assert paths_for_key(value, 'mutation_map') == ['/slots/one/binding/mutation_map']


def test_published_evidence_accounts_for_every_mutation_and_reports_reopen_limits():
    path = Path(__file__).with_name('source-slot-ownership-summary.json')
    evidence = json.loads(path.read_text(encoding='utf-8'))
    for case in evidence['cases'].values():
        for name, stage in case['stages'].items():
            assert stage['sidecar_evidence']['mutation_map'] == []
            assert stage['sidecar_evidence']['byte_edits'] == []
            for page in stage['pages'].values():
                if 'mutations' not in page:
                    continue
                assert page['untouched_gaps_identical']
                assert sum(m['delta'] for m in page['mutations']) == page['delta_bytes']
                assert sum(m['operator_delta'] for m in page['mutations']) == page['delta_operators']
                assert sum(v['mutations'] for v in page['by_owner'].values()) == len(page['mutations'])
                for owner, totals in page['by_owner'].items():
                    rows = [m for m in page['mutations'] if str(m['owner']) == owner]
                    before, after = totals['consumed_counts'], totals['replacement_counts']
                    assert before['bytes'] == sum(m['consumed_length'] for m in rows)
                    assert after['bytes'] == sum(m['replacement_length'] for m in rows)
                    assert after['bytes'] - before['bytes'] == totals['delta_bytes']
                    assert after['operators'] - before['operators'] == totals['delta_operators']
            for slot in stage['slots'].values():
                if name != 'initial' and slot['selected_glyphs']:
                    assert slot['all_current_events_at_emitted_glyph_anchors']
            if 'noop' in name:
                assert stage['noop_pixels_equal']
                assert stage['reopened_in_fresh_process_without_previous_report']
    shared = evidence['cases']['shared']['stages']
    for name in ('noop1', 'noop2', 'noop3'):
        assert shared[name]['pages']['1']['delta_bytes'] > 0
        assert shared[name]['pages']['2']['delta_bytes'] > 0
        assert shared[name]['pages']['3']['delta_bytes'] == 0
    assert shared['empty_noop']['slots']['slot-0']['insertion_binding']
    retained = evidence['cases']['retained_partial_multievent']['stages']
    assert retained['initial']['slots']['editable']['events'][0]['foreign_glyphs'] > 0
    # reused_code_glyph_count measures code_witness for newly authored text,
    # not retained source units. Original-font aliases and the emitted anchors
    # witness these six retained glyphs without conflating those counters.
    assert retained['first']['slots']['editable']['font_aliases'] == ['/Regular']
    assert retained['first']['slots']['editable']['selected_glyphs'] == 6
