"""Run the PoC on the fixed cases and measure it as the current approach is measured (issue #122).

  .venv/bin/python -m experiments.route_poc.evaluate --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir>

Per case: the edit (click, select, type; the PoC's approvals given as a user
would: growth into empty space, a fallback font), then the edit back from the
saved PDF alone. Each saved step is measured:

* saved: the old text is gone from the edited area and the new text is there
  (MuPDF, whitespace aside);
* protection: outside the edited area (old and new, 1 pt around), the glyphs,
  drawings, images, annotations and links are those of the source, the other
  pages' content is unchanged, and no pixel differs (MuPDF 72 and 150 dpi,
  Poppler 72 dpi) -- glyphs are placed by their origin, so a glyph of the next
  line whose box reaches into the area still counts; marked content: no MCID
  the page draws is lost, and the page is untagged (the PoC draws the new
  lines outside marked content, so on a tagged page they are untagged);
* search and copy: search_copy.measure on the drawn lines (issue #114);
* appearance: the paragraph's own font kept (a fallback font is a visible
  change, approved per edit; never counted as kept, also not when the edit
  back reuses the fallback the edit embedded);
* placement: the lines drawn on the planned baselines, at the paragraph's
  left edge and first-line start (0.3 pt).

practical = saved and protection and marked content kept and search/copy
verified and the font kept and placement; a check not made is null. re-edit
= the edit back saved from the output alone; restore = the edit back's page
text and pixels equal the source's.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import pymupdf

from . import retypeset

CASES = Path('experiments/route_poc/cases.json')


def _glyphs(page, away):
    out = Counter()
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                for c in s['chars']:
                    # By origin: a glyph of the next line whose box reaches into the area is still outside it.
                    o = pymupdf.Point(c['origin'])
                    if c['c'].strip() and not any(a.contains(o) for a in away):
                        out[(c['c'], round(c['origin'][0], 1), round(c['origin'][1], 1), round(s['size'], 1))] += 1
    return out


def _drawings(page, away):
    return Counter((x.get('type'), str(x.get('fill')), str(x.get('color')), tuple(round(v, 1) for v in x['rect']))
                   for x in page.get_drawings() if not any(a.contains(x['rect']) for a in away))


def _pixels(a, b, page, away, dpi, renderer):
    if renderer == 'mupdf':
        m = pymupdf.Matrix(dpi / 72, dpi / 72)
        pa, pb = a[page - 1].get_pixmap(matrix=m, alpha=False), b[page - 1].get_pixmap(matrix=m, alpha=False)
        for r in away:
            box = (r * m).irect
            pa.set_rect(box, (255, 255, 255))
            pb.set_rect(box, (255, 255, 255))
        return sum(x != y for x, y in zip(pa.samples, pb.samples))
    from PIL import Image, ImageChops, ImageDraw
    imgs = []
    for path in (a.name, b.name):
        out = subprocess.run(['pdftoppm', '-r', str(dpi), '-f', str(page), '-l', str(page), '-png', path], capture_output=True).stdout
        import io
        imgs.append(Image.open(io.BytesIO(out)).convert('RGB'))
    s = dpi / 72
    for im in imgs:
        for r in away:
            ImageDraw.Draw(im).rectangle((int(r.x0 * s), int(r.y0 * s), int(r.x1 * s) + 1, int(r.y1 * s) + 1), fill=(255, 255, 255))
    diff = ImageChops.difference(*imgs)
    return sum(1 for p in diff.getdata() if p != (0, 0, 0))


def _mcids(doc, page):
    data = b''.join(doc.xref_stream(x) for x in doc[page - 1].get_contents())
    return Counter(int(m) for m in re.findall(rb'/MCID\s+(\d+)', data))


def source_font(r):
    """The paragraph's own font name from a step's result: 'embedded (X)' or 'fallback (Y) for X' -> X."""
    f = r.get('font') or ''
    return f.split(' for ', 1)[1] if ' for ' in f else f[len('embedded ('):-1] if f.startswith('embedded (') else None


def measure(source, output, page, r, original_font=None):
    """The checks of a saved step ``r`` (retypeset.edit's result). ``original_font``: the paragraph's font in the
    first source (an edit back drawn in a fallback font embedded by the edit is not the paragraph's font)."""
    from prototypes.paragraph_frame.search_copy import measure as search_measure, verified
    old, new = pymupdf.Rect(r['old_area']), pymupdf.Rect(r['frame'])
    away = [old + (-1, -1, 1, 1), new + (-1, -1, 1, 1)]
    out = {}
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        pa, pb = a[page - 1], b[page - 1]
        bare = lambda t: ''.join(t.split())
        out['text_replaced'] = bare(r['expected']) == bare(pb.get_text(clip=new + (-1, -1, 1, 1)))
        prot = dict(glyphs=_glyphs(pa, away) == _glyphs(pb, away), drawings=_drawings(pa, away) == _drawings(pb, away),
                    images=sorted(str(x['bbox']) for x in pa.get_image_info()) == sorted(str(x['bbox']) for x in pb.get_image_info()),
                    annotations=[(x.type, tuple(x.rect)) for x in pa.annots()] == [(x.type, tuple(x.rect)) for x in pb.annots()],
                    links=[(tuple(x['from']), x.get('uri'), x.get('page')) for x in pa.get_links()] == [(tuple(x['from']), x.get('uri'), x.get('page')) for x in pb.get_links()],
                    other_pages=all(a[k].read_contents() == b[k].read_contents() for k in range(len(a)) if k != page - 1) and len(a) == len(b))
        pixels = {f'{rnd}{dpi}': _pixels(a, b, page, away, dpi, rnd) for rnd, dpi in (('mupdf', 72), ('mupdf', 150), ('poppler', 72))}
        prot['pixels'] = not any(pixels.values())
        out['protection'] = dict(prot, pixel_counts=pixels, result=all(prot.values()))
        lost = _mcids(a, page) - _mcids(b, page)
        tagged = bool(_mcids(a, page))
        # TextWriter draws the new lines outside any marked content: on a tagged page they are untagged, and the
        # paragraph's own marked content is left empty (its MCIDs stay, its text is gone).
        out['marked_content'] = dict(result=not lost and not tagged, mcids_lost=sorted(lost)[:20], tagged=tagged,
                                     new_text_tagged=False if tagged else None)
        # Placement: the drawn rows on the planned baselines and edges.
        rows = {}
        for blk in pb.get_text('rawdict')['blocks']:
            for l in blk.get('lines', []):
                for s in l['spans']:
                    for c in s['chars']:
                        if c['c'].strip() and new.contains(pymupdf.Point(c['origin'])):
                            rows.setdefault(min(r['baselines'], key=lambda y: abs(y - c['origin'][1])), []).append(c)
        out['placement'] = dict(result=bool(rows) and all(any(abs(c['origin'][1] - y) <= .3 for c in cs) for y, cs in rows.items())
                                and len(rows) == len(r['baselines']))
    m = search_measure(output, page, r['expected'], r['replacement'], r['at'], r['frame'], r['baselines'])
    check = verified(m)
    out['search_copy'] = dict(result=check['result'], route=check['route'], failed=check['failed'],
                              measures={k: m.get(k) for k in ('search_full', 'search_per_line', 'extraction_mupdf', 'extraction_poppler', 'copy_equivalent')})
    out['font_kept'] = r['font'] == f"embedded ({original_font or source_font(r)})"
    parts = [out['text_replaced'], out['protection']['result'], out['marked_content']['result'],
             check['result'] if check['result'] != 'not applicable' else True, out['font_kept'], out['placement']['result']]
    out['practical'] = False if any(p is False for p in parts) else None if any(p is None for p in parts) else True
    return out


def _click_back(output, page, r):
    """Where the user clicks to edit back: the first glyph of the new text in the saved PDF."""
    with pymupdf.open(output) as d:
        hits = [h for h in d[page - 1].search_for(r['replacement'][:4] or r['expected'][:4]) if pymupdf.Rect(r['frame']).intersects(h)]
    if hits:
        return [(hits[0].x0 + hits[0].x1) / 2, (hits[0].y0 + hits[0].y1) / 2]
    f = r['frame']
    return [f[0] + 3, r['baselines'][0] - 3]


def run(args):
    from prototypes.paragraph_frame.fidelity_evaluate import source_of
    where = SimpleNamespace(fusei=args.fusei or '', pdfjs=args.pdfjs or '')
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    results = {}
    for case in json.loads(CASES.read_text())['cases']:
        source = source_of(case['document'], where)
        if case.get('status') == 'input missing' or not source.exists():
            results[case['id']] = [dict(outcome='input missing')]
            continue
        if hashlib.sha256(source.read_bytes()).hexdigest() != case['sha256']:
            results[case['id']] = [dict(outcome='input differs')]
            continue
        steps, start, click, old, new = [], source, case['click'], case['old'], case['new']
        for n in (1, 2):
            out = root / f"{case['id']}-{n}.pdf"
            # The user's approvals: none first; then what the PoC asks for, as listed in its refusal.
            plain = retypeset.edit(str(start), str(root / f"{case['id']}-{n}-unapproved.pdf"), case['page'], click, old, new)
            r = retypeset.edit(str(start), str(out), case['page'], click, old, new, approve_growth=True, approve_font=True)
            r['unapproved'] = plain['outcome']
            r['operations'] = 3 + len(r.get('approvals') or [])
            if n == 1:
                original = source_font(r) if r['outcome'] == 'saved' else None
            if r['outcome'] == 'saved':
                r['checks'] = measure(start, out, case['page'], r, original)
            steps.append(r)
            print(case['id'], n, r['outcome'], (r.get('reason') or '')[:100], r.get('checks', {}).get('practical'), flush=True)
            if r['outcome'] != 'saved':
                break
            click, old, new, start = _click_back(out, case['page'], r), case['new'], case['old'], out
        if len(steps) == 2 and steps[1]['outcome'] == 'saved':
            with pymupdf.open(source) as a, pymupdf.open(root / f"{case['id']}-2.pdf") as b:
                bare = lambda t: ''.join(t.split())
                ta, tb = bare(a[case['page'] - 1].get_text()), bare(b[case['page'] - 1].get_text())
                pix = _pixels(a, b, case['page'], [], 72, 'mupdf')
            # text: the page's text in extraction order; same_characters: the same glyphs in another order (the
            # redrawn paragraph is extracted where TextWriter appended it, at the end of the page's content).
            steps[1]['restore'] = dict(text=ta == tb, same_characters=Counter(ta) == Counter(tb), pixels_differing=pix,
                                       result=ta == tb and pix == 0)
        results[case['id']] = steps
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', required=True)
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    run(p.parse_args())


if __name__ == '__main__':
    main()
