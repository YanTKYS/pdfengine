"""Reflow source-linked styled text with original and supplied font providers."""
from __future__ import annotations

from dataclasses import asdict
from io import BytesIO
import hashlib
import math

import pymupdf
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from pypdf.generic import NameObject

from .attributed import apply_edits
from .backend import PdfError
from .composition import _check_obstacles, _observations, _pixels_equal, _require_removal
from .content_stream import PaintChar, multiply, number, patch_streams, rewritten_event, translate
from .explicit_reflow import allow_clip, matrix_operator
from .model import Rect
from .mutation import Mutation
from .pdf_save import program_pdf_bytes
from .replay import compare_glyphs
from .rich_layout import InlineGlyph, layout_attributed
from .shaped_font import ShapedFont, ShapedRun
from .transaction import Plan, Transaction


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
                ascent, descent = max(0,baseline-y0-style.rise),max(0,y1-baseline+style.rise)
                # MuPDF trace bboxes normalize the font's vertical metrics to
                # the em size. Once new text becomes retained source text,
                # using only that observation can shrink generated leading.
                # Preserve the embedded program's layout metrics as well as
                # the previous conservative trace bounds; never reduce them.
                program = self.source_fonts.get(style.event.state.font.xref)
                if program is not None and 'hhea' in program and 'head' in program:
                    scale = style.size / program['head'].unitsPerEm
                    ascent = max(ascent,program['hhea'].ascent*scale-style.rise)
                    descent = max(descent,-program['hhea'].descent*scale+style.rise)
                result.append(InlineGlyph(unit.text,advance,ink,
                    ascent,descent,
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


def _layout_parameters(paragraph, snapshot, *, width=None, x=None, first_line_indent=None,
                       baseline=None, min_line_height=None, max_bottom=None):
    available = paragraph.resolved.widths
    if width is not None:
        available = available.with_explicit(width)
    suggestion = snapshot['layout_suggestion']
    size = paragraph.styles[paragraph.units[0].style_id if paragraph.units else paragraph.default_style_id].size
    observed = min((b-a for a,b in zip(suggestion['base_baselines'],suggestion['base_baselines'][1:]) if b>a),default=size*1.4)
    baseline = suggestion['baseline'] if baseline is None else baseline
    bottom = paragraph.content.page.rect.height if max_bottom is None else max_bottom
    if not math.isfinite(bottom) or bottom <= baseline:
        raise PdfError('paragraph bottom must be finite and below the first baseline')
    return available, dict(x=suggestion['x'] if x is None else x, baseline=baseline,
        width=available.require_available_width(),
        first_line_indent=suggestion['first_line_indent'] if first_line_indent is None else first_line_indent,
        min_line_height=observed if min_line_height is None else min_line_height,
        max_bottom=min(bottom,paragraph.content.page.rect.height),empty_ascent=size*.8,empty_descent=size*.2)


def plan_paragraph(source, snapshot, edits, *, fonts=None, render_styles=None, **layout_options):
    """Measure with the writer's shaper/layout, without authorizing any paint."""
    from .logical_element import paragraph_from_snapshot
    paragraph=paragraph_from_snapshot(source,snapshot)
    shaper=None
    try:
        from .destination_style import bind_destination_styles
        paragraph=bind_destination_styles(paragraph,render_styles)
        units=apply_edits(paragraph,snapshot,edits)
        _,options=_layout_parameters(paragraph,snapshot,**layout_options)
        shaper=ParagraphShaper(paragraph,units,fonts or {})
        layout=layout_attributed(shaper.text,shape=shaper.shape,**options)
        return dict(text=shaper.text,baseline=options['baseline'],
            last_baseline=layout.lines[-1].baseline if layout.lines else options['baseline'],
            lines=[{k:getattr(line,k) for k in ('start','end','baseline','width','ascent','descent')} for line in layout.lines])
    finally:
        if shaper is not None:shaper.close()
        paragraph.close()


class ParagraphPlan(Plan):
    """A guarded paragraph rewrite, planned on the source and applied by a transaction."""
    kind = 'edit'

    def __init__(self, owner=None):
        super().__init__(owner)
        self.paragraph = self.shaper = self.anchored = None
        self.first_mutation = None
        self.glyph_names = []
        self.slot = False
        self._check = None
        self._report = {}

    def check(self, page, *, exclude_glyphs, paint_changes):
        self._check(exclude_glyphs, paint_changes)

    def emitted_glyphs(self, identity):
        return identity.emitted_glyphs(self.first_mutation, self.glyph_names)

    def empty_slot_offset(self, identity):
        return identity.program.anchor(self.first_mutation, 'slot') if self.slot else None

    def report(self, result):
        identity = result.identity(self.page.number)
        return dict(self._report, empty_slot_offset=self.empty_slot_offset(identity),
                    byte_edits=[m.edit() for m in sorted(self.mutations, key=lambda m: m.start)],
                    mutation_map=[m.record() for m in sorted(self.mutations, key=lambda m: m.start)],
                    anchors=self.anchored.report() if self.anchored else None)

    def close(self):
        if self.shaper is not None:
            self.shaper.close()
        if self.paragraph is not None:
            self.paragraph.close()


def plan_paragraph_edit(page, snapshot, edits, *, fonts=None, width=None, x=None, first_line_indent=None,
                        max_bottom=None, min_line_height=None, element_snapshot=None, element_relations=None,
                        anchor_spec=None, baseline=None, preserve_empty=False, empty_style_id=None,
                        render_styles=None, owner=None):
    """Prove source facts and plan every byte mutation of one paragraph edit."""
    from .logical_element import paragraph_from_snapshot, style_recipes
    from .destination_style import bind_destination_styles
    source, content = page.source, page.content
    result = ParagraphPlan(owner)
    try:
        paragraph = paragraph_from_snapshot(source, snapshot, content=content)
        result.paragraph = paragraph
        paragraph = bind_destination_styles(paragraph, render_styles)
        result.paragraph = paragraph
        if anchor_spec is not None and any('runs' in e for e in edits):
            raise PdfError('attributed replacement runs need explicit decoration projection')
        units = apply_edits(paragraph, snapshot, edits)
        empty_typing_style = None
        if preserve_empty and not units:
            if anchor_spec is not None:
                raise PdfError('empty element paint relations require explicit dormant decoration/ownership semantics')
            if empty_style_id is not None:
                empty_typing_style = empty_style_id
            elif hasattr(paragraph, 'default_style_id'):
                empty_typing_style = paragraph.default_style_id
            elif len(paragraph.styles) == 1:
                empty_typing_style = next(iter(paragraph.styles))
            else:
                raise PdfError('empty multi-style paragraph needs an explicit empty_style_id')
            if empty_typing_style not in paragraph.styles:
                raise PdfError('unknown empty paragraph typing style')
        anchored = None
        if anchor_spec is not None:
            if element_relations is not None:
                raise PdfError('anchored editing uses the fixed relations in its anchor specification')
            from .anchors import AnchoredPaintEdit
            anchored = AnchoredPaintEdit(source, paragraph, snapshot, element_snapshot, anchor_spec, edits)
            result.anchored = anchored
        selected = set(paragraph.selection['glyph_ids'])
        resolved = paragraph.resolved
        pno, first = resolved.page - 1, paragraph.first
        state = first.state
        available, layout_options = _layout_parameters(paragraph, snapshot, width=width, x=x,
            first_line_indent=first_line_indent, baseline=baseline, min_line_height=min_line_height, max_bottom=max_bottom)
        x, baseline, width, indent, leading, bottom = (layout_options[k] for k in
            ('x', 'baseline', 'width', 'first_line_indent', 'min_line_height', 'max_bottom'))
        untouched = [g for i, g in enumerate(paragraph.observations) if i not in selected]
        virtual = -content.page.xref
        removed = patch_streams(content, paragraph.events, selected, remove=True)[virtual]
        replay = patch_streams(content, paragraph.events, selected, remove=False)[virtual]
        with pymupdf.open(stream=program_pdf_bytes(source, pno, replay), filetype='pdf') as doc:
            if not compare_glyphs(paragraph.observations, _observations(doc[pno]))['passed'] or not all(
                    _pixels_equal(content.document[i], doc[i]) for i in range(len(doc))):
                raise PdfError("source no-op failed; attributed editing is prohibited")
        with pymupdf.open(stream=program_pdf_bytes(source, pno, removed), filetype='pdf') as doc:
            _require_removal(untouched, doc[pno])
        shaper = ParagraphShaper(paragraph, units, fonts or {})
        result.shaper = shaper
        layout = layout_attributed(shaper.text, shape=shaper.shape, **layout_options)
        resources, font_reports = {}, {}
        for provider, font in shaper.fonts.items():
            glyphs = [g.glyph.payload['shaped_glyph'] for g in layout.glyphs
                      if g.glyph.payload['provider'] == provider and g.glyph.payload['source_index'] is None]
            if not glyphs:
                continue
            resource = font.resource([ShapedRun('', tuple(glyphs))])
            alias = page.reserve_font_alias('PRF')
            resources[alias] = resource
            font_reports[provider] = {"resource": alias, "name": font.name,
                "source_sha256": font.source_sha256, "instance_sha256": font.instance_sha256,
                "subset_sha256": hashlib.sha256(resource.program).hexdigest(),
                "font_index": font.font_index, "variations": font.variations}
        inverse = ~(pymupdf.Matrix(*state.ctm) * content.page.transformation_matrix)
        inverse_ctm = tuple(~pymupdf.Matrix(*state.ctm))
        commands, plan, inks, glyph_offsets = [b' q 0 Tc 0 Tw 0 Ts '], [], [], []
        length = len(commands[0])
        for placed in layout.glyphs:
            payload = placed.glyph.payload
            style = paragraph.styles[payload['style_id']]
            if payload['provider'] != 'original':
                alias = font_reports[payload['provider']]['resource']
                resource = resources[alias]
                code = resource.code(payload['shaped_glyph'])
                cid = int.from_bytes(code, 'big')
            else:
                alias, code, cid = payload['resource'], payload['code'], None
            dx, dy = payload['offset']
            gx, gy = placed.x + dx, placed.baseline + dy
            point = pymupdf.Point(gx, gy) * inverse
            basis = multiply(style.matrix, inverse_ctm)[:4]
            fill_op, fill_values = style.event.state.fill
            prefix = b''.join((_name(alias), b' ', number(style.event.state.size), b' Tf ',
                number(style.event.state.tz), b' Tz ', b' '.join(number(float(v)) for v in fill_values),
                b' ' + fill_op.encode() + b' ', matrix_operator((*basis, point.x, point.y))))
            commands.append(prefix)
            length += len(prefix)
            glyph_offsets.append(length)
            operand = b'<' + code.hex().encode() + b'> Tj '
            commands.append(operand)
            length += len(operand)
            ink = placed.ink
            if ink is not None:
                if not Rect(x, 0, x + width, bottom).contains(ink, .001) or not Rect(*content.page.rect).contains(ink, .001):
                    raise PdfError("styled glyph extends beyond the confirmed paragraph region")
                allow_clip(state, ink, content.page.transformation_matrix)
                inks.append(ink)
            witness = payload['source_index'] if payload['source_index'] is not None else payload.get('code_witness')
            plan.append({"unicode": placed.glyph.text, "style_id": style.id, "start": placed.start, "end": placed.end,
                "source_index": payload['source_index'], "provider": payload['provider'],
                "code_witness": payload.get('code_witness'),
                "font_resource": alias, "code": code.hex(), "cid": cid, "glyph_id": payload['glyph_id'],
                "origin": [gx, gy], "size": style.size,
                "trace_size": style.size * style.horizontal_scale, "color": style.color,
                "advance": placed.glyph.advance, "bounds_source": payload['bounds_source'],
                "font": paragraph.observations[witness]['font'] if witness is not None else None,
                "nominal_pdf_width": (resources[alias].font.nominal_width(payload['glyph_id']) * 1000 / resources[alias].font.upem
                                      if cid is not None else None)})
        delta_advance = sum(a.advance if isinstance(a, PaintChar) else -float(a) / 1000 * state.size * state.tz / 100 for a in first.atoms)
        after = translate(first.text_matrix, delta_advance, 0)
        delta = multiply(after, tuple(~pymupdf.Matrix(*first.line_matrix)))
        if abs(delta[5]) > .001 or any(abs(a - b) > 1e-5 for a, b in zip(delta[:4], (1, 0, 0, 1))):
            raise PdfError("cannot restore original text and line matrices")
        commands.extend((b' Q ', matrix_operator(first.line_matrix),
            b'[' + number(-delta[4] / (state.size * state.tz / 100) * 1000) + b'] TJ '))
        for event in paragraph.events:
            data, op_offset, chars = rewritten_event(event, selected, remove=True)
            anchors = {'rewritten': op_offset}
            if event is first:
                if preserve_empty and not units:
                    anchors['slot'] = len(data) + 1
                    data += b' [] TJ '
                    result.slot = True
                base = len(data)
                for n, offset in enumerate(glyph_offsets):
                    anchors[f'glyph:{n}'] = base + offset
                    result.glyph_names.append(f'glyph:{n}')
                data += b''.join(commands)
            mutation = Mutation(event.operator.start, event.operator.end, data, kind='text-edit',
                                anchors=anchors, chars=chars, owner=owner)
            result.mutations.append(mutation)
            if event is first:
                result.first_mutation = mutation
            removal, removal_offset, removal_chars = rewritten_event(event, selected, remove=True)
            result.removal_mutations.append(Mutation(event.operator.start, event.operator.end, removal, kind='text-remove',
                                                     anchors={'rewritten': removal_offset}, chars=removal_chars, owner=owner))
        ink_bounds = Rect(x, baseline, x, baseline)
        for ink in inks:
            ink_bounds = ink_bounds.union(ink)
        relations = element_relations
        if anchored is not None:
            anchored.plan(layout, inks, ink_bounds, Rect(x, 0, x + width, bottom), owner=owner)
            result.mutations.extend(anchored.mutations)
            result.removal_mutations.extend(anchored.removal_mutations)
            result.paint_changes.update(anchored.paint_changes)
            result.consumed_paths |= anchored.ids
            result.final_rects.extend(Rect(**s['bounds']) for s in anchored.segments)
            relations = anchor_spec.get('fixed_relations', [])
            check = anchored.check
        elif element_snapshot is None:
            if element_relations is not None:
                raise PdfError("element relations need a source-bound element snapshot")
            def check(exclude_glyphs, paint_changes):
                _check_obstacles(content, selected, resolved, inks, ink_bounds, exclude_glyphs=exclude_glyphs)
        else:
            from .elements import check_paragraph_obstacles
            def check(exclude_glyphs, paint_changes):
                check_paragraph_obstacles(source, content, selected, resolved, inks, ink_bounds,
                                          element_snapshot, element_relations, paragraph_snapshot=snapshot,
                                          exclude_glyphs=exclude_glyphs, paint_changes=paint_changes)
        result._check = check
        result.background_paths = {r['source_id'] for r in (relations or []) if r.get('relation') == 'backgrounds'}
        result.consumed = selected
        result.new_glyphs = plan
        result.final_rects.extend(inks)
        result.affected = resolved.bbox.union(anchored.bounds if anchored else ink_bounds)
        result.font_builders = {alias: resource.build for alias, resource in resources.items()}
        result.font_subsets = {alias: hashlib.sha256(resource.program).hexdigest() for alias, resource in resources.items()}
        result._report = {"schema_version": 1, "backend": "attributed-source-and-shaped-fonts",
            **({'destination_style_binding': paragraph.binding_report()} if render_styles is not None else {}),
            "element_snapshot_sha256": element_snapshot.get('snapshot_sha256') if element_snapshot else None,
            "element_relations": element_relations,
            "snapshot_sha256": snapshot['snapshot_sha256'], "selection": paragraph.selection,
            "before": paragraph.text, "after": shaper.text, "composed_text": ''.join(line.text for line in layout.lines),
            "logical_styles": [u.style_id for u in units],
            "empty_typing_style_id": empty_typing_style,
            "empty_style_recipes": style_recipes(paragraph) if empty_typing_style is not None else None,
            "logical_origins": [shaper.original_offsets[id(u.retained)] if u.retained is not None else None for u in units],
            "styles": paragraph.export_styles() if render_styles is not None else snapshot['styles'], "edits": edits, "fonts": font_reports,
            "widths": asdict(available), "x": x, "baseline": baseline, "first_line_indent": indent, "min_line_height": leading,
            "old_line_count": len(resolved.lines), "new_line_count": len(layout.lines),
            "lines": [{k: getattr(line, k) for k in ('text', 'start', 'end', 'x', 'baseline', 'width', 'ascent', 'descent')} for line in layout.lines],
            "glyph_plan": [{k: v for k, v in g.items() if k != 'font'} for g in plan], "audit_bbox": asdict(result.affected),
            "source_replay_mupdf_passed": True,
            "retained_glyph_count": sum(g['source_index'] is not None for g in plan),
            "provided_font_glyph_count": sum(g['provider'] != 'original' for g in plan),
            "reused_code_glyph_count": sum(g['code_witness'] is not None for g in plan),
            "font_policy": "Retain original codes for unedited intervals; shape edited intervals using explicitly supplied fonts.",
            "mupdf_outside_pixels_equal": True, "independent_renderer_verified": False}
        return page.add(result)
    except Exception:
        result.close()
        raise


def edit_paragraph(source, output, snapshot, edits, *, removal_output=None, **options):
    """Edit one paragraph as a single-plan transaction."""
    page_number = snapshot['selection']['page']
    with Transaction(source) as transaction:
        plan = plan_paragraph_edit(transaction.page(page_number), snapshot, edits, **options)
        result = transaction.commit(output, removal_output=removal_output)
        try:
            return plan.report(result)
        finally:
            result.close()
