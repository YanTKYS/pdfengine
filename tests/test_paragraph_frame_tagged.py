"""B2 on tagged PDFs: block elements, shared MCIDs, cross-span edits, atoms, placed objects, markers, structure proof."""
import re
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject, TextStringObject)
from provider_font import cjk_provider_bytes
from test_paragraph_frame_continuity import page_with
from prototypes.paragraph_frame import semantics
from prototypes.paragraph_frame.auto import approve, propose, save
from prototypes.paragraph_frame.audit import painting, semantic
from prototypes.paragraph_frame.engine import Refused
from pdfeditor.marked_content import observe_marked_content

LINES = [(40, 60, '本年度の補助事業について、募集を'), (40, 76, '行います。申請書は窓口で配布し'), (40, 92, 'ます。'), (40, 130, '後続段落です。')]
FIRST = '本年度の補助事業について、募集を行います。申請書は窓口で配布します。'


def element(w, role, parent, page, *, typed=True, **entries):
    d = DictionaryObject({NameObject('/S'): NameObject(role), NameObject('/P'): parent, NameObject('/Pg'): page})
    if typed:
        d[NameObject('/Type')] = NameObject('/StructElem')
    for k, v in entries.items():
        d[NameObject('/' + k)] = v
    return w._add_object(d)


def tagged(tmp_path, blocks, *, lines=LINES, tree=True, name='tagged.pdf', root=None, edit_streams=None):
    """Page ``lines`` (one content stream each) tagged as ``blocks``.

    A block is dict(role, kids, within=(), typed=True, entries={}); a kid is a
    stream index (a direct MCID) or dict(streams=[...], role='/Span', entries={})
    (an inline leaf). Without ``tree`` the MCIDs stay but no StructTreeRoot is written.
    """
    plain, font = page_with(tmp_path, lines, height=300)
    w = PdfWriter(clone_from=PdfReader(plain))
    p = w.pages[0]
    page = p.indirect_reference
    contents = p['/Contents']
    data = [ref.get_object().get_data() for ref in contents]
    if edit_streams:
        data = edit_streams(data)
    root_ref = w._add_object(DictionaryObject({NameObject('/Type'): NameObject('/StructTreeRoot')}))
    owners, refs, mcid = [], [], 0
    for block in blocks:
        parent = root_ref
        for role in block.get('within', ()):
            parent = element(w, role, parent, page)
            parent.get_object()[NameObject('/K')] = ArrayObject()
            refs.append(parent) if parent.get_object()['/P'] is root_ref else None
        b = element(w, block['role'], parent, page, typed=block.get('typed', True), **block.get('entries', {}))
        if parent is root_ref:
            refs.append(b)
        else:
            parent.get_object()['/K'].append(b)
        kids = ArrayObject()
        for kid in block['kids']:
            if isinstance(kid, int):
                data[kid] = f'/{block.get("tag", "P")} <</MCID {mcid}>> BDC\n'.encode() + data[kid] + b'\nEMC\n'
                kids.append(NumberObject(mcid)); owners.append(b); mcid += 1
                continue
            leaf = element(w, kid.get('role', '/Span'), b, page, **kid.get('entries', {}))
            items = ArrayObject()
            for s in kid['streams']:
                data[s] = f'/Span <</MCID {mcid}>> BDC\n'.encode() + data[s] + b'\nEMC\n'
                items.append(NumberObject(mcid)); owners.append(leaf); mcid += 1
            leaf.get_object()[NameObject('/K')] = items[0] if len(items) == 1 else items
            kids.append(leaf)
        b.get_object()[NameObject('/K')] = kids
    for n, ref in enumerate(contents):
        stream = DecodedStreamObject()
        stream.set_data(data[n])
        contents[n] = w._add_object(stream)
    if tree:
        r = root_ref.get_object()
        r[NameObject('/K')] = ArrayObject(refs)
        r[NameObject('/ParentTree')] = w._add_object(DictionaryObject({NameObject('/Nums'): ArrayObject([NumberObject(0), w._add_object(ArrayObject(owners))])}))
        for k, v in (root or {}).items():
            r[NameObject(k)] = v(w, refs) if callable(v) else v
        p[NameObject('/StructParents')] = NumberObject(0)
        w._root_object[NameObject('/StructTreeRoot')] = root_ref
        w._root_object[NameObject('/MarkInfo')] = DictionaryObject({NameObject('/Marked'): BooleanObject(True)})
    out = tmp_path / name
    w.write(out)
    return out, font


def edit(source, old, new, out, **options):
    proposal = propose(str(source), 1, old, new, **options)
    result = save(str(source), approve(str(source), proposal, acknowledge_inference=True), str(out))
    return proposal, result


def drawn_text(path):
    with pymupdf.open(path) as d:
        return d[0].get_text().replace(' ', '').replace('\n', '')


def mcid_texts(path):
    """Text drawn in each MCID sequence of page 1 and the owner's ActualText and Lang."""
    from pdfeditor.content_stream import ContentPage
    marks = observe_marked_content(str(path), 1)
    c = ContentPage(str(path), 1)
    try:
        rows = {}
        for s in marks['scopes']:
            a = s['association']
            if a.get('mcid') is None:
                continue
            lo, hi = s['content_byte_range']
            text = ''.join(ch.text for e in c.events if e.stream_xref == s['stream_xref'] and lo <= e.operator.start < hi for ch in e.chars)
            owner = c.reader.get_object(a['owner_reference']['xref']) if a.get('owner_reference') else {}
            rows[a['mcid']] = (text, str(owner['/ActualText']) if '/ActualText' in owner else None, str(owner['/Lang']) if '/Lang' in owner else None)
        return rows
    finally:
        c.close()


def structure_unchanged(source, out, allowed=0):
    changed, other = semantics.changes(semantics.canonical(PdfReader(str(source))), semantics.canonical(PdfReader(str(out))))
    assert not other and len(changed) <= allowed, (changed, other)


def test_canonical_structure_is_stable_across_rewriting(tmp_path):
    source, _ = tagged(tmp_path, [dict(role='/P', kids=[0, 1, 2]), dict(role='/P', kids=[3])])
    w = PdfWriter(clone_from=PdfReader(source))
    w.write(tmp_path / 'clone.pdf')
    structure_unchanged(source, tmp_path / 'clone.pdf')


def test_table_cell_owning_its_mcids_without_type_is_edited(tmp_path):
    """Distiller: /TD elements (no /Type) own their MCIDs directly, inside /Table /TR."""
    source, _ = tagged(tmp_path, [dict(role='/TD', kids=[0, 1, 2], typed=False, within=('/Table', '/TR')),
                                  dict(role='/TD', kids=[3], typed=False)])
    out = tmp_path / 'out.pdf'
    proposal, _ = edit(source, '申請書は窓口で', '申請書は各窓口で', out)
    assert proposal['evidence']['observed']['semantic_parents'][0] is not None
    assert drawn_text(out) == drawn_text(source).replace('申請書は窓口で', '申請書は各窓口で')
    rows = mcid_texts(out)
    assert sorted(rows) == [0, 1, 2, 3] and ''.join(rows[k][0] for k in (0, 1, 2)) == FIRST.replace('窓口', '各窓口')
    structure_unchanged(source, out)
    assert all(semantic(str(out), 1)['checks'].values())
    again, _ = edit(out, '申請書は各窓口で', '申請書は窓口で', tmp_path / 'again.pdf')
    assert again['evidence']['conditions']['status'] == 'applied'


def test_shared_mcid_is_a_container_for_one_of_its_paragraphs(tmp_path):
    """One MCID draws two visual paragraphs (a table cell): the edit stays inside that one sequence."""
    def one_sequence(data):
        return [b'/P <</MCID 0>> BDC\n' + data[0]] + data[1:3] + [data[3] + b'\nEMC\n']
    source, _ = tagged(tmp_path, [dict(role='/TD', kids=[])], edit_streams=one_sequence)
    # Bind the sequence: the cell owns MCID 0.
    w = PdfWriter(clone_from=PdfReader(source))
    cell = w._root_object['/StructTreeRoot']['/K'][0].get_object()
    cell[NameObject('/K')] = ArrayObject([NumberObject(0)])
    w._root_object['/StructTreeRoot']['/ParentTree'].get_object()['/Nums'][1].get_object().append(cell.indirect_reference)
    w.write(tmp_path / 'shared.pdf')
    source = tmp_path / 'shared.pdf'
    out = tmp_path / 'out.pdf'
    edit(source, '申請書は窓口で', '申請書は各窓口で', out)
    data = PdfReader(out).pages[0].get_contents().get_data()
    assert data.count(b'MCID 0') == 1
    rows = mcid_texts(out)
    assert rows[0][0].replace(' ', '') == (FIRST + '後続段落です。').replace('窓口', '各窓口')
    structure_unchanged(source, out)


def test_edit_across_spans_keeps_unchanged_characters_in_their_spans(tmp_path):
    """Word: /Span leaves with ActualText and /Lang; the replacement crosses two of them."""
    texts = [t for _, _, t in LINES]
    spans = [dict(streams=[n], entries=dict(ActualText=TextStringObject(texts[n]), Lang=TextStringObject('ja'))) for n in (0, 1, 2)]
    source, _ = tagged(tmp_path, [dict(role='/P', kids=spans), dict(role='/P', kids=[3])])
    out = tmp_path / 'out.pdf'
    # 募集を | 行います: the inserted words belong to the first span, the second span is unchanged.
    edit(source, '募集を行います', '募集を必ず行います', out)
    rows = mcid_texts(out)
    assert rows[0][0].endswith('募集を必ず') and rows[1][0].startswith('行います')
    for k in (0, 1, 2):
        text, actual, lang = rows[k]
        assert actual == text and lang == 'ja'
    structure_unchanged(source, out, allowed=1)
    # Characters changed in two spans stay in their own spans.
    edit(out, '必ず行います', '必ず実施します', tmp_path / 'two.pdf')
    rows = mcid_texts(tmp_path / 'two.pdf')
    assert rows[0][0].endswith('必ず') and rows[1][0].startswith('実施します') and all(rows[k][1] == rows[k][0] for k in (0, 1, 2))


def test_mcids_without_a_structure_tree_are_containers(tmp_path):
    source, _ = tagged(tmp_path, [dict(role='/P', kids=[0, 1, 2], tag='Figure'), dict(role='/P', kids=[3])], tree=False)
    assert '/StructTreeRoot' not in PdfReader(source).trailer['/Root']
    out = tmp_path / 'out.pdf'
    edit(source, '申請書は窓口で', '申請書は各窓口で', out)
    assert drawn_text(out) == drawn_text(source).replace('申請書は窓口で', '申請書は各窓口で')
    data = PdfReader(out).pages[0].get_contents().get_data()
    assert data.count(b'BDC') == 4


def _atom(data, glyphs=1):
    """The first ``glyphs`` glyphs of line 2 in an unmarked /Span ActualText sequence."""
    m = re.search(rb'(1 0 0 1 [\d.]+ [\d.]+ Tm\n/Fixture 11 Tf )\[<([0-9a-f]+)>\]TJ', data[1])
    head, hexes = m.group(1), m.group(2)
    split = 4 * glyphs
    data[1] = data[1][:m.start()] + head + b'/Span <</ActualText <FEFF0007>>> BDC [<' + hexes[:split] + b'>]TJ EMC [<' + hexes[split:] + b'>]TJ' + data[1][m.end():]
    return data


def test_replacement_text_marker_is_kept_atomic(tmp_path):
    # InDesign without tags: no MCIDs, a marker sequence inside the line.
    source, _ = tagged(tmp_path, [], tree=False, edit_streams=_atom)
    out = tmp_path / 'out.pdf'
    edit(source, '申請書は窓口で', '申請書は各窓口で', out)
    data = PdfReader(out).pages[0].get_contents().get_data()
    m = re.search(rb'/Span <</ActualText <FEFF0007>>> BDC(.*?)EMC', data, re.S)
    assert m and len(re.findall(rb'<[0-9a-f]{4}>', m.group(1))) == 1
    # Replacing the marked glyph removes the sequence with it.
    edit(source, '行います', '実施します', tmp_path / 'gone.pdf')
    assert b'ActualText' not in PdfReader(tmp_path / 'gone.pdf').pages[0].get_contents().get_data()


def test_replacement_text_marker_is_never_split(tmp_path):
    source, _ = tagged(tmp_path, [], tree=False, edit_streams=lambda d: _atom(d, glyphs=2))
    with pytest.raises(Refused, match='splits replacement-text marked content'):
        propose(str(source), 1, 'います', 'う')


def _placed(data):
    """The first glyph of line 2 drawn as an inline placed object with its own clip."""
    m = re.search(rb'q\nBT\n1 0 0 1 ([\d.]+) ([\d.]+) Tm\n/Fixture 11 Tf \[<([0-9a-f]{4})([0-9a-f]+)>\]TJ\nET\nQ', data[1])
    x, y, first, rest = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
    placed = (f'/PlacedPDF BMC q {x - 1} {y - 3} 13 14 re W n BT 1 0 0 1 {x:g} {y:g} Tm /Fixture 11 Tf [<'.encode() + first
              + b'>]TJ ET Q EMC\n' + f'q BT 1 0 0 1 {x + 11:g} {y:g} Tm /Fixture 11 Tf [<'.encode() + rest + b'>]TJ ET Q')
    data[1] = data[1][:m.start()] + placed + data[1][m.end():]
    return data


def test_inline_placed_object_stays_where_it_is(tmp_path):
    source, _ = tagged(tmp_path, [], tree=False, edit_streams=_placed)
    out = tmp_path / 'out.pdf'
    edit(source, '申請書は窓口で', '申請書は窓口と郵送で', out)
    before = PdfReader(source).pages[0].get_contents().get_data()
    after = PdfReader(out).pages[0].get_contents().get_data()
    block = re.search(rb'/PlacedPDF BMC.*?EMC', before, re.S).group(0)
    assert after.count(block) == 1
    assert drawn_text(out) == drawn_text(source).replace('申請書は窓口で', '申請書は窓口と郵送で')
    with pytest.raises(Refused, match='removes an inline placed object'):
        propose(str(source), 1, 'を行います', 'をします')
    with pytest.raises(Refused, match='inline placed object would move'):
        propose(str(source), 1, '本年度の補助事業について、募集を行', '募集を行')


def test_id_tree_and_element_ids_are_kept(tmp_path):
    def idtree(w, refs):
        return w._add_object(DictionaryObject({NameObject('/Names'): ArrayObject([TextStringObject('p1'), refs[0]])}))
    source, _ = tagged(tmp_path, [dict(role='/P', kids=[0, 1, 2], entries=dict(ID=TextStringObject('p1'))), dict(role='/P', kids=[3])],
                       root={'/IDTree': idtree})
    out = tmp_path / 'out.pdf'
    edit(source, '申請書は窓口で', '申請書は各窓口で', out)
    structure_unchanged(source, out)
    assert semantics.canonical(PdfReader(out))['ids'] == semantics.canonical(PdfReader(source))['ids']


@pytest.mark.parametrize('kind', ['alt', 'figure'])
def test_unprovable_structure_is_refused(tmp_path, kind):
    blocks = {'alt': [dict(role='/P', kids=[0, 1, 2], entries=dict(Alt=TextStringObject('description'))), dict(role='/P', kids=[3])],
              'figure': [dict(role='/Figure', kids=[0, 1, 2]), dict(role='/P', kids=[3])]}[kind]
    source, _ = tagged(tmp_path, blocks)
    with pytest.raises(Refused, match={'alt': 'unsupported structure entries /Alt', 'figure': 'unsupported structure role /Figure'}[kind]):
        propose(str(source), 1, '申請書は窓口で', '申請書は各窓口で')


def test_cells_on_one_line_are_separate_paragraphs(tmp_path):
    # Adjacent cells (4 pt apart): only the structure separates them.
    lines = [(40, 60, '申請期間'), (88, 60, '四月一日から五月末日まで'), (40, 100, '後続段落です。')]
    source, _ = tagged(tmp_path, [dict(role='/TD', kids=[0]), dict(role='/TD', kids=[1]), dict(role='/P', kids=[2])], lines=lines)
    with pytest.raises(Refused, match='occurrence not found'):
        propose(str(source), 1, '期間四月', '期間：四月')
    out = tmp_path / 'out.pdf'
    edit(source, '五月末日まで', '六月末日まで', out)
    rows = mcid_texts(out)
    assert rows[0][0] == '申請期間' and rows[1][0] == '四月一日から六月末日まで'


def test_nonzero_mediabox_origin(tmp_path):
    plain, _ = page_with(tmp_path, LINES, height=300)
    reference = tmp_path / 'reference.pdf'
    edit(plain, '申請書は窓口で', '申請書は各窓口で', reference)
    w = PdfWriter(clone_from=PdfReader(plain))
    p = w.pages[0]
    p[NameObject('/MediaBox')] = ArrayObject([FloatObject(-12), FloatObject(12), FloatObject(288), FloatObject(312)])
    for ref in p['/Contents']:
        s = ref.get_object()
        s.set_data(b'q 1 0 0 1 -12 12 cm\n' + s.get_data() + b'\nQ\n')
    w.write(tmp_path / 'shifted.pdf')
    out = tmp_path / 'out.pdf'
    edit(tmp_path / 'shifted.pdf', '申請書は窓口で', '申請書は各窓口で', out)

    def glyphs(path):
        with pymupdf.open(path) as d:
            return [(c['c'], round(c['origin'][0], 2), round(c['origin'][1], 2)) for b in d[0].get_text('rawdict')['blocks']
                    for l in b.get('lines', []) for s in l['spans'] for c in s['chars']]
    assert glyphs(out) == glyphs(reference)


def _gstates(w, page, **states):
    res = page['/Resources']
    gs = DictionaryObject()
    for name, entries in states.items():
        gs[NameObject('/' + name)] = w._add_object(DictionaryObject({NameObject(k): v for k, v in entries.items()}))
    res[NameObject('/ExtGState')] = gs


def test_inherited_transparency_is_neutralised_for_opaque_text(tmp_path):
    plain, _ = page_with(tmp_path, LINES, height=300)

    def variant(name, glass):
        w = PdfWriter(clone_from=PdfReader(plain))
        p = w.pages[0]
        _gstates(w, p, Glass={'/Type': NameObject('/ExtGState'), '/ca': FloatObject(.5 if glass else 1), '/BM': NameObject('/Multiply' if glass else '/Normal')},
                 Solid={'/Type': NameObject('/ExtGState'), '/ca': FloatObject(1), '/BM': NameObject('/Normal')})
        # The transparent state is set first and never restored; the following
        # paragraph is drawn before the target, so no earlier point is free of it.
        streams = list(p['/Contents'])
        for ref in streams:
            s = ref.get_object()
            s.set_data(s.get_data().replace(b'BT\n', b'BT\n/Solid gs\n'))
        first = DecodedStreamObject()
        first.set_data(b'/Glass gs\n')
        p[NameObject('/Contents')] = ArrayObject([w._add_object(first), streams[3]] + streams[:3])
        w.write(tmp_path / name)
        return tmp_path / name
    glass, clear = variant('glass.pdf', True), variant('clear.pdf', False)
    out, reference = tmp_path / 'out.pdf', tmp_path / 'reference.pdf'
    edit(glass, '申請書は窓口で', '申請書は各窓口で', out)
    edit(clear, '申請書は窓口で', '申請書は各窓口で', reference)
    assert b'/PFNeutral gs' in PdfReader(out).pages[0].get_contents().get_data()
    assert b'/PFNeutral gs' not in PdfReader(reference).pages[0].get_contents().get_data()
    # Same picture (an explicit neutral ExtGState may change antialiasing by a level).
    with pymupdf.open(out) as a, pymupdf.open(reference) as b:
        assert max(abs(x - y) for x, y in zip(a[0].get_pixmap().samples, b[0].get_pixmap().samples)) <= 2


def test_text_drawn_with_a_blend_mode_is_refused(tmp_path):
    plain, _ = page_with(tmp_path, LINES, height=300)
    w = PdfWriter(clone_from=PdfReader(plain))
    p = w.pages[0]
    _gstates(w, p, Mul={'/Type': NameObject('/ExtGState'), '/BM': NameObject('/Multiply')})
    for ref in p['/Contents']:
        s = ref.get_object()
        s.set_data(b'/Mul gs\n' + s.get_data())
    w.write(tmp_path / 'multiply.pdf')
    with pytest.raises(Refused, match='non-fill or transparent'):
        propose(str(tmp_path / 'multiply.pdf'), 1, '申請書は窓口で', '申請書は各窓口で')


def _clip_outside(data):
    """Line 2's first glyph as a placed object whose clip is set before its BMC and
    released after its EMC (InDesign), with a painted background of its own."""
    m = re.search(rb'q\nBT\n1 0 0 1 ([\d.]+) ([\d.]+) Tm\n/Fixture 11 Tf \[<([0-9a-f]{4})([0-9a-f]+)>\]TJ\nET\nQ', data[1])
    x, y, first, rest = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4)
    placed = (f'q {x - 1} {y - 3} 13 14 re W n /PlacedPDF BMC q .9 g {x - 1} {y - 3} 13 14 re f Q BT 1 0 0 1 {x:g} {y:g} Tm /Fixture 11 Tf [<'.encode()
              + first + b'>]TJ ET EMC Q\n' + f'q BT 1 0 0 1 {x + 11:g} {y:g} Tm /Fixture 11 Tf [<'.encode() + rest + b'>]TJ ET Q')
    data[1] = data[1][:m.start()] + placed + data[1][m.end():]
    return data


def test_text_after_a_placed_object_is_inserted_outside_its_clip(tmp_path):
    source, _ = tagged(tmp_path, [], tree=False, edit_streams=_clip_outside)
    out = tmp_path / 'out.pdf'
    edit(source, '申請書は窓口で', '申請書は窓口と郵送で', out)
    before = PdfReader(source).pages[0].get_contents().get_data()
    after = PdfReader(out).pages[0].get_contents().get_data()
    block = re.search(rb'q [\d.]+ [\d.]+ 13 14 re W n /PlacedPDF BMC.*?EMC Q', before, re.S).group(0)
    assert after.count(block) == 1
    assert drawn_text(out) == drawn_text(source).replace('申請書は窓口で', '申請書は窓口と郵送で')
    # The object's own paint lies inside the approved width; it was there when
    # the conditions were approved, so the saved PDF alone continues with them.
    proposal = propose(str(out), 1, '窓口と郵送で', '窓口と郵送でも')
    assert proposal['evidence']['conditions']['status'] == 'applied'


def _glyph_page(tmp_path, items, name='glyphs.pdf'):
    """One text paint per (x, y, text, size), in this order."""
    data = cjk_provider_bytes()
    path = tmp_path / name
    d = pymupdf.open()
    p = d.new_page(width=300, height=300)
    p.insert_font(fontname='Fixture', fontbuffer=data)
    for x, y, text, size in items:
        p.insert_text((x, y), text, fontsize=size, fontname='Fixture')
    d.save(path)
    d.close()
    return path


def test_marker_on_an_offset_baseline_moves_with_its_line(tmp_path):
    # A small ▶ drawn 1.5 pt above the last line (beyond the 0.15 em line
    # grouping of the page analysis) and painted after the rest of the page.
    source = _glyph_page(tmp_path, [(40, 60, '本年度の補助事業について、募集を', 11), (40, 76, '行います。申請書は窓口で配布し', 11),
                                    (40, 92, 'ます。', 11), (40, 130, '後続段落です。', 11), (73.5, 90.5, '▶', 6)])
    proposal = propose(str(source), 1, '本年度の補助事業について、', '本年度の')
    assert proposal['paragraphs'][0].endswith('ます。▶')
    out = tmp_path / 'out.pdf'
    save(str(source), approve(str(source), proposal, acknowledge_inference=True), str(out))
    with pymupdf.open(out) as d:
        glyphs = [(c['c'], c['origin']) for b in d[0].get_text('rawdict')['blocks'] for l in b.get('lines', [])
                  for s in l['spans'] for c in s['chars']]
    marker = next(o for ch, o in glyphs if ch == '▶')
    stop = next(o for ch, o in glyphs if ch == '。' and abs(o[1] - marker[1] - 1.5) < .05 and o[0] < marker[0])
    assert 0 < marker[0] - stop[0] < 12
    # Drawn in reading order: extraction follows its line.
    assert drawn_text(out) == '本年度の募集を行います。申請書は窓口で配布します。▶後続段落です。'


def test_list_items_opening_with_one_marker_are_separate_paragraphs(tmp_path):
    items = [(40, 60, '●申請書は窓口で配布します。', 11), (40, 76, '●提出は郵送でも受け付けます。', 11), (40, 92, '●締切は五月末日です。', 11)]
    source = _glyph_page(tmp_path, items)
    proposal = propose(str(source), 1, '窓口で', '各窓口で')
    assert proposal['paragraphs'][0] == '●申請書は窓口で配布します。'
    out = tmp_path / 'out.pdf'
    save(str(source), approve(str(source), proposal, acknowledge_inference=True), str(out))
    with pymupdf.open(out) as d:
        lines = [''.join(s['text'] for s in l['spans']) for b in d[0].get_text('dict')['blocks'] for l in b.get('lines', [])]
    assert lines == ['●申請書は各窓口で配布します。', '●提出は郵送でも受け付けます。', '●締切は五月末日です。']


def test_row_placed_apart_in_one_element_is_not_a_continuation(tmp_path):
    """A smaller note mark set apart in the cell (not at a continuation x) is its own paragraph."""
    lines = [(40, 60, '本年度の補助事業について、募集を'), (150, 70, '注１'), (40, 76, '行います。')]

    def one_sequence(data):
        data[1] = data[1].replace(b'/Fixture 11 Tf', b'/Fixture 7 Tf')
        return [b'/TD <</MCID 0>> BDC\n' + data[0], data[1], data[2] + b'\nEMC\n']
    source, _ = tagged(tmp_path, [dict(role='/TD', kids=[])], lines=lines, edit_streams=one_sequence)
    w = PdfWriter(clone_from=PdfReader(source))
    cell = w._root_object['/StructTreeRoot']['/K'][0].get_object()
    cell[NameObject('/K')] = ArrayObject([NumberObject(0)])
    w._root_object['/StructTreeRoot']['/ParentTree'].get_object()['/Nums'][1].get_object().append(cell.indirect_reference)
    w.write(tmp_path / 'cell.pdf')
    source = tmp_path / 'cell.pdf'
    out = tmp_path / 'out.pdf'
    proposal, _ = edit(source, '補助事業', '補助金事業', out)
    assert proposal['paragraphs'][0] == '本年度の補助事業について、募集を行います。'

    def note(path):
        with pymupdf.open(path) as d:
            return [(c['c'], c['origin']) for b in d[0].get_text('rawdict')['blocks'] for l in b.get('lines', [])
                    for s in l['spans'] for c in s['chars'] if c['c'] in '注１']
    assert note(out) == note(source)
    structure_unchanged(source, out)


def test_centred_paragraph_is_refused(tmp_path):
    # Line 2 is centred under line 1; reflow would align it at the start.
    lines = [(40, 60, '本年度の補助事業について、募集を'), (100.5, 76, '行います。'), (40, 130, '後続段落です。')]
    source, _ = tagged(tmp_path, [dict(role='/P', kids=[0, 1]), dict(role='/P', kids=[2])], lines=lines)
    with pytest.raises(Refused, match='centred paragraph'):
        propose(str(source), 1, '補助事業', '補助金事業')
