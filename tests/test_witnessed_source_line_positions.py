"""A saved line report is only a candidate for exact current-operator replay."""
from copy import copy, deepcopy
from io import BytesIO
import shutil
import subprocess

from fontTools.ttLib import TTFont
from PIL import Image, ImageChops
import pymupdf
import pytest

from pdfeditor.attributed import EditUnit, SourceParagraph, apply_edits, inspect_paragraph
from pdfeditor.content_stream import ContentPage
from pdfeditor.backend import PdfError
from pdfeditor.editable import open_editable, write_editable
from pdfeditor.explicit_reflow import matrix_operator
from pdfeditor.paragraph import ParagraphShaper, plan_paragraph, source_line_candidates
from pdfeditor.layout import LayoutError
from pdfeditor.logical_element import paragraph_from_snapshot
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


@pytest.mark.parametrize('option', ['x', 'width', 'first_line_indent', 'min_line_height'])
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


def test_move_up_save_reopen_move_down_preserves_current_witnesses(source_case, tmp_path):
    source, snapshot, lines = source_case
    binding = dict(layout=OPTIONS, physical_layout=dict(provenance='generated-by-pdfengine', lines=lines))
    moved_options = dict(OPTIONS, baseline=116.41998291015625)
    candidates = source_line_candidates(binding, moved_options, [])
    assert candidates == lines
    up, side = tmp_path / 'up.pdf', tmp_path / 'up.json'
    write_editable(source, up, side, snapshot, [], _preserve_source_line_gaps=True,
                   _source_line_layout=candidates, **moved_options)
    opened = open_editable(up, side)
    assert opened['status'] == 'restored'
    current = opened['state']
    moved = [line['baseline'] for line in current['physical_layout']['lines']]
    offsets = [b - BASELINES[0] for b in BASELINES]
    assert moved == [moved_options['baseline'] + d for d in offsets]
    assert [b - moved[0] + BASELINES[0] for b in moved] == BASELINES
    paragraph = paragraph_from_snapshot(up, current['paragraph'])
    try:
        units = apply_edits(paragraph, current['paragraph'], [])
        stale = ParagraphShaper(paragraph, units, {}, source_lines=lines)
        assert stale.source_baselines is None  # Previous-revision lines have no authority.
        stale.close()
        fresh = ParagraphShaper(paragraph, units, {}, source_lines=current['physical_layout']['lines'])
        assert [BASELINES[0] + d for d in fresh.source_offsets] == BASELINES
        fresh.close()
    finally:
        paragraph.close()
    returned, final_side = tmp_path / 'returned.pdf', tmp_path / 'returned.json'
    write_editable(up, returned, final_side, current['paragraph'], [], previous_state=current,
                   _preserve_source_line_gaps=True,
                   _source_line_layout=source_line_candidates(current, OPTIONS, []), **OPTIONS)
    final = open_editable(returned, final_side)
    assert final['status'] == 'restored'
    assert [line['baseline'] for line in final['state']['physical_layout']['lines']] == BASELINES
    assert matrices(returned) == matrices(source)
    with pymupdf.open(source) as a, pymupdf.open(returned) as b:
        assert [c for s in a[0].get_texttrace() for c in s['chars']] == [
            c for s in b[0].get_texttrace() for c in s['chars']]
        assert a[0].get_pixmap(dpi=144).samples == b[0].get_pixmap(dpi=144).samples
    renderer = shutil.which('pdftoppm')
    if not renderer:
        pytest.skip('Poppler is required for moved-source roundtrip comparison')
    for label, path in [('original-position', source), ('returned-position', returned)]:
        subprocess.run([renderer, '-f', '1', '-l', '1', '-singlefile', '-r', '144', '-png',
                        str(path), str(tmp_path / label)], check=True, capture_output=True)
    with Image.open(tmp_path / 'original-position.png') as a, Image.open(tmp_path / 'returned-position.png') as b:
        assert ImageChops.difference(a.convert('RGB'), b.convert('RGB')).getbbox() is None


def test_changed_retained_style_cannot_reuse_relative_positions(source_case):
    source, snapshot, lines = source_case
    paragraph = SourceParagraph(source, snapshot['selection'])
    try:
        original = paragraph.units[0].style_id
        paragraph.styles['different'] = copy(paragraph.styles[original])
        paragraph.styles['different'].size += 1
        units = [EditUnit(u.text, 'different', u) for u in paragraph.units]
        shaper = ParagraphShaper(paragraph, units, {}, source_lines=lines)
        assert shaper.source_offsets is None
        shaper.close()
    finally:
        paragraph.close()


@pytest.mark.parametrize('overrides', [dict(width=37), dict(min_line_height=26), dict(max_bottom=300)])
def test_translated_candidates_do_not_override_wrap_height_or_bounds(source_case, overrides):
    source, snapshot, lines = source_case
    options = dict(OPTIONS, baseline=116.41998291015625, **overrides)
    with pytest.raises((PdfError, LayoutError)):
        plan_paragraph(source, snapshot, [], _preserve_source_line_gaps=True,
                       _source_line_layout=lines, **options)


def test_translation_that_cannot_represent_relative_spacing_refuses(source_case):
    source, snapshot, lines = source_case
    paragraph = SourceParagraph(source, snapshot['selection'])
    shaper = ParagraphShaper(paragraph, apply_edits(paragraph, snapshot, []), {}, source_lines=lines)
    try:
        options = dict(OPTIONS, baseline=2**40, max_bottom=None)
        layout = layout_attributed(shaper.text, shape=shaper.shape, **options,
            source_line_gap=shaper.source_line_gap, empty_ascent=10, empty_descent=2)
        with pytest.raises(PdfError, match='candidate differs from the final output layout'):
            shaper.verify_source_layout(layout)
    finally:
        shaper.close(); paragraph.close()


def test_empty_saved_candidates_keep_empty_noop_restorable(tmp_path):
    from pdfeditor.editable import edit_document
    from test_empty_element import empty

    _, _, source, side, _ = empty(tmp_path)
    state = open_editable(source, side)['state']
    candidates = source_line_candidates(state, state['layout'], [])
    assert candidates == []
    paragraph = paragraph_from_snapshot(source, state['paragraph'])
    shaper = ParagraphShaper(paragraph, [], {}, source_lines=candidates)
    try:
        assert shaper.source_baselines is None
    finally:
        shaper.close(); paragraph.close()
    output, output_side = tmp_path / 'empty-noop.pdf', tmp_path / 'empty-noop.json'
    report = edit_document(source, side, output, output_side, [],
        _preserve_source_line_gaps=True, _source_line_layout=candidates)
    assert report['glyph_plan'] == []
    assert open_editable(output, output_side)['status'] == 'restored'
    with pymupdf.open(source) as before, pymupdf.open(output) as after:
        assert len(before) == len(after)
        assert all(a.get_pixmap().samples == b.get_pixmap().samples
                   for a, b in zip(before, after))
