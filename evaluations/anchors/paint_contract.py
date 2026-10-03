"""Design evidence for PR #35 gates; no runtime ownership implementation.

Pure geometry/policy models and current-runtime observations only. Synthetic
current-runtime saves use existing APIs; prototype bytes are never installed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import source_ctms
from pdfeditor.editable import write_editable, open_editable, edit_document
from pdfeditor.backend import PdfError
from evaluations.anchors.canonical_geometry import format_body, recipe
from evaluations.anchors.paint_ownership import fixture
from evaluations.continuation.canonical_metrics import program, sha
from evaluations.continuation.source_slot_accumulation import dump, runtime_digest
from evaluations.continuation.source_output_probes import render_noop
from evaluations.anchors.paint_boundary_analysis import evaluate_boundaries

ROOT = Path(__file__).resolve().parents[2]
BASE = 'fdba3dcb44b0fe4010a3ea1fc986b3aac83da2e6'


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def identity(owner, creation_model, source_ids, initial_range):
    """Pure identity example. Creation model digest distinguishes new lifetimes."""
    anchor = digest(dict(domain='pdfengine-anchor-v1', owner=owner, kind='underline',
        model=creation_model, source_ids=sorted(source_ids), range=initial_range))
    marker = sha(b'pdfengine-paint-v1\0'+anchor.encode('ascii'))
    return anchor, marker


def inventory_policy(claimed, discovered, *, current_revision=True):
    """Symbolic sets already discovered by a future parser, NOT a marker parser.

    One independently managed paint-owning editable per PDF document. Other
    ownership domains are filtered by the caller; no permission is inferred.
    """
    if not current_revision:
        raise ValueError('STALE_OWNER_MODEL')
    if len(discovered) != len(set(discovered)):
        raise ValueError('INVALID_COLLIDING')
    if set(claimed) != set(discovered):
        raise ValueError('DOCUMENT_OWNER_INVENTORY_MISMATCH')
    return 'OWNED_BY_CURRENT' if claimed else 'NO_PAINT_OWNER'


def transition(groups, action, *, validated=False, paragraph_empty=False, target=None):
    """Pure lifecycle algebra; validated is a premise, not a PDF validation stub."""
    if not validated:
        raise ValueError('OWNERSHIP_UNPROVEN')
    if paragraph_empty:
        raise ValueError('FULL_EMPTY_REFUSED')
    if action == 'revive':
        raise ValueError('REVIVAL_UNSUPPORTED')
    result = deepcopy(groups)
    if action == 'delete':
        if target not in result:
            raise ValueError('UNKNOWN_ACTIVE_GROUP')
        removed = result.pop(target)
        return result, dict(effect='delete_complete_block_and_record', range=removed['block_range'])
    if action == 'noop':
        return result, dict(effect='replace_body', identity_unchanged=True)
    raise ValueError('UNKNOWN_ACTION')


def source_context(pdf, path):
    """Read-only path terminal boundary/CTM evidence, no future authority grant."""
    data = program(pdf, 1)
    index = path['source']['operator_index']
    exact = source_ctms(data)[index]
    if exact is None:
        raise ValueError('unproven source-decimal CTM')
    with_context = ContentPage(pdf, 1)
    try:
        boundary = with_context.boundaries[index]
        state = boundary.state.report()
        return dict(exact_ctm=[str(x) for x in exact], interpreter_ctm=state['ctm'],
            at_terminal_exit=dict(pending_path=boundary.pending_path, pending_clip=boundary.pending_clip,
                text_object=boundary.text_object, marked=boundary.marked_content_depth,
                compatibility=boundary.compatibility_depth, q_depth=boundary.q_depth),
            fill=state['fill'], opacity=state['opacity'], other=state['other'], clip=state['clip'])
    finally:
        with_context.close()


def planned_segments(report, group):
    a, b = group['output_range']
    result = []
    for line in report['lines']:
        glyphs = [g for g in report['glyph_plan'] if g['start'] < min(b, line['end'])
                  and g['end'] > max(a, line['start'])]
        while glyphs and glyphs[0]['unicode'].isspace():
            glyphs.pop(0)
        while glyphs and glyphs[-1]['unicode'].isspace():
            glyphs.pop()
        if glyphs:
            result.append([glyphs[0]['origin'][0], glyphs[-1]['origin'][0]+glyphs[-1]['advance'], line['baseline']])
    return result


def stage(report, fixed_recipe, context, source=None):
    glyphs = [{k: g[k] for k in ('unicode', 'start', 'end', 'origin', 'advance', 'style_id', 'size', 'provider')}
              for g in report['glyph_plan']]
    geometry = [{k: g[k] for k in ('start', 'end', 'origin', 'advance')} for g in glyphs]
    lines = [{k: line[k] for k in ('start', 'end', 'x', 'baseline', 'width')} for line in report['lines']]
    segments = planned_segments(report, report['anchors']['groups'][0])
    body, coordinates = format_body(fixed_recipe, segments, context['exact_ctm'], page_height=260)
    source_metrics=[]
    if source is not None:
        content=ContentPage(source,1)
        try:
            by_id={i:(event,char) for event in content.events for char in event.chars for i in char.source_orders}
            for glyph in report['glyph_plan']:
                if glyph['source_index'] is None:
                    source_metrics.append(dict(start=glyph['start'],provider=glyph['provider'],
                        nominal_pdf_width=glyph['nominal_pdf_width']))
                else:
                    event,char=by_id[glyph['source_index']]
                    source_metrics.append(dict(start=glyph['start'],provider='original',width_1000em=char.pdf_width,
                        text_space_advance=char.advance,Tf=event.state.size,Tz=event.state.tz,
                        Tc=event.state.tc,Ts=event.state.ts,font_resource=event.state.font.name))
        finally:
            content.close()
    return dict(text=report['after'], glyph_geometry_sha256=digest(geometry),
        line_allocation_sha256=digest([[l['start'], l['end']] for l in lines]),
        style_ids_sha256=digest([g['style_id'] for g in glyphs]), glyphs=glyphs, lines=lines,
        styles=report['styles'], source_font_metrics=source_metrics, alignment=report['alignment'],
        planned_segments=segments,
        prototype=dict(bytes=len(body), sha256=sha(body), operators=len(list(operators(body))),
            body=body.decode(), segment_coordinates=coordinates))


def differences(a, b):
    ga, gb = a['glyphs'], b['glyphs']
    if [(g['start'],g['end']) for g in ga] != [(g['start'],g['end']) for g in gb]:
        raise ValueError('not comparing the same logical glyph intervals')
    origins = [max(abs(x-y) for x,y in zip(g['origin'],h['origin'])) for g,h in zip(ga,gb)]
    advances = [abs(g['advance']-h['advance']) for g,h in zip(ga,gb)]
    return dict(glyph_geometry_exact=a['glyph_geometry_sha256']==b['glyph_geometry_sha256'],
        max_origin_delta=max(origins, default=0), max_advance_delta=max(advances, default=0),
        changed_advances=sum(v!=0 for v in advances),
        line_allocation_exact=a['line_allocation_sha256']==b['line_allocation_sha256'],
        style_ids_exact=a['style_ids_sha256']==b['style_ids_sha256'],
        prototype_body_exact=a['prototype']['body']==b['prototype']['body'])


def observe_geometry(root, variant):
    folder=root/variant
    folder.mkdir()
    source, specs=fixture(folder, 'lifecycle' if variant=='identity' else 'ctm')
    paragraph, options=specs[0]
    source_ids=options['anchor_spec']['underlines'][0]['source_ids']
    path=next(p for p in options['element_snapshot']['paths'] if p['source_id']==source_ids[0])
    context=source_context(source,path)
    pdf, model=folder/'first.pdf',folder/'first.json'
    report=write_editable(source,pdf,model,paragraph,[dict(start=4,end=7,text='FIVE SEVEN')],**options)
    dump(folder/'first-report.json',report)
    group=report['anchors']['groups'][0]
    fixed=recipe(group['offset'],group['thickness'])
    result=dict(context=context,recipe=fixed,stages={'first':stage(report,fixed,context,source)},comparisons={})
    previous='first'
    for name, edits in [('noop1',[]),('noop2',[]),('noop3',[]),
                        ('change',[dict(start=4,end=9,text='')]),('change_noop',[])]:
        out,saved,request,response=[folder/f'{name}{s}' for s in ('.pdf','.json','-request.json','-response.json')]
        dump(request,edits)
        run=subprocess.run([sys.executable,'-m','evaluations.anchors.paint_ownership','--worker',
            *map(str,(pdf,model,out,saved,request,response))],cwd=ROOT,capture_output=True,text=True)
        if run.returncode:
            raise RuntimeError(run.stderr)
        value=json.loads(response.read_text(encoding='utf-8'))
        if value['status']!='saved':
            raise ValueError(value)
        report=value['report']
        result['stages'][name]=stage(report,fixed,context,pdf)
        if not edits:
            compared=differences(result['stages'][previous],result['stages'][name])
            compared['renderer']=render_noop(pdf,out,folder/f'{name}-render')
            if not compared['renderer']['passed']:
                raise ValueError('current-runtime no-op renderer mismatch')
            result['comparisons'][previous+'->'+name]=compared
        print(variant,name,result['stages'][name]['prototype']['sha256'],flush=True)
        pdf,model,previous=out,saved,name
    return result


def observe_sidecars(root):
    folder=root/'sidecars'
    folder.mkdir()
    # Current runtime can coordinate both sidecars. Independent save cannot.
    from evaluations.anchors.paint_ownership import first
    pdf,_,states,_=first(folder,'two')
    out,saved=folder/'A.pdf',folder/'A.json'
    edit_document(pdf,states[0],out,saved,[])
    stale=open_editable(out,states[1])
    edited=json.loads(saved.read_text(encoding='utf-8'))
    # Show that changing just the PDF hash cannot refresh the global snapshot.
    from pdfeditor.editable import _seal
    patched=deepcopy(states[1]);patched.pop('model_sha256');patched['pdf_sha256']=edited['pdf_sha256']
    forged=open_editable(out,_seal(patched))
    b_out,b_state=folder/'B.pdf',folder/'B.json'
    try:
        edit_document(out,states[1],b_out,b_state,[])
        raise AssertionError('stale B unexpectedly edited')
    except PdfError as exc:
        b_refusal=str(exc)
    next_pdf,next_state=folder/'A2.pdf',folder/'A2.json'
    edit_document(out,saved,next_pdf,next_state,[])
    return dict(B_before=open_editable(pdf,states[1])['status'], B_after_A=stale['status'],
        reason=stale.get('reason'), hash_only_refresh=forged['status'], hash_only_reason=forged.get('reason'),
        A_after_A=open_editable(out,saved)['status'],
        B_edit_refusal=b_refusal, B_published=b_out.exists() or b_state.exists(),
        A_second_edit=open_editable(next_pdf,next_state)['status'],
        narrow_sequence=[['confirm A', 'valid'],['confirm B owned','refuse: DOCUMENT_OWNER_INVENTORY_MISMATCH'],
            ['edit A','valid'],['reopen B legacy','refuse: STALE_OWNER_MODEL'],
            ['edit B owned','refuse: DOCUMENT_OWNER_INVENTORY_MISMATCH'],
            ['reopen A','valid'],['edit A','valid']])


def evaluate(root):
    result=dict(base=BASE,runtime_sha256=runtime_digest(),scope='NARROW V1',
        method='Pure prototypes plus unmodified current-runtime observation; prototype bytes never installed',
        geometry={v:observe_geometry(root,v) for v in ('identity','ctm')},sidecars=observe_sidecars(root),
        boundaries=evaluate_boundaries(root/'boundaries'),state_model=policy_examples())
    result['verdict']='NOT READY' if any(not c['glyph_geometry_exact'] or not c['prototype_body_exact']
        for case in result['geometry'].values() for c in case['comparisons'].values()) else 'REVIEW REQUIRED'
    if runtime_digest()!=result['runtime_sha256']:
        raise ValueError('runtime changed')
    return result


def policy_examples():
    """Explicit transition cases; no actual PDF or marker validation here."""
    owner=digest('owner A')
    a,ma=identity(owner,digest('creation revision 1'),['source-underline'],[0,7])
    b,mb=identity(owner,digest('creation revision 2'),['new-confirmed-source'],[0,7])
    groups={a:dict(marker_id=ma,block_range=[100,200])}
    active,noop=transition(groups,'noop',validated=True)
    terminated,deletion=transition(groups,'delete',target=a,validated=True)
    cases={}
    for name,call in {
        'unknown_other_owner':lambda:inventory_policy([ma],[ma,mb]),
        'marker_without_record':lambda:inventory_policy([],[ma]),
        'lost_sidecar':lambda:inventory_policy([],[ma]),
        'stale_sidecar':lambda:inventory_policy([ma],[ma],current_revision=False),
        'duplicate_marker':lambda:inventory_policy([ma],[ma,ma]),
        'missing_marker':lambda:inventory_policy([ma],[]),
        'old_identity_revival':lambda:transition(terminated,'revive',validated=True,target=a),
        'full_empty':lambda:transition(groups,'delete',validated=True,target=a,paragraph_empty=True),
        'unproven_termination':lambda:transition(groups,'delete',target=a),
    }.items():
        try:
            call()
            raise AssertionError('expected a policy refusal')
        except ValueError as exc:
            cases[name]=str(exc)
    return dict(owned=inventory_policy([ma],[ma]),no_owner=inventory_policy([],[]),
        active_identity_unchanged=active==groups,termination=deletion,groups_after_termination=terminated,
        new_creation_identity_differs=a!=b and ma!=mb,refusals=cases,
        premise='Input block authenticity/complete range/containment verified before transition; model does not verify PDFs')


def write_summary(path, value):
    value=deepcopy(value)
    tables={}
    for case in value['geometry'].values():
        for measured in case['stages'].values():
            for key in ('glyphs','styles','source_font_metrics'):
                token=f'__table_{len(tables)}__'
                tables[token]='[\n'+',\n'.join('          '+json.dumps(row,separators=(',',':'))
                                                for row in measured[key])+'\n        ]'
                measured[key]=token
    text=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    for token,table in tables.items():
        text=text.replace(json.dumps(token),table)
    path.write_text(text+'\n',encoding='utf-8',newline='\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.work.mkdir(parents=True,exist_ok=False)
    write_summary(args.output,evaluate(args.work.resolve()))


if __name__=='__main__':
    main()
