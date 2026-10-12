"""Adopt followers from a restored revision, preserving source and generated owners."""
from copy import deepcopy
import pymupdf
import pytest
from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import _Analysis, propose_page_flow, replace_in_flow, _seal
from pdfeditor.owned_flow_proposal import propose_owned_flow_extension,accept_owned_flow_report
from pdfeditor.shared_flow import edit_shared_flow,open_shared_flow
from test_owned_flow_extension import setup_case,ROW


def saved_case(root):
    original,base,_ = setup_case(root)
    short = '期限'+ROW
    pdf,side = root/'saved.pdf',root/'saved.json'
    edit_shared_flow(original,base,pdf,side,replace_in_flow(base,'期限',short,paragraph_id='A',style_id='page-1:style-1'))
    opened = open_shared_flow(pdf,side); assert opened['status']=='restored'
    base = opened['state']
    lines = _Analysis(pdf,2).lines
    ids = [l['id'] for l in lines if 120 < l['baseline'] < 160]
    page = propose_page_flow(pdf,2,font_candidates=[root/'source.ttf'],line_ids=ids)
    proposal = propose_owned_flow_extension(pdf,base,[page],
        paragraphs=[dict(id='C',parts=[dict(page=2,paragraph_id='P1')])],
        relationship='The next complete application instruction C follows the currently edited B.',append_to_region='page-2')
    return original,pdf,base,proposal,short


def test_restored_extension_preserves_slots_and_reflows(tmp_path):
    original,source,base,proposal,short = saved_case(tmp_path)
    long = '期限'+ROW*2
    blocked,side = tmp_path/'blocked.pdf',tmp_path/'blocked.json'
    with pytest.raises(PdfError):
        edit_shared_flow(source,base,blocked,side,replace_in_flow(base,short,long,paragraph_id='A',style_id='page-1:style-1'))
    assert not blocked.exists() and not side.exists()
    accepted = accept_owned_flow_report(source,proposal); state=accepted['state']
    assert state['paragraphs']['A'] == base['paragraphs']['A']
    assert state['paragraphs']['B'] == base['paragraphs']['B']
    assert state['destination_bindings']==base['destination_bindings']
    assert state['generated_fonts']==base['generated_fonts']
    for sid,slot in base['slots'].items():
        assert state['slots'][sid].get('source_output')==slot.get('source_output')
        assert state['slots'][sid]['binding']['paragraph']==slot['binding']['paragraph']
    for i,(old,new) in enumerate([(None,None),(short,long),(long,short),(short,long)]):
        pdf,side=tmp_path/f'{i}.pdf',tmp_path/f'{i}.json'
        report=edit_shared_flow(source,state,pdf,side,{} if old is None else
            replace_in_flow(state,old,new,paragraph_id='A',style_id='page-1:style-1'))
        opened=open_shared_flow(pdf,side); assert opened['status']=='restored'
        if i==0:
            with pymupdf.open(source) as a,pymupdf.open(pdf) as b:
                assert [p.get_pixmap().samples for p in a]==[p.get_pixmap().samples for p in b]
        if i==1: assert report['plan']['fragments']['slot-2']['lines'][0]['baseline'] > 126.9
        state=opened['state'];source=pdf
    # Pulling C farther upward would enter a conditional destination that only
    # authorizes releasing B. Even after adoption, that authority stays bounded.
    pdf,side=tmp_path/'unauthorized.pdf',tmp_path/'unauthorized.json'
    with pytest.raises(PdfError,match='collides with an unselected glyph'):
        edit_shared_flow(source,state,pdf,side,replace_in_flow(state,long,'期限',
            paragraph_id='A',style_id='page-1:style-1'))
    assert not pdf.exists() and not side.exists()


@pytest.mark.parametrize('kind',['stale','review','duplicate-slot','overlap','unselected','skipped-text','protected','capacity'])
def test_post_edit_extension_refuses_invalid_authority(tmp_path,kind):
    original,source,base,proposal,short=saved_case(tmp_path)
    from pdfeditor.shared_flow import _contract,_reseal
    if kind=='stale':
        with pytest.raises(PdfError):accept_owned_flow_report(original,proposal)
    elif kind=='review':
        proposal['edited_extension']['review']['current_baseline_gap'] += 1
        with pytest.raises(PdfError):accept_owned_flow_report(source,_seal(proposal))
    elif kind=='duplicate-slot':
        sid=next(s for s,v in base['slots'].items() if v.get('destination_id'))
        base['slots']['duplicate']=deepcopy(base['slots'][sid])
        base['contract_sha256']=_contract(base);base=_reseal(base)
        with pytest.raises(PdfError):
            propose_owned_flow_extension(source,base,proposal['pages'],paragraphs=proposal['paragraphs'],
                relationship='Explicit following instruction.',append_to_region='page-2')
    elif kind=='overlap':
        lines=_Analysis(source,2).lines
        owned=set(base['slots']['slot-1']['binding']['paragraph']['selection']['glyph_ids'])
        page=propose_page_flow(source,2,font_candidates=[tmp_path/'source.ttf'],
            line_ids=[l['id'] for l in lines if set(l['glyph_ids'])<=owned])
        with pytest.raises(PdfError,match='overlaps'):
            propose_owned_flow_extension(source,base,[page],paragraphs=proposal['paragraphs'],
                relationship='Cannot adopt B twice.',append_to_region='page-2')
    elif kind=='skipped-text':
        page=propose_page_flow(source,2,font_candidates=[tmp_path/'source.ttf'],
            line_ids=[proposal['pages'][0]['paragraphs'][0]['line_ids'][-1]])
        with pytest.raises(PdfError,match='skips unselected'):
            propose_owned_flow_extension(source,base,[page],paragraphs=proposal['paragraphs'],
                relationship='Attempt to skip the first line of C.',append_to_region='page-2')
    elif kind=='protected':
        ident=proposal['pages'][0]['paragraphs'][0]['line_ids'][0]
        box=next(l['bbox'] for l in _Analysis(source,2).lines if l['id']==ident)
        for region in base['pages']['2']['protected_regions']:
            if region['bounds']==box:region['role']='header'
        base['contract_sha256']=_contract(base);base=_reseal(base)
        proposal=propose_owned_flow_extension(source,base,proposal['pages'],paragraphs=proposal['paragraphs'],
            relationship='Explicit header protection must survive text selection.',append_to_region='page-2')
        with pytest.raises(PdfError,match='protected'):
            accept_owned_flow_report(source,proposal)
    elif kind=='unselected':
        proposal['pages'][0]['paragraphs'][0]['glyph_ids'].append(99999)
        with pytest.raises(PdfError):accept_owned_flow_report(source,_seal(proposal))
    else:
        state=accept_owned_flow_report(source,proposal)['state']
        pdf,side=tmp_path/'overflow.pdf',tmp_path/'overflow.json'
        with pytest.raises(PdfError):
            edit_shared_flow(source,state,pdf,side,replace_in_flow(state,short,'期限'+ROW*4,
                paragraph_id='A',style_id='page-1:style-1'))
        assert not pdf.exists() and not side.exists()


def test_noop_retains_native_tm_precision_beyond_trace_floats(tmp_path):
    from test_source_ownership import custom_flow
    from pdfeditor.content_stream import ContentPage
    source,state=custom_flow(tmp_path,b'BT /Regular 12 Tf 1 0 0 1 84.2 200.2 Tm (A) Tj ET',
        [('A',[0],84.19999694824219,100,[80,40,190,90],59.80000305175781)])
    from pdfeditor.shared_flow import _contract,_reseal,SOURCE_PLACEMENT
    state['paragraph_policies']['A']['text_placement']=SOURCE_PLACEMENT
    state['contract_sha256']=_contract(state);state=_reseal(state)
    pdf,side=tmp_path/'precise.pdf',tmp_path/'precise.json'
    edit_shared_flow(source,state,pdf,side,{})
    assert open_shared_flow(pdf,side)['status']=='restored'
    before,after=ContentPage(source,1),ContentPage(pdf,1)
    try:
        matrices=lambda c:[e.text_matrix for e in c.events if any(ch.text=='A' for ch in e.chars)]
        assert matrices(before)==matrices(after)==[(1.,0.,0.,1.,84.2,200.2)]
    finally:before.close();after.close()


def test_adoption_certificate_cannot_be_missing_or_claim_generated_owner(tmp_path):
    _,source,_,proposal,_=saved_case(tmp_path)
    state=accept_owned_flow_report(source,proposal)['state']
    from pdfeditor.shared_flow import _contract,_reseal
    for kind in ('missing','generated'):
        bad=deepcopy(state)
        if kind=='missing':del bad['source_adoptions']
        else:
            generated=next(s for s,v in bad['slots'].items() if v.get('destination_id'))
            bad['source_adoptions'][generated]=deepcopy(bad['source_adoptions']['slot-2'])
        bad['contract_sha256']=_contract(bad)
        opened=open_shared_flow(source,_reseal(bad))
        assert opened['status']!='restored'
        assert 'source output cannot return' in opened['reason'] or 'invalid current-revision source adoption' in opened['reason']
