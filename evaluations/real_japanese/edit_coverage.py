"""Edit coverage on fixed real-PDF cases through the unified edit-intent API.

  .venv/bin/python -m evaluations.real_japanese.edit_coverage [--root tmp/<new directory>]
Cases come from edit-coverage-cases.json (fixed before evaluating). Each case
is proposed from its glyph occurrence and replacement; a proposed case is
accepted (simulating the user's explicit approval), saved into its own
directory and audited independently: per-owner ActualText and drawn text,
structure, other owners, drawings, pixels outside the approved rectangles
(MuPDF and Poppler at 144 dpi) and reopen. The original PDF is only read.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time

from PIL import Image, ImageChops, ImageDraw
import pymupdf
from pypdf import PdfReader

from pdfeditor.adjacent_intent import accept_adjacent_intent, edit_adjacent_intent, propose_adjacent_intent
from pdfeditor.content_stream import ContentPage
from pdfeditor.edit_intent import accept_edit_intent, edit_edit_intent, open_edit_result, propose_edit_intent
from pdfeditor.marked_content import observe_marked_content, structure_objects
from pdfeditor.selection import make_selection, source_sha

CASES = Path('evaluations/real_japanese/edit-coverage-cases.json')
PR81 = Path('tmp/osaka-adjacent-intent-quality/edited.pdf')
PR81_SHA = '79840e338c2097e6712166a651feeccd5242344b93721b1acaf22465aeec98c2'


def observe(path):
    marked = observe_marked_content(path)
    reader = PdfReader(path); content = ContentPage(path, 1); rows = {}
    try:
        data = content.streams[-content.page.xref]
        for s in marked['scopes']:
            a = s['association']; lo, hi = s['content_byte_range']
            glyphs = [dict(text=ch.text, code=ch.code.hex(), gid=content.actual[ch.source_orders[0]]['gid'],
                           font=e.state.font.name, font_xref=e.state.font.xref,
                           origin=list(content.actual[ch.source_orders[0]]['origin']))
                      for e in content.events if not e.invocation and lo <= e.operator.start < hi for ch in e.chars]
            ref = a.get('owner_reference')
            rows[a.get('mcid')] = dict(owner=ref, association=a, properties=s['properties'], glyphs=glyphs,
                                       actualtext=reader.get_object(ref['xref']).get('/ActualText') if ref else None,
                                       envelope=data[s['begin_byte_range'][0]:s['end_byte_range'][1]].hex())
        fonts = {f[0]: hashlib.sha256(content.document.extract_font(f[0])[3]).hexdigest()
                 for f in content.document[0].get_fonts(full=True)}
    finally:
        content.close()
    with pymupdf.open(path) as document:
        drawings = [{k: v for k, v in d.items() if k != 'seqno'} for d in document[0].get_drawings()]
    return dict(rows=rows, fonts=fonts, drawings=drawings, reader=reader)


def render(path, prefix):
    with pymupdf.open(path) as document:
        document[0].get_pixmap(dpi=144, alpha=False).save(str(prefix) + '-mupdf.png')
    subprocess.run(['pdftoppm', '-singlefile', '-r', '144', '-png', str(path), str(prefix) + '-poppler'],
                   check=True, capture_output=True)


def pixels(first, second, masks=()):
    result = {}
    for renderer in ('mupdf', 'poppler'):
        with Image.open(str(first) + '-' + renderer + '.png') as a, Image.open(str(second) + '-' + renderer + '.png') as b:
            diff = ImageChops.difference(a.convert('RGB'), b.convert('RGB'))
            changed = diff.getbbox()
            for m in masks:
                # Raster cells intersecting each exact approved rectangle; no margin.
                ImageDraw.Draw(diff).rectangle((math.floor(m['x0'] * 2), math.floor(m['y0'] * 2),
                                                math.ceil(m['x1'] * 2) - 1, math.ceil(m['y1'] * 2) - 1), fill=(0, 0, 0))
            raw = diff.tobytes()
            result[renderer] = dict(outside_pixels=sum(bool(raw[i] or raw[i + 1] or raw[i + 2])
                                                       for i in range(0, len(raw), 3)), changed_raster_bbox=changed)
    return result


def regions(proposal):
    if proposal['route'] == 'single-span':
        return [proposal['routes']['single-span']['region']]
    inner = proposal['routes']['adjacent-span']['proposal']['regions']
    return inner['owners'] + ([inner['follower']] if inner['follower'] else [])


def changed_owners(proposal):
    if proposal['route'] == 'single-span':
        return [proposal['normalized']['owner']['mcid']], None
    owners = proposal['routes']['adjacent-span']['proposal']['owners']
    follower = proposal['routes']['adjacent-span']['proposal']['follower']
    return [owners['first']['mcid'], owners['second']['mcid']], follower.get('mcid') if follower.get('approved') else None


def audit(source, edited, before, proposal, root):
    after = observe(edited)
    mcids, follower = changed_owners(proposal)
    xrefs = tuple(before['rows'][m]['owner']['xref'] for m in mcids)
    omit = xrefs if len(xrefs) > 1 else xrefs[0]
    found = proposal['normalized']
    old_text = ''.join(before['rows'][m]['actualtext'] for m in mcids)
    edit = found.get('edit')
    expected = old_text[:edit['start']] + edit['text'] + old_text[edit['end']:] if len(mcids) == 1 else None
    owner_texts = [dict(mcid=m, before=before['rows'][m]['actualtext'], after=after['rows'][m]['actualtext'],
                        drawn=''.join(g['text'] for g in after['rows'][m]['glyphs'])) for m in mcids]
    others = [m for m in before['rows'] if m not in mcids and m != follower]
    checks = dict(
        actualtext_equals_drawn=all(o['after'] == o['drawn'] for o in owner_texts),
        logical_text=(''.join(o['after'] for o in owner_texts) == expected) if expected is not None else
        ''.join(o['after'] for o in owner_texts) == proposal['routes']['adjacent-span']['proposal']['logical_after'],
        structure_except_changed_actualtext=structure_objects(after['reader'], omit_actual_text=omit)
        == structure_objects(before['reader'], omit_actual_text=omit),
        changed_structure_objects=sorted(k for k, v in structure_objects(before['reader']).items()
                                         if structure_objects(after['reader']).get(k) != v),
        other_owners_unchanged=all(after['rows'][m] == before['rows'][m] for m in others),
        original_fonts_unchanged=all(after['fonts'].get(x) == sha for x, sha in before['fonts'].items()),
        drawings_unchanged=after['drawings'] == before['drawings'],
        follower_semantics_unchanged=follower is None or all(
            after['rows'][follower][k] == before['rows'][follower][k] for k in ('owner', 'association', 'properties', 'actualtext')))
    checks['changed_structure_objects_are_owners'] = checks['changed_structure_objects'] == sorted('%d:0' % x for x in xrefs)
    render(edited, root / 'edited')
    return dict(owner_texts=owner_texts, checks=checks, pixels=pixels(root.parent / 'source', root / 'edited', regions(proposal)))


def run_case(case, source, before, root):
    root.mkdir(parents=True)
    ids = list(range(case['anchor'], case['anchor'] + len(case['selected'])))
    drawn = ''.join(before['glyph_text'][i] for i in ids)
    record = dict(case, glyph_ids=ids)
    if drawn != case['selected']:
        return dict(record, outcome='case-error', reason='drawn text %r differs from the fixed case' % drawn)
    started = time.monotonic()
    proposal = propose_edit_intent(source, make_selection(source, 1, glyph_ids=ids), case['replacement'],
                                   work_dir=root / 'work')
    found = proposal['normalized']
    record.update(proposal_sha256=proposal['model_sha256'], status=proposal['status'], route=proposal['route'],
                  selection_owners=[o['mcid'] for o in found['selection_owners']],
                  normalized=dict(changed_text=found['changed_text'], inserted=found['inserted'], rule=found['rule'],
                                  owner=found['owner']['mcid'] if found['owner'] else None, edit=found.get('edit')),
                  routes={k: {f: v.get(f) for f in ('status', 'reason', 'capacity')} for k, v in proposal['routes'].items()},
                  propose_seconds=time.monotonic() - started)
    if proposal['status'] == 'refused':
        return dict(record, outcome='refused', reason=proposal['reason'])
    if proposal['status'] == 'ambiguous':
        return dict(record, outcome='needs-confirmation', reason='several boundaries; an explicit choice is required')
    acceptance = accept_edit_intent(source, proposal, work_dir=root / 'work')
    edited, side = root / 'edited.pdf', root / 'edited.json'
    result = edit_edit_intent(source, acceptance, edited, side, work_dir=root / 'work')
    reopened = open_edit_result(edited, side)
    record.update(after=result['after'], reopen=reopened['status'], contract=reopened['contract'],
                  sha256=dict(pdf=source_sha(edited), sidecar=source_sha(side)),
                  provenance=[{k: p[k] for k in ('owner', 'kind', 'unicode')} for p in result.get('provenance') or []],
                  saved_seconds=time.monotonic() - started)
    record.update(audit(source, edited, before, proposal, root))
    passed = (reopened['status'] == 'restored' and all(v for k, v in record['checks'].items() if k != 'changed_structure_objects')
              and all(p['outside_pixels'] == 0 for p in record['pixels'].values()))
    return dict(record, outcome='success' if passed else 'audit-failed')


def compatibility(source, root):
    """Case B against PR #81: the adjacent_intent API stays byte-identical; the unified route keeps more source glyphs."""
    root.mkdir(parents=True)
    ids = list(range(149, 153))
    proposal = propose_adjacent_intent(source, make_selection(source, 1, glyph_ids=ids), 'におきまして', work_dir=root / 'work')
    acceptance = accept_adjacent_intent(source, proposal, work_dir=root / 'work')
    edit_adjacent_intent(source, acceptance, root / 'adjacent-api.pdf', root / 'adjacent-api.json', work_dir=root / 'work')
    return dict(adjacent_intent_api_sha256=source_sha(root / 'adjacent-api.pdf'),
                adjacent_intent_api_equals_pr81=source_sha(root / 'adjacent-api.pdf') == PR81_SHA)


def compare_with_pr81(edited, root):
    if not PR81.exists():
        return None
    first, second = observe(PR81), observe(edited)
    texts = {m: (first['rows'][m]['actualtext'], second['rows'][m]['actualtext']) for m in (28, 29)}
    render(PR81, root / 'pr81')
    diff = pixels(root / 'pr81', root / 'edited')
    retained = lambda rows: sum(g['font'] == '/F1' for m in (28, 29) for g in rows[m]['glyphs'])
    return dict(actualtext_equal=all(a == b for a, b in texts.values()), actualtext=texts,
                structure_equal=structure_objects(first['reader']) == structure_objects(second['reader']),
                original_f1_glyphs=dict(pr81=retained(first['rows']), unified=retained(second['rows'])),
                whole_page_pixel_difference=diff)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='tmp/edit-coverage')
    args = parser.parse_args()
    spec = json.loads(CASES.read_text(encoding='utf-8'))
    source = Path(spec['source']['path'])
    assert source_sha(source) == spec['source']['sha256']
    root = Path(args.root); root.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    before = observe(source)
    content = ContentPage(source, 1)
    before['glyph_text'] = [g['unicode'] for g in content.actual]
    content.close()
    render(source, root / 'source')
    results = []
    for case in spec['cases']:
        try:
            results.append(run_case(case, source, before, root / case['id']))
        except Exception as exc:  # recorded, never hidden
            results.append(dict(case, outcome='error', reason='%s: %s' % (type(exc).__name__, exc)))
        print(case['id'], results[-1]['outcome'], results[-1].get('route'), results[-1].get('reason', '')[:120])
    b = next(r for r in results if r['id'] == 'B')
    compat = compatibility(source, root / 'compat')
    if b['outcome'] == 'success':
        compat['unified_vs_pr81'] = compare_with_pr81(root / 'B' / 'edited.pdf', root / 'B')
    assert source_sha(source) == spec['source']['sha256']
    summary = dict(schema='pdfengine-edit-coverage-results-1', cases_file=str(CASES), source=spec['source'],
                   results=results, compatibility=compat,
                   totals={o: sum(r['outcome'] == o for r in results) for o in
                           ('success', 'needs-confirmation', 'refused', 'audit-failed', 'case-error', 'error')},
                   elapsed_seconds=time.monotonic() - started)
    (root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=list) + '\n', encoding='utf-8')
    print(json.dumps(dict(totals=summary['totals'], compatibility=compat), ensure_ascii=False, indent=2, default=list))


if __name__ == '__main__':
    main()
