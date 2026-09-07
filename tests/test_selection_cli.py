"""CLI contracts for manual selection and the independently verified Stage 1 gate."""

import json
import subprocess
import sys

import pymupdf
import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject


def invoke(*args):
    return subprocess.run([sys.executable, "-m", "pdfeditor", *map(str, args)],
                          capture_output=True, encoding="utf-8")


def successful(*args):
    result = invoke(*args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def source(tmp_path):
    writer = PdfWriter()
    fonts = DictionaryObject()
    for key, name in (("F1", "Courier"), ("F2", "Courier-Bold")):
        fonts[NameObject("/" + key)] = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/" + name), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            NameObject("/FirstChar"): NumberObject(32), NameObject("/LastChar"): NumberObject(126),
            NameObject("/Widths"): ArrayObject([NumberObject(600) for _ in range(95)]),
        }))
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 1 0 0 1 20 150 Tm (AB) Tj /F2 12 Tf (CD) Tj ET "
                    b"BT /F1 12 Tf 1 0 0 1 20 130 Tm (EFGH) Tj ET "
                    b"BT /F1 12 Tf 1 0 0 1 20 110 Tm (IJKL) Tj ET")
    page = writer.add_blank_page(300, 200)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): fonts})
    page[NameObject("/Contents")] = writer._add_object(stream)
    path = tmp_path / "source.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.mark.parametrize("command", ["observe", "select", "replay", "edit-selected", "compose-selected"])
def test_new_commands_offer_help(command):
    result = invoke(command, "--help")
    assert result.returncode == 0
    assert command in result.stdout
    if command == "replay":
        assert "MuPDF" in result.stdout and "independent" in result.stdout
    if command == "edit-selected":
        assert "--stage1-report" in result.stdout


def test_observe_catalog_and_correct_selection_json(source, tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog = successful("observe", source, "--json", catalog_path)
    assert catalog == json.loads(catalog_path.read_text(encoding="utf-8"))
    assert [line["text"] for line in catalog["lines"]] == ["ABCD", "EFGH", "IJKL"]
    assert [run["text"] for run in catalog["lines"][0]["runs"]] == ["AB", "CD"]
    assert catalog["inferred_available_width"] is None

    manifest_path = tmp_path / "selection.json"
    preview = successful("select", source, manifest_path, "--run", "p1-l1-r2", "--glyph", "8", "--width", "180")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert preview["selection"] == manifest
    assert manifest["glyph_ids"] == [2, 3, 8]
    assert preview["preview"]["lines"] == ["CD", "I"]
    assert preview["preview"]["bbox"]["x1"] > preview["preview"]["bbox"]["x0"]

    corrected_path = tmp_path / "corrected.json"
    corrected = successful("select", source, corrected_path, "--from-selection", manifest_path,
                           "--add-line", "p1-l2", "--exclude-line", "p1-l1")
    assert corrected["selection"]["glyph_ids"] == [4, 5, 6, 7, 8]
    assert corrected["preview"]["lines"] == ["EFGH", "I"]
    assert corrected["preview"]["widths"]["explicitly_supplied_width"] == 180
    assert corrected["preview"]["widths"]["inferred_available_width"] is None
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_replay_cli_report_is_truthfully_mupdf_only_and_cannot_open_later_stages(source, tmp_path):
    before = source.read_bytes()
    manifest = tmp_path / "selection.json"
    successful("select", source, manifest, "--line", "p1-l2")
    output, removed, report_path = (tmp_path / name for name in ("replayed.pdf", "removed.pdf", "report.json"))
    report = successful("replay", source, output, "--selection", manifest,
                        "--removal-output", removed, "--report", report_path)
    assert report == json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mupdf_144dpi_all_pages_identical"]
    assert report["removal_audit"]["passed"] and report["replay_audit"]["passed"]
    assert report["stage1_passed"] is False
    assert report["poppler_stage1_passed"] is False
    assert "MuPDF only" in report["stage1_validation_scope"]
    with pymupdf.open(removed) as document:
        assert "EFGH" not in document[0].get_text()
        assert "ABCD" in document[0].get_text() and "IJKL" in document[0].get_text()
    for stage in (2, 3, 4):
        changed = tmp_path / f"stage-{stage}.pdf"
        text_file = tmp_path / f"stage-{stage}.txt"
        text_file.write_text("EFGH" if stage == 2 else "EF", encoding="utf-8-sig")
        result = invoke("edit-selected", source, changed, "--selection", manifest,
                        "--stage", stage, "--stage1-report", report_path, "--text-file", text_file)
        assert result.returncode == 2
        assert "passing Stage 1 report" in result.stderr
        assert not changed.exists()
    assert source.read_bytes() == before


def test_replay_output_path_collisions_are_rejected_before_publication(source, tmp_path):
    manifest = tmp_path / "selection.json"
    successful("select", source, manifest, "--line", "p1-l2")
    output = tmp_path / "collision.pdf"
    result = invoke("replay", source, output, "--selection", manifest, "--removal-output", output)
    assert result.returncode == 2
    assert "distinct" in result.stderr
    assert not output.exists()
    before = manifest.read_bytes()
    result = invoke("replay", source, output, "--selection", manifest, "--report", manifest)
    assert result.returncode == 2
    assert manifest.read_bytes() == before
    assert not output.exists()


def test_select_rejects_invalid_existing_manifest_and_cross_page_correction(source, tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    output = tmp_path / "selection.json"
    result = invoke("select", source, output, "--from-selection", empty, "--line", "p1-l2")
    assert result.returncode == 2
    assert "unsupported selection schema" in result.stderr
    assert not output.exists()
    successful("select", source, output, "--line", "p1-l2")
    correction = tmp_path / "wrong-page.json"
    result = invoke("select", source, correction, "--from-selection", output, "--page", "2")
    assert result.returncode == 2
    assert "retain its original page" in result.stderr
    assert not correction.exists()


def test_compose_cli_embeds_font_and_protects_inputs(source, tmp_path):
    font = tmp_path / 'provided.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    manifest = tmp_path / 'selection.json'
    successful('select', source, manifest, '--line', 'p1-l3', '--width', '180')
    output, report_path, removed = (tmp_path / name for name in ('composed.pdf', 'compose.json', 'removed.pdf'))
    text_file = tmp_path / 'replacement.txt'
    text_file.write_text('申請書類2026※', encoding='utf-8')
    report = successful('compose-selected', source, output, '--selection', manifest, '--font', font,
                        '--text-file', text_file, '--report', report_path, '--removal-output', removed)
    assert report['font_substituted'] is True
    assert report['independent_renderer_verified'] is False
    assert report == json.loads(report_path.read_text(encoding='utf-8'))
    with pymupdf.open(output) as doc:
        assert '申請書類2026※' in doc[0].get_text()
        assert 'IJKL' not in doc[0].get_text()
    before = output.read_bytes()
    result = invoke('compose-selected', source, output, '--selection', manifest, '--font', font, '--text', '受付')
    assert result.returncode == 2 and output.read_bytes() == before
    rejected = tmp_path / 'rejected.pdf'
    result = invoke('compose-selected', source, rejected, '--selection', manifest, '--font', font,
                    '--text', '受付', '--report', font)
    assert result.returncode == 2 and not rejected.exists()
