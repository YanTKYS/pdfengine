"""Unmodified Kyoto Q&A body continues before the next section on its existing page 2.

Run from the checkout with the public PDF and the SHA-verified OFL BIZ UDMincho font.
No evaluation-only slot construction, source rewriting or glyph mapping is used.
"""
import argparse
from pathlib import Path
import hashlib
import json
from io import BytesIO
import shutil
import subprocess

import pymupdf
from PIL import Image, ImageChops, ImageDraw
from pypdf import PdfReader
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import DecomposingRecordingPen

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage
from pdfeditor.continuation import confirm_continuation_destination, slot_id
from pdfeditor.continuation_review import review_continuation_geometry, build_continuation_boundary_confirmation_request
from pdfeditor.operator_nesting import audit as nesting_audit
from pdfeditor.page_proposal import propose_page_flow, accept_page_flow_report, replace_in_flow
from pdfeditor.proof_session import proof_session
from pdfeditor.selection import source_sha
from pdfeditor.shared_flow import confirm_shared_flow_continuations, edit_shared_flow, open_shared_flow
from pdfeditor.shaped_font import ShapedFont

SOURCE=Path(__file__).resolve().parents[1]/'realpdf/corpus/word_kyoto_questions.pdf'
URL='https://www.city.kyoto.lg.jp/kankyo/cmsfiles/contents/0000311/311254/kaitou_hp.pdf'
SHA='61ef6bb9aaf0e6c6099fc7b4b67de804d311855ec0265d4648d197fb75dfad46'
FONT_SHA='468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2'
FIND='この限りではありません。'
LONG=('この限りではありません。送付物の内容や数量に変更がある場合は、発送前に本市と必要な資料を確認してください。'
      '複数店舗分をまとめて発送する場合も、送付物の数量及び発送方法について事前に確認してください。')
SECOND=('事前に確認してください。','事前に確認をお願いします。')


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def compact(text):return ''.join(text.split())
def chars(page):return [c for span in page.get_texttrace() for c in span['chars']]
def outline(font,gid):
    glyphs=font.getGlyphSet();pen=DecomposingRecordingPen(glyphs)
    glyphs[font.getGlyphName(gid)].draw(pen)
    return pen.value


def codes(path,number):
    content=ContentPage(path,number)
    try:
        programs={}
        result={}
        for event in content.events:
            face=event.state.font
            if face.xref not in programs:
                programs[face.xref]=hashlib.sha256(content.document.extract_font(face.xref)[3]).hexdigest()
            for char in event.chars:
                for index in char.source_orders:
                    result[index]=(char.code.hex(),face.name,programs[face.xref])
        return result
    finally:content.close()


def audit(before,after,initial,state,report,provider):
    regions={r['page']:pymupdf.Rect(r['bounds']) for r in state['regions'].values()}
    errors=[];retained=provided=0;placements=[]
    with pymupdf.open(SOURCE) as original,pymupdf.open(before) as previous,pymupdf.open(after) as saved:
        assert len(original)==len(saved)==2
        for number,(a,b) in enumerate(zip(original,saved),1):
            region=regions[number]
            outside=lambda p:[(c[0],tuple(c[2]),tuple(c[3])) for c in chars(p) if not region.contains(pymupdf.Point(c[2]))]
            assert outside(a)==outside(b),'foreign Unicode/origins/bounds changed'
            draws=lambda p:[{k:v for k,v in d.items() if k!='seqno'} for d in p.get_drawings()]
            assert draws(a)==draws(b)
            assert a.get_image_info(hashes=True)==b.get_image_info(hashes=True)
            x,y=a.get_pixmap(dpi=144),b.get_pixmap(dpi=144)
            diff=ImageChops.difference(Image.frombytes('RGB',(x.width,x.height),x.samples),Image.frombytes('RGB',(y.width,y.height),y.samples))
            r=(region*pymupdf.Matrix(2,2)).irect
            ImageDraw.Draw(diff).rectangle((r.x0,r.y0,r.x1-1,r.y1-1),fill=(0,0,0))
            assert diff.getbbox() is None,'outside-region MuPDF pixels changed'
        for step in report['steps']:
            sid=step['slot_id'];slot=state['slots'][sid];number=state['regions'][slot['region_id']]['page']
            plan=report['plan']['fragments'][sid];glyphs=step['report']['glyph_plan']
            actual_all=chars(saved[number-1]);actual=[(i,c) for i,c in enumerate(actual_all) if regions[number].contains(pymupdf.Point(c[2]))]
            assert len(actual)==len(glyphs)
            assert ''.join(chr(c[0]) for _,c in actual)==''.join(g['unicode'] for g in glyphs)
            assert ''.join(chr(c[0]) for _,c in actual)==''.join(plan['text'][l['start']:l['end']].rstrip() for l in plan['lines'])
            old_codes,new_codes=codes(before,number),codes(after,number)
            old_chars=chars(previous[number-1]);fonts={f[4]:f[0] for f in saved[number-1].get_fonts(full=True)};programs={}
            for (index,c),g in zip(actual,glyphs):
                error=max(abs(c[2][k]-g['origin'][k]) for k in (0,1));errors.append(error);assert error<.002
                assert regions[number].contains(pymupdf.Rect(c[3]))
                assert new_codes[index][:2]==(g['code'],g['font_resource']) and c[1]==g['glyph_id']
                if g['source_index'] is not None:
                    old=g['source_index'];assert old_codes[old]==new_codes[index] and old_chars[old][1]==c[1]
                    retained+=1
                else:
                    alias=g['font_resource'].lstrip('/')
                    if alias not in programs:programs[alias]=TTFont(BytesIO(saved.extract_font(fonts[alias])[3]))
                    assert outline(programs[alias],c[1])==provider.outline(c[1])
                    assert programs[alias]['hmtx'][programs[alias].getGlyphName(c[1])][0]==provider.nominal_width(c[1])
                    provided+=1
            for font in programs.values():font.close()
            placements.append(dict(slot=sid,page=number,range=slot['range'],text=plan['text'],
                lines=[plan['text'][l['start']:l['end']].rstrip() for l in plan['lines']],
                baselines=[l['baseline'] for l in plan['lines']],glyphs=len(glyphs),retained=step['report']['retained_glyph_count'],
                provided=step['report']['provided_font_glyph_count'],dormant=slot['occupancy'] is None))
    reader=PdfReader(after);original=PdfReader(SOURCE)
    for number,page in enumerate(reader.pages,1):
        old=compact(original.pages[number-1].extract_text());new=compact(page.extract_text())
        was=''.join(s['binding']['paragraph']['text'] for s in initial['slots'].values() if initial['regions'][s['region_id']]['page']==number)
        now=''.join(s['binding']['paragraph']['text'] for s in state['slots'].values() if state['regions'][s['region_id']]['page']==number)
        if was:
            assert old.count(compact(was))==1;expected=old.replace(compact(was),compact(now))
        else:expected=compact(now)+old
        assert new==expected,'independent whole-page extraction changed'
        assert not nesting_audit(page.get_contents().get_data())['violations']
        assert '/StructParents' not in page
    assert '/StructTreeRoot' not in reader.trailer['/Root'] and '/StructTreeRoot' not in original.trailer['/Root']
    assert SOURCE.read_bytes().splitlines()[0]==after.read_bytes().splitlines()[0]
    return dict(placements=placements,max_origin_error_pt=max(errors,default=0),retained_glyphs=retained,provided_glyphs=provided,
        retained_codes_resources_gids_programs_equal=True,new_outlines_advances_equal_provider=True,
        unicode_mupdf_and_pypdf='exact (pypdf whitespace normalized)',outside_mupdf_changed_pixels=0,
        foreign_text_drawings_images_equal=True,all_glyph_bounds_inside_regions=True,structure='untagged; valid PDF 1.x operator nesting')


@proof_session
def run(output,font):
    output.mkdir(parents=True,exist_ok=False)
    assert source_sha(SOURCE)==SHA and source_sha(font)==FONT_SHA
    p=propose_page_flow(SOURCE,1,line_ids=['p1-l34','p1-l35'],font_candidates=[font],include_embedded_fonts=True,
        font_cache=output/'fonts',new_text={'style-1':LONG+SECOND[1]})
    write(output/'proposal.json',p)
    accepted=accept_page_flow_report(SOURCE,p,provider_choices={'style-1':dict(sha256=FONT_SHA,font_index=0,relation='substituted')})
    write(output/'source-acceptance.json',accepted['receipt'])
    state=accepted['state']
    region=dict(page=2,bounds=[p['region']['x'],54,p['region']['x']+p['region']['width'],82],
        x=p['region']['x'],width=p['region']['width'],first_baseline=70.91998291015625)
    review=review_continuation_geometry(SOURCE,2,region['bounds']);write(output/'geometry-review.json',review)
    rows=[c for g in review['groups'] for c in g['candidates']]
    # Explicit evaluator decision: boundary 0, after the opening cm and before q,
    # before all page 2 paints. Geometry is above the next section, never over it.
    selected=next(c for c in rows if c['ordinal']==0)
    assert selected['operator_context']=='cm -> boundary -> q' and selected['geometry']['checks_passed']
    request=build_continuation_boundary_confirmation_request(review,boundary_id=selected['boundary_id'],
        destination_id='next-page',paragraph_id='P1',region_id='R2')
    d=confirm_continuation_destination(SOURCE,**request['confirm_kwargs']);write(output/'destination.json',d)
    initial=state=confirm_shared_flow_continuations(SOURCE,state,regions={'R2':region},region_order=['R2'],continuation_destinations={'next-page':d})
    write(output/'accepted-flow.json',state)
    original_text=state['paragraphs']['P1']['logical']['text'];expected=original_text;source=SOURCE;sid=slot_id(d)
    provider=ShapedFont(font)
    result=dict(verdict='NOT VALIDATED',source=dict(url=URL,sha256=SHA,line_ids=['p1-l34','p1-l35'],text=original_text),
        source_reproduction=accepted['receipt']['reproduction'],regions=state['regions'],boundary=selected,
        destination_authority=d['authority'],provider=dict(sha256=FONT_SHA,face=0,appearance='explicit substitution; no source equivalence claim'),stages=[])
    replacements=[(FIND,LONG),SECOND,(LONG.replace(*SECOND),FIND),(FIND,LONG),None]
    for index,(name,edit) in enumerate(zip(('grow','second','shorten','regrow','noop'),replacements),1):
        change=replace_in_flow(state,*edit) if edit else {}
        if name=='second':
            start=expected.index(edit[0]);assert state['slots'][sid]['range'][0]<=start<state['slots'][sid]['render_end']
        pdf,model=output/f'{index}-{name}.pdf',output/f'{index}-{name}.json'
        report=edit_shared_flow(source,state,pdf,model,change);write(output/f'{index}-report.json',report)
        opened=open_shared_flow(pdf,model);assert opened['status']=='restored',opened.get('reason')
        state=opened['state'];expected=expected.replace(*edit) if edit else expected
        assert state['paragraphs']['P1']['logical']['text']==expected
        checked=audit(source,pdf,initial,state,report,provider)
        if name=='shorten':
            assert expected==original_text and state['slots'][sid]['occupancy'] is None
            with pymupdf.open(SOURCE) as a,pymupdf.open(pdf) as b:assert a[1].get_pixmap(dpi=144).samples==b[1].get_pixmap(dpi=144).samples
        else:assert state['slots'][sid]['occupancy'] is not None
        if name=='second':assert next(v for v in checked['placements'] if v['page']==2)['retained']>0
        if name=='noop':
            with pymupdf.open(source) as a,pymupdf.open(pdf) as b:assert [p.get_pixmap().samples for p in a]==[p.get_pixmap().samples for p in b]
        result['stages'].append(dict(stage=name,reopen='restored',text=expected,edit=edit,audit=checked))
        print(name,'restored',[(v['page'],len(v['lines']),v['retained']) for v in checked['placements']],flush=True)
        source=pdf
    refusal_pdf,refusal_model=output/'overflow.pdf',output/'overflow.json'
    try:edit_shared_flow(source,state,refusal_pdf,refusal_model,replace_in_flow(state,LONG,LONG*2))
    except PdfError as exc:
        assert 'exceed all explicitly confirmed shared regions' in str(exc)
        result['overflow_refusal']=str(exc)
    else:raise AssertionError('overflow accepted')
    assert not refusal_pdf.exists() and not refusal_model.exists()
    assert source_sha(SOURCE)==SHA
    if shutil.which('pdftoppm'):
        for name,path in [('before',SOURCE),('after',source)]:
            subprocess.run(['pdftoppm','-scale-to','1600','-png',str(path),str(output/name)],check=True,capture_output=True)
        result['poppler']='both pages rendered for before/after inspection'
    provider.font.close()
    result.update(verdict='REAL-WORLD MULTI-REGION PDF EDITING VALIDATED — PASS',original_sha_unchanged=True)
    write(output/'summary.json',result);print(result['verdict'],flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--font',type=Path,required=True)
    args=parser.parse_args();run(args.output,args.font)
