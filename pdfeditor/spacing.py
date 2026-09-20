"""Evidence for separating inline tracking from line layout adjustments.

Every horizontal gap between two painted glyphs on a physical line decomposes
exactly, in page units, into what the content stream proves:

    measured = nominal + tc + tw + tj + reposition

``nominal`` is the glyph advance from the font's /Widths or /W (glyph
metrics); ``tc`` and ``tw`` are the Tc/Tw state in force; ``tj`` is the TJ
adjustment written before the next glyph; ``reposition`` is whatever remains,
which is a Td/Tm repositioning between operators. Each term stays a separate
fact: a repositioning is never read as a word gap, and only a painted
whitespace glyph makes one. Nothing here decides what an author meant. A Tc that is identical on every line, including the
last one, is the only spacing this module proposes as inline tracking; the
excess above it is line layout evidence, and alignment is reported as a
candidate that still needs explicit confirmation.
"""
from __future__ import annotations

from statistics import median

from .content_stream import PaintChar, multiply

SCHEMA = 'pdfengine-spacing-evidence-1'
GAP_TOLERANCE = .02
EDGE_TOLERANCE = .15


def _adjustments(event):
    """TJ adjustment (text space, 1/1000 em) written immediately before each character."""
    result, pending = {}, 0.0
    for atom in event.atoms:
        if isinstance(atom, PaintChar):
            result[id(atom)] = pending
            pending = 0.0
        else:
            pending += float(atom)
    return result


def _close(values, tolerance):
    return max(values) - min(values) <= tolerance


def _line(paragraph, units, adjustments):
    glyphs = []
    for unit in units:
        state=unit.event.state
        matrix=multiply(unit.event.text_matrix,state.ctm)
        horizontal=matrix[0]*state.tz/100
        scale=state.size*horizontal
        tc,tw=state.tc*horizontal,state.tw*horizontal if state.font.unit==1 else 0.0
        glyphs.append(dict(text=unit.text, x=unit.observation['origin'][0],
                           nominal=unit.char.pdf_width / 1000 * scale,
                           tc=tc,
                           tw=tw if unit.char.code == b' ' else 0.0,
                           tj=-adjustments[id(unit.char)] / 1000 * scale,
                           state_tc=tc, state_tw=tw))
    gaps = []
    for a, b in zip(glyphs, glyphs[1:]):
        measured = b['x'] - a['x']
        reposition = measured - (a['nominal'] + a['tc'] + a['tw'] + b['tj'])
        # A word gap needs a painted whitespace glyph. A repositioning between
        # operators is recorded as its own fact; it never implies a word boundary.
        gaps.append(dict(measured=measured, nominal=a['nominal'], tc=a['tc'], tw=a['tw'], tj=b['tj'],
                         reposition=reposition, whitespace=a['text'].isspace(),
                         repositioned=abs(reposition) > GAP_TOLERANCE))
    first, last = glyphs[0], glyphs[-1]
    natural = sum(g['nominal'] for g in glyphs)
    return dict(left=first['x'], right=last['x'] + last['nominal'], natural_width=natural,
                measured_width=last['x'] + last['nominal'] - first['x'],
                tc=sorted({g['state_tc'] for g in glyphs}), tw=sorted({g['state_tw'] for g in glyphs}),
                gaps=gaps, glyph_count=len(glyphs))


def _distribution(line, tracking):
    """How the spacing above inline tracking is spread over the line's gaps.

    ``uniform_char`` and ``uniform_word`` are witnessed by spacing operators
    (Tc, Tw, TJ) in the source. Spacing realized by repositioning between
    operators is ``uniform_reposition`` even when it is perfectly even: a
    per-glyph or per-word placement proves neither tracking nor a word gap.
    """
    gaps = line['gaps']
    excess = [g['tc'] - tracking + g['tw'] + g['tj'] + g['reposition'] for g in gaps]
    if not excess:
        return 'none', 0.0
    total = sum(excess)
    if all(abs(v) <= GAP_TOLERANCE for v in excess):
        return 'none', total
    positive = [v for v in excess if abs(v) > GAP_TOLERANCE]
    if any(g['repositioned'] for g in gaps):
        carried = [v for v, g in zip(excess, gaps) if g['repositioned']]
        rest = [v for v, g in zip(excess, gaps) if not g['repositioned']]
        if all(abs(v) <= GAP_TOLERANCE for v in rest) and _close(carried, GAP_TOLERANCE) and carried[0] > GAP_TOLERANCE:
            return 'uniform_reposition', total
        return 'irregular', total
    if _close(excess, GAP_TOLERANCE) and excess[0] > GAP_TOLERANCE:
        return 'uniform_char', total
    spaces = [v for v, g in zip(excess, gaps) if g['whitespace']]
    others = [v for v, g in zip(excess, gaps) if not g['whitespace']]
    if spaces and all(abs(v) <= GAP_TOLERANCE for v in others) and _close(spaces, GAP_TOLERANCE) and spaces[0] > GAP_TOLERANCE:
        return 'uniform_word', total
    return 'irregular', total


def observe_spacing(paragraph):
    """Line-by-line spacing evidence and unconfirmed candidates for one paragraph."""
    adjustments = {}
    for event in paragraph.events:
        adjustments.update(_adjustments(event))
    lines = []
    for index in range(len(paragraph.resolved.lines)):
        units = [u for u in paragraph.units if u.line == index and u.source_index is not None and u.char is not None]
        if units:
            lines.append(dict(index=index, **_line(paragraph, units, adjustments)))
    result = dict(schema=SCHEMA, lines=lines, tracking=dict(value=None, provenance='unknown'),
                  alignment_candidates=[], contract='Evidence only; alignment and tracking need explicit confirmation.')
    if not lines:
        return result
    # Inline tracking: the same Tc on every line, the last one included. A
    # justifier never stretches the last line, so a constant value there
    # cannot be a line adjustment. Any variation stays unknown.
    tc_sets = [tuple(line['tc']) for line in lines]
    if all(len(s) == 1 for s in tc_sets) and len(set(tc_sets)) == 1:
        value = tc_sets[0][0]
        result['tracking'] = dict(value=value, provenance='inferred_consistent_tracking' if value else 'observed_source',
                                  requires_confirmation=bool(value), evidence=dict(lines=len(lines)))
        tracking = value
    else:
        result['tracking'] = dict(value=None, provenance='unknown', requires_confirmation=True,
                                  reason='character spacing differs between lines or inside a line', tc_by_line=tc_sets)
        tracking = 0.0
    for line in lines:
        line['distribution'], line['excess'] = _distribution(line, tracking)
        line['excess_tracking'] = tracking * (line['glyph_count'] - 1)
    result['alignment_candidates'] = _alignment(lines)
    return result


def _alignment(lines):
    if len(lines) < 2:
        return [dict(value='unknown', provenance='unknown', reason='a single line cannot witness its alignment')]
    full, last = lines[:-1], lines[-1]
    lefts = [line['left'] for line in lines]
    rights = [line['right'] for line in lines]
    distributed = [line for line in full if line['distribution'] in ('uniform_char', 'uniform_word')
                   and line['excess'] > GAP_TOLERANCE]
    unproven = [line for line in full if line['distribution'] in ('uniform_reposition', 'irregular')]
    edge = max(line['right'] for line in full)
    candidates = []
    if distributed and _close([line['right'] for line in distributed], EDGE_TOLERANCE) and _close(lefts, EDGE_TOLERANCE) \
            and all(line['right'] <= edge + EDGE_TOLERANCE for line in lines):
        candidates.append(dict(value='justify', provenance='alignment_candidate', requires_confirmation=True,
            evidence=dict(distributed_lines=[line['index'] for line in distributed],
                          distributions=sorted({line['distribution'] for line in distributed}),
                          unproven_lines=[line['index'] for line in unproven],
                          right_edge=edge, ragged_lines=[line['index'] for line in lines if line['right'] < edge - EDGE_TOLERANCE],
                          note='ragged lines may end before a hard break or the paragraph; they are not stretched')))
    elif unproven and _close(lefts, EDGE_TOLERANCE) and _close([line['right'] for line in full], EDGE_TOLERANCE):
        candidates.append(dict(value='unknown', provenance='unknown',
            reason='even edges are realized by repositioning between operators; no spacing operator witnesses a word gap or justification',
            unproven_lines=[line['index'] for line in unproven]))
    elif len(full) >= 2 and _close(lefts, EDGE_TOLERANCE) and _close([line['right'] for line in full], EDGE_TOLERANCE) \
            and all(abs(line['excess']) <= GAP_TOLERANCE for line in full):
        candidates.append(dict(value='unknown', provenance='unknown',
            reason='full lines share an edge without any distributed spacing; equal width may be coincidental'))
    if not candidates:
        if _close(lefts, EDGE_TOLERANCE) and not _close(rights, EDGE_TOLERANCE):
            candidates.append(dict(value='left', provenance='alignment_candidate', requires_confirmation=True,
                                   evidence=dict(left=median(lefts))))
        elif _close(rights, EDGE_TOLERANCE) and not _close(lefts, EDGE_TOLERANCE):
            candidates.append(dict(value='right', provenance='alignment_candidate', requires_confirmation=True,
                                   evidence=dict(right=median(rights))))
        elif _close([(l + r) / 2 for l, r in zip(lefts, rights)], EDGE_TOLERANCE) and not _close(lefts, EDGE_TOLERANCE):
            candidates.append(dict(value='center', provenance='alignment_candidate', requires_confirmation=True,
                                   evidence=dict(center=median((l + r) / 2 for l, r in zip(lefts, rights)))))
        else:
            candidates.append(dict(value='unknown', provenance='unknown', reason='line edges witness no single alignment'))
    return candidates
