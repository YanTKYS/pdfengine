"""Reviewed LibreOffice source -> the reviewed page-six area, at a confirmed page-program boundary.

The single-destination evaluation (evaluate.py) inserts the continuation into
the reviewed empty area [55,80,385,120] of page 6 at the page entry. Here the
paragraph, providers, area and edits are the same; only the insertion
authority differs. The evaluator reviewed page 6's candidates and chose the
boundary between the page body's top-level q ... Q (text and underline
paths) and the CC-BY-SA logo's top-level q ... Q, fixed below by ID and
witnesses. The block therefore paints after the body and before the logo.
Nothing is chosen from geometry or from the order of a candidate list.
Only evaluator decisions live here; PDFs, text and rasters stay in runs/.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import sys

import pymupdf
from pypdf import PdfReader

from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import (BOUNDARY, BOUNDARY_STATE, PAINT, confirm_continuation_destination,
                                    inspect_continuation_boundaries, markers, slot_id)
from pdfeditor.elements import _close, _paint_value
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.pdf_save import font_fingerprints
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow, plan_shared_flow
from evaluations.attributed.evaluate import font_mapping_audit
from evaluations.continuation import evaluate as single
from evaluations.continuation.resources import inventory
from evaluations.elements.evaluate import audit_render, extraction
from evaluations.flow_transaction.evaluate import normalize, refuse
from evaluations.realpdf.evaluate import image_fingerprints
from evaluations.story_flow.evaluate import annotation_fingerprint


ROOT, BASE = single.ROOT, single.BASE
# Reviewed on the source with SHA-256 13665875...a5f3: page 6's two top-level
# groups. The boundary lies after the body group's final Q and before the
# logo group's q; 286 paint operators precede it and 15 (the logo) follow.
REVIEWED_BOUNDARY = dict(
    page=6, boundary_id='boundary-b848698b464255ff0b2b6f90', offset=17602, ordinal=1303,
    previous=dict(operator='Q', start=17601, end=17602,
                  sha256='4ae81572f06e1b88fd5ced7a1a000945432e83e1551e6f721ee9c00b8cc33260'),
    next=dict(operator='q', start=17603, end=17604,
              sha256='8e35c2cd3bf6641bdb0e2050b76932cbb2e6034a0ddacc1d9bea82a6ba57f7cf'),
    prefix_paint_operators=286, suffix_paint_operators=15)
DEPENDENCIES = single.DEPENDENCIES + ['evaluations/continuation/evaluate.py']
TEXT_SHOWING = frozenset({'Tj', 'TJ', "'", '"'})


def reviewed_boundary():
    """The engine must list the reviewed boundary as a safe candidate, with the same witnesses."""
    inspected = inspect_continuation_boundaries(single.SOURCE, REVIEWED_BOUNDARY['page'])
    found = [c for c in inspected['candidates'] if c['boundary_id'] == REVIEWED_BOUNDARY['boundary_id']]
    if len(found) != 1:
        raise ValueError('the reviewed page-6 boundary is not a safe candidate of the reviewed source')
    c = found[0]
    witnessed = dict(page=c['page'], boundary_id=c['boundary_id'], offset=c['offset'], ordinal=c['ordinal'],
        previous={k: c['previous'][k] for k in ('operator', 'start', 'end', 'sha256')},
        next={k: c['next'][k] for k in ('operator', 'start', 'end', 'sha256')},
        prefix_paint_operators=c['z_order']['prefix_paint_operators'],
        suffix_paint_operators=c['z_order']['suffix_paint_operators'])
    if witnessed != REVIEWED_BOUNDARY:
        raise ValueError('the reviewed page-6 boundary witnesses differ: ' + json.dumps(witnessed, sort_keys=True))
    content = ContentPage(single.SOURCE, REVIEWED_BOUNDARY['page'])
    try:
        suffix_text = sum(b.operator.name in TEXT_SHOWING for b in content.boundaries
                          if b.operator.start >= REVIEWED_BOUNDARY['offset'])
    finally:
        content.close()
    # The independent extractor reads in content-stream order; the page's own
    # text then precedes the block's text only because the suffix shows none.
    if suffix_text:
        raise ValueError('the reviewed suffix shows text; the page Unicode order is not the one this evaluation expects')
    summary = dict(candidates=len(inspected['candidates']), refused_boundaries=inspected['refused_boundaries'],
                   operators=inspected['operators'], refusal_reasons=inspected['refusal_reasons'],
                   useful_candidates=[x['boundary_id'] for x in inspected['candidates']
                                      if x['z_order']['prefix_paint_operators'] and x['z_order']['suffix_paint_operators']])
    return c, summary


def prepare():
    """evaluate.prepare(), with the page-6 destination at the reviewed page-program boundary."""
    if source_sha(single.SOURCE) != single.SOURCE_SHA:
        raise ValueError('reviewed source differs')
    story = single.prepare_story()
    pid = story['logical']['id']
    regions = {ident: dict(page=c['page'], bounds=c['bounds'], x=c['layout']['x'], width=c['layout']['width'],
                           first_baseline=c['layout']['baseline']) for ident, c in story['containers'].items()}
    regions['page6'] = dict(page=6, bounds=[55, 80, 385, 120], x=56.8, width=326, first_baseline=92)
    destination = confirm_continuation_destination(single.SOURCE, destination_id='reviewed-page6-boundary',
        paragraph_id=pid, region_id='page6', page=6, bounds=regions['page6']['bounds'], insertion=BOUNDARY,
        graphics_state=BOUNDARY_STATE, boundary=REVIEWED_BOUNDARY['boundary_id'])
    state = confirm_shared_flow(single.SOURCE, {pid: story}, flow_id='reviewed-boundary-continuation',
        paragraph_order=[pid], regions=regions, region_order=list(regions), slot_regions={pid: {'A': 'A', 'B': 'B'}},
        paragraph_policies={pid: dict(min_line_height=21.6, first_line_indent=0, keep_together=False,
            break_before='auto', break_after='auto', empty=dict(kind='reserve-line', ascent=8.4, descent=2.1))},
        follows=[], protected_regions={str(i): [dict(role='fixed', bounds=[398, 58, 540, 111])] for i in (4, 5, 6)},
        continuation_destinations={destination['destination_id']: destination})
    return state, pid, destination


def order(pdf, state, destination, source_program):
    """prefix | block | suffix of page 6, from the saved bytes, against the reviewed witnesses.

    Page 6 holds no source slot, so prefix and suffix are the source program's
    bytes before and after the confirmed boundary, unchanged.
    """
    binding = state['destination_bindings'][destination['destination_id']]
    data = PdfReader(pdf).pages[5].get_contents().get_data()
    begin, end = markers(destination)
    if 'end' not in binding:
        if begin in data or data != source_program or binding['boundary']['offset'] != REVIEWED_BOUNDARY['offset']:
            raise ValueError('an unused boundary changed page 6')
        return dict(block=False, offset=binding['boundary']['offset'])
    start, stop = binding['start'], binding['end']
    prefix, block, suffix = data[:start], data[start:stop], data[stop:]
    if (data.count(begin) != 1 or not block.startswith(begin) or not block.endswith(end)
            or hashlib.sha256(block).hexdigest() != binding['block_sha256'] or binding['boundary']['offset'] != start):
        raise ValueError('boundary binding does not name its own block')
    if prefix != source_program[:REVIEWED_BOUNDARY['offset']] or suffix != source_program[REVIEWED_BOUNDARY['offset']:]:
        raise ValueError('the confirmed prefix or suffix of page 6 changed')
    last, first = list(operators(prefix))[-1], list(operators(suffix))[0]
    for op, data_, witness in ((last, prefix, REVIEWED_BOUNDARY['previous']), (first, suffix, REVIEWED_BOUNDARY['next'])):
        if op.name != witness['operator'] or hashlib.sha256(data_[op.start:op.end]).hexdigest() != witness['sha256']:
            raise ValueError('the operators around the block are not the reviewed ones')
    paints = lambda part: sum(op.name in PAINT for op in operators(part))
    return dict(block=True, start=start, end=stop, block_bytes=stop - start, prefix_bytes=len(prefix),
                suffix_bytes=len(suffix), prefix_paint_operators=paints(prefix), suffix_paint_operators=paints(suffix),
                prefix_and_suffix_are_the_source_program=True)


def audit(before, after, state, initial, report, directory, original_text, *, noop=False, owned=None):
    """evaluate.audit(), with page 6's text after the page's own text (content-stream order)."""
    directory.mkdir()
    visual, edited = {}, {4, 5, 6}
    for page in range(1, state['page_count'] + 1):
        if page in edited or noop:
            r = next((r for r in state['regions'].values() if r['page'] == page), None)
            v = audit_render(before, after, page, directory / f'page-{page}', r['bounds'] if r else (0, 0, 0, 0),
                             noop=noop or page not in edited, edited_pages=edited)
            visual[str(page)] = dict(mupdf_equal=v['mupdf_page_pixels'], poppler_changed_pixels=v['poppler_diff']['all_changed_pixels'],
                                     poppler_outside_changed_pixels=v['poppler_diff_with_1pt_margin']['outside_changed_pixels'])
    for step in report['steps']:
        font_mapping_audit(after, step['report'])
    extracted = extraction(after, directory, 'saved')['pages']
    for page in range(1, state['page_count'] + 1):
        text = normalize(original_text[page - 1])
        initial_slots = [s for s in initial['slots'].values() if initial['regions'][s['region_id']]['page'] == page]
        current = [s for s in state['slots'].values() if state['regions'][s['region_id']]['page'] == page]
        if initial_slots:
            old = ''.join(normalize(s['binding']['paragraph']['text']) for s in initial_slots)
            if text.count(old) != 1:
                raise ValueError('source Unicode is not uniquely delimited')
            text = text.replace(old, ''.join(normalize(s['binding']['paragraph']['text']) for s in current))
        elif current:
            # The block paints after the confirmed prefix; the reviewed suffix shows no text.
            text = text + ''.join(normalize(s['binding']['paragraph']['text']) for s in current)
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


def compare(directory, page_entry, wait=4 * 3600):
    """The same edits at page entry (an evaluate.py run of this engine): same glyphs, same pixels.

    The page-entry run may still be running; its summary marks completion.
    """
    import time
    other = BASE / 'runs' / page_entry
    deadline = time.monotonic() + wait
    while not (other / 'summary.json').exists():
        if time.monotonic() > deadline:
            raise ValueError('the page-entry run did not complete')
        time.sleep(60)
    summary = json.loads((other / 'summary.json').read_text(encoding='utf-8'))
    result = {}
    for name in single.STAGES:
        mine = json.loads((directory / f'{name}-report.json').read_text(encoding='utf-8'))
        theirs = json.loads((other / f'{name}-report.json').read_text(encoding='utf-8'))
        glyphs = lambda r: [[{k: g[k] for k in single.GLYPH_FIELDS} for g in step['report']['glyph_plan']] for step in r['steps']]
        images = {}
        for path in sorted((directory / f'{name}-audit').glob('page-*/after.png')):
            twin = other / path.relative_to(directory)
            images[path.parent.name] = twin.exists() and twin.read_bytes() == path.read_bytes()
        if glyphs(mine) != glyphs(theirs) or not images or not all(images.values()):
            raise ValueError(f'{name}: the boundary and page-entry continuations differ in glyphs or pixels')
        result[name] = dict(planned_glyph_fields_equal=True, planned_glyphs=sum(map(len, glyphs(mine))),
                            poppler_images_equal=sorted(images))
    return dict(page_entry_run=page_entry, page_entry_engine_digest=summary['environment']['engine_digest'], stages=result)


def run(directory, page_entry=None):
    engine = {p.name: source_sha(p) for p in sorted((ROOT / 'pdfeditor').glob('*.py'))}
    environment = single.tools()
    chosen, inspection = reviewed_boundary()
    state, pid, destination = prepare()
    initial = deepcopy(state)
    providers = single.provider_evidence(state['paragraphs'][pid]['style_registry'])
    reviewed, provider_evidence_source = single.reviewed_providers()
    if providers != reviewed:
        raise ValueError('reflow providers differ from the reviewed story_styles providers: ' + json.dumps(providers, sort_keys=True))
    single.write(directory / 'initial.json', state)
    replay = single.source_replay(directory, state)
    original_text = extraction(single.SOURCE, directory, 'original')['pages']
    logical = deepcopy(state['paragraphs'][pid]['logical'])
    sid = slot_id(destination)
    source_program = PdfReader(single.SOURCE).pages[5].get_contents().get_data()
    extra = '確認した空き領域へ同じ文章の続きを配置し、再編集と保存後の文字位置を確認します。' * 2
    source_aliases = {p: {e['alias'] for e in v} for p, v in inventory(single.SOURCE, source=single.SOURCE)['pages'].items()}
    baseline = single.resources(single.SOURCE, state, destination, source_aliases)
    source_nesting = single.operator_nesting(single.SOURCE, single.EDITED)
    source_header = single.pdf_header(single.SOURCE)
    stages, pdf, previous, creation, previous_sizes = [], single.SOURCE, None, None, baseline
    for name in single.STAGES:
        noop = name.startswith('noop')
        edits = ({pid: dict(edits=[dict(start=0, end=len(state['paragraphs'][pid]['logical']['text']), text='確認。', style_id='body')])}
                 if name == 'shorten' else {} if noop else
                 {pid: dict(edits=[dict(start=0, end=len(state['paragraphs'][pid]['logical']['text']), runs=[
                     *[dict(text=logical['text'][s['start']:s['end']], style_id=s['style_id']) for s in logical['style_spans']],
                     dict(text=extra.replace('再編集', '追加編集') if name == 'second' else extra, style_id='body')])])})
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
        if sid not in updated['slots'] or len(updated['slots']) != 3:
            raise ValueError('generated identity changed')
        if (updated['slots'][sid]['occupancy'] is None) != (name == 'shorten'):
            raise ValueError('continuation did not activate/dormant')
        if updated['contract_sha256'] != initial['contract_sha256']:
            raise ValueError('confirmation changed')
        if updated['continuation_destinations'][destination['destination_id']]['authority']['boundary_id'] != chosen['boundary_id']:
            raise ValueError('boundary authority changed')
        if set(report['plan']['new_slots']) != ({sid} if name == 'overflow' else set()):
            raise ValueError('generated slot was not reused')
        creation = creation or updated['slots'][sid]['creation_binding']
        mutation = creation['mutation']
        if (updated['slots'][sid]['creation_binding'] != creation or mutation['start'] != REVIEWED_BOUNDARY['offset']
                or mutation['end'] != mutation['start'] or 'insertion_order' in mutation):
            raise ValueError('generated creation provenance changed or is not at the reviewed boundary')
        if name == 'shorten' and updated['slots'][sid]['binding']['paragraph']['text']:
            raise ValueError('dormant slot still paints text')
        position = order(out, updated, destination, source_program)
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
            checks = dict(paragraph_style_destination_records_equal=True, slot_identity_allocation_layout_style_equal=True,
                          planned_glyph_fields_equal=list(single.GLYPH_FIELDS), planned_glyphs=sum(map(len, glyphs(report))))
        proof = audit(pdf, out, updated, initial, report, directory / (name + '-audit'), original_text, noop=noop,
                      owned=state.get('generated_fonts', {}))
        nesting = single.operator_nesting(out, single.EDITED)
        if single.pdf_header(out) != source_header:
            raise ValueError('saving changed the PDF version')
        sizes = single.resources(out, updated, destination, source_aliases)
        outcome = report['generated_font_outcome']
        if noop:
            if any(sizes[k] != previous_sizes[k] for k in single.RESOURCE_COUNTS):
                raise ValueError('no-op changed font/resource counts')
            if any(set(v.values()) != {'reused'} for v in outcome.values()):
                raise ValueError('no-op wrote a new font object')
            if updated['generated_fonts'] != state['generated_fonts']:
                raise ValueError('no-op changed generated font records')
        stages.append(dict(stage=name, status='passed', restored=True, slot_id=sid, saves=report['saves'],
            new_slots=sorted(report['plan']['new_slots']),
            generated_lines=len(updated['slots'][sid]['binding']['physical_layout']['lines']), boundary=position,
            **checks, **proof, operator_nesting=nesting, pdf_header=single.pdf_header(out),
            resources=dict(sizes, font_outcome=outcome)))
        single.write(directory / 'stages.json', stages)
        print(name, 'passed', flush=True)
        pdf, state, previous, previous_sizes = out, updated, report, sizes
    refused = refuse(directory, 'capacity', lambda o, j: edit_shared_flow(pdf, state, o, j,
        {pid: dict(edits=[dict(start=0, end=0, text=extra * 2, style_id='body')])}))
    if refused['reason'] != single.CAPACITY_REASON:
        raise ValueError('capacity negative control was refused for an unexpected reason: ' + refused['reason'])
    comparison = compare(directory, page_entry) if page_entry else None
    if engine != {p.name: source_sha(p) for p in sorted((ROOT / 'pdfeditor').glob('*.py'))}:
        raise ValueError('engine changed during evaluation')
    digest = hashlib.sha256(json.dumps(engine, sort_keys=True).encode()).hexdigest()
    if comparison and comparison['page_entry_engine_digest'] != digest:
        raise ValueError('the page-entry run used another engine')
    return dict(schema='pdfengine-boundary-destination-evaluation-1', status='passed', source_url=single.URL,
        source_sha256=single.SOURCE_SHA, reviewed_boundary=REVIEWED_BOUNDARY, inspection=inspection,
        source_replay=replay, source_resources=baseline, source_operator_nesting=source_nesting,
        source_pdf_header=source_header, stages=stages, negative_controls=[refused], destination=destination,
        page_entry_comparison=comparison,
        scope='one external LibreOffice paragraph; the reviewed page-6 area at one reviewed page-level boundary',
        providers=providers, provider_evidence_source=provider_evidence_source,
        environment=dict(engine_digest=digest, engine_sha256=engine,
            runner_sha256=source_sha(Path(__file__)), evaluation_dependencies_sha256={d: source_sha(ROOT / d) for d in DEPENDENCIES},
            python=sys.version.split()[0], platform=platform.platform(), pymupdf=pymupdf.VersionBind, **environment))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--page-entry-run', help='an evaluate.py run of this engine to compare glyphs and pixels with')
    args = parser.parse_args()
    directory = BASE / 'runs' / ('boundary-' + args.run_name)
    directory.mkdir(parents=True)
    single.write(directory / 'summary.json', run(directory, args.page_entry_run))
