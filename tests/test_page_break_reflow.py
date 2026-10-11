"""Independent source paragraphs separated by a page break, with separate empty authority."""
from copy import deepcopy

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.continuation import confirm_continuation_destination, slot_id
from pdfeditor.owned_flow_proposal import propose_owned_flow, accept_owned_flow_report, accept_owned_flow
from pdfeditor.page_proposal import propose_page_flow, replace_in_flow, _seal
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow, _contract, _reseal
from test_cross_page_owned_flow import make_source, ROW

GROUPS = [dict(id=pid, parts=[dict(page=n, paragraph_id='P1')]) for pid, n in [('A', 1), ('B', 2)]]
BREAK = dict(before='A', after='B', minimum_baseline_gap=26.5,
             placement='source-region-floor-and-same-page-follow')


@pytest.fixture(scope='module')
def independent(tmp_path_factory):
    root = tmp_path_factory.mktemp('independent-page-break')
    source, font = make_source(root, rows=[
        [(ROW[:30], 270, True), ('申請期限を確認。', 291.6, False)],
        [(ROW[:30], 80, True), ('必要書類を提出してください。', 100.4, False)]])
    pages = [propose_page_flow(source, n, font_candidates=[font], line_ids=[f'p{n}-l{i}' for i in ids])
             for n, ids in [(1, (2, 3)), (2, (1, 2))]]
    assert all(p['status'] == 'proposed' for p in pages), pages
    region = dict(page=2, bounds=[40, 50, 205, 70], x=40, width=165, first_baseline=62)
    dest = confirm_continuation_destination(source, destination_id='prefix', paragraph_id='A', region_id='prefix-2',
        page=2, bounds=region['bounds'], insertion='before-page-program', graphics_state='isolated-pdf-initial-state')
    p = propose_owned_flow(source, pages, paragraphs=GROUPS,
        relationship='A finishes before the page break; B is the related independent next paragraph.',
        page_breaks=[BREAK], page_overrides={'2': {'region_top': 70}},
        continuations=[dict(region_id='prefix-2', before_region='page-2', region=region, destination=dest)])
    accepted = accept_owned_flow_report(source, p)
    return source, p, accepted, dest


def test_noedit_keeps_original_page_starts_and_independent_owners(independent, tmp_path):
    source, p, accepted, dest = independent
    state = accepted['state']
    assert accepted['receipt']['reproduction']['lines'] == 4
    assert state['paragraphs']['A']['logical']['text'].endswith('確認。')
    assert state['slots']['slot-0']['paragraph_id'] == 'A' and state['slots']['slot-1']['paragraph_id'] == 'B'
    assert slot_id(dest) not in state['slots']
    pdf, model = tmp_path / 'noop.pdf', tmp_path / 'noop.json'
    report = edit_shared_flow(source, state, pdf, model, {})
    assert report['plan']['fragments']['slot-1']['lines'][0]['baseline'] == 80
    assert open_shared_flow(pdf, model)['status'] == 'restored'
    with pymupdf.open(source) as a, pymupdf.open(pdf) as b:
        assert [page.get_pixmap().samples for page in a] == [page.get_pixmap().samples for page in b]


def test_grow_moves_independent_follower_then_shorten_regrow(independent, tmp_path):
    original, _, accepted, dest = independent
    state = accepted['state']; source = original; sha = source_sha(original)
    old_b = state['paragraphs']['B']['logical']['text']; longer = '期限' + ROW
    edits = [{'A': ('期限', longer)}, {'B': ('必要', '各種必要')},
             {'A': (longer, '期限'), 'B': ('各種必要', '必要')}, {'A': ('期限', longer)}, {}]
    for index, replacements in enumerate(edits):
        changes = {}
        for pid, edit in replacements.items():
            changes.update(replace_in_flow(state, *edit, paragraph_id=pid,
                style_id='page-1:style-1' if pid == 'A' else 'page-2:style-1'))
        pdf, model = tmp_path / f'{index}.pdf', tmp_path / f'{index}.json'
        report = edit_shared_flow(source, state, pdf, model, changes)
        opened = open_shared_flow(pdf, model); assert opened['status'] == 'restored'; state = opened['state']
        follower = report['plan']['fragments']['slot-1']
        continuation = state['slots'][slot_id(dest)]
        if index == 2:
            assert continuation['occupancy'] is None and follower['lines'][0]['baseline'] == 80
            assert state['paragraphs']['B']['logical']['text'] == old_b
        else:
            assert continuation['occupancy'] is not None
            assert follower['lines'][0]['baseline'] == pytest.approx(88.5, abs=.002)
            assert follower['lines'][0]['baseline'] - continuation['occupancy']['last_baseline'] == pytest.approx(26.5, abs=.002)
        if index == 0:
            step = next(s for s in report['steps'] if s['slot_id'] == 'slot-1')
            assert step['report']['provided_font_glyph_count'] == 0
        with pymupdf.open(original) as a, pymupdf.open(pdf) as b:
            for x, y in zip(a, b):
                assert x.get_pixmap(clip=pymupdf.Rect(35, 230, 200, 250)).samples == y.get_pixmap(clip=pymupdf.Rect(35, 230, 200, 250)).samples
        source = pdf
    assert source_sha(original) == sha


@pytest.mark.parametrize('kind', ['missing', 'zero', 'nan', 'wrong-order', 'wrong-placement'])
def test_missing_or_invalid_page_break_policy_refuses(independent, kind):
    source, p, _, _ = independent
    changed = deepcopy(p)
    if kind == 'missing': changed.pop('page_breaks')
    elif kind == 'zero': changed['page_breaks'][0]['minimum_baseline_gap'] = 0
    elif kind == 'nan': changed['page_breaks'][0]['minimum_baseline_gap'] = float('nan')
    elif kind == 'wrong-order': changed['page_breaks'][0]['before'] = 'B'
    else: changed['page_breaks'][0]['placement'] = 'guess-page-coordinate-gap'
    with pytest.raises(PdfError):
        accept_owned_flow(source, _seal(changed))


def test_missing_continuation_cannot_borrow_follower_source(independent, tmp_path):
    source, p, _, _ = independent
    changed = deepcopy(p); changed.pop('continuations')
    state = accept_owned_flow(source, _seal(changed))
    pdf, model = tmp_path / 'unapproved.pdf', tmp_path / 'unapproved.json'
    with pytest.raises(PdfError, match='new continuation is not authorized'):
        edit_shared_flow(source, state, pdf, model,
            replace_in_flow(state, '期限', '期限' + ROW, paragraph_id='A', style_id='page-1:style-1'))
    assert not pdf.exists() and not model.exists()


def test_capacity_foreign_bounds_and_region_floor_are_not_relaxed(independent, tmp_path):
    source, p, accepted, _ = independent
    pdf, model = tmp_path / 'overflow.pdf', tmp_path / 'overflow.json'
    with pytest.raises(PdfError):
        edit_shared_flow(source, accepted['state'], pdf, model,
            replace_in_flow(accepted['state'], '期限', ROW * 3, paragraph_id='A', style_id='page-1:style-1'))
    assert not pdf.exists() and not model.exists()
    changed = deepcopy(p); changed.pop('page_overrides')
    with pytest.raises(PdfError, match='disjoint'):
        accept_owned_flow(source, _seal(changed))
    changed = deepcopy(accepted['state'])
    changed['follows'][0]['source_break']['after_region'] = 'prefix-2'
    changed['contract_sha256'] = _contract(changed)
    assert open_shared_flow(source, _reseal(changed))['status'] == 'needs_confirmation'


def test_prefix_authority_is_specific_to_the_preceding_paragraph(independent):
    source, p, _, _ = independent
    changed = deepcopy(p)
    changed['continuations'][0]['destination']['paragraph_id'] = 'B'
    with pytest.raises(PdfError, match='must serve the predecessor'):
        accept_owned_flow(source, _seal(changed))
