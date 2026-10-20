"""Edit intent (glyph selection + replacement) -> adjacent-Span proposal, kept apart from acceptance."""
from copy import deepcopy
import json

import pymupdf
import pytest
from pypdf import PdfReader

from pdfeditor.adjacent_intent import accept_adjacent_intent, edit_adjacent_intent, propose_adjacent_intent
from pdfeditor.adjacent_span import (accept_adjacent_span, edit_adjacent_span, open_adjacent_span,
                                     propose_adjacent_span)
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.selection import make_selection
from pdfeditor.span_edit import _seal
import test_adjacent_span as adj

# The other paragraph is right-aligned to a 270pt column; line one wraps there.
COLUMN = dict(other='提出していただきます', other_x=165)


def source(root, name='source.pdf', **options):
    return adj.make_source(root / name, **dict(COLUMN, **options))


def occurrence(path, mcid, text):
    """Glyph IDs of one concrete occurrence inside one marked-content item (a UI selection)."""
    ids = adj.ids_of(path, mcid)
    content = ContentPage(path, 1)
    try:
        drawn = ''.join(content.actual[i]['unicode'] for i in ids)
    finally:
        content.close()
    start = drawn.index(text)
    return make_selection(path, 1, glyph_ids=ids[start:start + len(text)])


def intent(path, root, text='について', replacement='につきまして', mcid=1):
    return propose_adjacent_intent(path, occurrence(path, mcid, text), replacement, work_dir=root / 'work')


def actual_texts(path):
    reader = PdfReader(path)
    parent = reader.trailer['/Root']['/StructTreeRoot']['/K'][0].get_object()['/K'][0].get_object()
    return [c.get_object().get('/ActualText') for c in parent['/K']]


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp('adjacent-intent')
    path = source(root)
    return path, root, intent(path, root)


def test_proposal_derives_owners_boundary_follower_rectangles_and_font(world):
    path, root, proposal = world
    assert proposal['status'] == 'proposed' and proposal['adjacent']['status'] == 'proposed'
    assert proposal['selected_text'] == 'について' and proposal['replacement'] == 'につきまして'
    assert [proposal['owners'][r]['mcid'] for r in ('first', 'second')] == [1, 2]
    assert proposal['follower']['approved'] and proposal['follower']['mcid'] == 3
    assert proposal['original_boundary'] == len(adj.FIRST)
    chosen = proposal['candidates'][proposal['chosen']]
    assert chosen['boundary'] == adj.BOUNDARY and chosen['relocated'] == 'を行' and chosen['natural_break']
    assert [c['status'] for c in proposal['candidates']] == ['refused', 'refused', 'qualified']
    assert chosen['follower_dx'] == pytest.approx(21)
    evidence = proposal['regions']['evidence']
    assert evidence['column_right'] == pytest.approx(270) and evidence['column_witness']['unicode'] == 'す'
    first, second = proposal['regions']['owners']
    assert first['x1'] == second['x1'] == pytest.approx(270) and first['y1'] <= second['y0']
    assert proposal['font']['provider']['sha256'] == proposal['font']['program_sha256']
    # Proposing never writes the PDF and never confirms the adjacent state.
    assert adj.ids_of(path, 1) and not list(root.glob('*.json'))


def test_accepted_intent_matches_manual_edit_and_noop(world, tmp_path):
    path, root, proposal = world
    acceptance = accept_adjacent_intent(path, proposal, work_dir=root / 'work')
    pdf, side = tmp_path / 'intent.pdf', tmp_path / 'intent.json'
    result = edit_adjacent_intent(path, acceptance, pdf, side, work_dir=root / 'work')
    assert result['after'] == ['本年度の補助事業につきまして募集', 'を行います。']
    opened = open_adjacent_span(pdf, json.loads(side.read_text()))
    assert opened['status'] == 'restored'
    assert actual_texts(pdf) == [adj.LEAD, '本年度の補助事業につきまして募集', 'を行います。', ' ']
    assert opened['state']['witness']['structure'] == proposal['adjacent']['witness']['structure']
    # The manual adjacent-Span contract with caller rectangles gives the same page.
    manual = accept_adjacent_span(path, propose_adjacent_span(path, *adj.owners(path), follower=adj.follower(path)))
    recipe = proposal['font']['provider']
    edit_adjacent_span(path, manual, tmp_path / 'manual.pdf', tmp_path / 'manual.json', adj.EDIT,
                       boundary=adj.BOUNDARY, provider=recipe)
    contents = [PdfReader(p).pages[0].get_contents().get_data() for p in (pdf, tmp_path / 'manual.pdf')]
    assert contents[0] == contents[1] and actual_texts(tmp_path / 'manual.pdf') == actual_texts(pdf)
    assert adj.raster(pdf) == adj.raster(tmp_path / 'manual.pdf')
    noop, noop_side = tmp_path / 'noop.pdf', tmp_path / 'noop.json'
    edit_adjacent_span(pdf, opened['state'], noop, noop_side)
    assert open_adjacent_span(noop, json.loads(noop_side.read_text()))['status'] == 'restored'
    assert adj.raster(pdf) == adj.raster(noop)


def test_ambiguous_boundaries_need_an_explicit_choice(tmp_path):
    path = source(tmp_path, first='本年度の補助事業について募集ab')
    proposal = intent(path, tmp_path, replacement='についての')
    assert proposal['status'] == 'ambiguous' and proposal['chosen'] is None
    options = [proposal['candidates'][i] for i in proposal['options']]
    assert [(o['relocated'], o['natural_break']) for o in options] == [('b', False), ('ab', True)]
    with pytest.raises(PdfError, match='choose one explicitly'):
        accept_adjacent_intent(path, proposal, work_dir=tmp_path / 'work')
    acceptance = accept_adjacent_intent(path, proposal, work_dir=tmp_path / 'work', option=proposal['options'][1])
    result = edit_adjacent_intent(path, acceptance, tmp_path / 'out.pdf', tmp_path / 'out.json', work_dir=tmp_path / 'work')
    assert result['after'] == ['本年度の補助事業についての募集', 'ab' + adj.SECOND]


@pytest.mark.parametrize('kind,match', [('non-contiguous', 'contiguous'), ('cross-owner', 'crosses owners'),
                                        ('no-following-owner', 'no following owner'), ('head-of-owner', 'inside the owner')])
def test_selection_refusals(world, kind, match):
    path, root, _ = world
    first = adj.ids_of(path, 1)
    if kind == 'non-contiguous':
        # Two occurrences of the same glyph run are never one selection.
        ids = [first[0], first[8]]
    elif kind == 'cross-owner':
        ids = first[-2:] + adj.ids_of(path, 2)[:1]
    elif kind == 'no-following-owner':
        ids = adj.ids_of(path, 4)[1:3]
    else:
        ids = first[:2]
    with pytest.raises(PdfError, match=match):
        propose_adjacent_intent(path, make_selection(path, 1, glyph_ids=ids), 'の', work_dir=root / 'work')


def test_repeated_display_text_is_resolved_by_the_selected_occurrence_only(tmp_path):
    path = source(tmp_path, first='本年度の補助事業について募集をの')
    ids = adj.ids_of(path, 1)
    content = ContentPage(path, 1)
    try:
        drawn = [content.actual[i]['unicode'] for i in ids]
    finally:
        content.close()
    occurrences = [i for i, c in enumerate(drawn) if c == 'の']
    assert len(occurrences) == 2
    with pytest.raises(PdfError, match='contiguous'):
        propose_adjacent_intent(path, make_selection(path, 1, glyph_ids=[ids[i] for i in occurrences]), 'が',
                                work_dir=tmp_path / 'work')


def test_nearby_unrelated_glyph_is_neither_paired_nor_overrun(tmp_path):
    path = source(tmp_path, foreign=115)
    with pytest.raises(PdfError, match='no boundary qualifies.*follower leaves its approved rectangle'):
        intent(path, tmp_path)


@pytest.mark.parametrize('kind', ['actualtext-mismatch', 'bad-parenttree', 'bad-backlink'])
def test_structure_refusals(tmp_path, kind):
    path = adj.mutate(source(tmp_path), tmp_path / (kind + '.pdf'), kind)
    with pytest.raises(PdfError):
        intent(path, tmp_path)


def test_ineligible_follower_is_not_moved_and_capacity_refuses(tmp_path):
    path = adj.mutate(source(tmp_path), tmp_path / 'alt.pdf', 'follower-lang-owner')
    with pytest.raises(PdfError, match='no boundary qualifies'):
        intent(path, tmp_path)


@pytest.mark.parametrize('replacement,match', [('につきまして' * 3, 'no boundary qualifies'),
                                               ('につき髙して', 'lacks')])
def test_capacity_and_font_refusals(world, replacement, match):
    path, root, _ = world
    with pytest.raises(PdfError, match=match):
        intent(path, root, replacement=replacement)


def test_stale_tampered_and_unaccepted_intents_publish_nothing(world, tmp_path):
    path, root, proposal = world
    work = root / 'work'
    out, side = tmp_path / 'bad.pdf', tmp_path / 'bad.json'
    with pytest.raises(PdfError, match='acceptance'):
        edit_adjacent_intent(path, proposal, out, side, work_dir=work)
    forged = deepcopy(proposal); forged['candidates'][forged['chosen']]['boundary'] += 1
    with pytest.raises(PdfError, match='unmodified'):
        accept_adjacent_intent(path, forged, work_dir=work)
    with pytest.raises(PdfError, match='stale or was modified'):
        accept_adjacent_intent(path, _seal(forged), work_dir=work)
    widened = deepcopy(proposal); widened['regions']['owners'][0]['x1'] = 400
    with pytest.raises(PdfError, match='stale or was modified'):
        accept_adjacent_intent(path, _seal(widened), work_dir=work)
    acceptance = accept_adjacent_intent(path, proposal, work_dir=work)
    moved = deepcopy(acceptance); moved['boundary'] -= 1
    with pytest.raises(PdfError, match='differs from its proposal'):
        edit_adjacent_intent(path, _seal(moved), out, side, work_dir=work)
    assert not out.exists() and not side.exists()
    # A different revision: the selection itself is stale.
    edited = tmp_path / 'edited.pdf'
    edit_adjacent_intent(path, acceptance, edited, tmp_path / 'edited.json', work_dir=work)
    with pytest.raises(PdfError):
        accept_adjacent_intent(edited, proposal, work_dir=work)
    with pytest.raises(PdfError):
        edit_adjacent_intent(edited, acceptance, out, side, work_dir=work)
    assert not out.exists() and not side.exists()
