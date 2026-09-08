"""Failures that would otherwise publish visually plausible, incorrect edits."""
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, BooleanObject

from pdfeditor.attributed import EditUnit, SourceUnit, _require_complete_graphemes
from pdfeditor.backend import PdfError
from pdfeditor.paragraph import edit_paragraph
from test_attributed import source_pdf, snapshot


def test_combining_mark_cannot_be_inserted_after_a_retained_base(tmp_path):
    source = source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (e) Tj ET')
    output = tmp_path/'bad.pdf'
    with pytest.raises(PdfError,match='complete grapheme'):
        edit_paragraph(source,output,snapshot(source,count=1),
                       [dict(start=1,end=1,text='\u0301')])
    assert not output.exists()


def test_complete_new_cluster_allowed_but_split_font_providers_refused():
    paragraph = SimpleNamespace(text='e',units=[])
    units = [EditUnit(c,'s0',None,'provided') for c in 'e\u0301']
    _require_complete_graphemes(paragraph,units)
    units[-1].provider = 'another'
    with pytest.raises(PdfError,match='font providers'):
        _require_complete_graphemes(paragraph,units)


def test_deleting_a_break_cannot_silently_join_old_regional_indicators():
    original = [SourceUnit(c,'s0',i,0,char=object()) for i,c in enumerate('\U0001F1EFX\U0001F1F5')]
    paragraph = SimpleNamespace(text=''.join(u.text for u in original),units=original)
    with pytest.raises(PdfError,match='previously separate'):
        _require_complete_graphemes(paragraph,[EditUnit(u.text,'s0',u) for u in (original[0],original[2])])


def test_different_non_inline_overprint_is_not_collapsed_to_first_span(tmp_path):
    source = source_pdf(tmp_path,b'BT /Regular 12 Tf 20 200 Td (AB) Tj /Over gs (CD) Tj ET')
    writer = PdfWriter(clone_from=PdfReader(source))
    writer.pages[0]['/Resources'][NameObject('/ExtGState')] = DictionaryObject({
        NameObject('/Over'): DictionaryObject({NameObject('/op'):BooleanObject(True)})})
    writer.write(source)
    with pytest.raises(PdfError,match='non-inline graphics state'):
        snapshot(source,count=4)


@pytest.mark.parametrize('matrix,tz,trace_size', [('2 0 0 1',100,24),('1 0 0 1',70,8.4)])
def test_trace_size_is_separate_from_vertical_em(tmp_path,matrix,tz,trace_size):
    source = source_pdf(tmp_path,f'BT /Regular 12 Tf {tz} Tz {matrix} 20 200 Tm (ABCD) Tj ET'.encode())
    report = edit_paragraph(source,tmp_path/'saved.pdf',snapshot(source,count=4,width=130),[])
    assert all(g['size']==12 and g['trace_size']==pytest.approx(trace_size) for g in report['glyph_plan'])
