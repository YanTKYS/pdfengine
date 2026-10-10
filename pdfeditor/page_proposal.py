"""Ordinary-page front door to shared flow v2 (critical-path blocker B1).

A proposal is observation + evidence + a candidate interpretation of one page.
It is never authority. Only `accept_page_flow`, an explicit caller act, turns
it into the existing `confirm_story` / `confirm_shared_flow` arguments, and the
shared-flow v2 validators decide the resulting state. Irregular source
adjacency is an explicit optional paragraph policy, confirmed with the proposal.

Nothing here writes a PDF or a sidecar, adds a sidecar schema, or relaxes a
guard. Scope: one page, horizontal text, one column of non-overlapping
paragraphs; everything else on the page is fixed foreign content.

Evidence rules:

* Available width is never the observed content width. Observed lines bound it
  (every observed line fits; a wrapped line could not take the next break
  unit); a page-geometry candidate (margin symmetry, foreign boundary) is chosen
  only when it lies inside those bounds.
* A font candidate qualifies only by metrics: glyph presence and outline, the
  embedded program's advances (exact rational em) and the PDF /W widths (within
  half of the written decimal quantum). Names only order verified candidates.
* Before a proposal is offered (and again on acceptance) the exact shared-flow
  state is built and planned with no edits; it must reproduce the observed line
  breaks, baselines, widths and every painted glyph origin (`elements._close`,
  the existing shared-flow geometry tolerance).
  A proposal that cannot reproduce the page is refused.
"""
from copy import deepcopy
from fractions import Fraction
from io import BytesIO
import json
import hashlib
from pathlib import Path
import tempfile
import math
import ntpath
import os
import posixpath
import re
import sys
import unicodedata
from statistics import median
from types import SimpleNamespace

import pymupdf
from fontTools.ttLib import TTFont
from uniseg.linebreak import line_break_boundaries

from .attributed import digest, inspect_paragraph
from .backend import PdfError, extract_page
from .content_stream import ContentPage
from .elements import _close
from .inference import _alignment_cost, _line_groups, _paragraphs, _separator, _size
from .layout import _SPACES, _safe_boundary
from .model import Rect
from .selection import make_selection, observation_lines, page_observation_sha, source_sha
from .shaped_font import FontError, ShapedFont
from .shared_flow import SOURCE_PLACEMENT, confirm_shared_flow, plan_shared_flow
from .story_flow import confirm_story
from . import source_ownership

SCHEMA = 'pdfengine-page-flow-proposal-1'
RECEIPT_SCHEMA = 'pdfengine-page-flow-acceptance-1'
FONT_SUFFIXES = ('.ttf', '.ttc', '.otf', '.otc')
OVERRIDE_KEYS = frozenset({'width', 'region_top', 'region_bottom', 'paragraph_starts'})
SUBSET_TAG = re.compile(r'^[A-Z]{6}\+')
SENTENCE_END = ('。', '！', '？', '.', '!', '?')  # the terminal set inference._paragraphs uses


# -- installed font discovery ---------------------------------------------------

def default_font_roots(platform=None, environ=None, home=None):
    """Installed-font directories for this platform, in a fixed order (strings)."""
    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform.startswith('win'):
        windows = environ.get('WINDIR') or environ.get('SystemRoot') or 'C:\\Windows'
        roots = [ntpath.join(windows, 'Fonts')]
        if environ.get('LOCALAPPDATA'):
            roots.append(ntpath.join(environ['LOCALAPPDATA'], 'Microsoft', 'Windows', 'Fonts'))
        return roots
    home = str(home if home is not None else os.path.expanduser('~'))
    if platform == 'darwin':
        return ['/System/Library/Fonts', '/System/Library/Fonts/Supplemental', '/Library/Fonts',
                posixpath.join(home, 'Library', 'Fonts')]
    data_home = environ.get('XDG_DATA_HOME') or posixpath.join(home, '.local', 'share')
    data_dirs = [d for d in (environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share').split(':') if d]
    roots = [posixpath.join(data_home, 'fonts'), posixpath.join(home, '.fonts')]
    roots += [posixpath.join(d, 'fonts') for d in data_dirs]
    return list(dict.fromkeys(roots))


def discover_font_files(roots):
    """Font files under the roots, sorted by path string (never by enumeration order)."""
    found = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for directory, _, names in os.walk(root):
            for name in names:
                if name.lower().endswith(FONT_SUFFIXES):
                    found.add(os.path.join(directory, name))
    return sorted(found)


def _faces(path):
    try:
        with open(path, 'rb') as stream:
            head = stream.read(12)
            size = os.fstat(stream.fileno()).st_size
    except OSError:
        return []
    if head[:4] == b'ttcf' and len(head) == 12:
        count = int.from_bytes(head[8:12], 'big')
        return list(range(count)) if 0 < count <= (size - 12) // 4 else []
    return [0]


def _decimal_quantum(value):
    """Half the last written decimal unit of a PDF width (the producer's rounding bound).

    A /W or /Widths entry is the producer's decimal spelling of an exact advance
    in 1/1000 em. Its shortest round-trip spelling fixes its decimal places d;
    the exact advance then lies within 10**-d / 2 of it. This is a derived
    quantization bound, not a tuning tolerance.
    """
    text = repr(float(value))
    if 'e' in text or 'E' in text:
        raise PdfError('PDF width spelling is outside the decimal comparison scope')
    decimals = text.split('.', 1)[1].rstrip('0') if '.' in text else ''
    return Fraction(text), Fraction(1, 2 * 10 ** len(decimals))


class _FontPool:
    """Opened candidate faces, keyed by (path, index); closed together."""

    def __init__(self):
        self.faces = {}

    def get(self, path, index):
        key = (path, index)
        if key not in self.faces:
            try:
                self.faces[key] = TTFont(path, fontNumber=index, lazy=True)
            except Exception:  # unreadable candidate: not a provider
                self.faces[key] = None
        return self.faces[key]

    def close(self):
        for font in self.faces.values():
            if font is not None:
                font.close()


def _font_names(font):
    names = font['name'] if 'name' in font else None
    ps = names.getDebugName(6) if names else None
    family = names.getDebugName(1) if names else None
    return ps or '', family or ''


def _normalized(name):
    return re.sub(r'[^a-z0-9]', '', SUBSET_TAG.sub('', name or '').lower())


def _verify_face(font, needed):
    """Metric qualification of one face against the observed glyphs of one font program."""
    if font is None or any(t not in font for t in ('glyf', 'hmtx', 'cmap', 'head')):
        return 'not a TrueType outline font with hmtx/cmap'
    flags = font['OS/2'].fsType if 'OS/2' in font else 0
    if flags & 0x202 or flags & 4 and not flags & 8:
        return 'embedding restricted by OS/2 fsType'
    cmap = font.getBestCmap() or {}
    upem = font['head'].unitsPerEm
    hmtx, glyf = font['hmtx'], font['glyf']
    for char in sorted(needed):
        width, embedded = needed[char]
        name = cmap.get(ord(char))
        if name is None:
            return f'missing glyph U+{ord(char):04X}'
        if name not in glyf or (glyf[name].numberOfContours == 0 and unicodedata.category(char) != 'Zs'):
            return f'empty outline for U+{ord(char):04X}'
        advance = Fraction(hmtx[name][0], upem)
        if embedded is not None and advance != embedded:
            return f'advance of U+{ord(char):04X} differs from the embedded program'
        written, quantum = _decimal_quantum(width)
        if abs(written - advance * 1000) > quantum:
            return f'advance of U+{ord(char):04X} differs from the PDF width'
    return None


def _verify_providers(pool, files, needed, source_name):
    """(verified candidates, name-matched rejections, evaluated face count) for one font program."""
    verified, rejected, evaluated = {}, {}, 0
    wanted = _normalized(source_name)
    for path in files:
        for index in _faces(path):
            evaluated += 1
            font = pool.get(path, index)
            try:
                reason = _verify_face(font, needed)
                ps, family = _font_names(font) if font is not None else ('', '')
            except Exception:  # malformed discovered tables are not font authority
                continue
            # Ranking only: a producer may append a style word to the subset BaseFont.
            matched = bool(wanted) and any(n and wanted.startswith(n) for n in (_normalized(ps), _normalized(family)))
            if reason is None:
                try:
                    shaped = ShapedFont(path, font_index=index)
                except (FontError, OSError, ValueError) as exc:
                    reason = 'existing font writer refuses it: ' + str(exc)
                else:
                    key = (shaped.source_sha256, index)
                    entry = verified.get(key)
                    if entry is None or path < entry['path']:
                        verified[key] = dict(sha256=shaped.source_sha256, font_index=index, path=path,
                                             postscript_name=ps, family=family, name_match=matched)
                    shaped.font.close()
                    continue
            if matched:
                rejected.setdefault((path, index), dict(path=path, font_index=index, postscript_name=ps,
                                                       reason=reason))
    ordered = sorted(verified.values(), key=lambda c: (not c['name_match'], c['sha256'], c['font_index']))
    return ordered, sorted(rejected.values(), key=lambda c: (c['path'], c['font_index'])), evaluated


def _embedded_candidates(source, programs, cache):
    """Materialize unchanged source programs in a derived, content-addressed cache.

    Cache bytes are never authority: both the source PDF and the candidate SHA
    are rechecked on acceptance; persistent provider recipes verify them again.
    A cache may be deleted, in which case an existing state needs confirmation.
    """
    result = []
    with pymupdf.open(source) as doc:
        for xref in sorted(programs):
            data = doc.extract_font(xref)[3]
            if not data:
                continue
            sha = hashlib.sha256(data).hexdigest()
            root = Path(cache); root.mkdir(parents=True, exist_ok=True)
            path = root / (sha + '.ttf')
            if not path.exists():
                # Publish complete bytes; concurrent proposals may derive the same program.
                with tempfile.NamedTemporaryFile(dir=root, delete=False) as out:
                    temp = Path(out.name)
                    out.write(data)
                try:
                    os.replace(temp, path)
                finally:
                    temp.unlink(missing_ok=True)
            if source_sha(path) != sha:
                raise PdfError('embedded font cache differs from the bound source program')
            result.append(dict(path=str(path.resolve()), sha256=sha, font_index=0,
                               source_xref=xref, provenance='unmodified-embedded-program'))
    return result


def _coverage_candidates(pool, files, text):
    """Qualify new input using actual shaping, independently of source metrics.

    These are *alternatives*, never automatically accepted or labelled equal
    to the source face. The normal writer enforces embedding rights and outlines.
    """
    candidates, rejected = {}, []
    required = {ord(c) for c in text if c not in '\r\n'}
    for path in files:
        for index in _faces(path):
            font = pool.get(path, index)
            try:
                if font is None or not {'glyf','hmtx','cmap','head'} <= set(font.keys()):
                    continue
                if not required <= set(font.getBestCmap() or {}):
                    continue
            except Exception:
                continue
            shaped = None
            try:
                shaped = ShapedFont(path, font_index=index)
                for run in re.split(r'\r\n|\r|\n', text):
                    shaped.shape(run)
                ps, family = _font_names(font)
                entry = dict(path=path, sha256=shaped.source_sha256, font_index=index,
                    instance_sha256=shaped.instance_sha256, postscript_name=ps, family=family,
                    checked_text=text, qualification='editable embedding + actual HarfBuzz shaping + visible glyphs; no source-metric equivalence claim')
                key = (entry['sha256'], index)
                if key not in candidates or path < candidates[key]['path']:
                    candidates[key] = entry
            except Exception as exc:  # discovered malformed programs are never candidates
                rejected.append(dict(path=path, font_index=index, reason=str(exc)))
            finally:
                if shaped is not None:
                    shaped.font.close()
    return sorted(candidates.values(), key=lambda c: (c['sha256'], c['font_index'])), rejected


# -- page analysis ---------------------------------------------------------------

def _line_record(line, ident):
    glyphs = sorted(line.glyphs, key=lambda g: (g.origin[0], g.source_order))
    # The layout trims trailing spaces before measuring a line; a wrap pulls the next unit after them.
    visible = [g for g in glyphs if g.text not in _SPACES] or glyphs
    return dict(id=ident, text=line.text, baseline=line.baseline,
                x=glyphs[0].origin[0], right=visible[-1].origin[0] + visible[-1].advance,
                pen_end=glyphs[-1].origin[0] + glyphs[-1].advance,
                top=line.bbox.y0, bottom=line.bbox.y1, bbox=list(line.bbox.tuple()),
                glyph_ids=sorted(g.source_order for g in line.glyphs), size=_size(line),
                starts=[g.origin[0] for g in glyphs])


def _joiner(lines):
    found = set()
    for previous, current in zip(lines, lines[1:]):
        if previous['text'][-1:].isspace() or current['text'][:1].isspace():
            found.add('')
        else:
            found.add(_separator(previous['text'], current['text']))
    if len(found) > 1:
        raise PdfError('paragraph lines need different joiners; review the paragraph explicitly')
    return found.pop() if found else ''


def _first_unit_width(line):
    """Width of the first legal line-break unit of a line (what a wider measure would pull up)."""
    text = line['text']
    for boundary in line_break_boundaries(text):
        if 0 < boundary and _safe_boundary(text, 0, boundary):
            stop = boundary
            while stop > 0 and text[stop - 1] in _SPACES:
                stop -= 1
            end = line['right'] if stop >= len(text.rstrip(_SPACES)) else line['starts'][stop]
            return end - line['x']
    return line['right'] - line['x']


def _numbered_column(lines):
    """A narrow, evidence-only candidate for an explicitly selected hanging list.

    Consecutive markers, equal marker/body origins and a witnessed continuation
    are required. This never includes an unselected line. Font qualification,
    structural ownership, foreign-region exclusion and glyph reproduction still
    decide whether the candidate can be offered and accepted.
    """
    marker = re.compile(r'^\(([0-9]+)\) +(?=\S)')
    groups, numbers, body_x = [], [], []
    for line in lines:
        match = marker.match(line['text'])
        if match:
            if len(line['starts']) != len(line['text']):
                return None  # Multi-character glyphs need a separate marker-offset proof.
            numbers.append(int(match[1]))
            body_x.append(line['starts'][match.end()])
            groups.append([line])
        elif groups:
            groups[-1].append(line)
        else:
            return None
    if (len(groups) < 2 or numbers != list(range(numbers[0], numbers[0] + len(numbers)))
            or not any(len(p) > 1 for p in groups)
            or not all(_close(p[0]['x'], groups[0][0]['x']) for p in groups)
            or not all(_close(x, body_x[0]) for x in body_x)
            or body_x[0] <= groups[0][0]['x']
            or not all(_close(l['size'], lines[0]['size']) for l in lines)
            or not all(_close(l['x'], body_x[0]) for p in groups for l in p[1:])
            or not all(a['bottom'] < b['top'] for a, b in zip(lines, lines[1:]))):
        return None
    return groups


def _columns(paragraphs):
    """Greedy top-down chains of size-compatible, left-aligned paragraph candidates."""
    columns = []
    for para in sorted(paragraphs, key=lambda p: (p.lines[0].bbox.y0, p.lines[0].bbox.x0)):
        first = para.lines[0]
        choices = []
        for index, column in enumerate(columns):
            last, head = column[-1].lines[-1], column[0].lines[0]
            size, other = _size(first), _size(last)
            if min(size, other) / max(size, other) < 0.82 or first.baseline <= last.baseline:
                continue
            cost = _alignment_cost(last, first, head)
            if cost is not None:
                choices.append((cost, index))
        if choices:
            columns[min(choices)[1]].append(para)
        else:
            columns.append([para])
    return columns


class _Analysis:
    """Everything a proposal is derived from; recomputed (never trusted) on acceptance."""

    def __init__(self, source, page, line_ids=None):
        self.target_line_ids = None
        self.source, self.page = str(source), page
        self.refusals, self.unresolved = [], []
        with pymupdf.open(source) as doc:
            if doc.needs_pass:
                raise PdfError('encrypted PDF requires decryption before a page proposal')
            if not 1 <= page <= len(doc):
                raise PdfError(f'page must be between 1 and {len(doc)}')
            pdf_page = doc[page - 1]
            self.rotation = pdf_page.rotation
            model = extract_page(doc, page - 1)
        self.width, self.height = model.width, model.height
        self.observation_sha256 = page_observation_sha(model)
        objects = observation_lines(model)
        self.lines = [_line_record(line, f'p{page}-l{i}') for i, line in enumerate(objects, 1)]
        by_id = {id(line): record for line, record in zip(objects, self.lines)}
        if line_ids is not None:
            observed = [line['id'] for line in self.lines]
            if (not isinstance(line_ids, (list, tuple)) or not line_ids
                    or any(not isinstance(i, str) or i not in observed for i in line_ids)
                    or len(set(line_ids)) != len(line_ids)):
                raise PdfError('line_ids must name distinct observed page lines')
            positions = [observed.index(i) for i in line_ids]
            if positions != list(range(positions[0], positions[-1] + 1)):
                raise PdfError('line_ids must be consecutive in observed page order')
            self.target_line_ids = list(line_ids)
            objects = [objects[i] for i in positions]
        self.nonhorizontal = [g for g in model.glyphs
                              if abs(g.direction[0] - 1) > .001 or abs(g.direction[1]) > .001]
        self.obstacles = [dict(kind=o.kind, bounds=list(o.bbox.tuple())) for o in model.obstacles]
        if self.rotation:
            self.refusals.append(dict(code='rotated-page', detail='page display rotation is outside the B1 scope'))
        if not self.lines:
            self.refusals.append(dict(code='no-horizontal-text', detail='no horizontal text lines on this page'))
            self.column = []
            return
        self.numbered_column = (_numbered_column([by_id[id(line)] for line in objects])
                                if self.target_line_ids is not None else None)
        candidates = [para for group in _line_groups(objects) for para in _paragraphs(group)[0]]
        lines_of = {id(p): [by_id[id(line)] for line in p.lines] for p in candidates}
        columns = sorted(([lines_of[id(p)] for p in column] for column in _columns(candidates)),
                         key=lambda c: (c[0][0]['top'], c[0][0]['x']))
        weight = lambda column: sum(len(line['glyph_ids']) for para in column for line in para)
        heaviest = max(weight(c) for c in columns)
        main = [c for c in columns if weight(c) == heaviest]
        if len(main) != 1 and not self.numbered_column:
            self.refusals.append(dict(code='ambiguous-main-column',
                                      detail='several columns hold the same amount of text'))
        self.column = self.numbered_column or main[0]
        self.other_columns = [c for c in columns if c is not main[0]]
        if self.target_line_ids is not None and {l['id'] for p in self.column for l in p} != set(self.target_line_ids):
            self.refusals.append(dict(code='selected-lines-outside-flow',
                detail='every explicitly selected line must belong to the proposed column'))
        top = min(line['top'] for para in self.column for line in para)
        bottom = max(line['bottom'] for para in self.column for line in para)
        for column in ([] if self.numbered_column else self.other_columns):
            a = min(line['top'] for para in column for line in para)
            b = max(line['bottom'] for para in column for line in para)
            if a < bottom and b > top:
                self.refusals.append(dict(code='multiple-columns',
                    detail=f'text beside the main column ({column[0][0]["id"]}) overlaps it vertically'))
                break
        if any(not (bottom <= g.bbox.y0 or g.bbox.y1 <= top) for g in self.nonhorizontal):
            self.refusals.append(dict(code='nonhorizontal-text',
                                      detail='rotated or vertical text lies beside the main column'))

    # Foreign content: every text line outside the accepted paragraphs and every paint obstacle.
    def foreign(self, included_line_ids):
        items = [dict(kind='text', bounds=line['bbox'], text=line['text'], line_id=line['id'])
                 for line in self.lines if line['id'] not in included_line_ids]
        items += [dict(kind='nonhorizontal-text', bounds=list(g.bbox.tuple()), text=g.text)
                  for g in self.nonhorizontal]
        items += [dict(kind=o['kind'], bounds=o['bounds']) for o in self.obstacles]
        return items


# -- proposal construction ------------------------------------------------------------

def _split(column_lines, starts):
    """Re-split the column at explicit paragraph-start line ids (override)."""
    flat = [line for para in column_lines for line in para]
    ids = [line['id'] for line in flat]
    if not starts or starts[0] != ids[0] or len(set(starts)) != len(starts) or any(s not in ids for s in starts) \
            or [s for s in ids if s in starts] != list(starts):
        raise PdfError('paragraph_starts must list column line ids in order, beginning with the first line')
    result = []
    for line in flat:
        if line['id'] in starts:
            result.append([])
        result[-1].append(line)
    return result


def _width(analysis, column, col_x, foreign):
    lines = [line for para in column for line in para]
    lower = max(line['right'] - col_x for line in lines)
    # A break after sentence-final punctuation may be a paragraph end, so it is no wrap evidence.
    uppers = [para[i]['pen_end'] - col_x + _first_unit_width(para[i + 1])
              for para in column for i in range(len(para) - 1)
              if not para[i]['text'].rstrip(_SPACES).endswith(SENTENCE_END)]
    upper = min(uppers) if uppers else None
    top, bottom = min(line['top'] for line in lines), max(line['bottom'] for line in lines)
    right = [f['bounds'][0] for f in foreign
             if f['bounds'][0] >= max(line['right'] for line in lines)
             and f['bounds'][1] < bottom and f['bounds'][3] > top]
    limit = min([analysis.width] + right)
    cap = limit - col_x
    options = []
    if col_x < analysis.width / 2:
        options.append(('margin-symmetry', analysis.width - 2 * col_x))
    if right:
        options.append(('foreign-content-boundary', cap))
    record = dict(lower=lower, upper=upper, cap=cap, candidates=[])
    chosen = None
    for label, value in options:
        fits = lower <= value <= cap and (upper is None or value < upper)
        record['candidates'].append(dict(evidence=label, value=value, consistent=fits))
        if fits and chosen is None:
            chosen = (label, value)
    if chosen is None and upper is not None and lower <= (lower + upper) / 2 <= cap:
        # Only wraps bound the measure: the interval midpoint reproduces them with the most margin.
        chosen = ('wrapped-line-interval-midpoint', (lower + upper) / 2)
    record['evidence_basis'] = ['observed-line-right-edges'] + (['wrapped-line-right-edge'] if uppers else [])
    return chosen, record


def _check_width(width, record):
    if not (record['lower'] <= width <= record['cap'] and (record['upper'] is None or width < record['upper'])):
        raise PdfError('width override must keep every observed line and wrap and stay inside foreign boundaries')
    return width


def _column_x(column):
    non_first = [line['x'] for para in column for line in para[1:]]
    if non_first:
        return min(non_first), 'continuation-line-left-edge'
    return min(para[0]['x'] for para in column), 'paragraph-left-edge'


def _unexplained_breaks(analysis, column, width=None):
    """Split where the measure could not have wrapped: the next break unit would have fitted.

    Inference may join paragraphs whose boundary looks like a wrap. Once page
    geometry and the trusted wraps fix the measure, a line break that leaves
    room for the next unit is a hard break, not a soft wrap.
    """
    included = {line['id'] for para in column for line in para}
    col_x, _ = _column_x(column)
    chosen, _ = _width(analysis, column, col_x, analysis.foreign(included))
    if width is not None:
        chosen = ('caller-override', width)
    if chosen is None:
        return column, []
    result, splits = [], []
    for para in column:
        current = [para[0]]
        for previous, line in zip(para, para[1:]):
            if previous['pen_end'] - col_x + _first_unit_width(line) <= chosen[1]:
                splits.append(line['id'])
                result.append(current)
                current = [line]
            else:
                current.append(line)
        result.append(current)
    return result, splits


def _region(analysis, column, col_x, width, foreign, overrides):
    lines = [line for para in column for line in para]
    top_line, bottom_line = min(line['top'] for line in lines), max(line['bottom'] for line in lines)
    x0 = min([col_x] + [line['bbox'][0] for line in lines])
    x1 = max([col_x + width] + [line['bbox'][2] for line in lines])
    band = lambda f: f['bounds'][0] < x1 and f['bounds'][2] > x0
    above = [f['bounds'][3] for f in foreign if band(f) and f['bounds'][3] <= top_line]
    below = [f['bounds'][1] for f in foreign if band(f) and f['bounds'][1] >= bottom_line]
    top = (max(above), 'foreign-content-boundary') if above else (0.0, 'page-edge')
    content_top = min([line['top'] for line in analysis.lines] + [o['bounds'][1] for o in analysis.obstacles])
    options = [(analysis.height, 'page-edge')]
    if below:
        options.append((min(below), 'foreign-content-boundary'))
    if analysis.height - content_top > bottom_line:
        options.append((analysis.height - content_top, 'margin-symmetry'))
    bottom = min(options)
    if 'region_top' in overrides:
        top = (overrides['region_top'], 'caller-override')
    if 'region_bottom' in overrides:
        bottom = (overrides['region_bottom'], 'caller-override')
    bounds = [x0, top[0], x1, bottom[0]]
    if not (top[0] < top_line and bottom[0] > bottom_line and 0 <= top[0] and bottom[0] <= analysis.height):
        raise PdfError('region bounds must contain every column line inside the page')
    box = Rect(*bounds)
    hits = [f for f in foreign if box.intersects(Rect(*f['bounds']))]
    if hits:
        raise PdfError('the column region collides with foreign content: '
                       + ', '.join(f.get('line_id') or f['kind'] for f in hits[:5]))
    return dict(bounds=bounds, evidence=dict(top=top[1], bottom=bottom[1], left='column-left-edge',
                                             right='available-width'))


def _policies(column, col_x):
    pitches = [b['baseline'] - a['baseline'] for para in column for a, b in zip(para, para[1:])]
    result = []
    for para in column:
        own = [b['baseline'] - a['baseline'] for a, b in zip(para, para[1:])]
        if own:
            pitch, evidence = median(own), 'observed-line-pitch'
        elif pitches:
            pitch, evidence = median(pitches), 'column-line-pitch'
        else:
            pitch, evidence = para[0]['bottom'] - para[0]['top'], 'observed-line-box'
        first = para[0]
        result.append(dict(
            min_line_height=dict(value=pitch, evidence=evidence),
            first_line_indent=dict(value=first['x'] - col_x, evidence='observed-first-line-offset'),
            keep_together=dict(value=False, evidence='single-region-flow'),
            break_before=dict(value='auto', evidence='single-region-flow'),
            break_after=dict(value='auto', evidence='single-region-flow'),
            empty=dict(value=dict(kind='reserve-line', ascent=first['baseline'] - first['top'],
                                  descent=first['bottom'] - first['baseline']), evidence='observed-line-box')))
    return result


def _observed_fonts(source, page, column, snapshots):
    """Per embedded font program: name, needed chars with (PDF width, embedded em advance)."""
    content = ContentPage(source, page)
    programs = {}
    try:
        orders = {g for para in column for line in para for g in line['glyph_ids']}
        gids = {}
        with pymupdf.open(source) as doc:
            for span in doc[page - 1].get_texttrace():
                for c in span['chars']:
                    gids[len(gids)] = c[1]
        for event in content.events:
            for char in event.chars:
                hit = orders.intersection(char.source_orders)
                if not hit:
                    continue
                font = event.state.font
                program = programs.setdefault(font.xref, dict(name=SUBSET_TAG.sub('', font.basefont.lstrip('/')),
                                                              needed={}, embedded=None, gids={}))
                program['gids'][char.text] = gids[min(hit)]
                width = program['needed'].setdefault(char.text, char.pdf_width)
                if width != char.pdf_width:
                    raise PdfError(f'one character has two PDF widths in one font program (U+{ord(char.text):04X})')
        for xref, program in programs.items():
            data = content.document.extract_font(xref)[3]
            embedded = None
            if data:
                try:
                    embedded = TTFont(BytesIO(data))
                except Exception:
                    embedded = None
            needed = {}
            for char, width in program['needed'].items():
                em = None
                if embedded is not None and 'hmtx' in embedded and 'head' in embedded:
                    gid = program['gids'][char]
                    if 0 <= gid < len(embedded.getGlyphOrder()):
                        em = Fraction(embedded['hmtx'][embedded.getGlyphName(gid)][0], embedded['head'].unitsPerEm)
                needed[char] = (width, em)
            program['needed'] = needed
            program['program_sha256'] = hashlib.sha256(data).hexdigest() if data else None
            program['cmap_codepoints'] = set(embedded.getBestCmap() or {}) if embedded is not None and 'cmap' in embedded else set()
            program['embedded_program'] = 'TrueType hmtx' if embedded is not None and 'hmtx' in embedded else None
            if embedded is not None:
                embedded.close()
    finally:
        content.close()
    return programs


def _persistent_route(source, page, column):
    """Read-only source context and tagged-bundle proof for each paragraph."""
    content = ContentPage(source, page)
    try:
        for para in column:
            try:
                events = content.selected_events({g for line in para for g in line['glyph_ids']})
                first = min(events, key=lambda e: e.operator.start)
                source_ownership.initial_context(content, SimpleNamespace(first=first, events=events))
            except PdfError as exc:
                tagged = any(b.marked_content_depth for b in content.boundaries)
                return dict(code='unsupported-for-persistent-flow',
                            detail=('tagged-page: ' if tagged else '') + str(exc), paragraph_first_line=para[0]['id'])
    finally:
        content.close()
    return None


def _build(analysis, *, font_files, font_roots, paragraph_ids=None, overrides=None, provider_choices=None,
           reproduce=True, include_embedded_fonts=False, font_cache=None, new_text=None):
    overrides = dict(overrides or {})
    if set(overrides) - OVERRIDE_KEYS:
        raise PdfError('unsupported override: ' + ', '.join(sorted(set(overrides) - OVERRIDE_KEYS)))
    for key in ('width', 'region_top', 'region_bottom'):
        if key in overrides and (type(overrides[key]) not in (int, float) or not math.isfinite(overrides[key])):
            raise PdfError(f'override {key} must be a finite number')
    if 'paragraph_starts' in overrides and not (isinstance(overrides['paragraph_starts'], list)
                                                and all(isinstance(v, str) for v in overrides['paragraph_starts'])):
        raise PdfError('override paragraph_starts must be a list of line ids')
    source, page = analysis.source, analysis.page
    new_text = dict(new_text or {})
    if any(not isinstance(k, str) or not isinstance(v, str) or not v for k,v in new_text.items()):
        raise PdfError('new_text must map logical style IDs to nonempty proposed Unicode')
    proposal = dict(schema=SCHEMA, status='proposed',
        binding=dict(source_sha256=source_sha(source), page=page, page_observation_sha256=analysis.observation_sha256,
                     page_size=[analysis.width, analysis.height]),
        font_search=dict(font_roots=font_roots, font_candidates=None if font_roots is not None else font_files,
                         include_embedded_fonts=include_embedded_fonts, font_cache=font_cache, new_text=new_text),
        refusals=list(analysis.refusals), unresolved=[], paragraphs=[], region=None, follows=[], styles=[],
        foreign_content=[], reproduction=dict(status='not-run'))
    if analysis.target_line_ids is not None:
        proposal['target'] = dict(line_ids=analysis.target_line_ids, provenance='caller-selected-observed-lines')
    if proposal['refusals']:
        proposal['status'] = 'refused'
        return proposal, None
    column, width_splits = analysis.column, []
    if 'paragraph_starts' in overrides:
        column = _split(column, overrides['paragraph_starts'])
    if 'width' in overrides:
        # Checked against the observed segmentation first, so an override can never re-segment paragraphs.
        _check_width(overrides['width'], _width(analysis, column, _column_x(column)[0],
                                                analysis.foreign({l['id'] for p in column for l in p}))[1])
    if 'paragraph_starts' not in overrides and not analysis.numbered_column:
        column, width_splits = _unexplained_breaks(analysis, column, overrides.get('width'))
    ids = [f'P{i}' for i in range(1, len(column) + 1)]
    if paragraph_ids is not None:
        if not paragraph_ids or any(p not in ids for p in paragraph_ids) or len(set(paragraph_ids)) != len(paragraph_ids):
            raise PdfError('paragraph_ids must name proposed paragraphs')
        positions = sorted(ids.index(p) for p in paragraph_ids)
        if positions != list(range(positions[0], positions[-1] + 1)):
            raise PdfError('accepted paragraphs must be consecutive in the proposed order')
        column, ids = column[positions[0]:positions[-1] + 1], ids[positions[0]:positions[-1] + 1]
    included = {line['id'] for para in column for line in para}
    foreign = analysis.foreign(included)
    proposal['foreign_content'] = [dict(kind=f['kind'], bounds=f['bounds'], **({'text': f['text']} if 'text' in f else {}))
                                   for f in foreign]
    col_x, x_evidence = _column_x(column)
    if any(para[0]['x'] < col_x for para in column) and not analysis.numbered_column:
        proposal['refusals'].append(dict(code='hanging-indent', detail='a first line starts left of the column'))
    route = _persistent_route(source, page, column)
    if route:
        proposal['refusals'].append(route)
    try:
        chosen, width_record = _width(analysis, column, col_x, foreign)
        if 'width' in overrides:
            chosen = ('caller-override', _check_width(overrides['width'], width_record))
        if chosen is None:
            proposal['unresolved'].append(dict(code='available-width-unresolved',
                detail='no wrapped line or page-geometry evidence bounds the width; supply overrides.width'))
        region = (_region(analysis, column, col_x, chosen[1], foreign, overrides) if chosen else None)
    except PdfError as exc:
        proposal['refusals'].append(dict(code='region', detail=str(exc)))
        chosen, width_record, region = None, None, None
    policies = _policies(column, col_x)
    snapshots, paragraphs = [], []
    for pid, para, policy in zip(ids, column, policies):
        glyph_ids = sorted(g for line in para for g in line['glyph_ids'])
        try:
            joiner = _joiner(para)
            selection = make_selection(source, page, glyph_ids=glyph_ids, explicit_width=chosen[1] if chosen else None)
            snapshot = inspect_paragraph(source, selection, line_joiner=joiner)
            if snapshot['text'] != joiner.join(line['text'] for line in para):
                raise PdfError('paragraph Unicode differs from its observed lines')
        except PdfError as exc:
            # The existing attributed-paragraph evidence refuses this paragraph; the proposal says so.
            proposal['refusals'].append(dict(code='paragraph-evidence', detail=f'{pid} ({para[0]["id"]}): {exc}'))
            continue
        snapshots.append(snapshot)
        spacing = snapshot['spacing']
        if (any(line['distribution'] == 'irregular' for line in spacing['lines'])
                and all(line['tc'] == [0.0] and line['tw'] == [0.0] for line in spacing['lines'])):
            policy['text_placement'] = dict(value=SOURCE_PLACEMENT,
                evidence='observed source glyph adjacency; not an inferred tracking or justify policy',
                editing='retain existing codes and same-line adjacent source gaps; shape replacement and new boundaries normally')
        paragraphs.append(dict(id=pid, order=len(paragraphs) + 1, page=page, text=snapshot['text'],
            line_ids=[line['id'] for line in para], glyph_ids=glyph_ids, line_joiner=joiner,
            lines=[dict(id=line['id'], text=line['text'], baseline=line['baseline']) for line in para],
            bounds=[min(l['bbox'][0] for l in para), para[0]['top'], max(l['bbox'][2] for l in para), para[-1]['bottom']],
            first_baseline=para[0]['baseline'], last_baseline=para[-1]['baseline'],
            style_spans=[dict(start=s['start'], end=s['end'], style_id=s['style_id']) for s in snapshot['spans']],
            policy=policy, evidence=('consecutive-numbered-markers-and-aligned-continuations' if analysis.numbered_column else
                                    'break-not-explained-by-width' if para[0]['id'] in width_splits else
                                     'inference line grouping and paragraph segmentation (candidate, not truth)')))
        if 'marked_structure' in snapshot:
            paragraphs[-1]['marked_content'] = snapshot['marked_structure']
    proposal['paragraphs'] = paragraphs
    if len(snapshots) != len(column):
        proposal['status'] = 'refused'
        return proposal, None
    proposal['follows'] = [dict(before=a['id'], after=b['id'],
                                minimum_baseline_gap=b['first_baseline'] - a['last_baseline'],
                                evidence='observed-baseline-gap') for a, b in zip(paragraphs, paragraphs[1:])]
    if any(f['minimum_baseline_gap'] <= 0 for f in proposal['follows']):
        proposal['refusals'].append(dict(code='paragraph-order', detail='paragraphs do not descend on the page'))
    if chosen and region:
        proposal['region'] = dict(id='R1', page=page, bounds=region['bounds'], x=col_x, width=chosen[1],
                                  first_baseline=paragraphs[0]['first_baseline'],
                                  evidence=dict(region['evidence'], x=x_evidence, width=chosen[0],
                                                first_baseline='first-paragraph-baseline'),
                                  width_interval=width_record)
    # Logical styles: identical observed inline attributes and the same embedded font program.
    programs = _observed_fonts(source, page, column, snapshots)
    logical, assignments = {}, []
    for pid, snapshot in zip(ids, snapshots):
        mapping = {}
        for style in snapshot['styles']:
            key = digest(dict({k: style[k] for k in ('font_size', 'horizontal_scale', 'tracking', 'baseline_shift',
                                                       'fill', 'observed_color')},
                              font_name=SUBSET_TAG.sub('', style['font_name']), font_xref=style['font_xref']))
            if key not in logical:
                logical[key] = dict(id=f'style-{len(logical) + 1}', font_xref=style['font_xref'],
                    observed={k: style[k] for k in ('font_name', 'font_size', 'horizontal_scale', 'tracking',
                                                    'baseline_shift', 'fill', 'observed_color')},
                    members=[])
            logical[key]['members'].append(f'{pid}:{style["id"]}')
            mapping[style['id']] = logical[key]['id']
        assignments.append(mapping)
    if set(new_text) - {e['id'] for e in logical.values()}:
        raise PdfError('new_text names an unknown logical style')
    embedded = _embedded_candidates(source, programs, font_cache) if include_embedded_fonts else []
    proposal['embedded_fonts'] = embedded
    font_files = sorted(set(font_files) | {e['path'] for e in embedded})
    pool, results, coverage = _FontPool(), {}, {}
    try:
        for xref, program in sorted(programs.items()):
            results[xref] = _verify_providers(pool, font_files, program['needed'], program['name'])
        for ident, text in new_text.items():
            coverage[ident] = _coverage_candidates(pool, font_files, text)
    finally:
        pool.close()
    choices = dict(provider_choices or {})
    if set(choices) - {s['id'] for s in logical.values()}:
        raise PdfError('provider_choices name an unknown logical style')
    providers = {}
    for entry in logical.values():
        verified, rejected, evaluated = results[entry['font_xref']]
        program = programs[entry['font_xref']]
        outcome = ('unique-metric-verified' if len(verified) == 1 else
                   'ambiguous-metric-verified' if verified else 'no-metric-verified-provider')
        style = dict(id=entry['id'], observed=entry['observed'], physical_styles=entry['members'],
                     source_font=dict(name=program['name'], embedded_program=program['embedded_program'],
                                      checked_characters=len(program['needed']), program_sha256=program['program_sha256'],
                                      cmap_coverage=len(program['cmap_codepoints'])),
                     providers=dict(outcome=outcome, verified=verified, name_matched_rejections=rejected,
                                    evaluated_faces=evaluated,
                                    qualification='glyph presence/outline + embedded em advance (exact) + PDF width '
                                                  '(written decimal quantum); names only order candidates'))
        ident = entry['id']
        alternatives, coverage_rejections = coverage.get(ident, ([], []))
        eligible = verified
        if ident in new_text:
            identities = {(c['sha256'], c['font_index']) for c in alternatives}
            # Equal advances are not proof of equal outlines. For new-text
            # discovery only the identical source program may be auto-selected;
            # a full/other program still retains its exact metric result, but
            # needs explicit approval to supply new glyphs.
            eligible = [c for c in verified if (c['sha256'], c['font_index']) in identities
                        and c['sha256'] == program['program_sha256']]
            metric_ids = {(c['sha256'], c['font_index']) for c in verified}
            alternatives = [dict(c, source_metric_qualified=(c['sha256'],c['font_index']) in metric_ids,
                                 source_program_identical=False)
                            for c in alternatives if c['sha256'] != program['program_sha256']]
            style['source_font']['missing_new_characters'] = sorted(
                {c for c in new_text[ident] if c not in '\r\n' and ord(c) not in program['cmap_codepoints']})
            style['providers'].update(new_text=new_text[ident], covering_metric_providers=eligible,
                substitutes=alternatives, coverage_rejections=coverage_rejections,
                substitute_authority='explicit caller choice; only new glyphs; source appearance not claimed')
        picked = None
        if ident in choices:
            choice = choices[ident]
            if (not isinstance(choice, dict) or set(choice) - {'sha256','font_index','relation'}
                    or type(choice.get('font_index',0)) is not int
                    or choice.get('relation', 'confirmed_reflow_provider') not in ('confirmed_reflow_provider','substituted')):
                raise PdfError('provider choice needs a SHA, face index and supported relationship')
            substitute = choice.get('relation') == 'substituted'
            pool_choices = alternatives if substitute else eligible
            picked = next((c for c in pool_choices if c['sha256'] == choice.get('sha256')
                           and c['font_index'] == choice.get('font_index', 0)), None)
            if picked is None:
                raise PdfError(f'provider choice for {ident} is not a ' +
                               ('qualified substitute' if substitute else 'metric-verified candidate') + ' for the declared text')
            if substitute:
                # Every paragraph using this logical style must preserve its source glyphs.
                # A provider choice never authorizes regenerating the old text in another face.
                for mapping, policy, snapshot in zip(assignments, policies, snapshots):
                    if ident not in mapping.values():
                        continue
                    if not all(l['tc'] == [0.0] and l['tw'] == [0.0] for l in snapshot['spacing']['lines']):
                        raise PdfError('substitution requires witnessed zero-Tc/Tw source adjacency')
                    policy['text_placement'] = dict(value=SOURCE_PLACEMENT,
                        evidence='explicit new-glyph substitution; retain witnessed source glyphs')
            providers[ident] = (picked, 'caller-substitution' if substitute else 'caller-choice')
        elif any(c['sha256'] == program['program_sha256'] for c in eligible):
            original = next(c for c in eligible if c['sha256'] == program['program_sha256'])
            providers[ident] = (original, 'source-program-verified')
            style['providers']['preferred_source_program'] = dict(sha256=original['sha256'],font_index=original['font_index'])
        elif len(eligible) == 1:
            providers[ident] = (eligible[0], 'unique-metric-verified')
        else:
            code = ('explicit-substitution-required' if not eligible and alternatives else
                    'ambiguous-metric-verified' if eligible else
                    'no-provider-for-new-text' if ident in new_text else outcome)
            proposal['unresolved'].append(dict(code=code, style=ident,
                detail='choose a qualified provider explicitly; substitution must name relation=substituted'))
        proposal['styles'].append(style)
    if proposal['refusals']:
        proposal['status'] = 'refused'
        return proposal, None
    if proposal['unresolved']:
        proposal['status'] = 'needs-choice' if all(u['code'] in ('ambiguous-metric-verified', 'explicit-substitution-required')
                                                   for u in proposal['unresolved']) else 'unresolved'
        proposal['reproduction'] = dict(status='pending', detail='needs a width and one provider per style')
        return proposal, None
    plan = dict(column=column, ids=ids, snapshots=snapshots, assignments=assignments, providers=providers,
                logical={e['id']: e for e in logical.values()}, policies=policies, paragraphs=paragraphs,
                region=proposal['region'], follows=proposal['follows'], foreign=foreign)
    if reproduce:
        try:
            state = _confirm(analysis, plan)
        except PdfError as exc:
            # The unchanged shared-flow validators refuse the candidate interpretation.
            proposal['refusals'].append(dict(code='shared-flow-confirmation', detail=str(exc)))
            proposal['status'] = 'refused'
            return proposal, None
        proposal['reproduction'] = _reproduce(source, state, plan)
        if proposal['reproduction']['status'] != 'reproduced':
            proposal['refusals'].append(dict(code='layout-not-reproduced', detail=proposal['reproduction']['detail']))
            proposal['status'] = 'refused'
            return proposal, None
        plan['state'] = state
    return proposal, plan


def _protected(analysis, foreign):
    page = Rect(0, 0, analysis.width, analysis.height)
    result = []
    for f in foreign:
        box = Rect(*f['bounds'])
        box = Rect(max(box.x0, page.x0), max(box.y0, page.y0), min(box.x1, page.x1), min(box.y1, page.y1))
        if box.width > 0 and box.height > 0:
            result.append(dict(bounds=list(box.tuple()), role='fixed'))
    return {str(analysis.page): result}


def _arguments(analysis, plan):
    """The exact `confirm_story` / `confirm_shared_flow` arguments an expert caller would write."""
    region = plan['region']
    protected = _protected(analysis, plan['foreign'])
    stories = {}
    for pid, para, snapshot, mapping, policy in zip(plan['ids'], plan['column'], plan['snapshots'],
                                                    plan['assignments'], plan['policies']):
        used = sorted(set(mapping.values()))
        styles = {}
        for lid in used:
            candidate = plan['providers'][lid][0]
            provider = dict(path=candidate['path'])
            if candidate['font_index']:
                provider['font_index'] = candidate['font_index']
            how = plan['providers'][lid][1]
            styles[lid] = dict(provider=provider,
                provider_relation='substituted' if how == 'caller-substitution' else 'confirmed_reflow_provider')
            if how == 'caller-substitution':
                styles[lid]['provider_selection'] = dict(provenance='explicit-new-glyph-substitution',
                    sha256=candidate['sha256'], font_index=candidate['font_index'],
                    instance_sha256=candidate['instance_sha256'], checked_text=candidate['checked_text'],
                    qualification=candidate['qualification'], appearance='source-equivalence-not-proven')
        first_style = next(s['style_id'] for s in snapshot['spans'])
        stories[pid] = dict(
            containers={'part': dict(page=analysis.page, bounds=region['bounds'], paragraph=snapshot, paint_relations=[],
                layout=dict(x=region['x'], baseline=para[0]['baseline'], width=region['width'],
                            max_bottom=region['bounds'][3], min_line_height=policy['min_line_height']['value'],
                            # The story container contract fixes indent 0; the shared-flow paragraph
                            # policy carries the observed first-line indent.
                            first_line_indent=0))},
            paragraph_id=pid, chain=['part'], protected_regions=protected, styles=styles,
            style_assignments={'part': dict(mapping)}, typing_style_id=mapping[first_style])
    shared = dict(flow_id=f'page{analysis.page}-flow', paragraph_order=list(plan['ids']),
        regions={'R1': dict(page=analysis.page, bounds=region['bounds'], x=region['x'], width=region['width'],
                            first_baseline=region['first_baseline'])},
        region_order=['R1'], slot_regions={pid: {'part': 'R1'} for pid in plan['ids']},
        paragraph_policies={pid: {k: policy[k]['value'] for k in ('min_line_height', 'first_line_indent', 'keep_together',
                                                                  'break_before', 'break_after', 'empty', 'text_placement')
                                  if k in policy}
                            for pid, policy in zip(plan['ids'], plan['policies'])},
        follows=[dict(before=f['before'], after=f['after'], minimum_baseline_gap=f['minimum_baseline_gap'],
                      region_start='reset-to-region-baseline') for f in plan['follows']],
        protected_regions=protected)
    return stories, shared


def _confirm(analysis, plan):
    stories, shared = _arguments(analysis, plan)
    confirmed = {pid: confirm_story(analysis.source, args.pop('containers'), **args)
                 for pid, args in deepcopy(stories).items()}
    return confirm_shared_flow(analysis.source, confirmed, **shared)


def _reproduce(source, state, plan):
    """No-op T2 must reproduce line allocation and each source glyph origin."""
    try:
        result = plan_shared_flow(source, state, {})
    except PdfError as exc:
        return dict(status='not-reproduced', detail='shared-flow planning refuses the unedited page: ' + str(exc))
    by_paragraph = {s['paragraph_id']: sid for sid, s in state['slots'].items()}
    for pid, para in zip(plan['ids'], plan['column']):
        fragment = result['fragments'][by_paragraph[pid]]
        text = state['paragraphs'][pid]['logical']['text']
        joiner = len(text) - sum(len(line['text']) for line in para)
        step = joiner // max(1, len(para) - 1) if len(para) > 1 else 0
        starts, cursor = [], 0
        for line in para:
            starts.append(cursor)
            cursor += len(line['text']) + step
        planned = fragment['lines']
        if [line['start'] for line in planned] != starts:
            return dict(status='not-reproduced', detail=f'{pid}: planned line breaks {[l["start"] for l in planned]} '
                                                      f'differ from observed {starts}')
        if not _close([line['baseline'] for line in planned], [line['baseline'] for line in para]):
            return dict(status='not-reproduced', detail=f'{pid}: planned baselines differ from observed baselines')
        # Left-aligned natural widths must equal the observed line extents; a justified or
        # spaced source would otherwise be re-rendered with different glyph positions.
        if not _close([line['width'] for line in planned], [line['right'] - line['x'] for line in para]):
            return dict(status='not-reproduced', detail=f'{pid}: planned line widths differ from observed line widths '
                                                      '(justified or spaced source text)')
        # Equal line extents alone do not prove reproduction: opposite gap
        # errors can cancel. Compare every painted Unicode occurrence using
        # its source offset, never a nearest-position or text search match.
        from .logical_element import paragraph_from_snapshot
        snapshot = state['slots'][by_paragraph[pid]]['binding']['paragraph']
        paragraph = paragraph_from_snapshot(source, snapshot)
        try:
            painted = set()
            for glyph in fragment['glyphs']:
                start, end = glyph['start'], glyph['end']
                unit = paragraph.units[start]
                if (end != start + 1 or unit.observation is None or unit.text != glyph['text']
                        or not _close(glyph['origin'], unit.observation['origin'])):
                    return dict(status='not-reproduced', detail=f'{pid}: planned glyph position differs at Unicode {start}')
                painted.add(start)
            if any(i not in painted and unit.text not in _SPACES + '\r\n'
                   for i, unit in enumerate(paragraph.units)):
                return dict(status='not-reproduced', detail=f'{pid}: no-edit layout drops a painted character')
        finally:
            paragraph.close()
    return dict(status='reproduced', method='plan_shared_flow with no edits; line starts exact, baselines and line '
                                            'widths and each painted glyph origin within the shared-flow geometry tolerance (elements._close)',
                lines=sum(len(p) for p in plan['column']))


def _font_files(font_candidates, font_roots):
    if font_candidates is not None and font_roots is not None:
        raise PdfError('pass font_candidates or font_roots, not both')
    if font_candidates is not None:
        return sorted({str(p) for p in font_candidates}), None
    # Root order carries no meaning; the recorded set is canonical so the proposal digest does not depend on it.
    roots = sorted({str(r) for r in (font_roots if font_roots is not None else default_font_roots())})
    return discover_font_files(roots), roots


def _seal(proposal):
    value = deepcopy(proposal)
    value.pop('digest', None)
    value = json.loads(json.dumps(value))
    value['digest'] = digest(value)
    return value


def propose_page_flow(source, page=1, *, font_candidates=None, font_roots=None, line_ids=None,
                      include_embedded_fonts=None, font_cache=None, new_text=None):
    """Observe the unchanged PDF and propose an evidence-labelled shared flow.

    `font_candidates` (explicit files) or `font_roots` (directories) inject the
    candidate set; by default the platform's installed-font roots are searched.
    `line_ids` optionally selects consecutive observed lines as the candidate
    region. Every other line and paint remains fixed foreign content; width,
    fonts, source ownership and unchanged-layout reproduction still qualify it.
    Default discovery includes unchanged embedded programs in a derived cache;
    explicit candidate/root inventories retain their scope unless include_embedded_fonts=True.
    new_text maps observed logical style IDs to proposed replacement text. A
    different source program requires provider_choices with relation='substituted' at
    acceptance; the choice only supplies new glyphs and preserves source glyphs.
    """
    if include_embedded_fonts is None:
        include_embedded_fonts = font_candidates is None and font_roots is None
    if type(include_embedded_fonts) is not bool:
        raise PdfError('include_embedded_fonts must be a boolean')
    font_cache = str(Path(font_cache or (Path(tempfile.gettempdir()) / 'pdfengine-font-cache')).resolve()) if include_embedded_fonts else None
    files, roots = _font_files(font_candidates, font_roots)
    analysis = _Analysis(source, page, line_ids=line_ids)
    proposal, _ = _build(analysis, font_files=files, font_roots=roots,
                         include_embedded_fonts=include_embedded_fonts, font_cache=font_cache, new_text=new_text)
    return _seal(proposal)


def _recompute(source, proposal):
    if not isinstance(proposal, dict) or proposal.get('schema') != SCHEMA:
        raise PdfError('not a page-flow proposal')
    claimed = proposal.get('digest')
    if claimed != digest({k: v for k, v in proposal.items() if k != 'digest'}):
        raise PdfError('page-flow proposal was modified; express changes as overrides')
    binding = proposal['binding']
    if source_sha(source) != binding['source_sha256']:
        raise PdfError('page-flow proposal belongs to another PDF revision')
    search = proposal['font_search']
    if any(k not in search for k in ('include_embedded_fonts','font_cache','new_text')):
        raise PdfError('page-flow proposal predates font discovery; propose again')
    files, roots = _font_files(search['font_candidates'], search['font_roots'])
    target = proposal.get('target')
    if target is not None and (not isinstance(target, dict) or set(target) != {'line_ids', 'provenance'}
                               or target['provenance'] != 'caller-selected-observed-lines'):
        raise PdfError('invalid page-flow target')
    analysis = _Analysis(source, binding['page'], line_ids=target['line_ids'] if target else None)
    if analysis.observation_sha256 != binding['page_observation_sha256']:
        raise PdfError('page observation changed since the proposal')
    fresh, _ = _build(analysis, font_files=files, font_roots=roots,
                      **{k:search[k] for k in ('include_embedded_fonts','font_cache','new_text')})
    if _seal(fresh)['digest'] != claimed:
        raise PdfError('page-flow proposal is stale: the page or the installed fonts changed; propose again')
    return analysis, files, roots


def accept_page_flow_report(source, proposal, *, paragraph_ids=None, provider_choices=None, overrides=None):
    """The single explicit acceptance; returns the shared-flow v2 state and an acceptance receipt."""
    analysis, files, roots = _recompute(source, proposal)
    built, plan = _build(analysis, font_files=files, font_roots=roots, paragraph_ids=paragraph_ids,
                         overrides=overrides, provider_choices=provider_choices,
                         **{k:proposal['font_search'][k] for k in ('include_embedded_fonts','font_cache','new_text')})
    if built['status'] != 'proposed' or plan is None:
        reasons = built['refusals'] + built['unresolved']
        raise PdfError('page-flow proposal cannot be accepted: ' + '; '.join(
            f"{r['code']}: {r.get('detail', r.get('style', ''))}" for r in reasons))
    state = plan['state']
    receipt = dict(schema=RECEIPT_SCHEMA, proposal_digest=proposal['digest'],
        source_sha256=proposal['binding']['source_sha256'], page=proposal['binding']['page'],
        accepted_paragraphs=list(plan['ids']), state_schema=state['schema'], state_model_sha256=state['model_sha256'],
        providers={lid: dict(sha256=c['sha256'], font_index=c['font_index'], path=c['path'], provenance=how)
                   for lid, (c, how) in sorted(plan['providers'].items())},
        overrides={k: dict(value=v, provenance='caller-override') for k, v in sorted((overrides or {}).items())},
        paragraph_selection='caller-selected' if paragraph_ids is not None else 'all-proposed',
        reproduction=built['reproduction'])
    return dict(state=state, receipt=receipt)


def accept_page_flow(source, proposal, *, paragraph_ids=None, provider_choices=None, overrides=None):
    """Explicitly accept a proposal; returns the unchanged `pdfengine-shared-flow-2` state."""
    return accept_page_flow_report(source, proposal, paragraph_ids=paragraph_ids,
                                   provider_choices=provider_choices, overrides=overrides)['state']


def replace_in_flow(state, find, replacement, *, paragraph_id=None, style_id=None):
    """`edit_shared_flow` changes replacing the unique occurrence of `find`; writes nothing."""
    if not isinstance(find, str) or not find or not isinstance(replacement, str):
        raise PdfError('find must be nonempty text and replacement must be text')
    paragraphs = state['paragraphs']
    if paragraph_id is not None and paragraph_id not in paragraphs:
        raise PdfError('unknown paragraph_id')
    matches = []
    for pid in state['flow']['paragraphs']:
        if paragraph_id is not None and pid != paragraph_id:
            continue
        text = paragraphs[pid]['logical']['text']
        start = text.find(find)
        while start != -1:
            matches.append((pid, start))
            start = text.find(find, start + 1)
    if not matches:
        raise PdfError('find text does not occur in the accepted flow')
    if len(matches) > 1:
        raise PdfError(f'find text occurs {len(matches)} times; name paragraph_id or a longer find text')
    pid, start = matches[0]
    end = start + len(find)
    logical = paragraphs[pid]['logical']
    covered = {s['style_id'] for s in logical['style_spans'] if s['start'] < end and s['end'] > start}
    if style_id is None:
        if len(covered) != 1:
            raise PdfError('the replaced text crosses logical styles; pass style_id explicitly')
        style_id = covered.pop()
    elif style_id not in paragraphs[pid]['style_registry']:
        raise PdfError('unknown style_id for this paragraph')
    return {pid: dict(edits=[dict(start=start, end=end, text=replacement, style_id=style_id)])}
