"""Concrete source-code witnesses first; verified font shaping only if needed."""
from collections import Counter, defaultdict
from copy import copy
from statistics import median
import hashlib
from pathlib import Path
import re
from pdfeditor.shaped_font import ShapedFont
from .engine import Refused
from . import text_space


PUNCTUATION=set('、。，．・：；？！）〕］｝〉》」』】（〔［｛〈《「『【')
# Marks whose leading space is removed too (opening brackets, middle dots).
OPENING=set('（〔［｛〈《「『【・：；')
CLOSING=set('、。，．）〕］｝〉》」』】')


def family(font):return re.sub(r'^/?[A-Z]{6}\+','',font.basefont).lstrip('/')


def source_tokens(c):
    """Drawn glyphs with page-space size, horizontal scale, spacing and advance (text_space)."""
    tokens={}
    for e in c.events:
        try:space=text_space.effective(e)
        except Refused:continue
        for ch in e.chars:
            if len(ch.source_orders)!=1 or len(ch.text)!=1:continue
            i=ch.source_orders[0];g=c.actual[i]
            if g['unicode']!=ch.text or not g['gid'] and ch.text.strip():continue
            tokens[i]=dict(text=ch.text,source_id=i,code=ch.code,font=e.state.font,font_name=e.state.font.name,
                family=family(e.state.font),size=space['size'],tz=space['tz'],fill=e.state.fill,fill_space=e.state.fill_space,tc=space['tc'],tw=space['tw'],
                rise=space['rise'],origin=list(g['origin']),advance=ch.advance*space['sx'],gid=g['gid'],bbox=list(g['bbox']),
                mode='retained-source',scope=None,event=e,tracking=0.,nominal_advance=ch.pdf_width/1000*space['size']*space['tz']/100)
    # Pair displacements include TJ tracking, while line-final advance remains
    # the nominal width. This avoids carrying a line reset into the next line.
    # One row per drawn line: mixed sizes on a line are centred on slightly
    # different baselines (within 0.35 em of each other).
    rows=[]
    for t in sorted(tokens.values(),key=lambda t:t['origin'][1]):
        if rows and abs(t['origin'][1]-rows[-1][0]['origin'][1])<=.35*min(t['size'],rows[-1][0]['size']):rows[-1].append(t)
        else:rows.append([t])
    tracking=defaultdict(list)
    for row in rows:
        row.sort(key=lambda t:t['origin'][0])
        for t,n in zip(row,row[1:]):
            delta=n['origin'][0]-t['origin'][0]
            same=all(t[k]==n[k] for k in ('font_name','size','tz','rise','tc','tw'))
            pair=t['text'] in CLOSING and n['text'] in OPENING
            if abs(delta-t['advance'])<.25*t['size'] or abs(delta-t['nominal_advance'])<.25*t['size']:
                # The observed pen step is kept even across a font switch
                # (TJ adjustments); only same-style pairs feed the tracking median.
                # TJ adjustments may also undo a character spacing (Tc), so the
                # step is compared with the glyph width with and without it.
                t['tracking']=delta-t['advance'];t['advance']=delta
                if same:tracking[(t['font_name'],t['size'],t['tz'])].append(t['tracking'])
                continue
            elif (t['text'] in PUNCTUATION or n['text'] in OPENING) and (0 if pair else .2)*t['advance']<=delta<t['advance']:
                # Japanese writers compress punctuation (約物詰め): the space after
                # closing marks and before opening brackets is removed (half width
                # plus tracking; InDesign down to 0.24 em). A closing mark directly
                # followed by an opening one may lose both halves (「）【」 in one em
                # box, step 0). Keep the observed advance, but do not let it skew
                # the tracking median.
                own=delta>=.2*t['advance'];t['tracking']=delta-t['advance'];t['advance']=delta
                # Only a compressed mark itself is evidence for that mark's width,
                # not a pair step below that, which depends on the next mark.
                if t['text'] in PUNCTUATION and own:t['compressed_punctuation']=True
    med={k:median(v) for k,v in tracking.items()}
    return tokens,med


def resource_witnesses(c,font):
    """Unicode -> (code, gid) of an original font resource, from the resource itself.

    A character deleted by an earlier edit has no drawn occurrence any more,
    but the unchanged embedded program and its ToUnicode still define it. Only
    an unambiguous inverse mapping whose glyph has an outline and whose PDF
    width equals the program advance is a witness. Type0/Identity-H (CID ->
    GID map) and simple TrueType (code -> glyph through the (1,0)/(3,0) cmap);
    Type0 over a CID-keyed CFF program (InDesign, Illustrator: CID -> glyph
    through the CFF charset).
    """
    from io import BytesIO
    from fontTools.ttLib import TTFont
    if font.error or font.subtype not in ('/Type0','/TrueType'):return {}
    data=c.document.extract_font(font.xref)[3]
    try:program=TTFont(BytesIO(data))
    except Exception:
        return _cff_witnesses(font,data) if font.subtype=='/Type0' else {}
    try:
        if 'CFF ' in program and font.subtype=='/Type0':return _cff_witnesses(font,program['CFF '].cff)
        if 'glyf' not in program or 'hmtx' not in program:return {}
        order=program.getGlyphOrder();upem=program['head'].unitsPerEm
        if font.subtype=='/Type0':
            descendant=font.resource['/DescendantFonts'][0].get_object()
            mapping=descendant.get('/CIDToGIDMap','/Identity')
            table=None if str(mapping)=='/Identity' else mapping.get_object().get_data()
            def gid_of(value):return value if table is None else int.from_bytes(table[2*value:2*value+2] or b'\0\0','big')
        else:
            cmaps=[t.cmap for t in program['cmap'].tables if (t.platformID,t.platEncID) in ((1,0),(3,0))]
            def gid_of(value):
                name=next((m.get(value) or m.get(0xF000|value) for m in cmaps if m.get(value) or m.get(0xF000|value)),None)
                return program.getGlyphID(name) if name else 0
        found={}
        keys={ord(k) for k in font.unicode_map if isinstance(k,str) and len(k)==1}
        for value in set(font.widths)|keys:
            if value>=256**font.unit:continue
            code=value.to_bytes(font.unit,'big')
            try:char=font.decode(code)[0][1]
            except Exception:continue
            found.setdefault(char,[]).append(value)
        result={}
        for char,values in found.items():
            if len(values)!=1 or values[0] not in font.widths:continue
            value=values[0];gid=gid_of(value)
            if not 0<gid<len(order):continue
            glyph=program['glyf'][order[gid]]
            if not char.isspace() and not (glyph.isComposite() or glyph.numberOfContours>0):continue
            if abs(program['hmtx'][order[gid]][0]*1000/upem-font.widths[value])>1:continue
            result[char]=(value.to_bytes(font.unit,'big'),gid)
        return result
    finally:program.close()


def _inverse(font):
    """Unicode -> (the only code of the resource that decodes to it, its PDF width incl. /DW)."""
    found={}
    for key,target in font.unicode_map.items():
        value=ord(key) if isinstance(key,str) and len(key)==1 else None
        if value is None or not isinstance(target,str) or len(target)!=1 or value>=256**font.unit:continue
        try:code,char,width=font.decode(value.to_bytes(font.unit,'big'))[0]
        except Exception:continue
        found.setdefault(char,[]).append((value,width))
    return {char:values[0] for char,values in found.items() if len(values)==1}


def _cff_witnesses(font,data):
    """Witnesses of an Identity-H Type0 font over a CID-keyed CFF program."""
    from io import BytesIO
    from fontTools.cffLib import CFFFontSet
    from fontTools.misc.psCharStrings import T2WidthExtractor
    descendant=font.resource['/DescendantFonts'][0].get_object()
    if str(font.resource.get('/Encoding'))!='/Identity-H' or descendant.get('/Subtype')!='/CIDFontType0':return {}
    try:
        if isinstance(data,(bytes,bytearray)):
            cff=CFFFontSet();cff.decompile(BytesIO(data),None)
        else:cff=data
        top=cff[cff.fontNames[0]]
        if not hasattr(top,'ROS'):return {}
        strings=top.CharStrings;names=set(top.charset)
    except Exception:return {}
    result={}
    for char,(cid,width) in _inverse(font).items():
        name=f'cid{cid:05d}'
        if name not in names:continue
        try:
            glyph=strings[name];glyph.decompile();private=glyph.private
            meter=T2WidthExtractor(getattr(private,'Subrs',[]),top.GlobalSubrs,private.nominalWidthX,private.defaultWidthX)
            meter.execute(glyph)
            if not char.isspace() and glyph.calcBounds(strings) is None:continue
            if abs(meter.width-width)>1:continue
        except Exception:continue
        result[char]=(cid.to_bytes(font.unit,'big'),list(top.charset).index(name))
    return result


def sibling_fonts(c,font):
    """Page font resources that are other subsets of the same embedded face.

    Producers split a face over several subsets (LibreOffice: 256 codes each).
    Same family after the subset tag, same subtype and descriptor style; the
    anchor's own resource always comes first.
    """
    from pdfeditor.content_stream import FontCodec
    def style(f):
        d=f.resource.get('/FontDescriptor') or (f.resource['/DescendantFonts'][0].get_object().get('/FontDescriptor') if '/DescendantFonts' in f.resource else None)
        d=d.get_object() if d else {}
        return (family(f),f.subtype,d.get('/Flags'),d.get('/ItalicAngle'),d.get('/FontWeight'),d.get('/StemV'))
    result=[]
    for name,ref in c.reader.pages[c.page.number]['/Resources']['/Font'].items():
        other=FontCodec(ref,name)
        if other.xref!=font.xref and not other.error and style(other)==style(font):result.append(other)
    return result


def build(c,paragraph,start,old,new,substitute=None,justify=False):
    source,tracking=source_tokens(c)
    try:base=[copy(source[i]) for i in paragraph.ids]
    except KeyError:raise Refused('selected source glyph lacks a concrete code/GID witness')
    # Mixed sizes on one line may be centred on their own baselines; keep each
    # glyph's observed offset from its line (new glyphs copy their anchor's).
    for t in base:
        line=min(paragraph.baselines,key=lambda b:abs(b-t['origin'][1]))
        offset=t['origin'][1]-line
        t['baseline_offset']=offset if .01<abs(offset)<.35*t['size'] else 0.
    # A wider gap between two glyphs of one line than the kept pen steps
    # (a word space drawn as positioning, as TeX does, or wide letter
    # spacing) has no token to carry it: the relayout would close it up.
    line_of=lambda t:min(paragraph.baselines,key=lambda b:abs(b-t['origin'][1]))
    for t,n in zip(base,base[1:]):
        if line_of(t)==line_of(n) and t['text'].strip() and n['text'].strip() and n['origin'][0]-t['origin'][0]-t['advance']>.1*t['size']:
            raise Refused('positional gap within a line without a space glyph (word space or letter spacing drawn by positioning) is unsupported')
    for t in base:
        for n,s in enumerate(paragraph.scopes):
            if s['content_byte_range'][0]<=t['event'].operator.start<s['content_byte_range'][1]:t['scope']=n;break
    fonts={};cache={}
    candidates=[]
    # One approved substitute, or several approved candidates of which B2 picks,
    # per source font, the one whose measured glyph weight is closest.
    for item in (substitute if isinstance(substitute,list) else [substitute] if substitute else []):
        if item.get('relation')!='substituted' or item.get('font_index',0)!=0:raise Refused('explicit substitute relation/font index required')
        raw=Path(item['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise Refused('substitute font hash mismatch')
        candidates.append((item['sha256'],raw,ShapedFont(raw)))
    chosen={}

    def substitute_for(anchor):
        """The approved substitute for this source font, measured once per font."""
        xref=anchor['font'].xref
        if xref not in chosen:
            if len(candidates)==1:chosen[xref]=(candidates[0],None)
            else:
                scores=weight_scores(c,[t for t in base if t['font'].xref==xref],candidates)
                best=min(range(len(candidates)),key=lambda n:(scores[n]['distance'] is None,scores[n]['distance'] or 0,n))
                chosen[xref]=(candidates[best],scores)
        return chosen[xref][0]

    def resolve(char,anchor,mode=None):
        """A new token in the anchor's style: source witness, same program, then approved substitute."""
        t=copy(anchor);t.update(text=char,source_id=None,origin=None,bbox=None)
        # Never silently borrow another font resource merely because Unicode
        # happens to match. A source witness must be in the anchor resource.
        witnesses=[v for v in source.values() if v['text']==char and v['font'].xref==anchor['font'].xref]
        if witnesses:
            w=witnesses[0];t.update(code=w['code'],gid=w['gid'],mode=mode or 'source-code-witness')
            nominal=anchor['font'].decode(w['code'])[0][2]/1000*anchor['size']
            t['nominal_advance']=nominal*anchor['tz']/100
            t['advance']=(nominal+anchor['tc']+(anchor['tw'] if w['code']==b' ' else 0))*anchor['tz']/100+tracking.get((anchor['font_name'],anchor['size'],anchor['tz']),0)
            return t
        xref=anchor['font'].xref
        if ('fonts',xref) not in cache:cache[('fonts',xref)]=[anchor['font']]+sibling_fonts(c,anchor['font'])
        for n,owner in enumerate(cache[('fonts',xref)]):
            if ('resource',owner.xref) not in cache:cache[('resource',owner.xref)]=resource_witnesses(c,owner)
            if char in cache[('resource',owner.xref)]:
                code,gid=cache[('resource',owner.xref)][char]
                t.update(code=code,gid=gid,font=owner,font_name=owner.name,mode=mode or ('source-resource-witness' if not n else 'sibling-subset-witness'))
                nominal=owner.decode(code)[0][2]/1000*anchor['size']
                t['nominal_advance']=nominal*anchor['tz']/100
                t['advance']=(nominal+anchor['tc']+(anchor['tw'] if code==b' ' else 0))*anchor['tz']/100+tracking.get((anchor['font_name'],anchor['size'],anchor['tz']),0)
                return t
        if xref not in cache:
            try:
                data=c.document.extract_font(xref)[3];cache[xref]=ShapedFont(data) if data else None
            except Exception:cache[xref]=None
        font=cache[xref];kind=mode or 'same-program-new-code';key='source:'+str(xref)
        try:
            run=font.shape(char) if font else None
            if run is None or len(run.glyphs)!=1 or not run.glyphs[0].gid:raise ValueError('absent Unicode glyph')
        except Exception:
            if mode:raise Refused('implicit line-join space has no glyph in the source font')
            if not candidates:raise Refused('missing source glyph requires explicit substitute approval: '+char)
            digest,_,font=substitute_for(anchor);key='substitute' if len(candidates)==1 else 'substitute:'+digest[:12]
            kind='approved-substitute';run=font.shape(char)
        if len(run.glyphs)!=1 or not run.glyphs[0].gid:raise Refused('one shaped glyph per scalar required')
        g=run.glyphs[0];fonts[key]=font;t.update(mode=kind,font_key=key,shaped=g,gid=g.gid,code=None)
        t['nominal_advance']=g.advance*anchor['size']/font.upem*anchor['tz']/100
        t['advance']=(g.advance*anchor['size']/font.upem+anchor['tc'])*anchor['tz']/100+tracking.get((anchor['font_name'],anchor['size'],anchor['tz']),0)
        return t

    # Justified word spaces were stretched by the source writer; the new layout
    # stretches them again per line, so retained spaces start from their
    # nominal width (glyph width with Tc/Tw, without the justification).
    if justify:
        for t in base:
            if t['text']==' ':t['advance']=t['nominal_advance']+(t['tc']+t['tw'])*t['tz']/100
    # One token per logical character. A Latin line join has no source glyph;
    # it is drawn as a space in the preceding glyph's style.
    tokens=[];rest=iter(base)
    for n,char in enumerate(paragraph.text):
        tokens.append(resolve(' ',tokens[-1],'implicit-line-join') if n in paragraph.joins else next(rest))
    # Only the changed middle needs new characters. Unchanged prefix/suffix
    # keep source code, resource, style and source occurrence evidence.
    prefix=0
    while prefix<min(len(old),len(new)) and old[prefix]==new[prefix]:prefix+=1
    suffix=0
    while suffix<min(len(old)-prefix,len(new)-prefix) and old[-suffix-1]==new[-suffix-1]:suffix+=1
    at=start+prefix; stop=start+len(old)-suffix; inserted=new[prefix:len(new)-suffix if suffix else len(new)]
    anchor_index=min(at,len(tokens)-1)
    if at>0 and not tokens[anchor_index]['text'].strip() and inserted.strip():anchor_index=at-1
    # A glyph drawn by an earlier B2 save with a generated resource (a new
    # code or an approved substitute) is not the paragraph's source font. The
    # style anchor is the nearest original-resource glyph of the same size.
    def generated(t):return t['font_name'].startswith('/PFAuto')
    if generated(tokens[anchor_index]):
        order=sorted(range(len(tokens)),key=lambda j:(abs(j-anchor_index),j>anchor_index))
        anchor_index=next((j for j in order if tokens[j]['text'].strip() and not generated(tokens[j])
                           and tokens[j]['size']==tokens[anchor_index]['size']),anchor_index)
    anchor=copy(tokens[anchor_index])
    newtokens=[resolve(char,anchor) for char in inserted]
    # New CJK glyphs may be squeezed by the per-glyph compression the source
    # writer used for ordinary CJK glyphs of the same font and size (median of
    # observed reductions; capped at 0.05 em). Layout applies it only to a line
    # that would otherwise overflow, so restored text breaks as it did before.
    reductions=defaultdict(list)
    for t in base:
        gap=t['nominal_advance']-t['advance']
        if ord(t['text'])>=0x2E80 and t['text'] not in PUNCTUATION and gap>.001:reductions[(t['font_name'],t['size'])].append(gap)
    for t in newtokens:
        seen=reductions.get((anchor['font_name'],anchor['size']),[])
        if len(seen)>=3 and ord(t['text'])>=0x2E80 and t['text'] not in PUNCTUATION:t['squeezable']=min(median(seen),.05*t['size'])
    # New punctuation takes the compression its character had in the source
    # paragraph (median of observed advances), never more.
    observed=defaultdict(list)
    for t in base:
        if t.get('compressed_punctuation'):observed[(t['text'],t['font_name'],t['size'])].append(t['advance'])
    for t in newtokens:
        seen=observed.get((t['text'],t['font_name'],t['size']))
        if seen and median(seen)<t['advance']:t['advance']=median(seen)
    result=tokens[:at]+newtokens+tokens[stop:]
    choice={str(xref):dict(chosen_sha256=pick[0],scores=scores) for xref,(pick,scores) in chosen.items() if scores is not None}
    return result,fonts,dict(Counter(t['mode'] for t in result)),choice


def _density(image):
    """Fraction of ink pixels in a glyph box; ink differs from the box's border colour."""
    from PIL import ImageChops,ImageStat
    gray=image.convert('L');w,h=gray.size
    border=[gray.getpixel((x,y)) for x in range(w) for y in (0,h-1)]+[gray.getpixel((x,y)) for y in range(h) for x in (0,w-1)]
    background=sorted(border)[len(border)//2]
    diff=ImageChops.difference(gray,gray.point(lambda v:background)).point(lambda v:255 if v>96 else 0)
    return ImageStat.Stat(diff).mean[0]/255


def weight_scores(c,tokens,candidates,limit=24):
    """Glyph-weight distance of each candidate to the source font, per shared character.

    The source weight is measured on the PDF's own rendering of retained
    glyphs (em box at the glyph origin); a candidate draws the same characters
    in the same em box. The mean absolute difference of ink density is a
    simple, alignment-free proxy for stroke weight and face size.
    """
    from io import BytesIO
    from PIL import Image,ImageDraw,ImageFont
    import pymupdf
    zoom=4;samples=[]
    seen=set()
    for t in tokens:
        if t['text'].strip() and ord(t['text'])>=0x2E80 and t['text'] not in seen and t['origin']:
            seen.add(t['text']);samples.append(t)
        if len(samples)>=limit:break
    if not samples:return [dict(distance=None,characters=0) for _ in candidates]
    pix=c.page.get_pixmap(matrix=pymupdf.Matrix(zoom,zoom),alpha=False)
    page=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    source={}
    for t in samples:
        x,y=t['origin'];em=t['size']
        box=(round(x*zoom),round((y-.88*em)*zoom),round((x+em)*zoom),round((y+.12*em)*zoom))
        source[t['text']]=_density(page.crop(box))
    result=[]
    for digest,raw,font in candidates:
        size=round(samples[0]['size']*zoom);face=ImageFont.truetype(BytesIO(raw),size)
        distances=[]
        for char,weight in source.items():
            try:
                if not font.shape(char).glyphs[0].gid:continue
            except Exception:continue
            image=Image.new('L',(size,size),255);ImageDraw.Draw(image).text((0,round(.88*size)),char,font=face,fill=0,anchor='ls')
            distances.append(abs(_density(image)-weight))
        result.append(dict(sha256=digest,distance=round(sum(distances)/len(distances),5) if distances else None,characters=len(distances)))
    return result


def describe(c,paragraphs):
    """Public observations for approval; absent style metadata stays unknown."""
    selected={i for p in paragraphs for i in p.ids};styles={};programs={}
    for e in c.events:
        count=sum(len(selected.intersection(ch.source_orders)) for ch in e.chars)
        if not count:continue
        state=e.state;font=state.font;space=text_space.effective(e)
        if font.xref not in programs:
            data=c.document.extract_font(font.xref)[3]
            programs[font.xref]=hashlib.sha256(data).hexdigest() if data else None
        key=(font.name,space['size'],space['tz'],space['tc'],space['tw'],space['rise'],state.fill)
        if key not in styles:
            resource=font.resource
            if '/DescendantFonts' in resource:resource=resource['/DescendantFonts'][0].get_object()
            descriptor=resource.get('/FontDescriptor');descriptor=descriptor.get_object() if descriptor else {}
            # Page-space values: a scale in the text matrix or CTM is part of the size.
            styles[key]=dict(resource=font.name,base_font=font.basefont,embedded_program_sha256=programs[font.xref],size=space['size'],horizontal_scale=space['tz'],
                character_spacing=space['tc'],word_spacing=space['tw'],text_rise=space['rise'],fill=[state.fill[0],[float(v) for v in state.fill[1]]],
                descriptor={k:float(descriptor[k]) if k in descriptor else None for k in ('/Flags','/ItalicAngle','/FontWeight')},source_glyph_count=0)
        styles[key]['source_glyph_count']+=count
    return list(styles.values())
