"""Source identities survive byte mutations through provenance, never geometry."""
import json

import pytest

from pdfeditor.backend import PdfError
from pdfeditor.content_stream import ContentPage, rewritten_event
from pdfeditor.mutation import (IdentityMap, Mutation, MutationProgram, emitted_glyphs, map_glyphs,
                                map_offset, map_path)
from pdfeditor.paint_provenance import source_path_catalog
from pdfeditor.pdf_save import program_pdf_bytes
from test_attributed import source_pdf


PROGRAM=(b'0 0 1 rg 20 150 60 8 re f 0 g BT /Regular 12 Tf 20 200 Td (ONE) Tj '
         b'0 -20 Td (TWO) Tj 0 -20 Td (END) Tj ET 1 0 0 rg 100 150 30 8 re f')


def saved(tmp_path, source, program, name):
    path=tmp_path/f'{name}.pdf'
    path.write_bytes(program_pdf_bytes(source,0,program))
    return path


def opened(tmp_path):
    source=source_pdf(tmp_path,PROGRAM)
    before=ContentPage(source,1)
    return source,before,MutationProgram(before.streams[-before.page.xref])


def test_unrelated_and_wrapping_mutations_keep_glyph_identity(tmp_path):
    source,before,program=opened(tmp_path)
    first,second,third=before.events
    # The first operator grows by a TJ adjustment; the third is wrapped in a
    # translation. Neither the second operator nor any glyph is re-found by
    # coordinates: the map follows byte provenance and witnesses.
    program.add(Mutation(first.operator.start,first.operator.end,b'[<4f> 10 <4e45>] TJ',
                         kind='text-edit',anchors={'rewritten':0},chars={0:0,1:1,2:2}))
    prefix=b'q 1 0 0 1 0 -5 cm '
    program.add(Mutation(third.operator.start,third.operator.end,prefix+b'(END) Tj Q',
                         kind='text-move',anchors={'op':len(prefix)}))
    out=saved(tmp_path,source,program.apply(),'moved')
    after=ContentPage(out,1)
    try:
        mapping=map_glyphs(before,after,program,range(9))
        assert sorted(mapping)==list(range(9)) and len(set(mapping.values()))==9
        for old,new in mapping.items():
            assert before.actual[old]['unicode']==after.actual[new]['unicode']
            assert before.actual[old]['gid']==after.actual[new]['gid']
        assert after.actual[mapping[6]]['origin'][1]==pytest.approx(before.actual[6]['origin'][1]+5)
        assert program.map_offset(second.operator.start)==second.operator.start+len(b'[<4f> 10 <4e45>] TJ')-len(b'(ONE) Tj')
        assert map_offset(second.operator.start,program.edits())==program.map_offset(second.operator.start)
        # A component consumed by its mutation has no successor and is not guessed.
        with pytest.raises(PdfError,match='consumed'):
            program.map_offset(first.operator.start+2)
    finally:
        after.close();before.close()


def test_same_operator_edit_maps_retained_characters_and_refuses_removed_ones(tmp_path):
    source,before,program=opened(tmp_path)
    second=before.events[1]
    data,offset,chars=rewritten_event(second,{3},remove=True)
    assert chars=={1:0,2:1}
    program.add(Mutation(second.operator.start,second.operator.end,data,kind='text-edit',
                         anchors={'rewritten':offset},chars=chars))
    out=saved(tmp_path,source,program.apply(),'removed')
    after=ContentPage(out,1)
    try:
        mapping=map_glyphs(before,after,program,[0,4,5,8])
        assert [after.actual[mapping[i]]['unicode'] for i in (0,4,5,8)]==['O','W','O','D']
        with pytest.raises(PdfError,match='no successor'):
            map_glyphs(before,after,program,[3])
    finally:
        after.close();before.close()


def test_ambiguous_or_changed_witnesses_fail_closed(tmp_path):
    source,before,program=opened(tmp_path)
    first,second,third=before.events
    with pytest.raises(PdfError,match='overlap'):
        bad=MutationProgram(program.source)
        bad.add(Mutation(second.operator.start,second.operator.end,b'(TWO) Tj'))
        bad.add(Mutation(second.operator.start+1,second.operator.end,b'(TWO) Tj'))
    # A rewritten operator whose character no longer matches its witness.
    program.add(Mutation(second.operator.start,second.operator.end,b'(TWX) Tj',kind='text-edit',
                         anchors={'rewritten':0},chars={0:0,1:1,2:2}))
    out=saved(tmp_path,source,program.apply(),'witness')
    after=ContentPage(out,1)
    try:
        assert map_glyphs(before,after,program,[3,4])
        with pytest.raises(PdfError,match='witness'):
            map_glyphs(before,after,program,[5])
        # A map rebuilt from wrong byte edits cannot reproduce the saved program.
        with pytest.raises(PdfError,match='do not reproduce'):
            IdentityMap.from_edits(source,out,1,[])
        identity=IdentityMap.from_edits(source,out,1,program.edits())
        try:
            assert identity.map_glyphs([0,1,2,6,7,8])
            with pytest.raises(PdfError):
                identity.map_glyphs([5])
        finally:
            identity.close()
    finally:
        after.close();before.close()


def test_path_identity_maps_untouched_and_emitted_operators(tmp_path):
    source,before,program=opened(tmp_path)
    catalog=source_path_catalog(source,1)
    blue,red=catalog['paths']
    op=program.source[blue['merged_range'][0]:blue['merged_range'][1]]
    assert op==b'f'
    prefix=b'n q 1 0 0 1 0 10 cm 20 150 60 8 re '
    moved=program.add(Mutation(blue['merged_range'][0],blue['merged_range'][1],prefix+b'f Q',kind='path-move',
                               anchors={'paint':len(prefix)}))
    text=before.events[0]
    program.add(Mutation(text.operator.start,text.operator.end,b'[<4f4e45>] TJ',kind='text-edit',
                         anchors={'rewritten':0},chars={0:0,1:1,2:2}))
    out=saved(tmp_path,source,program.apply(),'paths')
    after_catalog=source_path_catalog(out,1)
    new_blue=map_path(catalog,after_catalog,program,blue['id'],anchor=(moved,'paint'))
    new_red=map_path(catalog,after_catalog,program,red['id'])
    by_id={p['id']:p for p in after_catalog['paths']}
    assert new_blue!=blue['id'] and new_red!=red['id'] and new_blue!=new_red
    assert by_id[new_red]['operation_sha256']==red['operation_sha256']
    assert by_id[new_blue]['operator']=='f'
    with pytest.raises(PdfError,match='consumed'):
        map_path(catalog,after_catalog,program,blue['id'])
    with pytest.raises(PdfError,match='unknown'):
        map_path(catalog,after_catalog,program,'path-missing')
    before.close()


def test_program_boundaries_refuse_ambiguous_insertions_and_allow_adjacent_replacements():
    def program():
        return MutationProgram(b'0123456789ABCDEFGHIJKLMNOPQRS')
    for start,end,label in [(15,15,'inside'),(10,10,'at start'),(20,20,'at end')]:
        p=program();p.add(Mutation(10,20,b'XY'))
        with pytest.raises(PdfError,match='overlap|touches'):
            p.add(Mutation(start,end,b'ins'))
        p=program();p.add(Mutation(start,end,b'ins'))
        with pytest.raises(PdfError,match='overlap|touches'):
            p.add(Mutation(10,20,b'XY'))
    p=program();p.add(Mutation(12,12,b'a'))
    with pytest.raises(PdfError,match='overlap|touches'):
        p.add(Mutation(12,12,b'b'))
    p=program();p.add(Mutation(10,20,b'XY'));p.add(Mutation(20,25,b'z'));p.add(Mutation(5,10,b'Q'))
    assert p.apply()==b'01234QXYzPQRS'
    assert p.map_offset(25)==len(b'01234QXYz') and p.map_offset(4)==4
    # An insertion strictly inside a gap precedes the source byte at its position.
    p=program();p.add(Mutation(10,20,b'XY'));p.add(Mutation(3,3,b'ins'))
    assert p.apply()==b'012ins3456789XYKLMNOPQRS' and p.map_offset(3)==6 and p.map_offset(20)==15


def test_identity_map_is_rebuilt_from_persisted_records_not_only_byte_edits(tmp_path):
    source,before,program=opened(tmp_path)
    second=before.events[1]
    data,offset,chars=rewritten_event(second,{3},remove=True)
    prefix=b'q 1 0 0 1 0 0 cm ';generated=data+b' '+prefix+b'(Z) Tj Q'
    program.add(Mutation(second.operator.start,second.operator.end,generated,kind='text-edit',
                         anchors={'rewritten':offset,'glyph:0':len(data)+1+len(prefix)},chars=chars,owner='B'))
    catalog=source_path_catalog(source,1);blue=catalog['paths'][0]
    path_prefix=b'n q 20 150 60 8 re '
    moved=program.add(Mutation(blue['merged_range'][0],blue['merged_range'][1],path_prefix+b'f Q',kind='path-move',
                               anchors={'paint':len(path_prefix)}))
    out=saved(tmp_path,source,program.apply(),'records')
    after=ContentPage(out,1)
    try:
        expected=map_glyphs(before,after,program,[0,4,5,8])
        emitted=emitted_glyphs(after,program,program.mutation_at(second.operator.start),['glyph:0'])
        expected_path=map_path(catalog,source_path_catalog(out,1),program,blue['id'],anchor=(moved,'paint'))
        records=program.records();edits=program.edits()
    finally:
        after.close();before.close()
    del program
    # Byte edits alone cannot follow retained or generated components.
    partial=IdentityMap.from_edits(source,out,1,edits)
    try:
        with pytest.raises(PdfError,match='consumed'):
            partial.map_glyphs([4])
    finally:
        partial.close()
    # Complete records reproduce the original map, including JSON round-tripping.
    rebuilt=IdentityMap.from_records(source,out,1,json.loads(json.dumps(records)),
                                     path_anchors={blue['id']:None})
    try:
        assert rebuilt.map_glyphs([0,4,5,8])==expected
        mutation=rebuilt.program.mutation_at(second.operator.start)
        assert mutation.chars=={1:0,2:1} and mutation.owner=='B'
        assert rebuilt.emitted_glyphs(mutation,['glyph:0'])==emitted
        assert rebuilt.after.actual[emitted[0]]['unicode']=='Z'
        rebuilt.path_anchors[blue['id']]=(rebuilt.program.mutation_at(blue['merged_range'][0]),'paint')
        assert rebuilt.map_path(blue['id'])==expected_path
        with pytest.raises(PdfError,match='no successor'):
            rebuilt.map_glyphs([3])
    finally:
        rebuilt.close()
