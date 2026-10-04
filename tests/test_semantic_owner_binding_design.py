"""B1-L-O design evidence: semantic payload co-bound with the stored source-output owner.

Design/evidence only. Uses the unmodified runtime shared-flow/source-ownership
code; no runtime schema, opener or writer is implemented here.
"""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from pdfeditor.shared_flow import open_shared_flow
from evaluations.anchors import semantic_owner_binding as model
from evaluations.anchors import semantic_owner_observation as obs


@pytest.fixture(scope='module')
def stored(tmp_path_factory):
    root = tmp_path_factory.mktemp('semantic-owner')
    source, owned_pdf, owned_json, asset = obs.flow(root / 'fixture')
    obs.ASSET = asset.read_bytes()
    state = model.initial_confirmation(owned_pdf, obs.load(owned_json), dict(operation='confirm-semantic-layout',
        slot_id='slot-0', payload=obs.payload(asset_bytes=obs.ASSET)), obs.ASSET)
    store = root / 'store'; store.mkdir()
    shutil.copyfile(owned_pdf, store / obs.NAMES[0]); obs.dump(store / obs.NAMES[1], state)
    edit = obs.candidate(root / 'edit', store / obs.NAMES[0], obs.load(store / obs.NAMES[1]),
                         dict(operation='edit', start=1, end=1, text='A'), obs.ASSET)
    return dict(root=root, source=source, owned_pdf=owned_pdf, owned_json=owned_json, store=store,
                pdf=store / obs.NAMES[0], state=obs.load(store / obs.NAMES[1]), edit=edit)


def test_stored_owner_and_semantic_reopen_from_disk(stored):
    opened = model.open_semantic_flow(stored['pdf'], obs.load(stored['store'] / obs.NAMES[1]), obs.ASSET)
    assert opened['owner']['state'] == 'owned'
    assert opened['evidence']['emitted'] == 'A B' and opened['evidence']['entry_exit_proven'] is True
    assert stored['state']['schema'] == model.SCHEMA and set(stored['state']['slots']['slot-0']['semantic']) == model.SEMANTIC_KEYS


def test_candidate_rebinds_owner_and_semantic_in_one_sidecar(stored):
    old = stored['state']['slots']['slot-0']['source_output']
    new_state = obs.load(stored['edit']['sidecar'])
    new = new_state['slots']['slot-0']['source_output']
    assert new['marker_id'] == old['marker_id'] and new['created_from'] == old['created_from']
    assert new['current'] != old['current']
    assert new['current']['entry_context_sha256'] == old['current']['entry_context_sha256']
    assert new_state['slots']['slot-0']['semantic']['binding']['owner_block_sha256'] == new['current']['block_sha256']
    assert stored['edit']['diff'] == {'text': {'before': 'A B', 'after': 'AA B'}}
    assert sorted(p.name for p in stored['edit']['pdf'].parent.iterdir()) == sorted(obs.NAMES)
    assert model.open_semantic_flow(stored['edit']['pdf'], new_state, obs.ASSET)['evidence']['emitted'] == 'AA B'


def test_stale_owner_witness_refuses_even_with_new_semantic(stored):
    old = stored['state']['slots']['slot-0']['source_output']['current']
    stale = obs.resealed(stored['edit']['state'], lambda v: v['slots']['slot-0']['source_output'].update(current=deepcopy(old)))
    with pytest.raises(ValueError):
        model.open_semantic_flow(stored['edit']['pdf'], stale, obs.ASSET)
    isolated = obs.owner_refusal(stored['edit']['pdf'], stale, 'witness mismatch')
    assert isolated['isolated'], isolated


def test_stale_semantic_refuses_even_with_new_owner(stored):
    stale = obs.resealed(stored['edit']['state'],
        lambda v: v['slots']['slot-0'].update(semantic=deepcopy(stored['state']['slots']['slot-0']['semantic'])))
    with pytest.raises(ValueError, match='OWNER_SEMANTIC_BINDING'):
        model.open_semantic_flow(stored['edit']['pdf'], stale, obs.ASSET)


@pytest.mark.parametrize('name,edit,reason', [
    ('second_slot', lambda v: v['slots'].update({'slot-1': deepcopy(v['slots']['slot-0'])}), 'EXACTLY_ONE_SOURCE_SLOT'),
    ('continuation', lambda v: v.update(continuation_destinations={'d': {'page': 1}}), 'CONTINUATION_REFUSED'),
    ('generated_slot', lambda v: v['slots']['slot-0'].update(destination_id='d'), 'GENERATED_CONTINUATION_SLOT_REFUSED'),
    ('wrong_slot', lambda v: v['slots']['slot-0']['semantic']['binding'].update(slot_id='slot-9'), 'OWNER_SEMANTIC_BINDING'),
    ('wrong_paragraph', lambda v: v['slots']['slot-0']['semantic']['binding'].update(paragraph_id='B'), 'OWNER_SEMANTIC_BINDING'),
    ('wrong_region', lambda v: v['slots']['slot-0']['semantic']['payload'].update(region_id='R2'), 'OWNER_SEMANTIC_BINDING|REGION'),
    ('unowned', lambda v: v['slots']['slot-0']['source_output'].update(state='uninitialized'), 'SOURCE_SLOT_NOT_OWNED'),
])
def test_scope_and_identity_refusals(stored, name, edit, reason):
    with pytest.raises(ValueError, match=reason):
        model.open_semantic_flow(stored['pdf'], obs.resealed(stored['state'], edit), obs.ASSET)


def test_confirmation_is_explicit_and_needs_an_owned_slot(stored):
    with pytest.raises(ValueError, match='EXPLICIT_SEMANTIC_CONFIRMATION_REQUIRED'):
        model.initial_confirmation(stored['owned_pdf'], obs.load(stored['owned_json']),
                                   dict(operation='confirm-semantic-layout', slot_id='slot-0'), obs.ASSET)
    confirmed = obs.load(stored['root'] / 'fixture' / 'confirmed.json')
    with pytest.raises(ValueError, match='SOURCE_SLOT_NOT_OWNED'):
        model.initial_confirmation(stored['source'], confirmed, dict(operation='confirm-semantic-layout',
            slot_id='slot-0', payload=obs.payload(asset_bytes=obs.ASSET)), obs.ASSET)


def test_v2_is_never_auto_upgraded_and_runtime_rejects_semantic_schema(stored):
    v2 = open_shared_flow(stored['owned_pdf'], obs.load(stored['owned_json']))
    assert v2['status'] == 'restored' and v2['state']['schema'] != model.SCHEMA
    assert 'semantic' not in v2['state']['slots']['slot-0']
    assert open_shared_flow(stored['pdf'], stored['state'])['status'] == 'needs_confirmation'


def test_self_seal_and_unrequested_candidate_semantics_refuse(stored, tmp_path):
    swapped = obs.resealed(stored['state'], lambda v: v['slots']['slot-0']['semantic']['payload'].update(text='B A'))
    with pytest.raises(ValueError, match='SEMANTIC_TEXT_BINDING'):
        model.open_semantic_flow(stored['pdf'], swapped, obs.ASSET)
    with pytest.raises(ValueError, match='UNAUTHORIZED_SEMANTIC_DIFF'):
        obs.candidate(tmp_path / 'tamper', stored['pdf'], stored['state'], dict(operation='edit', start=1, end=1, text='A'),
                      obs.ASSET, tamper=lambda p: p.update(body_style_id='other'))


def test_bundle_publication_has_two_artifacts_and_no_pointer(stored, tmp_path):
    row = obs.publish_bundle(stored['edit']['pdf'].parent, tmp_path, 'bundle-1')
    assert row['complete_new_pair'] and row['entries_after'] == ['bundle-1']
    assert sorted(p.name for p in (tmp_path / 'bundle-1').iterdir()) == sorted(obs.NAMES)
    opened = model.open_semantic_flow(tmp_path / 'bundle-1' / obs.NAMES[0],
                                      obs.load(tmp_path / 'bundle-1' / obs.NAMES[1]), obs.ASSET)
    assert opened['evidence']['emitted'] == 'AA B'


def test_publication_gate_requires_every_row():
    good = {f'{o}:{f}': dict(error=None if f == 'none' else f, public_files=[c, c], formal_current='new' if c else 'old',
                complete_new_pair=c, half_public=False, visible_before_commit=False, old_intact=True)
            for o in ('pdf-first', 'record-first') for f, c in (('none', True), ('second-link', False),
                ('rollback-unlink', False), ('before-rename', False), ('after-rename', True))}
    assert obs.publication_gate(good)
    missing = dict(good); missing.pop('record-first:after-rename')
    assert not obs.publication_gate(missing)
    never = deepcopy(good); never['pdf-first:none'].update(public_files=[False, False], complete_new_pair=False, formal_current='old')
    assert not obs.publication_gate(never)


def test_verdict_requires_owner_co_publication_gates():
    gates = dict(model_determinism=dict(exact_derived=True), input_admissibility=dict(scoped_physical=True, scope_conditions=True),
        authenticated_binding={k: True for k in ('current_binding', 'semantic_transition_authority', 'stored_owner_binding',
            'physical_mutation_ownership', 'candidate_reverification', 'owner_semantic_rebind', 'fresh_bundle_reopen',
            'atomic_pair_publication')})
    assert model.verdict(gates) == 'DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR'
    for name in list(gates['authenticated_binding']):
        broken = deepcopy(gates); broken['authenticated_binding'].pop(name)
        assert model.verdict(broken) == 'NOT READY'


def test_field_classes_keep_creation_identity_apart_from_semantics():
    classes = model.FIELD_CLASSES
    assert 'slots[s].source_output.created_from' in classes['immutable_creation_identity']
    assert classes['semantic_authority'] == ['slots[s].semantic.payload']
    assert 'slots[s].source_output.current' in classes['mutable_current_owner_witness']


def test_published_summary_is_gate_derived():
    summary = json.loads(Path(__file__).resolve().parents[1].joinpath(
        'evaluations', 'anchors', 'semantic-owner-summary.json').read_text(encoding='utf-8'))
    assert summary['artifacts'] == list(obs.NAMES) and summary['schema'] == model.SCHEMA
    assert all(summary['positive'].values())
    assert all(v['refused'] for v in summary['negative'].values())
    assert all(v['isolated'] for v in summary['isolated_owner_refusals'].values())
    assert obs.publication_gate(summary['publication'])
    assert summary['verdict'] == model.verdict(summary['gates'])
    assert summary['runtime_sha256'] == 'd22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea'
