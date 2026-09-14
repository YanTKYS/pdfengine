"""Spacing evidence separates glyph metrics, inline tracking and line adjustments."""
import pytest

from pdfeditor.attributed import SourceParagraph
from pdfeditor.selection import inspect_selection_source, make_selection
from pdfeditor.spacing import observe_spacing
from test_attributed import source_pdf

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
