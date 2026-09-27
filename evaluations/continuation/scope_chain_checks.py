"""Focused saved-byte/state evidence for the external depth-2 evaluation.

The caller supplies its explicitly reviewed boundary. This module neither
chooses a candidate nor changes the PDF or the engine's admission rules.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

from pdfeditor.content_stream import ContentPage, State, multiply, operators
from pdfeditor.continuation import COMPENSATION_TOLERANCE, INK_MARGIN, PAINT, clip_contains, clip_state, markers, slot_id
from pdfeditor.document_flow import _reseal
from pdfeditor.shared_flow import open_shared_flow
from evaluations.continuation import evaluate as single
from evaluations.continuation.scope_destination import _state_value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _q_pairs(ops):
    """Independently pair actual page-program q/Q operators with a stack."""
    stack, pairs = [], {}
    for index, op in enumerate(ops):
        if op.name == 'q':
            stack.append(index)
        elif op.name == 'Q':
            if not stack:
                raise ValueError('unbalanced Q in saved page program')
            pairs[stack.pop()] = index
    if stack:
        raise ValueError('unbalanced q in saved page program')
    return pairs


def order(pdf, state, destination, reviewed_boundary, source_program):
    """Verify unchanged prefix/suffix, four bindings, compensation and full state restoration."""
    auth = destination['authority']
    scope = auth['graphics_state_scope']
    compensation = auth['ctm_compensation']
    if (scope['depth'] != 2 or scope != reviewed_boundary['graphics_state_scope']
            or compensation != reviewed_boundary['ctm_compensation']
            or auth['graphics_state'] != reviewed_boundary['graphics_state']
            or auth['clip_constraint'] != reviewed_boundary['clip_constraint']):
        raise ValueError('the destination differs from the reviewed scope-chain authority')
    binding = state['destination_bindings'][destination['destination_id']]
    data = PdfReader(pdf).pages[destination['page'] - 1].get_contents().get_data()
    begin, end = markers(destination)
    insertion = reviewed_boundary['offset']
    if 'end' not in binding:
        if begin in data or data != source_program:
            raise ValueError('an unused destination changed its source page')
        return dict(block=False)
    start, stop = binding['start'], binding['end']
    prefix, block, suffix = data[:start], data[start:stop], data[stop:]
    if (start != insertion or data.count(begin) != 1 or not block.startswith(begin) or not block.endswith(end)
            or hashlib.sha256(block).hexdigest() != binding['block_sha256']
            or prefix != source_program[:insertion] or suffix != source_program[insertion:]):
        raise ValueError('the generated binding or its source prefix/suffix differs')
    ops = list(operators(data))
    by_start = {op.start: i for i, op in enumerate(ops)}
    pairs = _q_pairs(ops)
    inside = [i for i, op in enumerate(ops) if start <= op.start and op.end <= stop]
    if not inside or ops[inside[0]].name != 'q' or pairs.get(inside[0]) != inside[-1]:
        raise ValueError('the generated block does not own its complete q ... Q')
    for op, witness in ((ops[inside[0] - 1], reviewed_boundary['previous']),
                        (ops[inside[-1] + 1], reviewed_boundary['next'])):
        if (op.name != witness['operator']
                or hashlib.sha256(data[op.start:op.end]).hexdigest() != witness['sha256']):
            raise ValueError('the generated block is not between the reviewed operators')
    # Both q precede the insertion. Both Q move only by the current block's size.
    where, level_indices = {}, {}
    for level in ('outer', 'inner'):
        where[level], level_indices[level] = {}, {}
        for edge, name in (('opening', 'q'), ('matching', 'Q')):
            witness = scope[level][edge]
            shift = len(block) if edge == 'matching' else 0
            location = {key: witness[key] + shift for key in ('start', 'end')}
            index = by_start.get(location['start'])
            if index is None:
                raise ValueError('a reviewed scope-chain operator disappeared')
            op = ops[index]
            if (op.end != location['end'] or op.name != name
                    or hashlib.sha256(data[op.start:op.end]).hexdigest() != witness['sha256']):
                raise ValueError('a reviewed scope-chain operator changed')
            where[level][edge], level_indices[level][edge] = location, index
        if pairs.get(level_indices[level]['opening']) != level_indices[level]['matching']:
            raise ValueError('a reviewed q no longer pairs with its reviewed Q')
    outer, inner = where['outer'], where['inner']
    if (binding.get('scope') != where
            or not (outer['opening']['end'] <= inner['opening']['start']
                    and inner['opening']['end'] <= start < stop <= inner['matching']['start']
                    and inner['matching']['end'] <= outer['matching']['start'])):
        raise ValueError('the block or a binding is outside the reviewed scope chain')
    cm = [i for i in inside if ops[i].name == 'cm']
    if (cm != [inside[0] + 1]
            or data[ops[cm[0]].start:ops[cm[0]].end] != compensation['operator'].encode('ascii')
            or not block.startswith(begin + b'q ' + compensation['operator'].encode('ascii') + b' BT ')
            or any(ops[i].name in {'W', 'W*', 're', 'n'} for i in inside)):
        raise ValueError('the block does not contain exactly the authority compensation and inherited clip')
    content = ContentPage(pdf, destination['page'])
    try:
        states = {b.operator.start: b for b in content.boundaries}
        body = [states[ops[i].start] for i in inside[:-1]]
        cancelled = list(multiply(tuple(map(float, compensation['matrix'])), tuple(compensation['confirmed_ctm'])))
        clip = auth['graphics_state']['clip']
        if (any(b.q_depth <= 2 or clip_state(b.state.clip) != clip for b in body)
                or list(body[0].state.ctm) != auth['graphics_state']['ctm']
                or any(list(b.state.ctm) != cancelled for b in body[1:])):
            raise ValueError('the generated block does not draw under the compensated CTM and inherited clip')
        restoration = {}
        checkpoints = [('generated', inside[-1], 2, auth['graphics_state'])]
        for level, depth in (('inner', 1), ('outer', 0)):
            opening = level_indices[level]['opening']
            before = _state_value(states[ops[opening - 1].start].state) if opening else _state_value(State())
            if before != scope[level]['restored_state']:
                raise ValueError('the state before a reviewed q differs from its authority')
            checkpoints.append((level, level_indices[level]['matching'], depth, before))
        for label, index, depth, expected in checkpoints:
            boundary = states[ops[index].start]
            actual = _state_value(boundary.state)
            if boundary.q_depth != depth or actual != expected:
                raise ValueError(label + ' Q did not restore the complete comparable graphics state')
            restoration[label] = dict(q_depth=depth, complete_state_equal=True, state_sha256=_digest(actual))
        fields = sorted(auth['graphics_state'])
    finally:
        content.close()
    paints = lambda part: sum(op.name in PAINT for op in operators(part))
    return dict(block=True, start=start, end=stop, block_bytes=len(block),
        prefix_bytes=len(prefix), suffix_bytes=len(suffix),
        prefix_paint_operators=paints(prefix), suffix_paint_operators=paints(suffix),
        prefix_and_suffix_are_the_source_program=True, scope=where,
        block_before_inner_matching_q=True, inner_matching_q_offset_from_block_end=inner['matching']['start'] - stop,
        block_q_depth=[min(b.q_depth for b in body), max(b.q_depth for b in body)],
        compensation_operator=compensation['operator'], compensation_exact_bytes=True,
        compensated_ctm=cancelled, inherited_clip_unchanged=True,
        restoration=dict(checkpoints=restoration, compared_fields=fields,
                         normalization='font object numbers and clip source positions omitted'))


def glyph_coordinates(pdf, state, destination, report):
    """Compare plan, interpreted bytes and saved MuPDF page-space origins; check both ink envelopes."""
    sid = slot_id(destination)
    steps = [step['report'] for step in report['steps'] if step['slot_id'] == sid]
    if len(steps) != 1:
        raise ValueError('the generated slot has no unique glyph plan')
    planned = steps[0]['glyph_plan']
    binding = state['destination_bindings'][destination['destination_id']]
    start, stop = binding['start'], binding['end']
    constraint = destination['authority'].get('clip_constraint')
    inks = report['plan']['fragments'][sid]['ink_bounds']
    if constraint and any(not clip_contains(constraint, ink, INK_MARGIN) for ink in inks):
        raise ValueError('planned generated ink extends beyond the inherited rectangle')
    content = ContentPage(pdf, destination['page'])
    try:
        events = [e for e in content.events if not e.invocation and start <= e.operator.start < stop]
        if any(e.error for e in events):
            raise ValueError('a generated text event cannot be interpreted or matched to saved glyphs')
        chars = [(event, char) for event in events for char in event.chars]
        if len(chars) != len(planned):
            raise ValueError('the saved generated glyph count differs from the plan')
        max_interpreted = max_saved = 0.0
        saved, seqnos = [], set()
        for wanted, (event, char) in zip(planned, chars):
            if (char.text != wanted['unicode'] or char.code.hex() != wanted['code']
                    or event.state.font.name != wanted['font_resource'] or len(char.source_orders) != 1):
                raise ValueError('a generated glyph differs from its planned Unicode/code/font')
            actual = content.actual[char.source_orders[0]]
            if actual['unicode'] != wanted['unicode'] or actual['gid'] != wanted['glyph_id']:
                raise ValueError('a saved generated glyph differs from its planned Unicode/GID')
            max_interpreted = max(max_interpreted, *(abs(a - b) for a, b in zip(wanted['origin'], char.origin)))
            max_saved = max(max_saved, *(abs(a - b) for a, b in zip(wanted['origin'], actual['origin'])))
            saved.append(dict(unicode=actual['unicode'], glyph_id=actual['gid'], origin=list(actual['origin'])))
            seqnos.add(actual['span']['seqno'])
        tolerance = COMPENSATION_TOLERANCE
        if max(max_interpreted, max_saved) >= tolerance:
            raise ValueError('generated page-space glyph origins differ from the plan')
        paints = content.page.get_bboxlog()
        envelopes = []
        for seqno in sorted(seqnos):
            kind, box = paints[seqno]
            if kind != 'fill-text':
                raise ValueError('the generated block has an unexpected text paint')
            if box[0] <= box[2] and box[1] <= box[3]:
                if constraint and not clip_contains(constraint, box):
                    raise ValueError('saved generated text paint extends beyond the inherited rectangle')
                envelopes.append(list(box))
    finally:
        content.close()
    envelope_bounds = ([min(box[0] for box in envelopes), min(box[1] for box in envelopes),
                        max(box[2] for box in envelopes), max(box[3] for box in envelopes)] if envelopes else None)
    return dict(glyphs=len(saved), planned_ink_boxes=len(inks),
        saved_paint_envelopes=dict(count=len(envelopes), bounds=envelope_bounds, sha256=_digest(envelopes)),
        planned_ink_with_margin_inside_clip=True if constraint else None,
        saved_paint_inside_clip=True if constraint else None, planned_ink_margin=INK_MARGIN if constraint else None,
        planned_to_interpreted_max_coordinate_error=max_interpreted,
        planned_to_saved_max_coordinate_error=max_saved, origin_tolerance=tolerance,
        saved_glyph_origins_sha256=_digest(saved),
        planned_origin_samples=[] if not planned else [
            {key: glyph[key] for key in ('unicode', 'glyph_id', 'origin')} for glyph in (planned[0], planned[-1])],
        saved_origin_samples=[] if not saved else [saved[0], saved[-1]])


def tampered_binding(directory, pdf, sidecar, destination):
    """Reseal one inner-Q binding forged to the identical bytes at the outer Q."""
    state = json.loads(Path(sidecar).read_text(encoding='utf-8'))
    ident = destination['destination_id']
    control = Path(directory) / 'tampered-control.json'
    single.write(control, _reseal(state))
    if open_shared_flow(pdf, control)['status'] != 'restored':
        raise ValueError('the resealed unchanged sidecar was not restored')
    scope = state['destination_bindings'][ident]['scope']
    original, replacement = scope['inner']['matching'], scope['outer']['matching']
    data = PdfReader(pdf).pages[destination['page'] - 1].get_contents().get_data()
    original_bytes = data[original['start']:original['end']]
    if original == replacement or original_bytes != b'Q' or data[replacement['start']:replacement['end']] != original_bytes:
        raise ValueError('the negative control does not use another identical-bytes Q')
    bad = deepcopy(state)
    bad['destination_bindings'][ident]['scope']['inner']['matching'] = deepcopy(replacement)
    forged = Path(directory) / 'tampered-inner-Q-to-outer-Q.json'
    single.write(forged, _reseal(bad))
    opened = open_shared_flow(pdf, forged)
    if opened['status'] != 'needs_confirmation' or 'not in its confirmed q ... Q scope' not in opened['reason']:
        raise ValueError('the forged scope-chain binding was not refused by the scope check')
    return dict(case='inner-Q-binding-to-identical-bytes-outer-Q', status=opened['status'], reason=opened['reason'],
                resealed_unchanged_control='restored', identical_operator_bytes=original_bytes.decode(),
                moved='inner.matching', original=original, replacement=replacement)
