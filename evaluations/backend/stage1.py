"""External corpus Stage 1; downstream gates require MuPDF AND Poppler equality."""
import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys

from PIL import Image, ImageChops
import pymupdf

from pdfeditor.content_stream import ContentPage
from pdfeditor.replay import no_op_replay
from pdfeditor.selection import source_sha
from evaluations.realpdf.evaluate import DEFAULT_POPPLER, DEFAULT_PYPDF, poppler_render, pixel_diff, independent_text, write_json

BASE=Path(__file__).resolve().parent
PROJECT=BASE.parent.parent


def state_by_glyph(content):
    def clean(v):
        if isinstance(v,dict):return {k:clean(a) for k,a in v.items() if k!="at"}
        if isinstance(v,(list,tuple)):return [clean(a) for a in v]
        if isinstance(v,float):return round(v,7)
        return v
    records={}
    fonts={}
    for event in content.events:
        if not event.state.font or event.state.font.error:continue
        font=event.state.font
        if font.xref not in fonts:
            from pdfeditor.content_stream import digest
            fonts[font.xref]={"basefont":font.basefont,"widths":digest(json.dumps(font.widths,sort_keys=True).encode()),
                              "default_width":font.default,"unit":font.unit,
                              "font_program":digest(content.document.extract_font(font.xref)[3])}
        state=event.state.report();state.pop("font_xref")
        state["font_identity"]=fonts[font.xref]
        for c in event.chars:
            for i in c.source_orders:
                records[i]={"code":c.code.hex(),"state":clean(state),
                            "text_matrix":clean(event.text_matrix),"line_matrix":clean(event.line_matrix),
                            "advance":round(c.advance,7),"pdf_width":c.pdf_width}
    return records


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--run-name",default="stage1")
    args=parser.parse_args();sys.stdout.reconfigure(encoding="utf-8")
    root=BASE/"runs"/args.run_name;root.mkdir(parents=True,exist_ok=False)
    selections=json.loads((BASE/"selections.json").read_text(encoding="utf-8"))
    results=[]
    write_json(root/"environment.json",{"pymupdf":pymupdf.VersionBind,
        "engine_sha256":{p.name:source_sha(p) for p in (PROJECT/"pdfeditor").glob("*.py")}})
    for case in selections:
        source=PROJECT/case["source"];selection=case["selection"];page=selection["page"]
        directory=root/case["id"];directory.mkdir()
        result={"case":case,"stage":1,"status":"pending","later_stages_allowed":False}
        try:
            report=no_op_replay(source,directory/"replayed.pdf",selection,removal_output=directory/"removed.pdf")
            before=ContentPage(source,page);after=ContentPage(directory/"replayed.pdf",page)
            old_states,new_states=state_by_glyph(before),state_by_glyph(after)
            differences=[i for i in selection["glyph_ids"] if old_states.get(i)!=new_states.get(i)]
            result["paint_state_compare"]={"different_glyphs":differences,"passed":not differences,
                                           "properties":"codes, font resource, CTM, operator text/line matrices, Tc/Tw/Tz/TL/Ts/Tr, fill/stroke, opacity, clip path+scope, other tracked graphics state, /W advance"}
            before.close();after.close()
            result["poppler"]={}
            for label,pdf in [("before",source),("after",directory/"replayed.pdf"),("removed",directory/"removed.pdf")]:
                result["poppler"][label]=poppler_render(DEFAULT_POPPLER,pdf,page,directory/f"{label}.png",dpi=144)
            if result["poppler"]["before"]["rendered"] and result["poppler"]["after"]["rendered"]:
                with Image.open(directory/"before.png") as a,Image.open(directory/"after.png") as b:
                    result["poppler"]["diff"]=pixel_diff(a,b,dpi=144)
                    ImageChops.difference(a.convert("RGB"),b.convert("RGB")).save(directory/"difference.png")
            original_text=independent_text(DEFAULT_PYPDF,source,directory/"pypdf_before.json")
            replay_text=independent_text(DEFAULT_PYPDF,directory/"replayed.pdf",directory/"pypdf_after.json")
            removed_text=independent_text(DEFAULT_PYPDF,directory/"removed.pdf",directory/"pypdf_removed.json")
            result["independent_text_equal"]=not original_text.get("error") and original_text.get("pages")==replay_text.get("pages")
            result["independent_removed_extracted"]=not removed_text.get("error")
            selected_unicode="".join(item["unicode"] for item in report.get("selected_before", []))
            removed_unicode="".join("".join(page.split()) for page in removed_text.get("pages", []))
            result["removed_selected_unicode_absent"]=(not selected_unicode or selected_unicode not in removed_unicode)
            report["removed_selected_unicode_absent"]=result["removed_selected_unicode_absent"]
            poppler_ok=result["poppler"].get("diff",{}).get("all_changed_pixels",-1)==0
            result["status"]="passed" if poppler_ok and not differences and result["independent_text_equal"] and result["removed_selected_unicode_absent"] else "failed_fidelity"
            result["later_stages_allowed"]=result["status"]=="passed"
            report["poppler_stage1_passed"]=poppler_ok
            report["paint_state_compare_passed"]=not differences
            report["independent_text_equal"]=result["independent_text_equal"]
            report["stage1_passed"]=result["later_stages_allowed"]
            report["replayed_sha256"]=source_sha(directory/"replayed.pdf")
            result["report"]=report
            write_json(directory/"gate.json",report)
        except Exception as exc:
            result.update(status="rejected",error_type=type(exc).__name__,error=str(exc))
        result["source_unchanged"]=source_sha(source)==selection["source_sha256"]
        write_json(directory/"result.json",result);results.append(result)
        write_json(root/"results.json",results)
        print(json.dumps({"case":case["id"],"status":result["status"],"error":result.get("error"),
                          "state_diff":result.get("paint_state_compare",{}).get("different_glyphs"),
                          "poppler_changed":result.get("poppler",{}).get("diff",{}).get("all_changed_pixels")},ensure_ascii=False),flush=True)
    print(dict(Counter(r["status"] for r in results)))


if __name__=="__main__":main()
