from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader,PdfWriter

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.document_flow import _reseal
from pdfeditor.selection import make_selection,source_sha
from pdfeditor.shared_flow import confirm_shared_flow,edit_shared_flow,open_shared_flow,plan_shared_flow
from pdfeditor.story_flow import confirm_story
from test_attributed import source_pdf


def prepared(tmp_path, *, keep=False, break_before='auto', decorate=None):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (First ) Tj /Bold 14 Tf 0 0 1 rg (BLUE) Tj ET '
        b'BT /Regular 11 Tf 0 .5 0 rg 20 144 Td (Second ) Tj ET '
        b'BT /Regular 12 Tf 20 20 Td (FIXED FOOTER) Tj ET')
    reader=PdfReader(source);writer=PdfWriter(clone_from=reader);writer.add_page(reader.pages[1])
    writer.pages[1]['/Contents'].get_object().set_data(b'BT /Bold 13 Tf 1 0 0 rg 20 200 Td (tail) Tj ET')
    writer.write(source)
    if decorate:decorate(source)
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    stories={}
    for pid,parts in {'A':[('a1',1,list(range(10)),60,95)],
                      'B':[('b1',1,list(range(10,17)),116,145),('b2',2,list(range(4)),60,215)]}.items():
        specs={};mapping={}
        for ident,page,ids,baseline,bottom in parts:
            p=inspect_paragraph(source,make_selection(source,page,glyph_ids=ids,explicit_width=150))
            specs[ident]=dict(page=page,bounds=[18,baseline-17,173,bottom],paragraph=p,paint_relations=[],
                layout=dict(x=20,baseline=baseline,width=150,max_bottom=bottom,min_line_height=22,first_line_indent=0))
            mapping[ident]={'s0':'accent'} if ident=='b2' else {'s0':'body','s1':'accent'} if pid=='A' else {'s0':'body'}
        stories[pid]=confirm_story(source,specs,paragraph_id=pid,chain=list(specs),protected_regions={},
            styles={i:dict(provider=dict(path=str(font)),provider_relation='substituted') for i in ('body','accent')},
            style_assignments=mapping,typing_style_id='body')
    policies={i:dict(min_line_height=22,first_line_indent=0,keep_together=False,break_before='auto',break_after='auto',
        empty=dict(kind='reserve-line',ascent=10,descent=3)) for i in stories}
    policies['B'].update(keep_together=keep,break_before=break_before)
    state=confirm_shared_flow(source,stories,flow_id='main-body',paragraph_order=['A','B'],
        regions={'R1':dict(page=1,bounds=[18,43,173,145],x=20,width=150,first_baseline=60),
                 'R2':dict(page=2,bounds=[18,43,173,215],x=20,width=150,first_baseline=60)},region_order=['R1','R2'],
        slot_regions={'A':{'a1':'R1'},'B':{'b1':'R1','b2':'R2'}},paragraph_policies=policies,
        follows=[dict(before='A',after='B',minimum_baseline_gap=24,region_start='reset-to-region-baseline')],
        protected_regions={'1':[dict(role='footer',bounds=[10,226,310,253])]})
    return source,state


SHORT=[dict(text='Hi ',style_id='body'),dict(text='B',style_id='accent')]
LONG=[dict(text='Line one\nLine two\nLine three\n',style_id='body'),dict(text='End',style_id='accent')]


def replace(state,pid,runs):
    return dict(edits=[dict(start=0,end=len(state['paragraphs'][pid]['logical']['text']),runs=runs)])


def saved(tmp_path,source,state,changes,name):
    out,model=tmp_path/f'{name}.pdf',tmp_path/f'{name}.json'
    plan=plan_shared_flow(source,state,changes)
    report=edit_shared_flow(source,state,out,model,changes)
    assert report['plan']==plan
    opened=open_shared_flow(out,model);assert opened['status']=='restored',opened
    updated=opened['state']
    for key in ('flow','regions','pages','paragraph_policies','follows','paragraph_boundaries','contract_sha256'):
        assert updated[key]==state[key]
    for pid in state['paragraphs']:
        assert updated['paragraphs'][pid]['style_registry']==state['paragraphs'][pid]['style_registry']
        assert updated['paragraphs'][pid]['logical']['id']==pid
    return out,updated,report


def occupancy(state,pid):
    return [s['occupancy'] for s in state['slots'].values() if s['paragraph_id']==pid and s['occupancy'] is not None]


def test_independent_mixed_paragraphs_share_capacity_across_pages_and_empty_retype(tmp_path):
    source,state=prepared(tmp_path);original=source_sha(source);original_b=deepcopy(state['paragraphs']['B']['logical'])
    out,state,_=saved(tmp_path,source,state,{'A':replace(state,'A',SHORT)},'short')
    assert occupancy(state,'B')[0]['region_id']=='R1' and occupancy(state,'B')[0]['baseline']==84
    assert state['paragraphs']['B']['logical']==original_b
    assert state['physical_breaks']==[]
    out,state,report=saved(tmp_path,out,state,{'A':replace(state,'A',LONG)},'grow')
    assert occupancy(state,'B')[0]['region_id']=='R2' and occupancy(state,'B')[0]['baseline']==60
    assert len(state['paragraphs']['A']['logical']['boundaries'])==3
    assert state['paragraphs']['B']['logical']==original_b
    assert report['plan']['source_occupancy_dependencies']['slot-0']==['slot-1']
    assert report['plan']['schedule'].index('slot-1')<report['plan']['schedule'].index('slot-0')
    out,state,_=saved(tmp_path,out,state,{'A':dict(replace(state,'A',[]),typing_style_id='accent')},'empty-a')
    assert occupancy(state,'A')[0]['empty'] and occupancy(state,'A')[0]['baseline']==60
    assert occupancy(state,'B')[0]['baseline']==84
    out,state,_=saved(tmp_path,out,state,{'B':replace(state,'B',[])},'empty-both')
    assert set(state['paragraphs'])=={'A','B'} and all(occupancy(state,i)[0]['empty'] for i in ('A','B'))
    out,state,_=saved(tmp_path,out,state,{'A':dict(edits=[dict(start=0,end=0,text='Typed')]),
        'B':replace(state,'B',[dict(text='Other ',style_id='body'),dict(text='text',style_id='accent')])},'retyped')
    assert state['paragraphs']['A']['logical']['style_spans']==[dict(start=0,end=5,style_id='accent')]
    copied,state,_=saved(tmp_path,out,state,{},'noop')
    with pymupdf.open(out) as a,pymupdf.open(copied) as b,pymupdf.open(source) as old:
        assert all(a[i].get_pixmap().samples==b[i].get_pixmap().samples for i in range(len(a)))
        assert old[2].get_pixmap().samples==b[2].get_pixmap().samples
    assert source_sha(source)==original


@pytest.mark.parametrize('kind',['gap','empty','policy','paragraph_identity','paragraph_boundary','range','style','provider','width','unknown_paragraph','missing_slot','overflow'])
def test_shared_contract_refusals_leave_no_output(tmp_path,kind):
    source,state=prepared(tmp_path);changes={}
    if kind=='gap':state['follows'][0]['minimum_baseline_gap']=0
    if kind=='empty':state['paragraph_policies']['A']['empty']='unknown'
    if kind=='policy':state['paragraph_policies']['A']['keep_together']=True
    if kind=='paragraph_identity':state['paragraphs']['B']['logical']['id']='A'
    if kind=='paragraph_boundary':state['paragraph_boundaries']=[]
    if kind=='range':state['slots']['slot-1']['range']=[10,17]
    if kind=='style':state['slots']['slot-1']['style_binding']['styles']['s0']='accent'
    if kind=='provider':state['paragraphs']['B']['style_registry']['body']['reflow_provider']['sha256']='0'*64
    if kind=='width':state['regions']['R1']['width']=None
    if kind=='unknown_paragraph':changes={'unknown':dict(edits=[])}
    if kind=='missing_slot':changes={'A':replace(state,'A',[dict(text='First\nSecond\nThird\nFourth\nFifth',style_id='body')])}
    if kind=='overflow':changes={'B':replace(state,'B',[dict(text='Line\n'*13+'End',style_id='body')])}
    with pytest.raises(PdfError):edit_shared_flow(source,_reseal(state),tmp_path/'bad.pdf',tmp_path/'bad.json',changes)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


@pytest.mark.parametrize('kind',['keep','break'])
def test_confirmed_break_policies_change_final_allocation(tmp_path,kind):
    source,state=prepared(tmp_path,keep=kind=='keep',break_before='next-region' if kind=='break' else 'auto')
    changes={'A':replace(state,'A',SHORT),'B':replace(state,'B',[dict(text='First\nSecond\nThird\nFourth',style_id='body')])}
    _,_,report=saved(tmp_path,source,state,changes,'policy')
    plan=report['plan']
    assert plan['fragments']['slot-1']['occupancy'] is None
    assert plan['fragments']['slot-2']['occupancy']['baseline']==60


@pytest.mark.parametrize('kind',['clip','image','annotation','vector'])
def test_shared_flow_keeps_destination_paint_guards(tmp_path,kind):
    def decorate(source):
        if kind=='clip':
            writer=PdfWriter(clone_from=PdfReader(source));s=writer.pages[0]['/Contents'].get_object()
            s.set_data(b'q 0 140 320 120 re W n '+s.get_data()+b' Q');writer.write(source)
        else:
            with pymupdf.open(source) as doc:
                box=pymupdf.Rect(20,78,150,88)
                if kind=='annotation':doc[0].add_rect_annot(box)
                elif kind=='vector':doc[0].draw_rect(box,color=None,fill=(.4,.4,.4))
                else:
                    pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,2,2));pix.clear_with(100)
                    doc[0].insert_image(box,stream=pix.tobytes('png'))
                doc.saveIncr()
    source,state=prepared(tmp_path,decorate=decorate)
    # Clip case grows A below its clip; other cases place B on the obstacle.
    changes={'A':replace(state,'A',LONG if kind=='clip' else SHORT)}
    with pytest.raises(PdfError):edit_shared_flow(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',changes)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_late_fragment_failure_does_not_publish_shared_flow(tmp_path,monkeypatch):
    import pdfeditor.shared_flow as shared
    source,state=prepared(tmp_path);real=shared.plan_document_edit;calls=[]
    def fail(*args,**kwargs):
        calls.append(1)
        if len(calls)==2:raise PdfError('injected second-fragment failure')
        return real(*args,**kwargs)
    monkeypatch.setattr(shared,'plan_document_edit',fail)
    with pytest.raises(PdfError,match='second-fragment'):
        edit_shared_flow(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',{'A':replace(state,'A',SHORT)})
    assert len(calls)==2 and not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_failed_sidecar_publication_rolls_back_our_pdf(tmp_path,monkeypatch):
    from pathlib import Path
    import pdfeditor.editable as editable
    source,state=prepared(tmp_path);out,model=tmp_path/'final.pdf',tmp_path/'final.json'
    real=editable.os.link
    def fail(source,destination):
        if Path(destination)==model:raise OSError('injected final sidecar publication failure')
        return real(source,destination)
    monkeypatch.setattr(editable.os,'link',fail)
    with pytest.raises(OSError,match='final sidecar'):
        edit_shared_flow(source,state,out,model,{'A':replace(state,'A',SHORT)})
    assert not out.exists() and not model.exists()
