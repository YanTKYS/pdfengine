"""Stages 2/3: reuse original glyph slots; no unknown-width reflow."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path

import pymupdf

from .backend import PdfError
from .content_stream import ContentPage, PaintChar, number
from .fonts import validate_simple_text
from .replay import ensure_destination, glyph_observations, compare_glyphs, publish, set_page_program
from .selection import resolve_selection, source_sha
from .pdf_save import publish_program, font_fingerprints


def require_stage1(source,manifest,report):
    if (not report or not report.get("stage1_passed") or not report.get("poppler_stage1_passed")
            or not report.get("paint_state_compare_passed") or not report.get("independent_text_equal")
            or report.get("source_sha256")!=source_sha(source) or report.get("selection")!=manifest):
        raise PdfError("a passing Stage 1 report for this exact source and selection is required")
    replayed=Path(report.get("output",""))
    if not replayed.is_file() or source_sha(replayed)!=report.get("replayed_sha256"):
        raise PdfError("Stage 1 replay artifact is missing or changed")


def edit_slots(source,output,manifest,replacement,*,stage,stage1_report):
    output=ensure_destination(output,source)
    require_stage1(source,manifest,stage1_report)
    validate_simple_text(replacement)
    if "\n" in replacement:raise PdfError("slot edits do not interpret new line breaks")
    if stage not in {2,3}:raise PdfError("slot editing supports Stages 2 and 3 only")
    resolved=resolve_selection(source,manifest);selected=set(manifest["glyph_ids"])
    order=[g.source_order for line in resolved.lines for g in line.glyphs]
    if set(order)!=selected or len(order)!=len(selected):raise PdfError("ambiguous reading-order slots")
    if stage==2 and len(replacement)!=len(order):raise PdfError("Stage 2 slot replacement must keep the glyph count")
    if stage==3 and not len(replacement)<len(order):raise PdfError("Stage 3 requires a shorter replacement")
    content=ContentPage(source,resolved.page)
    try:
        events=content.selected_events(selected)
        fonts={e.state.font.xref for e in events}
        if len(fonts)!=1 or any(e.state.tr not in (0,1) for e in events):raise PdfError("slot editing needs one font and one paint per character")
        font=events[0].state.font
        codes=font.encode_available(replacement)
        code_by_id=dict(zip(order,codes));text_by_id=dict(zip(order,replacement))
        for event in events:
            for atom in event.chars:
                for i in atom.source_orders:
                    if text_by_id.get(i)==atom.text:code_by_id[i]=atom.code
        before=glyph_observations(content.page)
        patches=[]
        for event in events:
            items=[]
            for atom in event.atoms:
                if not isinstance(atom,PaintChar):items.append(number(atom));continue
                hit=selected.intersection(atom.source_orders)
                if not hit:items.append(b"<"+atom.code.hex().encode()+b">");continue
                i=next(iter(hit))
                scale=event.state.size*event.state.tz/100
                if i not in code_by_id:
                    items.append(number(-atom.advance/scale*1000));continue
                code=code_by_id[i];new_width=font.decode(code)[0][2]
                advance=(new_width/1000*event.state.size+event.state.tc+
                         (event.state.tw if code==b" " else 0))*event.state.tz/100
                if abs(advance-atom.advance)>.001:
                    raise PdfError("new glyph advance differs from the original slot; explicit-width reflow is required")
                items.append(b"<"+code.hex().encode()+b">")
                if advance!=atom.advance:items.append(number((advance-atom.advance)/scale*1000))
            prefix=b""
            if event.operator.name=='"':prefix=number(event.state.tw)+b" Tw "+number(event.state.tc)+b" Tc T* "
            elif event.operator.name=="'":prefix=b"T* "
            patches.append((event.operator.start,event.operator.end,prefix+b"["+b" ".join(items)+b"] TJ"))
        data=content.streams[-content.page.xref]
        for start,end,new in sorted(patches,reverse=True):data=data[:start]+new+data[end:]
        def verify(doc):
            actual=glyph_observations(doc[resolved.page-1]);expected=[];positions=[]
            for i,g in enumerate(before):
                if i not in selected:expected.append(g);positions.append((False,g));continue
                if i in code_by_id:
                    changed=deepcopy(g);changed["unicode"]=text_by_id[i]
                    expected.append(changed);positions.append((True,changed))
            if len(actual)!=len(expected):raise PdfError("changed glyph count does not match slot plan")
            for (changed,e),a in zip(positions,actual):
                if changed:
                    if a["glyph_id"]==0 and not a["unicode"].isspace():raise PdfError("replacement maps to .notdef glyph")
                    e=deepcopy(e);e["glyph_id"]=a["glyph_id"]
                if not compare_glyphs([e],[a])["passed"]:raise PdfError("slot result differs from expected Unicode, origin, metrics, or paint")
            if len(doc)!=len(content.document) or doc.permissions!=content.document.permissions or doc.metadata.get("encryption")!=content.document.metadata.get("encryption"):
                raise PdfError("page count or security changed")
            if font_fingerprints(doc,resolved.page-1)!=font_fingerprints(content.document,resolved.page-1):raise PdfError("font resources changed")
        with pymupdf.open(source) as doc:
            set_page_program(doc,resolved.page-1,data);verify(doc)
            publish_program(source,resolved.page-1,data,output,verify)
        return {"schema_version":1,"stage":stage,"backend":"resource-preserving-slots",
                "source_sha256":source_sha(source),"selection":manifest,"output":str(output),
                "before":"".join(before[i]["unicode"] for i in order),"after":replacement,
                "font_reused":True,"font_substituted":False,"selected_font_resource":font.name,"font_xref":font.xref,
                "old_glyph_count":len(order),"new_glyph_count":len(replacement),
                "positions_policy":"Retain original observed slots; reject changed advance. No inferred width or reflow.",
                "verification":"Reopened output: glyph IDs nonzero, Unicode/positions/metrics/paint, nonselected glyphs, resources, security, page count"}
    finally:content.close()
