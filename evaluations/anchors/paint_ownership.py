"""Observe existing anchored editable writes; no future ownership implementation.

Run with --work (a new ignored directory) and --output (compact public JSON).
Re-edits execute in fresh processes with only current PDF, current sidecar(s),
edit requests and font assets. Previous reports are observer-only evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import platform
import subprocess
import sys

import pymupdf

from pdfeditor.anchors import inspect_anchors
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import operators
from pdfeditor.editable import (bind_document_edit, bind_editable, edit_document,
    open_editable, plan_document_edit, plan_editable, write_editable)
from pdfeditor.elements import inspect_element
from pdfeditor.selection import make_selection
from pdfeditor.transaction import Transaction
from evaluations.continuation.canonical_metrics import program, sha
from evaluations.continuation.source_slot_accumulation import dump, reconcile, runtime_digest
from evaluations.continuation.source_output_probes import render_noop, suffix_witness

ROOT = Path(__file__).resolve().parents[2]
BASE = '0e468ff7fd3abd76612b5de6cffd2444f8135a2f'
TEXT = set('BT ET Tf Tm Td TD T* Tj TJ Tc Tw Tz TL Ts Tr'.split()) | {"'", '"'}
PATH = set('m l c v y h re S s f F f* B B* b b* n'.split())


def census(data):
    """Operator-token spans, NOT owned ranges; whitespace excluded by parser."""
    ops = list(operators(data))
    tally = Counter(o.name for o in ops)
    return dict(bytes=len(data), operators=len(ops),
        text_token_bytes=sum(o.end-o.start for o in ops if o.name in TEXT),
        path_token_bytes=sum(o.end-o.start for o in ops if o.name in PATH),
        path_operators=sum(tally[n] for n in PATH),
        fill_operators=sum(tally[n] for n in ('f', 'F', 'f*')),
        n=tally['n'], q=tally['q'], Q=tally['Q'])


def mutation_measure(old, new, records):
    rows = reconcile(old, new, records)
    groups = defaultdict(lambda: dict(count=0, delta_bytes=0, delta_operators=0))
    bodies = []
    for row in rows:
        value = groups[f"{row['kind']} / {row['owner']}"]
        value['count'] += 1
        value['delta_bytes'] += row['delta']
        value['delta_operators'] += row['operator_delta']
        if row['kind'] == 'decoration':
            data = new[row['output_start']:row['output_start']+row['replacement_length']]
            ops = list(operators(data))
            q = next((o for o in ops if o.name == 'q'), None)
            if q is not None:
                # Measurement of this write's payload, never a persistent certificate.
                body = data[q.start:]
                bodies.append(dict(bytes=len(body), sha256=sha(body),
                    operators=[o.name for o in operators(body)],
                    source_terminal_range=[row['start'], row['end']],
                    output_start=row['output_start']+q.start))
    return dict(count=len(rows), by_kind_owner=dict(groups),
        unchanged_gaps_verified=True, emitted_group_payloads=bodies)


def paint_inventory(states, data):
    paths = {p['source_id']: p for s in states for p in s['element']['paths']}
    anchored = {i for s in states for g in s['anchors']['underlines'] for i in g['source_ids']}
    fixed = {r['source_id'] for s in states for r in s['anchors']['fixed_relations']}

    def tokens(ids):
        spans = set()
        for ident in ids:
            path = paths[ident]['source']
            spans.add(tuple(path['merged_range']))
            spans.update(tuple(r['merged_range']) for r in path['path_source_ranges'])
        return sum(b-a for a, b in spans)

    return dict(current_anchor_path_token_bytes=tokens(anchored),
        fixed_path_token_bytes=tokens(fixed), other_current_path_token_bytes=tokens(set(paths)-anchored-fixed),
        unbound_path_token_bytes=census(data)['path_token_bytes']-tokens(paths),
        note='Unbound includes source nonpainting path/clip operations, not an authorship classification',
        other_paints=[dict(source_id=i, fingerprints=[p['fingerprint'] for p in paths[i]['proof'].get('paints', [])])
                      for i in sorted(set(paths)-anchored-fixed)])


def evidence(pdf, states, *, previous=None, records=None):
    data = program(pdf, 1)
    result = dict(page=census(data), program_sha256=sha(data), paint_inventory=paint_inventory(states, data), bindings=[])
    for state in states:
        p, e, a = state['paragraph'], state['element'], state['anchors']
        paths = {path['source_id']: path for path in e['paths']}
        groups = a['underlines']
        fixed = []
        for relation in a['fixed_relations']:
            path = paths[relation['source_id']]
            fixed.append(dict(source_id=relation['source_id'],
                paint_fingerprints=[paint['fingerprint'] for paint in path['proof'].get('paints', [])]))
        result['bindings'].append(dict(text=p['text'], styles=len(p['styles']),
            paragraph_sha256=p['snapshot_sha256'], element_sha256=e['snapshot_sha256'],
            logical_element=state['logical_element'], previous_model_sha256=state.get('previous_model_sha256'),
            groups=groups, fixed=fixed, relations=state['relations'],
            element_path_count=len(paths), current_anchor_paints=sum(len(g['source_ids']) for g in groups),
            anchor_template_paints=[dict(matrix=paths[s]['proof']['paints'][0]['matrix'],
                bounds=paths[s]['proof']['paints'][0]['bounds'],
                clips=paths[s]['proof']['paints'][0].get('clips', []))
                for g in groups for s in g['source_ids']],
            has_paint_output_field=any(key in state for key in ('paint_output', 'paint_outputs')),
            sidecar_bytes=len(json.dumps(state, separators=(',', ':')).encode())))
    if previous:
        old = program(previous, 1)
        result['delta'] = {k: result['page'][k]-v for k, v in census(old).items()}
        result['mutation'] = mutation_measure(old, data, records)
        result['other_page_program_equal'] = program(previous, 2) == program(pdf, 2)
    return result


def summarize_geometry(result, root):
    """Read current snapshots, including runs produced before this extra metric.

    This is observation only: bounds never decide provenance or edit authority.
    """
    for name, case in result['cases'].items():
        for stage, measured in case.items():
            if 'mutation' not in measured:
                continue
            raw = json.loads((root/name/f'{stage}.json').read_text(encoding='utf-8'))
            states = raw if isinstance(raw, list) else [raw]
            measured['paint_inventory'] = paint_inventory(states, program(root/name/f'{stage}.pdf', 1))
            if name != 'two':
                before = suffix_witness(root/name/'source.pdf')
                after = suffix_witness(root/name/f'{stage}.pdf')
                for witness in (before, after):
                    for clip in witness['state']['clip']:
                        clip.pop('at', None)
                measured['source_KEEP_state_and_glyphs_equal'] = before == after
                if before != after:
                    raise ValueError(f'{name}/{stage}: source suffix state/glyphs changed')
            for state, binding in zip(states, measured['bindings']):
                binding.pop('has_authorship_record', None)
                binding['has_paint_output_field'] = any(key in state for key in ('paint_output', 'paint_outputs'))
                paths = {p['source_id']: p for p in state['element']['paths']}
                for witness, source_id in zip(binding['anchor_template_paints'],
                        (s for g in binding['groups'] for s in g['source_ids'])):
                    paint = paths[source_id]['proof']['paints'][0]
                    witness.update({key: paint[key] for key in ('bounds', 'colorspace', 'components', 'opacity', 'even_odd')})
    return result


def write_summary(path, result):
    """Compact repeated binding/payload rows while retaining readable stage totals."""
    result = deepcopy(result)
    tables = {}

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('bindings', 'emitted_group_payloads') and isinstance(child, list):
                    token = f'__row_table_{len(tables)}__'
                    tables[token] = '[\n' + ',\n'.join('          '+json.dumps(row, separators=(',', ':'))
                                                      for row in child) + '\n        ]'
                    value[key] = token
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    for token, table in tables.items():
        rendered = rendered.replace(json.dumps(token), table)
    path.write_text(rendered+'\n', encoding='utf-8', newline='\n')


def fixture(root, variant):
    # Reuse the established synthetic PDF/font machinery.
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_attributed import source_pdf
    bg = b'0.9 g 15 140 150 80 re f 0 g '
    first = b'BT /Regular 12 Tf 20 200 Td (ONE TWO ) Tj ET '
    if variant == 'styles':
        first = b'BT /Regular 12 Tf 20 200 Td (ONE ) Tj /Bold 12 Tf (TWO ) Tj ET '
    unrelated = (b'% pdfengine-paint-v1 begin foreign-example\n250 130 10 10 re f\n'
                 b'% pdfengine-paint-v1 end foreign-example\n') if variant == 'unrelated' else b''
    data = bg + first + unrelated + b'20 198 50.4 .7 re f '
    data += (b'0 0 1 rg BT /Bold 12 Tf 20 184 Td (THREE FOUR) Tj ET 20 182 72 .9 re f 0 g '
             if variant == 'paint_styles' else
             b'BT /Regular 12 Tf 20 184 Td (THREE FOUR) Tj ET 20 182 72 .7 re f ')
    data += b'BT /Regular 12 Tf 220 200 Td (KEEP) Tj ET'
    if variant == 'q':
        data = b'q 2 w [3 2] 1 d 1 J 2 j 8 M 0.3 G ' + data + b' Q'
    if variant == 'ctm':
        data = b'q 0.83 0 0 0.91 7.25 11.5 cm ' + data + b' Q'
    if variant == 'clip':
        data = b'q 10 120 300 130 re W n ' + data + b' Q'
    if variant == 'two':
        data = (b'BT /Regular 12 Tf 20 200 Td (ONE TWO) Tj ET 20 198 50.4 .7 re f '
                b'BT /Regular 12 Tf 200 200 Td (NEXT ONE) Tj ET 200 198 57.6 .7 re f')
    source = source_pdf(root, data)
    font = root / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    selections = [list(range(7)), list(range(7, 15))] if variant == 'two' else [list(range(18))]
    specs = []
    for ids in selections:
        selection = make_selection(source, glyph_ids=ids, explicit_width=80)
        p = inspect_paragraph(source, selection)
        e = inspect_element(source, selection)
        candidates = inspect_anchors(source, p, e)
        if variant in ('multiple', 'delete_group'):
            groups = [dict(source_ids=[c['source_id']], range=c['range']) for c in candidates['candidates']]
        else:
            groups = [dict(source_ids=g['source_ids'], range=g['range']) for g in candidates['suggested_groups']]
        if not groups:
            raise ValueError('synthetic fixture has no existing supported anchor')
        fixed = [] if variant == 'two' else [dict(source_id=e['paths'][0]['source_id'],
            relation='backgrounds', behavior='fixed-to-page')]
        a = dict(paragraph_sha256=p['snapshot_sha256'], element_sha256=e['snapshot_sha256'],
                 underlines=groups, fixed_relations=fixed)
        fonts = {s['id']: {'path': str(font)} for s in p['styles']}
        specs.append((p, dict(fonts=fonts, element_snapshot=e, anchor_spec=a, max_bottom=115)))
    return source, specs


def first(root, variant):
    source, specs = fixture(root, variant)
    pdf, model = root/'first.pdf', root/'first.json'
    initial = dict(page=census(program(source, 1)), bindings=[dict(text=p['text'],
        styles=len(p['styles']), anchors=opts['anchor_spec'],
        element_sha256=opts['element_snapshot']['snapshot_sha256']) for p, opts in specs])
    if len(specs) == 1:
        edits = [dict(start=4, end=7, text='FIVE SEVEN')] if variant == 'lifecycle' else []
        p, opts = specs[0]
        report = write_editable(source, pdf, model, p, edits, **opts)
        states = [json.loads(model.read_text(encoding='utf-8'))]
        records = report['mutation_map']
    else:
        with Transaction(source) as tx:
            plans = [plan_editable(tx.page(1), p, [], owner=f'paragraph-{i}', **opts)
                     for i, (p, opts) in enumerate(specs)]
            result = tx.commit(pdf)
            try:
                pairs = [bind_editable(result.identity(1), plan, result, **opts)
                         for plan, (_, opts) in zip(plans, specs)]
                states = [pair[0] for pair in pairs]
                records = [r for _, report in pairs for r in report['mutation_map']]
            finally:
                result.close()
        dump(model, states)
    return pdf, model, states, dict(initial=initial, first=evidence(pdf, states, previous=source, records=records))


def worker(files):
    source, model, output, saved, request, report_path = map(Path, files)
    changes = json.loads(request.read_text(encoding='utf-8'))
    raw = json.loads(model.read_text(encoding='utf-8'))
    states = raw if isinstance(raw, list) else [raw]
    for state in states:
        restored = open_editable(source, state)
        if restored['status'] != 'restored':
            raise ValueError(restored)
    try:
        if isinstance(raw, list):
            with Transaction(source) as tx:
                plans = [plan_document_edit(tx.page(1), state, changes, owner=f'paragraph-{i}')
                         for i, state in enumerate(states)]
                result = tx.commit(output)
                try:
                    pairs = [bind_document_edit(result.identity(1), plan, result) for plan in plans]
                    dump(saved, [state for state, _ in pairs])
                    report = dict(mutation_map=[m for _, r in pairs for m in r['mutation_map']])
                finally:
                    result.close()
        else:
            report = edit_document(source, model, output, saved, changes)
        dump(report_path, dict(status='saved', report=report))
    except PdfError as exc:
        dump(report_path, dict(status='refused', reason=str(exc),
            pdf_published=output.exists(), sidecar_published=saved.exists()))


def advance(root, pdf, model, name, edits, *, expect_refusal=False):
    out, saved, request, report_path = [root/f'{name}{suffix}' for suffix in
                                      ('.pdf', '.json', '-request.json', '-report.json')]
    dump(request, edits)
    run = subprocess.run([sys.executable, '-m', 'evaluations.anchors.paint_ownership', '--worker',
        *map(str, (pdf, model, out, saved, request, report_path))], cwd=ROOT, capture_output=True, text=True)
    if run.returncode:
        raise RuntimeError(run.stderr)
    response = json.loads(report_path.read_text(encoding='utf-8'))
    if response['status'] != 'saved':
        if not expect_refusal:
            raise ValueError(f'{root.name}/{name}: unexpected refusal: {response}')
        return pdf, model, response
    raw = json.loads(saved.read_text(encoding='utf-8'))
    result = evidence(out, raw if isinstance(raw, list) else [raw], previous=pdf,
                      records=response['report']['mutation_map'])
    result.update(status='saved', fresh_process=True, provenance_inputs='current PDF + current sidecar(s) + fonts')
    if not edits:
        result['render'] = render_noop(pdf, out, root/f'{name}-render')
        if not result['render']['passed']:
            raise ValueError(f'{root.name}/{name}: no-op renderer mismatch')
    print(f'{root.name}/{name}: {result["delta"]}', flush=True)
    return out, saved, result


def evaluate(root):
    result = dict(base=BASE, runtime_sha256=runtime_digest(), python=platform.python_version(),
        pymupdf=pymupdf.VersionBind, method='Current runtime only; no paint ownership parser or writer', cases={})
    for variant in ('lifecycle', 'multiple', 'styles', 'two', 'unrelated', 'q', 'ctm', 'clip', 'delete_group', 'paint_styles'):
        folder = root/variant
        folder.mkdir(parents=True)
        pdf, model, states, case = first(folder, variant)
        result['cases'][variant] = case
        for name in (('noop1', 'noop2', 'noop3') if variant in ('lifecycle', 'ctm') else ('noop1',)):
            pdf, model, case[name] = advance(folder, pdf, model, name, [])
        if variant == 'lifecycle':
            pdf, model, case['change'] = advance(folder, pdf, model, 'change', [dict(start=4, end=9, text='')])
            pdf, model, case['change_noop'] = advance(folder, pdf, model, 'change_noop', [])
            state = json.loads(model.read_text(encoding='utf-8'))
            pdf, model, case['empty'] = advance(folder, pdf, model, 'empty',
                [dict(start=0, end=len(state['paragraph']['text']), text='', style_id=state['paragraph']['styles'][0]['id'])],
                expect_refusal=True)
            if case['empty'].get('status') != 'refused':
                raise ValueError('empty anchor refusal changed; revise observation')
            case['empty_noop'] = case['regrow'] = dict(status='unreachable', reason='empty revision was not published')
        if variant == 'delete_group':
            state = json.loads(model.read_text(encoding='utf-8'))
            a, b = state['anchors']['underlines'][0]['range']
            pdf, model, case['delete'] = advance(folder, pdf, model, 'delete', [dict(start=a, end=b, text='')])
            pdf, model, case['delete_noop'] = advance(folder, pdf, model, 'delete_noop', [])
            pdf, model, case['insert_again'] = advance(folder, pdf, model, 'insert_again', [dict(start=0, end=0, text='ONE ')])
        dump(root/'observation.json', result)
    if result['runtime_sha256'] != runtime_digest():
        raise ValueError('runtime changed during observation')
    return summarize_geometry(result, root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', nargs=6)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    else:
        if args.work is None or args.output is None:
            parser.error('--work and --output are required unless --worker is used')
        if args.work.exists():
            raise ValueError('--work must be a new directory')
        args.work.mkdir(parents=True)
        write_summary(args.output, evaluate(args.work.resolve()))


if __name__ == '__main__':
    main()
