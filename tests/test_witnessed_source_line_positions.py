"""A saved line report is only a candidate for exact current-operator replay."""
from copy import deepcopy
from io import BytesIO
import shutil
import subprocess

from fontTools.ttLib import TTFont
from PIL import Image, ImageChops
import pymupdf
import pytest

from pdfeditor.attributed import SourceParagraph, apply_edits, inspect_paragraph
from pdfeditor.content_stream import ContentPage
from pdfeditor.backend import PdfError
from pdfeditor.editable import open_editable, write_editable
from pdfeditor.explicit_reflow import matrix_operator
from pdfeditor.paragraph import ParagraphShaper, source_line_candidates
from pdfeditor.rich_layout import layout_attributed
from pdfeditor.selection import make_selection
import page_flow_fixture as fx

BASELINES = [126.4, 151.95999755859376, 177.639990234375, 203.19998779296876,
             228.7599853515625, 254.43997802734376, 279.9999755859375,
             305.5900024414062, 331.2699951171875]
OPTIONS = dict(x=85.10399627685547, width=73, baseline=BASELINES[0],
               first_line_indent=0, min_line_height=25.55999755859375, max_bottom=400)


@pytest.fixture
def source_case(tmp_path):
    program = fx.build_font()
    font = TTFont(BytesIO(program)); gid = font.getGlyphID(font.getBestCmap()[ord('本')]); font.close()
    doc = pymupdf.open(); page = doc.new_page(width=595.32, height=841.92)
    page.insert_font(fontname='F1', fontbuffer=program)
    page.insert_text((85, 100), '本', fontname='F1')
    inverse = ~page.transformation_matrix
    commands = [b'BT /F1 12 Tf 0 Tc 0 Tw ']
    for baseline in BASELINES:
        for i in range(6):
            point = pymupdf.Point(OPTIONS['x'] + 12 * i, baseline) * inverse
            commands += [matrix_operator((1, 0, 0, 1, point.x, point.y)),
                         f'<{gid:04x}> Tj '.encode()]
    commands.append(b'ET')
    doc.update_stream(page.get_contents()[0], b''.join(commands))
    source = tmp_path / 'source.pdf'; doc.save(source); doc.close()
    snapshot = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(54)), explicit_width=73))
    lines = [dict(start=6*i, end=6*(i+1), baseline=b) for i, b in enumerate(BASELINES)]
    return source, snapshot, lines


def measured(source, snapshot, candidates, edits=None):
    paragraph = SourceParagraph(source, snapshot['selection'])
    shaper = ParagraphShaper(paragraph, apply_edits(paragraph, snapshot, edits or []), {}, source_lines=candidates)
    try:
        result = layout_attributed(shaper.text, shape=shaper.shape, **OPTIONS,
                                   source_line_gap=shaper.source_line_gap, empty_ascent=10, empty_descent=2)
        shaper.verify_source_layout(result)
        return shaper.source_baselines, [line.baseline for line in result.lines]
    finally:
        shaper.close(); paragraph.close()


def matrices(path):
    content = ContentPage(path, 1)
    try:
        return [(g.code, e.state.font.name, g.source_text_matrix)
                for e in content.events for g in e.chars]
    finally:
        content.close()


def test_exact_operator_proof_prevents_noop_line_drift(source_case, tmp_path):
    source, snapshot, lines = source_case
    # This is the old observation/clamp path: the same first baseline drifts
    # on later lines even though every current occurrence is unchanged.
    _, observed = measured(source, snapshot, None)
    assert observed[0] == BASELINES[0] and observed[6] != BASELINES[6]
    proven, exact = measured(source, snapshot, lines)
    assert proven == exact == BASELINES
    binding = dict(layout=OPTIONS, physical_layout=dict(provenance='generated-by-pdfengine', lines=lines))
    candidates = source_line_candidates(binding, OPTIONS, [])
    pdf, side = tmp_path / 'noop.pdf', tmp_path / 'noop.json'
    write_editable(source, pdf, side, snapshot, [], _preserve_source_line_gaps=True,
                   _source_line_layout=candidates, **OPTIONS)
    assert open_editable(pdf, side)['status'] == 'restored'
    assert matrices(pdf) == matrices(source)
    with pymupdf.open(source) as a, pymupdf.open(pdf) as b:
        assert a[0].get_pixmap(dpi=144).samples == b[0].get_pixmap(dpi=144).samples
    poppler = shutil.which('pdftoppm')
    if not poppler:
        pytest.skip('Poppler is required for source-line raster comparison')
    for label, path in [('before', source), ('after', pdf)]:
        subprocess.run([poppler, '-f', '1', '-l', '1', '-singlefile', '-r', '144', '-png',
                        str(path), str(tmp_path / label)], check=True, capture_output=True)
    with Image.open(tmp_path / 'before.png') as a, Image.open(tmp_path / 'after.png') as b:
        assert ImageChops.difference(a.convert('RGB'), b.convert('RGB')).getbbox() is None


@pytest.mark.parametrize('tamper', ['baseline', 'range', 'missing_line'])
def test_tampered_candidate_cannot_authorize_source_positions(source_case, tamper):
    source, snapshot, lines = source_case
    changed = deepcopy(lines)
    if tamper == 'baseline':
        changed[6]['baseline'] += .0001  # Below existing geometry tolerance; native Tm differs.
    elif tamper == 'range':
        changed[6]['start'] += 1
    else:
        changed.pop()
    proof, result = measured(source, snapshot, changed)
    assert proof is None
    assert result == measured(source, snapshot, None)[1]


@pytest.mark.parametrize('option', ['baseline', 'x', 'width', 'first_line_indent', 'min_line_height'])
def test_changed_layout_uses_normal_reflow(source_case, option):
    _, _, lines = source_case
    binding = dict(layout=OPTIONS, physical_layout=dict(provenance='generated-by-pdfengine', lines=lines))
    changed = dict(OPTIONS); changed[option] += 1
    assert source_line_candidates(binding, changed, []) is None


def test_edited_occurrences_cannot_use_noop_candidates(source_case):
    source, snapshot, lines = source_case
    edits = [dict(start=0, end=1, text='本')]
    binding = dict(layout=OPTIONS, physical_layout=dict(provenance='generated-by-pdfengine', lines=lines))
    assert source_line_candidates(binding, OPTIONS, edits) is None
    paragraph = SourceParagraph(source, snapshot['selection'])
    shaper = ParagraphShaper(paragraph, apply_edits(paragraph, snapshot, edits), {}, source_lines=lines)
    try:
        assert shaper.source_baselines is None
    finally:
        shaper.close(); paragraph.close()


def test_roundtripping_candidate_must_match_final_clamped_layout(source_case, tmp_path):
    source, snapshot, lines = source_case
    changed = deepcopy(lines)
    changed[1]['baseline'] -= 1e-9
    paragraph = SourceParagraph(source, snapshot['selection'])
    shaper = ParagraphShaper(paragraph, apply_edits(paragraph, snapshot, []), {}, source_lines=changed)
    try:
        # The perturbation maps to exactly the same serialized current Tm.
        assert shaper.source_baselines == [line['baseline'] for line in changed]
    finally:
        shaper.close(); paragraph.close()
    output, side = tmp_path / 'tampered.pdf', tmp_path / 'tampered.json'
    with pytest.raises(PdfError, match='candidate differs from the final output layout'):
        write_editable(source, output, side, snapshot, [], _preserve_source_line_gaps=True,
                       _source_line_layout=changed, **OPTIONS)
    assert not output.exists() and not side.exists()
