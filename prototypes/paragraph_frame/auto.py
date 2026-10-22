"""B2 API: select → review inferred proposal → explicitly approve → save.

There is deliberately no coordinate/frame argument and no evaluation import.
Acceptance recomputes the proposal from the current PDF revision.
"""
import hashlib
import json
from pathlib import Path
import time
from .auto_detect import detect
from .auto_style import build
from .auto_layout import layout
from .auto_writer import write
from .engine import Refused,sha


def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _plan(source,page,selection,replacement,occurrence,substitute):
    start=time.perf_counter();c,paragraphs,spec,at,evidence=detect(source,page,selection,occurrence);observed=time.perf_counter()
    try:
        tokens,fonts,counts=build(c,paragraphs[0],at,selection,replacement,substitute)
        lines=layout(tokens,spec)
        public=dict(schema='auto-paragraph-frame-1',source_sha256=sha(source),page=page,selection=selection,replacement=replacement,occurrence=occurrence,
          substitute=substitute,evidence=evidence,paragraphs=[p.text for p in paragraphs],expected=paragraphs[0].text[:at]+replacement+paragraphs[0].text[at+len(selection):],
          lines=[dict(text=l['text'],baseline=l['baseline']) for l in lines],source_lines=len(paragraphs[0].baselines),font_counts=counts,
          required_approval=['inferred paragraph and capacity','possible follower translation','source style and any explicit substitute'])
        public['proposal_sha256']=_digest(public)
        return c,paragraphs,spec,evidence,fonts,lines,public,dict(observation=observed-start,style_layout=time.perf_counter()-observed)
    except Exception:c.close();raise


def propose(source,page,selection,replacement,*,occurrence=0,substitute=None):
    planned=_plan(source,page,selection,replacement,occurrence,substitute)
    planned[0].close();return planned[-2]


def approve(source,proposal,*,acknowledge_inference=False):
    if acknowledge_inference is not True:raise Refused('explicit inference/format approval required')
    again=propose(source,proposal['page'],proposal['selection'],proposal['replacement'],occurrence=proposal['occurrence'],substitute=proposal['substitute'])
    if again!=proposal:raise Refused('stale or altered proposal')
    return dict(proposal=again,approved=True,approval_count=1)


def save(source,acceptance,output):
    if acceptance.get('approved') is not True:raise Refused('explicit approval missing')
    p=acceptance['proposal'];start=time.perf_counter();planned=_plan(source,p['page'],p['selection'],p['replacement'],p['occurrence'],p['substitute'])
    c,paragraphs,spec,evidence,fonts,lines,again,timing=planned
    try:
        if p!=again:raise Refused('stale or altered accepted proposal')
        before=time.perf_counter();result=write(c,paragraphs,lines,fonts,spec,evidence,output)
        result.update(proposal_sha256=p['proposal_sha256'],pdf_sha256=sha(output),font_counts=p['font_counts'],lines=p['lines'],timing=dict(timing,save_and_stage=time.perf_counter()-before,total=time.perf_counter()-start))
        return result
    finally:c.close()
