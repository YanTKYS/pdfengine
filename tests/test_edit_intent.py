"""Edit-intent normalization and route selection over the tagged-Span contracts."""
from copy import deepcopy
import json
import re

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

import pdfeditor.paragraph as paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.edit_intent import (accept_edit_intent, edit_edit_intent, normalize_edit, open_edit_result,
                                   propose_edit_intent)
from pdfeditor.selection import make_selection
from pdfeditor.span_edit import _seal
import test_adjacent_intent as intent
import test_adjacent_span as adj

# LEAD「申請書」is kerned like Word output and FIRST starts where the kerned
# LEAD ends, so their nominal boxes overlap slightly (as Span59/Span60 do).
KERNED_FIRST_X = 50 + 3 * 10.5 - 11 * 10.5 / 1000 - .004


def kerned_source(tmp_path):
    path = adj.make_source(tmp_path / 'kerned.pdf', extra=((50, 230, '書類'),))
    writer = PdfWriter(PdfReader(path), full=True); page = writer.pages[0]
    data = page.get_contents().get_data()
    data, lead = re.subn(rb'<([0-9A-F]{8})([0-9A-F]{4})> Tj', rb'[<\1>11<\2>] TJ', data, count=1)
    data, first = re.subn(rb'1 0 0 1 100 ', b'1 0 0 1 %.4f ' % KERNED_FIRST_X, data, count=1)
    assert lead == first == 1
    stream = DecodedStreamObject(); stream.set_data(data); page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)
    return path


def glyphs(path):
    content = ContentPage(path, 1)
    try:
        return [dict(text=g['unicode'], origin=g['origin'], gid=g['gid']) for g in content.actual]
    finally:
        content.close()


def select(path, mcid, text, *, before=0, after=0):
    ids = adj.ids_of(path, mcid)
    drawn = ''.join(glyphs(path)[i]['text'] for i in ids)
    start = drawn.index(text)
    return ids[start - before:start + len(text) + after]


def actual_texts(path):
    return intent.actual_texts(path)


@pytest.fixture
def kerned(tmp_path):
    return kerned_source(tmp_path)


def test_normalization_keeps_unchanged_graphemes_and_finds_the_owner(kerned):
    lead_end = select(kerned, 0, '書')
    first_start = select(kerned, 1, '本年')
    selection = make_selection(kerned, 1, glyph_ids=lead_end + first_start)
    found = normalize_edit(kerned, selection, '類本年')
    assert found['selected_text'] == '書本年' and found['changed_text'] == '書' and found['inserted'] == '類'
    assert found['kept_suffix'] == '本年' and found['owner']['mcid'] == 0
    assert found['edit'] == dict(start=2, end=3, text='類')
    assert [o['mcid'] for o in found['selection_owners']] == [0, 1]
    both = normalize_edit(kerned, selection, '類類年')
    assert both['owner'] is None and 'span 2 owners' in both['reason']


@pytest.mark.parametrize('text,replacement,changed,inserted,rule', [
    ('について', 'についての', 'て', 'ての', 'preceding'),
    ('につ', 'のにつ', 'に', 'のに', 'following'),
    ('について', 'に関して', 'つい', '関し', 'changed characters'),
])
def test_insertions_attach_to_a_selected_glyph(tmp_path, text, replacement, changed, inserted, rule):
    path = intent.source(tmp_path)
    found = normalize_edit(path, make_selection(path, 1, glyph_ids=select(path, 1, text)), replacement)
    assert (found['changed_text'], found['inserted']) == (changed, inserted) and rule in found['rule']
    assert found['owner']['mcid'] == 1


def test_selection_and_replacement_refusals(kerned):
    ids = select(kerned, 0, '申請書')
    with pytest.raises(PdfError, match='equals'):
        normalize_edit(kerned, make_selection(kerned, 1, glyph_ids=ids), '申請書')
    with pytest.raises(PdfError, match='contiguous'):
        normalize_edit(kerned, make_selection(kerned, 1, glyph_ids=[ids[0], ids[2]]), '申書')


def test_cross_owner_selection_edits_one_owner_end_with_source_spacing(kerned, tmp_path):
    selection = make_selection(kerned, 1, glyph_ids=select(kerned, 0, '書') + select(kerned, 1, '本'))
    before = glyphs(kerned)
    proposal = propose_edit_intent(kerned, selection, '類本', work_dir=tmp_path / 'work')
    assert proposal['status'] == 'proposed' and proposal['route'] == 'single-span'
    route = proposal['routes']['single-span']
    assert route['after'] == '申請類' and route['new'] == 1 and route['retained'] == 2
    acceptance = accept_edit_intent(kerned, proposal, work_dir=tmp_path / 'work')
    out, side = tmp_path / 'out.pdf', tmp_path / 'out.json'
    result = edit_edit_intent(kerned, acceptance, out, side, work_dir=tmp_path / 'work')
    assert result['route'] == 'single-span' and result['after'] == ['申請類']
    assert open_edit_result(out, side)['status'] == 'restored'
    assert actual_texts(out)[:2] == ['申請類', adj.FIRST]
    after = glyphs(out)
    lead = select(kerned, 0, '申請書')
    # The replacement sits exactly where the kerned source glyph was; nothing else moved.
    new = next(g for g in after if g['text'] == '類' and abs(g['origin'][1] - before[lead[2]]['origin'][1]) < .01)
    assert new['origin'][0] == pytest.approx(before[lead[2]]['origin'][0], abs=.002)
    first = [g for g in after if g['text'] in adj.FIRST and abs(g['origin'][1] - before[lead[0]]['origin'][1]) < .01]
    assert [round(g['origin'][0], 3) for g in first] == [round(before[i]['origin'][0], 3) for i in adj.ids_of(kerned, 1)]


def test_without_source_spacing_the_kerned_owner_has_no_capacity(kerned, tmp_path, monkeypatch):
    original = paragraph.apply_edits

    def without_slots(source_paragraph, snapshot, edits):
        return original(source_paragraph, snapshot, [{k: v for k, v in e.items() if k != 'keep_spacing'} for e in edits])
    monkeypatch.setattr(paragraph, 'apply_edits', without_slots)
    selection = make_selection(kerned, 1, glyph_ids=select(kerned, 0, '書') + select(kerned, 1, '本'))
    proposal = propose_edit_intent(kerned, selection, '類本', work_dir=tmp_path / 'work')
    single = proposal['routes']['single-span']
    assert single['status'] == 'refused' and single['capacity']
    # The capacity refusal is the only door to the adjacent route; here the next
    # sibling is on the same line, so that route refuses too.
    assert proposal['status'] == 'refused' and proposal['routes']['adjacent-span']['status'] == 'refused'


def test_capacity_refusal_routes_to_adjacent_and_saves(tmp_path):
    path = intent.source(tmp_path)
    proposal = propose_edit_intent(path, make_selection(path, 1, glyph_ids=select(path, 1, 'について')),
                                   'につきまして', work_dir=tmp_path / 'work')
    assert proposal['normalized']['changed_text'] == 'い' and proposal['normalized']['inserted'] == 'きまし'
    assert proposal['routes']['single-span']['capacity'] and proposal['route'] == 'adjacent-span'
    acceptance = accept_edit_intent(path, proposal, work_dir=tmp_path / 'work')
    out, side = tmp_path / 'out.pdf', tmp_path / 'out.json'
    result = edit_edit_intent(path, acceptance, out, side, work_dir=tmp_path / 'work')
    assert result['after'] == ['本年度の補助事業につきまして募集', 'を行います。']
    assert open_edit_result(out, side) == dict(open_edit_result(out, side), status='restored', contract='adjacent-span')
    kinds = [p['kind'] for p in result['provenance']]
    assert kinds.count('inserted') == 3 and kinds.count('relocated') == 2


def test_non_capacity_refusal_never_tries_another_route(tmp_path):
    path = intent.source(tmp_path)
    proposal = propose_edit_intent(path, make_selection(path, 1, glyph_ids=select(path, 1, 'について')),
                                   'につき髙して', work_dir=tmp_path / 'work')
    assert proposal['status'] == 'refused' and 'lacks' in proposal['reason']
    assert set(proposal['routes']) == {'single-span'} and not proposal['routes']['single-span']['capacity']
    with pytest.raises(PdfError, match='refused proposal'):
        accept_edit_intent(path, proposal, work_dir=tmp_path / 'work')


def test_stale_tampered_and_unaccepted_states_publish_nothing(kerned, tmp_path):
    work = tmp_path / 'work'
    selection = make_selection(kerned, 1, glyph_ids=select(kerned, 0, '書') + select(kerned, 1, '本'))
    proposal = propose_edit_intent(kerned, selection, '類本', work_dir=work)
    out, side = tmp_path / 'bad.pdf', tmp_path / 'bad.json'
    with pytest.raises(PdfError, match='acceptance'):
        edit_edit_intent(kerned, proposal, out, side, work_dir=work)
    widened = deepcopy(proposal); widened['routes']['single-span']['region']['x1'] += 20
    with pytest.raises(PdfError, match='stale or was modified'):
        accept_edit_intent(kerned, _seal(widened), work_dir=work)
    rerouted = deepcopy(proposal); rerouted['route'] = 'adjacent-span'
    with pytest.raises(PdfError, match='stale or was modified'):
        accept_edit_intent(kerned, _seal(rerouted), work_dir=work)
    acceptance = accept_edit_intent(kerned, proposal, work_dir=work)
    swapped = deepcopy(acceptance); swapped['route'] = 'adjacent-span'
    with pytest.raises(PdfError, match='differs'):
        edit_edit_intent(kerned, _seal(swapped), out, side, work_dir=work)
    other = adj.make_source(tmp_path / 'other.pdf', extra=((50, 230, '書類'),))
    with pytest.raises(PdfError):
        accept_edit_intent(other, proposal, work_dir=work)
    assert not out.exists() and not side.exists()
