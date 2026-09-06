"""Constrained PDF text interpreter with byte-range provenance.

Read objects with pypdf; preserve original bytes of every non-target operator.
PDF font codes and /Widths or CID /W define displacement, not a reloaded font.
Unsupported mappings and ambiguous paint/glyph correspondences fail closed.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from copy import copy, deepcopy
from io import BytesIO
import hashlib
import math

import pymupdf
from pypdf import PdfReader
from pypdf._cmap import get_encoding
from pypdf.generic import ByteStringObject, read_object

from .backend import PdfError


def digest(data):
    return hashlib.sha256(data).hexdigest()


def state_object(value,depth=0):
    """Compare resource state by value, never dictionary order or xref number."""
    if depth>12:raise PdfError("graphics-state resource recursion limit")
    if hasattr(value,"get_object"):value=value.get_object()
    if isinstance(value,dict):
        result={str(k):state_object(v,depth+1) for k,v in value.items() if k not in {"/Length","/Filter","/DecodeParms"}}
        if hasattr(value,"get_data"):result["decoded_stream_sha256"]=digest(value.get_data())
        return result
    if isinstance(value,(list,tuple)):return [state_object(v,depth+1) for v in value]
    if isinstance(value,(int,float,bool)):return value
    if isinstance(value,bytes):return {"bytes":value.hex()}
    return str(value)


def number(value):
    if not math.isfinite(float(value)):
        raise PdfError("nonfinite PDF operand")
    return format(float(value), ".12g").encode("ascii")


@dataclass
class Operator:
    start: int
    end: int
    name: str
    args: list


def operators(data: bytes):
    """Record exact token spans; strings/arrays/dictionaries use the PDF parser."""
    stream=BytesIO(data)
    args=[]
    start=None
    whitespace=b"\x00\t\n\x0c\r "
    delimiters=b"()<>[]{}/%"
    while stream.tell()<len(data):
        pos=stream.tell();ch=stream.read(1)
        if ch in whitespace:continue
        if ch==b"%":
            while stream.tell()<len(data) and stream.read(1) not in b"\r\n":pass
            continue
        stream.seek(pos)
        if start is None:start=pos
        if ch in b"(/<[+-.0123456789" or data[pos:pos+4] in (b"true",b"null") or data[pos:pos+5]==b"false":
            obj=read_object(stream,None,"bytes")
            if stream.tell()<=pos:raise PdfError("PDF parser made no progress")
            args.append(obj)
            continue
        end=pos
        while end<len(data) and data[end] not in whitespace+delimiters:end+=1
        if end==pos:raise PdfError(f"unexpected content delimiter at {pos}")
        name=data[pos:end].decode("ascii")
        if name=="BI":raise PdfError("inline image tokenization is outside this backend's supported scope")
        stream.seek(end)
        yield Operator(start,end,name,args)
        args=[];start=None
    if args:raise PdfError("content stream has trailing operands")


class FontCodec:
    def __init__(self, resource, name):
        self.resource=resource.get_object()
        self.name=name
        ref=getattr(self.resource,"indirect_reference",None)
        self.xref=ref.idnum if ref else None
        self.subtype=str(self.resource.get("/Subtype"))
        self.encoding_name=str(self.resource.get("/Encoding"))
        self.basefont=str(self.resource.get("/BaseFont"))
        self.error=None
        self.widths={}
        self.default=0
        try:
            self.encoding,self.unicode_map=get_encoding(self.resource)
            if self.subtype=="/Type0":
                if self.encoding_name!="/Identity-H":
                    raise PdfError("only Identity-H CID code mapping is supported")
                self.unit=2
                descendant=self.resource["/DescendantFonts"][0].get_object()
                self.default=float(descendant.get("/DW",1000))
                entries=descendant.get("/W",[])
                if hasattr(entries,"get_object"):entries=entries.get_object()
                i=0
                while i<len(entries):
                    first=int(entries[i]);item=entries[i+1];i+=2
                    if isinstance(item,list):
                        self.widths.update((first+j,float(w)) for j,w in enumerate(item))
                    else:
                        last=int(item);w=float(entries[i]);i+=1
                        if last-first>65535:raise PdfError("invalid CID width range")
                        self.widths.update((c,w) for c in range(first,last+1))
                self.width_source="CIDFont /W and /DW"
            elif self.subtype in {"/TrueType","/Type1"}:
                self.unit=1
                first=int(self.resource.get("/FirstChar",0))
                entries=self.resource.get("/Widths",[])
                if hasattr(entries,"get_object"):entries=entries.get_object()
                self.widths={first+i:float(w) for i,w in enumerate(entries)}
                descriptor=self.resource.get("/FontDescriptor",{}).get_object() if self.resource.get("/FontDescriptor") else {}
                self.default=float(descriptor.get("/MissingWidth",0))
                if not self.widths:raise PdfError("explicit simple-font /Widths are required")
                self.width_source="simple Font /Widths"
            else:raise PdfError(f"unsupported font subtype {self.subtype}")
        except Exception as exc:
            self.error=str(exc)

    def decode(self,data):
        if self.error:raise PdfError(self.error)
        if len(data)%self.unit:raise PdfError("truncated font character code")
        result=[]
        for i in range(0,len(data),self.unit):
            code=data[i:i+self.unit]
            value=int.from_bytes(code,"big")
            if self.unit==2:
                key=code.decode("utf-16-be",errors="surrogatepass")
            elif isinstance(self.encoding,dict):key=self.encoding.get(value,chr(value))
            else:
                try:key=code.decode(self.encoding,errors="strict")
                except (LookupError,UnicodeError):raise PdfError("unsupported character encoding")
            text=self.unicode_map.get(key,key)
            if len(text)!=1 or text=="\ufffd" or 0xD800<=ord(text)<=0xDFFF:
                raise PdfError("Unicode restoration is unavailable or multi-character ligature is unsupported")
            result.append((code,text,self.widths.get(value,self.default)))
        return result

    def encode_available(self,text):
        reverse={}
        candidates=set(self.widths)
        for key in self.unicode_map:
            if isinstance(key,str) and len(key)==1 and ord(key)<256**self.unit:
                candidates.add(ord(key))
        for value in candidates:
            try:
                code=value.to_bytes(self.unit,"big")
                decoded=self.decode(code)[0][1]
                if decoded not in reverse:reverse[decoded]=code
            except (ValueError,PdfError,OverflowError):continue
        missing=sorted(set(text)-reverse.keys())
        if missing:raise PdfError("original font resource cannot encode " + ", ".join(f"U+{ord(c):04X}" for c in missing))
        return [reverse[c] for c in text]


@dataclass
class State:
    ctm: tuple=(1,0,0,1,0,0)
    font: FontCodec | None=None
    size: float=0
    tc: float=0
    tw: float=0
    tz: float=100
    tl: float=0
    ts: float=0
    tr: int=0
    fill: tuple=("g",(0,))
    stroke: tuple=("G",(0,))
    opacity: float=1
    stroke_opacity: float=1
    clip: tuple=()
    other: dict=field(default_factory=dict)

    def report(self):
        return {"ctm":list(self.ctm),"font_resource":self.font.name if self.font else None,
                "font_xref":self.font.xref if self.font else None,"font_size":self.size,
                "character_spacing":self.tc,"word_spacing":self.tw,"horizontal_scale":self.tz,
                "leading":self.tl,"rise":self.ts,"rendering_mode":self.tr,
                "fill":self.fill,"stroke":self.stroke,"opacity":self.opacity,
                "stroke_opacity":self.stroke_opacity,"clip":list(self.clip),"other":self.other}


def multiply(a,b):
    return tuple(pymupdf.Matrix(*a)*pymupdf.Matrix(*b))


def translate(matrix,x,y):
    return multiply((1,0,0,1,x,y),matrix)


@dataclass
class PaintChar:
    code: bytes
    text: str
    origin: tuple
    advance: float
    pdf_width: float
    source_orders: list[int]=field(default_factory=list)


@dataclass
class TextEvent:
    id: str
    stream_xref: int
    invocation: tuple
    operator: Operator
    state: State
    text_matrix: tuple
    atoms: list
    chars: list[PaintChar]
    error: str | None=None
    line_matrix: tuple=(1,0,0,1,0,0)

    def report(self):
        return {"id":self.id,"stream_xref":self.stream_xref,"invocation":self.invocation,
                "operator":self.operator.name,"byte_range":[self.operator.start,self.operator.end],
                "text_matrix":list(self.text_matrix),"line_matrix":list(self.line_matrix),"state":self.state.report(),"error":self.error,
                "characters":[{"code_hex":c.code.hex(),"unicode":c.text,"origin":c.origin,
                               "advance_text_space":c.advance,"pdf_width_1000em":c.pdf_width,
                               "source_orders":c.source_orders} for c in self.chars]}


class ContentPage:
    def __init__(self,path,page_number):
        self.reader=PdfReader(path)
        if self.reader.is_encrypted:self.reader.decrypt("")
        self.pdf_page=self.reader.pages[page_number-1]
        self.document=pymupdf.open(path)
        if self.document.needs_pass:raise PdfError("password required")
        self.page=self.document[page_number-1]
        self.events=[];self.streams={};self.errors=[];self._font_cache={}
        self.actual=[]
        for span in self.page.get_texttrace():
            for c in span["chars"]:
                self.actual.append({"unicode":chr(c[0]),"gid":c[1],"origin":c[2],"bbox":c[3],
                                    "span":{k:v for k,v in span.items() if k!="chars"}})
        resources=self.pdf_page["/Resources"].get_object()
        state=State();tm=(1,0,0,1,0,0);lm=tm;stack=[]
        # /Contents is a single program even when operands or q/Q cross stream
        # boundaries. Join decoded bytes, never parse each array member alone.
        merged=self.pdf_page.get_contents()
        if merged is not None:
            state,tm,lm,stack=self._walk(merged,resources,state,tm,lm,stack,(),0,
                                       data_override=merged.get_data(),xref_override=-self.page.xref)
        if stack:self.errors.append("unbalanced q/Q")
        self._map_observations()

    def close(self):self.document.close()

    def _font(self,resources,name):
        item=resources["/Font"][name]
        ref=getattr(item,"indirect_reference",None)
        key=(ref.idnum if ref else id(item),name)
        if key not in self._font_cache:self._font_cache[key]=FontCodec(item,name)
        return self._font_cache[key]

    def _walk(self,ref,resources,state,tm,lm,stack,invocation,depth,data_override=None,xref_override=None):
        if depth>8:raise PdfError("Form XObject nesting limit")
        obj=ref.get_object();xref=xref_override if xref_override is not None else obj.indirect_reference.idnum
        data=obj.get_data() if data_override is None else data_override
        self.streams[xref]=data
        path=[];pending_clip=None;pending_text_clips=[]
        try:ops=list(operators(data))
        except Exception as exc:
            self.errors.append(f"stream {xref}: {exc}");return state,tm,lm,stack
        for index,op in enumerate(ops):
            n,a=op.name,op.args
            if n=="q":stack.append(copy(state));state.other=deepcopy(state.other)
            elif n=="Q":
                if not stack:raise PdfError("unbalanced Q")
                state=stack.pop()
            elif n=="cm":state.ctm=multiply(tuple(map(float,a)),state.ctm)
            elif n=="BT":tm=lm=(1,0,0,1,0,0);pending_text_clips=[]
            elif n=="ET":
                state.clip=state.clip+tuple(pending_text_clips);pending_text_clips=[]
            elif n=="Tf":state.font=self._font(resources,str(a[0]));state.size=float(a[1])
            elif n in {"Tc","Tw","Tz","TL","Ts","Tr"}:setattr(state,n.lower(),int(a[0]) if n=="Tr" else float(a[0]))
            elif n=="Tm":tm=lm=tuple(map(float,a))
            elif n in {"Td","TD"}:
                tx,ty=map(float,a)
                if n=="TD":state.tl=-ty
                tm=lm=translate(lm,tx,ty)
            elif n=="T*":tm=lm=translate(lm,0,-state.tl)
            elif n in {"g","rg","k","cs","sc","scn"}:state.fill=(n,tuple(map(str,a)))
            elif n in {"G","RG","K","CS","SC","SCN"}:state.stroke=(n,tuple(map(str,a)))
            elif n=="gs":
                gs=resources["/ExtGState"][a[0]].get_object()
                state.opacity=float(gs.get("/ca",state.opacity));state.stroke_opacity=float(gs.get("/CA",state.stroke_opacity))
                state.other["ExtGState:"+str(a[0])]=state_object(gs)
                if "/Font" in gs:raise PdfError("ExtGState Font is not supported")
            elif n in {"w","J","j","M","d","ri","i"}:state.other[n]=str(a)
            elif n in {"m","l","c","v","y","h","re"}:
                path.append({"operator":n,"args":list(map(float,a)),"ctm":list(state.ctm)})
            elif n in {"W","W*"}:pending_clip=n
            elif n in {"n","S","s","f","F","f*","B","B*","b","b*"}:
                if pending_clip:
                    state.clip=state.clip+({"rule":pending_clip,"path":path.copy(),"at":[xref,index]},)
                    pending_clip=None
                path=[]
            elif n in {"BMC","BDC","EMC"}:state.other.setdefault("marked_content",[]).append([n,str(a)])
            elif n=="Do":
                target=resources.get("/XObject",{}).get(a[0])
                if target and target.get_object().get("/Subtype")=="/Form":
                    form=target.get_object();child=copy(state);child.other=deepcopy(state.other)
                    child.ctm=multiply(tuple(map(float,form.get("/Matrix",(1,0,0,1,0,0)))),state.ctm)
                    child.clip=child.clip+({"rule":"Form BBox","bbox":list(map(float,form["/BBox"])),"ctm":list(child.ctm)},)
                    self._walk(target,form.get("/Resources",resources).get_object(),child,tm,lm,[],invocation+((xref,index,str(a[0])),),depth+1)
            elif n in {"Tj","TJ","'",'"'}:
                if n=='"':state.tw=float(a[0]);state.tc=float(a[1])
                if n in {"'",'"'}:tm=lm=translate(lm,0,-state.tl)
                atoms=a[0] if n=="TJ" else [a[-1]]
                event=TextEvent(f"s{xref}-o{index}"+"".join(f"@{i[1]}" for i in invocation),xref,invocation,op,copy(state),tm,[],[])
                event.line_matrix=lm
                event.state.other=deepcopy(state.other)
                try:
                    if not state.font or state.size==0:raise PdfError("font or font size missing")
                    for atom in atoms:
                        if isinstance(atom,(bytes,ByteStringObject)):
                            for code,text,w in state.font.decode(bytes(atom)):
                                point=pymupdf.Point(0,state.ts)*pymupdf.Matrix(*tm)*pymupdf.Matrix(*state.ctm)*self.page.transformation_matrix
                                advance=(w/1000*state.size+state.tc+(state.tw if code==b" " else 0))*state.tz/100
                                char=PaintChar(code,text,tuple(point),advance,w)
                                event.atoms.append(char);event.chars.append(char)
                                tm=translate(tm,advance,0)
                        else:
                            value=float(atom);event.atoms.append(value)
                            tm=translate(tm,-value/1000*state.size*state.tz/100,0)
                except Exception as exc:event.error=str(exc)
                if state.tr>=4:
                    pending_text_clips.append({"rule":"text clipping","at":[xref,index],
                                               "font_xref":state.font.xref if state.font else None,
                                               "text_matrix":list(event.text_matrix),"ctm":list(state.ctm),
                                               "geometry":"unknown glyph outlines; later reflow must refuse"})
                self.events.append(event)
            elif n not in {"ET","sh","MP","DP","BX","EX","d0","d1"}:
                self.errors.append(f"unsupported operator {n}")
        return state,tm,lm,stack

    def _map_observations(self):
        # Match parser-derived character origins to trace observations. Duplicate
        # fill/stroke paints remain distinct; ambiguous overlapping paints refuse.
        candidates=defaultdict(list)
        for i,g in enumerate(self.actual):candidates[g["unicode"]].append(i)
        used=set()
        for event in self.events:
            if event.error:continue
            mode=event.state.tr
            paints=[0,1] if mode==2 else [mode] if mode in (0,1,3) else []
            if not paints:
                event.error="text clipping rendering mode is not editable";continue
            for c in event.chars:
                for paint in paints:
                    matches=[i for i in candidates[c.text] if i not in used
                             and self.actual[i]["span"]["type"]==paint
                             and max(abs(v-w) for v,w in zip(self.actual[i]["origin"],c.origin))<.015]
                    if len(matches)!=1:
                        event.error="ambiguous or unmatched glyph provenance";continue
                    c.source_orders.append(matches[0]);used.add(matches[0])

    def selected_events(self,source_orders):
        selected=set(source_orders);found=set();events=[]
        for event in self.events:
            indices={i for c in event.chars for i in c.source_orders}
            if selected & indices:
                if event.error:raise PdfError(event.error)
                if event.invocation:raise PdfError("selected text is inside a Form XObject; invocation-local cloning is not implemented")
                if event.state.tr>=3:raise PdfError("invisible or text-clipping paint requires separate semantics")
                for c in event.chars:
                    if selected.intersection(c.source_orders) and not set(c.source_orders)<=selected:
                        raise PdfError("one text operator paints both fill and stroke; select both paint events")
                events.append(event);found.update(indices&selected)
        if found!=selected:
            bad=[self.actual[i]["unicode"] for i in sorted(selected-found)]
            if any(c=="\ufffd" for c in bad):raise PdfError("Unicode restoration is unavailable")
            raise PdfError("backend cannot establish unique text-operator provenance for every selected glyph")
        if self.errors:raise PdfError("content interpretation incomplete: "+self.errors[0])
        return events


def serialized_event(event,selected,remove=False):
    """Rebuild text operands as explicit character codes, retaining every TJ gap.

    Deletion emits numeric displacement, so later text in the same BT/ET keeps
    its original position. Replay emits fresh hex-string operands using the
    same font resource and same paint state at the same stream location.
    """
    items=[]
    for atom in event.atoms:
        if isinstance(atom,PaintChar):
            if remove and selected.intersection(atom.source_orders):
                value=-atom.advance/(event.state.size*event.state.tz/100)*1000
                items.append(number(value))
            else:items.append(b"<"+atom.code.hex().encode("ascii")+b">")
        else:items.append(number(atom))
    prefix=b""
    if event.operator.name=='"':prefix=number(event.state.tw)+b" Tw "+number(event.state.tc)+b" Tc T* "
    elif event.operator.name=="'":prefix=b"T* "
    return prefix+b"["+b" ".join(items)+b"] TJ"


def patch_streams(content,events,selected,remove=False):
    by_stream=defaultdict(list)
    for e in events:by_stream[e.stream_xref].append(e)
    changed={}
    for xref,group in by_stream.items():
        data=content.streams[xref]
        for event in sorted(group,key=lambda e:e.operator.start,reverse=True):
            data=data[:event.operator.start]+serialized_event(event,selected,remove)+data[event.operator.end:]
        changed[xref]=data
    return changed
