"""Source-bound decoration roles anchored to Unicode ranges, not fixed pixels.

Ownership is explicit. Geometry, paint order and baseline agreement only
propose candidates. The planner projects confirmed logical ranges through
edits and line layout; the writer preserves each source paint context.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from statistics import median

from uniseg.graphemecluster import grapheme_cluster_boundaries

from .attributed import SourceParagraph
from .backend import PdfError
from .content_stream import number, serialized_event
from .elements import (inspect_element, _confirmed, _close, _paint_value,
                       _check_clip, _check_region_obstacles, _local_translation)
from .model import Rect
from .paint_geometry import fill_cells
from .paint_provenance import interpreted_paints


def _candidates(paragraph, element):
    result=[]
    for item in element['paths']:
        proof=item['proof']
        if proof['status']!='proven' or len(proof['paints'])!=1:
            continue
        paint=proof['paints'][0]
        cells=fill_cells(paint)
        if paint['kind']!='fill-path' or cells is None or len(cells)!=1 or paint['opacity']!=1:
            continue
        rect=cells[0]
        choices=[]
        for line in range(len(paragraph.resolved.lines)):
            units=[(i,u) for i,u in enumerate(paragraph.units) if u.line==line and u.observation is not None]
            visible=[(i,u) for i,u in units if not u.text.isspace()]
            if not visible:
                continue
            size=max(paragraph.styles[u.style_id].size for i,u in visible)
            baseline=median(u.observation['origin'][1]-paragraph.styles[u.style_id].rise for i,u in visible)
            offset=rect.y0-baseline
            if not 0<rect.height<size*.2 or not 0<=offset<size*.3:
                continue
            first=visible[0][1].observation['origin'][0]
            last=visible[-1][1].observation['bbox'][2]
            if abs(rect.x0-first)>size*.15 or abs(rect.x1-last)>size*.3:
                continue
            seq=max(paragraph.content.actual[u.source_index]['span']['seqno'] for i,u in visible)
            if paint['seqno']<=seq:
                continue
            style=paragraph.styles[visible[0][1].style_id]
            op,values=style.event.state.fill
            if paint['colorspace']!={'g':'DeviceGray','rg':'DeviceRGB','k':'DeviceCMYK'}.get(op):
                continue
            if not _close(paint['components'],[float(v) for v in values]):
                continue
            choices.append(dict(source_id=item['source_id'],range=[visible[0][0],visible[-1][0]+1],
                line=line,baseline=baseline,offset=offset,thickness=rect.height,
                observed_bounds=asdict(rect),status='inferred',requires_confirmation=True,
                evidence=dict(provenance='proven',paint_seqno=paint['seqno'],text_seqno=seq,
                              left_residual=rect.x0-first,right_residual=rect.x1-last)))
        if len(choices)==1:
            result.append(choices[0])
    return result


def inspect_anchors(source, paragraph_snapshot, element_snapshot):
    paragraph=SourceParagraph(source,paragraph_snapshot['selection'],line_joiner=paragraph_snapshot['line_joiner'],logical=paragraph_snapshot.get('logical'))
    try:
        if paragraph.snapshot()!=paragraph_snapshot or inspect_element(source,element_snapshot['selection'])!=element_snapshot:
            raise PdfError('anchor inputs no longer match their source')
        if set(paragraph.selection['glyph_ids'])!=set(element_snapshot['selection']['glyph_ids']):
            raise PdfError('paragraph and element select different text')
        candidates=_candidates(paragraph,element_snapshot)
        groups=[]
        for candidate in sorted(candidates,key=lambda c:c['range'][0]):
            if (groups and candidate['line']==groups[-1]['last_line']+1
                    and abs(candidate['offset']-groups[-1]['offset'])<.05
                    and abs(candidate['thickness']-groups[-1]['thickness'])<.05):
                group=groups[-1]
                group['source_ids'].append(candidate['source_id'])
                group['range'][1]=candidate['range'][1]
                group['last_line']=candidate['line']
            else:
                groups.append(dict(source_ids=[candidate['source_id']],range=list(candidate['range']),
                    last_line=candidate['line'],offset=candidate['offset'],thickness=candidate['thickness'],
                    status='inferred',requires_confirmation=True))
        return dict(schema_version=1,paragraph_sha256=paragraph_snapshot['snapshot_sha256'],
            element_sha256=element_snapshot['snapshot_sha256'],candidates=candidates,suggested_groups=groups,
            contract='Confirm logical underline ranges and boundary insertion affinity; geometry is not ownership.')
    finally:
        paragraph.close()


def project_range(text, edits, start, end, *, start_affinity='reject', end_affinity='reject'):
    """Map a confirmed range through disjoint source-coordinate edits.

    Replacement crossing a range edge is ambiguous. Insertions at an edge
    require an explicit inside/outside affinity. Full deletion removes the
    decoration, and replacement of exactly the complete range retains it.
    """
    boundaries={0,len(text),*grapheme_cluster_boundaries(text)}
    if type(start) is not int or type(end) is not int or start not in boundaries or end not in boundaries or not start<end:
        raise PdfError('anchor range must use ordered Unicode grapheme boundaries')
    if start_affinity not in ('inside','outside','reject') or end_affinity not in ('inside','outside','reject'):
        raise PdfError('anchor boundary affinity must be inside, outside or reject')
    membership=[];parts=[];cursor=0
    for edit in sorted(edits,key=lambda e:(e['start'],e['end'])):
        a,b,value=edit['start'],edit['end'],edit['text']
        parts.append(text[cursor:a]);membership.extend(start<=i<end for i in range(cursor,a))
        if a<b and ((a<start<b) or (a<end<b)):
            raise PdfError('edit crosses an anchor boundary; explicitly redefine the decorated range')
        inside=start<=a and b<=end
        if a==b:
            inside=start<a<end
            if a in (start,end):
                affinity=start_affinity if a==start else end_affinity
                if affinity=='reject' and value:
                    raise PdfError('insertion at an anchor boundary needs explicit affinity')
                inside=affinity=='inside'
        parts.append(value);membership.extend([inside]*len(value));cursor=b
    parts.append(text[cursor:]);membership.extend(start<=i<end for i in range(cursor,len(text)))
    positions=[i for i,flag in enumerate(membership) if flag]
    if not positions:
        return None
    result=(positions[0],positions[-1]+1)
    if positions!=list(range(*result)):
        raise PdfError('edited decoration is no longer one contiguous range')
    after=''.join(parts)
    limits={0,len(after),*grapheme_cluster_boundaries(after)}
    if any(p not in limits for p in result):
        raise PdfError('edited anchor would split a grapheme')
    return result


def _rect_commands(rectangles, matrix):
    commands=[]
    for rect in rectangles:
        points=[(rect.x0,rect.y0),(rect.x1,rect.y0),(rect.x1,rect.y1),(rect.x0,rect.y1)]
        for i,(x,y) in enumerate(points):
            local=_local_translation(matrix,x-matrix[4],y-matrix[5])
            commands.append(number(local[0])+b' '+number(local[1])+(b' m ' if i==0 else b' l '))
        commands.append(b'h ')
    return b''.join(commands)


class AnchoredPaintEdit:
    def __init__(self, source, paragraph, paragraph_snapshot, element, specification, edits):
        if not element or inspect_element(source,element['selection'])!=element:
            raise PdfError('anchor element snapshot does not match its source')
        if (specification.get('paragraph_sha256')!=paragraph_snapshot['snapshot_sha256']
                or specification.get('element_sha256')!=element['snapshot_sha256']):
            raise PdfError('anchor specification must reference the reviewed paragraph and element snapshots')
        if (set(paragraph.selection['glyph_ids'])!=set(element['selection']['glyph_ids'])
                or paragraph.selection['page']!=element['selection']['page']):
            raise PdfError('anchors and paragraph select different source text')
        anchors=specification.get('underlines')
        fixed=specification.get('fixed_relations',[])
        if not isinstance(anchors,list) or not isinstance(fixed,list):
            raise PdfError('underline anchors and fixed relations must be lists')
        if any(r.get('behavior')!='fixed-to-page' for r in fixed):
            raise PdfError('non-decoration relations must explicitly remain fixed to the page')
        candidates={c['source_id']:c for c in _candidates(paragraph,element)}
        ids=[];self.groups=[];ranges=[]
        for anchor in anchors:
            source_ids=anchor.get('source_ids')
            if not isinstance(source_ids,list) or not source_ids or any(i not in candidates for i in source_ids):
                raise PdfError('underline needs source-proven horizontal solid-fill candidates')
            a,b=anchor['range']
            if any(a<y and b>x for x,y in ranges):
                raise PdfError('overlapping decoration anchors need a richer style model')
            ranges.append((a,b))
            projected=project_range(paragraph.text,edits,a,b,start_affinity=anchor.get('start_affinity','reject'),
                                    end_affinity=anchor.get('end_affinity','reject'))
            values=[candidates[i] for i in source_ids]
            offset=median(c['offset'] for c in values);thickness=median(c['thickness'] for c in values)
            if any(abs(c['offset']-offset)>.05 or abs(c['thickness']-thickness)>.05 for c in values):
                raise PdfError('underline templates disagree in baseline offset or thickness')
            ids.extend(source_ids)
            self.groups.append(dict(source_ids=source_ids,input_range=[a,b],output_range=projected,
                                    offset=offset,thickness=thickness,provenance='explicitly_supplied',
                                    start_affinity=anchor.get('start_affinity','reject'),end_affinity=anchor.get('end_affinity','reject')))
        if len(ids)!=len(set(ids)):
            raise PdfError('a source path cannot belong to multiple underline groups')
        self.paths,self.decisions=_confirmed(element,fixed+[dict(source_id=i,relation='decorates',behavior='fixed-to-element') for i in ids])
        self.ids=set(ids);self.source=source;self.paragraph=paragraph;self.element=element
        self.observation=interpreted_paints(source,paragraph.resolved.page)
        if self.observation['errors']:
            raise PdfError(self.observation['errors'][0])
        marks=element['marked_content']
        if not marks['complete']:
            raise PdfError('incomplete marked-content observation')
        selected_ranges={tuple(self.paths[i]['source']['merged_range']) for i in ids}
        if any(not op['invocation'] and tuple(op['byte_range']) in selected_ranges and op['active_scope_ids'] for op in marks['operators']):
            raise PdfError('anchored paint in marked content needs structure-aware reconstruction')
        self.indices={n for i in ids for n in self.paths[i]['proof']['paint_indices']}
        self.patches=[];self.segments=[];self.replacements={}

    def plan(self, layout, inks, ink_bounds, available_region):
        for group_index,group in enumerate(self.groups):
            paths=sorted((self.paths[i] for i in group['source_ids']),key=lambda p:p['source']['merged_range'][0])
            template=paths[0]['proof']['paints'][0]
            _check_clip(template,ink_bounds)
            for path in paths:
                other=path['proof']['paints'][0]
                for key in ('colorspace','components','opacity','color_params','clips','groups','layers','even_odd'):
                    if not _close(template[key],other[key]):
                        raise PdfError('underline source paints have different rendering contexts')
                if path['source']['pending_clip_operator_indices']:
                    raise PdfError('underline paint also installs a clip')
            rectangles=[]
            if group['output_range'] is not None:
                start,end=group['output_range']
                for line in layout.lines:
                    glyphs=[g for g in line.glyphs if g.start<end and g.end>start]
                    if any(g.start<start or g.end>end for g in glyphs):
                        raise PdfError('underline anchor splits a shaped cluster')
                    while glyphs and glyphs[0].glyph.text.isspace():glyphs.pop(0)
                    while glyphs and glyphs[-1].glyph.text.isspace():glyphs.pop()
                    if not glyphs:
                        continue
                    rect=Rect(glyphs[0].x,line.baseline+group['offset'],
                              glyphs[-1].x+glyphs[-1].glyph.advance,line.baseline+group['offset']+group['thickness'])
                    if rect.width<=0:
                        raise PdfError('underline has no positive visual advance')
                    if not available_region.contains(rect,.001):
                        raise PdfError('underline extends beyond the confirmed paragraph region')
                    _check_clip(template,rect)
                    rectangles.append(rect)
                    self.segments.append(dict(group=group_index,range=[glyphs[0].start,glyphs[-1].end],baseline=line.baseline,bounds=asdict(rect)))
            for path in paths:
                replacement=path['proof']['evidence']['replacement'].encode()
                if path is paths[0] and rectangles:
                    # Keep one paint operator per visual segment so a reopened
                    # PDF can recover and edit these roles through source IDs.
                    replacement+=b' q '
                    painted=[]
                    for r in rectangles:
                        replacement+=_rect_commands([r],template['matrix'])+path['source']['operator'].encode()+b' '
                        paint=deepcopy(template)
                        paint['geometry']=[['m',r.x0,r.y0],['l',r.x1,r.y0],['l',r.x1,r.y1],['l',r.x0,r.y1],['h']]
                        paint['bounds']=list(r.tuple());painted.append(paint)
                    replacement+=b' Q '
                    self.replacements[path['proof']['paint_indices'][0]]=painted
                a,b=path['source']['merged_range'];self.patches.append((a,b,replacement))
        regions=list(inks)+[Rect(**s['bounds']) for s in self.segments]
        self.bounds=ink_bounds
        for i in self.ids:
            self.bounds=self.bounds.union(Rect(*self.paths[i]['proof']['paints'][0]['bounds']))
        for r in regions:
            self.bounds=self.bounds.union(r)
            if not Rect(*self.paragraph.content.page.rect).contains(r,.001):
                raise PdfError('anchored decoration extends outside the page')
        _check_region_obstacles(self.paragraph.content,set(self.paragraph.selection['glyph_ids']),self.paragraph.resolved,
            regions,self.bounds,self.observation,self.paths,self.decisions,self.ids)

    def removal_program(self):
        paragraph=self.paragraph;selected=set(paragraph.selection['glyph_ids'])
        patches=[(e.operator.start,e.operator.end,serialized_event(e,selected,remove=True)) for e in paragraph.events]
        patches.extend((*self.paths[i]['source']['merged_range'],self.paths[i]['proof']['evidence']['replacement'].encode()) for i in self.ids)
        data=paragraph.content.streams[-paragraph.content.page.xref]
        for a,b,value in sorted(patches,reverse=True):data=data[:a]+value+data[b:]
        return data

    def verify(self, document, *, removed=False):
        observation=interpreted_paints(document.tobytes(),self.paragraph.resolved.page)
        if observation['errors']:
            raise PdfError(observation['errors'][0])
        def nontext(events):
            return [_paint_value(e) for e in events if e['kind'] not in ('fill-text','stroke-text','ignore-text')]
        expected=[]
        for i,event in enumerate(self.observation['events']):
            if i not in self.indices:expected.append(event)
            elif not removed and i in self.replacements:expected.extend(self.replacements[i])
        if not _close(nontext(expected),nontext(observation['events'])):
            raise PdfError('saved decoration or other non-text paint differs from the anchored plan')

    def report(self):
        return dict(groups=self.groups,segments=self.segments,source_paths_replaced=len(self.ids),
            nontext_paint_plan_verified=True,behavior='underline follows edited Unicode range and line advances',
            background_behavior='fixed-to-page',flow='inline range to visual lines; no downstream element movement')
