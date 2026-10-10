"""Unmodified Okinawa numbered body reflow, two saves and independent audits.

Run: python -m evaluations.real_japanese.body_reflow --output tmp/okinawa-reflow
PDF/font/render outputs remain ignored. No repair, manual region or font fallback.
"""
import argparse
from pathlib import Path
import platform
import shutil
import subprocess

import pymupdf

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, replace_in_flow
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from .body_edit import SOURCE, URL, SHA, PAGE, write, independent_audit

TARGET = ['p2-l44', 'p2-l45', 'p2-l46']
EDITS = [('その他詳細', '応募に関する手続、提出書類の内容及び企画提案のその他詳細'),
         ('仕様書', '仕様書の内容')]


@proof_session
def run(output):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA
    snapshot = inspect_paragraph(SOURCE, make_selection(SOURCE, PAGE, line_ids=TARGET))
    fonts, evidence = [], []
    with pymupdf.open(SOURCE) as doc:
        for xref in sorted({s['font_xref'] for s in snapshot['styles']}):
            data = doc.extract_font(xref)[3]
            assert data
            path = output/f'embedded-{xref}.ttf'; path.write_bytes(data)
            fonts.append(path)
            evidence.append(dict(xref=xref, sha256=source_sha(path), relation='unmodified embedded source program'))
    proposal = propose_page_flow(SOURCE, PAGE, font_candidates=fonts, line_ids=TARGET)
    write(output/'proposal.json', proposal)
    assert proposal['status'] == 'proposed', proposal['refusals']
    assert [p['line_ids'] for p in proposal['paragraphs']] == [TARGET[:1], TARGET[1:]]
    state = accept_page_flow(SOURCE, proposal)
    expected = {pid:p['logical']['text'] for pid,p in state['paragraphs'].items()}
    original_text = dict(expected)
    source = SOURCE
    gap = proposal['follows'][0]['minimum_baseline_gap']
    pitch = proposal['paragraphs'][1]['policy']['min_line_height']['value']
    original_baseline = proposal['paragraphs'][1]['first_baseline']
    result = dict(verdict='NOT VALIDATED', platform=platform.platform(),
        source=dict(url=URL, sha256=SHA, page=PAGE, line_ids=TARGET, paragraphs=original_text),
        fonts=evidence, region=proposal['region'], follows=proposal['follows'], reproduction=proposal['reproduction'], stages=[])
    for index, edit in enumerate(EDITS, 1):
        pdf, sidecar = output/f'rev{index}.pdf', output/f'rev{index}.json'
        report = edit_shared_flow(source, state, pdf, sidecar,
                                 replace_in_flow(state, *edit, paragraph_id='P1'))
        write(output/f'report{index}.json', report)
        opened = open_shared_flow(pdf, sidecar)
        assert opened['status'] == 'restored', opened.get('reason')
        expected['P1'] = expected['P1'].replace(*edit)
        assert len(edit[0]) != len(edit[1])
        assert {pid:p['logical']['text'] for pid,p in opened['state']['paragraphs'].items()} == expected
        fragments = [report['plan']['fragments'][sid] for sid in ('slot-0','slot-1')]
        assert [len(f['lines']) for f in fragments] == [2,2]
        a,b = fragments
        assert abs(b['lines'][0]['baseline']-a['lines'][-1]['baseline']-gap) < .002
        assert abs(b['lines'][0]['baseline']-original_baseline-pitch) < .002
        # Derive expected painted text from logical Unicode and the verified line ranges.
        visible = ''.join(f['text'][l['start']:l['end']].rstrip() for f in fragments for l in f['lines'])
        steps = {s['paragraph_id']:s['report'] for s in report['steps']}
        glyphs = [g for pid in ('P1','P2') for g in steps[pid]['glyph_plan']]
        audit = independent_audit(SOURCE,pdf,proposal['region']['bounds'],visible,report,glyph_plan=glyphs)
        with pymupdf.open(source) as previous:
            observed = [c for span in previous[PAGE-1].get_texttrace() for c in span['chars']]
            for g in glyphs:
                if g['source_index'] is not None:
                    c = observed[g['source_index']]
                    assert g['unicode'] == chr(c[0]) and g['glyph_id'] == c[1]
        # The untouched follower must be a translation of its actual source glyphs.
        with pymupdf.open(SOURCE) as original, pymupdf.open(pdf) as saved:
            old = [c for span in original[PAGE-1].get_texttrace() for c in span['chars']]
            old = [old[i] for i in proposal['paragraphs'][1]['glyph_ids']]
            new = [c for span in saved[PAGE-1].get_texttrace() for c in span['chars']
                   if c[2][1] >= b['lines'][0]['baseline']-.002
                   and pymupdf.Rect(proposal['region']['bounds']).contains(pymupdf.Point(c[2]))]
            old = old[:len(new)]  # only layout-trimmed terminal whitespace is unpainted
            assert ''.join(chr(c[0]) for c in old) == expected['P2'].rstrip()
            assert len(old)==len(new)
            translation_error = max(abs(n[2][axis]-o[2][axis]-(pitch if axis==1 else 0))
                                    for o,n in zip(old,new) for axis in (0,1))
            assert translation_error < .002
            audit['unchanged_follower_translation_error_pt'] = translation_error
        paragraphs=[]
        for pid,f in zip(('P1','P2'),fragments):
            starts = [next(g for g in f['glyphs'] if g['start']==l['start'])['origin'][0] for l in f['lines']]
            assert all(abs(x-y)<.002 for x,y in zip(starts,[70.91999816894531,88.91999816894531]))
            paragraphs.append(dict(id=pid,text=expected[pid],
                visual_lines=[f['text'][l['start']:l['end']].rstrip() for l in f['lines']],
                baselines=[l['baseline'] for l in f['lines']],line_x=starts,
                retained=steps[pid]['retained_glyph_count'],provided=steps[pid]['provided_font_glyph_count']))
        result['stages'].append(dict(revision=index,find=edit[0],replacement=edit[1],
            replacement_lengths=list(map(len,edit)),reopen='restored',paragraphs=paragraphs,
            following_push_down_pt=b['lines'][0]['baseline']-original_baseline,
            preserved_baseline_gap_pt=gap,audit=audit))
        source,state=pdf,opened['state']
        print(f'revision {index}: restored, 2 + 2 lines, following paragraph +{pitch:.6f} pt',flush=True)
    overflow_pdf, overflow_model=output/'overflow.pdf',output/'overflow.json'
    try:
        edit_shared_flow(source,state,overflow_pdf,overflow_model,
            replace_in_flow(state,EDITS[0][1],EDITS[0][1]*5,paragraph_id='P1'))
    except PdfError as exc:
        assert 'exceed all explicitly confirmed shared regions' in str(exc)
        result['overflow']=dict(status='refused',reason=str(exc))
    else:
        raise AssertionError('overflow was not refused')
    assert not overflow_pdf.exists() and not overflow_model.exists()
    refusals = {}
    for name, operation in [
        ('nonconsecutive_selection', lambda: propose_page_flow(SOURCE, PAGE, font_candidates=fonts,
                                                              line_ids=[TARGET[0],TARGET[2]])),
        ('foreign_footer_collision', lambda: accept_page_flow(SOURCE, proposal,
                                                              overrides={'region_bottom':780})),
    ]:
        try:
            operation()
        except PdfError as exc:
            refusals[name] = str(exc)
        else:
            raise AssertionError(f'{name} was not refused')
    assert 'consecutive' in refusals['nonconsecutive_selection']
    assert 'collides with foreign content' in refusals['foreign_footer_collision']
    result['additional_refusals'] = refusals
    assert source_sha(SOURCE)==SHA
    result['original_sha_unchanged']=True
    if shutil.which('pdftoppm'):
        for name,path in [('before',SOURCE),('after',source)]:
            subprocess.run(['pdftoppm','-f',str(PAGE),'-l',str(PAGE),'-singlefile','-scale-to','1600','-png',
                            str(path),str(output/name)],check=True,capture_output=True)
        result['poppler']='full page images rendered; visual inspection recorded separately'
    result['verdict']='FIRST REAL-WORLD JAPANESE BODY REFLOW VALIDATED — PASS'
    write(output/'summary.json',result)
    print(result['verdict'],flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output)
