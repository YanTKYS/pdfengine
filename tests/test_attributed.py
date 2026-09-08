"""Source-linked paragraph edits: source paint survives inline new Unicode."""
from copy import deepcopy
import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject,
)

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.paragraph import edit_paragraph
from pdfeditor.replay import compare_glyphs, glyph_observations
from pdfeditor.selection import make_selection
from pdfeditor.shaped_font import ShapedFont


@pytest.fixture(scope="module")
def font():
    return ShapedFont(pymupdf.Font("cjk").buffer)


def source_pdf(tmp_path, program=None):
    writer = PdfWriter()
    resources = DictionaryObject()
    for alias, name in (("/Regular", "/Courier"), ("/Bold", "/Courier-Bold")):
        resources[NameObject(alias)] = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject(name),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            NameObject("/FirstChar"): NumberObject(32),
            NameObject("/LastChar"): NumberObject(126),
            NameObject("/Widths"): ArrayObject([NumberObject(600)] * 95),
        }))
    program = program or (
        b"BT /Bold 14 Tf 1 0 0 rg 1 0 0 1 20 200 Tm (HEAD ) Tj "
        b"/Regular 10 Tf 0 g (body ) Tj "
        b"/Bold 12 Tf 0 0 1 rg (TAIL) Tj "
        b"/Regular 10 Tf 0 g 160 0 Td (KEEP) Tj ET"
    )
    for i in range(2):
        page = writer.add_blank_page(320, 260)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): resources})
        stream = DecodedStreamObject()
        stream.set_data(program if i == 0 else b"BT /Regular 12 Tf 20 200 Td (OTHER PAGE) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    source = tmp_path / "source.pdf"
    writer.write(source)
    return source


def observe(path):
    with pymupdf.open(path) as doc:
        return glyph_observations(doc[0])


def snapshot(source, count=14, width=140, **kwargs):
    return inspect_paragraph(source, make_selection(
        source, glyph_ids=list(range(count)), explicit_width=width), **kwargs)


def compact_text(path):
    return "".join(PdfReader(path).pages[0].extract_text().split())


def test_inline_unicode_retains_source_bold_regular_size_color_and_other_objects(tmp_path, font):
    source = source_pdf(tmp_path)
    before_bytes, before = source.read_bytes(), observe(source)
    paragraph = snapshot(source)
    assert paragraph["text"] == "HEAD body TAIL"
    assert [(s["start"], s["end"], s["style_id"]) for s in paragraph["spans"]] == [
        (0, 5, "s0"), (5, 10, "s1"), (10, 14, "s2")]
    output, removed = tmp_path / "edited.pdf", tmp_path / "removed.pdf"
    report = edit_paragraph(source, output, paragraph,
        [{"start": 5, "end": 9, "text": "本文ABC※"}], fonts={"s1": font},
        max_bottom=150, removal_output=removed)
    assert source.read_bytes() == before_bytes
    assert compact_text(output) == "HEAD本文ABC※TAILKEEP"
    assert compact_text(removed) == "KEEP"
    assert report["retained_glyph_count"] == 10
    assert report["provided_font_glyph_count"] == 6
    assert len(report["fonts"]) == 1
    after = observe(output)
    assert compare_glyphs(before[14:], after[-4:])["passed"]
    for actual, planned in zip(after[:-4], report["glyph_plan"]):
        assert actual["origin"] == pytest.approx(planned["origin"], abs=.002)
        if planned["source_index"] is not None:
            original = before[planned["source_index"]]
            for key in ("unicode", "glyph_id", "font", "size", "color", "opacity", "paint_type"):
                assert actual[key] == original[key]
        else:
            assert actual["glyph_id"] == font.shape(actual["unicode"]).glyphs[0].gid
            assert actual["size"] == 10
            assert actual["color"] == [0, 0, 0]
    a, b = PdfReader(source), PdfReader(output)
    for alias in ("/Regular", "/Bold"):
        assert a.pages[0]["/Resources"]["/Font"][alias] == b.pages[0]["/Resources"]["/Font"][alias]
    assert a.pages[1].extract_text() == b.pages[1].extract_text()
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        assert a[1].get_pixmap().samples == b[1].get_pixmap().samples


def test_cross_style_edit_needs_explicit_inheritance_then_keeps_remaining_styles(tmp_path, font):
    source = source_pdf(tmp_path)
    paragraph = snapshot(source)
    output = tmp_path / "cross-style.pdf"
    edit = {"start": 3, "end": 7, "text": "追記"}
    with pytest.raises(PdfError, match="crossing style boundaries"):
        edit_paragraph(source, output, paragraph, [edit], fonts={"s0": font})
    assert not output.exists()
    report = edit_paragraph(source, output, paragraph, [dict(edit, style_id="s0")],
                            fonts={"s0": font}, max_bottom=150)
    added = [g for g in report["glyph_plan"] if g["source_index"] is None]
    assert "".join(g["unicode"] for g in added) == "追記"
    assert all(g["size"] == 14 and g["color"] == [1, 0, 0] for g in added)
    assert compact_text(output) == "HEA追記dyTAILKEEP"
    assert {g["style_id"] for g in report["glyph_plan"]} == {"s0", "s1", "s2"}


def test_no_edit_reuses_only_source_fonts_and_reconstructs_mixed_paint_exactly(tmp_path):
    source = source_pdf(tmp_path)
    output = tmp_path / "unchanged.pdf"
    report = edit_paragraph(source, output, snapshot(source), [])
    assert report["provided_font_glyph_count"] == 0
    assert report["fonts"] == {}
    assert compare_glyphs(observe(source), observe(output))["passed"]
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        for index in range(len(a)):
            assert a[index].get_pixmap(dpi=144).samples == b[index].get_pixmap(dpi=144).samples


def test_source_offsets_and_shared_font_provider_keep_two_different_styles(tmp_path, font):
    source = source_pdf(tmp_path)
    output = tmp_path / "two-edits.pdf"
    # Offsets refer to the source snapshot even when lengths change and the
    # caller supplies the later edit first. One new font serves two styles.
    report = edit_paragraph(source, output, snapshot(source), [
        {"start": 10, "end": 14, "text": "末尾", "font_id": "shared"},
        {"start": 0, "end": 4, "text": "見出し", "font_id": "shared"},
    ], fonts={"shared": font}, max_bottom=150)
    assert compact_text(output) == "見出しbody末尾KEEP"
    assert set(report["fonts"]) == {"shared"}
    retained = [g for g in report["glyph_plan"] if g["source_index"] is not None]
    assert "".join(g["unicode"] for g in retained) == " body "
    added = [g for g in report["glyph_plan"] if g["source_index"] is None]
    assert {g["font_resource"] for g in added} == {report["fonts"]["shared"]["resource"]}
    assert [(g["size"], g["color"]) for g in added[:3]] == [(14, [1, 0, 0])] * 3
    assert [(g["size"], g["color"]) for g in added[3:]] == [(12, [0, 0, 1])] * 2


@pytest.mark.parametrize("edits", [
    [{"start": 1, "end": 4, "text": ""}, {"start": 3, "end": 4, "text": ""}],
    [{"start": 5, "end": 5, "text": "A"}, {"start": 5, "end": 5, "text": "B"}],
    [{"start": -1, "end": 2, "text": ""}],
])
def test_invalid_edit_ranges_publish_nothing(tmp_path, font, edits):
    source = source_pdf(tmp_path)
    output = tmp_path / "invalid.pdf"
    with pytest.raises(PdfError, match="disjoint|grapheme boundaries"):
        edit_paragraph(source, output, snapshot(source), edits,
                        fonts={"s0": font, "s1": font, "s2": font})
    assert not output.exists()


def test_snapshot_is_source_bound_and_tampering_cannot_change_style(tmp_path, font):
    source = source_pdf(tmp_path)
    paragraph = snapshot(source)
    output = tmp_path / "stale.pdf"
    tampered = deepcopy(paragraph)
    tampered["styles"][0]["font_size"] = 99
    with pytest.raises(PdfError, match="snapshot changed"):
        edit_paragraph(source, output, tampered, [], fonts={"s0": font})
    assert not output.exists()
    source.write_bytes(source.read_bytes() + b"\n% changed source revision\n")
    with pytest.raises(PdfError, match="SHA-256"):
        edit_paragraph(source, output, paragraph, [])
    assert not output.exists()


def test_lengthening_wraps_then_deletion_reflows_and_reedit_uses_original_new_font(tmp_path, font):
    source = source_pdf(tmp_path)
    paragraph = snapshot(source, width=100)
    replacement = "本文を変更しました。新しい漢字と英数字ABCを入力し、文章を自然に折り返します。"
    first = tmp_path / "long.pdf"
    report = edit_paragraph(source, first, paragraph,
        [{"start": 5, "end": 9, "text": replacement}], fonts={"s1": font}, max_bottom=200)
    assert report["new_line_count"] > report["old_line_count"] == 1
    first_glyphs = observe(first)
    ids = list(range(len(first_glyphs) - 4))
    again = inspect_paragraph(first, make_selection(first, glyph_ids=ids, explicit_width=140))
    start = again["text"].index(replacement)
    second = tmp_path / "short.pdf"
    short = edit_paragraph(first, second, again,
        [{"start": start + 2, "end": start + len(replacement), "text": ""}], max_bottom=150)
    assert short["new_line_count"] == 1
    assert short["provided_font_glyph_count"] == 0
    assert short["fonts"] == {}
    assert compact_text(second) == "HEAD本文TAILKEEP"
    first_new = [g for g in first_glyphs if g["unicode"] in ("本", "文")][:2]
    second_new = [g for g in observe(second) if g["unicode"] in ("本", "文")]
    for a, b in zip(first_new, second_new):
        for key in ("font", "glyph_id", "unicode", "size", "color"):
            assert a[key] == b[key]


def test_deletion_across_all_source_styles_needs_no_font(tmp_path):
    source = source_pdf(tmp_path)
    paragraph = snapshot(source)
    output, removed = tmp_path / "empty.pdf", tmp_path / "removed.pdf"
    report = edit_paragraph(source, output, paragraph,
        [{"start": 0, "end": 14, "text": "", "style_id": "s0"}], removal_output=removed)
    assert report["new_line_count"] == 0
    assert report["glyph_plan"] == []
    assert report["fonts"] == {}
    assert compact_text(output) == compact_text(removed) == "KEEP"


def test_scaled_source_style_retains_physical_size_tracking_and_baseline_shift(tmp_path, font):
    source = source_pdf(tmp_path,
        b"q .75 0 0 .75 0 0 cm BT /Regular 16 Tf 2 Tc 80 Tz 3 Ts "
        b"1 0 0 1 30 240 Tm (ABCD) Tj 220 0 Td (KEEP) Tj ET Q")
    paragraph = snapshot(source, count=4, width=110)
    style = paragraph["styles"][0]
    assert style["font_size"] == 12
    assert style["tracking"] == pytest.approx(1.2)
    assert style["horizontal_scale"] == pytest.approx(.8)
    assert style["baseline_shift"] == pytest.approx(-2.25)
    output = tmp_path / "scaled.pdf"
    before = observe(source)
    report = edit_paragraph(source, output, paragraph,
        [{"start": 1, "end": 3, "text": "本文"}], fonts={"s0": font}, max_bottom=170)
    after = observe(output)
    assert compare_glyphs(before[4:], after[-4:])["passed"]
    assert compact_text(output) == "A本文DKEEP"
    assert all(g["size"] == 12 for g in report["glyph_plan"])
    assert all(g["origin"][1] == pytest.approx(before[0]["origin"][1], abs=.002)
               for g in report["glyph_plan"])
