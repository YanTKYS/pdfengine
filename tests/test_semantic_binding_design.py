"""Design-only trust-boundary and actual current-PDF extraction evidence."""
from copy import deepcopy
from fractions import Fraction as F
import json
import secrets

import pytest

from evaluations.anchors.semantic_binding import (admit, bind, canonical, decimal, expected_program,
    extract_current, issue_test_confirmation, reseal, sha, three_layer_verdict)
from evaluations.anchors.semantic_binding_observation import (TARGET, evaluate, fixture, mutate_pdf,
    negative_trials)


@pytest.fixture(scope='module')
def bundle():
    data,record,asset=fixture()
    key=secrets.token_bytes(32)
    receipt=issue_test_confirmation(record,data,asset,TARGET,key)
    return data,record,asset,key,receipt


def test_current_font_intervals_whitespace_and_empty_style(bundle):
    data,record,asset,key,receipt=bundle
    result=bind(data,record,asset,receipt,external_key=key,target=TARGET)
    assert result['admitted'] and result['accuracy']['passed']
    assert not result['witness']['subset_bytes_equal_asset']
    bindings=result['witness']['binding']
    assert [v['interval'] for v in bindings[:4]]==[[0,1],[1,2],[2,3],[3,4]]
    assert len({tuple(v['byte_span']) for v in bindings})==len(bindings)
    assert len({v['code'] for v in bindings[:4]})==1
    assert record['omitted'] and {v['kind'] for v in record['omitted']}=={'newline','trimmed-space'}
    assert result['witness']['default_style_span'][1]>result['witness']['default_style_span'][0]
    assert any(v['unicode']==' ' for v in result['witness']['font_samples'])


@pytest.mark.parametrize('text',['','   ','\n','A\n\nA','AAAA','A A A  '])
def test_nonpainted_and_repeated_states_rebind(text):
    data,record,asset=fixture(text)
    key=secrets.token_bytes(32);receipt=issue_test_confirmation(record,data,asset,TARGET,key)
    result=bind(data,record,asset,receipt,external_key=key,target=TARGET)
    assert result['admitted']
    painted={v['interval'][0] for v in result['witness']['binding']}
    assert painted | {v['offset'] for v in record['omitted']}==set(range(len(text)))
    assert not painted.intersection(v['offset'] for v in record['omitted'])


def test_gid_renumbering_is_not_glyph_semantics():
    data,record,asset=fixture('AAAA A',remap=True)
    key=secrets.token_bytes(32);receipt=issue_test_confirmation(record,data,asset,TARGET,key)
    result=bind(data,record,asset,receipt,external_key=key,target=TARGET)
    assert any(v['current_gid']!=v['asset_gid'] for v in result['witness']['font_samples'])


def test_tw_edge_equal_physical_bytes_require_independent_confirmation():
    data,tw,asset=fixture('A B')
    edge_data,edge,_=fixture('A B',intent='edge')
    assert data==edge_data
    key=secrets.token_bytes(32);receipt=issue_test_confirmation(tw,data,asset,TARGET,key)
    swapped=reseal(edge)
    font,plan=admit(swapped,asset)
    try:assert extract_current(data,swapped,font,plan)['binding']
    finally:font.font.close()
    with pytest.raises(ValueError,match='CONFIRMATION'):
        bind(data,swapped,asset,receipt,external_key=key,target=TARGET)
    # A real independent new confirmation would authorize a semantic change.
    # The harness models it; geometry alone never grants that authorization.
    edge_receipt=issue_test_confirmation(edge,data,asset,TARGET,key)
    assert bind(data,edge,asset,edge_receipt,external_key=key,target=TARGET)['admitted']


def test_all_tamper_reseal_and_current_pdf_negatives(bundle):
    results=negative_trials(*bundle[:3],bundle[3],bundle[4])
    assert len(results)>=25
    assert all(v['refused'] for v in results.values()),results
    assert results['rehashed_receipt']['reason']=='EXTERNAL_AUTHORITY_MISMATCH'
    assert results['physical_unicode']['reason']=='UNICODE_MAPPING'
    assert results['physical_width']['reason']=='PDF_WIDTH_MISMATCH'
    assert results['physical_outline']['reason']=='OUTLINE_MISMATCH'


@pytest.mark.parametrize('kind',['interval','empty_style','omitted','metric','history','nonpositive'])
def test_valid_receipt_does_not_bypass_input_admissibility(bundle,kind):
    data,record,asset,key,_=bundle
    record=deepcopy(record)
    if kind=='interval':record['intervals'][0]['start']=1
    elif kind=='empty_style':record['empty_style_id']='nearest-glyph'
    elif kind=='omitted':record['omitted']=[]
    elif kind=='metric':record['model']['glyphs'][0]['width']='601'
    elif kind=='history':record['model']['old_layouts']=[{}]
    else:record['model']['style']['font_size']='0'
    receipt=issue_test_confirmation(record,data,asset,TARGET,key)
    with pytest.raises(ValueError):bind(data,record,asset,receipt,external_key=key,target=TARGET)


def test_self_hash_is_not_the_trust_root(bundle):
    data,record,asset,key,receipt=bundle
    # Diagnostic self-hash is deliberately not consulted by the authenticator.
    record=dict(record,self_hash='any public self-hash')
    assert bind(data,record,asset,receipt,external_key=key,target=TARGET)['admitted']
    with pytest.raises(ValueError,match='AUTHORITY'):
        bind(data,record,asset,receipt,external_key=secrets.token_bytes(32),target=TARGET)


@pytest.mark.parametrize('kind',['unicode','cid_gid','width','outline','marker','style','empty_style','position','repeat_order'])
def test_current_physical_mutation_refused_even_with_test_issuer(bundle,kind):
    data,record,asset,key,_=bundle
    data=mutate_pdf(data,kind)
    record=reseal(dict(record,pdf_sha=sha(data)))
    receipt=issue_test_confirmation(record,data,asset,TARGET,key)
    with pytest.raises(ValueError):bind(data,record,asset,receipt,external_key=key,target=TARGET)


def test_decimal_output_does_not_become_semantic_authority():
    assert decimal('1/3')=='0.333333'
    assert decimal('-1/100000000')=='0'
    assert decimal('1.0000005')=='1' and decimal('1.0000015')=='1.000002'
    data,record,asset=fixture('A A',size='37/3')
    before=canonical(record)
    key=secrets.token_bytes(32);receipt=issue_test_confirmation(record,data,asset,TARGET,key)
    result=bind(data,record,asset,receipt,external_key=key,target=TARGET)
    assert record['model']['style']['font_size']=='37/3' and canonical(record)==before
    assert F(result['plan']['glyphs'][0]['metric']['style']['font_size'])==F('37/3')
    assert result['accuracy']['passed']


def test_three_layers_and_accuracy_are_independent():
    gates={k:{'proof':True} for k in ('model_determinism','input_admissibility','authenticated_binding')}
    assert three_layer_verdict(gates).startswith('DESIGN READY')
    for layer in gates:
        bad=deepcopy(gates);bad[layer]['proof']=False;bad['accuracy']={'passed':True}
        assert three_layer_verdict(bad)=='NOT READY'
    assert three_layer_verdict({'model_determinism':{'proof':True}})=='NOT READY'
    gates['authenticated_binding']={}
    assert three_layer_verdict(gates)=='NOT READY'


def test_fresh_process_authenticated_rebind_and_open_issuance(tmp_path):
    result=evaluate(tmp_path/'binding')
    assert len(result['positive'])==9
    assert sum(v['fresh_reopens'] for v in result['positive'].values())==27
    assert all(v['reopen_exact'] for v in result['positive'].values())
    assert result['intent_ambiguity']['pdf_bytes_identical']
    assert result['intent_ambiguity']['physical_only_accepts_swapped_intent']
    assert all(v['refused'] for v in result['negative'].values())
    assert result['verdict']=='NOT READY'
    assert result['gates']['authenticated_binding']['current_font']
    assert not result['gates']['authenticated_binding']['production_confirmation_issuance']
