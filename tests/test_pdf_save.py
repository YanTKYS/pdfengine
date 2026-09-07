"""Full-save regressions: object reachability, resource fidelity and encryption."""
from io import BytesIO

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf._crypt_providers import crypt_provider
from pypdf.constants import UserAccessPermissions
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, IndirectObject, NameObject, NumberObject

from pdfeditor.backend import PdfError
from pdfeditor.pdf_save import font_fingerprints, program_pdf_bytes, publish_program


def _raw_pdf(tmp_path, *, array_contents=False, shared=False):
    """Write a tiny independent fixture without pypdf normalizing its input values."""
    first = b'BT /F1 12 Tf 20 150 Td (ORIGINAL_SECRET) Tj ET'
    second = b'BT /F1 12 Tf 20 120 Td (SECOND_SECRET) Tj ET'

    def stream(data):
        return b'<< /Length ' + str(len(data)).encode() + b' >>\nstream\n' + data + b'\nendstream'

    contents = b'[5 0 R 6 0 R]' if array_contents else b'5 0 R'
    resources = (b'<< /Font << /F1 4 0 R >> /Properties << /Audit << '
                 b'/OriginalName /#82#6c#82#72#96#be#92#a9 '
                 b'/Precise 600.123456789 /Small 0.123456789123 >> >> >>')
    page = b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources ' + resources + b' /Contents ' + contents + b' >>'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Count ' + (b'2 /Kids [3 0 R 7 0 R]' if shared else b'1 /Kids [3 0 R]') + b' >>',
        page,
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding '
        b'/FirstChar 32 /LastChar 126 /Widths [' + b' '.join([b'600'] * 95) + b'] >>',
        stream(first), stream(second), page if shared else b'null',
    ]
    output = bytearray(b'%PDF-1.7\n')
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(str(number).encode() + b' 0 obj\n' + obj + b'\nendobj\n')
    startxref = len(output)
    output.extend(b'xref\n0 ' + str(len(objects) + 1).encode() + b'\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(b'trailer\n<< /Size ' + str(len(objects) + 1).encode() + b' /Root 1 0 R >>\nstartxref\n' + str(startxref).encode() + b'\n%%EOF\n')
    path = tmp_path / 'source.pdf'
    path.write_bytes(output)
    return path


def _all_decoded_streams(encoded, password=''):
    reader = PdfReader(BytesIO(encoded))
    if reader.is_encrypted:
        assert reader.decrypt(password)
    result = []
    for generation, entries in reader.xref.items():
        for object_id in entries:
            if not object_id:
                continue
            obj = reader.get_object(IndirectObject(object_id, generation, reader))
            if hasattr(obj, 'get_data'):
                result.append(obj.get_data())
    return result


def test_resource_numeric_values_retain_input_precision(tmp_path):
    source = _raw_pdf(tmp_path)
    encoded = program_pdf_bytes(source, 0, b'q Q')
    # Inspect numeric tokens as well as parsed values: a new serializer must
    # not silently round a resource that this operation did not select.
    assert b'600.123456789' in encoded
    assert b'0.123456789123' in encoded


def test_non_utf8_pdf_name_bytes_are_retained(tmp_path):
    source = _raw_pdf(tmp_path)
    encoded = program_pdf_bytes(source, 0, b'q Q')
    with pymupdf.open(source) as original, pymupdf.open(stream=encoded) as saved:
        before = original.xref_get_key(original[0].xref, 'Resources/Properties/Audit/OriginalName')
        after = saved.xref_get_key(saved[0].xref, 'Resources/Properties/Audit/OriginalName')
        assert after == before


def test_lossless_adapter_does_not_change_unrelated_pypdf_parser_behavior(tmp_path):
    source = _raw_pdf(tmp_path)

    def ordinary_numeric_object():
        return PdfReader(source).pages[0]['/Resources']['/Properties']['/Audit']['/Precise']

    assert type(ordinary_numeric_object()) is FloatObject
    program_pdf_bytes(source, 0, b'q Q')
    assert type(ordinary_numeric_object()) is FloatObject
    # Exceptional exit must also reset the preservation context.
    with pytest.raises(IndexError):
        program_pdf_bytes(source, 99, b'q Q')
    assert type(ordinary_numeric_object()) is FloatObject


@pytest.mark.parametrize('array_contents', [False, True])
def test_unreachable_original_streams_are_removed_from_file_not_only_page(tmp_path, array_contents):
    source = _raw_pdf(tmp_path, array_contents=array_contents)
    before = source.read_bytes()
    encoded = program_pdf_bytes(source, 0, b'BT /F1 12 Tf 20 150 Td (NEW_TEXT) Tj ET')
    assert source.read_bytes() == before
    streams = _all_decoded_streams(encoded)
    assert any(b'NEW_TEXT' in s for s in streams)
    assert not any(b'ORIGINAL_SECRET' in s or b'SECOND_SECRET' in s for s in streams)
    with pymupdf.open(stream=encoded) as saved:
        assert 'NEW_TEXT' in saved[0].get_text()
        assert 'SECRET' not in saved[0].get_text()


def test_streams_shared_by_another_page_remain_reachable_and_unchanged(tmp_path):
    source = _raw_pdf(tmp_path, array_contents=True, shared=True)
    encoded = program_pdf_bytes(source, 0, b'BT /F1 12 Tf 20 150 Td (NEW_TEXT) Tj ET')
    streams = _all_decoded_streams(encoded)
    assert any(b'ORIGINAL_SECRET' in s for s in streams)
    assert any(b'SECOND_SECRET' in s for s in streams)
    with pymupdf.open(source) as original, pymupdf.open(stream=encoded) as saved:
        assert 'SECRET' not in saved[0].get_text()
        assert 'ORIGINAL_SECRET' in saved[1].get_text()
        assert saved[1].get_pixmap(dpi=144).samples == original[1].get_pixmap(dpi=144).samples


@pytest.mark.parametrize('algorithm', ['RC4-40', 'RC4-128', 'AES-128', 'AES-256'])
def test_empty_user_password_and_original_owner_password_are_preserved(tmp_path, algorithm):
    if algorithm.startswith('AES') and crypt_provider[0] == 'local_crypt_fallback':
        pytest.skip('AES provider unavailable: pypdf needs cryptography or pycryptodome')
    plain = _raw_pdf(tmp_path, shared=True)
    writer = PdfWriter(clone_from=plain)
    writer.encrypt('', 'original-owner-password', algorithm=algorithm,
                   permissions_flag=UserAccessPermissions.PRINT | UserAccessPermissions.EXTRACT)
    source = tmp_path / 'encrypted.pdf'
    with source.open('wb') as handle:
        writer.write(handle)
    before = source.read_bytes()
    original = PdfReader(source)
    assert original.decrypt('') == 1
    encryption_before = original.trailer['/Encrypt'].get_object()
    first_id = original.trailer['/ID'][0].original_bytes
    encoded = program_pdf_bytes(source, 0, b'BT /F1 12 Tf 20 150 Td (CHANGED) Tj ET')
    assert source.read_bytes() == before
    saved = PdfReader(BytesIO(encoded))
    assert saved.is_encrypted
    assert saved.decrypt('') == 1
    assert saved.trailer['/ID'][0].original_bytes == first_id
    encryption_after = saved.trailer['/Encrypt'].get_object()
    for key in ('/V', '/R', '/Length', '/O', '/U', '/P', '/CF', '/StmF', '/StrF', '/OE', '/UE', '/Perms'):
        assert encryption_after.get(key) == encryption_before.get(key), key
    assert PdfReader(BytesIO(encoded)).decrypt('original-owner-password') == 2
    assert PdfReader(BytesIO(encoded)).decrypt('wrong-password') == 0
    with pymupdf.open(source) as source_doc, pymupdf.open(stream=encoded) as saved_doc:
        assert source_doc.permissions == saved_doc.permissions
        assert source_doc.metadata['encryption'] == saved_doc.metadata['encryption']
        assert 'CHANGED' in saved_doc[0].get_text()
        assert saved_doc[1].get_pixmap().samples == source_doc[1].get_pixmap().samples


def test_password_required_source_is_rejected(tmp_path):
    plain = _raw_pdf(tmp_path)
    writer = PdfWriter(clone_from=plain)
    writer.encrypt('nonempty-user-password', 'owner', algorithm='RC4-128')
    source = tmp_path / 'protected.pdf'
    with source.open('wb') as handle:
        writer.write(handle)
    with pytest.raises(PdfError, match='password required'):
        program_pdf_bytes(source, 0, b'q Q')


@pytest.mark.parametrize('use_source_as_destination', [False, True])
def test_publish_does_not_overwrite_and_cleans_temporary_files(tmp_path, use_source_as_destination):
    source = _raw_pdf(tmp_path)
    original = source.read_bytes()
    destination = source if use_source_as_destination else tmp_path / 'existing.pdf'
    if not use_source_as_destination:
        destination.write_bytes(b'existing result must survive')
    existing = destination.read_bytes()
    with pytest.raises((FileExistsError, PdfError)):
        publish_program(source, 0, b'q Q', destination)
    assert destination.read_bytes() == existing
    assert source.read_bytes() == original
    assert not list(tmp_path.glob('.program-*'))


def test_failed_verification_publishes_nothing(tmp_path):
    source = _raw_pdf(tmp_path)
    destination = tmp_path / 'rejected.pdf'

    def reject(document):
        assert len(document) == 1
        raise PdfError('deliberate verification failure')

    with pytest.raises(PdfError, match='verification failure'):
        publish_program(source, 0, b'q Q', destination, reject)
    assert not destination.exists()
    assert not list(tmp_path.glob('.program-*'))


def test_publish_verifies_serialized_pdf_before_creating_destination(tmp_path):
    source = _raw_pdf(tmp_path)
    destination = tmp_path / 'saved.pdf'
    seen = []

    def verify(document):
        assert not destination.exists()
        assert 'ACCEPTED' in document[0].get_text()
        seen.append(True)

    publish_program(source, 0, b'BT /F1 12 Tf 20 150 Td (ACCEPTED) Tj ET', destination, verify)
    assert seen == [True]
    assert destination.is_file()
    assert not list(tmp_path.glob('.program-*'))


def _new_font(writer):
    unicode_map = DecodedStreamObject()
    unicode_map.set_data(
        b'% new-font-cmap-reachability\n/CIDInit /ProcSet findresource begin\n'
        b'12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n'
        b'/CMapName /PEUnicode def /CMapType 2 def\n'
        b'1 begincodespacerange <00> <FF> endcodespacerange\n'
        b'1 beginbfrange <00> <FF> <0000> endbfrange\n'
        b'endcmap CMapName currentdict /CMap defineresource pop end end')
    return writer._add_object(DictionaryObject({
        NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Courier'), NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
        NameObject('/FirstChar'): NumberObject(32), NameObject('/LastChar'): NumberObject(126),
        NameObject('/Widths'): ArrayObject([NumberObject(600) for _ in range(95)]),
        NameObject('/ToUnicode'): writer._add_object(unicode_map),
    }))


def _shared_resources_pdf(tmp_path, kind):
    plain = _raw_pdf(tmp_path, shared=True)
    writer = PdfWriter(clone_from=plain)
    resources = DictionaryObject(writer.pages[0]['/Resources'].get_object())
    resources[NameObject('/Font')] = writer._add_object(DictionaryObject(resources['/Font'].get_object()))
    if kind in {'resources', 'inherited'}:
        resource_ref = writer._add_object(resources)
        if kind == 'inherited':
            writer.root_object['/Pages'][NameObject('/Resources')] = resource_ref
        for page in writer.pages:
            if kind == 'inherited':
                page.pop('/Resources', None)
            else:
                page[NameObject('/Resources')] = resource_ref
    else:
        for page in writer.pages:
            page[NameObject('/Resources')] = writer._add_object(DictionaryObject(resources))
    source = tmp_path / 'shared-resources.pdf'
    with source.open('wb') as stream:
        writer.write(stream)
    return source


@pytest.mark.parametrize('kind', ['resources', 'fonts', 'inherited'])
def test_new_font_uses_page_local_resource_containers_and_keeps_original_fonts(tmp_path, kind):
    source = _shared_resources_pdf(tmp_path, kind)
    original_bytes = source.read_bytes()
    encoded = program_pdf_bytes(source, 0, b'BT /PEF1 12 Tf 20 150 Td (NEW_FONT) Tj ET',
                                font_builders={'/PEF1': _new_font})
    assert source.read_bytes() == original_bytes
    reader = PdfReader(BytesIO(encoded))
    first = reader.pages[0]['/Resources']['/Font']
    other = reader.pages[1]['/Resources']['/Font']
    assert '/PEF1' in first and '/PEF1' not in other
    # The original font graph remains shared, rather than cloned or replaced.
    assert first.raw_get('/F1') == other.raw_get('/F1')
    assert any(b'new-font-cmap-reachability' in s for s in _all_decoded_streams(encoded))
    with pymupdf.open(source) as original, pymupdf.open(stream=encoded) as saved:
        retained = [f for f in font_fingerprints(saved, 0) if f[0][3] != 'PEF1']
        assert retained == font_fingerprints(original, 0)
        assert font_fingerprints(saved, 1) == font_fingerprints(original, 1)
        assert 'NEW_FONT' in saved[0].get_text()
        assert original[1].get_pixmap(dpi=144).samples == saved[1].get_pixmap(dpi=144).samples


@pytest.mark.parametrize('alias', ['/F1', 'PEF1', '/', '/F#31', '/Bad Name', '/日本語'])
def test_font_alias_collision_or_invalid_name_fails_before_builder_and_publication(tmp_path, alias):
    source = _raw_pdf(tmp_path)
    original_bytes = source.read_bytes()
    output = tmp_path / 'must-not-exist.pdf'
    calls = []

    def builder(writer):
        calls.append(True)
        return _new_font(writer)

    with pytest.raises(PdfError, match='alias'):
        publish_program(source, 0, b'q Q', output, font_builders={alias: builder})
    assert not calls
    assert not output.exists()
    assert source.read_bytes() == original_bytes
    assert not list(tmp_path.glob('.program-*'))


@pytest.mark.parametrize('invalid_kind', ['direct', 'foreign_root', 'foreign_child', 'not_font'])
def test_builder_must_return_an_owned_indirect_font_graph(tmp_path, invalid_kind):
    source = _raw_pdf(tmp_path)
    output = tmp_path / 'must-not-exist.pdf'
    foreign = PdfWriter()

    def builder(writer):
        if invalid_kind == 'foreign_root':
            return _new_font(foreign)
        reference = _new_font(writer)
        if invalid_kind == 'direct':
            return reference.get_object()
        if invalid_kind == 'foreign_child':
            reference.get_object()[NameObject('/ToUnicode')] = foreign._add_object(DecodedStreamObject())
        elif invalid_kind == 'not_font':
            reference.get_object()[NameObject('/Type')] = NameObject('/XObject')
        return reference

    with pytest.raises(PdfError, match='font'):
        publish_program(source, 0, b'q Q', output, font_builders={'/PEF1': builder})
    assert not output.exists()


def test_new_font_graph_is_encrypted_with_original_credentials_and_permissions(tmp_path):
    plain = _shared_resources_pdf(tmp_path, 'resources')
    writer = PdfWriter(clone_from=plain)
    writer.encrypt('', 'font-owner-password', algorithm='RC4-128',
                   permissions_flag=UserAccessPermissions.PRINT | UserAccessPermissions.EXTRACT)
    source = tmp_path / 'encrypted-font-source.pdf'
    with source.open('wb') as handle:
        writer.write(handle)
    original_bytes = source.read_bytes()
    output = tmp_path / 'encrypted-font-output.pdf'
    publish_program(source, 0, b'BT /PEF1 12 Tf 20 150 Td (NEW_FONT) Tj ET', output,
                    font_builders={'/PEF1': _new_font})
    assert source.read_bytes() == original_bytes
    saved = PdfReader(output)
    assert saved.is_encrypted and saved.decrypt('font-owner-password') == 2
    assert '/PEF1' in saved.pages[0]['/Resources']['/Font']
    assert '/PEF1' not in saved.pages[1]['/Resources']['/Font']
    assert b'new-font-cmap-reachability' not in output.read_bytes()
    assert any(b'new-font-cmap-reachability' in s for s in _all_decoded_streams(output.read_bytes()))
    with pymupdf.open(source) as original, pymupdf.open(output) as changed:
        assert original.permissions == changed.permissions
        assert original.metadata['encryption'] == changed.metadata['encryption']
        assert 'NEW_FONT' in changed[0].get_text()
        assert original[1].get_pixmap().samples == changed[1].get_pixmap().samples
