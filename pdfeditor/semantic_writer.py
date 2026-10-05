"""L2: canonical island writer for one semantic shared-flow source slot.

Builds a private *candidate* (PDF + shared-flow-3 sidecar) from a verified
current bundle and an explicit request. Authority chain, nothing new:

* `open_semantic_flow` verifies the current bundle (owner, semantics, island);
* `plan_semantic_transition` is the only transition policy, and
  `require_authorized_payload` / `require_authorized_authority` pin the writer
  to its `next` payload and `next_authority` (current style/font authority);
* `source_ownership.owned_body` proves the byte span this slot owns, and one
  `source-output-rewrite` Mutation replaces only that body inside the existing
  `Transaction` (source revision, overlap, foreign glyph/paint, font ownership);
* after commit, `source_ownership.rebind` and the shared-flow binding update
  describe the candidate revision, and the sealed sidecar must pass
  `open_semantic_flow` again from disk, plus exact canonical-body equality and
  trace accuracy.

No public publication, pointer, receipt or history is produced (L3).
"""
from copy import deepcopy
from dataclasses import asdict
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import pymupdf

from .backend import PdfError
from .composition import _check_obstacles
from .content_stream import ContentPage
from .destination_style import bind_destination_styles
from .document_flow import _reseal
from .editable import bind_document_edit, document_edit_options
from .logical_element import paragraph_from_snapshot, style_recipes
from .model import Rect
from .proof_session import proof_session
from .selection import source_sha
from .shaped_font import ShapedFont
from .story_flow import LINE_KEYS, _boundaries
from .style_confirmation import confirm_paragraph
from .transaction import Plan, Transaction
from . import semantic_island as island
from . import semantic_layout as semantic
from . import shared_flow
from . import source_ownership as owned
from . import story_styles as styles

# Semantic record version 2 carries explicit current style/font authority
# (B-L2-S resolution), so every L1 reinterpretation is writable. A version 1
# record (PR #41/#42) binds style values and the font asset to the source
# registry; for it only position/label reinterpretations stay writable.
WRITABLE_REINTERPRETATIONS = semantic.REINTERPRETABLE
LEGACY_WRITABLE_REINTERPRETATIONS = frozenset({'word_spacing', 'edges', 'body_style_id'})


class SemanticIslandPlan(Plan):
    """One owned-body rewrite, exposing what `bind_editable` already consumes."""
    kind = 'semantic-island'

    def __init__(self, owner):
        super().__init__(owner)
        self.paragraph = self.anchored = self.first_mutation = self.font = None
        self.glyph_names, self.empty_style_names, self.slot = [], {}, False
        self._check, self._report, self.document_context = None, {}, None

    def check(self, page, *, exclude_glyphs, paint_changes):
        self._check(exclude_glyphs)

    def emitted_glyphs(self, identity):
        return identity.emitted_glyphs(self.first_mutation, self.glyph_names)

    def report(self, result):
        identity = result.identity(self.page.number)
        return dict(self._report,
                    empty_slot_offset=identity.program.anchor(self.first_mutation, 'slot') if self.slot else None,
                    empty_style_offsets={ident: identity.program.anchor(self.first_mutation, name)
                                         for ident, name in self.empty_style_names.items()},
                    byte_edits=[m.edit() for m in self.mutations], mutation_map=[m.record() for m in self.mutations],
                    anchors=None)

    def close(self):
        if self.font is not None:
            self.font.font.close()
            self.font = None
        if self.paragraph is not None:
            self.paragraph.close()


def _writable(plan):
    if plan['classification'] == 'N':
        raise PdfError('a reopen needs no candidate')
    if plan['classification'] == 'E':
        changed = set(plan['request']['changes'])
        writable = WRITABLE_REINTERPRETATIONS if plan['version'] != 1 else LEGACY_WRITABLE_REINTERPRETATIONS
        if changed - writable:
            raise PdfError('B-L2-S: semantic version 1 binds style/font to the source registry; explicit version 2 '
                           'confirmation is needed before reinterpreting: ' + ', '.join(sorted(changed - writable)))


def _page_top(content):
    box = [F(str(v)) for v in content.pdf_page.mediabox]
    if box[0] != 0 or box[1] != 0 or content.page.rotation:
        raise PdfError('semantic island needs an unrotated MediaBox anchored at the origin')
    return box[3]


def _logical(state, pid, request, text):
    """Shared-flow logical record for the authorized text (same projection as edit_shared_flow)."""
    p = state['paragraphs'][pid]
    typing = p['logical']['typing_style_id']
    edits = ([dict(start=request['start'], end=request['end'], text=request['text'], style_id=typing)]
             if request['operation'] == 'edit' else [])
    projected, spans, typing = styles.project(p, edits, typing)
    if projected != text:
        raise PdfError('authorized semantic text differs from the shared-flow projection')
    return dict(text=projected, style_spans=spans, typing_style_id=typing, boundaries=_boundaries(projected))


def _lines(plan, region, text):
    lines = []
    for line in plan['lines']:
        start, end = line['start'], line['end']
        lines.append(dict(text=text[start:end], start=start, end=end, x=float(F(region['x'])),
                          baseline=float(F(line['baseline'])), width=float(F(line['width'])),
                          ascent=float(F(line['ascent'])), descent=float(F(line['descent']))))
    return lines


def _current_paragraph(state, slot, payload, authority):
    """Paragraph style view the island is written for (source registry or current-style adapter)."""
    p = state['paragraphs'][slot['paragraph_id']]
    return (dict(p, style_registry=semantic.current_registry(state, slot, payload, authority)),
            semantic.current_confirmations(state, slot, payload, authority))


def _plan_island(page, state, sid, payload, authority, derived_plan, request, logical):
    """Serialize the canonical body and plan its single owned-body Mutation."""
    from .mutation import Mutation
    slot = state['slots'][sid]
    pid = slot['paragraph_id']
    p, confirmations = _current_paragraph(state, slot, payload, authority)
    content = page.content
    record = slot['source_output']
    snapshot = slot['binding']['paragraph']
    result = SemanticIslandPlan(sid)
    try:
        paragraph = paragraph_from_snapshot(page.source, snapshot, content=content)
        result.paragraph = paragraph
        paragraph = bind_destination_styles(paragraph, styles.render_styles(p), confirmations)
        result.paragraph = paragraph
        paragraph = confirm_paragraph(paragraph, confirmations)
        result.paragraph = paragraph
        typing = 'logical:' + p['logical']['typing_style_id']
        style = paragraph.styles[typing]
        start, end = owned.owned_body(content, record, snapshot)  # write authority for these bytes only
        selected = set(paragraph.selection['glyph_ids'])
        provider = p['style_registry'][p['logical']['typing_style_id']]['reflow_provider']
        font = ShapedFont(provider['path'])
        result.font = font  # kept open until the Transaction has built the subset
        if font.source_sha256 != payload['font']['sha']:
            raise PdfError('current font provider differs from the semantic font')
        emitted = derived_plan['emitted']
        chars = semantic.codebook(emitted)
        shaped, resource, identity = semantic.font_resource(font, emitted)
        alias = page.reserve_font_alias('PRF', consumed=frozenset(selected), retained=frozenset(),
                                        provider=identity, owner=sid)
        codes = {c: int.from_bytes(resource.code(shaped[c]), 'big') for c in chars}
        region = semantic._region(state, slot)
        fill = styles.properties(p['style_registry'][p['logical']['typing_style_id']])['fill']
        top = _page_top(content)
        data, anchors = island.body(payload['style'], emitted, alias=alias, codes=codes, fill=fill,
                                    page_top=top, origin=(region['x'], region['baseline']))
        mutation = Mutation(start, end, data, kind=owned.REWRITE, anchors=anchors, owner=sid)
        result.mutations.append(mutation)
        result.first_mutation = mutation
        style_ = payload['style']
        size, scale, rise = F(style_['font_size']), F(style_['horizontal_scale']), F(style_['rise'])
        sx, sy = size * scale / font.upem, size / font.upem
        new_glyphs, inks = [], []
        for n, glyph in enumerate(emitted):
            g = shaped[glyph['text']]
            x, y = (F(v) for v in glyph['origin'])
            bounds = font.ink(g.gid)
            if bounds is not None:
                a, b, c, d = bounds
                inks.append(Rect(float(x + a * sx), float(y - d * sy), float(x + c * sx), float(y - b * sy)))
            new_glyphs.append(dict(unicode=glyph['text'], style_id=typing, start=glyph['offset'], end=glyph['offset'] + 1,
                source_index=None, provider=typing, code_witness=None, font_resource=alias,
                code=f"{codes[glyph['text']]:04x}", cid=codes[glyph['text']], glyph_id=g.gid,
                origin=[float(x), float(y)], size=float(size), trace_size=float(size * scale), color=style.color,
                advance=float(F(glyph['advance'])), bounds_source='semantic_asset_outline', font=None,
                nominal_pdf_width=font.nominal_width(g.gid) * 1000 / font.upem))
        result.glyph_names = [f'glyph:{n}' for n in range(len(emitted))]
        if not emitted:
            result.slot = True
            result.empty_style_names = {typing: 'slot'}
        resolved = paragraph.resolved
        ink_bounds = Rect(float(F(region['x'])), float(F(region['baseline'])), float(F(region['x'])), float(F(region['baseline'])))
        for ink in inks:
            ink_bounds = ink_bounds.union(ink)
        result._check = lambda exclude: _check_obstacles(content, selected, resolved, inks, ink_bounds, exclude_glyphs=exclude)
        result.consumed = selected
        result.new_glyphs = new_glyphs
        result.final_rects = list(inks)
        result.affected = resolved.bbox.union(ink_bounds)
        result.font_builders = {alias: resource.build}
        result.font_subsets = {alias: hashlib.sha256(resource.program).hexdigest()}
        result.font_records = {alias: dict(subset_sha256=hashlib.sha256(resource.program).hexdigest(),
                                           basefont=resource.basefont, provider=identity)}
        text = payload['text']
        lines = _lines(derived_plan, region, text)
        result._report = {
            'schema_version': 1, 'backend': 'semantic-canonical-island', 'selection': paragraph.selection,
            'snapshot_sha256': snapshot['snapshot_sha256'], 'alignment': dict(value='left', provenance='generated_layout_policy'),
            'paragraph_continues': False, 'before': paragraph.text, 'after': text, 'composed_text': ''.join(l['text'] for l in lines),
            'logical_styles': [typing] * len(text),
            'empty_typing_style_id': typing if not emitted else None,
            'empty_style_recipes': ({k: v for k, v in style_recipes(paragraph).items() if k == typing} if not emitted else None),
            'logical_origins': [None] * len(text),
            'styles': [s for s in paragraph.export_styles() if s['id'] == typing],
            'fonts': {typing: dict(resource=alias, name=font.name, source_sha256=font.source_sha256,
                                   instance_sha256=font.instance_sha256, subset_sha256=result.font_subsets[alias],
                                   font_index=font.font_index, variations=font.variations)},
            'widths': dict(explicitly_supplied_width=state['regions'][slot['region_id']]['width']),
            'x': state['regions'][slot['region_id']]['x'], 'baseline': state['regions'][slot['region_id']]['first_baseline'],
            'first_line_indent': state['paragraph_policies'][pid]['first_line_indent'],
            'min_line_height': state['paragraph_policies'][pid]['min_line_height'],
            'lines': lines, 'glyph_plan': new_glyphs, 'edits': [],
            'retained_glyph_count': 0, 'provided_font_glyph_count': len(new_glyphs), 'reused_code_glyph_count': 0}
        return page.add(result), lines, data
    except Exception:
        result.close()
        raise


def _verify_candidate(pdf, sidecar, plan, body):
    """Fresh-from-disk verification: L1 opener, exact canonical body, trace accuracy."""
    opened = semantic.open_semantic_flow(pdf, sidecar)
    if opened['status'] != 'restored':
        raise PdfError('candidate does not verify: ' + opened['reason'])
    if (opened['semantic'] != plan['next'] or opened['derived'] != plan['next_derived']
            or opened['authority'] != plan['next_authority']):
        raise PdfError('candidate semantic state differs from the authorized plan')
    if not opened['island']['canonical']:
        raise PdfError('candidate island is not the exact canonical body')
    state = opened['state']
    record = state['slots'][opened['slot_id']]['source_output']
    page = state['regions'][state['slots'][opened['slot_id']]['region_id']]['page']
    content = ContentPage(pdf, page)
    try:
        span = owned.inventory(content.streams[-content.page.xref])[record['marker_id']]
        if content.streams[-content.page.xref][span[1]:span[2]] != body:
            raise PdfError('candidate island bytes differ from the serialized canonical body')
        events = [e for e in content.events if span[1] <= e.operator.start < span[2] and e.chars]
    finally:
        content.close()
    slot = state['slots'][opened['slot_id']]
    font = semantic._asset(state, slot, opened['semantic'], opened['authority'])
    try:
        _, exact = semantic._derive(state, slot, opened['semantic'], font)
    finally:
        font.font.close()
    with pymupdf.open(pdf) as document:
        trace = [c for span in document[page - 1].get_texttrace() for c in span['chars']]
    actual = {tuple(round(v, 3) for v in c[2]): chr(c[0]) for c in trace}
    error = 0.0
    if len(events) != len(exact['emitted']):
        raise PdfError('candidate island paints a different glyph count')
    for event, glyph in zip(events, exact['emitted']):
        origin = event.chars[0].origin
        if event.chars[0].text != glyph['text']:
            raise PdfError('candidate island glyph order differs from the plan')
        error = max(error, *(abs(float(F(w)) - v) for w, v in zip(glyph['origin'], origin)))
    if error > .002:
        raise PdfError('candidate island origins exceed the 0.002 pt placement contract')
    return opened, dict(max_origin_error=error, glyphs=len(events), trace_glyphs=len(actual))


@proof_session
def build_semantic_candidate(source, model, request, *, workspace, asset=None):
    """Write a verified private candidate PDF + shared-flow-3 sidecar under `workspace`.

    Never publishes; never modifies `source` or `model`. Returns the candidate
    directory, paths, authorized plan, verification and owner witnesses.
    """
    value = model if isinstance(model, dict) else json.loads(Path(model).read_text(encoding='utf-8'))
    opened = semantic.open_semantic_flow(source, value)
    if opened['status'] != 'restored':
        raise PdfError('current semantic bundle does not verify: ' + opened['reason'])
    plan = semantic.plan_semantic_transition(source, value, request, asset=asset)
    _writable(plan)
    payload, authority = deepcopy(plan['next']), deepcopy(plan['next_authority'])
    semantic.require_authorized_payload(plan, payload)
    semantic.require_authorized_authority(plan, authority)
    state = deepcopy(opened['state'])
    sid = opened['slot_id']
    slot = state['slots'][sid]
    pid = slot['paragraph_id']
    font = semantic._asset(state, slot, payload, authority)
    try:
        derived, exact = semantic._derive(state, slot, payload, font)
    finally:
        font.font.close()
    if derived != plan['next_derived']:
        raise PdfError('writer derivation differs from the authorized plan')
    logical = _logical(state, pid, plan['request'], payload['text'])
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    private = Path(tempfile.mkdtemp(prefix='semantic-candidate-', dir=root))
    try:
        target = private / 'document.pdf'
        initial = deepcopy(state)
        with Transaction(source) as transaction:
            if transaction.source_sha256 != state['pdf_sha256']:
                raise PdfError('source revision changed after verification')
            number = state['regions'][slot['region_id']]['page']
            page = transaction.page(number)
            page.own_fonts(state.get('generated_fonts', {}).get(str(number), {}))
            region = state['regions'][slot['region_id']]
            wanted_layout = dict(x=region['x'], width=region['width'], baseline=region['first_baseline'],
                                 max_bottom=region['bounds'][3], first_line_indent=state['paragraph_policies'][pid]['first_line_indent'],
                                 min_line_height=state['paragraph_policies'][pid]['min_line_height'])
            island_plan, lines, body = _plan_island(page, state, sid, payload, authority, exact, plan['request'], logical)
            p, _ = _current_paragraph(state, slot, payload, authority)
            island_plan.document_context = dict(fonts=styles.providers(p),
                options=document_edit_options(slot['binding'], wanted_layout), previous_state=slot['binding'])
            result = transaction.commit(target)
            try:
                if source_sha(source) != initial['pdf_sha256']:
                    raise PdfError('source changed during semantic candidate commit')
                state['paragraphs'][pid]['logical'].update(logical)
                edited, report = bind_document_edit(result.identity(number), island_plan, result)
                wanted = dict(text=payload['text'], style_spans=logical['style_spans'] if payload['text'] else [])
                # Current fragment binds to the current style view; the source
                # registry (creation evidence) itself is never rewritten.
                styles.bind_fragment(dict(shared_flow._style_state(state, pid), style_registry=p['style_registry']),
                                     sid, edited, wanted, generated=True)
                edited['boundaries'] = _boundaries(payload['text'])
                edited['layout_provenance'] = {k: ('explicitly_confirmed' if k == 'width' else
                                                   'generated-from-confirmed-shared-flow') for k in edited['layout']}
                slot['binding'] = _reseal(edited)
                slot['range'] = [0, len(payload['text'])]
                slot['render_end'] = len(payload['text'])
                slot['occupancy'] = dict(paragraph_id=pid, region_id=slot['region_id'],
                    baseline=lines[0]['baseline'] if lines else region['first_baseline'],
                    last_baseline=lines[-1]['baseline'] if lines else region['first_baseline'],
                    ascent=lines[0]['ascent'] if lines else state['paragraph_policies'][pid]['empty']['ascent'],
                    descent=lines[-1]['descent'] if lines else state['paragraph_policies'][pid]['empty']['descent'],
                    empty=not payload['text'])
                slot['style_binding']['pdf_sha256'] = source_sha(target)
                slot['source_output'] = owned.rebind(result.identity(number).after, initial['slots'][sid]['source_output'],
                                                     initial['slots'][sid]['source_output']['current']['entry_context_sha256'])
                state['generated_fonts'] = shared_flow._generated_fonts(initial, state, result, source_sha(target))
            finally:
                result.close()
        state.update(pdf_sha256=source_sha(target), allocation_provenance='generated-from-confirmed-shared-flow',
                     previous_model_sha256=initial['model_sha256'])
        state['physical_breaks'] = shared_flow._breaks(state)
        for current in state['slots'].values():
            current.pop('semantic', None)
        candidate = semantic._attach(state, sid, payload, authority, source=target)
        sidecar = private / 'shared-flow.json'
        sidecar.write_bytes((json.dumps(candidate, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        verified, accuracy = _verify_candidate(target, sidecar, plan, body)
        return dict(directory=private, pdf=target, sidecar=sidecar, plan=plan, verification=dict(
            island=verified['island'], accuracy=accuracy, owner_before=initial['slots'][sid]['source_output'],
            owner_after=verified['owner']), body=body)
    except Exception:
        shutil.rmtree(private, ignore_errors=True)
        raise
