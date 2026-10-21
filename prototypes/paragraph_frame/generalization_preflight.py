"""Freeze additional inputs/requests/oracles before implementing the B2 path.

Oracle geometry is evaluation-only and must not be passed to B2 proposal APIs.
"""
import hashlib
import json
from pathlib import Path
from pdfeditor.content_stream import ContentPage
from pdfeditor.page_proposal import _Analysis
from pdfeditor.marked_content import observe_marked_content

DEST=Path('evaluations/paragraph_frame_generalization/manifest.json')
NEW=[
 ('F1','fire','標記災害','本件災害','same-length','本件災害','今回災害'),
 ('F2','fire','感染症対策を円滑かつ適切に実施いただくよう','感染症対策を適切に実施するよう','shorten','適切に','確実に'),
 ('F3','fire','感染症対策を円滑かつ適切に実施いただくようお願いします。','感染症対策を円滑かつ適切に実施いただくようお願いします。関係機関で情報を共有し、支援が必要な方に確実に対応できるよう準備を進めてください。','grow-wrap','情報を共有','情報を確認'),
 ('F4','fire','地域の実情に応じ、別紙に留意の上、','地域の実情に合わせて、別紙の内容に留意し、','cross-show','内容に留意','内容を確認'),
 ('N1','newfeatures','注目の新機能','便利な新機能','same-length','便利な','有用な'),
 ('N2','newfeatures','追加・更新された幾つかの注目の新機能','主な新機能','shorten','主な','便利な'),
 ('N3','newfeatures','ご紹介します。','ご紹介します。導入前に変更点を確認し、普段の作業で必要な機能を試してください。保存した文書の表示や印刷結果も確認すると安心です。','grow-wrap','変更点を確認','変更点を点検'),
 ('N4','newfeatures','LibreOffice 4.1.1が8月下旬に','LibreOffice 4.1.1は8月末に','cross-show','8月末に','8月下旬に'),
]


def main():
 old=json.loads(Path('evaluations/architecture_poc/manifest.json').read_text())
 sources=json.loads(Path('evaluations/sources.json').read_text())
 result=dict(schema='paragraph-frame-generalization-1',baseline_commit='54219cc4d35c1ea4bce6884c2a1fe150f641c92f',
  prior_manifest_sha256=hashlib.sha256(Path('evaluations/architecture_poc/manifest.json').read_bytes()).hexdigest(),
  policy=dict(frozen_before_b2_implementation=True,no_case_replacement=True,
   b2_inputs='source PDF, page, selected text/occurrence, replacement; optional explicit substitute font approval. No oracle geometry or source-name special cases.',
   approvals='Geometry is proposed by B2 then explicitly accepted. Ambiguity must be exposed; no automatic expansion after failure.',
   fairness='Fixed oracle mask and maximum movement envelope used for all arms; source font reuse and same explicit substitute allowed. B1 remains the unmodified PR86 two-paragraph engine.',
   protected='All content outside oracle paragraphs, including non-text foreground inside the mask, remains protected. Follower movement is allowed only within the frozen envelope.',
   line_end_spaces='Existing trailing space glyphs may remain at a line edge; meaningful internal spaces are retained.'),font=old['font'],documents={},requests=[])
 for key,spec in old['documents'].items():
  result['documents'][key]=dict(spec)
  result['documents'][key]['role']='regression'
 for c in old['requests']:result['requests'].append(dict(c,category='regression'))
 specs=[('fire','word_osaka_fire_notice',[(280,306),(320,346),(360,386)], [52,272,550,431],53.88,547.2,64.464,284.57,19.8,19.8),
        ('newfeatures','lo_newfeatures_ja',[(125,146)], [27,116,569,175],28.5,567,34.1,128.8,15.7,15.7)]
 for key,sid,bands,frame,left,right,first,base,leading,gap in specs:
  source=next(s for s in sources if s['id']==sid);path=Path(source['path']);assert hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256']
  c=ContentPage(path,1);assert not c.errors;marks=observe_marked_content(path,1);paras=[]
  for lo,hi in bands:
   ids=[i for i,g in enumerate(c.actual) if lo<=g['origin'][1]<=hi]
   events=[e for e in c.events if any(i in ids for ch in e.chars for i in ch.source_orders)]
   paras.append(dict(text=''.join(c.actual[i]['unicode'] for i in ids),glyph_ids=ids,
     glyphs=[dict(id=i,text=c.actual[i]['unicode'],origin=list(c.actual[i]['origin']),bbox=list(c.actual[i]['bbox']),gid=c.actual[i]['gid']) for i in ids],
     source_line_baselines=sorted({round(c.actual[i]['origin'][1],3) for i in ids}),
     source_events=[dict(start=e.operator.start,end=e.operator.end,font=e.state.font.name,size=e.state.size,text=''.join(ch.text for ch in e.chars)) for e in events]))
  analysis=_Analysis(path,1);line_ids=[l['id'] for l in analysis.lines if any(lo<=l['baseline']<=hi for lo,hi in bands)]
  result['documents'][key]=dict(source,role='holdout',page=1,frame=frame,left=left,right=right,first_x=first,baseline=base,line_height=leading,paragraph_gap=gap,paragraphs=paras,line_ids=line_ids,tagged=bool(marks['scopes']))
  c.close()
 for ident,key,find,replacement,category,again,newagain in NEW:
  spec=result['documents'][key];p=spec['paragraphs'][0];assert p['text'].count(find)==1,(ident,p['text'])
  start=p['text'].index(find);ids=p['glyph_ids'][start:start+len(find)]
  source=ContentPage(spec['path'],spec['page']);events=[e for e in source.events if any(set(ids).intersection(ch.source_orders) for ch in e.chars)]
  if category=='same-length':assert len(find)==len(replacement)
  if category=='cross-show':assert len(events)>=2
  result['requests'].append(dict(id=ident,document=key,category=category,old=find,new=replacement,start=start,end=start+len(find),glyph_ids=ids,
     selected_show_operators=len(events),expected=p['text'].replace(find,replacement),reedit_old=again,reedit_new=newagain))
  source.close()
 DEST.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:dict(paragraphs=[p['text'] for p in s['paragraphs']],frame=s['frame'],line_ids=s['line_ids']) for k,s in result['documents'].items() if s['role']=='holdout'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
