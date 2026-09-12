"""Explicit multi-page evaluation must still detect edits on other pages."""
from pypdf import PdfReader,PdfWriter
import pytest

from evaluations.backend import followup
from test_attributed import source_pdf


@pytest.mark.parametrize('third_changed',[False,True])
def test_declared_changed_pages_do_not_hide_an_undeclared_page(tmp_path,monkeypatch,third_changed):
    source=source_pdf(tmp_path);r=PdfReader(source);w=PdfWriter(clone_from=r);w.add_page(r.pages[0]);w.write(source)
    w=PdfWriter(clone_from=PdfReader(source))
    # Detach each stream to avoid sharing the clone's page-one content.
    from pypdf.generic import DecodedStreamObject,NameObject
    for i in [0,1]+([2] if third_changed else []):
        stream=DecodedStreamObject();stream.set_data(b'BT /Regular 12 Tf 20 100 Td (CHANGED) Tj ET')
        w.pages[i][NameObject('/Contents')]=w._add_object(stream)
    target=tmp_path/'edited.pdf';w.write(target)
    monkeypatch.setattr(followup,'poppler_render',lambda *a,**k:{'rendered':False})
    default=followup.render_audit(source,target,1,tmp_path,(0,0,0,0))
    assert default['outside_page_text_equal'] is False and default['outside_page_mupdf_pixels_equal'] is False
    explicit=followup.render_audit(source,target,1,tmp_path,(0,0,0,0),edited_pages={1,2})
    assert explicit['declared_edited_pages']==[1,2]
    assert explicit['outside_page_text_equal'] is (not third_changed)
    assert explicit['outside_page_mupdf_pixels_equal'] is (not third_changed)


def test_edited_page_declarations_need_existing_pages(tmp_path,monkeypatch):
    source=source_pdf(tmp_path)
    monkeypatch.setattr(followup,'poppler_render',lambda *a,**k:{'rendered':False})
    with pytest.raises(followup.EvaluationFailure):
        followup.render_audit(source,source,1,tmp_path,(0,0,0,0),edited_pages={9})


def test_annotation_graph_compares_destination_and_appearance_not_xref(tmp_path):
    import pymupdf
    from pypdf.generic import NameObject,DictionaryObject,TextStringObject
    from evaluations.story_flow.evaluate import annotation_fingerprint
    source=source_pdf(tmp_path)
    with pymupdf.open(source) as doc:
        doc[0].insert_link({'kind':pymupdf.LINK_URI,'from':pymupdf.Rect(20,90,70,105),'uri':'https://example.org/source'})
        doc[0].add_rect_annot(pymupdf.Rect(20,120,70,135))
        doc[0].insert_link({'kind':pymupdf.LINK_GOTO,'from':pymupdf.Rect(90,90,140,105),'page':1,'to':pymupdf.Point(20,60)})
        doc.saveIncr()
    r=PdfReader(source);baseline=annotation_fingerprint(r,1)
    w=PdfWriter()
    for i in range(20):w._add_object(DictionaryObject({NameObject('/Unrelated'):TextStringObject('padding')}))
    w.append(r);out=tmp_path/'renumbered.pdf';w.write(out)
    assert r.pages[0]['/Annots'][0].idnum!=PdfReader(out).pages[0]['/Annots'][0].idnum
    assert annotation_fingerprint(PdfReader(out),1)==baseline
    w=PdfWriter(clone_from=PdfReader(out))
    w.pages[0]['/Annots'][0].get_object()['/A'][NameObject('/URI')]=TextStringObject('https://example.org/changed')
    bad=tmp_path/'different.pdf';w.write(bad)
    assert annotation_fingerprint(PdfReader(bad),1)!=baseline
    w=PdfWriter(clone_from=PdfReader(out))
    appearance=w.pages[0]['/Annots'][1].get_object()['/AP']['/N'].get_object()
    appearance.set_data(b'0 0 m 10 10 l S')
    bad_appearance=tmp_path/'appearance.pdf';w.write(bad_appearance)
    assert annotation_fingerprint(PdfReader(bad_appearance),1)!=baseline
    w=PdfWriter(clone_from=PdfReader(out));link=w.pages[0]['/Annots'][2].get_object()
    destination=link['/Dest'] if '/Dest' in link else link['/A']['/D']
    destination[0]=w.pages[0].indirect_reference
    bad_destination=tmp_path/'destination.pdf';w.write(bad_destination)
    assert annotation_fingerprint(PdfReader(bad_destination),1)!=baseline
