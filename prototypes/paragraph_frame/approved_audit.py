"""Audit of a saved edit that tells approved changes from what must not change (issue #108).

The existing audit (indesign_evaluate.audit) compares the page outside one
region around the edited paragraph: every approved move (following
paragraphs, placed objects, line decorations, chain elements) fails it by
design. Here the proposal and the save record are read into a *change set*,
and the saved page is checked against it:

* ``approval``: the save belongs to this proposal (proposal and source
  digests); every moved line decoration and chain element was approved by
  this proposal, every approved one moved, and every approved id is one of
  this proposal's own candidates (an id of another edit is refused);
* ``approved``: each approved change happened as approved. A translation:
  every glyph (character, origin 0.01 pt), drawing (kind, colours, width,
  rectangle 0.02 pt), image (digest, box) and annotation (/Rect, /QuadPoints
  0.01 pt, the rest of its dictionary unchanged) of its area is found
  translated exactly, and nothing stays behind in its area. The text change:
  the new text is drawn in the approved frame;
* ``protected``: outside the edited paragraph, its frame and the approved
  areas (where they were and where they went), glyphs, drawings, images,
  annotations (paired by their dictionary and place, not by sort order) and
  pixels (72 and 150 dpi) are unchanged; the other pages' content and the
  font programs too;
* ``quality`` (apart from safety): MuPDF and Poppler extraction equal the
  source's with the replacement, the new text is found by search (a
  replacement broken over lines is not searched: unverified, its lines'
  joined text reported apart), the edited paragraph keeps its sizes and
  colours. ``pass`` only when every check was made and passed.

A change kind this audit cannot check yet (a stretched background, changed
line spacing: issue #107 allows them on approval, later) makes the verdict
``unverified``, never ``pass``. Each part is ``pass``, ``fail`` or
``not applicable`` with its reasons; ``verdict`` is ``pass`` only when
approval, approved and protected pass.

  .venv/bin/python -m prototypes.paragraph_frame.approved_audit <source> <output.pdf> [<page>]
  (reads <output>.proposal.json and <output>.save.json)
  .venv/bin/python -m prototypes.paragraph_frame.approved_audit --compare-root tmp/hub/<run> --documents <pdf> ... --out <json>
  (the existing audit beside this one, for every saved step of the runs)
"""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

import pymupdf
from pypdf import PdfReader

TOL = .01
KINDS = ('text', 'translate')


def _rect(r):
    return pymupdf.Rect(r)


def _union(rects):
    rects = [r for r in rects if r is not None]
    if not rects:
        return None
    out = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        out |= r
    return out


def followers_area(source, proposal):
    """The source area of the paragraphs following the edited one (they move with its growth), or None."""
    if len(proposal.get('paragraphs') or []) < 2:
        return None
    from .auto_detect import detect
    c, ps, *_ = detect(str(source), proposal['page'], proposal['selection'], proposal.get('occurrence', 0),
                       proposal.get('stored_conditions', True), tuple(proposal.get('continuation', ())))
    try:
        return _union([_rect(c.actual[i]['bbox']) for p in ps[1:] for i in p.ids if c.actual[i]['unicode'].strip()])
    finally:
        c.close()


def change_set(proposal, save, followers=None):
    """The approved changes of a saved edit (page space, y down).

    Each change: dict(kind, what, approval, old (area), dy (translation) | new (area), annotations (moved ones, PDF space)).
    ``followers``: the following paragraphs' source area (followers_area). Kinds other than ``text`` and
    ``translate`` are reported as unchecked."""
    lines = proposal['evidence']['observed']['lines']
    n = proposal['source_lines']
    edited = _union([_rect(l['bbox']) for l in lines[:n]])
    frame = _rect(proposal['evidence']['inferred']['frame'])
    changes = [dict(kind='text', what='edited paragraph', approval='acknowledge_inference', old=list(edited), new=list(frame | edited),
                    replace=[proposal['selection'], proposal['replacement']], occurrence=proposal.get('occurrence', 0))]
    dy = save.get('follower_translation')
    if followers is not None and isinstance(dy, (int, float)) and abs(dy) > TOL:
        changes.append(dict(kind='translate', what='following paragraphs', approval='acknowledge_inference', old=list(followers), dy=dy,
                            annotations=[]))
    elif followers is not None and isinstance(dy, str):
        changes.append(dict(kind='per-line translation', what='following paragraphs (column flow)', approval='acknowledge_inference'))
    for kind, approval in (('placed_objects_moved', 'acknowledge_inference'), ('decorations_moved', 'decorations'), ('chain_moved', 'chain')):
        for m in save.get(kind) or []:
            old, new = _rect(m['old']), _rect(m['new'])
            changes.append(dict(kind='translate', what=kind.replace('_moved', '').replace('_', ' '), approval=approval, id=m.get('id'),
                                old=list(old), dy=new.y0 - old.y0, annotations=m.get('annotations') or []))
    return changes


def _candidates(proposal, part):
    found = proposal['evidence'].get(part) or {}
    return {c['id'] for c in found.get('candidates', [])}


def approval_check(source, proposal, save):
    reasons = []
    if save.get('proposal_sha256') != proposal.get('proposal_sha256'):
        reasons.append('the save record is not of this proposal')
    if proposal.get('source_sha256') and proposal['source_sha256'] != _sha(source):
        reasons.append('the proposal is not of this source')
    for part, key in (('chain', 'chain_moved'), ('decorations', 'decorations_moved')):
        approved = set(proposal.get(part) or [])
        moved = {m['id'] for m in save.get(key) or [] if m.get('id')}
        if approved - _candidates(proposal, part):
            reasons.append(f'{part}: approved ids that are not this proposal\'s candidates: {sorted(approved - _candidates(proposal, part))}')
        if moved - approved:
            reasons.append(f'{part}: moved without approval: {sorted(moved - approved)}')
        if approved - moved:
            reasons.append(f'{part}: approved but not moved: {sorted(approved - moved)}')
    return dict(status='fail' if reasons else 'pass', reasons=reasons)


def _sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- page content -------------------------------------------------------------

def _glyphs(page):
    """Glyphs: (character, origin x, y, a point box at the centre of MuPDF's box: its box is the font's, taller than the engine's areas)."""
    out = []
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                for c in s['chars']:
                    r = pymupdf.Rect(c['bbox'])
                    mid = (r.tl + r.br) / 2
                    out.append((c['c'], c['origin'][0], c['origin'][1], pymupdf.Rect(mid, mid)))
    return out


def _drawings(page):
    return [(x.get('type'), str(x.get('fill')), str(x.get('color')), str(x.get('width')), pymupdf.Rect(x['rect'])) for x in page.get_drawings()]


def _images(page):
    """Images: (identity, box). An image XObject is its digest; a shading MuPDF reports as a raster (no xref) is
    sampled on the device grid, so its samples change with its place: it is its colour space (as in PR #100)."""
    return [((x.get('digest') if x.get('xref') else ('shading raster', x.get('cs-name'))), pymupdf.Rect(x['bbox']))
            for x in page.get_image_info(hashes=True, xrefs=True)]


def _annotations(path, page_number, page_height):
    """Annotations: (their dictionary without /Rect, /QuadPoints, /P, a page they name as its number; page-space rect; quads), from pypdf."""
    out = []
    reader = PdfReader(str(path))
    pages = {p.indirect_reference.idnum: n + 1 for n, p in enumerate(reader.pages) if p.indirect_reference is not None}
    page = reader.pages[page_number - 1]
    for ref in page.get('/Annots') or []:
        a = ref.get_object()
        x0, y0, x1, y1 = (float(v) for v in a['/Rect'])
        rect = pymupdf.Rect(min(x0, x1), page_height - max(y0, y1), max(x0, x1), page_height - min(y0, y1))
        body = {str(k): _plain(v, pages) for k, v in a.items() if k not in ('/Rect', '/QuadPoints', '/P', '/Parent', '/Popup', '/AP')}
        quads = [float(v) for v in a.get('/QuadPoints', [])]
        out.append((json.dumps(body, sort_keys=True, ensure_ascii=False, default=str), rect, quads))
    return out


def _plain(v, pages, depth=0):
    ref = getattr(v, 'idnum', None)
    if ref in pages:
        return f'page {pages[ref]}'
    v = v.get_object() if hasattr(v, 'get_object') else v
    if depth > 4:
        return str(v)
    if isinstance(v, dict):
        return {str(k): _plain(x, pages, depth + 1) for k, x in v.items() if k not in ('/P', '/Parent')}
    if isinstance(v, list):
        return [_plain(x, pages, depth + 1) for x in v]
    return str(v)


def _inside(rect, areas, pad=.5):
    return any((a + (-pad, -pad, pad, pad)).contains(rect) for a in areas)


def _touches(rect, areas):
    r = rect + (-.01, -.01, .01, .01)
    return any(r.intersects(a) for a in areas)


def _match(want, have, tol):
    """Pairs of ``want`` and ``have`` items (key, rect) equal in key and within ``tol``: (missing, extra)."""
    pool = list(have)
    missing = []
    for key, r in want:
        # (A shading's raster is placed on whole device pixels: up to a point at 72 dpi.)
        t = 1.0 if isinstance(key, tuple) and key[:1] == ('shading raster',) else tol
        hit = next((n for n, (k, q) in enumerate(pool) if k == key and max(abs(a - b) for a, b in zip(r, q)) <= t), None)
        if hit is None:
            missing.append((key, [round(v, 2) for v in r]))
        else:
            pool.pop(hit)
    return missing, [(k, [round(v, 2) for v in r]) for k, r in pool]


def audit(source, output, page_number=None, proposal=None, save=None):
    output = Path(output)
    proposal = proposal or json.loads(output.with_suffix('.proposal.json').read_text())
    save = save or json.loads(output.with_suffix('.save.json').read_text())
    page_number = page_number or proposal['page']
    changes = change_set(proposal, save, followers_area(source, proposal))
    unchecked = [c for c in changes if c['kind'] not in KINDS]
    text = next(c for c in changes if c['kind'] == 'text')
    moves = [c for c in changes if c['kind'] == 'translate']
    edited_old, edited_new = _rect(text['old']), _rect(text['new'])
    olds = [_rect(c['old']) for c in moves]
    news = [_rect(c['old']) + (0, c['dy'], 0, c['dy']) for c in moves]
    with pymupdf.open(source) as a, pymupdf.open(output) as b:
        pa, pb = a[page_number - 1], b[page_number - 1]
        height = pa.rect.height
        ga, gb, da, db, ia, ib = _glyphs(pa), _glyphs(pb), _drawings(pa), _drawings(pb), _images(pa), _images(pb)
        pages_same = len(a) == len(b)
        # --- approved: each translation exactly, nothing left behind, nothing added where it went ---
        approved = []
        # What may lie in a destination besides the move's own items: what the other approved moves carry
        # there (translated) and what stayed where it was (in no moved area). Each accounts for one item (a
        # duplicate is not explained by its original). The edited paragraph's new text goes where the moved
        # elements were, not where they went (its frame may reach past them): no glyph is excused for it.
        def carried(items, rect_of, key_of, old, c):
            cut = [edited_old] + [o for o, m in zip(olds, moves) if o is not old and abs(m['dy'] - c['dy']) > TOL]
            return [(key_of(k), rect_of(k) + (0, c['dy'], 0, c['dy'])) for k in items if _inside(rect_of(k), [old]) and not _inside(rect_of(k), cut, 0)]
        point = lambda k: pymupdf.Rect(k[1], k[2], k[1], k[2])
        kinds = dict(glyph=(ga, point, lambda k: k[0], lambda k: k[3]), drawing=(da, lambda k: k[4], lambda k: k[:4], lambda k: k[4]),
                     image=(ia, lambda k: k[1], lambda k: k[0], lambda k: k[1]))
        stays = {kind: [(key_of(k), rect_of(k)) for k in items if not _touches(box_of(k), olds)] for kind, (items, rect_of, key_of, box_of) in kinds.items()}

        def unexplained(extra, kind, tol, this, own):
            """Items found in a destination that no other approved move, no unmoved content and no new text accounts for.

            ``own``: this move's own items (paired already); an item another move carries too (overlapping moves
            with one translation) is not counted twice."""
            items, rect_of, key_of, _ = kinds[kind]
            pool = list(stays[kind])
            for c, old in zip(moves, olds):
                if old is not this:
                    pool += carried(items, rect_of, key_of, old, c)
            for key, r in own:
                hit = next((n for n, (k, q) in enumerate(pool) if k == key and max(abs(a - b) for a, b in zip(q, r)) <= tol), None)
                if hit is not None:
                    pool.pop(hit)
            out = []
            for key, r in extra:
                rect = pymupdf.Rect(r)
                t = 1.0 if isinstance(key, tuple) and key[:1] == ('shading raster',) else tol
                hit = next((n for n, (k, q) in enumerate(pool) if k == key and max(abs(a - b) for a, b in zip(q, rect)) <= t), None)
                if hit is None:
                    out.append((key, r))
                else:
                    pool.pop(hit)
            return out

        for c, old, new in zip(moves, olds, news):
            dy = c['dy']
            cut = [edited_old] + [o for o, m in zip(olds, moves) if o is not old and abs(m['dy'] - c['dy']) > TOL]
            mine = lambda r: _inside(r, [old]) and not _inside(r, cut, 0)
            g_want = [(ch, pymupdf.Rect(x, y + dy, x, y + dy)) for ch, x, y, bb in ga if mine(bb)]
            g_have = [(ch, pymupdf.Rect(x, y, x, y)) for ch, x, y, bb in gb if _inside(bb, [new])]
            d_want = [(k[:4], k[4] + (0, dy, 0, dy)) for k in da if mine(k[4])]
            d_have = [(k[:4], k[4]) for k in db if _inside(k[4], [new])]
            i_want = [(k[0], k[1] + (0, dy, 0, dy)) for k in ia if mine(k[1])]
            i_have = [(k[0], k[1]) for k in ib if _inside(k[1], [new])]
            gm, gx = _match(g_want, g_have, TOL)
            dm, dx = _match(d_want, d_have, .02)
            im, ix = _match(i_want, i_have, .02)
            added = dict(glyphs=[x for x in unexplained(gx, 'glyph', TOL, old, g_want) if x[0].strip()],
                         drawings=unexplained(dx, 'drawing', .02, old, d_want), images=unexplained(ix, 'image', .02, old, i_want))
            # Nothing of the source stays where it was (unless something else now lies there: new text, another move).
            elsewhere = [edited_new] + [n for n in news if n is not new]
            left = [(ch, round(x, 2), round(y, 2)) for ch, x, y, bb in gb if _inside(bb, [old], 0) and not _inside(bb, [new] + elsewhere, 0)
                    and ch.strip()]
            reasons = ([f'{len(gm)} glyphs not moved exactly: {gm[:3]}'] if gm else []) + ([f'{len(dm)} drawings not moved exactly'] if dm else []) \
                + ([f'{len(im)} images not moved exactly'] if im else []) + ([f'{len(left)} glyphs left behind: {left[:3]}'] if left else []) \
                + [f'{len(v)} {k} added where it went, not carried by an approved move: {v[:2]}' for k, v in added.items() if v]
            approved.append(dict(what=c['what'], id=c.get('id'), approval=c['approval'], dy=round(dy, 3), glyphs=len(g_want), drawings=len(d_want),
                                 images=len(i_want), shading_rasters=sum(isinstance(k, tuple) for k, _ in i_want),
                                 status='fail' if reasons else 'pass', reasons=reasons))
        # The text change: the new text is drawn inside the approved frame (MuPDF, the frame's text without spaces and line breaks).
        bare = lambda t: ''.join(t.split())
        drawn = bare(text['replace'][1]) in bare(pb.get_text(clip=edited_new + (-2, -2, 2, 2)))
        approved.append(dict(what='text', status='pass' if drawn else 'fail',
                             reasons=[] if drawn else ['the new text is not drawn inside the approved frame']))
        # --- protected: everything outside the edited and approved areas ---
        areas = [edited_old, edited_new] + olds + news
        keep = lambda items, rect_of: Counter((json.dumps(k[:-1], default=str), tuple(round(v, 2) for v in rect_of(k))) for k in items
                                             if not _touches(rect_of(k), areas))
        reasons = []
        # (A space has no ink: a line's trailing space may lie just past its frame. Extraction is quality's.)
        ink = lambda glyphs: [g for g in glyphs if g[0].strip()]
        for name, x, y in (('glyphs', ink(ga), ink(gb)), ('drawings', da, db), ('images', ia, ib)):
            ka, kb = keep(x, lambda k: k[-1]), keep(y, lambda k: k[-1])
            if ka != kb:
                reasons.append(f'{name} outside the approved areas changed: {sum((ka - kb).values())} gone, {sum((kb - ka).values())} new')
        pixels = {}
        for dpi in (72, 150):
            m = pymupdf.Matrix(dpi / 72, dpi / 72)
            xa, xb = pa.get_pixmap(matrix=m, alpha=False), pb.get_pixmap(matrix=m, alpha=False)
            for r in areas:
                box = ((r + (-2, -2, 2, 2)) * m).irect
                xa.set_rect(box, (255, 255, 255))
                xb.set_rect(box, (255, 255, 255))
            pixels[str(dpi)] = sum(p != q for p, q in zip(xa.samples, xb.samples))
        if any(pixels.values()):
            reasons.append(f'pixels outside the approved areas changed: {pixels}')
    # Annotations: each source annotation, translated when an approved move carries it, has its output twin.
    moved_rects = [(_rect(m['old']), _rect(m['new'])) for c in moves for m in c.get('annotations') or []]
    aa, ab = _annotations(source, page_number, height), _annotations(output, page_number, height)
    pool = list(ab)
    lost = []
    for body, rect, quads in aa:
        want, shift = rect, 0.
        for o, n in moved_rects:
            if max(abs(p - q) for p, q in zip(o, rect)) <= .02:
                want, shift = n, n.y0 - o.y0
        hit = next((k for k, (bb, r, q) in enumerate(pool) if bb == body and max(abs(p - s) for p, s in zip(r, want)) <= .02
                    and len(q) == len(quads) and all(abs(p - (s if n % 2 == 0 else s - shift)) <= .02 for n, (p, s) in enumerate(zip(q, quads)))), None)
        if hit is None:
            lost.append(dict(rect=[round(v, 2) for v in rect], annotation=body[:120]))
        else:
            pool.pop(hit)
    if lost or pool:
        reasons.append(f'annotations differ: {len(lost)} without their twin, {len(pool)} new')
    from .indesign_evaluate import programs
    if not pages_same or programs(source, page_number) != programs(output, page_number):
        reasons.append('another page or its forms changed')
    protected = dict(status='fail' if reasons else 'pass', reasons=reasons, pixels=pixels, annotations=len(aa))
    approval = approval_check(source, proposal, save)
    quality = _quality(source, output, page_number, text, edited_old, edited_new, olds + news)
    parts = [approval['status'], protected['status']] + [x['status'] for x in approved]
    verdict = 'fail' if 'fail' in parts else 'unverified' if unchecked else 'pass'
    return dict(verdict=verdict, approval=approval, approved=approved, protected=protected, quality=quality,
                unchecked=[c['kind'] for c in unchecked], changes=len(changes),
                mode='layout' if any(c['what'] != 'following paragraphs' for c in moves) else 'faithful')


def _styles(path, page, area, away):
    """(size, colour) of the inked glyphs whose centre lies in ``area`` and in none of ``away``."""
    out = set()
    with pymupdf.open(path) as d:
        for b in d[page - 1].get_text('rawdict')['blocks']:
            for l in b.get('lines', []):
                for s in l['spans']:
                    for c in s['chars']:
                        r = pymupdf.Rect(c['bbox'])
                        mid = (r.tl + r.br) / 2
                        if c['c'].strip() and area.contains(mid) and not any(a.contains(mid) for a in away):
                            out.add((round(s['size'], 2), s['color']))
    return out


def _quality(source, output, page, text, edited_old, edited_new, moved):
    """Extraction (MuPDF, Poppler) equal to the source's with the replacement; the new text found by search; the
    edited paragraph keeps its sizes and colours (its glyphs alone: approved moves into its area are not its text)."""
    old, new = text['replace']
    bare = lambda s: ''.join(s.split())
    out = {}
    for name, extract in (('mupdf', lambda p: pymupdf.open(p)[page - 1].get_text()),
                          ('poppler', lambda p: subprocess.run(['pdftotext', '-f', str(page), '-l', str(page), '-enc', 'UTF-8', str(p), '-'],
                                                               capture_output=True, check=True).stdout.decode())):
        a, b = bare(extract(source)), bare(extract(output))
        at = -1
        for _ in range(text.get('occurrence', 0) + 1):
            at = a.find(bare(old), at + 1)
        out[f'extraction_{name}'] = at >= 0 and b == a[:at] + bare(new) + a[at + len(bare(old)):]
    with pymupdf.open(output) as d:
        # Search finds text within a reader line. A replacement found by search is a success; one broken over
        # lines is not searched (None: unverified), and its lines' joined text is reported apart.
        hits = d[page - 1].search_for(new) if new.strip() else None
        if not new.strip():
            pass  # a deletion leaves no text to search for: no such check
        elif hits:
            out['search_mupdf'] = True
        else:
            lines = [''.join(s['text'] for s in l['spans']) for b in d[page - 1].get_text('dict')['blocks'] for l in b.get('lines', [])]
            wrapped = not any(new in l for l in lines)
            out['search_mupdf'] = None if wrapped else False
            out['search_wrapped_lines_text'] = wrapped and bare(new) in bare(''.join(lines))
    out['size_and_colour_kept'] = _styles(output, page, edited_new, moved) <= _styles(source, page, edited_new, moved)
    # A check not made (None) is no success: the status is then unverified, unless another check failed.
    values = list(out.values())
    out['status'] = 'fail' if any(v is False for v in values) else 'unverified' if any(v is None for v in values) else 'pass'
    return out


def compare_runs(root, documents, evaluations=('chain', 'separate', 'decoration', 'restore', 'placed')):
    """The existing audit (as each run recorded it) beside this audit, for every saved step of the runs in ``root`` (eval_hub layout).

    A step's source is found by the digest its proposal names, among the
    runs' fixtures, ``documents`` and the earlier steps' outputs."""
    import hashlib
    root = Path(root)
    digest = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    pool = {digest(p): p for p in documents}
    rows = []
    for name in evaluations:
        folder = root / name
        if not (folder / 'results.json').exists():
            continue
        for f in sorted(folder.glob('fixtures/**/*.pdf')):
            pool.setdefault(digest(f), f)
        results = json.loads((folder / 'results.json').read_text())
        outputs = sorted(folder.glob('*/**/out.pdf'))
        for out in outputs:
            pool.setdefault(digest(out), out)
        for out in outputs:
            proposal_path = out.with_suffix('.proposal.json')
            if not proposal_path.exists() or not out.with_suffix('.save.json').exists():
                continue
            proposal = json.loads(proposal_path.read_text())
            rel = out.relative_to(folder).parts[:-1]
            case, step = rel[0], '/'.join(rel[1:])
            old = _recorded(results, rel)
            source = pool.get(proposal.get('source_sha256'))
            row = dict(evaluation=name, case=case, step=step, old=old)
            if source is None:
                row['new'] = dict(verdict='not run', error='source not found by its digest')
            else:
                try:
                    a = audit(source, out, proposal['page'], proposal)
                    row['new'] = dict(verdict=a['verdict'], mode=a['mode'], approval=a['approval']['status'], quality=a['quality']['status'],
                                      approved=[x['status'] for x in a['approved']], protected=a['protected']['status'],
                                      reasons=a['approval']['reasons'] + [r for x in a['approved'] for r in x['reasons']] + a['protected']['reasons'])
                except Exception as exc:
                    row['new'] = dict(verdict='not run', error=f'{type(exc).__name__}: {exc}'[:200])
            rows.append(row)
            print(name, case, step, (old or {}).get('safety'), (old or {}).get('failed'), '->', row['new'].get('verdict'), row['new'].get('reasons', [])[:1], flush=True)
    return rows


def _recorded(results, rel):
    """The existing audit of the step whose output is ``<case>/<kind...>/<n>/out.pdf``."""
    x = results.get(rel[0])
    for part in rel[1:]:
        if isinstance(x, dict):
            x = x.get(part)
        elif isinstance(x, list) and part.isdigit() and int(part) <= len(x):
            x = x[int(part) - 1]
        else:
            return None
    if not isinstance(x, dict):
        return None
    return dict(outcome=x.get('outcome'), safety=x.get('safety'), quality=x.get('quality'), failed=x.get('failed'))


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('source', nargs='?')
    p.add_argument('output', nargs='?')
    p.add_argument('page', nargs='?', type=int)
    p.add_argument('--compare-root', help='eval_hub layout: compare the existing audit with this one on every saved step')
    p.add_argument('--documents', nargs='*', default=[])
    p.add_argument('--out')
    args = p.parse_args()
    if args.compare_root:
        rows = compare_runs(args.compare_root, args.documents)
        table = Counter((r['evaluation'], 'old safety ' + str((r['old'] or {}).get('safety')), 'new ' + r['new'].get('verdict', '?')) for r in rows)
        out = dict(rows=rows, table=[list(k) + [v] for k, v in sorted(table.items())])
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
        for k, v in sorted(table.items()):
            print(*k, v)
    else:
        print(json.dumps(audit(args.source, args.output, args.page), ensure_ascii=False, indent=1, default=str))


if __name__ == '__main__':
    main()
