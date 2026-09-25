"""PDF 1.x operator nesting of every content stream pdfengine writes.

A text object (BT ... ET) admits no special graphics state (q, Q, cm); new
text is isolated in its own q BT ... ET Q at the page description level, and
the reopened source text object continues from the original cursor.
"""
from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader

from pdfeditor.backend import PdfError
from pdfeditor.composition import compose_selected
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import OPERATOR_NESTING, _block, _markers
from pdfeditor.elements import inspect_element, move_element
from pdfeditor.operator_nesting import audit, require_text_object_split, text_object_split
from pdfeditor.paragraph import edit_paragraph
from pdfeditor.replay import compare_glyphs, glyph_observations
from pdfeditor.selection import make_selection
from test_attributed import font, snapshot, source_pdf
import test_composition


def assert_conforming(path):
    """Every page program of ``path`` has PDF 1.x operator nesting."""
    for number, page in enumerate(PdfReader(path).pages, 1):
        contents = page.get_contents()
        if contents is not None:
            violations = audit(contents.get_data())['violations']
            assert not violations, (str(path), number, violations[:5])


def header(path):
    return path.read_bytes().split(b'\n', 1)[0].strip()


def assert_saved_revision(source, output, state):
    """A saved shared-flow revision: conforming, same PDF version, strict block records."""
    assert_conforming(output)
    assert header(output) == header(source)
    for binding in state.get('destination_bindings', {}).values():
        if 'end' in binding:
            assert binding['operator_nesting'] == OPERATOR_NESTING


# -- the audit itself ---------------------------------------------------------

@pytest.mark.parametrize('program,kinds', [
    (b'q BT /F 1 Tf (a) Tj ET Q', []),
    (b'BT /P BMC (a) Tj EMC ET /P BMC BT (b) Tj ET EMC', []),
    (b'q BT q (a) Tj Q ET Q', ['special-graphics-state-in-text-object'] * 2),
    (b'BT 1 0 0 1 5 5 cm (a) Tj ET', ['special-graphics-state-in-text-object']),
    (b'q BT Q ET', ['special-graphics-state-in-text-object']),
    (b'BT 0 0 5 5 re f ET', ['operator-not-allowed-in-text-object'] * 2),
    (b'BT BT ET ET', ['nested-text-object', 'ET-without-BT']),
    (b'BT (a) Tj', ['unclosed-text-object']),
    (b'Q q', ['Q-without-q', 'unclosed-q']),
    (b'/P BMC BT EMC ET', ['marked-content-crosses-text-object']),
    (b'BT /P BMC ET EMC', ['marked-content-crosses-text-object'] * 2),
    (b'EMC /P BMC', ['EMC-without-BMC', 'unclosed-marked-content']),
])
def test_audit_reports_pdf1_nesting_violations(program, kinds):
    assert [v['kind'] for v in audit(program)['violations']] == kinds


@pytest.mark.parametrize('program,allowed', [
    (b'BT (a) Tj (b) Tj ET', True),
    (b'BT /P BMC EMC (a) Tj (b) Tj ET', True),
    (b'BT q (a) Tj Q ET', False),
    (b'BT /P BMC (a) Tj EMC ET', False),
    (b'BT BX (a) Tj EX ET', False),
    (b'q (a) Tj Q', False),
])
def test_text_object_split_never_crosses_what_the_text_object_opened(program, allowed):
    offset = program.index(b'Tj') + 2
    if allowed:
        assert text_object_split(program, offset) == (0, len(program))
    else:
        with pytest.raises(PdfError):
            text_object_split(program, offset)


# -- source paragraph rewrite -------------------------------------------------

# Nondefault text state before the edited operator; every following operator
# depends on the cursor (Tm), the line matrix (Td, T*, ', "), TL, Tc, Tw or Tz.
FOLLOWING = (b'BT /Regular 10 Tf 12 TL 0.5 Tc 1.5 Tw 95 Tz 1 Ts 0 0.4 0 rg 0.2 0 0 RG 1 0 0 1 20 200 Tm '
             b'(EDIT ME) Tj (next one) Tj 0 -14 Td (td a) Tj T* (star a) Tj (quote a) \' '
             b'2 1 (dq a) " [(t a) -120 (j)] TJ ET')


def following_events(path):
    # The cursor is restored with a 12-digit numeric TJ; positions agree to
    # about 1e-5 pt, so they are compared at 1e-4 pt.
    content = ContentPage(path, 1)
    try:
        return [dict(text=''.join(c.text for c in e.chars), operator=e.operator.name,
                     codes=[c.code for c in e.chars], origins=[tuple(round(v, 4) for v in c.origin) for c in e.chars],
                     text_matrix=tuple(round(v, 4) for v in e.text_matrix),
                     line_matrix=tuple(round(v, 4) for v in e.line_matrix),
                     state=(e.state.font.name, e.state.size, e.state.tc, e.state.tw, e.state.tz, e.state.ts,
                            e.state.tl, e.state.tr, e.state.fill, e.state.stroke, e.state.ctm))
                for e in content.events if e.chars and e.state.font.name == '/Regular'
                and ''.join(c.text for c in e.chars) != 'EDIT ME']
    finally:
        content.close()


def test_source_rewrite_isolates_new_text_and_keeps_following_cursor_and_state(tmp_path, font):
    source = source_pdf(tmp_path, FOLLOWING)
    before, events = glyph_observations(pymupdf.open(source)[0]), following_events(source)
    assert [e['text'] for e in events] == ['next one', 'td a', 'star a', 'quote a', 'dq a', 't aj']
    output = tmp_path / 'edited.pdf'
    # The source Tc/Ts are confirmed page-point values (Tc*Tz/100, -Ts).
    report = edit_paragraph(source, output, snapshot(source, count=7, width=40), [dict(start=0, end=7, text='NEW')],
                            fonts={'s0': font}, paragraph_style={'s0': dict(tracking=0.475, baseline_shift=-1)},
                            max_bottom=150)
    assert ''.join(g['unicode'] for g in report['glyph_plan']) == 'NEW'
    assert_conforming(output)
    data = PdfReader(output).pages[0].get_contents().get_data()
    assert audit(data)['text_objects'] == 3 and b' ET q BT ' in data and b' ET Q BT ' in data
    # Every following operator: same text/line matrix, font, size, Tc, Tw,
    # Tz, Ts, TL, Tr, colors and CTM, same codes and glyph origins.
    assert following_events(output) == events
    after = glyph_observations(pymupdf.open(output)[0])
    assert compare_glyphs(before[7:], after[3:])['passed']
    assert ''.join(PdfReader(output).pages[0].extract_text().split()).startswith('NEWnextone')
    assert header(output) == header(source)


@pytest.mark.parametrize('program,message', [
    (b'BT /Regular 12 Tf 20 200 Td q (EDIT) Tj Q (KEEP) Tj ET', 'cannot isolate new text'),
    (b'BT /Regular 12 Tf 20 200 Td /Span <</MCID 0>> BDC (EDIT) Tj EMC (KEEP) Tj ET', 'cannot isolate new text'),
    (b'BT /Regular 12 Tf 20 200 Td (EDIT) Tj 7 Tr (CLIP) Tj ET', 'renders text as a clip'),
])
def test_rewrite_refuses_to_cross_what_the_source_text_object_opened(tmp_path, font, program, message):
    source = source_pdf(tmp_path, program)
    output = tmp_path / 'edited.pdf'
    with pytest.raises(PdfError, match=message):
        edit_paragraph(source, output, snapshot(source, count=4, width=60), [dict(start=0, end=4, text='NEW')],
                       fonts={'s0': font}, max_bottom=150)
    assert not output.exists()


def test_empty_style_witness_is_its_own_isolated_text_object(tmp_path):
    from pdfeditor.attributed import inspect_paragraph
    from pdfeditor.destination_style import inline_properties
    from pdfeditor.editable import open_editable, write_editable
    source = source_pdf(tmp_path, b'BT /Regular 12 Tf 20 200 Td (ABCD) Tj (KEEP) Tj ET')
    p = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(4)), explicit_width=25))
    provider = tmp_path / 'font.ttf'
    provider.write_bytes(pymupdf.Font('cjk').buffer)
    out, side = tmp_path / 'empty.pdf', tmp_path / 'empty.json'
    write_editable(source, out, side, p, [dict(start=0, end=4, runs=[])],
                   fonts={'logical:body': dict(path=str(provider))},
                   render_styles={'logical:body': inline_properties(p['styles'][0])},
                   empty_style_id='logical:body', _allow_empty_style_witnesses=True, min_line_height=22, max_bottom=78)
    state = open_editable(out, side)
    assert state['status'] == 'restored'
    assert_conforming(out)
    witnesses = state['state']['paragraph']['style_slot_bindings']
    assert witnesses
    data = PdfReader(out).pages[0].get_contents().get_data()
    ops = list(operators(data))
    for stored in witnesses.values():
        # The witness [] TJ is alone in its own q BT ... ET Q.
        at = next(i for i, op in enumerate(ops) if op.start == stored['event']['byte_range'][0])
        bt = max(i for i in range(at) if ops[i].name == 'BT')
        et = next(i for i in range(at, len(ops)) if ops[i].name == 'ET')
        assert ops[bt - 1].name == 'q' and ops[et + 1].name == 'Q'
        assert [op.name for op in ops[bt + 1:et]] == ['Tf', 'Tz', 'Tc', 'Ts', 'Tm', 'TJ']
    with pymupdf.open(source) as a, pymupdf.open(out) as b:
        assert compare_glyphs(glyph_observations(a[0])[4:], glyph_observations(b[0]))['passed']


# -- PDF version --------------------------------------------------------------

@pytest.mark.parametrize('version', [b'%PDF-1.4', b'%PDF-1.7'])
def test_saving_keeps_the_source_pdf_version(tmp_path, font, version):
    source = source_pdf(tmp_path)
    source.write_bytes(source.read_bytes().replace(b'%PDF-1.3', version, 1))
    output = tmp_path / 'edited.pdf'
    edit_paragraph(source, output, snapshot(source), [dict(start=5, end=9, text='本文')], fonts={'s1': font},
                   max_bottom=150)
    assert header(output) == version
    assert_conforming(output)


# -- the other writers of the same pattern ------------------------------------

@pytest.mark.parametrize('operator', [b'(OLD) Tj', b"(OLD) '", b'0.4 0.2 (OLD) "'])
def test_composition_keeps_graphics_state_outside_text_objects(tmp_path, font, operator):
    source = test_composition.source_pdf(tmp_path, b'BT /F1 12 Tf 18 TL 1 0 0 1 20 168 Tm ' + operator +
                                         b' 160 0 Td (KEEP) Tj ET')
    before = glyph_observations(pymupdf.open(source)[0])
    output = tmp_path / 'composed.pdf'
    compose_selected(source, output, make_selection(source, glyph_ids=[0, 1, 2], explicit_width=100), '提出',
                     font_source=font)
    assert_conforming(output)
    after = glyph_observations(pymupdf.open(output)[0])
    assert compare_glyphs(before[3:], after[-4:])['passed']


def test_element_text_move_keeps_cm_and_graphics_state_outside_text_objects(tmp_path):
    source = source_pdf(tmp_path, b'BT /Regular 12 Tf 14 TL 20 200 Td (AB) Tj (CD) Tj T* (KEEP) Tj ET')
    element = inspect_element(source, make_selection(source, glyph_ids=[0, 1, 2, 3]))
    before = glyph_observations(pymupdf.open(source)[0])
    output = tmp_path / 'moved.pdf'
    move_element(source, output, element, [], dx=0, dy=-30)
    assert_conforming(output)
    after = glyph_observations(pymupdf.open(output)[0])
    for old, new in zip(before[:4], after[:4]):
        assert new['origin'][1] == pytest.approx(old['origin'][1] - 30) and new['origin'][0] == old['origin'][0]
    # The unmoved line below keeps its T*-relative position.
    assert compare_glyphs(before[4:], after[4:])['passed']


# -- continuation blocks ------------------------------------------------------

def test_block_validator_reads_legacy_nesting_only_for_legacy_bindings():
    begin, end = _markers('continuation-0')
    glyph = b' 0 Tc 0 Tw 0 Ts /PRF1 10 Tf 1 0 0 1 20 200 Tm <0001> Tj '
    current = begin + b'q BT' + glyph + b'ET Q\n' + end
    legacy = begin + b'q BT  q' + glyph + b'Q ET Q\n' + end
    assert _block(current, 'continuation-0', 0)[1] == {'/PRF1'}
    assert _block(legacy, 'continuation-0', 0, legacy=True)[1] == {'/PRF1'}
    with pytest.raises(PdfError, match='inside a text object'):
        _block(legacy, 'continuation-0', 0)
    for bad in (begin + b'q BT' + glyph + b'ET Q Q q\n' + end,          # closes its own state early
                begin + b'q BT ET 1 0 0 1 0 0 Tm <0001> Tj Q\n' + end,  # shows text outside BT
                begin + b'q BT' + glyph + b'ET 1 0 0 1 5 5 cm Q\n' + end):
        with pytest.raises(PdfError):
            _block(bad, 'continuation-0', 0)
    assert OPERATOR_NESTING == 'pdf-1.x-text-objects'
