"""Canonical full-measurement design evidence. Runtime remains unmodified.

PDFs are produced only by existing runtime APIs. Candidate records and witness
comparison models never become runtime plans, sidecars or verifier decisions.
"""
from __future__ import annotations

import argparse
import hashlib
from copy import deepcopy
from fractions import Fraction as F
from io import BytesIO
import json
from pathlib import Path
import platform
import subprocess
import sys

import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.content_stream import ContentPage
from pdfeditor.backend import PdfError
from pdfeditor.editable import write_editable
from pdfeditor.selection import make_selection
from pdfeditor.shaped_font import ShapedFont
from evaluations.anchors.layout_observation import (ROOT, observe, reedit, accuracy, compare, digest,
                                                    ink_counterexample, source_case, write_summary as _write_summary)
from evaluations.anchors.paint_ownership import fixture
from evaluations.anchors.measurement_authority import (exact_source, measurement, layout, edit_record,
    resolve_edge, bounds_policy, verification_candidate, WITNESS_FIELDS, verdict)
from evaluations.continuation.source_slot_accumulation import dump, runtime_digest

BASE = '8f9bf6e9e5288b73046ff0c769fa337cde4c537e'


def synthetic_font():
    builder = FontBuilder(1000, isTTF=True)
    names = ['.notdef', 'A', 'B', 'space']
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({65:'A', 66:'B', 32:'space'})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        if name != 'space':
            bottom = -200 if name == 'B' else 0
            pen.moveTo((0,bottom)); pen.lineTo((500,bottom))
            pen.lineTo((500,600)); pen.lineTo((0,600)); pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({n:(600,0) for n in names})
    builder.setupHorizontalHeader(ascent=600, descent=-200)
    builder.setupNameTable(dict(familyName='MeasurementProof', styleName='Regular',
        uniqueFontIdentifier='MeasurementProof-v1', fullName='MeasurementProof', psName='MeasurementProof'))
    builder.setupOS2(sTypoAscender=600,sTypoDescender=-200,usWinAscent=600,usWinDescent=200,fsType=0)
    builder.setupPost(); builder.setupMaxp()
    builder.font['head'].created = builder.font['head'].modified = 2082844800
    builder.font.recalcTimestamp = False
    out = BytesIO(); builder.save(out)
    return out.getvalue()


def embedded_fixture(folder, scaled):
    asset = folder/'metric.ttf'; asset.write_bytes(synthetic_font())
    font = ShapedFont(asset)
    run = font.shape('AB AB')
    resource = font.resource([run])
    writer = PdfWriter()
    ref = resource.build(writer)
    data = b'BT /Regular 12 Tf 20 200 Td <' + b''.join(resource.code(g) for g in run.glyphs).hex().encode() + b'> Tj ET'
    if scaled: data = b'q .83 0 0 .91 7.25 11.5 cm '+data+b' Q'
    for i in range(2):
        page = writer.add_blank_page(320,260)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/Regular'):ref})})
        stream = DecodedStreamObject(); stream.set_data(data if not i else b'')
        page[NameObject('/Contents')] = writer._add_object(stream)
    source = folder/'source.pdf'; writer.write(source)
    p = inspect_paragraph(source,make_selection(source,glyph_ids=list(range(5)),explicit_width=50))
    return source,p,dict(fonts={s['id']:dict(path=str(asset)) for s in p['styles']},min_line_height=1,max_bottom=180)


def detailed_compare(a,b):
    result = compare(a,b)
    fields = ('start','end','semantic_style','advance','ink','ascent','descent')
    tuples = lambda stage: [{k:g[k] for k in fields} for g in stage['glyphs']]
    result['measurement_tuple_exact'] = tuples(a)==tuples(b)
    result['measurement_sha_before'],result['measurement_sha_after'] = digest(tuples(a)),digest(tuples(b))
    return result


def lifecycle(root, kind, scaled):
    name = kind+('_scaled' if scaled else '_identity')
    folder = root/name; folder.mkdir()
    if kind == 'embedded':
        source,p,opts = embedded_fixture(folder,scaled)
        edits = [dict(start=0,end=5,text='AB\nBA')]
        change = [dict(start=3,end=5,text='AB')]
        growth = [dict(start=1,end=1,text=' AB')]
        reflow_width = 15
    else:
        source,specs = fixture(folder,'ctm' if scaled else 'lifecycle')
        p,opts = specs[0]
        edits = [dict(start=4,end=7,text='FIVE SEVEN')]
        change = [dict(start=4,end=9,text='')]
        growth = [dict(start=5,end=5,text=' EXTRA')]
        reflow_width = 65
    pdf,model = folder/'first.pdf',folder/'first.json'
    report = write_editable(source,pdf,model,p,edits,**opts)
    dump(folder/'first-report.json',report)
    stages = {'first':observe(source,p,edits,opts['fonts'],report)}
    accuracies = {'first':accuracy(pdf,report)}
    previous,comparisons = 'first',{}
    sequence = [('noop'+str(i),[],{}) for i in range(1,4)]
    for stage,edit,options in [('change',change,{}),('growth',growth,{}),('reflow',[],{'width':reflow_width})]:
        sequence.append((stage,edit,options))
        sequence.extend((stage+'_noop'+str(i),[],{}) for i in range(1,4))
    for name,edits,options in sequence:
        state = json.loads(model.read_text(encoding='utf-8'))
        out,saved,report = reedit(folder,pdf,model,name,edits,options)
        stages[name] = observe(pdf,state['paragraph'],edits,state['fonts'],report)
        accuracies[name] = accuracy(out,report)
        if not edits and not options: comparisons[previous+'->'+name] = detailed_compare(stages[previous],stages[name])
        pdf,model,previous = out,saved,name
        print(folder.name,name,flush=True)
    dump(folder/'physical-observations.json',stages)
    return dict(comparisons=comparisons,accuracy=accuracies,
        stages={n:dict(text=s['text'],lines=s['lines'],physical_sha256=digest(s),
            bounds_sources=sorted({g['bounds_source'] for g in s['glyphs']}),
            # Entire per-glyph measurement vectors remain in ignored raw output.
            witnesses=s['glyphs'][:2]+s['glyphs'][-2:]) for n,s in stages.items()})


def near_boundary(root, scaled, width):
    sys.path.insert(0,str(ROOT/'tests'))
    from test_attributed import source_pdf
    folder = root/('boundary-'+('scaled-' if scaled else '')+width); folder.mkdir()
    data = b'BT /Regular 12 Tf 100 200 Td (AB) Tj ET'
    if scaled: data=b'q .83 0 0 .91 7.25 11.5 cm '+data+b' Q'
    source=source_pdf(folder,data)
    p=inspect_paragraph(source,make_selection(source,glyph_ids=[0,1],explicit_width=float(width)))
    pdf,model=folder/'first.pdf',folder/'first.json'
    try:
        report=write_editable(source,pdf,model,p,[],x=20,min_line_height=16)
        first=observe(source,p,[],{},report)
        state=json.loads(model.read_text(encoding='utf-8'))
        out,_,second=reedit(folder,pdf,model,'noop',[])
        noop=observe(pdf,state['paragraph'],[],{},second)
        dump(folder/'physical-observations.json',dict(first=first,noop=noop))
        return dict(status='saved',first=first,noop=noop,comparison=detailed_compare(first,noop),
            accuracy=[accuracy(pdf,report),accuracy(out,second)])
    except (PdfError,RuntimeError) as exc:
        # A refused boundary save is an observation, never a passing roundtrip.
        return dict(status='refused',reason=str(exc),first_published=pdf.exists(),noop_published=(folder/'noop.pdf').exists())


def metric_glyph(text,font):
    if text=='\n': return dict(text=text)
    g=font.shape(text).glyphs[0]
    return dict(text=text,font_identity=font.source_sha256,glyph_identity=str(g.gid),
        upem=str(font.upem),width=str(font.nominal_width(g.gid)),
        ascent=str(font.font['hhea'].ascent),descent=str(-font.font['hhea'].descent),
        x_offset='0',y_offset='0',ink=list(map(str,font.ink(g.gid))) if font.ink(g.gid) else None,
        empty_outline_verified=text==' ')


def candidate_record(font,scaled=False,text='AB AB'):
    sx,sy=(F('.83'),F('.91')) if scaled else (F(1),F(1))
    style=dict(font_size=str(12*sy),horizontal_scale=str(sx/sy),rise='0',tracking='0',
        word_spacing='0',spacing_intent='confirmed-inline')
    return dict(alignment='left',glyphs=[metric_glyph(c,font) for c in text],style=style,
        edges=[],x='20',baseline='60',width='50',leading='1',
        empty=dict(ascent=str(F(600,1000)*12*sy),descent=str(F(200,1000)*12*sy)))


def candidates(root):
    font=ShapedFont(synthetic_font())
    result={}
    for scaled in (False,True):
        record=candidate_record(font,scaled,'AB\nBA')
        changed=edit_record(record,3,5,[metric_glyph(c,font) for c in 'AB'])
        growth=edit_record(changed,1,1,[metric_glyph(c,font) for c in ' AB'])
        scenarios={'first':record,'change':changed,'growth':growth,'reflow':dict(growth,width='15')}
        values={}
        for name,r in scenarios.items():
            expected=layout(r)
            runs=[]
            for _ in range(3):
                p=subprocess.run([sys.executable,'-m','evaluations.anchors.measurement_authority'],input=json.dumps(r),
                    text=True,capture_output=True,check=True,cwd=ROOT)
                runs.append(json.loads(p.stdout))
            values[name]=dict(exact=all(v==expected for v in runs),input_sha256=digest(r),plan=expected)
            dump(root/('candidate-'+str(scaled)+'-'+name+'.json'),r)
        sx=F('.83') if scaled else F(1)
        r=candidate_record(font,scaled,'AB')
        boundary=F('14.4')*sx
        values['boundary']={str(w):layout(dict(r,width=str(w))) for w in (boundary-F('0.000001'),boundary,boundary+F('0.000001'))}
        values['empty_line']=layout(candidate_record(font,scaled,'A\n\nB'))
        values['rise_tracking_word']=layout(dict(candidate_record(font,scaled,'A B'),style=dict(record['style'],rise='-1',tracking='1/3',word_spacing='6/5')))
        result['scaled' if scaled else 'identity']=values
    return result


def operator_trials():
    cases={}
    for scaled in (False,True):
        for name,spacing,text in [('Tj','','(A B) Tj'),('Tc','.4 Tc','(A B) Tj'),('Tw','1.2 Tw','(A B) Tj'),
            ('Tz','80 Tz','(A B) Tj'),('Ts','2 Ts','(A B) Tj'),('TJ','','[(A ) -100 (B)] TJ'),
            ('Tm','','(A ) Tj 1 0 0 1 35.6 200 Tm (B) Tj')]:
            program=f'BT /F 12 Tf {spacing} 20 200 Td {text} ET'
            if scaled: program='q .83 0 0 .91 7.25 11.5 cm '+program+' Q'
            value=exact_source(program,{c:'600' for c in 'AB '})
            cases[name+('_scaled' if scaled else '')]=dict(program=program,**value)
    return cases


def positioning_trials():
    font=ShapedFont(synthetic_font())
    records={}
    for name in ('TJ','Tm'):
        r=candidate_record(font,False,'A B')
        e=resolve_edge(dict(left=1,right=2,delta='6/5',operator=name),confirmed=True)
        r['edges']=[e]
        records[name]=dict(confirmed=layout(r),
            insert_breaks_edge=layout(edit_record(r,2,2,[metric_glyph('A',font)])),
            delete_endpoint=layout(edit_record(r,1,2,[])),
            reflow_suppresses_line_end_edge=layout(dict(r,width='15')),
            policy='Explicitly confirmed adjacent-pair semantics; not inferred from source operator or distance')
    r=candidate_record(font,False,'A B')
    r['style']['word_spacing']='6/5'
    records['Tw']=dict(plan=layout(r),policy='Confirmed authored word spacing, retained as a logical style field')
    records['same_origins_different_intent']=([g['origin'] for g in records['Tw']['plan']['glyphs']]==
        [g['origin'] for g in records['TJ']['confirmed']['glyphs']]==
        [g['origin'] for g in records['Tm']['confirmed']['glyphs']])
    records['unresolved_refusal']='SOURCE_POSITIONING_INTENT_UNRESOLVED'
    return records


def style_roundtrip(root,scaled):
    sys.path.insert(0,str(ROOT/'tests'))
    from test_attributed import source_pdf
    folder=root/('style-scaled' if scaled else 'style-identity');folder.mkdir()
    data=b'BT /Regular 12 Tf .4 Tc 1.2 Tw 80 Tz 2 Ts 20 200 Td (A B) Tj ET'
    if scaled:data=b'q .83 0 0 .91 7.25 11.5 cm '+data+b' Q'
    source=source_pdf(folder,data)
    p=inspect_paragraph(source,make_selection(source,glyph_ids=[0,1,2],explicit_width=100))
    confirmation={s['id']:dict(tracking=.4*.8*(.83 if scaled else 1),baseline_shift=-2*(.91 if scaled else 1)) for s in p['styles']}
    pdf,model=folder/'first.pdf',folder/'first.json'
    report=write_editable(source,pdf,model,p,[],paragraph_style=confirmation)
    first=observe(source,p,[],{},report,confirmation=confirmation)
    stages={'first':first};comparisons={};accuracies={'first':accuracy(pdf,report)};previous='first'
    for i in range(1,4):
        name='noop'+str(i);state=json.loads(model.read_text(encoding='utf-8'))
        out,saved,report=reedit(folder,pdf,model,name,[])
        stages[name]=observe(pdf,state['paragraph'],[],{},report)
        comparisons[previous+'->'+name]=detailed_compare(stages[previous],stages[name])
        accuracies[name]=accuracy(out,report);pdf,model,previous=out,saved,name
    dump(folder/'physical-observations.json',stages)
    return dict(stages=stages,comparisons=comparisons,accuracy=accuracies,
        exact_source_style=exact_source(data.decode(),{c:'600' for c in 'A B'})['glyphs'][0]['style'])


def verification_trials():
    # Synthetic witness-data model, not authenticated extraction from a PDF.
    expected={k:'witness:'+k for k in WITNESS_FIELDS}
    exact=verification_candidate(expected,deepcopy(expected))
    mutations={}
    for k in WITNESS_FIELDS:
        current=dict(expected,**{k:'tampered'})
        mutations[k]=verification_candidate(expected,current)
    return dict(premise='Symbolic comparison of witness data, NOT a current-PDF binding verifier',
        exact=exact,mutations=mutations,missing=verification_candidate(expected,{}),
        name_only=verification_candidate(expected,{'font_name':'MeasurementProof'}))


def bounds_inventory(root):
    sys.path.insert(0,str(ROOT/'tests'))
    from test_attributed import source_pdf
    folder=root/'unknown-font';folder.mkdir()
    source=source_pdf(folder,b'BT /Regular 12 Tf 20 200 Td (AB) Tj ET')
    writer=PdfWriter(clone_from=source)
    writer.pages[0]['/Resources']['/Font']['/Regular'].get_object()[NameObject('/BaseFont')]=NameObject('/UnknownMetricProgram')
    unknown=folder/'unknown.pdf';writer.write(unknown)
    return read_bounds_inventory(root)


def read_bounds_inventory(root):
    source=root/'unknown-font'/'source.pdf'
    unknown=root/'unknown-font'/'unknown.pdf'
    result={}
    for label,pdf in [('embedded',root/'embedded_identity'/'source.pdf'),
                      ('base14',source),('unknown',unknown)]:
        c=ContentPage(pdf,1)
        try:
            event=c.events[0];codec=event.state.font
            data=c.document.extract_font(codec.xref)[3]
            result[label]=dict(basefont=codec.basefont,program_available=bool(data),
                width=event.chars[0].pdf_width,decision='font/code binding required' if data else 'FONT_METRIC_AUTHORITY_UNPROVEN')
        finally:c.close()
    result['supplied']=dict(asset_sha=hashlib.sha256((root/'embedded_identity'/'metric.ttf').read_bytes()).hexdigest(),
        decision=bounds_policy('supplied_asset',asset_bound=True),premise='asset binding is an explicit external precondition')
    result['afm_candidate']=dict(decision=bounds_policy('base14',afm_pinned=True),
        selected=False,reason='AFM can define canonical widths/bounds, but no program/renderer outline binding is proven here',
        specification='https://adobe-type-tools.github.io/font-tech-notes/pdfs/5004.AFM_Spec.pdf')
    return result


def font_witness_observation(pdf,model,asset):
    """Read selected current codes/programs and compare used glyph witnesses.

    Partial evidence only: not a verifier, admission decision, or producer of
    authoritative records. It deliberately leaves full intent binding open.
    """
    from fontTools.ttLib import TTFont
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    state=json.loads(model.read_text(encoding='utf-8'))
    selected=set(state['paragraph']['selection']['glyph_ids'])
    font=ShapedFont(asset)
    content=ContentPage(pdf,1)
    rows=[]
    try:
        for event in content.events:
            for char in event.chars:
                if not selected.intersection(char.source_orders): continue
                codec=event.state.font
                data=content.document.extract_font(codec.xref)[3]
                if not data:
                    rows.append(dict(code=char.code.hex(),bounds_class='base14' if codec.basefont=='/Courier' else 'unknown',
                        has_program=False,authority='FONT_METRIC_AUTHORITY_UNPROVEN'))
                    continue
                embedded=TTFont(BytesIO(data))
                try:
                    if codec.subtype!='/Type0':
                        rows.append(dict(code=char.code.hex(),bounds_class='embedded_outline',mapping='unsupported experiment mapping'))
                        continue
                    descendant=codec.resource['/DescendantFonts'][0].get_object()
                    mapping=descendant['/CIDToGIDMap']
                    cid=int.from_bytes(char.code,'big')
                    gid=cid if str(mapping)=='/Identity' else int.from_bytes(mapping.get_object().get_data()[2*cid:2*cid+2],'big')
                    name=embedded.getGlyphName(gid)
                    expected=font.shape(char.text).glyphs[0]
                    pen=DecomposingRecordingPen(embedded.getGlyphSet());embedded.getGlyphSet()[name].draw(pen)
                    hmtx=embedded['hmtx'][name][0]
                    upem=embedded['head'].unitsPerEm
                    descriptor=descendant['/FontDescriptor'].get_object()
                    rows.append(dict(code=char.code.hex(),unicode=char.text,cid=cid,mapped_gid=gid,
                        expected_gid=expected.gid,bounds_class='embedded_outline',
                        asset_sha=font.source_sha256,current_program_sha=hashlib.sha256(data).hexdigest(),
                        program_hash_equals_asset=hashlib.sha256(data).hexdigest()==font.source_sha256,
                        upem=upem,hhea=[embedded['hhea'].ascent,embedded['hhea'].descent],
                        width=char.pdf_width,hmtx=hmtx,
                        descriptor_metrics=[str(descriptor['/Ascent']),str(descriptor['/Descent'])],
                        descriptor_metrics_match=(F(str(descriptor['/Ascent']))==F(font.font['hhea'].ascent*1000,font.upem) and
                            F(str(descriptor['/Descent']))==F(font.font['hhea'].descent*1000,font.upem)),
                        upem_matches=upem==font.upem,hmtx_matches=hmtx==font.nominal_width(expected.gid),
                        width_equals_hmtx=F(str(char.pdf_width))==F(hmtx*1000,upem),
                        unicode_asset_gid_matches=expected.gid==gid,
                        outline_matches=pen.value==font.outline(expected.gid),
                        hhea_matches=(embedded['hhea'].ascent,embedded['hhea'].descent)==
                                     (font.font['hhea'].ascent,font.font['hhea'].descent)))
                finally: embedded.close()
    finally: content.close()
    return dict(premise='Read-only partial current glyph witnesses; no full binding/intent admission proof',glyphs=rows)


def evaluate(root, *, physical=None):
    before=runtime_digest()
    if physical is None:
        physical=physical_trials(root)
    logical=dict(full_measurement=candidates(root),source_operator_arithmetic=operator_trials(),positioning=positioning_trials())
    verification=verification_trials()
    verification['current_font_observation']={n:font_witness_observation(root/'embedded_identity'/f'{n}.pdf',
        root/'embedded_identity'/f'{n}.json',root/'embedded_identity'/'metric.ttf') for n in ('first','noop1')}
    verification['base14_observation']=font_witness_observation(root/'courier_identity'/'first.pdf',
        root/'courier_identity'/'first.json',root/'courier_identity'/'font.ttf')
    verification['bounds_inventory']=bounds_inventory(root) if not (root/'unknown-font').exists() else read_bounds_inventory(root)
    model_exact=all(c[n]['exact'] for c in logical['full_measurement'].values() for n in ('first','change','growth','reflow'))
    runtime_exact=all(c['geometry_exact'] and c['semantic_style_exact'] and c['measurement_tuple_exact']
                      for v in physical['lifecycle'].values() for c in v['comparisons'].values())
    obligations=dict(current_code_outline_samples=all(v['glyphs'] and all(all(g.get(k) is True for k in
        ('outline_matches','width_equals_hmtx','hhea_matches','unicode_asset_gid_matches',
         'upem_matches','hmtx_matches','descriptor_metrics_match')) for g in v['glyphs'])
        for v in verification['current_font_observation'].values()),
        full_font_association_contract=False,current_semantic_intent_witness=False)
    verification['open_contract_obligations']=obligations
    gates=dict(style_authority=dict(passed=model_exact,scope='preconfirmed exact style candidate only'),
        ink_vertical_authority=dict(passed=model_exact,scope='known synthetic TT metric program; Base14/unreadable refused'),
        font_binding=dict(passed=all(obligations[k] for k in ('current_code_outline_samples','full_font_association_contract')),
            reason='used-glyph samples and symbolic comparisons do not close complete font association/empty-style binding'),
        spacing_intent=dict(passed=obligations['current_semantic_intent_witness'],
            reason='Tw and confirmed TJ/Tm can produce identical glyph positions; current persisted intent witness contract is absent'),
        lifecycle_canonicality=dict(passed=model_exact and all(obligations.values()),
            legacy_runtime_exact=runtime_exact,scope='candidate current-only cycle unproven; unchanged runtime failure is diagnostic, not itself a design readiness criterion'))
    value=dict(base=BASE,runtime_sha256=before,python=platform.python_version(),pymupdf=pymupdf.VersionBind,
        physical_observation=physical,logical_authority_candidate=logical,verification_candidate=verification,
        gates=gates,verdict=verdict(gates))
    if runtime_digest()!=before: raise ValueError('runtime changed')
    return value


def physical_trials(root):
    physical=dict(lifecycle={kind+('_scaled' if scaled else '_identity'):lifecycle(root,kind,scaled)
                  for kind in ('courier','embedded') for scaled in (False,True)},
                  trace_ink=ink_counterexample(root))
    physical['boundary']={str(scaled)+':'+str(float(w)):near_boundary(root,scaled,str(float(w)))
        for scaled in (False,True) for w in ((F('14.4')*(F('.83') if scaled else 1))+d for d in (F('-0.000001'),F(0),F('.000001')))}
    reference=physical['boundary']['False:14.4']
    if reference['status']=='saved':
        midpoint=(reference['first']['lines'][0]['width']+reference['noop']['lines'][0]['width'])/2
        physical['boundary']['observed_noise_band']=near_boundary(root,False,str(midpoint))
        physical['boundary']['observed_noise_band']['width_selection']='midpoint of observed first/noop widths; diagnostic probe, not correction'
    physical['Tw_identity']=source_case(root,'Tw-loss',b'1.2 Tw',b'(A B C ) Tj')
    physical['style']={str(s):style_roundtrip(root,s) for s in (False,True)}
    return physical


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.work.mkdir(parents=True,exist_ok=False)
    write_summary(args.output,evaluate(args.work.resolve()))


def write_summary(path,value):
    """Keep comparisons plus representative metrics; complete rows stay raw."""
    value=deepcopy(value)
    for case in value['physical_observation']['lifecycle'].values():
        for stage in case['stages'].values():
            if 'witnesses' not in stage: continue
            rows=stage.pop('witnesses')
            stage['glyphs']=[{k:g[k] for k in ('start','text','provider','semantic_style',
                'origin','advance','ink','ascent','descent','bounds_source','Tc','Tw','Tz','Tf')}
                for g in (rows[0],rows[-1])]
    value['summary_sampling']='Lifecycle glyph rows sample first/last; physical_sha256 covers complete ignored per-stage rows'
    _write_summary(path,value)


if __name__=='__main__':main()
