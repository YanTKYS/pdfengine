"""Source-output v1 ownership, canonical saves and conservative refusals."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter

from pdfeditor import source_ownership as owned
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, State
from pdfeditor.document_flow import _reseal
from pdfeditor.shared_flow import LEGACY_SCHEMA, SCHEMA, _contract, edit_shared_flow, open_shared_flow
from test_continuation import prepared, saved, change, LONG
from test_generated_block_canonical import block_of, pixels


def program(pdf, page=1):
    return PdfReader(pdf).pages[page-1].get_contents().get_data()


def body(pdf, state, sid='slot-0'):
    slot = state['slots'][sid]
    page = state['regions'][slot['region_id']]['page']
    data = program(pdf, page)
    span = owned.inventory(data)[slot['source_output']['marker_id']]
    return data[span[1]:span[2]]


def legacy(state):
    state = deepcopy(state)
    state['schema'] = LEGACY_SCHEMA
    for slot in state['slots'].values():
        slot.pop('source_output', None)
    state['contract_sha256'] = _contract(state)
    return _reseal(state)


@pytest.fixture(scope='module')
def lifecycle(tmp_path_factory):
    root = tmp_path_factory.mktemp('source-lifecycle')
    source, state = prepared(root, confirm=False)
    initial = deepcopy(state)
    rows = {}
    for name, text in [('first', 'ABCDE'), ('noop1', None), ('noop2', None), ('noop3', None),
                       ('second', 'XYZ'), ('second_noop', None), ('empty', ''),
                       ('empty_noop', None), ('regrow', 'ABCDE')]:
        out, state, report = saved(root, source, state, {} if text is None else change(state, text), name)
        rows[name] = (out, deepcopy(state), report)
        source = out
    return root, initial, rows


def test_first_body_and_page_are_already_canonical(lifecycle):
    _, initial, rows = lifecycle
    assert initial['schema'] == SCHEMA
    assert initial['slots']['slot-0']['source_output']['state'] == 'uninitialized'
    first, state, _ = rows['first']
    for name in ('noop1', 'noop2', 'noop3'):
        pdf, current, report = rows[name]
        assert body(pdf, current) == body(first, state)
        assert program(pdf) == program(first)
        assert pixels(pdf) == pixels(first)
        assert current['generated_fonts'] == state['generated_fonts']
        [mutation] = report['mutation_map'][1]['mutations']
        assert mutation['kind'] == owned.REWRITE and mutation['owner'] == 'slot-0'
        a, b, c, d = owned.inventory(program(first))[state['slots']['slot-0']['source_output']['marker_id']]
        assert (mutation['start'], mutation['end']) == (b, c)
        assert mutation['length'] == c-b
        assert current['slots']['slot-0']['source_output'] == state['slots']['slot-0']['source_output']


def test_fixed_bridge_second_empty_regrow(lifecycle):
    _, _, rows = lifecycle
    first, initial, _ = rows['first']
    record = initial['slots']['slot-0']['source_output']
    start, _, _, end = owned.inventory(program(first))[record['marker_id']]
    prefix, suffix = program(first)[:start], program(first)[end:]
    for pdf, state, _ in rows.values():
        current = state['slots']['slot-0']['source_output']
        assert owned.immutable(current) == owned.immutable(record)
        assert current['current']['entry_context_sha256'] == record['current']['entry_context_sha256']
        a, _, _, b = owned.inventory(program(pdf))[record['marker_id']]
        assert program(pdf)[:a] == prefix and program(pdf)[b:] == suffix
    for changed, noop in [('second', 'second_noop'), ('empty', 'empty_noop')]:
        assert program(rows[changed][0]) == program(rows[noop][0])
        assert pixels(rows[changed][0]) == pixels(rows[noop][0])
    empty, state, _ = rows['empty']
    paragraph = state['slots']['slot-0']['binding']['paragraph']
    assert paragraph['insertion_binding'] and paragraph['style_slot_bindings']
    content = ContentPage(empty, 1)
    try:
        a, b, c, d = owned.inventory(program(empty))[record['marker_id']]
        assert all(not e.chars for e in content.events if b <= e.operator.start < c)
        # Typing witness also witnesses its style; it is not duplicated.
        assert sum(b <= e.operator.start < c for e in content.events) == len(paragraph['style_slot_bindings'])
    finally:
        content.close()
    assert body(rows['regrow'][0], rows['regrow'][1]) == body(first, initial)


def test_poppler_noop_pixels(lifecycle, tmp_path):
    renderer = shutil.which('pdftoppm')
    if renderer is None:
        pytest.skip('Poppler unavailable; Windows external renderer validation remains required')
    from PIL import Image
    _, _, rows = lifecycle
    def rendered(pdf, name):
        images = []
        for page in range(1, len(PdfReader(pdf).pages)+1):
            dest = tmp_path/f'{name}-{page}'
            subprocess.run([renderer, '-f', str(page), '-l', str(page), '-singlefile', '-r', '72',
                            '-png', str(pdf), str(dest)], check=True, capture_output=True)
            with Image.open(dest.with_suffix('.png')) as image:
                images.append((image.size, image.convert('RGB').tobytes()))
        return images
    expected = rendered(rows['first'][0], 'first')
    for name in ('noop1', 'noop2', 'noop3'):
        assert rendered(rows[name][0], name) == expected


@pytest.mark.parametrize('field', ['missing', 'version', 'state', 'marker', 'seed', 'program', 'block', 'range', 'context', 'owner'])
def test_resealed_ownership_tamper_fails_before_publication(lifecycle, tmp_path, field):
    _, _, rows = lifecycle
    pdf, state, _ = rows['first']
    state = deepcopy(state)
    record = state['slots']['slot-0']['source_output']
    if field == 'missing': del state['slots']['slot-0']['source_output']
    elif field == 'version': record['version'] = 9
    elif field == 'state': record['state'] = 'uninitialized'; record.pop('current')
    elif field == 'marker': record['marker_id'] = '0'*64
    elif field == 'seed': record['created_from']['pdf_sha256'] = '0'*64
    elif field == 'program': record['current']['program_sha256'] = '0'*64
    elif field == 'block': record['current']['block_sha256'] = '0'*64
    elif field == 'range': record['current']['range'][0] += 1
    elif field == 'context': record['current']['entry_context_sha256'] = '0'*64
    elif field == 'owner': state['slots']['slot-0']['paragraph_id'] = 'foreign'
    state = _reseal(state)
    assert open_shared_flow(pdf, state)['status'] == 'needs_confirmation'
    with pytest.raises(PdfError):
        edit_shared_flow(pdf, state, tmp_path/'bad.pdf', tmp_path/'bad.json', {})
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_marker_recognition_uses_operator_gaps_not_literal_strings():
    record = {'marker_id': 'a'*64}
    begin, end = owned.markers(record)
    fake = b'BT /F 12 Tf (' + begin + end + b') Tj ET'
    assert owned.inventory(fake) == {}
    data = fake + begin + b'q BT [] TJ ET Q\n' + end
    assert set(owned.inventory(data)) == {'a'*64}
    with pytest.raises(PdfError, match='duplicate'):
        owned.inventory(data + begin + b'q BT [] TJ ET Q\n' + end)
    with pytest.raises(PdfError, match='missing'):
        owned.inventory(fake + begin)
    with pytest.raises(PdfError, match='malformed'):
        owned.inventory(data.replace(b'begin ', b'begin  '))


@pytest.mark.parametrize('extra', [b'1 0 0 1 0 0 cm', b'/GS gs', b'0 0 1 1 re f', b'/X Do',
                                     b'/DeviceRGB cs', b'0 0 0 sc', b'/P BMC EMC', b'BX EX', b'% comment\n',
                                     b'q Q', b'BT ET'])
def test_body_grammar_rejects_foreign_operators_and_nested_groups(extra):
    with pytest.raises(PdfError):
        owned.grammar(b'q BT ' + extra + b' [] TJ ET Q\n')


def test_context_preserves_string_evidence_and_ignores_location():
    state = State(fill=('rg', ('0.10', '-0', '1')))
    state.clip = ({'rule': 'W', 'at': [12, 8], 'path': [dict(operator='re', args=[0, -0.0, 100, 100],
                                                                ctm=[1, 0, 0, 1, 0, 0])]},)
    boundary = SimpleNamespace(state=state, text_object=False, q_depth=2, marked_content_depth=0,
                               compatibility_depth=0, pending_path=False, pending_clip=False)
    original = owned.context(boundary)
    state.clip[0]['at'] = [99, 999]
    state.clip[0]['path'][0]['args'][1] = 0
    assert owned.context(boundary) == original
    state.fill = ('rg', ('0.1', '-0', '1'))
    assert owned.context(boundary) != original
    state.fill = ('sc', ('0.1',))
    with pytest.raises(PdfError): owned.context(boundary)


def test_legacy_open_edit_never_upgrades(tmp_path):
    source, state = prepared(tmp_path, confirm=False)
    state = legacy(state)
    assert open_shared_flow(source, state)['status'] == 'restored'
    first, state, _ = saved(tmp_path, source, state, change(state, 'ABCDE'), 'first')
    second, state, report = saved(tmp_path, first, state, {}, 'noop')
    assert state['schema'] == LEGACY_SCHEMA
    assert all('source_output' not in s for s in state['slots'].values())
    assert not owned.inventory(program(second))
    assert len(program(second)) > len(program(first))
    assert all(m['kind'] == 'text-edit' for m in report['mutation_map'][1]['mutations'])


def test_fresh_process_needs_no_previous_report_or_pdf(lifecycle, tmp_path):
    _, _, rows = lifecycle
    pdf, state, _ = rows['noop3']
    current = tmp_path/'current.pdf'
    shutil.copyfile(pdf, current)
    model = tmp_path/'current.json'
    model.write_text(json.dumps(state), encoding='utf-8')
    code = ('from pdfeditor.shared_flow import edit_shared_flow; import sys; '
            'edit_shared_flow(*sys.argv[1:5], {})')
    subprocess.run([sys.executable, '-c', code, str(current), str(model), str(tmp_path/'out.pdf'),
                    str(tmp_path/'out.json')], check=True, capture_output=True, text=True)
    assert program(tmp_path/'out.pdf') == program(current)


def test_generated_continuation_and_source_islands_coexist(tmp_path):
    source, state = prepared(tmp_path, same_page=True)
    first, state, _ = saved(tmp_path, source, state, change(state, LONG), 'first')
    previous = deepcopy(state)
    second, state, _ = saved(tmp_path, first, state, {}, 'noop')
    assert program(first) == program(second)
    assert block_of(first, previous, 'empty') == block_of(second, state, 'empty')
    assert state['continuation_destinations'] == previous['continuation_destinations']
    assert state['generated_fonts'] == previous['generated_fonts']
    assert all('source_output' not in slot for slot in state['slots'].values() if slot.get('destination_id'))


def decorate_program(prefix, suffix):
    def decorate(source):
        writer = PdfWriter(clone_from=PdfReader(source))
        stream = writer.pages[0]['/Contents'].get_object()
        stream.set_data(prefix + stream.get_data() + suffix)
        writer.write(source)
    return decorate


@pytest.mark.parametrize('prefix,suffix', [
    (b'q ', b' Q'),
    (b'q 1 0 0 1 1 0 cm ', b' Q'),
    (b'q 0 0 320 260 re W n ', b' Q'),
])
def test_scoped_ctm_and_proven_clip_keep_context_and_bytes(tmp_path, prefix, suffix):
    source, state = prepared(tmp_path, confirm=False, decorate=decorate_program(prefix, suffix))
    first, state, _ = saved(tmp_path, source, state, change(state, 'ABCDE'), 'first')
    context = state['slots']['slot-0']['source_output']['current']['entry_context_sha256']
    second, state, _ = saved(tmp_path, first, state, {}, 'noop')
    assert program(first) == program(second)
    assert pixels(first) == pixels(second)
    assert state['slots']['slot-0']['source_output']['current']['entry_context_sha256'] == context


@pytest.mark.parametrize('prefix,suffix', [
    (b'/P BMC ', b' EMC'),
    (b'BX ', b' EX'),
    (b'0 0 m ', b' n'),
    (b'0 0 320 260 re W ', b' n'),
])
def test_unsupported_entry_scopes_refuse_initial_creation(tmp_path, prefix, suffix):
    source, state = prepared(tmp_path, confirm=False, decorate=decorate_program(prefix, suffix))
    with pytest.raises(PdfError, match='source output'):
        edit_shared_flow(source, state, tmp_path/'bad.pdf', tmp_path/'bad.json', change(state, 'ABCDE'))
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_foreign_marker_comment_never_becomes_a_source_owner(tmp_path):
    begin, end = owned.markers({'marker_id': 'f'*64})
    with pytest.raises(PdfError, match='marker inventory'):
        prepared(tmp_path, confirm=False, decorate=decorate_program(begin+b'q BT [] TJ ET Q\n'+end, b''))


def test_current_glyph_and_empty_witness_containment(lifecycle):
    _, _, rows = lifecycle
    for name in ('first', 'empty'):
        pdf, state, _ = rows[name]
        record = state['slots']['slot-0']['source_output']
        snapshot = deepcopy(state['slots']['slot-0']['binding']['paragraph'])
        content = ContentPage(pdf, 1)
        try:
            if name == 'first':
                snapshot['selection']['glyph_ids'] = snapshot['selection']['glyph_ids'][1:]
                with pytest.raises(PdfError, match='foreign'):
                    owned.owned_body(content, record, snapshot)
                snapshot['selection']['glyph_ids'].append(10000)
                with pytest.raises(PdfError): owned.owned_body(content, record, snapshot)
            else:
                snapshot['insertion_binding']['event']['byte_range'] = [0, 5]
                with pytest.raises(PdfError, match='witness outside'):
                    owned.owned_body(content, record, snapshot)
        finally:
            content.close()


@pytest.mark.parametrize('phase', ['rebind', 'context', 'publish'])
def test_late_failure_publishes_neither_artifact(lifecycle, tmp_path, monkeypatch, phase):
    _, _, rows = lifecycle
    pdf, state, _ = rows['first']
    original = pdf.read_bytes(), deepcopy(state)
    if phase == 'rebind':
        def fail(*args): raise PdfError('injected source rebind failure')
        monkeypatch.setattr(owned, 'rebind', fail)
    elif phase == 'context':
        real = owned.witness
        def changed(content, *args):
            result = real(content, *args)
            # Keep validation of the input valid; corrupt the output witness.
            if Path(content.document.name).resolve() != pdf.resolve():
                result['entry_context_sha256'] = '0'*64
            return result
        monkeypatch.setattr(owned, 'witness', changed)
    else:
        import pdfeditor.editable as editable
        real = editable.os.link
        def fail(source, target):
            if Path(target) == tmp_path/'bad.json': raise OSError('injected publication failure')
            return real(source, target)
        monkeypatch.setattr(editable.os, 'link', fail)
    with pytest.raises((PdfError, OSError), match='injected|context'):
        edit_shared_flow(pdf, state, tmp_path/'bad.pdf', tmp_path/'bad.json', {})
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
    assert (pdf.read_bytes(), state) == original


def custom_flow(root, data, specs, *, fixed_background=False):
    from pdfeditor.attributed import inspect_paragraph
    from pdfeditor.selection import make_selection
    from pdfeditor.story_flow import confirm_story
    from pdfeditor.shared_flow import confirm_shared_flow
    from test_attributed import source_pdf
    source = source_pdf(root, data)
    font = root/'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    stories, regions, mapping, policies = {}, {}, {}, {}
    for pid, ids, x, width, bounds, baseline in specs:
        paragraph = inspect_paragraph(source, make_selection(source, glyph_ids=ids, explicit_width=width))
        relations = []
        if fixed_background:
            from pdfeditor.elements import inspect_element
            element = inspect_element(source, paragraph['selection'])
            relations = [dict(source_id=element['paths'][0]['source_id'], relation='backgrounds', behavior='fixed-to-page')]
        rid = 'region-'+pid
        stories[pid] = confirm_story(source, {'part': dict(page=1, bounds=bounds, paragraph=paragraph,
            paint_relations=relations, layout=dict(x=x, baseline=baseline, width=width, max_bottom=bounds[3],
                                           min_line_height=22, first_line_indent=0))},
            paragraph_id=pid, chain=['part'], protected_regions={},
            styles={'body': dict(provider=dict(path=str(font)), provider_relation='substituted')},
            style_assignments={'part': {s['id']: 'body' for s in paragraph['styles']}}, typing_style_id='body')
        regions[rid] = dict(page=1, bounds=bounds, x=x, width=width, first_baseline=baseline)
        mapping[pid] = {'part': rid}
        policies[pid] = dict(min_line_height=22, first_line_indent=0, keep_together=False,
                            break_before='auto' if len(stories)==1 else 'next-region', break_after='auto',
                            empty=dict(kind='reserve-line', ascent=10, descent=3))
    pairs = zip(list(stories), list(stories)[1:])
    state = confirm_shared_flow(source, stories, flow_id='custom', paragraph_order=list(stories),
        regions=regions, region_order=list(regions), slot_regions=mapping, paragraph_policies=policies,
        follows=[dict(before=a, after=b, minimum_baseline_gap=24, region_start='reset-to-region-baseline') for a,b in pairs],
        protected_regions={})
    return source, state


def test_same_source_show_cannot_have_two_owners(tmp_path):
    source, state = custom_flow(tmp_path, b'BT /Regular 12 Tf 20 200 Td [(AB) -10000 (CD)] TJ ET',
        [('A', [0,1], 20, 50, [18,40,73,78], 60), ('B', [2,3], 155, 60, [148,40,223,78], 60)])
    with pytest.raises(PdfError, match='overlap|two owners'):
        edit_shared_flow(source, state, tmp_path/'bad.pdf', tmp_path/'bad.json', {})
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()


def test_mixed_show_multiple_text_objects_preserve_unselected_paint(tmp_path):
    data = (b'BT /Regular 12 Tf 20 200 Td (PRE ABC POST) Tj ET '
            b'BT /Regular 12 Tf 20 175 Td (DEF) Tj ET '
            b'BT /Regular 12 Tf 220 200 Td (KEEP) Tj ET')
    source, state = custom_flow(tmp_path, data, [('A', [4,5,6,12,13,14], 20, 100, [18,40,173,200], 110)])
    first, state, _ = saved(tmp_path, source, state, {}, 'first')
    second, state, _ = saved(tmp_path, first, state, {}, 'noop')
    assert program(first) == program(second)
    def outside(pdf, selected):
        content = ContentPage(pdf, 1)
        try:
            return [(c.code, c.origin, e.state.font.name) for e in content.events for c in e.chars
                    if not set(c.source_orders) & set(selected)]
        finally: content.close()
    assert outside(source, [4,5,6,12,13,14]) == outside(second, state['slots']['slot-0']['binding']['paragraph']['selection']['glyph_ids'])


def test_source_formatter_keeps_original_font_codes_for_retained_units(tmp_path):
    from pdfeditor.editable import plan_document_edit, bind_document_edit
    from pdfeditor.transaction import Transaction
    source, state = prepared(tmp_path, confirm=False)
    slot = state['slots']['slot-0']
    output = tmp_path/'retained.pdf'
    with Transaction(source) as transaction:
        plan = plan_document_edit(transaction.page(1), slot['binding'], [dict(start=1,end=2,text='Z')],
            fonts={'s0': dict(path=str(tmp_path/'font.ttf'))}, owner='slot-0', _source_output=slot['source_output'])
        result = transaction.commit(output)
        try:
            binding, report = bind_document_edit(result.identity(1), plan, result)
            record = owned.rebind(result.identity(1).after, slot['source_output'], plan.source_entry_context)
            owned.owned_body(result.identity(1).after, record, binding['paragraph'])
            assert report['retained_glyph_count'] == 3 and report['provided_font_glyph_count'] == 1
            assert {g['font_resource'] for g in report['glyph_plan']} == {'/Regular', '/PRF1'}
        finally: result.close()
    from pdfeditor.pdf_save import _written_value
    fonts = lambda path: PdfReader(path).pages[0]['/Resources']['/Font']
    assert _written_value(fonts(source)['/Regular']) == _written_value(fonts(output)['/Regular'])


def test_fixed_background_and_unrelated_annotation_survive(tmp_path):
    source, state = custom_flow(tmp_path, b'0.9 g 15 140 160 90 re f 0 g '
                               b'BT /Regular 12 Tf 20 200 Td (ABCD) Tj ET',
                               [('A', list(range(4)), 20, 150, [18,40,173,120], 60)], fixed_background=True)
    first, current, _ = saved(tmp_path, source, state, change(state, 'ABCDE'), 'first')
    second, current, _ = saved(tmp_path, first, current, {}, 'noop')
    assert program(first) == program(second)
    assert current['slots']['slot-0']['binding']['relations'][0]['relation'] == 'backgrounds'
    assert program(second).startswith(b'0.9 g 15 140 160 90 re f 0 g ')
    # Annotation regression uses an ordinary source outside the editable region.
    other = tmp_path/'annotated'; other.mkdir()
    def decorate(path):
        with pymupdf.open(path) as doc:
            doc[0].add_rect_annot(pymupdf.Rect(220,180,250,210))
            image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0,0,2,2))
            image.clear_with(120)
            doc[0].insert_image(pymupdf.Rect(220,100,250,130), stream=image.tobytes('png'))
            doc.saveIncr()
    original, model = prepared(other, confirm=False, decorate=decorate)
    out, _, _ = saved(other, original, model, change(model, 'ABCDE'), 'first')
    with pymupdf.open(original) as a, pymupdf.open(out) as b:
        assert [tuple(v.rect) for v in a[0].annots()] == [tuple(v.rect) for v in b[0].annots()]
        assert [a.extract_image(v[0])['image'] for v in a[0].get_images()] == [b.extract_image(v[0])['image'] for v in b[0].get_images()]


def test_initial_island_refuses_text_clipping_in_same_object(tmp_path):
    from test_attributed import source_pdf
    pdf = source_pdf(tmp_path, b'BT /Regular 12 Tf 20 200 Td (ABCD) Tj 7 Tr (CLIP) Tj ET')
    content = ContentPage(pdf, 1)
    try:
        with pytest.raises(PdfError):
            owned.initial_context(content, SimpleNamespace(first=content.events[0]))
    finally: content.close()


def test_multi_slot_rebind_and_schedule_semantics(tmp_path, monkeypatch):
    import pdfeditor.shared_flow as shared
    from test_shared_flow import prepared as multiple, replace, SHORT
    source, state = multiple(tmp_path)
    changes = {'A': replace(state, 'A', SHORT)}
    first, current, _ = saved(tmp_path, source, state, changes, 'first')
    before = deepcopy(current)
    second, current, _ = saved(tmp_path, first, current, {}, 'noop')
    assert all(program(first,p)==program(second,p) for p in (1,2,3))
    for sid, slot in current['slots'].items():
        record = slot['source_output']
        page = current['regions'][slot['region_id']]['page']
        assert record['current']['program_sha256'] == owned.sha(program(second,page))
        assert record == before['slots'][sid]['source_output']
    real = shared._plan
    def reversed_plan(*args):
        result = real(*args)
        result['schedule'] = list(reversed(result['schedule']))
        return result
    monkeypatch.setattr(shared, '_plan', reversed_plan)
    alternate, alternate_state, _ = saved(tmp_path, source, state, changes, 'reordered')
    assert pixels(alternate) == pixels(first)
    for sid, slot in alternate_state['slots'].items():
        assert slot['source_output']['marker_id'] == before['slots'][sid]['source_output']['marker_id']
        assert slot['binding']['paragraph']['text'] == before['slots'][sid]['binding']['paragraph']['text']


def test_confirmed_spacing_styles_remain_in_empty_body(tmp_path):
    source, state = prepared(tmp_path, spacing=True, confirm=False)
    first, state, _ = saved(tmp_path, source, state, change(state, 'ABCDE'), 'first')
    empty, state, _ = saved(tmp_path, first, state, change(state, ''), 'empty')
    record = deepcopy(state['slots']['slot-0']['source_output'])
    noop, state, _ = saved(tmp_path, empty, state, {}, 'empty-noop')
    assert program(empty) == program(noop)
    paragraph = state['slots']['slot-0']['binding']['paragraph']
    assert paragraph['style_slot_bindings']
    out, state, _ = saved(tmp_path, noop, state, change(state, 'ABCDE'), 'regrow')
    assert state['slots']['slot-0']['source_output']['current']['entry_context_sha256'] == record['current']['entry_context_sha256']
    assert pixels(first) == pixels(out)


@pytest.mark.parametrize('kind', ['body-cm', 'body-path', 'entry-context'])
def test_current_pdf_tamper_fails_even_with_updated_byte_hashes(lifecycle, tmp_path, kind):
    from pypdf.generic import DecodedStreamObject, NameObject
    _, _, rows = lifecycle
    pdf, state, _ = rows['first']
    record = deepcopy(state['slots']['slot-0']['source_output'])
    data = program(pdf)
    start, a, b, end = owned.inventory(data)[record['marker_id']]
    if kind == 'entry-context':
        data = b'0.5 g ' + data
    else:
        extra = b'1 0 0 1 0 0 cm ' if kind == 'body-cm' else b'0 0 1 1 re n '
        data = data[:a+5] + extra + data[a+5:]
    start, a, b, end = owned.inventory(data)[record['marker_id']]
    record['current'].update(program_sha256=owned.sha(data), range=[start,end], block_sha256=owned.sha(data[start:end]))
    writer = PdfWriter(clone_from=PdfReader(pdf))
    stream = DecodedStreamObject(); stream.set_data(data)
    writer.pages[0][NameObject('/Contents')] = writer._add_object(stream)
    changed = tmp_path/'changed.pdf'; writer.write(changed)
    content = ContentPage(changed, 1)
    try:
        with pytest.raises(PdfError, match='grammar|ownership'):
            owned.owned_body(content, record, state['slots']['slot-0']['binding']['paragraph'])
    finally: content.close()


@pytest.mark.parametrize('kind', ['anchors', 'decorates', 'decoration_ranges'])
def test_text_only_scope_remains_closed(lifecycle, tmp_path, kind):
    _, _, rows = lifecycle
    pdf, original, _ = rows['first']
    state = deepcopy(original)
    binding = state['slots']['slot-0']['binding']
    if kind == 'anchors': binding['anchors'] = {}
    elif kind == 'decorates': binding['relations'] = [dict(behavior='fixed-to-page', relation='decorates')]
    else: state['paragraphs'][state['slots']['slot-0']['paragraph_id']]['logical']['decoration_ranges'] = [dict(start=0,end=1)]
    state = _reseal(state)
    assert open_shared_flow(pdf, state)['status'] == 'needs_confirmation'
    with pytest.raises(PdfError):
        edit_shared_flow(pdf, state, tmp_path/'bad.pdf', tmp_path/'bad.json', {})
    assert not (tmp_path/'bad.pdf').exists() and not (tmp_path/'bad.json').exists()
