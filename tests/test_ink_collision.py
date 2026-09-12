"""Outline certificates must prove new placements, never infer ownership."""
from io import BytesIO
from types import SimpleNamespace

import pymupdf
import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from pdfeditor.backend import PdfError
from pdfeditor.composition import _observations
from pdfeditor.elements import inspect_element, move_element
from pdfeditor.ink_collision import _bitmap_bounds, certify_text_translation, source_ink, POLICY
from pdfeditor.model import Rect
from pdfeditor.selection import make_selection


@pytest.fixture(scope='module')
def font_bytes():
    builder=FontBuilder(1000,isTTF=True)
    names=['.notdef','H','p','space']
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({72:'H',112:'p',32:'space'})
    glyphs={}
    for name in names:
        pen=TTGlyphPen(None)
        if name!='space':
            # Font metrics deliberately exceed most glyph ink; p descends.
            bottom=-200 if name=='p' else 0
            pen.moveTo((0,bottom));pen.lineTo((500,bottom))
            pen.lineTo((500,700));pen.lineTo((0,700));pen.closePath()
        glyphs[name]=pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name:(600,0) for name in names})
    builder.setupHorizontalHeader(ascent=1000,descent=-300)
    builder.setupNameTable({'familyName':'GeometryProof','styleName':'Regular',
                           'uniqueFontIdentifier':'GeometryProof','fullName':'GeometryProof',
                           'psName':'GeometryProof'})
    builder.setupOS2(sTypoAscender=1000,sTypoDescender=-300,usWinAscent=1000,usWinDescent=300)
    builder.setupPost();builder.setupMaxp()
    out=BytesIO();builder.save(out);return out.getvalue()


def embedded_source(tmp_path,font_bytes,*,first='HH',second='HH',mode=0,opacity=1,clip=False,duplicate=False):
    doc=pymupdf.open();page=doc.new_page(width=300,height=240)
    page.insert_font(fontname='PE',fontbuffer=font_bytes)
    page.insert_text((30,80),first,fontname='PE',fontsize=12,render_mode=mode,fill_opacity=opacity)
    if duplicate:page.insert_text((30,80),first,fontname='PE',fontsize=12)
    page.insert_text((30,110),second,fontname='PE',fontsize=12)
    if clip:
        # Crop the lower row by a clip shared by both source runs. Its
        # unclipped outline still determines collision; it cannot vanish.
        page.clean_contents();xref=page.get_contents()[0]
        doc.update_stream(xref,b'q 0 120 300 120 re W n '+doc.xref_stream(xref)+b' Q')
    doc.new_page(width=300,height=240)
    source=tmp_path/'source.pdf';doc.save(source);doc.close();return source


def test_bbox_overlap_can_be_proven_disjoint_before_mutation(tmp_path,font_bytes):
    source=embedded_source(tmp_path,font_bytes)
    selection=make_selection(source,glyph_ids=[2,3]);snapshot=inspect_element(source,selection)
    with pymupdf.open(source) as doc:
        before=_observations(doc[0]);a=Rect(*before[0]['bbox']);b=Rect(*before[2]['bbox'])
    moved=Rect(b.x0,b.y0-20,b.x1,b.y1-20)
    assert a.intersects(moved)
    proof=certify_text_translation(source,1,{2,3},0,-20)
    assert proof['status']=='disjoint' and proof['minimum_axis_clearance_pt']>0
    output=tmp_path/'moved.pdf'
    report=move_element(source,output,snapshot,[],dx=0,dy=-20)
    assert report['text_collision']['policy']==POLICY
    with pymupdf.open(output) as doc:
        after=_observations(doc[0]);assert before[:2]==after[:2]
        assert after[2]['origin'][1]==pytest.approx(before[2]['origin'][1]-20)


@pytest.mark.parametrize('dy',[-30,-22,-21])
def test_real_contact_and_clearance_failure_publish_nothing(tmp_path,font_bytes,dy):
    source=embedded_source(tmp_path,font_bytes)
    assert certify_text_translation(source,1,{2,3},0,dy)['status']=='not_certified'
    snap=inspect_element(source,make_selection(source,glyph_ids=[2,3]))
    with pytest.raises(PdfError,match='expanded outline envelopes'):
        move_element(source,tmp_path/'bad.pdf',snap,[],dx=0,dy=dy)
    assert not (tmp_path/'bad.pdf').exists()


@pytest.mark.parametrize('options',[{'mode':1},{'mode':2},{'opacity':.5},{'opacity':0},{'duplicate':True}])
def test_stroke_transparency_and_ambiguous_correspondence_remain_unknown(tmp_path,font_bytes,options):
    source=embedded_source(tmp_path,font_bytes,**options)
    with pymupdf.open(source) as doc:n=len(_observations(doc[0]))
    proof=certify_text_translation(source,1,{n-2,n-1},0,-20)
    assert proof['status']=='unknown'


def test_clip_does_not_shrink_the_outline_or_authorize_clip_crossing(tmp_path,font_bytes):
    source=embedded_source(tmp_path,font_bytes,clip=True)
    assert certify_text_translation(source,1,{2,3},0,-30)['status']=='not_certified'
    snap=inspect_element(source,make_selection(source,glyph_ids=[2,3]))
    with pytest.raises(PdfError,match='active clip'):
        move_element(source,tmp_path/'outside.pdf',snap,[],dx=0,dy=20)


def test_empty_outline_is_proven_by_font_not_unicode(tmp_path,font_bytes):
    source=embedded_source(tmp_path,font_bytes,first='  ')
    proof=certify_text_translation(source,1,{2,3},0,-30)
    assert proof['status']=='disjoint' and proof['compared_pairs']==0
    # A whitespace code mapped to H still has ink.
    from fontTools.ttLib import TTFont
    font=TTFont(BytesIO(font_bytes))
    for table in font['cmap'].tables:
        if table.isUnicode():table.cmap[32]='H'
    out=BytesIO();font.save(out)
    source=embedded_source(tmp_path,out.getvalue(),first='  ')
    assert certify_text_translation(source,1,{2,3},0,-30)['status']=='not_certified'


def test_unknown_outline_cannot_override_bbox_guard(tmp_path,font_bytes,monkeypatch):
    source=embedded_source(tmp_path,font_bytes)
    snap=inspect_element(source,make_selection(source,glyph_ids=[2,3]))
    monkeypatch.setattr(pymupdf.mupdf,'fz_outline_glyph',lambda *a:None)
    with pytest.raises(PdfError,match='did not provide'):
        move_element(source,tmp_path/'bad.pdf',snap,[],dx=0,dy=-20)
    assert not (tmp_path/'bad.pdf').exists()


def test_all_bitmap_strikes_expand_geometry_without_using_pixels():
    bitmap_type=type('ebdt_bitmap_format_1',(),{})
    bitmap=bitmap_type();bitmap.metrics=SimpleNamespace(BearingX=-2,BearingY=9,width=8,height=12)
    strike=SimpleNamespace(bitmapSizeTable=SimpleNamespace(bitDepth=1,flags=1,ppemX=10,ppemY=10))
    font={'EBLC':SimpleNamespace(strikes=[strike]),'EBDT':SimpleNamespace(strikeData=[{'H':bitmap}])}
    bounds=_bitmap_bounds(font,'H',[10,0,0,-10,30,80])
    assert bounds==Rect(28,71,36,83)
    strike.bitmapSizeTable.bitDepth=8
    with pytest.raises(ValueError,match='unsupported embedded bitmap'):_bitmap_bounds(font,'H',[10,0,0,-10,30,80])


def test_geometry_follows_full_transform(tmp_path,font_bytes):
    source=embedded_source(tmp_path,font_bytes)
    with pymupdf.open(source) as doc:
        page=doc[0];page.clean_contents();xref=page.get_contents()[0]
        doc.update_stream(xref,b'q 1 .1 .2 1 3 4 cm '+doc.xref_stream(xref)+b' Q')
        transformed=tmp_path/'transformed.pdf';doc.save(transformed)
    original=source_ink(source);changed=source_ink(transformed)
    assert changed['status']=='proven'
    def first(data):return next(v for v in data['ink'].values() if v['bounds'])['bounds']
    assert first(original)==pytest.approx([30,71.6,36,80],abs=.01)
    # PDF-space shear/translation composed with the page's y inversion.
    assert first(changed)==pytest.approx([65,64,72.68,73],abs=.01)
    proof=certify_text_translation(transformed,1,{2,3},0,-30)
    assert proof['status']=='not_certified'


@pytest.mark.parametrize('owned',[False,True])
def test_text_certificate_does_not_authorize_vector_collision_or_ownership(tmp_path,font_bytes,owned):
    source=embedded_source(tmp_path,font_bytes)
    with pymupdf.open(source) as doc:
        rect=pymupdf.Rect(28,99,46,114) if owned else pymupdf.Rect(28,89,46,90)
        doc[0].draw_rect(rect,color=None,fill=(.9,.9,0),overlay=False)
        painted=tmp_path/'painted.pdf';doc.save(painted)
    assert certify_text_translation(painted,1,{2,3},0,-20)['status']=='disjoint'
    snap=inspect_element(painted,make_selection(painted,glyph_ids=[2,3]))
    relations=[dict(source_id=snap['paths'][0]['source_id'],relation='backgrounds',behavior='fixed-to-element')] if owned else []
    with pytest.raises(PdfError,match='unselected glyph' if owned else 'fixed vector'):
        move_element(painted,tmp_path/'bad.pdf',snap,relations,dx=0,dy=-20)
    assert not (tmp_path/'bad.pdf').exists()


def test_unembedded_font_cannot_supply_a_new_permission(tmp_path):
    from test_attributed import source_pdf
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (HH) Tj 0 -30 Td (HH) Tj ET')
    proof=certify_text_translation(source,1,{2,3},0,-20)
    assert proof['status']=='unknown'


def test_certificate_must_bind_the_source_of_the_mutation(tmp_path,font_bytes,monkeypatch):
    source=embedded_source(tmp_path,font_bytes)
    snap=inspect_element(source,make_selection(source,glyph_ids=[2,3]))
    monkeypatch.setattr('pdfeditor.ink_collision.certify_text_translation',
                        lambda *a:dict(status='disjoint',source_sha256='different revision'))
    with pytest.raises(PdfError,match='different PDF revision'):
        move_element(source,tmp_path/'bad.pdf',snap,[],dx=0,dy=-20)
    assert not (tmp_path/'bad.pdf').exists()
