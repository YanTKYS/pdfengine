"""Explicit paragraph identities and follows relations above physical PDF bindings.

One fixed, confirmed page region; one paragraph edit per transaction. Edges
relate the follower's first baseline to its predecessor's last baseline.
An empty paragraph reserves its confirmed first baseline. No edge or paint
ownership is ever inferred from a coordinate, bounding box, or paint order.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import tempfile

import pymupdf

from .attributed import digest, inspect_paragraph
from .backend import PdfError
from .composition import _observations
from .content_stream import ContentPage, multiply
from .editable import _seal, edit_document, open_editable
from .elements import _close, _confirmed, _local_translation, _paint_value, inspect_element, move_element
from .logical_element import paragraph_from_snapshot, slot_binding
from .model import Rect
from .paragraph import _layout_parameters, plan_paragraph
from .replay import compare_glyphs, ensure_destination
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
    relations=deepcopy(spec.get('paint_relations',[]))
    _confirmed(paint,relations)
    if any(r['behavior']!='fixed-to-page' for r in relations):
        raise PdfError('declare ownership separately; text-edit paint relations stay fixed-to-page')
    observed=snapshot['layout_suggestion']['base_baselines']
    last=observed[-1] if observed else layout['baseline']
    state=_seal(dict(schema='pdfengine-editable-2',pdf_sha256=source_sha(source),paragraph=snapshot,
        logical_element=dict(id=ident,kind='paragraph',contained_by=region,
            provenance='caller_confirmed_selection',alignment=dict(value='left',provenance='generated_layout_policy')),
        element=paint,anchors=None,relations=relations,fonts=fonts,layout=layout,
        layout_provenance={k:'explicitly_confirmed' if spec['layout'].get(k) is not None else
                           'generated-by-pdfengine' if k=='min_line_height' else 'observed_source' for k in layout},
        boundaries=[dict(offset=m.start(),end=m.end(),kind='hard_break',provenance='explicitly_confirmed')
                    for m in re.finditer(r'\r\n|\r|\n',snapshot['text'])],
        physical_layout=dict(provenance='observed_source',lines=[dict(baseline=b) for b in observed]),
        container=dict(kind='text_region',sizing='fixed',overflow='reject',padding=None,padding_provenance='unknown',
            region=dict(x=layout['x'],width=layout['width'],first_baseline=layout['baseline'],bottom=layout['max_bottom']),
            follows=[],follows_provenance='not_declared',ownership='only explicitly confirmed paint relations'),
        previous_model_sha256=None))
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
                or p['selection']['page']!=c['page'] or b['anchors'] is not None):
            raise PdfError('element identity, page or decoration policy differs from the document')
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
        paths,decisions=_confirmed(b['element'],b['relations'])
        if any(r['behavior']!='fixed-to-page' for r in decisions.values()):
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


def _map_offset(offset, edits):
    delta=0
    for edit in sorted(edits,key=lambda e:e['start']):
        if edit['start']<=offset<edit['end']:
            if offset==edit['start'] and 'preserved_event_offset' in edit:
                return offset+delta+edit['preserved_event_offset']
            raise PdfError('another element mutation consumed a bound insertion slot')
        if edit['end']<=offset:delta+=edit['length']-(edit['end']-edit['start'])
    return offset+delta


def _shift_paint(paint, dy):
    value=_paint_value(paint)
    if dy:
        value['bounds'][1]+=dy;value['bounds'][3]+=dy
        for command in value['geometry']:
            for j in range(2,len(command),2):command[j]+=dy
    return value


def _rebind(source, output, entry, byte_edits, *, dy=0, moving_paths=(), paint_dy=0):
    """Carry meaning using exact glyph witnesses and known byte/paint mutations."""
    result=deepcopy(entry);state=result['binding'];old=entry['binding'];p=old['paragraph']
    checksum=source_sha(output)
    a=ContentPage(source,p['selection']['page']);b=ContentPage(output,p['selection']['page'])
    try:
        if p['selection']['glyph_ids']:
            before,after=_observations(a.page),_observations(b.page);mapping={}
            for index in p['selection']['glyph_ids']:
                wanted=deepcopy(before[index]);wanted['origin'][1]+=dy
                wanted['bbox'][1]+=dy;wanted['bbox'][3]+=dy
                matches=[j for j,g in enumerate(after) if compare_glyphs([wanted],[g],.002)['passed']]
                if len(matches)!=1:raise PdfError('cross-element glyph continuity is ambiguous')
                mapping[index]=matches[0]
            if len(set(mapping.values()))!=len(mapping):raise PdfError('cross-element glyph binding is not one-to-one')
            selection=make_selection(output,p['selection']['page'],glyph_ids=list(mapping.values()),explicit_width=state['layout']['width'])
            logical=deepcopy(p.get('logical'))
            if logical is not None:
                logical['pdf_sha256']=checksum
                for unit in logical['units']:
                    for key in ('glyph_id','style_glyph_id'):
                        if unit[key] is not None:unit[key]=mapping[unit[key]]
            snapshot=inspect_paragraph(output,selection,line_joiner=p['line_joiner'],logical=logical)
            # Matching paint alone cannot authorize a different encoded glyph.
            def codes(content, ids):
                return {i:(e.state.font.name,ch.code.hex(),ch.pdf_width)
                        for e in content.selected_events(set(ids)) for ch in e.chars for i in ch.source_orders if i in ids}
            old_codes,new_codes=codes(a,mapping),codes(b,mapping.values())
            if any(old_codes[i]!=new_codes[j] for i,j in mapping.items()):
                raise PdfError('cross-element source font codes changed')
            # Object numbers may change on a full save. The lower writer
            # verifies font dictionaries/program fingerprints; resource names,
            # codes, widths and style values still have to remain identical.
            def styles(values):
                return [{k:v for k,v in s.items() if k!='font_xref'} for s in values]
            if styles(snapshot['styles'])!=styles(p['styles']) or snapshot['text']!=p['text']:
                raise PdfError('cross-element logical text or style changed')
        else:
            snapshot=deepcopy(p);snapshot.pop('snapshot_sha256')
            offset=_map_offset(p['insertion_binding']['event']['byte_range'][0],byte_edits)
            binding,_=slot_binding(b,offset)
            expected=deepcopy(p['insertion_binding']['event']);actual=json.loads(json.dumps(binding['event']))
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
    finally:a.close();b.close()
    element=inspect_element(output,snapshot['selection'],paragraph_snapshot=snapshot)
    old_paths,new_paths=old['element']['paths'],element['paths']
    if len(old_paths)!=len(new_paths):raise PdfError('path source order changed across the transaction')
    path_map={}
    for previous,current in zip(old_paths,new_paths):
        moved=previous['source_id'] in moving_paths
        if (previous['source']['operator']!=current['source']['operator']
                or previous['proof']['status']!=current['proof']['status']
                or not _close([_shift_paint(v,paint_dy if moved else 0) for v in previous['proof'].get('paints',[])],
                              [_paint_value(v) for v in current['proof'].get('paints',[])])
                or (not moved and previous['source']['operation_sha256']!=current['source']['operation_sha256'])):
            raise PdfError('path continuity differs from the explicitly translated paint plan')
        path_map[previous['source_id']]=current['source_id']
    state.update(pdf_sha256=checksum,paragraph=snapshot,element=element)
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
    """Edit one identity and move only its explicit descendants, or publish nothing."""
    restored=open_document(source,model)
    if restored['status']!='restored':raise PdfError('document edit requires confirmation: '+restored['reason'])
    initial=restored['state'];state=deepcopy(initial)
    if element_id not in state['elements']:raise PdfError('unknown logical element identity')
    output=ensure_destination(output,source);model_output=ensure_destination(model_output,source)
    if output==model_output:raise PdfError('PDF and document model need distinct destinations')
    order,edges=_order(state);descendants=set();queue=list(edges[element_id])
    while queue:
        i=queue.pop();descendants.add(i);queue.extend(edges[i])
    entry=state['elements'][element_id];binding=entry['binding']
    plan=plan_paragraph(source,binding['paragraph'],edits,fonts=_fonts(binding),**binding['layout'])
    dy=plan['last_baseline']-entry['extent']['last_baseline']
    moving=[i for i in order if i in descendants] if abs(dy)>.002 else []
    output.parent.mkdir(parents=True,exist_ok=True)
    reports=[]
    with tempfile.TemporaryDirectory(prefix='.flow-',dir=output.parent) as directory:
        root=Path(directory);current=Path(source)
        def move(ident):
            nonlocal current,state
            entry=state['elements'][ident];b=entry['binding'];owned=entry['owned_paints']
            target=root/f'{len(reports)}-move.pdf'
            relations=[dict(r,behavior='fixed-to-element' if r['source_id'] in owned else 'fixed-to-page') for r in b['relations']]
            report=move_element(current,target,b['element'],relations,dx=0,dy=dy,paragraph_snapshot=b['paragraph'])
            state['elements']={i:_rebind(current,target,e,report['byte_edits'],dy=dy if i==ident else 0,
                                      moving_paths=owned,paint_dy=dy) for i,e in state['elements'].items()}
            reports.append(dict(kind='move',element_id=ident,report=report));current=target
        # Expanding predecessors need the space vacated first. Contracting
        # predecessors vacate it before followers move upward. Intermediate
        # graphs can be unsatisfied; all PDF collision/paint guards still run.
        if dy>0:
            for ident in reversed(moving):move(ident)
        target=root/f'{len(reports)}-edit.pdf';sidecar=root/'paragraph.json'
        previous=state['elements'][element_id];b=previous['binding']
        report=edit_document(current,b,target,sidecar,edits,empty_style_id=empty_style_id)
        if not _close(plan['lines'],[{k:line[k] for k in ('start','end','baseline','width','ascent','descent')} for line in report['lines']]):
            raise PdfError('writer layout differs from the follows planning measurement')
        edited=json.loads(sidecar.read_text(encoding='utf-8'))
        paths={a['source_id']:b['source_id'] for a,b in zip(b['element']['paths'],edited['element']['paths'])}
        state['elements']={i:(dict(binding=edited,owned_paints=[paths[p] for p in previous['owned_paints']],
            extent=dict(last_baseline=plan['last_baseline'],provenance='generated-by-pdfengine')) if i==element_id else
            _rebind(current,target,e,report['byte_edits'])) for i,e in state['elements'].items()}
        reports.append(dict(kind='edit',element_id=element_id,report=report));current=target
        if dy<0:
            for ident in moving:move(ident)
        state['pdf_sha256']=source_sha(current);state['previous_model_sha256']=initial['model_sha256']
        state=_validate(current,_reseal(state))
        state_file=root/'document.json';state_file.write_bytes((json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
        published=[]
        try:
            for temporary,destination in ((current,output),(state_file,model_output)):
                destination.parent.mkdir(parents=True,exist_ok=True)
                os.link(temporary,destination);published.append(destination)
        except Exception:
            for destination in published:destination.unlink()
            raise
    return dict(backend='explicit-document-follows',element_id=element_id,baseline_delta=dy,
        moved_elements=moving,steps=reports,model_sha256=state['model_sha256'],pdf_sha256=state['pdf_sha256'],
        relation_plan_verified=True,publication='verified PDF plus revision-bound sidecar; rollback on exception')
