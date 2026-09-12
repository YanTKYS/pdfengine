"""Confirmed logical relations survive physical revisions and zero glyphs."""
from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.document_flow import confirm_document, edit_flow, open_document, _reseal
from pdfeditor.elements import inspect_element
from pdfeditor.selection import make_selection, source_sha
from test_attributed import source_pdf


def prepared(tmp_path, *, blocker=False):
    source=source_pdf(tmp_path,b'.9 .9 0 rg 15 155 85 18 re f 0 g '
        b'BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj 0 -40 Td (NEXT) Tj '
        b'180 -25 Td (FIXED) Tj ET '+(b'BT /Regular 12 Tf 20 75 Td (BLOCK) Tj ET' if blocker else b''))
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    elements={}
    for ident,ids in [('A',list(range(7))),('B',list(range(7,11)))]:
        p=inspect_paragraph(source,make_selection(source,glyph_ids=ids,explicit_width=90))
        e=inspect_element(source,p['selection'])
        paint=e['paths'][0]['source_id']
        elements[ident]=dict(paragraph=p,layout=dict(width=90,min_line_height=18,max_bottom=220),
            fonts={'s0':{'path':str(font)}},
            paint_relations=[dict(source_id=paint,relation='backgrounds',behavior='fixed-to-page')] if ident=='B' else [],
            owned_paints=[paint] if ident=='B' else [])
    model=confirm_document(source,elements,container_id='body',bounds=[10,40,120,220],page=1,
                           follows=[dict(before='A',after='B',gap=40)])
    return source,model


def change(tmp_path, pdf, model, ident, text, n):
    before=model['elements'][ident]['binding']['paragraph']['text']
    out,path=tmp_path/f'g{n}.pdf',tmp_path/f'g{n}.json'
    r=edit_flow(pdf,model,out,path,ident,[dict(start=0,end=len(before),text=text)] if before!=text else [])
    restored=open_document(out,path)
    assert restored['status']=='restored',restored
    state=restored['state']
    assert state['elements'][ident]['binding']['paragraph']['text']==text
    assert list(state['elements'])==['A','B'] and state['follows']==model['follows']
    assert state['container']==model['container']
    return out,state,r


def test_following_owned_paint_empty_and_nonempty_lifecycle(tmp_path):
    source,model=prepared(tmp_path);initial=source_sha(source)
    assert open_document(source,model)['status']=='restored' and source_sha(source)==initial
    pdf,state,r=change(tmp_path,source,model,'A','ONE TWO THREE FOUR FIVE SIX',1)
    assert r['baseline_delta']>0 and r['moved_elements']==['B']
    assert [s['kind'] for s in r['steps']]==['move','edit']
    assert r['steps'][0]['report']['moved_path_operators']==1
    assert state['elements']['B']['binding']['paragraph']['text']=='NEXT'
    assert state['elements']['B']['binding']['physical_layout']['provenance']=='generated_from_explicit_follows'
    with pymupdf.open(source) as a,pymupdf.open(pdf) as b:
        assert a[1].get_pixmap().samples==b[1].get_pixmap().samples
        assert [g for g in a[0].get_text('dict')['blocks'] if g.get('lines')][-1]['bbox']==[g for g in b[0].get_text('dict')['blocks'] if g.get('lines')][-1]['bbox']
    pdf,state,r=change(tmp_path,pdf,state,'A','SHORT',2)
    assert r['baseline_delta']<0 and [s['kind'] for s in r['steps']]==['edit','move']
    # A can move a follower that currently has no glyphs, including its owned
    # background. B's insertion slot is mapped through known operator edits.
    pdf,state,_=change(tmp_path,pdf,state,'B','',3)
    assert state['elements']['B']['binding']['paragraph']['selection']['glyph_ids']==[]
    pdf,state,r=change(tmp_path,pdf,state,'A','ONE TWO THREE FOUR FIVE SIX',4)
    assert r['steps'][0]['report']['moved_glyphs']==0 and r['steps'][0]['report']['moved_path_operators']==1
    pdf,state,r=change(tmp_path,pdf,state,'A','',5)
    assert all(e['binding']['paragraph']['text']=='' for e in state['elements'].values())
    assert PdfReader(pdf).pages[0].extract_text().strip()=='FIXED'
    pdf,state,_=change(tmp_path,pdf,state,'A','RETYPE PARAGRAPH AGAIN',6)
    pdf,state,_=change(tmp_path,pdf,state,'B','NEXT',7)
    copy,_,r=change(tmp_path,pdf,state,'A',state['elements']['A']['binding']['paragraph']['text'],8)
    assert not r['moved_elements']
    with pymupdf.open(pdf) as a,pymupdf.open(copy) as b:
        assert a[0].get_pixmap().samples==b[0].get_pixmap().samples


@pytest.mark.parametrize('mode',['stale','cycle','unknown','wrong_gap','shared_glyph','duplicate_owner','unknown_width','overflow','fixed_collision'])
def test_graph_and_transaction_guards_publish_nothing(tmp_path,mode):
    source,model=prepared(tmp_path,blocker=mode=='fixed_collision')
    if mode=='stale':
        w=PdfWriter(clone_from=PdfReader(source));w.add_metadata({'/Title':'external save'})
        external=tmp_path/'external.pdf';w.write(external);source=external
    if mode=='cycle':model['follows'].append(dict(before='B',after='A',gap=40,provenance='explicitly_confirmed'))
    if mode=='unknown':model['follows'][0]['after']='C'
    if mode=='wrong_gap':model['follows'][0]['gap']=2
    if mode=='shared_glyph':
        model['elements']['B']=deepcopy(model['elements']['A'])
        b=model['elements']['B']['binding'];b['logical_element']['id']='B';model['elements']['B']['binding']=_reseal(b)
    if mode=='duplicate_owner':model['elements']['B']['owned_paints']*=2
    if mode=='unknown_width':
        b=model['elements']['A']['binding'];b['layout']['width']=None;model['elements']['A']['binding']=_reseal(b)
    model=_reseal(model)
    text='ONE\nTWO\nTHREE\nFOUR\nFIVE\nSIX\nSEVEN\nEIGHT' if mode=='overflow' else (
        'ONE\nTWO\nTHREE\nFOUR\nFIVE\nSIX' if mode=='fixed_collision' else 'NEW')
    out,path=tmp_path/'bad.pdf',tmp_path/'bad.json'
    with pytest.raises((PdfError,ValueError)):
        edit_flow(source,model,out,path,'A',[dict(start=0,end=7,text=text)])
    assert not out.exists() and not path.exists()
    assert not list(tmp_path.glob('.flow-*'))


def test_without_edge_lower_element_is_fixed(tmp_path):
    source,model=prepared(tmp_path);model['follows']=[];model=_reseal(model)
    # The unchanged B and its owned background block A's expansion. Their
    # lower coordinates do not imply permission to move either occurrence.
    with pytest.raises(PdfError,match='intersect|overlap|paint|collides'):
        change(tmp_path,source,model,'A','ONE TWO THREE FOUR FIVE SIX',1)


def test_publication_failure_rolls_back_pdf_and_model(tmp_path,monkeypatch):
    import os
    source,model=prepared(tmp_path);out,path=tmp_path/'out.pdf',tmp_path/'out.json'
    link=os.link
    def fail_model(a,b):
        if b==path:raise OSError('simulated model publication failure')
        return link(a,b)
    monkeypatch.setattr(os,'link',fail_model)
    with pytest.raises(OSError,match='simulated'):
        edit_flow(source,model,out,path,'A',[])
    assert not out.exists() and not path.exists()


def test_three_element_chain_moves_farthest_follower_first(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ONE) Tj '
                      b'0 -50 Td (TWO) Tj 0 -50 Td (END) Tj ET')
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    specs={i:dict(paragraph=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(n*3,n*3+3)),explicit_width=90)),
                  layout=dict(width=90,min_line_height=18,max_bottom=250),fonts={'s0':{'path':str(font)}})
           for n,i in enumerate(('A','B','C'))}
    model=confirm_document(source,specs,container_id='body',bounds=[10,40,120,250],page=1,
                           follows=[dict(before='A',after='B',gap=50),dict(before='B',after='C',gap=50)])
    out,path=tmp_path/'out.pdf',tmp_path/'out.json'
    report=edit_flow(source,model,out,path,'A',[dict(start=0,end=3,text='ONE TWO THREE FOUR')])
    assert [s['element_id'] for s in report['steps']]==['C','B','A']
    state=open_document(out,path)['state']
    assert state['elements']['C']['binding']['paragraph']['text']=='END'
    assert state['elements']['C']['binding']['layout']['baseline']==pytest.approx(160+report['baseline_delta'])
