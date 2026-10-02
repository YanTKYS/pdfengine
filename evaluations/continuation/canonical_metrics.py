"""Measurements of saved continuation evidence, not a second engine contract.

Read existing bindings, marker helpers, tokenizer and mutation provenance.
All sizes refer to decoded programs; no ownership is inferred from an alias.
"""
from collections import Counter, defaultdict
import hashlib

from pypdf import PdfReader

from pdfeditor.content_stream import operators
from pdfeditor.continuation import markers, slot_id
from pdfeditor.operator_nesting import audit
from evaluations.continuation.resources import generated_block_bytes


def sha(data):
    return hashlib.sha256(data).hexdigest()


def program(pdf, page):
    return PdfReader(pdf).pages[page - 1].get_contents().get_data()


def block(pdf, state, ident):
    destination = state['continuation_destinations'][ident]
    binding = state['destination_bindings'][ident]
    data = program(pdf, destination['page'])
    begin, end = markers(destination)
    value = data[binding['start']:binding['end']]
    if not (data.count(begin) == data.count(end) == 1 and value.startswith(begin)
            and value.endswith(end) and sha(value) == binding['block_sha256']
            and len(value) == generated_block_bytes(pdf, destination['page'], destination)):
        raise ValueError('saved binding and marked block disagree')
    return value


def census(value):
    """Count text-show operators with nonempty strings, including TJ arrays.

    Generated blocks have fill-only text per the engine's verified binding.
    This is not a general PDF painting/visibility classifier.
    """
    ops = list(operators(value))
    counts = Counter(op.name for op in ops)

    def nonempty(value):
        if isinstance(value, bytes):
            return bool(value)
        if isinstance(value, (list, tuple)):
            return any(nonempty(item) for item in value)
        return False

    nesting = audit(value)
    if nesting['violations']:
        raise ValueError('generated block operator nesting violation')
    return dict(byte_length=len(value), sha256=sha(value), operator_count=len(ops),
                Tf=counts['Tf'], Tm=counts['Tm'], Tj=counts['Tj'], TJ=counts['TJ'],
                text_show_count=counts['Tj'] + counts['TJ'],
                painting_text_show_count=sum(op.name in ('Tj', 'TJ') and nonempty(op.args) for op in ops),
                text_objects=nesting['text_objects'], operator_nesting_violations=0,
                selected_font_aliases=sorted({str(op.args[0]) for op in ops if op.name == 'Tf'}))


def measure(pdf, state, ident):
    destination = state['continuation_destinations'][ident]
    sid = slot_id(destination)
    binding = state['destination_bindings'][ident]
    data = program(pdf, destination['page'])
    begin, end = markers(destination)
    result = census(block(pdf, state, ident))
    owned = sorted(a for a, record in state['generated_fonts'][str(destination['page'])].items()
                   if record['slot_id'] == sid)
    if not set(result['selected_font_aliases']) <= set(owned):
        raise ValueError('block selected a font outside its recorded ownership')
    paragraph = state['slots'][sid]['binding']['paragraph']
    return dict(destination_id=ident, slot_id=sid, page=destination['page'],
                start=binding['start'], end=binding['end'], marker_begin_count=data.count(begin),
                marker_end_count=data.count(end), marker_pair_sha256=[sha(begin), sha(end)],
                generated_font_aliases=owned, dormant=state['slots'][sid]['occupancy'] is None,
                insertion_binding_present=bool(paragraph.get('insertion_binding')),
                style_slot_bindings_present=bool(paragraph.get('style_slot_bindings')),
                typing_style_id=paragraph.get('typing_style_id'),
                style_witness_ids=sorted(paragraph.get('style_slot_bindings', {})),
                prefix_sha256=sha(data[:binding['start']]), suffix_sha256=sha(data[binding['end']:]),
                **result)


def mutation_breakdown(before, after, state, report):
    """Attribute byte deltas to explicit slot owners, reconciling actual bytes.

    Source slots have no marker-delimited whole block. Their *deltas*, not an
    invented absolute span, are measured from the transaction's byte edits.
    """
    rows = {}
    for page in range(1, state['page_count'] + 1):
        old, new = program(before, page), program(after, page)
        changes = report['mutation_map'].get(str(page), report['mutation_map'].get(page, {}))
        mutations = changes.get('mutations', [])
        totals = dict(generated_continuation=0, source_slot=0, other=0)
        owners = defaultdict(lambda: dict(delta_bytes=0, mutations=0, kinds=set()))
        for mutation in mutations:
            owner = mutation['owner']
            slot = state['slots'].get(owner)
            kind = ('generated_continuation' if slot and slot.get('destination_id') else
                    'source_slot' if slot is not None else 'other')
            delta = mutation['length'] - (mutation['end'] - mutation['start'])
            totals[kind] += delta
            owners[str(owner)]['delta_bytes'] += delta
            owners[str(owner)]['mutations'] += 1
            owners[str(owner)]['kinds'].add(mutation['kind'])
        actual = len(new) - len(old)
        if sum(totals.values()) != actual:
            raise ValueError(f'page {page}: mutation delta does not explain decoded program growth')
        for values in owners.values():
            values['kinds'] = sorted(values['kinds'])
        rows[str(page)] = dict(before_bytes=len(old), after_bytes=len(new), delta_bytes=actual,
                              program_bytes_equal=old == new,
                              before_operators=sum(1 for _ in operators(old)),
                              after_operators=sum(1 for _ in operators(new)),
                              **totals, owners=dict(owners))
    return rows
