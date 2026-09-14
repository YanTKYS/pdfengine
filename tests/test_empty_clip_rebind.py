"""A dormant insertion keeps its actual clip across page object renumbering."""
import json

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import TextStringObject

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.document_flow import (
    _rebind_clip_locations, _reseal, rebind_entry,
    confirm_document, edit_flow, open_document,
)
from pdfeditor.elements import _close
from pdfeditor.mutation import IdentityMap
from pdfeditor.selection import make_selection, source_sha
from test_attributed import source_pdf


def rewrite(source, output, program):
    writer=PdfWriter()
    # Keep an unrelated object before cloning to force different page xrefs.
    writer._add_object(TextStringObject('unrelated object'))
    writer.append(PdfReader(source))
    writer.pages[0]['/Contents'].get_object().set_data(program)
    writer.write(output)


@pytest.mark.parametrize('mutation',['unchanged','path','rule','relocated','wrong_edits','consumed','foreign_stream'])
def test_clip_source_mapping_retains_geometry_scope_and_operator_provenance(tmp_path,mutation):
    program=b'q 0 0 320 260 re W n BT /Regular 12 Tf 20 200 Td [] TJ ET Q'
    source=source_pdf(tmp_path,program);out=tmp_path/'saved.pdf'
    prefix=b'q Q ';new=prefix+program
    edits=[dict(start=0,end=0,length=len(prefix))]
    if mutation=='path':new=new.replace(b'320 260',b'319 260')
    if mutation=='rule':
        new=new.replace(b' W ',b' W* ')
        start=program.index(b'W n')
        edits.append(dict(start=start,end=start+1,length=2))
    if mutation=='relocated':new=new.replace(b' W n ',b' W n q Q 0 0 320 260 re W n ')
    if mutation=='wrong_edits':edits=[]
    rewrite(source,out,new)
    a,b=ContentPage(source,1),ContentPage(out,1)
    try:
        assert a.page.xref!=b.page.xref
        expected=json.loads(json.dumps(a.events[0].report()))
        actual=json.loads(json.dumps(b.events[0].report()))
        if mutation=='consumed':
            start=program.index(b'n BT')
            edits.append(dict(start=start,end=start+1,length=1))
        if mutation=='foreign_stream':expected['state']['clip'][0]['at'][0]=99
        if mutation in ('wrong_edits','consumed','foreign_stream'):
            with pytest.raises(PdfError):_rebind_clip_locations(expected,a,b,edits)
        else:
            _rebind_clip_locations(expected,a,b,edits)
            assert _close(expected['state']['clip'],actual['state']['clip'])==(mutation=='unchanged')
    finally:a.close();b.close()


def test_empty_paragraph_rebinds_clip_then_retypes_after_renumbered_save(tmp_path):
    source=source_pdf(tmp_path,b'q 0 0 320 260 re W* n BT /Regular 12 Tf 20 200 Td (OLD) Tj ET Q')
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    p=inspect_paragraph(source,make_selection(source,glyph_ids=[0,1,2],explicit_width=90))
    model=confirm_document(source,{'A':dict(paragraph=p,layout=dict(width=90,max_bottom=140,min_line_height=20),
        fonts={'s0':dict(path=str(font))})},container_id='body',bounds=[10,40,120,140],page=1,follows=[])
    empty,sidecar=tmp_path/'empty.pdf',tmp_path/'empty.json'
    edit_flow(source,model,empty,sidecar,'A',[dict(start=0,end=3,text='')])
    state=open_document(empty,sidecar)['state']
    content=ContentPage(empty,1)
    try:program=content.streams[-content.page.xref]
    finally:content.close()
    out=tmp_path/'renumbered.pdf';rewrite(empty,out,b'q Q '+program)
    with pymupdf.open(empty) as a,pymupdf.open(out) as b:
        assert a[0].xref!=b[0].xref
        assert all(a[i].get_pixmap().samples==b[i].get_pixmap().samples for i in range(len(a)))
    edits=[dict(start=0,end=0,length=4)]
    def rebind(after):
        identity=IdentityMap.from_edits(empty,after,1,edits)
        try:return rebind_entry(identity,state['elements']['A'])
        finally:identity.close()
    rebound=rebind(out)
    bad=tmp_path/'changed-clip.pdf'
    rewrite(empty,bad,b'q Q '+program.replace(b'320 260',b'319 260'))
    with pytest.raises(PdfError,match='graphics state changed|do not reproduce'):
        rebind(bad)
    old_clip=state['elements']['A']['binding']['paragraph']['insertion_binding']['event']['state']['clip']
    new_clip=rebound['binding']['paragraph']['insertion_binding']['event']['state']['clip']
    assert old_clip[0]['at']!=new_clip[0]['at'] and old_clip[0]['path']==new_clip[0]['path']
    state['elements']['A']=rebound;state['pdf_sha256']=source_sha(out);state=_reseal(state)
    assert open_document(out,state)['status']=='restored'
    final,final_model=tmp_path/'retyped.pdf',tmp_path/'retyped.json'
    edit_flow(out,state,final,final_model,'A',[dict(start=0,end=0,text='NEW')])
    assert open_document(final,final_model)['state']['elements']['A']['binding']['paragraph']['text']=='NEW'
    assert PdfReader(final).pages[0].extract_text().strip()=='NEW'
