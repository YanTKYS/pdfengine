"""Audit the changed collision boundary after the existing FCC lifecycle run.

Uses stored stage reports/renders and adds source-geometry negative controls;
it does not repeat the document lifecycle or regenerate its source no-op.
"""
import argparse
import json
from pathlib import Path

from pdfeditor.backend import PdfError
from pdfeditor.document_flow import edit_flow, open_document
from pdfeditor.elements import move_element
from pdfeditor.ink_collision import POLICY, MARGIN_PT, certify_text_translation, source_ink
from pdfeditor.selection import source_sha
from evaluations.document_flow.evaluate import audit


BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]


def read(path):return json.loads(path.read_text(encoding='utf-8'))


def run(lifecycle,directory):
    summary=read(lifecycle/'summary.json')
    if summary['result']['status']!='passed':raise ValueError('complete lifecycle evidence required')
    engine={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    prior=summary['environment']['engine_sha256']
    changed=[name for name in set(prior)|set(engine) if prior.get(name)!=engine.get(name)]
    if any(name!='elements.py' for name in changed):
        raise ValueError('only the movement boundary may differ from the reused lifecycle core')
    result=summary['result']
    if result['source_sha256']!='11ad813fce341afed902f98f7c5335f346bd9e746c648a509c1c7f9ba4f7f3fd':
        raise ValueError('this evaluation reviews the FCC source selection')
    stages=[]
    for record in result['stages']:
        name=record['stage'];pdf=lifecycle/f'{name}.pdf'
        if source_sha(pdf)!=record['output_sha256']:raise ValueError('stored stage PDF changed')
        report=read(lifecycle/f'{name}-report.json')
        certificates=[]
        for step in report['steps']:
            proof=step['report'].get('text_collision')
            if proof and proof['policy']==POLICY:
                if proof['status']!='disjoint':raise ValueError('saved movement lacks a separation certificate')
                if proof['source_sha256']!=step['report']['source_sha256']:
                    raise ValueError('stored outline certificate belongs to a different mutation revision')
                certificates.append({k:v for k,v in proof.items() if k not in ('selected_glyphs','broad_phase_conflicts')})
                certificates[-1]['broad_phase_conflict_count']=len(proof['broad_phase_conflicts'])
                certificates[-1]['selected_glyph_count']=len(proof['selected_glyphs'])
        stages.append(dict(record,outline_certificates=certificates))
    if not next(r for r in stages if r['stage']=='g2')['outline_certificates']:
        raise ValueError('previously blocked shortening did not exercise geometry')

    # The final review added an explicit certificate/mutation SHA comparison.
    # Re-run that affected g2 transaction on the final core, reusing the other
    # completed lifecycle stages as historical evidence with their own hashes.
    before=lifecycle/'g1.pdf';model=read(lifecycle/'g1.json')
    old=model['elements']['A']['binding']['paragraph']['text']
    source=directory/'g2.pdf';model_output=directory/'g2.json'
    current=edit_flow(before,model,source,model_output,'A',
        [dict(start=0,end=len(old),text='Check PDF 2026.',style_id='s0')])
    (directory/'g2-report.json').write_bytes((json.dumps(current,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    current_audit=audit(before,source,current,directory/'g2-audit')
    if source_sha(source)!=source_sha(lifecycle/'g2.pdf'):
        raise ValueError('final core produced different g2 bytes; review before reusing later lifecycle stages')
    restored=open_document(source,model_output)
    if restored['status']!='restored':raise ValueError(restored['reason'])
    state=restored['state']
    if state['container']!=model['container'] or state['follows']!=model['follows'] or list(state['elements'])!=list(model['elements']):
        raise ValueError('final core changed logical contracts')
    b=state['elements']['B']['binding'];selected=set(b['paragraph']['selection']['glyph_ids'])
    gap=state['follows'][0]['gap']
    probes=[]
    for name,dy in [('insufficient_clearance',-2),('same_baseline',-gap)]:
        proof=certify_text_translation(source,1,selected,0,dy)
        if proof['status']!='not_certified':raise ValueError('unsafe displacement unexpectedly certified')
        output=directory/f'{name}.pdf'
        try:move_element(source,output,b['element'],b['relations'],dx=0,dy=dy,paragraph_snapshot=b['paragraph'])
        except PdfError as exc:
            if output.exists():raise ValueError('refused move was published')
            probes.append(dict(case=name,dy=dy,status='safely_refused',reason=str(exc),output_created=False))
        else:raise ValueError('unsafe displacement unexpectedly published')
    outlines=source_ink(source,1)
    if outlines['status']!='proven':raise ValueError(outlines['reason'])
    return dict(schema='pdfengine-ink-collision-evaluation-1',source_url=result['source_url'],
        source_sha256=result['source_sha256'],lifecycle_environment=summary['environment'],
        final_environment=dict(engine_sha256=engine,runner_sha256=source_sha(Path(__file__))),
        changed_core_files_since_lifecycle=sorted(changed),
        final_core_g2=dict(status='passed',same_pdf_bytes_as_lifecycle=True,reopened=True,
                          pdf_sha256=source_sha(source),**current_audit),
        lifecycle_directory_hint='evaluations/document_flow/runs/<run-name>',
        policy=POLICY,margin_per_outline_pt=MARGIN_PT,
        previous_boundary='g2 refused by unselected font bbox overlap in document_flow evaluation at 752b09f',
        source_replay={k:{p:v for p,v in gate.items() if p!='reused_from'} for k,gate in result['source_replay'].items()},
        font_policy=result['font_policy'],font_sha256=result['font_sha256'],
        stages=stages,negative_controls=result['negative_controls']+probes,
        g2_embedded_bitmap_glyphs_enclosed=sum(v.get('embedded_bitmap_enclosed',False) for v in outlines['ink'].values()),
        semantic_contract='same confirmed container, follows gap, ownership and authored identities across all stages',
        scope='text-only rigid follower translation; fixed vectors/images/clip/ownership checks retained; no resize or simultaneous layout',
        limits=['Sufficient disjoint envelopes, not an exact outline intersection solver.',
                'New permission requires complete unique page text correspondence and static embedded TrueType fills.',
                'Stroke, transparency, substitute/synthetic/color/variable/Type3 fonts and unsupported bitmap/scopes remain unknown.',
                'Clipping never reduces the envelope; all supported monochrome bitmap strikes are enclosed.',
                'The clearance is a physical policy evaluated at 144dpi; arbitrary low-resolution rasterization is not guaranteed.',
                'Composed-text obstacle checks and owned-paint group bounds remain conservative.',
                'No previously valid sidecar is rewritten merely to adopt the collision policy. Evidence is recomputed for each revision.'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lifecycle',type=Path,required=True)
    parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True,exist_ok=False)
    result=run(args.lifecycle,directory)
    (directory/'summary.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
    print(json.dumps(dict(stages=len(result['stages']),negative_controls=len(result['negative_controls']),
                         geometry_certificates=sum(len(s['outline_certificates']) for s in result['stages'])),indent=2))


if __name__=='__main__':main()
