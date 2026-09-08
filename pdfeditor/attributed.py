"""Source-linked Unicode intervals and explicit edits, separate from layout.

Retained text carries original codes and paint style. Inserted text carries a
style reference and a supplied font, never a guessed mapping into a subset.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from statistics import median

from uniseg.graphemecluster import grapheme_cluster_boundaries

from .backend import PdfError
from .composition import _canonical, _observations
from .content_stream import ContentPage, multiply
from .selection import resolve_selection


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@dataclass
class SourceStyle:
    id: str
    event: object
    size: float
    horizontal_scale: float
    tracking: float
    word_spacing: float
    rise: float
    matrix: tuple
    color: list
    font_name: str

    def export(self):
        return {"id":self.id, "font_resource":self.event.state.font.name,
                "font_name":self.font_name, "font_size":self.size,
                "horizontal_scale":self.horizontal_scale, "tracking":self.tracking,
                "word_spacing":self.word_spacing, "baseline_shift":self.rise,
                "fill":self.event.state.fill, "observed_color":self.color,
                "font_xref":self.event.state.font.xref}


@dataclass
class SourceUnit:
    text: str
    style_id: str
    source_index: int | None
    line: int
    char: object = None
    observation: dict | None = None


@dataclass
class EditUnit:
    text: str
    style_id: str
    retained: SourceUnit | None
    provider: str | None = None


class SourceParagraph:
    def __init__(self, source, selection, *, line_joiner=""):
        if line_joiner not in ("", " ", "\n"):
            raise PdfError("line joiner must be empty, a space, or a newline")
        self.selection = selection
        self.line_joiner = line_joiner
        self.resolved = resolve_selection(source, selection)
        self.content = ContentPage(source, self.resolved.page)
        try:
            selected = set(selection["glyph_ids"])
            self.events = self.content.selected_events(selected)
            self.first = min(self.events, key=lambda e:e.operator.start)
            self.observations = _observations(self.content.page)
            self.styles = {}
            self.units = []
            styles_by_key = {}
            mapping = {}
            clip = _canonical(self.first.state.clip)
            non_inline_state = _canonical(self.first.state.other)
            # Text remains in one paint context. Variable font/size/color/Tc
            # are inline attributes, while clip/blend/marked semantics are not.
            for event in self.events:
                state = event.state
                matrix = multiply(event.text_matrix, state.ctm)
                if (abs(matrix[1]) > 1e-5 or abs(matrix[2]) > 1e-5
                        or matrix[0] <= 0 or matrix[3] <= 0 or state.size <= 0 or state.tz <= 0):
                    raise PdfError("attributed composition requires positive horizontal text transforms")
                if state.tr != 0 or state.opacity != 1 or state.stroke_opacity != 1:
                    raise PdfError("attributed composition requires opaque fill text")
                if _canonical(state.clip) != clip:
                    raise PdfError("selected style spans have different active clips")
                if _canonical(state.other) != non_inline_state:
                    # The writer replays font, spacing, geometry and fill for
                    # each span under the first event's remaining state. The
                    # interpreter records other state conservatively; it does
                    # not prove differently expressed overprint, transfer,
                    # rendering-intent or marked-content states equivalent.
                    raise PdfError("selected style spans have different non-inline graphics state")
                if state.fill[0] not in ("g", "rg", "k"):
                    raise PdfError("attributed text requires a device gray/RGB/CMYK fill")
                for key, value in state.other.items():
                    if key.startswith("ExtGState:") and isinstance(value, dict):
                        if value.get("/SMask", "/None") != "/None" or value.get("/BM", "/Normal") != "/Normal":
                            raise PdfError("blend/mask semantics are not inline text styles")
                    if key == "marked_content" and any(s in str(value) for s in ("/ActualText", "/OC")):
                        raise PdfError("ActualText and optional-content spans need separate semantics")
                size = state.size * matrix[3]
                scale = matrix[0] / matrix[3] * state.tz / 100
                tracking = state.tc * matrix[0] * state.tz / 100
                word = state.tw * matrix[0] * state.tz / 100 if state.font.unit == 1 else 0
                if word and state.font.decode(b" ")[0][1] != " ":
                    raise PdfError("source word spacing is not associated with Unicode space")
                ids = [i for c in event.chars for i in c.source_orders if i in selected]
                observation = self.observations[ids[0]]
                key = digest([state.font.xref, state.font.name, size, scale, tracking, word,
                              -state.ts*matrix[3], state.fill, observation["color"]])
                if key not in styles_by_key:
                    ident = f"s{len(self.styles)}"
                    styles_by_key[key] = ident
                    self.styles[ident] = SourceStyle(ident,event,size,scale,tracking,word,
                        -state.ts*matrix[3],matrix,observation["color"],observation["font"])
                for char in event.chars:
                    for index in char.source_orders:
                        if index in selected:
                            if index in mapping:
                                raise PdfError("ambiguous attributed glyph provenance")
                            mapping[index] = (styles_by_key[key], char)
            for line_index, line in enumerate(self.resolved.lines):
                if line_index and line_joiner:
                    self.units.append(SourceUnit(line_joiner, self.units[-1].style_id, None, line_index))
                for glyph in line.glyphs:
                    style_id, char = mapping[glyph.source_order]
                    self.units.append(SourceUnit(glyph.text,style_id,glyph.source_order,line_index,
                                                 char,self.observations[glyph.source_order]))
            if len([u for u in self.units if u.source_index is not None]) != len(selected):
                raise PdfError("line reconstruction did not account for every selected glyph")
            self.text = "".join(u.text for u in self.units)
            if any(len(u.text) != 1 for u in self.units):
                raise PdfError("source clusters need an explicit multi-codepoint selection contract")
        except Exception:
            self.close()
            raise

    def close(self):
        self.content.close()

    def snapshot(self):
        spans = []
        for offset, unit in enumerate(self.units):
            if not spans or spans[-1]["style_id"] != unit.style_id:
                spans.append({"start":offset,"end":offset,"style_id":unit.style_id,"text":""})
            spans[-1]["end"] = offset+1
            spans[-1]["text"] += unit.text
        lefts = [min(g.origin[0] for g in line.glyphs) for line in self.resolved.lines]
        baselines = [median(u.observation['origin'][1]-self.styles[u.style_id].rise
                            for u in self.units if u.line==i and u.observation is not None)
                     for i in range(len(self.resolved.lines))]
        x = min(lefts)
        value = {"schema_version":1,"selection":self.selection,"line_joiner":self.line_joiner,
                 "text":self.text,"spans":spans,"styles":[s.export() for s in self.styles.values()],
                 "layout_suggestion":{"x":x,"first_line_indent":lefts[0]-x,
                    "baseline":baselines[0],"base_baselines":baselines,
                    "observed_baselines":[line.baseline for line in self.resolved.lines]},
                 "contract":"Review text, style intervals and line joining. Supply available width; edits use Unicode offsets, not PDF glyph IDs."}
        # JSON-normalize tuples so snapshots can be round-tripped directly.
        value = json.loads(json.dumps(value))
        value["snapshot_sha256"] = digest(value)
        return value


def inspect_paragraph(source, selection, *, line_joiner=""):
    paragraph = SourceParagraph(source,selection,line_joiner=line_joiner)
    try:
        return paragraph.snapshot()
    finally:
        paragraph.close()


def _require_complete_graphemes(paragraph, units):
    """Keep each resulting grapheme inside one supported shaping provenance.

    A supplied run may shape an entire new cluster. Original glyphs may retain
    an unchanged source cluster, but combining them with a new mark, a different
    style/provider, or source characters newly adjacent after deletion would
    need reshaping across the retention boundary. Source edit boundaries alone
    do not establish this property of the resulting Unicode string.
    """
    text = "".join(unit.text for unit in units)
    original_boundaries = {0, len(paragraph.text), *grapheme_cluster_boundaries(paragraph.text)}
    original_positions = {id(unit): index for index, unit in enumerate(paragraph.units)}
    start = 0
    for end in grapheme_cluster_boundaries(text):
        if end <= start:
            continue
        cluster = units[start:end]
        start = end
        if len(cluster) <= 1:
            continue
        if len({unit.style_id for unit in cluster}) != 1:
            raise PdfError("resulting grapheme crosses style boundaries; replace the complete grapheme with one style")
        retained = [unit.retained is not None and unit.retained.char is not None for unit in cluster]
        if any(retained) and not all(retained):
            raise PdfError("resulting grapheme crosses retained and supplied text; replace the complete grapheme")
        if all(retained):
            positions = [original_positions[id(unit.retained)] for unit in cluster]
            if (positions != list(range(positions[0], positions[0] + len(positions)))
                    or positions[0] not in original_boundaries
                    or positions[-1] + 1 not in original_boundaries
                    or len({unit.retained.line for unit in cluster}) != 1):
                raise PdfError("resulting grapheme joins previously separate source clusters; replace the complete grapheme")
        elif len({unit.provider or unit.style_id for unit in cluster}) != 1:
            raise PdfError("resulting grapheme crosses supplied font providers; use one font for the complete grapheme")


def apply_edits(paragraph, snapshot, edits):
    expected = paragraph.snapshot()
    if snapshot != expected:
        raise PdfError("paragraph snapshot changed or does not match the source")
    if not isinstance(edits, list):
        raise PdfError("edits must be a list of source Unicode ranges")
    boundaries = {0, len(paragraph.text), *grapheme_cluster_boundaries(paragraph.text)}
    ordered = []
    for edit in edits:
        if not isinstance(edit,dict):
            raise PdfError("each edit must be a Unicode range object")
        start, end, text = edit.get("start"), edit.get("end"), edit.get("text")
        if (type(start) is not int or type(end) is not int or start not in boundaries
                or end not in boundaries or not 0 <= start <= end <= len(paragraph.text)):
            raise PdfError("edit range must use ordered Unicode grapheme boundaries")
        if not isinstance(text,str):
            raise PdfError("replacement text must be Unicode")
        styles = {u.style_id for u in paragraph.units[start:end]}
        style = edit.get("style_id")
        if style is None:
            if len(styles) > 1:
                raise PdfError("an edit crossing style boundaries needs an explicit style_id")
            style = next(iter(styles)) if styles else paragraph.units[min(start,len(paragraph.units)-1)].style_id
        if style not in paragraph.styles:
            raise PdfError("unknown source style_id")
        provider = edit.get("font_id",style)
        ordered.append((start,end,text,style,provider))
    ordered.sort(key=lambda e:(e[0],e[1]))
    result, cursor = [], 0
    previous_start = None
    for start,end,text,style,provider in ordered:
        if start < cursor or start == previous_start:
            raise PdfError("edits must be disjoint, with one operation at each source position")
        result.extend(EditUnit(u.text,u.style_id,u) for u in paragraph.units[cursor:start])
        result.extend(EditUnit(c,style,None,provider) for c in text)
        cursor, previous_start = end, start
    result.extend(EditUnit(u.text,u.style_id,u) for u in paragraph.units[cursor:])
    _require_complete_graphemes(paragraph, result)
    return result
