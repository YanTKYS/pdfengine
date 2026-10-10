"""Source Tm gaps remain observations, never a guessed justify policy."""
from copy import deepcopy
from io import BytesIO

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
import pymupdf
import pytest

from pdfeditor.attributed import digest, inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow, replace_in_flow
from pdfeditor.selection import source_sha, make_selection
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow, plan_shared_flow
import page_flow_fixture as fx
from test_page_proposal import pixels


BODY = '申請書を提出してください。 なお、期限を確認。'
FOLLOWING = '必要書類を提出してください。'


def space_font():
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(['.notdef', 'space'])
    builder.setupCharacterMap({32: 'space'})
    builder.setupGlyf({n: TTGlyphPen(None).glyph() for n in ('.notdef', 'space')})
    builder.setupHorizontalMetrics({n: (280, 0) for n in ('.notdef', 'space')})
    builder.setupHorizontalHeader(ascent=880, descent=-120)
    builder.setupNameTable(dict(familyName='FixtureSpace', styleName='Regular',
                               fullName='FixtureSpace', psName='FixtureSpace'))
    builder.setupOS2(sTypoAscender=880, sTypoDescender=-120, usWinAscent=880, usWinDescent=120, fsType=0)
    builder.setupPost(); builder.setupMaxp()
    out = BytesIO(); builder.save(out)
    return out.getvalue()


@pytest.fixture(scope='module')
def body(tmp_path_factory):
    root = tmp_path_factory.mktemp('source-adjacency')
    font = TTFont(BytesIO(fx.build_font()))
    # Punctuation ink is narrow; compressing its full nominal advance must
    # not turn a synthetic full-width rectangle into an overlap fixture.
    for char in '。、':
        pen = TTGlyphPen(None)
        pen.moveTo((60, 0)); pen.lineTo((220, 0)); pen.lineTo((220, 160)); pen.lineTo((60, 160)); pen.closePath()
        font['glyf'][font.getBestCmap()[ord(char)]] = pen.glyph()
    primary = root / 'body.ttf'; font.save(primary)
    other = root / 'space.ttf'; other.write_bytes(space_font())
    source = root / 'source.pdf'
    document = pymupdf.open(); page = document.new_page(width=fx.PAGE[0], height=fx.PAGE[1])
    page.insert_font(fontname='F1', fontbuffer=primary.read_bytes())
    page.insert_font(fontname='F2', fontbuffer=other.read_bytes())
    page.insert_text((85, 120), fx.TITLE, fontname='F1')
    stream = page.get_contents()[0]
    cmap = font.getBestCmap()
    content = []
    for text, y in ((BODY, 240), (FOLLOWING, 280)):
        x = 85.
        for i, char in enumerate(text):
            alias = 'F2' if char == ' ' else 'F1'
            gid = 1 if char == ' ' else font.getGlyphID(cmap[ord(char)])
            content.append(f'BT /{alias} 10.5 Tf 0 Tc 0 Tw 1 0 0 1 {x:.5f} {fx.PAGE[1]-y:.5f} Tm <{gid:04X}> Tj ET')
            x += (2.28 if char == ' ' else 5.1 if char in '。、' else 10.38 if i % 3 == 2 else 10.5)
    content.append('q .5 g 50 60 480 1 re f Q')
    document.update_stream(stream, '\n'.join(content).encode())
    document.save(source); document.close()
    proposal = propose_page_flow(source, font_candidates=[primary, other])
    assert proposal['status'] == 'proposed', proposal['refusals']
    assert len(proposal['paragraphs']) == 2
    return source, proposal


def test_noedit_reproduces_every_glyph_without_justify(body, tmp_path):
    source, proposal = body
    state = accept_page_flow(source, proposal)
    snap = inspect_paragraph(source, make_selection(source, line_ids=['p1-l1']))
    assert snap['spacing']['lines'][0]['distribution'] == 'irregular'
    assert snap['spacing']['lines'][0]['tc'] == [0.0]
    assert snap['spacing']['lines'][0]['tw'] == [0.0]
    assert any(g['reposition'] < -5 for g in snap['spacing']['lines'][0]['gaps'])
    out, sidecar = tmp_path / 'noop.pdf', tmp_path / 'noop.json'
    report = edit_shared_flow(source, state, out, sidecar, {})
    assert open_shared_flow(out, sidecar)['status'] == 'restored'
    assert pixels(source, (0, 0, *fx.PAGE)) == pixels(out, (0, 0, *fx.PAGE))
    assert all(s['report']['provided_font_glyph_count'] == 0 for s in report['steps'])


def test_two_edits_wrap_push_following_and_preserve_foreign(body, tmp_path):
    source, proposal = body
    original_sha = source_sha(source)
    state = accept_page_flow(source, proposal)
    before = source
    old_second = proposal['paragraphs'][1]['first_baseline']
    expected = BODY
    for index, edit in enumerate((fx.FIRST_EDIT, fx.SECOND_EDIT), 1):
        out, sidecar = tmp_path / f'rev{index}.pdf', tmp_path / f'rev{index}.json'
        report = edit_shared_flow(before, state, out, sidecar, replace_in_flow(state, *edit, paragraph_id='P1'))
        opened = open_shared_flow(out, sidecar)
        assert opened['status'] == 'restored', opened.get('reason')
        expected = expected.replace(*edit)
        assert opened['state']['paragraphs']['P1']['logical']['text'] == expected
        assert opened['state']['paragraphs']['P2']['logical']['text'] == FOLLOWING
        assert report['steps'][0]['report']['retained_glyph_count'] > 0
        assert pixels(out, (0, 770, fx.PAGE[0], fx.PAGE[1])) == pixels(source, (0, 770, fx.PAGE[0], fx.PAGE[1]))
        before, state = out, opened['state']
    assert len(report['plan']['fragments']['slot-0']['lines']) == 2
    assert report['plan']['fragments']['slot-1']['lines'][0]['baseline'] > old_second
    assert source_sha(source) == original_sha


def test_repeated_text_disjoint_ranges_do_not_guess_retained_identity(body):
    source, proposal = body
    state = accept_page_flow(source, proposal)
    text = state['paragraphs']['P1']['logical']['text']
    first, last = text.index('を'), text.rindex('を')
    assert first != last
    edits = [dict(start=first, end=first+1, text='を再'),
             dict(start=last, end=last+1, text='の')]
    plan = plan_shared_flow(source, state, {'P1': dict(edits=edits)})
    linked = plan['fragments']['slot-0']['source_edits']
    assert [(e['start'], e['end']) for e in linked] == [(e['start'], e['end']) for e in edits]
    assert [''.join(r['text'] for r in e['runs']) for e in linked] == ['を再', 'の']


def test_overflow_is_atomic_and_does_not_expand_the_region(body, tmp_path):
    source, proposal = body
    state = accept_page_flow(source, proposal, overrides={'region_bottom': 300})
    before = source_sha(source)
    out, sidecar = tmp_path / 'overflow.pdf', tmp_path / 'overflow.json'
    with pytest.raises(PdfError, match='exceed all explicitly confirmed shared regions'):
        edit_shared_flow(source, state, out, sidecar,
                         replace_in_flow(state, '申請書', '申請書'*40, paragraph_id='P1'))
    assert not out.exists() and not sidecar.exists()
    assert source_sha(source) == before


def test_tagged_retention_keeps_the_original_structure(tmp_path):
    from pypdf import PdfReader, PdfWriter
    from test_tagged_page_proposal import verify
    font = fx.write_fonts(tmp_path / 'fonts')['full']
    original = fx.make_page(tmp_path / 'tagged.pdf', font.read_bytes(), tree_backed=True)
    writer = PdfWriter(clone_from=PdfReader(original))
    page = writer.pages[0]
    data = page.get_contents().get_data()
    # The short body paragraph is MCID 3. Split its Tj after three glyphs,
    # adjusting the following Tm, without changing any BDC/EMC or tree node.
    import re
    match = re.search(rb'(/P << /MCID 3 >> BDC.*?)(<[0-9a-fA-F]+>) Tj', data)
    if match is None:
        match = re.search(rb'(/P <</MCID 3>> BDC.*?)(<[0-9a-fA-F]+>) Tj', data)
    assert match
    codes = match.group(2)[1:-1]
    x, baseline, size, _ = fx.layout()[3]
    split = (b'<'+codes[:12]+b'> Tj '+
             f'1 0 0 1 {x+3*size-.12:.5f} {fx.PAGE[1]-baseline:.5f} Tm '.encode()+
             b'<'+codes[12:]+b'>')
    changed = data[:match.start(2)]+split+data[match.end(2):]
    stream = page.get_contents(); stream.set_data(changed); page.replace_contents(stream)
    source = tmp_path / 'positioned-tagged.pdf'; writer.write(source)
    proposal = propose_page_flow(source, font_candidates=[font])
    assert proposal['status'] == 'proposed', proposal['refusals']
    state = accept_page_flow(source, proposal)
    for index, edit in enumerate((fx.FIRST_EDIT, fx.SECOND_EDIT), 1):
        out, sidecar = tmp_path/f'tagged-{index}.pdf', tmp_path/f'tagged-{index}.json'
        edit_shared_flow(source, state, out, sidecar, replace_in_flow(state, *edit))
        verify(out, tmp_path/'positioned-tagged.pdf')
        opened = open_shared_flow(out, sidecar)
        assert opened['status'] == 'restored', opened.get('reason')
        source, state = out, opened['state']


def test_unknown_placement_and_stale_source_fail_closed(body, tmp_path):
    source, proposal = body
    state = accept_page_flow(source, proposal)
    changed = deepcopy(state)
    changed['paragraph_policies']['P1']['text_placement'] = 'justify-guessed'
    from pdfeditor.shared_flow import _contract
    changed['contract_sha256'] = _contract(changed)
    changed.pop('model_sha256'); changed['model_sha256'] = digest(changed)
    assert open_shared_flow(source, changed)['status'] == 'needs_confirmation'
    stale = tmp_path / 'stale.pdf'; stale.write_bytes(source.read_bytes() + b'\n')
    assert open_shared_flow(stale, state)['status'] == 'needs_confirmation'
    altered = deepcopy(proposal)
    del altered['paragraphs'][0]['policy']['text_placement']
    altered.pop('digest'); altered['digest'] = digest(altered)
    with pytest.raises(PdfError, match='stale'):
        accept_page_flow(source, altered)


def test_delete_retype_and_noop_keep_the_confirmed_policy(body, tmp_path):
    source, proposal = body
    state = accept_page_flow(source, proposal)
    requests = [dict(edits=[dict(start=0,end=len(BODY),text='')]),
                dict(edits=[dict(start=0,end=0,text='申請書',style_id='style-1')]), dict(edits=[])]
    for index, request in enumerate(requests):
        out, sidecar = tmp_path/f'empty-{index}.pdf', tmp_path/f'empty-{index}.json'
        edit_shared_flow(source, state, out, sidecar, {'P1': request})
        opened = open_shared_flow(out, sidecar)
        assert opened['status'] == 'restored', opened.get('reason')
        source, state = out, opened['state']
        assert state['paragraph_policies']['P1']['text_placement'] == 'preserve-source-adjacency'
    assert state['paragraphs']['P1']['logical']['text'] == '申請書'


def test_equal_width_does_not_hide_opposite_position_errors(tmp_path):
    font = fx.write_fonts(tmp_path / 'fonts')['full']
    text = fx.PARAGRAPHS[1]
    # Below the observer's classification threshold, but above the existing
    # geometry gate: matching line widths must not let interior drift pass.
    rows = [(85+i*fx.SIZE+(.01 if i == 3 else 0), 240, fx.SIZE, char) for i, char in enumerate(text)]
    source = fx.make_page(tmp_path / 'cancelled.pdf', font.read_bytes(), lines=rows)
    proposal = propose_page_flow(source, font_candidates=[font])
    assert proposal['status'] == 'refused'
    assert any(r['code'] == 'layout-not-reproduced' and 'glyph position' in r['detail'] for r in proposal['refusals'])
