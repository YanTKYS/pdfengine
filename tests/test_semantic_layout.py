"""Runtime L1: shared-flow-3 semantic layout state (read-only, no PDF writer)."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import shutil

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from pypdf import PdfReader, PdfWriter
import pytest

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.editable import _seal
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow
from pdfeditor.story_flow import confirm_story
from pdfeditor import semantic_layout as semantic
from pdfeditor import source_ownership as owned
from pdfeditor.semantic_measure import font_policy
from test_attributed import source_pdf


def static_font(family='SemanticProof'):
    builder = FontBuilder(1000, isTTF=True)
    names = ['.notdef', 'A', 'B', 'space']
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({65: 'A', 66: 'B', 32: 'space'})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name != 'space':
            bottom = -200 if name == 'B' else 0
            pen.moveTo((0, bottom)); pen.lineTo((500, bottom)); pen.lineTo((500, 600)); pen.lineTo((0, 600)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({n: (600, 0) for n in names})
    builder.setupHorizontalHeader(ascent=600, descent=-200)
    builder.setupNameTable(dict(familyName=family, styleName='Regular', uniqueFontIdentifier=family + '-v1',
                                fullName=family, psName=family))
    builder.setupOS2(sTypoAscender=600, sTypoDescender=-200, usWinAscent=600, usWinDescent=200, fsType=0)
    builder.setupPost(); builder.setupMaxp()
    builder.font['head'].created = builder.font['head'].modified = 2082844800
    out = BytesIO(); builder.save(out)
    return out.getvalue()


def make_flow(root, flow_id='semantic-flow'):
    """Single paragraph / single slot / one region / one style; first save creates the owner."""
    root.mkdir(parents=True, exist_ok=True)
    source = source_pdf(root, b'BT /Regular 12 Tf 20 200 Td (XY) Tj ET')
    asset = root / 'asset.ttf'; asset.write_bytes(static_font())
    p = inspect_paragraph(source, make_selection(source, glyph_ids=[0, 1], explicit_width=150))
    story = confirm_story(source, {'part': dict(page=1, bounds=[18, 40, 200, 220], paragraph=p, paint_relations=[],
        layout=dict(x=20, baseline=200, width=150, max_bottom=220, min_line_height=22, first_line_indent=0))},
        paragraph_id='A', chain=['part'], protected_regions={},
        styles={'body': dict(provider=dict(path=str(asset)), provider_relation='substituted')},
        style_assignments={'part': {s['id']: 'body' for s in p['styles']}}, typing_style_id='body')
    state = confirm_shared_flow(source, {'A': story}, flow_id=flow_id, paragraph_order=['A'],
        regions={'R': dict(page=1, bounds=[18, 40, 200, 220], x=20, width=150, first_baseline=200)},
        region_order=['R'], slot_regions={'A': {'part': 'R'}},
        paragraph_policies={'A': dict(min_line_height=22, first_line_indent=0, keep_together=False,
            break_before='auto', break_after='auto', empty=dict(kind='reserve-line', ascent=10, descent=3))},
        follows=[], protected_regions={})
    (root / 'confirmed.json').write_text(json.dumps(state))
    change = lambda text: {'A': dict(edits=[dict(start=0, end=len(state['paragraphs']['A']['logical']['text']),
                                                 text=text, style_id='body')])}
    edit_shared_flow(source, state, root / 'rev1.pdf', root / 'rev1.json', change('A B'))
    rev1 = json.loads((root / 'rev1.json').read_text())
    edit_shared_flow(root / 'rev1.pdf', rev1, root / 'rev2.pdf', root / 'rev2.json',
                     {'A': dict(edits=[dict(start=1, end=1, text='A', style_id='body')])})
    return source, asset


def statement(asset, text='A B', **style):
    values = dict(font_size='12', horizontal_scale='1', rise='0', tracking='0', word_spacing='0',
                  spacing_intent='confirmed-inline')
    values.update(style)
    return dict(text=text, style=values, font=dict(sha=hashlib.sha256(asset.read_bytes()).hexdigest(), policy=font_policy()),
                body_style_id='body', edges=[], region_id='R')


def load(path):
    return json.loads(path.read_text())


@pytest.fixture(scope='module')
def flows(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic-layout')
    source, asset = make_flow(root / 'main')
    main = root / 'main'
    v3_rev1 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0',
                                               semantic=statement(asset))
    v3_rev2 = semantic.confirm_semantic_layout(main / 'rev2.pdf', main / 'rev2.json', slot_id='slot-0',
                                               semantic=statement(asset, 'AA B'))
    (main / 'rev1-v3.json').write_text(json.dumps(v3_rev1))
    (main / 'rev2-v3.json').write_text(json.dumps(v3_rev2))
    make_flow(root / 'other', 'other-flow')
    return dict(root=root, main=main, source=source, asset=asset, v3_rev1=v3_rev1, v3_rev2=v3_rev2,
                other=load(root / 'other' / 'rev1.json'))


def resealed(state, change):
    value = deepcopy(state); value.pop('model_sha256'); change(value); return _seal(value)


def refused(pdf, state, reason=None):
    result = semantic.open_semantic_flow(pdf, state)
    assert result['status'] == 'needs_confirmation', result.get('reason')
    if reason:
        assert reason in result['reason'], result['reason']
    return result['reason']


def test_explicit_confirmation_adds_semantic_state_to_the_owned_slot(flows):
    state = flows['v3_rev1']
    slot = state['slots']['slot-0']
    assert state['schema'] == semantic.SCHEMA and set(slot['semantic']) == semantic.SEMANTIC_KEYS
    assert slot['semantic']['payload'] == statement(flows['asset'])
    assert slot['semantic']['binding']['owner_block_sha256'] == slot['source_output']['current']['block_sha256']
    v2 = load(flows['main'] / 'rev1.json')
    assert {k: v for k, v in state.items() if k not in ('schema', 'slots', 'model_sha256')} == \
           {k: v for k, v in v2.items() if k not in ('schema', 'slots', 'model_sha256')}


def test_v2_is_never_upgraded_implicitly(flows):
    main = flows['main']
    v2 = open_shared_flow(main / 'rev1.pdf', main / 'rev1.json')
    assert v2['status'] == 'restored' and v2['state']['schema'] != semantic.SCHEMA
    assert all('semantic' not in s for s in v2['state']['slots'].values())
    assert semantic.open_semantic_flow(main / 'rev1.pdf', main / 'rev1.json')['status'] == 'needs_confirmation'
    assert open_shared_flow(main / 'rev1.pdf', main / 'rev1-v3.json')['status'] == 'needs_confirmation'


def test_confirmation_refuses_unowned_slot_and_non_v2_input(flows):
    with pytest.raises(PdfError):
        semantic.confirm_semantic_layout(flows['source'], flows['main'] / 'confirmed.json', slot_id='slot-0',
                                         semantic=statement(flows['asset'], 'XY'))
    with pytest.raises(PdfError, match='v2 owner sidecar'):
        semantic.confirm_semantic_layout(flows['main'] / 'rev1.pdf', flows['v3_rev1'], slot_id='slot-0',
                                         semantic=statement(flows['asset']))


def test_confirmation_never_infers_or_accepts_unwitnessed_intent(flows):
    main = flows['main']
    for bad in (statement(flows['asset'], 'AB'), statement(flows['asset'], word_spacing='6/5'),
                dict(statement(flows['asset']), edges=[dict(left=0, right=1, delta='1', operator='TJ',
                     semantics='confirmed-adjacent-pair', boundary_policy='suppress-at-line-end')]),
                statement(flows['asset'], font_size='12.0')):
        with pytest.raises(PdfError):
            semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0', semantic=bad)


def test_stored_v3_reopens_read_only_and_repeatably(flows):
    main = flows['main']
    before = (main / 'rev1.pdf').read_bytes(), (main / 'rev1-v3.json').read_bytes()
    results = [semantic.open_semantic_flow(main / 'rev1.pdf', main / 'rev1-v3.json') for _ in range(3)]
    assert all(r['status'] == 'restored' for r in results)
    assert results[0]['semantic'] == results[1]['semantic'] == results[2]['semantic']
    assert results[0]['owner'] == results[1]['owner'] == results[2]['owner']
    assert results[0]['derived'] == results[1]['derived'] == results[2]['derived']
    assert ((main / 'rev1.pdf').read_bytes(), (main / 'rev1-v3.json').read_bytes()) == before
    r = results[0]
    assert (r['slot_id'], r['paragraph_id'], r['region_id']) == ('slot-0', 'A', 'R')
    assert r['derived']['intervals'][2] == dict(start=2, end=3, id='current:2', style='body')
    assert r['derived']['omitted'] == [] and r['derived']['empty'] == dict(ascent='36/5', descent='12/5')


@pytest.mark.parametrize('name,change,reason', [
    ('text', lambda v: v['slots']['slot-0']['semantic']['payload'].update(text='B A'), 'semantic text'),
    ('style', lambda v: v['slots']['slot-0']['semantic']['payload']['style'].update(font_size='13'), 'body style'),
    ('font', lambda v: v['slots']['slot-0']['semantic']['payload']['font'].update(sha='0' * 64), 'font'),
    ('region', lambda v: v['slots']['slot-0']['semantic']['payload'].update(region_id='R2'), 'region'),
    ('binding_slot', lambda v: v['slots']['slot-0']['semantic']['binding'].update(slot_id='slot-9'), 'owner witness'),
    ('binding_paragraph', lambda v: v['slots']['slot-0']['semantic']['binding'].update(paragraph_id='B'), 'owner witness'),
    ('binding_region', lambda v: v['slots']['slot-0']['semantic']['binding'].update(region_id='R2'), 'owner witness'),
    ('derived', lambda v: v['slots']['slot-0']['semantic']['derived'].update(omitted=[{'offset': 1}]), 'derived'),
    ('history', lambda v: v['slots']['slot-0']['semantic'].update(history=[]), 'semantic payload'),
])
def test_resealed_semantic_tampering_refuses(flows, name, change, reason):
    refused(flows['main'] / 'rev1.pdf', resealed(flows['v3_rev1'], change), reason)


def test_stale_owner_witness_refuses(flows):
    old = flows['v3_rev1']['slots']['slot-0']['source_output']['current']
    stale = resealed(flows['v3_rev2'], lambda v: v['slots']['slot-0']['source_output'].update(current=deepcopy(old)))
    refused(flows['main'] / 'rev2.pdf', stale, 'ownership')


def test_stale_semantic_refuses_even_with_forged_current_binding(flows):
    old = flows['v3_rev1']['slots']['slot-0']['semantic']
    stale = resealed(flows['v3_rev2'], lambda v: v['slots']['slot-0'].update(semantic=deepcopy(old)))
    refused(flows['main'] / 'rev2.pdf', stale, 'owner witness')
    current_binding = flows['v3_rev2']['slots']['slot-0']['semantic']['binding']
    forged = resealed(stale, lambda v: v['slots']['slot-0']['semantic'].update(binding=deepcopy(current_binding)))
    refused(flows['main'] / 'rev2.pdf', forged, 'semantic text')


def test_old_owner_against_new_pdf_refuses(flows):
    refused(flows['main'] / 'rev2.pdf', flows['v3_rev1'])


def two_paragraphs(v):
    v['flow']['paragraphs'].append('B'); v['paragraphs']['B'] = deepcopy(v['paragraphs']['A'])


def mixed(v):
    registry = v['paragraphs']['A']['style_registry']; registry['accent'] = deepcopy(registry['body'])


@pytest.mark.parametrize('name,change,reason', [
    ('two_slots', lambda v: v['slots'].update({'slot-1': deepcopy(v['slots']['slot-0'])}), 'one source slot'),
    ('continuation', lambda v: v.update(continuation_destinations={'d': {'page': 1}}), 'continuation destinations'),
    ('generated_slot', lambda v: v['slots']['slot-0'].update(destination_id='d'), 'generated continuation'),
    ('unowned', lambda v: v['slots']['slot-0']['source_output'].update(state='uninitialized'), 'already owned'),
    ('second_paragraph', two_paragraphs, 'one paragraph'),
    ('second_region', lambda v: v['regions'].update(R2=deepcopy(v['regions']['R'])), 'one confirmed region'),
    ('mixed_style', mixed, 'one body style'),
])
def test_scope_refusals(flows, name, change, reason):
    refused(flows['main'] / 'rev1.pdf', resealed(flows['v3_rev1'], change), reason)


def test_copied_owner_record_refuses(flows):
    copied = flows['other']['slots']['slot-0']['source_output']
    refused(flows['main'] / 'rev1.pdf', resealed(flows['v3_rev1'],
            lambda v: v['slots']['slot-0'].update(source_output=deepcopy(copied))), 'source output ownership')


def marker_lines(data):
    begin = data.index(b'\n% pdfengine-source-slot-v1 begin')
    end = data.index(b'% pdfengine-source-slot-v1 end')
    return data[begin:data.index(b'\n', begin + 1) + 1], data[end:data.index(b'\n', end) + 1]


def physical_variant(tmp_path, flows, name, transform):
    pdf = flows['main'] / 'rev1.pdf'
    writer = PdfWriter(clone_from=PdfReader(pdf))
    stream = writer.pages[0]['/Contents'].get_object(); stream.set_data(transform(stream.get_data()))
    path = tmp_path / (name + '.pdf'); writer.write(path)
    state = deepcopy(flows['v3_rev1']); state.pop('model_sha256')
    record = state['slots']['slot-0']['source_output']
    state['pdf_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    content = ContentPage(path, 1)
    try:
        try:
            span = owned.inventory(content.streams[-content.page.xref])[record['marker_id']]
            record['current'] = owned.witness(content, record, span)
        except PdfError:
            pass
    finally:
        content.close()
    state['slots']['slot-0']['semantic']['binding'].update(pdf_sha256=state['pdf_sha256'],
        owner_program_sha256=record['current']['program_sha256'], owner_block_sha256=record['current']['block_sha256'])
    return path, _seal(state)


@pytest.mark.parametrize('name,transform,owner_reason', [
    ('duplicate_marker', lambda d: d + d[d.index(b'\n%'):], 'duplicate'),
    ('marker_spoof', lambda d: b'q Q' + marker_lines(d)[0] + b'q Q\n' + marker_lines(d)[1] + d, 'duplicate'),
    ('foreign_glyph', lambda d: d.replace(b'<0001> Tj', b'<0001> Tj 1 0 0 1 60 60 Tm <0001> Tj', 1), 'foreign'),
])
def test_existing_owner_checks_cannot_be_bypassed(tmp_path, flows, name, transform, owner_reason):
    path, state = physical_variant(tmp_path, flows, name, transform)
    refused(path, state)
    with pytest.raises(PdfError, match=owner_reason):
        owned.validate(path, state)


def test_stale_context_refuses(tmp_path, flows):
    path, state = physical_variant(tmp_path, flows, 'context', lambda d: b'0.5 g\n' + d)
    entry = flows['v3_rev1']['slots']['slot-0']['source_output']['current']['entry_context_sha256']
    state = resealed(state, lambda v: v['slots']['slot-0']['source_output']['current'].update(entry_context_sha256=entry))
    refused(path, state)
    with pytest.raises(PdfError, match='witness mismatch'):
        owned.validate(path, state)


def plan(flows, request, **kwargs):
    main = flows['main']
    return semantic.plan_semantic_transition(main / 'rev1.pdf', main / 'rev1-v3.json', request, **kwargs)


@pytest.mark.parametrize('request_,text', [
    (dict(operation='edit', start=1, end=1, text='A'), 'AA B'),
    (dict(operation='edit', start=0, end=1, text=''), ' B'),
    (dict(operation='edit', start=0, end=1, text='B'), 'B B'),
    (dict(operation='edit', start=3, end=3, text=' AB AB'), 'A B AB AB'),
])
def test_d_text_transitions_are_authorized_read_only(flows, request_, text):
    before = (flows['main'] / 'rev1.pdf').read_bytes()
    result = plan(flows, request_)
    assert result['classification'] == 'D' and result['next']['text'] == text
    assert result['diff'] == {'text': {'before': 'A B', 'after': text}}
    assert result['executable_now'] is False and result['requires'] == semantic.WRITER
    assert len(result['next_derived']['intervals']) == len(text)
    assert (flows['main'] / 'rev1.pdf').read_bytes() == before


def test_newline_and_trailing_space_transitions_derive_omissions(flows):
    result = plan(flows, dict(operation='edit', start=3, end=3, text='  \n\nB'))
    kinds = [(o['offset'], o['kind']) for o in result['next_derived']['omitted']]
    assert kinds == [(3, 'trimmed-space'), (4, 'trimmed-space'), (5, 'newline'), (6, 'newline')]
    assert result['next_plan']['emitted'] == 4 and len(result['next_plan']['lines']) == 3


def test_reflow_keeps_region_authority(flows):
    assert plan(flows, dict(operation='reflow', width='150'))['classification'] == 'D'
    with pytest.raises(PdfError, match='region'):
        plan(flows, dict(operation='reflow', width='16'))


def test_explicit_reinterpretations_and_edge_policy(flows):
    edge = dict(left=0, right=1, delta='6/5', operator='TJ', semantics='confirmed-adjacent-pair',
                boundary_policy='suppress-at-line-end')
    tw = plan(flows, dict(operation='reinterpret', changes=dict(word_spacing='6/5')))
    assert tw['classification'] == 'E' and tw['diff'] == {'style.word_spacing': {'before': '0', 'after': '6/5'}}
    to_edge = plan(flows, dict(operation='reinterpret', changes=dict(word_spacing='0', edges=[edge])))
    assert set(to_edge['diff']) == {'edges'}
    style = plan(flows, dict(operation='reinterpret', changes=dict(font_size='37/3', horizontal_scale='4/5', rise='-1',
                                                                   tracking='1/4')))
    assert set(style['diff']) == {'style.font_size', 'style.horizontal_scale', 'style.rise', 'style.tracking'}
    label = plan(flows, dict(operation='reinterpret', changes=dict(body_style_id='body-next')))
    assert set(label['diff']) == {'body_style_id'}
    assert label['next_derived'] == plan(flows, dict(operation='reopen'))['next_derived']
    with pytest.raises(PdfError):
        plan(flows, dict(operation='reinterpret', changes=dict(edges=[dict(edge, right=2)])))
    with pytest.raises(PdfError, match='asset'):
        plan(flows, dict(operation='reinterpret', changes=dict(font='0' * 64)))
    with pytest.raises(PdfError, match='asset'):
        plan(flows, dict(operation='save'), asset=flows['asset'])


def test_font_reinterpretation_needs_the_supplied_asset(flows, tmp_path):
    alternate = tmp_path / 'alternate.ttf'
    alternate.write_bytes(static_font('SemanticAlternate'))
    sha = hashlib.sha256(alternate.read_bytes()).hexdigest()
    result = plan(flows, dict(operation='reinterpret', changes=dict(font=sha)), asset=alternate)
    assert result['classification'] == 'E' and result['diff'] == {'font.sha': {
        'before': flows['v3_rev1']['slots']['slot-0']['semantic']['payload']['font']['sha'], 'after': sha}}


@pytest.mark.parametrize('field,change', [
    ('style.word_spacing', lambda p: p['style'].update(word_spacing='1')),
    ('font', lambda p: p['font'].update(sha='1' * 64)),
    ('body_style_id', lambda p: p.update(body_style_id='other')),
    ('edges', lambda p: p.update(edges=[{'left': 0}])),
])
def test_unauthorized_semantic_diff_refuses(flows, field, change):
    result = plan(flows, dict(operation='edit', start=1, end=1, text='A'))
    candidate = deepcopy(result['next']); change(candidate)
    with pytest.raises(PdfError, match='unauthorized semantic diff'):
        semantic.require_authorized_payload(result, candidate)
    assert semantic.require_authorized_payload(result, deepcopy(result['next'])) == result['diff']


@pytest.mark.parametrize('operation', ['split', 'join', 'mixed_style', 'vertical_edge', 'cross_paragraph_edge',
                                       'region_reassignment', 'hash_refresh', 'replace_record', 'unknown'])
def test_refused_operations(flows, operation):
    with pytest.raises(PdfError):
        plan(flows, dict(operation=operation))


def test_noop_reopen_needs_no_authority_and_no_api_refreshes_hashes(flows):
    result = plan(flows, dict(operation='reopen'))
    assert result['classification'] == 'N' and result['diff'] == {} and result['executable_now'] is True
    assert not any('refresh' in name for name in dir(semantic))


def test_field_authority_separates_owner_creation_from_semantics():
    authority = semantic.FIELD_AUTHORITY
    assert 'slots[s].source_output.created_from' in authority['immutable_creation_identity']
    assert authority['semantic_authority'] == ('slots[s].semantic.payload',)
    assert 'slots[s].source_output.current' in authority['mutable_current_owner_witness']
