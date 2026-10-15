"""Explicit current-revision margin witnesses never turn fixed paint into owners."""
from copy import deepcopy
import json
import shutil
import subprocess
import warnings

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.continuation import slot_id
from pdfeditor.owned_flow_proposal import propose_owned_flow_extension, accept_owned_flow_report
from pdfeditor.page_proposal import _Analysis, _seal, accept_page_flow, propose_page_flow, replace_in_flow
from pdfeditor.post_edit_extension import inspect_fixed_foreign, _fixed_foreign
from pdfeditor.selection import source_sha
from pdfeditor.source_ownership import immutable
from test_cross_page_owned_flow import make_source, ROW
from test_next_page_extension import LONG, exterior_equal
from test_sequential_extension import page_request, pixel_identical, save, kept, refused


def margins(source, page, classification):
    lines = _Analysis(source, page).lines
    header = next(l for l in lines if l['text'] == '募集要項')
    footer = next(l for l in lines if l['text'] == '－１－')
    line = header if classification == 'header' else footer
    return inspect_fixed_foreign(source, page=page, line_ids=[line['id']], classification=classification,
        body_bounds=[0, header['bbox'][3], 250, footer['bbox'][1]])


def next_request(source, number=2, before='P1', after='B', bottom=125):
    request = page_request(number, before, after, f'{before}-next', region_bottom=bottom)
    request['fixed_foreign'] = [margins(source, number - 1, 'footer'), margins(source, number, 'header')]
    return request


def proposal(source, base, page, request):
    number = page['binding']['page']
    after = request['page_break']['after']
    return propose_owned_flow_extension(source, base, [page],
        paragraphs=[dict(id=after, parts=[dict(page=number, paragraph_id='P1')])],
        relationship='The selected instructions continue on the next page; the margins remain fixed.',
        next_page=request)


@pytest.fixture(scope='module')
def margin_case(tmp_path_factory):
    root = tmp_path_factory.mktemp('fixed-margin')
    raw, font = make_source(root, height=340, rows=[
        [(ROW[:30], 285, True), ('申請期限を確認。', 306.6, False)],
        [(ROW[:30], 80, True), ('必要書類を提出してください。', 100.4, False)],
        [(ROW[:30], 80, True), ('書類を確認してください。', 100.4, False)]], fixed_pages=set())
    original = root / 'margins.pdf'
    with pymupdf.open(raw) as doc:
        for page in doc:
            page.insert_text((40, 15), '募集要項', fontname='F1', fontsize=10.5)
            page.insert_text((40, 330), '－１－', fontname='F1', fontsize=10.5)
        doc.save(original)
    first = accept_page_flow(original, propose_page_flow(original, 1, font_candidates=[font],
                                                        line_ids=['p1-l2', 'p1-l3']))
    source, base, _ = save(original, first, root, 'saved',
        replace_in_flow(first, '確認', '再確認', paragraph_id='P1', style_id='style-1'))
    page = propose_page_flow(source, 2, font_candidates=[font], line_ids=['p2-l2', 'p2-l3'])
    return original, source, base, page, font


@pytest.mark.parametrize('keep,match', [(None, 'after the last'), (0, 'before the additional'), (1, 'after the last')])
def test_default_and_incomplete_margin_proofs_still_refuse(margin_case, keep, match):
    _, source, base, page, _ = margin_case
    request = next_request(source)
    if keep is None:
        del request['fixed_foreign']
    else:
        request['fixed_foreign'] = [request['fixed_foreign'][keep]]
    with pytest.raises(PdfError, match=match):
        proposal(source, base, page, request)


@pytest.mark.parametrize('field', ['source_sha256', 'page_observation_sha256', 'line_id', 'glyph_id', 'origin',
                                  'code', 'font', 'body_bounds', 'classification', 'extra'])
def test_resealed_proof_tampering_is_reobserved(margin_case, field):
    _, source, base, page, _ = margin_case
    request = next_request(source)
    witness = request['fixed_foreign'][0]
    if field in ('source_sha256', 'page_observation_sha256'):
        witness[field] = '0' * 64
    elif field == 'line_id': witness['lines'][0]['id'] = 'p1-l2'
    elif field == 'glyph_id': witness['glyphs'][0]['glyph_id'] += 1
    elif field == 'origin': witness['glyphs'][0]['origin'][0] += .25
    elif field == 'code': witness['paint'][0]['code'] = '00'
    elif field == 'font': witness['paint'][0]['font_resource'] = '/Other'
    elif field == 'body_bounds': witness['body_bounds'] = [0, 200, 250, 250]
    elif field == 'classification': witness['classification'] = 'header'
    else: witness['release_paragraphs'] = ['B']
    # A fresh outer seal is not authority to alter independent observations.
    clean = proposal(source, base, page, next_request(source))
    clean['next_page'] = request
    clean['edited_extension']['review']['page_boundary']['fixed_foreign'] = deepcopy(request['fixed_foreign'])
    with pytest.raises(PdfError, match='fixed foreign'):
        accept_owned_flow_report(source, _seal(clean))


@pytest.mark.parametrize('classification,body', [('header', [0, 40, 250, 320]),
                                                ('footer', [0, 20, 250, 300]),
                                                ('header', [0, 110, 250, 320])])
def test_body_interior_text_cannot_be_classified_as_a_margin(margin_case, classification, body):
    _, source, _, _, _ = margin_case
    # A skipped body line/heading cannot escape by shrinking the body rectangle.
    with pytest.raises(PdfError, match='fixed foreign'):
        inspect_fixed_foreign(source, page=2, line_ids=['p2-l2'], classification=classification, body_bounds=body)


def test_numeric_footer_without_exact_code_observations_still_refuses(tmp_path):
    source = tmp_path / 'unmapped-footer.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page(width=250, height=340)
        # Standard Helvetica has no explicit /Widths, so the content interpreter
        # cannot independently map its character codes. Numeric text earns no exemption.
        page.insert_text((40, 330), '12')
        doc.save(source)
    with pytest.raises(PdfError, match='exact individual source-code observations'):
        inspect_fixed_foreign(source, page=1, line_ids=['p1-l1'], classification='footer',
                              body_bounds=[0, 0, 250, 300])


def test_fixed_margin_cannot_be_owned_or_lose_protection(margin_case):
    _, source, base, page, _ = margin_case
    prior, analysis = _Analysis(source, 1), _Analysis(source, 2)
    witness = margins(source, 2, 'header')
    fixed_ids = set(witness['lines'][0]['glyph_ids'])
    with pytest.raises(PdfError, match='remain unowned'):
        _fixed_foreign(source, base, page, prior, analysis, set(), fixed_ids, [witness])
    witness = margins(source, 1, 'footer')
    tampered = deepcopy(base)
    tampered['pages']['1']['protected_regions'] = []
    with pytest.raises(PdfError, match='protected region'):
        _fixed_foreign(source, tampered, page, prior, analysis, set(), set(), [witness])
    # Even a freshly observed margin assertion cannot shrink confirmed capacity.
    witness['body_bounds'][0] = 41
    with pytest.raises(PdfError, match='contain all confirmed'):
        _fixed_foreign(source, base, page, prior, analysis, set(), set(), [witness])


@pytest.mark.parametrize('geometry', ['destination', 'source-top', 'source-bottom'])
def test_fixed_margin_cannot_be_capacity_or_a_continuation_destination(margin_case, geometry):
    _, source, base, page, _ = margin_case
    request = next_request(source)
    if geometry == 'destination':
        item = request['vacated_continuation']
        item['region']['bounds'][1] = 0  # Covers the independently witnessed fixed header.
        item['confirmation']['bounds'] = item['region']['bounds']
    elif geometry == 'source-top':
        request['page_overrides']['region_top'] = 0
    else:
        request['page_overrides']['region_bottom'] = 335
    reviewed = proposal(source, base, page, request)
    with pytest.raises(PdfError, match='unselected|protected|occupied|collid'):
        accept_owned_flow_report(source, reviewed)


def fixed_paint(source):
    # Compare all code/font/GID/position observations, allowing only source-order
    # IDs to change when owned text is rewritten ahead of these foreign glyphs.
    result = []
    for page in (1, 2, 3):
        for classification in ('header', 'footer'):
            witness = margins(source, page, classification)
            resources = {p['font_resource'].lstrip('/') for p in witness['paint']}
            with pymupdf.open(source) as doc:
                programs = {font[4]: doc.extract_font(font[0])[3] for font in doc[page - 1].get_fonts()
                            if font[4] in resources}
            assert set(programs) == resources and all(programs.values())
            result.append(([{k: v for k, v in g.items() if k != 'source_order'} for g in witness['glyphs']],
                           [{k: v for k, v in p.items() if k != 'glyph_ids'} for p in witness['paint']], programs))
    return result


def noop_pixels_equal(before, after, root):
    """Always check MuPDF; independently check every page with Poppler when installed."""
    assert pixel_identical(before, after)
    renderer = shutil.which('pdftoppm')
    if renderer is None:
        warnings.warn('Poppler unavailable: independent no-op raster check NOT RUN; MuPDF lifecycle still checked',
                      RuntimeWarning, stacklevel=2)
        return
    from PIL import Image
    directory = root / ('raster-' + after.stem)
    directory.mkdir()
    rendered = []
    with pymupdf.open(before) as doc:
        count = len(doc)
    for label, pdf in [('before', before), ('after', after)]:
        subprocess.run([renderer, '-r', '72', '-png', str(pdf), str(directory / label)],
                       check=True, capture_output=True, timeout=60)
        images = sorted(directory.glob(label + '-*.png'))
        assert len(images) == count
        pixels = []
        for path in images:
            with Image.open(path) as image:
                pixels.append((image.size, image.convert('RGB').tobytes()))
        rendered.append(pixels)
    assert rendered[0] == rendered[1]


def test_two_current_seams_preserve_fixed_paint_through_saved_revisions(margin_case, tmp_path):
    original, source, base, page, font = margin_case
    original_sha = source_sha(original)
    original_paint = fixed_paint(original)
    refused(source, base, tmp_path, 'P1', '期限', LONG)
    first = proposal(source, base, page, next_request(source))
    # The public request is JSON-portable, with no tuple-only comparisons.
    state1 = accept_owned_flow_report(source, json.loads(json.dumps(first)))['state']
    b = next(s for s in state1['slots'] if state1['slots'][s]['paragraph_id'] == 'B')
    first_destination = state1['continuation_destinations']['P1-next']
    first_generated = slot_id(first_destination)
    assert set(first_destination['vacated_space']['owners']) == {b}
    assert state1['pages']['1']['protected_regions'] == base['pages']['1']['protected_regions']
    kept(base, state1)
    noop, noop_state, _ = save(source, state1, tmp_path, 'noop', {})
    noop_pixels_equal(source, noop, tmp_path)
    assert fixed_paint(noop) == original_paint
    grown, grown_state, report = save(noop, noop_state, tmp_path, 'grown',
        replace_in_flow(noop_state, '期限', LONG, paragraph_id='P1', style_id='style-1'))
    assert grown_state['slots'][first_generated]['occupancy'] is not None
    assert report['plan']['fragments'][first_generated]['lines'][0]['baseline'] == 62
    assert report['plan']['fragments'][b]['lines'][0]['baseline'] == 88.5
    assert fixed_paint(grown) == original_paint
    assert exterior_equal(noop, grown, grown_state)
    growth = '必要書類' + ROW[:36]
    refused(grown, grown_state, tmp_path, 'B', '必要書類', growth)
    page3 = propose_page_flow(grown, 3, font_candidates=[font], line_ids=['p3-l2', 'p3-l3'])
    current = next_request(grown, 3, 'B', 'C', bottom=200)
    stale = deepcopy(current)
    stale['fixed_foreign'][0] = margins(source, 2, 'footer')
    with pytest.raises(PdfError, match='current revision'):
        proposal(grown, grown_state, page3, stale)
    second = proposal(grown, grown_state, page3, current)
    state = accept_owned_flow_report(grown, second)['state']
    kept(grown_state, state)
    c = next(s for s in state['slots'] if state['slots'][s]['paragraph_id'] == 'C')
    second_destination = state['continuation_destinations']['B-next']
    second_generated = slot_id(second_destination)
    assert state['regions']['release-3']['page'] == 3
    assert set(state['continuation_destinations']['P1-next']['vacated_space']['owners']) == {b}
    assert set(second_destination['vacated_space']['owners']) == {c}
    assert state['continuation_destinations']['P1-next'] == first_destination
    assert state['source_adoptions'][c]['pdf_sha256'] == source_sha(grown)
    foreign = {page: {gid for role in ('header', 'footer') for line in margins(grown, page, role)['lines']
                     for gid in line['glyph_ids']} for page in (1, 2, 3)}
    for slot in state['slots'].values():
        number = state['regions'][slot['region_id']]['page']
        assert not set(slot['binding']['paragraph']['selection']['glyph_ids']) & foreign[number]
    # The second adoption must reproduce the current PDF before any new growth.
    previous, state, _ = save(grown, state, tmp_path, 'second-extension-noop', {})
    noop_pixels_equal(grown, previous, tmp_path)
    assert fixed_paint(previous) == original_paint
    first_creation = state['slots'][first_generated]['creation_binding']
    second_creation = None
    stages = [('B', '必要書類', growth), ('B', growth, '必要書類'), ('B', '必要書類', growth),
              ('P1', LONG, '期限'), ('P1', '期限', LONG), (None,)]
    for index, stage in enumerate(stages):
        changes = {} if stage[0] is None else replace_in_flow(state, *stage[1:], paragraph_id=stage[0], style_id='style-1')
        pdf, new, report = save(previous, state, tmp_path, f'stage-{index}', changes)
        fragments = report['plan']['fragments']
        if index in (0, 2, 4):
            # B really paints on page 3 and moves C, rather than merely owning unused capacity.
            assert new['slots'][second_generated]['occupancy'] is not None
            assert fragments[second_generated]['lines'][0]['baseline'] == 62
            assert fragments[c]['lines'][0]['baseline'] == 88.5
        if index == 0:
            witness = next(s for s in report['steps'] if s['slot_id'] == second_generated)['report']['vacated_space']
            assert [o['source_owner'] for o in witness['owners']] == [c]
        if index == 1:
            assert new['slots'][second_generated]['occupancy'] is None
            assert fragments[c]['lines'][0]['baseline'] == 80
        if index == 3:
            assert new['slots'][first_generated]['occupancy'] is None
        if index == 4:
            assert new['slots'][first_generated]['occupancy'] is not None
        for sid in ('slot-0', b, c):
            assert immutable(new['slots'][sid]['source_output']) == immutable(state['slots'][sid]['source_output'])
        assert new['slots'][first_generated]['creation_binding'] == first_creation
        second_creation = second_creation or new['slots'][second_generated]['creation_binding']
        assert new['slots'][second_generated]['creation_binding'] == second_creation
        assert new['continuation_destinations'] == state['continuation_destinations']
        assert set(new['continuation_destinations']['P1-next']['vacated_space']['owners']) == {b}
        assert set(new['continuation_destinations']['B-next']['vacated_space']['owners']) == {c}
        assert fixed_paint(pdf) == original_paint
        assert exterior_equal(previous, pdf, new)
        assert new['pages'] == state['pages']
        if not changes: noop_pixels_equal(previous, pdf, tmp_path)
        state, previous = new, pdf
    assert len(state['continuation_destinations']) == 2
    assert source_sha(original) == original_sha
