"""One logical paragraph across caller-confirmed, existing physical regions.

No page creation, coordinate-derived continuation, or compound writer. Each
fragment uses the existing source-linked writer and retains its own insertion
context. Fragment text is a view of the story, never a new paragraph identity.
"""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import tempfile

import pymupdf
from uniseg.graphemecluster import grapheme_cluster_boundaries

from .attributed import EditUnit, digest
from .backend import PdfError
from .document_flow import _initial, _rebind, _reseal, _fonts
from .editable import edit_document, open_editable
from .elements import _close
from .logical_element import paragraph_from_snapshot
from .model import Rect
from .paragraph import ParagraphShaper, plan_paragraph
from .proof_session import proof_session
from .replay import ensure_destination
from .rich_layout import layout_attributed
from .selection import source_sha


LINE_KEYS = ('start','end','baseline','width','ascent','descent')
STYLE_KEYS = ('font_size','horizontal_scale','tracking','word_spacing','baseline_shift','observed_color')


def _style(binding):
    styles=binding['paragraph']['styles']
    families={re.sub(r'^[A-Z]{6}\+','',s['font_name']) for s in styles}
    if len(families)!=1 or any(not _close({k:s[k] for k in STYLE_KEYS},{k:styles[0][k] for k in STYLE_KEYS}) for s in styles):
        raise PdfError('story flow currently requires one homogeneous logical style')
    return {k:styles[0][k] for k in STYLE_KEYS}


def _boundaries(text):
    return [dict(offset=m.start(),end=m.end(),kind='hard_break',provenance='authored_unicode')
            for m in re.finditer(r'\r\n|\r|\n',text)]


def _entry(binding):
    lines=binding['physical_layout']['lines']
    return dict(binding=binding,owned_paints=[],extent=dict(
        last_baseline=lines[-1]['baseline'] if lines else binding['layout']['baseline'],provenance='physical_fragment'))


def _validate(source, value):
    state=deepcopy(value);checksum=state.pop('model_sha256',None)
    if state.get('schema')!='pdfengine-story-flow-1' or checksum!=digest(state):
        raise PdfError('story model version or checksum is invalid')
    state['model_sha256']=checksum
    if state['pdf_sha256']!=source_sha(source):raise PdfError('story PDF revision changed')
    logical=state['logical'];containers=state['containers'];chain=state['flow_chain'];fragments=state['fragments']
    if (not isinstance(logical['id'],str) or not logical['id'] or logical['kind']!='paragraph'
            or logical['boundaries']!=_boundaries(logical['text'])
            or logical['style_spans']!=([dict(start=0,end=len(logical['text']),style_id='body')] if logical['text'] else [])
            or logical['decoration_ranges']!=[] or logical['paragraph_boundaries']!=[]):
        raise PdfError('unsupported logical identity, style or boundary semantics')
    ids=chain['containers']
    if (not ids or len(ids)!=len(set(ids)) or set(ids)!=set(containers) or set(ids)!=set(fragments)
            or chain['provenance']!='explicitly_confirmed' or chain['policy']!='fill-fixed-regions-in-declared-order'
            or chain['overflow']!='reject' or chain['paragraph_id']!=logical['id']):
        raise PdfError('flow needs one explicitly confirmed, noncyclic chain covering its containers')
    font=state['font_recipe']
    if source_sha(font['path'])!=font['sha256']:raise PdfError('story supplied font changed')
    cursor=0;used=set();slots=set()
    graphemes={0,len(logical['text']),*grapheme_cluster_boundaries(logical['text'])}
    with pymupdf.open(source) as doc:
        if len(doc)!=state['page_count'] or set(state['pages'])!={str(i+1) for i in range(len(doc))}:
            raise PdfError('story does not create or remove pages')
        for key,policy in state['pages'].items():
            if policy['unselected_elements']!='fixed-to-page' or policy['header_inference']!='unknown':
                raise PdfError('page semantics must not be inferred from position')
            for region in policy['protected_regions']:
                box=Rect(*region['bounds'])
                if (region['role'] not in ('header','footer','fixed') or region['provenance']!='explicitly_confirmed'
                        or not all(math.isfinite(v) for v in box.tuple()) or box.width<=0 or box.height<=0
                        or not Rect(*doc[int(key)-1].rect).contains(box)):
                    raise PdfError('invalid explicitly protected page region')
        for ident in ids:
            c=containers[ident];f=fragments[ident];b=f['binding'];p=b['paragraph'];box=Rect(*c['bounds'])
            if (c['id']!=ident or c['role']!='paragraph-continuation' or c['sizing']!='fixed'
                    or c['provenance']!='explicitly_confirmed' or c['padding']!='unknown'
                    or c['paint_policy']!='fixed-to-page' or c['page']!=p['selection']['page']
                    or type(c['page']) is not int or not 1<=c['page']<=len(doc)
                    or not all(math.isfinite(v) for v in box.tuple()) or box.width<=0 or box.height<=0
                    or not Rect(*doc[c['page']-1].rect).contains(box)):
                raise PdfError('invalid fixed continuation container')
            if any(box.intersects(Rect(*r['bounds'])) for r in state['pages'][str(c['page'])]['protected_regions']):
                raise PdfError('continuation region intersects protected page content')
            for other in ids[:ids.index(ident)]:
                previous=containers[other]
                if previous['page']==c['page'] and box.intersects(Rect(*previous['bounds'])):
                    raise PdfError('continuation containers must be disjoint')
            if b['logical_element']['id']!=logical['id'] or b['logical_element']['contained_by']!=ident:
                raise PdfError('physical fragment cannot acquire another paragraph identity')
            result=open_editable(source,b)
            if result['status']!='restored':raise PdfError(result['reason'])
            if (b['anchors'] is not None or b['boundaries']!=_boundaries(p['text'])
                    or not _close(_style(b),state['style']) or b['layout']!=c['layout']
                    or b['layout']['first_line_indent']!=0 or b['layout']['width'] is None
                    or not box.contains(Rect(b['layout']['x'],b['layout']['baseline'],
                        b['layout']['x']+b['layout']['width'],b['layout']['max_bottom']))):
                raise PdfError('fragment style, layout or decoration policy differs from its contract')
            if any(r['behavior']!='fixed-to-page' or r['relation']=='decorates' for r in b['relations']):
                raise PdfError('semantic decoration ranges need a separate cross-container paint contract')
            expected_fonts={s['id']:font for s in p['styles']}
            if b['fonts']!=expected_fonts:raise PdfError('fragment must use the explicit story font recipe')
            a,z=f['range'];end=f['render_end']
            if (type(a) is not int or type(z) is not int or type(end) is not int or a!=cursor
                    or not a<=end<=z<=len(logical['text']) or not {a,z,end}<=graphemes or p['text']!=logical['text'][a:end]
                    or any(ch not in ' \r\n' for ch in logical['text'][end:z])):
                raise PdfError('fragment ranges do not partition the one logical Unicode sequence')
            cursor=z
            observed=b['element']['text_element']['observed_bounds']
            if observed and not box.contains(Rect(**observed),.002):raise PdfError('fragment glyphs exceed its confirmed region')
            for gid in p['selection']['glyph_ids']:
                key=(c['page'],gid)
                if key in used:raise PdfError('fragments cannot share source glyph occurrences')
                used.add(key)
            if not p['selection']['glyph_ids']:
                key=(c['page'],p['insertion_binding']['event']['byte_range'][0])
                if key in slots:raise PdfError('fragments cannot share insertion slots')
                slots.add(key)
        if cursor!=len(logical['text']):raise PdfError('story has unbound logical text')
    if state['physical_breaks']!=_physical_breaks(state):raise PdfError('physical continuation metadata disagrees with fragments')
    return state


def _physical_breaks(state):
    result=[];ids=state['flow_chain']['containers']
    for a,b in zip(ids,ids[1:]):
        f=state['fragments'][a];g=state['fragments'][b]
        if g['range'][0]==g['range'][1]:continue
        prefix='generated' if state['layout_provenance']=='generated-by-pdfengine' else 'observed'
        result.append(dict(offset=f['range'][1],before=a,after=b,paragraph_id=state['logical']['id'],
            kind=prefix+('_page_break' if state['containers'][a]['page']!=state['containers'][b]['page'] else '_container_break'),
            provenance=state['layout_provenance'],logical_break_inserted=False))
    return result


@proof_session
def confirm_story(source, containers, *, paragraph_id, chain, font, protected_regions):
    """Confirm ordered source fragments as one homogeneous logical paragraph.

    Each container supplies page, bounds, paragraph and explicit layout.
    Its source text is concatenated exactly, without inserting line/page breaks.
    The supplied full font is the explicit reflow provider for the entire story.
    """
    checksum=source_sha(source);font=dict(font,path=str(Path(font['path']).resolve()),sha256=source_sha(font['path']))
    values={};fragments={};cursor=0;text='';style=None;source_family=None
    if len(chain)!=len(set(chain)) or set(chain)!=set(containers):raise PdfError('explicit chain must name each container once')
    for ident in chain:
        spec=containers[ident]
        if set(spec)-{'page','bounds','paragraph','layout','paint_relations'}:raise PdfError('unsupported continuation policy')
        if set(spec['layout'])!={'x','baseline','width','max_bottom','min_line_height','first_line_indent'}:
            raise PdfError('continuation requires explicit width, position, bottom, leading and indent')
        if spec['page']!=spec['paragraph']['selection']['page']:raise PdfError('source fragment page mismatch')
        b=_initial(source,paragraph_id,ident,dict(spec,fonts={s['id']:font for s in spec['paragraph']['styles']}))['binding']
        # The caller supplies the reflow provider independently of the source subset.
        b['fonts']={s['id']:font for s in b['paragraph']['styles']};b['boundaries']=_boundaries(b['paragraph']['text']);b=_reseal(b)
        current=_style(b)
        family=re.sub(r'^[A-Z]{6}\+','',b['paragraph']['styles'][0]['font_name'])
        if source_family is None:source_family=family
        if family!=source_family:raise PdfError('source fragments have different font families')
        if style is None:style=current
        if not _close(style,current):raise PdfError('initial fragments do not share the confirmed logical style')
        text+=b['paragraph']['text'];end=len(text)
        fragments[ident]=dict(range=[cursor,end],render_end=end,binding=b);cursor=end
        values[ident]=dict(id=ident,page=spec['page'],bounds=list(spec['bounds']),layout=deepcopy(b['layout']),
            role='paragraph-continuation',sizing='fixed',paint_policy='fixed-to-page',padding='unknown',provenance='explicitly_confirmed')
    with pymupdf.open(source) as doc:count=len(doc)
    if set(protected_regions)-{str(i+1) for i in range(count)}:raise PdfError('unknown page in protected regions')
    state=dict(schema='pdfengine-story-flow-1',pdf_sha256=checksum,page_count=count,
        logical=dict(id=paragraph_id,kind='paragraph',text=text,boundaries=_boundaries(text),paragraph_boundaries=[],
            style_spans=[dict(start=0,end=len(text),style_id='body')] if text else [],decoration_ranges=[]),
        style=style,source_font_family=source_family,font_recipe=font,containers=values,fragments=fragments,
        flow_chain=dict(paragraph_id=paragraph_id,containers=list(chain),provenance='explicitly_confirmed',
                        policy='fill-fixed-regions-in-declared-order',overflow='reject'),
        pages={str(i+1):dict(unselected_elements='fixed-to-page',header_inference='unknown',
            protected_regions=[dict(r,provenance='explicitly_confirmed') for r in protected_regions.get(str(i+1),[])]) for i in range(count)},
        layout_provenance='caller_confirmed_source_partition',previous_model_sha256=None)
    state['physical_breaks']=_physical_breaks(state)
    return _validate(source,_reseal(state))


@proof_session
def open_story(source, model):
    try:
        value=model if isinstance(model,dict) else json.loads(Path(model).read_text(encoding='utf-8'))
        return dict(status='restored',state=_validate(source,value))
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError,PdfError) as exc:
        return dict(status='needs_confirmation',reason=str(exc),semantics='unknown')


def _restore(source, model):
    result=open_story(source,model)
    if result['status']!='restored':raise PdfError('story requires confirmation: '+result['reason'])
    return result['state']


def _edit_text(text, edits):
    boundaries={0,len(text),*grapheme_cluster_boundaries(text)};ordered=[]
    if not isinstance(edits,list):raise PdfError('story edits must use logical Unicode ranges')
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)-{'start','end','text'}:raise PdfError('story supports its confirmed homogeneous style only')
        a,z,s=(edit.get(k) for k in ('start','end','text'))
        if (type(a) is not int or type(z) is not int or a not in boundaries or z not in boundaries
                or not 0<=a<=z<=len(text) or not isinstance(s,str)):
            raise PdfError('story edits need ordered grapheme boundaries and Unicode')
        ordered.append((a,z,s))
    result='';cursor=0;previous=None
    for a,z,s in sorted(ordered):
        if a<cursor or a==previous:raise PdfError('story edits must not overlap')
        result+=text[cursor:a]+s;cursor=z;previous=a
    return result+text[cursor:]


def _replacement(binding,text):
    return [dict(start=0,end=len(binding['paragraph']['text']),text=text,style_id=binding['paragraph']['styles'][0]['id'])]


def _plan(source,state,edits):
    text=_edit_text(state['logical']['text'],edits);cursor=0;plans={}
    for ident in state['flow_chain']['containers']:
        c=state['containers'][ident];b=state['fragments'][ident]['binding'];remaining=text[cursor:]
        paragraph=paragraph_from_snapshot(source,b['paragraph']);shaper=None
        try:
            sid=b['paragraph']['styles'][0]['id']
            shaper=ParagraphShaper(paragraph,[EditUnit(ch,sid,None,sid) for ch in remaining],_fonts(b))
            layout=layout_attributed(remaining,shape=shaper.shape,**dict(c['layout'],max_bottom=None),
                empty_ascent=state['style']['font_size']*.8,empty_descent=state['style']['font_size']*.2)
            fitting=[line for line in layout.lines if line.baseline+line.descent<=c['layout']['max_bottom']+1e-6]
            if remaining and not fitting:raise PdfError('continuation container cannot hold its next complete line')
            if len(fitting)==len(layout.lines):end=cut=len(remaining)
            else:
                end=fitting[-1].end;cut=layout.lines[len(fitting)].start
                if cut>=len(remaining) or not remaining[:end].strip(' \r\n'):
                    raise PdfError('blank-only continuation needs a richer nonpainting line binding')
                if any(ch not in ' \r\n' for ch in remaining[end:cut]):raise PdfError('flow would elide non-whitespace Unicode')
            visible=remaining[:end]
            measured=plan_paragraph(source,b['paragraph'],_replacement(b,visible),fonts=_fonts(b),**c['layout'])
            expected=[{k:getattr(line,k) for k in LINE_KEYS} for line in fitting]
            if not _close(measured['lines'],expected):raise PdfError('fragment shaping differs from the global continuation plan')
            if any(line['baseline']-line['ascent']<c['bounds'][1]-.002 for line in expected):
                raise PdfError('planned line crosses the confirmed container top')
            plans[ident]=dict(range=[cursor,cursor+cut],render_end=cursor+end,text=visible,lines=expected)
            cursor+=cut
        finally:
            if shaper is not None:shaper.close()
            paragraph.close()
    if cursor!=len(text):raise PdfError('story exceeds all explicitly confirmed continuation containers')
    return dict(schema='pdfengine-story-plan-1',source_sha256=state['pdf_sha256'],model_sha256=state['model_sha256'],
        paragraph_id=state['logical']['id'],text=text,boundaries=_boundaries(text),fragments=plans,
        schedule=list(state['flow_chain']['containers']),paint_authority='none; fixed page paint',
        collision_status='not_certified; every fragment writer must pass its guards')


@proof_session
def plan_story(source,model,edits):return _plan(source,_restore(source,model),edits)


@proof_session
def edit_story(source,model,output,model_output,edits):
    initial=_restore(source,model);plan=_plan(source,initial,edits)
    output=ensure_destination(output,source);model_output=ensure_destination(model_output,source)
    if output==model_output:raise PdfError('story PDF and sidecar need distinct destinations')
    output.parent.mkdir(parents=True,exist_ok=True);steps=[];state=deepcopy(initial)
    with tempfile.TemporaryDirectory(prefix='.story-',dir=output.parent) as directory:
        root=Path(directory);current=Path(source)
        for n,ident in enumerate(plan['schedule']):
            b=state['fragments'][ident]['binding'];wanted=plan['fragments'][ident]
            pdf,sidecar=root/f'{n}.pdf',root/f'{n}.json'
            report=edit_document(current,b,pdf,sidecar,_replacement(b,wanted['text']),
                empty_style_id=b['paragraph']['styles'][0]['id'])
            result=open_editable(pdf,sidecar)
            if result['status']!='restored':raise PdfError(result['reason'])
            edited=result['state'];edited['fonts']={s['id']:state['font_recipe'] for s in edited['paragraph']['styles']}
            edited['boundaries']=_boundaries(edited['paragraph']['text']);edited=_reseal(edited)
            actual=[{k:line[k] for k in LINE_KEYS} for line in edited['physical_layout']['lines']]
            if not _close(actual,wanted['lines']) or edited['paragraph']['text']!=wanted['text']:
                raise PdfError('saved fragment differs from its precomputed line and Unicode plan')
            page=state['containers'][ident]['page']
            for other,f in state['fragments'].items():
                if other==ident:
                    f.update(binding=edited,range=wanted['range'],render_end=wanted['render_end'])
                else:
                    byte_edits=report['byte_edits'] if state['containers'][other]['page']==page else []
                    f['binding']=_rebind(current,pdf,_entry(f['binding']),byte_edits)['binding']
            steps.append(dict(container_id=ident,page=page,report=report));current=pdf
        if source_sha(source)!=initial['pdf_sha256']:raise PdfError('story input changed before publication')
        state['logical'].update(text=plan['text'],boundaries=plan['boundaries'],
            style_spans=[dict(start=0,end=len(plan['text']),style_id='body')] if plan['text'] else [])
        state.update(pdf_sha256=source_sha(current),previous_model_sha256=initial['model_sha256'],layout_provenance='generated-by-pdfengine')
        state['physical_breaks']=_physical_breaks(state);state=_validate(current,_reseal(state))
        saved=root/'story.json';saved.write_bytes((json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
        published=[]
        try:
            for temporary,destination in ((current,output),(saved,model_output)):
                destination.parent.mkdir(parents=True,exist_ok=True);os.link(temporary,destination);published.append(destination)
        except Exception:
            for destination in published:destination.unlink()
            raise
    return dict(backend='explicit-story-flow',plan=plan,steps=steps,pdf_sha256=state['pdf_sha256'],
        model_sha256=state['model_sha256'],logical_identity_verified=True,physical_partition_verified=True,
        publication='all fragment guards passed; rollback on exception')
