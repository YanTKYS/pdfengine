"""Synthetic PDFs and read-only authenticated rebind evidence, never runtime output.

Test-only confirmation issuer is outside the candidate bundle. Its random key
is not published in the summary. This does not implement a production issuer,
key store, transaction, semantic PDF writer, or layout runtime replacement.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import secrets
import re
import subprocess
import sys

from fontTools.ttLib import TTFont
from fontTools.ttLib.reorderGlyphs import reorderGlyphs
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject, NumberObject

from pdfeditor.shaped_font import ShapedFont
from evaluations.anchors.measurement_authority import layout, resolve_edge
from evaluations.anchors.measurement_observation import synthetic_font, candidate_record
from evaluations.anchors.semantic_binding import (admit, bind, canonical, decimal, expected_program,
    font_policy, issue_test_confirmation, record_payload, reseal, sha, three_layer_verdict)
from evaluations.continuation.source_slot_accumulation import runtime_digest

BASE='f749bb893ac883e235ce73d5ea1aa2a5bdc1fddd'
ROOT=Path(__file__).resolve().parents[2]
TARGET=dict(document='independent-caller-document-1',paragraph='paragraph-1',page=1,audience='binding-design')


def fixture(text='AAAA\nA A A  \n\nAB', *, intent='Tw', width='100', size='12', remap=False):
    """Generated test PDF; separate from every pdfeditor writer/serializer."""
    asset=synthetic_font();font=ShapedFont(asset)
    model=candidate_record(font,False,text)
    model['width']=width;model['style']['font_size']=size
    from fractions import Fraction as F
    model['empty']=dict(ascent=str(F(size)*F(3,5)),descent=str(F(size)*F(1,5)))
    if intent=='Tw':model['style']['word_spacing']='6/5'
    elif intent=='edge':
        # This fixture intentionally has the same origins as the Tw example A B.
        model['edges']=[resolve_edge(dict(left=1,right=2,delta='6/5',operator='TJ'),confirmed=True)]
    else:raise ValueError('fixture intent')
    chars=list(dict.fromkeys(c for c in text+'A ' if c!='\n'))
    resource=font.resource([font.shape(''.join(chars),nominal_spacing=True)])
    codebook={c:resource.cids[(font.shape(c).glyphs[0].gid,c)] for c in chars}
    plan=layout(model)
    painted={g['start'] for g in plan['glyphs']}
    record=dict(model=model,font_policy=font_policy(),codebook=codebook,
        intervals=[dict(start=i,end=i+1,id=f'current:{i}',style='body') for i in range(len(text))],
        omitted=[dict(offset=i,kind='newline' if c=='\n' else 'trimmed-space',style='body')
                 for i,c in enumerate(text) if i not in painted],
        default_style_id='body',empty_style_id='body',paragraph_id=TARGET['paragraph'],pdf_sha='pending')
    writer=PdfWriter();ref=resource.build(writer)
    if remap:
        f=ref.get_object()['/DescendantFonts'][0].get_object()
        stream=f['/FontDescriptor']['/FontFile2'].get_object()
        subset=TTFont(BytesIO(stream.get_data()),recalcTimestamp=False)
        old=subset.getGlyphOrder()
        new=[old[0],*reversed(old[1:])]
        reorderGlyphs(subset,new)
        mapping=f['/CIDToGIDMap'].get_object()
        raw=mapping.get_data()
        mapping_new=b''.join(new.index(old[int.from_bytes(raw[i:i+2],'big')]).to_bytes(2,'big')
                             for i in range(0,len(raw),2))
        out=BytesIO();subset.save(out);subset.close()
        replacement=DecodedStreamObject();replacement.set_data(out.getvalue())
        f['/FontDescriptor'].get_object()[NameObject('/FontFile2')]=writer._add_object(replacement)
        replacement=DecodedStreamObject();replacement.set_data(mapping_new)
        f[NameObject('/CIDToGIDMap')]=writer._add_object(replacement)
    page=writer.add_blank_page(320,260)
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F'):ref})})
    program=DecodedStreamObject();program.set_data(expected_program(record,plan))
    page[NameObject('/Contents')]=writer._add_object(program)
    out=BytesIO();writer.write(out);data=out.getvalue()
    record['pdf_sha']=sha(data)
    font.font.close()
    return data,reseal(record),asset


def mutate_pdf(data,kind):
    """Independent negative artifact mutations; not a production edit path."""
    writer=PdfWriter(clone_from=BytesIO(data))
    page=writer.pages[0]
    resource=page['/Resources']['/Font']['/F'].get_object()
    cid=resource['/DescendantFonts'][0].get_object()
    def replace_stream(owner,key,raw):
        value=DecodedStreamObject();value.set_data(raw);owner[NameObject(key)]=writer._add_object(value)
    if kind=='unicode':
        raw=resource['/ToUnicode'].get_object().get_data().replace(b'<0041>',b'<0042>',1)
        replace_stream(resource,'/ToUnicode',raw)
    elif kind=='cid_gid':
        raw=bytearray(cid['/CIDToGIDMap'].get_object().get_data());raw[2:4]=b'\0\0'
        replace_stream(cid,'/CIDToGIDMap',bytes(raw))
    elif kind=='width':cid['/W'][1][0]=NumberObject(601)
    elif kind=='outline':
        desc=cid['/FontDescriptor'].get_object()
        font=TTFont(BytesIO(desc['/FontFile2'].get_object().get_data()),recalcTimestamp=False)
        glyph=font['glyf'][font.getBestCmap()[ord('A')]]
        glyph.coordinates[1]=(450,0)
        out=BytesIO();font.save(out);font.close();replace_stream(desc,'/FontFile2',out.getvalue())
    elif kind=='marker':
        replace_stream(page,'/Contents',b'% semantic-authority paragraph-1\n'+page['/Contents'].get_object().get_data())
    elif kind=='style':
        replace_stream(page,'/Contents',page['/Contents'].get_object().get_data().replace(b'12 Tf',b'13 Tf'))
    elif kind=='empty_style':
        replace_stream(page,'/Contents',page['/Contents'].get_object().get_data().replace(b'[] TJ',b'/F 13 Tf [] TJ /F 12 Tf'))
    elif kind=='position':
        replace_stream(page,'/Contents',page['/Contents'].get_object().get_data().replace(b'20 200 Tm',b'21 200 Tm'))
    elif kind=='repeat_order':
        raw=page['/Contents'].get_object().get_data()
        spans=list(re.finditer(rb'1 0 0 1 [^\n]+ Tm <[0-9A-F]{4}> Tj\n',raw))
        a,b=spans[:2]
        replace_stream(page,'/Contents',raw[:a.start()]+b[0]+a[0]+raw[b.end():])
    else:raise ValueError(kind)
    out=BytesIO();writer.write(out);return out.getvalue()


def refused(call):
    try:
        call()
    except (ValueError,KeyError,IndexError) as exc:
        return dict(refused=True,reason=str(exc))
    return dict(refused=False,reason='unexpected acceptance')


def negative_trials(data,record,asset,key,receipt):
    results={}
    def verify(d=data,r=record,a=asset,c=receipt,t=TARGET,k=key):
        return bind(d,r,a,c,external_key=k,target=t)
    for name in ('font','Tw','edge','empty_style','interval','omitted'):
        changed=deepcopy(record)
        if name=='font':changed['model']['glyphs'][0]['font_identity']='other'
        elif name=='Tw':changed['model']['style']['word_spacing']='0'
        elif name=='edge':changed['model']['edges']=[dict(left=0,right=1,delta='1',semantics='unknown')]
        elif name=='empty_style':changed['empty_style_id']='other'
        elif name=='interval':changed['intervals'][0],changed['intervals'][1]=changed['intervals'][1],changed['intervals'][0]
        else:changed['omitted']=[]
        results['tampered_'+name]=refused(lambda:verify(r=changed))
        results['resealed_'+name]=refused(lambda:verify(r=reseal(changed)))
    results['wrong_asset']=refused(lambda:verify(a=asset+b'changed'))
    results['foreign_key']=refused(lambda:verify(k=secrets.token_bytes(32)))
    for k in ('document','paragraph'):
        results['copied_'+k]=refused(lambda:verify(t=dict(TARGET,**{k:'different'})))
    other_pdf,_,_=fixture('AAAA')
    copied=reseal(dict(record,pdf_sha=sha(other_pdf)))
    results['copied_other_pdf_record']=refused(lambda:verify(d=other_pdf,r=copied))
    changed_pdf=mutate_pdf(data,'position')
    results['stale_pdf']=refused(lambda:verify(d=changed_pdf))
    updated=reseal(dict(record,pdf_sha=sha(changed_pdf)))
    results['pdf_hash_only_updated']=refused(lambda:verify(d=changed_pdf,r=updated))
    forged=deepcopy(receipt)
    forged['message']['pdf_sha']=sha(changed_pdf)
    forged['message']['semantic_sha']=sha(canonical(record_payload(updated)))
    forged['tag']=sha(canonical(forged['message']))
    results['rehashed_receipt']=refused(lambda:verify(d=changed_pdf,r=updated,c=forged))
    # Isolate physical checks: the test authority knowingly signs invalid fixture
    # claims. This is NOT permission to re-confirm foreign files in production.
    for kind in ('unicode','cid_gid','width','outline','marker','style','empty_style','position','repeat_order'):
        changed_pdf=mutate_pdf(data,kind)
        changed=reseal(dict(record,pdf_sha=sha(changed_pdf)))
        new=issue_test_confirmation(changed,changed_pdf,asset,TARGET,key)
        results['physical_'+kind]=refused(lambda:verify(d=changed_pdf,r=changed,c=new))
    return results


def compact(result):
    return dict(admitted=result['admitted'],semantic_sha=result['semantic_sha'],plan_sha=sha(canonical(result['plan'])),
        measurement_sha=sha(canonical([g['metric'] for g in result['plan']['glyphs']])),
        witness=result['witness'],accuracy=result['accuracy'],authority=result['authority'])


def evaluate(root):
    root.mkdir(parents=True,exist_ok=False)
    before=runtime_digest();key=secrets.token_bytes(32)
    # Only the test runner supplies this separate file via an explicit CLI input.
    # No key appears in the current bundle, record, receipt or tracked summary.
    key_path=root/'external-test-authority.key';key_path.write_bytes(key)
    positive={};negative={}
    cases=[('repeated','AAAA\nA A A  \n\nAB',{}),('tw','A B',{}),
           ('edge','A B',dict(intent='edge')),('empty','',{}),('spaces','   ',{}),
           ('reflow','AAAA A A A  \n\nAB',dict(width='16')),
           ('gid_renumbered','AAAA A',dict(remap=True)),('fractional_style','A A',dict(size='1092/100')),
           ('nonterminating_style','A A',dict(size='37/3'))]
    for name,text,kwargs in cases:
        data,record,asset=fixture(text,**kwargs)
        receipt=issue_test_confirmation(record,data,asset,TARGET,key)
        folder=root/name;folder.mkdir()
        (folder/'current.pdf').write_bytes(data);(folder/'asset.ttf').write_bytes(asset)
        (folder/'current.json').write_bytes(canonical(dict(record=record,confirmation=receipt)))
        initial=bind(data,record,asset,receipt,external_key=key,target=TARGET)
        reopens=[]
        for _ in range(3):
            p=subprocess.run([sys.executable,'-m','evaluations.anchors.semantic_binding_observation',
                '--rebind',str(folder),'--test-authority',str(key_path)],cwd=ROOT,text=True,capture_output=True,check=True)
            reopens.append(json.loads(p.stdout))
        positive[name]=dict(**compact(initial),reopen_exact=all(v==compact(initial) for v in reopens),
                            fresh_reopens=len(reopens),omitted=record['omitted'])
        if name=='repeated':negative=negative_trials(data,record,asset,key,receipt)
    twdata,tw,asset=fixture('A B');edgedata,edge,_=fixture('A B',intent='edge')
    twreceipt=issue_test_confirmation(tw,twdata,asset,TARGET,key)
    # Same PDF bytes for this fixture: change only semantic record, then reseal.
    swapped=reseal(dict(edge,pdf_sha=sha(twdata)))
    intent_swap=refused(lambda:bind(twdata,swapped,asset,twreceipt,external_key=key,target=TARGET))
    f,p=admit(swapped,asset)
    from evaluations.anchors.semantic_binding import extract_current
    physical_only=extract_current(twdata,swapped,f,p)
    f.font.close()
    negative['Tw_to_edge_resealed']=intent_swap
    evidence=dict(pdf_bytes_identical=twdata==edgedata,
        physical_only_accepts_swapped_intent=bool(physical_only),authenticated_refusal=intent_swap,
        limitation='Option B does not physically witness intent; independent confirmation distinguishes the records')
    complete=len(positive)==len(cases) and all(v['admitted'] for v in positive.values())
    model=complete and all(p['reopen_exact'] for p in positive.values())
    negatives=bool(negative) and all(v['refused'] for v in negative.values())
    font_bound=complete and all(v['witness']['font_samples'] for v in positive.values())
    intervals_bound=complete and all(len({tuple(g['interval']) for g in v['witness']['binding']})==
                                     len(v['witness']['binding']) for v in positive.values())
    gates=dict(model_determinism=dict(exact_reopens=model),
        input_admissibility=dict(static_tt_scope=complete,asset_metrics=bool(font_bound),explicit_style=complete,confirmed_intent=complete),
        authenticated_binding=dict(current_font=bool(font_bound),interval_bijection=intervals_bound,
            nonpainted_style=complete and all(v['witness']['default_style_span'] for v in positive.values()),
            intent_receipt=negatives,current_revision=complete and negatives,
            fresh_process_rebind=model,production_confirmation_issuance=False,
            atomic_edit_publication_contract=False))
    require_digest=runtime_digest()
    if require_digest!=before:raise ValueError('runtime changed')
    return dict(base=BASE,runtime_sha256=before,scope='synthetic whole-page static TT, external trusted test confirmation',
        positive=positive,negative=negative,intent_ambiguity=evidence,gates=gates,
        serialization=dict(exact='1/3',output=decimal('1/3'),authority_after_output='1/3'),
        verdict=three_layer_verdict(gates),
        open_blocker='B1-L-C: trusted confirmation issuance and atomic publication across an authorized edit are not established by a test signer')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path);p.add_argument('--output',type=Path)
    p.add_argument('--rebind',type=Path);p.add_argument('--test-authority',type=Path)
    a=p.parse_args()
    if a.rebind:
        bundle=json.loads((a.rebind/'current.json').read_text())
        result=bind((a.rebind/'current.pdf').read_bytes(),bundle['record'],(a.rebind/'asset.ttf').read_bytes(),
            bundle['confirmation'],external_key=a.test_authority.read_bytes(),target=TARGET)
        print(canonical(compact(result)).decode())
    else:
        result=evaluate(a.work.resolve())
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_bytes((json.dumps(result,indent=2)+'\n').encode('utf-8'))
        print(result['verdict'])


if __name__=='__main__':main()
