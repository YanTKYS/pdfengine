"""A paragraph exists independently of its current painted glyphs.

The empty state keeps observed style recipes and a generated, nonpainting
insertion slot. The slot proves where a writer may run under the original
graphics state; it is not a hidden glyph, an old string, or a semantic style.
"""
from copy import copy, deepcopy
import hashlib
import json
import math

from .attributed import SourceParagraph, SourceStyle, digest
from .backend import PdfError
from .composition import _observations
from .content_stream import ContentPage
from .model import Rect, WidthConstraint
from .selection import ResolvedSelection, source_sha


def paragraph_from_snapshot(source, snapshot):
    if snapshot.get('kind') == 'empty-logical-paragraph':
        return EmptyParagraph(source, snapshot)
    return SourceParagraph(source, snapshot['selection'], line_joiner=snapshot['line_joiner'],
                           logical=snapshot.get('logical'))


def style_recipes(paragraph):
    if isinstance(paragraph, EmptyParagraph):
        return deepcopy(paragraph.saved['style_recipes'])
    return {ident: dict(properties=style.export(), matrix=list(style.matrix),
                       pdf_font_size=style.event.state.size, pdf_horizontal_scale=style.event.state.tz,
                       provenance='observed_source', source_pdf_sha256=paragraph.selection['source_sha256'])
            for ident, style in paragraph.styles.items()}


def slot_binding(content, offset):
    events=[e for e in content.events if not e.invocation and e.operator.start == offset]
    if (content.errors or len(events) != 1 or events[0].error or events[0].chars
            or events[0].operator.name != 'TJ' or events[0].atoms):
        raise PdfError('empty paragraph needs a verified, nonpainting [] TJ insertion slot')
    event=events[0]
    if event.state.tr != 0 or event.state.opacity != 1 or event.state.stroke_opacity != 1:
        raise PdfError('empty insertion slot has unsupported paint state')
    for key,value in event.state.other.items():
        if key.startswith('ExtGState:') and isinstance(value,dict):
            if value.get('/SMask','/None')!='/None' or value.get('/BM','/Normal')!='/Normal':
                raise PdfError('empty insertion slot has unsupported blend/mask semantics')
        if key=='marked_content' and any(s in str(value) for s in ('/ActualText','/OC')):
            raise PdfError('empty insertion slot needs separate marked-content semantics')
    program=content.streams[-content.page.xref]
    return dict(program_sha256=hashlib.sha256(program).hexdigest(), event=event.report(),
                provenance='generated-by-pdfengine'), event


def bind_empty(source, report):
    if report['after']:
        raise PdfError('nonempty text without painted glyphs needs independent whitespace style spans')
    content=ContentPage(source,report['selection']['page'])
    try:
        binding,_=slot_binding(content,report['empty_slot_offset'])
        selection=dict(source_sha256=source_sha(source),page=report['selection']['page'],glyph_ids=[],
                       explicitly_supplied_width=report['widths']['explicitly_supplied_width'],
                       binding_kind='nonpainting-text-slot')
        value=dict(kind='empty-logical-paragraph',schema_version=1,selection=selection,text='',spans=[],
            styles=report['styles'],style_recipes=report['empty_style_recipes'],
            typing_style_id=report['empty_typing_style_id'],insertion_binding=binding,line_joiner='',
            layout_suggestion=dict(x=report['x'],baseline=report['baseline'],
                first_line_indent=report['first_line_indent'],base_baselines=[],observed_baselines=[]),
            logical=dict(pdf_sha256=source_sha(source),text='',units=[],
                         text_provenance='explicitly_confirmed',binding_provenance='generated-by-pdfengine'))
        # Preserve JSON round-tripping just like observed paragraph snapshots.
        value=json.loads(json.dumps(value));value['snapshot_sha256']=digest(value)
        return value,{ident:ident for ident in report['empty_style_recipes']}
    finally:
        content.close()


class EmptyParagraph:
    """Layout input with zero source glyphs and real, independent style recipes."""
    def __init__(self, source, snapshot):
        self.saved=deepcopy(snapshot);self.selection=snapshot['selection']
        self.content=ContentPage(source,self.selection['page'])
        try:
            value=deepcopy(snapshot);checksum=value.pop('snapshot_sha256',None)
            if checksum != digest(value) or self.selection['source_sha256'] != source_sha(source):
                raise PdfError('empty logical element belongs to a different revision or is damaged')
            if (snapshot['text'] != '' or snapshot['spans'] or self.selection['glyph_ids']
                    or snapshot['logical']['text'] != '' or snapshot['logical']['units']
                    or snapshot['logical']['pdf_sha256'] != source_sha(source)):
                raise PdfError('empty element cannot claim physical glyphs or old logical text')
            stored=snapshot['insertion_binding']
            binding,self.first=slot_binding(self.content,stored['event']['byte_range'][0])
            # JSON-normalize the parser's tuples before comparing source evidence.
            if json.loads(json.dumps(binding)) != stored:
                raise PdfError('empty paragraph insertion context differs from its source binding')
            if self.content.page.rotation:
                raise PdfError('empty element requires unrotated page coordinates')
            self.events=[self.first];self.observations=_observations(self.content.page)
            self.units=[];self.text='';self.styles={};self.line_joiner='';self.logical=snapshot['logical']
            for ident,recipe in snapshot['style_recipes'].items():
                properties=recipe['properties'];matrix=tuple(recipe['matrix'])
                values=[properties[k] for k in ('font_size','horizontal_scale','tracking','word_spacing','baseline_shift')]
                if (len(matrix)!=6 or not all(math.isfinite(v) for v in [*matrix,*values,recipe['pdf_font_size'],recipe['pdf_horizontal_scale']])
                        or matrix[0]<=0 or matrix[3]<=0 or abs(matrix[1])+abs(matrix[2])>1e-5
                        or values[0]<=0 or values[1]<=0 or recipe['pdf_font_size']<=0 or recipe['pdf_horizontal_scale']<=0
                        or properties['fill'][0] not in ('g','rg','k') or properties['id']!=ident
                        or abs(values[0]-recipe['pdf_font_size']*matrix[3])>.001
                        or abs(values[1]-matrix[0]/matrix[3]*recipe['pdf_horizontal_scale']/100)>.001):
                    raise PdfError('invalid independent paragraph style recipe')
                # The glyph-free slot supplies only clip/other paint context.
                # Font coverage comes from an explicit provider when text is inserted.
                event=copy(self.first);event.state=copy(self.first.state)
                event.state.size=recipe['pdf_font_size'];event.state.tz=recipe['pdf_horizontal_scale']
                event.state.fill=properties['fill']
                self.styles[ident]=SourceStyle(ident,event,*values,matrix,properties['observed_color'],properties['font_name'])
            if snapshot['styles'] != [r['properties'] for r in snapshot['style_recipes'].values()]:
                raise PdfError('independent style registry disagrees with paragraph styles')
            self.default_style_id=snapshot['typing_style_id']
            if self.default_style_id not in self.styles:
                raise PdfError('empty paragraph needs a confirmed typing style')
            layout=snapshot['layout_suggestion'];x,y=layout['x'],layout['baseline']
            self.resolved=ResolvedSelection(self.selection['page'],[],[],Rect(x,y,x,y),
                WidthConstraint(0,None,self.selection['explicitly_supplied_width']))
        except Exception:
            self.close();raise

    def snapshot(self):
        return deepcopy(self.saved)

    def close(self):
        self.content.close()
