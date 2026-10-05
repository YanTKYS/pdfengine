"""L1–L3 integrated lifecycle: published bundles are the next revision's only input.

confirm → edit → reinterpret (style, font, Tw) → no-op → publish A → open A from disk →
edit → Tw→edge → font back → no-op → publish B → fresh reopen. Only the existing runtime
APIs are used: confirm_semantic_layout, open_semantic_flow, plan_semantic_transition,
build_semantic_candidate and publish_semantic_bundle.
"""
from copy import deepcopy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.transaction import Transaction
from pdfeditor import semantic_island as island
from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_publication as publication
from pdfeditor import semantic_writer as writer
from pdfeditor import source_ownership as owned
from test_semantic_authority import alternate_font, creation_evidence
from test_semantic_layout import make_flow, resealed, statement
from test_semantic_writer import EDGE, body_of, ops, pixels

ROOT = Path(__file__).resolve().parents[1]
EDGES = [dict(EDGE, left=2, right=3), dict(EDGE, left=4, right=5)]  # "AA B A": both spaces, like Tw on every space


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def files(directory):
    return {p.name: p.read_bytes() for p in sorted(Path(directory).iterdir())}


def entries(parent):
    return sorted(p.name for p in Path(parent).iterdir())


def opened(pdf, sidecar):
    result = semantic.open_semantic_flow(pdf, sidecar)
    assert result['status'] == 'restored', result.get('reason')
    return result


def run(code, *args):
    out = subprocess.run([sys.executable, '-c', code, *map(str, args)], capture_output=True, text=True, check=True,
                         cwd=ROOT)
    return json.loads(out.stdout)


@pytest.fixture(scope='module')
def chain(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic-lifecycle')
    _, font_a = make_flow(root / 'main')
    main = root / 'main'
    font_b = root / 'font-b.ttf'
    font_b.write_bytes(alternate_font())
    public = root / 'public'
    public.mkdir()
    v3 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0',
                                          semantic=statement(font_a))
    (main / 'v3.json').write_text(json.dumps(v3))
    revisions = {'confirmed': dict(pdf=main / 'rev1.pdf', sidecar=main / 'v3.json')}
    work = root / 'work'

    def build(name, parent, request, asset=None):
        result = writer.build_semantic_candidate(revisions[parent]['pdf'], revisions[parent]['sidecar'], request,
                                                 workspace=work, asset=asset)
        revisions[name] = dict(pdf=result['pdf'], sidecar=result['sidecar'], body=result['body'], plan=result['plan'])

    def publish(name, parent):
        result = publication.publish_semantic_bundle(revisions[parent]['pdf'], revisions[parent]['sidecar'],
                                                     public / name)
        revisions[name] = dict(pdf=result['pdf'], sidecar=result['sidecar'], result=result)

    build('r1_edit', 'confirmed', dict(operation='edit', start=1, end=1, text='A'))
    build('r2_style', 'r1_edit', dict(operation='reinterpret', changes=dict(font_size='13', tracking='1/4')))
    build('r3_font_b', 'r2_style', dict(operation='reinterpret', changes=dict(font=sha(font_b))), font_b)
    build('r4_tw', 'r3_font_b', dict(operation='reinterpret', changes=dict(word_spacing='6/5')))
    build('r5_noop', 'r4_tw', dict(operation='save'))
    publish('bundle-A', 'r5_noop')
    snapshot_a = files(public / 'bundle-A')
    # The published bundle is the next revision's input: never the candidate or a staging copy.
    build('a_noop', 'bundle-A', dict(operation='save'))
    build('r6_edit', 'bundle-A', dict(operation='edit', start=4, end=4, text=' A'))
    build('r7_edge', 'r6_edit', dict(operation='reinterpret', changes=dict(word_spacing='0', edges=EDGES)))
    build('r8_font_a', 'r7_edge', dict(operation='reinterpret', changes=dict(font=sha(font_a))), font_a)
    build('r9_noop', 'r8_font_a', dict(operation='save'))
    publish('bundle-B', 'r9_noop')
    return dict(root=root, main=main, public=public, work=work, font_a=font_a, font_b=font_b, rev=revisions,
                snapshot_a=snapshot_a, snapshot_b=files(public / 'bundle-B'), v2=load(main / 'rev1.json'))


def pair(chain, name):
    return chain['rev'][name]['pdf'], chain['rev'][name]['sidecar']


ORDER = ['confirmed', 'r1_edit', 'r2_style', 'r3_font_b', 'r4_tw', 'r5_noop', 'bundle-A', 'r6_edit', 'r7_edge',
         'r8_font_a', 'r9_noop', 'bundle-B']


# --- main lifecycle: explicit current state at every revision ------------------------------------------------------

def test_every_revision_reopens_with_the_expected_current_state(chain):
    a, b = sha(chain['font_a']), sha(chain['font_b'])
    expected = {  # text, size, tracking, word_spacing, edges, font, provenance
        'confirmed': ('A B', '12', '0', '0', [], a, semantic.SOURCE_CONFIRMED),
        'r1_edit': ('AA B', '12', '0', '0', [], a, semantic.SOURCE_CONFIRMED),
        'r2_style': ('AA B', '13', '1/4', '0', [], a, semantic.CALLER_CONFIRMED),
        'r3_font_b': ('AA B', '13', '1/4', '0', [], b, semantic.CALLER_CONFIRMED),
        'r4_tw': ('AA B', '13', '1/4', '6/5', [], b, semantic.CALLER_CONFIRMED),
        'r5_noop': ('AA B', '13', '1/4', '6/5', [], b, semantic.CALLER_CONFIRMED),
        'bundle-A': ('AA B', '13', '1/4', '6/5', [], b, semantic.CALLER_CONFIRMED),
        'r6_edit': ('AA B A', '13', '1/4', '6/5', [], b, semantic.CALLER_CONFIRMED),
        'r7_edge': ('AA B A', '13', '1/4', '0', EDGES, b, semantic.CALLER_CONFIRMED),
        'r8_font_a': ('AA B A', '13', '1/4', '0', EDGES, a, semantic.CALLER_CONFIRMED),
        'r9_noop': ('AA B A', '13', '1/4', '0', EDGES, a, semantic.CALLER_CONFIRMED),
        'bundle-B': ('AA B A', '13', '1/4', '0', EDGES, a, semantic.CALLER_CONFIRMED),
    }
    for name in ORDER:
        result = opened(*pair(chain, name))
        style = result['semantic']['style']
        actual = (result['semantic']['text'], style['font_size'], style['tracking'], style['word_spacing'],
                  result['semantic']['edges'], result['semantic']['font']['sha'], result['authority']['provenance'])
        assert actual == expected[name], name
        assert result['authority']['provider']['sha256'] == actual[5]
        assert result['island']['canonical'] is (name != 'confirmed')  # confirmed rev1 is the pre-L2 island


def test_bundles_are_complete_immutable_directories(chain):
    public = chain['public']
    assert entries(public) == ['bundle-A', 'bundle-B']
    for name in ('bundle-A', 'bundle-B'):
        assert entries(public / name) == ['document.pdf', 'shared-flow.json']
    # Building five candidates and publishing B from bundle A left bundle A byte-identical.
    assert files(public / 'bundle-A') == chain['snapshot_a']


@pytest.mark.parametrize('candidate,bundle', [('r5_noop', 'bundle-A'), ('r9_noop', 'bundle-B')])
def test_candidate_and_published_pairs_are_byte_identical(chain, candidate, bundle):
    for key in ('pdf', 'sidecar'):
        assert Path(chain['rev'][candidate][key]).read_bytes() == Path(chain['rev'][bundle][key]).read_bytes()
    assert chain['rev'][bundle]['result']['sha256'] == {'document.pdf': sha(chain['rev'][candidate]['pdf']),
                                                       'shared-flow.json': sha(chain['rev'][candidate]['sidecar'])}


def test_noop_is_byte_stable_across_publication(chain):
    candidate = chain['rev']['r5_noop']['body']
    published = body_of(*pair(chain, 'bundle-A'))
    assert candidate == body_of(*pair(chain, 'r4_tw')) == published == chain['rev']['a_noop']['body']
    assert len(ops(published)) == len(ops(chain['rev']['a_noop']['body']))
    assert chain['rev']['r9_noop']['body'] == chain['rev']['r8_font_a']['body'] == body_of(*pair(chain, 'bundle-B'))


# --- authority, owner and creation-evidence continuity --------------------------------------------------------------

def test_source_creation_evidence_never_changes(chain):
    evidence = creation_evidence(chain['v2'])
    for name in ORDER:
        value = load(chain['rev'][name]['sidecar'])
        assert creation_evidence(value) == evidence, name
        registry = value['paragraphs']['A']['style_registry']['body']
        assert registry['reflow_provider']['sha256'] == sha(chain['font_a'])  # font A stays creation evidence
        assert registry['attributes']['font_size']['value'] == 12.0
        assert value['paragraphs']['A']['source_model_sha256'] == chain['v2']['paragraphs']['A']['source_model_sha256']


def test_owner_identity_continues_and_the_current_witness_rebinds_each_revision(chain):
    states = [load(chain['rev'][n]['sidecar']) for n in ORDER]
    records = [s['slots']['slot-0']['source_output'] for s in states]
    assert all(r['marker_id'] == records[0]['marker_id'] and r['created_from'] == records[0]['created_from']
               for r in records)
    for name, state in zip(ORDER, states):
        pdf = chain['rev'][name]['pdf']
        record = state['slots']['slot-0']['source_output']
        assert state['pdf_sha256'] == sha(pdf) == state['slots']['slot-0']['semantic']['binding']['pdf_sha256']
        content = ContentPage(pdf, 1)
        try:  # the stored witness is exactly the one recomputed from this revision's PDF bytes
            span = owned.inventory(content.streams[-content.page.xref])[record['marker_id']]
            assert owned.witness(content, record, span) == record['current'], name
        finally:
            content.close()
        assert state['slots']['slot-0']['semantic']['binding']['owner_block_sha256'] == record['current']['block_sha256']
    # Every semantic change produced a new witness; the witness of revision n is not that of n+1.
    for before, after in [('confirmed', 'r1_edit'), ('r1_edit', 'r2_style'), ('r2_style', 'r3_font_b'),
                          ('r3_font_b', 'r4_tw'), ('bundle-A', 'r6_edit'), ('r6_edit', 'r8_font_a')]:
        assert (load(chain['rev'][before]['sidecar'])['slots']['slot-0']['source_output']['current'] !=
                load(chain['rev'][after]['sidecar'])['slots']['slot-0']['source_output']['current']), (before, after)


def test_caller_confirmed_authority_survives_edits_saves_and_publication(chain):
    # 13 pt / 1/4 tracking set once at r2 survive a later edit, two no-ops and two publications.
    for name in ('r5_noop', 'bundle-A', 'a_noop', 'r6_edit', 'r9_noop', 'bundle-B'):
        result = opened(*pair(chain, name))
        assert result['authority']['provenance'] == semantic.CALLER_CONFIRMED
        assert (result['semantic']['style']['font_size'], result['semantic']['style']['tracking']) == ('13', '1/4')
    assert chain['rev']['r6_edit']['plan']['diff'] == {'text': {'before': 'AA B', 'after': 'AA B A'}}
    assert b'/PRF1 13 Tf' in body_of(*pair(chain, 'bundle-B'))


def test_font_lifecycle_a_b_publish_a(chain):
    for name, font, basefont in (('r3_font_b', chain['font_b'], 'SemanticAlternate'),
                                 ('bundle-A', chain['font_b'], 'SemanticAlternate'),
                                 ('r6_edit', chain['font_b'], 'SemanticAlternate'),
                                 ('r8_font_a', chain['font_a'], 'SemanticProof'),
                                 ('bundle-B', chain['font_a'], 'SemanticProof')):
        value = load(chain['rev'][name]['sidecar'])
        current = value['slots']['slot-0']['semantic']['current']['provider']
        record = value['generated_fonts']['1']['/PRF1']
        assert current == dict(path=str(Path(font).resolve()), sha256=sha(font)), name
        assert record['provider']['source_sha256'] == sha(font) and record['basefont'].endswith('+' + basefont)
        assert list(value['generated_fonts']['1']) == ['/PRF1']


def test_tw_and_edge_keep_intent_over_the_same_physical_body(chain):
    tw, edge = opened(*pair(chain, 'r6_edit')), opened(*pair(chain, 'r7_edge'))
    assert chain['rev']['r6_edit']['body'] == chain['rev']['r7_edge']['body']
    assert (tw['semantic']['style']['word_spacing'], tw['semantic']['edges']) == ('6/5', [])
    assert (edge['semantic']['style']['word_spacing'], edge['semantic']['edges']) == ('0', EDGES)
    assert opened(*pair(chain, 'bundle-A'))['semantic']['style']['word_spacing'] == '6/5'
    assert opened(*pair(chain, 'bundle-B'))['semantic']['edges'] == EDGES


def test_revision_identities_are_distinct(chain):
    def identity(name):
        value = load(chain['rev'][name]['sidecar'])
        slot = value['slots']['slot-0']
        return (value['pdf_sha256'], json.dumps(slot['source_output']['current'], sort_keys=True),
                json.dumps(slot['semantic']['current'], sort_keys=True), body_of(*pair(chain, name)))
    changes = ['r1_edit', 'r2_style', 'r3_font_b', 'r6_edit', 'r8_font_a']
    assert len({identity(n) for n in changes}) == len(changes)
    assert identity('r5_noop')[1:] == identity('bundle-A')[1:] and identity('r5_noop')[0] == identity('bundle-A')[0]


# --- path independence, restart simulation, fresh processes ---------------------------------------------------------

def test_bundle_moved_to_another_directory_still_opens_and_edits(chain, tmp_path):
    moved = tmp_path / 'elsewhere' / 'renamed-bundle'
    shutil.copytree(chain['public'] / 'bundle-A', moved)
    result = opened(moved / 'document.pdf', moved / 'shared-flow.json')
    assert result['semantic'] == opened(*pair(chain, 'bundle-A'))['semantic']
    candidate = writer.build_semantic_candidate(moved / 'document.pdf', moved / 'shared-flow.json',
                                                dict(operation='edit', start=4, end=4, text=' A'), workspace=tmp_path)
    assert candidate['body'] == chain['rev']['r6_edit']['body']


FRESH_OPEN = ('import json,sys;from pdfeditor.semantic_layout import open_semantic_flow;'
              'r=open_semantic_flow(sys.argv[1],sys.argv[2]);'
              'print(json.dumps([r["status"],r.get("semantic"),r.get("authority"),r.get("owner"),'
              'r.get("island",{}).get("canonical")]))')


@pytest.mark.parametrize('name', ['bundle-A', 'bundle-B'])
def test_published_bundles_reopen_in_a_fresh_process(chain, name):
    status, payload, authority, owner, canonical = run(FRESH_OPEN, *pair(chain, name))
    here = opened(*pair(chain, name))
    assert status == 'restored' and canonical is True
    assert (payload, authority, owner) == (here['semantic'], here['authority'], here['owner'])


def test_next_revision_from_disk_only_in_a_fresh_process(chain, tmp_path):
    """Restart simulation: no in-memory plan or opened object; only the published files and the asset."""
    code = ('import json,sys;from pathlib import Path;'
            'from pdfeditor.semantic_layout import open_semantic_flow,plan_semantic_transition;'
            'from pdfeditor.semantic_writer import build_semantic_candidate;'
            'pdf,sidecar,work=sys.argv[1:4];request=dict(operation="edit",start=4,end=4,text=" A");'
            'o=open_semantic_flow(pdf,sidecar);p=plan_semantic_transition(pdf,sidecar,request);'
            'c=build_semantic_candidate(pdf,sidecar,request,workspace=work);'
            'print(json.dumps([o["status"],p["classification"],p["diff"],str(c["pdf"]),str(c["sidecar"]),c["body"].decode()]))')
    status, kind, diff, pdf, sidecar, body = run(code, *pair(chain, 'bundle-A'), tmp_path / 'work')
    assert status == 'restored' and kind == 'D' and diff == {'text': {'before': 'AA B', 'after': 'AA B A'}}
    # The fresh-process revision equals the in-process one: nothing in memory was authority.
    assert body.encode() == chain['rev']['r6_edit']['body']
    assert opened(pdf, sidecar)['semantic'] == opened(*pair(chain, 'r6_edit'))['semantic']
    assert files(chain['public'] / 'bundle-A') == chain['snapshot_a']


def test_provider_asset_is_required_and_not_packaged(chain, tmp_path):
    asset = tmp_path / 'font-b-copy.ttf'
    asset.write_bytes(chain['font_b'].read_bytes())
    candidate = writer.build_semantic_candidate(*pair(chain, 'bundle-B'), dict(
        operation='reinterpret', changes=dict(font=sha(asset))), workspace=tmp_path / 'work', asset=asset)
    bundle = publication.publish_semantic_bundle(candidate['pdf'], candidate['sidecar'], tmp_path / 'bundle-C')
    asset.unlink()
    assert semantic.open_semantic_flow(bundle['pdf'], bundle['sidecar'])['status'] == 'needs_confirmation'
    with pytest.raises(PdfError, match='does not verify'):
        writer.build_semantic_candidate(bundle['pdf'], bundle['sidecar'], dict(operation='save'),
                                        workspace=tmp_path / 'work')
    asset.write_bytes(chain['font_b'].read_bytes())  # the same asset back at its recorded path
    assert opened(bundle['pdf'], bundle['sidecar'])['semantic']['font']['sha'] == sha(chain['font_b'])


# --- cross-revision negatives ---------------------------------------------------------------------------------------

def test_mixed_revision_pairs_refuse(chain, tmp_path):
    a, b = pair(chain, 'bundle-A'), pair(chain, 'bundle-B')
    for pdf, sidecar in ((a[0], b[1]), (b[0], a[1])):
        assert semantic.open_semantic_flow(pdf, sidecar)['status'] == 'needs_confirmation'
        with pytest.raises(PdfError, match='does not verify'):
            writer.build_semantic_candidate(pdf, sidecar, dict(operation='save'), workspace=tmp_path)
        with pytest.raises(PdfError, match='does not verify'):
            publication.publish_semantic_bundle(pdf, sidecar, tmp_path / 'mixed')
    assert not (tmp_path / 'mixed').exists()


def _stale(chain, change):
    old, new = load(chain['rev']['bundle-A']['sidecar']), load(chain['rev']['bundle-B']['sidecar'])
    return resealed(new, lambda v: change(v, old))


@pytest.mark.parametrize('name,change', [
    ('owner', lambda v, old: v['slots']['slot-0']['source_output'].update(
        current=deepcopy(old['slots']['slot-0']['source_output']['current']))),
    ('semantic', lambda v, old: v['slots']['slot-0'].update(semantic=deepcopy(old['slots']['slot-0']['semantic']))),
    ('semantic_forged_binding', lambda v, old: v['slots']['slot-0'].update(semantic=dict(
        deepcopy(old['slots']['slot-0']['semantic']), binding=deepcopy(v['slots']['slot-0']['semantic']['binding'])))),
    ('authority_only', lambda v, old: v['slots']['slot-0']['semantic'].update(
        current=deepcopy(old['slots']['slot-0']['semantic']['current']))),
    ('generated_fonts', lambda v, old: v.update(generated_fonts=deepcopy(old['generated_fonts']))),
    ('slot_binding', lambda v, old: v['slots']['slot-0'].update(binding=deepcopy(old['slots']['slot-0']['binding']))),
])
def test_stale_revision_fields_resealed_into_bundle_b_refuse(chain, name, change):
    assert semantic.open_semantic_flow(chain['rev']['bundle-B']['pdf'], _stale(chain, change))['status'] == \
        'needs_confirmation'


@pytest.mark.parametrize('change', [
    lambda v: v['paragraphs']['A']['style_registry']['body']['attributes']['font_size'].update(value=13.0),
    lambda v: v['paragraphs']['A']['style_registry']['body']['reflow_provider'].update(sha256='1' * 64),
    lambda v: [w['properties'].update(font_size=13.0)
               for w in v['paragraphs']['A']['style_registry']['body']['source_observations']],
    lambda v: v['slots']['slot-0']['source_output']['created_from'].update(pdf_sha256='0' * 64),
    lambda v: v['paragraphs']['A'].update(source_model_sha256='0' * 64),
    lambda v: v['slots']['slot-0'].update(source_snapshot_sha256='0' * 64),
])
def test_source_evidence_tampering_still_refuses_after_many_revisions(chain, change):
    tampered = resealed(load(chain['rev']['bundle-B']['sidecar']), change)
    assert semantic.open_semantic_flow(chain['rev']['bundle-B']['pdf'], tampered)['status'] == 'needs_confirmation'


@pytest.mark.parametrize('target', ['pdf', 'sidecar_raw', 'sidecar_resealed'])
def test_tampered_published_bundle_is_not_a_next_source(chain, tmp_path, target):
    copy = tmp_path / 'bundle-A-copy'
    shutil.copytree(chain['public'] / 'bundle-A', copy)
    pdf, sidecar = copy / 'document.pdf', copy / 'shared-flow.json'
    if target == 'pdf':
        pdf.write_bytes(pdf.read_bytes() + b'\n% tampered after publication\n')
    elif target == 'sidecar_raw':
        sidecar.write_text(sidecar.read_text().replace('"13"', '"14"', 1))
    else:
        sidecar.write_text(json.dumps(resealed(load(sidecar), lambda v: v['slots']['slot-0']['semantic']['payload'][
            'style'].update(font_size='14'))))
    with pytest.raises(PdfError, match='does not verify'):
        writer.build_semantic_candidate(pdf, sidecar, dict(operation='edit', start=0, end=0, text='B'),
                                        workspace=tmp_path / 'work')
    with pytest.raises(PdfError):
        semantic.plan_semantic_transition(pdf, sidecar, dict(operation='save'))
    assert files(chain['public'] / 'bundle-A') == chain['snapshot_a']


# --- failure isolation ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize('point', ['plan', 'serializer', 'commit', 'rebind', 'semantic_bind', 'reverify'])
def test_failed_next_revision_leaves_the_published_bundle_untouched(chain, monkeypatch, tmp_path, point):
    def boom(*args, **kwargs):
        raise PdfError('injected ' + point)
    if point == 'plan':
        monkeypatch.setattr(semantic, 'plan_semantic_transition', boom)
    elif point == 'serializer':
        monkeypatch.setattr(island, 'body', boom)
    elif point == 'commit':
        monkeypatch.setattr(Transaction, 'commit', boom)
    elif point == 'rebind':
        monkeypatch.setattr(owned, 'rebind', boom)
    elif point == 'semantic_bind':
        monkeypatch.setattr(semantic, '_attach', boom)
    else:
        real_open, calls = semantic.open_semantic_flow, []
        def second(*args, **kwargs):
            calls.append(1)
            return real_open(*args, **kwargs) if len(calls) < 2 else dict(status='needs_confirmation', reason='injected')
        monkeypatch.setattr(semantic, 'open_semantic_flow', second)
    work = tmp_path / 'work'
    with pytest.raises(PdfError):
        writer.build_semantic_candidate(*pair(chain, 'bundle-A'), dict(operation='edit', start=4, end=4, text=' A'),
                                        workspace=work)
    assert files(chain['public'] / 'bundle-A') == chain['snapshot_a']
    assert not work.exists() or entries(work) == []


@pytest.mark.parametrize('point', ['staged_verification', 'rename'])
def test_failed_publication_of_b_preserves_a(chain, monkeypatch, point):
    destination = chain['public'] / ('failed-' + point)
    if point == 'staged_verification':
        monkeypatch.setattr(semantic, 'open_semantic_flow', lambda *a: dict(status='needs_confirmation', reason='injected'))
    else:
        monkeypatch.setattr(os, 'rename', lambda a, b: (_ for _ in ()).throw(OSError('injected rename failure')))
    with pytest.raises((PdfError, OSError), match='injected'):
        publication.publish_semantic_bundle(*pair(chain, 'r9_noop'), destination)
    assert not os.path.lexists(destination) and entries(chain['public']) == ['bundle-A', 'bundle-B']
    assert files(chain['public'] / 'bundle-A') == chain['snapshot_a']
    assert files(chain['public'] / 'bundle-B') == chain['snapshot_b']


def test_withdrawn_bundle_is_never_a_next_source(chain, monkeypatch, tmp_path):
    parent = tmp_path / 'public'
    parent.mkdir()
    real_open, calls = semantic.open_semantic_flow, []
    def failing_public_reopen(pdf, sidecar):
        calls.append(1)
        return dict(status='needs_confirmation', reason='injected') if len(calls) == 2 else real_open(pdf, sidecar)
    monkeypatch.setattr(semantic, 'open_semantic_flow', failing_public_reopen)
    with pytest.raises(PdfError, match='withdrawn'):
        publication.publish_semantic_bundle(*pair(chain, 'r9_noop'), parent / 'bundle-W')
    monkeypatch.undo()
    assert not os.path.lexists(parent / 'bundle-W')
    quarantine = [n for n in entries(parent) if n.startswith(publication.WITHDRAWN_PREFIX)]
    assert len(quarantine) == 1  # kept for inspection only; nothing adopts it as the current revision
    with pytest.raises((PdfError, OSError)):
        writer.build_semantic_candidate(parent / 'bundle-W' / 'document.pdf', parent / 'bundle-W' / 'shared-flow.json',
                                        dict(operation='save'), workspace=tmp_path / 'work')
    with pytest.raises(PdfError, match='already exists|plain new'):
        publication.publish_semantic_bundle(*pair(chain, 'r9_noop'), parent / quarantine[0])


def test_published_sync_error_bundle_is_a_valid_next_source(chain, monkeypatch, tmp_path):
    parent = tmp_path / 'public'
    parent.mkdir()
    real_sync = publication._sync_directory
    def sync(path):
        if Path(path) == parent:
            raise OSError('injected parent sync failure')
        return real_sync(path)
    monkeypatch.setattr(publication, '_sync_directory', sync)
    with pytest.raises(publication.PublishedSyncError) as error:
        publication.publish_semantic_bundle(*pair(chain, 'r9_noop'), parent / 'bundle-S')
    monkeypatch.undo()
    result = error.value.result
    assert result['directory'] == parent / 'bundle-S' and result['verification']['status'] == 'restored'
    assert files(parent / 'bundle-S') == chain['snapshot_b']  # the same verified bytes as bundle B
    nxt = writer.build_semantic_candidate(result['pdf'], result['sidecar'], dict(operation='edit', start=0, end=0,
                                          text='B'), workspace=tmp_path / 'work')
    assert opened(nxt['pdf'], nxt['sidecar'])['semantic']['text'] == 'BAA B A'


# --- long mixed lifecycle and boundedness ---------------------------------------------------------------------------

def _shape(value):
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return ['list', len(value)]
    return type(value).__name__


def test_long_mixed_lifecycle_is_bounded(chain, tmp_path):
    a, b = sha(chain['font_a']), sha(chain['font_b'])
    steps = [
        (dict(operation='save'), None), (dict(operation='edit', start=0, end=0, text='B'), None),
        (dict(operation='save'), None), (dict(operation='reinterpret', changes=dict(horizontal_scale='4/5')), None),
        (dict(operation='save'), None), (dict(operation='reinterpret', changes=dict(word_spacing='6/5', edges=[])), None),
        (dict(operation='save'), None),
        (dict(operation='reinterpret', changes=dict(word_spacing='0', edges=[dict(EDGE, left=3, right=4)])), None),
        (dict(operation='save'), None), (dict(operation='reinterpret', changes=dict(font=b)), chain['font_b']),
        (dict(operation='save'), None), (dict(operation='reinterpret', changes=dict(font=a)), chain['font_a']),
        (dict(operation='save'), None), (dict(operation='save'), None), (dict(operation='save'), None),
    ]
    current = pair(chain, 'bundle-B')
    marker = load(current[1])['slots']['slot-0']['source_output']['marker_id']
    rows = []
    for request, asset in steps:
        result = writer.build_semantic_candidate(*current, request, workspace=tmp_path / 'work', asset=asset)
        current = (result['pdf'], result['sidecar'])
        reopened = opened(*current)
        value = load(current[1])
        assert reopened['island']['canonical'] and reopened['owner']['marker_id'] == marker
        assert reopened['authority']['provenance'] == semantic.CALLER_CONFIRMED
        assert set(value['slots']['slot-0']['semantic']) == semantic.SEMANTIC_KEYS
        assert creation_evidence(value) == creation_evidence(chain['v2'])
        rows.append(dict(request=request['operation'], pdf=Path(current[0]).stat().st_size, sidecar=len(current[1].read_bytes()),
                         body=len(result['body']), ops=len(ops(result['body'])), fonts=list(value['generated_fonts']['1']),
                         shape=_shape(value)))
    # No history accumulates: the trailing no-ops have identical sizes, operator counts and sidecar structure.
    tail = rows[-3:]  # three consecutive no-op saves after the last font change
    assert [r['request'] for r in tail] == ['save'] * 3
    assert len({(r['pdf'], r['sidecar'], r['body'], r['ops']) for r in tail}) == 1
    assert rows[-4]['body'] == tail[0]['body'] and rows[-4]['ops'] == tail[0]['ops']
    assert all(r['shape'] == tail[0]['shape'] for r in tail)
    # Text of length 7 painted: bounded by the text, not by the number of revisions.
    assert max(r['ops'] for r in rows) == len(ops(result['body'])) and all(r['fonts'] == ['/PRF1'] for r in rows)
    first = load(chain['rev']['r1_edit']['sidecar'])
    assert set(load(current[1])) == set(first)  # no new top-level (history) keys
    assert isinstance(load(current[1])['previous_model_sha256'], str)  # a single digest link, not a list
    final = publication.publish_semantic_bundle(*current, tmp_path / 'bundle-long')
    assert run(FRESH_OPEN, final['pdf'], final['sidecar'])[0] == 'restored'


def test_sidecar_size_is_independent_of_the_number_of_revisions(chain):
    # Same text and same style: revision 5 (bundle A) vs revision 0 extended to the same state has the same shape.
    early, late = load(chain['rev']['r4_tw']['sidecar']), load(chain['rev']['bundle-A']['sidecar'])
    assert _shape(early) == _shape(late)
    assert abs(len(json.dumps(early)) - len(json.dumps(late))) < 200


# --- raster ---------------------------------------------------------------------------------------------------------

def test_mupdf_raster_across_the_lifecycle(chain):
    def render(name):
        return pixels(chain['rev'][name]['pdf'])
    assert render('r5_noop') == render('r4_tw') == render('bundle-A')        # no-op and publication: identical
    assert render('r9_noop') == render('r8_font_a') == render('bundle-B')
    assert render('r6_edit') == render('r7_edge')                            # Tw → edge: same appearance
    for before, after in [('confirmed', 'r1_edit'), ('r1_edit', 'r2_style'), ('r2_style', 'r3_font_b'),
                          ('bundle-A', 'r6_edit'), ('r7_edge', 'r8_font_a')]:
        assert render(before) != render(after), (before, after)


def test_poppler_raster_across_publication(chain, tmp_path):
    renderer = shutil.which('pdftoppm')
    if renderer is None:
        pytest.skip('Poppler unavailable; Windows external renderer validation remains required')
    from PIL import Image
    def render(name):
        dest = tmp_path / name
        subprocess.run([renderer, '-singlefile', '-r', '144', '-png', str(chain['rev'][name]['pdf']), str(dest)],
                       check=True, capture_output=True)
        with Image.open(dest.with_suffix('.png')) as image:
            return image.size, image.convert('RGB').tobytes()
    assert render('r5_noop') == render('bundle-A') == render('r4_tw')
    assert render('r9_noop') == render('bundle-B')
    assert render('bundle-A') != render('bundle-B')


# --- regression found by the long lifecycle -------------------------------------------------------------------------

def test_removed_old_ink_beyond_the_renderer_box_is_inside_the_planned_area(chain, tmp_path):
    """Font B ink reaches 700/1000 em, above its 600/1000 ascent. Under Tz 80 MuPDF reports the old glyph box from a
    scaled size (13 × 4/5), so the box ends at y 192.2 while the old ink reaches 190.9. The writer must plan the exact
    ink it removes; before the fix Transaction refused 'changed pixels outside its planned areas'."""
    a, b = sha(chain['font_a']), sha(chain['font_b'])
    work = tmp_path / 'work'
    current = pair(chain, 'bundle-A')  # font B, 13 pt
    scaled = writer.build_semantic_candidate(*current, dict(operation='reinterpret', changes=dict(
        horizontal_scale='4/5')), workspace=work)
    back = writer.build_semantic_candidate(scaled['pdf'], scaled['sidecar'], dict(
        operation='reinterpret', changes=dict(font=a)), workspace=work, asset=chain['font_a'])
    result = opened(back['pdf'], back['sidecar'])
    assert result['semantic']['font']['sha'] == a and result['semantic']['style']['horizontal_scale'] == '4/5'
    with pymupdf.open(scaled['pdf']) as document:
        boxes = [s['bbox'] for s in document[0].get_texttrace() if 'SemanticAlternate' in s['font']]
    assert boxes and min(box[1] for box in boxes) > 191.0  # the renderer box is shorter than the ink (190.9)
    deleted = writer.build_semantic_candidate(scaled['pdf'], scaled['sidecar'], dict(
        operation='edit', start=0, end=len(result['semantic']['text']), text=''), workspace=work)
    assert opened(deleted['pdf'], deleted['sidecar'])['semantic']['text'] == '' and b != a
