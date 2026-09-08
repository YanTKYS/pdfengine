"""Reviewable JSON and file ownership contracts for attributed edits."""
import json

import pymupdf
import pytest

from pdfeditor.selection import make_selection
from test_attributed import source_pdf
from test_selection_cli import invoke, successful


def write(path,value):
    path.write_bytes(json.dumps(value,ensure_ascii=False).encode('utf-8'))


def prepared(tmp_path):
    source = source_pdf(tmp_path)
    selection,paragraph = tmp_path/'selection.json',tmp_path/'paragraph.json'
    write(selection,make_selection(source,glyph_ids=list(range(14)),explicit_width=140))
    snapshot = successful('inspect-paragraph',source,'--selection',selection,'--json',paragraph)
    assert snapshot['text']=='HEAD body TAIL'
    return source,selection,paragraph


def test_cli_relative_font_and_style_preserving_edit(tmp_path):
    source,_,paragraph = prepared(tmp_path)
    font = tmp_path/'supplied.ttf'
    font.write_bytes(pymupdf.Font('cjk').buffer)
    edits,output,report,removed = [tmp_path/name for name in ('edits.json','edited.pdf','report.json','removed.pdf')]
    write(edits,{'edits':[dict(start=5,end=9,text='本文')], 'fonts':{'s1':{'path':'supplied.ttf'}}})
    result = successful('edit-paragraph',source,output,'--paragraph',paragraph,'--edits',edits,
                        '--report',report,'--removal-output',removed)
    assert result['retained_glyph_count']==10 and result['provided_font_glyph_count']==2
    assert not result['independent_renderer_verified']
    with pymupdf.open(output) as doc:
        assert '本文' in doc[0].get_text()
    with pymupdf.open(removed) as doc:
        assert doc[0].get_text().strip()=='KEEP'


@pytest.mark.parametrize('target', ['source','paragraph','edits','font','same_report'])
def test_cli_protects_inputs_and_distinct_outputs(tmp_path,target):
    source,_,paragraph = prepared(tmp_path)
    edits,font = tmp_path/'edits.json',tmp_path/'supplied.ttf'
    font.write_bytes(b'protected font bytes')
    write(edits,{'edits':[], 'fonts':{'s0':{'path':'supplied.ttf'}}})
    paths = {'source':source,'paragraph':paragraph,'edits':edits,'font':font}
    originals = {p:p.read_bytes() for p in paths.values()}
    output = tmp_path/'edited.pdf'
    report = output if target=='same_report' else paths[target]
    result = invoke('edit-paragraph',source,output,'--paragraph',paragraph,'--edits',edits,'--report',report)
    assert result.returncode==2
    assert not output.exists()
    assert all(p.read_bytes()==data for p,data in originals.items())
