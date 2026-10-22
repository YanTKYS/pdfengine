"""Selection-driven observation and conservative horizontal-column inference.

No evaluation geometry enters this module. Occupied right edges are evidence,
not an assertion that a PDF stores an author's text frame.
"""
from statistics import median
import pymupdf
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import _Analysis
from .engine import Paragraph, Refused, _check_inbound


def qualify(c, marks, ids, rows):
    chosen=set(ids); events=[e for e in c.events if any(chosen.intersection(ch.source_orders) for ch in e.chars)]
    if [i for e in events for ch in e.chars for i in ch.source_orders if i in chosen]!=ids:
        raise Refused('source paint order differs from paragraph reading order')
    sizes={i:e.state.size for e in events for ch in e.chars for i in ch.source_orders}
    for e in events:
        if e.invocation or e.state.tr!=0 or e.state.opacity!=1:
            raise Refused('Form, non-fill or transparent selected text unsupported')
        if e.state.ctm!=(1,0,0,1,0,0) or e.text_matrix[:4]!=(1,0,0,1):
            raise Refused('non-axis-aligned source text matrix unsupported')
        if e.state.ts!=0:raise Refused('nonzero text rise requires a baseline-shift adapter')
        if e.state.fill[0] not in ('g','rg','k') or e.state.tz<=0:
            raise Refused('unsupported source color or horizontal scale')
        if any(len(ch.source_orders)!=1 or len(ch.text)!=1 for ch in e.chars if chosen.intersection(ch.source_orders)):
            raise Refused('one-code/one-Unicode/one-occurrence witness required')
    scopes=[]
    for s in marks['scopes']:
        hits={i for e in c.events if s['content_byte_range'][0]<=e.operator.start<s['content_byte_range'][1] for ch in e.chars for i in ch.source_orders}
        if hits & chosen:
            if not hits<=chosen or s['invocation'] or s['enclosing_scope_ids']:
                raise Refused('mixed or nested marked-content scope')
            scopes.append(s)
    # A previous edit may leave an ordered empty scope. Its semantic child
    # remains part of the paragraph even though no glyph can select it.
    parent_candidates=set()
    for s in scopes:
        a=s['association']
        if a['status']=='tree_backed':
            owner=c.reader.get_object(a['owner_reference']['xref'])
            parent_candidates.add(a['owner_reference']['xref'] if owner.get('/S')=='/P' else owner.raw_get('/P').idnum)
    if len(parent_candidates)==1:
        chosen_parent=next(iter(parent_candidates))
        for s in marks['scopes']:
            if s in scopes or s['association']['status']!='tree_backed':continue
            a=s['association'];owner=c.reader.get_object(a['owner_reference']['xref'])
            parent_id=a['owner_reference']['xref'] if owner.get('/S')=='/P' else owner.raw_get('/P').idnum
            if parent_id!=chosen_parent:continue
            lo,hi=s['content_byte_range']
            if not any(ch.source_orders for e in c.events if lo<=e.operator.start<hi for ch in e.chars):
                if s['invocation'] or s['enclosing_scope_ids']:raise Refused('nested empty scope')
                scopes.append(s)
        scopes.sort(key=lambda s:s['begin_byte_range'][0])
    parent=None
    root=c.reader.trailer['/Root'].get('/StructTreeRoot')
    if root and '/IDTree' in root:raise Refused('IDTree references require richer semantic adapter')
    if scopes:
        parents=set()
        for s in scopes:
            a=s['association']
            if set(s['properties'])-{'/MCID','/Lang'}:raise Refused('unsupported scope semantics')
            if a['status']!='tree_backed':raise Refused('unbound marked content')
            owner=c.reader.get_object(a['owner_reference']['xref'])
            text=''.join(ch.text for e in c.events if s['content_byte_range'][0]<=e.operator.start<s['content_byte_range'][1] for ch in e.chars)
            if '/ActualText' in owner and str(owner['/ActualText'])!=text:raise Refused('source ActualText differs from drawing')
            if owner.get('/S') not in ('/Span','/P') or set(owner)-{'/Type','/S','/P','/K','/Pg','/ActualText','/Lang'}:raise Refused('unsupported leaf semantics')
            parents.add(a['owner_reference']['xref'] if owner.get('/S')=='/P' else owner.raw_get('/P').idnum)
        if len(parents)!=1:raise Refused('multiple semantic paragraph parents')
        parent=parents.pop(); p=c.reader.get_object(parent)
        if p.get('/S')!='/P' or set(p)-{'/Type','/S','/P','/K','/Pg','/Lang'}:raise Refused('unsupported paragraph semantics')
        actual=[]
        for item in p['/K']:
            if hasattr(item,'idnum'):actual.append(('span',item.idnum))
            elif isinstance(item,int):actual.append(('mcid',int(item)))
            else:raise Refused('unsupported paragraph child')
        expected=[('mcid',s['association']['mcid']) if s['association']['owner_reference']['xref']==parent else ('span',s['association']['owner_reference']['xref']) for s in scopes]
        if actual!=expected:raise Refused('paragraph child order/coverage mismatch')
    elif root:raise Refused('tagged selected glyphs lack semantic association')
    return Paragraph(''.join(c.actual[i]['unicode'] for i in ids),ids,[sizes[i] for i in ids],[r['baseline'] for r in rows],scopes,parent)


def detect(source,page,selection,occurrence=0):
    c=ContentPage(source,page)
    try:
        if c.errors:raise Refused('incomplete source interpretation: '+str(c.errors))
        if c.page.rotation or c.page.rect.x0 or c.page.rect.y0:raise Refused('rotated/nonzero-origin page')
        marks=observe_marked_content(source,page)
        if not marks['complete']:raise Refused('incomplete marked-content observation')
        analysis=_Analysis(source,page)
        rows=[dict(l) for l in analysis.lines if l['text'].strip()]
        # Preserve meaningful spaces while dropping independent blank-only paints.
        for r in rows:
            ids=r['glyph_ids']; first=min(c.actual[i]['origin'][0] for i in ids if c.actual[i]['unicode'].strip())
            r['glyph_ids']=[i for i in ids if c.actual[i]['unicode'].strip() or c.actual[i]['origin'][0]>first+.001]
        parent_by_id={}
        for s in marks['scopes']:
            a=s['association']
            if a['status']=='tree_backed':
                owner=c.reader.get_object(a['owner_reference']['xref']); pr=owner.indirect_reference if owner.get('/S')=='/P' else owner.get('/P')
                if pr is not None and pr.get_object().get('/S')=='/P':
                    for e in c.events:
                        if s['content_byte_range'][0]<=e.operator.start<s['content_byte_range'][1]:
                            for ch in e.chars:
                                for i in ch.source_orders:parent_by_id[i]=pr.idnum
        groups=[]
        for r in rows:
            ids=r['glyph_ids']; parents={parent_by_id.get(i) for i in ids}; parent=next(iter(parents)) if len(parents)==1 else None
            new=True
            if groups:
                prev=groups[-1][-1]; prevparents={parent_by_id.get(i) for i in prev['glyph_ids']}
                gap=r['baseline']-prev['baseline']; size=r['size']
                if parent is not None:new=prevparents!={parent}
                else:
                    same_size=abs(size-prev['size'])<.1
                    # Continuations start at the same or a slightly smaller x;
                    # an indented new line or paragraph gap starts a paragraph.
                    new=not (same_size and 0<gap<2.25*size and -1.6*size<=r['x']-prev['x']<.2*size)
            if new:groups.append([])
            groups[-1].append(r)
        texts=[''.join(c.actual[i]['unicode'] for r in g for i in r['glyph_ids']) for g in groups]
        matches=[]
        for n,t in enumerate(texts):
            at=0
            while selection and (pos:=t.find(selection,at))>=0:matches.append((n,pos));at=pos+1
        if not isinstance(occurrence,int) or occurrence<0 or occurrence>=len(matches):raise Refused('selected string occurrence not found in one inferred paragraph')
        target,start=matches[occurrence]; selected=groups[target]; size=median(r['size'] for r in selected)
        left=min(r['x'] for r in selected); first=selected[0]['x']
        # A single-line paragraph borrows the observed continuation edge from
        # nearby same-size body rows, never from a manifest.
        nearby=[r for r in rows if abs(r['size']-size)<.1 and abs(r['baseline']-selected[0]['baseline'])<8*size and first-1.6*size<=r['x']<=first+.1]
        left=min([left]+[r['x'] for r in nearby]); followers=[]
        previous=selected
        for group in groups[target+1:]:
            r=group[0]; gap=r['baseline']-previous[-1]['baseline']
            if abs(r['size']-size)>.1 or abs(r['x']-first)>.2*size or not 0<gap<2.8*size:break
            followers.append(group);previous=group
        allgroups=[selected]+followers; allrows=[r for g in allgroups for r in g]
        deltas=[b['baseline']-a['baseline'] for g in allgroups for a,b in zip(g,g[1:])]
        leading=median(deltas) if deltas else min([b['baseline']-a['baseline'] for a,b in zip(allrows,allrows[1:])] or [size*1.5])
        right=max(r['pen_end'] for r in allrows)
        top=min(min(r['top'] for r in selected)-.5,selected[0]['baseline']-max(r['size'] for r in selected)-.5)
        ids={i for r in allrows for i in r['glyph_ids']}; last=max(r['bottom'] for r in allrows)
        obstacles=list(analysis.obstacles)
        for link in c.page.get_links():obstacles.append(dict(kind='annotation',bounds=list(link['from'])))
        for annot in (c.page.annots() or []):obstacles.append(dict(kind='annotation',bounds=list(annot.rect)))
        obstacles.extend(dict(kind='foreign-text',bounds=list(g['bbox'])) for i,g in enumerate(c.actual) if i not in ids and g['unicode'].strip())
        below=[o['bounds'][1] for o in obstacles if o['bounds'][1]>last+.1 and o['bounds'][0]<right and o['bounds'][2]>left]
        bottom=min(below+[c.page.rect.height])-1
        # Existing paints intersecting a proposed frame remain protected; the
        # writer checks actual new ink, not just this envelope.
        frame=[left-.5,top,right+.5,bottom]
        paragraphs=[qualify(c,marks,[i for r in g for i in r['glyph_ids']],g) for g in allgroups]
        # No structure leaves are deleted: writer retains every original scope.
        # Existing inbound references therefore remain valid.
        evidence=dict(observed=dict(lines=[{k:r[k] for k in ('id','text','glyph_ids','x','pen_end','baseline','size','bbox')} for r in allrows],semantic_parents=[p.parent for p in paragraphs],obstacles=obstacles),
            inferred=dict(frame=frame,left=left,right=right,first_x=first,baseline=selected[0]['baseline'],line_height=leading,paragraph_gap=[g[0]['baseline']-h[-1]['baseline'] for h,g in zip(allgroups,allgroups[1:])],baseline_policy='existing target line baselines are observed and retained; added lines use inferred median leading'),
            confidence=dict(paragraph='high: ordered P children' if paragraphs[0].parent else 'medium: indentation, spacing and style continuity',right='medium: maximum occupied pen extent; author frame absent',bottom='medium: next protected paint/annotation or page edge',top='medium: observed line boxes plus one-em ascender allowance; staged ink and raster checked'),
            warnings=['Occupied width is an inferred capacity, not a stored author frame.','Following same-column paragraphs may translate; protected content never moves.'],manual_geometry_inputs=0)
        return c,paragraphs,dict(page=page,frame=frame,left=left,right=right,first_x=first,baseline=selected[0]['baseline'],line_height=leading,line_offsets=[r['baseline']-selected[0]['baseline'] for r in selected]),start,evidence
    except Exception:c.close();raise
