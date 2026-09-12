"""Final placement is independent of request order; paint guards still decide."""
from copy import deepcopy

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.document_flow import confirm_document, edit_flow, open_document
from pdfeditor.flow_transaction import edit_flow_batch, plan_flow
from pdfeditor.selection import make_selection, source_sha
from test_attributed import source_pdf


def prepared(tmp_path, *, obstacle=b'', follows=True):
    source = source_pdf(tmp_path, b'BT /Regular 12 Tf 20 200 Td (ONE) Tj '
                        b'0 -20 Td (TWO) Tj 0 -20 Td (END) Tj '
                        b'0 -30 Td (NEXT) Tj 180 -45 Td (FIXED) Tj ET ' + obstacle)
    font = tmp_path / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    elements = {
        ident: dict(paragraph=inspect_paragraph(source, make_selection(source, glyph_ids=ids, explicit_width=90)),
                    layout=dict(width=90, max_bottom=145, min_line_height=20),
                    fonts={'s0': {'path': str(font)}})
        for ident, ids in [('A', list(range(9))), ('B', list(range(9, 13)))]
    }
    model = confirm_document(source, elements, container_id='body', bounds=[10, 45, 120, 145], page=1,
                             follows=[dict(before='A', after='B', gap=30)] if follows else [])
    return source, model


def request(model, **texts):
    return {i: {'edits': [dict(start=0, end=len(model['elements'][i]['binding']['paragraph']['text']),
                              text=text, style_id='s0')], 'empty_style_id': 's0'} for i, text in texts.items()}


def run(tmp_path, source, model, changes, name):
    out, path = tmp_path / f'{name}.pdf', tmp_path / f'{name}.json'
    report = edit_flow_batch(source, model, out, path, changes)
    restored = open_document(out, path)
    assert restored['status'] == 'restored', restored
    assert restored['state']['previous_model_sha256'] == model['model_sha256']
    assert report['final_layout_verified'] and report['every_intermediate_guard_verified']
    return out, restored['state'], report


def test_final_plan_vacates_space_and_survives_reverse_empty_retype(tmp_path):
    source, model = prepared(tmp_path)
    before = deepcopy(model)
    changes = request(model, B='NEXT\nMORE\nEND', A='SHORT')
    copy = deepcopy(changes)
    # B cannot fit at the current baseline. No larger container is supplied.
    with pytest.raises(ValueError, match='vertical space'):
        edit_flow(source, model, tmp_path/'isolated.pdf', tmp_path/'isolated.json', 'B', changes['B']['edits'])
    plan = plan_flow(source, model, changes)
    assert plan['schedule'] == ['A', 'B']
    assert plan['final_positions']['B'] == dict(baseline=90, last_baseline=130)
    assert model == before and changes == copy
    assert not (tmp_path/'isolated.pdf').exists()
    pdf, state, report = run(tmp_path, source, model, changes, 'grow-b')
    assert [t['element_id'] for t in report['transactions']] == ['A', 'B']
    assert report['plan'] == plan
    with pymupdf.open(source) as a, pymupdf.open(pdf) as b:
        assert a[1].get_pixmap().samples == b[1].get_pixmap().samples
    # Reverse allocation requires B to contract before growing A; the edit
    # offsets still refer to this batch's original logical texts.
    pdf, state, report = run(tmp_path, pdf, state, request(state, A='ONE\nTWO\nEND', B='NEXT'), 'grow-a')
    assert report['plan']['schedule'] == ['B', 'A']
    assert state['elements']['B']['binding']['layout']['baseline'] == 130
    pdf, state, _ = run(tmp_path, pdf, state, request(state, A='', B=''), 'empty')
    assert all(not e['binding']['paragraph']['text'] for e in state['elements'].values())
    assert PdfReader(pdf).pages[0].extract_text().strip() == 'FIXED'
    pdf, state, _ = run(tmp_path, pdf, state, request(state, B='NEXT\nMORE\nEND', A='SHORT'), 'retype')
    assert [e['binding']['paragraph']['text'] for e in state['elements'].values()] == ['SHORT', 'NEXT\nMORE\nEND']
    copied, _, _ = run(tmp_path, pdf, state, {'A': {'edits': []}, 'B': {'edits': []}}, 'noop')
    with pymupdf.open(pdf) as a, pymupdf.open(copied) as b:
        assert all(a[i].get_pixmap().samples == b[i].get_pixmap().samples for i in range(len(a)))


@pytest.mark.parametrize('mode', ['final_overflow', 'unknown_id', 'unknown_policy', 'stale', 'font_changed'])
def test_invalid_plan_never_calls_writer(tmp_path, monkeypatch, mode):
    import pdfeditor.flow_transaction as module
    source, model = prepared(tmp_path)
    changes = request(model, B='NEXT\nMORE\nEND', A='SHORT')
    if mode == 'final_overflow': changes['A']['edits'][0]['text'] = 'ONE\nTWO\nEND'
    if mode == 'unknown_id': changes['C'] = changes.pop('B')
    if mode == 'unknown_policy': changes['B']['resize'] = True
    if mode == 'font_changed': (tmp_path/'font.ttf').write_bytes(b'changed')
    if mode == 'stale':
        w = PdfWriter(clone_from=PdfReader(source)); w.add_metadata({'/Title': 'external'})
        source = tmp_path/'external.pdf'; w.write(source)
    def forbidden(*a, **k): raise AssertionError('writer must not run for an invalid final plan')
    monkeypatch.setattr(module, 'edit_flow', forbidden)
    with pytest.raises((PdfError, ValueError)):
        edit_flow_batch(source, model, tmp_path/'bad.pdf', tmp_path/'bad.json', changes)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


@pytest.mark.parametrize('kind', ['vector', 'text', 'clip', 'annotation'])
def test_final_extent_plan_does_not_authorize_collisions(tmp_path, kind):
    obstacle = (b'0 0 1 rg 70 160 20 10 re f' if kind == 'vector' else
                b'BT /Regular 12 Tf 70 152 Td (X) Tj ET' if kind == 'text' else b'')
    source, model = prepared(tmp_path, obstacle=obstacle)
    if kind in ('clip', 'annotation'):
        # Bind AFTER the source geometry change, so this tests the backend
        # guard rather than only a stale source hash.
        if kind == 'clip':
            w = PdfWriter(clone_from=PdfReader(source))
            stream = w.pages[0]['/Contents'].get_object()
            stream.set_data(b'q 10 125 48 100 re W n ' + stream.get_data() + b' Q')
            w.write(source)
        else:
            with pymupdf.open(source) as doc:
                doc[0].add_rect_annot(pymupdf.Rect(70, 90, 90, 100))
                doc.saveIncr()
        # Source selections themselves still fit the narrow clip.
        font = tmp_path/'font.ttf'
        elements = {i: dict(paragraph=inspect_paragraph(source, make_selection(source, glyph_ids=ids, explicit_width=90)),
                           layout=dict(width=90,max_bottom=145,min_line_height=20), fonts={'s0':{'path':str(font)}})
                    for i, ids in [('A',list(range(9))),('B',list(range(9,13)))]}
        model = confirm_document(source, elements, container_id='body', bounds=[10,45,120,145],page=1,
                                 follows=[dict(before='A',after='B',gap=30)])
    changes = request(model, A='SHORT', B='NEXT\n1234567890\nEND')
    plan = plan_flow(source, model, changes)
    assert plan['collision_status'].startswith('not_certified')
    with pytest.raises((PdfError, ValueError)):
        edit_flow_batch(source, model, tmp_path/'bad.pdf', tmp_path/'bad.json', changes)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
    assert not list(tmp_path.glob('.flow-batch-*'))


def test_owned_paint_keeps_rigid_geometry_and_unowned_fixed_paint_does_not_follow(tmp_path):
    from test_document_flow import prepared as with_paint
    source, model = with_paint(tmp_path)
    b = model['elements']['B']['binding']
    old = b['element']['paths'][0]['proof']['paints'][0]
    pdf, state, report = run(tmp_path, source, model, request(model, A='ONE\nTWO\nEND', B='NEXT'), 'owned')
    new = state['elements']['B']['binding']['element']['paths'][0]['proof']['paints'][0]
    delta = report['plan']['final_positions']['B']['baseline'] - b['layout']['baseline']
    assert delta > 0
    assert new['bounds'] == pytest.approx([old['bounds'][0], old['bounds'][1]+delta,
                                          old['bounds'][2], old['bounds'][3]+delta])
    assert new['components'] == old['components']
    # Removing ownership changes the contract, not the bbox. The same fixed
    # background must not be silently attached to B by the batch scheduler.
    from pdfeditor.document_flow import _reseal
    fixed = deepcopy(model)
    fixed['elements']['B']['owned_paints'] = []
    fixed = _reseal(fixed)
    with pytest.raises(PdfError, match='vector|background'):
        edit_flow_batch(source, fixed, tmp_path/'fixed.pdf', tmp_path/'fixed.json',
                        request(fixed, A='ONE\nTWO\nEND', B='NEXT'))
    assert not (tmp_path/'fixed.pdf').exists() and not (tmp_path/'fixed.json').exists()


def test_no_relation_does_not_reallocate_space(tmp_path):
    source, model = prepared(tmp_path, follows=False)
    with pytest.raises(ValueError, match='vertical space'):
        plan_flow(source, model, request(model, A='SHORT', B='NEXT\nMORE\nEND'))


def test_chain_keeps_unedited_third_element_and_retained_inline_text(tmp_path):
    source = source_pdf(tmp_path, b'BT /Regular 12 Tf 20 200 Td (ONE) Tj '
                        b'0 -20 Td (TWO) Tj 0 -20 Td (END) Tj '
                        b'0 -30 Td (NEXT) Tj 0 -30 Td (LAST) Tj ET')
    font = tmp_path/'font.ttf'; font.write_bytes(pymupdf.Font('cjk').buffer)
    specs = {i: dict(paragraph=inspect_paragraph(source, make_selection(source, glyph_ids=ids, explicit_width=90)),
                     layout=dict(width=90,max_bottom=180,min_line_height=20),fonts={'s0':{'path':str(font)}})
             for i, ids in [('A',list(range(9))),('B',list(range(9,13))),('C',list(range(13,17)))]}
    model = confirm_document(source,specs,container_id='body',bounds=[10,45,120,180],page=1,
                             follows=[dict(before='A',after='B',gap=30),dict(before='B',after='C',gap=30)])
    changes = request(model, A='SHORT')
    changes['B'] = {'edits':[dict(start=4,end=4,text='\nMORE\nEND',style_id='s0')]}
    pdf, state, report = run(tmp_path,source,model,changes,'chain')
    assert report['plan']['schedule'] == ['A','B']
    assert state['elements']['C']['binding']['layout']['baseline'] == 160
    assert state['elements']['B']['binding']['paragraph']['text'] == 'NEXT\nMORE\nEND'
    from pdfeditor.replay import glyph_observations, compare_glyphs
    with pymupdf.open(source) as a, pymupdf.open(pdf) as b:
        before = glyph_observations(a[0]); after = glyph_observations(b[0])
        ids = state['elements']['C']['binding']['paragraph']['selection']['glyph_ids']
        assert compare_glyphs(before[13:17],[after[i] for i in ids])['passed']


def test_writer_plan_mismatch_rolls_back(tmp_path, monkeypatch):
    import pdfeditor.flow_transaction as module
    source, model = prepared(tmp_path)
    real = module.plan_paragraph
    def corrupt_measurement(*a, **k):
        result = real(*a, **k)
        result['lines'][0]['width'] += 1
        return result
    monkeypatch.setattr(module,'plan_paragraph',corrupt_measurement)
    with pytest.raises(PdfError, match='shaped lines differ'):
        edit_flow_batch(source,model,tmp_path/'bad.pdf',tmp_path/'bad.json',request(model,A='SHORT'))
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_failure_after_first_edit_and_publication_rollback(tmp_path, monkeypatch):
    import pdfeditor.flow_transaction as module
    source, model = prepared(tmp_path)
    original = source_sha(source)
    changes = request(model, B='NEXT\nMORE\nEND', A='SHORT')
    real = module.edit_flow
    calls = []
    def fail_second(*a, **k):
        calls.append(a[4])
        if len(calls) == 2: raise PdfError('injected second writer failure')
        return real(*a, **k)
    monkeypatch.setattr(module, 'edit_flow', fail_second)
    with pytest.raises(PdfError, match='second writer'):
        edit_flow_batch(source, model, tmp_path/'bad.pdf', tmp_path/'bad.json', changes)
    assert calls == ['A','B'] and source_sha(source) == original
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
    assert not list(tmp_path.glob('.flow-batch-*'))
    monkeypatch.setattr(module, 'edit_flow', real)
    real_link = module.os.link
    def fail_publish(a,b):
        if b == tmp_path/'bad.json': raise OSError('injected publication failure')
        return real_link(a,b)
    monkeypatch.setattr(module.os, 'link', fail_publish)
    with pytest.raises(OSError, match='publication'):
        edit_flow_batch(source, model, tmp_path/'bad.pdf', tmp_path/'bad.json', changes)
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
    assert not list(tmp_path.glob('.flow-batch-*'))
