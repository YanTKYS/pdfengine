"""ParagraphIR: the edited paragraph as a document model, independent of the PDF's operators (issue #124).

The PDF is read once (adapter.import_paragraph) into a ParagraphIR. Edits change
the IR only: its logical text, its characters' styles and spacing, its layout
parameters. Layout is computed from the IR. The PDF's operators are used again
only by the write-back adapter (adapter.write), which removes the source glyphs
of the lines whose layout changed and draws the IR's lines.

    ParagraphIR
      text            the logical text (str), one IRChar per character
      chars[i]        IRChar(text, style, adv, kind, src)
                        kind  'glyph' (drawn), 'gap' (a positional space the source drew no glyph for),
                              'join' (a Latin line join: no glyph, a word gap if a line break moves)
                        adv   the distance to the next character (source spacing kept: kerning, gaps,
                              justification); new characters: the font's advance
                        src   SourceRef(order, line, code, origin) for a character read from the PDF, else None
      styles[id]      Style(font resource, size, text matrix, fill, Tc/Tw/Tz/Ts/Tr, codec) -- what to draw with
      layout          Frame(left, first_x, right, baselines, pitch, alignment, hard_breaks, ascent, descent)
      relations       container (the drawing holding the paragraph), obstacles (foreign content near it),
                      followers (content directly below), structure (the marked content of each line)
      history         the edits, for undo

Layout (layout()) keeps every source line's break unless the line no longer
fits; a changed justified line is re-justified; overflow moves to the next
line, and past the last line new lines are added at the paragraph's pitch.
A line whose characters are exactly the source line's, at the same place, is
'retained' (the writer leaves its glyphs untouched).
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

NO_START = set('、。，．・：；？！）」』】〕｝〉》ーぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ,.;:!?)]}')
NO_END = set('（「『【〔｛〈《([{')


def is_cjk(ch):
    return ord(ch) >= 0x2E80


class Refused(Exception):
    pass


@dataclass(frozen=True)
class SourceRef:
    order: int          # the glyph's index in MuPDF's text trace of the source page
    line: int           # the source line it was drawn on
    code: bytes         # its character code in its font resource
    origin: tuple       # where it was drawn (page space, y down)
    marked: tuple = ()  # the marked-content sequences open around it (tags, optional content)


@dataclass
class IRChar:
    text: str
    style: str
    adv: float
    kind: str = 'glyph'
    src: SourceRef | None = None


@dataclass
class Style:
    font: str           # font resource name (as the page's resources name it)
    font_xref: int
    basefont: str
    size: float         # Tf size
    matrix: tuple       # linear part (a, b, c, d) of Tm x CTM, PDF space
    fill: tuple         # ('k' | 'rg' | 'g', values)
    tc: float
    tw: float
    tz: float
    tr: int
    ascent: float       # glyph box above/below the baseline, page space
    descent: float
    codec: object = field(default=None, repr=False, compare=False)

    def advance(self, ch):
        """(code, advance in page space) of ``ch`` in this font resource, or None if the resource has no code for it."""
        try:
            code = self.codec.encode_available(ch)[0]
        except Exception:
            return None
        width = self.codec.decode(code)[0][2]
        text_space = (width / 1000 * self.size + self.tc + (self.tw if code == b' ' else 0)) * self.tz / 100
        return code, text_space * self.matrix[0]


@dataclass
class Frame:
    left: float
    first_x: float
    right: float
    baselines: list
    pitch: float
    alignment: str      # 'left' | 'justify' | 'center'
    center: float
    hard_breaks: bool   # every source line ends with a hard break (centered blocks)
    max_width: float    # the widest a line may be (center: the widest source line)


@dataclass
class Line:
    start: int
    end: int
    baseline: float
    xs: list
    source_line: int | None     # the source line this line stands in (None: a new line)
    retained: bool

    def box(self, ir, k=None):
        """(x0, top, x1, bottom) of the drawn glyphs (page space)."""
        idx = [i for i in range(self.start, self.end) if ir.chars[i].kind == 'glyph']
        if not idx:
            return None
        x0 = min(self.xs[i - self.start] for i in idx)
        x1 = max(self.xs[i - self.start] + ir.chars[i].adv for i in idx)
        st = [ir.styles[ir.chars[i].style] for i in idx]
        return (x0, self.baseline - max(s.ascent for s in st), x1, self.baseline + max(s.descent for s in st))


@dataclass
class ParagraphIR:
    source: dict
    chars: list
    styles: dict
    frame: Frame
    relations: dict
    source_lines: list          # per source line: dict(start, end, baseline, x0, x1, orders, box)
    gap_width: float            # a word gap in this paragraph (median), for joins that become gaps
    min_gap: float = 0.0        # the narrowest word gap: a justified line's gaps shrink to it to fit
    history: list = field(default_factory=list)
    selection: tuple | None = None

    @property
    def text(self):
        return ''.join(c.text for c in self.chars)

    # -- edits (model only) ------------------------------------------------------------------------------------

    def find(self, old, near=None):
        """[a, b) of ``old`` in the text, whitespace aside; the occurrence holding (or nearest) index ``near``."""
        bare = [(i, c.text) for i, c in enumerate(self.chars) if not c.text.isspace()]
        flat = ''.join(t for _, t in bare)
        want = ''.join(old.split())
        spans = []
        k = flat.find(want)
        while want and k >= 0:
            spans.append((bare[k][0], bare[k + len(want) - 1][0] + 1))
            k = flat.find(want, k + 1)
        if not spans:
            raise Refused('the selected text is not in the paragraph')
        if near is None:
            return spans[0]
        return min(spans, key=lambda s: 0 if s[0] <= near < s[1] else 1 + min(abs(s[0] - near), abs(s[1] - near)))

    def replace(self, a, b, new):
        """Replace [a, b) with ``new``. Characters the old and new text share at either end (white space the source
        drew as a gap aside) are kept, with their source spacing and provenance; the rest are new characters in the
        style of the character before them. Returns the record pushed on the history."""
        old = self.chars[a:b]
        p = q = 0
        i, j = 0, 0
        while i < len(old) and j < len(new):
            if old[i].text == new[j]:
                i += 1
                j += 1
            elif old[i].kind != 'glyph' and old[i].text.isspace() and not new[j].isspace():
                i += 1
            else:
                break
        p, pn = i, j
        i, j = len(old), len(new)
        while i > p and j > pn:
            if old[i - 1].text == new[j - 1]:
                i -= 1
                j -= 1
            elif old[i - 1].kind != 'glyph' and old[i - 1].text.isspace() and not new[j - 1].isspace():
                i -= 1
            else:
                break
        q, qn = i, j
        anchor = self.chars[a + p - 1] if a + p > 0 else self.chars[a + p] if a + p < len(self.chars) else self.chars[-1]
        # Typing back what an earlier edit took out at this place: the IR still holds those characters, with their
        # source spacing and provenance, and gives them back (the model remembers; the PDF is not read again).
        back = next((h for h in reversed(self.history) if h['at'] == a + p and h['inserted'] == q - p
                     and ''.join(c.text for c in self.chars[a + p:a + q]) == h['text'] and h['old'] == new[pn:qn]), None)
        inserted = list(back['removed']) if back else [self._new_char(ch, anchor) for ch in new[pn:qn]]
        removed = self.chars[a + p:a + q]
        self.chars[a + p:a + q] = inserted
        record = dict(at=a + p, removed=removed, inserted=len(inserted), text=new[pn:qn], old=''.join(c.text for c in removed))
        self.history.append(record)
        return record

    def undo(self):
        """Take back the last edit: the removed characters come back with their source spacing and provenance."""
        if not self.history:
            raise Refused('nothing to undo')
        r = self.history.pop()
        self.chars[r['at']:r['at'] + r['inserted']] = r['removed']
        return r

    def _new_char(self, ch, anchor):
        style = self.styles[anchor.style]
        if ch.isspace():
            got = style.advance(' ')
            # A font without a space glyph (TeX subsets): the word gap is positioning, not a glyph.
            if got is None:
                return IRChar(' ', anchor.style, self.gap_width, 'gap')
            return IRChar(' ', anchor.style, got[1], 'glyph')
        got = style.advance(ch)
        if got is None:
            raise Refused(f'the paragraph\'s font ({style.basefont}) has no code for {ch!r} (U+{ord(ch):04X}): '
                          'a new glyph in the same program or a substitute font is not done by this PoC')
        return IRChar(ch, anchor.style, got[1], 'glyph')

    def snapshot(self):
        return deepcopy([(c.text, c.style, round(c.adv, 4), c.kind, c.src) for c in self.chars])

    # -- layout ------------------------------------------------------------------------------------------------

    def _lines_by_source(self):
        """The characters grouped by the source line they belong to (new characters join the line of the one before)."""
        groups, cur = [], None
        for i, c in enumerate(self.chars):
            line = c.src.line if c.src is not None else cur if cur is not None else 0
            if not groups or line != cur:
                groups.append([line, i, i + 1])
            else:
                groups[-1][2] = i + 1
            cur = line
        return groups

    def _is_gap(self, c):
        return c.kind in ('gap', 'join') or c.text == ' '

    def _adv(self, i):
        """A character's advance; in a justified paragraph a word gap at its narrowest (the stretch is set again
        when the line is placed)."""
        c = self.chars[i]
        if self.frame.alignment == 'justify' and self._is_gap(c) and self.min_gap:
            return min(c.adv, self.min_gap)
        return c.adv

    def _width(self, a, b):
        while b > a and (self.chars[b - 1].kind != 'glyph' or self.chars[b - 1].text.isspace()):
            b -= 1
        return sum(self._adv(i) for i in range(a, b)), b

    def _breaks(self, a, b):
        """Positions in (a, b) where a line may break: after a gap/join (Latin), or between CJK characters
        (not before a closing mark, not after an opening one)."""
        out = []
        for i in range(a + 1, b):
            prev, cur = self.chars[i - 1], self.chars[i]
            if cur.kind in ('gap', 'join') or cur.text == ' ':
                continue
            if prev.kind in ('gap', 'join') or prev.text == ' ':
                out.append(i)
            elif (is_cjk(prev.text) or is_cjk(cur.text)) and cur.text not in NO_START and prev.text not in NO_END:
                out.append(i)
        return out

    def layout(self):
        """The paragraph's lines from the IR: list of Line. Source line breaks are kept while a line fits; the part
        of a line that no longer fits goes to the start of the next line (soft breaks) or onto a line of its own
        (hard breaks); past the last line, new lines are added at the paragraph's pitch."""
        f = self.frame
        groups = self._lines_by_source()
        out, k, gi, pending = [], 0, 0, None
        while gi < len(groups) or pending is not None:
            if pending is not None:
                if gi < len(groups) and not f.hard_breaks:
                    a, b = pending[0], groups[gi][2]
                    gi += 1
                else:
                    a, b = pending
                pending = None
            else:
                _, a, b = groups[gi]
                gi += 1
            while a < b and self.chars[a].kind in ('gap', 'join'):
                a += 1
            if a >= b:
                continue
            same = self._unchanged(a, b)
            if same is not None:
                # A source line the edits left whole keeps its glyphs' positions (it may only move down).
                baseline = f.baselines[k] if k < len(f.baselines) else f.baselines[-1] + f.pitch * (k - len(f.baselines) + 1)
                xs, x = [], None
                for i in range(a, b):
                    c = self.chars[i]
                    x = c.src.origin[0] if c.src is not None else x + self.chars[i - 1].adv
                    xs.append(x)
                line = Line(a, b, baseline, xs, k if k < len(self.source_lines) else None, False)
                line.retained = self._retained(line, k)
                out.append(line)
                k += 1
                continue
            x0 = f.first_x if k == 0 else f.left
            # A line that still starts with its source line's first glyph starts where the source drew it.
            first = self.chars[a].src
            if k < len(self.source_lines) and first is not None and first.line == k and first.order == self.source_lines[k]['orders'][0]:
                x0 = first.origin[0]
            limit = f.max_width if f.alignment == 'center' else f.right - x0
            end = b
            if self._width(a, b)[0] > limit + .05:
                fits = [i for i in self._breaks(a, b) if self._width(a, i)[0] <= limit + .05]
                if not fits:
                    raise Refused(f'a line of the paragraph cannot be broken to fit {limit:.1f} pt')
                end = fits[-1]
                pending = (end, b)
            last = pending is None and gi >= len(groups)
            baseline = f.baselines[k] if k < len(f.baselines) else f.baselines[-1] + f.pitch * (k - len(f.baselines) + 1)
            line = Line(a, end, baseline, self._place(a, end, x0, limit, last), k if k < len(self.source_lines) else None, False)
            line.retained = self._retained(line, k)
            out.append(line)
            k += 1
        return out

    def _place(self, a, b, x0, limit, last):
        f = self.frame
        width, vis = self._width(a, b)
        advs = [self._adv(i) for i in range(a, b)]
        if f.alignment == 'center':
            x0 = f.center - width / 2
        elif f.alignment == 'justify' and not last and width < limit - .05:
            # Re-justify: the slack goes to the word gaps (Latin), else between the characters (CJK).
            slots = [i - a for i in range(a, vis - 1) if self._is_gap(self.chars[i])]
            if not slots:
                slots = list(range(0, vis - a - 1))
            if slots:
                extra = (limit - width) / len(slots)
                for s in slots:
                    advs[s] += extra
        xs, x = [], x0
        for v in advs:
            xs.append(x)
            x += v
        return xs

    def _unchanged(self, a, b):
        """The source line whose glyphs [a, b) are, all and only, in source order; else None."""
        glyphs = [c for c in self.chars[a:b] if c.kind == 'glyph']
        if not glyphs or any(c.src is None for c in glyphs):
            return None
        k = glyphs[0].src.line
        if any(c.src.line != k for c in glyphs) or [c.src.order for c in glyphs] != list(self.source_lines[k]['orders']) \
                or self.chars[a].src is None:
            return None
        return k

    def _retained(self, line, k):
        if k >= len(self.source_lines):
            return False
        sl = self.source_lines[k]
        chars = self.chars[line.start:line.end]
        glyphs = [c for c in chars if c.kind == 'glyph']
        if any(c.src is None or c.src.line != k for c in glyphs) or {c.src.order for c in glyphs} != set(sl['orders']):
            return False
        if abs(line.baseline - sl['baseline']) > .01:
            return False
        return all(abs(x - c.src.origin[0]) < .05 for x, c in zip(line.xs, chars) if c.kind == 'glyph')

    def summary(self):
        f = self.frame
        return dict(text=self.text, characters=len(self.chars), from_source=sum(1 for c in self.chars if c.src),
                    styles={k: dict(font=s.basefont, resource=s.font, size=s.size, matrix=[round(v, 3) for v in s.matrix],
                                    fill=list(map(str, s.fill))) for k, s in self.styles.items()},
                    frame=dict(left=round(f.left, 2), first_x=round(f.first_x, 2), right=round(f.right, 2), alignment=f.alignment,
                               pitch=round(f.pitch, 3), hard_breaks=f.hard_breaks, max_width=round(f.max_width, 2),
                               baselines=[round(b, 2) for b in f.baselines]),
                    relations={k: v for k, v in self.relations.items() if k != 'obstacles'} | dict(obstacles=len(self.relations.get('obstacles', []))),
                    source_lines=len(self.source_lines), history=[dict(at=h['at'], old=h['old'], new=h['text']) for h in self.history])
