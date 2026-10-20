"""One edit-intent entry point over the tagged-Span editing contracts.

The caller supplies a page glyph selection (concrete glyph occurrences) and
replacement text. ``normalize_edit`` removes the unchanged leading and
trailing graphemes and maps what remains to glyph occurrences and their
structure owner, so a selection crossing owners may still change only one.
The route is then decided by proved preconditions, not by trying routes:

* ``single-span``: the change lies in one leaf Span, which passes the
  single-Span contract inside its own observed extent (``span_edit``).
* ``adjacent-span``: only when that single-Span proof refuses for capacity
  alone, the same normalized change is proposed through ``adjacent_intent``.

Anything else is refused with its reason. Proposals never write; acceptance
and editing re-derive the normalization and route on the same revision.

Characters the owner's original embedded program lacks are reported
(``missing``) and refused, unless the caller passes an explicitly approved
``substitute`` (path, SHA-256, face index, ``relation='substituted'``). It
supplies only those characters, on the single-Span route only; it is never
chosen by the engine and never presented as the original font.
"""
import json
from pathlib import Path

from .adjacent_intent import (_coverage, _provider, accept_adjacent_intent, edit_adjacent_intent, line_band,
                              propose_adjacent_intent)
from .adjacent_span import SCHEMA as ADJACENT_STATE, open_adjacent_span
from .backend import PdfError
from .content_stream import ContentPage
from .layout import LayoutError, grapheme_cluster_boundaries
from .marked_content import observe_marked_content
from .selection import make_selection, resolve_selection, source_sha
from .span_edit import SCHEMA as SINGLE_STATE, _seal, accept_single_span, edit_single_span, missing_characters, \
    open_single_span, plan_single_span_edit, propose_single_span

SCHEMA = 'pdfengine-edit-intent-proposal-1'
SUBSTITUTE_WARNING = ('substitute glyphs come from a different font program; their appearance may differ '
                      'from the original font')
ACCEPTANCE = 'pdfengine-edit-intent-acceptance-1'
# Trace boxes are single precision; the single-Span rectangle gets only that
# rounding beyond the owner's observed extent.
PRECISION = .001
# Refusals that mean "the planned text does not fit", as opposed to structure,
# font or provenance refusals, which never lead to another route.
CAPACITY = ('Reflowed text exceeds', 'cannot wrap', 'edited ink exceeds the approved rectangle',
            'too narrow for a grapheme')


def _refuse(reason):
    raise PdfError('edit intent: ' + reason)


def _owners(source, page, content):
    """Each glyph's marked-content scope and the scope's ordered glyphs."""
    observed = observe_marked_content(source, page)
    if not observed['complete'] or content.errors:
        _refuse('incomplete marked-content observation')
    scope_of, ordered = {}, {}
    for scope in observed['scopes']:
        lo, hi = scope['content_byte_range']
        ids = [i for e in content.events if not e.invocation and lo <= e.operator.start < hi
               for ch in e.chars for i in ch.source_orders]
        ordered[scope['id']] = (scope, ids)
        for i in ids:
            scope_of[i] = scope['id']
    return scope_of, ordered


def _common(a, b):
    """Leading and trailing grapheme-aligned common lengths, not overlapping."""
    ab, bb = set(grapheme_cluster_boundaries(a)) | {0, len(a)}, set(grapheme_cluster_boundaries(b)) | {0, len(b)}
    prefix = max(k for k in range(min(len(a), len(b)) + 1) if a[:k] == b[:k] and k in ab and k in bb)
    limit = min(len(a), len(b)) - prefix
    suffix = max(k for k in range(limit + 1) if a[len(a) - k:] == b[len(b) - k:]
                 and len(a) - k in ab and len(b) - k in bb)
    return prefix, suffix


def normalize_edit(source, selection, replacement):
    """The smallest changed glyph run of one occurrence selection, and its owner.

    Unchanged leading/trailing graphemes are kept as source glyphs. A pure
    insertion is attached to the preceding selected glyph (or the following
    one at the selection start), so the change stays a replacement of a
    concrete glyph occurrence inside one owner.
    """
    if not isinstance(replacement, str) or '\n' in replacement or '\r' in replacement:
        _refuse('replacement must be single-line Unicode text')
    resolve_selection(source, selection)
    ids = list(selection['glyph_ids'])
    if not ids or ids != list(range(ids[0], ids[0] + len(ids))):
        _refuse('selection is not one contiguous glyph occurrence')
    page = selection['page']
    content = ContentPage(source, page)
    try:
        if any(len(content.actual[i]['unicode']) != 1 for i in ids):
            _refuse('a selected glyph maps to several characters; normalization would split it')
        text = ''.join(content.actual[i]['unicode'] for i in ids)
        if text == replacement:
            _refuse('the replacement equals the selected text')
        prefix, suffix = _common(text, replacement)
        start, end = prefix, len(text) - suffix
        inserted = replacement[prefix:len(replacement) - suffix]
        rule = 'changed characters only'
        if start == end:
            bounds = sorted(set(grapheme_cluster_boundaries(text)) | {0, len(text)})
            if start > 0:
                start = max(b for b in bounds if b < start)
                inserted = text[start:end] + inserted
                rule = 'insertion attached to the preceding selected glyph'
            else:
                end = min(b for b in bounds if b > end)
                inserted = inserted + text[start:end]
                rule = 'insertion attached to the following selected glyph'
        changed = ids[start:end]
        scope_of, ordered = _owners(source, page, content)
        scopes = {scope_of.get(i) for i in changed}
        value = dict(page=page, selection=selection, selected_text=text, replacement=replacement,
                     kept_prefix=text[:prefix], kept_suffix=text[len(text) - suffix:], changed_ids=changed,
                     changed_text=text[start:end], inserted=inserted, rule=rule, owner=None,
                     selection_owners=_selection_owners(ordered, scope_of, ids))
        if None in scopes or len(scopes) != 1:
            value['reason'] = 'the changed characters %s span %d owners' % (text[start:end], len(scopes))
            return value
        scope, owner_ids = ordered[scopes.pop()]
        association = scope['association']
        if association['status'] != 'tree_backed' or association.get('owner_reference') is None:
            value['reason'] = 'the changed characters have no tree-backed structure owner'
            return value
        offset = owner_ids.index(changed[0])
        value.update(owner=dict(owner=association['owner_reference'], mcid=association['mcid'],
                                text=''.join(content.actual[i]['unicode'] for i in owner_ids), glyph_ids=owner_ids,
                                fonts=sorted({e.state.font.xref for e in content.events for ch in e.chars
                                              if set(ch.source_orders) & set(owner_ids)})),
                     edit=dict(start=offset, end=offset + len(changed), text=inserted))
        return value
    finally:
        content.close()


def _selection_owners(ordered, scope_of, ids):
    """The marked-content owners of the selected glyphs, in content order, with their selected text."""
    result = []
    for i in ids:
        scope_id = scope_of.get(i)
        a = ordered[scope_id][0]['association'] if scope_id is not None else {}
        key = dict(mcid=a.get('mcid'), owner=a.get('owner_reference'))
        if not result or {k: result[-1][k] for k in ('mcid', 'owner')} != key:
            result.append(dict(key, glyph_ids=[]))
        result[-1]['glyph_ids'].append(i)
    return result


def _missing(data, text):
    """The original program's name and the characters of ``text`` it cannot shape (each once, in order)."""
    from .shaped_font import ShapedFont
    font = ShapedFont(data)
    try:
        return font.name, ''.join(missing_characters(font, text))
    finally:
        font.font.close()


def _single_route(source, found, work_dir, substitute=None):
    """Single-Span preconditions: leaf contract, edit position, font and capacity in the owner's own extent."""
    owner = found['owner']
    content = ContentPage(source, found['page'])
    try:
        own, y0, y1 = line_band(content.actual, owner['glyph_ids'])
        clip = next(e for e in content.events for ch in e.chars if set(ch.source_orders) & set(owner['glyph_ids'])).state.clip
        region = dict(x0=own.x0, y0=y0, x1=own.x1 + PRECISION, y1=y1)
        if len(clip) == 1 and clip[0]['path'][0]['operator'] == 're':
            x, y, w, h = clip[0]['path'][0]['args']
            height = content.page.rect.height
            region = dict(x0=max(region['x0'], min(x, x + w)), y0=max(region['y0'], height - max(y, y + h)),
                          x1=min(region['x1'], max(x, x + w)), y1=min(region['y1'], height - min(y, y + h)))
        recipe = font = None
        record = dict(region=region, region_basis='owner glyph extent, half gaps to neighbouring lines, clip',
                      edits=[found['edit']])
        if found['inserted']:
            try:
                recipe, font, data = _provider(content, dict(font_xrefs=owner['fonts']), work_dir)
                font['family'] = _coverage(data, found['inserted'])
            except PdfError as exc:
                family, missing = _missing(data, found['inserted']) if font is not None else (None, '')
                if substitute is None or not missing:
                    refused = dict(record, provider=None, font=None, status='refused', reason=str(exc), capacity=False)
                    return dict(refused, missing=missing) if missing else refused
                font.update(family=family, missing=missing)
    finally:
        content.close()
    record.update(provider=recipe, font=font)
    try:
        selection = make_selection(source, found['page'], glyph_ids=owner['glyph_ids'],
                                   explicit_width=region['x1'] - region['x0'])
        proposal = propose_single_span(source, selection, region=region)
        options = dict(provider=recipe) if substitute is None else dict(provider=recipe, substitute=substitute)
        plan = plan_single_span_edit(source, _seal(dict(proposal, status='confirmed')), [found['edit']], **options)
    except (PdfError, LayoutError) as exc:
        reason = str(exc)
        return dict(record, status='refused', reason=reason, capacity=any(c in reason for c in CAPACITY))
    glyphs = plan['glyph_plan']
    value = dict(record, status='qualified', proposal=proposal, after=plan['after'],
                 width=plan['lines'][0]['width'], width_limit=region['x1'] - plan['lines'][0]['x'],
                 retained=sum(g['provider'] == 'original' for g in glyphs),
                 new=sum(g['provider'] != 'original' for g in glyphs))
    if substitute is not None:
        chosen = plan['substitute']
        value['substitute'] = dict({k: chosen[k] for k in ('relation', 'sha256', 'font_index', 'family')},
                                   file=Path(chosen['path']).name, characters=chosen['characters'],
                                   glyphs=chosen['glyphs'], new=sum(g['provider'] == 'single-span-substitute'
                                                                    for g in glyphs),
                                   warning=SUBSTITUTE_WARNING)
    return value


def _derive(source, selection, replacement, work_dir, substitute=None):
    found = normalize_edit(source, selection, replacement)
    value = dict(schema=SCHEMA, pdf_sha256=source_sha(source), normalized=found, routes={})
    if substitute is not None:
        # The caller's explicit approval, kept verbatim so acceptance and
        # editing re-qualify the same bytes; never inferred by the engine.
        value['substitute'] = substitute
    if found['owner'] is None:
        value.update(status='refused', route=None, reason=found['reason'])
        return value
    single = _single_route(source, found, work_dir, substitute)
    value['routes']['single-span'] = single
    if single['status'] == 'qualified':
        value.update(status='proposed', route='single-span')
        return value
    if not single['capacity']:
        value.update(status='refused', route=None, reason='single-span route: ' + single['reason'])
        return value
    if substitute is not None:
        # The adjacent-Span contract pins one original program for both owners.
        value.update(status='refused', route=None, reason='single Span lacks capacity (%s); the adjacent-Span route '
                     'does not take a substitute font' % single['reason'])
        return value
    normalized = make_selection(source, found['page'], glyph_ids=found['changed_ids'])
    try:
        adjacent = propose_adjacent_intent(source, normalized, found['inserted'], work_dir=work_dir)
    except (PdfError, LayoutError) as exc:
        value['routes']['adjacent-span'] = dict(status='refused', reason=str(exc))
        value.update(status='refused', route=None,
                     reason='single Span lacks capacity (%s); adjacent Span: %s' % (single['reason'], exc))
        return value
    value['routes']['adjacent-span'] = dict(status=adjacent['status'], proposal=adjacent)
    value.update(status=adjacent['status'], route='adjacent-span')
    return value


def propose_edit_intent(source, selection, replacement, *, work_dir, substitute=None):
    """Read-only proposal. ``status`` is proposed, ambiguous (adjacent boundary choice) or refused.

    ``substitute`` is the caller's explicit approval of a font for the
    characters a refused proposal reported as ``missing``.
    """
    return _seal(_derive(source, selection, replacement, work_dir, substitute))


def _rederive(source, proposal, work_dir):
    if not isinstance(proposal, dict) or proposal.get('schema') != SCHEMA or _seal(proposal) != proposal:
        _refuse('acceptance needs an unmodified edit-intent proposal')
    if source_sha(source) != proposal['pdf_sha256']:
        _refuse('the proposal is stale for this revision')
    found = proposal['normalized']
    if _seal(_derive(source, found['selection'], found['replacement'], work_dir, proposal.get('substitute'))) != proposal:
        _refuse('the proposal is stale or was modified')


def accept_edit_intent(source, proposal, *, work_dir, option=None):
    """Explicit acceptance of the proposed route on the proposal's revision."""
    _rederive(source, proposal, work_dir)
    if proposal['route'] is None:
        _refuse('a refused proposal cannot be accepted: ' + proposal['reason'])
    if proposal['route'] == 'single-span':
        if option is not None:
            _refuse('the single-Span route has no boundary options')
        route = proposal['routes']['single-span']
        inner = accept_single_span(source, route['proposal'])
    else:
        inner = accept_adjacent_intent(source, proposal['routes']['adjacent-span']['proposal'],
                                       work_dir=work_dir, option=option)
    return _seal(dict(schema=ACCEPTANCE, status='accepted', pdf_sha256=proposal['pdf_sha256'],
                      proposal=proposal, route=proposal['route'], inner=inner))


def edit_edit_intent(source, acceptance, output, model_output, *, work_dir):
    """Save the accepted edit through its route's own editing API after re-proving it."""
    if (not isinstance(acceptance, dict) or acceptance.get('schema') != ACCEPTANCE
            or acceptance.get('status') != 'accepted' or _seal(acceptance) != acceptance):
        _refuse('editing requires an explicit, unmodified acceptance')
    proposal = acceptance['proposal']
    _rederive(source, proposal, work_dir)
    if acceptance['route'] != proposal['route'] or proposal['route'] is None:
        _refuse('the acceptance differs from its proposal')
    if proposal['route'] == 'single-span':
        route = proposal['routes']['single-span']
        model = acceptance['inner']
        expected = {k: v for k, v in route['proposal'].items() if k not in ('status', 'model_sha256')}
        if {k: v for k, v in model.items() if k not in ('status', 'model_sha256')} != expected:
            _refuse('the accepted single-Span state differs from the proposal')
        options = dict(provider=route['provider'])
        if proposal.get('substitute') is not None:
            options['substitute'] = proposal['substitute']
        result = edit_single_span(source, model, output, model_output, route['edits'], **options)
        result = dict(result, after=[result['after']])
    else:
        result = edit_adjacent_intent(source, acceptance['inner'], output, model_output, work_dir=work_dir)
    return dict(result, route=proposal['route'], normalized={k: proposal['normalized'][k] for k in
                ('selected_text', 'replacement', 'changed_text', 'inserted', 'rule')})


def open_edit_result(path, model_output):
    """Reopen a saved edit with the contract its sidecar names."""
    state = json.loads(Path(model_output).read_text(encoding='utf-8'))
    if state.get('schema') == SINGLE_STATE:
        return dict(open_single_span(path, state), contract='single-span')
    if state.get('schema') == ADJACENT_STATE:
        return dict(open_adjacent_span(path, state), contract='adjacent-span')
    return dict(status='needs_confirmation', reason='unknown sidecar schema', contract=None)
