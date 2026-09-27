"""Read-only evidence that the reviewed source destination is genuinely empty."""
import math
import subprocess
import time

from PIL import Image, ImageChops
import pymupdf

from pdfeditor.content_stream import ContentPage
from pdfeditor.continuation import clip_contains, require_empty, require_inside_clip
from evaluations.continuation import evaluate as single, scope_destination as scope
from evaluations.realpdf.evaluate import poppler_render


def source_region_review(directory, chosen):
    """Inspect the unchanged PDF and render its whole page and exact empty area.

    All raster files are raw run evidence. Their visual inspection is a separate
    reviewer action; the machine check requires every area pixel to be white.
    """
    started = time.monotonic()
    target = directory / 'source-review'
    target.mkdir(parents=True, exist_ok=True)
    bounds = scope.REGION['bounds']
    clip = chosen['clip_constraint']
    if (single.source_sha(single.SOURCE) != single.SOURCE_SHA
            or chosen['page'] != scope.PAGE or chosen['scope']['q_depth'] != 2
            or chosen['reasons'] or 'ctm_compensation' not in chosen):
        raise ValueError('the source or reviewed candidate differs')
    require_inside_clip(dict(authority=dict(clip_constraint=clip), bounds=bounds))
    content = ContentPage(single.SOURCE, scope.PAGE)
    try:
        require_empty(content, bounds)
        paints = content.page.get_bboxlog()
        intersections = [dict(seqno=i, kind=kind, bbox=list(box))
                         for i, (kind, box) in enumerate(paints)
                         if pymupdf.Rect(bounds).intersects(pymupdf.Rect(box))]
        if intersections or content.errors:
            raise ValueError('source area intersects fixed paint or has interpreter errors')
        content.page.get_pixmap(dpi=144, alpha=False, colorspace=pymupdf.csRGB).save(
            target / 'source-page10-mupdf-144.png')
        content.page.get_pixmap(dpi=300, alpha=False, colorspace=pymupdf.csRGB,
                               clip=pymupdf.Rect(bounds)).save(target / 'source-region-mupdf-300.png')
        page_rectangle = list(content.page.rect)
    finally:
        content.close()
    renderer = single.renderer_paths.DEFAULT_POPPLER
    rendered = poppler_render(renderer, single.SOURCE, scope.PAGE,
                              target / 'source-page10-poppler-144.png', dpi=144)
    if not rendered['rendered']:
        raise ValueError('source full-page Poppler render failed: ' + str(rendered))
    scale = 300 / 72
    x, y = math.floor(bounds[0] * scale), math.floor(bounds[1] * scale)
    x1, y1 = math.ceil(bounds[2] * scale), math.ceil(bounds[3] * scale)
    command = [str(renderer), '-png', '-r', '300', '-f', str(scope.PAGE), '-l', str(scope.PAGE),
               '-singlefile', '-x', str(x), '-y', str(y), '-W', str(x1-x), '-H', str(y1-y),
               str(single.SOURCE), str(target / 'source-region-poppler-300')]
    subprocess.run(command, capture_output=True, check=True, timeout=90)
    rasters = {}
    for name in ('mupdf', 'poppler'):
        filename = f'source-region-{name}-300.png'
        with Image.open(target / filename) as image:
            rgb = image.convert('RGB')
            difference = ImageChops.difference(rgb, Image.new('RGB', rgb.size, 'white'))
            r, g, b = difference.split()
            histogram = ImageChops.lighter(ImageChops.lighter(r, g), b).histogram()
            nonwhite = sum(histogram[1:])
            if nonwhite:
                raise ValueError(name + ' source area is not blank')
            rasters[name] = dict(file=filename, dpi=300, pixel_size=list(rgb.size), nonwhite_pixels=nonwhite)
    result = dict(source_sha256=single.SOURCE_SHA, page=scope.PAGE, page_rectangle=page_rectangle,
        boundary_id=chosen['boundary_id'], ordinal=chosen['ordinal'], destination_bounds=bounds,
        require_empty='passed', content_errors=[], bboxlog_paints=len(paints), fixed_paint_intersections=[],
        clip_rectangle=clip['rectangle'], bounds_inside_clip=clip_contains(clip, bounds), region_rasters=rasters,
        full_page_rasters=['source-page10-mupdf-144.png', 'source-page10-poppler-144.png'],
        elapsed_seconds=round(time.monotonic()-started, 3))
    single.write(target / 'region-proof.json', result)
    return result
