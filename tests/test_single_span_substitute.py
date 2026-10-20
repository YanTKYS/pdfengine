"""Single-Span new glyphs from a caller-approved substitute for characters the original lacks."""
from copy import deepcopy
import hashlib
from io import BytesIO
import json

import pymupdf
import pytest
from fontTools.ttLib import TTFont
from pypdf import PdfReader

from pdfeditor.backend import PdfError
from pdfeditor.span_edit import (_seal, accept_single_span, edit_single_span, open_single_span,
                                 plan_single_span_edit)
from test_single_span_actualtext import REGION, TEXT, glyph_records, pinned_provider, raster, selection, world  # noqa: F401
import page_flow_fixture as fx

# 各 and 種 are fixture characters that the page never paints, so the
# embedded original subset lacks them; 申 and 請 are painted and covered.
MISSING = '各種'


def substitute_bytes(**changes):
    font = TTFont(BytesIO(fx.build_font(family='SubstituteGothic', salt=29)))
    if 'fs_type' in changes:
        font['OS/2'].fsType = changes['fs_type']
    if 'advance' in changes:
        for name in font.getGlyphOrder():
            font['hmtx'][name] = (changes['advance'], font['hmtx'][name][1])
    if 'drop' in changes:
        cmap = font['cmap']
        for table in cmap.tables:
            table.cmap.pop(ord(changes['drop']), None)
    out = BytesIO(); font.save(out)
    return out.getvalue()


def substitute(tmp_path, data=None, name='substitute.ttf'):
    data = substitute_bytes() if data is None else data
    path = tmp_path / name; path.write_bytes(data)
    return dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), font_index=0, relation='substituted')


def save(source, state, root, name, edits, **fonts):
    pdf, sidecar = root / (name + '.pdf'), root / (name + '.json')
    edit_single_span(source, state, pdf, sidecar, edits, **fonts)
    opened = open_single_span(pdf, json.loads(sidecar.read_text()))
    assert opened['status'] == 'restored', opened
    return pdf, opened['state']


def font_programs(path):
    with pymupdf.open(path) as document:
        return {name: hashlib.sha256(document.extract_font(xref)[3]).hexdigest()
                for xref, _, _, _, name, *_ in document[0].get_fonts(full=True)}


def test_original_still_lacks_the_characters(world, tmp_path):
    source, proposal = world
    state = accept_single_span(source, proposal)
    with pytest.raises(PdfError):
        plan_single_span_edit(source, state, [dict(start=4, end=6, text=MISSING)],
                              provider=pinned_provider(source, proposal, tmp_path))


@pytest.mark.parametrize('text', [MISSING, '各請', '申種'])
def test_substitute_supplies_only_missing_characters(world, tmp_path, text):
    source, proposal = world
    state = accept_single_span(source, proposal)
    provider, chosen = pinned_provider(source, proposal, tmp_path), substitute(tmp_path)
    edits = [dict(start=4, end=6, text=text)]
    plan = plan_single_span_edit(source, state, edits, provider=provider, substitute=chosen)
    lacking = ''.join(c for c in text if c in MISSING)
    assert plan['substitute']['characters'] == lacking and plan['substitute']['relation'] == 'substituted'
    assert sum(g['provider'] == 'single-span-substitute' for g in plan['glyph_plan']) == len(lacking)
    assert sum(g['provider'] == 'single-span-provider' for g in plan['glyph_plan']) == len(text) - len(lacking)
    out, current = save(source, state, tmp_path, 'substituted', edits, provider=provider, substitute=chosen)
    assert current['witness']['text'] == TEXT[:4] + text + TEXT[6:]
    assert current['witness']['structure'] == state['witness']['structure']
    assert current['witness']['clip'] == state['witness']['clip']
    assert current['witness']['owner'] == state['witness']['owner'] and current['witness']['mcid'] == state['witness']['mcid']
    old = glyph_records(source, state['selection']['glyph_ids'])
    new = glyph_records(out, current['selection']['glyph_ids'])
    # Unchanged glyphs keep code, GID, resource and font object.
    assert new[:4] == old[:4] and new[6:] == old[6:]
    binding = current['font_binding']
    substitutes = binding['substitutes']
    assert len(substitutes) == 1
    alias, record = next(iter(substitutes.items()))
    assert record['relation'] == 'substituted' and record['provider']['source_sha256'] == chosen['sha256']
    assert set(record['glyphs']) == set(lacking)
    assert len(binding['generated']) == (1 if len(lacking) < len(text) else 0)
    assert alias not in binding['generated'] and alias != binding['source_resource']
    for glyph, c in zip(new[4:6], text):
        assert glyph['text'] == c
        assert (glyph['resource'] == alias) == (c in lacking)
        if c in lacking:
            assert glyph['gid'] == record['glyphs'][c]['gid']
    # The original program object is unchanged.
    assert font_programs(out)[binding['source_resource'][1:]] == binding['source_program_sha256']
    # ActualText follows the drawn text.
    reader = PdfReader(out)
    assert reader.get_object(current['witness']['owner']['xref'])['/ActualText'] == current['witness']['text']


def test_equal_advance_keeps_the_replaced_glyph_positions(world, tmp_path):
    source, proposal = world
    state = accept_single_span(source, proposal)
    edits = [dict(start=4, end=6, text=MISSING)]
    out, current = save(source, state, tmp_path, 'kept', edits, provider=pinned_provider(source, proposal, tmp_path),
                        substitute=substitute(tmp_path))
    with pymupdf.open(source) as a, pymupdf.open(out) as b:
        before = [c[2] for block in a[0].get_texttrace() for c in block['chars']]
        after = [c[2] for block in b[0].get_texttrace() for c in block['chars']]
    assert len(before) == len(after)
    assert all(abs(x - y) < .01 for p, q in zip(before, after) for x, y in zip(p, q))


def test_reopen_second_edit_and_noop_keep_the_substitute_record(world, tmp_path):
    source, proposal = world
    state = accept_single_span(source, proposal)
    provider, chosen = pinned_provider(source, proposal, tmp_path), substitute(tmp_path)
    out, current = save(source, state, tmp_path, 'first', [dict(start=4, end=6, text=MISSING)],
                        provider=provider, substitute=chosen)
    noop, unchanged = save(out, current, tmp_path, 'noop', [])
    assert raster(noop) == raster(out)
    assert unchanged['font_binding'] == current['font_binding']
    # A second edit with original-program characters only takes no substitute.
    again, edited = save(noop, unchanged, tmp_path, 'again', [dict(start=7, end=8, text='申')], provider=provider)
    assert edited['font_binding']['substitutes'] == current['font_binding']['substitutes']
    assert len(edited['font_binding']['generated']) == 1
    # Replacing a substituted glyph with original-program text retains the rest.
    third, last = save(again, edited, tmp_path, 'third', [dict(start=4, end=5, text='申')], provider=provider)
    assert last['witness']['text'] == TEXT[:4] + '申種' + TEXT[6:7] + '申' + TEXT[8:]
    # A later missing character needs the substitute approved again.
    with pytest.raises(PdfError):
        edit_single_span(third, last, tmp_path / 'bad.pdf', tmp_path / 'bad.json', [dict(start=6, end=7, text='各')],
                         provider=provider)
    fourth, final = save(third, last, tmp_path, 'fourth', [dict(start=6, end=7, text='各')],
                         provider=provider, substitute=chosen)
    assert len(final['font_binding']['substitutes']) == 2
    assert final['witness']['structure'] == state['witness']['structure']


REASONS = {'unapproved': 'notdef', 'relation': 'relation=substituted',
           'tampered': 'differ from the approved SHA-256', 'sha': 'differ from the approved SHA-256',
           'embedding': 'embedding flags', 'missing-glyph': 'does not qualify', 'original-as-substitute': 'is the original program',
           'not-needed': 'covers every new character', 'extra-key': 'explicit path, SHA-256',
           'face': 'face index', 'capacity': 'cannot wrap|exceeds'}


@pytest.mark.parametrize('kind', ['unapproved', 'relation', 'tampered', 'sha', 'embedding', 'missing-glyph',
                                  'original-as-substitute', 'not-needed', 'extra-key', 'face', 'capacity'])
def test_substitute_refusals_publish_nothing(world, tmp_path, kind):
    source, proposal = world
    state = deepcopy(proposal); state['status'] = 'confirmed'; state = _seal(state)
    provider, chosen = pinned_provider(source, proposal, tmp_path), substitute(tmp_path)
    edit = dict(start=4, end=6, text=MISSING)
    if kind == 'unapproved':
        chosen = None
    elif kind == 'relation':
        chosen['relation'] = 'confirmed_reflow_provider'
    elif kind == 'tampered':
        (tmp_path / 'substitute.ttf').write_bytes(substitute_bytes(advance=900))
    elif kind == 'sha':
        chosen['sha256'] = '0' * 64
    elif kind == 'embedding':
        chosen = substitute(tmp_path, substitute_bytes(fs_type=2), 'restricted.ttf')
    elif kind == 'missing-glyph':
        chosen = substitute(tmp_path, substitute_bytes(drop='種'), 'lacking.ttf')
    elif kind == 'original-as-substitute':
        chosen = dict(provider, relation='substituted')
    elif kind == 'not-needed':
        edit['text'] = '申請'
    elif kind == 'extra-key':
        chosen['fallback'] = True
    elif kind == 'face':
        chosen['font_index'] = 1
    elif kind == 'capacity':
        edit['text'] = '各種申請' * 10
    with pytest.raises(PdfError, match=REASONS[kind]):
        edit_single_span(source, state, tmp_path / 'bad.pdf', tmp_path / 'bad.json', [edit],
                         provider=provider, substitute=chosen)
    assert not (tmp_path / 'bad.pdf').exists() and not (tmp_path / 'bad.json').exists()


def test_unequal_advance_is_not_kept_in_the_source_slot(world, tmp_path):
    source, proposal = world
    state = accept_single_span(source, proposal)
    wide = substitute(tmp_path, substitute_bytes(advance=1200), 'wide.ttf')
    plan = plan_single_span_edit(source, state, [dict(start=4, end=6, text=MISSING)],
                                 provider=pinned_provider(source, proposal, tmp_path), substitute=wide)
    new = [g for g in plan['glyph_plan'] if g['provider'] == 'single-span-substitute']
    assert [round(g['advance'], 3) for g in new] == [round(10.5 * 1.2, 3)] * 2


@pytest.mark.parametrize('kind', ['gid', 'outline', 'advance', 'unicode', 'relation', 'as-generated', 'unrecorded'])
def test_reopen_refuses_a_changed_substitute_binding(world, tmp_path, kind):
    source, proposal = world
    state = accept_single_span(source, proposal)
    out, current = save(source, state, tmp_path, 'first', [dict(start=4, end=6, text=MISSING)],
                        provider=pinned_provider(source, proposal, tmp_path), substitute=substitute(tmp_path))
    forged = deepcopy(current); binding = forged['font_binding']
    alias = next(iter(binding['substitutes'])); record = binding['substitutes'][alias]
    if kind == 'gid':
        record['glyphs']['各']['gid'] += 1
    elif kind == 'outline':
        record['glyphs']['各']['outline_sha256'] = '0' * 64
    elif kind == 'advance':
        record['glyphs']['各']['advance'] += 1
    elif kind == 'unicode':
        record['glyphs'] = {'申': record['glyphs']['各'], '種': record['glyphs']['種']}
    elif kind == 'relation':
        record['relation'] = 'identical'
    elif kind == 'as-generated':
        # A substitute subset must never pass as an original-program subset.
        binding['generated'][alias] = {k: record[k] for k in ('provider', 'subset_sha256', 'basefont')}
        del binding['substitutes'][alias]
    elif kind == 'unrecorded':
        del binding['substitutes']
    opened = open_single_span(out, _seal(forged))
    assert opened['status'] == 'needs_confirmation'
    assert ('registry' if kind in ('relation', 'as-generated') else
            'not a recorded original-program subset' if kind == 'unrecorded' else
            'Unicode/GID' if kind in ('gid', 'unicode') else 'outline or advance') in opened['reason']


def test_changed_embedded_substitute_glyph_is_refused(world, tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    from pdfeditor.span_edit import _verify_font_binding
    source, proposal = world
    state = accept_single_span(source, proposal)
    out, current = save(source, state, tmp_path, 'first', [dict(start=4, end=6, text=MISSING)],
                        provider=pinned_provider(source, proposal, tmp_path), substitute=substitute(tmp_path))
    alias = next(iter(current['font_binding']['substitutes']))
    gid = current['font_binding']['substitutes'][alias]['glyphs']['各']['gid']
    writer = PdfWriter(PdfReader(out), full=True)
    descriptor = writer.pages[0]['/Resources']['/Font'][alias]['/DescendantFonts'][0].get_object()['/FontDescriptor']
    program = TTFont(BytesIO(descriptor['/FontFile2'].get_data()))
    program['glyf'][program.getGlyphName(gid)].coordinates[0] = (10, 10)
    data = BytesIO(); program.save(data)
    stream = DecodedStreamObject(); stream.set_data(data.getvalue())
    descriptor[NameObject('/FontFile2')] = writer._add_object(stream)
    changed = tmp_path / 'changed.pdf'; writer.write(changed)
    forged = deepcopy(current['font_binding'])
    forged['substitutes'][alias]['subset_sha256'] = hashlib.sha256(data.getvalue()).hexdigest()
    with pytest.raises(PdfError, match='outline or advance'):
        _verify_font_binding(changed, current['paragraph'], forged)
