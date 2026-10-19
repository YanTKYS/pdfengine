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


def save(path, state, root, name, edits, *, provider=None):
    pdf, sidecar = root / (name + '.pdf'), root / (name + '.json')
    edit_single_span(path, state, pdf, sidecar, edits, provider=provider)
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


def pinned_provider(source, proposal, root):
    import hashlib
    with pymupdf.open(source) as doc:
        data = doc.extract_font(proposal['paragraph']['styles'][0]['font_xref'])[3]
    path = root / 'provider.ttf'; path.write_bytes(data)
    return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), font_index=0)


def glyph_records(path, ids):
    content = ContentPage(path, 1)
    try:
        return [dict(text=ch.text, code=ch.code.hex(), gid=content.actual[i]['gid'],
                     resource=e.state.font.name, font_xref=e.state.font.xref)
                for e in content.events for ch in e.chars for i in ch.source_orders if i in ids]
    finally:
        content.close()


@pytest.mark.parametrize('start,end,text', [(4, 6, '申請'), (4, 6, '申'), (4, 6, '申請書'), (4, 4, '申請')])
def test_nonempty_replacement_and_insertion_preserve_native_glyphs(world, tmp_path, start, end, text):
    from pdfeditor.span_edit import plan_single_span_edit
    source, proposal = world
    state = accept_single_span(source, proposal)
    provider = pinned_provider(source, proposal, tmp_path)
    edits = [dict(start=start, end=end, text=text)]
    plan = plan_single_span_edit(source, state, edits, provider=provider)
    assert plan['provided_font_glyph_count'] == len(text)
    assert plan['new_line_count'] == 1
    out, current = save(source, state, tmp_path, 'replacement', edits, provider=provider)
    assert current['witness']['text'] == TEXT[:start] + text + TEXT[end:]
    assert current['witness']['structure'] == state['witness']['structure']
    assert current['witness']['clip'] == state['witness']['clip']
    old = glyph_records(source, state['selection']['glyph_ids'])
    new = glyph_records(out, current['selection']['glyph_ids'])
    assert new[:start] == old[:start] and new[start + len(text):] == old[end:]
    assert all(g['resource'] != old[0]['resource'] for g in new[start:start+len(text)])
    assert len(current['font_binding']['generated']) == 1
    # _restore independently checks current subset outlines, advances and
    # Unicode/GID against the original embedded provider on every reopen.
    assert open_single_span(out, current)['status'] == 'restored'
    noop, unchanged = save(out, current, tmp_path, 'noop-replacement', [])
    assert raster(out) == raster(noop)
    # Replace across the physical native/generated font boundary under the
    # same approved appearance, then retain the resulting glyphs in deletion.
    again, edited = save(noop, unchanged, tmp_path, 'again', [dict(start=3, end=6, text='提出')], provider=provider)
    final, last = save(again, edited, tmp_path, 'deleted', [dict(start=2, end=3, text='')])
    assert last['witness']['structure'] == state['witness']['structure']


@pytest.mark.parametrize('kind', ['missing', 'bad-sha', 'wrong-file', 'bad-face', 'extra', 'coverage', 'capacity',
                                 'head-insert', 'tail-insert', 'newline', 'ignorable', 'combining'])
def test_provider_and_new_edit_refusals_publish_neither_artifact(world, tmp_path, kind):
    source, proposal = world
    state = deepcopy(proposal); state['status'] = 'confirmed'; state = _seal(state)
    provider = pinned_provider(source, proposal, tmp_path)
    edit = dict(start=4, end=6, text='申請')
    if kind == 'missing': provider = None
    elif kind == 'bad-sha': provider['sha256'] = '0' * 64
    elif kind == 'wrong-file':
        provider['path'] = str(tmp_path / 'missing.ttf')
    elif kind == 'bad-face': provider['font_index'] = 1
    elif kind == 'extra': provider['fallback'] = 'guess'
    elif kind == 'coverage': edit['text'] = '髙'
    elif kind == 'capacity': edit['text'] = '申請書' * 12
    elif kind == 'head-insert': edit.update(start=0, end=0)
    elif kind == 'tail-insert': edit.update(start=len(TEXT), end=len(TEXT))
    elif kind == 'newline': edit['text'] = '申\n請'
    elif kind == 'ignorable': edit['text'] = '\u200b'
    elif kind == 'combining': edit['text'] = '申\u0301'
    with pytest.raises(PdfError):
        edit_single_span(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', [edit], provider=provider)
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()


def test_generated_registry_and_stale_revision_are_not_authority(world, tmp_path):
    source, proposal = world; state = accept_single_span(source, proposal)
    provider = pinned_provider(source, proposal, tmp_path)
    out, current = save(source, state, tmp_path, 'generated', [dict(start=4, end=6, text='申請')], provider=provider)
    assert open_single_span(out, state)['status'] == 'needs_confirmation'
    for kind in ('missing', 'hash', 'instance', 'style', 'unrecorded'):
        forged = deepcopy(current); binding = forged['font_binding']; alias = next(iter(binding['generated']))
        if kind == 'missing': del forged['font_binding']
        elif kind == 'hash': binding['generated'][alias]['subset_sha256'] = '0' * 64
        elif kind == 'instance': binding['generated'][alias]['provider']['instance_sha256'] = '0' * 64
        elif kind == 'style': binding['appearance']['font_size'] = 1
        elif kind == 'unrecorded': binding['generated'] = {}
        assert open_single_span(out, _seal(forged))['status'] == 'needs_confirmation'


def test_new_glyph_ink_must_fit_original_clip(world, tmp_path):
    source, _ = world
    writer = PdfWriter(PdfReader(source), full=True); page = writer.pages[0]
    data = page.get_contents().get_data()
    data = data.replace(b'/MCID 1>> BDC q 0 0 595.28 841.89 re', b'/MCID 1>> BDC q 100 0 127 841.89 re')
    stream = DecodedStreamObject(); stream.set_data(data); page[NameObject('/Contents')] = writer._add_object(stream)
    clipped = tmp_path / 'clipped.pdf'; writer.write(clipped)
    proposal = propose_single_span(clipped, selection(clipped), region=REGION)
    state = accept_single_span(clipped, proposal); provider = pinned_provider(clipped, proposal, tmp_path)
    with pytest.raises(PdfError, match='clip'):
        edit_single_span(clipped, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json',
                         [dict(start=4, end=4, text='申請')], provider=provider)
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()


@pytest.mark.parametrize('kind', ['outline', 'advance'])
def test_matching_subset_hash_cannot_hide_changed_glyphs(world, tmp_path, kind):
    from io import BytesIO
    import hashlib
    from fontTools.ttLib import TTFont
    from pdfeditor.span_edit import _verify_font_binding
    source, proposal = world; state = accept_single_span(source, proposal)
    recipe = pinned_provider(source, proposal, tmp_path)
    out, current = save(source, state, tmp_path, 'generated', [dict(start=4, end=6, text='申請')], provider=recipe)
    alias = next(iter(current['font_binding']['generated']))
    glyph = glyph_records(out, current['selection']['glyph_ids'])[4]
    writer = PdfWriter(PdfReader(out), full=True)
    font = writer.pages[0]['/Resources']['/Font'][alias]
    descriptor = font['/DescendantFonts'][0].get_object()['/FontDescriptor']
    program = TTFont(BytesIO(descriptor['/FontFile2'].get_data()))
    name = program.getGlyphName(glyph['gid'])
    if kind == 'advance':
        width, lsb = program['hmtx'][name]; program['hmtx'][name] = (width + 7, lsb)
    else:
        program['glyf'][name].coordinates[0] = (10, 10)
    data = BytesIO(); program.save(data)
    stream = DecodedStreamObject(); stream.set_data(data.getvalue())
    descriptor[NameObject('/FontFile2')] = writer._add_object(stream)
    changed = tmp_path / 'changed-font.pdf'; writer.write(changed)
    forged = deepcopy(current['font_binding'])
    forged['generated'][alias]['subset_sha256'] = hashlib.sha256(data.getvalue()).hexdigest()
    with pytest.raises(PdfError, match='outline or advance'):
        _verify_font_binding(changed, current['paragraph'], forged)


@pytest.mark.parametrize('phase', ['semantic', 'sidecar-publication'])
def test_nonempty_failure_publishes_neither_pdf_nor_sidecar(world, tmp_path, monkeypatch, phase):
    source, proposal = world; state = accept_single_span(source, proposal)
    provider = pinned_provider(source, proposal, tmp_path)
    output, sidecar = tmp_path / 'bad.pdf', tmp_path / 'bad.json'
    if phase == 'semantic':
        import pdfeditor.source_ownership as ownership
        def fail(*args): raise PdfError('injected semantic failure')
        monkeypatch.setattr(ownership, 'verify_single_span_output', fail)
    else:
        import os
        from pathlib import Path
        original = os.link
        def fail(src, dst, *args, **kwargs):
            if Path(dst) == sidecar: raise OSError('injected sidecar publication failure')
            return original(src, dst, *args, **kwargs)
        monkeypatch.setattr(os, 'link', fail)
    with pytest.raises((PdfError, OSError)):
        edit_single_span(source, state, output, sidecar, [dict(start=4, end=6, text='申請')], provider=provider)
    assert not output.exists() and not sidecar.exists()
