"""Plan several authored edits, then apply them in one guarded transaction.

This is a bounded scheduler, not a collision solver. Final positions are
measured before any mutation; every element edit or move is a plan of one
transaction whose final-state guards, single save and single verification
replace the former sequence of intermediate PDFs. All edits refer to logical
Unicode offsets in one input revision, once per identity.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile

from .attributed import digest
from .backend import PdfError
from .document_flow import _element_relations, _fixed_relations, _fonts, _order, _reseal, open_document, rebind_entry
from .editable import _publish, bind_document_edit, plan_document_edit
from .elements import _close, plan_element_move
from .model import Rect
from .paragraph import plan_paragraph
from .proof_session import proof_session
from .replay import ensure_destination
from .selection import source_sha
from .transaction import Transaction


POLICY = 'final-placement; single-transaction-v2'
LINE_KEYS = ('start', 'end', 'baseline', 'width', 'ascent', 'descent')


def _restore(source, model):
    result = open_document(source, model)
    if result['status'] != 'restored':
        raise PdfError('batch edit requires confirmation: ' + result['reason'])
    return result['state']


def _requests(state, changes):
    if not isinstance(changes, dict) or not changes:
        raise PdfError('batch needs a nonempty mapping of logical identities to edits')
    result = deepcopy(changes)
    for ident, change in result.items():
        if ident not in state['elements']:
            raise PdfError('batch contains an unknown logical identity')
        if (not isinstance(change, dict) or 'edits' not in change
                or set(change) - {'edits', 'empty_style_id'}
                or not isinstance(change['edits'], list)):
            raise PdfError('each batch identity needs edits and optional empty_style_id only')
    return result


def _check_extents(state, positions):
    region = Rect(*state['container']['bounds'])
    for ident, position in positions.items():
        b = state['elements'][ident]['binding']
        first, last = position['baseline'], position['last_baseline']
        if (not all(math.isfinite(v) for v in (first, last)) or last < first - .002
                or first < region.y0 or last >= b['layout']['max_bottom']):
            raise PdfError('planned logical extent exceeds the confirmed fixed container')
        # Ownership does not acquire resizing semantics: its whole original
        # paint bundle may only translate by the paragraph's baseline delta.
        dy = first - b['layout']['baseline']
        owned = set(state['elements'][ident]['owned_paints'])
        for path in b['element']['paths']:
            if path['source_id'] not in owned:
                continue
            for paint in path['proof']['paints']:
                a, top, c, bottom = paint['bounds']
                if not region.contains(Rect(a, top + dy, c, bottom + dy), .002):
                    raise PdfError('planned owned paint exceeds the fixed container; resize is not authorized')


def _plan(source, state, changes):
    order, _ = _order(state)
    incoming = {r['after']: r for r in state['follows']}
    heights = {i: e['extent']['last_baseline'] - e['binding']['layout']['baseline']
               for i, e in state['elements'].items()}
    final, measured, deltas = {}, {}, {}
    # Measure directly at the FINAL baseline using only confirmed width/bottom.
    for ident in order:
        entry = state['elements'][ident]
        b = entry['binding']
        edge = incoming.get(ident)
        first = (final[edge['before']]['last_baseline'] + edge['gap'] if edge else
                 b['layout']['baseline'])
        if ident in changes:
            p = plan_paragraph(source, b['paragraph'], changes[ident]['edits'],
                               fonts=_fonts(b), **dict(b['layout'], baseline=first))
            measured[ident] = p
            height = p['last_baseline'] - first
            deltas[ident] = height - heights[ident]
            last = p['last_baseline']
        else:
            last = first + heights[ident]
        final[ident] = dict(baseline=first, last_baseline=last)
    _check_extents(state, final)
    for ident, position in final.items():
        # Source-only elements keep their original glyph envelope. Reflowed
        # ink, vectors, images, annotations and clips are checked by the
        # transaction; line measurement is not an ink collision certificate.
        b = state['elements'][ident]['binding']
        bounds = b['element']['text_element']['observed_bounds']
        if ident not in changes and bounds is not None:
            box = Rect(**bounds)
            dy = position['baseline'] - b['layout']['baseline']
            if not Rect(*state['container']['bounds']).contains(
                    Rect(box.x0, box.y0 + dy, box.x1, box.y1 + dy), .002):
                raise PdfError('planned untouched text exceeds the confirmed container')

    def phase(ident):
        delta = deltas[ident]
        return 0 if delta < -.002 else 2 if delta > .002 else 1
    schedule = sorted(changes, key=lambda i: (phase(i), -order.index(i)))
    result = dict(schema='pdfengine-flow-plan-2', source_sha256=state['pdf_sha256'],
                  model_sha256=state['model_sha256'], changes_sha256=digest(changes),
                  policy=POLICY, container=deepcopy(state['container']), follows=deepcopy(state['follows']),
                  final_positions=final, paragraph_plans=measured, extent_deltas=deltas,
                  schedule=schedule,
                  paint_authority='unchanged: explicit rigid ownership only; no resize',
                  collision_status='not_certified; the transaction verifies the final state')
    result['plan_sha256'] = digest(result)
    return result


@proof_session
def plan_flow(source, model, changes):
    """Read-only final placement; no source mutation or paint permission."""
    state = _restore(source, model)
    return _plan(source, state, _requests(state, changes))


def plan_document_transaction(transaction, state, changes, plan):
    """Add every element's edit or move to one transaction at its final position."""
    page = transaction.page(state['container']['page'])
    order, _ = _order(state)
    entries = {}
    for ident in order:
        entry = state['elements'][ident]
        b = entry['binding']
        position = plan['final_positions'][ident]
        dy = position['baseline'] - b['layout']['baseline']
        moved = abs(dy) > .002
        # Owned paint and anchored decoration travel with the element; the
        # rest of its confirmed relations stay fixed to the page.
        relations = [dict(r, behavior='fixed-to-element' if r['source_id'] in entry['owned_paints'] or r['relation'] == 'decorates'
                          else 'fixed-to-page')
                     for r in _element_relations(b['element'], _fixed_relations(b), b['anchors'])]
        if ident in changes:
            change = changes[ident]
            paints = None
            if moved and entry['owned_paints']:
                paints = plan_element_move(page, b['element'], relations, dx=0, dy=dy,
                                           paragraph_snapshot=b['paragraph'], owner=ident, text=False)
            edit = plan_document_edit(page, b, change['edits'], empty_style_id=change.get('empty_style_id'),
                                      owner=ident, baseline=position['baseline'])
            entries[ident] = dict(kind='edit', plan=edit, paints=paints, dy=dy if moved else 0)
        elif moved:
            move = plan_element_move(page, b['element'], relations, dx=0, dy=dy,
                                     paragraph_snapshot=b['paragraph'], owner=ident)
            entries[ident] = dict(kind='move', plan=move, paints=None, dy=dy)
        else:
            entries[ident] = dict(kind='fixed', plan=None, paints=None, dy=0)
    return entries


def bind_document_transaction(result, state, entries, plan):
    """Rebuild every element binding from the saved revision through the identity map."""
    identity = result.identity(state['container']['page'])
    elements, reports = {}, {}
    for ident, entry in entries.items():
        previous = state['elements'][ident]
        if entry['kind'] == 'edit':
            binding, report = bind_document_edit(identity, entry['plan'], result)
            if entry['dy']:
                binding['layout_provenance']['baseline'] = 'generated_from_explicit_follows'
                binding = _reseal(binding)
            owned = [identity.map_path(i) for i in previous['owned_paints']]
            measured = plan['paragraph_plans'][ident]
            elements[ident] = dict(binding=binding, owned_paints=owned,
                                   extent=dict(last_baseline=measured['last_baseline'], provenance='generated-by-pdfengine'))
            reports[ident] = dict(kind='edit', element_id=ident, report=report)
            if entry['paints'] is not None:
                reports[ident]['paint_move'] = entry['paints'].report(result)
        else:
            elements[ident] = rebind_entry(identity, previous, dy=entry['dy'])
            reports[ident] = dict(kind=entry['kind'], element_id=ident,
                                  report=entry['plan'].report(result) if entry['plan'] else None)
    return elements, reports


def _verify_positions(state, expected):
    actual = {i: dict(baseline=e['binding']['layout']['baseline'],
                      last_baseline=e['extent']['last_baseline']) for i, e in state['elements'].items()}
    if not _close(actual, expected):
        raise PdfError('writer positions differ from the precomputed batch layout')


@proof_session
def edit_flow_batch(source, model, output, model_output, changes):
    """Publish all requested edits together, after one transaction verifies.

    Plans supplied by callers are not accepted as authority. Recompute from
    the exact input PDF/model/font recipes. A failure at any step publishes
    neither PDF nor sidecar. This is not a crash-atomic two-file filesystem API.
    """
    initial = _restore(source, model)
    changes = _requests(initial, changes)
    plan = _plan(source, initial, changes)
    output = ensure_destination(output, source)
    model_output = ensure_destination(model_output, source)
    if output == model_output:
        raise PdfError('PDF and document model need distinct destinations')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.flow-batch-', dir=output.parent) as directory:
        root = Path(directory)
        target = root / 'edited.pdf'
        with Transaction(source) as transaction:
            entries = plan_document_transaction(transaction, initial, changes, plan)
            result = transaction.commit(target)
            try:
                elements, reports = bind_document_transaction(result, initial, entries, plan)
                mutation_map = result.mutation_map()
            finally:
                result.close()
        state = deepcopy(initial)
        state['elements'] = elements
        _verify_positions(state, plan['final_positions'])
        for ident, measured in plan['paragraph_plans'].items():
            b = state['elements'][ident]['binding']
            lines = [{k: line[k] for k in LINE_KEYS} for line in b['physical_layout']['lines']]
            if b['paragraph']['text'] != measured['text'] or not _close(lines, measured['lines']):
                raise PdfError('final authored text or shaped lines differ from the batch plan')
        if source_sha(source) != plan['source_sha256']:
            raise PdfError('batch input PDF changed before publication')
        state.update(pdf_sha256=source_sha(target), previous_model_sha256=initial['model_sha256'])
        state = _restore(target, _reseal(state))
        if state['container'] != initial['container'] or state['follows'] != initial['follows']:
            raise PdfError('batch changed a confirmed semantic relation or layout policy')
        state_file = root / 'document.json'
        state_file.write_bytes((json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        _publish(root, [(target, output), (state_file, model_output)])
    return dict(backend='planned-document-flow-transaction', plan=plan,
                transactions=[dict(element_id=i, report=reports[i]['report']) for i in plan['schedule']],
                steps=list(reports.values()), mutation_map=mutation_map,
                pdf_sha256=state['pdf_sha256'], model_sha256=state['model_sha256'],
                final_layout_verified=True, single_transaction_verified=True, saves=1,
                publication='verified PDF plus revision-bound sidecar; rollback on exception')
