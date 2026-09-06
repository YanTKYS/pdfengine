"""Exercise failure paths against real PDFs and verify byte-for-byte invariance.

These fixtures are authored directly with MuPDF. No editor implementation or
mock supplies their geometry, fonts, links, annotations, or paint properties.
"""

from pathlib import Path
import os

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.engine import edit_pdf
from pdfeditor.layout import LayoutError


def source_pdf(tmp_path: Path, decorate=None, *, target="Target", render_mode=0) -> Path:
    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=300)
        page.insert_text((40, 60), target, fontsize=12, fontname="helv", render_mode=render_mode)
        if decorate is not None:
            decorate(page)
        document.save(source)
    return source


def reject_unchanged(source: Path, match: str, *, exception=PdfError, **kwargs):
    output = source.parent / "rejected.pdf"
    before = source.read_bytes()
    arguments = {"find": "Target", "replacement": "Replacement", "width": 180}
    arguments.update(kwargs)
    with pytest.raises(exception, match=match):
        edit_pdf(source, output, **arguments)
    assert source.read_bytes() == before, "Rejected edits must never modify source bytes."
    assert not output.exists(), "Rejected edits must not publish an output PDF."
    assert not list(source.parent.glob(".pdfeditor-*.pdf")), "No temporary output may remain."


def test_same_input_and_output_is_rejected_without_modifying_source(tmp_path):
    source = source_pdf(tmp_path)
    before = source.read_bytes()
    with pytest.raises(PdfError, match="different files"):
        edit_pdf(source, source, find="Target", replacement="Changed")
    assert source.read_bytes() == before
    assert set(tmp_path.iterdir()) == {source}


def test_existing_output_is_preserved(tmp_path):
    source = source_pdf(tmp_path)
    output = tmp_path / "existing.pdf"
    output.write_bytes(b"existing output must survive")
    before = source.read_bytes()
    with pytest.raises(PdfError, match="already exists"):
        edit_pdf(source, output, find="Target", replacement="Changed")
    assert source.read_bytes() == before
    assert output.read_bytes() == b"existing output must survive"
    assert set(tmp_path.iterdir()) == {source, output}


def test_hard_link_to_source_is_not_treated_as_a_distinct_output(tmp_path):
    source = source_pdf(tmp_path)
    alias = tmp_path / "same-file.pdf"
    os.link(source, alias)
    before = source.read_bytes()
    with pytest.raises(PdfError, match="different files"):
        edit_pdf(source, alias, find="Target", replacement="Changed")
    assert source.read_bytes() == before
    assert alias.read_bytes() == before
    assert set(tmp_path.iterdir()) == {source, alias}


def test_ambiguous_text_regions_are_not_silently_chosen(tmp_path):
    source = source_pdf(tmp_path, lambda page: page.insert_text((40, 180), "Target", fontsize=12))
    reject_unchanged(source, "found 2")


def test_repeated_substring_in_one_region_requires_disambiguation(tmp_path):
    source = source_pdf(tmp_path, target="Target Target")
    reject_unchanged(source, "more than once")


def test_mixed_style_region_is_rejected_before_rewriting(tmp_path):
    x = 40 + pymupdf.get_text_length("Target", fontname="helv", fontsize=12)
    source = source_pdf(tmp_path, lambda page: page.insert_text((x, 60), " Bold", fontname="hebo", fontsize=12))
    reject_unchanged(source, "mixed styles")


def test_page_rotation_requires_a_supported_coordinate_writer(tmp_path):
    source = source_pdf(tmp_path, lambda page: page.set_rotation(90))
    reject_unchanged(source, "rotated pages")


def test_rotated_glyph_is_inspectable_but_not_horizontally_rewritten(tmp_path):
    source = source_pdf(tmp_path, lambda page: page.insert_text((200, 180), "V", rotate=90, fontsize=12))
    reject_unchanged(source, "rotated, vertical or transformed", find="V")


def test_expansion_cannot_overwrite_another_text_region(tmp_path):
    source = source_pdf(tmp_path, lambda page: page.insert_text((40, 100), "Protected text", fontsize=12))
    reject_unchanged(source, "another text region", replacement="First\nSecond\nThird")


@pytest.mark.parametrize("graphic", ["stroke", "fill"])
def test_expansion_cannot_overwrite_a_graphic(tmp_path, graphic):
    def decorate(page):
        if graphic == "stroke":
            page.draw_line((30, 94), (220, 94), color=(0, 0, 0), width=2)
        else:
            page.draw_rect((30, 87, 220, 110), color=None, fill=(0.2, 0.4, 0.6))

    source = source_pdf(tmp_path, decorate)
    reject_unchanged(source, "collides with .*vector", replacement="First\nSecond\nThird")


def test_expansion_cannot_leave_its_existing_background(tmp_path):
    source = source_pdf(tmp_path, lambda page: page.draw_rect(
        (30, 40, 220, 70), color=None, fill=(0.9, 0.9, 0.9), overlay=False))
    reject_unchanged(source, "outside its existing background", replacement="First\nSecond")


def test_expansion_cannot_overwrite_an_image(tmp_path):
    def decorate(page):
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
        pixmap.clear_with(180)
        page.insert_image((30, 87, 220, 110), pixmap=pixmap, keep_proportion=False)

    source = source_pdf(tmp_path, decorate)
    reject_unchanged(source, "collides with image", replacement="First\nSecond\nThird")


def test_pending_redaction_elsewhere_is_never_applied_as_a_side_effect(tmp_path):
    def decorate(page):
        page.insert_text((40, 180), "Keep this text", fontsize=12)
        page.add_redact_annot((38, 165, 145, 185), fill=(0, 0, 0))

    source = source_pdf(tmp_path, decorate)
    reject_unchanged(source, "pending redactions")


def test_link_intersecting_the_edited_region_is_rejected(tmp_path):
    source = source_pdf(tmp_path, lambda page: page.insert_link({
        "kind": pymupdf.LINK_URI, "from": pymupdf.Rect(38, 45, 90, 64),
        "uri": "https://example.com/"}))
    reject_unchanged(source, "intersects a link")


def test_annotation_intersecting_the_edited_region_is_rejected(tmp_path):
    source = source_pdf(tmp_path, lambda page: page.add_rect_annot((38, 45, 90, 64)))
    reject_unchanged(source, "intersects an annotation")


def test_widget_intersecting_the_edited_region_is_rejected(tmp_path):
    def decorate(page):
        widget = pymupdf.Widget()
        widget.field_name = "review"
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(38, 45, 130, 65)
        page.add_widget(widget)

    source = source_pdf(tmp_path, decorate)
    reject_unchanged(source, "intersects a form widget")


@pytest.mark.parametrize("render_mode", [1, 3])
def test_stroked_or_invisible_text_does_not_become_filled_visible_text(tmp_path, render_mode):
    source = source_pdf(tmp_path, render_mode=render_mode)
    reject_unchanged(source, "invisible, stroked, transparent")


def test_transparent_text_does_not_become_opaque_text(tmp_path):
    source = source_pdf(tmp_path, lambda page: page.insert_text(
        (40, 180), "Faded", fontsize=12, fill_opacity=0.4))
    reject_unchanged(source, "invisible, stroked, transparent", find="Faded")


def test_clipped_text_is_not_repainted_outside_its_original_clip(tmp_path):
    def decorate(page):
        document = page.parent
        for xref in page.get_contents():
            original = document.xref_stream(xref)
            # PDF uses bottom-left coordinates here: only the left 15 pt of
            # the text at the 60 pt top-left baseline remain visible.
            document.update_stream(xref, b"q\n40 230 15 30 re W n\n" + original + b"\nQ")

    source = source_pdf(tmp_path, decorate)
    with pymupdf.open(source) as document:
        page = document[0]
        assert "".join(chr(char[0]) for span in page.get_texttrace() for char in span["chars"]) == "Target"
        assert any(drawing["type"] == "clip" for drawing in page.get_drawings(extended=True))
    reject_unchanged(source, "clipping")


def test_full_page_rectangular_clip_allows_an_ordinary_edit(tmp_path):
    def decorate(page):
        document = page.parent
        for xref in page.get_contents():
            original = document.xref_stream(xref)
            document.update_stream(xref, b"q\n0 0 400 300 re W n\n" + original + b"\nQ")

    source = source_pdf(tmp_path, decorate)
    before = source.read_bytes()
    output = tmp_path / "page-clip-edited.pdf"
    report = edit_pdf(source, output, find="Target", replacement="Changed", width=180)
    assert report["after"] == "Changed"
    assert source.read_bytes() == before
    with pymupdf.open(output) as document:
        assert document[0].get_text().strip() == "Changed"
    assert not list(tmp_path.glob(".pdfeditor-*.pdf"))


def test_encrypted_document_requires_decryption_before_editing(tmp_path):
    source = tmp_path / "encrypted.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 60), "Target", fontsize=12)
        document.save(source, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                      user_pw="test-reader", owner_pw="test-owner")
    reject_unchanged(source, "requires decryption")


@pytest.mark.parametrize("encryption", [pymupdf.PDF_ENCRYPT_RC4_128, pymupdf.PDF_ENCRYPT_AES_256])
def test_empty_reader_password_preserves_encryption_and_permissions(tmp_path, encryption):
    source = tmp_path / "restricted.pdf"
    output = tmp_path / "edited.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 60), "Target", fontsize=12)
        document.save(source, encryption=encryption, user_pw="", owner_pw="test-owner",
                      permissions=pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_MODIFY)
    before = source.read_bytes()
    with pymupdf.open(source) as original:
        expected_permissions = original.permissions
        expected_encryption = original.metadata["encryption"]
    edit_pdf(source, output, find="Target", replacement="Changed", width=180)
    assert source.read_bytes() == before
    with pymupdf.open(output) as edited:
        assert not edited.needs_pass
        assert edited.metadata["encryption"] == expected_encryption
        assert edited.permissions == expected_permissions
        assert edited.authenticate("test-owner") & 4
        assert edited[0].get_text().strip() == "Changed"


def test_insufficient_vertical_space_does_not_publish_partial_output(tmp_path):
    source = source_pdf(tmp_path)
    reject_unchanged(source, "vertical space", exception=LayoutError,
                     replacement="First\nSecond\nThird", max_height=20)


@pytest.mark.parametrize("width", [0, -1, float("nan"), float("inf")])
def test_invalid_region_width_is_rejected_without_output(tmp_path, width):
    source = source_pdf(tmp_path)
    reject_unchanged(source, "positive and finite", width=width)


def test_page_boundary_is_enforced_when_region_grows_downwards(tmp_path):
    source = source_pdf(tmp_path)
    reject_unchanged(source, "vertical space", exception=LayoutError,
                     replacement="\n".join(["Line"] * 20))
