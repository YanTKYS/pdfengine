"""Regressions discovered in external PDFs; originals are never regenerated."""
from pathlib import Path

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.engine import edit_pdf, inspect_pdf


CORPUS = Path(__file__).resolve().parents[1] / "evaluations/realpdf/corpus"


@pytest.mark.parametrize("name", ["word_kyoto_questions", "word_okinawa_procurement"])
def test_reused_subset_with_wrong_emitted_widths_never_publishes_output(tmp_path, name):
    source = CORPUS / f"{name}.pdf"
    if not source.exists():
        pytest.skip("external corpus not downloaded")
    before = source.read_bytes()
    box = next(b for b in inspect_pdf(source)["boxes"] if b["id"] == "p1-b4")
    output = tmp_path / "edited.pdf"
    with pytest.raises(PdfError, match="writer glyph positions"):
        # Explicitly constrain this historical writer regression to the old
        # occupied width. Unknown-width rejection is tested separately.
        edit_pdf(source, output, box_id=box["id"], replacement=box["text"],
                 width=box["observed_content_width"])
    assert source.read_bytes() == before
    assert not output.exists()
    assert not list(tmp_path.iterdir())


def test_external_empty_password_pdf_retains_security_and_other_pages(tmp_path):
    source = CORPUS / "word_niigata_hearing.pdf"
    if not source.exists():
        pytest.skip("external corpus not downloaded")
    output = tmp_path / "edited.pdf"
    before = source.read_bytes()
    edit_pdf(source, output, box_id="p1-b3", replacement="１ 素案の概要と説明会について", width=230)
    assert source.read_bytes() == before
    with pymupdf.open(source) as original, pymupdf.open(output) as edited:
        assert original.metadata["encryption"] == edited.metadata["encryption"]
        assert original.permissions == edited.permissions
        assert len(edited) == len(original)
        for index in range(1, len(original)):
            assert original[index].get_pixmap().samples == edited[index].get_pixmap().samples
