"""Explicit paragraph identities and follows relations above physical PDF bindings.

One fixed, confirmed page region; one paragraph edit per transaction. Edges
relate the follower's first baseline to its predecessor's last baseline.
An empty paragraph reserves its confirmed first baseline. No edge or paint
ownership is ever inferred from a coordinate, bounding box, or paint order.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import re

import pymupdf

from .attributed import digest, inspect_paragraph
from .backend import PdfError
from .content_stream import multiply, operators
from .editable import _seal, open_editable
from .elements import _close, _confirmed, _local_translation, _paint_value, inspect_element
from .logical_element import paragraph_from_snapshot, slot_binding
from .model import Rect
from .mutation import map_offset
from .paragraph import _layout_parameters
from .selection import make_selection, source_sha
from .proof_session import proof_session


def _reseal(state):
    value=deepcopy(state);value.pop('model_sha256',None)
    return _seal(value)


def _fonts(state):
    result={}
    for ident,spec in state['fonts'].items():
        if source_sha(spec['path'])!=spec['sha256']:
            raise PdfError('saved supplied-font recipe changed')
        result[ident]={k:v for k,v in spec.items() if k!='sha256'}
    return result


def _fixed_relations(binding):
    """Fixed paint relations of a binding, whether declared directly or inside its anchors."""
    anchors=binding.get('anchors')
    return deepcopy(anchors.get('fixed_relations',[]) if anchors is not None else binding.get('relations') or [])


def _element_relations(element, fixed, anchors):
    """Every relation the element paint model must resolve: fixed paint plus decorations."""
    decorated=[i for u in (anchors or {}).get('underlines',[]) for i in u['source_ids']] if anchors else []
    known={p['source_id'] for p in element['paths']}
    if any(i not in known for i in decorated):
        raise PdfError('anchored decoration references an unknown source path')
    return list(fixed)+[dict(source_id=i,relation='decorates',behavior='fixed-to-element') for i in decorated]


def _initial(source, ident, region, spec):
    snapshot=spec['paragraph']
    paragraph=paragraph_from_snapshot(source,snapshot)
    try:
        if paragraph.snapshot()!=snapshot:
            raise PdfError('confirmed paragraph snapshot changed')
        _,options=_layout_parameters(paragraph,snapshot,**spec['layout'])
    finally:paragraph.close()
    layout={k:options[k] for k in ('x','baseline','width','first_line_indent','min_line_height','max_bottom')}
    if spec['layout'].get('width') is None:
        raise PdfError('document import requires explicitly confirmed available width')
    fonts={i:dict(s,path=str(Path(s['path']).resolve()),sha256=source_sha(s['path']))
           for i,s in spec.get('fonts',{}).items()}
    paint=inspect_element(source,snapshot['selection'],paragraph_snapshot=snapshot)
    anchors=deepcopy(spec.get('anchors'))
    if anchors is not None:
        if spec.get('paint_relations'):
            raise PdfError('anchored elements carry their fixed relations inside the anchor specification')
        if (anchors.get('paragraph_sha256')!=snapshot['snapshot_sha256'] or anchors.get('element_sha256')!=paint['snapshot_sha256']
                or not isinstance(anchors.get('underlines'),list) or not anchors['underlines']):
            raise PdfError('anchor specification must reference this paragraph and element snapshot')
        relations=None
        fixed=deepcopy(anchors.get('fixed_relations',[]))
    else:
        relations=fixed=deepcopy(spec.get('paint_relations',[]))
    _confirmed(paint,_element_relations(paint,fixed,anchors))
    if any(r['behavior']!='fixed-to-page' for r in fixed):
        raise PdfError('declare ownership separately; text-edit paint relations stay fixed-to-page')
    if anchors is not None and set(spec.get('owned_paints',[]))&{i for u in anchors['underlines'] for i in u['source_ids']}:
        raise PdfError('anchored decoration is not a separately owned paint')
    observed=snapshot['layout_suggestion']['base_baselines']
    last=observed[-1] if observed else layout['baseline']
    state=_seal(dict(schema='pdfengine-editable-2',pdf_sha256=source_sha(source),paragraph=snapshot,
        logical_element=dict(id=ident,kind='paragraph',contained_by=region,
            provenance='caller_confirmed_selection',alignment=dict(value='left',provenance='generated_layout_policy')),
        element=paint,anchors=anchors,relations=relations,fonts=fonts,layout=layout,
        layout_provenance={k:'explicitly_confirmed' if spec['layout'].get(k) is not None else
                           'generated-by-pdfengine' if k=='min_line_height' else 'observed_source' for k in layout},
        boundaries=[dict(offset=m.start(),end=m.end(),kind='hard_break',provenance='explicitly_confirmed')
                    for m in re.finditer(r'\r\n|\r|\n',snapshot['text'])],
        physical_layout=dict(provenance='observed_source',lines=[dict(baseline=b) for b in observed]),
        container=dict(kind='text_region',sizing='fixed',overflow='reject',padding=None,padding_provenance='unknown',
            region=dict(x=layout['x'],width=layout['width'],first_baseline=layout['baseline'],bottom=layout['max_bottom']),
            follows=[],follows_provenance='not_declared',ownership='only explicitly confirmed paint relations'),
        previous_model_sha256=None))
    if spec.get('paragraph_layout') is not None:
        from .alignment import confirm
        state['logical_element']['alignment']=confirm(snapshot,spec['paragraph_layout'])
        state=_reseal(state)
    return dict(binding=state,owned_paints=list(spec.get('owned_paints',[])),
                extent=dict(last_baseline=last,provenance='observed_source' if observed else 'explicit_empty_policy'))


@proof_session
def confirm_document(source, elements, *, container_id, bounds, page, follows):
    """Import caller-reviewed ranges/widths/ownership without rewriting the PDF.

    ``follows`` is a list of {before, after, gap} declarations in points.
    Gap is last-baseline to first-baseline spacing, including an empty element.
    Padding is unknown; bounds are an explicitly supplied usable region.
    """
    state=_seal(dict(schema='pdfengine-document-1',pdf_sha256=source_sha(source),
        elements={i:_initial(source,i,container_id,s) for i,s in elements.items()},
        container=dict(id=container_id,page=page,bounds=list(bounds),children=list(elements),
            provenance='explicitly_confirmed',sizing='fixed',overflow='reject',padding=None,padding_provenance='unknown'),
        follows=[dict(r,provenance='explicitly_confirmed') for r in follows],
        extent_policy='last-baseline; empty-reserves-first-baseline',previous_model_sha256=None))
    return _validate(source,state)


def _order(state):
    ids=state['elements'];incoming={};outgoing={i:[] for i in ids}
    for r in state['follows']:
        a,b=r['before'],r['after']
        if (a not in ids or b not in ids or b in incoming or a==b or
                r['provenance']!='explicitly_confirmed' or not math.isfinite(r['gap']) or r['gap']<=0):
            raise PdfError('follows needs known distinct identities, one predecessor and a positive confirmed gap')
        incoming[b]=a;outgoing[a].append(b)
    order=[];queue=[i for i in ids if i not in incoming]
    while queue:
        i=queue.pop(0);order.append(i);queue.extend(outgoing[i])
    if len(order)!=len(ids):raise PdfError('cyclic follows relations are unsupported')
    return order,outgoing


def _validate(source, value):
    state=deepcopy(value);checksum=state.pop('model_sha256',None)
    if state.get('schema')!='pdfengine-document-1' or checksum!=digest(state):
        raise PdfError('document model version or checksum is invalid')
    state['model_sha256']=checksum
    if state['pdf_sha256']!=source_sha(source):raise PdfError('PDF revision differs from the document model')
    c=state['container'];elements=state['elements']
    if (not elements or c['children']!=list(elements) or c['sizing']!='fixed' or c['overflow']!='reject'
            or c['padding'] is not None or c['padding_provenance']!='unknown' or c['provenance']!='explicitly_confirmed'
            or state['extent_policy']!='last-baseline; empty-reserves-first-baseline'):
        raise PdfError('unsupported document container or extent policy')
    region=Rect(*c['bounds'])
    if not all(math.isfinite(v) for v in region.tuple()) or region.width<=0 or region.height<=0:
        raise PdfError('container needs finite positive bounds')
    used=set();owned=set();slots=set()
    for ident,entry in elements.items():
        b=entry['binding'];p=b['paragraph'];layout=b['layout']
        if (b['logical_element']['id']!=ident or b['logical_element']['contained_by']!=c['id']
                or p['selection']['page']!=c['page']):
            raise PdfError('element identity or page differs from the document')
        if b['anchors'] is not None and (b['anchors']['paragraph_sha256']!=p['snapshot_sha256']
                or b['anchors']['element_sha256']!=b['element']['snapshot_sha256']):
            raise PdfError('anchored decoration no longer references the bound paragraph and paint')
        restored=open_editable(source,b)
        if restored['status']!='restored':raise PdfError(restored['reason'])
        if not Rect(*b['element']['page_bounds']).contains(region):
            raise PdfError('container is outside the source page')
        last=entry['extent']['last_baseline']
        lines=b['physical_layout']['lines'];observed=p['layout_suggestion']['base_baselines']
        if (abs(last-(lines[-1]['baseline'] if lines else layout['baseline']))>.002
                or (observed and (abs(observed[0]-layout['baseline'])>.002 or observed[-1]>last+.002))):
            raise PdfError('logical extent differs from the bound paragraph baselines')
        if (not math.isfinite(last) or last<layout['baseline']-.002 or last>=layout['max_bottom']
                or (not p['text'] and abs(last-layout['baseline'])>.002)
                or not region.contains(Rect(layout['x'],layout['baseline'],layout['x']+layout['width'],layout['max_bottom']))):
            raise PdfError('logical extent or text region exceeds the confirmed container')
        physical=b['element']['text_element']['observed_bounds']
        if physical is not None and not region.contains(Rect(**physical),.002):
            raise PdfError('bound source text is outside its confirmed container')
        ids=set(p['selection']['glyph_ids'])
        if used & ids:raise PdfError('logical elements cannot share source glyph occurrences')
        used.update(ids)
        if not ids:
            slot=p['insertion_binding']['event']['byte_range'][0]
            if slot in slots:raise PdfError('logical elements cannot share an insertion slot')
            slots.add(slot)
        paths,decisions=_confirmed(b['element'],_element_relations(b['element'],_fixed_relations(b),b['anchors']))
        if any(r['behavior']!='fixed-to-page' for r in decisions.values() if r['relation']!='decorates'):
            raise PdfError('document ownership is separate from text-edit paint behavior')
        for path_id in entry['owned_paints']:
            if (path_id in owned or path_id not in decisions or decisions[path_id]['relation']=='unrelated'
                    or paths[path_id]['proof']['status']!='proven'):
                raise PdfError('owned paint needs unique explicit ownership and proven source correspondence')
            owned.add(path_id)
            if any(not region.contains(Rect(*paint['bounds']),.002) for paint in paths[path_id]['proof']['paints']):
                raise PdfError('owned paint exceeds the fixed container')
    _order(state)
    for r in state['follows']:
        expected=elements[r['before']]['extent']['last_baseline']+r['gap']
        if abs(expected-elements[r['after']]['binding']['layout']['baseline'])>.002:
            raise PdfError('confirmed follows relation does not match bound element positions')
    return state


@proof_session
def open_document(source, model):
    try:
        value=model if isinstance(model,dict) else json.loads(Path(model).read_text(encoding='utf-8'))
        return dict(status='restored',state=_validate(source,value))
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError,PdfError) as exc:
        return dict(status='needs_confirmation',reason=str(exc),semantics='unknown')


def _rebind_clip_locations(expected, before, after, byte_edits):
    """Map page-program clip witnesses, retaining geometry and scope checks.

    Clip ``at`` records a virtual page xref and an operator ordinal, neither
    of which is stable after a save or earlier text replacement. Map through
    byte provenance to the same surviving path terminator; never discard the
    locator or equate unrelated operators merely because their paint matches.
    The caller still compares the complete ordered clip paths, CTMs and rules.
    """
    if not expected['state']['clip']:return
    old_key,new_key=-before.page.xref,-after.page.xref
    old_data,new_data=before.streams[old_key],after.streams[new_key]
    old_ops,new_ops=list(operators(old_data)),list(operators(new_data))
    new_starts={op.start:(i,op) for i,op in enumerate(new_ops)}
    for clip in expected['state']['clip']:
        at=clip.get('at')
        if (clip.get('rule') not in ('W','W*') or not isinstance(at,list) or len(at)!=2
                or at[0]!=old_key or type(at[1]) is not int or not 0<=at[1]<len(old_ops)):
            raise PdfError('empty insertion clip source location cannot be rebound')
        old_op=old_ops[at[1]]
        if old_op.name not in ('n','S','s','f','F','f*','B','B*','b','b*'):
            raise PdfError('empty insertion clip witness is not a path terminator')
        # Even a replacement that happens to reproduce this operator is not
        # continuity: the source clip terminator must survive byte-for-byte.
        if any(e['start']<old_op.end and e['end']>old_op.start for e in byte_edits):
            raise PdfError('another mutation consumed the empty insertion clip witness')
        try:mapped=map_offset(old_op.start,byte_edits)
        except PdfError:mapped=None
        match=new_starts.get(mapped) if mapped is not None else None
        if match is None or old_data[old_op.start:old_op.end]!=new_data[match[1].start:match[1].end]:
            raise PdfError('empty insertion clip operator continuity differs')
        clip['at']=[new_key,match[0]]


def rebind_entry(identity, entry, *, dy=0):
    """Carry one element's meaning across a revision through the identity map.

    Text glyphs and paths are mapped by source provenance and re-verified by
    their witnesses; the observed geometry must then equal the planned
    translation. Nothing is re-associated by proximity or list position.
    """
    result=deepcopy(entry);state=result['binding'];old=entry['binding'];p=old['paragraph']
    output=identity.output
    checksum=source_sha(output)
    a,b=identity.before,identity.after
    if p['selection']['glyph_ids']:
        mapping=identity.map_glyphs(p['selection']['glyph_ids'])
        for index,new in mapping.items():
            before,after=a.actual[index],b.actual[new]
            if any(abs(x+(dy if k else 0)-y)>.002 for k,(x,y) in enumerate(zip(before['origin'],after['origin']))):
                raise PdfError('cross-element glyph did not move by the planned translation')
        selection=make_selection(output,p['selection']['page'],glyph_ids=list(mapping.values()),explicit_width=state['layout']['width'])
        logical=deepcopy(p.get('logical'))
        if logical is not None:
            logical['pdf_sha256']=checksum
            for unit in logical['units']:
                for key in ('glyph_id','style_glyph_id'):
                    if unit[key] is not None:unit[key]=mapping[unit[key]]
        snapshot=inspect_paragraph(output,selection,line_joiner=p['line_joiner'],logical=logical)
        # Object numbers may change on a full save. The lower writer
        # verifies font dictionaries/program fingerprints; resource names,
        # codes, widths and style values still have to remain identical.
        def styles(values):
            return [{k:v for k,v in s.items() if k!='font_xref'} for s in values]
        if styles(snapshot['styles'])!=styles(p['styles']) or snapshot['text']!=p['text']:
            raise PdfError('cross-element logical text or style changed')
    else:
        snapshot=deepcopy(p);snapshot.pop('snapshot_sha256')
        offset=identity.map_offset(p['insertion_binding']['event']['byte_range'][0])
        binding,_=slot_binding(b,offset)
        expected=deepcopy(p['insertion_binding']['event']);actual=json.loads(json.dumps(binding['event']))
        _rebind_clip_locations(expected,a,b,identity.program.edits())
        for event in (expected,actual):
            for key in ('id','stream_xref','byte_range'):event.pop(key)
            event['state'].pop('font_xref')
        delta=_local_translation(list(pymupdf.Matrix(*expected['state']['ctm'])*a.page.transformation_matrix),0,dy)
        expected['state']['ctm']=list(multiply((1,0,0,1,*delta),expected['state']['ctm']))
        if not _close(expected,actual):
            raise PdfError('empty insertion graphics state changed beyond the planned translation')
        snapshot['insertion_binding']=json.loads(json.dumps(binding))
        snapshot['selection']['source_sha256']=checksum;snapshot['logical']['pdf_sha256']=checksum
        snapshot['layout_suggestion']['baseline']+=dy
        snapshot['snapshot_sha256']=digest(snapshot)
    element=inspect_element(output,snapshot['selection'],paragraph_snapshot=snapshot)
    current={path['source_id']:path for path in element['paths']}
    anchors=deepcopy(old['anchors'])
    decorated=[i for u in (anchors or {}).get('underlines',[]) for i in u['source_ids']] if anchors else []
    referenced={r['source_id'] for r in _fixed_relations(old)}|set(entry['owned_paints'])|set(decorated)
    path_map={}
    for previous in old['element']['paths']:
        ident=previous['source_id']
        if ident in identity.consumed_paths:
            if ident in referenced:
                raise PdfError('a paint this element relies on was replaced by generated paint')
            continue
        successor=identity.map_path(ident)
        after=current.get(successor)
        if after is None:
            raise PdfError('mapped path is missing from the saved element observation')
        planned=identity.planned_paints.get(ident)
        wanted=planned if planned is not None else [_paint_value(v) for v in previous['proof'].get('paints',[])]
        if (previous['source']['operator']!=after['source']['operator']
                or previous['proof']['status']!=after['proof']['status']
                or not _close(wanted,[_paint_value(v) for v in after['proof'].get('paints',[])])
                or (planned is None and previous['source']['operation_sha256']!=after['source']['operation_sha256'])):
            raise PdfError('path continuity differs from the planned paint transformation')
        path_map[ident]=successor
    state.update(pdf_sha256=checksum,paragraph=snapshot,element=element)
    if anchors is not None:
        anchors['fixed_relations']=[dict(r,source_id=path_map[r['source_id']]) for r in anchors.get('fixed_relations',[])]
        for underline in anchors['underlines']:
            underline['source_ids']=[path_map[i] for i in underline['source_ids']]
        anchors.update(paragraph_sha256=snapshot['snapshot_sha256'],element_sha256=element['snapshot_sha256'])
        state['anchors']=anchors;state['relations']=None
    else:
        state['relations']=[dict(r,source_id=path_map[r['source_id']]) for r in old['relations']]
    result['owned_paints']=[path_map[i] for i in entry['owned_paints']]
    if dy:
        state['layout']['baseline']+=dy
        state['layout_provenance']['baseline']='generated_from_explicit_follows'
        state['container']['region']['first_baseline']+=dy
        for line in state['physical_layout']['lines']:line['baseline']+=dy
        state['physical_layout']['provenance']='generated_from_explicit_follows'
        result['extent']['last_baseline']+=dy;result['extent']['provenance']='generated_from_explicit_follows'
    state['previous_model_sha256']=old['model_sha256'];result['binding']=_reseal(state)
    return result


@proof_session
def edit_flow(source, model, output, model_output, element_id, edits, *, empty_style_id=None):
    """Edit one identity and move only its explicit descendants, in one transaction."""
    from .flow_transaction import edit_flow_batch
    restored=open_document(source,model)
    if restored['status']!='restored':raise PdfError('document edit requires confirmation: '+restored['reason'])
    state=restored['state']
    if element_id not in state['elements']:raise PdfError('unknown logical element identity')
    change=dict(edits=edits)
    if empty_style_id is not None:change['empty_style_id']=empty_style_id
    report=edit_flow_batch(source,state,output,model_output,{element_id:change})
    plan=report['plan'];order,edges=_order(state)
    descendants=set();queue=list(edges[element_id])
    while queue:
        i=queue.pop();descendants.add(i);queue.extend(edges[i])
    dy=plan['extent_deltas'][element_id]
    moving=[i for i in order if i in descendants] if abs(dy)>.002 else []
    steps={s['element_id']:s for s in report['steps']}
    ordered=([steps[i] for i in reversed(moving)]+[steps[element_id]] if dy>0 else
             [steps[element_id]]+[steps[i] for i in moving])
    return dict(backend='explicit-document-follows',element_id=element_id,baseline_delta=dy,
        moved_elements=moving,steps=ordered,model_sha256=report['model_sha256'],pdf_sha256=report['pdf_sha256'],
        relation_plan_verified=True,publication=report['publication'])
