"""Selection-driven observation and conservative horizontal-column inference.

No evaluation geometry enters this module. Occupied right edges are evidence,
not an assertion that a PDF stores an author's text frame.
"""
from statistics import median
import pymupdf
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import _Analysis
from . import auto_conditions as conditions
from .engine import Paragraph, Refused, _check_inbound

LATIN_END = ',.;:!?)]\'"%'
LATIN_START = '(["\''


def row_text(c, row):
    return ''.join(c.actual[i]['unicode'] for i in row['glyph_ids'])


def group_text(c, group):
    """Logical paragraph text of visual rows, with implicit Latin line-join spaces.

    A line break between two Latin words is a word space that has no glyph.
    A line-final hyphen between Latin letters may be soft or hard; it is kept
    as drawn and reported, never removed or joined by guesswork.
    """
    text, joins, hyphens = '', [], []
    for n, row in enumerate(group):
        line = row_text(c, row)
        if n and text and line:
            prev, first = text[-1], line[0]
            if (prev == '-' and len(text) > 1 and text[-2].isascii() and text[-2].isalpha()
                    and first.isascii() and first.isalpha()):
                hyphens.append(group[n - 1]['glyph_ids'][-1])
            elif (prev.isascii() and first.isascii() and (prev.isalnum() or prev in LATIN_END)
                  and (first.isalnum() or first in LATIN_START)):
                joins.append(len(text))
                text += ' '
        text += line
    return text, joins, hyphens


def clean_rows(c, rows):
    """Analysis rows with independent blank-only paints at a row start removed."""
    result = []
    for r in rows:
        r = dict(r)
        ids = r['glyph_ids']
        inked = [c.actual[i]['origin'][0] for i in ids if c.actual[i]['unicode'].strip()]
        if not inked:
            continue
        first = min(inked)
        r['glyph_ids'] = [i for i in ids if c.actual[i]['unicode'].strip() or c.actual[i]['origin'][0] > first + .001]
        result.append(r)
    return result


def _overlap(a, b):
    return a['x'] < b['pen_end'] and b['x'] < a['pen_end']


def _first_word_fits(c, prev, row, right):
    """Whether the next row's first Latin word would have fit on the previous row.

    If it would, the previous row ended its paragraph deliberately. Only
    applied to Latin words: CJK lines may end early for line-breaking rules.
    """
    word=[]
    for i in row['glyph_ids']:
        ch=c.actual[i]['unicode']
        if ch.isspace():break
        word.append(i)
    if not word or not all(c.actual[i]['unicode'].isascii() and c.actual[i]['unicode'].isalnum() for i in word):return False
    width=c.actual[word[-1]]['bbox'][2]-row['x']
    return prev['pen_end']+.25*prev['size']+width<right-.5*prev['size']


def qualify(c, marks, ids, rows):
    chosen=set(ids)
    owned={i for e in c.events for ch in e.chars for i in ch.source_orders}
    if not chosen<=owned:
        reasons=sorted({e.error for e in c.events if e.error}) or ['trace glyph without a content-stream character']
        raise Refused('paragraph glyphs lack content-stream provenance: '+'; '.join(reasons))
    events=[e for e in c.events if any(chosen.intersection(ch.source_orders) for ch in e.chars)]
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
    text,joins,hyphens=group_text(c,rows)
    return Paragraph(text,ids,[sizes[i] for i in ids],[r['baseline'] for r in rows],scopes,parent,tuple(joins),tuple(hyphens))


def detect(source,page,selection,occurrence=0,stored=True):
    c=ContentPage(source,page)
    try:
        if c.errors:raise Refused('incomplete source interpretation: '+str(c.errors))
        if c.page.rotation or c.page.rect.x0 or c.page.rect.y0:raise Refused('rotated/nonzero-origin page')
        marks=observe_marked_content(source,page)
        if not marks['complete']:raise Refused('incomplete marked-content observation')
        analysis=_Analysis(source,page)
        # Preserve meaningful spaces while dropping independent blank-only paints.
        rows=clean_rows(c,[dict(l) for l in analysis.lines if l['text'].strip()])
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
            # The predecessor is the latest row of the same column (horizontal
            # overlap), so columns sharing baselines are not interleaved.
            candidate=next((g for g in reversed(groups) if _overlap(g[-1],r)),None)
            new=True
            if candidate is not None:
                prev=candidate[-1]; prevparents={parent_by_id.get(i) for i in prev['glyph_ids']}
                gap=r['baseline']-prev['baseline']; size=r['size']
                if parent is not None:new=prevparents!={parent}
                else:
                    same_size=abs(size-prev['size'])<.1
                    # Continuations start at the same or a slightly smaller x;
                    # an indented new line or paragraph gap starts a paragraph.
                    new=not (same_size and 0<gap<2.25*size and -1.6*size<=r['x']-prev['x']<.2*size)
                    gaps=[b['baseline']-a['baseline'] for a,b in zip(candidate,candidate[1:])]
                    # A gap clearly wider than the group's established line pitch,
                    # or a short line whose successor's first word would have fit,
                    # ends the paragraph.
                    if not new and gaps and gap>1.3*median(gaps):new=True
                    if not new and _first_word_fits(c,prev,r,max(x['pen_end'] for x in candidate+[r])):new=True
            if new:groups.append([r])
            else:candidate.append(r)
        # Approved conditions stored by an earlier save name their paragraph by
        # its exact drawn lines. A verified record defines the paragraph rows.
        records,record_status=conditions.read(c,page) if stored else ([],'not consulted')
        owner={}
        for n,record in enumerate(records):
            found=conditions.match(c,record,rows)
            if found is None:continue
            ids={id(r) for r in found};position=min(k for k,g in enumerate(groups) if any(id(r) in ids for r in g))
            groups=[[r for r in g if id(r) not in ids] for g in groups]
            groups.insert(position,sorted(found,key=lambda r:r['baseline']));groups=[g for g in groups if g]
            owner[n]=ids
        def record_of(group):return next((n for n,ids in owner.items() if {id(r) for r in group}==ids),None)
        texts=[group_text(c,g)[0] for g in groups]
        matches=[]
        for n,t in enumerate(texts):
            at=0
            while selection and (pos:=t.find(selection,at))>=0:matches.append((n,pos));at=pos+1
        if not isinstance(occurrence,int) or occurrence<0 or occurrence>=len(matches):raise Refused('selected string occurrence not found in one inferred paragraph')
        target,start=matches[occurrence]; selected=groups[target]; size=median(r['size'] for r in selected)
        left=min(r['x'] for r in selected); first=selected[0]['x']
        # A single-line paragraph borrows the observed continuation edge from
        # nearby same-size body rows, never from a manifest.
        nearby=[r for r in rows if abs(r['size']-size)<.1 and abs(r['baseline']-selected[0]['baseline'])<8*size and first-1.6*size<=r['x']<=first+.1 and _overlap(r,selected[0])]
        left=min([left]+[r['x'] for r in nearby]); followers=[];follower_records=[]
        previous=selected;column=dict(x=min(r['x'] for r in selected),pen_end=max(r['pen_end'] for r in selected))
        for group in groups[target+1:]:
            r=group[0]
            if not _overlap(r,column):continue
            gap=r['baseline']-previous[-1]['baseline']
            if abs(r['size']-size)>.1 or abs(r['x']-first)>.2*size or not 0<gap<2.8*size:break
            followers.append(group);follower_records.append(record_of(group));previous=group
        # The target must qualify. A follower that cannot be qualified (and every
        # later one) stays protected foreign content; it bounds the capacity.
        target_paragraph=qualify(c,marks,[i for r in selected for i in r['glyph_ids']],selected)
        follower_paragraphs=[];unmovable=None
        for group in followers:
            try:follower_paragraphs.append(qualify(c,marks,[i for r in group for i in r['glyph_ids']],group))
            except Refused as exc:unmovable=str(exc);break
        followers=followers[:len(follower_paragraphs)];follower_records=follower_records[:len(follower_paragraphs)]
        allgroups=[selected]+followers; allrows=[r for g in allgroups for r in g]
        deltas=[b['baseline']-a['baseline'] for g in allgroups for a,b in zip(g,g[1:])]
        leading=median(deltas) if deltas else min([b['baseline']-a['baseline'] for a,b in zip(allrows,allrows[1:])] or [size*1.5])
        right=max(r['pen_end'] for r in allrows)
        top=min(min(r['top'] for r in selected)-.5,selected[0]['baseline']-max(r['size'] for r in selected)-.5)
        ids={i for r in allrows for i in r['glyph_ids']}; last=max(r['bottom'] for r in allrows)
        obstacles=[dict(o) for o in analysis.obstacles]
        for link in c.page.get_links():obstacles.append(dict(kind='annotation',bounds=list(link['from'])))
        for annot in (c.page.annots() or []):obstacles.append(dict(kind='annotation',bounds=list(annot.rect)))
        # Paint that already contains every original line of these paragraphs
        # (page background, a panel or an image behind the text) is the
        # paragraphs' container: new ink may overlap it but must stay inside it.
        containers=[]
        for o in obstacles:
            if o['kind'] in ('filled-vector','image') and all(pymupdf.Rect(o['bounds']).contains(pymupdf.Rect(r['bbox'])) for r in allrows):
                o['kind']='container';containers.append(o['bounds'])
        obstacles.extend(dict(kind='foreign-text',bounds=list(g['bbox'])) for i,g in enumerate(c.actual) if i not in ids and g['unicode'].strip())
        basis='median within-paragraph observed gaps' if deltas else 'peer paragraph gap' if len(allrows)>1 else 'unobserved: 1.5 em default'
        provenance=dict(left='observed: minimum continuation x' if len(selected)>1 else 'inferred: nearby same-size continuation edge' if nearby else 'observed: first-line x',
                        right='inferred: maximum occupied pen extent',first_x='observed: first line x',line_height=basis,
                        bottom='inferred: next protected paint, annotation or page edge')
        state=dict(status=record_status if not records else 'no record for this paragraph',records=records,target=None,followers=follower_records,applied=False,stale=[])
        n=record_of(selected)
        if n is None:
            state['stale']=[k for k,record in enumerate(records) if conditions.overlaps(record,selected)]
            if state['stale']:state['status']='stale: the drawn paragraph differs from the stored approved record'
        else:
            record=records[n];state['target']=n
            hits=conditions.intrusions(record,selected,obstacles)
            if hits:state.update(status='stale: protected paint entered the approved width ('+', '.join(sorted(set(hits)))+')',stale=[n],target=None)
            else:
                approved=record['conditions'];left,right,leading=approved['left'],approved['right'],approved['line_height']
                basis='approved condition stored in the PDF (first approved as: '+record['origin']['provenance']['line_height']+')'
                provenance={k:'approved: stored record verified against the drawn paragraph' for k in ('left','right','line_height')}|dict(first_x='observed: first line x',bottom='min(stored approved bottom, current protected paint)')
                state.update(status='applied',applied=True)
        below=[o['bounds'][1] for o in obstacles if o['kind']!='container' and o['bounds'][1]>last+.1 and o['bounds'][0]<right and o['bounds'][2]>left]
        bottom=min(below+[c.page.rect.height])-1
        if state['applied']:bottom=min(bottom,records[state['target']]['conditions']['bottom'])
        for box in containers:bottom=min(bottom,box[3])
        # Existing paints intersecting a proposed frame remain protected; the
        # writer checks actual new ink, not just this envelope.
        frame=[left-.5,top,right+.5,bottom]
        for box in containers:frame=[max(frame[0],box[0]),max(frame[1],box[1]),min(frame[2],box[2]),min(frame[3],box[3])]
        paragraphs=[target_paragraph]+follower_paragraphs
        values=dict(left=left,right=right,first_x=first,line_height=leading,bottom=bottom)
        origin=records[state['target']]['origin'] if state['applied'] else dict(values=values,provenance=provenance)
        evidence=dict(observed=dict(lines=[{k:r[k] for k in ('id','text','glyph_ids','x','pen_end','baseline','size','bbox')} for r in allrows],semantic_parents=[p.parent for p in paragraphs],obstacles=obstacles),
            inferred=dict(frame=frame,left=left,right=right,first_x=first,baseline=selected[0]['baseline'],line_height=leading,leading_basis=basis,paragraph_gap=[g[0]['baseline']-h[-1]['baseline'] for h,g in zip(allgroups,allgroups[1:])],baseline_policy='existing target line baselines are observed and retained; added lines use the approved or inferred leading'),
            conditions=dict(status=state['status'],applied=state['applied'],provenance=provenance,origin=origin,
                            approval_count=records[state['target']]['approval']['count'] if state['applied'] else 0,
                            stale_records=len(state['stale']),containers=containers,implicit_line_join_spaces=len(paragraphs[0].joins),line_end_hyphens=len(paragraphs[0].hyphens)),
            confidence=dict(paragraph='approved: stored record matched every drawn line' if state['applied'] else 'high: ordered P children' if paragraphs[0].parent else 'medium: indentation, spacing and style continuity',
                            right='approved: stored condition' if state['applied'] else 'medium: maximum occupied pen extent; author frame absent',bottom='medium: next protected paint/annotation or page edge',top='medium: observed line boxes plus one-em ascender allowance; staged ink and raster checked'),
            selection_match_count=len(matches),warnings=['Occupied width is an inferred capacity, not a stored author frame.','Following same-column paragraphs may translate; protected content never moves.'],manual_geometry_inputs=0)
        if not deltas and not state['applied']:evidence['warnings'].append('No within-paragraph leading is observable; approve the reported inferred/default leading.')
        if state['status'].startswith('stale'):evidence['warnings'].append('Stored approved conditions were not applied: '+state['status'])
        if record_status.startswith(('unreadable','page modified')) or 'malformed' in record_status:evidence['warnings'].append('Stored conditions: '+record_status)
        if unmovable:evidence['warnings'].append('Following paragraph stays fixed and bounds the capacity: '+unmovable)
        if paragraphs[0].hyphens:evidence['warnings'].append('Line-final hyphens are kept as drawn; reflow that moves them is refused.')
        if len(matches)>1:evidence['warnings'].append('Multiple string matches exist; approve the displayed occurrence and paragraph explicitly.')
        # A space-separated (non-CJK) paragraph whose non-final lines all end at
        # the right edge is justified; reflow keeps that alignment. CJK text
        # keeps its observed per-character tracking instead.
        justified=len(selected)>1 and ' ' in paragraphs[0].text and all(ord(ch)<0x2E80 for ch in paragraphs[0].text) and all(abs(r['pen_end']-max(x['pen_end'] for x in selected))<.75 for r in selected[:-1])
        flush=len(selected)>1 and all(abs(r['pen_end']-max(x['pen_end'] for x in selected))<.75 for r in selected[:-1])
        cjk=flush and not justified and any(ord(ch)>=0x2E80 for ch in paragraphs[0].text)
        align='justify' if justified else 'justify-cjk' if cjk else 'start'
        if state['applied']:align=records[state['target']]['conditions'].get('align',align)
        evidence['inferred']['alignment']=dict(justify='justify: non-final lines end at the right edge',start='start',
            **{'justify-cjk':'justify (CJK): non-final lines end at the right edge; observed per-glyph advances are kept'})[align]
        spec=dict(page=page,frame=frame,left=left,right=right,first_x=first,align=align,baseline=selected[0]['baseline'],line_height=leading,line_offsets=[r['baseline']-selected[0]['baseline'] for r in selected],
                  stored=state,values=values,provenance=provenance,origin=origin)
        return c,paragraphs,spec,start,evidence
    except Exception:c.close();raise
