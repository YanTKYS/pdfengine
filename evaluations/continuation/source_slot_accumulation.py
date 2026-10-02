"""Read current source-slot mutation evidence; never implement a rewrite contract.

Run: python -m evaluations.continuation.source_slot_accumulation --output SUMMARY
Raw synthetic PDFs/reports stay in --work (an ignored, new directory). Every
re-edit runs in a fresh process with only the current PDF/sidecar as provenance
inputs. Font provider files are ordinary required editing assets, not history.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

import pymupdf
import pypdf

from pdfeditor.content_stream import operators
from pdfeditor.editable import edit_document, open_editable, write_editable
from pdfeditor.logical_element import paragraph_from_snapshot
from pdfeditor.operator_nesting import audit
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from evaluations.continuation.canonical_metrics import program, sha

ROOT = Path(__file__).resolve().parents[2]
BASE = '8c4e904d1e7d51e4ba1d8c7e693eaad816059f0f'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def dump_summary(path, value):
    """Readable stage totals, compact per-mutation/event rows (no raw dumps)."""
    value = deepcopy(value)
    tables = {}

    def visit(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in ('mutations', 'events') and isinstance(child, list):
                    token = f'__compact_table_{len(tables)}__'
                    tables[token] = '[\n' + ',\n'.join('            ' + json.dumps(row, separators=(',', ':'))
                                                     for row in child) + '\n          ]'
                    item[key] = token
                else:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    for token, table in tables.items():
        rendered = rendered.replace(json.dumps(token), table)
    path.write_text(rendered + '\n', encoding='utf-8', newline='\n')


def counts(data):
    """Nonempty strings mean painting only for these verified fill-only fixtures."""
    ops = list(operators(data))
    tally = Counter(op.name for op in ops)

    def strings(value):
        if isinstance(value, bytes):
            return bool(value)
        return isinstance(value, (list, tuple)) and any(strings(v) for v in value)

    arrays = [op.args[0] for op in ops if op.name == 'TJ']
    return dict(bytes=len(data), operators=len(ops), Tf=tally['Tf'], Tm=tally['Tm'],
                Tj=tally['Tj'], TJ=tally['TJ'],
                painting_show=sum(op.name in ('Tj', 'TJ') and strings(op.args) for op in ops),
                nonpainting_numeric_TJ=sum(bool(a) and all(isinstance(v, (int, float)) for v in a)
                                          for a in arrays), empty_TJ=sum(not a for a in arrays))


def paths_for_key(value, wanted, prefix=''):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f'{prefix}/{key}'
            if key == wanted:
                found.append(path)
            found.extend(paths_for_key(child, wanted, path))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            found.extend(paths_for_key(child, wanted, f'{prefix}/{i}'))
    return found


def inventory(state):
    return {key: paths_for_key(state, key) for key in (
        'mutation_map', 'byte_edits', 'creation_binding', 'previous_model_sha256',
        'generated_fonts', 'insertion_binding', 'style_slot_bindings', 'binding_provenance',
        'anchors', 'relations', 'owned_paints')}


def bindings(pdf, state, kind):
    slots = state['slots'] if kind == 'shared' else {'editable': {'binding': state}}
    result = {}
    for sid, slot in slots.items():
        snapshot = slot['binding']['paragraph']
        paragraph = paragraph_from_snapshot(pdf, snapshot)
        try:
            selected = set(snapshot['selection']['glyph_ids'])
            events = []
            for event in paragraph.events:
                events.append(dict(start=event.operator.start, end=event.operator.end,
                    operator=event.operator.name,
                    selected_glyphs=sum(len(set(c.source_orders) & selected) for c in event.chars),
                    foreign_glyphs=sum(len(set(c.source_orders) - selected) for c in event.chars)))
            result[sid] = dict(page=snapshot['selection']['page'], text=snapshot['text'],
                paragraph_id=slot.get('paragraph_id', slot['binding']['logical_element']['id']),
                selected_glyphs=len(selected), events=events,
                font_aliases=sorted({e.state.font.name for e in paragraph.events}),
                typing_style_id=snapshot.get('typing_style_id'),
                insertion_binding=bool(snapshot.get('insertion_binding')),
                style_slot_bindings=sorted(snapshot.get('style_slot_bindings', {})))
        finally:
            paragraph.close()
    return result


def reconcile(old, new, records):
    """Prove every untouched gap byte-for-byte; record deltas without claiming ownership."""
    rows, shift, cursor = [], 0, 0
    for record in sorted(records, key=lambda m: (m['start'], m['end'])):
        start, end, length = record['start'], record['end'], record['length']
        if start < cursor or old[cursor:start] != new[cursor + shift:start + shift]:
            raise ValueError('mutation overlap or an unreported gap changed')
        before = old[start:end]
        after = new[start + shift:start + shift + length]
        a, b = counts(before), counts(after)
        row = dict(kind=record['kind'], owner=record['owner'], start=start, end=end,
            output_start=start + shift, replacement_length=length, consumed_length=end-start,
            delta=length-(end-start), operator_delta=b['operators']-a['operators'],
            glyph_anchor_count=sum(k.startswith('glyph:') for k in record.get('anchors', {})))
        # Keep one short exact example, not large raw command dumps.
        if not rows:
            row.update(consumed=a, replacement=b, consumed_sha256=sha(before), replacement_sha256=sha(after))
            row['consumed_example'] = before.decode('ascii')[:160]
            row['replacement_prefix'] = after.decode('ascii')[:180]
        rows.append(row)
        shift += row['delta']
        cursor = end
    if old[cursor:] != new[cursor + shift:] or len(old) + shift != len(new):
        raise ValueError('unreported suffix change or byte delta mismatch')
    if sum(r['operator_delta'] for r in rows) != counts(new)['operators'] - counts(old)['operators']:
        raise ValueError('operator deltas do not reconcile')
    return rows


def stage(pdf, state, kind, *, previous=None, report=None):
    bound = bindings(pdf, state, kind)
    pages = {}
    for page in range(1, len(pypdf.PdfReader(pdf).pages) + 1):
        data = program(pdf, page)
        nesting = audit(data)
        if nesting['violations']:
            raise ValueError(nesting)
        value = dict(counts(data), sha256=sha(data), nesting_violations=0)
        if previous is not None:
            old = program(previous, page)
            records = (report['mutation_map'].get(str(page), {}).get('mutations', [])
                       if kind == 'shared' else report['mutation_map'] if page == 1 else [])
            rows = reconcile(old, data, records)
            owners = defaultdict(lambda: dict(mutations=0, delta_bytes=0, delta_operators=0))
            for row in rows:
                owner = str(row['owner'])
                owners[owner]['mutations'] += 1
                owners[owner]['delta_bytes'] += row['delta']
                owners[owner]['delta_operators'] += row['operator_delta']
            for sid, binding in bound.items():
                if binding['page'] != page or not binding['selected_glyphs']:
                    continue
                emitted = set()
                shift = 0
                for record in sorted(records, key=lambda m: (m['start'], m['end'])):
                    if kind != 'shared' or record['owner'] == sid:
                        emitted.update(record['start'] + shift + offset
                                       for name, offset in record.get('anchors', {}).items()
                                       if name.startswith('glyph:'))
                    shift += record['length'] - (record['end'] - record['start'])
                binding['all_current_events_at_emitted_glyph_anchors'] = all(
                    e['start'] in emitted for e in binding['events'])
                if not binding['all_current_events_at_emitted_glyph_anchors']:
                    raise ValueError('a selected event was not emitted by the current writer')
            value.update(delta_bytes=len(data)-len(old), delta_operators=value['operators']-counts(old)['operators'],
                         mutations=rows, by_owner=dict(owners), untouched_gaps_identical=True)
        pages[str(page)] = value
    return dict(pages=pages, slots=bound, sidecar_evidence=inventory(state))


def worker(args):
    # This process has no prior state object and takes no previous report/PDF.
    source, model, out, saved, changes, report_file = map(Path, args.files)
    change = json.loads(changes.read_text(encoding='utf-8'))
    opener = open_shared_flow if args.kind == 'shared' else open_editable
    restored = opener(source, model)
    if restored['status'] != 'restored':
        raise ValueError(restored)
    if paths_for_key(restored['state'], 'mutation_map'):
        raise ValueError('fixture unexpectedly persists mutation history')
    writer = edit_shared_flow if args.kind == 'shared' else edit_document
    report = writer(source, model, out, saved, change)
    dump(report_file, report)


def advance(root, kind, source, model, changes, name):
    out, saved, request, report_file = [root / f'{name}{suffix}' for suffix in
                                      ('.pdf', '.json', '-changes.json', '-report.json')]
    dump(request, changes)
    completed = subprocess.run([sys.executable, '-m', 'evaluations.continuation.source_slot_accumulation',
                    '--worker', '--kind', kind, str(source), str(model), str(out), str(saved),
                    str(request), str(report_file)], cwd=ROOT, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr)
    state = json.loads(saved.read_text(encoding='utf-8'))
    report = json.loads(report_file.read_text(encoding='utf-8'))
    measured = stage(out, state, kind, previous=source, report=report)
    measured['reopened_in_fresh_process_without_previous_report'] = True
    measured['reused_code_glyph_count'] = (sum(s['report']['reused_code_glyph_count'] for s in report['steps'])
                                         if kind == 'shared' else report['reused_code_glyph_count'])
    if not changes:
        with pymupdf.open(source) as before, pymupdf.open(out) as after:
            measured['noop_pixels_equal'] = all(a.get_pixmap().samples == b.get_pixmap().samples
                                                for a, b in zip(before, after))
        if not measured['noop_pixels_equal']:
            raise ValueError('synthetic noop pixels changed')
    print(f'{root.name}/{name}: ' + ', '.join(f'p{p} {v["delta_bytes"]:+} bytes'
                                           for p, v in measured['pages'].items()), flush=True)
    return out, saved, state, measured


def evaluate(work):
    # Reuse repository fixtures rather than introduce a second PDF generator.
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_shared_flow import prepared, replace, LONG, SHORT
    from test_attributed import source_pdf
    from test_editable import first as anchored_first
    from pdfeditor.attributed import inspect_paragraph
    from pdfeditor.selection import make_selection

    shared = work / 'shared'
    shared.mkdir()
    source, state = prepared(shared)
    model = shared / 'initial.json'
    dump(model, state)
    cases = {'shared': {'fixture': 'tests/test_shared_flow.py::prepared',
                        'stages': {'initial': stage(source, state, 'shared')}}}
    for name, runs in [('grow', LONG), ('noop1', None), ('noop2', None), ('noop3', None),
                       ('second', SHORT), ('second_noop', None), ('empty', []),
                       ('empty_noop', None), ('regrow', LONG)]:
        changes = {} if runs is None else {'A': replace(state, 'A', runs)}
        source, model, state, measured = advance(shared, 'shared', source, model, changes, name)
        cases['shared']['stages'][name] = measured

    retained = work / 'retained'
    retained.mkdir()
    # Partial selection inside a show, later selected text in another BT, and unrelated suffix.
    source = source_pdf(retained, b'BT /Regular 12 Tf 20 200 Td (PRE ABC POST) Tj ET '
                        b'BT /Regular 12 Tf 20 175 Td (DEF) Tj ET '
                        b'BT /Regular 12 Tf 220 200 Td (KEEP) Tj ET')
    snapshot = inspect_paragraph(source, make_selection(source, glyph_ids=[4, 5, 6, 12, 13, 14], explicit_width=100))
    out, model = retained / 'first.pdf', retained / 'first.json'
    # All characters retained: source-font codes still get emitted in NEW operators.
    report = write_editable(source, out, model, snapshot, [], x=20, baseline=110, max_bottom=180)
    state = json.loads(model.read_text(encoding='utf-8'))
    cases['retained_partial_multievent'] = dict(fixture='tests/test_attributed.py::source_pdf', stages={
        'initial': stage(source, {'paragraph': snapshot, 'logical_element': {'id': 'probe'}}, 'editable'),
        'first': stage(out, state, 'editable', previous=source, report=report)})
    cases['retained_partial_multievent']['stages']['first']['reused_code_glyph_count'] = report['reused_code_glyph_count']
    source = out
    source, model, state, measured = advance(retained, 'editable', source, model, [], 'noop')
    cases['retained_partial_multievent']['stages']['noop'] = measured

    anchored = work / 'anchored'
    anchored.mkdir()
    source, out, model, report = anchored_first(anchored)
    state = json.loads(model.read_text(encoding='utf-8'))
    cases['anchored'] = dict(fixture='tests/test_editable.py::first (test_anchors.prepared)', stages={
        'first': stage(out, state, 'editable', previous=source, report=report)})
    for name in ('noop1', 'noop2'):
        out, model, state, measured = advance(anchored, 'editable', out, model, [], name)
        cases['anchored']['stages'][name] = measured
    return cases


def runtime_digest():
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'pdfeditor').glob('*.py')):
        digest.update(path.name.encode() + b'\0' + path.read_bytes())
    return digest.hexdigest()


def owner_censuses(result, work):
    """Count consumed/emitted commands per owner, not an inferred owned envelope."""
    directories = {'shared': 'shared', 'retained_partial_multievent': 'retained', 'anchored': 'anchored'}
    for case, content in result['cases'].items():
        folder = work / directories[case]
        before = folder / 'source.pdf'
        for name, measured in content['stages'].items():
            after = folder / ('source.pdf' if name == 'initial' else f'{name}.pdf')
            for page, value in measured['pages'].items():
                if 'mutations' not in value:
                    continue
                old, new = program(before, int(page)), program(after, int(page))
                for owner in value['by_owner'].values():
                    owner.update(consumed_counts={k: 0 for k in counts(b'')},
                                 replacement_counts={k: 0 for k in counts(b'')})
                for row in value['mutations']:
                    owner = value['by_owner'][str(row['owner'])]
                    for key, data in [('consumed_counts', old[row['start']:row['end']]),
                                      ('replacement_counts', new[row['output_start']:
                                                                 row['output_start'] + row['replacement_length']])]:
                        for metric, number in counts(data).items():
                            owner[key][metric] += number
            before = after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--kind', choices=['shared', 'editable'])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--work', type=Path)
    parser.add_argument('files', nargs='*')
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if args.output is None:
        parser.error('--output is required')
    if args.work is None:
        scratch = ROOT / 'tmp'
        scratch.mkdir(exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix='source-slot-', dir=scratch))
    else:
        work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    before = runtime_digest()
    cases = evaluate(work)
    if runtime_digest() != before:
        raise ValueError('runtime changed during evaluation')
    result = dict(schema_version=1, base_head=BASE,
        environment=dict(system=platform.system(), python=platform.python_version(),
                         pymupdf=pymupdf.VersionBind, pypdf=pypdf.__version__),
        runtime_digest=dict(algorithm='SHA256(sorted filename + NUL + file bytes, pdfeditor/*.py)', value=before),
        scope='synthetic current-runtime observation; no candidate implementation or real-PDF rerun',
        attribution='explicit mutation owner and byte-exact untouched gaps; no absolute source-owned span inferred',
        painting_count_limit='nonempty Tj/TJ string operand in these fill-only fixtures; not general visibility',
        reopen_inputs='current PDF + current sidecar + configured font assets; no previous report or revision input',
        cases=cases)
    owner_censuses(result, work)
    dump_summary(args.output, result)
    print(f'Summary: {args.output}', flush=True)


if __name__ == '__main__':
    main()
