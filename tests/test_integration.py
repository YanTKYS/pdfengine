"""Exercise real PDF input, editable output and pixels outside the edited region."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pymupdf
import pytest
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from examples.demo import CASES, create_source
from pdfeditor.engine import edit_pdf, inspect_pdf


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("pdf") / "original.pdf"
    create_source(path)
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rendered_region(page, clip):
    return page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), clip=pymupdf.Rect(*clip)).samples


@pytest.mark.parametrize("index,case", list(enumerate(CASES)))
def test_required_cases(source, tmp_path, index, case):
    before_hash = digest(source)
    output = tmp_path / f"{case['id']}.pdf"
    report = edit_pdf(source, output, page=index + 1, find="".join(case["lines"]), replacement=case["after"])
    assert digest(source) == before_hash
    assert report["width_source"] == "enclosing-rectangle-with-symmetric-inset"
    assert report["width"] == 260
    assert all(line["width"] <= report["width"] + .01 for line in report["lines"])
    assert report["font"]["preserved"]
    assert report["font"]["source"].startswith("embedded:")
    if case["id"] == "A":
        assert report["old_line_count"] == report["new_line_count"] == 1
    elif case["id"] in {"B", "D"}:
        assert report["old_line_count"] == 1 < report["new_line_count"]
    elif case["id"] == "C":
        assert report["old_line_count"] == 3 and report["new_line_count"] == 1
        assert report["new_bbox"]["y1"] < report["old_bbox"]["y1"]
    with pymupdf.open(source) as original, pymupdf.open(output) as edited:
        assert len(original) == len(edited) == 5
        area = pymupdf.Rect(65, 136, 340, 321)
        extracted = edited[index].get_text(clip=area).replace("\n", "")
        assert extracted == case["after"]
        assert "\ufffd" not in edited[index].get_text()
        for line in edited[index].get_text("dict", clip=area)["blocks"][0]["lines"]:
            assert line["bbox"][2] <= 332.05
        # Raster invariance across every untouched page and both neighbors on edited page.
        for page in range(5):
            if page != index:
                assert rendered_region(original[page], (0, 0, 595, 500)) == rendered_region(edited[page], (0, 0, 595, 500))
        for clip in ((360, 120, 550, 290), (40, 340, 560, 450), (0, 0, 595, 130)):
            assert rendered_region(original[index], clip) == rendered_region(edited[index], clip)
        # Existing pale background is still present where old text was removed.
        assert len(original[index].get_drawings()) == len(edited[index].get_drawings())


def test_independent_reportlab_cid_pdf_font_fallback(tmp_path):
    source, output = tmp_path / "reportlab.pdf", tmp_path / "edited.pdf"
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    document = canvas.Canvas(str(source), pagesize=(400, 400))
    document.setFont("HeiseiMin-W3", 12)
    document.drawString(50, 320, "申請期限は9月10日です。")
    document.save()
    report = edit_pdf(source, output, find="申請期限は9月10日です。",
                      replacement="申請書類の提出期限は10月15日までです。", width=150)
    assert not report["font"]["preserved"]
    assert any("not embedded" in reason for reason in report["font"]["reasons"])
    assert report["new_line_count"] >= 2
    with pymupdf.open(output) as edited:
        assert edited[0].get_text().replace("\n", "") == report["after"]


def test_substring_reflows_complete_paragraph(source, tmp_path):
    report = edit_pdf(source, tmp_path / "part.pdf", page=2, find="9月10日",
                      replacement="10月15日まで")
    assert report["after"] == "申請期限は10月15日までです。"
    assert report["new_line_count"] == 1


def test_empty_replacement_removes_region(source, tmp_path):
    report = edit_pdf(source, tmp_path / "delete.pdf", find="申請", replacement="")
    assert report["new_line_count"] == 0
    with pymupdf.open(tmp_path / "delete.pdf") as document:
        assert not document[0].get_text(clip=pymupdf.Rect(65, 136, 340, 321)).strip()


def test_reverse_drawing_order_is_reconstructed(tmp_path):
    source = tmp_path / "reverse.pdf"
    document = pymupdf.open()
    page = document.new_page()
    for position, char in reversed(list(enumerate("Editable text"))):
        page.insert_text((72 + position * 7.2, 100), char, fontsize=12, fontname="cour")
    document.save(source)
    document.close()
    model = inspect_pdf(source)
    assert model["boxes"][0]["text"] == "Editable text"
    report = edit_pdf(source, tmp_path / "reverse-out.pdf", find="Editable text", replacement="A longer editable text", width=110)
    assert report["new_line_count"] == 2


def test_cli_inspect_and_edit(source, tmp_path):
    metadata = tmp_path / "model.json"
    inspect = subprocess.run([sys.executable, "-m", "pdfeditor", "inspect", str(source), "--json", str(metadata)],
                             capture_output=True, encoding="utf-8")
    assert inspect.returncode == 0, inspect.stderr
    boxes = json.loads(metadata.read_text(encoding="utf-8"))["boxes"]
    box = next(b for b in boxes if b["text"] == "申請")
    assert box["id"].startswith("p1-")
    output, report = tmp_path / "cli.pdf", tmp_path / "report.json"
    result = subprocess.run([sys.executable, "-m", "pdfeditor", "edit", str(source), str(output),
                             "--box", box["id"], "--text", "申請書類", "--report", str(report)],
                            capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert json.loads(report.read_text(encoding="utf-8"))["new_line_count"] == 1
