"""Collect text-only measured evidence; never copy PDFs/fonts/raster artifacts."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
from datetime import datetime, timezone
import subprocess
from .preflight import MANIFEST
from .evaluate import write


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def collect(root,destination):
    root=Path(root);destination=Path(destination);(destination/'audits').mkdir(parents=True,exist_ok=True)
    manifest=json.loads(MANIFEST.read_text())
    log=root/'focused-tests-final.log'
    match=re.search(r'(\d+) passed in ([0-9.]+)s',log.read_text()) if log.exists() else None
    test_record=dict(command='.venv/bin/python -m pytest -q tests/test_paragraph_frame_poc.py --durations=5',passed=int(match[1]) if match else None,seconds=float(match[2]) if match else None,full_suite='not run; independent prototype only; production modules unchanged')
    review=root/'independent-review.json'
    reviewed=json.loads(review.read_text()) if review.exists() else dict(status='not reviewed')
    result=dict(schema='paragraph-frame-comparison-results-1',baseline_commit='8fa868f',manifest_commit='35b06b6',manifest_sha256=digest(MANIFEST),
      dates=dict(initial_edits=datetime.fromtimestamp((root/'A.pdf').stat().st_mtime,timezone.utc).date().isoformat(),completion=datetime.now(timezone.utc).date().isoformat()),
      environment=dict(platform=platform.platform(),python=platform.python_version(),versions={name:importlib.metadata.version(name) for name in ['PyMuPDF','pypdf','uharfbuzz','fonttools','uniseg','Pillow']}),
      sdk=dict(arm='C',status='unverified',reason='No evaluation content-editor API/license supplied; PDFNetPython3 module unavailable. No SDK performance claim.'),
      timing_policy='One exploratory first save per request, no cold/warm guarantee, no p95 or general speedup. A intent and proposal/accept/save measured separately; B analysis/layout/save+readback measured separately. Audit seconds include reusable raster cache. RSS was not measured. Initial B timings precede extra fail-closed guards; final read-only qualification verifies the same source/layout.',
      source_sha256_unchanged={key:digest(spec['path'])==spec['sha256'] for key,spec in manifest['documents'].items()},
      final_guard_qualification=json.loads((root/'final-guard-qualification.json').read_text()),
      tests=test_record,independent_review=reviewed,
      cases={})
    for case in manifest['requests']:
        ident=case['id'];record=dict(document=case['document'],old=case['old'],new=case['new'],expected=case['expected'],arms={})
        for arm in ('A','B'):
            value={};pdf=root/f'{ident}.pdf' if arm=='B' else root/'baseline-final'/ident/'flow-edited.pdf'
            if arm=='A':
                baseline=json.loads((root/f'baseline-{ident}.json').read_text())
                value['baseline_routes']=dict(edit_intent={k:baseline['edit_intent'].get(k) for k in ('status','reason','route')},
                    page_flow={k:baseline['page_flow'].get(k) for k in ('status','saved','acceptance_refusal')})
                value['timing']=baseline['timing']
            else:
                saved=json.loads((root/f'{ident}.json').read_text())
                value['timing']=saved.get('timing');value['planned_lines']=saved.get('lines')
                value['source_glyphs_removed']=saved.get('source_glyphs_removed')
                value['source_show_operators_rewritten']=saved.get('source_show_operators_rewritten')
            if not pdf.exists():
                value.update(edit='refused',quality='not evaluated',lifecycle='not applicable: no output')
            else:
                value.update(edit='saved',pdf_sha256=digest(pdf))
                audit=json.loads((root/f'{arm}-audit-final-{ident}.json').read_text());apath=destination/'audits'/f'{arm}-{ident}-initial.json';write(apath,audit)
                value.update(quality=audit['outcome'],audit=str(apath),failed_checks=[k for k,v in audit['checks'].items() if not v],
                             verification_seconds=audit['seconds'],physical_lines=audit['physical_lines'])
                quality_keys={'mupdf_search_new'}
                value['safety_checks_passed']=all(v for k,v in audit['checks'].items() if k not in quality_keys)
                lifecycle=json.loads((root/f'{arm}-lifecycle-{ident}.json').read_text());modes={}
                for mode,item in lifecycle.items():
                    row={k:item.get(k) for k in ('status','reason','reopen','seconds') if item.get(k) is not None}
                    if item.get('record',{}).get('page_flow'):
                        row['refusal']=item['record']['page_flow'].get('acceptance_refusal')
                        row['routes']={k:{f:v.get(f) for f in ('status','reason','acceptance_refusal')} for k,v in item['record'].items() if k in ('edit_intent','page_flow')}
                    if 'audit' in item:
                        apath=destination/'audits'/f'{arm}-{ident}-{mode}-reedit.json';write(apath,item['audit'])
                        row.update(quality=item['audit']['outcome'],audit=str(apath),failed_checks=[k for k,v in item['audit']['checks'].items() if not v])
                    if 'noop' in item:row['noop']=item['noop']
                    modes[mode]=row
                value['lifecycle']=modes
            record['arms'][arm]=value
        result['cases'][ident]=record
    write(destination/'results.json',result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default='tmp/architecture-poc');p.add_argument('--destination',default='evaluations/architecture_poc');args=p.parse_args()
    result=collect(args.root,args.destination)
    print(json.dumps({k:{a:(v['edit'],v['quality']) for a,v in c['arms'].items()} for k,c in result['cases'].items()},ensure_ascii=False))
