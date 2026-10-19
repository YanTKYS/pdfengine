"""Explicit, persistent single-Span shortening with atomic leaf ActualText.

This is not B1 page-flow or B2 paragraph aggregation. The caller selects the
complete leaf and approves a fixed rectangle; only an internal deletion is
supported. Original glyphs, fonts, clip and all other owners stay authoritative.
"""
from copy import deepcopy
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


def _seal(value):
    value = deepcopy(value)
    value.pop('model_sha256', None)
    value['model_sha256'] = digest(value)
    return value


def _observe(source, selection, region):
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
        for i, glyph in enumerate(content.actual):
            box = Rect(*glyph['bbox'])
            if i not in selection['glyph_ids'] and rect.intersects(box):
                overlap = Rect(max(rect.x0, box.x0), max(rect.y0, box.y0),
                               min(rect.x1, box.x1), min(rect.y1, box.y1))
                if not resolved.bbox.contains(overlap):
                    raise PdfError('single Span rectangle adds foreign glyph capacity')
    finally:
        content.close()
    paragraph = inspect_paragraph(source, selection)
    if paragraph['text'] != proof['text'] or len(paragraph['styles']) != 1:
        raise PdfError('single Span needs one style and unambiguous text order')
    return proof, paragraph


def _plan(transaction, model, edits):
    page = transaction.page(model['selection']['page'])
    proof = model['witness']; paragraph = model['paragraph']; region = Rect(**model['region'])
    plan = plan_paragraph_edit(page, paragraph, edits, owner='single-span:' + str(proof['owner']['xref']),
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
    proof, paragraph = _observe(source, model['selection'], model['region'])
    if proof != model['witness'] or paragraph != model['paragraph']:
        raise PdfError('single Span current ownership or structure differs')
    return deepcopy(model)


def open_single_span(source, model):
    try:
        return dict(status='restored', state=_restore(source, model))
    except (PdfError, KeyError, TypeError, ValueError) as exc:
        return dict(status='needs_confirmation', reason=str(exc))


def edit_single_span(source, model, output, model_output, edits):
    """Shorten inside one leaf, or save a byte-identical-program no-op.

    Empty/inserted/replaced runs, cross-owner operations and free reflow are
    deliberately outside this initial contract. No new font is needed.
    """
    state = _restore(source, model)
    text = state['witness']['text']
    if edits:
        if (not isinstance(edits, list) or len(edits) != 1 or set(edits[0]) != {'start', 'end', 'text'}
                or type(edits[0]['start']) is not int or type(edits[0]['end']) is not int
                or not 0 < edits[0]['start'] < edits[0]['end'] < len(text) or edits[0]['text'] != ''):
            raise PdfError('single Span supports one internal deletion only')
    elif edits != []:
        raise PdfError('single Span edits must be a list')
    output, model_output = ensure_destination(output, source), ensure_destination(model_output, source)
    if output == model_output:
        raise PdfError('single Span needs distinct PDF and sidecar destinations')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.single-span-', dir=output.parent) as directory:
        root = Path(directory); target = root / 'span.pdf'
        with Transaction(source) as transaction:
            if edits:
                plan = _plan(transaction, state, edits)
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
        witness, paragraph = _observe(target, selection, state['region'])
        wanted = text if not edits else text[:edits[0]['start']] + text[edits[0]['end']:]
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
