"""Paragraph and row inference: lines moved by an approved chain on reopen (R13), hanging indents (S2) (issue #110)."""
import pymupdf
import pytest
from provider_font import cjk_provider_bytes
from prototypes.paragraph_frame.auto import approve, propose, save
from prototypes.paragraph_frame.auto_detect import detect, group_text, observe
from prototypes.paragraph_frame.engine import Refused
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import _Analysis

# A list, one item per line: a smaller label raised a little, then a name (as
# 府政だより477 p4 draws it). The item to edit draws as many label as name
# glyphs: its line's size (the median of its glyphs') differs from the others.
ITEMS = [('D-22', ' 国際連合'), ('D-23', ' ベトナム'), ('D-24', ' モザンビーク'), ('D-25', ' ペルー'), ('D-26', ' ヨルダン'), ('D-27', ' 団体休憩所')]
LIST_GROW = ('D-25 ペ', 'D-25 ペD-25 ペルー')
# Body text, then footnotes: a number, the note's text after a tab, its next line at the text's x.
BODY = [(40, 60, '本年度の補助事業について、募集を行います。申請'), (40, 76, '書は窓口で配布します。')]
NOTE = '補助事業の対象は、府内に事務所を置く団体とし'
NOTE_END = 'ます。詳しくは窓口へ。'
NOTE_GROW = ('詳しくは窓口へ。', '詳しくは窓口へ。申請書は窓口と郵送と電子申請とファクスで受け付けます。')


def page(tmp_path, items, name='source.pdf', width=300):
    """``items``: (x, y, text, size) drawn with the test font, each by its own text object."""
    data = cjk_provider_bytes()
    path = tmp_path / name
    d = pymupdf.open()
    p = d.new_page(width=width, height=300)
    p.insert_font(fontname='Fixture', fontbuffer=data)
    for x, y, text, size in items:
        p.insert_text((x, y), text, fontsize=size, fontname='Fixture')
    d.save(path)
    d.close()
    return path


def listing(tmp_path, name='list.pdf'):
    font = pymupdf.Font(fontbuffer=cjk_provider_bytes())
    items = []
    for k, (label, text) in enumerate(ITEMS):
        y = 100 + 10.3 * k
        items += [(40, y - .26, label, 6.23), (40 + font.text_length(label, 6.23), y, text, 7.36)]
    return page(tmp_path, items, name)


def footnotes(tmp_path, notes, name='notes.pdf', body=BODY, size=11):
    """``body`` lines, then per note (number, first line, next lines (x, text), y): the number at 40, its text at 57."""
    items = [(x, y, t, size) for x, y, t in body]
    for number, first, rest, y in notes:
        items += [(40, y, number, size), (57, y, first, size)] + [(x, y + 15 * (k + 1), t, size) for k, (x, t) in enumerate(rest)]
    return page(tmp_path, items, name)


def groups(path, page_number=1):
    c = ContentPage(str(path), page_number)
    try:
        _, _, found = observe(c, observe_marked_content(str(path), page_number), _Analysis(str(path), page_number))
        return [[dict(text=''.join(c.actual[i]['unicode'] for i in r['glyph_ids']).strip(), x=round(r['x'], 2), baseline=round(r['baseline'], 2),
                      moved=r.get('moved'), moved_as=r.get('moved_as')) for r in g] for g in found]
    finally:
        c.close()


def texts(path):
    return [[r['text'] for r in g] for g in groups(path)]


def origins(path):
    with pymupdf.open(path) as d:
        return sorted((c['c'], round(c['origin'][0], 2), round(c['origin'][1], 2)) for b in d[0].get_text('rawdict')['blocks']
                      for l in b.get('lines', []) for s in l['spans'] for c in s['chars'] if c['c'].strip())


def edit(source, out, old, new, chain=None):
    """Save ``old`` -> ``new`` approving ``chain`` (default: every listed candidate); (proposal, save record)."""
    p = propose(str(source), 1, old, new)
    ids = [c['id'] for c in (p['evidence'].get('chain') or {}).get('candidates', [])] if chain is None else chain
    p = propose(str(source), 1, old, new, chain=ids)
    accepted = approve(str(source), p, acknowledge_inference=True)
    return accepted['proposal'], save(str(source), accepted, str(out))


# --- moved lines on reopen (R13) ----------------------------------------------

@pytest.fixture
def grown(tmp_path):
    source = listing(tmp_path)
    out = tmp_path / 'grown.pdf'
    proposal, record = edit(source, out, *LIST_GROW)
    return source, out, proposal, record


def test_the_list_item_is_one_paragraph_and_the_items_below_a_chain(tmp_path):
    source = listing(tmp_path)
    assert ['D-25 ペルー'] in texts(source)
    p = propose(str(source), 1, *LIST_GROW)
    assert p['paragraphs'] == ['D-25 ペルー'] and [c['paragraph'] for c in p['evidence']['chain']['candidates']] == ['D-26 ヨルダンD-27 団体休憩所']
    with pytest.raises(Refused):
        save(str(source), approve(str(source), p, acknowledge_inference=True), str(tmp_path / 'unapproved.pdf'))


def test_moved_lines_are_not_grouped_with_the_grown_paragraph(grown):
    source, out, proposal, record = grown
    assert len(record['chain_moved']) == 1 and [l['text'] for l in proposal['lines']] == ['D-25 ペ', 'D-25 ペ', 'ルールー']
    found = groups(out)
    moved = [g for g in found if any(r['moved'] is not None for r in g)]
    # The moved lines form their own group, as recorded; the paragraph's new lines are apart from them.
    assert [[r['text'] for r in g] for g in moved] == [['D-26 ヨルダン', 'D-27 団体休憩所']]
    assert all(r['moved'] == moved[0][0]['moved'] and r['moved_as'] == 'chain' for r in moved[0])
    assert not any({'ルールー', 'D-26 ヨルダン'} <= {r['text'] for r in g} for g in found)


def test_the_grown_text_is_found_again_and_the_moved_lines_need_approval_to_go_back(grown, tmp_path):
    source, out, proposal, record = grown
    c, paragraphs, spec, start, evidence = detect(str(out), 1, LIST_GROW[1])
    try:
        assert len(paragraphs) == 1 and paragraphs[0].text == 'D-25 ペD-25 ペルールー' and evidence['conditions']['status'] == 'applied'
    finally:
        c.close()
    back = propose(str(out), 1, LIST_GROW[1], LIST_GROW[0])
    # Moved as an approved following element, the lines are not the paragraph's followers now:
    # moving them back is a chain candidate again.
    assert back['paragraphs'] == ['D-25 ペD-25 ペルールー']
    assert [c['paragraph'] for c in back['evidence']['chain']['candidates']] == ['D-26 ヨルダンD-27 団体休憩所']
    # Not approved: nothing below moves (the lines stay where the edit moved them).
    kept = tmp_path / 'kept.pdf'
    save(str(out), approve(str(out), back, acknowledge_inference=True), str(kept))
    assert [o for o in origins(kept) if o[2] > 158] == [o for o in origins(out) if o[2] > 158]


def test_the_edit_back_restores_every_glyph(grown, tmp_path):
    source, out, proposal, record = grown
    back = tmp_path / 'back.pdf'
    _, saved = edit(out, back, *reversed(LIST_GROW))
    assert len(saved['chain_moved']) == 1
    # The edited item's two baselines (label and name) and the moved items: where they were (0.01 pt).
    assert origins(back) == origins(source)
    assert texts(back) == texts(source)


# --- hanging indents (S2) ------------------------------------------------------

def test_a_footnote_continues_at_its_hanging_indent(tmp_path):
    source = footnotes(tmp_path, [('1', NOTE, [(57, NOTE_END)], 130)])
    assert ['1' + NOTE, NOTE_END] in [[r.replace(' ', '') for r in g] for g in texts(source)]
    c, paragraphs, spec, start, evidence = detect(str(source), 1, NOTE_GROW[0])
    try:
        assert (spec['first_x'], spec['left'], spec['line_height']) == (40, 57, 15)
        assert 'hanging indent' in evidence['conditions']['provenance']['left']
    finally:
        c.close()


def test_added_lines_start_at_the_hanging_indent_and_go_back(tmp_path):
    source = footnotes(tmp_path, [('1', NOTE, [(57, NOTE_END)], 130)])
    out = tmp_path / 'out.pdf'
    proposal, record = edit(source, out, *NOTE_GROW)
    assert [l['baseline'] for l in proposal['lines']] == [130, 145, 160]
    # The proposal names the lines the hanging indent joined, and every line as it will be laid out.
    hanging = proposal['evidence']['inferred']['hanging']
    assert hanging['label'] == '1' and [(l['x'], l['baseline']) for l in hanging['lines']] == [(40, 130), (57, 145)]
    assert [(l['x'], l['baseline']) for l in hanging['proposed']] == [(40, 130), (57, 145), (57, 160)]
    assert any('hanging indent' in a for a in proposal['required_approval'])
    # From the saved PDF alone: one paragraph, its added line at the indent; the edit back restores the source.
    assert [[(r['x'], r['baseline']) for r in g] for g in groups(out) if g[0]['baseline'] >= 130] == [[(40, 130), (57, 145), (57, 160)]]
    back = tmp_path / 'back.pdf'
    edit(out, back, *reversed(NOTE_GROW))
    assert origins(back) == origins(source)


@pytest.mark.parametrize('notes, body, expected', [
    # A heading-like short line (not broken at the edge) with a line below at its text's x.
    ([('1', '目的', [(57, NOTE)], 130)], BODY, [['1目的'], [NOTE]]),
    # The next line starts at another indent than the text after the number.
    ([('1', NOTE, [(50, NOTE_END)], 130)], BODY, [['1' + NOTE], [NOTE_END]]),
    # Two notes: the second number, back at the first one's x, starts the next note.
    ([('1', NOTE, [(57, NOTE_END)], 130), ('2', NOTE, [(57, NOTE_END)], 175)], BODY,
     [['1' + NOTE, NOTE_END], ['2' + NOTE, NOTE_END]]),
])
def test_what_is_no_hanging_indent_stays_apart(tmp_path, notes, body, expected):
    source = footnotes(tmp_path, notes, body=body)
    found = [[r.replace(' ', '') for r in g] for g in texts(source)]
    assert found[-len(expected):] == expected


def test_a_line_drawn_across_a_gutter_is_no_hanging_indent(tmp_path):
    # Two notes side by side: the first row of the left one runs into the right column's first row (one drawn row).
    left, right = '補助事業の対象', '府内の団体'
    source = page(tmp_path, [(40, 60, '1', 11), (57, 60, left, 11), (57 + 11 * len(left) + 7, 60, right, 11), (57, 75, NOTE_END, 11)])
    found = [[r.replace(' ', '') for r in g] for g in texts(source)]
    assert [NOTE_END] in found and not any(NOTE_END in g and len(g) > 1 for g in found)


def test_notes_in_two_columns_hang_in_their_own_column(tmp_path):
    a, b = '補助事業の対象', '申請書の提出先'
    items = []
    for x, text in ((20, a), (170, b)):
        # A body line (it sets the column's right edge), then the note.
        items += [(x, 100, '本年度の補助事業につい', 11), (x, 130, '1', 11), (x + 17, 130, text + 'は', 11), (x + 17, 145, 'です。', 11)]
    source = page(tmp_path, items)
    found = [[(r['x'], r['text'].replace(' ', '')) for r in g] for g in groups(source) if g[0]['baseline'] >= 130]
    assert found == [[(20, '1' + a + 'は'), (37, 'です。')], [(170, '1' + b + 'は'), (187, 'です。')]]


def test_a_one_line_note_names_its_possible_indent_and_keeps_the_first_line_x(tmp_path):
    # No next line shows the indent: it is reported, not applied.
    source = footnotes(tmp_path, [('1', NOTE, [], 130)])
    c, paragraphs, spec, start, evidence = detect(str(source), 1, NOTE[:6])
    try:
        assert spec['left'] == 40 and evidence['inferred']['hanging_candidate']['x'] == 57
        assert any('hanging indent' in w for w in evidence['warnings'])
    finally:
        c.close()
