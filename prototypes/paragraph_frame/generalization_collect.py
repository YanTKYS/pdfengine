"""Collect text-only evidence without regenerating any saved PDF."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import shutil
from .generalization_evaluate import MANIFEST,dump


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True,type=Path);args=parser.parse_args();root=args.root;dest=MANIFEST.parent;audits=dest/'audits';audits.mkdir(exist_ok=True)
    manifest=json.loads(MANIFEST.read_text());prior=json.loads(Path('evaluations/architecture_poc/results.json').read_text());paths=json.loads((root/'paths.json').read_text());qualification=json.loads((root/'final-qualification.json').read_text())
    result=dict(schema='paragraph-frame-generalization-results-1',base_commit=manifest['baseline_commit'],manifest_commit='a18019d',manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),completion_date='2026-10-22',
        source_sha256_unchanged={k:hashlib.sha256(Path(s['path']).read_bytes()).hexdigest()==s['sha256'] for k,s in manifest['documents'].items()},
        tests=dict(command='.venv/bin/pytest -q tests/test_paragraph_frame_auto.py tests/test_paragraph_frame_poc.py --durations=5',result=(root/'focused-final.txt').read_text(),full_suite='not run; focused policy in CLAUDE.md'),
        scope=dict(manual_geometry_inputs_B2=0,manual_layout_fields_B1=7,approval_count_B2=1,noop='PDF-alone unedited PdfWriter serialization, not layout/reflow no-op'),
        timing_policy='Single exploratory saves. Recorded B2 timing covers save including its recomputed analysis/layout/stage; separate proposal and approval latency was not measured. Initial saves preceded final stage hardening; all 24 candidates passed current validate_stage read-only. Audit timings may use cached renders. No RSS/p95/cold-warm or speedup claim.',
        limitations=['horizontal identity-matrix, zero-rise one-scalar/one-glyph adapter','limited ordered P/Span semantics','inferred occupied width is not author frame','mixed fallback visual family remains apparent','native cross-line search and inherited reader spaces remain quality limits'],
        independent_review=dict(reviewer='parent agent, independent of implementation',checks=['original hashes','strict foreign glyph counters','selected MuPDF crops B/C/D/F3/N3','proposal/oracle separation','scope and extraction audit review'],visual_finding='Original follower/font preservation improved; approved Droid fallback appears heavier than Mincho in mixed runs.'),cases={})
    def evidence(src):
        target=audits/src.name;shutil.copyfile(src,target);return str(target)
    for case in manifest['requests']:
        k=case['id'];spec=manifest['documents'][case['document']];output=Path(paths[k]);saved=json.loads(output.with_suffix('.save.json').read_text());initial=json.loads((root/(k+'.audit.json')).read_text());again=json.loads((root/(k+'.lifecycle.audit.json')).read_text());noop=json.loads((root/(k+'.noop.audit.json')).read_text())
        b2=dict(edit='saved',quality=initial['outcome'],failed_checks=initial['failed'],audit=evidence(root/(k+'.audit.json')),pdf_sha256=initial['output_sha256'],font_counts=saved['font_counts'],target_lines=len(saved['lines']),source_target_lines=len(spec['paragraphs'][0]['source_line_baselines']),lines=saved['lines'],follower_translation=saved['follower_translation'],followers_repainted=saved['followers_repainted'],timing=saved['timing'],
            final_guard_qualification=qualification[k],inference_vs_oracle=dict(paragraph_texts_match=qualification[k]['initial']['paragraphs']==[p['text'] for p in spec['paragraphs']],oracle_frame=spec['frame'],proposed_frame=qualification[k]['initial']['inferred']['frame']),
            lifecycle=dict(edit='saved in separate process, PDF-alone',quality=again['outcome'],failed_checks=again['failed'],audit=evidence(root/(k+'.lifecycle.audit.json')),noop=noop['outcome'],noop_audit=evidence(root/(k+'.noop.audit.json'))))
        arms={'B2':b2}
        if case['category']=='regression':
            arms['A']=dict(prior['cases'][k]['arms']['A'],evidence_reuse='PR86 frozen outputs; no product API changes since that measurement')
            arms['B1']=dict(prior['cases'][k]['arms']['B'],evidence_reuse='PR86 exact unchanged B1 implementation and requests')
        else:
            a=json.loads((root/('A-'+k+'.json')).read_text());b1=json.loads((root/('B1-'+k+'.json')).read_text());arms['B1']=b1;arms['A']=dict(baseline_routes=a,edit='saved' if a['outcome'].startswith('saved') else 'refused')
            if (root/('A-'+k+'.audit.json')).exists():
                aa=json.loads((root/('A-'+k+'.audit.json')).read_text());arms['A'].update(quality=aa['outcome'],failed_checks=aa['failed'],audit=evidence(root/('A-'+k+'.audit.json')),target_fidelity=aa['target_fidelity'])
                life=json.loads((root/('A-'+k+'.lifecycle.json')).read_text());arms['A']['lifecycle']=dict(outcome=life['outcome'],evidence=evidence(root/('A-'+k+'.lifecycle.json')),final_reason=life.get('auto_region',{}).get('reason'))
        result['cases'][k]=dict(document=case['document'],category=case['category'],old=case['old'],new=case['new'],expected=case['expected'],arms=arms)
    result['summary']=dict(initial_quality=dict(Counter(c['arms']['B2']['quality'] for c in result['cases'].values())),pdf_alone_reedit_quality=dict(Counter(c['arms']['B2']['lifecycle']['quality'] for c in result['cases'].values())),noop=dict(Counter(c['arms']['B2']['lifecycle']['noop'] for c in result['cases'].values())))
    dump(dest/'results.json',result);dump(dest/'final-qualification.json',qualification);print(json.dumps(result['summary'],indent=2))

if __name__=='__main__':main()
