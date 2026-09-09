from copy import deepcopy

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from pdfeditor.paint_provenance import source_path_catalog, prove_path_paint
from test_attributed import source_pdf


def test_path_bundle_maps_to_source_and_keeps_clip(tmp_path):
    p=source_pdf(tmp_path,b'10 10 200 200 re W B 20 20 10 10 re f')
    catalog=source_path_catalog(p)
    proof=prove_path_paint(p,1,catalog['paths'][0]['id'],catalog=catalog)
    assert proof['status']=='proven',proof
    assert [p['kind'] for p in proof['paints']]==['fill-path','stroke-path']
    assert proof['evidence']['other_paint_and_scope_equal']
    altered=deepcopy(catalog);altered['paths'][0]['operator_index']+=1
    assert prove_path_paint(p,1,catalog['paths'][0]['id'],catalog=altered)['status']=='refused'


def test_identical_duplicate_paints_do_not_establish_unique_provenance(tmp_path):
    p=source_pdf(tmp_path,b'20 20 100 100 re f 20 20 100 100 re f')
    catalog=source_path_catalog(p)
    proof=prove_path_paint(p,1,catalog['paths'][0]['id'])
    assert proof['status']=='refused' and 'duplicate' in proof['reason']


def test_physical_stream_ranges_can_cross_contents_boundaries(tmp_path):
    p=source_pdf(tmp_path)
    writer=PdfWriter(clone_from=PdfReader(p))
    refs=[]
    for raw in (b'10 10 100',b'100 re f'):
        stream=DecodedStreamObject();stream.set_data(raw);refs.append(writer._add_object(stream))
    writer.pages[0][NameObject('/Contents')]=ArrayObject(refs)
    writer.write(p)
    c=source_path_catalog(p)
    spans=c['paths'][0]['path_source_ranges'][0]['source_ranges']
    assert [r['contents_index'] for r in spans]==[0,1]
    assert prove_path_paint(p,1,c['paths'][0]['id'])['status']=='proven'


def test_shared_form_invocations_are_identified_but_not_mutated(tmp_path):
    p=source_pdf(tmp_path,b'/Fx Do q 1 0 0 1 100 0 cm /Fx Do Q')
    writer=PdfWriter(clone_from=PdfReader(p))
    form=DecodedStreamObject();form.set_data(b'0 0 20 20 re f')
    form.update({NameObject('/Type'):NameObject('/XObject'),NameObject('/Subtype'):NameObject('/Form'),
                 NameObject('/BBox'):ArrayObject([NumberObject(x) for x in [0,0,30,30]]),
                 NameObject('/Resources'):DictionaryObject()})
    writer.pages[0]['/Resources'][NameObject('/XObject')]=writer._add_object(DictionaryObject({NameObject('/Fx'):writer._add_object(form)}))
    writer.write(p)
    c=source_path_catalog(p)
    assert len(c['forms'])==len(c['paths'])==2
    assert c['paths'][0]['id']!=c['paths'][1]['id']
    assert all(x['page_invocation_count']==2 for x in c['forms'])
    assert all(not x['mutable'] for x in c['paths'])
    assert 'invocation-local' in prove_path_paint(p,1,c['paths'][0]['id'])['reason']
