"""Resource-preserving operator replay, gated by actual deletion and no-op fidelity."""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
import os
from pathlib import Path
import tempfile

import pymupdf

from .backend import PdfError
from .content_stream import ContentPage, digest, patch_streams
from .selection import resolve_selection, source_sha
from .pdf_save import publish_program, font_fingerprints


def glyph_observations(page):
    result=[]
    for span in page.get_texttrace():
        for c in span["chars"]:
            result.append({"unicode":chr(c[0]),"glyph_id":c[1],"origin":list(c[2]),"bbox":list(c[3]),
                           "advance_bbox":c[3][2]-c[3][0],"font":span["font"],"size":span["size"],
                           "direction":list(span["dir"]),"paint_type":span["type"],
                           "color":list(span["color"]),"opacity":span["opacity"],
                           "wmode":span.get("wmode"),"layer":span.get("layer")})
    return result


def compare_glyphs(expected,actual,tolerance=.001):
    max_origin=max_size=max_bbox=0.0
    errors=[]
    if len(expected)!=len(actual):errors.append("glyph_count")
    for index,(e,a) in enumerate(zip(expected,actual)):
        for key in ("unicode","glyph_id","font","direction","paint_type","color","opacity","wmode","layer"):
            if e[key]!=a[key]:errors.append(f"{index}:{key}")
        max_origin=max(max_origin,max(abs(x-y) for x,y in zip(e["origin"],a["origin"])))
        max_bbox=max(max_bbox,max(abs(x-y) for x,y in zip(e["bbox"],a["bbox"])))
        max_size=max(max_size,abs(e["size"]-a["size"]))
    return {"passed":not errors and max(max_origin,max_bbox,max_size)<=tolerance,
            "expected_count":len(expected),"actual_count":len(actual),"errors":errors[:30],
            "max_origin_error_pt":max_origin,"max_bbox_error_pt":max_bbox,
            "max_font_size_error_pt":max_size,"tolerance_pt":tolerance}


def set_page_program(document,page_number,data):
    xref=document.get_new_xref();document.update_object(xref,"<<>>")
    document.update_stream(xref,data)
    document[page_number].set_contents(xref)
    return xref


def ensure_destination(path,source):
    path=Path(path).resolve()
    if path==Path(source).resolve() or path.exists():raise PdfError("output must be a new path distinct from the source")
    return path


def publish(document,destination,verify=None):
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix=".replay-",suffix=".pdf",dir=destination.parent);os.close(fd)
    try:
        # Remove unreferenced old streams without renumbering the reused fonts.
        document.save(temporary,garbage=1,deflate=True,encryption=pymupdf.PDF_ENCRYPT_KEEP)
        if verify:
            with pymupdf.open(temporary) as check:verify(check)
        os.link(temporary,destination)
    finally:Path(temporary).unlink(missing_ok=True)


def no_op_replay(source,output,manifest,*,removal_output=None):
    output=ensure_destination(output,source)
    if removal_output:removal_output=ensure_destination(removal_output,source)
    resolved=resolve_selection(source,manifest)
    selected=set(manifest["glyph_ids"])
    page_number=resolved.page-1
    content=ContentPage(source,resolved.page)
    try:
        events=content.selected_events(selected)
        styles={(e.state.font.xref,e.state.size,e.state.fill,e.state.tr,e.state.opacity) for e in events}
        if len(styles)!=1:raise PdfError("manual range contains multiple styles or paint modes; select a single-style range")
        original=glyph_observations(content.page)
        expected=[g for i,g in enumerate(original) if i not in selected]
        virtual=-content.page.xref
        removed=patch_streams(content,events,selected,remove=True)[virtual]
        replayed=patch_streams(content,events,selected,remove=False)[virtual]
        before_pixels=content.page.get_pixmap(dpi=144,alpha=False).samples
        before_fonts=font_fingerprints(content.document,page_number)
        with pymupdf.open(source) as document:
            set_page_program(document,page_number,removed)
            removal_audit=compare_glyphs(expected,glyph_observations(document[page_number]))
            if not removal_audit["passed"]:raise PdfError("operator removal failed to preserve nonselected glyphs: "+str(removal_audit))
            # Keep the removal checkpoint for evaluation, but only publish it
            # after the complete replay has passed its fidelity checks.
            set_page_program(document,page_number,replayed)
            replay_audit=compare_glyphs(original,glyph_observations(document[page_number]))
            pixel_equal=before_pixels==document[page_number].get_pixmap(dpi=144,alpha=False).samples
            if not replay_audit["passed"] or not pixel_equal:
                raise PdfError("no-op replay did not reproduce source glyphs/pixels; later stages are prohibited")
            def verify(check):
                if len(check)!=len(content.document):raise PdfError("page count changed")
                if check.metadata.get("encryption")!=content.document.metadata.get("encryption") or check.permissions!=content.document.permissions:
                    raise PdfError("security settings changed")
                if font_fingerprints(check,page_number)!=before_fonts:raise PdfError("font resources changed")
                if not compare_glyphs(original,glyph_observations(check[page_number]))["passed"]:raise PdfError("saved glyphs changed")
                for i in range(len(check)):
                    if check[i].get_pixmap(dpi=144,alpha=False).samples!=content.document[i].get_pixmap(dpi=144,alpha=False).samples:
                        raise PdfError("saved no-op pixels changed")
            publish_program(source,page_number,replayed,output,verify)
        if removal_output:
            def verify_removal(check):
                if not compare_glyphs(expected,glyph_observations(check[page_number]))["passed"]:
                    raise PdfError("saved removal checkpoint changed unselected glyphs")
            publish_program(source,page_number,removed,removal_output,verify_removal)
        return {"schema_version":1,"backend":"resource-preserving-text-operators","stage":1,
                "source_sha256":source_sha(source),"selection":manifest,"output":str(output),
                "selected_glyphs":len(selected),"selected_events":len(events),
                "observed_content_width":resolved.widths.observed_content_width,
                "inferred_available_width":None,"explicitly_supplied_width":resolved.widths.explicitly_supplied_width,
                "removal_audit":removal_audit,"replay_audit":replay_audit,
                "mupdf_144dpi_all_pages_identical":True,"font_resources_identical":True,
                "graphics_state_policy":"Replay at the original byte location; original q/Q, clip paths, text state, matrices, paint order and resource dictionaries are retained.",
                "save_backend":"pypdf full rewrite; preserve resource values and encryption; collect unreachable old streams",
                "source_program_sha256":digest(content.streams[virtual]),"removed_program_sha256":digest(removed),
                "replayed_program_sha256":digest(replayed),"events":[e.report() for e in events],
                "selected_before":[original[i] for i in sorted(selected)]}
    finally:content.close()
