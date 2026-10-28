"""B2: a following paragraph moved with its line decoration is found again in the saved PDF alone (issue #101)."""
import json
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject
from test_paragraph_frame_decoration import GROW, SHRINK, candidates, decorated, edit, links
from prototypes.paragraph_frame import auto_conditions as conditions
from prototypes.paragraph_frame.auto import propose
from prototypes.paragraph_frame.auto_detect import page_rows
from prototypes.paragraph_frame.engine import Refused
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import _Analysis


def moved_once(tmp_path, old=GROW[0], new=GROW[1]):
    source = decorated(tmp_path)
    (c,) = candidates(source, old, new)['candidates']
    out = tmp_path / 'moved.pdf'
    edit(source, old, new, out, decorations=[c['id']])
    return source, out


def rows(path, moved=True):
    c = ContentPage(str(path), 1)
    try:
        return c, page_rows(c, observe_marked_content(str(path), 1), _Analysis(str(path), 1), moved)[0]
    except Exception:
        c.close()
        raise


def test_the_saved_pdf_records_the_moved_rows(tmp_path):
    source, out = moved_once(tmp_path)
    c, observed = rows(out, moved=False)
    try:
        (entry,) = conditions.read_moved(c, 1)
        recorded = conditions.resolve_moved(c, entry)
        # The following paragraph's row as observed before the move, where it now is.
        assert [r['text'] for r in recorded] == ['後続段落です。']
        assert recorded[0]['glyph_ids'] == next(r['glyph_ids'] for r in observed if r['text'].startswith('後続'))
    finally:
        c.close()


def test_recorded_rows_replace_rows_that_split_them(tmp_path):
    # Another column's baseline can take a glyph of the moved line: the
    # recorded row is used again when every recorded glyph is drawn as recorded.
    source, out = moved_once(tmp_path)
    c, observed = rows(out, moved=False)
    try:
        row = next(r for r in observed if r['text'].startswith('後続'))
        head, tail = dict(row, glyph_ids=row['glyph_ids'][:3], text=row['text'][:3]), dict(row, glyph_ids=row['glyph_ids'][3:], text=row['text'][3:])
        split = [r for r in observed if r is not row] + [head, tail]
        fixed = conditions.apply_moved(c, 1, split)
        assert [r['text'] for r in fixed if set(r['glyph_ids']) & set(row['glyph_ids'])] == ['後続段落です。']
        # A row that also holds a glyph the record does not name is left as observed.
        other = next(r for r in observed if r is not row)
        mixed = [r for r in observed if r not in (row, other)] + [dict(row, glyph_ids=row['glyph_ids'] + other['glyph_ids'][:1])] + [other]
        assert conditions.apply_moved(c, 1, mixed) == mixed
    finally:
        c.close()


def test_a_record_naming_glyphs_not_drawn_there_is_not_applied(tmp_path):
    source, out = moved_once(tmp_path)
    w = PdfWriter(clone_from=PdfReader(str(out)))
    piece = w.pages[0]['/PieceInfo'][conditions.KEY]['/Private'].get_object()
    data = json.loads(piece.get_data())
    data['moved'][0]['rows'][0]['glyphs'][0][3] += .5  # a glyph moved since the record was written
    stream = DecodedStreamObject()
    stream.set_data(json.dumps(data).encode())
    w.pages[0]['/PieceInfo'][conditions.KEY][NameObject('/Private')] = w._add_object(stream)
    edited = tmp_path / 'edited.pdf'
    w.write(edited)
    c = ContentPage(str(edited), 1)
    try:
        (entry,) = conditions.read_moved(c, 1)
        assert conditions.resolve_moved(c, entry) is None
    finally:
        c.close()


@pytest.mark.parametrize('old,new', [GROW, SHRINK])
def test_restoring_needs_its_own_approval_and_returns_to_the_source(tmp_path, old, new):
    source, out = moved_once(tmp_path, old, new)
    (first,) = candidates(source, old, new)['candidates']
    (again,) = candidates(out, new, old)['candidates']
    # The approval of the edit is not an approval of its restoration.
    assert again['id'] != first['id']
    with pytest.raises(Refused, match='not a candidate of this edit'):
        propose(str(out), 1, new, old, decorations=[first['id']])
    with pytest.raises(Refused, match='moves with it only when approved explicitly'):
        edit(out, new, old, tmp_path / 'unapproved.pdf')
    back = tmp_path / 'back.pdf'
    edit(out, new, old, back, decorations=[again['id']])
    assert links(back) == links(source)
    with pymupdf.open(source) as a, pymupdf.open(back) as b:
        assert a[0].get_pixmap().samples == b[0].get_pixmap().samples
        glyphs = lambda d: sorted((c['c'], round(c['origin'][0], 3), round(c['origin'][1], 3)) for bl in d[0].get_text('rawdict')['blocks']
                                  for l in bl.get('lines', []) for s in l['spans'] for c in s['chars'])
        assert glyphs(a) == glyphs(b)
    # The restoration's own record replaces the edit's: it names the rows where they are now.
    c, _ = rows(back, moved=False)
    try:
        (entry,) = conditions.read_moved(c, 1)
        assert conditions.resolve_moved(c, entry) is not None
    finally:
        c.close()
