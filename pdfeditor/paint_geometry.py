"""Conservative geometry certificates over mature-renderer path commands.

Rectangle unions and holes have a finite exact cell decomposition. Curves and
non-rectangular polygons stay unknown; their bbox is never treated as a hole.
"""
from __future__ import annotations

from .model import Rect

EPS = 1e-4


def shifted(rect, dx, dy):
    return Rect(rect.x0+dx,rect.y0+dy,rect.x1+dx,rect.y1+dy)


def rectangle_subpaths(geometry):
    paths, points = [], []
    for command in geometry:
        if command[0] == 'm':
            if points:
                paths.append(points)
            points = [tuple(command[1:])]
        elif command[0] == 'l' and points:
            points.append(tuple(command[1:]))
        elif command[0] == 'h' and points:
            paths.append(points);points = []
        else:
            return None
    if points:
        paths.append(points)  # PDF fills implicitly close every subpath.
    result = []
    for points in paths:
        if len(points)>1 and points[0]==points[-1]:
            points.pop()
        if len(points)<4:
            return None
        pairs = list(zip(points,points[1:]+points[:1]))
        if any(abs(a[0]-b[0])>EPS and abs(a[1]-b[1])>EPS for a,b in pairs):
            return None
        x0,x1 = min(x for x,y in points),max(x for x,y in points)
        y0,y1 = min(y for x,y in points),max(y for x,y in points)
        area = sum(a[0]*b[1]-a[1]*b[0] for a,b in pairs)/2
        # A rectilinear path on the four edges must traverse the perimeter
        # exactly once. This excludes inward notches and repeated windings.
        length = sum(abs(a[0]-b[0])+abs(a[1]-b[1]) for a,b in pairs)
        if (x1-x0<=EPS or y1-y0<=EPS or abs(abs(area)-(x1-x0)*(y1-y0))>EPS
                or abs(length-2*(x1-x0+y1-y0))>EPS):
            return None
        result.append((Rect(x0,y0,x1,y1),1 if area>0 else -1))
    return result if result else None


def fill_cells(paint):
    if paint['kind'] not in ('fill-path','clip-path'):
        return None
    rectangles = rectangle_subpaths(paint['geometry'])
    if rectangles is None or len(rectangles)>32:
        return None
    xs = sorted({v for r,_ in rectangles for v in (r.x0,r.x1)})
    ys = sorted({v for r,_ in rectangles for v in (r.y0,r.y1)})
    cells = []
    for x0,x1 in zip(xs,xs[1:]):
        for y0,y1 in zip(ys,ys[1:]):
            x,y = (x0+x1)/2,(y0+y1)/2
            weights = [w for r,w in rectangles if r.x0<x<r.x1 and r.y0<y<r.y1]
            if (len(weights)%2 if paint.get('even_odd') else sum(weights)!=0):
                cells.append(Rect(x0,y0,x1,y1))
    return cells


def intersects_fill(paint, rect):
    cells = fill_cells(paint)
    if cells is None:
        return None
    return any(Rect(c.x0-EPS,c.y0-EPS,c.x1+EPS,c.y1+EPS).intersects(rect) for c in cells)


def contains_fill(paint, rect):
    cells = fill_cells(paint)
    if cells is None:
        return False
    # Test a slightly enlarged query so numerical edge uncertainty does not
    # turn a border-touching glyph into a containment certificate.
    query = Rect(rect.x0-EPS,rect.y0-EPS,rect.x1+EPS,rect.y1+EPS)
    area = sum(max(0,min(c.x1,query.x1)-max(c.x0,query.x0))*
               max(0,min(c.y1,query.y1)-max(c.y0,query.y0)) for c in cells)
    return abs(area-(query.x1-query.x0)*(query.y1-query.y0))<1e-6
