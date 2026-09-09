from copy import deepcopy

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.elements import inspect_element,move_element,_local_translation
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.paragraph import edit_paragraph
from pdfeditor.model import Rect
from pdfeditor.paint_geometry import fill_cells,contains_fill,intersects_fill
from pdfeditor.selection import make_selection
from test_attributed import source_pdf,observe


def rectangle(x,y,w,h,reverse=False):
    points=[(x,y),(x+w,y),(x+w,y+h),(x,y+h)]
    if reverse:points.reverse()
    return [[('m' if i==0 else 'l'),*p] for i,p in enumerate(points)]+[['h']]


@pytest.mark.parametrize('even_odd,reverse,hole',[(False,True,True),(False,False,False),(True,False,True)])
def test_compound_rectangle_holes_follow_actual_fill_rule(even_odd,reverse,hole):
    paint={'kind':'fill-path','even_odd':even_odd,'geometry':rectangle(0,0,100,100)+rectangle(10,10,80,80,reverse)}
    assert intersects_fill(paint,Rect(20,20,30,30)) is (not hole)
    assert contains_fill(paint,Rect(20,20,30,30)) is (not hole)
    assert intersects_fill(paint,Rect(2,2,5,5))
    assert fill_cells({'kind':'fill-path','geometry':[['m',0,0],['c',1,0,2,1,3,1]],'even_odd':False}) is None


def prepared(tmp_path):
    p=source_pdf(tmp_path,b'0.9 g 18 186 125 30 re f 0 g '
        b'BT /Regular 12 Tf 20 200 Td (ABCD) Tj ET 20 198 28.8 .7 re f '
        b'BT /Regular 12 Tf 220 200 Td (KEEP) Tj ET')
    snapshot=inspect_element(p,make_selection(p,glyph_ids=list(range(4))))
    relations=[dict(source_id=x['source_id'],relation='backgrounds' if i==0 else 'decorates',
                    behavior='fixed-to-element') for i,x in enumerate(snapshot['paths'])]
    return p,snapshot,relations


def test_group_moves_text_background_decoration_with_original_font(tmp_path):
    p,s,r=prepared(tmp_path)
    output,removed=tmp_path/'moved.pdf',tmp_path/'removed.pdf'
    report=move_element(p,output,s,r,dx=0,dy=60,removal_output=removed)
    assert report['moved_path_operators']==2 and report['moved_glyphs']==4
    a,b=observe(p),observe(output)
    for i in range(4):
        assert b[i]['origin'][1]==pytest.approx(a[i]['origin'][1]+60)
        assert b[i]['glyph_id']==a[i]['glyph_id'] and b[i]['font']==a[i]['font']
    assert a[4:]==b[4:]
    assert ''.join(g['unicode'] for g in observe(removed))=='KEEP'
    with pymupdf.open(removed) as doc:
        assert doc[0].get_drawings()==[]


@pytest.mark.parametrize('mode',['unresolved','collision','tampered','incomplete'])
def test_movement_refuses_incomplete_relations_and_unsafe_destinations(tmp_path,mode):
    p,s,r=prepared(tmp_path)
    dx,dy=0,60
    if mode=='unresolved':r=r[:1]
    if mode=='collision':dx,dy=150,0
    if mode=='tampered':s['paths'][0]['proof']['paints'][0]['opacity']=0.3
    if mode=='incomplete':
        s=inspect_element(p,make_selection(p,glyph_ids=[0,1]));r=[dict(x) for x in r]
    output=tmp_path/'bad.pdf'
    with pytest.raises(PdfError):move_element(p,output,s,r,dx=dx,dy=dy)
    assert not output.exists()


@pytest.mark.parametrize('late', [False, True])
def test_explicit_background_needs_proven_coverage_and_earlier_paint(tmp_path, late):
    background=b'0.9 g 0 0 m 320 0 l 320 130 l 320 260 l 0 260 l 0 0 l f 0 g '
    text=b'BT /Regular 12 Tf 20 200 Td (ABCD) Tj ET '
    source=source_pdf(tmp_path,text+background if late else background+text)
    selection=make_selection(source,glyph_ids=list(range(4)),explicit_width=120)
    paragraph=inspect_paragraph(source,selection)
    element=inspect_element(source,selection)
    relations=[dict(source_id=element['paths'][0]['source_id'],relation='backgrounds',behavior='fixed-to-page')]
    edits=[dict(start=2,end=4,text='')]
    # The legacy conservative path rejects a multi-segment page background.
    with pytest.raises(PdfError):
        edit_paragraph(source,tmp_path/'legacy.pdf',paragraph,edits,max_bottom=100)
    output=tmp_path/'edited.pdf'
    if late:
        with pytest.raises(PdfError,match='fixed vector'):
            edit_paragraph(source,output,paragraph,edits,max_bottom=100,
                           element_snapshot=element,element_relations=relations)
        assert not output.exists()
    else:
        report=edit_paragraph(source,output,paragraph,edits,max_bottom=100,
                              element_snapshot=element,element_relations=relations)
        assert report['retained_glyph_count']==2
        with pymupdf.open(source) as before,pymupdf.open(output) as after:
            assert before[0].get_drawings()==after[0].get_drawings()


def test_complete_paint_group_cannot_hide_unselected_text(tmp_path):
    source=source_pdf(tmp_path,b'0.9 g 18 186 125 30 re f 0 g '
        b'BT /Regular 12 Tf 20 200 Td (AB) Tj (CD) Tj ET')
    element=inspect_element(source,make_selection(source,glyph_ids=[0,1]))
    relations=[dict(source_id=element['paths'][0]['source_id'],relation='backgrounds',behavior='fixed-to-element')]
    with pytest.raises(PdfError,match='unselected text'):
        move_element(source,tmp_path/'bad.pdf',element,relations,dx=0,dy=60)


def test_cli_element_snapshot_move_and_input_protection(tmp_path):
    from test_paragraph_cli import write
    from test_selection_cli import successful,invoke
    source,element,relations=prepared(tmp_path)
    selection_path,element_path,relations_path=[tmp_path/x for x in ('selection.json','element.json','relations.json')]
    write(selection_path,element['selection'])
    actual=successful('inspect-element',source,'--selection',selection_path,'--json',element_path)
    assert actual==element
    write(relations_path,{'relations':relations})
    output=tmp_path/'moved.pdf'
    protected=element_path.read_bytes()
    assert invoke('move-element',source,output,'--element',element_path,'--relations',relations_path,
                  '--report',element_path,'--dy','60').returncode==2
    assert element_path.read_bytes()==protected and not output.exists()
    result=successful('move-element',source,output,'--element',element_path,'--relations',relations_path,'--dy','60')
    assert result['moved_glyphs']==4 and not result['independent_renderer_verified']


def test_target_clip_is_preserved_and_crossing_it_is_refused(tmp_path):
    source=source_pdf(tmp_path,b'q 0 160 120 75 re W n BT /Regular 12 Tf 20 200 Td (AB) Tj ET Q')
    snapshot=inspect_element(source,make_selection(source,glyph_ids=[0,1]))
    move_element(source,tmp_path/'inside.pdf',snapshot,[],dx=10,dy=0)
    with pytest.raises(PdfError,match='active clip'):
        move_element(source,tmp_path/'outside.pdf',snapshot,[],dx=0,dy=60)
    assert not (tmp_path/'outside.pdf').exists()


def test_interleaved_path_state_is_not_reconstructed_from_renderer_floats(tmp_path):
    source=source_pdf(tmp_path,b'0.9 g 18 186 m 1 0 0 1 0 0 cm 143 186 l 143 216 l 18 216 l h f 0 g '
                      b'BT /Regular 12 Tf 20 200 Td (AB) Tj ET')
    snapshot=inspect_element(source,make_selection(source,glyph_ids=[0,1]))
    relations=[dict(source_id=snapshot['paths'][0]['source_id'],relation='backgrounds',behavior='fixed-to-element')]
    with pytest.raises(PdfError,match='path construction crosses'):
        move_element(source,tmp_path/'bad.pdf',snapshot,relations,dx=0,dy=60)


def test_translation_vector_is_independent_of_large_ctm_origin():
    for tx,ty in [(0,0),(564,65.27999877929688),(1e8,-1e8)]:
        assert _local_translation([.75,0,0,.75,tx,ty],0,180)==(0,240)
        assert _local_translation([0,2,-2,0,tx,ty],20,40)==(20,-10)


def test_unlinked_overlapping_paint_is_unknown_not_silently_fixed(tmp_path):
    source=source_pdf(tmp_path,b'0.9 g 18 186 125 30 re f 18 186 125 30 re f 0 g '
        b'BT /Regular 12 Tf 20 200 Td (AB) Tj ET')
    snapshot=inspect_element(source,make_selection(source,glyph_ids=[0,1]))
    assert len(snapshot['unlinked_path_paints'])==2
    assert all(p['near_selected_text'] for p in snapshot['unlinked_path_paints'])
    with pytest.raises(PdfError,match='unique source provenance'):
        move_element(source,tmp_path/'bad.pdf',snapshot,[],dx=0,dy=60)


def test_rotated_page_cannot_mix_unrotated_glyphs_with_rotated_masks(tmp_path):
    from pypdf import PdfReader,PdfWriter
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (AB) Tj ET')
    writer=PdfWriter(clone_from=PdfReader(source))
    writer.pages[0].rotate(90)
    writer.write(source)
    selection=make_selection(source,glyph_ids=[0,1])
    with pytest.raises(PdfError,match='unrotated page'):
        inspect_element(source,selection)
