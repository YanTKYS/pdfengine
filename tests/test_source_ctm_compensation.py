"""Source-decimal inverse, dual-model bounds and the PR #18 Poppler regression."""
from copy import deepcopy
from fractions import Fraction

from PIL import Image, ImageChops
import pytest

from pdfeditor import continuation as destinations
from pdfeditor.content_stream import multiply, operators
from pdfeditor.continuation import COMPENSATION_TOLERANCE, compensation, source_ctms
from pdfeditor.shared_flow import open_shared_flow
from evaluations.realpdf.evaluate import DEFAULT_POPPLER, poppler_render
from test_boundary_destination import REGION, _tampered, program
from test_continuation import LONG, change, saved
from test_ctm_compensation import _forged, confirmed, glyphs, grown, painted, pixels
from test_scope_chain_boundary import assert_restored, chained, flow, matching_of

FRACTIONAL = b'1 0 0 1 158.2 662.8'
INVERSE = '1 0 0 1 -158.2 -662.8 cm'
TRANSFORM, BOX = [1, 0, 0, -1, 0, 842], [0, 0, 595, 842]


def interpreted(data):
    return list(multiply(tuple(map(float, next(operators(data)).args)), destinations.IDENTITY))


def test_source_decimal_bytes_survive_pypdf_binary64_rounding():
    text = b'158.20000000000000000000000000000001'
    data = b'1 0 0 1 ' + text + b' 662.8 cm'
    parsed = next(operators(data)).args[4]
    assert Fraction(parsed) != Fraction(text.decode())
    assert source_ctms(data)[0][4:] == (Fraction(text.decode()), Fraction('662.8'))
    # Comments, all PDF whitespace, sign/decimal forms and nested q/Q work
    # without interpreting any other operator's operands as a cm.
    decorated = b'1\x000 +.0 1. +158.2% between operands\r\n662.8 cm'
    first = source_ctms(decorated)[0]
    assert first == tuple(map(Fraction, (1, 0, 0, 1, 0, 0)))[:4] + (Fraction('158.2'), Fraction('662.8'))
    values = source_ctms(decorated + b' q 2 0 0 3 .1 -.2 cm 0.1 w Q')
    assert values[2] == (Fraction(2), Fraction(0), Fraction(0), Fraction(3), Fraction('158.3'), Fraction('662.6'))
    assert values[3] == values[2] and values[4] == first


@pytest.mark.parametrize('token', [b'1e2', b'true', b'/Bad', b'(158.2)', b'158..2'])
def test_unproven_cm_spelling_is_not_promoted_to_a_decimal(token):
    # Restrict just the cm reader, even when pypdf would repair an operand.
    from pdfeditor.content_stream import Operator
    data = b'1 0 0 1 ' + token + b' 662.8 cm'
    assert destinations._decimal_cm(data, Operator(0, len(data), 'cm', [1, 0, 0, 1, 0, 0])) is None


def test_fractional_translation_uses_source_inverse_and_proves_both_models():
    data = FRACTIONAL + b' cm'
    m, s = interpreted(data), source_ctms(data)[0]
    value, reason = compensation(m, s, TRANSFORM, BOX)
    assert reason is None and value['operator'] == INVERSE
    assert value['confirmed_ctm'] == m and m[4] != 158.2
    proof = value['proof']
    assert proof['source_ctm'] == ['1', '0', '0', '1', '791/5', '3314/5']
    assert proof['models']['source']['residual'] == [1, 0, 0, 1, 0, 0]
    assert proof['models']['interpreted']['residual'][4:] != [0, 0]
    assert all(v['displacement_bound'] <= COMPENSATION_TOLERANCE for v in proof['models'].values())
    assert compensation(m, s, TRANSFORM, BOX) == (value, None)


def test_each_model_can_independently_refuse_the_serialized_inverse(monkeypatch):
    s = tuple(map(Fraction, (1, 0, 0, 1, 0, 0)))[:4] + (Fraction('0.01'), Fraction(0))
    # N cancels S, but interpreted M is displaced beyond the unchanged limit.
    assert compensation([1, 0, 0, 1, 0.02, 0], s, TRANSFORM, BOX) == (None, 'numerically-unstable-ctm')
    # A defective serialization cancels M but not S. The source proof must
    # independently reject it rather than trusting how N was calculated.
    written = destinations._written
    monkeypatch.setattr(destinations, '_written', lambda v: '0' if v == Fraction('-0.01') else written(v))
    assert compensation([1, 0, 0, 1, 0, 0], s, TRANSFORM, BOX) == (None, 'numerically-unstable-ctm')


def test_source_singular_even_when_interpreted_model_is_invertible():
    assert compensation([1, 0, 0, 1, 0, 0], tuple(map(Fraction, (1, 2, 2, 4, 0, 0))),
                        TRANSFORM, BOX) == (None, 'singular-ctm')


def test_old_compensated_authority_requires_confirmation_even_when_n_is_unchanged(tmp_path):
    _, out, state, _ = grown(tmp_path, 'translation')
    before = deepcopy(state)

    def legacy(auth):
        value = auth['ctm_compensation']
        value.pop('inverse_basis')
        proof = value['proof']
        proof['source_ctm'] = [float(Fraction(v)) for v in proof['source_ctm']]
        proof['residual'] = proof.pop('models')['interpreted']['residual']

    opened = open_shared_flow(out, _forged(state, legacy))
    assert opened['status'] == 'needs_confirmation' and 'compensation differs' in opened['reason']
    assert state == before


def poppler_pixels(pdf, directory, name):
    png = directory / (name + '.png')
    result = poppler_render(DEFAULT_POPPLER, pdf, 2, png, dpi=144)
    assert result['rendered'], result
    with Image.open(png) as image:
        return image.convert('RGB').copy()


@pytest.mark.skipif(not DEFAULT_POPPLER.is_file(), reason='Poppler is required for the fractional-translation regression')
@pytest.mark.parametrize('depth', [0, 1, 2])
def test_fractional_translation_matches_page_entry_in_both_renderers(tmp_path, depth):
    # Same translated bytes at depth 0, one save, or a two-save chain with
    # the rectangular clip set in the outer scope. Baseline is on a pixel boundary.
    page2 = chained(FRACTIONAL, clip='outer') if depth == 2 else painted(FRACTIONAL)
    if depth == 1:
        # The closing Q adds a boundary after the final paint. Use the
        # equivalent even-odd fill for that rectangle so the shared explicit
        # selector still identifies only the intended first f, before TAIL.
        before, after = page2.rsplit(b' h f', 1)
        page2 = before + b' h f*' + after
        page2 = b'q\n' + page2 + b'\nQ'
    outputs = {}
    for kind in ('entry', 'boundary'):
        path = tmp_path / kind
        if depth == 2:
            source, state, _ = flow(path, page2, {'R2': ('entry' if kind == 'entry' else 'chain', REGION)})
        else:
            source, state, _ = confirmed(path, page2, {'R2': ('entry' if kind == 'entry' else 'compensated', REGION)})
        out, updated, report = saved(path, source, state, change(state, LONG), 'grow')
        fields = ('unicode', 'glyph_id', 'origin', 'size', 'advance', 'code', 'cid', 'nominal_pdf_width')
        outputs[kind] = dict(out=out, state=updated,
            plan=[[{k: g[k] for k in fields} for g in step['report']['glyph_plan']] for step in report['steps']],
            glyphs=glyphs(out), mupdf=pixels(out), poppler=poppler_pixels(out, tmp_path, kind))
    entry, boundary = outputs['entry'], outputs['boundary']
    assert boundary['plan'] == entry['plan'] and boundary['glyphs'] == entry['glyphs']
    assert boundary['mupdf'] == entry['mupdf']
    assert ImageChops.difference(boundary['poppler'], entry['poppler']).getbbox() is None
    d = boundary['state']['continuation_destinations']['dest-R2']
    auth = d['authority']
    assert auth['ctm_compensation']['operator'] == INVERSE
    assert auth['boundary']['scope']['q_depth'] == depth
    if depth == 2:
        assert 'clip_constraint' in auth
        binding = boundary['state']['destination_bindings']['dest-R2']
        assert_restored(boundary['out'], binding, d)
        data = program(boundary['out'], 2)
        ops, matching = matching_of(data)
        ordinals = {op.start: i for i, op in enumerate(ops)}
        for level in ('outer', 'inner'):
            carried, original = binding['scope'][level], auth['graphics_state_scope'][level]
            for edge, shift in (('opening', 0), ('matching', binding['end']-binding['start'])):
                assert carried[edge] == {k: original[edge][k]+shift for k in ('start', 'end')}
            assert matching[ordinals[carried['opening']['start']]] == ordinals[carried['matching']['start']]
    # Old basis control: only replace N in the already-saved generated block.
    # This is not an alternative production writer or a valid new authority.
    old = ' '.join(destinations._written(v) for v in destinations._inverse(
        tuple(map(Fraction, auth['graphics_state']['ctm'])))) + ' cm'
    data = program(boundary['out'], 2)
    assert data.count(INVERSE.encode()) == 1 and old != INVERSE
    legacy = _tampered(boundary['out'], 2, data.replace(INVERSE.encode(), old.encode()), 'legacy-inverse.pdf')
    assert pixels(legacy) == entry['mupdf']
    assert ImageChops.difference(poppler_pixels(legacy, tmp_path, 'legacy'), entry['poppler']).getbbox() is not None
