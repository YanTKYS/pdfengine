"""B-L2-S resolution: current semantic style/font authority bound to actual candidates.

Three layers stay separate:
A. creation evidence (source registry, observations, provider, owner creation) is immutable;
B. current semantic authority (`semantic.payload` + `semantic.current`) changes only by explicit E;
C. current physical binding (canonical island, generated font subset, owner witness) is verified.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import shutil
import subprocess
import sys
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
import pymupdf
from pypdf import PdfReader, PdfWriter
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.editable import _seal
from pdfeditor import semantic_island as island
from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_writer as writer
from pdfeditor import source_ownership as owned
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.editable import open_editable, write_editable
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow
from pdfeditor.story_flow import confirm_story
from test_attributed import source_pdf
from test_semantic_layout import make_flow, resealed, statement, static_font
from test_semantic_writer import EDGE, body_of, ops, pixels

COMBINED = dict(font_size='37/3', horizontal_scale='4/5', rise='-1', tracking='1/4')


def glyph_font(family, cmap, *, width, box):
    """Static simple unhinted TT whose outlines/advances differ from the source asset."""
    builder = FontBuilder(1000, isTTF=True)
    names = ['.notdef', *cmap.values()]
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap(cmap)
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name != 'space':
            a, b, c, d = box
            pen.moveTo((a, b)); pen.lineTo((c, b)); pen.lineTo((c, d)); pen.lineTo((a, d)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({n: (width, 0) for n in names})
    builder.setupHorizontalHeader(ascent=600, descent=-200)
    builder.setupNameTable(dict(familyName=family, styleName='Regular', uniqueFontIdentifier=family + '-v1',
                                fullName=family, psName=family))
    builder.setupOS2(sTypoAscender=600, sTypoDescender=-200, usWinAscent=600, usWinDescent=200, fsType=0)
    builder.setupPost(); builder.setupMaxp()
    builder.font['head'].created = builder.font['head'].modified = 2082844800
    out = BytesIO(); builder.save(out)
    return out.getvalue()


def alternate_font(family='SemanticAlternate'):
    return glyph_font(family, {65: 'A', 66: 'B', 32: 'space'}, width=500, box=(50, -100, 400, 700))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reinterpret(**changes):
    return dict(operation='reinterpret', changes=changes)


def save():
    return dict(operation='save')


@pytest.fixture(scope='module')
def life(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic-authority')
    source, asset = make_flow(root / 'main')
    main = root / 'main'
    alternate = root / 'alternate.ttf'
    alternate.write_bytes(alternate_font())
    v3 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0', semantic=statement(asset))
    (main / 'v3.json').write_text(json.dumps(v3))
    steps = {'confirmed': (main / 'rev1.pdf', main / 'v3.json')}
    font_b, font_a = (dict(font=sha(alternate)), alternate), (dict(font=sha(asset)), asset)
    order = [
        ('base', 'confirmed', save(), None),
        ('size', 'base', reinterpret(font_size='13'), None),
        ('size_n1', 'size', save(), None), ('size_n2', 'size_n1', save(), None),
        ('size_edit', 'size', dict(operation='edit', start=1, end=1, text='A'), None),
        ('scale', 'base', reinterpret(horizontal_scale='4/5'), None),
        ('rise', 'base', reinterpret(rise='-1'), None),
        ('tracking', 'base', reinterpret(tracking='1/4'), None),
        ('combined', 'base', reinterpret(**COMBINED), None),
        ('combined_n1', 'combined', save(), None), ('combined_n2', 'combined_n1', save(), None),
        ('font', 'base', reinterpret(**font_b[0]), font_b[1]),
        ('font_n1', 'font', save(), None), ('font_n2', 'font_n1', save(), None),
        ('font_back', 'font', reinterpret(**font_a[0]), font_a[1]),
        ('tw', 'base', reinterpret(word_spacing='6/5'), None),
        ('tw_style', 'tw', reinterpret(font_size='13', tracking='1/4'), None),
        ('edge', 'tw', reinterpret(word_spacing='0', edges=[EDGE]), None),
        ('edge_style', 'edge', reinterpret(horizontal_scale='4/5', rise='-1'), None),
        ('edge_style_n1', 'edge_style', save(), None),
        ('empty', 'base', dict(operation='edit', start=0, end=3, text=''), None),
        ('empty_font', 'empty', reinterpret(**font_b[0]), font_b[1]),
        ('empty_size', 'empty_font', reinterpret(font_size='13'), None),
        ('empty_n1', 'empty_size', save(), None), ('empty_n2', 'empty_n1', save(), None),
        ('empty_regrow', 'empty_n2', dict(operation='edit', start=0, end=0, text='A B'), None),
    ]
    results = {}
    for name, parent, request, supplied in order:
        result = writer.build_semantic_candidate(*steps[parent], request, workspace=root / 'work', asset=supplied)
        steps[name] = (result['pdf'], result['sidecar'])
        results[name] = result
    return dict(root=root, main=main, asset=asset, alternate=alternate, steps=steps, results=results,
                v2=json.loads((main / 'rev1.json').read_text()))


def opened(life, name):
    result = semantic.open_semantic_flow(*life['steps'][name])
    assert result['status'] == 'restored', result.get('reason')
    return result


def state(life, name):
    return json.loads(Path(life['steps'][name][1]).read_text())


def refused(pdf, value, reason=None):
    result = semantic.open_semantic_flow(pdf, value)
    assert result['status'] == 'needs_confirmation'
    if reason:
        assert reason in result['reason'], result['reason']
    return result['reason']


def physical_style(life, name):
    return state(life, name)['slots']['slot-0']['binding']['paragraph']['styles']


def trace(pdf):
    with pymupdf.open(pdf) as document:
        return [(chr(c[0]), round(c[2][0], 3), round(c[2][1], 3), round(span['size'], 3))
                for span in document[0].get_texttrace() for c in span['chars']]


def creation_evidence(value):
    """Layer A: everything the source creation contract binds."""
    p = value['paragraphs']['A']
    return dict(registry=p['style_registry'], source_model=p['source_model_sha256'], contract=value['contract_sha256'],
                snapshot=value['slots']['slot-0']['source_snapshot_sha256'],
                owner={k: value['slots']['slot-0']['source_output'][k] for k in ('version', 'marker_id', 'created_from')})


# --- initial confirmation -------------------------------------------------------------------------------------------

def test_initial_confirmation_is_source_confirmed(life):
    value = json.loads((life['main'] / 'v3.json').read_text())
    record = value['slots']['slot-0']['semantic']
    assert record['version'] == semantic.SEMANTIC_VERSION and set(record) == semantic.SEMANTIC_KEYS
    provider = life['v2']['paragraphs']['A']['style_registry']['body']['reflow_provider']
    assert record['current'] == dict(provenance=semantic.SOURCE_CONFIRMED, provider=provider)
    assert record['binding']['font_resource'] == '/PRF1'
    assert creation_evidence(value) == creation_evidence(life['v2'])


@pytest.mark.parametrize('change', [dict(font_size='13'), dict(horizontal_scale='4/5'), dict(rise='-1'),
                                    dict(tracking='1/4')])
def test_initial_confirmation_refuses_arbitrary_style_reinterpretation(life, change):
    with pytest.raises(PdfError, match='body style'):
        semantic.confirm_semantic_layout(life['main'] / 'rev1.pdf', life['main'] / 'rev1.json', slot_id='slot-0',
                                         semantic=statement(life['asset'], **change))


def test_initial_confirmation_refuses_a_font_other_than_the_source_provider(life):
    payload = statement(life['alternate'])
    with pytest.raises(PdfError):
        semantic.confirm_semantic_layout(life['main'] / 'rev1.pdf', life['main'] / 'rev1.json', slot_id='slot-0',
                                         semantic=payload)


# --- style lifecycles -------------------------------------------------------------------------------------------------

def test_font_size_lifecycle(life):
    for name in ('size', 'size_n1', 'size_n2'):
        result = opened(life, name)
        assert result['semantic']['style']['font_size'] == '13' and result['island']['canonical']
        assert result['authority']['provenance'] == semantic.CALLER_CONFIRMED
        assert [s['font_size'] for s in physical_style(life, name)] == [13.0]
        assert {t[3] for t in trace(life['steps'][name][0])} == {13.0}
    bodies = [body_of(*life['steps'][n]) for n in ('size', 'size_n1', 'size_n2')]
    assert b'/PRF1 13 Tf' in bodies[0] and bodies[0] == bodies[1] == bodies[2]
    assert len(ops(bodies[0])) == len(ops(bodies[2])) == len(ops(body_of(*life['steps']['base'])))
    assert life['results']['size']['plan']['diff'] == {'style.font_size': {'before': '12', 'after': '13'}}
    assert creation_evidence(state(life, 'size_n2')) == creation_evidence(life['v2'])


def test_horizontal_scale_lifecycle(life):
    result = opened(life, 'scale')
    assert result['semantic']['style']['horizontal_scale'] == '4/5'
    assert b' 80 Tz ' in body_of(*life['steps']['scale'])
    assert [s['horizontal_scale'] for s in physical_style(life, 'scale')] == [0.8]
    xs = [t[1] for t in trace(life['steps']['scale'][0]) if t[0] in 'AB']
    assert xs == [20.0, 31.52]  # 2 x 600/1000 x 12 x 4/5 = 11.52


def test_rise_lifecycle_stays_inside_the_region(life):
    result = opened(life, 'rise')
    assert result['semantic']['style']['rise'] == '-1' and b' 1 Ts ' in body_of(*life['steps']['rise'])
    assert [s['baseline_shift'] for s in physical_style(life, 'rise')] == [-1.0]
    base = [t[2] for t in trace(life['steps']['base'][0])]
    assert [t[2] for t in trace(life['steps']['rise'][0])] == [round(y - 1, 3) for y in base]
    assert result['derived']['empty'] == dict(ascent='41/5', descent='7/5')


def test_tracking_lifecycle(life):
    result = opened(life, 'tracking')
    assert result['semantic']['style']['tracking'] == '1/4' and b' 0.25 Tc ' in body_of(*life['steps']['tracking'])
    assert [s['tracking'] for s in physical_style(life, 'tracking')] == [0.25]
    xs = [t[1] for t in trace(life['steps']['tracking'][0])]
    assert xs == [20.0, 27.45, 34.9]


def test_combined_style_lifecycle(life):
    for name in ('combined', 'combined_n1', 'combined_n2'):
        result = opened(life, name)
        assert {k: result['semantic']['style'][k] for k in COMBINED} == COMBINED
    assert set(life['results']['combined']['plan']['diff']) == {'style.' + k for k in COMBINED}
    body = body_of(*life['steps']['combined'])
    assert b'/PRF1 12.333333 Tf 80 Tz 0.3125 Tc 0 Tw 1 Ts 0 g' in body
    assert body == body_of(*life['steps']['combined_n1']) == body_of(*life['steps']['combined_n2'])
    style = physical_style(life, 'combined')[0]
    assert (style['font_size'], style['horizontal_scale'], style['tracking'], style['baseline_shift']) == (12.333333, .8, .25, -1)


def test_text_edit_after_style_change_keeps_current_authority(life):
    result = opened(life, 'size_edit')
    assert result['semantic']['text'] == 'AA B' and result['semantic']['style']['font_size'] == '13'
    assert result['authority'] == opened(life, 'size')['authority']
    assert life['results']['size_edit']['plan']['diff'] == {'text': {'before': 'A B', 'after': 'AA B'}}


# --- font lifecycle ---------------------------------------------------------------------------------------------------

def test_font_lifecycle_binds_current_provider_and_keeps_source_evidence(life):
    b = sha(life['alternate'])
    for name in ('font', 'font_n1', 'font_n2'):
        result = opened(life, name)
        value = state(life, name)
        assert result['semantic']['font']['sha'] == b
        assert result['authority'] == dict(provenance=semantic.CALLER_CONFIRMED,
                                           provider=dict(path=str(life['alternate'].resolve()), sha256=b))
        record = value['generated_fonts']['1']['/PRF1']
        assert record['provider']['source_sha256'] == b and record['basefont'].endswith('+SemanticAlternate')
        assert record['slot_id'] == 'slot-0' and record['paragraph_id'] == 'A'
        binding = value['slots']['slot-0']['semantic']['binding']
        assert (binding['font_resource'], binding['font_subset_sha256'], binding['pdf_sha256']) == (
            '/PRF1', record['subset_sha256'], sha(life['steps'][name][0]))
        assert value['slots']['slot-0']['binding']['fonts'] == {'s0': result['authority']['provider']}
        # Creation evidence still names font A; it is never rewritten.
        assert creation_evidence(value) == creation_evidence(life['v2'])
        assert value['paragraphs']['A']['style_registry']['body']['reflow_provider']['sha256'] == sha(life['asset'])
    assert body_of(*life['steps']['font']) == body_of(*life['steps']['font_n1']) == body_of(*life['steps']['font_n2'])
    assert life['results']['font']['plan']['diff'] == {'font.sha': {'before': sha(life['asset']), 'after': b}}
    # Placement uses the current asset's metrics (500/1000 advances), not the source provider's (600/1000).
    assert [t[1] for t in trace(life['steps']['font'][0])] == [20.0, 26.0, 32.0]


def test_font_rollback_is_a_new_explicit_transition(life):
    result = opened(life, 'font_back')
    a = sha(life['asset'])
    assert result['semantic']['font']['sha'] == a
    # A → B → A is not history: authority stays caller-confirmed, provider is the explicitly supplied asset.
    assert result['authority'] == dict(provenance=semantic.CALLER_CONFIRMED,
                                       provider=dict(path=str(life['asset'].resolve()), sha256=a))
    assert body_of(*life['steps']['font_back']) == body_of(*life['steps']['base'])
    base, back = state(life, 'base'), state(life, 'font_back')
    assert back['generated_fonts']['1']['/PRF1']['subset_sha256'] == base['generated_fonts']['1']['/PRF1']['subset_sha256']
    assert pixels(life['steps']['font_back'][0]) == pixels(life['steps']['base'][0])


def test_font_change_retargets_only_the_slot_alias(life):
    def fonts(pdf):
        resources = PdfReader(pdf).pages[0]['/Resources']['/Font']
        return {k: v.get_object().get('/BaseFont') for k, v in resources.items()}
    before, after = fonts(life['steps']['base'][0]), fonts(life['steps']['font'][0])
    assert set(before) == set(after)
    assert {k: v for k, v in before.items() if k != '/PRF1'} == {k: v for k, v in after.items() if k != '/PRF1'}
    assert before['/PRF1'] != after['/PRF1'] and after['/PRF1'].endswith('+SemanticAlternate')
    assert list(state(life, 'font')['generated_fonts']['1']) == ['/PRF1']


# --- Tw / edges / empty -----------------------------------------------------------------------------------------------

def test_style_change_keeps_tw_and_edge_semantics(life):
    tw = opened(life, 'tw_style')
    assert tw['semantic']['style']['word_spacing'] == '6/5' and tw['semantic']['edges'] == []
    assert (tw['semantic']['style']['font_size'], tw['semantic']['style']['tracking']) == ('13', '1/4')
    # A(7.8+.25) space(7.8+1.2+.25): B at 20 + 8.05 + 9.25.
    assert b'1 0 0 1 37.3 60 Tm' in body_of(*life['steps']['tw_style'])
    edge = opened(life, 'edge_style')
    assert edge['semantic']['edges'] == [EDGE] and edge['semantic']['style']['word_spacing'] == '0'
    assert body_of(*life['steps']['edge_style']) == body_of(*life['steps']['edge_style_n1'])
    assert opened(life, 'edge_style_n1')['semantic'] == edge['semantic']


def test_empty_and_nonpainting_states_carry_current_style_and_font(life):
    b = sha(life['alternate'])
    for name in ('empty_font', 'empty_size', 'empty_n1', 'empty_n2'):
        result = opened(life, name)
        assert result['semantic']['text'] == '' and result['semantic']['font']['sha'] == b
        assert b'[] TJ' in body_of(*life['steps'][name])
    assert b'/PRF1 13 Tf' in body_of(*life['steps']['empty_size'])
    assert opened(life, 'empty_size')['derived']['empty'] == dict(ascent='39/5', descent='13/5')
    assert body_of(*life['steps']['empty_size']) == body_of(*life['steps']['empty_n1']) == body_of(*life['steps']['empty_n2'])
    regrow = opened(life, 'empty_regrow')
    assert regrow['semantic']['text'] == 'A B' and regrow['semantic']['font']['sha'] == b
    assert b'/PRF1 13 Tf' in body_of(*life['steps']['empty_regrow'])


@pytest.mark.parametrize('text', ['   ', '\n', ' \n '])
def test_whitespace_only_text_keeps_the_existing_l2_refusal_under_current_style(life, text):
    """All-space/newline-only paint nothing; the persistent binding (PR #42) refuses them with or
    without a style/font transition, so current authority neither causes nor hides the refusal."""
    existing = candidates(life['root'])
    request = dict(operation='edit', start=0, end=3, text=text)
    reasons = []
    for step in ('base', 'combined', 'font'):
        plan = semantic.plan_semantic_transition(*life['steps'][step], request)
        assert plan['next_plan']['emitted'] == 0 and plan['next_authority'] == opened(life, step)['authority']
        with pytest.raises(PdfError) as error:
            writer.build_semantic_candidate(*life['steps'][step], request, workspace=life['root'] / 'work')
        reasons.append(str(error.value))
    assert len(set(reasons)) == 1 and 'without painted glyphs' in reasons[0]
    assert candidates(life['root']) == existing


# --- fresh process / raster -------------------------------------------------------------------------------------------

@pytest.mark.parametrize('name', ['size', 'combined', 'font', 'font_back', 'empty_size', 'edge_style'])
def test_style_and_font_candidates_reopen_in_a_fresh_process(life, name):
    pdf, sidecar = life['steps'][name]
    code = ('import json,sys;from pdfeditor.semantic_layout import open_semantic_flow;'
            'r=open_semantic_flow(sys.argv[1],sys.argv[2]);'
            'print(json.dumps([r["status"],r.get("semantic"),r.get("authority"),r.get("island")]))')
    out = subprocess.run([sys.executable, '-c', code, str(pdf), str(sidecar)], capture_output=True, text=True, check=True,
                         cwd=Path(__file__).resolve().parents[1])
    status, payload, authority, evidence = json.loads(out.stdout)
    assert status == 'restored' and evidence['canonical'] is True
    reopened = semantic.open_semantic_flow(pdf, sidecar)
    assert payload == reopened['semantic'] and authority == reopened['authority']


def test_mupdf_raster_changes_with_style_and_is_stable_through_noops(life):
    base = pixels(life['steps']['base'][0])
    for change, noops in (('size', ('size_n1', 'size_n2')), ('combined', ('combined_n1', 'combined_n2')),
                          ('font', ('font_n1', 'font_n2')), ('empty_size', ('empty_n1', 'empty_n2'))):
        rendered = pixels(life['steps'][change][0])
        assert all(pixels(life['steps'][n][0]) == rendered for n in noops)
    for change in ('size', 'scale', 'rise', 'tracking', 'combined', 'font'):
        assert pixels(life['steps'][change][0]) != base


def test_poppler_raster_is_stable_through_noops(life, tmp_path):
    renderer = shutil.which('pdftoppm')
    if renderer is None:
        pytest.skip('Poppler unavailable; Windows external renderer validation remains required')
    from PIL import Image

    def rendered(name):
        dest = tmp_path / name
        subprocess.run([renderer, '-singlefile', '-r', '144', '-png', str(life['steps'][name][0]), str(dest)],
                       check=True, capture_output=True)
        with Image.open(dest.with_suffix('.png')) as image:
            return image.size, image.convert('RGB').tobytes()
    for change, noops in (('size', ('size_n1', 'size_n2')), ('font', ('font_n1', 'font_n2')),
                          ('combined', ('combined_n1', 'combined_n2')), ('font_back', ())):
        expected = rendered(change)
        assert all(rendered(n) == expected for n in noops)
        assert (expected == rendered('base')) is (change == 'font_back')


# --- negative evidence: sidecar tampering -----------------------------------------------------------------------------

def _registry(v):
    return v['paragraphs']['A']['style_registry']['body']


@pytest.mark.parametrize('step,change', [
    ('size', lambda v: _registry(v)['attributes']['font_size'].update(value=13.0)),
    ('size', lambda v: [w['properties'].update(font_size=13.0) for w in _registry(v)['source_observations']]),
    ('size', lambda v: [_registry(v)['attributes']['font_size'].update(value=13.0),
                        [w['properties'].update(font_size=13.0) for w in _registry(v)['source_observations']]]),
    ('font', lambda v: _registry(v)['reflow_provider'].update(sha256=v['slots']['slot-0']['semantic']['payload']['font']['sha'])),
    ('font', lambda v: _registry(v).update(provider_relation='confirmed_reflow_provider')),
    ('font', lambda v: v['slots']['slot-0']['source_output']['created_from'].update(pdf_sha256='0' * 64)),
    ('combined', lambda v: v['paragraphs']['A'].update(source_model_sha256='0' * 64)),
])
def test_source_evidence_tampering_refuses_after_current_transitions(life, step, change):
    refused(life['steps'][step][0], resealed(state(life, step), change))


@pytest.mark.parametrize('step,change,reason', [
    ('size', lambda v: v['slots']['slot-0']['semantic']['payload']['style'].update(font_size='14'), None),
    ('size', lambda v: v['slots']['slot-0']['semantic']['payload']['style'].update(tracking='1/4'), None),
    ('size', lambda v: v['slots']['slot-0']['semantic']['current'].update(provenance=semantic.SOURCE_CONFIRMED),
     'inline attributes'),
    ('size', lambda v: v['slots']['slot-0']['semantic']['current'].update(provenance='observed_source'), 'authority'),
    ('size', lambda v: v['slots']['slot-0']['semantic'].pop('current'), 'semantic payload'),
    ('size', lambda v: v['slots']['slot-0']['semantic'].update(version=1), 'semantic payload'),
    ('font', lambda v: v['slots']['slot-0']['semantic']['current']['provider'].update(
        path=v['paragraphs']['A']['style_registry']['body']['reflow_provider']['path']), 'provider'),
    ('font', lambda v: [v['slots']['slot-0']['semantic']['payload']['font'].update(
        sha=_registry(v)['reflow_provider']['sha256']),
        v['slots']['slot-0']['semantic']['current'].update(provider=deepcopy(_registry(v)['reflow_provider']))], None),
    ('base', lambda v: [v['slots']['slot-0']['semantic']['payload']['style'].update(font_size='13'),
                        v['slots']['slot-0']['semantic']['current'].update(provenance=semantic.CALLER_CONFIRMED)], None),
])
def test_current_semantic_tampering_refuses(life, step, change, reason):
    refused(life['steps'][step][0], resealed(state(life, step), change), reason)


def test_unrequested_registry_current_style_change_refuses(life):
    """No request: a sidecar that claims 13pt over the unchanged 12pt island is refused."""
    value = state(life, 'base')
    def claim(v):
        v['slots']['slot-0']['semantic']['payload']['style'].update(font_size='13')
        v['slots']['slot-0']['semantic']['current'].update(provenance=semantic.CALLER_CONFIRMED)
        record = v['slots']['slot-0']['semantic']
        font = semantic._asset(v, v['slots']['slot-0'], record['payload'], record['current'])
        try:
            record['derived'] = semantic._derive(v, v['slots']['slot-0'], record['payload'], font)[0]
        finally:
            font.font.close()
    refused(life['steps']['base'][0], resealed(value, claim), 'inline attributes')


@pytest.mark.parametrize('donor,field', [('base', 'semantic'), ('font_back', 'semantic'),
                                        ('base', 'generated_fonts'), ('base', 'font_subset')])
def test_stale_semantic_or_stale_font_record_does_not_co_bind(life, donor, field):
    old = state(life, donor)
    def change(v):
        if field == 'semantic':
            v['slots']['slot-0']['semantic'] = deepcopy(old['slots']['slot-0']['semantic'])
        elif field == 'generated_fonts':
            v['generated_fonts'] = deepcopy(old['generated_fonts'])
        else:
            v['slots']['slot-0']['semantic']['binding']['font_subset_sha256'] = \
                old['slots']['slot-0']['semantic']['binding']['font_subset_sha256']
    refused(life['steps']['font'][0], resealed(state(life, 'font'), change))


def test_stale_semantic_with_forged_binding_does_not_co_bind(life):
    """new font + semantic A whose binding is forged to the current revision."""
    current = state(life, 'font')
    stale = deepcopy(state(life, 'base')['slots']['slot-0']['semantic'])
    stale['binding'] = deepcopy(current['slots']['slot-0']['semantic']['binding'])
    refused(life['steps']['font'][0], resealed(current, lambda v: v['slots']['slot-0'].update(semantic=stale)))


def test_current_provider_record_tampering_refuses(life):
    value = state(life, 'font')
    a = sha(life['asset'])
    refused(life['steps']['font'][0], resealed(value, lambda v: v['generated_fonts']['1']['/PRF1']['provider'].update(
        source_sha256=a, instance_sha256=a)))
    refused(life['steps']['font'][0], resealed(value, lambda v: v['generated_fonts']['1'].pop('/PRF1')))
    refused(life['steps']['font'][0], resealed(value, lambda v: v['generated_fonts']['1']['/PRF1'].update(slot_id='slot-9')))


def test_wrong_current_provider_bytes_refuse(life, tmp_path):
    copy = tmp_path / 'alternate.ttf'
    copy.write_bytes(life['alternate'].read_bytes())
    result = writer.build_semantic_candidate(*life['steps']['base'], reinterpret(font=sha(copy)),
                                             workspace=tmp_path / 'work', asset=copy)
    assert semantic.open_semantic_flow(result['pdf'], result['sidecar'])['status'] == 'restored'
    copy.write_bytes(alternate_font('SemanticOther'))
    refused(result['pdf'], result['sidecar'], 'SHA')


# --- negative evidence: physical tampering ----------------------------------------------------------------------------

def physical(tmp_path, life, name, transform):
    """Rewrite the candidate content stream and re-witness owner/semantic binding to the new bytes."""
    pdf = life['steps'][name][0]
    out = PdfWriter(clone_from=PdfReader(pdf))
    stream = out.pages[0]['/Contents'].get_object()
    stream.set_data(transform(stream.get_data()))
    path = tmp_path / (name + '-physical.pdf')
    out.write(path)
    value = state(life, name)
    value.pop('model_sha256')
    record = value['slots']['slot-0']['source_output']
    value['pdf_sha256'] = sha(path)
    content = ContentPage(path, 1)
    try:
        span = owned.inventory(content.streams[-content.page.xref])[record['marker_id']]
        record['current'] = owned.witness(content, record, span)
    finally:
        content.close()
    value['slots']['slot-0']['semantic']['binding'].update(pdf_sha256=value['pdf_sha256'],
        owner_program_sha256=record['current']['program_sha256'], owner_block_sha256=record['current']['block_sha256'])
    return path, _seal(value)


@pytest.mark.parametrize('name,transform', [
    ('size', lambda d: d.replace(b'/PRF1 13 Tf', b'/PRF1 14 Tf', 1)),
    ('tracking', lambda d: d.replace(b' 0.25 Tc ', b' 0.5 Tc ', 1)),
    ('rise', lambda d: d.replace(b' 1 Ts ', b' 2 Ts ', 1)),
    ('scale', lambda d: d.replace(b' 80 Tz ', b' 90 Tz ', 1)),
    ('size', lambda d: d.replace(b'/PRF1 13 Tf', b'/PRF1 12 Tf', 1)),
    ('font', lambda d: d.replace(b'<0003> Tj', b'<0002> Tj', 1)),
])
def test_current_physical_tampering_refuses(tmp_path, life, name, transform):
    path, value = physical(tmp_path, life, name, transform)
    refused(path, value)


# --- negative evidence: writer drift ----------------------------------------------------------------------------------

def candidates(root):
    work = root / 'work'
    return sorted(p.name for p in work.iterdir()) if work.exists() else []


def test_unrequested_style_drift_in_the_writer_refuses(life, monkeypatch):
    pdf, sidecar = life['steps']['base']
    existing = candidates(life['root'])
    real = island.body
    monkeypatch.setattr(island, 'body', lambda style, *a, **k: real(dict(style, tracking='1/4'), *a, **k))
    with pytest.raises(PdfError):
        writer.build_semantic_candidate(pdf, sidecar, reinterpret(font_size='13'), workspace=life['root'] / 'work')
    assert candidates(life['root']) == existing


def test_unrequested_adapter_drift_refuses(life, monkeypatch):
    pdf, sidecar = life['steps']['base']
    existing = candidates(life['root'])
    real = semantic.current_registry
    def drifted(state_, slot, payload, current):
        result = deepcopy(real(state_, slot, payload, current))
        for entry in result.values():
            entry['attributes']['tracking'] = dict(value=.25, provenance=semantic.CALLER_CONFIRMED)
        return result
    monkeypatch.setattr(semantic, 'current_registry', drifted)
    with pytest.raises(PdfError):
        writer.build_semantic_candidate(pdf, sidecar, reinterpret(font_size='13'), workspace=life['root'] / 'work')
    assert candidates(life['root']) == existing


def test_unrequested_font_drift_refuses(life, monkeypatch):
    pdf, sidecar = life['steps']['base']
    existing = candidates(life['root'])
    from pdfeditor.shaped_font import ShapedFont
    real = semantic.font_resource
    monkeypatch.setattr(semantic, 'font_resource', lambda font, emitted: real(ShapedFont(life['alternate']), emitted))
    with pytest.raises(PdfError):
        writer.build_semantic_candidate(pdf, sidecar, reinterpret(tracking='1/4'), workspace=life['root'] / 'work')
    assert candidates(life['root']) == existing


def test_unauthorized_authority_is_refused(life):
    plan = semantic.plan_semantic_transition(*life['steps']['base'], reinterpret(tracking='1/4'))
    with pytest.raises(PdfError, match='authority'):
        semantic.require_authorized_authority(plan, dict(plan['next_authority'], provenance=semantic.SOURCE_CONFIRMED))
    with pytest.raises(PdfError, match='authority'):
        semantic.require_authorized_authority(plan, dict(plan['next_authority'], provider=dict(
            path=str(life['alternate']), sha256=sha(life['alternate']))))
    semantic.require_authorized_authority(plan, deepcopy(plan['next_authority']))
    assert plan['diff'] == {'style.tracking': {'before': '0', 'after': '1/4'}}


def test_font_requests_need_the_exact_supplied_asset(life, tmp_path):
    pdf, sidecar = life['steps']['base']
    with pytest.raises(PdfError, match='asset'):
        writer.build_semantic_candidate(pdf, sidecar, reinterpret(tracking='1/4'), workspace=tmp_path,
                                        asset=life['alternate'])
    with pytest.raises(PdfError, match='asset'):
        writer.build_semantic_candidate(pdf, sidecar, reinterpret(font='1' * 64), workspace=tmp_path,
                                        asset=life['alternate'])
    with pytest.raises(PdfError, match='asset'):
        writer.build_semantic_candidate(pdf, sidecar, reinterpret(font=sha(life['alternate'])), workspace=tmp_path)


def _font_without_b():
    return glyph_font('NoB', {65: 'A', 32: 'space'}, width=600, box=(0, 0, 500, 600))


def test_font_that_cannot_map_the_current_text_refuses(life, tmp_path):
    asset = tmp_path / 'nob.ttf'
    asset.write_bytes(_font_without_b())
    with pytest.raises(PdfError, match='cannot map'):
        writer.build_semantic_candidate(*life['steps']['base'], reinterpret(font=sha(asset)), workspace=tmp_path,
                                        asset=asset)


# --- version 1 compatibility ------------------------------------------------------------------------------------------

@pytest.fixture(scope='module')
def legacy(life):
    """A stored version 1 record exactly as PR #41/#42 wrote it (no current authority)."""
    value = state(life, 'base')
    v1 = semantic._attach(value, 'slot-0', value['slots']['slot-0']['semantic']['payload'])
    path = life['root'] / 'legacy.json'
    path.write_text(json.dumps(v1))
    return life['steps']['base'][0], path


def test_version1_record_opens_without_being_upgraded(legacy):
    pdf, path = legacy
    before = path.read_bytes()
    result = semantic.open_semantic_flow(pdf, path)
    assert result['status'] == 'restored' and result['version'] == 1 and result['authority'] is None
    assert set(json.loads(before)['slots']['slot-0']['semantic']) == semantic.LEGACY_SEMANTIC_KEYS
    assert path.read_bytes() == before


def test_version1_text_save_stays_version1(legacy, life):
    result = writer.build_semantic_candidate(*legacy, dict(operation='edit', start=1, end=1, text='A'),
                                             workspace=life['root'] / 'work')
    value = json.loads(Path(result['sidecar']).read_text())
    assert value['slots']['slot-0']['semantic']['version'] == 1 and 'current' not in value['slots']['slot-0']['semantic']


def test_version1_compatibility_confirmation_is_explicit_and_exact(legacy, life):
    pdf, path = legacy
    payload = json.loads(path.read_text())['slots']['slot-0']['semantic']['payload']
    with pytest.raises(PdfError, match='cannot reinterpret'):
        semantic.confirm_semantic_layout(pdf, path, slot_id='slot-0', semantic=dict(payload, body_style_id='other'))
    upgraded = semantic.confirm_semantic_layout(pdf, path, slot_id='slot-0', semantic=payload)
    record = upgraded['slots']['slot-0']['semantic']
    assert record['version'] == semantic.SEMANTIC_VERSION and record['current']['provenance'] == semantic.SOURCE_CONFIRMED
    assert semantic.open_semantic_flow(pdf, upgraded)['status'] == 'restored'
    result = writer.build_semantic_candidate(pdf, upgraded, reinterpret(font_size='13'), workspace=life['root'] / 'work')
    assert semantic.open_semantic_flow(result['pdf'], result['sidecar'])['semantic']['style']['font_size'] == '13'


# --- nonzero source baseline shift: version 1 sign rule kept, version 2 sign rule -------------------------------------

@pytest.fixture(scope='module')
def risen(tmp_path_factory):
    """Owned flow whose source text has Ts 1, i.e. an explicitly confirmed baseline_shift of -1 (y-down)."""
    root = tmp_path_factory.mktemp('semantic-rise')
    raw = source_pdf(root, b'BT /Regular 12 Tf 1 Ts 20 200 Td (XY) Tj ET')
    asset = root / 'asset.ttf'
    asset.write_bytes(static_font())
    observed = inspect_paragraph(raw, make_selection(raw, glyph_ids=[0, 1], explicit_width=150))
    assert [s['baseline_shift'] for s in observed['styles']] == [-1.0]
    write_editable(raw, root / 'confirmed.pdf', root / 'confirmed-editable.json', observed, [],
                   fonts={'s0': dict(path=str(asset))}, paragraph_style={'s0': dict(baseline_shift=-1.0)},
                   min_line_height=22, max_bottom=220)
    source = root / 'confirmed.pdf'
    paragraph = open_editable(source, root / 'confirmed-editable.json')['state']['paragraph']
    story = confirm_story(source, {'part': dict(page=1, bounds=[18, 40, 200, 220], paragraph=paragraph, paint_relations=[],
        layout=dict(x=20, baseline=200, width=150, max_bottom=220, min_line_height=22, first_line_indent=0))},
        paragraph_id='A', chain=['part'], protected_regions={},
        styles={'body': dict(provider=dict(path=str(asset)), provider_relation='substituted')},
        style_assignments={'part': {s['id']: 'body' for s in paragraph['styles']}}, typing_style_id='body')
    flow = confirm_shared_flow(source, {'A': story}, flow_id='rise-flow', paragraph_order=['A'],
        regions={'R': dict(page=1, bounds=[18, 40, 200, 250], x=20, width=150, first_baseline=200)},
        region_order=['R'], slot_regions={'A': {'part': 'R'}},
        paragraph_policies={'A': dict(min_line_height=22, first_line_indent=0, keep_together=False,
            break_before='auto', break_after='auto', empty=dict(kind='reserve-line', ascent=10, descent=3))},
        follows=[], protected_regions={})
    edit_shared_flow(source, flow, root / 'rev1.pdf', root / 'rev1.json',
                     {'A': dict(edits=[dict(start=0, end=2, text='A B', style_id='body')])})
    v2 = json.loads((root / 'rev1.json').read_text())
    assert v2['paragraphs']['A']['style_registry']['body']['attributes']['baseline_shift']['value'] == -1.0
    # Version 1 as PR #41/#42 sealed it: rise = -baseline_shift = +1 (legacy sign).
    legacy = semantic._attach(v2, 'slot-0', statement(asset, rise='1'))
    (root / 'legacy.json').write_text(json.dumps(legacy))
    return dict(root=root, asset=asset, pdf=root / 'rev1.pdf', v2=root / 'rev1.json', legacy=root / 'legacy.json')


def test_version1_nonzero_baseline_shift_bundle_still_reopens(risen):
    before = risen['legacy'].read_bytes()
    result = semantic.open_semantic_flow(risen['pdf'], risen['legacy'])
    assert result['status'] == 'restored', result.get('reason')
    assert result['version'] == 1 and result['authority'] is None and result['semantic']['style']['rise'] == '1'
    assert risen['legacy'].read_bytes() == before
    # The version 1 rule itself is unchanged: the version 2 sign is not accepted for a version 1 record.
    flipped = semantic._attach(json.loads(risen['v2'].read_text()), 'slot-0', statement(risen['asset'], rise='-1'))
    refused(risen['pdf'], flipped, 'body style')


def test_version2_source_confirmed_rise_equals_the_observed_baseline_shift(risen):
    with pytest.raises(PdfError, match='body style'):
        semantic.confirm_semantic_layout(risen['pdf'], risen['v2'], slot_id='slot-0',
                                         semantic=statement(risen['asset'], rise='1'))
    v3 = semantic.confirm_semantic_layout(risen['pdf'], risen['v2'], slot_id='slot-0',
                                          semantic=statement(risen['asset'], rise='-1'))
    assert v3['slots']['slot-0']['semantic']['current']['provenance'] == semantic.SOURCE_CONFIRMED
    # Ts 1 → baseline_shift −1 → rise −1 holds physically: a source-confirmed save writes Ts = −rise = 1 and verifies.
    work = risen['root'] / 'work'
    first = writer.build_semantic_candidate(risen['pdf'], v3, save(), workspace=work)
    second = writer.build_semantic_candidate(first['pdf'], first['sidecar'], save(), workspace=work)
    for result in (first, second):
        opened_ = semantic.open_semantic_flow(result['pdf'], result['sidecar'])
        assert opened_['status'] == 'restored' and opened_['island']['canonical']
        assert opened_['authority']['provenance'] == semantic.SOURCE_CONFIRMED
        assert b' 1 Ts ' in result['body']
        styles_ = json.loads(Path(result['sidecar']).read_text())['slots']['slot-0']['binding']['paragraph']['styles']
        assert [s['baseline_shift'] for s in styles_] == [-1.0]
    assert first['body'] == second['body']


def test_version1_nonzero_rise_is_not_upgraded_silently(risen):
    before = risen['legacy'].read_bytes()
    stored = json.loads(before)['slots']['slot-0']['semantic']['payload']
    with pytest.raises(PdfError, match='legacy sign'):
        semantic.confirm_semantic_layout(risen['pdf'], risen['legacy'], slot_id='slot-0', semantic=stored)
    # Supplying the converted value is a reinterpretation of the stored payload, not an upgrade.
    with pytest.raises(PdfError, match='cannot reinterpret'):
        semantic.confirm_semantic_layout(risen['pdf'], risen['legacy'], slot_id='slot-0',
                                         semantic=statement(risen['asset'], rise='-1'))
    assert risen['legacy'].read_bytes() == before
