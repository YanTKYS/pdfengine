"""Freeze explicit glyph sets after visual review; never edit a guessed Box."""
from dataclasses import asdict
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from pdfeditor.selection import inspect_selection_source, make_selection, resolve_selection

BASE=Path(__file__).resolve().parent
CORPUS=BASE.parent/"realpdf/corpus"
# Physical lines are the selector UI only. The persisted contract is SHA+page+
# explicit source glyph IDs, and the generated preview is reviewed by a human.
SPECS=[
 ("word_osaka_fire_notice",1,[13,14],"First body paragraph; trailing whitespace paints excluded",None),
 ("word_osaka_guideline",1,[14,15],"Middle body paragraph, excluding greeting and final request",None),
 ("word_wakayama_guidelines",1,[16,17,18,19],"Complete first eligibility paragraph, excluding heading and next paragraph",None),
 ("word_takeo_notice",2,[4,5],"Complete first body paragraph",None),
 ("lo_migration_ja",1,[12],"One explicitly selected Japanese body line, not an inferred paragraph",None),
 ("lo_newfeatures_ja",1,[3,4],"Complete introductory paragraph",None),
 ("lo_enterprise_en",1,[15],"Independent standards bullet text; bullet mark excluded",None),
 ("print_canvia",1,[3],"Single title line; explicit line selection",None),
 ("print_ubiquiti",1,[2],"Header only; vector diagram body is outside text editing scope",None),
 ("print_fcc_ms",1,[2,3,4],"French introductory paragraph within yellow note frame",None),
 ("print_fcc_chrome",1,[6,7],"Complete intro paragraph, excluding popup and navigation",None),
 ("word_kyoto_questions",1,[5,6,7,8],"Complete question including first line; frame/answer excluded",425),
 ("word_niigata_hearing",1,[7],"Independent section heading",230),
 ("word_okinawa_procurement",1,[5,6],"Intro paragraph only; date and organization excluded",468),
 ("word_osaka_symposium",1,[1],"Negative: observed glyphs have no restored Unicode",None),
]


def main():
    cases=[];tiles=[]
    (BASE/"previews").mkdir(parents=True,exist_ok=True)
    for name,page,ids,note,width in SPECS:
        path=CORPUS/f"{name}.pdf";observed=inspect_selection_source(path,page)
        chosen=[];texts=[]
        for ident in ids:
            line=next(l for l in observed["lines"] if l["id"]==f"p{page}-l{ident}")
            indices=line["glyph_ids"].copy()
            while indices and observed["glyphs"][indices[-1]]["text"].isspace():indices.pop()
            while indices and observed["glyphs"][indices[0]]["text"].isspace():indices.pop(0)
            chosen.extend(indices);texts.append("".join(observed["glyphs"][i]["text"] for i in indices))
        manifest=make_selection(path,page,glyph_ids=chosen,explicit_width=width)
        selection=resolve_selection(path,manifest)
        with pymupdf.open(path) as doc:
            rect=pymupdf.Rect(selection.bbox.tuple())+(-8,-8,8,8)
            pix=doc[page-1].get_pixmap(dpi=144,clip=rect,alpha=False)
            image=Image.frombytes("RGB",(pix.width,pix.height),pix.samples)
        image.save(BASE/"previews"/f"{name}.png")
        cases.append({"id":name,"source":str(path.relative_to(BASE.parent.parent)),"selection":manifest,
                      "logical_lines":texts,"review_note":note,"bbox":asdict(selection.bbox),
                      "line_selector_ids":[f"p{page}-l{i}" for i in ids],"preview":f"previews/{name}.png"})
        tile=Image.new("RGB",(1100,image.height+28),"#eeeeee")
        ImageDraw.Draw(tile).text((6,5),name,fill="black");tile.paste(image,(6,25));tiles.append(tile)
    (BASE/"selections.json").write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding="utf-8")
    for start in range(0,len(tiles),5):
        batch=tiles[start:start+5];sheet=Image.new("RGB",(1100,sum(t.height for t in batch)),"white");y=0
        for tile in batch:sheet.paste(tile,(0,y));y+=tile.height
        sheet.save(BASE/"previews"/f"contact-{start//5+1}.png")
    print(f"Prepared {len(cases)} explicit selections for review")


if __name__=="__main__":main()
