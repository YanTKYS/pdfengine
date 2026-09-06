"""Manual selections preserve observed identity; reflow never invents width."""

from copy import deepcopy
import json

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.engine import edit_pdf, inspect_pdf
from pdfeditor.model import WidthConstraint, WidthUnknownError
from pdfeditor.selection import (
    adjust_selection, inspect_selection_source, make_selection, resolve_selection,
)


@pytest.fixture
def selection_pdf(tmp_path):
    source = tmp_path / "selection-source.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=300)
        page.insert_text((40, 60), "First", fontsize=12, fontname="helv")
        # A real geometric gap without a painted space. The selection preview
        # must not present an inferred space as an observed source glyph.
        x = 45 + pymupdf.get_text_length("First", fontsize=12, fontname="helv")
        page.insert_text((x, 60), "Bold", fontsize=12, fontname="hebo")
        page.insert_text((40, 78), "Second line", fontsize=12, fontname="helv")
        page.insert_text((40, 96), "Date 2026", fontsize=12, fontname="helv")
        other = document.new_page(width=400, height=300)
        other.insert_text((40, 60), "Other page has enough glyphs", fontsize=12, fontname="helv")
        document.save(source)
    return source


def test_lines_and_runs_are_independent_of_paragraph_box_scope(selection_pdf):
    catalog = inspect_selection_source(selection_pdf)
    assert [line["text"] for line in catalog["lines"]] == ["FirstBold", "Second line", "Date 2026"]
    assert len(catalog["lines"][0]["runs"]) == 2
    manifest = make_selection(selection_pdf, line_ids=["p1-l2"])
    selected = resolve_selection(selection_pdf, manifest)
    assert "".join(g.text for g in selected.glyphs) == "Second line"
    assert len(selected.lines) == 1
    assert all(g.source_order >= 0 for line in selected.lines for g in line.glyphs)
    assert selected.bbox.tuple() == tuple(catalog["lines"][1]["bbox"].values())
    assert selected.widths.observed_content_width == selected.bbox.width
    assert selected.widths.inferred_available_width is None
    assert selected.widths.explicitly_supplied_width is None


def test_add_and_remove_lines_keeps_only_the_confirmed_glyphs(selection_pdf):
    selected = make_selection(selection_pdf, line_ids=["p1-l1"], explicit_width=180)
    corrected = adjust_selection(selection_pdf, selected,
                                 add_lines=["p1-l2", "p1-l3"], remove_lines=["p1-l1", "p1-l3"])
    resolved = resolve_selection(selection_pdf, corrected)
    assert "".join(g.text for g in resolved.glyphs) == "Second line"
    assert resolved.widths.require_available_width() == 180
    assert corrected["glyph_ids"] == inspect_selection_source(selection_pdf)["lines"][1]["glyph_ids"]
    # Producing a corrected manifest does not mutate the caller's selection.
    assert selected["glyph_ids"] != corrected["glyph_ids"]


def test_run_and_individual_glyph_selection_roundtrip_json(selection_pdf):
    catalog = inspect_selection_source(selection_pdf)
    bold = catalog["lines"][0]["runs"][1]
    final_glyph = catalog["lines"][2]["glyph_ids"][-1]
    manifest = make_selection(selection_pdf, run_ids=[bold["id"]], glyph_ids=[final_glyph])
    resolved = resolve_selection(selection_pdf, json.loads(json.dumps(manifest)))
    assert "".join(g.text for g in resolved.glyphs) == "Bold6"
    assert {g.source_order for g in resolved.glyphs} == set(bold["glyph_ids"]) | {final_glyph}
    assert len(resolved.lines) == 2


def test_source_hash_rejects_same_layout_from_different_document(selection_pdf, tmp_path):
    manifest = make_selection(selection_pdf, line_ids=["p1-l2"])
    other = tmp_path / "different-bytes.pdf"
    with pymupdf.open(selection_pdf) as document:
        document.set_metadata({"title": "Changed source identity"})
        document.save(other)
    with pytest.raises(PdfError, match="SHA-256"):
        resolve_selection(other, manifest)


def test_page_change_cannot_reinterpret_an_existing_selection(selection_pdf):
    manifest = make_selection(selection_pdf, line_ids=["p1-l1"])
    manifest["page"] = 2
    with pytest.raises(PdfError, match="page|observation"):
        resolve_selection(selection_pdf, manifest)


@pytest.mark.parametrize("change", [
    {"glyph_ids": []}, {"glyph_ids": [-1]}, {"glyph_ids": [99999]},
    {"glyph_ids": [0, 0]}, {"glyph_ids": [True]}, {"page": 0},
    {"inferred_available_width": 200},
])
def test_invalid_or_falsely_inferred_selection_is_rejected(selection_pdf, change):
    manifest = make_selection(selection_pdf, line_ids=["p1-l2"])
    manifest.update(change)
    with pytest.raises(PdfError):
        resolve_selection(selection_pdf, manifest)


def test_user_can_correct_raw_glyph_ids_without_guessing_a_box(selection_pdf):
    manifest = make_selection(selection_pdf, line_ids=["p1-l2"])
    corrected = deepcopy(manifest)
    corrected["glyph_ids"] = corrected["glyph_ids"][:6]
    assert "".join(g.text for g in resolve_selection(selection_pdf, corrected).glyphs) == "Second"


def test_unknown_width_is_distinct_from_observed_geometry():
    constraint = WidthConstraint(24)
    assert constraint.source == "unknown"
    with pytest.raises(WidthUnknownError, match="observed content width is not"):
        constraint.require_available_width()
    assert constraint.with_explicit(180).require_available_width() == 180
    inferred = WidthConstraint(24, inferred_available_width=160)
    assert inferred.source == "inferred"
    assert inferred.require_available_width() == 160
    overridden = inferred.with_explicit(180)
    assert overridden.source == "explicit"
    assert overridden.require_available_width() == 180
    assert overridden.observed_content_width == 24
    assert overridden.inferred_available_width == 160


def test_unknown_width_rejects_reflow_without_publishing_output(selection_pdf, tmp_path):
    output = tmp_path / "edited.pdf"
    before = selection_pdf.read_bytes()
    with pytest.raises(WidthUnknownError, match="available width is unknown"):
        # The second page is uniform, so the unavailable width is the relevant
        # rejection rather than a style or selection ambiguity.
        edit_pdf(selection_pdf, output, page=2, find="Other page has enough glyphs",
                 replacement="Longer replacement text requires a confirmed composing width")
    assert selection_pdf.read_bytes() == before
    assert not output.exists()
    observed = inspect_pdf(selection_pdf, page=2)["boxes"][0]
    assert observed["observed_content_width"] > 0
    assert observed["inferred_available_width"] is None
    assert observed["explicitly_supplied_width"] is None
