"""Pinned pypdf adapter: retain PDF name bytes and decimal resource tokens.

PDF names are byte identifiers, not Unicode text. pypdf's default charset
guessing and UTF-8 serialization changes legacy names; its float writer also
rounds resource values. The hooks below dispatch through a ContextVar, so
ordinary pypdf users (including other threads/tasks) retain the default parser.
Only full-clone saves use the lossless primitive subclasses. No PDF-specific
names, fonts or producer checks are involved.
"""
from contextvars import ContextVar
from functools import wraps

from pypdf.generic import NameObject, NumberObject, FloatObject
from pypdf._utils import read_until_regex
from pypdf.errors import PdfReadError


_preserve = ContextVar("pdfeditor_preserve_primitives", default=False)
_read_name = NameObject.read_from_stream
_read_number = NumberObject.read_from_stream


class ByteName(NameObject):
    def __new__(cls, raw):
        obj = super().__new__(cls, raw.decode("latin1"))
        obj.raw_pdf_bytes = raw
        return obj

    def renumber(self):
        return b"/" + b"".join(bytes([c]) if 33 <= c <= 126 and c not in b"#()<>[]{}/%"
                               else f"#{c:02X}".encode("ascii") for c in self.raw_pdf_bytes[1:])

    def clone(self, pdf_dest, force_duplicate=False, ignore_fields=()):
        return self._reference_clone(ByteName(self.raw_pdf_bytes), pdf_dest, force_duplicate)


class DecimalToken(FloatObject):
    def __new__(cls, token):
        obj = super().__new__(cls, token)
        obj.pdf_token = token.decode("ascii") if isinstance(token, bytes) else token
        return obj

    def myrepr(self):
        return self.pdf_token

    def clone(self, pdf_dest, force_duplicate=False, ignore_fields=()):
        return self._reference_clone(DecimalToken(self.pdf_token), pdf_dest, force_duplicate)


def _name_dispatch(stream, pdf):
    if not _preserve.get():
        return _read_name(stream, pdf)
    if stream.read(1) != b"/":
        raise PdfReadError("Name read error")
    return ByteName(NameObject.unnumber(b"/" + read_until_regex(stream, NameObject.delimiter_pattern)))


def _number_dispatch(stream):
    if not _preserve.get():
        return _read_number(stream)
    token = read_until_regex(stream, NumberObject.NumberPattern)
    return DecimalToken(token) if b"." in token else NumberObject(token)


NameObject.read_from_stream = staticmethod(_name_dispatch)
NumberObject.read_from_stream = staticmethod(_number_dispatch)


def preserve_primitive_tokens(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = _preserve.set(True)
        try:
            return function(*args, **kwargs)
        finally:
            _preserve.reset(token)
    return wrapped
