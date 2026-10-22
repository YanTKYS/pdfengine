"""Position native source codes and verified new glyphs; stage before publishing."""
from collections import Counter
from statistics import median
import os
from pathlib import Path
import subprocess
import tempfile
import pymupdf
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,TextStringObject,DecodedStreamObject
from pdfeditor.content_stream import ContentPage,number,serialized_event
from .auto_style import source_tokens
from .audit import painting,semantic
from PIL import Image,ImageChops,ImageDraw
import math
from .engine import Refused
from . import form_write
from . import auto_conditions as conditions


def glyph_key(g):return (g['unicode'],g['gid'],*g['origin'],*g['bbox'])


def write(c,paragraphs,lines,fonts,spec,evidence,output,approval=None,moves=None):
    """``moves`` (column flow) gives each following paragraph's glyph translations;
    without it, B2 translates all following paragraphs rigidly when the edited
    paragraph's last baseline changes."""
    if Path(output).exists():raise Refused('output already exists')
    delta=lines[-1]['baseline']-paragraphs[0].baselines[-1]
    if abs(delta)<.001:delta=0.
    if moves is None:
        active=paragraphs if delta else paragraphs[:1]
        moves=[{i:(0.,delta) for i in p.ids} for p in active[1:]]
    else:
        active=paragraphs[:1+len(moves)]
    selected={i for p in active for i in p.ids}
    removed=[e for e in c.events if any(selected.intersection(ch.source_orders) for ch in e.chars)]
    # The edited text is rewritten in the program that draws it: the page or
    # one Form XObject invocation (copy-on-write, see form_write).
    stream,invocation=form_write.drawing_site(c,removed)
    if invocation and any(p.scopes for p in active):raise Refused('marked content inside a Form XObject requires a semantic adapter')
    edits=[(e.operator.start,e.operator.end,serialized_event(e,selected,remove=True)) for e in removed]
    for p in active:
        for s in p.scopes:edits.extend([(s['begin_byte_range'][0],s['begin_byte_range'][1],b''),(s['end_byte_range'][0],s['end_byte_range'][1],b'')])
    first=min(e.operator.start for e in removed)
    containers=active[0].containers
    if any([x['id'] for x in p.containers]!=[x['id'] for x in containers] for p in active):raise Refused('edited paragraphs lie in different marked-content containers')
    boundaries=c.stream_boundaries[(stream,invocation)];program=c.streams[stream];last=max(e.operator.end for e in removed)
    starts={e.operator.start for e in removed}
    # Keep content (reading) order: before the edited text if no other text
    # lies in between, else right after its text object, else spliced into
    # the text object right before the edited text. When the first edited
    # operator keeps glyphs drawn before the edit (and the last none after
    # it), the place after its text object comes first.
    safe=form_write.safe_boundaries(boundaries,first,containers)
    kept=lambda e:[bool(selected.intersection(ch.source_orders)) for ch in e.chars]
    head,tail=kept(min(removed,key=lambda e:e.operator.start)),kept(max(removed,key=lambda e:e.operator.start))
    lead=not head[0];trail=not tail[-1]
    before=safe[-1] if safe and not form_write.foreign_text_between(program,safe[-1].operator.end,first,starts) else None
    after=form_write.after_text(boundaries,program,first,last,containers)
    if after is not None and form_write.foreign_text_between(program,last,after.operator.end,starts):after=None
    choice=(after or before) if lead and not trail else (before or after)
    restore=b''
    if choice is None and not any(p.scopes for p in active):
        spliced=form_write.inside_text(boundaries,program,min(removed,key=lambda e:e.operator.start),containers)
        if spliced:choice,restore=spliced
    if choice is None and safe:choice=safe[-1]
    if choice is None:raise Refused('no safe insertion point before the edited text')
    # New drawing is placed in default user space under the inverse CTM.
    prefix=form_write.insertion(choice,spec.get('frames',[spec['frame']]),c.page.rect.height);insertion=choice.operator.end
    if any(a<insertion<b for a,b,_ in edits):raise Refused('insertion intersects removal')
    writer=PdfWriter(clone_from=c.reader);page=writer.pages[spec['page']-1];lookup=writer._id_translated[id(c.reader)]
    taken=set(form_write.program_fonts(c,invocation));built={};new_fonts={}
    for key,font in fonts.items():
        name='/PFAuto'+str(len(taken));
        while name in taken:name+='x'
        taken.add(name)
        runs=[font.shape(t['text']) for l in lines for t in l['tokens'] if t.get('font_key')==key]
        resource=font.resource(runs);new_fonts[name]=resource.build(writer);built[key]=(name,resource)
    source,_=source_tokens(c);batches=[(paragraphs[0],[t for l in lines for t in l['tokens']])]
    for p,move in zip(active[1:],moves):
        placed=[]
        for i in p.ids:
            dx,dy=move[i]
            t=dict(source[i],x=source[i]['origin'][0]+dx,y=source[i]['origin'][1]+dy)
            for n,s in enumerate(p.scopes):
                if s['content_byte_range'][0]<=t['event'].operator.start<s['content_byte_range'][1]:t['scope']=n;break
            placed.append(t)
        batches.append((p,placed))
    data=c.streams[stream];ops=[]
    # Laid-out positions come from rendered (float32) glyph origins; where they
    # are the source's own coordinates, write the exact source values, so that
    # renderers order lines and words as in the source.
    pool=([],[])
    for e in removed:
        A,B,C,D,E,F=e.state.ctm
        for ch in e.chars:
            if ch.source_text_matrix:
                a,b,c_,d,e_,f=ch.source_text_matrix;u,v=e.state.ts*c_+e_,e.state.ts*d+f
                pool[0].append(u*A+v*C+E);pool[1].append(u*B+v*D+F)
    def exact(value,axis):
        best=min(pool[axis],key=lambda p:abs(p-value),default=value)
        return best if abs(best-value)<1e-3 else value
    for p,tokens in batches:
        scopegroups=[(n,[t for t in tokens if t['scope']==n]) for n in range(len(p.scopes))] if p.scopes else [(None,tokens)]
        if ''.join(t['text'] for _,ts in scopegroups for t in ts)!=''.join(t['text'] for t in tokens):raise Refused('replacement crosses incompatible scope reading order')
        for n,ts in scopegroups:
            if n is not None:
                s=p.scopes[n];a,b=s['begin_byte_range'];ops.append(data[a:b]+b'\n')
                owner=c.reader.get_object(s['association']['owner_reference']['xref'])
                if '/ActualText' in owner:
                    writer.get_object(lookup[s['association']['owner_reference']['xref']])[NameObject('/ActualText')]=TextStringObject(''.join(t['text'] for t in ts))
            ops.append(b'q\n'+prefix+b'BT\n0 Tc 0 Tw 0 Ts 0 Tr\n')
            run=None
            for t in ts:
                name=t['font_name'];code=t['code'];x=t['x'];y=t['y']
                if t.get('font_key'):
                    name,res=built[t['font_key']];g=t['shaped'];font=fonts[t['font_key']];code=res.code(g)
                    x+=g.x_offset*t['size']/font.upem*t['tz']/100;y-=g.y_offset*t['size']/font.upem
                color=' '.join(str(float(v)) for v in t['fill'][1])+' '+t['fill'][0]
                if t['fill'][0] in ('sc','scn'):color=t['fill_space']+' cs '+color
                # Consecutive glyphs of one original font and style on one
                # baseline form one TJ, kerned to the laid-out pen positions,
                # like the source: text extraction joins them as the source.
                # Kerns are rounded to 1/100 000 em (float noise would be read
                # as word breaks); the pen follows the written kerns.
                key=None if t.get('font_key') else (name,t['size'],t['tz'],color,y)
                if key is not None and run is not None and run[0]==key:
                    unit=t['size']*t['tz']/100000;kern=round((run[1]-x)/unit,2)
                    ops[-1]=ops[-1][:-4]+(b' '+number(kern)+b' ' if kern else b'')+f'<{code.hex()}>]TJ\n'.encode()
                    run=(key,run[1]-kern*unit+t['nominal_advance'])
                    continue
                x=exact(x,0)
                ops.append(f'{color}\n{name} {t["size"]:.9f} Tf {t["tz"]:.9f} Tz 1 0 0 1 {x:.9f} {exact(c.page.rect.height-y,1):.9f} Tm [<{code.hex()}>]TJ\n'.encode())
                run=None if key is None else (key,x+t['nominal_advance'])
            ops.append(b'ET\nQ\n')
            if n is not None:ops.append(b'EMC\n')
    edits.append((insertion,insertion,form_write.wrap(choice,b''.join(ops),restore)))
    for a,b,replacement in sorted(edits,reverse=True):data=data[:a]+replacement+data[b:]
    clones=form_write.copy_on_write(writer,page,c,stream,invocation,data,new_fonts)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(suffix='.pdf',dir=output.parent);os.close(fd)
    fd,draft=tempfile.mkstemp(suffix='.pdf',dir=output.parent);os.close(fd)
    expected=''.join(t['text'] for _,ts in batches for t in ts)
    try:
        writer.write(draft)
        if spec.get('mode')=='flow':stored=store_flow(c,draft,writer,page,spec,selected,[len(ts) for _,ts in batches],approval)
        else:stored=store_conditions(c,draft,writer,page,spec,selected,len(batches[0][1]),delta,active,approval)
        writer.write(tmp)
        validate_stage(c,tmp,spec,evidence,selected,expected)
        form_write.unchanged_elsewhere(c.source,tmp,spec['page'])
        if stored['record'] is not None:verify_conditions(tmp,spec['page'],stored['record'])
        os.replace(tmp,output)
    finally:
        for path in (tmp,draft):
            if os.path.exists(path):os.unlink(path)
    return dict(drawn_in='page' if not invocation else 'Form XObject (copy-on-write)',form_clones=clones,insertion_ctm=list(choice.state.ctm),insertion_inside_text_object=choice.text_object,stored_conditions=stored['summary'],follower_translation=delta if spec.get('mode')!='flow' else 'per line (column flow)',followers_repainted=len(active)-1,source_glyphs_removed=len(selected),scope_policy='original ordered scopes and ParentTree retained',stage_checks=['protected glyph identity','exact drawn text','inferred frame bounds','protected paint collision','page count','font programs','protected non-text paint','semantic consistency','MuPDF and Poppler inferred-frame outside pixels'])


def _covered(rect,boxes):
    """Whether the corners and centre of ``rect`` lie in the given boxes."""
    points=[pymupdf.Point(x,y) for x in (rect.x0+.01,rect.x1-.01) for y in (rect.y0+.01,rect.y1-.01)]+[(rect.tl+rect.br)/2]
    return all(any(b.contains(p) for b in boxes) for p in points)


def validate_stage(c,tmp,spec,evidence,selected,expected):
    """Read-only publication checks, also callable on existing saved candidates."""
    check=ContentPage(tmp,spec['page'])
    try:
        if check.errors:raise Refused('staged source interpretation failed')
        foreign=Counter(glyph_key(g) for i,g in enumerate(c.actual) if i not in selected);remain=Counter(foreign);added=[]
        for g in check.actual:
            key=glyph_key(g)
            if remain[key]:remain[key]-=1
            else:added.append(g)
        if +remain:raise Refused('staged protected text changed')
        def styled(content,exclude=None):
            result=Counter()
            for event in content.events:
                for ch in event.chars:
                    for i in ch.source_orders:
                        if exclude and i in exclude:continue
                        state=event.state
                        result[(glyph_key(content.actual[i]),ch.code,state.font.name,state.size,state.tz,state.fill,state.tr,state.opacity)]+=1
            return result
        if styled(c,selected)-styled(check):raise Refused('staged protected source code/style changed')
        if ''.join(g['unicode'] for g in added)!=expected:raise Refused('staged drawing text differs from requested paragraphs')
        old_boxes=[pymupdf.Rect(c.actual[i]['bbox'])+(-.3,-.3,.3,.3) for i in selected if c.actual[i]['unicode'].strip()]
        frames=[pymupdf.Rect(f) for f in spec.get('frames',[spec['frame']])]
        for g in added:
            if not g['unicode'].strip():continue
            box=pymupdf.Rect(g['bbox'])
            if not any(frame.contains(box) for frame in frames):raise Refused('capacity: staged glyph bounds outside approved inferred frame')
            for o in evidence['observed']['obstacles']:
                # Target/follower glyphs were excluded at detection. A container
                # (background behind every original line) bounds the frame instead.
                if o['kind']=='container':continue
                overlap=box&pymupdf.Rect(o['bounds'])
                # New ink may overlap protected paint only where the replaced
                # glyphs already did (text on part of a band or label).
                if not overlap.is_empty and overlap.width>.05 and overlap.height>.05 and not _covered(overlap,old_boxes):
                    raise Refused('staged glyph collides with protected '+o['kind'])
        original_paint=painting(c.source,spec['page']);staged_paint=painting(tmp,spec['page'])
        if any(original_paint[k]!=staged_paint[k] for k in ('drawings','images','links','annots')):raise Refused('staged protected non-text paint changed')
        if any(staged_paint['fonts'].get(k)!=v for k,v in original_paint['fonts'].items()):raise Refused('staged original font program changed')
        if not all(semantic(tmp,spec['page'])['checks'].values()):raise Refused('staged semantic structure/ActualText inconsistency')
        if any(not g['gid'] for g in added if g['unicode'].strip()):raise Refused('staged missing glyph')
        # A cheap publication gate complements later all-page, two-renderer
        # oracle audit. Mask only the approved automatically inferred frame.
        a=c.page.get_pixmap(dpi=144,alpha=False);b=check.page.get_pixmap(dpi=144,alpha=False)
        ia=Image.frombytes('RGB',(a.width,a.height),a.samples);ib=Image.frombytes('RGB',(b.width,b.height),b.samples)
        # Poppler too: its text positioning arithmetic differs from MuPDF's.
        for renderer,(ia,ib) in (('MuPDF',(ia,ib)),('Poppler',(_poppler(c.source,spec['page']),_poppler(tmp,spec['page'])))):
            diff=ImageChops.difference(ia,ib)
            for x0,y0,x1,y1 in spec.get('frames',[spec['frame']]):
                ImageDraw.Draw(diff).rectangle((math.floor(x0*2),math.floor(y0*2),math.ceil(x1*2)-1,math.ceil(y1*2)-1),fill=(0,0,0))
            if diff.getbbox():raise Refused(f'staged raster changes outside approved inferred frame ({renderer})')
        # Native retained font resources/programs are cloned, never replaced.
        if len(check.document)!=len(c.document):raise Refused('page count changed')
    finally:check.close()


def _poppler(path,page):
    """One page rendered by Poppler at 144 dpi (RGB)."""
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-r','144','-png','-singlefile',str(path),folder+'/page'],capture_output=True,check=True)
        with Image.open(folder+'/page.png') as image:return image.convert('RGB')


def store_conditions(c,draft,writer,page,spec,selected,target_count,delta,active,approval):
    """Record the approved conditions of the edited paragraph by its drawn lines.

    The lines are observed in the staged PDF itself, with the same analysis the
    next proposal uses. If they cannot be separated from foreign glyphs, no
    record is stored and the next edit falls back to inference.
    """
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import clean_rows
    state=spec['stored'];records=[r for n,r in enumerate(state['records']) if n!=state['target'] and n not in state['stale']]
    if delta:
        moved=[n for n,p in zip(state['followers'],active[1:]) if n is not None]
        records=[r for n,r in enumerate(state['records']) if n!=state['target'] and n not in state['stale'] and n not in moved]
        records+=[conditions.shifted(state['records'][n],delta) for n in moved]
    check=ContentPage(draft,spec['page'])
    try:
        foreign=Counter(glyph_key(g) for i,g in enumerate(c.actual) if i not in selected);added=[]
        for n,g in enumerate(check.actual):
            key=glyph_key(g)
            if foreign[key]:foreign[key]-=1
            else:added.append(n)
        target=set(added[:target_count])
        rows=clean_rows(check,[dict(l) for l in _Analysis(draft,spec['page']).lines if l['text'].strip()])
        mine=[r for r in rows if set(r['glyph_ids'])<=target]
        covered={i for r in mine for i in r['glyph_ids']}
        inked={i for i in target if check.actual[i]['unicode'].strip()}
        record=None
        if mine and inked<=covered and all(not set(r['glyph_ids'])&target for r in rows if r not in mine):
            values=spec['values'];previous=state['records'][state['target']] if state['applied'] else None
            record=conditions.make(check,mine,conditions=dict(values,frame=spec['frame']),provenance=spec['provenance'],origin=spec['origin'],
                                   approval=dict(count=(previous['approval']['count'] if previous else 0)+1,**(approval or {})))
            records.append(record)
        if records or state['records']:conditions.attach(writer,page,records,spec['page'])
        summary=dict(written=record is not None,records_on_page=len(records),applied_before_edit=state['applied'],status_before_edit=state['status'],
                     reason=None if record is not None else 'edited paragraph rows are not separable from foreign glyphs')
        return dict(record=record,summary=summary)
    finally:check.close()


def verify_conditions(path,page,record=None):
    """The stored record must be readable and match the staged paragraph again."""
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import clean_rows
    check=ContentPage(path,page)
    try:
        records,status=conditions.read(check,page)
        rows=clean_rows(check,[dict(l) for l in _Analysis(path,page).lines if l['text'].strip()])
        mine=[r for r in records if r==record] if record is not None else records[-1:]
        if status!='present' or len(mine)!=1 or conditions.match(check,mine[0],rows) is None:
            raise Refused('stored approved conditions do not round-trip in the staged PDF')
    finally:check.close()


def store_flow(c,draft,writer,page,spec,selected,counts,approval):
    """Record the approved column flow by every drawn line of it after the edit.

    Unchanged paragraphs of the flow are found by their unchanged glyphs; the
    repainted ones by paint order. Records of single paragraphs inside the
    flow are replaced by the flow record.
    """
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import clean_rows
    state=spec['stored'];flow=spec['flow']
    flow_ids={i for g in flow['paragraphs'] for r in g for i in r['glyph_ids']}
    check=ContentPage(draft,spec['page'])
    try:
        # Unchanged glyphs keep their identity; map them to their paragraph.
        index={}
        for p,g in enumerate(flow['paragraphs']):
            for r in g:
                for i in r['glyph_ids']:
                    if i not in selected:index[glyph_key(c.actual[i])]=p
        foreign=Counter(glyph_key(g) for i,g in enumerate(c.actual) if i not in selected);owner={};added=[]
        for n,g in enumerate(check.actual):
            key=glyph_key(g)
            if foreign[key]:
                foreign[key]-=1
                if key in index:owner[n]=index[key]
            else:added.append(n)
        position=0
        for k,count in enumerate(counts):
            for n in added[position:position+count]:owner[n]=flow['target']+k
            position+=count
        rows=clean_rows(check,[dict(l) for l in _Analysis(draft,spec['page']).lines if l['text'].strip()])
        mine=[];separable=True
        for r in rows:
            owners={owner.get(i) for i in r['glyph_ids']}
            if owners=={None}:continue
            if None in owners or len(owners)!=1:separable=False;break
            mine.append((r,owners.pop()))
        covered={i for r,_ in mine for i in r['glyph_ids']}
        separable=separable and all(i in covered for i in owner if check.actual[i]['unicode'].strip())
        # Reading order: paragraph order, then the region (column), then baseline.
        regions=flow['regions']
        from .auto_flow import region_of
        record=None
        if mine and separable:
            try:
                mine.sort(key=lambda m:(m[1],region_of(regions,m[0]),m[0]['baseline']))
                previous=state['records'][state['target']] if state['applied'] else None
                size=median(r['size'] for r,_ in mine)
                record=conditions.make_flow(check,mine,regions,dict(pitch=flow['pitch'],gap=flow['gap'],align=spec['align'],size=size),
                                            [dict(id=l['id'],status=l['status'],evidence=l.get('evidence')) for l in flow['links']],spec['provenance'],spec['origin'],
                                            dict(count=(previous['approval']['count'] if previous else 0)+1,**(approval or {})))
            except Refused:record=None
        records=[r for n,r in enumerate(state['records']) if n!=state['target'] and n not in state['stale'] and not (r.get('kind')!='flow' and conditions.overlaps(r,[x for g in flow['paragraphs'] for x in g]))]
        if record is not None:records.append(record)
        if records or state['records']:conditions.attach(writer,page,records,spec['page'])
        summary=dict(written=record is not None,kind='flow',records_on_page=len(records),applied_before_edit=state['applied'],status_before_edit=state['status'],
                     reason=None if record is not None else 'flow rows are not separable from foreign glyphs')
        return dict(record=record,summary=summary)
    finally:check.close()
