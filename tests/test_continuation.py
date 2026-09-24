from copy import deepcopy
import json

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.continuation import confirm_continuation_destination, slot_id
from pdfeditor.document_flow import _reseal
from pdfeditor.editable import write_editable, open_editable
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow, plan_shared_flow
from pdfeditor.story_flow import confirm_story
from pdfeditor.style_confirmation import observed_values
from test_attributed import source_pdf


def prepared(tmp_path, *, mode='left', spacing=False, confirm=True, decorate=None, follower=False, same_page=False):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf '+(b'1 Tc 2 Ts ' if spacing else b'')+b'20 200 Td (ABCD) Tj ET')
    if decorate:decorate(source)
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(4)),explicit_width=150))
    if spacing:
        out,side=tmp_path/'confirmed.pdf',tmp_path/'confirmed.json'
        write_editable(source,out,side,p,[],fonts={'s0':dict(path=str(font))},
            paragraph_style={'s0':dict(tracking=1,baseline_shift=-2)},min_line_height=22,max_bottom=78)
        source=out;p=open_editable(out,side)['state']['paragraph']
    alignment=dict(alignment=mode,**({'justify_policy':'word'} if mode=='justify' else {}))
    stories={}
    def story(pid,p,page,bounds,baseline):
        return confirm_story(source,{'original':dict(page=page,bounds=bounds,paragraph=p,paint_relations=[],
            layout=dict(x=20,baseline=baseline,width=150,max_bottom=bounds[3],min_line_height=22,first_line_indent=0))},
            paragraph_id=pid,chain=['original'],protected_regions={},paragraph_layout=alignment,
            styles={'body':dict(provider=dict(path=str(font)),provider_relation='substituted')},
            style_assignments={'original':{'s0':'body'}},typing_style_id='body')
    stories['A']=story('A',p,1,[18,40,173,78],60)
    regions={'R1':dict(page=1,bounds=[18,40,173,78],x=20,width=150,first_baseline=60),
             'R2':dict(page=1 if same_page else 2,bounds=[18,90,173,220],x=20,width=150,first_baseline=110)}
    mapping={'A':{'original':'R1'}}
    if follower:
        p=inspect_paragraph(source,make_selection(source,2,glyph_ids=list(range(10)),explicit_width=150))
        stories['B']=story('B',p,2,[18,40,173,78],60)
        regions['R3']=dict(page=2,bounds=[18,40,173,78],x=20,width=150,first_baseline=60)
        mapping['B']={'original':'R3'}
    destinations={}
    if confirm:
        destinations['empty']=confirm_continuation_destination(source,destination_id='empty',paragraph_id='A',region_id='R2',
            page=regions['R2']['page'],bounds=regions['R2']['bounds'],insertion='before-page-program',graphics_state='isolated-pdf-initial-state')
    policies={pid:dict(min_line_height=22,first_line_indent=5,keep_together=False,
        break_before='next-region' if pid=='B' else 'auto',break_after='auto',empty=dict(kind='reserve-line',ascent=10,descent=3)) for pid in stories}
    return source,confirm_shared_flow(source,stories,flow_id='flow',paragraph_order=list(stories),regions=regions,
        region_order=list(regions),slot_regions=mapping,paragraph_policies=policies,
        follows=[dict(before='A',after='B',minimum_baseline_gap=24,region_start='reset-to-region-baseline')] if follower else [],
        protected_regions={},continuation_destinations=destinations)


LONG='One two three four five six seven eight nine ten eleven twelve thirteen fourteen.'


def change(state,text):
    return {'A':dict(edits=[dict(start=0,end=len(state['paragraphs']['A']['logical']['text']),text=text,style_id='body')])}


def saved(tmp_path,source,state,changes,name):
    out,side=tmp_path/(name+'.pdf'),tmp_path/(name+'.json')
    preview=plan_shared_flow(source,state,changes)
    report=edit_shared_flow(source,state,out,side,changes)
    assert preview==report['plan']
    opened=open_shared_flow(out,side);assert opened['status']=='restored',opened
    assert opened['state']['contract_sha256']==state['contract_sha256']
    assert report['saves']==1
    return out,opened['state'],report


@pytest.mark.parametrize('mode',['left','right','center','justify'])
def test_generated_lifecycle_alignment_inline_and_noop(tmp_path,mode):
    source,state=prepared(tmp_path,mode=mode,spacing=True)
    source,state,_=saved(tmp_path,source,state,change(state,'AB'),'fits')
    assert len(state['slots'])==1
    ident=slot_id(state['continuation_destinations']['empty'])
    previous_report=None
    for name,text in [('grow',LONG),('second',LONG.replace('twelve','TWELVE')),('shorten','AB'),('regrow',LONG),('noop',None)]:
        out,updated,report=saved(tmp_path,source,state,{} if text is None else change(state,text),name)
        assert ident in updated['slots'] and len(updated['slots'])==2
        generated=updated['slots'][ident]
        assert generated['paragraph_id']=='A' and generated['destination_id']=='empty'
        assert generated['binding']['layout']['first_line_indent']==0
        assert updated['paragraphs']['A']['logical']['text']==(text or LONG)
        assert not updated['paragraphs']['A']['logical']['boundaries']
        if name=='shorten':
            assert generated['occupancy'] is None and not generated['binding']['paragraph']['text']
            assert generated['binding']['paragraph']['style_slot_bindings']
        else:
            assert generated['binding']['paragraph']['text']
            assert generated['binding']['logical_element']['alignment']['value']==mode
        for slot in updated['slots'].values():
            for style in slot['binding']['paragraph']['styles']:
                if style['id'] in slot['style_binding']['styles']:
                    assert style['tracking']==1 and style['baseline_shift']==-2
        if text is None:
            with pymupdf.open(source) as a,pymupdf.open(out) as b:
                assert all(a[i].get_pixmap(dpi=144).samples==b[i].get_pixmap(dpi=144).samples for i in range(len(a)))
            for sid in state['slots']:
                assert state['slots'][sid]['range']==updated['slots'][sid]['range']
                assert state['slots'][sid]['occupancy']==updated['slots'][sid]['occupancy']
            for old,new in zip(previous_report['steps'],report['steps']):
                fields=('unicode','glyph_id','origin','size','advance','code','cid','nominal_pdf_width')
                assert [{k:g[k] for k in fields} for g in old['report']['glyph_plan']]==[
                    {k:g[k] for k in fields} for g in new['report']['glyph_plan']]
        previous_report=report
        source,state=out,updated


def test_does_not_borrow_following_paragraph(tmp_path):
    source,state=prepared(tmp_path,follower=True)
    before=deepcopy(state['paragraphs']['B']['logical'])
    _,updated,report=saved(tmp_path,source,state,change(state,LONG),'own')
    assert updated['paragraphs']['B']['logical']==before
    assert updated['slots']['slot-1']['paragraph_id']=='B'
    assert report['plan']['new_slots']


def test_same_page_creation_and_tampered_generated_binding(tmp_path):
    source,state=prepared(tmp_path,same_page=True)
    out,updated,_=saved(tmp_path,source,state,change(state,LONG),'same-page')
    ident=slot_id(updated['continuation_destinations']['empty'])
    for field in ('paragraph_id','region_id','destination_id','page','creation_provenance'):
        bad=deepcopy(updated);bad['slots'][ident][field]='wrong'
        assert open_shared_flow(out,_reseal(bad))['status']=='needs_confirmation'
    bad=deepcopy(updated);bad['destination_bindings']['empty']['end']+=1
    assert open_shared_flow(out,_reseal(bad))['status']=='needs_confirmation'


@pytest.mark.parametrize('insertion,graphics',[('page-tail','isolated-pdf-initial-state'),('before-page-program','unknown')])
def test_unknown_insertion_authority_is_not_geometry_permission(tmp_path,insertion,graphics):
    source=source_pdf(tmp_path)
    with pytest.raises(PdfError,match='explicit'):
        confirm_continuation_destination(source,destination_id='d',paragraph_id='A',region_id='R',page=2,
            bounds=[18,90,173,220],insertion=insertion,graphics_state=graphics)


def test_dormant_style_witness_restores_later_unselected_text_cursor(tmp_path):
    from pdfeditor.destination_style import inline_properties
    from pdfeditor.replay import glyph_observations, compare_glyphs
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ABCD) Tj (KEEP) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(4)),explicit_width=25))
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    out,side=tmp_path/'empty.pdf',tmp_path/'empty.json'
    write_editable(source,out,side,p,[dict(start=0,end=4,runs=[])],
        fonts={'logical:body':dict(path=str(font))},render_styles={'logical:body':inline_properties(p['styles'][0])},
        empty_style_id='logical:body',_allow_empty_style_witnesses=True,min_line_height=22,max_bottom=78)
    assert open_editable(out,side)['status']=='restored'
    with pymupdf.open(source) as a,pymupdf.open(out) as b:
        assert compare_glyphs(glyph_observations(a[0])[4:],glyph_observations(b[0]))['passed']


def test_paint_envelope_outside_trace_box_and_partial_ownership_are_protected(tmp_path):
    from pdfeditor.content_stream import ContentPage
    from pdfeditor.continuation import require_empty
    source=source_pdf(tmp_path);content=ContentPage(source,2)
    try:
        paint=content.page.get_bboxlog()[0][1]
        trace=content.page.get_texttrace()[0]['bbox']
        assert paint[0]<trace[0]
        bounds=[paint[0]+.01,54,trace[0]-.01,60]
        with pytest.raises(PdfError,match='fixed paint'):require_empty(content,bounds)
        with pytest.raises(PdfError,match='fixed paint'):require_empty(content,bounds,owned={0})
        require_empty(content,bounds,owned=set(range(len(content.actual))))
    finally:content.close()


@pytest.mark.parametrize('kind',['unconfirmed','capacity','state','geometry','owner','protected','witness'])
def test_refusals_are_atomic(tmp_path,kind):
    source,state=prepared(tmp_path,confirm=kind!='unconfirmed');edits=change(state,LONG)
    if kind=='capacity':edits=change(state,LONG*20)
    if kind=='state':state['continuation_destinations']['empty']['authority']['graphics_state']['opacity']=.5
    if kind=='geometry':state['continuation_destinations']['empty']['bounds'][0]=0
    if kind=='owner':state['continuation_destinations']['empty']['paragraph_id']='B'
    if kind=='protected':state['pages']['2']['protected_regions']=[dict(role='fixed',bounds=[20,100,50,130],provenance='explicitly_confirmed')]
    if kind=='witness':state['destination_bindings']['empty']['program_sha256']='0'*64
    # Recompute the contract to exercise the PDF/context guard, not only the checksum.
    from pdfeditor.shared_flow import _contract
    state['contract_sha256']=_contract(state);state=_reseal(state)
    with pytest.raises(PdfError):edit_shared_flow(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',edits)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


@pytest.mark.parametrize('kind',['text','image','path','annotation','group','clip_unknown'])
def test_destination_requires_empty_known_context(tmp_path,kind):
    def decorate(source):
        if kind=='group':
            writer=PdfWriter(clone_from=PdfReader(source))
            writer.pages[1][NameObject('/Group')]=DictionaryObject({NameObject('/S'):NameObject('/Transparency')})
            writer.write(source);return
        with pymupdf.open(source) as doc:
            box=pymupdf.Rect(20,105,150,130)
            if kind=='text':doc[1].insert_text((20,120),'FIXED')
            elif kind=='path':doc[1].draw_line((20,110),(150,110))
            elif kind=='annotation':doc[1].add_rect_annot(box)
            elif kind=='image':
                pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,2,2));pix.clear_with(100)
                doc[1].insert_image(box,stream=pix.tobytes('png'))
            elif kind=='clip_unknown':
                stream=doc[1].get_contents()[0];doc.update_stream(stream,b'Q '+doc.xref_stream(stream))
            doc.saveIncr()
    with pytest.raises(PdfError):prepared(tmp_path,decorate=decorate)


@pytest.mark.parametrize('phase',['commit','bind','publish'])
def test_late_failure_publishes_neither_artifact(tmp_path,monkeypatch,phase):
    import pdfeditor.shared_flow as shared
    source,state=prepared(tmp_path)
    def fail(*args,**kwargs):raise PdfError('injected late failure')
    if phase=='commit':monkeypatch.setattr(shared.Transaction,'_verify',fail)
    elif phase=='bind':monkeypatch.setattr(shared,'bind_document_edit',fail)
    else:
        import pdfeditor.editable as editable
        real=editable.os.link;calls=[]
        def link(a,b):
            calls.append(1)
            if len(calls)==2:fail()
            return real(a,b)
        monkeypatch.setattr(editable.os,'link',link)
    with pytest.raises(PdfError,match='injected'):edit_shared_flow(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',change(state,LONG))
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
