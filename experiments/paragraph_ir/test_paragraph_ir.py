"""Focused tests of the ParagraphIR PoC (issue #124). Not in the main suite (testpaths = tests):

  .venv/bin/python -m pytest -q experiments/paragraph_ir/test_paragraph_ir.py

Fixtures are written per test into tmp_path with the deterministic CJK subset of
tests/provider_font.py (kana and Latin only).
"""
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
from provider_font import cjk_provider_bytes  # noqa: E402

from experiments.paragraph_ir import adapter  # noqa: E402
from experiments.paragraph_ir.ir import Refused  # noqa: E402

LINES = ['あいうえおかきくけこさしすせそ', 'たちつてとなにぬねのはひふへほ', 'まみむめも。']


def _doc(lines=LINES, below=None, extra=None):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.insert_font(fontname='prov', fontbuffer=cjk_provider_bytes())
    for k, line in enumerate(lines):
        page.insert_text((40, 60 + 16 * k), line, fontname='prov', fontsize=12)
    if below:
        page.insert_text((40, 60 + 16 * len(lines)), below, fontname='prov', fontsize=12)
    if extra:
        extra(doc, page)
    return doc


def _save(doc, path):
    doc.save(path)
    return path


def _bare(t):
    return ''.join(t.split())


def _pixels(a, b):
    with pymupdf.open(a) as x, pymupdf.open(b) as y:
        return sum(p != q for p, q in zip(x[0].get_pixmap().samples, y[0].get_pixmap().samples))


def test_two_edits_on_one_import_then_undo(tmp_path):
    src = _save(_doc(below='やゆよ'), tmp_path / 'a.pdf')
    ir = adapter.import_paragraph(src, 1, [45, 56], 'かき')
    imported = ir.snapshot()
    a, b = ir.selection
    rec = ir.replace(a, b, 'カキカキ')
    lines = ir.layout()
    # Overflow moves to the next lines; the paragraph keeps three lines (the last had room).
    assert [ir.text[l.start:l.end] for l in lines][-1].endswith('まみむめも。') and len(lines) == 3
    r1 = adapter.write(ir, src, tmp_path / 'e1.pdf')
    assert r1['outcome'] == 'saved' and r1['audit']['failed'] == []
    with pymupdf.open(tmp_path / 'e1.pdf') as d:
        assert 'カキカキくけこ' in _bare(d[0].get_text()) and 'やゆよ' in _bare(d[0].get_text())
    # The second edit is made on the same IR (no import): typing the old text back gives its source characters.
    ir.replace(rec['at'], rec['at'] + rec['inserted'], rec['old'])
    assert ir.snapshot() == imported
    r2 = adapter.write(ir, src, tmp_path / 'e2.pdf')
    assert r2['outcome'] == 'saved' and r2['drawn_lines'] == 0 and _pixels(src, tmp_path / 'e2.pdf') == 0
    ir.undo()
    ir.undo()
    assert ir.snapshot() == imported


def test_pdf_alone_reads_the_bound_state_and_restores(tmp_path):
    src = _save(_doc(below='やゆよ'), tmp_path / 'a.pdf')
    ir = adapter.import_paragraph(src, 1, [45, 56], 'かき')
    a, b = ir.selection
    ir.replace(a, b, 'カ')
    adapter.write(ir, src, tmp_path / 'e1.pdf')
    again = adapter.import_paragraph(tmp_path / 'e1.pdf', 1, [45, 56], 'カ')
    assert again.relations['state_in_pdf'].startswith('frame read from the PDF') and 'edit history' in again.relations['state_in_pdf']
    a, b = again.selection
    again.replace(a, b, 'かき')
    assert adapter.write(again, tmp_path / 'e1.pdf', tmp_path / 'back.pdf')['outcome'] == 'saved'
    assert _pixels(src, tmp_path / 'back.pdf') == 0


def test_a_state_not_bound_to_the_drawn_glyphs_is_ignored(tmp_path):
    src = _save(_doc(), tmp_path / 'a.pdf')
    ir = adapter.import_paragraph(src, 1, [45, 56], 'かき')
    a, b = ir.selection
    ir.replace(a, b, 'カ')
    adapter.write(ir, src, tmp_path / 'e1.pdf')
    with pymupdf.open(tmp_path / 'e1.pdf') as d:
        rec = adapter._read_state(d, d[0])
        rec[-1]['glyphs'][0][1] += 3  # a glyph the record names is not where it is drawn
        xref = int(d.xref_get_key(d[0].xref, adapter.STATE_KEY)[1].split()[0])
        d.update_stream(xref, __import__('json').dumps(dict(paragraphs=rec)).encode())
        d.save(tmp_path / 'stale.pdf')
    again = adapter.import_paragraph(tmp_path / 'stale.pdf', 1, [45, 56], 'カ')
    assert again.relations['state_in_pdf'].endswith('ignored') and again.history == []


def test_glyphs_of_a_shared_operator_outside_the_paragraph_stay(tmp_path):
    """One TJ draws two columns; editing the left one removes only its glyphs (displacement keeps the right one)."""
    font = pymupdf.Font(fontbuffer=cjk_provider_bytes())
    code = lambda t: ''.join(f'{font.has_glyph(ord(c)):04x}' for c in t)

    def shared(doc, page):
        page.insert_text((40, 30), 'た', fontname='prov', fontsize=12)  # a content stream to append to
        xref = page.get_contents()[0]
        doc.update_stream(xref, doc.xref_stream(xref) + f'\nBT /prov 12 Tf 1 0 0 1 40 100 Tm [<{code("あいう")}> -8000 <{code("かきく")}>] TJ ET\n'.encode())
    src = _save(_doc(lines=[], extra=shared), tmp_path / 'a.pdf')
    ir = adapter.import_paragraph(src, 1, [45, 196], 'あい')
    assert ir.text == 'あいう'
    a, b = ir.selection
    ir.replace(a, b, 'アイ')
    r = adapter.write(ir, src, tmp_path / 'e1.pdf')
    assert r['outcome'] == 'saved' and r['shared_operators'] == 1 and r['audit']['glyphs_lost'] == []
    with pymupdf.open(tmp_path / 'e1.pdf') as d:
        t = _bare(d[0].get_text())
    assert 'アイう' in t and 'かきく' in t and 'あい' not in t


def test_a_tagged_line_is_redrawn_inside_its_marked_content(tmp_path):
    def tag(doc, page):
        xref = page.get_contents()[0]
        doc.update_stream(xref, b'/P <</MCID 0>> BDC\n' + doc.xref_stream(xref) + b'\nEMC\n')
    src = _save(_doc(extra=tag), tmp_path / 'a.pdf')
    ir = adapter.import_paragraph(src, 1, [45, 56], 'かき')
    a, b = ir.selection
    ir.replace(a, b, 'カキ')
    r = adapter.write(ir, src, tmp_path / 'e1.pdf')
    assert r['outcome'] == 'saved' and r['audit']['mcids']
    from pdfeditor.content_stream import ContentPage
    cp = ContentPage(str(tmp_path / 'e1.pdf'), 1)
    try:
        st = adapter._structure(cp)
        drawn = [e for e in cp.events if any(c.text == 'カ' for c in e.chars)]
        assert drawn and all(any('/MCID' in ''.join(args) and "0" in ''.join(args) for _, args in st[e.operator.start]) for e in drawn)
    finally:
        cp.close()


def test_a_collision_is_refused_and_nothing_is_written(tmp_path):
    def box(doc, page):
        page.draw_rect(pymupdf.Rect(40, 98, 260, 110), color=(0, 0, 1))
    src = _save(_doc(extra=box), tmp_path / 'a.pdf')
    ir = adapter.import_paragraph(src, 1, [45, 56], 'かき')
    a, b = ir.selection
    ir.replace(a, b, 'カキカキカキカキカキカキカキ')
    out = tmp_path / 'e1.pdf'
    out.write_bytes(b'an earlier output')
    with pytest.raises(Refused, match='needs approval|would meet drawing'):
        adapter.write(ir, src, out)
    with pytest.raises(Refused, match='would meet drawing'):
        adapter.write(ir, src, out, approve_growth=True)
    assert out.read_bytes() == b'an earlier output'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['a.pdf', 'e1.pdf']


def test_growth_into_empty_space_needs_approval(tmp_path):
    src = _save(_doc(), tmp_path / 'a.pdf')
    ir = adapter.import_paragraph(src, 1, [45, 56], 'かき')
    a, b = ir.selection
    ir.replace(a, b, 'カキカキカキカキカキカキカキ')
    with pytest.raises(Refused, match='needs approval'):
        adapter.write(ir, src, tmp_path / 'e1.pdf')
    assert not (tmp_path / 'e1.pdf').exists()
    r = adapter.write(ir, src, tmp_path / 'e1.pdf', approve_growth=True)
    assert r['outcome'] == 'saved' and r['approvals'] and len(r['lines']) == 4


def test_a_character_the_font_resource_cannot_encode_is_refused():
    src_doc = _doc()
    data = src_doc.tobytes()
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / 'a.pdf'
        p.write_bytes(data)
        ir = adapter.import_paragraph(p, 1, [45, 56], 'かき')
        a, b = ir.selection
        with pytest.raises(Refused, match='has no code for'):
            ir.replace(a, b, '龥')
