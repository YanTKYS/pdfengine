"""B1-L-O design model: semantic payload inside the shared-flow owner sidecar.

Design/evidence only; NOT a runtime schema, opener or writer. The proposed
`pdfengine-shared-flow-3` composition keeps exactly two persistent artifacts:
the PDF and one shared-flow sidecar. The single owned source slot carries its
existing `source_output` owner witness and a bounded current semantic payload,
sealed together by the existing model checksum. Ownership is re-proven only by
the unmodified runtime shared-flow v2 validation (including
`source_ownership.validate`) on a derived v2 projection; nothing here creates,
adopts or upgrades an owner. P4 trust model unchanged: trusted caller, explicit
request, checksum for integrity only.
"""
from copy import deepcopy
from fractions import Fraction as F
from hashlib import sha256
from pathlib import Path

from pdfeditor.attributed import digest
from pdfeditor.content_stream import ContentPage
from pdfeditor.document_flow import _reseal
from pdfeditor.shared_flow import SCHEMA as V2, open_shared_flow
from pdfeditor import source_ownership as owned
from pdfeditor import story_styles as styles
from evaluations.anchors.semantic_binding import font_policy, require
from evaluations.anchors.authorized_publication import derive

SCHEMA = 'pdfengine-shared-flow-3'
SEMANTIC_KEYS = {'version', 'payload', 'derived', 'binding'}
PAYLOAD_KEYS = {'text', 'style', 'font', 'body_style_id', 'edges', 'region_id'}
BINDING_KEYS = {'slot_id', 'paragraph_id', 'region_id', 'pdf_sha256', 'marker_id',
                'owner_program_sha256', 'owner_block_sha256'}

# Field classes of the composition (B1-L-O schema contract, §19.10).
FIELD_CLASSES = {
    'immutable_creation_identity': ['flow.id', 'slots[s].paragraph_id', 'slots[s].region_id',
        'slots[s].source_snapshot_sha256', 'slots[s].source_output.version',
        'slots[s].source_output.marker_id', 'slots[s].source_output.created_from', 'contract_sha256'],
    'mutable_current_owner_witness': ['slots[s].source_output.state', 'slots[s].source_output.current'],
    'semantic_authority': ['slots[s].semantic.payload'],
    'derived_current_physical_binding': ['pdf_sha256', 'slots[s].binding', 'generated_fonts',
        'physical_breaks', 'slots[s].semantic.derived', 'slots[s].semantic.binding'],
    'integrity': ['model_sha256 (digest link previous_model_sha256 keeps its existing role)'],
}


def file_sha(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def region_values(state, region_id, paragraph_id):
    """Confirmed shared-flow region is the layout authority; nothing is inferred."""
    r = state['regions'][region_id]
    return dict(x=str(F(str(r['x']))), baseline=str(F(str(r['first_baseline']))),
                width=str(F(str(r['width']))),
                leading=str(F(str(state['paragraph_policies'][paragraph_id]['min_line_height']))))


def model_semantic(state, sid):
    """The P4 model's semantic view, with region values taken from the confirmed region."""
    slot = state['slots'][sid]
    payload = slot['semantic']['payload']
    value = {k: deepcopy(payload[k]) for k in ('text', 'style', 'font', 'body_style_id', 'edges')}
    value['region'] = region_values(state, payload['region_id'], slot['paragraph_id'])
    return value


def scope(state):
    """Scope conditions; any failure refuses (no partial or multi-slot editing)."""
    require(state.get('schema') == SCHEMA, 'SEMANTIC_SCHEMA_REQUIRED')
    require(len(state['flow']['paragraphs']) == 1 and len(state['paragraphs']) == 1, 'EXACTLY_ONE_PARAGRAPH')
    require(len(state['slots']) == 1, 'EXACTLY_ONE_SOURCE_SLOT')
    sid, slot = next(iter(state['slots'].items()))
    require(slot.get('destination_id') is None and slot.get('creation_provenance') is None,
            'GENERATED_CONTINUATION_SLOT_REFUSED')
    require(not state.get('continuation_destinations') and not state.get('destination_bindings'),
            'CONTINUATION_REFUSED')
    require(len(state['regions']) == 1 and state['flow']['regions'] == [slot['region_id']], 'ONE_CONFIRMED_REGION')
    require(isinstance(slot.get('source_output'), dict) and slot['source_output'].get('state') == 'owned',
            'SOURCE_SLOT_NOT_OWNED')
    paragraph = state['paragraphs'][slot['paragraph_id']]
    registry = paragraph['style_registry']
    require(len(registry) == 1 and paragraph['logical']['typing_style_id'] in registry, 'ONE_BODY_STYLE')
    require(isinstance(slot.get('semantic'), dict) and set(slot['semantic']) == SEMANTIC_KEYS, 'SEMANTIC_PAYLOAD_REQUIRED')
    return sid, slot


def project_v2(state):
    """Derived v2 view for the unmodified runtime validator; never persisted."""
    value = deepcopy(state)
    value.pop('model_sha256', None)
    value['schema'] = V2
    for slot in value['slots'].values():
        slot.pop('semantic', None)
    return _reseal(value)


def seal(state):
    value = deepcopy(state)
    value['schema'] = SCHEMA
    return _reseal(value)


def integrity(state):
    value = deepcopy(state)
    claimed = value.pop('model_sha256', None)
    require(claimed == digest(value), 'MODEL_INTEGRITY')


def island(content, slot):
    """Owner span, parsed events and derived entry/exit context evidence."""
    record = slot['source_output']
    data = content.streams[-content.page.xref]
    span = owned.inventory(data)[record['marker_id']]
    current = owned.witness(content, record, span)
    before = owned.context(owned._boundary(content, span[1]))
    after = owned.context(owned._boundary(content, span[2]))
    events = [e for e in content.events if not e.invocation and span[1] <= e.operator.start < e.operator.end <= span[2]]
    return dict(span=span, current=current, before=before, after=after, events=events)


def semantic_binding(pdf, state, sid, asset):
    """Semantic payload ↔ owner witness ↔ current PDF co-binding (all must be current)."""
    slot = state['slots'][sid]
    semantic = slot['semantic']
    require(semantic['version'] == 1, 'SEMANTIC_VERSION')
    payload, binding = semantic['payload'], semantic['binding']
    require(set(payload) == PAYLOAD_KEYS and set(binding) == BINDING_KEYS, 'SEMANTIC_FIELDS')
    record = slot['source_output']
    require(binding == dict(slot_id=sid, paragraph_id=slot['paragraph_id'], region_id=slot['region_id'],
                            pdf_sha256=state['pdf_sha256'], marker_id=record['marker_id'],
                            owner_program_sha256=record['current']['program_sha256'],
                            owner_block_sha256=record['current']['block_sha256']), 'OWNER_SEMANTIC_BINDING')
    require(payload['region_id'] == slot['region_id'], 'REGION_AUTHORITY')
    paragraph = state['paragraphs'][slot['paragraph_id']]
    require(payload['text'] == paragraph['logical']['text'] == slot['binding']['paragraph']['text'], 'SEMANTIC_TEXT_BINDING')
    props = styles.properties(paragraph['style_registry'][paragraph['logical']['typing_style_id']])
    style = payload['style']
    require(F(style['font_size']) == F(str(props['font_size'])) and F(style['horizontal_scale']) == F(str(props['horizontal_scale']))
            and F(style['tracking']) == F(str(props['tracking'])) and F(style['rise']) == -F(str(props['baseline_shift'])),
            'SEMANTIC_STYLE_BINDING')
    # The current runtime writer (stand-in for the L2 canonical writer) emits 0 Tw
    # and no positioning edges; Tw/edge intent is admitted only with that writer.
    require(F(style['word_spacing']) == 0 and payload['edges'] == [], 'TW_EDGE_REQUIRE_L2_WRITER')
    require(payload['font'] == dict(sha=sha256(asset).hexdigest(), policy=font_policy()), 'FONT_ASSET_BINDING')
    derived, plan = derive(model_semantic(state, sid), asset)
    require(semantic['derived'] == dict(intervals=derived['intervals'], omitted=derived['omitted'],
                                        empty=derived['model']['empty']), 'DERIVED_SEMANTIC_MISMATCH')
    content = ContentPage(pdf, state['regions'][slot['region_id']]['page'])
    try:
        evidence = island(content, slot)
        require(evidence['current'] == record['current'], 'OWNER_WITNESS_STALE')
        require(evidence['before'] == evidence['after'] == record['current']['entry_context_sha256'], 'ENTRY_EXIT_CONTEXT')
        records = state.get('generated_fonts', {}).get(str(content.page.number + 1), {})
        emitted = [c.text for e in evidence['events'] for c in e.chars]
        for event in evidence['events']:
            if event.chars:
                font = records.get(event.state.font.name)
                require(font is not None and font['slot_id'] == sid and
                        font['provider']['source_sha256'] == payload['font']['sha'], 'ISLAND_FONT_NOT_SEMANTIC_ASSET')
        require(emitted == [g['metric']['text'] for g in plan['glyphs']], 'ISLAND_TEXT_NOT_SEMANTIC_TEXT')
        return dict(entry_exit_proven=evidence['before'] == evidence['after'], body_span=list(evidence['span'][1:3]),
                    show_spans=[[e.operator.start, e.operator.end] for e in evidence['events'] if e.chars],
                    emitted=''.join(emitted), plan=plan)
    finally:
        content.close()


def open_semantic_flow(pdf, state, asset):
    """Read-only design opener: integrity → scope → runtime v2 owner validation → co-binding."""
    integrity(state)
    sid, _ = scope(state)
    require(state['pdf_sha256'] == file_sha(pdf), 'STALE_PDF')
    opened = open_shared_flow(pdf, project_v2(state))
    require(opened['status'] == 'restored', 'RUNTIME_OWNER_VALIDATION:' + str(opened.get('reason')))
    evidence = semantic_binding(pdf, state, sid, asset)
    return dict(slot_id=sid, owner=state['slots'][sid]['source_output'], evidence=evidence)


def attach(v2_state, sid, payload, asset):
    """Bind an explicit (confirmed or authorized) payload to a runtime-verified v2 state."""
    state = deepcopy(v2_state)
    state.pop('model_sha256', None)
    slot = state['slots'][sid]
    record = slot['source_output']
    require(isinstance(record, dict) and record.get('state') == 'owned', 'SOURCE_SLOT_NOT_OWNED')
    semantic = dict(version=1, payload=deepcopy(payload), derived={}, binding=dict(
        slot_id=sid, paragraph_id=slot['paragraph_id'], region_id=slot['region_id'], pdf_sha256=state['pdf_sha256'],
        marker_id=record['marker_id'], owner_program_sha256=record['current']['program_sha256'],
        owner_block_sha256=record['current']['block_sha256']))
    slot['semantic'] = semantic
    state['schema'] = SCHEMA
    derived, _ = derive(model_semantic(state, sid), asset)
    semantic['derived'] = dict(intervals=derived['intervals'], omitted=derived['omitted'], empty=derived['model']['empty'])
    return seal(state)


def initial_confirmation(pdf, v2_state, request, asset):
    """Explicit opt-in: a trusted caller's full statement over an already-owned v2 slot.

    Opening a v2 sidecar never does this implicitly; no owner is created here.
    """
    require(request.get('operation') == 'confirm-semantic-layout' and set(request) == {'operation', 'slot_id', 'payload'},
            'EXPLICIT_SEMANTIC_CONFIRMATION_REQUIRED')
    require(v2_state.get('schema') == V2, 'V2_OWNER_SIDECAR_REQUIRED')
    opened = open_shared_flow(pdf, v2_state)
    require(opened['status'] == 'restored', 'RUNTIME_OWNER_VALIDATION')
    state = attach(opened['state'], request['slot_id'], request['payload'], asset)
    open_semantic_flow(pdf, state, asset)
    return state


def to_shared_flow_changes(state, request):
    """Translate an authorized D request into the existing shared-flow edit form."""
    paragraph = state['flow']['paragraphs'][0]
    if request['operation'] == 'save':
        return {}
    require(request['operation'] == 'edit', 'L2_WRITER_REQUIRED_FOR:' + request['operation'])
    style = state['paragraphs'][paragraph]['logical']['typing_style_id']
    return {paragraph: dict(edits=[dict(start=request['start'], end=request['end'], text=request['text'], style_id=style)])}


def verdict(gates):
    required = {
        'model_determinism': {'exact_derived'},
        'input_admissibility': {'scoped_physical', 'scope_conditions'},
        'authenticated_binding': {'current_binding', 'semantic_transition_authority', 'stored_owner_binding',
            'physical_mutation_ownership', 'candidate_reverification', 'owner_semantic_rebind',
            'fresh_bundle_reopen', 'atomic_pair_publication'}}
    for layer, names in required.items():
        results = gates.get(layer, {})
        if not names <= results.keys() or any(v is not True for v in results.values()):
            return 'NOT READY'
    return 'DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR'
