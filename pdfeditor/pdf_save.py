"""Full-save adapter preserving resource numbers' precision (pypdf 6.10)."""
from io import BytesIO
from collections.abc import Callable, Mapping
import os
from pathlib import Path
import re
import tempfile
import hashlib

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, ByteStringObject, DictionaryObject, FloatObject, IndirectObject,
                           NameObject, NumberObject, DecodedStreamObject, StreamObject, TextStringObject)
import pymupdf

from .backend import PdfError
from .pdf_primitives import preserve_primitive_tokens


def embedded_font_sha256(font):
    """SHA-256 of the decoded FontFile2 of a pdfengine-shaped Type0 font.

    Returns None for any other structure. The hash identifies bytes; it does
    not by itself establish who wrote them. Ownership comes only from the
    record pdfengine persisted when it wrote the subset (``generated_fonts``
    in the shared-flow sidecar).
    """
    try:
        font = font.get_object()
        if font.get("/Subtype") != "/Type0" or font.get("/Encoding") != "/Identity-H":
            return None
        descendants = font["/DescendantFonts"].get_object()
        if len(descendants) != 1:
            return None
        descendant = descendants[0].get_object()
        if descendant.get("/Subtype") != "/CIDFontType2":
            return None
        program = descendant["/FontDescriptor"].get_object()["/FontFile2"].get_object()
        return hashlib.sha256(program.get_data()).hexdigest()
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return None


def _written_value(value, depth=0):
    """A font graph as it serializes: decimal tokens, raw string bytes, decoded streams.

    Object numbers are not part of the value. Two graphs with the same written
    value are interchangeable resources.
    """
    if depth > 12:
        raise PdfError("font graph nesting limit")
    if isinstance(value, IndirectObject):
        value = value.get_object()
    if isinstance(value, DictionaryObject):
        result = {str(k): _written_value(v, depth + 1) for k, v in value.items()
                  if k not in ("/Length", "/Filter", "/DecodeParms")}
        if isinstance(value, StreamObject):
            result["decoded_stream_sha256"] = hashlib.sha256(value.get_data()).hexdigest()
        return result
    if isinstance(value, ArrayObject):
        return [_written_value(v, depth + 1) for v in value]
    if isinstance(value, FloatObject):
        return ("number", value.myrepr())
    if isinstance(value, NumberObject):
        return ("number", str(int(value)))
    if isinstance(value, NameObject):
        return ("name", str(value))
    if isinstance(value, TextStringObject):
        return ("string", value.original_bytes.hex())
    if isinstance(value, ByteStringObject):
        return ("string", bytes(value).hex())
    if isinstance(value, bool) or value is None:
        return ("literal", value)
    raise PdfError("font graph contains an unsupported object")


def _add_page_fonts(writer, page_number, font_builders, replacements=None, outcome=None):
    """Add writer-owned font graphs through page-local resource dictionaries.

    Builders are trusted internal font writers. They receive the destination
    writer and must return an indirect /Font root whose entire referenced graph
    belongs to that writer. Existing aliases are never renamed. An existing
    alias is re-targeted only when ``replacements`` names it together with the
    FontFile2 hash of the pdfengine-generated subset it currently holds; the
    caller proves that ownership and that no retained glyph paints with it. If
    the new graph has the same written value, the existing object is kept.
    ``outcome`` receives ``added``, ``replaced`` or ``reused`` per alias.
    """
    replacements = dict(replacements or {})
    if font_builders is None:
        font_builders = {}
    if not isinstance(font_builders, Mapping):
        raise PdfError("font_builders must map resource aliases to font builders")
    if set(replacements) - set(font_builders):
        raise PdfError("a replaced generated font alias needs its new font builder")
    if not font_builders:
        return
    page = writer.pages[page_number]
    resources = page.get("/Resources", DictionaryObject()).get_object()
    if not isinstance(resources, DictionaryObject):
        raise PdfError("page Resources must be a dictionary")
    fonts = resources.get("/Font", DictionaryObject()).get_object()
    if not isinstance(fonts, DictionaryObject):
        raise PdfError("page Font resources must be a dictionary")
    # Validate all aliases before invoking any builder. Only literal ASCII
    # names are accepted, preventing escaped spellings of an existing alias.
    for alias, builder in font_builders.items():
        if not isinstance(alias, str) or not re.fullmatch(r"/[A-Za-z][A-Za-z0-9_.-]*", alias):
            raise PdfError("new font alias must be a literal ASCII PDF name such as /PEF1")
        if alias in replacements:
            # Re-verify the ownership evidence against the bytes being saved.
            if alias not in fonts or embedded_font_sha256(fonts[alias]) != replacements[alias]:
                raise PdfError(f"replaced font alias is not the verified generated subset: {alias}")
        elif alias in fonts:
            raise PdfError(f"new font alias already exists: {alias}")
        if not callable(builder):
            raise PdfError("font builder must be callable")

    def verify_ownership(value, visited):
        if isinstance(value, IndirectObject):
            if value.pdf is not writer or value.generation != 0:
                raise PdfError("new font graph contains a reference not owned by the destination writer")
            if value.idnum in visited:
                return
            visited.add(value.idnum)
            value = value.get_object()
        if isinstance(value, DictionaryObject):
            for child in value.values():
                verify_ownership(child, visited)
        elif isinstance(value, ArrayObject):
            for child in value:
                verify_ownership(child, visited)

    # Shallow-copy only these two dictionary containers. All existing font,
    # image, ExtGState and other resource references retain their original
    # shared identities, including resources inherited from the page tree.
    local_resources, local_fonts = DictionaryObject(resources), DictionaryObject(fonts)
    for alias, builder in font_builders.items():
        reference = builder(writer)
        if not isinstance(reference, IndirectObject) or reference.pdf is not writer:
            raise PdfError("font builder must return a destination-writer-owned indirect font")
        verify_ownership(reference, set())
        font = reference.get_object()
        if (not isinstance(font, DictionaryObject) or font.get("/Type") != "/Font"
                or font.get("/Subtype") not in {"/Type0", "/Type1", "/TrueType", "/Type3", "/MMType1"}):
            raise PdfError("font builder did not return a valid font dictionary root")
        if alias in replacements:
            # The superseded graph is referenced only through this page-local
            # entry; once unreferenced, the reachability pass drops it.
            try:
                same = _written_value(fonts[alias]) == _written_value(reference)
            except PdfError:
                same = False
            if same:
                # Keep the existing indirect reference, never an inlined copy.
                local_fonts[NameObject(alias)] = fonts.raw_get(alias)
                state = "reused"
            else:
                local_fonts[NameObject(alias)] = reference
                state = "replaced"
        else:
            local_fonts[NameObject(alias)] = reference
            state = "added"
        if outcome is not None:
            outcome[alias] = state
    local_resources[NameObject("/Font")] = writer._add_object(local_fonts)
    page[NameObject("/Resources")] = writer._add_object(local_resources)


@preserve_primitive_tokens
def program_pdf_bytes(source, page_number, data, *, font_builders: Mapping[str, Callable[[PdfWriter], IndirectObject]] | None = None,
                      font_replacements=None, font_outcome=None):
    """Replace one page program, or several when ``data`` maps page numbers to programs.

    ``font_builders`` maps aliases to builders for a single page, or page
    numbers to such mappings when several pages change in one save.
    ``font_replacements`` has the same shape and names proven pdfengine-owned
    aliases (with their current FontFile2 hash) that the builders may re-target.
    ``font_outcome`` receives, per page, what happened to each alias.
    """
    reader = PdfReader(source)
    if reader.is_encrypted and not reader.decrypt(""):
        raise PdfError("password required")
    writer = PdfWriter(clone_from=reader)
    programs = data if isinstance(data, Mapping) else {page_number: data}
    builders = font_builders if isinstance(data, Mapping) else {page_number: font_builders}
    replacements = font_replacements if isinstance(data, Mapping) else {page_number: font_replacements}
    for number, program in programs.items():
        page_outcome = None if font_outcome is None else font_outcome.setdefault(number, {})
        _add_page_fonts(writer, number, (builders or {}).get(number), (replacements or {}).get(number), page_outcome)
        stream = DecodedStreamObject()
        stream.set_data(program)
        writer.pages[number][NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    if reader.is_encrypted:
        # Keep the authenticated file key, original /O, /U, /P and first ID.
        # pypdf derives per-object keys using the newly assigned object numbers.
        # Pinned private API; owner-password preservation has a regression test.
        writer._encryption = reader._encryption
        writer._encrypt_entry = reader.trailer["/Encrypt"].clone(writer)
        if writer._encrypt_entry.indirect_reference is None:
            writer._add_object(writer._encrypt_entry)
    reachable = set()
    visited = set()

    def visit(obj):
        if isinstance(obj, IndirectObject):
            if obj.idnum in reachable:
                return
            reachable.add(obj.idnum)
            obj = obj.get_object()
        ref = getattr(obj, "indirect_reference", None)
        if ref:
            reachable.add(ref.idnum)
        if id(obj) in visited:
            return
        visited.add(id(obj))
        if isinstance(obj, DictionaryObject):
            for value in obj.values():
                visit(value)
        elif isinstance(obj, ArrayObject):
            for value in obj:
                visit(value)

    for root in (writer.root_object, writer._info, writer._ID, writer._encrypt_entry):
        visit(root)
    for index in range(len(writer._objects)):
        if index + 1 not in reachable:
            writer._objects[index] = None
    output = BytesIO()
    writer.write(output)
    writer.close()
    return output.getvalue()


def publish_program(source, page_number, data, destination, verify=None, *, font_builders: Mapping[str, Callable[[PdfWriter], IndirectObject]] | None = None,
                    font_replacements=None, font_outcome=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = program_pdf_bytes(source, page_number, data, font_builders=font_builders,
                                font_replacements=font_replacements, font_outcome=font_outcome)
    if verify:
        with pymupdf.open(stream=encoded, filetype="pdf") as document:
            verify(document)
    descriptor, temporary = tempfile.mkstemp(prefix=".program-", suffix=".pdf", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        os.link(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def font_fingerprints(document, page_number):
    return sorted((tuple(item[1:6]), hashlib.sha256(document.extract_font(item[0])[3]).hexdigest())
                  for item in document[page_number].get_fonts(full=True))
