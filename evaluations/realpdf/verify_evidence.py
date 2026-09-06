"""Check corpus immutability, evaluated engine hashes, and recorded invariants."""
import json
from pathlib import Path

from evaluations.realpdf.evaluate import BASE, PROJECT, sha, write_json


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    originals=[]
    for path in sorted((BASE/"corpus").glob("*.pdf")):
        inv=read(BASE/"inventory"/f"{path.stem}.json")
        assert sha(path)==inv["sha256"],path
        originals.append({"source":path.stem,"sha256":sha(path),"pages":len(inv["pages"])})
    versions={}
    for run in ["baseline","guarded","supplemental"]:
        root=BASE/"runs"/run
        env=read(root/"environment.json")
        code=BASE/"baseline"/"pdfeditor" if run=="baseline" else PROJECT/"evaluations/backend/previous_stage2/pdfeditor"
        for filename,digest in env["engine_sha256"].items():
            assert sha(code/filename)==digest,(run,filename)
        results=read(root/"results.json")
        for r in results:
            assert r["input_unchanged"]
            assert r["input_sha256"]==sha(BASE/"corpus"/f"{r['case']['source']}.pdf")
            if r["status"]=="rejected":
                assert not (root/r["case"]["id"]/"edited.pdf").exists()
            elif run!="baseline":
                assert r["written_geometry"]["passed"] and r["security"]["preserved"]
                assert r["untouched_text_signatures_retained"] and r["removal_audit"]["exact_signature_match"]
                assert r["images_preserved"] and r["drawings_preserved"]
                assert r["pypdf"]["new_text_present"] and not r["pypdf"]["error"]
                assert all(d["outside_changed_pixels"]==0 for d in r["mupdf_all_pages_diff"])
                assert r["poppler"]["diff"]["outside_changed_pixels"]==0
        versions[run]={"file_hashes_matched":len(env["engine_sha256"]),"cases":len(results),
                       "written":sum(r["status"]=="written" for r in results)}
    result={"originals":originals,"total_pages":sum(r["pages"] for r in originals),
            "engine_versions":versions,"recorded_invariants_passed":True,
            "scope":"Recomputed source/code hashes; checked stored extraction/render/geometry audits. This does not rerun rendering or replace visual/semantic review."}
    write_json(BASE/"verification.json",result)
    print(f"Verified {len(originals)} original hashes, {result['total_pages']} pages, all 3 engine snapshots/runs, and current written-output invariants.")


if __name__=="__main__":main()
