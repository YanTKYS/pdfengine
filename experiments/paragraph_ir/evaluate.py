"""Run the ParagraphIR PoC on the fixed cases and measure it (issue #124).

  .venv/bin/python -m experiments.paragraph_ir.evaluate --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir>

Per case (experiments/paragraph_ir/cases.json): import once; E1 (the case edit)
written to <id>-E1.pdf; E2 on the same IR (new -> old) written to <id>-E2.pdf;
undo twice on the IR; a fresh import from <id>-E1.pdf alone, new -> old, written
to <id>-alone.pdf. A step needing approval is first run without it (refused) and
then with the approval a user would give (growth into the container's empty
space); the operations are counted as click + select + type + one per approval.

Measures of a saved step (independent of the writer's own audit):
  protection  outside the removed and drawn lines (1 pt around): the glyphs (by origin), drawings, images,
              annotations, links, other pages; pixels (MuPDF 72/150 dpi, Poppler 72 dpi)
  structure   no MCID lost or added; the drawn glyphs inside the marked content their source line had
              (tags and optional content)
  search_copy search_copy.verified of the typed text (issues #114/#116)
  font        the drawn glyphs in the paragraph's own font resources (no substitute)
  placement   the drawn glyphs where the IR laid them out (0.15 pt), on the IR's baselines; lines at the
              paragraph's left edge (or its centre for centered text)
practical = all true (a null is unverified, never a success). restore: E2 and alone against the source: page
text and pixels (MuPDF 72 dpi).
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pymupdf

from experiments.route_poc.evaluate import _drawings, _pixels
from pdfeditor.content_stream import ContentPage

from . import adapter
from .ir import Refused

CASES = Path('experiments/paragraph_ir/cases.json')


def _bare(t):
    return ''.join(t.split())


def _glyphs(page, away):
    out = Counter()
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                for c in s['chars']:
                    o = pymupdf.Point(c['origin'])
                    if c['c'].strip() and not any(a.contains(o) for a in away):
                        out[(c['c'], round(c['origin'][0], 1), round(c['origin'][1], 1), round(s['size'], 1))] += 1
    return out


def _structure_of_drawn(output, page, ir, lines):
    """Per drawn glyph, the marked content open around the operator drawing it in ``output``."""
    cp = ContentPage(str(output), page)
    try:
        st = adapter._structure(cp)
        where = {}
        for e in cp.events:
            for c in e.chars:
                where.setdefault((c.text, round(c.origin[0], 1), round(c.origin[1], 1)), st.get(e.operator.start, []))
    finally:
        cp.close()
    out = []
    for l in lines:
        if l.retained:
            continue
        want = ()
        for i in range(l.start, l.end):
            c = ir.chars[i]
            if c.src is not None:
                want = c.src.marked
            elif i == l.start:
                nxt = next((x.src for x in ir.chars[i:l.end] if x.src is not None), None)
                want = nxt.marked if nxt else ir.chars[i - 1].src.marked if i and ir.chars[i - 1].src else ()
            if c.kind == 'glyph':
                got = where.get((c.text, round(l.xs[i - l.start], 1), round(l.baseline, 1)))
                out.append(got is not None and tuple((n, tuple(a)) for n, a in got) == want)
    return out


def measure(source, output, ir, lines, typed, at):
    page = ir.source['page']
    removed = {k for k in range(len(ir.source_lines)) if not any(l.retained and l.source_line == k for l in lines)}
    area = [pymupdf.Rect(ir.source_lines[k]['box']) + (-1, -1, 1, 1) for k in removed]
    area += [pymupdf.Rect(l.box(ir)) + (-1, -1, 1, 1) for l in lines if not l.retained and l.box(ir)]
    out = {}
    with pymupdf.open(str(source)) as a, pymupdf.open(str(output)) as b:
        pa, pb = a[page - 1], b[page - 1]
        prot = dict(glyphs=_glyphs(pa, area) == _glyphs(pb, area), drawings=_drawings(pa, []) == _drawings(pb, []),
                    images=sorted(str(x['bbox']) for x in pa.get_image_info()) == sorted(str(x['bbox']) for x in pb.get_image_info()),
                    annotations=[(x.type, tuple(x.rect)) for x in pa.annots()] == [(x.type, tuple(x.rect)) for x in pb.annots()],
                    links=[(tuple(x['from']), x.get('uri'), x.get('page')) for x in pa.get_links()] == [(tuple(x['from']), x.get('uri'), x.get('page')) for x in pb.get_links()],
                    other_pages=len(a) == len(b) and all(a[k].read_contents() == b[k].read_contents() for k in range(len(a)) if k != page - 1))
        pixels = {f'{r}{d}': _pixels(a, b, page, area, d, r) for r, d in (('mupdf', 72), ('mupdf', 150), ('poppler', 72))}
        prot['pixels'] = not any(pixels.values())
        out['protection'] = dict(prot, pixel_counts=pixels, result=all(prot.values()))
        ma, mb = adapter.mcids(a, pa), adapter.mcids(b, pb)
        inside = _structure_of_drawn(output, page, ir, lines)
        out['structure'] = dict(mcids_kept=ma == mb, tagged=bool(ma), drawn_in_source_marked_content=all(inside) if inside else None,
                                result=ma == mb and (all(inside) if inside else True))
        # Font, size and colour of the drawn glyphs: texttrace spans.
        fonts = {s.basefont.lstrip('/') for s in ir.styles.values()}
        drawn_xy = {(round(l.xs[i - l.start], 1), round(l.baseline, 1)) for l in lines if not l.retained
                    for i in range(l.start, l.end) if ir.chars[i].kind == 'glyph'}
        used = set()
        for span in pb.get_texttrace():
            for c in span['chars']:
                if (round(c[2][0], 1), round(c[2][1], 1)) in drawn_xy:
                    used.add(span['font'])
        out['font'] = dict(used=sorted(used), result=bool(drawn_xy) and all(any(u in f or f.endswith(u) or u.endswith(f.split('+')[-1]) for f in fonts) for u in used)
                           if drawn_xy else True)
        got = Counter((chr(c[0]), round(c[2][0], 1), round(c[2][1], 1)) for span in pb.get_texttrace() for c in span['chars'])
        want = [(ir.chars[i].text, round(l.xs[i - l.start], 1), round(l.baseline, 1)) for l in lines if not l.retained
                for i in range(l.start, l.end) if ir.chars[i].kind == 'glyph']
        near = lambda g: any(g[0] == h[0] and abs(g[1] - h[1]) <= .15 and abs(g[2] - h[2]) <= .15 for h in got)
        f = ir.frame
        edges = []
        for k, l in enumerate(lines):
            box = l.box(ir)
            if box is None:
                continue
            if f.alignment == 'center':
                edges.append(abs((box[0] + box[2]) / 2 - f.center) < .6 or l.retained)
            else:
                edges.append(l.retained or abs(l.xs[0] - (f.first_x if k == 0 else f.left)) < .3
                             or (l.source_line is not None and abs(l.xs[0] - ir.source_lines[l.source_line]['x0']) < .05))
        out['placement'] = dict(drawn_where_laid_out=all(near(g) for g in want), edges=all(edges),
                                result=all(near(g) for g in want) and all(edges))
    text = ir.text
    frame = pymupdf.Rect(ir.relations['paragraph_box'])
    for l in lines:
        if l.box(ir):
            frame |= pymupdf.Rect(l.box(ir))
    from prototypes.paragraph_frame.search_copy import measure as sc_measure, verified
    try:
        m = sc_measure(str(output), page, text, typed, at, list(frame + (-1, -1, 1, 1)), [l.baseline for l in lines])
        check = verified(m)
    except Exception as exc:
        m, check = dict(error=str(exc)), verified(dict(error=str(exc)))
    out['search_copy'] = dict(result=check['result'], route=check['route'], failed=check['failed'], note=check.get('note'),
                              measures={k: m.get(k) for k in ('search_full', 'search_per_line', 'extraction_mupdf', 'extraction_poppler', 'copy_equivalent')})
    parts = [out['protection']['result'], out['structure']['result'], out['font']['result'], out['placement']['result'],
             check['result'] if check['result'] != 'not applicable' else True]
    out['practical'] = False if any(p is False for p in parts) else None if any(p is None for p in parts) else True
    return out


def restore(source, output, page):
    with pymupdf.open(str(source)) as a, pymupdf.open(str(output)) as b:
        ta, tb = _bare(a[page - 1].get_text()), _bare(b[page - 1].get_text())
        pix = _pixels(a, b, page, [], 72, 'mupdf')
    return dict(text=ta == tb, same_characters=Counter(ta) == Counter(tb), pixels_differing=pix, result=ta == tb and pix == 0)


def _typed(ir, at, new):
    """The typed text as it stands in the IR text from ``at`` (gaps the source drew inside it kept)."""
    want, end = _bare(new), at
    while end < len(ir.chars) and len(_bare(ir.text[at:end])) < len(want):
        end += 1
    return ir.text[at:end]


def _save(ir, source, output):
    """write() without approval, then with the growth approval a user gives when that is all it asks for."""
    try:
        r = adapter.write(ir, source, output)
        r['unapproved'] = r['outcome']
    except Refused as exc:
        if 'needs approval' not in str(exc):
            return dict(outcome='refused', reason=str(exc), unapproved='refused')
        try:
            r = adapter.write(ir, source, output, approve_growth=True)
            r['unapproved'] = 'refused: ' + str(exc)
        except Refused as exc2:
            return dict(outcome='refused', reason=str(exc2), unapproved='refused: ' + str(exc))
    return r


def run_case(case, source, root):
    page = case['page']
    res = dict(id=case['id'])
    t = time.perf_counter()
    try:
        ir = adapter.import_paragraph(source, page, case['click'], case['old'])
    except Refused as exc:
        res.update(E1=dict(outcome='refused', reason=f'import: {exc}'))
        return res
    res['import'] = dict(seconds=round(time.perf_counter() - t, 2), ir=ir.summary())
    imported = ir.snapshot()
    a, b = ir.selection
    # E1
    try:
        rec = ir.replace(a, b, case['new'])
    except Refused as exc:
        res['E1'] = dict(outcome='refused', reason=str(exc))
        return res
    out1 = root / f"{case['id']}-E1.pdf"
    r = _save(ir, source, out1)
    r['operations'] = 3 + len(r.get('approvals') or [])
    if r['outcome'] == 'saved':
        lines = ir.layout()
        r['ir_lines'] = [ir.text[l.start:l.end] for l in lines]
        r['checks'] = measure(source, out1, ir, lines, _typed(ir, a, case['new']), a)
        e1_lines, e1_text = lines, ir.text
    res['E1'] = r
    if r['outcome'] != 'saved':
        return res
    # E2 on the same IR: the typed text back to the old.
    added = ''.join(c.text for c in ir.chars[rec['at']:rec['at'] + rec['inserted']])
    ir.replace(rec['at'], rec['at'] + rec['inserted'], rec['old'])
    out2 = root / f"{case['id']}-E2.pdf"
    r2 = _save(ir, source, out2)
    if r2['outcome'] == 'saved':
        r2['restore'] = restore(source, out2, page)
        r2['identical_to_import'] = ir.snapshot() == imported
    res['E2'] = r2
    # Undo on the model: E2 then E1.
    ir.undo()
    after_one = ir.text == e1_text
    ir.undo()
    res['undo'] = dict(after_first_undo_is_E1=after_one, after_second_undo_is_import=ir.snapshot() == imported)
    # PDF alone: a fresh import from E1's output, click on the typed text, type the old text back.
    l0 = next(l for l in e1_lines if l.start <= rec['at'] + max(rec['inserted'] - 1, 0) < l.end or l.start <= rec['at'] < l.end)
    idx = min(max(rec['at'], l0.start), l0.end - 1)
    click = [l0.xs[idx - l0.start] + 1.0, l0.baseline - 2.0]
    out3 = root / f"{case['id']}-alone.pdf"
    try:
        ir3 = adapter.import_paragraph(out1, page, click, case['new'])
        a3, b3 = ir3.selection
        ir3.replace(a3, b3, case['old'])
        r3 = _save(ir3, out1, out3)
        if r3['outcome'] == 'saved':
            r3['checks'] = measure(out1, out3, ir3, ir3.layout(), _typed(ir3, a3, case['old']), a3)
            r3['restore'] = restore(source, out3, page)
    except Refused as exc:
        r3 = dict(outcome='refused', reason=str(exc))
    r3['click'] = [round(v, 2) for v in click]
    res['alone'] = r3
    res['typed_added'] = added
    return res


def main():
    from prototypes.paragraph_frame.fidelity_evaluate import source_of
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    p.add_argument('--only', nargs='*')
    args = p.parse_args()
    where = SimpleNamespace(fusei=args.fusei or '', pdfjs=args.pdfjs or '')
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / 'results.json'
    results = json.loads(path.read_text()) if args.only and path.exists() else {}
    for case in json.loads(CASES.read_text())['cases']:
        if args.only and case['id'] not in args.only:
            continue
        source = source_of(case['document'], where)
        if not source.exists():
            results[case['id']] = dict(id=case['id'], E1=dict(outcome='input missing'))
            continue
        if hashlib.sha256(source.read_bytes()).hexdigest() != case['sha256']:
            results[case['id']] = dict(id=case['id'], E1=dict(outcome='input differs'))
            continue
        for f in root.glob(f"{case['id']}-*.pdf"):
            f.unlink()
        t = time.perf_counter()
        res = run_case(case, source, root)
        res['seconds'] = round(time.perf_counter() - t, 2)
        results[case['id']] = res
        e1 = res.get('E1', {})
        print(case['id'], e1.get('outcome'), (e1.get('reason') or '')[:160], 'practical', (e1.get('checks') or {}).get('practical'),
              'E2', res.get('E2', {}).get('outcome'), 'alone', res.get('alone', {}).get('outcome'), flush=True)
        path.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


if __name__ == '__main__':
    main()
