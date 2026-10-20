"""Read-only source observation; run before either comparison arm."""
import hashlib
import json
from pathlib import Path
import pymupdf
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import _Analysis

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'evaluations/architecture_poc/manifest.json'
REQUESTS = [
 ('A', 'osaka', 'につきまして、', 'に関して、', 'に関して、', 'について、'),
 ('B', 'osaka', '標記につきまして、', '標記のガイドラインにつきまして、内容をご確認いただけるよう', '内容をご確認', '内容を再確認'),
 ('C', 'libreoffice', '依然こそこそと、隠れたやり方で', '依然として', '依然として', 'なおも'),
 ('D', 'libreoffice', '抵抗します。', '抵抗します。このため、移行の意義を丁寧に説明し、実際の作業を通じて疑問を解消することが重要です。', '丁寧に説明', '十分に説明'),
]

def sha(data): return hashlib.sha256(data).hexdigest()

def main():
 sources = json.loads((ROOT / 'evaluations/sources.json').read_text())
 specs = [('osaka','word_osaka_guideline',1,[(340,365),(380,384)], [70,334,530,424],70.944,524.5,81.504,346.13,18,18),
          ('libreoffice','lo_migration_ja',9,[(520,570),(590,637)], [55,512,541,710],56.8,539,67.3,526.1,21,26.5)]
 result = dict(schema='paragraph-frame-comparison-1',baseline_commit='8fa868f',
  policy=dict(region_fixed_before_runs=True, no_page_extension=True, originals_independent=True,
   font='Both arms may use the same explicitly approved whole-paragraph Droid Sans Fallback substitution; A may retain original glyphs.',
   whitespace='Trailing source spaces remain part of the logical paragraph; PDF line breaks are presentation only.',
   paragraphs='Manual selection of exactly two source paragraphs; infer subsequent PDF-alone boundaries from first-line indent. No owner-based editing boundary.',
   region='Includes two blank Osaka line bands, but no attachment heading; LO ends at y710 before footer. Foreign spaces are retained.',
   rerender='144 dpi, exact fixed frame mask on target page; all other pages unmasked; no-op all pages unmasked.'),
  font=dict(origin="pymupdf.Font('cjk').buffer", name=pymupdf.Font('cjk').name, sha256=sha(pymupdf.Font('cjk').buffer),relation='substituted',font_index=0), documents={},requests=[])
 for key,sid,page,bands,frame,left,right,indent,baseline,leading,gap in specs:
  source=next(s for s in sources if s['id']==sid); path=ROOT/source['path']; assert sha(path.read_bytes())==source['sha256']
  c=ContentPage(path,page); assert not c.errors
  marked=observe_marked_content(path,page)
  paras=[]
  for lo,hi in bands:
   ids=[i for i,g in enumerate(c.actual) if lo<=g['origin'][1]<=hi]
   chars=[dict(id=i,text=c.actual[i]['unicode'],origin=list(c.actual[i]['origin']),bbox=list(c.actual[i]['bbox']),gid=c.actual[i]['gid']) for i in ids]
   events=[e for e in c.events if any(i in ids for ch in e.chars for i in ch.source_orders)]
   scopes=[s for s in marked['scopes'] if any(s['content_byte_range'][0]<=e.operator.start<s['content_byte_range'][1] for e in events)]
   paras.append(dict(text=''.join(g['text'] for g in chars),glyphs=chars,source_line_baselines=sorted({round(g['origin'][1],3) for g in chars}),
    source_events=[dict(start=e.operator.start,end=e.operator.end,font=e.state.font.name,font_xref=e.state.font.xref,size=e.state.size,text=''.join(ch.text for ch in e.chars)) for e in events],
    scopes=[dict(mcid=s['association'].get('mcid'),owner=s['association'].get('owner_reference'),begin=s['begin_byte_range'],end=s['end_byte_range']) for s in scopes]))
  a=_Analysis(path,page); line_ids=[l['id'] for l in a.lines if any(lo<=l['baseline']<=hi for lo,hi in bands)]
  result['documents'][key]=dict(source, page=page,frame=frame,left=left,right=right,first_x=indent,baseline=baseline,line_height=leading,paragraph_gap=gap,paragraphs=paras,line_ids=line_ids,tagged=bool(marked['scopes']),paragraph_structure_xrefs=[58,65] if key=='osaka' else None)
  c.close()
 for ident,key,old,new,again,replacement in REQUESTS:
  text=result['documents'][key]['paragraphs'][0]['text']; assert text.count(old)==1
  start=text.index(old); ids=[g['id'] for g in result['documents'][key]['paragraphs'][0]['glyphs'][start:start+len(old)]]
  result['requests'].append(dict(id=ident,document=key,old=old,new=new,start=start,end=start+len(old),glyph_ids=ids,expected=text.replace(old,new),reedit_old=again,reedit_new=replacement))
 MANIFEST.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:dict(texts=[p['text'] for p in d['paragraphs']],frame=d['frame'],line_ids=d['line_ids']) for k,d in result['documents'].items()},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
