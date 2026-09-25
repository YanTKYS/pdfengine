"""A confirmed page-program boundary as a continuation insertion authority.

The caller selects one safe candidate from inspect_continuation_boundaries.
The generated block then paints after all paint of the confirmed prefix and
before all paint of the confirmed suffix, through every later edit.
"""
from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject, NumberObject

from pdfeditor import continuation as destinations
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import (BOUNDARY, BOUNDARY_STATE, CREATE, Z_ORDER, confirm_continuation_destination,
                                    inspect_continuation_boundaries, markers, slot_id)
from pdfeditor.document_flow import _reseal
from pdfeditor.mutation import Mutation, MutationProgram
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow
from pdfeditor.story_flow import confirm_story
from test_continuation import LONG, change, saved

LONGER = LONG + ' Fifteen sixteen seventeen eighteen nineteen twenty.'
SOURCE_PAGE = b'BT /Regular 12 Tf 20 200 Td (ABCD) Tj ET'
# Page 2: paint A (a blue rectangle), then the boundary, then paint B (text,
# still blue). Line width and fill color at the boundary are not the page-entry
# defaults: the block sets its own fill and restores everything. The two
# comment lines are placeholders that tests replace, byte for byte, with
# operators that change the state at the same boundary offset while the
# operators on both sides of the boundary stay the same.
PREFIX_SLOT, SUFFIX_SLOT = b'%' + b'A' * 30, b'%' + b'B' * 30
DESTINATION_PAGE = (PREFIX_SLOT + b'\n0.5 w 0 0 1 rg 200 200 60 30 re f\n'
                    + b'BT /Regular 12 Tf 200 40 Td (TAIL) Tj ET\n' + SUFFIX_SLOT)
PAINT_A, PAINT_B = b'200 200 60 30 re f', b'(TAIL) Tj'
REGION = dict(bounds=[18, 90, 173, 220], x=20, width=150, first_baseline=110)


def build(tmp_path, *programs, name='source.pdf'):
    """320 x 260 pt pages with Courier fonts and one ExtGState (/GS0, ca 0.5)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    writer, fonts = PdfWriter(), DictionaryObject()
    for alias, base in (('/Regular', '/Courier'), ('/Bold', '/Courier-Bold')):
        fonts[NameObject(alias)] = writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject(base), NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
            NameObject('/FirstChar'): NumberObject(32), NameObject('/LastChar'): NumberObject(126),
            NameObject('/Widths'): ArrayObject([NumberObject(600)] * 95)}))
    states = DictionaryObject({NameObject('/GS0'): writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/ExtGState'), NameObject('/ca'): FloatObject(0.5)}))})
    for program in programs:
        page = writer.add_blank_page(320, 260)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): fonts, NameObject('/ExtGState'): states})
        stream = DecodedStreamObject()
        stream.set_data(program)
        page[NameObject('/Contents')] = writer._add_object(stream)
    path = tmp_path / name
    writer.write(path)
    return path


def candidate(source, page, previous):
    """The caller's explicit choice: the one safe candidate right after ``previous``."""
    found = [c for c in inspect_continuation_boundaries(source, page)['candidates'] if c['previous']['operator'] == previous]
    assert len(found) == 1, found
    return found[0]


def program(path, page):
    return PdfReader(path).pages[page - 1].get_contents().get_data()


def _story(source, font, p):
    return confirm_story(source, {'original': dict(page=1, bounds=[18, 40, 173, 78], paragraph=p, paint_relations=[],
        layout=dict(x=20, baseline=60, width=150, max_bottom=78, min_line_height=22, first_line_indent=0))},
        paragraph_id='A', chain=['original'], protected_regions={}, paragraph_layout=dict(alignment='left'),
        styles={'body': dict(provider=dict(path=str(font)), provider_relation='substituted')},
        style_assignments={'original': {'s0': 'body'}}, typing_style_id='body')


def flow(tmp_path, pages=(SOURCE_PAGE, DESTINATION_PAGE), *, page=2, after='f', areas=None):
    """Paragraph A (page 1) continues into confirmed areas.

    ``areas`` maps region IDs to ('entry' | 'boundary', geometry); by default
    one boundary area on ``page`` right after the operator ``after``.
    """
    source = build(tmp_path, *pages)
    font = tmp_path / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    p = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(4)), explicit_width=150))
    areas = areas or {'R2': ('boundary', dict(REGION, page=page))}
    regions = {'R1': dict(page=1, bounds=[18, 40, 173, 78], x=20, width=150, first_baseline=60)}
    confirmed, chosen = {}, None
    for rid, (kind, geometry) in areas.items():
        regions[rid] = geometry
        if kind == 'boundary':
            chosen = candidate(source, geometry['page'], after)
            d = confirm_continuation_destination(source, destination_id='dest-' + rid, paragraph_id='A', region_id=rid,
                page=geometry['page'], bounds=geometry['bounds'], insertion=BOUNDARY, graphics_state=BOUNDARY_STATE,
                boundary=chosen['boundary_id'])
        else:
            d = confirm_continuation_destination(source, destination_id='dest-' + rid, paragraph_id='A', region_id=rid,
                page=geometry['page'], bounds=geometry['bounds'], insertion='before-page-program',
                graphics_state='isolated-pdf-initial-state')
        confirmed[d['destination_id']] = d
    state = confirm_shared_flow(source, {'A': _story(source, font, p)}, flow_id='flow', paragraph_order=['A'],
        regions=regions, region_order=list(regions), slot_regions={'A': {'original': 'R1'}},
        paragraph_policies={'A': dict(min_line_height=22, first_line_indent=5, keep_together=False, break_before='auto',
                                      break_after='auto', empty=dict(kind='reserve-line', ascent=10, descent=3))},
        follows=[], protected_regions={}, continuation_destinations=confirmed)
    return source, state, chosen


def split(path, state, ident):
    """prefix, block and suffix of a destination's page program, checked against its binding."""
    d = state['continuation_destinations'][ident]
    binding = state['destination_bindings'][ident]
    data = program(path, d['page'])
    start, end = binding['start'], binding['end']
    begin, stop = markers(d)
    assert 0 < start < end < len(data)
    assert data[start:start + len(begin)] == begin and data[end - len(stop):end] == stop
    assert binding['boundary']['offset'] == start
    return data[:start], data[start:end], data[end:]


def last_operator(data):
    return list(operators(data))[-1].name


def first_operator(data):
    return list(operators(data))[0].name


# -- A. candidate inspection ---------------------------------------------------

@pytest.mark.parametrize('data,after,reasons', [
    (b'0.5 w 1 0 0 rg 0 0 5 5 re f 0 g', b'0 0 5 5 re f', []),
    (b'BT /Regular 12 Tf 20 200 Td (A) Tj (B) Tj ET 0 g', b'(A) Tj', ['inside-text-object']),
    (b'q 0 0 5 5 re f Q 0 g', b'0 0 5 5 re f', ['inside-graphics-state-save']),
    (b'/P BMC 0 0 5 5 re f EMC 0 g', b'0 0 5 5 re f', ['inside-marked-content']),
    (b'BX 0 0 5 5 re f EX 0 g', b'0 0 5 5 re f', ['inside-compatibility-section']),
    (b'0 0 5 5 re f 0 g', b'0 0 5 5 re', ['pending-path']),
    (b'0 0 5 5 re W n 0 g', b'0 0 5 5 re W', ['pending-path', 'pending-clip']),
    (b'2 0 0 2 0 0 cm 0 0 5 5 re f 0 g', b'0 0 5 5 re f', ['nonidentity-ctm']),
    (b'0 0 320 260 re W n 0 0 5 5 re f 0 g', b'0 0 5 5 re f', ['active-clip']),
    (b'/GS0 gs 0 0 5 5 re f 0 g', b'0 0 5 5 re f', ['transparency', 'extgstate']),
    (b'/Perceptual ri 0 0 5 5 re f 0 g', b'0 0 5 5 re f', ['graphics-state-side-effect']),
    (b'3 Tr 0 0 5 5 re f 0 g', b'0 0 5 5 re f', ['text-rendering-mode']),
])
def test_only_safe_page_level_boundaries_are_candidates(tmp_path, data, after, reasons):
    source = build(tmp_path, data)
    offset = data.index(after) + len(after)
    found = inspect_continuation_boundaries(source, 1, include_refused=True)
    everything = {b['offset']: b for b in found['candidates'] + found['refused']}
    assert everything[offset]['reasons'] == reasons
    assert (offset in {c['offset'] for c in found['candidates']}) == (not reasons)
    # Never the page entry, never after the last operator.
    assert min(everything) > 0 and max(everything) < len(data)
    assert all(c['status'] == 'safe' and c['z_order']['semantics'] == Z_ORDER for c in found['candidates'])


# -- B. explicit confirmation --------------------------------------------------

def test_the_caller_confirms_one_listed_candidate_not_a_geometry(tmp_path):
    source = build(tmp_path, SOURCE_PAGE, DESTINATION_PAGE)
    chosen = candidate(source, 2, 'f')
    assert (chosen['previous']['operator'], chosen['next']['operator']) == ('f', 'BT')
    assert chosen['z_order'] == dict(semantics=Z_ORDER, prefix_paint_operators=1, suffix_paint_operators=1)
    confirm = lambda **k: confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R2',
        page=2, **{**dict(bounds=REGION['bounds'], insertion=BOUNDARY, graphics_state=BOUNDARY_STATE), **k})
    d = confirm(boundary=chosen['boundary_id'])
    auth = d['authority']
    assert auth['position'] == BOUNDARY and auth['boundary_id'] == chosen['boundary_id']
    assert auth['source_program_sha256'] == chosen['program_sha256'] == d['source_program_sha256']
    assert auth['boundary']['previous'] == chosen['previous'] and auth['boundary']['next'] == chosen['next']
    assert auth['graphics_state'] == chosen['graphics_state'] and auth['graphics_state']['other'] == {'w': '[0.5]'}
    assert auth['z_order']['semantics'] == Z_ORDER and 'page_entry_order' not in d
    # Geometry never selects the boundary: other bounds, the same authority.
    assert confirm(bounds=[18, 100, 173, 200], boundary=chosen['boundary_id'])['authority'] == auth
    refused = next(b for b in inspect_continuation_boundaries(source, 2, include_refused=True)['refused']
                   if b['previous']['operator'] == 're')
    for bad, message in ((dict(boundary='boundary-000000000000000000000000'), 'not a safe'),
                         (dict(boundary=refused['boundary_id']), 'pending-path'),
                         (dict(boundary=None), 'names a boundary'),
                         (dict(boundary=chosen['boundary_id'], graphics_state='isolated-pdf-initial-state'), 'explicit'),
                         (dict(boundary=chosen['boundary_id'], page_entry_order=10), 'no page-entry order')):
        with pytest.raises(PdfError, match=message):
            confirm(**bad)
    with pytest.raises(PdfError, match='names a boundary'):
        confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R2', page=2,
            bounds=REGION['bounds'], insertion='before-page-program', graphics_state='isolated-pdf-initial-state',
            boundary=chosen['boundary_id'])


# -- C to I. lifecycle ---------------------------------------------------------

def test_lifecycle_keeps_prefix_block_suffix_through_edits_and_noops(tmp_path):
    source, state, chosen = flow(tmp_path)
    ident, sid = 'dest-R2', slot_id(state['continuation_destinations']['dest-R2'])
    assert state['destination_bindings'][ident]['boundary']['offset'] == chosen['offset']
    source, state, _ = saved(tmp_path, source, state, change(state, 'AB'), 'fits')
    assert 'end' not in state['destination_bindings'][ident]
    creation = start = previous = None
    for name, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('shorten', 'AB'),
                       ('regrow', LONG), ('noop1', None), ('noop2', None), ('noop3', None)]:
        out, updated, report = saved(tmp_path, source, state, {} if text is None else change(state, text), name)
        slot = updated['slots'][sid]
        assert set(report['plan']['new_slots']) == ({sid} if name == 'grow' else set())
        creation = creation or slot['creation_binding']
        assert slot['creation_binding'] == creation
        mutation = creation['mutation']
        assert mutation['kind'] == CREATE and mutation['start'] == mutation['end'] == chosen['offset']
        assert 'insertion_order' not in mutation
        prefix, block, suffix = split(out, updated, ident)
        # The page-2 prefix never changes, so the block stays where it was created.
        start = start or len(prefix)
        assert len(prefix) == start == chosen['offset'] and prefix == DESTINATION_PAGE[:start]
        assert PAINT_A in prefix and PAINT_B not in prefix and PAINT_B in suffix and PAINT_A not in suffix
        assert last_operator(prefix) == 'f' and first_operator(suffix) == 'BT'
        assert suffix.lstrip(b'\n') == DESTINATION_PAGE[start:].lstrip(b'\n')
        # Paint order from the interpreted program: paint A, the block's glyphs, paint B.
        content = ContentPage(out, 2)
        try:
            events = [e for e in content.events if e.chars]
            glyphs = [e for e in events if start < e.operator.start < start + len(block)]
            tail = [e for e in events if ''.join(c.text for c in e.chars) == 'TAIL']
            paint_a = next(b for b in content.boundaries if b.operator.end == start)
        finally:
            content.close()
        assert len(tail) == 1 and tail[0].operator.start > start + len(block) and paint_a.operator.name == 'f'
        if name == 'shorten':
            assert slot['occupancy'] is None and not slot['binding']['paragraph']['text'] and not glyphs
        else:
            assert slot['occupancy'] is not None and glyphs
        assert updated['destination_bindings'][ident]['operator_nesting'] == destinations.OPERATOR_NESTING
        if name.startswith('noop'):
            with pymupdf.open(source) as a, pymupdf.open(out) as b:
                assert all(a[i].get_pixmap(dpi=144).samples == b[i].get_pixmap(dpi=144).samples for i in range(len(a)))
            fields = ('unicode', 'glyph_id', 'origin', 'size', 'advance', 'code', 'cid', 'nominal_pdf_width')
            for old, new in zip(previous['steps'], report['steps']):
                assert [{k: g[k] for k in fields} for g in old['report']['glyph_plan']] == [
                    {k: g[k] for k in fields} for g in new['report']['glyph_plan']]
            assert updated['generated_fonts'] == state['generated_fonts']
            assert all(set(v.values()) == {'reused'} for v in report['generated_font_outcome'].values())
            for key in ('range', 'occupancy'):
                assert {k: v[key] for k, v in updated['slots'].items()} == {k: v[key] for k, v in state['slots'].items()}
        own = {a for a, r in updated['generated_fonts'].get('2', {}).items() if r['slot_id'] == sid}
        assert own and all(r['slot_id'] == sid for r in updated['generated_fonts']['2'].values())
        source, state, previous = out, updated, report


# -- J, K. same-page mutations before and after the boundary -------------------

@pytest.mark.parametrize('page1,after,where', [
    (SOURCE_PAGE + b'\n0 1 0 rg 200 40 60 20 re f', 'ET', 'prefix'),
    (b'0 1 0 rg 200 200 60 20 re f\n' + SOURCE_PAGE, 'f', 'suffix'),
])
def test_same_transaction_mutation_before_or_after_the_boundary(tmp_path, page1, after, where):
    source, state, chosen = flow(tmp_path, (page1, b'0 g'), page=1, after=after)
    ident, source_offset = 'dest-R2', chosen['offset']
    starts = []
    for name, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('noop', None)]:
        out, state, report = saved(tmp_path, source, state, {} if text is None else change(state, text), name)
        prefix, block, suffix = split(out, state, ident)
        # The source slot of paragraph A is rewritten in this same transaction.
        assert (b'(ABCD) Tj' not in (prefix if where == 'prefix' else suffix))
        assert last_operator(prefix) == after and first_operator(suffix) == ('rg' if where == 'prefix' else 'BT')
        starts.append(len(prefix))
        source = out
    if where == 'prefix':
        # The prefix grew with each rewrite: the block moved with it, by provenance.
        assert source_offset < starts[0] < starts[1] < starts[2]
    else:
        assert starts == [source_offset] * 3


# -- L. touching mutations -----------------------------------------------------

def test_a_mutation_touching_the_boundary_is_refused(tmp_path):
    source = build(tmp_path, SOURCE_PAGE, DESTINATION_PAGE)
    chosen, data = candidate(source, 2, 'f'), DESTINATION_PAGE
    block = b'%pdfengine-begin x\nq BT ET Q\n%pdfengine-end x\n'
    create = lambda: Mutation(chosen['offset'], chosen['offset'], block, kind=CREATE, owner='x',
                              anchors=dict(block_start=0, block_end=len(block)))
    previous = chosen['previous']
    for other in (lambda: Mutation(previous['start'], previous['end'], b'B', kind='text-edit'),
                  lambda: Mutation(chosen['offset'], chosen['offset'], b' 0 g ', kind='mutation')):
        for first, second in ((create, other), (other, create)):
            program = MutationProgram(data)
            program.add(first())
            with pytest.raises(PdfError, match='touches'):
                program.add(second())
    # A change right after the boundary need not touch the insertion, but the
    # revision no longer carries the confirmed neighbor: its witness refuses it.
    source, state, _ = flow(tmp_path / 'flow')
    d = state['continuation_destinations']['dest-R2']
    following = chosen['next']
    changed = DESTINATION_PAGE[:following['start']] + b'0 g ' + DESTINATION_PAGE[following['start']:]
    tampered = build(tmp_path / 'next', SOURCE_PAGE, changed)
    content = ContentPage(tampered, 2)
    try:
        with pytest.raises(PdfError, match='operators differ'):
            destinations.page_witness(content, [d], set(), locations={'dest-R2': chosen['offset']})
    finally:
        content.close()


# -- M, 19. tampering ----------------------------------------------------------

def _tampered(path, page, data, name):
    writer = PdfWriter(clone_from=PdfReader(path))
    stream = DecodedStreamObject()
    stream.set_data(data)
    writer.pages[page - 1][NameObject('/Contents')] = writer._add_object(stream)
    target = path.parent / name
    writer.write(target)
    return target


def _grown(tmp_path):
    source, state, chosen = flow(tmp_path)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    return out, state, chosen


def test_sidecar_and_program_tampering_is_refused(tmp_path):
    out, state, chosen = _grown(tmp_path)
    side = tmp_path / 'grow.json'
    d, binding = state['continuation_destinations']['dest-R2'], state['destination_bindings']['dest-R2']
    edits = {'boundary-id': lambda s: s['continuation_destinations']['dest-R2']['authority'].update(
                 boundary_id='boundary-000000000000000000000000'),
             'source-witness': lambda s: s['continuation_destinations']['dest-R2']['authority']['boundary']['previous'].update(
                 sha256='0' * 64),
             'authority-state': lambda s: s['continuation_destinations']['dest-R2']['authority']['graphics_state'].update(
                 other={'w': '[2]'}),
             'authority-scope': lambda s: s['continuation_destinations']['dest-R2']['authority']['boundary']['scope'].update(
                 q_depth=1),
             'binding-offset': lambda s: s['destination_bindings']['dest-R2']['boundary'].update(offset=binding['start'] + 1),
             'binding-neighbor': lambda s: s['destination_bindings']['dest-R2']['boundary']['previous'].update(start=0)}
    for name, edit in edits.items():
        bad = deepcopy(state)
        edit(bad)
        assert open_shared_flow(out, _reseal(bad))['status'] == 'needs_confirmation', name
    assert open_shared_flow(out, side)['status'] == 'restored'
    data = program(out, 2)
    begin, end = markers(d)
    block = data[binding['start']:binding['end']]
    without = data[:binding['start']] + data[binding['end']:]
    variants = {'missing-marker': data.replace(begin, b'% no marker\n'),
                'block-at-page-entry': block + without,
                'block-after-suffix': without + b'\n' + block,
                'neighbor-operator': data.replace(PAINT_A, PAINT_A[:-1] + b'F', 1)}
    for name, value in variants.items():
        tampered = _tampered(out, 2, value, name + '.pdf')
        content = ContentPage(tampered, 2)
        try:
            with pytest.raises(PdfError):
                destinations.page_witness(content, [d], {'dest-R2'})
        finally:
            content.close()
        assert open_shared_flow(tampered, side)['status'] == 'needs_confirmation', name


@pytest.mark.parametrize('prefix,suffix,reason', [
    (b'1 0 0 1 5 5 cm', b'', 'ctm'),
    (b'0 0 320 260 re W n', b'', 'clip'),
    (b'/GS0 gs', b'', 'extgstate'),
    (b'q', b'Q', 'graphics-state-save'),
    (b'/P BMC', b'EMC', 'marked content'),
])
def test_same_offset_with_another_state_is_not_the_same_authority(tmp_path, prefix, suffix, reason):
    out, state, chosen = _grown(tmp_path)
    d, binding = state['continuation_destinations']['dest-R2'], state['destination_bindings']['dest-R2']
    data = program(out, 2)
    value = data.replace(PREFIX_SLOT, prefix.ljust(len(PREFIX_SLOT)), 1)
    value = value.replace(SUFFIX_SLOT, suffix.ljust(len(SUFFIX_SLOT)), 1)
    assert len(value) == len(data) and value.index(markers(d)[0]) == binding['start']
    tampered = _tampered(out, 2, value, 'state.pdf')
    content = ContentPage(tampered, 2)
    try:
        with pytest.raises(PdfError, match='state or scope differs'):
            destinations.page_witness(content, [d], {'dest-R2'})
    finally:
        content.close()
    assert open_shared_flow(tampered, tmp_path / 'grow.json')['status'] == 'needs_confirmation'
    # The unused boundary of a changed source is not the confirmed one either.
    source = build(tmp_path / reason, SOURCE_PAGE,
                   DESTINATION_PAGE.replace(PREFIX_SLOT, prefix.ljust(len(PREFIX_SLOT)))
                   .replace(SUFFIX_SLOT, suffix.ljust(len(SUFFIX_SLOT))))
    assert not [c for c in inspect_continuation_boundaries(source, 2)['candidates'] if c['offset'] == chosen['offset']]


# -- N. page-entry coexistence -------------------------------------------------

def test_page_entry_and_boundary_blocks_share_a_page_independently(tmp_path):
    areas = {'DA': ('entry', dict(page=2, bounds=[18, 90, 173, 160], x=20, width=150, first_baseline=110)),
             'DB': ('boundary', dict(page=2, bounds=[18, 170, 173, 250], x=20, width=150, first_baseline=190))}
    source, state, chosen = flow(tmp_path, areas=areas)
    sids = {r: slot_id(state['continuation_destinations']['dest-' + r]) for r in areas}
    for name, text in [('grow', LONGER), ('shorten', LONG), ('regrow', LONGER), ('noop', None)]:
        out, state, report = saved(tmp_path, source, state, {} if text is None else change(state, text), name)
        entry, inside = state['destination_bindings']['dest-DA'], state['destination_bindings']['dest-DB']
        data = program(out, 2)
        # The page-entry chain starts at offset 0; the boundary block follows paint A.
        assert entry['start'] == 0 and data.startswith(markers(state['continuation_destinations']['dest-DA'])[0])
        prefix, block, suffix = split(out, state, 'dest-DB')
        assert entry['end'] <= len(prefix) and last_operator(prefix) == 'f' and PAINT_B in suffix
        assert 'boundary' not in entry and 'page_entry_order' not in entry and inside['boundary']['offset'] == len(prefix)
        records = state['generated_fonts']['2']
        owners = {r: {a for a, v in records.items() if v['slot_id'] == sids[r]} for r in areas}
        assert owners['DA'] and owners['DB'] and not owners['DA'] & owners['DB']
        if name == 'shorten':
            assert state['slots'][sids['DB']]['occupancy'] is None
        source = out
    # A tampered boundary block does not disturb the page-entry chain.
    content = ContentPage(source, 2)
    try:
        current, _ = destinations.page_witness(content, [state['continuation_destinations']['dest-DA']], {'dest-DA'})
    finally:
        content.close()
    assert current['dest-DA'] == state['destination_bindings']['dest-DA']


# -- O. rollback ---------------------------------------------------------------

@pytest.mark.parametrize('phase', ['rebind', 'commit'])
def test_late_failure_after_the_boundary_block_publishes_nothing(tmp_path, monkeypatch, phase):
    import pdfeditor.shared_flow as shared
    source, state, _ = flow(tmp_path)
    real, calls = destinations.rebind, []

    def rebind(*args, **kwargs):
        real(*args, **kwargs)
        calls.append(args[1])
        raise PdfError('injected late failure')
    if phase == 'rebind':
        monkeypatch.setattr(shared.destinations, 'rebind', rebind)
    else:
        monkeypatch.setattr(shared.Transaction, '_verify', lambda *a, **k: (_ for _ in ()).throw(PdfError('injected late failure')))
    with pytest.raises(PdfError, match='injected'):
        edit_shared_flow(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', change(state, LONG))
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()
    if phase == 'rebind':
        assert calls == [slot_id(state['continuation_destinations']['dest-R2'])]


# -- page entry and boundary draw the same continuation ------------------------

def test_boundary_and_page_entry_render_the_same_continuation(tmp_path):
    results = {}
    for kind in ('entry', 'boundary'):
        source, state, _ = flow(tmp_path / kind, areas={'R2': (kind, dict(REGION, page=2))})
        out, state, report = saved(tmp_path / kind, source, state, change(state, LONG), 'grow')
        binding = state['destination_bindings']['dest-R2']
        assert (binding['start'] == 0) == (kind == 'entry')
        fields = ('unicode', 'glyph_id', 'origin', 'size', 'code', 'cid')
        with pymupdf.open(out) as doc:
            pixels = [page.get_pixmap(dpi=144).samples for page in doc]
        results[kind] = ([[{k: g[k] for k in fields} for g in step['report']['glyph_plan']] for step in report['steps']],
                         pixels)
    assert results['entry'][0] == results['boundary'][0]
    assert results['entry'][1] == results['boundary'][1]
