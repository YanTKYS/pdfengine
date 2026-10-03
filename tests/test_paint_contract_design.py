"""Pure design experiments and evidence gates; no owned PDF writer/verifier."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys

import pytest

from evaluations.anchors.canonical_geometry import format_body, recipe, token
from evaluations.anchors.paint_contract import identity, inventory_policy, transition, policy_examples
from pdfeditor.continuation import source_ctms


def test_decimal_policy_is_fixed_and_not_a_rendering_tolerance():
    assert [token(x) for x in ('-0', '-0.0000005', '0.0000015', '1e2', '1.2345675')] == [
        '0', '0', '0.000002', '100', '1.234568']
    half=Fraction(10000005,10000000)
    assert token(half-Fraction(1,10**100))=='1'
    assert token(half+Fraction(1,10**100))=='1.000001'
    for value in ('NaN', 'Infinity', '-Infinity', '1000000001', True):
        with pytest.raises(ValueError):
            token(value)
    for thickness in (0, -1, '0.0000001'):
        with pytest.raises(ValueError):
            recipe(1, thickness)
    style=recipe('1.3','.7')
    a,_=format_body(style,[[20,70,60]],['1','0','0','1','0','0'],page_height=260)
    b,_=format_body(style,[[20,70.000002,60]],['1','0','0','1','0','0'],page_height=260)
    assert a != b  # Far below 0.002pt still changes canonical bytes.


def test_formatter_exact_source_decimal_ctm_and_fresh_process_determinism():
    data=b'q .83 0 0 .91 7.25 11.5 cm 0 0 m 1 0 l 1 1 l h f Q'
    ctm=source_ctms(data)[-2]
    assert ctm[0]==Fraction(83,100) and ctm[3]==Fraction(91,100)
    style=recipe('1.3','.7','evenodd')
    rows=[[23.85,65.35,65.5],[23.85,65.35,80.5]]
    body,coords=format_body(style,rows,ctm,page_height=260)
    assert coords[0][0][0]=='20' and coords[0][1][0]=='70'
    assert body.startswith(b'q\n') and body.endswith(b'Q\n') and body.count(b'f*\n')==2
    # Independent process, only immutable value inputs; no previous output supplied.
    script="from evaluations.anchors.canonical_geometry import *; print(format_body(recipe('1.3','.7','evenodd'),[[23.85,65.35,65.5],[23.85,65.35,80.5]],['83/100','0','0','91/100','29/4','23/2'],page_height=260)[0].hex())"
    remote=subprocess.check_output([sys.executable,'-c',script],text=True).strip()
    assert bytes.fromhex(remote)==body
    for invalid in ([], [[20,20,60]]):
        with pytest.raises(ValueError):
            format_body(style,invalid,ctm,page_height=260)
    with pytest.raises(ValueError,match='axis-aligned'):
        format_body(style,rows,[1,0,1,1,0,0],page_height=260)


def test_termination_removes_exact_block_and_record_under_explicit_proof_premise():
    groups={'A':dict(block_range=[6,18],marker_id='A')}
    original=deepcopy(groups)
    rest,effect=transition(groups,'delete',validated=True,target='A')
    assert groups==original and rest=={}
    two={**groups,'B':dict(block_range=[30,50],marker_id='B')}
    survivor,_=transition(two,'delete',validated=True,target='A')
    assert survivor=={'B':two['B']}
    # Opaque byte algebra only; this does not parse or verify a PDF block.
    stream=b'prefix'+b'owned-block!'+b'foreign-suffix'
    start,end=effect['range']
    assert stream[:start]+stream[end:]==b'prefixforeign-suffix'
    with pytest.raises(ValueError,match='OWNERSHIP_UNPROVEN'):
        transition(groups,'delete',target='A')
    with pytest.raises(ValueError,match='FULL_EMPTY_REFUSED'):
        transition(groups,'delete',validated=True,target='A',paragraph_empty=True)
    with pytest.raises(ValueError,match='REVIVAL_UNSUPPORTED'):
        transition(rest,'revive',validated=True,target='A')


def test_document_wide_inventory_rejects_second_owner_even_on_other_page():
    a='page-1-owner-A';b='page-2-owner-B'
    assert inventory_policy([a],[a])=='OWNED_BY_CURRENT'
    assert inventory_policy([],[])=='NO_PAINT_OWNER'
    for discovered in ([a,b], [b], []):
        with pytest.raises(ValueError,match='INVENTORY_MISMATCH'):
            inventory_policy([a],discovered)
    with pytest.raises(ValueError,match='COLLIDING'):
        inventory_policy([a],[a,a])
    with pytest.raises(ValueError,match='STALE_OWNER_MODEL'):
        inventory_policy([a],[a],current_revision=False)


def test_creation_identity_has_no_save_counter_and_new_lifetime_has_new_seed():
    a=identity('owner','creation-model-1',['y','x'],[0,7])
    assert a==identity('owner','creation-model-1',['x','y'],[0,7])
    assert a!=identity('owner','creation-model-2',['y','x'],[0,7])
    examples=policy_examples()
    assert examples['active_identity_unchanged'] and examples['new_creation_identity_differs']
    assert examples['groups_after_termination']=={}


def test_recorded_b1_failure_is_not_hidden_by_pixel_equality():
    path=Path(__file__).resolve().parents[1]/'evaluations/anchors/paint-contract-summary.json'
    data=json.loads(path.read_text(encoding='utf-8'))
    assert data['verdict']=='NOT READY' and data['scope']=='NARROW V1'
    for case in data['geometry'].values():
        for name in ('first->noop1','change->change_noop'):
            row=case['comparisons'][name]
            assert not row['glyph_geometry_exact'] and not row['prototype_body_exact']
            assert 0 < row['max_advance_delta'] < .002
            assert row['line_allocation_exact'] and row['renderer']['passed']
        for name in ('noop1->noop2','noop2->noop3'):
            assert case['comparisons'][name]['glyph_geometry_exact']
            assert case['comparisons'][name]['prototype_body_exact']
    metrics=data['geometry']['identity']['stages']
    before=next(g for g in metrics['first']['source_font_metrics'] if g['start']==3)
    after=next(g for g in metrics['noop1']['source_font_metrics'] if g['start']==3)
    assert before==after
    assert before['width_1000em']==600 and before['Tf']==12 and before['Tz']==100
    assert data['sidecars']['B_after_A']=='needs_confirmation'
    assert not data['sidecars']['B_published'] and data['sidecars']['A_second_edit']=='restored'
    bounds=data['boundaries']
    for name in ('plain','q_line_state','scale','rectangle_clip'):
        assert bounds[name]['eligible_for_further_proof']
    for name in ('compound_clip','curve_clip','marked','compatibility','extgstate','page_group','default_color','text_object',
                 'before_terminal_pending_path','before_clip_consume'):
        assert not bounds[name]['eligible_for_further_proof']
    assert bounds['extgstate']['opacity']==1 and bounds['extgstate']['reasons']==['INHERITED_EXTGSTATE']
