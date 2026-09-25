"""Operator nesting of the content pdfengine writes, for PDF 1.x output.

PDF Reference 1.3-1.7 section 4.1 (Figure 4.1) and ISO 32000-1 8.2 (Figure 9):
a text object (BT ... ET) admits only general graphics state, color, text
state, text-positioning, text-showing and marked-content operators. Special
graphics state (q, Q, cm) and path, clipping, shading, XObject and inline
image operators belong to the page description level. Marked-content
sequences and text objects must each be properly nested (PDF Reference 9.5,
ISO 32000-1 14.6). PDF 2.0 also allows q/Q inside a text object, where they
save Tm and Tlm too; pdfengine keeps the source's 1.x version, so it writes
the 1.x form: graphics-state isolation always happens outside text objects.

This checks the operators pdfengine emits. It is not a PDF validator.
"""
from .backend import PdfError
from .content_stream import operators


SPECIAL_GRAPHICS_STATE = frozenset({'q', 'Q', 'cm'})
IN_TEXT_OBJECT = frozenset({
    'w', 'J', 'j', 'M', 'd', 'ri', 'i', 'gs',                          # general graphics state
    'CS', 'cs', 'SC', 'SCN', 'sc', 'scn', 'G', 'g', 'RG', 'rg', 'K', 'k',  # color
    'Tc', 'Tw', 'Tz', 'TL', 'Tf', 'Tr', 'Ts',                          # text state
    'Td', 'TD', 'Tm', 'T*',                                            # text positioning
    'Tj', 'TJ', "'", '"',                                              # text showing
    'MP', 'DP', 'BMC', 'BDC', 'EMC',                                   # marked content
    'BX', 'EX', 'ET'})


def audit(data):
    """Nesting violations of one content program (a page's joined /Contents).

    Each violation names its kind, operator and byte offset. An empty list
    means: text objects are neither nested nor left open, q/Q balance and
    never occur inside a text object, no other page-level operator occurs
    inside one, and no marked-content sequence crosses a text object.
    """
    violations, text, saves, marked, count = [], None, 0, [], 0

    def bad(kind, op):
        violations.append(dict(kind=kind, operator=op.name, offset=op.start))

    for op in operators(data):
        name = op.name
        if text is not None and name != 'BT' and name not in IN_TEXT_OBJECT:
            bad('special-graphics-state-in-text-object' if name in SPECIAL_GRAPHICS_STATE
                else 'operator-not-allowed-in-text-object', op)
        if name == 'BT':
            if text is not None:
                bad('nested-text-object', op)
            else:
                text, count = op.start, count + 1
        elif name == 'ET':
            if text is None:
                bad('ET-without-BT', op)
            else:
                if any(m == text for m in marked):
                    bad('marked-content-crosses-text-object', op)
                text = None
        elif name == 'q':
            saves += 1
        elif name == 'Q':
            if saves:
                saves -= 1
            else:
                bad('Q-without-q', op)
        elif name in ('BMC', 'BDC'):
            marked.append(text)
        elif name == 'EMC':
            if not marked:
                bad('EMC-without-BMC', op)
            elif marked.pop() != text:
                bad('marked-content-crosses-text-object', op)
    end = len(data)
    if text is not None:
        violations.append(dict(kind='unclosed-text-object', operator='BT', offset=text))
    if saves:
        violations.append(dict(kind='unclosed-q', operator='q', offset=end))
    if marked:
        violations.append(dict(kind='unclosed-marked-content', operator='BMC', offset=end))
    return dict(text_objects=count, violations=violations)


def text_object_split(data, offset):
    """The text object open at ``offset``, where it may be closed and reopened.

    ``offset`` is a boundary between operators inside a page-level text
    object. Closing that text object there (ET), isolating new content in its
    own ``q BT ... ET Q`` and reopening it (BT) must not cross anything opened
    inside the text object: a graphics-state save, a marked-content sequence
    or a compatibility section still open at ``offset`` refuses the split.
    Returns the text object's byte span ``(start, end)``.
    """
    text = None
    inside = dict(q=0, marked=0, compatibility=0)
    found = None
    for op in operators(data):
        if found is None and op.start >= offset:
            if text is None:
                raise PdfError('new text is not isolated from a page-level text object')
            if any(inside.values()):
                raise PdfError('cannot isolate new text: a graphics-state save, marked-content sequence '
                               'or compatibility section opened inside this text object is still open')
            found = text
        name = op.name
        if name == 'BT':
            text = op.start
            inside = dict(q=0, marked=0, compatibility=0)
        elif name == 'ET':
            if found is not None:
                return found, op.end
            text = None
        elif text is not None:
            key = {'q': 'q', 'Q': 'q', 'BMC': 'marked', 'BDC': 'marked', 'EMC': 'marked',
                   'BX': 'compatibility', 'EX': 'compatibility'}.get(name)
            if key is not None:
                inside[key] += -1 if name in ('Q', 'EMC', 'EX') else 1
    raise PdfError('new text is not isolated from a page-level text object')


def require_text_object_split(content, offset):
    """Refuse a split whose text object renders text as a clip (Tr 4-7).

    Glyph outlines shown in a clipping mode accumulate into one clip that is
    applied at ET. Splitting the text object would apply it in parts.
    """
    start, end = text_object_split(content.streams[-content.page.xref], offset)
    if any(e.state.tr >= 4 for e in content.events if not e.invocation and start <= e.operator.start < end):
        raise PdfError('cannot isolate new text inside a text object that renders text as a clip')
    return start, end
