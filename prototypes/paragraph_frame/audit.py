"""Read-only result audit, independent of the rewrite/IR/layout implementation.

MuPDF and Poppler extraction/raster checks supplement a content interpreter
and pypdf structure-tree traversal. No writer result is trusted as evidence.
"""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
from PIL import Image,ImageChops,ImageDraw
import pymupdf
from pypdf import PdfReader
from pypdf.generic import IndirectObject
from pdfeditor.content_stream import ContentPage,state_object
from pdfeditor.marked_content import observe_marked_content


def normalized(text):
    # Only presentation line edges are stripped; internal spaces are meaningful.
    # A break between two Latin words is a word space (CJK lines join directly).
    result=''
    for line in (l.strip() for l in text.splitlines()):
        # (the same rule as B2's logical text: not after a hyphen or a space).
        if result and line and result[-1].isascii() and not result[-1].isspace() and result[-1]!='-' and line[0].isascii() and line[0].isalnum():result+=' '
        result+=line
    return result


def extract(path,page,root):
    with pymupdf.open(path) as doc: mupdf=doc[page-1].get_text(sort=False)
    result=subprocess.run(['pdftotext','-f',str(page),'-l',str(page),'-enc','UTF-8',str(path),'-'],capture_output=True,check=True)
    poppler=result.stdout.decode('utf-8')
    return dict(mupdf=mupdf,poppler=poppler,poppler_stderr=result.stderr.decode())


def render(path,root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    stamp=root/'sha.txt'
    with pymupdf.open(path) as doc:
        count=len(doc)
        if stamp.exists() and stamp.read_text()==digest:return count
        for i,page in enumerate(doc):page.get_pixmap(dpi=144,alpha=False).save(root/f'mupdf-{i+1}.png')
    run=subprocess.run(['pdftoppm','-r','144','-png',str(path),str(root/'poppler')],capture_output=True,check=True)
    (root/'poppler-stderr.txt').write_bytes(run.stderr)
    stamp.write_text(digest)
    return count


def pixels(a,b,root,spec=None):
    first=Path(root)/'before';second=Path(root)/'after'
    count=render(a,first);assert render(b,second)==count
    result={}
    for renderer in ('mupdf','poppler'):
        vals=[]
        for page in range(1,count+1):
            names=[f'{renderer}-{page}.png',f'{renderer}-{page:02}.png']
            pa=next(first/n for n in names if (first/n).exists());pb=next(second/n for n in names if (second/n).exists())
            with Image.open(pa) as ia,Image.open(pb) as ib:
                if ia.size!=ib.size:raise ValueError('raster dimensions differ')
                delta=ImageChops.difference(ia.convert('RGB'),ib.convert('RGB'))
                changed=delta.getbbox()
                if spec and page==spec['page']:
                    x0,y0,x1,y1=spec['frame'];ImageDraw.Draw(delta).rectangle((math.floor(x0*2),math.floor(y0*2),math.ceil(x1*2)-1,math.ceil(y1*2)-1),fill=(0,0,0))
                r,g,b=delta.split();mono=ImageChops.lighter(ImageChops.lighter(r,g),b)
                outside=ia.width*ia.height-mono.histogram()[0]
                vals.append(dict(page=page,outside_pixels=outside,changed_bbox=changed))
        result[renderer]=vals
    return result


def glyphs(path,page,excluded=None,new_font=False):
    c=ContentPage(path,page)
    try:
        result=[]
        for e in c.events:
            if new_font and e.state.font.name.startswith('/PFRebuild'):continue
            for ch in e.chars:
                for i in ch.source_orders:
                    if excluded and i in excluded:continue
                    g=c.actual[i]
                    result.append(dict(text=ch.text,code=ch.code.hex(),gid=g['gid'],font=e.state.font.name,
                                       origin=list(g['origin']),bbox=list(g['bbox']),size=e.state.size,fill=e.state.fill,tr=e.state.tr,
                                       opacity=e.state.opacity))
        return result
    finally:c.close()


def painting(path,page):
    with pymupdf.open(path) as d:
        p=d[page-1]
        drawings=[{k:v for k,v in x.items() if k!='seqno'} for x in p.get_drawings()]
        # 'number' is the block index among text and image blocks; it changes
        # when text blocks are reordered and does not identify the image.
        images=[{k:v for k,v in x.items() if k!='number'} for x in p.get_image_info(hashes=True,xrefs=False)]
        links=[{k:v for k,v in x.items() if k not in ('xref','id')} for x in p.get_links()]
        annots=[dict(type=a.type,rect=list(a.rect),info=a.info) for a in (p.annots() or [])]
        fonts={f[4]:hashlib.sha256(d.extract_font(f[0])[3]).hexdigest() for f in p.get_fonts(full=True)}
        return dict(drawings=drawings,images=images,links=links,annots=annots,fonts=fonts)


def _entry(value,depth=0,seen=()):
    """A structure entry as plain data (attribute dictionaries may nest deeply; cycles are cut)."""
    if isinstance(value,IndirectObject):
        if value.idnum in seen or depth>64:return 'ref'
        seen=seen+(value.idnum,);value=value.get_object()
    if isinstance(value,dict):return {str(k):_entry(v,depth+1,seen) for k,v in value.items() if k not in ('/Length','/Filter','/DecodeParms','/P','/Pg','/K')}
    if isinstance(value,(list,tuple)):return [_entry(v,depth+1,seen) for v in value]
    return state_object(value)


def semantic(path,page):
    reader=PdfReader(path);root=reader.trailer['/Root'].get('/StructTreeRoot')
    if root is None:return dict(tagged=False,checks=dict(no_struct_tree=True),tree=None)
    marked=observe_marked_content(path,page);c=ContentPage(path,page)
    rows=[]
    try:
        for s in marked['scopes']:
            a=s['association'];lo,hi=s['content_byte_range']
            drawn=''.join(ch.text for e in c.events if lo<=e.operator.start<hi for ch in e.chars)
            owner=reader.get_object(a['owner_reference']['xref']) if a.get('owner_reference') else {}
            rows.append(dict(mcid=a.get('mcid'),status=a['status'],tag=s['tag'],drawn=drawn,actualtext=owner.get('/ActualText'),
                             role=owner.get('/S'),parent=owner.raw_get('/P').idnum if '/P' in owner else None))
    finally:c.close()
    # Pagination artifacts are unmarked by definition (PDF 32000-1 §14.8.2.2).
    content=[r for r in rows if r['tag']!='/Artifact']
    checks=dict(complete=marked['complete'],all_tree_backed=all(r['status']=='tree_backed' for r in content),
                unique_mcids=len({r['mcid'] for r in content})==len(content),
                actualtext_matches_drawn=all(r['actualtext'] is None or r['actualtext']==r['drawn'] for r in rows))
    parents_ok=[]
    def tree(node,parent=None):
        if isinstance(node,list):return [tree(x,parent) for x in node]
        obj=node.get_object() if hasattr(node,'get_object') else node
        if isinstance(obj,int):return dict(mcid=int(obj))
        if not isinstance(obj,dict):return str(obj)
        if '/S' in obj:
            parents_ok.append(isinstance(obj.raw_get('/P'),IndirectObject) and obj.raw_get('/P').idnum==parent)
        ref=getattr(obj,'indirect_reference',None)
        result={k:_entry(v) for k,v in obj.items() if k not in ('/K','/P','/Pg','/ParentTree')}
        if obj.get('/S')=='/P':result['_xref']=ref.idnum if ref else None
        if '/K' in obj:result['/K']=tree(obj['/K'],ref.idnum if ref else None)
        return result
    graph=tree(root)
    checks['all_parent_backlinks']=all(parents_ok)
    # ParentTree reverse direction, including now-null removed slots.
    parenttree=root.get_object()['/ParentTree']
    def entries(node):
        node=node.get_object();out=[];nums=node.get('/Nums',[])
        for i in range(0,len(nums),2):out.append((int(nums[i]),nums[i+1].get_object()))
        for child in node.get('/Kids',[]):out.extend(entries(child))
        return out
    arr=dict(entries(parenttree))[int(reader.pages[page-1]['/StructParents'])]
    active={r['mcid'] for r in content}
    checks['parent_tree_reverse']=all(type(v.get_object()).__name__=='NullObject' or i in active for i,v in enumerate(arr))
    return dict(tagged=True,checks=checks,tree=graph,rows=rows)


def audit(source,edited,spec,case,root,*,expected=None,arm='B'):
    started=time.perf_counter();root=Path(root);root.mkdir(parents=True,exist_ok=True)
    before=extract(source,spec['page'],root);after=extract(edited,spec['page'],root)
    expected_text=case['expected'] if expected is None else expected
    replacement=expected_text
    old_paragraph=spec['paragraphs'][0]['text']
    checks={}
    extraction={}
    for renderer in ('mupdf','poppler'):
        original=normalized(before[renderer])
        # Delimit the selected paragraph by unique unchanged edge text. Some
        # source engines infer a space after 16% although there is no space
        # glyph. Regeneration replaces that entire logical paragraph.
        prefix=old_paragraph.strip()[:20];suffix=old_paragraph.strip()[-12:]
        lo=original.index(prefix);hi=original.index(suffix,lo)+len(suffix)
        want=original[:lo]+replacement.strip()+original[hi:]
        inherited=original[lo:hi].replace(case['old'],case['new'])
        if expected is not None:inherited=inherited.replace(case['reedit_old'],case['reedit_new'])
        inherited_page=original[:lo]+inherited+original[hi:]
        got=normalized(after[renderer])
        checks[renderer+'_whole_page_text']=got in (want,inherited_page)
        checks[renderer+'_new_paragraph_once']=got.count(expected_text.strip())==1 or got.count(inherited)==1
        extraction[renderer]=dict(expected_page=want,inherited_spacing_page=inherited_page,actual_page=got,raw=after[renderer],
                                 copied_spacing='logical' if got==want else 'inherited source extractor spacing' if got==inherited_page else 'mismatch')
    selected={g['id'] for p in spec['paragraphs'] for g in p['glyphs']}
    oldg=glyphs(source,spec['page'],excluded=selected);newg=glyphs(edited,spec['page'],new_font=True)
    if arm=='A':
        with pymupdf.open(edited) as d:
            ids={i for i,g in enumerate([ch for span in d[spec['page']-1].get_texttrace() for ch in span['chars']]) if pymupdf.Rect(spec['frame']).contains(pymupdf.Point(g[2]))}
        newg=glyphs(edited,spec['page'],excluded=ids)
    checks['foreign_glyph_identity_position_state']=oldg==newg
    c=ContentPage(edited,spec['page'])
    try:
        if arm=='B':
            drawn=''.join(ch.text for e in c.events if e.state.font.name.startswith('/PFRebuild') for ch in e.chars)
        else:
            drawn=''.join(g['unicode'] for g in c.actual if pymupdf.Rect(spec['frame']).contains(pymupdf.Point(g['origin'])))
        checks['exact_drawn_paragraphs']=drawn==expected_text+spec['paragraphs'][1]['text']
    finally:c.close()
    pb=painting(source,spec['page']);pa=painting(edited,spec['page'])
    for key in ('drawings','images','links','annots'):checks['foreign_'+key]=pb[key]==pa[key]
    checks['original_fonts_retained']=all(pa['fonts'].get(k)==v for k,v in pb['fonts'].items())
    sb=semantic(source,spec['page']);sa=semantic(edited,spec['page'])
    checks['tagged_status_preserved']=sb['tagged']==sa['tagged']
    checks.update({'semantic_'+k:v for k,v in sa['checks'].items()})
    if sa['tagged']:
        # Tree snapshots keyed by traversal order, ignoring only selected P
        # children. All parent siblings/attrs, RoleMap etc stay identical.
        before_tree=sb['tree'];after_tree=sa['tree']
        for graph,observed in ((before_tree,sb),(after_tree,sa)):
            parents={r['parent'] for r in observed['rows'] if r['drawn'].startswith(('標記','つきましては、'))}
            def prune(node):
                if isinstance(node,list):
                    for x in node:prune(x)
                elif isinstance(node,dict):
                    xref=node.pop('_xref',None)
                    if xref in parents:
                        node['/K']='EDITED PARAGRAPH';return
                    if '/K' in node:prune(node['/K'])
            prune(graph)
        checks['foreign_semantic_tree']=before_tree==after_tree
        changed=[r for r in sa['rows'] if r['drawn'].startswith(('標記','つきましては、'))]
        checks['changed_semantic_paragraphs']=len(changed)==2 and [r['actualtext'] for r in changed]==[expected_text,spec['paragraphs'][1]['text']]
    raster=pixels(source,edited,root/'raster',spec)
    checks['outside_pixels_zero']=all(r['outside_pixels']==0 for rs in raster.values() for r in rs)
    # Independent foreign occurrence census plus exact_drawn_paragraphs
    # prevents unnoticed source residues. B consumes whole paragraphs; A may
    # retain source glyphs. Foreign spaces are retained explicitly.
    checks['foreign_occurrence_census_preserved']=len(oldg)==len(newg) and oldg==newg
    with pymupdf.open(edited) as doc:
        page=doc[spec['page']-1]
        needle=case['new'] if expected is None else case['reedit_new']
        checks['mupdf_search_new']=bool(page.search_for(needle))
        selected_copy=''.join(s['text'] for b in page.get_text('dict')['blocks'] if 'lines' in b for l in b['lines'] for s in l['spans'])
        checks['copy_text_new']=normalized(needle) in normalized(selected_copy)
    with pymupdf.open(edited) as doc:
        page=doc[spec['page']-1];rows={}
        for span in page.get_texttrace():
            for code,gid,origin,bbox in span['chars']:
                if pymupdf.Rect(spec['frame']).contains(pymupdf.Point(origin)) and not chr(code).isspace():
                    rows.setdefault(round(origin[1],2),[]).append((origin[0],chr(code),gid,list(bbox),span['opacity'],span['type']))
        ordered=[sorted(row) for _,row in sorted(rows.items())]
        checks['edited_frame_glyphs_visible_nonzero']=all(g[2]!=0 and g[4]==1 and g[5]==0 for row in ordered for g in row)
        checks['line_bands_do_not_overlap']=all(max(g[3][3] for g in a)<=min(g[3][1] for g in b) for a,b in zip(ordered,ordered[1:]))
        checks['kinsoku']=all(row[0][1] not in '、。，．？！）］｝」』】〕〉》ぁぃぅぇぉっゃゅょァィゥェォッャュョ' and row[-1][1] not in '（［｛「『【〔〈《' for row in ordered)
        physical_lines=[dict(text=''.join(g[1] for g in row),baseline=y) for (y,_),row in zip(sorted(rows.items()),ordered)]
    return dict(outcome='success' if all(checks.values()) else 'audit-failed',checks=checks,extraction=extraction,
                semantic={k:v for k,v in sa.items() if k!='tree'},physical_lines=physical_lines,raster=raster,seconds=time.perf_counter()-started)
