"""Logical decoration ranges survive edits; ownership and paint stay separate."""
import pymupdf
import pytest

from pdfeditor.anchors import inspect_anchors,project_range
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.elements import inspect_element
from pdfeditor.paragraph import edit_paragraph
from pdfeditor.paint_geometry import intersects_fill
from pdfeditor.selection import make_selection
from test_attributed import source_pdf


def test_range_projection_and_boundary_affinity():
    text='abc DEF ghi'
    assert project_range(text,[dict(start=5,end=5,text='XYZ')],4,7)==(4,10)
    assert project_range(text,[dict(start=4,end=7,text='Q')],4,7)==(4,5)
    assert project_range(text,[dict(start=4,end=7,text='')],4,7) is None
    assert project_range(text,[dict(start=0,end=3,text='z')],4,7)==(2,5)
    assert project_range(text,[dict(start=4,end=4,text='X')],4,7,start_affinity='inside')==(4,8)
    assert project_range(text,[dict(start=4,end=4,text='X')],4,7,start_affinity='outside')==(5,8)
    with pytest.raises(PdfError,match='affinity'):
        project_range(text,[dict(start=7,end=7,text='X')],4,7)
    with pytest.raises(PdfError,match='crosses an anchor boundary'):
        project_range(text,[dict(start=2,end=6,text='X')],4,7)
    with pytest.raises(PdfError,match='grapheme'):
        project_range('a\u0301b',[],1,2)


def test_curved_hole_certificate_refuses_boundary_or_unresolved_ray():
    from pdfeditor.model import Rect
    # An outer frame with one cubic corner and a reversed inner rectangle.
    geometry=[['m',0,10],['c',0,0,0,0,10,0],['l',100,0],['l',100,100],['l',0,100],['h'],
              ['m',10,10],['l',10,90],['l',90,90],['l',90,10],['h']]
    paint=dict(kind='fill-path',geometry=geometry,even_odd=False)
    assert intersects_fill(paint,Rect(20,20,80,80)) is False
    assert intersects_fill(paint,Rect(92,30,95,60)) is True
    assert intersects_fill(paint,Rect(2,2,12,12)) is None
    assert intersects_fill(paint,Rect(88,20,92,80)) is None
    assert intersects_fill(paint,Rect(float('nan'),20,80,80)) is None
    paint['geometry'][1][1]=float('inf')
    assert intersects_fill(paint,Rect(20,20,80,80)) is None


def prepared(tmp_path):
    source=source_pdf(tmp_path,b'0.9 g 15 140 150 80 re f 0 g '
        b'BT /Regular 12 Tf 20 200 Td (ONE TWO ) Tj ET 20 198 50.4 .7 re f '
        b'BT /Regular 12 Tf 20 184 Td (THREE FOUR) Tj ET 20 182 72 .7 re f '
        b'BT /Regular 12 Tf 220 200 Td (KEEP) Tj ET')
    selection=make_selection(source,glyph_ids=list(range(18)),explicit_width=80)
    paragraph=inspect_paragraph(source,selection)
    element=inspect_element(source,selection)
    candidates=inspect_anchors(source,paragraph,element)
    assert len(candidates['candidates'])==2 and len(candidates['suggested_groups'])==1
    assert all(c['status']=='inferred' and c['requires_confirmation'] for c in candidates['candidates'])
    group=candidates['suggested_groups'][0]
    spec=dict(paragraph_sha256=paragraph['snapshot_sha256'],element_sha256=element['snapshot_sha256'],
        underlines=[dict(source_ids=group['source_ids'],range=group['range'])],
        fixed_relations=[dict(source_id=element['paths'][0]['source_id'],relation='backgrounds',behavior='fixed-to-page')])
    return source,paragraph,element,spec


def test_reflow_replaces_physical_underlines_with_range_segments(tmp_path):
    source,p,e,s=prepared(tmp_path)
    font=tmp_path/'supplied.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    output,removed=tmp_path/'edited.pdf',tmp_path/'removed.pdf'
    report=edit_paragraph(source,output,p,[dict(start=4,end=7,text='FIVE SEVEN')],
        fonts={'s0':{'path':str(font)}},element_snapshot=e,anchor_spec=s,max_bottom=115,removal_output=removed)
    assert report['new_line_count']==3
    assert len(report['anchors']['segments'])==3
    assert report['anchors']['groups'][0]['output_range']==(0,25)
    with pymupdf.open(source) as before,pymupdf.open(output) as after,pymupdf.open(removed) as deleted:
        assert before[0].get_drawings()[0]==after[0].get_drawings()[0]
        assert len(deleted[0].get_drawings())==1
        assert deleted[0].get_text().strip()=='KEEP'
        assert before[1].get_pixmap().samples==after[1].get_pixmap().samples
    # Reopen the edited PDF and recover source IDs for all three new segments.
    selection=make_selection(output,glyph_ids=list(range(len(report['glyph_plan']))),explicit_width=80)
    p2=inspect_paragraph(output,selection)
    e2=inspect_element(output,selection)
    c2=inspect_anchors(output,p2,e2)
    assert len(c2['candidates'])==3
    group=c2['suggested_groups'][0]
    s2=dict(paragraph_sha256=p2['snapshot_sha256'],element_sha256=e2['snapshot_sha256'],
        underlines=[dict(source_ids=group['source_ids'],range=group['range'])],
        fixed_relations=[dict(source_id=e2['paths'][0]['source_id'],relation='backgrounds',behavior='fixed-to-page')])
    start=p2['text'].index('FIVE')
    second=edit_paragraph(output,tmp_path/'second.pdf',p2,[dict(start=start,end=start+4,text='')],
        element_snapshot=e2,anchor_spec=s2,max_bottom=115)
    assert second['provided_font_glyph_count']==0 and second['anchors']['source_paths_replaced']==3


@pytest.mark.parametrize('mode',['missing','tampered','crossing','overflow'])
def test_anchor_failures_do_not_publish_output(tmp_path,mode):
    source,p,e,s=prepared(tmp_path)
    font=tmp_path/'supplied.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    edits=[];bottom=115
    if mode=='missing':s['underlines']=[]
    if mode=='tampered':s['paragraph_sha256']='wrong'
    if mode=='crossing':s['underlines'][0]['range']=[4,7];edits=[dict(start=2,end=6,text='X')]
    if mode=='overflow':edits=[dict(start=4,end=7,text='FIVE SEVEN EIGHT NINE TEN ELEVEN TWELVE')];bottom=90
    output=tmp_path/'bad.pdf'
    with pytest.raises((PdfError,ValueError)):
        edit_paragraph(source,output,p,edits,fonts={'s0':{'path':str(font)}},element_snapshot=e,anchor_spec=s,max_bottom=bottom)
    assert not output.exists()


def test_cli_anchor_inputs_and_report_protection(tmp_path):
    from test_paragraph_cli import write
    from test_selection_cli import successful,invoke
    source,p,e,s=prepared(tmp_path)
    paragraph,element,spec,edits=[tmp_path/n for n in ('p.json','e.json','s.json','edits.json')]
    for path,value in [(paragraph,p),(element,e),(spec,s),(edits,{'edits':[dict(start=4,end=7,text='')]})]:write(path,value)
    result=successful('inspect-anchors',source,'--paragraph',paragraph,'--element',element,'--json',tmp_path/'candidates.json')
    assert len(result['suggested_groups'])==1
    output=tmp_path/'edited.pdf'
    assert invoke('edit-paragraph',source,output,'--paragraph',paragraph,'--element',element,'--anchors',spec,
                  '--edits',edits,'--report',spec).returncode==2
    assert not output.exists()
    report=successful('edit-paragraph',source,output,'--paragraph',paragraph,'--element',element,'--anchors',spec,'--edits',edits)
    assert report['anchors']['source_paths_replaced']==2


@pytest.mark.parametrize('scope,reason',[
    (b'q 10 180 45 40 re W n %s Q', 'active clip'),
    (b'/Artifact BMC %s EMC', 'marked content'),
])
def test_decoration_scope_is_checked_independently_of_text(tmp_path,scope,reason):
    # Text is outside the decoration's scope. A text-only guard cannot protect
    # the underline's narrower clip or marked-content membership.
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ONE) Tj ET '+
                      scope % b'20 198 21.6 .7 re f')
    selection=make_selection(source,glyph_ids=[0,1,2],explicit_width=100)
    p=inspect_paragraph(source,selection);e=inspect_element(source,selection)
    c=inspect_anchors(source,p,e)['suggested_groups'][0]
    spec=dict(paragraph_sha256=p['snapshot_sha256'],element_sha256=e['snapshot_sha256'],
              underlines=[dict(source_ids=c['source_ids'],range=c['range'])])
    font=tmp_path/'supplied.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    output=tmp_path/'edited.pdf'
    with pytest.raises(PdfError,match=reason):
        edit_paragraph(source,output,p,[dict(start=0,end=3,text='ELEVEN')],fonts={'s0':{'path':str(font)}},
                       element_snapshot=e,anchor_spec=spec,max_bottom=100)
    assert not output.exists()


def test_underline_must_fit_even_when_text_fits_confirmed_bottom(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ONE) Tj ET 20 194.3 21.6 2.3 re f')
    selection=make_selection(source,glyph_ids=[0,1,2],explicit_width=100)
    p=inspect_paragraph(source,selection);e=inspect_element(source,selection)
    c=inspect_anchors(source,p,e)['suggested_groups'][0]
    spec=dict(paragraph_sha256=p['snapshot_sha256'],element_sha256=e['snapshot_sha256'],
              underlines=[dict(source_ids=c['source_ids'],range=c['range'])])
    output=tmp_path/'edited.pdf'
    with pytest.raises(PdfError,match='underline extends beyond the confirmed paragraph region'):
        edit_paragraph(source,output,p,[],element_snapshot=e,anchor_spec=spec,max_bottom=64.8)
    assert not output.exists()
