"""A confirmed page-program boundary inside one enclosing q ... Q scope.

A boundary at q depth 1 lies between one opening q and its matching Q. The
block's own q ... Q saves and restores only the boundary state, and closes
before that matching Q, which then restores the state before the scope, as
it always did. The scope is identified by structure, witnesses and the q/Q
positions carried by each save's mutation map, never by bytes alone (every
q and Q has the same bytes). PR #12's compensation and PR #14's rectangular
clip apply inside the scope unchanged; nothing else is relaxed.
"""
from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader

from pdfeditor import continuation as destinations
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import (BOUNDARY, BOUNDARY_STATE, CREATE, OPERATOR_NESTING, SCOPE, SCOPE_CONTRACT,
                                    block_ctm, carried_scope, clip_state, confirm_continuation_destination,
                                    inspect_continuation_boundaries, markers, slot_id)
from pdfeditor.document_flow import _reseal
from pdfeditor.mutation import Mutation, MutationProgram
from pdfeditor.operator_nesting import audit
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import _contract, confirm_shared_flow, edit_shared_flow, open_shared_flow
from test_boundary_destination import (DESTINATION_PAGE, PREFIX_SLOT, REGION, SOURCE_PAGE, SUFFIX_SLOT, _story,
                                       _tampered, build, program, split)
from test_clip_boundary import CLIP_BOX, IDENTITY, _re
from test_continuation import LONG, change, saved
from test_ctm_compensation import (CTMS, _inverse, _number, _rectangle, _then, _values, drawings, glyphs, outside,
                                   pixels)

# Page space with y up on a 320 x 260 pt page: page (MuPDF) coordinates are
# (x, 260 - y). REGION ([18, 90, 173, 220]) is empty and inside the clip.
PAINT_A = (200, 200, 260, 230)  # before the scope
PAINT_E = (270, 0, 300, 20)     # scope prefix, before the scope sets its CTM or clip
PAINT_B = (190, 0, 210, 20)     # scope prefix, under its CTM and clip; crosses the clip's bottom edge
TAIL = (190, 140)               # scope suffix text
PAINT_C = (220, 100, 260, 140)  # scope suffix: crosses the clip's right edge
OUTER = (270, 60)               # after the matching Q: text
PAINT_D = (220, 20, 260, 50)    # after the matching Q: crosses where the clip was, drawn in full
# Comment placeholders tests replace, byte for byte, with operators: one in
# the scope before the boundary, one in the scope after its suffix text.
SCOPE_SLOT, INNER_SLOT = b'%' + b'C' * 30, b'%' + b'D' * 30
VARIANTS = {'identity': (None, False, 'clip-cm'), 'compensated': (CTMS['rotation'], False, 'clip-cm'),
            'clip': (None, True, 'clip-cm'), 'compensated-clip': (CTMS['rotation'], True, 'clip-cm')}
CONTRACT = ('witnessed page-level state; the block sets its own font, text state and fill and restores every '
            'parameter with its own q ... Q')


def scoped(ctm=None, *, clip=False, order='clip-cm', source=None):
    """Page 2: paint A, then ``q``, paint E, the scope's clip and CTM, paint B,
    the candidate boundary, TAIL and paint C, the matching ``Q``, OUTER and paint D.

    Every paint lands at the same page coordinates whatever the CTM. With
    ``source`` ('before', 'prefix', 'suffix', 'after'), paragraph A's source
    text is placed before the scope, in it before or after the boundary, or
    after it.
    """
    inverse = _inverse(_values(ctm.decode())) if ctm else IDENTITY
    cm = ctm + b' cm\n' if ctm else b''
    fence = (_re(IDENTITY if order == 'clip-cm' else inverse, CLIP_BOX) + b' W n\n') if clip else b''
    text = SOURCE_PAGE + b'\n'
    place = lambda where: text if source == where else b''
    tail = _then((1, 0, 0, 1, *TAIL), inverse)
    return (PREFIX_SLOT + b'\n0.5 w 0 0 1 rg ' + _rectangle(IDENTITY, PAINT_A) + b'\n' + place('before')
            + b'q\n' + place('prefix') + _rectangle(IDENTITY, PAINT_E) + b'\n'
            + (fence + cm if order == 'clip-cm' else cm + fence) + SCOPE_SLOT + b'\n' + _rectangle(inverse, PAINT_B)
            + b'\nBT /Regular 12 Tf ' + ' '.join(map(_number, tail)).encode() + b' Tm (TAIL) Tj ET\n'
            + INNER_SLOT + b'\n' + place('suffix') + b'0 1 0 rg ' + _rectangle(inverse, PAINT_C) + b'\nQ\n'
            + place('after') + b'BT /Regular 12 Tf ' + ' '.join(map(_number, OUTER)).encode() + b' Td (OUTER) Tj ET\n'
            + _rectangle(IDENTITY, PAINT_D) + b'\n' + SUFFIX_SLOT)


def variant(name, **kwargs):
    ctm, clip, order = VARIANTS[name]
    return scoped(ctm, clip=clip, order=order, **kwargs)


def pick(source, page=2, following='BT'):
    """The caller's explicit choice: the one candidate in the scope right after a paint, before ``following``."""
    found = [c for c in inspect_continuation_boundaries(source, page)['candidates']
             if c['previous']['operator'] == 'f' and c['next']['operator'] == following and 'graphics_state_scope' in c]
    assert len(found) == 1, found
    return found[0]


def flow(tmp_path, page2, areas=None, *, same_page=False, following='BT'):
    """Paragraph A continues into confirmed areas: {region: ('entry' | 'scope', geometry)}.

    By default one area, REGION, at the boundary in the scope. With
    ``same_page``, page 1 is ``page2`` itself, which then holds paragraph A's
    source text (see scoped), and the areas are on it.
    """
    areas = areas or {'R2': ('scope', REGION)}
    source = build(tmp_path, page2) if same_page else build(tmp_path, SOURCE_PAGE, page2)
    page = 1 if same_page else 2
    font = tmp_path / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    p = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(4)), explicit_width=150))
    assert p['text'] == 'ABCD'
    regions = {'R1': dict(page=1, bounds=[18, 40, 173, 78], x=20, width=150, first_baseline=60)}
    chosen, confirmed = {}, {}
    for rid, (kind, geometry) in areas.items():
        regions[rid] = dict(geometry, page=page)
        common = dict(destination_id='dest-' + rid, paragraph_id='A', region_id=rid, page=page,
                      bounds=geometry['bounds'])
        if kind == 'entry':
            d = confirm_continuation_destination(source, insertion='before-page-program',
                                                 graphics_state='isolated-pdf-initial-state', **common)
        else:
            chosen[rid] = pick(source, page, following)
            d = confirm_continuation_destination(source, insertion=BOUNDARY, graphics_state=BOUNDARY_STATE,
                                                 boundary=chosen[rid]['boundary_id'], **common)
        confirmed[d['destination_id']] = d
    state = confirm_shared_flow(source, {'A': _story(source, font, p)}, flow_id='flow', paragraph_order=['A'],
        regions=regions, region_order=list(regions), slot_regions={'A': {'original': 'R1'}},
        paragraph_policies={'A': dict(min_line_height=22, first_line_indent=5, keep_together=False, break_before='auto',
                                      break_after='auto', empty=dict(kind='reserve-line', ascent=10, descent=3))},
        follows=[], protected_regions={}, continuation_destinations=confirmed)
    return source, state, chosen


def head(d):
    compensated = d['authority'].get('ctm_compensation')
    return markers(d)[0] + b'q ' + (compensated['operator'].encode() + b' ' if compensated else b'') + b'BT '


def scope_ops(data):
    """Ordinals of the page's top-level q and Q, and of each q's matching Q."""
    ops = list(operators(data))
    open_after, matching = destinations.graphics_scopes(ops)
    return ops, matching


# -- inspection -----------------------------------------------------------------------

def test_a_boundary_in_one_scope_is_a_candidate_with_its_witnesses(tmp_path):
    page2 = variant('identity')
    source = build(tmp_path, SOURCE_PAGE, page2)
    chosen = pick(source)
    s = chosen['graphics_state_scope']
    assert chosen['reasons'] == [] and chosen['scope']['q_depth'] == 1
    assert s['policy'] == SCOPE and s['depth'] == 1 and s['contract'] == SCOPE_CONTRACT
    # The opening q and matching Q: positions, and bytes that cannot tell them apart.
    q, Q = s['opening'], s['matching']
    assert (q['operator'], Q['operator']) == ('q', 'Q') and page2[q['start']:q['end']] == b'q'
    assert page2[Q['start']:Q['end']] == b'Q' and q['end'] <= chosen['offset'] < Q['start']
    ops, matching = scope_ops(page2)
    assert matching == {q['ordinal']: Q['ordinal']} and ops[q['ordinal']].start == q['start']
    assert q['ordinal'] < chosen['ordinal'] < Q['ordinal']
    # The state the matching Q restores: as before the q.
    restored = s['restored_state']
    assert restored['ctm'] == list(IDENTITY) and restored['clip'] == [] and restored['other'] == {'w': '[0.5]'}
    assert restored['fill'] == ['rg', ['0', '0', '1']]
    found = inspect_continuation_boundaries(source, 2, include_refused=True)
    everything = found['candidates'] + found['refused']
    inside = [b for b in everything if q['end'] <= b['offset'] < Q['start']]
    outside_ = [b for b in everything if not q['end'] <= b['offset'] < Q['start']]
    # Every boundary of the scope carries the same witnesses; no other one carries any.
    assert inside and all(b['graphics_state_scope'] == s for b in inside if b['scope']['q_depth'] == 1)
    assert all('graphics_state_scope' not in b and b['scope']['q_depth'] == 0 for b in outside_)
    assert any(b['status'] == 'safe' for b in outside_)


SCOPE_CASES = [
    # One scope, a sibling scope closed before the boundary, marked content or
    # a compatibility section closed inside the scope: one proven scope.
    (b'q', b' Q', [], True),
    (b'q q Q', b' Q', [], True),
    (b'q /P BMC EMC BX EX', b' Q', [], True),
    # Deeper, or a scope interleaved with marked content or a compatibility section.
    (b'q q', b' Q Q', ['nested-graphics-state-save'], False),
    (b'/P BMC q EMC', b' Q', ['unproven-graphics-state-scope'], False),
    (b'q /P BMC', b' Q EMC', ['unproven-graphics-state-scope', 'inside-marked-content'], False),
    (b'BX q EX', b' Q', ['unproven-graphics-state-scope'], False),
    # Everything else still refuses inside a scope.
    (b'q /GS0 gs', b' Q', ['transparency', 'extgstate'], True),
    (b'q 1 2 2 4 0 0 cm', b' Q', ['singular-ctm'], True),
    (b'q 10 10 m 60 10 l 60 60 l h W n', b' Q', ['nonrectangular-clip'], True),
    (b'q 3 Tr', b' Q', ['text-rendering-mode'], True),
]


@pytest.mark.parametrize('before,after,reasons,proven', SCOPE_CASES)
def test_only_one_proven_scope_is_relaxed(tmp_path, before, after, reasons, proven):
    paint = b'0 0 5 5 re f'
    data = before + b' ' + paint + after + b' 0 g 1 1 1 1 re f'
    source = build(tmp_path, data)
    offset = data.index(paint) + len(paint)
    found = inspect_continuation_boundaries(source, 1, include_refused=True)
    boundary = next(b for b in found['candidates'] + found['refused'] if b['offset'] == offset)
    assert boundary['reasons'] == reasons and (boundary in found['candidates']) == (not reasons)
    assert ('graphics_state_scope' in boundary) == proven
    common = dict(destination_id='d', paragraph_id='A', region_id='R1', page=1, bounds=[100, 100, 200, 200],
                  insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=boundary['boundary_id'])
    if reasons:
        with pytest.raises(PdfError, match=reasons[-1]):
            confirm_continuation_destination(source, **common)
    else:
        d = confirm_continuation_destination(source, **common)
        assert d['authority']['graphics_state_scope'] == boundary['graphics_state_scope']


@pytest.mark.parametrize('data', [b'q 0 0 5 5 re f 0 g', b'0 0 5 5 re f Q 0 g', b'q 0 0 5 5 re f Q Q 0 g'])
def test_a_program_whose_q_and_q_do_not_balance_has_no_candidates(tmp_path, data):
    """An unclosed q or a Q without q: the page's graphics context is unknown."""
    source = build(tmp_path, data)
    with pytest.raises(PdfError, match='unsupported|unbalanced'):
        inspect_continuation_boundaries(source, 1, include_refused=True)


def test_the_authority_records_the_scope_and_page_level_ones_do_not(tmp_path):
    source = build(tmp_path, SOURCE_PAGE, variant('compensated-clip'))
    chosen = pick(source)
    d = confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R2', page=2,
        bounds=REGION['bounds'], insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=chosen['boundary_id'])
    auth = d['authority']
    assert auth['graphics_state_scope'] == chosen['graphics_state_scope'] and auth['boundary']['scope']['q_depth'] == 1
    assert 'ctm_compensation' in auth and 'clip_constraint' in auth
    assert auth['graphics_state_contract'] == (
        'witnessed state inside one confirmed q ... Q scope; the block cancels the witnessed CTM with the recorded '
        'inverse, draws inside the witnessed rectangular clip, which it inherits and never changes, sets its own '
        "font, text state and fill and restores every parameter with its own q ... Q, which closes before the "
        "scope's matching Q")
    # The candidate after paint A, before the scope, is page level: no scope, the PR #11 contract.
    before = next(c for c in inspect_continuation_boundaries(source, 2)['candidates']
                  if c['previous']['operator'] == 'f' and 'graphics_state_scope' not in c)
    plain = confirm_continuation_destination(source, destination_id='p', paragraph_id='A', region_id='R2', page=2,
        bounds=REGION['bounds'], insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=before['boundary_id'])
    assert 'graphics_state_scope' not in plain['authority'] and plain['authority']['graphics_state_contract'] == CONTRACT


# -- lifecycle ------------------------------------------------------------------------

def _states(path, page, binding, d):
    """Interpreted state in the block, after its Q, for TAIL, after the matching Q, and for OUTER."""
    content = ContentPage(path, page)
    try:
        start, end, scope = binding['start'], binding['end'], binding['scope']
        inside = [e for e in content.events if start < e.operator.start < end]
        closing = next(b for b in content.boundaries if b.operator.end == end - len(markers(d)[1]) - 1)
        after = next(b for b in content.boundaries if b.operator.start == scope['matching']['start'])
        named = {name: [e for e in content.events if ''.join(c.text for c in e.chars) == name]
                 for name in ('TAIL', 'OUTER')}
        return inside, closing, after, named
    finally:
        content.close()


@pytest.mark.parametrize('name', VARIANTS)
def test_lifecycle_keeps_the_block_inside_its_scope_through_edits_and_noops(tmp_path, name):
    page2 = variant(name)
    source, state, chosen = flow(tmp_path, page2)
    ident, chosen = 'dest-R2', chosen['R2']
    d = state['continuation_destinations'][ident]
    sid, witnessed = slot_id(d), chosen['graphics_state_scope']
    assert d['authority']['graphics_state_scope'] == witnessed
    confirmed_scope = state['destination_bindings'][ident]['scope']
    assert confirmed_scope == {k: {f: witnessed[k][f] for f in ('start', 'end')} for k in ('opening', 'matching')}
    source, state, _ = saved(tmp_path, source, state, change(state, 'AB'), 'fits')
    assert 'end' not in state['destination_bindings'][ident] and state['destination_bindings'][ident]['scope'] == confirmed_scope
    creation = previous = None
    for step, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('shorten', 'AB'),
                       ('regrow', LONG), ('noop1', None), ('noop2', None), ('noop3', None)]:
        out, updated, report = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        d = updated['continuation_destinations'][ident]
        assert d == state['continuation_destinations'][ident] and d['authority']['graphics_state_scope'] == witnessed
        slot = updated['slots'][sid]
        creation = creation or slot['creation_binding']
        assert slot['creation_binding'] == creation and creation['mutation']['kind'] == CREATE
        assert creation['mutation']['start'] == chosen['offset']
        binding = updated['destination_bindings'][ident]
        assert binding['operator_nesting'] == OPERATOR_NESTING
        prefix, block, suffix = split(out, updated, ident)
        assert prefix == page2[:chosen['offset']] and suffix.lstrip(b'\n') == page2[len(prefix):].lstrip(b'\n')
        # The block lies between the scope's q and its matching Q, which moved only by the block's growth.
        data, scope = program(out, 2), binding['scope']
        q, Q = scope['opening'], scope['matching']
        assert data[q['start']:q['end']] == b'q' and data[Q['start']:Q['end']] == b'Q'
        assert q == confirmed_scope['opening'] and Q['start'] == confirmed_scope['matching']['start'] + len(block)
        assert q['end'] <= binding['start'] < binding['end'] <= Q['start']
        ops, matching = scope_ops(data)
        ordinal = {op.start: i for i, op in enumerate(ops)}
        assert matching[ordinal[q['start']]] == ordinal[Q['start']]
        body = block[len(markers(d)[0]):len(block) - len(markers(d)[1])]
        assert block.startswith(head(d)) and not {op.name for op in operators(body)} & {'W', 'W*', 'n', 're'}
        assert audit(data)['violations'] == [] and audit(body)['violations'] == []
        inside, closing, after, named = _states(out, 2, binding, d)
        auth = d['authority']
        # In the block: the witnessed state, the CTM cancelled if needed, one q deeper.
        assert inside and {e.state.ctm for e in inside} == {block_ctm(d)}
        assert all(clip_state(e.state.clip) == auth['graphics_state']['clip'] for e in inside)
        # After the block's Q, still in the scope: the boundary state again, q depth 1.
        assert closing.operator.name == 'Q' and closing.q_depth == 1
        assert destinations._state(closing) == auth['graphics_state']
        tail = named['TAIL']
        assert len(tail) == 1 and list(tail[0].state.ctm) == auth['graphics_state']['ctm']
        assert clip_state(tail[0].state.clip) == auth['graphics_state']['clip']
        # After the matching Q: the state before the scope, at page level.
        assert after.operator.name == 'Q' and after.q_depth == 0
        assert destinations._state(after) == witnessed['restored_state']
        outer = named['OUTER']
        assert len(outer) == 1 and outer[0].state.ctm == IDENTITY and outer[0].state.clip == ()
        assert outer[0].state.fill == ('rg', ('0', '0', '1'))
        if step == 'shorten':
            assert slot['occupancy'] is None and not glyphs(out)
        else:
            assert slot['occupancy'] is not None and len(glyphs(out)) > 20
        own = {a for a, r in updated['generated_fonts'].get('2', {}).items() if r['slot_id'] == sid}
        assert own and all(r['slot_id'] == sid for r in updated['generated_fonts']['2'].values())
        if step.startswith('noop'):
            assert pixels(out) == pixels(source) and glyphs(out) == glyphs(source)
            assert updated['generated_fonts'] == state['generated_fonts']
            assert all(set(v.values()) == {'reused'} for v in report['generated_font_outcome'].values())
            fields = ('unicode', 'glyph_id', 'origin', 'size', 'advance', 'code', 'cid', 'nominal_pdf_width')
            for old, new in zip(previous['steps'], report['steps']):
                assert [{k: g[k] for k in fields} for g in old['report']['glyph_plan']] == [
                    {k: g[k] for k in fields} for g in new['report']['glyph_plan']]
        source, state, previous = out, updated, report


@pytest.mark.parametrize('name', VARIANTS)
def test_the_block_in_a_scope_draws_where_page_entry_does(tmp_path, name):
    page2 = variant(name)
    outputs = {}
    for kind in ('entry', 'scope'):
        source, state, _ = flow(tmp_path / kind, page2, {'R2': (kind, REGION)})
        out, state, report = saved(tmp_path / kind, source, state, change(state, LONG), 'grow')
        fields = ('unicode', 'glyph_id', 'origin', 'size', 'code', 'cid')
        outputs[kind] = dict(source=source, out=out, glyphs=glyphs(out), pixels=pixels(out),
            plan=[[{k: g[k] for k in fields} for g in step['report']['glyph_plan']] for step in report['steps']])
    entry, mine = outputs['entry'], outputs['scope']
    assert mine['plan'] == entry['plan'] and mine['glyphs'] == entry['glyphs'] and len(mine['glyphs']) > 20
    assert mine['pixels'] == entry['pixels']
    # Inside and after the scope, everything else paints what the source paints:
    # paints B and C cut by the scope's clip, OUTER and paint D in full after it.
    for path in (entry['out'], mine['out']):
        assert glyphs(path, bounds=(180, 0, 320, 260)) == glyphs(mine['source'], bounds=(180, 0, 320, 260))
        assert drawings(path) == drawings(mine['source'])
        assert outside(pixels(path), REGION['bounds']) == outside(pixels(mine['source']), REGION['bounds'])


def test_a_block_at_the_end_of_its_scope_closes_before_the_matching_q(tmp_path):
    """The boundary right before the matching Q: the block is the scope's last content."""
    page2 = variant('compensated-clip')
    source, state, chosen = flow(tmp_path, page2, following='Q')
    for step, text in [('grow', LONG), ('noop', None)]:
        out, state, _ = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        binding = state['destination_bindings']['dest-R2']
        data = program(out, 2)
        # Only whitespace separates the block's own end from the matching Q.
        assert data[binding['end']:binding['scope']['matching']['start']].strip() == b''
        d = state['continuation_destinations']['dest-R2']
        inside, closing, after, named = _states(out, 2, binding, d)
        assert closing.q_depth == 1 and after.q_depth == 0 and after.operator.start > closing.operator.start
        assert destinations._state(after) == d['authority']['graphics_state_scope']['restored_state']
        assert named['OUTER'][0].state.ctm == IDENTITY and inside
        source = out


# -- tampering ------------------------------------------------------------------------

def _forged(state, edit):
    """A sidecar whose authority is edited and whose contract and checksum match again."""
    bad = deepcopy(state)
    edit(bad['continuation_destinations']['dest-R2']['authority'])
    bad['contract_sha256'] = _contract(bad)
    return _reseal(bad)


def _grown(tmp_path, name='compensated-clip'):
    page2 = variant(name)
    source, state, chosen = flow(tmp_path, page2)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    return page2, out, state


def test_a_changed_scope_record_is_not_the_same_authority(tmp_path):
    _, out, state = _grown(tmp_path)
    side = tmp_path / 'grow.json'
    assert open_shared_flow(out, side)['status'] == 'restored'
    scope = lambda a: a['graphics_state_scope']
    edits = {
        'opening-ordinal': (lambda a: scope(a)['opening'].update(ordinal=scope(a)['opening']['ordinal'] + 1),
                            'ID differs'),
        'opening-bytes': (lambda a: scope(a)['opening'].update(sha256='0' * 64), 'ID differs'),
        'matching-end': (lambda a: scope(a)['matching'].update(end=scope(a)['matching']['end'] + 1), 'ID differs'),
        'depth': (lambda a: scope(a).update(depth=2), 'q ... Q scope differs'),
        'restored-state': (lambda a: scope(a)['restored_state'].update(fill=['g', ['0']]), 'q ... Q scope differs'),
        'policy': (lambda a: scope(a).update(policy='none'), 'q ... Q scope differs'),
        'contract': (lambda a: scope(a).update(contract='none'), 'q ... Q scope differs'),
        'removed': (lambda a: a.pop('graphics_state_scope'), 'ID differs'),
        'state-contract': (lambda a: a.update(graphics_state_contract=CONTRACT), 'graphics-state contract differs'),
        'boundary-depth': (lambda a: a['boundary']['scope'].update(q_depth=0), 'state or scope differs'),
    }
    for name, (edit, message) in edits.items():
        opened = open_shared_flow(out, _forged(state, edit))
        assert opened['status'] == 'needs_confirmation' and message in opened['reason'], (name, opened)
    # This revision's binding names where the scope's q and Q are.
    binding = lambda s: s['destination_bindings']['dest-R2']
    moves = {
        'binding-opening': (lambda s: binding(s)['scope']['opening'].update(start=binding(s)['scope']['opening']['start'] + 1),
                            'not in its confirmed q ... Q scope'),
        'binding-matching': (lambda s: binding(s)['scope']['matching'].update(start=binding(s)['scope']['matching']['start'] - 1),
                             'not in its confirmed q ... Q scope'),
        'binding-removed': (lambda s: binding(s).pop('scope'), 'needs its scope location'),
    }
    for name, (edit, message) in moves.items():
        bad = deepcopy(state)
        edit(bad)
        opened = open_shared_flow(out, _reseal(bad))
        assert opened['status'] == 'needs_confirmation' and message in opened['reason'], (name, opened)


def _swap(data, old, new):
    assert data.count(old) == 1 and len(old) == len(new), old
    return data.replace(old, new)


def test_another_q_or_q_with_the_same_bytes_is_another_scope(tmp_path):
    page2, out, state = _grown(tmp_path)
    side = tmp_path / 'grow.json'
    d, binding = state['continuation_destinations']['dest-R2'], state['destination_bindings']['dest-R2']
    data = program(out, 2)
    q, Q = binding['scope']['opening'], binding['scope']['matching']
    paint_e = _rectangle(IDENTITY, PAINT_E)
    outer = b'BT /Regular 12 Tf ' + ' '.join(map(_number, OUTER)).encode() + b' Td (OUTER) Tj ET\n'
    blank = lambda value, spans: b''.join(
        [value[:spans[0]['start']]] + [b' ' + value[a['end']:b['start']] for a, b in zip(spans, spans[1:])]
        + [b' ' + value[spans[-1]['end']:]])
    block = data[binding['start']:binding['end']]
    without = data[:binding['start']] + data[binding['end']:]
    moved_q = without.index(b'\nQ\n', binding['start']) + 3
    variants = {
        # The same bytes, depth and state, but another q or another Q.
        'opening-q-moved': (_swap(data, b'q\n' + paint_e + b'\n', paint_e + b'\nq\n'), 'not in its confirmed q ... Q scope'),
        'matching-Q-moved': (_swap(data, b'Q\n' + outer, outer + b'Q\n'), 'not in its confirmed q ... Q scope'),
        # A Q q after the boundary: the opening q now matches another Q.
        'correspondence': (_swap(data, INNER_SLOT, b'Q q'.ljust(len(INNER_SLOT))), 'not in its confirmed q ... Q scope'),
        # A Q q before the boundary: the boundary is in another scope.
        'other-scope': (_swap(data, SCOPE_SLOT, b'Q q'.ljust(len(SCOPE_SLOT))), 'scope'),
        # Depth 2, depth 0, and a Q without its q.
        'nested': (_swap(_swap(data, SCOPE_SLOT, b'q'.ljust(len(SCOPE_SLOT))), INNER_SLOT, b'Q'.ljust(len(INNER_SLOT))),
                   'state or scope differs'),
        'scope-removed': (blank(data, [q, Q]), 'state or scope differs'),
        'unbalanced': (blank(data, [Q]), 'unsupported'),
        # The block moved past the matching Q.
        'block-past-matching-Q': (without[:moved_q] + block + without[moved_q:], 'operators'),
    }
    for name, (value, message) in variants.items():
        assert value != data, name
        tampered = _tampered(out, 2, value, name + '.pdf')
        content = ContentPage(tampered, 2)
        try:
            with pytest.raises(PdfError, match=message):
                destinations.page_witness(content, [d], {'dest-R2'}, scopes={'dest-R2': binding['scope']})
        finally:
            content.close()
        assert open_shared_flow(tampered, side)['status'] == 'needs_confirmation', name
    # Control: the untouched revision, witnessed the same way.
    content = ContentPage(out, 2)
    try:
        current, _ = destinations.page_witness(content, [d], {'dest-R2'}, scopes={'dest-R2': binding['scope']})
    finally:
        content.close()
    assert current['dest-R2'] == binding


# -- mutation and rebind --------------------------------------------------------------

def _source_ids(source, page):
    """Glyph IDs of paragraph A's ABCD, wherever it is on the page."""
    with pymupdf.open(source) as doc:
        chars = [chr(c[0]) for span in doc[page - 1].get_texttrace() for c in span['chars']]
    start = ''.join(chars).index('ABCD')
    return list(range(start, start + 4))


@pytest.mark.parametrize('where', ['before', 'prefix', 'suffix', 'after'])
def test_same_transaction_edits_around_the_scope_keep_it(tmp_path, where, monkeypatch):
    """Paragraph A's source slot sits before the scope, in it before or after the boundary, or after it.

    Each save rewrites that slot in the same transaction: offsets and
    ordinals move, and the boundary stays in the same scope.
    """
    import test_scope_boundary as module
    real = module.make_selection
    monkeypatch.setattr(module, 'make_selection', lambda source, *a, glyph_ids, **k: real(
        source, *a, glyph_ids=_source_ids(source, 1), **k))
    page = scoped(source=where)
    source, state, chosen = flow(tmp_path, page, same_page=True)
    chosen = chosen['R2']
    confirmed = state['destination_bindings']['dest-R2']['scope']
    opening, gaps = [], []
    for step, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('noop', None)]:
        out, state, _ = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        d, binding = state['continuation_destinations']['dest-R2'], state['destination_bindings']['dest-R2']
        assert d['authority']['graphics_state_scope'] == chosen['graphics_state_scope']
        data, scope = program(out, 1), binding['scope']
        prefix = split(out, state, 'dest-R2')[0]
        assert b'(ABCD) Tj' not in data and data[scope['opening']['start']:scope['opening']['end']] == b'q'
        assert scope['opening']['end'] <= binding['start'] < binding['end'] <= scope['matching']['start']
        ops, matching_of = scope_ops(data)
        ordinal = {op.start: i for i, op in enumerate(ops)}
        assert matching_of[ordinal[scope['opening']['start']]] == ordinal[scope['matching']['start']]
        assert (len(prefix) > chosen['offset']) == (where in ('before', 'prefix'))
        opening.append(scope['opening']['start'])
        gaps.append(scope['matching']['start'] - binding['end'])
        source = out
    # The slot grows with each rewrite, and moves what follows it: the q only
    # when it precedes the scope, the matching Q past the block only when it is
    # in the scope's suffix. The block's own growth moves the matching Q too.
    assert (opening == sorted(set(opening)) and opening[0] > confirmed['opening']['start']) == (where == 'before')
    if where != 'before':
        assert opening == [confirmed['opening']['start']] * 3
    if where == 'suffix':
        assert gaps == sorted(set(gaps)) and gaps[0] > confirmed['matching']['start'] - chosen['offset']
    else:
        assert gaps == [confirmed['matching']['start'] - chosen['offset']] * 3


def test_a_mutation_across_the_scope_edge_leaves_it_no_successor():
    data = variant('identity')
    ops, matching = scope_ops(data)
    q = next(i for i, op in enumerate(ops) if op.name == 'q')
    q, Q = ops[q], ops[matching[q]]
    location = dict(opening=dict(start=q.start, end=q.end), matching=dict(start=Q.start, end=Q.end))
    # An insertion in front of the q, or in the scope, moves them: they follow.
    program_map = MutationProgram(data)
    program_map.add(Mutation(q.start, q.start, b'% x\n', kind='mutation'))
    program_map.add(Mutation(Q.start - 1, Q.start - 1, b'% yy\n', kind='mutation'))
    assert carried_scope(program_map, location) == dict(opening=dict(start=q.start + 4, end=q.end + 4),
                                                        matching=dict(start=Q.start + 9, end=Q.end + 9))
    # A mutation that consumes the q or the Q, alone or with its neighbors, crosses the scope's edge.
    for start, end in ((q.start, q.end), (q.start - 3, q.end + 2), (Q.start, Q.end), (Q.start - 4, Q.end)):
        program_map = MutationProgram(data)
        program_map.add(Mutation(start, end, b'X', kind='mutation'))
        with pytest.raises(PdfError, match='consumed'):
            carried_scope(program_map, location)


# -- rollback -------------------------------------------------------------------------

@pytest.mark.parametrize('phase', ['rebind', 'commit'])
def test_late_failure_in_a_scope_publishes_nothing(tmp_path, monkeypatch, phase):
    import pdfeditor.shared_flow as shared
    page2 = variant('compensated-clip')
    source, state, _ = flow(tmp_path, page2)
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
    assert open_shared_flow(source, state)['status'] == 'restored'
    assert PdfReader(source).pages[1].get_contents().get_data() == page2


# -- page-level boundaries keep their form --------------------------------------------

def test_a_page_level_boundary_beside_a_scope_keeps_its_form(tmp_path):
    """The candidate after paint A precedes the scope: no scope record, no scope in its binding."""
    import test_scope_boundary as module
    source = build(tmp_path, SOURCE_PAGE, variant('identity'))
    before = next(c for c in inspect_continuation_boundaries(source, 2)['candidates']
                  if c['previous']['operator'] == 'f' and 'graphics_state_scope' not in c)
    assert before['scope']['q_depth'] == 0
    from test_boundary_destination import flow as plain
    source, state, chosen = plain(tmp_path / 'plain', (SOURCE_PAGE, DESTINATION_PAGE))
    d = state['continuation_destinations']['dest-R2']
    assert 'graphics_state_scope' not in d['authority'] and d['authority']['graphics_state_contract'] == CONTRACT
    out, state, _ = saved(tmp_path / 'plain', source, state, change(state, LONG), 'grow')
    assert set(state['destination_bindings']['dest-R2']) == {'program_sha256', 'boundary', 'start', 'end',
                                                             'block_sha256', 'operator_nesting'}
    assert module.SCOPE == SCOPE
