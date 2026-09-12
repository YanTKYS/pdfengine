"""Certified vertical extension of solid fill paths with a straight middle band.

Caps translate rigidly or stay fixed. Only vertical connections may cross the
caller-supplied band. This is not arbitrary path scaling or ownership inference.
"""
from copy import deepcopy
from dataclasses import asdict
import math

import pymupdf

from .attributed import digest
from .backend import PdfError
from .composition import _observations, _pixels_equal
from .content_stream import ContentPage, number, operators
from .elements import _check_clip, _close, _local_translation, _paint_value, inspect_element
from .model import Rect
from .paint_geometry import intersects_fill
from .paint_provenance import interpreted_paints
from .pdf_save import font_fingerprints, program_pdf_bytes, publish_program
from .proof_session import proof_session
from .replay import compare_glyphs, ensure_destination
from .selection import source_sha


MODEL = 'vertical-straight-band-v1'


def extend_geometry(paint, band, delta):
    """Prove an orientation-preserving band extension, then transform its caps.

    A strictly increasing piecewise-linear y map is the identity above the
    band and translation below it. Since crossings are vertical straight
    lines, those lines represent its exact image; no curve approximation or
    corner scaling is involved. Control hulls must stay outside the band.
    """
    top, bottom = band
    if (not all(math.isfinite(v) for v in (top, bottom, delta))
            or bottom - top <= .02 or bottom - top + delta <= .02):
        raise PdfError('resize band must retain a positive height')
    if paint['kind'] != 'fill-path' or paint['opacity'] != 1:
        raise PdfError('band resize supports opaque solid fill paths only')
    if paint['colorspace'] not in ('DeviceGray', 'DeviceRGB', 'DeviceCMYK'):
        raise PdfError('band resize needs a known solid device color')
    _check_clip(paint, Rect(*paint['bounds']))

    def zone(point):
        # Keep a guard around the declared thresholds: renderer coordinates
        # and original number tokens must not classify opposite sides.
        if point[1] < top - .01: return 0
        if point[1] > bottom + .01: return 1
        raise PdfError('path vertex or curve control lies in the resize band')

    result = deepcopy(paint['geometry'])
    start = current = None
    crossings = 0
    moved = []
    for original, command in zip(paint['geometry'], result):
        name = original[0]
        points = [original[i:i+2] for i in range(1, len(original), 2)]
        if name == 'm':
            if current is not None: raise PdfError('resize needs explicitly closed subpaths')
            start = current = points[0]
            zone(current)
        elif name in ('l', 'h'):
            if current is None: raise PdfError('resize path has no current point')
            end = start if name == 'h' else points[0]
            if zone(current) != zone(end):
                if abs(current[0] - end[0]) > 1e-9:
                    raise PdfError('only vertical straight edges may cross the resize band')
                crossings += 1
            current = None if name == 'h' else end
        elif name == 'c':
            if current is None or len({zone(p) for p in [current] + points}) != 1:
                raise PdfError('a cubic curve crosses the resize band')
            current = points[-1]
        else:
            raise PdfError('unsupported path geometry in resize model')
        for i, point in enumerate(points):
            if zone(point):
                moved.append(point)
                command[2+2*i] += delta
    if current is not None or crossings < 2 or not moved:
        raise PdfError('resize requires closed caps joined across the band')
    expected = _paint_value(paint)
    expected['geometry'] = result
    expected['bounds'][3] += delta
    # Only lower caps and extensions of their vertical connections change.
    # Everything above this strip is mathematically unchanged.
    low = min(p[1] for p in moved)
    changed = Rect(paint['bounds'][0], low + min(0, delta), paint['bounds'][2],
                   max(paint['bounds'][3], paint['bounds'][3]+delta))
    _check_clip(paint, Rect(*expected['bounds']))
    return dict(expected=expected, changed_bounds=asdict(changed), vertical_crossings=crossings,
                proof='strictly increasing band map; rigid caps; exact vertical connections')


def _raw_path(data, ops, path, paint, band, delta):
    first = min(path['source']['path_operator_indices'])
    last = path['source']['operator_index']
    if any(op.name not in ('m','l','c','v','y','h','re') for op in ops[first:last]):
        raise PdfError('resize path construction crosses graphics state or another paint')
    matrix = paint['matrix']
    dx, dy = _local_translation(matrix, 0, delta)
    parts = []
    for index in path['source']['path_operator_indices']:
        op = ops[index]
        if op.name == 'h' or not delta:
            parts.append(data[op.start:op.end]); continue
        args = [float(v) for v in op.args]
        if op.name == 're':
            x,y,w,h = args
            commands = [('m',[x,y]),('l',[x+w,y]),('l',[x+w,y+h]),('l',[x,y+h]),('h',[])]
        else:
            commands = [(op.name,args)]
        for name, values in commands:
            changed = False
            for i in range(0,len(values),2):
                page_y = values[i]*matrix[1] + values[i+1]*matrix[3] + matrix[5]
                if page_y > band[1] + .01:
                    values[i] += dx; values[i+1] += dy; changed = True
                elif page_y >= band[0] - .01:
                    raise PdfError('source operands do not lie outside the declared band')
            if not changed and op.name != 're':
                parts.append(data[op.start:op.end])
            else:
                parts.append(b' '.join([number(v) for v in values]+[name.encode()]))
    return b' '.join(parts)


def _program(content, paths, policies, delta):
    data = content.streams[-content.page.xref]
    ops = list(operators(data)); edits = []
    for policy in policies:
        path = paths[policy['source_id']]
        paint = path['proof']['paints'][0]
        raw = _raw_path(data,ops,path,paint,policy['band'],delta)
        a,b = path['source']['merged_range']
        new = b'n q '+raw+b' '+path['source']['operator'].encode()+b' Q '
        edits.append((a,b,new))
    for a,b,new in sorted(edits,reverse=True): data = data[:a]+new+data[b:]
    return data, [dict(start=a,end=b,length=len(new)) for a,b,new in sorted(edits)]


@proof_session
def resize_paints(source, output, snapshot, policies, *, delta, available_bounds, paragraph_snapshot=None):
    """Change only explicitly owned paint geometry; preserve every text event."""
    output = ensure_destination(output,source)
    if inspect_element(source,snapshot['selection'],paragraph_snapshot=paragraph_snapshot) != snapshot:
        raise PdfError('resize snapshot differs from the current PDF')
    if not snapshot['marked_content']['complete'] or snapshot['renderer_observation_errors']:
        raise PdfError('resize needs complete source and graphics-state observation')
    if not policies or len({p['source_id'] for p in policies}) != len(policies):
        raise PdfError('resize needs unique explicitly owned paints')
    paths = {p['source_id']:p for p in snapshot['paths']}
    permitted = Rect(*available_bounds)
    if not all(math.isfinite(v) for v in permitted.tuple()) or permitted.width<=0 or permitted.height<=0:
        raise PdfError('paint resize needs a finite confirmed available region')
    observation = interpreted_paints(source,snapshot['selection']['page'])
    if observation['errors']: raise PdfError(observation['errors'][0])
    expected = [_paint_value(p) for p in observation['events']]
    plans = {}; selected = set(); area = None
    for policy in policies:
        if (policy.get('geometry_model') != MODEL or policy.get('role') not in ('backgrounds','borders')
                or not isinstance(policy.get('owner'),str) or not policy['owner']
                or policy.get('provenance') != 'explicitly_confirmed'):
            raise PdfError('paint ownership, role and resize model require separate explicit confirmation')
        path = paths.get(policy['source_id'])
        if (path is None or path['proof']['status'] != 'proven' or len(path['proof']['paints']) != 1
                or not path['source']['mutable'] or path['source']['invocation']
                or path['source']['marked_content'] or path['source']['pending_clip_operator_indices']
                or path['source']['operator'] not in ('f','f*')):
            raise PdfError('resize needs a unique unmarked root fill without pending clipping')
        plan = extend_geometry(path['proof']['paints'][0],policy['band'],delta)
        if (not permitted.contains(Rect(*path['proof']['paints'][0]['bounds']),.002)
                or not permitted.contains(Rect(*plan['expected']['bounds']),.002)):
            raise PdfError('resized paint exceeds its confirmed available region')
        index = path['proof']['paint_indices'][0]
        if index in selected: raise PdfError('resize paints share a paint event')
        selected.add(index); expected[index] = plan['expected']; plans[policy['source_id']] = plan
        box = Rect(**plan['changed_bounds']); area = box if area is None else area.union(box)
    content = ContentPage(source,snapshot['selection']['page'])
    try:
        if not Rect(*content.page.rect).contains(permitted): raise PdfError('resize region is outside the page')
        original = _observations(content.page)
        if delta:
            # Clearance is a physical policy, not an inference of ownership.
            guard = Rect(area.x0-1,area.y0-1,area.x1+1,area.y1+1)
            if any(guard.intersects(Rect(*g['bbox'])) for g in original):
                raise PdfError('paint resize strip intersects text; vacate it before resizing')
            for i,event in enumerate(observation['events']):
                if i in selected: continue
                if event['kind'] in ('fill-path','stroke-path') and guard.intersects(Rect(*event['bounds'])):
                    if event['kind']=='fill-path' and intersects_fill(event,guard) is False: continue
                    raise PdfError('paint resize strip intersects a fixed vector')
            if any(guard.intersects(Rect(*p['bbox'])) for p in content.page.get_image_info()):
                raise PdfError('paint resize strip intersects an image')
            if any(guard.intersects(Rect(*p['from'])) for p in content.page.get_links()):
                raise PdfError('paint resize strip intersects a link')
            for p in list(content.page.annots() or [])+list(content.page.widgets() or []):
                if guard.intersects(Rect(*p.rect)): raise PdfError('paint resize strip intersects an annotation or widget')
        control,_ = _program(content,paths,policies,0)
        with pymupdf.open(stream=program_pdf_bytes(source,content.page.number,control),filetype='pdf') as doc:
            paints = interpreted_paints(doc.tobytes(),snapshot['selection']['page'])
            if (paints['errors'] or not _close([_paint_value(p) for p in observation['events']],[_paint_value(p) for p in paints['events']])
                    or not all(_pixels_equal(content.document[i],doc[i]) for i in range(len(doc)))):
                raise PdfError('paint resize no-op changed paint or pixels')
        data, byte_edits = _program(content,paths,policies,delta)
        fonts = font_fingerprints(content.document,content.page.number)
        def verify(doc):
            if (len(doc)!=len(content.document) or doc.permissions!=content.document.permissions
                    or doc.metadata.get('encryption')!=content.document.metadata.get('encryption')):
                raise PdfError('paint resize changed page count or security')
            actual = interpreted_paints(doc.tobytes(),snapshot['selection']['page'])
            if actual['errors'] or not _close(expected,[_paint_value(p) for p in actual['events']]):
                raise PdfError('saved paint differs from the certified band transformation')
            if not compare_glyphs(original,_observations(doc[content.page.number]))['passed']:
                raise PdfError('paint resize changed a source glyph')
            if font_fingerprints(doc,content.page.number)!=fonts: raise PdfError('paint resize changed font resources')
            if not all(_pixels_equal(content.document[i],doc[i],area if delta and i==content.page.number else None) for i in range(len(doc))):
                raise PdfError('paint resize changed pixels outside its proven strip')
        if source_sha(source)!=snapshot['source_sha256']: raise PdfError('resize source revision changed')
        publish_program(source,content.page.number,data,output,verify)
        return dict(backend='source-linked-band-resize',source_sha256=snapshot['source_sha256'],output_sha256=source_sha(output),
                    policies=deepcopy(policies),delta=delta,plans=plans,plan_sha256=digest(plans),byte_edits=byte_edits,
                    audit_bbox=asdict(area),paint_plan_verified=True,all_glyphs_preserved=True,font_resources_preserved=True,
                    no_op_verified=True,mupdf_outside_pixels_equal=True)
    finally:
        content.close()
