"""B1 ordinary-page bootstrap: propose -> explicit accept -> existing shared-flow v2 editing."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pymupdf
import pytest

from pdfeditor.attributed import digest, inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.cli import main
from pdfeditor.page_proposal import (_decimal_quantum, accept_page_flow, accept_page_flow_report,
                                     default_font_roots, discover_font_files, propose_page_flow, replace_in_flow)
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow
from pdfeditor.story_flow import confirm_story

import page_flow_fixture as fx


@pytest.fixture(scope='module')
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp('page-proposal')
    fonts = fx.write_fonts(root / 'all', mismatch=dict(advance=980), ambiguous=dict(family='PdfengineFixtureGothic', salt=3),
                           missing=dict(drop=('申',)))
    roots = {}
    for name, members in dict(full=['full'], ambiguous=['full', 'ambiguous'], mismatch=['mismatch'],
                              missing=['missing'], empty=[]).items():
        roots[name] = root / ('roots-' + name)
        roots[name].mkdir()
        for member in members:
            shutil.copy(fonts[member], roots[name] / fonts[member].name)
    pdf = fx.make_page(root / 'page.pdf', fonts['full'].read_bytes())
    proposal = propose_page_flow(pdf, 1, font_roots=[roots['full']])
    return dict(root=root, fonts=fonts, roots=roots, pdf=pdf, proposal=proposal)


def lines(path):
    with pymupdf.open(path) as doc:
        return [(round(line['spans'][0]['origin'][1], 3), ''.join(s['text'] for s in line['spans']))
                for block in doc[0].get_text('dict')['blocks'] for line in block.get('lines', [])]


def pixels(path, rect):
    with pymupdf.open(path) as doc:
        return doc[0].get_pixmap(dpi=144, clip=pymupdf.Rect(*rect)).samples


def resealed(proposal, change):
    value = deepcopy(proposal)
    value.pop('digest')
    change(value)
    value['digest'] = digest(value)
    return value


# -- proposal -----------------------------------------------------------------------

def test_proposal_reads_an_ordinary_page_with_evidence(world):
    p, before = world['proposal'], source_sha(world['pdf'])
    assert p['schema'] == 'pdfengine-page-flow-proposal-1' and p['status'] == 'proposed'
    assert p['refusals'] == [] and p['unresolved'] == []
    assert p['binding']['source_sha256'] == before and p['binding']['page'] == 1
    assert [para['text'] for para in p['paragraphs']] == list(fx.PARAGRAPHS)
    assert [para['line_ids'] for para in p['paragraphs']] == [['p1-l2', 'p1-l3'], ['p1-l4'], ['p1-l5', 'p1-l6']]
    region = p['region']
    # Observed lines bound the width; margin symmetry is chosen only because it lies inside those bounds.
    assert region['evidence']['width'] == 'margin-symmetry'
    interval = region['width_interval']
    assert interval['lower'] == pytest.approx(40 * fx.SIZE, abs=1e-4)
    assert interval['upper'] == pytest.approx(41 * fx.SIZE, abs=1e-4)
    assert interval['lower'] <= region['width'] < interval['upper']
    assert region['width'] == pytest.approx(fx.PAGE[0] - 2 * fx.MARGIN, abs=1e-4)
    assert region['evidence']['top'] == 'foreign-content-boundary'      # the title bounds the column
    assert region['evidence']['bottom'] in ('margin-symmetry', 'foreign-content-boundary')
    assert region['bounds'][3] < 800 - fx.SIZE                            # the footer stays outside
    assert [f['minimum_baseline_gap'] for f in p['follows']] == [fx.PITCH + fx.SPACE_AFTER] * 2
    assert {f['evidence'] for f in p['follows']} == {'observed-baseline-gap'}
    for para in p['paragraphs']:
        assert para['policy']['first_line_indent']['value'] == pytest.approx(fx.SIZE, abs=1e-4)
        assert para['policy']['min_line_height']['value'] == pytest.approx(fx.PITCH)
    style, = p['styles']
    assert style['providers']['outcome'] == 'unique-metric-verified'
    assert style['physical_styles'] == ['P1:s0', 'P2:s0', 'P3:s0']
    assert {f.get('text') for f in p['foreign_content']} >= {fx.TITLE, fx.FOOTER}
    assert p['reproduction']['status'] == 'reproduced' and p['reproduction']['lines'] == 5
    assert source_sha(world['pdf']) == before


def test_proposal_is_deterministic_and_independent_of_root_order(world, tmp_path):
    other = tmp_path / 'copy'
    shutil.copytree(world['roots']['full'], other)
    a = propose_page_flow(world['pdf'], 1, font_roots=[world['roots']['full'], other])
    b = propose_page_flow(world['pdf'], 1, font_roots=[other, world['roots']['full']])
    assert a == b
    assert propose_page_flow(world['pdf'], 1, font_roots=[world['roots']['full']]) == world['proposal']
    verified = a['styles'][0]['providers']['verified']
    assert len(verified) == 1          # the same font bytes in two places are one identity


# -- primary acceptance scenario ----------------------------------------------------------

def test_ordinary_page_edits_through_propose_accept_replace(world, tmp_path):
    """Caller input: the PDF, page 1, the find text and the replacement. Fonts come from discovery."""
    pdf = world['pdf']
    state = accept_page_flow(pdf, world['proposal'])
    assert state['schema'] == 'pdfengine-shared-flow-2'
    request = replace_in_flow(state, *fx.FIRST_EDIT)
    assert request == {'P2': {'edits': [dict(start=0, end=3, text='各種申請書', style_id='style-1')]}}
    edit_shared_flow(pdf, state, tmp_path / 'rev1.pdf', tmp_path / 'rev1.json', request)
    first = open_shared_flow(tmp_path / 'rev1.pdf', json.loads((tmp_path / 'rev1.json').read_text()))
    assert first['status'] == 'restored', first.get('reason')
    original, rev1 = lines(pdf), lines(tmp_path / 'rev1.pdf')
    assert rev1[3] == (205.0, '各種申請書を提出してください。')
    assert [l for i, l in enumerate(rev1) if i != 3] == [l for i, l in enumerate(original) if i != 3]

    second = replace_in_flow(first['state'], *fx.SECOND_EDIT)
    edit_shared_flow(tmp_path / 'rev1.pdf', first['state'], tmp_path / 'rev2.pdf', tmp_path / 'rev2.json', second)
    again = open_shared_flow(tmp_path / 'rev2.pdf', json.loads((tmp_path / 'rev2.json').read_text()))
    assert again['status'] == 'restored', again.get('reason')
    rev2 = lines(tmp_path / 'rev2.pdf')
    p2 = '各種申請書を必要書類一式とあわせて、担当窓口へ持参または郵送により提出してください。'
    assert rev2[3:5] == [(205.0, p2[:39]), (223.0, p2[39:])]            # natural wrap at the proposed width
    assert rev2[5:7] == [(250.0, original[4][1]), (268.0, original[5][1])]   # P3 pushed down by one line
    assert rev2[5][0] - rev2[4][0] == fx.PITCH + fx.SPACE_AFTER              # observed gap kept
    assert rev2[:3] == original[:3] and rev2[-1] == original[-1]
    title = (0, 100, fx.PAGE[0], 126)
    footer = (0, 785, fx.PAGE[0], 815)
    for rect in (title, footer):
        assert pixels(pdf, rect) == pixels(tmp_path / 'rev1.pdf', rect) == pixels(tmp_path / 'rev2.pdf', rect)


def test_accepted_state_equals_the_hand_written_expert_state(world):
    """An expert writing the proposal's values into the existing APIs gets the identical state."""
    pdf, p = world['pdf'], world['proposal']
    region = p['region']
    width, height = p['binding']['page_size']
    protected = []
    for f in p['foreign_content']:
        x0, y0, x1, y1 = f['bounds']
        box = [max(x0, 0), max(y0, 0), min(x1, width), min(y1, height)]
        if box[2] > box[0] and box[3] > box[1]:
            protected.append(dict(bounds=box, role='fixed'))
    style = p['styles'][0]
    provider = style['providers']['verified'][0]['path']
    stories = {}
    for para in p['paragraphs']:
        snapshot = inspect_paragraph(pdf, make_selection(pdf, 1, glyph_ids=para['glyph_ids'],
                                                         explicit_width=region['width']),
                                     line_joiner=para['line_joiner'])
        stories[para['id']] = confirm_story(pdf, {'part': dict(page=1, bounds=region['bounds'], paragraph=snapshot,
            paint_relations=[], layout=dict(x=region['x'], baseline=para['first_baseline'], width=region['width'],
                max_bottom=region['bounds'][3], min_line_height=para['policy']['min_line_height']['value'],
                first_line_indent=0))},
            paragraph_id=para['id'], chain=['part'], protected_regions={'1': protected},
            styles={style['id']: dict(provider=dict(path=provider), provider_relation='confirmed_reflow_provider')},
            style_assignments={'part': {'s0': style['id']}}, typing_style_id=style['id'])
    ids = [para['id'] for para in p['paragraphs']]
    expert = confirm_shared_flow(pdf, stories, flow_id='page1-flow', paragraph_order=ids,
        regions={'R1': dict(page=1, bounds=region['bounds'], x=region['x'], width=region['width'],
                            first_baseline=region['first_baseline'])},
        region_order=['R1'], slot_regions={i: {'part': 'R1'} for i in ids},
        paragraph_policies={para['id']: {k: para['policy'][k]['value'] for k in ('min_line_height', 'first_line_indent',
                            'keep_together', 'break_before', 'break_after', 'empty')} for para in p['paragraphs']},
        follows=[dict(before=f['before'], after=f['after'], minimum_baseline_gap=f['minimum_baseline_gap'],
                      region_start='reset-to-region-baseline') for f in p['follows']],
        protected_regions={'1': protected})
    accepted = accept_page_flow(pdf, p)
    assert json.dumps(accepted, sort_keys=True) == json.dumps(expert, sort_keys=True)
    assert accepted['model_sha256'] == expert['model_sha256']


def test_a_break_the_measure_cannot_explain_is_a_paragraph_boundary(world, tmp_path):
    """Inference joins two single-line paragraphs; the page measure splits them again."""
    first, second = fx.PARAGRAPHS[1], fx.PARAGRAPHS[2][-11:]          # the first line is the widest
    pdf = fx.make_page(tmp_path / 'merged.pdf', world['fonts']['full'].read_bytes(),
                       lines=[(fx.MARGIN, 160.0, fx.SIZE, first), (fx.MARGIN, 184.0, fx.SIZE, second)])
    p = propose_page_flow(pdf, 1, font_roots=[world['roots']['full']])
    assert p['status'] == 'proposed', p['refusals']
    assert [para['text'] for para in p['paragraphs']] == [first, second]
    assert p['paragraphs'][1]['evidence'] == 'break-not-explained-by-width'
    assert p['region']['evidence']['width'] == 'margin-symmetry'
    assert p['region']['width_interval']['upper'] is None        # a sentence end is no wrap evidence
    assert p['follows'][0]['minimum_baseline_gap'] == 24.0


# -- binding ---------------------------------------------------------------------------

def test_modified_stale_or_foreign_proposals_are_refused(world, tmp_path):
    pdf, p = world['pdf'], world['proposal']
    edited = deepcopy(p)
    edited['region']['width'] += 5
    with pytest.raises(PdfError, match='modified'):
        accept_page_flow(pdf, edited)
    forged = resealed(p, lambda v: v['region'].update(width=v['region']['width'] + 5))
    with pytest.raises(PdfError, match='stale'):
        accept_page_flow(pdf, forged)
    observed = resealed(p, lambda v: v['binding'].update(page_observation_sha256='0' * 64))
    with pytest.raises(PdfError, match='page observation changed'):
        accept_page_flow(pdf, observed)
    other = fx.make_page(tmp_path / 'other.pdf', world['fonts']['full'].read_bytes(), tagged=True)
    with pytest.raises(PdfError, match='another PDF revision'):
        accept_page_flow(other, p)
    with pytest.raises(PdfError, match='not a page-flow proposal'):
        accept_page_flow(pdf, dict(p, schema='something-else'))


# -- fonts -------------------------------------------------------------------------------

def test_wrong_metrics_and_missing_glyphs_never_qualify(world):
    for root, reason in (('mismatch', 'differs from the'), ('missing', 'missing glyph U+7533')):
        p = propose_page_flow(world['pdf'], 1, font_roots=[world['roots'][root]])
        providers = p['styles'][0]['providers']
        assert p['status'] == 'unresolved' and providers['outcome'] == 'no-metric-verified-provider'
        assert providers['verified'] == []
        # Same family name as the source font: listed for review, but names never qualify.
        assert reason in providers['name_matched_rejections'][0]['reason']
        with pytest.raises(PdfError, match='no-metric-verified-provider'):
            accept_page_flow(world['pdf'], p)
    empty = propose_page_flow(world['pdf'], 1, font_roots=[world['roots']['empty']])
    assert empty['unresolved'][0]['code'] == 'no-metric-verified-provider'
    assert empty['reproduction']['status'] == 'pending'


def test_ambiguous_verified_fonts_need_an_explicit_choice(world):
    p = propose_page_flow(world['pdf'], 1, font_roots=[world['roots']['ambiguous']])
    verified = p['styles'][0]['providers']['verified']
    assert p['status'] == 'needs-choice' and len(verified) == 2
    assert verified[0]['name_match'] and not verified[1]['name_match']   # names only order the list
    with pytest.raises(PdfError, match='ambiguous-metric-verified'):
        accept_page_flow(world['pdf'], p)
    with pytest.raises(PdfError, match='not a metric-verified candidate'):
        accept_page_flow(world['pdf'], p, provider_choices={'style-1': dict(sha256='0' * 64)})
    chosen = verified[1]
    result = accept_page_flow_report(world['pdf'], p, provider_choices={'style-1': dict(sha256=chosen['sha256'])})
    registry = result['state']['paragraphs']['P1']['style_registry']['style-1']
    assert registry['reflow_provider']['sha256'] == chosen['sha256']
    assert result['receipt']['providers']['style-1']['provenance'] == 'caller-choice'


def test_decimal_quantum_is_derived_from_the_written_width():
    assert _decimal_quantum(1000.0) == (1000, pytest.approx(0.5))
    value, quantum = _decimal_quantum(488.28)
    assert str(value) == '12207/25' and quantum == pytest.approx(0.005)
    with pytest.raises(PdfError):
        _decimal_quantum(1e-20)


def test_installed_font_roots_and_discovery_are_deterministic(tmp_path):
    windows = default_font_roots('win32', {'WINDIR': 'D:\\Win', 'LOCALAPPDATA': 'C:\\Users\\u\\AppData\\Local'})
    assert windows == ['D:\\Win\\Fonts', 'C:\\Users\\u\\AppData\\Local\\Microsoft\\Windows\\Fonts']
    assert default_font_roots('win32', {})[0] == 'C:\\Windows\\Fonts'
    mac = default_font_roots('darwin', {}, home='/Users/u')
    assert mac[-1] == '/Users/u/Library/Fonts' and '/Library/Fonts' in mac
    linux = default_font_roots('linux', {'XDG_DATA_DIRS': '/opt/share:/usr/share'}, home='/home/u')
    assert linux == ['/home/u/.local/share/fonts', '/home/u/.fonts', '/opt/share/fonts', '/usr/share/fonts']
    for name in ('b.TTF', 'a.ttc', 'c.otf', 'skip.woff', 'sub/d.ttf'):
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_bytes(b'')
    found = discover_font_files([str(tmp_path), str(tmp_path / 'missing')])
    assert [Path(f).relative_to(tmp_path).as_posix() for f in found] == ['a.ttc', 'b.TTF', 'c.otf', 'sub/d.ttf']


# -- layout refusals ---------------------------------------------------------------------------

def test_tagged_page_is_refused_for_the_persistent_route(world, tmp_path):
    pdf = fx.make_page(tmp_path / 'tagged.pdf', world['fonts']['full'].read_bytes(), tagged=True)
    p = propose_page_flow(pdf, 1, font_roots=[world['roots']['full']])
    assert p['status'] == 'refused'
    refusal, = [r for r in p['refusals'] if r['code'] == 'unsupported-for-persistent-flow']
    assert refusal['detail'].startswith('tagged-page: ')
    with pytest.raises(PdfError, match='unsupported-for-persistent-flow'):
        accept_page_flow(pdf, p)


def two_columns():
    left, right = fx.PARAGRAPHS[0][:36], fx.PARAGRAPHS[2][:36]
    result = []
    for i in range(2):
        result.append((fx.MARGIN, 160.0 + i * fx.PITCH, fx.SIZE, left[i * 18:(i + 1) * 18]))
        result.append((320.0, 160.0 + i * fx.PITCH, fx.SIZE, right[i * 18:(i + 1) * 18]))
    return result


@pytest.mark.parametrize('name, options, code', [
    ('columns', dict(lines=two_columns()), 'multiple-columns'),
    ('foreign', dict(drawings=[(150.0, 210.0, 300.0, 222.0)]), 'region'),
    ('rotated', dict(rotate=90), 'rotated-page'),
    ('vertical', dict(matrix=lambda x, y: f'0 1 -1 0 {x:g} {y:g}'), 'no-horizontal-text'),
])
def test_layouts_outside_the_one_column_scope_are_refused(world, tmp_path, name, options, code):
    pdf = fx.make_page(tmp_path / f'{name}.pdf', world['fonts']['full'].read_bytes(), **options)
    before = source_sha(pdf)
    p = propose_page_flow(pdf, 1, font_roots=[world['roots']['full']])
    assert p['status'] == 'refused' and code in [r['code'] for r in p['refusals']], p['refusals']
    with pytest.raises(PdfError, match='cannot be accepted'):
        accept_page_flow(pdf, p)
    assert source_sha(pdf) == before
    assert sorted(f.name for f in tmp_path.iterdir()) == [f'{name}.pdf']


def test_justified_source_lines_are_not_silently_reflowed(world, tmp_path):
    """A wrapped line spaced out to the measure cannot be reproduced by a left-aligned flow."""
    text = fx.PARAGRAPHS[0]
    spaced = [(fx.MARGIN + fx.SIZE, 160.0, fx.SIZE, text[:37], 2 * fx.SIZE / 36),
              (fx.MARGIN, 178.0, fx.SIZE, text[37:77])]
    pdf = fx.make_page(tmp_path / 'justified.pdf', world['fonts']['full'].read_bytes(), lines=spaced)
    p = propose_page_flow(pdf, 1, font_roots=[world['roots']['full']])
    assert p['status'] == 'refused'
    # Either the existing style guard (varying tracking) or the reproduction check refuses it.
    assert [r['code'] for r in p['refusals']] in (['layout-not-reproduced'], ['shared-flow-confirmation']), p['refusals']
    with pytest.raises(PdfError, match='cannot be accepted'):
        accept_page_flow(pdf, p)


# -- overrides and paragraph selection -----------------------------------------------------------

def test_overrides_are_explicit_validated_and_recorded(world):
    pdf, p = world['pdf'], world['proposal']
    with pytest.raises(PdfError, match='unsupported override'):
        accept_page_flow(pdf, p, overrides=dict(gap=3))
    with pytest.raises(PdfError, match='width override'):
        accept_page_flow(pdf, p, overrides=dict(width=p['region']['width_interval']['upper']))
    with pytest.raises(PdfError, match='width override'):
        accept_page_flow(pdf, p, overrides=dict(width=300))
    with pytest.raises(PdfError, match='finite number'):
        accept_page_flow(pdf, p, overrides=dict(width='wide'))
    result = accept_page_flow_report(pdf, p, overrides=dict(width=422.0))
    assert result['state']['regions']['R1']['width'] == 422.0
    assert result['receipt']['overrides'] == {'width': dict(value=422.0, provenance='caller-override')}
    assert result['receipt']['reproduction']['status'] == 'reproduced'
    with pytest.raises(PdfError, match='consecutive'):
        accept_page_flow(pdf, p, paragraph_ids=['P1', 'P3'])
    partial = accept_page_flow_report(pdf, p, paragraph_ids=['P2', 'P3'])
    state = partial['state']
    assert state['flow']['paragraphs'] == ['P2', 'P3'] and partial['receipt']['paragraph_selection'] == 'caller-selected'
    assert state['regions']['R1']['bounds'][1] >= p['paragraphs'][0]['bounds'][3]   # P1 is foreign now
    split = accept_page_flow(pdf, p, overrides=dict(paragraph_starts=['p1-l2', 'p1-l4', 'p1-l5']))
    assert split['flow']['paragraphs'] == ['P1', 'P2', 'P3']
    with pytest.raises(PdfError, match='paragraph_starts'):
        accept_page_flow(pdf, p, overrides=dict(paragraph_starts=['p1-l4']))


# -- replace_in_flow -----------------------------------------------------------------------------

def test_replace_in_flow_needs_one_unambiguous_match_and_writes_nothing(world):
    state = accept_page_flow(world['pdf'], world['proposal'])
    frozen = deepcopy(state)
    with pytest.raises(PdfError, match='does not occur'):
        replace_in_flow(state, '存在しない', 'x')
    with pytest.raises(PdfError, match='occurs 3 times'):
        replace_in_flow(state, 'ください', 'x')
    assert replace_in_flow(state, 'ください', 'x', paragraph_id='P2') == {
        'P2': {'edits': [dict(start=8, end=12, text='x', style_id='style-1')]}}
    with pytest.raises(PdfError, match='unknown paragraph_id'):
        replace_in_flow(state, '申請書', 'x', paragraph_id='P9')
    with pytest.raises(PdfError, match='unknown style_id'):
        replace_in_flow(state, '申請書', 'x', style_id='style-9')
    assert state == frozen


# -- CLI --------------------------------------------------------------------------------------------

def test_cli_propose_accept_replace(world, tmp_path, capsys):
    pdf = world['pdf']
    root = str(world['roots']['full'])
    assert main(['propose-page', str(pdf), '--page', '1', '--json', str(tmp_path / 'p.json'), '--font-root', root]) == 0
    assert main(['accept-page', str(pdf), '--proposal', str(tmp_path / 'p.json'), '--json', str(tmp_path / 'flow.json'),
                 '--receipt', str(tmp_path / 'receipt.json')]) == 0
    assert main(['replace-text', str(pdf), str(tmp_path / 'out.pdf'), '--state', str(tmp_path / 'flow.json'),
                 '--state-output', str(tmp_path / 'out.json'), '--find', '申請書', '--replacement', '各種申請書']) == 0
    assert lines(tmp_path / 'out.pdf')[3] == (205.0, '各種申請書を提出してください。')
    assert main(['replace-text', str(pdf), str(tmp_path / 'bad.pdf'), '--state', str(tmp_path / 'flow.json'),
                 '--state-output', str(tmp_path / 'bad.json'), '--find', 'ください', '--replacement', 'x']) == 2
    assert 'occurs 3 times' in capsys.readouterr().err
    assert not (tmp_path / 'bad.pdf').exists()
