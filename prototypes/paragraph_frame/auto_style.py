"""Concrete source-code witnesses first; verified font shaping only if needed."""
from collections import Counter, defaultdict
from copy import copy
from statistics import median
import hashlib
from pathlib import Path
import re
from pdfeditor.shaped_font import ShapedFont
from .engine import Refused


def family(font):return re.sub(r'^/?[A-Z]{6}\+','',font.basefont).lstrip('/')


def source_tokens(c):
    tokens={}
    for e in c.events:
        for ch in e.chars:
            if len(ch.source_orders)!=1 or len(ch.text)!=1:continue
            i=ch.source_orders[0];g=c.actual[i]
            if g['unicode']!=ch.text or not g['gid'] and ch.text.strip():continue
            tokens[i]=dict(text=ch.text,source_id=i,code=ch.code,font=e.state.font,font_name=e.state.font.name,
                family=family(e.state.font),size=e.state.size,tz=e.state.tz,fill=e.state.fill,tc=e.state.tc,tw=e.state.tw,
                rise=e.state.ts,origin=list(g['origin']),advance=ch.advance,gid=g['gid'],bbox=list(g['bbox']),
                mode='retained-source',scope=None,event=e,tracking=0.,nominal_advance=ch.pdf_width/1000*e.state.size*e.state.tz/100)
    # Pair displacements include TJ tracking, while line-final advance remains
    # the nominal width. This avoids carrying a line reset into the next line.
    rows=defaultdict(list)
    for i,t in tokens.items():rows[round(t['origin'][1],2)].append(t)
    tracking=defaultdict(list)
    for row in rows.values():
        row.sort(key=lambda t:t['origin'][0])
        for t,n in zip(row,row[1:]):
            delta=n['origin'][0]-t['origin'][0]
            if all(t[k]==n[k] for k in ('font_name','size','tz','rise','tc','tw')) and abs(delta-t['advance'])<.25*t['size']:
                t['tracking']=delta-t['advance'];t['advance']=delta
                tracking[(t['font_name'],t['size'],t['tz'])].append(t['tracking'])
    med={k:median(v) for k,v in tracking.items()}
    return tokens,med


def build(c,paragraph,start,old,new,substitute=None):
    source,tracking=source_tokens(c)
    try:tokens=[copy(source[i]) for i in paragraph.ids]
    except KeyError:raise Refused('selected source glyph lacks a concrete code/GID witness')
    for t in tokens:
        for n,s in enumerate(paragraph.scopes):
            if s['content_byte_range'][0]<=t['event'].operator.start<s['content_byte_range'][1]:t['scope']=n;break
    # Only the changed middle needs new characters. Unchanged prefix/suffix
    # keep source code, resource, style and source occurrence evidence.
    prefix=0
    while prefix<min(len(old),len(new)) and old[prefix]==new[prefix]:prefix+=1
    suffix=0
    while suffix<min(len(old)-prefix,len(new)-prefix) and old[-suffix-1]==new[-suffix-1]:suffix+=1
    at=start+prefix; stop=start+len(old)-suffix; inserted=new[prefix:len(new)-suffix if suffix else len(new)]
    anchor_index=min(at,len(tokens)-1)
    if at>0 and not tokens[anchor_index]['text'].strip() and inserted.strip():anchor_index=at-1
    anchor=copy(tokens[anchor_index])
    if not inserted:return tokens[:at]+tokens[stop:],{},dict(Counter(t['mode'] for t in tokens[:at]+tokens[stop:]))
    fonts={};cache={};newtokens=[]
    substitute_font=None
    if substitute:
        if substitute.get('relation')!='substituted' or substitute.get('font_index',0)!=0:raise Refused('explicit substitute relation/font index required')
        path=Path(substitute['path']);raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=substitute['sha256']:raise Refused('substitute font hash mismatch')
        substitute_font=ShapedFont(raw)
    for char in inserted:
        t=copy(anchor);t.update(text=char,source_id=None,origin=None,bbox=None)
        # Never silently borrow another font resource merely because Unicode
        # happens to match. A source witness must be in the anchor resource.
        witnesses=[v for v in source.values() if v['text']==char and v['font'].xref==anchor['font'].xref]
        if witnesses:
            w=witnesses[0];t.update(code=w['code'],gid=w['gid'],mode='source-code-witness')
            nominal=anchor['font'].decode(w['code'])[0][2]/1000*anchor['size']
            t['nominal_advance']=nominal*anchor['tz']/100
            t['advance']=(nominal+anchor['tc']+(anchor['tw'] if w['code']==b' ' else 0))*anchor['tz']/100+tracking.get((anchor['font_name'],anchor['size'],anchor['tz']),0)
        else:
            xref=anchor['font'].xref
            if xref not in cache:
                try:
                    data=c.document.extract_font(xref)[3];cache[xref]=ShapedFont(data) if data else None
                except Exception:cache[xref]=None
            font=cache[xref];mode='same-program-new-code';key='source:'+str(xref)
            try:
                run=font.shape(char) if font else None
                if run is None or len(run.glyphs)!=1 or not run.glyphs[0].gid:raise ValueError('absent Unicode glyph')
            except Exception:
                if substitute_font is None:raise Refused('missing source glyph requires explicit substitute approval: '+char)
                font=substitute_font;key='substitute';mode='approved-substitute';run=font.shape(char)
            if len(run.glyphs)!=1 or not run.glyphs[0].gid:raise Refused('one shaped glyph per scalar required')
            g=run.glyphs[0];fonts[key]=font;t.update(mode=mode,font_key=key,shaped=g,gid=g.gid,code=None)
            t['nominal_advance']=g.advance*anchor['size']/font.upem*anchor['tz']/100
            t['advance']=(g.advance*anchor['size']/font.upem+anchor['tc']+(0))*anchor['tz']/100+tracking.get((anchor['font_name'],anchor['size'],anchor['tz']),0)
        newtokens.append(t)
    result=tokens[:at]+newtokens+tokens[stop:]
    return result,fonts,dict(Counter(t['mode'] for t in result))
