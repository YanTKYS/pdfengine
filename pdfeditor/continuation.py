"""Explicit page-entry authority for source-free paragraph continuations.

The only supported insertion authority is the page entry, before the existing
page program. Each generated block starts in the PDF initial graphics state,
not another paragraph's text context, and closes its own state again. Several
confirmed destinations may share one page: their blocks form an ordered
page-entry chain in front of the original program. The order is confirmed
data of each destination (``page_entry_order``), never dictionary, creation or
activation order. Each destination's entire rectangle must be empty,
including paint which will be moved/removed by another flow plan; only its
own generated glyphs are exempt.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
from itertools import combinations
import json

from .attributed import digest
from .backend import PdfError
from .composition import _check_obstacles, _observations
from .content_stream import ContentPage, State, TextEvent, Operator, operators, state_object
from .model import Rect, WidthConstraint
from .selection import ResolvedSelection, source_sha


PROVENANCE = 'generated-from-confirmed-continuation-destination'
CREATE = 'confirmed-continuation-create'
# A generated block is one graphics-state save around text objects in the
# initial graphics state: no CTM, clip, ExtGState, XObject, path or
# rendering-mode operator.
BLOCK_OPERATORS = frozenset({'q', 'Q', 'BT', 'ET', 'Tf', 'Tz', 'Tc', 'Tw', 'Ts', 'Tm', 'Tj', 'TJ', 'g', 'rg', 'k'})
TEXT_OBJECT_ONLY = frozenset({'Tm', 'Tj', 'TJ'})
# Blocks keep q/Q outside text objects (PDF 1.x, see operator_nesting) and
# their bindings record it. A binding without this record was written before
# the contract, when the writer nested q/Q inside the block's text object; it
# is read with that rule and never written again.
OPERATOR_NESTING = 'pdf-1.x-text-objects'


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
                                     insertion, graphics_state, page_entry_order=None):
    """Confirm geometry AND an explicitly selected page-entry drawing policy.

    Callers must pass insertion='before-page-program' and
    graphics_state='isolated-pdf-initial-state'. No PDF font is borrowed.
    ``page_entry_order`` is the caller-confirmed position of this
    destination's block in its page's page-entry chain (lower paints first).
    Every destination of a page that holds several needs a unique one; a sole
    destination may omit it and keeps the original single-destination form.
    """
    if insertion!='before-page-program' or graphics_state!='isolated-pdf-initial-state':
        raise PdfError('continuation requires explicit page-entry insertion and initial graphics-state authority')
    if any(not isinstance(v,str) or not v for v in (destination_id,paragraph_id,region_id)):
        raise PdfError('continuation destination and owner identities are required')
    if page_entry_order is not None and (type(page_entry_order) is not int or page_entry_order<0):
        raise PdfError('page-entry order must be an explicit non-negative integer')
    content=ContentPage(source,page)
    try:
        witness=context(content);require_empty(content,bounds)
        value=dict(destination_id=destination_id,paragraph_id=paragraph_id,region_id=region_id,page=page,
            bounds=list(bounds),provenance='explicitly_confirmed',authority=witness,
            source_pdf_sha256=source_sha(source),source_program_sha256=hashlib.sha256(program(content)).hexdigest())
        if page_entry_order is not None:value['page_entry_order']=page_entry_order
        return value
    finally:content.close()


def slot_id(destination):
    return 'continuation-'+digest([destination[k] for k in ('destination_id','paragraph_id','region_id','page')])[:24]


def _markers(sid):
    tag=sid.encode('ascii')
    return b'%pdfengine-begin '+tag+b'\n', b'%pdfengine-end '+tag+b'\n'


def markers(destination):
    return _markers(slot_id(destination))


def chain(destinations):
    """One page's destinations in their confirmed page-entry order.

    A page holding several destinations needs a unique explicit order for
    each; nothing falls back to dictionary, creation or activation order.
    """
    orders=[d.get('page_entry_order') for d in destinations]
    if len({d['page'] for d in destinations})!=1:raise PdfError('a page-entry chain belongs to one page')
    if (len(destinations)>1 or orders!=[None]) and (
            any(type(o) is not int or o<0 for o in orders) or len(set(orders))!=len(orders)):
        raise PdfError('destinations sharing a page need unique explicit page-entry orders')
    return sorted(destinations,key=lambda d:d.get('page_entry_order') or 0)


def _block(data, sid, start, *, legacy=False):
    """The generated block of ``sid`` at ``start``: its own markers, closed state, known operators.

    One q ... Q encloses the whole block and closes only at its end; text
    objects are neither nested nor left open; text positioning and showing
    occur only inside them. q/Q inside a text object is accepted only for a
    ``legacy`` binding (see OPERATOR_NESTING).
    """
    begin,end=_markers(sid)
    if data.count(begin)!=1 or data.count(end)!=1 or data.find(begin)!=start:
        raise PdfError('generated continuation insertion marker is missing, ambiguous or out of page-entry order')
    stop=data.index(end)+len(end);body=data[start+len(begin):stop-len(end)]
    if stop<=start+len(begin) or not body.startswith(b'q BT ') or not body.endswith(b'Q\n') or b'%' in body:
        raise PdfError('generated continuation block is not one isolated q BT ... ET Q object')
    ops=list(operators(body));depth=0;text=False;fonts=set()
    for index,op in enumerate(ops):
        if op.name not in BLOCK_OPERATORS:raise PdfError('generated continuation block contains a foreign operator')
        if op.name in ('q','Q'):
            if text and not legacy:
                raise PdfError('generated continuation block saves or restores graphics state inside a text object')
            depth+=1 if op.name=='q' else -1
            if depth==0 and index!=len(ops)-1:
                raise PdfError('generated continuation block restores state it does not own')
        elif op.name in ('BT','ET'):
            if text==(op.name=='BT'):raise PdfError('generated continuation block nests or leaves a text object')
            text=op.name=='BT'
        elif op.name in TEXT_OBJECT_ONLY and not text:
            raise PdfError('generated continuation block positions or shows text outside a text object')
        elif op.name=='Tf':fonts.add(str(op.args[0]))
    if depth or text:raise PdfError('generated continuation block does not close its own graphics and text state')
    return stop,fonts


def page_witness(content, destinations, generated, legacy=frozenset()):
    """Witness every confirmed destination of one page against its page-entry chain.

    ``generated`` names the destinations whose block must exist. Their blocks
    form a contiguous prefix of the page program in confirmed order, before
    the original program, each with exactly one pair of its own markers and
    no bytes between them. Destinations without a block have no marker.
    ``legacy`` names blocks whose stored binding predates OPERATOR_NESTING.
    Returns each destination's witness and the font aliases its block selects.
    """
    ordered=chain(destinations);authority=context(content)
    data=program(content);sha=hashlib.sha256(data).hexdigest();result={};fonts={};cursor=0
    for d in ordered:
        if authority!=d['authority']:
            raise PdfError('continuation drawing context differs from its confirmed authority')
        value=dict(program_sha256=sha);ident=d['destination_id']
        if 'page_entry_order' in d:value['page_entry_order']=d['page_entry_order']
        if ident in generated:
            stop,fonts[ident]=_block(data,slot_id(d),cursor,legacy=ident in legacy)
            value.update(start=cursor,end=stop,block_sha256=hashlib.sha256(data[cursor:stop]).hexdigest())
            if ident not in legacy:value['operator_nesting']=OPERATOR_NESTING
            cursor=stop
        elif any(marker in data for marker in markers(d)):
            raise PdfError('an unused continuation destination already has an insertion marker')
        result[ident]=value
    return result,fonts


def witness(content, destination):
    """Witness of a destination that has no generated block yet."""
    return page_witness(content,[destination],set())[0][destination['destination_id']]


def entry(destination, destinations, bindings):
    """Where a new block of ``destination`` enters its page-entry chain.

    ``bindings`` are this revision's verified witnesses. The boundary is the
    end of the last generated block ordered before the destination, or the
    start of the page program; generated blocks ordered after it follow the
    new block. With no explicit order (a sole destination) it is offset 0.
    """
    ordered=chain(destinations);order=destination.get('page_entry_order')
    blocks=[d for d in ordered if 'end' in bindings[d['destination_id']]
            and d['destination_id']!=destination['destination_id']]
    before=[d for d in blocks if (d.get('page_entry_order') or 0)<(order or 0)]
    after=[d for d in blocks if (d.get('page_entry_order') or 0)>(order or 0)]
    return dict(order=order,offset=bindings[before[-1]['destination_id']]['end'] if before else 0,
                preceding=[slot_id(d) for d in before],following=[slot_id(d) for d in after])


def verify_entry(content, destination, value):
    """Re-verify a planned page-entry boundary on the bytes of this revision."""
    if value.get('order')!=destination.get('page_entry_order'):raise PdfError('continuation page-entry order changed')
    # Only the boundaries are re-verified here; this revision's blocks were
    # validated in their recorded nesting mode when it was opened.
    data=program(content);cursor=0
    for sid in value['preceding']:cursor,_=_block(data,sid,cursor,legacy=True)
    if cursor!=value['offset']:raise PdfError('continuation page-entry boundary changed')
    for sid in value['following']:cursor,_=_block(data,sid,cursor,legacy=True)
    return value['offset']


def rebind(program_map, sid, old, new, creation=None):
    """Carry one destination's block identity from one revision to the next.

    An existing block is followed through the byte mutation map by its own
    begin and end markers, which no mutation may consume; a new block by the
    anchors of the mutation that created it. Absolute offsets alone are never
    the witness: another block's growth or a new neighbor moves them.
    """
    begin,end=_markers(sid)
    if creation is not None:
        if creation.owner!=sid or creation.kind!=CREATE or 'end' in (old or {}):
            raise PdfError('a continuation block has no single creation mutation')
        start,stop=program_map.anchor(creation,'block_start'),program_map.anchor(creation,'block_end')
    elif old is not None and 'end' in old:
        marker=old['end']-len(end)
        if (program_map.source[old['start']:old['start']+len(begin)]!=begin
                or program_map.source[marker:old['end']]!=end):
            raise PdfError('previous continuation binding does not name its own markers')
        start=program_map.map_offset(old['start'])
        stop=program_map.map_offset(marker)+len(end)
    elif 'end' in new:
        raise PdfError('a continuation block appeared without its creation mutation')
    else:
        return
    if (start,stop)!=(new.get('start'),new.get('end')):
        raise PdfError('generated continuation block lost its insertion identity')


def validate_destinations(source,state):
    destinations=state.get('continuation_destinations',{})
    bindings=state.get('destination_bindings',{})
    if set(bindings)!=set(destinations):raise PdfError('continuation destination evidence is incomplete')
    pairs=set();pages=defaultdict(list)
    for ident,d in destinations.items():
        pid,rid=d['paragraph_id'],d['region_id'];sid=slot_id(d)
        if (d['destination_id']!=ident or d['provenance']!='explicitly_confirmed'
                or pid not in state['paragraphs'] or rid not in state['regions'] or (pid,rid) in pairs):
            raise PdfError('continuation needs independent paragraph and region ownership')
        pairs.add((pid,rid));region=state['regions'][rid];pages[d['page']].append(d)
        # One destination covers exactly one confirmed region.
        # Shared flow's width/baseline policy therefore cannot exceed it.
        if d['page']!=region['page'] or d['bounds']!=region['bounds']:
            raise PdfError('continuation destination must cover its confirmed shared region exactly')
        for other,slot in state['slots'].items():
            if slot['paragraph_id']==pid and slot['region_id']==rid and other!=sid:
                raise PdfError('continuation cannot replace or borrow an existing source slot')
    records=state.get('generated_fonts')
    for page,group in pages.items():
        ordered=chain(group)
        if any(Rect(*a['bounds']).intersects(Rect(*b['bounds'])) for a,b in combinations(ordered,2)):
            raise PdfError('continuation destinations of one page must not intersect')
        generated={d['destination_id'] for d in ordered if slot_id(d) in state['slots']}
        legacy={i for i in generated if 'end' in bindings[i] and 'operator_nesting' not in bindings[i]}
        content=ContentPage(source,page)
        try:
            current,fonts=page_witness(content,ordered,generated,legacy)
            for d in ordered:_validate_destination(content,state,d,bindings[d['destination_id']],
                current[d['destination_id']],fonts.get(d['destination_id']),records)
        finally:content.close()


def _validate_destination(content,state,d,binding,current,fonts,records):
    ident,pid,rid,sid=d['destination_id'],d['paragraph_id'],d['region_id'],slot_id(d)
    if binding!=current:raise PdfError('continuation program witness is stale')
    generated=state['slots'].get(sid)
    owned=set(generated['binding']['paragraph']['selection']['glyph_ids']) if generated else set()
    # Only this destination's own generated glyphs are exempt; every other
    # glyph, including another destination's generated text, is an obstacle.
    require_empty(content,d['bounds'],owned)
    if not generated:return
    if any(generated.get(k)!=v for k,v in dict(paragraph_id=pid,region_id=rid,destination_id=ident,
            page=d['page'],creation_provenance=PROVENANCE).items()):
        raise PdfError('generated slot destination or owner differs')
    creation=generated.get('creation_binding',{})
    mutation=creation.get('mutation',{});anchors=mutation.get('anchors',{})
    if (creation.get('destination_contract_sha256')!=digest(d)
            or mutation.get('owner')!=sid or mutation.get('kind')!=CREATE
            or type(mutation.get('start')) is not int or mutation.get('start')!=mutation.get('end')
            or mutation.get('insertion_order')!=d.get('page_entry_order')
            or anchors.get('block_start')!=0 or anchors.get('block_end')!=mutation.get('length')):
        raise PdfError('generated slot has no independent creation provenance')
    start,stop=current['start'],current['end']
    events=content.selected_events(owned) if owned else [e for e in content.events
        if e.operator.start==generated['binding']['paragraph']['insertion_binding']['event']['byte_range'][0]]
    for stored in generated['binding']['paragraph'].get('style_slot_bindings',{}).values():
        events.extend(e for e in content.events if e.operator.start==stored['event']['byte_range'][0])
    if not events or any(e.invocation or not start<e.operator.start<stop for e in events):
        raise PdfError('generated glyphs are outside their own insertion block')
    for e in events:
        s=e.state
        if s.ctm!=(1,0,0,1,0,0) or s.clip or s.other or s.opacity!=1 or s.stroke_opacity!=1 or s.tr!=0:
            raise PdfError('generated continuation graphics state differs from initial-state authority')
    if any(set(c.source_orders)-owned for e in content.events if not e.invocation and start<=e.operator.start<stop
           for c in e.chars):
        raise PdfError('generated block contains text owned by another slot')
    if records is not None:
        own={a for a,r in records.get(str(d['page']),{}).items() if r.get('slot_id')==sid}
        if fonts-own:raise PdfError('generated block selects a font it does not own')


def snapshot(source,destination,binding,region,typing,page_entry=None):
    value=dict(kind='confirmed-continuation-input',selection=dict(source_sha256=source_sha(source),
        page=destination['page'],glyph_ids=[],explicitly_supplied_width=region['width']),
        text='',spans=[],styles=[],line_joiner='',destination=deepcopy(destination),destination_binding=deepcopy(binding),
        page_entry=deepcopy(page_entry) if page_entry is not None else dict(order=None,offset=0,preceding=[],following=[]),
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
            if witness(self.content,snapshot['destination'])!=snapshot['destination_binding']:
                raise PdfError('continuation insertion program changed')
            self.entry=verify_entry(self.content,snapshot['destination'],snapshot['page_entry'])
            require_empty(self.content,snapshot['destination']['bounds'])
            self.events=[];self.observations=_observations(self.content.page);self.styles={};self.units=[]
            self.text='';self.logical=None;self.line_joiner='';self.default_style_id=snapshot['typing_style_id']
            self.first=TextEvent('confirmed-page-entry',-self.content.page.xref,(),Operator(self.entry,self.entry,'TJ',[]),
                State(size=1),(1,0,0,1,0,0),[],[])
            x,y=snapshot['layout_suggestion']['x'],snapshot['layout_suggestion']['baseline']
            self.resolved=ResolvedSelection(self.selection['page'],[],[],Rect(x,y,x,y),
                WidthConstraint(0,None,self.selection['explicitly_supplied_width']))
        except Exception:self.close();raise

    def snapshot(self):return deepcopy(self.saved)
    def close(self):
        if self.owns_content:self.content.close()
