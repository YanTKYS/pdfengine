"""Stage 4 only: confirmed width, original PDF font codes/advances, local reflow."""
from __future__ import annotations
from collections import Counter
from math import hypot

import pymupdf

from .backend import PdfError
from .content_stream import ContentPage, multiply, number, serialized_event
from .fonts import validate_simple_text
from .layout import layout_text
from .model import Rect
from .pdf_save import publish_program, font_fingerprints
from .replay import ensure_destination, glyph_observations, set_page_program
from .selection import resolve_selection, source_sha
from .slot_edit import require_stage1


def matrix_operator(values):return b" ".join(number(v) for v in values)+b" Tm "


def signature(g):
    return (g["unicode"],g["glyph_id"],g["font"],round(g["size"],4),
            tuple(round(v,3) for v in g["origin"]),g["paint_type"],tuple(g["color"]),round(g["opacity"],5))


def allow_clip(state,new_bounds,page_transform):
    for clip in state.clip:
        if clip["rule"] not in {"W","W*"} or len(clip["path"])!=1 or clip["path"][0]["operator"]!="re":
            raise PdfError("Stage 4 cannot prove new text fits a nonrectangular or text clip")
        path=clip["path"][0];x,y,w,h=path["args"]
        transform=pymupdf.Matrix(*path["ctm"])*page_transform
        if abs(transform.b)>.00001 or abs(transform.c)>.00001:raise PdfError("Stage 4 rotated clip geometry is unsupported")
        rect=pymupdf.Rect(x,y,x+w,y+h)*transform
        if not Rect(*rect).contains(new_bounds,.001):raise PdfError("new text extends beyond its active clip")


def edit_reflow(source,output,manifest,replacement,*,stage1_report):
    output=ensure_destination(output,source);require_stage1(source,manifest,stage1_report)
    resolved=resolve_selection(source,manifest)
    width=resolved.widths.require_available_width()
    validate_simple_text(replacement)
    content=ContentPage(source,resolved.page)
    try:
        selected=set(manifest["glyph_ids"]);events=content.selected_events(selected)
        first=min(events,key=lambda e:e.operator.start);state=first.state;font=state.font
        if any(e.state.font.xref!=font.xref or e.state.tr!=0 for e in events):raise PdfError("Stage 4 needs one fill-text font resource")
        if state.opacity<.999 or state.stroke_opacity<.999:raise PdfError("Stage 4 transparent text is unsupported")
        if any("/SMask" in gs and gs["/SMask"]!="/None" or "/BM" in gs and gs["/BM"]!="/Normal"
               for key,gs in state.other.items() if key.startswith("ExtGState:") and isinstance(gs,dict)):
            raise PdfError("Stage 4 soft masks or blend modes are unsupported")
        # This local writer permits scale+translation only, with a common font
        # matrix. It does not guess transformations or carry text across pages.
        matrix=multiply(first.text_matrix,state.ctm)
        if abs(matrix[1])>.00001 or abs(matrix[2])>.00001 or matrix[0]<=0 or matrix[3]<=0:
            raise PdfError("Stage 4 requires axis-aligned, positive text matrices")
        for e in events:
            if any(abs(a-b)>.00001 for a,b in zip(multiply(e.text_matrix,e.state.ctm)[:4],matrix[:4])) or e.state.size!=state.size:
                raise PdfError("Stage 4 requires a common text scale/font size")
        codes=font.encode_available(replacement.replace("\n",""))
        encoding={c:code for c,code in zip(replacement.replace("\n",""),codes)}
        # Unchanged characters retain their original font codes when aliases
        # exist in a ToUnicode map.
        for e in events:
            for c in e.chars:
                if c.text in encoding:encoding[c.text]=c.code
        unit_scale=abs(matrix[0])
        def advance(char):
            code=encoding[char];w=font.decode(code)[0][2]
            return (w/1000*state.size+state.tc+(state.tw if code==b" " else 0))*state.tz/100*unit_scale
        def measure(text):return sum(advance(c) for c in text)
        lines=resolved.lines;size=resolved.glyphs[0].style.size
        leading=min((b.baseline-a.baseline for a,b in zip(lines,lines[1:]) if b.baseline>a.baseline),default=size*1.4)
        ascender=max(g.origin[1]-g.bbox.y0 for g in resolved.glyphs)
        descender=min(g.origin[1]-g.bbox.y1 for g in resolved.glyphs)
        layout=layout_text(replacement,x=resolved.bbox.x0,baseline=lines[0].baseline,width=width,
                           line_height=leading,ascender=ascender,descender=descender,
                           measure=measure,max_bottom=content.page.rect.height)
        expected_new=[];commands=[b"q "]
        inverse=~(pymupdf.Matrix(*state.ctm)*content.page.transformation_matrix)
        for line in layout.lines:
            x=line.x
            for char in line.text:
                point=pymupdf.Point(x,line.baseline)*inverse
                tm=(*first.text_matrix[:4],point.x-state.ts*first.text_matrix[2],point.y-state.ts*first.text_matrix[3])
                commands += [matrix_operator(tm),b"<"+encoding[char].hex().encode()+b"> Tj "]
                expected_new.append((char,x,line.baseline));x+=advance(char)
        # Tm sets both text and line matrices. Restore BOTH: the original line
        # matrix, then a TJ displacement to the original current text matrix.
        delta=multiply(first.text_matrix,tuple(~pymupdf.Matrix(*first.line_matrix)))
        if abs(delta[5])>.00001 or any(abs(a-b)>.00001 for a,b in zip(delta[:4],(1,0,0,1))):
            raise PdfError("cannot restore the original text/line matrix relationship")
        commands += [b"Q ",matrix_operator(first.line_matrix),b"["+number(-delta[4]/(state.size*state.tz/100)*1000)+b"] TJ "]
        insertion=b"".join(commands)
        data=content.streams[-content.page.xref]
        for event in sorted(events,key=lambda e:e.operator.start,reverse=True):
            new=serialized_event(event,selected,remove=True)
            if event is first:new=insertion+new
            data=data[:event.operator.start]+new+data[event.operator.end:]
        original=glyph_observations(content.page)
        untouched=Counter(signature(g) for i,g in enumerate(original) if i not in selected)
        def verify(document):
            actual=glyph_observations(document[resolved.page-1]);remaining=untouched.copy();new=[]
            for g in actual:
                key=signature(g)
                if remaining[key]:remaining[key]-=1
                else:new.append(g)
            if any(remaining.values()) or len(new)!=len(expected_new):raise PdfError("reflow changed unselected text or glyph count")
            for g,(char,x,y) in zip(new,expected_new):
                if g["unicode"]!=char or max(abs(g["origin"][0]-x),abs(g["origin"][1]-y))>.001 or g["glyph_id"]==0:
                    raise PdfError("reflow glyph output disagrees with PDF-font advance plan")
                rect=Rect(*g["bbox"])
                if not Rect(*content.page.rect).contains(rect,.001):raise PdfError("new text is outside the page")
                allow_clip(state,rect,content.page.transformation_matrix)
                for i,old in enumerate(original):
                    if i not in selected and rect.intersects(Rect(*old["bbox"]),.1):raise PdfError("new text collides with an unselected glyph")
                for info in content.page.get_image_info():
                    if rect.intersects(Rect(*info["bbox"]),.01):raise PdfError("new text intersects an image; background semantics not established")
                for drawing in content.page.get_drawings():
                    bounds=Rect(*drawing["rect"])
                    if drawing.get("fill") is not None:
                        if bounds.contains(resolved.bbox) and len(drawing["items"])==1 and drawing["items"][0][0]=="re" and bounds.contains(rect):continue
                        if rect.intersects(bounds,.01):raise PdfError("new text intersects a filled vector")
                    if drawing.get("color") is not None:
                        if len(drawing["items"])==1 and drawing["items"][0][0]=="re":
                            t=max(float(drawing.get("width",1))/2,.2)
                            edges=[Rect(bounds.x0-t,bounds.y0-t,bounds.x1+t,bounds.y0+t),Rect(bounds.x0-t,bounds.y1-t,bounds.x1+t,bounds.y1+t),Rect(bounds.x0-t,bounds.y0,bounds.x0+t,bounds.y1),Rect(bounds.x1-t,bounds.y0,bounds.x1+t,bounds.y1)]
                        else:edges=[bounds]
                        if any(rect.intersects(e,.01) for e in edges):raise PdfError("new text intersects a vector border/path")
            for link in content.page.get_links():
                if layout.bbox.intersects(Rect(*link["from"])):raise PdfError("reflow intersects a link")
            for a in content.page.annots() or []:
                if layout.bbox.intersects(Rect(*a.rect)):raise PdfError("reflow intersects an annotation")
            if font_fingerprints(document,resolved.page-1)!=font_fingerprints(content.document,resolved.page-1):raise PdfError("font resources changed")
            if len(document)!=len(content.document) or document.permissions!=content.document.permissions or document.metadata.get("encryption")!=content.document.metadata.get("encryption"):
                raise PdfError("page count/security changed")
        with pymupdf.open(source) as document:
            set_page_program(document,resolved.page-1,data);verify(document)
        publish_program(source,resolved.page-1,data,output,verify)
        from dataclasses import asdict
        return {"schema_version":1,"stage":4,"backend":"explicit-PDF-font-advance-reflow",
                "source_sha256":source_sha(source),"selection":manifest,"output":str(output),"after":replacement,
                "widths":asdict(resolved.widths),"font_reused":True,"font_substituted":False,
                "old_line_count":len(lines),"new_line_count":len(layout.lines),"lines":[asdict(l) for l in layout.lines],
                "new_bbox":asdict(layout.bbox),"advance_source":font.width_source,"font_xref":font.xref,
                "graphics_state":"original resource and active state at insertion; text/line matrices restored before remaining original program"}
    finally:content.close()
