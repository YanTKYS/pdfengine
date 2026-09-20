"""PR B: explicit tracking witnesses on a reviewed external Word text span."""
import argparse
from pathlib import Path

import pymupdf

from pdfeditor.attributed import inspect_paragraph, SourceParagraph
from pdfeditor.backend import PdfError
from pdfeditor.editable import write_editable, edit_document, open_editable
from pdfeditor.paragraph import plan_paragraph
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.style_confirmation import observed_values
from evaluations.anchors.evaluate import replay_gate
from evaluations.spacing.evaluate import audit, read, write

ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent


def run(directory):
    engine={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    meta=next(c for c in read(ROOT/'evaluations/sources.json') if c['id']=='word_osaka_fire_notice')
    source=ROOT/meta['path'];assert source_sha(source)==meta['sha256']
    # Review the complete first line, including the two nonzero-Tc glyphs at
    # offsets 39..41. Its right margin and width are supplied by the evaluator.
    selection=make_selection(source,1,glyph_ids=list(range(143,190)),explicit_width=488)
    snapshot=inspect_paragraph(source,selection)
    write(directory/'source-snapshot.json',snapshot)
    assert len(snapshot['styles'])==2 and snapshot['styles'][1]['tracking_provenance']=='candidate'
    assert snapshot['spacing']['tracking']['provenance']=='unknown'
    expected=dict(tracking=-.12,baseline_shift=0.0)
    gate,_=replay_gate(source,selection,directory)
    refused={}
    for name,reviewed in [('selected_run',snapshot),('mixed_paragraph',inspect_paragraph(source,
            make_selection(source,1,glyph_ids=list(range(143,218)),explicit_width=480)))]:
        offset=39
        try:plan_paragraph(source,reviewed,[dict(start=offset,end=offset+1,text='新')])
        except PdfError as exc:
            assert 'tracking candidate' in str(exc);refused[name]=str(exc)
        else:raise AssertionError('unconfirmed candidate was accepted')
    font=Path('C:/Windows/Fonts/msgothic.ttc')
    current=source;model=None;stages=[]
    for n in range(4):
        output,sidecar=directory/f'v{n}.pdf',directory/f'v{n}.json'
        if n==0:
            report=write_editable(source,output,sidecar,snapshot,[],
                paragraph_style={'s1':expected},fonts={'s1':dict(path=str(font),font_index=0)},
                width=488,max_bottom=300,min_line_height=14.784)
        else:
            edits=[] if n==3 else [dict(start=39,end=41,text='資料' if n==1 else '文書')]
            report=edit_document(current,model,output,sidecar,edits)
        write(directory/f'v{n}-report.json',report)
        restored=open_editable(output,sidecar);assert restored['status']=='restored'
        p=restored['state']['paragraph'];assert p['text']==report['after']
        confirmed_styles={span['style_id'] for span in p['spans'] if span['start']<=39<span['end']}
        for style in p['styles']:
            if style['id'] not in confirmed_styles:
                assert style['tracking']==0 and style['tracking_provenance']=='observed_source'
                continue
            for key,value in expected.items():
                assert abs(style[key]-value)<.001 and style[key+'_provenance']=='explicitly_confirmed'
        physical=SourceParagraph(output,p['selection'])
        try:
            target_ids={p['logical']['units'][i]['glyph_id'] for i in (39,40)}
            witnessed=[observed_values(u) for u in physical.units if u.source_index in target_ids]
            assert witnessed and all(all(abs(v[k]-expected[k])<.001 for k in expected) for v in witnessed)
            # PDF-only observation remains a candidate; only the verified
            # sidecar restores explicit confirmation.
            assert all(physical.styles[u.style_id].tracking is None and physical.styles[u.style_id].tracking_provenance=='candidate'
                       for u in physical.units if u.source_index in target_ids)
        finally:physical.close()
        evidence=audit(current,output,report,directory/f'v{n}-audit',noop=n in (0,3))
        stages.append(dict(stage=n,status='passed',logical_unicode_restored=True,
            confirmed_styles_verified=True,pdf_witnesses=witnessed,**evidence))
        current,model=output,sidecar
        print('stage',n,'passed',flush=True)
    assert engine=={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    return dict(date='2026-09-20',source_id=meta['id'],source_url=meta['source_url'],
        source_sha256=meta['sha256'],selection=selection,confirmed=expected,
        source_replay=dict(mupdf_all_equal=gate['mupdf_page_pixels'],poppler_changed_pixels=gate['poppler_diff']['all_changed_pixels']),
        unconfirmed_refusals=refused,stages=stages,engine_sha256=engine,
        runner_sha256=source_sha(Path(__file__)),provider_sha256=source_sha(font),
        font_policy='MS Gothic TTC face 0 explicitly supplied for new glyphs; original resource/code retained otherwise',
        environment=dict(pymupdf=pymupdf.VersionBind),
        scope='Full first line selected to preserve nearby glyph context; only its two-glyph nonzero tracking style is confirmed. Real rise is zero; synthetic roundtrips cover nonzero rise. No alignment or paragraph-wide spacing inference.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True)
    write(directory/'summary.json',run(directory))
