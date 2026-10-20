"""Osaka guideline: edit intent (selection + replacement) -> proposal -> acceptance -> edit -> no-op.

Run after focused tests with:
  .venv/bin/python -m evaluations.real_japanese.osaka_adjacent_intent [--root tmp/<new directory>]
The proposal receives only a concrete glyph selection and replacement text;
owner numbers, MCIDs, the boundary and rectangles are read back from it for
the audit. Starts from the unmodified corpus PDF. PDFs, fonts, sidecars and
rasters stay under ignored tmp/. Two successful saves at most.
"""
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import time

from PIL import Image, ImageChops, ImageDraw
import pymupdf
from pypdf import PdfReader

from pdfeditor.adjacent_intent import accept_adjacent_intent, edit_adjacent_intent, propose_adjacent_intent
from pdfeditor.adjacent_span import edit_adjacent_span, open_adjacent_span
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content, structure_objects
from pdfeditor.selection import make_selection, source_sha
from .osaka_adjacent_span import font_audit

SOURCE = Path('evaluations/realpdf/corpus/word_osaka_guideline.pdf')
SOURCE_SHA = '9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe'
ROOT = Path('tmp/osaka-adjacent-intent')
SELECTED, REPLACEMENT = 'において', 'におきまして'
# Earlier saved results compared when present: manual adjacent edit and the PR #80 intent run.
EARLIER = dict(manual=Path('tmp/osaka-adjacent-span'), intent=Path('tmp/osaka-adjacent-intent'))


def user_selection():
    """A UI gesture yields concrete glyph IDs; here the one drawn occurrence on page 1.

    The evaluation proves the occurrence is unique on the page; with more than
    one, a pointer position would be required and the evaluation stops.
    """
    content = ContentPage(SOURCE, 1)
    try:
        drawn = [g['unicode'] for g in content.actual]
    finally:
        content.close()
    starts = [i for i in range(len(drawn)) if ''.join(drawn[i:i + len(SELECTED)]) == SELECTED]
    assert len(starts) == 1, 'ambiguous display text needs a pointer position'
    return make_selection(SOURCE, 1, glyph_ids=list(range(starts[0], starts[0] + len(SELECTED)))), starts


def observe(path, first, second, follower, parent):
    """Every scope plus the derived owners' envelopes, structure, fonts and drawings."""
    marked = observe_marked_content(path)
    assert marked['complete'] and len({s['properties']['/MCID'] for s in marked['scopes']}) == len(marked['scopes'])
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
        outside = [data[:rows[first]['range'][0]].hex(), data[rows[follower]['range'][1]:].hex()]
        gaps = [data[rows[a]['range'][1]:rows[b]['range'][0]].hex() for a, b in ((first, second), (second, follower))]
    finally:
        content.close()
    with pymupdf.open(path) as document:
        drawings = [{k: v for k, v in d.items() if k != 'seqno'} for d in document[0].get_drawings()]
    children = [c.idnum for c in reader.get_object(parent)['/K']]
    owners = (rows[first]['owner']['xref'], rows[second]['owner']['xref'])
    return dict(rows=rows, fonts=fonts, outside=outside, gaps=gaps, drawings=drawings, children=children,
                paragraph=''.join(reader.get_object(c).get('/ActualText') for c in children),
                structure=structure_objects(reader, omit_actual_text=owners), complete_structure=structure_objects(reader))


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
                # Raster cells intersecting each exact approved rectangle only.
                ImageDraw.Draw(diff).rectangle((math.floor(mask['x0'] * 2), math.floor(mask['y0'] * 2),
                    math.ceil(mask['x1'] * 2) - 1, math.ceil(mask['y1'] * 2) - 1), fill=(0, 0, 0))
            raw = diff.tobytes()
            count = sum(bool(raw[i] or raw[i + 1] or raw[i + 2]) for i in range(0, len(raw), 3))
            result[renderer] = dict(outside_pixels=count, changed_raster_bbox=changed)
            assert count == 0, result
    return result


def safe_to_accept(proposal):
    """The conditions under which this evaluation simulates the user's approval."""
    chosen = proposal['candidates'][proposal['chosen']] if proposal['chosen'] is not None else None
    evidence = proposal['regions']['evidence']
    checks = dict(
        status_proposed=proposal['status'] == 'proposed',
        chosen_qualified_natural=bool(chosen and chosen['status'] == 'qualified' and chosen['natural_break']),
        # Moved characters only prefix the second owner; its original text stays its tail.
        forward_relocation_only=bool(chosen and chosen['after'][1].endswith(proposal['owners']['second']['text'])
                                     and chosen['after'][1][:chosen['relocated_count']] == chosen['relocated']),
        follower_contract_proved=proposal['follower'].get('approved') is True,
        rectangles_within_column=all(r['x1'] <= evidence['column_right'] for r in
                                     proposal['regions']['owners'] + [proposal['regions']['follower']]),
        column_edge_inside_wrap_window=evidence['wrap_window'][0] <= evidence['column_right'] < evidence['wrap_window'][1],
        provider_is_original_program=proposal['font']['provider']['sha256'] == proposal['font']['program_sha256'],
        growth=len(proposal['logical_after']) > len(proposal['logical_before']))
    return checks


def main():
    global ROOT
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(ROOT))
    ROOT = Path(parser.parse_args().root)
    started = time.monotonic()
    assert source_sha(SOURCE) == SOURCE_SHA
    ROOT.mkdir(parents=True, exist_ok=True)
    assert not (ROOT / 'noop.pdf').exists(), 'two-save budget already used'
    selection, starts = user_selection()
    proposal = propose_adjacent_intent(SOURCE, selection, REPLACEMENT, work_dir=ROOT / 'work')
    proposed = time.monotonic()
    (ROOT / 'proposal.json').write_text(json.dumps(proposal, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    checks = safe_to_accept(proposal)
    assert all(checks.values()), checks
    # Simulated explicit approval of exactly this proposal and its chosen boundary.
    acceptance = accept_adjacent_intent(SOURCE, proposal, work_dir=ROOT / 'work')
    (ROOT / 'acceptance.json').write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    accepted = time.monotonic()

    owners = proposal['owners']; first, second = owners['first']['mcid'], owners['second']['mcid']
    follower = proposal['follower']['mcid']; parent = owners['parent']['xref']
    chosen = proposal['candidates'][proposal['chosen']]
    regions = proposal['regions']['owners'] + [proposal['regions']['follower']]
    before = observe(SOURCE, first, second, follower, parent)
    render(SOURCE, 'source')
    edited, side = ROOT / 'edited.pdf', ROOT / 'edited.json'
    resumed = edited.exists()
    result = None
    if not resumed:
        result = edit_adjacent_intent(SOURCE, acceptance, edited, side, work_dir=ROOT / 'work')
        assert result['after'] == chosen['after']
    saved = time.monotonic()
    opened = open_adjacent_span(edited, json.loads(side.read_text(encoding='utf-8')))
    assert opened['status'] == 'restored'
    after = observe(edited, first, second, follower, parent)
    assert source_sha(SOURCE) == SOURCE_SHA
    for mcid, wanted in zip((first, second), chosen['after']):
        row = after['rows'][mcid]
        assert row['actualtext'] == wanted == ''.join(g['text'] for g in row['glyphs'])
        assert row['association'] == before['rows'][mcid]['association']
    assert after['paragraph'] == before['paragraph'].replace(SELECTED, REPLACEMENT)
    assert (len(before['paragraph']), len(after['paragraph'])) == (50, 52)
    assert after['structure'] == before['structure'] and after['children'] == before['children']
    changed = {k for k in before['complete_structure'] if before['complete_structure'][k] != after['complete_structure'].get(k)}
    owner_keys = {'%d:0' % owners[r]['owner']['xref'] for r in ('first', 'second')}
    assert changed == owner_keys and set(after['complete_structure']) == set(before['complete_structure'])
    stable = lambda row: {k: v for k, v in row.items() if k not in ('range', 'ids')}
    untouched = [m for m in before['rows'] if m not in (first, second, follower)]
    assert all(stable(after['rows'][m]) == stable(before['rows'][m]) for m in untouched)
    assert all(after['fonts'][x] == sha for x, sha in before['fonts'].items())
    assert after['drawings'] == before['drawings'] and after['outside'] == before['outside'] and after['gaps'] == before['gaps']
    old_f, new_f = before['rows'][follower], after['rows'][follower]
    dx = chosen['follower_dx']
    assert {k: new_f[k] for k in ('owner', 'association', 'properties', 'actualtext')} == {
        k: old_f[k] for k in ('owner', 'association', 'properties', 'actualtext')}
    assert [{k: v for k, v in g.items() if k not in ('origin', 'bbox')} for g in new_f['glyphs']] == [
        {k: v for k, v in g.items() if k not in ('origin', 'bbox')} for g in old_f['glyphs']]
    assert abs(new_f['glyphs'][0]['origin'][0] - old_f['glyphs'][0]['origin'][0] - dx) <= .002
    old_env, new_env = bytes.fromhex(old_f['envelope']), bytes.fromhex(new_f['envelope'])
    o, n = (re.search(rb'1 0 0 1 (\S+) (\S+) Tm', env) for env in (old_env, new_env))
    assert old_env[:o.start()] == new_env[:n.start()] and old_env[o.end():] == new_env[n.end():]
    assert o.group(2) == n.group(2) and abs(float(n.group(1)) - float(o.group(1)) - dx) <= 1e-9
    for mcid in (first, second):
        old_env, new_env = bytes.fromhex(before['rows'][mcid]['envelope']), bytes.fromhex(after['rows'][mcid]['envelope'])
        assert new_env.startswith(old_env[:old_env.index(b'BT')]) and new_env.endswith(b'ET\r\nQ\r\n EMC')
    # Provenance from the same read-only proof the save re-ran.
    from pdfeditor.adjacent_span import plan_adjacent_span_edit
    plan = plan_adjacent_span_edit(SOURCE, acceptance['model'], acceptance['edit'], boundary=acceptance['boundary'],
                                   provider=acceptance['provider'])
    classes, generated, cursor = {'retained': 0, 'relocated': 0, 'inserted': 0}, [], {'first': 0, 'second': 0}
    rows = {'first': after['rows'][first], 'second': after['rows'][second]}
    for record in plan['provenance']:
        classes[record['kind']] += 1
        glyph = rows[record['owner']]['glyphs'][cursor[record['owner']]]
        index = rows[record['owner']]['ids'][cursor[record['owner']]]; cursor[record['owner']] += 1
        assert glyph['text'] == record['unicode']
        if record['kind'] == 'retained':
            source = before['rows'][first if record['source']['owner'] == 'first' else second]['glyphs'][record['source']['offset']]
            keys = ('text', 'code', 'gid', 'font', 'font_xref', 'font_sha256')
            assert {k: glyph[k] for k in keys} == {k: source[k] for k in keys}
        else:
            generated.append(dict(id=index, owner=record['owner'], kind=record['kind'], text=glyph['text'],
                                  code=glyph['code'], gid=glyph['gid'], font=glyph['font'],
                                  source_offset=record['source']['offset'] if record['source'] else None))
    fonts = font_audit(edited, generated, acceptance['provider']['path'], before['rows'][first]['glyphs'])
    render(edited, 'edited')
    exterior = pixels('source', 'edited', masks=regions)
    manual = {}
    for label, root in EARLIER.items():
        earlier = root / 'edited.pdf'
        if earlier.exists() and root.resolve() != ROOT.resolve():
            manual[label] = dict(content_stream_equal=PdfReader(earlier).pages[0].get_contents().get_data()
                                 == PdfReader(edited).pages[0].get_contents().get_data(),
                                 sha256_equal=source_sha(earlier) == source_sha(edited))
    audited = time.monotonic()

    noop, noop_side = ROOT / 'noop.pdf', ROOT / 'noop.json'
    edit_adjacent_span(edited, opened['state'], noop, noop_side)
    assert open_adjacent_span(noop, json.loads(noop_side.read_text(encoding='utf-8')))['status'] == 'restored'
    assert observe(noop, first, second, follower, parent) == after
    assert PdfReader(edited).pages[0].get_contents().get_data() == PdfReader(noop).pages[0].get_contents().get_data()
    render(noop, 'noop')
    whole = pixels('edited', 'noop')
    for label, root in EARLIER.items():
        earlier = root / 'noop.pdf'
        if label in manual and earlier.exists():
            manual[label]['noop_sha256_equal'] = source_sha(earlier) == source_sha(noop)
    assert source_sha(SOURCE) == SOURCE_SHA
    finished = time.monotonic()
    summary = dict(verdict='PASS', source=str(SOURCE), source_sha256=SOURCE_SHA, page=1,
        input=dict(selection_glyph_ids=selection['glyph_ids'], selected_text=SELECTED, replacement=REPLACEMENT,
                   drawn_occurrences_on_page=len(starts)),
        derived=dict(first=owners['first'], second=owners['second'], parent=owners['parent'],
                     follower={k: proposal['follower'][k] for k in ('owner', 'mcid', 'text', 'note', 'approved')},
                     original_boundary=proposal['original_boundary'], boundary=chosen['boundary'],
                     relocated=chosen['relocated'], follower_dx=dx, regions=proposal['regions'], font=proposal['font']),
        candidates=[{k: c.get(k) for k in ('relocated_count', 'boundary', 'after', 'natural_break', 'break_evidence',
                                           'status', 'reason', 'widths', 'width_limits', 'follower_dx')}
                    for c in proposal['candidates']],
        acceptance_checks=checks, proposal_sha256=proposal['model_sha256'], acceptance_sha256=acceptance['model_sha256'],
        paragraph=dict(before=before['paragraph'], after=after['paragraph'],
                       characters=[len(before['paragraph']), len(after['paragraph'])], reading_order=before['children']),
        owner_text=[dict(mcid=m, before=before['rows'][m]['actualtext'], after=after['rows'][m]['actualtext'])
                    for m in (first, second)],
        provenance_counts=classes, generated_glyphs=fonts, other_scopes_unchanged=len(untouched),
        foreign_drawings=len(before['drawings']), structure_changed_objects=sorted(changed),
        earlier_results=manual, restored=['edited', 'noop'], successful_saves=2,
        edited_audit_resumed_without_resave=resumed, dpi=144, exterior_pixels=exterior, noop_whole_page_pixels=whole,
        sha256={str(p): source_sha(p) for p in (edited, side, noop, noop_side)},
        elapsed_seconds=dict(proposal=proposed - started, acceptance=accepted - proposed,
                             edit=saved - accepted, audit=audited - saved, noop_and_audit=finished - audited,
                             total=finished - started))
    (ROOT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=list) + '\n', encoding='utf-8')
    print(json.dumps({k: summary[k] for k in ('verdict', 'input', 'acceptance_checks', 'paragraph', 'provenance_counts',
                                              'earlier_results', 'exterior_pixels', 'noop_whole_page_pixels',
                                              'sha256', 'elapsed_seconds')}, ensure_ascii=False, indent=2, default=list))
    print(json.dumps(summary['derived'], ensure_ascii=False, indent=1, default=list))


if __name__ == '__main__':
    main()
