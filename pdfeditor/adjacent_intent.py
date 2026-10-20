"""Edit-intent proposals for the explicit adjacent-Span contract.

The caller supplies only a page glyph selection (concrete glyph occurrences,
never a text search) and replacement text. Owners, the following Span, an
optional whitespace follower, owner rectangles, the pinned provider and the
owner boundary are derived from current-revision evidence and then proved by
the existing adjacent-Span engine. Derived values remain a proposal: nothing
is written until the exact proposal is explicitly accepted on the same
revision, and accepting never widens the adjacent-Span ownership contract.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from .adjacent_span import (accept_adjacent_span, edit_adjacent_span, plan_adjacent_span_edit,
                            propose_adjacent_span)
from .backend import PdfError
from .content_stream import ContentPage
from .layout import LayoutError, _safe_boundary, line_break_boundaries
from .marked_content import _reference, observe_marked_content
from .model import Rect
from .selection import make_selection, resolve_selection, source_sha
from .span_edit import _seal

SCHEMA = 'pdfengine-adjacent-intent-proposal-1'
ACCEPTANCE = 'pdfengine-adjacent-intent-acceptance-1'
# Finite boundary search: at most this many first-owner tail characters move.
MAX_RELOCATED = 16


def _refuse(reason):
    raise PdfError('adjacent intent: ' + reason)


def _discover(source, selection):
    """Owner, following sibling and whitespace follower from structure evidence only."""
    resolve_selection(source, selection)
    page = selection['page']
    observed = observe_marked_content(source, page)
    content = ContentPage(source, page)
    try:
        if not observed['complete'] or content.errors:
            _refuse('incomplete marked-content observation')
        ordered, owner_of = {}, {}
        for scope in observed['scopes']:
            lo, hi = scope['content_byte_range']
            ids = [i for e in content.events if not e.invocation and lo <= e.operator.start < hi
                   for ch in e.chars for i in ch.source_orders]
            ordered[scope['id']] = ids
            for i in ids:
                owner_of[i] = scope
        chosen = list(selection['glyph_ids'])
        if not chosen or len(set(chosen)) != len(chosen):
            _refuse('select at least one distinct glyph occurrence')
        if any(i not in owner_of for i in chosen):
            _refuse('selected glyphs lie outside marked content')
        scopes = {owner_of[i]['id'] for i in chosen}
        if len(scopes) != 1:
            _refuse('selection crosses owners')
        scope = owner_of[chosen[0]]
        ids = ordered[scope['id']]
        positions = sorted(ids.index(i) for i in chosen)
        if positions != list(range(positions[0], positions[-1] + 1)):
            _refuse('selection is not one contiguous glyph occurrence')
        if positions[0] == 0:
            _refuse('selection must lie inside the owner, after its first glyph')
        association = scope['association']
        owner = association.get('owner_reference')
        if association['status'] != 'tree_backed' or owner is None:
            _refuse('selected glyphs have no tree-backed structure owner')

        def scopes_of(reference):
            return [s for s in observed['scopes'] if s['association'].get('owner_reference') == reference]

        if len(scopes_of(owner)) != 1:
            _refuse('ambiguous owner: it has several marked-content items')
        reader = content.reader
        parent = reader.get_object(owner['xref']).get('/P')
        kids = parent.get_object().get('/K') if parent is not None else None
        kids = [_reference(k) for k in (kids if isinstance(kids, list) else [])]
        if owner not in kids:
            _refuse('owner is not an ordered child of its parent')
        index = kids.index(owner)
        if index + 1 >= len(kids):
            _refuse('no following owner in the same parent')
        following = scopes_of(kids[index + 1])
        if len(following) != 1:
            _refuse('ambiguous continuation: the following sibling has %d marked-content items' % len(following))

        def text(s):
            return ''.join(content.actual[i]['unicode'] for i in ordered[s['id']])

        follower, follower_note = None, 'no sibling after the following owner'
        if index + 2 < len(kids):
            after = scopes_of(kids[index + 2])
            if len(after) == 1 and ordered[after[0]['id']] and not text(after[0]).strip(' '):
                follower = dict(owner=kids[index + 2], mcid=after[0]['association'].get('mcid'),
                                glyph_ids=ordered[after[0]['id']], text=text(after[0]))
                follower_note = 'whitespace leaf after the following owner'
            else:
                follower_note = 'the next sibling is not a single whitespace leaf'
        first = dict(owner=owner, mcid=association['mcid'], glyph_ids=ids, text=text(scope))
        second = dict(owner=kids[index + 1], mcid=following[0]['association'].get('mcid'),
                      glyph_ids=ordered[following[0]['id']], text=text(following[0]))
        fonts = {e.state.font.xref for e in content.events for ch in e.chars
                 if set(ch.source_orders) & set(first['glyph_ids'] + second['glyph_ids'])}
        return dict(page=page, parent=_reference(parent), children=kids, first=first, second=second,
                    follower=follower, follower_note=follower_note, font_xrefs=sorted(fonts),
                    start=positions[0], end=positions[-1] + 1)
    finally:
        content.close()


def _line(content, ids):
    return {round(content.actual[i]['origin'][1], 4) for i in ids}


def _regions(content, found):
    """Owner and follower rectangles derived from observed glyphs, drawings and clips.

    The column right edge is the rightmost visible glyph on the page; it is
    accepted only if the first owner's line break is consistent with it (the
    next logical character could not have fit). Vertical bands split the gap
    to the nearest glyph above/below. Nothing reaches the page margin.
    """
    actual = content.actual
    visible = [(g['bbox'][2], i) for i, g in enumerate(actual) if g['unicode'].strip()]
    right, witness = max(visible)
    support = sorted(i for x, i in visible if right - x <= .5)
    first, second = found['first']['glyph_ids'], found['second']['glyph_ids']
    follower = found['follower']['glyph_ids'] if found['follower'] else []
    box = lambda ids: Rect(min(actual[i]['bbox'][0] for i in ids), min(actual[i]['bbox'][1] for i in ids),
                           max(actual[i]['bbox'][2] for i in ids), max(actual[i]['bbox'][3] for i in ids))
    first_box, second_box = box(first), box(second)
    next_char = actual[second[0]]
    if right < max(first_box.x1, second_box.x1):
        _refuse('observed column edge lies left of the owners; an explicit rectangle is required')
    if first_box.x1 + (next_char['bbox'][2] - next_char['bbox'][0]) <= right:
        _refuse('the first owner\'s line break does not prove the observed column edge; '
                'an explicit rectangle is required')
    drawings = [Rect(*d['rect']) for d in content.page.get_drawings()]
    evidence = dict(column_right=right, column_witness=dict(unicode=actual[witness]['unicode'],
                    bbox=list(actual[witness]['bbox'])), column_support=len(support),
                    wrap_check=dict(first_line_end=first_box.x1, next_character=next_char['unicode'],
                                    next_advance=next_char['bbox'][2] - next_char['bbox'][0]))

    def band(ids, allowed):
        own = box(ids); line = _line(content, ids)
        # Every other line counts, so the two owners' bands never overlap.
        above = [actual[j]['bbox'][3] for j in range(len(actual)) if round(actual[j]['origin'][1], 4) not in line
                 and actual[j]['bbox'][3] <= own.y0]
        below = [actual[j]['bbox'][1] for j in range(len(actual)) if round(actual[j]['origin'][1], 4) not in line
                 and actual[j]['bbox'][1] >= own.y1]
        cap = own.height / 2
        y0 = own.y0 - min(cap, (own.y0 - max(above)) / 2) if above else own.y0 - cap
        y1 = own.y1 + min(cap, (min(below) - own.y1) / 2) if below else own.y1 + cap
        stops = [actual[j]['bbox'][0] for j in range(len(actual)) if j not in allowed
                 and round(actual[j]['origin'][1], 4) in line and actual[j]['bbox'][0] >= own.x0
                 and actual[j]['bbox'][2] > own.x1]
        x1 = max(own.x1, min([right] + stops))
        return own, y0, y1, x1, stops

    regions, notes = [], []
    for name, ids, allowed in (('first', first, set(first)), ('second', second, set(second) | set(follower))):
        own, y0, y1, x1, stops = band(ids, allowed)
        events = [e for e in content.events for ch in e.chars if set(ch.source_orders) & set(ids)]
        clip = events[0].state.clip
        rect = dict(x0=own.x0, y0=y0, x1=x1, y1=y1)
        if len(clip) == 1 and clip[0]['path'][0]['operator'] == 're':
            x, y, w, h = clip[0]['path'][0]['args']
            height = content.page.rect.height
            limit = Rect(min(x, x + w), height - max(y, y + h), max(x, x + w), height - min(y, y + h))
            rect = dict(x0=max(rect['x0'], limit.x0), y0=max(rect['y0'], limit.y0),
                        x1=min(rect['x1'], limit.x1), y1=min(rect['y1'], limit.y1))
        if any(Rect(**rect).intersects(d) for d in drawings):
            _refuse('a drawing lies inside the derived %s rectangle; an explicit rectangle is required' % name)
        regions.append(rect)
        notes.append(dict(owner=name, left='owner first glyph', right='foreign glyph on the line' if stops and
                          rect['x1'] < right else 'observed column edge', vertical='half gap to neighbouring lines'))
    follower_region = None
    if follower:
        own = box(follower)
        follower_region = dict(x0=own.x0, y0=regions[1]['y0'], x1=regions[1]['x1'], y1=regions[1]['y1'])
    evidence['rectangles'] = notes
    return regions, follower_region, evidence


def _provider(content, found, work_dir):
    """The single original embedded program both owners paint with, pinned by SHA."""
    from .shaped_font import ShapedFont
    if len(found['font_xrefs']) != 1:
        _refuse('owners paint with several font resources; an explicit provider is required')
    xref = found['font_xrefs'][0]
    _, ext, _, data = content.document.extract_font(xref)
    if not data:
        _refuse('the owners\' font program is not embedded')
    sha = hashlib.sha256(data).hexdigest()
    path = Path(work_dir) / ('provider-' + sha[:16] + '.' + (ext or 'ttf'))
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        path.write_bytes(data)
    font = ShapedFont(data)
    try:
        family = font.name
    finally:
        font.font.close()
    return dict(path=str(path), sha256=sha, font_index=0), dict(font_xref=xref, family=family, program_sha256=sha)


def _coverage(recipe, text):
    from .fonts import FontError
    from .shaped_font import ShapedFont
    font = ShapedFont(Path(recipe['path']).read_bytes())
    missing = []
    try:
        for c in text:
            try:
                if any(g.gid == 0 for g in font.shape(c).glyphs):
                    missing.append(c)
            except FontError:
                missing.append(c)
    finally:
        font.font.close()
    if missing:
        _refuse('the original embedded program lacks ' + ''.join(missing) + '; no fallback font is used')


def propose_adjacent_intent(source, selection, replacement, *, work_dir):
    """Read-only proposal from a glyph selection and replacement text.

    Returns status ``proposed`` (one recommended boundary) or ``ambiguous``
    (several defensible boundaries; the caller must choose). Refusals raise.
    """
    if not isinstance(replacement, str) or '\n' in replacement or '\r' in replacement:
        _refuse('replacement must be single-line Unicode text')
    found = _discover(source, selection)
    content = ContentPage(source, found['page'])
    try:
        regions, follower_region, evidence = _regions(content, found)
        recipe, font = _provider(content, found, work_dir)
    finally:
        content.close()
    _coverage(recipe, replacement)
    page = found['page']
    owners = [dict(selection=make_selection(source, page, glyph_ids=found[r]['glyph_ids'],
                                            explicit_width=region['x1'] - region['x0']), region=region)
              for r, region in zip(('first', 'second'), regions)]
    follower = (dict(glyph_ids=found['follower']['glyph_ids'], region=follower_region)
                if found['follower'] is not None else None)
    try:
        adjacent = propose_adjacent_span(source, *owners, follower=follower)
    except PdfError as exc:
        if follower is None:
            raise
        # The whitespace leaf is a follower only if the existing contract proves it.
        found['follower_note'] = 'whitespace leaf refused by the follower contract: ' + str(exc)
        follower = None
        owners[1]['region'] = dict(regions[1], x1=min(regions[1]['x1'], _follower_left(source, found)))
        owners[1]['selection'] = make_selection(source, page, glyph_ids=found['second']['glyph_ids'],
                                                explicit_width=owners[1]['region']['x1'] - owners[1]['region']['x0'])
        adjacent = propose_adjacent_span(source, *owners, follower=None)
    # Read-only qualification only; this is not the caller's acceptance.
    probe = _seal(dict(adjacent, status='confirmed'))
    first, second = found['first']['text'], found['second']['text']
    edit = dict(start=found['start'], end=found['end'], text=replacement)
    delta = len(replacement) - (edit['end'] - edit['start'])
    after = first[:edit['start']] + replacement + first[edit['end']:] + second
    breaks = set(line_break_boundaries(after))
    candidates, chosen, status = [], None, None
    for count in range(0, min(MAX_RELOCATED, len(first) - edit['end']) + 1):
        boundary = len(first) + delta - count
        natural = boundary in breaks and _safe_boundary(after, 0, boundary)
        record = dict(relocated_count=count, boundary=boundary, relocated=first[len(first) - count:],
                      after=[after[:boundary], after[boundary:]], natural_break=natural,
                      break_evidence='UAX #14 break opportunity and Japanese line-head/tail rules'
                      if natural else 'not a UAX #14 break opportunity or violates line-head/tail rules')
        try:
            plan = plan_adjacent_span_edit(source, probe, edit, boundary=boundary, provider=recipe)
            record.update(status='qualified', widths=[o['lines'][0]['width'] for o in plan['owners']],
                          width_limits=[o['region']['x1'] - p['lines'][0]['x'] for o, p in zip(owners, plan['owners'])],
                          follower_dx=plan['follower']['dx'] if plan['follower'] else None)
        except (PdfError, LayoutError) as exc:
            record.update(status='refused', reason=str(exc))
        candidates.append(record)
        if record['status'] == 'qualified' and natural:
            break
    qualified = [i for i, c in enumerate(candidates) if c['status'] == 'qualified']
    if not qualified:
        _refuse('no boundary qualifies: ' + '; '.join('%d moved: %s' % (c['relocated_count'], c['reason'])
                                                      for c in candidates))
    natural = [i for i in qualified if candidates[i]['natural_break']]
    if not natural:
        _refuse('only boundaries without a natural line break qualify; an explicit boundary is required')
    if natural[0] == qualified[0]:
        status, chosen, options = 'proposed', qualified[0], [qualified[0]]
    else:
        status, options = 'ambiguous', [qualified[0], natural[0]]
    value = dict(schema=SCHEMA, status=status, pdf_sha256=source_sha(source), page=page,
        selection=selection, selected_text=first[edit['start']:edit['end']], replacement=replacement,
        logical_before=first + second, logical_after=after, edit=edit,
        owners=dict(first={k: found['first'][k] for k in ('owner', 'mcid', 'text')},
                    second={k: found['second'][k] for k in ('owner', 'mcid', 'text')},
                    parent=found['parent'], children=found['children']),
        original_boundary=len(first), follower=dict(found['follower'] or {}, note=found['follower_note'],
                                                     approved=follower is not None),
        regions=dict(owners=[o['region'] for o in owners], follower=follower['region'] if follower else None,
                     evidence=evidence),
        font=dict(font, provider=recipe), candidates=candidates, options=options, chosen=chosen,
        requires_confirmation=['owner pair and follower', 'derived rectangles (column edge and line bands)',
                               'owner boundary and moved characters', 'pinned original font program'],
        adjacent=adjacent)
    return _seal(value)


def _follower_left(source, found):
    """Right limit for the second owner when the whitespace leaf stays in place."""
    content = ContentPage(source, found['page'])
    try:
        return min(content.actual[i]['bbox'][0] for i in found['follower']['glyph_ids'])
    finally:
        content.close()


def accept_adjacent_intent(source, proposal, *, work_dir, option=None):
    """Explicit acceptance of one proposed boundary on the proposal's revision.

    The proposal is re-derived from its own selection and replacement and must
    be identical; the adjacent-Span proposal is then accepted by its own API.
    """
    if not isinstance(proposal, dict) or proposal.get('schema') != SCHEMA or _seal(proposal) != proposal:
        _refuse('acceptance needs an unmodified intent proposal')
    current = propose_adjacent_intent(source, proposal['selection'], proposal['replacement'], work_dir=work_dir)
    if current != proposal:
        _refuse('the intent proposal is stale or was modified')
    if option is None:
        if proposal['status'] != 'proposed':
            _refuse('several boundaries are defensible; choose one explicitly')
        option = proposal['chosen']
    if option not in proposal['options']:
        _refuse('the chosen boundary is not one of the proposed options')
    model = accept_adjacent_span(source, proposal['adjacent'])
    candidate = proposal['candidates'][option]
    return _seal(dict(schema=ACCEPTANCE, status='accepted', pdf_sha256=proposal['pdf_sha256'],
                      proposal=proposal, option=option, edit=proposal['edit'], boundary=candidate['boundary'],
                      provider=proposal['font']['provider'], model=model))


def edit_adjacent_intent(source, acceptance, output, model_output, *, work_dir):
    """Run the accepted edit through ``edit_adjacent_span`` after re-proving it."""
    if (not isinstance(acceptance, dict) or acceptance.get('schema') != ACCEPTANCE
            or acceptance.get('status') != 'accepted' or _seal(acceptance) != acceptance):
        _refuse('editing requires an explicit, unmodified acceptance')
    proposal = acceptance['proposal']
    current = propose_adjacent_intent(source, proposal['selection'], proposal['replacement'], work_dir=work_dir)
    if current != proposal or source_sha(source) != acceptance['pdf_sha256']:
        _refuse('the accepted proposal is stale for this revision')
    candidate = proposal['candidates'][acceptance['option']]
    if (acceptance['option'] not in proposal['options'] or acceptance['edit'] != proposal['edit']
            or acceptance['boundary'] != candidate['boundary'] or acceptance['provider'] != proposal['font']['provider']):
        _refuse('the acceptance differs from its proposal')
    model = acceptance['model']
    expected = {k: v for k, v in proposal['adjacent'].items() if k not in ('status', 'model_sha256')}
    if {k: v for k, v in model.items() if k not in ('status', 'model_sha256')} != expected:
        _refuse('the accepted adjacent-Span state differs from the proposal')
    plan = plan_adjacent_span_edit(source, model, acceptance['edit'], boundary=acceptance['boundary'],
                                   provider=acceptance['provider'])
    dx = plan['follower']['dx'] if plan['follower'] else None
    if plan['split']['after'] != candidate['after'] or dx != candidate['follower_dx']:
        _refuse('the current plan differs from the accepted candidate')
    result = edit_adjacent_span(source, model, output, model_output, acceptance['edit'],
                                boundary=acceptance['boundary'], provider=acceptance['provider'])
    return dict(result, intent=dict(selected_text=proposal['selected_text'], replacement=proposal['replacement'],
                                    boundary=acceptance['boundary'], option=acceptance['option']))
