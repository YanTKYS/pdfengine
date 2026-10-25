"""Local layout: an edit keeps what it does not change where it was drawn (auto_local)."""
import pymupdf
from pypdf import PdfReader
from test_paragraph_frame_continuity import page_with
from prototypes.paragraph_frame.auto import approve, propose, save

LINES = [(40, 60, '本年度の補助事業について、募集を'), (40, 76, '行います。申請書は窓口で配布し'), (40, 92, 'ます。'), (40, 130, '後続段落です。')]


def edit(source, old, new, out):
    proposal = propose(str(source), 1, old, new)
    save(str(source), approve(str(source), proposal, acknowledge_inference=True), str(out))
    return proposal


def origins(path):
    with pymupdf.open(path) as d:
        return [(c['c'], round(c['origin'][0], 3), round(c['origin'][1], 3)) for b in d[0].get_text('rawdict')['blocks']
                for l in b.get('lines', []) for s in l['spans'] for c in s['chars'] if c['c'].strip()]


def streams(path):
    page = PdfReader(str(path)).pages[0]
    contents = page['/Contents']
    refs = contents if isinstance(contents, list) else [contents]
    return [ref.get_object().get_data() for ref in refs]


def test_same_text_keeps_every_glyph_where_it_was(tmp_path):
    source, _ = page_with(tmp_path, LINES, height=300)
    out = tmp_path / 'out.pdf'
    proposal = edit(source, '窓口で', '窓口で', out)
    assert proposal['layout'] == 'local' and all(l['kept'] for l in proposal['lines'])
    assert origins(out) == origins(source)


def test_shortening_keeps_the_lines_it_does_not_change_as_drawn(tmp_path):
    source, _ = page_with(tmp_path, LINES, height=300)
    out = tmp_path / 'out.pdf'
    proposal = edit(source, '申請書は', '', out)
    # Line 1 is untouched: same glyphs, origins and operators.
    assert proposal['lines'][0]['kept'] and not proposal['lines'][1]['kept']
    assert streams(source)[0].strip() in b''.join(streams(out))
    first = [g for g in origins(source) if g[2] == 60.0]
    assert [g for g in origins(out) if g[2] == 60.0] == first
    # The shortened line takes up the rest of the paragraph (a gap wider than
    # the paragraph's own ragged lines); the edited text reads in order.
    assert [l['text'] for l in proposal['lines']] == ['本年度の補助事業について、募集を', '行います。窓口で配布します。']


def test_lengthening_stops_at_the_author_break(tmp_path):
    # Line 2 ends short although the next line's first character would fit:
    # the author broke the line there; reflow never crosses it.
    lines = [(40, 60, '本年度の補助事業について、募集を'), (40, 76, '行います。'), (40, 92, '申請書は窓口で配布します。'), (40, 130, '後続段落です。')]
    source, _ = page_with(tmp_path, lines, height=300)
    out = tmp_path / 'out.pdf'
    proposal = edit(source, '募集を', '募集を必ず', out)
    assert [l['text'] for l in proposal['lines']] == ['本年度の補助事業について、募集を', '必ず行います。', '申請書は窓口で配布します。']
    assert [l['kept'] for l in proposal['lines']] == [True, False, True]
    kept = lambda path: [g for g in origins(path) if g[2] in (60.0, 92.0)]
    assert kept(out) == kept(source)
    # The third line keeps its operators too.
    assert streams(source)[2].strip() in b''.join(streams(out))


def test_a_saved_edit_is_stable_when_edited_again(tmp_path):
    source, _ = page_with(tmp_path, LINES, height=300)
    first, again = tmp_path / 'first.pdf', tmp_path / 'again.pdf'
    edit(source, '申請書は', '', first)
    proposal = edit(first, '窓口で', '窓口で', again)
    assert proposal['evidence']['conditions']['status'] == 'applied'
    assert origins(again) == origins(first)


def test_a_table_cell_gives_its_text_the_width_inside_its_rules(tmp_path):
    # One line of text in a cell drawn by rules, 4 pt from the left rule: it
    # may grow to 4 pt before the right rule, not only to its own end (where
    # it would wrap).
    import pytest
    lines = [(44, 60, '申請書は窓口で配布します。'), (44, 130, '後続段落です。')]

    def cell(p):
        p.draw_rect(pymupdf.Rect(40, 48, 260, 66), color=(0, 0, 0), width=.5)
    source, _ = page_with(tmp_path, lines, height=300, before=cell)
    plain, _ = page_with(tmp_path, lines, height=300, name='plain.pdf')
    wrapped = edit(plain, '配布します。', '配布しています。', tmp_path / 'plain-out.pdf')
    assert len(wrapped['lines']) == 2
    proposal = edit(source, '配布します。', '配布しています。', tmp_path / 'out.pdf')
    assert proposal['evidence']['inferred']['right'] == pytest.approx(256, abs=.3)
    assert [l['text'] for l in proposal['lines']] == ['申請書は窓口で配布しています。']


def test_a_line_ending_with_a_hyphen_keeps_its_break(tmp_path):
    # The hyphen may be soft or hard: the shortened line keeps its break
    # (its gap is not filled from the next line) and the next lines stay.
    lines = [(40, 60, 'Calc is a spreadsheet program for'), (40, 76, 'the user in mind and easy for profes-'),
             (40, 92, 'sional users and number crunchers.'), (40, 130, 'Following paragraph.')]
    source, _ = page_with(tmp_path, lines, height=300)
    out = tmp_path / 'out.pdf'
    proposal = edit(source, 'in mind and ', '', out)
    assert [l['text'].strip() for l in proposal['lines']] == ['Calc is a spreadsheet program for', 'the user easy for profes-',
                                                              'sional users and number crunchers.']
    assert [l['kept'] for l in proposal['lines']] == [True, False, True]


def test_a_cell_width_stops_at_the_clip_of_its_text(tmp_path):
    # The cell's rules allow 256 pt, but the text is drawn under a clip that
    # ends at 200 (a form cell): ink beyond it would not show. The line wraps
    # inside the clip instead of being refused there.
    import pytest
    def cell(p):
        p.draw_rect(pymupdf.Rect(40, 48, 260, 100), color=(0, 0, 0), width=.5)
    source, _ = page_with(tmp_path, [(44, 60, '申請書は窓口で配布します。')], height=300, before=cell)
    clipped = tmp_path / 'clipped.pdf'
    with pymupdf.open(source) as d:
        text = d[0].get_contents()[-1]
        d.update_stream(text, b'q 40 200 160 52 re W n\n' + d.xref_stream(text) + b'\nQ')
        d.save(clipped)
    proposal = edit(clipped, '配布します。', '配布しています。', tmp_path / 'out.pdf')
    assert proposal['evidence']['inferred']['right'] == pytest.approx(199.5, abs=.3)
    assert [l['text'] for l in proposal['lines']] == ['申請書は窓口で配布していま', 'す。']


def test_undoing_an_edit_across_a_line_break_restores_the_lines(tmp_path):
    # 'specific page or position' wraps 'position' to the next line; undoing it
    # aligns the 'p' of 'page' with the 'p' of 'position'. The old line start
    # before 'osition' is no longer a break: the word is not split there.
    lines = [(40, 60, 'There are two types of links in a'), (40, 76, 'document: a link to a specific position'),
             (40, 92, 'in the same document, and a link to an'), (40, 108, 'external document or a website.'),
             (40, 200, 'Following paragraph.')]
    source, _ = page_with(tmp_path, lines, height=300)
    first, again = tmp_path / 'first.pdf', tmp_path / 'again.pdf'
    edit(source, 'specific position', 'specific page or position', first)
    proposal = edit(first, 'specific page or position', 'specific position', again)
    assert [l['text'].strip() for l in proposal['lines']] == [t for _, _, t in lines[:4]]
    assert origins(again) == origins(source)


def test_an_edited_line_still_continues_the_kept_lines_on_reopen(tmp_path):
    # The source sets its black as '0 0 0 rg'; the redrawn last line writes
    # '0.0 0.0 0.0 rg'. The same colour: the reopened page, read from its
    # drawing alone (no stored record), still has one paragraph of three lines.
    lines = [(40, 60, 'Cumulative effects of'), (40, 76, 'changes in accounting'), (40, 92, 'policies'),
             (40, 200, 'Following paragraph.')]
    plain, _ = page_with(tmp_path, lines, height=300)
    source = tmp_path / 'black.pdf'
    with pymupdf.open(plain) as d:
        for xref in d[0].get_contents():
            d.update_stream(xref, b'0 0 0 rg\n' + d.xref_stream(xref))
        d.save(source)
    out = tmp_path / 'out.pdf'
    proposal = edit(source, 'policies', 'policies in effect', out)
    assert [l['kept'] for l in proposal['lines']] == [True, True, False]
    again = propose(str(out), 1, 'effects of changes in accounting policies in effect', 'effects of changes in accounting policies',
                    stored_conditions=False)
    assert len(again['evidence']['observed']['lines']) == 3


def test_the_same_text_on_the_edge_of_its_clip_is_not_refused(tmp_path):
    # The clip the text is drawn under ends inside the glyph boxes (a table
    # cell cutting the descender space): the frame stops at the clip, but the
    # glyphs drawn again where they were add no ink beyond it.
    lines = [(44, 60, '申請書は窓口で配布します。')]
    plain, _ = page_with(tmp_path, lines, height=300)
    clipped = tmp_path / 'clipped.pdf'
    from pdfeditor.content_stream import ContentPage
    page = ContentPage(str(plain), 1)
    bottom = max(g['bbox'][3] for g in page.actual)
    with pymupdf.open(plain) as d:
        text = d[0].get_contents()[-1]
        clip = f'q 40 {300 - bottom + .2:.2f} 200 40 re W n\n'.encode()
        d.update_stream(text, clip + d.xref_stream(text) + b'\nQ')
        d.save(clipped)
    out = tmp_path / 'out.pdf'
    proposal = edit(clipped, '窓口で', '窓口で', out)
    assert proposal['evidence']['inferred']['frame'][3] < bottom
    assert origins(out) == origins(clipped)


def kerned(tmp_path, lines, name='kerned.pdf', every=3, adjust=-40, spacing=0):
    """``page_with`` lines drawn with TJ kerning: every ``every``-th glyph is set
    ``adjust`` thousandths of an em closer, so advances differ from nominal widths;
    ``spacing``: character spacing (Tc) of every line."""
    import re
    plain, _ = page_with(tmp_path, lines, height=300)
    out = tmp_path / name
    with pymupdf.open(plain) as d:
        def kern(m):
            codes = [m.group(1)[i:i + 4] for i in range(0, len(m.group(1)), 4)]
            parts = [b'<' + c + b'>' + (b' %d ' % adjust if (n + 1) % every == 0 and n + 1 < len(codes) else b'') for n, c in enumerate(codes)]
            return b'[' + b''.join(parts) + b']TJ'
        for xref in d[0].get_contents():
            data = re.sub(rb'\[<([0-9a-f]+)>\]TJ', kern, d.xref_stream(xref))
            d.update_stream(xref, data.replace(b'BT\n', b'BT\n%g Tc\n' % spacing) if spacing else data)
        d.save(out)
    return out


def page_text(path):
    with pymupdf.open(path) as d:
        return d[0].get_text()


def test_undoing_an_edit_restores_the_recorded_layout(tmp_path):
    # Kerned source lines: re-inserted glyphs at their nominal widths would not
    # land where they were. The paragraph's layout recorded at the first save
    # brings every glyph, line break and the page text back (issue #93).
    lines = [(40, 60, 'There are two types of links in a'), (40, 76, 'document: a link to a specific position'),
             (40, 92, 'in the same document, and a link to an'), (40, 108, 'external document or a website.'),
             (40, 200, 'Following paragraph.')]
    source = kerned(tmp_path, lines)
    first, again = tmp_path / 'first.pdf', tmp_path / 'again.pdf'
    edit(source, 'specific position', 'specific page or position', first)
    proposal = edit(first, 'specific page or position', 'specific position', again)
    assert origins(again) == origins(source)
    assert page_text(again) == page_text(source)
    assert proposal['evidence']['conditions']['status'] == 'applied'
    assert any(l.get('restored') for l in proposal['lines'])


def test_an_edit_applied_again_after_its_undo_draws_the_same(tmp_path):
    lines = [(40, 60, 'There are two types of links in a'), (40, 76, 'document: a link to a specific position'),
             (40, 92, 'in the same document, and a link to an'), (40, 108, 'external document or a website.'),
             (40, 200, 'Following paragraph.')]
    source = kerned(tmp_path, lines)
    a, b, c = tmp_path / 'a.pdf', tmp_path / 'b.pdf', tmp_path / 'c.pdf'
    edit(source, 'two types', 'several types', a)
    edit(a, 'several types', 'two types', b)
    edit(b, 'two types', 'several types', c)
    assert origins(b) == origins(source)
    assert origins(c) == origins(a)


def test_undoing_a_wrap_does_not_pull_up_a_paragraph_that_did_not_follow(tmp_path):
    # The edit wraps a fifth line; the next paragraph, 42 pt below at first,
    # is now near enough to count as a follower. The undo shrinks the
    # paragraph back: that paragraph stays where it always was (it was no
    # follower when the layout was recorded), and the page is as before.
    lines = [(40, 60, 'There are two types of links in a'), (40, 76, 'document: a link to a specific position'),
             (40, 92, 'in the same document, and a link to an'), (40, 108, 'external document or a website.'),
             (40, 150, 'Following paragraph.')]
    source, _ = page_with(tmp_path, lines, height=300)
    first, again = tmp_path / 'first.pdf', tmp_path / 'again.pdf'
    edit(source, 'specific position', 'specific page or position', first)
    edit(first, 'specific page or position', 'specific position', again)
    assert origins(again) == origins(source)
    assert page_text(again) == page_text(source)


def test_a_recorded_layout_is_used_only_when_well_formed():
    from prototypes.paragraph_frame.auto_restore import follower_shift, valid
    layout = dict(text='ab c', glyphs=[['a', 10., 50., 5., 3, '/F1', '61'], ['b', 15., 50., None, 4, '/F1', '62'], None, ['c', 10., 66., None, 5, '/F1', '63']],
                  rows=[[0, 2, 50., 10.], [3, 4, 66., 10.]], below=dict(baseline=100., follower=False))
    assert valid(layout, 300, 300)
    for broken in (dict(layout, text='abc'), dict(layout, rows=[[0, 3, 50., 10.]]), dict(layout, rows=[[0, 2, 400., 10.]]),
                   dict(layout, glyphs=[['x', 10., 50., 5., 3, '/F1', '78']] + layout['glyphs'][1:]),
                   dict(layout, glyphs=[['a', 10., 50., 5., 3, '/F1', 'zz']] + layout['glyphs'][1:]), dict(layout, below=dict(baseline='x', follower=False))):
        assert not valid(broken, 300, 300)
    # A block that was no follower is not pulled above where it was; a
    # follower keeps its gap; growing moves both.
    assert follower_shift(layout, -16., 100.) == 0.
    assert follower_shift(layout, -16., 110.) == -10.
    assert follower_shift(dict(layout, below=dict(baseline=100., follower=True)), -16., 100.) == -16.
    assert follower_shift(layout, 16., 100.) == 16.


def test_text_with_character_spacing_reads_the_same_after_it_is_redrawn(tmp_path):
    # The source sets its lines with character spacing (Tc). A redrawn line
    # keeps it (the pen positions are the same either way): an edit applied
    # again after its undo draws exactly what it drew the first time.
    lines = [(40, 60, 'There are two types of links in a'), (40, 76, 'document: a link to a specific position'),
             (40, 92, 'in the same document, and a link to an'), (40, 108, 'external document or a website.'),
             (40, 200, 'Following paragraph.')]
    source = kerned(tmp_path, lines, every=1000, spacing=-.4)
    a, b, c = tmp_path / 'a.pdf', tmp_path / 'b.pdf', tmp_path / 'c.pdf'
    edit(source, 'specific position', 'specific page or position', a)
    edit(a, 'specific page or position', 'specific position', b)
    edit(b, 'specific position', 'specific page or position', c)
    assert origins(b) == origins(source)
    assert origins(c) == origins(a)
