"""Full-save adapter preserving resource numbers' precision (pypdf 6.10)."""
from io import BytesIO
from collections.abc import Callable, Mapping
import os
from pathlib import Path
import re
import tempfile
import hashlib

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, DecodedStreamObject
import pymupdf

from .backend import PdfError
from .pdf_primitives import preserve_primitive_tokens


def _add_page_fonts(writer, page_number, font_builders):
    """Add writer-owned font graphs through page-local resource dictionaries.

    Builders are trusted internal font writers. They receive the destination
    writer and must return an indirect /Font root whose entire referenced graph
    belongs to that writer. Existing aliases are never overwritten or renamed.
    """
    if font_builders is None:
        return
    if not isinstance(font_builders, Mapping):
        raise PdfError("font_builders must map resource aliases to font builders")
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
        if alias in fonts:
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
        local_fonts[NameObject(alias)] = reference
    local_resources[NameObject("/Font")] = writer._add_object(local_fonts)
    page[NameObject("/Resources")] = writer._add_object(local_resources)


@preserve_primitive_tokens
def program_pdf_bytes(source, page_number, data, *, font_builders: Mapping[str, Callable[[PdfWriter], IndirectObject]] | None = None):
    reader = PdfReader(source)
    if reader.is_encrypted and not reader.decrypt(""):
        raise PdfError("password required")
    writer = PdfWriter(clone_from=reader)
    _add_page_fonts(writer, page_number, font_builders)
    stream = DecodedStreamObject()
    stream.set_data(data)
    writer.pages[page_number][NameObject("/Contents")] = writer._add_object(stream.flate_encode())
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


def publish_program(source, page_number, data, destination, verify=None, *, font_builders: Mapping[str, Callable[[PdfWriter], IndirectObject]] | None = None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = program_pdf_bytes(source, page_number, data, font_builders=font_builders)
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
