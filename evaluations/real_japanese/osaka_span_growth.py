"""Read-only qualification for a character-count-increasing single-Span edit.

Run from the repository root:
  .venv/bin/python -m evaluations.real_japanese.osaka_span_growth
Only unmodified corpus PDFs are read. No PDF/sidecar is saved unless one
candidate passes proposal, acceptance and plan_single_span_edit; this run
records the observations and the direct refusal of each probed candidate.
Fonts and the summary stay under ignored tmp/.
"""
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import time

import pymupdf
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from pypdf import PdfReader

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.layout import LayoutError
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import propose_single_span, accept_single_span
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.span_edit import plan_single_span_edit

ROOT = Path('tmp/osaka-single-span-growth')
CORPUS = Path('evaluations/realpdf/corpus')
# (MCID, replaced text, growth text). At most five per PDF; the second PDF is
# probed only because the first has no qualifying candidate.
SOURCES = [
    dict(name='word_osaka_guideline', sha256='9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe',
         # Prior approved Span62 right edge; observed column edge is 524.62.
         column_x1=525.0,
         candidates=[(28, 'において', 'におきまして'), (23, '御協力いただき', '御協力をいただき'),
                     (31, '周知いただき', '周知していただき'), (29, 'いたします', 'させていただきます')]),
    dict(name='word_osaka_fire_notice', sha256='02036efa843b1cbf18e8454583e9a160ed7d635e9d054fb639d6ff1f96ab3afa',
         column_x1=None,
         candidates=[(23, 'に係る', 'に関する'), (26, '場合は', '場合には'), (28, '今後、', '今後とも、'),
                     (30, 'については', 'につきましては'), (40, '通', '通電話')]),
]


def inventory(path):
    """Every text-bearing scope, its contract-relevant labels and right-side room."""
    marked = observe_marked_content(path); reader = PdfReader(path); content = ContentPage(path, 1)
    try:
        assert marked['complete'] and not content.errors
        owner_of = {}
        for s in marked['scopes']:
            lo, hi = s['content_byte_range']
            for e in content.events:
                if not e.invocation and lo <= e.operator.start < hi:
                    for ch in e.chars:
                        for i in ch.source_orders:
                            owner_of[i] = s['association'].get('mcid')
        with pymupdf.open(path) as document:
            drawings = [list(d['rect']) for d in document[0].get_drawings()]
        visible = [g['bbox'][2] for g in content.actual if g['unicode'].strip()]
        rows = []
        for s in marked['scopes']:
            a = s['association']; mcid = a.get('mcid')
            ids = sorted(i for i, m in owner_of.items() if m == mcid)
            if not ids:
                continue
            text = ''.join(content.actual[i]['unicode'] for i in ids)
            ref = a.get('owner_reference'); actual = reader.get_object(ref['xref']).get('/ActualText') if ref else None
            boxes = [content.actual[i]['bbox'] for i in ids]
            x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
            x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
            right = [(content.actual[j]['bbox'][0], owner_of.get(j)) for j in range(len(content.actual))
                     if j not in ids and content.actual[j]['bbox'][1] < y1 and content.actual[j]['bbox'][3] > y0
                     and content.actual[j]['bbox'][2] > x1 - .5]
            nearest = min(right) if right else None
            rows.append(dict(mcid=mcid, owner=ref and ref['xref'], owner_role=(a.get('owner_role') or {}).get('mapped'),
                tag=s['tag'], text=text, characters=len(text), actualtext_matches=actual == text,
                lines=len({round(content.actual[i]['origin'][1], 4) for i in ids}),
                bbox=[x0, y0, x1, y1], next_foreign_glyph_mcid=nearest and nearest[1],
                room_to_next_foreign_glyph_pt=nearest and nearest[0] - x1,
                line_drawings=[d for d in drawings if d[1] < y1 and d[3] > y0 and d[2] > x1],
                glyph_ids=ids,
                single_span_shape=s['tag'] == '/P' and (a.get('owner_role') or {}).get('mapped') == '/Span'
                and actual == text))
        return dict(scopes=len(marked['scopes']), glyphs=len(content.actual), drawings=drawings,
                    observed_column_x1=max(visible), rows=rows)
    finally:
        content.close()


def min_visible_advance(path, xref):
    """Smallest inked advance in the embedded program at the observed font size."""
    with pymupdf.open(path) as document:
        data = document.extract_font(xref)[3]
    font = TTFont(BytesIO(data)); glyphs = font.getGlyphSet(); best = None
    for cp, name in font.getBestCmap().items():
        pen = BoundsPen(glyphs); glyphs[name].draw(pen)
        if pen.bounds is None or chr(cp).isspace():
            continue
        advance = font['hmtx'][name][0]
        if best is None or advance < best[0]:
            best = (advance, chr(cp))
    result = dict(units=best[0], unicode=best[1], upem=font['head'].unitsPerEm, cmap_entries=len(font.getBestCmap()),
                  family=font['name'].getDebugName(4))
    font.close()
    return result


def probe(path, rows, column_x1, mcid, old, new):
    row = next(r for r in rows if r['mcid'] == mcid); text = row['text']
    start = text.index(old); edit = dict(start=start, end=start + len(old), text=new)
    content = ContentPage(path, 1)
    try:
        xref = next(e.state.font.xref for e in content.events for ch in e.chars if row['glyph_ids'][0] in ch.source_orders)
        data = content.document.extract_font(xref)[3]
    finally:
        content.close()
    font_path = ROOT / (path.stem + '-font-' + str(xref) + '.ttf'); font_path.write_bytes(data)
    recipe = dict(path=str(font_path), sha256=hashlib.sha256(data).hexdigest(), font_index=0)
    x0, y0, x1, y1 = row['bbox']
    result = dict(mcid=mcid, owner=row['owner'], before=text, after=text[:start] + new + text[edit['end']:],
                  edit=edit, characters=[len(text), len(text) + len(new) - len(old)],
                  provider_sha256=recipe['sha256'], font_xref=xref, regions={})
    # Tight: no room beyond the source glyphs. Column: the widest right edge
    # already justified by observation; never the page margin.
    for label, right in (('tight', x1), ('column', max(x1, column_x1))):
        region = dict(x0=x0, y0=float(math.floor(y0) - 2), x1=right, y1=float(math.ceil(y1) + 3))
        record = dict(region=region)
        try:
            selection = make_selection(path, 1, glyph_ids=row['glyph_ids'], explicit_width=region['x1'] - region['x0'])
            state = accept_single_span(path, propose_single_span(path, selection, region=region))
            record['proposal'] = 'accepted'
            unchanged = plan_single_span_edit(path, state, [])
            record['unchanged_layout_width_pt'] = unchanged['lines'][0]['width']
            record['width_limit_pt'] = region['x1'] - x0
            try:
                plan = plan_single_span_edit(path, state, [edit], provider=recipe)
                record.update(plan='qualified', layout_width_pt=plan['lines'][0]['width'], lines=plan['new_line_count'])
            except (PdfError, LayoutError) as exc:
                record.update(plan='REFUSED', reason=str(exc))
        except (PdfError, LayoutError) as exc:
            record.update(proposal='REFUSED', reason=str(exc))
        result['regions'][label] = record
    return result


def main():
    started = time.monotonic()
    ROOT.mkdir(parents=True, exist_ok=True)
    summary = dict(sources=[], successful_saves=0)
    for source in SOURCES:
        path = CORPUS / (source['name'] + '.pdf')
        assert source_sha(path) == source['sha256']
        begin = time.monotonic()
        observed = inventory(path)
        column_x1 = source['column_x1'] or observed['observed_column_x1']
        probes = [probe(path, observed['rows'], column_x1, *c) for c in source['candidates']]
        assert len(probes) <= 5 and source_sha(path) == source['sha256']
        qualified = [p for p in probes if any(r.get('plan') == 'qualified' for r in p['regions'].values())]
        fonts = {p['font_xref'] for p in probes}
        summary['sources'].append(dict(name=source['name'], sha256=source['sha256'], page=1,
            scopes=observed['scopes'], glyphs=observed['glyphs'], drawings=observed['drawings'],
            observed_column_x1=observed['observed_column_x1'], region_column_x1=column_x1,
            single_span_shaped_text_scopes=[r['mcid'] for r in observed['rows'] if r['single_span_shape']],
            inventory=[{k: v for k, v in r.items() if k != 'glyph_ids'} for r in observed['rows']],
            min_visible_advance={x: min_visible_advance(path, x) for x in sorted(fonts)},
            probes=probes, qualified=[p['mcid'] for p in qualified], elapsed_seconds=time.monotonic() - begin))
        if qualified:
            break
    summary['verdict'] = 'QUALIFIED' if summary['sources'][-1]['qualified'] else 'REFUSED'
    summary['elapsed_seconds'] = time.monotonic() - started
    (ROOT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for source in summary['sources']:
        for p in source['probes']:
            print(source['name'], p['mcid'], p['owner'], p['characters'],
                  {k: (v['proposal'], v.get('plan'), v.get('reason')) for k, v in p['regions'].items()})
    print(summary['verdict'], round(summary['elapsed_seconds'], 3))


if __name__ == '__main__':
    main()
