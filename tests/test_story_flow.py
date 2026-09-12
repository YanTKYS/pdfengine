from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader,PdfWriter

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.document_flow import _reseal
from pdfeditor.selection import make_selection,source_sha
from pdfeditor.story_flow import confirm_story,edit_story,open_story,plan_story
from test_attributed import source_pdf


def prepared(tmp_path, *, same_page=False, decorate=None):
    program=b'BT /Regular 12 Tf 20 200 Td (FIRST TEXT) Tj 0 -180 Td (FIXED FOOTER) Tj ET '
    if same_page:program+=b'BT /Regular 12 Tf 180 200 Td (SECOND) Tj ET '
    source=source_pdf(tmp_path,program)
    reader=PdfReader(source);writer=PdfWriter(clone_from=reader);writer.add_page(reader.pages[1]);writer.write(source)
    if decorate:decorate(source)
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    specs={}
    for ident,page,ids,x,bottom in [('A',1,list(range(10)),20,94),
            ('B',1 if same_page else 2,list(range(22,28)) if same_page else list(range(10)),180 if same_page else 20,214)]:
        p=inspect_paragraph(source,make_selection(source,page,glyph_ids=ids,explicit_width=110))
        specs[ident]=dict(page=page,bounds=[x-2,43,x+112,bottom],paragraph=p,
            layout=dict(x=x,baseline=60,width=110,max_bottom=bottom,min_line_height=20,first_line_indent=0),paint_relations=[])
    state=confirm_story(source,specs,paragraph_id='one-paragraph',chain=['A','B'],font={'path':str(font)},
        protected_regions={'1':[dict(role='footer',bounds=[10,226,310,253])]})
    return source,state


LONG='A single logical paragraph contains enough words to continue through the next confirmed container without adding an authored break.'


def replace(state,text):return [dict(start=0,end=len(state['logical']['text']),text=text)]


def saved(tmp_path,source,state,edits,name):
    out,sidecar=tmp_path/f'{name}.pdf',tmp_path/f'{name}.json'
    plan=plan_story(source,state,edits)
    report=edit_story(source,state,out,sidecar,edits)
    assert report['plan']==plan
    reopened=open_story(out,sidecar)
    assert reopened['status']=='restored',reopened
    after=reopened['state']
    assert after['logical']['id']==state['logical']['id']
    assert after['flow_chain']==state['flow_chain'] and after['containers']==state['containers'] and after['pages']==state['pages']
    assert all(f['binding']['logical_element']['id']=='one-paragraph' for f in after['fragments'].values())
    return out,after,report


def test_one_story_crosses_pages_returns_empties_retypes_and_noops(tmp_path):
    source,state=prepared(tmp_path);sha=source_sha(source)
    out,state,_=saved(tmp_path,source,state,replace(state,LONG),'grow')
    assert state['logical']['text']==LONG and state['logical']['boundaries']==[]
    assert state['physical_breaks'][0]['kind']=='generated_page_break'
    cut=state['fragments']['A']['range'][1]
    # One Unicode edit straddles the physical page boundary.
    out,state,_=saved(tmp_path,out,state,[dict(start=cut-2,end=cut+2,text='FLOW')],'cross')
    assert state['logical']['text']==LONG[:cut-2]+'FLOW'+LONG[cut+2:]
    out,state,_=saved(tmp_path,out,state,replace(state,'Short text.'),'short')
    assert state['fragments']['B']['binding']['paragraph']['text']=='' and state['physical_breaks']==[]
    out,state,_=saved(tmp_path,out,state,replace(state,''),'empty')
    assert all(not f['binding']['paragraph']['selection']['glyph_ids'] for f in state['fragments'].values())
    out,state,_=saved(tmp_path,out,state,replace(state,LONG),'retype')
    copied,state,_=saved(tmp_path,out,state,[],'noop')
    with pymupdf.open(source) as original,pymupdf.open(out) as before,pymupdf.open(copied) as after:
        assert all(before[i].get_pixmap().samples==after[i].get_pixmap().samples for i in range(len(before)))
        assert original[2].get_pixmap().samples==after[2].get_pixmap().samples
    assert source_sha(source)==sha


def test_explicit_columns_do_not_create_paragraphs(tmp_path):
    source,state=prepared(tmp_path,same_page=True)
    out,state,_=saved(tmp_path,source,state,replace(state,LONG),'columns')
    assert state['physical_breaks'][0]['kind']=='generated_container_break'
    out,state,_=saved(tmp_path,out,state,replace(state,'Tiny'),'back')
    out,state,_=saved(tmp_path,out,state,replace(state,LONG),'again')
    assert state['logical']['text']==LONG


def test_authored_break_at_container_cut_is_not_duplicated(tmp_path):
    source,state=prepared(tmp_path)
    text='One line.\nSecond line.\nThird line.'
    out,state,_=saved(tmp_path,source,state,replace(state,text),'breaks')
    assert state['logical']['text']==text and len(state['logical']['boundaries'])==2
    assert state['fragments']['A']['render_end']<state['fragments']['A']['range'][1]
    out,state,_=saved(tmp_path,out,state,[],'same')
    assert state['logical']['text']==text and len(state['logical']['boundaries'])==2


@pytest.mark.parametrize('mode',['overflow','stale','font_changed','duplicate_chain','missing_flow','wrong_identity',
    'changed_range','header','overlap','style','unknown_width','authored_split','decoration'])
def test_story_refusals_leave_no_outputs(tmp_path,mode):
    source,state=prepared(tmp_path);edits=replace(state,LONG)
    if mode=='overflow':edits=replace(state,LONG*6)
    if mode=='stale':state['pdf_sha256']='0'*64
    if mode=='font_changed':state['font_recipe']['sha256']='0'*64
    if mode=='duplicate_chain':state['flow_chain']['containers']=['A','A']
    if mode=='missing_flow':state['flow_chain']['provenance']='inferred_from_coordinates'
    if mode=='wrong_identity':
        b=state['fragments']['B']['binding'];b['logical_element']['id']='other';state['fragments']['B']['binding']=_reseal(b)
    if mode=='changed_range':state['fragments']['A']['range'][1]-=1
    if mode=='header':state['pages']['1']['protected_regions'][0]['bounds']=[10,40,160,120]
    if mode=='overlap':state['containers']['B']['page']=1
    if mode=='style':edits[0]['style_id']='bold'
    if mode=='unknown_width':state['containers']['A']['layout']['width']=None
    if mode=='authored_split':state['logical']['text']+='\n'
    if mode=='decoration':state['logical']['decoration_ranges']=[dict(start=0,end=3)]
    state=_reseal(state)
    with pytest.raises((PdfError,ValueError)):
        edit_story(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',edits)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_late_failure_keeps_original_and_publishes_nothing(tmp_path,monkeypatch):
    import pdfeditor.story_flow as module
    source,state=prepared(tmp_path);sha=source_sha(source);original=module.edit_document;calls=[]
    def fail_second(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise PdfError('injected second fragment failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'edit_document',fail_second)
    with pytest.raises(PdfError,match='second fragment'):
        edit_story(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',replace(state,LONG))
    assert len(calls)==2 and source_sha(source)==sha
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists() and not list(tmp_path.glob('.story-*'))


@pytest.mark.parametrize('kind',['image','annotation','vector','clip'])
def test_continuation_preserves_existing_obstacle_guards(tmp_path,kind):
    def decorate(source):
        if kind=='clip':
            writer=PdfWriter(clone_from=PdfReader(source));stream=writer.pages[1]['/Contents'].get_object()
            stream.set_data(b'q 0 175 320 85 re W n '+stream.get_data()+b' Q');writer.write(source)
        else:
            with pymupdf.open(source) as doc:
                box=pymupdf.Rect(20,76,125,88)
                if kind=='image':
                    pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,2,2));pix.clear_with(100)
                    doc[1].insert_image(box,stream=pix.tobytes('png'))
                elif kind=='annotation':doc[1].add_rect_annot(box)
                else:doc[1].draw_rect(box,color=None,fill=(.4,.4,.4))
                doc.saveIncr()
    source,state=prepared(tmp_path,decorate=decorate)
    with pytest.raises(PdfError):
        edit_story(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',replace(state,LONG))
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_overlapping_regions_do_not_authorize_compound_mutation(tmp_path):
    source,state=prepared(tmp_path,same_page=True)
    state['containers']['B']['bounds']=[15,43,295,214];state=_reseal(state)
    with pytest.raises(PdfError,match='disjoint'):
        edit_story(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',replace(state,LONG))
    assert not (tmp_path/'bad.pdf').exists()


def test_font_family_difference_is_not_a_homogeneous_style(tmp_path):
    from pdfeditor.story_flow import _style
    source,state=prepared(tmp_path)
    b=deepcopy(state['fragments']['A']['binding']);style=deepcopy(b['paragraph']['styles'][0])
    style['id']='other';style['font_name']='Courier-Bold';b['paragraph']['styles'].append(style)
    with pytest.raises(PdfError,match='homogeneous'):_style(b)
