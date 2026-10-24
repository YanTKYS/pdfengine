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
