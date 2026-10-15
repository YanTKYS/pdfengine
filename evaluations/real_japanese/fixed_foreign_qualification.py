"""Observe real three-page prose and margin witnesses; stop at failed B1 ownership.

This is a qualification probe, never a three-page reflow certification. A newly
qualifying source requires a separate saved-revision edit lifecycle evaluation.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import time

import pymupdf

from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import _Analysis, propose_page_flow, accept_page_flow_report
from pdfeditor.post_edit_extension import inspect_fixed_foreign
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from .continuation_edit import FONT_SHA


SOURCE = Path(__file__).resolve().parents[1] / 'realpdf/corpus/cathedral_ja.pdf'
URL = 'https://cruel.org/freeware/cathedral.pdf'
SHA = '4a4fc57797d000eef36858db9c985133a946f383cee031565332ef98a029014f'
SELECTION = {22: range(26, 30), 23: range(1, 34), 24: range(1, 5)}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


@proof_session
def run(source, font, output):
    start = time.monotonic()
    started = datetime.now(timezone.utc).isoformat()
    if source_sha(source) != SHA or source_sha(font) != FONT_SHA:
        raise ValueError('Use the exact original PDF and pinned provider font')
    output.mkdir(parents=True, exist_ok=False)
    observations = []
    for number, indices in SELECTION.items():
        analysis = _Analysis(source, number)
        ids = [f'p{number}-l{i}' for i in indices]
        selected = [line for line in analysis.lines if line['id'] in ids]
        assert [line['id'] for line in selected] == ids
        # Human-reviewed printed page numbers; no numeric/footer inference in API.
        footer = next(line for line in analysis.lines if line['text'].strip() == str(number))
        witness = inspect_fixed_foreign(source, page=number, line_ids=[footer['id']],
            classification='footer', body_bounds=[0, 0, analysis.width, 760])
        assert witness['source_sha256'] == SHA
        write(output / f'page-{number}-margin-witness.json', witness)
        with pymupdf.open(source) as doc:
            fonts = doc[number - 1].get_fonts()
        observations.append(dict(page=number, proposed_line_ids=ids,
            characters=sum(len(line['text']) for line in selected), lines=len(selected),
            baselines=[line['baseline'] for line in selected],
            text='\n'.join(line['text'] for line in selected),
            glyph_ids=[gid for line in selected for gid in line['glyph_ids']],
            fonts=fonts, footer=dict(line_id=footer['id'], text=footer['text'],
                baseline=footer['baseline'], bounds=footer['bbox'],
                glyph_ids=footer['glyph_ids'], original_codes=witness['paint']),
            owner_status='not accepted', margin_witness='observed; grants no authority'))
    proposal = propose_page_flow(source, 22, line_ids=observations[0]['proposed_line_ids'],
        font_candidates=[font], include_embedded_fonts=True, font_cache=output / 'fonts')
    write(output / 'first-owner-proposal.json', proposal)
    try:
        accept_page_flow_report(source, proposal)
    except PdfError as exc:
        refusal = str(exc)
    else:
        raise AssertionError('B1 now qualifies: run the actual edit lifecycle before claiming success')
    assert proposal['status'] == 'refused'
    assert 'unique text-operator provenance' in refusal, refusal
    assert source_sha(source) == SHA
    assert not list(output.glob('*.pdf'))
    result = dict(verdict='REAL-WORLD THREE-PAGE SEQUENTIAL REFLOW — NOT VALIDATED',
        scope='real source and fixed-margin observation; first B1 owner refused',
        source=dict(url=URL, sha256=SHA, unchanged=True, pages=40),
        font_sha256=FONT_SHA, started_utc=started,
        environment=dict(python=platform.python_version(), platform=platform.platform(),
                         pymupdf=pymupdf.VersionBind, mupdf=pymupdf.VersionFitz),
        observations=observations, first_owner_refusals=proposal['refusals'], acceptance_refusal=refusal,
        accepted_owners=[], destinations=[], release_authority=[],
        saves=[], restores=[], raster_comparisons='not run: no accepted owner or edited PDF',
        publication='not attempted: first owner refused before editing; no PDF or state sidecar created',
        elapsed_seconds=time.monotonic() - start)
    write(output / 'summary.json', result)
    print(json.dumps({k: result[k] for k in ('verdict', 'scope', 'elapsed_seconds')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--font', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.source, args.font, args.output)
