"""Single leaf semantics, independent of B2 same-owner paragraph aggregation."""
from copy import deepcopy
import json
import subprocess

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject, DecodedStreamObject

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content, structure_objects
from pdfeditor.page_proposal import propose_single_span, accept_single_span
from pdfeditor.selection import make_selection
from pdfeditor.span_edit import edit_single_span, open_single_span, _plan, _seal
from pdfeditor.transaction import Transaction
import page_flow_fixture as fx

TEXT = '本年度の補助事業について'
REGION = dict(x0=99, y0=148, x1=260, y1=164)


def selection(path, mcid=1, extra=()):
    observed = observe_marked_content(path)
    scope = next(s for s in observed['scopes'] if s['properties'].get('/MCID') == mcid)
    lo, hi = scope['content_byte_range']
    content = ContentPage(path, 1)
    try:
        ids = [i for e in content.events if lo <= e.operator.start < hi for ch in e.chars for i in ch.source_orders]
    finally:
        content.close()
    return make_selection(path, 1, glyph_ids=ids + list(extra), explicit_width=160)


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp('single-span')
    path = fx.make_page(root / 'source.pdf', fx.build_font(), tagged=True,
        lines=[(50, 160, 10.5, '申請書'), (100, 160, 10.5, TEXT), (300, 160, 10.5, '提出してください。')])
    writer = PdfWriter(PdfReader(path), full=True)
    page = writer.pages[0]
    tree = DictionaryObject({NameObject('/Type'): NameObject('/StructTreeRoot')})
    tree_ref = writer._add_object(tree)
    paragraph = DictionaryObject({NameObject('/Type'): NameObject('/StructElem'), NameObject('/S'): NameObject('/P'),
        NameObject('/P'): tree_ref, NameObject('/Pg'): page.indirect_reference})
    p_ref = writer._add_object(paragraph)
    owners = []
    for mcid, text in enumerate(('申請書', TEXT, '提出してください。')):
        owners.append(writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/StructElem'),
            NameObject('/S'): NameObject('/Span'), NameObject('/P'): p_ref, NameObject('/Pg'): page.indirect_reference,
            NameObject('/K'): ArrayObject([NumberObject(mcid)]), NameObject('/ActualText'): TextStringObject(text)})))
    paragraph[NameObject('/K')] = ArrayObject(owners)
    tree[NameObject('/K')] = ArrayObject([p_ref])
    tree[NameObject('/ParentTree')] = writer._add_object(DictionaryObject({NameObject('/Nums'):
        ArrayObject([NumberObject(0), ArrayObject(owners)])}))
    writer.root_object[NameObject('/StructTreeRoot')] = tree_ref
    page[NameObject('/StructParents')] = NumberObject(0)
    data = page.get_contents().get_data().replace(b'BDC BT', b'BDC q 0 0 595.28 841.89 re W* n BT').replace(b'ET EMC', b'ET Q EMC')
    stream = DecodedStreamObject(); stream.set_data(data)
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    proposal = propose_single_span(path, selection(path), region=REGION)
    return path, proposal


def save(path, state, root, name, edits):
    pdf, sidecar = root / (name + '.pdf'), root / (name + '.json')
    edit_single_span(path, state, pdf, sidecar, edits)
    opened = open_single_span(pdf, json.loads(sidecar.read_text()))
    assert opened['status'] == 'restored', opened
    return pdf, opened['state']


def raster(path):
    with pymupdf.open(path) as doc:
        return doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).samples


def test_lifecycle_atomic_semantics_structure_clip_and_noop(world, tmp_path):
    source, proposal = world
    state = accept_single_span(source, proposal)
    first, updated = save(source, state, tmp_path, 'edit', [dict(start=4, end=6, text='')])
    assert updated['witness']['text'] == TEXT[:4] + TEXT[6:]
    assert updated['witness']['owner'] == state['witness']['owner']
    assert updated['witness']['structure'] == state['witness']['structure']
    assert updated['witness']['clip'] == state['witness']['clip']
    assert updated['witness']['mcid'] == 1
    noop, unchanged = save(first, updated, tmp_path, 'noop', [])
    assert PdfReader(first).pages[0].get_contents().get_data() == PdfReader(noop).pages[0].get_contents().get_data()
    assert raster(first) == raster(noop)
    images = []
    for path in (first, noop):
        prefix = tmp_path / (path.stem + '-poppler')
        subprocess.run(['pdftoppm', '-singlefile', '-r', '144', '-png', str(path), str(prefix)], check=True, capture_output=True)
        from PIL import Image
        images.append(Image.open(str(prefix) + '.png').tobytes())
    assert images[0] == images[1]
    second, last = save(noop, unchanged, tmp_path, 'reedit', [dict(start=1, end=2, text='')])
    assert last['witness']['text'] == updated['witness']['text'][:1] + updated['witness']['text'][2:]
    assert last['witness']['structure'] == state['witness']['structure']
    # Every original clipping operator remains, even across repeated edits.
    for path in (source, first, noop, second):
        data = PdfReader(path).pages[0].get_contents().get_data()
        assert data.count(b'0 0 595.28 841.89 re W* n') == 3


def mutate(source, target, kind):
    writer = PdfWriter(PdfReader(source), full=True)
    root = writer.root_object['/StructTreeRoot']; parent = root['/K'][0].get_object()
    refs = parent['/K']; owner = refs[1].get_object(); page = writer.pages[0]
    data = page.get_contents().get_data()
    if kind == 'actualtext-mismatch': owner[NameObject('/ActualText')] = TextStringObject('different')
    elif kind == 'absent-actualtext': del owner['/ActualText']
    elif kind == 'ancestor-actualtext': parent[NameObject('/ActualText')] = TextStringObject(TEXT)
    elif kind == 'ancestor-alt': parent[NameObject('/Alt')] = TextStringObject(TEXT)
    elif kind == 'multi-mcid': owner['/K'].append(NumberObject(0))
    elif kind == 'bad-parenttree': root['/ParentTree']['/Nums'][1][1] = refs[0]
    elif kind == 'bad-backlink': owner[NameObject('/P')] = root.indirect_reference
    elif kind == 'bad-k': owner[NameObject('/K')] = ArrayObject([NumberObject(8)])
    elif kind == 'duplicate-mcid': data = data.replace(b'MCID 2', b'MCID 1')
    elif kind == 'reversed-children': parent[NameObject('/K')] = ArrayObject(list(reversed(refs)))
    elif kind == 'clip': data = data.replace(b're W* n', b're W n')
    elif kind == 'clipped-text': data = data.replace(b'0 0 595.28 841.89 re', b'0 0 10 10 re')
    elif kind == 'graphics-state': data = data.replace(b'/MCID 1>> BDC q', b'/MCID 1>> BDC q 2 w')
    elif kind == 'unbalanced': data = data.replace(b'/MCID 1>> BDC q', b'/MCID 1>> BDC q q')
    elif kind == 'nested': data = data.replace(b'/MCID 1>> BDC q', b'/MCID 1>> BDC /Artifact BMC EMC q')
    elif kind == 'sibling-nonleaf': refs[0].get_object()[NameObject('/K')] = ArrayObject([refs[2]])
    else: raise AssertionError(kind)
    stream = DecodedStreamObject(); stream.set_data(data); page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(target)
    return target


@pytest.mark.parametrize('kind', ['actualtext-mismatch', 'absent-actualtext', 'ancestor-actualtext', 'ancestor-alt',
    'multi-mcid', 'bad-parenttree', 'bad-backlink', 'bad-k', 'duplicate-mcid', 'reversed-children',
    'clip', 'clipped-text', 'graphics-state', 'unbalanced', 'nested', 'sibling-nonleaf'])
def test_structure_and_clip_refusals(world, tmp_path, kind):
    source, _ = world
    bad = mutate(source, tmp_path / (kind + '.pdf'), kind)
    with pytest.raises(PdfError):
        propose_single_span(bad, selection(bad), region=REGION)


def test_partial_and_cross_owner_selection_refuse(world):
    source, _ = world
    selected = selection(source)
    for ids in (selected['glyph_ids'][1:], selected['glyph_ids'] + selection(source, 0)['glyph_ids']):
        with pytest.raises(PdfError):
            propose_single_span(source, make_selection(source, 1, glyph_ids=ids, explicit_width=200),
                                region=dict(x0=49, y0=148, x1=260, y1=164))


@pytest.mark.parametrize('edit', [dict(start=0, end=99, text=''), dict(start=1, end=2, text='追加'),
    dict(start=0, end=1, text=''), dict(start=1, end=2, text='', owner=999)])
def test_unsupported_edit_no_publication(world, tmp_path, edit):
    source, proposal = world
    state = deepcopy(proposal); state['status'] = 'confirmed'; state = _seal(state)
    with pytest.raises(PdfError):
        edit_single_span(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', [edit])
    assert not list(tmp_path.iterdir())


def test_region_and_stale_refusals(world, tmp_path):
    source, proposal = world
    for region in (dict(REGION, x1=150), dict(REGION, x1=400)):
        with pytest.raises(PdfError): propose_single_span(source, selection(source), region=region)
    forged = deepcopy(proposal); forged['witness']['mcid'] = 9; forged = _seal(forged)
    with pytest.raises(PdfError): accept_single_span(source, forged)
    bad = mutate(source, tmp_path / 'changed.pdf', 'actualtext-mismatch')
    state = deepcopy(proposal); state['status'] = 'confirmed'; state = _seal(state)
    assert open_single_span(bad, state)['status'] == 'needs_confirmation'


def test_atomic_semantic_failure_does_not_publish(world, tmp_path, monkeypatch):
    source, proposal = world
    state = deepcopy(proposal); state['status'] = 'confirmed'; state = _seal(state)
    import pdfeditor.source_ownership as ownership
    def fail(*args): raise PdfError('injected semantic output verification failure')
    monkeypatch.setattr(ownership, 'verify_single_span_output', fail)
    with pytest.raises(PdfError, match='semantic output'):
        edit_single_span(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', [dict(start=4, end=6, text='')])
    assert not list(tmp_path.iterdir())


def test_save_adapter_compare_and_set_failure_does_not_publish(world, tmp_path, monkeypatch):
    source, proposal = world
    state = deepcopy(proposal); state['status'] = 'confirmed'; state = _seal(state)
    import pdfeditor.source_ownership as ownership
    original = ownership.single_span_update
    def changed_before(*args):
        update = original(*args)
        return dict(update, before='not the current ActualText')
    monkeypatch.setattr(ownership, 'single_span_update', changed_before)
    with pytest.raises(PdfError, match='compare-and-set'):
        edit_single_span(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', [dict(start=4, end=6, text='')])
    assert not list(tmp_path.iterdir())


def test_transaction_revalidates_owner_and_forbids_removal_checkpoint(world, tmp_path):
    source, proposal = world
    with Transaction(source) as transaction:
        plan = _plan(transaction, proposal, [dict(start=4, end=6, text='')])
        with pytest.raises(PdfError, match='removal-only'):
            transaction.commit(tmp_path / 'bad.pdf', removal_output=tmp_path / 'removed.pdf')
        plan.single_span_witness = deepcopy(plan.single_span_witness)
        plan.single_span_witness['owner']['xref'] += 1
        with pytest.raises(PdfError, match='current ownership'):
            transaction.commit(tmp_path / 'bad.pdf')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('x,accepted', [('68.62', True), ('70', False)])
def test_existing_nominal_box_overlap_still_requires_ink_separation(world, tmp_path, x, accepted):
    source, _ = world
    writer = PdfWriter(PdfReader(source), full=True)
    page = writer.pages[0]
    data = page.get_contents().get_data()
    assert b'1 0 0 1 50 ' in data
    data = data.replace(b'1 0 0 1 50 ', b'1 0 0 1 ' + x.encode() + b' ', 1)
    stream = DecodedStreamObject(); stream.set_data(data); page[NameObject('/Contents')] = writer._add_object(stream)
    path = tmp_path / 'adjacent.pdf'; writer.write(path)
    region = dict(REGION, x0=100)
    if accepted:
        proposal = propose_single_span(path, selection(path), region=region)
        state = accept_single_span(path, proposal)
        save(path, state, tmp_path, 'shortened', [dict(start=4, end=6, text='')])
        # Moving the region into additional foreign em-box area is forbidden.
        with pytest.raises(PdfError, match='foreign glyph capacity'):
            propose_single_span(path, selection(path), region=dict(region, x0=99))
    else:
        with pytest.raises(PdfError, match='collides'):
            propose_single_span(path, selection(path), region=region)
