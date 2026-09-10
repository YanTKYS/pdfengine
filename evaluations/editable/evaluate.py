"""External PDF editing semantics survive three generations without inference.

Evaluator-confirmed initial selection/break labels/roles are the only semantic
inputs. Subsequent edits consume the saved document, not newly proposed line
joins, decoration ranges, available widths or paint ownership.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import pymupdf
from pypdf import PdfReader,PdfWriter

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import state_object
from pdfeditor.editable import write_editable,edit_document,open_editable
from pdfeditor.selection import make_selection,source_sha
from evaluations.anchors.evaluate import review,long_edits,replay_gate,underline_pixels
from evaluations.attributed.evaluate import font_mapping_audit,original_code_audit
from evaluations.backend.followup import independent_edit_audit
from evaluations.elements.evaluate import audit_render,bounds,extraction
from evaluations.realpdf.evaluate import image_fingerprints,write_json

ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent


def audit(before,after,removed,report,directory):
    rendered=audit_render(before,after,1,directory/'edit',bounds(report['audit_bbox']))
    deleted=audit_render(before,removed,1,directory/'removal',bounds(report['audit_bbox']))
    original=extraction(before,directory,'before')
    independent_edit_audit(original,extraction(after,directory,'after'),1,report['before'],report['composed_text'])
    independent_edit_audit(original,extraction(removed,directory,'removed'),1,report['before'],'')
    font_mapping_audit(after,report);original_code_audit(before,report)
    underline_pixels(directory/'edit/after.png',report['anchors']['segments'])
    with pymupdf.open(before) as a,pymupdf.open(after) as b:
        if any(image_fingerprints(a[i])!=image_fingerprints(b[i]) for i in range(len(a))):
            raise ValueError('image content changed')
    return dict(poppler_outside_changed_pixels=rendered['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        removal_outside_changed_pixels=deleted['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        independent_text_and_removal=True,font_codes_gids_widths=True,images_unchanged=True,
        nontext_paint_plan=report['anchors']['nontext_paint_plan_verified'])


def generations(source,directory,font_dir,results):
    original_sha=source_sha(source)
    selection=make_selection(source,glyph_ids=list(range(257,509)),explicit_width=196)
    d=directory/'generation1';d.mkdir(parents=True)
    p,e,a=review(source,selection,d)
    expected=None;previous_state=None
    for generation in (1,2,3):
        d=directory/f'generation{generation}';d.mkdir(exist_ok=True)
        if generation>1:
            restored=open_editable(source,model)
            if restored['status']!='restored':raise ValueError(restored['reason'])
            previous_state=restored['state'];p=previous_state['paragraph']
            if p['text']!=expected:raise ValueError('logical text changed on reopen')
            selection=p['selection']
        gate,_=replay_gate(source,selection,d)
        output,model,removed=d/'edited.pdf',d/'editable.json',d/'removed.pdf'
        if generation==1:
            edits=long_edits(p,a)
            report=write_editable(source,output,model,p,edits,fonts={'s0':{'path':str(font_dir/'arial.ttf')}},
                element_snapshot=e,anchor_spec=a,max_bottom=155,removal_output=removed,
                boundary_kinds={139:'paragraph_boundary'})
            expected=report['after']  # Caller-confirmed first edit, including authored breaks.
        else:
            if generation==2:
                offset=expected.index(' certifiés et vérifiés')
                edits=[dict(start=offset,end=offset+len(' certifiés et vérifiés'),text='')]
            else:
                offset=expected.index('UL')+2
                edits=[dict(start=offset,end=offset,text=' vérifiés')]
            change=edits[0]
            expected=expected[:change['start']]+change['text']+expected[change['end']:]
            # No repeated selection, break insertion, width, role or font arguments.
            report=edit_document(source,previous_state,output,model,edits,removal_output=removed)
        write_json(d/'edits.json',dict(edits=edits));write_json(d/'report.json',report)
        restored=open_editable(output,model)
        if restored['status']!='restored':raise ValueError(restored['reason'])
        current=restored['state']
        if current['paragraph']['text']!=expected:raise ValueError('logical text not retained exactly')
        boundary=[b for b in current['boundaries'] if b['kind']=='paragraph_boundary']
        if len(boundary)!=1 or boundary[0]['offset']!=expected.index('\n'):
            raise ValueError('confirmed paragraph boundary did not follow the Unicode edit')
        if len(current['boundaries'])!=2 or expected.count('\n')!=2:
            raise ValueError('authored breaks were lost or generated soft wraps became hard breaks')
        decorated=current['anchors']['underlines'][0]['range']
        if decorated!=[0,expected.index('\n')]:raise ValueError('logical decoration range drifted')
        if previous_state and current['layout']!=previous_state['layout']:
            raise ValueError('confirmed region or layout limits changed')
        item=dict(case=f'generation{generation}',status='passed',classification='persistent_editing_semantics',
            source_sha256=source_sha(source),output_sha256=source_sha(output),model_sha256=current['model_sha256'],
            no_op_poppler_changed_pixels=gate['poppler_diff']['all_changed_pixels'],
            old_lines=report['old_line_count'],new_lines=report['new_line_count'],
            underline_segments=len(report['anchors']['segments']),retained_glyphs=report['retained_glyph_count'],
            provided_font_glyphs=report['provided_font_glyph_count'],reused_source_code_glyphs=report['reused_code_glyph_count'],
            exact_logical_text=True,authored_breaks=2,paragraph_boundaries=1,range_and_container_preserved=True,
            repeated_semantic_confirmation=False if generation>1 else True,
            **audit(source,output,removed,report,d))
        results.append(item);print(json.dumps(item),flush=True)
        write_json(directory/'generations.json',results)
        source=output
    return results,source,model,original_sha


def external_cases(source,model,directory):
    results=[]
    with pymupdf.open(source) as doc:box=list(doc[0].rect)
    for kind in ('missing_sidecar','pypdf_resave','mupdf_resave','external_change','damaged_sidecar'):
        d=directory/kind;d.mkdir()
        target=source;sidecar=model;visual=None;rewrite={}
        if kind=='missing_sidecar':sidecar=None
        elif kind=='damaged_sidecar':
            sidecar=json.loads(model.read_text(encoding='utf-8'));sidecar['pdf_sha256']='damaged'
        else:
            target=d/'external.pdf'
            if kind=='mupdf_resave':
                with pymupdf.open(source) as doc:doc.save(target,garbage=4,deflate=True)
            else:
                writer=PdfWriter(clone_from=PdfReader(source))
                writer.add_metadata({'/Title':'External save for semantic binding evaluation'})
                if kind=='external_change':writer.pages[0].add_transformation((1,0,0,1,2,0))
                writer.write(target)
            if kind!='external_change':
                # This is an external-save observation, never a gate that
                # authorizes editing that revision. Record renderer differences
                # independently of the mandatory stale-state refusal below.
                check=audit_render(source,target,1,d/'resave',box)
                visual=check['poppler_diff']['all_changed_pixels']
                before,after=PdfReader(source).pages[0],PdfReader(target).pages[0]
                rewrite=dict(page_boxes_equal=all(before.get(k)==after.get(k) for k in ('/MediaBox','/CropBox')),
                    decoded_contents_equal=before.get_contents().get_data()==after.get_contents().get_data(),
                    resource_values_equal=state_object(before['/Resources'])==state_object(after['/Resources']))
        opened=open_editable(target,sidecar)
        if opened['status']!='needs_confirmation' or not opened['observation']['lines']:
            raise ValueError('missing or stale semantic state did not fall back to physical observation')
        try:edit_document(target,sidecar,d/'prohibited.pdf',d/'prohibited.json',[])
        except PdfError:pass
        else:raise ValueError('stale semantic edit was accepted')
        if (d/'prohibited.pdf').exists() or (d/'prohibited.json').exists():
            raise ValueError('stale edit published an output')
        item=dict(case=kind,status='rejected',classification='safe_semantic_refusal_with_observation_fallback',
                  ordinary_pdf_readable=True,semantics='unknown',output_created=False,
                  resave_poppler_changed_pixels=visual,resave_visual_equivalent=None if visual is None else visual==0,
                  external_rewrite=rewrite,reason=opened['reason'])
        results.append(item);print(json.dumps(item),flush=True)
    return results


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True)
    parser.add_argument('--font-dir',type=Path,default=Path('C:/Windows/Fonts'))
    args=parser.parse_args();sys.stdout.reconfigure(encoding='utf-8')
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True,exist_ok=False)
    environment=dict(engine_sha256={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))},
        runner_sha256=source_sha(Path(__file__)),pymupdf=pymupdf.VersionBind)
    results=[]
    source=ROOT/'evaluations/realpdf/corpus/print_fcc_ms.pdf'
    try:
        results,edited,model,original=generations(source,directory,args.font_dir,results)
        results+=external_cases(edited,model,directory)
        if source_sha(source)!=original:raise ValueError('external original changed')
    except Exception as exc:
        results.append(dict(case='evaluation',status='failed_evaluation',error=str(exc),error_type=type(exc).__name__))
        print(json.dumps(results[-1],ensure_ascii=False),flush=True)
    summary=dict(environment=environment,counts=dict(Counter(r['status'] for r in results)),cases=results,
        scope='One external original and three editing generations; fixed container, no inter-element or page flow.')
    write_json(directory/'summary.json',summary);print(json.dumps(summary['counts']),flush=True)
    return int(any(r['status']=='failed_evaluation' for r in results))


if __name__=='__main__':raise SystemExit(main())
