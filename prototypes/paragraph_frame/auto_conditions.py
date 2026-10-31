"""Approved paragraph editing conditions stored in the PDF page (PieceInfo).

A PDF does not store the author's text frame. After an edit, the saved PDF
alone can therefore lose the evidence the first inference used (a paragraph
shortened to one line has no continuation edge and no observable leading).
B2 stores what the user approved, not a claim about the author's frame:

* the page ``/PieceInfo`` entry ``/PdfEngineB2`` (PDF 32000-1 §14.5, page-piece
  dictionaries for conforming-product private data) holds one JSON stream;
* each record names one logical paragraph by the exact drawn lines it had when
  saved (text, x, baseline, end, font resource/size/fill), and the approved
  left/right/first-line x/leading/bottom with their provenance;
* a record is applied only when every recorded line is drawn again exactly
  and no protected paint has entered the approved width (paint already there
  when the record was written is listed in it). Otherwise the record
  is reported stale and fresh inference is used, so an external edit never
  revives an old condition silently.

PDFs without the entry use the ordinary inference unchanged.
"""
from datetime import datetime, timezone
import json
import math
import pymupdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject
from . import text_space

KEY = '/PdfEngineB2'
SCHEMA = 'pdfengine-b2-paragraph-conditions-1'
TOLERANCE = .01  # points; trace origins are single precision


def line_record(c, row):
    """The drawn identity of one analysed row: text, position and paint style."""
    ids = row['glyph_ids']
    styles = set()
    for e in c.events:
        if any(i in ids for ch in e.chars for i in ch.source_orders):
            s = e.state
            # Page-space size: ``1 Tf`` under ``Tm 9.6 0 0 9.6`` and ``9.6 Tf`` are one style.
            size = text_space.size_of(e)
            styles.add((s.font.name, round(size if size is not None else s.size, 4), s.fill[0], tuple(round(float(v), 4) for v in s.fill[1])))
    return dict(text=''.join(c.actual[i]['unicode'] for i in ids), x=round(c.actual[ids[0]]['origin'][0], 3),
                baseline=round(row['baseline'], 3), end=round(row['pen_end'], 3),
                styles=[list(x[:3]) + [list(x[3])] for x in sorted(styles)])


def read(c, page):
    """All stored records of the page, or ([], reason) when absent/unreadable."""
    try:
        piece = c.reader.pages[page - 1].get('/PieceInfo')
        entry = piece.get_object().get(KEY) if piece is not None else None
        if entry is None:
            return [], 'absent'
        entry = entry.get_object()
        data = json.loads(entry['/Private'].get_object().get_data().decode('utf-8'))
        if data.get('schema') != SCHEMA or data.get('page') != page or not isinstance(data.get('records'), list):
            return [], 'unreadable: unsupported schema or page'
        records = [r for r in data['records'] if valid(r, c.page.rect.width, c.page.rect.height)]
        page_modified = c.reader.pages[page - 1].get('/LastModified')
        status = ('page modified after the conditions were stored'
                  if page_modified is not None and str(page_modified) != str(entry.get('/LastModified')) else 'present')
        dropped = len(data['records']) - len(records)
        if dropped:
            status += f'; {dropped} malformed record(s) ignored'
        return records, status
    except Exception as exc:  # malformed private data is never trusted
        return [], 'unreadable: ' + type(exc).__name__


def _number(value, low=-math.inf, high=math.inf):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def valid(record, width, height):
    """Whether a stored record has every field B2 reads, with values on the page.

    A record written by another product, truncated or edited by hand is not
    repaired; it is dropped, and its paragraph falls back to inference.
    """
    if isinstance(record, dict) and record.get('kind') == 'flow':
        return valid_flow(record, width, height)
    try:
        lines, cond, origin, approval = record['lines'], record['conditions'], record['origin'], record['approval']
        if not isinstance(lines, list) or not lines or not isinstance(record['provenance'], dict):
            return False
        for line in lines:
            if (not isinstance(line['text'], str) or not line['text'] or not isinstance(line['styles'], list)
                    or not all(_number(line[k], -1, width + 1) for k in ('x', 'end'))
                    or not _number(line['baseline'], 0, height) or line['end'] < line['x']):
                return False
        if (not all(_number(cond[k], -1, width + 1) for k in ('left', 'right', 'first_x'))
                or not cond['left'] < cond['right'] or not _number(cond['line_height'], 0, height) or cond['line_height'] <= 0
                or not _number(cond['bottom'], 0, height + 1)
                or cond.get('align', 'start') not in ('start', 'justify', 'justify-cjk')):
            return False
        if not isinstance(origin['provenance']['line_height'], str) or not isinstance(origin['values'], dict):
            return False
        # Paint may extend beyond the page (a background image): finite bounds only.
        known = record.get('present', [])
        if not isinstance(known, list) or not all(isinstance(k, list) and len(k) == 5 and isinstance(k[0], str)
                                                  and all(_number(v) for v in k[1:]) for k in known):
            return False
        return type(approval['count']) is int and approval['count'] >= 1
    except (KeyError, TypeError, AttributeError):
        return False


def _valid_lines(lines, width, height):
    if not isinstance(lines, list) or not lines:
        return False
    for line in lines:
        if (not isinstance(line['text'], str) or not line['text'] or not isinstance(line['styles'], list)
                or not all(_number(line[k], -1, width + 1) for k in ('x', 'end'))
                or not _number(line['baseline'], 0, height) or line['end'] < line['x']):
            return False
    return True


def valid_flow(record, width, height):
    """A stored column flow: every line with its paragraph, the approved regions and spacing."""
    try:
        lines, regions, cond, approval = record['lines'], record['regions'], record['conditions'], record['approval']
        if not _valid_lines(lines, width, height) or not all(type(l['paragraph']) is int and l['paragraph'] >= 0 for l in lines):
            return False
        if [l['paragraph'] for l in lines] != sorted(l['paragraph'] for l in lines):
            return False
        if not isinstance(regions, list) or not regions:
            return False
        for r in regions:
            if (not all(_number(r[k], -1, width + 1) for k in ('left', 'right')) or not r['left'] < r['right']
                    or not all(_number(r[k], 0, height) for k in ('top', 'bottom')) or r['top'] > r['bottom']):
                return False
        if (not all(_number(cond[k], 0, height) and cond[k] > 0 for k in ('pitch', 'gap', 'size'))
                or cond['align'] not in ('start', 'justify', 'justify-cjk')):
            return False
        if not isinstance(record['links'], list) or not isinstance(record['provenance'], dict) or not isinstance(record['origin'], dict):
            return False
        return type(approval['count']) is int and approval['count'] >= 1
    except (KeyError, TypeError, AttributeError):
        return False


def flow_intrusions(c, record, flow_ids, paint):
    """Protected paint or foreign text now inside an approved region of a stored flow."""
    size = record['conditions']['size']
    hits = []
    for r in record['regions']:
        band = pymupdf.Rect(r['left'], r['top'] - size, r['right'], r['bottom'] + .3 * size)
        for o in paint:
            box = pymupdf.Rect(o['bounds'])
            if o['kind'] in ('filled-vector', 'image') and box.contains(band):
                continue  # a background behind the whole region
            overlap = band & box
            if not overlap.is_empty and overlap.width > .05 and overlap.height > .05:
                hits.append(o['kind'])
        for i, g in enumerate(c.actual):
            if i not in flow_ids and g['unicode'].strip():
                overlap = band & pymupdf.Rect(g['bbox'])
                if not overlap.is_empty and overlap.width > .05 and overlap.height > .05:
                    hits.append('foreign-text')
    return hits


def match(c, record, rows):
    """Current rows that redraw every recorded line exactly, in order, or None."""
    found = []
    for line in record['lines']:
        hits = [r for r in rows if r not in found and abs(r['baseline'] - line['baseline']) <= TOLERANCE]
        hits = [r for r in hits if line_record(c, r) == line or _close(line_record(c, r), line)]
        if len(hits) != 1:
            return None
        found.append(hits[0])
    return found


def _close(a, b):
    return (a['text'] == b['text'] and a['styles'] == b['styles']
            and all(abs(a[k] - b[k]) <= TOLERANCE for k in ('x', 'baseline', 'end')))


def overlaps(record, rows):
    """Whether a record names any of these rows' baselines (used to report staleness)."""
    return any(abs(r['baseline'] - line['baseline']) <= TOLERANCE and r['x'] < line['end'] and line['x'] < r['pen_end']
               for line in record['lines'] for r in rows)


def _band(conditions, rows):
    return pymupdf.Rect(conditions['left'], min(r['top'] for r in rows), conditions['right'], max(r['bottom'] for r in rows))


def _inside(band, o):
    overlap = band & pymupdf.Rect(o['bounds'])
    return not overlap.is_empty and overlap.width > .05 and overlap.height > .05


def present(conditions, rows, obstacles):
    """Protected paint already inside the approved width when the record is
    written (a form field beside the last line, an inline icon's paths); it is
    there by approval, not an intrusion. Foreign text is never recorded."""
    band = _band(conditions, rows)
    return [[o['kind']] + [round(v, 3) for v in o['bounds']] for o in obstacles
            if o['kind'] not in ('container', 'foreign-text') and _inside(band, o)]


def intrusions(record, rows, obstacles):
    """Protected paint now inside the approved width of the paragraph's current lines.

    Paint the record lists as present at approval, at the same place, is not
    an intrusion.
    """
    band = _band(record['conditions'], rows)
    known = record.get('present', [])
    hits = []
    for o in obstacles:
        if o['kind'] == 'container':
            continue
        if any(k[0] == o['kind'] and all(abs(a - b) <= .05 for a, b in zip(k[1:], o['bounds'])) for k in known):
            continue
        if _inside(band, o):
            hits.append(o['kind'])
    return hits


def make(c, rows, conditions, provenance, origin, approval, present=(), layout=None):
    """``layout``: the paragraph as first approved (auto_restore), carried over."""
    record = dict(lines=[line_record(c, r) for r in rows], conditions=conditions, provenance=provenance,
                  origin=origin, approval=approval)
    if present:
        record['present'] = list(present)
    if layout:
        record['layout'] = layout
    return record


def make_flow(c, lines, regions, conditions, links, provenance, origin, approval):
    """``lines`` is a list of (row, paragraph index) in reading order."""
    return dict(kind='flow', lines=[dict(line_record(c, r), paragraph=p) for r, p in lines], regions=regions, conditions=conditions,
                links=links, provenance=provenance, origin=origin, approval=approval)


def shifted(record, dy):
    """A follower record after a rigid vertical translation of its lines."""
    from .auto_restore import shifted as shifted_layout
    result = json.loads(json.dumps(record))
    for line in result['lines']:
        line['baseline'] = round(line['baseline'] + dy, 3)
    if result.get('layout'):
        result['layout'] = shifted_layout(result['layout'], dy)
    return result


def attach(writer, page, records, page_number, moved=()):
    """Write the records into the page PieceInfo, keeping other products' entries.

    ``moved``: the glyphs of following paragraphs an edit moved (moved_entry)."""
    stamp = 'D:' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S') + 'Z'
    data = dict(schema=SCHEMA, page=page_number, records=records)
    if moved:
        data['moved'] = list(moved)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    stream = DecodedStreamObject()
    stream.set_data(payload.encode('utf-8'))
    existing = page.get('/PieceInfo')
    pieces = DictionaryObject(dict(existing.get_object())) if existing is not None else DictionaryObject()
    pieces[NameObject(KEY)] = DictionaryObject({NameObject('/LastModified'): TextStringObject(stamp),
                                                 NameObject('/Private'): writer._add_object(stream.flate_encode())})
    page[NameObject('/PieceInfo')] = pieces
    page[NameObject('/LastModified')] = TextStringObject(stamp)
    return stamp


# The rows of following paragraphs moved by an edit (with their inline placed
# objects or an approved line decoration) are recorded as they were observed
# before the move, translated. The page's row observation clusters baselines
# across all columns, so after a move another column's row can take a blank of
# the moved line, or join its rows: on reopen the recorded rows are used again,
# but only when every recorded glyph is drawn exactly where recorded and the
# rows now holding them hold nothing else.
ROW_KEYS = ('text', 'baseline', 'x', 'right', 'pen_end', 'top', 'bottom', 'size')


def moved_entry(c, rows, dy, approval, kind=None):
    """The record of observed ``rows`` (page_rows) translated by ``dy`` (page y);
    ``kind`` 'chain': the rows moved as an approved following element."""
    out = []
    for r in rows:
        row = {k: r[k] for k in ROW_KEYS}
        for k in ('baseline', 'top', 'bottom'):
            row[k] = r[k] + dy
        x0, y0, x1, y1 = r['bbox']
        row['bbox'] = [x0, y0 + dy, x1, y1 + dy]
        row['starts'] = list(r.get('starts', []))
        row['glyphs'] = [[c.actual[i]['unicode'], c.actual[i]['gid'], round(c.actual[i]['origin'][0], 3), round(c.actual[i]['origin'][1] + dy, 3)]
                         for i in r['glyph_ids']]
        out.append(row)
    return dict(rows=out, approval=approval, **({'as': kind} if kind else {}))


def read_moved(c, page):
    """The moved-row records of the page (malformed ones dropped)."""
    try:
        piece = c.reader.pages[page - 1].get('/PieceInfo')
        entry = piece.get_object().get(KEY) if piece is not None else None
        if entry is None:
            return []
        data = json.loads(entry.get_object()['/Private'].get_object().get_data().decode('utf-8'))
        if data.get('schema') != SCHEMA or data.get('page') != page or not isinstance(data.get('moved', []), list):
            return []
        w, h = c.page.rect.width, c.page.rect.height
        good = lambda r: (isinstance(r, dict) and isinstance(r.get('text'), str) and all(_number(r.get(k), -1, max(w, h) + 1) for k in ROW_KEYS[1:])
                          and isinstance(r.get('glyphs'), list) and r['glyphs']
                          and all(isinstance(g, list) and len(g) == 4 and isinstance(g[0], str) and type(g[1]) is int
                                  and _number(g[2], -1, w + 1) and _number(g[3], -1, h + 1) for g in r['glyphs']))
        return [m for m in data.get('moved', []) if isinstance(m, dict) and isinstance(m.get('rows'), list) and m['rows'] and all(map(good, m['rows']))]
    except Exception:  # malformed private data is never trusted
        return []


def resolve_moved(c, entry):
    """The recorded rows with the page's glyph ids drawn exactly as recorded (0.01 pt), or None."""
    taken, rows = set(), []
    for r in entry['rows']:
        ids = []
        for u, gid, x, y in r['glyphs']:
            hits = [i for i, g in enumerate(c.actual) if g['unicode'] == u and g['gid'] == gid and i not in taken
                    and abs(g['origin'][0] - x) <= TOLERANCE and abs(g['origin'][1] - y) <= TOLERANCE]
            if len(hits) != 1:
                return None
            taken.add(hits[0])
            ids.append(hits[0])
        rows.append(dict({k: r[k] for k in ROW_KEYS}, bbox=list(r['bbox']), starts=list(r.get('starts', [])), glyph_ids=ids))
    return rows


def apply_moved(c, page, rows):
    """``rows`` with the moved-row records of the page applied where they hold (see above)."""
    for n, entry in enumerate(read_moved(c, page)):
        recorded = resolve_moved(c, entry)
        if recorded is None:
            continue
        ids = {i for r in recorded for i in r['glyph_ids']}
        holding = [r for r in rows if ids & set(r['glyph_ids'])]
        if not holding or any(set(r['glyph_ids']) - ids for r in holding):
            continue
        at = min(rows.index(r) for r in holding)
        rest = [r for r in rows if r not in holding]
        # The rows keep the record they came from: on reopen they are not
        # grouped with other rows (auto_detect.observe), and lines that moved
        # as an approved following element stay one (auto_detect.detect).
        for k, r in enumerate(recorded):
            r.update(id=f'p{page}-moved{n}-{k}', moved=n, moved_as=entry.get('as'))
        rows = rest[:at] + recorded + rest[at:]
    return rows
