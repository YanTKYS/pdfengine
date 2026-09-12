"""Revision-bound editing semantics, independent of physical PDF line layout.

The sidecar is a caller-trusted editing document, not proof of authorship.
Its checksum detects accidental damage; an exact PDF hash prevents stale
meaning from being applied after an external save. Missing/stale state only
returns observations and never silently proceeds with an edit.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
import re
from pathlib import Path
import tempfile

from pypdf import PdfReader

from .attributed import SourceParagraph, digest, inspect_paragraph
from .backend import PdfError
from .content_stream import ContentPage
from .elements import inspect_element, _close, _paint_value
from .paragraph import edit_paragraph
from .logical_element import paragraph_from_snapshot, bind_empty
from .replay import ensure_destination
from .selection import inspect_selection_source, make_selection, source_sha


def _seal(value):
    value=json.loads(json.dumps(value))
    value['model_sha256']=digest(value)
    return value


def _read(value):
    if not isinstance(value,dict):
        value=json.loads(Path(value).read_text(encoding='utf-8'))
    value=deepcopy(value)
    checksum=value.pop('model_sha256',None)
    if value.get('schema') not in ('pdfengine-editable-1','pdfengine-editable-2') or checksum!=digest(value):
        raise PdfError('editable model version or checksum is invalid')
    value['model_sha256']=checksum
    return value


def open_editable(source, model=None, *, page=1):
    """Restore semantics or explicitly return physical observations for review."""
    try:
        if model is None:
            raise PdfError('editable model is missing')
        state=_read(model)
        if state['pdf_sha256']!=source_sha(source):
            raise PdfError('PDF revision differs from the editable model')
        if PdfReader(source).is_encrypted:
            raise PdfError('plaintext editing sidecars are not supported for encrypted PDFs')
        snapshot=state['paragraph']
        if state['schema']=='pdfengine-editable-2':
            identity=state['logical_element']
            if (not isinstance(identity['id'],str) or not identity['id'] or identity['kind']!='paragraph'
                    or not isinstance(identity['contained_by'],str) or not identity['contained_by']
                    or identity['alignment']!=dict(value='left',provenance='generated_layout_policy')):
                raise PdfError('unsupported logical element identity or paragraph formatting')
        declared=state['boundaries']
        spans=[(m.start(),m.end()) for m in re.finditer(r'\r\n|\r|\n',snapshot['text'])]
        if ([(b['offset'],b['end']) for b in declared]!=spans or
                any(b['kind'] not in ('hard_break','paragraph_boundary') for b in declared)):
            raise PdfError('logical boundary declarations disagree with authored break spans')
        container=state['container'];layout=state['layout']
        if (container['kind']!='text_region' or container['sizing']!='fixed' or container['overflow']!='reject'
                or container['follows'] or container['region']!=dict(x=layout['x'],width=layout['width'],
                    first_baseline=layout['baseline'],bottom=layout['max_bottom'])):
            raise PdfError('unsupported or inconsistent persistent container relations')
        paragraph=paragraph_from_snapshot(source,snapshot)
        try:actual=paragraph.snapshot()
        finally:paragraph.close()
        if actual!=snapshot:
            raise PdfError('stored paragraph no longer matches physical PDF evidence')
        if state.get('element') is not None and inspect_element(source,state['element']['selection'],paragraph_snapshot=snapshot)!=state['element']:
            raise PdfError('stored paint bindings no longer match physical PDF evidence')
        return dict(status='restored',state=state,semantics='caller-confirmed; generated layout is not re-inferred')
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError,PdfError) as exc:
        return dict(status='needs_confirmation',reason=str(exc),semantics='unknown',
                    observation=inspect_selection_source(source,page))


def _bind_paragraph(source, report):
    """Bind writer-known Unicode offsets to verified output codes and glyphs."""
    plan=report['glyph_plan']
    if not plan:
        return bind_empty(source,report)
    content=ContentPage(source,report['selection']['page'])
    ids=[]
    try:
        for glyph in plan:
            matches=[i for i,actual in enumerate(content.actual)
                if actual['unicode']==glyph['unicode'] and actual['gid']==glyph['glyph_id']
                and _close(list(actual['origin']),glyph['origin'])]
            if len(matches)!=1:
                raise PdfError('output glyph binding is ambiguous')
            ids.append(matches[0])
    finally:
        content.close()
    if len(ids)!=len(set(ids)):
        raise PdfError('output glyph plan is not one-to-one')
    selection=make_selection(source,report['selection']['page'],glyph_ids=ids,
                             explicit_width=report['widths']['explicitly_supplied_width'])
    physical=SourceParagraph(source,selection)
    try:
        observed={u.source_index:u for u in physical.units}
        records=[None]*len(report['after'])
        for glyph,index in zip(plan,ids):
            unit=observed[index];style=physical.styles[unit.style_id]
            if (glyph['end']-glyph['start']!=1 or style.event.state.font.name!=glyph['font_resource']
                    or unit.char.code.hex()!=glyph['code']):
                raise PdfError('persistent glyph binding needs matching single-codepoint source codes')
            expected=next(s for s in report['styles'] if s['id']==glyph['style_id'])
            actual=style.export()
            if any(not _close(expected[k],actual[k]) for k in
                   ('font_size','horizontal_scale','tracking','word_spacing','baseline_shift','observed_color')):
                raise PdfError('normalized output cannot yet restore this logical style; persistence refused')
            records[glyph['start']]=dict(glyph_id=index,style_glyph_id=index)
        for offset,record in enumerate(records):
            if record is not None:
                continue
            if report['after'][offset] not in ' \r\n':
                raise PdfError('layout omitted non-whitespace logical text')
            choices=[(g,i) for g,i in zip(plan,ids) if g['style_id']==report['logical_styles'][offset]]
            if not choices:
                raise PdfError('non-painted logical style has no output glyph witness')
            _,index=min(choices,key=lambda pair:abs(pair[0]['start']-offset))
            records[offset]=dict(glyph_id=None,style_glyph_id=index)
        logical=dict(pdf_sha256=source_sha(source),text=report['after'],units=records,
                     text_provenance='explicitly_confirmed',binding_provenance='generated-by-pdfengine')
        snapshot=inspect_paragraph(source,selection,logical=logical)
        # Output style IDs may split when a retained resource and a new font
        # coexist. Keep supplied-font intent attached to their actual witnesses.
        style_inputs={observed[i].style_id:(g['style_id'] if g['provider']=='original' else g['provider'])
                      for g,i in zip(plan,ids)}
        return snapshot,style_inputs
    finally:
        physical.close()


def _unique_path(snapshot, paint):
    matches=[p['source_id'] for p in snapshot['paths'] if p['proof']['status']=='proven'
             and len(p['proof']['paints'])==1 and _close(_paint_value(p['proof']['paints'][0]),_paint_value(paint))]
    if len(matches)!=1:
        raise PdfError('generated or fixed paint has no unique source binding')
    return matches[0]


def _bind_relations(source, snapshot, report, previous_element, previous_anchors, previous_relations):
    if previous_element is None:
        return None,None,None
    element=inspect_element(source,snapshot['selection'],paragraph_snapshot=snapshot)
    old_paths={p['source_id']:p for p in previous_element['paths']}
    fixed=previous_anchors.get('fixed_relations',[]) if previous_anchors is not None else previous_relations
    relations=[]
    # With text-only mutations, the ordered path program remains intact.
    # Validate every path's operator and interpreted paint before using its
    # ordinal to distinguish identical repeated backgrounds. This is source
    # continuity, never nearest-bbox ownership or an inferred new relation.
    fixed_map=None
    if previous_anchors is None:
        old,new=previous_element['paths'],element['paths']
        def path_value(path):
            return dict(operator=path['source']['operator'],operation_sha256=path['source']['operation_sha256'],
                        status=path['proof']['status'],paints=[_paint_value(p) for p in path['proof'].get('paints',[])])
        if len(old)!=len(new) or not _close([path_value(p) for p in old],[path_value(p) for p in new]):
            raise PdfError('fixed paint source sequence changed during text-only editing')
        fixed_map={a['source_id']:b['source_id'] for a,b in zip(old,new)}
    for relation in fixed:
        paints=old_paths[relation['source_id']]['proof'].get('paints',[])
        if len(paints)!=1:
            raise PdfError('persistent fixed relation requires a single source-proven paint')
        relations.append(dict(relation,source_id=fixed_map[relation['source_id']] if fixed_map is not None
                              else _unique_path(element,paints[0])))
    if previous_anchors is None:
        return element,None,relations
    groups=[]
    for n,group in enumerate(report['anchors']['groups']):
        if group['output_range'] is None:
            continue
        first=min((old_paths[i] for i in group['source_ids']),key=lambda p:p['source']['merged_range'][0])
        template=first['proof']['paints'][0]
        ids=[]
        for segment in report['anchors']['segments']:
            if segment['group']!=n:
                continue
            r=segment['bounds'];x0,y0,x1,y1=(r[k] for k in ('x0','y0','x1','y1'))
            paint=deepcopy(template)
            paint['geometry']=[['m',x0,y0],['l',x1,y0],['l',x1,y1],['l',x0,y1],['h']]
            paint['bounds']=[x0,y0,x1,y1]
            ids.append(_unique_path(element,paint))
        if not ids:
            raise PdfError('nonempty decoration without paint needs a virtual style anchor')
        groups.append(dict(source_ids=ids,range=group['output_range'],
            start_affinity=group['start_affinity'],end_affinity=group['end_affinity']))
    anchors=dict(paragraph_sha256=snapshot['snapshot_sha256'],element_sha256=element['snapshot_sha256'],
                 underlines=groups,fixed_relations=relations)
    return element,anchors,None


def write_editable(source, output, model_output, snapshot, edits, *, fonts=None,
                   boundary_kinds=None, previous_state=None, empty_style_id=None, **options):
    """Publish an ordinary PDF and its bound editing model after both verify.

    Only current semantics are saved, never removed text or an undo history.
    The two-file publication rolls back our own links on an exception. A crash
    between links can leave a PDF without its sidecar: reopening then asks for
    confirmation and retains the ordinary PDF analysis route.
    """
    output=ensure_destination(output,source);model_output=ensure_destination(model_output,source)
    if PdfReader(source).is_encrypted:
        raise PdfError('plaintext editing sidecars are not supported for encrypted PDFs')
    removal=options.pop('removal_output',None)
    targets=[output,model_output]+([ensure_destination(removal,source)] if removal else [])
    if len(set(targets))!=len(targets):
        raise PdfError('PDF, editable model and removal output must be distinct')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.editable-',dir=output.parent) as directory:
        root=Path(directory).resolve()
        if root.parent!=output.parent.resolve():
            raise PdfError('temporary editing workspace is outside the output directory')
        pdf=root/'edited.pdf';removed=root/'removed.pdf'
        report=edit_paragraph(source,pdf,snapshot,edits,fonts=fonts,
                              removal_output=removed if removal else None,preserve_empty=True,
                              empty_style_id=empty_style_id,**options)
        paragraph,styles=_bind_paragraph(pdf,report)
        element,anchors,relations=_bind_relations(pdf,paragraph,report,options.get('element_snapshot'),
                                                 options.get('anchor_spec'),options.get('element_relations'))
        supplied={}
        for out_style,in_style in styles.items():
            spec=(fonts or {}).get(in_style)
            if spec is not None:
                if not isinstance(spec,dict):
                    raise PdfError('persistent supplied fonts need explicit file recipes')
                path=Path(spec['path']).resolve()
                checksum=source_sha(path)
                if in_style in report['fonts'] and checksum!=report['fonts'][in_style]['source_sha256']:
                    raise PdfError('supplied font changed while creating the editing model')
                supplied[out_style]=dict(spec,path=str(path),sha256=checksum)
        # New authored breaks default to hard breaks; confirmed paragraph
        # boundaries on retained Unicode positions survive subsequent edits.
        previous={b['offset']:b['kind'] for b in (previous_state or {}).get('boundaries',[])}
        declarations=boundary_kinds or {}
        boundaries=[]
        for match in re.finditer(r'\r\n|\r|\n',report['after']):
            i=match.start()
            kind=declarations.get(str(i),declarations.get(i,previous.get(report['logical_origins'][i],'hard_break')))
            if kind not in ('hard_break','paragraph_boundary'):
                raise PdfError('unknown confirmed logical boundary kind')
            boundaries.append(dict(offset=i,end=match.end(),kind=kind,provenance='explicitly_confirmed'))
        if any(int(i) not in [b['offset'] for b in boundaries] for i in declarations):
            raise PdfError('logical boundary must identify an authored CR or LF')
        layout=dict(width=report['widths']['explicitly_supplied_width'],x=report['x'],
            baseline=report['baseline'],
            first_line_indent=report['first_line_indent'],min_line_height=report['min_line_height'],
            max_bottom=options.get('max_bottom'))
        if layout['width'] is None:
            raise PdfError('persistent text region needs explicitly confirmed available width')
        provenance=dict((previous_state or {}).get('layout_provenance',{}))
        for key in layout:
            if options.get(key) is not None and (previous_state is None or layout[key]!=previous_state['layout'][key]):
                provenance[key]='explicitly_confirmed'
            elif key not in provenance:
                provenance[key]=('explicitly_confirmed' if key=='width' else
                                 'generated-by-pdfengine' if key=='min_line_height' else 'observed_source')
        state=_seal(dict(schema='pdfengine-editable-2',pdf_sha256=source_sha(pdf),paragraph=paragraph,
            logical_element=deepcopy((previous_state or {}).get('logical_element')) or dict(
                id='paragraph-1',kind='paragraph',contained_by='region-1',
                provenance='caller_confirmed_selection',alignment=dict(value='left',provenance='generated_layout_policy')),
            element=element,anchors=anchors,relations=relations,fonts=supplied,layout=layout,layout_provenance=provenance,
            boundaries=boundaries,physical_layout=dict(provenance='generated-by-pdfengine',lines=report['lines']),
            container=dict(kind='text_region',sizing='fixed',overflow='reject',padding=None,padding_provenance='unknown',
                           region=dict(x=layout['x'],width=layout['width'],first_baseline=layout['baseline'],bottom=layout['max_bottom']),
                           follows=[],follows_provenance='not_declared',ownership='only explicitly confirmed paint relations'),
            previous_model_sha256=(previous_state or {}).get('model_sha256')))
        restored=open_editable(pdf,state)
        if restored['status']!='restored':
            raise PdfError('new editable state did not verify: '+restored['reason'])
        state_file=root/'editable.json'
        state_file.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        sources=[pdf,state_file]+([removed] if removal else [])
        published=[]
        try:
            for temporary,target in zip(sources,targets):
                target.parent.mkdir(parents=True,exist_ok=True)
                os.link(temporary,target);published.append(target)
        except Exception:
            for target in published:target.unlink()
            raise
    report['editable_model_sha256']=state['model_sha256']
    report['semantic_state_verified']=True
    return report


def edit_document(source, model, output, model_output, edits, *, fonts=None, boundary_kinds=None, **overrides):
    restored=open_editable(source,model)
    if restored['status']!='restored':
        raise PdfError('semantic re-edit requires confirmation: '+restored['reason'])
    state=restored['state'];supplied={}
    for ident,spec in state['fonts'].items():
        if ident in (fonts or {}):continue
        try:checksum=source_sha(spec['path'])
        except OSError as exc:
            raise PdfError('saved supplied font is unavailable; explicitly supply a replacement') from exc
        if checksum!=spec['sha256']:
            raise PdfError('saved supplied-font recipe changed; explicitly supply a replacement')
        supplied[ident]={k:v for k,v in spec.items() if k!='sha256'}
    supplied.update(fonts or {})
    options=dict(state['layout'],element_snapshot=state['element'],anchor_spec=state['anchors'],
                 element_relations=state['relations'])
    options.update(overrides)
    return write_editable(source,output,model_output,state['paragraph'],edits,fonts=supplied,
                          boundary_kinds=boundary_kinds,previous_state=state,**options)
