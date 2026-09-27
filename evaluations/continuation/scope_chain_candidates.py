"""Reviewed, unchanged LibreOffice depth-2 witnesses for the scope-chain evaluator.

The selected boundary follows a completed underline stroke. Its translated
inner scope still owns a blue stroke and 0.7 pt line width, while restoring
the inner scope returns to black and 0.1 pt; the outer restoration removes
the inherited clip. Selection is independent of candidate-list order.
"""
import json
from pathlib import Path

from pypdf import PdfReader

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import operators
from pdfeditor.continuation import (BOUNDARY, BOUNDARY_STATE,
    confirm_continuation_destination, inspect_continuation_boundaries)
from pdfeditor.selection import source_sha
from evaluations.continuation import evaluate as single
from evaluations.flow_transaction.evaluate import normalize


PAGE = 10
IDENTITY = [1, 0, 0, 1, 0, 0]
REGION = dict(page=PAGE, bounds=[55, 80, 385, 120], x=56.8, width=326, first_baseline=92)
Q_SHA = '4ae81572f06e1b88fd5ced7a1a000945432e83e1551e6f721ee9c00b8cc33260'
SAVE_SHA = '8e35c2cd3bf6641bdb0e2050b76932cbb2e6034a0ddacc1d9bea82a6ba57f7cf'
REVIEWED_BOUNDARY = dict(
    page=PAGE, boundary_id='boundary-54efad3408573ff6b8cd7f08', offset=701, ordinal=62,
    previous=dict(ordinal=62, operator='S', start=700, end=701,
                  sha256='8de0b3c47f112c59745f717a626932264c422a7563954872e237b223af4ad643'),
    next=dict(ordinal=63, operator='Q', start=702, end=703, sha256=Q_SHA))
REVIEWED_SCOPE = dict(
    policy='two-nested-graphics-state-saves', depth=2,
    outer=dict(opening=dict(ordinal=1, operator='q', start=6, end=7, sha256=SAVE_SHA),
               matching=dict(ordinal=659, operator='Q', start=10027, end=10028, sha256=Q_SHA)),
    inner=dict(opening=dict(ordinal=56, operator='q', start=632, end=633, sha256=SAVE_SHA),
               matching=dict(ordinal=63, operator='Q', start=702, end=703, sha256=Q_SHA)))
REVIEWED_CTM = [1.0, 0.0, 0.0, 1.0, 158.1999969482422, 662.7999877929688]
REVIEWED_COMPENSATION = '1 0 0 1 -158.199996948 -662.799987793 cm'
REVIEWED_CLIP = [0.0010867600854683331, 0.10108676008551382,
                 595.1989132399145, 841.8989132399145]
REVIEWED_PAINT_COUNTS = [7, 125]
SELECTION_REASON = (
    'After the first completed underline stroke S, before its inner matching Q: '
    'the path is complete while the translated inner scope, blue stroke and '
    '0.7 pt line width remain live. Seven original paints precede it and 125 '
    'follow it. The inner restoration returns to black and 0.1 pt with the '
    'outer clip retained; the outer restoration removes that clip.')
PENDING_BOUNDARY = dict(boundary_id='boundary-5af5df74284e26764464f7a6',
                        ordinal=60, offset=686, previous='m', next='l')
PREFIX = 'LibreOfficeを入手するLibreOffice4.0はhttp://ja.libreoffice.org/download/'
PR16_SUMMARY = single.BASE / 'scope-destination-summary.json'


def _scope_witnesses(candidate):
    scope = candidate['graphics_state_scope']
    return dict(policy=scope['policy'], depth=scope['depth'],
                **{level: {key: scope[level][key] for key in ('opening', 'matching')}
                   for level in ('outer', 'inner')})


def inspect_and_select(directory):
    """Inspect fresh candidates, compare PR #16 structure, then pin reviewed witnesses.

    Full nonidentity candidate/refusal records stay in the run directory;
    the returned inspection is the compact, publishable evidence.
    """
    directory = Path(directory)
    if source_sha(single.SOURCE) != single.SOURCE_SHA:
        raise ValueError('the reviewed LibreOffice source differs')
    prior = json.loads(PR16_SUMMARY.read_text(encoding='utf-8'))['inspection']
    raw, published, chosen = {}, {}, None
    for page in (1, 10):
        found = inspect_continuation_boundaries(single.SOURCE, page, include_refused=True)
        before = prior[str(page)]
        if found['program_sha256'] != before['program_sha256']:
            raise ValueError('the program differs from the PR #16 inspected source')
        previous = {b['ordinal']: b for b in before['nonidentity_ctm']}
        current = [b for b in sorted(found['candidates'] + found['refused'], key=lambda b: b['ordinal'])
                   if b['graphics_state']['ctm'] != IDENTITY]
        if {b['ordinal'] for b in current} != set(previous):
            raise ValueError('the nonidentity boundary ordinals differ from PR #16')
        rows = []
        for b in current:
            old = previous[b['ordinal']]
            structural = dict(previous=b['previous']['operator'], next=b['next']['operator'],
                q_depth=b['scope']['q_depth'], ctm=b['graphics_state']['ctm'],
                ctm_compensation=b['ctm_compensation']['operator'],
                prefix_paint_operators=b['z_order']['prefix_paint_operators'],
                suffix_paint_operators=b['z_order']['suffix_paint_operators'])
            if (any(old[k] != v for k, v in structural.items())
                    or b['clip_constraint']['rectangle'] != before['clip_rectangles'][old['clip']]
                    or b['reasons'] != [r for r in old['reasons'] if r != 'nested-graphics-state-save']
                    or 'nested-graphics-state-save' not in old['reasons']):
                raise ValueError('a nonidentity boundary does not structurally correspond to PR #16')
            scope = b['graphics_state_scope']
            rows.append([b['ordinal'], b['offset'], b['boundary_id'], old['boundary_id'],
                b['previous']['operator'], b['next']['operator'], b['status'], b['reasons'],
                b['graphics_state']['ctm'], b['ctm_compensation']['operator'],
                [scope[level][key]['ordinal'] for level in ('outer', 'inner') for key in ('opening', 'matching')],
                b['clip_constraint']['rectangle'], b['z_order']['prefix_paint_operators'],
                b['z_order']['suffix_paint_operators']])
            if page == PAGE and b['boundary_id'] == REVIEWED_BOUNDARY['boundary_id']:
                chosen = b
        safe = sum(b['status'] == 'safe' for b in current)
        pending = sum(b['reasons'] == ['pending-path'] for b in current)
        if (safe, pending) != ((2, 0) if page == 1 else (12, 6)):
            raise ValueError('unexpected counts of safe and pending-path nonidentity boundaries')
        raw[str(page)] = dict(program_sha256=found['program_sha256'], operators=found['operators'],
                             nonidentity=current)
        published[str(page)] = dict(program_sha256=found['program_sha256'], operators=found['operators'],
            q_depth=2, safe_compensated_clipped=safe, pending_path_refused=pending,
            all_pr16_structural_correspondence=True,
            columns=['ordinal', 'offset', 'boundary_id', 'pr16_boundary_id', 'previous', 'next',
                     'status', 'reasons', 'ctm', 'compensation', 'outer_q_Q_inner_q_Q',
                     'clip_rectangle', 'prefix_paints', 'suffix_paints'], rows=rows)
    if (chosen is None or {k: chosen[k] for k in REVIEWED_BOUNDARY} != REVIEWED_BOUNDARY
            or _scope_witnesses(chosen) != REVIEWED_SCOPE
            or chosen['status'] != 'safe' or chosen['reasons'] or chosen['scope']['pending_path']
            or chosen['scope']['q_depth'] != 2 or chosen['graphics_state']['ctm'] != REVIEWED_CTM
            or chosen['ctm_compensation']['operator'] != REVIEWED_COMPENSATION
            or chosen['clip_constraint']['rectangle'] != REVIEWED_CLIP
            or [chosen['z_order'][k] for k in ('prefix_paint_operators', 'suffix_paint_operators')]
               != REVIEWED_PAINT_COUNTS):
        raise ValueError('the reviewed after-stroke depth-2 candidate witnesses differ')
    single.write(directory / 'candidate-inspection.json', raw)
    single.write(directory / 'reviewed-candidate.json', dict(candidate=chosen, selection_reason=SELECTION_REASON))
    return chosen, dict(pages=published, selection_reason=SELECTION_REASON,
                        comparison_summary='evaluations/continuation/scope-destination-summary.json',
                        comparison_summary_sha256=source_sha(PR16_SUMMARY))


def pending_refusal(directory):
    """Use a just-inspected PR #16 pending-path boundary; confirmation writes nothing."""
    raw = json.loads((Path(directory) / 'candidate-inspection.json').read_text(encoding='utf-8'))
    found = [b for b in raw['10']['nonidentity'] if b['boundary_id'] == PENDING_BOUNDARY['boundary_id']]
    if len(found) != 1:
        raise ValueError('the reviewed pending-path boundary was not inspected')
    b = found[0]
    witnesses = dict(boundary_id=b['boundary_id'], ordinal=b['ordinal'], offset=b['offset'],
                     previous=b['previous']['operator'], next=b['next']['operator'])
    if (witnesses != PENDING_BOUNDARY or b['reasons'] != ['pending-path']
            or b['scope']['q_depth'] != 2 or not b['scope']['pending_path']
            or 'ctm_compensation' not in b or 'clip_constraint' not in b):
        raise ValueError('the negative control is not refused only by its pending path')
    expected = 'the confirmed boundary is not a safe page-level candidate of this page program: pending-path'
    try:
        confirm_continuation_destination(single.SOURCE, destination_id='refused-pending-path',
            paragraph_id='refused', region_id='refused', page=PAGE, bounds=REGION['bounds'],
            insertion=BOUNDARY, graphics_state=BOUNDARY_STATE, boundary=b['boundary_id'])
    except PdfError as exc:
        if str(exc) != expected:
            raise ValueError('the pending-path boundary was refused for another reason') from exc
        return dict(case='depth-2-pending-path', page=PAGE, **witnesses,
                    status='safely_refused', reason=str(exc), q_depth=2,
                    ctm_compensation_proven=True, rectangular_clip_proven=True)
    raise ValueError('the pending-path boundary was accepted')


def unicode_parts(directory=None):
    """Return normalized source text before/after the fixed insertion, without editing the PDF.

    The operand visitor must align with every source operator. Text visitors
    flush the last prefix run at ordinal 54, before the selected stroke at 62;
    the first suffix fragment is at ordinal 70. This avoids the depth-1
    evaluator's assumption that all original text precedes the insertion.
    """
    page = PdfReader(single.SOURCE).pages[PAGE - 1]
    ordinal, observed, fragments = -1, [], []

    def before(op, args, cm, tm):
        nonlocal ordinal
        ordinal += 1
        observed.append(op.decode())

    def visitor(text, cm, tm, font, size):
        if text:
            fragments.append(dict(ordinal=ordinal, text=text))

    full = page.extract_text(visitor_operand_before=before, visitor_text=visitor)
    prefix = [f for f in fragments if f['ordinal'] <= REVIEWED_BOUNDARY['ordinal']]
    suffix = [f for f in fragments if f['ordinal'] > REVIEWED_BOUNDARY['ordinal']]
    parts = tuple(normalize(''.join(f['text'] for f in group)) for group in (prefix, suffix))
    if (observed != [op.name for op in operators(page.get_contents().get_data())]
            or full != ''.join(f['text'] for f in fragments)
            or ''.join(parts) != normalize(full) or parts[0] != PREFIX or len(parts[1]) != 919
            or prefix[-1]['ordinal'] != 54 or suffix[0]['ordinal'] != 70):
        raise ValueError('source Unicode does not split at the reviewed underline boundary')
    if directory is not None:
        single.write(Path(directory) / 'source-unicode-parts.json', dict(
            operator_visitors_match_source=True, operators=len(observed),
            visitor_text_equals_full_extraction=True, prefix=parts[0], suffix=parts[1],
            last_prefix_text_ordinal=54, first_suffix_text_ordinal=70, fragments=fragments))
    return parts
