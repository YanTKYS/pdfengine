from copy import deepcopy
from pathlib import Path

import pymupdf
import pytest
from pypdf import PdfReader,PdfWriter

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.document_flow import _reseal
from pdfeditor.selection import make_selection,source_sha
from pdfeditor.story_flow import confirm_story,edit_story,open_story,plan_story
from pdfeditor.story_styles import project,style_ids
from test_attributed import source_pdf


def prepared(tmp_path,decorate=None,*,split_same_style=False):
    source=source_pdf(tmp_path,b'q .5 0 0 .5 0 0 cm BT /Regular 20 Tf 40 400 Td (first body ) Tj '
        b'/Bold 28 Tf 0 0 1 rg (BLUE) Tj ET Q BT /Regular 10 Tf 20 20 Td (FIXED) Tj ET')
    writer=PdfWriter(clone_from=PdfReader(source))
    writer.pages[1]['/Contents'].get_object().set_data(b'q 1.25 0 0 1.25 0 0 cm BT /Regular 8 Tf 16 160 Td (second body) Tj ET Q')
    writer.write(source)
    if decorate:decorate(source)
    font=tmp_path/'body.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    accent=Path('C:/Windows/Fonts/times.ttf')
    if not accent.exists():accent=font
    specs={}
    for ident,page,count,bottom in [('A',1,15,105),('B',2,11,215)]:
        p=inspect_paragraph(source,make_selection(source,page,glyph_ids=list(range(count)),explicit_width=150))
        specs[ident]=dict(page=page,bounds=[18,42,173,bottom],paragraph=p,paint_relations=[],
            layout=dict(x=20,baseline=60,width=150,max_bottom=bottom,min_line_height=22,first_line_indent=0))
    definitions={i:dict(provider=dict(path=str(path)),provider_relation='substituted') for i,path in [('body',font),('accent',accent)]}
    if split_same_style:definitions['other-body']=deepcopy(definitions['body'])
    state=confirm_story(source,specs,paragraph_id='mixed-story',chain=['A','B'],protected_regions={},
        styles=definitions,style_assignments={'A':{'s0':'body','s1':'accent'},'B':{'s0':'other-body' if split_same_style else 'body'}},
        typing_style_id='body')
    return source,state


RUNS=[dict(text='Body: ',style_id='body'),dict(text='Blue words continue through the page boundary with their original logical style identity. ',style_id='accent'),
      dict(text='Body follows.',style_id='body')]


def saved(tmp_path,source,state,edits,name,**options):
    plan=plan_story(source,state,edits,**options)
    out,model=tmp_path/f'{name}.pdf',tmp_path/f'{name}.json'
    report=edit_story(source,state,out,model,edits,**options)
    assert report['plan']==plan
    opened=open_story(out,model);assert opened['status']=='restored',opened
    after=opened['state']
    for key in ('style_registry','flow_chain','containers','pages'):
        assert after[key]==state[key]
    assert after['logical']['id']==state['logical']['id']
    assert all(f['binding']['logical_element']['id']==state['logical']['id'] for f in after['fragments'].values())
    return out,after,report


def test_mixed_story_rebinds_across_contexts_returns_empties_retypes_and_noops(tmp_path):
    source,state=prepared(tmp_path);original_sha=source_sha(source)
    out,state,r=saved(tmp_path,source,state,[dict(start=0,end=len(state['logical']['text']),runs=RUNS)],'mixed')
    cut=state['fragments']['A']['range'][1]
    ids=style_ids(state['logical']['text'],state['logical']['style_spans'],state['style_registry'])
    assert ids[cut-1]==ids[cut]=='accent'
    assert not any(s['start']==cut for s in state['logical']['style_spans'])
    assert state['logical']['boundaries']==[]
    for step in r['steps']:
        for glyph in step['report']['glyph_plan']:
            if glyph['style_id']=='logical:accent':assert glyph['size']==14 and glyph['color']==[0,0,1]
    out,state,_=saved(tmp_path,out,state,[dict(start=cut-2,end=cut+2,runs=[
        dict(text='BODY',style_id='body'),dict(text='blue',style_id='accent')])],'cross')
    out,state,_=saved(tmp_path,out,state,[dict(start=0,end=len(state['logical']['text']),runs=[
        dict(text='Blue ',style_id='accent'),dict(text='body.',style_id='body')])],'short')
    assert state['fragments']['B']['range'][0]==state['fragments']['B']['range'][1]
    out,state,_=saved(tmp_path,out,state,[dict(start=0,end=len(state['logical']['text']),runs=[])],'empty',typing_style_id='accent')
    assert state['logical']['style_spans']==[] and state['logical']['typing_style_id']=='accent'
    out,state,_=saved(tmp_path,out,state,[dict(start=0,end=0,text='Blue typing')],'typing')
    assert state['logical']['style_spans']==[dict(start=0,end=11,style_id='accent')]
    out,state,_=saved(tmp_path,out,state,[dict(start=0,end=len(state['logical']['text']),runs=RUNS)],'retype')
    copied,state,_=saved(tmp_path,out,state,[],'noop')
    with pymupdf.open(out) as a,pymupdf.open(copied) as b:
        assert all(a[i].get_pixmap().samples==b[i].get_pixmap().samples for i in range(len(a)))
    assert source_sha(source)==original_sha


@pytest.mark.parametrize('kind',['cross_style','boundary_insertion','grapheme','unknown_style',
    'provider_changed','attribute_changed','physical_style_changed','fragment_style_break','decoration','overflow','policy'])
def test_mixed_story_refuses_ambiguous_or_unproven_edits(tmp_path,kind):
    source,state=prepared(tmp_path);edits=[]
    if kind=='cross_style':edits=[dict(start=9,end=14,text='replacement')]
    if kind=='boundary_insertion':edits=[dict(start=11,end=11,text='X')]
    if kind=='grapheme':edits=[dict(start=0,end=1,runs=[dict(text='A',style_id='body'),dict(text='\u0301',style_id='accent')])]
    if kind=='unknown_style':edits=[dict(start=0,end=1,text='X',style_id='unknown')]
    if kind=='provider_changed':state['style_registry']['body']['reflow_provider']['sha256']='0'*64
    if kind=='attribute_changed':state['style_registry']['body']['attributes']['font_size']['value']=11
    if kind=='physical_style_changed':state['fragments']['B']['style_binding']['styles']['s0']='accent'
    if kind=='fragment_style_break':
        state['logical']['style_spans'][-1:]=[dict(start=15,end=20,style_id='body'),dict(start=20,end=26,style_id='body')]
    if kind=='decoration':state['logical']['decoration_ranges']=[dict(start=0,end=4)]
    if kind=='policy':state['reflow_font_policy']='copy_source_resources_to_other_pages'
    if kind=='overflow':edits=[dict(start=0,end=26,text='word '*50,style_id='accent')]
    state=_reseal(state)
    reasons={'cross_style':'explicit','boundary_insertion':'explicit','grapheme':'grapheme','unknown_style':'known style',
        'provider_changed':'changed','attribute_changed':'source witnesses','physical_style_changed':'inline attributes',
        'fragment_style_break':'physical breaks','decoration':'boundary semantics','overflow':'exceeds all','policy':'policy'}
    with pytest.raises(PdfError,match=reasons[kind]):edit_story(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',edits)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_projection_retains_unedited_styles_and_explicit_cross_style_replacement(tmp_path):
    _,state=prepared(tmp_path)
    text,spans,typing=project(state,[dict(start=9,end=17,text='X',style_id='accent')])
    assert text=='first bodXcond body'
    assert spans==[dict(start=0,end=9,style_id='body'),dict(start=9,end=10,style_id='accent'),dict(start=10,end=19,style_id='body')]
    assert typing=='body'


def test_identical_font_properties_do_not_merge_caller_style_identities(tmp_path):
    source,state=prepared(tmp_path,split_same_style=True)
    assert {k:v['value'] for k,v in state['style_registry']['body']['attributes'].items()}=={
        k:v['value'] for k,v in state['style_registry']['other-body']['attributes'].items()}
    out,state,report=saved(tmp_path,source,state,[dict(start=0,end=26,runs=[
        dict(text='Body ',style_id='body'),dict(text='Other',style_id='other-body')])],'identities')
    assert state['logical']['style_spans']==[dict(start=0,end=5,style_id='body'),dict(start=5,end=10,style_id='other-body')]
    assert set(state['fragments']['A']['style_binding']['styles'].values())=={'body','other-body'}


@pytest.mark.parametrize('kind',['clip','image','annotation','vector'])
def test_mixed_style_destination_keeps_existing_paint_guards(tmp_path,kind):
    def decorate(source):
        if kind=='clip':
            writer=PdfWriter(clone_from=PdfReader(source));stream=writer.pages[1]['/Contents'].get_object()
            stream.set_data(b'q 0 185 320 75 re W n '+stream.get_data()+b' Q');writer.write(source)
        else:
            with pymupdf.open(source) as doc:
                box=pymupdf.Rect(20,77,170,89)
                if kind=='annotation':doc[1].add_rect_annot(box)
                elif kind=='vector':doc[1].draw_rect(box,color=None,fill=(.4,.4,.4))
                else:
                    pix=pymupdf.Pixmap(pymupdf.csRGB,pymupdf.IRect(0,0,2,2));pix.clear_with(100)
                    doc[1].insert_image(box,stream=pix.tobytes('png'))
                doc.saveIncr()
    source,state=prepared(tmp_path,decorate);sha=source_sha(source)
    with pytest.raises(PdfError):
        edit_story(source,state,tmp_path/'bad.pdf',tmp_path/'bad.json',
            [dict(start=0,end=len(state['logical']['text']),runs=RUNS)])
    assert source_sha(source)==sha and not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
