"""Read-only boundary eligibility experiment; never authorizes an owned block."""
from pathlib import Path
import sys

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, FloatObject, BooleanObject

from pdfeditor.content_stream import ContentPage, operators
from pdfeditor.continuation import source_ctms
from evaluations.continuation.canonical_metrics import program


def inspect_boundary(pdf, index):
    content = ContentPage(pdf, 1)
    try:
        boundary = content.boundaries[index]
        state = boundary.state
        data = program(pdf, 1)
        exact = source_ctms(data)[index]
        reasons = []
        if content.errors:
            reasons.append('INTERPRETER_ERRORS')
        for key, value in (('PENDING_PATH', boundary.pending_path), ('PENDING_CLIP', boundary.pending_clip),
            ('TEXT_OBJECT', boundary.text_object), ('MARKED_CONTENT', boundary.marked_content_depth),
            ('COMPATIBILITY', boundary.compatibility_depth)):
            if value:
                reasons.append(key)
        if exact is None or exact[1] or exact[2] or exact[0] <= 0 or exact[3] <= 0:
            reasons.append('UNSUPPORTED_CTM')
        if any(key.startswith('ExtGState:') for key in state.other):
            reasons.append('INHERITED_EXTGSTATE')
        if content.pdf_page.get('/Group') is not None:
            reasons.append('PAGE_GROUP')
        if state.opacity != 1 or state.stroke_opacity != 1:
            reasons.append('NONDEFAULT_OPACITY')
        if state.fill[0] not in ('g', 'rg', 'k'):
            reasons.append('UNSUPPORTED_FILL')
        if len(state.clip) > 1 or any(len(c.get('path', [])) != 1 or c['path'][0]['operator'] != 're'
                                     or c.get('rule') not in ('W', 'W*') for c in state.clip):
            reasons.append('UNSUPPORTED_CLIP')
        colors = content.pdf_page['/Resources'].get_object().get('/ColorSpace', {})
        if hasattr(colors, 'get_object'):
            colors = colors.get_object()
        if any(key in colors for key in ('/DefaultGray', '/DefaultRGB', '/DefaultCMYK')):
            reasons.append('DEFAULT_COLORSPACE_OVERRIDE')
        page = content.pdf_page
        if (content.page.rotation or float(page.get('/UserUnit', 1)) != 1
                or list(map(float, page.mediabox.lower_left)) != [0, 0]
                or list(map(float, page.cropbox)) != list(map(float, page.mediabox))):
            reasons.append('UNSUPPORTED_PAGE_FRAME')
        return dict(eligible_for_further_proof=not reasons, reasons=reasons,
            exact_ctm=[str(x) for x in exact] if exact is not None else None,
            interpreter_ctm=list(state.ctm), opacity=state.opacity, other=state.other,
            q_depth=boundary.q_depth, pending_path=boundary.pending_path, pending_clip=boundary.pending_clip,
            clip_count=len(state.clip), fill=state.fill,
            note='Boundary evidence only; does not prove containment, authorship, clip ink bounds or ownership')
    finally:
        content.close()


def evaluate_boundaries(root):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tests'))
    from test_attributed import source_pdf
    body=b'0 g 20 198 50.4 .7 re f '
    cases={
        'plain':body,
        'q_line_state':b'q 2 w [3 2] 1 d 1 J 2 j 8 M .3 G '+body+b'Q',
        'scale':b'q .83 0 0 .91 7.25 11.5 cm '+body+b'Q',
        'rectangle_clip':b'q 0 0 320 260 re W n '+body+b'Q',
        'compound_clip':b'q 0 0 320 260 re 1 1 300 200 re W n '+body+b'Q',
        'curve_clip':b'q 0 0 m 0 260 320 260 320 0 c h W n '+body+b'Q',
        'marked':b'/Artifact BMC '+body+b'EMC',
        'compatibility':b'BX '+body+b'EX',
        'extgstate':b'/Test gs '+body,
        'page_group':body,
        'default_color':body,
        'text_object':b'BT /Regular 12 Tf 20 200 Td (TEST) Tj ET '+body,
    }
    result={}
    for name,data in cases.items():
        folder=root/name
        folder.mkdir(parents=True)
        pdf=source_pdf(folder,data)
        if name in ('extgstate','page_group','default_color'):
            writer=PdfWriter(clone_from=PdfReader(pdf))
            page=writer.pages[0]
            if name=='extgstate':
                page['/Resources'][NameObject('/ExtGState')]=DictionaryObject({NameObject('/Test'):DictionaryObject({
                    NameObject('/ca'):FloatObject(1),NameObject('/CA'):FloatObject(1),NameObject('/BM'):NameObject('/Multiply')})})
            elif name=='page_group':
                page[NameObject('/Group')]=DictionaryObject({NameObject('/S'):NameObject('/Transparency'),
                    NameObject('/I'):BooleanObject(True),NameObject('/CS'):NameObject('/DeviceRGB')})
            else:
                page['/Resources'][NameObject('/ColorSpace')]=DictionaryObject({NameObject('/DefaultRGB'):NameObject('/DeviceRGB')})
            writer.write(pdf)
        ops=list(operators(program(pdf,1)))
        index=next(i for i,o in enumerate(ops) if o.name==('Tj' if name=='text_object' else 'f'))
        result[name]=inspect_boundary(pdf,index)
        if name=='plain':
            result['before_terminal_pending_path']=inspect_boundary(pdf,index-1)
        if name=='rectangle_clip':
            result['before_clip_consume']=inspect_boundary(pdf,next(i for i,o in enumerate(ops) if o.name=='W'))
    return result
