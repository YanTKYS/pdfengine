"""Reproducible fixed-manifest evaluation; B2 receives no oracle geometry.

Use a new --root for a fresh experiment. Existing PDFs are never overwritten;
--phase audit and noop-audit only read them and refresh JSON evidence.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import pymupdf
from .auto import propose,approve,save
from .baseline import run as baseline
from .engine import rewrite
from .generalization_audit import audit,audit_noop,records,key,ids
from pdfeditor.page_proposal import accept_page_flow_report,replace_in_flow
from pdfeditor.shared_flow import edit_shared_flow

MANIFEST=Path('evaluations/paragraph_frame_generalization/manifest.json')


def dump(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def a_auto_region(case,spec,root,substitute):
    """Use the existing public inferred-region option after fixed-envelope refusal."""
    started=time.perf_counter();proposal=json.loads((root/'flow-proposal.json').read_text())
    choices={style['id']:{k:substitute[k] for k in ('sha256','font_index','relation')} for style in proposal['styles']}
    starts=[];offset=0
    for paragraph in spec['paragraphs']:
        starts.append(spec['line_ids'][offset]);offset+=len(paragraph['source_line_baselines'])
    accepted=accept_page_flow_report(spec['path'],proposal,provider_choices=choices,overrides=dict(width=spec['right']-spec['left'],paragraph_starts=starts))
    state=accepted['state'];target=next(p for p in state['paragraphs'].values() if case['old'] in p['logical']['text'])
    at=target['logical']['text'].index(case['old']);style=next(s['style_id'] for s in target['logical']['style_spans'] if s['start']<=at<s['end'])
    changes=replace_in_flow(state,case['old'],case['new'],style_id=style)
    output=root/'auto-edited.pdf'
    if output.exists():raise ValueError('output already exists')
    edit_shared_flow(spec['path'],state,output,root/'auto-edited.json',changes)
    return dict(status='saved-needs-audit',receipt=accepted['receipt'],timing=time.perf_counter()-started)


def continuation_oracle(spec,case,first):
    """Evaluation-only glyph inventory of a saved PDF, never an engine input."""
    original=records(spec['path'],spec['page']);saved=records(first,spec['page']);selected={i for p in spec['paragraphs'] for i in ids(p)}
    foreign=Counter(key(r) for r in original if r['id'] not in selected);editable=[]
    for r in saved:
        k=key(r)
        if foreign[k]:foreign[k]-=1
        else:editable.append(r)
    if +foreign:raise ValueError('first save failed protected-glyph qualification')
    result=deepcopy(spec);offset=0
    for i,p in enumerate(result['paragraphs']):
        p['text']=case['expected'] if i==0 else p['text'];rows=editable[offset:offset+len(p['text'])];offset+=len(rows)
        if ''.join(r['text'] for r in rows)!=p['text']:raise ValueError('continuation oracle text mismatch')
        p['glyph_ids']=[r['id'] for r in rows];p['source_line_baselines']=sorted({r['origin'][1] for r in rows})
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True,type=Path);parser.add_argument('--phase',required=True,choices=['initial','baseline','audit','lifecycle','noop-audit']);parser.add_argument('--cases',nargs='*');args=parser.parse_args()
    manifest=json.loads(MANIFEST.read_text());root=args.root;root.mkdir(parents=True,exist_ok=True)
    font=root/'substitute.ttf'
    if not font.exists():font.write_bytes(pymupdf.Font('cjk').buffer)
    substitute=dict(path=str(font.resolve()),sha256=hashlib.sha256(font.read_bytes()).hexdigest(),font_index=0,relation='substituted')
    if substitute['sha256']!=manifest['font']['sha256']:raise ValueError('common approved font hash mismatch')
    dump(root/'substitute.json',substitute)
    paths=json.loads((root/'paths.json').read_text()) if (root/'paths.json').exists() else {}
    for case in manifest['requests']:
        ident=case['id']
        if args.cases and ident not in args.cases:continue
        spec=manifest['documents'][case['document']]
        try:
            if args.phase=='initial':
                output=root/(ident+'.pdf')
                if output.exists():raise ValueError('existing initial artifact; use a new root')
                # Deliberately only basic user inputs, never spec/frame/line IDs.
                proposal=propose(spec['path'],spec['page'],case['old'],case['new'],substitute=substitute)
                dump(output.with_suffix('.proposal.json'),proposal)
                accepted=approve(spec['path'],proposal,acknowledge_inference=True)
                result=save(spec['path'],accepted,output);dump(output.with_suffix('.save.json'),result);paths[ident]=str(output);dump(root/'paths.json',paths)
            elif args.phase=='baseline':
                for arm in ('B1','A'):
                    receipt=root/(arm+'-'+ident+'.json')
                    if receipt.exists():raise ValueError('existing baseline artifact; use a new root')
                    try:
                        if arm=='B1':result=rewrite(spec['path'],root/(arm+'-'+ident+'.pdf'),spec,case['old'],case['new'],font)
                        else:
                            result=baseline(case,spec,font,root/(arm+'-'+ident))
                            if result['outcome']=='refused':
                                try:result['auto_region']=a_auto_region(case,spec,root/(arm+'-'+ident),substitute);result['outcome']='saved-needs-independent-audit'
                                except Exception as exc:result['auto_region']=dict(status='refused',reason=f'{type(exc).__name__}: {exc}')
                    except Exception as exc:result=dict(outcome='refused',reason=f'{type(exc).__name__}: {exc}')
                    dump(receipt,result)
            elif args.phase=='audit':
                result=audit(spec['path'],paths[ident],spec,case,root/(ident+'-audit'));dump(root/(ident+'.audit.json'),result)
            elif args.phase=='lifecycle':
                source=paths[ident];output=root/(ident+'-reedit.pdf')
                if not output.exists():
                    subprocess.run([sys.executable,'-m','prototypes.paragraph_frame.generalization_cli','reedit','--source',source,'--page',str(spec['page']),'--old',case['reedit_old'],'--new',case['reedit_new'],'--output',str(output),'--substitute-json',str(root/'substitute.json'),'--acknowledge-inference'],check=True,capture_output=True)
                oracle=continuation_oracle(spec,case,source)
                changed=dict(old=case['reedit_old'],new=case['reedit_new'],expected=case['expected'].replace(case['reedit_old'],case['reedit_new']))
                result=audit(source,output,oracle,changed,root/(ident+'-reedit-audit'));dump(root/(ident+'.lifecycle.audit.json'),result)
                noop=root/(ident+'-noop.pdf')
                if not noop.exists():subprocess.run([sys.executable,'-m','prototypes.paragraph_frame.generalization_cli','noop','--source',str(output),'--output',str(noop)],check=True,capture_output=True)
                dump(root/(ident+'.noop.audit.json'),audit_noop(output,noop,spec['page'],root/(ident+'-noop-audit')))
            else:
                result=audit_noop(root/(ident+'-reedit.pdf'),root/(ident+'-noop.pdf'),spec['page'],root/(ident+'-noop-audit'));dump(root/(ident+'.noop.audit.json'),result)
            print(ident,args.phase,result.get('outcome','saved'),flush=True)
        except Exception as exc:
            reason=f'{type(exc).__name__}: {exc}'
            if isinstance(exc,subprocess.CalledProcessError):reason+=' '+exc.stderr.decode()[-2000:]
            dump(root/(ident+'.'+args.phase+'.refusal.json'),dict(outcome='refused',reason=reason));print(ident,args.phase,'refused',reason,flush=True)

if __name__=='__main__':main()
