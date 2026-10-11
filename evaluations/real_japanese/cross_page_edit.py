"""Unmodified LibreOffice pages 3–4: existing cross-page paragraph and owned follower."""
import argparse
import json
import logging
from pathlib import Path
import shutil
import subprocess

import pymupdf
from PIL import Image, ImageChops, ImageDraw

from pdfeditor.backend import PdfError
from pdfeditor.owned_flow_proposal import propose_owned_flow, accept_owned_flow_report
from pdfeditor.page_proposal import propose_page_flow, replace_in_flow
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.shaped_font import ShapedFont
from .owned_body_edit import SOURCE, URL, SHA, audit
from .continuation_edit import FONT_SHA, write, chars

FIND = '他組織に文書を渡す必要があったとしても'
LONG = '取引先などの他組織に文書を渡し、内容の確認や印刷を依頼する必要があったとしても'
SECOND = ('自由を制限', '利用の自由を制限')
GROUPS = [dict(id='A', parts=[dict(page=3, paragraph_id='P1'), dict(page=4, paragraph_id='P1')]),
          dict(id='B', parts=[dict(page=4, paragraph_id='P2')])]
RELATIONSHIP = ('Page 3 ends mid-sentence with 閲覧したり印刷; page 4 starts したりしたいだけならば. '
    'These are the same paragraph about ODF/PDF document exchange. The next indented paragraph '
    'それぞれのドキュメントについて explains why choosing interoperable formats matters. '
    'There is no intervening body, heading or footnote at the seam; the following heading is excluded.')


@proof_session
def run(output, font):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA and source_sha(font) == FONT_SHA
    pages = []
    for number, ids in [(3, range(26, 29)), (4, range(1, 7))]:
        kwargs = dict(line_ids=[f'p{number}-l{i}' for i in ids], font_candidates=[font],
                      include_embedded_fonts=True, font_cache=output / 'fonts')
        observed = propose_page_flow(SOURCE, number, **kwargs)
        pages.append(propose_page_flow(SOURCE, number, **kwargs,
            new_text={style['id']: LONG + SECOND[1] for style in observed['styles']}))
    proposal = propose_owned_flow(SOURCE, pages, paragraphs=GROUPS, relationship=RELATIONSHIP)
    write(output / 'proposal.json', proposal)
    accepted = accept_owned_flow_report(SOURCE, proposal, provider_choices={str(page['binding']['page']):
        {s['id']: dict(sha256=FONT_SHA, font_index=0, relation='substituted') for s in page['styles']} for page in pages})
    write(output / 'acceptance.json', accepted['receipt'])
    initial = state = accepted['state']; write(output / 'initial.json', state)
    originals = {pid: p['logical']['text'] for pid, p in state['paragraphs'].items()}
    expected = dict(originals); source = SOURCE; provider = ShapedFont(font)
    result = dict(verdict='NOT VALIDATED', source=dict(url=URL, sha256=SHA, paragraphs=originals),
        grouping=GROUPS, relationship=RELATIONSHIP, regions=state['regions'], acceptance=accepted['receipt'],
        provider=dict(sha256=FONT_SHA, face=0, appearance='explicit new-glyph substitution; source equivalence not claimed'), stages=[])
    edits = [{'A': (FIND, LONG)}, {'B': SECOND}, {'A': (LONG, FIND), 'B': SECOND[::-1]}, {'A': (FIND, LONG)}, {}]
    for index, (name, replacements) in enumerate(zip(('grow', 'second', 'shorten', 'regrow', 'noop'), edits), 1):
        changes = {}
        for pid, edit in replacements.items():
            changes.update(replace_in_flow(state, *edit, paragraph_id=pid,
                style_id='page-3:style-1' if pid == 'A' else 'page-4:style-1'))
            expected[pid] = expected[pid].replace(*edit)
        pdf, model = output / f'{index}-{name}.pdf', output / f'{index}-{name}.json'
        report = edit_shared_flow(source, state, pdf, model, changes); write(output / f'{index}-report.json', report)
        opened = open_shared_flow(pdf, model); assert opened['status'] == 'restored', opened.get('reason')
        state = opened['state']
        assert {pid: p['logical']['text'] for pid, p in state['paragraphs'].items()} == expected
        checked = audit(source, pdf, initial, state, report, provider)
        first, continued, follower = [report['plan']['fragments'][sid] for sid in ('slot-0', 'slot-1', 'slot-2')]
        assert first['text'] + continued['text'] == expected['A'] and follower['text'] == expected['B']
        gap = follower['lines'][0]['baseline'] - continued['lines'][-1]['baseline']
        assert abs(gap - 28.20001220703125) < .002
        shift = follower['lines'][0]['baseline'] - pages[1]['paragraphs'][1]['first_baseline']
        if name == 'shorten':
            assert expected == originals and len(continued['lines']) == 2
        else:
            assert len(first['lines']) == 3 and len(continued['lines']) == 3 and shift > 22
        if name == 'grow':
            old_ids = initial['slots']['slot-2']['binding']['paragraph']['selection']['glyph_ids']
            new_ids = state['slots']['slot-2']['binding']['paragraph']['selection']['glyph_ids']
            with pymupdf.open(SOURCE) as a, pymupdf.open(pdf) as b:
                old, new = chars(a[3]), chars(b[3])
                assert len(old_ids) == len(new_ids)
                error = max(abs(new[j][2][axis] - old[i][2][axis] - (shift if axis == 1 else 0))
                            for i, j in zip(old_ids, new_ids) for axis in (0, 1))
                assert error < .002
                checked['unchanged_follower_translation_error_pt'] = error
        if name == 'noop':
            with pymupdf.open(source) as a, pymupdf.open(pdf) as b:
                assert [p.get_pixmap(dpi=144).samples for p in a] == [p.get_pixmap(dpi=144).samples for p in b]
        result['stages'].append(dict(stage=name, reopen='restored', edits=replacements,
            follower_push_down_pt=shift, paragraph_baseline_gap_pt=gap, audit=checked))
        write(output / 'progress.json', result)
        print(name, 'restored', 'push', shift, [(p['paragraph'], p['page'], len(p['lines'])) for p in checked['placements']], flush=True)
        source = pdf
    refused_pdf, refused_model = output / 'refused.pdf', output / 'refused.json'
    try:
        edit_shared_flow(source, state, refused_pdf, refused_model,
            replace_in_flow(state, LONG, LONG * 3, paragraph_id='A', style_id='page-3:style-1'))
    except PdfError as exc:
        result['over_capacity_refusal'] = str(exc)
    else:
        raise AssertionError('unowned region or foreign heading overwritten')
    assert not refused_pdf.exists() and not refused_model.exists() and source_sha(SOURCE) == SHA
    if shutil.which('pdftoppm'):
        for name, path in [('before', SOURCE), ('after', source)]:
            subprocess.run(['pdftoppm', '-f', '3', '-l', '4', '-scale-to', '1600', '-png', str(path), str(output / name)],
                           check=True, capture_output=True)
        with pymupdf.open(SOURCE) as doc:
            for number in (3, 4):
                a = Image.open(output / f'before-{number:02}.png').convert('RGB')
                b = Image.open(output / f'after-{number:02}.png').convert('RGB')
                diff = ImageChops.difference(a, b)
                rect = next(r['bounds'] for r in state['regions'].values() if r['page'] == number)
                mask = (pymupdf.Rect(rect) * pymupdf.Matrix(a.width / doc[number-1].rect.width, a.height / doc[number-1].rect.height)).irect
                ImageDraw.Draw(diff).rectangle((mask.x0, mask.y0, mask.x1-1, mask.y1-1), fill=(0, 0, 0))
                assert diff.getbbox() is None
        result['poppler_outside_changed_pixels'] = {'3': 0, '4': 0}
    provider.font.close()
    result.update(verdict='REAL-WORLD CROSS-PAGE OWNED BODY EDITING VALIDATED — PASS', original_sha_unchanged=True)
    write(output / 'summary.json', result); print(result['verdict'], flush=True)
    return result


if __name__ == '__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--font', type=Path, required=True)
    args = parser.parse_args(); run(args.output, args.font)
