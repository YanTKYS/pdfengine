"""Real font tests for shaping, visible coverage and PDF resource consistency."""
from io import BytesIO
from pathlib import Path
import re

from fontTools import subset
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph
import pymupdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest

from pdfeditor.fonts import FontError
from pdfeditor.shaped_font import ShapedFont


JAPANESE = "提出期限 2026/09/30 ※①€"


@pytest.fixture(scope="module")
def cjk_bytes():
    return pymupdf.Font("cjk").buffer


@pytest.fixture(scope="module")
def cjk(cjk_bytes):
    return ShapedFont(cjk_bytes)


@pytest.fixture(scope="module")
def small_font(cjk_bytes):
    """Keep real outlines, with a small file for embedding-policy variations."""
    font = TTFont(BytesIO(cjk_bytes))
    worker = subset.Subsetter()
    worker.populate(text="AB C")
    worker.subset(font)
    output = BytesIO()
    font.save(output)
    return output.getvalue()


@pytest.fixture(scope="module")
def arial():
    path = Path("C:/Windows/Fonts/arial.ttf")
    if not path.is_file():
        pytest.skip("Windows Arial is unavailable; no external font is downloaded")
    return ShapedFont(path)


def changed_font(raw, change):
    font = TTFont(BytesIO(raw), recalcTimestamp=False)
    change(font)
    output = BytesIO()
    font.save(output)
    return output.getvalue()


def unicode_map(top):
    cmap = top["/ToUnicode"].get_data()
    pairs = {}
    for group in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for cid, text in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", group):
            pairs[int(cid, 16)] = bytes.fromhex(text.decode()).decode("utf-16-be")
    return pairs


def make_pdf(resource, run, size=20):
    """An independent positioned writer consumes the resource's nominal widths."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=120)
    font_ref = resource.build(writer)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/Probe"): font_ref})})
    commands = [f"BT /Probe {size} Tf ".encode()]
    x = 30.0
    for glyph in run.glyphs:
        origin = x + glyph.x_offset / resource.font.upem * size
        y = 60 + glyph.y_offset / resource.font.upem * size
        commands.append(f"1 0 0 1 {origin:.9f} {y:.9f} Tm <{resource.code(glyph).hex()}> Tj ".encode())
        x += glyph.advance / resource.font.upem * size
    commands.append(b"ET")
    content = DecodedStreamObject()
    content.set_data(b"".join(commands))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_kerning_changes_occurrence_advance_without_changing_nominal_glyph_width(arial):
    run = arial.shape("AVATAR")
    nominal = [arial.nominal_width(g.gid) for g in run.glyphs]
    assert run.advance < sum(nominal)
    same_letter = [g for g in run.glyphs if g.text == "A"]
    assert len({g.gid for g in same_letter}) == 1
    assert len({g.advance for g in same_letter}) > 1
    assert arial.nominal_width(same_letter[0].gid) == nominal[0]


def test_codepoint_clusters_preserve_original_decomposed_unicode(arial):
    run = arial.shape("Ae\u0301B")
    assert [g.cluster for g in run.glyphs] == [0, 1, 3]
    assert [g.text for g in run.glyphs] == ["A", "e\u0301", "B"]
    assert len(run.glyphs) == 3
    assert "".join(g.text for g in run.glyphs) == run.text


def test_new_japanese_digits_and_symbols_have_real_outlines(cjk):
    run = cjk.shape(JAPANESE)
    assert "".join(g.text for g in run.glyphs) == JAPANESE
    assert all(g.gid != 0 for g in run.glyphs)
    for glyph in run.glyphs:
        if glyph.text.isspace():
            continue
        bounds = cjk.ink(glyph.gid)
        assert bounds is not None
        assert bounds[0] < bounds[2] and bounds[1] < bounds[3]


def test_variable_font_uses_serialized_instance_for_shaping_and_subset_outlines():
    path = Path("C:/Windows/Fonts/NotoSansJP-VF.ttf")
    if not path.is_file():
        pytest.skip("Noto Sans JP variable font is unavailable; no external font is downloaded")
    font = ShapedFont(path, variations={"wght": 400})
    assert font.variations == {"wght": 400}
    assert "fvar" not in font.font
    serialized = TTFont(BytesIO(font.data), recalcTimestamp=False)
    serialized_glyphs = serialized.getGlyphSet()
    cmap = serialized.getBestCmap()
    run = font.shape(JAPANESE)
    assert [g.text for g in run.glyphs] == list(JAPANESE)
    assert [g.gid for g in run.glyphs] == [serialized.getGlyphID(cmap[ord(c)]) for c in JAPANESE]
    assert all(g.gid != 0 for g in run.glyphs)

    # Instancing leaves fractional coordinates in memory, whereas TrueType
    # serialization rounds them. The measured outline and the final embedded
    # subset must both use the serialized geometry that HarfBuzz consumes.
    resource = font.resource([run])
    embedded = TTFont(BytesIO(resource.program), recalcTimestamp=False)
    embedded_glyphs = embedded.getGlyphSet()
    assert "fvar" not in embedded
    for glyph in run.glyphs:
        original_name = serialized.getGlyphName(glyph.gid)
        subset_name = embedded.getGlyphName(glyph.gid)
        original_pen = DecomposingRecordingPen(serialized_glyphs)
        subset_pen = DecomposingRecordingPen(embedded_glyphs)
        serialized_glyphs[original_name].draw(original_pen)
        embedded_glyphs[subset_name].draw(subset_pen)
        assert font.outline(glyph.gid) == original_pen.value == subset_pen.value
        assert font.nominal_width(glyph.gid) == serialized["hmtx"][original_name][0] == embedded["hmtx"][subset_name][0]
        if not glyph.text.isspace():
            assert original_pen.value

    binary = make_pdf(resource, run)
    assert PdfReader(BytesIO(binary)).pages[0].extract_text() == JAPANESE
    with pymupdf.open(stream=binary, filetype="pdf") as document:
        actual = [g for span in document[0].get_texttrace() for g in span["chars"]]
        assert [g[1] for g in actual] == [g.gid for g in run.glyphs]
        x = 30.0
        for glyph, observed in zip(run.glyphs, actual):
            assert observed[2][0] == pytest.approx(x + glyph.x_offset / font.upem * 20, abs=.001)
            x += glyph.advance / font.upem * 20


def test_subset_pdf_codes_widths_and_embedded_gids_remain_consistent(cjk):
    run = cjk.shape(JAPANESE)
    resource = cjk.resource([run])
    binary = make_pdf(resource, run)
    reader = PdfReader(BytesIO(binary))
    top = reader.pages[0]["/Resources"]["/Font"]["/Probe"]
    child = top["/DescendantFonts"][0].get_object()
    assert top["/Encoding"] == "/Identity-H"
    assert child["/Subtype"] == "/CIDFontType2"
    ids = child["/CIDToGIDMap"].get_data()
    cmap = unicode_map(top)
    widths = child["/W"]
    first_cid, entries = int(widths[0]), widths[1]
    embedded = TTFont(BytesIO(child["/FontDescriptor"]["/FontFile2"].get_data()))
    embedded_glyphs = embedded.getGlyphSet()
    assert len(resource.program) < len(cjk.data)
    for glyph in run.glyphs:
        cid = int.from_bytes(resource.code(glyph), "big")
        assert int.from_bytes(ids[cid * 2:cid * 2 + 2], "big") == glyph.gid
        assert cmap[cid] == glyph.text
        width = embedded["hmtx"][embedded.getGlyphName(glyph.gid)][0]
        assert width == cjk.nominal_width(glyph.gid)
        assert float(entries[cid - first_cid]) == pytest.approx(width / cjk.upem * 1000)
        # Compare all contour commands, not merely the glyph's bounding box.
        source_pen = DecomposingRecordingPen(cjk.glyph_set)
        subset_pen = DecomposingRecordingPen(embedded_glyphs)
        cjk.glyph_set[cjk.font.getGlyphName(glyph.gid)].draw(source_pen)
        embedded_glyphs[embedded.getGlyphName(glyph.gid)].draw(subset_pen)
        assert source_pen.value == subset_pen.value
    assert reader.pages[0].extract_text() == JAPANESE
    with pymupdf.open(stream=binary, filetype="pdf") as document:
        actual = [g for s in document[0].get_texttrace() for g in s["chars"]]
        assert [g[1] for g in actual] == [g.gid for g in run.glyphs]
        expected_x = 30.0
        for planned, observed in zip(run.glyphs, actual):
            assert observed[2][0] == pytest.approx(expected_x + planned.x_offset / cjk.upem * 20, abs=.001)
            expected_x += planned.advance / cjk.upem * 20


def test_pdf_positions_follow_contextual_kerning_and_keep_nominal_widths(arial):
    run = arial.shape("AVATAR")
    resource = arial.resource([run])
    binary = make_pdf(resource, run, size=17)
    reader = PdfReader(BytesIO(binary))
    child = reader.pages[0]["/Resources"]["/Font"]["/Probe"]["/DescendantFonts"][0].get_object()
    first_cid, widths = child["/W"]
    with pymupdf.open(stream=binary, filetype="pdf") as document:
        actual = [g for s in document[0].get_texttrace() for g in s["chars"]]
        assert len(actual) == len(run.glyphs)
        x = 30.0
        for glyph, observed in zip(run.glyphs, actual):
            cid = int.from_bytes(resource.code(glyph), "big")
            assert float(widths[cid - int(first_cid)]) == pytest.approx(arial.nominal_width(glyph.gid) / arial.upem * 1000)
            assert observed[2][0] == pytest.approx(x + glyph.x_offset / arial.upem * 17, abs=.001)
            x += glyph.advance / arial.upem * 17
    assert reader.pages[0].extract_text() == "AVATAR"


def test_same_gid_can_preserve_two_different_unicode_cluster_spellings(arial):
    composed, decomposed = arial.shape("é"), arial.shape("e\u0301")
    a, b = composed.glyphs[0], decomposed.glyphs[0]
    assert a.gid == b.gid
    resource = arial.resource([composed, decomposed])
    assert resource.code(a) != resource.code(b)
    writer = PdfWriter()
    top = resource.build(writer).get_object()
    cmap = unicode_map(top)
    assert cmap[int.from_bytes(resource.code(a), "big")] == "é"
    assert cmap[int.from_bytes(resource.code(b), "big")] == "e\u0301"
    assert PdfReader(BytesIO(make_pdf(resource, decomposed))).pages[0].extract_text() == "e\u0301"


@pytest.mark.parametrize("flags", [0x2, 0x4, 0x200, 0x202])
def test_restricted_or_preview_only_or_bitmap_only_embedding_is_rejected(small_font, flags):
    raw = changed_font(small_font, lambda f: setattr(f["OS/2"], "fsType", flags))
    with pytest.raises(FontError, match="embedding"):
        ShapedFont(raw)


def test_no_subsetting_flag_keeps_the_entire_font(small_font):
    raw = changed_font(small_font, lambda f: setattr(f["OS/2"], "fsType", 0x108))
    font = ShapedFont(raw)
    resource = font.resource([font.shape("A")])
    embedded = TTFont(BytesIO(resource.program))
    assert font.no_subset is True
    assert embedded.getGlyphOrder() == font.font.getGlyphOrder()
    # B is absent from the requested text, but its outline must survive.
    name = embedded.getBestCmap()[ord("B")]
    assert embedded["glyf"][name].numberOfContours != 0
    assert "+" not in resource.basefont


def test_cmap_entry_and_nonzero_gid_do_not_make_an_empty_outline_valid(small_font):
    def blank_a(font):
        name = font.getBestCmap()[ord("A")]
        font["glyf"][name] = Glyph()
    raw = changed_font(small_font, blank_a)
    font = ShapedFont(raw)
    assert font.hb_font.get_nominal_glyph(ord("A")) > 0
    with pytest.raises(FontError, match="no visible outline"):
        font.shape("A")
    assert font.shape(" ").glyphs[0].text == " "


def test_missing_visible_character_is_rejected_instead_of_using_notdef(small_font):
    with pytest.raises(FontError, match="notdef"):
        ShapedFont(small_font).shape("提")


@pytest.mark.parametrize("text", ["abc\n", "abc\t", "abc\u202e", "שלום"])
def test_unimplemented_control_and_direction_semantics_are_rejected(arial, text):
    with pytest.raises(FontError):
        arial.shape(text)


def test_variable_instance_uses_serialized_outlines_for_shaping_and_subset():
    path = Path('C:/Windows/Fonts/NotoSansJP-VF.ttf')
    if not path.is_file():
        pytest.skip('Noto Sans JP variable font is unavailable')
    font = ShapedFont(path, variations={'wght':400})
    run = font.shape('提出期限ABC-123※')
    resource = font.resource([run])
    canonical = TTFont(BytesIO(font.data))
    saved = TTFont(BytesIO(resource.program))
    assert 'fvar' not in canonical and 'fvar' not in saved
    assert font.variations['wght'] == 400
    for glyph in run.glyphs:
        pens = []
        for instance in (canonical, saved):
            glyphs = instance.getGlyphSet()
            pen = DecomposingRecordingPen(glyphs)
            glyphs[instance.getGlyphName(glyph.gid)].draw(pen)
            pens.append(pen.value)
        assert pens[0] == pens[1] == font.outline(glyph.gid)
        assert font.hb_font.get_glyph_h_advance(glyph.gid) == font.nominal_width(glyph.gid)
