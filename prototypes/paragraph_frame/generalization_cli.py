"""Small reviewable B2 CLI. Geometry is produced, never accepted as input.

``columns`` lists the page's column blocks and continuation links; a
candidate link is used only when its id is passed with ``--continuation``.
"""
import argparse
import json
from pathlib import Path
from .auto import propose,approve,save
from .auto_detect import recognize
from .engine import noop


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['propose','approve','save','reedit','noop','columns']);p.add_argument('--source',required=True);p.add_argument('--output',required=True)
    p.add_argument('--page',type=int);p.add_argument('--old');p.add_argument('--new');p.add_argument('--occurrence',type=int,default=0);p.add_argument('--substitute-json');p.add_argument('--proposal');p.add_argument('--acceptance');p.add_argument('--acknowledge-inference',action='store_true');p.add_argument('--no-stored-conditions',action='store_true')
    p.add_argument('--continuation',action='append',default=[],help='approve a candidate column link by its id (repeatable)')
    p.add_argument('--decoration',action='append',default=[],help='approve a candidate line decoration by its id (repeatable)');args=p.parse_args()
    out=Path(args.output)
    if out.exists():raise ValueError('output already exists')
    if args.action in ('propose','reedit'):
        sub=json.loads(Path(args.substitute_json).read_text()) if args.substitute_json else None
        result=propose(args.source,args.page,args.old,args.new,occurrence=args.occurrence,substitute=sub,stored_conditions=not args.no_stored_conditions,continuation=args.continuation,decorations=args.decoration)
        if args.action=='reedit':
            accepted=approve(args.source,result,acknowledge_inference=args.acknowledge_inference)
            record=save(args.source,accepted,out);out.with_suffix('.save.json').write_text(json.dumps(record,ensure_ascii=False,indent=2));out.with_suffix('.proposal.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return
    elif args.action=='approve':result=approve(args.source,json.loads(Path(args.proposal).read_text()),acknowledge_inference=args.acknowledge_inference)
    elif args.action=='save':
        result=save(args.source,json.loads(Path(args.acceptance).read_text()),out);out.with_suffix('.save.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return
    elif args.action=='columns':result=recognize(args.source,args.page,args.continuation)
    else:noop(args.source,out);return
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
