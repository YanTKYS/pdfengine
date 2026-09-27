"""A confirmed page-program boundary inside a chain of two q ... Q scopes.

A boundary at q depth 2 lies inside an inner q ... Q that lies inside an
outer q ... Q. Both levels are the authority: each one's opening q and
matching Q, and the state its Q restores. The block's own q ... Q closes
before the inner matching Q; the inner Q restores the outer scope's state
and the outer Q the page-level state, as they always did. All four
operators are carried from revision to revision on their own. PR #12's
compensation and PR #14's rectangular clip apply unchanged, whichever level
set the CTM or the clip; nothing else is relaxed, and depth 3 still refuses.
"""
from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader

from pdfeditor import continuation as destinations
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import (BOUNDARY, BOUNDARY_STATE, CREATE, OPERATOR_NESTING, SCOPE, SCOPE_CHAIN,
                                    SCOPE_CHAIN_CONTRACT, SCOPE_CONTRACT, block_ctm, boundary_id, carried_scope,
                                    clip_state, confirm_continuation_destination, inspect_continuation_boundaries,
                                    markers, slot_id)
from pdfeditor.document_flow import _reseal
from pdfeditor.mutation import Mutation, MutationProgram
from pdfeditor.operator_nesting import audit
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import _contract, confirm_shared_flow, edit_shared_flow, open_shared_flow
from test_boundary_destination import PREFIX_SLOT, REGION, SOURCE_PAGE, SUFFIX_SLOT, _story, _tampered, build, program, split
from test_clip_boundary import CLIP_BOX, IDENTITY, _re
from test_continuation import LONG, change, saved
from test_ctm_compensation import CTMS, _inverse, _number, _rectangle, _then, _values, drawings, glyphs, outside, pixels

# Page space with y up on a 320 x 260 pt page: page (MuPDF) coordinates are
# (x, 260 - y). REGION ([18, 90, 173, 220]) is empty and inside the clip.
PAINT_A = (200, 200, 260, 230)  # page level, before the chain
PAINT_E = (270, 0, 300, 20)     # outer prefix, before the outer scope changes its state
PAINT_B = (190, 0, 210, 20)     # inner prefix, under the chain's CTM and clip; crosses the clip's bottom edge
TAIL = (190, 140)               # inner suffix text
PAINT_C = (220, 100, 260, 140)  # inner suffix: crosses the clip's right edge
MIDDLE = (190, 165)             # outer suffix text, after the inner Q
PAINT_G = (215, 60, 250, 90)    # outer suffix: crosses the clip's right edge when the outer scope clips
OUTER = (270, 60)               # after the outer Q: text
PAINT_D = (220, 20, 260, 50)    # after the outer Q: crosses where the clip was, drawn in full
# Comment placeholders tests replace, byte for byte, with operators: in the
# outer prefix before the inner q, in the inner scope before and after the
# boundary, and in the outer suffix after the inner Q.
OUTER_SLOT, SCOPE_SLOT, INNER_SLOT, OUTER_SUFFIX_SLOT = (b'%' + c * 30 for c in (b'O', b'C', b'D', b'S'))
# The CTM, set in the inner scope, and where the rectangular clip is set.
VARIANTS = {'identity': (None, None), 'compensated': (CTMS['rotation'], None), 'clip': (None, 'inner'),
            'compensated-clip': (CTMS['rotation'], 'outer')}
# The form PR #16 found in the LibreOffice source: the page clip set in the
# outer scope, a translation in the inner one.
GEOMETRY = dict(VARIANTS, **{'translated-clip': (CTMS['translation'], 'outer')})
PAGE_FILL, OUTER_FILL = ('rg', ('0', '0', '1')), ('rg', ('0', '0.6', '0'))
CONTRACT = ('witnessed state inside a confirmed chain of two nested q ... Q scopes; the block cancels the witnessed '
            'CTM with the recorded inverse, draws inside the witnessed rectangular clip, which it inherits and never '
            "changes, sets its own font, text state and fill and restores every parameter with its own q ... Q, "
            "which closes before the inner scope's matching Q")


def text_line(at, name, matrix=None):
    if matrix is None:
        return b'BT /Regular 12 Tf ' + ' '.join(map(_number, at)).encode() + b' Td (' + name + b') Tj ET\n'
    return b'BT /Regular 12 Tf ' + ' '.join(map(_number, matrix)).encode() + b' Tm (' + name + b') Tj ET\n'


def chained(ctm=None, *, clip=None, source=None):
    """Page 2: paint A; the outer ``q``, paint E and the outer state (fill,
    stroke, width, and the clip when ``clip == 'outer'``); the inner ``q``,
    its clip (``clip == 'inner'``), its CTM and fill, paint B, the candidate
    boundary, TAIL and paint C; the inner ``Q``; MIDDLE and paint G; the
    outer ``Q``; OUTER and paint D.

    Every paint lands at the same page coordinates whatever the CTM. With
    ``source`` ('before', 'outer-prefix', 'inner-prefix', 'inner-suffix',
    'outer-suffix', 'after'), paragraph A's source text is placed there.
    """
    inverse = _inverse(_values(ctm.decode())) if ctm else IDENTITY
    cm = ctm + b' cm\n' if ctm else b''
    fence = _re(IDENTITY, CLIP_BOX) + b' W n\n'
    place = lambda where: SOURCE_PAGE + b'\n' if source == where else b''
    return (PREFIX_SLOT + b'\n0.5 w 0 0 1 rg ' + _rectangle(IDENTITY, PAINT_A) + b'\n' + place('before')
            + b'q\n' + _rectangle(IDENTITY, PAINT_E) + b'\n' + place('outer-prefix')
            + b'2 w 1 0 0 RG 0 0.6 0 rg\n' + (fence if clip == 'outer' else b'') + OUTER_SLOT + b'\n'
            + b'q\n' + (fence if clip == 'inner' else b'') + cm + b'0.5 0 0.5 rg\n' + place('inner-prefix')
            + SCOPE_SLOT + b'\n' + _rectangle(inverse, PAINT_B) + b'\n'
            + text_line(None, b'TAIL', _then((1, 0, 0, 1, *TAIL), inverse))
            + INNER_SLOT + b'\n' + place('inner-suffix') + b'0 1 0 rg ' + _rectangle(inverse, PAINT_C) + b'\nQ\n'
            + OUTER_SUFFIX_SLOT + b'\n' + place('outer-suffix') + text_line(MIDDLE, b'MIDDLE')
            + _rectangle(IDENTITY, PAINT_G) + b'\nQ\n'
            + place('after') + text_line(OUTER, b'OUTER') + _rectangle(IDENTITY, PAINT_D) + b'\n' + SUFFIX_SLOT)


def variant(name, **kwargs):
    ctm, clip = GEOMETRY[name]
    return chained(ctm, clip=clip, **kwargs)


def pick(source, page=2, following='BT'):
    """The caller's explicit choice: the one depth-2 candidate right after a paint, before ``following``."""
    found = [c for c in inspect_continuation_boundaries(source, page)['candidates']
             if c['previous']['operator'] == 'f' and c['next']['operator'] == following
             and c['scope']['q_depth'] == 2]
    assert len(found) == 1, found
    return found[0]


def flow(tmp_path, page2, areas=None, *, same_page=False, following='BT'):
    """Paragraph A continues into confirmed areas: {region: ('entry' | 'chain', geometry)}.

    By default one area, REGION, at the boundary in the inner scope. With
    ``same_page``, page 1 is ``page2`` itself, which then holds paragraph A's
    source text (see chained), and the areas are on it.
    """
    areas = areas or {'R2': ('chain', REGION)}
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


def matching_of(data):
    """The page's top-level operators and, for each q's ordinal, its matching Q's ordinal."""
    ops = list(operators(data))
    return ops, destinations.graphics_scopes(ops)[1]


def where(record):
    return {k: {f: record[k][f] for f in ('start', 'end')} for k in ('opening', 'matching')}


# -- inspection -----------------------------------------------------------------------

def test_a_boundary_in_a_chain_of_two_scopes_is_a_candidate_with_both_levels(tmp_path):
    page2 = variant('compensated-clip')
    source = build(tmp_path, SOURCE_PAGE, page2)
    chosen = pick(source)
    s = chosen['graphics_state_scope']
    assert chosen['reasons'] == [] and chosen['scope']['q_depth'] == 2
    assert set(s) == {'policy', 'depth', 'outer', 'inner', 'contract'}
    assert s['policy'] == SCOPE_CHAIN and s['depth'] == 2 and s['contract'] == SCOPE_CHAIN_CONTRACT
    outer, inner = s['outer'], s['inner']
    assert set(outer) == set(inner) == {'opening', 'matching', 'restored_state'}
    # outer q < inner q < boundary < inner Q < outer Q, and each q is matched by its Q on the q/Q stack.
    assert (outer['opening']['end'] <= inner['opening']['start'] and inner['opening']['end'] <= chosen['offset']
            < inner['matching']['start'] and inner['matching']['end'] <= outer['matching']['start'])
    ops, matching = matching_of(page2)
    for level in (outer, inner):
        q, Q = level['opening'], level['matching']
        assert (q['operator'], Q['operator']) == ('q', 'Q') and page2[q['start']:q['end']] == b'q'
        assert page2[Q['start']:Q['end']] == b'Q' and matching[q['ordinal']] == Q['ordinal']
        assert ops[q['ordinal']].start == q['start'] and ops[Q['ordinal']].start == Q['start']
    # The inner Q restores the outer scope's state; the outer Q the page-level state.
    assert inner['restored_state']['fill'] == [OUTER_FILL[0], list(OUTER_FILL[1])]
    assert inner['restored_state']['stroke'] == ['RG', ['1', '0', '0']] and inner['restored_state']['other'] == {'w': '[2]'}
    assert inner['restored_state']['ctm'] == list(IDENTITY) and len(inner['restored_state']['clip']) == 1
    assert outer['restored_state']['fill'] == [PAGE_FILL[0], list(PAGE_FILL[1])]
    assert outer['restored_state']['ctm'] == list(IDENTITY) and outer['restored_state']['clip'] == []
    assert outer['restored_state']['other'] == {'w': '[0.5]'}
    # The chain's CTM and clip are proven with the existing contracts.
    assert 'ctm_compensation' in chosen and chosen['clip_constraint']['rectangle'][0] > 10
    # Every depth-2 boundary of the inner scope carries the same chain; the
    # outer scope's own boundaries keep the depth-1 form; none outside carries one.
    found = inspect_continuation_boundaries(source, 2, include_refused=True)
    everything = found['candidates'] + found['refused']
    depth = {b['scope']['q_depth'] for b in everything}
    assert depth == {0, 1, 2}
    assert all(b['graphics_state_scope'] == s for b in everything if b['scope']['q_depth'] == 2)
    ones = [b for b in everything if b['scope']['q_depth'] == 1]
    assert ones and all(b['graphics_state_scope']['policy'] == SCOPE and b['graphics_state_scope']['depth'] == 1
                        and b['graphics_state_scope']['opening'] == outer['opening']
                        and set(b['graphics_state_scope']) == {'policy', 'depth', 'opening', 'matching',
                                                               'restored_state', 'contract'} for b in ones)
    assert all('graphics_state_scope' not in b for b in everything if b['scope']['q_depth'] == 0)


CHAIN_CASES = [
    # Two nested scopes, with a closed sibling or closed marked content or a
    # compatibility section in the outer one: one proven chain.
    (b'q q', b' Q Q', [], 2),
    (b'q q Q q', b' Q Q', [], 2),
    (b'q /P BMC EMC BX EX q', b' Q Q', [], 2),
    # Three levels, or a chain interleaved with marked content or a compatibility section.
    (b'q q q', b' Q Q Q', ['nested-graphics-state-save'], None),
    (b'q /P BMC q EMC', b' Q Q', ['unproven-graphics-state-scope'], None),
    (b'q BX q EX', b' Q Q', ['unproven-graphics-state-scope'], None),
    (b'/P BMC q EMC q', b' Q Q', ['unproven-graphics-state-scope'], None),
    # Everything else still refuses inside a proven chain.
    (b'q q /GS0 gs', b' Q Q', ['transparency', 'extgstate'], 2),
    (b'q /GS0 gs q', b' Q Q', ['transparency', 'extgstate'], 2),
    (b'q q 1 2 2 4 0 0 cm', b' Q Q', ['singular-ctm'], 2),
    (b'q q 10 10 m 60 10 l 60 60 l h W n', b' Q Q', ['nonrectangular-clip'], 2),
    (b'q q 3 Tr', b' Q Q', ['text-rendering-mode'], 2),
    (b'q q BT /Regular 12 Tf 7 Tr (A) Tj ET 0 Tr', b' Q Q', ['text-clip'], 2),
]


@pytest.mark.parametrize('before,after,reasons,proven', CHAIN_CASES)
def test_only_a_proven_chain_of_two_is_relaxed(tmp_path, before, after, reasons, proven):
    paint = b'0 0 5 5 re f'
    data = before + b' ' + paint + after + b' 0 g 1 1 1 1 re f'
    source = build(tmp_path, data)
    offset = data.index(paint) + len(paint)
    found = inspect_continuation_boundaries(source, 1, include_refused=True)
    boundary = next(b for b in found['candidates'] + found['refused'] if b['offset'] == offset)
    assert boundary['reasons'] == reasons and (boundary in found['candidates']) == (not reasons)
    assert boundary.get('graphics_state_scope', {}).get('depth') == proven
    common = dict(destination_id='d', paragraph_id='A', region_id='R1', page=1, bounds=[100, 100, 200, 200],
                  insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=boundary['boundary_id'])
    if reasons:
        with pytest.raises(PdfError, match=reasons[-1]):
            confirm_continuation_destination(source, **common)
    else:
        d = confirm_continuation_destination(source, **common)
        assert d['authority']['graphics_state_scope'] == boundary['graphics_state_scope']


def test_the_boundary_id_covers_both_levels_and_their_correspondence():
    """Same page, program and boundary operators: another inner or outer scope, or another pairing, is another ID."""
    op = lambda ordinal, end, name: dict(ordinal=ordinal, operator=name, start=end - 1, end=end, sha256=name * 64)
    previous, following = op(10, 100, 'f'), op(11, 110, 'B')
    level = lambda q, Q: dict(opening=op(*q, 'q'), matching=op(*Q, 'Q'), restored_state={})
    chain = lambda outer, inner: dict(policy=SCOPE_CHAIN, depth=2, outer=level(*outer), inner=level(*inner),
                                      contract=SCOPE_CHAIN_CONTRACT)
    base = chain(((1, 5), (30, 300)), ((4, 50), (20, 200)))
    ids = {boundary_id(2, 'p', previous, following, s) for s in (
        base,
        chain(((1, 5), (30, 300)), ((5, 60), (20, 200))),     # another inner q
        chain(((1, 5), (30, 300)), ((4, 50), (21, 210))),     # another inner Q
        chain(((0, 3), (30, 300)), ((4, 50), (20, 200))),     # another outer q
        chain(((1, 5), (31, 310)), ((4, 50), (20, 200))),     # another outer Q
        chain(((1, 5), (20, 200)), ((4, 50), (30, 300))),     # the Qs paired the other way
    )}
    assert len(ids) == 6
    # Depth 1 over the inner level alone, and the page level, are other boundaries too.
    one = dict(policy=SCOPE, depth=1, **level((4, 50), (20, 200)), contract=SCOPE_CONTRACT)
    assert boundary_id(2, 'p', previous, following, one) not in ids
    assert boundary_id(2, 'p', previous, following) not in ids
    assert boundary_id(2, 'p', previous, following, base) == boundary_id(2, 'p', previous, following, deepcopy(base))


def test_the_authority_and_binding_record_both_levels(tmp_path):
    page2 = variant('compensated-clip')
    source, state, chosen = flow(tmp_path, page2)
    chosen = chosen['R2']
    d, binding = state['continuation_destinations']['dest-R2'], state['destination_bindings']['dest-R2']
    auth = d['authority']
    assert auth['graphics_state_scope'] == chosen['graphics_state_scope'] and auth['boundary']['scope']['q_depth'] == 2
    assert auth['boundary_id'] == chosen['boundary_id']
    assert 'ctm_compensation' in auth and 'clip_constraint' in auth and auth['graphics_state_contract'] == CONTRACT
    s = auth['graphics_state_scope']
    assert binding['scope'] == dict(outer=where(s['outer']), inner=where(s['inner']))
    assert list(binding['scope']) == ['outer', 'inner']


# -- lifecycle ------------------------------------------------------------------------

def _states(path, page, binding, d):
    """Interpreted state in the block, after its Q, after the inner and the outer Q, and for the named texts."""
    content = ContentPage(path, page)
    try:
        start, end, scope = binding['start'], binding['end'], binding['scope']
        inside = [e for e in content.events if start < e.operator.start < end]
        closing = next(b for b in content.boundaries if b.operator.end == end - len(markers(d)[1]) - 1)
        at = {b.operator.start: b for b in content.boundaries}
        inner, outer = at[scope['inner']['matching']['start']], at[scope['outer']['matching']['start']]
        named = {name: [e for e in content.events if ''.join(c.text for c in e.chars) == name]
                 for name in ('TAIL', 'MIDDLE', 'OUTER')}
        return inside, closing, inner, outer, named
    finally:
        content.close()


def assert_restored(out, binding, d):
    """1. block Q -> boundary state; 2. inner Q -> outer scope state; 3. outer Q -> page-level state."""
    auth, chain = d['authority'], d['authority']['graphics_state_scope']
    inside, closing, inner, outer, named = _states(out, d['page'], binding, d)
    # In the block: the witnessed state, the CTM cancelled if needed, one q deeper.
    assert inside and {e.state.ctm for e in inside} == {block_ctm(d)}
    assert all(clip_state(e.state.clip) == auth['graphics_state']['clip'] for e in inside)
    # After the block's Q, still in the inner scope: the boundary state, q depth 2.
    assert closing.operator.name == 'Q' and closing.q_depth == 2 and closing.operator.start < inner.operator.start
    assert destinations._state(closing) == auth['graphics_state']
    tail = named['TAIL']
    assert len(tail) == 1 and list(tail[0].state.ctm) == auth['graphics_state']['ctm']
    assert clip_state(tail[0].state.clip) == auth['graphics_state']['clip']
    # After the inner Q: the outer scope's state, q depth 1 (its fill, stroke and width; no inner CTM).
    assert inner.operator.name == 'Q' and inner.q_depth == 1
    assert destinations._state(inner) == chain['inner']['restored_state']
    middle = named['MIDDLE']
    assert len(middle) == 1 and middle[0].state.ctm == IDENTITY and middle[0].state.fill == OUTER_FILL
    assert clip_state(middle[0].state.clip) == chain['inner']['restored_state']['clip']
    assert middle[0].state.stroke == ('RG', ('1', '0', '0')) and middle[0].state.other.get('w') == '[2]'
    # After the outer Q: the page-level state, q depth 0.
    assert outer.operator.name == 'Q' and outer.q_depth == 0 and inner.operator.start < outer.operator.start
    assert destinations._state(outer) == chain['outer']['restored_state']
    last = named['OUTER']
    assert len(last) == 1 and last[0].state.ctm == IDENTITY and last[0].state.clip == ()
    assert last[0].state.fill == PAGE_FILL and last[0].state.other.get('w') == '[0.5]'


@pytest.mark.parametrize('name', VARIANTS)
def test_lifecycle_keeps_the_block_inside_its_chain_through_edits_and_noops(tmp_path, name):
    page2 = variant(name)
    source, state, chosen = flow(tmp_path, page2)
    ident, chosen = 'dest-R2', chosen['R2']
    d = state['continuation_destinations'][ident]
    sid, witnessed = slot_id(d), chosen['graphics_state_scope']
    assert d['authority']['graphics_state_scope'] == witnessed
    confirmed = state['destination_bindings'][ident]['scope']
    assert confirmed == dict(outer=where(witnessed['outer']), inner=where(witnessed['inner']))
    source, state, _ = saved(tmp_path, source, state, change(state, 'AB'), 'fits')
    assert 'end' not in state['destination_bindings'][ident] and state['destination_bindings'][ident]['scope'] == confirmed
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
        # Both q stay put; both Q moved only by the block's growth.
        data, scope = program(out, 2), binding['scope']
        for level in ('outer', 'inner'):
            q, Q = scope[level]['opening'], scope[level]['matching']
            assert data[q['start']:q['end']] == b'q' and data[Q['start']:Q['end']] == b'Q'
            assert q == confirmed[level]['opening']
            assert Q['start'] == confirmed[level]['matching']['start'] + len(block)
        outer, inner = scope['outer'], scope['inner']
        assert (outer['opening']['end'] <= inner['opening']['start'] and inner['opening']['end'] <= binding['start']
                < binding['end'] <= inner['matching']['start'] and inner['matching']['end'] <= outer['matching']['start'])
        ops, matching = matching_of(data)
        ordinal = {op.start: i for i, op in enumerate(ops)}
        for level in (outer, inner):
            assert matching[ordinal[level['opening']['start']]] == ordinal[level['matching']['start']]
        body = block[len(markers(d)[0]):len(block) - len(markers(d)[1])]
        forbidden = {'W', 'W*', 'n', 're'} | (set() if 'ctm_compensation' in d['authority'] else {'cm'})
        assert block.startswith(head(d)) and not {op.name for op in operators(body)} & forbidden
        assert audit(data)['violations'] == [] and audit(body)['violations'] == []
        assert_restored(out, binding, d)
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


@pytest.mark.parametrize('name', GEOMETRY)
def test_the_block_in_a_chain_draws_where_page_entry_does(tmp_path, name):
    page2 = variant(name)
    outputs = {}
    for kind in ('entry', 'chain'):
        source, state, _ = flow(tmp_path / kind, page2, {'R2': (kind, REGION)})
        out, state, report = saved(tmp_path / kind, source, state, change(state, LONG), 'grow')
        fields = ('unicode', 'glyph_id', 'origin', 'size', 'code', 'cid')
        outputs[kind] = dict(source=source, out=out, glyphs=glyphs(out), pixels=pixels(out), state=state,
            plan=[[{k: g[k] for k in fields} for g in step['report']['glyph_plan']] for step in report['steps']])
    entry, mine = outputs['entry'], outputs['chain']
    # Planned page coordinates, saved glyph origins and rendered pixels.
    assert mine['plan'] == entry['plan'] and mine['glyphs'] == entry['glyphs'] and len(mine['glyphs']) > 20
    assert mine['pixels'] == entry['pixels']
    d = mine['state']['continuation_destinations']['dest-R2']
    assert d['authority']['graphics_state_scope']['depth'] == 2
    assert ('ctm_compensation' in d['authority']) == (GEOMETRY[name][0] is not None)
    assert ('clip_constraint' in d['authority']) == (GEOMETRY[name][1] is not None)
    assert_restored(mine['out'], mine['state']['destination_bindings']['dest-R2'], d)
    # Inside and after the chain, everything else paints what the source paints.
    for path in (entry['out'], mine['out']):
        assert glyphs(path, bounds=(180, 0, 320, 260)) == glyphs(mine['source'], bounds=(180, 0, 320, 260))
        assert drawings(path) == drawings(mine['source'])
        assert outside(pixels(path), REGION['bounds']) == outside(pixels(mine['source']), REGION['bounds'])


def test_a_block_at_the_end_of_the_inner_scope_closes_before_the_inner_q(tmp_path):
    """The boundary right before the inner matching Q: the block is the inner scope's last content."""
    page2 = variant('compensated-clip')
    source, state, chosen = flow(tmp_path, page2, following='Q')
    for step, text in [('grow', LONG), ('noop', None)]:
        out, state, _ = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        binding = state['destination_bindings']['dest-R2']
        data = program(out, 2)
        # Only whitespace separates the block's own end from the inner matching Q.
        assert data[binding['end']:binding['scope']['inner']['matching']['start']].strip() == b''
        d = state['continuation_destinations']['dest-R2']
        inside, closing, inner, outer, _ = _states(out, 2, binding, d)
        assert closing.q_depth == 2 and inner.q_depth == 1 and outer.q_depth == 0
        chain = d['authority']['graphics_state_scope']
        assert destinations._state(inner) == chain['inner']['restored_state']
        assert destinations._state(outer) == chain['outer']['restored_state']
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
    source, state, _ = flow(tmp_path, page2)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    return page2, out, state


def test_a_changed_chain_record_is_not_the_same_authority(tmp_path):
    _, out, state = _grown(tmp_path)
    assert open_shared_flow(out, tmp_path / 'grow.json')['status'] == 'restored'
    chain = lambda a: a['graphics_state_scope']
    level = lambda name: lambda a: chain(a)[name]

    def swapped(a):
        chain(a)['outer'], chain(a)['inner'] = chain(a)['inner'], chain(a)['outer']
    edits = {
        # Each of the four operators is part of the ID.
        'inner-q-ordinal': (lambda a: level('inner')(a)['opening'].update(ordinal=level('inner')(a)['opening']['ordinal'] + 1),
                            'ID differs'),
        'inner-q-bytes': (lambda a: level('inner')(a)['opening'].update(sha256='0' * 64), 'ID differs'),
        'inner-Q-end': (lambda a: level('inner')(a)['matching'].update(end=level('inner')(a)['matching']['end'] + 1),
                        'ID differs'),
        'outer-q-ordinal': (lambda a: level('outer')(a)['opening'].update(ordinal=level('outer')(a)['opening']['ordinal'] - 1),
                            'ID differs'),
        'outer-Q-bytes': (lambda a: level('outer')(a)['matching'].update(sha256='0' * 64), 'ID differs'),
        'levels-swapped': (swapped, 'ID differs'),
        # The states each Q restores, the policy and the contract.
        'inner-restored': (lambda a: level('inner')(a)['restored_state'].update(fill=['g', ['0']]), 'q ... Q scope differs'),
        'outer-restored': (lambda a: level('outer')(a)['restored_state'].update(fill=['g', ['0']]), 'q ... Q scope differs'),
        'policy': (lambda a: chain(a).update(policy=SCOPE), 'q ... Q scope differs'),
        'contract': (lambda a: chain(a).update(contract=SCOPE_CONTRACT), 'q ... Q scope differs'),
        # The form: another depth, a missing level, or no chain at all.
        'depth-1': (lambda a: chain(a).update(depth=1), 'q ... Q scope differs'),
        'depth-3': (lambda a: chain(a).update(depth=3), 'q ... Q scope differs'),
        'inner-removed': (lambda a: chain(a).pop('inner'), 'malformed'),
        'removed': (lambda a: a.pop('graphics_state_scope'), 'ID differs'),
        'state-contract': (lambda a: a.update(graphics_state_contract=CONTRACT.replace('a confirmed chain of two nested',
                                                                                      'one confirmed')),
                           'graphics-state contract differs'),
        'boundary-depth': (lambda a: a['boundary']['scope'].update(q_depth=1), 'state or scope differs'),
    }
    for name, (edit, message) in edits.items():
        opened = open_shared_flow(out, _forged(state, edit))
        assert opened['status'] == 'needs_confirmation' and message in opened['reason'], (name, opened)
    # This revision's binding names where each level's q and Q are.
    binding = lambda s: s['destination_bindings']['dest-R2']
    moves = {
        'inner-q': lambda s: binding(s)['scope']['inner']['opening'].update(start=binding(s)['scope']['inner']['opening']['start'] + 1),
        'inner-Q': lambda s: binding(s)['scope']['inner']['matching'].update(start=binding(s)['scope']['inner']['matching']['start'] - 1),
        'outer-q': lambda s: binding(s)['scope']['outer']['opening'].update(start=binding(s)['scope']['outer']['opening']['start'] + 1),
        'outer-Q': lambda s: binding(s)['scope']['outer']['matching'].update(start=binding(s)['scope']['outer']['matching']['start'] - 1),
        'levels-swapped': lambda s: binding(s).update(scope=dict(outer=binding(s)['scope']['inner'],
                                                                 inner=binding(s)['scope']['outer'])),
        'depth-1-form': lambda s: binding(s).update(scope=binding(s)['scope']['inner']),
    }
    for name, edit in moves.items():
        bad = deepcopy(state)
        edit(bad)
        opened = open_shared_flow(out, _reseal(bad))
        assert opened['status'] == 'needs_confirmation' and 'not in its confirmed q ... Q scope' in opened['reason'], (
            name, opened)
    bad = deepcopy(state)
    binding(bad).pop('scope')
    opened = open_shared_flow(out, _reseal(bad))
    assert opened['status'] == 'needs_confirmation' and 'needs its scope location' in opened['reason']
    # Control: resealing alone changes nothing.
    assert open_shared_flow(out, _reseal(deepcopy(state)))['status'] == 'restored'


def _swap(data, old, new):
    assert data.count(old) == 1 and len(old) == len(new), old
    return data.replace(old, new)


def _slot(data, slot, value):
    return _swap(data, slot, value.ljust(len(slot)))


def test_another_q_or_q_with_the_same_bytes_is_another_chain(tmp_path):
    page2, out, state = _grown(tmp_path)
    side = tmp_path / 'grow.json'
    d, binding = state['continuation_destinations']['dest-R2'], state['destination_bindings']['dest-R2']
    data = program(out, 2)
    cm = CTMS['rotation'] + b' cm\n'
    paint_e = _rectangle(IDENTITY, PAINT_E)
    middle, outer_text = text_line(MIDDLE, b'MIDDLE'), text_line(OUTER, b'OUTER')
    blank = lambda value, spans: b''.join(
        [value[:spans[0]['start']]] + [b' ' + value[a['end']:b['start']] for a, b in zip(spans, spans[1:])]
        + [b' ' + value[spans[-1]['end']:]])
    block = data[binding['start']:binding['end']]
    without = data[:binding['start']] + data[binding['end']:]
    past_inner = without.index(b'Q\n' + OUTER_SUFFIX_SLOT) + 2
    past_outer = without.index(b'Q\n' + outer_text) + 2
    scope = binding['scope']
    variants = {
        # The same bytes, depth and state, but another q or Q at one level.
        'inner-q-moved': (_swap(data, b'q\n' + cm, cm + b'q\n'), 'scope'),
        'inner-Q-moved': (_swap(data, b'Q\n' + OUTER_SUFFIX_SLOT + b'\n' + middle,
                                OUTER_SUFFIX_SLOT + b'\n' + middle + b'Q\n'), 'not in its confirmed q ... Q scope'),
        'outer-q-moved': (_swap(data, b'q\n' + paint_e + b'\n', paint_e + b'\nq\n'), 'not in its confirmed q ... Q scope'),
        'outer-Q-moved': (_swap(data, b'Q\n' + outer_text, outer_text + b'Q\n'), 'not in its confirmed q ... Q scope'),
        # A Q q after the boundary: the inner q, or the outer q, now matches another Q.
        'inner-correspondence': (_slot(data, INNER_SLOT, b'Q q'), 'not in its confirmed q ... Q scope'),
        'outer-correspondence': (_slot(data, OUTER_SUFFIX_SLOT, b'Q q'), 'not in its confirmed q ... Q scope'),
        # A Q q before the boundary: the boundary is in another inner, or outer, scope.
        'other-inner-scope': (_slot(data, SCOPE_SLOT, b'Q q'), 'scope'),
        'other-outer-scope': (_slot(data, OUTER_SLOT, b'Q q'), 'scope'),
        # Depth 3, depth 1, and a Q without its q.
        'deeper': (_slot(_slot(data, SCOPE_SLOT, b'q'), INNER_SLOT, b'Q'), 'state or scope differs'),
        'shallower': (blank(data, [scope['outer']['opening'], scope['outer']['matching']]), 'state or scope differs'),
        'unbalanced': (blank(data, [scope['inner']['matching']]), 'unsupported'),
        # The block moved past the inner, or the outer, matching Q.
        'block-past-inner-Q': (without[:past_inner] + block + without[past_inner:], 'operators|scope'),
        'block-past-outer-Q': (without[:past_outer] + block + without[past_outer:], 'operators|scope'),
    }
    for name, (value, message) in variants.items():
        assert value != data, name
        tampered = _tampered(out, 2, value, name + '.pdf')
        content = ContentPage(tampered, 2)
        try:
            with pytest.raises(PdfError, match=message):
                destinations.page_witness(content, [d], {'dest-R2'}, scopes={'dest-R2': scope})
        finally:
            content.close()
        assert open_shared_flow(tampered, side)['status'] == 'needs_confirmation', name
    # Control: the untouched revision, witnessed the same way.
    content = ContentPage(out, 2)
    try:
        current, _ = destinations.page_witness(content, [d], {'dest-R2'}, scopes={'dest-R2': scope})
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


# Which of the four operators a rewrite of paragraph A's source slot moves.
MOVES = {'before': {'outer-q', 'inner-q', 'inner-Q', 'outer-Q'}, 'outer-prefix': {'inner-q', 'inner-Q', 'outer-Q'},
         'inner-prefix': {'inner-Q', 'outer-Q'}, 'inner-suffix': {'inner-Q', 'outer-Q'},
         'outer-suffix': {'outer-Q'}, 'after': set()}


@pytest.mark.parametrize('where', MOVES)
def test_same_transaction_edits_around_the_chain_keep_it(tmp_path, where, monkeypatch):
    """Paragraph A's source slot sits before, in or after either level; each save rewrites it.

    Offsets and ordinals move; each of the four operators follows the
    mutation map on its own, and the boundary stays in the same chain.
    """
    import test_scope_chain_boundary as module
    real = module.make_selection
    monkeypatch.setattr(module, 'make_selection', lambda source, *a, glyph_ids, **k: real(
        source, *a, glyph_ids=_source_ids(source, 1), **k))
    page = variant('identity', source=where)
    source, state, chosen = flow(tmp_path, page, same_page=True)
    chosen = chosen['R2']
    confirmed = state['destination_bindings']['dest-R2']['scope']
    shifts = []
    for step, text in [('grow', LONG), ('noop', None)]:
        out, state, _ = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        d, binding = state['continuation_destinations']['dest-R2'], state['destination_bindings']['dest-R2']
        assert d['authority']['graphics_state_scope'] == chosen['graphics_state_scope']
        data, scope = program(out, 1), binding['scope']
        assert b'(ABCD) Tj' not in data
        ops, matching = matching_of(data)
        ordinal = {op.start: i for i, op in enumerate(ops)}
        for level in ('outer', 'inner'):
            q, Q = scope[level]['opening'], scope[level]['matching']
            assert data[q['start']:q['end']] == b'q' and data[Q['start']:Q['end']] == b'Q'
            assert matching[ordinal[q['start']]] == ordinal[Q['start']]
        assert (scope['outer']['opening']['end'] <= scope['inner']['opening']['start']
                and scope['inner']['opening']['end'] <= binding['start'] < binding['end']
                <= scope['inner']['matching']['start'] and scope['inner']['matching']['end']
                <= scope['outer']['matching']['start'])
        assert_restored(out, binding, d)
        # Beyond the block's own growth (both Qs follow the block), an operator
        # moves exactly when the rewritten slot precedes it.
        block = binding['end'] - binding['start']
        shifts.append({f'{level}-{name}': scope[level][key]['start'] - confirmed[level][key]['start']
                       - (block if key == 'matching' else 0)
                       for level in ('outer', 'inner') for key, name in (('opening', 'q'), ('matching', 'Q'))})
        source = out
    for shift in shifts:
        assert {name for name, value in shift.items() if value} == MOVES[where], (where, shift)


def test_a_mutation_consuming_any_of_the_four_operators_leaves_no_successor():
    data = variant('identity')
    ops, matching = matching_of(data)
    qs = [i for i, op in enumerate(ops) if op.name == 'q']
    outer, inner = ((ops[q], ops[matching[q]]) for q in qs)
    location = dict(outer=dict(opening=dict(start=outer[0].start, end=outer[0].end),
                               matching=dict(start=outer[1].start, end=outer[1].end)),
                    inner=dict(opening=dict(start=inner[0].start, end=inner[0].end),
                               matching=dict(start=inner[1].start, end=inner[1].end)))
    # Insertions in front of each operator move it, and everything after it.
    program_map = MutationProgram(data)
    for op, text in ((outer[0], b'% a\n'), (inner[0], b'% bb\n'), (inner[1], b'% ccc\n'), (outer[1], b'% dddd\n')):
        program_map.add(Mutation(op.start, op.start, text, kind='mutation'))
    shifted = lambda op, by: dict(start=op.start + by, end=op.end + by)
    assert carried_scope(program_map, location) == dict(
        outer=dict(opening=shifted(outer[0], 4), matching=shifted(outer[1], 4 + 5 + 6 + 7)),
        inner=dict(opening=shifted(inner[0], 4 + 5), matching=shifted(inner[1], 4 + 5 + 6)))
    # A mutation consuming any one of them, alone or with its neighbors, crosses an edge of the chain.
    for op in (outer[0], inner[0], inner[1], outer[1]):
        for start, end in ((op.start, op.end), (op.start - 2, op.end + 1)):
            program_map = MutationProgram(data)
            program_map.add(Mutation(start, end, b'X', kind='mutation'))
            with pytest.raises(PdfError, match='consumed'):
                carried_scope(program_map, location)


# -- rollback -------------------------------------------------------------------------

@pytest.mark.parametrize('phase', ['rebind', 'commit'])
def test_late_failure_in_a_chain_publishes_nothing(tmp_path, monkeypatch, phase):
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


# -- depth 0 and depth 1 keep their form ----------------------------------------------

def test_boundaries_outside_the_inner_scope_keep_their_form(tmp_path):
    """Beside a chain: a page-level boundary has no scope record, one in the outer scope the depth-1 form."""
    source = build(tmp_path, SOURCE_PAGE, variant('compensated-clip'))
    found = inspect_continuation_boundaries(source, 2)['candidates']
    level = lambda depth: [c for c in found if c['scope']['q_depth'] == depth]
    # After paint A (page level) and after paint G (outer scope, under its clip, before the outer Q).
    page_level = next(c for c in level(0) if c['previous']['operator'] == 'f')
    in_outer = next(c for c in level(1) if c['previous']['operator'] == 'f' and c['next']['operator'] == 'Q')
    for rid, chosen, bounds in (('P0', page_level, [18, 20, 60, 30]), ('P1', in_outer, [18, 90, 60, 100])):
        d = confirm_continuation_destination(source, destination_id=rid, paragraph_id='A', region_id=rid, page=2,
            bounds=bounds, insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=chosen['boundary_id'])
        scope = d['authority'].get('graphics_state_scope')
        if chosen is page_level:
            assert scope is None and d['authority']['graphics_state_contract'].startswith('witnessed page-level state')
        else:
            assert list(scope) == ['policy', 'depth', 'opening', 'matching', 'restored_state', 'contract']
            assert scope['policy'] == SCOPE and scope['depth'] == 1 and scope['contract'] == SCOPE_CONTRACT
            assert d['authority']['graphics_state_contract'].startswith('witnessed state inside one confirmed q ... Q scope')
            assert d['authority']['graphics_state_contract'].endswith("which closes before the scope's matching Q")
            content = ContentPage(source, 2)
            try:
                assert destinations.witness(content, d)['scope'] == where(scope)
            finally:
                content.close()
