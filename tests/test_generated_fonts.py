"""Lifecycle of pdfengine-generated fonts across shared-flow re-saves."""
from copy import deepcopy
import json
import os

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from evaluations.continuation.resources import generated_block_bytes, inventory
from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import confirm_continuation_destination, slot_id
from pdfeditor.document_flow import _reseal
from pdfeditor.pdf_save import _written_value, embedded_font_sha256
from pdfeditor.selection import make_selection
from pdfeditor.shaped_font import ShapedFont
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow
from pdfeditor.story_flow import confirm_story
from pdfeditor.transaction import Transaction
from test_continuation import LONG, change, prepared, saved


def _fonts(path, page):
    fonts = PdfReader(path).pages[page - 1]['/Resources'].get_object().get('/Font')
    return fonts.get_object() if fonts is not None else DictionaryObject()


def _tf_aliases(path, page):
    data = PdfReader(path).pages[page - 1].get_contents().get_data()
    return {str(op.args[0]) for op in operators(data) if op.name == 'Tf'}


def _type0_programs(path):
    reader = PdfReader(path)
    numbers = {n for table in reader.xref.values() for n in table}
    return {embedded_font_sha256(reader.get_object(n)) for n in numbers
            if isinstance(reader.get_object(n), DictionaryObject) and reader.get_object(n).get('/Subtype') == '/Type0'}


def _measure(path, state, source):
    inv = inventory(path, records=state.get('generated_fonts'), source=source)
    return dict(pages={p: [(e['alias'], e['kind'], e['program_sha256']) for e in v] for p, v in inv['pages'].items()},
                font_entries={p: len(v) for p, v in inv['pages'].items()}, type0=inv['type0_fonts'],
                owned_roots=inv['owned_font_roots'], owned_objects=inv['owned_font_graph_objects'],
                size=os.path.getsize(path))


def test_generated_fonts_stay_bounded_through_edits_and_repeated_noops(tmp_path):
    source, state = prepared(tmp_path, spacing=True)
    original = source
    ident = slot_id(state['continuation_destinations']['empty'])
    destination = state['continuation_destinations']['empty']
    measures, records, creation = {}, {}, None
    for name, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('shorten', 'AB'),
                       ('regrow', LONG), ('noop1', None), ('noop2', None), ('noop3', None)]:
        out, updated, report = saved(tmp_path, source, state, {} if text is None else change(state, text), name)
        assert ident in updated['slots'] and set(updated['slots']) == {'slot-0', ident}
        creation = creation or updated['slots'][ident]['creation_binding']
        assert updated['slots'][ident]['creation_binding'] == creation
        assert report['plan']['new_slots'] == ({} if name != 'grow' else report['plan']['new_slots'])
        measures[name] = _measure(out, updated, original)
        records[name] = deepcopy(updated['generated_fonts'])
        outcome = report['generated_font_outcome']
        # Every generated alias is recorded, owned and never a source alias.
        for page, entries in measures[name]['pages'].items():
            owned = {a for a, kind, _ in entries if kind == 'pdfengine-owned'}
            assert owned == set(records[name].get(page, {}))
            assert {a for a, kind, _ in entries if kind == 'original'} == {'/Regular', '/Bold'}
            assert not [a for a, kind, _ in entries if kind == 'unknown']
        if name == 'grow':
            assert set(outcome['2'].values()) == {'added'}
        elif name == 'second':
            # The page-2 glyph set changed: the alias is re-targeted and the old
            # subset leaves the file; no second generated font appears.
            assert set(outcome['2'].values()) == {'replaced'}
            old = {r['subset_sha256'] for r in records['grow']['2'].values()}
            assert not old & _type0_programs(out)
        elif name == 'shorten':
            generated = updated['slots'][ident]
            assert generated['occupancy'] is None and not generated['binding']['paragraph']['text']
            assert generated_block_bytes(out, 2, destination) > 0
            assert updated['destination_bindings']['empty']['program_sha256']
            # The dormant witnesses still select the page-2 alias with Tf, so
            # its resource must exist, but nothing on page 2 is painted with it.
            alias, = records[name]['2']
            assert alias in _tf_aliases(out, 2)
            assert '2' not in outcome
            with pymupdf.open(out) as doc:
                assert not doc[1].get_text(clip=pymupdf.Rect(destination['bounds'])).strip()
        elif name == 'regrow':
            assert set(records[name]['2']) == set(records['second']['2'])
            assert set(outcome['2'].values()) <= {'reused', 'replaced'}
        else:
            # A no-op creates no font object: every alias keeps its existing graph.
            assert all(set(v.values()) == {'reused'} for v in outcome.values())
            assert records[name] == records['regrow']
            with pymupdf.open(source) as a, pymupdf.open(out) as b:
                assert all(a[i].get_pixmap(dpi=144).samples == b[i].get_pixmap(dpi=144).samples for i in range(len(a)))
        source, state = out, updated
    counts = {k: {f: m[f] for f in ('font_entries', 'type0', 'owned_roots', 'owned_objects')} for k, m in measures.items()}
    assert counts['noop1'] == counts['noop2'] == counts['noop3'] == counts['regrow'] == counts['grow']
    assert counts['second'] == counts['grow'] and counts['shorten']['type0'] <= counts['grow']['type0']
    assert measures['noop3']['pages'] == measures['regrow']['pages']


def _foreign_source(tmp_path, font):
    """Three pages inheriting one shared resource dictionary from the page tree.

    It holds source Type1 fonts and a foreign /PRF1 whose structure is exactly
    what pdfengine writes, but for which no flow record exists.
    """
    writer = PdfWriter()
    def type1(name):
        return writer._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject(name), NameObject('/Encoding'): NameObject('/WinAnsiEncoding'),
            NameObject('/FirstChar'): NumberObject(32), NameObject('/LastChar'): NumberObject(126),
            NameObject('/Widths'): ArrayObject([NumberObject(600)] * 95)}))
    shaped = ShapedFont(font)
    foreign = shaped.resource([shaped.shape('XY')])
    fonts = writer._add_object(DictionaryObject({NameObject('/Regular'): type1('/Courier'),
        NameObject('/Bold'): type1('/Courier-Bold'), NameObject('/PRF1'): foreign.build(writer)}))
    resources = writer._add_object(DictionaryObject({NameObject('/Font'): fonts}))
    programs = [b'BT /Regular 12 Tf 20 200 Td (ABCD) Tj ET BT /Bold 10 Tf 200 200 Td (KEEP) Tj ET '
                b'BT /PRF1 10 Tf 20 20 Td <0001> Tj ET',
                b'BT /Bold 10 Tf 20 20 Td (P2) Tj ET',
                b'BT /Bold 12 Tf 20 200 Td (OTHER PAGE) Tj ET']
    for program in programs:
        page = writer.add_blank_page(320, 260)
        if '/Resources' in page:
            del page['/Resources']
        stream = DecodedStreamObject()
        stream.set_data(program)
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer._root_object['/Pages'].get_object()[NameObject('/Resources')] = resources
    source = tmp_path / 'foreign.pdf'
    writer.write(source)
    return source


def _foreign_flow(tmp_path):
    font = tmp_path / 'font.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    source = _foreign_source(tmp_path, font)
    p = inspect_paragraph(source, make_selection(source, glyph_ids=list(range(4)), explicit_width=150))
    story = confirm_story(source, {'original': dict(page=1, bounds=[18, 40, 173, 78], paragraph=p, paint_relations=[],
        layout=dict(x=20, baseline=60, width=150, max_bottom=78, min_line_height=22, first_line_indent=0))},
        paragraph_id='A', chain=['original'], protected_regions={},
        styles={'body': dict(provider=dict(path=str(font)), provider_relation='substituted')},
        style_assignments={'original': {'s0': 'body'}}, typing_style_id='body')
    regions = {'R1': dict(page=1, bounds=[18, 40, 173, 78], x=20, width=150, first_baseline=60),
               'R2': dict(page=2, bounds=[18, 90, 173, 220], x=20, width=150, first_baseline=110)}
    destination = confirm_continuation_destination(source, destination_id='empty', paragraph_id='A', region_id='R2',
        page=2, bounds=regions['R2']['bounds'], insertion='before-page-program', graphics_state='isolated-pdf-initial-state')
    state = confirm_shared_flow(source, {'A': story}, flow_id='flow', paragraph_order=['A'], regions=regions,
        region_order=['R1', 'R2'], slot_regions={'A': {'original': 'R1'}},
        paragraph_policies={'A': dict(min_line_height=22, first_line_indent=5, keep_together=False, break_before='auto',
                                      break_after='auto', empty=dict(kind='reserve-line', ascent=10, descent=3))},
        follows=[], protected_regions={}, continuation_destinations={'empty': destination})
    return source, state


def test_source_shared_inherited_and_foreign_prf_fonts_are_never_retargeted(tmp_path):
    source, state = _foreign_flow(tmp_path)
    original = source
    reference = {page: {str(a): _written_value(f) for a, f in _fonts(original, page).items()} for page in (1, 2, 3)}
    with pymupdf.open(original) as doc:
        other_page = doc[2].get_pixmap(dpi=144).samples
    for name, text in [('grow', LONG), ('second', LONG.replace('twelve', 'TWELVE')), ('noop', None)]:
        out, state, report = saved(tmp_path, source, state, {} if text is None else change(state, text), name)
        for page in (1, 2, 3):
            fonts = _fonts(out, page)
            # Source fonts, including the foreign generated-looking /PRF1, keep
            # their exact written value whether used, unused or inherited.
            assert {a: _written_value(fonts[a]) for a in reference[page]} == reference[page]
        assert set(_fonts(out, 3)) == set(reference[3])
        assert all('/PRF1' not in aliases for aliases in state['generated_fonts'].values())
        assert all(set(v) <= {'/PRF2'} for v in state['generated_fonts'].values())
        kinds = {e['alias']: e['kind'] for e in inventory(out, records=state['generated_fonts'], source=original)['pages']['1']}
        assert kinds['/PRF1'] == 'original' and kinds['/Regular'] == 'original' and kinds['/PRF2'] == 'pdfengine-owned'
        with pymupdf.open(out) as doc:
            assert doc[2].get_pixmap(dpi=144).samples == other_page
            assert 'X' in doc[0].get_text() and 'KEEP' in doc[0].get_text()
        source = out


def test_reservation_needs_ownership_evidence_and_released_glyphs(tmp_path):
    source, state = prepared(tmp_path)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    records = state['generated_fonts']['2']
    alias, = records
    content = ContentPage(out, 2)
    try:
        painted = {i for e in content.events if e.state.font and e.state.font.name == alias for c in e.chars for i in c.source_orders}
    finally:
        content.close()
    assert painted
    with Transaction(out) as transaction:
        page = transaction.page(2)
        page.own_fonts(records)
        assert page.reserve_font_alias('PRF') != alias  # glyphs it paints are not consumed
        assert page.reserve_font_alias('PRF', consumed=frozenset(painted), retained=frozenset({alias})) != alias
        # Another slot never takes this slot's alias, even with its glyphs consumed.
        assert page.reserve_font_alias('PRF', consumed=frozenset(painted), owner='slot-0') != alias
        assert page.reserve_font_alias('PRF', consumed=frozenset(painted), owner=records[alias]['slot_id']) == alias
        assert page.font_replacements() == {alias: records[alias]['subset_sha256']}
    with Transaction(out) as transaction:
        # Without a record the same alias is never ownership, however it looks.
        assert transaction.page(2).reserve_font_alias('PRF', consumed=frozenset(painted)) != alias
    for bad in ({alias: dict(records[alias], subset_sha256='0' * 64)}, {'/Regular': records[alias]}, {'/Missing': records[alias]}):
        with Transaction(out) as transaction, pytest.raises(PdfError, match='ownership evidence'):
            transaction.page(2).own_fonts(bad)


def test_tampered_font_record_is_not_ownership(tmp_path):
    source, state = prepared(tmp_path)
    out, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    side = tmp_path / 'grow.json'
    for field, value in (('subset_sha256', '0' * 64), ('slot_id', 'slot-0'), ('basefont', 'AAAAAA+Other')):
        bad = deepcopy(state)
        record = next(iter(bad['generated_fonts']['2'].values()))
        record[field] = value
        assert open_shared_flow(out, _reseal(bad))['status'] == 'needs_confirmation'
    bad = deepcopy(state)
    bad['generated_fonts']['1'] = {'/Regular': next(iter(state['generated_fonts']['2'].values()))}
    assert open_shared_flow(out, _reseal(bad))['status'] == 'needs_confirmation'
    assert open_shared_flow(out, side)['status'] == 'restored'


@pytest.mark.parametrize('phase', ['retarget', 'records'])
def test_font_lifecycle_failure_publishes_neither_artifact(tmp_path, monkeypatch, phase):
    import pdfeditor.pdf_save as pdf_save
    import pdfeditor.shared_flow as shared
    source, state = prepared(tmp_path)
    source, state, _ = saved(tmp_path, source, state, change(state, LONG), 'grow')
    if phase == 'retarget':
        # The save-time re-verification of ownership fails after planning.
        monkeypatch.setattr(pdf_save, 'embedded_font_sha256', lambda font: '0' * 64)
    else:
        def fail(*args, **kwargs):
            raise PdfError('injected font record failure')
        monkeypatch.setattr(shared, '_generated_fonts', fail)
    with pytest.raises(PdfError):
        edit_shared_flow(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', {})
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()
