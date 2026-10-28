"""The same chains without drawing proofs: does one more line fit the page at all (issue #103)?

  PYTHONPATH=. .venv/bin/python prototypes/paragraph_frame/chain_geometry.py <pdf> <page> <survey.json> <out.json>

For the paragraphs of a chain_survey output whose chain did not hold, each
reached line is taken as its glyphs and the paint in its slot (no
decoration.group): what the moved lines would meet, or "fits". Evaluation only.
"""
import json, sys, glob
from pathlib import Path
import pymupdf


def main():
    from prototypes.paragraph_frame.auto_detect import detect
    from prototypes.paragraph_frame import chain
    from prototypes.paragraph_frame.engine import Refused
    from prototypes.paragraph_frame.auto import _glyph_area
    src, page, survey, out = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
    rows = [r for r in json.load(open(survey)) if r.get('outcome') == 'chain' and r.get('unavailable')]
    result = []
    def element(c, spec, el, lines, followers, taken, delta, frame, obstacles, page_m):
        ids = [i for l in el for i in l['ids']]
        slot = (el[0]['slot'][0], el[-1]['slot'][1])
        ext = el[0]['extent']
        for l in el[1:]:
            ext = ext | l['extent']
        band = pymupdf.Rect(ext.x0, slot[0], ext.x1, slot[1])
        rects = [pymupdf.Rect(c.actual[i]['bbox']) for i in ids]
        rects += [pymupdf.Rect(o['bounds']) for o in obstacles if o['kind'] != 'container' and band.contains(pymupdf.Rect(o['bounds']))]
        b = rects[0]
        for r in rects[1:]:
            b |= r
        row = dict(text=''.join(l['text'] for l in el), rows=[x for l in el for x in l['rows']], ids=ids, top=el[0]['top'], bottom=max(l['bottom'] for l in el))
        return dict(bounds=b, old=list(b), new=list(b + (0, delta, 0, delta)), row=row, evidence=dict(translation=delta))
    chain._element = element
    for r in rows:
        try:
            c, ps, spec, at, ev = detect(src, page, r['selection'])
            try:
                f = chain.plan(c, ps, spec, ev, spec['line_height'], followers=[_glyph_area(c, p) for p in ps[1:]])
                result.append(dict(text=r['text'], drawing=r['unavailable'][:200], geometric=(f or {}).get('unavailable') or 'fits',
                                   lines=[u['row']['text'][:24] for u in (f or {}).get('units', [])]))
            finally:
                c.close()
        except Exception as exc:
            result.append(dict(text=r['text'], error=str(exc)[:200]))
    Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
