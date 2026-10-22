"""B2 API: select → review inferred proposal → explicitly approve → save.

There is deliberately no coordinate/frame argument and no evaluation import.
Acceptance recomputes the proposal from the current PDF revision.
"""
import hashlib
import json
from pathlib import Path
import time
from .auto_detect import detect
from .auto_style import build,describe
from .auto_layout import layout
from .auto_writer import write
from .engine import Refused,sha


def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _hyphens_stay_at_line_ends(paragraph,lines):
    """A line-final hyphen may be soft; reflow must not move it inside a line."""
    hyphens=set(paragraph.hyphens)
    for line in lines:
        ink=[t for t in line['tokens'] if t['text'].strip()]
        if any(t.get('source_id') in hyphens for t in ink[:-1]):
            raise Refused('reflow would move a line-final hyphen inside a line (soft or hard hyphen is ambiguous)')


def _plan(source,page,selection,replacement,occurrence,substitute,stored=True):
    start=time.perf_counter();c,paragraphs,spec,at,evidence=detect(source,page,selection,occurrence,stored);observed=time.perf_counter()
    try:
        tokens,fonts,counts,choice=build(c,paragraphs[0],at,selection,replacement,substitute,justify=spec['align']=='justify')
        # Alignment is an approved condition: a stored record supplies it after
        # the multi-line evidence has been edited away.
        spec['values'].update(align=spec['align'])
        lines=layout(tokens,spec)
        _hyphens_stay_at_line_ends(paragraphs[0],lines)
        public=dict(schema='auto-paragraph-frame-1',source_sha256=sha(source),page=page,selection=selection,replacement=replacement,occurrence=occurrence,
          substitute=substitute,stored_conditions=stored,evidence=evidence,source_styles=describe(c,paragraphs),paragraphs=[p.text for p in paragraphs],expected=paragraphs[0].text[:at]+replacement+paragraphs[0].text[at+len(selection):],
          lines=[dict(text=l['text'],baseline=l['baseline']) for l in lines],source_lines=len(paragraphs[0].baselines),font_counts=counts,substitute_choice=choice,
          required_approval=['inferred paragraph and capacity','possible follower translation','source style and any explicit substitute'])
        public['proposal_sha256']=_digest(public)
        return c,paragraphs,spec,evidence,fonts,lines,public,dict(observation=observed-start,style_layout=time.perf_counter()-observed)
    except Exception:c.close();raise


def propose(source,page,selection,replacement,*,occurrence=0,substitute=None,stored_conditions=True):
    """``stored_conditions=False`` ignores conditions stored by earlier saves (inference only)."""
    planned=_plan(source,page,selection,replacement,occurrence,substitute,stored_conditions)
    planned[0].close();return planned[-2]


def approve(source,proposal,*,acknowledge_inference=False):
    if acknowledge_inference is not True:raise Refused('explicit inference/format approval required')
    again=propose(source,proposal['page'],proposal['selection'],proposal['replacement'],occurrence=proposal['occurrence'],substitute=proposal['substitute'],
                  stored_conditions=proposal.get('stored_conditions',True))
    if again!=proposal:raise Refused('stale or altered proposal')
    return dict(proposal=again,approved=True,approval_count=1)


def save(source,acceptance,output):
    if acceptance.get('approved') is not True:raise Refused('explicit approval missing')
    p=acceptance['proposal'];start=time.perf_counter();planned=_plan(source,p['page'],p['selection'],p['replacement'],p['occurrence'],p['substitute'],p.get('stored_conditions',True))
    c,paragraphs,spec,evidence,fonts,lines,again,timing=planned
    try:
        if p!=again:raise Refused('stale or altered accepted proposal')
        before=time.perf_counter();result=write(c,paragraphs,lines,fonts,spec,evidence,output,approval=dict(proposal_sha256=p['proposal_sha256']))
        result.update(proposal_sha256=p['proposal_sha256'],pdf_sha256=sha(output),font_counts=p['font_counts'],lines=p['lines'],timing=dict(timing,save_and_stage=time.perf_counter()-before,total=time.perf_counter()-start))
        return result
    finally:c.close()
