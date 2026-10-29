"""Position native source codes and verified new glyphs; stage before publishing."""
from collections import Counter
from statistics import median
import os
from pathlib import Path
import subprocess
import tempfile
import pymupdf
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DictionaryObject,NameObject,TextStringObject,DecodedStreamObject
from pdfeditor.content_stream import ContentPage,number,serialized_event
from .auto_style import source_tokens
from .audit import painting,semantic
from PIL import Image,ImageChops,ImageDraw
import math
from .engine import Refused
from . import form_write
from . import semantics
from . import auto_restore
from . import placed_move
from . import decoration
from . import auto_conditions as conditions


def glyph_key(g):return (g['unicode'],g['gid'],*g['origin'],*g['bbox'])


def moved_key(g,shift=(0.,0.)):
    """A glyph translated by ``shift`` (page), to 0.001 pt: a translated drawing is read back through another matrix."""
    dx,dy=shift;x0,y0,x1,y1=g['bbox']
    return (g['unicode'],g['gid'],round(g['origin'][0]+dx,3),round(g['origin'][1]+dy,3),round(x0+dx,3),round(y0+dy,3),round(x1+dx,3),round(y1+dy,3))


def follower_delta(paragraphs,lines,spec):
    """The page translation of the following paragraphs (single column)."""
    delta=lines[-1]['baseline']-paragraphs[0].baselines[-1]
    if len(paragraphs)>1:delta=auto_restore.follower_shift(spec.get('original'),delta,paragraphs[1].baselines[0])
    return 0. if abs(delta)<.001 else delta


def _fixed_units(c,paragraph):
    """The placed_move units of a following paragraph and their program, or a refusal."""
    program=next((s['stream_xref'],tuple(tuple(i) for i in s['invocation'])) for s in paragraph.scopes if s['kind']=='fixed')
    return program,[placed_move.unit(c,paragraph,s,program,c.page.transformation_matrix) for s in paragraph.scopes if s['kind']=='fixed']


def decoration_candidates(c,paragraphs,lines,spec,evidence):
    """Line decorations the following paragraphs need to move with them
    (decoration.group), by follower index, and why others have none.

    Only a follower whose placed objects would leave other content on its
    line behind gets a candidate; it moves only once approved by its id."""
    if spec.get('mode')=='flow':return {},{}
    delta=follower_delta(paragraphs,lines,spec)
    if not delta:return {},{}
    frames=spec.get('frames',[spec['frame']]);obstacles=evidence['observed']['obstacles'];page=c.page.transformation_matrix
    units,unavailable,found={},{},{}
    for k,p in enumerate(paragraphs[1:],1):
        if not p.fixed:continue
        try:units[k]=_fixed_units(c,p)
        except Refused as exc:unavailable[k]=str(exc)
    # Approved following elements (chain) move by the same translation.
    approved=[g['bounds'] for g in spec.get('chain') or ()]
    for k,(program,us) in units.items():
        moving=[u['bounds'] for j,(_,vs) in units.items() if j!=k for u in vs]+approved
        try:placed_move.plan(c,paragraphs[k],program,delta,frames,obstacles,page,moving=moving);continue
        except Refused as exc:
            if 'holds other content' not in str(exc):unavailable[k]=str(exc);continue
        taken={i for j,q in enumerate(paragraphs) if j!=k for i in q.ids}
        try:found[k]=decoration.group(c,paragraphs[k],spec['page'],delta,frames,obstacles,page,units=us,taken=taken,moving=moving)
        except Refused as exc:unavailable[k]=str(exc)
    return found,unavailable


def write(c,paragraphs,lines,fonts,spec,evidence,output,approval=None,moves=None):
    """``moves`` (column flow) gives each following paragraph's glyph translations;
    without it, B2 translates all following paragraphs rigidly when the edited
    paragraph's last baseline changes.

    ``spec['decorations']`` (by follower index) are the line decorations the
    user approved by id (decoration.group); they move with their paragraph."""
    if Path(output).exists():raise Refused('output already exists')
    delta=lines[-1]['baseline']-paragraphs[0].baselines[-1]
    if moves is None and len(paragraphs)>1:delta=follower_delta(paragraphs,lines,spec)
    if abs(delta)<.001:delta=0.
    if moves is None:
        active=paragraphs if delta else paragraphs[:1]
        moves=[{i:(0.,delta) for i in p.ids} for p in active[1:]]
    else:
        active=paragraphs[:1+len(moves)]
    # Lines that local layout kept as drawn keep their operators too, so
    # their glyphs, drawing order and extraction order stay the source's.
    kept_ids,kept_scopes=kept_lines(c,active[0],lines)
    # An edit that leaves every line as drawn (the same text) is redrawn at
    # the source origins: there is no drawing site without removed text.
    if not set(active[0].ids)-kept_ids-set(active[0].fixed):kept_ids,kept_scopes=set(),set()
    # An approved line decoration keeps its operators, its paragraph's glyphs too.
    groups={k:g for k,g in (spec.get('decorations') or {}).items() if k<len(active)}
    # Following elements the growth reaches move only once approved (chain).
    chained=spec.get('chain') or []
    reached=spec.get('chain_candidates') or {}
    if reached.get('needed') and reached.get('units') and not reached.get('unavailable') and not chained:
        raise Refused('capacity: the paragraph would grow into the lines below it (following elements '
                      +', '.join(u['evidence']['id'] for u in reached['units'])+' move with it only when approved explicitly)')
    held={i for g in groups.values() for i in g['glyphs']}
    # Inline placed objects stay drawn as they are (semantics: fixed).
    selected={i for p in active for i in p.ids if i not in set(p.fixed) and i not in kept_ids and i not in held}
    removed=[e for e in c.events if any(selected.intersection(ch.source_orders) for ch in e.chars)]
    # The edited text is rewritten in the program that draws it: the page or
    # one Form XObject invocation (copy-on-write, see form_write).
    stream,invocation=form_write.drawing_site(c,removed)
    if invocation and any(s['kind']=='mcid' for p in active for s in p.scopes):raise Refused('marked content inside a Form XObject requires a semantic adapter')
    # An inline placed object of a following paragraph moves with its text
    # when the operators that draw it are proven its own (placed_move); it is
    # translated where it is drawn, by the same page translation.
    units=[]
    others={}
    for k,p in enumerate(active[1:],1):
        if p.fixed and k not in groups:
            try:others[k]=[u['bounds'] for u in _fixed_units(c,p)[1]]
            except Refused:others[k]=[]
    for k,(p,move) in enumerate(zip(active[1:],moves),1):
        if not p.fixed:continue
        shifts={move[i] for i in p.ids}
        if len(shifts)!=1 or next(iter(shifts))[0]:raise Refused('a following paragraph with an inline placed object cannot move')
        dy=next(iter(shifts))[1]
        if k in groups:
            g=groups[k]
            if g['program']!=(stream,invocation):raise Refused('an approved line decoration is not drawn where the edit is written')
            units.append(dict(g,paragraph=p,shift=(0.,dy)))
            continue
        # Approved decorations, following elements and the other followers' placed objects move with it.
        moving=[g['bounds'] for g in groups.values()]+[g['bounds'] for g in chained]+[r for j,rs in others.items() if j!=k for r in rs]
        try:units+=[dict(u,paragraph=p,shift=(0.,dy)) for u in placed_move.plan(c,p,(stream,invocation),dy,spec.get('frames',[spec['frame']]),
                                                                                evidence['observed']['obstacles'],c.page.transformation_matrix,moving=moving)]
        except Refused as exc:
            candidate=(spec.get('decoration_candidates') or {}).get(k)
            if candidate:raise Refused(f"{exc} (line decoration {candidate['evidence']['id']} moves with it only when approved explicitly)")
            raise
    # Approved following elements: their operators, translated where they are drawn.
    for g in chained:
        if g['program']!=(stream,invocation):raise Refused('an approved following element is not drawn where the edit is written')
        units.append(dict(g,paragraph=g['row']['paragraph'],shift=(0.,delta)))
    moved={i:u['shift'] for u in units for i in u['glyphs']}
    if {i for p in active[1:] for i in p.fixed}-set(moved):raise Refused('a following paragraph with an inline placed object cannot move')
    edits=[(e.operator.start,e.operator.end,serialized_event(e,selected,remove=True)) for e in removed]
    program=c.streams[stream]
    for u in units:
        # A line separated from a text object that draws other text too
        # (decoration.separate): its operators leave it and are drawn, moved, at their place in the content.
        for x in u.get('splits') or ():edits+=list(x['edits'])
        for r in u.get('ranges') or ([u] if u.get('start') is not None else []):
            # q and the translation before its first operator, Q after its last
            # (each extends that operator, so insertions at the same place keep their order).
            tx,ty=r['translation'];first_end=placed_move.operator_end(c,(stream,invocation),r['start'])
            edits.append((r['start'],first_end,b'q 1 0 0 1 '+number(tx)+b' '+number(ty)+b' cm\n'+program[r['start']:first_end]))
            last=placed_move.operator_start(c,(stream,invocation),r['end'])
            # The state a line decoration left is set again after it (decoration.group).
            edits.append((last,r['end'],program[last:r['end']]+b'\nQ'+(b'\n'+r['replay'] if r.get('replay') else b'')))
    # Re-emitted sequences lose their old BDC/EMC; they are written again
    # around their new glyphs (fixed objects and containers stay as drawn).
    for p in active:
        for n,s in enumerate(p.scopes):
            if p is active[0] and n in kept_scopes:continue
            if s['kind']!='fixed':edits.extend([(s['begin_byte_range'][0],s['begin_byte_range'][1],b''),(s['end_byte_range'][0],s['end_byte_range'][1],b'')])
    first=min(e.operator.start for e in removed)
    containers=active[0].containers
    if any([x['id'] for x in p.containers]!=[x['id'] for x in containers] for p in active):raise Refused('edited paragraphs lie in different marked-content containers')
    boundaries=c.stream_boundaries[(stream,invocation)];program=c.streams[stream];last=max(e.operator.end for e in removed)
    starts={e.operator.start for e in removed}
    # Keep content (reading) order: before the edited text if no other text
    # lies in between, else right after its text object, else spliced into
    # the text object right before the edited text. When the first edited
    # operator keeps glyphs drawn before the edit (and the last none after
    # it), the place after its text object comes first.
    reemitted=[x for p in active for n,x in enumerate(p.scopes) if x['kind']!='fixed' and not (p is active[0] and n in kept_scopes)
               and x['stream_xref']==stream and tuple(tuple(i) for i in x['invocation'])==invocation]
    safe=form_write.safe_boundaries(boundaries,first,containers,reemitted)
    kept=lambda e:[bool(selected.intersection(ch.source_orders)) for ch in e.chars]
    head,tail=kept(min(removed,key=lambda e:e.operator.start)),kept(max(removed,key=lambda e:e.operator.start))
    lead=not head[0];trail=not tail[-1]
    before=safe[-1] if safe and not form_write.foreign_text_between(program,safe[-1].operator.end,first,starts) else None
    after=form_write.after_text(boundaries,program,first,last,containers)
    if after is not None and form_write.foreign_text_between(program,last,after.operator.end,starts):after=None
    candidates=[(b,b'') for b in ((after,before) if lead and not trail else (before,after)) if b is not None]
    if not any(p.scopes for p in active):
        spliced=form_write.inside_text(boundaries,program,min(removed,key=lambda e:e.operator.start),containers)
        if spliced:candidates.append(spliced)
    # Earlier boundaries with no other text in between keep the content order
    # too; one of them may have the graphics state and clip of the text itself.
    candidates+=[(b,b'') for b in reversed(safe) if b is not before and not form_write.foreign_text_between(program,b.operator.end,first,starts)]
    if safe:candidates.append((safe[-1],b''))
    if not candidates:raise Refused('no safe insertion point before the edited text')
    # New drawing is placed in default user space under the inverse CTM, at
    # the first candidate whose inherited state (opacity, ExtGState, clip) is
    # proven harmless for the approved frames.
    problem=None;neutral=None
    # The edited text itself was drawn opaque and with harmless ExtGStates:
    # where the inherited state is not proven harmless, the new drawing sets
    # that state again (a neutral ExtGState with the text's own overprint).
    own=all(form_write.drawn_neutral(e.state) for e in removed)
    # Overprint of each glyph as drawn (InDesign: black text overprints, a
    # coloured marker does not). Glyphs of different overprint each get a
    # neutral ExtGState of their own.
    variant=lambda state:tuple(sorted(form_write.overprint([state]).items()))
    variants=sorted({variant(e.state) for e in removed})
    if len(variants)>1 and not own:raise Refused('edited text with mixed overprint is not drawn opaque')
    own_overprint=dict(variants[0]) if len(variants)==1 else None
    taken_gs=set(form_write.program_fonts(c,invocation,'/ExtGState'));gs_names={}
    for v in variants:
        name='/PFNeutral'+(str(len(gs_names)) if gs_names else '')
        while name in taken_gs:name+='x'
        gs_names[v]=name
    neutral_resource=gs_names[variants[0]]
    for choice,restore in candidates if own_overprint is not None else ():
        try:prefix=form_write.insertion(choice,spec.get('frames',[spec['frame']]),c.page.transformation_matrix,text_overprint=own_overprint);break
        except Refused as exc:problem=problem or exc
    else:
        problem=problem or Refused('no safe insertion point before the edited text')
        if not own:raise problem
        for choice,restore in candidates:
            try:prefix=form_write.insertion(choice,spec.get('frames',[spec['frame']]),c.page.transformation_matrix,neutral=True);break
            except Refused:continue
        else:raise problem
        neutral=neutral_resource
        prefix+=neutral.encode()+b' gs\n'
    insertion=choice.operator.end
    # Page (top-left) coordinates to default user space, whatever the MediaBox origin.
    to_user=~c.page.transformation_matrix
    if any(a<insertion<b for a,b,_ in edits):raise Refused('insertion intersects removal')
    writer=PdfWriter(clone_from=c.reader);page=writer.pages[spec['page']-1];lookup=writer._id_translated[id(c.reader)]
    taken=set(form_write.program_fonts(c,invocation));built={};new_fonts={}
    for key,font in fonts.items():
        name='/PFAuto'+str(len(taken));
        while name in taken:name+='x'
        taken.add(name)
        runs=[font.shape(t['text']) for l in lines for t in l['tokens'] if t.get('font_key')==key]
        resource=font.resource(runs);new_fonts[name]=resource.build(writer);built[key]=(name,resource)
    # An implicit line join the paragraph's recorded layout drew without a
    # glyph stays undrawn when its line is restored (auto_restore); it remains
    # in the paragraph's text.
    source,_=source_tokens(c);batches=[(paragraphs[0],[t for l in lines if not l.get('kept') or not kept_ids for t in l['tokens'] if not t.get('undrawn')])]
    full_text=''.join(t['text'] for l in lines for t in l['tokens'] if not t.get('fixed'))
    for p,move in zip(active[1:],moves):
        placed=[]
        for i in p.ids:
            dx,dy=move[i]
            t=dict(source[i],x=source[i]['origin'][0]+dx,y=source[i]['origin'][1]+dy)
            # A glyph of a moved placed object stays in its operators (placed_move).
            if i in moved:t.update(fixed=True,unit=next(k for k,u in enumerate(units) if i in u['glyphs']))
            for n,s in enumerate(p.scopes):
                if s['stream_xref']==t['event'].stream_xref and s['content_byte_range'][0]<=t['event'].operator.start<s['content_byte_range'][1]:t['scope']=n;break
            placed.append(t)
        batches.append((p,placed))
    data=c.streams[stream];ops=[];rewritten=set()
    # Laid-out positions come from rendered (float32) glyph origins; where they
    # are the source's own coordinates, write the exact source values, so that
    # renderers order lines and words as in the source.
    pool=([],[])
    for e in removed:
        A,B,C,D,E,F=e.state.ctm
        for ch in e.chars:
            if ch.source_text_matrix:
                a,b,c_,d,e_,f=ch.source_text_matrix;u,v=e.state.ts*c_+e_,e.state.ts*d+f
                pool[0].append(u*A+v*C+E);pool[1].append(u*B+v*D+F)
    def exact(value,axis):
        best=min(pool[axis],key=lambda p:abs(p-value),default=value)
        return best if abs(best-value)<1e-3 else value
    def emit(p,tokens,edited,prefix):
        """Drawing of ``tokens`` of paragraph ``p``: its sequences re-emitted around their glyphs."""
        ops=[]
        # Following paragraphs are repainted as a rigid translation of their
        # rendered glyph origins, one positioned glyph each; runs and exact
        # source coordinates apply to the edited paragraph.
        runs=scope_runs(c,p,[t for t in tokens if not t.get('fixed')],skip=kept_scopes if p is paragraphs[0] else ())
        for n,ts in runs:
            if n is not None:
                s=p.scopes[n];a,b=s['begin_byte_range'];ops.append(data[a:b]+b'\n')
                if s['kind']=='mcid':
                    owner=c.reader.get_object(s['association']['owner_reference']['xref'])
                    if '/ActualText' in owner:
                        writer.get_object(lookup[s['association']['owner_reference']['xref']])[NameObject('/ActualText')]=TextStringObject(''.join(t['text'] for t in ts))
                        rewritten.add(s['association']['owner_reference']['xref'])
            if not ts:
                if n is not None:ops.append(b'EMC\n')
                continue
            ops.append(b'q\n'+prefix+b'BT\n0 Tc 0 Tw 0 Ts 0 Tr\n')
            run=None;current=variants[0];spacing_state=(0.,0.)
            for t in ts:
                name=t['font_name'];code=t['code'];x=t['x'];y=t['y']
                if len(variants)>1 and variant(t['event'].state)!=current:
                    current=variant(t['event'].state);ops.append(gs_names[current].encode()+b' gs\n');run=None
                if t.get('font_key'):
                    name,res=built[t['font_key']];g=t['shaped'];font=fonts[t['font_key']];code=res.code(g)
                    x+=g.x_offset*t['size']/font.upem*t['tz']/100;y-=g.y_offset*t['size']/font.upem
                color=' '.join(str(float(v)) for v in t['fill'][1])+' '+t['fill'][0]
                if t['fill'][0] in ('sc','scn'):color=t['fill_space']+' cs '+color
                # Consecutive glyphs of one original font and style on one
                # baseline form one TJ, kerned to the laid-out pen positions,
                # like the source: text extraction joins them as the source.
                # Kerns are rounded to 1/100 000 em (float noise would be read
                # as word breaks); the pen follows the written kerns. The
                # glyphs keep their source character and word spacing (Tc, Tw):
                # a later edit reads the text drawn as the source drew it.
                tc,tw=t.get('tc',0.),t.get('tw',0.)
                pen=lambda t:t['nominal_advance']+(tc+(tw if code==b' ' else 0.))*t['tz']/100
                key=None if t.get('font_key') or not edited else (name,t['size'],t['tz'],tc,tw,color,y)
                if key is not None and run is not None and run[0]==key:
                    unit=t['size']*t['tz']/100000;kern=round((run[1]-x)/unit,2)
                    ops[-1]=ops[-1][:-4]+(b' '+number(kern)+b' ' if kern else b'')+f'<{code.hex()}>]TJ\n'.encode()
                    run=(key,run[1]-kern*unit+pen(t))
                    continue
                point=pymupdf.Point(x,y)*to_user
                px,py=(exact(point.x,0),exact(point.y,1)) if edited else (point.x,point.y)
                spacing=f' {tc:.9f} Tc {tw:.9f} Tw' if (tc,tw)!=spacing_state else '';spacing_state=(tc,tw)
                ops.append(f'{color}\n{name} {t["size"]:.9f} Tf {t["tz"]:.9f} Tz{spacing} 1 0 0 1 {px:.9f} {py:.9f} Tm [<{code.hex()}>]TJ\n'.encode())
                run=None if key is None else (key,x+pen(t))
            ops.append(b'ET\nQ\n')
            if n is not None:ops.append(b'EMC\n')
        return ops
    # An inline placed object stays where it is in the content: the text
    # after it is inserted after its sequence, so content order is kept.
    head,first_tokens=batches[0]
    pieces=[[]]
    for t in first_tokens:
        if t.get('fixed'):
            if t['scope'] not in [x for x,_ in pieces[1:]]:pieces.append((t['scope'],[]))
            continue
        (pieces[-1] if not isinstance(pieces[-1],tuple) else pieces[-1][1]).append(t)
    parts=[(None,pieces[0])]+[(n,ts) for n,ts in pieces[1:]]
    if len(parts)>1 and any(x['kind']=='mcid' for x in head.scopes):raise Refused('structured marked content around an inline placed object')
    drawn=[]
    def after(end):
        """The first boundary after ``end`` (an inline placed object) whose clip
        holds the frame (an object's clip may be set before its BDC and
        restored after its EMC), no later than the text that followed it in
        the source, with no other text in between: paint and content order
        are kept."""
        nonlocal neutral
        limit=min([e.operator.start for e in removed if e.operator.start>=end]+[containers[-1]['end_byte_range'][0] if containers else len(data)])
        spot=pre=problem=None;places=[]
        for b in boundaries:
            if not end<=b.operator.end<=limit or b.marked_content_depth!=len(containers) or b.text_object:continue
            if form_write.foreign_text_between(data,end,b.operator.end,starts):break
            places.append(b)
        for use in (True,) if neutral else (False,True) if own else (False,):
            for b in places:
                try:pre=form_write.insertion(b,spec.get('frames',[spec['frame']]),c.page.transformation_matrix,neutral=use,text_overprint=own_overprint);spot=b;break
                except Refused as exc:problem=problem or exc
            if spot is not None:
                if use:neutral=neutral_resource;pre+=neutral.encode()+b' gs\n'
                break
        if spot is None:raise Refused('no safe insertion point after an inline placed object'+(f' ({problem})' if problem else ''))
        at=spot.operator.end
        if any(a<at<b for a,b,_ in edits):raise Refused('insertion intersects removal')
        return at,(spot,b''),pre
    # A following paragraph's text before its first moved placed object goes
    # with the edited paragraph; the text after one goes right after it.
    leading,trailing=[],[]
    for p,tokens in batches[1:]:
        lead,runs=[],{}
        for t in tokens:
            if t.get('unit') is not None:runs.setdefault(t['unit'],[]);current=runs[t['unit']];continue
            (current if runs else lead).append(t)
        if any(runs.values()) and any(s['kind']=='mcid' for s in p.scopes):raise Refused('structured marked content around an inline placed object of a following paragraph')
        leading.append((p,lead));trailing+=[(p,units[k],ts) for k,ts in runs.items() if ts]
    for k,(n,ts) in enumerate(parts):
        if not k:
            at,pre,wrapped=insertion,prefix,(choice,restore)
        else:
            at,wrapped,pre=after(head.scopes[n]['end_byte_range'][1])
        ops=emit(head,ts,True,pre) if ts else []
        if k==len(parts)-1:
            for p,tokens in leading:ops+=emit(p,tokens,False,pre)
        drawn.append((at,wrapped,ops))
    for p,u,ts in trailing:
        at,wrapped,pre=after(u['end'])
        drawn.append((at,wrapped,emit(p,ts,False,pre)))
    # New text is never written inside a moved unit: its translation would move it too.
    spans=[(r['start'],r['end']) for u in units for r in u.get('ranges') or ([u] if u.get('start') is not None else [])]+[tuple(x['text_object']) for u in units for x in u.get('splits') or ()]
    if any(a<at<b for a,b in spans for at,_,ops in drawn if ops) or any(a<insertion<b for a,b in spans):
        raise Refused('new text would be written inside a moved placed object or line decoration')
    for at,(spot,restore_),ops in drawn:
        if ops:edits.append((at,at,form_write.wrap(spot,b''.join(ops),restore_)))
    # Replacement text of a paragraph element itself follows its new text.
    for p,tokens in batches:
        if p.parent is not None and '/ActualText' in c.reader.get_object(p.parent):
            text=full_text if p is paragraphs[0] else ''.join(t['text'] for t in tokens if not t.get('fixed'))
            writer.get_object(lookup[p.parent])[NameObject('/ActualText')]=TextStringObject(text)
            rewritten.add(p.parent)

    for a,b,replacement in sorted(edits,reverse=True):data=data[:a]+replacement+data[b:]
    decoration.move_annotations(writer,lookup,list(groups.values())+list(chained))
    clones=form_write.copy_on_write(writer,page,c,stream,invocation,data,new_fonts,{name:writer._add_object(form_write.neutral_gs(dict(v))) for v,name in gs_names.items()} if neutral else None)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(suffix='.pdf',dir=output.parent);os.close(fd)
    fd,draft=tempfile.mkstemp(suffix='.pdf',dir=output.parent);os.close(fd)
    expected=''.join(t['text'] for _,ts in batches for t in ts if not t.get('fixed'))
    try:
        writer.write(draft)
        if spec.get('mode')=='flow':stored=store_flow(c,draft,writer,page,spec,selected,[len(ts) for _,ts in batches],approval)
        else:stored=store_conditions(c,draft,writer,page,spec,selected,len(batches[0][1]),delta,active,approval,kept=kept_ids,moved=moved,
                                     carried=[(set(p.ids)|{i for u in units if u['paragraph'] is p for i in u['glyphs']},dy) for p,dy in
                                              {id(u['paragraph']):(u['paragraph'],u['shift'][1]) for u in units}.values()])
        writer.write(tmp)
        validate_stage(c,tmp,spec,evidence,selected,expected,actual_text=rewritten,moved=moved,units=units)
        form_write.unchanged_elsewhere(c.source,tmp,spec['page'])
        if stored['record'] is not None:verify_conditions(tmp,spec['page'],stored['record'])
        os.replace(tmp,output)
    finally:
        for path in (tmp,draft):
            if os.path.exists(path):os.unlink(path)
    return dict(drawn_in='page' if not invocation else 'Form XObject (copy-on-write)',form_clones=clones,insertion_ctm=list(choice.state.ctm),insertion_inside_text_object=choice.text_object,stored_conditions=stored['summary'],follower_translation=delta if spec.get('mode')!='flow' else 'per line (column flow)',followers_repainted=len(active)-1,placed_objects_moved=[dict(old=u['old'],new=u['new'],translation=list(u['translation']),glyphs=len(u['glyphs'])) for u in units if 'evidence' not in u],decorations_moved=[moved_record(u) for u in units if 'evidence' in u and 'row' not in u],**({'chain_moved':[dict(moved_record(u),line=u['row']['text'][:40]) for u in units if 'row' in u]} if chained else {}),source_glyphs_removed=len(selected),layout=spec.get('layout','column flow'),kept_glyphs=len(kept_ids),scope_policy='original ordered scopes and ParentTree retained',stage_checks=['protected glyph identity','exact drawn text','inferred frame bounds','protected paint collision','page count','font programs','protected non-text paint','semantic consistency','MuPDF and Poppler inferred-frame outside pixels'])


def moved_record(u):
    """A moved line decoration or following element in the save record."""
    return dict(id=u['evidence']['id'],old=u['old'],new=u['new'],translation=list(u['translation']),glyphs=len(u['glyphs']),
                **({'separated':len(u['splits'])} if u.get('splits') else {}),
                annotations=[dict(xref=a['xref'],old=[round(v,3) for v in a['rect']],new=[round(v,3) for v in pymupdf.Rect(a['rect'])+(0,u['shift'][1],0,u['shift'][1])],uri=a['uri']) for a in u['annotations']])


def kept_lines(c,paragraph,lines):
    """Source glyph IDs of the lines local layout kept as drawn, and the
    paragraph's marked-content sequences all of whose glyphs are on them.

    Their operators stay. Nothing is kept when a re-emitted sequence would
    hold kept and redrawn glyphs (it is written once), nor around an inline
    placed object: the paragraph is then redrawn, kept lines at their
    origins.
    """
    ids={t['source_id'] for l in lines if l.get('kept') for t in l['tokens'] if t.get('source_id') is not None}
    if not ids or paragraph.fixed:return set(),set()
    # Sequences that redrawn text (new glyphs included) is written into.
    drawn={t.get('scope') for l in lines if not l.get('kept') for t in l['tokens']}
    mine=set(paragraph.ids);scopes=set()
    for n,s in enumerate(paragraph.scopes):
        hits=semantics._hits(c,s)&mine
        if hits and hits<=ids and n not in drawn:scopes.add(n)
        elif hits&ids:return set(),set()
    return ids,scopes


def scope_runs(c,p,tokens,skip=()):
    """``(scope index or None, tokens)`` runs in reading order, with emptied MCIDs kept.

    Each re-emitted sequence must be one contiguous run (an MCID is written
    once). A replacement-text atom is written only around exactly its old
    glyphs; with none left it disappears with them. An MCID left without
    glyphs is written as an empty sequence where it stood, so the structure
    element keeps its content item.
    """
    runs=[]
    for t in tokens:
        n=t.get('scope')
        if runs and runs[-1][0]==n:runs[-1][1].append(t)
        else:runs.append((n,[t]))
    seen=[n for n,_ in runs if n is not None]
    if len(seen)!=len(set(seen)):raise Refused('replacement crosses incompatible scope reading order')
    for n,ts in runs:
        if n is not None and p.scopes[n]['kind']=='atom':
            lo,hi=p.scopes[n]['content_byte_range']
            old=[i for e in c.events if e.stream_xref==p.scopes[n]['stream_xref'] and lo<=e.operator.start<hi for ch in e.chars for i in ch.source_orders]
            if [t.get('source_id') for t in ts]!=old:raise Refused('replacement-text marked content would change')
    present=set(seen)
    for n,s in enumerate(p.scopes):
        if s['kind']!='mcid' or n in present or n in skip:continue
        # After the run of the preceding sequence in source order, else first.
        before=[k for k,(m,_) in enumerate(runs) if m is not None and m<n]
        runs.insert(before[-1]+1 if before else 0,(n,[]))
    order=[n for n,_ in runs if n is not None]
    if order!=sorted(order):raise Refused('replacement crosses incompatible scope reading order')
    return runs


def _covered(rect,boxes):
    """Whether the corners and centre of ``rect`` lie in the given boxes."""
    points=[pymupdf.Point(x,y) for x in (rect.x0+.01,rect.x1-.01) for y in (rect.y0+.01,rect.y1-.01)]+[(rect.tl+rect.br)/2]
    return all(any(b.contains(p) for b in boxes) for p in points)


def validate_stage(c,tmp,spec,evidence,selected,expected,actual_text=None,moved=None,units=()):
    """Read-only publication checks, also callable on existing saved candidates.

    ``actual_text``: structure elements whose /ActualText the edit rewrote;
    every other entry and relation of the structure tree must be unchanged.
    ``moved``/``units``: glyphs and drawings of inline placed objects approved
    to move with their paragraph (placed_move): each must be found exactly
    translated, and nothing else may change.
    """
    check=ContentPage(tmp,spec['page'])
    try:
        if check.errors:raise Refused('staged source interpretation failed')
        moved=moved or {}
        foreign=Counter(glyph_key(g) for i,g in enumerate(c.actual) if i not in selected and i not in moved);remain=Counter(foreign);added=[]
        shifted=Counter(moved_key(c.actual[i],moved[i]) for i in moved)
        for g in check.actual:
            key=glyph_key(g)
            if remain[key]:remain[key]-=1
            elif shifted[moved_key(g)]:shifted[moved_key(g)]-=1
            else:added.append(g)
        if +remain:raise Refused('staged protected text changed')
        if +shifted:raise Refused('staged moved placed object text differs from its approved translation')
        def styled(content,exclude=None,only=None,key=lambda content,i:glyph_key(content.actual[i])):
            result=Counter()
            for event in content.events:
                for ch in event.chars:
                    for i in ch.source_orders:
                        if exclude and i in exclude or only is not None and i not in only:continue
                        state=event.state
                        result[(key(content,i),ch.code,state.font.name,state.size,state.tz,state.fill,state.tr,state.opacity)]+=1
            return result
        if styled(c,set(selected)|set(moved))-styled(check):raise Refused('staged protected source code/style changed')
        # A moved placed object's glyphs keep their code and style where they moved.
        if moved and styled(c,only=moved,key=lambda content,i:moved_key(content.actual[i],moved[i]))-styled(check,key=lambda content,i:moved_key(content.actual[i])):
            raise Refused('staged moved placed object code/style changed')
        if ''.join(g['unicode'] for g in added)!=expected:raise Refused('staged drawing text differs from requested paragraphs')
        old_boxes=[pymupdf.Rect(c.actual[i]['bbox'])+(-.3,-.3,.3,.3) for i in selected if c.actual[i]['unicode'].strip()]
        frames=[pymupdf.Rect(f) for f in spec.get('frames',[spec['frame']])]
        for g in added:
            if not g['unicode'].strip():continue
            box=pymupdf.Rect(g['bbox'])
            # A glyph where a replaced glyph of the paragraph was (an original
            # box on the edge of the clip that bounds the frame) adds no ink
            # beyond it; the outside-pixel checks below still apply.
            if not any(frame.contains(box) for frame in frames) and not any(o.contains(box) for o in old_boxes):
                raise Refused(f"capacity: staged glyph bounds outside approved inferred frame ({g['unicode']!r} at {[round(v,1) for v in box]})")
            for o in evidence['observed']['obstacles']+[dict(kind='moved placed object',bounds=u['new'],old=u['old'],shift=u['shift']) for u in units]:
                # Target/follower glyphs were excluded at detection. A container
                # (background behind every original line) bounds the frame instead.
                if o['kind']=='container':continue
                # A moved placed object's paint is checked where it moved to.
                if o['kind']!='moved placed object' and any((pymupdf.Rect(u['old'])+(-.5,-.5,.5,.5)).contains(pymupdf.Rect(o['bounds'])) for u in units):continue
                overlap=box&pymupdf.Rect(o['bounds'])
                if o['kind']=='moved placed object' and not overlap.is_empty:
                    # Its own paragraph's text overlapped it before as it does now.
                    back=overlap+(-o['shift'][0],-o['shift'][1],-o['shift'][0],-o['shift'][1])
                    if _covered(back,old_boxes):continue
                # New ink may overlap protected paint only where the replaced
                # glyphs already did (text on part of a band or label).
                if not overlap.is_empty and overlap.width>.05 and overlap.height>.05 and not _covered(overlap,old_boxes):
                    raise Refused('staged glyph collides with protected '+o['kind'])
        original_paint=painting(c.source,spec['page']);staged_paint=painting(tmp,spec['page'])
        if units:
            # Paint inside a moved placed object is expected where it moved to
            # (to 0.02 pt: the translation rounds either way at 0.01 pt); all other paint is unchanged.
            within=lambda item,key,which:any((pymupdf.Rect(u[which])+(-.5,-.5,.5,.5)).contains(pymupdf.Rect(item[key])) for u in units)
            if Counter(placed_move.paint_key(x) for x in original_paint['drawings'] if not within(x,'rect','old'))!=Counter(placed_move.paint_key(x) for x in staged_paint['drawings'] if not within(x,'rect','new')):
                raise Refused('staged protected non-text paint changed')
            if not placed_move.same_paint([placed_move.paint_key(x,units) for x in original_paint['drawings'] if within(x,'rect','old')],
                                          [placed_move.paint_key(x) for x in staged_paint['drawings'] if within(x,'rect','new')]):
                raise Refused('staged moved paint differs from its approved translation')
            # A shading of an approved line decoration is reported as an image
            # rasterized on the device grid: its samples and its position to a
            # device pixel (96 dpi) follow the translation, not its bytes.
            shaded=[u for u in units if (u.get('evidence') or {}).get('shadings')]
            inside=lambda image,rects:any((pymupdf.Rect(r)+(-.5,-.5,.5,.5)).contains(pymupdf.Rect(image['bbox'])) for r in rects)
            o_rest=[x for x in original_paint['images'] if not inside(x,[u['old'] for u in shaded])]
            s_rest=[x for x in staged_paint['images'] if not inside(x,[u['new'] for u in shaded])]
            if Counter(placed_move.image_key(x) for x in o_rest if not within(x,'bbox','old'))!=Counter(placed_move.image_key(x) for x in s_rest if not within(x,'bbox','new')):
                raise Refused('staged protected non-text paint changed')
            if not placed_move.same_paint([placed_move.image_key(x,units) for x in o_rest if within(x,'bbox','old')],
                                          [placed_move.image_key(x) for x in s_rest if within(x,'bbox','new')]):
                raise Refused('staged moved paint differs from its approved translation')
            # An image of the decoration is where it moved, with the same samples;
            # a shading's raster (at most one per shading) only lies inside its new area, in its colour space.
            o_shaded=[x for x in original_paint['images'] if x not in o_rest];s_shaded=[x for x in staged_paint['images'] if x not in s_rest]
            for x in list(o_shaded):
                dy=next(u['shift'][1] for u in shaded if inside(x,[u['old']]))
                hit=next((y for y in s_shaded if y.get('digest')==x.get('digest') and all(abs(p+q-r)<=.02 for p,q,r in zip(x['bbox'],(0,dy,0,dy),y['bbox']))),None)
                if hit is not None:o_shaded.remove(x);s_shaded.remove(hit)
            space=lambda images:sorted((x.get('cs-name'),x.get('bpc')) for x in images)
            if len(o_shaded)>sum(u['evidence']['shadings'] for u in shaded) or space(o_shaded)!=space(s_shaded):
                raise Refused('staged moved line decoration paint differs from its approved translation')
            if original_paint['annots']!=staged_paint['annots']:raise Refused('staged protected non-text paint changed')
            # A link of an approved line decoration is where it moved, with the same target; the others are unchanged.
            carried=[(pymupdf.Rect(a['rect']),u['shift']) for u in units for a in u.get('annotations',())]
            # Same target and kind; the area exactly where it was or moved to (0.02 pt: a translation rounds either way).
            def link_key(link,shift=(0.,0.)):
                r=pymupdf.Rect(link['from']);dx,dy=shift
                return (tuple(sorted((k,str(v)) for k,v in link.items() if k!='from')),round(r.x0+dx,2),round(r.y0+dy,2),round(r.x1+dx,2),round(r.y1+dy,2))
            expected=[];matched=0
            for link in original_paint['links']:
                shift=next((s for r,s in carried if max(abs(x-y) for x,y in zip(r,pymupdf.Rect(link['from'])))<.05),None)
                matched+=shift is not None;expected.append(link_key(link,shift or (0.,0.)))
            if not placed_move.same_paint(expected,[link_key(link) for link in staged_paint['links']]):
                raise Refused('staged links changed beyond the approved line decorations')
            # Every annotation as the PDF stores it (also the links a renderer
            # does not list): a moved one translated exactly, the others unchanged.
            if not annotations_moved(c.reader,PdfReader(tmp),spec['page'],{a['xref']:u['pdf_translation'] for u in units for a in u.get('annotations',())}):
                raise Refused('staged links changed beyond the approved line decorations')
        elif any(original_paint[k]!=staged_paint[k] for k in ('drawings','images','links','annots')):raise Refused('staged protected non-text paint changed')
        if any(staged_paint['fonts'].get(k)!=v for k,v in original_paint['fonts'].items()):raise Refused('staged original font program changed')
        # Structure checks that hold in the source must hold after the edit;
        # the structure tree is unchanged except the rewritten replacement text.
        before=semantic(c.source,spec['page'])['checks'];after=semantic(tmp,spec['page'])['checks']
        if any(before.get(k,True) and not v for k,v in after.items()):raise Refused('staged semantic structure/ActualText inconsistency')
        source_tree=semantics.canonical(c.reader);staged_tree=semantics.canonical(PdfReader(tmp))
        changed,other=semantics.changes(source_tree,staged_tree)
        allowed=set() if source_tree is None else {source_tree['paths'][x] for x in (actual_text or ()) if x in source_tree['paths']}
        if other or (actual_text is not None and set(changed)-allowed):raise Refused('staged structure tree changed beyond the replacement text')
        if any(not g['gid'] for g in added if g['unicode'].strip()):raise Refused('staged missing glyph')
        # A cheap publication gate complements later all-page, two-renderer
        # oracle audit. Mask only the approved automatically inferred frame.
        a=c.page.get_pixmap(dpi=144,alpha=False);b=check.page.get_pixmap(dpi=144,alpha=False)
        ia=Image.frombytes('RGB',(a.width,a.height),a.samples);ib=Image.frombytes('RGB',(b.width,b.height),b.samples)
        # Poppler too: its text positioning arithmetic differs from MuPDF's.
        for renderer,(ia,ib) in (('MuPDF',(ia,ib)),('Poppler',(_poppler(c.source,spec['page']),_poppler(tmp,spec['page'])))):
            diff=ImageChops.difference(ia,ib)
            # A moved placed object may reach past the frame where it already
            # did; its paint was matched exactly translated above.
            for x0,y0,x1,y1 in spec.get('frames',[spec['frame']])+[pymupdf.Rect(u[k])+(-1,-1,1,1) for u in units for k in ('old','new')]:
                ImageDraw.Draw(diff).rectangle((math.floor(x0*2),math.floor(y0*2),math.ceil(x1*2)-1,math.ceil(y1*2)-1),fill=(0,0,0))
            if diff.getbbox():raise Refused(f'staged raster changes outside approved inferred frame ({renderer})')
        # Native retained font resources/programs are cloned, never replaced.
        if len(check.document)!=len(c.document):raise Refused('page count changed')
    finally:check.close()


def _plain(obj,pages,depth=0):
    """A PDF object as plain data: a page by its index, other references resolved (to depth 8)."""
    from pypdf.generic import IndirectObject,DictionaryObject,ArrayObject,StreamObject
    import hashlib
    if isinstance(obj,IndirectObject):
        if obj.idnum in pages:return ('page',pages[obj.idnum])
        return ('ref',) if depth>8 else _plain(obj.get_object(),pages,depth+1)
    if isinstance(obj,DictionaryObject):
        out={str(k):_plain(v,pages,depth+1) for k,v in obj.items() if k not in ('/P','/Parent','/Rect','/QuadPoints')}
        if isinstance(obj,StreamObject):out['stream']=hashlib.sha256(obj.get_data()).hexdigest()
        return out
    if isinstance(obj,ArrayObject):return [_plain(v,pages,depth+1) for v in obj]
    if isinstance(obj,float):return round(obj,6)
    return str(obj)


def annotations_moved(source,staged,page,moved):
    """The page's annotations in ``staged`` are those of ``source``, each listed in
    ``moved`` (source object number: PDF translation) with /Rect and /QuadPoints
    translated (0.02 pt), every other one with them unchanged."""
    def listed(reader):
        pages={p.indirect_reference.idnum:i for i,p in enumerate(reader.pages)}
        return [(getattr(ref,'idnum',None),ref.get_object(),pages) for ref in (reader.pages[page-1].get('/Annots') or [])]
    a,b=listed(source),listed(staged)
    if len(a)!=len(b):return False
    for (num,x,pa),(_,y,pb) in zip(a,b):
        if _plain(x,pa)!=_plain(y,pb):return False
        dx,dy=moved.get(num,(0.,0.))
        tol=.02 if num in moved else 1e-6
        for key,shift in (('/Rect',(dx,dy,dx,dy)),('/QuadPoints',None)):
            u=[float(v) for v in x.get(key,[])];w=[float(v) for v in y.get(key,[])]
            shift=shift or [dx if i%2==0 else dy for i in range(len(u))]
            if len(u)!=len(w) or any(abs(p+q-r)>tol for p,q,r in zip(u,shift,w)):return False
    return True


def _poppler(path,page):
    """One page rendered by Poppler at 144 dpi (RGB)."""
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-r','144','-png','-singlefile',str(path),folder+'/page'],capture_output=True,check=True)
        with Image.open(folder+'/page.png') as image:return image.convert('RGB')


def store_conditions(c,draft,writer,page,spec,selected,target_count,delta,active,approval,kept=(),moved=None,carried=()):
    """Record the approved conditions of the edited paragraph by its drawn lines.

    The lines are observed in the staged PDF itself, with the same analysis the
    next proposal uses. If they cannot be separated from foreign glyphs, no
    record is stored and the next edit falls back to inference.
    """
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import page_rows
    from pdfeditor.marked_content import observe_marked_content
    state=spec['stored'];records=[r for n,r in enumerate(state['records']) if n!=state['target'] and n not in state['stale']]
    if delta:
        followers=[n for n,p in zip(state['followers'],active[1:]) if n is not None]
        records=[r for n,r in enumerate(state['records']) if n!=state['target'] and n not in state['stale'] and n not in followers]
        records+=[conditions.shifted(state['records'][n],delta) for n in followers]
    check=ContentPage(draft,spec['page'])
    try:
        # Glyphs of an inline placed object and of lines kept as drawn stay
        # where they were; they are lines of the edited paragraph too.
        fixed=set(active[0].fixed);stay=fixed|set(kept);remaining=Counter(glyph_key(c.actual[i]) for i in stay)
        moved=moved or {}
        foreign=Counter(glyph_key(g) for i,g in enumerate(c.actual) if i not in selected and i not in stay and i not in moved);added=[];placed=[]
        # Glyphs of a moved placed object are foreign to the edited paragraph, where they moved.
        shifted=Counter(moved_key(c.actual[i],moved[i]) for i in moved)
        for n,g in enumerate(check.actual):
            key=glyph_key(g)
            if remaining[key]:remaining[key]-=1;placed.append(n)
            elif foreign[key]:foreign[key]-=1
            elif shifted[moved_key(g)]:shifted[moved_key(g)]-=1
            else:added.append(n)
        target=set(added[:target_count-len(fixed)])|set(placed)
        analysis=_Analysis(draft,spec['page'])
        rows=page_rows(check,observe_marked_content(str(draft),spec['page']),analysis)[0]
        mine=[r for r in rows if set(r['glyph_ids'])<=target]
        covered={i for r in mine for i in r['glyph_ids']}
        inked={i for i in target if check.actual[i]['unicode'].strip()}
        record=None
        if mine and inked<=covered and all(not set(r['glyph_ids'])&target for r in rows if r not in mine):
            values=spec['values'];previous=state['records'][state['target']] if state['applied'] else None
            known=conditions.present(values,mine,[dict(o) for o in analysis.obstacles])
            record=conditions.make(check,mine,conditions=dict(values,frame=spec['frame']),provenance=spec['provenance'],origin=spec['origin'],
                                   approval=dict(count=(previous['approval']['count'] if previous else 0)+1,**(approval or {})),present=known,
                                   layout=spec.get('original'))
            records.append(record)
        # Following paragraphs this edit moved keep their rows as observed before
        # the move (auto_conditions.apply_moved); earlier such records stay only
        # while every glyph they name is drawn exactly where they name it.
        before=conditions.read_moved(c,spec['page'])
        kept_moved=[m for m in before if conditions.resolve_moved(check,m) is not None]
        # The source rows made only of glyphs this edit moved, translated.
        source_rows=page_rows(c,observe_marked_content(c.source,spec['page']),_Analysis(c.source,spec['page']))[0] if carried else []
        new_moved=[conditions.moved_entry(c,[r for r in source_rows if r['glyph_ids'] and set(r['glyph_ids'])<=ids],dy,dict(approval or {})) for ids,dy in carried]
        new_moved=[m for m in new_moved if m['rows']]
        named=lambda m:{tuple(g) for r in m['rows'] for g in r['glyphs']}
        kept_moved=[m for m in kept_moved if not any(named(m)&named(n) for n in new_moved)]
        if records or state['records'] or kept_moved or new_moved or before:conditions.attach(writer,page,records,spec['page'],moved=kept_moved+new_moved)
        summary=dict(written=record is not None,records_on_page=len(records),applied_before_edit=state['applied'],status_before_edit=state['status'],moved_paragraphs_recorded=len(kept_moved+new_moved),
                     reason=None if record is not None else 'edited paragraph rows are not separable from foreign glyphs')
        return dict(record=record,summary=summary)
    finally:check.close()


def verify_conditions(path,page,record=None):
    """The stored record must be readable and match the staged paragraph again."""
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import page_rows
    from pdfeditor.marked_content import observe_marked_content
    check=ContentPage(path,page)
    try:
        records,status=conditions.read(check,page)
        rows=page_rows(check,observe_marked_content(str(path),page),_Analysis(path,page))[0]
        mine=[r for r in records if r==record] if record is not None else records[-1:]
        if status!='present' or len(mine)!=1 or conditions.match(check,mine[0],rows) is None:
            raise Refused('stored approved conditions do not round-trip in the staged PDF')
    finally:check.close()


def store_flow(c,draft,writer,page,spec,selected,counts,approval):
    """Record the approved column flow by every drawn line of it after the edit.

    Unchanged paragraphs of the flow are found by their unchanged glyphs; the
    repainted ones by paint order. Records of single paragraphs inside the
    flow are replaced by the flow record.
    """
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import page_rows
    from pdfeditor.marked_content import observe_marked_content
    state=spec['stored'];flow=spec['flow']
    flow_ids={i for g in flow['paragraphs'] for r in g for i in r['glyph_ids']}
    check=ContentPage(draft,spec['page'])
    try:
        # Unchanged glyphs keep their identity; map them to their paragraph.
        index={}
        for p,g in enumerate(flow['paragraphs']):
            for r in g:
                for i in r['glyph_ids']:
                    if i not in selected:index[glyph_key(c.actual[i])]=p
        foreign=Counter(glyph_key(g) for i,g in enumerate(c.actual) if i not in selected);owner={};added=[]
        for n,g in enumerate(check.actual):
            key=glyph_key(g)
            if foreign[key]:
                foreign[key]-=1
                if key in index:owner[n]=index[key]
            else:added.append(n)
        position=0
        for k,count in enumerate(counts):
            for n in added[position:position+count]:owner[n]=flow['target']+k
            position+=count
        rows=page_rows(check,observe_marked_content(str(draft),spec['page']),_Analysis(draft,spec['page']))[0]
        mine=[];separable=True
        for r in rows:
            owners={owner.get(i) for i in r['glyph_ids']}
            if owners=={None}:continue
            if None in owners or len(owners)!=1:separable=False;break
            mine.append((r,owners.pop()))
        covered={i for r,_ in mine for i in r['glyph_ids']}
        separable=separable and all(i in covered for i in owner if check.actual[i]['unicode'].strip())
        # Reading order: paragraph order, then the region (column), then baseline.
        regions=flow['regions']
        from .auto_flow import region_of
        record=None
        if mine and separable:
            try:
                mine.sort(key=lambda m:(m[1],region_of(regions,m[0]),m[0]['baseline']))
                previous=state['records'][state['target']] if state['applied'] else None
                size=median(r['size'] for r,_ in mine)
                record=conditions.make_flow(check,mine,regions,dict(pitch=flow['pitch'],gap=flow['gap'],align=spec['align'],size=size),
                                            [dict(id=l['id'],status=l['status'],evidence=l.get('evidence')) for l in flow['links']],spec['provenance'],spec['origin'],
                                            dict(count=(previous['approval']['count'] if previous else 0)+1,**(approval or {})))
            except Refused:record=None
        records=[r for n,r in enumerate(state['records']) if n!=state['target'] and n not in state['stale'] and not (r.get('kind')!='flow' and conditions.overlaps(r,[x for g in flow['paragraphs'] for x in g]))]
        if record is not None:records.append(record)
        if records or state['records']:conditions.attach(writer,page,records,spec['page'])
        summary=dict(written=record is not None,kind='flow',records_on_page=len(records),applied_before_edit=state['applied'],status_before_edit=state['status'],
                     reason=None if record is not None else 'flow rows are not separable from foreign glyphs')
        return dict(record=record,summary=summary)
    finally:check.close()
