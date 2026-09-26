"""A confirmed page-program boundary under an inherited rectangular clip.

A clip set at page level cannot be ended by the block's own q ... Q, so the
block inherits it unchanged and never writes W, W* or n. Only a clip proven
to be one page rectangle is inherited; the destination bounds and every
generated glyph must lie inside it. PR #12's compensation cancels the CTM,
never the clip. Every other condition of a page-level boundary still holds.
"""
from copy import deepcopy
import hashlib
import math
from types import SimpleNamespace

import pymupdf
import pytest
from pypdf import PdfReader

from pdfeditor import continuation as destinations
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import (BOUNDARY, BOUNDARY_STATE, CLIP, COMPENSATION_TOLERANCE, CREATE, INK_MARGIN,
                                    OPERATOR_NESTING, PAINT_PADDING, block_ctm, clip_contains, clip_state,
                                    confirm_continuation_destination, inspect_continuation_boundaries, markers,
                                    require_inside_clip, slot_id)
from pdfeditor.document_flow import _reseal
from pdfeditor.operator_nesting import audit
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import _contract, confirm_shared_flow, edit_shared_flow, open_shared_flow, plan_shared_flow
from test_boundary_destination import (PREFIX_SLOT, REGION, SOURCE_PAGE, SUFFIX_SLOT, _story,
                                       _tampered, build, first_operator, last_operator, program, split)
from test_continuation import LONG, change, saved
from test_ctm_compensation import (CTMS, _inverse, _number, _rectangle, _then, _values, drawings, glyphs, outside,
                                   pixels)

IDENTITY = (1, 0, 0, 1, 0, 0)
# Page space with y up (the identity CTM's user space) on a 320 x 260 pt page:
# page (MuPDF) coordinates are (x, 260 - y). The clip is page [10, 80, 230,
# 250]; REGION ([18, 90, 173, 220]) lies inside it.
CLIP_BOX = (10, 10, 230, 180)
PAGE_CLIP = [10, 80, 230, 250]
PAINT_A = (200, 200, 260, 230)  # prefix, before the clip: all of it shows
PAINT_B = (190, 0, 210, 20)     # prefix, under the clip: crosses its bottom edge
TAIL = (190, 140)               # suffix text, inside the clip
PAINT_C = (220, 100, 260, 140)  # suffix: crosses the clip's right edge
FENCE = ('W', 'W*', 'n', 're')  # clip operators a block never writes
CONTRACT = ('witnessed page-level state; the block sets its own font, text state and fill and restores every '
            'parameter with its own q ... Q')
COMPENSATED_CONTRACT = ('witnessed page-level state; the block cancels the witnessed CTM with the recorded inverse, '
                        'sets its own font, text state and fill and restores every parameter with its own q ... Q')


def _re(inverse, box):
    """``box`` as ``x y w h re`` in the user space of a CTM without rotation whose inverse is ``inverse``."""
    (x0, y0), (x1, y1) = (_then((0, 0, 0, 0, x, y), inverse)[4:] for x, y in (box[:2], box[2:]))
    return ' '.join(map(_number, (x0, y0, x1 - x0, y1 - y0))).encode() + b' re'


def clipped(ctm=None, *, order='clip-cm', prefix=b''):
    """Page 2: paint A, the clip and the CTM, paint B, the candidate boundary, TAIL and paint C.

    Every paint lands at the same page coordinates whatever the CTM: the
    source places each with the CTM's inverse. ``clip-cm`` sets the clip
    under the identity CTM before ``ctm``; ``cm-clip`` sets it under ``ctm``.
    """
    inverse = _inverse(_values(ctm.decode())) if ctm else IDENTITY
    cm = ctm + b' cm\n' if ctm else b''
    if order == 'clip-cm':
        head = b'0.5 w 0 0 1 rg ' + _rectangle(IDENTITY, PAINT_A) + b'\n' + _re(IDENTITY, CLIP_BOX) + b' W n\n' + cm
    else:
        head = cm + b'0.5 w 0 0 1 rg ' + _rectangle(inverse, PAINT_A) + b'\n' + _re(inverse, CLIP_BOX) + b' W n\n'
    tail = _then((1, 0, 0, 1, *TAIL), inverse)
    return (PREFIX_SLOT + b'\n' + prefix + head + _rectangle(inverse, PAINT_B)
            + b'\nBT /Regular 12 Tf ' + ' '.join(map(_number, tail)).encode() + b' Tm (TAIL) Tj ET\n0 1 0 rg '
            + _rectangle(inverse, PAINT_C) + b'\n' + SUFFIX_SLOT)


def pick(source, page=2):
    """The caller's explicit choice: the one candidate under the clip right after a paint."""
    found = [c for c in inspect_continuation_boundaries(source, page)['candidates']
             if c['previous']['operator'] == 'f' and 'clip_constraint' in c]
    assert len(found) == 1, found
    return found[0]


def flow(tmp_path, page2, areas=None, *, same_page=False):
    """Paragraph A (page 1) continues into confirmed page-2 areas.

    ``areas`` maps region IDs to ('entry' | 'clip', geometry); by default one
    area, REGION, at the boundary under the clip. With ``same_page``, page 1
    is paragraph A's source followed by ``page2``, and the areas are on it.
    """
    areas = areas or {'R2': ('clip', REGION)}
    source = build(tmp_path, SOURCE_PAGE + b'\n' + page2) if same_page else build(tmp_path, SOURCE_PAGE, page2)
    page = 1 if same_page else 2
    font = tmp_path / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    p = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(4)), explicit_width=150))
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
            chosen[rid] = pick(source, page)
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


def own_paints(path, state, ident='dest-R2'):
    """The renderer's paint envelope of each glyph of the destination's generated slot."""
    d = state['continuation_destinations'][ident]
    owned = state['slots'][slot_id(d)]['binding']['paragraph']['selection']['glyph_ids']
    content = ContentPage(path, d['page'])
    try:
        log = content.page.get_bboxlog()
        return [log[s] for s in sorted({content.actual[i]['span']['seqno'] for i in owned})]
    finally:
        content.close()


def near(values, expected, tolerance):
    return all(abs(a - b) <= tolerance for a, b in zip(values, expected))


# -- inspection -------------------------------------------------------------------

def test_a_proven_rectangular_clip_is_a_candidate_with_its_constraint(tmp_path):
    source = build(tmp_path, SOURCE_PAGE, clipped())
    chosen = pick(source)
    c = chosen['clip_constraint']
    assert chosen['reasons'] == [] and chosen['status'] == 'safe' and 'ctm_compensation' not in chosen
    # The clip state names rule and path; where it was set is witnessed by bytes.
    assert chosen['graphics_state']['clip'] == [
        dict(rule='W', path=[dict(operator='re', args=[10.0, 10.0, 220.0, 170.0], ctm=list(IDENTITY))])]
    assert c['policy'] == CLIP and len(c['clips']) == 1
    clip = c['clips'][0]
    assert clip['rule'] == 'W' and clip['operands'] == [10.0, 10.0, 220.0, 170.0] and clip['ctm'] == list(IDENTITY)
    assert [(o['operator'], o['sha256']) for o in clip['operators']] == [
        (name, hashlib.sha256(text).hexdigest()) for name, text in (('re', b'10 10 220 170 re'), ('W', b'W'), ('n', b'n'))]
    assert clip['rectangle'] == clip['source_rectangle'] == PAGE_CLIP
    # The certified rectangle: the exact one, shrunk by the binary32 rounding bound.
    bound = c['proof']['rounding_bound']
    assert c['proof']['arithmetic'] == 'binary32' and 0 < bound < 1e-3 and bound == clip['rounding_bound']
    assert near(c['rectangle'], [10 + bound, 80 + bound, 230 - bound, 250 - bound], 1e-12)
    assert all(a > b if i < 2 else a < b for i, (a, b) in enumerate(zip(c['rectangle'], PAGE_CLIP)))
    assert c['containment']['ink_margin'] == INK_MARGIN and c['containment']['destination'] == 'bounds-inside-rectangle'
    # The boundaries before the clip have none; nothing but the program decides it.
    before = [x for x in inspect_continuation_boundaries(source, 2)['candidates'] if 'clip_constraint' not in x]
    assert [x['previous']['operator'] for x in before] == ['w', 'rg', 'f']
    assert all(x['graphics_state']['clip'] == [] and x['offset'] < chosen['offset'] for x in before)
    assert pick(source) == chosen


CLIP_CASES = [
    # Proven: one rectangle, either rule, or an intersection of rectangles.
    (b'10 10 50 50 re W n', b'', [], [10, 200, 60, 250]),
    (b'10 10 50 50 re W* n', b'', [], [10, 200, 60, 250]),
    (b'10 10 50 50 re W n 30 0 100 30 re W* n', b'', [], [30, 230, 60, 250]),
    (b'2 0 0 0.5 -40 30 cm 20 0 60 200 re W n', b'', [], [0, 130, 120, 230]),
    # Several subpaths, a polygon (even one that is a rectangle) or a curve.
    (b'10 10 50 50 re 100 100 50 50 re W n', b'', ['nonrectangular-clip'], None),
    (b'10 10 m 60 10 l 60 60 l 10 60 l h W n', b'', ['nonrectangular-clip'], None),
    (b'10 10 m 60 10 60 60 10 60 c h W n', b'', ['nonrectangular-clip'], None),
    # A rectangle under rotation or skew is no page rectangle (the CTM itself could be cancelled).
    (b'0.866025403784 0.5 -0.5 0.866025403784 100 -40 cm 10 10 50 50 re W n', b'', ['rotated-clip'], None),
    (b'1 0 0.25 1 -20 0 cm 10 10 50 50 re W n', b'', ['rotated-clip'], None),
    (b'0 1 -1 0 300 0 cm 10 10 50 50 re W n', b'', ['rotated-clip'], None),
    # No area, or not set by one re W n.
    (b'10 10 50 50 re W n 100 100 50 50 re W n', b'', ['empty-clip'], None),
    (b'10 10 0 50 re W n', b'', ['empty-clip'], None),
    (b'10 10 50 50 re W f', b'', ['unproven-clip'], None),
    (b'W 10 10 50 50 re n', b'', ['unproven-clip'], None),
    (b'BT /Regular 12 Tf 7 Tr 20 200 Td (A) Tj ET 0 Tr', b'', ['text-clip'], None),
    # Everything else still refuses, whatever the clip.
    (b'q 10 10 50 50 re W n', b' Q', ['inside-graphics-state-save'], [10, 200, 60, 250]),
    (b'10 10 50 50 re W n /GS0 gs', b'', ['transparency', 'extgstate'], [10, 200, 60, 250]),
    (b'/P BMC 10 10 50 50 re W n', b' EMC', ['inside-marked-content'], [10, 200, 60, 250]),
    (b'BX 10 10 50 50 re W n', b' EX', ['inside-compatibility-section'], [10, 200, 60, 250]),
]


@pytest.mark.parametrize('before,after,reasons,rectangle', CLIP_CASES)
def test_only_a_clip_proven_to_be_one_page_rectangle_is_inherited(tmp_path, before, after, reasons, rectangle):
    paint = b'0 0 5 5 re f'
    data = before + b' ' + paint + after + b' 0 g'
    source = build(tmp_path, data)
    offset = data.index(paint) + len(paint)
    found = inspect_continuation_boundaries(source, 1, include_refused=True)
    boundary = next(b for b in found['candidates'] + found['refused'] if b['offset'] == offset)
    assert boundary['reasons'] == reasons and boundary['graphics_state']['clip']
    assert (boundary in found['candidates']) == (not reasons)
    if rectangle is None:
        assert 'clip_constraint' not in boundary
    else:
        c = boundary['clip_constraint']
        assert near(c['rectangle'], rectangle, 1e-3) and clip_contains(dict(rectangle=rectangle), c['rectangle'])
    common = dict(destination_id='d', paragraph_id='A', region_id='R1', page=1, bounds=[100, 20, 110, 30],
                  insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=boundary['boundary_id'])
    if reasons:
        with pytest.raises(PdfError, match=reasons[-1]):
            confirm_continuation_destination(source, **common)
    else:
        with pytest.raises(PdfError, match='beyond its inherited rectangular clip'):
            confirm_continuation_destination(source, **common)


def test_pending_path_and_clip_under_an_active_clip_are_refused(tmp_path):
    data = b'10 10 50 50 re W n 20 20 5 5 re W n 0 g'
    source = build(tmp_path, data)
    found = inspect_continuation_boundaries(source, 1, include_refused=True)
    everything = {b['offset']: b for b in found['candidates'] + found['refused']}
    for text, reasons in ((b'20 20 5 5 re', ['pending-path']), (b'20 20 5 5 re W', ['pending-path', 'pending-clip'])):
        offset = data.index(text) + len(text)
        assert everything[offset]['reasons'] == reasons
    # Once painted with n, the second clip intersects the first.
    after = everything[data.index(b'W n 0 g') + 3]
    assert after['reasons'] == [] and near(after['clip_constraint']['rectangle'], [20, 235, 25, 240], 1e-3)


# -- confirmation: destination bounds -----------------------------------------------

def test_the_destination_must_lie_inside_the_certified_rectangle(tmp_path):
    source = build(tmp_path, SOURCE_PAGE, clipped())
    chosen = pick(source)
    x0, y0, x1, y1 = rectangle = chosen['clip_constraint']['rectangle']

    def confirm(bounds):
        return confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R2', page=2,
            bounds=bounds, insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=chosen['boundary_id'])
    # On the rectangle's edge is inside; any amount beyond it is not. The
    # clip's own edge (10, 80, ...) lies beyond it by the rounding bound.
    inside = [[x0, 90, 173, 220], [18, y0, 173, 220], [18, 165, x1, 220], [18, 90, 173, y1]]
    beyond = [[math.nextafter(x0, -math.inf), 90, 173, 220], [18, math.nextafter(y0, -math.inf), 173, 220],
              [18, 165, math.nextafter(x1, math.inf), 220], [18, 90, 173, math.nextafter(y1, math.inf)],
              [PAGE_CLIP[0], 90, 173, 220], [18, PAGE_CLIP[1], 173, 220], [9, 90, 173, 220], [18, 90, 173, 251]]
    authorities = [confirm(bounds)['authority'] for bounds in inside + [REGION['bounds']]]
    # Geometry never selects the boundary: other bounds, the same authority.
    assert all(a == authorities[0] for a in authorities) and authorities[0]['clip_constraint']['rectangle'] == rectangle
    assert authorities[0]['clip_constraint'] == chosen['clip_constraint']
    for bounds in beyond:
        with pytest.raises(PdfError, match='destination extends beyond its inherited rectangular clip'):
            confirm(bounds)


def test_paint_the_clip_hides_is_still_an_obstacle(tmp_path):
    """A red paint hidden by its own clip, inside REGION: invisible, and still paint."""
    hidden = b'q 0 0 1 1 re W n 1 0 0 rg 40 100 60 40 re f Q\n'
    plain, covered = (build(tmp_path / name, SOURCE_PAGE, clipped(prefix=prefix))
                      for name, prefix in (('plain', b''), ('covered', hidden)))
    assert pixels(covered) == pixels(plain)
    chosen = pick(covered)
    with pytest.raises(PdfError, match='intersects a filled vector|fixed paint'):
        confirm_continuation_destination(covered, destination_id='d', paragraph_id='A', region_id='R2', page=2,
            bounds=REGION['bounds'], insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=chosen['boundary_id'])


# -- lifecycle ----------------------------------------------------------------------

LIFECYCLES = {'identity': (None, 'clip-cm'), 'rotation-after-clip': (CTMS['rotation'], 'clip-cm'),
              'scale-before-clip': (CTMS['scale'], 'cm-clip')}


@pytest.mark.parametrize('name', LIFECYCLES)
def test_lifecycle_inherits_the_clip_through_edits_and_noops(tmp_path, name):
    ctm, order = LIFECYCLES[name]
    page2 = clipped(ctm, order=order)
    source, state, chosen = flow(tmp_path, page2)
    ident, chosen = 'dest-R2', chosen['R2']
    d = state['continuation_destinations'][ident]
    sid, clip = slot_id(d), chosen['clip_constraint']
    auth = d['authority']
    assert auth['clip_constraint'] == clip and auth['initial_clip'] == CLIP
    assert ('ctm_compensation' in auth) == (ctm is not None) and 'draws inside the witnessed rectangular clip' in auth[
        'graphics_state_contract']
    assert near(clip['rectangle'], PAGE_CLIP, 1e-3)
    source, state, _ = saved(tmp_path, source, state, change(state, 'AB'), 'fits')
    assert 'end' not in state['destination_bindings'][ident]
    creation = previous = None
    for step, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('shorten', 'AB'),
                       ('regrow', LONG), ('noop1', None), ('noop2', None), ('noop3', None)]:
        out, updated, report = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        # Reopened: the same authority, clip constraint and creation.
        d = updated['continuation_destinations'][ident]
        assert d == state['continuation_destinations'][ident] and d['authority']['clip_constraint'] == clip
        slot = updated['slots'][sid]
        creation = creation or slot['creation_binding']
        assert slot['creation_binding'] == creation and creation['mutation']['kind'] == CREATE
        assert creation['mutation']['start'] == chosen['offset'] and 'insertion_order' not in creation['mutation']
        binding = updated['destination_bindings'][ident]
        assert binding['operator_nesting'] == OPERATOR_NESTING
        prefix, block, suffix = split(out, updated, ident)
        assert prefix == page2[:chosen['offset']] and suffix.lstrip(b'\n') == page2[len(prefix):].lstrip(b'\n')
        assert last_operator(prefix) == 'f' and first_operator(suffix) == 'BT'
        # The block neither ends nor sets a clip: no W, W*, n or path of its own.
        body = block[len(markers(d)[0]):len(block) - len(markers(d)[1])]
        assert block.startswith(head(d)) and not {op.name for op in operators(body)} & set(FENCE)
        assert audit(program(out, 2))['violations'] == [] and audit(body)['violations'] == []
        content = ContentPage(out, 2)
        try:
            inside = [e for e in content.events if binding['start'] < e.operator.start < binding['end']]
            closing = next(b for b in content.boundaries if b.operator.end == binding['end'] - len(markers(d)[1]) - 1)
            tail = [e for e in content.events if ''.join(c.text for c in e.chars) == 'TAIL']
        finally:
            content.close()
        witnessed = d['authority']['graphics_state']
        # Inside: the witnessed clip, which the block's own cm does not change.
        assert inside and {e.state.ctm for e in inside} == {block_ctm(d)}
        assert all(clip_state(e.state.clip) == witnessed['clip'] for e in inside)
        # After the block's Q and for the suffix: the witnessed CTM and clip, at q depth 0.
        assert closing.operator.name == 'Q' and closing.q_depth == 0 and list(closing.state.ctm) == witnessed['ctm']
        assert clip_state(closing.state.clip) == witnessed['clip']
        assert len(tail) == 1 and list(tail[0].state.ctm) == witnessed['ctm']
        assert clip_state(tail[0].state.clip) == witnessed['clip']
        paints = own_paints(out, updated)
        if step == 'shorten':
            assert slot['occupancy'] is None and not glyphs(out) and not paints
        else:
            assert slot['occupancy'] is not None and len(glyphs(out)) > 20 and len(paints) == len(glyphs(out))
            # Planned and saved: every generated glyph inside the certified rectangle.
            assert all(kind == 'fill-text' and clip_contains(clip, rect) for kind, rect in paints)
            plan = [g for s in report['steps'] if s['region_id'] == 'R2' for g in s['report']['glyph_plan']]
            assert plan and all(clip_contains(clip, (*g['origin'], *g['origin']), INK_MARGIN) for g in plan)
        # Generated fonts belong to this slot only; no-ops reuse them.
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


PLACEMENTS = {'identity': (None, 'clip-cm'), 'translation-before-clip': (CTMS['translation'], 'cm-clip'),
              'reflection-before-clip': (CTMS['reflection'], 'cm-clip'), 'rotation-after-clip': (CTMS['rotation'], 'clip-cm'),
              'skew-after-clip': (CTMS['skew'], 'clip-cm'), 'scale-before-clip': (CTMS['scale'], 'cm-clip')}


@pytest.mark.parametrize('name', PLACEMENTS)
def test_the_block_draws_where_page_entry_does_and_the_clip_stays_in_page_space(tmp_path, name):
    ctm, order = PLACEMENTS[name]
    page2 = clipped(ctm, order=order)
    outputs = {}
    for kind in ('entry', 'clip'):
        source, state, chosen = flow(tmp_path / kind, page2, {'R2': (kind, REGION)})
        out, state, report = saved(tmp_path / kind, source, state, change(state, LONG), 'grow')
        fields = ('unicode', 'glyph_id', 'origin', 'size', 'code', 'cid')
        outputs[kind] = dict(source=source, out=out, state=state, chosen=chosen, glyphs=glyphs(out), pixels=pixels(out),
            plan=[[{k: g[k] for k in fields} for g in step['report']['glyph_plan']] for step in report['steps']])
    entry, mine = outputs['entry'], outputs['clip']
    # The same glyphs at the same page coordinates as the page-entry block,
    # which draws before the clip exists: the clip cuts none of them.
    assert mine['plan'] == entry['plan'] and mine['glyphs'] == entry['glyphs'] and len(mine['glyphs']) > 20
    assert mine['pixels'] == entry['pixels']
    # The clip is the same page-space rectangle whatever the CTM, and the
    # suffix still draws under it: paints B and C stay cut where the source cuts them.
    clip = mine['chosen']['R2']['clip_constraint']
    assert near(clip['rectangle'], PAGE_CLIP, 1e-3)
    identity = pick(build(tmp_path / 'identity', SOURCE_PAGE, clipped()))['clip_constraint']
    if order == 'clip-cm':
        assert clip == identity
    for path in (entry['out'], mine['out']):
        assert glyphs(path, bounds=(180, 0, 320, 260)) == glyphs(mine['source'], bounds=(180, 0, 320, 260))
        assert drawings(path) == drawings(mine['source'])
        assert outside(pixels(path), REGION['bounds']) == outside(pixels(mine['source']), REGION['bounds'])
    # Control: without the clip, paints B and C would show beyond it.
    data = program(mine['out'], 2)
    fence = _re(IDENTITY if order == 'clip-cm' else _inverse(_values(ctm.decode())), CLIP_BOX) + b' W n'
    assert data.count(fence) == 1
    unclipped = _tampered(mine['out'], 2, data.replace(fence, fence[:-4] + b'   n'), 'unclipped.pdf')
    assert outside(pixels(unclipped), REGION['bounds']) != outside(pixels(mine['source']), REGION['bounds'])
    content = ContentPage(unclipped, 2)
    try:
        with pytest.raises(PdfError, match='state or scope differs'):
            destinations.page_witness(content, [mine['state']['continuation_destinations']['dest-R2']], {'dest-R2'})
    finally:
        content.close()


# -- generated ink --------------------------------------------------------------------

def test_clip_containment_is_exact_and_grows_ink_by_the_margin():
    # The renderer's paint padding plus the saved-origin tolerance.
    assert INK_MARGIN == PAINT_PADDING + COMPENSATION_TOLERANCE and PAINT_PADDING == 1
    c = dict(rectangle=[8.0, 16.0, 64.0, 128.0])
    assert clip_contains(c, [8.0, 16.0, 64.0, 128.0]) and clip_contains(c, [8.0, 16.0, 8.0, 16.0])
    for side in range(4):
        box = [8.0, 16.0, 64.0, 128.0]
        box[side] = math.nextafter(box[side], -math.inf if side < 2 else math.inf)
        assert not clip_contains(c, box)
    # 1 + 1/256 pt from the edge: inside after growing by the margin; 1 + 1/512 pt: not.
    assert clip_contains(c, [9 + 1 / 256, 17 + 1 / 256, 63 - 1 / 256, 127 - 1 / 256], INK_MARGIN)
    assert not clip_contains(c, [9 + 1 / 512, 17 + 1 / 256, 63 - 1 / 256, 127 - 1 / 256], INK_MARGIN)
    assert not clip_contains(c, [9 + 1 / 256, 17 + 1 / 256, 63 - 1 / 256, 127 - 1 / 512], INK_MARGIN)
    assert not any(clip_contains(c, box) for box in ([math.nan, 16, 64, 128], [8, 16, math.inf, 128]))
    d = dict(bounds=[10, 20, 60, 120], authority=dict(clip_constraint=c))
    require_inside_clip(d, [[10, 20, 60, 120]])
    with pytest.raises(PdfError, match='ink may extend beyond'):
        require_inside_clip(d, [[10, 20, 60, 120], [9.001, 20, 60, 120]])
    with pytest.raises(PdfError, match='destination extends beyond'):
        require_inside_clip(dict(d, bounds=[7.999, 20, 60, 120]))
    # A destination without a clip constraint has nothing to lie inside.
    require_inside_clip(dict(bounds=[0, 0, 1000, 1000], authority={}), [[-5, -5, 2000, 2000]])


def _edge(tmp_path, offset, text):
    """A region whose left edge is ``offset`` from the certified rectangle, and ``text`` in it."""
    page2 = clipped()
    x0 = pick(build(tmp_path / 'probe', SOURCE_PAGE, page2))['clip_constraint']['rectangle'][0]
    region = dict(REGION, bounds=[x0 + offset, 90, 173, 220], x=x0 + offset, width=150)
    source, state, _ = flow(tmp_path, page2, {'R2': ('clip', region)})
    return source, state, change(state, text), x0


# 'A' of the test font has no side bearings: its ink starts exactly at its origin.
EDGE_TEXT = ' '.join(['Abc'] * 24)


@pytest.mark.parametrize('offset', [0, 1], ids=['on-the-edge', 'within-the-margin'])
def test_generated_ink_the_clip_may_cut_is_refused(tmp_path, offset):
    """On the edge, the ink is inside the clip but its paint envelope is not;
    one point inside, the envelope touches the edge but the saved origin may move."""
    source, state, edits, x0 = _edge(tmp_path, offset, EDGE_TEXT)
    for attempt in (lambda: plan_shared_flow(source, state, edits),
                    lambda: edit_shared_flow(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', edits)):
        with pytest.raises(PdfError, match='generated glyph ink may extend beyond its inherited rectangular clip'):
            attempt()
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()
    assert open_shared_flow(source, state)['status'] == 'restored'


def test_generated_ink_clear_of_the_margin_is_written(tmp_path):
    source, state, edits, x0 = _edge(tmp_path, 1.01, EDGE_TEXT)
    out, state, report = saved(tmp_path, source, state, edits, 'grow')
    plan = [g for s in report['steps'] if s['region_id'] == 'R2' for g in s['report']['glyph_plan']]
    assert len([g for g in plan if g['unicode'] == 'A' and abs(g['origin'][0] - (x0 + 1.01)) < 1e-9]) >= 3
    # The renderer's envelope of each generated glyph: its outline box and one
    # point of padding, 1/100 pt from the certified rectangle's edge.
    clip = state['continuation_destinations']['dest-R2']['authority']['clip_constraint']
    paints = own_paints(out, state)
    assert paints and all(kind == 'fill-text' and clip_contains(clip, rect) for kind, rect in paints)
    assert 0 < min(rect[0] for _, rect in paints) - clip['rectangle'][0] < .011


def test_the_writer_refuses_ink_on_the_clip_edge_by_itself(tmp_path, monkeypatch):
    """With the planning check disabled, the creating writer still refuses."""
    import pdfeditor.shared_flow as shared
    source, state, edits, _ = _edge(tmp_path, 0, EDGE_TEXT)
    proxy = SimpleNamespace(**{k: getattr(destinations, k) for k in dir(destinations) if not k.startswith('__')})
    proxy.require_inside_clip = lambda *a, **k: None
    monkeypatch.setattr(shared, 'destinations', proxy)
    with pytest.raises(PdfError, match='generated glyph ink may extend beyond its inherited rectangular clip'):
        edit_shared_flow(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', edits)
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()


def test_a_re_edit_whose_ink_reaches_the_clip_edge_is_refused(tmp_path):
    # 'B' starts 1.17 pt right of its origin: lines of it clear the margin.
    source, state, _, _ = _edge(tmp_path, 0, '')
    out, state, _ = saved(tmp_path, source, state, change(state, ' '.join(['Bcd'] * 24)), 'grow')
    edits = change(state, EDGE_TEXT)
    with pytest.raises(PdfError, match='generated glyph ink may extend beyond its inherited rectangular clip'):
        edit_shared_flow(out, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', edits)
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()
    assert open_shared_flow(out, tmp_path / 'grow.json')['status'] == 'restored'


def test_a_saved_paint_beyond_the_clip_is_refused(tmp_path, monkeypatch):
    """Control: had the renderer painted a generated glyph beyond the clip, the revision is refused."""
    source, state, _ = flow(tmp_path, clipped())
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    rectangle = state['continuation_destinations']['dest-R2']['authority']['clip_constraint']['rectangle']
    real = pymupdf.Page.get_bboxlog

    def widened(page, *args, **kwargs):
        # Page 2's text paints left of TAIL: the generated glyphs.
        return [(kind, (rectangle[0] - 1, *rect[1:]) if page.number == 1 and kind == 'fill-text' and rect[0] < 173
                 else rect) for kind, rect in real(page, *args, **kwargs)]
    monkeypatch.setattr(pymupdf.Page, 'get_bboxlog', widened)
    opened = open_shared_flow(out, tmp_path / 'grow.json')
    assert opened['status'] == 'needs_confirmation' and 'saved generated text paint extends beyond' in opened['reason']
    # The same widened paint at a boundary without a clip is only the slot's own paint.
    from test_boundary_destination import flow as plain
    source, state, _ = plain(tmp_path / 'plain')
    out, state, _ = saved(tmp_path / 'plain', source, state, change(state, LONG), 'grow')
    assert open_shared_flow(out, tmp_path / 'plain' / 'grow.json')['status'] == 'restored'


# -- tampering ----------------------------------------------------------------------

def _forged(state, edit):
    bad = deepcopy(state)
    edit(bad['continuation_destinations']['dest-R2']['authority'])
    bad['contract_sha256'] = _contract(bad)
    return _reseal(bad)


def test_a_changed_clip_is_not_the_same_authority(tmp_path):
    source, state, _ = flow(tmp_path, clipped())
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    side = tmp_path / 'grow.json'
    assert open_shared_flow(out, side)['status'] == 'restored'
    d = state['continuation_destinations']['dest-R2']
    constraint = lambda a: a['clip_constraint']
    first = lambda a: a['clip_constraint']['clips'][0]
    edits = {
        'rectangle': (lambda a: constraint(a)['rectangle'].__setitem__(0, 9.0), 'clip constraint differs'),
        'rule': (lambda a: first(a).update(rule='W*'), 'clip constraint differs'),
        'operands': (lambda a: first(a)['operands'].__setitem__(0, 11.0), 'clip constraint differs'),
        'ctm': (lambda a: first(a)['ctm'].__setitem__(4, 1.0), 'clip constraint differs'),
        'operator': (lambda a: first(a)['operators'][0].update(sha256='0' * 64), 'clip constraint differs'),
        'proof': (lambda a: constraint(a)['proof'].update(rounding_bound=0.0), 'clip constraint differs'),
        'margin': (lambda a: constraint(a)['containment'].update(ink_margin=0.0), 'clip constraint differs'),
        'policy': (lambda a: constraint(a).update(policy='none'), 'clip constraint differs'),
        'removed': (lambda a: a.pop('clip_constraint'), 'clip constraint differs'),
        'initial-clip': (lambda a: a.update(initial_clip='page-crop-box'), 'clip constraint differs'),
        'contract': (lambda a: a.update(graphics_state_contract=CONTRACT), 'graphics-state contract differs'),
        'state-rule': (lambda a: a['graphics_state']['clip'][0].update(rule='W*'), 'state or scope differs'),
        'state-path': (lambda a: a['graphics_state']['clip'][0]['path'][0]['args'].__setitem__(0, 11.0),
                       'state or scope differs'),
        'state-removed': (lambda a: a['graphics_state'].update(clip=[]), 'state or scope differs'),
    }
    for name, (edit, message) in edits.items():
        opened = open_shared_flow(out, _forged(state, edit))
        assert opened['status'] == 'needs_confirmation' and message in opened['reason'], (name, opened)
    # The PDF: the clip's rule, geometry, a second clip, no clip, a clip in the block.
    data = program(out, 2)
    fence = b'10 10 220 170 re W n'
    assert data.count(fence) == 1
    shorter = lambda value: value.replace(PREFIX_SLOT, PREFIX_SLOT[:-1], 1)
    variants = {
        'even-odd': (shorter(data.replace(fence, fence[:-2] + b'* n')), 'state or scope differs'),
        'geometry': (data.replace(fence, fence.replace(b'170', b'171')), 'state or scope differs'),
        'second-clip': (data.replace(PREFIX_SLOT, b'50 50 20 20 re W n'.ljust(len(PREFIX_SLOT)), 1),
                        'state or scope differs'),
        'no-clip': (data.replace(fence, fence[:-3] + b'  n'), 'state or scope differs'),
        'clip-in-block': (data.replace(head(d) + b'0 Tc 0 Tw 0 Ts ', head(d) + b'0 0 9 9 re W n ', 1), 'foreign operator'),
    }
    for name, (value, message) in variants.items():
        assert len(value) == len(data) and value != data, name
        tampered = _tampered(out, 2, value, name + '.pdf')
        content = ContentPage(tampered, 2)
        try:
            with pytest.raises(PdfError, match=message):
                destinations.page_witness(content, [d], {'dest-R2'})
        finally:
            content.close()
        assert open_shared_flow(tampered, side)['status'] == 'needs_confirmation', name


# -- another authority on the page ----------------------------------------------------

def test_a_page_entry_block_before_the_clip_leaves_its_authority_alone(tmp_path):
    """DA draws at page entry, before the clip exists; DB at the boundary under it.

    DA's block moves the clip's operators, never their bytes: the clip is
    witnessed by what it is, not where it is.
    """
    areas = {'DA': ('entry', dict(bounds=[18, 90, 173, 150], x=20, width=150, first_baseline=110)),
             'DB': ('clip', dict(bounds=[18, 160, 173, 220], x=20, width=150, first_baseline=180))}
    page2 = clipped(CTMS['rotation'])
    source, state, chosen = flow(tmp_path, page2, areas)
    longer = LONG + ' Fifteen sixteen seventeen eighteen nineteen twenty.'
    fence = b'10 10 220 170 re W n'
    # The clip's n is the program's first n: paints end in f, blocks write none.
    confirmed = [op.name for op in operators(page2)].index('n')
    for step, text in [('grow', longer), ('shorten', LONG), ('regrow', longer), ('noop', None)]:
        out, state, report = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        da, db = (state['continuation_destinations']['dest-' + r] for r in areas)
        a, b = (state['destination_bindings']['dest-' + r] for r in areas)
        data = program(out, 2)
        assert a['start'] == 0 and a['end'] <= data.index(fence) < b['start']
        assert db['authority']['clip_constraint'] == chosen['DB']['clip_constraint']
        assert 'clip_constraint' not in da['authority']
        content = ContentPage(out, 2)
        try:
            events = {r: [e for e in content.events if w['start'] < e.operator.start < w['end']]
                      for r, w in (('DA', a), ('DB', b))}
        finally:
            content.close()
        # DA's glyphs draw before the clip; DB's under the witnessed one,
        # which DA's block has moved to another operator position.
        assert events['DA'] and all(e.state.clip == () for e in events['DA'])
        assert events['DB'] or step == 'shorten'
        for e in events['DB']:
            assert clip_state(e.state.clip) == db['authority']['graphics_state']['clip']
            assert e.state.clip[0]['at'][1] == [op.name for op in operators(data)].index('n') > confirmed
        assert audit(data)['violations'] == []
        if step == 'noop':
            assert pixels(out) == pixels(source)
        source = out


def test_a_source_rewrite_before_the_clip_leaves_its_authority_alone(tmp_path):
    """Paragraph A's own source slot precedes the clip on the page it continues on.

    Each save rewrites that slot in the same transaction, so the clip's
    operators and the boundary move; the block follows by provenance, and
    the clip is still the confirmed one.
    """
    source, state, chosen = flow(tmp_path, clipped(), same_page=True)
    ident, chosen = 'dest-R2', chosen['R2']
    fence = b'10 10 220 170 re W n'
    confirmed = [op.name for op in operators(program(source, 1))].index('n')
    starts = []
    for step, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('noop', None)]:
        out, state, _ = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        d, binding = state['continuation_destinations'][ident], state['destination_bindings'][ident]
        data = program(out, 1)
        prefix = split(out, state, ident)[0]
        assert b'(ABCD) Tj' not in prefix and fence in prefix and last_operator(prefix) == 'f'
        assert d['authority']['clip_constraint'] == chosen['clip_constraint']
        content = ContentPage(out, 1)
        try:
            inside = [e for e in content.events if binding['start'] < e.operator.start < binding['end']]
        finally:
            content.close()
        assert inside and all(clip_state(e.state.clip) == d['authority']['graphics_state']['clip'] for e in inside)
        assert inside[0].state.clip[0]['at'][1] == [op.name for op in operators(data)].index('n') > confirmed
        starts.append(len(prefix))
        source = out
    # The rewritten slot grew before the boundary: the block moved with it.
    assert chosen['offset'] < starts[0] < starts[1] < starts[2]


# -- rollback -----------------------------------------------------------------------

@pytest.mark.parametrize('phase', ['rebind', 'commit'])
def test_late_failure_under_the_clip_publishes_nothing(tmp_path, monkeypatch, phase):
    import pdfeditor.shared_flow as shared
    source, state, _ = flow(tmp_path, clipped(CTMS['rotation']))
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
    assert PdfReader(source).pages[1].get_contents().get_data() == clipped(CTMS['rotation'])


# -- boundaries without a clip keep their PR #11 / #12 form ---------------------------

@pytest.mark.parametrize('name', ['identity', 'rotation'])
def test_a_boundary_without_a_clip_keeps_its_authority_and_block(tmp_path, name):
    from test_boundary_destination import flow as plain
    from test_ctm_compensation import compensated
    source, state, chosen = plain(tmp_path) if name == 'identity' else compensated(tmp_path, name)
    found = inspect_continuation_boundaries(source, 2, include_refused=True)
    assert not any('clip_constraint' in b for b in found['candidates'] + found['refused'])
    d = state['continuation_destinations']['dest-R2']
    auth = d['authority']
    assert 'clip_constraint' not in auth and auth['initial_clip'] == 'page-crop-box'
    assert auth['graphics_state']['clip'] == []
    assert auth['graphics_state_contract'] == (CONTRACT if name == 'identity' else COMPENSATED_CONTRACT)
    assert set(auth) == {'position', 'boundary_id', 'source_program_sha256', 'boundary', 'z_order', 'graphics_state',
                         'graphics_state_contract', 'page_group', 'page_transform', 'page_bounds', 'initial_clip',
                         'isolation', 'font_policy'} | ({'ctm_compensation'} if name == 'rotation' else set())
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    assert set(state['destination_bindings']['dest-R2']) == {'program_sha256', 'boundary', 'start', 'end',
                                                             'block_sha256', 'operator_nesting'}
    _, block, _ = split(out, state, 'dest-R2')
    assert block.startswith(head(d)) and (b' cm ' in block) == (name == 'rotation')
