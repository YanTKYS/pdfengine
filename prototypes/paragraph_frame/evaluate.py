"""Reproducible evaluation stages; artifacts stay in an ignored directory.

  python -m prototypes.paragraph_frame.evaluate --root tmp/architecture-poc

Preflight/manifest must already be committed. --stage lifecycle resumes from
saved results without regenerating the initial edits. All worker edits and
no-op saves run in fresh Python processes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import pymupdf
from .audit import audit,pixels
from .baseline import run as baseline
from .preflight import ROOT,MANIFEST


def write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def worker(root,name,job):
    path=root/(name+'-job.json');result=root/(name+'-worker.json')
    job['result']=str(result);write(path,job)
    run=subprocess.run([sys.executable,'-m','prototypes.paragraph_frame.cli',str(path)],capture_output=True,text=True)
    (root/(name+'-worker.log')).write_text(run.stdout+run.stderr)
    if run.returncode:raise RuntimeError(run.stderr[-1000:])
    return json.loads(result.read_text())


def initial(root,m,font):
    if any((root/f"{c['id']}.pdf").exists() for c in m['requests']) or (root/'baseline-final').exists():
        raise ValueError('initial evaluation requires a fresh artifact directory')
    for case in m['requests']:
        spec=m['documents'][case['document']];ident=case['id']
        # Alternate the arm order for these single cold measurements. No p95
        # or statistical speedup is inferred from four correlated requests.
        for arm in (('A','B') if ident in ('A','C') else ('B','A')):
            if arm=='A':
                result=baseline(case,spec,str(font),root/'baseline-final'/ident)
                write(root/f'baseline-{ident}.json',result)
            else:
                result=worker(root,f'initial-{ident}',dict(action='edit',source=spec['path'],output=str(root/f'{ident}.pdf'),
                    spec=spec,old=case['old'],new=case['new'],font=str(font)))
                if result['status']!='saved':write(root/f'{ident}.json',result)


def audits(root,m):
    for arm in ('A','B'):
        for case in m['requests']:
            ident=case['id'];spec=m['documents'][case['document']]
            pdf=root/f'{ident}.pdf' if arm=='B' else root/'baseline-final'/ident/'flow-edited.pdf'
            if not pdf.exists():continue
            result=audit(spec['path'],pdf,spec,case,root/f'{arm}-audit-final-{ident}',arm=arm)
            write(root/f'{arm}-audit-final-{ident}.json',result)
            print('audit',arm,ident,result['outcome'],flush=True)


def lifecycle(root,m,font,arms):
    for arm in arms:
        for case in m['requests']:
            ident=case['id'];spec=m['documents'][case['document']]
            source=root/f'{ident}.pdf' if arm=='B' else root/'baseline-final'/ident/'flow-edited.pdf'
            if not source.exists():continue
            rows={}
            for mode in ('pdf-alone','sidecar'):
                name=f'{arm}-{ident}-{mode}';output=root/f'{name}-reedited.pdf'
                if output.exists() or (root/f'{name}-proposal').exists():
                    raise ValueError('lifecycle output already exists; use audit-only refresh, not another save')
                job=dict(action='edit' if arm=='B' else ('a-pdf-alone' if mode=='pdf-alone' else 'a-reedit'),source=str(source),
                         output=str(output),spec=spec,old=case['reedit_old'],new=case['reedit_new'],font=str(font),directory=str(root/f'{name}-proposal'))
                if mode=='sidecar':job['sidecar']=str(source.with_suffix('.json'))
                result=worker(root,name,job)
                if arm=='A' and mode=='pdf-alone':output=root/f'{name}-proposal'/'flow-edited.pdf'
                if output.exists():
                    expected=case['expected'].replace(case['reedit_old'],case['reedit_new'])
                    result['audit']=audit(spec['path'],output,spec,case,root/f'{name}-audit',expected=expected,arm=arm)
                    nooutput=root/f'{name}-noop.pdf'
                    nojob=dict(action='noop' if arm=='B' else 'a-noop',source=str(output),output=str(nooutput),sidecar=str(output.with_suffix('.json')))
                    noresult=worker(root,name+'-noop',nojob)
                    if nooutput.exists():
                        noresult['pixels']=pixels(output,nooutput,root/f'{name}-noop-raster')
                        noresult['all_pages_zero']=all(r['outside_pixels']==0 for rows in noresult['pixels'].values() for r in rows)
                    result['noop']=noresult
                rows[mode]=result
                write(root/f'{arm}-lifecycle-{ident}.json',rows)
                print('lifecycle',arm,ident,mode,result['status'],result.get('audit',{}).get('outcome'),flush=True)


def refresh_lifecycle_audits(root,m,arms):
    """Read-only re-audit of saved evidence; never repeats edits or no-op saves."""
    for arm in arms:
        for case in m['requests']:
            ident=case['id'];spec=m['documents'][case['document']];summary=root/f'{arm}-lifecycle-{ident}.json'
            if not summary.exists():continue
            rows=json.loads(summary.read_text())
            for mode,result in rows.items():
                name=f'{arm}-{ident}-{mode}'
                output=root/f'{name}-reedited.pdf'
                if arm=='A' and mode=='pdf-alone':output=root/f'{name}-proposal'/'flow-edited.pdf'
                if not output.exists():continue
                expected=case['expected'].replace(case['reedit_old'],case['reedit_new'])
                result['audit']=audit(spec['path'],output,spec,case,root/f'{name}-audit',expected=expected,arm=arm)
                print('reaudit',arm,ident,mode,result['audit']['outcome'],flush=True)
            write(summary,rows)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='tmp/architecture-poc');parser.add_argument('--stage',choices=['initial','audit','lifecycle','refresh','all'],default='all');parser.add_argument('--arm',choices=['A','B','both'],default='both');args=parser.parse_args()
    root=Path(args.root);root.mkdir(parents=True,exist_ok=True);m=json.loads(MANIFEST.read_text());font=root/'provider.ttf'
    data=pymupdf.Font('cjk').buffer
    assert hashlib.sha256(data).hexdigest()==m['font']['sha256'];font.write_bytes(data)
    for spec in m['documents'].values():assert hashlib.sha256(Path(spec['path']).read_bytes()).hexdigest()==spec['sha256']
    if args.stage in ('initial','all'):initial(root,m,font)
    if args.stage in ('audit','all'):audits(root,m)
    if args.stage=='refresh':refresh_lifecycle_audits(root,m,('A','B') if args.arm=='both' else (args.arm,))
    if args.stage in ('lifecycle','all'):lifecycle(root,m,font,('A','B') if args.arm=='both' else (args.arm,))

if __name__=='__main__':main()
