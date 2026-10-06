"""Semantic underline contract (§27): design evidence, now run against the production runtime (§28).

PR #49 fixed the contract with in-file prototypes; §28 implements it, so every
probe below now calls the production functions instead:

* P-SEM: `semantic_paint.validate/remap/reinterpret/renumber` and
  `semantic_layout._next_decorations` (decoration E policy, mixing refusal);
* P-REC: `semantic_paint.rectangles` (exact em recipe, line-box rule);
* serialization: `semantic_paint.paint_group` (the one canonical serializer);
* P-VER: `semantic_paint.body_grammar` (v3 current-body grammar), the production
  owner witness with the injected v3 grammar, `semantic_layout.canonical_body`
  (canonical text + underline body), and the production writer/Transaction.

Inputs are the stored sidecar, the current asset and the current page frame
(the same inputs as §26.4); tampered PDFs are only written to `tmp_path` copies.
End-to-end lifecycle, Transaction, publication and raster evidence lives in
`test_semantic_underline.py`.
"""
from copy import deepcopy
from fractions import Fraction as F

import pymupdf
import pytest

from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_paint as paint
from pdfeditor import semantic_writer as writer
from pdfeditor import shared_flow
from pdfeditor import source_ownership as owned
from pdfeditor import story_styles as styles
from pdfeditor.backend import PdfError
from pdfeditor.composition import _check_obstacles, _pixels_equal
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.editable import _seal
from pdfeditor.elements import inspect_element
from pdfeditor.model import Rect
from pdfeditor.paint_provenance import _load_catalog, interpreted_paints, prove_path_paint
from pdfeditor.selection import make_selection, resolve_selection, source_sha
from test_paint_reassessment import RECIPE as PR48_RECIPE
from test_paint_reassessment import chain, page_top, plan_of  # noqa: F401  (module fixture reused)
from test_paint_reassessment import underline as pr48_underline

# Caller-confirmed default candidate for this evidence only (not a standard): at 12 pt it is the
# PR #48 prototype's 13/10 pt offset and 7/10 pt thickness.
RECIPE = {'offset_em': '13/120', 'thickness_em': '7/120'}


# ---------------------------------------------------------------- production P-SEM / P-REC / P-VER


visible = paint.visible
renumber = paint.renumber
validate = paint.validate
rectangles = paint.rectangles
paint_group = paint.paint_group
body_grammar = paint.body_grammar


def decoration(start, end, recipe=RECIPE, index=0):
    return dict(id=f'current:{index}', kind='underline', start=start, end=end,
                start_affinity='outside', end_affinity='outside', recipe=dict(recipe))


def remap(text, decorations, start, end, inserted):
    """D: one semantic edit {start, end, text}. Returns (next text, next decorations)."""
    return text[:start] + inserted + text[end:], paint.remap(text, decorations, start, end, inserted)


def reinterpret(text, decorations, changes):
    """E: the production decoration policy of `plan_semantic_transition` (version 3 record)."""
    return semantic._next_decorations(dict(text=text, decorations=decorations), changes, semantic.DECORATED_VERSION)


def revision(chain, name):
    state, plan = plan_of(chain[name]['sidecar'])
    slot = next(iter(state['slots'].values()))
    pdf = chain[name]['pdf']
    with pymupdf.open(pdf) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    span = owned.inventory(data)[slot['source_output']['marker_id']]
    return dict(name=name, pdf=pdf, state=state, slot=slot, payload=slot['semantic']['payload'], plan=plan,
                top=page_top(pdf), fill=styles.properties(semantic._source_entry(state, slot))['fill'],
                marker=slot['source_output']['marker_id'], body=data[span[1]:span[2]])


def canonical_paint(rev, decorations):
    return paint_group(rectangles(rev['plan'], rev['payload']['style'], decorations), rev['fill'], rev['top'])


def painted(rev, out, paint, *, first=False, before=b'', after=b''):
    """A tmp copy of the revision with `paint` placed in the owned body (not a published revision)."""
    with pymupdf.open(rev['pdf']) as document:
        xref = document[0].get_contents()[-1]
        data = document.xref_stream(xref)
        start, body_start, body_end, end = owned.inventory(data)[rev['marker']]
        body = data[body_start:body_end]
        new_body = paint + body if first else body + paint
        document.update_stream(xref, data[:start] + before + data[start:body_start] + new_body
                               + data[body_end:end] + after + data[end:])
        document.save(out)
    return out


def verify_current_body(pdf, rev, decorations):
    """Production v3 current-body checks over an actual PDF (§27.15 steps 3, 4, 7); returns the witness.

    The owner witness is recomputed with the injected v3 grammar (unchanged witness shape),
    then containment, decoration schema and the exact canonical text + underline body.
    """
    content = ContentPage(pdf, 1)
    try:
        data = content.streams[-content.page.xref]
        spans = owned.inventory(data)
        if set(spans) != {rev['marker']}:
            raise PdfError('source output marker inventory differs from the owned slot')
        span = spans[rev['marker']]
        record = rev['slot']['source_output']
        witness = owned.witness(content, record, span, body_grammar=paint.body_grammar)
        if witness['entry_context_sha256'] != record['current']['entry_context_sha256']:
            raise PdfError('semantic island entry/exit context differs')
        owned.containment(content, rev['slot']['binding']['paragraph'], span)
        validate(rev['payload']['text'], decorations)
        body = data[span[1]:span[2]]
        text, _ = body_grammar(body)
        if text != rev['body']:
            raise PdfError('text group is not the canonical text body')
        events = [e for e in content.events if not e.invocation and span[1] <= e.operator.start < e.operator.end <= span[2]]
        payload = dict(rev['payload'], decorations=decorations)
        if body != semantic.canonical_body(content, rev['state'], 'slot-0', payload, rev['plan'], events, body,
                                           decorations):
            raise PdfError('underline group is not the canonical serialization of the decorations')
        return witness
    finally:
        content.close()


def derive(rev, text=None, **style):
    """Pure exact plan for a modified payload over the same slot, region and asset (no PDF)."""
    payload = deepcopy(rev['payload'])
    if text is not None:
        payload['text'] = text
    payload['style'].update(style)
    font = semantic._asset(rev['state'], rev['slot'], payload, rev['slot']['semantic'].get('current'))
    try:
        return semantic._derive(rev['state'], rev['slot'], payload, font)[1], payload
    finally:
        font.font.close()


def inks(rev):
    font = semantic._asset(rev['state'], rev['slot'], rev['payload'], rev['slot']['semantic'].get('current'))
    try:
        return writer._ink_rects(font, rev['payload']['style'], rev['plan']['emitted'])
    finally:
        font.font.close()


def as_rects(rects):
    return [Rect(float(a), float(b), float(c), float(d)) for a, b, c, d in rects]


def full(rev):
    return [decoration(0, len(rev['payload']['text']))]


# ---------------------------------------------------------------- 1, 2: grammar split


def test_v3_grammar_accepts_the_canonical_text_and_underline_body(chain, tmp_path):
    rev = revision(chain, 'save3')
    decorations = [decoration(0, 1), decoration(2, 3, index=1)]
    paint = canonical_paint(rev, decorations)
    pdf = painted(rev, tmp_path / 'painted.pdf', paint)
    witness = verify_current_body(pdf, rev, decorations)
    # The unchanged witness shape covers the paint: same fields, new block/program SHA.
    assert set(witness) == set(rev['slot']['source_output']['current'])
    assert witness['entry_context_sha256'] == rev['slot']['source_output']['current']['entry_context_sha256']
    assert witness['block_sha256'] != rev['slot']['source_output']['current']['block_sha256']
    assert paint == (b'q 0 g\n20 58.7 m 27.2 58.7 l 27.2 58 l 20 58 l h f\n'
                     b'34.4 58.7 m 41.6 58.7 l 41.6 58 l 34.4 58 l h f\nQ\n')
    # A different paint byte is a different block witness: no paint-specific witness field is needed.
    moved = paint.replace(b'27.2 58.7 l 27.2 58 l', b'27.3 58.7 l 27.3 58 l')
    tampered = painted(rev, tmp_path / 'tampered.pdf', moved)
    with pytest.raises(PdfError, match='canonical serialization'):
        verify_current_body(tampered, rev, decorations)
    with pymupdf.open(tampered) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    start, _, _, end = owned.inventory(data)[rev['marker']]
    assert owned.sha(data[start:end]) != witness['block_sha256']


def test_unchanged_v2_grammar_rejects_the_same_painted_body(chain, tmp_path):
    rev = revision(chain, 'save3')
    paint = canonical_paint(rev, full(rev))
    pdf = painted(rev, tmp_path / 'painted.pdf', paint)
    assert owned.BODY_OPERATORS == frozenset('q Q BT ET Tf Tz Tc Tw Ts Tm Tj TJ g rg k'.split())
    owned.grammar(rev['body'])
    with pytest.raises(PdfError, match='source output body grammar'):
        owned.grammar(rev['body'] + paint)
    body_grammar(rev['body'] + paint)
    # Even with the owner witness and PDF hash resealed to the painted revision, v2 validation refuses.
    state = deepcopy(rev['state'])
    state.pop('model_sha256')
    state['pdf_sha256'] = source_sha(pdf)
    state['slots']['slot-0']['source_output']['current'] = verify_current_body(pdf, rev, full(rev))
    with pytest.raises(PdfError, match='source output body grammar'):
        owned.validate(pdf, _seal(state))
    assert semantic.open_semantic_flow(pdf, rev['state'])['status'] == 'needs_confirmation'


def test_decorations_exist_only_in_semantic_version_3(chain):
    """No v2 payload extension; version 3 is a known record whose payload must carry decorations."""
    rev = revision(chain, 'save3')
    with pytest.raises(PdfError, match='exactly text, style, font'):
        semantic._payload(dict(rev['payload'], decorations=full(rev)), rev['slot'])
    slot = deepcopy(rev['slot'])
    slot['semantic']['version'] = 3
    semantic._record(slot)
    with pytest.raises(PdfError, match='version 3 payload needs exactly'):
        semantic._payload(rev['payload'], rev['slot'], semantic.DECORATED_VERSION)
    semantic._payload(dict(rev['payload'], decorations=full(rev)), rev['slot'], semantic.DECORATED_VERSION)
    slot['semantic']['version'] = 4
    with pytest.raises(PdfError, match='no valid semantic payload'):
        semantic._record(slot)


# ---------------------------------------------------------------- 3–6: canonical bytes and the em recipe


@pytest.mark.parametrize('group', [
    ('save1', 'save2', 'save3'), ('edit', 'edit_noop'), ('scaled', 'scaled_noop1', 'scaled_noop2'),
    ('tw', 'tw_noop'), ('edge', 'edge_noop'), ('font_b', 'font_b_noop'),
])
def test_canonical_underline_bytes_are_a_noop_fixed_point(chain, group):
    def bodies(name):
        rev = revision(chain, name)
        n = len(rev['payload']['text'])
        return [canonical_paint(rev, d) for d in ([decoration(0, n)], [decoration(0, 1)],
                                                   [decoration(0, 1), decoration(n - 1, n, index=1)])]
    results = [bodies(name) for name in group]
    assert all(r == results[0] for r in results)
    assert all(body.startswith(b'q 0 g\n') and body.endswith(b' h f\nQ\n') for body in results[0])


def test_em_recipe_at_12pt_reproduces_the_pr48_prototype_coordinates(chain):
    rev = revision(chain, 'save3')
    numbers = lambda data: [str(op.args) for op in operators(data) if op.name in ('m', 'l')]
    old = pr48_underline(rev['plan'], rev['top'], 0, 3, PR48_RECIPE)
    assert numbers(old) == numbers(canonical_paint(rev, full(rev)))


def test_em_recipe_follows_font_size_rise_and_ignores_horizontal_scale(chain):
    base, scaled = revision(chain, 'edit_noop'), revision(chain, 'scaled')  # 12 pt -> 37/3 pt, scale 4/5
    for rev in (base, scaled):
        size = F(rev['payload']['style']['font_size'])
        (left, upper, right, lower), = rectangles(rev['plan'], rev['payload']['style'], full(rev))
        baseline = F(rev['plan']['lines'][0]['baseline'])
        assert upper - baseline == F(13, 120) * size and lower - upper == F(7, 120) * size
    assert F(scaled['payload']['style']['horizontal_scale']) == F(4, 5)  # vertical recipe is not scaled
    # rise -1 (page y-down: 1 pt up) moves the glyph baseline and the underline by exactly -1.
    plan, payload = derive(base, rise='-1')
    (_, upper, _, lower), = rectangles(plan, payload['style'], full(base))
    (_, upper0, _, lower0), = rectangles(base['plan'], base['payload']['style'], full(base))
    assert (upper - upper0, lower - lower0) == (-1, -1)


def test_endpoints_follow_tracking_tw_edges_and_font(chain):
    """Exact endpoint arithmetic independent of the plan: left = first visible origin, right = last origin + advance."""
    width = lambda units, size, scale=F(1): F(units, 1000) * size * scale
    size, scale, track = F(37, 3), F(4, 5), F(1, 4)
    a = width(600, size, scale)                       # 'AA B', font A, 37/3 pt, scale 4/5, tracking 1/4
    expected = {
        'save3': (F(20), 20 + 2 * F(36, 5) + F(36, 5)),
        'scaled': (F(20), 20 + 3 * (a + track) + a),
        'tw': (F(20), 20 + 3 * (a + track) + F(6, 5) + a),
        'edge': (F(20), 20 + 3 * (a + track) + F(6, 5) + a),
        'font_b': (F(20), 20 + 3 * (width(500, size, scale) + track) + F(6, 5) + width(500, size, scale)),
    }
    for name, (left, right) in expected.items():
        rev = revision(chain, name)
        (x0, _, x1, _), = rectangles(rev['plan'], rev['payload']['style'], full(rev))
        assert (x0, x1) == (left, right), name
    # A range ending mid-line keeps the last glyph's tracking: [0, 1) of 'AA B' at tracking 1/4.
    rev = revision(chain, 'scaled')
    (_, _, x1, _), = rectangles(rev['plan'], rev['payload']['style'], [decoration(0, 1)])
    assert x1 == 20 + a + track
    assert float(expected['font_b'][1]) == pytest.approx(41.683333, abs=1e-6)


def test_tw_and_confirmed_edge_give_identical_underline_bytes(chain):
    tw, edge = revision(chain, 'tw'), revision(chain, 'edge')
    assert tw['payload']['style']['word_spacing'] == '6/5' and edge['payload']['edges']
    for ranges in ([(0, 4)], [(0, 3)], [(2, 4)], [(0, 1), (3, 4)]):
        decorations = [decoration(a, b, index=i) for i, (a, b) in enumerate(ranges)]
        assert canonical_paint(tw, decorations) == canonical_paint(edge, decorations)


def test_font_change_keeps_the_recipe_and_its_vertical_geometry(chain):
    """The recipe is stored caller authority; a font asset change never re-derives it from font tables."""
    before, after = revision(chain, 'edge_noop'), revision(chain, 'font_b')
    assert before['payload']['font'] != after['payload']['font']
    (_, upper0, _, lower0), = rectangles(before['plan'], before['payload']['style'], full(before))
    (_, upper1, _, lower1), = rectangles(after['plan'], after['payload']['style'], full(after))
    assert (upper0, lower0) == (upper1, lower1)


def test_line_box_containment_is_exact_and_size_invariant(chain):
    rev = revision(chain, 'save3')  # hhea descent 200/1000 em
    deep = dict(offset_em='3/20', thickness_em='1/10')  # 1/4 em > 1/5 em
    edge = dict(offset_em='1/10', thickness_em='1/10')  # exactly 1/5 em: allowed
    for size in ('12', '37/3', '24'):
        plan, payload = derive(rev, font_size=size)
        with pytest.raises(PdfError, match='line box'):
            rectangles(plan, payload['style'], [decoration(0, 3, deep)])
        rectangles(plan, payload['style'], [decoration(0, 3, edge)])


# ---------------------------------------------------------------- 7, 8: P-SEM transitions and P-EMPTY


@pytest.mark.parametrize('ranges, edit, expected', [
    ([(1, 3)], (0, 0, 'A'), [(2, 4)]),          # insert before: shift
    ([(0, 3)], (1, 1, 'B'), [(0, 4)]),          # insert inside: included
    ([(1, 3)], (1, 1, 'A'), [(2, 4)]),          # insert at start: outside (start affinity)
    ([(0, 2)], (2, 2, 'B'), [(0, 2)]),          # insert at end: outside (end affinity)
    ([(0, 4)], (1, 2, ''), [(0, 3)]),           # delete inside: shrink
    ([(0, 4)], (0, 1, ''), [(0, 3)]),           # delete start endpoint: shrink
    ([(0, 4)], (3, 4, ''), [(0, 3)]),           # delete end endpoint: shrink
    ([(1, 4)], (0, 2, ''), [(0, 2)]),           # delete across start: shrink
    ([(0, 2)], (0, 2, ''), []),                 # delete the whole range: terminate
    ([(0, 2)], (0, 2, 'BA'), [(0, 2)]),         # replace exactly the range: kept (existing convention)
    ([(1, 4)], (0, 2, 'BB'), [(2, 4)]),         # replace across start: inserted text outside
    ([(0, 2)], (1, 3, 'A'), [(0, 1)]),          # replace across end: inserted text outside
    ([(0, 1)], (2, 4, 'A'), [(0, 1)]),          # edit after: unchanged
    ([(0, 3)], (0, 1, ''), [(0, 2)]),           # 'A B' -> ' B'
    ([(1, 3)], (1, 2, ''), []),                 # range 'A ' loses its A -> ' ': no visible character, terminate
    ([(0, 2), (2, 4)], (1, 3, 'A'), [(0, 1), (2, 3)]),  # touching stays separate, never merged
    ([(0, 2), (3, 4)], (0, 4, ''), []),         # empty text: every decoration terminates
])
def test_d_remap_policy(ranges, edit, expected):
    text = 'AA B' if max(b for _, b in ranges) <= 4 else None
    decorations = renumber([decoration(a, b) for a, b in ranges])
    after, result = remap(text, decorations, *edit)
    assert [(d['start'], d['end']) for d in result] == expected
    assert [d['id'] for d in result] == [f'current:{i}' for i in range(len(expected))]


def test_empty_text_terminates_decorations_and_regrow_never_revives(chain):
    text, decorations = 'AA B', renumber([decoration(0, 2), decoration(3, 4)])
    empty, none = remap(text, decorations, 0, 4, '')
    assert (empty, none) == ('', [])
    regrown, still = remap(empty, none, 0, 0, 'AA B')
    assert (regrown, still) == ('AA B', [])  # only an explicit E add can underline again
    # decorations == [] means no paint group: the v3 body is byte-identical to the L2 text-only body.
    rev = revision(chain, 'save3')
    assert canonical_paint(rev, []) == b''
    assert body_grammar(rev['body']) == (rev['body'], b'')


def test_newline_and_wrap_split_rectangles_per_line_without_painting_the_newline(chain):
    rev = revision(chain, 'save3')
    plan, payload = derive(rev, text='AB\nBA ')
    rects = rectangles(plan, payload['style'], [decoration(1, 6)])
    assert len(rects) == 2 and rects[0][1] < rects[1][1]  # one per line, top to bottom, never joined
    first, second = plan['lines']
    assert rects[0][0] == 20 + F(36, 5) and rects[0][2] == 20 + 2 * F(36, 5)   # B only; newline not painted
    assert rects[1][0] == 20 and rects[1][2] == 20 + 2 * F(36, 5)              # 'BA'; trailing space excluded
    assert first['end'] == 2 and second['start'] == 3


# ---------------------------------------------------------------- E / R policy


def add(start, end, **extra):
    item = dict(kind='underline', start=start, end=end, start_affinity='outside', end_affinity='outside',
                recipe=dict(RECIPE))
    item.update(extra)
    return {'decorations': {'add': item}}


def test_e_add_remove_and_recipe_change():
    text = 'AA B'
    one = reinterpret(text, [], add(3, 4))
    two = reinterpret(text, one, add(0, 2))
    assert [(d['id'], d['start'], d['end']) for d in two] == [('current:0', 0, 2), ('current:1', 3, 4)]
    other = dict(offset_em='1/10', thickness_em='1/20')
    changed = reinterpret(text, two, {'decorations': {'recipe': dict(id='current:1', start=3, end=4, recipe=other)}})
    assert changed[1]['recipe'] == other and changed[0] == two[0]
    removed = reinterpret(text, changed, {'decorations': {'remove': dict(id='current:0', start=0, end=2)}})
    assert [(d['id'], d['start'], d['end']) for d in removed] == [('current:0', 3, 4)]


@pytest.mark.parametrize('changes, reason', [
    (add(3, 4, kind='strikeout'), 'unsupported decoration kind'),
    (add(3, 4, id='current:7'), 'exactly kind'),                                   # caller-chosen ID
    (add(3, 4, source_id='path-1'), 'exactly kind'),                               # source path adoption
    (add(3, 4, recipe='auto'), 'offset_em and thickness_em'),                      # hidden recipe inference
    (add(3, 4, recipe=dict(offset_em='0.1', thickness_em='7/120')), 'canonical exact rational'),
    (add(3, 4, recipe=dict(offset_em='13/120', thickness_em='0')), 'exact bounds'),
    (add(3, 4, start_affinity='inside'), 'outside/outside'),
    (add(0, 2), 'overlap'),                                                        # overlaps current:0 [1, 3)
    (add(2, 2), 'ordered grapheme boundaries'),                                    # zero-length
    (add(2, 4), 'overlap'),
    (add(2, 3), 'no visible character'),
    ({'decorations': {'add': dict(kind='underline', start=0, end=1, start_affinity='outside',
                                  end_affinity='outside', recipe=dict(RECIPE))}, 'font_size': '13'}, 'its own'),
    ({'decorations': {'remove': dict(id='current:0', start=0, end=2)}}, 'not the current decoration'),  # stale
    ({'decorations': {'remove': dict(id='current:1', start=1, end=3)}}, 'not the current decoration'),  # old ID
    ({'decorations': {'revive': dict(id='current:0')}}, 'unsupported decoration action'),
    ({'decorations': {'adopt': dict(source_id='path-1')}}, 'unsupported decoration action'),
    ({'decorations': {'recipe': dict(id='current:0', start=1, end=3, recipe=dict(RECIPE))}}, 'must differ'),
])
def test_e_refusals(changes, reason):
    current = reinterpret('AA B', [], add(1, 3))
    with pytest.raises(PdfError, match=reason):
        reinterpret('AA B', current, changes)


def test_all_space_and_newline_only_ranges_are_refused_not_dormant():
    for text, start, end in (('A  B', 1, 3), ('A\nB', 1, 2), ('A \nB', 1, 3)):
        with pytest.raises(PdfError, match='no visible character'):
            reinterpret(text, [], add(start, end))


# ---------------------------------------------------------------- 9: tamper matrix (prototype verifier)


@pytest.fixture(scope='module')
def tamper_base(chain):
    rev = revision(chain, 'save3')
    decorations = [decoration(0, 1), decoration(2, 3, index=1)]
    return rev, decorations, canonical_paint(rev, decorations)


@pytest.mark.parametrize('name, change', [
    ('range', lambda d: (d.pop(), d[0].update(end=3))),
    ('recipe', lambda d: d[0]['recipe'].update(thickness_em='1/20')),
    ('kind', lambda d: d[0].update(kind='strikeout')),
    ('id', lambda d: d[0].update(id='current:5')),
    ('order', lambda d: d.reverse()),
    ('affinity', lambda d: d[0].update(end_affinity='inside')),
    ('missing decoration', lambda d: d.pop()),
    ('extra decoration', lambda d: d.append(decoration(1, 2, index=2))),
])
def test_semantic_decoration_tamper_is_refused(tamper_base, tmp_path, name, change):
    rev, decorations, paint = tamper_base
    pdf = painted(rev, tmp_path / 'painted.pdf', paint)
    verify_current_body(pdf, rev, decorations)
    tampered = deepcopy(decorations)
    change(tampered)
    with pytest.raises(PdfError):
        verify_current_body(pdf, rev, tampered)


@pytest.mark.parametrize('name, body', [
    ('geometry', lambda p: p.replace(b'27.2 58 l', b'27.2 57.9 l')),
    ('fill', lambda p: p.replace(b'q 0 g', b'q 1 0 0 rg')),
    ('extra path', lambda p: p.replace(b'Q\n', b'50 58.7 m 60 58.7 l 60 58 l 50 58 l h f\nQ\n')),
    ('missing path', lambda p: b''.join(p.splitlines(keepends=True)[:2] + p.splitlines(keepends=True)[3:])),
    ('reordered rectangles', lambda p: b''.join([p.splitlines(keepends=True)[i] for i in (0, 2, 1, 3)])),
    ('re operator', lambda p: b'q 0 g\n20 58 7.2 0.7 re f\n34.4 58 7.2 0.7 re f\nQ\n'),
    ('stroke', lambda p: p.replace(b'h f\n', b'h S\n')),
    ('cm', lambda p: p.replace(b'q 0 g', b'q 1 0 0 1 0 0 cm 0 g')),
    ('ExtGState', lambda p: p.replace(b'q 0 g', b'q /GS0 gs 0 g')),
    ('nested q', lambda p: p.replace(b'q 0 g\n', b'q 0 g\nq\n').replace(b'Q\n', b'Q\nQ\n')),
    ('curve', lambda p: p.replace(b'27.2 58 l', b'27.2 58.5 27.2 58.2 27.2 58 c')),
    ('clip', lambda p: p.replace(b'h f\n', b'h W n\n', 1)),
    ('empty group', lambda p: b'q 0 g\nQ\n'),
    ('comment', lambda p: p.replace(b'Q\n', b'% x\nQ\n')),
    ('foreign path moved into the owner', lambda p: b'q 0 g\n100 100 m 110 100 l 110 99 l 100 99 l h f\nQ\n'),
])
def test_current_body_paint_tamper_is_refused(tamper_base, tmp_path, name, body):
    rev, decorations, paint = tamper_base
    tampered = body(paint)
    assert tampered != paint
    pdf = painted(rev, tmp_path / 'tampered.pdf', tampered)
    with pytest.raises((PdfError, KeyError)):  # an unknown resource fails the interpreter itself
        verify_current_body(pdf, rev, decorations)


def test_trimmed_boundary_spaces_are_not_physically_witnessed(tamper_base):
    """Stated limit (like Tw vs edge): ranges differing only in trimmed boundary spaces paint the same bytes."""
    rev, _, _ = tamper_base
    assert canonical_paint(rev, [decoration(2, 3)]) == canonical_paint(rev, [decoration(1, 3)])


def test_paint_before_text_is_not_the_canonical_order(tamper_base, tmp_path):
    rev, decorations, paint = tamper_base
    pdf = painted(rev, tmp_path / 'paint-first.pdf', paint, first=True)
    with pytest.raises(PdfError):
        verify_current_body(pdf, rev, decorations)


# ---------------------------------------------------------------- 10: removed/new paint area


PAIRS = [  # (old revision, old ranges, new revision, new ranges)
    ('save3', 'full', 'edit', 'full'),                 # growth
    ('edit_noop', 'full', 'scaled', 'full'),           # font size + scale + tracking
    ('scaled_noop2', 'full', 'tw', 'full'),            # Tw
    ('tw', 'full', 'edge', 'full'),                    # Tw -> edge (identical)
    ('edge_noop', 'full', 'font_b', 'full'),           # font change
    ('save3', 'full', 'save3', []),                    # E remove
    ('save3', [], 'save3', 'full'),                    # E add
    ('save3', 'full', 'save3', [(2, 3)]),              # range shrink
]


def _state(rev, ranges):
    return full(rev) if ranges == 'full' else [decoration(a, b, index=i) for i, (a, b) in enumerate(ranges)]


@pytest.mark.parametrize('old_name, old_ranges, new_name, new_ranges', PAIRS)
def test_old_and_new_rectangles_bound_every_changed_pixel(chain, tmp_path, old_name, old_ranges, new_name, new_ranges):
    old, new = revision(chain, old_name), revision(chain, new_name)
    old_d, new_d = _state(old, old_ranges), _state(new, new_ranges)
    old_rects = as_rects(rectangles(old['plan'], old['payload']['style'], old_d))
    new_rects = as_rects(rectangles(new['plan'], new['payload']['style'], new_d))
    a = painted(old, tmp_path / 'old.pdf', canonical_paint(old, old_d))
    b = painted(new, tmp_path / 'new.pdf', canonical_paint(new, new_d))
    glyphs = inks(old) + inks(new)
    with pymupdf.open(a) as first, pymupdf.open(b) as second:
        assert _pixels_equal(first[0], second[0], glyphs + old_rects + new_rects)
        if not new_rects:   # removal: the removed rectangles are required in the planned area
            assert not _pixels_equal(first[0], second[0], glyphs + new_rects)
        if not old_rects:   # first add: the new rectangles are required
            assert not _pixels_equal(first[0], second[0], glyphs + old_rects)
        if old_name == new_name and old_rects and new_rects:   # shrink: the old rectangles are required
            assert not _pixels_equal(first[0], second[0], glyphs + new_rects)


# ---------------------------------------------------------------- 11: z-order


def test_text_then_paint_is_pixel_equivalent_and_keeps_the_text_group_a_byte_prefix(chain, tmp_path):
    rev = revision(chain, 'save3')
    paint = canonical_paint(rev, full(rev))
    text_first = painted(rev, tmp_path / 'text-first.pdf', paint)
    paint_first = painted(rev, tmp_path / 'paint-first.pdf', paint, first=True)
    (rect,) = as_rects(rectangles(rev['plan'], rev['payload']['style'], full(rev)))
    b_ink = inks(rev)[-1]
    assert rect.intersects(b_ink)  # B's descender crosses the underline, so the order is actually exercised
    assert all(not rect.intersects(ink) for ink in inks(rev)[:-1])  # no other glyph is touched
    with pymupdf.open(text_first) as one, pymupdf.open(paint_first) as two:
        assert one[0].get_pixmap(dpi=144, alpha=False).samples == two[0].get_pixmap(dpi=144, alpha=False).samples
        trace = lambda page: [(c[0], tuple(round(v, 4) for v in c[2]))
                              for s in page.get_texttrace() for c in s['chars']]
        assert trace(one[0]) == trace(two[0])
    # Text-first keeps the text group (and the island anchors, which are offsets into it) a byte prefix.
    with pymupdf.open(text_first) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    _, start, end, _ = owned.inventory(data)[rev['marker']]
    assert data[start:end] == rev['body'] + paint


# ---------------------------------------------------------------- P-VER: owned vs foreign paint (O1)


def test_owned_paint_is_identified_by_the_catalog_between_foreign_paint(chain, tmp_path):
    rev = revision(chain, 'save3')
    decorations = [decoration(0, 1), decoration(2, 3, index=1)]
    pdf = painted(rev, tmp_path / 'foreign.pdf', canonical_paint(rev, decorations),
                  before=b' q 0 0 1 rg 100 50 10 5 re f Q', after=b'q 1 0 0 rg 120 50 10 5 re f Q\n')
    events = [e for e in interpreted_paints(str(pdf), 1)['events'] if e['kind'] != 'fill-text']
    bounds = [[round(v, 3) for v in e['bounds']] for e in events]
    expected = [[round(float(v), 3) for v in r] for r in rectangles(rev['plan'], rev['payload']['style'], decorations)]
    assert bounds == [[100, 205, 110, 210], *expected, [120, 205, 130, 210]]  # foreign, owned (in order), foreign
    catalog, _ = _load_catalog(str(pdf), 1)
    with pymupdf.open(pdf) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    _, start, end, _ = owned.inventory(data)[rev['marker']]
    inside, outside = [], []
    for path in catalog['paths']:
        proof = prove_path_paint(str(pdf), 1, path['id'], catalog=catalog)
        assert proof['status'] == 'proven' and len(proof['paint_indices']) == 1
        a, b = path['merged_range']
        (inside if start <= a and b <= end else outside).append(proof['paint_indices'][0])
    all_events = interpreted_paints(str(pdf), 1)['events']
    assert [[round(v, 3) for v in all_events[i]['bounds']] for i in inside] == expected
    assert len(outside) == 2 and not set(inside) & set(outside)


def test_existing_obstacle_check_would_treat_old_owned_paint_as_foreign(chain, tmp_path):
    """Obligation (implemented): only the island's own old paint is excluded from obstacles."""
    rev = revision(chain, 'save3')
    glyph_inks = inks(rev)
    bounds = glyph_inks[0]
    for ink in glyph_inks[1:]:
        bounds = bounds.union(ink)

    def check(pdf, rects, exclude=frozenset()):
        selection = make_selection(pdf, glyph_ids=[0, 1, 2], explicit_width=150)
        content = ContentPage(pdf, 1)
        try:
            _check_obstacles(content, {0, 1, 2}, resolve_selection(pdf, selection), rects, bounds,
                             exclude_paint_seqnos=exclude)
        finally:
            content.close()

    def seqnos(pdf):
        return frozenset(e['seqno'] for e in interpreted_paints(str(pdf), 1)['events'] if e['kind'] == 'fill-path')

    check(rev['pdf'], glyph_inks)
    owned_paint = painted(rev, tmp_path / 'owned.pdf', canonical_paint(rev, full(rev)))
    with pytest.raises(PdfError, match='filled vector'):
        check(owned_paint, glyph_inks)  # the island's own old underline vs the B descender ink
    check(owned_paint, glyph_inks, seqnos(owned_paint))  # excluded by its proven seqno only
    # Foreign paint stays an obstacle for a new underline, and only where it is actually hit.
    new_rects = as_rects(rectangles(rev['plan'], rev['payload']['style'], full(rev)))
    near = painted(rev, tmp_path / 'near.pdf', b'', before=b' q 0 0 1 rg 22 58.2 4 0.3 re f Q')
    far = painted(rev, tmp_path / 'far.pdf', b'', before=b' q 0 0 1 rg 100 50 10 5 re f Q')
    check(near, glyph_inks)
    with pytest.raises(PdfError, match='filled vector'):
        check(near, glyph_inks + new_rects)
    check(far, glyph_inks + new_rects)


def test_v2_element_observation_infers_a_relation_for_owned_paint(chain, tmp_path):
    """Obligation: inside the owned span a path is owned by the owner, never an inferred element relation."""
    rev = revision(chain, 'save3')
    pdf = painted(rev, tmp_path / 'painted.pdf', canonical_paint(rev, full(rev)))
    element = inspect_element(pdf, make_selection(pdf, glyph_ids=[0, 1, 2], explicit_width=150))
    (path,) = element['paths']
    assert path['relationship']['hypothesis'] == 'decoration_candidate'
    assert path['relationship']['requires_confirmation'] is True
    with pymupdf.open(pdf) as document:
        data = document.xref_stream(document[0].get_contents()[-1])
    _, start, end, _ = owned.inventory(data)[rev['marker']]
    a, b = path['source']['merged_range']
    assert start <= a < b <= end


# ---------------------------------------------------------------- the runtime gates a painted candidate meets (implemented)


def test_the_production_runtime_passes_every_gate_for_a_painted_candidate(chain, tmp_path):
    """PR #49 named the gates a painted candidate hit; §28 implements each obligation:

    1. v3 grammar injected only for semantic version 3 (v2 grammar unchanged);
    2. the Transaction declares the owned island paint (insertion, then replacement);
    3. the element observation still records an inferred `decoration_candidate`, without authority;
    4. the next save excludes only the island's own old underline from obstacles, and the next edit
       declares the old paint replaced.
    """
    rev = revision(chain, 'save3')
    add = dict(operation='reinterpret', changes={'decorations': {'add': dict(
        kind='underline', start=0, end=3, start_affinity='outside', end_affinity='outside', recipe=dict(RECIPE))}})
    candidate = writer.build_semantic_candidate(rev['pdf'], chain['save3']['sidecar'], add, workspace=tmp_path / 'add')
    opened = semantic.open_semantic_flow(candidate['pdf'], candidate['sidecar'])
    assert opened['status'] == 'restored' and opened['island']['canonical'] and opened['version'] == 3
    assert candidate['body'] == rev['body'] + canonical_paint(rev, full(rev))
    (path,) = opened['state']['slots']['slot-0']['binding']['element']['paths']
    assert path['relationship']['hypothesis'] == 'decoration_candidate'
    saved = writer.build_semantic_candidate(candidate['pdf'], candidate['sidecar'], dict(operation='save'),
                                            workspace=tmp_path / 'next')
    assert saved['body'] == candidate['body']
    edited = writer.build_semantic_candidate(candidate['pdf'], candidate['sidecar'],
                                             dict(operation='edit', start=1, end=1, text='A'), workspace=tmp_path / 'edit')
    assert semantic.open_semantic_flow(edited['pdf'], edited['sidecar'])['semantic']['decorations'][0]['end'] == 4
    # The v2 path (unchanged grammar) still refuses the painted candidate.
    refused = shared_flow.open_shared_flow(candidate['pdf'], semantic._project_v2(opened['state']))
    assert refused['status'] == 'needs_confirmation' and 'source output body grammar' in refused['reason']
