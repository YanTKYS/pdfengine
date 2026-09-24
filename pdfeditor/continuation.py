"""Explicit page-entry authority for source-free paragraph continuations.

The only supported insertion boundary is before the existing page program.
It has the PDF initial graphics state, not another paragraph's text context.
One destination per page owns that boundary. Its entire rectangle must be
empty, including paint which will be moved/removed by another flow plan.
"""
from copy import deepcopy
import hashlib
import json

from .attributed import digest
from .backend import PdfError
from .composition import _check_obstacles, _observations
from .content_stream import ContentPage, State, TextEvent, Operator, state_object
from .model import Rect, WidthConstraint
from .selection import ResolvedSelection, source_sha


PROVENANCE = 'generated-from-confirmed-continuation-destination'


def program(content):
    return content.streams.get(-content.page.xref, b'')


def context(content):
    group=content.pdf_page.get('/Group')
    group=group.get_object() if group is not None else None
    if group is not None and (set(group)-{'/S','/CS','/I','/K','/Type'} or group.get('/S')!='/Transparency'
            or group.get('/Type','/Group')!='/Group'
            or group.get('/CS') not in ('/DeviceRGB','/DeviceGray','/DeviceCMYK')
            or not bool(group.get('/I',False)) or bool(group.get('/K',False))):
        raise PdfError('continuation page-entry graphics context has unsupported group semantics')
    colors=content.pdf_page['/Resources'].get_object().get('/ColorSpace',{})
    if hasattr(colors,'get_object'):colors=colors.get_object()
    if any(k in colors for k in ('/DefaultGray','/DefaultRGB','/DefaultCMYK')):
        raise PdfError('continuation initial device colors have an unsupported default color-space override')
    if (content.errors or content.page.rotation
            or float(content.pdf_page.get('/UserUnit', 1)) != 1):
        raise PdfError('continuation page-entry graphics context is unsupported')
    return dict(position='before-page-program', offset=0, z_order='before-all-existing-page-paint',
        graphics_state=json.loads(json.dumps(State().report())),
        page_group=state_object(group) if group is not None else None,
        page_transform=list(content.page.transformation_matrix), page_bounds=list(content.page.rect),
        initial_clip='page-crop-box', isolation='q-BT-ET-Q', font_policy='explicit-paragraph-style-providers')


def require_empty(content, bounds, owned=frozenset()):
    box=Rect(*bounds)
    if box.width<=0 or box.height<=0 or not Rect(*content.page.rect).contains(box):
        raise PdfError('continuation destination must be inside its existing page')
    resolved=ResolvedSelection(content.page.number+1,[],[],box,WidthConstraint(0,None,box.width))
    _check_obstacles(content,set(),resolved,[box],box,exclude_glyphs=owned)
    # Trace glyph boxes can be smaller than the rendered paint envelope.
    # Exempt a text paint only when every observed glyph in it is our own;
    # a shared paint must still protect its foreign glyphs conservatively.
    own_paints={g['span']['seqno'] for i,g in enumerate(content.actual) if i in owned}
    foreign_paints={g['span']['seqno'] for i,g in enumerate(content.actual) if i not in owned}
    for seqno,(kind,rect) in enumerate(content.page.get_bboxlog()):
        if (kind in ('fill-text','stroke-text','ignore-text') and seqno in own_paints-foreign_paints):
            continue
        if box.intersects(Rect(*rect)):
            raise PdfError('continuation destination intersects fixed paint')


def confirm_continuation_destination(source, *, destination_id, paragraph_id, region_id, page, bounds,
                                     insertion, graphics_state):
    """Confirm geometry AND an explicitly selected page-entry drawing policy.

    Callers must pass insertion='before-page-program' and
    graphics_state='isolated-pdf-initial-state'. No PDF font is borrowed.
    """
    if insertion!='before-page-program' or graphics_state!='isolated-pdf-initial-state':
        raise PdfError('continuation requires explicit page-entry insertion and initial graphics-state authority')
    if any(not isinstance(v,str) or not v for v in (destination_id,paragraph_id,region_id)):
        raise PdfError('continuation destination and owner identities are required')
    content=ContentPage(source,page)
    try:
        witness=context(content);require_empty(content,bounds)
        return dict(destination_id=destination_id,paragraph_id=paragraph_id,region_id=region_id,page=page,
            bounds=list(bounds),provenance='explicitly_confirmed',authority=witness,
            source_pdf_sha256=source_sha(source),source_program_sha256=hashlib.sha256(program(content)).hexdigest())
    finally:content.close()


def slot_id(destination):
    return 'continuation-'+digest([destination[k] for k in ('destination_id','paragraph_id','region_id','page')])[:24]


def markers(destination):
    tag=slot_id(destination).encode('ascii')
    return b'%pdfengine-begin '+tag+b'\n', b'%pdfengine-end '+tag+b'\n'


def witness(content, destination, generated):
    if context(content)!=destination['authority']:
        raise PdfError('continuation drawing context differs from its confirmed authority')
    data=program(content);result=dict(program_sha256=hashlib.sha256(data).hexdigest())
    if generated:
        begin,end=markers(destination)
        if not data.startswith(begin) or data.count(begin)!=1 or data.count(end)!=1:
            raise PdfError('generated continuation insertion marker is missing or ambiguous')
        stop=data.index(end)+len(end)
        result.update(start=0,end=stop,block_sha256=hashlib.sha256(data[:stop]).hexdigest())
    return result


def validate_destinations(source,state):
    destinations=state.get('continuation_destinations',{})
    bindings=state.get('destination_bindings',{})
    if set(bindings)!=set(destinations):raise PdfError('continuation destination evidence is incomplete')
    pages=set();pairs=set()
    for ident,d in destinations.items():
        pid,rid=d['paragraph_id'],d['region_id'];sid=slot_id(d)
        if (d['destination_id']!=ident or d['provenance']!='explicitly_confirmed'
                or pid not in state['paragraphs'] or rid not in state['regions'] or d['page'] in pages
                or (pid,rid) in pairs):
            raise PdfError('continuation needs independent ownership and one page-entry authority per page')
        pages.add(d['page']);pairs.add((pid,rid));region=state['regions'][rid]
        # Initial scope: one destination covers exactly one confirmed region.
        # Shared flow's width/baseline policy therefore cannot exceed it.
        if d['page']!=region['page'] or d['bounds']!=region['bounds']:
            raise PdfError('continuation destination must cover its confirmed shared region exactly')
        for other,slot in state['slots'].items():
            if slot['paragraph_id']==pid and slot['region_id']==rid and other!=sid:
                raise PdfError('continuation cannot replace or borrow an existing source slot')
        generated=state['slots'].get(sid)
        content=ContentPage(source,d['page'])
        try:
            current=witness(content,d,generated is not None)
            if bindings[ident]!=current:raise PdfError('continuation program witness is stale')
            owned=set(generated['binding']['paragraph']['selection']['glyph_ids']) if generated else set()
            require_empty(content,d['bounds'],owned)
            if generated:
                if any(generated.get(k)!=v for k,v in dict(paragraph_id=pid,region_id=rid,destination_id=ident,
                        page=d['page'],creation_provenance=PROVENANCE).items()):
                    raise PdfError('generated slot destination or owner differs')
                creation=generated.get('creation_binding',{})
                mutation=creation.get('mutation',{})
                if (creation.get('destination_contract_sha256')!=digest(d)
                        or mutation.get('owner')!=sid or mutation.get('kind')!='confirmed-continuation-create'
                        or mutation.get('start')!=0 or mutation.get('end')!=0
                        or mutation.get('anchors',{}).get('block_start')!=0):
                    raise PdfError('generated slot has no independent creation provenance')
                events=content.selected_events(owned) if owned else [e for e in content.events
                    if e.operator.start==generated['binding']['paragraph']['insertion_binding']['event']['byte_range'][0]]
                for stored in generated['binding']['paragraph'].get('style_slot_bindings',{}).values():
                    events.extend(e for e in content.events if e.operator.start==stored['event']['byte_range'][0])
                if not events or any(e.invocation or not current['start']<e.operator.start<current['end'] for e in events):
                    raise PdfError('generated glyphs are outside their own insertion block')
                for e in events:
                    s=e.state
                    if s.ctm!=(1,0,0,1,0,0) or s.clip or s.other or s.opacity!=1 or s.stroke_opacity!=1 or s.tr!=0:
                        raise PdfError('generated continuation graphics state differs from initial-state authority')
                if any(set(c.source_orders)-owned for e in content.events if e.operator.start<current['end']
                       for c in e.chars):
                    raise PdfError('generated block contains text owned by another slot')
        finally:content.close()


def snapshot(source,destination,binding,region,typing):
    value=dict(kind='confirmed-continuation-input',selection=dict(source_sha256=source_sha(source),
        page=destination['page'],glyph_ids=[],explicitly_supplied_width=region['width']),
        text='',spans=[],styles=[],line_joiner='',destination=deepcopy(destination),destination_binding=deepcopy(binding),
        typing_style_id='logical:'+typing,layout_suggestion=dict(x=region['x'],baseline=region['first_baseline'],
            first_line_indent=0,base_baselines=[],observed_baselines=[]))
    value['snapshot_sha256']=digest(value)
    return value


class ContinuationParagraph:
    """Source-free input to the existing paragraph writer, never a fake glyph."""
    creation=True

    def __init__(self,source,snapshot,*,content=None):
        self.saved=deepcopy(snapshot);self.selection=snapshot['selection'];self.owns_content=content is None
        self.content=ContentPage(source,self.selection['page']) if content is None else content
        try:
            if (self.content.page.number!=self.selection['page']-1
                    or snapshot['destination']['page']!=self.selection['page']
                    or self.selection['glyph_ids'] or snapshot['text'] or snapshot['spans'] or snapshot['styles']):
                raise PdfError('continuation input must be source-free and bound to its destination page')
            value=deepcopy(snapshot);checksum=value.pop('snapshot_sha256')
            if checksum!=digest(value) or self.selection['source_sha256']!=source_sha(source):
                raise PdfError('continuation input revision changed')
            if witness(self.content,snapshot['destination'],False)!=snapshot['destination_binding']:
                raise PdfError('continuation insertion program changed')
            require_empty(self.content,snapshot['destination']['bounds'])
            self.events=[];self.observations=_observations(self.content.page);self.styles={};self.units=[]
            self.text='';self.logical=None;self.line_joiner='';self.default_style_id=snapshot['typing_style_id']
            self.first=TextEvent('confirmed-page-entry',-self.content.page.xref,(),Operator(0,0,'TJ',[]),
                State(size=1),(1,0,0,1,0,0),[],[])
            x,y=snapshot['layout_suggestion']['x'],snapshot['layout_suggestion']['baseline']
            self.resolved=ResolvedSelection(self.selection['page'],[],[],Rect(x,y,x,y),
                WidthConstraint(0,None,self.selection['explicitly_supplied_width']))
        except Exception:self.close();raise

    def snapshot(self):return deepcopy(self.saved)
    def close(self):
        if self.owns_content:self.content.close()
