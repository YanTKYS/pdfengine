"""Recheck final read-only guards and layout against already saved first outputs.

This never rewrites a PDF. It permits safety hardening after a discovery run
without regenerating the evidence or attributing its timing to new guards.
"""
import json
from pathlib import Path
from .engine import observe,wrap,_check_insertion_state
from pdfeditor.shaped_font import ShapedFont


def qualify(root,manifest):
    root=Path(root);font=ShapedFont(root/'provider.ttf');result={}
    for case in manifest['requests']:
        spec=manifest['documents'][case['document']];c,paras=observe(spec['path'],spec)
        try:
            chosen={i for p in paras for i in p.ids}
            first=min(e.operator.start for e in c.events if any(chosen.intersection(ch.source_orders) for ch in e.chars))
            safe=[b for b in c.boundaries if b.operator.end<first and not b.text_object and b.marked_content_depth==0
                  and not b.pending_path and not b.pending_clip and b.state.ctm==(1,0,0,1,0,0)]
            if safe:_check_insertion_state(safe[-1],spec,c.page.rect.height)
            baseline=spec['baseline'];lines=[];texts=[]
            for p in paras:
                text=p.text;sizes=p.sizes[:]
                if case['old'] in text:
                    start=text.index(case['old']);sizes[start:start+len(case['old'])]=[sizes[start]]*len(case['new']);text=text.replace(case['old'],case['new'])
                ls=wrap(text,sizes,font,spec,baseline);lines.append([dict(text=l['text'],baseline=l['baseline']) for l in ls]);texts.append(text)
                baseline=ls[-1]['baseline']+spec['paragraph_gap']
            record=json.loads((root/f"{case['id']}.json").read_text())
            after,observed=observe(root/f"{case['id']}.pdf",spec);after.close()
            result[case['id']]=dict(source_semantics_and_inbound_guards=True,insertion_clip_contains_frame=True,
                 layout_unchanged=lines==record['lines'],pdf_alone_observation_matches=[p.text for p in observed]==texts,
                 original_occurrences_match_manifest=[p.ids for p in paras]==[[g['id'] for g in p['glyphs']] for p in spec['paragraphs']])
            assert all(result[case['id']].values())
        finally:c.close()
    return result

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',default='tmp/architecture-poc');args=p.parse_args()
    m=json.loads(Path('evaluations/architecture_poc/manifest.json').read_text());result=qualify(args.root,m)
    Path(args.root,'final-guard-qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
