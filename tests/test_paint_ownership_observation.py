"""Evidence accounting and existing guards, not a future ownership verifier."""
import json
from pathlib import Path

import pymupdf
import pytest

from evaluations.anchors.paint_ownership import census, mutation_measure
from pdfeditor.backend import PdfError
from pdfeditor.editable import edit_document, write_editable
from pdfeditor.mutation import IdentityMap
from test_anchors import prepared
from test_editable import first


def test_operator_census_does_not_count_tokens_inside_strings_or_comments():
    data = b'% q m f\nBT (q m f Q) Tj ET 0 0 m 1 0 l 1 1 l h f'
    result = census(data)
    assert result['operators'] == 8
    assert result['fill_operators'] == 1 and result['q'] == result['Q'] == 0
    assert result['path_operators'] == 5
    assert result['text_token_bytes'] == len(b'BT') + len(b'(q m f Q) Tj') + len(b'ET')


def test_decoration_delta_is_attributed_without_claiming_source_construction():
    old = b'0 0 m 1 0 l 1 1 l h f 7 w'
    start = old.index(b'f')
    replacement = b'n q 0 0 m 2 0 l 2 1 l h f Q '
    new = old[:start] + replacement + old[start+1:]
    records = [dict(start=start, end=start+1, length=len(replacement),
                    kind='decoration', owner=None, anchors={})]
    measured = mutation_measure(old, new, records)
    assert measured['unchanged_gaps_verified']
    assert measured['by_kind_owner']['decoration / None']['delta_bytes'] == len(replacement)-1
    body = measured['emitted_group_payloads'][0]
    assert body['source_terminal_range'] == [start, start+1]
    assert body['output_start'] == start+2
    assert body['operators'] == ['q', 'm', 'l', 'l', 'h', 'f', 'Q']
    with pytest.raises(ValueError, match='suffix'):
        mutation_measure(old, new[:-1]+b'J', records)


def test_current_anchored_empty_refusal_publishes_neither_file(tmp_path):
    _, pdf, state, _ = first(tmp_path)
    model = json.loads(state.read_text(encoding='utf-8'))
    output, saved = tmp_path/'empty.pdf', tmp_path/'empty.json'
    with pytest.raises(PdfError, match='explicit dormant decoration/ownership semantics'):
        edit_document(pdf, state, output, saved,
            [dict(start=0, end=len(model['paragraph']['text']), text='',
                  style_id=model['paragraph']['styles'][0]['id'])])
    assert not output.exists() and not saved.exists()


def test_ordinary_source_output_owner_guard_stays_closed(tmp_path):
    _, pdf, state, _ = first(tmp_path)
    output, saved = tmp_path/'flag.pdf', tmp_path/'flag.json'
    with pytest.raises(PdfError, match='source output requires a text-only owning source slot'):
        edit_document(pdf, state, output, saved, [], _source_output={'state': 'owned'})
    assert not output.exists() and not saved.exists()


def test_late_emitted_path_rebind_failure_keeps_publication_empty(tmp_path, monkeypatch):
    source, paragraph, element, anchors = prepared(tmp_path)
    font = tmp_path/'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    calls = []

    def failed(self, mutation, names):
        # At this point Transaction has saved its temporary PDF.
        assert Path(self.output).exists()
        calls.append(True)
        raise PdfError('injected final paint rebind failure')

    monkeypatch.setattr(IdentityMap, 'emitted_paths', failed)
    output, saved = tmp_path/'edited.pdf', tmp_path/'edited.json'
    with pytest.raises(PdfError, match='injected final paint rebind failure'):
        write_editable(source, output, saved, paragraph, [],
            fonts={'s0': {'path': str(font)}}, element_snapshot=element,
            anchor_spec=anchors, max_bottom=115)
    assert calls and not output.exists() and not saved.exists()
    assert not list(tmp_path.glob('.editable-*'))


def test_public_observation_reports_growth_refusal_and_ctm_instability():
    summary = json.loads((Path(__file__).resolve().parents[1] /
        'evaluations/anchors/paint-ownership-summary.json').read_text(encoding='utf-8'))
    cases = summary['cases']
    lifecycle = cases['lifecycle']
    for name in ('noop1', 'noop2', 'noop3'):
        stage = lifecycle[name]
        assert stage['delta']['bytes'] == 2028 and stage['delta']['operators'] == 192
        assert stage['mutation']['by_kind_owner']['decoration / None']['delta_bytes'] == 244
        assert stage['bindings'][0]['current_anchor_paints'] == 3
    assert lifecycle['empty']['status'] == 'refused'
    assert not lifecycle['empty']['pdf_published'] and not lifecycle['empty']['sidecar_published']
    assert lifecycle['empty_noop']['status'] == lifecycle['regrow']['status'] == 'unreachable'
    assert [len(cases['delete_group'][name]['bindings'][0]['groups']) for name in
            ('first', 'delete', 'delete_noop', 'insert_again')] == [2, 1, 1, 1]
    ctm_hashes = [cases['ctm'][name]['mutation']['emitted_group_payloads'][0]['sha256']
                  for name in ('first', 'noop1', 'noop2', 'noop3')]
    assert len(set(ctm_hashes)) == 4
    renders = [stage['render'] for case in cases.values() for stage in case.values() if 'render' in stage]
    assert len(renders) == 16 and all(r['passed'] for r in renders)
    assert len(cases['two']['noop1']['bindings']) == 2
    assert cases['styles']['first']['bindings'][0]['styles'] == 2
    painted = cases['paint_styles']['first']['bindings'][0]
    assert len(painted['groups']) == 2
    assert {p['colorspace'] for p in painted['anchor_template_paints']} == {'DeviceGray', 'DeviceRGB'}
    for case in cases.values():
        for stage in case.values():
            if 'paint_inventory' not in stage:
                continue
            inventory = stage['paint_inventory']
            assert sum(inventory[key] for key in ('current_anchor_path_token_bytes', 'fixed_path_token_bytes',
                'other_current_path_token_bytes', 'unbound_path_token_bytes')) == stage['page']['path_token_bytes']
            assert stage.get('source_KEEP_state_and_glyphs_equal', True)
