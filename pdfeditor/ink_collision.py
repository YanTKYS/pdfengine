"""Pre-mutation separation certificates for source-linked text translations.

An outline envelope is a sufficient separation test, not an ink intersection
solver. Unknown state, outline failure and touching envelopes never authorize
a placement. Ownership, clips, images and vector guards remain separate.
"""
from io import BytesIO
import math
import re

import pymupdf
from fontTools.pens.boundsPen import ControlBoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

from .composition import _observations
from .model import Rect
from .paint_geometry import shifted
from .paint_provenance import _PaintDevice, _digest, _matrix, _path, _sha, interpreted_paints
from .proof_session import evidence_cache, revision

m=pymupdf.mupdf
POLICY='embedded-truetype-outline-envelopes-v1'
# One pixel per outline at the independent evaluation resolution (144 dpi),
# plus a numerical allowance. This is a physical clearance policy, not a
# guarantee for arbitrary low-resolution rasterization or font hinting.
MARGIN_PT=.501


def _hull(commands):
    points=[]
    for command in commands:
        if command[0] not in ('m','l','c','h'):
            raise ValueError('unsupported outline command')
        points.extend(zip(command[1::2],command[2::2]))
    if not points:return None
    xs,ys=zip(*points)
    if not all(math.isfinite(v) and abs(v)<1e6 for v in (*xs,*ys)):
        raise ValueError('outline coordinates outside numerical contract')
    return Rect(min(xs),min(ys),max(xs),max(ys))


def _expanded(rect):
    return Rect(rect.x0-MARGIN_PT,rect.y0-MARGIN_PT,rect.x1+MARGIN_PT,rect.y1+MARGIN_PT)


def _separated(a,b):
    # Closed sets: touching is not a certificate, unlike Rect.intersects.
    return max(b.x0-a.x1,a.x0-b.x1,b.y0-a.y1,a.y0-b.y1)>0


def _bitmap_bounds(font,name,transform):
    """Enclose every static monochrome strike by its declared glyph metrics.

    Even renderers which choose an embedded strike instead of the outline
    remain inside this union. No bitmap pixels are rendered or tested here.
    """
    if 'EBDT' not in font and 'EBLC' not in font:return None
    if 'EBDT' not in font or 'EBLC' not in font:raise ValueError('incomplete embedded bitmap tables')
    strikes=font['EBLC'].strikes;data=font['EBDT'].strikeData
    if len(strikes)!=len(data):raise ValueError('embedded bitmap strike tables disagree')
    result=None;a,b,c,d,e,f=transform
    for strike,glyphs in zip(strikes,data):
        size=strike.bitmapSizeTable
        if size.bitDepth!=1 or size.flags!=1 or min(size.ppemX,size.ppemY)<=0:
            raise ValueError('unsupported embedded bitmap strike')
        if name not in glyphs:continue
        bitmap=glyphs[name]
        if bitmap.__class__.__name__ not in ('ebdt_bitmap_format_1','ebdt_bitmap_format_2','ebdt_bitmap_format_6','ebdt_bitmap_format_7'):
            raise ValueError('compound or unsupported embedded glyph bitmap')
        metrics=bitmap.metrics
        x=getattr(metrics,'BearingX',getattr(metrics,'horiBearingX',None))
        y=getattr(metrics,'BearingY',getattr(metrics,'horiBearingY',None))
        if x is None or y is None:raise ValueError('embedded bitmap lacks horizontal bearings')
        if metrics.width==0 or metrics.height==0:continue
        x0,x1=x/size.ppemX,(x+metrics.width)/size.ppemX
        y0,y1=(y-metrics.height)/size.ppemY,y/size.ppemY
        points=[(x*a+y*c+e,x*b+y*d+f) for x in (x0,x1) for y in (y0,y1)]
        xs,ys=zip(*points);bounds=Rect(min(xs),min(ys),max(xs),max(ys))
        result=bounds if result is None else result.union(bounds)
    return result


class _OutlineDevice(_PaintDevice):
    def __init__(self, programs):
        super().__init__({})
        self.programs=programs;self.fonts={};self.ink={}

    def close_fonts(self):
        for font in self.fonts.values():font.close()

    def outline(self,font,item,matrix,paint,stroke,kind,wmode):
        if (kind!='fill-text' or stroke is not None or wmode!=0 or paint.get('opacity')!=1
                or self.groups or self.layers or paint.get('colorspace') not in ('DeviceGray','DeviceRGB','DeviceCMYK')
                or any(c['kind']!='clip-path' for c in self.clips)):
            raise ValueError('outline refinement requires opaque horizontal fill without special scopes')
        flags=m.ll_fz_font_flags(font.m_internal)
        if (not flags.embed or any(getattr(flags,k) for k in ('ft_substitute','ft_stretch','fake_bold','fake_italic'))
                or not m.fz_font_ft_face(font) or item.gid<0):
            raise ValueError('substitute, synthetic or non-outline font')
        data=m.fz_buffer_extract_copy(m.FzBuffer(m.ll_fz_keep_buffer(font.m_internal.buffer)))
        fingerprint=_sha(data)
        if fingerprint not in self.programs:
            raise ValueError('renderer font bytes do not match an embedded page resource')
        if fingerprint not in self.fonts:
            candidate=TTFont(BytesIO(data))
            if ('glyf' not in candidate or 'head' not in candidate
                    or any(k in candidate for k in ('fvar','COLR','SVG ','CBDT','sbix'))):
                candidate.close()
                raise ValueError('only static monochrome TrueType outlines are certified')
            self.fonts[fingerprint]=candidate
        parsed=self.fonts[fingerprint];order=parsed.getGlyphOrder()
        if not 0<=item.gid<len(order):raise ValueError('glyph ID outside embedded font')
        transform=_matrix(matrix)
        if not all(math.isfinite(v) and abs(v)<1e6 for v in transform) or abs(transform[0]*transform[3]-transform[1]*transform[2])<1e-8:
            raise ValueError('unsupported glyph transform')
        outline=m.fz_outline_glyph(font,item.gid,matrix)
        if not outline:raise ValueError('renderer did not provide a glyph outline')
        commands=_path(outline.m_internal,m.FzMatrix())
        renderer=_hull(commands)
        # Independently enclose the unhinted embedded outline. FreeType's
        # high-resolution outline may have hint adjustments; neither bound
        # is allowed to shrink the other. Bezier control hulls contain curves.
        glyphs=parsed.getGlyphSet();pen=ControlBoundsPen(glyphs)
        upem=parsed['head'].unitsPerEm
        glyphs[order[item.gid]].draw(TransformPen(pen,tuple(v/upem for v in transform[:4])+tuple(transform[4:])))
        embedded=Rect(*pen.bounds) if pen.bounds is not None else None
        if (renderer is None)!=(embedded is None):raise ValueError('empty outline disagreement')
        bounds=renderer.union(embedded) if renderer is not None else None
        bitmap=_bitmap_bounds(parsed,order[item.gid],transform)
        if bitmap is not None:bounds=bitmap if bounds is None else bounds.union(bitmap)
        if bounds is not None and not all(math.isfinite(v) and abs(v)<1e6 for v in bounds.tuple()):
            raise ValueError('embedded outline outside numerical contract')
        return dict(status='proven',bounds=list(bounds.tuple()) if bounds else None,
                    font_sha256=fingerprint,outline_sha256=_digest(commands),
                    embedded_bitmap_enclosed=bitmap is not None,
                    clip_policy='full unclipped outline; clipping never shrinks the certificate')

    def text(self,kind,text,ctm,paint,stroke=None):
        start=len(self.events)
        super().text(kind,text,ctm,paint,stroke)
        cursor=start;span=text.head
        while span:
            wrapper=m.FzTextSpan(span);font=wrapper.font()
            for index in range(span.len):
                item=wrapper.items(index)
                trm=m.FzMatrix(*(_matrix(wrapper.trm())[:4]+[item.x,item.y]))
                try:
                    value=self.outline(font,item,m.fz_concat(trm,m.FzMatrix(ctm)),paint,stroke,kind,span.wmode)
                except Exception as exc:
                    value=dict(status='unknown',reason=str(exc))
                self.ink[cursor]=value;cursor+=1
            span=span.next


@evidence_cache(lambda source,page=1:(revision(source),page,POLICY),limit=4)
def source_ink(source,page=1):
    """Read actual font instances and transformed outlines, without rasterizing."""
    before=revision(source);device=None
    document=pymupdf.open(source)
    try:
        target=document[page-1]
        if target.rotation:target.set_rotation(0)
        programs={_sha(document.extract_font(f[0])[3]) for f in target.get_fonts(full=True)
                  if f[1]=='ttf'}
        device=_OutlineDevice(programs)
        m.fz_run_page_contents(target.this,device,m.FzMatrix(),m.FzCookie())
        m.fz_close_device(device)
        observation=interpreted_paints(source,page)
        expected=[{k:v for k,v in e.items() if k not in ('index','fingerprint')} for e in observation['events']]
        if device.events!=expected or device.errors or observation['errors'] or device.clips or device.groups or device.layers:
            return dict(status='unknown',reason='outline adapter paint/state differs from source observation')
        if before!=revision(source):raise ValueError('PDF changed while reading outlines')
        return dict(status='proven',source_sha256=before,page=page,events=expected,ink=device.ink)
    finally:
        if device is not None:device.close_fonts()
        document.close()


def certify_text_translation(source, page, selected, dx, dy):
    """Certify every selected/unselected glyph pair, including empty outlines.

    This fallback requires whole-page text correspondence; unknown distant
    text also prevents a new permission. It does not replace vector/image,
    annotation, clip, ownership, no-op or post-save verification.
    """
    report=dict(policy=POLICY,margin_per_outline_pt=MARGIN_PT,status='unknown')
    if not selected or not all(math.isfinite(v) for v in (dx,dy)):
        return dict(report,reason='translation requires selected glyphs and finite displacement')
    try:
        source_hash=revision(source);data=source_ink(source,page)
        if data['status']!='proven':return dict(report,reason=data['reason'])
        with pymupdf.open(source) as document:
            target=document[page-1]
            if target.rotation:target.set_rotation(0)
            glyphs=_observations(target)
        boxes={};used=set()
        for index,g in enumerate(glyphs):
            candidates=[]
            for n,e in enumerate(data['events']):
                if (e.get('kind')!='fill-text' or e['gid']!=g['glyph_id'] or e['unicode']!=ord(g['unicode'])
                        or re.sub(r'^[A-Z]{6}\+','',e['font'])!=re.sub(r'^[A-Z]{6}\+','',g['font'])
                        or g['paint_type']!=0):continue
                a,b,c,d,x,y=e['matrix'];gx,gy=e['glyph_position']
                origin=(gx*a+gy*c+x,gx*b+gy*d+y)
                if max(abs(v-w) for v,w in zip(origin,g['origin']))<=.002:candidates.append(n)
            if len(candidates)!=1 or candidates[0] in used:
                return dict(report,reason='glyph-to-outline paint correspondence is ambiguous or unsupported')
            n=candidates[0];used.add(n);ink=data['ink'][n]
            if ink['status']!='proven':return dict(report,reason=ink['reason'])
            boxes[index]=Rect(*ink['bounds']) if ink['bounds'] is not None else None
        if not selected<=boxes.keys() or len(used)!=len(data['ink']):
            return dict(report,reason='outline text coverage differs from extraction')
        count=0;clearance=None
        for i in sorted(selected):
            if boxes[i] is None:continue
            a=_expanded(shifted(boxes[i],dx,dy))
            for j,b in boxes.items():
                if j in selected or b is None:continue
                b=_expanded(b);count+=1
                gap=max(b.x0-a.x1,a.x0-b.x1,b.y0-a.y1,a.y0-b.y1)
                if not _separated(a,b):
                    return dict(report,status='not_certified',reason='expanded outline envelopes overlap or touch',
                                selected_glyph=i,obstacle_glyph=j)
                clearance=gap if clearance is None else min(clearance,gap)
        if source_hash!=revision(source) or data['source_sha256']!=source_hash:
            return dict(report,reason='PDF changed during collision planning')
        return dict(report,status='disjoint',source_sha256=source_hash,page=page,
            selected_glyphs=sorted(selected),translation=[dx,dy],compared_pairs=count,
            minimum_axis_clearance_pt=clearance,outline_evidence_sha256=_digest(data),
            authorization='pre-mutation geometry; no output renderer pixels used')
    except Exception as exc:
        return dict(report,reason=str(exc))
