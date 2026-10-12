"""Conditional continuation authority over explicitly owned, jointly rewritten text.

Geometry is a capacity ceiling, not a claim that the current PDF is empty.
Every paint exclusion is bound to a current source owner, and every writer
using it owes that owner's complete removal/rewrite in the same Transaction.
"""
from copy import deepcopy

from .attributed import digest
from .backend import PdfError
from . import source_ownership

POLICY = 'joint-source-owner-removal-and-final-placement'


def owner_contract(slot):
    return dict(paragraph_id=slot['paragraph_id'], region_id=slot['region_id'],
        source_snapshot_sha256=slot['source_snapshot_sha256'],
        source_output=source_ownership.immutable(slot['source_output']))


def owners(state, destination):
    authority = destination.get('vacated_space')
    if authority is None:
        return {}
    if (not isinstance(authority, dict) or set(authority) != {'policy', 'owners'}
            or authority['policy'] != POLICY or not authority['owners']):
        raise PdfError('vacated-space authority needs explicit source owners')
    result = {}
    order = state['flow']['paragraphs']
    for sid, expected in authority['owners'].items():
        slot = state['slots'].get(sid)
        if (slot is None or slot.get('destination_id') is not None
                or owner_contract(slot) != expected
                or state['regions'][slot['region_id']]['page'] != destination['page']
                or order.index(slot['paragraph_id']) <= order.index(destination['paragraph_id'])
                or 'marked_structure' in slot['binding']['paragraph']):
            raise PdfError('vacated-space authority differs from jointly owned subsequent source')
        result[sid] = slot
    return result


def evidence(state, destination):
    selected = owners(state, destination)
    if not selected:
        return None
    value = dict(flow=state['flow'], allocation_provenance=state['allocation_provenance'],
        pdf_sha256=state['pdf_sha256'], slots=selected)
    # An owner adopted from an edited revision stays uninitialized until its
    # first save; its sealed adoption record is the only creation authority.
    adoptions = {sid: state['source_adoptions'][sid] for sid in selected if sid in state.get('source_adoptions', {})}
    if adoptions:
        value['source_adoptions'] = adoptions
    return deepcopy(value)


def glyphs(source, content, destination, proof):
    """Re-prove current bindings; a saved glyph-ID list alone grants no exemption."""
    if 'vacated_space' not in destination:
        if proof is not None: raise PdfError('unexpected vacated-space witness')
        return set()
    from .editable import open_editable
    from .selection import source_sha
    if (not isinstance(proof, dict) or proof.get('pdf_sha256') != source_sha(source)
            or set(proof.get('slots', {})) != set(destination['vacated_space']['owners'])):
        raise PdfError('vacated-space ownership witness is absent or stale')
    result = set()
    for sid, slot in proof['slots'].items():
        if owner_contract(slot) != destination['vacated_space']['owners'][sid]:
            raise PdfError('vacated-space source owner identity changed')
        record = source_ownership.record_identity(proof, sid)
        binding = slot['binding']; snapshot = binding['paragraph']
        if (snapshot['selection']['page'] != destination['page']
                or snapshot['selection']['page'] != content.page.number + 1
                or 'marked_structure' in snapshot
                or open_editable(source, binding)['status'] != 'restored'):
            raise PdfError('vacated-space current source binding is not restored')
        if record['state'] == 'owned':
            source_ownership.owned_body(content, record, snapshot)
        selected = set(snapshot['selection']['glyph_ids'])
        if result & selected: raise PdfError('vacated-space owners share glyphs')
        result |= selected
    return result


def overlap_allowed(state, first, second):
    """Only a conditional destination and its exact source-owner region may overlap."""
    for d in state.get('continuation_destinations', {}).values():
        if d['region_id'] not in (first, second) or 'vacated_space' not in d: continue
        other = second if first == d['region_id'] else first
        if other in {s['region_id'] for s in owners(state, d).values()}:
            return True
    return False


def check_transaction(page, plan):
    obligation = getattr(plan, 'vacated_space', None)
    if obligation is None: return
    d, proof = obligation['destination'], obligation['proof']
    glyphs(page.source, page.content, d, proof)
    by_owner = {p.owner: p for p in page.plans}
    certificates = []
    for sid, slot in proof['slots'].items():
        other = by_owner.get(sid)
        selected = set(slot['binding']['paragraph']['selection']['glyph_ids'])
        if (other is None or other is plan or other.kind != 'edit'
                or not selected <= other.consumed or not other.removal_mutations
                or getattr(other, 'source_output_identity', None) != owner_contract(slot)['source_output']):
            raise PdfError('vacated-space draw requires its source owner removal in this same Transaction')
        if any(a.intersects(b, .001) for a in plan.final_rects for b in other.final_rects):
            raise PdfError('vacated-space final placements overlap')
        certificates.append(dict(source_owner=sid, current_snapshot=slot['binding']['paragraph']['snapshot_sha256'],
            consumed_glyphs=sorted(selected), owner_final_bounds=[list(r.tuple()) for r in other.final_rects],
            removed_by=[m.record() for m in other.removal_mutations]))
    plan._report['vacated_space'] = dict(source_pdf_sha256=proof['pdf_sha256'],
        authority_sha256=digest(d), drawable_bounds=d['bounds'],
        used_bounds=[list(r.tuple()) for r in plan.final_rects], owners=certificates)


def confirm(source, model, *, region_id, before_region, region, release_paragraphs, confirmation):
    """Explicitly accept conditional capacity on an already jointly accepted flow."""
    from .shared_flow import (_restore, _plan, _contract, _validate, _reseal,
                              _bind_confirmed_destinations)
    from .continuation import _confirm_destination
    state = _restore(source, model)
    if (state['schema'] != 'pdfengine-shared-flow-2'
            or state['allocation_provenance'] != 'observed-source-uncomposed'
            or region_id in state['regions'] or before_region not in state['regions']
            or not isinstance(release_paragraphs, list) or not release_paragraphs
            or len(set(release_paragraphs)) != len(release_paragraphs)):
        raise PdfError('vacated-space confirmation requires a distinct region and explicit initial source owners')
    pid = confirmation.get('paragraph_id')
    edge = next((e for e in state['follows'] if e['before'] == pid
                 and e.get('source_break', {}).get('after_region') == before_region), None)
    selected = {sid:s for sid,s in state['slots'].items() if s['paragraph_id'] in release_paragraphs
                and s['region_id'] == before_region and s.get('destination_id') is None}
    if (edge is None or edge['after'] not in release_paragraphs
            or {s['paragraph_id'] for s in selected.values()} != set(release_paragraphs)
            or region['page'] != state['regions'][before_region]['page']
            or confirmation.get('region_id') != region_id or confirmation.get('page') != region['page']
            or confirmation.get('bounds') != region['bounds']):
        raise PdfError('vacated-space continuation must precede its independently owned source follower')
    before = _plan(source, state, {})
    authority = dict(policy=POLICY, owners={sid:owner_contract(s) for sid,s in selected.items()})
    provisional = dict(confirmation, vacated_space=authority)
    proof = evidence(state, provisional)
    dest = _confirm_destination(source, **confirmation, _vacated_space=authority, _release_proof=proof)
    if dest['destination_id'] in state.get('continuation_destinations', {}):
        raise PdfError('vacated-space destination identity already exists')
    state['regions'][region_id] = dict(region, provenance='explicitly_confirmed')
    state['flow']['regions'].insert(state['flow']['regions'].index(before_region), region_id)
    all_destinations = dict(state.get('continuation_destinations', {}), **{dest['destination_id']: dest})
    _bind_confirmed_destinations(source, state, all_destinations)
    state['contract_sha256'] = _contract(state)
    state = _validate(source, _reseal(state))
    after = _plan(source, state, {})
    if after['new_slots'] or after['fragments'] != before['fragments']:
        raise PdfError('vacated-space confirmation changes original no-edit placement')
    return state
