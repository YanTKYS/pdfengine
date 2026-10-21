"""Focused safety/continuation tests for the independent frame experiment."""
import json
from pathlib import Path
import pymupdf
from pypdf import PdfReader,PdfWriter
from pypdf.generic import (ArrayObject,BooleanObject,DecodedStreamObject,DictionaryObject,
                          NameObject,NumberObject,TextStringObject)
import pytest
from provider_font import cjk_provider_bytes
from prototypes.paragraph_frame.engine import Refused,observe,rewrite,wrap
from pdfeditor.shaped_font import ShapedFont


def obj(**kwargs):return DictionaryObject({NameObject('/'+k):v for k,v in kwargs.items()})


@pytest.fixture
def fixture(tmp_path):
    data=cjk_provider_bytes();font=tmp_path/'font.ttf';font.write_bytes(data)
    source=tmp_path/'source.pdf'
    d=pymupdf.open();p=d.new_page(width=220,height=240)
    p.insert_font(fontname='Fixture',fontbuffer=data)
    p.insert_text((30,50),'本文です。',fontsize=11,fontname='Fixture')
    p.insert_text((30,80),'後続文章です。',fontsize=11,fontname='Fixture')
    d.save(source);d.close()
    spec=dict(page=1,frame=[19,35,202,190],left=20,right=200,first_x=30,baseline=50,line_height=15,paragraph_gap=30)
    return source,font,spec


def tagged(fixture,tmp_path,mutate=None):
    source,font,spec=fixture;r=PdfReader(source);w=PdfWriter(clone_from=r);p=w.pages[0]
    root=w._add_object(obj(Type=NameObject('/StructTreeRoot')))
    children=[];leaves=[]
    contents=p['/Contents']
    for i,(stream,text) in enumerate(zip(contents,['本文です。','後続文章です。'])):
        parent=w._add_object(obj(Type=NameObject('/StructElem'),S=NameObject('/P'),P=root,Pg=p.indirect_reference))
        leaf=w._add_object(obj(Type=NameObject('/StructElem'),S=NameObject('/Span'),P=parent,Pg=p.indirect_reference,K=NumberObject(i),ActualText=TextStringObject(text)))
        parent.get_object()[NameObject('/K')]=ArrayObject([leaf]);children.append(parent);leaves.append(leaf)
        updated=DecodedStreamObject();updated.set_data(f'/Span <</MCID {i}>> BDC\n'.encode()+stream.get_object().get_data()+b'\nEMC\n')
        contents[i]=w._add_object(updated)
    root.get_object()[NameObject('/K')]=ArrayObject(children)
    root.get_object()[NameObject('/ParentTree')]=w._add_object(obj(Nums=ArrayObject([NumberObject(0),w._add_object(ArrayObject(leaves))])))
    p[NameObject('/StructParents')]=NumberObject(0);w._root_object[NameObject('/StructTreeRoot')]=root
    w._root_object[NameObject('/MarkInfo')]=obj(Marked=BooleanObject(True))
    if mutate:mutate(w,children,leaves)
    path=tmp_path/'tagged.pdf';w.write(path)
    return path,font,spec


def test_logical_rebuild_and_pdf_alone_reedit(fixture,tmp_path):
    source,font,spec=fixture;first=tmp_path/'first.pdf'
    state=rewrite(source,first,spec,'本文','変更した本文',font)
    assert state['paragraphs']==['変更した本文です。','後続文章です。']
    before=source.read_bytes();source.unlink()  # PDF-alone must not read the original.
    second=tmp_path/'second.pdf';again=rewrite(first,second,spec,'変更した','再編集した',font)
    assert again['paragraphs'][0]=='再編集した本文です。'
    assert before!=first.read_bytes()


def test_tagged_rebuild_semantic_parent_preserved(fixture,tmp_path):
    source,font,spec=tagged(fixture,tmp_path);out=tmp_path/'out.pdf'
    rewrite(source,out,spec,'本文','編集した文章',font)
    c,paras=observe(out,spec);c.close()
    assert [p.text for p in paras]==['編集した文章です。','後続文章です。']
    r=PdfReader(out);root=r.trailer['/Root']['/StructTreeRoot']
    assert str(root['/K'][0].get_object()['/K'][0].get_object()['/ActualText'])=='編集した文章です。'


@pytest.mark.parametrize('kind',['actualtext','duplicate-child','language','external-reference'])
def test_unsafe_semantics_refused_before_publication(fixture,tmp_path,kind):
    def mutate(w,parents,leaves):
        if kind=='actualtext':leaves[0].get_object()[NameObject('/ActualText')]=TextStringObject('別の意味')
        elif kind=='duplicate-child':parents[0].get_object()['/K'].append(leaves[0])
        elif kind=='language':leaves[0].get_object()[NameObject('/Lang')]=TextStringObject('en-US')
        else:w._root_object[NameObject('/UnexpectedReference')]=leaves[0]
    source,font,spec=tagged(fixture,tmp_path,mutate);out=tmp_path/'out.pdf'
    with pytest.raises(Refused):rewrite(source,out,spec,'本文','変更',font)
    assert not out.exists()


def test_capacity_does_not_publish_or_change_input(fixture,tmp_path):
    source,font,spec=fixture;before=source.read_bytes();out=tmp_path/'out.pdf'
    with pytest.raises(Refused,match='capacity'):rewrite(source,out,spec,'本文','長い文章'*100,font)
    assert source.read_bytes()==before and not out.exists()


def test_stale_sidecar_refused(fixture,tmp_path):
    source,font,spec=fixture;side=tmp_path/'state.json';side.write_text(json.dumps(dict(pdf_sha256='wrong',paragraphs=[],spec=spec)))
    with pytest.raises(Refused,match='stale'):rewrite(source,tmp_path/'out.pdf',spec,'本文','変更',font,sidecar=side)


def test_japanese_kinsoku(fixture):
    _,path,spec=fixture;font=ShapedFont(path)
    narrow=dict(spec,right=80)
    lines=wrap('本文（文章）、本文です。',[11]*len('本文（文章）、本文です。'),font,narrow,50)
    assert all(l['text'][0] not in '、。）' and l['text'][-1] not in '（' for l in lines)


def test_cross_owner_rebuild_keeps_foreign_semantic_sibling(fixture,tmp_path):
    _,font,spec=fixture
    d=pymupdf.open();page=d.new_page(width=220,height=240)
    page.insert_font(fontname='Fixture',fontbuffer=font.read_bytes())
    parts=[('見出し',30,20),('本文',30,50),('です。',52,50),('後続文章です。',30,80)]
    for text,x,y in parts:page.insert_text((x,y),text,fontsize=11,fontname='Fixture')
    raw=tmp_path/'split-source.pdf';d.save(raw);d.close();w=PdfWriter(clone_from=raw);page=w.pages[0]
    root=w._add_object(obj(Type=NameObject('/StructTreeRoot')))
    parents=[w._add_object(obj(Type=NameObject('/StructElem'),S=NameObject('/P'),P=root,Pg=page.indirect_reference,K=ArrayObject())) for _ in range(3)]
    leaves=[]
    for i,(stream,(text,_,_)) in enumerate(zip(page['/Contents'],parts)):
        parent=parents[0 if i==0 else 1 if i<3 else 2]
        leaf=w._add_object(obj(Type=NameObject('/StructElem'),S=NameObject('/Span'),P=parent,Pg=page.indirect_reference,K=NumberObject(i),ActualText=TextStringObject(text)))
        parent.get_object()['/K'].append(leaf);leaves.append(leaf)
        updated=DecodedStreamObject();updated.set_data(f'/Span <</MCID {i}>> BDC\n'.encode()+stream.get_object().get_data()+b'\nEMC\n');page['/Contents'][i]=w._add_object(updated)
    root.get_object()[NameObject('/K')]=ArrayObject(parents)
    root.get_object()[NameObject('/ParentTree')]=w._add_object(obj(Nums=ArrayObject([NumberObject(0),w._add_object(ArrayObject(leaves))])))
    page[NameObject('/StructParents')]=NumberObject(0);w._root_object[NameObject('/StructTreeRoot')]=root
    source=tmp_path/'split-tagged.pdf';w.write(source);out=tmp_path/'split-edited.pdf'
    rewrite(source,out,spec,'文です','文を編集しました',font)
    r=PdfReader(out);tree=r.trailer['/Root']['/StructTreeRoot'];p=tree['/K'][1].get_object();array=tree['/ParentTree']['/Nums'][1].get_object()
    assert len(p['/K'])==1 and p['/K'][0].get_object()['/ActualText']=='本文を編集しました。'
    assert type(array[2].get_object()).__name__=='NullObject'
    assert array[1].idnum==p['/K'][0].idnum
    assert tree['/K'][0].get_object()['/K'][0].get_object()['/ActualText']=='見出し'
    assert array[0].idnum==tree['/K'][0].get_object()['/K'][0].idnum
    with pymupdf.open(source) as a,pymupdf.open(out) as b:
        assert a[0].get_textbox(pymupdf.Rect(0,0,220,30))==b[0].get_textbox(pymupdf.Rect(0,0,220,30))
        assert a[0].get_pixmap(clip=pymupdf.Rect(0,0,220,30)).samples==b[0].get_pixmap(clip=pymupdf.Rect(0,0,220,30)).samples


def test_marked_actualtext_is_not_silently_erased(fixture,tmp_path):
    source,font,spec=tagged(fixture,tmp_path)
    writer=PdfWriter(clone_from=source);stream=writer.pages[0]['/Contents'][0].get_object()
    stream.set_data(stream.get_data().replace(b'/MCID 0',b'/ActualText (different meaning) /MCID 0'))
    path=tmp_path/'marked-actualtext.pdf';writer.write(path)
    with pytest.raises(Refused,match='property semantics'):
        rewrite(path,tmp_path/'out.pdf',spec,'本文','変更',font)


def test_existing_output_is_not_overwritten(fixture,tmp_path):
    source,font,spec=fixture;out=tmp_path/'out.pdf';out.write_bytes(b'previous artifact')
    with pytest.raises(Refused,match='already exists'):rewrite(source,out,spec,'本文','変更',font)
    assert out.read_bytes()==b'previous artifact'
