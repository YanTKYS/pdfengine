"""Horizontal, single-column two-paragraph PoC.

The editable unit is a logical paragraph, never a source MCID. The existing
byte-provenance interpreter and verified font subset builder are adapters.
One pypdf writer changes both content and structure; no redaction/white paint.
Unsupported painting/semantics is rejected before publication.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import pymupdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                          NameObject, NullObject, NumberObject, TextStringObject, IndirectObject)
from uniseg.linebreak import line_break_boundaries
from pdfeditor.content_stream import ContentPage, serialized_event
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.shaped_font import ShapedFont

class Refused(ValueError): pass

@dataclass
class Paragraph:
    text: str
    ids: list
    sizes: list
    baselines: list
    scopes: list
    parent: int | None
    # Text indices of implicit Latin line-join spaces (not source glyphs) and
    # source glyph IDs of line-final hyphens between Latin letters.
    joins: tuple = ()
    hyphens: tuple = ()


GEOMETRY_KEYS=('page','frame','left','right','first_x','baseline','line_height','paragraph_gap')

def geometry(spec):return {key:spec[key] for key in GEOMETRY_KEYS}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def observe(source, spec):
    """Recover paragraphs from concrete glyphs, indentation and the fixed frame.

    This path is identical for source and saved PDFs; no embedded private
    metadata, sidecar, original xrefs, owner counts or original glyph IDs.
    """
    c=ContentPage(source,spec['page'])
    try:
        if c.errors: raise Refused('incomplete source interpretation: '+str(c.errors))
        if c.page.rotation or c.page.rect.x0 or c.page.rect.y0:
            raise Refused('rotated/nonzero-origin page unsupported')
        marks=observe_marked_content(source,spec['page'])
        if not marks['complete']: raise Refused('incomplete marked-content observation')
        sizes={i:e.state.size for e in c.events for ch in e.chars for i in ch.source_orders}
        frame=pymupdf.Rect(spec['frame']); rows={}
        for i,g in enumerate(c.actual):
            if frame.contains(pymupdf.Point(g['origin'])):
                rows.setdefault(round(g['origin'][1],2),[]).append(i)
        groups=[]
        for y,ids in sorted(rows.items()):
            ids.sort(key=lambda i:c.actual[i]['origin'][0])
            if not ''.join(c.actual[i]['unicode'] for i in ids).strip(): continue
            first_ink=min(c.actual[i]['origin'][0] for i in ids if c.actual[i]['unicode'].strip())
            # Unrelated blank-line spaces can share a moved line's baseline.
            # They are retained foreign source, not part of the new paragraph.
            ids=[i for i in ids if c.actual[i]['unicode'].strip() or c.actual[i]['origin'][0]>first_ink+.001]
            x=c.actual[ids[0]]['origin'][0]
            if abs(x-spec['first_x'])<.05: groups.append([])
            if not groups: raise Refused('paragraph first-line indent cannot be inferred')
            groups[-1].append((y,ids))
        if len(groups)!=2: raise Refused('expected exactly two indented paragraphs in the approved frame')
        result=[]
        for group in groups:
            ids=[i for _,row in group for i in row]; chosen=set(ids)
            events=[e for e in c.events if any(chosen.intersection(ch.source_orders) for ch in e.chars)]
            if [i for e in events for ch in e.chars for i in ch.source_orders if i in chosen]!=ids:
                raise Refused('source paint order differs from visual paragraph order')
            if any(e.invocation for e in events): raise Refused('Form source unsupported')
            if any(e.state.tr!=0 or e.state.opacity!=1 or (e.state.fill[0] not in ('g','rg') or any(float(v)!=0 for v in e.state.fill[1])) for e in events):
                raise Refused('only opaque black fill text supported')
            scopes=[]
            for s in marks['scopes']:
                hits={i for e in c.events if s['content_byte_range'][0]<=e.operator.start<s['content_byte_range'][1]
                      for ch in e.chars for i in ch.source_orders}
                if hits & chosen:
                    if not hits<=chosen or s['invocation'] or s['enclosing_scope_ids']:
                        raise Refused('mixed/nested marked content is outside this adapter')
                    scopes.append(s)
            parent=None
            if c.reader.trailer['/Root'].get('/StructTreeRoot') and '/IDTree' in c.reader.trailer['/Root']['/StructTreeRoot']:
                raise Refused('IDTree references require a richer semantic adapter')
            if scopes:
                parents=set()
                for s in scopes:
                    a=s['association']
                    if set(s['properties'])-{'/MCID','/Lang'}:
                        raise Refused('unsupported marked-content property semantics')
                    if a['status']!='tree_backed': raise Refused('unbound marked content')
                    owner=c.reader.get_object(a['owner_reference']['xref'])
                    scope_text=''.join(ch.text for e in c.events if s['content_byte_range'][0]<=e.operator.start<s['content_byte_range'][1] for ch in e.chars)
                    if '/ActualText' in owner and str(owner['/ActualText'])!=scope_text:
                        raise Refused('source ActualText differs from drawn text')
                    if owner.get('/Lang') or (s['properties'].get('/Lang') and scope_text.strip()):
                        raise Refused('language-specific non-whitespace span unsupported')
                    if owner.get('/S')!='/Span' or set(owner)-{'/Type','/S','/P','/K','/Pg','/ActualText','/Lang'}:
                        raise Refused('unsupported leaf semantics')
                    parents.add(owner.raw_get('/P').idnum)
                if len(parents)!=1: raise Refused('selected paragraph has multiple semantic parents')
                parent=parents.pop(); pobj=c.reader.get_object(parent)
                if pobj.get('/S')!='/P' or set(pobj)-{'/Type','/S','/P','/K','/Pg','/Lang'}:
                    raise Refused('unsupported paragraph semantics')
                if [x.idnum for x in pobj['/K']]!=[s['association']['owner_reference']['xref'] for s in scopes]:
                    raise Refused('paragraph includes unselected semantic children')
            elif c.reader.trailer['/Root'].get('/StructTreeRoot'):
                raise Refused('tagged PDF has unassociated selected glyphs')
            result.append(Paragraph(''.join(c.actual[i]['unicode'] for i in ids),ids,[sizes[i] for i in ids],
                                    [y for y,_ in group],scopes,parent))
        _check_inbound(c.reader,result,spec['page'])
        return c,result
    except Exception:
        c.close(); raise


def _check_inbound(reader,paragraphs,page_number):
    targets={s['association']['owner_reference']['xref'] for p in paragraphs for s in p.scopes}
    if not targets:return
    parents={p.parent for p in paragraphs}
    tree=reader.trailer['/Root']['/StructTreeRoot']['/ParentTree']
    key=int(reader.pages[page_number-1]['/StructParents'])
    def array(node):
        node=node.get_object();nums=node.get('/Nums',[])
        for i in range(0,len(nums),2):
            if int(nums[i])==key:return nums[i+1].get_object()
        for kid in node.get('/Kids',[]):
            found=array(kid)
            if found is not None:return found
    parent_array=array(tree)
    def refs(obj,path=()):
        if isinstance(obj,IndirectObject):
            if obj.idnum in targets:yield path,obj.idnum
        elif isinstance(obj,dict):
            for k,v in obj.items():yield from refs(v,path+(str(k),))
        elif isinstance(obj,list):
            for i,v in enumerate(obj):yield from refs(v,path+(i,))
    # Inspect compressed and ordinary objects. References from parent K and
    # the page ParentTree array are the only permitted inbound references.
    for xref in sorted(set(reader.xref_objStm)|set(reader.xref.get(0,{}))):
        if not xref:continue
        obj=reader.get_object(xref)
        for path,target in refs(obj):
            if obj is parent_array and len(path)==1:continue
            if xref in parents and len(path)==2 and path[0]=='/K':continue
            raise Refused('external semantic reference to selected Span')


def _check_insertion_state(boundary,spec,height):
    state=boundary.state
    if state.opacity!=1 or state.stroke_opacity!=1 or set(state.other)-{'marked_content','w'}:
        raise Refused('unsupported inherited insertion graphics state')
    for clip in state.clip:
        path=clip['path']
        if len(path)!=1 or path[0]['operator']!='re' or path[0]['ctm']!=[1,0,0,1,0,0]:
            raise Refused('nonrectangular insertion clip')
        x,y,w,h=path[0]['args']
        rect=pymupdf.Rect(x,height-y-h,x+w,height-y)
        if not rect.contains(pymupdf.Rect(spec['frame'])):
            raise Refused('insertion clip does not contain approved frame')


def wrap(text,sizes,font,spec,baseline):
    """UAX14 line opportunities plus no-overflow glyph ink bounds; no scaling."""
    opportunities=set(line_break_boundaries(text)); opportunities.add(len(text))
    glyphs=[]
    for ch,size in zip(text,sizes):
        run=font.shape(ch)
        if len(run.glyphs)!=1: raise Refused('multi-glyph cluster unsupported')
        g=run.glyphs[0]; glyphs.append((g,size,g.advance*size/font.upem))
    lines=[]; start=0
    while start<len(text):
        x=spec['first_x'] if not lines else spec['left']; width=spec['right']-x
        total=0; stops=[]
        for i in range(start,len(text)):
            total+=glyphs[i][2]
            if total>width+.00001: break
            if i+1 in opportunities: stops.append(i+1)
        if not stops: raise Refused('capacity: no legal line break inside frame width')
        end=max(stops); placed=[]; cursor=x
        for g,size,advance in glyphs[start:end]:
            box=font.ink(g.gid)
            if box:
                scale=size/font.upem
                ink=[cursor+(box[0]+g.x_offset)*scale,baseline-(box[3]+g.y_offset)*scale,
                     cursor+(box[2]+g.x_offset)*scale,baseline-(box[1]+g.y_offset)*scale]
                if not pymupdf.Rect(spec['frame']).contains(pymupdf.Rect(ink)):
                    raise Refused('capacity: glyph ink outside pre-approved frame')
            placed.append((g,size,cursor,baseline)); cursor+=advance
        lines.append(dict(text=text[start:end],baseline=baseline,glyphs=placed)); start=end; baseline+=spec['line_height']
    return lines


def _parent_array(writer,page):
    key=int(page['/StructParents']); root=writer._root_object['/StructTreeRoot']; tree=root['/ParentTree']
    def find(node):
        node=node.get_object()
        nums=node.get('/Nums',[])
        for i in range(0,len(nums),2):
            if int(nums[i])==key: return nums[i+1].get_object()
        for child in node.get('/Kids',[]):
            found=find(child)
            if found is not None:return found
        return None
    arr=find(tree)
    if arr is None: raise Refused('missing page ParentTree')
    return arr


def _retag(writer,reader,page,paragraph,text):
    lookup=writer._id_translated[id(reader)]
    parent=writer.get_object(lookup[paragraph.parent]); refs=list(parent['/K'])
    first=refs[0]; leaf=first.get_object(); mcid=paragraph.scopes[0]['association']['mcid']
    arr=_parent_array(writer,page)
    for s in paragraph.scopes:
        arr[s['association']['mcid']]=NullObject()
    for ref in refs:
        o=ref.get_object()
        if '/ActualText' in o: del o['/ActualText']
    leaf[NameObject('/K')]=NumberObject(mcid)
    leaf[NameObject('/ActualText')]=TextStringObject(text)
    parent[NameObject('/K')]=ArrayObject([first]); arr[mcid]=first
    return mcid


def _draw(lines,font,resource,height,mcid):
    ops=[b'q\n0 g\n0 G\nBT\n0 Tc 0 Tw 100 Tz 0 Ts 0 Tr\n']
    if mcid is not None: ops.insert(0,f'/Span <</MCID {mcid}>> BDC\n'.encode())
    for line in lines:
        for g,size,x,y in line['glyphs']:
            scale=size/font.upem
            ops.append(f'/PFRebuild {size:.9f} Tf 1 0 0 1 {x+g.x_offset*scale:.9f} {height-y+g.y_offset*scale:.9f} Tm <{resource.code(g).hex()}> Tj\n'.encode())
    ops.append(b'ET\nQ\n')
    if mcid is not None:ops.append(b'EMC\n')
    return b''.join(ops)


def rewrite(source,output,spec,old,new,font_path,*,sidecar=None):
    if Path(output).exists():raise Refused('output already exists; choose a new path')
    spec=geometry(spec)
    started=time.perf_counter(); c,paragraphs=observe(source,spec)
    try:
        if sum(p.text.count(old) for p in paragraphs)!=1: raise Refused('replacement must match once in selected frame')
        if sidecar is not None:
            state=json.loads(Path(sidecar).read_text())
            if state['pdf_sha256']!=sha(source) or state['paragraphs']!=[p.text for p in paragraphs] or geometry(state['spec'])!=spec:
                raise Refused('stale or incompatible sidecar')
        observed=time.perf_counter()
        font=ShapedFont(font_path)
        planned=[]; baseline=spec['baseline']
        for p in paragraphs:
            text=p.text; sizes=p.sizes[:]
            if old in text:
                start=text.index(old); sizes[start:start+len(old)]=[sizes[start]]*len(new); text=text.replace(old,new)
            lines=wrap(text,sizes,font,spec,baseline)
            planned.append((p,text,lines)); baseline=lines[-1]['baseline']+spec['paragraph_gap']
        planned_at=time.perf_counter()
        # All selected occurrences are consumed. Numeric TJ compensation keeps
        # the state seen by every retained text-show operator unchanged.
        selected={i for p in paragraphs for i in p.ids}
        edits=[]
        for e in c.events:
            if any(selected.intersection(ch.source_orders) for ch in e.chars):
                edits.append((e.operator.start,e.operator.end,serialized_event(e,selected,remove=True)))
        for p in paragraphs:
            for s in p.scopes:
                edits.extend([(s['begin_byte_range'][0],s['begin_byte_range'][1],b''),
                              (s['end_byte_range'][0],s['end_byte_range'][1],b'')])
        first=min(e.operator.start for e in c.events if any(selected.intersection(ch.source_orders) for ch in e.chars))
        # Insert before a closed text/marked-content boundary. q/Q restores all
        # inherited painting state; only identity page coordinates are supported.
        safe=[b for b in c.boundaries if b.operator.end<first and not b.text_object and b.marked_content_depth==0
              and not b.pending_path and not b.pending_clip and b.state.ctm==(1,0,0,1,0,0)]
        if safe:
            _check_insertion_state(safe[-1],spec,c.page.rect.height)
            insertion=safe[-1].operator.end
        else:
            # The page-program start has the PDF default graphics state.
            # This also supports a tagged paragraph at the very first operator.
            insertion=0
        if any(a<insertion<b for a,b,_ in edits):raise Refused('insertion intersects removal')
        writer=PdfWriter(clone_from=c.reader); page=writer.pages[spec['page']-1]
        runs=[font.shape(ch) for _,text,_ in planned for ch in text]
        resource=font.resource(runs)
        fonts=DictionaryObject(dict(page['/Resources']['/Font']))
        if '/PFRebuild' in fonts:
            # Saved output is reconstructed again; a fresh resource name avoids
            # modifying any retained source resource, even on later edits.
            name='/PFRebuild2'; n=2
            while name in fonts:n+=1;name=f'/PFRebuild{n}'
        else:name='/PFRebuild'
        fonts[NameObject(name)]=resource.build(writer)
        resources=DictionaryObject(dict(page['/Resources']));resources[NameObject('/Font')]=fonts
        page[NameObject('/Resources')]=resources
        drawn=[]
        for p,text,lines in planned:
            mcid=_retag(writer,c.reader,page,p,text) if p.scopes else None
            drawn.append(_draw(lines,font,resource,c.page.rect.height,mcid).replace(b'/PFRebuild ',name.encode()+b' '))
        edits.append((insertion,insertion,b'\n'+b''.join(drawn)))
        data=c.streams[-c.page.xref]
        for a,b,replacement in sorted(edits,reverse=True):data=data[:a]+replacement+data[b:]
        stream=DecodedStreamObject();stream.set_data(data)
        page[NameObject('/Contents')]=writer._add_object(stream.flate_encode())
        output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
        fd,temporary=tempfile.mkstemp(suffix='.pdf',dir=output.parent);os.close(fd)
        try:
            writer.write(temporary)
            # Stage and re-open before the single-file atomic rename. Independent
            # audit remains separate; this is not a claim of audit success.
            with pymupdf.open(temporary) as doc:
                if len(doc)!=len(c.document):raise Refused('page count changed')
            recovered,again=observe(temporary,spec)
            recovered.close()
            if [p.text for p in again]!=[t for _,t,_ in planned]:raise Refused('staged paragraph readback mismatch')
            os.replace(temporary,output)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
        end=time.perf_counter()
        record=dict(pdf_sha256=sha(output),spec=spec,paragraphs=[t for _,t,_ in planned],
                    lines=[[dict(text=l['text'],baseline=l['baseline']) for l in lines] for _,_,lines in planned],
                    source_glyphs_removed=len(selected),source_show_operators_rewritten=sum(1 for e in c.events if any(selected.intersection(ch.source_orders) for ch in e.chars)),
                    font_sha256=sha(font_path),timing=dict(analysis=observed-started,layout=planned_at-observed,save_and_readback=end-planned_at,total=end-started))
        return record
    finally:c.close()


def noop(source,output):
    if Path(output).exists():raise Refused('output already exists; choose a new path')
    writer=PdfWriter(clone_from=source);writer.write(output)
