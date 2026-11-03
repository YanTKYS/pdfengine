"""Paint standing beside the text column is not the content of a following line (issue #120).

A following line moves with a lengthened paragraph only on approval (chain). Paint on its band within its
column that is not the line's (a bullet, a balloon's tail) refuses it. A screenshot, photo, panel or rule standing
beside the column (clear across of the text, from beside the paragraph down past the line) is not on the line:
it stays, and the line moves on approval when it meets nothing.
"""
import json

import pymupdf
import pytest

from test_paragraph_frame_chain import GROW, chain_of, chained
from prototypes.paragraph_frame.approved_audit import audit
from prototypes.paragraph_frame.auto import approve, propose, save
from prototypes.paragraph_frame.decoration import side_of_column
from prototypes.paragraph_frame.engine import Refused


def panel(x0, y0, x1, y1):
    """A grey rectangle at page (x0, y0, x1, y1) on the 300 pt page, drawn first."""
    return b'q .8 g %g %g %g %g re f Q\n' % (x0, 300 - y1, x1 - x0, y1 - y0)


def edit(source, out, ids=None):
    found = chain_of(source) or {}
    ids = [c['id'] for c in found.get('candidates', [])] if ids is None else ids
    accepted = approve(str(source), propose(str(source), 1, *GROW, chain=ids), acknowledge_inference=True)
    record = save(str(source), accepted, str(out))
    out.with_suffix('.proposal.json').write_text(json.dumps(accepted['proposal'], ensure_ascii=False))
    out.with_suffix('.save.json').write_text(json.dumps(record, ensure_ascii=False, default=str))
    return accepted['proposal'], record


def test_a_panel_beside_the_column_stays_and_the_line_moves_on_approval(tmp_path):
    # Beside the paragraph (its ink to x 216) and the HP line below it, from y 45 to 140: not on the line.
    source = chained(tmp_path, before=panel(220, 45, 250, 140))
    found = chain_of(source)
    assert found['candidates'] and not found.get('unavailable')
    with pytest.raises(Refused):
        edit(source, tmp_path / 'no.pdf', [])
    assert not (tmp_path / 'no.pdf').exists()
    out = tmp_path / 'out.pdf'
    proposal, record = edit(source, out)
    a = audit(source, out, 1, proposal, record)
    assert a['verdict'] == 'pass' and a['protected']['status'] == 'pass'
    # The panel is where it was.
    with pymupdf.open(source) as s, pymupdf.open(out) as o:
        keep = lambda d: [x['rect'] for x in d[0].get_drawings() if x.get('fill') == (.8, .8, .8)]
        assert keep(s) == keep(o)


@pytest.mark.parametrize('box, why', [
    ((220, 104, 226, 112), 'a mark beside the line only (it starts below the paragraph)'),
    ((220, 100, 250, 140), 'a panel starting below the paragraph'),
    ((216.2, 45, 250, 140), 'touching the column (not clear across)'),
    ((30, 104, 38, 112), 'a mark left of the line (a bullet)'),
])
def test_paint_on_the_line_still_refuses_it(tmp_path, box, why):
    source = chained(tmp_path, before=panel(*box))
    found = chain_of(source)
    assert not found.get('candidates') and 'outside its line decoration' in (found.get('unavailable') or ''), (why, found)


def test_side_of_column():
    beside = pymupdf.Rect(40, 51, 216, 94)
    texts = [pymupdf.Rect(52, 103, 120, 114), pymupdf.Rect(52, 119, 150, 130)]
    assert side_of_column(pymupdf.Rect(220, 45, 250, 140), beside, texts)
    assert side_of_column(pymupdf.Rect(10, 60, 30, 120), beside, texts)            # on the left, beside the paragraph
    assert not side_of_column(pymupdf.Rect(220, 45, 250, 110), beside, texts)       # ends above the line's first row
    assert not side_of_column(pymupdf.Rect(220, 95, 250, 140), beside, texts)       # starts below the paragraph
    assert not side_of_column(pymupdf.Rect(140, 45, 250, 140), beside, texts)       # across the text
    assert not side_of_column(pymupdf.Rect(220, 45, 250, 140), None, texts)         # outside the chain: unchanged
