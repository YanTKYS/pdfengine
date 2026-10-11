"""Whole owned paragraphs with PDF pen quantization and unequal source leading."""
from io import BytesIO
import pymupdf
import pytest
from fontTools.ttLib import TTFont
from pypdf import PdfReader,PdfWriter
from pypdf.generic import NameObject,ArrayObject,NumberObject
from pdfeditor.page_proposal import propose_page_flow,accept_page_flow,replace_in_flow
from pdfeditor.shared_flow import edit_shared_flow,open_shared_flow
from pdfeditor.selection import source_sha
import page_flow_fixture as fx

ROW='申請書を提出してください'*3
ROWS=[ROW[:30],ROW[:32],'申請期限を確認。',ROW[:30],ROW[:32],'必要書類を提出してください。']
BASES=[100,121.6,142,168.5,188.9,209.4]

@pytest.fixture(scope='module')
def paragraphs(tmp_path_factory):
    root=tmp_path_factory.mktemp('owned-paragraphs')
    font=TTFont(BytesIO(fx.build_font()));font['head'].unitsPerEm=2048
    for name in font.getGlyphOrder():font['hmtx'][name]=(1001,0)
    provider=root/'source.ttf';font.save(provider)
    raw=root/'raw.pdf';source=root/'source.pdf'
    with pymupdf.open() as doc:
        page=doc.new_page(width=250,height=430)
        page.insert_font(fontname='F1',fontbuffer=provider.read_bytes())
        page.insert_text((40,390),'FIXED FOOTER')
        stream=page.get_contents()[0];parts=[];cmap=font.getBestCmap()
        for row,(text,y) in enumerate(zip(ROWS,BASES)):
            x=50.5 if row in (0,3) else 40.
            for index,ch in enumerate(text):
                gid=font.getGlyphID(cmap[ord(ch)])
                parts.append(f'BT /F1 10.5 Tf 1 0 0 1 {x:.7f} {430-y:.7f} Tm <{gid:04x}> Tj ET')
                x+=10.5*489/1000-(.12 if index%3==2 else 0)
        doc.update_stream(stream,doc.xref_stream(stream)+'\n'.join(parts).encode());doc.save(raw)
    writer=PdfWriter(clone_from=PdfReader(raw))
    face=writer.pages[0]['/Resources']['/Font']['/F1']['/DescendantFonts'][0].get_object()
    face[NameObject('/W')]=ArrayObject([NumberObject(0),ArrayObject([NumberObject(489)]*len(font.getGlyphOrder()))])
    writer.write(source)
    p=propose_page_flow(source,font_candidates=[provider],line_ids=[f'p1-l{i}' for i in range(1,7)])
    assert p['status']=='proposed',(p['refusals'],p['unresolved'])
    assert [len(v['line_ids']) for v in p['paragraphs']]==[3,3]
    return source,p


def test_full_source_lines_reproduce_quantized_pen_and_unequal_leading(paragraphs,tmp_path):
    source,p=paragraphs
    state=accept_page_flow(source,p)
    assert p['reproduction']['lines']==6
    assert p['paragraphs'][0]['policy']['min_line_height']['value']==pytest.approx(20.4,abs=.002)
    with pymupdf.open(source) as a:
        char=next(s['chars'][0] for s in a[0].get_texttrace() if s['font']!='Helvetica')
        assert abs((char[3][2]-char[3][0])-10.5*489/1000)>.002
        original=a[0].get_pixmap().samples
    pdf,model=tmp_path/'noop.pdf',tmp_path/'noop.json'
    report=edit_shared_flow(source,state,pdf,model,{})
    assert open_shared_flow(pdf,model)['status']=='restored'
    assert all(step['report']['provided_font_glyph_count']==0 for step in report['steps'])
    with pymupdf.open(pdf) as b:assert b[0].get_pixmap().samples==original


def test_grow_moves_complete_follower_with_witnessed_gaps_then_shorten(paragraphs,tmp_path):
    original,p=paragraphs;sha=source_sha(original);state=accept_page_flow(original,p)
    follower=state['paragraphs']['P2']['logical']['text'];source=original
    longer='期限'+ROW*2
    for index,edit in enumerate([('期限',longer),(longer,'期限'),('期限',longer),None]):
        changes=replace_in_flow(state,*edit,paragraph_id='P1') if edit else {}
        pdf,model=tmp_path/f'{index}.pdf',tmp_path/f'{index}.json'
        report=edit_shared_flow(source,state,pdf,model,changes)
        opened=open_shared_flow(pdf,model);assert opened['status']=='restored'
        state=opened['state'];assert state['paragraphs']['P2']['logical']['text']==follower
        first,second=[report['plan']['fragments'][s] for s in ('slot-0','slot-1')]
        if index!=1:assert len(first['lines'])>3 and second['lines'][0]['baseline']>BASES[3]
        else:assert len(first['lines'])==3
        assert [b['baseline']-a['baseline'] for a,b in zip(second['lines'],second['lines'][1:])]==pytest.approx([20.4,20.5],abs=.002)
        assert second['lines'][0]['baseline']-first['lines'][-1]['baseline']==pytest.approx(26.5,abs=.002)
        source=pdf
    assert source_sha(original)==sha


def test_unselected_follower_and_fixed_footer_remain_protected(paragraphs,tmp_path):
    from pdfeditor.backend import PdfError
    source,p=paragraphs
    state=accept_page_flow(source,p,paragraph_ids=['P1'])
    pdf,model=tmp_path/'refused.pdf',tmp_path/'refused.json'
    with pytest.raises(PdfError,match='exceed all explicitly confirmed shared regions'):
        edit_shared_flow(source,state,pdf,model,replace_in_flow(state,'期限','期限'+ROW*2,paragraph_id='P1'))
    assert not pdf.exists() and not model.exists()
    with pytest.raises(PdfError,match='collides with foreign content'):
        accept_page_flow(source,p,overrides={'region_bottom':410})
    fonts=[s['providers']['verified'][0]['path'] for s in p['styles']]
    with pytest.raises(PdfError,match='consecutive'):
        propose_page_flow(source,font_candidates=fonts,line_ids=['p1-l1','p1-l3','p1-l4'])
