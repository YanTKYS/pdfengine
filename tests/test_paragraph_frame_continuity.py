"""B2 continuous editing: approved paragraph conditions survive save and reopen."""
import hashlib
import json
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, NameObject, NumberObject,
                           TextStringObject)
from provider_font import cjk_provider_bytes
from test_paragraph_frame_poc import obj
from prototypes.paragraph_frame import auto_conditions as conditions
from prototypes.paragraph_frame.auto import approve, propose, save
from prototypes.paragraph_frame.audit import painting, semantic
from prototypes.paragraph_frame.engine import Refused
from pdfeditor.content_stream import ContentPage

LINES = [(40, 60, '本年度の補助事業について、募集を'), (29, 76, '行います。'), (40, 100, '後続段落です。')]
TARGET = '本年度の補助事業について、募集を行います。'


def binding(font):
    return dict(path=str(font), sha256=hashlib.sha256(font.read_bytes()).hexdigest(), font_index=0, relation='substituted')


def page_with(tmp_path, lines, *, name='source.pdf', width=300, height=300, before=None):
    data = cjk_provider_bytes()
    font = tmp_path / 'font.ttf'
    font.write_bytes(data)
    path = tmp_path / name
    d = pymupdf.open()
    p = d.new_page(width=width, height=height)
    if before:
        before(p)
    p.insert_font(fontname='Fixture', fontbuffer=data)
    for x, y, text in lines:
        p.insert_text((x, y), text, fontsize=11, fontname='Fixture')
    d.save(path)
    d.close()
    return path, font


def tag(path, groups, tmp_path):
    """One /P per group of content streams, one /Span leaf per stream (like Word)."""
    w = PdfWriter(clone_from=PdfReader(path))
    p = w.pages[0]
    root = w._add_object(obj(Type=NameObject('/StructTreeRoot')))
    parents, leaves = [], []
    contents = p['/Contents']
    texts = [t for _, _, t in LINES]
    for group in groups:
        parent = w._add_object(obj(Type=NameObject('/StructElem'), S=NameObject('/P'), P=root, Pg=p.indirect_reference))
        kids = []
        for i in group:
            leaf = w._add_object(obj(Type=NameObject('/StructElem'), S=NameObject('/Span'), P=parent, Pg=p.indirect_reference,
                                     K=NumberObject(i), ActualText=TextStringObject(texts[i])))
            kids.append(leaf)
            leaves.append(leaf)
            stream = DecodedStreamObject()
            stream.set_data(f'/Span <</MCID {i}>> BDC\n'.encode() + contents[i].get_object().get_data() + b'\nEMC\n')
            contents[i] = w._add_object(stream)
        parent.get_object()[NameObject('/K')] = ArrayObject(kids)
        parents.append(parent)
    root.get_object()[NameObject('/K')] = ArrayObject(parents)
    root.get_object()[NameObject('/ParentTree')] = w._add_object(obj(Nums=ArrayObject([NumberObject(0), w._add_object(ArrayObject(leaves))])))
    p[NameObject('/StructParents')] = NumberObject(0)
    w._root_object[NameObject('/StructTreeRoot')] = root
    w._root_object[NameObject('/MarkInfo')] = obj(Marked=BooleanObject(True))
    out = tmp_path / 'tagged.pdf'
    w.write(out)
    return out


@pytest.fixture(params=['untagged', 'tagged'])
def world(request, tmp_path):
    path, font = page_with(tmp_path, LINES)
    if request.param == 'tagged':
        path = tag(path, [[0, 1], [2]], tmp_path)
    return path, font, request.param


def edit(source, old, new, out, font, **options):
    proposal = propose(source, 1, old, new, substitute=binding(font), **options)
    result = save(source, approve(source, proposal, acknowledge_inference=True), out)
    return proposal, result


def drawn_lines(path):
    """(text, x, baseline) of each drawn row, independent of B2."""
    c = ContentPage(path, 1)
    try:
        rows = {}
        for g in c.actual:
            if g['unicode'].strip():
                rows.setdefault(round(g['origin'][1], 2), []).append(g)
        return [(''.join(g['unicode'] for g in sorted(r, key=lambda g: g['origin'][0])),
                 round(min(g['origin'][0] for g in r), 2), y) for y, r in sorted(rows.items())]
    finally:
        c.close()


def test_shorten_reopen_restore_grow_replace_keeps_approved_conditions(world, tmp_path):
    source, font, kind = world
    original = drawn_lines(source)
    first, _ = edit(source, '補助事業について、募集を行います。', '募集します。', tmp_path / 'step1.pdf', font)
    assert first['evidence']['conditions']['status'] == 'absent' and len(first['lines']) == 1
    approved = {k: first['evidence']['inferred'][k] for k in ('left', 'right', 'line_height')}
    assert approved == dict(left=29, right=pytest.approx(216, abs=.01), line_height=16)
    source.unlink()  # PDF-alone continuation: the original is never consulted again.
    # Without the stored record the one-line paragraph loses its evidence.
    blind = propose(tmp_path / 'step1.pdf', 1, '募集します。', '補助事業について、募集を行います。',
                    substitute=binding(font), stored_conditions=False)
    assert blind['evidence']['inferred']['left'] != 29 and blind['evidence']['inferred']['line_height'] != 16
    second, _ = edit(tmp_path / 'step1.pdf', '募集します。', '補助事業について、募集を行います。', tmp_path / 'step2.pdf', font)
    conds = second['evidence']['conditions']
    assert conds['status'] == 'applied' and conds['approval_count'] == 1
    assert {k: second['evidence']['inferred'][k] for k in approved} == approved
    # Restoring the original text restores the original lines, x and baselines,
    # with the original font: deleted glyphs come back from the resource itself.
    assert drawn_lines(tmp_path / 'step2.pdf') == original
    assert second['font_counts'].get('source-resource-witness') and not {'approved-substitute', 'same-program-new-code'} & set(second['font_counts'])
    third, _ = edit(tmp_path / 'step2.pdf', '行います。', '行います。応募をお待ちしています。', tmp_path / 'step3.pdf', font)
    assert [l['baseline'] for l in third['lines']] == [60, 76] and third['evidence']['conditions']['approval_count'] == 2
    fourth, _ = edit(tmp_path / 'step3.pdf', '応募', '申請', tmp_path / 'step4.pdf', font)
    assert {k: fourth['evidence']['inferred'][k] for k in approved} == approved
    final = drawn_lines(tmp_path / 'step4.pdf')
    assert final[0][1:] == original[0][1:] and final[1][1] == 29
    assert final[-1] == ('後続段落です。', 40.0, 100.0)  # the follower is back in place
    if kind == 'tagged':
        assert all(semantic(tmp_path / 'step4.pdf', 1)['checks'].values())


def test_follower_records_move_with_their_paragraph(tmp_path):
    lines = LINES + [(29, 116, 'です。')]
    path, font = page_with(tmp_path, lines)
    # Give the follower its own approved record first, then move it twice.
    edit(path, '後続段落です。', '後続の段落です。', tmp_path / 'a.pdf', font)
    edit(tmp_path / 'a.pdf', '補助事業について、募集を行います。', '募集します。', tmp_path / 'b.pdf', font)
    moved = propose(tmp_path / 'b.pdf', 1, '後続の', '後続', substitute=binding(font))
    assert moved['evidence']['conditions']['status'] == 'applied'


def test_external_changes_make_stored_conditions_stale(world, tmp_path):
    source, font, _ = world
    edit(source, '補助事業について、募集を行います。', '募集します。', tmp_path / 'short.pdf', font)
    short = tmp_path / 'short.pdf'

    # A rectangle drawn inside the approved width (right of the short line).
    w = PdfWriter(clone_from=PdfReader(short))
    page = w.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(b'q 0.5 g 180 236 20 8 re f Q\n')
    page[NameObject('/Contents')] = ArrayObject([page.raw_get('/Contents'), w._add_object(stream)])
    intruded = tmp_path / 'intruded.pdf'
    w.write(intruded)
    p = propose(intruded, 1, '募集します。', '補助事業について、募集を行います。', substitute=binding(font))
    assert p['evidence']['conditions']['status'].startswith('stale: protected paint')
    assert not p['evidence']['conditions']['applied'] and p['evidence']['inferred']['left'] != 29

    # The drawn paragraph no longer matches the record (text changed elsewhere).
    c = ContentPage(short, 1)
    try:
        records, _ = conditions.read(c, 1)
    finally:
        c.close()
    w = PdfWriter(clone_from=PdfReader(short))
    changed = json.loads(json.dumps(records))
    changed[0]['lines'][0]['text'] = changed[0]['lines'][0]['text'].replace('募集', '応募')
    conditions.attach(w, w.pages[0], changed, 1)
    forged = tmp_path / 'forged.pdf'
    w.write(forged)
    p = propose(forged, 1, '募集します。', '補助事業について、募集を行います。', substitute=binding(font))
    assert p['evidence']['conditions']['status'].startswith('stale: the drawn paragraph differs')

    # Unreadable private data is reported and ignored.
    w = PdfWriter(clone_from=PdfReader(short))
    stream = DecodedStreamObject()
    stream.set_data(b'{not json')
    w.pages[0]['/PieceInfo'][conditions.KEY][NameObject('/Private')] = w._add_object(stream)
    broken = tmp_path / 'broken.pdf'
    w.write(broken)
    p = propose(broken, 1, '募集します。', '補助事業について、募集を行います。', substitute=binding(font))
    assert not p['evidence']['conditions']['applied']
    assert any('unreadable' in warning for warning in p['evidence']['warnings'])


def test_external_save_that_updates_last_modified_still_verifies(world, tmp_path):
    source, font, _ = world
    edit(source, '補助事業について、募集を行います。', '募集します。', tmp_path / 'short.pdf', font)
    w = PdfWriter(clone_from=PdfReader(tmp_path / 'short.pdf'))
    w.pages[0][NameObject('/LastModified')] = TextStringObject('D:20990101000000Z')
    later = tmp_path / 'later.pdf'
    w.write(later)
    p = propose(later, 1, '募集します。', '補助事業について、募集を行います。', substitute=binding(font))
    assert p['evidence']['conditions']['status'] == 'applied'
    assert any('page modified' in warning for warning in p['evidence']['warnings'])


def test_conditions_are_page_private_data_and_noop_keeps_them(world, tmp_path):
    source, font, _ = world
    edit(source, '募集', '公募', tmp_path / 'one.pdf', font)
    reader = PdfReader(tmp_path / 'one.pdf')
    page = reader.pages[0]
    entry = page['/PieceInfo'][conditions.KEY]
    assert str(entry['/LastModified']) == str(page['/LastModified'])
    data = json.loads(entry['/Private'].get_object().get_data())
    assert data['schema'] == conditions.SCHEMA and len(data['records']) == 1
    record = data['records'][0]
    assert record['origin']['provenance']['right'].startswith('inferred') and record['approval']['count'] == 1
    # Fonts, drawings and other page content are unaffected by the private data.
    assert painting(tmp_path / 'one.pdf', 1)['drawings'] == painting(source, 1)['drawings']
    noop = tmp_path / 'noop.pdf'
    PdfWriter(clone_from=tmp_path / 'one.pdf').write(noop)
    again = propose(noop, 1, '公募', '募集', substitute=binding(font))
    assert again['evidence']['conditions']['status'] == 'applied'


LATIN = [(20, 40, 'Alpha beta gamma delta'), (200, 40, 'Second column text'),
         (20, 54, 'epsilon zeta eta theta'), (200, 54, 'continues here and'),
         (20, 68, 'iota.'), (200, 68, 'ends.')]


def test_columns_and_implicit_line_join_spaces(tmp_path):
    path, font = page_with(tmp_path, LATIN, width=400)
    p = propose(path, 1, 'delta epsilon', 'delta and epsilon', substitute=binding(font))
    assert p['paragraphs'][0] == 'Alpha beta gamma delta epsilon zeta eta theta iota.'
    assert p['evidence']['conditions']['implicit_line_join_spaces'] == 2
    out = tmp_path / 'out.pdf'
    save(path, approve(path, p, acknowledge_inference=True), out)
    def column(path, right):
        c = ContentPage(path, 1)
        try:
            return [(g['unicode'], g['origin']) for g in c.actual if g['unicode'].strip() and (g['origin'][0] >= 200) == right]
        finally:
            c.close()
    assert column(out, True) == column(path, True)  # the other column is untouched
    assert ''.join(t for t, _ in column(out, False)) == 'Alphabetagammadeltaandepsilonzetaetathetaiota.'


def test_line_final_hyphen_is_never_moved_inside_a_line(tmp_path):
    lines = [(20, 40, 'The very long inter-'), (20, 54, 'national meeting is'), (20, 68, 'tomorrow.')]
    path, font = page_with(tmp_path, lines)
    p = propose(path, 1, 'tomorrow.', 'today.', substitute=binding(font))
    assert any('hyphen' in w for w in p['evidence']['warnings'])
    assert p['paragraphs'][0] == 'The very long inter-national meeting is tomorrow.'
    with pytest.raises(Refused, match='hyphen'):
        propose(path, 1, 'very long ', '', substitute=binding(font))


def test_background_container_is_not_an_obstacle(tmp_path):
    def panel(p):
        p.draw_rect(pymupdf.Rect(10, 30, 290, 130), color=None, fill=(.85, .9, 1))
    path, font = page_with(tmp_path, LINES, before=panel)
    p = propose(path, 1, '募集を', '公募を', substitute=binding(font))
    assert p['evidence']['conditions']['containers'] and p['evidence']['inferred']['frame'][1] >= 30
    out = tmp_path / 'out.pdf'
    save(path, approve(path, p, acknowledge_inference=True), out)
    assert painting(out, 1)['drawings'] == painting(path, 1)['drawings']


def test_ligature_provenance_is_reported(tmp_path, monkeypatch):
    path, font = page_with(tmp_path, LINES)
    import prototypes.paragraph_frame.auto_detect as detect
    original = detect.ContentPage

    class Lost(original):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            for e in self.events:
                if any(ch.text == '補' for ch in e.chars):
                    e.error = 'Unicode restoration is unavailable or multi-character ligature is unsupported'
                    for ch in e.chars:
                        ch.source_orders.clear()
    monkeypatch.setattr(detect, 'ContentPage', Lost)
    with pytest.raises(Refused, match='ligature'):
        propose(path, 1, '募集', '公募', substitute=binding(font))


def test_substitute_weight_choice_prefers_the_closer_face(tmp_path):
    from prototypes.paragraph_frame.auto_style import source_tokens, weight_scores
    from pdfeditor.shaped_font import ShapedFont
    import test_adjacent_span as adj
    path, font = page_with(tmp_path, LINES)
    same, boxes = cjk_provider_bytes(), adj.build_font(sorted(set(''.join(t for _, _, t in LINES))))
    c = ContentPage(path, 1)
    try:
        tokens = list(source_tokens(c)[0].values())
        scores = weight_scores(c, tokens, [('same', same, ShapedFont(same)), ('boxes', boxes, ShapedFont(boxes))])
    finally:
        c.close()
    assert scores[0]['characters'] > 5 and scores[0]['distance'] < scores[1]['distance']


def test_substituted_glyph_is_not_the_font_anchor_of_a_later_edit(tmp_path):
    import test_adjacent_span as adj
    missing = chr(0x9B31)  # absent from the fixture font (written as an escape on purpose)
    path, font = page_with(tmp_path, LINES)
    extra = tmp_path / 'extra.ttf'
    extra.write_bytes(adj.build_font([missing]))
    first, _ = edit(path, '募集を', missing + '集を', tmp_path / 'one.pdf', extra)
    assert first['font_counts']['approved-substitute'] == 1
    second, _ = edit(tmp_path / 'one.pdf', missing, '公募', tmp_path / 'two.pdf', extra)
    assert 'approved-substitute' not in second['font_counts'] and 'same-program-new-code' not in second['font_counts']
    c = ContentPage(tmp_path / 'two.pdf', 1)
    try:
        fonts = {e.state.font.name for e in c.events for ch in e.chars if ch.text in '公募'}
    finally:
        c.close()
    assert not any(name.startswith('/PFAuto') for name in fonts)


def _with_records(path, records, out):
    w = PdfWriter(clone_from=PdfReader(path))
    conditions.attach(w, w.pages[0], records, 1)
    w.write(out)
    return out


def _stored(path):
    c = ContentPage(path, 1)
    try:
        return conditions.read(c, 1)[0]
    finally:
        c.close()


@pytest.mark.parametrize('kind', ['empty', 'no-lines', 'line-key', 'nan', 'reversed', 'leading', 'off-page', 'types', 'align', 'count'])
def test_malformed_records_are_ignored_and_inference_is_used(world, tmp_path, kind):
    source, font, _ = world
    edit(source, '補助事業について、募集を行います。', '募集します。', tmp_path / 'short.pdf', font)
    good = _stored(tmp_path / 'short.pdf')[0]
    bad = json.loads(json.dumps(good))
    if kind == 'empty':
        bad = {}
    elif kind == 'no-lines':
        del bad['lines']
    elif kind == 'line-key':
        del bad['lines'][0]['baseline']
    elif kind == 'nan':
        bad['conditions']['right'] = float('nan')
    elif kind == 'reversed':
        bad['conditions']['left'], bad['conditions']['right'] = bad['conditions']['right'], bad['conditions']['left']
    elif kind == 'leading':
        bad['conditions']['line_height'] = 0
    elif kind == 'off-page':
        bad['lines'][0]['baseline'] = 10_000
    elif kind == 'types':
        bad['lines'] = 'not a list'
    elif kind == 'align':
        bad['conditions']['align'] = 'center'
    elif kind == 'count':
        bad['approval']['count'] = 0
    broken = _with_records(tmp_path / 'short.pdf', [bad], tmp_path / 'broken.pdf')
    p = propose(broken, 1, '募集します。', '補助事業について、募集を行います。', substitute=binding(font))
    conds = p['evidence']['conditions']
    assert not conds['applied'] and '1 malformed record(s) ignored' in conds['status']
    assert any('malformed' in warning for warning in p['evidence']['warnings'])
    assert p['evidence']['inferred']['left'] != 29  # fresh inference, not the stored value
    out = tmp_path / 'saved.pdf'
    save(broken, approve(broken, p, acknowledge_inference=True), out)
    assert len(_stored(out)) == 1  # the malformed record is not carried forward


def test_valid_record_still_applies_next_to_a_malformed_one(world, tmp_path):
    source, font, _ = world
    edit(source, '補助事業について、募集を行います。', '募集します。', tmp_path / 'short.pdf', font)
    good = _stored(tmp_path / 'short.pdf')[0]
    mixed = _with_records(tmp_path / 'short.pdf', [{'lines': [{}]}, good], tmp_path / 'mixed.pdf')
    p = propose(mixed, 1, '募集します。', '補助事業について、募集を行います。', substitute=binding(font))
    conds = p['evidence']['conditions']
    assert conds['applied'] and p['evidence']['inferred']['left'] == 29
    assert any('1 malformed record(s) ignored' in warning for warning in p['evidence']['warnings'])
