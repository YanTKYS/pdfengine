"""Timing and call counts for adjacent-intent propose/accept/edit on the Osaka guideline.

Run the same script against two code trees in one environment, e.g.:
  PYTHONPATH=<tree> .venv/bin/python evaluations/real_japanese/osaka_adjacent_intent_timing.py --label after
Every run starts from the unmodified corpus PDF and saves into a fresh
directory under tmp/osaka-adjacent-intent-timing/<label>/; these timing saves
are measurements, not evaluation results.
"""
import argparse
import cProfile
import json
from pathlib import Path
import pstats
import statistics
import time

import pdfeditor
from pdfeditor.adjacent_intent import accept_adjacent_intent, edit_adjacent_intent, propose_adjacent_intent
from pdfeditor.content_stream import ContentPage
from pdfeditor.selection import make_selection, source_sha

SOURCE = Path('evaluations/realpdf/corpus/word_osaka_guideline.pdf')
SOURCE_SHA = '9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe'
ROOT = Path('tmp/osaka-adjacent-intent-timing')
SELECTED, REPLACEMENT = 'において', 'におきまして'
# Functions whose call counts and cumulative time are reported per phase.
WATCH = ('observe_marked_content', '_walk@content_stream', 'structure_objects', 'leaf_span_structure',
         'adjacent_span_structure', 'plan_adjacent_span_edit', 'propose_adjacent_span', 'accept_adjacent_span',
         'edit_adjacent_span', '_observe_pair', '_observe', 'inspect_paragraph', 'inspect_selection_source',
         'make_selection', 'resolve_selection', 'plan_paragraph_edit', 'commit', 'program_pdf_bytes',
         'publish_program', '_pixels_equal', 'interpreted_paints', 'extract_font', '__init__@shaped_font',
         'propose_adjacent_intent', '_discover', '_regions', '_provider', '_coverage', '_follower_shift',
         'verify_adjacent_span_output', '_verify')


def selection():
    content = ContentPage(SOURCE, 1)
    try:
        drawn = [g['unicode'] for g in content.actual]
    finally:
        content.close()
    starts = [i for i in range(len(drawn)) if ''.join(drawn[i:i + len(SELECTED)]) == SELECTED]
    assert len(starts) == 1
    return make_selection(SOURCE, 1, glyph_ids=list(range(starts[0], starts[0] + len(SELECTED))))


def phases(root, profile=False):
    work = root / 'work'
    profilers = {name: cProfile.Profile() for name in ('propose', 'accept', 'edit')} if profile else {}
    times = {}

    def timed(name, call):
        start = time.perf_counter()
        if profile:
            profilers[name].enable()
        try:
            return call()
        finally:
            if profile:
                profilers[name].disable()
            times[name] = time.perf_counter() - start

    chosen = selection()
    proposal = timed('propose', lambda: propose_adjacent_intent(SOURCE, chosen, REPLACEMENT, work_dir=work))
    acceptance = timed('accept', lambda: accept_adjacent_intent(SOURCE, proposal, work_dir=work))
    result = timed('edit', lambda: edit_adjacent_intent(SOURCE, acceptance, root / 'edited.pdf', root / 'edited.json',
                                                        work_dir=work))
    assert result['after'] == proposal['candidates'][proposal['chosen']]['after']
    return times, profilers, dict(proposal_sha256=proposal['model_sha256'], edited_sha256=source_sha(root / 'edited.pdf'))


def counts(profiler):
    stats = pstats.Stats(profiler).stats
    result = {}
    for (filename, _, name), (_, calls, _, cumulative, _) in stats.items():
        for watched in WATCH:
            function, _, module = watched.partition('@')
            if name == function and (module in filename if module else 'pdfeditor' in filename or name == 'extract_font'):
                entry = result.setdefault(watched, dict(calls=0, cumulative_seconds=0.0))
                entry['calls'] += calls
                entry['cumulative_seconds'] += cumulative
    return dict(sorted(result.items(), key=lambda item: -item[1]['cumulative_seconds']))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', required=True)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    assert source_sha(SOURCE) == SOURCE_SHA
    base = ROOT / args.label
    base.mkdir(parents=True, exist_ok=False)
    runs = []
    for index in range(args.repeat):
        times, _, identity = phases(base / ('run%d' % index))
        runs.append(dict(times, **identity))
    _, profilers, _ = phases(base / 'profile', profile=True)
    assert source_sha(SOURCE) == SOURCE_SHA
    summary = dict(label=args.label, code=str(Path(pdfeditor.__file__).parent), repeat=args.repeat, runs=runs,
                   statistics={name: dict(mean=statistics.mean(r[name] for r in runs),
                                          stdev=statistics.stdev(r[name] for r in runs) if len(runs) > 1 else 0.0,
                                          min=min(r[name] for r in runs), max=max(r[name] for r in runs))
                               for name in ('propose', 'accept', 'edit')},
                   profile={name: counts(profiler) for name, profiler in profilers.items()})
    (base / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary['statistics'], indent=2))


if __name__ == '__main__':
    main()
