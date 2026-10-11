"""Whole related LibreOffice body paragraphs: push-down and follower continuation.

The original PDF is never rewritten before B1 proposal/acceptance. Every region
and provider is explicitly accepted through existing production APIs.
"""
import argparse
from io import BytesIO
import json
import logging
from pathlib import Path
import shutil
import subprocess
import pymupdf
from PIL import Image,ImageChops,ImageDraw
from pypdf import PdfReader
from fontTools.ttLib import TTFont
from pdfeditor.backend import PdfError
from pdfeditor.page_proposal import propose_page_flow,accept_page_flow_report,replace_in_flow
from pdfeditor.shared_flow import confirm_shared_flow_continuations,edit_shared_flow,open_shared_flow
from pdfeditor.continuation import confirm_continuation_destination,slot_id
from pdfeditor.continuation_review import review_continuation_geometry,build_continuation_boundary_confirmation_request
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from pdfeditor.shaped_font import ShapedFont
from pdfeditor.operator_nesting import audit as nesting_audit
from evaluations.story_flow.evaluate import annotation_fingerprint
from .continuation_edit import codes,outline,chars,compact,write,FONT_SHA

SOURCE=Path(__file__).resolve().parents[1]/'realpdf/corpus/lo_migration_ja.pdf'
URL='https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf'
SHA='13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3'
FIND='抵抗します。'
LONG=('抵抗します。そこで、移行を進める前に、利用者が現在の作業で大切にしている点を聞き取り、変更後も維持する手順を確認します。'
'担当者は具体的な文書を使って操作を説明し、質問を受け付ける時間と相談先をあらかじめ案内します。'
'最初からすべての作業を変更するのではなく、日常的な業務から順番に試し、困った点を記録して共有します。'
'管理者と利用者が定期的に話し合い、改善した点と残る課題を確認することも必要です。'
'研修後にも実際の作業を支援し、習得の速さが異なる人にも十分な時間を設けます。'
'こうした対話を継続することで、変化に対する不安を減らし、移行計画への理解を深めることができます。'
'利用者の意見は担当部署で共有し、改善の優先順位と対応時期を決め、進捗を分かりやすく伝えます。'
'実施後は相談内容を整理し、次の研修や手順書の見直しに反映して、継続的な支援につなげます。')
SECOND=('不信と批評','不信や誤解と批評')


def audit(before,after,initial,state,report,provider):
    regions={}
    for r in state['regions'].values():regions.setdefault(r['page'],[]).append(pymupdf.Rect(r['bounds']))
    reader,original=PdfReader(after),PdfReader(SOURCE)
    errors=[];placements=[]
    with pymupdf.open(SOURCE) as orig,pymupdf.open(before) as previous,pymupdf.open(after) as saved:
        assert len(orig)==len(saved)==10
        for number,(a,b) in enumerate(zip(orig,saved),1):
            page_regions=regions.get(number,[])
            inside=lambda point:any(r.contains(pymupdf.Point(point)) for r in page_regions)
            outside=lambda p:[(c[0],tuple(c[2]),tuple(c[3])) for c in chars(p) if not inside(c[2])]
            assert outside(a)==outside(b),'foreign text changed'
            draws=lambda p:[{k:v for k,v in d.items() if k!='seqno'} for d in p.get_drawings()]
            assert draws(a)==draws(b) and a.get_image_info(hashes=True)==b.get_image_info(hashes=True)
            links=lambda p:[{k:v for k,v in v.items() if k!='xref'} for v in p.get_links()]
            assert links(a)==links(b) and annotation_fingerprint(original,number)==annotation_fingerprint(reader,number)
            x,y=a.get_pixmap(dpi=144),b.get_pixmap(dpi=144)
            diff=ImageChops.difference(Image.frombytes('RGB',(x.width,x.height),x.samples),Image.frombytes('RGB',(y.width,y.height),y.samples))
            if page_regions:
                for region in page_regions:
                    r=(region*pymupdf.Matrix(2,2)).irect
                    ImageDraw.Draw(diff).rectangle((r.x0,r.y0,r.x1-1,r.y1-1),fill=(0,0,0))
                owned={i for s in state['slots'].values() if state['regions'][s['region_id']]['page']==number for i in s['binding']['paragraph']['selection']['glyph_ids']}
                observed={i for i,c in enumerate(chars(b)) if inside(c[2])}
                assert owned==observed,'unaccounted region glyphs'
            assert diff.getbbox() is None,'outside-region pixels changed'
            expected=compact(original.pages[number-1].extract_text())
            for pid in initial['flow']['paragraphs']:
                was=''.join(s['binding']['paragraph']['text'] for s in initial['slots'].values() if s['paragraph_id']==pid and initial['regions'][s['region_id']]['page']==number)
                now=''.join(s['binding']['paragraph']['text'] for s in state['slots'].values() if s['paragraph_id']==pid and state['regions'][s['region_id']]['page']==number)
                if was:
                    assert expected.count(compact(was))==1
                    expected=expected.replace(compact(was),compact(now))
                elif now:expected=compact(now)+expected
            assert compact(reader.pages[number-1].extract_text())==expected,'pypdf Unicode differs'
            assert not nesting_audit(reader.pages[number-1].get_contents().get_data())['violations']
        for step in report['steps']:
            sid=step['slot_id'];slot=state['slots'][sid];number=state['regions'][slot['region_id']]['page']
            plan=report['plan']['fragments'][sid];glyphs=step['report']['glyph_plan']
            all_chars=chars(saved[number-1]);ids=slot['binding']['paragraph']['selection']['glyph_ids'];actual=[(i,all_chars[i]) for i in ids]
            assert len(actual)==len(glyphs)
            assert ''.join(chr(c[0]) for _,c in actual)==''.join(g['unicode'] for g in glyphs)
            assert ''.join(chr(c[0]) for _,c in actual)==''.join(plan['text'][l['start']:l['end']].rstrip() for l in plan['lines'])
            old_codes,new_codes=codes(before,number),codes(after,number)
            old_chars=chars(previous[number-1]);fonts={f[4]:f[0] for f in saved[number-1].get_fonts(full=True)};programs={}
            for (index,c),g in zip(actual,glyphs):
                error=max(abs(c[2][k]-g['origin'][k]) for k in (0,1));errors.append(error);assert error<.002
                assert pymupdf.Rect(state['regions'][slot['region_id']]['bounds']).contains(pymupdf.Rect(c[3]))
                assert new_codes[index][:2]==(g['code'],g['font_resource']) and c[1]==g['glyph_id']
                if g['source_index'] is not None:
                    old=g['source_index'];assert old_codes[old]==new_codes[index] and old_chars[old][1]==c[1]
                else:
                    alias=g['font_resource'].lstrip('/')
                    if alias not in programs:programs[alias]=TTFont(BytesIO(saved.extract_font(fonts[alias])[3]))
                    assert outline(programs[alias],c[1])==provider.outline(c[1])
                    assert programs[alias]['hmtx'][programs[alias].getGlyphName(c[1])][0]==provider.nominal_width(c[1])
            for f in programs.values():f.close()
            placements.append(dict(slot=sid,paragraph=slot['paragraph_id'],page=number,range=slot['range'],text=plan['text'],
                lines=[plan['text'][l['start']:l['end']].rstrip() for l in plan['lines']],baselines=[l['baseline'] for l in plan['lines']],
                line_x=[next(g['origin'][0] for g in plan['glyphs'] if g['start']==l['start']) for l in plan['lines']],
                retained=step['report']['retained_glyph_count'],provided=step['report']['provided_font_glyph_count']))
    assert '/StructTreeRoot' not in reader.trailer['/Root'] and '/StructTreeRoot' not in original.trailer['/Root']
    assert SOURCE.read_bytes().splitlines()[0]==after.read_bytes().splitlines()[0]
    return dict(placements=placements,max_origin_error_pt=max(errors),outside_mupdf_changed_pixels=0,
        foreign_text_drawings_images_links_annotations_equal=True,all_ten_pages_checked=True,
        unicode_mupdf_pypdf='exact (pypdf whitespace normalized)',retained_codes_resources_gids_programs_equal=True,
        provided_outlines_advances_equal=True,structure='untagged; PDF version/count and operator nesting preserved')


@proof_session
def run(output,font):
    output.mkdir(parents=True,exist_ok=False)
    assert source_sha(SOURCE)==SHA and source_sha(font)==FONT_SHA
    lines=[f'p9-l{i}' for i in range(18,24)]
    proposal=propose_page_flow(SOURCE,9,line_ids=lines,font_candidates=[font],include_embedded_fonts=True,
        font_cache=output/'fonts',new_text={f'style-{i}':LONG+SECOND[1] for i in range(1,5)})
    write(output/'proposal.json',proposal)
    accepted=accept_page_flow_report(SOURCE,proposal,provider_choices={s['id']:dict(sha256=FONT_SHA,font_index=0,relation='substituted') for s in proposal['styles']})
    assert [p['line_ids'] for p in proposal['paragraphs']]==[lines[:3],lines[3:]]
    state=accepted['state'];write(output/'acceptance.json',accepted['receipt'])
    r=dict(page=10,bounds=[proposal['region']['x'],110,proposal['region']['x']+proposal['region']['width'],137],
        x=proposal['region']['x'],width=proposal['region']['width'],first_baseline=122)
    review=review_continuation_geometry(SOURCE,10,r['bounds']);write(output/'review.json',review)
    selected=next(c for g in review['groups'] for c in g['candidates'] if c['ordinal']==0)
    assert selected['geometry']['checks_passed'] and selected['operator_context']=='w -> boundary -> q'
    request=build_continuation_boundary_confirmation_request(review,boundary_id=selected['boundary_id'],
        destination_id='following',paragraph_id='P2',region_id='R2')
    dest=confirm_continuation_destination(SOURCE,**request['confirm_kwargs'])
    initial=state=confirm_shared_flow_continuations(SOURCE,state,regions={'R2':r},region_order=['R2'],continuation_destinations={'following':dest})
    write(output/'initial.json',state)
    originals={pid:p['logical']['text'] for pid,p in state['paragraphs'].items()};expected=dict(originals)
    result=dict(verdict='NOT VALIDATED',source=dict(url=URL,sha256=SHA,line_ids=lines,paragraphs=originals),
        source_reproduction=accepted['receipt']['reproduction'],regions=state['regions'],boundary=selected,
        provider=dict(sha256=FONT_SHA,face=0,appearance='explicit substitution; no source equivalence claim'),stages=[])
    sid=slot_id(dest);source=SOURCE;provider=ShapedFont(font)
    edits=[{'P1':(FIND,LONG)},{'P2':SECOND},{'P1':(LONG,FIND),'P2':SECOND[::-1]},{'P1':(FIND,LONG)},{}]
    for index,(name,replacements) in enumerate(zip(('grow','second','shorten','regrow','noop'),edits),1):
        changes={}
        for pid,edit in replacements.items():
            changes.update(replace_in_flow(state,*edit,paragraph_id=pid,style_id='style-1'))
            expected[pid]=expected[pid].replace(*edit)
        if name=='second':assert state['slots'][sid]['range'][0]<=state['paragraphs']['P2']['logical']['text'].index(SECOND[0])
        pdf,model=output/f'{index}-{name}.pdf',output/f'{index}-{name}.json'
        report=edit_shared_flow(source,state,pdf,model,changes);write(output/f'{index}-report.json',report)
        opened=open_shared_flow(pdf,model);assert opened['status']=='restored',opened.get('reason');state=opened['state']
        assert {pid:p['logical']['text'] for pid,p in state['paragraphs'].items()}==expected
        checked=audit(source,pdf,initial,state,report,provider)
        if name=='grow':
            original_ids=initial['slots']['slot-1']['binding']['paragraph']['selection']['glyph_ids']
            current_ids=state['slots']['slot-1']['binding']['paragraph']['selection']['glyph_ids']
            with pymupdf.open(SOURCE) as x,pymupdf.open(pdf) as y:
                old,new=chars(x[8]),chars(y[8])
                delta=new[current_ids[0]][2][1]-old[original_ids[0]][2][1]
                error=max(abs(new[j][2][axis]-old[i][2][axis]-(delta if axis==1 else 0))
                          for i,j in zip(original_ids,current_ids) for axis in (0,1))
                assert len(current_ids)==99 and error<.002
                checked['unchanged_follower_translation_error_pt']=error
        a,b=[report['plan']['fragments'][s] for s in ('slot-0','slot-1')]
        gap=b['lines'][0]['baseline']-a['lines'][-1]['baseline']
        assert abs(gap-proposal['follows'][0]['minimum_baseline_gap'])<.002
        shift=b['lines'][0]['baseline']-proposal['paragraphs'][1]['first_baseline']
        if name=='shorten':
            assert state['slots'][sid]['occupancy'] is None and expected==originals
            with pymupdf.open(SOURCE) as x,pymupdf.open(pdf) as y:assert x[9].get_pixmap(dpi=144).samples==y[9].get_pixmap(dpi=144).samples
        else:assert state['slots'][sid]['occupancy'] is not None and shift>0 and len(a['lines'])>3
        if name=='noop':
            with pymupdf.open(source) as x,pymupdf.open(pdf) as y:assert [p.get_pixmap().samples for p in x]==[p.get_pixmap().samples for p in y]
        result['stages'].append(dict(stage=name,reopen='restored',edits=replacements,following_push_down_pt=shift,
            baseline_gap_pt=gap,audit=checked))
        print(name,'restored','push',shift,[(p['paragraph'],p['page'],len(p['lines'])) for p in checked['placements']],flush=True)
        source=pdf
    refused_pdf,refused_model=output/'overflow.pdf',output/'overflow.json'
    try:edit_shared_flow(source,state,refused_pdf,refused_model,replace_in_flow(state,LONG,LONG*2,paragraph_id='P1',style_id='style-1'))
    except PdfError as exc:result['overflow_refusal']=str(exc)
    else:raise AssertionError('unowned P1 continuation accepted')
    assert not refused_pdf.exists() and not refused_model.exists() and source_sha(SOURCE)==SHA
    if shutil.which('pdftoppm'):
        for name,path in [('before',SOURCE),('after',source)]:
            subprocess.run(['pdftoppm','-f','9','-l','10','-scale-to','1600','-png',str(path),str(output/name)],check=True,capture_output=True)
        changed={}
        with pymupdf.open(SOURCE) as doc:
            for number in (9,10):
                a=Image.open(output/f'before-{number:02}.png').convert('RGB')
                b=Image.open(output/f'after-{number:02}.png').convert('RGB')
                diff=ImageChops.difference(a,b)
                r=next(r['bounds'] for r in state['regions'].values() if r['page']==number)
                mask=(pymupdf.Rect(r)*pymupdf.Matrix(a.width/doc[number-1].rect.width,a.height/doc[number-1].rect.height)).irect
                ImageDraw.Draw(diff).rectangle((mask.x0,mask.y0,mask.x1-1,mask.y1-1),fill=(0,0,0))
                assert diff.getbbox() is None,'Poppler outside-region pixels changed'
                changed[str(number)]=0
        result['poppler_outside_changed_pixels']=changed
    provider.font.close()
    result.update(verdict='REAL-WORLD OWNED BODY REFLOW AND CONTINUATION VALIDATED — PASS',original_sha_unchanged=True)
    write(output/'summary.json',result);print(result['verdict'],flush=True)
    return result

if __name__=='__main__':
    logging.getLogger('fontTools.ttLib.tables._h_e_a_d').setLevel(logging.ERROR)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--font',type=Path,required=True)
    args=parser.parse_args();run(args.output,args.font)
