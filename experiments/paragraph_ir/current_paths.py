"""The current pdfeditor paths on the same fixed cases, with the inputs a user must give by hand (issue #124).

  .venv/bin/python -m experiments.paragraph_ir.current_paths --root <dir> --fusei <477.pdf> --pdfjs <pdf.js dir>

Path P (README "propose-page -> accept-page -> replace-text"): the paragraph's
observed line IDs are given by hand (--line), since the page-level proposal
refuses every one of these pages (multiple columns); embedded programs are
offered as font candidates. Path E ("observe -> select -> inspect-paragraph ->
edit-paragraph"): line IDs and the available width by hand, and the font file
for the new text by hand (the source programs are subsets; an installed font is
given: IPAGothic for Japanese, DejaVu Serif for the English case: a substitute).
The line IDs are taken from the lines the ParagraphIR import found (the same
paragraph), the width from its frame.

A saved output is measured like the IR's (protection outside the source and
output paragraphs, structure, search/copy, font kept); a refusal records its
reason. B2's results are the committed ones (cases.json, current_b2).
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pymupdf

from experiments.route_poc.evaluate import _drawings, _pixels

from . import adapter
from .evaluate import CASES, _bare, _glyphs

PY = '.venv/bin/python'
FONTS = dict(ja='/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf', en='/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf')


def _cli(*args, timeout=900):
    t = time.perf_counter()
    r = subprocess.run([PY, '-m', 'pdfeditor', *map(str, args)], capture_output=True, text=True, timeout=timeout)
    out = (r.stdout + r.stderr).strip()
    try:
        data = json.loads(r.stdout)
    except Exception:
        data = None
    return r.returncode, data, out[-600:], round(time.perf_counter() - t, 1)


def _line_ids(source, page, ir):
    from pdfeditor.page_proposal import _Analysis
    an = _Analysis(str(source), page)
    ids = []
    for line in an.lines:
        box = pymupdf.Rect(line['bbox'])
        c = pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
        if any((pymupdf.Rect(sl['box']) + (-1, -1, 1, 1)).contains(c) for sl in ir.source_lines):
            ids.append(line['id'])
    return ids


def measure(source, output, page, ir_source, click, typed):
    """Protection outside the source paragraph and the output's paragraph; structure; search/copy; font."""
    out = {}
    try:
        ir_out = adapter.import_paragraph(output, page, click, typed)
    except Exception as exc:
        return dict(error=f'the edited paragraph cannot be read back: {exc}', practical=False)
    area = [pymupdf.Rect(ir_source.relations['paragraph_box']) + (-1, -1, 1, 1), pymupdf.Rect(ir_out.relations['paragraph_box']) + (-1, -1, 1, 1)]
    with pymupdf.open(str(source)) as a, pymupdf.open(str(output)) as b:
        pa, pb = a[page - 1], b[page - 1]
        prot = dict(glyphs=_glyphs(pa, area) == _glyphs(pb, area), drawings=_drawings(pa, []) == _drawings(pb, []),
                    images=sorted(str(x['bbox']) for x in pa.get_image_info()) == sorted(str(x['bbox']) for x in pb.get_image_info()),
                    other_pages=len(a) == len(b) and all(a[k].read_contents() == b[k].read_contents() for k in range(len(a)) if k != page - 1))
        pixels = {f'{r}{d}': _pixels(a, b, page, area, d, r) for r, d in (('mupdf', 72), ('mupdf', 150), ('poppler', 72))}
        prot['pixels'] = not any(pixels.values())
        out['protection'] = dict(prot, pixel_counts=pixels, result=all(prot.values()))
        out['structure'] = dict(mcids_kept=adapter.mcids(a, pa) == adapter.mcids(b, pb), result=adapter.mcids(a, pa) == adapter.mcids(b, pb))
    fonts = {s.basefont.split('+')[-1] for s in ir_source.styles.values()}
    used = {s.basefont.split('+')[-1] for s in ir_out.styles.values()}
    out['font'] = dict(source=sorted(fonts), output=sorted(used), result=used <= fonts)
    from prototypes.paragraph_frame.search_copy import measure as sc_measure, verified
    at = ir_out.selection[0]
    lines = ir_out.layout()
    try:
        m = sc_measure(str(output), page, ir_out.text, ir_out.text[at:ir_out.selection[1]], at,
                       list(pymupdf.Rect(ir_out.relations['paragraph_box']) + (-1, -1, 1, 1)), [l.baseline for l in lines])
        check = verified(m)
    except Exception as exc:
        check = verified(dict(error=str(exc)))
    out['search_copy'] = dict(result=check['result'], failed=check['failed'], note=check.get('note'))
    parts = [out['protection']['result'], out['structure']['result'], out['font']['result'],
             check['result'] if check['result'] != 'not applicable' else True]
    out['practical'] = False if any(p is False for p in parts) else None if any(p is None for p in parts) else True
    return out


def path_p(case, source, root, ir):
    page, ids = case['page'], _line_ids(source, case['page'], ir)
    rec = dict(manual_inputs=dict(line_ids=ids), operations=3 + 1 + 1)  # + choose lines + accept the proposal
    d = root / f"{case['id']}-P"
    d.mkdir(exist_ok=True)
    code, prop, text, s = _cli('propose-page', source, '--page', page, '--json', d / 'proposal.json', *sum((['--line', i] for i in ids), []),
                               '--include-embedded-fonts')
    prop = json.loads((d / 'proposal.json').read_text()) if (d / 'proposal.json').exists() else prop
    if not prop or prop.get('status') != 'proposed':
        rec.update(outcome='refused', step='propose-page', reason=(prop or {}).get('refusals') or text, seconds=s)
        return rec
    code, data, text, s2 = _cli('accept-page', source, '--proposal', d / 'proposal.json', '--json', d / 'flow.json', '--receipt', d / 'receipt.json')
    if code or not (d / 'flow.json').exists():
        rec.update(outcome='refused', step='accept-page', reason=text, seconds=s + s2)
        return rec
    out = root / f"{case['id']}-P.pdf"
    code, data, text, s3 = _cli('replace-text', source, out, '--state', d / 'flow.json', '--state-output', d / 'flow2.json',
                                '--find', case['old'], '--replacement', case['new'])
    if code or not out.exists():
        rec.update(outcome='refused', step='replace-text', reason=text, seconds=s + s2 + s3)
        return rec
    rec.update(outcome='saved', seconds=s + s2 + s3)
    return rec


def _observed_ids(source, page, ir, d):
    """The paragraph's line IDs in pdfeditor's observe catalog (select takes these)."""
    _cli('observe', source, '--page', page, '--json', d / 'catalog.json')
    ids = []
    for line in json.loads((d / 'catalog.json').read_text())['lines']:
        b = line['bbox']
        c = pymupdf.Point((b['x0'] + b['x1']) / 2, (b['y0'] + b['y1']) / 2)
        if any((pymupdf.Rect(sl['box']) + (-1, -1, 1, 1)).contains(c) for sl in ir.source_lines):
            ids.append(line['id'])
    return ids


def path_e(case, source, root, ir):
    d0 = root / f"{case['id']}-E"
    d0.mkdir(exist_ok=True)
    page, ids = case['page'], _observed_ids(source, case['page'], ir, d0)
    width = round(ir.frame.right - ir.frame.left, 2)
    font = FONTS['en' if case['id'] == 'S-T1' else 'ja']
    rec = dict(manual_inputs=dict(line_ids=ids, width=width, font=font, edit_range='start/end in the inspected Unicode'),
               operations=3 + 4)  # + lines, width, font file, range
    d = root / f"{case['id']}-E"
    d.mkdir(exist_ok=True)
    code, data, text, s = _cli('select', source, d / 'selection.json', '--page', page, *sum((['--line', i] for i in ids), []), '--width', width)
    if code or not (d / 'selection.json').exists():
        rec.update(outcome='refused', step='select', reason=text, seconds=s)
        return rec
    code, data, text, s2 = _cli('inspect-paragraph', source, '--selection', d / 'selection.json', '--json', d / 'paragraph.json',
                                '--line-joiner', 'space' if case['id'] == 'S-T1' else 'none')
    if code or not (d / 'paragraph.json').exists():
        rec.update(outcome='refused', step='inspect-paragraph', reason=text, seconds=s + s2)
        return rec
    para = json.loads((d / 'paragraph.json').read_text())
    uni = para.get('unicode') or para.get('text') or ''
    k = _bare(uni).find(_bare(case['old']))
    if k < 0:
        rec.update(outcome='refused', step='edit range', reason='the selected text is not in the inspected paragraph', seconds=s + s2)
        return rec
    idx = [i for i, ch in enumerate(uni) if not ch.isspace()]
    start, end = idx[k], idx[k + len(_bare(case['old'])) - 1] + 1
    # The font for the new text, and (for joins drawn as spaces) for each source style: given by hand.
    spec = dict(path=font, font_index=0)
    fonts = dict(f=spec, **{st['id']: spec for st in para.get('styles', [])})
    (d / 'changes.json').write_text(json.dumps(dict(edits=[dict(start=start, end=end, text=case['new'], font_id='f')], fonts=fonts)))
    out = root / f"{case['id']}-E.pdf"
    code, data, text, s3 = _cli('edit-paragraph', source, out, '--paragraph', d / 'paragraph.json', '--edits', d / 'changes.json',
                                '--width', width, '--report', d / 'report.json')
    if code or not out.exists():
        rec.update(outcome='refused', step='edit-paragraph', reason=text, seconds=s + s2 + s3)
        return rec
    rec.update(outcome='saved', seconds=s + s2 + s3)
    return rec


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
    results = {}
    for case in json.loads(CASES.read_text())['cases']:
        if args.only and case['id'] not in args.only:
            continue
        source = source_of(case['document'], where)
        if not source.exists():
            results[case['id']] = dict(P=dict(outcome='input missing'), E=dict(outcome='input missing'))
            continue
        for f in root.glob(f"{case['id']}-*.pdf"):
            f.unlink()
        ir = adapter.import_paragraph(source, case['page'], case['click'], case['old'])
        res = {}
        for name, fn in (('P', path_p), ('E', path_e)):
            try:
                r = fn(case, source, root, ir)
            except Exception as exc:
                r = dict(outcome='not run', reason=f'evaluator error: {type(exc).__name__}: {exc}'[:300])
            if r.get('outcome') == 'saved':
                out = root / f"{case['id']}-{name}.pdf"
                with pymupdf.open(str(out)) as d:
                    hits = d[case['page'] - 1].search_for(case['new'][:3])
                click = [hits[0].x0 + 1, (hits[0].y0 + hits[0].y1) / 2] if hits else case['click']
                r['checks'] = measure(source, out, case['page'], ir, click, case['new'])
            res[name] = r
            print(case['id'], name, r.get('outcome'), r.get('step'), str(r.get('reason'))[:200], (r.get('checks') or {}).get('practical'), flush=True)
        results[case['id']] = res
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


if __name__ == '__main__':
    main()
