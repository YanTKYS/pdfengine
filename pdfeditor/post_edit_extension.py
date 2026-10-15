"""Append current-revision source owners without recreating existing output slots."""
from copy import deepcopy
import json
import math

from .backend import PdfError
from .elements import _close
from .page_proposal import (_Analysis, _arguments, _build, _protected, _recompute,
                            _reproduce_fragments, _seal)
from .shared_flow import (_restore, _slots, _plan, _contract, _breaks, _validate,
                          _layout_options, _reseal, SOURCE_PLACEMENT)
from .story_flow import confirm_story, _boundaries
from .selection import inspect_selection_source
from . import source_ownership, vacated_space

NEXT_PAGE_KEYS = {'region_id', 'page_break', 'page_overrides', 'continuation', 'vacated_continuation', 'fixed_foreign'}
FIXED_FOREIGN_SCHEMA = 'pdfengine-fixed-foreign-margin-1'
# Deliberately bounded geometry, not inferred header/footer semantics: the
# declared body must include the central 75% of the page's height.
FIXED_MARGIN_FRACTION = 1 / 8
PAGE_BREAK_PLACEMENT = 'source-region-floor-and-same-page-follow'
FOLLOWER_PLACEMENT = 'approved-baseline-floor-and-same-region-follow'
APPEND_KEYS = {'region_id', 'incoming'}
INCOMING_KEYS = {'before', 'after', 'minimum_baseline_gap', 'baseline_floor', 'placement'}
REGION_KEYS = {'page', 'bounds', 'x', 'width', 'first_baseline'}
CONFIRMATION_KEYS = {'destination_id', 'paragraph_id', 'region_id', 'page', 'bounds', 'insertion',
                     'graphics_state', 'page_entry_order', 'boundary'}


def _selection(source, base, pages, paragraphs, relationship):
    """Whole, newly named, observed-order paragraphs of one current B1 page selection."""
    if base['allocation_provenance'] != 'generated-from-confirmed-shared-flow':
        raise PdfError('post-edit extension requires a restored edited allocation')
    if not isinstance(relationship, str) or not relationship.strip():
        raise PdfError('describe the textual relationship of the additional paragraphs')
    if not isinstance(pages,list) or len(pages) != 1 or not isinstance(paragraphs,list) or not paragraphs:
        raise PdfError('append complete followers from one current source page')
    if any(not isinstance(p,dict) or set(p) != {'id','parts'}
           or not isinstance(p['id'],str) or not p['id']
           or not isinstance(p['parts'],list) or len(p['parts']) != 1
           or not isinstance(p['parts'][0],dict) or set(p['parts'][0]) != {'page','paragraph_id'}
           for p in paragraphs):
        raise PdfError('additional paragraphs need distinct IDs and whole source parts')
    page = pages[0]
    analysis, _, _ = _recompute(source, page)
    observed = [(analysis.page, p['id']) for p in page['paragraphs']]
    ids = [p['id'] for p in paragraphs]
    if len(set(ids)) != len(ids) or set(ids) & set(base['paragraphs']) or any(not isinstance(i,str) or not i for i in ids):
        raise PdfError('additional paragraph identities must be new and distinct')
    if (page.get('target') is None
            or [(p['parts'][0]['page'], p['parts'][0]['paragraph_id']) for p in paragraphs] != observed):
        raise PdfError('append all explicitly selected whole paragraphs in observed order')
    return page, analysis


def review(source, base, pages, paragraphs, relationship, append_to_region=None, next_page=None):
    base = _restore(source, base)
    if (append_to_region is None) == (next_page is None):
        raise PdfError('extend either the last confirmed source region or the next existing page')
    page, analysis = _selection(source, base, pages, paragraphs, relationship)
    if next_page is not None:
        return _next_page_review(source, base, page, analysis, paragraphs, relationship, next_page)
    rid, incoming = _append_request(append_to_region)
    if rid != base['flow']['regions'][-1] or base['regions'][rid]['page'] != analysis.page:
        raise PdfError('additional owners must follow the last confirmed source region')
    owned = {gid for slot in base['slots'].values() if base['regions'][slot['region_id']]['page'] == analysis.page
             for gid in slot['binding']['paragraph']['selection']['glyph_ids']}
    added = {gid for p in page['paragraphs'] for gid in p['glyph_ids']}
    if owned & added:
        raise PdfError('additional source owner overlaps existing source or generated slot')
    previous = base['flow']['paragraphs'][-1]
    tail = base['slots'][_slots(base, previous)[-1]]
    if tail['region_id'] != rid or tail['occupancy'] is None:
        raise PdfError('last source owner must occupy the region being extended')
    first = page['paragraphs'][0]['lines'][0]['baseline']
    last = tail['occupancy']['last_baseline']
    if first <= last:
        raise PdfError('additional owners must follow the current last paragraph')
    r = base['regions'][rid]
    for line in analysis.lines:
        if (last < line['baseline'] < first and line['bbox'][0] < r['bounds'][2]
                and line['bbox'][2] > r['bounds'][0] and not set(line['glyph_ids']) <= owned | added):
            raise PdfError('extension skips unselected intervening text')
    evidence = dict(base_model_sha256=base['model_sha256'], source_sha256=base['pdf_sha256'],
        retained_paragraphs=base['flow']['paragraphs'], retained_slots=sorted(base['slots']),
        added_paragraphs=deepcopy(page['paragraphs']), append_to_region=rid,
        previous_regions=base['regions'], proposed_page_region=page['region'],
        current_baseline_gap=first-last, relationship=relationship)
    if incoming is not None:
        evidence['incoming'] = _incoming_review(base, page, rid, previous, paragraphs[0]['id'], incoming, first, last)
    return evidence


def _append_request(request):
    """`region_id`, or {region_id, incoming}: the follower's explicitly approved placement."""
    if isinstance(request, str):
        return request, None
    if (not isinstance(request, dict) or set(request) != APPEND_KEYS
            or not isinstance(request['region_id'], str) or not request['region_id']):
        raise PdfError('append_to_region takes a region ID or exactly {region_id, incoming}')
    return request['region_id'], request['incoming']


def _incoming_review(base, page, rid, previous, follower, edge, first, last):
    """Separate the current placement witness from the approved future placement policy.

    The witness is this revision's last owned baseline and the follower's current
    first baseline. The policy is the caller's: a same-region minimum gap and a
    page-coordinate floor in `rid` that the follower's first line never rises
    above. Nothing is inferred from an earlier revision. The policy must place the
    follower exactly where it is now, so the extension no-op moves nothing.
    """
    if (not isinstance(edge, dict) or set(edge) != INCOMING_KEYS or edge['placement'] != FOLLOWER_PLACEMENT
            or edge['before'] != previous or edge['after'] != follower):
        raise PdfError('follower placement needs an explicitly approved floor-and-follow policy '
                       'from the last confirmed paragraph')
    gap, floor = edge['minimum_baseline_gap'], edge['baseline_floor']
    if not _finite(gap) or gap <= 0 or not _finite(floor) or floor <= 0:
        raise PdfError('approved follower gap and floor must be positive and finite')
    region = base['regions'][rid]
    bottom = max(region['bounds'][3], page['region']['bounds'][3])
    if not region['first_baseline'] <= floor < bottom:
        raise PdfError('approved follower floor lies outside the extended source region capacity')
    placed = max(last + gap, floor)
    if not _close(placed, first):
        raise PdfError('approved follower placement differs from the current PDF; the extension no-op would move it '
                       f'({placed} pt instead of {first} pt)')
    return dict(edge, region_id=rid,
        current=dict(last_owned_baseline=last, first_follower_baseline=first, baseline_gap=first-last),
        extension_noop_baseline=placed,
        floor_is='caller-approved page coordinate in this region; not derived from another revision')


def propose(source, base, pages, *, paragraphs, relationship, append_to_region=None, next_page=None):
    from .owned_flow_proposal import SCHEMA
    base = _restore(source, base)
    evidence = review(source, base, pages, paragraphs, relationship, append_to_region, next_page)
    target = dict(append_to_region=append_to_region) if next_page is None else dict(next_page=deepcopy(next_page))
    return _seal(dict(schema=SCHEMA, source_sha256=base['pdf_sha256'], pages=deepcopy(pages),
        paragraphs=deepcopy(paragraphs), relationship=relationship,
        edited_extension=dict(base_model=deepcopy(base), review=evidence), **target))


def accept(source, proposal, provider_choices):
    extension = proposal['edited_extension']
    base = _restore(source, extension['base_model'])
    evidence = review(source, base, proposal['pages'], proposal['paragraphs'], proposal['relationship'],
                      proposal.get('append_to_region'), proposal.get('next_page'))
    if extension['review'] != evidence:
        raise PdfError('post-edit extension review differs from the current revision')
    if 'next_page' in proposal:
        return _accept_next_page(source, base, proposal, evidence, provider_choices)
    page = proposal['pages'][0]
    analysis, files, roots = _recompute(source, page)
    choices = provider_choices if provider_choices is not None else {}
    if not isinstance(choices,dict) or set(choices) - {str(analysis.page)}:
        raise PdfError('provider choices must name the additional source page')
    # A standalone page cannot claim existing source-output markers. Defer only
    # reproduction to the complete joint model below; all B1 qualification runs.
    built, plan = _build(analysis, font_files=files, font_roots=roots, reproduce=False,
        provider_choices=choices.get(str(analysis.page)),
        **{k: page['font_search'][k] for k in ('include_embedded_fonts','font_cache','new_text')})
    if built['status'] != 'proposed' or plan is None:
        raise PdfError('additional page qualification failed: ' + str(built['refusals'] + built['unresolved']))
    stories, shared = _arguments(analysis, plan)
    state = deepcopy(base)
    rid = evidence['append_to_region']; region = state['regions'][rid]
    new = shared['regions']['R1']
    if not _close(region['x'], new['x']):
        raise PdfError('additional paragraphs do not share the confirmed text column')
    region['width'] = max(region['width'], new['width'])
    region['bounds'] = [min(region['bounds'][0],new['bounds'][0]),region['bounds'][1],
                        max(region['bounds'][2],new['bounds'][2]),max(region['bounds'][3],new['bounds'][3])]
    all_owned = {gid for slot in base['slots'].values() if base['regions'][slot['region_id']]['page'] == analysis.page
                 for gid in slot['binding']['paragraph']['selection']['glyph_ids']}
    all_owned.update(gid for p in page['paragraphs'] for gid in p['glyph_ids'])
    selected = [line['id'] for line in analysis.lines if set(line['glyph_ids']) <= all_owned]
    protected = _protected(analysis, analysis.foreign(selected))[str(analysis.page)]
    # Preserve prior explicit obstacles unless this exact observed line has just
    # become owned. Never erase a caller's larger protected area by intersection.
    added_bounds = [line['bbox'] for para in plan['column'] for line in para]
    for old in base['pages'][str(analysis.page)]['protected_regions']:
        if (old['role'] != 'fixed' or old['bounds'] not in added_bounds) and not any(p['bounds'] == old['bounds'] for p in protected):
            protected.append({k:v for k,v in old.items() if k != 'provenance'})
    state['pages'][str(analysis.page)]['protected_regions'] = [dict(p,provenance='explicitly_confirmed') for p in protected]
    approved = evidence.get('incoming')
    first = (dict(minimum_baseline_gap=evidence['current_baseline_gap'], region_start='reset-to-region-baseline')
             if approved is None else
             dict(minimum_baseline_gap=approved['minimum_baseline_gap'], region_start='reset-to-region-baseline',
                  baseline_floor=dict(region_id=rid, baseline=approved['baseline_floor'])))
    _adopt(source, state, stories, shared, proposal, rid, first)
    # Capacity metadata changes, but snapshots, source markers, destination
    # identities, style registries and existing physical layouts remain intact.
    _relayout(state, state['slots'])
    return _complete(source, base, state, analysis, proposal, evidence)


def _complete(source, base, state, analysis, proposal, evidence):
    """Measure only new owners, then require the complete joint no-edit proof."""
    planned = _plan(source,state,{})
    if planned['new_slots']:
        raise PdfError('extension no-op cannot create or replace a continuation slot')
    for sid, slot in state['slots'].items():
        if sid not in base['slots']:
            slot['occupancy'] = planned['fragments'][sid]['occupancy']
            # Imported observed stories have no shaped ascent/descent yet.
            # These measured metrics are accepted only after the independent
            # observation/Unicode/glyph-origin reproduction proof below.
            slot['binding']['physical_layout']['lines'] = deepcopy(planned['fragments'][sid]['lines'])
            slot['binding'] = _reseal(slot['binding'])
    state['previous_model_sha256'] = base['model_sha256']
    state['contract_sha256'] = _contract(state); state['physical_breaks'] = _breaks(state)
    state = _validate(source,_reseal(state))
    analyses = {analysis.page:analysis}; expected = {}
    for sid, slot in state['slots'].items():
        number = state['regions'][slot['region_id']]['page']
        if number not in analyses: analyses[number] = _Analysis(source,number)
        glyphs = set(slot['binding']['paragraph']['selection']['glyph_ids'])
        expected[sid] = [line for line in analyses[number].lines if set(line['glyph_ids']) <= glyphs]
        if {gid for line in expected[sid] for gid in line['glyph_ids']} != glyphs:
            raise PdfError('source owner does not cover whole observed lines')
    reproduction = _reproduce_fragments(source,state,expected)
    if reproduction['status'] != 'reproduced':
        raise PdfError('post-edit extension no-edit reproduction failed: '+reproduction['detail'])
    return dict(state=state,receipt=dict(schema='pdfengine-owned-flow-acceptance-1',
        proposal_digest=proposal['digest'], source_sha256=state['pdf_sha256'], extension=evidence,
        reproduction=reproduction,state_model_sha256=state['model_sha256']))


def _adopt(source, state, stories, shared, proposal, rid, first):
    """Import the reviewed B1 stories as new current-revision source owners of `rid`.

    `first` is the explicitly reviewed incoming edge of the first adopted paragraph;
    later edges are the B1-observed follows on the same current page.
    """
    rename = {p['parts'][0]['paragraph_id']:p['id'] for p in proposal['paragraphs']}
    previous = state['flow']['paragraphs'][-1]; added = []
    for local, pid in rename.items():
        spec = deepcopy(stories[local]); spec['paragraph_id'] = pid
        story = confirm_story(source, **spec)
        state['paragraphs'][pid] = {k:deepcopy(story[k]) for k in ('logical','style_registry','reflow_font_policy')}
        state['paragraphs'][pid]['source_model_sha256'] = story['model_sha256']
        policy = shared['paragraph_policies'][local]
        if policy.get('text_placement') != SOURCE_PLACEMENT:
            raise PdfError('additional owner needs witnessed source adjacency')
        state['paragraph_policies'][pid] = deepcopy(policy)
        sid = next('slot-'+str(i) for i in range(len(state['slots'])+1) if 'slot-'+str(i) not in state['slots'])
        slot = deepcopy(story['fragments']['part']); b = slot['binding']
        b['logical_element']['contained_by'] = rid; b['boundaries'] = _boundaries(b['paragraph']['text'])
        slot.update(paragraph_id=pid, region_id=rid, source_snapshot_sha256=b['paragraph']['snapshot_sha256'], occupancy=None)
        state['slots'][sid] = slot
        slot['source_output'] = source_ownership.seed(state,sid)
        state.setdefault('source_adoptions',{})[sid] = deepcopy(slot['source_output']['created_from'])
        if not added: edge = dict(first)
        else:
            observed = next(e for e in shared['follows'] if rename[e['before']] == previous and rename[e['after']] == pid)
            edge = dict(minimum_baseline_gap=observed['minimum_baseline_gap'], region_start='reset-to-region-baseline')
        state['follows'].append(dict(edge,before=previous,after=pid,provenance='explicitly_confirmed'))
        state['paragraph_boundaries'].append(dict(before=previous,after=pid,kind='paragraph_boundary',provenance='explicitly_confirmed'))
        state['flow']['paragraphs'].append(pid); previous = pid; added.append(sid)
    return added


def _relayout(state, sids):
    for sid in sids:
        slot = state['slots'][sid]; b = slot['binding']; pid = slot['paragraph_id']
        baseline = slot['occupancy']['baseline'] if slot['occupancy'] else b['layout']['baseline']
        b['layout'] = _layout_options(state,pid,slot['region_id'],baseline,
            state['paragraph_policies'][pid]['first_line_indent'] if slot['range'][0] == 0 else 0)
        layout = b['layout']
        b['container']['region'] = dict(x=layout['x'],width=layout['width'],
            first_baseline=layout['baseline'],bottom=layout['max_bottom'])
        slot['binding'] = _reseal(b)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _band(box, bounds):
    return box[0] < bounds[2] and box[2] > bounds[0]


def inspect_fixed_foreign(source, *, page, line_ids, classification, body_bounds):
    """Observe explicitly classified, whole header/footer lines on this revision.

    This grants nothing. Pass the returned witness in ``next_page.fixed_foreign``
    for review. The caller declares a body rectangle containing the central 75%
    of the page height; all selected ink must lie outside its top/bottom interior
    in the corresponding outer eighth. Neither text nor page numbers imply a
    classification. Review additionally excludes owners and all usable regions.
    """
    from .content_stream import ContentPage
    if type(page) is not int or page < 1:
        raise PdfError('fixed foreign page must be a positive integer')
    analysis = _Analysis(source, page)
    if (classification not in ('header', 'footer') or not isinstance(body_bounds, list)
            or len(body_bounds) != 4 or not all(_finite(v) for v in body_bounds)):
        raise PdfError('fixed foreign needs explicit header/footer classification and finite body bounds')
    x0, y0, x1, y1 = body_bounds
    if (not 0 <= x0 < x1 <= analysis.width or not 0 <= y0 < y1 <= analysis.height
            or y0 > analysis.height * FIXED_MARGIN_FRACTION
            or y1 < analysis.height * (1 - FIXED_MARGIN_FRACTION)):
        raise PdfError('fixed foreign body bounds must include the central page body; only outer margins qualify')
    observed = {line['id']: line for line in analysis.lines}
    if (not isinstance(line_ids, list) or not line_ids or any(not isinstance(i, str) or i not in observed for i in line_ids)
            or len(set(line_ids)) != len(line_ids)):
        raise PdfError('fixed foreign needs distinct whole observed line IDs')
    lines = [line for line in analysis.lines if line['id'] in line_ids]
    if [line['id'] for line in lines] != line_ids:
        raise PdfError('fixed foreign lines must be in observed order')
    for line in lines:
        box = line['bbox']
        if (not 0 <= box[0] < box[2] <= analysis.width or not 0 <= box[1] < box[3] <= analysis.height
                or not (box[3] <= y0 if classification == 'header' else box[1] >= y1)):
            raise PdfError('fixed foreign line is not wholly outside the declared body in its classified margin')
    ids = {gid for line in lines for gid in line['glyph_ids']}
    observation = inspect_selection_source(source, page)
    # Keep both rendered observations (GID, font, origin, bounds) and interpreted
    # original codes. The revision hash also binds the exact font/program bytes.
    glyphs = json.loads(json.dumps([g for g in observation['glyphs'] if g['source_order'] in ids]))
    content = ContentPage(source, page)
    try:
        paint = [dict(glyph_ids=c.source_orders, code=c.code.hex(), font_resource=e.state.font.name,
                      origin=list(c.origin)) for e in content.events for c in e.chars if set(c.source_orders) & ids]
    finally:
        content.close()
    if (any(len(p['glyph_ids']) != 1 for p in paint)
            or sorted(gid for p in paint for gid in p['glyph_ids']) != sorted(ids)):
        raise PdfError('fixed foreign glyphs need exact individual source-code observations')
    return dict(schema=FIXED_FOREIGN_SCHEMA, source_sha256=observation['source_sha256'], page=page,
        page_observation_sha256=observation['page_observation_sha256'], classification=classification,
        body_bounds=deepcopy(body_bounds), line_ids=list(line_ids), lines=deepcopy(lines), glyphs=glyphs, paint=paint)


def _fixed_foreign(source, base, page, prior, analysis, owned, added, request):
    """Re-observe every assertion; exemptions confer no layout or paint authority."""
    if not isinstance(request, list):
        raise PdfError('fixed_foreign must be an explicit list of current margin witnesses')
    result = {prior.page: set(), analysis.page: set()}
    for witness in request:
        if (not isinstance(witness, dict) or type(witness.get('page')) is not int
                or witness['page'] not in result):
            raise PdfError('fixed foreign witness must name a page at this seam')
        observed = prior if witness['page'] == prior.page else analysis
        if (witness.get('source_sha256') != base['pdf_sha256']
                or witness.get('page_observation_sha256') != observed.observation_sha256):
            raise PdfError('fixed foreign witness differs from the current revision')
        try:
            current = inspect_fixed_foreign(source, **{k: witness[k] for k in
                ('page', 'line_ids', 'classification', 'body_bounds')})
        except KeyError as exc:
            raise PdfError('fixed foreign witness is incomplete') from exc
        if current != witness:
            raise PdfError('fixed foreign witness differs from the current revision')
        number = witness['page']; existing = number == prior.page
        ids = {gid for line in current['lines'] for gid in line['glyph_ids']}
        if ids & (owned if existing else added) or result[number] & set(current['line_ids']):
            raise PdfError('fixed foreign lines must remain unowned and may be witnessed only once')
        regions = [r['bounds'] for r in base['regions'].values() if r['page'] == number]
        regions += [d['bounds'] for d in base.get('continuation_destinations', {}).values() if d['page'] == number]
        if not existing:
            regions.append(page['region']['bounds'])
        body = current['body_bounds']
        if any(not (body[0] <= b[0] < b[2] <= body[2] and body[1] <= b[1] < b[3] <= body[3]) for b in regions):
            raise PdfError('fixed foreign body bounds must contain all confirmed and proposed regions')
        selected = [l['id'] for l in analysis.lines if set(l['glyph_ids']) <= added]
        protected = (base['pages'][str(number)]['protected_regions'] if existing else
                     _protected(analysis, analysis.foreign(selected))[str(number)])
        for line in current['lines']:
            box = line['bbox']
            if not any(p['role'] in ('fixed', 'header', 'footer') and
                       p['bounds'][0] <= box[0] < box[2] <= p['bounds'][2] and
                       p['bounds'][1] <= box[1] < box[3] <= p['bounds'][3] for p in protected):
                raise PdfError('fixed foreign line needs an independently protected region')
        result[number].update(current['line_ids'])
    return result


def _page_boundary(source, base, page, analysis, last, tail, fixed_foreign=None):
    """Witness the current page seam; it orders owners, it never yields a gap."""
    number = base['regions'][tail]['page']
    prior = _Analysis(source, number)
    owned = {gid for slot in base['slots'].values() if base['regions'][slot['region_id']]['page'] == number
             for gid in slot['binding']['paragraph']['selection']['glyph_ids']}
    own_last = {gid for sid in _slots(base, last) if base['regions'][base['slots'][sid]['region_id']]['page'] == number
                for gid in base['slots'][sid]['binding']['paragraph']['selection']['glyph_ids']}
    lines = [l for l in prior.lines if set(l['glyph_ids']) & owned]
    if any(not set(l['glyph_ids']) <= owned for l in lines):
        raise PdfError('current page seam has a partly owned observed line')
    end = max(lines, key=lambda l: l['baseline'], default=None)
    if end is None or not set(end['glyph_ids']) <= own_last:
        raise PdfError('the last owned line before the page break is not the last confirmed paragraph')
    added = {gid for p in page['paragraphs'] for gid in p['glyph_ids']}
    fixed = _fixed_foreign(source, base, page, prior, analysis, owned, added,
                           fixed_foreign if fixed_foreign is not None else [])
    used = {number: set(), analysis.page: set()}
    for line in prior.lines:
        if (line['baseline'] > end['baseline'] and not set(line['glyph_ids']) <= owned
                and _band(line['bbox'], base['regions'][tail]['bounds'])):
            if line['id'] not in fixed[number]:
                raise PdfError('page seam skips unselected text after the last confirmed paragraph')
            used[number].add(line['id'])
    head = page['paragraphs'][0]['lines'][0]
    for line in analysis.lines:
        if (line['baseline'] < head['baseline'] and not set(line['glyph_ids']) <= added
                and _band(line['bbox'], page['region']['bounds'])):
            if line['id'] not in fixed[analysis.page]:
                raise PdfError('page seam skips unselected text before the additional paragraphs')
            used[analysis.page].add(line['id'])
    if used != fixed:
        raise PdfError('fixed foreign witnesses may name only foreign lines obstructing this page seam')
    boundary = dict(previous_page=number, previous_region=tail,
        last_owned_line=dict(id=end['id'], baseline=end['baseline'], text=end['text']),
        next_page=analysis.page, first_added_line=dict(id=head['id'], baseline=head['baseline'], text=head['text']),
        gap='caller-selected same-page gap; coordinates of different pages are never subtracted')
    if fixed_foreign:
        boundary['fixed_foreign'] = deepcopy(fixed_foreign)
    return boundary


def _continuation_request(request, base, analysis, last, rid, added_ids):
    """Exactly one explicit authority for the last paragraph's next-page continuation."""
    if 'vacated_continuation' in request:
        kind, item = 'vacated-source-owner-release', request['vacated_continuation']
        keys = {'region_id', 'region', 'release_paragraphs', 'confirmation'}
    elif 'continuation' in request:
        kind, item = 'independently-confirmed-empty-destination', request['continuation']
        keys = {'region_id', 'region', 'destination'}
    else:
        return None, None
    if not isinstance(item, dict) or set(item) != keys:
        raise PdfError('next-page continuation needs a region and its explicit insertion authority')
    crid, region = item['region_id'], item['region']
    if (not isinstance(crid, str) or not crid or crid == rid or crid in base['regions']
            or not isinstance(region, dict) or set(region) != REGION_KEYS or region['page'] != analysis.page
            or not isinstance(region['bounds'], list) or len(region['bounds']) != 4
            or not all(_finite(v) for v in (*region['bounds'], region['x'], region['width'], region['first_baseline']))):
        raise PdfError('next-page continuation needs a distinct finite region on the additional page')
    grant = item['confirmation'] if kind.startswith('vacated') else item['destination']
    if (not isinstance(grant, dict) or grant.get('paragraph_id') != last or grant.get('region_id') != crid
            or grant.get('page') != analysis.page or grant.get('bounds') != region['bounds']):
        raise PdfError('next-page continuation must serve the last confirmed paragraph in exactly its region')
    if kind.startswith('vacated'):
        release = item['release_paragraphs']
        if (set(grant) - CONFIRMATION_KEYS or not isinstance(release, list) or not release
                or len(set(release)) != len(release) or not set(release) <= set(added_ids)):
            raise PdfError('vacated continuation may release only explicitly adopted next-page owners')
    elif 'vacated_space' in grant or grant.get('source_pdf_sha256') != base['pdf_sha256']:
        raise PdfError('empty continuation destination is not confirmed on the current revision')
    return kind, item


def _next_page_review(source, base, page, analysis, paragraphs, relationship, request):
    """Review owners on the page after the last source region; grants nothing."""
    if (not isinstance(request, dict) or not {'region_id', 'page_break'} <= set(request)
            or set(request) - NEXT_PAGE_KEYS or {'continuation', 'vacated_continuation'} <= set(request)):
        raise PdfError('next-page extension needs a new region, an explicit page break and at most one continuation')
    flow = base['flow']; last = flow['paragraphs'][-1]
    sources = [sid for sid in _slots(base, last) if base['slots'][sid].get('destination_id') is None]
    tail = base['slots'][sources[-1]]['region_id']
    if tail != flow['regions'][-1] or analysis.page != base['regions'][tail]['page'] + 1:
        raise PdfError('additional owners must start on the page after the last confirmed source region')
    if (any(r['page'] == analysis.page for r in base['regions'].values())
            or any(d['page'] == analysis.page for d in base.get('continuation_destinations', {}).values())):
        raise PdfError('the next page already holds confirmed regions; extend its last region instead')
    rid = request['region_id']
    if not isinstance(rid, str) or not rid or rid in base['regions']:
        raise PdfError('the next-page source region needs a new identity')
    ids = [p['id'] for p in paragraphs]
    edge = request['page_break']
    if (not isinstance(edge, dict) or set(edge) != {'before', 'after', 'minimum_baseline_gap', 'placement'}
            or edge['before'] != last or edge['after'] != ids[0] or edge['placement'] != PAGE_BREAK_PLACEMENT
            or not _finite(edge['minimum_baseline_gap']) or edge['minimum_baseline_gap'] <= 0):
        raise PdfError('next-page owners need an explicit source-region floor and caller-selected same-page gap')
    overrides = request.get('page_overrides', {})
    if (not isinstance(overrides, dict) or set(overrides) - {'width', 'region_top', 'region_bottom'}
            or not all(_finite(v) for v in overrides.values())):
        raise PdfError('next-page overrides may only change qualified region geometry')
    if 'fixed_foreign' in request and not isinstance(request['fixed_foreign'], list):
        raise PdfError('fixed_foreign must be an explicit list of current margin witnesses')
    boundary = _page_boundary(source, base, page, analysis, last, tail, request.get('fixed_foreign'))
    kind, item = _continuation_request(request, base, analysis, last, rid, ids)
    return dict(base_model_sha256=base['model_sha256'], source_sha256=base['pdf_sha256'],
        retained_paragraphs=flow['paragraphs'], retained_slots=sorted(base['slots']),
        previous_regions=base['regions'], added_paragraphs=deepcopy(page['paragraphs']),
        region_id=rid, proposed_page_region=page['region'], page_overrides=overrides,
        page_break=dict(edge, source_break=dict(before_region=tail, after_region=rid)),
        page_boundary=boundary, continuation_kind=kind, continuation=deepcopy(item), relationship=relationship)


def _accept_next_page(source, base, proposal, evidence, provider_choices):
    from .content_stream import ContentPage
    from .continuation import _confirm_destination, page_witness
    page = proposal['pages'][0]
    analysis, files, roots = _recompute(source, page)
    choices = provider_choices if provider_choices is not None else {}
    if not isinstance(choices,dict) or set(choices) - {str(analysis.page)}:
        raise PdfError('provider choices must name the additional source page')
    # Review proved that this page has no owner or destination in the flow, so
    # no marker inventory is claimed: the standalone B1 proof runs in full too.
    built, plan = _build(analysis, font_files=files, font_roots=roots, overrides=evidence['page_overrides'],
        provider_choices=choices.get(str(analysis.page)),
        **{k: page['font_search'][k] for k in ('include_embedded_fonts','font_cache','new_text')})
    if built['status'] != 'proposed' or plan is None:
        raise PdfError('additional page qualification failed: ' + str(built['refusals'] + built['unresolved']))
    stories, shared = _arguments(analysis, plan)
    state = deepcopy(base); rid = evidence['region_id']; number = str(analysis.page)
    state['regions'][rid] = dict(shared['regions']['R1'], provenance='explicitly_confirmed')
    state['flow']['regions'].append(rid)
    protected = shared['protected_regions'][number]
    for old in base['pages'][number]['protected_regions']:
        if not any(p['bounds'] == old['bounds'] for p in protected):
            protected.append({k:v for k,v in old.items() if k != 'provenance'})
    state['pages'][number]['protected_regions'] = [dict(p,provenance='explicitly_confirmed') for p in protected]
    edge = evidence['page_break']
    added = _adopt(source, state, stories, shared, proposal, rid, dict(minimum_baseline_gap=edge['minimum_baseline_gap'],
        region_start=edge['placement'], source_break=edge['source_break']))
    state['paragraph_policies'][edge['after']]['break_before'] = 'next-region'
    # Existing owners, generated slots and their layouts are not touched.
    _relayout(state, added)
    item = evidence['continuation']
    if item is not None:
        crid = item['region_id']
        state['regions'][crid] = dict(item['region'], provenance='explicitly_confirmed')
        state['flow']['regions'].insert(state['flow']['regions'].index(rid), crid)
        if evidence['continuation_kind'] == 'vacated-source-owner-release':
            release = {sid: state['slots'][sid] for sid in added
                       if state['slots'][sid]['paragraph_id'] in item['release_paragraphs']}
            authority = dict(policy=vacated_space.POLICY,
                             owners={sid: vacated_space.owner_contract(s) for sid, s in release.items()})
            proof = vacated_space.evidence(state, dict(item['confirmation'], vacated_space=authority))
            # Geometry, emptiness except the named current owners, graphics
            # state, CTM and clip are independently re-proved on this revision.
            destination = _confirm_destination(source, **item['confirmation'],
                                               _vacated_space=authority, _release_proof=proof)
        else:
            destination = deepcopy(item['destination'])
        ident = destination['destination_id']
        if ident in state.get('continuation_destinations', {}):
            raise PdfError('next-page continuation destination identity already exists')
        content = ContentPage(source, analysis.page)
        try:
            binding = page_witness(content, [destination], set())[0][ident]
        finally:
            content.close()
        if binding['program_sha256'] != destination['source_program_sha256']:
            raise PdfError('confirmed continuation source program differs')
        state.setdefault('continuation_destinations', {})[ident] = destination
        state.setdefault('destination_bindings', {})[ident] = binding
    return _complete(source, base, state, analysis, proposal, evidence)
