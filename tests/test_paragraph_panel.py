"""A rectangular background panel extended down with its paragraph on explicit approval (issue #112)."""
import copy

import pymupdf
import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, FloatObject, NameObject

from provider_font import cjk_provider_bytes
from test_paragraph_frame_tagged import LINES
from prototypes.paragraph_frame.approved_audit import audit
from prototypes.paragraph_frame.auto import approve, propose, save
from prototypes.paragraph_frame.engine import Refused

GROW = ('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で')
# The paragraph's three lines (baselines 60, 76, 92) on a panel from 46 to 97 (page y): PDF y 203, height 51.
PANEL = b'34 203 196 51 re'
FILL = b'q .9 g ' + PANEL + b' f Q\n'
OUTLINE = b'q 0 G 1 w ' + PANEL + b' S Q\n'


def panel_pdf(tmp_path, before=FILL, after=b'', name='panel.pdf', resources=None):
    """The paragraph drawn in one content stream: ``before`` it (the panel), ``after`` it."""
    path = tmp_path / name
    d = pymupdf.open()
    p = d.new_page(width=300, height=300)
    p.insert_font(fontname='Fixture', fontbuffer=cjk_provider_bytes())
    for x, y, text in LINES[:3]:
        p.insert_text((x, y), text, fontsize=11, fontname='Fixture')
    p.clean_contents()
    xref = p.get_contents()[0]
    d.update_stream(xref, before + d.xref_stream(xref) + b'\n' + after)
    d.save(path)
    d.close()
    if resources:
        w = PdfWriter(clone_from=PdfReader(path))
        res = w.pages[0]['/Resources']
        for k, v in resources.items():
            res[NameObject(k)] = v
        w.write(path)
    return path


def candidates(source, old=GROW[0], new=GROW[1]):
    return propose(str(source), 1, old, new)['evidence'].get('panels') or {}


def edit(source, out, ids=None, old=GROW[0], new=GROW[1]):
    """Save approving ``ids`` (default: the listed panel extensions); (proposal, save record)."""
    found = candidates(source, old, new)
    ids = [c['id'] for c in found.get('candidates', [])] if ids is None else ids
    p = propose(str(source), 1, old, new, panels=ids)
    accepted = approve(str(source), p, acknowledge_inference=True)
    return accepted['proposal'], save(str(source), accepted, str(out))


def rects(path):
    with pymupdf.open(path) as d:
        return [(x['type'], [round(v, 2) for v in x['rect']], x.get('fill'), x.get('color')) for x in d[0].get_drawings()]


def glyphs(path):
    with pymupdf.open(path) as d:
        return sorted((c['c'], round(c['origin'][0], 2), round(c['origin'][1], 2)) for b in d[0].get_text('rawdict')['blocks']
                      for l in b.get('lines', []) for s in l['spans'] for c in s['chars'] if c['c'].strip())


@pytest.mark.parametrize('before', [FILL, FILL + OUTLINE, b'q .9 g 0 G 1 w ' + PANEL + b' B Q\n'], ids=['fill', 'fill+outline', 'B'])
def test_an_approved_extension_moves_only_the_panel_bottom(tmp_path, before):
    source = panel_pdf(tmp_path, before)
    found = candidates(source)
    [c] = found['candidates']
    # Down by the added line (16 pt); top, sides and colours kept; listed with its operators and band.
    assert c['old'] == [34, 46, 230, 97] and c['new'] == [34, 46, 230, 113] and c['extension'] == 16
    assert all(o['operator'].endswith(' re') for o in c['operators']) and c['band'][1] <= 97 < 113 <= c['band'][3]
    # Not approved: refused, nothing written.
    with pytest.raises(Refused):
        save(str(source), approve(str(source), propose(str(source), 1, *GROW), acknowledge_inference=True), str(tmp_path / 'no.pdf'))
    assert not (tmp_path / 'no.pdf').exists()
    out = tmp_path / 'out.pdf'
    proposal, record = edit(source, out)
    assert [x['id'] for x in record['panels_extended']] == [c['id']] and proposal['panels'] == [c['id']]
    want = [(t, [34, 46, 230, 113] if r == [34, 46, 230, 97] else r, f, col) for t, r, f, col in rects(source)]
    assert rects(out) == want
    a = audit(source, out, 1, proposal, record)
    assert a['verdict'] == 'pass' and a['mode'] == 'layout' and [x['status'] for x in a['approved'] if x['what'] == 'panel'] == ['pass']


@pytest.mark.parametrize('before, after, reason', [
    # Not a rectangle: four lines.
    (b'q .9 g 34 203 m 230 203 l 230 254 l 34 254 l h f Q\n', b'', 'not one rectangle'),
    # Two rectangles painted by one fill.
    (b'q .9 g ' + PANEL + b' 240 203 40 51 re f Q\n', b'', 'not one rectangle'),
    # Clipped where the panel ends.
    (b'q 0 203 300 97 re W n .9 g ' + PANEL + b' f Q\n', b'', 'cut by its clip'),
    # A rule right below the panel.
    (FILL + b'q 0 G 1 w 34 190 m 230 190 l S Q\n', b'', 'would meet'),
    # Inside a background that ends right below it.
    (b'q .95 g 20 195 220 70 re f Q\n' + FILL, b'', 'would meet'),
    # Drawn over the paragraph.
    (b'', FILL, 'over the paragraph'),
    # The text clipped to the panel (a Word text box): the clip is not extended.
    (FILL + b'q ' + PANEL + b' W n\n', b'Q\n', 'clipped'),
])
def test_what_cannot_be_extended_is_refused_with_its_reason(tmp_path, before, after, reason):
    source = panel_pdf(tmp_path, before, after)
    found = candidates(source)
    assert not found.get('candidates') and reason in (found.get('unavailable') or ''), found
    with pytest.raises(Refused):
        edit(source, tmp_path / 'out.pdf')


def test_a_transparent_panel_is_not_extended(tmp_path):
    gs = DictionaryObject({NameObject('/GS0'): DictionaryObject({NameObject('/ca'): FloatObject(.5)})})
    source = panel_pdf(tmp_path, b'q /GS0 gs .9 g ' + PANEL + b' f Q\n', resources={'/ExtGState': gs})
    assert 'transparent' in candidates(source)['unavailable']


def test_a_background_holding_the_extended_panel_is_no_obstacle(tmp_path):
    source = panel_pdf(tmp_path, b'q .95 g 20 100 220 170 re f Q\n' + FILL)
    assert len(candidates(source)['candidates']) == 1
    edit(source, tmp_path / 'out.pdf')


def test_an_id_of_another_edit_or_page_is_no_approval(tmp_path):
    source = panel_pdf(tmp_path)
    other = panel_pdf(tmp_path, b'q .8 g ' + PANEL + b' f Q\n', name='other.pdf')
    [foreign] = candidates(other)['candidates']
    [mine] = candidates(source)['candidates']
    assert foreign['id'] != mine['id']
    with pytest.raises(Refused, match='not a candidate of this edit'):
        propose(str(source), 1, *GROW, panels=[foreign['id']])
    # Another edit of the same paragraph (another extension) has its own id.
    other_edit = candidates(source, GROW[0], '申請書は窓口と郵送と電子申請とファクスと持参とメールで')
    assert other_edit['candidates'][0]['id'] != mine['id']
    # A proposal changed after it was made is refused.
    p = propose(str(source), 1, *GROW, panels=[mine['id']])
    changed = copy.deepcopy(p)
    changed['panels'] = []
    with pytest.raises(Refused):
        approve(str(source), changed, acknowledge_inference=True)


@pytest.mark.parametrize('tamper', ['more', 'colour', 'other'])
def test_a_panel_changed_beyond_the_approval_fails_the_audit(tmp_path, tamper):
    source = panel_pdf(tmp_path, FILL + b'q .5 g 250 20 20 20 re f Q\n')
    out = tmp_path / 'out.pdf'
    proposal, record = edit(source, out)
    bad = tmp_path / 'bad.pdf'
    with pymupdf.open(out) as d:
        xref = d[0].get_contents()[0]
        data = d.xref_stream(xref)
        if tamper == 'more':
            data = data.replace(b'34 187 196 67 re', b'34 180 196 74 re')
        elif tamper == 'colour':
            data = data.replace(b'q .9 g 34 187', b'q .8 g 34 187')
        else:
            data = data.replace(b'250 20 20 20 re', b'250 20 20 25 re')
        assert data != d.xref_stream(xref)
        d.update_stream(xref, data)
        d.save(bad)
    r = audit(source, bad, 1, proposal, record)
    assert r['verdict'] == 'fail', r


def test_the_saved_pdf_alone_finds_the_extension_and_grows_again_on_a_new_approval(tmp_path):
    source = panel_pdf(tmp_path, FILL + OUTLINE)
    out = tmp_path / 'out.pdf'
    _, record = edit(source, out)
    first = record['panels_extended'][0]['id']
    # Reopened: the stored record finds the panel drawn as approved.
    back = propose(str(out), 1, GROW[1], GROW[0])
    [stored] = back['evidence']['conditions']['panels']
    assert stored['status'] == 'drawn as approved' and stored['old'] == [34, 46, 230, 97] and stored['new'] == [34, 46, 230, 113]
    # Shorter again: the panel goes back only on its own approval; without it, it stays.
    [shrink] = back['evidence']['panels']['candidates']
    assert shrink['extension'] == -16 and shrink['new'] == [34, 46, 230, 97] and shrink['id'] != first
    kept = tmp_path / 'kept.pdf'
    save(str(out), approve(str(out), back, acknowledge_inference=True), str(kept))
    assert [r for r in rects(kept) if r[1][3] == 113] and glyphs(kept) == glyphs(source)
    restored = tmp_path / 'restored.pdf'
    edit(out, restored, old=GROW[1], new=GROW[0])
    assert rects(restored) == rects(source) and glyphs(restored) == glyphs(source)
    # Longer again: a new extension, approved anew.
    again = candidates(out, GROW[1], GROW[1] + '。郵送の場合は申請書を窓口へ送付してください')
    assert [c['old'][3] for c in again['candidates']] == [113] and again['candidates'][0]['id'] != first


# --- PR #113 review: what the extension would hide; the record checked on reopen ---------------

def image_pdf(tmp_path, name='image.pdf'):
    """A photo behind the panel and its band (drawn first), the panel on it, then the paragraph."""
    path = tmp_path / name
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), False)
    for x in range(8):
        for y in range(8):
            pix.set_pixel(x, y, ((x * 30) % 256, (y * 30) % 256, 120))
    d = pymupdf.open()
    p = d.new_page(width=300, height=300)
    p.insert_image(pymupdf.Rect(20, 30, 280, 250), pixmap=pix)
    p.insert_font(fontname='Fixture', fontbuffer=cjk_provider_bytes())
    for x, y, text in LINES[:3]:
        p.insert_text((x, y), text, fontsize=11, fontname='Fixture')
    p.clean_contents()
    xref = p.get_contents()[0]
    data = d.xref_stream(xref)
    at = data.index(b'BT')
    d.update_stream(xref, data[:at] + FILL + data[at:])
    d.save(path)
    d.close()
    return path


def test_an_image_the_extension_would_cover_refuses_it(tmp_path):
    source = image_pdf(tmp_path)
    found = candidates(source)
    assert not found.get('candidates') and 'image' in found['unavailable'], found
    with pytest.raises(Refused):
        edit(source, tmp_path / 'out.pdf')


def test_a_background_drawing_not_proven_plain_refuses_it(tmp_path):
    # A plain-coloured polygon (not one rectangle) behind the panel and its band.
    source = panel_pdf(tmp_path, b'q .95 g 20 100 m 260 100 l 260 270 l 20 270 l h f Q\n' + FILL)
    found = candidates(source)
    assert not found.get('candidates') and 'not one plain filled rectangle' in found['unavailable'], found


@pytest.mark.parametrize('tamper', ['colour', 'outline', 'order'])
def test_a_saved_panel_changed_alone_is_not_taken_as_approved_on_reopen(tmp_path, tamper):
    # The saved PDF alone, its panel changed in place (same rectangle, same text): the stored record is not applied.
    source = panel_pdf(tmp_path, FILL + OUTLINE)
    out = tmp_path / 'out.pdf'
    edit(source, out)
    good = propose(str(out), 1, GROW[1], GROW[0])['evidence']['conditions']
    assert good['status'] == 'applied' and good['panels'][0]['status'] == 'drawn as approved'
    bad = tmp_path / 'bad.pdf'
    with pymupdf.open(out) as d:
        xref = d[0].get_contents()[0]
        data = d.xref_stream(xref)
        if tamper == 'colour':
            data = data.replace(b'q .9 g 34 187', b'q .8 g 34 187', 1)
        elif tamper == 'outline':
            data = data.replace(b'q 0 G 1 w 34 187', b'q 0 G 2 w 34 187', 1)
        else:
            # The panel drawn again after the text (over it), at the same place.
            fill = b'q .9 g 34 187 196 67 re f Q\n'
            assert fill in data
            data = data.replace(fill, b'', 1) + b'\n' + fill
        assert data != d.xref_stream(xref)
        d.update_stream(xref, data)
        d.save(bad)
    p = propose(str(bad), 1, GROW[1], GROW[0])
    state = p['evidence']['conditions']
    assert state['status'].startswith('stale: an approved panel extension is not drawn as approved'), state['status']
    # Nothing follows from the record: no way back is offered for it.
    assert not (p['evidence'].get('panels') or {}).get('candidates')
