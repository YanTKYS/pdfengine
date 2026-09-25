"""Several confirmed continuation destinations on one existing page.

Every destination keeps the page-entry authority (before the page program,
PDF initial graphics state) and its own identity. Their generated blocks form
an ordered page-entry chain whose order is the caller-confirmed
``page_entry_order``: never activation, creation or dictionary order.
"""
from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from evaluations.attributed.evaluate import font_mapping_audit
from evaluations.continuation.resources import inventory
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
import pdfeditor.continuation as destinations
from pdfeditor.continuation import confirm_continuation_destination, markers, slot_id
from pdfeditor.document_flow import _reseal
from pdfeditor.editable import open_editable, write_editable
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import _contract, confirm_shared_flow, edit_shared_flow, open_shared_flow, plan_shared_flow
from pdfeditor.story_flow import confirm_story
from test_attributed import source_pdf


LONG = 'One two three four five six seven eight nine ten eleven twelve thirteen fourteen.'
LONGER = LONG + ' Fifteen sixteen seventeen eighteen nineteen twenty.'
SHORT = 'One two three four five six seven eight.'
# Page 2 is 320 x 260 and holds "OTHER PAGE" near the top. Two reviewed empty
# areas below it, separated by a gap, each holding three 22pt lines. R1 on
# page 1 holds one line: SHORT and LONG end in DA, LONGER reaches DB.
AREAS = {'DA': dict(page=2, bounds=[18, 90, 173, 160], x=20, width=150, first_baseline=110),
         'DB': dict(page=2, bounds=[18, 170, 173, 250], x=20, width=150, first_baseline=190)}


def _story(source, font, pid, p, page, baseline, alignment):
    return confirm_story(source, {'original': dict(page=page, bounds=[18, 40, 173, 78], paragraph=p, paint_relations=[],
        layout=dict(x=20, baseline=baseline, width=150, max_bottom=78, min_line_height=22, first_line_indent=0))},
        paragraph_id=pid, chain=['original'], protected_regions={}, paragraph_layout=alignment,
        styles={'body': dict(provider=dict(path=str(font)), provider_relation='substituted')},
        style_assignments={'original': {'s0': 'body'}}, typing_style_id='body')


def flow(tmp_path, *, orders=None, owners=None, two=False, spacing=False, mode='left', areas=None):
    """Paragraph A on page 1 (and B on page 2 when ``two``); confirmed areas on page 2.

    ``orders`` maps area IDs to their confirmed page-entry order, ``owners``
    to the paragraph each area continues. Flow order: A's source, A's areas,
    then B's source and B's areas.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    areas = deepcopy(areas or AREAS)
    orders = orders or {'DA': 10, 'DB': 20}
    owners = owners or ({'DA': 'A', 'DB': 'B'} if two else {r: 'A' for r in areas})
    source = source_pdf(tmp_path, b'BT /Regular 12 Tf ' + (b'1 Tc 2 Ts ' if spacing else b'') + b'20 200 Td (ABCD) Tj ET')
    font = tmp_path / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    p = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(4)), explicit_width=150))
    if spacing:
        out, side = tmp_path / 'confirmed.pdf', tmp_path / 'confirmed.json'
        write_editable(source, out, side, p, [], fonts={'s0': dict(path=str(font))},
            paragraph_style={'s0': dict(tracking=1, baseline_shift=-2)}, min_line_height=22, max_bottom=78)
        source = out
        p = open_editable(out, side)['state']['paragraph']
    alignment = dict(alignment=mode, **({'justify_policy': 'word'} if mode == 'justify' else {}))
    stories = {'A': _story(source, font, 'A', p, 1, 60, alignment)}
    regions = {'R1': dict(page=1, bounds=[18, 40, 173, 78], x=20, width=150, first_baseline=60)}
    regions.update({r: v for r, v in areas.items() if owners[r] == 'A'})
    mapping = {'A': {'original': 'R1'}}
    if two:
        q = inspect_paragraph(source, make_selection(source, 2, glyph_ids=list(range(10)), explicit_width=150))
        stories['B'] = _story(source, font, 'B', q, 2, 60, alignment)
        regions['R3'] = dict(page=2, bounds=[18, 40, 173, 78], x=20, width=150, first_baseline=60)
        regions.update({r: v for r, v in areas.items() if owners[r] == 'B'})
        mapping['B'] = {'original': 'R3'}
    confirmed = {}
    for r in areas:
        d = confirm_continuation_destination(source, destination_id='dest-' + r, paragraph_id=owners[r], region_id=r,
            page=2, bounds=areas[r]['bounds'], insertion='before-page-program',
            graphics_state='isolated-pdf-initial-state', page_entry_order=orders[r])
        confirmed[d['destination_id']] = d
    policies = {pid: dict(min_line_height=22, first_line_indent=5, keep_together=False,
        break_before='next-region' if pid == 'B' else 'auto', break_after='auto',
        empty=dict(kind='reserve-line', ascent=10, descent=3)) for pid in stories}
    state = confirm_shared_flow(source, stories, flow_id='flow', paragraph_order=list(stories), regions=regions,
        region_order=list(regions), slot_regions=mapping, paragraph_policies=policies,
        follows=[dict(before='A', after='B', minimum_baseline_gap=24, region_start='reset-to-region-baseline')] if two else [],
        protected_regions={}, continuation_destinations=confirmed)
    return source, state


def change(state, **texts):
    return {pid: dict(edits=[dict(start=0, end=len(state['paragraphs'][pid]['logical']['text']), text=text, style_id='body')])
            for pid, text in texts.items()}


def save(tmp_path, source, state, changes, name):
    out, side = tmp_path / (name + '.pdf'), tmp_path / (name + '.json')
    preview = plan_shared_flow(source, state, changes)
    report = edit_shared_flow(source, state, out, side, changes)
    assert preview == report['plan'] and report['saves'] == 1
    opened = open_shared_flow(out, side)
    assert opened['status'] == 'restored', opened
    assert opened['state']['contract_sha256'] == state['contract_sha256']
    for step in report['steps']:
        font_mapping_audit(out, step['report'])
    return out, opened['state'], report


def program(path, page=2):
    return PdfReader(path).pages[page - 1].get_contents().get_data()


def chain_order(path, state):
    """Slot IDs of the generated blocks in page-program order, verified from bytes and bindings."""
    data = program(path)
    found = []
    for ident, d in state['continuation_destinations'].items():
        binding = state['destination_bindings'][ident]
        begin, end = markers(d)
        if 'end' not in binding:
            assert begin not in data and end not in data
            continue
        # Each binding names its own marker pair, and the block is exactly that range.
        assert data.count(begin) == data.count(end) == 1
        assert data[binding['start']:binding['end']].startswith(begin)
        assert data[binding['start']:binding['end']].endswith(end)
        assert binding['page_entry_order'] == d['page_entry_order']
        found.append((binding['start'], binding['end'], slot_id(d)))
    found.sort()
    # Contiguous from the page entry, ending where the original program begins.
    assert [a for a, _, _ in found] == [0] + [b for _, b, _ in found[:-1]]
    assert data[found[-1][1] if found else 0:].lstrip().startswith(b'BT') or not found
    return [sid for _, _, sid in found]


def confirmed_order(state):
    return [slot_id(d) for d in sorted(state['continuation_destinations'].values(), key=lambda d: d['page_entry_order'])]


def ids(state):
    return {r: slot_id(state['continuation_destinations']['dest-' + r]) for r in ('DA', 'DB')}


@pytest.mark.parametrize('orders', [{'DA': 10, 'DB': 20}, {'DA': 20, 'DB': 10}])
def test_simultaneous_first_creation_follows_confirmed_order(tmp_path, orders):
    source, state = flow(tmp_path, orders=orders)
    out, state, report = save(tmp_path, source, state, change(state, A=LONGER), 'both')
    slots = ids(state)
    assert set(report['plan']['new_slots']) == set(slots.values())
    assert chain_order(out, state) == confirmed_order(state)
    # Each block has its own creation mutation: owner, order and contract.
    records = {r['owner']: r for r in report['mutation_map'][2]['mutations'] if r['kind'] == 'confirmed-continuation-create'}
    assert set(records) == set(slots.values())
    for r, sid in slots.items():
        d = state['continuation_destinations']['dest-' + r]
        creation = state['slots'][sid]['creation_binding']
        assert creation['mutation'] == records[sid]
        assert creation['mutation']['insertion_order'] == orders[r] and creation['mutation']['start'] == 0
        assert creation['destination_contract_sha256'] == destinations.digest(d)
    # The page program ends with the untouched original program.
    assert program(out).endswith(program(source)) or program(source) in program(out)


@pytest.mark.parametrize('orders', [{'DA': 10, 'DB': 20}, {'DA': 20, 'DB': 10}])
def test_sequential_and_reverse_activation_converge_to_confirmed_order(tmp_path, orders):
    """DA activates first by flow order. With DA=20, DB=10 the later block enters in front."""
    source, state = flow(tmp_path, orders=orders)
    slots = ids(state)
    out, state, report = save(tmp_path, source, state, change(state, A=LONG), 'first')
    assert set(report['plan']['new_slots']) == {slots['DA']}
    assert chain_order(out, state) == [slots['DA']]
    first = deepcopy(state['destination_bindings'])
    out2, state2, report = save(tmp_path, out, state, change(state, A=LONGER), 'second')
    assert set(report['plan']['new_slots']) == {slots['DB']}
    assert chain_order(out2, state2) == confirmed_order(state2)
    # The new block entered at the boundary the chain prescribes, not at offset 0 blindly.
    created = state2['slots'][slots['DB']]['creation_binding']['mutation']
    assert created['start'] == (first['dest-DA']['end'] if orders['DB'] > orders['DA'] else 0)
    assert state2['slots'][slots['DA']]['creation_binding'] == state['slots'][slots['DA']]['creation_binding']
    # The same final order as simultaneous creation in one transaction.
    other = tmp_path / 'simultaneous'
    other.mkdir()
    s, st = flow(other, orders=orders)
    o, st, _ = save(other, s, st, change(st, A=LONGER), 'both')
    assert chain_order(o, st) == chain_order(out2, state2)


@pytest.mark.parametrize('mode', ['left', 'justify'])
def test_one_paragraph_continues_through_two_destinations(tmp_path, mode):
    source, state = flow(tmp_path, spacing=True, mode=mode)
    out, state, report = save(tmp_path, source, state, change(state, A=LONGER), 'grow')
    slots = ids(state)
    logical = state['paragraphs']['A']['logical']
    assert logical['text'] == LONGER and not logical['boundaries']
    # One style span over the whole paragraph, not one per region.
    assert [(x['start'], x['end'], x['style_id']) for x in logical['style_spans']] == [(0, len(LONGER), 'body')]
    # One Unicode sequence, partitioned by slot ranges without inserted breaks.
    order = ['slot-0', slots['DA'], slots['DB']]
    ranges = [state['slots'][s]['range'] for s in order]
    assert ranges[0][0] == 0 and ranges[-1][1] == len(LONGER)
    assert all(a[1] == b[0] for a, b in zip(ranges, ranges[1:])) and all(a < b for a, b in ranges)
    assert ''.join(state['slots'][s]['binding']['paragraph']['text'] + LONGER[state['slots'][s]['render_end']:state['slots'][s]['range'][1]]
                   for s in order) == LONGER
    assert [(b['kind'], b['before'], b['after'], b['logical_break_inserted']) for b in state['physical_breaks']] == [
        ('page_break', 'slot-0', slots['DA'], False), ('container_break', slots['DA'], slots['DB'], False)]
    # First-line indent only on the paragraph's first visual line.
    assert state['slots']['slot-0']['binding']['layout']['first_line_indent'] == 5
    assert all(state['slots'][s]['binding']['layout']['first_line_indent'] == 0 for s in slots.values())
    for sid in slots.values():
        binding = state['slots'][sid]['binding']
        assert binding['logical_element']['alignment']['value'] == mode
        # Justify keeps the paragraph continuation policy across both regions.
        assert binding['physical_layout']['paragraph_continues'] == (sid == slots['DA'])
        for style in binding['paragraph']['styles']:
            if style['id'] in state['slots'][sid]['style_binding']['styles']:
                assert style['tracking'] == 1 and style['baseline_shift'] == -2
        spans = state['slots'][sid]['style_binding']['styles']
        assert set(spans.values()) == {'body'}
    # Allocation order equals flow order; paint order is the chain order.
    assert chain_order(out, state) == [slots['DA'], slots['DB']]
    glyphs = {s['slot_id']: s['report']['glyph_plan'] for s in report['steps']}
    assert all(g['cid'] is not None and g['glyph_id'] > 0 for sid in slots.values() for g in glyphs[sid])
    with pymupdf.open(out) as doc:
        text = doc[1].get_text()
    assert 'eleven' in text or 'fourteen' in text


def test_two_paragraphs_keep_separate_destinations_blocks_and_fonts(tmp_path):
    # Chain order deliberately differs from flow order: B's block paints first.
    source, state = flow(tmp_path, two=True, orders={'DA': 20, 'DB': 10})
    slots = ids(state)
    out, state, report = save(tmp_path, source, state, change(state, A=LONG, B='OTHER ' + LONG), 'both')
    assert set(report['plan']['new_slots']) == set(slots.values())
    assert chain_order(out, state) == [slots['DB'], slots['DA']]
    assert state['slots'][slots['DA']]['paragraph_id'] == 'A' and state['slots'][slots['DB']]['paragraph_id'] == 'B'
    fonts = state['generated_fonts']['2']
    owners = {a: r['slot_id'] for a, r in fonts.items()}
    assert set(owners.values()) == {'slot-1', slots['DA'], slots['DB']}
    b_block = {k: state['slots'][slots['DB']][k] for k in ('creation_binding', 'destination_id', 'paragraph_id', 'region_id')}
    b_fonts = {a: r for a, r in fonts.items() if r['slot_id'] == slots['DB']}
    # Re-edit and shorten A (it still continues into DA): B keeps its identity and fonts.
    shorter = LONG.replace('fourteen', 'XIV').replace('eleven ', '')
    out2, state2, report = save(tmp_path, out, state, change(state, A=shorter), 'a-edit')
    assert not report['plan']['new_slots']
    assert chain_order(out2, state2) == [slots['DB'], slots['DA']]
    assert {k: state2['slots'][slots['DB']][k] for k in b_block} == b_block
    fonts2 = state2['generated_fonts']['2']
    assert {a: r for a, r in fonts2.items() if r['slot_id'] == slots['DB']} == b_fonts
    assert {a: r['slot_id'] for a, r in fonts2.items()} == owners
    outcome = report['generated_font_outcome']['2']
    assert all(outcome[a] == 'reused' for a in b_fonts)
    # Each block's text belongs to its own slot only.
    content = ContentPage(out2, 2)
    try:
        for r, sid in slots.items():
            binding = state2['destination_bindings']['dest-' + r]
            owned = set(state2['slots'][sid]['binding']['paragraph']['selection']['glyph_ids'])
            inside = {i for e in content.events if binding['start'] <= e.operator.start < binding['end']
                      for c in e.chars for i in c.source_orders}
            assert inside == owned and owned
            names = {e.state.font.name for e in content.events if binding['start'] <= e.operator.start < binding['end']}
            assert names <= {a for a, o in owners.items() if o == sid}
    finally:
        content.close()
    # B becomes dormant and regrows into the same slot and block; A is untouched.
    out3, state3, report = save(tmp_path, out2, state2, change(state2, B='OTHER'), 'b-short')
    assert state3['slots'][slots['DB']]['occupancy'] is None and state3['slots'][slots['DA']]['occupancy'] is not None
    assert chain_order(out3, state3) == [slots['DB'], slots['DA']]
    out4, state4, report = save(tmp_path, out3, state3, change(state3, B='OTHER ' + LONG), 'b-regrow')
    assert not report['plan']['new_slots'] and state4['slots'][slots['DB']]['occupancy'] is not None
    assert state4['slots'][slots['DB']]['creation_binding'] == b_block['creation_binding']
    assert {a: r['slot_id'] for a, r in state4['generated_fonts']['2'].items()} == owners


def _counts(path, state, source):
    inv = inventory(path, records=state['generated_fonts'], source=source)
    assert not [e for page in inv['pages'].values() for e in page if e['kind'] == 'unknown']
    return dict(entries={p: len(v) for p, v in inv['pages'].items()}, type0=inv['type0_fonts'],
                owned=inv['owned_font_roots'], objects=inv['owned_font_graph_objects'])


def test_lifecycle_shorten_regrow_reopen_and_repeated_noops(tmp_path):
    source, state = flow(tmp_path, spacing=True)
    original = source
    slots = ids(state)
    creation, counts, previous = {}, {}, None
    for name, text in [('grow', LONGER), ('second', LONGER.replace('twelve', 'TWELVE')), ('shorten', SHORT),
                       ('regrow', LONGER), ('noop1', None), ('noop2', None), ('noop3', None)]:
        out, updated, report = save(tmp_path, source, state, {} if text is None else change(state, A=text), name)
        assert set(updated['slots']) == {'slot-0', *slots.values()}
        assert set(report['plan']['new_slots']) == (set(slots.values()) if name == 'grow' else set())
        for sid in slots.values():
            creation.setdefault(sid, updated['slots'][sid]['creation_binding'])
            assert updated['slots'][sid]['creation_binding'] == creation[sid]
        # The dormant block keeps its place in the chain; nothing is re-created.
        assert chain_order(out, updated) == [slots['DA'], slots['DB']]
        dormant = updated['slots'][slots['DB']]['occupancy'] is None
        assert dormant == (name == 'shorten')
        assert updated['slots'][slots['DA']]['occupancy'] is not None
        if dormant:
            assert not updated['slots'][slots['DB']]['binding']['paragraph']['text']
            with pymupdf.open(out) as doc:
                assert not doc[1].get_text(clip=pymupdf.Rect(AREAS['DB']['bounds'])).strip()
        counts[name] = _counts(out, updated, original)
        # One generated alias per generated slot on page 2, never shared or swapped.
        page_fonts = updated['generated_fonts']['2']
        assert sorted(r['slot_id'] for r in page_fonts.values()) == sorted(slots.values())
        aliases = aliases if name != 'grow' else {a: r['slot_id'] for a, r in page_fonts.items()}
        assert {a: r['slot_id'] for a, r in page_fonts.items()} == aliases
        if text is None:
            with pymupdf.open(source) as a, pymupdf.open(out) as b:
                assert all(a[i].get_pixmap(dpi=144).samples == b[i].get_pixmap(dpi=144).samples for i in range(len(a)))
            assert updated['generated_fonts'] == state['generated_fonts']
            assert all(set(v.values()) == {'reused'} for v in report['generated_font_outcome'].values())
            for sid, slot in state['slots'].items():
                assert (slot['range'], slot['occupancy']) == (updated['slots'][sid]['range'], updated['slots'][sid]['occupancy'])
            fields = ('unicode', 'glyph_id', 'origin', 'size', 'advance', 'code', 'cid', 'nominal_pdf_width')
            plans = lambda r: {s['slot_id']: [{k: g[k] for k in fields} for g in s['report']['glyph_plan']] for s in r['steps']}
            assert plans(previous) == plans(report)
            data = program(out)
            for d in updated['continuation_destinations'].values():
                assert all(data.count(m) == 1 for m in markers(d))
        previous = report
        source, state = out, updated
    assert counts['noop1'] == counts['noop2'] == counts['noop3'] == counts['regrow']


THIRDS = {'D1': dict(page=2, bounds=[18, 90, 173, 138], x=20, width=150, first_baseline=110),
          'D2': dict(page=2, bounds=[18, 142, 173, 190], x=20, width=150, first_baseline=162),
          'D3': dict(page=2, bounds=[18, 194, 173, 242], x=20, width=150, first_baseline=214)}
LONGEST = LONGER + ' Twenty one, two.'


@pytest.mark.parametrize('orders', [{'D1': 10, 'D2': 20, 'D3': 30}, {'D1': 20, 'D2': 10, 'D3': 30}])
def test_several_new_blocks_enter_an_existing_chain_in_one_transaction(tmp_path, orders):
    source, state = flow(tmp_path, orders=orders, areas=THIRDS)
    sid = {r: slot_id(state['continuation_destinations']['dest-' + r]) for r in THIRDS}
    out, state, report = save(tmp_path, source, state, change(state, A=SHORT), 'first')
    assert set(report['plan']['new_slots']) == {sid['D1']}
    existing = state['destination_bindings']['dest-D1']
    out, state, report = save(tmp_path, out, state, change(state, A=LONGEST), 'rest')
    assert set(report['plan']['new_slots']) == {sid['D2'], sid['D3']}
    assert chain_order(out, state) == confirmed_order(state)
    created = {r: state['slots'][sid[r]]['creation_binding']['mutation'] for r in ('D2', 'D3')}
    # Each new block names the chain boundary it entered at; two may share one.
    for r, mutation in created.items():
        assert mutation['start'] == (existing['end'] if orders[r] > orders['D1'] else 0)
        assert mutation['insertion_order'] == orders[r] and mutation['owner'] == sid[r]
    assert all(state['slots'][sid[r]]['occupancy'] is not None for r in THIRDS)


def _flow_with_blocks(tmp_path):
    source, state = flow(tmp_path)
    out, state, _ = save(tmp_path, source, state, change(state, A=LONGER), 'both')
    return out, state


def test_order_is_part_of_the_confirmation(tmp_path):
    source, state = flow(tmp_path)
    # Before any block exists: the witnessed order is part of each binding.
    bad = deepcopy(state)
    bad['continuation_destinations']['dest-DB']['page_entry_order'] = 5
    assert open_shared_flow(source, _reseal(bad))['status'] == 'needs_confirmation'
    bad['contract_sha256'] = _contract(bad)
    assert open_shared_flow(source, _reseal(bad))['status'] == 'needs_confirmation'
    out, state = _flow_with_blocks(tmp_path)
    for orders in ({'dest-DB': 5}, {'dest-DA': 20, 'dest-DB': 10}, {'dest-DA': 15}):
        bad = deepcopy(state)
        for ident, value in orders.items():
            bad['continuation_destinations'][ident]['page_entry_order'] = value
        # Only the sidecar changed: the confirmed contract no longer matches.
        assert open_shared_flow(out, _reseal(bad))['status'] == 'needs_confirmation'
        # Even with a recomputed contract and bindings, the creation provenance,
        # recorded insertion order and physical chain disagree.
        bad['contract_sha256'] = _contract(bad)
        for ident, value in orders.items():
            bad['destination_bindings'][ident]['page_entry_order'] = value
        assert open_shared_flow(out, _reseal(bad))['status'] == 'needs_confirmation'
    assert open_shared_flow(out, tmp_path / 'both.json')['status'] == 'restored'


def _tampered(path, data, name):
    writer = PdfWriter(clone_from=PdfReader(path))
    stream = DecodedStreamObject()
    stream.set_data(data)
    writer.pages[1][NameObject('/Contents')] = writer._add_object(stream)
    target = path.parent / name
    writer.write(target)
    return target


def test_marker_and_chain_tampering_is_refused(tmp_path):
    out, state = _flow_with_blocks(tmp_path)
    data = program(out)
    a, b = (state['continuation_destinations'][i] for i in ('dest-DA', 'dest-DB'))
    ba, bb = (state['destination_bindings'][i] for i in ('dest-DA', 'dest-DB'))
    block_a, block_b, rest = data[:ba['end']], data[bb['start']:bb['end']], data[bb['end']:]
    (a0, a1), (b0, b1) = markers(a), markers(b)
    swap = lambda value: value.replace(a0, b'\0').replace(b0, a0).replace(b'\0', b0).replace(a1, b'\1').replace(b1, a1).replace(b'\1', b1)
    variants = {
        'swapped-markers': swap(block_a + block_b) + rest,
        'reordered-blocks': block_b + block_a + rest,
        'duplicated-block': block_a + block_a + block_b + rest,
        'missing-begin': block_a + block_b.replace(b0, b'') + rest,
        'missing-end': block_a + block_b.replace(b1, b'') + rest,
        'foreign-bytes-between': block_a + b'0 0 1 rg\n' + block_b + rest,
        'foreign-operator-inside': block_a.replace(b'q BT ', b'q 1 0 0 1 5 5 cm BT ', 1) + block_b + rest,
        'block-after-original': block_a + rest + block_b,
    }
    generated = {'dest-DA', 'dest-DB'}
    for name, value in variants.items():
        tampered = _tampered(out, value, name + '.pdf')
        content = ContentPage(tampered, 2)
        try:
            with pytest.raises(PdfError):
                destinations.page_witness(content, [a, b], generated)
        finally:
            content.close()
        with pytest.raises(PdfError):
            destinations.validate_destinations(tampered, state)
        assert open_shared_flow(tampered, tmp_path / 'both.json')['status'] == 'needs_confirmation'
    # The untouched bytes in a fresh object still verify: the checks are about content.
    rewritten = _tampered(out, data, 'same.pdf')
    destinations.validate_destinations(rewritten, state)
    # A planned boundary is re-verified against the bytes, not trusted.
    content = ContentPage(out, 2)
    try:
        with pytest.raises(PdfError, match='boundary'):
            destinations.verify_entry(content, b, dict(order=20, offset=0, preceding=[slot_id(a)], following=[]))
        with pytest.raises(PdfError, match='order'):
            destinations.verify_entry(content, b, dict(order=10, offset=ba['end'], preceding=[slot_id(a)], following=[]))
        with pytest.raises(PdfError):
            destinations.verify_entry(content, b, dict(order=20, offset=0, preceding=[], following=[slot_id(b)]))
        assert destinations.verify_entry(content, b, dict(order=20, offset=ba['end'], preceding=[slot_id(a)], following=[])) == ba['end']
    finally:
        content.close()


def test_invalid_destination_sets_are_refused(tmp_path):
    overlapping = deepcopy(AREAS)
    overlapping['DB']['bounds'] = [18, 150, 173, 220]
    overlapping['DB']['first_baseline'] = 170
    with pytest.raises(PdfError, match='disjoint|intersect'):
        flow(tmp_path / 'overlap', areas=overlapping)
    for orders in ({'DA': 10, 'DB': 10}, {'DA': 10, 'DB': None}, {'DA': None, 'DB': None}):
        with pytest.raises(PdfError, match='page-entry order'):
            flow(tmp_path / ('order-' + '-'.join(map(str, orders.values()))), orders=orders)
    source = source_pdf(tmp_path)
    for value in (-1, 1.5, '10', True):
        with pytest.raises(PdfError, match='page-entry order'):
            confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R', page=2,
                bounds=[18, 90, 173, 150], insertion='before-page-program',
                graphics_state='isolated-pdf-initial-state', page_entry_order=value)
    # Fixed paint and foreign paragraph text are not empty space ("OTHER PAGE" on page 2).
    with pytest.raises(PdfError, match='paint|obstacle|glyph|intersect'):
        confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R', page=2,
            bounds=[18, 40, 173, 100], insertion='before-page-program',
            graphics_state='isolated-pdf-initial-state', page_entry_order=1)
    # Another destination's generated glyphs are obstacles too.
    out, state = _flow_with_blocks(tmp_path / 'blocks')
    with pytest.raises(PdfError):
        confirm_continuation_destination(out, destination_id='late', paragraph_id='A', region_id='R', page=2,
            bounds=AREAS['DA']['bounds'], insertion='before-page-program',
            graphics_state='isolated-pdf-initial-state', page_entry_order=30)
    # A protected region inside one destination refuses the whole flow edit.
    source, state = flow(tmp_path / 'protected')
    state['pages']['2']['protected_regions'] = [dict(role='fixed', bounds=[20, 180, 60, 200], provenance='explicitly_confirmed')]
    state['contract_sha256'] = _contract(state)
    state = _reseal(state)
    with pytest.raises(PdfError):
        edit_shared_flow(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', change(state, A=LONGER))
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()


@pytest.mark.parametrize('phase', ['second-block', 'commit'])
def test_late_failure_after_second_block_publishes_nothing(tmp_path, monkeypatch, phase):
    import pdfeditor.shared_flow as shared
    source, state = flow(tmp_path)
    real, calls = destinations.rebind, []

    def rebind(*args, **kwargs):
        real(*args, **kwargs)
        calls.append(args[1])
        if len(calls) == 2:
            raise PdfError('injected late failure')
    if phase == 'second-block':
        monkeypatch.setattr(shared.destinations, 'rebind', rebind)
    else:
        monkeypatch.setattr(shared.Transaction, '_verify', lambda *a, **k: (_ for _ in ()).throw(PdfError('injected late failure')))
    with pytest.raises(PdfError, match='injected'):
        edit_shared_flow(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', change(state, A=LONGER))
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()
    if phase == 'second-block':
        assert calls == [slot_id(state['continuation_destinations'][i]) for i in ('dest-DA', 'dest-DB')]


def test_single_destination_keeps_the_original_page_entry_form(tmp_path):
    from test_continuation import LONG as SINGLE, change as single_change, prepared
    source, state = prepared(tmp_path)
    d = state['continuation_destinations']['empty']
    assert 'page_entry_order' not in d and 'page_entry_order' not in state['destination_bindings']['empty']
    out, side = tmp_path / 'single.pdf', tmp_path / 'single.json'
    edit_shared_flow(source, state, out, side, single_change(state, SINGLE))
    state = open_shared_flow(out, side)['state']
    mutation = state['slots'][slot_id(d)]['creation_binding']['mutation']
    assert mutation['start'] == mutation['end'] == 0 and 'insertion_order' not in mutation
    assert set(state['destination_bindings']['empty']) == {'program_sha256', 'start', 'end', 'block_sha256'}
