"""Keep source decimal positions for later glyphs, without changing layout authority."""
import shutil
import subprocess

import pymupdf
import pytest
from PIL import Image, ImageChops

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.content_stream import ContentPage
from pdfeditor.editable import write_editable, open_editable, edit_document
from pdfeditor.selection import make_selection
from test_attributed import source_pdf
from provider_font import cjk_provider_bytes


def test_source_positions_accumulate_text_operators_without_float32(tmp_path):
    source = source_pdf(tmp_path, b'BT /Regular 12 Tf .24 Tc 1 0 0 1 83.87 200.09 Tm '
        b'[(A) -19 (B) -29 (C)] TJ (D) Tj 10.1 -20.2 Td (E) Tj '
        b'15.3 TL (F) \' 0 .24 (G) " ET')
    content = ContentPage(source, 1)
    try:
        positions = {g.text: g.source_text_matrix[4:] for event in content.events for g in event.chars}
        expected = {
            'A': (83.87, 200.09), 'B': (91.538, 200.09), 'C': (99.326, 200.09),
            'D': (106.766, 200.09), 'E': (93.97, 179.89),
            'F': (93.97, 164.59), 'G': (93.97, 149.29)}
        assert positions.keys() == expected.keys()
        for glyph, position in expected.items():
            assert positions[glyph] == pytest.approx(position, abs=1e-10)
    finally:
        content.close()


def test_later_tj_glyph_uses_source_matrix_only_when_unchanged(tmp_path):
    source = source_pdf(tmp_path, b'BT /Regular 12 Tf .24 Tc 1 0 0 1 83.87 200.09 Tm '
        b'[(A) -19 (B) -29 (C) -19 (D)] TJ ET')
    snapshot = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(4)), explicit_width=150))
    font = tmp_path / 'font.ttf'; font.write_bytes(cjk_provider_bytes())
    pdf, side = tmp_path / 'noop.pdf', tmp_path / 'noop.json'
    write_editable(source, pdf, side, snapshot, [], fonts={'s0': {'path': str(font)}},
        paragraph_style={'s0': {'tracking': .24}}, min_line_height=20, max_bottom=100)
    assert open_editable(pdf, side)['status'] == 'restored'
    content = ContentPage(pdf, 1)
    try:
        c = next(event for event in content.events if any(g.text == 'C' for g in event.chars))
        assert c.text_matrix[4:] == (99.326, 200.09)
    finally:
        content.close()
    with pymupdf.open(source) as a, pymupdf.open(pdf) as b:
        assert all(x.get_pixmap(dpi=144).samples == y.get_pixmap(dpi=144).samples for x, y in zip(a, b))
    poppler = shutil.which('pdftoppm')
    if not poppler:
        pytest.skip('Poppler is required for source-position raster comparison')
    for name, path in [('before', source), ('after', pdf)]:
        subprocess.run([poppler, '-f', '1', '-l', '1', '-singlefile', '-r', '144', '-png',
                        str(path), str(tmp_path / name)], check=True, capture_output=True)
    with Image.open(tmp_path / 'before.png') as a, Image.open(tmp_path / 'after.png') as b:
        assert ImageChops.difference(a.convert('RGB'), b.convert('RGB')).getbbox() is None
    # An insertion moves the retained C. Its old source matrix is no longer
    # authority, even though its original code/GID/font remain retained.
    moved, moved_side = tmp_path / 'moved.pdf', tmp_path / 'moved.json'
    edit_document(pdf, side, moved, moved_side, [dict(start=0, end=1, text='AAAA')])
    assert open_editable(moved, moved_side)['status'] == 'restored'
    content = ContentPage(moved, 1)
    try:
        c = next(g for event in content.events for g in event.chars if g.text == 'C')
        assert c.source_text_matrix[4] > 99.326 + 10
    finally:
        content.close()


def test_tj_accumulated_position_keeps_roundtrip_precision(tmp_path):
    # Ten advances of 12 pt plus the original fractional TJ adjustments are
    # mathematically 126 pt, but the interpreter accumulates just below it.
    atoms = b''.join(b'(' + bytes([65+i]) + b') ' + (b'-35.4 ' if i == 4 else b'-29.4 ')
                     for i in range(10)) + b'(K)'
    source = source_pdf(tmp_path, b'BT /Regular 20 Tf 1 0 0 1 135.5 200.09 Tm [' + atoms + b'] TJ ET')
    snapshot = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(11)), explicit_width=150))
    before = ContentPage(source, 1)
    try:
        expected = [g.source_text_matrix for e in before.events for g in e.chars]
        codes = [(g.code, e.state.font.name) for e in before.events for g in e.chars]
        assert expected[-1][4] == 261.49999999999994
    finally:
        before.close()
    pdf, side = tmp_path / 'precise.pdf', tmp_path / 'precise.json'
    write_editable(source, pdf, side, snapshot, [], min_line_height=25, max_bottom=100)
    assert open_editable(pdf, side)['status'] == 'restored'
    after = ContentPage(pdf, 1)
    try:
        assert [g.source_text_matrix for e in after.events for g in e.chars] == expected
        assert [(g.code, e.state.font.name) for e in after.events for g in e.chars] == codes
    finally:
        after.close()
    with pymupdf.open(source) as a, pymupdf.open(pdf) as b:
        assert a[0].get_pixmap(dpi=144).samples == b[0].get_pixmap(dpi=144).samples
    poppler = shutil.which('pdftoppm')
    if not poppler:
        pytest.skip('Poppler is required for roundtrip-position raster comparison')
    for name, path in [('original-prefix', source), ('retained-position', pdf)]:
        subprocess.run([poppler, '-f', '1', '-l', '1', '-singlefile', '-r', '144', '-png',
                        str(path), str(tmp_path / name)], check=True, capture_output=True)
    with Image.open(tmp_path / 'original-prefix.png') as a, Image.open(tmp_path / 'retained-position.png') as b:
        assert ImageChops.difference(a.convert('RGB'), b.convert('RGB')).getbbox() is None


def test_source_matrix_roundtrip_has_pdf_numeric_syntax():
    from pdfeditor.paragraph import _source_matrix_operator
    from pdfeditor.content_stream import operators
    values = (1.0, 1e-20, -1e-20, 1.0, 1e20, 261.49999999999994)
    serialized = _source_matrix_operator(values)
    assert b'e' not in serialized.lower()
    parsed = list(operators(serialized))
    assert len(parsed) == 1 and parsed[0].name == 'Tm'
    assert tuple(map(float, parsed[0].args)) == values
