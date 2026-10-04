"""Pure narrow measurement/verification candidates, never a runtime replacement.

No PDF writer, sidecar schema or actual binding verifier. Operator arithmetic
accepts only the small explicit ASCII grammar below and externally witnessed
decimal widths. Measurement consumes preconfirmed current logical records.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction as F
import json
import re
import sys

from pdfeditor.continuation import _compose

I = tuple(map(F, (1, 0, 0, 1, 0, 0)))
NUM = r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)'
TOKEN = re.compile(r'\s+|%[^\r\n]*|\([^()\\]*\)|/[A-Za-z][A-Za-z0-9]*|\[|\]|' + NUM + r'|[A-Za-z]+')


def rational(value):
    if not isinstance(value, str):
        raise ValueError('canonical numbers must be rational strings')
    n = F(value)
    if abs(n) > 10**9:
        raise ValueError('numeric scope exceeded')
    return n


def exact_source(program, widths, *, page_height='260'):
    """Decimal operator witness experiment; no pypdf float roundtrip.

    No escaped/nested strings, hex strings, Forms, font switching ambiguity,
    rotation/shear or implicit widths. Unknown syntax/operators are refused.
    Width strings are a separate lexical font-dictionary witness obligation.
    """
    if any(ord(c)>127 for c in program): raise ValueError('SOURCE_GRAMMAR_UNSUPPORTED')
    tokens, cursor = [], 0
    for match in TOKEN.finditer(program):
        if match.start() != cursor:
            raise ValueError('SOURCE_GRAMMAR_UNSUPPORTED')
        delimiters=' \t\r\n\f\x00()<>[]{}/%'
        if cursor and program[cursor-1] not in delimiters and match.group()[0] not in delimiters:
            raise ValueError('SOURCE_GRAMMAR_UNSUPPORTED')
        cursor = match.end()
        token = match.group()
        if not token.isspace() and not token.startswith('%'):
            tokens.append(token)
    if cursor != len(program):
        raise ValueError('SOURCE_GRAMMAR_UNSUPPORTED')
    state = dict(ctm=I, Tf=F(0), Tc=F(0), Tw=F(0), Tz=F(100), Ts=F(0), font=None)
    tm = lm = I
    stack, args, rows, edges = [], [], [], []
    in_text, array, pending, font_alias = False, None, None, None
    def numbers(count):
        if len(args) != count or any(not re.fullmatch(NUM, a) for a in args):
            raise ValueError('SOURCE_OPERANDS_UNSUPPORTED')
        return list(map(F, args))
    def show(text):
        nonlocal tm, pending
        if not in_text or state['Tf'] <= 0 or state['Tz'] <= 0 or not state['font']:
            raise ValueError('SOURCE_TEXT_STATE_UNPROVEN')
        for char in text:
            if not 32 <= ord(char) <= 126 or char not in widths:
                raise ValueError('SOURCE_GLYPH_UNPROVEN')
            m = _compose(tm, state['ctm'])
            if m[1] or m[2] or m[0] <= 0 or m[3] <= 0:
                raise ValueError('SOURCE_MATRIX_UNSUPPORTED')
            width = rational(widths[char])
            if width <= 0:
                raise ValueError('SOURCE_WIDTH_UNSUPPORTED')
            h = m[0] * state['Tz'] / 100
            step = (width / 1000 * state['Tf'] + state['Tc'] +
                    (state['Tw'] if char == ' ' else 0)) * state['Tz'] / 100
            if step <= 0: raise ValueError('SOURCE_NONPOSITIVE_ADVANCE')
            x, y = m[4], F(page_height) - m[5] - state['Ts'] * m[3]
            if pending and rows:
                previous = rows[-1]
                edges.append(dict(left=len(rows)-1, right=len(rows), operator=pending,
                    delta=str(x-F(previous['origin'][0])-F(previous['advance'])),
                    delta_y=str(y-F(previous['origin'][1])),
                    semantics='SOURCE_POSITIONING_INTENT_UNRESOLVED'))
            row = dict(text=char, offset=len(rows), origin=[str(x), str(y)], advance=str(step*m[0]),
                style=dict(font_size=str(state['Tf']*m[3]), horizontal_scale=str(h/m[3]),
                    rise=str(-state['Ts']*m[3]), tracking=str(state['Tc']*h),
                    word_spacing=str(state['Tw']*h)),
                operator_state={k: str(v) for k, v in state.items() if k not in ('ctm',)},
                source_ctm=list(map(str, state['ctm'])), width=widths[char])
            rows.append(row)
            tm = _compose((F(1), F(0), F(0), F(1), step, F(0)), tm)
            pending = None
    for token in tokens:
        if token == '[':
            if array is not None or args:
                raise ValueError('SOURCE_ARRAY_UNSUPPORTED')
            array = []
        elif token == ']':
            if array is None:
                raise ValueError('SOURCE_ARRAY_UNSUPPORTED')
            args = [array]
            array = None
        elif array is not None:
            if not (token.startswith('(') or re.fullmatch(NUM, token)):
                raise ValueError('SOURCE_ARRAY_UNSUPPORTED')
            array.append(token)
        elif token.startswith(('(', '/')) or re.fullmatch(NUM, token):
            args.append(token)
        else:
            if token == 'q':
                numbers(0); stack.append(deepcopy(state))
            elif token == 'Q':
                numbers(0)
                if not stack: raise ValueError('SOURCE_Q_UNBALANCED')
                state = stack.pop()
            elif token == 'cm':
                state['ctm'] = _compose(numbers(6), state['ctm'])
            elif token == 'BT':
                numbers(0)
                if in_text: raise ValueError('SOURCE_TEXT_NESTED')
                in_text = True; tm = lm = I
            elif token == 'ET':
                numbers(0)
                if not in_text: raise ValueError('SOURCE_TEXT_UNBALANCED')
                in_text = False
            elif token == 'Tf':
                if len(args) != 2 or not isinstance(args[0], str) or not args[0].startswith('/') or not re.fullmatch(NUM, args[1]):
                    raise ValueError('SOURCE_FONT_UNSUPPORTED')
                if font_alias is not None and font_alias != args[0]:
                    raise ValueError('SOURCE_FONT_SWITCH_UNPROVEN')
                font_alias = args[0]
                state['font'], state['Tf'] = args[0], F(args[1])
            elif token in ('Tc', 'Tw', 'Tz', 'Ts'):
                state[token] = numbers(1)[0]
            elif token in ('Tm', 'Td'):
                tm = lm = tuple(numbers(6)) if token == 'Tm' else _compose((F(1), F(0), F(0), F(1), *numbers(2)), lm)
                pending = token if rows else None
            elif token == 'Tj':
                if len(args) != 1 or not isinstance(args[0], str) or not args[0].startswith('('):
                    raise ValueError('SOURCE_STRING_UNSUPPORTED')
                show(args[0][1:-1])
            elif token == 'TJ':
                if len(args) != 1 or not isinstance(args[0], list):
                    raise ValueError('SOURCE_ARRAY_UNSUPPORTED')
                for atom in args[0]:
                    if atom.startswith('('):
                        show(atom[1:-1])
                    else:
                        delta = -F(atom)/1000*state['Tf']*state['Tz']/100
                        tm = _compose((F(1), F(0), F(0), F(1), delta, F(0)), tm)
                        pending = 'TJ'
            else:
                raise ValueError('SOURCE_OPERATOR_UNSUPPORTED:' + token)
            args = []
    if args or array is not None or stack or in_text:
        raise ValueError('SOURCE_UNBALANCED')
    return dict(glyphs=rows, positioning_edges=edges)


def bounds_policy(kind, *, asset_bound=False, afm_pinned=False):
    if kind == 'embedded_outline': return 'outline+hmtx+hhea; code/width binding required'
    if kind == 'supplied_asset' and asset_bound: return 'asset outline+hmtx+hhea; current subset witness required'
    if kind == 'base14' and afm_pinned: return 'AFM experiment only; actual renderer font binding unresolved'
    raise ValueError('FONT_METRIC_AUTHORITY_UNPROVEN')


def measurement(g, style, *, terminal=False, edge='0'):
    """One provider-independent tuple. Metrics are font units, styles page-space."""
    if not {'text','font_identity','glyph_identity','upem','width','ascent','descent','x_offset','y_offset','ink'} <= g.keys():
        raise ValueError('FONT_METRIC_OR_INK_MISSING')
    if not {'font_size','horizontal_scale','rise','tracking','word_spacing','spacing_intent'} <= style.keys():
        raise ValueError('SEMANTIC_STYLE_MISSING')
    if len(g['text']) != 1 or not 32 <= ord(g['text']) <= 126:
        raise ValueError('GLYPH_SCOPE_UNSUPPORTED')
    if not g.get('font_identity') or not g.get('glyph_identity'):
        raise ValueError('GLYPH_IDENTITY_MISSING')
    values = [rational(g[k]) for k in ('upem', 'width', 'ascent', 'descent')]
    upem, width, asc, desc = values
    size, scale, rise, tracking, word = [rational(style[k]) for k in
        ('font_size', 'horizontal_scale', 'rise', 'tracking', 'word_spacing')]
    if min(upem, width, size, scale) <= 0 or min(asc, desc) < 0 or asc+desc <= 0:
        raise ValueError('NONPOSITIVE_METRIC')
    if style.get('spacing_intent') != 'confirmed-inline':
        raise ValueError('SPACING_INTENT_UNRESOLVED')
    style = dict(zip(('font_size','horizontal_scale','rise','tracking','word_spacing'),
                     map(str,(size,scale,rise,tracking,word))),spacing_intent='confirmed-inline')
    if g.get('x_offset') != '0' or g.get('y_offset') != '0':
        raise ValueError('SHAPING_OFFSET_UNSUPPORTED')
    sx, sy = size*scale/upem, size/upem
    bounds = g.get('ink')
    if bounds is None:
        if g['text'] != ' ' or not g.get('empty_outline_verified'):
            raise ValueError('INK_AUTHORITY_MISSING')
        ink = None
    else:
        if len(bounds) != 4: raise ValueError('INK_INVALID')
        a, b, c, d = map(rational, bounds)
        if a > c or b > d: raise ValueError('INK_INVALID')
        ink = [a*sx, -d*sy+rise, c*sx, -b*sy+rise]
    advance = width*sx + (word if g['text'] == ' ' else 0) + (0 if terminal else tracking+F(edge))
    if advance <= 0: raise ValueError('NONPOSITIVE_ADVANCE')
    return dict(text=g['text'], font_identity=g['font_identity'], glyph_identity=g['glyph_identity'],
        advance=str(advance), x_offset='0', y_offset=str(rise), ink=list(map(str, ink)) if ink else None,
        ascent=str(max(F(0), asc*sy-rise)), descent=str(max(F(0), desc*sy+rise)), style=deepcopy(style))


def resolve_edge(edge, *, confirmed=False):
    if confirmed is not True:
        raise ValueError('SOURCE_POSITIONING_INTENT_UNRESOLVED')
    if edge['right'] != edge['left']+1:
        raise ValueError('NONADJACENT_POSITIONING_EDGE')
    if F(edge.get('delta_y','0')): raise ValueError('SOURCE_POSITIONING_INTENT_UNRESOLVED')
    return dict(edge, semantics='confirmed-adjacent-pair', boundary_policy='suppress-at-line-end')


def edit_record(record, start, end, inserted):
    r = deepcopy(record)
    if not 0 <= start <= end <= len(r['glyphs']): raise ValueError('INVALID_EDIT')
    shift = len(inserted)-(end-start)
    r['glyphs'][start:end] = deepcopy(inserted)
    edges = []
    for edge in r.get('edges', []):
        # Delete either endpoint, or insert between endpoints: remove the edge.
        if start <= edge['right'] and end > edge['left'] or start == end == edge['right']:
            continue
        if edge['left'] >= end:
            edge['left'] += shift; edge['right'] += shift
        edges.append(edge)
    r['edges'] = edges
    return r


def layout(record):
    """Exact ASCII/explicit-newline layout including ink and vertical metrics.

    Not Unicode rich_layout or a writer. Empty lines use an explicit metric
    recipe, never nearest trace/style. Logical cells are the current state.
    """
    if record.get('alignment') != 'left': raise ValueError('NON_LEFT_OUT_OF_SCOPE')
    glyphs, style = record['glyphs'], record['style']
    x, baseline, width, leading = map(rational, (record['x'], record['baseline'], record['width'], record['leading']))
    if width <= 0 or leading <= 0: raise ValueError('NONPOSITIVE_REGION')
    empty = [rational(record['empty'][k]) for k in ('ascent', 'descent')]
    if min(empty) < 0 or sum(empty) <= 0: raise ValueError('EMPTY_METRIC_INVALID')
    edges = {}
    for e in record.get('edges', []):
        if e.get('semantics') != 'confirmed-adjacent-pair' or e.get('boundary_policy') != 'suppress-at-line-end':
            raise ValueError('SOURCE_POSITIONING_INTENT_UNRESOLVED')
        if not 0 <= e['left'] < e['right'] < len(glyphs) or e['right'] != e['left']+1 or e['left'] in edges:
            raise ValueError('POSITIONING_EDGE_INVALID')
        if rational(e.get('delta_y', '0')) or any(glyphs[i]['text'] == '\n' for i in (e['left'], e['right'])):
            raise ValueError('SOURCE_POSITIONING_INTENT_UNRESOLVED')
        edges[e['left']] = str(rational(e['delta']))
    # Validate even trailing, nonpainted whitespace cells.
    for g in glyphs:
        if g['text'] != '\n': measurement(g, style)
    def measure(start, end):
        if start == end:
            return [], F(0), F(0), *empty
        tuples = [measurement(glyphs[i], style, terminal=i==end-1, edge=edges.get(i, '0')) for i in range(start, end)]
        pen = left = right = asc = desc = F(0)
        for t in tuples:
            asc, desc = max(asc, F(t['ascent'])), max(desc, F(t['descent']))
            if t['ink'] is not None:
                a,b,c,d = map(F, t['ink'])
                left, right = min(left, pen+a), max(right, pen+c)
                asc, desc = max(asc, -b), max(desc, d)
            pen += F(t['advance']); right = max(right, pen)
        return tuples, right-left, -left, asc, desc
    lines, placed = [], []
    def append(start, end, metrics):
        nonlocal baseline
        tuples, span, inset, asc, desc = metrics
        if lines: baseline += max(leading, F(lines[-1]['descent'])+asc)
        pen = x+inset
        for i,t in enumerate(tuples, start):
            placed.append(dict(start=i,end=i+1,baseline_origin=[str(pen),str(baseline)],
                origin=[str(pen),str(baseline+F(t['y_offset']))],metric=t))
            pen += F(t['advance'])
        lines.append(dict(start=start,end=end,width=str(span),inset=str(inset),
            baseline=str(baseline),ascent=str(asc),descent=str(desc)))
    start = 0
    for end in [i for i,g in enumerate(glyphs) if g['text']=='\n']+[len(glyphs)]:
        cursor = start
        if cursor == end: append(cursor,end,([],F(0),F(0),*empty))
        while cursor < end:
            choices = []
            for stop in range(cursor+1,end+1):
                trimmed = stop
                while trimmed > cursor and glyphs[trimmed-1]['text']==' ': trimmed -= 1
                metrics = measure(cursor,trimmed)
                if metrics[1] <= width:
                    choices.append((stop==end or glyphs[stop-1]['text']==' ',stop,trimmed,metrics))
            if not choices: raise ValueError('TOO_NARROW')
            _,stop,trimmed,metrics = ([c for c in choices if c[0]] or choices)[-1]
            append(cursor,trimmed,metrics);cursor=stop
            while cursor < end and glyphs[cursor]['text']==' ': cursor+=1
        start=end+1
    return dict(lines=lines,glyphs=placed)


WITNESS_FIELDS = ('asset_sha', 'instance', 'features', 'upem', 'hhea', 'code_mapping',
                  'widths', 'outlines', 'descriptor', 'unicode_mapping', 'spacing_witness')


def verification_candidate(expected, current):
    """Compare supplied witness DATA; does not discover/authenticate PDF witnesses."""
    errors = [k for k in WITNESS_FIELDS if k not in expected or k not in current or expected[k] != current[k]]
    return dict(passed=not errors, errors=errors, premise='Witness extraction/association is external and unproven')


def verdict(gates):
    required = ('style_authority', 'ink_vertical_authority', 'font_binding', 'spacing_intent', 'lifecycle_canonicality')
    if any(k not in gates or gates[k].get('passed') is not True for k in required):
        return 'NOT READY'
    return 'DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR'


if __name__ == '__main__':
    print(json.dumps(layout(json.load(sys.stdin)), sort_keys=True, separators=(',', ':')))
