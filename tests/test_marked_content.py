from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject

from pdfeditor.marked_content import observe_marked_content
from pdfeditor.elements import inspect_element,move_element
from pdfeditor.selection import make_selection
from pdfeditor.backend import PdfError
import pytest
from test_attributed import source_pdf


def test_marked_scope_is_independent_of_graphics_stack_and_orphan_is_not_owner(tmp_path):
    p=source_pdf(tmp_path,b'q /P << /MCID 0 >> BDC Q BT /Regular 12 Tf 20 200 Td (A) Tj ET EMC')
    r=observe_marked_content(p)
    assert r['complete']
    scope=r['scopes'][0]
    text=next(o for o in r['operators'] if o['operator']=='Tj')
    assert text['active_scope_ids']==[scope['id']]
    assert scope['association']['status']=='orphan_mcid'


def test_named_properties_and_unmatched_emc_are_observed(tmp_path):
    p=source_pdf(tmp_path,b'/P /Prop BDC EMC EMC')
    writer=PdfWriter(clone_from=PdfReader(p))
    writer.pages[0]['/Resources'][NameObject('/Properties')]=DictionaryObject({
        NameObject('/Prop'):DictionaryObject({NameObject('/MCID'):NumberObject(2)})})
    writer.write(p)
    r=observe_marked_content(p)
    assert r['scopes'][0]['properties']['/MCID']==2
    assert not r['complete'] and 'EMC' in r['diagnostics'][0]['reason']


def test_parenttree_requires_matching_structuretree_backlink(tmp_path):
    p=source_pdf(tmp_path,b'/P << /MCID 0 >> BDC BT /Regular 12 Tf 20 200 Td (A) Tj ET EMC')
    writer=PdfWriter(clone_from=PdfReader(p))
    root=DictionaryObject({NameObject('/Type'):NameObject('/StructTreeRoot')})
    root_ref=writer._add_object(root)
    owner=DictionaryObject({NameObject('/Type'):NameObject('/StructElem'),NameObject('/S'):NameObject('/P'),
        NameObject('/P'):root_ref,NameObject('/Pg'):writer.pages[0].indirect_reference,NameObject('/K'):NumberObject(0)})
    owner_ref=writer._add_object(owner)
    root[NameObject('/K')]=ArrayObject([owner_ref])
    root[NameObject('/ParentTree')]=DictionaryObject({NameObject('/Nums'):ArrayObject([
        NumberObject(0),ArrayObject([owner_ref])])})
    writer.pages[0][NameObject('/StructParents')]=NumberObject(0)
    writer._root_object[NameObject('/StructTreeRoot')]=root_ref
    writer.write(p)
    assert observe_marked_content(p)['scopes'][0]['association']['status']=='tree_backed'
    snapshot=inspect_element(p,make_selection(p,glyph_ids=[0]))
    with pytest.raises(PdfError,match='structure-layout updates'):
        move_element(p,tmp_path/'moved.pdf',snapshot,[],dx=0,dy=30)
    assert not (tmp_path/'moved.pdf').exists()
    owner[NameObject('/K')]=NumberObject(1);writer.write(p)
    a=observe_marked_content(p)['scopes'][0]['association']
    assert a['status']=='unknown' and not a['backlink_verified']
