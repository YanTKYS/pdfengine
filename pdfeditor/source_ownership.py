"""Current-revision ownership of shared-flow source text output.

Markers locate bytes; the caller-trusted semantic binding, current hashes,
grammar and interpreter witnesses authorize their replacement. No history,
font ownership, destination authority or paragraph layout lives here.
"""
from copy import deepcopy
import hashlib
import math

from .attributed import digest
from .backend import PdfError
from .content_stream import ContentPage, operators
from .operator_nesting import audit, require_text_object_split

DOMAIN = 'pdfengine-source-slot-v1'
PREFIX = ('% ' + DOMAIN + ' ').encode('ascii')
CREATE = 'source-output-create'
REWRITE = 'source-output-rewrite'
BODY_OPERATORS = frozenset('q Q BT ET Tf Tz Tc Tw Ts Tm Tj TJ g rg k'.split())


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _hex(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def identity(flow_id, slot_id, created_from):
    return digest([DOMAIN, flow_id, slot_id, created_from])


def seed(state, sid):
    created = dict(pdf_sha256=state['pdf_sha256'],
                   paragraph_snapshot_sha256=state['slots'][sid]['source_snapshot_sha256'])
    return dict(version=1, state='uninitialized', marker_id=identity(state['flow']['id'], sid, created),
                created_from=created)


def immutable(record):
    return {k: record[k] for k in ('version', 'marker_id', 'created_from')}


def markers(record):
    token = record['marker_id'].encode('ascii')
    return b'\n' + PREFIX + b'begin ' + token + b'\n', PREFIX + b'end ' + token + b'\n'


def inventory(data):
    """Recognize complete marker comment lines only in existing operator gaps.

    operators() consumes complete operands (including strings) and rejects
    inline images. It, not a second lexer/regex parser, defines the gaps.
    """
    found = {}
    previous = 0
    ops = list(operators(data))
    for index, stop in enumerate([op.start for op in ops] + [len(data)]):
        # The matching operator's end becomes the next gap's start below.
        gap = data[previous:stop]
        offset = previous
        for line in gap.splitlines(keepends=True):
            if line.lstrip().startswith(PREFIX.rstrip()):
                fields = line.rstrip(b'\r\n').split(b' ')
                if (len(fields) != 4 or fields[:2] != [b'%', DOMAIN.encode()]
                        or fields[2] not in (b'begin', b'end')
                        or not _hex(fields[3].decode('ascii', errors='replace'))
                        or line != b' '.join(fields) + b'\n'
                        or offset == 0 or data[offset-1:offset] != b'\n'):
                    raise PdfError('malformed source output marker comment')
                ident, kind = fields[3].decode('ascii'), fields[2].decode('ascii')
                entries = found.setdefault(ident, {})
                if kind in entries:
                    raise PdfError('duplicate source output marker')
                entries[kind] = (offset, offset + len(line))
            offset += len(line)
        if stop != len(data):
            previous = ops[index].end
    result = {}
    for ident, pair in found.items():
        if set(pair) != {'begin', 'end'} or pair['begin'][1] >= pair['end'][0]:
            raise PdfError('missing or reversed source output marker')
        result[ident] = (pair['begin'][0]-1, pair['begin'][1], pair['end'][0], pair['end'][1])
    spans = sorted(result.values())
    if any(a[3] > b[0] for a, b in zip(spans, spans[1:])):
        raise PdfError('overlapping source output islands')
    return result


def grammar(body):
    if b'%' in body:
        raise PdfError('comments are not allowed in source output body')
    ops = list(operators(body))
    if not ops or any(op.name not in BODY_OPERATORS for op in ops) or audit(body)['violations']:
        raise PdfError('invalid source output body grammar')
    phase = 'q'
    for op in ops:
        name = op.name
        if phase in ('q', 'BT', 'Q'):
            if name != phase or op.args:
                raise PdfError('source output requires isolated q BT ... ET Q groups')
            phase = {'q': 'BT', 'BT': 'text', 'Q': 'q'}[phase]
        elif name == 'ET' and not op.args:
            phase = 'Q'
        elif name in ('q', 'Q', 'BT', 'ET'):
            raise PdfError('source output group nesting mismatch')
    if phase != 'q':
        raise PdfError('unclosed source output group')


def _canonical(value):
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise PdfError('nonfinite source output context')
        return 0 if value == 0 else value
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {k: _canonical(v) for k, v in sorted(value.items())}
    raise PdfError('unsupported source output context evidence')


def context(boundary, *, split=False):
    """Semantic State evidence, with strings preserved and location fields removed."""
    if (boundary.text_object and not split or boundary.marked_content_depth
            or boundary.compatibility_depth or boundary.pending_path or boundary.pending_clip):
        raise PdfError('source output needs an unmarked, path-free page-level context')
    state = boundary.state
    if (state.tr != 0 or state.opacity != 1 or state.stroke_opacity != 1
            or state.fill[0] not in ('g', 'rg', 'k') or state.stroke[0] not in ('G', 'RG', 'K')):
        raise PdfError('unsupported source output paint or color-space context')
    value = state.report()
    value.pop('font_xref')
    clips = []
    for clip in state.clip:
        if (set(clip) != {'rule', 'path', 'at'} or clip['rule'] not in ('W', 'W*')
                or len(clip['path']) != 1 or clip['path'][0]['operator'] != 're'
                or set(clip['path'][0]) != {'operator', 'args', 'ctm'}):
            raise PdfError('source output needs a proven rectangular clip')
        path = clip['path'][0]
        if (len(path['args']) != 4 or len(path['ctm']) != 6
                or abs(path['ctm'][1]) > .00001 or abs(path['ctm'][2]) > .00001):
            raise PdfError('unsupported source output clip transform')
        clips.append(dict(rule=clip['rule'], path=clip['path']))
    value['clip'] = clips
    value.update(q_depth=boundary.q_depth, text_object=False,
                 marked_content_depth=0, compatibility_depth=0, pending_path=False, pending_clip=False)
    return digest(_canonical(value))


def _boundary(content, offset):
    found = [b for b in content.boundaries if b.operator.end <= offset]
    if not found:
        raise PdfError('source output has no preceding operator context')
    return found[-1]


def initial_context(content, paragraph):
    if content.errors or audit(content.streams[-content.page.xref])['violations']:
        raise PdfError('invalid source program for source output creation')
    require_text_object_split(content, paragraph.first.operator.end)
    return context(_boundary(content, paragraph.first.operator.end), split=True)


def witness(content, record, span):
    data = content.streams[-content.page.xref]
    start, body_start, body_end, end = span
    if content.errors:
        raise PdfError('source output program has interpreter errors')
    grammar(data[body_start:body_end])
    before = context(_boundary(content, body_start))
    after = context(_boundary(content, body_end))
    if before != after:
        raise PdfError('source output changes its surrounding context')
    return dict(program_sha256=sha(data), range=[start, end], block_sha256=sha(data[start:end]),
                entry_context_sha256=before)


def containment(content, snapshot, span):
    _, start, end, _ = span
    selected = set(snapshot['selection']['glyph_ids'])
    inside = set()
    for event in content.events:
        glyphs = {g for c in event.chars for g in c.source_orders}
        in_body = not event.invocation and start <= event.operator.start < event.operator.end <= end
        if glyphs & selected and not in_body:
            raise PdfError('current source paragraph glyph is outside its island')
        if in_body:
            if event.error or any(not c.source_orders for c in event.chars) or glyphs - selected:
                raise PdfError('foreign or unproven glyph inside source output island')
            inside |= glyphs
    if inside != selected:
        raise PdfError('source output glyph containment mismatch')
    witnesses = list(snapshot.get('style_slot_bindings', {}).values())
    if snapshot.get('insertion_binding'):
        witnesses.append(snapshot['insertion_binding'])
    for stored in witnesses:
        a, b = stored['event']['byte_range']
        if not start <= a < b <= end:
            raise PdfError('empty/style witness outside source output island')


def record_identity(state, sid):
    slot = state['slots'][sid]
    record = slot.get('source_output')
    if (not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != 1
            or record.get('state') not in ('uninitialized', 'owned')):
        raise PdfError('missing or unsupported source output record')
    fields = {'version', 'state', 'marker_id', 'created_from'} | ({'current'} if record['state'] == 'owned' else set())
    created = record.get('created_from')
    if (set(record) != fields or not isinstance(created, dict)
            or set(created) != {'pdf_sha256', 'paragraph_snapshot_sha256'}
            or not all(_hex(v) for v in created.values())
            or created['paragraph_snapshot_sha256'] != slot['source_snapshot_sha256']
            or record['marker_id'] != identity(state['flow']['id'], sid, created)):
        raise PdfError('source output creation identity mismatch')
    if record['state'] == 'uninitialized':
        if (state['allocation_provenance'] != 'observed-source-uncomposed'
                or created['pdf_sha256'] != state['pdf_sha256']
                or created['paragraph_snapshot_sha256'] != slot['binding']['paragraph']['snapshot_sha256']):
            raise PdfError('source output cannot return to uninitialized')
    else:
        current = record['current']
        if (not isinstance(current, dict)
                or set(current) != {'program_sha256', 'range', 'block_sha256', 'entry_context_sha256'}
                or not all(_hex(current[k]) for k in ('program_sha256', 'block_sha256', 'entry_context_sha256'))
                or not isinstance(current['range'], list) or len(current['range']) != 2
                or not all(type(v) is int for v in current['range'])
                or not 0 <= current['range'][0] < current['range'][1]):
            raise PdfError('invalid source output current witness')
    return record


def validate(source, state):
    groups = {}
    for sid, slot in state['slots'].items():
        if slot.get('destination_id') is not None:
            if 'source_output' in slot:
                raise PdfError('continuation slot cannot own source output')
            continue
        record = record_identity(state, sid)
        page = state['regions'][slot['region_id']]['page']
        groups.setdefault(page, []).append((slot, record))
    # On each source page, an unclaimed marker never grants ownership.
    for page in groups:
        content = ContentPage(source, page)
        try:
            spans = inventory(content.streams[-content.page.xref])
            owned = [(s, r) for s, r in groups.get(page, []) if r['state'] == 'owned']
            if set(spans) != {r['marker_id'] for _, r in owned} or len(owned) != len(spans):
                raise PdfError('source output marker inventory differs from owned slots')
            for slot, record in owned:
                span = spans[record['marker_id']]
                if record['current'] != witness(content, record, span):
                    raise PdfError('source output program/block/range/context witness mismatch')
                containment(content, slot['binding']['paragraph'], span)
        finally:
            content.close()


def owned_body(content, record, snapshot):
    spans = inventory(content.streams[-content.page.xref])
    span = spans.get(record['marker_id'])
    if span is None or record['state'] != 'owned' or record['current'] != witness(content, record, span):
        raise PdfError('source output ownership is not proven for this revision')
    containment(content, snapshot, span)
    return span[1:3]


def rebind(content, record, expected_context):
    updated = deepcopy(record)
    span = inventory(content.streams[-content.page.xref]).get(record['marker_id'])
    if span is None:
        raise PdfError('source output marker missing after transaction')
    current = witness(content, record, span)
    if current['entry_context_sha256'] != expected_context:
        raise PdfError('source output entry context changed during transaction')
    updated.update(state='owned', current=current)
    return updated
