"""An owned generated continuation block converges to its canonical form on every save.

A re-edit of an existing generated slot replaces the body between the
block's own markers with exactly what a creation writes for the current
fragment; superseded operators are not kept, so no-op saves do not grow the
page program. Ownership is the verified destination binding of this
revision; source slots and legacy bindings keep the ordinary rewrite.
"""
from collections import Counter
from copy import deepcopy
import hashlib

import pymupdf
import pytest

from pdfeditor import continuation as destinations
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import CREATE, OPERATOR_NESTING, REWRITE, markers, owned_block, slot_id
from pdfeditor.document_flow import _reseal
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
import test_boundary_destination as boundary
import test_clip_boundary as clip
import test_multi_destination as multi
import test_scope_boundary as scope
from test_continuation import LONG, change, prepared, saved

SHOWS = ('Tj', 'TJ')


def block_of(path, state, ident):
    """The destination's marked block, exactly the range its verified binding names."""
    d, binding = state['continuation_destinations'][ident], state['destination_bindings'][ident]
    data = boundary.program(path, d['page'])
    begin, end = markers(d)
    value = data[binding['start']:binding['end']]
    assert value.startswith(begin) and value.endswith(end) and data.count(begin) == data.count(end) == 1
    return value


def census(block):
    ops = list(operators(block))
    names = Counter(op.name for op in ops)
    painting = sum(op.name in SHOWS and any(isinstance(a, bytes) and a for a in op.args) for op in ops)
    return dict(bytes=len(block), operators=len(ops), Tf=names['Tf'], Tm=names['Tm'],
                shows=names['Tj'] + names['TJ'], painting=painting)


def pixels(path):
    with pymupdf.open(path) as doc:
        return [page.get_pixmap(dpi=72).samples for page in doc]


def rewrites(report, page):
    return [m for m in report['mutation_map'][page]['mutations'] if m['kind'] == REWRITE]


def lifecycle(tmp_path, source, state, ident, steps):
    """Save each (name, text) step; per step the output, state, report and block."""
    rows = {}
    for name, text in steps:
        previous = state['destination_bindings'][ident]
        out, state, report = saved(tmp_path, source, state, {} if text is None else change(state, text), name)
        rows[name] = dict(out=out, state=state, report=report, block=block_of(out, state, ident), previous=previous)
        source = out
    return rows


def assert_rewritten_inside_markers(row, ident, page):
    """A re-edit replaces exactly the body between the block's unchanged markers, as its own slot."""
    d = row['state']['continuation_destinations'][ident]
    begin, end = markers(d)
    previous = row['previous']
    [mutation] = rewrites(row['report'], page)
    assert (mutation['start'], mutation['end']) == (previous['start'] + len(begin), previous['end'] - len(end))
    assert mutation['owner'] == slot_id(d)


NOOPS = [('grow', LONG), ('noop1', None), ('noop2', None), ('noop3', None)]


def assert_stable_noops(rows, ident):
    grow = rows['grow']
    for name in ('noop1', 'noop2', 'noop3'):
        row = rows[name]
        # The block a creation wrote is already canonical: re-edits reproduce it byte for byte.
        assert row['block'] == grow['block'] and census(row['block']) == census(grow['block'])
        assert len(boundary.program(row['out'], 2)) == len(boundary.program(grow['out'], 2))
        assert row['state']['generated_fonts'] == grow['state']['generated_fonts']
        assert all(set(v.values()) == {'reused'} for v in row['report']['generated_font_outcome'].values())
        assert pixels(row['out']) == pixels(grow['out'])
        fields = ('unicode', 'glyph_id', 'origin', 'size', 'advance', 'code', 'cid', 'nominal_pdf_width')
        plans = lambda r: [[{k: g[k] for k in fields} for g in s['report']['glyph_plan']] for s in r['report']['steps']]
        assert plans(row) == plans(grow)
        assert_rewritten_inside_markers(row, ident, 2)
    census_grow = census(grow['block'])
    assert census_grow['Tf'] == census_grow['Tm'] == census_grow['shows'] == census_grow['painting'] > 20


def test_page_entry_block_is_canonical_through_noops(tmp_path):
    source, state = prepared(tmp_path)
    source, state, _ = saved(tmp_path, source, state, change(state, 'AB'), 'fits')
    rows = lifecycle(tmp_path, source, state, 'empty', NOOPS)
    assert_stable_noops(rows, 'empty')
    assert rows['noop3']['state']['destination_bindings']['empty']['start'] == 0


def test_boundary_block_keeps_its_prefix_suffix_position_and_authority(tmp_path):
    source, state, chosen = boundary.flow(tmp_path)
    source, state, _ = saved(tmp_path, source, state, change(state, 'AB'), 'fits')
    authority = deepcopy(state['continuation_destinations']['dest-R2'])
    rows = lifecycle(tmp_path, source, state, 'dest-R2', NOOPS)
    assert_stable_noops(rows, 'dest-R2')
    for row in rows.values():
        prefix, block, suffix = boundary.split(row['out'], row['state'], 'dest-R2')
        assert len(prefix) == chosen['offset'] and prefix == boundary.DESTINATION_PAGE[:chosen['offset']]
        assert suffix.lstrip(b'\n') == boundary.DESTINATION_PAGE[chosen['offset']:].lstrip(b'\n')
        assert row['state']['continuation_destinations']['dest-R2'] == authority
        assert authority['authority']['boundary_id'] == chosen['boundary_id']


def test_compensated_block_keeps_one_inverse_and_does_not_grow(tmp_path):
    import test_ctm_compensation as ctm
    source, out, state, chosen = ctm.grown(tmp_path, 'rotation')
    d = deepcopy(state['continuation_destinations']['dest-R2'])
    page2 = boundary.program(source, 2)
    blocks = [block_of(out, state, 'dest-R2')]
    for name in ('noop1', 'noop2'):
        previous = state['destination_bindings']['dest-R2']
        out, state, report = saved(tmp_path, out, state, {}, name)
        row = dict(state=state, report=report, previous=previous)
        assert_rewritten_inside_markers(row, 'dest-R2', 2)
        blocks.append(block_of(out, state, 'dest-R2'))
        prefix, block, suffix = boundary.split(out, state, 'dest-R2')
        # Exactly the recorded inverse, once; the suffix keeps its source CTM bytes.
        assert block.startswith(ctm.head(d)) and block.count(b' cm ') == 1
        assert suffix.lstrip(b'\n') == page2[chosen['offset']:].lstrip(b'\n')
        assert state['continuation_destinations']['dest-R2'] == d
    assert blocks[0] == blocks[1] == blocks[2]


def test_clip_in_a_scope_is_kept_and_the_block_stays_inside_it(tmp_path):
    source, state, chosen = scope.flow(tmp_path, scope.variant('clip'))
    authority = deepcopy(state['continuation_destinations']['dest-R2'])
    assert 'clip_constraint' in authority['authority'] and 'graphics_state_scope' in authority['authority']
    rows = lifecycle(tmp_path, source, state, 'dest-R2', [('grow', LONG), ('noop1', None), ('noop2', None)])
    assert rows['noop1']['block'] == rows['noop2']['block'] == rows['grow']['block']
    for name in ('noop1', 'noop2'):
        row = rows[name]
        assert_rewritten_inside_markers(row, 'dest-R2', 2)
        binding = row['state']['destination_bindings']['dest-R2']
        # Reopened: the same authority, the scope location recorded, the block
        # between that scope's q and Q, and no clip written by the block.
        assert row['state']['continuation_destinations']['dest-R2'] == authority
        assert binding['operator_nesting'] == OPERATOR_NESTING and 'scope' in binding
        data = boundary.program(row['out'], 2)
        ops, matching = scope.scope_ops(data)
        opening = max(i for i, op in enumerate(ops) if op.name == 'q' and op.end <= binding['start']
                      and ops[matching[i]].start >= binding['end'])
        assert ops[opening].end <= binding['start'] < binding['end'] <= ops[matching[opening]].start
        assert not {op.name for op in operators(row['block'])} & set(clip.FENCE)


def test_dormant_slot_keeps_its_witnesses_and_regrows_into_the_same_block(tmp_path):
    source, state = prepared(tmp_path, spacing=True)
    source, state, _ = saved(tmp_path, source, state, change(state, 'AB'), 'fits')
    sid = slot_id(state['continuation_destinations']['empty'])
    rows = lifecycle(tmp_path, source, state, 'empty', [('grow', LONG), ('shorten', 'AB'), ('dormant1', None),
                                                         ('dormant2', None), ('regrow', LONG)])
    creation = rows['grow']['state']['slots'][sid]['creation_binding']
    assert creation['mutation']['kind'] == CREATE
    for name in ('shorten', 'dormant1', 'dormant2'):
        row = rows[name]
        slot = row['state']['slots'][sid]
        paragraph = slot['binding']['paragraph']
        assert slot['occupancy'] is None and paragraph['kind'] == 'empty-logical-paragraph'
        assert paragraph['insertion_binding'] and paragraph['style_slot_bindings']
        assert census(row['block'])['painting'] == 0 and census(row['block'])['shows'] >= 2
        fonts = {op.args[0] for op in operators(row['block']) if op.name == 'Tf'}
        owned = {a for a, r in row['state']['generated_fonts']['1' if slot['page'] == 1 else '2'].items()
                 if r['slot_id'] == sid}
        assert {str(f) for f in fonts} <= owned
        assert slot['creation_binding'] == creation
        assert_rewritten_inside_markers(row, 'empty', 2)
    assert rows['dormant1']['block'] == rows['dormant2']['block'] == rows['shorten']['block']
    assert rows['dormant2']['state']['generated_fonts'] == rows['shorten']['state']['generated_fonts']
    regrow = rows['regrow']
    assert regrow['block'] == rows['grow']['block'] and not regrow['report']['plan']['new_slots']
    assert regrow['state']['slots'][sid]['creation_binding'] == creation
    assert_rewritten_inside_markers(regrow, 'empty', 2)


def test_each_destination_rewrites_only_its_own_block(tmp_path):
    source, state = multi.flow(tmp_path, two=True, orders={'DA': 20, 'DB': 10})
    slots = multi.ids(state)
    out, state, _ = multi.save(tmp_path, source, state, multi.change(state, A=LONG, B='OTHER ' + LONG), 'both')
    before = {r: block_of(out, state, 'dest-' + r) for r in slots}
    shorter = LONG.replace('fourteen', 'XIV').replace('eleven ', '')
    bindings = deepcopy(state['destination_bindings'])
    out2, state2, report = multi.save(tmp_path, out, state, multi.change(state, A=shorter), 'a-edit')
    assert multi.chain_order(out2, state2) == [slots['DB'], slots['DA']]
    after = {r: block_of(out2, state2, 'dest-' + r) for r in slots}
    # B's text did not change: its canonical block is byte-identical; A's shrank.
    assert after['DB'] == before['DB'] and len(after['DA']) < len(before['DA'])
    for mutation in rewrites(report, 2):
        r = next(r for r, sid in slots.items() if sid == mutation['owner'])
        d = state['continuation_destinations']['dest-' + r]
        begin, end = markers(d)
        old = bindings['dest-' + r]
        assert (mutation['start'], mutation['end']) == (old['start'] + len(begin), old['end'] - len(end))
    out3, state3, _ = multi.save(tmp_path, out2, state2, {}, 'noop')
    assert {r: block_of(out3, state3, 'dest-' + r) for r in slots} == after


def test_source_slots_keep_the_ordinary_rewrite(tmp_path):
    source, state = prepared(tmp_path)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    out, state, report = saved(tmp_path, out, state, {}, 'noop')
    page1 = report['mutation_map'][1]['mutations']
    assert page1 and all(m['kind'] != REWRITE for m in page1)
    assert {m['owner'] for m in page1} == {'slot-0'}
    assert [m['owner'] for m in rewrites(report, 2)] == [slot_id(state['continuation_destinations']['empty'])]


def test_ownership_must_be_proven_on_this_revisions_bytes(tmp_path):
    source, state = prepared(tmp_path)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    d, binding = state['continuation_destinations']['empty'], state['destination_bindings']['empty']
    data = boundary.program(out, 2)
    begin, end = markers(d)
    assert owned_block(data, d, binding) == (binding['start'] + len(begin), binding['end'] - len(end), None)
    block = data[binding['start']:binding['end']]
    foreign = data.replace(block, block.replace(b'<0001> Tj', b'<0001> Tj 0 0 5 5 re f', 1))
    tampered = {'legacy binding': (data, {k: v for k, v in binding.items() if k != 'operator_nesting'}),
                'block hash': (data, dict(binding, block_sha256='0' * 64)),
                'program hash': (data, dict(binding, program_sha256='0' * 64)),
                'offsets': (data, dict(binding, start=binding['start'] + 1)),
                'marker': (data.replace(begin, b'%pdfengine-begin continuation-' + b'0' * 24 + b'\n'), binding),
                'foreign bytes': (foreign, dict(binding, program_sha256=hashlib.sha256(foreign).hexdigest(),
                    end=binding['end'] + len(foreign) - len(data),
                    block_sha256=hashlib.sha256(foreign[binding['start']:binding['end'] + len(foreign) - len(data)]).hexdigest()))}
    for name, (value, bound) in tampered.items():
        with pytest.raises(PdfError):
            owned_block(value, d, bound)
    # A generated-looking block of another destination is not this one's.
    other = dict(d, destination_id='elsewhere')
    with pytest.raises(PdfError):
        owned_block(data, other, binding)
    # A tampered binding does not open, so it never reaches a rewrite.
    for name in ('block_sha256', 'start'):
        bad = deepcopy(state)
        bad['destination_bindings']['empty'][name] = '0' * 64 if name == 'block_sha256' else 1
        assert open_shared_flow(out, _reseal(bad))['status'] == 'needs_confirmation', name


def test_a_legacy_binding_is_not_canonicalized(tmp_path, monkeypatch):
    source, state = prepared(tmp_path)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    legacy = deepcopy(state)
    legacy['destination_bindings']['empty'].pop('operator_nesting')
    side = _reseal(legacy)
    assert open_shared_flow(out, side)['status'] == 'restored'
    import pdfeditor.paragraph as paragraph
    monkeypatch.setattr(paragraph, '_rewrite_generated_block',
                        lambda *a, **k: pytest.fail('a legacy block was canonicalized'))
    try:
        report = edit_shared_flow(out, side, tmp_path / 'legacy-noop.pdf', tmp_path / 'legacy-noop.json', {})
    except PdfError:
        return  # refusing to re-save a legacy block is the existing fail-closed contract
    assert not rewrites(report, 2)


@pytest.mark.parametrize('phase', ['plan', 'bind', 'rebind'])
def test_late_failure_in_a_rewrite_publishes_neither_artifact(tmp_path, monkeypatch, phase):
    import pdfeditor.paragraph as paragraph
    import pdfeditor.shared_flow as shared
    source, state = prepared(tmp_path)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    fail = lambda *a, **k: (_ for _ in ()).throw(PdfError('injected late failure'))
    if phase == 'plan':
        real = paragraph._rewrite_generated_block

        def planned(result, *args, **kwargs):
            real(result, *args, **kwargs)
            raise PdfError('injected late failure')
        monkeypatch.setattr(paragraph, '_rewrite_generated_block', planned)
    elif phase == 'bind':
        monkeypatch.setattr(shared, 'bind_document_edit', fail)
    else:
        monkeypatch.setattr(shared.destinations, 'rebind', fail)
    with pytest.raises(PdfError, match='injected'):
        edit_shared_flow(out, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', change(state, LONG + ' More.'))
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()
