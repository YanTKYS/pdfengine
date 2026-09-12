from copy import deepcopy

import pymupdf
import pytest

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.document_flow import confirm_document, _reseal
from pdfeditor.elements import inspect_element
from pdfeditor.paint_resize import MODEL, extend_geometry
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.variable_container import (confirm_variable_container, edit_variable_container,
                                          open_variable_container, plan_variable_container)
from test_attributed import source_pdf


def prepared(tmp_path, obstacle=b'', decorate=None, min_line_height=20):
    source=source_pdf(tmp_path,b'.9 .9 0 rg 10 80 110 160 re f 0 g '
        b'BT /Regular 12 Tf 20 220 Td (HEAD) Tj 0 -30 Td (ONE) Tj 0 -40 Td (NEXT) Tj '
        b'180 -20 Td (FIXED) Tj ET '+obstacle)
    if decorate: decorate(source)
    font=tmp_path/'font.ttf'; font.write_bytes(pymupdf.Font('cjk').buffer)
    elements={}
    for ident,ids in [('H',range(4)),('A',range(4,7)),('B',range(7,11))]:
        p=inspect_paragraph(source,make_selection(source,glyph_ids=list(ids),explicit_width=90))
        e=inspect_element(source,p['selection']); path=e['paths'][0]['source_id']
        elements[ident]=dict(paragraph=p,layout=dict(width=90,max_bottom=175,min_line_height=min_line_height),
            fonts={'s0':{'path':str(font)}},paint_relations=[dict(source_id=path,relation='backgrounds',behavior='fixed-to-page')])
    doc=confirm_document(source,elements,container_id='note',bounds=[15,25,115,175],page=1,
                         follows=[dict(before='A',after='B',gap=40)])
    model=confirm_variable_container(source,doc,paints=[dict(source_id=path,role='backgrounds',band=[60,160],
        geometry_model=MODEL,bottom_anchor='preserve-offset-to-container-bottom')],min_bottom=175,max_bottom=235,
        baseline_bottom_gap=6,available_paint_region=[8,18,124,242],fixed_children=['H'])
    return source,model


def change(model, **texts):
    return {i:dict(edits=[dict(start=0,end=len(model['document']['elements'][i]['binding']['paragraph']['text']),text=t,style_id='s0')],
                   empty_style_id='s0') for i,t in texts.items()}


def edit(tmp_path,source,model,texts,name):
    output,sidecar=tmp_path/f'{name}.pdf',tmp_path/f'{name}.json'
    request=change(model,**texts); plan=plan_variable_container(source,model,request)
    result=edit_variable_container(source,model,output,sidecar,request)
    restored=open_variable_container(output,sidecar)
    assert restored['status']=='restored',restored
    state=restored['state']
    assert state['layout_policy']==model['layout_policy'] and state['semantic_owner']==model['semantic_owner']
    assert state['fixed_children']==model['fixed_children']
    assert state['document']['follows']==model['document']['follows']
    assert result['plan']==plan
    return output,state,result


def test_shared_container_expands_contracts_and_survives_empty_retype(tmp_path):
    source,model=prepared(tmp_path); original=source_sha(source)
    long='ONE\nTWO\nTHREE\nFOUR\nFIVE\nSIX'
    pdf,state,r=edit(tmp_path,source,model,{'A':long},'grow')
    assert r['plan']['delta']>0 and [s['kind'] for s in r['steps']]==['resize','edit']
    assert state['document']['container']['bounds'][3]>175
    assert state['paints'][0]['owner']=='note'
    assert all(not e['owned_paints'] for e in state['document']['elements'].values())
    with pymupdf.open(source) as a,pymupdf.open(pdf) as b:
        assert a[1].get_pixmap().samples==b[1].get_pixmap().samples
    pdf,state,r=edit(tmp_path,pdf,state,{'A':'ONE'},'shrink')
    assert r['plan']['delta']<0 and [s['kind'] for s in r['steps']]==['edit','resize']
    assert state['document']['container']['bounds'][3]==175
    pdf,state,_=edit(tmp_path,pdf,state,{'A':'','B':''},'empty')
    assert all(not state['document']['elements'][i]['binding']['paragraph']['text'] for i in ('A','B'))
    pdf,state,r=edit(tmp_path,pdf,state,{'A':long,'B':'NEXT'},'retype')
    assert r['plan']['delta']>0
    copied=tmp_path/'noop.pdf'; sidecar=tmp_path/'noop.json'
    report=edit_variable_container(pdf,state,copied,sidecar,{'A':{'edits':[]},'B':{'edits':[]}})
    assert report['plan']['delta']==0 and report['plan']['final_bottom']==state['document']['container']['bounds'][3]
    with pymupdf.open(pdf) as a,pymupdf.open(copied) as b:
        assert all(a[i].get_pixmap().samples==b[i].get_pixmap().samples for i in range(len(a)))
    assert source_sha(source)==original


@pytest.mark.parametrize('mode',['fixed_child','overflow','missing_anchor','unknown_width','wrong_owner','stale','obstacle'])
def test_variable_guards_publish_nothing(tmp_path,mode):
    source,model=prepared(tmp_path,b'BT /Regular 12 Tf 20 65 Td (BLOCK) Tj ET' if mode=='obstacle' else b'')
    texts={'A':'ONE\nTWO\nTHREE\nFOUR\nFIVE\nSIX'}
    if mode=='fixed_child': texts={'H':'OTHER'}
    if mode=='overflow': texts['A']+='\nSEVEN\nEIGHT\nNINE'
    if mode=='missing_anchor': model['paints'][0].pop('bottom_anchor')
    if mode=='wrong_owner': model['paints'][0]['owner']='A'
    if mode=='unknown_width':
        b=model['document']['elements']['A']['binding'];b['layout']['width']=None
        model['document']['elements']['A']['binding']=_reseal(b);model['document']=_reseal(model['document'])
    if mode=='stale': model['pdf_sha256']='0'*64
    model=_reseal(model)
    with pytest.raises((PdfError,ValueError)):
        edit_variable_container(source,model,tmp_path/'bad.pdf',tmp_path/'bad.json',change(model,**texts))
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
    assert not list(tmp_path.glob('.variable-*'))


@pytest.mark.parametrize('mode',['slanted','curve','inside','open','collapse','stroke','opacity'])
def test_band_proof_never_treats_arbitrary_shapes_as_rectangles(tmp_path,mode):
    _,model=prepared(tmp_path)
    paint=deepcopy(model['document']['elements']['A']['binding']['element']['paths'][0]['proof']['paints'][0])
    delta=25
    if mode=='slanted': paint['geometry'][2][1]+=3
    if mode=='curve': paint['geometry'][2]=['c',120,40,120,120,120,180]
    if mode=='inside': paint['geometry'][1][2]=120
    if mode=='open': paint['geometry'].pop()
    if mode=='collapse': delta=-100
    if mode=='stroke': paint['kind']='stroke-path'
    if mode=='opacity': paint['opacity']=.5
    with pytest.raises(PdfError): extend_geometry(paint,[60,160],delta)


def test_error_after_paint_mutation_does_not_publish(tmp_path,monkeypatch):
    import pdfeditor.variable_container as module
    source,model=prepared(tmp_path); original=source_sha(source)
    def fail(*a,**k): raise PdfError('injected text mutation failure')
    monkeypatch.setattr(module,'edit_flow_batch',fail)
    with pytest.raises(PdfError,match='injected'):
        edit_variable_container(source,model,tmp_path/'bad.pdf',tmp_path/'bad.json',
                                change(model,A='ONE\nTWO\nTHREE\nFOUR\nFIVE\nSIX'))
    assert source_sha(source)==original and not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
    assert not list(tmp_path.glob('.variable-*'))


@pytest.mark.parametrize('kind',['image','annotation','clip'])
def test_extension_keeps_image_annotation_and_clip_guards(tmp_path,kind):
    def decorate(source):
        if kind=='clip':
            from pypdf import PdfReader,PdfWriter
            w=PdfWriter(clone_from=PdfReader(source));s=w.pages[0]['/Contents'].get_object()
            data=s.get_data().replace(b'.9 .9 0 rg',b'q 0 75 200 185 re W n .9 .9 0 rg',1)
            s.set_data(data.replace(b're f 0 g',b're f Q 0 g',1));w.write(source)
        else:
            with pymupdf.open(source) as doc:
                if kind=='image':
                    pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,2,2));pix.clear_with(100)
                    doc[0].insert_image(pymupdf.Rect(20,188,50,208),stream=pix.tobytes('png'))
                else: doc[0].add_rect_annot(pymupdf.Rect(20,188,50,208))
                doc.saveIncr()
    source,model=prepared(tmp_path,decorate=decorate)
    with pytest.raises(PdfError,match=kind):
        edit_variable_container(source,model,tmp_path/'bad.pdf',tmp_path/'bad.json',
                                change(model,A='ONE\nTWO\nTHREE\nFOUR\nFIVE\nSIX'))
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_curved_caps_and_hole_keep_shape_under_certified_extension(tmp_path):
    _,model=prepared(tmp_path)
    paint=deepcopy(model['document']['elements']['A']['binding']['element']['paths'][0]['proof']['paints'][0])
    paint['geometry']=[['m',15,20],['l',115,20],['c',118,20,120,22,120,25],
        ['l',120,175],['c',120,178,118,180,115,180],['l',15,180],
        ['c',12,180,10,178,10,175],['l',10,25],['c',10,22,12,20,15,20],['h'],
        ['m',15,25],['l',15,175],['l',115,175],['l',115,25],['h']]
    paint['even_odd']=True
    proof=extend_geometry(paint,[60,160],25)
    geometry=proof['expected']['geometry']
    assert geometry[2]==paint['geometry'][2] and geometry[8]==paint['geometry'][8]
    assert geometry[4]==['c',120,203,118,205,115,205]
    assert geometry[6]==['c',12,205,10,203,10,200]
    assert proof['vertical_crossings']==4 and proof['expected']['even_odd'] is True


def test_generated_leading_does_not_shrink_when_font_becomes_retained(tmp_path):
    source,model=prepared(tmp_path,min_line_height=12)
    pdf,state,_=edit(tmp_path,source,model,{'A':'ONE\nTWO\nTHREE\nFOUR\nFIVE\nSIX','B':'NEXT'},'grow')
    output,sidecar=tmp_path/'noop.pdf',tmp_path/'noop.json'
    report=edit_variable_container(pdf,state,output,sidecar,{'A':{'edits':[]},'B':{'edits':[]}})
    assert abs(report['plan']['delta'])<.002
    reopened=open_variable_container(output,sidecar)['state']
    before=state['document']['elements']['A']['binding']['physical_layout']['lines']
    after=reopened['document']['elements']['A']['binding']['physical_layout']['lines']
    assert [p['baseline'] for p in after]==pytest.approx([p['baseline'] for p in before],abs=.002)
    with pymupdf.open(pdf) as a,pymupdf.open(output) as b:
        assert all(a[i].get_pixmap().samples==b[i].get_pixmap().samples for i in range(len(a)))
