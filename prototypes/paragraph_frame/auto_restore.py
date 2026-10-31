"""The paragraph's original layout, kept with its approved conditions (issue #93).

A PDF does not remember how its text was set before an edit: the saved PDF
alone shows only the edited drawing. When the text is changed back, local
layout (auto_local) has to set the re-inserted glyphs anew (nominal advances,
evenly justified spaces, the current line ends) and cannot reproduce what
the writer drew. The first approved edit of a paragraph therefore stores the
paragraph as it was drawn then, with its approved conditions in the page
(auto_conditions, PieceInfo), and every later approval carries it over:

* ``text``: the paragraph's logical text (implicit line-join spaces included);
* ``glyphs``: per character of ``text``, ``[unicode, x, y, advance, gid,
  font resource, code]`` of its drawn glyph (advance: pen step to the next
  glyph of the line, or null at a line end; code: the character code, hex),
  or null for an implicit join;
* ``rows``: per drawn line, ``[start, end, baseline, x, pen end, ink end,
  size, author break, hyphen]``: the character range of its glyphs, its
  baseline and start, where its pen and its last visible glyph end, its font
  size, whether its break is the author's and whether it ends with a hyphen
  (auto_detect.source_rows; records of PR #94 hold the first four);
* ``sizes``: per character of ``text``, the font size its glyph was drawn at
  (null for an implicit join): a glyph a later save redraws reads back at
  the size the writer wrote, a few 1e-10 apart, which local layout would
  take for another size;
* ``below``: the first baseline of the text block below the paragraph in its
  column and whether it followed the paragraph then (null if none): a block
  that was no follower is not pulled above its place by a shrinking edit.

It is layout evidence, never authority: a recorded position is used only for
a character that is the same glyph (unicode, glyph ID, font resource) in the
same order as recorded, and every staged-output check of the writer applies
to it as to any other position.

With full rows, later edits are laid out against the recorded layout as the
source drawing (``against``, issue #95): what differs from the recorded text
is the edit, whatever edits, undos and orders led to it, so an edit gives
the same lines however the paragraph got to its text.
"""
import difflib
import math

FIELDS = 7
ROW = 9     # row fields (PR #94 records: 4)


def snapshot(c, paragraph, rows, below=None, stats=None):
    """The drawn layout of ``paragraph`` (rows: auto_detect.source_rows), or None;
    ``stats``: how the page sets a new glyph (auto_style.build ``page_stats``)."""
    fonts, codes = {}, {}
    for e in c.events:
        for ch in e.chars:
            for i in ch.source_orders:
                fonts[i] = e.state.font.name if e.state.font else None
                codes[i] = ch.code.hex()
    ids = iter(paragraph.ids)
    glyphs, index = [], {}
    try:
        for n, char in enumerate(paragraph.text):
            if n in paragraph.joins:
                glyphs.append(None)
                continue
            i = next(ids)
            g = c.actual[i]
            if g['unicode'] != char:
                return None
            index[i] = n
            glyphs.append([char, round(g['origin'][0], 5), round(g['origin'][1], 5), None, g['gid'], fonts.get(i), codes.get(i)])
    except StopIteration:
        return None
    out_rows = []
    for r in rows:
        at = [index[i] for i in r['ids'] if i in index]
        if len(at) != len(r['ids']) or at != list(range(at[0], at[0] + len(at))):
            return None
        for a, b in zip(at, at[1:]):
            glyphs[a][3] = round(glyphs[b][1] - glyphs[a][1], 5)
        out_rows.append([at[0], at[-1] + 1, round(r['baseline'], 5), round(glyphs[at[0]][1], 5), round(r['pen_end'], 5),
                         round(r['ink_end'], 5), round(r['size'], 5), bool(r['hard']), r['ids'][-1] in paragraph.hyphens])
    out = dict(text=paragraph.text, glyphs=glyphs, rows=out_rows, below=below)
    if stats is not None:
        from .auto_style import source_tokens
        drawn, _ = source_tokens(c)
        sizes = [None if g is None else None for g in glyphs]
        for i, n in index.items():
            sizes[n] = drawn[i]['size'] if i in drawn else None
        if all(s is not None for s, g in zip(sizes, glyphs) if g is not None):
            out.update(stats=stats, sizes=sizes)
    return out


def valid(layout, width, height):
    """Whether a stored layout has the expected shape and values on the page."""
    finite = lambda v, low, high: type(v) in (int, float) and math.isfinite(v) and low <= v <= high
    try:
        text, glyphs, rows = layout['text'], layout['glyphs'], layout['rows']
        if not isinstance(text, str) or not isinstance(glyphs, list) or len(glyphs) != len(text) or not isinstance(rows, list) or not rows:
            return False
        for char, g in zip(text, glyphs):
            if g is None:
                continue
            if (len(g) != FIELDS or g[0] != char or not finite(g[1], -1, width + 1) or not finite(g[2], -1, height + 1)
                    or not (g[3] is None or finite(g[3], -width, width)) or type(g[4]) is not int or not (g[5] is None or isinstance(g[5], str))
                    or not (g[6] is None or isinstance(g[6], str) and len(g[6]) % 2 == 0 and all(ch in '0123456789abcdef' for ch in g[6]))):
                return False
        end = 0
        if len({len(r) for r in rows}) != 1:
            return False
        for r in rows:
            start, stop = r[0], r[1]
            if (len(r) not in (4, ROW) or type(start) is not int or type(stop) is not int or not end <= start < stop <= len(text)
                    or not finite(r[2], 0, height) or not finite(r[3], -1, width + 1) or any(glyphs[i] is None for i in range(start, stop))):
                return False
            if len(r) == ROW and not (finite(r[4], -1, width + 1) and finite(r[5], -1, width + 1) and finite(r[6], .1, height)
                                      and type(r[7]) is bool and type(r[8]) is bool):
                return False
            end = stop
        below = layout.get('below')
        if below is not None and not (finite(below['baseline'], 0, height + 1) and type(below['follower']) is bool):
            return False
        sizes = layout.get('sizes')
        if sizes is not None and (not isinstance(sizes, list) or len(sizes) != len(text)
                                  or any((g is None) != (s is None) or s is not None and not finite(s, .1, height) for s, g in zip(sizes, glyphs))):
            return False
        stats = layout.get('stats')
        if stats is not None:
            shapes = dict(tracking=(str, float, float, float), reduction=(str, float, float), marks=(str, str, float, float))
            for key, shape in shapes.items():
                for row in stats[key]:
                    if len(row) != len(shape) or not all(isinstance(v, str) if kind is str else finite(v, -height, height)
                                                         for v, kind in zip(row, shape)):
                        return False
        return True
    except (KeyError, TypeError, IndexError):
        return False


def shifted(layout, dy):
    """The layout of a following paragraph translated vertically with it."""
    if not layout:
        return layout
    below = layout.get('below')
    return dict(text=layout['text'], glyphs=[None if g is None else [g[0], g[1], round(g[2] + dy, 5)] + g[3:] for g in layout['glyphs']],
                rows=[[r[0], r[1], round(r[2] + dy, 5)] + r[3:] for r in layout['rows']],
                below=None if below is None else dict(below, baseline=round(below['baseline'] + dy, 5)),
                **{k: layout[k] for k in ('stats', 'sizes') if k in layout})


def follower_shift(layout, delta, follower_baseline):
    """The translation of the following paragraphs when the paragraph's last
    baseline moves by ``delta``: the block below keeps its gap, except that a
    block that did not follow the paragraph when its layout was recorded is
    not pulled above where it was then (the paragraph grew into its reach)."""
    below = (layout or {}).get('below')
    if delta >= 0 or below is None or below['follower']:
        return delta
    return min(0., max(delta, below['baseline'] - follower_baseline))


def same_glyph(t, g):
    """Token ``t`` draws the recorded glyph ``g``: same character and glyph ID of
    the same font program (the recorded font resource, or a new code for the
    same program once no drawn glyph witnesses the old one); never a substitute."""
    font_key = t.get('font_key') or ''
    return (g is not None and t['text'] == g[0] and t.get('gid') == g[4] and (not font_key or font_key.startswith('source:'))
            and t.get('font_name') == g[5] and t.get('mode') != 'implicit-line-join')


def preferences(text, layout):
    """New-text index -> recorded glyph, for characters of ``text`` in an
    unchanged run of the recorded text: a re-inserted character is drawn
    with the code it had there (auto_style.build) when its resource still
    decodes that code to the same character."""
    matcher = difflib.SequenceMatcher(None, text, layout['text'], autojunk=False)
    return {i + k: layout['glyphs'][j + k] for i, j, n in matcher.get_matching_blocks() for k in range(n)
            if layout['glyphs'][j + k] is not None}


def align(tokens, layout):
    """Token index -> recorded character index for tokens in an unchanged run of
    the recorded text that draw the recorded glyph (joins map to joins)."""
    a = ''.join(t['text'] for t in tokens)
    matcher = difflib.SequenceMatcher(None, a, layout['text'], autojunk=False)
    out = {}
    for i, j, n in matcher.get_matching_blocks():
        for k in range(n):
            g = layout['glyphs'][j + k]
            t = tokens[i + k]
            if g is None and t['text'] == ' ' or same_glyph(t, g):
                out[i + k] = j + k
    return out


def full(layout):
    """Whether a stored layout holds the rows ``against`` needs (not a PR #94 record)."""
    return bool(layout) and all(len(r) == ROW for r in layout['rows'])


def retained(tokens, layout, aligned=False):
    """Token index -> recorded character index for the text that is still the
    recorded text, as a first edit of the recorded paragraph would retain it
    (auto_style.changed_runs): the equal runs of a character alignment of the
    recorded text with the new one (ties go to the earliest recorded run);
    without ``aligned`` (a paragraph of one marked-content sequence, whose
    edit is one changed run between a common prefix and suffix), a run
    shorter than the changes on its two sides is part of one change (a chance
    match inside rewritten text: 'ra' of 'array' and 'range', a phrase whose
    words swap places). Each insertion or deletion is slid as far right as it
    goes ('the end user' inserts 'end ', not ' end'); a character not drawn
    with the recorded glyph is new text. A line join maps to a join or a
    space. The result depends on the text and the record alone, not on which
    edits, in which order, drew the paragraph as it is."""
    a = ''.join(t['text'] for t in tokens)
    b = layout['text']
    blocks = [[j, i, n] for i, j, n in difflib.SequenceMatcher(None, b, a, autojunk=False).get_matching_blocks() if n]
    gap = lambda x, y: max(y[0] - x[0] - x[2], y[1] - x[1] - x[2])
    merged = not aligned
    while merged:
        merged = False
        bounds = [[0, 0, 0]] + blocks + [[len(a), len(b), 0]]
        for k in range(1, len(bounds) - 1):
            before, after = gap(bounds[k - 1], bounds[k]), gap(bounds[k], bounds[k + 1])
            if before and after and bounds[k][2] < before + after:
                del blocks[k - 1]
                merged = True
                break
    for x, y in zip(blocks, blocks[1:]):
        # Exactly one side has text between the two runs: slide it right.
        while y[2] and (x[0] + x[2] < y[0]) != (x[1] + x[2] < y[1]):
            if (a[x[0] + x[2]] if x[0] + x[2] < y[0] else b[x[1] + x[2]]) != a[y[0]]:
                break
            x[2] += 1
            y[0] += 1
            y[1] += 1
            y[2] -= 1
    out = {}
    for i, j, n in blocks:
        for k in range(n):
            t, g = tokens[i + k], layout['glyphs'][j + k]
            if (g is None or t.get('mode') == 'implicit-line-join') and t['text'] == ' ' == b[j + k] or same_glyph(t, g):
                out[i + k] = j + k
    return out


def plain(t):
    """A glyph's own advance: its width with character and word spacing."""
    return t['nominal_advance'] + (t.get('tc', 0.) + (t.get('tw', 0.) if t.get('code') == b' ' else 0.)) * t['tz'] / 100


def alone(t, stats):
    """``t`` set as new text, as auto_style.build sets it on the page ``stats``
    describe: its own advance with its font's tracking, the CJK compression
    and the punctuation advance the page's writer used."""
    from .auto_style import PUNCTUATION
    near = lambda a, b: abs(a - b) < 1e-3
    t = dict(t)
    t.pop('squeezable', None)
    t['advance'] = plain(t) + next((v for f, s, z, v in stats['tracking'] if f == t['font_name'] and near(s, t['size']) and near(z, t['tz'])), 0.)
    if ord(t['text'][0]) >= 0x2E80 and t['text'] not in PUNCTUATION:
        squeeze = next((v for f, s, v in stats['reduction'] if f == t['font_name'] and near(s, t['size'])), None)
        if squeeze is not None:
            t['squeezable'] = squeeze
    mark = next((v for c, f, s, v in stats['marks'] if c == t['text'] and f == t['font_name'] and near(s, t['size'])), None)
    if mark is not None and mark < t['advance']:
        t['advance'] = mark
    return t


def against(tokens, layout, first_baseline, aligned=False, first_ids=None):
    """``tokens`` as a first edit of the recorded paragraph would see them, and
    its source rows and line-final hyphens: a retained character is the
    recorded glyph (its recorded origin and advance, an ID of its own); any
    other character is new text set at its natural advance, and a space where
    the recorded paragraph changed lines is a line join. ``token`` keeps each
    one's index in ``tokens``; ``aligned`` as in ``retained``. ``first_baseline`` places the recorded paragraph
    (a follower moved since its record was written moves its record with it).
    ``first_ids``: the glyphs drawn on the paragraph's first line now. When
    recorded first-line glyphs among them all moved by one amount, that is the
    paragraph's move: a line's baseline is a median of its glyphs' baselines,
    and on a line mixing baselines (a smaller label raised or lowered) it
    changes with the line's text, not with a move (PR #106, R13)."""
    keep = retained(tokens, layout, aligned)
    shift = first_baseline - layout['rows'][0][2]
    a, b = layout['rows'][0][:2]
    moves = [t['origin'][1] - layout['glyphs'][j][2] for m, j in keep.items() if a <= j < b and layout['glyphs'][j] is not None
             and (t := tokens[m]).get('origin') and t.get('source_id') in set(first_ids or ())]
    if moves and max(moves) - min(moves) < 1e-3:
        shift = moves[0]
    stats = layout.get('stats') or tokens[0]['page_stats']
    sizes = layout.get('sizes')
    out, size = [], {}
    for m, t in enumerate(tokens):
        v = dict(t, token=m)
        v.pop('positional_gap', None)
        j = keep.get(m)
        g = layout['glyphs'][j] if j is not None else None
        # The size the recorded paragraph drew a glyph at; new text takes that
        # of the recorded glyph of its font before it, as it takes its style.
        drawn = sizes[j] if sizes and g is not None else size.get(t['font_name'])
        if drawn is not None and abs(drawn - t['size']) < 1e-4:
            v.update(size=drawn, nominal_advance=t['nominal_advance'] * drawn / t['size'])
        if sizes and g is not None:
            size[t['font_name']] = sizes[j]
        if g is not None:
            v.pop('squeezable', None)
            origin = [g[1], g[2] + shift]
            # A glyph still drawn where it was recorded (to the record's rounding)
            # keeps its exact origin: its line stays as drawn.
            if t.get('source_id') is not None and t.get('origin') and all(abs(p - q) < 1e-4 for p, q in zip(t['origin'], origin)):
                origin = list(t['origin'])
            v.update(source_id=('recorded', j), origin=origin, advance=g[3] if g[3] is not None else plain(t))
            if v.get('mode') == 'implicit-line-join':
                v['mode'] = 'recorded-space'
        elif j is not None or t.get('mode') == 'implicit-line-join':
            v = dict(alone(v, stats), source_id=None, origin=None, mode='implicit-line-join')
        else:
            v = dict(alone(v, stats), source_id=None, origin=None)
            if t.get('source_id') is not None:
                v['mode'] = 'redrawn-source'
        out.append(v)
    rows = [dict(ids=[('recorded', j) for j in range(r[0], r[1])], baseline=r[2] + shift, x=r[3], pen_end=r[4], ink_end=r[5], size=r[6], hard=r[7])
            for r in layout['rows']]
    hyphens = [('recorded', r[1] - 1) for r in layout['rows'][:-1] if r[8]]
    return out, rows, hyphens
