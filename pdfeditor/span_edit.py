"""Explicit, persistent single-Span editing with atomic leaf ActualText.

This is not B1 page-flow or B2 paragraph aggregation. The caller selects the
complete leaf and approves a fixed rectangle. New glyphs require an explicitly
pinned original embedded program; no font-name lookup or substitution occurs.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile

from .attributed import digest, inspect_paragraph
from .backend import PdfError
from .content_stream import ContentPage
from .editable import _publish
from .model import Rect
from .paragraph import plan_paragraph_edit
from .replay import ensure_destination
from .selection import make_selection, resolve_selection, source_sha
from .source_ownership import single_span_witness
from .transaction import Plan, Transaction

SCHEMA = 'pdfengine-single-span-actualtext-1'

STYLE_FIELDS = ('font_size', 'horizontal_scale', 'tracking', 'tracking_provenance',
                'baseline_shift', 'baseline_shift_provenance', 'fill', 'observed_color')


def _appearance(style):
    return {k: style[k] for k in STYLE_FIELDS}


def _font_binding(source, paragraph):
    import pymupdf
    first = paragraph['styles'][0]
    with pymupdf.open(source) as document:
        program = document.extract_font(first['font_xref'])[3]
    return dict(source_resource=first['font_resource'], source_xref=first['font_xref'],
                source_program_sha256=hashlib.sha256(program).hexdigest(),
                appearance=_appearance(first), generated={})


def _verify_font_binding(source, paragraph, binding):
    """Only recorded writer resources may supplement the original style.

    Program hashes bind resources; every painted generated glyph additionally
    retains its GID, outline and advance in the original embedded provider.
    """
    from .shaped_font import ShapedFont
    from .pdf_save import embedded_font_sha256
    from .fonts import FontError
    from fontTools.ttLib import TTFont
    content = ContentPage(source, paragraph['selection']['page'])
    provider = None; parsed = {}
    try:
        fonts = content.pdf_page['/Resources']['/Font']
        original = fonts.get(binding['source_resource'])
        if original is None or original.idnum != binding['source_xref']:
            raise PdfError('single Span original font identity changed')
        data = content.document.extract_font(binding['source_xref'])[3]
        if hashlib.sha256(data).hexdigest() != binding['source_program_sha256']:
            raise PdfError('single Span original font program changed')
        provider = ShapedFont(data)
        expected_provider = dict(source_sha256=provider.source_sha256, font_index=0,
                                 variations={}, instance_sha256=provider.instance_sha256)
        for alias, record in binding['generated'].items():
            if (set(record) != {'provider', 'subset_sha256', 'basefont'} or record['provider'] != expected_provider
                    or alias == binding['source_resource']
                    or embedded_font_sha256(fonts.get(alias)) != record['subset_sha256']):
                raise PdfError('single Span generated font registry differs from the current resources')
        for style in paragraph['styles']:
            if _appearance(style) != binding['appearance']:
                raise PdfError('single Span generated resource changes the approved style')
            alias = style['font_resource']
            if alias == binding['source_resource']:
                continue
            record = binding['generated'].get(alias)
            if (record is None or record['provider']['source_sha256'] != binding['source_program_sha256']
                    or embedded_font_sha256(fonts.get(alias)) != record['subset_sha256']):
                raise PdfError('single Span font resource is not a recorded original-program subset')
            parsed[alias] = TTFont(BytesIO(content.document.extract_font(style['font_xref'])[3]))
        if parsed:
            from fontTools.pens.recordingPen import DecomposingRecordingPen
            selected = set(paragraph['selection']['glyph_ids'])
            for event in content.selected_events(selected):
                alias = event.state.font.name
                if alias not in parsed:
                    continue
                font = parsed[alias]; glyphs = font.getGlyphSet()
                for char in event.chars:
                    if not set(char.source_orders) & selected:
                        continue
                    gid = content.actual[char.source_orders[0]]['gid']
                    shaped = provider.shape(char.text).glyphs
                    if len(shaped) != 1 or shaped[0].text != char.text or shaped[0].gid != gid:
                        raise PdfError('single Span generated Unicode/GID differs from the pinned provider')
                    name = font.getGlyphName(gid)
                    pen = DecomposingRecordingPen(glyphs); glyphs[name].draw(pen)
                    if (pen.value != provider.outline(gid) or font['head'].unitsPerEm != provider.upem
                            or font['hmtx'][name][0] != provider.nominal_width(gid)
                            or abs(char.pdf_width - provider.nominal_width(gid) * 1000 / provider.upem) > 1e-6):
                        raise PdfError('single Span generated outline or advance differs from the pinned provider')
    except (KeyError, IndexError, AttributeError, FontError) as exc:
        raise PdfError('single Span invalid font binding: ' + str(exc)) from exc
    finally:
        if provider is not None:
            provider.font.close()
        for font in parsed.values():
            font.close()
        content.close()


@contextmanager
def _provider(source, state, edits, recipe):
    from .shaped_font import ShapedFont
    font = None
    try:
        needed = edits[0]['text'] if edits else ''
        if not needed:
            if recipe is not None:
                raise PdfError('single Span no-op/deletion does not take a font provider')
            yield None
            return
        if (not isinstance(recipe, dict) or set(recipe) != {'path', 'sha256', 'font_index'}
                or type(recipe['font_index']) is not int or recipe['font_index'] != 0):
            raise PdfError('single Span new glyphs require an explicit path, SHA and face-zero provider')
        binding = state.get('font_binding') or _font_binding(source, state['paragraph'])
        data = Path(recipe['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != recipe['sha256'] or recipe['sha256'] != binding['source_program_sha256']:
            raise PdfError('single Span provider must match the pinned original embedded program')
        font = ShapedFont(data)
        run = font.shape(needed)
        scalar_runs = [font.shape(c).glyphs for c in needed]
        if len(run.glyphs) != len(needed) or any(g.text != c or len(scalar) != 1 or scalar[0].gid != g.gid
                for g, c, scalar in zip(run.glyphs, needed, scalar_runs)):
            raise PdfError('single Span new glyphs require one unambiguous glyph per Unicode character')
        yield font
    except (OSError, TypeError, ValueError) as exc:
        if isinstance(exc, PdfError):
            raise
        raise PdfError('single Span provider qualification failed: ' + str(exc)) from exc
    finally:
        if font is not None:
            font.font.close()


def _seal(value):
    value = deepcopy(value)
    value.pop('model_sha256', None)
    value['model_sha256'] = digest(value)
    return value


def _observe(source, selection, region, font_binding=None, *, follower=frozenset()):
    import math
    if (not isinstance(region, dict) or set(region) != {'x0', 'y0', 'x1', 'y1'}
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in region.values())):
        raise PdfError('single Span requires a finite explicit rectangle')
    rect = Rect(**region)
    resolved = resolve_selection(source, selection)
    if rect.width <= 0 or rect.height <= 0:
        raise PdfError('single Span rectangle is empty')
    def contains(box):
        return rect.x0 <= box.x0 and rect.y0 <= box.y0 and rect.x1 >= box.x1 and rect.y1 >= box.y1
    if not contains(resolved.bbox) or len(resolved.lines) != 1:
        raise PdfError('single Span source exceeds the approved rectangle')
    content = ContentPage(source, selection['page'])
    try:
        proof = single_span_witness(content, selection['glyph_ids'])
        if rect.x0 < 0 or rect.y0 < 0 or rect.x1 > content.page.rect.width or rect.y1 > content.page.rect.height:
            raise PdfError('single Span rectangle exceeds the page')
        # Do not grant new foreign capacity. Nominal character boxes can
        # already overlap (e.g. a comma's empty advance box). Permit only
        # that existing overlap inside the selected source box; the writer's
        # per-glyph ink/foreign-box obstacle proof remains mandatory below.
        # Only the adjacent-Span contract names an approved follower, whose
        # translation is proved separately in the same Transaction.
        for i, glyph in enumerate(content.actual):
            box = Rect(*glyph['bbox'])
            if i not in selection['glyph_ids'] and i not in follower and rect.intersects(box):
                overlap = Rect(max(rect.x0, box.x0), max(rect.y0, box.y0),
                               min(rect.x1, box.x1), min(rect.y1, box.y1))
                if not resolved.bbox.contains(overlap):
                    raise PdfError('single Span rectangle adds foreign glyph capacity')
    finally:
        content.close()
    paragraph = inspect_paragraph(source, selection)
    if paragraph['text'] != proof['text'] or (font_binding is None and len(paragraph['styles']) != 1):
        raise PdfError('single Span needs one style and unambiguous text order')
    if font_binding is not None:
        _verify_font_binding(source, paragraph, font_binding)
    return proof, paragraph


def _plan(transaction, model, edits, provider=None):
    page = transaction.page(model['selection']['page'])
    proof = model['witness']; paragraph = model['paragraph']; region = Rect(**model['region'])
    requested = edits
    if edits:
        # Generated and native resource IDs are physical styles only. Their
        # identical approved appearance is re-proved on the current revision.
        ident = next(s['style_id'] for s in paragraph['spans'] if s['start'] <= edits[0]['start'] < s['end'])
        requested = [dict(edits[0], style_id=ident, font_id='single-span-provider')]
    plan = plan_paragraph_edit(page, paragraph, requested, owner='single-span:' + str(proof['owner']['xref']),
        fonts={'single-span-provider': provider} if provider is not None else None,
        width=region.x1 - paragraph['layout_suggestion']['x'], max_bottom=region.y1)
    if plan._report['new_line_count'] != 1:
        raise PdfError('single Span cannot wrap')
    if any(box.x0 < region.x0 or box.y0 < region.y0 or box.x1 > region.x1 or box.y1 > region.y1
           for box in plan.final_rects):
        raise PdfError('single Span edited ink exceeds the approved rectangle')
    # Only selected text operators may be rewritten; the original clip and
    # every wrapper/outside operator stay byte-identical in the same order.
    starts = {e.operator.start: e.operator.end for e in page.content.selected_events(set(proof['glyph_ids']))}
    if any(starts.get(m.start) != m.end for m in plan.mutations):
        raise PdfError('single Span mutation escapes its selected text operators')
    plan.single_span_witness = proof
    page.check()
    return plan


def propose_single_span(source, selection, *, region):
    """Read-only complete-owner proposal; region is explicit caller authority."""
    proof, paragraph = _observe(source, selection, region)
    value = dict(schema=SCHEMA, status='proposed', pdf_sha256=source_sha(source),
                 selection=selection, region=region, witness=proof, paragraph=paragraph)
    # Use the same writer's source replay/removal and unchanged layout proof.
    with Transaction(source) as transaction:
        plan = _plan(transaction, value, [])
        original = [transaction.page(selection['page']).content.actual[i] for i in proof['glyph_ids']]
        if len(plan.new_glyphs) != len(original) or any(
                a['unicode'] != b['unicode'] or a['glyph_id'] != b['gid']
                or max(abs(x-y) for x, y in zip(a['origin'], b['origin'])) > .002
                for a, b in zip(plan.new_glyphs, original)):
            raise PdfError('single Span no-edit layout does not reproduce the source')
        # Source replay alone proves reversibility, not the proposed writer's
        # no-edit layout. Render its actual in-memory program as well.
        import pymupdf
        from .composition import _pixels_equal
        from .pdf_save import program_pdf_bytes
        page = transaction.page(selection['page'])
        encoded = program_pdf_bytes(source, selection['page'] - 1, page.program.apply())
        with pymupdf.open(stream=encoded, filetype='pdf') as rendered:
            if not all(_pixels_equal(transaction.document[i], rendered[i]) for i in range(len(rendered))):
                raise PdfError('single Span no-edit writer pixels differ from the source')
    value['reproduction'] = 'source-replay-and-no-edit-writer-pixels-proved'
    return _seal(value)


def accept_single_span(source, proposal):
    current = propose_single_span(source, proposal['selection'], region=proposal['region'])
    if current != proposal:
        raise PdfError('single Span proposal is stale or modified')
    current['status'] = 'confirmed'
    return _seal(current)


def _restore(source, model):
    if (model.get('schema') != SCHEMA or model.get('status') != 'confirmed' or _seal(model) != model
            or source_sha(source) != model.get('pdf_sha256')):
        raise PdfError('single Span state needs current-revision confirmation')
    proof, paragraph = _observe(source, model['selection'], model['region'], model.get('font_binding'))
    if proof != model['witness'] or paragraph != model['paragraph']:
        raise PdfError('single Span current ownership or structure differs')
    return deepcopy(model)


def open_single_span(source, model):
    try:
        return dict(status='restored', state=_restore(source, model))
    except (PdfError, KeyError, TypeError, ValueError) as exc:
        return dict(status='needs_confirmation', reason=str(exc))


def _edits(text, edits):
    if edits:
        if (not isinstance(edits, list) or len(edits) != 1 or set(edits[0]) != {'start', 'end', 'text'}
                or type(edits[0]['start']) is not int or type(edits[0]['end']) is not int
                or not 0 < edits[0]['start'] <= edits[0]['end'] < len(text)
                or not isinstance(edits[0]['text'], str)
                or (edits[0]['start'] == edits[0]['end'] and not edits[0]['text'])):
            raise PdfError('single Span supports one internal edit only')
    elif edits != []:
        raise PdfError('single Span edits must be a list')


def plan_single_span_edit(source, model, edits, *, provider=None):
    """Read-only shaping/capacity/font proof for the requested internal edit."""
    state = _restore(source, model); _edits(state['witness']['text'], edits)
    with _provider(source, state, edits, provider) as font, Transaction(source) as transaction:
        return deepcopy(_plan(transaction, state, edits, font)._report)


def edit_single_span(source, model, output, model_output, edits, *, provider=None):
    """One internal replacement/insertion/deletion, or an unchanged-program no-op.

    New glyphs are shaped with a SHA-pinned exact original embedded program.
    No alternate provider, wrap, font resizing or owner redistribution occurs.
    """
    state = _restore(source, model)
    text = state['witness']['text']; _edits(text, edits)
    output, model_output = ensure_destination(output, source), ensure_destination(model_output, source)
    if output == model_output:
        raise PdfError('single Span needs distinct PDF and sidecar destinations')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.single-span-', dir=output.parent) as directory:
        root = Path(directory); target = root / 'span.pdf'
        with _provider(source, state, edits, provider) as font, Transaction(source) as transaction:
            if edits:
                plan = _plan(transaction, state, edits, font)
                if font is not None:
                    binding = state.setdefault('font_binding', _font_binding(source, state['paragraph']))
                    binding['generated'].update(deepcopy(plan.font_records))
            else:
                plan = transaction.page(state['selection']['page']).add(Plan('single-span-noop'))
            result = transaction.commit(target)
            result.close()
        # Reobserve the saved owner by MCID/identity, never by stale glyph IDs.
        from .marked_content import observe_marked_content
        observed = observe_marked_content(target, state['selection']['page'])
        scopes = [s for s in observed['scopes'] if s['association'].get('owner_reference') == state['witness']['owner']]
        if len(scopes) != 1:
            raise PdfError('single Span saved owner identity changed')
        lo, hi = scopes[0]['content_byte_range']
        content = ContentPage(target, state['selection']['page'])
        try:
            ids = [i for e in content.events if not e.invocation and lo <= e.operator.start < hi
                   for ch in e.chars for i in ch.source_orders]
        finally:
            content.close()
        selection = make_selection(target, state['selection']['page'], glyph_ids=ids,
                                   explicit_width=state['selection']['explicitly_supplied_width'])
        witness, paragraph = _observe(target, selection, state['region'], state.get('font_binding'))
        wanted = text if not edits else text[:edits[0]['start']] + edits[0]['text'] + text[edits[0]['end']:]
        if (witness['text'] != wanted or witness['owner'] != state['witness']['owner']
                or witness['mcid'] != state['witness']['mcid'] or witness['clip'] != state['witness']['clip']
                or witness['structure'] != state['witness']['structure']):
            raise PdfError('single Span saved semantics/structure/clip differ from the plan')
        state.update(pdf_sha256=source_sha(target), selection=selection, witness=witness, paragraph=paragraph)
        state = _seal(state)
        _restore(target, state)
        if source_sha(source) != model['pdf_sha256']:
            raise PdfError('single Span source changed during save')
        sidecar = root / 'span.json'
        sidecar.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        _publish(root, [(target, output), (sidecar, model_output)])
    return dict(status='saved', state=state, saves=1, after=wanted)
