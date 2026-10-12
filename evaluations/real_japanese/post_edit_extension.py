"""Extend the live saved A/B flow; never replay its edits from an expanded original."""
import argparse
from copy import deepcopy
import json
import logging
from pathlib import Path
import subprocess

import pymupdf
from PIL import Image, ImageChops, ImageDraw

from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import _Analysis, propose_page_flow, replace_in_flow
from pdfeditor.owned_flow_proposal import propose_owned_flow, propose_owned_flow_extension, accept_owned_flow_report
from pdfeditor.shared_flow import edit_shared_flow, open_shared_flow
from pdfeditor.continuation import inspect_continuation_boundaries, slot_id
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from pdfeditor.source_ownership import immutable
from pdfeditor.shaped_font import ShapedFont
from .extended_joint_edit import FIND, LONG, SECOND, BREAK, GROUPS, RELATIONSHIP
from .owned_body_edit import SOURCE, URL, SHA, audit
from .continuation_edit import FONT_SHA, write, chars
from .vacated_space_edit import audit_original_noop

SHORT = LONG[:LONG.index('。', len(FIND)) + 1]


def choices(pages):
    return {str(p['binding']['page']): {s['id']:dict(sha256=FONT_SHA,font_index=0,relation='substituted')
            for s in p['styles']} for p in pages}


def bootstrap(output,font):
    pages=[]
    for number,ids in [(8,range(24,28)),(9,range(1,4))]:
        kw=dict(line_ids=[f'p{number}-l{i}' for i in ids],font_candidates=[font],
                include_embedded_fonts=True,font_cache=output/'fonts')
        observed=propose_page_flow(SOURCE,number,**kw)
        pages.append(propose_page_flow(SOURCE,number,**kw,
            new_text={s['id']:LONG+SECOND[1] for s in observed['styles']}))
    region=dict(page=9,bounds=[56.79999923706055,110,540,174],x=56.79999923706055,
                width=483.20000076293945,first_baseline=122)
    boundary=next(c for c in inspect_continuation_boundaries(SOURCE,9)['candidates'] if c['ordinal']==0)
    request=dict(region_id='vacated-9',before_region='page-9',region=region,release_paragraphs=['B'],
        confirmation=dict(destination_id='A-vacated',paragraph_id='A',region_id='vacated-9',page=9,
            bounds=region['bounds'],insertion='confirmed-page-program-boundary',
            graphics_state='confirmed-boundary-state',boundary=boundary['boundary_id']))
    accepted=accept_owned_flow_report(SOURCE,propose_owned_flow(SOURCE,pages,paragraphs=GROUPS[:2],
        relationship='The complete innovator and early-adopter paragraphs; all subsequent paragraphs stay foreign.',
        page_breaks=[BREAK],vacated_continuations=[request]),provider_choices=choices(pages))
    write(output/'limited-acceptance.json',accepted)
    state=accepted['state']
    report=edit_shared_flow(SOURCE,state,output/'saved.pdf',output/'saved.json',
        replace_in_flow(state,FIND,SHORT,paragraph_id='A',style_id='page-8:style-1'))
    write(output/'bootstrap.json',dict(short=SHORT,report=report))


def pixel_equal(before,after):
    with pymupdf.open(before) as a,pymupdf.open(after) as b:
        assert [p.get_pixmap(dpi=144).samples for p in a]==[p.get_pixmap(dpi=144).samples for p in b]


def poppler(output,before,after,state,*,whole=False):
    for name,path in [('before',before),('after',after)]:
        subprocess.run(['pdftoppm','-scale-to','1600','-png',str(path),str(output/name)],check=True,capture_output=True)
    with pymupdf.open(before) as doc:
        for number in range(1,len(doc)+1):
            a=Image.open(output/f'before-{number:02}.png').convert('RGB')
            b=Image.open(output/f'after-{number:02}.png').convert('RGB')
            diff=ImageChops.difference(a,b)
            if not whole:
                for r in state['regions'].values():
                    if r['page']==number:
                        box=(pymupdf.Rect(r['bounds'])*pymupdf.Matrix(a.width/doc[number-1].rect.width,a.height/doc[number-1].rect.height)).irect
                        ImageDraw.Draw(diff).rectangle((box.x0,box.y0,box.x1-1,box.y1-1),fill=(0,0,0))
            assert diff.getbbox() is None


@proof_session
def run(output,font,resume=False):
    output.mkdir(parents=True,exist_ok=resume)
    assert source_sha(SOURCE)==SHA and source_sha(font)==FONT_SHA
    if not (resume and (output/'saved.json').exists()):bootstrap(output,font)
    saved=output/'saved.pdf';opened=open_shared_flow(saved,output/'saved.json')
    assert opened['status']=='restored';base=opened['state']
    generated=slot_id(base['continuation_destinations']['A-vacated'])
    assert base['slots'][generated]['occupancy'] is not None
    assert base['slots']['slot-1']['occupancy']['baseline']>142
    blocked,side=output/'blocked.pdf',output/'blocked.json'
    try:edit_shared_flow(saved,base,blocked,side,replace_in_flow(base,SHORT,LONG,paragraph_id='A',style_id='page-8:style-1'))
    except PdfError as exc:refusal=str(exc)
    else:raise AssertionError('limited edited state accepted excessive growth')
    assert not blocked.exists() and not side.exists()
    print('edited capacity REFUSE',refusal,flush=True)
    if resume and (output/'accepted.json').exists():accepted=json.loads((output/'accepted.json').read_text())
    else:
        lines=_Analysis(saved,9).lines
        ids=[l['id'] for l in lines if 208<l['baseline']<650]
        kw=dict(line_ids=ids,font_candidates=[font],include_embedded_fonts=True,font_cache=output/'fonts')
        observed=propose_page_flow(saved,9,**kw)
        page=propose_page_flow(saved,9,**kw,new_text={s['id']:LONG+SECOND[1] for s in observed['styles']})
        groups=[dict(id=chr(67+i),parts=[dict(page=9,paragraph_id=p['id'])]) for i,p in enumerate(page['paragraphs'])]
        proposal=propose_owned_flow_extension(saved,base,[page],paragraphs=groups,
            relationship=RELATIONSHIP,append_to_region='page-9');write(output/'proposal.json',proposal)
        accepted=accept_owned_flow_report(saved,proposal,provider_choices=choices([page]));write(output/'accepted.json',accepted)
    state=accepted['state'];assert open_shared_flow(saved,state)['status']=='restored'
    for pid in ('A','B'):assert state['paragraphs'][pid]==base['paragraphs'][pid]
    for sid,old in base['slots'].items():
        assert state['slots'][sid]['binding']['paragraph']==old['binding']['paragraph']
        assert state['slots'][sid].get('source_output')==old.get('source_output')
    for key in ('continuation_destinations','destination_bindings','generated_fonts'):assert state[key]==base[key]
    additions={sid:dict(paragraph=slot['paragraph_id'],source_output=slot['source_output'],
        selection=slot['binding']['paragraph']['selection']) for sid,slot in state['slots'].items() if sid not in base['slots']}
    assert all(a['selection']['source_sha256']==base['pdf_sha256']
               and a['source_output']['created_from']['pdf_sha256']==base['pdf_sha256'] for a in additions.values())
    initial=deepcopy(state)
    # Independent original-text reference for the existing audit only. It is
    # never passed to an editing/proposal/acceptance API.
    reference=deepcopy(initial)
    original_lines=_Analysis(SOURCE,8).lines
    reference['slots']['slot-0']['binding']['paragraph']['text']=''.join(l['text'] for l in original_lines if l['id'] in [f'p8-l{i}' for i in range(24,28)])
    del reference['slots'][generated]
    result=dict(verdict='NOT VALIDATED',source=dict(url=URL,sha256=SHA),initial_saved_revision=base['pdf_sha256'],
        initial_replacement=SHORT,larger_replacement=LONG,limited_refusal=refusal,acceptance=accepted['receipt'],
        preserved_existing_slots=list(base['slots']),generated_slot=generated,additional_current_owners=additions,stages=[])
    provider=ShapedFont(font);source=saved
    expected={p:s['logical']['text'] for p,s in state['paragraphs'].items()}
    stages=[('extension-noop',{}),('grow',{'A':(SHORT,LONG)}),('second',{'B':SECOND}),
            ('shorten',{'A':(LONG,FIND),'B':SECOND[::-1]}),('regrow',{'A':(FIND,LONG)}),('noop',{})]
    for i,(name,edits) in enumerate(stages):
        changes={}
        for pid,edit in edits.items():
            changes.update(replace_in_flow(state,*edit,paragraph_id=pid,style_id=f'page-{8 if pid=="A" else 9}:style-1'))
            expected[pid]=expected[pid].replace(*edit)
        pdf,model=output/f'{i}-{name}.pdf',output/f'{i}-{name}.json'
        report=edit_shared_flow(source,state,pdf,model,changes);write(output/f'{i}-report.json',report)
        opened=open_shared_flow(pdf,model);assert opened['status']=='restored';state=opened['state']
        for sid,slot in initial['slots'].items():
            if sid!=generated:assert immutable(slot['source_output'])==immutable(state['slots'][sid]['source_output'])
        assert state['slots'][generated]['creation_binding']==base['slots'][generated]['creation_binding']
        assert state['continuation_destinations']==base['continuation_destinations']
        assert {p:s['logical']['text'] for p,s in state['paragraphs'].items()}==expected
        checked=audit(source,pdf,reference,state,report,provider)
        if not edits:
            pixel_equal(source,pdf)
            checked['whole_document_mupdf_pixel_identical']=True
        if i==0:
            assert state['generated_fonts']==base['generated_fonts']
            checked['identity']=audit_original_noop(saved,pdf,initial,state)
            poppler(output,saved,pdf,state,whole=True)
            checked['whole_document_poppler_pixel_identical']=True
        if name=='grow':
            f=report['plan']['fragments'];c=next(s for s in state['slots'] if state['slots'][s]['paragraph_id']=='C')
            assert f[c]['lines'][0]['baseline']>initial['slots'][c]['occupancy']['baseline']
            assert max(b[3] for b in f['slot-1']['ink_bounds'])>base['regions']['page-9']['bounds'][3]
            assert len(f[generated]['lines'])==2
            with pymupdf.open(saved) as old,pymupdf.open(pdf) as new:
                a,b=chars(old[8]),chars(new[8])
                previous_c=initial['slots'][c]['binding']['paragraph']['selection']['glyph_ids']
                current_b=state['slots']['slot-1']['binding']['paragraph']['selection']['glyph_ids']
                used=[i for i in previous_c if any(pymupdf.Rect(a[i][3]).intersects(pymupdf.Rect(b[j][3])) for j in current_b)]
                assert used
                checked['newly_owned_C_glyph_bounds_used_by_B']=used
            witness=next(s for s in report['steps'] if s['slot_id']==generated)['report']['vacated_space']
            assert witness['owners'][0]['source_owner']=='slot-1'
            checked['release_transaction_witness']=witness
        if name=='shorten':assert state['slots'][generated]['occupancy'] is None
        else:assert state['slots'][generated]['occupancy'] is not None
        result['stages'].append(dict(name=name,reopen='restored',audit=checked,
            follows=state['follows'],occupancy={s:v['occupancy'] for s,v in state['slots'].items()}))
        write(output/'progress.json',result)
        print(name,'restored',[(p['paragraph'],p['page'],len(p['lines'])) for p in checked['placements']],flush=True)
        source=pdf
    poppler(output,SOURCE,source,state)
    provider.font.close();assert source_sha(SOURCE)==SHA
    result.update(verdict='REAL-WORLD POST-EDIT OWNERSHIP EXTENSION VALIDATED — PASS',
        outside_poppler_changed_pixels=0,original_sha_unchanged=True)
    write(output/'summary.json',result);print(result['verdict'],flush=True)


if __name__=='__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--font',type=Path,required=True);p.add_argument('--resume',action='store_true')
    args=p.parse_args();run(args.output,args.font,args.resume)
