"""Independent attributed paragraphs sharing explicitly confirmed page regions.

The final allocation precedes mutation. Existing source slots keep their
paragraph ownership and paint context. An allocation is not paint permission:
each scheduled fragment still goes through the guarded paragraph writer.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile

import pymupdf
from pypdf import PdfReader
from uniseg.graphemecluster import grapheme_cluster_boundaries

from .attributed import EditUnit, digest
from .backend import PdfError
from .destination_style import bind_destination_styles
from .document_flow import _reseal
from .editable import _publish, bind_document_edit, open_editable, plan_document_edit
from .elements import _close
from .transaction import Transaction
from .logical_element import paragraph_from_snapshot
from .model import Rect
from .paragraph import ParagraphShaper, plan_paragraph
from .pdf_save import embedded_font_sha256
from .proof_session import proof_session
from .replay import ensure_destination
from .rich_layout import layout_attributed
from .selection import source_sha
from .story_flow import LINE_KEYS, _boundaries, open_story
from . import story_styles as styles
from .alignment import DEFAULT, request as alignment_request, options as alignment_options, continuation
from . import continuation as destinations


SCHEMA = 'pdfengine-shared-flow-1'
POLICY_KEYS = {'min_line_height', 'first_line_indent', 'keep_together', 'break_before', 'break_after', 'empty'}
GENERATED_FONT_KEYS = {'provenance', 'subset_sha256', 'basefont', 'provider', 'slot_id', 'paragraph_id', 'created_pdf_sha256'}


def _verify_generated_fonts(source, state):
    """Ownership records: page-local fonts that pdfengine wrote for a slot of this flow.

    A record is written only by the save that embedded the subset and is
    carried forward only while the page resource still holds those bytes.
    Fonts without a record (source, foreign or legacy) are never owned.
    """
    records = state.get('generated_fonts')
    if records is None:
        return
    if not isinstance(records, dict):
        raise PdfError('generated font records must map pages to aliases')
    reader = PdfReader(source)
    if reader.is_encrypted:
        reader.decrypt('')
    for number, fonts in records.items():
        if number not in state['pages'] or not isinstance(fonts, dict) or not fonts:
            raise PdfError('generated font records name an unknown page')
        resources = reader.pages[int(number) - 1].get('/Resources')
        page_fonts = resources.get_object().get('/Font') if resources is not None else None
        page_fonts = page_fonts.get_object() if page_fonts is not None else {}
        for alias, record in fonts.items():
            slot = state['slots'].get(record.get('slot_id')) if isinstance(record, dict) else None
            if (slot is None or set(record) != GENERATED_FONT_KEYS or record['provenance'] != 'generated-by-pdfengine'
                    or slot['paragraph_id'] != record['paragraph_id']
                    or state['regions'][slot['region_id']]['page'] != int(number)
                    or alias not in page_fonts or embedded_font_sha256(page_fonts[alias]) != record['subset_sha256']
                    or page_fonts[alias].get_object().get('/BaseFont') != '/' + record['basefont']):
                raise PdfError('generated font record differs from the saved page resources')


def _generated_fonts(initial, state, result, output_sha256):
    """Carry verified records forward and record the subsets written by this save."""
    fonts = deepcopy(initial.get('generated_fonts', {}))
    for number, page in result.pages.items():
        current = fonts.setdefault(str(number), {})
        outcome = result.font_outcome.get(number, {})
        for plan in page.plans:
            for alias, record in plan.font_records.items():
                previous = current.get(alias)
                kept = (outcome.get(alias) == 'reused' and previous is not None
                        and previous['subset_sha256'] == record['subset_sha256'])
                current[alias] = dict(record, provenance='generated-by-pdfengine', slot_id=plan.owner,
                    paragraph_id=state['slots'][plan.owner]['paragraph_id'],
                    created_pdf_sha256=previous['created_pdf_sha256'] if kept else output_sha256)
        if not current:
            del fonts[str(number)]
    return fonts


def _contract(state):
    return digest(dict(flow=state['flow'], regions=state['regions'], pages=state['pages'],
        follows=state['follows'], paragraph_boundaries=state['paragraph_boundaries'],
        paragraph_policies=state['paragraph_policies'],
        paragraphs={i:dict(source_model_sha256=p['source_model_sha256'], style_registry=p['style_registry'],
                          reflow_font_policy=p['reflow_font_policy']) for i,p in state['paragraphs'].items()},
        slots={i:{k:s[k] for k in ('paragraph_id','region_id','source_snapshot_sha256')}
            for i,s in state['slots'].items() if s.get('creation_provenance')!=destinations.PROVENANCE},
        **({'continuation_destinations':state['continuation_destinations']} if state.get('continuation_destinations') else {})))


def _slots(state, paragraph):
    order=state['flow']['regions']
    return sorted((i for i,s in state['slots'].items() if s['paragraph_id']==paragraph),
                  key=lambda i:order.index(state['slots'][i]['region_id']))


def _style_state(state, paragraph):
    p=state['paragraphs'][paragraph]
    return dict(p, pdf_sha256=state['pdf_sha256'],
                fragments={i:state['slots'][i] for i in _slots(state,paragraph)})


def _breaks(state):
    result=[]
    for pid in state['flow']['paragraphs']:
        active=[i for i in _slots(state,pid) if state['slots'][i]['range'][0]!=state['slots'][i]['range'][1]]
        for before,after in zip(active,active[1:]):
            a,b=state['slots'][before],state['slots'][after]
            result.append(dict(paragraph_id=pid,offset=a['range'][1],before=before,after=after,
                kind='page_break' if state['regions'][a['region_id']]['page']!=state['regions'][b['region_id']]['page'] else 'container_break',
                provenance=state['allocation_provenance'],logical_break_inserted=False))
    return result


def _policies(state):
    order=state['flow']['paragraphs'];policies=state['paragraph_policies']
    if set(policies)!=set(order):raise PdfError('each paragraph needs its own confirmed layout policy')
    for pid,p in policies.items():
        if (set(p)!=POLICY_KEYS or type(p['keep_together']) is not bool
                or p['break_before'] not in ('auto','next-region') or p['break_after'] not in ('auto','next-region')
                or not all(type(p[k]) in (int,float) and math.isfinite(p[k]) for k in ('min_line_height','first_line_indent'))
                or p['min_line_height']<=0 or p['first_line_indent']<0):
            raise PdfError('unsupported paragraph spacing or break policy')
        e=p['empty']
        if (set(e)!={'kind','ascent','descent'} or e['kind']!='reserve-line'
                or not all(type(e[k]) in (int,float) and math.isfinite(e[k]) and e[k]>=0 for k in ('ascent','descent'))
                or e['ascent']+e['descent']<=0):
            raise PdfError('empty paragraph needs explicitly confirmed reserve-line metrics')
    if policies[order[0]]['break_before']!='auto':raise PdfError('the first paragraph cannot skip the first confirmed region')
    expected=[(a,b) for a,b in zip(order,order[1:])]
    if [(e['before'],e['after']) for e in state['follows']]!=expected:
        raise PdfError('shared flow needs an explicit consecutive paragraph relation')
    for edge in state['follows']:
        if (set(edge)!={'before','after','minimum_baseline_gap','region_start','provenance'}
                or edge['region_start']!='reset-to-region-baseline' or edge['provenance']!='explicitly_confirmed'
                or type(edge['minimum_baseline_gap']) not in (int,float)
                or not math.isfinite(edge['minimum_baseline_gap']) or edge['minimum_baseline_gap']<=0):
            raise PdfError('paragraph gap and its treatment at a region break must be confirmed')
    if state['paragraph_boundaries']!=[dict(before=a,after=b,kind='paragraph_boundary',provenance='explicitly_confirmed') for a,b in expected]:
        raise PdfError('paragraph boundaries are identity relations, not Unicode or physical breaks')


def _validate(source,value):
    state=deepcopy(value);checksum=state.pop('model_sha256',None)
    if state.get('schema')!=SCHEMA or checksum!=digest(state):raise PdfError('shared flow model version or checksum differs')
    state['model_sha256']=checksum
    if source_sha(source)!=state['pdf_sha256']:raise PdfError('shared flow PDF revision changed')
    flow=state['flow'];pids=flow['paragraphs'];rids=flow['regions']
    if (not isinstance(flow['id'],str) or not flow['id'] or not pids or not rids or len(pids)!=len(set(pids)) or len(rids)!=len(set(rids))
            or set(pids)!=set(state['paragraphs']) or set(rids)!=set(state['regions'])
            or flow['policy']!='ordered-paragraphs-share-fixed-regions' or flow['provenance']!='explicitly_confirmed'
            or flow['overflow']!='reject' or state['contract_sha256']!=_contract(state)):
        raise PdfError('shared capacity, identities or confirmed contract changed')
    _policies(state)
    used=set();insertion_slots=set();pairs=set()
    with pymupdf.open(source) as doc:
        if len(doc)!=state['page_count'] or set(state['pages'])!={str(n+1) for n in range(len(doc))}:
            raise PdfError('shared flow cannot add or remove pages')
        for number,policy in state['pages'].items():
            if policy['unselected_elements']!='fixed-to-page' or policy['header_inference']!='unknown':
                raise PdfError('unselected page semantics must remain fixed')
            for protected in policy['protected_regions']:
                box=Rect(*protected['bounds'])
                if (protected['provenance']!='explicitly_confirmed' or protected['role'] not in ('header','footer','fixed')
                        or not all(math.isfinite(v) for v in box.tuple()) or box.width<=0 or box.height<=0
                        or not Rect(*doc[int(number)-1].rect).contains(box)):
                    raise PdfError('invalid confirmed protected page region')
        for index,rid in enumerate(rids):
            r=state['regions'][rid];box=Rect(*r['bounds'])
            if (set(r)!={'page','bounds','x','width','first_baseline','provenance'} or type(r['page']) is not int
                    or not 1<=r['page']<=len(doc) or r['provenance']!='explicitly_confirmed'
                    or not all(type(v) in (int,float) and math.isfinite(v) for v in (*box.tuple(),r['x'],r['width'],r['first_baseline']))
                    or box.width<=0 or box.height<=0 or r['width']<=0 or not box.y0<r['first_baseline']<box.y1
                    or not box.x0<=r['x']<r['x']+r['width']<=box.x1 or not Rect(*doc[r['page']-1].rect).contains(box)):
                raise PdfError('shared region needs explicit finite usable width, baseline and bounds')
            others=[v['bounds'] for v in state['regions'].values() if v is not r and v['page']==r['page']]
            protected=state['pages'][str(r['page'])]['protected_regions']
            if any(box.intersects(Rect(*b)) for b in others+[v['bounds'] for v in protected]):
                raise PdfError('shared regions must be disjoint and outside protected page content')
        for sid,slot in state['slots'].items():
            pid,rid=slot['paragraph_id'],slot['region_id'];b=slot['binding'];p=b['paragraph']
            if pid not in pids or rid not in rids or (pid,rid) in pairs:
                raise PdfError('existing destination slots need unique paragraph and region ownership')
            pairs.add((pid,rid));r=state['regions'][rid];box=Rect(*r['bounds'])
            if (b['logical_element']['id']!=pid or b['logical_element']['contained_by']!=rid
                    or p['selection']['page']!=r['page'] or b['anchors'] is not None
                    or b['boundaries']!=_boundaries(p['text'])
                    or any(v['behavior']!='fixed-to-page' or v['relation']=='decorates' for v in b['relations'])):
                raise PdfError('slot identity, page or fixed paint policy differs')
            restored=open_editable(source,b)
            if restored['status']!='restored':raise PdfError(restored['reason'])
            layout=b['layout'];observed=b['element']['text_element']['observed_bounds']
            if (layout['width'] is None or not box.contains(Rect(layout['x'],layout['baseline'],layout['x']+layout['width'],layout['max_bottom']),.002)
                    or observed and not box.contains(Rect(**observed),.002)):
                raise PdfError('current fragment exceeds the confirmed shared region')
            for gid in p['selection']['glyph_ids']:
                key=(r['page'],gid)
                if key in used:raise PdfError('paragraphs cannot share source glyph occurrences')
                used.add(key)
            if not p['selection']['glyph_ids']:
                key=(r['page'],p['insertion_binding']['event']['byte_range'][0])
                if key in insertion_slots:raise PdfError('paragraphs cannot share source insertion slots')
                insertion_slots.add(key)
            if slot.get('creation_provenance')==destinations.PROVENANCE:
                d=state.get('continuation_destinations',{}).get(slot.get('destination_id'))
                if d is None or sid!=destinations.slot_id(d):raise PdfError('generated slot has no confirmed destination owner')
        for pid,p in state['paragraphs'].items():
            logical=p['logical'];cursor=0
            alignment=logical.get('alignment',DEFAULT);alignment_request(alignment)
            boundaries={0,len(logical['text']),*grapheme_cluster_boundaries(logical['text'])}
            if (logical['id']!=pid or logical['kind']!='paragraph' or logical['boundaries']!=_boundaries(logical['text'])
                    or logical['decoration_ranges']!=[] or logical['paragraph_boundaries']!=[]):
                raise PdfError('each paragraph retains independent Unicode, hard breaks and style semantics')
            styles.validate_registry(p)
            if not _slots(state,pid):raise PdfError('paragraph has no verified destination slot')
            for sid in _slots(state,pid):
                slot=state['slots'][sid];a,z=slot['range'];end=slot['render_end']
                if (not all(type(v) is int for v in (a,z,end)) or a!=cursor or not a<=end<=z<=len(logical['text'])
                        or not {a,z,end}<=boundaries
                        or slot['binding']['paragraph']['text']!=logical['text'][a:end]
                        or any(c not in ' \r\n' for c in logical['text'][end:z])):
                    raise PdfError('fragment ranges must partition only their owning paragraph')
                styles.validate_fragment(_style_state(state,pid),sid);cursor=z
                if state['allocation_provenance']=='generated-from-confirmed-shared-flow':
                    b=slot['binding']
                    if (b['logical_element']['alignment']!=alignment
                            or alignment!=DEFAULT and b['physical_layout'].get('paragraph_continues',False)!=continuation(logical['text'],end,z)):
                        raise PdfError('shared fragment alignment or continuation differs from its paragraph')
            if cursor!=len(logical['text']):raise PdfError('unbound paragraph Unicode remains')
    destinations.validate_destinations(source,state)
    _verify_generated_fonts(source,state)
    if state['physical_breaks']!=_breaks(state):raise PdfError('physical breaks differ from paragraph fragment allocation')
    if state['allocation_provenance']=='generated-from-confirmed-shared-flow':_verify_placement(state)
    elif state['allocation_provenance']!='observed-source-uncomposed':raise PdfError('unknown allocation provenance')
    return state


@proof_session
def confirm_shared_flow(source, stories, *, flow_id, paragraph_order, regions, region_order,
                        slot_regions, paragraph_policies, follows, protected_regions, continuation_destinations=None):
    """Import reviewed schema-2 stories and independently confirm shared capacity.

    slot_regions maps each paragraph's existing fragment ids to region ids.
    Spacing is a caller-supplied minimum last-to-first baseline gap, reset at
    region starts. An empty logical paragraph explicitly reserves one line.
    """
    if set(stories)!=set(paragraph_order) or set(slot_regions)!=set(stories):raise PdfError('explicit paragraph membership is required')
    paragraphs={};slots={}
    for pid in paragraph_order:
        opened=open_story(source,stories[pid])
        if opened['status']!='restored':raise PdfError(opened['reason'])
        story=opened['state']
        if story['schema']!='pdfengine-story-flow-2' or story['logical']['id']!=pid:
            raise PdfError('shared flow imports independent confirmed attributed paragraph identities')
        if set(slot_regions[pid])!=set(story['fragments']):raise PdfError('every source fragment needs a confirmed shared region')
        paragraphs[pid]={k:deepcopy(story[k]) for k in ('logical','style_registry','reflow_font_policy')}
        paragraphs[pid]['source_model_sha256']=story['model_sha256']
        for local,fragment in story['fragments'].items():
            sid='slot-'+str(len(slots));rid=slot_regions[pid][local];slot=deepcopy(fragment)
            slot.update(paragraph_id=pid,region_id=rid,source_snapshot_sha256=fragment['binding']['paragraph']['snapshot_sha256'],
                        occupancy=None)
            b=slot['binding'];b['logical_element']['contained_by']=rid;b['boundaries']=_boundaries(b['paragraph']['text'])
            slot['binding']=_reseal(b);slots[sid]=slot
    with pymupdf.open(source) as doc:count=len(doc)
    state=dict(schema=SCHEMA,pdf_sha256=source_sha(source),page_count=count,
        flow=dict(id=flow_id,paragraphs=list(paragraph_order),regions=list(region_order),
                  policy='ordered-paragraphs-share-fixed-regions',overflow='reject',provenance='explicitly_confirmed'),
        paragraphs=paragraphs,regions={i:dict(r,provenance='explicitly_confirmed') for i,r in regions.items()},
        slots=slots,paragraph_policies=deepcopy(paragraph_policies),
        follows=[dict(e,provenance='explicitly_confirmed') for e in follows],
        paragraph_boundaries=[dict(before=a,after=b,kind='paragraph_boundary',provenance='explicitly_confirmed')
                              for a,b in zip(paragraph_order,paragraph_order[1:])],
        pages={str(i+1):dict(unselected_elements='fixed-to-page',header_inference='unknown',
                    protected_regions=[dict(r,provenance='explicitly_confirmed') for r in protected_regions.get(str(i+1),[])]) for i in range(count)},
        allocation_provenance='observed-source-uncomposed',previous_model_sha256=None)
    if continuation_destinations:
        from .content_stream import ContentPage
        state['continuation_destinations']=deepcopy(continuation_destinations);state['destination_bindings']={}
        for ident,d in continuation_destinations.items():
            if d['source_pdf_sha256']!=state['pdf_sha256']:raise PdfError('continuation confirmation belongs to another PDF')
            content=ContentPage(source,d['page'])
            try:state['destination_bindings'][ident]=destinations.witness(content,d,False)
            finally:content.close()
            if state['destination_bindings'][ident]['program_sha256']!=d['source_program_sha256']:
                raise PdfError('confirmed continuation source program differs')
    state['contract_sha256']=_contract(state);state['physical_breaks']=_breaks(state)
    return _validate(source,_reseal(state))


@proof_session
def open_shared_flow(source,model):
    try:
        value=model if isinstance(model,dict) else json.loads(Path(model).read_text(encoding='utf-8'))
        return dict(status='restored',state=_validate(source,value))
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError,PdfError) as exc:
        return dict(status='needs_confirmation',reason=str(exc),semantics='unknown')


def _restore(source,model):
    result=open_shared_flow(source,model)
    if result['status']!='restored':raise PdfError('shared flow requires confirmation: '+result['reason'])
    return result['state']


def _layout(source,state,pid,binding,text,ids,region,indent):
    p=state['paragraphs'][pid];paragraph=paragraph_from_snapshot(source,binding['paragraph']);shaper=None
    try:
        paragraph=bind_destination_styles(paragraph,styles.render_styles(p),styles.render_confirmations(p))
        from .style_confirmation import confirm_paragraph
        paragraph=confirm_paragraph(paragraph,styles.render_confirmations(p))
        alignment=p['logical'].get('alignment',DEFAULT)
        shaper=ParagraphShaper(paragraph,[EditUnit(ch,'logical:'+sid,None,'logical:'+sid) for ch,sid in zip(text,ids)],
                              styles.providers(p),alignment=alignment['value'])
        size=styles.properties(p['style_registry'][p['logical']['typing_style_id']])['font_size']
        return layout_attributed(text,shape=shaper.shape,x=region['x'],baseline=0,width=region['width'],
            min_line_height=state['paragraph_policies'][pid]['min_line_height'],max_bottom=None,
            first_line_indent=indent,empty_ascent=size*.8,empty_descent=size*.2,**alignment_options(alignment))
    finally:
        if shaper is not None:shaper.close()
        paragraph.close()


def _layout_options(state,pid,rid,baseline,indent):
    r=state['regions'][rid]
    return dict(x=r['x'],width=r['width'],baseline=baseline,max_bottom=r['bounds'][3],
                first_line_indent=indent,min_line_height=state['paragraph_policies'][pid]['min_line_height'])


def _schedule(state,fragments):
    # Exact planned glyph ink versus the original selected glyph envelope.
    # Edges only choose order. All intermediate writer guards still apply.
    needs={i:set() for i in fragments}
    for sid,wanted in fragments.items():
        page=state['regions'][state['slots'][sid]['region_id']]['page']
        for other,slot in state['slots'].items():
            element=slot['binding'].get('element')
            bounds=element['text_element']['observed_bounds'] if element else None
            if other==sid or bounds is None or state['regions'][slot['region_id']]['page']!=page:continue
            if any(Rect(*ink).intersects(Rect(**bounds)) for ink in wanted['ink_bounds']):needs[sid].add(other)
    order=[];remaining=list(fragments)
    while remaining:
        ready=next((i for i in remaining if not needs[i]-set(order)),None)
        if ready is None:raise PdfError('source occupancy dependencies are cyclic; no guarded sequential route is certified')
        order.append(ready);remaining.remove(ready)
    return order,{i:sorted(v) for i,v in needs.items()}


def _plan(source,state,changes):
    if not isinstance(changes,dict) or set(changes)-set(state['paragraphs']):raise PdfError('changes must name independent paragraph identities')
    projected={}
    for pid,p in state['paragraphs'].items():
        request=changes.get(pid,dict(edits=[]))
        if not isinstance(request,dict) or set(request)-{'edits','typing_style_id'} or 'edits' not in request:
            raise PdfError('each paragraph change needs local Unicode edits and optional typing style')
        text,spans,typing=styles.project(p,request['edits'],request.get('typing_style_id'))
        projected[pid]=dict(text=text,style_spans=spans,typing_style_id=typing,boundaries=_boundaries(text))
    working=deepcopy(state)
    for pid,value in projected.items():working['paragraphs'][pid]['logical'].update(value)
    fragments={};new_slots={};rids=state['flow']['regions'];ri=0;previous=None
    incoming={e['after']:e for e in state['follows']}
    for pid in state['flow']['paragraphs']:
        p=working['paragraphs'][pid];logical=p['logical'];text=logical['text'];cursor=0
        ids=styles.style_ids(text,logical['style_spans'],p['style_registry']);policy=state['paragraph_policies'][pid]
        owned=_slots(state,pid);by_region={state['slots'][s]['region_id']:s for s in owned}
        if previous and (policy['break_before']=='next-region' or state['paragraph_policies'][previous['paragraph_id']]['break_after']=='next-region'):
            ri+=1;previous=None
        while True:
            if ri>=len(rids):raise PdfError('paragraphs exceed all explicitly confirmed shared regions')
            rid=rids[ri];region=state['regions'][rid];sid=by_region.get(rid)
            baseline=region['first_baseline'] if previous is None else previous['last_baseline']+incoming[pid]['minimum_baseline_gap']
            if baseline>=region['bounds'][3]-.002:ri+=1;previous=None;continue
            indent=policy['first_line_indent'] if cursor==0 else 0
            if sid is None:
                d=next((d for d in state.get('continuation_destinations',{}).values()
                    if d['paragraph_id']==pid and d['region_id']==rid),None)
                if d is not None:
                    sid=destinations.slot_id(d)
                    new_slots[sid]=dict(paragraph_id=pid,region_id=rid,destination_id=d['destination_id'],page=d['page'],
                        creation_provenance=destinations.PROVENANCE,source_snapshot_sha256=None,
                        binding=dict(paragraph=destinations.snapshot(source,d,state['destination_bindings'][d['destination_id']],
                            region,logical['typing_style_id'])))
                    working['slots'][sid]=new_slots[sid]
            binding=working['slots'][sid or owned[0]]['binding']
            remaining=text[cursor:]
            layout=_layout(source,working,pid,binding,remaining,ids[cursor:],region,indent)
            first_ascent=layout.lines[0].ascent if layout.lines else policy['empty']['ascent']
            if previous:baseline=previous['last_baseline']+max(incoming[pid]['minimum_baseline_gap'],previous['descent']+first_ascent)
            if baseline-first_ascent<region['bounds'][1]-.002:raise PdfError('first line exceeds the explicitly confirmed region top')
            fitting=[line for line in layout.lines if baseline+line.baseline+line.descent<=region['bounds'][3]+.002]
            empty_fits=baseline+policy['empty']['descent']<=region['bounds'][3]+.002
            if ((remaining and (not fitting or policy['keep_together'] and len(fitting)!=len(layout.lines)))
                    or not remaining and not empty_fits):
                ri+=1;previous=None;continue
            if sid is None:raise PdfError('final allocation needs a missing paragraph destination slot; new continuation is not authorized')
            if len(fitting)==len(layout.lines):end=cut=len(remaining)
            else:
                end=fitting[-1].end;cut=layout.lines[len(fitting)].start
                if end==0:raise PdfError('blank-only continuation has no supported physical glyph binding')
                if any(c not in ' \r\n' for c in remaining[end:cut]):raise PdfError('shared flow would drop non-whitespace Unicode')
            visible=remaining[:end];spans=styles.canonical_spans(ids[cursor:cursor+end])
            lines=[dict(**{k:getattr(line,k) for k in LINE_KEYS if k!='baseline'},baseline=baseline+line.baseline) for line in fitting]
            options=_layout_options(state,pid,rid,baseline,indent)
            measured=plan_paragraph(source,binding['paragraph'],styles.replacement(binding,visible,spans),
                fonts=styles.providers(p),render_styles=styles.render_styles(p),paragraph_style=styles.render_confirmations(p),
                paragraph_layout=alignment_request(p['logical'].get('alignment',DEFAULT)),
                _paragraph_continues=continuation(remaining,end,cut),**options)
            if not _close(measured['lines'],lines):raise PdfError('local writer differs from final shared paragraph layout')
            ink=[list(Rect(g.ink.x0,g.ink.y0+baseline,g.ink.x1,g.ink.y1+baseline).tuple())
                 for line in fitting for g in line.glyphs if g.ink is not None]
            occupancy=dict(paragraph_id=pid,region_id=rid,baseline=baseline,
                last_baseline=lines[-1]['baseline'] if lines else baseline,
                ascent=lines[0]['ascent'] if lines else policy['empty']['ascent'],
                descent=lines[-1]['descent'] if lines else policy['empty']['descent'],empty=not text)
            fragments[sid]=dict(range=[cursor,cursor+cut],render_end=cursor+end,text=visible,style_spans=spans,
                lines=lines,layout=options,ink_bounds=ink,occupancy=occupancy)
            cursor+=cut
            if cursor==len(text):previous=occupancy;break
            ri+=1;previous=None
        # Dormant slots preserve verified source insertion contexts but consume
        # no shared capacity. They are distinct from an empty logical paragraph.
        pos=0
        for sid in owned:
            if sid in fragments:pos=fragments[sid]['range'][1];continue
            rid=state['slots'][sid]['region_id'];r=state['regions'][rid]
            fragments[sid]=dict(range=[pos,pos],render_end=pos,text='',style_spans=[],lines=[],ink_bounds=[],occupancy=None,
                layout=_layout_options(state,pid,rid,r['first_baseline'],0))
    new_slots={sid:slot for sid,slot in new_slots.items() if sid in fragments}
    scheduling=deepcopy(state);scheduling['slots'].update(new_slots)
    schedule,dependencies=_schedule(scheduling,fragments)
    result=dict(schema='pdfengine-shared-flow-plan-1',source_sha256=state['pdf_sha256'],model_sha256=state['model_sha256'],
        contract_sha256=state['contract_sha256'],changes_sha256=digest(changes),paragraphs=projected,fragments=fragments,
        new_slots=new_slots,
        schedule=schedule,source_occupancy_dependencies=dependencies,
        collision_status='not_certified; every intermediate writer must pass its existing guards',
        paint_authority='owned source slots and explicitly confirmed continuation destinations; other content fixed')
    result['plan_sha256']=digest(result)
    return result


def _verify_placement(state):
    previous=None;rids=state['flow']['regions'];incoming={e['after']:e for e in state['follows']}
    for pid in state['flow']['paragraphs']:
        active=[state['slots'][sid] for sid in _slots(state,pid) if state['slots'][sid]['occupancy'] is not None]
        p=state['paragraphs'][pid];policy=state['paragraph_policies'][pid]
        if not active or policy['keep_together'] and len(active)>1:raise PdfError('paragraph occupancy violates its break policy')
        for index,slot in enumerate(active):
            o=slot['occupancy'];r=state['regions'][slot['region_id']];b=slot['binding'];lines=b['physical_layout']['lines']
            if (o['paragraph_id']!=pid or o['region_id']!=slot['region_id'] or o['empty']!=(not p['logical']['text'])
                    or not _close(o['baseline'],b['layout']['baseline'])
                    or not _close(o['last_baseline'],lines[-1]['baseline'] if lines else o['baseline'])
                    or not _close(o['ascent'],lines[0]['ascent'] if lines else policy['empty']['ascent'])
                    or not _close(o['descent'],lines[-1]['descent'] if lines else policy['empty']['descent'])
                    or o['baseline']-o['ascent']<r['bounds'][1]-.002 or o['last_baseline']+o['descent']>r['bounds'][3]+.002):
                raise PdfError('paragraph occupancy differs from the verified physical line metrics')
            expected=r['first_baseline']
            if previous:
                prev=rids.index(previous['region_id']);current=rids.index(o['region_id'])
                if current<prev:raise PdfError('paragraph flow order cannot go backwards')
                if current==prev:
                    if (index or policy['break_before']=='next-region'
                            or state['paragraph_policies'][previous['paragraph_id']]['break_after']=='next-region'):
                        raise PdfError('physical region violates the paragraph break policy')
                    expected=previous['last_baseline']+max(incoming[pid]['minimum_baseline_gap'],previous['descent']+o['ascent'])
            if not _close(expected,o['baseline']):raise PdfError('paragraph spacing differs from confirmed shared flow policy')
            previous=o
        for sid in _slots(state,pid):
            s=state['slots'][sid]
            if s['occupancy'] is None and s['binding']['paragraph']['text']:raise PdfError('dormant destination contains text')
            rid=s['region_id'];o=s['occupancy']
            expected=_layout_options(state,pid,rid,o['baseline'] if o is not None else state['regions'][rid]['first_baseline'],
                policy['first_line_indent'] if o is not None and s['range'][0]==0 else 0)
            if not _close(s['binding']['layout'],expected):raise PdfError('fragment layout differs from its paragraph and shared region policy')


@proof_session
def plan_shared_flow(source,model,changes):return _plan(source,_restore(source,model),changes)


@proof_session
def edit_shared_flow(source,model,output,model_output,changes):
    """Re-lay out every destination slot of the shared flow in one transaction."""
    initial=_restore(source,model);plan=_plan(source,initial,changes);state=deepcopy(initial)
    output=ensure_destination(output,source);model_output=ensure_destination(model_output,source)
    if output==model_output:raise PdfError('shared flow PDF and sidecar require distinct destinations')
    for pid,value in plan['paragraphs'].items():state['paragraphs'][pid]['logical'].update(value)
    state['slots'].update(deepcopy(plan['new_slots']))
    output.parent.mkdir(parents=True,exist_ok=True);steps=[]
    with tempfile.TemporaryDirectory(prefix='.shared-flow-',dir=output.parent) as directory:
        root=Path(directory);target=root/'shared.pdf'
        with Transaction(source) as transaction:
            plans={};owned=initial.get('generated_fonts',{})
            for sid in plan['schedule']:
                slot=state['slots'][sid];pid=slot['paragraph_id'];p=state['paragraphs'][pid];wanted=plan['fragments'][sid]
                number=state['regions'][slot['region_id']]['page'];fresh=number not in transaction.pages
                page=transaction.page(number)
                # Verified records from this flow's previous saves are the only
                # ownership evidence; every other font on the page stays untouched.
                if fresh:page.own_fonts(owned.get(str(number),{}))
                options=dict(
                    fonts=styles.providers(p),render_styles=styles.render_styles(p),
                    paragraph_style=styles.render_confirmations(p),
                    paragraph_layout=alignment_request(p['logical'].get('alignment',DEFAULT)),
                    _paragraph_continues=continuation(p['logical']['text'],wanted['render_end'],wanted['range'][1]),
                    _allow_empty_style_witnesses=bool(state.get('continuation_destinations')),
                    empty_style_id='logical:'+p['logical']['typing_style_id'],owner=sid,**wanted['layout'])
                edits=styles.replacement(slot['binding'],wanted['text'],wanted['style_spans'])
                if sid in plan['new_slots']:
                    from .editable import plan_editable
                    plans[sid]=plan_editable(page,slot['binding']['paragraph'],edits,**options)
                    plans[sid].document_context=dict(fonts=options['fonts'],
                        options={k:v for k,v in options.items() if k!='fonts'},previous_state=None)
                else:plans[sid]=plan_document_edit(page,slot['binding'],edits,**options)
            result=transaction.commit(target)
            try:
                for sid in plan['schedule']:
                    slot=state['slots'][sid];pid=slot['paragraph_id'];wanted=plan['fragments'][sid]
                    page=state['regions'][slot['region_id']]['page']
                    edited,report=bind_document_edit(result.identity(page),plans[sid],result)
                    if sid in plan['new_slots']:
                        from .elements import inspect_element
                        edited['logical_element'].update(id=pid,contained_by=slot['region_id'])
                        edited['element']=inspect_element(target,edited['paragraph']['selection'],paragraph_snapshot=edited['paragraph'])
                        edited['relations']=[]
                        d=state['continuation_destinations'][slot['destination_id']]
                        slot['creation_binding']=dict(source_pdf_sha256=initial['pdf_sha256'],
                            destination_contract_sha256=digest(d),mutation=plans[sid].first_mutation.record())
                    if edited['paragraph']['text']!=wanted['text'] or not _close(
                            [{k:line[k] for k in LINE_KEYS} for line in edited['physical_layout']['lines']],wanted['lines']):
                        raise PdfError('executed paragraph fragment differs from final allocation')
                    styles.bind_fragment(_style_state(state,pid),sid,edited,wanted,generated=True)
                    edited['boundaries']=_boundaries(wanted['text'])
                    edited['layout_provenance']={k:('explicitly_confirmed' if k=='width' else
                        'generated-from-confirmed-shared-flow') for k in edited['layout']}
                    slot['binding']=_reseal(edited)
                    for key in ('range','render_end','occupancy'):slot[key]=deepcopy(wanted[key])
                    slot['style_binding']['pdf_sha256']=source_sha(target)
                    steps.append(dict(slot_id=sid,paragraph_id=pid,region_id=slot['region_id'],report=report))
                mutation_map=result.mutation_map()
                from .content_stream import ContentPage
                for ident,d in state.get('continuation_destinations',{}).items():
                    content=ContentPage(target,d['page'])
                    try:
                        current=destinations.witness(content,d,destinations.slot_id(d) in state['slots'])
                        if destinations.slot_id(d) in initial['slots']:
                            old=initial['destination_bindings'][ident]
                            identity=result.identity(d['page'])
                            if identity.program.map_offset(old['end'])!=current['end']:
                                raise PdfError('generated continuation block lost its insertion identity')
                        state['destination_bindings'][ident]=current
                    finally:content.close()
                state['generated_fonts']=_generated_fonts(initial,state,result,source_sha(target))
                font_outcome={str(n):dict(sorted(v.items())) for n,v in sorted(result.font_outcome.items()) if v}
            finally:result.close()
        if source_sha(source)!=initial['pdf_sha256']:raise PdfError('source changed during shared flow mutation')
        state.update(pdf_sha256=source_sha(target),allocation_provenance='generated-from-confirmed-shared-flow',
                     previous_model_sha256=initial['model_sha256'])
        state['physical_breaks']=_breaks(state);state=_validate(target,_reseal(state))
        saved=root/'shared.json';saved.write_bytes((json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
        _publish(root,[(target,output),(saved,model_output)])
    return dict(backend='shared-attributed-paragraph-flow',plan=plan,steps=steps,mutation_map=mutation_map,
        generated_font_outcome=font_outcome,pdf_sha256=state['pdf_sha256'],
        model_sha256=state['model_sha256'],paragraph_identities_and_allocation_verified=True,saves=1,
        publication='one transaction verified every slot; rollback on exception')
