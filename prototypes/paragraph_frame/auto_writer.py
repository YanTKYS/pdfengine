"""Position native source codes and verified new glyphs; stage before publishing."""
from collections import Counter
import os
from pathlib import Path
import tempfile
import pymupdf
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,TextStringObject,DecodedStreamObject
from pdfeditor.content_stream import ContentPage,serialized_event
from .auto_style import source_tokens
from .audit import painting,semantic
from PIL import Image,ImageChops,ImageDraw
import math
from .engine import Refused,_check_insertion_state
from . import auto_conditions as conditions


def glyph_key(g):return (g['unicode'],g['gid'],*g['origin'],*g['bbox'])


def write(c,paragraphs,lines,fonts,spec,evidence,output,approval=None):
    if Path(output).exists():raise Refused('output already exists')
    delta=lines[-1]['baseline']-paragraphs[0].baselines[-1]
    if abs(delta)<.001:delta=0.
    active=paragraphs if delta else paragraphs[:1]
    selected={i for p in active for i in p.ids};edits=[]
    for e in c.events:
        if any(selected.intersection(ch.source_orders) for ch in e.chars):edits.append((e.operator.start,e.operator.end,serialized_event(e,selected,remove=True)))
    for p in active:
        for s in p.scopes:edits.extend([(s['begin_byte_range'][0],s['begin_byte_range'][1],b''),(s['end_byte_range'][0],s['end_byte_range'][1],b'')])
    first=min(e.operator.start for e in c.events if any(selected.intersection(ch.source_orders) for ch in e.chars))
    safe=[b for b in c.boundaries if b.operator.end<first and not b.text_object and not b.marked_content_depth and not b.pending_path and not b.pending_clip and b.state.ctm==(1,0,0,1,0,0)]
    insertion=0
    if safe:_check_insertion_state(safe[-1],spec,c.page.rect.height);insertion=safe[-1].operator.end
    if any(a<insertion<b for a,b,_ in edits):raise Refused('insertion intersects removal')
    writer=PdfWriter(clone_from=c.reader);page=writer.pages[spec['page']-1];lookup=writer._id_translated[id(c.reader)]
    resources=DictionaryObject(dict(page['/Resources']));fontdict=DictionaryObject(dict(resources['/Font']));built={}
    for key,font in fonts.items():
        name='/PFAuto'+str(len(fontdict));
        while name in fontdict:name+='x'
        runs=[font.shape(t['text']) for l in lines for t in l['tokens'] if t.get('font_key')==key]
        resource=font.resource(runs);fontdict[NameObject(name)]=resource.build(writer);built[key]=(name,resource)
    resources[NameObject('/Font')]=fontdict;page[NameObject('/Resources')]=resources
    source,_=source_tokens(c);batches=[(paragraphs[0],[t for l in lines for t in l['tokens']])]
    for p in active[1:]:
        placed=[]
        for i in p.ids:
            t=dict(source[i],x=source[i]['origin'][0],y=source[i]['origin'][1]+delta)
            for n,s in enumerate(p.scopes):
                if s['content_byte_range'][0]<=t['event'].operator.start<s['content_byte_range'][1]:t['scope']=n;break
            placed.append(t)
        batches.append((p,placed))
    data=c.streams[-c.page.xref];ops=[]
    for p,tokens in batches:
        scopegroups=[(n,[t for t in tokens if t['scope']==n]) for n in range(len(p.scopes))] if p.scopes else [(None,tokens)]
        if ''.join(t['text'] for _,ts in scopegroups for t in ts)!=''.join(t['text'] for t in tokens):raise Refused('replacement crosses incompatible scope reading order')
        for n,ts in scopegroups:
            if n is not None:
                s=p.scopes[n];a,b=s['begin_byte_range'];ops.append(data[a:b]+b'\n')
                owner=c.reader.get_object(s['association']['owner_reference']['xref'])
                if '/ActualText' in owner:
                    writer.get_object(lookup[s['association']['owner_reference']['xref']])[NameObject('/ActualText')]=TextStringObject(''.join(t['text'] for t in ts))
            ops.append(b'q\nBT\n0 Tc 0 Tw 0 Ts 0 Tr\n')
            for t in ts:
                name=t['font_name'];code=t['code'];x=t['x'];y=t['y']
                if t.get('font_key'):
                    name,res=built[t['font_key']];g=t['shaped'];font=fonts[t['font_key']];code=res.code(g)
                    x+=g.x_offset*t['size']/font.upem*t['tz']/100;y-=g.y_offset*t['size']/font.upem
                color=' '.join(str(float(v)) for v in t['fill'][1])+' '+t['fill'][0]
                ops.append(f'{color}\n{name} {t["size"]:.9f} Tf {t["tz"]:.9f} Tz 1 0 0 1 {x:.9f} {c.page.rect.height-y:.9f} Tm <{code.hex()}> Tj\n'.encode())
            ops.append(b'ET\nQ\n')
            if n is not None:ops.append(b'EMC\n')
    edits.append((insertion,insertion,b'\n'+b''.join(ops)))
    for a,b,replacement in sorted(edits,reverse=True):data=data[:a]+replacement+data[b:]
    stream=DecodedStreamObject();stream.set_data(data);page[NameObject('/Contents')]=writer._add_object(stream.flate_encode())
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(suffix='.pdf',dir=output.parent);os.close(fd)
    fd,draft=tempfile.mkstemp(suffix='.pdf',dir=output.parent);os.close(fd)
    expected=''.join(t['text'] for _,ts in batches for t in ts)
    try:
        writer.write(draft)
        stored=store_conditions(c,draft,writer,page,spec,selected,len(batches[0][1]),delta,active,approval)
        writer.write(tmp)
        validate_stage(c,tmp,spec,evidence,selected,expected)
        if stored['record'] is not None:verify_conditions(tmp,spec['page'])
        os.replace(tmp,output)
    finally:
        for path in (tmp,draft):
            if os.path.exists(path):os.unlink(path)
    return dict(stored_conditions=stored['summary'],follower_translation=delta,followers_repainted=len(active)-1,source_glyphs_removed=len(selected),scope_policy='original ordered scopes and ParentTree retained',stage_checks=['protected glyph identity','exact drawn text','inferred frame bounds','protected paint collision','page count','font programs','protected non-text paint','semantic consistency','MuPDF inferred-frame outside pixels'])


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
        frame=pymupdf.Rect(spec['frame'])
        for g in added:
            if not g['unicode'].strip():continue
            box=pymupdf.Rect(g['bbox'])
            if not frame.contains(box):raise Refused('capacity: staged glyph bounds outside approved inferred frame')
            for o in evidence['observed']['obstacles']:
                # Target/follower glyphs were excluded at detection. A container
                # (background behind every original line) bounds the frame instead.
                if o['kind']=='container':continue
                overlap=box&pymupdf.Rect(o['bounds'])
                if not overlap.is_empty and overlap.width>.05 and overlap.height>.05:raise Refused('staged glyph collides with protected '+o['kind'])
        original_paint=painting(c.source,spec['page']);staged_paint=painting(tmp,spec['page'])
        if any(original_paint[k]!=staged_paint[k] for k in ('drawings','images','links','annots')):raise Refused('staged protected non-text paint changed')
        if any(staged_paint['fonts'].get(k)!=v for k,v in original_paint['fonts'].items()):raise Refused('staged original font program changed')
        if not all(semantic(tmp,spec['page'])['checks'].values()):raise Refused('staged semantic structure/ActualText inconsistency')
        if any(not g['gid'] for g in added if g['unicode'].strip()):raise Refused('staged missing glyph')
        # A cheap publication gate complements later all-page, two-renderer
        # oracle audit. Mask only the approved automatically inferred frame.
        a=c.page.get_pixmap(dpi=144,alpha=False);b=check.page.get_pixmap(dpi=144,alpha=False)
        ia=Image.frombytes('RGB',(a.width,a.height),a.samples);ib=Image.frombytes('RGB',(b.width,b.height),b.samples)
        diff=ImageChops.difference(ia,ib);x0,y0,x1,y1=spec['frame']
        ImageDraw.Draw(diff).rectangle((math.floor(x0*2),math.floor(y0*2),math.ceil(x1*2)-1,math.ceil(y1*2)-1),fill=(0,0,0))
        if diff.getbbox():raise Refused('staged raster changes outside approved inferred frame')
        # Native retained font resources/programs are cloned, never replaced.
        if len(check.document)!=len(c.document):raise Refused('page count changed')
    finally:check.close()


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


def verify_conditions(path,page):
    """The stored record must be readable and match the staged paragraph again."""
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import clean_rows
    check=ContentPage(path,page)
    try:
        records,status=conditions.read(check,page)
        rows=clean_rows(check,[dict(l) for l in _Analysis(path,page).lines if l['text'].strip()])
        if status!='present' or conditions.match(check,records[-1],rows) is None:
            raise Refused('stored approved conditions do not round-trip in the staged PDF')
    finally:check.close()
