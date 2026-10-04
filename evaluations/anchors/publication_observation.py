"""Design adapters: synthetic PDFs, real owner/parser and _publish probes.

No existing runtime implementation is changed. The state machine's PDF builder
is a fixture generator, not a runtime layout serializer. Island and filesystem
probes are separate evidence for the future adapter boundary, not an integrated
production implementation.
"""
from copy import deepcopy
from io import BytesIO
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch
from fontTools.ttLib import TTFont

from pypdf import PdfReader,PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject

from pdfeditor.shaped_font import ShapedFont
from pdfeditor.content_stream import ContentPage,operators
from pdfeditor import source_ownership as owned
from pdfeditor.mutation import Mutation,MutationProgram
from pdfeditor.editable import _publish
from evaluations.anchors.measurement_observation import synthetic_font
from evaluations.anchors.semantic_binding import (admit,extract_current,physical_accuracy,
    expected_program,canonical,sha,font_policy,require)
from evaluations.anchors.authorized_publication import (derive,initial_confirmation,verify_current,
    authorize_transition,check_diff,PairStore,run,seal,unpack,STAGES,verdict)
from evaluations.continuation.source_slot_accumulation import runtime_digest

BASE='1f5a7f0d70d4365eb0d6fac7cbebd647a3dd6ace'
TARGET={'document':'caller-document','paragraph':'paragraph-1'}
OWNER={'domain':'source-output','id':'owner-1'}


def semantic(text='A B  \n\nAA'):
    asset=synthetic_font()
    return dict(text=text,font=dict(sha=sha(asset),policy=font_policy()),body_style_id='body',edges=[],
        style=dict(font_size='12',horizontal_scale='1',rise='0',tracking='0',word_spacing='6/5',spacing_intent='confirmed-inline'),
        region=dict(x='20',baseline='60',width='100',leading='1'))


def build_pdf(record,plan,asset):
    font=ShapedFont(asset)
    try:
        resource=font.resource([font.shape(''.join(sorted(record['codebook'])),nominal_spacing=True)])
        actual={c:resource.cids[(font.shape(c).glyphs[0].gid,c)] for c in record['codebook']}
        require(actual==record['codebook'],'FIXTURE_CODEBOOK')
        writer=PdfWriter();ref=resource.build(writer);page=writer.add_blank_page(320,260)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F'):ref})})
        stream=DecodedStreamObject();stream.set_data(expected_program(record,plan))
        page[NameObject('/Contents')]=writer._add_object(stream)
        out=BytesIO();writer.write(out);return out.getvalue()
    finally:font.font.close()


def physical(pdf,record,asset,plan):
    font,actual=admit(record,asset)
    try:
        require(actual==plan,'CANDIDATE_LAYOUT_MISMATCH')
        result=extract_current(pdf,record,font,plan)
        accuracy=physical_accuracy(pdf,plan);require(accuracy['passed'],'CANDIDATE_ACCURACY')
        return dict(witness=result,accuracy=accuracy)
    finally:font.font.close()


def initial(text='A B  \n\nAA'):
    asset=synthetic_font();s=semantic(text);derived,plan=derive(s,asset)
    pdf=build_pdf(derived,plan,asset)
    verified=initial_confirmation(pdf,s,asset,TARGET,OWNER,physical,{'operation':'confirm','semantic':s})
    return PairStore(pdf,unpack(verified.record)),asset


def requests():
    edge=dict(left=1,right=2,delta='6/5',operator='TJ',semantics='confirmed-adjacent-pair',boundary_policy='suppress-at-line-end')
    return {
        'noop_save':dict(operation='save'),
        'insert':dict(operation='edit',start=1,end=1,text='A'),
        'space_insert':dict(operation='edit',start=1,end=1,text=' '),
        'delete':dict(operation='edit',start=0,end=1,text=''),
        'replace':dict(operation='edit',start=0,end=1,text='B'),
        'growth':dict(operation='edit',start=3,end=3,text=' AA'),
        'newline_insert':dict(operation='edit',start=1,end=1,text='\n'),
        'newline_delete':dict(operation='edit',start=5,end=6,text=''),
        'trailing_space_insert':dict(operation='edit',start=9,end=9,text=' '),
        'trailing_space_delete':dict(operation='edit',start=3,end=5,text=''),
        'empty':dict(operation='edit',start=0,end=9,text=''),
        'all_space':dict(operation='edit',start=0,end=9,text='   '),
        'reflow':dict(operation='reflow',width='16'),
        'Tw_change':dict(operation='reinterpret',changes=dict(word_spacing='2')),
        'Tw_to_edge':dict(operation='reinterpret',changes=dict(word_spacing='0',edges=[edge])),
        'style_change':dict(operation='reinterpret',changes=dict(font_size='37/3',horizontal_scale='4/5',rise='-1',tracking='1/4')),
        'body_style_label_rename':dict(operation='reinterpret',changes=dict(body_style_id='body-next'))}


def negative(call):
    try:call()
    except (ValueError,KeyError,TypeError,RuntimeError) as exc:return dict(refused=True,reason=str(exc))
    return dict(refused=False)


def island_probe(root):
    store,asset=initial('AAAA');pdf,record=store.current
    writer=PdfWriter(clone_from=BytesIO(pdf));page=writer.pages[0]
    body=page['/Contents'].get_object().get_data()
    owner=dict(version=1,state='owned',marker_id='a'*64)
    begin,end=owned.markers(owner)
    prefix=b'q 0 g 0 0 3 3 re f Q\n';suffix=b'q 0 g 310 0 3 3 re f Q\n'
    data=prefix+begin+body+end+suffix
    stream=DecodedStreamObject();stream.set_data(data);page[NameObject('/Contents')]=writer._add_object(stream)
    path=root/'island.pdf';writer.write(path)
    content=ContentPage(path,1)
    try:
        span=owned.inventory(data)[owner['marker_id']]
        owner['current']=owned.witness(content,owner,span)
        selected=sorted({n for e in content.events for c in e.chars for n in c.source_orders})
        snapshot={'selection':{'glyph_ids':selected}}
        a,b=owned.owned_body(content,owner,snapshot)
        ops=list(operators(data[a:b]));shows=[o for o in ops if o.name=='Tj']
        require(len(shows)==4 and all(a<=a+o.start<a+o.end<=b for o in shows),'PARSED_SHOW_SPANS')
        program=MutationProgram(data)
        program.add(Mutation(a,b,body,kind=owned.REWRITE,owner='owner-1'))
        same=program.apply()
        require(same[:a]==data[:a] and same[b:]==data[b:],'FOREIGN_CONTENT_CHANGED')
        overlap=negative(lambda:program.add(Mutation(a+1,a+2,b'',owner='foreign')))
        bad=deepcopy(owner);bad['current']['entry_context_sha256']='0'*64
        stale=negative(lambda:owned.owned_body(content,bad,snapshot))
        foreign=deepcopy(snapshot);foreign['selection']['glyph_ids']=selected[1:]
        containment=negative(lambda:owned.owned_body(content,owner,foreign))
        return dict(body_equal=data[a:b]==body,foreign_prefix_suffix_preserved=same==data,
            body_span=[a,b],parsed_show_spans=[[a+o.start,a+o.end] for o in shows],entry_exit_proven=True,
            overlap=overlap,stale_context=stale,foreign_containment=containment,
            limitation='Caller-granted fixture owner; no automatic marker adoption or semantic write grant. Separate adapter probe, not integrated layout runtime.')
    finally:content.close()


def publication_probe(root):
    """Actual unmodified _publish, fresh named paths; no recursive deletion."""
    results={};real_link=os.link
    for order in ('pdf-first','record-first'):
        for failure in ('none','second-link','rollback-unlink'):
            folder=root/(order+'-'+failure);folder.mkdir()
            old_pdf=folder/'old.pdf';old_record=folder/'old.json'
            old_pdf.write_bytes(b'old-pdf');old_record.write_bytes(b'old-record')
            temporary=folder/'staging';temporary.mkdir()
            pdf=temporary/'candidate.pdf';record=temporary/'candidate.json'
            pdf.write_bytes(b'new-pdf');record.write_bytes(b'new-record')
            targets=[folder/'new.pdf',folder/'new.json']
            pairs=list(zip([pdf,record],targets))
            if order=='record-first':pairs.reverse()
            observed=[];calls=0;original_unlink=Path.unlink
            def link(a,b):
                nonlocal calls
                calls+=1
                observed.append([p.exists() for p in targets])
                if calls==2 and failure!='none':raise OSError('injected second-link failure')
                return real_link(a,b)
            def unlink(path,*args,**kwargs):
                if failure=='rollback-unlink' and path==pairs[0][1]:raise OSError('injected rollback failure')
                return original_unlink(path,*args,**kwargs)
            error=None
            try:
                with patch('pdfeditor.editable.os.link',link),patch.object(Path,'unlink',unlink):
                    _publish(temporary,pairs)
            except OSError as exc:error=str(exc)
            exists=[p.exists() for p in targets]
            results[order+':'+failure]=dict(error=error,targets=exists,intermediate=observed,
                old_intact=old_pdf.read_bytes()==b'old-pdf' and old_record.read_bytes()==b'old-record',
                formal_current='new' if error is None and all(exists) else 'old',
                orphan=any(exists) and not all(exists))
    return results


def directory_publication_probe(root):
    """Proposed adapter, experimental real filesystem evidence, NOT runtime code.

    New bundle directory only, same parent/filesystem, exclusive caller, no
    overwrite. The existing _publish writes PRIVATE children. One directory
    rename is the public commit point. No receipt or third durable file.
    """
    results={};real_link=os.link
    for order in ('pdf-first','record-first'):
        for failure in ('none','second-link','rollback-unlink','before-rename','after-rename'):
            folder=root/('directory-'+order+'-'+failure);folder.mkdir()
            private=folder/'.pending';private.mkdir();staging=folder/'.build';staging.mkdir()
            old=folder/'old';old.mkdir();(old/'document.pdf').write_bytes(b'old-pdf');(old/'semantic.json').write_bytes(b'old-record')
            pdf=staging/'document.pdf';rec=staging/'semantic.json'
            pdf.write_bytes(b'new-pdf');rec.write_bytes(b'new-record')
            public=folder/'new';pairs=[(pdf,private/pdf.name),(rec,private/rec.name)]
            if order=='record-first':pairs.reverse()
            calls=0;seen=[];original_unlink=Path.unlink
            def link(a,b):
                nonlocal calls
                calls+=1;seen.append(public.exists())
                if calls==2 and failure in ('second-link','rollback-unlink'):raise OSError('second-link')
                return real_link(a,b)
            def unlink(path,*args,**kwargs):
                if failure=='rollback-unlink' and path==pairs[0][1]:raise OSError('rollback-unlink')
                return original_unlink(path,*args,**kwargs)
            error=None
            try:
                with patch('pdfeditor.editable.os.link',link),patch.object(Path,'unlink',unlink):_publish(staging,pairs)
                if failure=='before-rename':raise OSError('before-rename')
                require(private.resolve().is_relative_to(root.resolve()) and
                        public.resolve().is_relative_to(root.resolve()) and
                        private.resolve().parent==public.resolve().parent and not public.exists(),
                        'PUBLICATION_PATH_SCOPE')
                os.rename(private,public)
                if failure=='after-rename':raise OSError('after-rename')
            except OSError as exc:error=str(exc)
            public_files=[(public/n).exists() for n in ('document.pdf','semantic.json')]
            results[order+':'+failure]=dict(error=error,public_files=public_files,
                complete_new_pair=all(public_files) and
                    (public/'document.pdf').read_bytes()==b'new-pdf' and
                    (public/'semantic.json').read_bytes()==b'new-record',
                formal_current='new' if all(public_files) else 'old',
                half_public=any(public_files) and not all(public_files),
                private_leak=private.exists() and any(private.iterdir()),visible_before_commit=any(seen),
                old_intact=(old/'document.pdf').read_bytes()==b'old-pdf' and (old/'semantic.json').read_bytes()==b'old-record')
    return results


def publication_gate(directory):
    """Require every expected outcome, including successful publication."""
    for order in ('pdf-first','record-first'):
        for failure in ('none','second-link','rollback-unlink','before-rename','after-rename'):
            row=directory.get(order+':'+failure,{})
            committed=failure in ('none','after-rename')
            if not (row.get('old_intact') is True and row.get('half_public') is False and
                    row.get('visible_before_commit') is False and
                    row.get('formal_current')==('new' if committed else 'old') and
                    row.get('public_files')==[committed,committed] and
                    row.get('complete_new_pair') is committed and 'error' in row and
                    (row['error'] is None if failure=='none' else bool(row['error']))):
                return False
    return True


def ownership_gate(island):
    """Require positive span/context evidence as well as all three refusals."""
    if not all(island.get(k) is True for k in
               ('body_equal','foreign_prefix_suffix_preserved','entry_exit_proven')):
        return False
    if not all(island.get(k,{}).get('refused') is True for k in
               ('overlap','stale_context','foreign_containment')):
        return False
    body=island.get('body_span',[]);spans=island.get('parsed_show_spans',[])
    if len(body)!=2 or len(spans)!=4:
        return False
    end=body[0]
    for span in spans:
        if len(span)!=2 or not end<=span[0]<span[1]<=body[1]:
            return False
        end=span[1]
    return True


def evaluate(root):
    root.mkdir(parents=True,exist_ok=False);before=runtime_digest()
    positives={}
    for name,request in requests().items():
        store,asset=initial();old=deepcopy(store.current[1]['semantic'])
        result=run(store,request,asset,TARGET,physical=physical,build=build_pdf,owner='owner-1')
        repeat,repeat_asset=initial()
        run(repeat,request,repeat_asset,TARGET,physical=physical,build=build_pdf,owner='owner-1')
        current=verify_current(*store.current,asset,TARGET,physical)
        derived,plan=derive(unpack(current.record)['semantic'],asset)
        positives[name]=dict(classification=result['classification'],diff=result['diff'],
            semantic_exact=result['semantic_exact'],deterministic=store.current==repeat.current,plan_sha=sha(canonical(plan)),
            accuracy=physical(store.current[0],derived,asset,plan)['accuracy'],
            Tw_preserved=old['style']['word_spacing']==store.current[1]['semantic']['style']['word_spacing'])
    edge_evidence={}
    edge_requests={
        'endpoint_delete':dict(operation='edit',start=1,end=2,text=''),
        'between_insert':dict(operation='edit',start=2,end=2,text='A'),
        'suppression':dict(operation='reflow',width='8'),
        'edge_to_Tw':dict(operation='reinterpret',changes=dict(edges=[],word_spacing='6/5'))}
    for name,request in edge_requests.items():
        store,asset=initial('A B');before_pdf=store.current[0]
        run(store,requests()['Tw_to_edge'],asset,TARGET,physical=physical,build=build_pdf,owner='owner-1')
        same_pdf=before_pdf==store.current[0]
        result=run(store,request,asset,TARGET,physical=physical,build=build_pdf,owner='owner-1')
        s=store.current[1]['semantic'];_,plan=derive(s,asset)
        edge_evidence[name]=dict(explicit_Tw_to_edge_same_pdf=same_pdf,edges=s['edges'],
            advances=[g['metric']['advance'] for g in plan['glyphs']],diff=result['diff'],verified=bool(verify_current(*store.current,asset,TARGET,physical)))
    store,asset=initial();font=TTFont(BytesIO(asset),recalcTimestamp=False)
    font['name'].setName('ExplicitAlternate',6,3,1,1033);out=BytesIO();font.save(out);font.close();alternate=out.getvalue()
    change=run(store,dict(operation='reinterpret',changes=dict(font=sha(alternate))),asset,TARGET,
        supplied_asset=alternate,physical=physical,build=build_pdf,owner='owner-1')
    font_evidence=dict(diff=change['diff'],verified=bool(verify_current(*store.current,alternate,TARGET,physical)))
    store,asset=initial();old_sem=canonical(store.current[1]['semantic']);old_pdf=store.current[0]
    result=run(store,{'operation':'save'},asset,TARGET,physical=physical,
               build=lambda *args:build_pdf(*args)+b'\n',owner='owner-1')
    noop_bytes=dict(pdf_changed=old_pdf!=store.current[0],semantic_identical=old_sem==canonical(store.current[1]['semantic']),
                    current_verified=bool(verify_current(*store.current,asset,TARGET,physical)))
    store,asset=initial();original=deepcopy(store.current)
    failures={}
    for stage in STAGES[:-1]+('after_pdf','after_record'):
        orders=('pdf-first','record-first') if stage.startswith('after_') else ('pdf-first',)
        for order in orders:
            store=PairStore(*original)
            result=negative(lambda:run(store,requests()['insert'],asset,TARGET,physical=physical,build=build_pdf,
                owner='owner-1',fail=stage,order=order))
            failures[stage+':'+order]=dict(**result,old_pair_intact=store.current==original,
                provisional_targets=bool(store.targets),events=store.events)
    store=PairStore(*original);old=verify_current(*store.current,asset,TARGET,physical)
    negatives={name:negative(lambda req={'operation':name}:authorize_transition(old,req))
               for name in ('hash_refresh','split','join','mixed_style','vertical_edge')}
    negatives['unverified']=negative(lambda:authorize_transition({},requests()['insert']))
    for field in ('word_spacing','font','body_style_id'):
        def tamper(s,field=field):
            if field=='word_spacing':s['style'][field]='0'
            else:s[field]='unrequested'
        negatives['unrequested_'+field]=negative(lambda:run(PairStore(*original),{'operation':'save'},asset,TARGET,
            physical=physical,build=build_pdf,owner='owner-1',tamper=tamper))
    negatives['foreign_owner']=negative(lambda:run(PairStore(*original),requests()['insert'],asset,TARGET,
        physical=physical,build=build_pdf,owner='foreign'))
    negatives['ownership_overlap']=negative(lambda:run(PairStore(*original),requests()['insert'],asset,TARGET,
        physical=physical,build=build_pdf,owner='owner-1',overlap=True))
    stale=deepcopy(original[1]);stale['semantic']['style']['word_spacing']='0'
    negatives['record_corruption']=negative(lambda:verify_current(original[0],stale,asset,TARGET,physical))
    negatives['stale_pdf']=negative(lambda:verify_current(original[0]+b'\n',original[1],asset,TARGET,physical))
    negatives['foreign_target']=negative(lambda:verify_current(*original,asset,dict(TARGET,document='foreign'),physical))
    negatives['candidate_physical']=negative(lambda:run(PairStore(*original),requests()['insert'],asset,TARGET,
        physical=physical,build=lambda *args:original[0],owner='owner-1'))
    island=island_probe(root);publication=publication_probe(root);directory=directory_publication_probe(root)
    gates=dict(model_determinism={'exact_derived':all(v['semantic_exact'] and v['deterministic'] for v in positives.values())},
        input_admissibility={'scoped_physical':all(v['accuracy']['passed'] for v in positives.values())},
        authenticated_binding=dict(current_binding=all(v['refused'] for v in negatives.values()),
            semantic_transition_authority=all(v['refused'] for v in negatives.values()) and font_evidence['verified'] and
                all(v['verified'] for v in edge_evidence.values()) and noop_bytes['semantic_identical'],
            physical_mutation_ownership=ownership_gate(island),
            candidate_reverification=negatives['candidate_physical']['refused'],
            atomic_pair_publication=all(v['old_pair_intact'] and not v['provisional_targets'] for v in failures.values())
                and publication_gate(directory)))
    require(runtime_digest()==before,'RUNTIME_CHANGED')
    return dict(base=BASE,runtime_sha256=before,positive=positives,negative=negatives,failure_injection=failures,
        owned_island=island,existing_publish=publication,directory_publication=directory,
        noop_byte_change=noop_bytes,edge_transitions=edge_evidence,font_transition=font_evidence,gates=gates,verdict=verdict(gates))


def main():
    p=argparse.ArgumentParser();p.add_argument('--work',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();result=evaluate(a.work.resolve());a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_bytes((json.dumps(result,indent=2)+'\n').encode());print(result['verdict'])


if __name__=='__main__':main()
