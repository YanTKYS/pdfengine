"""Repeated edits preserve caller meaning; stale bindings never authorize edits."""
import json

import pymupdf
import pytest
from pypdf import PdfReader,PdfWriter

from pdfeditor.attributed import digest,inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.editable import write_editable,edit_document,open_editable,_seal
from pdfeditor.selection import make_selection
from test_anchors import prepared
from test_attributed import source_pdf


def first(tmp_path):
    source,p,e,a=prepared(tmp_path)
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    pdf,state=tmp_path/'first.pdf',tmp_path/'first.json'
    report=write_editable(source,pdf,state,p,[dict(start=4,end=7,text='FIVE SEVEN')],
        fonts={'s0':{'path':str(font)}},element_snapshot=e,anchor_spec=a,max_bottom=115)
    return source,pdf,state,report


def test_logical_spaces_ranges_and_limits_survive_repeated_edits(tmp_path):
    source,pdf,state,report=first(tmp_path)
    restored=open_editable(pdf,state)
    assert restored['status']=='restored'
    saved=restored['state'];p=saved['paragraph']
    assert p['text']=='ONE FIVE SEVEN THREE FOUR'
    assert any(u['glyph_id'] is None for u in p['logical']['units'])
    assert saved['layout']['max_bottom']==115 and saved['layout']['width']==80
    assert saved['anchors']['underlines'][0]['range']==[0,len(p['text'])]
    second,second_state=tmp_path/'second.pdf',tmp_path/'second.json'
    changed=edit_document(pdf,state,second,second_state,[dict(start=4,end=9,text='')])
    assert changed['after']=='ONE SEVEN THREE FOUR'
    again=open_editable(second,second_state)['state']
    assert again['paragraph']['text']==changed['after']
    assert again['anchors']['underlines'][0]['range']==[0,len(changed['after'])]
    assert again['previous_model_sha256']==saved['model_sha256']
    # A third generation keeps meaning without reselecting glyphs or anchors.
    third=edit_document(second,second_state,tmp_path/'third.pdf',tmp_path/'third.json',[])
    assert third['after']==changed['after'] and third['semantic_state_verified']
    with pymupdf.open(source) as a,pymupdf.open(second) as b:
        assert a[1].get_pixmap().samples==b[1].get_pixmap().samples


def test_hard_break_paragraph_boundary_and_soft_wrap_are_distinct(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ONE TWO THREE) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(13)),explicit_width=70))
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    text='ONE TWO THREE\nFOUR\n\nFIVE'
    write_editable(source,tmp_path/'a.pdf',tmp_path/'a.json',p,[dict(start=0,end=13,text=text)],
        fonts={'s0':{'path':str(font)}},max_bottom=190,boundary_kinds={18:'paragraph_boundary'})
    state=open_editable(tmp_path/'a.pdf',tmp_path/'a.json')['state']
    assert state['paragraph']['text']==text
    assert [b['offset'] for b in state['boundaries']]==[13,18,19]
    report=edit_document(tmp_path/'a.pdf',state,tmp_path/'b.pdf',tmp_path/'b.json',[dict(start=0,end=0,text='NEW ')])
    after=open_editable(tmp_path/'b.pdf',tmp_path/'b.json')['state']
    assert after['paragraph']['text']=='NEW '+text
    assert next(b for b in after['boundaries'] if b['kind']=='paragraph_boundary')['offset']==22
    assert report['new_line_count']>len(after['boundaries'])+1


@pytest.mark.parametrize('mode',['missing','corrupt','changed_pdf','external_resave','font_changed','font_missing'])
def test_fallback_and_stale_state_cannot_publish(tmp_path,mode):
    _,pdf,path,_=first(tmp_path)
    model=json.loads(path.read_text(encoding='utf-8'))
    if mode=='missing':model=None
    if mode=='corrupt':model['paragraph']['logical']['text']='wrong'
    if mode in ('changed_pdf','external_resave'):
        writer=PdfWriter(clone_from=PdfReader(pdf))
        writer.add_metadata({'/TestSave':'external'})
        if mode=='changed_pdf':writer.pages[0].add_transformation((1,0,0,1,3,0))
        target=tmp_path/'external.pdf';writer.write(target);pdf=target
    if mode=='font_changed':(tmp_path/'font.ttf').write_bytes(b'changed')
    if mode=='font_missing':(tmp_path/'font.ttf').unlink()
    opened=open_editable(pdf,model)
    if mode not in ('font_changed','font_missing'):
        assert opened['status']=='needs_confirmation'
        assert opened['semantics']=='unknown' and opened['observation']['lines']
    else:assert opened['status']=='restored'
    with pytest.raises(PdfError):
        edit_document(pdf,model,tmp_path/'bad.pdf',tmp_path/'bad.json',[])
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_sealed_but_inconsistent_source_binding_is_rejected(tmp_path):
    _,pdf,path,_=first(tmp_path)
    state=json.loads(path.read_text(encoding='utf-8'));state.pop('model_sha256')
    p=state['paragraph'];p['logical']['units'][0]['glyph_id']=p['logical']['units'][1]['glyph_id']
    p.pop('snapshot_sha256');p['snapshot_sha256']=digest(p)
    # Checksums are not signatures; even a recomputed checksum cannot override
    # disagreement with physical glyph evidence.
    assert open_editable(pdf,_seal(state))['status']=='needs_confirmation'


def test_failed_second_publication_rolls_back_only_our_outputs(tmp_path,monkeypatch):
    import pdfeditor.editable as module
    _,pdf,state,_=first(tmp_path)
    original=module.os.link
    target=tmp_path/'second.json'
    def fail_model(source,destination):
        if destination==target:raise OSError('simulated sidecar publication failure')
        return original(source,destination)
    monkeypatch.setattr(module.os,'link',fail_model)
    with pytest.raises(OSError,match='sidecar publication'):
        edit_document(pdf,state,tmp_path/'second.pdf',target,[])
    assert not (tmp_path/'second.pdf').exists() and not target.exists()
    assert pdf.exists() and state.exists()


def test_cli_create_restore_and_edit_logical_document(tmp_path):
    from test_paragraph_cli import write
    from test_selection_cli import successful,invoke
    source,p,e,a=prepared(tmp_path)
    pfile,efile,afile,edits=[tmp_path/n for n in ('p.json','e.json','a.json','edits.json')]
    for path,value in [(pfile,p),(efile,e),(afile,a),(edits,{'edits':[]})]:write(path,value)
    pdf,state=tmp_path/'first.pdf',tmp_path/'first.json'
    successful('edit-paragraph',source,pdf,'--paragraph',pfile,'--edits',edits,
        '--element',efile,'--anchors',afile,'--editable-state',state,'--max-bottom','115')
    opened=successful('open-editable',pdf,'--state',state,'--json',tmp_path/'open.json')
    assert opened['state']['paragraph']['text']==p['text']
    assert invoke('edit-document',pdf,tmp_path/'bad.pdf','--state',state,'--state-output',state,'--edits',edits).returncode==2
    assert not (tmp_path/'bad.pdf').exists()
    result=successful('edit-document',pdf,tmp_path/'second.pdf','--state',state,
                      '--state-output',tmp_path/'second.json','--edits',edits,'--width','140')
    assert result['semantic_state_verified'] and result['new_line_count']==1
    assert result['reused_code_glyph_count']>0 and result['provided_font_glyph_count']==0


def test_unpersistable_semantics_leave_no_partial_result(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 2 Tc 20 200 Td (ONE TWO) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7)),explicit_width=130))
    with pytest.raises(PdfError,match='logical style'):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,[])
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_leading_authored_blank_line_does_not_shift_on_every_reopen(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7)),explicit_width=130))
    write_editable(source,tmp_path/'a.pdf',tmp_path/'a.json',p,[dict(start=0,end=0,text='\n')],max_bottom=160)
    first=open_editable(tmp_path/'a.pdf',tmp_path/'a.json')['state']
    edit_document(tmp_path/'a.pdf',first,tmp_path/'b.pdf',tmp_path/'b.json',[])
    second=open_editable(tmp_path/'b.pdf',tmp_path/'b.json')['state']
    assert second['paragraph']['text']=='\nONE TWO'
    assert first['layout']==second['layout']
    assert first['layout_provenance']==second['layout_provenance']
    with pymupdf.open(tmp_path/'a.pdf') as a,pymupdf.open(tmp_path/'b.pdf') as b:
        assert a[0].get_pixmap().samples==b[0].get_pixmap().samples


def test_encrypted_pdf_does_not_leak_semantics_into_plaintext_sidecar(tmp_path):
    source=source_pdf(tmp_path)
    writer=PdfWriter(clone_from=PdfReader(source));writer.encrypt('',owner_password='owner',algorithm='RC4-128')
    encrypted=tmp_path/'encrypted.pdf';writer.write(encrypted)
    with pytest.raises(PdfError,match='plaintext editing sidecars'):
        write_editable(encrypted,tmp_path/'bad.pdf',tmp_path/'bad.json',{},[])
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_crlf_is_one_authored_boundary_and_retains_its_kind(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7)),explicit_width=130))
    write_editable(source,tmp_path/'a.pdf',tmp_path/'a.json',p,[dict(start=3,end=4,text='\r\n')],
                   boundary_kinds={3:'paragraph_boundary'},max_bottom=150)
    state=open_editable(tmp_path/'a.pdf',tmp_path/'a.json')['state']
    assert state['boundaries']==[dict(offset=3,end=5,kind='paragraph_boundary',provenance='explicitly_confirmed')]
    edit_document(tmp_path/'a.pdf',state,tmp_path/'b.pdf',tmp_path/'b.json',[])
    again=open_editable(tmp_path/'b.pdf',tmp_path/'b.json')['state']
    assert again['boundaries']==state['boundaries'] and again['paragraph']['text']=='ONE\r\nTWO'
