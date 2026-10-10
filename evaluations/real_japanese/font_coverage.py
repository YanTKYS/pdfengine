"""Unmodified Okinawa body: explicit new-glyph font choice, reflow and two saves.

Run: python -m evaluations.real_japanese.font_coverage --output tmp/okinawa-coverage --font-root tmp/b3/fonts
PDF/font/render outputs remain ignored. Source fonts are discovered by production code.
The external provider is explicitly selected by SHA, never silently substituted.
"""
import argparse
from pathlib import Path
import platform
from io import BytesIO
import hashlib
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import DecomposingRecordingPen
import shutil
import subprocess

import pymupdf

from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, accept_page_flow_report, replace_in_flow, default_font_roots
from pdfeditor.proof_session import proof_session
from pdfeditor.content_stream import ContentPage
from pdfeditor.shaped_font import ShapedFont
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from .body_edit import SOURCE, URL, SHA, PAGE, write, independent_audit

TARGET = ['p2-l44', 'p2-l45', 'p2-l46']
EDITS = [('その他詳細', '応募に関する申請期限、受付窓口及び郵送書類の確認その他詳細'),
         ('仕様書', '仕様書の追加内容')]
PROVIDER_SHA = '468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2'
PROVIDER_URL = 'https://raw.githubusercontent.com/google/fonts/main/ofl/bizudmincho/BIZUDMincho-Regular.ttf'


def source_codes(path):
    content=ContentPage(path,PAGE)
    try:
        return {i:dict(code=c.code.hex(),resource=e.state.font.name,
                        program_sha=hashlib.sha256(content.document.extract_font(e.state.font.xref)[3]).hexdigest())
                for e in content.events for c in e.chars for i in c.source_orders}
    finally:
        content.close()


def outline(font,gid):
    pen=DecomposingRecordingPen(font.getGlyphSet())
    font.getGlyphSet()[font.getGlyphName(gid)].draw(pen)
    return pen.value


def font_audit(before,after,plan):
    old_codes=source_codes(before);new_codes=source_codes(after)
    with pymupdf.open(before) as original, pymupdf.open(after) as saved:
        previous=[c for span in original[PAGE-1].get_texttrace() for c in span['chars']]
        actual=[c for span in saved[PAGE-1].get_texttrace() for c in span['chars']]
        aliases={f[4]:f[0] for f in saved[PAGE-1].get_fonts(full=True)}
        retained=provided=0
        programs={}
        for g in plan:
            hits=[i for i,c in enumerate(actual) if chr(c[0])==g['unicode']
                  and max(abs(c[2][k]-g['origin'][k]) for k in (0,1))<.002]
            assert len(hits)==1
            now=new_codes[hits[0]]
            assert now['code']==g['code'] and now['resource']==g['font_resource']
            assert actual[hits[0]][1]==g['glyph_id']
            if g['source_index'] is not None:
                old=old_codes[g['source_index']]
                assert now==old, 'retained code/resource/font program changed'
                assert previous[g['source_index']][1]==g['glyph_id']
                retained+=1
            else:
                alias=g['font_resource'].lstrip('/')
                if alias not in programs:
                    programs[alias]=TTFont(BytesIO(saved.extract_font(aliases[alias])[3]))
                assert g['glyph_id']!=0
                assert outline(programs[alias],g['glyph_id'])==PROVIDER.outline(g['glyph_id'])
                assert programs[alias]['hmtx'][programs[alias].getGlyphName(g['glyph_id'])][0]==PROVIDER.nominal_width(g['glyph_id'])
                provided+=1
        for f in programs.values():f.close()
    return dict(retained_codes_resources_gids_programs_equal=True,retained_glyphs=retained,
                provided_glyphs=provided,new_glyph_outlines_equal_selected_provider=True)



@proof_session
def run(output,font_root):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA
    proposal = propose_page_flow(SOURCE, PAGE, line_ids=TARGET,
        font_roots=[*default_font_roots(), str(font_root)], include_embedded_fonts=True,
        font_cache=output/'font-cache', new_text={'style-1': ''.join(e[1] for e in EDITS)})
    write(output/'proposal.json', proposal)
    assert proposal['status'] == 'needs-choice', proposal['refusals']
    assert [p['line_ids'] for p in proposal['paragraphs']] == [TARGET[:1], TARGET[1:]]
    selected = next(c for c in proposal['styles'][0]['providers']['substitutes'] if c['sha256']==PROVIDER_SHA and c['font_index']==0)
    global PROVIDER
    PROVIDER=ShapedFont(selected['path'],font_index=0)
    choices={'style-1':dict(sha256=PROVIDER_SHA,font_index=0,relation='substituted')}
    accepted = accept_page_flow_report(SOURCE, proposal, provider_choices=choices)
    state = accepted['state']
    write(output/'acceptance.json', accepted['receipt'])
    with pymupdf.open(SOURCE) as doc:
        cmap=TTFont(BytesIO(doc.extract_font(22)[3])).getBestCmap()
        new_chars=set(''.join(e[1] for e in EDITS))-set(''.join(doc[i].get_text() for i in range(len(doc))))
        missing=sorted(c for c in new_chars if ord(c) not in cmap)
        assert missing and '窓' in missing and '郵' in missing
        coverage=dict(source_cmap_characters=len(cmap),characters_absent_from_original_pdf_and_subset=missing,
                      codepoints=[f'U+{ord(c):04X}' for c in missing])
        old=TTFont(BytesIO(doc.extract_font(22)[3]));ch='申'
        assert outline(old,old.getGlyphID(cmap[ord(ch)]))!=PROVIDER.outline(PROVIDER.font.getGlyphID(PROVIDER.font.getBestCmap()[ord(ch)]))
        old.close()
    evidence=dict(selected_provider=selected,url=PROVIDER_URL,license='SIL OFL 1.1',
        appearance='explicit different font for new glyphs; not source outline equivalence',
        source_programs=proposal['embedded_fonts'],coverage=coverage)

    expected = {pid:p['logical']['text'] for pid,p in state['paragraphs'].items()}
    original_text = dict(expected)
    source = SOURCE
    gap = proposal['follows'][0]['minimum_baseline_gap']
    pitch = proposal['paragraphs'][1]['policy']['min_line_height']['value']
    original_baseline = proposal['paragraphs'][1]['first_baseline']
    result = dict(verdict='NOT VALIDATED', platform=platform.platform(),
        source=dict(url=URL, sha256=SHA, page=PAGE, line_ids=TARGET, paragraphs=original_text),
        fonts=evidence, region=proposal['region'], follows=proposal['follows'], reproduction=accepted['receipt']['reproduction'], stages=[])
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
        audit['fonts']=font_audit(source,pdf,glyphs)
        assert all(f['source_sha256']==PROVIDER_SHA for step in steps.values() for f in step['fonts'].values())
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
        ('nonconsecutive_selection', lambda: propose_page_flow(SOURCE, PAGE,
                                                              line_ids=[TARGET[0],TARGET[2]])),
        ('foreign_footer_collision', lambda: accept_page_flow(SOURCE, proposal,provider_choices=choices,
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
    PROVIDER.font.close()
    result['verdict']='REAL-WORLD JAPANESE FONT COVERAGE EXPANDED — PASS'
    write(output/'summary.json',result)
    print(result['verdict'],flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--font-root',type=Path,required=True,help='additional directory containing the explicitly selected OFL font')
    args=parser.parse_args()
    run(args.output,args.font_root)
