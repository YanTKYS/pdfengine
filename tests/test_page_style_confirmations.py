"""B1 exposes witnessed inline confirmations without guessing source spacing."""
from copy import deepcopy
import re

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import _seal, propose_page_flow, accept_page_flow_report, replace_in_flow
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
import page_flow_fixture as fx


@pytest.fixture
def source(tmp_path):
    font = tmp_path / 'font.ttf'
    font.write_bytes(fx.build_font())
    text = fx.PARAGRAPHS[0]
    raw = fx.make_page(tmp_path / 'raw.pdf', font.read_bytes(), lines=[
        (fx.MARGIN + fx.SIZE, 160, fx.SIZE, text[:39], .24),
        (fx.MARGIN, 178, fx.SIZE, text[39:], .24)])
    pdf = tmp_path / 'source.pdf'
    with pymupdf.open(raw) as doc:
        xref = doc[0].get_contents()[0]
        data = doc.xref_stream(xref)
        # Nonuniform TJ remains source adjacency; it is not the confirmed Tc.
        data = re.sub(rb'<([0-9A-F]{40})([0-9A-F]+)> Tj', rb'[<\1> -20 <\2>] TJ', data, count=1)
        doc.update_stream(xref, data)
        doc.save(pdf)
    return pdf, font


def proposal(source, confirmation=None):
    pdf, font = source
    return propose_page_flow(pdf, font_candidates=[font], source_style_confirmations=confirmation)


def test_confirmation_reproduces_mixed_tc_tj_and_survives_edit(source, tmp_path):
    pdf, _ = source
    assert proposal(source)['status'] == 'refused'
    p = proposal(source, {'P1': {'s0': {'tracking': .24}}})
    assert p['status'] == 'proposed', p['refusals']
    state = accept_page_flow_report(pdf, p)['state']
    assert state['paragraph_policies']['P1']['text_placement'] == 'preserve-source-adjacency'
    original = pdf
    for name, edits in [('noop', {}), ('edit', replace_in_flow(state, '本年度', '再本年度', paragraph_id='P1', style_id='style-1'))]:
        out, side = tmp_path / f'{name}.pdf', tmp_path / f'{name}.json'
        edit_shared_flow(pdf, state, out, side, edits)
        opened = open_shared_flow(out, side)
        assert opened['status'] == 'restored'
        state = opened['state']
        if name == 'noop':
            with pymupdf.open(original) as a, pymupdf.open(out) as b:
                assert a[0].get_pixmap(dpi=144).samples == b[0].get_pixmap(dpi=144).samples
        attrs = state['paragraphs']['P1']['style_registry']['style-1']['attributes']
        assert attrs['tracking']['value'] == .24
        pdf = out


@pytest.mark.parametrize('value', [0, .25, True, float('nan')])
def test_confirmation_requires_every_glyph_witness(source, value):
    p = proposal(source, {'P1': {'s0': {'tracking': value}}})
    assert p['status'] == 'refused'
    assert any(r['code'] == 'paragraph-evidence' for r in p['refusals'])


@pytest.mark.parametrize('value', [False, [], '', 0])
def test_confirmation_registry_type_is_strict(source, value):
    with pytest.raises(PdfError, match='source style confirmations'):
        proposal(source, value)


def test_resealed_confirmation_change_is_reobserved(source):
    pdf, _ = source
    p = proposal(source, {'P1': {'s0': {'tracking': .24}}})
    changed = deepcopy(p)
    changed['source_style_confirmations']['P1']['s0']['tracking'] = .25
    with pytest.raises(PdfError, match='stale|cannot be accepted'):
        accept_page_flow_report(pdf, _seal(changed))


def test_across_line_unknown_tracking_cannot_be_confirmed(source, tmp_path):
    pdf, font = source
    changed = tmp_path / 'varying.pdf'
    with pymupdf.open(pdf) as doc:
        xref = doc[0].get_contents()[0]
        data = doc.xref_stream(xref)
        first, remainder = data.split(b'0.24 Tc', 1)
        doc.update_stream(xref, first + b'0.24 Tc' + remainder.replace(b'0.24 Tc', b'0.48 Tc'))
        doc.save(changed)
    p = proposal((changed, font), {'P1': {'s0': {'tracking': .24}}})
    assert p['status'] == 'refused'
    assert any('unknown tracking cannot be confirmed' in r['detail'] for r in p['refusals'])
