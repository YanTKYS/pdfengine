"""Spacing evidence separates glyph metrics, inline tracking and line adjustments."""
import pytest

from pdfeditor.attributed import SourceParagraph,inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.editable import write_editable,open_editable,edit_document
from pdfeditor.paragraph import edit_paragraph,plan_paragraph
from pdfeditor.replay import glyph_observations,compare_glyphs
from pdfeditor.selection import inspect_selection_source, make_selection
from pdfeditor.spacing import observe_spacing
from test_attributed import source_pdf
import pymupdf

CHAR=7.2  # Courier 600/1000 at 12 pt


def justified_tj(text, extra):
    """Spread ``extra`` points evenly over every gap with TJ adjustments."""
    gaps=len(text)-1
    adjustment=-extra/gaps/12*1000
    parts=[f'({c})' for c in text]
    return ('['+f' {adjustment:.4f} '.join(parts)+'] TJ').encode()


def positioned(text, x, y, right):
    """Place every glyph with its own Td so the line spans exactly ``x``..``right``."""
    step=(right-x-CHAR)/(len(text)-1)
    return b'BT /Regular 12 Tf '+b' '.join(f'1 0 0 1 {x+i*step:.4f} {y} Tm ({c}) Tj'.encode() for i,c in enumerate(text))+b' ET '


CASES={
    'left_tracking': (b'BT /Regular 12 Tf 1 Tc 20 200 Td (ONE TWO) Tj 0 -16 Td (THREE) Tj ET', 1.0, 'left'),
    'justify_tw': (b'BT /Regular 12 Tf 20 200 Td 20.4 Tw (ONE TWO SIX) Tj 0 -16 Td 6 Tw (FOUR FIVE SEVEN) Tj 0 -16 Td 0 Tw (END) Tj ET', 0.0, 'justify'),
    'justify_tj': (b'BT /Regular 12 Tf 20 200 Td '+justified_tj('ONE TWO',120-7*CHAR)+b' 0 -16 Td '+justified_tj('THREE FOUR',120-10*CHAR)+b' 0 -16 Td (END) Tj ET', 0.0, 'justify'),
    'tracking_and_justify': (b'BT /Regular 12 Tf .5 Tc 20 200 Td 17.9 Tw (ONE TWO SIX) Tj 0 -16 Td 2.5 Tw (FOUR FIVE SEVEN) Tj 0 -16 Td 0 Tw (END) Tj ET', 0.5, 'justify'),
    'coincidental_full_lines': (b'BT /Regular 12 Tf 20 200 Td (ONE TWOS) Tj 0 -16 Td (SIX SEVE) Tj 0 -16 Td (END) Tj ET', 0.0, 'unknown'),
    'repositioned_words': (b'BT /Regular 12 Tf 20 200 Td (ONE) Tj 39.2 0 Td (TWO) Tj 39.2 0 Td (SIX) Tj -78.4 -16 Td (FOUR) Tj 32 0 Td (FIVE) Tj 32 0 Td (SEVEN) Tj -64 -16 Td (END) Tj ET', 0.0, 'unknown'),
    'per_glyph_positioning': (positioned('ONETWO',20,200,140)+positioned('SIXSEVEN',20,184,140)+b'BT /Regular 12 Tf 20 168 Td (END) Tj ET', 0.0, 'unknown'),
}


@pytest.mark.parametrize('name',list(CASES))
def test_spacing_evidence_distinguishes_tracking_metrics_and_line_adjustments(tmp_path,name):
    program,tracking,alignment=CASES[name]
    source=source_pdf(tmp_path,program)
    ids=[g for line in inspect_selection_source(source)['lines'] for g in line['glyph_ids']]
    paragraph=SourceParagraph(source,make_selection(source,glyph_ids=ids,explicit_width=120))
    try:
        evidence=observe_spacing(paragraph)
    finally:
        paragraph.close()
    assert evidence['tracking']['value']==pytest.approx(tracking)
    assert evidence['tracking']['provenance']==('inferred_consistent_tracking' if tracking else 'observed_source')
    candidate=evidence['alignment_candidates'][0]
    assert candidate['value']==alignment,evidence['alignment_candidates']
    if alignment!='unknown':
        assert candidate['requires_confirmation'] and candidate['provenance']=='alignment_candidate'
    lines=evidence['lines']
    # Glyph metrics are exact: every gap decomposes into nominal advance plus state and adjustments.
    for line in lines:
        for gap in line['gaps']:
            assert gap['nominal']==pytest.approx(CHAR)
            assert gap['measured']==pytest.approx(gap['nominal']+gap['tc']+gap['tw']+gap['tj']+gap['reposition'])
    if alignment=='justify':
        full=[line for line in lines[:-1]];edge=candidate['evidence']['right_edge']
        assert all(line['right']==pytest.approx(edge,abs=.15) for line in full)
        assert lines[-1]['distribution']=='none' and lines[-1]['right']<edge
        assert {line['distribution'] for line in full}<={'uniform_char','uniform_word'}
        assert candidate['evidence']['ragged_lines']==[lines[-1]['index']]
    if name=='left_tracking':
        # The constant Tc is inline tracking, not a per-line distribution.
        assert all(line['distribution']=='none' for line in lines)
    if name=='tracking_and_justify':
        assert all(abs(line['excess_tracking']-0.5*(line['glyph_count']-1))<1e-9 for line in lines)
    if name=='coincidental_full_lines':
        assert 'coincidental' in candidate['reason']
    if name in ('repositioned_words','per_glyph_positioning'):
        # Even gaps realized only by Td/Tm repositioning, without a painted
        # whitespace glyph, prove neither a word gap nor justification. Both
        # lines reach the same edge; the model still refuses to call it justify.
        assert all(line['distribution']=='uniform_reposition' for line in lines[:-1])
        assert all(not gap['whitespace'] for line in lines for gap in line['gaps'])
        assert lines[0]['right']==pytest.approx(lines[1]['right'],abs=.15)
        assert 'repositioning' in candidate['reason'] and candidate['unproven_lines']==[0,1]
    if name=='justify_tw':
        assert all(gap['whitespace']==(gap['tw']>0) for line in lines for gap in line['gaps'])
    assert evidence['tracking']['requires_confirmation']==bool(tracking)


@pytest.mark.parametrize('name',['justify_tw','justify_tj','tracking_and_justify','left_tracking'])
def test_snapshot_separates_line_spacing_and_unconfirmed_tracking(tmp_path,name):
    source=source_pdf(tmp_path,CASES[name][0])
    ids=[g for l in inspect_selection_source(source)['lines'] for g in l['glyph_ids']]
    p=inspect_paragraph(source,make_selection(source,glyph_ids=ids,explicit_width=120))
    assert len(p['styles'])==1 and 'word_spacing' not in p['styles'][0]
    assert p['spacing']['schema']=='pdfengine-spacing-evidence-1'
    assert p['spacing']['alignment_candidates'][0]['value']==CASES[name][2]
    expected=None if CASES[name][1] else 0.0
    assert p['styles'][0]['tracking']==expected
    assert p['styles'][0]['tracking_provenance']==('candidate' if expected is None else 'observed_source')


@pytest.mark.parametrize('inline',[False,True])
def test_line_varying_tc_merges_but_inline_tc_changes_stay_separate(tmp_path,inline):
    program=(b'BT /Regular 12 Tf .4 Tc 20 200 Td (AB) Tj .8 Tc (CD) Tj ET' if inline else
             b'BT /Regular 12 Tf .4 Tc 20 200 Td (ABCD) Tj 0 -16 Td .8 Tc (EFGH) Tj ET')
    source=source_pdf(tmp_path,program)
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(4 if inline else 8)),explicit_width=120))
    assert len(p['styles'])==(2 if inline else 1)
    assert all(s['tracking'] is None and s['tracking_provenance']==('candidate' if inline else 'unknown') for s in p['styles'])
    assert p['spacing']['tracking']['provenance']=='unknown'
    with pytest.raises(PdfError,match='confirmation' if inline else 'tracking is unknown'):
        plan_paragraph(source,p,[dict(start=0,end=1,text='X')])


@pytest.mark.parametrize('tc',[b'.4',b'.8'])
def test_retained_spacing_uses_each_glyph_witness_and_noop_is_exact(tmp_path,tc):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf .4 Tc 20 200 Td (ABCD) Tj 0 -16 Td '+tc+b' Tc (EFGH) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=list(range(8)),explicit_width=120),line_joiner='\n')
    output=tmp_path/'noop.pdf'
    edit_paragraph(source,output,p,[],min_line_height=16)
    with pymupdf.open(source) as a,pymupdf.open(output) as b:
        assert compare_glyphs(glyph_observations(a[0]),glyph_observations(b[0]))['passed']
        assert all(a[i].get_pixmap(dpi=144).samples==b[i].get_pixmap(dpi=144).samples for i in range(len(a)))
    plan=plan_paragraph(source,p,[dict(start=8,end=9,text='')],min_line_height=16)
    assert plan['lines'][-1]['width']==pytest.approx(3*CHAR+2*float(tc),abs=.002)
    assert p['styles'][0]['tracking'] is None
    with pytest.raises(PdfError,match='persistence refused'):
        write_editable(source,tmp_path/'bad.pdf',tmp_path/'bad.json',p,[],min_line_height=16)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_unknown_typing_recipe_survives_deletion_but_cannot_invent_tracking(tmp_path):
    source=source_pdf(tmp_path,b'BT /Regular 12 Tf .4 Tc 20 200 Td (ABCD) Tj ET')
    p=inspect_paragraph(source,make_selection(source,glyph_ids=[0,1,2,3],explicit_width=120))
    out,model=tmp_path/'empty.pdf',tmp_path/'empty.json'
    write_editable(source,out,model,p,[dict(start=0,end=4,text='')])
    opened=open_editable(out,model)
    assert opened['status']=='restored'
    assert opened['state']['paragraph']['styles'][0]['tracking'] is None
    with pytest.raises(PdfError,match='tracking candidate'):
        edit_document(out,model,tmp_path/'bad.pdf',tmp_path/'bad.json',[dict(start=0,end=0,text='X')])
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_line_word_spacing_no_longer_creates_false_edit_boundaries(tmp_path):
    source=source_pdf(tmp_path,CASES['justify_tw'][0])
    ids=[g for l in inspect_selection_source(source)['lines'] for g in l['glyph_ids']]
    p=inspect_paragraph(source,make_selection(source,glyph_ids=ids,explicit_width=150))
    font=tmp_path/'font.ttf';font.write_bytes(pymupdf.Font('cjk').buffer)
    out,model=tmp_path/'edited.pdf',tmp_path/'edited.json'
    # No explicit style override: these are three lines of one inline style.
    write_editable(source,out,model,p,[dict(start=0,end=len(p['text']),text='First line\nSecond line')],
        fonts={'s0':dict(path=str(font))},min_line_height=16,max_bottom=140)
    state=open_editable(out,model)
    assert state['status']=='restored' and len(state['state']['paragraph']['styles'])==1
    assert state['state']['paragraph']['styles'][0]['tracking']==0.0
    again,model2=tmp_path/'again.pdf',tmp_path/'again.json'
    edit_document(out,model,again,model2,[dict(start=0,end=5,text='Other')])
    opened=open_editable(again,model2)
    assert opened['status']=='restored' and opened['state']['paragraph']['text']=='Other line\nSecond line'
    noop=tmp_path/'noop.pdf'
    edit_document(again,model2,noop,tmp_path/'noop.json',[])
    with pymupdf.open(again) as a,pymupdf.open(noop) as b:
        assert all(a[i].get_pixmap(dpi=144).samples==b[i].get_pixmap(dpi=144).samples for i in range(len(a)))
