"""Explicitly join B1 source paragraph fragments across consecutive occupied pages.

Each page keeps its own B1 evidence and source owners. A caller supplies the
semantic paragraph grouping; geometry never invents that relationship. This
bounded bootstrap joins paragraphs already split across a page seam. It grants
no empty destination, moves no unselected content, and changes no T2 schema.
"""
from copy import deepcopy

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


def propose_owned_flow(source, page_proposals, *, paragraphs, relationship):
    """Propose caller-declared paragraph joins without accepting or editing the PDF.

    `paragraphs` is an ordered list of {id, parts: [{page, paragraph_id}]}.
    Each part names a whole paragraph from an explicit B1 page selection. Parts
    of one logical paragraph concatenate exactly, without an invented separator.
    `relationship` records the caller's textual basis, not a geometry inference.
    Provider substitutions still require explicit per-page choices at acceptance.
    """
    _selection(page_proposals, paragraphs, relationship)
    for page in page_proposals:
        _recompute(source, page)
    return _seal(dict(schema=SCHEMA, source_sha256=source_sha(source),
        pages=page_proposals, paragraphs=paragraphs, relationship=relationship,
        relationship_provenance='caller-declared-textual-relationship; explicit acceptance required'))


def _seam(analysis, plan, at_end):
    """Refuse a join that skips observed text in the selected column at a page edge."""
    edge = plan['column'][-1][-1] if at_end else plan['column'][0][0]
    selected = {line['id'] for para in plan['column'] for line in para}
    left, _, right, _ = plan['region']['bounds']
    for line in analysis.lines:
        beyond = line['baseline'] > edge['baseline'] if at_end else line['baseline'] < edge['baseline']
        if line['id'] not in selected and beyond and line['bbox'][0] < right and line['bbox'][2] > left:
            raise PdfError('page seam skips unselected text; cannot prove a contiguous owned paragraph')


def _compose(source, entries, paragraphs):
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
            raise PdfError('the paragraph gap across a page break is unobserved; no follows gap is inferred')
        local_follows = args[tail['page']][1]['follows']
        edge = next((f for f in local_follows if f['before'] == tail['paragraph_id'] and f['after'] == head['paragraph_id']), None)
        if edge is None:
            raise PdfError('no observed follows relationship between the selected paragraphs')
        follows.append(dict(edge, before=before['id'], after=after['id']))
    confirmed = {pid: confirm_story(source, **spec) for pid, spec in stories.items()}
    state = confirm_shared_flow(source, confirmed, flow_id='cross-page-owned-flow',
        paragraph_order=[p['id'] for p in paragraphs], regions=regions, region_order=list(regions),
        slot_regions=slots, paragraph_policies=policies, follows=follows, protected_regions=protected)
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
    numbers = _selection(proposal['pages'], proposal['paragraphs'], proposal['relationship'])
    choices = provider_choices if provider_choices is not None else {}
    if not isinstance(choices, dict) or set(choices) - {str(n) for n in numbers}:
        raise PdfError('provider choices must name selected page numbers')
    entries, evidence = [], []
    for page in proposal['pages']:
        analysis, files, roots = _recompute(source, page)
        built, plan = _build(analysis, font_files=files, font_roots=roots,
            provider_choices=choices.get(str(analysis.page)),
            **{k: page['font_search'][k] for k in ('include_embedded_fonts', 'font_cache', 'new_text')})
        if built['status'] != 'proposed' or plan is None:
            raise PdfError(f'page {analysis.page} cannot be accepted: {built["refusals"] + built["unresolved"]}')
        entries.append((analysis, plan))
        evidence.append(dict(binding=page['binding'], proposal_digest=page['digest'], reproduction=built['reproduction'],
            providers={lid: dict(sha256=c['sha256'], font_index=c['font_index'], provenance=how)
                       for lid, (c, how) in sorted(plan['providers'].items())}))
    state, reproduction = _compose(source, entries, proposal['paragraphs'])
    return dict(state=state, receipt=dict(schema='pdfengine-owned-flow-acceptance-1',
        proposal_digest=proposal['digest'], source_sha256=proposal['source_sha256'], pages=evidence,
        paragraphs=deepcopy(proposal['paragraphs']), relationship=proposal['relationship'],
        relationship_provenance=proposal['relationship_provenance'], reproduction=reproduction,
        state_model_sha256=state['model_sha256']))


def accept_owned_flow(source, proposal, *, provider_choices=None):
    """Return the existing shared-flow v2 state from an explicit joint acceptance."""
    return accept_owned_flow_report(source, proposal, provider_choices=provider_choices)['state']
