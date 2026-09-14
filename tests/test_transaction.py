"""Several plans, one page program mutation, one save, one verification."""
import pymupdf
import pytest

from pdfeditor.anchors import inspect_anchors
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.document_flow import confirm_document, edit_flow, open_document
from pdfeditor.elements import inspect_element
from pdfeditor.paragraph import edit_paragraph, plan_paragraph_edit
from pdfeditor.selection import inspect_selection_source, make_selection, source_sha
from pdfeditor.transaction import Transaction
from test_attributed import source_pdf


def counted_saves(monkeypatch):
    import pdfeditor.transaction as module
    calls=[]
    real=module.publish_program
    def counted(*args,**kwargs):
        calls.append(1)
        return real(*args,**kwargs)
    monkeypatch.setattr(module,'publish_program',counted)
    return calls


def two_paragraphs(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj ET BT /Regular 12 Tf 20 120 Td (NEXT) Tj ET')
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    fonts={'s0':{'path':str(font)}}
    a=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7)),explicit_width=90))
    b=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7,11)),explicit_width=90))
    return source,fonts,a,b


def test_two_paragraph_plans_save_once_and_match_the_sequential_result(tmp_path,monkeypatch):
    source,fonts,a,b=two_paragraphs(tmp_path)
    edit_a=[dict(start=0,end=7,text='ONE TWO THREE')];edit_b=[dict(start=0,end=4,text='NEXT LINE')]
    # Sequential reference: two saves, the second selection re-observed.
    first=tmp_path/'first.pdf'
    edit_paragraph(source,first,a,edit_a,fonts=fonts,max_bottom=110)
    line=next(l for l in inspect_selection_source(first)['lines'] if l['text']=='NEXT')
    b_again=inspect_paragraph(first,make_selection(first,glyph_ids=line['glyph_ids'],explicit_width=90))
    second=tmp_path/'second.pdf'
    edit_paragraph(first,second,b_again,edit_b,fonts=fonts,max_bottom=200)
    calls=counted_saves(monkeypatch)
    output=tmp_path/'together.pdf'
    with Transaction(source) as transaction:
        page=transaction.page(1)
        plan_a=plan_paragraph_edit(page,a,edit_a,fonts=fonts,max_bottom=110,owner='A')
        plan_b=plan_paragraph_edit(page,b,edit_b,fonts=fonts,max_bottom=200,owner='B')
        result=transaction.commit(output)
        try:
            reports=[plan_a.report(result),plan_b.report(result)]
            mutation_map=result.mutation_map()
        finally:
            result.close()
    assert calls==[1]
    assert [r['after'] for r in reports]==['ONE TWO THREE','NEXT LINE']
    assert sorted(m['owner'] for m in mutation_map[1]['mutations'])==['A','B']
    with pymupdf.open(second) as expected,pymupdf.open(output) as actual:
        assert all(expected[i].get_pixmap(dpi=144).samples==actual[i].get_pixmap(dpi=144).samples for i in range(len(expected)))
    assert source_sha(source)!=source_sha(output)


def test_plans_claiming_one_glyph_occurrence_are_refused_before_any_save(tmp_path,monkeypatch):
    source,fonts,a,b=two_paragraphs(tmp_path)
    overlap=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(4,11)),explicit_width=90))
    calls=counted_saves(monkeypatch)
    with pytest.raises(PdfError,match='overlap|same source glyph'):
        with Transaction(source) as transaction:
            page=transaction.page(1)
            plan_paragraph_edit(page,a,[dict(start=0,end=7,text='X')],fonts=fonts,max_bottom=110)
            plan_paragraph_edit(page,overlap,[dict(start=0,end=7,text='Y')],fonts=fonts,max_bottom=200)
            transaction.commit(tmp_path/'bad.pdf')
    assert calls==[] and not (tmp_path/'bad.pdf').exists()


def test_document_flow_moves_and_edits_in_one_save(tmp_path,monkeypatch):
    from test_document_flow import prepared
    source,model=prepared(tmp_path)
    calls=counted_saves(monkeypatch)
    out,path=tmp_path/'flow.pdf',tmp_path/'flow.json'
    report=edit_flow(source,model,out,path,'A',[dict(start=0,end=7,text='ONE TWO THREE FOUR FIVE SIX')])
    assert calls==[1] and report['moved_elements']==['B']
    assert [s['kind'] for s in report['steps']]==['move','edit']
    assert open_document(out,path)['status']=='restored'
    assert not list(tmp_path.glob('.flow-*'))


def anchored_document(tmp_path):
    source=source_pdf(tmp_path,b'0.9 g 15 140 150 80 re f 0 g '
        b'BT /Regular 12 Tf 20 240 Td (HEAD) Tj ET '
        b'BT /Regular 12 Tf 20 200 Td (ONE TWO ) Tj ET 20 198 50.4 .7 re f '
        b'BT /Regular 12 Tf 20 184 Td (THREE FOUR) Tj ET 20 182 72 .7 re f '
        b'BT /Regular 12 Tf 20 100 Td (NEXT) Tj ET')
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    fonts={'s0':{'path':str(font)}}
    head=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(4)),explicit_width=80))
    selection=make_selection(source,glyph_ids=list(range(4,22)),explicit_width=80)
    paragraph=inspect_paragraph(source,selection)
    element=inspect_element(source,selection)
    group=inspect_anchors(source,paragraph,element)['suggested_groups'][0]
    background=element['paths'][0]['source_id']
    anchors=dict(paragraph_sha256=paragraph['snapshot_sha256'],element_sha256=element['snapshot_sha256'],
        underlines=[dict(source_ids=group['source_ids'],range=group['range'],end_affinity='inside')],
        fixed_relations=[dict(source_id=background,relation='backgrounds',behavior='fixed-to-page')])
    tail=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(22,26)),explicit_width=80))
    elements={'H':dict(paragraph=head,layout=dict(width=80,min_line_height=16,max_bottom=40),fonts=fonts),
              'A':dict(paragraph=paragraph,layout=dict(width=80,min_line_height=16,max_bottom=118),fonts=fonts,anchors=anchors),
              'B':dict(paragraph=tail,layout=dict(width=80,min_line_height=16,max_bottom=230),fonts=fonts)}
    model=confirm_document(source,elements,container_id='body',bounds=[10,10,180,235],page=1,
                           follows=[dict(before='H',after='A',gap=40),dict(before='A',after='B',gap=84)])
    return source,model


def underlines(state,ident):
    binding=state['elements'][ident]['binding']
    known={p['source_id'] for p in binding['element']['paths']}
    ids=[i for u in binding['anchors']['underlines'] for i in u['source_ids']]
    assert all(i in known for i in ids)
    return ids


def test_anchored_decoration_and_follows_share_one_identity_map(tmp_path):
    source,model=anchored_document(tmp_path)
    assert len(underlines(model,'A'))==2
    step=lambda pdf,state,ident,edits,name:(tmp_path/f'{name}.pdf',tmp_path/f'{name}.json',
                                              edit_flow(pdf,state,tmp_path/f'{name}.pdf',tmp_path/f'{name}.json',ident,edits))
    # The anchored paragraph grows: its underline follows three lines and B moves down.
    pdf,path,report=step(source,model,'A',[dict(start=18,end=18,text=' FIVE')],'grow')
    state=open_document(pdf,path)['state']
    assert report['moved_elements']==['B'] and len(underlines(state,'A'))==3
    assert state['elements']['A']['binding']['anchors']['underlines'][0]['range']==[0,23]
    # An unrelated element is edited: A keeps its decoration identity through the map.
    pdf,path,report=step(pdf,state,'B',[dict(start=0,end=4,text='NEXT ONE')],'other')
    state=open_document(pdf,path)['state']
    assert not report['moved_elements'] and len(underlines(state,'A'))==3
    # A predecessor grows: the anchored element moves rigidly with its underlines.
    before=[state['elements'][i]['binding']['layout']['baseline'] for i in ('A','B')]
    pdf,path,report=step(pdf,state,'H',[dict(start=0,end=4,text='HEAD\nLINE')],'head')
    state=open_document(pdf,path)['state']
    assert report['moved_elements']==['A','B'] and len(underlines(state,'A'))==3
    assert [state['elements'][i]['binding']['layout']['baseline'] for i in ('A','B')]==pytest.approx([v+16 for v in before])
    # The anchored paragraph shrinks again and B follows upward.
    style=state['elements']['A']['binding']['paragraph']['styles'][0]['id']
    pdf,path,report=step(pdf,state,'A',[dict(start=0,end=23,text='ONE',style_id=style)],'shrink')
    state=open_document(pdf,path)['state']
    assert report['moved_elements']==['B'] and len(underlines(state,'A'))==1
    with pymupdf.open(source) as a,pymupdf.open(pdf) as b:
        assert a[1].get_pixmap().samples==b[1].get_pixmap().samples
