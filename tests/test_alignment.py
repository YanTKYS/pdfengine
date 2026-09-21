from copy import deepcopy
import json

import pymupdf
import pytest

from pdfeditor.alignment import confirm
from pdfeditor.attributed import inspect_paragraph, SourceParagraph, apply_edits, digest
from pdfeditor.backend import PdfError
from pdfeditor.editable import write_editable, edit_document, open_editable, _seal
from pdfeditor.paragraph import ParagraphShaper, plan_paragraph
from pdfeditor.selection import make_selection
from pdfeditor.rich_layout import layout_attributed, InlineGlyph
from pdfeditor.layout import LayoutError
from test_attributed import source_pdf


def fixture(tmp_path, text='AB CD EF GH IJ KL', tc=0, ts=0):
    source=source_pdf(tmp_path,f'BT /Regular 12 Tf {tc} Tc {ts} Ts 20 200 Td ({text}) Tj ET'.encode())
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(len(text))),explicit_width=64))
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    return source,p,{'s0':dict(path=str(font))}


def placed(text, alignment, **kwargs):
    return layout_attributed(text,shape=lambda a,b:[InlineGlyph(c,1,None,8,2) for c in text[a:b]],
        x=10,baseline=20,width=6,min_line_height=12,max_bottom=200,empty_ascent=8,empty_descent=2,
        alignment=alignment,**kwargs)


@pytest.mark.parametrize('alignment,x',[('left',10),('right',12),('center',11)])
def test_alignment_edges(alignment,x):
    line=placed('abcd',alignment).lines[0]
    assert line.x==x and line.width==4


@pytest.mark.parametrize('policy,text',[('word','ab cd ef gh'),('character','abcdefghijk')])
def test_justify_soft_lines_and_ragged_final(policy,text):
    result=placed(text,'justify',justify_policy=policy)
    assert len(result.lines)==2
    assert result.lines[0].x==10 and result.lines[0].width==6
    assert result.lines[-1].width<6 and result.lines[-1].x==10


def test_hard_break_and_blank_lines_are_not_justified():
    result=placed('ab cd\n\nxy','justify',justify_policy='word')
    assert [l.width for l in result.lines]==[5,0,2]


def test_no_word_gap_refuses_instead_of_stretching_letters():
    with pytest.raises(LayoutError,match='eligible'):
        placed('abcdefghijk','justify',justify_policy='word',first_line_indent=0.5)


@pytest.mark.parametrize('invalid',[{}, {'alignment':'unknown'}, {'alignment':'justify'},
    {'alignment':'left','justify_policy':'word'}, {'alignment':'justify','justify_policy':'auto'}])
def test_invalid_confirmation(invalid):
    with pytest.raises(PdfError):confirm({},invalid)


def test_unknown_is_not_confirmation_and_positive_contradiction_refuses():
    snapshot={'spacing':{'alignment_candidates':[dict(value='right',provenance='alignment_candidate')]}}
    assert confirm(snapshot,None)['provenance']=='generated_layout_policy'
    with pytest.raises(PdfError,match='contradict'):confirm(snapshot,dict(alignment='justify',justify_policy='word'))
    snapshot['spacing']['alignment_candidates'][0]=dict(value='unknown',provenance='unknown')
    assert confirm(snapshot,dict(alignment='center'))['provenance']=='explicitly_confirmed'


@pytest.mark.parametrize('mode',['left','right','center','justify'])
def test_alignment_save_reopen_edit_noop(tmp_path,mode):
    source,p,fonts=fixture(tmp_path)
    layout=dict(alignment=mode,**({'justify_policy':'word'} if mode=='justify' else {}))
    pdf,sidecar=tmp_path/'first.pdf',tmp_path/'first.json'
    write_editable(source,pdf,sidecar,p,[],fonts=fonts,paragraph_layout=layout,min_line_height=20,max_bottom=220)
    for n,edits in enumerate(([dict(start=3,end=5,text='XYZ')],[])):
        state=open_editable(pdf,sidecar)
        assert state['status']=='restored',state
        assert state['state']['logical_element']['alignment']['value']==mode
        out,model=tmp_path/f'v{n}.pdf',tmp_path/f'v{n}.json'
        edit_document(pdf,sidecar,out,model,edits)
        if not edits:
            with pymupdf.open(pdf) as a,pymupdf.open(out) as b:
                assert a[0].get_pixmap(dpi=144).samples==b[0].get_pixmap(dpi=144).samples
        pdf,sidecar=out,model
    assert open_editable(pdf,sidecar)['status']=='restored'


@pytest.mark.parametrize('tc,ts',[(1,0),(0,2),(1,-2)])
def test_tracking_rise_with_justify(tmp_path,tc,ts):
    source,p,fonts=fixture(tmp_path,tc=tc,ts=ts)
    pdf,sidecar=tmp_path/'first.pdf',tmp_path/'first.json'
    write_editable(source,pdf,sidecar,p,[],fonts=fonts,
        paragraph_style={'s0':dict(tracking=tc,baseline_shift=-ts)},
        paragraph_layout=dict(alignment='justify',justify_policy='word'),min_line_height=20,max_bottom=220)
    state=open_editable(pdf,sidecar);assert state['status']=='restored',state
    edit_document(pdf,sidecar,tmp_path/'edited.pdf',tmp_path/'edited.json',[dict(start=3,end=5,text='XYZ')])


def test_retained_nonleft_discards_source_tw_tj_and_reposition(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 8 Tw 20 200 Td [(AB ) -200 (CD)] TJ ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(5)),explicit_width=100))
    paragraph=SourceParagraph(source,p['selection'])
    try:
        units=apply_edits(paragraph,p,[])
        old=ParagraphShaper(paragraph,units,{})
        new=ParagraphShaper(paragraph,units,{},alignment='justify')
        try:
            assert old.shape(0,5)[2].advance>7.2
            assert [g.advance for g in new.shape(0,5)]==pytest.approx([7.2]*5)
            assert paragraph.units[2].event.state.tw==8
        finally:old.close();new.close()
    finally:paragraph.close()


def test_sidecar_alignment_and_geometry_tampering_refused(tmp_path):
    source,p,fonts=fixture(tmp_path)
    pdf,model=tmp_path/'first.pdf',tmp_path/'first.json'
    write_editable(source,pdf,model,p,[],fonts=fonts,paragraph_layout=dict(alignment='right'))
    original=json.loads(model.read_text())
    for kind in ('alignment','width','line','downgrade'):
        state=deepcopy(original);state.pop('model_sha256')
        if kind=='alignment':state['logical_element']['alignment']['value']='center'
        elif kind=='downgrade':state['logical_element']['alignment']=dict(value='left',provenance='generated_layout_policy')
        elif kind=='width':
            state['layout']['width']+=2;state['container']['region']['width']+=2
        else:state['physical_layout']['lines'][0]['width']+=2
        restored=open_editable(pdf,_seal(state))
        assert restored['status']=='needs_confirmation' and 'alignment' in restored['reason'],restored


def test_geometry_failure_does_not_publish(tmp_path,monkeypatch):
    import pdfeditor.paragraph as module
    from dataclasses import replace
    real=module.layout_attributed
    def wrong(*a,**kw):
        layout=real(*a,**kw)
        return replace(layout,glyphs=[replace(g,x=g.x+1) if i==0 else g for i,g in enumerate(layout.glyphs)])
    monkeypatch.setattr(module,'layout_attributed',wrong)
    source,p,fonts=fixture(tmp_path)
    with pytest.raises(PdfError,match='geometry witness'):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,[],fonts=fonts,paragraph_layout=dict(alignment='right'))
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_source_justified_word_fixture_is_pixel_exact(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 7.2 Tw 20 200 Td (AB CD) Tj 0 Tw 0 -20 Td (EF) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(7)),explicit_width=43.2),line_joiner=' ')
    assert p['spacing']['alignment_candidates'][0]['value']=='justify'
    out,model=tmp_path/'same.pdf',tmp_path/'same.json'
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    write_editable(source,out,model,p,[],fonts={'s0':dict(path=str(font))},
        paragraph_layout=dict(alignment='justify',justify_policy='word'),min_line_height=20)
    with pymupdf.open(source) as a,pymupdf.open(out) as b:
        assert all(a[i].get_pixmap(dpi=144).samples==b[i].get_pixmap(dpi=144).samples for i in range(len(a)))


def test_unknown_inline_tracking_cannot_be_filled_by_alignment(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf 1 Tc 20 200 Td (ABCD) Tj 2 Tc 0 -20 Td (EF) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(6)),explicit_width=44))
    with pytest.raises(PdfError,match='logical tracking'):
        plan_paragraph(source,p,[],paragraph_layout=dict(alignment='justify',justify_policy='character'))
