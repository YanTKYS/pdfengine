"""Independent, page-streamed preservation checks for a real saved-flow history."""
from contextlib import ExitStack
import hashlib
from io import BytesIO
from pathlib import Path
import shutil
import subprocess

import pymupdf
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader

from pdfeditor.content_stream import ContentPage
from pdfeditor.operator_nesting import audit as nesting_audit
from pdfeditor.selection import source_sha
from pdfeditor.shaped_font import ShapedFont
from evaluations.story_flow.evaluate import annotation_fingerprint
from .continuation_edit import chars, outline


def _owned(state, number):
    return {gid for slot in state['slots'].values()
            if state['regions'][slot['region_id']]['page'] == number
            for gid in slot['binding']['paragraph']['selection']['glyph_ids']}


def _codes(path, number):
    content = ContentPage(path, number)
    try:
        assert not content.errors, (number, content.errors)
        programs, result = {}, {}
        for event in content.events:
            font = event.state.font
            if font.xref not in programs:
                program = content.document.extract_font(font.xref)[3]
                assert program, ('font program missing', number, font.name)
                programs[font.xref] = hashlib.sha256(program).hexdigest()
            for char in event.chars:
                assert len(char.source_orders) == 1, ('ambiguous original code', number)
                gid = char.source_orders[0]
                assert gid not in result, ('duplicate code mapping', number, gid)
                result[gid] = (char.code.hex(), font.name, programs[font.xref])
        assert set(result) == set(range(len(content.actual))), ('unmapped glyphs', number)
        return result
    finally:
        content.close()


def _foreign(page, codes, owned):
    result, index = [], 0
    for span in page.get_texttrace():
        attributes = {k: v for k, v in span.items() if k not in ('chars', 'seqno', 'bbox')}
        for char in span['chars']:
            if index not in owned:
                result.append((char, codes[index], attributes))
            index += 1
    return result


def _pixels(a, b, page_rect, boxes):
    assert a.size == b.size
    diff = ImageChops.difference(a, b)
    for box in boxes:
        rect = (box * pymupdf.Matrix(a.width / page_rect.width, a.height / page_rect.height)).irect
        ImageDraw.Draw(diff).rectangle((rect.x0, rect.y0, rect.x1 - 1, rect.y1 - 1), fill=0)
    channels = diff.split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    changed = a.width * a.height - maximum.histogram()[0]
    return changed


def audit(before, after, before_state, after_state, report, output, *, whole=False):
    """Compare exact foreign ownership and every writer glyph, then both renderers.

    Pass the accepted extended state as before_state for its immediate no-op. No
    renderer is optional: missing Poppler is an environment failure, not a PASS.
    Region masks use the existing evaluator convention (outward integer bounds);
    exact glyph ownership and glyph-bound containment are checked independently.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    renderer = shutil.which('pdftoppm')
    assert renderer, 'Poppler is required for real-PDF validation'
    assert source_sha(before) == before_state['pdf_sha256']
    assert source_sha(after) == after_state['pdf_sha256']
    old_reader, new_reader = PdfReader(before), PdfReader(after)
    regions = {}
    for region in after_state['regions'].values():
        regions.setdefault(region['page'], []).append(pymupdf.Rect(region['bounds']))
    providers = {entry['reflow_provider']['sha256']: entry['reflow_provider']
                 for paragraph in after_state['paragraphs'].values()
                 for entry in paragraph['style_registry'].values()}
    pages, placements, provider_fonts = [], [], {}
    max_error, retained, provided = 0.0, 0, 0
    with ExitStack() as stack:
        old = stack.enter_context(pymupdf.open(before))
        new = stack.enter_context(pymupdf.open(after))
        assert len(old) == len(new) == before_state['page_count'] == after_state['page_count']
        for number, (a, b) in enumerate(zip(old, new), 1):
            old_chars, new_chars = chars(a), chars(b)
            old_codes, new_codes = _codes(before, number), _codes(after, number)
            old_owned, new_owned = _owned(before_state, number), _owned(after_state, number)
            assert old_owned <= set(old_codes) and new_owned <= set(new_codes)
            assert _foreign(a, old_codes, old_owned) == _foreign(b, new_codes, new_owned), ('foreign glyphs/fonts', number)
            drawings = lambda page: [{k: v for k, v in d.items() if k != 'seqno'} for d in page.get_drawings()]
            links = lambda page: [{k: v for k, v in link.items() if k != 'xref'} for link in page.get_links()]
            assert drawings(a) == drawings(b), ('foreign drawings', number)
            assert a.get_image_info(hashes=True) == b.get_image_info(hashes=True), ('foreign images', number)
            assert links(a) == links(b), ('links', number)
            assert annotation_fingerprint(old_reader, number) == annotation_fingerprint(new_reader, number), ('annotations', number)
            contents = new_reader.pages[number - 1].get_contents()
            if contents is not None:
                assert not nesting_audit(contents.get_data())['violations']
            audited = set()
            for step in report['steps']:
                sid = step['slot_id']; slot = after_state['slots'][sid]
                region = after_state['regions'][slot['region_id']]
                if region['page'] != number:
                    continue
                glyphs = step['report']['glyph_plan']
                ids = slot['binding']['paragraph']['selection']['glyph_ids']
                assert len(ids) == len(glyphs) and not audited.intersection(ids)
                audited.update(ids)
                fragment = report['plan']['fragments'][sid]
                assert ''.join(chr(new_chars[i][0]) for i in ids) == ''.join(g['unicode'] for g in glyphs)
                for index, glyph in zip(ids, glyphs):
                    char = new_chars[index]
                    error = max(abs(char[2][k] - glyph['origin'][k]) for k in (0, 1))
                    max_error = max(max_error, error)
                    assert error < .002 and char[1] == glyph['glyph_id'], ('glyph plan', number, sid, index)
                    assert pymupdf.Rect(region['bounds']).contains(pymupdf.Rect(char[3]))
                    assert new_codes[index][:2] == (glyph['code'], glyph['font_resource'])
                    witness = glyph['source_index'] if glyph['source_index'] is not None else glyph.get('code_witness')
                    if witness is not None:
                        assert old_codes[witness] == new_codes[index] and old_chars[witness][1] == char[1]
                        retained += 1
                    else:
                        info = step['report']['fonts'][glyph['provider']]
                        assert new_codes[index][2] == info['subset_sha256']
                        key = (info['source_sha256'], info['font_index'], tuple(sorted(info['variations'].items())))
                        if key not in provider_fonts:
                            specification = providers[info['source_sha256']]
                            assert source_sha(specification['path']) == info['source_sha256']
                            font = ShapedFont(specification['path'], font_index=info['font_index'], variations=info['variations'])
                            stack.callback(font.font.close); provider_fonts[key] = font
                        provider = provider_fonts[key]
                        resource = glyph['font_resource'].lstrip('/')
                        xref = next(f[0] for f in b.get_fonts() if f[4] == resource)
                        with TTFont(BytesIO(new.extract_font(xref)[3])) as embedded:
                            assert outline(embedded, char[1]) == provider.outline(char[1])
                            assert embedded['hmtx'][embedded.getGlyphName(char[1])][0] == provider.nominal_width(char[1])
                        provided += 1
                placements.append(dict(slot=sid, paragraph=slot['paragraph_id'], page=number,
                    characters=slot['range'][1] - slot['range'][0], glyphs=len(ids),
                    lines=len(fragment['lines']), baselines=[line['baseline'] for line in fragment['lines']],
                    occupancy=slot['occupancy'], range=slot['range']))
            assert audited == new_owned, ('unaudited owned glyphs', number)
            boxes = [] if whole else regions.get(number, [])
            pixmaps = [page.get_pixmap(dpi=144, colorspace=pymupdf.csRGB, alpha=False) for page in (a, b)]
            images = [Image.frombytes('RGB', (p.width, p.height), p.samples) for p in pixmaps]
            mupdf = _pixels(*images, a.rect, boxes)
            assert mupdf == 0, ('MuPDF changed pixels', number, mupdf)
            for label, pdf in [('before', before), ('after', after)]:
                subprocess.run([renderer, '-f', str(number), '-l', str(number), '-singlefile',
                    '-r', '144', '-png', str(pdf), str(output / label)], check=True, capture_output=True, timeout=120)
            with Image.open(output / 'before.png') as x, Image.open(output / 'after.png') as y:
                poppler = _pixels(x.convert('RGB'), y.convert('RGB'), a.rect, boxes)
            assert poppler == 0, ('Poppler changed pixels', number, poppler)
            pages.append(dict(page=number, foreign_glyphs=len(old_chars) - len(old_owned),
                foreign_codes_gids_fonts_geometry_equal=True, drawings_images_links_annotations_equal=True,
                mupdf_changed_pixels=mupdf, poppler_changed_pixels=poppler))
    return dict(scope='all-page-whole-noop' if whole else 'all-page-outside-confirmed-regions',
        dpi=144, pages=pages, placements=placements, max_origin_error_pt=max_error,
        retained_or_reused_codes_gids_programs_checked=retained, provided_glyph_outlines_advances_checked=provided)
