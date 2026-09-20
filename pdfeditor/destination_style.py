"""Bind confirmed inline styles to a verified destination paint context.

The source paragraph remains the authority for deletion, clip and non-inline
state. Added styles have no source font code/resource: every new glyph needs
its explicit provider. Logical style identities live above this adapter.
"""
from copy import copy, deepcopy
import math

from .attributed import SourceStyle,digest
from .backend import PdfError


PROPERTIES = ('font_size', 'horizontal_scale', 'tracking',
              'baseline_shift', 'fill', 'observed_color', 'font_name')


def inline_properties(style):
    result={k:deepcopy(style[k]) for k in PROPERTIES}
    # Source operators retain their numeric token spelling. Logical color
    # values are numbers; the original tokens remain in source observations.
    result['fill']=[style['fill'][0],[float(v) for v in style['fill'][1]]]
    return result


class DestinationParagraph:
    def __init__(self, paragraph, recipes, paragraph_style=None):
        self.base = paragraph
        self.styles = dict(paragraph.styles)
        self.rendered = {}
        if paragraph.content.page.rotation:
            raise PdfError('destination style binding requires an unrotated page')
        for ident, recipe in recipes.items():
            if not isinstance(ident, str) or not ident.startswith('logical:'):
                raise PdfError('destination styles require a separate logical namespace')
            if ident in self.styles and paragraph.units:
                raise PdfError('destination style cannot replace a retained source style')
            if set(recipe) != set(PROPERTIES):
                raise PdfError('destination style properties must be explicit')
            recipe=inline_properties(recipe)
            size, scale, tracking, rise = (recipe[k] for k in PROPERTIES[:4])
            if (not all(type(v) in (int, float) and math.isfinite(v) for v in (size, scale, tracking, rise))
                    or size <= 0 or scale <= 0):
                raise PdfError('invalid destination inline metrics')
            from .style_confirmation import values
            confirmed=(paragraph_style or {}).get(ident,{})
            if confirmed:values(confirmed)
            for key,value in (('tracking',tracking),('baseline_shift',rise)):
                if value and confirmed.get(key)!=value:
                    raise PdfError('nonzero destination tracking/rise needs explicit confirmation')
            op, color = recipe['fill']
            if (op not in ('g', 'rg', 'k') or len(color) != {'g':1, 'rg':3, 'k':4}[op]
                    or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 1 for v in color)
                    or not isinstance(recipe['font_name'], str)):
                raise PdfError('destination style requires explicit device fill and a font label')
            event = copy(paragraph.first); event.state = copy(paragraph.first.state)
            event.state.size = size; event.state.tz = scale * 100; event.state.fill = deepcopy(recipe['fill'])
            # Normalize the inline basis; the writer maps it through destination
            # CTM and page coordinates. Keep destination clip/other state intact.
            self.styles[ident] = SourceStyle(ident, event, size, scale, tracking, rise,
                                            (1, 0, 0, 1, 0, 0), deepcopy(recipe['observed_color']), recipe['font_name'])
            self.rendered[ident] = dict(recipe, id=ident, font_resource=None, font_xref=None,
                tracking_provenance='observed_source',baseline_shift_provenance='observed_source')

    def __getattr__(self, name):
        return getattr(self.base, name)

    def snapshot(self):
        return self.base.snapshot()

    def export_styles(self):
        values = {s['id']:s for s in self.base.snapshot()['styles']}
        values.update(self.rendered)
        return list(values.values())

    def destination_recipes(self):
        from .logical_element import style_recipes
        result = style_recipes(self.base)
        result.update({ident:dict(properties=deepcopy(properties), matrix=[1,0,0,1,0,0],
            pdf_font_size=properties['font_size'], pdf_horizontal_scale=properties['horizontal_scale']*100,
            provenance='confirmed_inline_style_bound_to_destination',
            source_pdf_sha256=self.selection['source_sha256']) for ident,properties in self.rendered.items()})
        return result

    def binding_report(self):
        return dict(source_pdf_sha256=self.selection['source_sha256'],page=self.selection['page'],
            source_snapshot_sha256=self.base.snapshot()['snapshot_sha256'],
            destination_event_sha256=digest(self.first.report()),
            active_clip_sha256=digest(self.first.state.clip),other_state_sha256=digest(self.first.state.other),
            destination_ctm=list(self.first.state.ctm),inline_basis=[1,0,0,1],
            render_style_ids=list(self.rendered),font_resources='generated from explicit providers in destination page',
            non_inline_state='retained destination context; no imported source-page context')


def bind_destination_styles(paragraph, recipes, paragraph_style=None):
    if paragraph_style is not None and not isinstance(paragraph_style,dict):
        raise PdfError('paragraph_style must map known style IDs to explicit values')
    return DestinationParagraph(paragraph, recipes, paragraph_style) if recipes is not None else paragraph
