"""Bounded Osaka Span62 qualification: exactly one edit save and one no-op.

Run from the repository root after focused tests:
  .venv/bin/python -m evaluations.real_japanese.osaka_span_actualtext
PDFs, sidecars and rasters are kept only under ignored tmp/.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

from PIL import Image, ImageChops, ImageDraw
import pymupdf
from pypdf import PdfReader

from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content, structure_objects
from pdfeditor.page_proposal import propose_single_span, accept_single_span
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.span_edit import edit_single_span, open_single_span

SOURCE = Path('evaluations/realpdf/corpus/word_osaka_guideline.pdf')
SOURCE_SHA = '9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe'
ROOT = Path('tmp/osaka-single-span-actualtext')
REGION = dict(x0=176.05999755859375, y0=335.0, x1=525.0, y1=351.0)
ORIGINAL = '国立感染症研究所においてガイドラインが作成されましたので、参考に送'


def observe(path):
    marked = observe_marked_content(path)
    assert marked['complete'] and len(marked['scopes']) == 97
    assert Counter(s['association']['status'] for s in marked['scopes']) == {'tree_backed': 97}
    assert len({s['properties']['/MCID'] for s in marked['scopes']}) == 97
    reader = PdfReader(path); content = ContentPage(path, 1)
    rows = {}; selected = []; fonts = {}
    try:
        assert not content.errors
        data = content.streams[-content.page.xref]
        for s in marked['scopes']:
            a = s['association']; lo, hi = s['content_byte_range']; mcid = a['mcid']
            assert a['backlink_verified'] and all(x['parent_backlink_verified'] for x in a['ancestors'])
            glyphs = []
            for e in content.events:
                if not e.invocation and lo <= e.operator.start < hi:
                    for ch in e.chars:
                        assert len(ch.source_orders) == 1
                        i = ch.source_orders[0]; actual = content.actual[i]
                        font = e.state.font
                        if font.xref not in fonts:
                            fonts[font.xref] = hashlib.sha256(content.document.extract_font(font.xref)[3]).hexdigest()
                        glyphs.append(dict(text=ch.text, code=ch.code.hex(), gid=actual['gid'],
                            font=font.name, font_xref=font.xref, font_sha256=fonts[font.xref],
                            origin=list(actual['origin']), bbox=list(actual['bbox'])))
                        if mcid == 28: selected.append(i)
            rows[mcid] = dict(owner=a['owner_reference'], association=a,
                actualtext=reader.get_object(a['owner_reference']['xref']).get('/ActualText'), glyphs=glyphs,
                envelope=data[s['begin_byte_range'][0]:s['end_byte_range'][1]].hex())
        scope = marked['scopes'][28]
        lo, hi = scope['begin_byte_range'][0], scope['end_byte_range'][1]
        outside = [data[:lo].hex(), data[hi:].hex()]
    finally:
        content.close()
    return dict(rows=rows, selected=selected, fonts=fonts, outside=outside,
                structure=structure_objects(reader, omit_actual_text=62),
                complete_structure=structure_objects(reader), program=data.hex())


def render(path, name):
    with pymupdf.open(path) as doc:
        assert len(doc) == 1
        doc[0].get_pixmap(dpi=144, alpha=False).save(ROOT / (name + '-mupdf.png'))
    subprocess.run(['pdftoppm', '-singlefile', '-r', '144', '-png', str(path), str(ROOT / (name + '-poppler'))],
                   check=True, capture_output=True)


def pixels(first, second, *, mask=None):
    result = {}
    for renderer in ('mupdf', 'poppler'):
        with Image.open(ROOT / (first + '-' + renderer + '.png')) as a, Image.open(ROOT / (second + '-' + renderer + '.png')) as b:
            assert a.size == b.size
            diff = ImageChops.difference(a.convert('RGB'), b.convert('RGB'))
            if mask:
                # Raster cells intersecting the exact approved rectangle are
                # inside; no extra point margin or channel threshold is used.
                ImageDraw.Draw(diff).rectangle((math.floor(mask['x0'] * 2), math.floor(mask['y0'] * 2),
                    math.ceil(mask['x1'] * 2) - 1, math.ceil(mask['y1'] * 2) - 1), fill=(0, 0, 0))
            count = sum(any(pixel) for pixel in diff.getdata())
            result[renderer] = count
            assert count == 0, result
    return result


def main():
    started = time.monotonic()
    assert source_sha(SOURCE) == SOURCE_SHA
    ROOT.mkdir(parents=True, exist_ok=True)
    assert not any((ROOT / name).exists() for name in ('edited.pdf', 'noop.pdf')), 'successful save budget already used'
    before = observe(SOURCE)
    assert before['rows'][28]['owner'] == {'xref': 62, 'generation': 0}
    assert before['rows'][28]['actualtext'] == ORIGINAL
    selection = make_selection(SOURCE, 1, glyph_ids=before['selected'], explicit_width=REGION['x1'] - REGION['x0'])
    proposal = propose_single_span(SOURCE, selection, region=REGION)
    state = accept_single_span(SOURCE, proposal)
    (ROOT / 'proposal.json').write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    start = ORIGINAL.index('まし'); end = start + 2
    edits = [dict(start=start, end=end, text='')]
    wanted = ORIGINAL[:start] + ORIGINAL[end:]
    render(SOURCE, 'source')
    qualified = time.monotonic()
    edited, side = ROOT / 'edited.pdf', ROOT / 'edited.json'
    edit_single_span(SOURCE, state, edited, side, edits)
    opened = open_single_span(edited, json.loads(side.read_text(encoding='utf-8')))
    assert opened['status'] == 'restored'
    after = observe(edited)
    assert after['structure'] == before['structure'] and after['outside'] == before['outside']
    assert after['fonts'] == before['fonts']
    assert {i: r for i, r in after['rows'].items() if i != 28} == {i: r for i, r in before['rows'].items() if i != 28}
    target = after['rows'][28]
    assert target['actualtext'] == wanted == ''.join(g['text'] for g in target['glyphs'])
    assert target['association'] == before['rows'][28]['association']
    kept = before['rows'][28]['glyphs'][:start] + before['rows'][28]['glyphs'][end:]
    assert [{k: v for k, v in g.items() if k not in ('origin', 'bbox')} for g in target['glyphs']] == [
        {k: v for k, v in g.items() if k not in ('origin', 'bbox')} for g in kept]
    assert opened['state']['witness']['clip'] == state['witness']['clip']
    render(edited, 'edited')
    exterior = pixels('source', 'edited', mask=REGION)
    edit_done = time.monotonic()
    noop, noop_side = ROOT / 'noop.pdf', ROOT / 'noop.json'
    edit_single_span(edited, opened['state'], noop, noop_side, [])
    reopened = open_single_span(noop, json.loads(noop_side.read_text(encoding='utf-8')))
    assert reopened['status'] == 'restored'
    nooped = observe(noop)
    assert nooped == after
    render(noop, 'noop')
    whole = pixels('edited', 'noop')
    assert source_sha(SOURCE) == SOURCE_SHA
    finished = time.monotonic()
    result = dict(verdict='BOUNDED PASS', source_sha256=SOURCE_SHA, owner=62, mcid=28, page=1,
        original_actualtext=ORIGINAL, edited_actualtext=wanted, edit=edits[0], region=REGION,
        original_characters=len(ORIGINAL), edited_characters=len(wanted), foreign_scopes_unchanged=96,
        original_fonts=before['fonts'], retained_glyph_codes_gids_fonts_unchanged=len(kept),
        source_and_edited_structure_except_target_actualtext_equal=True, clip_unchanged=True,
        restored=['edited', 'noop'], successful_saves=2, dpi=144, exterior_pixels=exterior, noop_whole_page_pixels=whole,
        sha256={str(p): source_sha(p) for p in (edited, side, noop, noop_side)},
        elapsed_seconds=dict(qualification=qualified-started, edit_and_audit=edit_done-qualified,
                             noop_and_audit=finished-edit_done, total=finished-started))
    (ROOT / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
