"""Semantic layout state for one owned shared-flow source slot (L1, read-only).

`pdfengine-shared-flow-3` is a shared-flow v2 state whose single owned source
slot also carries a bounded current semantic payload. Persistent artifacts stay
the PDF plus this one sidecar. Ownership is never re-implemented here: the
unmodified v2 validator (including `source_ownership.validate`) runs on an
in-memory v2 projection. Trust model P4: the trusted API caller states
semantics explicitly; every semantic change comes from an explicit request;
the model checksum is integrity only. Nothing here writes a PDF, creates or
adopts an owner, upgrades a v2 sidecar implicitly, or refreshes a PDF hash.

Style/font authority (semantic record version 2, B-L2-S resolution) has three
separate layers that are never merged:

A. creation evidence: the shared-flow style registry, its source observations
   and source provider (immutable, part of the confirmed contract);
B. current semantic authority: `semantic.payload` plus `semantic.current`
   (provenance and the asset whose SHA the payload names);
C. current physical binding: the canonical owned island, the slot's generated
   font record and subset, the owner witness and `semantic.binding`.

`source-confirmed` current authority must equal A. Only an explicit E
transition makes it `caller-confirmed-current-semantic`; then the current
fragment is checked against an in-memory current-style adapter built from B,
while A is still validated unchanged. Version 1 records (PR #41/#42) keep
their rules and are never upgraded by opening them.
"""
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path

from .alignment import DEFAULT
from .attributed import digest
from .backend import PdfError
from .content_stream import ContentPage
from .editable import _seal
from .proof_session import proof_session
from .selection import source_sha
from .shaped_font import ShapedFont, ShapedRun
from . import semantic_island as island
from . import semantic_measure as measure
from . import shared_flow
from . import source_ownership as owned
from . import story_styles as styles

SCHEMA = 'pdfengine-shared-flow-3'
SEMANTIC_VERSION = 2
SEMANTIC_KEYS = frozenset({'version', 'payload', 'current', 'derived', 'binding'})
LEGACY_SEMANTIC_KEYS = frozenset({'version', 'payload', 'derived', 'binding'})  # version 1 (PR #41/#42)
CURRENT_KEYS = frozenset({'provenance', 'provider'})
SOURCE_CONFIRMED = 'source-confirmed'
CALLER_CONFIRMED = 'caller-confirmed-current-semantic'
PAYLOAD_KEYS = frozenset({'text', 'style', 'font', 'body_style_id', 'edges', 'region_id'})
DERIVED_KEYS = frozenset({'intervals', 'omitted', 'empty'})
LEGACY_BINDING_KEYS = frozenset({'slot_id', 'paragraph_id', 'region_id', 'pdf_sha256', 'marker_id',
                                 'owner_program_sha256', 'owner_block_sha256'})
BINDING_KEYS = LEGACY_BINDING_KEYS | {'font_resource', 'font_subset_sha256'}
REINTERPRETABLE = frozenset({'font', 'font_size', 'horizontal_scale', 'rise', 'tracking', 'word_spacing',
                             'edges', 'body_style_id'})
# Explicit E changes that move current style/font authority away from the source-confirmed state.
STYLE_REINTERPRETATIONS = frozenset({'font', 'font_size', 'horizontal_scale', 'rise', 'tracking'})
REFUSED_OPERATIONS = frozenset({'split', 'join', 'mixed_style', 'vertical_edge', 'cross_paragraph_edge',
                                'nonadjacent_edge', 'region_reassignment', 'hash_refresh', 'replace_record'})
WRITER = 'L2 canonical island writer + authorized publication'

FIELD_AUTHORITY = {
    'immutable_creation_identity': ('flow.id', 'slots[s].paragraph_id', 'slots[s].region_id',
        'slots[s].source_snapshot_sha256', 'slots[s].source_output.marker_id',
        'slots[s].source_output.created_from', 'contract_sha256'),
    'mutable_current_owner_witness': ('slots[s].source_output.state', 'slots[s].source_output.current'),
    'source_creation_evidence': ('paragraphs[p].style_registry', 'paragraphs[p].source_model_sha256',
        'slots[s].source_snapshot_sha256', 'contract_sha256'),
    'semantic_authority': ('slots[s].semantic.payload',),
    'current_semantic_authority': ('slots[s].semantic.current',),
    'current_physical_binding': ('slots[s].binding', 'slots[s].style_binding', 'generated_fonts[page][alias]'),
    'derived_current_binding': ('slots[s].semantic.derived', 'slots[s].semantic.binding', 'pdf_sha256'),
    'integrity': ('model_sha256',),
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _exact(value):
    if not isinstance(value, str) or str(measure.rational(value)) != value:
        raise PdfError('semantic values must use canonical exact rational spelling')
    return measure.rational(value)


def _scope(state):
    """Exactly one paragraph, one owned source slot, one region, one body style, no continuation."""
    if state.get('schema') != SCHEMA:
        raise PdfError('semantic layout needs the shared-flow-3 schema')
    if len(state['flow']['paragraphs']) != 1 or len(state['paragraphs']) != 1:
        raise PdfError('semantic layout admits exactly one paragraph')
    if len(state['slots']) != 1:
        raise PdfError('semantic layout admits exactly one source slot')
    sid, slot = next(iter(state['slots'].items()))
    if slot.get('destination_id') is not None or slot.get('creation_provenance') is not None:
        raise PdfError('semantic layout refuses generated continuation slots')
    if state.get('continuation_destinations') or state.get('destination_bindings'):
        raise PdfError('semantic layout refuses continuation destinations')
    if len(state['regions']) != 1 or state['flow']['regions'] != [slot['region_id']]:
        raise PdfError('semantic layout admits exactly one confirmed region')
    record = slot.get('source_output')
    if not isinstance(record, dict) or record.get('state') != 'owned':
        raise PdfError('semantic layout needs an already owned source slot')
    paragraph = state['paragraphs'][slot['paragraph_id']]
    if len(paragraph['style_registry']) != 1 or paragraph['logical']['typing_style_id'] not in paragraph['style_registry']:
        raise PdfError('semantic layout admits exactly one body style')
    if paragraph['logical'].get('alignment', DEFAULT) != DEFAULT:
        raise PdfError('semantic layout admits left alignment only')
    return sid, slot


def _payload(payload, slot):
    """Shape of an explicit semantic statement; values are never inferred."""
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
        raise PdfError('semantic payload needs exactly text, style, font, body_style_id, edges and region_id')
    text = payload['text']
    if not isinstance(text, str) or len(text) > measure.MAX_TEXT or set(text) - measure.CHARACTERS:
        raise PdfError('semantic text is outside the A/B/space/newline scope')
    style = payload['style']
    if not isinstance(style, dict) or set(style) != measure.STYLE_KEYS or style['spacing_intent'] != 'confirmed-inline':
        raise PdfError('semantic style needs exact confirmed values')
    for key in measure.STYLE_KEYS - {'spacing_intent'}:
        _exact(style[key])
    font = payload['font']
    sha = font.get('sha') if isinstance(font, dict) else None
    if (font != dict(sha=sha, policy=measure.font_policy()) or not isinstance(sha, str) or len(sha) != 64
            or set(sha) - set('0123456789abcdef')):
        raise PdfError('semantic font needs an asset SHA and the pinned font policy')
    if not isinstance(payload['body_style_id'], str) or not 0 < len(payload['body_style_id']) <= 64:
        raise PdfError('body_style_id must be a short explicit label')
    if not isinstance(payload['edges'], list):
        raise PdfError('positioning edges must be an explicit list')
    measure.validate_edges(text, payload['edges'])
    if payload['region_id'] != slot['region_id']:
        raise PdfError('semantic region must be the slot confirmed region')


def _region(state, slot):
    """Confirmed shared-flow region (incl. vertical bounds) and leading; nothing from PDF geometry."""
    r = state['regions'][slot['region_id']]
    leading = state['paragraph_policies'][slot['paragraph_id']]['min_line_height']
    return {k: str(F(str(v))) for k, v in
            dict(x=r['x'], baseline=r['first_baseline'], width=r['width'], leading=leading,
                 top=r['bounds'][1], bottom=r['bounds'][3]).items()}


def _source_entry(state, slot):
    paragraph = state['paragraphs'][slot['paragraph_id']]
    return paragraph['style_registry'][paragraph['logical']['typing_style_id']]


def _asset(state, slot, payload, current=None, asset=None):
    """The current font asset checked by SHA.

    Version 1 uses the confirmed registry provider; version 2 uses the current
    authority provider; an explicit font reinterpretation uses the supplied asset.
    """
    provider = _source_entry(state, slot)['reflow_provider']
    path = asset if asset is not None else current['provider']['path'] if current is not None else provider['path']
    if source_sha(path) != payload['font']['sha']:
        raise PdfError('semantic font asset SHA differs from the supplied asset')
    if asset is None and current is None and provider.get('sha256') != payload['font']['sha']:
        raise PdfError('semantic font differs from the confirmed style provider')
    font = ShapedFont(path)
    measure.require_static_tt(font)
    return font


def _authority(current, payload):
    """Shape of the current semantic authority record (layer B); never inferred."""
    if (not isinstance(current, dict) or set(current) != CURRENT_KEYS
            or current['provenance'] not in (SOURCE_CONFIRMED, CALLER_CONFIRMED)):
        raise PdfError('current semantic authority needs an explicit provenance and provider')
    provider = current['provider']
    if (not isinstance(provider, dict) or set(provider) != {'path', 'sha256'} or not isinstance(provider['path'], str)
            or provider['sha256'] != payload['font']['sha']):
        raise PdfError('current semantic font provider differs from the semantic font')


def _source_provider(state, slot):
    provider = _source_entry(state, slot)['reflow_provider']
    if set(provider) != {'path', 'sha256'}:
        raise PdfError('semantic layout needs a path/SHA source provider')
    return dict(path=provider['path'], sha256=provider['sha256'])


def current_registry(state, slot, payload, current):
    """Style registry the current fragment is bound to (never persisted).

    Version 1 and source-confirmed authority use the immutable source registry
    unchanged. Caller-confirmed authority gets an in-memory current-style
    adapter: current editable values and the current provider in separate
    fields with their own provenance; fill/color stay source-confirmed.
    """
    paragraph = state['paragraphs'][slot['paragraph_id']]
    registry = paragraph['style_registry']
    if current is None or current['provenance'] == SOURCE_CONFIRMED:
        return registry
    typing = paragraph['logical']['typing_style_id']
    source = registry[typing]
    style = payload['style']
    values = dict(font_size=float(F(style['font_size'])), horizontal_scale=float(F(style['horizontal_scale'])),
                  tracking=float(F(style['tracking'])), baseline_shift=float(F(style['rise'])))
    attributes = {k: dict(value=v, provenance=CALLER_CONFIRMED) for k, v in values.items()}
    attributes.update({k: deepcopy(source['attributes'][k]) for k in ('fill', 'observed_color')})
    same_font = current['provider']['sha256'] == source['reflow_provider'].get('sha256')
    attributes['font_name'] = (deepcopy(source['attributes']['font_name']) if same_font else
                               dict(value='semantic-font-' + current['provider']['sha256'][:12], provenance=CALLER_CONFIRMED))
    return {typing: dict(id=typing, identity_provenance=CALLER_CONFIRMED, attributes=attributes,
                         reflow_provider=deepcopy(current['provider']))}


def current_confirmations(state, slot, payload, current):
    """Inline tracking/rise confirmations for the writer's current style."""
    paragraph = state['paragraphs'][slot['paragraph_id']]
    if current is None or current['provenance'] == SOURCE_CONFIRMED:
        return styles.render_confirmations(paragraph)
    style = payload['style']
    return {'logical:' + paragraph['logical']['typing_style_id']: dict(
        tracking=float(F(style['tracking'])), baseline_shift=float(F(style['rise'])))}


def codebook(emitted):
    """Canonical island font codebook: used characters plus A and space, sorted."""
    return sorted({g['text'] for g in emitted} | {'A', ' '})


def font_resource(font, emitted):
    """Deterministic generated subset for one canonical island (writer and verifier)."""
    chars = codebook(emitted)
    shaped = {c: font.shape(c, nominal_spacing=True).glyphs[0] for c in chars}
    resource = font.resource([ShapedRun('', tuple(shaped[c] for c in chars))])
    identity = dict(source_sha256=font.source_sha256, font_index=font.font_index,
                    variations=font.variations, instance_sha256=font.instance_sha256)
    return shaped, resource, identity


def _derive(state, slot, payload, font, region=None):
    """Current-only derived state: intervals, omissions, empty metrics (and the exact plan)."""
    style, text = payload['style'], payload['text']
    metrics = [{'text': '\n'} if c == '\n' else measure.nominal_glyph(c, font) for c in text]
    size, rise = F(style['font_size']), F(style['rise'])
    hhea = font.font['hhea']
    empty = dict(ascent=str(max(F(0), F(hhea.ascent, font.upem) * size - rise)),
                 descent=str(max(F(0), -F(hhea.descent, font.upem) * size + rise)))
    plan = measure.layout(text, style, metrics, region or _region(state, slot), empty, payload['edges'])
    derived = dict(intervals=[dict(start=i, end=i + 1, id=f'current:{i}', style='body') for i in range(len(text))],
                   omitted=plan['omitted'], empty=empty)
    return derived, plan


def _binding(state, sid, font=None):
    """Co-binding to this revision; version 2 also names the island's generated font."""
    slot = state['slots'][sid]
    record = slot['source_output']
    result = dict(slot_id=sid, paragraph_id=slot['paragraph_id'], region_id=slot['region_id'],
                  pdf_sha256=state['pdf_sha256'], marker_id=record['marker_id'],
                  owner_program_sha256=record['current']['program_sha256'],
                  owner_block_sha256=record['current']['block_sha256'])
    if font is not None:
        result.update(font_resource=font[0], font_subset_sha256=font[1])
    return result


def _project_v2(state):
    """In-memory v2 view for the unmodified runtime validator; never persisted."""
    value = deepcopy(state)
    value.pop('model_sha256', None)
    value['schema'] = shared_flow.SCHEMA
    for slot in value['slots'].values():
        slot.pop('semantic', None)
    return _seal(value)


def _registry_binding(state, slot, payload, current=None):
    """Semantic text agrees with the shared-flow logical record of this revision.

    Version 1 and source-confirmed style/font must equal the source-confirmed
    registry. Caller-confirmed current authority is not compared with source
    observations; its physical binding is the current-style adapter and the
    canonical island, while the registry is still validated as creation evidence.
    """
    paragraph = state['paragraphs'][slot['paragraph_id']]
    if not payload['text'] == paragraph['logical']['text'] == slot['binding']['paragraph']['text']:
        raise PdfError('semantic text differs from the current paragraph and slot binding')
    if current is not None and current['provenance'] == CALLER_CONFIRMED:
        return
    props = styles.properties(_source_entry(state, slot))
    style = payload['style']
    # Semantic rise and registry baseline_shift are both page y-down offsets
    # (origin = baseline + rise; PDF Ts = -rise).
    if (F(style['font_size']) != F(str(props['font_size'])) or F(style['horizontal_scale']) != F(str(props['horizontal_scale']))
            or F(style['tracking']) != F(str(props['tracking'])) or F(style['rise']) != F(str(props['baseline_shift']))):
        raise PdfError('semantic style differs from the confirmed body style')
    if current is not None and current['provider'] != _source_provider(state, slot):
        raise PdfError('source-confirmed semantic font differs from the confirmed style provider')
    # Tw and positioning edges are physically carried only by glyph positions;
    # they are admitted only when the island is the exact canonical body (_island).


def _island_font(content, state, sid, events):
    """(alias, subset SHA) of the one generated font every island text operator selects."""
    page = state['regions'][state['slots'][sid]['region_id']]['page']
    aliases = {e.state.font.name for e in events if e.state.font is not None}
    if not aliases:
        return None, None
    if len(aliases) != 1:
        raise PdfError('semantic island selects more than one font')
    alias = next(iter(aliases))
    record = state.get('generated_fonts', {}).get(str(page), {}).get(alias)
    if record is None or record['slot_id'] != sid:
        raise PdfError('semantic island font is not a generated font owned by this slot')
    return alias, record['subset_sha256']


def island_font(source, state, sid):
    """Generated font named by the current island (for sealing a version 2 binding)."""
    slot = state['slots'][sid]
    page = state['regions'][slot['region_id']]['page']
    content = ContentPage(source, page)
    try:
        span = owned.inventory(content.streams[-content.page.xref])[slot['source_output']['marker_id']]
        events = [e for e in content.events if not e.invocation and span[1] <= e.operator.start < e.operator.end <= span[2]]
        return _island_font(content, state, sid, events)
    finally:
        content.close()


def _current_font(state, sid, payload, plan, font, alias, codes):
    """Current physical font binding: the generated subset is exactly the current asset's.

    The slot's generated record must name the current asset identity, and the
    deterministic canonical subset rebuilt from that asset must have the same
    bytes (SHA), BaseFont and character codes (hence glyph IDs) as the island.
    """
    page = state['regions'][state['slots'][sid]['region_id']]['page']
    record = state['generated_fonts'][str(page)][alias]
    shaped, resource, identity = font_resource(font, plan['emitted'])
    if record['provider'] != identity or record['provider']['source_sha256'] != payload['font']['sha']:
        raise PdfError('generated font provider is not the current semantic font asset')
    if record['subset_sha256'] != hashlib.sha256(resource.program).hexdigest() or record['basefont'] != resource.basefont:
        raise PdfError('generated font subset is not the current semantic font asset')
    if any(code != int.from_bytes(resource.code(shaped[c]), 'big') for c, code in codes.items()):
        raise PdfError('island glyph codes differ from the current semantic font asset')


def _island(source, state, sid, payload, plan, current=None, asset_font=None):
    """Island-scoped physical binding through the existing owner contract only."""
    slot = state['slots'][sid]
    record = slot['source_output']
    page = state['regions'][slot['region_id']]['page']
    content = ContentPage(source, page)
    try:
        data = content.streams[-content.page.xref]
        span = owned.inventory(data)[record['marker_id']]
        if owned.witness(content, record, span) != record['current']:
            raise PdfError('source output owner witness is stale')
        entry = owned._boundary(content, span[1])
        before, after = owned.context(entry), owned.context(owned._boundary(content, span[2]))
        if not before == after == record['current']['entry_context_sha256']:
            raise PdfError('semantic island entry/exit context differs')
        state_in = entry.state
        default_paint = (state_in.fill[0] == 'g' and tuple(map(str, state_in.fill[1])) == ('0',)
                         and state_in.stroke[0] == 'G' and tuple(map(str, state_in.stroke[1])) == ('0',))
        if (tuple(float(v) for v in state_in.ctm) != (1.0, 0.0, 0.0, 1.0, 0.0, 0.0) or state_in.clip
                or state_in.other or state_in.opacity != 1 or state_in.stroke_opacity != 1 or not default_paint):
            raise PdfError('semantic layout needs an identity, unclipped, default black opaque entry context')
        events = [e for e in content.events if not e.invocation and span[1] <= e.operator.start < e.operator.end <= span[2]]
        fonts = state.get('generated_fonts', {}).get(str(page), {})
        for event in events:
            if event.chars:
                font = fonts.get(event.state.font.name)
                if font is None or font['slot_id'] != sid or font['provider']['source_sha256'] != payload['font']['sha']:
                    raise PdfError('island text is not painted with the semantic font asset')
        if [c.text for e in events for c in e.chars] != [g['text'] for g in plan['emitted']]:
            raise PdfError('island text differs from the semantic plan')
        body = data[span[1]:span[2]]
        canonical = body == canonical_body(content, state, sid, payload, plan, events, body)
        if (F(payload['style']['word_spacing']) != 0 or payload['edges']) and not canonical:
            raise PdfError('Tw or positioning-edge intent needs a canonical island written for it')
        result = dict(body_span=[span[1], span[2]], entry_exit_context=before, canonical=canonical)
        if current is None:
            return result
        alias, subset = _island_font(content, state, sid, events)
        if current['provenance'] == CALLER_CONFIRMED and not canonical:
            raise PdfError('caller-confirmed current style/font needs a canonical island written for it')
        if canonical:
            _current_font(state, sid, payload, plan, asset_font, alias, island.island_codes(body, events)[1])
        return dict(result, font_resource=alias, font_subset_sha256=subset)
    finally:
        content.close()


def canonical_body(content, state, sid, payload, plan, events, body_bytes):
    """The exact canonical body this semantic state would have with the island's own alias/codes.

    Style values come from the semantic payload (current authority); fill stays
    the source-confirmed registry fill (unchanged by every transition).
    """
    slot = state['slots'][sid]
    box = [F(str(v)) for v in content.pdf_page.mediabox]
    if box[0] != 0 or box[1] != 0:
        raise PdfError('semantic island needs a MediaBox anchored at the origin')
    try:
        alias, codes = island.island_codes(body_bytes, events)
    except PdfError:
        return None  # not a canonical-form island (e.g. one written before L2)
    if set(codes) != {g['text'] for g in plan['emitted']}:
        return None
    paragraph = state['paragraphs'][slot['paragraph_id']]
    fill = styles.properties(paragraph['style_registry'][paragraph['logical']['typing_style_id']])['fill']
    region = _region(state, slot)
    return island.body(payload['style'], plan['emitted'], alias=alias, codes=codes, fill=fill, page_top=box[3],
                       origin=(region['x'], region['baseline']))[0]


def _record(slot):
    """(semantic record, payload, current authority or None for version 1)."""
    semantic = slot.get('semantic')
    version = semantic.get('version') if isinstance(semantic, dict) else None
    keys = {1: LEGACY_SEMANTIC_KEYS, SEMANTIC_VERSION: SEMANTIC_KEYS}.get(version) if type(version) is int else None
    if keys is None or set(semantic) != keys:
        raise PdfError('owned slot carries no valid semantic payload')
    return semantic, semantic['payload'], semantic.get('current')


def _verify(source, state):
    """Integrity → scope → creation evidence + owner (v2 validator) → authority → co-binding → island."""
    value = deepcopy(state)
    checksum = value.pop('model_sha256', None)
    if checksum != digest(value):
        raise PdfError('semantic shared flow checksum differs')
    sid, slot = _scope(state)
    semantic, payload, current = _record(slot)
    if current is None:
        restored = shared_flow.open_shared_flow(source, _project_v2(state))
    else:
        _payload(payload, slot)
        _authority(current, payload)
        # Layer A (registry, source observations, contract, owner creation) is
        # validated unchanged; only the current fragment is checked against B.
        restored = shared_flow._open_current_flow(source, _project_v2(state),
            {slot['paragraph_id']: current_registry(state, slot, payload, current)})
    if restored['status'] != 'restored':
        raise PdfError('source output ownership: ' + restored['reason'])
    if current is None:
        _payload(payload, slot)
    keys = LEGACY_BINDING_KEYS if current is None else BINDING_KEYS
    binding = semantic['binding']
    if (not isinstance(binding, dict) or set(binding) != keys
            or {k: binding[k] for k in LEGACY_BINDING_KEYS} != _binding(state, sid)):
        raise PdfError('semantic payload is not bound to the current owner witness')
    _registry_binding(state, slot, payload, current)
    font = _asset(state, slot, payload, current)
    try:
        derived, plan = _derive(state, slot, payload, font)
        if set(semantic['derived']) != DERIVED_KEYS or semantic['derived'] != derived:
            raise PdfError('derived semantic state differs from its payload')
        island = _island(source, state, sid, payload, plan, current, font)
    finally:
        font.font.close()
    if current is not None and binding != _binding(state, sid, (island['font_resource'], island['font_subset_sha256'])):
        raise PdfError('semantic payload is not bound to the current generated font')
    return dict(slot_id=sid, paragraph_id=slot['paragraph_id'], region_id=slot['region_id'], version=semantic['version'],
                semantic=deepcopy(payload), authority=deepcopy(current), derived=derived,
                owner=deepcopy(slot['source_output']), island=island)


def _attach(state, sid, payload, current=None, *, source=None):
    """Seal one semantic record for this revision (version 1 when `current` is None).

    A version 2 record also binds the generated font the island at `source` selects.
    """
    value = deepcopy(state)
    value.pop('model_sha256', None)
    value['schema'] = SCHEMA
    slot = value['slots'][sid]
    slot.pop('semantic', None)
    font = _asset(value, slot, payload, current)
    try:
        derived, _ = _derive(value, slot, payload, font)
    finally:
        font.font.close()
    if current is None:
        slot['semantic'] = dict(version=1, payload=deepcopy(payload), derived=derived, binding=_binding(value, sid))
    else:
        _authority(current, payload)
        slot['semantic'] = dict(version=SEMANTIC_VERSION, payload=deepcopy(payload), current=deepcopy(current),
                                derived=derived, binding=_binding(value, sid, island_font(source, value, sid)))
    return _seal(value)


@proof_session
def confirm_semantic_layout(source, model, *, slot_id, semantic):
    """Explicitly enable semantic layout on an already owned v2 source slot.

    `semantic` is the trusted caller's complete statement. The v2 sidecar is
    validated by the unmodified runtime opener first; no owner is created and
    no value is inferred from PDF geometry. Returns a freshly verified v3 state
    (semantic record version 2) whose current style/font authority is
    `source-confirmed`: it must equal the source-confirmed registry, so no
    reinterpretation is mixed into the first confirmation.

    Compatibility: a stored version 1 record (PR #41/#42) is never upgraded by
    opening it. Passing that verified shared-flow-3 bundle here with exactly
    its stored payload is the explicit request to re-seal it as version 2.
    """
    value = model if isinstance(model, dict) else json.loads(Path(model).read_text(encoding='utf-8'))
    legacy = (value.get('schema') == SCHEMA and slot_id in value.get('slots', {})
              and isinstance(value['slots'][slot_id].get('semantic'), dict)
              and value['slots'][slot_id]['semantic'].get('version') == 1)
    if value.get('schema') != shared_flow.SCHEMA and not legacy:
        raise PdfError('semantic confirmation starts from a shared-flow v2 owner sidecar')
    if legacy:
        opened = open_semantic_flow(source, value)
        if opened['status'] != 'restored' or opened['version'] != 1:
            raise PdfError('semantic version 1 bundle requires confirmation: ' + opened.get('reason', 'version'))
        if _canonical(semantic) != _canonical(opened['semantic']):
            raise PdfError('version 1 compatibility confirmation cannot reinterpret the stored payload')
        state = deepcopy(value)
    else:
        restored = shared_flow.open_shared_flow(source, value)
        if restored['status'] != 'restored':
            raise PdfError('shared flow requires confirmation: ' + restored['reason'])
        state = restored['state']
    if slot_id not in state['slots']:
        raise PdfError('unknown semantic slot')
    candidate = deepcopy(state)
    candidate['schema'] = SCHEMA
    sid, slot = _scope(candidate)
    if sid != slot_id:
        raise PdfError('unknown semantic slot')
    _payload(semantic, slot)
    current = dict(provenance=SOURCE_CONFIRMED, provider=_source_provider(state, slot))
    v3 = _attach(state, sid, semantic, current, source=source)
    _verify(source, v3)
    return v3


@proof_session
def open_semantic_flow(source, model):
    """Read-only open of a shared-flow-3 PDF + sidecar; any mismatch needs confirmation."""
    try:
        value = model if isinstance(model, dict) else json.loads(Path(model).read_text(encoding='utf-8'))
        result = _verify(source, value)
        return dict(status='restored', state=value, **result)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, PdfError) as exc:
        return dict(status='needs_confirmation', reason=str(exc), semantics='unknown')


def semantic_diff(old, new, prefix=''):
    """Changed semantic paths with before/after values (exact, no normalization)."""
    result = {}
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if a != b:
            if isinstance(a, dict) and isinstance(b, dict):
                result.update(semantic_diff(a, b, prefix + key + '.'))
            else:
                result[prefix + key] = dict(before=deepcopy(a), after=deepcopy(b))
    return result


def _next_authority(current, request, asset):
    """Current authority after an authorized request (None stays version 1).

    Only an explicit E change of font_size/horizontal_scale/rise/tracking/font
    makes the authority caller-confirmed; a font change names the supplied
    asset. Everything else carries the current record unchanged (no history).
    """
    if current is None:
        return None
    changes = set(request['changes']) if request['operation'] == 'reinterpret' else set()
    if not changes & STYLE_REINTERPRETATIONS:
        return deepcopy(current)
    provider = (dict(path=str(Path(asset).resolve()), sha256=request['changes']['font']) if 'font' in changes
                else deepcopy(current['provider']))
    return dict(provenance=CALLER_CONFIRMED, provider=provider)


def _next_payload(state, slot, payload, request, asset):
    """Closed N/D/E/R policy. Returns (classification, next payload, writer region)."""
    if not isinstance(request, dict) or 'operation' not in request:
        raise PdfError('semantic transition needs an explicit request')
    operation, nxt, region = request['operation'], deepcopy(payload), None
    if operation == 'reopen':
        if set(request) != {'operation'}:
            raise PdfError('unknown semantic request fields')
        return 'N', nxt, region
    if operation == 'save':
        if set(request) != {'operation'}:
            raise PdfError('unknown semantic request fields')
        return 'D', nxt, region
    if operation == 'edit':
        if set(request) != {'operation', 'start', 'end', 'text'}:
            raise PdfError('unknown semantic request fields')
        a, b, text = request['start'], request['end'], request['text']
        if type(a) is not int or type(b) is not int or not 0 <= a <= b <= len(nxt['text']) or not isinstance(text, str):
            raise PdfError('semantic edit range is invalid')
        nxt['edges'] = measure.edit_edges(nxt['edges'], a, b, len(text))
        nxt['text'] = nxt['text'][:a] + text + nxt['text'][b:]
        return 'D', nxt, region
    if operation == 'reflow':
        if set(request) != {'operation', 'width'}:
            raise PdfError('unknown semantic request fields')
        width = str(_exact(request['width']))
        if width != _region(state, slot)['width']:
            # The width is confirmed shared-flow region data; changing it is a
            # region change, which this surface refuses until a region contract exists.
            raise PdfError('reflow width changes the confirmed shared-flow region; region reassignment refused')
        return 'D', nxt, region
    if operation == 'reinterpret':
        changes = request.get('changes')
        if set(request) != {'operation', 'changes'} or not isinstance(changes, dict) or not changes:
            raise PdfError('semantic reinterpretation needs explicit changes')
        if set(changes) - REINTERPRETABLE:
            raise PdfError('unsupported semantic reinterpretation')
        for key, value in changes.items():
            if key == 'font':
                if asset is None or source_sha(asset) != value:
                    raise PdfError('font reinterpretation needs the explicitly supplied asset')
                nxt['font'] = dict(sha=value, policy=measure.font_policy())
            elif key in ('edges', 'body_style_id'):
                nxt[key] = deepcopy(value)
            else:
                nxt['style'][key] = str(_exact(value))
        return 'E', nxt, region
    if operation in REFUSED_OPERATIONS:
        raise PdfError('refused semantic transition: ' + operation)
    raise PdfError('unknown semantic transition: ' + str(operation))


@proof_session
def plan_semantic_transition(source, model, request, *, asset=None):
    """Authorize and derive the next semantic state; never writes a PDF.

    Execution of any D/E transition requires the L2 writer and authorized
    publication; L1 returns `executable_now=False` for them.
    """
    value = model if isinstance(model, dict) else json.loads(Path(model).read_text(encoding='utf-8'))
    current = _verify(source, value)
    sid = current['slot_id']
    slot = value['slots'][sid]
    if not isinstance(request, dict):
        raise PdfError('semantic transition needs an explicit request')
    if asset is not None and not (request.get('operation') == 'reinterpret' and 'font' in request.get('changes', {})):
        raise PdfError('an asset may only accompany an explicit font reinterpretation')
    classification, nxt, region = _next_payload(value, slot, current['semantic'], request, asset)
    _payload(nxt, slot)
    authority = _next_authority(current['authority'], request, asset)
    if authority is not None:
        _authority(authority, nxt)
    supplied = asset if 'font' in request.get('changes', {}) else None
    font = _asset(value, slot, nxt, authority, supplied)
    try:
        derived, plan = _derive(value, slot, nxt, font, region)
    finally:
        font.font.close()
    return dict(classification=classification, slot_id=sid, request=json.loads(_canonical(request)),
                current=current['semantic'], next=nxt, next_derived=derived, version=current['version'],
                authority=current['authority'], next_authority=authority,
                diff=semantic_diff(current['semantic'], nxt),
                executable_now=classification == 'N', requires=None if classification == 'N' else WRITER,
                next_plan=dict(lines=plan['lines'], emitted=len(plan['emitted'])))


def require_authorized_payload(plan, candidate):
    """Refuse any candidate payload that differs from the authorized next state."""
    if _canonical(candidate) != _canonical(plan['next']):
        diff = semantic_diff(plan['next'], candidate)
        raise PdfError('unauthorized semantic diff: ' + ', '.join(sorted(diff)))
    return plan['diff']


def require_authorized_authority(plan, authority):
    """Refuse any current style/font authority other than the authorized next one."""
    if _canonical(authority) != _canonical(plan['next_authority']):
        raise PdfError('unauthorized current semantic authority')
