"""Explicit container-owned band paints above the existing fixed-flow writer.

Only the bottom edge can move. Horizontal bounds and the top stay fixed;
available space, baseline-to-bottom gap, minimum size and paint extrusion are all
caller-confirmed policies. Shared content is owned by the container, not one
paragraph. This layer introduces no compound source writer.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile

from .attributed import digest
from .backend import PdfError
from .document_flow import _rebind, _reseal, open_document
from .elements import _close, _paint_value
from .flow_transaction import plan_flow, edit_flow_batch
from .model import Rect
from .paint_resize import MODEL, extend_geometry, resize_paints
from .proof_session import proof_session
from .replay import ensure_destination
from .selection import source_sha


def _document(source, value):
    result=open_document(source,value)
    if result['status']!='restored': raise PdfError(result['reason'])
    return result['state']


def _paths(document):
    return next(iter(document['elements'].values()))['binding']['element']['paths']


def _with_bottom(document,bottom):
    """Apply a bottom derived from the confirmed outer policy, never the bbox."""
    result=deepcopy(document); result['container']['bounds'][3]=bottom
    for entry in result['elements'].values():
        b=entry['binding']; b['layout']['max_bottom']=bottom
        b['container']['region']['bottom']=bottom
        b['layout_provenance']['max_bottom']='derived_from_confirmed_container_resize'
        entry['binding']=_reseal(b)
    return _reseal(result)


def _validate(source, value):
    state=deepcopy(value); checksum=state.pop('model_sha256',None)
    if state.get('schema')!='pdfengine-variable-container-1' or checksum!=digest(state):
        raise PdfError('variable container model version or checksum is invalid')
    state['model_sha256']=checksum
    if state['pdf_sha256']!=source_sha(source): raise PdfError('variable container PDF revision changed')
    document=_document(source,state['document']); policy=state['layout_policy']; owner=state['semantic_owner']
    c=document['container']; paths={p['source_id']:p for p in _paths(document)}
    if (owner!=dict(id=c['id'],kind='container',children=list(document['elements']),provenance='explicitly_confirmed')
            or policy.get('sizing')!='bottom-variable' or policy.get('fixed_edges')!=['left','top','right']
            or policy.get('movable_edges')!=['bottom'] or policy.get('overflow')!='reject'
            or policy.get('provenance')!='explicitly_confirmed' or policy.get('padding')!='unknown'):
        raise PdfError('unsupported semantic owner or variable container policy')
    minimum,maximum,gap=(policy[k] for k in ('min_bottom','max_bottom','baseline_bottom_gap'))
    if (not all(math.isfinite(v) for v in (minimum,maximum,gap)) or minimum>maximum or gap<0
            or not minimum<=c['bounds'][3]<=maximum or c['bounds'][:3]!=policy['fixed_bounds']):
        raise PdfError('bottom range, fixed edges or explicit baseline-to-bottom gap is invalid')
    available=Rect(*policy['available_paint_region'])
    if not all(math.isfinite(v) for v in available.tuple()) or available.width<=0 or available.height<=0:
        raise PdfError('paint available region must be explicitly finite and positive')
    fixed=state['fixed_children']
    if any(e['binding']['layout']['max_bottom']!=c['bounds'][3] for e in document['elements'].values()):
        raise PdfError('variable children must share the explicitly confirmed content bottom')
    if set(fixed)-set(document['elements']): raise PdfError('unknown fixed child')
    for ident, anchor in fixed.items():
        b=document['elements'][ident]['binding']
        if not _close(anchor,dict(text=b['paragraph']['text'],x=b['layout']['x'],baseline=b['layout']['baseline'])):
            raise PdfError('a fixed child changed its logical content or anchor')
    paints=state['paints']
    if not paints or len({p['source_id'] for p in paints})!=len(paints):
        raise PdfError('container needs unique explicitly owned paints')
    paragraph_owned={p for e in document['elements'].values() for p in e['owned_paints']}
    for p in paints:
        path=paths.get(p['source_id'])
        if (p.get('owner')!=owner['id'] or p.get('role') not in ('backgrounds','borders')
                or p.get('geometry_model')!=MODEL or p.get('provenance')!='explicitly_confirmed'
                or p.get('bottom_anchor')!='preserve-offset-to-container-bottom'
                or p['source_id'] in paragraph_owned or path is None
                or path['proof']['status']!='proven' or len(path['proof']['paints'])!=1):
            raise PdfError('container paint ownership or source correspondence is invalid')
        paint=path['proof']['paints'][0]
        zero=extend_geometry(paint,p['band'],0)
        if not available.contains(Rect(*zero['expected']['bounds']),.002):
            raise PdfError('current container paint is outside its confirmed region')
        # These anchors bind paint geometry independently from ownership.
        if not _close(p['fixed_bounds'],paint['bounds'][:3]) or abs(p['bottom_offset']-(paint['bounds'][3]-c['bounds'][3]))>.002:
            raise PdfError('container paint changed its fixed edges or bottom anchor')
        if minimum<=p['band'][1]: raise PdfError('minimum bottom must leave the confirmed band intact')
    return state


@proof_session
def confirm_variable_container(source, document, *, paints, min_bottom, max_bottom, baseline_bottom_gap,
                               available_paint_region, fixed_children):
    """Confirm ownership AND explicit band/edge/space policy without a PDF edit.

    Each paint input supplies source_id, role, band, geometry_model and an
    explicit preserve-offset-to-container-bottom anchor. Its
    observed bottom offset records the explicitly selected anchor; it does
    not choose a resize direction, region or padding from geometry.
    """
    document=_document(source,document); c=document['container']
    if min_bottom!=c['bounds'][3]: raise PdfError('import minimum must equal the confirmed initial content bottom')
    if len(set(fixed_children))!=len(fixed_children) or set(fixed_children)-set(document['elements']):
        raise PdfError('fixed children must identify known distinct elements')
    paths={p['source_id']:p for p in _paths(document)}; confirmed=[]
    for spec in paints:
        if set(spec)!={'source_id','role','band','geometry_model','bottom_anchor'} or spec['source_id'] not in paths:
            raise PdfError('paint needs explicit source, role, band, geometry model and bottom anchor')
        values=paths[spec['source_id']]['proof'].get('paints',[])
        if len(values)!=1: raise PdfError('resize needs one proven fill event per source path')
        confirmed.append(dict(spec,owner=c['id'],provenance='explicitly_confirmed',
                              fixed_bounds=values[0]['bounds'][:3],bottom_offset=values[0]['bounds'][3]-c['bounds'][3]))
    state=dict(schema='pdfengine-variable-container-1',pdf_sha256=source_sha(source),document=document,
        semantic_owner=dict(id=c['id'],kind='container',children=list(document['elements']),provenance='explicitly_confirmed'),
        layout_policy=dict(sizing='bottom-variable',fixed_edges=['left','top','right'],movable_edges=['bottom'],
            fixed_bounds=c['bounds'][:3],min_bottom=min_bottom,max_bottom=max_bottom,baseline_bottom_gap=baseline_bottom_gap,
            available_paint_region=list(available_paint_region),overflow='reject',padding='unknown',provenance='explicitly_confirmed'),
        paints=confirmed,fixed_children={i:dict(text=document['elements'][i]['binding']['paragraph']['text'],
            x=document['elements'][i]['binding']['layout']['x'],baseline=document['elements'][i]['binding']['layout']['baseline']) for i in fixed_children},
        previous_model_sha256=None)
    return _validate(source,_reseal(state))


@proof_session
def open_variable_container(source, model):
    try:
        value=model if isinstance(model,dict) else json.loads(Path(model).read_text(encoding='utf-8'))
        return dict(status='restored',state=_validate(source,value))
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError,PdfError) as exc:
        return dict(status='needs_confirmation',reason=str(exc),semantics='unknown')


def _restore(source, model):
    result=open_variable_container(source,model)
    if result['status']!='restored': raise PdfError('variable edit requires confirmation: '+result['reason'])
    return result['state']


def _plan(source, state, changes):
    if not isinstance(changes,dict) or set(changes)&set(state['fixed_children']):
        raise PdfError('edits cannot change a confirmed fixed child')
    policy=state['layout_policy']; document=state['document']
    capacity=_with_bottom(document,policy['max_bottom'])
    layout=plan_flow(source,capacity,changes)
    # A source renderer's normalized font bbox is not the writer's em ascent /
    # descent. Do not use that unstable observation as a semantic size anchor.
    # The caller confirms a gap from the stable logical LAST BASELINE instead.
    low=max(policy['min_bottom'],max(p['last_baseline'] for p in layout['final_positions'].values())+policy['baseline_bottom_gap'])
    if low>policy['max_bottom']: raise PdfError('planned logical extent and baseline gap exceed maximum bottom')
    for ident,position in layout['final_positions'].items():
        if ident in state['fixed_children'] and abs(position['baseline']-state['fixed_children'][ident]['baseline'])>.002:
            raise PdfError('follows would move a fixed child')
        measured=layout['paragraph_plans'].get(ident)
        b=document['elements'][ident]['binding']
        if measured:
            bottom=max((line['baseline']+line['descent'] for line in measured['lines']),default=position['baseline'])
        else:
            box=b['element']['text_element']['observed_bounds']
            bottom=box['y1']+position['baseline']-b['layout']['baseline'] if box else position['baseline']
        if bottom>low+.002:
            raise PdfError('confirmed baseline-to-bottom gap does not contain the measured text extent')
    delta=low-document['container']['bounds'][3]; paths={p['source_id']:p for p in _paths(document)}
    geometry={}
    for p in state['paints']:
        proof=extend_geometry(paths[p['source_id']]['proof']['paints'][0],p['band'],delta)
        if not Rect(*policy['available_paint_region']).contains(Rect(*proof['expected']['bounds']),.002):
            raise PdfError('planned paint exceeds its confirmed available region')
        geometry[p['source_id']]=proof
    return dict(schema='pdfengine-variable-plan-1',source_sha256=state['pdf_sha256'],model_sha256=state['model_sha256'],
                layout=layout,current_bottom=document['container']['bounds'][3],final_bottom=low,delta=delta,paint_plans=geometry,
                execution='resize then edit' if delta>0 else 'edit then resize' if delta<0 else 'edit only',
                collision_status='not_certified; resize and text writer guards still required')


@proof_session
def plan_variable_container(source, model, changes):
    return _plan(source,_restore(source,model),changes)


def _remap(before, after, paints):
    old,new=_paths(before),_paths(after)
    if len(old)!=len(new): raise PdfError('container paint source order changed')
    mapping={a['source_id']:b['source_id'] for a,b in zip(old,new)}
    return [dict(p,source_id=mapping[p['source_id']]) for p in paints]


@proof_session
def edit_variable_container(source, model, output, model_output, changes):
    initial=_restore(source,model); changes=deepcopy(changes); plan=_plan(source,initial,changes)
    output=ensure_destination(output,source); model_output=ensure_destination(model_output,source)
    if output==model_output: raise PdfError('PDF and model need different destinations')
    output.parent.mkdir(parents=True,exist_ok=True); reports=[]
    with tempfile.TemporaryDirectory(prefix='.variable-',dir=output.parent) as directory:
        root=Path(directory); current=Path(source); state=deepcopy(initial); document=state['document']
        def resize():
            nonlocal current, document
            b=next(iter(document['elements'].values()))['binding']; target=root/'resize.pdf'
            report=resize_paints(current,target,b['element'],state['paints'],delta=plan['delta'],
                available_bounds=state['layout_policy']['available_paint_region'],paragraph_snapshot=b['paragraph'])
            if report['source_sha256']!=source_sha(current) or report['output_sha256']!=source_sha(target):
                raise PdfError('paint mutation receipt belongs to a different revision')
            replacement={i:[p['expected']] for i,p in report['plans'].items()}
            updated=deepcopy(document)
            updated['elements']={i:_rebind(current,target,e,report['byte_edits'],paint_replacements=replacement)
                                  for i,e in document['elements'].items()}
            updated['pdf_sha256']=source_sha(target)
            updated=_document(target,_with_bottom(updated,plan['final_bottom']))
            state['paints']=_remap(document,updated,state['paints'])
            reports.append(dict(kind='resize',report=report)); current,document=target,updated
        if plan['delta']>0: resize()
        target=root/'edited.pdf'; sidecar=root/'edited.json'
        batch=edit_flow_batch(current,document,target,sidecar,changes)
        updated=_document(target,sidecar)
        state['paints']=_remap(document,updated,state['paints'])
        reports.append(dict(kind='edit',report=batch)); current,document=target,updated
        if plan['delta']<0: resize()
        positions={i:dict(baseline=e['binding']['layout']['baseline'],last_baseline=e['extent']['last_baseline']) for i,e in document['elements'].items()}
        if not _close(positions,plan['layout']['final_positions']): raise PdfError('variable writer positions differ from the final plan')
        if document['follows']!=initial['document']['follows']: raise PdfError('resize changed a semantic follows relation')
        if source_sha(source)!=initial['pdf_sha256']: raise PdfError('variable input revision changed before publication')
        document['previous_model_sha256']=initial['document']['model_sha256']; document=_reseal(document)
        state.update(document=document,pdf_sha256=source_sha(current),previous_model_sha256=initial['model_sha256'])
        state=_validate(current,_reseal(state)); state_file=root/'variable.json'
        state_file.write_bytes((json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
        published=[]
        try:
            for temporary,destination in ((current,output),(state_file,model_output)):
                destination.parent.mkdir(parents=True,exist_ok=True); os.link(temporary,destination); published.append(destination)
        except Exception:
            for destination in published: destination.unlink()
            raise
    return dict(backend='explicit-variable-container',plan=plan,steps=reports,pdf_sha256=state['pdf_sha256'],
                model_sha256=state['model_sha256'],semantic_policy_verified=True,final_layout_verified=True,
                publication='all guarded steps verified; rollback on exception')
