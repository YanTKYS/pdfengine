"""Same-page column flow: continuation evidence, reflow across columns, reopen and refusal."""
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, BooleanObject, DecodedStreamObject, NameObject, NumberObject, TextStringObject
from test_paragraph_frame_continuity import page_with
from test_paragraph_frame_poc import obj
from prototypes.paragraph_frame import auto_conditions as conditions
from prototypes.paragraph_frame.auto import approve, propose, save
from prototypes.paragraph_frame.auto_detect import recognize
from prototypes.paragraph_frame.audit import painting, semantic
from prototypes.paragraph_frame.engine import Refused

COL1, COL2, BOTTOM = 30, 190, 105
P1 = ['本年度の補助事業について', '募集を行います。申請書は', '窓口で配布します。']
P2 = ['交付の決定後に事業を開始', 'してください。実績報告は', '期限までに提出します。']
P3 = ['問い合わせは市役所の担当', '課までお願いします。電話', 'でも受け付けます。']
# P2 is split by the column break: two lines in column 1, one in column 2.
SPLIT = ([(40, P1[0]), (55, P1[1]), (70, P1[2]), (90, P2[0]), (105, P2[1])],
         [(40, P2[2]), (60, P3[0]), (75, P3[1]), (90, P3[2])])
GROW = ('窓口で配布します。', '窓口で配布します。郵送でも受け付けます。')


def columns(tmp_path, col1, col2, *, name='source.pdf', before=None):
    lines = [(COL1, y, t) for y, t in col1] + [(COL2, y, t) for y, t in col2]
    path, _ = page_with(tmp_path, lines, name=name, width=380, height=200, before=before)
    return path


def tag(path, paragraphs, tmp_path, *, style):
    """One /P per paragraph (lists of line indices = content streams).

    ``word``: one /Span with ActualText per line. ``libreoffice``: a custom
    /Standard role-mapped to /P with layout attributes and one /Span owning
    every line's MCID.
    """
    w = PdfWriter(clone_from=PdfReader(path))
    p = w.pages[0]
    root = w._add_object(obj(Type=NameObject('/StructTreeRoot')))
    contents = p['/Contents']
    parents, slots = [], {}
    for lines in paragraphs:
        kind = '/P' if style == 'word' else '/Standard'
        parent = w._add_object(obj(Type=NameObject('/StructElem'), S=NameObject(kind), P=root, Pg=p.indirect_reference))
        if style == 'libreoffice':
            parent.get_object()[NameObject('/A')] = obj(O=NameObject('/Layout'), Placement=NameObject('/Block'))
            leaf = w._add_object(obj(Type=NameObject('/StructElem'), S=NameObject('/Span'), P=parent, Pg=p.indirect_reference,
                                     K=ArrayObject([NumberObject(i) for i in lines])))
            kids = [leaf]
            slots.update({i: leaf for i in lines})
        else:
            kids = []
            for i in lines:
                leaf = w._add_object(obj(Type=NameObject('/StructElem'), S=NameObject('/Span'), P=parent, Pg=p.indirect_reference, K=NumberObject(i)))
                kids.append(leaf)
                slots[i] = leaf
        for i in lines:
            stream = DecodedStreamObject()
            stream.set_data(f'/Span <</MCID {i}>> BDC\n'.encode() + contents[i].get_object().get_data() + b'\nEMC\n')
            contents[i] = w._add_object(stream)
        parent.get_object()[NameObject('/K')] = ArrayObject(kids)
        parents.append(parent)
    root.get_object()[NameObject('/K')] = ArrayObject(parents)
    if style == 'libreoffice':
        root.get_object()[NameObject('/RoleMap')] = obj(Standard=NameObject('/P'))
    root.get_object()[NameObject('/ParentTree')] = w._add_object(obj(Nums=ArrayObject([NumberObject(0), w._add_object(ArrayObject([slots[i] for i in sorted(slots)]))])))
    p[NameObject('/StructParents')] = NumberObject(0)
    w._root_object[NameObject('/StructTreeRoot')] = root
    w._root_object[NameObject('/MarkInfo')] = obj(Marked=BooleanObject(True))
    out = tmp_path / f'tagged-{style}.pdf'
    w.write(out)
    return out


def edit(source, old, new, out, **options):
    proposal = propose(source, 1, old, new, **options)
    save(source, approve(source, proposal, acknowledge_inference=True), out)
    return proposal


def drawn(path):
    """(column, baseline, text) of every drawn line, in column reading order."""
    result = []
    for block in pymupdf.open(path)[0].get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = ''.join(s['text'] for s in line['spans']).strip()
            if text:
                result.append((1 if line['bbox'][0] < COL2 - 5 else 2, round(line['spans'][0]['origin'][1], 2), text))
    return sorted(result)


@pytest.fixture(params=['untagged', 'word', 'libreoffice'])
def split(request, tmp_path):
    path = columns(tmp_path, *SPLIT)
    if request.param != 'untagged':
        path = tag(path, [[0, 1, 2], [3, 4, 5], [6, 7, 8]], tmp_path, style=request.param)
    return path, request.param


def test_split_paragraph_is_a_determined_continuation(split):
    path, kind = split
    links = recognize(str(path), 1)['links']
    assert [(l['status'], l['split']) for l in links] == [('determined', True)]
    assert ('one tagged paragraph' in links[0]['evidence']) == (kind != 'untagged')


def test_growth_moves_following_lines_into_the_next_column_and_back(split, tmp_path):
    path, kind = split
    original = drawn(path)
    grown = tmp_path / 'grown.pdf'
    proposal = edit(path, *GROW, grown)
    assert proposal['evidence']['column_flow']['mode'] == 'flow'
    assert [l['region'] for l in proposal['lines']] == [0, 0, 0, 0]
    lines = drawn(grown)
    assert [t for col, _, t in lines if col == 1] == P1[:2] + ['窓口で配布します。郵送で', 'も受け付けます。', P2[0]]
    assert [t for col, _, t in lines if col == 2] == P2[1:] + P3
    # The flow bottom never grows; protected paint and page count are unchanged.
    assert max(y for _, y, _ in lines) <= BOTTOM + .01
    assert [(y) for col, y, _ in lines if col == 2] == [40, 55, 75, 90, 105]
    before, after = painting(str(path), 1), painting(str(grown), 1)
    assert all(before[k] == after[k] for k in ('drawings', 'images', 'links', 'annots'))
    text = pymupdf.open(grown)[0].get_text('text').replace('\n', '')
    assert text == ''.join(P1[:2]) + GROW[1] + ''.join(P2) + ''.join(P3)
    if kind != 'untagged':
        assert all(semantic(str(grown), 1)['checks'].values())
    # Restoring the text from the saved PDF alone restores the original lines.
    restored = tmp_path / 'restored.pdf'
    again = edit(grown, GROW[1], GROW[0], restored)
    assert again['evidence']['conditions']['status'] == 'applied'
    assert drawn(restored) == original


def test_shortening_pulls_the_next_column_back(split, tmp_path):
    path, _ = split
    short = tmp_path / 'short.pdf'
    edit(path, '募集を行います。申請書は窓口で配布します。', '募集を行います。', short)
    lines = drawn(short)
    assert [t for col, _, t in lines if col == 1] == [P1[0], '募集を行います。'] + P2
    assert [(y, t) for col, y, t in lines if col == 2] == [(40, P3[0]), (55, P3[1]), (70, P3[2])]
    # Column 2 then holds only P3; the stored flow keeps the empty capacity
    # known, so growing again moves P2's tail back into column 2.
    regrown = tmp_path / 'regrown.pdf'
    proposal = edit(short, '募集を行います。', '募集を行います。申請書は窓口で配布します。', regrown)
    assert proposal['evidence']['column_flow']['stored'] is True
    assert drawn(regrown) == drawn(path)


def test_paragraph_boundary_break_is_only_a_candidate_until_approved(tmp_path):
    col1 = [(40, P1[0]), (55, P1[1]), (70, P1[2]), (90, P2[0]), (105, '期限までに提出します。')]
    col2 = [(40, P3[0]), (55, P3[1]), (70, P3[2])]
    path = columns(tmp_path, col1, col2)
    links = recognize(str(path), 1)['links']
    assert [l['status'] for l in links] == ['candidate']
    with pytest.raises(Refused, match='candidate continuation.*' + links[0]['id'].replace('>', '.')):
        propose(path, 1, *GROW)
    out = tmp_path / 'approved.pdf'
    proposal = edit(path, *GROW, out, continuation=[links[0]['id']])
    assert proposal['evidence']['column_flow']['links'][0]['status'] == 'approved'
    assert [t for col, _, t in drawn(out) if col == 2] == ['期限までに提出します。'] + P3
    # The approval is stored with the flow: reopening needs no new approval.
    back = tmp_path / 'back.pdf'
    edit(out, GROW[1], GROW[0], back)
    assert drawn(back) == drawn(path)


@pytest.mark.parametrize('blocker', ['heading', 'image'])
def test_unrelated_or_blocked_next_column_is_never_used(tmp_path, blocker):
    col1 = [(40, P1[0]), (55, P1[1]), (70, P1[2]), (90, P2[0]), (105, '期限までに提出します。')]
    if blocker == 'heading':
        def before(page):
            page.insert_text((COL2, 42), '別の記事の見出し', fontsize=16, fontname='helv')
        col2 = [(65, P3[0]), (80, P3[1]), (95, P3[2])]
    else:
        def before(page):
            page.draw_rect(pymupdf.Rect(COL2, 28, COL2 + 130, 58), color=None, fill=(.6, .7, .8))
        col2 = [(75, P3[0]), (90, P3[1]), (105, P3[2])]
    path = columns(tmp_path, col1, col2, before=before)
    assert [l['status'] for l in recognize(str(path), 1)['links']] in ([], ['rejected'])
    with pytest.raises(Refused, match='capacity: .*shared bottom.*no continuation column is linked'):
        propose(path, 1, *GROW)


def test_growth_beyond_every_column_is_refused_without_shrinking(split):
    path, _ = split
    with pytest.raises(Refused, match='capacity: the column flow is full'):
        propose(path, 1, GROW[0], GROW[0] + '郵送でも受け付けます。' * 12)


def test_external_paint_in_a_stored_region_makes_the_flow_stale(tmp_path):
    path = columns(tmp_path, *SPLIT)
    short = tmp_path / 'short.pdf'
    edit(path, '募集を行います。申請書は窓口で配布します。', '募集を行います。', short)
    changed = tmp_path / 'changed.pdf'
    d = pymupdf.open(short)
    d[0].draw_rect(pymupdf.Rect(COL2, 80, COL2 + 100, 110), color=None, fill=(.9, .2, .2))
    d.save(changed)
    proposal = propose(changed, 1, '募集を行います。', '募集を行います。')
    assert proposal['evidence']['column_flow']['mode'] == 'single-column'
    assert any('stored approved column flow was not applied: protected paint entered' in w for w in proposal['evidence']['warnings'])
    # Without the stored flow the emptied column is not a proven continuation.
    with pytest.raises(Refused, match='capacity'):
        propose(changed, 1, '募集を行います。', '募集を行います。申請書は窓口で配布します。')


def _page(path):
    from pdfeditor.content_stream import ContentPage
    return ContentPage(str(path), 1), 1


def test_flow_records_are_validated(tmp_path):
    path = columns(tmp_path, *SPLIT)
    grown = tmp_path / 'grown.pdf'
    edit(path, *GROW, grown)
    c, page = _page(grown)
    try:
        records, status = conditions.read(c, page)
    finally:
        c.close()
    assert status == 'present' and [r['kind'] for r in records] == ['flow']
    record = records[0]
    assert {l['paragraph'] for l in record['lines']} == {0, 1, 2}
    assert conditions.valid(record, 380, 200)
    for broken in ({**record, 'regions': []}, {**record, 'lines': record['lines'][::-1]},
                   {**record, 'conditions': {**record['conditions'], 'pitch': -1}}):
        assert not conditions.valid(broken, 380, 200)
