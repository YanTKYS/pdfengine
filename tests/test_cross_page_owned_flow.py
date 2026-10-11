"""Existing source fragments on two pages, including an independently owned follower."""
from copy import deepcopy
from io import BytesIO

import pymupdf
import pytest
from fontTools.ttLib import TTFont
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, NumberObject

from pdfeditor.backend import PdfError
from pdfeditor.owned_flow_proposal import propose_owned_flow, accept_owned_flow_report, accept_owned_flow
from pdfeditor.page_proposal import propose_page_flow, replace_in_flow, _seal
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
import page_flow_fixture as fx

ROW = '申請書を提出してください' * 3
GROUPS = [dict(id='A', parts=[dict(page=1, paragraph_id='P1'), dict(page=2, paragraph_id='P1')]),
          dict(id='B', parts=[dict(page=2, paragraph_id='P2')])]


def make_source(root, *, height=310, intervening=False, rows=None):
    font = TTFont(BytesIO(fx.build_font())); font['head'].unitsPerEm = 2048
    for name in font.getGlyphOrder():
        font['hmtx'][name] = (1001, 0)
    provider = root / 'source.ttf'; font.save(provider)
    raw, source = root / 'raw.pdf', root / 'source.pdf'
    pages = rows or [
            [(ROW[:30], 270, True), (ROW[:32], 291.6, False)],
            [(ROW[:32], 80, False), ('申請期限を確認。', 101.6, False),
             (ROW[:30], 128.1, True), ('必要書類を提出してください。', 148.5, False)]]
    with pymupdf.open() as doc:
        for number, rows in enumerate(pages):
            page = doc.new_page(width=250, height=height)
            page.insert_font(fontname='F1', fontbuffer=provider.read_bytes())
            page.insert_text((40, 240), 'FIXED CONTENT')
            if number == 0 and intervening:
                page.insert_text((40, 308), 'UNSELECTED FOOTNOTE')
            stream = page.get_contents()[0]; parts = []; cmap = font.getBestCmap()
            for text, y, indent in rows:
                x = 50.5 if indent else 40.
                for index, char in enumerate(text):
                    gid = font.getGlyphID(cmap[ord(char)])
                    parts.append(f'BT /F1 10.5 Tf 1 0 0 1 {x:.7f} {height-y:.7f} Tm <{gid:04x}> Tj ET')
                    x += 10.5 * 489 / 1000 - (.12 if index % 3 == 2 else 0)
            doc.update_stream(stream, doc.xref_stream(stream) + '\n'.join(parts).encode())
        doc.save(raw)
    writer = PdfWriter(clone_from=PdfReader(raw))
    for page in writer.pages:
        face = page['/Resources']['/Font']['/F1']['/DescendantFonts'][0].get_object()
        face[NameObject('/W')] = ArrayObject([NumberObject(0), ArrayObject([NumberObject(489)] * len(font.getGlyphOrder()))])
    writer.write(source)
    return source, provider


def page_proposals(source, provider):
    pages = [propose_page_flow(source, n, font_candidates=[provider], line_ids=[f'p{n}-l{i}' for i in ids])
             for n, ids in [(1, (2, 3)), (2, (1, 2, 3, 4))]]
    for p in pages:
        assert p['status'] == 'proposed', (p['refusals'], p['unresolved'])
    return pages


@pytest.fixture(scope='module')
def cross_page(tmp_path_factory):
    source, provider = make_source(tmp_path_factory.mktemp('cross-page-owned'))
    pages = page_proposals(source, provider)
    proposal = propose_owned_flow(source, pages, paragraphs=GROUPS,
        relationship='A continues across the page break; B is the following application paragraph.')
    accepted = accept_owned_flow_report(source, proposal)
    return source, proposal, accepted


def test_noedit_proves_both_pages_and_each_source_fragment(cross_page, tmp_path):
    source, proposal, accepted = cross_page
    assert [p['binding']['page'] for p in accepted['receipt']['pages']] == [1, 2]
    assert accepted['receipt']['reproduction']['lines'] == 6
    state = accepted['state']
    assert [s['paragraph_id'] for s in state['slots'].values()] == ['A', 'A', 'B']
    pdf, model = tmp_path / 'noop.pdf', tmp_path / 'noop.json'
    report = edit_shared_flow(source, state, pdf, model, {})
    assert open_shared_flow(pdf, model)['status'] == 'restored'
    assert all(s['report']['provided_font_glyph_count'] == 0 for s in report['steps'])
    with pymupdf.open(source) as a, pymupdf.open(pdf) as b:
        assert [p.get_pixmap().samples for p in a] == [p.get_pixmap().samples for p in b]


def test_grow_second_edit_shorten_regrow_and_current_ownership(cross_page, tmp_path):
    original, _, accepted = cross_page
    state = accepted['state']; source = original; sha = source_sha(original)
    follower = state['paragraphs']['B']['logical']['text']
    longer = '期限' + ROW
    stages = [('期限', longer), ('確認', '再確認'), (longer, '期限'), ('期限', longer)]
    for index, change in enumerate(stages):
        pdf, model = tmp_path / f'{index}.pdf', tmp_path / f'{index}.json'
        report = edit_shared_flow(source, state, pdf, model,
            replace_in_flow(state, *change, paragraph_id='A', style_id='page-2:style-1'))
        opened = open_shared_flow(pdf, model); assert opened['status'] == 'restored'
        state = opened['state']; assert state['paragraphs']['B']['logical']['text'] == follower
        fragments = report['plan']['fragments']; a, b = fragments['slot-1'], fragments['slot-2']
        if index != 2:
            assert len(a['lines']) > 2 and b['lines'][0]['baseline'] > 128.1
        assert b['lines'][0]['baseline'] - a['lines'][-1]['baseline'] == pytest.approx(26.5, abs=.002)
        with pymupdf.open(original) as x, pymupdf.open(pdf) as y:
            for first, second in zip(x, y):
                assert first.get_pixmap(clip=pymupdf.Rect(35, 230, 200, 250)).samples == second.get_pixmap(clip=pymupdf.Rect(35, 230, 200, 250)).samples
        source = pdf
    with pytest.raises(PdfError):
        edit_shared_flow(source, accepted['state'], tmp_path / 'stale.pdf', tmp_path / 'stale.json', {})
    assert source_sha(original) == sha and not (tmp_path / 'stale.pdf').exists()


@pytest.mark.parametrize('kind', ['duplicate', 'omitted', 'reversed', 'same-page-merge', 'unknown'])
def test_invalid_source_grouping_refuses(cross_page, kind):
    source, proposal, _ = cross_page
    groups = deepcopy(GROUPS)
    if kind == 'duplicate': groups[0]['parts'].append(groups[0]['parts'][0])
    elif kind == 'omitted': groups.pop()
    elif kind == 'reversed': groups.reverse()
    elif kind == 'same-page-merge': groups[0]['parts'] += groups.pop()['parts']
    else: groups[1]['parts'][0]['paragraph_id'] = 'unknown'
    with pytest.raises(PdfError):
        propose_owned_flow(source, proposal['pages'], paragraphs=groups, relationship='Explicit selection')


def test_modified_binding_and_overflow_refuse_atomically(cross_page, tmp_path):
    source, proposal, accepted = cross_page
    modified = deepcopy(proposal); modified['pages'][1]['binding']['page'] = 1
    with pytest.raises(PdfError, match='modified'):
        accept_owned_flow(source, modified)
    with pytest.raises(PdfError):
        accept_owned_flow(source, _seal(modified))
    pdf, model = tmp_path / 'overflow.pdf', tmp_path / 'overflow.json'
    with pytest.raises(PdfError, match='exceed all explicitly confirmed shared regions'):
        edit_shared_flow(source, accepted['state'], pdf, model,
            replace_in_flow(accepted['state'], '期限', ROW * 6, paragraph_id='A', style_id='page-2:style-1'))
    assert not pdf.exists() and not model.exists()


@pytest.mark.parametrize('kind', ['extra-capacity', 'intervening-text'])
def test_join_needs_joint_reproduction_and_uninterrupted_seam(tmp_path, kind):
    source, provider = make_source(tmp_path, height=320 if kind == 'extra-capacity' else 310,
                                   intervening=kind == 'intervening-text')
    pages = page_proposals(source, provider)
    proposal = propose_owned_flow(source, pages, paragraphs=GROUPS, relationship='Caller-selected continuation')
    # Both independent B1 proofs pass. The joint allocator must not silently
    # pull original page-2 glyphs up, or skip a foreign footnote at the seam.
    reason = 'joint no-edit reproduction' if kind == 'extra-capacity' else 'seam skips unselected text'
    with pytest.raises(PdfError, match=reason):
        accept_owned_flow(source, proposal)


def test_stale_pdf_and_unknown_page_choices_refuse(cross_page, tmp_path):
    source, proposal, _ = cross_page
    changed = tmp_path / 'revision.pdf'
    writer = PdfWriter(clone_from=PdfReader(source)); writer.add_metadata({'/Title': 'new revision'}); writer.write(changed)
    with pytest.raises(PdfError, match='another PDF revision'):
        accept_owned_flow(changed, proposal)
    with pytest.raises(PdfError, match='selected page numbers'):
        accept_owned_flow(source, proposal, provider_choices={'3': {}})


def test_separate_paragraphs_do_not_invent_an_unobserved_page_gap(cross_page):
    source, proposal, _ = cross_page
    groups = [dict(id='A1', parts=[GROUPS[0]['parts'][0]]),
              dict(id='A2', parts=[GROUPS[0]['parts'][1]]), GROUPS[1]]
    p = propose_owned_flow(source, proposal['pages'], paragraphs=groups, relationship='Related paragraphs')
    with pytest.raises(PdfError, match='paragraph gap across a page break is unobserved'):
        accept_owned_flow(source, p)


def test_unselected_page_two_follower_remains_foreign(cross_page, tmp_path):
    source, proposal, _ = cross_page
    font = proposal['pages'][1]['font_search']['font_candidates']
    page_two = propose_page_flow(source, 2, font_candidates=font, line_ids=['p2-l1', 'p2-l2'])
    assert page_two['status'] == 'proposed'
    p = propose_owned_flow(source, [proposal['pages'][0], page_two], paragraphs=GROUPS[:1],
                           relationship='Only the cross-page paragraph is accepted')
    state = accept_owned_flow(source, p)
    assert len(state['slots']) == 2
    pdf, model = tmp_path / 'foreign.pdf', tmp_path / 'foreign.json'
    with pytest.raises(PdfError, match='exceed all explicitly confirmed shared regions'):
        edit_shared_flow(source, state, pdf, model,
            replace_in_flow(state, '期限', '期限' + ROW, paragraph_id='A', style_id='page-2:style-1'))
    assert not pdf.exists() and not model.exists()
