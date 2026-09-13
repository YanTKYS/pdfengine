"""Logical attributed ranges, independent of destination PDF font aliases."""
from copy import deepcopy
import hashlib
from pathlib import Path
import re

from uniseg.graphemecluster import grapheme_cluster_boundaries

from .attributed import digest
from .backend import PdfError
from .destination_style import PROPERTIES,inline_properties
from .elements import _close
from .logical_element import paragraph_from_snapshot
from .selection import source_sha


def canonical_spans(ids):
    result=[]
    for offset, ident in enumerate(ids):
        if not result or result[-1]['style_id']!=ident:
            result.append(dict(start=offset,end=offset,style_id=ident))
        result[-1]['end']=offset+1
    return result


def style_ids(text, spans, registry):
    cursor=0;result=[];boundaries={0,len(text),*grapheme_cluster_boundaries(text)}
    for span in spans:
        a,z,ident=span['start'],span['end'],span['style_id']
        if (set(span)!={'start','end','style_id'} or type(a) is not int or type(z) is not int
                or a!=cursor or not a<z<=len(text) or ident not in registry or not {a,z}<=boundaries):
            raise PdfError('logical style spans must partition Unicode at grapheme boundaries')
        result.extend([ident]*(z-a));cursor=z
    if cursor!=len(text) or spans!=canonical_spans(result):
        raise PdfError('logical style spans must be complete and independent of physical breaks')
    return result


def physical_ids(snapshot):
    ids=[]
    for span in snapshot['spans']:
        if span['start']!=len(ids):raise PdfError('physical style spans are not contiguous')
        ids.extend([span['style_id']]*(span['end']-span['start']))
    if len(ids)!=len(snapshot['text']):raise PdfError('physical style mapping does not cover its text')
    return ids


def properties(entry):
    return {key:entry['attributes'][key]['value'] for key in PROPERTIES}


def render_styles(state):
    return {'logical:'+ident:properties(entry) for ident,entry in state['style_registry'].items()}


def providers(state):
    return {'logical:'+ident:entry['reflow_provider'] for ident,entry in state['style_registry'].items()}


def confirm_registry(source, fragments, definitions, assignments, typing_style_id):
    if (not isinstance(definitions,dict) or not definitions or typing_style_id not in definitions
            or not isinstance(assignments,dict) or set(assignments)!=set(fragments)
            or any(not isinstance(i,str) or not i or i.startswith('logical:') for i in definitions)):
        raise PdfError('explicit style definitions, source assignments and typing style are required')
    witnesses={i:[] for i in definitions};ids=[]
    for container,fragment in fragments.items():
        snapshot=fragment['binding']['paragraph'];mapping=assignments[container]
        if set(mapping)!={s['id'] for s in snapshot['styles']} or any(i not in definitions for i in mapping.values()):
            raise PdfError('every physical source style needs an explicitly confirmed logical assignment')
        paragraph=paragraph_from_snapshot(source,snapshot)
        try:
            for sid,ident in mapping.items():
                style=paragraph.styles[sid];data=paragraph.content.document.extract_font(style.event.state.font.xref)[3]
                witnesses[ident].append(dict(id=container+':'+sid,container=container,source_style_id=sid,
                    pdf_sha256=snapshot['selection']['source_sha256'],page=snapshot['selection']['page'],
                    snapshot_sha256=snapshot['snapshot_sha256'],properties=style.export(),
                    font_program_sha256=hashlib.sha256(data).hexdigest() if data else None,
                    paint_context_sha256=digest(style.event.report()),provenance='observed_source'))
        finally:paragraph.close()
        ids.extend(mapping[i] for i in physical_ids(snapshot))
        fragment['style_binding']=dict(pdf_sha256=source_sha(source),page=snapshot['selection']['page'],
            styles=deepcopy(mapping),provenance='caller_confirmed_source_mapping')
    registry={}
    for ident,definition in definitions.items():
        if (set(definition)-{'provider','provider_relation','language'} or not witnesses[ident]
                or definition['provider_relation'] not in ('confirmed_reflow_provider','substituted')):
            raise PdfError('logical style needs observed witnesses and an explicit provider relationship')
        observed=witnesses[ident];base=inline_properties(observed[0]['properties']);props=deepcopy(base)
        names={re.sub(r'^[A-Z]{6}\+','',w['properties']['font_name']) for w in observed}
        if len(names)!=1 or any(not _close({k:inline_properties(w['properties'])[k] for k in PROPERTIES if k!='font_name'},
                                         {k:base[k] for k in PROPERTIES if k!='font_name'}) for w in observed):
            raise PdfError('caller style assignment combines different observed inline attributes')
        props['font_name']=next(iter(names))
        font=definition['provider'];font=dict(font,path=str(Path(font['path']).resolve()),sha256=source_sha(font['path']))
        registry[ident]=dict(id=ident,identity_provenance='caller_confirmed_source_assignments',
            attributes={k:dict(value=v,provenance='observed_source',witnesses=[w['id'] for w in observed]) for k,v in props.items()},
            source_observations=observed,reflow_provider=font,provider_relation=definition['provider_relation'],
            traits=dict(weight='unknown',italic='unknown',script='unknown',
                        language=dict(value=definition['language'],provenance='explicitly_confirmed') if 'language' in definition else 'unknown'))
    return registry,canonical_spans(ids)


def validate_registry(state):
    registry=state['style_registry'];logical=state['logical']
    if state['reflow_font_policy']!='explicit_provider_for_each_logical_style_in_entire_story':
        raise PdfError('unsupported story font regeneration policy')
    if not registry or logical['typing_style_id'] not in registry:
        raise PdfError('story needs an independent confirmed typing style')
    if logical['typing_style_provenance']!='explicitly_confirmed':raise PdfError('typing style cannot be guessed')
    for ident,entry in registry.items():
        observed=entry['source_observations'];attrs=entry['attributes'];font=entry['reflow_provider']
        if (entry['id']!=ident or not observed or entry['identity_provenance']!='caller_confirmed_source_assignments'
                or set(attrs)!=set(PROPERTIES) or entry['provider_relation'] not in ('confirmed_reflow_provider','substituted')
                or source_sha(font['path'])!=font['sha256']):
            raise PdfError('logical style identity, provenance or supplied font changed')
        for key,attribute in attrs.items():
            if (attribute['provenance']!='observed_source' or attribute['witnesses']!=[w['id'] for w in observed]
                    or any(not _close(attribute['value'],re.sub(r'^[A-Z]{6}\+','',w['properties'][key])
                              if key=='font_name' else inline_properties(w['properties'])[key]) for w in observed)):
                raise PdfError('logical inline attribute disagrees with its source witnesses')
        if (entry['traits']['weight']!='unknown' or entry['traits']['italic']!='unknown' or entry['traits']['script']!='unknown'
                or any(w['provenance']!='observed_source' for w in observed)):
            raise PdfError('font traits or source provenance cannot be inferred from names')
    return style_ids(logical['text'],logical['style_spans'],registry)


def bind_fragment(state, ident, binding, wanted, *, generated):
    fragment=state['fragments'][ident];snapshot=binding['paragraph'];registry=state['style_registry']
    if snapshot['text']:
        expected=style_ids(wanted['text'],wanted['style_spans'],registry);mapping={}
        for local,logical in zip(physical_ids(snapshot),expected):
            if local in mapping and mapping[local]!=logical:
                raise PdfError('one physical font binding ambiguously represents different logical styles')
            mapping[local]=logical
    else:
        typing=state['logical']['typing_style_id'];mapping={'logical:'+typing:typing}
    binding['fonts']={local:deepcopy(registry[logical]['reflow_provider']) for local,logical in mapping.items()}
    fragment['style_binding']=dict(pdf_sha256=binding['pdf_sha256'],page=snapshot['selection']['page'],styles=mapping,
        provenance='generated_font_and_glyph_verified' if generated else 'caller_confirmed_source_mapping')


def validate_fragment(state, ident):
    fragment=state['fragments'][ident];binding=fragment['binding'];snapshot=binding['paragraph'];link=fragment['style_binding']
    registry=state['style_registry'];mapping=link['styles'];observed={s['id']:s for s in snapshot['styles']}
    if (link['pdf_sha256']!=state['pdf_sha256'] or link['page']!=snapshot['selection']['page']
            or link['provenance'] not in ('caller_confirmed_source_mapping','generated_font_and_glyph_verified')
            or not mapping or set(mapping)-set(observed) or set(mapping.values())-set(registry)):
        raise PdfError('destination style binding has no current source evidence')
    for local,logical in mapping.items():
        actual=inline_properties(observed[local]);expected=properties(registry[logical])
        if not _close({k:actual[k] for k in PROPERTIES if k!='font_name'},
                      {k:expected[k] for k in PROPERTIES if k!='font_name'}):
            raise PdfError('destination inline attributes differ from the logical style')
    if binding['fonts']!={local:registry[logical]['reflow_provider'] for local,logical in mapping.items()}:
        raise PdfError('destination provider recipe differs from its logical style')
    a,_=fragment['range'];z=fragment['render_end']
    all_ids=style_ids(state['logical']['text'],state['logical']['style_spans'],registry)
    if [mapping[i] for i in physical_ids(snapshot)]!=all_ids[a:z]:
        raise PdfError('physical style bindings disagree with the global attributed range')
    if not snapshot['text'] and snapshot['typing_style_id']!='logical:'+state['logical']['typing_style_id']:
        raise PdfError('empty fragment lost its confirmed typing style')


def project(state, edits, typing_style_id=None):
    logical=state['logical'];text=logical['text'];registry=state['style_registry']
    original=style_ids(text,logical['style_spans'],registry);boundaries={0,len(text),*grapheme_cluster_boundaries(text)}
    typing=logical['typing_style_id'] if typing_style_id is None else typing_style_id
    if typing not in registry:raise PdfError('unknown confirmed typing style')
    if not isinstance(edits,list):raise PdfError('story edits must use logical ranges')
    ordered=[]
    for edit in edits:
        if not isinstance(edit,dict) or set(edit)-{'start','end','text','style_id','runs'}:
            raise PdfError('unsupported attributed story edit')
        a,z=edit.get('start'),edit.get('end')
        if type(a) is not int or type(z) is not int or not 0<=a<=z<=len(text) or not {a,z}<=boundaries:
            raise PdfError('story edits require complete grapheme ranges')
        if 'runs' in edit:
            if 'text' in edit or 'style_id' in edit or not isinstance(edit['runs'],list):raise PdfError('ambiguous replacement runs')
            runs=edit['runs']
        else:
            value=edit.get('text')
            if not isinstance(value,str):raise PdfError('replacement must be Unicode')
            ident=edit.get('style_id')
            if ident is None:
                choices=set(original[a:z]) if a<z else set(original[max(0,a-1):min(len(text),a+1)])
                if not value:ident=typing
                elif len(choices)>1:raise PdfError('cross-style replacement or insertion needs explicit style_id or runs')
                else:ident=next(iter(choices)) if choices else typing
            runs=[dict(text=value,style_id=ident)]
        value='';ids=[]
        for run in runs:
            if (not isinstance(run,dict) or set(run)!={'text','style_id'} or not isinstance(run['text'],str)
                    or run['style_id'] not in registry):raise PdfError('each story replacement run needs an explicit known style')
            value+=run['text'];ids.extend([run['style_id']]*len(run['text']))
        ordered.append((a,z,value,ids))
    result='';ids=[];cursor=0;previous=None
    for a,z,value,replacement_ids in sorted(ordered,key=lambda e:(e[0],e[1])):
        if a<cursor or a==previous:raise PdfError('story edits must be disjoint')
        result+=text[cursor:a]+value;ids.extend(original[cursor:a]+replacement_ids);cursor=z;previous=a
    result+=text[cursor:];ids.extend(original[cursor:]);spans=canonical_spans(ids)
    style_ids(result,spans,registry)
    return result,spans,typing


def replacement(binding, text, spans):
    return [dict(start=0,end=len(binding['paragraph']['text']),runs=[
        dict(text=text[s['start']:s['end']],style_id='logical:'+s['style_id']) for s in spans])]
