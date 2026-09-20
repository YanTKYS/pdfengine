"""Spacing integration on reviewed Word ranges; evidence, never guessed intent."""
import argparse
import json
from pathlib import Path

import pymupdf
from pypdf import PdfReader

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.backend import PdfError
from pdfeditor.editable import write_editable,edit_document,open_editable
from pdfeditor.paragraph import plan_paragraph
from pdfeditor.pdf_save import font_fingerprints
from pdfeditor.selection import make_selection,source_sha
from evaluations.anchors.evaluate import replay_gate
from evaluations.attributed.evaluate import font_mapping_audit,original_code_audit
from evaluations.backend.followup import independent_edit_audit
from evaluations.elements.evaluate import audit_render,bounds,extraction
from evaluations.realpdf.evaluate import drawings_fingerprint,image_fingerprints
from evaluations.story_flow.evaluate import annotation_fingerprint

ROOT=Path(__file__).resolve().parents[2];BASE=Path(__file__).resolve().parent


def write(path,value):path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
def read(path):return json.loads(path.read_text(encoding='utf-8'))


def observed(snapshot):
    evidence=snapshot['spacing']
    return dict(snapshot_sha256=snapshot['snapshot_sha256'],unicode_length=len(snapshot['text']),
        styles=[{k:s[k] for k in ('id','font_name','font_size','tracking','tracking_provenance')} for s in snapshot['styles']],
        tracking=evidence['tracking'],alignment_candidates=evidence['alignment_candidates'],
        lines=[{k:l[k] for k in ('index','tc','tw','distribution','glyph_count')} for l in evidence['lines']])


def paint_audit(before,after,page,report):
    ra,rb=PdfReader(before),PdfReader(after)
    aliases={f['resource'].lstrip('/') for f in report['fonts'].values()}
    with pymupdf.open(before) as a,pymupdf.open(after) as b:
        assert len(a)==len(b)
        for i in range(len(a)):
            assert drawings_fingerprint(a[i])==drawings_fingerprint(b[i])
            assert image_fingerprints(a[i])==image_fingerprints(b[i])
            assert annotation_fingerprint(ra,i+1)==annotation_fingerprint(rb,i+1)
            current=font_fingerprints(b,i)
            if i+1==page:current=[f for f in current if f[0][3] not in aliases]
            assert current==font_fingerprints(a,i)


def audit(before,after,report,directory,*,noop=False):
    directory.mkdir()
    page=report['selection']['page']
    visual=audit_render(before,after,page,directory,bounds(report['audit_bbox']),noop=noop)
    a,b=extraction(before,directory,'before'),extraction(after,directory,'after')
    text=independent_edit_audit(a,b,page,report['before'],report['composed_text'])
    if not text['passed']:raise ValueError('independent extraction disagrees with the edit')
    paint_audit(before,after,page,report)
    return dict(output_sha256=source_sha(after),independent_text=text,
        poppler_outside_changed_pixels=visual['poppler_diff_with_1pt_margin']['outside_changed_pixels'],
        poppler_all_changed_pixels=visual['poppler_diff']['all_changed_pixels'],
        mupdf_all_equal=visual['mupdf_page_pixels'],other_pages_equal=visual['outside_page_mupdf_pixels_equal'],
        fixed_paints_images_annotations_fonts_preserved=True,
        font_mapping=font_mapping_audit(after,report),original_codes=original_code_audit(before,report),
        mutation_record_count=len(report['mutation_map']))


def run(directory):
    engine={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    manifest={v['id']:v for v in read(ROOT/'evaluations/sources.json')}
    cases=[]
    for name in ('word_osaka_fire_notice','word_okinawa_procurement'):
        meta=manifest[name];source=ROOT/meta['path'];assert source_sha(source)==meta['sha256']
        sub=directory/name;sub.mkdir()
        if name=='word_osaka_fire_notice':
            selection=make_selection(source,1,glyph_ids=list(range(143,218)),explicit_width=480)
        else:selection=make_selection(source,2,glyph_ids=list(range(912,941)),explicit_width=468)
        p=inspect_paragraph(source,selection);write(sub/'snapshot.json',p)
        gate,_=replay_gate(source,selection,sub)
        item=dict(id=name,source_url=meta['source_url'],source_sha256=meta['sha256'],page=selection['page'],
            selection=selection,observation=observed(p),source_replay=dict(mupdf_all_equal=gate['mupdf_page_pixels'],
                poppler_all_changed_pixels=gate['poppler_diff']['all_changed_pixels']))
        if name=='word_osaka_fire_notice':
            assert len(p['styles'])==2 and p['spacing']['tracking']['provenance']=='unknown'
            candidate=next(s['id'] for s in p['styles'] if s['tracking_provenance']=='candidate')
            span=next(s for s in p['spans'] if s['style_id']==candidate)
            edits=[dict(start=span['start'],end=span['start']+1,text='新',style_id=candidate)]
            try:plan_paragraph(source,p,edits)
            except PdfError as exc:
                assert 'tracking candidate' in str(exc)
                item.update(status='safe_refusal',reason=str(exc),output_created=False,
                    cause='within-line Tc difference remains a separate style; nonzero logical tracking is unconfirmed')
            else:raise AssertionError('unconfirmed tracking was silently accepted')
        else:
            stages=[];current=source;model=None
            for n in range(4):
                out,sidecar=sub/f'v{n}.pdf',sub/f'v{n}.json'
                if n==0:
                    report=write_editable(source,out,sidecar,p,[],width=468,max_bottom=768,
                        fonts={s['id']:dict(path='C:/Windows/Fonts/msmincho.ttc',font_index=0) for s in p['styles']})
                else:
                    state=open_editable(current,model);assert state['status']=='restored'
                    text=state['state']['paragraph']['text']
                    # Preserve the numbered prefix and its distinct Arial space.
                    # Only the reviewed Japanese body has a single source style.
                    edits=([dict(start=4,end=len(text),text='内容を確認してください。')] if n==1 else
                           [dict(start=text.index('内容'),end=text.index('内容')+2,text='書類')] if n==2 else [])
                    report=edit_document(current,model,out,sidecar,edits)
                write(sub/f'v{n}-report.json',report)
                opened=open_editable(out,sidecar);assert opened['status']=='restored'
                assert opened['state']['paragraph']['text']==report['after']
                assert all(s['tracking']==0.0 for s in opened['state']['paragraph']['styles'])
                record=audit(current,out,report,sub/f'v{n}-audit',noop=n in (0,3))
                stages.append(dict(stage=n,status='passed',logical_unicode_restored=True,**record))
                current,model=out,sidecar
            item.update(status='passed',stages=stages,font_policy='explicit MS Mincho face 0 for new glyphs; original codes retained otherwise')
        cases.append(item);write(directory/'cases.json',cases)
        print(name,item['status'],flush=True)
    assert engine=={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    return dict(date='2026-09-20',cases=cases,engine_sha256=engine,runner_sha256=source_sha(Path(__file__)),
        environment=dict(pymupdf=pymupdf.VersionBind),
        scope='PR A spacing/style separation only; alignment rendering and confirmed nonzero tracking are not implemented',
        interpretation='synthetic Tw line fragmentation is fixed; the real Word inline Tc difference must remain split',
        provider_sha256=source_sha(Path('C:/Windows/Fonts/msmincho.ttc')))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True)
    write(directory/'summary.json',run(directory))
