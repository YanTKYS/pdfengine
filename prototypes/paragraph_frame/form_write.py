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


def program_fonts(c, invocation):
    """Font resource names in effect in the page program or at the end of a Form invocation chain."""
    scope = c.pdf_page['/Resources'].get_object()
    for _, _, name in invocation:
        scope = scope['/XObject'][name].get_object().get('/Resources', scope).get_object()
    return [str(k) for k in (scope.get('/Font') or {}).keys()]


def _axis_aligned(m):
    a, b, c, d = m[:4]
    return abs(b) < 1e-9 and abs(c) < 1e-9 and abs(a) > 1e-9 and abs(d) > 1e-9


def _rect(points):
    xs, ys = [p.x for p in points], [p.y for p in points]
    return pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))


def _page_rect(x0, y0, x1, y1, ctm, height):
    """A user-space rectangle under ``ctm`` in page (top-left) coordinates."""
    m = pymupdf.Matrix(*ctm)
    corners = [pymupdf.Point(x, y) * m for x in (x0, x1) for y in (y0, y1)]
    r = _rect(corners)
    return pymupdf.Rect(r.x0, height - r.y1, r.x1, height - r.y0)


def safe_boundaries(boundaries, first, containers=()):
    """Boundaries before ``first`` outside text objects, paths and clips, under an axis-aligned CTM.

    With no ``containers`` the boundary is outside marked content. Otherwise it
    lies inside the innermost container, at its nesting depth, so the new
    drawing stays in the same labelled or layered sequence as the old text.
    """
    depth = len(containers)
    low = containers[-1]['content_byte_range'][0] if containers else 0
    return [b for b in boundaries if low <= b.operator.end <= first and b.marked_content_depth == depth and not b.text_object
            and not b.pending_path and not b.pending_clip and _axis_aligned(b.state.ctm)]


LINE_STATE = {'marked_content', 'w', 'J', 'j', 'M', 'd', 'ri', 'i'}
# ExtGState entries that do not change how an opaque text fill is painted on
# screen (overprint only affects separations; line parameters and stroke
# adjustment do not apply to Tr 0 text).
HARMLESS_GS = {'/Type', '/AIS', '/BM', '/CA', '/ca', '/OP', '/op', '/OPM', '/SA', '/SMask', '/LW', '/LC', '/LJ', '/ML', '/D', '/RI', '/FL', '/SM'}


def _harmless(gs):
    return (isinstance(gs, dict) and set(gs) <= HARMLESS_GS and gs.get('/BM', '/Normal') in ('/Normal', '/Compatible')
            and gs.get('/SMask', '/None') == '/None' and gs.get('/ca', 1) == 1 and str(gs.get('/AIS', 'False')) == 'False')


def insertion(boundary, frames, height):
    """Bytes that put new drawing in default user space at ``boundary``, or a refusal."""
    state = boundary.state
    if state.opacity != 1 or state.stroke_opacity != 1:
        raise Refused('unsupported inherited insertion graphics state')
    for key, value in state.other.items():
        if key in LINE_STATE:
            continue
        if not (key.startswith('ExtGState:') and _harmless(value)):
            raise Refused('unsupported inherited insertion graphics state: ' + key)
    for clip in state.clip:
        if clip.get('rule') == 'Form BBox':
            x0, y0, x1, y1 = clip['bbox']
            area = _page_rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), clip['ctm'], height)
        elif 'path' in clip and len(clip['path']) == 1 and clip['path'][0]['operator'] == 're' and _axis_aligned(clip['path'][0]['ctm']):
            x, y, w, h = clip['path'][0]['args']
            area = _page_rect(min(x, x + w), min(y, y + h), max(x, x + w), max(y, y + h), clip['path'][0]['ctm'], height)
        else:
            raise Refused('nonrectangular insertion clip')
        if not all(area.contains(pymupdf.Rect(f)) for f in frames):
            raise Refused('insertion clip does not contain approved frame')
    if tuple(state.ctm) == IDENTITY:
        return b''
    return b' '.join(number(v) for v in inverse(state.ctm)) + b' cm\n'


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


def copy_on_write(writer, page, c, stream, invocation, data, fonts):
    """Install the edited program ``data``: the page contents, or clones along a Form invocation chain.

    ``fonts`` maps new font resource names to written font objects; they are
    added to the resources of the program that draws them. Returns the clone
    summary (empty for the page program).
    """
    lookup = writer._id_translated[id(c.reader)]
    resources = DictionaryObject(dict(page['/Resources']))
    if not invocation:
        if fonts:
            fontdict = DictionaryObject(dict(resources.get('/Font', DictionaryObject())))
            fontdict.update({NameObject(k): v for k, v in fonts.items()})
            resources[NameObject('/Font')] = fontdict
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
            fontdict = DictionaryObject(dict(form_resources.get('/Font', DictionaryObject())))
            fontdict.update({NameObject(n): v for n, v in fonts.items()})
            form_resources[NameObject('/Font')] = fontdict
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
    """Digest of the page program and of every Form reachable from it, by resource name path."""
    result = {}

    def walk(resources, prefix, depth):
        if depth > 8:
            return
        for name, ref in (resources.get('/XObject') or {}).items():
            form = ref.get_object()
            if form.get('/Subtype') != '/Form':
                continue
            path = prefix + (str(name),)
            result[path] = hashlib.sha256(form.get_data()).hexdigest()
            walk(form.get('/Resources', resources).get_object(), path, depth + 1)
    p = reader.pages[page]
    contents = p.get_contents()
    result[()] = hashlib.sha256(contents.get_data() if contents is not None else b'').hexdigest()
    walk(p['/Resources'].get_object(), (), 0)
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
