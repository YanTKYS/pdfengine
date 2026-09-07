"""HarfBuzz glyph runs and fontTools subsets, independent of source PDF codes.

PDF /W contains nominal hmtx widths. Contextual HarfBuzz advances and offsets
belong to the positioned run, not to a global width entry for the glyph.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import hashlib
import math
from pathlib import Path
import re
import unicodedata

from fontTools import subset
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
import uharfbuzz as hb

from .fonts import FontError


@dataclass(frozen=True)
class ShapedGlyph:
    gid: int
    cluster: int
    text: str
    advance: int
    x_offset: int
    y_offset: int


@dataclass(frozen=True)
class ShapedRun:
    text: str
    glyphs: tuple[ShapedGlyph, ...]

    @property
    def advance(self):
        return sum(g.advance for g in self.glyphs)


class ShapedFont:
    def __init__(self, source: str | Path | bytes, *, font_index=0, variations=None):
        raw = source if isinstance(source, bytes) else Path(source).read_bytes()
        self.source_sha256 = hashlib.sha256(raw).hexdigest()
        self.font_index = font_index
        font = TTFont(BytesIO(raw), fontNumber=font_index, recalcTimestamp=False)
        if "glyf" not in font or "hmtx" not in font or "cmap" not in font:
            raise FontError("new font writer requires a TrueType outline font with a Unicode cmap")
        flags = font["OS/2"].fsType if "OS/2" in font else 0
        if flags & 0x202 or flags & 4 and not flags & 8:
            raise FontError("font embedding flags do not permit editable outline embedding")
        self.no_subset = bool(flags & 0x100)
        self.variations = {}
        requested = dict(variations or {})
        if "fvar" in font:
            axes = {a.axisTag: a for a in font["fvar"].axes}
            if set(requested) - axes.keys():
                raise FontError("unknown font variation axis")
            self.variations = {tag: requested.get(tag, a.defaultValue) for tag, a in axes.items()}
            if any(not math.isfinite(v) or not axes[k].minValue <= v <= axes[k].maxValue for k, v in self.variations.items()):
                raise FontError("font variation is outside its declared range")
            font = instantiateVariableFont(font, self.variations, inplace=True)
        elif requested:
            raise FontError("variation values supplied for a static font")
        buffer = BytesIO()
        font.save(buffer)
        self.data = buffer.getvalue()
        # Variable-font instancing may leave fractional coordinates in memory.
        # TrueType serialization rounds them. Shape, measure, inspect and subset
        # the same serialized instance, never the pre-serialization geometry.
        font = TTFont(BytesIO(self.data), recalcTimestamp=False)
        self.font = font
        self.upem = font["head"].unitsPerEm
        self.name = font["name"].getDebugName(6) or "ProvidedFont"
        self.ascender = font["hhea"].ascent / self.upem
        self.descender = font["hhea"].descent / self.upem
        self.order = font.getGlyphOrder()
        self.glyph_set = font.getGlyphSet()
        self.instance_sha256 = hashlib.sha256(self.data).hexdigest()
        self.hb_font = hb.Font(hb.Face(self.data))
        self.hb_font.scale = (self.upem, self.upem)
        hb.ot_font_set_funcs(self.hb_font)

    @lru_cache(maxsize=8192)
    def ink(self, gid):
        pen = BoundsPen(self.glyph_set)
        self.glyph_set[self.order[gid]].draw(pen)
        return pen.bounds

    def nominal_width(self, gid):
        return self.font["hmtx"][self.order[gid]][0]

    @lru_cache(maxsize=8192)
    def outline(self, gid):
        pen = DecomposingRecordingPen(self.glyph_set)
        self.glyph_set[self.order[gid]].draw(pen)
        return pen.value

    @lru_cache(maxsize=2048)
    def shape(self, text: str) -> ShapedRun:
        for c in text:
            if (unicodedata.category(c) in {"Cc", "Cf", "Cs"}
                    or unicodedata.bidirectional(c) in {"R", "AL", "AN"}):
                raise FontError("this composition path needs horizontal LTR text without control characters")
        if not text:
            return ShapedRun(text, ())
        buffer = hb.Buffer()
        buffer.add_str(text)  # UTF-32/code-point cluster indices in uharfbuzz.
        buffer.guess_segment_properties()
        if buffer.direction != "ltr":
            raise FontError("bidirectional/vertical paragraph composition is not implemented")
        hb.shape(self.hb_font, buffer)
        infos, positions = buffer.glyph_infos, buffer.glyph_positions
        clusters = [g.cluster for g in infos]
        if clusters != sorted(set(clusters)) or not clusters or clusters[0] != 0:
            raise FontError("multi-glyph or reordered clusters require a richer extraction contract")
        ends = clusters[1:] + [len(text)]
        result = []
        for info, pos, end in zip(infos, positions, ends):
            cluster_text = text[info.cluster:end]
            if info.codepoint == 0:
                raise FontError("provided font shapes a required character to .notdef")
            if not cluster_text.isspace() and self.ink(info.codepoint) is None:
                raise FontError("provided font has a glyph ID but no visible outline for required text")
            if pos.y_advance or pos.x_advance < 0:
                raise FontError("nonhorizontal or negative glyph advance is unsupported")
            result.append(ShapedGlyph(info.codepoint, info.cluster, cluster_text,
                                      pos.x_advance, pos.x_offset, pos.y_offset))
        return ShapedRun(text, tuple(result))

    def resource(self, runs):
        return FontResource(self, runs)


class FontResource:
    """One CID per (GID, Unicode cluster), with a verified TrueType subset."""
    def __init__(self, font: ShapedFont, runs):
        self.font = font
        keys = dict.fromkeys((g.gid, g.text) for run in runs for g in run.glyphs)
        if len(keys) > 65535:
            raise FontError("one font resource cannot exceed 65535 encoded clusters")
        self.cids = {key: i for i, key in enumerate(keys, 1)}
        prepared = TTFont(BytesIO(font.data), recalcTimestamp=False)
        if not font.no_subset:
            options = subset.Options()
            options.retain_gids = True
            options.notdef_outline = True
            options.recalc_timestamp = False
            worker = subset.Subsetter(options=options)
            worker.populate(gids={0, *(gid for gid, _ in keys)})
            worker.subset(prepared)
        # Subsetting is delegated, but preserving the shaping GIDs and nominal
        # metrics is an explicit contract checked before producing PDF objects.
        subset_glyphs = prepared.getGlyphSet()
        for gid, _ in keys:
            name = prepared.getGlyphName(gid)
            if prepared["hmtx"][name][0] != font.nominal_width(gid):
                raise FontError("font subsetting changed a planned glyph advance")
            pen = DecomposingRecordingPen(subset_glyphs)
            subset_glyphs[name].draw(pen)
            if pen.value != font.outline(gid):
                raise FontError("font subsetting changed a planned glyph outline")
        output = BytesIO()
        prepared.save(output)
        self.program = output.getvalue()
        tag = "".join(chr(65 + b % 26) for b in hashlib.sha256(self.program).digest()[:6])
        name = re.sub(r"[^A-Za-z0-9_.-]", "", font.name) or "ProvidedFont"
        self.basefont = (tag + "+" if not font.no_subset else "") + name

    def code(self, glyph):
        return self.cids[(glyph.gid, glyph.text)].to_bytes(2, "big")

    def build(self, writer):
        from pypdf.generic import (ArrayObject, ByteStringObject, DecodedStreamObject,
                                  DictionaryObject, FloatObject, NameObject, NumberObject)
        def dictionary(**values):
            return DictionaryObject({NameObject("/" + k): v for k, v in values.items()})
        def stream(data):
            value = DecodedStreamObject()
            value.set_data(data)
            return writer._add_object(value.flate_encode())
        def n(value):
            return FloatObject(float(value))
        scale = 1000 / self.font.upem
        head = self.font.font["head"]
        program = DecodedStreamObject()
        program.set_data(self.program)
        program[NameObject("/Length1")] = NumberObject(len(self.program))
        descriptor = writer._add_object(dictionary(
            Type=NameObject("/FontDescriptor"), FontName=NameObject("/" + self.basefont),
            Flags=NumberObject(4), FontBBox=ArrayObject([n(x * scale) for x in (head.xMin, head.yMin, head.xMax, head.yMax)]),
            ItalicAngle=n(self.font.font["post"].italicAngle), Ascent=n(self.font.ascender * 1000),
            Descent=n(self.font.descender * 1000), CapHeight=n(getattr(self.font.font["OS/2"], "sCapHeight", head.yMax) * scale),
            StemV=NumberObject(80), FontFile2=writer._add_object(program.flate_encode())))
        mapping = b"\0\0" + b"".join(gid.to_bytes(2, "big") for gid, _ in self.cids)
        widths = ArrayObject([NumberObject(1), ArrayObject([n(self.font.nominal_width(gid) * scale) for gid, _ in self.cids])])
        descendant = writer._add_object(dictionary(
            Type=NameObject("/Font"), Subtype=NameObject("/CIDFontType2"), BaseFont=NameObject("/" + self.basefont),
            CIDSystemInfo=dictionary(Registry=ByteStringObject(b"Adobe"), Ordering=ByteStringObject(b"Identity"), Supplement=NumberObject(0)),
            FontDescriptor=descriptor, DW=NumberObject(1000), W=widths, CIDToGIDMap=stream(mapping)))
        cmap = [b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n",
                b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n",
                b"/CMapName /PEUnicode def /CMapType 2 def\n1 begincodespacerange <0000> <FFFF> endcodespacerange\n"]
        items = list(self.cids.items())
        for start in range(0, len(items), 100):
            chunk = items[start:start + 100]
            cmap.append(f"{len(chunk)} beginbfchar\n".encode())
            for (_, text), cid in chunk:
                cmap.append(f"<{cid:04X}> <{text.encode('utf-16-be').hex().upper()}>\n".encode())
            cmap.append(b"endbfchar\n")
        cmap.append(b"endcmap CMapName currentdict /CMap defineresource pop end end\n")
        return writer._add_object(dictionary(Type=NameObject("/Font"), Subtype=NameObject("/Type0"),
            BaseFont=NameObject("/" + self.basefont), Encoding=NameObject("/Identity-H"),
            DescendantFonts=ArrayObject([descendant]), ToUnicode=stream(b"".join(cmap))))
