"""Human-correctable selections bound to the exact source PDF, independent of boxes."""
from __future__ import annotations
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import pymupdf

from .backend import PdfError, extract_page
from .inference import _horizontal_lines
from .model import WidthConstraint


def source_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def observation_lines(model):
    return _horizontal_lines([g for g in model.glyphs if abs(g.direction[0]-1)<.001 and abs(g.direction[1])<.001],infer_spaces=False)


def page_observation_sha(model):
    return hashlib.sha256(json.dumps({"page":model.page,"glyphs":[asdict(g) for g in model.glyphs]},
                                    ensure_ascii=False,sort_keys=True).encode("utf-8")).hexdigest()


def inspect_selection_source(path,page=1):
    with pymupdf.open(path) as doc:model=extract_page(doc,page-1)
    lines=[]
    for i,line in enumerate(observation_lines(model),1):
        line_id=f"p{page}-l{i}"
        lines.append({"id":line_id,"text":line.text,"bbox":asdict(line.bbox),"baseline":line.baseline,
                      "glyph_ids":[g.source_order for g in line.glyphs if g.source_order>=0],
                      "runs":[{"id":f"{line_id}-r{j}","text":r.text,"bbox":asdict(r.bbox),
                               "glyph_ids":[g.source_order for g in r.glyphs if g.source_order>=0]}
                              for j,r in enumerate(line.runs,1)]})
    return {"schema_version":1,"source_sha256":source_sha(path),"page":page,
            "page_observation_sha256":page_observation_sha(model),
            "lines":lines,"glyphs":[asdict(g) for g in model.glyphs],
            "inferred_available_width":None,
            "note":"Line grouping is an observation aid. Review bbox/preview; add or exclude lines/runs/glyphs. No Paragraph or TextBox is selected implicitly."}


def make_selection(path,page=1,*,line_ids=(),run_ids=(),glyph_ids=(),exclude_line_ids=(),explicit_width=None):
    observation=inspect_selection_source(path,page)
    line_map={l["id"]:l for l in observation["lines"]}
    run_map={r["id"]:r for l in observation["lines"] for r in l["runs"]}
    chosen=set(glyph_ids)
    for key in line_ids:
        if key not in line_map:raise PdfError(f"unknown line {key}")
        chosen.update(line_map[key]["glyph_ids"])
    for key in run_ids:
        if key not in run_map:raise PdfError(f"unknown run {key}")
        chosen.update(run_map[key]["glyph_ids"])
    for key in exclude_line_ids:
        if key not in line_map:raise PdfError(f"unknown line {key}")
        chosen.difference_update(line_map[key]["glyph_ids"])
    result={"schema_version":1,"source_sha256":observation["source_sha256"],"page":page,
            "page_observation_sha256":observation["page_observation_sha256"],
            "glyph_ids":sorted(chosen),"explicitly_supplied_width":explicit_width,
            "inferred_available_width":None}
    resolve_selection(path,result)
    return result


@dataclass
class ResolvedSelection:
    page: int
    glyphs: list
    lines: list
    bbox: object
    widths: WidthConstraint


def resolve_selection(path,manifest):
    if manifest.get("schema_version")!=1:raise PdfError("unsupported selection schema")
    if manifest.get("source_sha256")!=source_sha(path):raise PdfError("selection source SHA-256 does not match")
    page=manifest.get("page")
    if type(page)!=int or page<1:raise PdfError("selection page must be a positive integer")
    with pymupdf.open(path) as doc:model=extract_page(doc,page-1)
    if manifest.get("page_observation_sha256")!=page_observation_sha(model):raise PdfError("selection page observation hash does not match")
    ids=manifest.get("glyph_ids",[])
    if not ids or any(type(i)!=int or not 0<=i<len(model.glyphs) for i in ids):raise PdfError("selection needs valid observed glyph IDs")
    if len(ids)!=len(set(ids)):raise PdfError("duplicate selected glyph IDs")
    glyphs=[model.glyphs[i] for i in sorted(ids)]
    if any(abs(g.direction[0]-1)>.001 or abs(g.direction[1])>.001 for g in glyphs):raise PdfError("selection is not horizontal text")
    bbox=glyphs[0].bbox
    for g in glyphs[1:]:bbox=bbox.union(g.bbox)
    # A manually supplied JSON number is explicit input, not inferred evidence.
    if manifest.get("inferred_available_width") is not None:raise PdfError("manual selections may supply explicit width, not claim inferred width")
    widths=WidthConstraint(bbox.width,None,manifest.get("explicitly_supplied_width"))
    return ResolvedSelection(page,glyphs,observation_lines(type("Model",(),{"glyphs":glyphs})()),bbox,widths)


def adjust_selection(path,manifest,*,add_lines=(),remove_lines=(),explicit_width=None):
    resolve_selection(path,manifest)
    return make_selection(path,manifest["page"],line_ids=add_lines,glyph_ids=manifest["glyph_ids"],
                          exclude_line_ids=remove_lines,
                          explicit_width=manifest.get("explicitly_supplied_width") if explicit_width is None else explicit_width)
