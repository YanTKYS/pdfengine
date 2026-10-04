"""B1-L evidence: unmodified runtime observations and separate candidate trials.

Raw PDFs, fonts and per-stage reports stay in --work (ignored). The summary
retains comparisons and named counterexamples, not a runtime certificate.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

import pymupdf

from pdfeditor.attributed import apply_edits, inspect_paragraph
from pdfeditor.content_stream import multiply
from pdfeditor.editable import edit_document, write_editable
from pdfeditor.logical_element import paragraph_from_snapshot
from pdfeditor.paragraph import ParagraphShaper
from pdfeditor.replay import glyph_observations
from pdfeditor.rich_layout import layout_attributed
from pdfeditor.selection import make_selection
from pdfeditor.shaped_font import ShapedFont
from pdfeditor.style_confirmation import confirm_paragraph
from evaluations.anchors.layout_authority import cell, edit, plan, semantic_style
from evaluations.anchors.paint_ownership import fixture, ROOT
from evaluations.continuation.source_slot_accumulation import dump, runtime_digest

BASE = 'b97c6b8dbba33176f25554ae35618a2ae2568941'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def observe(source, snapshot, edits, fonts, report, *, confirmation=None):
    """Capture actual shaping inputs, including the branch for each final line.

    Fixture font meaning is witnessed by known construction: unembedded
    Courier or supplied asset SHA. Reopened supplied fonts additionally carry
    their physical name; this is NOT a general subset equivalence validator.
    """
    p = paragraph_from_snapshot(source, snapshot)
    units = apply_edits(p, snapshot, edits)
    p = confirm_paragraph(p, confirmation)
    shaper = ParagraphShaper(p, units, fonts)
    rows = []
    try:
        for line in report['lines']:
            shaped = shaper.shape(line['start'], line['end'])
            offset = line['start']
            for g in shaped:
                unit = units[offset]
                original = unit.retained
                style = p.styles[unit.style_id]
                payload = g.payload
                provider = payload['provider']
                r = next(r for r in report['glyph_plan'] if r['start'] == offset)
                if g.advance != r['advance']:
                    raise ValueError('observer did not reproduce the actual planned advance')
                font_name = style.font_name
                if provider != 'original':
                    font = shaper.font(provider)
                    font_identity = 'asset:' + font.source_sha256
                elif font_name == 'Courier':
                    font_identity = 'fixture:Courier/WinAnsi/600'
                else:
                    # Current sidecar's supplied asset recipe, not an old PDF.
                    spec = fonts.get(unit.style_id)
                    font_identity = ('asset:' + hashlib.sha256(Path(spec['path']).read_bytes()).hexdigest()
                                     if spec else 'UNPROVEN:' + font_name)
                row = dict(start=offset, end=offset + len(g.text), text=g.text,
                    glyph_id=r['glyph_id'], code=r['code'], provider=provider,
                    semantic_font=font_identity,
                    font_identity_proof='controlled fixture lineage; general subset verification unresolved',
                    semantic_style=semantic_style(style.export(), font_identity),
                    physical_style_id=r['style_id'], physical_font_name=font_name,
                    source_index=None, source_offset=None, source_line=None,
                    following_provider=None, same_source_line=None, source_contiguous=None,
                    paint_advance=None, width_1000em=None, shaped_metric=None,
                    Tc=None, Tw=None, Tz=None, Tf=None,
                    advance=r['advance'], origin=r['origin'], baseline=line['baseline'],
                    line_end=offset + len(g.text) == line['end'],
                    ink=list(g.ink.tuple()) if g.ink else None,
                    ascent=g.ascent, descent=g.descent, bounds_source=r['bounds_source'])
                row['semantic_style']['word_spacing_intent'] = (
                    'unconfirmed-source-adjustment' if style.event.state.tw else 'none-observed')
                if original is not None and original.char is not None:
                    s = original.event.state
                    row.update(source_index=original.source_index,
                        source_offset=shaper.original_offsets[id(original)], source_line=original.line,
                        paint_advance=original.char.advance, width_1000em=original.char.pdf_width,
                        Tc=s.tc, Tw=s.tw, Tz=s.tz, Tf=s.size,
                        source_matrix=list(multiply(original.event.text_matrix, s.ctm)))
                    row['authority'] = 'metric-minus-Tc' if row['line_end'] else 'metric'
                    if not row['line_end']:
                        following = units[offset + 1].retained
                        row['following_provider'] = 'original' if following and following.char else 'new'
                        if following and following.char:
                            row['same_source_line'] = following.line == original.line
                            row['source_contiguous'] = (shaper.original_offsets[id(following)] ==
                                                        shaper.original_offsets[id(original)] + 1)
                            row['trace_pair'] = [original.observation['origin'][0], following.observation['origin'][0]]
                            if row['same_source_line'] and row['source_contiguous']:
                                row['authority'] = 'trace-difference'
                else:
                    sg = payload['shaped_glyph']
                    row.update(authority='shaped', shaped_metric=dict(advance=sg.advance,
                        x_offset=sg.x_offset, y_offset=sg.y_offset, upem=font.upem,
                        nominal=font.nominal_width(sg.gid)), width_1000em=r['nominal_pdf_width'])
                rows.append(row)
                offset += len(g.text)
        return dict(text=report['after'], glyphs=rows,
            lines=[{k: l[k] for k in ('start', 'end', 'x', 'baseline', 'width', 'ascent', 'descent')}
                   for l in report['lines']],
            omitted_logical_offsets=[i for i in range(len(report['after'])) if not any(g['start'] <= i < g['end'] for g in rows)])
    finally:
        shaper.close()
        p.close()


def compare(a, b):
    ga = {g['start']: g for g in a['glyphs']}
    gb = {g['start']: g for g in b['glyphs']}
    keys = sorted(ga.keys() & gb.keys())
    geometry = lambda s: [[g['start'], g['end'], g['origin'], g['advance']] for g in s['glyphs']]
    semantic = lambda s: [[g['start'], g['end'], g['semantic_style']] for g in s['glyphs']]
    return dict(geometry_exact=geometry(a) == geometry(b),
        allocation_exact=[[l['start'], l['end']] for l in a['lines']] == [[l['start'], l['end']] for l in b['lines']],
        baselines_exact=[l['baseline'] for l in a['lines']] == [l['baseline'] for l in b['lines']],
        semantic_style_exact=semantic(a) == semantic(b),
        max_origin_delta=max((abs(x-y) for k in keys for x, y in zip(ga[k]['origin'], gb[k]['origin'])), default=0),
        max_advance_delta=max((abs(ga[k]['advance']-gb[k]['advance']) for k in keys), default=0),
        changed=[dict(offset=k, before_authority=ga[k]['authority'], after_authority=gb[k]['authority'],
                      before_advance=ga[k]['advance'], after_advance=gb[k]['advance'])
                 for k in keys if ga[k]['advance'] != gb[k]['advance']])


def accuracy(pdf, report):
    ids = report['glyph_plan']
    # Use writer-established current sidecar identities, never nearest-neighbor matching.
    state = json.loads(pdf.with_suffix('.json').read_text(encoding='utf-8'))
    with pymupdf.open(pdf) as doc:
        obs = glyph_observations(doc[0])
    delta = max((abs(x-y) for g in ids
                 for x, y in zip(g['origin'], obs[state['paragraph']['logical']['units'][g['start']]['glyph_id']]['origin'])), default=0)
    return dict(plan_to_saved_trace_max_origin_delta=delta, within_0_002=delta <= .002)


def worker(args):
    source, model, output, saved, request, response = map(Path, args)
    request = json.loads(request.read_text(encoding='utf-8'))
    report = edit_document(source, model, output, saved, request['edits'], **request.get('options', {}))
    dump(response, report)


def reedit(folder, pdf, model, name, edits, overrides=None):
    paths = [folder / (name + s) for s in ('.pdf', '.json', '-request.json', '-report.json')]
    out, saved, request, response = paths
    dump(request, dict(edits=edits, options=overrides or {}))
    run = subprocess.run([sys.executable, '-m', 'evaluations.anchors.layout_observation', '--worker',
        *map(str, (pdf, model, *paths))], cwd=ROOT, text=True, capture_output=True)
    if run.returncode:
        raise RuntimeError(run.stderr)
    return out, saved, json.loads(response.read_text(encoding='utf-8'))


def lifecycle(root, variant):
    folder = root / variant
    folder.mkdir()
    source, specs = fixture(folder, 'lifecycle' if variant == 'identity' else 'ctm')
    snapshot, options = specs[0]
    edits = [dict(start=4, end=7, text='FIVE SEVEN')]
    pdf, model = folder / 'first.pdf', folder / 'first.json'
    report = write_editable(source, pdf, model, snapshot, edits, **options)
    stages = {'first': observe(source, snapshot, edits, options['fonts'], report)}
    accuracies = {'first': accuracy(pdf, report)}
    dump(folder / 'first-report.json', report)
    previous, comparisons = 'first', {}
    # Change is a deletion (L2); growth inserts inside the surviving active range.
    sequence = [('noop1', []), ('noop2', []), ('noop3', []),
        ('change', [dict(start=4, end=9, text='')]),
        ('change_noop1', []), ('change_noop2', []), ('change_noop3', []),
        ('growth', [dict(start=5, end=5, text=' EXTRA')]), ('growth_noop', []),
        ('reflow', []), ('reflow_noop', [])]
    ranges = {}
    for name, edits in sequence:
        state = json.loads(model.read_text(encoding='utf-8'))
        out, saved, report = reedit(folder, pdf, model, name, edits, {'width': 65} if name == 'reflow' else None)
        stages[name] = observe(pdf, state['paragraph'], edits, state['fonts'], report)
        accuracies[name] = accuracy(out, report)
        ranges[name] = [g['output_range'] for g in report['anchors']['groups']]
        if not edits and name != 'reflow':
            comparisons[previous + '->' + name] = compare(stages[previous], stages[name])
        pdf, model, previous = out, saved, name
        print(variant, name, flush=True)
    dump(folder / 'physical-observations.json', stages)
    witnesses = {name: {str(k): next((g for g in stages[name]['glyphs'] if g['start'] == k), None)
                       for k in (3, 14, 16)} for name in ('first', 'noop1', 'change', 'change_noop1')}
    return dict(comparisons=comparisons, accuracy=accuracies, witnesses=witnesses, active_ranges=ranges,
        stages={name: dict(text=s['text'], physical_sha256=digest(s),
            providers=sorted({g['provider'] for g in s['glyphs']}), lines=s['lines'],
            omitted_logical_offsets=s['omitted_logical_offsets']) for name, s in stages.items()})


def source_case(root, name, state=b'', text=b'(A B C ) Tj', *, scaled=False):
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_attributed import source_pdf
    folder = root / name
    folder.mkdir()
    program = b'BT /Regular 12 Tf ' + state + b' 20 200 Td ' + text + b' ET'
    if scaled:
        program = b'q .83 0 0 .91 7.25 11.5 cm ' + program + b' Q'
    source = source_pdf(folder, program)
    with pymupdf.open(source) as doc:
        count = len(glyph_observations(doc[0]))
    snapshot = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(count)), explicit_width=100))
    font = folder / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    fonts = {s['id']: dict(path=str(font)) for s in snapshot['styles']}
    confirmation = ({s['id']: dict(tracking=.4 * (.83 if scaled else 1)) for s in snapshot['styles']}
                    if b'Tc' in state else None)
    pdf, model = folder / 'first.pdf', folder / 'first.json'
    report = write_editable(source, pdf, model, snapshot, [], fonts=fonts, paragraph_style=confirmation)
    first = observe(source, snapshot, [], fonts, report, confirmation=confirmation)
    first_accuracy = accuracy(pdf, report)
    out, saved, second = reedit(folder, pdf, model, 'noop', [])
    current = json.loads(model.read_text(encoding='utf-8'))
    noop = observe(pdf, current['paragraph'], [], current['fonts'], second)
    # Source relative positions vs nominal-only formula: explicit TJ is lost by B.
    p = paragraph_from_snapshot(source, snapshot)
    try:
        nominal_errors = []
        # Independent decimal construction oracle for these six literal fixtures.
        sx, sy, tx, ty = (F('0.83'), F('0.91'), F('7.25'), F('11.5')) if scaled else (F(1), F(1), F(0), F(0))
        tc = F('.4') if b'Tc' in state else F(0)
        tw = F('1.2') if b'Tw' in state else F(0)
        hz = F('.8') if b'Tz' in state else F(1)
        pen, oracle_errors = F(20), []
        for i, u in enumerate(p.units):
            if i == 1 and b'-100' in text:
                pen += F('1.2')
            if i == 1 and b'Tm' in text:
                pen = F(30)
            expected = [float(pen * sx + tx), float(260 - (200 * sy + ty))]
            oracle_errors.extend(abs(a-b) for a, b in zip(expected, u.observation['origin']))
            pen += (F('7.2') + tc + (tw if u.text == ' ' else 0)) * hz
        for a, b in zip(p.units, p.units[1:]):
            matrix = multiply(a.event.text_matrix, a.event.state.ctm)
            nominal_errors.append(abs((b.observation['origin'][0] - a.observation['origin'][0]) - a.char.advance * matrix[0]))
    finally:
        p.close()
    dump(folder / 'physical-observations.json', dict(first=first, noop=noop))
    return dict(first=first, noop=noop, comparison=compare(first, noop),
        accuracy=dict(first=first_accuracy, noop=accuracy(out, second)),
        source_decimal_oracle_max_origin_delta=max(oracle_errors, default=0),
        source_nominal_only_max_gap_error=max(nominal_errors, default=0))


def ink_counterexample(root):
    """Same canonical advance with source-trace ink still changes width/leading."""
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_attributed import source_pdf
    measured = []
    for x in ('20', '100', '200'):
        folder = root / ('ink-' + x)
        folder.mkdir()
        source = source_pdf(folder, f'BT /Regular 12 Tf {x} 200 Td (AB) Tj ET'.encode())
        snapshot = inspect_paragraph(source, make_selection(source, glyph_ids=[0, 1], explicit_width=100))
        p = paragraph_from_snapshot(source, snapshot)
        s = ParagraphShaper(p, apply_edits(p, snapshot, []), {})
        try:
            glyphs = [replace(g, advance=7.2) for g in s.shape(0, 2)]
            opts = dict(x=20, baseline=60, min_line_height=16, max_bottom=200, empty_ascent=10, empty_descent=2)
            layout = layout_attributed('AB', shape=lambda a, b: glyphs[a:b], width=100, **opts)
            measured.append(dict(source_x=x, width=layout.lines[0].width,
                ink=[list(g.ink.tuple()) for g in glyphs], ascent=layout.lines[0].ascent,
                descent=layout.lines[0].descent, glyphs=glyphs))
        finally:
            s.close()
            p.close()
    low, high = min(m['width'] for m in measured), max(m['width'] for m in measured)
    boundary = (low + high) / 2
    for m in measured:
        glyphs = m.pop('glyphs')
        layout = layout_attributed('AB', shape=lambda a, b: glyphs[a:b], width=boundary, **opts)
        m['boundary_allocation'] = [[l.start, l.end] for l in layout.lines]
        m['width_variants'] = {}
        for width in (14.399999, 14.4, 14.400001):
            layout = layout_attributed('AB', shape=lambda a, b: glyphs[a:b], width=width, **opts)
            m['width_variants'][str(width)] = [[l.start, l.end] for l in layout.lines]
    return dict(fixed_advance=7.2, width_probe=boundary, cases=measured,
        width_exact=len({m['width'] for m in measured}) == 1,
        allocation_exact=len({str(m['boundary_allocation']) for m in measured}) == 1)


def candidate_trials(root):
    font = ShapedFont(root / 'identity' / 'font.ttf')
    def supplied(text):
        run = font.shape(text)
        if any(len(g.text) != 1 or g.x_offset or g.y_offset for g in run.glyphs):
            raise ValueError('candidate fixture needs single-codepoint, zero-offset shaping')
        return [cell(g.text, metric=str(g.advance), upem=str(font.upem),
                     font='asset:' + font.source_sha256) for g in run.glyphs]
    original = dict(cells=[cell(c) for c in 'ONE TWO THREE '], x='20', baseline='60', width='80', leading='16')
    record = edit(original, 4, 7, supplied('FIVE SEVEN'))
    records = {'first': record}
    records['change'] = edit(record, 4, 9, [])
    records['growth'] = edit(records['change'], 5, 5, supplied(' EXTRA'))
    records['reflow'] = dict(record, width='50')
    comparisons = {}
    for name, r in records.items():
        dump(root / (name + '-candidate-input.json'), r)
        expected = plan(r)
        outputs = []
        for _ in range(3):
            run = subprocess.run([sys.executable, '-m', 'evaluations.anchors.layout_authority'],
                input=json.dumps(r), text=True, capture_output=True, check=True, cwd=ROOT)
            outputs.append(json.loads(run.stdout))
        comparisons[name] = dict(exact_all_three=all(o == expected for o in outputs),
            input_sha256=digest(r), plan_sha256=digest(expected), plan=expected)
    boundary = {}
    for width in ('14.399999', '14.4', '14.400001'):
        r = dict(record, cells=[cell(c) for c in 'AB '], width=width)
        boundary[width] = plan(r)
    spacing = {name: plan(dict(record, cells=[cell(c, **args) for c in 'A B ']))
               for name, args in [('default', {}), ('Tc', dict(tc='.4')), ('Tw', dict(tw='1.2')),
                                  ('Tz', dict(tz='80')), ('scaled', dict(scale='.83'))]}
    return dict(premise='Preconfirmed ASCII metric cells; no PDF persistence/binding proof; advance-only, not rich_layout',
        records=comparisons, boundary=boundary, spacing=spacing)


def evaluate(root):
    before = runtime_digest()
    value = dict(base=BASE, runtime_sha256=before, python=platform.python_version(), pymupdf=pymupdf.VersionBind,
        physical_observation=dict(lifecycle={v: lifecycle(root, v) for v in ('identity', 'ctm')}),
        logical_candidate=candidate_trials(root))
    cases = [('default', b'', b'(A B C ) Tj'), ('Tc', b'.4 Tc', b'(A B C ) Tj'),
             ('Tw', b'1.2 Tw', b'(A B C ) Tj'), ('Tz', b'80 Tz', b'(A B C ) Tj'),
             ('TJ', b'', b'[(A) -100 ( B C )] TJ'),
             ('Tm', b'', b'(A) Tj 1 0 0 1 30 200 Tm ( B C ) Tj')]
    value['physical_observation']['spacing'] = {
        name + ('_scaled' if scaled else ''): source_case(root, name + ('_scaled' if scaled else ''), state, text, scaled=scaled)
        for name, state, text in cases for scaled in (False, True)}
    value['physical_observation']['fixed_advance_trace_ink'] = ink_counterexample(root)
    value['verdict'] = 'NOT READY'
    value['blocker'] = 'B1-L-M: fixed advances still wrap differently through trace ink; common metric authority and current-only font/intent binding remain unproven'
    if before != runtime_digest():
        raise ValueError('runtime changed')
    return value


def write_summary(path, value):
    # One row per glyph keeps semantic/physical witnesses reviewable and compact.
    value = deepcopy(value)
    tables = {}
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k in ('glyphs', 'lines', 'changed') and isinstance(v, list):
                    token = f'__rows_{len(tables)}__'
                    tables[token] = '[\n' + ',\n'.join('      ' + json.dumps(r, separators=(',', ':')) for r in v) + '\n    ]'
                    obj[k] = token
                elif k == 'witnesses' and isinstance(v, dict):
                    token = f'__rows_{len(tables)}__'
                    tables[token] = '{\n' + ',\n'.join('      ' + json.dumps(name) + ': ' +
                        json.dumps(rows, separators=(',', ':')) for name, rows in v.items()) + '\n    }'
                    obj[k] = token
                else:
                    walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk(value)
    rendered = json.dumps(value, indent=2, allow_nan=False)
    for token, table in tables.items():
        rendered = rendered.replace(json.dumps(token), table)
    path.write_text(rendered + '\n', encoding='utf-8', newline='\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', nargs=6)
    parser.add_argument('--work', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    args.work.mkdir(parents=True, exist_ok=False)
    write_summary(args.output, evaluate(args.work.resolve()))


if __name__ == '__main__':
    main()
