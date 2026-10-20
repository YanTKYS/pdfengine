"""Osaka P58: Span62 -> Span63 adjacent-owner growth, one edit save and one no-op.

Run after focused tests with:
  .venv/bin/python -m evaluations.real_japanese.osaka_adjacent_span
Starts from the unmodified corpus PDF. PDFs, fonts, sidecars and rasters stay
under ignored tmp/. The script refuses to overwrite successful outputs.
"""
from io import BytesIO
import hashlib
import json
import math
import re
from pathlib import Path
import subprocess
import time

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw
import pymupdf
from pypdf import PdfReader

from pdfeditor.adjacent_span import (accept_adjacent_span, edit_adjacent_span, open_adjacent_span,
                                     plan_adjacent_span_edit, propose_adjacent_span)
from pdfeditor.attributed import digest
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.layout import LayoutError
from pdfeditor.marked_content import observe_marked_content, structure_objects
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.shaped_font import ShapedFont

SOURCE = Path('evaluations/realpdf/corpus/word_osaka_guideline.pdf')
SOURCE_SHA = '9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe'
ROOT = Path('tmp/osaka-adjacent-span')
PARENT, FIRST, SECOND, FOLLOWER = 58, 28, 29, 30          # P58; MCIDs of Span62, Span63, Span64
OWNERS = {FIRST: 62, SECOND: 63, FOLLOWER: 64}
BEFORE = ['国立感染症研究所においてガイドラインが作成されましたので、参考に送', '付いたします。']
AFTER = ['国立感染症研究所におきましてガイドラインが作成されましたので、参考', 'に送付いたします。']
# Observed Word text-column right edge is 524.62pt (right-aligned 日); 525 is
# Span62's previously approved edge. Line two holds only Span63/Span64 and no
# drawing; its rectangles stop at the same column edge, never the page margin.
REGIONS = [dict(x0=176.05999755859375, y0=335.0, x1=525.0, y1=351.0),
           dict(x0=70.94400024414062, y0=353.0, x1=525.0, y1=369.0)]
FOLLOWER_REGION = dict(x0=144.5, y0=353.0, x1=525.0, y1=369.0)


def observe(path):
    """Every scope's owner/ActualText/glyphs/envelope plus structure, fonts and drawings."""
    marked = observe_marked_content(path)
    assert marked['complete'] and len(marked['scopes']) == 97
    assert len({s['properties']['/MCID'] for s in marked['scopes']}) == 97
    reader = PdfReader(path); content = ContentPage(path, 1); fonts = {}; rows = {}
    try:
        assert not content.errors
        data = content.streams[-content.page.xref]
        for s in marked['scopes']:
            a = s['association']; lo, hi = s['content_byte_range']
            assert a['status'] == 'tree_backed' and a['backlink_verified']
            glyphs, ids = [], []
            for e in content.events:
                if not e.invocation and lo <= e.operator.start < hi:
                    for ch in e.chars:
                        i = ch.source_orders[0]; actual = content.actual[i]; font = e.state.font
                        if font.xref not in fonts:
                            fonts[font.xref] = hashlib.sha256(content.document.extract_font(font.xref)[3]).hexdigest()
                        glyphs.append(dict(text=ch.text, code=ch.code.hex(), gid=actual['gid'], font=font.name,
                                           font_xref=font.xref, font_sha256=fonts[font.xref],
                                           origin=list(actual['origin']), bbox=list(actual['bbox'])))
                        ids.append(i)
            rows[a['mcid']] = dict(owner=a['owner_reference'], association=a, properties=s['properties'],
                actualtext=reader.get_object(a['owner_reference']['xref']).get('/ActualText'), glyphs=glyphs, ids=ids,
                envelope=data[s['begin_byte_range'][0]:s['end_byte_range'][1]].hex(),
                range=[s['begin_byte_range'][0], s['end_byte_range'][1]])
        lo, hi = rows[FIRST]['range'][0], rows[FOLLOWER]['range'][1]
        outside = [data[:lo].hex(), data[hi:].hex()]
        gaps = [data[rows[a]['range'][1]:rows[b]['range'][0]].hex() for a, b in ((FIRST, SECOND), (SECOND, FOLLOWER))]
    finally:
        content.close()
    with pymupdf.open(path) as document:
        drawings = [{k: v for k, v in d.items() if k != 'seqno'} for d in document[0].get_drawings()]
    parent = reader.get_object(PARENT)
    children = [c.idnum for c in parent['/K']]
    return dict(rows=rows, fonts=fonts, outside=outside, gaps=gaps, drawings=drawings, children=children,
                paragraph=''.join(reader.get_object(c).get('/ActualText') for c in children),
                structure=structure_objects(reader, omit_actual_text=(OWNERS[FIRST], OWNERS[SECOND])),
                complete_structure=structure_objects(reader))


def render(path, name):
    with pymupdf.open(path) as document:
        assert len(document) == 1
        document[0].get_pixmap(dpi=144, alpha=False).save(ROOT / (name + '-mupdf.png'))
    subprocess.run(['pdftoppm', '-singlefile', '-r', '144', '-png', str(path), str(ROOT / (name + '-poppler'))],
                   check=True, capture_output=True)


def pixels(first, second, masks=()):
    result = {}
    for renderer in ('mupdf', 'poppler'):
        with Image.open(ROOT / (first + '-' + renderer + '.png')) as a, Image.open(ROOT / (second + '-' + renderer + '.png')) as b:
            assert a.size == b.size
            diff = ImageChops.difference(a.convert('RGB'), b.convert('RGB'))
            changed = diff.getbbox()
            for mask in masks:
                # Raster cells intersecting each exact approved rectangle; no
                # extra margin and no channel threshold.
                ImageDraw.Draw(diff).rectangle((math.floor(mask['x0'] * 2), math.floor(mask['y0'] * 2),
                    math.ceil(mask['x1'] * 2) - 1, math.ceil(mask['y1'] * 2) - 1), fill=(0, 0, 0))
            raw = diff.tobytes()
            count = sum(bool(raw[i] or raw[i + 1] or raw[i + 2]) for i in range(0, len(raw), 3))
            result[renderer] = dict(outside_pixels=count, changed_raster_bbox=changed)
            assert count == 0, result
    return result


def font_audit(path, glyphs, provider_path, source_rows):
    """Independent CID/GID/outline/hmtx/PDF-width audit of every generated glyph."""
    provider = ShapedFont(Path(provider_path).read_bytes())
    reader = PdfReader(path); fonts = reader.pages[0]['/Resources']['/Font']
    content = ContentPage(path, 1); decoded = {}
    try:
        for event in content.events:
            for char in event.chars:
                decoded[char.source_orders[0]] = (event, char)
        evidence = []
        for record in glyphs:
            event, char = decoded[record['id']]
            resource = fonts[record['font']].get_object()
            program = resource['/DescendantFonts'][0].get_object()['/FontDescriptor']['/FontFile2'].get_data()
            subset = TTFont(BytesIO(program)); glyph_set = subset.getGlyphSet()
            shaped = provider.shape(record['text']).glyphs
            assert len(shaped) == 1 and shaped[0].gid == record['gid'] and char.code.hex() == record['code']
            name = subset.getGlyphName(record['gid'])
            pen = DecomposingRecordingPen(glyph_set); glyph_set[name].draw(pen)
            assert pen.value == provider.outline(record['gid'])
            assert subset['head'].unitsPerEm == provider.upem and subset['hmtx'][name][0] == provider.nominal_width(record['gid'])
            assert abs(char.pdf_width - provider.nominal_width(record['gid']) * 1000 / provider.upem) <= 1e-6
            value = dict(owner=record['owner'], kind=record['kind'], unicode=record['text'], cid_code=record['code'],
                         gid=record['gid'], font_resource=record['font'], font_xref=event.state.font.xref,
                         subset_sha256=hashlib.sha256(program).hexdigest(),
                         provider_advance_units=shaped[0].advance, nominal_pdf_width=char.pdf_width,
                         outline_sha256=digest(pen.value))
            if record['kind'] == 'relocated':
                source = source_rows[record['source_offset']]
                assert source['text'] == record['text'] and source['gid'] == record['gid']
                value['source_glyph'] = {k: source[k] for k in ('code', 'gid', 'font', 'font_xref', 'font_sha256', 'origin')}
            evidence.append(value)
            subset.close()
        return evidence
    finally:
        provider.font.close(); content.close()


def selection(path, row, region):
    return make_selection(path, 1, glyph_ids=row['ids'], explicit_width=region['x1'] - region['x0'])


def without_follower(before, recipe):
    """First establish that the edit cannot succeed while Span64 stays where it is."""
    records = []
    edit = dict(start=BEFORE[0].index('において'), end=BEFORE[0].index('において') + 4, text='におきまして')
    for label, second in (('column-rectangle', REGIONS[1]),
                          ('rectangle-before-Span64', dict(REGIONS[1], x1=before['rows'][SECOND]['glyphs'][-1]['bbox'][2]))):
        owners = [dict(selection=selection(SOURCE, before['rows'][m], r), region=r)
                  for m, r in ((FIRST, REGIONS[0]), (SECOND, second))]
        record = dict(label=label, second_region=second)
        try:
            state = accept_adjacent_span(SOURCE, propose_adjacent_span(SOURCE, *owners))
            record['proposal'] = 'accepted'
            plan_adjacent_span_edit(SOURCE, state, edit, boundary=len(AFTER[0]), provider=recipe)
            record['plan'] = 'qualified'
        except (PdfError, LayoutError) as exc:
            record.setdefault('proposal', 'REFUSED'); record['reason'] = str(exc)
            if record['proposal'] == 'accepted':
                record['plan'] = 'REFUSED'
        records.append(record)
    assert all(r.get('plan') != 'qualified' for r in records)
    return records


def main():
    started = time.monotonic()
    assert source_sha(SOURCE) == SOURCE_SHA
    ROOT.mkdir(parents=True, exist_ok=True)
    assert not (ROOT / 'noop.pdf').exists(), 'two-save budget already used'
    # An earlier run may have saved edited.pdf and then stopped in this
    # script's own audit; resume its audit instead of saving again.
    before = observe(SOURCE)
    assert [before['rows'][m]['owner']['xref'] for m in (FIRST, SECOND, FOLLOWER)] == [62, 63, 64]
    assert [before['rows'][m]['actualtext'] for m in (FIRST, SECOND)] == BEFORE
    assert before['children'] == [59, 60, 61, 62, 63, 64]
    with pymupdf.open(SOURCE) as document:
        program = document.extract_font(before['rows'][FIRST]['glyphs'][0]['font_xref'])[3]
    provider_path = ROOT / 'source-F1.ttf'; provider_path.write_bytes(program)
    recipe = dict(path=str(provider_path), sha256=hashlib.sha256(program).hexdigest(), font_index=0)
    refusals = without_follower(before, recipe)
    owners = [dict(selection=selection(SOURCE, before['rows'][m], r), region=r) for m, r in zip((FIRST, SECOND), REGIONS)]
    follower = dict(glyph_ids=before['rows'][FOLLOWER]['ids'], region=FOLLOWER_REGION)
    proposal = propose_adjacent_span(SOURCE, *owners, follower=follower)
    state = accept_adjacent_span(SOURCE, proposal)
    edit = dict(start=BEFORE[0].index('において'), end=BEFORE[0].index('において') + 4, text='におきまして')
    boundary = len(AFTER[0])
    plan = plan_adjacent_span_edit(SOURCE, state, edit, boundary=boundary, provider=recipe)
    assert plan['split']['after'] == AFTER
    edited, side = ROOT / 'edited.pdf', ROOT / 'edited.json'
    resumed = edited.exists()
    if resumed:
        assert side.exists() and json.loads((ROOT / 'plan.json').read_text(encoding='utf-8')) == json.loads(
            json.dumps(plan, ensure_ascii=False))
    for name, value in (('proposal', proposal), ('plan', plan)):
        (ROOT / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    render(SOURCE, 'source'); qualified = time.monotonic()

    if not resumed:
        edit_adjacent_span(SOURCE, state, edited, side, edit, boundary=boundary, provider=recipe)
    # The read-only plan is the same proof the save re-ran; audit against it.
    result = dict(provenance=plan['provenance'], follower_dx=plan['follower']['dx'])
    opened = open_adjacent_span(edited, json.loads(side.read_text(encoding='utf-8')))
    assert opened['status'] == 'restored'
    after = observe(edited)
    assert source_sha(SOURCE) == SOURCE_SHA
    # Semantics: exactly two ActualText values change, each equal to its drawn text.
    for mcid, wanted in zip((FIRST, SECOND), AFTER):
        row = after['rows'][mcid]
        assert row['actualtext'] == wanted == ''.join(g['text'] for g in row['glyphs'])
        assert row['association'] == before['rows'][mcid]['association']
    assert after['paragraph'] == before['paragraph'].replace('において', 'におきまして')
    assert len(after['paragraph']) == len(before['paragraph']) + 2
    assert after['structure'] == before['structure'] and after['children'] == before['children']
    changed_structure = {k for k in before['complete_structure'] if before['complete_structure'][k] != after['complete_structure'].get(k)}
    assert changed_structure == {'62:0', '63:0'} and set(after['complete_structure']) == set(before['complete_structure'])
    # Everything else: other owners, original fonts, drawings, program outside the three envelopes.
    untouched = [m for m in before['rows'] if m not in (FIRST, SECOND, FOLLOWER)]
    # Byte offsets and page-wide glyph indices shift after two added glyphs;
    # every observed value of the other owners must stay identical.
    stable = lambda row: {k: v for k, v in row.items() if k not in ('range', 'ids')}
    assert all(stable(after['rows'][m]) == stable(before['rows'][m]) for m in untouched)
    assert all(after['fonts'][x] == sha for x, sha in before['fonts'].items())
    assert after['drawings'] == before['drawings'] and after['outside'] == before['outside'] and after['gaps'] == before['gaps']
    # Follower: semantics, glyph/font identity and envelope unchanged except its Tm translation.
    dx = result['follower_dx']
    old_f, new_f = before['rows'][FOLLOWER], after['rows'][FOLLOWER]
    assert {k: new_f[k] for k in ('owner', 'association', 'properties', 'actualtext')} == {
        k: old_f[k] for k in ('owner', 'association', 'properties', 'actualtext')}
    assert [{k: v for k, v in g.items() if k not in ('origin', 'bbox')} for g in new_f['glyphs']] == [
        {k: v for k, v in g.items() if k not in ('origin', 'bbox')} for g in old_f['glyphs']]
    assert abs(new_f['glyphs'][0]['origin'][0] - old_f['glyphs'][0]['origin'][0] - dx) <= .002
    assert new_f['glyphs'][0]['origin'][1] == old_f['glyphs'][0]['origin'][1]
    old_env, new_env = bytes.fromhex(old_f['envelope']), bytes.fromhex(new_f['envelope'])
    o, n = (re.search(rb'1 0 0 1 (\S+) (\S+) Tm', env) for env in (old_env, new_env))
    assert old_env[:o.start()] == new_env[:n.start()] and old_env[o.end():] == new_env[n.end():]
    assert o.group(2) == n.group(2) and abs(float(n.group(1)) - float(o.group(1)) - dx) <= 1e-9
    # Clips and graphics wrappers: each owner envelope still opens with its original clip.
    for mcid in (FIRST, SECOND):
        old_env, new_env = bytes.fromhex(before['rows'][mcid]['envelope']), bytes.fromhex(after['rows'][mcid]['envelope'])
        prefix = old_env[:old_env.index(b'BT')]
        assert new_env.startswith(prefix) and new_env.endswith(b'ET\r\nQ\r\n EMC')
    assert [opened['state']['witness'][r]['clip'] for r in ('first', 'second')] == [state['witness'][r]['clip'] for r in ('first', 'second')]
    # Glyph provenance: retained native codes, relocated and inserted generated glyphs.
    classes, generated = {'retained': 0, 'relocated': 0, 'inserted': 0}, []
    cursor = {'first': 0, 'second': 0}
    rows = {'first': after['rows'][FIRST], 'second': after['rows'][SECOND]}
    for record in result['provenance']:
        classes[record['kind']] += 1
        glyph = rows[record['owner']]['glyphs'][cursor[record['owner']]]
        index = rows[record['owner']]['ids'][cursor[record['owner']]]; cursor[record['owner']] += 1
        assert glyph['text'] == record['unicode']
        if record['kind'] == 'retained':
            source_row = before['rows'][FIRST if record['source']['owner'] == 'first' else SECOND]['glyphs'][record['source']['offset']]
            assert {k: glyph[k] for k in ('text', 'code', 'gid', 'font', 'font_xref', 'font_sha256')} == {
                k: source_row[k] for k in ('text', 'code', 'gid', 'font', 'font_xref', 'font_sha256')}
        else:
            assert glyph['font'] != before['rows'][FIRST]['glyphs'][0]['font']
            generated.append(dict(id=index, owner=record['owner'], kind=record['kind'], text=glyph['text'],
                                  code=glyph['code'], gid=glyph['gid'], font=glyph['font'],
                                  source_offset=record['source']['offset'] if record['source'] else None))
    assert classes == {'retained': 34, 'relocated': 2, 'inserted': 6}
    fonts = font_audit(edited, generated, provider_path, before['rows'][FIRST]['glyphs'])
    render(edited, 'edited')
    exterior = pixels('source', 'edited', masks=REGIONS + [FOLLOWER_REGION])
    edited_done = time.monotonic()

    noop, noop_side = ROOT / 'noop.pdf', ROOT / 'noop.json'
    edit_adjacent_span(edited, opened['state'], noop, noop_side)
    reopened = open_adjacent_span(noop, json.loads(noop_side.read_text(encoding='utf-8')))
    assert reopened['status'] == 'restored'
    assert observe(noop) == after
    assert PdfReader(edited).pages[0].get_contents().get_data() == PdfReader(noop).pages[0].get_contents().get_data()
    render(noop, 'noop')
    whole = pixels('edited', 'noop')
    assert source_sha(SOURCE) == SOURCE_SHA
    finished = time.monotonic()
    layouts = [dict(text=o['after'], line=o['lines'][0], width_limit=r['x1'] - o['x']) for o, r in zip(plan['owners'], REGIONS)]
    summary = dict(verdict='PASS', source=str(SOURCE), source_sha256=SOURCE_SHA, page=1, parent=PARENT,
        owners=[dict(owner=OWNERS[m], mcid=m, before=b, after=a, characters=[len(b), len(a)])
                for m, b, a in zip((FIRST, SECOND), BEFORE, AFTER)],
        follower=dict(owner=64, mcid=FOLLOWER, text=old_f['actualtext'], properties=old_f['properties'],
                      dx=dx, before=old_f['glyphs'][0]['bbox'], after=new_f['glyphs'][0]['bbox']),
        edit=edit, boundary=boundary, relocated=plan['split']['relocated'],
        paragraph=dict(before=before['paragraph'], after=after['paragraph'],
                       characters=[len(before['paragraph']), len(after['paragraph'])], reading_order=before['children']),
        regions=REGIONS, follower_region=FOLLOWER_REGION, layouts=layouts, without_follower=refusals,
        provenance_counts=classes, generated_glyphs=fonts, provider_sha256=recipe['sha256'],
        generated_font_bindings=[o['font_binding']['generated'] for o in opened['state']['owners']],
        original_font_programs=before['fonts'], other_scopes_unchanged=len(untouched),
        foreign_drawings=len(before['drawings']), structure_changed_objects=sorted(changed_structure),
        restored=['edited', 'noop'], successful_saves=2, edited_audit_resumed_without_resave=resumed, dpi=144, exterior_pixels=exterior, noop_whole_page_pixels=whole,
        sha256={str(p): source_sha(p) for p in (edited, side, noop, noop_side)},
        elapsed_seconds=dict(qualification=qualified - started, edit_and_audit=edited_done - qualified,
                             noop_and_audit=finished - edited_done, total=finished - started))
    (ROOT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=list) + '\n', encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('verdict', 'owners', 'follower', 'paragraph', 'provenance_counts',
                                              'exterior_pixels', 'noop_whole_page_pixels', 'sha256', 'elapsed_seconds')},
                     ensure_ascii=False, indent=2, default=list))


if __name__ == '__main__':
    main()
