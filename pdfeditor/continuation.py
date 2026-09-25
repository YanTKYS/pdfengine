"""Explicit insertion authorities for source-free paragraph continuations.

Two authorities exist. The page entry places blocks before the existing page
program. Several confirmed destinations may share it: their blocks form an
ordered page-entry chain in front of the original program. The order is
confirmed data of each destination (``page_entry_order``), never dictionary,
creation or activation order.

A confirmed page-program boundary places a block between two top-level
operators that the caller selected from the candidates this module lists.
A candidate is a page-level boundary (no open q, text object, marked-content
or compatibility scope, path or pending clip) of a program whose operators
nest (see operator_nesting; otherwise no scope is trusted and no boundary is
a candidate), and whose state is the page-entry state for what a block
draws: no clip, full opacity, fill-only text rendering, no ExtGState, and an
identity CTM or one the block can cancel (see compensation). The block
paints after all paint of the confirmed prefix and before all paint of the
confirmed suffix. One destination owns one exact boundary.

Each generated block starts from that state, not another paragraph's text
context, and closes its own state again. Each destination's entire rectangle
must be empty, including paint which will be moved/removed by another flow
plan; only its own generated glyphs are exempt.

CTM compensation. At a boundary whose CTM M is not the identity, the block
is ``q N cm BT ... ET Q``: N is the inverse of M, written once, right after
the block's own q and outside its text object. The block's glyphs are then
placed in page-entry coordinates, and its Q restores M for the suffix. N is
derived from the confirmed CTM alone and recorded in the authority with its
proof; a block's ``cm`` must be exactly that N. M is refused when it is not
finite, is singular, or N·M cannot be proven to stay close enough to the
identity (see compensation).
"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
from itertools import combinations
import json
import math

from .attributed import digest
from .backend import PdfError
from .composition import _check_obstacles, _observations
from .content_stream import ContentPage, State, TextEvent, Operator, multiply, operators, state_object
from .model import Rect, WidthConstraint
from .operator_nesting import audit
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
PAGE_ENTRY = 'before-page-program'
BOUNDARY = 'confirmed-page-program-boundary'
BOUNDARY_STATE = 'confirmed-boundary-state'
Z_ORDER = 'after-all-paint-of-the-confirmed-prefix-before-all-paint-of-the-confirmed-suffix'
# Top-level operators that paint: text showing, path painting, shading, XObjects.
PAINT = frozenset({'Tj', 'TJ', "'", '"', 'S', 's', 'f', 'F', 'f*', 'B', 'B*', 'b', 'b*', 'sh', 'Do'})
# Line width, cap, join, miter limit and dash only affect stroking. A block
# requires fill-only text (Tr 0) and paints nothing else, so these may differ
# from the page-entry defaults. Every other state parameter must be default.
STROKE_ONLY = frozenset({'w', 'J', 'j', 'M', 'd'})
IDENTITY = (1, 0, 0, 1, 0, 0)
COMPENSATION = 'inverse-ctm-inside-block-save'
# Significant decimal digits of each written operand of N, in fixed notation:
# PDF numbers have no exponent syntax.
COMPENSATION_DIGITS = 12
# Renderers such as MuPDF compose matrices in IEEE binary32. The proof bounds,
# anywhere on the page, how far a point drawn under the composed N·M can land
# from the same point drawn at page entry. The limit is the tolerance a saved
# revision's generated glyph origins are verified with (user-space units).
COMPENSATION_TOLERANCE = 0.002
UNIT_ROUNDOFF = 2.0 ** -24
# Rounding steps the bound covers per coordinate: both matrix operands, one
# product and two sums per composed entry, then applying the composite to a
# point (one product and two sums).
ROUNDING_STEPS = 8
# Every operand of N and of M stays a normal binary32 value and a PDF integer.
MAGNITUDE = (2.0 ** -126, 2 ** 31 - 1)


def program(content):
    return content.streams.get(-content.page.xref, b'')


def _page_context(content):
    """Page-level drawing context shared by both authorities: group, transform, bounds."""
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
    return dict(page_group=state_object(group) if group is not None else None,
        page_transform=list(content.page.transformation_matrix), page_bounds=list(content.page.rect))


def context(content):
    return dict(position=PAGE_ENTRY, offset=0, z_order='before-all-existing-page-paint',
        graphics_state=json.loads(json.dumps(State().report())), **_page_context(content),
        initial_clip='page-crop-box', isolation='q-BT-ET-Q', font_policy='explicit-paragraph-style-providers')


def is_boundary(destination):
    return destination['authority'].get('position')==BOUNDARY


def _operator(data, op, ordinal):
    """A source operator by its exact bytes (operands and operator)."""
    return dict(ordinal=ordinal, operator=op.name, start=op.start, end=op.end,
                sha256=hashlib.sha256(data[op.start:op.end]).hexdigest())


def _state(boundary):
    value=json.loads(json.dumps(boundary.state.report()))
    # Object numbers are not state; the marked-content depth is scope.
    value.pop('font_xref',None)
    return value


def _scope(boundary):
    return dict(q_depth=boundary.q_depth, text_object=boundary.text_object,
                marked_content_depth=boundary.marked_content_depth,
                compatibility_depth=boundary.compatibility_depth,
                pending_path=boundary.pending_path, pending_clip=boundary.pending_clip)


def _compose(first, then):
    """``first`` then ``then`` as one PDF matrix (row vectors: p·first·then)."""
    a,b,c,d,e,f=first;A,B,C,D,E,F=then
    return (a*A+b*C,a*B+b*D,c*A+d*C,c*B+d*D,e*A+f*C+E,e*B+f*D+F)


def _inverse(m):
    a,b,c,d,e,f=m;det=a*d-b*c
    return (d/det,-b/det,-c/det,a/det,(c*f-d*e)/det,(b*e-a*f)/det)


def _written(value):
    """``value`` rounded to COMPENSATION_DIGITS significant digits, as a PDF number."""
    with localcontext() as context:
        context.prec=COMPENSATION_DIGITS
        text=format(Decimal(value.numerator)/Decimal(value.denominator),'f')
    if '.' in text:text=text.rstrip('0').rstrip('.')
    return '0' if text in ('0','-0') else text


def source_ctms(data):
    """The CTM after each top-level operator, exactly, from the operands as parsed.

    The interpreter composes in binary32, like MuPDF; a renderer composing in
    binary64 sees these. Only q, Q and cm change the page-level CTM. None
    stands for a CTM with a nonfinite operand.
    """
    ctm=tuple(map(Fraction,IDENTITY));stack=[];result=[]
    for op in operators(data):
        if op.name=='q':stack.append(ctm)
        elif op.name=='Q' and stack:ctm=stack.pop()
        elif op.name=='cm':
            values=[float(v) for v in op.args]
            ctm=(_compose(tuple(map(Fraction,values)),ctm)
                 if ctm is not None and len(values)==6 and all(map(math.isfinite,values)) else None)
        result.append(ctm)
    return result


def compensation(ctm, source_ctm, page_transform, page_bounds):
    """N for a nonidentity boundary CTM, with its proof; or why no N is proven.

    ``ctm`` is the confirmed (interpreted, binary32) CTM M and ``source_ctm``
    the same CTM composed exactly from the parsed operands. N is the exact
    inverse of M rounded to COMPENSATION_DIGITS significant digits; the proof
    uses exactly those written operands. For each of the two CTMs and each
    corner p of the page box (user space) it bounds the distance between p
    and p·N·M: the exact residual plus, per coordinate, γ·(|p|·|N|·|M|) with
    γ = ROUNDING_STEPS·u/(1-ROUNDING_STEPS·u), u the binary32 unit roundoff,
    the standard bound on the rounding of those products and sums. A
    bound over COMPENSATION_TOLERANCE refuses M as numerically unstable.
    Returns ``(value, None)`` or ``(None, reason)``.
    """
    if source_ctm is None or not all(map(math.isfinite,ctm)):return None,'nonfinite-ctm'
    m=tuple(map(Fraction,ctm))
    if m[0]*m[3]-m[1]*m[2]==0:return None,'singular-ctm'
    operands=[_written(v) for v in _inverse(m)]
    n=tuple(Fraction(Decimal(v)) for v in operands)
    if any(v and not MAGNITUDE[0]<=abs(v)<=MAGNITUDE[1] for v in (*n,*m,*source_ctm)):
        return None,'numerically-unstable-ctm'
    x0,y0,x1,y1=page_bounds
    user=_inverse(tuple(map(Fraction,page_transform)))
    corners=[_compose((0,0,0,0,Fraction(x),Fraction(y)),user)[4:] for x in (x0,x1) for y in (y0,y1)]
    gamma=ROUNDING_STEPS*UNIT_ROUNDOFF/(1-ROUNDING_STEPS*UNIT_ROUNDOFF)
    bound=0.0
    for matrix in (m,source_ctm):
        r=_compose(n,matrix);s=_compose(tuple(map(abs,n)),tuple(map(abs,matrix)))
        for x,y in corners:
            dx=float(abs(x*(r[0]-1)+y*r[2]+r[4]))+gamma*float(abs(x)*s[0]+abs(y)*s[2]+s[4])
            dy=float(abs(x*r[1]+y*(r[3]-1)+r[5]))+gamma*float(abs(x)*s[1]+abs(y)*s[3]+s[5])
            bound=max(bound,math.sqrt(dx*dx+dy*dy))
    if not bound<=COMPENSATION_TOLERANCE:return None,'numerically-unstable-ctm'
    xs,ys=[float(x) for x,_ in corners],[float(y) for _,y in corners]
    return dict(policy=COMPENSATION,confirmed_ctm=list(ctm),matrix=operands,operator=' '.join(operands)+' cm',
        proof=dict(source_ctm=[float(v) for v in source_ctm],page_box=[min(xs),min(ys),max(xs),max(ys)],
                   residual=[float(v) for v in _compose(n,m)],arithmetic='binary32',unit_roundoff=UNIT_ROUNDOFF,
                   rounding_steps=ROUNDING_STEPS,displacement_bound=bound,tolerance=COMPENSATION_TOLERANCE)),None


def _compensation(state, source_ctm, page):
    """``(None, None)`` at an identity CTM, else compensation()."""
    if state['ctm']==list(IDENTITY):return None,None
    return compensation(state['ctm'],source_ctm,page['page_transform'],page['page_bounds'])


def block_ctm(destination):
    """The CTM a destination's block draws under, as the interpreter composes it."""
    value=destination['authority'].get('ctm_compensation')
    if value is None:return IDENTITY
    return multiply(tuple(map(float,value['matrix'])),tuple(value['confirmed_ctm']))


def _refusals(scope, state, ctm_reason=None):
    """Why a block drawn at this boundary would not look like one drawn at page entry."""
    reasons=[]
    if scope['q_depth']:reasons.append('inside-graphics-state-save')
    if scope['text_object']:reasons.append('inside-text-object')
    if scope['marked_content_depth']:reasons.append('inside-marked-content')
    if scope['compatibility_depth']:reasons.append('inside-compatibility-section')
    if scope['pending_path']:reasons.append('pending-path')
    if scope['pending_clip']:reasons.append('pending-clip')
    if ctm_reason:reasons.append(ctm_reason)
    if state['clip']:reasons.append('active-clip')
    if state['opacity']!=1 or state['stroke_opacity']!=1:reasons.append('transparency')
    if state['rendering_mode']!=0:reasons.append('text-rendering-mode')
    if any(k.startswith('ExtGState:') for k in state['other']):reasons.append('extgstate')
    if any(k not in STROKE_ONLY and not k.startswith('ExtGState:') for k in state['other']):
        reasons.append('graphics-state-side-effect')
    return reasons


def _nesting_refusals(data):
    """A program whose text objects, q/Q or marked content do not nest has no trustworthy scope."""
    return ['invalid-operator-nesting'] if audit(data)['violations'] else []


def _boundary_authority(content, page, ident):
    """The caller-selected candidate of this exact program, fixed with its witnesses."""
    inspected=_inspect(content,page,include_refused=True)
    found=[c for c in inspected['candidates'] if c['boundary_id']==ident]
    if len(found)!=1:
        reasons=[r['reasons'] for r in inspected['refused'] if r['boundary_id']==ident]
        raise PdfError('the confirmed boundary is not a safe page-level candidate of this page program'
                       +(': '+', '.join(reasons[0]) if reasons else ''))
    c=found[0];compensated=c.get('ctm_compensation')
    value=dict(position=BOUNDARY,boundary_id=ident,source_program_sha256=c['program_sha256'],
        boundary=dict(offset=c['offset'],ordinal=c['ordinal'],previous=c['previous'],next=c['next'],scope=c['scope']),
        z_order=c['z_order'],graphics_state=c['graphics_state'],
        graphics_state_contract='witnessed page-level state; the block sets its own font, text state and fill '
                                'and restores every parameter with its own q ... Q' if compensated is None else
                                'witnessed page-level state; the block cancels the witnessed CTM with the recorded '
                                'inverse, sets its own font, text state and fill and restores every parameter '
                                'with its own q ... Q',
        **c['context'],initial_clip='page-crop-box',isolation=_isolation(compensated),
        font_policy='explicit-paragraph-style-providers')
    if compensated is not None:value['ctm_compensation']=compensated
    return value


def _isolation(compensated):
    return 'q-BT-ET-Q' if compensated is None else 'q-cm-BT-ET-Q'


def _boundary_value(content, data, sha, d, generated, legacy, location):
    """Verify one confirmed page-program boundary in this revision.

    The block is found by its own unique markers; an unused boundary by the
    location carried from the previous revision. Either way the operator
    ending exactly there and the next source operator must be the confirmed
    ones (same operator, same bytes), and the scope and state there must be
    the confirmed, still safe, ones. An offset alone never identifies it.
    """
    auth=d['authority'];confirmed=auth['boundary'];sid=slot_id(d);begin,end=_markers(sid)
    compensated=auth.get('ctm_compensation')
    if auth['boundary_id']!=boundary_id(d['page'],auth['source_program_sha256'],confirmed['previous'],confirmed['next']):
        raise PdfError('confirmed page-program boundary ID differs from its own source witnesses')
    fonts=None;value=dict(program_sha256=sha)
    if generated:
        if data.count(begin)!=1:
            raise PdfError('generated continuation insertion marker is missing or ambiguous')
        offset=data.find(begin)
        stop,fonts=_block(data,sid,offset,legacy=legacy,
                          compensation=None if compensated is None else compensated['operator'].encode('ascii'))
        after=stop
    else:
        if any(marker in data for marker in (begin,end)):
            raise PdfError('an unused continuation destination already has an insertion marker')
        offset=after=location
    items=content.boundaries
    index=next((i for i,b in enumerate(items) if b.operator.end==offset),None)
    following=next((b for b in items[index+1:] if b.operator.start>=after),None) if index is not None else None
    if index is None or following is None:
        raise PdfError('confirmed page-program boundary is not between two operators of this revision')
    b=items[index]
    previous,following=_operator(data,b.operator,b.ordinal),_operator(data,following.operator,following.ordinal)
    for mine,theirs in ((previous,confirmed['previous']),(following,confirmed['next'])):
        if (mine['operator'],mine['sha256'])!=(theirs['operator'],theirs['sha256']):
            raise PdfError('confirmed page-program boundary operators differ from their source witnesses')
    if _nesting_refusals(data):
        raise PdfError('confirmed page-program boundary is in a program whose operators do not nest')
    scope,state=_scope(b),_state(b)
    # The CTM is part of the state: another CTM is another authority. The
    # compensation is re-derived from the witnessed CTM, never read back.
    exact=source_ctms(data)[b.ordinal] if state['ctm']!=list(IDENTITY) else None
    expected,reason=_compensation(state,exact,auth)
    if scope!=confirmed['scope'] or state!=auth['graphics_state'] or _refusals(scope,state,reason):
        raise PdfError('confirmed page-program boundary state or scope differs from its authority')
    if compensated!=expected or auth['isolation']!=_isolation(expected):
        raise PdfError('confirmed page-program boundary CTM compensation differs from its authority')
    value['boundary']=dict(offset=offset,previous=dict(start=previous['start'],end=previous['end']),
                           next=dict(start=following['start'],end=following['end']))
    if generated:
        value.update(start=offset,end=stop,block_sha256=hashlib.sha256(data[offset:stop]).hexdigest())
        if not legacy:value['operator_nesting']=OPERATOR_NESTING
    return value,fonts


def boundary_id(page, program_sha256, previous, following):
    return 'boundary-'+digest([page,program_sha256,previous['ordinal'],previous['end'],
                               previous['sha256'],following['sha256']])[:24]


def inspect_continuation_boundaries(source, page, *, include_refused=False):
    """List the page-level operator boundaries of one page program.

    Each boundary lies between two complete top-level operators of the merged
    /Contents program: never offset 0 (the page entry), never after the last
    operator. A ``safe`` candidate satisfies every condition of the module
    docstring; a caller may confirm it with its ``boundary_id``. The witnesses
    come from the same interpretation that edits the page. Nothing here
    chooses a boundary from destination geometry.
    """
    content=ContentPage(source,page)
    try:
        return _inspect(content,page,include_refused=include_refused)
    finally:content.close()


def _inspect(content, page, *, include_refused=False):
    page_context=_page_context(content)
    data=program(content);sha=hashlib.sha256(data).hexdigest()
    items=content.boundaries;invalid=_nesting_refusals(data)
    paints=[b.ordinal for b in items if b.operator.name in PAINT]
    candidates,refused,counts=[],[],defaultdict(int)
    exact=source_ctms(data) if any(b.state.ctm!=IDENTITY for b in items[:-1]) else None
    for index,b in enumerate(items[:-1]):
        previous=_operator(data,b.operator,b.ordinal)
        following=_operator(data,items[index+1].operator,items[index+1].ordinal)
        scope,state=_scope(b),_state(b)
        compensated,reason=_compensation(state,exact[b.ordinal] if exact else None,page_context)
        reasons=invalid+_refusals(scope,state,reason)
        value=dict(page=page,boundary_id=boundary_id(page,sha,previous,following),program_sha256=sha,
            offset=b.operator.end,ordinal=b.ordinal,previous=previous,next=following,scope=scope,graphics_state=state,
            z_order=dict(semantics=Z_ORDER,prefix_paint_operators=sum(p<=b.ordinal for p in paints),
                         suffix_paint_operators=sum(p>b.ordinal for p in paints)),
            context=page_context,status='refused' if reasons else 'safe',reasons=reasons)
        if compensated is not None:value['ctm_compensation']=compensated
        if reasons:
            refused.append(value)
            for reason in reasons:counts[reason]+=1
        else:candidates.append(value)
    result=dict(page=page,program_sha256=sha,program_length=len(data),operators=len(items),
                context=page_context,candidates=candidates,refused_boundaries=len(refused),
                refusal_reasons=dict(sorted(counts.items())))
    if include_refused:result['refused']=refused
    return result


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
                                     insertion, graphics_state, page_entry_order=None, boundary=None):
    """Confirm geometry AND an explicitly selected insertion authority.

    Page entry: insertion='before-page-program' and
    graphics_state='isolated-pdf-initial-state'. ``page_entry_order`` is the
    caller-confirmed position of this destination's block in its page's
    page-entry chain (lower paints first). Every page-entry destination of a
    page that holds several needs a unique one; a sole one may omit it and
    keeps the original single-destination form.

    Page-program boundary: insertion='confirmed-page-program-boundary',
    graphics_state='confirmed-boundary-state' and ``boundary``, the
    ``boundary_id`` of a safe candidate from inspect_continuation_boundaries
    for this exact source program. The block then paints after all paint of
    the confirmed prefix and before all paint of the confirmed suffix.

    No PDF font is borrowed.
    """
    if (insertion,graphics_state) not in ((PAGE_ENTRY,'isolated-pdf-initial-state'),(BOUNDARY,BOUNDARY_STATE)):
        raise PdfError('continuation requires explicit page-entry insertion and initial graphics-state authority')
    if (insertion==BOUNDARY)!=(boundary is not None):
        raise PdfError('only a confirmed page-program boundary insertion names a boundary')
    if insertion==BOUNDARY and (not isinstance(boundary,str) or page_entry_order is not None):
        raise PdfError('a page-program boundary destination needs a candidate ID and no page-entry order')
    if any(not isinstance(v,str) or not v for v in (destination_id,paragraph_id,region_id)):
        raise PdfError('continuation destination and owner identities are required')
    if page_entry_order is not None and (type(page_entry_order) is not int or page_entry_order<0):
        raise PdfError('page-entry order must be an explicit non-negative integer')
    content=ContentPage(source,page)
    try:
        witness=context(content) if insertion==PAGE_ENTRY else _boundary_authority(content,page,boundary)
        require_empty(content,bounds)
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
    """One page's destinations: the page-entry chain in confirmed order, then boundaries.

    A page holding several page-entry destinations needs a unique explicit
    order for each; nothing falls back to dictionary, creation or activation
    order. Each confirmed page-program boundary is its own authority: it has
    no page-entry order, and one destination owns one exact boundary.
    """
    if len({d['page'] for d in destinations})!=1:raise PdfError('a page-entry chain belongs to one page')
    entries=[d for d in destinations if not is_boundary(d)]
    boundaries=[d for d in destinations if is_boundary(d)]
    orders=[d.get('page_entry_order') for d in entries]
    if (len(entries)>1 or orders not in ([None],[])) and (
            any(type(o) is not int or o<0 for o in orders) or len(set(orders))!=len(orders)):
        raise PdfError('destinations sharing a page need unique explicit page-entry orders')
    if any('page_entry_order' in d for d in boundaries):
        raise PdfError('a page-program boundary destination has no page-entry order')
    ids=[d['authority']['boundary_id'] for d in boundaries]
    if len(set(ids))!=len(ids):
        raise PdfError('one confirmed page-program boundary holds one continuation destination')
    return (sorted(entries,key=lambda d:d.get('page_entry_order') or 0)
            +sorted(boundaries,key=lambda d:d['authority']['boundary']['ordinal']))


def _block(data, sid, start, *, legacy=False, compensation=None):
    """The generated block of ``sid`` at ``start``: its own markers, closed state, known operators.

    One q ... Q encloses the whole block and closes only at its end; text
    objects are neither nested nor left open; text positioning and showing
    occur only inside them. q/Q inside a text object is accepted only for a
    ``legacy`` binding (see OPERATOR_NESTING). ``compensation`` is the exact
    ``N cm`` operator its authority records: the block then opens with
    ``q N cm BT`` and holds no other cm. Without it, a block holds no cm.
    """
    begin,end=_markers(sid)
    if data.count(begin)!=1 or data.count(end)!=1 or data.find(begin)!=start:
        raise PdfError('generated continuation insertion marker is missing, ambiguous or out of page-entry order')
    stop=data.index(end)+len(end);body=data[start+len(begin):stop-len(end)]
    head=b'q BT ' if compensation is None else b'q '+compensation+b' BT '
    if (stop<=start+len(begin) or not body.startswith(head) or not body.endswith(b'Q\n') or b'%' in body
            or compensation is not None and legacy):
        raise PdfError('generated continuation block is not one isolated q BT ... ET Q object'
                       if compensation is None else
                       'generated continuation block is not one isolated q N cm BT ... ET Q object '
                       'with the inverse CTM of its authority')
    ops=list(operators(body));depth=0;text=False;fonts=set()
    # The recorded inverse, right after the block's own q, outside its text object.
    if compensation is not None:
        if [op.name for op in ops[:3]]!=['q','cm','BT'] or len(ops[1].args)!=6:
            raise PdfError('generated continuation block is not one isolated q N cm BT ... ET Q object '
                           'with the inverse CTM of its authority')
        del ops[1]
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


def page_witness(content, destinations, generated, legacy=frozenset(), locations=None):
    """Witness every confirmed destination of one page against its own authority.

    ``generated`` names the destinations whose block must exist. Page-entry
    blocks form a contiguous prefix of the page program in confirmed order,
    before the original program, each with exactly one pair of its own
    markers and no bytes between them. Each page-program boundary is verified
    on its own (``_boundary_value``); ``locations`` gives, for an unused
    boundary, its offset carried into this revision, and defaults to the
    confirmed offset in the confirmed program. Destinations without a block
    have no marker. ``legacy`` names blocks whose stored binding predates
    OPERATOR_NESTING. Returns each destination's witness and the font aliases
    its block selects.
    """
    ordered=chain(destinations)
    entries=[d for d in ordered if not is_boundary(d)]
    data=program(content);sha=hashlib.sha256(data).hexdigest();result={};fonts={};cursor=0
    if len(entries)<len(ordered):
        page=_page_context(content)
        for d in ordered[len(entries):]:
            ident=d['destination_id'];location=(locations or {}).get(ident)
            if any(d['authority'][k]!=v for k,v in page.items()):
                raise PdfError('continuation drawing context differs from its confirmed authority')
            if ident not in generated and location is None:
                if sha!=d['authority']['source_program_sha256']:
                    raise PdfError('an unused page-program boundary needs its location in this revision')
                location=d['authority']['boundary']['offset']
            result[ident],own=_boundary_value(content,data,sha,d,ident in generated,ident in legacy,location)
            if own is not None:fonts[ident]=own
    authority=context(content) if entries else None
    for d in entries:
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


def witness(content, destination, location=None):
    """Witness of a destination that has no generated block yet."""
    ident=destination['destination_id']
    return page_witness(content,[destination],set(),
                        locations=None if location is None else {ident:location})[0][ident]


def entry(destination, destinations, bindings):
    """Where a new block of ``destination`` enters its page program.

    ``bindings`` are this revision's verified witnesses. A page-program
    boundary destination enters at its own verified boundary. A page-entry
    destination enters its chain: after the last generated block ordered
    before it, or at the start of the page program; generated blocks ordered
    after it follow the new block. With no explicit order (a sole page-entry
    destination) it is offset 0.
    """
    if is_boundary(destination):
        return dict(boundary_id=destination['authority']['boundary_id'],
                    offset=bindings[destination['destination_id']]['boundary']['offset'])
    ordered=[d for d in chain(destinations) if not is_boundary(d)];order=destination.get('page_entry_order')
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
        # An unused boundary is verified where this revision's binding says it
        # is: the operators, scope and state there must be the confirmed ones.
        locations={d['destination_id']:bindings[d['destination_id']].get('boundary',{}).get('offset')
                   for d in ordered if is_boundary(d) and d['destination_id'] not in generated}
        if any(type(v) is not int for v in locations.values()):
            raise PdfError('an unused page-program boundary binding has no location')
        content=ContentPage(source,page)
        try:
            current,fonts=page_witness(content,ordered,generated,legacy,locations)
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
    # Page entry: nothing but the initial state. A confirmed boundary: its
    # witnessed stroke-only parameters, and no open marked content (scope);
    # its CTM is the identity or the witnessed CTM cancelled by the recorded
    # inverse, exactly as the interpreter composes them.
    expected=d['authority']['graphics_state']['other'] if is_boundary(d) else {}
    ctm=block_ctm(d) if is_boundary(d) else IDENTITY
    for e in events:
        s=e.state
        other={k:v for k,v in s.other.items() if k!='marked_content'} if is_boundary(d) else s.other
        if (s.ctm!=ctm or s.clip or json.loads(json.dumps(other))!=expected
                or s.opacity!=1 or s.stroke_opacity!=1 or s.tr!=0):
            raise PdfError('generated continuation graphics state differs from its confirmed authority')
    if any(set(c.source_orders)-owned for e in content.events if not e.invocation and start<=e.operator.start<stop
           for c in e.chars):
        raise PdfError('generated block contains text owned by another slot')
    if records is not None:
        own={a for a,r in records.get(str(d['page']),{}).items() if r.get('slot_id')==sid}
        if fonts-own:raise PdfError('generated block selects a font it does not own')


def snapshot(source,destination,binding,region,typing,page_entry=None):
    """Source-free writer input. ``page_entry`` is ``entry()``: a page-entry
    chain position, or for a page-program boundary its verified location."""
    position=({'boundary':deepcopy(page_entry)} if is_boundary(destination) else
              {'page_entry':deepcopy(page_entry) if page_entry is not None else dict(order=None,offset=0,preceding=[],following=[])})
    value=dict(kind='confirmed-continuation-input',selection=dict(source_sha256=source_sha(source),
        page=destination['page'],glyph_ids=[],explicitly_supplied_width=region['width']),
        text='',spans=[],styles=[],line_joiner='',destination=deepcopy(destination),destination_binding=deepcopy(binding),
        **position,typing_style_id='logical:'+typing,layout_suggestion=dict(x=region['x'],baseline=region['first_baseline'],
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
            if is_boundary(snapshot['destination']):
                # Re-verify the boundary where this revision's binding locates it.
                location=snapshot['destination_binding']['boundary']['offset']
                if (snapshot['boundary']!=dict(boundary_id=snapshot['destination']['authority']['boundary_id'],offset=location)
                        or witness(self.content,snapshot['destination'],location)!=snapshot['destination_binding']):
                    raise PdfError('continuation insertion program changed')
                self.entry=location
            else:
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
