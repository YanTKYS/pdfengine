"""L2: canonical semantic island writer (private candidates only)."""
from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor import semantic_island as island
from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_writer as writer
from pdfeditor import source_ownership as owned
from pdfeditor.transaction import Transaction
from test_semantic_layout import make_flow, physical_variant, resealed, statement

EDGE = dict(left=1, right=2, delta='6/5', operator='TJ', semantics='confirmed-adjacent-pair',
            boundary_policy='suppress-at-line-end')


def build(pdf, sidecar, request, root, **kwargs):
    return writer.build_semantic_candidate(pdf, sidecar, request, workspace=root / 'work', **kwargs)


def body_of(pdf, sidecar):
    state = json.loads(Path(sidecar).read_text())
    record = next(iter(state['slots'].values()))['source_output']
    data = pymupdf.open(pdf)[0].read_contents()
    span = owned.inventory(data)[record['marker_id']]
    return data[span[1]:span[2]]


def ops(data):
    from pdfeditor.content_stream import operators
    return [op.name for op in operators(data)]


def pixels(pdf):
    with pymupdf.open(pdf) as document:
        return document[0].get_pixmap(dpi=144).samples


@pytest.fixture(scope='module')
def chain(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic-writer')
    source, asset = make_flow(root / 'main')
    main = root / 'main'
    v3 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0', semantic=statement(asset))
    (main / 'v3.json').write_text(json.dumps(v3))
    steps = {'confirmed': (main / 'rev1.pdf', main / 'v3.json')}
    order = [('save1', 'confirmed', dict(operation='save')), ('save2', 'save1', dict(operation='save')),
             ('save3', 'save2', dict(operation='save')),
             ('edit', 'save3', dict(operation='edit', start=1, end=1, text='A')),
             ('edit_noop', 'edit', dict(operation='save')),
             ('tw', 'save3', dict(operation='reinterpret', changes=dict(word_spacing='6/5'))),
             ('tw_noop', 'tw', dict(operation='save')),
             ('tw_to_edge', 'tw', dict(operation='reinterpret', changes=dict(word_spacing='0', edges=[EDGE]))),
             ('edge_to_tw', 'tw_to_edge', dict(operation='reinterpret', changes=dict(word_spacing='6/5', edges=[]))),
             ('label', 'save3', dict(operation='reinterpret', changes=dict(body_style_id='body-next'))),
             ('empty', 'save3', dict(operation='edit', start=0, end=3, text='')),
             ('empty_noop', 'empty', dict(operation='save')),
             ('regrow', 'empty', dict(operation='edit', start=0, end=0, text='A B')),
             ('newline', 'save3', dict(operation='edit', start=3, end=3, text='\nB')),
             ('trailing', 'save3', dict(operation='edit', start=3, end=3, text=' '))]
    results = {}
    for name, parent, request in order:
        result = build(*steps[parent], request, root)
        steps[name] = (result['pdf'], result['sidecar'])
        results[name] = result
    return dict(root=root, main=main, asset=asset, steps=steps, results=results)


def test_writer_rewrites_only_the_owned_body_and_rebinds_owner(chain):
    before = pymupdf.open(chain['steps']['confirmed'][0])[0].read_contents()
    after = pymupdf.open(chain['steps']['save1'][0])[0].read_contents()
    state = json.loads(chain['main'].joinpath('v3.json').read_text())
    record = state['slots']['slot-0']['source_output']
    a = owned.inventory(before)[record['marker_id']]
    b = owned.inventory(after)[record['marker_id']]
    assert before[:a[1]] == after[:b[1]] and before[a[2]:] == after[b[2]:]
    verification = chain['results']['save1']['verification']
    old, new = verification['owner_before'], verification['owner_after']
    assert new['marker_id'] == old['marker_id'] and new['created_from'] == old['created_from']
    assert new['current'] != old['current']
    assert new['current']['entry_context_sha256'] == old['current']['entry_context_sha256']
    assert verification['island']['canonical'] is True and verification['accuracy']['max_origin_error'] <= .002


def test_repeated_noop_saves_keep_the_canonical_island_byte_stable(chain):
    bodies = [body_of(*chain['steps'][n]) for n in ('save1', 'save2', 'save3')]
    assert bodies[0] == bodies[1] == bodies[2]
    assert len(ops(bodies[0])) == len(ops(bodies[2]))
    assert ops(bodies[0])[:2] == ['q', 'BT'] and ops(bodies[0])[-2:] == ['ET', 'Q']
    assert pixels(chain['steps']['save1'][0]) == pixels(chain['steps']['save2'][0]) == pixels(chain['steps']['save3'][0])
    for name in ('save1', 'save2', 'save3'):
        opened = semantic.open_semantic_flow(*chain['steps'][name])
        assert opened['status'] == 'restored' and opened['semantic']['text'] == 'A B' and opened['island']['canonical']


def test_text_edit_lifecycle(chain):
    for name in ('edit', 'edit_noop'):
        opened = semantic.open_semantic_flow(*chain['steps'][name])
        assert opened['status'] == 'restored' and opened['semantic']['text'] == 'AA B'
    assert body_of(*chain['steps']['edit']) == body_of(*chain['steps']['edit_noop'])
    assert chain['results']['edit']['plan']['diff'] == {'text': {'before': 'A B', 'after': 'AA B'}}


def test_tw_lifecycle_moves_only_the_following_glyph(chain):
    tw = semantic.open_semantic_flow(*chain['steps']['tw'])
    assert tw['status'] == 'restored' and tw['semantic']['style']['word_spacing'] == '6/5'
    assert semantic.open_semantic_flow(*chain['steps']['tw_noop'])['semantic']['style']['word_spacing'] == '6/5'
    base, moved = body_of(*chain['steps']['save3']), body_of(*chain['steps']['tw'])
    assert b'34.4 60 Tm <0003>' in base and b'35.6 60 Tm <0003>' in moved
    assert body_of(*chain['steps']['tw']) == body_of(*chain['steps']['tw_noop'])


def test_tw_and_edge_records_stay_distinct_over_identical_physical_bodies(chain):
    tw, edge = semantic.open_semantic_flow(*chain['steps']['tw']), semantic.open_semantic_flow(*chain['steps']['tw_to_edge'])
    assert body_of(*chain['steps']['tw']) == body_of(*chain['steps']['tw_to_edge'])
    assert (tw['semantic']['style']['word_spacing'], tw['semantic']['edges']) == ('6/5', [])
    assert (edge['semantic']['style']['word_spacing'], edge['semantic']['edges']) == ('0', [EDGE])
    back = semantic.open_semantic_flow(*chain['steps']['edge_to_tw'])
    assert back['semantic'] == tw['semantic'] and back['island']['canonical']


@pytest.mark.parametrize('request_,edges', [
    (dict(operation='edit', start=1, end=2, text=''), []),                        # endpoint delete
    (dict(operation='edit', start=2, end=2, text='A'), []),                       # insertion between endpoints
    (dict(operation='edit', start=0, end=0, text='B'), [dict(EDGE, left=2, right=3)]),  # earlier edit remaps
])
def test_edge_edit_policy_in_actual_candidates(chain, request_, edges):
    result = build(*chain['steps']['tw_to_edge'], request_, chain['root'])
    opened = semantic.open_semantic_flow(result['pdf'], result['sidecar'])
    assert opened['status'] == 'restored' and opened['semantic']['edges'] == edges


def test_edge_contribution_is_suppressed_at_a_line_end(chain):
    prefix = next(p for p in ('AB ' * k for k in range(4, 9))
                  if semantic.plan_semantic_transition(*chain['steps']['tw_to_edge'],
                      dict(operation='edit', start=0, end=0, text=p))['next_plan']['lines'][1:2]
                  and semantic.plan_semantic_transition(*chain['steps']['tw_to_edge'],
                      dict(operation='edit', start=0, end=0, text=p))['next_plan']['lines'][1]['start'] == len(p) + 2)
    result = build(*chain['steps']['tw_to_edge'], dict(operation='edit', start=0, end=0, text=prefix), chain['root'])
    opened = semantic.open_semantic_flow(result['pdf'], result['sidecar'])
    assert opened['status'] == 'restored'
    assert opened['semantic']['edges'] == [dict(EDGE, left=len(prefix) + 1, right=len(prefix) + 2)]
    body = body_of(result['pdf'], result['sidecar'])
    plan = result['plan']
    assert body.count(b' Tj') == plan['next_plan']['emitted']
    # B starts line 2 at the region x: the edge contribution is suppressed at the line end.
    assert body.count(b'1 0 0 1 20 ') == 2


def test_empty_regrow_newline_and_trailing_space(chain):
    empty = semantic.open_semantic_flow(*chain['steps']['empty'])
    assert empty['status'] == 'restored' and empty['semantic']['text'] == '' and b'[] TJ' in body_of(*chain['steps']['empty'])
    assert body_of(*chain['steps']['empty']) == body_of(*chain['steps']['empty_noop'])
    assert body_of(*chain['steps']['regrow']) == body_of(*chain['steps']['save3'])
    newline = semantic.open_semantic_flow(*chain['steps']['newline'])
    assert newline['derived']['omitted'] == [dict(offset=3, kind='newline', style='body')]
    trailing = semantic.open_semantic_flow(*chain['steps']['trailing'])
    assert trailing['derived']['omitted'] == [dict(offset=3, kind='trimmed-space', style='body')]
    assert body_of(*chain['steps']['trailing']) == body_of(*chain['steps']['save3'])


def test_label_rename_keeps_the_physical_body(chain):
    opened = semantic.open_semantic_flow(*chain['steps']['label'])
    assert opened['semantic']['body_style_id'] == 'body-next'
    assert body_of(*chain['steps']['label']) == body_of(*chain['steps']['save3'])


def version1(chain, name):
    """The stored record exactly as PR #41/#42 sealed it (semantic version 1)."""
    state = json.loads(Path(chain['steps'][name][1]).read_text())
    return semantic._attach(state, 'slot-0', state['slots']['slot-0']['semantic']['payload'])


@pytest.mark.parametrize('changes', [dict(font_size='13'), dict(horizontal_scale='4/5'), dict(rise='-1'),
                                     dict(tracking='1/4')])
def test_style_reinterpretation_of_a_version1_record_keeps_the_b_l2_s_refusal(chain, changes):
    # Version 2 records carry explicit current authority (tests/test_semantic_authority.py);
    # a version 1 record binds style to the source registry and is never upgraded implicitly.
    with pytest.raises(PdfError, match='B-L2-S'):
        build(chain['steps']['save3'][0], version1(chain, 'save3'), dict(operation='reinterpret', changes=changes),
              chain['root'])


def test_font_change_of_a_version1_record_keeps_the_b_l2_s_refusal(chain, tmp_path):
    from test_semantic_layout import static_font
    import hashlib
    alternate = tmp_path / 'alternate.ttf'; alternate.write_bytes(static_font('SemanticAlternate'))
    sha = hashlib.sha256(alternate.read_bytes()).hexdigest()
    with pytest.raises(PdfError, match='B-L2-S'):
        build(chain['steps']['save3'][0], version1(chain, 'save3'), dict(operation='reinterpret', changes=dict(font=sha)),
              chain['root'], asset=alternate)


def test_reopen_needs_no_candidate_and_refusals_are_preserved(chain):
    pdf, sidecar = chain['steps']['save3']
    for request in (dict(operation='reopen'), dict(operation='split'), dict(operation='hash_refresh'),
                    dict(operation='reflow', width='16'), dict(operation='edit', start=3, end=3, text=' ' + 'AB ' * 30),
                    dict(operation='reinterpret', changes=dict(edges=[dict(EDGE, right=3)]))):
        with pytest.raises(PdfError):
            build(pdf, sidecar, request, chain['root'])


def test_stale_owner_and_stale_semantic_refuse(chain):
    old = json.loads(Path(chain['steps']['save1'][1]).read_text())
    new = json.loads(Path(chain['steps']['edit'][1]).read_text())
    stale_owner = resealed(new, lambda v: v['slots']['slot-0']['source_output'].update(
        current=deepcopy(old['slots']['slot-0']['source_output']['current'])))
    stale_semantic = resealed(new, lambda v: v['slots']['slot-0'].update(semantic=deepcopy(old['slots']['slot-0']['semantic'])))
    for state in (stale_owner, stale_semantic):
        with pytest.raises(PdfError, match='does not verify'):
            build(chain['steps']['edit'][0], state, dict(operation='save'), chain['root'])


def snapshot_files(*paths):
    return [Path(p).read_bytes() for p in paths]


def candidates(root):
    work = root / 'work'
    return sorted(p.name for p in work.iterdir()) if work.exists() else []


@pytest.mark.parametrize('point', ['plan', 'serializer', 'mutation', 'commit', 'rebind', 'semantic_bind', 'reverify'])
def test_failure_injection_leaves_inputs_and_workspace_untouched(chain, monkeypatch, point):
    pdf, sidecar = chain['steps']['save3']
    before, existing = snapshot_files(pdf, sidecar), candidates(chain['root'])
    def boom(*args, **kwargs):
        raise PdfError('injected ' + point)
    if point == 'plan':
        monkeypatch.setattr(semantic, 'plan_semantic_transition', boom)
    elif point == 'serializer':
        monkeypatch.setattr(island, 'body', boom)
    elif point == 'mutation':
        real = writer.SemanticIslandPlan.__init__
        def init(self, owner):
            real(self, owner)
            from pdfeditor.mutation import Mutation
            self.mutations.append(Mutation(0, 1, b' ', owner='foreign'))
        monkeypatch.setattr(writer.SemanticIslandPlan, '__init__', init)
    elif point == 'commit':
        monkeypatch.setattr(Transaction, 'commit', boom)
    elif point == 'rebind':
        monkeypatch.setattr(owned, 'rebind', boom)
    elif point == 'semantic_bind':
        monkeypatch.setattr(semantic, '_attach', boom)
    else:
        real_open = semantic.open_semantic_flow
        calls = []
        def second(*args, **kwargs):
            calls.append(1)
            return real_open(*args, **kwargs) if len(calls) < 2 else dict(status='needs_confirmation', reason='injected')
        monkeypatch.setattr(semantic, 'open_semantic_flow', second)
    with pytest.raises(PdfError):
        build(pdf, sidecar, dict(operation='edit', start=1, end=1, text='A'), chain['root'])
    assert snapshot_files(pdf, sidecar) == before and candidates(chain['root']) == existing


def test_serializer_tamper_and_writes_outside_the_owned_body_refuse(chain, monkeypatch):
    pdf, sidecar = chain['steps']['save3']
    real_body = island.body
    monkeypatch.setattr(island, 'body', lambda *a, **k: (real_body(*a, **k)[0].replace(b'27.2', b'27.9', 1), real_body(*a, **k)[1]))
    with pytest.raises(PdfError):
        build(pdf, sidecar, dict(operation='save'), chain['root'])
    monkeypatch.setattr(island, 'body', real_body)
    real_owned = owned.owned_body
    monkeypatch.setattr(owned, 'owned_body', lambda *a, **k: (real_owned(*a, **k)[0] - 3, real_owned(*a, **k)[1]))
    with pytest.raises(PdfError):
        build(pdf, sidecar, dict(operation='save'), chain['root'])


def test_physically_changed_source_refuses(chain, tmp_path):
    flows = dict(main=chain['main'], v3_rev1=json.loads(chain['main'].joinpath('v3.json').read_text()))
    for name, transform in [('spoof', lambda d: d + d[d.index(b'\n%'):]),
                            ('foreign', lambda d: d.replace(b'<0001> Tj', b'<0001> Tj 1 0 0 1 60 60 Tm <0001> Tj', 1)),
                            ('context', lambda d: b'0.5 g\n' + d)]:
        path, state = physical_variant(tmp_path, flows, name, transform)
        with pytest.raises(PdfError, match='does not verify'):
            build(path, state, dict(operation='save'), chain['root'])


def test_source_revision_change_after_verification_refuses(chain, monkeypatch, tmp_path):
    pdf, sidecar = chain['steps']['save3']
    copy = tmp_path / 'source.pdf'; copy.write_bytes(Path(pdf).read_bytes())
    real = semantic.plan_semantic_transition
    def racing(*args, **kwargs):
        result = real(*args, **kwargs)
        copy.write_bytes(copy.read_bytes() + b'\n')
        return result
    monkeypatch.setattr(semantic, 'plan_semantic_transition', racing)
    with pytest.raises(PdfError, match='revision changed'):
        build(copy, sidecar, dict(operation='save'), chain['root'])


@pytest.mark.parametrize('name', ['edit', 'save3', 'tw_to_edge', 'label'])
def test_candidates_reopen_in_a_fresh_process(chain, name):
    pdf, sidecar = chain['steps'][name]
    code = ('import json,sys;from pdfeditor.semantic_layout import open_semantic_flow;'
            'r=open_semantic_flow(sys.argv[1],sys.argv[2]);print(json.dumps([r["status"],r.get("semantic"),r.get("island")]))')
    out = subprocess.run([sys.executable, '-c', code, str(pdf), str(sidecar)], capture_output=True, text=True, check=True,
                         cwd=Path(__file__).resolve().parents[1])
    status, payload, evidence = json.loads(out.stdout)
    assert status == 'restored' and evidence['canonical'] is True
    assert payload == semantic.open_semantic_flow(pdf, sidecar)['semantic']
