"""Explicitly join B1 source paragraph fragments across consecutive occupied pages.

Each page keeps its own B1 evidence and source owners. A caller supplies the
semantic paragraph grouping; geometry never invents that relationship. This
bootstrap accepts explicit source-page breaks or joins paragraphs already split
across a seam. Empty continuations require separately confirmed authorities.
No unselected content moves and the existing T2 writer remains authoritative.
"""
from copy import deepcopy
import math

from .attributed import digest
from .backend import PdfError
from .page_proposal import _arguments, _build, _recompute, _reproduce_fragments, _seal
from .selection import source_sha
from .shared_flow import SOURCE_PLACEMENT, confirm_shared_flow
from .story_flow import confirm_story

SCHEMA = 'pdfengine-owned-flow-proposal-1'


def _selection(pages, paragraphs, relationship):
    if not isinstance(relationship, str) or not relationship.strip():
        raise PdfError('describe the observed textual relationship explicitly')
    if not isinstance(pages, list) or len(pages) < 2:
        raise PdfError('select at least two page-flow proposals')
    try:
        numbers = [p['binding']['page'] for p in pages]
        observed = [(p['binding']['page'], para['id']) for p in pages for para in p['paragraphs']]
        if any(p.get('target') is None for p in pages):
            raise PdfError('each page must explicitly select observed line_ids')
        if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            raise PdfError('source pages must be distinct, consecutive and ordered')
        if not isinstance(paragraphs, list) or not paragraphs:
            raise PdfError('select ordered logical paragraphs and source parts')
        ids = [p['id'] for p in paragraphs]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise PdfError('logical paragraph IDs must be distinct nonempty strings')
        selected = []
        for para in paragraphs:
            if set(para) != {'id', 'parts'} or not isinstance(para['parts'], list) or not para['parts']:
                raise PdfError('each logical paragraph needs id and nonempty parts')
            sequence = []
            for part in para['parts']:
                if set(part) != {'page', 'paragraph_id'} or type(part['page']) is not int:
                    raise PdfError('each source part needs page and paragraph_id')
                selected.append((part['page'], part['paragraph_id']))
                sequence.append(part['page'])
            if sequence != list(range(sequence[0], sequence[0] + len(sequence))):
                raise PdfError('joined source parts must cross consecutive page seams')
        if selected != observed:
            raise PdfError('source parts must cover all selected paragraphs exactly once in observed order')
    except (KeyError, TypeError, ValueError) as exc:
        raise PdfError('invalid owned-flow source selection') from exc
    return numbers


def propose_owned_flow(source, page_proposals, *, paragraphs, relationship, page_breaks=None,
                       page_overrides=None, continuations=None, vacated_continuations=None):
    """Propose caller-declared paragraph joins without accepting or editing the PDF.

    `paragraphs` is an ordered list of {id, parts: [{page, paragraph_id}]}.
    Each part names a whole paragraph from an explicit B1 page selection. Parts
    of one logical paragraph concatenate exactly, without an invented separator.
    `relationship` records the caller's textual basis, not a geometry inference.
    Provider substitutions still require explicit per-page choices at acceptance.
    page_breaks explicitly choose a same-page minimum gap and preserve the next
    paragraph's original region floor; no gap is inferred between page coordinates.
    page_overrides contains ordinary B1 region-geometry overrides by page number.
    continuations lists {region_id, before_region, region, destination}: each
    destination must already have independent continuation confirmation authority.
    vacated_continuations instead lists explicit source-owner release requests:
    region_id, before_region, region, release_paragraphs and confirmation kwargs.
    Acceptance binds those requests to the joint owners and re-proves no-edit
    placement; painting remains conditional on the same Transaction's removals.
    """
    _selection(page_proposals, paragraphs, relationship)
    for page in page_proposals:
        _recompute(source, page)
    options = {}
    if page_breaks is not None: options['page_breaks'] = page_breaks
    if page_overrides is not None: options['page_overrides'] = page_overrides
    if continuations is not None: options['continuations'] = continuations
    if vacated_continuations is not None: options['vacated_continuations'] = vacated_continuations
    _options(page_proposals, paragraphs, options)
    return _seal(dict(schema=SCHEMA, source_sha256=source_sha(source),
        pages=page_proposals, paragraphs=paragraphs, relationship=relationship,
        relationship_provenance='caller-declared-textual-relationship; explicit acceptance required', **options))


def _options(pages, paragraphs, proposal):
    """Caller policy for an unobserved gap is explicit, never derived from page y values."""
    breaks = proposal.get('page_breaks', [])
    if not isinstance(breaks, list):
        raise PdfError('page_breaks must list explicit independent-paragraph policies')
    pairs = {(a['id'], b['id']): (a['parts'][-1]['page'], b['parts'][0]['page'])
             for a, b in zip(paragraphs, paragraphs[1:])}
    seen = set()
    for edge in breaks:
        if (not isinstance(edge, dict) or set(edge) != {'before', 'after', 'minimum_baseline_gap', 'placement'}
                or not isinstance(edge['before'], str) or not isinstance(edge['after'], str)
                or edge['placement'] != 'source-region-floor-and-same-page-follow'):
            raise PdfError('page break needs an explicit source-region floor and same-page gap policy')
        pair = (edge['before'], edge['after'])
        if pair not in pairs or pair in seen or pairs[pair][1] != pairs[pair][0] + 1:
            raise PdfError('page break must name a distinct selected independent-paragraph page seam')
        gap = edge['minimum_baseline_gap']
        if type(gap) not in (int, float) or not math.isfinite(gap) or gap <= 0:
            raise PdfError('page break requires a positive finite caller-selected same-page gap')
        seen.add(pair)
    overrides = proposal.get('page_overrides', {})
    if not isinstance(overrides, dict) or set(overrides) - {str(p['binding']['page']) for p in pages}:
        raise PdfError('page overrides must name selected pages')
    if any(not isinstance(v, dict) or set(v) - {'width', 'region_top', 'region_bottom'} for v in overrides.values()):
        raise PdfError('joint page overrides may only change qualified region geometry')
    continuations = proposal.get('continuations', [])
    if not isinstance(continuations, list):
        raise PdfError('continuations must list separately confirmed insertion authorities')
    for item in continuations:
        if (not isinstance(item, dict) or set(item) != {'region_id', 'before_region', 'region', 'destination'}
                or not isinstance(item['before_region'], str) or not isinstance(item['region'], dict)
                or not isinstance(item['destination'], dict)):
            raise PdfError('continuation needs a region, insertion order and confirmed destination')
    # Older readers refuse the independent page seam rather than silently
    # ignoring geometry or prefix choices on an otherwise supported old join.
    conditional = proposal.get('vacated_continuations', [])
    if not isinstance(conditional, list) or any(not isinstance(v, dict) or set(v) != {
            'region_id', 'before_region', 'region', 'release_paragraphs', 'confirmation'} for v in conditional):
        raise PdfError('vacated continuations require explicit source owners, geometry and insertion confirmation')
    if (overrides or continuations or conditional) and not breaks:
        raise PdfError('joint geometry and prefix choices require an explicit independent source page break')
    return {(e['before'], e['after']): e for e in breaks}


def _extension_review(source, base_model, proposal):
    """Describe an append-only source selection, never grant paint authority."""
    from .shared_flow import _restore, _slots
    base = _restore(source, base_model)
    if (base['schema'] != 'pdfengine-shared-flow-2'
            or base['allocation_provenance'] != 'observed-source-uncomposed'):
        raise PdfError('extend ownership from the original accepted source, not an edited allocation')
    previous = base['flow']['paragraphs']
    current = [p['id'] for p in proposal['paragraphs']]
    if current[:len(previous)] != previous or len(current) <= len(previous):
        raise PdfError('ownership extension must retain the ordered paragraphs and append whole source owners')
    observed = {(p['binding']['page'], q['id']): q for p in proposal['pages'] for q in p['paragraphs']}
    reviewed = []
    for group in proposal['paragraphs']:
        parts = []
        for part in group['parts']:
            q = observed[(part['page'], part['paragraph_id'])]
            parts.append(dict(page=part['page'], glyph_ids=q['glyph_ids'], text=q['text'],
                line_ids=q['line_ids'], bounds=q['bounds'], lines=q['lines']))
        if group['id'] in previous:
            old = [base['slots'][sid]['binding']['paragraph'] for sid in _slots(base, group['id'])
                   if base['slots'][sid].get('destination_id') is None]
            identity = lambda p: (p['page'], p['glyph_ids'], p['text'])
            if [identity(p) for p in parts] != [(s['selection']['page'], s['selection']['glyph_ids'], s['text']) for s in old]:
                raise PdfError('ownership extension changes an already accepted source paragraph')
        else:
            reviewed.append(dict(paragraph_id=group['id'], parts=parts))
    return dict(base_model_sha256=base['model_sha256'], source_sha256=base['pdf_sha256'],
        retained_paragraphs=previous, added_paragraphs=reviewed,
        previous_regions=base['regions'], proposed_page_regions=[p['region'] for p in proposal['pages']],
        proposed_page_breaks=proposal.get('page_breaks', []),
        proposed_geometry_overrides=proposal.get('page_overrides', {}),
        proposed_continuations=proposal.get('continuations', []),
        proposed_vacated_continuations=proposal.get('vacated_continuations', []),
        relationship=proposal['relationship'])


def propose_owned_flow_extension(source, accepted_model, page_proposals, **selection):
    """Review additional whole followers against the accepted current revision.

    Rebuild B1 proposals with the enlarged explicit line selection. The returned
    proposal grants no new ownership until accept_owned_flow_report is called.
    For a restored edited flow, supply one B1 proposal containing only the new
    followers and append_to_region naming its last source region. Existing
    source/generated slots are retained; acceptance proves a joint no-op.
    By default the first follower keeps its current observed gap. Instead,
    append_to_region={region_id, incoming} approves its future placement:
    incoming names before/after, a minimum_baseline_gap, a baseline_floor (a
    page coordinate in that region the follower never rises above) and the
    approved-baseline-floor-and-same-region-follow placement. The policy must
    reproduce the follower's current baseline, or the review refuses.
    Instead of append_to_region, next_page adopts complete owners on the page
    after the last source region: region_id, page_break (caller-selected
    same-page gap; no gap is derived from different pages' y values), optional
    B1 page_overrides, and at most one continuation authority for the last
    paragraph: vacated_continuation (release of named adopted owners, proven
    in the same Transaction) or continuation (an empty destination confirmed
    on the current revision). Optional fixed_foreign holds current witnesses
    from post_edit_extension.inspect_fixed_foreign for explicitly classified
    header/footer lines in the outer page margins. They remain protected,
    unowned paint and supply no capacity or continuation authority.
    """
    from .shared_flow import _restore
    base = _restore(source, accepted_model)
    if base['allocation_provenance'] == 'generated-from-confirmed-shared-flow':
        from .post_edit_extension import propose
        return propose(source, base, page_proposals, **selection)
    proposal = propose_owned_flow(source, page_proposals, **selection)
    review = _extension_review(source, accepted_model, proposal)
    proposal['extension'] = dict(base_model=deepcopy(base), review=review)
    return _seal(proposal)


def _seam(analysis, plan, at_end):
    """Refuse a join that skips observed text in the selected column at a page edge."""
    edge = plan['column'][-1][-1] if at_end else plan['column'][0][0]
    selected = {line['id'] for para in plan['column'] for line in para}
    left, _, right, _ = plan['region']['bounds']
    for line in analysis.lines:
        beyond = line['baseline'] > edge['baseline'] if at_end else line['baseline'] < edge['baseline']
        if line['id'] not in selected and beyond and line['bbox'][0] < right and line['bbox'][2] > left:
            raise PdfError('page seam skips unselected text; cannot prove a contiguous owned paragraph')


def _compose(source, entries, paragraphs, page_breaks=None, continuations=(), vacated_continuations=()):
    stories, regions, slots, policies, protected, source_lines = {}, {}, {}, {}, {}, {}
    by_page = {a.page: (a, plan) for a, plan in entries}
    args = {a.page: _arguments(a, plan) for a, plan in entries}
    for number, (_, shared) in args.items():
        regions[f'page-{number}'] = shared['regions']['R1']
        protected.update(shared['protected_regions'])
    for para in paragraphs:
        pid = para['id']
        slots[pid] = {}
        for index, part in enumerate(para['parts']):
            number, local = part['page'], part['paragraph_id']
            analysis, plan = by_page[number]
            spec = deepcopy(args[number][0][local])
            policy = args[number][1]['paragraph_policies'][local]
            if policy.get('text_placement') != SOURCE_PLACEMENT:
                raise PdfError('cross-page source joins require witnessed preserve-source-adjacency on every part')
            if index:
                _seam(by_page[para['parts'][index-1]['page']][0], by_page[para['parts'][index-1]['page']][1], True)
                _seam(analysis, plan, False)
            ident = f'page-{number}:{local}'
            rename = {i: f'page-{number}:{i}' for i in spec['styles']}
            spec.update(paragraph_id=pid, chain=[ident], containers={ident: spec['containers']['part']},
                styles={rename[i]: v for i, v in spec['styles'].items()},
                style_assignments={ident: {i: rename[v] for i, v in spec['style_assignments']['part'].items()}},
                typing_style_id=rename[spec['typing_style_id']], protected_regions=protected)
            if index == 0:
                stories[pid] = spec
                policies[pid] = deepcopy(policy)
            else:
                for key in ('containers', 'styles', 'style_assignments'):
                    stories[pid][key].update(spec[key])
                stories[pid]['chain'].append(ident)
                policies[pid]['min_line_height'] = min(policies[pid]['min_line_height'], policy['min_line_height'])
            slots[pid][ident] = f'page-{number}'
            source_lines[(pid, ident)] = plan['column'][plan['ids'].index(local)]
    follows = []
    for before, after in zip(paragraphs, paragraphs[1:]):
        tail, head = before['parts'][-1], after['parts'][0]
        if tail['page'] != head['page']:
            chosen = (page_breaks or {}).get((before['id'], after['id']))
            if chosen is None:
                raise PdfError('the paragraph gap across a page break is unobserved; no follows gap is inferred')
            _seam(*by_page[tail['page']], True)
            _seam(*by_page[head['page']], False)
            policies[after['id']]['break_before'] = 'next-region'
            follows.append(dict(before=before['id'], after=after['id'],
                minimum_baseline_gap=chosen['minimum_baseline_gap'], region_start=chosen['placement'],
                source_break=dict(before_region=f'page-{tail["page"]}', after_region=f'page-{head["page"]}')))
            continue
        local_follows = args[tail['page']][1]['follows']
        edge = next((f for f in local_follows if f['before'] == tail['paragraph_id'] and f['after'] == head['paragraph_id']), None)
        if edge is None:
            raise PdfError('no observed follows relationship between the selected paragraphs')
        follows.append(dict(edge, before=before['id'], after=after['id']))
    region_order = list(regions)
    source_regions = set(regions)
    destinations = {}
    for item in continuations:
        rid, target, dest = item['region_id'], item['before_region'], item['destination']
        if (not isinstance(rid, str) or not rid or rid in regions or target not in source_regions
                or dest.get('region_id') != rid or dest.get('destination_id') in destinations):
            raise PdfError('continuation needs a distinct region and an explicit position before a source region')
        region = item['region']
        edges = [e for e in follows if e.get('source_break', {}).get('after_region') == target
                 and e['before'] == dest.get('paragraph_id')]
        if (len(edges) != 1 or region.get('page') != regions[target]['page']):
            raise PdfError('prefix continuation must serve the predecessor at its confirmed source page break')
        regions[rid] = deepcopy(region)
        region_order.insert(region_order.index(target), rid)
        destinations[dest['destination_id']] = deepcopy(dest)
    confirmed = {pid: confirm_story(source, **spec) for pid, spec in stories.items()}
    state = confirm_shared_flow(source, confirmed, flow_id='cross-page-owned-flow',
        paragraph_order=[p['id'] for p in paragraphs], regions=regions, region_order=region_order,
        slot_regions=slots, paragraph_policies=policies, follows=follows, protected_regions=protected,
        continuation_destinations=destinations)
    from .vacated_space import confirm as confirm_vacated
    for request in vacated_continuations:
        state = confirm_vacated(source, state, **request)
    # confirm_shared_flow imports containers in the explicit paragraph/chain order.
    expected = {}
    for sid, slot in state['slots'].items():
        matches = [(pid, ident) for pid, ident in source_lines
                   if pid == slot['paragraph_id']
                   and stories[pid]['containers'][ident]['paragraph'] == slot['binding']['paragraph']]
        if len(matches) != 1:
            raise PdfError('source fragment binding is not unique')
        expected[sid] = source_lines[matches[0]]
    reproduction = _reproduce_fragments(source, state, expected)
    if reproduction['status'] != 'reproduced':
        raise PdfError('joint no-edit reproduction failed: ' + reproduction['detail'])
    return state, reproduction


def accept_owned_flow_report(source, proposal, *, provider_choices=None):
    """Explicitly accept all named source owners, regions, joins and font choices.

    `provider_choices` maps decimal page-number strings to ordinary B1 provider
    choices. Every page is freshly verified before the joint no-edit proof.
    """
    if (not isinstance(proposal, dict) or proposal.get('schema') != SCHEMA
            or proposal.get('digest') != digest({k: v for k, v in proposal.items() if k != 'digest'})):
        raise PdfError('owned-flow proposal was modified or has an unsupported schema')
    if proposal['source_sha256'] != source_sha(source):
        raise PdfError('owned-flow proposal belongs to another PDF revision')
    if 'edited_extension' in proposal:
        from .post_edit_extension import accept
        return accept(source, proposal, provider_choices)
    numbers = _selection(proposal['pages'], proposal['paragraphs'], proposal['relationship'])
    page_breaks = _options(proposal['pages'], proposal['paragraphs'], proposal)
    extension = proposal.get('extension')
    if extension is not None:
        if (not isinstance(extension, dict) or set(extension) != {'base_model', 'review'}
                or _extension_review(source, extension['base_model'], proposal) != extension['review']):
            raise PdfError('ownership extension review differs from the accepted source or requested selection')
    choices = provider_choices if provider_choices is not None else {}
    if not isinstance(choices, dict) or set(choices) - {str(n) for n in numbers}:
        raise PdfError('provider choices must name selected page numbers')
    entries, evidence = [], []
    for page in proposal['pages']:
        analysis, files, roots = _recompute(source, page)
        built, plan = _build(analysis, font_files=files, font_roots=roots,
            provider_choices=choices.get(str(analysis.page)),
            overrides=proposal.get('page_overrides', {}).get(str(analysis.page)),
            **{k: page['font_search'][k] for k in ('include_embedded_fonts', 'font_cache', 'new_text')})
        if built['status'] != 'proposed' or plan is None:
            raise PdfError(f'page {analysis.page} cannot be accepted: {built["refusals"] + built["unresolved"]}')
        entries.append((analysis, plan))
        evidence.append(dict(binding=page['binding'], proposal_digest=page['digest'], reproduction=built['reproduction'],
            providers={lid: dict(sha256=c['sha256'], font_index=c['font_index'], provenance=how)
                       for lid, (c, how) in sorted(plan['providers'].items())}))
    state, reproduction = _compose(source, entries, proposal['paragraphs'], page_breaks, proposal.get('continuations', []),
        proposal.get('vacated_continuations', []))
    return dict(state=state, receipt=dict(schema='pdfengine-owned-flow-acceptance-1',
        proposal_digest=proposal['digest'], source_sha256=proposal['source_sha256'], pages=evidence,
        paragraphs=deepcopy(proposal['paragraphs']), relationship=proposal['relationship'],
        relationship_provenance=proposal['relationship_provenance'], reproduction=reproduction,
        state_model_sha256=state['model_sha256'],
        **({'extension': deepcopy(extension['review'])} if extension is not None else {}),
        **{k: deepcopy(proposal[k]) for k in ('page_breaks', 'page_overrides', 'continuations', 'vacated_continuations') if k in proposal}))


def accept_owned_flow(source, proposal, *, provider_choices=None):
    """Return the existing shared-flow v2 state from an explicit joint acceptance."""
    return accept_owned_flow_report(source, proposal, provider_choices=provider_choices)['state']
