"""Small reviewable B2 CLI. Geometry is produced, never accepted as input."""
import argparse
import json
from pathlib import Path
from .auto import propose,approve,save
from .engine import noop


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['propose','approve','save','reedit','noop']);p.add_argument('--source',required=True);p.add_argument('--output',required=True)
    p.add_argument('--page',type=int);p.add_argument('--old');p.add_argument('--new');p.add_argument('--occurrence',type=int,default=0);p.add_argument('--substitute-json');p.add_argument('--proposal');p.add_argument('--acceptance');p.add_argument('--acknowledge-inference',action='store_true');args=p.parse_args()
    out=Path(args.output)
    if out.exists():raise ValueError('output already exists')
    if args.action in ('propose','reedit'):
        sub=json.loads(Path(args.substitute_json).read_text()) if args.substitute_json else None
        result=propose(args.source,args.page,args.old,args.new,occurrence=args.occurrence,substitute=sub)
        if args.action=='reedit':
            accepted=approve(args.source,result,acknowledge_inference=args.acknowledge_inference)
            record=save(args.source,accepted,out);out.with_suffix('.save.json').write_text(json.dumps(record,ensure_ascii=False,indent=2));out.with_suffix('.proposal.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return
    elif args.action=='approve':result=approve(args.source,json.loads(Path(args.proposal).read_text()),acknowledge_inference=args.acknowledge_inference)
    elif args.action=='save':
        result=save(args.source,json.loads(Path(args.acceptance).read_text()),out);out.with_suffix('.save.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return
    else:noop(args.source,out);return
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
