"""Caller-confirmed paragraph alignment and its independent PDF geometry check."""
from .backend import PdfError


DEFAULT = dict(value='left', provenance='generated_layout_policy')


def confirm(snapshot, supplied):
    if supplied is None:
        return dict(DEFAULT)
    if (not isinstance(supplied, dict) or set(supplied) - {'alignment', 'justify_policy'}
            or supplied.get('alignment') not in ('left', 'right', 'center', 'justify')):
        raise PdfError('paragraph_layout needs an explicit left/right/center/justify alignment')
    value = supplied['alignment']
    if value == 'justify':
        if supplied.get('justify_policy') not in ('word', 'character'):
            raise PdfError('justify needs an explicitly confirmed word or character gap policy')
    elif 'justify_policy' in supplied:
        raise PdfError('only justify accepts a gap policy')
    candidates = snapshot.get('spacing', {}).get('alignment_candidates', [])
    # Left is the classifier's fallback for uneven right edges, which may also
    # be authored hard breaks. Right/center/distributed justification are
    # positive evidence. Unknown is absence of evidence, not a contradiction.
    strong = {c['value'] for c in candidates if c.get('provenance') == 'alignment_candidate'
              and c['value'] in ('right', 'center', 'justify')}
    if strong and value not in strong:
        raise PdfError('confirmed alignment contradicts source alignment evidence')
    result = dict(value=value, provenance='explicitly_confirmed')
    if value == 'justify':
        result['justify_policy'] = supplied['justify_policy']
    return result


def request(record):
    if record == DEFAULT:
        return None
    if not isinstance(record, dict) or record.get('provenance') != 'explicitly_confirmed':
        raise PdfError('alignment provenance is unknown')
    supplied = dict(alignment=record.get('value'))
    if 'justify_policy' in record:
        supplied['justify_policy'] = record['justify_policy']
    if confirm({}, supplied) != record:
        raise PdfError('invalid confirmed alignment record')
    return supplied


def options(record):
    request(record)
    return dict(alignment=record['value'], justify_policy=record.get('justify_policy'))


def verify(paragraph, state):
    """Re-layout from current PDF widths and compare identity-bound glyphs.

    This never associates glyphs by geometry or paint order. SourceParagraph's
    verified logical units supply each Unicode offset's actual PDF occurrence.
    """
    from .attributed import apply_edits
    from .paragraph import ParagraphShaper, _layout_parameters
    from .rich_layout import layout_attributed
    record = state.get('logical_element', {}).get('alignment', DEFAULT)
    declared = request(record)
    witness_contract = state['physical_layout'].get('alignment_contract')
    if witness_contract is not None and witness_contract != record:
        raise PdfError('alignment semantic differs from the generated geometry contract')
    if declared is None and witness_contract is None:
        return
    if state['layout_provenance'].get('width') != 'explicitly_confirmed':
        raise PdfError('confirmed alignment needs explicitly confirmed width')
    snapshot = state['paragraph']
    if not snapshot['text']:
        # A verified nonpainting slot carries no line-edge claim. Its owner
        # retains the alignment to use on retyping, as with empty style recipes.
        if snapshot.get('kind') != 'empty-logical-paragraph':
            raise PdfError('empty alignment needs a verified nonpainting slot')
        return
    _, layout_options = _layout_parameters(paragraph, snapshot, **state['layout'])
    from .editable import _saved_fonts
    shaper = ParagraphShaper(paragraph, apply_edits(paragraph, snapshot, []), _saved_fonts(state, None),
                             alignment=record['value'])
    try:
        layout = layout_attributed(shaper.text, shape=shaper.shape, **layout_options, **options(record),
            continues=state['physical_layout'].get('paragraph_continues',False))
        painted = set()
        for placed in layout.glyphs:
            if placed.end != placed.start + 1:
                raise PdfError('alignment witness needs single-codepoint glyph bindings')
            unit = paragraph.units[placed.start]
            if unit.source_index is None:
                raise PdfError('alignment requires a missing painted glyph witness')
            dx, dy = placed.glyph.payload['offset']
            actual = unit.observation['origin']
            if abs(actual[0] - placed.x - dx) > .002 or abs(actual[1] - placed.baseline - dy) > .002:
                raise PdfError(f'alignment geometry witness differs from confirmed layout at Unicode {placed.start}: '
                               f'PDF {actual}, expected {[placed.x + dx, placed.baseline + dy]}')
            painted.add(unit.source_index)
        if painted != set(snapshot['selection']['glyph_ids']):
            raise PdfError('alignment line allocation differs from painted logical Unicode')
        # A report is never authority for geometry; verify it against this
        # recomputation as well, when the state describes generated layout.
        if state['physical_layout']['provenance'] != 'observed_source':
            from .elements import _close
            keys = ('start', 'end', 'x', 'baseline', 'width')
            expected = [{k: getattr(line, k) for k in keys} for line in layout.lines]
            recorded = [{k: line[k] for k in keys} for line in state['physical_layout']['lines']]
            if not _close(expected, recorded):
                raise PdfError('alignment line edges or allocation report differs from PDF witnesses')
    finally:
        shaper.close()


def continuation(text, end, cut):
    """A physical fragment end is soft only if no authored break was consumed."""
    return cut < len(text) and not any(c in '\r\n' for c in text[end:cut])
