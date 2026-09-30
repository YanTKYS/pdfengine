"""Exactly three confirmed scopes: six identities, four restorations, no deeper authority."""
from copy import deepcopy

import pytest

from pdfeditor import continuation as destinations
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import (SCOPE_CHAIN, SCOPE_CHAIN_CONTRACT, SCOPE_THREE, SCOPE_THREE_CONTRACT,
                                    boundary_id, carried_scope, inspect_continuation_boundaries, markers, slot_id)
from pdfeditor.document_flow import _reseal
from pdfeditor.mutation import Mutation, MutationProgram
from pdfeditor.operator_nesting import audit
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from test_boundary_destination import REGION, SOURCE_PAGE, _tampered, build, program, split
from test_clip_boundary import CLIP_BOX, IDENTITY, _re
from test_continuation import LONG, change, saved
from test_ctm_compensation import _inverse, _rectangle, _then, _values, drawings, glyphs, outside, pixels
import test_scope_chain_boundary as two
from test_source_ctm_compensation import FRACTIONAL, INVERSE

LEVELS = ('outer', 'middle', 'inner')
EDGES = ('opening', 'matching')
VARIANTS = {'identity': (False, False), 'fractional': (True, False),
            'clip': (False, True), 'fractional-clip': (True, True)}
SLOTS = {name: b'%' + name.encode().ljust(35, b'_') for name in
         ('before', 'outer-prefix', 'middle-prefix', 'inner-prefix',
          'inner-suffix', 'middle-suffix', 'outer-suffix', 'after')}


def triple(name='fractional-clip', *, source=None):
    """Different fill/stroke/width/font/text state at page, outer, middle and inner.

    The middle level sets the clip; the inner sets the decimal CTM. Text and
    paint follow each Q, and all paint is outside the continuation region.
    Source text can sit in any of the eight positions for mutation tests.
    """
    fractional, clipped = VARIANTS[name]
    inverse = _inverse(_values(FRACTIONAL.decode())) if fractional else IDENTITY
    # Give the editable source its confirmed default inline style while
    # preserving the surrounding scope's deliberately different text state.
    source_text = b'q BT /Regular 12 Tf 0 Tc 0 Tw 100 Tz 0 Ts 20 200 Td (ABCD) Tj ET Q\n'
    place = lambda key: SLOTS[key] + b'\n' + (source_text if source == key else b'')
    return (b'0.5 w 0 0 1 rg 0 G BT /Regular 9 Tf 0.1 Tc 0.2 Tw 98 Tz 10 TL 1 Ts ET\n'
            + _rectangle(IDENTITY, two.PAINT_A) + b'\n' + place('before')
            + b'q\n' + place('outer-prefix')
            + b'2 w 1 0 0 RG 0 0.6 0 rg BT /Bold 10 Tf 0.3 Tc 0.4 Tw 97 Tz 11 TL 2 Ts ET\n'
            + b'q\n' + place('middle-prefix')
            + b'3 w 1 J 0 1 0 RG 0.6 0.4 0 rg BT /Regular 11 Tf 0.5 Tc 0.6 Tw 96 Tz 12 TL 3 Ts ET\n'
            + (_re(IDENTITY, CLIP_BOX) + b' W n\n' if clipped else b'')
            + b'q\n' + place('inner-prefix') + (FRACTIONAL + b' cm\n' if fractional else b'')
            + b'4 w 2 j 0 0 1 RG 0.5 0 0.5 rg BT /Bold 13 Tf 0.7 Tc 0.8 Tw 95 Tz 13 TL 4 Ts ET\n'
            + _rectangle(inverse, two.PAINT_B) + b'\n'
            + two.text_line(None, b'TAIL', _then((1, 0, 0, 1, *two.TAIL), inverse))
            + place('inner-suffix') + b'0 1 0 rg ' + _rectangle(inverse, two.PAINT_C) + b'\nQ\n'
            + place('middle-suffix') + two.text_line(two.MIDDLE, b'MIDDLE')
            + _rectangle(IDENTITY, two.PAINT_G) + b'\nQ\n'
            + place('outer-suffix') + two.text_line((200, 180), b'OUTER')
            + _rectangle(IDENTITY, two.PAINT_E) + b'\nQ\n'
            + place('after') + two.text_line(two.OUTER, b'PAGE')
            + _rectangle(IDENTITY, two.PAINT_D) + b'\n')


def pick(source, page=2, following='BT'):
    found = [b for b in inspect_continuation_boundaries(source, page)['candidates']
             if b['scope']['q_depth'] == 3 and b['previous']['operator'] == 'f'
             and b['next']['operator'] == following]
    assert len(found) == 1, found
    return found[0]


def flow(tmp_path, data, *, kind='chain', same_page=False, following='BT'):
    # Reuse the established explicit paragraph/region confirmation fixture.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(two, 'pick', pick)
        if same_page:
            selection = two.make_selection
            patch.setattr(two, 'make_selection', lambda source, *a, glyph_ids, **k:
                          selection(source, *a, glyph_ids=two._source_ids(source, 1), **k))
        return two.flow(tmp_path, data, {'R2': (kind, REGION)}, same_page=same_page, following=following)


def assert_pairing(data, location, start, end):
    ops, matching = two.matching_of(data)
    ordinal = {op.start: i for i, op in enumerate(ops)}
    for level in LEVELS:
        q, Q = (location[level][edge] for edge in EDGES)
        assert data[q['start']:q['end']] == b'q' and data[Q['start']:Q['end']] == b'Q'
        assert matching[ordinal[q['start']]] == ordinal[Q['start']]
    for outer, inner in zip(LEVELS, LEVELS[1:]):
        assert location[outer]['opening']['end'] <= location[inner]['opening']['start']
        assert location[inner]['matching']['end'] <= location[outer]['matching']['start']
    assert location['inner']['opening']['end'] <= start < end <= location['inner']['matching']['start']


def assert_restored(path, binding, d):
    content = ContentPage(path, d['page'])
    try:
        scope, auth = binding['scope'], d['authority']
        at = {b.operator.start: b for b in content.boundaries}
        closing = next(b for b in content.boundaries if b.operator.end == binding['end'] - len(markers(d)[1]) - 1)
        restorations = [(closing, 3, auth['graphics_state'])] + [
            (at[scope[name]['matching']['start']], depth, auth['graphics_state_scope'][name]['restored_state'])
            for name, depth in (('inner', 2), ('middle', 1), ('outer', 0))]
        for b, depth, expected in restorations:
            assert b.operator.name == 'Q' and b.q_depth == depth
            assert destinations._state(b) == expected  # all 15 state fields, including font/text and opacity
            assert set(expected) == set(destinations._state_value(destinations.State()))
        assert [b.operator.start for b, _, _ in restorations] == sorted(b.operator.start for b, _, _ in restorations)
        # Distinct state at every restoration, independent of the CTM/clip variant.
        assert [state['other']['w'] for _, _, state in restorations] == ['[4]', '[3]', '[2]', '[0.5]']
        assert [state['font_size'] for _, _, state in restorations] == [13, 11, 10, 9]
        inside = [e for e in content.events if binding['start'] < e.operator.start < binding['end']]
        assert inside and {e.state.ctm for e in inside} == {destinations.block_ctm(d)}
        assert all(destinations.clip_state(e.state.clip) == auth['graphics_state']['clip'] for e in inside)
        assert {''.join(c.text for c in e.chars) for e in content.events} >= {'TAIL', 'MIDDLE', 'OUTER', 'PAGE'}
    finally:
        content.close()


@pytest.mark.parametrize('name', VARIANTS)
def test_three_levels_are_authority_and_keep_the_existing_ctm_and_clip_proofs(tmp_path, name):
    data = triple(name)
    source = build(tmp_path, SOURCE_PAGE, data)
    chosen = pick(source)
    scope = chosen['graphics_state_scope']
    fractional, clipped = VARIANTS[name]
    assert set(scope) == {'policy', 'depth', 'outer', 'middle', 'inner', 'contract'}
    assert scope['policy'] == SCOPE_THREE and scope['depth'] == 3 and scope['contract'] == SCOPE_THREE_CONTRACT
    for level in LEVELS:
        assert set(scope[level]) == {'opening', 'matching', 'restored_state'}
    location = {level: two.where(scope[level]) for level in LEVELS}
    assert_pairing(data, location, chosen['offset'], chosen['offset'] + 1)
    assert ('ctm_compensation' in chosen) == fractional
    assert ('clip_constraint' in chosen) == clipped
    # The same boundary state/page geometry uses the unchanged PR #19 proof.
    if 'ctm_compensation' in chosen:
        content = ContentPage(source, 2)
        try:
            value, reason = destinations.compensation(chosen['graphics_state']['ctm'],
                destinations.source_ctms(data)[chosen['ordinal']], list(content.page.transformation_matrix), list(content.page.rect))
        finally:
            content.close()
        assert reason is None and value == chosen['ctm_compensation']
        assert value['inverse_basis'] == 'source-decimal-operands' and value['operator'] == INVERSE
        assert value['proof']['source_ctm'][-2:] == ['791/5', '3314/5']
        assert set(value['proof']['models']) == {'source', 'interpreted'}
        assert all(v['displacement_bound'] <= 0.002 for v in value['proof']['models'].values())


@pytest.mark.parametrize('before,at,after,reason', [
    (b'q q q q ', b'0 0 5 5 re f', b' Q Q Q Q', 'nested-graphics-state-save'),
    (b'q q q /GS0 gs ', b'0 0 5 5 re f', b' Q Q Q', 'extgstate'),
    (b'q q q 3 Tr ', b'0 0 5 5 re f', b' Q Q Q', 'text-rendering-mode'),
    (b'q q q 1 2 2 4 0 0 cm ', b'0 0 5 5 re f', b' Q Q Q', 'singular-ctm'),
    (b'q q q 10 10 m 60 10 l 60 60 l h W n ', b'0 0 5 5 re f', b' Q Q Q', 'nonrectangular-clip'),
    (b'q q q ', b'0 0 5 5 re', b' n Q Q Q', 'pending-path'),
    (b'q q q ', b'0 0 5 5 re W', b' n Q Q Q', 'pending-clip'),
    (b'q q q BT /Regular 12 Tf ', b'(A) Tj', b' ET Q Q Q', 'inside-text-object'),
    (b'q q q /P BMC ', b'0 0 5 5 re f', b' EMC Q Q Q', 'inside-marked-content'),
    (b'q q q BX ', b'0 0 5 5 re f', b' EX Q Q Q', 'inside-compatibility-section'),
    (b'q q /P BMC q EMC ', b'0 0 5 5 re f', b' Q Q Q', 'unproven-graphics-state-scope'),
    (b'q q BX q EX ', b'0 0 5 5 re f', b' Q Q Q', 'unproven-graphics-state-scope'),
    (b'q q q BT ', b'0 0 5 5 re f', b' Q ET Q Q', 'invalid-operator-nesting'),
])
def test_depth_four_and_all_other_barriers_still_refuse(tmp_path, before, at, after, reason):
    data = before + at + after + b' 0 g'
    source = build(tmp_path, data)
    found = inspect_continuation_boundaries(source, 1, include_refused=True)
    b = next(b for b in found['refused'] if b['offset'] == len(before + at))
    assert reason in b['reasons']
    with pytest.raises(PdfError):
        destinations.confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R',
            page=1, bounds=[100, 100, 200, 200], insertion=destinations.BOUNDARY,
            graphics_state=destinations.BOUNDARY_STATE, boundary=b['boundary_id'])


def test_id_covers_each_of_six_operators_and_the_level_pairing(tmp_path):
    source = build(tmp_path, SOURCE_PAGE, triple())
    b = pick(source)
    original = b['graphics_state_scope']
    identity = lambda scope: boundary_id(2, b['program_sha256'], b['previous'], b['next'], scope)
    ids = {identity(original)}
    for name in LEVELS:
        for edge in EDGES:
            scope = deepcopy(original)
            scope[name][edge]['ordinal'] += 1
            ids.add(identity(scope))
    for a, c in (('outer', 'middle'), ('middle', 'inner'), ('outer', 'inner')):
        for edge in (None, 'matching'):
            scope = deepcopy(original)
            if edge is None:
                scope[a], scope[c] = scope[c], scope[a]
            else:
                scope[a][edge], scope[c][edge] = scope[c][edge], scope[a][edge]
            ids.add(identity(scope))
    assert len(ids) == 13 and identity(deepcopy(original)) == b['boundary_id']


@pytest.mark.parametrize('name', VARIANTS)
def test_geometry_matches_page_entry_and_restores_all_four_states(tmp_path, name):
    outputs = {}
    for kind in ('entry', 'chain'):
        path = tmp_path / kind
        source, state, _ = flow(path, triple(name), kind=kind)
        out, state, report = saved(path, source, state, change(state, LONG), 'grow')
        fields = ('unicode', 'glyph_id', 'origin', 'size', 'advance', 'code', 'cid', 'nominal_pdf_width')
        outputs[kind] = dict(out=out, state=state, glyphs=glyphs(out), pixels=pixels(out),
            plan=[[{k: g[k] for k in fields} for g in step['report']['glyph_plan']] for step in report['steps']])
        assert drawings(out) == drawings(source)
        assert outside(pixels(out), REGION['bounds']) == outside(pixels(source), REGION['bounds'])
    entry, mine = outputs['entry'], outputs['chain']
    assert mine['plan'] == entry['plan'] and mine['glyphs'] == entry['glyphs'] and len(mine['glyphs']) > 20
    assert mine['pixels'] == entry['pixels']
    d = mine['state']['continuation_destinations']['dest-R2']
    binding = mine['state']['destination_bindings']['dest-R2']
    assert list(binding['scope']) == list(LEVELS)
    assert ('ctm_compensation' in d['authority']) == VARIANTS[name][0]
    assert ('clip_constraint' in d['authority']) == VARIANTS[name][1]
    assert_pairing(program(mine['out'], 2), binding['scope'], binding['start'], binding['end'])
    assert_restored(mine['out'], binding, d)


def test_lifecycle_reopen_second_shorten_regrow_and_one_noop(tmp_path):
    data = triple()
    source, state, chosen = flow(tmp_path, data)
    chosen = chosen['R2']
    original = deepcopy(state['continuation_destinations']['dest-R2'])
    confirmed = deepcopy(state['destination_bindings']['dest-R2']['scope'])
    assert confirmed == {name: two.where(original['authority']['graphics_state_scope'][name]) for name in LEVELS}
    creation = previous = None
    for step, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('shorten', 'AB'),
                       ('regrow', LONG), ('noop', None)]:
        out, updated, report = saved(tmp_path, source, state, {} if text is None else change(state, text), step)
        d, binding = updated['continuation_destinations']['dest-R2'], updated['destination_bindings']['dest-R2']
        assert d == original
        sid = slot_id(d)
        slot = updated['slots'][sid]
        creation = creation or slot['creation_binding']
        assert creation == slot['creation_binding'] and creation['mutation']['start'] == chosen['offset']
        prefix, block, suffix = split(out, updated, 'dest-R2')
        assert prefix == data[:chosen['offset']] and suffix.lstrip(b'\n') == data[len(prefix):].lstrip(b'\n')
        for name in LEVELS:
            assert binding['scope'][name]['opening'] == confirmed[name]['opening']
            assert binding['scope'][name]['matching'] == {k:v+len(block) for k,v in confirmed[name]['matching'].items()}
        assert_pairing(program(out, 2), binding['scope'], binding['start'], binding['end'])
        assert_restored(out, binding, d)
        body = block[len(markers(d)[0]):-len(markers(d)[1])]
        assert {op.name for op in operators(body)} & {'W', 'W*', 'n', 're'} == set()
        assert body.count(INVERSE.encode()) == 1 and audit(body)['violations'] == []
        assert audit(program(out, 2))['violations'] == []
        assert binding['operator_nesting'] == destinations.OPERATOR_NESTING
        assert bool(glyphs(out)) == (step != 'shorten')
        assert (slot['occupancy'] is None) == (step == 'shorten')
        assert updated['generated_fonts']['2'] and all(r['slot_id'] == sid for r in updated['generated_fonts']['2'].values())
        if step == 'noop':
            assert pixels(out) == pixels(source) and glyphs(out) == glyphs(source)
            assert updated['generated_fonts'] == state['generated_fonts']
            assert all(set(v.values()) == {'reused'} for v in report['generated_font_outcome'].values())
            fields = ('unicode', 'glyph_id', 'origin', 'size', 'advance', 'code', 'cid', 'nominal_pdf_width')
            plans = lambda r: [[{k:g[k] for k in fields} for g in s['report']['glyph_plan']] for s in r['steps']]
            assert plans(previous) == plans(report)
        source, state, previous = out, updated, report


def test_block_at_the_last_inner_boundary_closes_before_its_matching_q(tmp_path):
    source, state, _ = flow(tmp_path, triple(), following='Q')
    out, updated, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    binding = updated['destination_bindings']['dest-R2']
    d = updated['continuation_destinations']['dest-R2']
    assert_pairing(program(out, 2), binding['scope'], binding['start'], binding['end'])
    content = ContentPage(out, 2)
    try:
        after = next(b for b in content.boundaries if b.operator.end == binding['end']-len(markers(d)[1])-1)
        assert after.operator.name == 'Q' and after.q_depth == 3
        assert destinations._state(after) == d['authority']['graphics_state']
        following = next(b for b in content.boundaries if b.operator.start >= binding['end'])
        assert following.operator.start == binding['scope']['inner']['matching']['start']
        assert following.q_depth == 2 and destinations._state(following) == d['authority']['graphics_state_scope']['inner']['restored_state']
    finally:
        content.close()


def test_malformed_or_changed_authority_and_binding_fail_closed(tmp_path):
    source, state, _ = flow(tmp_path, triple())
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    edits = [lambda s: s.pop('middle'), lambda s: s.pop('outer'), lambda s: s.pop('inner'),
             lambda s: s.update(depth=2), lambda s: s.update(depth=True), lambda s: s.update(depth=4),
             lambda s: s.update(policy=SCOPE_CHAIN), lambda s: s.update(contract=SCOPE_CHAIN_CONTRACT),
             lambda s: s.update(unexpected={}), lambda s: s.update(middle=None)]
    def disguise(s):
        s.pop('middle')
        s.update(depth=2, policy=SCOPE_CHAIN, contract=SCOPE_CHAIN_CONTRACT)
    edits.append(disguise)
    for name in LEVELS:
        edits.append(lambda s, n=name: s[n]['restored_state'].update(opacity=0.5))
        for edge in EDGES:
            edits.append(lambda s, n=name, e=edge: s[n][e].update(sha256='0'*64))
            edits.append(lambda s, n=name, e=edge: s[n][e].pop('ordinal'))
    for a, b in (('outer', 'middle'), ('middle', 'inner'), ('outer', 'inner')):
        def swap(s, a=a, b=b):
            s[a], s[b] = s[b], s[a]
        edits.append(swap)
    for edit in edits:
        bad = two._forged(state, lambda a: edit(a['graphics_state_scope']))
        assert open_shared_flow(out, bad)['status'] == 'needs_confirmation'
    # Resealed binding tampering names a different occurrence even though every q/Q has equal bytes.
    for name in LEVELS:
        for edge in EDGES:
            bad = deepcopy(state)
            bad['destination_bindings']['dest-R2']['scope'][name][edge]['start'] += 1
            assert open_shared_flow(out, _reseal(bad))['status'] == 'needs_confirmation'
    for edit in (lambda s: s.pop('middle'), lambda s: s.update(middle=s['inner']),
                 lambda s: s.update(extra=s['outer'])):
        bad = deepcopy(state)
        edit(bad['destination_bindings']['dest-R2']['scope'])
        assert open_shared_flow(out, _reseal(bad))['status'] == 'needs_confirmation'
    assert open_shared_flow(out, _reseal(deepcopy(state)))['status'] == 'restored'


def test_changed_source_occurrences_pairings_and_block_location_are_refused(tmp_path):
    # Identity still has distinct state at each level; no numerical proof can mask an occurrence change.
    source, state, _ = flow(tmp_path, triple('identity'))
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    d, binding = state['continuation_destinations']['dest-R2'], state['destination_bindings']['dest-R2']
    data, scope = program(out, 2), binding['scope']
    variants = {}
    for name in LEVELS:
        for edge in EDGES:
            op = scope[name][edge]
            variants[name+'-'+edge] = data[:op['start']] + b' ' + data[op['end']:]
        # Close and reopen the chosen level with identical bytes, leaving depth/state unchanged.
        for suffix in ('prefix', 'suffix'):
            slot = SLOTS[name+'-'+suffix]
            variants[name+'-'+suffix] = two._slot(data, slot, b'Q q')
    block = data[binding['start']:binding['end']]
    without = data[:binding['start']] + data[binding['end']:]
    for name in LEVELS:
        at = scope[name]['matching']['end'] - len(block)
        variants['past-'+name] = without[:at] + block + without[at:]
    for name, value in variants.items():
        tampered = _tampered(out, 2, value, name+'.pdf')
        with pytest.raises(PdfError):
            content = ContentPage(tampered, 2)
            try:
                destinations.page_witness(content, [d], {'dest-R2'}, scopes={'dest-R2': scope})
            finally:
                content.close()
        assert open_shared_flow(tampered, tmp_path/'grow.json')['status'] == 'needs_confirmation', name


def test_unused_authority_checks_scope_identity_even_with_recomputed_id_and_valid_binding(tmp_path):
    # All three restored states and q/Q bytes are identical: neither state
    # differences, an old boundary ID nor a generated-slot digest can reject a forgery for us.
    data = b'q q q 0 0 5 5 re f 0 g Q Q Q 1 g'
    source = build(tmp_path, SOURCE_PAGE, data)
    b = next(b for b in inspect_continuation_boundaries(source, 2)['candidates']
             if b['scope']['q_depth'] == 3 and b['previous']['operator'] == 'f')
    d = destinations.confirm_continuation_destination(source, destination_id='d', paragraph_id='A', region_id='R',
        page=2, bounds=[100, 100, 200, 200], insertion=destinations.BOUNDARY,
        graphics_state=destinations.BOUNDARY_STATE, boundary=b['boundary_id'])
    content = ContentPage(source, 2)
    try:
        binding = destinations.witness(content, d)
        assert destinations.witness(content, d, binding['boundary']['offset'], binding['scope']) == binding
        edits = []
        for a, c in (('outer', 'middle'), ('middle', 'inner'), ('outer', 'inner')):
            def swap(s, a=a, c=c):
                s[a], s[c] = s[c], s[a]
            def pair(s, a=a, c=c):
                s[a]['matching'], s[c]['matching'] = s[c]['matching'], s[a]['matching']
            edits.extend((swap, pair))
        for name in LEVELS:
            for edge in EDGES:
                edits.append(lambda s, n=name, e=edge: s[n][e].update(ordinal=s[n][e]['ordinal']+1))
        for edit in edits:
            bad = deepcopy(d)
            auth = bad['authority']
            edit(auth['graphics_state_scope'])
            auth['boundary_id'] = boundary_id(2, auth['source_program_sha256'], auth['boundary']['previous'],
                                               auth['boundary']['next'], auth['graphics_state_scope'])
            with pytest.raises(PdfError, match='scope'):
                destinations.witness(content, bad, binding['boundary']['offset'], binding['scope'])
    finally:
        content.close()


MOVES = {
    'before': {'outer-q', 'middle-q', 'inner-q', 'inner-Q', 'middle-Q', 'outer-Q'},
    'outer-prefix': {'middle-q', 'inner-q', 'inner-Q', 'middle-Q', 'outer-Q'},
    'middle-prefix': {'inner-q', 'inner-Q', 'middle-Q', 'outer-Q'},
    'inner-prefix': {'inner-Q', 'middle-Q', 'outer-Q'},
    'inner-suffix': {'inner-Q', 'middle-Q', 'outer-Q'},
    'middle-suffix': {'middle-Q', 'outer-Q'}, 'outer-suffix': {'outer-Q'}, 'after': set(),
}


@pytest.mark.parametrize('where', MOVES)
def test_source_edits_in_all_eight_positions_carry_the_same_six_operators(tmp_path, where):
    source, state, _ = flow(tmp_path, triple('identity', source=where), same_page=True)
    confirmed = deepcopy(state['destination_bindings']['dest-R2']['scope'])
    authority = deepcopy(state['continuation_destinations']['dest-R2'])
    for step, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE'))]:
        out, state, _ = saved(tmp_path, source, state, change(state, text), step)
        d, binding = state['continuation_destinations']['dest-R2'], state['destination_bindings']['dest-R2']
        assert d == authority
        assert_pairing(program(out, 1), binding['scope'], binding['start'], binding['end'])
        assert_restored(out, binding, d)
        block = binding['end'] - binding['start']
        shifts = {name+'-'+letter: binding['scope'][name][edge]['start']-confirmed[name][edge]['start']
                  -(block if edge == 'matching' else 0)
                  for name in LEVELS for edge, letter in (('opening', 'q'), ('matching', 'Q'))}
        assert {k for k,v in shifts.items() if v} == MOVES[where]
        source = out


def test_consuming_any_scope_operator_cannot_rebind_even_to_an_identical_anchored_replacement(tmp_path):
    data = triple('identity')
    source = build(tmp_path, SOURCE_PAGE, data)
    scope = pick(source)['graphics_state_scope']
    location = {name: two.where(scope[name]) for name in LEVELS}
    program_map = MutationProgram(data)
    for name in LEVELS:
        for edge in EDGES:
            op = location[name][edge]
            program_map.add(Mutation(op['start'], op['start'], b'% moved\n', kind='mutation'))
    carried = carried_scope(program_map, location)
    for name in LEVELS:
        for edge in EDGES:
            op = location[name][edge]
            shift = sum(8 for level in location.values() for other in level.values() if other['start'] <= op['start'])
            assert carried[name][edge] == {k:v+shift for k,v in op.items()}
            for anchors in ({}, {'op': 0}):
                mapping = MutationProgram(data)
                mapping.add(Mutation(op['start'], op['end'], data[op['start']:op['end']], kind='mutation', anchors=anchors))
                with pytest.raises(PdfError, match='consumed'):
                    carried_scope(mapping, location)
    for bad in ({'middle': location['middle']}, dict(location, extra={}),
                dict(location, middle={'outer': location['outer'], 'inner': location['inner']})):
        with pytest.raises(PdfError, match='malformed'):
            carried_scope(MutationProgram(data), bad)


@pytest.mark.parametrize('phase', ['rebind', 'commit'])
def test_late_failure_rolls_back_three_scopes(tmp_path, monkeypatch, phase):
    import pdfeditor.shared_flow as shared
    data = triple()
    source, state, _ = flow(tmp_path, data)
    original = source.read_bytes(), deepcopy(state)
    real = destinations.rebind
    calls = []
    def fail(*args, **kwargs):
        real(*args, **kwargs)
        calls.append(args[1])
        raise PdfError('injected late failure')
    if phase == 'rebind':
        monkeypatch.setattr(shared.destinations, 'rebind', fail)
    else:
        monkeypatch.setattr(shared.Transaction, '_verify', lambda *a, **k: (_ for _ in ()).throw(PdfError('injected late failure')))
    with pytest.raises(PdfError, match='injected'):
        edit_shared_flow(source, state, tmp_path/'bad.pdf', tmp_path/'bad.json', change(state, LONG))
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
    assert (source.read_bytes(), state) == original
    assert open_shared_flow(source, state)['status'] == 'restored'
    if phase == 'rebind':
        assert calls == [slot_id(state['continuation_destinations']['dest-R2'])]
