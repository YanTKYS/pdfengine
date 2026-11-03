"""Focused tests of the route PoC (issue #122). Not in the main suite (testpaths = tests):

  .venv/bin/python -m pytest -q experiments/route_poc/test_retypeset.py
"""
import pymupdf

from experiments.route_poc import evaluate, retypeset

LINES = ['The quick brown fox jumps over the lazy', 'dog and keeps running far away.']


def _source(path, below=True):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    for k, line in enumerate(LINES):
        page.insert_text((40, 60 + 14 * k), line, fontname='helv', fontsize=11)
    if below:
        page.insert_text((40, 60 + 14 * 3), 'Next paragraph stays.', fontname='helv', fontsize=11)
    page.draw_rect(pymupdf.Rect(20, 200, 120, 240), color=(0, 0, 1))
    doc.save(path)
    return path


def _bare(t):
    return ''.join(t.split())


def _refused(r, match):
    assert r['outcome'] == 'refused' and match in r['reason'], r


def test_same_length_edit_replaces_text_without_painting_over(tmp_path):
    src, out = _source(tmp_path / 'a.pdf'), tmp_path / 'b.pdf'
    _refused(retypeset.edit(str(src), str(out), 1, [60, 57], 'quick', 'rapid'), 'fallback font')
    r = retypeset.edit(str(src), str(out), 1, [60, 57], 'quick', 'rapid', approve_font=True)
    assert r['outcome'] == 'saved' and r['approvals'] and r['approvals'][0].startswith('fallback')
    with pymupdf.open(out) as d:
        page = d[0]
        text = page.get_text()
        assert 'quick' not in text and page.search_for('rapid brown')
        assert 'Nextparagraphstays.' in _bare(text)
        # Nothing painted over: the drawings are the source's (one stroked rectangle).
        assert [x['rect'] for x in page.get_drawings()] == [pymupdf.Rect(20, 200, 120, 240)]
        assert not page.get_images()


def test_growth_over_following_text_is_refused_and_writes_nothing(tmp_path):
    src, out = _source(tmp_path / 'a.pdf'), tmp_path / 'b.pdf'
    _refused(retypeset.edit(str(src), str(out), 1, [60, 57], 'quick', 'quick and very very very very very quick', approve_growth=True, approve_font=True), 'would grow over text')
    assert not out.exists()


def test_growth_into_empty_space_needs_approval(tmp_path):
    src, out = _source(tmp_path / 'a.pdf', below=False), tmp_path / 'b.pdf'
    longer = 'quick and very very very very very quick'
    _refused(retypeset.edit(str(src), str(out), 1, [60, 57], 'quick', longer, approve_font=True), 'needs approval')
    r = retypeset.edit(str(src), str(out), 1, [60, 57], 'quick', longer, approve_growth=True, approve_font=True)
    assert r['outcome'] == 'saved' and len(r['lines']) > len(LINES)
    assert any(a.startswith('grows down') for a in r['approvals'])
    with pymupdf.open(out) as d:
        assert _bare(longer) in _bare(d[0].get_text())


def test_selection_outside_the_clicked_paragraph_is_refused(tmp_path):
    src, out = _source(tmp_path / 'a.pdf'), tmp_path / 'b.pdf'
    _refused(retypeset.edit(str(src), str(out), 1, [60, 57], 'Next', 'Last', approve_font=True), 'not in the clicked paragraph')


def test_evaluator_keeps_a_neighbouring_glyph_whose_box_reaches_into_the_area(tmp_path):
    """The glyph check places glyphs by origin (S-I1: a tall glyph of the next line removed by the redaction)."""
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((40, 100), 'X', fontname='helv', fontsize=40)
    area = [pymupdf.Rect(30, 60, 100, 75)]  # above the baseline, inside the glyph's box
    assert sum(evaluate._glyphs(page, area).values()) == 1


def test_source_font_name_of_a_step():
    assert evaluate.source_font(dict(font='embedded (MS-Gothic)')) == 'MS-Gothic'
    assert evaluate.source_font(dict(font='fallback (Droid Sans Fallback Regular) for IPAPGothic')) == 'IPAPGothic'


def test_an_edit_that_removes_a_glyph_of_the_next_line_is_refused_and_not_written(tmp_path):
    """S-I1's failure: a tall glyph of the next line whose box overlaps the paragraph is removed by the redaction."""
    src, out = tmp_path / 'a.pdf', tmp_path / 'b.pdf'
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    page.insert_text((40, 60), 'The quick brown fox jumps over.', fontname='helv', fontsize=11)
    page.insert_text((62, 84), '|', fontname='helv', fontsize=24)  # its box reaches up into "quick"
    page.insert_text((80, 84), 'next', fontname='helv', fontsize=11)
    doc.save(src)
    out.write_bytes(b'an earlier output')
    r = retypeset.edit(str(src), str(out), 1, [60, 57], 'quick', 'rapid', approve_font=True)
    _refused(r, 'unsafe, not written')
    assert r['unsafe'] and r['audit']['glyphs_lost'] and r['audit']['pixels_outside'] > 0
    assert out.read_bytes() == b'an earlier output'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['a.pdf', 'b.pdf']


def test_a_safe_edit_passes_the_audit_before_it_is_published(tmp_path):
    src, out = _source(tmp_path / 'a.pdf'), tmp_path / 'b.pdf'
    r = retypeset.edit(str(src), str(out), 1, [60, 57], 'quick', 'rapid', approve_font=True)
    assert r['outcome'] == 'saved' and r['audit']['failed'] == [] and r['audit']['pixels_outside'] == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ['a.pdf', 'b.pdf']


def test_a_tagged_page_is_refused_before_anything_is_written(tmp_path):
    """S-W2's failure: on a tagged page the new lines would be untagged."""
    src, out = _source(tmp_path / 'a.pdf'), tmp_path / 'b.pdf'
    with pymupdf.open(src) as doc:
        xref = doc[0].get_contents()[0]
        doc.update_stream(xref, b'/P <</MCID 0>> BDC\n' + doc.xref_stream(xref) + b'\nEMC\n')
        doc.save(tmp_path / 't.pdf')
    _refused(retypeset.edit(str(tmp_path / 't.pdf'), str(out), 1, [60, 57], 'quick', 'rapid', approve_font=True), 'the page is tagged')
    assert not out.exists()
