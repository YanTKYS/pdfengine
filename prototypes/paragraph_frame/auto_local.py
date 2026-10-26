"""Local layout: keep what an edit does not change where it was drawn.

The paragraph is not wrapped again from its first line. Lines before the
first edited line are the source lines, unchanged glyphs on the edited line
before the edit keep their origins, and the text after the edit keeps each
glyph's observed pen step. Line ends prefer the source line breaks:

* a line keeps its source break while its text fits and the line does not
  become a hole (a ragged line shorter than the paragraph's own ragged lines,
  or a justified line needing more than a quarter em per space to stay
  flush); otherwise the line takes text from the next one (UAX14 breaks);
* a line too long for the frame wraps at its last legal break;
* an author's break (a short line whose next line's first unit would have
  fit) is never crossed;
* once a line ends at a source break after the last edit, the following
  source lines are kept as drawn (moved vertically only if the line count
  changed).

Each line records whether it is kept as drawn. ``LocalUnavailable`` means a
case this layout does not cover; the caller uses the paragraph layout.
"""
from uniseg.linebreak import line_break_boundaries
from .auto_layout import fit, _squeezed
from .engine import Refused
from .auto_restore import against, full, align as align_recorded

STRETCH = .25   # em of extra advance per space a justified line may take
CJK_GAP = .05   # em of extra advance per glyph gap of a justified CJK line
RAGGED = .5     # em a ragged line may end short beyond the paragraph's own raggedness


class LocalUnavailable(Exception):
    pass


def local_layout(tokens, spec, record=True):
    """Local layout of ``tokens`` against the paragraph as drawn, or, when a
    stored record holds the paragraph's first approved layout in full, against
    that layout (auto_restore.against): what differs from the recorded text
    is laid out as one edit of the recorded paragraph, so the lines do not
    depend on the edits, undos and their order that led to the text. A line
    is kept as drawn only where it is drawn so now; a line set where the
    record had it is redrawn there, its line-end join undrawn as recorded.
    ``record=False`` lays out against the drawing (the caller's fallback)."""
    original = spec.get('recorded')
    if not record or not original or not full(original) or any(t.get('fixed') for t in tokens):
        return _layout(tokens, spec)
    rows = spec.get('source_rows')
    if not rows or not tokens:
        raise LocalUnavailable('no source lines')
    virtual, recorded_rows, hyphens = against(tokens, original, rows[0]['baseline'], spec.get('aligned', False))
    lines = _layout(virtual, dict(spec, source_rows=recorded_rows, hyphens=hyphens, recorded=None))
    drawn = {tuple(r['ids']) for r in rows}
    for line in lines:
        as_recorded = line.get('kept')
        # The recorded paragraph changed lines here without a glyph.
        joined = bool(line['tokens']) and line['tokens'][-1].get('source_id') is None and line['tokens'][-1].get('mode') == 'implicit-line-join'
        placed = []
        for p in line['tokens']:
            t = tokens[p.pop('token')]
            placed.append(dict(p, source_id=t.get('source_id'), origin=t.get('origin'), mode=t.get('mode')))
        line['tokens'] = placed
        ids = tuple(p['source_id'] for p in placed if p.get('mode') != 'implicit-line-join')
        line['kept'] = bool(as_recorded and ids in drawn and all(
            p.get('origin') is not None and abs(p['x'] - p['origin'][0]) < 1e-9 and abs(p['y'] - p['origin'][1]) < 1e-9
            for p in placed if p.get('mode') != 'implicit-line-join'))
        if as_recorded and not line['kept']:
            # Set where the recorded paragraph had it: its line-end join had no glyph.
            if joined and line['end'] < len(tokens):
                placed[-1]['undrawn'] = True
            line['restored'] = True
    return lines


def _layout(tokens, spec):
    rows = spec.get('source_rows')
    if not rows or not tokens:
        raise LocalUnavailable('no source lines')
    if spec.get('align') == 'justify':
        # Word spaces the source stretched to justify a line may give that
        # stretch back before the line wraps (natural width: glyph, Tc, Tw).
        tokens = [dict(t) for t in tokens]
        for t in tokens:
            if t['text'] == ' ' and t.get('source_id') is not None:
                natural = t['nominal_advance'] + (t['tc'] + t['tw']) * t['tz'] / 100
                if t['advance'] > natural + .01:
                    t['squeezable'] = t['advance'] - natural
    n = len(rows)
    # A line-final hyphen may be soft or hard: its break cannot move, so it
    # is kept like an author's break.
    hyphens = set(spec.get('hyphens', ()))
    rows = [dict(r, author=r['hard'], hard=r['hard'] or (k + 1 < len(rows) and r['ids'][-1] in hyphens)) for k, r in enumerate(rows)]
    line_of = {i: k for k, r in enumerate(rows) for i in r['ids']}
    ordered = [i for r in rows for i in r['ids']]
    order = {i: m for m, i in enumerate(ordered)}
    # Home line of each token (new text belongs to the line of the text before
    # it), and the edit points: new text, or retained text after deleted text.
    home, changed, affected = [], [], []
    previous, current = -1, 0
    for t in tokens:
        sid = t.get('source_id')
        if sid is not None and sid in line_of:
            if t.get('origin') is None:
                raise LocalUnavailable('retained glyph without origin')
            current = line_of[sid]
            gap = order[sid] != previous + 1
            if gap:
                if order[sid] <= previous:
                    raise LocalUnavailable('retained glyphs out of source order')
                affected.extend(line_of[i] for i in ordered[previous + 1:order[sid]])
                affected.append(current)
            previous = order[sid]
            changed.append(gap)
        elif t.get('mode') == 'implicit-line-join':
            changed.append(False)
        else:
            changed.append(True)
            affected.append(current)
        home.append(current)
    if previous != len(ordered) - 1:
        affected.extend(line_of[i] for i in ordered[previous + 1:])
    tokens = _set_width(tokens, home, changed)
    # The widest word-space stretch of the paragraph's source lines bounds any
    # re-justified space (a wider gap reads as a column break to extractors).
    spec = dict(spec, stretch=max([t.get('squeezable', 0.) for t in tokens if t['text'] == ' '] or [0.]))
    # The paragraph as first approved (auto_restore): a re-inserted glyph takes
    # the advance it had where the recorded text continues as recorded.
    original = spec.get('recorded')
    recorded = align_recorded(tokens, original) if original else {}
    if recorded:
        # Only a run of the recorded text that reaches a glyph still drawn
        # (not a chance match inside a new word: 'ra' of 'array' in 'range').
        runs, run = [], []
        for m in range(len(tokens)):
            if run and m in recorded and recorded.get(m - 1) == recorded[m] - 1:
                run.append(m)
            else:
                runs.append(run)
                run = [m] if m in recorded else []
        anchored = {m for r in runs + [run] if any(tokens[q].get('source_id') is not None for q in r) for m in r}
        tokens = [dict(t) for t in tokens]
        for m, t in enumerate(tokens):
            j = recorded.get(m)
            # A word space's drawn advance is its line's justification, not its own.
            if (j is not None and m in anchored and t.get('source_id') is None and t['text'] != ' '
                    and recorded.get(m + 1) == j + 1 and original['glyphs'][j] is not None and original['glyphs'][j][3] is not None):
                t['advance'] = original['glyphs'][j][3]
    if spec.get('align') == 'justify-cjk':
        # A justified CJK line may tighten its glyph gaps a little (0.05 em
        # each) to keep its source break; fit() counts that allowance.
        tokens = [dict(t, squeezable=max(t.get('squeezable', 0.), CJK_GAP * t['size'])) if ord(t['text'][0]) >= 0x2E80 else t
                  for t in tokens]
    edits = [k for k, c in enumerate(changed) if c]
    last_edit = max(edits) if edits else (len(tokens) - 1 if affected else -1)
    first_line = min(affected) if affected else n
    text = ''.join(t['text'] for t in tokens)
    breaks = set(line_break_boundaries(text))
    breaks.add(len(tokens))
    # A source line end is a legal break where it was one, while it is intact:
    # the line's first glyph still follows the previous line's last glyph (an
    # edit across it may have joined a word: 'p|osition').
    starts = {}
    for k in range(1, n):
        j = next((m for m, t in enumerate(tokens) if home[m] >= k), None)
        if j is None or home[j] != k or tokens[j].get('source_id') != rows[k]['ids'][0]:
            continue
        before = tokens[j - 1] if j else None
        if before is not None and before.get('mode') == 'implicit-line-join':
            before = tokens[j - 2] if j > 1 else None
        if before is not None and before.get('source_id') == rows[k - 1]['ids'][-1]:
            starts[k] = j
            breaks.add(j)
    extra_x = sorted(r['x'] for r in rows[1:])[(n - 1) // 2] if n > 1 else spec['left']
    # Measured to the last visible glyph, as _end measures a placed line.
    soft = [spec['right'] - r.get('ink_end', r['pen_end']) for r in rows[:-1] if not r['hard']]
    ragged = max(soft) if soft else 0.

    def baseline(k):
        return rows[k]['baseline'] if k < n else rows[-1]['baseline'] + (k - n + 1) * spec['line_height']

    def after(k):
        """The first token of a source line after ``k`` (where source line ``k`` ended)."""
        return next((m for m, h in enumerate(home) if h > k), len(tokens))

    # A justified line that keeps its source break ends where the source line
    # ended (its last visible glyph, if retained): a writer's justified lines
    # end a little apart, not all at the widest one.
    retained = {t['source_id']: t for t, c in zip(tokens, changed) if t.get('source_id') is not None and t.get('origin') is not None}

    def row_end(k):
        if k >= n or spec.get('align') not in ('justify', 'justify-cjk'):
            return None
        ids = rows[k]['ids']
        ink = [m for m, i in enumerate(ids) if i in retained and retained[i]['text'].strip()]
        # Its last visible glyph and the spaces after it are all still there.
        if not ink or any(i not in retained or retained[i]['text'].strip() for i in ids[ink[-1] + 1:]):
            return None
        t = retained[ids[ink[-1]]]
        return t['origin'][0] + t.get('nominal_advance', t['advance'])

    def source_line(k, a, b):
        """Tokens a..b are exactly source line k."""
        ids = [tokens[m].get('source_id') for m in range(a, b) if tokens[m].get('mode') != 'implicit-line-join']
        return k < n and ids == rows[k]['ids'] and not any(changed[a:b])

    def kept(a, b, y, dy=0.):
        """Tokens a..b as drawn (moved by ``dy``); an implicit line-join space,
        which has no glyph, follows the glyph before it."""
        out = []
        for t in tokens[a:b]:
            if t.get('origin') is None:
                prev = out[-1] if out else None
                out.append(dict(t, x=prev['x'] + prev['advance'] if prev else 0., y=y + dy))
            else:
                out.append(dict(t, x=t['origin'][0], y=t['origin'][1] + dy))
        return out

    # A recorded line whose text comes back as recorded is drawn where it was.
    row_at = {r[0]: m for m, r in enumerate(original['rows'])} if recorded else {}
    shift = rows[0]['baseline'] - original['rows'][0][2] if recorded else 0.

    def recorded_line(a, k, limit):
        j = recorded.get(a)
        if j not in row_at:
            return None
        m = row_at[j]
        start, stop, base = original['rows'][m][:3]
        b = a + stop - start
        if b > len(tokens) or any(recorded.get(a + q) != start + q for q in range(stop - start)):
            return None
        # The implicit join after a recorded line belongs to it.
        if b < len(tokens) and stop < len(original['text']) and original['glyphs'][stop] is None and recorded.get(b) == stop:
            b += 1
        # The line ends where it ended: what follows it is what followed it.
        follows = b == len(tokens) or recorded.get(b) == start + b - a
        if not follows or (m == 0) != (k == 0) or (limit is not None and b > limit):
            return None
        y = base + shift if m == k else baseline(k)
        placed = []
        for q in range(a, b):
            g = original['glyphs'][recorded[q]]
            if g is None:
                # The recorded line drew no glyph for its line-end join.
                prev = placed[-1]
                placed.append(dict(tokens[q], x=prev['x'] + prev['advance'], y=y, undrawn=tokens[q]['text'] == ' '))
            else:
                x, gy = g[1], g[2] - base + y
                o = tokens[q].get('origin') if tokens[q].get('source_id') is not None and not changed[q] else None
                # A glyph still drawn where it was recorded (to the record's
                # rounding) keeps its exact origin: its line stays as drawn.
                if o is not None and abs(o[0] - x) < 1e-4 and abs(o[1] - gy) < 1e-4:
                    x, gy = o
                placed.append(dict(tokens[q], x=x, y=gy))
        if all(p.get('origin') is not None and p['x'] == p['origin'][0] for p in placed if p['text'].strip()):
            y = placed[0]['y']
        line = dict(text=text[a:b], baseline=y, tokens=placed, justified=False, end=b)
        line['restored'] = _differs(line)
        if _end(line) > spec['right'] + .05 or placed[0]['x'] < spec['left'] - .05:
            return None
        return line

    lines = []
    cursor = 0
    for k in range(min(first_line, n)):
        end = after(k)
        lines.append(dict(text=text[cursor:end], baseline=rows[k]['baseline'], tokens=kept(cursor, end, rows[k]['baseline']),
                          justified=False, kept=True, end=end))
        cursor = end
    k = len(lines)
    hard_ends = [starts[j + 1] for j in range(n - 1) if rows[j]['hard'] and j + 1 in starts]
    hyphen_ends = {starts[j + 1] for j in range(n - 1) if rows[j]['ids'][-1] in hyphens and j + 1 in starts}
    while cursor < len(tokens):
        x0 = rows[k]['x'] if k < n else extra_x
        limit = min((e for e in hard_ends if e > cursor), default=None)
        choice = recorded_line(cursor, k, limit) if recorded else None
        ends = fit(tokens, text, breaks, cursor, spec['right'] - x0) if choice is None else [choice['end']]
        if not ends:
            raise Refused('capacity: no legal line break at original font size')
        allowed = [e for e in ends if limit is None or e <= limit] or [min(ends)]
        greedy = max(allowed)
        source_end = after(k) if k < n else None
        if choice is None and source_end is not None and source_end in allowed and source_end != greedy:
            # Before a line whose break is fixed (an author's break or a kept
            # hyphen), taking text from it would leave it short: a justified
            # line keeps its source break, made flush as a whole if need be.
            candidate = _line(tokens, text, cursor, source_end, x0, baseline(k), changed, home, k, rows, spec, last=False,
                              target=row_end(k), full=k + 1 < n and rows[k + 1]['hard'])
            if _keeps_shape(candidate, spec, rows[k], ragged):
                choice = candidate
        if choice is None:
            # The longest legal line whose placed glyphs stay within the frame
            # (a justified line may not give back enough stretch). A justified
            # line that takes all that fits is made flush whatever its stretch.
            for e in sorted(allowed, reverse=True):
                choice = _line(tokens, text, cursor, e, x0, baseline(k), changed, home, k, rows, spec, last=e >= len(tokens),
                               target=row_end(k) if e == source_end else None, full=e == max(ends) or e in hyphen_ends)
                if _end(choice) <= spec['right'] + .05:
                    break
        end = choice['end']
        # A hyphenated word fragment pushed onto a line of its own: its hyphen
        # may be soft (the word would rejoin) or hard; neither is provable.
        if end in hyphen_ends and not any(t['text'] == ' ' for t in tokens[cursor:end - 1]) and not source_line(k, cursor, end):
            raise Refused('reflow would leave a hyphenated word fragment alone on a line (soft or hard hyphen is ambiguous)')
        choice['kept'] = source_line(k, cursor, end) and all(p.get('origin') is None or abs(p['x'] - p['origin'][0]) < 1e-9
                                                             and abs(p['y'] - p['origin'][1]) < 1e-9 for p in choice['tokens'])
        lines.append(choice)
        cursor = end
        k += 1
        # Back on a source line start after the last edit: the rest is as drawn
        # (unless the recorded line there differs from it: it is drawn as recorded).
        if (cursor < len(tokens) and cursor > last_edit and not any(changed[cursor:])
                and not (recorded and _differs(recorded_line(cursor, k, None)))):
            j = next((j for j, s in starts.items() if s == cursor), None)
            if j is not None:
                dy = baseline(k) - rows[j]['baseline'] if k < n else (k - j) * spec['line_height']
                for m in range(j, n):
                    end = after(m)
                    moved = kept(cursor, end, rows[m]['baseline'], dy)
                    lines.append(dict(text=text[cursor:end], baseline=rows[m]['baseline'] + dy, tokens=moved, justified=False,
                                      kept=abs(dy) < 1e-9, end=end))
                    cursor = end
                break
    return lines


def _differs(line):
    """A placed line some glyph of which is not at its drawn origin."""
    return line is not None and any(p.get('origin') is None and p['text'].strip() or p.get('origin') is not None
                                    and (abs(p['x'] - p['origin'][0]) > 1e-9 or abs(p['y'] - p['origin'][1]) > 1e-9) for p in line['tokens'])


def _line(tokens, text, a, b, x0, y, changed, home, k, rows, spec, last, target=None, full=False):
    """One placed line (tokens a..b): positions, then the alignment of the
    paragraph applied to the glyphs after the first re-placed one. A justified
    line is made flush with ``target`` (its source line's end) when given;
    ``full``: no more text fits, the line is justified as a whole."""
    placed = _place(tokens, a, b, x0, y, changed, home, k, rows)
    justified = False
    moved = any(p.get('moved_from_prefix') for p in placed)
    # An author's break is not justified; a kept hyphen break is the writer's.
    if moved and not last and spec.get('align') in ('justify', 'justify-cjk') and not (k < len(rows) and rows[k].get('author', rows[k]['hard'])):
        justified = _justify(placed, spec['right'] if target is None else target, cjk=spec['align'] == 'justify-cjk', full=full,
                             widest=spec.get('stretch', 0.))
    elif moved:
        squeeze = _squeezed(placed, spec['right'] - x0)
        if squeeze:
            _shift(placed, squeeze)
    return dict(text=text[a:b], baseline=y, tokens=placed, justified=justified, end=b)


def _set_width(tokens, home, changed):
    """New glyphs set like their neighbours: a CJK ideograph takes the median
    observed tracking of the retained ideographs of its font and size on its
    source line (at least three; else of the paragraph), a punctuation mark the
    median observed advance of the same mark, font and size in the paragraph.
    A writer compresses or spaces a justified CJK line as a whole; a new glyph
    at its nominal width would make an edited line longer than the same text."""
    from statistics import median
    from .auto_style import PUNCTUATION
    def cjk(t):
        return ord(t['text'][0]) >= 0x2E80 and t['text'] not in PUNCTUATION
    retained = [(h, t) for h, t, c in zip(home, tokens, changed) if t.get('source_id') is not None and t.get('origin') is not None]
    line_tracking, paragraph_tracking, marks = {}, {}, {}
    for h, t in retained:
        key = (t['font_name'], t['size'])
        if cjk(t):
            line_tracking.setdefault((h,) + key, []).append(t['advance'] - t['nominal_advance'])
            paragraph_tracking.setdefault(key, []).append(t['advance'] - t['nominal_advance'])
        elif t['text'] in PUNCTUATION:
            marks.setdefault((t['text'],) + key, []).append(t['advance'])
    out = []
    for h, t in zip(home, tokens):
        if t.get('source_id') is None and t.get('mode') != 'implicit-line-join' and not t.get('font_key'):
            key = (t['font_name'], t['size'])
            if cjk(t):
                seen = line_tracking.get((h,) + key, [])
                seen = seen if len(seen) >= 3 else paragraph_tracking.get(key, [])
                # A positional gap carried over from the replaced text stays after it.
                if len(seen) >= 3:
                    t = dict(t, advance=t['nominal_advance'] + median(seen) + t.get('positional_gap', 0.))
            elif t['text'] in PUNCTUATION and marks.get((t['text'],) + key):
                t = dict(t, advance=median(marks[(t['text'],) + key]) + t.get('positional_gap', 0.))
        out.append(t)
    return out


def _keeps_shape(line, spec, row, ragged):
    """Whether a line may keep its source break although more text would fit:
    a justified line that was made flush again within the stretch allowance,
    or a ragged line no shorter than the paragraph's own ragged lines."""
    if row['hard']:
        return True
    if spec.get('align') in ('justify', 'justify-cjk'):
        return line['justified'] or _end(line) >= spec['right'] - .05
    return spec['right'] - _end(line) <= ragged + RAGGED * row['size']


def _end(line):
    ink = [p for p in line['tokens'] if p['text'].strip()]
    if not ink:
        return line['tokens'][0]['x'] if line['tokens'] else 0.
    return ink[-1]['x'] + ink[-1].get('nominal_advance', ink[-1]['advance'])


def _place(tokens, a, b, x0, y, changed, home, k, rows):
    """Glyph positions of one line: unchanged source glyphs at the line start
    keep their origins; from the first edit on, each glyph follows the
    previous one by its observed advance."""
    placed, x, prefix = [], x0, True
    first = rows[k]['ids'][0] if k < len(rows) else None
    for m in range(a, b):
        t = tokens[m]
        if prefix and (changed[m] or home[m] != k or t.get('origin') is None or (m == a and t.get('source_id') != first)):
            prefix = False
        offset = t.get('baseline_offset', 0.)
        if prefix:
            placed.append(dict(t, x=t['origin'][0], y=t['origin'][1]))
            x = t['origin'][0] + t['advance']
        else:
            placed.append(dict(t, x=x, y=y + offset, moved_from_prefix=True))
            x += t['advance']
    return placed


def _shift(placed, extra):
    run = 0.
    for n, p in enumerate(placed):
        p['x'] += run
        run += extra.get(n, 0.)


def _justify(placed, right, cjk=False, full=False, widest=0.):
    """Make the line flush with ``right`` by spreading the slack over the
    glyphs after the first re-placed one: word spaces (Latin) or every glyph
    gap (CJK, at most 0.05 em each). Glyphs before it keep their origins.
    Latin spaces after the edit go back to their natural width and share the
    slack evenly, as the writer justified them (an edit and its undo give the
    same spaces); each may stretch as far as the source stretched its spaces
    plus a quarter em. A Latin line that ends where it must (``full``: no
    more text fits, a kept hyphen, or a source break before a fixed one) is
    made flush as a writer makes it when that fails: the slack goes to every
    space of the line, glyphs before the edit included, none wider than the
    paragraph's widest source stretch (``widest``) plus a quarter em."""
    if full and not cjk and not _justify([dict(p) for p in placed], right, cjk):
        whole = [dict(p, moved_from_prefix=True) for p in placed]
        if _justify(whole, right, cjk, full=None, widest=widest):
            for p, q in zip(placed, whole):
                p['x'] = q['x']
            return True
        return False
    first = next((n for n, p in enumerate(placed) if p.get('moved_from_prefix')), None)
    last = max((n for n, p in enumerate(placed) if p['text'].strip()), default=None)
    if first is None or last is None or last <= first and not cjk:
        return False
    end = placed[last]['x'] + placed[last].get('nominal_advance', placed[last]['advance'])
    slack = right - end
    size = placed[last]['size']
    if abs(slack) < .01:
        return True
    gaps = list(range(max(first - 1, 0), last)) if cjk else [n for n in range(first, last) if placed[n]['text'] == ' ']
    if slack < 0 and cjk:
        # Overflow: tighten the glyph gaps after the edit, 0.05 em each at most.
        if not gaps or -slack / len(gaps) > CJK_GAP * size:
            return False
        _shift(placed, {n: slack / len(gaps) for n in gaps})
        return True
    if not gaps:
        return False
    if cjk:
        if slack / len(gaps) > CJK_GAP * size:
            return False
        _shift(placed, {n: slack / len(gaps) for n in gaps})
        return True
    stretch = {n: placed[n].get('squeezable', 0.) for n in gaps}
    natural = slack + sum(stretch.values())
    allowance = max(stretch.values()) if full is not None else max(max(stretch.values()), widest)
    if natural < -.01 or natural / len(gaps) > allowance + STRETCH * size:
        return False
    _shift(placed, {n: natural / len(gaps) - stretch[n] for n in gaps})
    return True
