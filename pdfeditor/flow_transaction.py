"""Plan several authored edits before executing guarded single-element writers.

This is a bounded scheduler, not a collision solver. A placement plan neither
authorizes paint changes nor relaxes an intermediate backend guard. All edits
refer to logical Unicode offsets in one input revision, once per identity.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile

from .attributed import digest
from .backend import PdfError
from .document_flow import _fonts, _order, _reseal, edit_flow, open_document
from .elements import _close
from .model import Rect
from .paragraph import plan_paragraph
from .proof_session import proof_session
from .replay import ensure_destination
from .selection import source_sha


POLICY = 'contract-neutral-expand; reverse-follows-order-v1'
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


def _positions(state, heights):
    order, _ = _order(state)
    incoming = {r['after']: r for r in state['follows']}
    result = {}
    for ident in order:
        edge = incoming.get(ident)
        first = (result[edge['before']]['last_baseline'] + edge['gap'] if edge else
                 state['elements'][ident]['binding']['layout']['baseline'])
        result[ident] = dict(baseline=first, last_baseline=first + heights[ident])
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
    # This avoids requiring a growing child to fit at its old, lower baseline.
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
        # ink, vectors, images, annotations and clips are checked by the writer;
        # line measurement is intentionally not an ink collision certificate.
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
    # Describe the bounded route BEFORE mutation. Refuse an extent-infeasible
    # prefix rather than silently trying other orders or bypassing a guard.
    prefixes = []
    for ident in schedule:
        heights[ident] = measured[ident]['last_baseline'] - measured[ident]['baseline']
        positions = _positions(state, heights)
        _check_extents(state, positions)
        prefixes.append(dict(element_id=ident, phase=('contract', 'neutral', 'expand')[phase(ident)],
                             positions=positions))
    result = dict(schema='pdfengine-flow-plan-1', source_sha256=state['pdf_sha256'],
                  model_sha256=state['model_sha256'], changes_sha256=digest(changes),
                  policy=POLICY, container=deepcopy(state['container']), follows=deepcopy(state['follows']),
                  final_positions=final, paragraph_plans=measured, extent_deltas=deltas,
                  schedule=schedule, prefixes=prefixes,
                  paint_authority='unchanged: explicit rigid ownership only; no resize',
                  collision_status='not_certified; every intermediate writer must still verify')
    result['plan_sha256'] = digest(result)
    return result


@proof_session
def plan_flow(source, model, changes):
    """Read-only final placement/route; no source mutation or paint permission."""
    state = _restore(source, model)
    return _plan(source, state, _requests(state, changes))


def _verify_positions(state, expected):
    actual = {i: dict(baseline=e['binding']['layout']['baseline'],
                      last_baseline=e['extent']['last_baseline']) for i, e in state['elements'].items()}
    if not _close(actual, expected):
        raise PdfError('writer positions differ from the precomputed batch layout')


@proof_session
def edit_flow_batch(source, model, output, model_output, changes):
    """Publish all requested edits together, only after every guarded step passes.

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
    transactions = []
    with tempfile.TemporaryDirectory(prefix='.flow-batch-', dir=output.parent) as directory:
        root = Path(directory)
        current, state = Path(source), initial
        for n, prefix in enumerate(plan['prefixes']):
            ident = prefix['element_id']
            target, sidecar = root / f'{n}.pdf', root / f'{n}.json'
            change = changes[ident]
            report = edit_flow(current, state, target, sidecar, ident, change['edits'],
                               empty_style_id=change.get('empty_style_id'))
            state = _restore(target, sidecar)
            _verify_positions(state, prefix['positions'])
            transactions.append(dict(element_id=ident, report=report))
            current = target
        _verify_positions(state, plan['final_positions'])
        if state['container'] != initial['container'] or state['follows'] != initial['follows']:
            raise PdfError('batch changed a confirmed semantic relation or layout policy')
        for ident, measured in plan['paragraph_plans'].items():
            b = state['elements'][ident]['binding']
            lines = [{k: line[k] for k in LINE_KEYS} for line in b['physical_layout']['lines']]
            if b['paragraph']['text'] != measured['text'] or not _close(lines, measured['lines']):
                raise PdfError('final authored text or shaped lines differ from the batch plan')
        if source_sha(source) != plan['source_sha256']:
            raise PdfError('batch input PDF changed before publication')
        state['previous_model_sha256'] = initial['model_sha256']
        state = _restore(current, _reseal(state))
        state_file = root / 'document.json'
        state_file.write_bytes((json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        published = []
        try:
            for temporary, destination in ((current, output), (state_file, model_output)):
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.link(temporary, destination)
                published.append(destination)
        except Exception:
            for destination in published:
                destination.unlink()
            raise
    return dict(backend='planned-document-flow-transaction', plan=plan, transactions=transactions,
                pdf_sha256=state['pdf_sha256'], model_sha256=state['model_sha256'],
                final_layout_verified=True, every_intermediate_guard_verified=True,
                publication='verified PDF plus revision-bound sidecar; rollback on exception')
