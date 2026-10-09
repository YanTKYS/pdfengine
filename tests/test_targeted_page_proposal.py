"""Target an observed paragraph below a table without assigning that table to flow."""
from copy import deepcopy
import json

import pymupdf
import pytest

from pdfeditor.attributed import digest
from pdfeditor.backend import PdfError
from pdfeditor.cli import main
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, replace_in_flow
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from test_page_proposal import pixels
import page_flow_fixture as fx


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp('targeted-page')
    font = fx.write_fonts(root / 'fonts')['full']
    rows = [(85, 120, 14, fx.TITLE),
            (85, 160, fx.SIZE, '申請書'), (350, 160, fx.SIZE, '提出期限'),
            (85, 180, fx.SIZE, '受付窓口'), (350, 180, fx.SIZE, '必要書類'),
            (85, 240, fx.SIZE, fx.PARAGRAPHS[1]), (290, 800, fx.SIZE, fx.FOOTER)]
    source = fx.make_page(root / 'table-and-body.pdf', font.read_bytes(), lines=rows,
                          drawings=[(80, 190, 500, 191)])
    target = ['p1-l6']
    proposal = propose_page_flow(source, font_candidates=[font], line_ids=target)
    return source, font, proposal, rows


def test_targeted_lifecycle_preserves_table_and_fixed_paint(world, tmp_path):
    source, font, proposal, _ = world
    original_sha = source_sha(source)
    whole = propose_page_flow(source, font_candidates=[font])
    assert whole['status'] == 'refused'
    assert any(r['code'] == 'multiple-columns' for r in whole['refusals'])
    assert proposal['status'] == 'proposed', proposal['refusals']
    assert proposal['target'] == dict(line_ids=['p1-l6'], provenance='caller-selected-observed-lines')
    assert proposal['paragraphs'][0]['text'] == fx.PARAGRAPHS[1]
    assert len([f for f in proposal['foreign_content'] if f['kind'] == 'text']) == 6
    state = accept_page_flow(source, proposal)
    before = source
    for i, edit in enumerate((fx.FIRST_EDIT, fx.SECOND_EDIT), 1):
        output, sidecar = tmp_path / f'rev{i}.pdf', tmp_path / f'rev{i}.json'
        edit_shared_flow(before, state, output, sidecar, replace_in_flow(state, *edit))
        opened = open_shared_flow(output, json.loads(sidecar.read_text(encoding='utf-8')))
        assert opened['status'] == 'restored', opened.get('reason')
        state, before = opened['state'], output
        for rect in ((0, 0, fx.PAGE[0], 225), (0, 300, fx.PAGE[0], fx.PAGE[1])):
            assert pixels(output, rect) == pixels(source, rect)
        with pymupdf.open(output) as changed, pymupdf.open(source) as original:
            drawing = lambda page: [{k: v for k, v in d.items() if k != 'seqno'} for d in page.get_drawings()]
            assert drawing(changed[0]) == drawing(original[0])
            assert '各種申請書' in changed[0].get_text()
    with pymupdf.open(before) as doc:
        body = [line for block in doc[0].get_text('dict')['blocks'] for line in block.get('lines', [])
                if 230 < line['spans'][0]['origin'][1] < 300]
        assert len(body) == 2
    assert source_sha(source) == original_sha


@pytest.mark.parametrize('ids', [[], ['p1-l99'], ['p2-l6'], ['p1-l6', 'p1-l6'],
                                  ['p1-l6', 'p1-l5'], ['p1-l2', 'p1-l6'], 'p1-l6'])
def test_target_must_be_distinct_consecutive_observed_lines(world, ids):
    source, font, _, _ = world
    with pytest.raises(PdfError, match='line_ids'):
        propose_page_flow(source, font_candidates=[font], line_ids=ids)


def test_target_recomputed_even_after_resealing(world):
    source, _, proposal, _ = world
    changed = deepcopy(proposal)
    changed['target']['line_ids'] = ['p1-l7']
    changed.pop('digest')
    changed['digest'] = digest(changed)
    with pytest.raises(PdfError, match='stale'):
        accept_page_flow(source, changed)


def test_target_keeps_foreign_and_tagged_guards(world, tmp_path):
    source, font, _, rows = world
    # A selected band with two side-by-side cells still cannot become one flow.
    p = propose_page_flow(source, font_candidates=[font], line_ids=['p1-l2', 'p1-l3'])
    assert p['status'] == 'refused' and any(r['code'] == 'multiple-columns' for r in p['refusals'])
    collision = fx.make_page(tmp_path / 'collision.pdf', font.read_bytes(), lines=rows,
                             drawings=[(80, 235, 300, 245)])
    p = propose_page_flow(collision, font_candidates=[font], line_ids=['p1-l6'])
    assert p['status'] == 'refused' and any(r['code'] == 'region' for r in p['refusals'])
    orphan = fx.make_page(tmp_path / 'orphan.pdf', font.read_bytes(), lines=rows, tagged=True)
    p = propose_page_flow(orphan, font_candidates=[font], line_ids=['p1-l6'])
    assert p['status'] == 'refused'
    assert any(r['code'] == 'unsupported-for-persistent-flow' for r in p['refusals'])


def test_target_retains_irregular_position_refusal(world, tmp_path):
    _, font, _, rows = world
    # Like the real Okinawa body, independent Tm positions contract selected gaps.
    text = rows[5][3]
    fragmented = [(85 + i * fx.SIZE - (0.12 if i >= 3 else 0), 240, fx.SIZE, char)
                  for i, char in enumerate(text)]
    source = fx.make_page(tmp_path / 'positioned.pdf', font.read_bytes(), lines=rows[:5] + fragmented + rows[6:])
    p = propose_page_flow(source, font_candidates=[font], line_ids=['p1-l6'])
    assert p['status'] == 'refused'
    assert any(r['code'] == 'layout-not-reproduced' for r in p['refusals'])


def test_cli_target(world, tmp_path):
    source, font, proposal, _ = world
    output = tmp_path / 'proposal.json'
    assert main(['propose-page', str(source), '--font', str(font), '--line', 'p1-l6',
                 '--json', str(output)]) == 0
    assert json.loads(output.read_text(encoding='utf-8')) == proposal
