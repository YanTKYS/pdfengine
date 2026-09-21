import pymupdf
import pytest
from copy import deepcopy

from pdfeditor.story_flow import edit_story, open_story
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.document_flow import confirm_document, open_document
from pdfeditor.document_flow import _reseal
from pdfeditor.flow_transaction import edit_flow_batch
from test_story_flow import prepared, LONG, replace
from test_shared_flow import prepared as shared_prepared
from test_alignment import fixture


@pytest.mark.parametrize('mode',['right','center','justify'])
def test_story_preserves_alignment_across_physical_breaks_and_noop(tmp_path,mode):
    alignment=dict(alignment=mode,**({'justify_policy':'word'} if mode=='justify' else {}))
    source,state=prepared(tmp_path,paragraph_layout=alignment)
    for n in range(2):
        out,sidecar=tmp_path/f'v{n}.pdf',tmp_path/f'v{n}.json'
        edit_story(source,state,out,sidecar,replace(state,LONG) if n==0 else [])
        opened=open_story(out,sidecar);assert opened['status']=='restored',opened
        state=opened['state']
        damaged=deepcopy(state)
        damaged['logical']['alignment']=dict(value='left',provenance='generated_layout_policy')
        assert open_story(out,_reseal(damaged))['status']=='needs_confirmation'
        for fragment in state['fragments'].values():
            b=fragment['binding'];assert b['logical_element']['alignment']['value']==mode
        if mode=='justify':
            first=state['fragments']['A']['binding']
            assert first['physical_layout']['paragraph_continues']
            assert first['physical_layout']['lines'][-1]['width']==pytest.approx(first['layout']['width'])
            last=state['fragments']['B']['binding']
            assert not last['physical_layout']['paragraph_continues']
            assert last['physical_layout']['lines'][-1]['width']<last['layout']['width']
        if n:
            with pymupdf.open(source) as a,pymupdf.open(out) as b:
                assert all(a[i].get_pixmap().samples==b[i].get_pixmap().samples for i in range(len(a)))
        source=out


def test_justify_shared_flow_inherits_paragraph_confirmation(tmp_path):
    source,state=shared_prepared(tmp_path,paragraph_layout=dict(alignment='justify',justify_policy='word'))
    edits={'B':dict(edits=[dict(start=0,end=len(state['paragraphs']['B']['logical']['text']),text=LONG,style_id='body')])}
    for n in range(2):
        out,sidecar=tmp_path/f's{n}.pdf',tmp_path/f's{n}.json'
        edit_shared_flow(source,state,out,sidecar,edits if n==0 else {})
        opened=open_shared_flow(out,sidecar);assert opened['status']=='restored',opened
        state=opened['state']
        damaged=deepcopy(state)
        damaged['paragraphs']['B']['logical']['alignment']=dict(value='left',provenance='generated_layout_policy')
        assert open_shared_flow(out,_reseal(damaged))['status']=='needs_confirmation'
        assert all(s['binding']['logical_element']['alignment']['value']=='justify' for s in state['slots'].values())
        source=out


def test_document_flow_passes_confirmed_left_and_reedit(tmp_path):
    source,p,fonts=fixture(tmp_path,'ABCD')
    state=confirm_document(source,{'p':dict(paragraph=p,fonts=fonts,paint_relations=[],
        paragraph_layout=dict(alignment='left'),layout=dict(x=20,baseline=60,width=64,max_bottom=220,min_line_height=20))},
        container_id='region',bounds=[10,40,100,230],page=1,follows=[])
    out,model=tmp_path/'doc.pdf',tmp_path/'doc.json'
    edit_flow_batch(source,state,out,model,{'p':dict(edits=[dict(start=4,end=4,text='EFGHIJKLM')])})
    opened=open_document(out,model);assert opened['status']=='restored',opened
    assert opened['state']['elements']['p']['binding']['logical_element']['alignment']==dict(value='left',provenance='explicitly_confirmed')
