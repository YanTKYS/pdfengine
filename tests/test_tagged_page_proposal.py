"""B2: unchanged structure, changing visual lines, current-revision ownership."""
import json

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from pdfeditor.backend import PdfError
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, replace_in_flow
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.source_ownership import inventory
from test_page_proposal import lines, pixels
import page_flow_fixture as fx


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp('tagged-page')
    font = fx.write_fonts(root / 'fonts')['full']
    pdf = fx.make_page(root / 'tagged.pdf', font.read_bytes(), tree_backed=True)
    proposal = propose_page_flow(pdf, 1, font_roots=[font.parent])
    return pdf, font, proposal


def structure(path):
    reader = PdfReader(path)
    root = reader.trailer['/Root']['/StructTreeRoot']
    # repr of indirect objects embeds a reader address; compare canonical PDF
    # object serialization instead, retaining actual indirect reference IDs.
    from io import BytesIO
    def encoded(obj):
        stream = BytesIO(); obj.write_to_stream(stream); return stream.getvalue()
    document = root['/K'][0].get_object()
    return (encoded(root), encoded(root['/ParentTree']), encoded(document),
            [encoded(o.get_object()) for o in document['/K']], reader.pages[0]['/StructParents'])


def verify(path, original):
    assert structure(path) == structure(original)
    observed = observe_marked_content(path)
    assert observed['complete']
    assert [s['association']['mcid'] for s in observed['scopes']] == list(range(7))
    for scope in observed['scopes']:
        assert scope['association']['status'] == 'tree_backed'
        assert scope['association']['backlink_verified']
        assert scope['association']['struct_parents'] == 0
    return observed


def save(pdf, state, root, name, changes):
    out, sidecar = root / (name + '.pdf'), root / (name + '.json')
    edit_shared_flow(pdf, state, out, sidecar, changes)
    opened = open_shared_flow(out, json.loads(sidecar.read_text(encoding='utf-8')))
    assert opened['status'] == 'restored', opened.get('reason')
    return out, opened['state']


def test_tree_backed_tagged_lifecycle(world, tmp_path):
    pdf, font, proposal = world
    assert proposal['status'] == 'proposed', proposal['refusals']
    assert proposal == propose_page_flow(pdf, 1, font_roots=[font.parent])
    assert [p['marked_content']['mcids'] for p in proposal['paragraphs']] == [[1, 2], [3], [4, 5]]
    assert proposal['reproduction']['status'] == 'reproduced'
    verify(pdf, pdf)
    state = accept_page_flow(pdf, proposal)
    first, state = save(pdf, state, tmp_path, 'first', replace_in_flow(state, *fx.FIRST_EDIT))
    verify(first, pdf)
    assert lines(first)[3] == (205.0, '各種申請書を提出してください。')
    assert lines(first)[:3] == lines(pdf)[:3] and lines(first)[4:] == lines(pdf)[4:]
    second, state = save(first, state, tmp_path, 'second', replace_in_flow(state, *fx.SECOND_EDIT))
    verify(second, pdf)
    assert len(lines(second)) == len(lines(pdf)) + 1
    assert lines(second)[4][0] == 223 and lines(second)[5][0] == 250
    # Two visual lines live under MCID 3; no fresh MCID/ParentTree entry.
    assert state['slots']['slot-1']['source_output']['current']['marked_structure']['mcids'] == [3]
    shrunk, state = save(second, state, tmp_path, 'shrunk',
                         replace_in_flow(state, fx.PARAGRAPHS[0], '申請書'))
    verify(shrunk, pdf)
    assert len(lines(shrunk)) == len(lines(pdf))
    noop, state = save(shrunk, state, tmp_path, 'noop', {})
    verify(noop, pdf)
    assert PdfReader(noop).pages[0].get_contents().get_data() == PdfReader(shrunk).pages[0].get_contents().get_data()
    empty, state = save(noop, state, tmp_path, 'empty', replace_in_flow(state, '申請書', '', paragraph_id='P1'))
    verify(empty, pdf)
    regrow, state = save(empty, state, tmp_path, 'regrow', {'P1': {'edits': [
        dict(start=0, end=0, text='申請書', style_id='style-1')]}})
    verify(regrow, pdf)
    for path in (first, second, shrunk, noop, empty, regrow):
        data = PdfReader(path).pages[0].get_contents().get_data()
        assert len(inventory(data)) == 3
        assert data.count(b'BDC') == data.count(b'EMC') == 7
        for rect in ((0, 100, fx.PAGE[0], 126), (0, 785, fx.PAGE[0], 815)):
            assert pixels(path, rect) == pixels(pdf, rect)


def mutate(pdf, target, kind):
    w = PdfWriter(clone_from=PdfReader(pdf)); p = w.pages[0]
    root = w._root_object['/StructTreeRoot']; tree = root['/ParentTree']; owners = root['/K'][0].get_object()['/K']
    owner = owners[1].get_object()
    data = p.get_contents().get_data()
    if kind == 'orphan': del w._root_object['/StructTreeRoot']
    elif kind == 'parent-missing': del root['/ParentTree']
    elif kind == 'duplicate-parent-key': tree['/Nums'].extend(tree['/Nums'][:])
    elif kind == 'bad-structparents': p[NameObject('/StructParents')] = NumberObject(-1)
    elif kind == 'outside-array': tree['/Nums'][1] = ArrayObject([])
    elif kind == 'not-structelem': owner[NameObject('/Type')] = NameObject('/Bad')
    elif kind == 'missing-backlink': owner[NameObject('/K')] = ArrayObject([])
    elif kind == 'duplicate-backlink': owner['/K'].append(NumberObject(1))
    elif kind == 'wrong-page': owner[NameObject('/Pg')] = root.indirect_reference
    elif kind == 'ancestor-backlink': root[NameObject('/K')] = ArrayObject([])
    elif kind == 'owner-actualtext': owner[NameObject('/ActualText')] = TextStringObject('alternate')
    elif kind == 'owner-layout': owner[NameObject('/A')] = DictionaryObject({NameObject('/O'): NameObject('/Layout')})
    elif kind == 'owner-namespace': owner[NameObject('/NS')] = DictionaryObject()
    elif kind == 'bad-ancestor': root['/K'][0].get_object()[NameObject('/Type')] = NameObject('/Bad')
    elif kind == 'missing-ancestor-role': del root['/K'][0].get_object()['/S']
    elif kind == 'bad-root': root[NameObject('/Type')] = NameObject('/Bad')
    elif kind == 'string-role': owner[NameObject('/S')] = TextStringObject('/P')
    elif kind == 'string-type': owner[NameObject('/Type')] = TextStringObject('/StructElem')
    elif kind == 'reversed-order': owner[NameObject('/K')] = ArrayObject([NumberObject(2), NumberObject(1)])
    elif kind == 'mcr': owner[NameObject('/K')] = ArrayObject([DictionaryObject({NameObject('/Type'): NameObject('/MCR'),
        NameObject('/MCID'): NumberObject(i), NameObject('/Pg'): p.indirect_reference}) for i in (1, 2)])
    elif kind == 'unrelated-owners':
        other = DictionaryObject(owner)
        other[NameObject('/K')] = ArrayObject([NumberObject(2)])
        ref = w._add_object(other)
        owners.insert(2, ref)
        owner[NameObject('/K')] = ArrayObject([NumberObject(1)])
        tree['/Nums'][1][2] = ref
    elif kind == 'split-line':
        from pdfeditor.content_stream import operators
        scope = observe_marked_content(pdf)['scopes'][1]
        op = next(o for o in operators(data) if o.name == 'Tj' and scope['content_byte_range'][0] < o.start < scope['content_byte_range'][1])
        raw = bytes(op.args[0]); midpoint = len(raw) // 4 * 2
        replacement = (b'<' + raw[:midpoint].hex().encode() + b'> Tj EMC /P <</MCID 2>> BDC <' +
                       raw[midpoint:].hex().encode() + b'> Tj')
        data = data[:op.start] + replacement + data[op.end:]
        data = data.replace(b'ET EMC\n/P <</MCID 2>> BDC BT', b'ET BT', 1)
    elif kind == 'form':
        from pypdf.generic import DecodedStreamObject
        form = DecodedStreamObject(); form.set_data(data)
        form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
            NameObject('/BBox'): p['/MediaBox'], NameObject('/Resources'): p['/Resources'],
            NameObject('/StructParents'): NumberObject(0)})
        ref = w._add_object(form)
        p[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/F'): ref})})
        data = b'/F Do'
    else:
        substitutions = {
            'actualtext': (b'/MCID 1', b'/ActualText (x) /MCID 1'),
            'oc': (b'/MCID 1', b'/OC /Hidden /MCID 1'),
            'artifact': (b'/P <</MCID 1', b'/Artifact <</MCID 1'),
            'malformed': (b'EMC', b''),
            'nested-empty': (b'/P <</MCID 1>> BDC', b'/P <</MCID 1>> BDC /Artifact BMC EMC'),
            'duplicate-mcid': (b'/MCID 2', b'/MCID 1'),
            'unknown-property': (b'/MCID 1', b'/Lang (ja) /MCID 1'),
            'paint-state': (b'/P <</MCID 2>> BDC', b'2 w /P <</MCID 2>> BDC'),
        }
        old, new = substitutions[kind]; data = data.replace(old, new, 1)
    from pypdf.generic import DecodedStreamObject
    stream = DecodedStreamObject(); stream.set_data(data)
    p[NameObject('/Contents')] = w._add_object(stream)
    w.write(target)
    return target


@pytest.mark.parametrize('kind', ['orphan', 'parent-missing', 'duplicate-parent-key', 'bad-structparents',
    'outside-array', 'not-structelem', 'missing-backlink', 'duplicate-backlink', 'wrong-page',
    'ancestor-backlink', 'owner-actualtext', 'owner-layout', 'reversed-order', 'mcr', 'actualtext', 'oc',
    'artifact', 'malformed', 'nested-empty', 'duplicate-mcid', 'unknown-property',
    'unrelated-owners', 'split-line', 'form', 'paint-state', 'owner-namespace', 'bad-ancestor',
    'missing-ancestor-role', 'bad-root', 'string-role', 'string-type'])
def test_tagged_refusals(world, tmp_path, kind):
    pdf, font, _ = world
    bad = mutate(pdf, tmp_path / (kind + '.pdf'), kind)
    proposal = propose_page_flow(bad, 1, font_roots=[font.parent])
    assert proposal['status'] == 'refused', proposal
    assert any(r['code'] in ('unsupported-for-persistent-flow', 'paragraph-evidence') for r in proposal['refusals'])
    with pytest.raises(PdfError): accept_page_flow(bad, proposal)


def test_structure_changes_invalidate_proposal_and_saved_state(world, tmp_path):
    pdf, _, proposal = world
    changed = mutate(pdf, tmp_path / 'changed.pdf', 'missing-backlink')
    with pytest.raises(PdfError, match='another PDF revision'):
        accept_page_flow(changed, proposal)
    out, state = save(pdf, accept_page_flow(pdf, proposal), tmp_path, 'saved', {})
    changed = mutate(out, tmp_path / 'changed-saved.pdf', 'owner-actualtext')
    assert open_shared_flow(changed, state)['status'] == 'needs_confirmation'


def test_current_witness_reobserves_structure_without_relying_on_pdf_sha(world, tmp_path):
    from pdfeditor import source_ownership
    pdf, _, proposal = world
    out, state = save(pdf, accept_page_flow(pdf, proposal), tmp_path, 'saved', {})
    changed = mutate(out, tmp_path / 'changed.pdf', 'owner-actualtext')
    with pytest.raises(PdfError, match='marked structure'):
        source_ownership.validate(changed, state)


def test_resealed_tagged_proposal_is_not_authority(world):
    from test_page_proposal import resealed
    pdf, _, proposal = world
    forged = resealed(proposal, lambda p: p['paragraphs'][0]['marked_content'].update(mcids=[999]))
    with pytest.raises(PdfError, match='stale'):
        accept_page_flow(pdf, forged)


def test_tagged_serialization_is_deterministic(world, tmp_path):
    pdf, _, proposal = world
    state = accept_page_flow(pdf, proposal)
    changes = replace_in_flow(state, *fx.FIRST_EDIT)
    a, sa = save(pdf, state, tmp_path, 'a', changes)
    b, sb = save(pdf, state, tmp_path, 'b', changes)
    assert PdfReader(a).pages[0].get_contents().get_data() == PdfReader(b).pages[0].get_contents().get_data()
    assert [s['source_output'] for s in sa['slots'].values()] == [s['source_output'] for s in sb['slots'].values()]


@pytest.mark.parametrize('tree_backed', [True, False])
def test_existing_single_scope_one_shot_remains_available(world, tmp_path, tree_backed):
    from pdfeditor.attributed import inspect_paragraph
    from pdfeditor.paragraph import edit_paragraph
    from pdfeditor.selection import make_selection
    original, font, proposal = world
    pdf = original if tree_backed else fx.make_page(tmp_path / 'orphan.pdf', font.read_bytes(), tagged=True)
    selected = make_selection(pdf, 1, glyph_ids=proposal['paragraphs'][1]['glyph_ids'],
                              explicit_width=proposal['region']['width'])
    paragraph = inspect_paragraph(pdf, selected)
    out = tmp_path / 'one-shot.pdf'
    edit_paragraph(pdf, out, paragraph, [dict(start=0, end=3, text='各種申請書')],
                   fonts={'s0': {'path': str(font)}})
    assert lines(out)[3] == (205.0, '各種申請書を提出してください。')
    assert not inventory(PdfReader(out).pages[0].get_contents().get_data())
    if tree_backed:
        verify(out, pdf)
