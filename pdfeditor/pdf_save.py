"""Full-save adapter preserving resource numbers' precision (pypdf 6.10)."""
from io import BytesIO
import os
from pathlib import Path
import tempfile
import hashlib

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject, DecodedStreamObject
import pymupdf

from .backend import PdfError
from .pdf_primitives import preserve_primitive_tokens


@preserve_primitive_tokens
def program_pdf_bytes(source, page_number, data):
    reader = PdfReader(source)
    if reader.is_encrypted and not reader.decrypt(""):
        raise PdfError("password required")
    writer = PdfWriter(clone_from=reader)
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


def publish_program(source, page_number, data, destination, verify=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = program_pdf_bytes(source, page_number, data)
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
