"""Two consecutive leaf Spans of one /P, distinct from the single-Span contract."""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
import subprocess

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject, DecodedStreamObject

from pdfeditor.adjacent_span import (accept_adjacent_span, edit_adjacent_span, open_adjacent_span,
                                     plan_adjacent_span_edit, propose_adjacent_span, _plan_owner)
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content, structure_objects
from pdfeditor.selection import make_selection
from pdfeditor.span_edit import _seal
from pdfeditor.transaction import Plan, Transaction
import page_flow_fixture as fx

LEAD = '申請書'
FIRST = '本年度の補助事業について募集を行'
SECOND = 'います。'
OTHER = '期限までに提出していただきますようお願いします。'
EDIT = dict(start=FIRST.index('について'), end=FIRST.index('について') + 4, text='につきまして')
BOUNDARY = 16
REGIONS = [dict(x0=100, y0=148, x1=270, y1=164), dict(x0=50, y0=166, x1=200, y1=182)]
FOLLOWER_REGION = dict(x0=92, y0=166, x1=200, y1=182)
BY_MCID = {0: dict(x0=50, y0=148, x1=99, y1=164), 1: REGIONS[0], 2: REGIONS[1]}


def build_font(chars):
    names = ['.notdef'] + [f'uni{ord(c):04X}' for c in chars]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({ord(c): n for c, n in zip(chars, names[1:])})
    glyphs = {}
    for index, name in enumerate(names):
        pen = TTGlyphPen(None)
        if name != 'uni0020':
            inset = 80 + (index * 7) % 60
            pen.moveTo((inset, -60)); pen.lineTo((1000 - inset, -60)); pen.lineTo((1000 - inset, 780))
            pen.lineTo((inset, 780)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({n: (500 if n == 'uni0020' else 1000, 80) for n in names})
    builder.setupHorizontalHeader(ascent=880, descent=-120)
    builder.setupNameTable(dict(familyName='AdjacentFixture', styleName='Regular',
                                uniqueFontIdentifier='AdjacentFixture-1', fullName='AdjacentFixture',
                                psName='AdjacentFixture'))
    builder.setupOS2(sTypoAscender=880, sTypoDescender=-120, usWinAscent=880, usWinDescent=120, fsType=0)
    builder.setupPost(); builder.setupMaxp()
    builder.font['head'].created = builder.font['head'].modified = 2082844800
    out = BytesIO(); builder.save(out)
    return out.getvalue()


def make_source(path, *, second_parent=0, follower=True, foreign=None, first=FIRST, second=SECOND,
                other=OTHER, other_x=50, extra=()):
    """P1: LEAD, first, second, ' ' (follower); P2: other.

    ``foreign`` adds a P2 glyph on line two; ``extra`` adds (x, baseline, text) P2 lines.
    """
    chars = sorted(set(LEAD + first + second + other + ''.join(e[2] for e in extra) + 'につきまして' + 'いたし' + ' '))
    lines = [(50, 160, 10.5, LEAD), (100, 160, 10.5, first), (50, 178, 10.5, second),
             (50 + len(second) * 10.5, 178, 10.5, ' '), (other_x, 196, 10.5, other)]
    if foreign is not None:
        lines.append((foreign, 178, 10.5, '提'))
    lines.extend((x, baseline, 10.5, text) for x, baseline, text in extra)
    fx.make_page(path, build_font(chars), tagged=True, lines=lines)
    writer = PdfWriter(PdfReader(path), full=True)
    page = writer.pages[0]
    tree = DictionaryObject({NameObject('/Type'): NameObject('/StructTreeRoot')})
    tree_ref = writer._add_object(tree)
    document = DictionaryObject({NameObject('/Type'): NameObject('/StructElem'),
        NameObject('/S'): NameObject('/Document'), NameObject('/P'): tree_ref})
    document_ref = writer._add_object(document)
    parents = []
    for _ in range(2):
        parents.append(writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/StructElem'),
            NameObject('/S'): NameObject('/P'), NameObject('/P'): document_ref, NameObject('/Pg'): page.indirect_reference})))
    membership = [0, 0, second_parent, 0, 1] + [1] * (len(lines) - 5)
    owners = []
    for mcid, (line, parent) in enumerate(zip(lines, membership)):
        owners.append(writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/StructElem'),
            NameObject('/S'): NameObject('/Span'), NameObject('/P'): parents[parent], NameObject('/Pg'): page.indirect_reference,
            NameObject('/K'): ArrayObject([NumberObject(mcid)]), NameObject('/ActualText'): TextStringObject(line[3])})))
    for index, parent in enumerate(parents):
        parent.get_object()[NameObject('/K')] = ArrayObject([o for o, p in zip(owners, membership) if p == index])
    document[NameObject('/K')] = ArrayObject(parents)
    tree[NameObject('/K')] = ArrayObject([document_ref])
    tree[NameObject('/ParentTree')] = writer._add_object(DictionaryObject({NameObject('/Nums'):
        ArrayObject([NumberObject(0), ArrayObject(owners)])}))
    writer.root_object[NameObject('/StructTreeRoot')] = tree_ref
    page[NameObject('/StructParents')] = NumberObject(0)
    data = page.get_contents().get_data().replace(b'BDC BT', b'BDC q 0 0 595.28 841.89 re W* n BT').replace(b'ET EMC', b'ET Q EMC')
    if follower:
        data = data.replace(b'/P <</MCID 3>> BDC', b'/Span <</MCID 3/Lang (en-US)>> BDC')
    stream = DecodedStreamObject(); stream.set_data(data)
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return path


def ids_of(path, mcid):
    observed = observe_marked_content(path)
    scope = next(s for s in observed['scopes'] if s['properties'].get('/MCID') == mcid)
    lo, hi = scope['content_byte_range']
    content = ContentPage(path, 1)
    try:
        return [i for e in content.events if lo <= e.operator.start < hi for ch in e.chars for i in ch.source_orders]
    finally:
        content.close()


def owners(path, mcids=(1, 2), regions=None):
    regions = regions or [BY_MCID[m] for m in mcids]
    return [dict(selection=make_selection(path, 1, glyph_ids=ids_of(path, m), explicit_width=r['x1'] - r['x0']),
                 region=r) for m, r in zip(mcids, regions)]


def follower(path, mcid=3, region=FOLLOWER_REGION):
    return dict(glyph_ids=ids_of(path, mcid), region=region)


def provider(path, root):
    with pymupdf.open(path) as doc:
        xref = next(f[0] for f in doc[0].get_fonts(full=True))
        data = doc.extract_font(xref)[3]
    target = root / 'provider.ttf'; target.write_bytes(data)
    return dict(path=str(target), sha256=hashlib.sha256(data).hexdigest(), font_index=0)


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp('adjacent-span')
    path = make_source(root / 'source.pdf')
    proposal = propose_adjacent_span(path, *owners(path), follower=follower(path))
    return path, proposal, provider(path, root)


def save(path, state, root, name, edit=None, boundary=None, recipe=None):
    pdf, sidecar = root / (name + '.pdf'), root / (name + '.json')
    result = edit_adjacent_span(path, state, pdf, sidecar, edit, boundary=boundary, provider=recipe)
    opened = open_adjacent_span(pdf, json.loads(sidecar.read_text()))
    assert opened['status'] == 'restored', opened
    return pdf, opened['state'], result


def raster(path):
    with pymupdf.open(path) as doc:
        return doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).samples


def poppler(path, root):
    from PIL import Image
    prefix = root / (path.stem + '-poppler')
    subprocess.run(['pdftoppm', '-singlefile', '-r', '144', '-png', str(path), str(prefix)], check=True, capture_output=True)
    with Image.open(str(prefix) + '.png') as image:
        return image.tobytes()


def actual_texts(path):
    reader = PdfReader(path)
    parent = reader.trailer['/Root']['/StructTreeRoot']['/K'][0].get_object()['/K'][0].get_object()
    return [(c.get_object().get('/ActualText'), list(c.get_object()['/K'])) for c in parent['/K']]


def test_lifecycle_relocates_tail_moves_follower_and_noop(world, tmp_path):
    source, proposal, recipe = world
    state = accept_adjacent_span(source, proposal)
    plan = plan_adjacent_span_edit(source, state, EDIT, boundary=BOUNDARY, provider=recipe)
    kinds = [(p['owner'], p['kind']) for p in plan['provenance']]
    assert kinds.count(('first', 'inserted')) == 6 and kinds.count(('second', 'relocated')) == 2
    assert kinds.count(('second', 'retained')) == len(SECOND)
    assert plan['follower']['dx'] == pytest.approx(21)
    edited, current, result = save(source, state, tmp_path, 'edit', EDIT, BOUNDARY, recipe)
    first = '本年度の補助事業につきまして募集'; second = 'を行' + SECOND
    assert result['after'] == [first, second] and ''.join(result['after']) == (FIRST + SECOND).replace('について', 'につきまして')
    assert [o['witness']['text'] for o in current['owners']] == [first, second]
    assert actual_texts(edited) == [(LEAD, [0]), (first, [1]), (second, [2]), (' ', [3])]
    assert structure_objects(PdfReader(edited), omit_actual_text=(current['witness']['first']['owner']['xref'],
        current['witness']['second']['owner']['xref'])) == state['witness']['structure']
    relocated = [p for p in result['provenance'] if p['kind'] == 'relocated']
    assert [p['source'] for p in relocated] == [dict(owner='first', offset=14), dict(owner='first', offset=15)]
    assert all(p['gid'] == p['source_glyph']['gid'] and p['provider'] != 'original' for p in relocated)
    assert all(p['provider'] == 'original' for p in result['provenance'] if p['kind'] == 'retained')
    moved = current['witness']['follower']
    assert moved['properties'] == state['witness']['follower']['properties'] and moved['text'] == ' '
    assert moved['bbox'][0] == pytest.approx(state['witness']['follower']['bbox'][0] + 21)
    assert len(current['owners'][0]['font_binding']['generated']) == 1
    assert len(current['owners'][1]['font_binding']['generated']) == 1
    noop, unchanged, _ = save(edited, current, tmp_path, 'noop')
    assert PdfReader(edited).pages[0].get_contents().get_data() == PdfReader(noop).pages[0].get_contents().get_data()
    assert raster(edited) == raster(noop) and poppler(edited, tmp_path) == poppler(noop, tmp_path)
    # Re-edit from the saved revision inside the second owner; the follower follows again.
    again, last, second_result = save(noop, unchanged, tmp_path, 'again',
        dict(start=len(first) + 3, end=len(first) + 3, text='たし'), len(first), recipe)
    assert [o['witness']['text'] for o in last['owners']] == [first, 'を行いたします。']
    assert second_result['follower_dx'] == pytest.approx(21)
    assert last['witness']['structure'] == state['witness']['structure']
    final, _, _ = save(again, last, tmp_path, 'final')
    assert raster(again) == raster(final)
    for path in (source, edited, noop, again, final):
        assert PdfReader(path).pages[0].get_contents().get_data().count(b'0 0 595.28 841.89 re W* n') == 5


def test_without_follower_the_second_owner_has_no_capacity(world, tmp_path):
    source, _, recipe = world
    narrow = [REGIONS[0], dict(REGIONS[1], x1=92)]
    state = accept_adjacent_span(source, propose_adjacent_span(source, *owners(source, regions=narrow)))
    with pytest.raises(PdfError):
        plan_adjacent_span_edit(source, state, EDIT, boundary=BOUNDARY, provider=recipe)
    # The invisible follower is never silently overlapped by a wider owner rectangle.
    with pytest.raises(PdfError, match='foreign glyph capacity'):
        propose_adjacent_span(source, *owners(source))


@pytest.mark.parametrize('kind', ['different-parent', 'not-adjacent', 'reversed', 'follower-not-space',
                                  'follower-not-next', 'follower-region'])
def test_owner_and_follower_relation_refusals(world, tmp_path, kind):
    source, _, _ = world
    path, mcids, fmcid, fregion = source, (1, 2), 3, FOLLOWER_REGION
    # Rectangles that exclude the real follower, so only the relation is at issue.
    narrow = dict(REGIONS[1], x1=92)
    regions = None
    if kind == 'different-parent':
        path = make_source(tmp_path / 'parent.pdf', second_parent=1)
    elif kind == 'not-adjacent':
        mcids = (0, 2)
    elif kind == 'reversed':
        mcids, regions, fmcid = (2, 1), [narrow, REGIONS[0]], None
    elif kind == 'follower-not-space':
        fmcid, regions = 0, [REGIONS[0], narrow]
    elif kind == 'follower-not-next':
        fmcid, regions = 4, [REGIONS[0], narrow]
    elif kind == 'follower-region':
        fregion = dict(FOLLOWER_REGION, y1=200)
    with pytest.raises(PdfError, match='adjacent Span'):
        propose_adjacent_span(path, *owners(path, mcids, regions),
                              follower=follower(path, fmcid, fregion) if fmcid is not None else None)


def mutate(source, target, kind):
    writer = PdfWriter(PdfReader(source), full=True)
    root = writer.root_object['/StructTreeRoot']; parent = root['/K'][0].get_object()['/K'][0].get_object()
    refs = parent['/K']; second = refs[2].get_object()
    if kind == 'actualtext-mismatch': second[NameObject('/ActualText')] = TextStringObject('違う')
    elif kind == 'bad-parenttree': root['/ParentTree']['/Nums'][1][2] = refs[1]
    elif kind == 'bad-backlink': second[NameObject('/P')] = root.indirect_reference
    elif kind == 'follower-lang-owner': refs[3].get_object()[NameObject('/Alt')] = TextStringObject('space')
    else: raise AssertionError(kind)
    writer.write(target)
    return target


@pytest.mark.parametrize('kind', ['actualtext-mismatch', 'bad-parenttree', 'bad-backlink', 'follower-lang-owner'])
def test_structure_refusals(world, tmp_path, kind):
    source, _, _ = world
    bad = mutate(source, tmp_path / (kind + '.pdf'), kind)
    with pytest.raises(PdfError):
        propose_adjacent_span(bad, *owners(bad), follower=follower(bad))


@pytest.mark.parametrize('kind', ['backward', 'inserted-crosses', 'boundary-edit', 'two-owners', 'coverage',
                                  'missing-provider', 'capacity', 'no-boundary'])
def test_split_and_font_refusals_publish_nothing(world, tmp_path, kind):
    source, proposal, recipe = world
    state = accept_adjacent_span(source, proposal)
    edit, boundary = dict(EDIT), BOUNDARY
    if kind == 'backward': boundary = BOUNDARY + 3
    elif kind == 'inserted-crosses': boundary = 12
    elif kind == 'boundary-edit': edit = dict(start=len(FIRST), end=len(FIRST), text='を')
    elif kind == 'two-owners': edit = dict(start=len(FIRST) - 1, end=len(FIRST) + 1, text='を')
    elif kind == 'coverage': edit['text'] = 'につき髙して'
    elif kind == 'missing-provider': recipe = None
    elif kind == 'capacity': boundary = BOUNDARY + 2
    elif kind == 'no-boundary': boundary = None
    with pytest.raises(PdfError):
        edit_adjacent_span(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', edit,
                           boundary=boundary, provider=recipe)
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()


def test_follower_destination_must_not_hit_foreign_glyph(tmp_path):
    path = make_source(tmp_path / 'foreign.pdf', foreign=115)
    recipe = provider(path, tmp_path)
    # The rectangle stops before the foreign glyph; the follower would have to land on it.
    state = accept_adjacent_span(path, propose_adjacent_span(path, *owners(path, regions=[REGIONS[0], dict(REGIONS[1], x1=114)]),
                                                             follower=follower(path, region=dict(FOLLOWER_REGION, x1=114))))
    with pytest.raises(PdfError, match='follower'):
        plan_adjacent_span_edit(path, state, EDIT, boundary=BOUNDARY, provider=recipe)
    with pytest.raises(PdfError, match='foreign glyph capacity'):
        propose_adjacent_span(path, *owners(path), follower=follower(path))


def test_new_glyphs_must_fit_the_second_owner_clip(tmp_path):
    path = make_source(tmp_path / 'clip.pdf')
    writer = PdfWriter(PdfReader(path), full=True); page = writer.pages[0]
    data = page.get_contents().get_data().replace(b'/P <</MCID 2>> BDC q 0 0 595.28 841.89 re',
                                                  b'/P <</MCID 2>> BDC q 0 0 95 841.89 re')
    stream = DecodedStreamObject(); stream.set_data(data); page[NameObject('/Contents')] = writer._add_object(stream)
    clipped = tmp_path / 'clipped.pdf'; writer.write(clipped)
    recipe = provider(clipped, tmp_path)
    state = accept_adjacent_span(clipped, propose_adjacent_span(clipped, *owners(clipped), follower=follower(clipped)))
    with pytest.raises(PdfError, match='clip'):
        edit_adjacent_span(clipped, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', EDIT,
                           boundary=BOUNDARY, provider=recipe)
    assert not (tmp_path / 'bad.pdf').exists()


def test_stale_and_forged_states_need_confirmation(world, tmp_path):
    source, proposal, recipe = world
    state = accept_adjacent_span(source, proposal)
    edited, current, _ = save(source, state, tmp_path, 'edit', EDIT, BOUNDARY, recipe)
    assert open_adjacent_span(edited, state)['status'] == 'needs_confirmation'
    with pytest.raises(PdfError):
        edit_adjacent_span(edited, state, tmp_path / 'stale.pdf', tmp_path / 'stale.json')
    forged = deepcopy(proposal); forged['witness']['follower'] = None; forged = _seal(forged)
    with pytest.raises(PdfError):
        accept_adjacent_span(source, forged)
    for kind in ('binding', 'follower-region', 'region'):
        changed = deepcopy(current)
        if kind == 'binding': changed['owners'][1]['font_binding']['generated'] = {}
        elif kind == 'follower-region': changed['follower']['region'] = dict(FOLLOWER_REGION, y1=200)
        else: changed['owners'][0]['region'] = dict(REGIONS[0], x1=200)
        assert open_adjacent_span(edited, _seal(changed))['status'] == 'needs_confirmation'


@pytest.mark.parametrize('phase', ['one-sided-actualtext', 'single-owner-update', 'semantic', 'sidecar-publication'])
def test_partial_semantics_or_publication_publishes_neither(world, tmp_path, monkeypatch, phase):
    source, proposal, recipe = world
    state = accept_adjacent_span(source, proposal)
    output, sidecar = tmp_path / 'bad.pdf', tmp_path / 'bad.json'
    import pdfeditor.source_ownership as ownership
    original = ownership.adjacent_span_update
    if phase == 'one-sided-actualtext':
        def stale_second(transaction):
            update = original(transaction)
            update['owners'][1]['after'] = update['owners'][1]['before']
            return update
        monkeypatch.setattr(ownership, 'adjacent_span_update', stale_second)
    elif phase == 'single-owner-update':
        def single(transaction):
            update = original(transaction)
            update['owners'] = update['owners'][:1]
            return update
        monkeypatch.setattr(ownership, 'adjacent_span_update', single)
    elif phase == 'semantic':
        def fail(*args): raise PdfError('injected semantic failure')
        monkeypatch.setattr(ownership, 'verify_adjacent_span_output', fail)
    else:
        import os
        from pathlib import Path
        link = os.link
        def fail(src, dst, *args, **kwargs):
            if Path(dst) == sidecar: raise OSError('injected sidecar publication failure')
            return link(src, dst, *args, **kwargs)
        monkeypatch.setattr(os, 'link', fail)
    with pytest.raises((PdfError, OSError)):
        edit_adjacent_span(source, state, output, sidecar, EDIT, boundary=BOUNDARY, provider=recipe)
    assert not output.exists() and not sidecar.exists()


def test_transaction_binds_exactly_the_approved_plans(world, tmp_path):
    source, proposal, _ = world
    state = accept_adjacent_span(source, proposal)
    with Transaction(source) as transaction:
        first = _plan_owner(transaction, state, 0, [], None)
        second = _plan_owner(transaction, state, 1, [], None)
        with pytest.raises(PdfError, match='removal-only'):
            transaction.commit(tmp_path / 'bad.pdf', removal_output=tmp_path / 'removed.pdf')
        # The follower is approved, so omitting its plan is refused.
        with pytest.raises(PdfError, match='follower'):
            transaction.commit(tmp_path / 'bad.pdf')
    with Transaction(source) as transaction:
        _plan_owner(transaction, state, 0, [], None)
        transaction.page(1).add(Plan('unrelated'))
        with pytest.raises(PdfError, match='exactly its owner'):
            transaction.commit(tmp_path / 'bad.pdf')
    unbound = deepcopy(state); unbound['witness']['follower'] = None; unbound['follower'] = None
    with Transaction(source) as transaction:
        first = _plan_owner(transaction, unbound, 0, [], None)
        _plan_owner(transaction, unbound, 1, [], None)
        first.adjacent_span_witness = deepcopy(first.adjacent_span_witness)
        first.adjacent_span_witness['first']['mcid'] = 9
        with pytest.raises(PdfError, match='different witnesses'):
            transaction.commit(tmp_path / 'bad.pdf')
    assert not list(tmp_path.iterdir())
