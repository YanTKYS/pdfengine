"""Canonical measurement experiments; no runtime acceptance certificate."""
from copy import deepcopy
from fractions import Fraction as F
import json
import struct
import subprocess
import sys

import pytest

from evaluations.anchors.measurement_authority import (bounds_policy, exact_source, measurement,
    layout, edit_record, resolve_edge, verification_candidate, verdict, WITNESS_FIELDS)
from evaluations.anchors.measurement_observation import (synthetic_font, candidate_record, metric_glyph,
    lifecycle, near_boundary, style_roundtrip, font_witness_observation)
from evaluations.anchors.layout_observation import ink_counterexample
from pdfeditor.shaped_font import ShapedFont


@pytest.fixture(scope='module')
def font(): return ShapedFont(synthetic_font())


def test_bounds_classes_and_base14_candidate_are_not_admission():
    assert 'outline' in bounds_policy('embedded_outline')
    assert 'subset witness' in bounds_policy('supplied_asset',asset_bound=True)
    assert 'unresolved' in bounds_policy('base14',afm_pinned=True)
    for name in ('base14','supplied_asset','unknown','unreadable'):
        with pytest.raises(ValueError,match='UNPROVEN'):bounds_policy(name)


def test_trace_ink_blocker_still_reproduces(tmp_path):
    value=ink_counterexample(tmp_path)
    assert not value['width_exact'] and not value['allocation_exact']


def test_exact_source_decimal_style_and_spacing():
    p='q .83 0 0 .91 7.25 11.5 cm BT /F 12 Tf 80 Tz .4 Tc 1.2 Tw 2 Ts 20 200 Td (A B) Tj ET Q'
    rows=exact_source(p,{c:'600' for c in 'A B'})['glyphs']
    s=rows[0]['style']
    assert F(s['font_size'])==F('10.92')
    assert F(s['horizontal_scale'])==F('.83')/F('.91')*F('.8')
    assert F(s['rise'])==F('-1.82')
    assert F(s['tracking'])==F('.4')*F('.83')*F('.8')
    assert F(s['word_spacing'])==F('1.2')*F('.83')*F('.8')
    assert F(rows[1]['advance'])==F('8.8')*F('.83')*F('.8')
    with pytest.raises(ValueError,match='GRAMMAR'):
        exact_source('BT /F 12Tf (A) Tj ET',{'A':'600'})


def test_identical_positions_do_not_identify_tw_tj_or_tm_intent():
    prefix='BT /F 12 Tf 20 200 Td '
    programs=['1.2 Tw (A B) Tj','[(A ) -100 (B)] TJ','(A ) Tj 1 0 0 1 35.6 200 Tm (B) Tj']
    r=[exact_source(prefix+p+' ET',{c:'600' for c in 'A B'}) for p in programs]
    assert all([g['origin'] for g in v['glyphs']]==[g['origin'] for g in r[0]['glyphs']] for v in r)
    assert r[0]['glyphs'][0]['style']['word_spacing']=='6/5'
    assert not r[0]['positioning_edges']
    assert r[1]['positioning_edges'][0]['operator']=='TJ'
    assert r[2]['positioning_edges'][0]['operator']=='Tm'
    assert all(v['positioning_edges'][0]['delta']=='6/5' for v in r[1:])


@pytest.mark.parametrize('program',[
    'BT /F 0 Tf (A) Tj ET','BT /F 12 Tf -8 Tc (A) Tj ET',
    'BT /F 12 Tf (A\\n) Tj ET','BT /F 12 Tf (A) Tj',
    'BT /F 12 Tf /G 12 Tf (A) Tj ET','BT /F 12 Tf 1 1 0 1 0 0 Tm (A) Tj ET'])
def test_exact_source_scope_refusals(program):
    with pytest.raises(ValueError):exact_source(program,{'A':'600'})


@pytest.mark.parametrize('key,value',[('width','0'),('width','-1'),('upem','0'),('ascent','-1'),('ink',None)])
def test_zero_negative_or_missing_font_metric_refused(font,key,value):
    r=candidate_record(font)
    g=dict(r['glyphs'][0],**{key:value})
    with pytest.raises(ValueError):measurement(g,r['style'])


def test_missing_metric_zero_size_and_unproven_blank_refused(font):
    r=candidate_record(font)
    g=deepcopy(r['glyphs'][0]);del g['width']
    with pytest.raises(ValueError,match='MISSING'):measurement(g,r['style'])
    with pytest.raises(ValueError,match='NONPOSITIVE'):measurement(r['glyphs'][0],dict(r['style'],font_size='0'))
    space=metric_glyph(' ',font)
    assert measurement(space,r['style'])['ink'] is None
    space['empty_outline_verified']=False
    with pytest.raises(ValueError,match='INK'):measurement(space,r['style'])


def test_common_measurement_includes_ink_rise_and_vertical_metrics(font):
    r=candidate_record(font,False,'AB\nBA')
    a=layout(r)
    altered=deepcopy(r)
    for g in altered['glyphs']:g['provider']='a different diagnostic provider'
    assert layout(altered)==a
    assert [F(l['baseline']) for l in a['lines']]==[F(60),F('69.6')]
    raised=layout(dict(r,style=dict(r['style'],rise='-2')))
    assert F(raised['glyphs'][0]['metric']['ink'][1])==F('-9.2')
    assert raised['glyphs'][0]['origin']==['20','58']
    assert raised['glyphs'][0]['baseline_origin']==['20','60']
    blank=layout(candidate_record(font,False,'A\n\nB'))
    assert len(blank['lines'])==3 and blank['lines'][1]['ascent']=='36/5'
    spaces=layout(candidate_record(font,False,'A\n  \nB'))
    assert spaces['lines'][1]['ascent']==blank['lines'][1]['ascent']
    assert spaces['lines'][2]['baseline']==blank['lines'][2]['baseline']


def test_exact_wrap_boundary_and_fresh_process(font):
    r=candidate_record(font,False,'AB')
    assert len(layout(dict(r,width='14.399999'))['lines'])==2
    assert len(layout(dict(r,width='14.4'))['lines'])==1
    assert len(layout(dict(r,width='14.400001'))['lines'])==1
    p=subprocess.run([sys.executable,'-m','evaluations.anchors.measurement_authority'],
        input=json.dumps(r),text=True,capture_output=True,check=True)
    assert json.loads(p.stdout)==layout(r)


def test_resolved_edge_edit_and_line_end_policy(font):
    edge=dict(left=0,right=1,delta='6/5',operator='TJ')
    with pytest.raises(ValueError,match='UNRESOLVED'):resolve_edge(edge)
    r=candidate_record(font,False,'AB')
    r['edges']=[resolve_edge(edge,confirmed=True)]
    assert layout(r)['glyphs'][0]['metric']['advance']=='42/5'
    narrow=layout(dict(r,width='8'))
    assert all(g['metric']['advance']=='36/5' for g in narrow['glyphs'])
    assert edit_record(r,1,1,[metric_glyph('A',font)])['edges']==[]
    assert edit_record(r,0,1,[])['edges']==[]
    assert edit_record(r,0,0,[metric_glyph('A',font)])['edges'][0]['left']==1
    with pytest.raises(ValueError,match='UNRESOLVED'):
        layout(dict(r,edges=[dict(r['edges'][0],delta_y='1')]))
    with pytest.raises(ValueError,match='UNRESOLVED'):
        layout(dict(candidate_record(font,False,'A\nB'),edges=r['edges']))


def test_verification_witness_model_rejects_each_mismatch_and_name_only():
    expected={k:'known' for k in WITNESS_FIELDS}
    assert verification_candidate(expected,expected)['passed']
    for k in WITNESS_FIELDS:
        result=verification_candidate(expected,dict(expected,**{k:'changed'}))
        assert not result['passed'] and result['errors']==[k]
    assert not verification_candidate(expected,{'name':'same subsetless name'})['passed']


def test_verdict_is_gate_derived_and_accuracy_cannot_override_it():
    keys=('style_authority','ink_vertical_authority','font_binding','spacing_intent','lifecycle_canonicality')
    gates={k:dict(passed=True) for k in keys}
    assert verdict(gates).startswith('DESIGN READY')
    for k in keys:
        changed=deepcopy(gates);changed[k]['passed']=False;changed['accuracy']=dict(passed=True)
        assert verdict(changed)=='NOT READY'
    assert verdict({})=='NOT READY'


def test_float32_normalization_is_not_semantic_arithmetic():
    f32=lambda v:struct.unpack('f',struct.pack('f',v))[0]
    assert f32(f32(20)+f32(7.2))-f32(20)!=f32(7.2)
    assert f32(7.2)!=7.2


@pytest.fixture(scope='module')
def embedded(tmp_path_factory):
    root=tmp_path_factory.mktemp('measurement-embedded')
    return root,lifecycle(root,'embedded',False)


def test_embedded_outline_vertical_normalization_is_independent_blocker(embedded):
    _,r=embedded
    a,b=r['stages']['first'],r['stages']['noop1']
    assert a['bounds_sources']==['provided_outline'] and b['bounds_sources']==['embedded_outline']
    assert a['lines'][1]['baseline']==69.6 and b['lines'][1]['baseline']==72
    assert not r['comparisons']['first->noop1']['measurement_tuple_exact']
    assert not r['comparisons']['first->noop1']['baselines_exact']
    assert r['comparisons']['first->noop1']['max_origin_delta']>.002
    assert all(a['within_0_002'] for a in r['accuracy'].values())
    for stage in ('change','growth','reflow'):
        assert stage+'_noop3' in r['stages']


def test_current_subset_witnesses_need_more_than_program_hash(embedded):
    root,_=embedded;p=root/'embedded_identity'
    r=font_witness_observation(p/'first.pdf',p/'first.json',p/'metric.ttf')
    assert r['glyphs'] and all(all(g[k] for k in ('outline_matches','width_equals_hmtx',
        'hhea_matches','upem_matches','hmtx_matches','descriptor_metrics_match',
        'unicode_asset_gid_matches')) for g in r['glyphs'])
    assert all(not g['program_hash_equals_asset'] for g in r['glyphs'])


def test_actual_near_boundary_roundtrip_changes_allocation(tmp_path):
    r=near_boundary(tmp_path,False,'14.3999973')
    assert r['status']=='saved'
    assert not r['comparison']['allocation_exact']
    assert r['comparison']['max_origin_delta']==16
    assert all(a['within_0_002'] for a in r['accuracy'])


@pytest.mark.parametrize('scaled',[False,True])
def test_tw_intent_loss_and_scaled_semantic_style_ratchet(tmp_path,scaled):
    r=style_roundtrip(tmp_path,scaled)
    a,b=r['stages']['first']['glyphs'][1],r['stages']['noop1']['glyphs'][1]
    assert a['Tw']==1.2 and b['Tw']==0
    assert a['semantic_style']['word_spacing_intent']!=b['semantic_style']['word_spacing_intent']
    assert not r['comparisons']['first->noop1']['semantic_style_exact']
    assert all(a['within_0_002'] for a in r['accuracy'].values())
    if scaled:
        styles=[r['stages'][n]['glyphs'][0]['semantic_style'] for n in ('first','noop1','noop2','noop3')]
        assert all(F(a['font_size'])>F(b['font_size']) for a,b in zip(styles,styles[1:]))
        assert all(F(a['horizontal_scale'])<F(b['horizontal_scale']) for a,b in zip(styles,styles[1:]))
