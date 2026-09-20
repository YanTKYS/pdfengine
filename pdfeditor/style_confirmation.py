"""Explicit inline confirmations, checked against per-glyph PDF witnesses.

Values use page points: tracking is horizontal; baseline_shift is positive
downwards. Confirmation recognizes an observed value, it does not restyle the
source or infer paragraph alignment.
"""
from copy import deepcopy
from dataclasses import replace
import math

from .backend import PdfError
from .content_stream import multiply

FIELDS = {'tracking': 'tracking_provenance', 'baseline_shift': 'baseline_shift_provenance'}
TOLERANCE = .001


def values(record):
    if not isinstance(record, dict) or not record or set(record) - FIELDS.keys():
        raise PdfError('style confirmation needs tracking and/or baseline_shift')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in record.values()):
        raise PdfError('confirmed style values must be finite page-point numbers')
    return record


def observed_values(unit):
    state = unit.event.state
    matrix = multiply(unit.event.text_matrix, state.ctm)
    return dict(tracking=state.tc * matrix[0] * state.tz / 100,
                baseline_shift=-state.ts * matrix[3])


def require_witness(unit, confirmed):
    actual = observed_values(unit)
    if any(abs(actual[k] - v) > TOLERANCE for k, v in values(confirmed).items()):
        raise PdfError('confirmed style differs from PDF Tc/Ts witness')


def confirmations(style):
    return {k: dict(value=style[k], provenance='explicitly_confirmed')
            for k, provenance in FIELDS.items() if style.get(provenance) == 'explicitly_confirmed'}


def restore_confirmations(paragraph, records):
    if not isinstance(records, dict):
        raise PdfError('logical style confirmations must be a style registry')
    for ident, record in records.items():
        if ident not in paragraph.styles or not isinstance(record, dict) or not record:
            raise PdfError('logical confirmation needs an observed style')
        if any(k not in FIELDS or not isinstance(v, dict) or set(v) != {'value', 'provenance'}
               or v['provenance'] != 'explicitly_confirmed' for k, v in record.items()):
            raise PdfError('logical style confirmation provenance is invalid')
        _confirm_source(paragraph, ident, {k: v['value'] for k, v in record.items()})


def _set(style, confirmed):
    for key, value in values(confirmed).items():
        setattr(style, 'rise' if key == 'baseline_shift' else key, float(value))
        setattr(style, FIELDS[key], 'explicitly_confirmed')


def _confirm_source(paragraph, ident, confirmed):
    values(confirmed)
    style = paragraph.styles[ident]
    if 'tracking' in confirmed and style.tracking_provenance == 'unknown':
        raise PdfError('unknown tracking cannot be confirmed as a single inline value')
    units = [u for u in paragraph.units if u.style_id == ident and u.source_index is not None]
    if not units:
        raise PdfError('confirmation needs painted PDF style witnesses')
    for unit in units:
        require_witness(unit, confirmed)
    _set(style, confirmed)


class ConfirmedParagraph:
    def __init__(self, paragraph, supplied):
        self.base = paragraph
        self.styles = {k: replace(v) for k, v in paragraph.styles.items()}
        if not isinstance(supplied, dict) or not supplied or set(supplied) - self.styles.keys():
            raise PdfError('paragraph_style must map known style IDs to explicit values')
        for ident, confirmed in supplied.items():
            values(confirmed)
            if ident in getattr(paragraph, 'rendered', {}):
                recipe = paragraph.rendered[ident]
                if any(recipe[k] != v for k, v in confirmed.items()):
                    raise PdfError('destination confirmation differs from its explicit recipe')
                _set(self.styles[ident], confirmed)
            else:
                _confirm_source(self, ident, confirmed)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def snapshot(self):
        return self.base.snapshot()

    def export_styles(self):
        original = self.base.export_styles() if hasattr(self.base, 'export_styles') else self.base.snapshot()['styles']
        result = deepcopy(original)
        for item in result:
            style = self.styles[item['id']].export()
            for key, provenance in FIELDS.items():
                item[key], item[provenance] = style[key], style[provenance]
        return result

    def destination_recipes(self):
        from .logical_element import style_recipes
        recipes = style_recipes(self.base)
        for style in self.export_styles():
            recipes[style['id']]['properties'] = style
        return recipes


def confirm_paragraph(paragraph, supplied):
    return ConfirmedParagraph(paragraph, supplied) if supplied is not None else paragraph


def writer_spacing(style):
    tracking = style.tracking if style.tracking_provenance == 'explicitly_confirmed' else 0.0
    rise = style.rise if style.baseline_shift_provenance == 'explicitly_confirmed' else 0.0
    return tracking / (style.matrix[0] * style.event.state.tz / 100), -rise / style.matrix[3], rise
