"""Unmodified Okinawa body text through public B1/T2, with independent audits.

Run from the checkout: python -m evaluations.real_japanese.body_edit --output tmp/okinawa-body
The original must already exist at the published corpus path. All PDF/font/PNG
and sidecar bytes stay in ignored local directories. Missing glyphs are errors.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import platform
import shutil
import subprocess

from PIL import Image, ImageChops, ImageDraw
import pymupdf
from pypdf import PdfReader

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.operator_nesting import audit as nesting_audit
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, replace_in_flow
from pdfeditor.paragraph import plan_paragraph
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow, plan_shared_flow
from pdfeditor import story_styles

SOURCE = Path(__file__).resolve().parents[1] / 'realpdf/corpus/word_okinawa_procurement.pdf'
URL = 'https://www.pref.okinawa.lg.jp/_res/projects/default_project/_page_/001/035/156/01_koukoku.pdf'
SHA = 'bbaa2b12eeaf1c7eac2dc91aca03c00da4ed948e7c14d99d879ff4c3083fd0b3'
PAGE = 2
TARGET = ['p2-l44']
TEXT = '(7) その他詳細は、「仕様書」及び「応募要領」による。 '
EDITS = [('その他詳細', '応募に関するその他詳細'), ('仕様書', '仕様書の内容')]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def chars(page, region):
    return [c for span in page.get_texttrace() for c in span['chars']
            if region.contains(pymupdf.Point(c[2]))]


def source_operators(snapshot):
    content = ContentPage(SOURCE, PAGE)
    try:
        selected = set(snapshot['selection']['glyph_ids'])
        events = [e for e in content.events if any(selected.intersection(c.source_orders) for c in e.chars)]
        start, end = events[0].operator.start, events[-1].operator.end
        counts = Counter(o.name for o in operators(content.streams[-content.page.xref]) if start <= o.start < end)
        return dict(selected_text_show_events=len(events), operators_between_first_and_last_show=dict(counts),
                    all_selected_Tc_Tw_zero=all(e.state.tc == e.state.tw == 0 for e in events),
                    positioning='Td and Tm both occur; not a separate Tm before every glyph')
    finally:
        content.close()


def independent_audit(source, output, bounds, expected, report):
    region = pymupdf.Rect(bounds)
    with pymupdf.open(source) as original, pymupdf.open(output) as edited:
        assert len(original) == len(edited)
        for index, (a, b) in enumerate(zip(original, edited)):
            selected = region if index == PAGE-1 else pymupdf.Rect()
            outside = lambda page: [(c[0], tuple(c[2]), tuple(c[3]))
                for span in page.get_texttrace() for c in span['chars']
                if not selected.contains(pymupdf.Point(c[2]))]
            assert outside(a) == outside(b), 'foreign Unicode/origins/bounds changed'
            drawings = lambda page: [{k:v for k,v in d.items() if k != 'seqno'} for d in page.get_drawings()]
            assert drawings(a) == drawings(b), 'drawings changed'
            images = lambda page: [{k:v for k,v in d.items() if k not in ('number','xref')}
                                  for d in page.get_image_info(hashes=True)]
            assert images(a) == images(b), 'images changed'
            ap, bp = a.get_pixmap(dpi=144), b.get_pixmap(dpi=144)
            assert (ap.width,ap.height,ap.n) == (bp.width,bp.height,bp.n)
            diff = ImageChops.difference(Image.frombytes('RGB',(ap.width,ap.height),ap.samples),
                                        Image.frombytes('RGB',(bp.width,bp.height),bp.samples))
            if index == PAGE-1:
                r = (region * pymupdf.Matrix(2,2)).irect
                ImageDraw.Draw(diff).rectangle((r.x0,r.y0,r.x1-1,r.y1-1), fill=(0,0,0))
            assert diff.getbbox() is None, 'MuPDF pixels changed outside accepted bounds'
        actual = chars(edited[PAGE-1], region)
        assert ''.join(chr(c[0]) for c in actual) == expected.rstrip(), 'target Unicode differs'
        planned = report['steps'][0]['report']['glyph_plan']
        assert len(actual) == len(planned)
        error = max(abs(c[2][axis]-g['origin'][axis]) for c,g in zip(actual,planned) for axis in (0,1))
        assert error <= .002  # the existing writer/readback geometry contract
        assert all(region.contains(pymupdf.Rect(c[3])) for c in actual)
        baselines = sorted({round(c[2][1],6) for c in actual})
    before, after = PdfReader(source), PdfReader(output)
    # pypdf extraction is independent of the engine's MuPDF observation.
    compact = lambda text: ''.join(text.split())
    assert compact(expected) in compact(after.pages[PAGE-1].extract_text())
    assert source.read_bytes().splitlines()[0] == output.read_bytes().splitlines()[0]
    for reader in (before, after):
        assert '/StructTreeRoot' not in reader.trailer['/Root']
        assert all('/StructParents' not in page for page in reader.pages)
    for number, page in enumerate(after.pages,1):
        assert not nesting_audit(page.get_contents().get_data())['violations']
        marks = observe_marked_content(output,number)
        assert marks['complete'] and marks['scopes'] == []
    return dict(unicode_mupdf='exact', unicode_pypdf='exact ignoring extraction whitespace',
        max_origin_error_pt=error, baselines=baselines, all_trace_bounds_inside_region=True,
        mupdf_outside_changed_pixels=0, other_pages_pixels_equal=True,
        foreign_text_origins_bounds_equal=True, drawings_equal=True, images_equal=True,
        structure='untagged before/after; no tree manufactured or removed', operator_nesting='valid PDF 1.x')


@proof_session
def run(output):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA, 'missing or different published original'
    snapshot = inspect_paragraph(SOURCE, make_selection(SOURCE,PAGE,line_ids=TARGET))
    assert snapshot['text'] == TEXT
    fonts, evidence = [], []
    with pymupdf.open(SOURCE) as document:
        for xref in sorted({s['font_xref'] for s in snapshot['styles']}):
            data = document.extract_font(xref)[3]
            assert data, 'source has no reusable embedded font program'
            path = output / f'embedded-{xref}.ttf'; path.write_bytes(data)
            assert path.read_bytes() == data
            fonts.append(path)
            evidence.append(dict(source_xref=xref, sha256=source_sha(path),
                relationship='byte-identical unmodified embedded program supplied explicitly by caller',
                coverage='only characters present in this source subset; missing characters remain refused'))
    proposal = propose_page_flow(SOURCE,PAGE,font_candidates=fonts,line_ids=TARGET)
    write(output/'proposal.json',proposal)
    assert proposal['status'] == 'proposed', proposal['refusals']
    state = accept_page_flow(SOURCE,proposal)
    # Diagnose the previous all-provider path with its actual nominal shaping;
    # this is a read-only measurement, not an alternative acceptance route.
    slot = state['slots']['slot-0']; paragraph = state['paragraphs']['P1']
    layout = slot['binding']['layout']
    nominal = plan_paragraph(SOURCE,snapshot,
        story_styles.replacement(slot['binding'],TEXT,paragraph['logical']['style_spans']),
        fonts=story_styles.providers(paragraph),render_styles=story_styles.render_styles(paragraph),**layout)
    actual_plan = plan_shared_flow(SOURCE,state,{})
    result = dict(verdict='NOT VALIDATED',platform=platform.platform(),python=platform.python_version(),
        source=dict(url=URL,sha256=SHA,page=PAGE,line_ids=TARGET,text=TEXT),
        fonts=evidence,spacing=snapshot['spacing'],source_operators=source_operators(snapshot),accepted_region=proposal['region'],
        reproduction=proposal['reproduction'],
        previous_nominal_width_pt=nominal['lines'][0]['width'],
        witnessed_width_pt=actual_plan['fragments']['slot-0']['lines'][0]['width'],stages=[])
    source, expected = SOURCE, TEXT
    for index, edit in enumerate(EDITS,1):
        pdf, sidecar = output/f'rev{index}.pdf', output/f'rev{index}.json'
        assert len(edit[0]) != len(edit[1])
        report = edit_shared_flow(source,state,pdf,sidecar,replace_in_flow(state,*edit))
        opened = open_shared_flow(pdf,sidecar)
        assert opened['status'] == 'restored', opened.get('reason')
        expected = expected.replace(*edit)
        audit = independent_audit(SOURCE,pdf,proposal['region']['bounds'],expected,report)
        # Verify retention against this revision's actual source occurrence.
        with pymupdf.open(source) as previous:
            original = [c for span in previous[PAGE-1].get_texttrace() for c in span['chars']]
            for glyph in report['steps'][0]['report']['glyph_plan']:
                if glyph['source_index'] is not None:
                    c = original[glyph['source_index']]
                    assert glyph['unicode'] == chr(c[0]) and glyph['glyph_id'] == c[1]
        row = report['steps'][0]['report']
        result['stages'].append(dict(revision=index,find=edit[0],replacement=edit[1],text=expected,
            replacement_lengths=list(map(len,edit)),status='restored',line_count=row['new_line_count'],
            retained_glyph_count=row['retained_glyph_count'],provided_glyph_count=row['provided_font_glyph_count'],
            lines=row['lines'],audit=audit))
        write(output/f'report{index}.json',report)
        source,state = pdf,opened['state']
    try:
        plan_shared_flow(source,state,replace_in_flow(state,'応募に関するその他詳細','応募に関するその他詳細'*4))
    except PdfError as exc:
        result['optional_wrap'] = dict(status='refused',reason=str(exc),outputs_written=False,
            explanation='item (8) is fixed foreign text immediately below the accepted one-line region')
    else:
        raise AssertionError('wrap unexpectedly fits; evaluate its saved lifecycle before claiming it')
    result['original_sha_unchanged'] = source_sha(SOURCE) == SHA
    assert result['original_sha_unchanged']
    if shutil.which('pdftoppm'):
        for name,path in [('before',SOURCE),('after',source)]:
            subprocess.run(['pdftoppm','-f',str(PAGE),'-l',str(PAGE),'-singlefile','-scale-to','1600','-png',
                            str(path),str(output/name)],check=True,capture_output=True)
        result['poppler'] = 'before/after full-page images rendered; visual inspection recorded separately'
    result['verdict'] = 'FIRST REAL-WORLD JAPANESE BODY TEXT EDITING VALIDATED — PASS'
    write(output/'summary.json',result)
    print(result['verdict'],flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    run(parser.parse_args().output)
