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
  and no protected paint has entered the approved width. Otherwise the record
  is reported stale and fresh inference is used, so an external edit never
  revives an old condition silently.

PDFs without the entry use the ordinary inference unchanged.
"""
from datetime import datetime, timezone
import json
import pymupdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, TextStringObject

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
            styles.add((s.font.name, round(s.size, 4), s.fill[0], tuple(round(float(v), 4) for v in s.fill[1])))
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
        page_modified = c.reader.pages[page - 1].get('/LastModified')
        if page_modified is not None and str(page_modified) != str(entry.get('/LastModified')):
            return data['records'], 'page modified after the conditions were stored'
        return data['records'], 'present'
    except Exception as exc:  # malformed private data is never trusted
        return [], 'unreadable: ' + type(exc).__name__


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


def intrusions(record, rows, obstacles):
    """Protected paint now inside the approved width of the paragraph's current lines."""
    top = min(r['top'] for r in rows)
    bottom = max(r['bottom'] for r in rows)
    band = pymupdf.Rect(record['conditions']['left'], top, record['conditions']['right'], bottom)
    hits = []
    for o in obstacles:
        if o['kind'] == 'container':
            continue
        overlap = band & pymupdf.Rect(o['bounds'])
        if not overlap.is_empty and overlap.width > .05 and overlap.height > .05:
            hits.append(o['kind'])
    return hits


def make(c, rows, conditions, provenance, origin, approval):
    return dict(lines=[line_record(c, r) for r in rows], conditions=conditions, provenance=provenance,
                origin=origin, approval=approval)


def shifted(record, dy):
    """A follower record after a rigid vertical translation of its lines."""
    result = json.loads(json.dumps(record))
    for line in result['lines']:
        line['baseline'] = round(line['baseline'] + dy, 3)
    return result


def attach(writer, page, records, page_number):
    """Write the records into the page PieceInfo, keeping other products' entries."""
    stamp = 'D:' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S') + 'Z'
    payload = json.dumps(dict(schema=SCHEMA, page=page_number, records=records), ensure_ascii=False, sort_keys=True)
    stream = DecodedStreamObject()
    stream.set_data(payload.encode('utf-8'))
    existing = page.get('/PieceInfo')
    pieces = DictionaryObject(dict(existing.get_object())) if existing is not None else DictionaryObject()
    pieces[NameObject(KEY)] = DictionaryObject({NameObject('/LastModified'): TextStringObject(stamp),
                                                 NameObject('/Private'): writer._add_object(stream.flate_encode())})
    page[NameObject('/PieceInfo')] = pieces
    page[NameObject('/LastModified')] = TextStringObject(stamp)
    return stamp
