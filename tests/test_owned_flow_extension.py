"""Additional source owners require a new review/acceptance on the original PDF."""
from copy import deepcopy

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.owned_flow_proposal import (propose_owned_flow, propose_owned_flow_extension,
    accept_owned_flow_report)
from pdfeditor.page_proposal import propose_page_flow, replace_in_flow, _seal
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from test_cross_page_owned_flow import make_source, ROW
from test_page_break_reflow import GROUPS, BREAK


def setup_case(root):
    source, font = make_source(root, rows=[
        [(ROW[:30],270,True), ('申請期限を確認。',291.6,False)],
        [(ROW[:30],80,True), ('必要書類を提出してください。',100.4,False),
         (ROW[:30],126.9,True), ('書類を確認してください。',147.3,False)]])
    first = propose_page_flow(source, 1, font_candidates=[font], line_ids=['p1-l2','p1-l3'])
    small = propose_page_flow(source, 2, font_candidates=[font], line_ids=['p2-l1','p2-l2'])
    full = propose_page_flow(source, 2, font_candidates=[font], line_ids=[f'p2-l{i}' for i in range(1,5)])
    region = dict(page=2,bounds=[40,50,205,116],x=40,width=165,first_baseline=62)
    request = dict(region_id='vacated-2',before_region='page-2',region=region,release_paragraphs=['B'],
        confirmation=dict(destination_id='release-B',paragraph_id='A',region_id='vacated-2',page=2,
            bounds=region['bounds'],insertion='before-page-program',graphics_state='isolated-pdf-initial-state'))
    options = dict(relationship='Explicit consecutive application instructions A, B and C.',
        page_breaks=[BREAK],vacated_continuations=[request])
    base = accept_owned_flow_report(source,propose_owned_flow(source,[first,small],paragraphs=GROUPS,**options))['state']
    groups = GROUPS + [dict(id='C',parts=[dict(page=2,paragraph_id='P2')])]
    proposal = propose_owned_flow_extension(source,base,[first,full],paragraphs=groups,**options)
    return source,base,proposal


def test_additional_owner_turns_same_refused_edit_into_restored_lifecycle(tmp_path):
    original,base,proposal = setup_case(tmp_path); sha = source_sha(original)
    longer = '期限'+ROW*2
    changes = replace_in_flow(base,'期限',longer,paragraph_id='A',style_id='page-1:style-1')
    pdf, side = tmp_path/'refused.pdf', tmp_path/'refused.json'
    with pytest.raises(PdfError):edit_shared_flow(original,base,pdf,side,changes)
    assert not pdf.exists() and not side.exists()
    assert base['flow']['paragraphs'] == ['A','B']  # A proposal alone changes no authority.
    accepted = accept_owned_flow_report(original,proposal); state = accepted['state']
    review = accepted['receipt']['extension']
    assert [p['paragraph_id'] for p in review['added_paragraphs']] == ['C']
    assert review['base_model_sha256'] == base['model_sha256']
    bottom = base['regions']['page-2']['bounds'][3]
    assert state['regions']['page-2']['bounds'][3] > bottom
    noop, model = tmp_path/'noop.pdf',tmp_path/'noop.json'
    edit_shared_flow(original,state,noop,model,{})
    with pymupdf.open(original) as a,pymupdf.open(noop) as b:
        assert [p.get_pixmap().samples for p in a] == [p.get_pixmap().samples for p in b]
    source = original
    for index, (old,new) in enumerate([('期限',longer),('確認','再確認'),(longer,'期限'),('期限',longer)]):
        pdf,model = tmp_path/f'{index}.pdf',tmp_path/f'{index}.json'
        report = edit_shared_flow(source,state,pdf,model,
            replace_in_flow(state,old,new,paragraph_id='A',style_id='page-1:style-1'))
        opened = open_shared_flow(pdf,model); assert opened['status'] == 'restored'
        state = opened['state']
        if index == 0:
            assert max(r[3] for r in report['plan']['fragments']['slot-1']['ink_bounds']) > bottom
            assert report['plan']['fragments']['slot-2']['lines'][0]['baseline'] > 126.9
            assert next(s for s in report['steps'] if s['slot_id']=='slot-2')['report']['provided_font_glyph_count']==0
        assert state['paragraphs']['C']['logical']['text'] == review['added_paragraphs'][0]['parts'][0]['text']
        source = pdf
    assert source_sha(original) == sha
    from pdfeditor.owned_flow_proposal import _extension_review
    with pytest.raises(PdfError,match='original accepted source'):
        _extension_review(source,state,proposal)


@pytest.mark.parametrize('kind', ['review','base','order','existing-owner','added-owner'])
def test_changed_extension_evidence_refuses(tmp_path,kind):
    source,base,proposal = setup_case(tmp_path)
    p = deepcopy(proposal)
    if kind == 'review':p['extension']['review']['added_paragraphs'][0]['parts'][0]['text'] = 'forged'
    elif kind == 'base':p['extension']['base_model']['pdf_sha256'] = '0'*64
    elif kind == 'order':p['paragraphs'][0]['id'],p['paragraphs'][1]['id'] = 'B','A'
    elif kind == 'existing-owner':p['pages'][1]['paragraphs'][0]['glyph_ids'] = [999]
    else:
        p['pages'][1]['paragraphs'][1]['glyph_ids'] = [999]
        p['extension']['review']['added_paragraphs'][0]['parts'][0]['glyph_ids'] = [999]
    with pytest.raises(PdfError):accept_owned_flow_report(source,_seal(p))


def test_same_owners_with_larger_rectangle_are_not_an_extension(tmp_path):
    source,base,proposal = setup_case(tmp_path)
    p = deepcopy(proposal)
    p['paragraphs'] = p['paragraphs'][:2]
    p['pages'][1]['paragraphs'] = p['pages'][1]['paragraphs'][:1]
    from pdfeditor.owned_flow_proposal import _extension_review
    with pytest.raises(PdfError,match='append whole source owners'):_extension_review(source,base,p)


def test_extension_cannot_borrow_foreign_capacity(tmp_path):
    source,base,proposal = setup_case(tmp_path)
    p = deepcopy(proposal)
    p['page_overrides'] = {'2': {'region_bottom': 250}}
    p['extension']['review']['proposed_geometry_overrides'] = p['page_overrides']
    with pytest.raises(PdfError):accept_owned_flow_report(source,_seal(p))


def test_existing_cross_page_parts_follow_region_order_not_json_key_order(tmp_path):
    from test_cross_page_owned_flow import GROUPS as JOINED
    source,font = make_source(tmp_path,rows=[
        [(ROW[:30],270,True),(ROW[:32],291.6,False)],
        [(ROW[:32],80,False),('申請期限を確認。',101.6,False),
         (ROW[:30],128.1,True),('必要書類を提出してください。',148.5,False),
         (ROW[:30],175,True),('書類を確認してください。',195.4,False)]])
    first = propose_page_flow(source,1,font_candidates=[font],line_ids=['p1-l2','p1-l3'])
    small = propose_page_flow(source,2,font_candidates=[font],line_ids=[f'p2-l{i}' for i in range(1,5)])
    full = propose_page_flow(source,2,font_candidates=[font],line_ids=[f'p2-l{i}' for i in range(1,7)])
    base = accept_owned_flow_report(source,propose_owned_flow(source,[first,small],paragraphs=JOINED,
        relationship='A crosses the page seam, followed by independent B.'))['state']
    base['slots'] = dict(reversed(list(base['slots'].items())))
    proposal = propose_owned_flow_extension(source,base,[first,full],
        paragraphs=JOINED+[dict(id='C',parts=[dict(page=2,paragraph_id='P3')])],
        relationship='Explicitly include the next complete application paragraph C.')
    accepted = accept_owned_flow_report(source,proposal)
    assert accepted['receipt']['extension']['retained_paragraphs'] == ['A','B']
    assert accepted['receipt']['reproduction']['lines'] == 8
