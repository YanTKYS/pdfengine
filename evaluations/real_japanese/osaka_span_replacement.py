"""One nonempty Osaka Span62 replacement and one saved-revision no-op.

Run after focused tests with:
  .venv/bin/python -m evaluations.real_japanese.osaka_span_replacement
Prior deletion artifacts are untouched. New PDFs/fonts/sidecars stay in tmp/.
"""
from io import BytesIO
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import DecomposingRecordingPen
from PIL import Image, ImageChops, ImageDraw
import pymupdf
from pypdf import PdfReader

from pdfeditor.attributed import digest
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.page_proposal import propose_single_span, accept_single_span
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.shaped_font import ShapedFont
from pdfeditor.span_edit import edit_single_span, open_single_span, plan_single_span_edit
from .osaka_span_actualtext import SOURCE, SOURCE_SHA, REGION, ORIGINAL, observe

ROOT = Path('tmp/osaka-single-span-replacement')


def render(path, name):
    with pymupdf.open(path) as document:
        assert len(document) == 1
        document[0].get_pixmap(dpi=144, alpha=False).save(ROOT / (name + '-mupdf.png'))
    subprocess.run(['pdftoppm', '-singlefile', '-r', '144', '-png', str(path), str(ROOT / (name + '-poppler'))],
                   check=True, capture_output=True)


def pixels(first, second, region=None):
    result = {}
    for renderer in ('mupdf', 'poppler'):
        with Image.open(ROOT / (first + '-' + renderer + '.png')) as a, Image.open(ROOT / (second + '-' + renderer + '.png')) as b:
            assert a.size == b.size
            diff = ImageChops.difference(a.convert('RGB'), b.convert('RGB'))
            if region:
                ImageDraw.Draw(diff).rectangle((math.floor(region['x0']*2), math.floor(region['y0']*2),
                    math.ceil(region['x1']*2)-1, math.ceil(region['y1']*2)-1), fill=(0, 0, 0))
            raw = diff.tobytes()
            count = sum(bool(raw[i] or raw[i+1] or raw[i+2]) for i in range(0, len(raw), 3))
            result[renderer] = count
            assert count == 0, result
    return result


def drawings(path):
    with pymupdf.open(path) as document:
        return [{k: v for k, v in row.items() if k != 'seqno'} for row in document[0].get_drawings()]


def new_font_audit(path, records, text, recipe, planned):
    """Independent embedded subset/Unicode/CID/GID/outline/hmtx/PDF-width audit."""
    provider = ShapedFont(recipe['path']); run = provider.shape(text)
    assert provider.source_sha256 == recipe['sha256']
    content = ContentPage(path, 1); decoded = {}
    try:
        for event in content.events:
            for char in event.chars:
                for i in char.source_orders:
                    decoded[i] = (event, char)
        current = observe(path); selected = current['selected']
        first = next(i for i, g in enumerate(current['rows'][28]['glyphs']) if g['font'] == '/PRF1')
        resource = PdfReader(path).pages[0]['/Resources']['/Font']['/PRF1']
        subset_bytes = resource['/DescendantFonts'][0].get_object()['/FontDescriptor']['/FontFile2'].get_data()
        subset = TTFont(BytesIO(subset_bytes)); glyphs = subset.getGlyphSet()
        evidence = []
        assert len(records) == len(run.glyphs)
        for index, (actual, shaped) in enumerate(zip(records, run.glyphs)):
            event, char = decoded[selected[first + index]]
            assert actual['text'] == shaped.text and actual['gid'] == shaped.gid
            assert actual['font'] == '/PRF1' and char.code.hex() == actual['code']
            assert actual['font_sha256'] == hashlib.sha256(subset_bytes).hexdigest()
            assert event.state.font.xref == actual['font_xref']
            name = subset.getGlyphName(shaped.gid)
            pen = DecomposingRecordingPen(glyphs); glyphs[name].draw(pen)
            assert pen.value == provider.outline(shaped.gid)
            assert subset['head'].unitsPerEm == provider.upem
            assert subset['hmtx'][name][0] == provider.nominal_width(shaped.gid)
            pdf_width = provider.nominal_width(shaped.gid) * 1000 / provider.upem
            assert abs(char.pdf_width - pdf_width) <= 1e-6
            expected = planned[index]
            assert expected['glyph_id'] == actual['gid'] and expected['code'] == actual['code']
            assert max(abs(x-y) for x, y in zip(expected['origin'], actual['origin'])) <= .002
            evidence.append(dict(unicode=actual['text'], cid_code=actual['code'], gid=actual['gid'],
                provider_advance_units=shaped.advance, nominal_pdf_width=char.pdf_width,
                planned_advance_pt=expected['advance'], outline_sha256=digest(pen.value),
                font_resource=actual['font'], font_xref=actual['font_xref']))
        subset.close()
        return dict(provider_sha256=provider.source_sha256, instance_sha256=provider.instance_sha256,
                    subset_sha256=hashlib.sha256(subset_bytes).hexdigest(), glyphs=evidence)
    finally:
        provider.font.close(); content.close()


def main():
    started = time.monotonic()
    assert source_sha(SOURCE) == SOURCE_SHA
    ROOT.mkdir(parents=True, exist_ok=True)
    assert not any((ROOT / name).exists() for name in ('replacement.pdf', 'noop.pdf')), 'two-save budget already used'
    before = observe(SOURCE); original_drawings = drawings(SOURCE)
    selection = make_selection(SOURCE, 1, glyph_ids=before['selected'], explicit_width=REGION['x1']-REGION['x0'])
    proposal = propose_single_span(SOURCE, selection, region=REGION)
    state = accept_single_span(SOURCE, proposal)
    with pymupdf.open(SOURCE) as document:
        data = document.extract_font(5)[3]
    path = ROOT / 'source-F1.ttf'; path.write_bytes(data)
    recipe = dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), font_index=0)
    original = '作成されましたので'; start = ORIGINAL.index(original); end = start+len(original)
    refusals = []; plan = None
    for candidate in ('作成されていたので', '作成されたので'):
        edit = dict(start=start, end=end, text=candidate)
        try:
            plan = plan_single_span_edit(SOURCE, state, [edit], provider=recipe)
            break
        except PdfError as exc:
            refusals.append(dict(candidate=candidate, reason=str(exc)))
    if plan is None:
        result = dict(verdict='REFUSED', successful_saves=0, candidate_refusals=refusals)
        (ROOT / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2)); return
    wanted = ORIGINAL[:start]+edit['text']+ORIGINAL[end:]
    for name, value in [('proposal', proposal), ('plan', plan)]:
        (ROOT / (name+'.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    assert plan['provided_font_glyph_count'] == len(edit['text']) > 0
    assert plan['new_line_count'] == 1
    render(SOURCE, 'source'); qualified = time.monotonic()
    edited, side = ROOT / 'replacement.pdf', ROOT / 'replacement.json'
    edit_single_span(SOURCE, state, edited, side, [edit], provider=recipe)
    opened = open_single_span(edited, json.loads(side.read_text(encoding='utf-8')))
    assert opened['status'] == 'restored'
    after = observe(edited); target = after['rows'][28]
    assert target['actualtext'] == wanted == ''.join(g['text'] for g in target['glyphs'])
    assert target['association'] == before['rows'][28]['association']
    assert after['structure'] == before['structure'] and after['outside'] == before['outside']
    assert {i: row for i, row in after['rows'].items() if i != 28} == {i: row for i, row in before['rows'].items() if i != 28}
    assert all(after['fonts'][xref] == sha for xref, sha in before['fonts'].items())
    assert drawings(edited) == original_drawings
    old_glyphs = before['rows'][28]['glyphs']; new_glyphs = target['glyphs']
    retained_before = old_glyphs[:start] + old_glyphs[end:]
    retained_after = new_glyphs[:start] + new_glyphs[start+len(edit['text']):]
    strip_position = lambda rows: [{k:v for k,v in g.items() if k not in ('origin','bbox')} for g in rows]
    assert strip_position(retained_before) == strip_position(retained_after)
    new = new_glyphs[start:start+len(edit['text'])]
    planned = [g for g in plan['glyph_plan'] if g['provider'] != 'original']
    font_proof = new_font_audit(edited, new, edit['text'], recipe, planned)
    assert opened['state']['witness']['clip'] == state['witness']['clip']
    render(edited, 'replacement'); exterior = pixels('source', 'replacement', REGION)
    edited_done = time.monotonic()
    noop, noop_side = ROOT / 'noop.pdf', ROOT / 'noop.json'
    edit_single_span(edited, opened['state'], noop, noop_side, [])
    assert open_single_span(noop, json.loads(noop_side.read_text(encoding='utf-8')))['status'] == 'restored'
    assert observe(noop) == after and drawings(noop) == original_drawings
    render(noop, 'noop'); whole = pixels('replacement', 'noop')
    assert source_sha(SOURCE) == SOURCE_SHA
    finished = time.monotonic()
    result = dict(verdict='BOUNDED PASS', owner=62, mcid=28, page=1, source_sha256=SOURCE_SHA,
        original_actualtext=ORIGINAL, edited_actualtext=wanted, edit=edit, candidate_refusals=refusals,
        original_characters=len(ORIGINAL), edited_characters=len(wanted), region=REGION,
        layout_width_pt=plan['lines'][0]['width'], retained_original_glyphs=len(retained_after), new_glyphs=len(new),
        font_proof=font_proof, original_font_programs=before['fonts'], foreign_scopes_unchanged=96,
        foreign_drawing_count=len(original_drawings), structure_except_target_actualtext_equal=True,
        clip_unchanged=True, restored=['replacement','noop'], successful_saves=2, dpi=144,
        exterior_pixels=exterior, noop_whole_page_pixels=whole,
        sha256={str(p): source_sha(p) for p in (edited, side, noop, noop_side)},
        elapsed_seconds=dict(qualification=qualified-started, replacement_and_audit=edited_done-qualified,
                             noop_and_audit=finished-edited_done, total=finished-started))
    (ROOT / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
