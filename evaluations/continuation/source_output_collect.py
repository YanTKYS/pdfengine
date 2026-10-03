"""Read-only compact collection of completed or stopped Windows evidence."""
import json
from pathlib import Path

from pdfeditor.attributed import digest
from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import COMPENSATION_TOLERANCE
from evaluations.continuation import evaluate as single
from evaluations.continuation import canonical_metrics as metrics
from evaluations.continuation.source_output_external import read, ENGINE
from evaluations.continuation.source_slot_accumulation import runtime_digest

ORDER = ('overflow','grow-noop','second','second-noop','shorten','dormant-noop','regrow','noop1','noop2','noop3')


def renderers(rows):
    return dict(pages_checked=sorted(map(int,rows)),
        mupdf_all_equal=all(r['mupdf_equal'] for r in rows.values()),
        poppler_changed_pixels=sum(r['poppler_changed_pixels'] for r in rows.values()),
        poppler_outside_changed_pixels=sum(r.get('poppler_outside_changed_pixels',0) for r in rows.values()))


def source_coordinates(pdf, state, report):
    """Same plan/interpreter/MuPDF comparison as the existing continuation audit.

    The runtime's current source_output body range selects the observations;
    it does not replace paragraph binding or establish new ownership.
    """
    rows = {}
    for step in report['steps']:
        sid = step['slot_id']
        slot = state['slots'][sid]
        if slot.get('destination_id') is not None:
            continue
        selected = set(slot['binding']['paragraph']['selection']['glyph_ids'])
        page = state['regions'][slot['region_id']]['page']
        content = ContentPage(pdf,page)
        try:
            chars = [(e,c) for e in content.events for c in e.chars if selected.intersection(c.source_orders)]
            planned = step['report']['glyph_plan']
            if len(chars)!=len(planned):
                raise ValueError('source saved glyph count differs from plan')
            saved, interpreted_error, saved_error = [], 0.0, 0.0
            for wanted,(event,char) in zip(planned,chars):
                if (char.text!=wanted['unicode'] or char.code.hex()!=wanted['code']
                        or event.state.font.name!=wanted['font_resource'] or len(char.source_orders)!=1):
                    raise ValueError('source saved Unicode/code/font differs from plan')
                actual = content.actual[char.source_orders[0]]
                if actual['unicode']!=wanted['unicode'] or actual['gid']!=wanted['glyph_id']:
                    raise ValueError('source saved Unicode/GID differs from plan')
                interpreted_error=max(interpreted_error,*(abs(a-b) for a,b in zip(wanted['origin'],char.origin)))
                saved_error=max(saved_error,*(abs(a-b) for a,b in zip(wanted['origin'],actual['origin'])))
                saved.append(dict(unicode=actual['unicode'],glyph_id=actual['gid'],origin=list(actual['origin'])))
            if max(interpreted_error,saved_error)>=COMPENSATION_TOLERANCE:
                raise ValueError('source saved glyph coordinate tolerance exceeded')
            rows[sid]=dict(glyphs=len(saved),planned_to_interpreted_max_coordinate_error=interpreted_error,
                planned_to_saved_max_coordinate_error=saved_error, origin_tolerance=COMPENSATION_TOLERANCE,
                saved_glyph_origins_sha256=digest(saved),unicode_code_font_gid_equal=True)
        finally:
            content.close()
    return rows


def initial_bridge(pdf, row):
    """Locate the first mutation's marker-external parts, without claiming them."""
    result={}
    for sid,source in row['source_slots'].items():
        data=metrics.program(pdf,source['page'])
        [created]=[m for m in row['mutations'][str(source['page'])]
                   if m['owner']==sid and m['kind']=='source-output-create']
        start,end=source['marker_range']
        prefix=data[created['output_start']:start]
        suffix=data[end:created['output_start']+created['replacement_length']]
        before,after=list(operators(prefix)),list(operators(suffix))
        if not before or before[-1].name!='ET' or [o.name for o in after[:3]]!=['BT','Tm','TJ']:
            raise ValueError('initial external split/restore bridge is not the expected writer output')
        result[sid]=dict(external_prefix=dict(bytes=len(prefix),sha256=metrics.sha(prefix),
                            operators=[o.name for o in before]),
            external_suffix=dict(bytes=len(suffix),sha256=metrics.sha(suffix),operators=[o.name for o in after]),
            original_show_consumed_bytes=created['consumed_length'],
            residue_and_bridge_outside_owned_range=True,
            later_whole_prefix_suffix_identity_checked=True)
    return result


def collect_scenario(root, name):
    directory=root/name
    if not directory.exists():
        return dict(status='not-run', lifecycle_stages=[])
    formal=read(directory/'summary.json') if (directory/'summary.json').exists() else None
    audited=(formal or {}).get('stages', read(directory/'stages.json') if (directory/'stages.json').exists() else [])
    rows=[]
    initial=read(directory/'initial.json')
    source_aliases={p:{e['alias'] for e in v} for p,v in single.inventory(single.SOURCE,source=single.SOURCE)['pages'].items()}
    destination=next(iter(initial['continuation_destinations'].values()))
    for stage in ORDER:
        path=directory/(stage+'-source.json')
        if not path.exists():
            continue
        row=read(path)
        probe=stage in ('grow-noop','second-noop','dormant-noop')
        pdf=directory/stage/'saved.pdf' if probe else directory/(stage+'.pdf')
        side=pdf.with_suffix('.json')
        state=read(side)
        report=read(directory/stage/'report.json' if probe else directory/(stage+'-report.json'))
        proof=(read(directory/stage/'checks.json') if probe else
               next((r for r in audited if r['stage']==stage),None))
        row['audit_status']='passed' if proof else 'incomplete'
        if proof:
            row['renderer_checks']=renderers(proof['renderers'])
            row['glyph_unicode_cid_gid_w']=proof['all_page_unicode'] and proof['cid_gid_w']
            row['source_paint_images_annotations_original_fonts']=proof['all_nontext_paint_images_annotations_original_fonts']
        row['source_coordinates']=source_coordinates(pdf,state,report)
        if stage=='overflow':
            row['one_time_residue_bridge']=initial_bridge(pdf,row)
        row['font_inventory']=(proof['resources'] if proof and 'resources' in proof else
            dict(single.resources(pdf,state,destination,source_aliases),font_outcome=report['generated_font_outcome']))
        row['font_aliases']={p:sorted(v) for p,v in state['generated_fonts'].items()}
        row['pdf_header']=single.pdf_header(pdf)
        others={p:v for p,v in row['page_program'].items() if p not in single.EDITED}
        if any(not v['program_bytes_equal'] or v['delta_bytes'] or v['before_operators']!=v['after_operators'] for v in others.values()):
            raise ValueError('cannot omit a changed unedited page')
        row['unchanged_other_pages']=sorted(map(int,others))
        row['page_program']={p:v for p,v in row['page_program'].items() if p in single.EDITED}
        rows.append(row)
    result=dict(status='passed' if formal and formal['status']=='passed' and len(rows)==len(ORDER) else 'incomplete',
        lifecycle_stages=rows, destination=destination,
        failure=read(directory/'failure.json') if (directory/'failure.json').exists() else None)
    if formal:
        result.update(source_replay=formal['source_replay'], negative_controls=formal['negative_controls'],
            elapsed_seconds=formal['elapsed_seconds'], page_entry_comparison=formal.get('page_entry_comparison'))
    if result['status']=='passed':
        bystage={r['stage']:r for r in rows}
        baseline=bystage['overflow']
        for stage in ('regrow','noop1','noop2','noop3'):
            if any(r['body']!=bystage[stage]['source_slots'][sid]['body'] for sid,r in baseline['source_slots'].items()):
                raise ValueError('regrow/noop body differs from first')
        for base, noop in [('overflow','grow-noop'),('second','second-noop'),('shorten','dormant-noop'),
                           ('regrow','noop1'),('noop1','noop2'),('noop2','noop3')]:
            a,b=bystage[base],bystage[noop]
            if (a['source_coordinates']!=b['source_coordinates'] or
                a['saved_coordinates']['saved_glyph_origins_sha256']!=b['saved_coordinates']['saved_glyph_origins_sha256']):
                raise ValueError('no-op saved source/generated glyph origins changed')
            if any(a['font_inventory'][k]!=b['font_inventory'][k] for k in single.RESOURCE_COUNTS):
                raise ValueError('no-op font graph changed')
        result['all_first_second_dormant_regrow_noop_invariants_passed']=True
    return result


def final_status(gate, scenarios, supplemental):
    if gate!='eligible':
        return 'not-eligible', 'NOT ELIGIBLE — VALIDATION STOPPED'
    if (set(scenarios)=={'page-entry','boundary'} and all(v['status']=='passed' for v in scenarios.values()) and
        all(supplemental.get(n,{}).get('status')=='passed' for n in ('scaled-ctm','creation-empty','operational','adjacent'))):
        return 'passed', 'PASS — WINDOWS EXTERNAL VALIDATION'
    return 'failed', 'FAIL — RUNTIME FOLLOW-UP REQUIRED'


def collect(root):
    result=read(root/'eligibility.json')
    result['eligibility']=result.pop('scenarios')
    scenarios={n:collect_scenario(root,n) for n in ('page-entry','boundary')}
    supplemental=read(root/'supplemental.json')
    status,label=final_status(result['status'],scenarios,supplemental)
    comparison={}
    for name,scenario in scenarios.items():
        comparison[name]={r['stage']:{p:dict(bytes=r['page_program'][p]['delta_bytes'],
            operators=r['page_program'][p]['after_operators']-r['page_program'][p]['before_operators'])
            for p in ('4','5')} for r in scenario['lifecycle_stages']
            if r['stage'] in ('grow-noop','second-noop','dormant-noop','noop1','noop2','noop3')}
    cross_scenario=None
    if all(s['status']=='passed' for s in scenarios.values()):
        for a,b in zip(scenarios['page-entry']['lifecycle_stages'],scenarios['boundary']['lifecycle_stages']):
            if (a['source_coordinates']!=b['source_coordinates'] or
                a['saved_coordinates']['saved_glyph_origins_sha256']!=b['saved_coordinates']['saved_glyph_origins_sha256'] or
                any(r['body']!=b['source_slots'][sid]['body'] for sid,r in a['source_slots'].items())):
                raise ValueError('scenario source bodies or saved glyph origins differ')
        cross_scenario=dict(source_bodies_equal=True, source_and_generated_saved_origins_equal=True,
                            source_markers='flow-specific identity; whole-page SHA need not match across flows')
    result.update(status=status,final_status=label,scenarios=scenarios,supplemental=supplemental,
        real_pdf_saves=sum(len(s['lifecycle_stages']) for s in scenarios.values()),
        historical_comparison=dict(path='evaluations/continuation/generated-block-canonical-summary.json',
            sha256=single.source_sha(single.BASE/'generated-block-canonical-summary.json'),
            per_noop={'4':dict(bytes=4420,operators=368),'5':dict(bytes=14312,operators=1117)},
            source_total=dict(bytes=18732,operators=1485),historical_record_unchanged=True,
            current_v2_noop_deltas=comparison), cross_scenario=cross_scenario,
        engine_digest_after=runtime_digest(),engine_unchanged=runtime_digest()==ENGINE,
        source_unchanged=single.source_sha(single.SOURCE)==single.SOURCE_SHA,
        providers_unchanged=all(single.source_sha(p['path'])==p['sha256'] for p in result['start']['providers']),
        tests=read(root/'tests.json') if (root/'tests.json').exists() else dict(
            execution='not recorded; this collector does not run pytest', full_suite_run=False),
        limitations=['one exact reviewed LibreOffice PDF, source pages 4/5 and the two existing page 6 destinations',
            'general editable remains NOT READY; no generic PDF cleanup or tagged-PDF scope expansion',
            'synthetic scale/empty fixtures are separate supplemental evidence'],
        evaluator_sha256={p.name:single.source_sha(p) for p in single.BASE.glob('source_output_*.py')})
    if not all(result[k] for k in ('engine_unchanged','source_unchanged','providers_unchanged')):
        raise ValueError('external inputs or runtime changed')
    result['evaluation_attempts']=[read(p) for p in sorted(root.glob('attempt-*.json'))]
    return result


def write_compact(path,value):
    # One line per stage keeps the public artifact small without dropping metrics.
    rendered=json.dumps(value,ensure_ascii=False,indent=2)
    for scenario in value['scenarios'].values():
        for row in scenario['lifecycle_stages']:
            pretty=json.dumps(row,ensure_ascii=False,indent=2)
            # Locate via the exact indentation used under scenarios/lifecycle_stages.
            indented='\n'.join('        '+line for line in pretty.splitlines())
            rendered=rendered.replace(indented,'        '+json.dumps(row,ensure_ascii=False,separators=(',',':')))
    path.write_text(rendered+'\n',encoding='utf-8',newline='\n')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    root=single.BASE/'runs'/args.run
    evidence=collect(root)
    single.write(root/'source-output-summary.json',evidence)
    if args.publish:
        write_compact(single.BASE/'source-output-external-summary.json',evidence)
    # The Windows console may be cp932; the UTF-8 JSON keeps the full label.
    print(evidence['status'])
