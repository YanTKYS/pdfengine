"""Retained current-slot glyphs and confirmed continuation ownership meet at allocation."""
from copy import deepcopy
import pymupdf
import pytest
from pdfeditor.backend import PdfError
from pdfeditor.continuation import confirm_continuation_destination, slot_id
from pdfeditor.continuation_review import review_continuation_geometry, build_continuation_boundary_confirmation_request
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, replace_in_flow
from pdfeditor.shared_flow import confirm_shared_flow_continuations, edit_shared_flow, open_shared_flow, plan_shared_flow
from pdfeditor.selection import source_sha
from test_source_adjacency_flow import body, BODY

REGION = dict(page=2,bounds=[80,80,530,380],x=85,width=440,first_baseline=100)


@pytest.fixture
def connected(body,tmp_path):
    original,proposal=body
    source=tmp_path/'source.pdf'
    with pymupdf.open(original) as doc:
        page=doc.new_page(width=595,height=842)
        page.insert_text((85,780),'FIXED FOOTER')
        doc.save(source)
    fonts=[c['path'] for style in proposal['styles'] for c in style['providers']['verified']]
    proposal=propose_page_flow(source,font_candidates=fonts)
    accepted=accept_page_flow(source,proposal,paragraph_ids=['P1'],overrides={'region_bottom':260})
    review=review_continuation_geometry(source,2,REGION['bounds'])
    # Caller selects the end of the existing program, after its fixed footer.
    candidates=[c for g in review['groups'] for c in g['candidates']]
    selected=max(candidates,key=lambda c:c['ordinal'])
    request=build_continuation_boundary_confirmation_request(review,boundary_id=selected['boundary_id'],
        destination_id='tail',paragraph_id='P1',region_id='R2')
    destination=confirm_continuation_destination(source,**request['confirm_kwargs'])
    state=confirm_shared_flow_continuations(source,accepted,regions={'R2':REGION},region_order=['R2'],
        continuation_destinations={'tail':destination})
    return source,accepted,state,destination


def test_grow_reedit_shorten_regrow_noop(connected,tmp_path):
    original,accepted,state,destination=connected;sha=source_sha(original)
    assert plan_shared_flow(original,state,{})['new_slots']=={}
    source=original; sid=slot_id(destination)
    extension='申請書を提出してください。'*5
    changes=[replace_in_flow(state,'申請書','申請書'+extension,paragraph_id='P1')]
    for i in range(5):
        if i==1:
            # A local edit wholly in the saved generated slot, not a whole-text replacement.
            start=state['slots'][sid]['range'][0]+1
            change={'P1':{'edits':[dict(start=start,end=start+1,text='申請')]}}
        elif i==2:
            change={'P1':{'edits':[dict(start=3,end=len(state['paragraphs']['P1']['logical']['text'])-len(BODY)+3,text='')]}}
        elif i==3:
            change=replace_in_flow(state,'申請書','申請書'+extension,paragraph_id='P1')
        elif i==4:change={}
        else:change=changes[0]
        pdf,model=tmp_path/f'{i}.pdf',tmp_path/f'{i}.json'
        report=edit_shared_flow(source,state,pdf,model,change)
        opened=open_shared_flow(pdf,model);assert opened['status']=='restored',opened.get('reason')
        updated=opened['state'];assert sid in updated['slots']
        if i==2:
            assert updated['slots'][sid]['occupancy'] is None
            with pymupdf.open(original) as a,pymupdf.open(pdf) as b:
                assert a[1].get_pixmap().samples==b[1].get_pixmap().samples
        else:
            assert updated['slots'][sid]['occupancy'] is not None
        if i==1:
            step=next(s['report'] for s in report['steps'] if s['slot_id']==sid)
            assert step['retained_glyph_count']>0
        if i==4:
            with pymupdf.open(source) as a,pymupdf.open(pdf) as b:
                assert [p.get_pixmap().samples for p in a]==[p.get_pixmap().samples for p in b]
        source,state=pdf,updated
    assert source_sha(original)==sha


def test_unapproved_overflow_capacity_and_stale_binding_are_atomic(connected,tmp_path):
    source,accepted,state,destination=connected
    growing=replace_in_flow(state,'申請書','申請書'+'申請書を提出してください。'*5)
    small=dict(REGION,bounds=[80,80,530,105])
    d=confirm_continuation_destination(source,destination_id='small',paragraph_id='P1',region_id='R2',page=2,
        bounds=small['bounds'],insertion='before-page-program',graphics_state='isolated-pdf-initial-state')
    limited=confirm_shared_flow_continuations(source,accepted,regions={'R2':small},region_order=['R2'],continuation_destinations={'small':d})
    for model,change,reason in [(accepted,growing,'exceed all explicitly confirmed shared regions'),
            (limited,replace_in_flow(limited,'申請書','申請書'*60),'exceed all explicitly confirmed shared regions')]:
        pdf,sidecar=tmp_path/'refused.pdf',tmp_path/'refused.json'
        with pytest.raises(PdfError,match=reason):edit_shared_flow(source,model,pdf,sidecar,change)
        assert not pdf.exists() and not sidecar.exists()
    pdf,sidecar=tmp_path/'grown.pdf',tmp_path/'grown.json'
    edit_shared_flow(source,state,pdf,sidecar,growing)
    assert open_shared_flow(pdf,state)['status']=='needs_confirmation'
    saved=open_shared_flow(pdf,sidecar)['state']
    from pdfeditor.attributed import digest
    bad=deepcopy(saved)
    bad['destination_bindings']['tail']['block_sha256']='0'*64
    bad.pop('model_sha256');bad['model_sha256']=digest(bad)
    assert open_shared_flow(pdf,bad)['status']=='needs_confirmation'
    # A same-source receipt is still required when binding new destination authority.
    with pytest.raises(PdfError):
        confirm_shared_flow_continuations(pdf,accepted,regions={'R2':REGION},region_order=['R2'],
            continuation_destinations={'tail':destination})


def test_destination_geometry_and_clip_are_not_promoted_to_authority(connected,tmp_path):
    source,accepted,_,destination=connected
    with pytest.raises(PdfError,match='confirmed destination authority'):
        confirm_shared_flow_continuations(source,accepted,regions={'R2':REGION},region_order=['R2'],continuation_destinations={})
    wrong=deepcopy(REGION);wrong['bounds'][3]+=10
    with pytest.raises(PdfError):
        confirm_shared_flow_continuations(source,accepted,regions={'R2':wrong},region_order=['R2'],
            continuation_destinations={'tail':destination})
    clipped=tmp_path/'clipped.pdf'
    with pymupdf.open(source) as doc:
        stream=doc[1].get_contents()[0]
        doc.update_stream(stream,b'q 0 0 40 40 re W n\n'+doc.xref_stream(stream)+b'\nQ')
        doc.save(clipped)
    review=review_continuation_geometry(clipped,2,REGION['bounds'])
    rejected=[c for g in review['groups'] for c in g['candidates']
              if c['geometry']['clip_check_required'] and not c['geometry']['bounds_inside_inherited_clip']]
    assert rejected
    with pytest.raises(ValueError):
        build_continuation_boundary_confirmation_request(review,boundary_id=rejected[0]['boundary_id'],
            destination_id='tail',paragraph_id='P1',region_id='R2')


def test_tagged_source_cannot_escape_its_structure_owner(tmp_path):
    from pypdf import PdfReader,PdfWriter
    import page_flow_fixture as fx
    font=fx.write_fonts(tmp_path/'fonts')['full']
    first=fx.make_page(tmp_path/'first.pdf',font.read_bytes(),tree_backed=True)
    source=tmp_path/'tagged.pdf'
    writer=PdfWriter(clone_from=PdfReader(first));writer.add_blank_page(width=595,height=842);writer.write(source)
    p=propose_page_flow(source,font_candidates=[font],include_embedded_fonts=True,font_cache=tmp_path/'cache',new_text={'style-1':'再申請書'})
    state=accept_page_flow(source,p,provider_choices={'style-1':dict(sha256=source_sha(font),font_index=0,relation='substituted')})
    destination=confirm_continuation_destination(source,destination_id='tail',paragraph_id='P1',region_id='R2',
        page=2,bounds=REGION['bounds'],insertion='before-page-program',graphics_state='isolated-pdf-initial-state')
    with pytest.raises(PdfError,match='tagged source adjacency'):
        confirm_shared_flow_continuations(source,state,regions={'R2':REGION},region_order=['R2'],continuation_destinations={'tail':destination})
