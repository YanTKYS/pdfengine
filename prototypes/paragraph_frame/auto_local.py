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

STRETCH = .25   # em of extra advance per space a justified line may take
CJK_GAP = .05   # em of extra advance per glyph gap of a justified CJK line
RAGGED = .5     # em a ragged line may end short beyond the paragraph's own raggedness


class LocalUnavailable(Exception):
    pass


def local_layout(tokens, spec):
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
    rows = [dict(r, hard=r['hard'] or (k + 1 < len(rows) and r['ids'][-1] in hyphens)) for k, r in enumerate(rows)]
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
    # A source line end is a legal break where it was one.
    starts = {}
    for k in range(1, n):
        j = next((m for m, t in enumerate(tokens) if home[m] >= k), None)
        if j is not None and home[j] == k:
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
        ends = fit(tokens, text, breaks, cursor, spec['right'] - x0)
        if not ends:
            raise Refused('capacity: no legal line break at original font size')
        limit = min((e for e in hard_ends if e > cursor), default=None)
        allowed = [e for e in ends if limit is None or e <= limit] or [min(ends)]
        greedy = max(allowed)
        source_end = after(k) if k < n else None
        choice = None
        if source_end is not None and source_end in allowed and source_end != greedy:
            candidate = _line(tokens, text, cursor, source_end, x0, baseline(k), changed, home, k, rows, spec, last=False)
            if _keeps_shape(candidate, spec, rows[k], ragged):
                choice = candidate
        if choice is None:
            # The longest legal line whose placed glyphs stay within the frame
            # (a justified line may not give back enough stretch).
            for e in sorted(allowed, reverse=True):
                choice = _line(tokens, text, cursor, e, x0, baseline(k), changed, home, k, rows, spec, last=e >= len(tokens))
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
        # Back on a source line start after the last edit: the rest is as drawn.
        if cursor < len(tokens) and cursor > last_edit and not any(changed[cursor:]):
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


def _line(tokens, text, a, b, x0, y, changed, home, k, rows, spec, last):
    """One placed line (tokens a..b): positions, then the alignment of the
    paragraph applied to the glyphs after the first re-placed one."""
    placed = _place(tokens, a, b, x0, y, changed, home, k, rows)
    justified = False
    moved = any(p.get('moved_from_prefix') for p in placed)
    if moved and not last and spec.get('align') in ('justify', 'justify-cjk') and not (k < len(rows) and rows[k]['hard']):
        justified = _justify(placed, spec['right'], cjk=spec['align'] == 'justify-cjk')
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


def _justify(placed, right, cjk=False):
    """Make the line flush with ``right`` by spreading the slack over the
    glyphs after the first re-placed one: word spaces (Latin, at most a
    quarter em extra each) or every glyph gap (CJK, at most 0.05 em each).
    Glyphs before it keep their origins."""
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
    if slack < 0:
        # Overflow: give back stretch the source put into the spaces after the edit.
        room = {n: placed[n].get('squeezable', 0.) for n in gaps if placed[n].get('squeezable')}
        if sum(room.values()) + slack < -.01:
            return False
        ratio = -slack / sum(room.values())
        _shift(placed, {n: -v * ratio for n, v in room.items()})
        return True
    if not gaps:
        return False
    if slack / len(gaps) > (CJK_GAP if cjk else STRETCH) * size:
        return False
    _shift(placed, {n: slack / len(gaps) for n in gaps})
    return True
