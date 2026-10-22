"""Continuous-editing and unused-PDF evaluation for B2 (fixed manifest).

  .venv/bin/python -m prototypes.paragraph_frame.continuity_evaluate --root tmp/<new> --phase <phase>

Phases: ``sequences`` (three PDF-alone edit chains, with stored conditions and
inference only), ``new`` (unused originals: A, B1, B2 and a PDF-alone re-edit),
``substitute`` (one approved font vs. several approved candidates),
``search`` (classify native search/copy failures of saved outputs) and
``collect`` (compact git-managed results). The regression phase reuses
``generalization_evaluate`` on a new root. Every B2 save runs in its own
process from the PDF alone; oracle geometry is used by audits only.
Generated PDFs, rasters and detailed audit JSON stay under ``--root``.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import pymupdf
from .generalization_audit import audit, audit_noop, ids, records
from .generalization_evaluate import continuation_oracle
from .audit import extract, normalized

MANIFEST = Path('evaluations/paragraph_frame_continuity/manifest.json')
PRIOR = Path('evaluations/paragraph_frame_generalization/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'


def dump(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=list) + '\n')


def load(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def substitute(root, manifest):
    font = root / 'substitute.ttf'
    if not font.exists():
        font.write_bytes(pymupdf.Font('cjk').buffer)
    binding = dict(path=str(font.resolve()), sha256=sha(font), font_index=0, relation='substituted')
    if binding['sha256'] != manifest['font']['sha256']:
        raise ValueError('common approved font hash mismatch')
    dump(root / 'substitute.json', binding)
    return root / 'substitute.json'


def b2(source, page, old, new, output, substitute_json, *, stored=True):
    """propose → approve(acknowledge) → save in a separate process; returns (proposal, save) or refusal."""
    args = [sys.executable, '-m', 'prototypes.paragraph_frame.generalization_cli', 'reedit', '--source', str(source),
            '--page', str(page), '--old', old, '--new', new, '--output', str(output), '--acknowledge-inference']
    if substitute_json:
        args += ['--substitute-json', str(substitute_json)]
    if not stored:
        args.append('--no-stored-conditions')
    started = time.perf_counter()
    run = subprocess.run(args, capture_output=True)
    seconds = time.perf_counter() - started
    if run.returncode:
        last = [l for l in run.stderr.decode().splitlines() if l.strip()]
        return dict(outcome='refused', reason=last[-1] if last else 'unknown', seconds=seconds)
    return dict(outcome='saved', proposal=load(output.with_suffix('.proposal.json')), save=load(output.with_suffix('.save.json')),
                seconds=seconds, pdf_sha256=sha(output))


def paragraph_rows(path, page, glyph_ids):
    """Drawn rows of the given glyphs: text, x, end, baseline, styles (independent of B2)."""
    rows = {}
    for r in records(path, page):
        if r['id'] in glyph_ids and r['text'].strip():
            rows.setdefault(round(r['origin'][1], 3), []).append(r)
    result = []
    for y, row in sorted(rows.items()):
        row.sort(key=lambda r: r['origin'][0])
        result.append(dict(text=''.join(r['text'] for r in row), x=round(row[0]['origin'][0], 3), end=round(max(r['bbox'][2] for r in row), 3),
                           baseline=y, fonts=sorted({r['font'] for r in row}), sizes=sorted({r['size'] for r in row}),
                           fills=sorted({json.dumps(r['fill']) for r in row})))
    return result


def layout_metrics(spec, rows):
    """Paragraph metrics comparable across saves: first x, continuation x, right extent, pitch."""
    pitch = [round(b['baseline'] - a['baseline'], 3) for a, b in zip(rows, rows[1:])]
    return dict(lines=len(rows), first_x=rows[0]['x'] if rows else None,
                continuation_x=sorted({r['x'] for r in rows[1:]}), right_extent=max((r['end'] for r in rows), default=None),
                pitch=sorted(set(pitch)), first_baseline=rows[0]['baseline'] if rows else None,
                fonts=sorted({f for r in rows for f in r['fonts']}), sizes=sorted({s for r in rows for s in r['sizes']}),
                fills=sorted({f for r in rows for f in r['fills']}))


def oracle_after(spec, expected, path):
    """Evaluation-only paragraph oracle of a saved PDF (target text replaced)."""
    result = continuation_oracle(spec, dict(expected=expected), path)
    result['path'] = str(path)
    return result


def summary_of_audit(result):
    return dict(outcome=result['outcome'], failed=result['failed'], output_sha256=result['output_sha256'],
                outside_pixels={k: sum(v['outside_pixels'] for v in rows) for k, rows in result['pixels'].items()},
                follower_metrics=result['follower_metrics'])


def phase_sequences(root, manifest, prior, subst):
    out = {}
    for seq in manifest['sequences']:
        spec = prior['documents'][seq['document']]
        original = paragraph_rows(spec['path'], spec['page'], set(ids(spec['paragraphs'][0])))
        follower_ids = [set(ids(p)) for p in spec['paragraphs'][1:]]
        original_followers = [paragraph_rows(spec['path'], spec['page'], ids) for ids in follower_ids]
        for mode in ('stored', 'inference'):
            folder = root / 'sequences' / f"{seq['id']}-{mode}"
            folder.mkdir(parents=True, exist_ok=True)
            source, oracle, previous = Path(spec['path']), spec, spec['paragraphs'][0]['text']
            steps = []
            for n, step in enumerate(seq['steps'], 1):
                output = folder / f'step{n}.pdf'
                if output.exists():
                    raise ValueError('existing sequence output; use a new root')
                saved = b2(source, spec['page'], step['old'], step['new'], output, subst, stored=mode == 'stored')
                record = dict(step=n, category=step['category'], old=step['old'], new=step['new'], outcome=saved['outcome'],
                              seconds=round(saved['seconds'], 2))
                if saved['outcome'] != 'saved':
                    record['reason'] = saved['reason']
                    steps.append(record)
                    break
                proposal = saved['proposal']
                inferred = proposal['evidence']['inferred']
                record.update(pdf_sha256=saved['pdf_sha256'], conditions=proposal['evidence']['conditions']['status'],
                              proposed=dict(left=round(inferred['left'], 3), right=round(inferred['right'], 3), first_x=round(inferred['first_x'], 3),
                                            line_height=round(inferred['line_height'], 3), leading_basis=inferred['leading_basis']),
                              planned_lines=[l['text'] for l in proposal['lines']], font_counts=proposal['font_counts'],
                              stored_after_save=saved['save'].get('stored_conditions'))
                case = dict(old=step['old'], new=step['new'], expected=step['expected_target'])
                result = audit(str(source), str(output), oracle, case, folder / f'step{n}-audit')
                dump(folder / f'step{n}.audit.json', result)
                record['audit'] = summary_of_audit(result)
                oracle = oracle_after(spec, step['expected_target'], output)
                rows = paragraph_rows(output, spec['page'], set(oracle['paragraphs'][0]['glyph_ids']))
                followers = [paragraph_rows(output, spec['page'], set(p['glyph_ids'])) for p in oracle['paragraphs'][1:]]
                record['layout'] = layout_metrics(spec, rows)
                record['rows'] = [dict(text=r['text'], x=r['x'], baseline=r['baseline']) for r in rows]
                if step['category'] == 'restore-original-text':
                    record['restore_equals_original'] = [(r['text'], r['x'], r['baseline']) for r in rows] == \
                        [(r['text'], r['x'], r['baseline']) for r in original]
                record['followers_at_original_positions'] = [[(r['text'], r['x'], r['baseline']) for r in f] for f in followers] == \
                    [[(r['text'], r['x'], r['baseline']) for r in f] for f in original_followers]
                steps.append(record)
                source, previous = output, step['expected_target']
            noop = folder / 'final-noop.pdf'
            final = folder / f'step{len(steps)}.pdf'
            noop_result = None
            if steps and steps[-1]['outcome'] == 'saved' and not noop.exists():
                subprocess.run([sys.executable, '-m', 'prototypes.paragraph_frame.generalization_cli', 'noop', '--source', str(final),
                                '--output', str(noop)], check=True, capture_output=True)
                noop_result = audit_noop(final, noop, spec['page'], folder / 'noop-audit')['outcome']
            out[f"{seq['id']}-{mode}"] = dict(sequence=seq['id'], document=seq['document'], tagged=seq['tagged'], mode=mode,
                                               original=layout_metrics(spec, original), steps=steps, final_noop=noop_result)
            print(seq['id'], mode, [(s['outcome'], s.get('audit', {}).get('outcome'), s.get('conditions')) for s in steps], flush=True)
    dump(root / 'sequences.json', out)
    return out


def expand(ranges):
    return [i for a, b in ranges for i in range(a, b + 1)]


def joins_of(logical, drawn):
    """Logical-text indices of spaces that have no drawn glyph (implicit line joins)."""
    joins, d = [], 0
    for i, ch in enumerate(logical):
        if d < len(drawn) and drawn[d] == ch:
            d += 1
        elif ch == ' ':
            joins.append(i)
        else:
            return None
    return joins if d == len(drawn) else None


def new_spec(doc, region):
    paragraphs = []
    for n, p in enumerate(region['paragraphs']):
        ids = expand(p['glyph_ids'])
        joins = joins_of(p['text'], p['drawn_text'])
        paragraphs.append(dict(text=p['text'] if n == 0 else p['drawn_text'], glyph_ids=ids, joins=joins or [],
                               source_line_baselines=p['source_line_baselines'], logical_text=p['text'],
                               line_ids=p['line_ids'], line_x=p['line_x']))
    first = region['paragraphs'][0]
    gaps = [b - a for a, b in zip(first['source_line_baselines'], first['source_line_baselines'][1:])]
    nexts = [b['source_line_baselines'][0] - a['source_line_baselines'][-1] for a, b in zip(region['paragraphs'], region['paragraphs'][1:])]
    return dict(path=doc['path'], page=doc['page'], frame=region['frame'], paragraphs=paragraphs,
                line_ids=[l for p in region['paragraphs'] for l in p['line_ids']],
                left=min(x for p in region['paragraphs'] for x in p['line_x']), right=max(e for p in region['paragraphs'] for e in p['line_pen_end']),
                first_x=first['line_x'][0], baseline=first['source_line_baselines'][0],
                line_height=sorted(gaps)[len(gaps) // 2] if gaps else 15, paragraph_gap=nexts[0] if nexts else 20)


def phase_new(root, manifest, subst):
    from .baseline import run as baseline
    from .engine import rewrite
    out = {}
    font = json.loads(Path(subst).read_text())['path']
    for case in manifest['requests']:
        doc = manifest['documents'][case['document']]
        region = doc['regions'][case['region']]
        spec = new_spec(doc, region)
        folder = root / 'new' / case['id']
        folder.mkdir(parents=True, exist_ok=True)
        record = dict(id=case['id'], document=case['document'], category=case['category'], old=case['old'], new=case['new'])
        target = spec['paragraphs'][0]
        if case['selectable_by_unicode'] and target['joins'] is not None:
            at = target['logical_text'].index(case['old'])
            joins = set(target['joins'])
            glyphs = [target['glyph_ids'][i - sum(1 for j in joins if j < i)] for i in range(at, at + len(case['old'])) if i not in joins]
        else:
            glyphs = None
        output = folder / 'B2.pdf'
        saved = b2(doc['path'], doc['page'], case['old'], case['new'], output, subst)
        b2_record = dict(edit=saved['outcome'], reason=saved.get('reason'), seconds=round(saved['seconds'], 2))
        if saved['outcome'] == 'saved':
            proposal = saved['proposal']
            b2_record.update(lines=[l['text'] for l in proposal['lines']], font_counts=proposal['font_counts'],
                             alignment=proposal['evidence']['inferred'].get('alignment'),
                             containers=proposal['evidence']['conditions']['containers'], warnings=proposal['evidence']['warnings'],
                             follower_translation=saved['save']['follower_translation'], pdf_sha256=saved['pdf_sha256'])
            result = audit(doc['path'], str(output), spec, dict(case, expected=case['expected']), folder / 'B2-audit')
            dump(folder / 'B2.audit.json', result)
            b2_record['audit'] = summary_of_audit(result)
            # PDF-alone re-edit reverting the change, then an unedited save.
            again = folder / 'B2-reedit.pdf'
            back = b2(output, doc['page'], case['new'], case['old'], again, subst)
            b2_record['reedit'] = dict(edit=back['outcome'], reason=back.get('reason'))
            if back['outcome'] == 'saved':
                b2_record['reedit']['conditions'] = back['proposal']['evidence']['conditions']['status']
                oracle = oracle_after(spec, case['expected'], output)
                oracle['paragraphs'][0]['joins'] = []
                result = audit(str(output), str(again), oracle, dict(old=case['new'], new=case['old'], expected=target['logical_text']),
                               folder / 'B2-reedit-audit')
                dump(folder / 'B2-reedit.audit.json', result)
                b2_record['reedit']['audit'] = summary_of_audit(result)
                noop = folder / 'B2-noop.pdf'
                subprocess.run([sys.executable, '-m', 'prototypes.paragraph_frame.generalization_cli', 'noop', '--source', str(again),
                                '--output', str(noop)], check=True, capture_output=True)
                b2_record['reedit']['noop'] = audit_noop(again, noop, doc['page'], folder / 'noop-audit')['outcome']
        record['B2'] = b2_record
        # A: unchanged public pdfeditor routes, given the selected glyph occurrence.
        if glyphs is not None:
            try:
                a = baseline(dict(case, glyph_ids=glyphs), spec, font, folder / 'A')
            except Exception as exc:
                a = dict(outcome='refused', reason=f'{type(exc).__name__}: {exc}')
            saved_a = next((folder / 'A' / n for n in ('flow-edited.pdf', 'edited.pdf') if (folder / 'A' / n).exists()), None)
            record['A'] = dict(edit='saved' if saved_a else 'refused', routes={k: v.get('status') or v.get('acceptance_refusal')
                                                                                for k, v in a.items() if isinstance(v, dict) and k != 'timing'})
            if saved_a:
                result = audit(doc['path'], str(saved_a), spec, dict(case, expected=case['expected']), folder / 'A-audit', arm='A')
                dump(folder / 'A.audit.json', result)
                record['A']['audit'] = summary_of_audit(result)
        else:
            record['A'] = dict(edit='not attempted', reason='the selected text has no Unicode occurrence in the PDF')
        # B1: the PR #86 engine with the oracle frame as manual input.
        try:
            r = rewrite(doc['path'], folder / 'B1.pdf', spec, case['old'], case['new'], font)
            record['B1'] = dict(edit='saved', lines=r['lines'])
        except Exception as exc:
            record['B1'] = dict(edit='refused', reason=f'{type(exc).__name__}: {exc}')
        out[case['id']] = record
        print(case['id'], 'B2', b2_record['edit'], b2_record.get('audit', {}).get('outcome'), (b2_record.get('reason') or '')[-120:],
              '| A', record['A']['edit'], '| B1', record['B1']['edit'], flush=True)
    dump(root / 'new.json', out)
    return out


def phase_substitute(root, manifest, prior, subst, fonts):
    """The PR #87 substitute cases with one approved font vs. several approved candidates."""
    candidates = [json.loads(Path(subst).read_text())] + [dict(path=str(Path(f).resolve()), sha256=sha(f), font_index=0, relation='substituted')
                                                          for f in fonts]
    names = {c['sha256']: Path(c['path']).name for c in candidates}
    multi = root / 'candidates.json'
    dump(multi, candidates)
    out = {}
    for case in prior['requests']:
        if case['id'] not in ('B', 'D', 'F1', 'F3', 'F4', 'N1', 'N3', 'N4'):
            continue
        spec = prior['documents'][case['document']]
        folder = root / 'substitute' / case['id']
        folder.mkdir(parents=True, exist_ok=True)
        saved = b2(spec['path'], spec['page'], case['old'], case['new'], folder / 'B2.pdf', multi)
        record = dict(edit=saved['outcome'], reason=saved.get('reason'))
        if saved['outcome'] == 'saved':
            choice = saved['proposal']['substitute_choice']
            record.update(font_counts=saved['proposal']['font_counts'],
                          choice={x: dict(chosen=names[c['chosen_sha256']], scores={names[s['sha256']]: s['distance'] for s in c['scores']})
                                  for x, c in choice.items()})
            result = audit(spec['path'], str(folder / 'B2.pdf'), spec, case, folder / 'audit')
            dump(folder / 'audit.json', result)
            record['audit'] = summary_of_audit(result)
        out[case['id']] = record
        print(case['id'], record.get('choice'), record.get('audit', {}).get('outcome'), flush=True)
    dump(root / 'substitute.json', out)
    return out


def phase_search(root, manifest, prior, regression):
    """Separate reader line-boundary search from PDF text information for failed searches."""
    out = {}
    for case in prior['requests']:
        for kind, path, needle, audit_path in (
                ('initial', regression / f"{case['id']}.pdf", case['new'], regression / f"{case['id']}.audit.json"),
                ('reedit', regression / f"{case['id']}-reedit.pdf", case['reedit_new'], regression / f"{case['id']}.lifecycle.audit.json")):
            if not path.exists() or not audit_path.exists():
                continue
            result = load(audit_path)
            failed = [k for k in result['failed'] if k.endswith('_copy_extract') or k == 'native_replacement_search']
            if not failed:
                continue
            spec = prior['documents'][case['document']]
            with pymupdf.open(path) as d:
                page = d[spec['page'] - 1]
                lines = [''.join(s['text'] for s in l['spans']) for b in page.get_text('dict')['blocks'] if 'lines' in b for l in b['lines']]
                text = page.get_text()
                hits = page.search_for(needle)
            joined = normalized(text)
            text_layer = extract(str(path), spec['page'], root)
            crosses = not any(needle in l for l in lines)
            out[f"{case['id']}-{kind}"] = dict(
                failed=failed, needle=needle, internal_drawn_text_exact=result['checks'].get('exact_drawn_paragraphs'),
                needle_crosses_reader_line=crosses, mupdf_search_hits=len(hits),
                mupdf_text_joined_contains=needle in joined, poppler_text_joined_contains=needle in normalized(text_layer['poppler']),
                classification=('reader line-boundary search (text information correct)'
                                if result['checks'].get('exact_drawn_paragraphs') and crosses and needle in joined else
                                'reader extraction spacing (inferred space inside the selection)' if result['checks'].get('exact_drawn_paragraphs') and not crosses else
                                'PDF text information'))
    dump(root / 'search.json', out)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--phase', required=True, choices=['sequences', 'new', 'substitute', 'search'])
    parser.add_argument('--regression-root', type=Path, help='root used for generalization_evaluate (search phase)')
    parser.add_argument('--fonts', nargs='*', default=[], help='additional approved substitute candidates (substitute phase)')
    args = parser.parse_args()
    manifest, prior = load(MANIFEST), load(PRIOR)
    for spec in list(prior['documents'].values()) + [d for d in manifest['documents'].values() if 'regions' in d]:
        if sha(spec['path']) != spec['sha256']:
            raise ValueError('source hash mismatch: ' + spec['path'])
    args.root.mkdir(parents=True, exist_ok=True)
    subst = substitute(args.root, manifest)
    if args.phase == 'sequences':
        phase_sequences(args.root, manifest, prior, subst)
    elif args.phase == 'new':
        phase_new(args.root, manifest, subst)
    elif args.phase == 'substitute':
        phase_substitute(args.root, manifest, prior, subst, args.fonts)
    else:
        phase_search(args.root, manifest, prior, args.regression_root)


if __name__ == '__main__':
    main()
