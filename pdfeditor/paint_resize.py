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
from .composition import _pixels_equal
from .content_stream import number, operators
from .elements import _check_clip, _close, _final_events, _local_translation, _paint_value, inspect_element
from .model import Rect
from .mutation import Mutation, MutationProgram
from .paint_geometry import intersects_fill
from .paint_provenance import interpreted_paints
from .pdf_save import program_pdf_bytes
from .proof_session import proof_session
from .transaction import Plan, Transaction


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


def _mutations(content, paths, policies, delta, *, owner=None):
    data = content.streams[-content.page.xref]
    ops = list(operators(data)); result = {}
    for policy in policies:
        path = paths[policy['source_id']]
        paint = path['proof']['paints'][0]
        raw = _raw_path(data,ops,path,paint,policy['band'],delta)
        a,b = path['source']['merged_range']
        prefix = b'n q '+raw+b' '
        result[policy['source_id']] = Mutation(a,b,prefix+path['source']['operator'].encode()+b' Q ',
                                               kind='path-resize',anchors={'paint':len(prefix)},owner=owner)
    return result


def _apply(content, mutations):
    program = MutationProgram(content.streams[-content.page.xref])
    for mutation in mutations: program.add(mutation)
    return program.apply()


class ResizePlan(Plan):
    kind = 'resize'

    def __init__(self, owner=None):
        super().__init__(owner)
        self._check = None
        self._report = {}

    def check(self, page, *, exclude_glyphs, paint_changes):
        self._check(exclude_glyphs, paint_changes)

    def report(self, result):
        return dict(self._report, output_sha256=result.output_sha256,
                    byte_edits=[m.edit() for m in sorted(self.mutations,key=lambda m:m.start)],
                    mutation_map=[m.record() for m in sorted(self.mutations,key=lambda m:m.start)])


def plan_paint_resize(page, snapshot, policies, *, delta, available_bounds, paragraph_snapshot=None, owner=None):
    """Change only explicitly owned paint geometry; preserve every text event."""
    source, content = page.source, page.content
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
    observation = page.observation
    if observation['errors']: raise PdfError(observation['errors'][0])
    plan = ResizePlan(owner)
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
        proof = extend_geometry(path['proof']['paints'][0],policy['band'],delta)
        if (not permitted.contains(Rect(*path['proof']['paints'][0]['bounds']),.002)
                or not permitted.contains(Rect(*proof['expected']['bounds']),.002)):
            raise PdfError('resized paint exceeds its confirmed available region')
        index = path['proof']['paint_indices'][0]
        if index in selected: raise PdfError('resize paints share a paint event')
        selected.add(index); plans[policy['source_id']] = proof
        plan.paint_changes[index] = [proof['expected']]
        plan.planned_paths[policy['source_id']] = [proof['expected']]
        box = Rect(**proof['changed_bounds']); area = box if area is None else area.union(box)
    if not Rect(*content.page.rect).contains(permitted): raise PdfError('resize region is outside the page')
    original = page.glyphs
    def check(exclude_glyphs, paint_changes):
        if not delta: return
        # Clearance is a physical policy, not an inference of ownership.
        guard = Rect(area.x0-1,area.y0-1,area.x1+1,area.y1+1)
        if any(i not in exclude_glyphs and guard.intersects(Rect(*g['bbox'])) for i,g in enumerate(original)):
            raise PdfError('paint resize strip intersects text; vacate it before resizing')
        for i,event in _final_events(observation,paint_changes):
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
    plan._check = check
    control = _apply(content,_mutations(content,paths,policies,0).values())
    with pymupdf.open(stream=program_pdf_bytes(source,content.page.number,control),filetype='pdf') as doc:
        paints = interpreted_paints(doc.tobytes(),snapshot['selection']['page'])
        if (paints['errors'] or not _close([_paint_value(p) for p in observation['events']],[_paint_value(p) for p in paints['events']])
                or not all(_pixels_equal(content.document[i],doc[i]) for i in range(len(doc)))):
            raise PdfError('paint resize no-op changed paint or pixels')
    mutations = _mutations(content,paths,policies,delta,owner=owner)
    plan.mutations = list(mutations.values())
    plan.path_anchors = {ident:(mutation,'paint') for ident,mutation in mutations.items()}
    plan.covering_paths = set(mutations) if delta > 0 else set()
    plan.affected = area if delta else None
    plan.final_rects = [area] if delta else []
    plan._report = dict(backend='source-linked-band-resize',source_sha256=snapshot['source_sha256'],
                        policies=deepcopy(policies),delta=delta,plans=plans,plan_sha256=digest(plans),
                        audit_bbox=asdict(area),paint_plan_verified=True,all_glyphs_preserved=True,font_resources_preserved=True,
                        no_op_verified=True,mupdf_outside_pixels_equal=True)
    return page.add(plan)


@proof_session
def resize_paints(source, output, snapshot, policies, *, delta, available_bounds, paragraph_snapshot=None):
    """Resize owned paints as a single-plan transaction."""
    with Transaction(source) as transaction:
        plan = plan_paint_resize(transaction.page(snapshot['selection']['page']),snapshot,policies,delta=delta,
                                 available_bounds=available_bounds,paragraph_snapshot=paragraph_snapshot)
        result = transaction.commit(output)
        try:
            return plan.report(result)
        finally:
            result.close()
