"""Explicit insertion authorities for source-free paragraph continuations.

Two authorities exist. The page entry places blocks before the existing page
program. Several confirmed destinations may share it: their blocks form an
ordered page-entry chain in front of the original program. The order is
confirmed data of each destination (``page_entry_order``), never dictionary,
creation or activation order.

A confirmed page-program boundary places a block between two top-level
operators that the caller selected from the candidates this module lists.
A candidate is a boundary outside any text object, marked-content or
compatibility scope, path or pending clip, either at page level or inside
exactly one q ... Q scope (see enclosing_scope), of a program whose
operators nest (see operator_nesting; otherwise no scope is trusted and no
boundary is a candidate), and whose state is the page-entry state for what
a block draws: full opacity, fill-only text rendering, no ExtGState, an
identity CTM or one the block can cancel (see compensation), and no clip or
one proven to be a single page rectangle (see clip_constraint). The block
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

Inherited clip. A clip set at page level cannot be ended by the block's own
q ... Q: there is no saved state without it. The block therefore inherits
it unchanged, writes no W, W* or n, and must lie entirely inside it. Only a
clip proven to be one rectangle in page coordinates is inherited; the
destination bounds and every generated glyph's ink, grown by INK_MARGIN, must
lie inside that rectangle. The block's own cm cancels the CTM, never the
clip, which was fixed in page space when it was set.

Enclosing q ... Q scope. A boundary at q depth 1 lies between one opening q
and its matching Q, both at page level. The block's own q ... Q saves and
restores only the boundary state and closes before that matching Q; the
block never restores the state the matching Q restores. The scope is
identified by its structure (the q open at the boundary and the Q that
closes it), its recorded witnesses, and, from one revision to the next, the
positions of that q and Q carried through the byte mutation map; a q or Q
with the same bytes elsewhere is another scope.
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
CLIP = 'inherited-rectangular-clip'
CLIP_PROOF = 'intersection-of-single-re-clips-under-ctms-without-rotation-or-skew'
CLIP_CONTAINMENT = 'destination-bounds-and-generated-ink-inside-the-clip-rectangle'
# The renderer's paint envelope of a glyph, as the bbox log records it and
# require_empty() reads it, is its outline box grown by one unit of the
# 72 dpi device (MuPDF's allowance for glyph-cache positioning).
PAINT_PADDING = 1.0
# A planned glyph's outline ink box, grown by that padding and by the
# tolerance its saved origin is verified with (which also bounds a
# compensated block's displacement), must lie inside the clip rectangle.
INK_MARGIN = PAINT_PADDING + COMPENSATION_TOLERANCE
SCOPE = 'one-enclosing-graphics-state-save'
SCOPE_CONTRACT = ('the boundary lies between the opening q and its matching Q; the block closes its own q ... Q '
                  'before that Q and never saves, restores or ends the enclosing scope')


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
    return _state_value(boundary.state)


def _state_value(state):
    value=json.loads(json.dumps(state.report()))
    # Object numbers are not state; the marked-content depth is scope.
    value.pop('font_xref',None)
    value['clip']=clip_state(state.clip)
    return value


def clip_state(clip):
    """A clip as state: each rule and path, not where or through which object it was set.

    Where a clip was set is witnessed by its operator bytes (clip_constraint);
    a program position moves with every edit before it.
    """
    return json.loads(json.dumps([{k:v for k,v in c.items() if k not in ('at','font_xref')} for c in clip]))


def _scope(boundary):
    return dict(q_depth=boundary.q_depth, text_object=boundary.text_object,
                marked_content_depth=boundary.marked_content_depth,
                compatibility_depth=boundary.compatibility_depth,
                pending_path=boundary.pending_path, pending_clip=boundary.pending_clip)


def graphics_scopes(ops):
    """The q ... Q structure of a page program: the q ordinals open after each
    operator, innermost last, and the matching Q of each q."""
    stack,open_after,matching=[],[],{}
    for index,op in enumerate(ops):
        if op.name=='q':stack.append(index)
        elif op.name=='Q' and stack:matching[stack.pop()]=index
        open_after.append(tuple(stack))
    return open_after,matching


def enclosing_scope(data, ops, structure, boundaries, ordinal):
    """The one q ... Q scope around the boundary after operator ``ordinal``, with its witnesses.

    Returns ``(None, None)`` at page level, ``(value, None)`` inside exactly
    one scope, else ``(None, reason)``. ``structure`` is graphics_scopes(ops)
    and ``boundaries`` the interpreted state after each operator. The
    opening q and its matching Q must themselves be at page level: outside
    text objects, marked content, compatibility sections and path
    construction, so the scope is not interleaved with any of them. The
    value records both operators (position and bytes), the depth, and the
    state before the q, which the interpreter shows the matching Q restores.
    """
    open_after,matching=structure;stack=open_after[ordinal]
    if len(stack)!=boundaries[ordinal].q_depth:return None,'unproven-graphics-state-scope'
    if not stack:return None,None
    if len(stack)>1:return None,'nested-graphics-state-save'
    q=stack[0];closing=matching.get(q)
    if closing is None or not q<=ordinal<closing:return None,'unproven-graphics-state-scope'
    around=[boundaries[q],boundaries[closing]]+([boundaries[q-1]] if q else [])
    if any(b.text_object or b.marked_content_depth or b.compatibility_depth or b.pending_path or b.pending_clip
           for b in around):
        return None,'unproven-graphics-state-scope'
    restored=_state(boundaries[q-1]) if q else _state_value(State())
    if _state(boundaries[closing])!=restored:return None,'unproven-graphics-state-scope'
    return dict(policy=SCOPE,depth=1,opening=_operator(data,ops[q],q),matching=_operator(data,ops[closing],closing),
                restored_state=restored,contract=SCOPE_CONTRACT),None


def _scope_witness(scope):
    """A scope's witnesses without its program positions, which move with edits before them."""
    if scope is None:return None
    return dict(scope,**{k:{f:scope[k][f] for f in ('operator','sha256')} for k in ('opening','matching')})


def _scope_location(scope):
    """Where a scope's opening q and matching Q are in its revision, for a binding."""
    return {k:dict(start=scope[k]['start'],end=scope[k]['end']) for k in ('opening','matching')}


def carried_scope(program_map, location):
    """A binding's scope location carried through one save's byte mutation map.

    The opening q and the matching Q each move with the mutations before
    them; a mutation that consumes either one (one that crosses the scope's
    edge) leaves no successor and is refused.
    """
    result={}
    for key,value in location.items():
        start=program_map.map_offset(value['start'])
        result[key]=dict(start=start,end=start+value['end']-value['start'])
    return result


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


def _outward(value, up):
    """The exact ``value`` as a float, rounded up when ``up``, else down."""
    result=float(value)
    if up and Fraction(result)<value:result=math.nextafter(result,math.inf)
    elif not up and Fraction(result)>value:result=math.nextafter(result,-math.inf)
    return result


def _clip_rectangle(entry, data, ops, ctms, xref, page_transform):
    """One path clip of the page program: ``((witness, exact rectangle), None)`` or ``(None, reason)``."""
    rule,path,at=entry.get('rule'),entry.get('path'),entry.get('at')
    if rule=='text clipping':return None,'text-clip'
    if rule not in ('W','W*') or not isinstance(path,list):return None,'unproven-clip'
    # One subpath, and a rectangle: not a polygon, curve or several subpaths.
    if len(path)!=1 or path[0]['operator']!='re':return None,'nonrectangular-clip'
    # Set at page level by the three consecutive operators re, W or W*, n: no
    # cm or other path between them, so the CTM of the path is the clip's.
    index=at[1] if isinstance(at,list) and len(at)==2 and at[0]==xref else None
    if (type(index) is not int or not 2<=index<len(ops)
            or [op.name for op in ops[index-2:index+1]]!=['re',rule,'n']):
        return None,'unproven-clip'
    values,ctm,exact=path[0]['args'],tuple(path[0]['ctm']),ctms[index-2]
    if (len(values)!=4 or [float(v) for v in ops[index-2].args]!=values or exact is None
            or not all(map(math.isfinite,(*values,*ctm)))):
        return None,'unproven-clip'
    matrices=(tuple(map(Fraction,ctm)),exact)
    # Without rotation or skew the rectangle stays one axis-aligned page rectangle.
    if any(m[1] or m[2] for m in matrices):return None,'rotated-clip'
    x,y,w,h=map(Fraction,values)
    if any(v and not MAGNITUDE[0]<=abs(v)<=MAGNITUDE[1] for v in (x,y,w,h,*matrices[0],*matrices[1])):
        return None,'unproven-clip'
    page=tuple(map(Fraction,page_transform));u=Fraction(UNIT_ROUNDOFF)
    gamma=ROUNDING_STEPS*u/(1-ROUNDING_STEPS*u)
    # |x|+|w| bounds |x|, |x+w| and the error of x+w composed from rounded operands.
    size=(abs(x)+abs(w),abs(y)+abs(h));rectangles=[];bound=Fraction(0)
    for m in matrices:
        t=_compose(m,page)
        corners=[_compose((0,0,0,0,px,py),t)[4:] for px in (x,x+w) for py in (y,y+h)]
        xs,ys=[p[0] for p in corners],[p[1] for p in corners]
        rectangles.append((min(xs),min(ys),max(xs),max(ys)))
        bound=max(bound,gamma*(size[0]*(abs(t[0])+abs(t[1]))+size[1]*(abs(t[2])+abs(t[3]))+abs(t[4])+abs(t[5])))
    exact_box=(max(r[0] for r in rectangles)+bound,max(r[1] for r in rectangles)+bound,
               min(r[2] for r in rectangles)-bound,min(r[3] for r in rectangles)-bound)
    witness=dict(rule=rule,operands=[float(v) for v in values],ctm=list(ctm),source_ctm=[float(v) for v in exact],
        operators=[dict(operator=op.name,sha256=hashlib.sha256(data[op.start:op.end]).hexdigest())
                   for op in ops[index-2:index+1]],
        rectangle=[float(v) for v in rectangles[0]],source_rectangle=[float(v) for v in rectangles[1]],
        rounding_bound=_outward(bound,True))
    return (witness,exact_box),None


def clip_constraint(clip, data, ops, ctms, xref, page_transform):
    """The rectangle an inherited clip certainly contains, with its proof; or why none is proven.

    ``clip`` is the interpreted clip at a boundary of the page program
    ``data`` (virtual stream ``xref``), ``ops`` its operators and ``ctms`` the
    exact CTM after each (source_ctms). Only an intersection of path clips is
    proven, each one ``x y w h re`` subpath set by the consecutive operators
    ``re W n`` or ``re W* n`` under a CTM without rotation or skew; for one
    rectangle both rules clip the same area. For each clip and each of its
    interpreted (binary32) CTM and the CTM composed exactly from the
    operands, the page rectangle is exact; the certified rectangle is their
    intersection over all clips, each shrunk on every side by
    γ·((|x|+|w|)·(|t_a|+|t_b|) + (|y|+|h|)·(|t_c|+|t_d|) + |t_e| + |t_f|), t
    the CTM composed with the page transform and γ as in compensation(); it
    is then rounded inward to binary64. Every renderer's clip contains it.
    Returns ``(None, [])`` without a clip, ``(value, [])`` or ``(None, reasons)``.
    """
    if not clip:return None,[]
    witnesses,reasons,box=[],[],None
    for entry in clip:
        value,reason=_clip_rectangle(entry,data,ops,ctms,xref,page_transform)
        if reason:
            if reason not in reasons:reasons.append(reason)
            continue
        witness,rectangle=value;witnesses.append(witness)
        box=rectangle if box is None else (max(box[0],rectangle[0]),max(box[1],rectangle[1]),
                                           min(box[2],rectangle[2]),min(box[3],rectangle[3]))
    if reasons:return None,reasons
    rectangle=[_outward(box[0],True),_outward(box[1],True),_outward(box[2],False),_outward(box[3],False)]
    if not (rectangle[0]<rectangle[2] and rectangle[1]<rectangle[3]):return None,['empty-clip']
    value=dict(policy=CLIP,rectangle=rectangle,clips=witnesses,
        proof=dict(method=CLIP_PROOF,page_transform=list(page_transform),arithmetic='binary32',
                   unit_roundoff=UNIT_ROUNDOFF,rounding_steps=ROUNDING_STEPS,
                   rounding_bound=max(w['rounding_bound'] for w in witnesses)),
        containment=dict(policy=CLIP_CONTAINMENT,destination='bounds-inside-rectangle',
                         ink='each-planned-glyph-ink-grown-by-the-margin-inside-rectangle',ink_margin=INK_MARGIN,
                         paint_padding=PAINT_PADDING,origin_tolerance=COMPENSATION_TOLERANCE,
                         saved_paint='each-generated-text-paint-envelope-inside-rectangle'))
    return json.loads(json.dumps(value)),[]


def clip_contains(constraint, box, margin=0):
    """``box`` grown by ``margin`` on every side lies inside the certified clip rectangle, exactly."""
    try:
        x0,y0,x1,y1=map(Fraction,constraint['rectangle']);a,b,c,d=map(Fraction,box);m=Fraction(margin)
    except (TypeError,ValueError,OverflowError):
        return False
    return x0<=a-m and y0<=b-m and c+m<=x1 and d+m<=y1


def require_inside_clip(destination, inks=()):
    """The destination bounds, and each planned glyph ink grown by INK_MARGIN, inside its inherited clip.

    The margin makes the plan predict the saved check of _validate_destination
    (each generated paint's envelope inside the rectangle). Nothing is
    required of a destination without a clip constraint. A box on the
    rectangle's edge is inside; anything beyond it, by any amount, is not.
    """
    clip=destination['authority'].get('clip_constraint')
    if clip is None:return
    if not clip_contains(clip,destination['bounds']):
        raise PdfError('continuation destination extends beyond its inherited rectangular clip')
    for ink in inks:
        if not clip_contains(clip,ink.tuple() if isinstance(ink,Rect) else ink,INK_MARGIN):
            raise PdfError('generated glyph ink may extend beyond its inherited rectangular clip')


def _refusals(scope, state, ctm_reason=None, clip_reasons=None, scope_reasons=None):
    """Why a block drawn at this boundary would not look like one drawn at page entry.

    ``clip_reasons`` are clip_constraint()'s and ``scope_reasons`` the reason
    enclosing_scope() gives, if any; a clip or a q scope that was not
    analyzed is refused.
    """
    reasons=[]
    if scope_reasons is None:scope_reasons=['unproven-graphics-state-scope'] if scope['q_depth'] else []
    reasons.extend(scope_reasons)
    if scope['text_object']:reasons.append('inside-text-object')
    if scope['marked_content_depth']:reasons.append('inside-marked-content')
    if scope['compatibility_depth']:reasons.append('inside-compatibility-section')
    if scope['pending_path']:reasons.append('pending-path')
    if scope['pending_clip']:reasons.append('pending-clip')
    if ctm_reason:reasons.append(ctm_reason)
    if state['clip']:reasons.extend(['unproven-clip'] if clip_reasons is None else clip_reasons)
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
    c=found[0];compensated=c.get('ctm_compensation');clipped=c.get('clip_constraint')
    enclosed=c.get('graphics_state_scope')
    value=dict(position=BOUNDARY,boundary_id=ident,source_program_sha256=c['program_sha256'],
        boundary=dict(offset=c['offset'],ordinal=c['ordinal'],previous=c['previous'],next=c['next'],scope=c['scope']),
        z_order=c['z_order'],graphics_state=c['graphics_state'],
        graphics_state_contract=_graphics_contract(compensated,clipped,enclosed),
        **c['context'],initial_clip=_initial_clip(clipped),isolation=_isolation(compensated),
        font_policy='explicit-paragraph-style-providers')
    if compensated is not None:value['ctm_compensation']=compensated
    if clipped is not None:value['clip_constraint']=clipped
    if enclosed is not None:value['graphics_state_scope']=enclosed
    return value


def _isolation(compensated):
    return 'q-BT-ET-Q' if compensated is None else 'q-cm-BT-ET-Q'


def _initial_clip(clipped):
    return 'page-crop-box' if clipped is None else CLIP


def _graphics_contract(compensated, clipped, enclosed=None):
    steps=(['cancels the witnessed CTM with the recorded inverse'] if compensated is not None else [])+(
        ['draws inside the witnessed rectangular clip, which it inherits and never changes']
        if clipped is not None else [])
    return (('witnessed page-level state' if enclosed is None else
             'witnessed state inside one confirmed q ... Q scope')+'; the block '
            +', '.join(steps+['sets its own font, text state and fill'])
            +' and restores every parameter with its own q ... Q'
            +('' if enclosed is None else ', which closes before the scope\'s matching Q'))


def _boundary_value(content, data, sha, d, generated, legacy, location, scope_location=None):
    """Verify one confirmed page-program boundary in this revision.

    The block is found by its own unique markers; an unused boundary by the
    location carried from the previous revision. Either way the operator
    ending exactly there and the next source operator must be the confirmed
    ones (same operator, same bytes), and the scope and state there must be
    the confirmed, still safe, ones. An offset alone never identifies it.

    A boundary inside a q ... Q scope must still be inside exactly one: the
    one this revision's structure puts around it must have the confirmed
    witnesses (bytes, depth, restored state), its opening q and matching Q
    must be where ``scope_location`` says the previous revision's ones are
    now (or, in the confirmed program, the confirmed ones), and the block or
    unused boundary must lie between them.
    """
    auth=d['authority'];confirmed=auth['boundary'];sid=slot_id(d);begin,end=_markers(sid)
    compensated=auth.get('ctm_compensation')
    if auth['boundary_id']!=boundary_id(d['page'],auth['source_program_sha256'],confirmed['previous'],confirmed['next'],
                                        auth.get('graphics_state_scope')):
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
    # The CTM, the clip and the enclosing q ... Q scope are part of the
    # authority: another one is another authority. The compensation, the
    # clip rectangle and the scope are re-derived from this revision's
    # state and program, never read back.
    enclosing=auth.get('graphics_state_scope')
    ops=list(operators(data)) if state['clip'] or b.q_depth or enclosing is not None else []
    ctms=source_ctms(data) if state['ctm']!=list(IDENTITY) or state['clip'] else None
    expected,reason=_compensation(state,ctms[b.ordinal] if ctms else None,auth)
    clipped,clip_reasons=clip_constraint(b.state.clip,data,ops,ctms,-content.page.xref,auth['page_transform'])
    derived,scope_reason=enclosing_scope(data,ops,graphics_scopes(ops),items,b.ordinal) if ops else (None,None)
    if (scope!=confirmed['scope'] or state!=auth['graphics_state']
            or _refusals(scope,state,reason,clip_reasons,[scope_reason] if scope_reason else [])):
        raise PdfError('confirmed page-program boundary state or scope differs from its authority')
    if compensated!=expected or auth['isolation']!=_isolation(expected):
        raise PdfError('confirmed page-program boundary CTM compensation differs from its authority')
    if auth.get('clip_constraint')!=clipped or auth['initial_clip']!=_initial_clip(clipped):
        raise PdfError('confirmed page-program boundary clip constraint differs from its authority')
    if _scope_witness(enclosing)!=_scope_witness(derived):
        raise PdfError('confirmed page-program boundary q ... Q scope differs from its authority')
    if derived is not None:
        # Which q and Q: the ones carried from the previous revision, or in
        # the confirmed program the confirmed ones. Equal bytes elsewhere are
        # another scope.
        confirmed_program=sha==auth['source_program_sha256']
        carried=scope_location if scope_location is not None else _scope_location(enclosing) if confirmed_program else None
        if carried is None:
            raise PdfError('a confirmed page-program boundary inside a q ... Q scope needs its scope location in this revision')
        if (_scope_location(derived)!=carried or confirmed_program and scope_location is None
                and (derived['opening'],derived['matching'])!=(enclosing['opening'],enclosing['matching'])):
            raise PdfError('confirmed page-program boundary is not in its confirmed q ... Q scope')
        # The block, or the unused boundary, never leaves the scope.
        if not derived['opening']['end']<=offset<=after<=derived['matching']['start']:
            raise PdfError('generated continuation block is not inside its confirmed q ... Q scope')
    if auth['graphics_state_contract']!=_graphics_contract(expected,clipped,derived):
        raise PdfError('confirmed page-program boundary graphics-state contract differs from its authority')
    value['boundary']=dict(offset=offset,previous=dict(start=previous['start'],end=previous['end']),
                           next=dict(start=following['start'],end=following['end']))
    if derived is not None:value['scope']=_scope_location(derived)
    if generated:
        value.update(start=offset,end=stop,block_sha256=hashlib.sha256(data[offset:stop]).hexdigest())
        if not legacy:value['operator_nesting']=OPERATOR_NESTING
    return value,fonts


def boundary_id(page, program_sha256, previous, following, scope=None):
    """A boundary's ID from its source witnesses, and its q ... Q scope's if it has one."""
    witnesses=[page,program_sha256,previous['ordinal'],previous['end'],previous['sha256'],following['sha256']]
    if scope is not None:
        witnesses+=[scope[k][f] for k in ('opening','matching') for f in ('ordinal','end','sha256')]
    return 'boundary-'+digest(witnesses)[:24]


def inspect_continuation_boundaries(source, page, *, include_refused=False):
    """List the page-level operator boundaries of one page program.

    Each boundary lies between two complete top-level operators of the merged
    /Contents program: never offset 0 (the page entry), never after the last
    operator. A ``safe`` candidate satisfies every condition of the module
    docstring; a caller may confirm it with its ``boundary_id``. A candidate
    under a clip carries its ``clip_constraint``: a destination confirmed
    there must lie inside its rectangle. A candidate inside a q ... Q scope
    carries its ``graphics_state_scope``. The witnesses come from the same
    interpretation that edits the page. Nothing here chooses a boundary from
    destination geometry.
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
    clipped=any(b.state.clip for b in items[:-1])
    exact=source_ctms(data) if clipped or any(b.state.ctm!=IDENTITY for b in items[:-1]) else None
    saved=any(b.operator.name=='q' for b in items)
    ops=list(operators(data)) if clipped or saved else [];clips={}
    structure=graphics_scopes(ops) if saved else None
    for index,b in enumerate(items[:-1]):
        previous=_operator(data,b.operator,b.ordinal)
        following=_operator(data,items[index+1].operator,items[index+1].ordinal)
        scope,state=_scope(b),_state(b)
        compensated,reason=_compensation(state,exact[b.ordinal] if exact else None,page_context)
        # Boundaries after the same clip operators share their clip entries.
        key=tuple(id(c) for c in b.state.clip)
        if key not in clips:
            clips[key]=clip_constraint(b.state.clip,data,ops,exact,-content.page.xref,page_context['page_transform'])
        constraint,clip_reasons=clips[key]
        enclosed,scope_reason=enclosing_scope(data,ops,structure,items,b.ordinal) if saved else (None,None)
        reasons=invalid+_refusals(scope,state,reason,clip_reasons,[scope_reason] if scope_reason else [])
        value=dict(page=page,boundary_id=boundary_id(page,sha,previous,following,enclosed),program_sha256=sha,
            offset=b.operator.end,ordinal=b.ordinal,previous=previous,next=following,scope=scope,graphics_state=state,
            z_order=dict(semantics=Z_ORDER,prefix_paint_operators=sum(p<=b.ordinal for p in paints),
                         suffix_paint_operators=sum(p>b.ordinal for p in paints)),
            context=page_context,status='refused' if reasons else 'safe',reasons=reasons)
        if compensated is not None:value['ctm_compensation']=compensated
        # The candidate carries the constraint its destination must satisfy.
        if constraint is not None:value['clip_constraint']=constraint
        if enclosed is not None:value['graphics_state_scope']=enclosed
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
    the confirmed prefix and before all paint of the confirmed suffix. At a
    candidate under a clip, ``bounds`` must lie inside its clip rectangle.

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
        require_inside_clip(dict(authority=witness,bounds=bounds))
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


def page_witness(content, destinations, generated, legacy=frozenset(), locations=None, scopes=None):
    """Witness every confirmed destination of one page against its own authority.

    ``generated`` names the destinations whose block must exist. Page-entry
    blocks form a contiguous prefix of the page program in confirmed order,
    before the original program, each with exactly one pair of its own
    markers and no bytes between them. Each page-program boundary is verified
    on its own (``_boundary_value``); ``locations`` gives, for an unused
    boundary, its offset carried into this revision, and defaults to the
    confirmed offset in the confirmed program. Destinations without a block
    have no marker. ``legacy`` names blocks whose stored binding predates
    OPERATOR_NESTING. ``scopes`` gives, for a boundary inside a q ... Q
    scope, where that scope's q and Q are in this revision (the previous
    binding's, carried; see carried_scope), and defaults to the confirmed
    ones in the confirmed program. Returns each destination's witness and
    the font aliases its block selects.
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
            result[ident],own=_boundary_value(content,data,sha,d,ident in generated,ident in legacy,location,
                                              (scopes or {}).get(ident))
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


def witness(content, destination, location=None, scope=None):
    """Witness of a destination that has no generated block yet."""
    ident=destination['destination_id']
    return page_witness(content,[destination],set(),locations=None if location is None else {ident:location},
                        scopes=None if scope is None else {ident:scope})[0][ident]


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
        # A boundary inside a q ... Q scope: the scope is verified where this
        # revision's binding says its q and Q are.
        scopes={d['destination_id']:bindings[d['destination_id']]['scope'] for d in ordered
                if is_boundary(d) and 'scope' in bindings[d['destination_id']]}
        content=ContentPage(source,page)
        try:
            current,fonts=page_witness(content,ordered,generated,legacy,locations,scopes)
            for d in ordered:_validate_destination(content,state,d,bindings[d['destination_id']],
                current[d['destination_id']],fonts.get(d['destination_id']),records)
        finally:content.close()


def _validate_destination(content,state,d,binding,current,fonts,records):
    ident,pid,rid,sid=d['destination_id'],d['paragraph_id'],d['region_id'],slot_id(d)
    if binding!=current:raise PdfError('continuation program witness is stale')
    generated=state['slots'].get(sid)
    owned=set(generated['binding']['paragraph']['selection']['glyph_ids']) if generated else set()
    require_inside_clip(d)
    # Only this destination's own generated glyphs are exempt; every other
    # glyph, including another destination's generated text, is an obstacle.
    # Paint the clip hides is still paint.
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
    # inverse, exactly as the interpreter composes them; its clip is the
    # witnessed one, which the recorded inverse does not change.
    expected=d['authority']['graphics_state']['other'] if is_boundary(d) else {}
    clip=d['authority']['graphics_state']['clip'] if is_boundary(d) else []
    ctm=block_ctm(d) if is_boundary(d) else IDENTITY
    for e in events:
        s=e.state
        other={k:v for k,v in s.other.items() if k!='marked_content'} if is_boundary(d) else s.other
        if (s.ctm!=ctm or clip_state(s.clip)!=clip or json.loads(json.dumps(other))!=expected
                or s.opacity!=1 or s.stroke_opacity!=1 or s.tr!=0):
            raise PdfError('generated continuation graphics state differs from its confirmed authority')
    if any(set(c.source_orders)-owned for e in content.events if not e.invocation and start<=e.operator.start<stop
           for c in e.chars):
        raise PdfError('generated block contains text owned by another slot')
    constraint=d['authority'].get('clip_constraint')
    if constraint is not None:
        # The renderer's own envelope of each generated text paint in this
        # revision, not only the planned ink, lies inside the clip rectangle.
        # An empty envelope (a blank glyph) paints nothing.
        paints=content.page.get_bboxlog()
        for seqno in sorted({content.actual[i]['span']['seqno'] for i in owned}):
            kind,(x0,y0,x1,y1)=paints[seqno]
            if kind!='fill-text' or not (x0>x1 or y0>y1 or clip_contains(constraint,(x0,y0,x1,y1))):
                raise PdfError('saved generated text paint extends beyond its inherited rectangular clip')
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
                        or witness(self.content,snapshot['destination'],location,
                                   snapshot['destination_binding'].get('scope'))!=snapshot['destination_binding']):
                    raise PdfError('continuation insertion program changed')
                self.entry=location
            else:
                if witness(self.content,snapshot['destination'])!=snapshot['destination_binding']:
                    raise PdfError('continuation insertion program changed')
                self.entry=verify_entry(self.content,snapshot['destination'],snapshot['page_entry'])
            require_inside_clip(snapshot['destination'])
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
