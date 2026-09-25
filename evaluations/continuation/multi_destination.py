"""Reviewed LibreOffice source -> two confirmed destinations inside the reviewed page-six area.

The single-destination evaluation (evaluate.py) confirmed the empty area
[55,80,385,120] on page 6. Here the evaluator splits exactly that area into two
non-intersecting regions and states their flow order and page-entry order.
Nothing outside the reviewed area is assumed empty, and the order is the
evaluator's decision, not a reading order inferred by the engine.
Only evaluator decisions live here; PDFs, text, glyph logs and rasters stay in
the ignored runs/ directory.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import sys

import pymupdf
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader

from pdfeditor.content_stream import ContentPage
from pdfeditor.continuation import PROVENANCE, confirm_continuation_destination, markers, slot_id
from pdfeditor.elements import _close, _paint_value
from pdfeditor.model import Rect
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.pdf_save import font_fingerprints
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow, plan_shared_flow
from evaluations.attributed.evaluate import font_mapping_audit
from evaluations.continuation import evaluate as single
from evaluations.continuation.resources import generated_block_bytes, inventory
from evaluations.elements.evaluate import audit_render, extraction
from evaluations.flow_transaction.evaluate import normalize, refuse
from evaluations.realpdf.evaluate import image_fingerprints
from evaluations.story_flow.evaluate import annotation_fingerprint


ROOT, BASE = single.ROOT, single.BASE
# The area reviewed by the single-destination evaluation, split by the evaluator.
REVIEWED_AREA = [55, 80, 385, 120]
SPLIT = {'page6-a': dict(page=6, bounds=[55, 80, 385, 100], x=56.8, width=326, first_baseline=92),
         'page6-b': dict(page=6, bounds=[55, 101.5, 385, 120], x=56.8, width=326, first_baseline=113.6)}
PAGE_ENTRY_ORDER = {'page6-a': 10, 'page6-b': 20}
PROTECTED = [398, 58, 540, 111]
EXTRA = '確認した空き領域へ同じ文章の続きを配置し、再編集と保存後の文字位置を確認します。' * 2
# The previous allocation put 209 characters into the source slots and 36 on
# page 6 (two lines). 60 added characters leave about 16 for page 6: one line.
EXTRA_A_ONLY = EXTRA[:60]
TEXTS = {'a-only': EXTRA_A_ONLY, 'shorten': EXTRA_A_ONLY, 'b-added': EXTRA, 'regrow': EXTRA, 'both': EXTRA,
         'second': EXTRA.replace('再編集', '追加編集'), 'capacity': EXTRA * 2}
SEQUENTIAL = ('a-only', 'b-added', 'second', 'shorten', 'regrow', 'noop1', 'noop2', 'noop3')
SIMULTANEOUS = ('both', 'noop1')
DEPENDENCIES = single.DEPENDENCIES + ['evaluations/continuation/evaluate.py']


def _check_split():
    area = Rect(*REVIEWED_AREA)
    rects = [Rect(*r['bounds']) for r in SPLIT.values()]
    if not all(area.contains(r) for r in rects) or rects[0].intersects(rects[1]):
        raise ValueError('split regions must lie inside the reviewed area and must not intersect')
    if len(set(PAGE_ENTRY_ORDER.values())) != len(PAGE_ENTRY_ORDER):
        raise ValueError('page-entry orders must be unique')


def prepare():
    """The reviewed story and regions of evaluate.py, with page 6 split into two destinations."""
    _check_split()
    source = single.SOURCE
    if source_sha(source) != single.SOURCE_SHA:
        raise ValueError('reviewed source differs')
    story = single.prepare_story()
    pid = story['logical']['id']
    regions = {ident: dict(page=c['page'], bounds=c['bounds'], x=c['layout']['x'], width=c['layout']['width'],
                           first_baseline=c['layout']['baseline']) for ident, c in story['containers'].items()}
    regions.update(deepcopy(SPLIT))
    confirmed = {}
    for region in SPLIT:
        d = confirm_continuation_destination(source, destination_id='reviewed-' + region, paragraph_id=pid,
            region_id=region, page=SPLIT[region]['page'], bounds=SPLIT[region]['bounds'],
            insertion='before-page-program', graphics_state='isolated-pdf-initial-state',
            page_entry_order=PAGE_ENTRY_ORDER[region])
        confirmed[d['destination_id']] = d
    state = confirm_shared_flow(source, {pid: story}, flow_id='reviewed-multi-continuation', paragraph_order=[pid],
        regions=regions, region_order=list(regions), slot_regions={pid: {'A': 'A', 'B': 'B'}},
        paragraph_policies={pid: dict(min_line_height=21.6, first_line_indent=0, keep_together=False,
            break_before='auto', break_after='auto', empty=dict(kind='reserve-line', ascent=8.4, descent=2.1))},
        follows=[], protected_regions={str(i): [dict(role='fixed', bounds=PROTECTED)] for i in (4, 5, 6)},
        continuation_destinations=confirmed)
    return state, pid, {d['region_id']: d for d in confirmed.values()}


def _edits(state, pid, logical, extra):
    return {pid: dict(edits=[dict(start=0, end=len(state['paragraphs'][pid]['logical']['text']), runs=[
        *[dict(text=logical['text'][s['start']:s['end']], style_id=s['style_id']) for s in logical['style_spans']],
        dict(text=extra, style_id='body')])])}


def chain(pdf, state, dests):
    """Generated blocks in page-program order, verified from the saved bytes against the bindings."""
    result = {}
    for page in sorted({d['page'] for d in dests.values()}):
        data = PdfReader(pdf).pages[page - 1].get_contents().get_data()
        found = []
        for region, d in dests.items():
            if d['page'] != page:
                continue
            binding = state['destination_bindings'][d['destination_id']]
            begin, end = markers(d)
            if 'end' not in binding:
                if begin in data or end in data:
                    raise ValueError('an unused destination has a marker')
                continue
            block = data[binding['start']:binding['end']]
            if (data.count(begin) != 1 or data.count(end) != 1 or not block.startswith(begin) or not block.endswith(end)
                    or hashlib.sha256(block).hexdigest() != binding['block_sha256']
                    or binding['page_entry_order'] != d['page_entry_order']):
                raise ValueError('destination binding does not name its own block')
            found.append((binding['start'], binding['end'], region, d['page_entry_order']))
        found.sort()
        if [a for a, *_ in found] != [0] + [b for _, b, *_ in found[:-1]]:
            raise ValueError('generated blocks do not form a contiguous page-entry prefix')
        if [o for *_, o in found] != sorted(o for *_, o in found):
            raise ValueError('generated blocks are not in confirmed page-entry order')
        result[str(page)] = [dict(region=r, start=a, end=b, bytes=b - a) for a, b, r, _ in found]
    return result


def region_diff(before_png, after_png, rects, dpi=144):
    """Poppler any-channel differences outside every allowed region (each with a 1pt margin)."""
    with Image.open(before_png) as a, Image.open(after_png) as b:
        if a.size != b.size:
            raise ValueError('rendered page size changed')
        r, g, blue = ImageChops.difference(a.convert('RGB'), b.convert('RGB')).split()
        maximum = ImageChops.lighter(ImageChops.lighter(r, g), blue)
        total = sum(maximum.histogram()[1:])
        draw = ImageDraw.Draw(maximum)
        for x0, y0, x1, y1 in rects:
            draw.rectangle([int((x0 - 1) * dpi / 72), int((y0 - 1) * dpi / 72),
                            int((x1 + 1) * dpi / 72 + .999), int((y1 + 1) * dpi / 72 + .999)], fill=0)
        return dict(changed=total, outside=sum(maximum.histogram()[1:]))


def audit(before, after, state, initial, report, directory, original_text, *, noop=False, owned=None):
    """The single-destination audit, with several regions per page and page-entry text order."""
    directory.mkdir()
    visual = {}
    edited = {r['page'] for r in state['regions'].values()}
    for page in range(1, state['page_count'] + 1):
        if page in edited or noop:
            rects = [r['bounds'] for r in state['regions'].values() if r['page'] == page]
            union = Rect(*rects[0]) if rects else None
            for rect in rects[1:]:
                union = union.union(Rect(*rect))
            v = audit_render(before, after, page, directory / f'page-{page}', union.tuple() if union else (0, 0, 0, 0),
                             noop=noop or page not in edited, edited_pages=edited)
            # Stricter than the union mask: nothing may change between the regions.
            split = region_diff(directory / f'page-{page}' / 'before.png', directory / f'page-{page}' / 'after.png', rects)
            if split['outside']:
                raise ValueError(f'Poppler difference outside the confirmed regions on page {page}')
            visual[str(page)] = dict(mupdf_equal=v['mupdf_page_pixels'], poppler_changed_pixels=v['poppler_diff']['all_changed_pixels'],
                poppler_outside_regions_changed_pixels=split['outside'])
    for step in report['steps']:
        font_mapping_audit(after, step['report'])
    extracted = extraction(after, directory, 'saved')['pages']
    for page in range(1, state['page_count'] + 1):
        text = normalize(original_text[page - 1])
        initial_slots = [s for s in initial['slots'].values() if initial['regions'][s['region_id']]['page'] == page]
        source_slots = [s for s in state['slots'].values() if state['regions'][s['region_id']]['page'] == page
                        and s.get('creation_provenance') != PROVENANCE]
        # Generated blocks paint before the page program, in page-entry order:
        # an independent extractor reads them in that order, not in flow order.
        generated = sorted((s for s in state['slots'].values() if state['regions'][s['region_id']]['page'] == page
                            and s.get('creation_provenance') == PROVENANCE),
                           key=lambda s: state['destination_bindings'][s['destination_id']]['start'])
        if initial_slots:
            old = ''.join(normalize(s['binding']['paragraph']['text']) for s in initial_slots)
            if text.count(old) != 1:
                raise ValueError('source Unicode is not uniquely delimited')
            text = text.replace(old, ''.join(normalize(s['binding']['paragraph']['text']) for s in source_slots))
        text = ''.join(normalize(s['binding']['paragraph']['text']) for s in generated) + text
        if normalize(extracted[page - 1]) != text:
            raise ValueError(f'complete-page Unicode differs on {page}')
    old, new = PdfReader(before), PdfReader(after)
    with pymupdf.open(before) as a, pymupdf.open(after) as b:
        for i in range(len(a)):
            if i + 1 not in edited and a[i].get_pixmap(dpi=144).samples != b[i].get_pixmap(dpi=144).samples:
                raise ValueError('fixed page changed')
            pa, pb = interpreted_paints(before, i + 1), interpreted_paints(after, i + 1)
            paint = lambda p: [_paint_value(e) for e in p['events'] if e['kind'] not in ('fill-text', 'stroke-text', 'ignore-text')]
            if pa['errors'] or pb['errors'] or not _close(paint(pa), paint(pb)):
                raise ValueError('fixed paint changed')
            if image_fingerprints(a[i]) != image_fingerprints(b[i]) or annotation_fingerprint(old, i + 1) != annotation_fingerprint(new, i + 1):
                raise ValueError('image/annotation changed')
            aliases = {g['font_resource'][1:] for step in report['steps'] if step['report']['selection']['page'] == i + 1
                       for g in step['report']['glyph_plan']}
            retargeted = {x[1:] for x in report['generated_font_outcome'].get(str(i + 1), {}) if x in (owned or {}).get(str(i + 1), {})}
            if ([f for f in font_fingerprints(b, i) if f[0][3] not in aliases]
                    != [f for f in font_fingerprints(a, i) if f[0][3] not in retargeted]):
                raise ValueError('original font resource changed')
    return dict(renderers=visual, all_page_unicode=True, all_nontext_paint_images_annotations_original_fonts=True, cid_gid_w=True,
                allocation={sid: dict(range=s['range'], occupancy=s['occupancy']) for sid, s in state['slots'].items()})


def resources(pdf, state, dests, source_aliases, source):
    records = state.get('generated_fonts', {})
    inv = inventory(pdf, records=records, source=source)
    pages = sorted({str(r['page']) for r in state['regions'].values()}, key=int)
    for page, entries in inv['pages'].items():
        if any(e['kind'] == 'unknown' for e in entries):
            raise ValueError(f'unrecorded font resource on page {page}')
        if {e['alias'] for e in entries if e['kind'] == 'original'} != source_aliases[page]:
            raise ValueError(f'source font resource removed or changed on page {page}')
    return dict(pdf_bytes=Path(pdf).stat().st_size,
        page_font_resources={p: len(inv['pages'][p]) for p in pages},
        generated_fonts_by_page={p: len(records.get(p, {})) for p in pages},
        generated_font_slots={p: sorted(r['slot_id'] for r in records.get(p, {}).values()) for p in pages},
        type0_fonts=inv['type0_fonts'], generated_fonts=inv['owned_font_roots'],
        generated_font_graph_objects=inv['owned_font_graph_objects'],
        generated_block_bytes={r: generated_block_bytes(pdf, d['page'], d) for r, d in dests.items()})


COUNTS = ('page_font_resources', 'generated_fonts_by_page', 'generated_font_slots', 'type0_fonts', 'generated_fonts',
          'generated_font_graph_objects')


def series(directory, names, state, pid, dests, original_text, source_aliases, source, texts=TEXTS):
    """Run one editing series; every stage is re-opened and audited before the next."""
    initial = deepcopy(state)
    logical = deepcopy(state['paragraphs'][pid]['logical'])
    ids = {r: slot_id(d) for r, d in dests.items()}
    stages, creation, allocations = [], {}, {}
    pdf, previous, previous_blocks = source, None, None
    previous_sizes = resources(source, state, dests, source_aliases, source)
    for name in names:
        noop = name.startswith('noop')
        edits = {} if noop else _edits(state, pid, logical, texts[name])
        preview = plan_shared_flow(pdf, state, edits)
        single.write(directory / f'{name}-plan.json', preview)
        out, side = directory / f'{name}.pdf', directory / f'{name}.json'
        report = edit_shared_flow(pdf, state, out, side, edits)
        single.write(directory / f'{name}-report.json', report)
        if report['plan'] != preview:
            raise ValueError('executed plan differs')
        opened = open_shared_flow(out, side)
        if opened['status'] != 'restored':
            raise ValueError(opened['reason'])
        updated = opened['state']
        if updated['contract_sha256'] != initial['contract_sha256']:
            raise ValueError('confirmation changed')
        active = {r for r, sid in ids.items() if sid in updated['slots'] and updated['slots'][sid]['occupancy'] is not None}
        expected_active = {'a-only': {'page6-a'}, 'shorten': {'page6-a'}}.get(name, set(ids))
        if active != expected_active:
            raise ValueError(f'{name}: active destinations {sorted(active)} differ from {sorted(expected_active)}')
        created = {r for r, sid in ids.items() if sid in report['plan']['new_slots']}
        expected_new = {'a-only': {'page6-a'}, 'b-added': {'page6-b'}, 'both': set(ids)}.get(name, set())
        if created != expected_new:
            raise ValueError(f'{name}: created {sorted(created)} instead of {sorted(expected_new)}')
        for r in created:
            mutation = updated['slots'][ids[r]]['creation_binding']['mutation']
            boundary = 0 if name != 'b-added' else state['destination_bindings'][dests['page6-a']['destination_id']]['end']
            if (mutation['start'], mutation['insertion_order'], mutation['owner']) != (boundary, PAGE_ENTRY_ORDER[r], ids[r]):
                raise ValueError('new block did not enter the page-entry chain at its confirmed boundary')
        for r, sid in ids.items():
            if sid in updated['slots']:
                creation.setdefault(r, updated['slots'][sid]['creation_binding'])
                if updated['slots'][sid]['creation_binding'] != creation[r]:
                    raise ValueError('generated creation provenance changed')
        if name == 'shorten' and updated['slots'][ids['page6-b']]['binding']['paragraph']['text']:
            raise ValueError('dormant slot still paints text')
        blocks = chain(out, updated, dests)
        checks = {}
        if noop:
            for ident, s in state['slots'].items():
                if s['range'] != updated['slots'][ident]['range'] or s['occupancy'] != updated['slots'][ident]['occupancy']:
                    raise ValueError('no-op allocation changed')
            if updated['paragraphs'] != state['paragraphs'] or updated['continuation_destinations'] != state['continuation_destinations']:
                raise ValueError('no-op changed paragraph, style or destination records')
            if any(single.stable_slot(s) != single.stable_slot(updated['slots'][i]) for i, s in state['slots'].items()):
                raise ValueError('no-op changed slot identity, allocation, layout or inline style')
            glyphs = lambda r: [[{k: g[k] for k in single.GLYPH_FIELDS} for g in step['report']['glyph_plan']] for step in r['steps']]
            if glyphs(previous) != glyphs(report):
                raise ValueError('no-op changed planned glyphs')
            if [b['region'] for b in blocks['6']] != [b['region'] for b in previous_blocks['6']]:
                raise ValueError('no-op changed the page-entry order')
            checks = dict(paragraph_style_destination_records_equal=True, slot_identity_allocation_layout_style_equal=True,
                          planned_glyph_fields_equal=list(single.GLYPH_FIELDS), planned_glyphs=sum(map(len, glyphs(report))),
                          page_entry_order_equal=True)
        proof = audit(pdf, out, updated, initial, report, directory / (name + '-audit'), original_text, noop=noop,
                      owned=state.get('generated_fonts', {}))
        # q/Q/cm stay outside text objects on every edited page, including
        # both page-entry blocks; the source's PDF version is kept.
        nesting = single.operator_nesting(out, sorted({r['page'] for r in updated['regions'].values()}))
        if single.pdf_header(out) != single.pdf_header(source):
            raise ValueError('saving changed the PDF version')
        sizes = resources(out, updated, dests, source_aliases, source)
        outcome = report['generated_font_outcome']
        if noop:
            if any(sizes[k] != previous_sizes[k] for k in COUNTS):
                raise ValueError('no-op changed font/resource counts')
            if any(set(v.values()) != {'reused'} for v in outcome.values()):
                raise ValueError('no-op wrote a new font object')
            if updated['generated_fonts'] != state['generated_fonts']:
                raise ValueError('no-op changed generated font records')
        allocations[name] = {sid: dict(range=s['range'], occupancy=s['occupancy']) for sid, s in updated['slots'].items()}
        stages.append(dict(stage=name, status='passed', restored=True, saves=report['saves'], new_slots=sorted(created),
            active=sorted(active), page_entry_chain=blocks, **checks, **proof, operator_nesting=nesting,
            pdf_header=single.pdf_header(out), resources=dict(sizes, font_outcome=outcome)))
        single.write(directory / 'stages.json', stages)
        print(name, 'passed', flush=True)
        pdf, state, previous, previous_sizes, previous_blocks = out, updated, report, sizes, blocks
    return stages, allocations, pdf, state, previous


def compare_series(directory, state, pid, dests, source, texts=TEXTS):
    """Sequential and simultaneous creation from the same confirmed state; they must converge."""
    original_text = extraction(source, directory, 'original')['pages']
    source_aliases = {p: {e['alias'] for e in v} for p, v in inventory(source, source=source)['pages'].items()}
    results = {}
    for label, names in (('sequential', SEQUENTIAL), ('simultaneous', SIMULTANEOUS)):
        (directory / label).mkdir()
        results[label] = series(directory / label, names, deepcopy(state), pid, dests, original_text, source_aliases,
                                source, texts)
    sequential, simultaneous = results['sequential'], results['simultaneous']
    # Creating both blocks in one transaction and one after the other ends in the same chain.
    first = {s['stage']: s for s in sequential[0]}
    second = {s['stage']: s for s in simultaneous[0]}
    if ([b['region'] for b in first['b-added']['page_entry_chain']['6']] != [b['region'] for b in second['both']['page_entry_chain']['6']]
            or sequential[1]['b-added'] != simultaneous[1]['both']):
        raise ValueError('simultaneous and sequential creation differ in page-entry order or allocation')
    last_pdf, last_state = sequential[2], sequential[3]
    refused = refuse(directory, 'capacity', lambda o, j: edit_shared_flow(last_pdf, last_state, o, j,
        {pid: dict(edits=[dict(start=0, end=0, text=texts['capacity'], style_id='body')])}))
    if refused['reason'] != single.CAPACITY_REASON:
        raise ValueError('capacity negative control was refused for an unexpected reason: ' + refused['reason'])
    return sequential[0], simultaneous[0], refused


def run(directory):
    engine = {p.name: source_sha(p) for p in sorted((ROOT / 'pdfeditor').glob('*.py'))}
    environment = single.tools()
    state, pid, dests = prepare()
    providers = single.provider_evidence(state['paragraphs'][pid]['style_registry'])
    reviewed, provider_evidence_source = single.reviewed_providers()
    if providers != reviewed:
        raise ValueError('reflow providers differ from the reviewed story_styles providers: ' + json.dumps(providers, sort_keys=True))
    single.write(directory / 'initial.json', state)
    replay = single.source_replay(directory, state)
    baseline = resources(single.SOURCE, state, dests,
        {p: {e['alias'] for e in v} for p, v in inventory(single.SOURCE, source=single.SOURCE)['pages'].items()}, single.SOURCE)
    try:
        source_nesting = single.operator_nesting(single.SOURCE, sorted({r['page'] for r in state['regions'].values()}))
    except ValueError as exc:
        raise ValueError('reviewed source pages already violate operator nesting; pdfengine output cannot be attributed') from exc
    sequential, simultaneous, refused = compare_series(directory, state, pid, dests, single.SOURCE)
    if engine != {p.name: source_sha(p) for p in sorted((ROOT / 'pdfeditor').glob('*.py'))}:
        raise ValueError('engine changed during evaluation')
    return dict(schema='pdfengine-multi-destination-evaluation-2', status='passed', source_url=single.URL,
        source_sha256=single.SOURCE_SHA, source_replay=replay, source_resources=baseline,
        source_operator_nesting=source_nesting, source_pdf_header=single.pdf_header(single.SOURCE),
        sequential=sequential, simultaneous=simultaneous, simultaneous_equals_sequential=True,
        negative_controls=[refused], destinations=dests, reviewed_area=REVIEWED_AREA, page_entry_order=PAGE_ENTRY_ORDER,
        scope='one external LibreOffice paragraph; the reviewed page-6 area split by the evaluator into two ordered destinations',
        providers=providers, provider_evidence_source=provider_evidence_source,
        environment=dict(engine_digest=hashlib.sha256(json.dumps(engine, sort_keys=True).encode()).hexdigest(), engine_sha256=engine,
            runner_sha256=source_sha(Path(__file__)), evaluation_dependencies_sha256={d: source_sha(ROOT / d) for d in DEPENDENCIES},
            python=sys.version.split()[0], platform=platform.platform(), pymupdf=pymupdf.VersionBind, **environment))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-name', required=True)
    args = parser.parse_args()
    directory = BASE / 'runs' / ('multi-' + args.run_name)
    directory.mkdir(parents=True)
    single.write(directory / 'summary.json', run(directory))
