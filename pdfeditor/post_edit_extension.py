"""Append current-revision source owners without recreating existing output slots."""
from copy import deepcopy

from .backend import PdfError
from .elements import _close
from .page_proposal import (_Analysis, _arguments, _build, _protected, _recompute,
                            _reproduce_fragments, _seal)
from .shared_flow import (_restore, _slots, _plan, _contract, _breaks, _validate,
                          _layout_options, _reseal, SOURCE_PLACEMENT)
from .story_flow import confirm_story, _boundaries
from . import source_ownership


def review(source, base, pages, paragraphs, relationship, append_to_region):
    base = _restore(source, base)
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
    rid = append_to_region
    if rid != base['flow']['regions'][-1] or base['regions'][rid]['page'] != analysis.page:
        raise PdfError('additional owners must follow the last confirmed source region')
    observed = [(analysis.page, p['id']) for p in page['paragraphs']]
    ids = [p['id'] for p in paragraphs]
    if len(set(ids)) != len(ids) or set(ids) & set(base['paragraphs']) or any(not isinstance(i,str) or not i for i in ids):
        raise PdfError('additional paragraph identities must be new and distinct')
    if (page.get('target') is None or any(set(p) != {'id','parts'} or len(p['parts']) != 1 for p in paragraphs)
            or [(p['parts'][0]['page'], p['parts'][0]['paragraph_id']) for p in paragraphs] != observed):
        raise PdfError('append all explicitly selected whole paragraphs in observed order')
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
    return dict(base_model_sha256=base['model_sha256'], source_sha256=base['pdf_sha256'],
        retained_paragraphs=base['flow']['paragraphs'], retained_slots=sorted(base['slots']),
        added_paragraphs=deepcopy(page['paragraphs']), append_to_region=rid,
        previous_regions=base['regions'], proposed_page_region=page['region'],
        current_baseline_gap=first-last, relationship=relationship)


def propose(source, base, pages, *, paragraphs, relationship, append_to_region):
    from .owned_flow_proposal import SCHEMA
    base = _restore(source, base)
    evidence = review(source, base, pages, paragraphs, relationship, append_to_region)
    return _seal(dict(schema=SCHEMA, source_sha256=base['pdf_sha256'], pages=deepcopy(pages),
        paragraphs=deepcopy(paragraphs), relationship=relationship,
        edited_extension=dict(base_model=deepcopy(base), review=evidence), append_to_region=append_to_region))


def accept(source, proposal, provider_choices):
    extension = proposal['edited_extension']
    base = _restore(source, extension['base_model'])
    evidence = review(source, base, proposal['pages'], proposal['paragraphs'],
                      proposal['relationship'], proposal['append_to_region'])
    if extension['review'] != evidence:
        raise PdfError('post-edit extension review differs from the current revision')
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
    rid = proposal['append_to_region']; region = state['regions'][rid]
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
    rename = {p['parts'][0]['paragraph_id']:p['id'] for p in proposal['paragraphs']}
    previous = state['flow']['paragraphs'][-1]
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
        if previous == base['flow']['paragraphs'][-1]: gap = evidence['current_baseline_gap']
        else:
            edge = next(e for e in shared['follows'] if rename[e['before']] == previous and rename[e['after']] == pid)
            gap = edge['minimum_baseline_gap']
        state['follows'].append(dict(before=previous,after=pid,minimum_baseline_gap=gap,
            region_start='reset-to-region-baseline',provenance='explicitly_confirmed'))
        state['paragraph_boundaries'].append(dict(before=previous,after=pid,kind='paragraph_boundary',provenance='explicitly_confirmed'))
        state['flow']['paragraphs'].append(pid); previous = pid
    # Capacity metadata changes, but snapshots, source markers, destination
    # identities, style registries and existing physical layouts remain intact.
    for sid, slot in state['slots'].items():
        b = slot['binding']; pid = slot['paragraph_id']
        baseline = slot['occupancy']['baseline'] if slot['occupancy'] else b['layout']['baseline']
        b['layout'] = _layout_options(state,pid,slot['region_id'],baseline,
            state['paragraph_policies'][pid]['first_line_indent'] if slot['range'][0] == 0 else 0)
        layout = b['layout']
        b['container']['region'] = dict(x=layout['x'],width=layout['width'],
            first_baseline=layout['baseline'],bottom=layout['max_bottom'])
        slot['binding'] = _reseal(b)
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
