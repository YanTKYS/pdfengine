"""Headless operation logic of the adjacent-Span GUI PoC (no Tk required)."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest
from pypdf import PdfReader

from examples import adjacent_span_session as gui
from examples.adjacent_span_session import AdjacentEditSession, Refused, review, review_text
from pdfeditor.adjacent_intent import accept_adjacent_intent, edit_adjacent_intent, propose_adjacent_intent
from pdfeditor.backend import PdfError
from pdfeditor.selection import source_sha
from pdfeditor.span_edit import _seal
import test_adjacent_intent as intent
import test_adjacent_span as adj

ZOOM = 1.5


def center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def screen_drag(session, first, last):
    """Press on the first glyph and release on the last one, in screen pixels at ZOOM."""
    points = [AdjacentEditSession.to_screen(center(session.glyphs[i]['bbox']) * 2, ZOOM)[:2] for i in (first, last)]
    return session.select(*(AdjacentEditSession.to_pdf(x, y, ZOOM) for x, y in points))


def occurrence(session, mcid, text):
    ids = adj.ids_of(session.source, mcid)
    drawn = ''.join(session.glyphs[i]['text'] for i in ids)
    start = drawn.index(text)
    return ids[start], ids[start + len(text) - 1]


def outputs(root, name='out'):
    return root / (name + '.pdf'), root / (name + '.json')


@pytest.fixture
def world(tmp_path):
    path = intent.source(tmp_path)
    return path, AdjacentEditSession(path, work_dir=tmp_path / 'work')


def test_screen_points_map_to_glyph_ids(world):
    _, session = world
    for glyph in session.glyphs:
        x, y = AdjacentEditSession.to_screen(center(glyph['bbox']) * 2, ZOOM)[:2]
        assert session.glyph_at(*AdjacentEditSession.to_pdf(x, y, ZOOM)) == glyph['id']


def test_drag_selection_matches_displayed_text_and_is_bound_to_the_revision(world):
    path, session = world
    chosen = screen_drag(session, *occurrence(session, 1, 'について'))
    assert chosen['text'] == 'について' == session.selected_text
    assert session.selection['glyph_ids'] == chosen['glyph_ids']
    assert session.selection['source_sha256'] == source_sha(path)
    # Dragging backwards selects the same content-order run.
    first, last = occurrence(session, 1, 'について')
    assert screen_drag(session, last, first)['glyph_ids'] == chosen['glyph_ids']


def test_ambiguous_or_empty_points_refuse_and_clear_the_selection(tmp_path, world):
    _, session = world
    screen_drag(session, *occurrence(session, 1, 'について'))
    first = session.glyphs[adj.ids_of(session.source, 1)[0]]['bbox']
    with pytest.raises(Refused, match='several glyphs'):
        session.select((first[2], center(first)[1]), (first[2], center(first)[1]))  # shared edge of two glyphs
    assert session.selection is None
    with pytest.raises(Refused, match='no glyph'):
        session.select((5, 5), (5, 5))
    # A glyph drawn twice at the same place is never resolved to one of them.
    duplicate = AdjacentEditSession(intent.source(tmp_path, 'duplicate.pdf', extra=(intent.DATE, (100, 160, '本'))),
                                    work_dir=tmp_path / 'work')
    twins = [g for g in duplicate.glyphs if g['text'] == '本' and g['bbox'][0] == 100]
    assert len(twins) == 2 and twins[0]['bbox'] == twins[1]['bbox']
    point = center(twins[0]['bbox'])
    with pytest.raises(Refused, match='several glyphs'):
        duplicate.select(point, point)


def test_cross_owner_selection_is_refused_by_the_engine(world, tmp_path):
    _, session = world
    first = adj.ids_of(session.source, 1)
    screen_drag(session, first[-2], adj.ids_of(session.source, 2)[1])
    session.set_replacement('につき')
    with pytest.raises(PdfError, match='crosses owners'):
        session.propose()
    assert session.proposal is None


def test_steps_refuse_out_of_order_and_publish_nothing(world, tmp_path):
    _, session = world
    pdf, sidecar = outputs(tmp_path)
    with pytest.raises(Refused, match='accept'):
        session.save(pdf, sidecar)
    with pytest.raises(Refused, match='select'):
        session.propose()
    screen_drag(session, *occurrence(session, 1, 'について'))
    with pytest.raises(Refused, match='replacement'):
        session.propose()
    session.set_replacement('につきまして')
    with pytest.raises(Refused, match='proposal'):
        session.accept()
    session.propose()
    with pytest.raises(Refused, match='accept'):
        session.save(pdf, sidecar)
    session.accept()
    # Any change of the inputs discards the proposal and the acceptance.
    session.set_replacement('につきまして、')
    assert session.proposal is None and session.acceptance is None
    with pytest.raises(Refused, match='accept'):
        session.save(pdf, sidecar)
    assert not pdf.exists() and not sidecar.exists()


def test_refused_proposal_writes_nothing(world, tmp_path):
    _, session = world
    screen_drag(session, *occurrence(session, 1, 'について'))
    session.set_replacement('につきまして' * 3)
    with pytest.raises(PdfError, match='no boundary qualifies'):
        session.propose()
    assert session.proposal is None and not list(tmp_path.glob('*.json'))


def test_stale_and_tampered_states_are_refused(world, tmp_path):
    path, session = world
    pdf, sidecar = outputs(tmp_path)
    screen_drag(session, *occurrence(session, 1, 'について'))
    session.set_replacement('につきまして')
    session.propose()
    forged = deepcopy(session.proposal); forged['regions']['owners'][0]['x1'] = 400
    session.proposal = _seal(forged)
    with pytest.raises(PdfError, match='stale or was modified'):
        session.accept()
    session.set_replacement('につきまして ')
    session.set_replacement('につきまして')
    session.propose(); session.accept()
    moved = deepcopy(session.acceptance); moved['boundary'] -= 1
    session.acceptance = _seal(moved)
    with pytest.raises(PdfError, match='differs from its proposal'):
        session.save(pdf, sidecar)
    assert not pdf.exists() and not sidecar.exists()
    # The PDF changed on disk after the proposal: nothing proceeds.
    session.set_replacement('につきまして ')
    session.set_replacement('につきまして')
    session.propose()
    shutil.copyfile(intent.source(tmp_path, 'other.pdf', extra=()), path)
    with pytest.raises(Refused, match='changed on disk'):
        session.accept()
    assert not pdf.exists()


def test_result_for_changed_inputs_is_discarded(world, monkeypatch):
    _, session = world
    screen_drag(session, *occurrence(session, 1, 'について'))
    session.set_replacement('につきまして')
    real = gui.propose_adjacent_intent

    def edited_meanwhile(*args, **kwargs):
        value = real(*args, **kwargs)
        session.set_replacement('につき')  # the user typed while the worker ran
        return value
    monkeypatch.setattr(gui, 'propose_adjacent_intent', edited_meanwhile)
    with pytest.raises(Refused, match='discarded'):
        session.propose()
    assert session.proposal is None


def test_gui_flow_saves_reopens_and_matches_the_direct_api(world, tmp_path):
    path, session = world
    screen_drag(session, *occurrence(session, 1, 'について'))
    session.set_replacement('につきまして')
    shown = session.propose()
    assert shown == review(session.proposal) and shown['status'] == 'proposed'
    assert shown['first']['mcid'] == 1 and shown['second']['mcid'] == 2 and shown['follower']['approved']
    assert shown['options'][0]['relocated'] == 'を行' and shown['options'][0]['follower_dx'] == pytest.approx(21)
    assert shown['column']['basis'] == 'corroborated line ends' and len(shown['column']['supports']) == 2
    assert {o['kind'] for o in shown['overlays']} == {'first', 'second', 'follower', 'wrap-window', 'support'}
    assert any('裏付けの行末' in line for line in review_text(shown))
    session.accept()
    pdf, sidecar = outputs(tmp_path)
    pdf.write_bytes(b'existing')
    with pytest.raises(Refused, match='already exists'):
        session.save(pdf, sidecar)
    assert pdf.read_bytes() == b'existing'
    pdf.unlink()
    saved = session.save(pdf, sidecar)
    assert saved['reopen'] == 'restored' and saved['after'] == ['本年度の補助事業につきまして募集', 'を行います。']
    assert source_sha(path) == session.sha256
    # The same edit through the APIs directly gives the same PDF and sidecar.
    direct = tmp_path / 'direct'
    proposal = propose_adjacent_intent(path, session.selection, 'につきまして', work_dir=direct / 'work')
    acceptance = accept_adjacent_intent(path, proposal, work_dir=direct / 'work')
    edit_adjacent_intent(path, acceptance, direct / 'out.pdf', direct / 'out.json', work_dir=direct / 'work')
    assert source_sha(direct / 'out.pdf') == saved['output_sha256']
    assert source_sha(direct / 'out.json') == saved['sidecar_sha256']
    assert json.loads(sidecar.read_text())['status'] == 'confirmed'


def test_unsupported_pages_are_refused(tmp_path):
    import pymupdf
    path = intent.source(tmp_path)
    with pymupdf.open(path) as document:
        document[0].set_rotation(90)
        document.save(tmp_path / 'rotated.pdf')
    with pytest.raises(Refused, match='rotated'):
        AdjacentEditSession(tmp_path / 'rotated.pdf')
