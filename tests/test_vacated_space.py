"""Conditional paint authority needs current owners AND their transaction removal."""
from copy import deepcopy

import pytest
import pymupdf

from pdfeditor.backend import PdfError
from pdfeditor.continuation import confirm_continuation_destination, slot_id
from pdfeditor.owned_flow_proposal import propose_owned_flow, accept_owned_flow_report
from pdfeditor.page_proposal import propose_page_flow, replace_in_flow
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow, plan_shared_flow
from pdfeditor.selection import source_sha
from test_cross_page_owned_flow import make_source, ROW
from test_page_break_reflow import GROUPS, BREAK


def setup_case(root, change=None):
    source,font=make_source(root, rows=[
        [(ROW[:30],270,True),('申請期限を確認。',291.6,False)],
        [(ROW[:30],80,True),('必要書類を提出してください。',100.4,False)]])
    pages=[propose_page_flow(source,n,font_candidates=[font],line_ids=[f'p{n}-l{i}' for i in ids])
        for n,ids in [(1,(2,3)),(2,(1,2))]]
    region=dict(page=2,bounds=[40,50,205,116],x=40,width=165,first_baseline=62)
    confirmation=dict(destination_id='release-B',paragraph_id='A',region_id='vacated-2',page=2,
        bounds=region['bounds'],insertion='before-page-program',graphics_state='isolated-pdf-initial-state')
    request=dict(region_id='vacated-2',before_region='page-2',region=region,
        release_paragraphs=['B'],confirmation=confirmation)
    if change:change(request)
    proposal=propose_owned_flow(source,pages,paragraphs=GROUPS,
        relationship='A and B are explicitly selected consecutive independent body paragraphs.',page_breaks=[BREAK],
        vacated_continuations=[request])
    accepted=accept_owned_flow_report(source,proposal)
    return source,accepted['state'],confirmation


def test_vacated_space_lifecycle(tmp_path):
    original,state,confirmation=setup_case(tmp_path)
    initial=deepcopy(state);sha=source_sha(original);source=original
    dest=state['continuation_destinations']['release-B'];sid=slot_id(dest)
    before=plan_shared_flow(source,state,{})
    assert before['fragments']['slot-1']['lines'][0]['baseline']==80
    noop=tmp_path/'original-noop.pdf';side=tmp_path/'original-noop.json'
    edit_shared_flow(source,state,noop,side,{})
    with pymupdf.open(source) as a,pymupdf.open(noop) as b:
        assert [p.get_pixmap().samples for p in a]==[p.get_pixmap().samples for p in b]
    longer='期限'+ROW*2
    revisions=[{'A':('期限',longer)}, {'B':('必要','各種必要')},
        {'A':(longer,'期限'),'B':('各種必要','必要')},{'A':('期限',longer)},{}]
    for index,edits in enumerate(revisions):
        changes={}
        for pid,edit in edits.items():
            changes.update(replace_in_flow(state,*edit,paragraph_id=pid,
                style_id='page-1:style-1' if pid=='A' else 'page-2:style-1'))
        pdf,model=tmp_path/f'{index}.pdf',tmp_path/f'{index}.json'
        report=edit_shared_flow(source,state,pdf,model,changes)
        opened=open_shared_flow(pdf,model);assert opened['status']=='restored',opened
        state=opened['state'];fragments=report['plan']['fragments']
        if index==2:
            assert not fragments[sid]['lines'] and fragments['slot-1']['lines'][0]['baseline']==80
        else:
            assert len(fragments[sid]['lines'])>=2
            assert fragments['slot-1']['lines'][0]['baseline']>100
            step=next(s for s in report['steps'] if s['slot_id']==sid)
            assert step['report']['vacated_space']['owners'][0]['source_owner']=='slot-1'
        source=pdf
    assert source_sha(original)==sha
    # Independent empty-destination confirmation still cannot authorize B's text.
    with pytest.raises(PdfError):confirm_continuation_destination(original,**confirmation)


def test_missing_release_plan_rolls_back(tmp_path,monkeypatch):
    source,state,_=setup_case(tmp_path)
    from pdfeditor.transaction import Transaction
    original=Transaction.commit
    def missing(self,*args,**kwargs):
        page=self.pages[2]
        page.plans=[p for p in page.plans if p.owner!='slot-1']
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Transaction,'commit',missing)
    pdf,model=tmp_path/'bad.pdf',tmp_path/'bad.json'
    with pytest.raises(PdfError,match='same Transaction'):
        edit_shared_flow(source,state,pdf,model,replace_in_flow(state,'期限','期限'+ROW*2,paragraph_id='A',style_id='page-1:style-1'))
    assert not pdf.exists() and not model.exists()


def test_capacity_and_stale_ownership_refuse(tmp_path):
    source,state,_=setup_case(tmp_path)
    with pytest.raises(PdfError):
        edit_shared_flow(source,state,tmp_path/'overflow.pdf',tmp_path/'overflow.json',
            replace_in_flow(state,'期限',ROW*5,paragraph_id='A',style_id='page-1:style-1'))
    assert not (tmp_path/'overflow.pdf').exists()
    bad=deepcopy(state)
    bad['slots']['slot-1']['binding']['paragraph']['selection']['glyph_ids']=[0]
    assert open_shared_flow(source,bad)['status']=='needs_confirmation'


@pytest.mark.parametrize('kind', ['wrong-owner', 'foreign-content', 'invalid-boundary'])
def test_unowned_or_unverified_capacity_refuses(tmp_path, kind):
    def change(request):
        if kind == 'wrong-owner':request['release_paragraphs'] = ['A']
        elif kind == 'foreign-content':request['region']['bounds'][3] = 250
        else:
            request['confirmation'].update(insertion='confirmed-page-program-boundary',
                graphics_state='confirmed-boundary-state',boundary='boundary-'+'0'*24)
    with pytest.raises(PdfError):setup_case(tmp_path,change)


def test_release_witness_cannot_be_reused_for_another_revision(tmp_path):
    from pdfeditor.vacated_space import evidence, glyphs
    from pdfeditor.content_stream import ContentPage
    source,state,_=setup_case(tmp_path)
    dest=state['continuation_destinations']['release-B'];proof=evidence(state,dest)
    proof['pdf_sha256']='0'*64
    content=ContentPage(source,2)
    try:
        with pytest.raises(PdfError,match='stale'):glyphs(source,content,dest,proof)
    finally:content.close()


def test_final_collision_rolls_back(tmp_path,monkeypatch):
    source,state,_=setup_case(tmp_path)
    from pdfeditor.transaction import Transaction
    original=Transaction.commit
    def collide(self,*args,**kwargs):
        page=self.pages[2]
        a=next(p for p in page.plans if getattr(p,'vacated_space',None))
        b=next(p for p in page.plans if p.owner=='slot-1')
        b.final_rects=list(a.final_rects)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Transaction,'commit',collide)
    pdf,model=tmp_path/'collision.pdf',tmp_path/'collision.json'
    with pytest.raises(PdfError,match='final placements overlap'):
        edit_shared_flow(source,state,pdf,model,replace_in_flow(state,'期限','期限'+ROW*2,paragraph_id='A',style_id='page-1:style-1'))
    assert not pdf.exists() and not model.exists()
