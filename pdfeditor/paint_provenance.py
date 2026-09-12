"""Connect source path operators to MuPDF paint by a counterfactual probe.

Byte provenance, interpreted paint and semantic ownership remain different
contracts. This module proves only a unique paint deletion from one source
operator; it neither infers ownership nor permits arbitrary geometry changes.
"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re

import pymupdf
from pypdf import PdfReader
from pypdf.generic import ArrayObject, ContentStream

from .backend import PdfError
from .content_stream import operators
from .pdf_save import program_pdf_bytes
from .proof_session import evidence_cache, revision

m = pymupdf.mupdf
PATH_PAINT = {"f", "F", "f*", "S", "s", "B", "B*", "b", "b*"}
PATH_BUILD = {"m", "l", "c", "v", "y", "h", "re"}
PATH_END = PATH_PAINT | {"n"}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())


def _xref(obj):
    ref = getattr(obj, "indirect_reference", None)
    return ref.idnum if ref is not None else None


def _program(page):
    """Mirror pypdf's decoded /Contents joining, recording synthetic newlines."""
    contents = page.get("/Contents")
    if contents is None:
        return b"", []
    value = contents.get_object()
    is_array = isinstance(value, ArrayObject)
    values = list(value) if is_array else [contents]
    data, parts = bytearray(), []
    for index, item in enumerate(values):
        stream = item.get_object()
        if not hasattr(stream, "get_data"):
            raise PdfError("Contents contains a non-stream object")
        raw = stream.get_data()
        start = len(data)
        data.extend(raw)
        parts.append(dict(index=index, xref=_xref(stream), merged_range=[start, len(data)],
                          decoded_sha256=_sha(raw), decoded_length=len(raw)))
        if is_array:
            # Obtain the separator from the actual save adapter. Some pypdf
            # versions append LF even when the component already ends in LF.
            # Never guess byte offsets from the intended joining algorithm.
            joined=ContentStream(ArrayObject([item]),page.pdf).get_data()
            if not joined.startswith(raw) or joined[len(raw):] not in (b'',b'\n'):
                raise PdfError('save adapter transformed a Contents component unexpectedly')
            data.extend(joined[len(raw):])
    merged = page.get_contents()
    if merged is not None and bytes(data) != merged.get_data():
        raise PdfError("physical Contents mapping differs from the save adapter program")
    return bytes(data), parts


def _physical(parts, start, end):
    return [dict(contents_index=p["index"], xref=p["xref"],
                 decoded_range=[max(start, p["merged_range"][0])-p["merged_range"][0],
                                min(end, p["merged_range"][1])-p["merged_range"][0]],
                 merged_range=[max(start, p["merged_range"][0]), min(end, p["merged_range"][1])])
            for p in parts if max(start,p["merged_range"][0]) < min(end,p["merged_range"][1])]


@evidence_cache(lambda source,page_number:(revision(source),page_number),limit=4)
def _load_catalog(source, page_number):
    reader = PdfReader(source)
    if reader.is_encrypted and not reader.decrypt(""):
        raise PdfError("password required")
    if not 1 <= page_number <= len(reader.pages):
        raise PdfError("page is outside this document")
    page = reader.pages[page_number-1]
    data, parts = _program(page)
    source_hash = _sha(Path(source).read_bytes())
    result = dict(schema_version=1, source_sha256=source_hash, page=page_number,
                  program_sha256=_sha(data), contents=parts, paths=[], forms=[], errors=[])

    def walk(program, resources, physical, invocation, ancestors):
        try:
            ops = list(operators(program))
        except Exception as exc:
            result["errors"].append(f"source tokenization incomplete: {exc}")
            return
        path_ops, marks, clip_ops = [], [], []
        for index, op in enumerate(ops):
            if op.name in PATH_BUILD:
                path_ops.append(index)
            if op.name in {"W", "W*"}:
                clip_ops.append(index)
            if op.name in {"BMC", "BDC"}:
                mark = {"operator":op.name, "tag":str(op.args[0]) if op.args else "", "operator_index":index}
                if op.name == "BDC" and len(op.args)>1:
                    props = op.args[1]
                    if isinstance(props, str):
                        dictionary = resources.get("/Properties", {})
                        if hasattr(dictionary,"get_object"):
                            dictionary = dictionary.get_object()
                        props = dictionary.get(props, {})
                        if hasattr(props,"get_object"):
                            props = props.get_object()
                    if isinstance(props, dict):
                        mark["mcid"] = int(props["/MCID"]) if "/MCID" in props else None
                        mark["property_keys"] = sorted(str(k) for k in props)
                marks.append(mark)
            elif op.name == "EMC":
                if marks:
                    marks.pop()
                else:
                    result["errors"].append("unbalanced marked content")
            if op.name in PATH_PAINT:
                identity = dict(source=source_hash, page=page_number, invocation=invocation,
                                program=_sha(program), byte_range=[op.start,op.end])
                result["paths"].append(dict(id="path-"+_digest(identity), operator=op.name,
                    operator_index=index, merged_range=[op.start,op.end],
                    source_ranges=_physical(physical,op.start,op.end),
                    path_operator_indices=list(path_ops),
                    path_source_ranges=[dict(operator=ops[i].name, merged_range=[ops[i].start,ops[i].end],
                        source_ranges=_physical(physical,ops[i].start,ops[i].end)) for i in path_ops],
                    pending_clip_operator_indices=list(clip_ops), invocation=deepcopy(invocation),
                    marked_content=deepcopy(marks), mutable=not invocation,
                    operation_sha256=_sha(program[op.start:op.end])))
            if op.name in PATH_END:
                path_ops, clip_ops = [], []
            if op.name == "Do" and op.args:
                name = str(op.args[0])
                xobjects = resources.get("/XObject", {})
                if hasattr(xobjects,"get_object"):
                    xobjects = xobjects.get_object()
                target = xobjects.get(name)
                if target is None:
                    continue
                form = target.get_object()
                if form.get("/Subtype") != "/Form":
                    continue
                xref = _xref(form)
                edge = dict(resource=name, xref=xref, caller_operator_index=index,
                            caller_range=[op.start,op.end], caller_source_ranges=_physical(physical,op.start,op.end))
                child_invocation = invocation+[edge]
                result["forms"].append(dict(invocation=child_invocation, xref=xref,
                    bbox=list(map(float,form.get("/BBox",[]))),
                    matrix=list(map(float,form.get("/Matrix",[1,0,0,1,0,0]))), mutable=False))
                if xref in ancestors or len(invocation)>=8:
                    result["errors"].append("cyclic or excessive Form invocation depth")
                    continue
                raw = form.get_data()
                child_parts = [dict(index=0,xref=xref,merged_range=[0,len(raw)],decoded_sha256=_sha(raw),decoded_length=len(raw))]
                child_res = form.get("/Resources", resources).get_object()
                walk(raw,child_res,child_parts,child_invocation,ancestors | {xref})
        if marks:
            result["errors"].append("unclosed marked content")

    walk(data,page["/Resources"].get_object(),parts,[],set())
    counts = {}
    for form in result["forms"]:
        counts[form["xref"]] = counts.get(form["xref"],0)+1
    for form in result["forms"]:
        form["page_invocation_count"] = counts[form["xref"]]
    return result, data


def source_path_catalog(source, page=1):
    """Return SHA-bound source IDs; offsets are decoded, not file offsets."""
    return _load_catalog(source,page)[0]


def _matrix(value):
    return [getattr(value,k) for k in "abcdef"]


def _rect(value):
    return [value.x0,value.y0,value.x1,value.y1]


class _PathWalker(m.FzPathWalker2):
    def __init__(self, matrix):
        super().__init__()
        self.matrix, self.commands = matrix, []
        for name in ("moveto","lineto","curveto","closepath"):
            getattr(self,"use_virtual_"+name)()

    def point(self,x,y):
        a,b,c,d,e,f = self.matrix
        return [x*a+y*c+e,x*b+y*d+f]

    def moveto(self,ctx,x,y):
        self.commands.append(["m",*self.point(x,y)])

    def lineto(self,ctx,x,y):
        self.commands.append(["l",*self.point(x,y)])

    def curveto(self,ctx,a,b,c,d,e,f):
        self.commands.append(["c",*self.point(a,b),*self.point(c,d),*self.point(e,f)])

    def closepath(self,ctx):
        self.commands.append(["h"])


def _path(path,ctm):
    walker = _PathWalker(_matrix(ctm))
    m.fz_walk_path(m.FzPath(m.ll_fz_keep_path(path)),walker,walker.m_internal)
    return walker.commands


def _stroke(value):
    return {**{k:getattr(value,k) for k in ("start_cap","dash_cap","end_cap","linejoin","linewidth","miterlimit","dash_phase")},
            "dash_list":[m.floats_getitem(value.dash_list,i) for i in range(value.dash_len)]}


def _color(cs,color,alpha,params):
    return dict(colorspace=m.ll_fz_colorspace_name(cs) if cs else None,
                components=[m.floats_getitem(color,i) for i in range(m.ll_fz_colorspace_n(cs))] if cs else [],
                opacity=alpha,color_params={k:getattr(params,k) for k in ("ri","bp","op","opm")})


class _PaintDevice(m.FzDevice2):
    """A narrow mature-renderer adapter; unsupported state prevents proof."""
    def __init__(self, aliases):
        super().__init__()
        self.events, self.clips, self.groups, self.layers, self.errors = [], [], [], [], []
        self.seqno, self.aliases = 0, aliases
        self.images = {}
        names = ("fill_path","stroke_path","fill_text","stroke_text","ignore_text","fill_image",
                 "fill_image_mask","fill_shade","clip_path","clip_stroke_path","clip_text",
                 "clip_stroke_text","clip_image_mask","pop_clip","begin_group","end_group",
                 "begin_layer","end_layer","begin_mask","end_mask","begin_tile","end_tile")
        for name in names:
            getattr(self,"use_virtual_"+name)()

    def paint(self,kind,**value):
        event = dict(kind=kind,seqno=self.seqno,clips=deepcopy(self.clips),groups=deepcopy(self.groups),
                     layers=list(self.layers),**value)
        self.events.append(event)
        return event

    def fill_path(self,ctx,path,even_odd,ctm,cs,color,alpha,params):
        self.paint("fill-path",geometry=_path(path,ctm),matrix=_matrix(ctm),even_odd=bool(even_odd),
                   bounds=_rect(m.ll_fz_bound_path(path,None,ctm)),**_color(cs,color,alpha,params))
        self.seqno += 1

    def stroke_path(self,ctx,path,stroke,ctm,cs,color,alpha,params):
        self.paint("stroke-path",geometry=_path(path,ctm),matrix=_matrix(ctm),stroke=_stroke(stroke),
                   bounds=_rect(m.ll_fz_bound_path(path,stroke,ctm)),**_color(cs,color,alpha,params))
        self.seqno += 1

    def text(self,kind,text,ctm,paint,stroke=None):
        span = text.head
        while span:
            wrapper = m.FzTextSpan(span)
            name = m.fz_font_name(wrapper.font())
            match = re.fullmatch(r"Type3 \((\d+) \d+ R\)",name)
            if match:
                name = "Type3:"+repr(self.aliases.get(int(match[1]),[]))
            for index in range(span.len):
                glyph = wrapper.items(index)
                self.paint(kind,font=name,gid=glyph.gid,unicode=glyph.ucs,
                    glyph_position=[glyph.x,glyph.y],text_matrix=_matrix(wrapper.trm()),
                    matrix=_matrix(ctm),wmode=span.wmode,stroke=stroke,**paint)
            span = span.next
        self.seqno += 1

    def fill_text(self,ctx,text,ctm,cs,color,alpha,params):
        self.text("fill-text",text,ctm,_color(cs,color,alpha,params))

    def stroke_text(self,ctx,text,stroke,ctm,cs,color,alpha,params):
        self.text("stroke-text",text,ctm,_color(cs,color,alpha,params),_stroke(stroke))

    def ignore_text(self,ctx,text,ctm):
        self.text("ignore-text",text,ctm,{})

    def image_fingerprint(self,image):
        pointer = int(image.this)
        if pointer not in self.images:
            pixmap = pymupdf.Pixmap(m.fz_get_unscaled_pixmap_from_image(m.FzImage(m.ll_fz_keep_image(image))))
            self.images[pointer] = dict(width=pixmap.width,height=pixmap.height,n=pixmap.n,
                                        alpha=pixmap.alpha,samples_sha256=_sha(pixmap.samples))
        return self.images[pointer]

    def image(self,kind,image,ctm,paint):
        self.paint(kind,matrix=_matrix(ctm),image=self.image_fingerprint(image),**paint)
        self.seqno += 1

    def fill_image(self,ctx,image,ctm,alpha,params):
        self.image("fill-image",image,ctm,dict(opacity=alpha,color_params={k:getattr(params,k) for k in ("ri","bp","op","opm")}))

    def fill_image_mask(self,ctx,image,ctm,cs,color,alpha,params):
        self.image("fill-image-mask",image,ctm,_color(cs,color,alpha,params))

    def fill_shade(self,*args):
        self.errors.append("shading fingerprint is not implemented")
        self.seqno += 1

    def clip_path(self,ctx,path,even_odd,ctm,scissor):
        clip = dict(kind="clip-path",geometry=_path(path,ctm),matrix=_matrix(ctm),even_odd=bool(even_odd))
        self.clips.append(clip)
        self.events.append(dict(kind="push-clip",clip=clip))

    def clip_stroke_path(self,ctx,path,stroke,ctm,scissor):
        clip = dict(kind="clip-stroke-path",geometry=_path(path,ctm),matrix=_matrix(ctm),stroke=_stroke(stroke))
        self.clips.append(clip)
        self.events.append(dict(kind="push-clip",clip=clip))

    def clip_text(self,*args):
        self.errors.append("text clipping fingerprint is not implemented")
        self.clips.append({"kind":"unknown-text-clip"})

    clip_stroke_text = clip_text

    def clip_image_mask(self,ctx,image,ctm,scissor):
        clip = dict(kind="clip-image-mask",matrix=_matrix(ctm),
                    image=self.image_fingerprint(image),scissor=_rect(scissor))
        self.clips.append(clip)
        self.events.append(dict(kind="push-clip",clip=clip))

    def pop_clip(self,ctx):
        if not self.clips:
            self.errors.append("unbalanced renderer clip stack")
        else:
            self.clips.pop()
        self.events.append(dict(kind="pop-clip"))

    def begin_group(self,ctx,bbox,cs,isolated,knockout,blendmode,alpha):
        group = dict(bounds=_rect(bbox),isolated=bool(isolated),knockout=bool(knockout),blendmode=blendmode,opacity=alpha)
        self.groups.append(group)
        self.events.append(dict(kind="begin-group",group=group))

    def end_group(self,ctx):
        if self.groups:
            self.groups.pop()
        else:
            self.errors.append("unbalanced renderer group")
        self.events.append(dict(kind="end-group"))

    def begin_layer(self,ctx,name):
        self.layers.append(name)
        self.events.append(dict(kind="begin-layer",name=name))

    def end_layer(self,ctx):
        if self.layers:
            self.layers.pop()
        else:
            self.errors.append("unbalanced renderer layer")
        self.events.append(dict(kind="end-layer"))

    def begin_mask(self,*args):
        self.errors.append("soft-mask fingerprint is not implemented")

    def end_mask(self,*args):
        self.clips.append({"kind":"unknown-mask"})

    def begin_tile(self,*args):
        self.errors.append("pattern tile fingerprint is not implemented")
        return 0

    def end_tile(self,*args):
        pass


@evidence_cache(lambda source,page=1:(revision(source),page),limit=4)
def interpreted_paints(source, page=1):
    """Return interpreted page-space geometry and state, without source claims.

    Text callbacks are atomized to glyphs: removing a path may legitimately
    merge adjacent renderer text buffers without changing their paint.
    """
    document = pymupdf.open(stream=source,filetype="pdf") if isinstance(source,bytes) else pymupdf.open(source)
    try:
        if document.needs_pass:
            raise PdfError("password required")
        target = document[page-1]
        if target.rotation:
            target.set_rotation(0)
        aliases = {}
        for font in target.get_fonts(full=True):
            if font[2] == "Type3":
                aliases.setdefault(font[0],[]).append(font[4])
        device = _PaintDevice({k:sorted(v) for k,v in aliases.items()})
        m.fz_run_page_contents(target.this,device,m.FzMatrix(),m.FzCookie())
        m.fz_close_device(device)
        if device.clips or device.groups or device.layers:
            device.errors.append("renderer scopes remain open")
        for index,event in enumerate(device.events):
            event["index"] = index
            event["fingerprint"] = _digest({k:v for k,v in event.items() if k not in {"seqno","index"}})
        return dict(events=device.events,errors=device.errors,paint_event_count=device.seqno,
                    pymupdf=pymupdf.VersionBind,mupdf=pymupdf.VersionFitz)
    finally:
        document.close()


@evidence_cache(lambda source,page,data:(revision(source),page,_sha(data)),limit=4)
def _control_pdf(source,page,data):
    return program_pdf_bytes(source,page-1,data)


@evidence_cache(lambda source,page,path_id,catalog=None:(revision(source),page,path_id,_digest(catalog)),limit=256)
def prove_path_paint(source, page, path_id, *, catalog=None):
    """Certify a unique path paint bundle by consuming its source without paint."""
    fresh,data = _load_catalog(source,page)
    result = dict(status="refused",proof_type="counterfactual-path-consumption",source_id=path_id,
                  source_sha256=fresh["source_sha256"],page=page,paint_indices=[],paint_seqnos=[],reason=None)
    def refuse(reason):
        result["reason"] = reason
        return result
    if catalog is not None and catalog != fresh:
        return refuse("source catalog changed or does not match this PDF")
    if fresh["errors"]:
        return refuse(fresh["errors"][0])
    matches = [p for p in fresh["paths"] if p["id"]==path_id]
    if len(matches)!=1:
        return refuse("unknown or ambiguous source path ID")
    path = matches[0]
    if not path["mutable"]:
        return refuse("Form invocation-local mutation is not implemented")
    op = list(operators(data))[path["operator_index"]]
    if op.args:
        return refuse("path paint operator has unexpected operands")
    control_bytes = _control_pdf(source,page,data)
    replacement = b"h n" if op.name in {"s","b","b*"} else b"n"
    changed_bytes = program_pdf_bytes(source,page-1,data[:op.start]+replacement+data[op.end:])
    original = interpreted_paints(source,page)
    control = interpreted_paints(control_bytes,page)
    changed = interpreted_paints(changed_bytes,page)
    for observation in (original,control,changed):
        if observation["errors"]:
            return refuse(observation["errors"][0])
    before = [e["fingerprint"] for e in original["events"]]
    if before != [e["fingerprint"] for e in control["events"]]:
        return refuse("full-save control changed interpreted paint or scope")
    after = [e["fingerprint"] for e in changed["events"]]
    expected = (["fill-path","stroke-path"] if op.name in {"B","B*","b","b*"}
                else ["stroke-path"] if op.name in {"S","s"} else ["fill-path"])
    count = len(expected)
    candidates = [i for i in range(len(before)-count+1)
                  if [e["kind"] for e in original["events"][i:i+count]] == expected
                  and before[:i]+before[i+count:] == after]
    if not candidates:
        return refuse("counterfactual changed other paint/state, or selected operator has no isolated paint bundle")
    if len(candidates)!=1:
        return refuse("duplicate paint makes source-to-renderer correspondence ambiguous")
    index = candidates[0]
    paints = original["events"][index:index+count]
    result.update(status="proven",source=path,paint_indices=list(range(index,index+count)),
                  paint_seqnos=[p["seqno"] for p in paints],paints=paints,
                  evidence=dict(control_paint_equal=True,other_paint_and_scope_equal=True,
                    unique_deletion=True,replacement=replacement.decode(),
                    program_sha256=fresh["program_sha256"],control_pdf_sha256=_sha(control_bytes),
                    counterfactual_pdf_sha256=_sha(changed_bytes),
                    before_event_count=len(before),after_event_count=len(after),
                    renderer_versions={k:original[k] for k in ("pymupdf","mupdf")}))
    return result
