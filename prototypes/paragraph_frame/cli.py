"""Fresh-process workers for save, PDF-alone and sidecar lifecycle checks."""
import argparse
import json
from pathlib import Path
import time
from .engine import rewrite,noop


def main():
    parser=argparse.ArgumentParser();parser.add_argument('job');args=parser.parse_args()
    job=json.loads(Path(args.job).read_text());start=time.perf_counter()
    try:
        if job['action']=='edit':
            record=rewrite(job['source'],job['output'],job['spec'],job['old'],job['new'],job['font'],sidecar=job.get('sidecar'))
            Path(job['output']).with_suffix('.json').write_text(json.dumps(record,ensure_ascii=False,indent=2))
            result=dict(status='saved',record=record)
        elif job['action']=='noop':
            noop(job['source'],job['output']);result=dict(status='saved')
        elif job['action'] in ('a-reedit','a-noop'):
            from pdfeditor.shared_flow import open_shared_flow,edit_shared_flow
            from pdfeditor.page_proposal import replace_in_flow
            opened=open_shared_flow(job['source'],job['sidecar'])
            if opened['status']!='restored':raise ValueError(opened.get('reason',opened['status']))
            state=opened['state'];changes={}
            if job['action']=='a-reedit':
                target=next(p for p in state['paragraphs'].values() if job['old'] in p['logical']['text'])
                offset=target['logical']['text'].index(job['old'])
                style=next(s['style_id'] for s in target['logical']['style_spans'] if s['start']<=offset<s['end'])
                changes=replace_in_flow(state,job['old'],job['new'],style_id=style)
            edit_shared_flow(job['source'],state,job['output'],str(Path(job['output']).with_suffix('.json')),changes)
            reopened=open_shared_flow(job['output'],str(Path(job['output']).with_suffix('.json')))
            result=dict(status='saved',reopen=reopened['status'])
        elif job['action']=='a-pdf-alone':
            from .baseline import run
            from pdfeditor.page_proposal import _Analysis
            from pdfeditor.content_stream import ContentPage
            spec=dict(job['spec']);spec['path']=job['source']
            analysis=_Analysis(job['source'],spec['page'])
            lines=[l for l in analysis.lines if spec['frame'][1]<=l['baseline']<=spec['frame'][3] and l['text'].strip()]
            spec['line_ids']=[l['id'] for l in lines]
            starts=[i for i,l in enumerate(lines) if abs(l['x']-spec['first_x'])<.1]
            if len(starts)!=2:raise ValueError('PDF-alone paragraph boundaries cannot be recovered')
            spec['paragraphs']=[dict(source_line_baselines=[l['baseline'] for l in lines[:starts[1]]]),{}]
            c=ContentPage(job['source'],spec['page'])
            try:
                text=''.join(g['unicode'] for g in c.actual);offset=text.index(job['old'])
                case=dict(old=job['old'],new=job['new'],glyph_ids=list(range(offset,offset+len(job['old']))))
            finally:c.close()
            record=run(case,spec,job['font'],job['directory'])
            result=dict(status=record['outcome'],record=record)
        else:raise ValueError('unknown action')
    except Exception as exc:result=dict(status='refused',reason=f'{type(exc).__name__}: {exc}')
    result['seconds']=time.perf_counter()-start
    Path(job['result']).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='record'},ensure_ascii=False))

if __name__=='__main__':main()
