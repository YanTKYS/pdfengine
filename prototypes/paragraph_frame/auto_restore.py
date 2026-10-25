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
* ``rows``: per drawn line, ``[start, end, baseline, x]``, the character
  range of its glyphs;
* ``below``: the first baseline of the text block below the paragraph in its
  column and whether it followed the paragraph then (null if none): a block
  that was no follower is not pulled above its place by a shrinking edit.

It is layout evidence, never authority: a recorded position is used only for
a character that is the same glyph (unicode, glyph ID, font resource) in the
same order as recorded, and every staged-output check of the writer applies
to it as to any other position.
"""
import difflib
import math

FIELDS = 7


def snapshot(c, paragraph, rows, below=None):
    """The drawn layout of ``paragraph`` (rows: auto_detect.source_rows), or None."""
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
        out_rows.append([at[0], at[-1] + 1, round(r['baseline'], 5), round(glyphs[at[0]][1], 5)])
    return dict(text=paragraph.text, glyphs=glyphs, rows=out_rows, below=below)


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
        for r in rows:
            start, stop = r[0], r[1]
            if (len(r) != 4 or type(start) is not int or type(stop) is not int or not end <= start < stop <= len(text)
                    or not finite(r[2], 0, height) or not finite(r[3], -1, width + 1) or any(glyphs[i] is None for i in range(start, stop))):
                return False
            end = stop
        below = layout.get('below')
        return below is None or (finite(below['baseline'], 0, height + 1) and type(below['follower']) is bool)
    except (KeyError, TypeError, IndexError):
        return False


def shifted(layout, dy):
    """The layout of a following paragraph translated vertically with it."""
    if not layout:
        return layout
    below = layout.get('below')
    return dict(text=layout['text'], glyphs=[None if g is None else [g[0], g[1], round(g[2] + dy, 5)] + g[3:] for g in layout['glyphs']],
                rows=[[r[0], r[1], round(r[2] + dy, 5), r[3]] for r in layout['rows']],
                below=None if below is None else dict(below, baseline=round(below['baseline'] + dy, 5)))


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
