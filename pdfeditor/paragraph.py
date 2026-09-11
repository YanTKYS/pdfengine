"""Reflow source-linked styled text with original and supplied font providers."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from io import BytesIO
import hashlib
import math

import pymupdf
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from pypdf.generic import NameObject

from .attributed import SourceParagraph, apply_edits
from .backend import PdfError
from .composition import (_check_obstacles, _observations, _pixels_equal,
                          _require_removal, _signature)
from .content_stream import PaintChar, multiply, number, patch_streams, serialized_event, translate
from .explicit_reflow import allow_clip, matrix_operator
from .model import Rect
from .pdf_save import font_fingerprints, program_pdf_bytes, publish_program
from .replay import compare_glyphs, ensure_destination
from .rich_layout import InlineGlyph, layout_attributed
from .shaped_font import ShapedFont, ShapedRun


def _name(value):
    stream = BytesIO()
    NameObject(value).write_to_stream(stream)
    return stream.getvalue()


class ParagraphShaper:
    def __init__(self, paragraph, units, fonts):
        self.paragraph, self.units, self.specs = paragraph, units, fonts
        self.text = "".join(u.text for u in units)
        self.fonts, self.source_fonts = {}, {}
        self.original_offsets = {id(unit):i for i,unit in enumerate(paragraph.units)}

    def font(self, ident):
        if ident not in self.fonts:
            if ident not in self.specs:
                raise PdfError(f"new text needs an explicit font for {ident}")
            spec = self.specs[ident]
            self.fonts[ident] = spec if isinstance(spec,ShapedFont) else ShapedFont(
                spec["path"], font_index=spec.get("font_index",0), variations=spec.get("variations"))
        return self.fonts[ident]

    def source_ink(self, unit, style):
        observation = unit.observation
        xref = style.event.state.font.xref
        if xref not in self.source_fonts:
            try:
                data = self.paragraph.content.document.extract_font(xref)[3]
                font = TTFont(BytesIO(data)) if data else None
                self.source_fonts[xref] = font
            except Exception:
                self.source_fonts[xref] = None
        font = self.source_fonts[xref]
        if font is not None:
            try:
                glyphs = font.getGlyphSet()
                pen = BoundsPen(glyphs)
                glyphs[font.getGlyphName(observation["glyph_id"])].draw(pen)
                if pen.bounds is None:
                    return None, "embedded_outline"
                a,b,c,d = pen.bounds
                sx,sy = style.size*style.horizontal_scale/font['head'].unitsPerEm, style.size/font['head'].unitsPerEm
                return Rect(a*sx,-d*sy+style.rise,c*sx,-b*sy+style.rise), "embedded_outline"
            except Exception:
                pass
        # Original codes still use the original renderer. Without a readable
        # program use its trace bbox conservatively, not a guessed new glyph.
        x,y = observation["origin"]
        a,b,c,d = observation["bbox"]
        return Rect(a-x,b-y+style.rise,c-x,d-y+style.rise), "conservative_trace_bbox"

    def shape(self, start, end):
        result, index = [], start
        while index < end:
            unit = self.units[index]
            style = self.paragraph.styles[unit.style_id]
            original = unit.retained
            if original is not None and original.char is not None:
                observation = original.observation
                if observation['glyph_id'] < 0:
                    raise PdfError("retained source cluster is not a single painted glyph")
                advance = original.char.advance * style.matrix[0]
                if index+1 < end:
                    following = self.units[index+1].retained
                    if (following is not None and following.char is not None
                            and original.source_index is not None and following.source_index is not None
                            and following.line == original.line
                            and self.original_offsets[id(following)] == self.original_offsets[id(original)]+1):
                        advance = following.observation['origin'][0] - observation['origin'][0]
                else:
                    advance -= style.tracking
                ink, bounds_source = self.source_ink(original,style)
                _, y0, _, y1 = observation['bbox']
                baseline = observation['origin'][1]
                result.append(InlineGlyph(unit.text,advance,ink,
                    max(0,baseline-y0-style.rise),max(0,y1-baseline+style.rise),
                    {"style_id":style.id,"source_index":original.source_index,
                     "code_witness":original.code_witness,
                     "provider":"original","resource":style.event.state.font.name,
                     "code":original.char.code,"glyph_id":observation['glyph_id'],
                     "font_name":observation['font'],"offset":(0,style.rise),
                     "bounds_source":bounds_source}))
                index += 1
                continue
            provider = unit.provider or unit.style_id
            stop = index+1
            while stop < end:
                next_unit = self.units[stop]
                if (next_unit.retained is not None and next_unit.retained.char is not None
                        or next_unit.style_id != unit.style_id
                        or (next_unit.provider or next_unit.style_id) != provider):
                    break
                stop += 1
            font = self.font(provider)
            run = font.shape(self.text[index:stop])
            sx, sy = style.size*style.horizontal_scale/font.upem, style.size/font.upem
            for position, glyph in enumerate(run.glyphs):
                dx,dy = glyph.x_offset*sx, -glyph.y_offset*sy+style.rise
                bounds = font.ink(glyph.gid)
                ink = (Rect(dx+bounds[0]*sx,dy-bounds[3]*sy,dx+bounds[2]*sx,dy-bounds[1]*sy)
                       if bounds is not None else None)
                advance = glyph.advance*sx + style.tracking + (style.word_spacing if glyph.text == ' ' else 0)
                if stop == end and position == len(run.glyphs)-1:
                    advance -= style.tracking
                result.append(InlineGlyph(glyph.text,advance,ink,
                    max(0,font.ascender*style.size-style.rise),max(0,-font.descender*style.size+style.rise),
                    {"style_id":style.id,"provider":provider,"source_index":None,
                     "glyph_id":glyph.gid,"shaped_glyph":glyph,"offset":(dx,dy),
                     "bounds_source":"provided_outline"}))
            index = stop
        return result

    def close(self):
        for font in self.source_fonts.values():
            if font is not None:
                font.close()


def edit_paragraph(source, output, snapshot, edits, *, fonts=None, width=None, x=None,
                   first_line_indent=None, max_bottom=None, min_line_height=None,
                   removal_output=None, element_snapshot=None, element_relations=None, anchor_spec=None, baseline=None,
                   preserve_empty=False, empty_style_id=None):
    output = ensure_destination(output,source)
    if removal_output:
        removal_output = ensure_destination(removal_output,source)
        if output == removal_output:
            raise PdfError("output and removal checkpoint must be distinct")
    from .logical_element import paragraph_from_snapshot, style_recipes
    paragraph = paragraph_from_snapshot(source,snapshot)
    shaper = None
    try:
        units = apply_edits(paragraph,snapshot,edits)
        empty_typing_style=None
        if preserve_empty and not units:
            if anchor_spec is not None:
                raise PdfError('empty element paint relations require explicit dormant decoration/ownership semantics')
            if empty_style_id is not None:
                empty_typing_style=empty_style_id
            elif hasattr(paragraph,'default_style_id'):
                empty_typing_style=paragraph.default_style_id
            elif len(paragraph.styles)==1:
                empty_typing_style=next(iter(paragraph.styles))
            else:
                raise PdfError('empty multi-style paragraph needs an explicit empty_style_id')
            if empty_typing_style not in paragraph.styles:
                raise PdfError('unknown empty paragraph typing style')
        anchored=None
        if anchor_spec is not None:
            if element_relations is not None:
                raise PdfError('anchored editing uses the fixed relations in its anchor specification')
            from .anchors import AnchoredPaintEdit
            anchored=AnchoredPaintEdit(source,paragraph,snapshot,element_snapshot,anchor_spec,edits)
        selected = set(paragraph.selection['glyph_ids'])
        resolved, content = paragraph.resolved, paragraph.content
        pno, first = resolved.page-1, paragraph.first
        state = first.state
        available = resolved.widths if width is None else resolved.widths.with_explicit(width)
        width = available.require_available_width()
        suggestion = snapshot['layout_suggestion']
        x = suggestion['x'] if x is None else x
        indent = suggestion['first_line_indent'] if first_line_indent is None else first_line_indent
        baseline = suggestion['baseline'] if baseline is None else baseline
        size = paragraph.styles[paragraph.units[0].style_id if paragraph.units else paragraph.default_style_id].size
        observed = min((b-a for a,b in zip(suggestion['base_baselines'],suggestion['base_baselines'][1:]) if b>a),default=size*1.4)
        leading = observed if min_line_height is None else min_line_height
        bottom = content.page.rect.height if max_bottom is None else max_bottom
        if not math.isfinite(bottom) or bottom <= baseline:
            raise PdfError("paragraph bottom must be finite and below the first baseline")
        bottom = min(bottom,content.page.rect.height)
        untouched = [g for i,g in enumerate(paragraph.observations) if i not in selected]
        virtual = -content.page.xref
        removed = patch_streams(content,paragraph.events,selected,remove=True)[virtual]
        replay = patch_streams(content,paragraph.events,selected,remove=False)[virtual]
        with pymupdf.open(stream=program_pdf_bytes(source,pno,replay),filetype='pdf') as doc:
            if not compare_glyphs(paragraph.observations,_observations(doc[pno]))['passed'] or not all(
                    _pixels_equal(content.document[i],doc[i]) for i in range(len(doc))):
                raise PdfError("source no-op failed; attributed editing is prohibited")
        with pymupdf.open(stream=program_pdf_bytes(source,pno,removed),filetype='pdf') as doc:
            _require_removal(untouched,doc[pno])
        shaper = ParagraphShaper(paragraph,units,fonts or {})
        layout = layout_attributed(shaper.text,shape=shaper.shape,x=x,baseline=baseline,width=width,
            min_line_height=leading,max_bottom=bottom,empty_ascent=size*.8,empty_descent=size*.2,
            first_line_indent=indent)
        resources, font_reports = {}, {}
        existing = content.pdf_page['/Resources'].get_object().get('/Font',{})
        for provider,font in shaper.fonts.items():
            glyphs = [g.glyph.payload['shaped_glyph'] for g in layout.glyphs
                      if g.glyph.payload['provider']==provider and g.glyph.payload['source_index'] is None]
            if not glyphs:
                continue
            resource = font.resource([ShapedRun('',tuple(glyphs))])
            n = 1
            while f'/PRF{n}' in existing or f'/PRF{n}' in resources:
                n += 1
            alias = f'/PRF{n}'
            resources[alias] = resource
            font_reports[provider] = {"resource":alias,"name":font.name,
                "source_sha256":font.source_sha256,"instance_sha256":font.instance_sha256,
                "subset_sha256":hashlib.sha256(resource.program).hexdigest(),
                "font_index":font.font_index,"variations":font.variations}
        inverse = ~(pymupdf.Matrix(*state.ctm)*content.page.transformation_matrix)
        inverse_ctm = tuple(~pymupdf.Matrix(*state.ctm))
        commands, plan, inks = [b' q 0 Tc 0 Tw 0 Ts '], [], []
        for placed in layout.glyphs:
            payload = placed.glyph.payload
            style = paragraph.styles[payload['style_id']]
            if payload['provider']!='original':
                alias = font_reports[payload['provider']]['resource']
                resource = resources[alias]
                code = resource.code(payload['shaped_glyph'])
                cid = int.from_bytes(code,'big')
            else:
                alias,code,cid = payload['resource'],payload['code'],None
            dx,dy = payload['offset']
            gx,gy = placed.x+dx,placed.baseline+dy
            point = pymupdf.Point(gx,gy)*inverse
            basis = multiply(style.matrix,inverse_ctm)[:4]
            fill_op,fill_values = style.event.state.fill
            commands.extend((_name(alias),b' ',number(style.event.state.size),b' Tf ',
                number(style.event.state.tz),b' Tz ',b' '.join(number(float(v)) for v in fill_values),
                b' '+fill_op.encode()+b' ',matrix_operator((*basis,point.x,point.y)),
                b'<'+code.hex().encode()+b'> Tj '))
            ink = placed.ink
            if ink is not None:
                if not Rect(x,0,x+width,bottom).contains(ink,.001) or not Rect(*content.page.rect).contains(ink,.001):
                    raise PdfError("styled glyph extends beyond the confirmed paragraph region")
                allow_clip(state,ink,content.page.transformation_matrix)
                inks.append(ink)
            plan.append({"unicode":placed.glyph.text,"style_id":style.id,"start":placed.start,"end":placed.end,
                "source_index":payload['source_index'],"provider":payload['provider'],
                "code_witness":payload.get('code_witness'),
                "font_resource":alias,"code":code.hex(),"cid":cid,"glyph_id":payload['glyph_id'],
                "origin":[gx,gy],"size":style.size,
                "trace_size":style.size*style.horizontal_scale,"color":style.color,
                "advance":placed.glyph.advance,"bounds_source":payload['bounds_source'],
                "nominal_pdf_width":(resources[alias].font.nominal_width(payload['glyph_id'])*1000/resources[alias].font.upem
                                     if cid is not None else None)})
        delta_advance = sum(a.advance if isinstance(a,PaintChar) else -float(a)/1000*state.size*state.tz/100 for a in first.atoms)
        after = translate(first.text_matrix,delta_advance,0)
        delta = multiply(after,tuple(~pymupdf.Matrix(*first.line_matrix)))
        if abs(delta[5])>.001 or any(abs(a-b)>1e-5 for a,b in zip(delta[:4],(1,0,0,1))):
            raise PdfError("cannot restore original text and line matrices")
        commands.extend((b' Q ',matrix_operator(first.line_matrix),
            b'['+number(-delta[4]/(state.size*state.tz/100)*1000)+b'] TJ '))
        mutations=[];empty_slot_offset=None
        for event in paragraph.events:
            new = serialized_event(event,selected,remove=True)
            if event is first:
                if preserve_empty and not units:
                    empty_slot_offset=event.operator.start+len(new)+1
                    new+=b' [] TJ '
                new += b''.join(commands)
            mutations.append((event.operator.start,event.operator.end,new))
        ink_bounds = Rect(x,baseline,x,baseline)
        for ink in inks:
            ink_bounds = ink_bounds.union(ink)
        if anchored is not None:
            anchored.plan(layout,inks,ink_bounds,Rect(x,0,x+width,bottom))
            mutations.extend(anchored.patches)
            removed=anchored.removal_program()
        elif element_snapshot is None:
            if element_relations is not None:
                raise PdfError("element relations need a source-bound element snapshot")
            _check_obstacles(content,selected,resolved,inks,ink_bounds)
        else:
            from .elements import check_paragraph_obstacles
            check_paragraph_obstacles(source,content,selected,resolved,inks,ink_bounds,
                                      element_snapshot,element_relations,paragraph_snapshot=snapshot)
        affected = resolved.bbox.union(anchored.bounds if anchored else ink_bounds)
        data=content.streams[virtual]
        for a,b,new in sorted(mutations,reverse=True):
            data=data[:a]+new+data[b:]
            if empty_slot_offset is not None and b<=first.operator.start:
                empty_slot_offset+=len(new)-(b-a)
        old_fonts = font_fingerprints(content.document,pno)
        def verify(document):
            if (len(document)!=len(content.document) or document.permissions!=content.document.permissions
                    or document.metadata.get('encryption')!=content.document.metadata.get('encryption')):
                raise PdfError("attributed edit changed page count or security")
            remaining = defaultdict(list)
            for g in untouched:
                remaining[_signature(g)].append(g)
            new,kept = [],[]
            for g in _observations(document[pno]):
                candidates = remaining[_signature(g)]
                match = next((i for i,old in enumerate(candidates) if compare_glyphs([old],[g])['passed']),None)
                if match is None:
                    new.append(g)
                else:
                    candidates.pop(match);kept.append(g)
            if any(remaining.values()) or not compare_glyphs(untouched,kept)['passed']:
                raise PdfError("attributed edit changed unselected glyphs")
            if ''.join(g['unicode'] for g in new) != ''.join(g['unicode'] for g in plan):
                raise PdfError("saved styled Unicode differs from the line plan")
            painted = [g for g in new if g['glyph_id']>=0]
            if len(painted)!=len(plan):
                raise PdfError("saved styled glyph count differs from the line plan")
            for actual,wanted in zip(painted,plan):
                if (actual['glyph_id']!=wanted['glyph_id'] or abs(actual['size']-wanted['trace_size'])>.002
                        or actual['paint_type']!=0 or actual['opacity']!=1 or actual['color']!=wanted['color']
                        or max(abs(a-b) for a,b in zip(actual['origin'],wanted['origin']))>.002):
                    raise PdfError("saved styled glyph ID, origin or paint differs from its plan")
                witness=wanted['source_index'] if wanted['source_index'] is not None else wanted['code_witness']
                if witness is not None and actual['font']!=paragraph.observations[witness]['font']:
                    raise PdfError("retained text no longer uses its original font")
            aliases = {name[1:] for name in resources}
            current = font_fingerprints(document,pno)
            if [f for f in current if f[0][3] not in aliases] != old_fonts:
                raise PdfError("an existing font resource changed")
            for alias,resource in resources.items():
                added = [f for f in current if f[0][3]==alias[1:]]
                if len(added)!=1 or added[0][1]!=hashlib.sha256(resource.program).hexdigest():
                    raise PdfError("new styled font differs from the verified subset")
            if not all(_pixels_equal(content.document[i],document[i],affected if i==pno else None) for i in range(len(document))):
                raise PdfError("styled edit changed pixels outside the affected region")
            if anchored:
                anchored.verify(document)
        publish_program(source,pno,data,output,verify,font_builders={k:v.build for k,v in resources.items()})
        if removal_output:
            def verify_removed(doc):
                _require_removal(untouched,doc[pno])
                if anchored:
                    anchored.verify(doc,removed=True)
            publish_program(source,pno,removed,removal_output,verify_removed)
        return {"schema_version":1,"backend":"attributed-source-and-shaped-fonts",
            "element_snapshot_sha256":element_snapshot.get('snapshot_sha256') if element_snapshot else None,
            "element_relations":element_relations,
            "anchors":anchored.report() if anchored else None,
            "snapshot_sha256":snapshot['snapshot_sha256'],"selection":paragraph.selection,
            "before":paragraph.text,"after":shaper.text,"composed_text":''.join(line.text for line in layout.lines),
            "logical_styles":[u.style_id for u in units],
            "empty_slot_offset":empty_slot_offset,"empty_typing_style_id":empty_typing_style,
            "empty_style_recipes":style_recipes(paragraph) if empty_typing_style is not None else None,
            "logical_origins":[shaper.original_offsets[id(u.retained)] if u.retained is not None else None for u in units],
            "styles":snapshot['styles'],"edits":edits,"fonts":font_reports,
            "widths":asdict(available),"x":x,"baseline":baseline,"first_line_indent":indent,"min_line_height":leading,
            "old_line_count":len(resolved.lines),"new_line_count":len(layout.lines),
            "lines":[{k:getattr(line,k) for k in ('text','start','end','x','baseline','width','ascent','descent')} for line in layout.lines],
            "glyph_plan":plan,"audit_bbox":asdict(affected),"source_replay_mupdf_passed":True,
            "retained_glyph_count":sum(g['source_index'] is not None for g in plan),
            "provided_font_glyph_count":sum(g['provider']!='original' for g in plan),
            "reused_code_glyph_count":sum(g['code_witness'] is not None for g in plan),
            "font_policy":"Retain original codes for unedited intervals; shape edited intervals using explicitly supplied fonts.",
            "mupdf_outside_pixels_equal":True,"independent_renderer_verified":False}
    finally:
        if shaper is not None:
            shaper.close()
        paragraph.close()
