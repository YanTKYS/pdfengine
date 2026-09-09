"""Source-bound text/paint relationships and explicit rigid element movement.

Observation, ownership hypotheses, and caller-confirmed editing behavior are
separate. A rigid move preserves internal relations; it does not infer flow or
resize decorations when paragraph text changes.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import math

import pymupdf

from .attributed import digest
from .backend import PdfError
from .composition import _empty_space_indices, _observations, _pixels_equal, _require_removal
from .content_stream import ContentPage, number, operators
from .marked_content import observe_marked_content
from .model import Rect
from .paint_geometry import contains_fill, intersects_fill, rectangle_subpaths, shifted
from .paint_provenance import (_load_catalog, _physical, interpreted_paints,
                               prove_path_paint)
from .pdf_save import font_fingerprints, program_pdf_bytes, publish_program
from .replay import compare_glyphs, ensure_destination
from .selection import resolve_selection


def inspect_element(source, selection):
    resolved = resolve_selection(source,selection)
    content = ContentPage(source,resolved.page)
    try:
        # Device observations and glyph coordinates are unrotated. Page.rect
        # and rendered masks rotate with /Rotate; mixing the two could admit
        # a destination outside the real crop or mask the wrong pixels.
        if content.page.rotation:
            raise PdfError('element editing requires an unrotated page; rotated bounds and masks are not normalized')
        events = content.selected_events(set(selection['glyph_ids']))
        catalog,_ = _load_catalog(source,resolved.page)
        marks = observe_marked_content(source,resolved.page)
        text_ops = []
        for event in events:
            text_ops.append(dict(id=event.id,operator=event.operator.name,
                byte_range=[event.operator.start,event.operator.end],invocation=event.invocation,
                source_ranges=_physical(catalog['contents'],event.operator.start,event.operator.end),
                glyph_ids=[i for c in event.chars for i in c.source_orders],
                fully_selected=all(i in selection['glyph_ids'] for c in event.chars for i in c.source_orders)))
        first = min(content.actual[i]['span']['seqno'] for i in selection['glyph_ids'])
        paths = []
        for path in catalog['paths']:
            proof = prove_path_paint(source,resolved.page,path['id'],catalog=catalog)
            # Encrypted save probes may use random IVs; those PDF byte hashes
            # are evidence of an individual probe, not snapshot identity.
            for key in ('control_pdf_sha256','counterfactual_pdf_sha256'):
                proof.get('evidence',{}).pop(key,None)
            item = dict(source_id=path['id'],source=path,proof=proof,
                        relationship=dict(status='unresolved',owner=None,behavior=None))
            if proof['status']=='proven':
                paints = proof['paints']
                near = any(Rect(*p['bounds']).intersects(resolved.bbox) and
                           (p['kind']!='fill-path' or intersects_fill(p,resolved.bbox) is not False) for p in paints)
                hint = 'unrelated_or_fixed'
                if near:
                    if all(p['kind']=='fill-path' and contains_fill(p,resolved.bbox) and p['seqno']<first for p in paints):
                        hint = 'background_candidate'
                    elif any(p['bounds'][3]-p['bounds'][1]<2 and p['seqno']>=first for p in paints):
                        hint = 'decoration_candidate'
                    else:
                        hint = 'overlap_or_border_candidate'
                item['relationship'].update(status='inferred',hypothesis=hint,
                    requires_confirmation=near,evidence=dict(paint_order=[p['seqno'] for p in paints],
                    intersects_text_bounds=near,source_provenance=proof['proof_type']),
                    policy='Geometry and paint order suggest a relation; they do not establish semantic ownership.')
            paths.append(item)
        observation=interpreted_paints(source,resolved.page)
        linked={i for p in paths if p['proof']['status']=='proven' for i in p['proof']['paint_indices']}
        unlinked=[]
        for i,paint in enumerate(observation['events']):
            if i in linked or paint['kind'] not in ('fill-path','stroke-path'):
                continue
            near=(Rect(*paint['bounds']).intersects(resolved.bbox) and
                  (paint['kind']!='fill-path' or intersects_fill(paint,resolved.bbox) is not False))
            unlinked.append(dict(paint_index=i,seqno=paint['seqno'],bounds=paint['bounds'],
                fingerprint=paint['fingerprint'],near_selected_text=near,source_provenance='unknown'))
        snapshot = dict(schema_version=1,source_sha256=selection['source_sha256'],selection=selection,
            text_element=dict(glyph_ids=selection['glyph_ids'],observed_bounds=asdict(resolved.bbox),
                              source_operators=text_ops),
            page_bounds=list(content.page.rect),paths=paths,marked_content=marks,
            unlinked_path_paints=unlinked,renderer_observation_errors=observation['errors'],
            flow_contract=dict(order='unknown',anchor='unspecified',available_region='unknown',
                               page_break='unsupported',movable='requires explicit behavior',
                               follows='not inferred from vertical proximity'))
        # Normalize tuple representations before writing a reviewable artifact.
        import json
        snapshot = json.loads(json.dumps(snapshot))
        snapshot['snapshot_sha256'] = digest(snapshot)
        return snapshot
    finally:
        content.close()


def _confirmed(snapshot, relations):
    if not isinstance(relations,list):
        raise PdfError('element relations must be an explicit list')
    if any(p['near_selected_text'] for p in snapshot['unlinked_path_paints']):
        raise PdfError('overlapping vector paint lacks unique source provenance; ownership cannot be confirmed')
    paths = {p['source_id']:p for p in snapshot['paths']}
    decisions = {}
    for relation in relations:
        if not isinstance(relation,dict):
            raise PdfError('each relation must identify source_id, relation and behavior')
        ident = relation.get('source_id')
        if ident not in paths or ident in decisions:
            raise PdfError('unknown or duplicate source path relation')
        if relation.get('relation') not in ('backgrounds','borders','decorates','unrelated'):
            raise PdfError('unknown element relation')
        if relation.get('behavior') not in ('fixed-to-element','fixed-to-page'):
            raise PdfError('element movement needs fixed-to-element or fixed-to-page behavior')
        if relation['relation']=='unrelated' and relation['behavior']!='fixed-to-page':
            raise PdfError('an unrelated paint cannot follow this element')
        decisions[ident] = dict(relation,evidence='explicitly_supplied')
    for ident,path in paths.items():
        if path['relationship'].get('requires_confirmation') and ident not in decisions:
            raise PdfError('unresolved background/decoration/overlap relation; review the element snapshot')
    return paths,decisions


def _close(a,b,tolerance=.002):
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return math.isfinite(a) and math.isfinite(b) and abs(a-b)<=tolerance
    if isinstance(a,dict) and isinstance(b,dict):
        return a.keys()==b.keys() and all(_close(a[k],b[k],tolerance) for k in a)
    if isinstance(a,list) and isinstance(b,list):
        return len(a)==len(b) and all(_close(x,y,tolerance) for x,y in zip(a,b))
    return a==b


def _paint_value(event):
    value = {k:deepcopy(v) for k,v in event.items() if k not in ('seqno','index','fingerprint')}
    if value['kind'] in ('fill-text','stroke-text','ignore-text'):
        ctm = pymupdf.Matrix(*value.pop('matrix'))
        value['origin'] = list(pymupdf.Point(*value.pop('glyph_position'))*ctm)
        value['text_matrix'] = list(pymupdf.Matrix(*value['text_matrix'])*ctm)[:4]
    elif value['kind'] in ('fill-path','stroke-path'):
        # Geometry is already in page space; linear matrix still determines
        # stroke width/dashes. Its translation is not applied a second time.
        value['matrix'] = value['matrix'][:4]
    return value


def _check_clip(paint, bounds):
    if paint.get('layers') or paint.get('groups'):
        raise PdfError('element movement does not relocate layers or transparency groups')
    for clip in paint.get('clips',[]):
        if clip['kind']!='clip-path' or not contains_fill(clip,bounds):
            raise PdfError('moved element crosses an active clip or its shape is unknown')


def check_paragraph_obstacles(source, content, selected, resolved, inks, layout_bounds,
                              snapshot, relations):
    """Keep declared backgrounds fixed while editing their contained text.

    Following/resizing decorations are deliberately not permitted here. Their
    glyph-range anchors would need updating when a paragraph is reflowed.
    """
    if set(snapshot['selection']['glyph_ids'])!=selected or snapshot['selection']['page']!=resolved.page:
        raise PdfError('paragraph and element select different source text')
    if inspect_element(source,snapshot['selection'])!=snapshot:
        raise PdfError('element snapshot changed or no longer matches its source')
    paths,decisions=_confirmed(snapshot,relations)
    if any(r['behavior']!='fixed-to-page' for r in decisions.values()):
        raise PdfError('paragraph reflow needs decoration anchors; fixed-to-element paint only supports rigid movement')
    observation=interpreted_paints(source,resolved.page)
    if observation['errors']:
        raise PdfError(observation['errors'][0])
    path_by_index={i:p for p in paths.values() if p['proof']['status']=='proven' for i in p['proof']['paint_indices']}
    first=min(content.actual[i]['span']['seqno'] for i in selected)
    original=_observations(content.page)
    empty=_empty_space_indices(content,original)
    for ink in inks:
        for i,g in enumerate(original):
            if i not in selected and i not in empty and ink.intersects(Rect(*g['bbox']),.1):
                raise PdfError('composed text collides with an unselected glyph')
        for image in content.page.get_image_info():
            if ink.intersects(Rect(*image['bbox']),.01):
                raise PdfError('composed text intersects an image')
        for i,event in enumerate(observation['events']):
            if event['kind'] not in ('fill-path','stroke-path') or not ink.intersects(Rect(*event['bounds']),.001):
                continue
            if event['kind']=='fill-path' and intersects_fill(event,ink) is False:
                continue
            if event['kind']=='stroke-path':
                rects=rectangle_subpaths(event['geometry'])
                if rects is not None:
                    a,b,c,d=event['matrix'][:4]
                    scale=math.sqrt(a*a+b*b+c*c+d*d)
                    margin=max(.2,event['stroke']['linewidth']*scale*max(1,event['stroke']['miterlimit'])/2)
                    edges=[]
                    for rect,_ in rects:
                        edges.extend((Rect(rect.x0-margin,rect.y0-margin,rect.x1+margin,rect.y0+margin),
                            Rect(rect.x0-margin,rect.y1-margin,rect.x1+margin,rect.y1+margin),
                            Rect(rect.x0-margin,rect.y0-margin,rect.x0+margin,rect.y1+margin),
                            Rect(rect.x1-margin,rect.y0-margin,rect.x1+margin,rect.y1+margin)))
                    if not any(ink.intersects(edge) for edge in edges):
                        continue
            path=path_by_index.get(i)
            relation=decisions.get(path['source_id']) if path else None
            if (relation and relation['relation']=='backgrounds' and event['kind']=='fill-path'
                    and event['seqno']<first and contains_fill(event,resolved.bbox) and contains_fill(event,ink)):
                continue
            raise PdfError('composed text intersects a fixed vector without a proven background relation')
    affected=resolved.bbox.union(layout_bounds)
    for link in content.page.get_links():
        if affected.intersects(Rect(*link['from'])):
            raise PdfError('composition intersects a link')
    for item in list(content.page.annots() or [])+list(content.page.widgets() or []):
        if affected.intersects(Rect(*item.rect)):
            raise PdfError('composition intersects an annotation or widget')


def _check_destination(content, original, moving_indices, bounds, snapshot, decisions):
    if not Rect(*content.page.rect).contains(bounds):
        raise PdfError('moved element is outside the page')
    path_by_index = {i:p for p in snapshot['paths'] if p['proof']['status']=='proven'
                     for i in p['proof']['paint_indices']}
    for i,event in enumerate(original):
        if i in moving_indices or event['kind'] in ('push-clip','pop-clip','begin-group','end-group','begin-layer','end-layer'):
            continue
        if event['kind'] in ('fill-path','stroke-path'):
            if event['kind']=='fill-path' and intersects_fill(event,bounds) is False:
                continue
            if not Rect(*event['bounds']).intersects(bounds):
                continue
            path = path_by_index.get(i)
            decision = decisions.get(path['source_id']) if path else None
            if (decision and decision['relation']=='backgrounds' and decision['behavior']=='fixed-to-page'
                    and event['kind']=='fill-path' and contains_fill(event,bounds)
                    and event['seqno']<min(original[j]['seqno'] for j in moving_indices)):
                continue
            raise PdfError('moved element intersects a fixed vector paint')
        if event['kind'] in ('fill-image','fill-image-mask'):
            rect = pymupdf.Rect(0,0,1,1)*pymupdf.Matrix(*event['matrix'])
            if Rect(*rect).intersects(bounds):
                raise PdfError('moved element intersects a fixed image')
    selected = set(snapshot['selection']['glyph_ids'])
    for i,glyph in enumerate(_observations(content.page)):
        if i not in selected and Rect(*glyph['bbox']).intersects(bounds):
            raise PdfError('moved element intersects an unselected glyph')
    for link in content.page.get_links():
        if bounds.intersects(Rect(*link['from'])):
            raise PdfError('element movement intersects a link')
    for item in list(content.page.annots() or [])+list(content.page.widgets() or []):
        if bounds.intersects(Rect(*item.rect)):
            raise PdfError('element movement intersects an annotation or widget')


def _local_translation(matrix, dx, dy):
    # A displacement is a vector, not the difference of transformed points.
    # Point/Matrix operations use float32 internally: cancellation against a
    # large CTM translation changed 240 into 239.999992 in an external PDF.
    # Invert only the linear transform using Python float64 arithmetic.
    a,b,c,d=matrix[:4]
    determinant=a*d-b*c
    if not math.isfinite(determinant) or abs(determinant)<1e-10:
        raise PdfError('element paint has a singular transform')
    return (d*dx-c*dy)/determinant,(-b*dx+a*dy)/determinant


def _move_program(content, events, moving, dx, dy):
    virtual = -content.page.xref
    data = content.streams[virtual]
    mutations = []
    source_operators=list(operators(data))
    for event in events:
        matrix = pymupdf.Matrix(*event.state.ctm)*content.page.transformation_matrix
        delta = _local_translation(list(matrix),dx,dy)
        op = data[event.operator.start:event.operator.end]
        new = b'q 1 0 0 1 '+number(delta[0])+b' '+number(delta[1])+b' cm '+op+b' Q '
        mutations.append((event.operator.start,event.operator.end,new))
    for path in moving:
        a,b = path['source']['merged_range']
        paints = path['proof']['paints']
        if any(p['geometry']!=paints[0]['geometry'] or p['matrix']!=paints[0]['matrix'] for p in paints):
            raise PdfError('a source path paint bundle has inconsistent geometry')
        operator = path['source']['operator']
        first=min(path['source']['path_operator_indices'])
        last=path['source']['operator_index']
        # The renderer proves source correspondence and geometry; it is not
        # the serialization authority for source coordinates. Float path
        # round-trips introduced a 1-pixel edge change in independent tests.
        # Only uninterrupted construction under one CTM can be copied at the
        # paint site. More complex source paths need a processor-backed writer.
        allowed={'m','l','c','v','y','h','re','W','W*'}
        if any(o.name not in allowed for o in source_operators[first:last]):
            raise PdfError('path construction crosses state or paint operations; exact replay needs a richer writer')
        raw=b' '.join(data[source_operators[i].start:source_operators[i].end]
                      for i in path['source']['path_operator_indices'])
        delta=_local_translation(paints[0]['matrix'],dx,dy)
        consume=path['proof']['evidence']['replacement'].encode()
        new=consume+b' q 1 0 0 1 '+number(delta[0])+b' '+number(delta[1])+b' cm '+raw+b' '+operator.encode()+b' Q '
        mutations.append((a,b,new))
    for a,b,new in sorted(mutations,reverse=True):
        data=data[:a]+new+data[b:]
    return data


def move_element(source, output, snapshot, relations, *, dx, dy, removal_output=None):
    output = ensure_destination(output,source)
    if removal_output:
        removal_output = ensure_destination(removal_output,source)
        if removal_output==output:
            raise PdfError('movement output and removal checkpoint must differ')
    if not all(math.isfinite(v) for v in (dx,dy)):
        raise PdfError('element displacement must be finite')
    fresh = inspect_element(source,snapshot['selection'])
    if fresh != snapshot:
        raise PdfError('element snapshot changed or no longer matches its source')
    paths,decisions = _confirmed(snapshot,relations)
    moving = [paths[i] for i,r in decisions.items() if r['behavior']=='fixed-to-element']
    if any(p['proof']['status']!='proven' for p in moving):
        raise PdfError('every moving path needs proven source provenance')
    marked = snapshot['marked_content']
    if not marked['complete']:
        raise PdfError('marked-content observation is incomplete')
    content = ContentPage(source,snapshot['selection']['page'])
    try:
        selected = set(snapshot['selection']['glyph_ids'])
        events = content.selected_events(selected)
        for event in events:
            if event.operator.name not in ('Tj','TJ') or any(i not in selected for c in event.chars for i in c.source_orders):
                raise PdfError('rigid movement requires complete Tj/TJ source operators')
            if event.state.tr!=0:
                raise PdfError('rigid text movement currently supports fill text')
        # Keeping source operators in place preserves their MCID membership;
        # layout attributes in a real StructureTree would still need updating.
        ranges = {(e.operator.start,e.operator.end) for e in events}
        ranges.update(tuple(p['source']['merged_range']) for p in moving)
        scopes = {s['id']:s for s in marked['scopes']}
        for op in marked['operators']:
            if op['invocation'] or tuple(op['byte_range']) not in ranges:
                continue
            for ident in op['active_scope_ids']:
                scope = scopes[ident]
                if scope['association']['status'] not in ('orphan_mcid','unmarked') or any(
                        k in scope['properties'] for k in ('/ActualText','/OC','/BBox')):
                    raise PdfError('moving tagged or special marked content needs structure-layout updates')
        observation = interpreted_paints(source,snapshot['selection']['page'])
        if observation['errors']:
            raise PdfError(observation['errors'][0])
        original = observation['events']
        expected = [_paint_value(e) for e in original]
        moved_indices = {i for p in moving for i in p['proof']['paint_indices']}
        glyphs = _observations(content.page)
        for index in selected:
            glyph = glyphs[index]
            matches = [i for i,e in enumerate(expected) if e['kind']=='fill-text' and e['gid']==glyph['glyph_id']
                       and e['unicode']==ord(glyph['unicode']) and _close(e['origin'],glyph['origin'],.015)]
            if len(matches)!=1:
                raise PdfError('text-to-renderer paint correspondence is ambiguous')
            moved_indices.add(matches[0])
        before_bounds = Rect(**snapshot['text_element']['observed_bounds'])
        for p in moving:
            for paint in p['proof']['paints']:
                before_bounds = before_bounds.union(Rect(*paint['bounds']))
        after_bounds = shifted(before_bounds,dx,dy)
        _check_destination(content,original,moved_indices,after_bounds,snapshot,decisions)
        for i,g in enumerate(glyphs):
            if i not in selected and Rect(*g['bbox']).intersects(before_bounds):
                raise PdfError('element bounds contain unselected text; confirm the complete group')
        annotation_bounds = before_bounds.union(after_bounds)
        for link in content.page.get_links():
            if annotation_bounds.intersects(Rect(*link['from'])):
                raise PdfError('a linked element requires annotation movement support')
        for item in list(content.page.annots() or [])+list(content.page.widgets() or []):
            if annotation_bounds.intersects(Rect(*item.rect)):
                raise PdfError('an annotated element requires annotation movement support')
        for i in moved_indices:
            e = expected[i]
            _check_clip(original[i],after_bounds)
            if e['kind']=='fill-text':
                e['origin'][0]+=dx;e['origin'][1]+=dy
            else:
                e['bounds']=list(shifted(Rect(*e['bounds']),dx,dy).tuple())
                for command in e['geometry']:
                    for j in range(1,len(command),2):
                        command[j]+=dx;command[j+1]+=dy
        virtual = -content.page.xref
        control=program_pdf_bytes(source,content.page.number,_move_program(content,events,moving,0,0))
        with pymupdf.open(stream=control,filetype='pdf') as document:
            if not all(_pixels_equal(content.document[i],document[i]) for i in range(len(document))):
                raise PdfError('element no-op reconstruction changed pixels; movement is prohibited')
        data=_move_program(content,events,moving,dx,dy)
        area=before_bounds.union(after_bounds)
        wanted_glyphs=deepcopy(glyphs)
        for i in selected:
            g=wanted_glyphs[i]
            g['origin']=[g['origin'][0]+dx,g['origin'][1]+dy]
            g['bbox']=list(shifted(Rect(*g['bbox']),dx,dy).tuple())
        old_fonts=font_fingerprints(content.document,content.page.number)
        def verify(document):
            if (len(document)!=len(content.document) or document.permissions!=content.document.permissions
                    or document.metadata.get('encryption')!=content.document.metadata.get('encryption')):
                raise PdfError('element movement changed page count or security')
            observed=interpreted_paints(document.tobytes(),snapshot['selection']['page'])
            if observed['errors']:
                raise PdfError(observed['errors'][0])
            actual = [_paint_value(e) for e in observed['events']]
            if not _close(expected,actual):
                raise PdfError('saved element paint differs from the translated plan')
            if not compare_glyphs(wanted_glyphs,_observations(document[content.page.number]))['passed']:
                raise PdfError('saved element glyphs differ from the translated plan')
            if font_fingerprints(document,content.page.number)!=old_fonts:
                raise PdfError('element movement changed existing font resources')
            mask=area if dx or dy else None
            if not all(_pixels_equal(content.document[i],document[i],mask if i==content.page.number else None) for i in range(len(document))):
                raise PdfError('element movement changed pixels outside its source/destination')
        publish_program(source,content.page.number,data,output,verify)
        if removal_output:
            # Apply path edits to the same original offsets together with text
            # removals, because text serialization changes operator lengths.
            from .content_stream import serialized_event
            removals=[(e.operator.start,e.operator.end,serialized_event(e,selected,remove=True)) for e in events]
            removals.extend((p['source']['merged_range'][0],p['source']['merged_range'][1],
                             p['proof']['evidence']['replacement'].encode()) for p in moving)
            removed=content.streams[virtual]
            for a,b,new in sorted(removals,reverse=True):
                removed=removed[:a]+new+removed[b:]
            untouched=[g for i,g in enumerate(glyphs) if i not in selected]
            remaining=[_paint_value(e) for i,e in enumerate(original) if i not in moved_indices]
            def verify_removed(doc):
                _require_removal(untouched,doc[content.page.number])
                observed=interpreted_paints(doc.tobytes(),snapshot['selection']['page'])
                if observed['errors'] or not _close(remaining,[_paint_value(e) for e in observed['events']]):
                    raise PdfError('removing the element changed other paint or scope')
            publish_program(source,content.page.number,removed,removal_output,verify_removed)
        return dict(schema_version=1,backend='source-linked-rigid-element',source_sha256=snapshot['source_sha256'],
            snapshot_sha256=snapshot['snapshot_sha256'],selection=snapshot['selection'],relations=list(decisions.values()),
            dx=dx,dy=dy,moved_text_operators=len(events),moved_glyphs=len(selected),moved_path_operators=len(moving),
            moved_paint_indices=sorted(moved_indices),before_bounds=asdict(before_bounds),after_bounds=asdict(after_bounds),
            path_writer='original coordinate tokens under translated source CTM',
            audit_bbox=asdict(area),paint_plan_verified=True,mupdf_outside_pixels_equal=True,
            independent_renderer_verified=False,flow='Explicit rigid placement only; no inferred downstream movement.')
    finally:
        content.close()
