"""A confirmed page-program boundary under a nonidentity CTM.

The block cancels the witnessed CTM M with the inverse N its authority
records, ``q N cm BT ... ET Q``: its glyphs land where a page-entry block
draws them, and its Q restores M, so every paint of the suffix keeps M. Only
the CTM is relaxed here: nested q scopes (one is test_scope_boundary's), a
clip not proven to be one page rectangle (see test_clip_boundary) or an
ExtGState still refuses the boundary, and a singular or numerically
unstable CTM has no inverse.
"""
from copy import deepcopy
from fractions import Fraction
import math

import pymupdf
import pytest
from pypdf import PdfReader

from pdfeditor import continuation as destinations
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, multiply, operators
from pdfeditor.continuation import (BOUNDARY, BOUNDARY_STATE, COMPENSATION, COMPENSATION_TOLERANCE, CREATE,
                                    OPERATOR_NESTING, block_ctm, compensation, confirm_continuation_destination,
                                    inspect_continuation_boundaries, markers, slot_id, source_ctms)
from pdfeditor.document_flow import _reseal
from pdfeditor.operator_nesting import audit
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import _contract, confirm_shared_flow, edit_shared_flow, open_shared_flow
from test_boundary_destination import (DESTINATION_PAGE, PREFIX_SLOT, REGION, SOURCE_PAGE, SUFFIX_SLOT, _story,
                                       _tampered, build, first_operator, last_operator, program, split)
from test_continuation import LONG, change, saved

LONGER = LONG + ' Fifteen sixteen seventeen eighteen nineteen twenty.'
IDENTITY = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
# Page CTMs as the source writes them. Every page below draws the same
# picture in page space, whatever its CTM: the source placed each paint with
# the CTM's inverse.
CTMS = {'translation': b'1 0 0 1 30 -20',
        'scale': b'2 0 0 0.5 -40 30',
        'rotation': b'0.866025403784 0.5 -0.5 0.866025403784 100 -40',
        'skew': b'1 0 0.25 1 -20 0',
        'reflection': b'1 0 0 -1 0 260'}
# Paint A (prefix, blue) and paint C (suffix, green) are rectangles, TAIL
# (suffix) is text; all in page space (y up), to the right of REGION.
PAINT_A, TAIL, PAINT_C = (200, 200, 260, 230), (200, 40), (260, 100, 300, 140)


def _values(text):
    return tuple(float(v) for v in text.split())


def _inverse(m):
    a, b, c, d, e, f = m
    det = a * d - b * c
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def _then(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D, c * A + d * C, c * B + d * D, e * A + f * C + E, e * B + f * D + F)


def _number(value):
    text = f'{value:.6f}'.rstrip('0').rstrip('.')
    return '0' if text in ('', '-0') else text


def _rectangle(inverse, box):
    x0, y0, x1, y1 = box
    corners = [_then((0, 0, 0, 0, x, y), inverse)[4:] for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    return (' '.join(f'{_number(x)} {_number(y)} {op}' for (x, y), op in zip(corners, 'mlll')) + ' h f').encode()


def painted(ctm, *, prefix=b''):
    """Page 2 under ``ctm``: paint A, the candidate boundary, then TAIL and paint C."""
    inverse = _inverse(_values(ctm.decode()))
    tail = _then((1, 0, 0, 1, *TAIL), inverse)
    return (PREFIX_SLOT + b'\n' + prefix + ctm + b' cm\n0.5 w 0 0 1 rg ' + _rectangle(inverse, PAINT_A)
            + b'\nBT /Regular 12 Tf ' + ' '.join(map(_number, tail)).encode() + b' Tm (TAIL) Tj ET\n0 1 0 rg '
            + _rectangle(inverse, PAINT_C) + b'\n' + SUFFIX_SLOT)


def pick(source, page, *, ctm):
    """The caller's explicit choice: the one candidate right after a paint, under ``ctm``."""
    found = [c for c in inspect_continuation_boundaries(source, page)['candidates']
             if c['previous']['operator'] == 'f' and (c['graphics_state']['ctm'] == IDENTITY) == (ctm is None)]
    assert len(found) == 1, found
    return found[0]


def confirmed(tmp_path, page2, areas):
    """Paragraph A (page 1) continues into confirmed page-2 areas.

    ``areas`` maps region IDs to (kind, geometry): 'entry', 'identity' (the
    boundary after the identity-CTM paint) or 'compensated' (after the paint
    under the page CTM).
    """
    source = build(tmp_path, SOURCE_PAGE, page2)
    font = tmp_path / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    p = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(4)), explicit_width=150))
    regions = {'R1': dict(page=1, bounds=[18, 40, 173, 78], x=20, width=150, first_baseline=60)}
    chosen, authorities = {}, {}
    for rid, (kind, geometry) in areas.items():
        regions[rid] = dict(geometry, page=2)
        common = dict(destination_id='dest-' + rid, paragraph_id='A', region_id=rid, page=2, bounds=geometry['bounds'])
        if kind == 'entry':
            d = confirm_continuation_destination(source, insertion='before-page-program',
                                                 graphics_state='isolated-pdf-initial-state', **common)
        else:
            chosen[rid] = pick(source, 2, ctm=None if kind == 'identity' else True)
            d = confirm_continuation_destination(source, insertion=BOUNDARY, graphics_state=BOUNDARY_STATE,
                                                 boundary=chosen[rid]['boundary_id'], **common)
        authorities[d['destination_id']] = d
    state = confirm_shared_flow(source, {'A': _story(source, font, p)}, flow_id='flow', paragraph_order=['A'],
        regions=regions, region_order=list(regions), slot_regions={'A': {'original': 'R1'}},
        paragraph_policies={'A': dict(min_line_height=22, first_line_indent=5, keep_together=False, break_before='auto',
                                      break_after='auto', empty=dict(kind='reserve-line', ascent=10, descent=3))},
        follows=[], protected_regions={}, continuation_destinations=authorities)
    return source, state, chosen


def compensated(tmp_path, name):
    return confirmed(tmp_path, painted(CTMS[name]), {'R2': ('compensated', REGION)})


def grown(tmp_path, name='rotation'):
    source, state, chosen = compensated(tmp_path, name)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    return source, out, state, chosen['R2']


def head(d):
    """The first bytes of a compensated block: its markers, q and the recorded inverse."""
    return markers(d)[0] + b'q ' + d['authority']['ctm_compensation']['operator'].encode() + b' BT '


def glyphs(path, page=2, bounds=REGION['bounds']):
    """Painted glyph origins inside ``bounds`` in page (MuPDF) coordinates, in trace order."""
    with pymupdf.open(path) as doc:
        return [(chr(c[0]), c[1], c[2]) for span in doc[page - 1].get_texttrace() for c in span['chars']
                if pymupdf.Rect(bounds).contains(pymupdf.Point(c[2]))]


def pixels(path, page=2):
    with pymupdf.open(path) as doc:
        pixmap = doc[page - 1].get_pixmap(dpi=144)
        return pixmap.width, pixmap.n, pixmap.samples


def outside(image, bounds, scale=2):
    """Samples of ``image`` with the pixels of ``bounds`` (plus one pixel) left out."""
    width, n, samples = image
    x0, y0, x1, y1 = (int(math.floor(bounds[0] * scale)) - 1, int(math.floor(bounds[1] * scale)) - 1,
                      int(math.ceil(bounds[2] * scale)) + 1, int(math.ceil(bounds[3] * scale)) + 1)
    rows = []
    for y in range(len(samples) // (width * n)):
        row = samples[y * width * n:(y + 1) * width * n]
        rows.append(row if not y0 <= y < y1 else row[:max(x0, 0) * n] + row[x1 * n:])
    return rows


def drawings(path, page=2):
    with pymupdf.open(path) as doc:
        return [(d['fill'], [str(item) for item in d['items']]) for d in doc[page - 1].get_drawings()]


def displacement(ctm, box):
    """How far ``ctm`` moves the corners of ``box`` (user space), at most."""
    x0, y0, x1, y1 = box
    return max(math.hypot(x * ctm[0] + y * ctm[2] + ctm[4] - x, x * ctm[1] + y * ctm[3] + ctm[5] - y)
               for x in (x0, x1) for y in (y0, y1))


# -- 1 to 4. the proof ---------------------------------------------------------

@pytest.mark.parametrize('name', CTMS)
def test_an_invertible_ctm_has_a_written_inverse_proven_close_to_the_identity(name):
    page_transform, page_bounds = [1, 0, 0, -1, 0, 260], [0, 0, 320, 260]
    source = source_ctms(CTMS[name] + b' cm')[0]
    ctm = list(multiply(_values(CTMS[name].decode()), (1, 0, 0, 1, 0, 0)))
    value, reason = compensation(ctm, source, page_transform, page_bounds)
    assert reason is None and value['policy'] == COMPENSATION and value['confirmed_ctm'] == ctm
    # One cm operator of exactly the recorded operands, in PDF syntax (no exponent).
    ops = list(operators(value['operator'].encode()))
    assert [op.name for op in ops] == ['cm'] and [float(a) for a in ops[0].args] == [float(v) for v in value['matrix']]
    assert value['operator'] == ' '.join(value['matrix']) + ' cm' and not any('e' in v for v in value['matrix'])
    # Exact residual of the written inverse, the bound, and binary32 as this engine composes.
    proof = value['proof']
    assert proof['page_box'] == [0, 0, 320, 260] and proof['source_ctm'] == [float(v) for v in source]
    assert max(abs(a - b) for a, b in zip(proof['residual'], IDENTITY)) < 1e-9
    assert 0 < proof['displacement_bound'] <= proof['tolerance'] == COMPENSATION_TOLERANCE
    # On this 320 x 260 pt page every example stays within half the tolerance.
    assert proof['displacement_bound'] <= COMPENSATION_TOLERANCE / 2
    composed = multiply(tuple(map(float, value['matrix'])), tuple(ctm))
    assert displacement(composed, proof['page_box']) <= proof['displacement_bound']
    # Nothing but the confirmed CTM and the page decides the value.
    assert compensation(ctm, source, page_transform, page_bounds) == (value, None)


@pytest.mark.parametrize('ctm,reason', [
    ((1, 2, 2, 4, 0, 0), 'singular-ctm'),
    ((0, 0, 0, 0, 0, 0), 'singular-ctm'),
    ((1, 0, 0, 0, 5, 5), 'singular-ctm'),
    ((math.inf, 0, 0, 1, 0, 0), 'nonfinite-ctm'),
    ((math.nan, 0, 0, 1, 0, 0), 'nonfinite-ctm'),
    # Ill-conditioned: N·M composed in binary32 may land far from the identity.
    ((1000, 999, 999, 998, 0, 0), 'numerically-unstable-ctm'),
    ((1, 0, 0, 1, 1e6, 0), 'numerically-unstable-ctm'),
    # Its inverse is no binary32-safe PDF number.
    ((1e-12, 0, 0, 1e-12, 0, 0), 'numerically-unstable-ctm'),
])
def test_a_singular_nonfinite_or_unstable_ctm_has_no_inverse(ctm, reason):
    source = tuple(map(Fraction, ctm)) if all(map(math.isfinite, ctm)) else None
    assert compensation(list(ctm), source, [1, 0, 0, -1, 0, 260], [0, 0, 320, 260]) == (None, reason)


def test_a_boundary_ctm_is_proven_against_the_source_operands_too():
    """Two programs that differ below binary32 resolution interpret to the same
    CTM; the proof still tells them apart (a binary64 renderer sees both)."""
    near = b'0.866025403785 0.5 -0.5 0.866025403784 100 -40'
    same = [list(multiply(_values(t.decode()), (1, 0, 0, 1, 0, 0))) for t in (CTMS['rotation'], near)]
    assert same[0] == same[1]
    values = [compensation(same[0], source_ctms(t + b' cm')[0], [1, 0, 0, -1, 0, 260], [0, 0, 320, 260])[0]
              for t in (CTMS['rotation'], near)]
    assert values[0]['matrix'] == values[1]['matrix'] and values[0]['proof'] != values[1]['proof']


# -- inspection: only the CTM is relaxed (5, 6) ------------------------------------

@pytest.mark.parametrize('name', CTMS)
def test_a_boundary_under_an_invertible_ctm_is_a_candidate_with_its_inverse(tmp_path, name):
    source = build(tmp_path, SOURCE_PAGE, painted(CTMS[name]))
    chosen = pick(source, 2, ctm=True)
    value = chosen['ctm_compensation']
    assert value['confirmed_ctm'] == chosen['graphics_state']['ctm'] != IDENTITY
    assert chosen['reasons'] == [] and chosen['z_order']['prefix_paint_operators'] == 1
    assert chosen['z_order']['suffix_paint_operators'] == 2
    d = confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R2', page=2,
        bounds=REGION['bounds'], insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=chosen['boundary_id'])
    auth = d['authority']
    assert auth['ctm_compensation'] == value and auth['isolation'] == 'q-cm-BT-ET-Q'
    assert auth['graphics_state']['ctm'] == value['confirmed_ctm'] and 'cancels the witnessed CTM' in auth[
        'graphics_state_contract']
    # An identity boundary keeps the PR #11 authority: no compensation, no cm.
    identity = pick(build(tmp_path / 'identity', SOURCE_PAGE, DESTINATION_PAGE), 2, ctm=None)
    assert 'ctm_compensation' not in identity


@pytest.mark.parametrize('ctm,wrap,reasons', [
    (b'1 2 2 4 0 0', None, ['singular-ctm']),
    (b'1000 999 999 998 0 0', None, ['numerically-unstable-ctm']),
    (b'1 0 0 1 30 -20', (b'0 0 100 100 re 150 150 100 100 re W n ', b''), ['nonrectangular-clip']),
    (b'1 0 0 1 30 -20', (b'q q ', b' Q Q'), ['nested-graphics-state-save']),
    (b'1 0 0 1 30 -20', (b'/GS0 gs ', b''), ['transparency', 'extgstate']),
    (b'1 0 0 1 30 -20', (b'/P BMC ', b' EMC'), ['inside-marked-content']),
])
def test_everything_but_the_ctm_still_refuses_the_boundary(tmp_path, ctm, wrap, reasons):
    after = b'0 0 5 5 re f'
    before, closing = wrap or (b'', b'')
    data = before + ctm + b' cm ' + after + closing + b' 0 g 5 5 5 5 re f'
    source = build(tmp_path, data)
    offset = data.index(after) + len(after)
    found = inspect_continuation_boundaries(source, 1, include_refused=True)
    boundary = next(b for b in found['refused'] if b['offset'] == offset)
    assert boundary['reasons'] == reasons
    with pytest.raises(PdfError, match=reasons[-1]):
        confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R1', page=1,
            bounds=[100, 100, 200, 200], insertion=BOUNDARY, graphics_state=BOUNDARY_STATE,
            boundary=boundary['boundary_id'])


# -- 7, 8. page coordinates of the block, CTM of the suffix -------------------------

@pytest.mark.parametrize('name', CTMS)
def test_the_block_draws_in_page_entry_coordinates_and_the_suffix_keeps_its_ctm(tmp_path, name):
    page2 = painted(CTMS[name])
    outputs = {}
    for kind in ('entry', 'compensated'):
        source, state, chosen = confirmed(tmp_path / kind, page2, {'R2': (kind, REGION)})
        out, state, report = saved(tmp_path / kind, source, state, change(state, LONG), 'grow')
        fields = ('unicode', 'glyph_id', 'origin', 'size', 'code', 'cid')
        outputs[kind] = dict(source=source, out=out, state=state, glyphs=glyphs(out), pixels=pixels(out),
            plan=[[{k: g[k] for k in fields} for g in step['report']['glyph_plan']] for step in report['steps']])
    entry, mine = outputs['entry'], outputs['compensated']
    d = mine['state']['continuation_destinations']['dest-R2']
    ctm = d['authority']['graphics_state']['ctm']
    # The same glyphs at the same page coordinates as the page-entry block,
    # and as a block at an identity-CTM boundary of another page.
    assert mine['plan'] == entry['plan'] and mine['glyphs'] == entry['glyphs'] and len(mine['glyphs']) > 20
    source, state, _ = confirmed(tmp_path / 'identity', DESTINATION_PAGE, {'R2': ('identity', REGION)})
    identity, _, _ = saved(tmp_path / 'identity', source, state, change(state, LONG), 'grow')
    assert glyphs(identity) == mine['glyphs']
    # Page-entry and compensated boundary output render the same page.
    assert mine['pixels'] == entry['pixels']
    prefix, block, suffix = split(mine['out'], mine['state'], 'dest-R2')
    assert block.startswith(head(d)) and block.endswith(b' ET Q\n' + markers(d)[1])
    assert suffix.lstrip(b'\n') == page2[len(prefix):].lstrip(b'\n') and prefix == page2[:len(prefix)]
    content = ContentPage(mine['out'], 2)
    try:
        start, end = len(prefix), len(prefix) + len(block)
        inside = [e for e in content.events if e.chars and start < e.operator.start < end]
        tail = [e for e in content.events if ''.join(c.text for c in e.chars) == 'TAIL']
        closing = next(b for b in content.boundaries if b.operator.end == end - len(markers(d)[1]) - 1)
        opening = next(b for b in content.boundaries if b.operator.name == 'cm' and start < b.operator.start < end)
    finally:
        content.close()
    # Inside: the witnessed CTM cancelled by the recorded inverse, as composed here.
    assert inside and {e.state.ctm for e in inside} == {block_ctm(d)} and opening.q_depth == 1
    assert displacement(block_ctm(d), d['authority']['ctm_compensation']['proof']['page_box']) <= COMPENSATION_TOLERANCE
    # After the block's Q: the witnessed CTM again, at q depth 0, for every suffix paint.
    assert closing.operator.name == 'Q' and closing.q_depth == 0 and list(closing.state.ctm) == ctm
    assert len(tail) == 1 and list(tail[0].state.ctm) == ctm and tail[0].operator.start > end
    # The suffix and the prefix paint exactly what the source paints.
    for path in (entry['out'], mine['out']):
        assert glyphs(path, bounds=(180, 0, 320, 260)) == glyphs(mine['source'], bounds=(180, 0, 320, 260))
        assert drawings(path) == drawings(mine['source'])
        assert outside(pixels(path), REGION['bounds']) == outside(pixels(mine['source']), REGION['bounds'])
    # Control: had the inverse leaked past the boundary, the suffix would move.
    leaked = _tampered(mine['out'], 2, prefix + b'\n' + d['authority']['ctm_compensation']['operator'].encode()
                       + b'\n' + suffix, 'leaked.pdf')
    assert glyphs(leaked, bounds=(0, 0, 320, 260)) != glyphs(mine['source'], bounds=(0, 0, 320, 260))
    assert drawings(leaked) != drawings(mine['source'])


# -- 9 to 13, 16, 17. lifecycle ----------------------------------------------------

@pytest.mark.parametrize('name', ['translation', 'scale', 'rotation'])
def test_lifecycle_keeps_the_compensated_block_through_edits_and_noops(tmp_path, name):
    source, state, chosen = compensated(tmp_path, name)
    page2 = program(source, 2)
    ident = 'dest-R2'
    d = state['continuation_destinations'][ident]
    sid, value = slot_id(d), chosen['R2']['ctm_compensation']
    assert d['authority']['ctm_compensation'] == value
    source, state, _ = saved(tmp_path, source, state, change(state, 'AB'), 'fits')
    assert 'end' not in state['destination_bindings'][ident]
    assert state['destination_bindings'][ident]['boundary']['offset'] == chosen['R2']['offset']
    creation = previous = None
    for step, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('shorten', 'AB'),
                       ('regrow', LONG), ('noop1', None), ('noop2', None), ('noop3', None)]:
        out, updated, report = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        # Reopened: the same confirmed CTM and inverse, the same creation.
        d = updated['continuation_destinations'][ident]
        assert d['authority']['ctm_compensation'] == value and d == state['continuation_destinations'][ident]
        slot = updated['slots'][sid]
        creation = creation or slot['creation_binding']
        assert slot['creation_binding'] == creation and creation['mutation']['kind'] == CREATE
        assert creation['mutation']['start'] == chosen['R2']['offset'] and 'insertion_order' not in creation['mutation']
        binding = updated['destination_bindings'][ident]
        assert binding['operator_nesting'] == OPERATOR_NESTING
        prefix, block, suffix = split(out, updated, ident)
        assert prefix == page2[:chosen['R2']['offset']] and suffix.lstrip(b'\n') == page2[len(prefix):].lstrip(b'\n')
        assert last_operator(prefix) == 'f' and first_operator(suffix) == 'BT'
        # One cm in the block: the recorded inverse, outside its text object.
        assert block.startswith(head(d)) and block.count(b' cm ') == 1
        assert audit(program(out, 2))['violations'] == [] and audit(block[len(markers(d)[0]):])['violations'] == []
        content = ContentPage(out, 2)
        try:
            inside = [e for e in content.events if binding['start'] < e.operator.start < binding['end']]
            closing = next(b for b in content.boundaries if b.operator.end == binding['end'] - len(markers(d)[1]) - 1)
        finally:
            content.close()
        assert inside and {e.state.ctm for e in inside} == {block_ctm(d)}
        assert list(closing.state.ctm) == d['authority']['graphics_state']['ctm'] and closing.q_depth == 0
        if step == 'shorten':
            assert slot['occupancy'] is None and not glyphs(out)
        else:
            assert slot['occupancy'] is not None and len(glyphs(out)) > 20
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


# -- 14. tampering ------------------------------------------------------------------

def test_the_block_validator_admits_only_the_recorded_inverse():
    begin, end = markers(dict(destination_id='d', paragraph_id='A', region_id='R2', page=2))
    sid = slot_id(dict(destination_id='d', paragraph_id='A', region_id='R2', page=2))
    inverse = b'1 0 0 1 -30 20 cm'

    def block(head, tail=b' /F 12 Tf 1 0 0 1 20 150 Tm (a) Tj ET Q\n'):
        return begin + head + tail + end
    good = block(b'q ' + inverse + b' BT')
    assert destinations._block(good, sid, 0, compensation=inverse) == (len(good), {'/F'})
    bad = [(good, None),                                               # a cm the identity authority has not
           (block(b'q BT'), inverse),                                  # the recorded inverse is missing
           (good, b'1 0 0 1 -30 21 cm'),                               # another inverse
           (block(b'q BT BT'), b'BT'),                                 # not a cm at all
           (block(b'q ' + inverse + b' 1 0 0 1 0 0 cm BT'), inverse),  # a second cm before the text object
           (block(b'q ' + inverse + b' BT', b' 1 0 0 1 0 0 cm (a) Tj ET Q\n'), inverse),  # one inside it
           (block(b'q ' + inverse + b' BT', b' (a) Tj ET\n'), inverse)]                  # no Q of its own
    for data, recorded in bad:
        with pytest.raises(PdfError):
            destinations._block(data, sid, 0, compensation=recorded)
    # Blocks written before PDF 1.x nesting never cancel a CTM.
    with pytest.raises(PdfError, match='inverse CTM'):
        destinations._block(good, sid, 0, legacy=True, compensation=inverse)


def _forged(state, edit):
    """A sidecar whose authority is edited and whose contract and checksum match again."""
    bad = deepcopy(state)
    edit(bad['continuation_destinations']['dest-R2']['authority'])
    bad['contract_sha256'] = _contract(bad)
    return _reseal(bad)


def _other(auth):
    """An internally consistent authority for another CTM."""
    ctm = [1.0, 0.0, 0.0, 1.0, 31.0, -20.0]
    value, _ = compensation(ctm, source_ctms(b'1 0 0 1 31 -20 cm')[0], auth['page_transform'], auth['page_bounds'])
    auth['graphics_state']['ctm'] = ctm
    auth['ctm_compensation'] = value


def test_a_changed_ctm_or_inverse_is_not_the_same_authority(tmp_path):
    source, out, state, chosen = grown(tmp_path)
    side = tmp_path / 'grow.json'
    assert open_shared_flow(out, side)['status'] == 'restored'
    d = state['continuation_destinations']['dest-R2']
    operator = d['authority']['ctm_compensation']['operator']
    edits = {
        'confirmed-ctm': (lambda a: a['graphics_state']['ctm'].__setitem__(4, 101.0), 'state or scope differs'),
        'recorded-ctm': (lambda a: a['ctm_compensation']['confirmed_ctm'].__setitem__(4, 101.0), 'compensation differs'),
        'matrix': (lambda a: a['ctm_compensation']['matrix'].__setitem__(4, '-66'), 'compensation differs'),
        'operator': (lambda a: a['ctm_compensation'].update(operator=operator.replace(' cm', '1 cm')), 'inverse CTM'),
        'proof': (lambda a: a['ctm_compensation']['proof'].update(displacement_bound=0.0), 'compensation differs'),
        'source-ctm': (lambda a: a['ctm_compensation']['proof']['source_ctm'].__setitem__(4, 101.0),
                       'compensation differs'),
        'policy': (lambda a: a['ctm_compensation'].update(policy='none'), 'compensation differs'),
        'isolation': (lambda a: a.update(isolation='q-BT-ET-Q'), 'compensation differs'),
        'removed': (lambda a: a.pop('ctm_compensation'), 'not one isolated q BT'),
        'other-ctm': (_other, 'inverse CTM'),
    }
    for name, (edit, message) in edits.items():
        opened = open_shared_flow(out, _forged(state, edit))
        assert opened['status'] == 'needs_confirmation' and message in opened['reason'], (name, opened)
    # The PDF: the block's cm, the source CTM.
    data = program(out, 2)
    written = b'q ' + operator.encode() + b' BT'
    assert data.count(written) == 1
    digits = operator.split()[4].encode()
    variants = {
        'inverse-operand': (data.replace(written, written.replace(digits, digits[:-1] + b'0' if digits[-1:] != b'0'
                                                                   else digits[:-1] + b'1')), 'inverse CTM'),
        'inverse-removed': (data.replace(written, b'q ' + b' ' * (len(written) - 5) + b' BT'), 'inverse CTM'),
        'second-cm': (data.replace(b' 0 Tc 0 Tw 0 Ts ', b' 1 0 0 1 0 0 cm ', 1), 'foreign operator'),
        # Below binary32 resolution: the same interpreted CTM, not the same source CTM.
        'source-operand': (data.replace(CTMS['rotation'], CTMS['rotation'].replace(b'784 100', b'785 100'), 1),
                           'compensation differs'),
        'source-ctm': (data.replace(PREFIX_SLOT, b'1 0 0 1 5 5 cm'.ljust(len(PREFIX_SLOT)), 1), 'state or scope differs'),
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
    # An identity boundary's block never holds a cm.
    source, state, _ = confirmed(tmp_path / 'identity', DESTINATION_PAGE, {'R2': ('identity', REGION)})
    out, state, _ = saved(tmp_path / 'identity', source, state, change(state, LONG), 'grow')
    d = state['continuation_destinations']['dest-R2']
    data = program(out, 2)
    assert data.count(markers(d)[0] + b'q BT 0 Tc 0 Tw 0 Ts ') == 1
    value = data.replace(markers(d)[0] + b'q BT 0 Tc 0 Tw 0 Ts ', markers(d)[0] + b'q 1 0 0 1 0 0 cm BT ', 1)
    assert len(value) == len(data) and value != data
    content = ContentPage(_tampered(out, 2, value, 'identity-cm.pdf'), 2)
    try:
        with pytest.raises(PdfError, match='not one isolated q BT'):
            destinations.page_witness(content, [d], {'dest-R2'})
    finally:
        content.close()


# -- 15. several authorities on one page --------------------------------------------

AREAS = {'DA': dict(bounds=[18, 90, 173, 160], x=20, width=150, first_baseline=110),
         'DB': dict(bounds=[18, 170, 173, 250], x=20, width=150, first_baseline=190)}


@pytest.mark.parametrize('first', ['entry', 'identity'])
def test_a_compensated_boundary_shares_a_page_with_another_authority(tmp_path, first):
    """DA is a page-entry destination or an identity-CTM boundary before the
    page CTM is set; DB is the boundary under it."""
    page2 = painted(CTMS['rotation'], prefix=b'0.5 w 1 0 0 rg 200 245 20 10 re f\n')
    areas = {'DA': (first, AREAS['DA']), 'DB': ('compensated', AREAS['DB'])}
    source, state, chosen = confirmed(tmp_path, page2, areas)
    sids = {r: slot_id(state['continuation_destinations']['dest-' + r]) for r in areas}
    for step, text in [('grow', LONGER), ('shorten', LONG), ('regrow', LONGER), ('noop', None)]:
        out, state, report = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        da, db = (state['continuation_destinations']['dest-' + r] for r in areas)
        data = program(out, 2)
        a, b = (state['destination_bindings']['dest-' + r] for r in areas)
        # DA: no cm, at page entry or before the page CTM; DB: the recorded inverse, after it.
        assert data[a['start']:a['end']].startswith(markers(da)[0] + b'q BT ') and b' cm ' not in data[a['start']:a['end']]
        assert data[b['start']:b['end']].startswith(head(db))
        cm = data.index(CTMS['rotation'] + b' cm')
        assert a['end'] <= cm < b['start'] and (a['start'] == 0) == (first == 'entry')
        assert 'ctm_compensation' not in da['authority'] and db['authority']['ctm_compensation'] == chosen['DB'][
            'ctm_compensation']
        content = ContentPage(out, 2)
        try:
            ctms = {r: {e.state.ctm for e in content.events if w['start'] < e.operator.start < w['end']}
                    for r, w in (('DA', a), ('DB', b))}
        finally:
            content.close()
        assert ctms['DA'] <= {(1, 0, 0, 1, 0, 0)} and ctms['DB'] <= {block_ctm(db)}
        assert ctms['DA'] and (ctms['DB'] or step == 'shorten')
        records = state['generated_fonts']['2']
        owners = {r: {x for x, v in records.items() if v['slot_id'] == sids[r]} for r in areas}
        assert owners['DA'] and owners['DB'] and not owners['DA'] & owners['DB']
        assert audit(data)['violations'] == []
        if step == 'noop':
            assert pixels(out) == pixels(source)
            assert all(set(v.values()) == {'reused'} for v in report['generated_font_outcome'].values())
        source = out
    # The other authority is witnessed on its own.
    content = ContentPage(source, 2)
    try:
        current, _ = destinations.page_witness(content, [da], {'dest-DA'},
                                               locations=None)
    finally:
        content.close()
    assert current['dest-DA'] == state['destination_bindings']['dest-DA']


# -- 18. rollback ------------------------------------------------------------------

@pytest.mark.parametrize('phase', ['rebind', 'commit'])
def test_late_failure_after_the_compensated_block_publishes_nothing(tmp_path, monkeypatch, phase):
    import pdfeditor.shared_flow as shared
    source, state, _ = compensated(tmp_path, 'rotation')
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
    assert PdfReader(source).pages[1].get_contents().get_data() == painted(CTMS['rotation'])
