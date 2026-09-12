"""External FCC simultaneous edits within its unchanged confirmed note frame.

Reuse the completed source replay/lifecycle evidence. No new permission is
derived from document names, paint sequence numbers or a successful raster.
Raw PDFs, models, glyph reports and images remain in ignored runs/.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import pymupdf

from pdfeditor.backend import PdfError
from pdfeditor.document_flow import _reseal, edit_flow, open_document
from pdfeditor.elements import _close, _paint_value
from pdfeditor.flow_transaction import edit_flow_batch, plan_flow
from pdfeditor.model import Rect
from pdfeditor.paint_provenance import interpreted_paints
from pdfeditor.selection import source_sha
from evaluations.attributed.evaluate import font_mapping_audit
from evaluations.document_flow.evaluate import LONG
from evaluations.elements.evaluate import audit_render, extraction
from evaluations.realpdf.evaluate import image_fingerprints


ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent
SHORT = 'Check PDF 2026.'
B_LONG = 'Example : PDF 2026\nConfirm the font.\nCheck the saved file.'
B_SHORT = 'Example : PDF 2026'
SOURCE_SHA = '11ad813fce341afed902f98f7c5335f346bd9e746c648a509c1c7f9ba4f7f3fd'
SEED_SHA = '207b4e68a3e2c25cb493917e4531c23f5385edf4ceb445aed236fe8ee6b0981d'


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path, value): path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))
def normalize(value): return ''.join(value.split())


def changes(model, texts):
    return {i: dict(edits=[] if text is None else [dict(start=0,
                    end=len(model['elements'][i]['binding']['paragraph']['text']), text=text, style_id='s0')],
                    empty_style_id='s0') for i, text in texts.items()}


def restored(pdf, model):
    value = open_document(pdf, model)
    if value['status'] != 'restored': raise ValueError(value['reason'])
    return value['state']


def refuse(directory, name, operation):
    output, sidecar = directory/f'{name}.pdf', directory/f'{name}.json'
    try: operation(output, sidecar)
    except (PdfError, ValueError) as exc:
        if output.exists() or sidecar.exists(): raise ValueError('refused batch was published')
        return dict(case=name, status='safely_refused', reason=str(exc), output_created=False)
    raise ValueError('negative control unexpectedly succeeded: '+name)


def audit(before, after, state, report, directory, prefix, suffix, *, noop=False):
    directory.mkdir()
    area = None
    for transaction in report['transactions']:
        for step in transaction['report']['steps']:
            box = Rect(**step['report']['audit_bbox'])
            area = box if area is None else area.union(box)
            if step['kind'] == 'edit': font_mapping_audit(after, step['report'])
    visual = audit_render(before, after, 1, directory/'render', area.tuple(), noop=noop)
    text = extraction(after, directory, 'after')
    # Independently reviewed A then B are a unique contiguous interval in this
    # source's reading order. Preserve the entire normalized prefix/suffix,
    # including when BOTH paragraphs have no extracted glyphs. No substring
    # absence shortcut or acceptance of duplicate old text is used.
    expected = prefix + ''.join(normalize(state['elements'][i]['binding']['paragraph']['text']) for i in ('A','B')) + suffix
    if len(text['pages']) != 1 or normalize(text['pages'][0]) != expected:
        raise ValueError('independent complete-page extraction differs from both intended replacements')
    def nontext(path):
        observed = interpreted_paints(path,1)
        if observed['errors']: raise ValueError(observed['errors'][0])
        return [_paint_value(p) for p in observed['events'] if p['kind'] not in ('fill-text','stroke-text','ignore-text')]
    if not _close(nontext(before), nontext(after)): raise ValueError('fixed nontext paint changed')
    with pymupdf.open(before) as a, pymupdf.open(after) as b:
        if any(image_fingerprints(a[i]) != image_fingerprints(b[i]) for i in range(len(a))):
            raise ValueError('image content changed')
    return dict(poppler_outside_changed_pixels=visual['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
                mupdf_outside_pixels_equal=True, independent_complete_page_text=True, font_cid_gid_widths=True,
                fixed_nontext_paints_equal=True, images_equal=True, other_pages='none in this one-page source',
                noop_poppler_all_changed_pixels=visual['poppler_diff']['all_changed_pixels'] if noop else None,
                noop_mupdf_all_pixels_equal=visual['mupdf_page_pixels'] if noop else None)


def run(lifecycle, directory):
    prior = read(ROOT/'evaluations/ink_collision/summary.json')
    source = ROOT/'evaluations/realpdf/corpus/print_fcc_ms.pdf'
    if source_sha(source) != SOURCE_SHA or prior['source_sha256'] != SOURCE_SHA:
        raise ValueError('reviewed external source changed')
    engine = {p.name: source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    if any(engine.get(k) != v for k,v in prior['final_environment']['engine_sha256'].items()):
        raise ValueError('an existing backend changed; reassess evidence reuse before this evaluation')
    pdf = lifecycle/'g1.pdf'
    if source_sha(pdf) != SEED_SHA: raise ValueError('verified external g1 seed changed')
    original_model = read(lifecycle/'g1.json')
    state = restored(pdf, original_model)
    source_text = extraction(pdf, directory, 'seed')
    page = normalize(source_text['pages'][0])
    old = ''.join(normalize(state['elements'][i]['binding']['paragraph']['text']) for i in ('A','B'))
    if page.count(old) != 1: raise ValueError('reviewed two-paragraph reading-order interval is not unique')
    prefix, suffix = page.split(old)
    paths = state['elements']['A']['binding']['element']['paths']
    note = [p for p in paths if p['proof'].get('paint_seqnos') in [[37],[38]]]
    geometry = [dict(paint_seqno=p['proof']['paint_seqnos'][0],
                     role='shared fixed background' if p['proof']['paint_seqnos']==[37] else 'fixed frame',
                     subpath_count=sum(c[0]=='m' for c in p['proof']['paints'][0]['geometry']),
                     cubic_count=sum(c[0]=='c' for c in p['proof']['paints'][0]['geometry'])) for p in note]
    if len(geometry)!=2: raise ValueError('reviewed note paints changed')
    negatives = [refuse(directory,'isolated_b_growth',lambda o,s: edit_flow(pdf,state,o,s,'B',changes(state,{'B':B_LONG})['B']['edits']))]
    stages = []
    for n, texts in enumerate([{'B':B_LONG,'A':SHORT}, {'A':LONG,'B':B_SHORT},
                               {'A':'','B':''}, {'B':B_LONG,'A':SHORT}, {'A':None,'B':None}],1):
        if n == 2:
            negatives.append(refuse(directory,'isolated_a_growth',lambda o,s: edit_flow(
                pdf,state,o,s,'A',changes(state,{'A':LONG})['A']['edits'])))
        request = changes(state,texts)
        planned = plan_flow(pdf,state,request)
        out, sidecar = directory/f'b{n}.pdf', directory/f'b{n}.json'
        write(directory/'progress.json',dict(stage=f'b{n}',target=out.name))
        report = edit_flow_batch(pdf,state,out,sidecar,request)
        write(directory/f'b{n}-report.json',report)
        if report['plan'] != planned: raise ValueError('preview and execution plans differ')
        next_state = restored(out,sidecar)
        if next_state['follows'] != state['follows'] or next_state['container'] != state['container']:
            raise ValueError('semantic or fixed-region policy changed')
        proof = audit(pdf,out,next_state,report,directory/f'b{n}-audit',prefix,suffix,noop=n==5)
        record = dict(stage=f'b{n}',status='passed',requested_order=list(texts),schedule=planned['schedule'],
                      final_positions=planned['final_positions'],extent_deltas=planned['extent_deltas'],
                      empty_elements=[i for i,e in next_state['elements'].items() if not e['binding']['paragraph']['text']],
                      source_mutations=[dict(kind=s['kind'],element_id=s['element_id']) for t in report['transactions'] for s in t['report']['steps']],
                      output_sha256=source_sha(out),**proof)
        stages.append(record); write(directory/'stages.json',stages)
        print(json.dumps(record),flush=True)
        pdf,state = out,next_state
    negatives.append(refuse(directory,'final_overflow',lambda o,s: edit_flow_batch(pdf,state,o,s,changes(state,{'A':LONG,'B':B_LONG}))))
    no_relation = deepcopy(state); no_relation['follows']=[]; no_relation=_reseal(no_relation)
    # Without the edge A's growth reaches B. No permission is inferred from
    # shared yellow background or from the successful related transaction.
    negatives.append(refuse(directory,'missing_relation',lambda o,s: edit_flow_batch(pdf,no_relation,o,s,changes(no_relation,{'A':LONG,'B':B_SHORT}))))
    negatives.append(refuse(directory,'resize_policy_not_supported',lambda o,s: edit_flow_batch(pdf,state,o,s,
        {'A':dict(changes(state,{'A':LONG})['A'],resize='fit-content')})))
    from pypdf import PdfReader,PdfWriter
    writer=PdfWriter(clone_from=PdfReader(pdf));writer.add_metadata({'/Title':'external save'})
    external=directory/'external.pdf';writer.write(external)
    negatives.append(refuse(directory,'external_save',lambda o,s: edit_flow_batch(external,state,o,s,changes(state,{'A':SHORT,'B':B_SHORT}))))
    return dict(schema='pdfengine-flow-transaction-evaluation-1',status='passed',source_url=prior['source_url'],
                source_sha256=SOURCE_SHA,seed_sha256=SEED_SHA,source_replay=prior['source_replay'],
                reused_evidence='original A/B no-op and verified g1 lineage from ink_collision; existing backend files unchanged',
                font_sha256=prior['font_sha256'],frame_assessment=geometry,stages=stages,negative_controls=negatives,
                environment=dict(engine_sha256=engine,runner_sha256=source_sha(Path(__file__)),pymupdf=pymupdf.VersionBind),
                scope='two explicit paragraph edits; unchanged fixed shared frame; staged guarded source operators; no resize or general solver')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--lifecycle',required=True,type=Path)
    parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True,exist_ok=False)
    result=run(args.lifecycle,directory);write(directory/'summary.json',result)


if __name__=='__main__':main()
