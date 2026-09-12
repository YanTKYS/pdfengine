from copy import deepcopy

from pypdf import PdfReader,PdfWriter

from pdfeditor import paint_provenance as paints
from pdfeditor.proof_session import proof_session
from test_attributed import source_pdf


def test_proofs_reuse_identical_revision_but_cannot_be_mutated_or_cross_operations(tmp_path,monkeypatch):
    source=source_pdf(tmp_path,b'.8 g 10 10 20 20 re f .5 g 50 10 20 20 re f '
                      b'BT /Regular 12 Tf 20 200 Td (TEXT) Tj ET')
    catalog,_=paints._load_catalog(source,1)
    original=paints.program_pdf_bytes;calls=[]
    def count(*a,**kw):calls.append(1);return original(*a,**kw)
    monkeypatch.setattr(paints,'program_pdf_bytes',count)
    @proof_session
    def run():
        first=paints.prove_path_paint(source,1,catalog['paths'][0]['id'],catalog=catalog)
        assert first['status']=='proven'
        first['status']='corrupt';first['paints'][0]['opacity']=0
        repeated=paints.prove_path_paint(source,1,catalog['paths'][0]['id'],catalog=catalog)
        assert repeated['status']=='proven' and repeated['paints'][0]['opacity']==1
        assert paints.prove_path_paint(source,1,catalog['paths'][1]['id'],catalog=catalog)['status']=='proven'
        bad=deepcopy(catalog);bad['program_sha256']='wrong'
        assert paints.prove_path_paint(source,1,catalog['paths'][0]['id'],catalog=bad)['status']=='refused'
    run();assert len(calls)==3  # one full-save control, two distinct deletion probes
    run();assert len(calls)==6  # next operation collects its own evidence


def test_same_path_with_changed_pdf_never_reuses_old_catalog(tmp_path):
    source=source_pdf(tmp_path)
    @proof_session
    def run():
        before,_=paints._load_catalog(source,1)
        writer=PdfWriter(clone_from=PdfReader(source));writer.add_metadata({'/Title':'changed revision'})
        changed=tmp_path/'changed.pdf';writer.write(changed);source.write_bytes(changed.read_bytes())
        after,_=paints._load_catalog(source,1)
        assert before['source_sha256']!=after['source_sha256']
    run()


def test_duplicate_paint_refusal_survives_reuse(tmp_path):
    source=source_pdf(tmp_path,b'.8 g 10 10 20 20 re f 10 10 20 20 re f')
    @proof_session
    def run():
        catalog,_=paints._load_catalog(source,1);ident=catalog['paths'][0]['id']
        a=paints.prove_path_paint(source,1,ident,catalog=catalog)
        b=paints.prove_path_paint(source,1,ident,catalog=catalog)
        assert a==b and a['status']=='refused' and 'ambiguous' in a['reason']
    run()
