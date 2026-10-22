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


def glyph_key(g):return (g['unicode'],g['gid'],*g['origin'],*g['bbox'])


def write(c,paragraphs,lines,fonts,spec,evidence,output):
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
    try:
        writer.write(tmp)
        validate_stage(c,tmp,spec,evidence,selected,''.join(t['text'] for _,ts in batches for t in ts))
        os.replace(tmp,output)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return dict(follower_translation=delta,followers_repainted=len(active)-1,source_glyphs_removed=len(selected),scope_policy='original ordered scopes and ParentTree retained',stage_checks=['protected glyph identity','exact drawn text','inferred frame bounds','protected paint collision','page count','font programs','protected non-text paint','semantic consistency','MuPDF inferred-frame outside pixels'])


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
                if o['kind']=='foreign-text':
                    # Target/follower observations were excluded at detection.
                    pass
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
