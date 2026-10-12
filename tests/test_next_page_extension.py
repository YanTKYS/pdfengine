"""Adopt owners on the next existing page from a restored edited revision."""
from copy import deepcopy

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.continuation import confirm_continuation_destination, slot_id
from pdfeditor.owned_flow_proposal import propose_owned_flow, propose_owned_flow_extension, accept_owned_flow_report
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, replace_in_flow, _seal
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from test_cross_page_owned_flow import make_source, ROW
from test_page_break_reflow import BREAK

LONG = '期限' + ROW[:36]
PLACEMENT = 'source-region-floor-and-same-page-follow'
RELEASE = dict(page=2, bounds=[40, 50, 206, 116], x=40, width=166, first_baseline=62)
FOLLOWERS = [dict(id='C', parts=[dict(page=2, paragraph_id='P1')]), dict(id='D', parts=[dict(page=2, paragraph_id='P2')])]


def request(region=RELEASE, release=('C',), gap=26.5):
    return dict(region_id='page-2', page_break=dict(before='P2', after='C', minimum_baseline_gap=gap, placement=PLACEMENT),
        vacated_continuation=dict(region_id='release-2', region=region, release_paragraphs=list(release),
            confirmation=dict(destination_id='B-next', paragraph_id='P2', region_id='release-2', page=2,
                bounds=region['bounds'], insertion='before-page-program', graphics_state='isolated-pdf-initial-state')))


def saved_case(root):
    """A and B jointly owned on page 1 only; B is edited, saved and restored."""
    original, font = make_source(root, height=340, rows=[
        [(ROW[:30], 262, True), ('申請期限を確認。', 282.4, False), (ROW[:30], 308.8, True), ('書類を確認してください。', 329.2, False)],
        [(ROW[:30], 80, True), ('必要書類を提出してください。', 100.4, False), (ROW[:30], 126.9, True), ('書類を確認してください。', 147.3, False)]])
    first = accept_page_flow(original, propose_page_flow(original, 1, font_candidates=[font],
                                                         line_ids=[f'p1-l{i}' for i in range(2, 6)]))
    pdf, side = root / 'saved.pdf', root / 'saved.json'
    edit_shared_flow(original, first, pdf, side, replace_in_flow(first, '確認して', '再確認して', paragraph_id='P2', style_id='style-1'))
    opened = open_shared_flow(pdf, side); assert opened['status'] == 'restored'
    page = propose_page_flow(pdf, 2, font_candidates=[font], line_ids=[f'p2-l{i}' for i in range(1, 5)])
    return original, pdf, opened['state'], page, font


def propose(source, base, page, followers=FOLLOWERS, **next_page):
    return propose_owned_flow_extension(source, base, [page], paragraphs=followers,
        relationship='C and D are the application instructions that follow B on the next page.',
        next_page=next_page or request())


def exterior_equal(before, after, state):
    """MuPDF raster outside every confirmed region of each page is unchanged."""
    from PIL import Image, ImageChops, ImageDraw
    with pymupdf.open(before) as a, pymupdf.open(after) as b:
        for number, (x, y) in enumerate(zip(a, b), 1):
            images = [Image.frombytes('RGB', (p.width, p.height), p.samples) for p in (x.get_pixmap(), y.get_pixmap())]
            diff = ImageChops.difference(*images)
            for r in state['regions'].values():
                if r['page'] == number:
                    box = pymupdf.Rect(r['bounds']).irect
                    ImageDraw.Draw(diff).rectangle((box.x0, box.y0, box.x1, box.y1), fill=(0, 0, 0))
            if diff.getbbox() is not None:
                return False
    return True


def test_next_page_owner_turns_refused_growth_into_restored_lifecycle(tmp_path):
    original, source, base, page, _ = saved_case(tmp_path); sha = source_sha(original)
    refused, side = tmp_path / 'refused.pdf', tmp_path / 'refused.json'
    with pytest.raises(PdfError, match='exceed all explicitly confirmed'):
        edit_shared_flow(source, base, refused, side, replace_in_flow(base, '期限', LONG, paragraph_id='P1', style_id='style-1'))
    assert not refused.exists() and not side.exists()
    proposal = propose(source, base, page)
    assert base['flow']['regions'] == ['R1']  # Proposing grants no authority.
    review = proposal['edited_extension']['review']
    assert review['page_boundary']['previous_page'] == 1 and review['page_boundary']['next_page'] == 2
    assert review['page_break']['source_break'] == dict(before_region='R1', after_region='page-2')
    accepted = accept_owned_flow_report(source, proposal); state = accepted['state']
    assert accepted['receipt']['reproduction']['lines'] == 8
    assert state['flow']['regions'] == ['R1', 'release-2', 'page-2']
    for pid in ('P1', 'P2'): assert state['paragraphs'][pid] == base['paragraphs'][pid]
    for sid, slot in base['slots'].items():
        assert state['slots'][sid]['binding'] == slot['binding']
        assert state['slots'][sid]['source_output'] == slot['source_output']
    adopted = {sid for sid in state['slots'] if sid not in base['slots']}
    assert {state['slots'][s]['paragraph_id'] for s in adopted} == {'C', 'D'}
    assert all(state['source_adoptions'][s]['pdf_sha256'] == source_sha(source) for s in adopted)
    edge = next(e for e in state['follows'] if e['after'] == 'C')
    assert edge['minimum_baseline_gap'] == 26.5 and edge['source_break']['after_region'] == 'page-2'
    destination = state['continuation_destinations']['B-next']
    assert set(destination['vacated_space']['owners']) == {s for s in adopted if state['slots'][s]['paragraph_id'] == 'C'}
    noop, model = tmp_path / 'noop.pdf', tmp_path / 'noop.json'
    edit_shared_flow(source, state, noop, model, {})
    assert open_shared_flow(noop, model)['status'] == 'restored'
    with pymupdf.open(source) as a, pymupdf.open(noop) as b:
        assert [p.get_pixmap().samples for p in a] == [p.get_pixmap().samples for p in b]
    generated = slot_id(destination); c = next(s for s in adopted if state['slots'][s]['paragraph_id'] == 'C')
    stages = [('P1', '期限', LONG), ('P2', '再確認', '必要な再確認'), ('P1', LONG, '期限'), ('P1', '期限', LONG)]
    previous = source; creation = None
    for index, (pid, old, new) in enumerate(stages):
        pdf, model = tmp_path / f'{index}.pdf', tmp_path / f'{index}.json'
        report = edit_shared_flow(previous, state, pdf, model, replace_in_flow(state, old, new, paragraph_id=pid, style_id='style-1'))
        opened = open_shared_flow(pdf, model); assert opened['status'] == 'restored', opened
        fragments = report['plan']['fragments']
        if index == 0:
            # The uninitialized adopted owner is released in the same Transaction.
            witness = next(s for s in report['steps'] if s['slot_id'] == generated)['report']['vacated_space']
            assert [o['source_owner'] for o in witness['owners']] == [c]
        if index == 2:
            assert opened['state']['slots'][generated]['occupancy'] is None
            assert fragments[c]['lines'][0]['baseline'] == 80
        else:
            assert fragments[generated]['lines'][0]['baseline'] == 62
            assert fragments[c]['lines'][0]['baseline'] > 80
        assert exterior_equal(previous, pdf, opened['state'])
        state = opened['state']; previous = pdf
        creation = creation or state['slots'][generated]['creation_binding']
        assert state['slots'][generated]['creation_binding'] == creation
    with pymupdf.open(previous) as doc:
        assert 'FIXED CONTENT' in doc[1].get_text() and doc[1].get_text().count('FIXED CONTENT') == 1
    assert source_sha(original) == sha


@pytest.mark.parametrize('kind', ['stale', 'review', 'gap', 'skipped', 'release-existing', 'unreleased-owner',
                                  'fixed-paint', 'no-continuation', 'capacity'])
def test_next_page_extension_refuses_without_authority(tmp_path, kind):
    original, source, base, page, font = saved_case(tmp_path)
    if kind == 'stale':
        proposal = propose(source, base, page)
        with pytest.raises(PdfError, match='another PDF revision'):accept_owned_flow_report(original, proposal)
    elif kind == 'review':
        proposal = propose(source, base, page)
        proposal['edited_extension']['review']['page_break']['minimum_baseline_gap'] = 20
        with pytest.raises(PdfError, match='review differs'):accept_owned_flow_report(source, _seal(proposal))
    elif kind == 'gap':
        with pytest.raises(PdfError, match='same-page gap'):propose(source, base, page, **request(gap=float('nan')))
    elif kind == 'skipped':
        later = propose_page_flow(source, 2, font_candidates=[font], line_ids=['p2-l3', 'p2-l4'])
        with pytest.raises(PdfError, match='skips unselected text before'):
            propose(source, base, later, [dict(id='C', parts=[dict(page=2, paragraph_id='P1')])], **request())
    elif kind == 'release-existing':
        with pytest.raises(PdfError, match='only explicitly adopted'):propose(source, base, page, **request(release=('P2',)))
    elif kind in ('unreleased-owner', 'fixed-paint'):
        options = request(release=('D',)) if kind == 'unreleased-owner' else request(
            region=dict(RELEASE, bounds=[40, 50, 206, 245]))
        proposal = propose(source, base, page, **options)
        with pytest.raises(PdfError):accept_owned_flow_report(source, proposal)
    else:
        options = request() if kind == 'capacity' else {k: v for k, v in request().items() if k != 'vacated_continuation'}
        state = accept_owned_flow_report(source, propose(source, base, page, **options))['state']
        pdf, side = tmp_path / 'refused.pdf', tmp_path / 'refused.json'
        # Without authority B cannot continue at all; with it, B still cannot
        # exceed the explicitly accepted continuation capacity.
        pid, old, new = ('P1', '期限', LONG) if kind == 'no-continuation' else ('P2', '再確認', '再確認' + ROW * 4)
        with pytest.raises(PdfError, match='new continuation is not authorized'):
            edit_shared_flow(source, state, pdf, side, replace_in_flow(state, old, new, paragraph_id=pid, style_id='style-1'))
        assert not pdf.exists() and not side.exists()


def test_next_page_keeps_existing_generated_continuation_identity(tmp_path):
    """Three pages: page-2 generated A continuation survives adopting C on page 3."""
    source, font = make_source(tmp_path, rows=[
        [(ROW[:30], 270, True), ('申請期限を確認。', 291.6, False)],
        [(ROW[:30], 270, True), ('必要書類を提出してください。', 291.6, False)],
        [(ROW[:30], 80, True), ('書類を確認してください。', 100.4, False)]], fixed_pages={0, 2})
    pages = [propose_page_flow(source, n, font_candidates=[font], line_ids=ids)
             for n, ids in [(1, ['p1-l2', 'p1-l3']), (2, ['p2-l1', 'p2-l2'])]]
    prefix = dict(page=2, bounds=[40, 50, 205, 225], x=40, width=165, first_baseline=62)
    dest = confirm_continuation_destination(source, destination_id='A-prefix', paragraph_id='A', region_id='prefix-2',
        page=2, bounds=prefix['bounds'], insertion='before-page-program', graphics_state='isolated-pdf-initial-state')
    groups = [dict(id=pid, parts=[dict(page=n, paragraph_id='P1')]) for pid, n in [('A', 1), ('B', 2)]]
    first = accept_owned_flow_report(source, propose_owned_flow(source, pages, paragraphs=groups,
        relationship='A and B are consecutive instructions on pages 1 and 2.', page_breaks=[BREAK],
        page_overrides={'2': {'region_top': 230}},
        continuations=[dict(region_id='prefix-2', before_region='page-2', region=prefix, destination=dest)]))['state']
    saved, side = tmp_path / 'saved.pdf', tmp_path / 'saved.json'
    edit_shared_flow(source, first, saved, side, replace_in_flow(first, '期限', LONG, paragraph_id='A', style_id='page-1:style-1'))
    base = open_shared_flow(saved, side)['state']; generated = slot_id(dest)
    assert base['slots'][generated]['occupancy'] is not None
    growth = ('必要書類', '必要書類' + ROW[:36])
    with pytest.raises(PdfError, match='exceed all'):
        edit_shared_flow(saved, base, tmp_path / 'x.pdf', tmp_path / 'x.json',
                         replace_in_flow(base, *growth, paragraph_id='B', style_id='page-2:style-1'))
    page = propose_page_flow(saved, 3, font_candidates=[font], line_ids=['p3-l1', 'p3-l2'])
    region = dict(page=3, bounds=[40, 50, 205, 70], x=40, width=165, first_baseline=62)
    empty = confirm_continuation_destination(saved, destination_id='B-next', paragraph_id='B', region_id='next-3',
        page=3, bounds=region['bounds'], insertion='before-page-program', graphics_state='isolated-pdf-initial-state')
    proposal = propose_owned_flow_extension(saved, base, [page], paragraphs=[dict(id='C', parts=[dict(page=3, paragraph_id='P1')])],
        relationship='C is the next instruction after B, on page 3.', next_page=dict(region_id='page-3',
            page_break=dict(before='B', after='C', minimum_baseline_gap=26.5, placement=PLACEMENT),
            page_overrides={'region_top': 70}, continuation=dict(region_id='next-3', region=region, destination=empty)))
    state = accept_owned_flow_report(saved, proposal)['state']
    assert state['slots'][generated] == base['slots'][generated]
    assert state['continuation_destinations']['A-prefix'] == base['continuation_destinations']['A-prefix']
    assert state['destination_bindings']['A-prefix'] == base['destination_bindings']['A-prefix']
    assert state['generated_fonts'] == base['generated_fonts']
    pdf, model = tmp_path / 'grown.pdf', tmp_path / 'grown.json'
    report = edit_shared_flow(saved, state, pdf, model, replace_in_flow(state, *growth, paragraph_id='B', style_id='page-2:style-1'))
    opened = open_shared_flow(pdf, model); assert opened['status'] == 'restored', opened
    fragments = report['plan']['fragments']; c = next(s for s in state['slots'] if state['slots'][s]['paragraph_id'] == 'C')
    assert fragments[slot_id(empty)]['lines'][0]['baseline'] == 62 and fragments[c]['lines'][0]['baseline'] == 88.5
    after = opened['state']['slots'][generated]
    assert after['creation_binding'] == base['slots'][generated]['creation_binding']
    assert after['binding']['paragraph']['text'] == base['slots'][generated]['binding']['paragraph']['text']
    assert exterior_equal(saved, pdf, opened['state'])
