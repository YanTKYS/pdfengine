"""Actual public B1/T2 front door against an unchanged, published Japanese PDF.

No external PDF/font is bundled. Run on Windows from the repository root:
  python -m evaluations.real_japanese.evaluate --output tmp/real-japanese-current
The verdict records failure if acceptance refuses; it never substitutes a fixture.
"""
import argparse
import json
from fractions import Fraction
from io import BytesIO
from fontTools.ttLib import TTFont
from pathlib import Path
import platform

from PIL import Image, ImageChops, ImageDraw
import pymupdf
from pypdf import PdfReader

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, replace_in_flow
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha, make_selection, inspect_selection_source
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow, plan_shared_flow

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'evaluations/realpdf/corpus/word_kyoto_questions.pdf'
SHA = '61ef6bb9aaf0e6c6099fc7b4b67de804d311855ec0265d4648d197fb75dfad46'
URL = 'https://www.city.kyoto.lg.jp/kankyo/cmsfiles/contents/0000311/311254/kaitou_hp.pdf'
PAGE = 1
TARGET = ['p1-l1']
TEXT = '質問に対する回答 '
EDITS = [('質問', '寄せられた質問'), ('回答', '回答について')]
FONT_PATHS = [Path('C:/Windows/Fonts') / name for name in
              ('msmincho.ttc', 'msgothic.ttc', 'arial.ttf', 'yugothm.ttc', 'meiryo.ttc')]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')


def provider_outline_evidence(snapshot, provider):
    """The caller's choice among equal-width fonts has an exact shape witness."""
    selection = snapshot['selection']
    with pymupdf.open(SOURCE) as document:
        chars = [c for span in document[PAGE - 1].get_texttrace() for c in span['chars']]
        embedded = document.extract_font(snapshot['styles'][0]['font_xref'])[3]
    with TTFont(BytesIO(embedded)) as original, TTFont(provider['path'], fontNumber=provider['font_index']) as full:
        def shape(font, name):
            coordinates, ends, flags = font['glyf'][name].getCoordinates(font['glyf'])
            upem = font['head'].unitsPerEm
            return ([tuple(Fraction(v) / upem for v in point) for point in coordinates], list(ends), list(flags))
        cmap = full.getBestCmap()
        for index in selection['glyph_ids']:
            codepoint, gid, _, _ = chars[index]
            assert shape(original, original.getGlyphName(gid)) == shape(full, cmap[codepoint]), 'provider outline differs'
        return dict(source_postscript_name=original['name'].getDebugName(6),
                    source_version=original['name'].getDebugName(5),
                    checked_glyphs=len(selection['glyph_ids']),
                    normalized_outline_coordinates_contours_flags='exactly equal',
                    provider_sha256=provider['sha256'], font_index=provider['font_index'])


def audit(source, output, bounds):
    """Independent fixed-content text/paint/image and 144 dpi raster comparison."""
    with pymupdf.open(source) as before, pymupdf.open(output) as after:
        assert len(before) == len(after)
        for i in range(len(before)):
            a, b = before[i], after[i]
            ap, bp = a.get_pixmap(dpi=144), b.get_pixmap(dpi=144)
            assert (ap.width, ap.height, ap.n) == (bp.width, bp.height, bp.n)
            av = Image.frombytes('RGB', (ap.width, ap.height), ap.samples)
            bv = Image.frombytes('RGB', (bp.width, bp.height), bp.samples)
            difference = ImageChops.difference(av, bv)
            region = pymupdf.Rect(bounds) if i == PAGE - 1 else pymupdf.Rect()
            if i == PAGE - 1:
                r = (region * pymupdf.Matrix(2, 2)).irect
                ImageDraw.Draw(difference).rectangle((max(0, r.x0), max(0, r.y0), r.x1 - 1, r.y1 - 1), fill=(0, 0, 0))
            assert difference.getbbox() is None, 'pixels changed outside the accepted region'
            def foreign(page):
                return [(c[0], tuple(c[2]), tuple(c[3])) for span in page.get_texttrace() for c in span['chars']
                        if not region.contains(pymupdf.Point(c[2]))]
            assert foreign(a) == foreign(b), 'foreign text changed'
            drawings = lambda page: [{k: v for k, v in d.items() if k != 'seqno'} for d in page.get_drawings()]
            assert drawings(a) == drawings(b), 'fixed drawings changed'
            images = lambda page: [{k: v for k, v in image.items() if k not in ('number', 'xref')}
                                   for image in page.get_image_info(hashes=True)]
            assert images(a) == images(b), 'images changed'
    # This real source is untagged. Never manufacture, remove or repair a tree.
    for path in (source, output):
        reader = PdfReader(path)
        assert '/StructTreeRoot' not in reader.trailer['/Root']
        assert all('/StructParents' not in page for page in reader.pages)
        for page_number in range(1, len(reader.pages) + 1):
            marked = observe_marked_content(path, page_number)
            assert marked['complete'] and marked['scopes'] == [], 'unexpected marked-content scope'
    return dict(mupdf_outside_changed_pixels=0, other_pages_pixels_equal=True,
                foreign_text_equal=True, drawings_equal=True, images_equal=True,
                structure='untagged before and after; no structure fabricated',
                all_pages_marked_content_complete=True, marked_scopes=0, mcids=0)


@proof_session
def run(output):
    output.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SHA, 'published original differs'
    observed = inspect_selection_source(SOURCE, PAGE)
    line = next(line for line in observed['lines'] if line['id'] == TARGET[0])
    assert line['text'] == TEXT
    snapshot = inspect_paragraph(SOURCE, make_selection(SOURCE, PAGE, line_ids=TARGET))
    fonts = [p for p in FONT_PATHS if p.exists()]
    whole = propose_page_flow(SOURCE, PAGE, font_candidates=fonts)
    write(output / 'whole-page-proposal.json', whole)
    proposal = propose_page_flow(SOURCE, PAGE, font_candidates=fonts, line_ids=TARGET)
    write(output / 'targeted-proposal.json', proposal)
    choices = {}
    outline_evidence = []
    for style in proposal['styles']:
        wanted = 'arial.ttf' if style['source_font']['name'] == 'Arial' else 'msgothic.ttc'
        matching = [c for c in style['providers']['verified']
                    if Path(c['path']).name == wanted and c['font_index'] == 0]
        assert len(matching) == 1, 'required face is not independently metric-qualified'
        choices[style['id']] = {k: matching[0][k] for k in ('sha256', 'font_index')}
        outline_evidence.append(provider_outline_evidence(snapshot, matching[0]))
    result = dict(verdict='REAL-WORLD PDF EDITING NOT YET VALIDATED', platform=platform.platform(),
        python=platform.python_version(), source=dict(url=URL, sha256=SHA, file=SOURCE.name, page=PAGE),
        target=dict(line_ids=TARGET, text=TEXT), original_sha_unchanged=False,
        whole_page_first_refusal=whole['refusals'][0], targeted_status=proposal['status'],
        provider_choices=choices, provider_outline_evidence=outline_evidence, spacing=snapshot['spacing'], stages=[],
        accepted_region=proposal['region'],
        no_external_pdf_or_font_committed=True)
    write(output / 'summary.json', result)
    try:
        state = accept_page_flow(SOURCE, proposal, provider_choices=choices)
    except PdfError as exc:
        result['remaining_blocker'] = str(exc)
        result['stages'] = ['propose_page_flow completed', 'accept_page_flow refused',
                            'replace/edit/reopen/second edit not reached']
    else:
        previous, expected = SOURCE, TEXT
        for index, edit in enumerate(EDITS, 1):
            pdf, sidecar = output / f'rev{index}.pdf', output / f'rev{index}.json'
            edit_shared_flow(previous, state, pdf, sidecar, replace_in_flow(state, *edit))
            reopened = open_shared_flow(pdf, json.loads(sidecar.read_text(encoding='utf-8')))
            assert reopened['status'] == 'restored', reopened.get('reason')
            expected = expected.replace(*edit)
            with pymupdf.open(pdf) as document:
                assert expected.strip() in document[PAGE - 1].get_text(), 'edited Unicode differs'
            checks = audit(SOURCE, pdf, proposal['region']['bounds'])
            result['stages'].append(dict(revision=index, status='restored', text=expected, line_count=1, audit=checks))
            previous, state = pdf, reopened['state']
        wrap_edit = replace_in_flow(state, expected.strip(), expected.strip() * 3)
        try:
            plan_shared_flow(previous, state, wrap_edit)
        except PdfError as exc:
            result['optional_wrap'] = dict(status='refused', reason=str(exc), outputs_written=False)
        else:
            pdf, sidecar = output / 'wrapped.pdf', output / 'wrapped.json'
            edit_shared_flow(previous, state, pdf, sidecar, wrap_edit)
            restored = open_shared_flow(pdf, json.loads(sidecar.read_text(encoding='utf-8')))
            assert restored['status'] == 'restored'
            with pymupdf.open(pdf) as document:
                region = pymupdf.Rect(proposal['region']['bounds'])
                lines = [line for block in document[PAGE - 1].get_text('dict')['blocks'] for line in block.get('lines', [])
                         if region.contains(pymupdf.Point(line['spans'][0]['origin']))]
                actual = ''.join(span['text'] for line in lines for span in line['spans'])
                assert actual.strip() == expected.strip() * 3 and len(lines) > 1
            result['optional_wrap'] = dict(status='restored', text=actual, line_count=len(lines),
                                           audit=audit(SOURCE, pdf, proposal['region']['bounds']))
        result['verdict'] = 'FIRST REAL-WORLD JAPANESE PDF EDITING VALIDATED — PASS'
    result['original_sha_unchanged'] = source_sha(SOURCE) == SHA
    assert result['original_sha_unchanged']
    write(output / 'summary.json', result)
    print(result['verdict'], flush=True)
    print(result.get('remaining_blocker', ''), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output)
