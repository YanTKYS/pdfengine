from copy import deepcopy

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.destination_style import PROPERTIES
from pdfeditor.editable import write_editable, edit_document, open_editable
from pdfeditor.paragraph import plan_paragraph
from test_attributed import source_pdf, snapshot


def recipes(tmp_path, paragraph):
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    values={}
    for ident,size,color in [('logical:body',10,[0,0,0]),('logical:accent',14,[0,0,1])]:
        p={k:deepcopy(paragraph['styles'][0][k]) for k in PROPERTIES}
        p.update(font_size=size,fill=['rg',color],observed_color=color,font_name='explicit provider')
        values[ident]=p
    return values,{i:dict(path=str(font)) for i in values}


@pytest.mark.parametrize('horizontal_scale',[1.0,.8])
def test_attributed_replacement_binds_missing_style_to_destination_and_keeps_source(tmp_path,horizontal_scale):
    source=source_pdf(tmp_path);p=snapshot(source);styles,fonts=recipes(tmp_path,p)
    styles['logical:accent']['horizontal_scale']=horizontal_scale
    edits=[dict(start=5,end=9,runs=[dict(text='new',style_id='logical:body'),dict(text='BLUE',style_id='logical:accent')])]
    plan=plan_paragraph(source,p,edits,fonts=fonts,render_styles=styles,width=160,max_bottom=180)
    out,model=tmp_path/'mixed.pdf',tmp_path/'mixed.json'
    r=write_editable(source,out,model,p,edits,fonts=fonts,render_styles=styles,width=160,max_bottom=180)
    assert r['after']==plan['text']=='HEAD newBLUE TAIL'
    assert r['retained_glyph_count']==10 and r['provided_font_glyph_count']==7
    assert open_editable(out,model)['status']=='restored'
    for g in r['glyph_plan']:
        if g['style_id']=='logical:accent':assert g['size']==14 and g['color']==[0,0,1]
    assert next(s for s in r['styles'] if s['id']=='logical:accent')['font_resource'] is None


def test_destination_typing_recipe_survives_empty_and_retype(tmp_path):
    source=source_pdf(tmp_path);p=snapshot(source);styles,fonts=recipes(tmp_path,p)
    empty,model=tmp_path/'empty.pdf',tmp_path/'empty.json'
    write_editable(source,empty,model,p,[dict(start=0,end=len(p['text']),runs=[])],
        fonts=fonts,render_styles=styles,empty_style_id='logical:accent',width=160,max_bottom=180)
    assert open_editable(empty,model)['state']['paragraph']['typing_style_id']=='logical:accent'
    out,newmodel=tmp_path/'retyped.pdf',tmp_path/'retyped.json'
    report=edit_document(empty,model,out,newmodel,[dict(start=0,end=0,runs=[
        dict(text='Blue',style_id='logical:accent'),dict(text=' body',style_id='logical:body')])],
        fonts=fonts,render_styles=styles)
    assert report['after']=='Blue body' and open_editable(out,newmodel)['status']=='restored'


@pytest.mark.parametrize('kind',['tracking','mixed_grapheme','conflicting_input'])
def test_destination_style_rejects_unproven_contracts(tmp_path,kind):
    source=source_pdf(tmp_path);p=snapshot(source);styles,fonts=recipes(tmp_path,p)
    edits=[dict(start=0,end=len(p['text']),runs=[dict(text='A',style_id='logical:body')])]
    if kind=='tracking':styles['logical:body']['tracking']=1
    if kind=='mixed_grapheme':edits[0]['runs'].append(dict(text='\u0301',style_id='logical:accent'))
    if kind=='conflicting_input':edits[0]['text']='ambiguous'
    with pytest.raises(PdfError):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,edits,
            fonts=fonts,render_styles=styles,width=160,max_bottom=180)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
