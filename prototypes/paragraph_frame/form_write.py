"""Where edited text is drawn: the page program or one Form XObject invocation.

A Form XObject may be painted several times (on one page, on other pages, or
inside other Forms). Writing into it would change every painting. Edits are
therefore copy-on-write per invocation:

* the program that draws the edited text (the innermost Form) is cloned with
  the edit applied, and so is every Form on the invocation chain above it;
* only the ``Do`` operator of that one invocation is renamed to the clone, in
  the cloned parent (or the page program); the original Form objects, their
  other invocations and other pages are left as they are;
* new drawing is inserted in the same program, at a boundary before the
  removed text, under ``q <inverse CTM> cm`` so that it is placed in default
  user space; the boundary state (clip, Form BBox, opacity) must contain the
  approved frames.

``unchanged_elsewhere`` verifies the result on the written file.
"""
import hashlib
import struct
import pymupdf
from pypdf import PdfReader
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from pdfeditor.content_stream import number, operators
from .engine import Refused
from .text_space import inverse

IDENTITY = (1, 0, 0, 1, 0, 0)


def drawing_site(c, events):
    """(stream xref, invocation) shared by every edited text operator."""
    sites = {(e.stream_xref, e.invocation) for e in events}
    if len(sites) != 1:
        raise Refused('the edited text is drawn by more than one content stream or Form XObject invocation')
    return sites.pop()


def program_fonts(c, invocation, kind='/Font'):
    """Resource names of one kind in effect in the page program or at the end of a Form invocation chain."""
    scope = c.pdf_page['/Resources'].get_object()
    for _, _, name in invocation:
        scope = scope['/XObject'][name].get_object().get('/Resources', scope).get_object()
    return [str(k) for k in (scope.get(kind) or {}).keys()]


# Opaque, normal-blend painting without soft mask or overprint: the state an
# edited text that qualified was drawn with, set again over an inherited one.
NEUTRAL_GS = {'/Type': '/ExtGState', '/ca': 1, '/CA': 1, '/BM': '/Normal', '/SMask': '/None', '/AIS': False, '/OP': False, '/op': False}


def drawn_neutral(state):
    """Whether text was painted with the neutral state: opaque, normal blend, no soft mask, alpha not shape."""
    b = state.blend
    return (state.opacity == 1 and state.stroke_opacity == 1 and b.get('/BM', '/Normal') in ('/Normal', '/Compatible')
            and b.get('/SMask', '/None') == '/None' and b.get('/AIS', 'False') == 'False')


def neutral_gs():
    from pypdf.generic import BooleanObject, NumberObject
    return DictionaryObject({NameObject(k): (BooleanObject(v) if isinstance(v, bool) else NumberObject(v) if isinstance(v, int) else NameObject(v))
                             for k, v in NEUTRAL_GS.items()})


def _axis_aligned(m):
    a, b, c, d = m[:4]
    return abs(b) < 1e-9 and abs(c) < 1e-9 and abs(a) > 1e-9 and abs(d) > 1e-9


def _rect(points):
    xs, ys = [p.x for p in points], [p.y for p in points]
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


def _page_rect(x0, y0, x1, y1, ctm, page):
    """A user-space rectangle under ``ctm`` in page (top-left) coordinates; ``page`` maps default user space there."""
    m = pymupdf.Matrix(*ctm) * pymupdf.Matrix(page)
    return _rect([pymupdf.Point(x, y) * m for x in (x0, x1) for y in (y0, y1)])


def clip_rect(clip, page):
    """A Form BBox or single-rectangle clip in page coordinates, or None for any other clip."""
    if clip.get('rule') == 'Form BBox':
        x0, y0, x1, y1 = clip['bbox']
        return _page_rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), clip['ctm'], page)
    if 'path' in clip and len(clip['path']) == 1 and clip['path'][0]['operator'] == 're' and _axis_aligned(clip['path'][0]['ctm']):
        x, y, w, h = clip['path'][0]['args']
        return _page_rect(min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h), clip['path'][0]['ctm'], page)
    return None


def safe_boundaries(boundaries, first, containers=(), reemitted=()):
    """Boundaries before ``first`` outside text objects, paths and clips, under an axis-aligned CTM.

    With no ``containers`` the boundary is outside marked content. Otherwise it
    lies inside the innermost container, at its nesting depth, so the new
    drawing stays in the same labelled or layered sequence as the old text.
    A boundary directly inside a ``reemitted`` sequence (whose BDC/EMC the edit
    removes) is at that depth too: a producer may set the clip of the text
    inside its sequence (Distiller: ``BDC Q q re W n BT …``).
    """
    depth = len(containers)
    low = containers[-1]['content_byte_range'][0] if containers else 0

    def level(b):
        inside = any(s['content_byte_range'][0] <= b.operator.end <= s['content_byte_range'][1] for s in reemitted)
        return b.marked_content_depth - (1 if inside else 0)
    return [b for b in boundaries if low <= b.operator.end <= first and level(b) == depth and not b.text_object
            and not b.pending_path and not b.pending_clip and _axis_aligned(b.state.ctm)]


LINE_STATE = {'marked_content', 'w', 'J', 'j', 'M', 'd', 'ri', 'i'}
# ExtGState entries that do not change how an opaque text fill is painted on
# screen (overprint only affects separations; line parameters and stroke
# adjustment do not apply to Tr 0 text).
HARMLESS_GS = {'/Type', '/AIS', '/BM', '/CA', '/ca', '/OP', '/op', '/OPM', '/SA', '/SMask', '/LW', '/LC', '/LJ', '/ML', '/D', '/RI', '/FL', '/SM'}


def _harmless(gs):
    return (isinstance(gs, dict) and set(gs) <= HARMLESS_GS and gs.get('/BM', '/Normal') in ('/Normal', '/Compatible')
            and gs.get('/SMask', '/None') == '/None' and gs.get('/ca', 1) == 1 and str(gs.get('/AIS', 'False')) == 'False')


# Operators that neither paint nor clip anything but text.
TEXT_ONLY = {'BT', 'ET', 'Tf', 'Tm', 'Td', 'TD', 'T*', 'Tc', 'Tw', 'Tz', 'TL', 'Ts', 'Tr', 'Tj', 'TJ', "'", '"', 'g', 'rg', 'k', 'G', 'RG', 'K',
             'cs', 'sc', 'scn', 'CS', 'SC', 'SCN', 'gs', 'BDC', 'BMC', 'EMC', 'q', 'Q', 'cm', 'ri', 'i', 'w', 'J', 'j', 'M', 'd'}


def after_text(boundaries, data, first, last, containers=()):
    """The first safe boundary after the edited text when only text lies between.

    Producers often draw a heading and the following paragraph in one text
    object; the boundary before it would move the new text in front of the
    heading in content (reading) order. After that text object, nothing but
    text was painted in between, so paint order is kept as well.
    """
    depth = len(containers)
    high = containers[-1]['end_byte_range'][0] if containers else len(data)
    ops = [o for o in operators(data) if o.start >= first]
    for b in boundaries:
        if b.operator.end < last or b.ordinal < 0:
            continue
        if b.operator.end > high:
            return None
        if any(o.name not in TEXT_ONLY for o in ops if o.start < b.operator.end):
            return None
        if (b.marked_content_depth == depth and not b.text_object and not b.pending_path and not b.pending_clip
                and _axis_aligned(b.state.ctm)):
            return b
    return None


SHOWS = {'Tj', 'TJ', "'", '"'}


def foreign_text_between(data, low, high, removed):
    """Whether a text-showing operator that is not removed lies in [low, high)."""
    return any(o.name in SHOWS and low <= o.start < high and o.start not in removed for o in operators(data))


def inside_text(boundaries, data, event, containers=()):
    """``(boundary, restore)`` inside the text object, right before the first edited text operator.

    Producers such as InDesign draw a whole column in one text object, with
    relative line moves; the new drawing is spliced in there as
    ``ET … BT <restore>``. BT/ET only reset the text and line matrices, and
    the restore sets both again to their values at that point (they must be
    equal: no text shown on the line before it), so the remaining text keeps
    its place and reading and paint order are exact. Not after
    text clipping (Tr >= 4) in the text object, or with marked content opened
    inside it.
    """
    if event.operator.name not in ('Tj', 'TJ') or any(abs(x - y) > 1e-9 for x, y in zip(event.text_matrix, event.line_matrix)):
        return None
    first = event.operator.start
    ops = list(operators(data))
    depth = len(containers)
    low = containers[-1]['content_byte_range'][0] if containers else 0
    before = [b for b in boundaries if b.operator.end <= first]
    if not before:
        return None
    b = before[-1]
    if any(o.start >= b.operator.end and o.start < first for o in ops):
        return None
    # Text clip modes accumulate a clip that ET applies; any of them earlier
    # in the text object rules out an ET there.
    clipping = False
    for o in ops:
        if o.start >= first:
            break
        if o.name == 'BT':
            clipping = False
        elif o.name == 'Tr' and o.args and float(o.args[-1]) >= 4:
            clipping = True
    if (not b.text_object or b.operator.end < low or b.marked_content_depth != depth or b.pending_path or b.pending_clip
            or b.state.tr >= 4 or clipping or not _axis_aligned(b.state.ctm)):
        return None
    # Later Td/T* moves are relative to this matrix, and renderers keep it in
    # their own arithmetic (MuPDF float32, Poppler double). Replaying the
    # text object's own positioning (and leading) operators up to here, from
    # its last absolute Tm, reproduces it in
    # every renderer exactly. Line-showing operators (' and ") cannot be
    # replayed; then the matrix is written: the exact source value where it
    # rounds to MuPDF's float32 value (glyph identity), else MuPDF's value.
    opened = max((n for n, o in enumerate(ops) if o.name == 'BT' and o.start < first), default=None)
    moves = [o for o in ops[opened + 1:] if o.start < first and o.name in ('Tm', 'Td', 'TD', 'T*', 'TL', "'", '"')] if opened is not None else None
    if moves is not None and not any(o.name in ("'", '"') for o in moves):
        last = max((n for n, o in enumerate(moves) if o.name == 'Tm'), default=0)
        return b, b''.join(data[o.start:o.end] + b'\n' for o in moves[last:])
    exact = event.atoms[0].source_text_matrix if event.atoms and not isinstance(event.atoms[0], (int, float)) else None
    matrix = [e if exact and _float32(e) == _float32(v) else v for e, v in zip(exact or event.text_matrix, event.text_matrix)]
    return b, b' '.join(number(v) for v in matrix) + b' Tm\n'


def _float32(value):
    return struct.unpack('f', struct.pack('f', value))[0]


def insertion(boundary, frames, page, neutral=False):
    """Bytes that put new drawing in default user space at ``boundary``, or a refusal.

    ``frames`` are page (top-left) rectangles; ``page`` is the page's
    transformation from default user space to them (any MediaBox origin).
    With ``neutral`` the drawing sets the neutral ExtGState itself, so an
    inherited transparency state does not matter (only clips do).
    """
    state = boundary.state
    if not neutral:
        if state.opacity != 1 or state.stroke_opacity != 1:
            raise Refused('unsupported inherited insertion graphics state')
        for key, value in state.other.items():
            if key in LINE_STATE:
                continue
            if not (key.startswith('ExtGState:') and _harmless(value)):
                raise Refused('unsupported inherited insertion graphics state: ' + key)
    for clip in state.clip:
        area = clip_rect(clip, page)
        if area is None:
            raise Refused('nonrectangular insertion clip')
        if not all(area.contains(pymupdf.Rect(f)) for f in frames):
            raise Refused('insertion clip does not contain approved frame')
    if tuple(state.ctm) == IDENTITY:
        return b''
    return b' '.join(number(v) for v in inverse(state.ctm)) + b' cm\n'


def wrap(boundary, drawing, restore=b''):
    """The inserted bytes at ``boundary``: inside a text object they are closed off by ET/BT."""
    return b'\nET\n' + drawing + b'BT\n' + restore if boundary.text_object else b'\n' + drawing


def _clone(writer, original, data, resources):
    clone = DecodedStreamObject()
    for key, value in original.items():
        if key not in ('/Length', '/Filter', '/DecodeParms'):
            clone[NameObject(key)] = value
    if resources is not None:
        clone[NameObject('/Resources')] = resources
    clone.set_data(data)
    return writer._add_object(clone.flate_encode())


def _fresh(names, base):
    name = base + 'PF'
    while name in names:
        name += 'x'
    return name


def _entries(resources, key):
    """A new dictionary with the entries of ``resources[key]``, references kept as references.

    ``dict()`` of an IndirectObject (what ``get`` returns) would resolve every
    value and write the font dictionaries and programs inline.
    """
    return DictionaryObject(dict(resources[key])) if key in resources else DictionaryObject()


def copy_on_write(writer, page, c, stream, invocation, data, fonts, gstates=None):
    """Install the edited program ``data``: the page contents, or clones along a Form invocation chain.

    ``fonts`` (and ``gstates``) map new font (ExtGState) resource names to
    written objects; they are added to the resources of the program that
    draws them. Returns the clone summary (empty for the page program).
    """
    lookup = writer._id_translated[id(c.reader)]
    resources = DictionaryObject(dict(page['/Resources']))
    if not invocation:
        if fonts:
            fontdict = _entries(resources, '/Font')
            fontdict.update({NameObject(k): v for k, v in fonts.items()})
            resources[NameObject('/Font')] = fontdict
        if gstates:
            gsdict = _entries(resources, '/ExtGState')
            gsdict.update({NameObject(k): v for k, v in gstates.items()})
            resources[NameObject('/ExtGState')] = gsdict
        page[NameObject('/Resources')] = resources
        stream_object = DecodedStreamObject()
        stream_object.set_data(data)
        page[NameObject('/Contents')] = writer._add_object(stream_object.flate_encode())
        return []
    # Resolve the chain: each Do name in the resources in effect (source and written copy).
    chain, scope, written = [], c.pdf_page['/Resources'].get_object(), page['/Resources'].get_object()
    for parent, index, name in invocation:
        ref = scope['/XObject'].raw_get(name)
        form = ref.get_object()
        copy = writer.get_object(lookup[ref.idnum])
        inner = copy.get('/Resources')
        inner = inner.get_object() if inner is not None else written
        chain.append(dict(parent=parent, index=index, name=name, idnum=ref.idnum, scope=scope, original=copy, resources=inner))
        scope, written = form.get('/Resources', scope).get_object(), inner
    clones, replacement, entry = [], data, None
    for k in range(len(chain) - 1, -1, -1):
        link = chain[k]
        original = link['original']
        form_resources = DictionaryObject(dict(link['resources']))
        if k == len(chain) - 1 and fonts:
            fontdict = _entries(form_resources, '/Font')
            fontdict.update({NameObject(n): v for n, v in fonts.items()})
            form_resources[NameObject('/Font')] = fontdict
        if k == len(chain) - 1 and gstates:
            gsdict = _entries(form_resources, '/ExtGState')
            gsdict.update({NameObject(n): v for n, v in gstates.items()})
            form_resources[NameObject('/ExtGState')] = gsdict
        if entry is not None:
            xobjects = DictionaryObject(dict(form_resources['/XObject']))
            xobjects[NameObject(entry[0])] = entry[1]
            form_resources[NameObject('/XObject')] = xobjects
        clone_ref = _clone(writer, original, replacement, form_resources)
        parent_data = c.streams[link['parent']]
        ops = list(operators(parent_data))
        op = ops[link['index']]
        if op.name != 'Do' or str(op.args[0]) != link['name']:
            raise Refused('Form invocation operator not found')
        names = set(link['scope'].get('/XObject', {}).keys())
        new = _fresh(names, link['name'])
        replacement = parent_data[:op.start] + new.encode('latin-1') + b' Do' + parent_data[op.end:]
        entry = (new, clone_ref)
        clones.append(dict(form=link['name'], clone=new, source_object=link['idnum']))
    # ``replacement`` is now the page program with the outermost Do renamed.
    xobjects = DictionaryObject(dict(resources['/XObject']))
    xobjects[NameObject(entry[0])] = entry[1]
    resources[NameObject('/XObject')] = xobjects
    page[NameObject('/Resources')] = resources
    stream_object = DecodedStreamObject()
    stream_object.set_data(replacement)
    page[NameObject('/Contents')] = writer._add_object(stream_object.flate_encode())
    return clones


def _programs(reader, page):
    """Digest of the page program and of every Form reachable from it, keyed by the first name path.

    Each Form object is visited once, breadth first (a Form without its own
    resources inherits its parent's and may reach itself again; shared Forms
    are reached by many paths). Name paths, not object numbers, are compared
    between the source and the written file.
    """
    p = reader.pages[page]
    contents = p.get_contents()
    result = {(): hashlib.sha256(contents.get_data() if contents is not None else b'').hexdigest()}
    seen, queue = set(), [((), p['/Resources'].get_object())]
    while queue:
        prefix, resources = queue.pop(0)
        for name, ref in (resources.get('/XObject') or {}).items():
            form = ref.get_object()
            key = ref.idnum if hasattr(ref, 'idnum') else id(form)
            if key in seen or form.get('/Subtype') != '/Form':
                continue
            seen.add(key)
            path = prefix + (str(name),)
            result[path] = hashlib.sha256(form.get_data()).hexdigest()
            queue.append((path, form.get('/Resources', resources).get_object()))
    return result


def unchanged_elsewhere(source, output, page):
    """Other pages and every original Form painting of the edited page are unchanged."""
    a, b = PdfReader(source), PdfReader(output)
    if len(a.pages) != len(b.pages):
        raise Refused('page count changed')
    for n in range(len(a.pages)):
        if n == page - 1:
            continue
        if _programs(a, n) != _programs(b, n):
            raise Refused('staged change outside the edited page (shared program or Form XObject)')
    before, after = _programs(a, page - 1), _programs(b, page - 1)
    lost = [path for path, digest in before.items() if path and after.get(path) != digest]
    if lost:
        raise Refused('an original Form XObject of the edited page changed: ' + '/'.join(lost[0]))
