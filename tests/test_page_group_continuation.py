"""Page-group defaults never authorize Form groups or ambiguous PDF flags."""
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, DictionaryObject, NameObject, NumberObject

from pdfeditor.backend import PdfError
from pdfeditor.continuation import inspect_continuation_boundaries, slot_id
from test_boundary_destination import build, SOURCE_PAGE, DESTINATION_PAGE
from test_continuation import prepared, saved, change, LONG


def decorate(path, flags):
    writer = PdfWriter(clone_from=PdfReader(path))
    group = DictionaryObject({NameObject('/S'): NameObject('/Transparency'),
        NameObject('/Type'): NameObject('/Group'), NameObject('/CS'): NameObject('/DeviceRGB')})
    group.update({NameObject(k): v for k, v in flags.items()})
    writer.pages[1][NameObject('/Group')] = group
    writer.write(path)


@pytest.mark.parametrize('flags', [{}, {'/I': BooleanObject(True), '/K': BooleanObject(False)}])
def test_known_page_group_can_inspect_and_save_continuation(tmp_path, flags):
    boundary_source = build(tmp_path / 'boundary', SOURCE_PAGE, DESTINATION_PAGE)
    decorate(boundary_source, flags)
    assert inspect_continuation_boundaries(boundary_source, 2)['candidates']
    source, state = prepared(tmp_path, decorate=lambda path: decorate(path, flags))
    pdf, grown, _ = saved(tmp_path, source, state, change(state, LONG), 'grown')
    ident = slot_id(grown['continuation_destinations']['empty'])
    assert grown['slots'][ident]['occupancy'] is not None
    again, restored, _ = saved(tmp_path, pdf, grown, {}, 'noop')
    assert restored['continuation_destinations'] == grown['continuation_destinations']
    with pymupdf.open(pdf) as a, pymupdf.open(again) as b:
        assert all(x.get_pixmap(dpi=144).samples == y.get_pixmap(dpi=144).samples for x, y in zip(a, b))
    assert PdfReader(source).pages[1]['/Group'] == PdfReader(again).pages[1]['/Group']


@pytest.mark.parametrize('flags', [
    {'/I': BooleanObject(False)}, {'/K': BooleanObject(True)},
    {'/I': NumberObject(1)}, {'/K': NumberObject(0)},
    {'/CS': NameObject('/UnknownColor')}, {'/Unexpected': BooleanObject(False)},
])
def test_page_group_flags_remain_strict(tmp_path, flags):
    source = build(tmp_path, SOURCE_PAGE, DESTINATION_PAGE)
    decorate(source, flags)
    with pytest.raises(PdfError, match='group'):
        inspect_continuation_boundaries(source, 2)
