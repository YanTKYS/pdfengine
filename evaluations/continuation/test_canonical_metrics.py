"""Small tokenizer/counting and mutation-attribution checks; no engine suite."""
import pytest
from evaluations.continuation import canonical_metrics as metrics


def test_census_counts_arrays_but_not_empty_style_witnesses():
    row = metrics.census(b'q BT /F1 12 Tf 1 0 0 1 0 0 Tm <01> Tj [<02> -10 <03>] TJ <> Tj [] TJ ET Q')
    assert (row['Tf'], row['Tm'], row['Tj'], row['TJ']) == (1, 1, 2, 2)
    assert row['painting_text_show_count'] == 2
    assert row['selected_font_aliases'] == ['/F1']
    assert row['operator_nesting_violations'] == 0


def test_invalid_nesting_is_rejected():
    with pytest.raises(ValueError, match='nesting'):
        metrics.census(b'BT q ET Q')


def test_mutation_owners_explain_actual_bytes(monkeypatch):
    monkeypatch.setattr(metrics, 'program', lambda path, page: b'q Q' if path == 'old' else b'q  Q  ')
    state = dict(page_count=1, slots={'source': {}, 'generated': {'destination_id': 'dest'}})
    report = dict(mutation_map={'1': {'mutations': [
        dict(owner='source', start=0, end=1, length=3, kind='ordinary'),
        dict(owner='generated', start=1, end=2, length=2, kind='rewrite')]}})
    # An empty source slot dictionary still denotes an explicitly recorded slot.
    row = metrics.mutation_breakdown('old', 'new', state, report)['1']
    assert (row['delta_bytes'], row['source_slot'], row['generated_continuation'], row['other']) == (3, 2, 1, 0)
    report['mutation_map']['1']['mutations'].pop()
    with pytest.raises(ValueError, match='does not explain'):
        metrics.mutation_breakdown('old', 'new', state, report)


def test_compact_summary_cannot_hide_growth_on_another_page():
    from evaluations.continuation.generated_block_canonical import compact_summary
    row = dict(page_program={'7': dict(delta_bytes=0, before_operators=10, after_operators=10, program_bytes_equal=True)},
               renderer_checks={'7': dict(mupdf_equal=True, poppler_changed_pixels=0)})
    evidence = dict(scenarios={'sample': dict(lifecycle_stages=[row], supplemental_noops={})})
    compact = compact_summary(evidence)['scenarios']['sample']['lifecycle_stages'][0]
    assert compact['unchanged_other_pages'] == [7]
    assert compact['renderer_checks']['pages_checked'] == [7]
    assert '7' in row['page_program']  # Raw evidence is not modified.
    row['page_program']['7']['delta_bytes'] = 1
    with pytest.raises(ValueError, match='cannot omit a changed page'):
        compact_summary(evidence)
    row['page_program']['7'].update(delta_bytes=0, program_bytes_equal=False)
    with pytest.raises(ValueError, match='cannot omit a changed page'):
        compact_summary(evidence)
