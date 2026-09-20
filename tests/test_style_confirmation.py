import json

import pymupdf
import pytest

from pdfeditor.attributed import digest, inspect_paragraph, SourceParagraph
from pdfeditor.backend import PdfError
from pdfeditor.editable import write_editable, edit_document, open_editable, _seal
from pdfeditor.selection import make_selection
from pdfeditor.style_confirmation import observed_values
from test_attributed import source_pdf


def setup(tmp_path, tc=1, ts=2, *, scaled=False, anisotropic=False):
    transform=b'q .75 0 0 .75 0 0 cm ' if scaled else b''
    program=transform+f'BT /Regular 12 Tf {tc} Tc {ts} Ts '.encode()+(
        (b'80 Tz 1 0 0 1.5 20 180 Tm ' if anisotropic else b'80 Tz 1 0 0 1 20 180 Tm ')
        if scaled else b'20 200 Td ')+b'(ABCD) Tj ET'+(b' Q' if scaled else b'')
    source=source_pdf(tmp_path,program)
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(4)),explicit_width=150))
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    return source,p,{'s0':dict(path=str(font))}


def verify(pdf, model, expected):
    opened=open_editable(pdf,model)
    assert opened['status']=='restored',opened
    p=opened['state']['paragraph']
    physical=SourceParagraph(pdf,p['selection'])
    try:
        for unit in physical.units:
            assert observed_values(unit)==pytest.approx(expected,abs=.001)
    finally:physical.close()
    for style in p['styles']:
        for field,value in expected.items():
            assert style[field]==pytest.approx(value)
            assert style[field+'_provenance']=='explicitly_confirmed'
    return opened['state']


@pytest.mark.parametrize('tc,ts,scaled',[(1,0,False),(0,2,False),(1,2,False),(1,-2,False),(2,2,True)])
def test_confirmed_spacing_roundtrip_and_exact_noop(tmp_path,tc,ts,scaled):
    source,p,fonts=setup(tmp_path,tc,ts,scaled=scaled)
    expected=dict(tracking=tc*(.6 if scaled else 1),baseline_shift=-ts*(.75 if scaled else 1))
    pdf,model=tmp_path/'first.pdf',tmp_path/'first.json'
    # No-op witnessing must not move glyphs (in particular, no doubled Ts).
    write_editable(source,pdf,model,p,[],fonts=fonts,paragraph_style={'s0':expected},min_line_height=20,max_bottom=170)
    state=verify(pdf,model,expected)
    with pymupdf.open(source) as a,pymupdf.open(pdf) as b:
        assert all(a[i].get_pixmap(dpi=144).samples==b[i].get_pixmap(dpi=144).samples for i in range(len(a)))
    for name,edits in [('edit',[dict(start=1,end=2,text='新')]),('reedit',[dict(start=1,end=2,text='字')]),('noop',[])]:
        out,sidecar=tmp_path/(name+'.pdf'),tmp_path/(name+'.json')
        r=edit_document(pdf,model,out,sidecar,edits)
        state=verify(out,sidecar,expected)
        assert state['paragraph']['text']==r['after']
        if name=='noop':
            with pymupdf.open(pdf) as a,pymupdf.open(out) as b:
                assert all(a[i].get_pixmap(dpi=144).samples==b[i].get_pixmap(dpi=144).samples for i in range(len(a)))
        pdf,model=out,sidecar


@pytest.mark.parametrize('mode',['unconfirmed','unknown','wrong_value','nan','bool','unknown_style','invalid_registry'])
def test_confirmation_is_explicit_and_fail_closed(tmp_path,mode):
    source,p,fonts=setup(tmp_path,1,0)
    confirmation={'s0':dict(tracking=1)}
    if mode=='unconfirmed':confirmation=None
    if mode=='unknown':
        source=source_pdf(tmp_path,b'BT /Regular 12 Tf 1 Tc 20 200 Td (AB) Tj 0 -20 Td 2 Tc (CD) Tj ET')
        p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(4)),explicit_width=150))
    if mode=='wrong_value':confirmation['s0']['tracking']=3
    if mode=='nan':confirmation['s0']['tracking']=float('nan')
    if mode=='bool':confirmation['s0']['tracking']=True
    if mode=='unknown_style':confirmation={'missing':dict(tracking=1)}
    if mode=='invalid_registry':confirmation=['s0']
    with pytest.raises(PdfError):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,[dict(start=0,end=1,text='X')],
            fonts=fonts,paragraph_style=confirmation)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


@pytest.mark.parametrize('field',['tracking','baseline_shift'])
def test_saved_style_must_match_pdf_even_when_sidecar_is_resealed(tmp_path,field):
    source,p,fonts=setup(tmp_path)
    pdf,model=tmp_path/'first.pdf',tmp_path/'first.json'
    write_editable(source,pdf,model,p,[],fonts=fonts,paragraph_style={'s0':dict(tracking=1,baseline_shift=-2)})
    state=json.loads(model.read_text(encoding='utf-8'));state.pop('model_sha256')
    paragraph=state['paragraph'];paragraph.pop('snapshot_sha256')
    paragraph['logical']['style_confirmations']['s0'][field]['value']+=1
    paragraph['styles'][0][field]+=1
    paragraph['snapshot_sha256']=digest(paragraph)
    result=open_editable(pdf,_seal(state))
    assert result['status']=='needs_confirmation' and 'witness' in result['reason']


@pytest.mark.parametrize('field',['tc','ts'])
def test_writer_witness_mismatch_never_publishes(tmp_path,monkeypatch,field):
    import pdfeditor.style_confirmation as module
    source,p,fonts=setup(tmp_path)
    original=module.writer_spacing
    def broken(style):
        tc,ts,rise=original(style)
        # Keep exactly the same rendered origins, but omit the promised state.
        return (0,ts,rise) if field=='tc' else (tc,0,0)
    monkeypatch.setattr(module,'writer_spacing',broken)
    with pytest.raises(PdfError,match='witness'):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,[],fonts=fonts,
            paragraph_style={'s0':dict(tracking=1,baseline_shift=-2)})
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_confirmed_destination_style_roundtrip(tmp_path):
    from test_destination_style import recipes
    source,p,_=setup(tmp_path,0,0);styles,fonts=recipes(tmp_path,p)
    styles['logical:accent'].update(tracking=.6,baseline_shift=-2)
    confirmed={'logical:accent':dict(tracking=.6,baseline_shift=-2)}
    pdf,model=tmp_path/'first.pdf',tmp_path/'first.json'
    write_editable(source,pdf,model,p,[dict(start=0,end=4,runs=[dict(text='Hello',style_id='logical:accent')])],
        render_styles=styles,fonts=fonts,paragraph_style=confirmed,max_bottom=180)
    verify(pdf,model,confirmed['logical:accent'])
    edit_document(pdf,model,tmp_path/'next.pdf',tmp_path/'next.json',[dict(start=0,end=1,text='Y')])
    verify(tmp_path/'next.pdf',tmp_path/'next.json',confirmed['logical:accent'])


def test_confirmed_empty_style_requires_a_pdf_witness(tmp_path):
    source,p,fonts=setup(tmp_path)
    with pytest.raises(PdfError,match='empty confirmed'):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,[dict(start=0,end=4,text='')],
            fonts=fonts,paragraph_style={'s0':dict(tracking=1,baseline_shift=-2)})
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_anisotropic_source_noop_keeps_existing_outside_pixel_refusal(tmp_path):
    from pdfeditor.paragraph import edit_paragraph
    source,p,_=setup(tmp_path,2,2,scaled=True,anisotropic=True)
    # The pre-confirmation path already refuses this transform. Do not weaken
    # its outside-pixel guard merely to accept a confirmed style.
    for name,options in [('plain',{}),('confirmed',dict(paragraph_style={
            's0':dict(tracking=1.2,baseline_shift=-2.25)}))]:
        output=tmp_path/(name+'.pdf')
        with pytest.raises(PdfError,match='pixels outside'):
            edit_paragraph(source,output,p,[],**options)
        assert not output.exists()


@pytest.mark.parametrize('confirmed',[dict(tracking=1),dict(baseline_shift=-2)])
def test_partial_confirmation_does_not_promote_other_properties(tmp_path,confirmed):
    source,p,fonts=setup(tmp_path)
    with pytest.raises(PdfError,match='persistence refused|tracking candidate'):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,[dict(start=0,end=1,text='X')],
            fonts=fonts,paragraph_style={'s0':confirmed})
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_pdf_observation_alone_never_promotes_a_candidate(tmp_path):
    source,p,fonts=setup(tmp_path)
    pdf,model=tmp_path/'confirmed.pdf',tmp_path/'confirmed.json'
    write_editable(source,pdf,model,p,[],fonts=fonts,paragraph_style={'s0':dict(tracking=1,baseline_shift=-2)})
    logical=open_editable(pdf,model)['state']['paragraph']
    observed=inspect_paragraph(pdf,logical['selection'])
    assert observed['styles'][0]['tracking'] is None
    assert observed['styles'][0]['tracking_provenance']=='candidate'
    assert open_editable(pdf,None)['status']=='needs_confirmation'
    with pytest.raises(PdfError,match='tracking candidate'):
        write_editable(pdf,tmp_path/'bad.pdf',tmp_path/'bad.json',observed,[dict(start=0,end=1,text='X')],fonts=fonts)


def test_empty_recipe_cannot_claim_confirmation_without_pdf_witness(tmp_path):
    source,p,fonts=setup(tmp_path,0,0)
    pdf,model=tmp_path/'empty.pdf',tmp_path/'empty.json'
    write_editable(source,pdf,model,p,[dict(start=0,end=4,text='')],fonts=fonts)
    state=json.loads(model.read_text(encoding='utf-8'));state.pop('model_sha256')
    paragraph=state['paragraph'];paragraph.pop('snapshot_sha256')
    paragraph['styles'][0]['baseline_shift_provenance']='explicitly_confirmed'
    paragraph['style_recipes']['s0']['properties']['baseline_shift_provenance']='explicitly_confirmed'
    paragraph['snapshot_sha256']=digest(paragraph)
    result=open_editable(pdf,_seal(state))
    assert result['status']=='needs_confirmation' and 'witness' in result['reason']
