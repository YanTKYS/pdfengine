"""An editable paragraph survives the disappearance of all of its glyphs."""
import json
from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter

from pdfeditor.attributed import digest, inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.editable import write_editable, edit_document, open_editable, _seal
from pdfeditor.elements import inspect_element
from pdfeditor.content_stream import ContentPage
from pdfeditor.logical_element import slot_binding
from pdfeditor.selection import make_selection
from test_attributed import source_pdf
from test_anchors import prepared


def empty(tmp_path, *, stream=None):
    source=source_pdf(tmp_path,stream or b'BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj 100 0 Td (KEEP) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7)),explicit_width=85))
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    out,state=tmp_path/'empty.pdf',tmp_path/'empty.json'
    report=write_editable(source,out,state,p,[dict(start=0,end=7,text='')],
        fonts={'s0':{'path':str(font)}},max_bottom=130,min_line_height=18)
    return source,p,out,state,report


def test_empty_reopen_retype_clear_retype_without_selection(tmp_path):
    source,p,pdf,path,report=empty(tmp_path)
    original_state=open_editable(pdf,path)['state']
    paragraph=original_state['paragraph']
    assert paragraph['kind']=='empty-logical-paragraph' and paragraph['text']==''
    assert paragraph['selection']['glyph_ids']==[] and paragraph['styles']==p['styles']
    assert paragraph['typing_style_id']=='s0' and len(paragraph['style_recipes'])==1
    assert report['glyph_plan']==[] and PdfReader(pdf).pages[0].extract_text().strip()=='KEEP'
    assert 'ONE TWO' not in path.read_text(encoding='utf-8')
    empty_copy,copy_state=tmp_path/'empty-copy.pdf',tmp_path/'empty-copy.json'
    edit_document(pdf,path,empty_copy,copy_state,[])
    with pymupdf.open(pdf) as a,pymupdf.open(empty_copy) as b:
        assert a[0].get_pixmap().samples==b[0].get_pixmap().samples
    for i,text in enumerate(['NEW TEXT','','AGAIN']):
        next_pdf,next_state=tmp_path/f'next{i}.pdf',tmp_path/f'next{i}.json'
        before=open_editable(pdf,path)['state']['paragraph']['text']
        r=edit_document(pdf,path,next_pdf,next_state,[dict(start=0,end=len(before),text=text)])
        state=open_editable(next_pdf,next_state)['state']
        assert state['paragraph']['text']==text
        assert state['layout']==original_state['layout']
        assert state['container']==original_state['container']
        assert state['layout_provenance']==original_state['layout_provenance']
        if text:
            assert r['glyph_plan'][0]['origin']==[20,60]
            assert all(g['size']==12 for g in r['glyph_plan'])
        with pymupdf.open(source) as a,pymupdf.open(next_pdf) as b:
            assert a[1].get_pixmap().samples==b[1].get_pixmap().samples
        pdf,path=next_pdf,next_state


@pytest.mark.parametrize('mode',['offset','context','old_text','style','stale','missing_font','collision','clip'])
def test_empty_binding_and_reinsertion_guards(tmp_path,mode):
    stream=(b'0 180 80 40 re W n BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj ET' if mode=='clip' else None)
    _,_,pdf,path,_=empty(tmp_path,stream=stream)
    state=json.loads(path.read_text(encoding='utf-8'));state.pop('model_sha256')
    p=state['paragraph']
    if mode=='offset':p['insertion_binding']['event']['byte_range'][0]+=1
    if mode=='context':p['insertion_binding']['event']['state']['opacity']=.5
    if mode=='old_text':p['text']='ONE TWO'
    if mode=='style':p['style_recipes']['s0']['properties']['font_size']=-1
    if mode=='stale':
        writer=PdfWriter(clone_from=PdfReader(pdf));writer.add_metadata({'/Title':'external'})
        target=tmp_path/'external.pdf';writer.write(target);pdf=target
    if mode=='missing_font':(tmp_path/'font.ttf').unlink()
    p.pop('snapshot_sha256');p['snapshot_sha256']=digest(p);state=_seal(state)
    if mode in ('offset','context','old_text','style','stale'):
        assert open_editable(pdf,state)['status']=='needs_confirmation'
    override={'x':120,'width':85} if mode=='collision' else {'width':180} if mode=='clip' else {}
    with pytest.raises((PdfError,ValueError)):
        edit_document(pdf,state,tmp_path/'bad.pdf',tmp_path/'bad.json',
                      [dict(start=0,end=0,text='NEW TEXT LONG')],**override)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_multistyle_empty_needs_confirmed_typing_style(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ONE) Tj /Bold 12 Tf (TWO) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(6)),explicit_width=100))
    change=[dict(start=0,end=6,text='',style_id='s0')]
    with pytest.raises(PdfError,match='empty_style_id'):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,change)
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    write_editable(source,tmp_path/'empty.pdf',tmp_path/'empty.json',p,change,
        empty_style_id='s1',fonts={'s1':{'path':str(font)}})
    state=open_editable(tmp_path/'empty.pdf',tmp_path/'empty.json')['state']
    assert len(state['paragraph']['styles'])==2 and state['paragraph']['typing_style_id']=='s1'
    r=edit_document(tmp_path/'empty.pdf',state,tmp_path/'new.pdf',tmp_path/'new.json',
                    [dict(start=0,end=0,text='NEW')])
    assert r['logical_styles']==['s1']*3


def test_empty_decoration_is_not_silently_discarded(tmp_path):
    source,p,e,a=prepared(tmp_path)
    with pytest.raises(PdfError,match='dormant decoration'):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,
            [dict(start=0,end=len(p['text']),text='')],element_snapshot=e,anchor_spec=a)
    assert not (tmp_path/'bad.pdf').exists()


@pytest.mark.parametrize('relation',['confirmed','missing','later_paint'])
def test_fixed_background_ownership_survives_zero_glyphs(tmp_path,relation):
    background=b'.9 g 10 160 100 60 re f .8 g 15 165 90 50 re f 0 g '
    text=b'BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj ET '
    source=source_pdf(tmp_path,(text+background if relation=='later_paint' else background+text))
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7)),explicit_width=80))
    element=inspect_element(source,p['selection'])
    fixed=[dict(source_id=x['source_id'],relation='backgrounds',behavior='fixed-to-page') for x in element['paths']]
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    write_editable(source,tmp_path/'empty.pdf',tmp_path/'empty.json',p,[dict(start=0,end=7,text='')],
        element_snapshot=element,element_relations=fixed,fonts={'s0':{'path':str(font)}})
    state=open_editable(tmp_path/'empty.pdf',tmp_path/'empty.json')['state']
    assert state['element']['text_element']['observed_bounds'] is None
    assert len(state['relations'])==2 and state['paragraph']['selection']['glyph_ids']==[]
    if relation=='missing':
        state.pop('model_sha256');state['relations']=[];state=_seal(state)
    def retype():
        return edit_document(tmp_path/'empty.pdf',state,tmp_path/'new.pdf',tmp_path/'new.json',
                             [dict(start=0,end=0,text='NEW TEXT')])
    if relation=='confirmed':
        retype()
        with pymupdf.open(source) as a,pymupdf.open(tmp_path/'new.pdf') as b:
            # Raw drawing seqnos change when text is removed, then return with
            # retyping. Both confirmed background paint occurrences survive.
            assert a[0].get_drawings()==b[0].get_drawings()
    else:
        with pytest.raises(PdfError,match='background.*relation'):retype()
        assert not (tmp_path/'new.pdf').exists()


def test_ambiguous_duplicate_paint_still_refuses_ownership(tmp_path):
    source=source_pdf(tmp_path,b'.9 g 10 160 100 60 re f 10 160 100 60 re f '
                      b'0 g BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7)),explicit_width=80))
    e=inspect_element(source,p['selection'])
    with pytest.raises(PdfError,match='unique source provenance'):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,[dict(start=0,end=7,text='')],
            element_snapshot=e,element_relations=[dict(source_id=x['source_id'],relation='backgrounds',
                behavior='fixed-to-page') for x in e['paths']])
    assert not (tmp_path/'bad.pdf').exists()


def test_glyph_free_page_preserves_scaled_color_style_against_direct_control(tmp_path):
    source,p,pdf,state,_=empty(tmp_path,stream=b'q 1.2 0 0 .8 0 0 cm '
        b'BT /Regular 15 Tf 85 Tz .2 .3 .7 rg 20 200 Td (ONE TWO) Tj ET Q')
    font={'s0':{'path':str(tmp_path/'font.ttf')}}
    with pymupdf.open(pdf) as doc:assert doc[0].get_texttrace()==[]
    direct=write_editable(source,tmp_path/'control.pdf',tmp_path/'control.json',p,
        [dict(start=0,end=7,text='NEW')],fonts=font,max_bottom=130,min_line_height=18)
    inserted=edit_document(pdf,state,tmp_path/'typed.pdf',tmp_path/'typed.json',[dict(start=0,end=0,text='NEW')])
    assert [g['origin'] for g in inserted['glyph_plan']]==[g['origin'] for g in direct['glyph_plan']]
    with pymupdf.open(tmp_path/'control.pdf') as a,pymupdf.open(tmp_path/'typed.pdf') as b:
        assert a[0].get_pixmap().samples==b[0].get_pixmap().samples


def test_nonpainting_slot_does_not_bypass_actualtext_guard(tmp_path):
    source=source_pdf(tmp_path,b'/Span <</ActualText (hidden)>> BDC '
        b'BT /Regular 12 Tf 20 200 Td [] TJ ET EMC')
    content=ContentPage(source,1)
    try:
        with pytest.raises(PdfError,match='marked-content semantics'):
            slot_binding(content,content.events[0].operator.start)
    finally:content.close()


def test_legacy_state_can_migrate_without_glyph_or_style_reselection(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7)),explicit_width=90))
    write_editable(source,tmp_path/'one.pdf',tmp_path/'one.json',p,[])
    state=json.loads((tmp_path/'one.json').read_text(encoding='utf-8'))
    state.pop('model_sha256');state.pop('logical_element');state['schema']='pdfengine-editable-1';state=_seal(state)
    assert open_editable(tmp_path/'one.pdf',state)['status']=='restored'
    edit_document(tmp_path/'one.pdf',state,tmp_path/'empty.pdf',tmp_path/'empty.json',
                  [dict(start=0,end=7,text='')])
    empty_state=open_editable(tmp_path/'empty.pdf',tmp_path/'empty.json')['state']
    assert empty_state['schema']=='pdfengine-editable-2'
    # A style survives without font coverage. Retyping still requires a real
    # provider; it does not guess a code from the old subset's display name.
    with pytest.raises(PdfError,match='explicit font'):
        edit_document(tmp_path/'empty.pdf',empty_state,tmp_path/'bad.pdf',tmp_path/'bad.json',
                      [dict(start=0,end=0,text='NEW')])
    assert not (tmp_path/'bad.pdf').exists()
