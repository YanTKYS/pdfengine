"""Independent B2 audit: no inference, layout or writer import.

The frozen oracle is used here only, never in the B2 API. Saved outputs are
re-interpreted and rendered independently; save metadata is not proof.
"""
from collections import Counter
import hashlib
from pathlib import Path
import time
import pymupdf
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content
from .audit import extract,normalized,painting,pixels,semantic


def records(path,page):
    c=ContentPage(path,page);rows=[]
    try:
        for e in c.events:
            for ch in e.chars:
                for i in ch.source_orders:
                    g=c.actual[i]
                    rows.append(dict(id=i,text=ch.text,code=ch.code.hex(),gid=g['gid'],font=e.state.font.name,size=e.state.size,
                        tz=e.state.tz,fill=(e.state.fill[0],tuple(float(v) for v in e.state.fill[1])),tr=e.state.tr,opacity=e.state.opacity,origin=list(g['origin']),bbox=list(g['bbox'])))
        return sorted(rows,key=lambda r:r['id'])
    finally:c.close()


def key(r):
    import json
    return json.dumps({k:v for k,v in r.items() if k!='id'},sort_keys=True)


def structural(tree):
    if isinstance(tree,list):return [structural(x) for x in tree]
    if isinstance(tree,dict):return {k:structural(v) for k,v in tree.items() if k not in ('_xref','/ActualText')}
    return tree


def ids(p):return p.get('glyph_ids',[g['id'] for g in p.get('glyphs',[])])

def audit(source,output,spec,case,root,*,expected=None):
    start=time.perf_counter();root=Path(root);root.mkdir(parents=True,exist_ok=True);checks={}
    want=expected or case['expected'];oldrows=records(source,spec['page']);newrows=records(output,spec['page'])
    selected={i for p in spec['paragraphs'] for i in ids(p)};foreign=Counter(key(r) for r in oldrows if r['id'] not in selected);remain=Counter(foreign);changed=[]
    for r in newrows:
        k=key(r)
        if remain[k]:remain[k]-=1
        else:changed.append(r)
    checks['foreign_exact_code_gid_resource_size_scale_color_position']=not +remain
    checks['exact_drawn_paragraphs']=''.join(r['text'] for r in changed)==want+''.join(p['text'] for p in spec['paragraphs'][1:])
    checks['inside_fixed_oracle']=all(pymupdf.Rect(spec['frame']).contains(pymupdf.Rect(r['bbox'])) for r in changed if r['text'].strip())
    pb=painting(source,spec['page']);pa=painting(output,spec['page'])
    for k in ('drawings','images','links','annots'):checks['foreign_'+k]=pb[k]==pa[k]
    checks['source_font_programs_retained']=all(pa['fonts'].get(k)==v for k,v in pb['fonts'].items())
    sb=semantic(source,spec['page']);sa=semantic(output,spec['page']);checks['semantic_internal']=all(sa['checks'].values())
    checks['semantic_structure_order_languages_preserved']=structural(sb['tree'])==structural(sa['tree'])
    if sb['tagged']:
        original={r['mcid']:r for r in sb['rows']};edited={r['mcid']:r for r in sa['rows']}
        checks['semantic_scope_inventory']=original.keys()==edited.keys()
        content=ContentPage(source,spec['page']);target_ids=set(ids(spec['paragraphs'][0]));target_mcids=set()
        try:
            for scope in observe_marked_content(source,spec['page'])['scopes']:
                lo,hi=scope['content_byte_range']
                scopeids={i for e in content.events if lo<=e.operator.start<hi for ch in e.chars for i in ch.source_orders}
                if scopeids & target_ids:target_mcids.add(scope['association']['mcid'])
        finally:content.close()
        checks['semantic_foreign_actualtext']=all(k in edited and edited[k]['actualtext']==r['actualtext'] for k,r in original.items() if k not in target_mcids)
    # Style and code preservation of all unchanged target characters, allowing
    # their coordinates to change during reflow. The edit is normalized first.
    old=case['old'];new=case['new'];prefix=0;suffix=0
    while prefix<min(len(old),len(new)) and old[prefix]==new[prefix]:prefix+=1
    while suffix<min(len(old)-prefix,len(new)-prefix) and old[-suffix-1]==new[-suffix-1]:suffix+=1
    target=[r for r in oldrows if r['id'] in ids(spec['paragraphs'][0])];at=spec['paragraphs'][0]['text'].index(old)+prefix;end=at+len(old)-prefix-suffix
    mapping=[(i,i) for i in range(at)]+[(i,i+len(new)-len(old)) for i in range(end,len(target))]
    if expected is not None:raise ValueError('Lifecycle must supply updated source oracle and concrete edit for style verification')
    stylekeys=('text','code','gid','font','size','tz','fill','tr','opacity')
    checks['retained_target_original_code_and_style']=all(all(target[i][k]==changed[j][k] for k in stylekeys) for i,j in mapping) if len(changed)>=len(want) else False
    follower_metrics=[];offset=len(want)
    for p in spec['paragraphs'][1:]:
        before=[r for r in oldrows if r['id'] in ids(p)];after=changed[offset:offset+len(before)];offset+=len(before)
        same=len(before)==len(after) and all(all(a[k]==b[k] for k in stylekeys) for a,b in zip(before,after));dy=[b['origin'][1]-a['origin'][1] for a,b in zip(before,after)]
        spread=max(dy)-min(dy) if dy else 0
        positions=all(a['origin'][0]==b['origin'][0] for a,b in zip(before,after)) and spread<.0001
        follower_metrics.append(dict(original_code_style=same,rigid_translation=positions,dy=dy[0] if dy else None,translation_spread=spread))
    checks['follower_original_code_style_rigid_translation']=all(f['original_code_style'] and f['rigid_translation'] for f in follower_metrics)
    btext=extract(source,spec['page'],root);atext=extract(output,spec['page'],root);extraction={}
    for renderer in ('mupdf','poppler'):
        got=normalized(atext[renderer]);logical=want.strip();original=normalized(btext[renderer])
        exact=got.count(logical)==1
        inherited=(original.count(case['old'])==1 and got==original.replace(case['old'],case['new'],1))
        checks[renderer+'_copy_extract']=exact or inherited
        extraction[renderer]=dict(raw=atext[renderer],paragraph_exact=exact,source_raw=btext[renderer],
            whole_page_inherited_spacing=inherited,comparison='logical paragraph' if exact else 'exact original reader whole-page replacement' if inherited else 'unmatched; no whitespace normalization beyond line edges')
    with pymupdf.open(output) as d:
        found=d[spec['page']-1].search_for(case['new']);checks['native_replacement_search']=bool(found)
    with pymupdf.open(source) as d:original_search_count=len(d[spec['page']-1].search_for(case['old']))
    raster=pixels(source,output,root/'raster',spec)
    checks['outside_pixels_zero']=all(v['outside_pixels']==0 for rows in raster.values() for v in rows)
    safety_keys=[k for k in checks if not k.endswith('_copy_extract') and k!='native_replacement_search']
    safe=all(checks[k] for k in safety_keys);quality=all(checks.values())
    return dict(outcome='success' if quality else 'quality-audit-failed' if safe else 'safety-audit-failed',checks=checks,
      failed=[k for k,v in checks.items() if not v],source_sha256=hashlib.sha256(Path(source).read_bytes()).hexdigest(),output_sha256=hashlib.sha256(Path(output).read_bytes()).hexdigest(),
      native_search=dict(original_selection_hits=original_search_count,replacement_hits=len(found)),follower_metrics=follower_metrics,retained_target_glyphs_verified=len(mapping),pixels=raster,extraction=extraction,semantic=sa,timing=time.perf_counter()-start)


def semantic_identity(snapshot):
    """Compare semantic content/topology; PDF object numbers are file-local."""
    def walk(value):
        if isinstance(value,list):return [walk(v) for v in value]
        if isinstance(value,dict):return {k:walk(v) for k,v in value.items() if k not in ('_xref','parent')}
        return value
    return walk(snapshot)


def audit_noop(source,output,page,root):
    raster=pixels(source,output,root);glyphs=records(source,page)==records(output,page)
    semantic_ok=semantic_identity(semantic(source,page))==semantic_identity(semantic(output,page))
    clean=all(p['outside_pixels']==0 for rows in raster.values() for p in rows)
    return dict(outcome='success' if glyphs and semantic_ok and clean else 'failed',pixels=raster,glyphs_exact=glyphs,semantic_content_topology_exact=semantic_ok)
