"""Font/resource inventory for evaluations and tests; not a production PDF analyzer.

Ownership is read from the flow's persisted ``generated_fonts`` records and the
bytes they describe. A font is ``original`` when the source PDF holds the same
alias with the same written value, ``owned`` when a verified record claims it,
and ``unknown`` otherwise. Nothing here infers ownership from an alias prefix.
"""
import hashlib

from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

from pdfeditor.continuation import markers
from pdfeditor.pdf_save import _written_value, embedded_font_sha256


def _reader(pdf):
    reader = PdfReader(pdf)
    if reader.is_encrypted:
        reader.decrypt('')
    return reader


def _page_fonts(page):
    resources = page.get('/Resources')
    fonts = resources.get_object().get('/Font') if resources is not None else None
    return fonts.get_object() if fonts is not None else DictionaryObject()


def _value_sha256(font):
    return hashlib.sha256(repr(_written_value(font)).encode()).hexdigest()


def _graph(value, seen):
    """Indirect object numbers reachable from one font root."""
    if isinstance(value, IndirectObject):
        if value.idnum in seen:
            return
        seen.add(value.idnum)
        value = value.get_object()
    if isinstance(value, DictionaryObject):
        for child in value.values():
            _graph(child, seen)
    elif isinstance(value, ArrayObject):
        for child in value:
            _graph(child, seen)


def inventory(pdf, *, records=None, source=None):
    """Per page font entries, ownership classes and document-level counts."""
    reader = _reader(pdf)
    original = {}
    if source is not None:
        for number, page in enumerate(_reader(source).pages, 1):
            original[number] = {str(a): _value_sha256(f) for a, f in _page_fonts(page).items()}
    pages, owned_roots, owned_objects, type0 = {}, set(), set(), set()
    for number, page in enumerate(reader.pages, 1):
        entries = []
        fonts = _page_fonts(page)
        claims = (records or {}).get(str(number), {})
        for alias in sorted(fonts, key=lambda a: (len(a), a)):
            raw = fonts.raw_get(alias)
            font = fonts[alias]
            program = embedded_font_sha256(font)
            if str(alias) in claims and program == claims[str(alias)]['subset_sha256']:
                kind = 'pdfengine-owned'
                if isinstance(raw, IndirectObject):
                    owned_roots.add(raw.idnum)
                _graph(raw, owned_objects)
            elif original.get(number, {}).get(str(alias)) == _value_sha256(font):
                kind = 'original'
            else:
                kind = 'unknown'
            entries.append(dict(alias=str(alias), subtype=str(font.get('/Subtype')),
                                basefont=str(font.get('/BaseFont')), program_sha256=program, kind=kind))
        pages[str(number)] = entries
    # In-use objects only: the garbage-collected numbers are free xref entries.
    numbers = {n for table in reader.xref.values() for n in table} | set(reader.xref_objStm)
    for number in sorted(numbers):
        value = reader.get_object(number)
        if isinstance(value, DictionaryObject) and value.get('/Type') == '/Font' and value.get('/Subtype') == '/Type0':
            type0.add(number)
    return dict(pages=pages, type0_fonts=len(type0), owned_font_roots=len(owned_roots),
                owned_font_graph_objects=len(owned_objects))


def generated_block_bytes(pdf, page, destination):
    """Bytes of the marked generated continuation block on ``page``; 0 when absent."""
    data = _reader(pdf).pages[page - 1].get_contents().get_data()
    begin, end = markers(destination)
    start = data.find(begin)
    if start < 0:
        return 0
    stop = data.find(end, start)
    if stop < 0:
        raise ValueError('generated continuation block has no end marker')
    return stop + len(end) - start
