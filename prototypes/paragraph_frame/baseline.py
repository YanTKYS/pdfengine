"""A: unchanged main public edit-intent and page-flow entry points."""
import json
from pathlib import Path
import time
from pdfeditor.edit_intent import propose_edit_intent,accept_edit_intent,edit_edit_intent
from pdfeditor.selection import make_selection
from pdfeditor.page_proposal import propose_page_flow,accept_page_flow_report,replace_in_flow
from pdfeditor.shared_flow import edit_shared_flow


def run(case,spec,font,root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    source=spec['path'];start=time.perf_counter();record={}
    substitute=dict(path=str(Path(font).resolve()),sha256=__import__('hashlib').sha256(Path(font).read_bytes()).hexdigest(),font_index=0,relation='substituted')
    try:
        proposal=propose_edit_intent(source,make_selection(source,spec['page'],glyph_ids=case['glyph_ids']),case['new'],work_dir=root/'intent',substitute=substitute)
        (root/'intent-proposal.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2))
        record['edit_intent']={k:proposal.get(k) for k in ('status','reason','route','normalized')}
        if proposal['status']=='proposed':
            acceptance=accept_edit_intent(source,proposal,work_dir=root/'intent')
            result=edit_edit_intent(source,acceptance,root/'edited.pdf',root/'edited.json',work_dir=root/'intent')
            record['edit_intent']['saved']=True
    except Exception as exc:record['edit_intent']=dict(status='refused',reason=f'{type(exc).__name__}: {exc}')
    intent_end=time.perf_counter()
    # The region and paragraph starts are the same explicit geometric approval
    # as B. Use the existing page-flow acceptance overrides, never new owners.
    try:
        proposal=propose_page_flow(source,spec['page'],line_ids=spec['line_ids'],font_candidates=[font],include_embedded_fonts=True,
                                  font_cache=str(root/'font-cache'))
        proposal=propose_page_flow(source,spec['page'],line_ids=spec['line_ids'],font_candidates=[font],include_embedded_fonts=True,
                                  font_cache=str(root/'font-cache'),new_text={style['id']:case['new'] for style in proposal['styles']})
        choices={style['id']:{k:substitute[k] for k in ('sha256','font_index','relation')} for style in proposal['styles']}
        (root/'flow-proposal.json').write_text(json.dumps(proposal,ensure_ascii=False,indent=2))
        record['page_flow']={k:proposal.get(k) for k in ('status','refusals','unresolved','paragraphs','styles','width_candidates')}
        starts=[spec['line_ids'][0],spec['line_ids'][len(spec['paragraphs'][0]['source_line_baselines'])]]
        accepted=accept_page_flow_report(source,proposal,provider_choices=choices,overrides=dict(width=spec['right']-spec['left'],region_top=spec['frame'][1],region_bottom=spec['frame'][3],paragraph_starts=starts))
        state=accepted['state']
        target=next(p for p in state['paragraphs'].values() if case['old'] in p['logical']['text'])
        offset=target['logical']['text'].index(case['old'])
        style=next(s['style_id'] for s in target['logical']['style_spans'] if s['start']<=offset<s['end'])
        changes=replace_in_flow(state,case['old'],case['new'],style_id=style)
        edit_shared_flow(source,state,root/'flow-edited.pdf',root/'flow-edited.json',changes)
        record['page_flow']['saved']=True
    except Exception as exc:
        record.setdefault('page_flow',{})['acceptance_refusal']=f'{type(exc).__name__}: {exc}'
    record['timing']=dict(edit_intent=intent_end-start,page_flow=time.perf_counter()-intent_end,total=time.perf_counter()-start)
    record['outcome']='saved-needs-independent-audit' if any(v.get('saved') for v in record.values() if isinstance(v,dict)) else 'refused'
    return record
