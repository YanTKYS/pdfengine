"""Read-only, narrow binding experiment; NOT a runtime verifier or schema.

Option B: an independently trusted caller confirms exact current semantic data.
The HMAC test harness represents that external channel, not a self-seal. No key
is read from the PDF, record or receipt. Production authority issuance is OPEN.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction as F
from io import BytesIO
import hashlib
import hmac
import json
import re

from fontTools.ttLib import TTFont
from fontTools import __version__ as fonttools_version
from fontTools.pens.recordingPen import DecomposingRecordingPen
from pypdf import PdfReader
from pypdf.generic import NumberObject
import pymupdf
import uharfbuzz

from pdfeditor.shaped_font import ShapedFont
from evaluations.anchors.measurement_authority import layout, measurement


DOMAIN = 'pdfengine-design-confirmation-v1'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def decimal(value):
    """Fixed 6-place nearest/ties-to-even OUTPUT rule, never semantic authority."""
    n = F(value)
    require(abs(n) <= 10**6, 'OUTPUT_RANGE')
    units = round(n * 10**6)
    sign = '-' if units < 0 else ''
    whole, tail = divmod(abs(units), 10**6)
    return sign + str(whole) + (('.' + f'{tail:06d}'.rstrip('0')) if tail else '')


def record_payload(record):
    value = deepcopy(record)
    value.pop('self_hash', None)
    return value


def reseal(record):
    value = record_payload(record)
    value['self_hash'] = sha(canonical(value))
    return value


def confirmation_message(record, pdf_bytes, asset_bytes, target):
    return dict(domain=DOMAIN, target=target, semantic_sha=sha(canonical(record_payload(record))),
                pdf_sha=sha(pdf_bytes), asset_sha=sha(asset_bytes))


def issue_test_confirmation(record, pdf_bytes, asset_bytes, target, external_key):
    """Test-only trusted issuer. Calling this is NOT proof a user confirmed data.

    The harness supplies known fixture intentions independently of untrusted
    inputs. Never automatically invoke this on a candidate record in a verifier.
    """
    require(len(external_key) >= 32, 'TRUST_KEY')
    msg = confirmation_message(record, pdf_bytes, asset_bytes, target)
    return dict(message=msg, tag=hmac.new(external_key, canonical(msg), 'sha256').hexdigest())


def check_confirmation(record, pdf_bytes, asset_bytes, receipt, target, external_key):
    require(len(external_key) >= 32, 'TRUST_KEY')
    msg = confirmation_message(record, pdf_bytes, asset_bytes, target)
    require(receipt.get('message') == msg, 'CURRENT_CONFIRMATION_MISMATCH')
    expected = hmac.new(external_key, canonical(msg), 'sha256').hexdigest()
    require(isinstance(receipt.get('tag'), str) and hmac.compare_digest(expected, receipt['tag']),
            'EXTERNAL_AUTHORITY_MISMATCH')


def font_policy():
    return dict(collection_index=0, variations={}, features={'kern': False},
                shaping='one-ASCII-codepoint/nominal-hmtx',
                shaping_version=uharfbuzz.version_string(), outline='simple-unhinted-TT/recordingPen-v1',
                outline_library_version=fonttools_version)


def nominal_glyph(char, font):
    gid = font.font.getGlyphID(font.font.getBestCmap()[ord(char)])
    ink = font.ink(gid)
    return dict(text=char, font_identity=font.source_sha256, glyph_identity=str(gid),
                upem=str(font.upem), width=str(font.nominal_width(gid)),
                ascent=str(font.font['hhea'].ascent), descent=str(-font.font['hhea'].descent),
                x_offset='0', y_offset='0', ink=list(map(str, ink)) if ink else None,
                empty_outline_verified=char == ' ' and ink is None)


def admit(record, asset):
    """Input admissibility from asset + externally confirmed explicit policies."""
    require(set(record_payload(record)) == {'model', 'font_policy', 'codebook', 'intervals',
        'omitted', 'default_style_id', 'empty_style_id', 'paragraph_id', 'pdf_sha'}, 'RECORD_SCOPE')
    require('self_hash' not in record or isinstance(record['self_hash'],str) and len(record['self_hash'])<=64,
            'SELF_HASH_SCOPE')
    require(asset[:4]==b'\x00\x01\x00\x00', 'STATIC_TT_ASSET_SCOPE')
    require(record['font_policy'] == font_policy(), 'FONT_INSTANCE_OR_SHAPING_POLICY')
    font = ShapedFont(asset)
    require('fvar' not in font.font and 'glyf' in font.font and
            not any(t in font.font for t in ('fpgm', 'prep', 'cvt ')), 'FONT_PROGRAM_SCOPE')
    model = record['model']
    require(set(model)=={'alignment','glyphs','style','edges','x','baseline','width','leading','empty'}, 'MODEL_SCOPE')
    require(set(model['style'])=={'font_size','horizontal_scale','rise','tracking','word_spacing','spacing_intent'}, 'STYLE_SCOPE')
    require(set(model['empty'])=={'ascent','descent'}, 'EMPTY_STYLE_SCOPE')
    require(model['style']['spacing_intent']=='confirmed-inline', 'SPACING_INTENT')
    require(record['default_style_id'] == record['empty_style_id'] == 'body', 'EMPTY_STYLE_POLICY')
    require(model['alignment'] == 'left', 'ALIGNMENT_SCOPE')
    measurement(nominal_glyph('A',font),model['style'])
    glyphs = model['glyphs']
    require(len(glyphs) <= 256, 'TEXT_SCOPE')
    require(record['intervals'] == [dict(start=i, end=i+1, id=f'current:{i}', style='body')
        for i in range(len(glyphs))], 'INTERVAL_BIJECTION')
    used = {g['text'] for g in glyphs if g['text'] != '\n'}
    require(used <= {'A', 'B', ' '}, 'CHARACTER_SCOPE')
    # Space/A remain current default-style coverage even for a fully empty fragment.
    require(set(record['codebook']) == used | {'A', ' '}, 'CODEBOOK_COVERAGE')
    codes = list(record['codebook'].values())
    require(all(type(c) is int for c in codes) and sorted(codes) == list(range(1, len(codes)+1)),
            'CODEBOOK_BIJECTION')
    for char in record['codebook']:
        gid = font.font.getGlyphID(font.font.getBestCmap()[ord(char)])
        glyph = font.font['glyf'][font.order[gid]]
        require(not glyph.isComposite(), 'OUTLINE_SCOPE')
        require(not getattr(glyph,'program',None) or not glyph.program.getBytecode(), 'OUTLINE_SCOPE')
    for edge in model['edges']:
        require(set(edge) <= {'left','right','delta','delta_y','operator','semantics','boundary_policy'}, 'EDGE_SCOPE')
        require(edge.get('operator') in ('TJ','Tm'), 'EDGE_OPERATOR_SCOPE')
    for g in glyphs:
        if g['text'] == '\n':
            require(g == {'text': '\n'}, 'NEWLINE_SCOPE')
        else:
            require(g == nominal_glyph(g['text'], font), 'LOGICAL_FONT_METRIC_MISMATCH')
            measurement(g, model['style'])
    size, rise = F(model['style']['font_size']), F(model['style']['rise'])
    # The designated body font is authoritative even with no painted glyph.
    expected_empty = dict(ascent=str(max(F(0), F(font.font['hhea'].ascent,font.upem)*size-rise)),
                          descent=str(max(F(0), -F(font.font['hhea'].descent,font.upem)*size+rise)))
    require(model['empty'] == expected_empty, 'EMPTY_METRIC_AUTHORITY')
    plan = layout(model)
    emitted = {g['start'] for g in plan['glyphs']}
    omitted = [dict(offset=i, kind='newline' if g['text']=='\n' else 'trimmed-space', style='body')
               for i,g in enumerate(glyphs) if i not in emitted]
    require(all(glyphs[v['offset']]['text'] in (' ', '\n') for v in omitted), 'OMITTED_VISIBLE_GLYPH')
    require(record['omitted'] == omitted, 'NONPAINTED_INTERVAL_MISMATCH')
    return font, plan


def expected_program(record, plan, *, height='260'):
    """Fixture expectation only; no runtime output or ownership adoption."""
    style = record['model']['style']
    parts = [f"q BT /F {decimal(style['font_size'])} Tf {decimal(F(style['horizontal_scale'])*100)} Tz 0 Tc 0 Tw 0 Ts 0 g\n"]
    parts.append(f"1 0 0 1 {decimal(record['model']['x'])} {decimal(F(height)-F(record['model']['baseline'])-F(style['rise']))} Tm [] TJ\n")
    for g in plan['glyphs']:
        x,y = g['origin']
        code = record['codebook'][g['metric']['text']]
        parts.append(f'1 0 0 1 {decimal(x)} {decimal(F(height)-F(y))} Tm <{code:04X}> Tj\n')
    return (''.join(parts)+'ET Q\n').encode('ascii')


def expected_cmap(codebook):
    rows = sorted((code,char) for char,code in codebook.items())
    return (b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n'
        b'/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n'
        b'/CMapName /PEUnicode def /CMapType 2 def\n1 begincodespacerange <0000> <FFFF> endcodespacerange\n'+
        f'{len(rows)} beginbfchar\n'.encode()+b''.join(f'<{c:04X}> <{ord(t):04X}>\n'.encode() for c,t in rows)+
        b'endbfchar\nendcmap CMapName currentdict /CMap defineresource pop end end\n')


def extract_current(pdf_bytes, record, asset_font, plan):
    """Actual PDF extraction, strict full-page fixture grammar; no marker lookup."""
    pdf = PdfReader(BytesIO(pdf_bytes), strict=True)
    require(not pdf.is_encrypted and len(pdf.pages)==1, 'PDF_SCOPE')
    root = pdf.trailer['/Root']
    require(set(root) <= {'/Type','/Pages'}, 'CATALOG_CONTEXT')
    page = pdf.pages[0]
    require(set(page) <= {'/Type','/Parent','/Resources','/MediaBox','/Contents'}, 'PAGE_CONTEXT')
    require(list(page.mediabox)==[0,0,320,260], 'PAGE_GEOMETRY')
    resources = page['/Resources'].get_object()
    require(set(resources)=={'/Font'} and set(resources['/Font'])=={'/F'}, 'RESOURCE_CONTEXT')
    program = page['/Contents'].get_object().get_data()
    require(program == expected_program(record, plan), 'CANONICAL_PROGRAM_MISMATCH')
    # Byte spans, not nearest positions or Unicode matching, bind occurrences.
    shows = list(re.finditer(rb'<([0-9A-F]{4})> Tj', program))
    require(len(shows)==len(plan['glyphs']), 'SHOW_BIJECTION')
    bindings = [dict(interval=[g['start'],g['end']], byte_span=[m.start(),m.end()],
                     code=int(m[1],16), style='body') for g,m in zip(plan['glyphs'], shows)]
    font = resources['/Font']['/F'].get_object()
    require(set(font)=={'/Type','/Subtype','/BaseFont','/Encoding','/DescendantFonts','/ToUnicode'}, 'FONT_RESOURCE_SCOPE')
    require(font['/Type']=='/Font' and font['/Subtype']=='/Type0' and font['/Encoding']=='/Identity-H' and len(font['/DescendantFonts'])==1,
            'ENCODING_SCOPE')
    require(font['/ToUnicode'].get_object().get_data()==expected_cmap(record['codebook']), 'UNICODE_MAPPING')
    cidfont=font['/DescendantFonts'][0].get_object()
    require(set(cidfont)=={'/Type','/Subtype','/BaseFont','/CIDSystemInfo','/FontDescriptor','/DW','/W','/CIDToGIDMap'}, 'CIDFONT_SCOPE')
    require(cidfont['/Type']=='/Font' and cidfont['/Subtype']=='/CIDFontType2' and cidfont['/BaseFont']==font['/BaseFont'], 'CIDFONT_IDENTITY')
    system=cidfont['/CIDSystemInfo']
    require(str(system['/Registry'])=='Adobe' and str(system['/Ordering'])=='Identity' and system['/Supplement']==0,
            'CIDSYSTEM_SCOPE')
    widths=cidfont['/W']
    rows=sorted((code,char) for char,code in record['codebook'].items())
    require(cidfont['/DW']==1000 and len(widths)==2 and widths[0]==1 and len(widths[1])==len(rows), 'WIDTHS_SCOPE')
    # This fixture language admits integer PDF widths only. Never round a
    # lexical fractional width through pypdf.FloatObject into equality.
    require(all(isinstance(w,NumberObject) for w in widths[1]), 'WIDTHS_INTEGER_SCOPE')
    mapping=cidfont['/CIDToGIDMap'].get_object().get_data()
    require(len(mapping)==2*(len(rows)+1) and mapping[:2]==b'\0\0', 'CID_MAPPING_SCOPE')
    desc=cidfont['/FontDescriptor'].get_object()
    require(set(desc)=={'/Type','/FontName','/Flags','/FontBBox','/ItalicAngle','/Ascent','/Descent','/CapHeight','/StemV','/FontFile2'}, 'DESCRIPTOR_SCOPE')
    data=desc['/FontFile2'].get_object().get_data()
    current=TTFont(BytesIO(data),recalcTimestamp=False)
    try:
        require('glyf' in current and 'fvar' not in current and not any(t in current for t in ('fpgm','prep','cvt ')), 'CURRENT_PROGRAM_SCOPE')
        upem=current['head'].unitsPerEm
        require(upem==asset_font.upem and (current['hhea'].ascent,current['hhea'].descent)==
                (asset_font.font['hhea'].ascent,asset_font.font['hhea'].descent), 'VERTICAL_METRIC_MISMATCH')
        require(desc['/Type']=='/FontDescriptor' and desc['/FontName']==font['/BaseFont'] and desc['/Flags']==4 and desc['/ItalicAngle']==0,
                'DESCRIPTOR_IDENTITY')
        require(F(str(desc['/Ascent']))==F(current['hhea'].ascent*1000,upem) and
                F(str(desc['/Descent']))==F(current['hhea'].descent*1000,upem), 'DESCRIPTOR_METRICS')
        # The fixture descriptor retains the confirmed full asset's bounding box;
        # a subset can have a smaller box. Do not demand byte/global-table identity.
        head=asset_font.font['head']
        require([F(str(v)) for v in desc['/FontBBox']]==[F(v*1000,upem) for v in (head.xMin,head.yMin,head.xMax,head.yMax)], 'DESCRIPTOR_BBOX')
        require(F(str(desc['/CapHeight']))==F(getattr(asset_font.font['OS/2'],'sCapHeight',head.yMax)*1000,upem)
                and desc['/StemV']==80, 'DESCRIPTOR_POLICY')
        samples=[]
        for code,char in rows:
            gid=int.from_bytes(mapping[2*code:2*code+2],'big')
            require(0 < gid < len(current.getGlyphOrder()), 'GID_RANGE')
            name=current.getGlyphName(gid)
            require(current.getBestCmap().get(ord(char))==name, 'PROGRAM_CMAP_MISMATCH')
            asset_gid=asset_font.font.getGlyphID(asset_font.font.getBestCmap()[ord(char)])
            glyph=current['glyf'][name]
            require(not glyph.isComposite(), 'CURRENT_OUTLINE_SCOPE')
            require(not getattr(glyph,'program',None) or not glyph.program.getBytecode(), 'CURRENT_HINT_SCOPE')
            pen=DecomposingRecordingPen(current.getGlyphSet());current.getGlyphSet()[name].draw(pen)
            require(pen.value==asset_font.outline(asset_gid), 'OUTLINE_MISMATCH')
            require(current['hmtx'][name]==asset_font.font['hmtx'][asset_font.order[asset_gid]], 'HMTX_MISMATCH')
            require(F(str(widths[1][code-1]))==F(current['hmtx'][name][0]*1000,upem), 'PDF_WIDTH_MISMATCH')
            samples.append(dict(code=code,cid=code,current_gid=gid,asset_gid=asset_gid,unicode=char,
                width=str(widths[1][code-1]),outline_sha=sha(canonical(pen.value))))
        return dict(program_sha=sha(program),program_bytes=len(program),binding=bindings,
                    default_style_span=[program.index(b'1 0 0 1'),program.index(b'[] TJ')+5],
                    font_samples=samples,asset_sha=asset_font.source_sha256,
                    embedded_program_sha=sha(data),subset_bytes_equal_asset=sha(data)==asset_font.source_sha256)
    finally:
        current.close()


def physical_accuracy(pdf_bytes, plan):
    with pymupdf.open(stream=pdf_bytes,filetype='pdf') as pdf:
        actual=[c for span in pdf[0].get_texttrace() for c in span['chars']]
    require(len(actual)==len(plan['glyphs']), 'TRACE_COUNT')
    require([chr(c[0]) for c in actual]==[g['metric']['text'] for g in plan['glyphs']], 'TRACE_UNICODE')
    error=max((max(abs(float(F(a))-b) for a,b in zip(g['origin'],c[2]))
               for g,c in zip(plan['glyphs'],actual)),default=0)
    return dict(max_origin_error=error,passed=error<=.002)


def bind(pdf_bytes, record, asset, receipt, *, external_key, target):
    """No mutations. Trust key/target are caller inputs outside untrusted bundle."""
    require(record.get('pdf_sha')==sha(pdf_bytes), 'STALE_PDF')
    require(target.get('page')==1 and target.get('audience')=='binding-design', 'TARGET_SCOPE')
    check_confirmation(record,pdf_bytes,asset,receipt,target,external_key)
    require(record['paragraph_id']==target['paragraph'], 'TARGET_PARAGRAPH')
    font,plan=admit(record,asset)
    try:
        witness=extract_current(pdf_bytes,record,font,plan)
        accuracy=physical_accuracy(pdf_bytes,plan)
        require(accuracy['passed'],'PLACEMENT_ACCURACY')
        return dict(admitted=True,plan=plan,witness=witness,accuracy=accuracy,
                    semantic_sha=sha(canonical(record_payload(record))),
                    authority='external-confirmation + current font/program witness; intent is not physically witnessed')
    finally:
        font.font.close()


def three_layer_verdict(gates):
    required=('model_determinism','input_admissibility','authenticated_binding')
    if any(not gates.get(k) or any(v is not True for v in gates[k].values()) for k in required):
        return 'NOT READY'
    return 'DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR'
