"""Measure saved synthetic source-ownership test artifacts without editing PDFs.

First run tests/test_source_ownership.py with --basetemp, then pass its
source-lifecycle0 directory. Historical PR #32 accumulation evidence is untouched.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from pdfeditor import source_ownership as owned
from pdfeditor.shared_flow import open_shared_flow
from evaluations.continuation.canonical_metrics import census, program
from evaluations.continuation.source_slot_accumulation import runtime_digest

BASE = '9cb6e0704f7caf062cbaf1460f0184409c344753'
STAGES = ('first', 'noop1', 'noop2', 'noop3', 'second', 'second_noop',
          'empty', 'empty_noop', 'regrow')
ROOT = Path(__file__).resolve().parents[2]


def baseline_digest():
    names = subprocess.check_output(['git', 'ls-tree', '--name-only', BASE, 'pdfeditor/'], cwd=ROOT).decode().splitlines()
    digest = hashlib.sha256()
    for name in sorted(n for n in names if n.endswith('.py')):
        data = subprocess.check_output(['git', 'show', BASE + ':' + name], cwd=ROOT)
        digest.update(Path(name).name.encode() + b'\0' + data)
    return digest.hexdigest()


def measure(work):
    rows = {}
    previous = program(work/'source.pdf', 1)
    for name in STAGES:
        pdf = work/(name+'.pdf')
        state = json.loads((work/(name+'.json')).read_text(encoding='utf-8'))
        restored = open_shared_flow(pdf, state)
        if restored['status'] != 'restored':
            raise ValueError(restored['reason'])
        record = state['slots']['slot-0']['source_output']
        data = program(pdf, 1)
        start, a, b, end = owned.inventory(data)[record['marker_id']]
        body = data[a:b]
        owned.grammar(body)
        old, current = census(previous), census(data)
        rows[name] = dict(body=census(body), page=current,
            page_delta=dict(bytes=len(data)-len(previous), operators=current['operator_count']-old['operator_count']),
            prefix_sha256=owned.sha(data[:start]), suffix_sha256=owned.sha(data[end:]),
            source_output=record)
        previous = data
    first = rows['first']
    for name in ('noop1', 'noop2', 'noop3'):
        assert rows[name]['body'] == first['body'] and rows[name]['page'] == first['page']
    for name, row in rows.items():
        assert owned.immutable(row['source_output']) == owned.immutable(first['source_output'])
        assert row['source_output']['current']['entry_context_sha256'] == first['source_output']['current']['entry_context_sha256']
        assert (row['prefix_sha256'], row['suffix_sha256']) == (first['prefix_sha256'], first['suffix_sha256'])
    for change, noop in [('second', 'second_noop'), ('empty', 'empty_noop')]:
        assert rows[change]['body'] == rows[noop]['body'] and rows[change]['page'] == rows[noop]['page']
    assert rows['regrow']['body'] == first['body']
    return dict(base_head=BASE, schema=state['schema'], engine_digest_before=baseline_digest(),
                engine_digest_after=runtime_digest(),
                fixture='tests/test_source_ownership.py::lifecycle, one source slot, page 1',
                real_pdf_validation='not run; PR #31 page 4/5 source-output-v1 eligibility remains unknown',
                stages=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('work', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    value = measure(args.work)
    # Compact per-stage rows; no raw PDF bytes or machine-specific paths.
    stages = value.pop('stages')
    text = json.dumps(value, ensure_ascii=False, indent=2)[:-2] + ',\n  "stages": {\n'
    text += ',\n'.join('    '+json.dumps(name)+': '+json.dumps(row, separators=(',', ':')) for name,row in stages.items())
    args.output.write_text(text+'\n  }\n}\n', encoding='utf-8', newline='\n')
