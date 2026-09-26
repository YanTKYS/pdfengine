"""Reviewed LibreOffice source -> a reviewed page-10 area, at a boundary inside one q ... Q scope.

PR #15 admits a confirmed page-program boundary inside exactly one q ... Q
scope (q depth 1) whose state the earlier contracts already cover (an
identity or proven-compensated CTM, no clip or a proven rectangular one).
The evaluator inspected pages 1 and 10 of the unmodified source; inspection()
records what the engine reports there. No boundary under a nonidentity CTM
is a candidate: each is at q depth 2. The boundaries PR #15 newly admits are
at q depth 1 with an identity CTM, most of them under a proven rectangular
clip.

On page 10 the evaluator reviewed those candidates and fixed one below by
ID and witnesses: after the Q of the page body's last inner q ... Q (its
last text line) and right before the matching Q of the body's enclosing
q ... Q, which opens with ``0 0.1 595.2 841.8 re W* n``. The block therefore
paints inside that scope and under that inherited clip, after all of the
body and before the CC-BY-SA logo's group.

The paragraph, providers and edits are those of the single-destination
evaluation (evaluate.py). The area has the same geometry, [55,80,385,120],
on page 10, where the evaluator reviewed it empty (left of the logo, above
the heading) and inside the clip. ``--authority page-entry`` runs the same
series with the block at page entry: the control a boundary run is compared
with. Nothing is chosen from geometry or from the order of a candidate list.
Only evaluator decisions live here; PDFs, text and rasters stay in runs/.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

import pymupdf
from pypdf import PdfReader

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import (BOUNDARY, BOUNDARY_STATE, PAGE_ENTRY, PAINT, clip_state,
                                    confirm_continuation_destination, inspect_continuation_boundaries, markers, slot_id)
from pdfeditor.document_flow import _reseal
from pdfeditor.elements import _close, _paint_value
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.pdf_save import font_fingerprints
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow, plan_shared_flow
from evaluations.attributed.evaluate import font_mapping_audit
from evaluations.continuation import evaluate as single
from evaluations.continuation.resources import generated_block_bytes, inventory
from evaluations.elements.evaluate import audit_render, extraction
from evaluations.flow_transaction.evaluate import normalize, refuse
from evaluations.realpdf.evaluate import image_fingerprints
from evaluations.story_flow.evaluate import annotation_fingerprint


ROOT, BASE = single.ROOT, single.BASE
PAGE = 10
EDITED = ('4', '5', str(PAGE))
INSPECTED_PAGES = (1, 10)
IDENTITY = [1, 0, 0, 1, 0, 0]
# Reviewed empty on page 10: left of the logo group (x >= 400.2), above the
# heading (y >= 141.07), inside the scope's clip. Same geometry as page 6's.
REGION = dict(page=PAGE, bounds=[55, 80, 385, 120], x=56.8, width=326, first_baseline=92)
PROTECTED = [398, 58, 540, 111]
# Reviewed on the source with SHA-256 13665875...a5f3: page 10's body group
# `q 0 0.1 595.2 841.8 re W* n ... Q` (ordinals 1..659) and the logo group
# (660..996). The boundary lies after the Q of the body's last text line
# (ordinal 658) and before the body group's matching Q; 117 paint operators
# precede it and 15 (the logo) follow.
REVIEWED_BOUNDARY = dict(
    page=PAGE, boundary_id='boundary-1cb2d3bbed6f7b8618f3d4b1', offset=10026, ordinal=658,
    previous=dict(operator='Q', start=10025, end=10026,
                  sha256='4ae81572f06e1b88fd5ced7a1a000945432e83e1551e6f721ee9c00b8cc33260'),
    next=dict(operator='Q', start=10027, end=10028,
              sha256='4ae81572f06e1b88fd5ced7a1a000945432e83e1551e6f721ee9c00b8cc33260'),
    prefix_paint_operators=117, suffix_paint_operators=15)
REVIEWED_SCOPE = dict(
    policy='one-enclosing-graphics-state-save', depth=1,
    opening=dict(ordinal=1, operator='q', start=6, end=7,
                 sha256='8e35c2cd3bf6641bdb0e2050b76932cbb2e6034a0ddacc1d9bea82a6ba57f7cf'),
    matching=dict(ordinal=659, operator='Q', start=10027, end=10028,
                  sha256='4ae81572f06e1b88fd5ced7a1a000945432e83e1551e6f721ee9c00b8cc33260'))
REVIEWED_CLIP = dict(
    policy='inherited-rectangular-clip',
    rectangle=[0.0010867600854683331, 0.10108676008551382, 595.1989132399145, 841.8989132399145],
    clips=[dict(rule='W*', operands=[0.0, 0.1, 595.2, 841.8], ctm=IDENTITY,
                operators=[dict(operator='re', sha256='5aad4d2124810762a8ea0bb38381d408cf8b3c8a056ce2f79138d4a6c95b6292'),
                           dict(operator='W*', sha256='88964bf0b073f1dab9443b3554705c882b1b642e49fb43ae42e37a10e68d3fbb'),
                           dict(operator='n', sha256='1b16b1df538ba12dc3f97edbb85caa7050d46c148134290feba80f8236c83db9')])])
# Reviewed refusals on the same source. The first boundary lies inside the
# underline group `q 1 0 0 1 158.2 662.8 cm ... S Q` nested in the body
# group: its translation is compensable and its clip is the body's rectangle,
# so the q depth alone refuses it. The second is a logo-group candidate
# whose inherited clip rectangle does not contain the reviewed area.
REFUSED_CONFIRMATIONS = (
    dict(case='depth-2-compensable-ctm', page=PAGE, boundary_id='boundary-0089cd9553278f1b208a1b8f',
         expected='the confirmed boundary is not a safe page-level candidate of this page program: nested-graphics-state-save'),
    dict(case='logo-scope-clip-excludes-area', page=PAGE, boundary_id=None, ordinal=663,
         expected='continuation destination extends beyond its inherited rectangular clip'))
DEPENDENCIES = single.DEPENDENCIES + ['evaluations/continuation/evaluate.py']
TEXT_SHOWING = frozenset({'Tj', 'TJ', "'", '"'})


def _scope_ordinals(b):
    scope = b.get('graphics_state_scope')
    return None if scope is None else [scope['opening']['ordinal'], scope['matching']['ordinal']]


def inspection():
    """What the engine reports for pages 1 and 10 of the source, recorded as found.

    Every boundary is listed by the engine; nothing here selects one. Each
    safe candidate is recorded with its q depth, scope, clip rectangle, CTM
    and paint split; the refused ones by reason; each boundary under a
    nonidentity CTM with its reasons and the compensation and clip the
    engine proved for it.
    """
    result = {}
    for page in INSPECTED_PAGES:
        r = inspect_continuation_boundaries(single.SOURCE, page, include_refused=True)
        rectangles = []

        def clip(b):
            value = b.get('clip_constraint')
            if value is None:
                return None
            if value['rectangle'] not in rectangles:
                rectangles.append(value['rectangle'])
            return rectangles.index(value['rectangle'])
        candidates = [[c['ordinal'], c['offset'], c['boundary_id'], c['previous']['operator'], c['next']['operator'],
                       c['scope']['q_depth'], _scope_ordinals(c), clip(c), c['graphics_state']['ctm'],
                       (c.get('ctm_compensation') or {}).get('operator'),
                       c['z_order']['prefix_paint_operators'], c['z_order']['suffix_paint_operators']]
                      for c in r['candidates']]
        nonidentity = [dict(ordinal=b['ordinal'], boundary_id=b['boundary_id'], previous=b['previous']['operator'],
                            next=b['next']['operator'], q_depth=b['scope']['q_depth'], ctm=b['graphics_state']['ctm'],
                            status=b['status'], reasons=b['reasons'],
                            ctm_compensation=(b.get('ctm_compensation') or {}).get('operator'), clip=clip(b),
                            prefix_paint_operators=b['z_order']['prefix_paint_operators'],
                            suffix_paint_operators=b['z_order']['suffix_paint_operators'])
                       for b in sorted(r['candidates'] + r['refused'], key=lambda b: b['ordinal'])
                       if b['graphics_state']['ctm'] != IDENTITY]
        scopes = Counter(json.dumps(_scope_ordinals(c)) for c in r['candidates'])
        result[str(page)] = dict(
            program_sha256=r['program_sha256'], operators=r['operators'], candidates=len(r['candidates']),
            refused_boundaries=r['refused_boundaries'], refusal_reasons=r['refusal_reasons'],
            refusal_combinations=dict(sorted(Counter(', '.join(b['reasons']) for b in r['refused']).items())),
            q_depth=dict(candidates=dict(sorted(Counter(c['scope']['q_depth'] for c in r['candidates']).items())),
                         refused=dict(sorted(Counter(b['scope']['q_depth'] for b in r['refused']).items()))),
            candidate_scopes={k: v for k, v in sorted(scopes.items())},
            candidates_with_ctm_compensation=sum('ctm_compensation' in c for c in r['candidates']),
            candidates_with_prefix_and_suffix_paint=sum(bool(c['z_order']['prefix_paint_operators']
                                                             and c['z_order']['suffix_paint_operators'])
                                                        for c in r['candidates']),
            nonidentity_ctm=nonidentity, clip_rectangles=rectangles,
            candidate_columns=['ordinal', 'offset', 'boundary_id', 'previous', 'next', 'q_depth', 'scope', 'clip',
                               'ctm', 'ctm_compensation', 'prefix_paint_operators', 'suffix_paint_operators'],
            candidate_rows=candidates)
    return result


def reviewed_boundary():
    """The engine must list the reviewed boundary as a safe candidate: same witnesses, scope, clip and CTM."""
    inspected = inspect_continuation_boundaries(single.SOURCE, PAGE)
    found = [c for c in inspected['candidates'] if c['boundary_id'] == REVIEWED_BOUNDARY['boundary_id']]
    if len(found) != 1:
        raise ValueError('the reviewed page-10 boundary is not a safe candidate of the reviewed source')
    c = found[0]
    witnessed = dict(page=c['page'], boundary_id=c['boundary_id'], offset=c['offset'], ordinal=c['ordinal'],
        previous={k: c['previous'][k] for k in ('operator', 'start', 'end', 'sha256')},
        next={k: c['next'][k] for k in ('operator', 'start', 'end', 'sha256')},
        prefix_paint_operators=c['z_order']['prefix_paint_operators'],
        suffix_paint_operators=c['z_order']['suffix_paint_operators'])
    if witnessed != REVIEWED_BOUNDARY:
        raise ValueError('the reviewed page-10 boundary witnesses differ: ' + json.dumps(witnessed, sort_keys=True))
    scope = c.get('graphics_state_scope')
    if (c['scope']['q_depth'] != 1 or scope is None
            or {k: scope[k] for k in ('policy', 'depth', 'opening', 'matching')} != REVIEWED_SCOPE):
        raise ValueError('the reviewed page-10 boundary is not inside the reviewed q ... Q scope')
    clip = c.get('clip_constraint')
    if clip is None or dict(policy=clip['policy'], rectangle=clip['rectangle'],
                            clips=[{k: w[k] for k in ('rule', 'operands', 'ctm', 'operators')} for w in clip['clips']]
                            ) != REVIEWED_CLIP:
        raise ValueError('the reviewed page-10 boundary is not under the reviewed rectangular clip')
    if 'ctm_compensation' in c or c['graphics_state']['ctm'] != IDENTITY:
        raise ValueError('the reviewed page-10 boundary CTM is not the reviewed identity')
    content = ContentPage(single.SOURCE, PAGE)
    try:
        suffix_text = sum(b.operator.name in TEXT_SHOWING for b in content.boundaries
                          if b.operator.start >= REVIEWED_BOUNDARY['offset'])
    finally:
        content.close()
    # The independent extractor reads in content-stream order; the page's own
    # text then precedes the block's text only because the suffix shows none.
    if suffix_text:
        raise ValueError('the reviewed suffix shows text; the page Unicode order is not the one this evaluation expects')
    return c


def refused_confirmations():
    """Reviewed boundaries the engine must refuse for the reviewed area; confirmation writes nothing."""
    result = []
    for case in REFUSED_CONFIRMATIONS:
        ident = case['boundary_id']
        if ident is None:
            listed = inspect_continuation_boundaries(single.SOURCE, case['page'])['candidates']
            found = [c for c in listed if c['ordinal'] == case['ordinal']]
            if len(found) != 1 or 'clip_constraint' not in found[0] or not found[0].get('graphics_state_scope'):
                raise ValueError('the reviewed logo-scope candidate is not a clipped candidate inside a scope')
            ident, rectangle = found[0]['boundary_id'], found[0]['clip_constraint']['rectangle']
        else:
            listed = inspect_continuation_boundaries(single.SOURCE, case['page'], include_refused=True)['refused']
            found = [b for b in listed if b['boundary_id'] == ident]
            # Only the depth refuses it: the CTM compensation and the clip rectangle are proven.
            if (len(found) != 1 or found[0]['reasons'] != ['nested-graphics-state-save']
                    or 'ctm_compensation' not in found[0] or 'clip_constraint' not in found[0]):
                raise ValueError('the reviewed depth-2 boundary is not refused by its q depth alone')
            rectangle = found[0]['clip_constraint']['rectangle']
        try:
            confirm_continuation_destination(single.SOURCE, destination_id='refused-' + case['case'],
                paragraph_id='refused', region_id='refused', page=case['page'], bounds=REGION['bounds'],
                insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=ident)
        except PdfError as exc:
            if str(exc) != case['expected']:
                raise ValueError(f'{case["case"]} was refused for an unexpected reason: {exc}') from exc
            result.append(dict(case=case['case'], page=case['page'], boundary_id=ident, bounds=REGION['bounds'],
                               clip_rectangle=rectangle, status='refused', reason=str(exc)))
            continue
        raise ValueError('confirmation unexpectedly succeeded: ' + case['case'])
    return result


def prepare(authority):
    """evaluate.prepare(), with the reviewed area on page 10 at page entry or at the reviewed boundary."""
    if source_sha(single.SOURCE) != single.SOURCE_SHA:
        raise ValueError('reviewed source differs')
    story = single.prepare_story()
    pid = story['logical']['id']
    regions = {ident: dict(page=c['page'], bounds=c['bounds'], x=c['layout']['x'], width=c['layout']['width'],
                           first_baseline=c['layout']['baseline']) for ident, c in story['containers'].items()}
    regions['page10'] = dict(REGION)
    if authority == 'boundary':
        destination = confirm_continuation_destination(single.SOURCE, destination_id='reviewed-page10-scope-boundary',
            paragraph_id=pid, region_id='page10', page=PAGE, bounds=REGION['bounds'], insertion=BOUNDARY,
            graphics_state=BOUNDARY_STATE, boundary=REVIEWED_BOUNDARY['boundary_id'])
    else:
        destination = confirm_continuation_destination(single.SOURCE, destination_id='reviewed-page10-space',
            paragraph_id=pid, region_id='page10', page=PAGE, bounds=REGION['bounds'], insertion=PAGE_ENTRY,
            graphics_state='isolated-pdf-initial-state')
    state = confirm_shared_flow(single.SOURCE, {pid: story}, flow_id='reviewed-page10-' + authority + '-continuation',
        paragraph_order=[pid], regions=regions, region_order=list(regions), slot_regions={pid: {'A': 'A', 'B': 'B'}},
        paragraph_policies={pid: dict(min_line_height=21.6, first_line_indent=0, keep_together=False,
            break_before='auto', break_after='auto', empty=dict(kind='reserve-line', ascent=8.4, descent=2.1))},
        follows=[], protected_regions={p: [dict(role='fixed', bounds=PROTECTED)] for p in EDITED},
        continuation_destinations={destination['destination_id']: destination})
    return state, pid, destination


def _state_value(state):
    """An interpreted state as the authority records it (no object numbers; clips by rule and path)."""
    value = json.loads(json.dumps(state.report()))
    value.pop('font_xref', None)
    value['clip'] = clip_state(state.clip)
    return value


def _matching_q(ops, index):
    """Ordinal of the q that the Q at ``index`` closes, by a plain q/Q stack (not the engine's scope code)."""
    stack = []
    for i, op in enumerate(ops[:index + 1]):
        if op.name == 'q':
            stack.append(i)
        elif op.name == 'Q':
            opened = stack.pop()
            if i == index:
                return opened
    raise ValueError('operator is not a Q that closes a q')


def order(pdf, state, destination, authority, source_program):
    """prefix | block | suffix of page 10, from the saved bytes, against the reviewed witnesses.

    Page 10 holds no source slot, so the prefix and suffix are the source
    program's bytes before and after the insertion point, unchanged. At the
    boundary, the operators around the block are the reviewed Q and matching
    Q; the opening q stays where it was; the Q after the block closes that
    opening q; the binding's scope names these two operators; and the
    interpreter shows the block drawing under the boundary state, its own Q
    restoring that state inside the scope, and the matching Q restoring the
    state before the scope.
    """
    binding = state['destination_bindings'][destination['destination_id']]
    data = PdfReader(pdf).pages[PAGE - 1].get_contents().get_data()
    begin, end = markers(destination)
    insertion = REVIEWED_BOUNDARY['offset'] if authority == 'boundary' else 0
    if 'end' not in binding:
        if begin in data or data != source_program:
            raise ValueError('an unused destination changed page 10')
        return dict(block=False)
    start, stop = binding['start'], binding['end']
    prefix, block, suffix = data[:start], data[start:stop], data[stop:]
    if (start != insertion or data.count(begin) != 1 or not block.startswith(begin) or not block.endswith(end)
            or hashlib.sha256(block).hexdigest() != binding['block_sha256']):
        raise ValueError('the binding does not name its own block at the reviewed insertion point')
    if prefix != source_program[:insertion] or suffix != source_program[insertion:]:
        raise ValueError('the confirmed prefix or suffix of page 10 changed')
    paints = lambda part: sum(op.name in PAINT for op in operators(part))
    result = dict(block=True, start=start, end=stop, block_bytes=stop - start, prefix_bytes=len(prefix),
                  suffix_bytes=len(suffix), prefix_paint_operators=paints(prefix), suffix_paint_operators=paints(suffix),
                  prefix_and_suffix_are_the_source_program=True)
    ops = list(operators(data))
    inside = [i for i, op in enumerate(ops) if start <= op.start and op.end <= stop]
    content = ContentPage(pdf, PAGE)
    try:
        items = {b.operator.start: b for b in content.boundaries}
        # Every operator of the block but its final Q runs inside the block's
        # own q (a re-edited block nests further q ... Q, never a cm or a clip);
        # the final Q returns to the boundary's depth.
        body, closing = [items[ops[i].start] for i in inside[:-1]], items[ops[inside[-1]].start]
        shows = sum(ops[i].name in ('Tj', 'TJ') for i in inside)
        base = 0 if authority == 'page-entry' else 1
        ctm, clip = (IDENTITY, []) if authority == 'page-entry' else (
            destination['authority']['graphics_state']['ctm'], destination['authority']['graphics_state']['clip'])
        if (not body or ops[inside[-1]].name != 'Q'
                or any(b.q_depth <= base or list(b.state.ctm) != ctm or clip_state(b.state.clip) != clip for b in body)):
            raise ValueError('the block does not draw under the boundary CTM and clip inside its own q ... Q')
        depths = dict(block_q_depth=[min(b.q_depth for b in body), max(b.q_depth for b in body)],
                      after_block_q_depth=closing.q_depth)
        if authority == 'page-entry':
            if binding.get('scope') is not None or closing.q_depth != 0 or closing.state.clip:
                raise ValueError('the page-entry block does not return to the initial state')
            return dict(result, text_operators=shows, **depths)
        auth = destination['authority']
        scope = auth['graphics_state_scope']
        last, first = ops[inside[0] - 1], ops[inside[-1] + 1]
        for op, witness in ((last, REVIEWED_BOUNDARY['previous']), (first, REVIEWED_BOUNDARY['next'])):
            if op.name != witness['operator'] or hashlib.sha256(data[op.start:op.end]).hexdigest() != witness['sha256']:
                raise ValueError('the operators around the block are not the reviewed ones')
        opening = ops[_matching_q(ops, inside[-1] + 1)]
        where = dict(opening=dict(start=opening.start, end=opening.end), matching=dict(start=first.start, end=first.end))
        reviewed = REVIEWED_SCOPE['opening']
        # The block's own q ... Q is balanced; the Q after it closes the reviewed opening q.
        if ((opening.start, opening.end) != (reviewed['start'], reviewed['end'])
                or hashlib.sha256(data[opening.start:opening.end]).hexdigest() != reviewed['sha256']
                or binding.get('scope') != where or not opening.end <= start < stop <= first.start):
            raise ValueError('the block is not inside the reviewed q ... Q scope, or the binding names another scope')
        confirmed = auth['graphics_state']
        after = items[first.start]
        # After the block's Q: the boundary state, still inside the scope.
        if (closing.q_depth != 1 or _state_value(closing.state) != confirmed
                # After the matching Q: the state before the scope.
                or after.q_depth != 0 or _state_value(after.state) != scope['restored_state']):
            raise ValueError('the block or the scope does not restore the reviewed states')
    finally:
        content.close()
    return dict(result, text_operators=shows, scope=where, matching_q_offset_from_block_end=first.start - stop,
                **depths, after_matching_q_depth=after.q_depth)


def audit(before, after, state, initial, report, directory, original_text, authority, *, noop=False, owned=None):
    """evaluate.audit() for pages 4, 5 and 10; page 10's text in content-stream order."""
    directory.mkdir()
    visual, edited = {}, set(map(int, EDITED))
    for page in range(1, state['page_count'] + 1):
        if page in edited or noop:
            r = next((r for r in state['regions'].values() if r['page'] == page), None)
            v = audit_render(before, after, page, directory / f'page-{page}', r['bounds'] if r else (0, 0, 0, 0),
                             noop=noop or page not in edited, edited_pages=edited)
            visual[str(page)] = dict(mupdf_equal=v['mupdf_page_pixels'], poppler_changed_pixels=v['poppler_diff']['all_changed_pixels'],
                                     poppler_outside_changed_pixels=v['poppler_diff_with_1pt_margin']['outside_changed_pixels'])
    for step in report['steps']:
        font_mapping_audit(after, step['report'])
    extracted = extraction(after, directory, 'saved')['pages']
    for page in range(1, state['page_count'] + 1):
        text = normalize(original_text[page - 1])
        initial_slots = [s for s in initial['slots'].values() if initial['regions'][s['region_id']]['page'] == page]
        current = [s for s in state['slots'].values() if state['regions'][s['region_id']]['page'] == page]
        if initial_slots:
            old = ''.join(normalize(s['binding']['paragraph']['text']) for s in initial_slots)
            if text.count(old) != 1:
                raise ValueError('source Unicode is not uniquely delimited')
            text = text.replace(old, ''.join(normalize(s['binding']['paragraph']['text']) for s in current))
        elif current:
            generated = ''.join(normalize(s['binding']['paragraph']['text']) for s in current)
            # At the boundary the block paints after the prefix and the reviewed
            # suffix shows no text; at page entry it paints first.
            text = text + generated if authority == 'boundary' else generated + text
        if normalize(extracted[page - 1]) != text:
            raise ValueError(f'complete-page Unicode differs on {page}')
    old, new = PdfReader(before), PdfReader(after)
    with pymupdf.open(before) as a, pymupdf.open(after) as b:
        for i in range(len(a)):
            if i + 1 not in edited and a[i].get_pixmap(dpi=144).samples != b[i].get_pixmap(dpi=144).samples:
                raise ValueError('fixed page changed')
            pa, pb = interpreted_paints(before, i + 1), interpreted_paints(after, i + 1)
            paint = lambda p: [_paint_value(e) for e in p['events'] if e['kind'] not in ('fill-text', 'stroke-text', 'ignore-text')]
            if pa['errors'] or pb['errors'] or not _close(paint(pa), paint(pb)):
                raise ValueError('fixed paint changed')
            if image_fingerprints(a[i]) != image_fingerprints(b[i]) or annotation_fingerprint(old, i + 1) != annotation_fingerprint(new, i + 1):
                raise ValueError('image/annotation changed')
            aliases = {g['font_resource'][1:] for step in report['steps'] if step['report']['selection']['page'] == i + 1
                       for g in step['report']['glyph_plan']}
            retargeted = {x[1:] for x in report['generated_font_outcome'].get(str(i + 1), {}) if x in (owned or {}).get(str(i + 1), {})}
            if ([f for f in font_fingerprints(b, i) if f[0][3] not in aliases]
                    != [f for f in font_fingerprints(a, i) if f[0][3] not in retargeted]):
                raise ValueError('original font resource changed')
    return dict(renderers=visual, all_page_unicode=True, all_nontext_paint_images_annotations_original_fonts=True, cid_gid_w=True,
                allocation={sid: dict(range=s['range'], occupancy=s['occupancy']) for sid, s in state['slots'].items()})


def resources(pdf, state, destination, source_aliases):
    """evaluate.resources() for pages 4, 5 and 10."""
    records = state.get('generated_fonts', {})
    inv = inventory(pdf, records=records, source=single.SOURCE)
    for page, entries in inv['pages'].items():
        if any(e['kind'] == 'unknown' for e in entries):
            raise ValueError(f'unrecorded font resource on page {page}')
        if {e['alias'] for e in entries if e['kind'] == 'original'} != source_aliases[page]:
            raise ValueError(f'source font resource removed or changed on page {page}')
    return dict(pdf_bytes=Path(pdf).stat().st_size,
        page_font_resources={p: len(inv['pages'][p]) for p in EDITED},
        generated_fonts_by_page={p: len(records.get(p, {})) for p in EDITED},
        type0_fonts=inv['type0_fonts'], generated_fonts=inv['owned_font_roots'],
        generated_font_graph_objects=inv['owned_font_graph_objects'],
        generated_block_bytes=generated_block_bytes(pdf, PAGE, destination))


def tampered_openings(directory, pdf, sidecar, destination, position):
    """The binding must name this revision's opening q and matching Q; equal bytes elsewhere are refused.

    Each forged sidecar is resealed, so only the scope location differs from
    the saved one; the resealed unchanged sidecar is the control.
    """
    state = json.loads(Path(sidecar).read_text(encoding='utf-8'))
    ident = destination['destination_id']
    control = directory / 'tampered-control.json'
    single.write(control, _reseal(state))
    if open_shared_flow(pdf, control)['status'] != 'restored':
        raise ValueError('the resealed unchanged sidecar was not restored')
    data = PdfReader(pdf).pages[PAGE - 1].get_contents().get_data()
    ops = list(operators(data))
    # Another Q with the same bytes (the reviewed inner Q before the block) and
    # another q (the one that opens the body's last inner group).
    inner_q = next(op for op in reversed(ops) if op.name == 'Q' and op.end <= position['start'])
    inner_open = ops[_matching_q(ops, ops.index(inner_q))]
    cases = dict(matching_q_moved=('matching', inner_q), opening_q_moved=('opening', inner_open))
    result = []
    for name, (key, op) in cases.items():
        tampered = deepcopy(state)
        tampered['destination_bindings'][ident]['scope'][key] = dict(start=op.start, end=op.end)
        path = directory / f'tampered-{name}.json'
        single.write(path, _reseal(tampered))
        opened = open_shared_flow(pdf, path)
        if opened['status'] != 'needs_confirmation' or 'not in its confirmed q ... Q scope' not in opened['reason']:
            raise ValueError('a binding naming another ' + ('Q' if key == 'matching' else 'q') + ' was not refused by the scope check')
        result.append(dict(case=name, status=opened['status'], reason=opened['reason'],
                           moved=key, to=dict(start=op.start, end=op.end)))
    return result


def compare(directory, page_entry, wait=4 * 3600):
    """The same edits at page entry on page 10: same planned glyphs, same saved glyphs, same pixels.

    The page-entry run may still be running; its summary marks completion.
    """
    other = BASE / 'runs' / page_entry
    deadline = time.monotonic() + wait
    while not (other / 'summary.json').exists():
        if time.monotonic() > deadline:
            raise ValueError('the page-entry run did not complete')
        time.sleep(60)
    summary = json.loads((other / 'summary.json').read_text(encoding='utf-8'))
    if summary.get('authority') != 'page-entry' or summary.get('status') != 'passed':
        raise ValueError('the comparison run is not a passed page-entry run of this evaluation')
    result = {}
    glyphs = lambda r: [[{k: g[k] for k in single.GLYPH_FIELDS} for g in step['report']['glyph_plan']] for step in r['steps']]
    for name in single.STAGES:
        mine = json.loads((directory / f'{name}-report.json').read_text(encoding='utf-8'))
        theirs = json.loads((other / f'{name}-report.json').read_text(encoding='utf-8'))
        images = {}
        for path in sorted((directory / f'{name}-audit').glob('page-*/after.png')):
            twin = other / path.relative_to(directory)
            images[path.parent.name] = twin.exists() and twin.read_bytes() == path.read_bytes()
        # Saved glyphs of page 10 in MuPDF's text trace: character, glyph, origin and box.
        traces, pixels = [], []
        for pdf in (directory / f'{name}.pdf', other / f'{name}.pdf'):
            with pymupdf.open(pdf) as document:
                page = document[PAGE - 1]
                traces.append([(c[0], c[1], tuple(round(v, 6) for v in c[2]), tuple(round(v, 6) for v in c[3]))
                               for span in page.get_texttrace() for c in span['chars']
                               if pymupdf.Rect(REGION['bounds']).contains(pymupdf.Point(c[2]))])
                pixels.append(page.get_pixmap(dpi=144, alpha=False).samples)
        if (glyphs(mine) != glyphs(theirs) or not images or not all(images.values())
                or traces[0] != traces[1] or pixels[0] != pixels[1]):
            raise ValueError(f'{name}: the boundary and page-entry continuations differ in glyphs or pixels')
        result[name] = dict(planned_glyph_fields_equal=True, planned_glyphs=sum(map(len, glyphs(mine))),
                            saved_page10_glyphs_equal=True, saved_page10_glyphs=len(traces[0]),
                            mupdf_page10_pixels_equal=True, poppler_images_equal=sorted(images))
    return dict(page_entry_run=page_entry, page_entry_engine_digest=summary['environment']['engine_digest'], stages=result)


def run(directory, authority, page_entry=None):
    engine = {p.name: source_sha(p) for p in sorted((ROOT / 'pdfeditor').glob('*.py'))}
    environment = single.tools()
    inspected = inspection()
    chosen = reviewed_boundary()
    refusals = refused_confirmations()
    state, pid, destination = prepare(authority)
    initial = deepcopy(state)
    providers = single.provider_evidence(state['paragraphs'][pid]['style_registry'])
    reviewed, provider_evidence_source = single.reviewed_providers()
    if providers != reviewed:
        raise ValueError('reflow providers differ from the reviewed story_styles providers: ' + json.dumps(providers, sort_keys=True))
    auth = destination['authority']
    if authority == 'boundary' and (
            auth['boundary_id'] != chosen['boundary_id'] or auth['graphics_state_scope'] != chosen['graphics_state_scope']
            or auth['clip_constraint'] != chosen['clip_constraint'] or 'ctm_compensation' in auth
            or auth['isolation'] != 'q-BT-ET-Q' or auth['initial_clip'] != 'inherited-rectangular-clip'):
        raise ValueError('the confirmed authority is not the reviewed candidate')
    single.write(directory / 'initial.json', state)
    replay = single.source_replay(directory, state)
    original_text = extraction(single.SOURCE, directory, 'original')['pages']
    logical = deepcopy(state['paragraphs'][pid]['logical'])
    sid = slot_id(destination)
    source_program = PdfReader(single.SOURCE).pages[PAGE - 1].get_contents().get_data()
    extra = '確認した空き領域へ同じ文章の続きを配置し、再編集と保存後の文字位置を確認します。' * 2
    source_aliases = {p: {e['alias'] for e in v} for p, v in inventory(single.SOURCE, source=single.SOURCE)['pages'].items()}
    baseline = resources(single.SOURCE, state, destination, source_aliases)
    try:
        source_nesting = single.operator_nesting(single.SOURCE, EDITED)
    except ValueError as exc:
        raise ValueError('reviewed source pages already violate operator nesting; pdfengine output cannot be attributed') from exc
    source_header = single.pdf_header(single.SOURCE)
    stages, pdf, previous, creation, previous_sizes, side = [], single.SOURCE, None, None, baseline, None
    for name in single.STAGES:
        noop = name.startswith('noop')
        edits = ({pid: dict(edits=[dict(start=0, end=len(state['paragraphs'][pid]['logical']['text']), text='確認。', style_id='body')])}
                 if name == 'shorten' else {} if noop else
                 {pid: dict(edits=[dict(start=0, end=len(state['paragraphs'][pid]['logical']['text']), runs=[
                     *[dict(text=logical['text'][s['start']:s['end']], style_id=s['style_id']) for s in logical['style_spans']],
                     dict(text=extra.replace('再編集', '追加編集') if name == 'second' else extra, style_id='body')])])})
        preview = plan_shared_flow(pdf, state, edits)
        single.write(directory / f'{name}-plan.json', preview)
        out, side = directory / f'{name}.pdf', directory / f'{name}.json'
        report = edit_shared_flow(pdf, state, out, side, edits)
        single.write(directory / f'{name}-report.json', report)
        if report['plan'] != preview:
            raise ValueError('executed plan differs')
        opened = open_shared_flow(out, side)
        if opened['status'] != 'restored':
            raise ValueError(opened['reason'])
        updated = opened['state']
        if sid not in updated['slots'] or len(updated['slots']) != 3:
            raise ValueError('generated identity changed')
        if (updated['slots'][sid]['occupancy'] is None) != (name == 'shorten'):
            raise ValueError('continuation did not activate/dormant')
        if updated['contract_sha256'] != initial['contract_sha256']:
            raise ValueError('confirmation changed')
        if updated['continuation_destinations'][destination['destination_id']] != json.loads(json.dumps(destination)):
            raise ValueError('the destination authority changed')
        if set(report['plan']['new_slots']) != ({sid} if name == 'overflow' else set()):
            raise ValueError('generated slot was not reused')
        creation = creation or updated['slots'][sid]['creation_binding']
        mutation = creation['mutation']
        at = REVIEWED_BOUNDARY['offset'] if authority == 'boundary' else 0
        if (updated['slots'][sid]['creation_binding'] != creation or mutation['start'] != at
                or mutation['end'] != mutation['start'] or 'insertion_order' in mutation):
            raise ValueError('generated creation provenance changed or is not at the reviewed insertion point')
        if name == 'shorten' and updated['slots'][sid]['binding']['paragraph']['text']:
            raise ValueError('dormant slot still paints text')
        position = order(out, updated, destination, authority, source_program)
        checks = {}
        if noop:
            for ident, s in state['slots'].items():
                if s['range'] != updated['slots'][ident]['range'] or s['occupancy'] != updated['slots'][ident]['occupancy']:
                    raise ValueError('no-op allocation changed')
            if updated['paragraphs'] != state['paragraphs'] or updated['continuation_destinations'] != state['continuation_destinations']:
                raise ValueError('no-op changed paragraph, style or destination records')
            if any(single.stable_slot(s) != single.stable_slot(updated['slots'][i]) for i, s in state['slots'].items()):
                raise ValueError('no-op changed slot identity, allocation, layout or inline style')
            glyphs = lambda r: [[{k: g[k] for k in single.GLYPH_FIELDS} for g in step['report']['glyph_plan']] for step in r['steps']]
            if glyphs(previous) != glyphs(report):
                raise ValueError('no-op changed planned glyphs')
            checks = dict(paragraph_style_destination_records_equal=True, slot_identity_allocation_layout_style_equal=True,
                          planned_glyph_fields_equal=list(single.GLYPH_FIELDS), planned_glyphs=sum(map(len, glyphs(report))))
        proof = audit(pdf, out, updated, initial, report, directory / (name + '-audit'), original_text, authority,
                      noop=noop, owned=state.get('generated_fonts', {}))
        nesting = single.operator_nesting(out, EDITED)
        if single.pdf_header(out) != source_header:
            raise ValueError('saving changed the PDF version')
        sizes = resources(out, updated, destination, source_aliases)
        outcome = report['generated_font_outcome']
        if noop:
            if any(sizes[k] != previous_sizes[k] for k in single.RESOURCE_COUNTS):
                raise ValueError('no-op changed font/resource counts')
            if any(set(v.values()) != {'reused'} for v in outcome.values()):
                raise ValueError('no-op wrote a new font object')
            if updated['generated_fonts'] != state['generated_fonts']:
                raise ValueError('no-op changed generated font records')
        stages.append(dict(stage=name, status='passed', restored=True, slot_id=sid, saves=report['saves'],
            new_slots=sorted(report['plan']['new_slots']),
            generated_lines=len(updated['slots'][sid]['binding']['physical_layout']['lines']), insertion=position,
            **checks, **proof, operator_nesting=nesting, pdf_header=single.pdf_header(out),
            resources=dict(sizes, font_outcome=outcome)))
        single.write(directory / 'stages.json', stages)
        print(name, 'passed', flush=True)
        pdf, state, previous, previous_sizes = out, updated, report, sizes
    refused = refuse(directory, 'capacity', lambda o, j: edit_shared_flow(pdf, state, o, j,
        {pid: dict(edits=[dict(start=0, end=0, text=extra * 2, style_id='body')])}))
    if refused['reason'] != single.CAPACITY_REASON:
        raise ValueError('capacity negative control was refused for an unexpected reason: ' + refused['reason'])
    tampered = tampered_openings(directory, pdf, side, destination, stages[-1]['insertion']) if authority == 'boundary' else []
    comparison = compare(directory, page_entry) if page_entry else None
    if engine != {p.name: source_sha(p) for p in sorted((ROOT / 'pdfeditor').glob('*.py'))}:
        raise ValueError('engine changed during evaluation')
    digest = hashlib.sha256(json.dumps(engine, sort_keys=True).encode()).hexdigest()
    if comparison and comparison['page_entry_engine_digest'] != digest:
        raise ValueError('the page-entry run used another engine')
    return dict(schema='pdfengine-scope-destination-evaluation-1', status='passed', authority=authority,
        source_url=single.URL, source_sha256=single.SOURCE_SHA, inspection=inspected,
        reviewed_boundary=dict(REVIEWED_BOUNDARY, graphics_state_scope=REVIEWED_SCOPE, clip_constraint=REVIEWED_CLIP,
                               ctm=IDENTITY, region=REGION),
        refused_confirmations=refusals, source_replay=replay, source_resources=baseline,
        source_operator_nesting=source_nesting, source_pdf_header=source_header, stages=stages,
        negative_controls=[refused, *tampered], destination=destination, page_entry_comparison=comparison,
        scope=('one external LibreOffice paragraph; the reviewed page-10 area '
               + ('at one reviewed boundary inside one q ... Q scope under an inherited rectangular clip'
                  if authority == 'boundary' else 'at page entry (control)')),
        providers=providers, provider_evidence_source=provider_evidence_source,
        environment=dict(engine_digest=digest, engine_sha256=engine,
            runner_sha256=source_sha(Path(__file__)), evaluation_dependencies_sha256={d: source_sha(ROOT / d) for d in DEPENDENCIES},
            python=sys.version.split()[0], platform=platform.platform(), pymupdf=pymupdf.VersionBind, **environment))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--authority', choices=('boundary', 'page-entry'), default='boundary')
    parser.add_argument('--page-entry-run', help='the runs/ directory of a page-entry run of this evaluation and engine')
    args = parser.parse_args()
    if args.page_entry_run and args.authority != 'boundary':
        parser.error('only a boundary run is compared with a page-entry run')
    directory = BASE / 'runs' / (('scope-' if args.authority == 'boundary' else 'scope-entry-') + args.run_name)
    directory.mkdir(parents=True)
    single.write(directory / 'summary.json', run(directory, args.authority, args.page_entry_run))
