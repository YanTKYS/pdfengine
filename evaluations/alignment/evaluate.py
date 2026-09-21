"""PR C: externally generated Japanese Word paragraph, explicit character justify."""
import argparse
from pathlib import Path

import pymupdf

from pdfeditor.attributed import inspect_paragraph, SourceParagraph
from pdfeditor.content_stream import multiply
from pdfeditor.editable import write_editable, edit_document, open_editable
from pdfeditor.selection import make_selection, source_sha
from pdfeditor.style_confirmation import observed_values
from evaluations.anchors.evaluate import replay_gate
from evaluations.spacing.evaluate import audit, read, write

ROOT=Path(__file__).resolve().parents[2]
BASE=Path(__file__).resolve().parent


def geometry(pdf, state, report):
    snapshot=state['paragraph']
    paragraph=SourceParagraph(pdf,snapshot['selection'],logical=snapshot['logical'])
    try:
        rows=[]
        for i,line in enumerate(report['lines']):
            units=[u for u in paragraph.units[line['start']:line['end']] if u.source_index is not None]
            first,last=units[0],units[-1]
            s=last.event.state;m=multiply(last.event.text_matrix,s.ctm)
            right=last.observation['origin'][0]+last.char.pdf_width/1000*s.size*m[0]*s.tz/100
            left=first.observation['origin'][0]
            expected=report['x']+report['widths']['explicitly_supplied_width']
            assert abs(left-line['x'])<.002
            if i<len(report['lines'])-1:assert abs(right-expected)<.002
            else:assert right<expected-.002
            rows.append(dict(start=line['start'],end=line['end'],left=left,right=right,
                region_right=expected,justified=i<len(report['lines'])-1,
                right_error=abs(right-expected) if i<len(report['lines'])-1 else None))
        witnesses=[]
        for unit in paragraph.units:
            if unit.source_index is None:continue
            actual=observed_values(unit);style=paragraph.styles[unit.style_id]
            assert abs(actual['tracking']-style.tracking)<.001
            assert abs(actual['baseline_shift']-style.rise)<.001
            witnesses.append(actual)
        return dict(lines=rows,inline_witness_count=len(witnesses),tracking_rise_witnesses_verified=True)
    finally:paragraph.close()


def run(directory):
    engine={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    meta=next(c for c in read(ROOT/'evaluations/sources.json') if c['id']=='word_osaka_fire_notice')
    source=ROOT/meta['path'];assert source_sha(source)==meta['sha256']
    # Manually reviewed complete first body paragraph, with one-character
    # first-line indent. The right edge/width are explicit evaluator input.
    selection=make_selection(source,1,glyph_ids=list(range(143,218)),explicit_width=493.2)
    p=inspect_paragraph(source,selection);write(directory/'source-snapshot.json',p)
    gate,_=replay_gate(source,selection,directory)
    request=dict(alignment='justify',justify_policy='character')
    font=Path('C:/Windows/Fonts/msgothic.ttc')
    current=source;model=None;stages=[]
    for n in range(4):
        output,sidecar=directory/f'v{n}.pdf',directory/f'v{n}.json'
        if n==0:
            report=write_editable(source,output,sidecar,p,[],
                fonts={s['id']:dict(path=str(font),font_index=0) for s in p['styles']},
                paragraph_style={'s1':dict(tracking=-.12,baseline_shift=0)},paragraph_layout=request,
                width=493.2,x=53.88,first_line_indent=10.584,max_bottom=314,min_line_height=19.8)
        else:
            text=open_editable(current,model)['state']['paragraph']['text']
            edits=([dict(start=42,end=len(text),text='')] if n==1 else
                   [dict(start=len(text),end=len(text),text='必要な情報を確認し、関係機関と連携して適切に対応してください。')] if n==2 else [])
            report=edit_document(current,model,output,sidecar,edits)
        write(directory/f'v{n}-report.json',report)
        opened=open_editable(output,sidecar);assert opened['status']=='restored',opened
        state=opened['state'];assert state['paragraph']['text']==report['after']
        assert state['logical_element']['alignment']==dict(value='justify',provenance='explicitly_confirmed',justify_policy='character')
        # The initial same-text reflow may change internal pixels. The final
        # generated no-op must be pixel-exact, not merely semantically equal.
        evidence=audit(current,output,report,directory/f'v{n}-audit',noop=n==3)
        stages.append(dict(stage=n,status='passed',logical_unicode_restored=True,
                           alignment_geometry=geometry(output,state,report),**evidence))
        current,model=output,sidecar
        print('stage',n,'passed',flush=True)
    assert [len(s['alignment_geometry']['lines']) for s in stages]==[2,1,2,2]
    assert engine=={p.name:source_sha(p) for p in sorted((ROOT/'pdfeditor').glob('*.py'))}
    return dict(date='2026-09-21',source_id=meta['id'],source_url=meta['source_url'],source_sha256=meta['sha256'],
        selection=selection,source_alignment_candidates=p['spacing']['alignment_candidates'],confirmed=request,
        source_replay=dict(mupdf_all_equal=gate['mupdf_page_pixels'],poppler_changed_pixels=gate['poppler_diff']['all_changed_pixels']),
        stages=stages,engine_sha256=engine,runner_sha256=source_sha(Path(__file__)),provider_sha256=source_sha(font),
        environment=dict(pymupdf=pymupdf.VersionBind),
        font_policy='Original resource/code retained; MS Gothic TTC face 0 explicitly supplied for new glyphs.',
        limitation='Source candidate is unknown (indent and exporter spacing), not proven justify. Alignment and character-gap policy are explicitly supplied after visual review; no classifier promotion. Real rise is zero.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run-name',required=True);args=parser.parse_args()
    directory=BASE/'runs'/args.run_name;directory.mkdir(parents=True)
    write(directory/'summary.json',run(directory))
