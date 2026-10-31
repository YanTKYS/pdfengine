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
from .auto_local import LocalUnavailable,local_layout
from . import auto_restore as restore
from .auto_flow import place,region_of
from .auto_writer import decoration_candidates,follower_delta,write
from . import chain as following
from .engine import Refused,sha


def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _hyphens_stay_at_line_ends(paragraph,lines):
    """A line-final hyphen may be soft; reflow must not move it inside a line."""
    hyphens=set(paragraph.hyphens)
    for line in lines:
        ink=[t for t in line['tokens'] if t['text'].strip()]
        if any(t.get('source_id') in hyphens for t in ink[:-1]):
            raise Refused('reflow would move a line-final hyphen inside a line (soft or hard hyphen is ambiguous)')


def _placed_objects_stay(lines):
    """An inline placed object (kept as drawn, with its own clip and paths) must keep its place in the new layout."""
    for line in lines:
        for t in line['tokens']:
            if t.get('fixed') and (abs(t['x']-t['origin'][0])>.01 or abs(t['y']-t['origin'][1])>.01):
                raise Refused('an inline placed object would move in the new layout')


def _leaves_frame(lines,paragraphs,spec):
    """Whether the last baseline of the paragraph, or of a follower moved by its growth, is below the frame."""
    delta=lines[-1]['baseline']-paragraphs[0].baselines[-1]
    deepest=max([lines[-1]['baseline']]+[p.baselines[-1]+delta for p in paragraphs[1:] if delta>0])
    return bool(spec.get('frame')) and deepest>spec['frame'][3]+.01


def _flow_moves(paragraphs,spec,moves):
    """Following paragraphs that must be repainted, as {glyph id: (dx, dy)} maps."""
    active=[]
    for rows in moves:
        if not any(dx or dy for _,dx,dy,_ in rows):break
        active.append(rows)
    flow=spec['flow']
    if len(active)>flow['movable']:
        raise Refused('a following paragraph of the column flow must move but cannot be repainted: '+str(flow['unmovable']))
    return [{i:(dx,dy) for row,dx,dy,_ in rows for i in row['glyph_ids']} for rows in active],active


def _plan(source,page,selection,replacement,occurrence,substitute,stored=True,continuation=(),decorations=(),chain=()):
    start=time.perf_counter();c,paragraphs,spec,at,evidence=detect(source,page,selection,occurrence,stored,continuation);observed=time.perf_counter()
    try:
        # A single-column paragraph keeps what the edit does not change where
        # it was drawn (auto_local, observed advances); the paragraph is wrapped
        # again only where local layout does not apply.
        local=spec.get('mode')!='flow'
        # The paragraph's recorded layout (auto_restore), when its stored record applies.
        recorded=spec.get('recorded_layout')
        recorded=recorded if recorded and restore.valid(recorded,c.page.rect.width,c.page.rect.height) else None
        expected=paragraphs[0].text[:at]+replacement+paragraphs[0].text[at+len(selection):]
        prefs=restore.preferences(expected,recorded) if recorded and local else None
        tokens,fonts,counts,choice=build(c,paragraphs[0],at,selection,replacement,substitute,justify=spec['align']=='justify' and not local,recorded=prefs)
        # Alignment is an approved condition: a stored record supplies it after
        # the multi-line evidence has been edited away.
        spec['values'].update(align=spec['align'])
        moved=None
        if spec.get('mode')=='flow':
            lines,moves=place(tokens,''.join(t['text'] for t in tokens),spec['flow'],align=spec['align'])
            spec['moves'],active=_flow_moves(paragraphs,spec,moves)
            paragraphs=paragraphs[:1+len(active)]
            moved=[dict(paragraph=spec['flow']['target']+1+k,lines=[dict(text=row['text'],baseline=round(row['baseline'],3),region=region_of(spec['flow']['regions'],row),
                        to_baseline=round(row['baseline']+dy,3),to_region=region) for row,dx,dy,region in rows]) for k,rows in enumerate(active)]
        else:
            try:
                # A line ending with a soft-or-hard hyphen keeps its break.
                spec['hyphens']=list(paragraphs[0].hyphens)
                # The layout the paragraph had when first approved, carried by its
                # stored record; else the paragraph as drawn now (auto_restore).
                spec['original']=recorded or restore.snapshot(c,paragraphs[0],spec['source_rows'],spec.get('below'),tokens[0]['page_stats'] if tokens else None)
                # Layout follows a stored record only; a first edit is laid out as before.
                spec['recorded']=recorded
                # Whether an edit aligns old and new text per character (auto_style.changed_runs).
                kinds=[s['kind'] for s in paragraphs[0].scopes]
                spec['aligned']=sum(k in ('mcid','plain') for k in kinds)>1 or 'atom' in kinds or 'fixed' in kinds
                lines=local_layout(tokens,spec);spec['layout']='local'
                # Set as the recorded paragraph would set the text, the paragraph
                # or its followers would leave the frame, which an earlier edit
                # may have made room for: the text is set against the drawing.
                if _leaves_frame(lines,paragraphs,spec):lines=local_layout(tokens,spec,record=False)
            except LocalUnavailable as exc:
                spec['layout']='paragraph ('+str(exc)+')'
                if spec['align']=='justify':
                    tokens,fonts,counts,choice=build(c,paragraphs[0],at,selection,replacement,substitute,justify=True,recorded=prefs)
                lines=layout(tokens,spec)
            bottom=spec.get('column_bottom');delta=lines[-1]['baseline']-paragraphs[0].baselines[-1]
            deepest=max([lines[-1]['baseline']]+[p.baselines[-1]+delta for p in paragraphs[1:] if delta>0])
            if bottom is not None and deepest>bottom+.01:
                hint=('; candidate continuation(s) '+', '.join(spec['candidates'])+' require explicit approval') if spec['candidates'] else '; no continuation column is linked'
                if spec['rejected_links']:hint+=' (rejected '+'; '.join(spec['rejected_links'])+')'
                raise Refused('capacity: the paragraph would grow below the shared bottom of the side-by-side columns'+hint)
        _hyphens_stay_at_line_ends(paragraphs[0],lines)
        _placed_objects_stay(lines)
        # The lines below the paragraphs that the growth reaches (chain) are
        # listed with their evidence; they move only when every one is approved by id.
        spec['chain']=[]
        reached=None if spec.get('mode')=='flow' else following.plan(c,paragraphs,spec,evidence,follower_delta(paragraphs,lines,spec),
                                                                     followers=[_glyph_area(c,p) for p in paragraphs[1:]])
        listed_chain=[u['evidence']['id'] for u in (reached or {}).get('units',[])]
        if set(chain)-set(listed_chain):raise Refused('an approved following element is not a candidate of this edit: '+', '.join(sorted(set(chain)-set(listed_chain))))
        if chain and set(chain)!=set(listed_chain):
            raise Refused('the following elements of a chain move only together; approve every one: '+', '.join(sorted(set(listed_chain)-set(chain))))
        if chain and reached['unavailable']:raise Refused('capacity: the following elements cannot move with the paragraph: '+reached['unavailable'])
        spec['chain_candidates']=reached
        if chain:
            # The frame reaches down past the approved elements (chain.plan): to
            # the next protected paint that does not move, never past its limit.
            spec['chain']=reached['units'];spec['frame']=spec['frame'][:3]+[reached['frame_bottom']]
            evidence['inferred']['frame']=spec['frame'];evidence['inferred']['frame_bottom']='approved following elements move: the next protected paint below that does not move, within the container or clip'
        if reached:evidence['chain']=following.describe(reached)
        # Line decorations a following paragraph needs to move with it are
        # listed with their evidence; only those approved by id move.
        found,unavailable=({},{}) if spec.get('mode')=='flow' else decoration_candidates(c,paragraphs,lines,spec,evidence)
        listed={g['evidence']['id']:k for k,g in found.items()}
        if set(decorations)-set(listed):raise Refused('an approved line decoration is not a candidate of this edit: '+', '.join(sorted(set(decorations)-set(listed))))
        spec['decoration_candidates']=found
        spec['decorations']={listed[i]:found[listed[i]] for i in decorations}
        if found or unavailable:
            evidence['decorations']=dict(candidates=[g['evidence'] for g in found.values()],
                                         unavailable=[dict(paragraph=paragraphs[k].text[:40],reason=r) for k,r in sorted(unavailable.items())])
        public=dict(schema='auto-paragraph-frame-1',source_sha256=sha(source),page=page,selection=selection,replacement=replacement,occurrence=occurrence,
          substitute=substitute,stored_conditions=stored,evidence=evidence,source_styles=describe(c,paragraphs),paragraphs=[p.text for p in paragraphs],expected=paragraphs[0].text[:at]+replacement+paragraphs[0].text[at+len(selection):],
          lines=[dict(text=l['text'],baseline=l['baseline'],**({'region':l['region']} if 'region' in l else {}),**({'kept':l['kept']} if 'kept' in l else {}),
                      **({'restored':True} if l.get('restored') else {})) for l in lines],
          layout=spec.get('layout','column flow'),source_lines=len(paragraphs[0].baselines),font_counts=counts,substitute_choice=choice,
          required_approval=['inferred paragraph and capacity','possible follower translation','source style and any explicit substitute'])
        public['continuation']=list(continuation)
        hang=evidence['inferred'].get('hanging')
        if hang:
            # Every line as proposed, with its start: the joined lines are laid out again.
            hang['proposed']=[dict(text=l['text'],x=round(l['tokens'][0]['x'],3) if l.get('tokens') else None,baseline=round(l['baseline'],3)) for l in lines]
            public['required_approval'].append(f"lines joined to the paragraph by a hanging indent, laid out again: {len(hang['lines'])} drawn, {len(lines)} proposed")
        if chain:
            public['chain']=sorted(chain)
            public['required_approval'].append('following elements moved with the paragraph (chain): '+', '.join(sorted(chain)))
        if decorations:
            public['decorations']=sorted(decorations)
            public['required_approval'].append('line decorations moved with their following paragraph: '+', '.join(sorted(decorations)))
        if spec.get('mode')=='flow':
            public['moved_paragraphs']=moved
            public['required_approval'].append('column flow continuation and the lines moved between columns')
        public['proposal_sha256']=_digest(public)
        return c,paragraphs,spec,evidence,fonts,lines,public,dict(observation=observed-start,style_layout=time.perf_counter()-observed)
    except Exception:c.close();raise


def _glyph_area(c,paragraph):
    """Page area of a paragraph's drawn glyphs."""
    import pymupdf
    boxes=[pymupdf.Rect(c.actual[i]['bbox']) for i in paragraph.ids if c.actual[i]['unicode'].strip()]
    out=boxes[0]
    for b in boxes[1:]:out|=b
    return list(out)


def propose(source,page,selection,replacement,*,occurrence=0,substitute=None,stored_conditions=True,continuation=(),decorations=(),chain=()):
    """``stored_conditions=False`` ignores conditions stored by earlier saves (inference only).

    ``continuation`` lists candidate column links (``column a->b`` ids from a
    previous proposal's evidence) that the user explicitly approves.
    ``decorations`` lists line decorations (``line-decoration-…`` ids from a
    previous proposal's ``evidence.decorations``) that the user explicitly
    approves to move with their following paragraph.
    ``chain`` lists the following elements (``following-element-…`` ids from a
    previous proposal's ``evidence.chain``) that the user explicitly approves
    to move with the paragraph; a chain moves only with every element approved.
    """
    planned=_plan(source,page,selection,replacement,occurrence,substitute,stored_conditions,tuple(continuation),tuple(sorted(set(decorations))),tuple(sorted(set(chain))))
    planned[0].close();return planned[-2]


def approve(source,proposal,*,acknowledge_inference=False):
    if acknowledge_inference is not True:raise Refused('explicit inference/format approval required')
    again=propose(source,proposal['page'],proposal['selection'],proposal['replacement'],occurrence=proposal['occurrence'],substitute=proposal['substitute'],
                  stored_conditions=proposal.get('stored_conditions',True),continuation=proposal.get('continuation',()),decorations=proposal.get('decorations',()),chain=proposal.get('chain',()))
    if again!=proposal:raise Refused('stale or altered proposal')
    return dict(proposal=again,approved=True,approval_count=1)


def save(source,acceptance,output):
    if acceptance.get('approved') is not True:raise Refused('explicit approval missing')
    p=acceptance['proposal'];start=time.perf_counter();planned=_plan(source,p['page'],p['selection'],p['replacement'],p['occurrence'],p['substitute'],p.get('stored_conditions',True),tuple(p.get('continuation',())),tuple(p.get('decorations',())),tuple(p.get('chain',())))
    c,paragraphs,spec,evidence,fonts,lines,again,timing=planned
    try:
        if p!=again:raise Refused('stale or altered accepted proposal')
        before=time.perf_counter();result=write(c,paragraphs,lines,fonts,spec,evidence,output,approval=dict(proposal_sha256=p['proposal_sha256']),moves=spec.get('moves'))
        result.update(proposal_sha256=p['proposal_sha256'],pdf_sha256=sha(output),font_counts=p['font_counts'],lines=p['lines'],timing=dict(timing,save_and_stage=time.perf_counter()-before,total=time.perf_counter()-start))
        return result
    finally:c.close()
