"""Focused B2 tests: approval, original style, semantics and safe publication."""
import hashlib
import json
from copy import deepcopy
import pymupdf
import pytest
from pypdf import PdfReader,PdfWriter
from pypdf.generic import NameObject,NumberObject
from provider_font import cjk_provider_bytes
from test_paragraph_frame_poc import tagged,fixture
from prototypes.paragraph_frame.auto import propose,approve,save
from prototypes.paragraph_frame.auto_detect import detect
from prototypes.paragraph_frame.auto_layout import layout
from prototypes.paragraph_frame.engine import Refused
from prototypes.paragraph_frame.audit import semantic
from pdfeditor.content_stream import ContentPage


def binding(font):return dict(path=str(font),sha256=hashlib.sha256(font.read_bytes()).hexdigest(),font_index=0,relation='substituted')


def test_selection_only_approval_reedit(fixture,tmp_path):
    source,font,_=fixture;p=propose(source,1,'本文','編集本文',substitute=binding(font))
    assert p['evidence']['manual_geometry_inputs']==0 and p['paragraphs'][0]=='本文です。'
    with pytest.raises(Refused,match='approval'):approve(source,p)
    wrong=deepcopy(p);wrong['evidence']['inferred']['right']+=100
    with pytest.raises(Refused,match='altered'):approve(source,wrong,acknowledge_inference=True)
    out=tmp_path/'out.pdf';save(source,approve(source,p,acknowledge_inference=True),out)
    source.unlink()
    p2=propose(out,1,'編集本文','本文',substitute=binding(font));save(out,approve(out,p2,acknowledge_inference=True),tmp_path/'again.pdf')


def test_scope_actualtext_and_parenttree_preserved(fixture,tmp_path):
    source,font,_=tagged(fixture,tmp_path)
    before=semantic(source,1);p=propose(source,1,'本文','文',substitute=binding(font));out=tmp_path/'out.pdf'
    save(source,approve(source,p,acknowledge_inference=True),out);after=semantic(out,1)
    assert all(after['checks'].values())
    assert [r['mcid'] for r in before['rows']]==[r['mcid'] for r in after['rows']]
    assert after['rows'][0]['actualtext']=='文です。'


def test_color_scale_spacing_and_source_witness(tmp_path):
    data=cjk_provider_bytes();source=tmp_path/'source.pdf';d=pymupdf.open();p=d.new_page(width=240,height=220)
    p.insert_font(fontname='Fixture',fontbuffer=data)
    p.insert_text((30,50),'本文です。本文です。',fontsize=11,fontname='Fixture',color=(.1,.3,.6))
    d.save(source);d.close();w=PdfWriter(clone_from=source)
    stream=w.pages[0]['/Contents'][0].get_object();stream.set_data(stream.get_data().replace(b'BT',b'BT\n90 Tz 0.2 Tc'))
    styled=tmp_path/'styled.pdf';w.write(styled)
    p=propose(styled,1,'です','本文');assert p['font_counts']['source-code-witness']==2
    out=tmp_path/'out.pdf';save(styled,approve(styled,p,acknowledge_inference=True),out)
    a=ContentPage(styled,1);b=ContentPage(out,1)
    try:
        assert {e.state.fill for e in a.events}=={e.state.fill for e in b.events if e.chars}
        assert {e.state.tz for e in b.events if e.chars}=={90}
        assert {e.state.size for e in b.events if e.chars}=={11}
        assert all(e.state.font.name=='/Fixture' for e in b.events if e.chars)
    finally:a.close();b.close()


def test_overflow_and_empty_do_not_publish(fixture,tmp_path):
    source,font,_=fixture;before=source.read_bytes();out=tmp_path/'out.pdf'
    p=propose(source,1,'本文','文章'*100,substitute=binding(font))
    with pytest.raises(Refused,match='capacity|collides'):save(source,approve(source,p,acknowledge_inference=True),out)
    assert not out.exists() and source.read_bytes()==before
    with pytest.raises(Refused,match='empty paragraph'):propose(source,1,'本文です。','')


def test_kinsoku_and_short_tail():
    text='本文（文章）、本文です。';tokens=[dict(text=c,advance=10.) for c in text]
    lines=layout(tokens,dict(right=80,left=0,first_x=0,baseline=20,line_height=15))
    assert all(l['text'][0] not in '、。）' and l['text'][-1] not in '（' for l in lines)
    assert ''.join(l['text'] for l in lines)==text


def test_missing_font_and_stale_revision(fixture,tmp_path):
    source,font,_=fixture
    with pytest.raises(Refused,match='explicit substitute'):propose(source,1,'本文','🙂')
    p=propose(source,1,'本文','文章',substitute=binding(font));source.write_bytes(source.read_bytes()+b'\n')
    with pytest.raises(Refused,match='stale'):approve(source,p,acknowledge_inference=True)


@pytest.mark.parametrize('direct_parent',[False,True])
def test_cross_scope_replacement_preserves_order_language_and_foreign(fixture,tmp_path,direct_parent):
    from test_paragraph_frame_poc import obj
    from pypdf.generic import ArrayObject,TextStringObject,DecodedStreamObject
    _,font,_=fixture;raw=tmp_path/'split.pdf';d=pymupdf.open();page=d.new_page(width=220,height=240);page.insert_font(fontname='Fixture',fontbuffer=font.read_bytes())
    parts=[('本文',30,50),('です。',52,50),('後続文章です。',30,80)]
    for text,x,y in parts:page.insert_text((x,y),text,fontsize=11,fontname='Fixture')
    d.save(raw);d.close();w=PdfWriter(clone_from=raw);page=w.pages[0];root=w._add_object(obj(Type=NameObject('/StructTreeRoot')))
    parents=[w._add_object(obj(Type=NameObject('/StructElem'),S=NameObject('/P'),P=root,Pg=page.indirect_reference,K=ArrayObject())) for _ in range(2)];leaves=[]
    for i,(stream,(text,_,_)) in enumerate(zip(page['/Contents'],parts)):
        parent=parents[int(i==2)]
        if direct_parent and i==0:leaf=parent;parent.get_object()['/K'].append(NumberObject(i))
        else:
            leaf=w._add_object(obj(Type=NameObject('/StructElem'),S=NameObject('/Span'),P=parent,Pg=page.indirect_reference,K=NumberObject(i),ActualText=TextStringObject(text),Lang=TextStringObject('ja-JP')));parent.get_object()['/K'].append(leaf)
        leaves.append(leaf);updated=DecodedStreamObject();updated.set_data(f'/Span <</MCID {i} /Lang (ja-JP)>> BDC\n'.encode()+stream.get_object().get_data()+b'\nEMC\n');page['/Contents'][i]=w._add_object(updated)
    root.get_object()[NameObject('/K')]=ArrayObject(parents);root.get_object()[NameObject('/ParentTree')]=w._add_object(obj(Nums=ArrayObject([NumberObject(0),w._add_object(ArrayObject(leaves))])))
    page[NameObject('/StructParents')]=NumberObject(0);w._root_object[NameObject('/StructTreeRoot')]=root;source=tmp_path/'tagged-split.pdf';w.write(source)
    p=propose(source,1,'文です','資料',substitute=binding(font));out=tmp_path/'out.pdf';save(source,approve(source,p,acknowledge_inference=True),out)
    a=semantic(out,1);assert all(a['checks'].values());assert [r['mcid'] for r in a['rows']]==[0,1,2]
    assert ''.join(r['drawn'] for r in a['rows'])=='本資料。後続文章です。'
    assert a['rows'][2]['actualtext']=='後続文章です。'
    r=PdfReader(out);tree=r.trailer['/Root']['/StructTreeRoot'];assert len(tree['/K'][0].get_object()['/K'])==2
    assert tree['/K'][0].get_object()['/K'][1].get_object()['/Lang']=='ja-JP'


def test_text_rise_is_explicitly_refused(fixture,tmp_path):
    source,font,_=fixture;w=PdfWriter(clone_from=source);stream=w.pages[0]['/Contents'][0].get_object();stream.set_data(stream.get_data().replace(b'BT',b'BT\n2 Ts'));raised=tmp_path/'raised.pdf';w.write(raised)
    with pytest.raises(Refused,match='text rise'):propose(raised,1,'本文','文章',substitute=binding(font))


def test_empty_scope_is_recovered_from_pdf_alone(fixture,tmp_path):
    source,font,_=tagged(fixture,tmp_path)
    # A source scope with no glyphs is a valid ordered child, as after deleting
    # a complete original run. Keep its binding and do not invent glyph IDs.
    from test_paragraph_frame_poc import obj
    from pypdf.generic import ArrayObject,TextStringObject,DecodedStreamObject
    w=PdfWriter(clone_from=source);page=w.pages[0];root=w._root_object['/StructTreeRoot'];parent=root['/K'][0];leaf=w._add_object(obj(Type=NameObject('/StructElem'),S=NameObject('/Span'),P=parent,Pg=page.indirect_reference,K=NumberObject(2),ActualText=TextStringObject('')))
    parent.get_object()['/K'].insert(0,leaf);root['/ParentTree']['/Nums'][1].get_object().append(leaf)
    stream=DecodedStreamObject();stream.set_data(b'/Span <</MCID 2>> BDC\nBT ET\nEMC\n'+page['/Contents'][0].get_object().get_data());page['/Contents'][0]=w._add_object(stream)
    edited=tmp_path/'empty-scope.pdf';w.write(edited);p=propose(edited,1,'本文','文章',substitute=binding(font));out=tmp_path/'reedit.pdf'
    save(edited,approve(edited,p,acknowledge_inference=True),out);assert all(semantic(out,1)['checks'].values())
