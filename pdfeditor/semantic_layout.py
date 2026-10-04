"""Semantic layout state for one owned shared-flow source slot (L1, read-only).

`pdfengine-shared-flow-3` is a shared-flow v2 state whose single owned source
slot also carries a bounded current semantic payload. Persistent artifacts stay
the PDF plus this one sidecar. Ownership is never re-implemented here: the
unmodified v2 validator (including `source_ownership.validate`) runs on an
in-memory v2 projection. Trust model P4: the trusted API caller states
semantics explicitly; every semantic change comes from an explicit request;
the model checksum is integrity only. Nothing here writes a PDF, creates or
adopts an owner, upgrades a v2 sidecar implicitly, or refreshes a PDF hash.
"""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path

from .alignment import DEFAULT
from .attributed import digest
from .backend import PdfError
from .content_stream import ContentPage
from .editable import _seal
from .proof_session import proof_session
from .selection import source_sha
from .shaped_font import ShapedFont
from . import semantic_island as island
from . import semantic_measure as measure
from . import shared_flow
from . import source_ownership as owned
from . import story_styles as styles

SCHEMA = 'pdfengine-shared-flow-3'
SEMANTIC_KEYS = frozenset({'version', 'payload', 'derived', 'binding'})
PAYLOAD_KEYS = frozenset({'text', 'style', 'font', 'body_style_id', 'edges', 'region_id'})
DERIVED_KEYS = frozenset({'intervals', 'omitted', 'empty'})
BINDING_KEYS = frozenset({'slot_id', 'paragraph_id', 'region_id', 'pdf_sha256', 'marker_id',
                          'owner_program_sha256', 'owner_block_sha256'})
REINTERPRETABLE = frozenset({'font', 'font_size', 'horizontal_scale', 'rise', 'tracking', 'word_spacing',
                             'edges', 'body_style_id'})
REFUSED_OPERATIONS = frozenset({'split', 'join', 'mixed_style', 'vertical_edge', 'cross_paragraph_edge',
                                'nonadjacent_edge', 'region_reassignment', 'hash_refresh', 'replace_record'})
WRITER = 'L2 canonical island writer + authorized publication'

FIELD_AUTHORITY = {
    'immutable_creation_identity': ('flow.id', 'slots[s].paragraph_id', 'slots[s].region_id',
        'slots[s].source_snapshot_sha256', 'slots[s].source_output.marker_id',
        'slots[s].source_output.created_from', 'contract_sha256'),
    'mutable_current_owner_witness': ('slots[s].source_output.state', 'slots[s].source_output.current'),
    'semantic_authority': ('slots[s].semantic.payload',),
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


def _asset(state, slot, payload, asset=None):
    """The confirmed registry provider (or an explicitly supplied replacement) checked by SHA."""
    paragraph = state['paragraphs'][slot['paragraph_id']]
    provider = paragraph['style_registry'][paragraph['logical']['typing_style_id']]['reflow_provider']
    path = asset if asset is not None else provider['path']
    if source_sha(path) != payload['font']['sha']:
        raise PdfError('semantic font asset SHA differs from the supplied asset')
    if asset is None and provider.get('sha256') != payload['font']['sha']:
        raise PdfError('semantic font differs from the confirmed style provider')
    font = ShapedFont(path)
    measure.require_static_tt(font)
    return font


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


def _binding(state, sid):
    slot = state['slots'][sid]
    record = slot['source_output']
    return dict(slot_id=sid, paragraph_id=slot['paragraph_id'], region_id=slot['region_id'],
                pdf_sha256=state['pdf_sha256'], marker_id=record['marker_id'],
                owner_program_sha256=record['current']['program_sha256'],
                owner_block_sha256=record['current']['block_sha256'])


def _project_v2(state):
    """In-memory v2 view for the unmodified runtime validator; never persisted."""
    value = deepcopy(state)
    value.pop('model_sha256', None)
    value['schema'] = shared_flow.SCHEMA
    for slot in value['slots'].values():
        slot.pop('semantic', None)
    return _seal(value)


def _registry_binding(state, slot, payload):
    """Semantic text and style agree with the shared-flow logical record of this revision."""
    paragraph = state['paragraphs'][slot['paragraph_id']]
    if not payload['text'] == paragraph['logical']['text'] == slot['binding']['paragraph']['text']:
        raise PdfError('semantic text differs from the current paragraph and slot binding')
    props = styles.properties(paragraph['style_registry'][paragraph['logical']['typing_style_id']])
    style = payload['style']
    if (F(style['font_size']) != F(str(props['font_size'])) or F(style['horizontal_scale']) != F(str(props['horizontal_scale']))
            or F(style['tracking']) != F(str(props['tracking'])) or F(style['rise']) != -F(str(props['baseline_shift']))):
        raise PdfError('semantic style differs from the confirmed body style')
    # Tw and positioning edges are physically carried only by glyph positions;
    # they are admitted only when the island is the exact canonical body (_island).


def _island(source, state, sid, payload, plan):
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
        canonical = data[span[1]:span[2]] == canonical_body(content, state, sid, payload, plan, events, data[span[1]:span[2]])
        if (F(payload['style']['word_spacing']) != 0 or payload['edges']) and not canonical:
            raise PdfError('Tw or positioning-edge intent needs a canonical island written for it')
        return dict(body_span=[span[1], span[2]], entry_exit_context=before, canonical=canonical)
    finally:
        content.close()


def canonical_body(content, state, sid, payload, plan, events, body_bytes):
    """The exact canonical body this semantic state would have with the island's own alias/codes."""
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


def _verify(source, state):
    """Integrity → scope → unmodified v2 owner validation → co-binding → island binding."""
    value = deepcopy(state)
    checksum = value.pop('model_sha256', None)
    if checksum != digest(value):
        raise PdfError('semantic shared flow checksum differs')
    sid, slot = _scope(state)
    semantic = slot.get('semantic')
    if not isinstance(semantic, dict) or set(semantic) != SEMANTIC_KEYS or semantic['version'] != 1:
        raise PdfError('owned slot carries no valid semantic payload')
    restored = shared_flow.open_shared_flow(source, _project_v2(state))
    if restored['status'] != 'restored':
        raise PdfError('source output ownership: ' + restored['reason'])
    payload = semantic['payload']
    _payload(payload, slot)
    if set(semantic['binding']) != BINDING_KEYS or semantic['binding'] != _binding(state, sid):
        raise PdfError('semantic payload is not bound to the current owner witness')
    _registry_binding(state, slot, payload)
    font = _asset(state, slot, payload)
    try:
        derived, plan = _derive(state, slot, payload, font)
    finally:
        font.font.close()
    if set(semantic['derived']) != DERIVED_KEYS or semantic['derived'] != derived:
        raise PdfError('derived semantic state differs from its payload')
    island = _island(source, state, sid, payload, plan)
    return dict(slot_id=sid, paragraph_id=slot['paragraph_id'], region_id=slot['region_id'],
                semantic=deepcopy(payload), derived=derived, owner=deepcopy(slot['source_output']), island=island)


def _attach(state, sid, payload):
    value = deepcopy(state)
    value.pop('model_sha256', None)
    value['schema'] = SCHEMA
    slot = value['slots'][sid]
    font = _asset(value, slot, payload)
    try:
        derived, _ = _derive(value, slot, payload, font)
    finally:
        font.font.close()
    slot['semantic'] = dict(version=1, payload=deepcopy(payload), derived=derived, binding=_binding(value, sid))
    return _seal(value)


@proof_session
def confirm_semantic_layout(source, model, *, slot_id, semantic):
    """Explicitly enable semantic layout on an already owned v2 source slot.

    `semantic` is the trusted caller's complete statement. The v2 sidecar is
    validated by the unmodified runtime opener first; no owner is created and
    no value is inferred from PDF geometry. Returns a freshly verified v3 state.
    """
    value = model if isinstance(model, dict) else json.loads(Path(model).read_text(encoding='utf-8'))
    if value.get('schema') != shared_flow.SCHEMA:
        raise PdfError('semantic confirmation starts from a shared-flow v2 owner sidecar')
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
    v3 = _attach(state, sid, semantic)
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
    font = _asset(value, slot, nxt, asset if 'font' in request.get('changes', {}) else None)
    try:
        derived, plan = _derive(value, slot, nxt, font, region)
    finally:
        font.font.close()
    return dict(classification=classification, slot_id=sid, request=json.loads(_canonical(request)),
                current=current['semantic'], next=nxt, next_derived=derived,
                diff=semantic_diff(current['semantic'], nxt),
                executable_now=classification == 'N', requires=None if classification == 'N' else WRITER,
                next_plan=dict(lines=plan['lines'], emitted=len(plan['emitted'])))


def require_authorized_payload(plan, candidate):
    """Refuse any candidate payload that differs from the authorized next state."""
    if _canonical(candidate) != _canonical(plan['next']):
        diff = semantic_diff(plan['next'], candidate)
        raise PdfError('unauthorized semantic diff: ' + ', '.join(sorted(diff)))
    return plan['diff']
