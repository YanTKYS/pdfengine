"""P4 caller/request authority, independent ownership and exception publication."""
from copy import deepcopy
from io import BytesIO
import pytest
from fontTools.ttLib import TTFont
from evaluations.anchors.authorized_publication import (derive,initial_confirmation,verify_current,
    authorize_transition,PairStore,run,seal,unpack,STAGES,verdict)
from evaluations.anchors.publication_observation import (initial,physical,build_pdf,requests,
    TARGET,OWNER,evaluate,publication_probe,directory_publication_probe,island_probe,
    publication_gate,ownership_gate)
from evaluations.anchors.semantic_binding import canonical,sha


@pytest.fixture
def bundle():return initial()


def apply(store,asset,request,**kw):
    return run(store,request,asset,TARGET,physical=physical,build=build_pdf,owner='owner-1',**kw)


def verified(store,asset):return verify_current(*store.current,asset,TARGET,physical)


def test_initial_explicit_confirmation_and_noop_reopen(bundle):
    store,asset=bundle;pdf,record=store.current
    with pytest.raises(ValueError,match='INITIAL_CONFIRMATION'):
        initial_confirmation(pdf,record['semantic'],asset,TARGET,OWNER,physical,{'operation':'confirm'})
    old=deepcopy(store.current)
    a=verified(store,asset);b=verified(store,asset)
    assert a.record==b.record and store.current==old
    with pytest.raises(ValueError,match='OLD_NOT_VERIFIED'):authorize_transition(record,requests()['insert'])


@pytest.mark.parametrize('name',list(requests()))
def test_explicit_requests_generate_verified_current_pair(bundle,name):
    store,asset=bundle;old=deepcopy(store.current[1]['semantic'])
    result=apply(store,asset,requests()[name]);verified(store,asset)
    assert result['trace']==list(STAGES) and result['semantic_exact']
    assert result['classification']==('E' if requests()[name]['operation']=='reinterpret' else 'D')
    if result['classification']=='D':
        assert store.current[1]['semantic']['style']==old['style']
        assert store.current[1]['semantic']['font']==old['font']


def edge_state():
    store,asset=initial('A B');apply(store,asset,requests()['Tw_to_edge']);return store,asset


@pytest.mark.parametrize('edit_request',[
    {'operation':'edit','start':2,'end':2,'text':'A'},
    {'operation':'edit','start':1,'end':2,'text':''}])
def test_endpoint_delete_or_insert_between_drops_edge(edit_request):
    store,asset=edge_state();apply(store,asset,edit_request)
    assert store.current[1]['semantic']['edges']==[]


def test_reflow_suppresses_but_does_not_reinterpret_edge():
    store,asset=edge_state();old=deepcopy(store.current[1]['semantic']['edges'])
    apply(store,asset,{'operation':'reflow','width':'8'})
    s=store.current[1]['semantic'];_,plan=derive(s,asset)
    assert s['edges']==old and len(plan['lines'])==2
    assert all(g['metric']['advance']=='36/5' for g in plan['glyphs'])
    apply(store,asset,{'operation':'reinterpret','changes':{'edges':[],'word_spacing':'6/5'}})
    assert store.current[1]['semantic']['edges']==[]


@pytest.mark.parametrize('change',[
    {'delta_y':'1'}, {'right':4}, {'left':5,'right':6}])
def test_explicit_request_does_not_admit_unsupported_edge(bundle,change):
    store,asset=bundle
    edge=dict(requests()['Tw_to_edge']['changes']['edges'][0],**change)
    with pytest.raises(ValueError):
        apply(store,asset,{'operation':'reinterpret','changes':{'edges':[edge]}})


def test_space_insert_applies_existing_tw(bundle):
    store,asset=bundle;apply(store,asset,requests()['space_insert'])
    _,plan=derive(store.current[1]['semantic'],asset)
    spaces=[g for g in plan['glyphs'] if g['metric']['text']==' ']
    assert spaces and all(g['metric']['advance']=='42/5' for g in spaces)


@pytest.mark.parametrize('field',['Tw','edge','font','default_style'])
def test_request_omission_cannot_reinterpret_semantics(bundle,field):
    store,asset=bundle;old=deepcopy(store.current)
    def tamper(s):
        if field=='Tw':s['style']['word_spacing']='0'
        elif field=='edge':s['edges']=requests()['Tw_to_edge']['changes']['edges']
        elif field=='font':s['font']['sha']='0'*64
        else:s['body_style_id']='new'
    with pytest.raises(ValueError,match='UNAUTHORIZED_SEMANTIC_DIFF'):
        apply(store,asset,{'operation':'save'},tamper=tamper)
    assert store.current==old


def test_explicit_font_asset_change_and_missing_request(bundle):
    store,asset=bundle;font=TTFont(BytesIO(asset),recalcTimestamp=False)
    font['name'].setName('ExplicitAlternate',6,3,1,1033)
    out=BytesIO();font.save(out);font.close();alternate=out.getvalue()
    assert sha(alternate)!=sha(asset)
    with pytest.raises(ValueError,match='UNREQUESTED_ASSET'):
        apply(store,asset,{'operation':'save'},supplied_asset=alternate)
    result=apply(store,asset,{'operation':'reinterpret','changes':{'font':sha(alternate)}},supplied_asset=alternate)
    assert 'font.sha' in result['diff'];verified(store,alternate)


@pytest.mark.parametrize('operation',['hash_refresh','split','join','mixed_style','vertical_edge'])
def test_refused_operations(bundle,operation):
    store,asset=bundle
    with pytest.raises(ValueError,match='REFUSED_TRANSITION'):apply(store,asset,{'operation':operation})


def test_stale_corrupt_copied_and_foreign_bundles(bundle):
    store,asset=bundle;pdf,record=store.current
    with pytest.raises(ValueError,match='STALE_PDF'):verify_current(pdf+b'\n',record,asset,TARGET,physical)
    changed=deepcopy(record);changed['semantic']['style']['word_spacing']='0'
    with pytest.raises(ValueError,match='INTEGRITY'):verify_current(pdf,changed,asset,TARGET,physical)
    with pytest.raises(ValueError,match='CURRENT_TARGET'):
        verify_current(pdf,record,asset,dict(TARGET,paragraph='foreign'),physical)
    other,_=initial('BBBB');copied=seal(dict(record,pdf_sha=sha(other.current[0])))
    with pytest.raises(ValueError,match='PROGRAM_MISMATCH'):verify_current(other.current[0],copied,asset,TARGET,physical)


def test_semantic_authority_does_not_grant_ownership(bundle):
    store,asset=bundle;assert authorize_transition(verified(store,asset),requests()['insert'])
    for owner,overlap in [('foreign',False),('owner-1',True)]:
        with pytest.raises(ValueError,match='PHYSICAL_OWNERSHIP'):
            run(store,requests()['insert'],asset,TARGET,physical=physical,build=build_pdf,owner=owner,overlap=overlap)


@pytest.mark.parametrize('stage',list(STAGES[:-1])+['after_pdf','after_record'])
def test_failure_before_commit_retains_old_pair(bundle,stage):
    store,asset=bundle;old=deepcopy(store.current)
    with pytest.raises(ValueError,match='INJECTED'):apply(store,asset,requests()['insert'],fail=stage)
    assert store.current==old and not store.targets


def test_after_commit_failure_has_complete_new_pair(bundle):
    store,asset=bundle;old=deepcopy(store.current)
    with pytest.raises(ValueError,match='Committed'):apply(store,asset,requests()['insert'],fail='Committed')
    assert store.current!=old and set(store.targets)=={'pdf','record'};verified(store,asset)


def test_noop_save_may_change_pdf_binding_but_not_semantics(bundle):
    store,asset=bundle;old=deepcopy(store.current)
    run(store,{'operation':'save'},asset,TARGET,physical=physical,
        build=lambda *args:build_pdf(*args)+b'\n',owner='owner-1')
    assert store.current[0]!=old[0]
    assert canonical(store.current[1]['semantic'])==canonical(old[1]['semantic'])
    assert store.current[1]['derived']==old[1]['derived'];verified(store,asset)


def test_candidate_physical_failure_is_not_self_confirmed(bundle):
    store,asset=bundle;old=deepcopy(store.current)
    with pytest.raises(ValueError,match='PROGRAM_MISMATCH'):
        run(store,requests()['insert'],asset,TARGET,physical=physical,build=lambda *args:old[0],owner='owner-1')
    assert store.current==old


def test_existing_publish_limit_and_directory_commit_point(tmp_path):
    raw=publication_probe(tmp_path)
    assert raw['pdf-first:rollback-unlink']['orphan'] and raw['record-first:rollback-unlink']['orphan']
    proposed=directory_publication_probe(tmp_path)
    assert all(v['old_intact'] and not v['half_public'] and not v['visible_before_commit'] for v in proposed.values())
    assert proposed['pdf-first:rollback-unlink']['private_leak']
    assert proposed['pdf-first:after-rename']['formal_current']=='new'


def test_actual_owner_and_parsed_operator_spans(tmp_path):
    v=island_probe(tmp_path)
    assert v['body_equal'] and v['foreign_prefix_suffix_preserved'] and len(v['parsed_show_spans'])==4
    assert all(v[k]['refused'] for k in ('overlap','stale_context','foreign_containment'))


def test_publication_gate_requires_all_expected_outcomes(tmp_path):
    evidence=directory_publication_probe(tmp_path)
    assert publication_gate(evidence)
    for key in evidence:
        missing=deepcopy(evidence);del missing[key]
        assert not publication_gate(missing)
        for field in ('old_intact','half_public','visible_before_commit','complete_new_pair'):
            changed=deepcopy(evidence);changed[key][field]=not changed[key][field]
            assert not publication_gate(changed)
        for field in ('formal_current','public_files','error'):
            changed=deepcopy(evidence);del changed[key][field]
            assert not publication_gate(changed)
    # A publisher that never commits is safe from half pairs, but is not READY.
    never=deepcopy(evidence)
    for row in never.values():
        row.update(formal_current='old',public_files=[False,False],complete_new_pair=False)
    assert not publication_gate(never)


def test_ownership_gate_requires_positive_and_negative_evidence(tmp_path):
    evidence=island_probe(tmp_path)
    assert ownership_gate(evidence)
    for field in evidence:
        if field=='limitation':continue
        changed=deepcopy(evidence);del changed[field]
        assert not ownership_gate(changed)
    for field in ('body_equal','foreign_prefix_suffix_preserved','entry_exit_proven'):
        changed=deepcopy(evidence);changed[field]=False
        assert not ownership_gate(changed)
    for field in ('overlap','stale_context','foreign_containment'):
        changed=deepcopy(evidence);changed[field]['refused']=False
        assert not ownership_gate(changed)
    for spans in ([],[[0,1]]*4,[evidence['body_span']]*4):
        assert not ownership_gate(dict(evidence,parsed_show_spans=spans))


@pytest.mark.parametrize('text',['A B  \n\nAA','','   '])
def test_body_style_label_rename_keeps_canonical_slot_and_metrics(text):
    store,asset=initial(text);old=deepcopy(store.current)
    result=apply(store,asset,requests()['body_style_label_rename']);verified(store,asset)
    new=store.current[1]
    assert result['diff']=={'body_style_id':{'before':'body','after':'body-next'}}
    assert new['semantic']['body_style_id']=='body-next'
    assert new['semantic']['style']==old[1]['semantic']['style']
    assert new['derived']==old[1]['derived'] and store.current[0]==old[0]
    assert new['derived']['default_style_id']==new['derived']['empty_style_id']=='body'
    assert all(v['style']=='body' for v in new['derived']['intervals']+new['derived']['omitted'])


def test_three_layers_cannot_be_overridden_by_accuracy():
    gates={'model_determinism':{'exact_derived':True},
        'input_admissibility':{'scoped_physical':True},
        'authenticated_binding':{k:True for k in ('current_binding','semantic_transition_authority',
            'physical_mutation_ownership','candidate_reverification','atomic_pair_publication')}}
    assert verdict(gates).startswith('DESIGN READY')
    for layer in gates:
        for gate in gates[layer]:
            changed=deepcopy(gates);changed[layer][gate]=False;changed['accuracy']={'passed':True}
            assert verdict(changed)=='NOT READY'
            del changed[layer][gate]
            assert verdict(changed)=='NOT READY'
    assert verdict({})=='NOT READY'


def test_evidence_drives_design_verdict(tmp_path):
    result=evaluate(tmp_path/'evidence')
    assert all(v['refused'] for v in result['negative'].values())
    assert all(v['old_pair_intact'] for v in result['failure_injection'].values())
    assert result['noop_byte_change']==dict(pdf_changed=True,semantic_identical=True,current_verified=True)
    assert result['verdict'].startswith('DESIGN READY')
