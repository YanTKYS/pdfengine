"""The approved-change audit: approved moves pass, what was not approved fails (issue #108)."""
import copy

import pymupdf
import pytest

from test_paragraph_frame_chain import chained, chain_of, GROW
from test_paragraph_frame_separate import shared
from prototypes.paragraph_frame.approved_audit import audit, change_set, followers_area
from prototypes.paragraph_frame.auto import approve, propose, save


def saved(source, out, chain=None, old=GROW[0], new=GROW[1]):
    """(approved proposal, save record) of an edit approving ``chain`` (default: every listed candidate)."""
    found = chain_of(source, old, new) or {}
    ids = [c['id'] for c in found.get('candidates', [])] if chain is None else chain
    proposal = propose(str(source), 1, old, new, chain=ids)
    accepted = approve(str(source), proposal, acknowledge_inference=True)
    return accepted['proposal'], save(str(source), accepted, str(out))


def tamper(path, out, edit):
    """``path`` with ``edit(page, doc)`` applied, written to ``out``."""
    with pymupdf.open(path) as d:
        edit(d[0], d)
        d.save(out)
    return out


@pytest.fixture
def moved(tmp_path):
    """A chain move (HP line with its icon, search box and link; enquiry line) on approval."""
    source = chained(tmp_path)
    out = tmp_path / 'out.pdf'
    proposal, record = saved(source, out)
    return source, out, proposal, record


@pytest.fixture
def separated(tmp_path):
    """A line separated from the heading drawn in its text object, moved on approval."""
    source = shared(tmp_path)
    out = tmp_path / 'sep.pdf'
    proposal, record = saved(source, out)
    return source, out, proposal, record


def test_approved_moves_pass(moved, separated):
    for source, out, proposal, record in (moved, separated):
        r = audit(source, out, 1, proposal, record)
        assert r['verdict'] == 'pass' and r['mode'] == 'layout' and r['quality']['status'] == 'pass', r
        assert all(x['status'] == 'pass' for x in r['approved'])
        assert r['protected']['annotations'] == (1 if source == moved[0] else 0)


def test_a_faithful_edit_passes_without_moves(tmp_path):
    source = shared(tmp_path)
    out = tmp_path / 'same.pdf'
    proposal, record = saved(source, out, chain=[], old='申請書は窓口で', new='申込書は窓口で')
    r = audit(source, out, 1, proposal, record)
    assert r['verdict'] == 'pass' and r['mode'] == 'faithful'
    assert [c['kind'] for c in change_set(proposal, record, followers_area(source, proposal))] == ['text']


def test_an_unapproved_glyph_change_fails(separated, tmp_path):
    source, out, proposal, record = separated
    # The heading (drawn with the separated line, not approved to change): one glyph code replaced.
    def edit(page, doc):
        for xref in page.get_contents():
            data = doc.xref_stream(xref)
            if b'40 276 Tm' in data:
                first = data.index(b'[<', data.index(b'40 276 Tm')) + 2
                doc.update_stream(xref, data[:first] + data[first + 4:first + 8] + data[first + 4:])
    bad = tamper(out, tmp_path / 'bad.pdf', edit)
    r = audit(source, bad, 1, proposal, record)
    assert r['verdict'] == 'fail' and r['protected']['status'] == 'fail'
    assert any('glyphs outside the approved areas changed' in x for x in r['protected']['reasons'])


def test_an_unrelated_drawing_fails(moved, tmp_path):
    source, out, proposal, record = moved
    bad = tamper(out, tmp_path / 'bad.pdf', lambda page, doc: page.draw_rect(pymupdf.Rect(250, 270, 260, 280), color=(1, 0, 0), fill=(1, 0, 0)))
    r = audit(source, bad, 1, proposal, record)
    assert r['verdict'] == 'fail' and any('drawings' in x for x in r['protected']['reasons']) and any('pixels' in x for x in r['protected']['reasons'])


def test_a_changed_link_target_fails(moved, tmp_path):
    source, out, proposal, record = moved
    def edit(page, doc):
        link = page.first_link
        xref = link.xref
        doc.xref_set_key(xref, 'A/URI', pymupdf.get_pdf_str('https://example.org/elsewhere'))
    bad = tamper(out, tmp_path / 'bad.pdf', edit)
    r = audit(source, bad, 1, proposal, record)
    assert r['verdict'] == 'fail' and any('annotations differ' in x for x in r['protected']['reasons'])


def test_a_move_by_another_amount_fails(moved, tmp_path):
    source, out, proposal, record = moved
    # The moved element's translation in the content changed by one point.
    dy = next(c['dy'] for c in change_set(proposal, record) if c['what'] == 'chain')
    def edit(page, doc):
        for xref in page.get_contents():
            data = doc.xref_stream(xref)
            old = f' {-dy:g} cm'.encode()
            if old in data:
                doc.update_stream(xref, data.replace(old, f' {-dy + 1:g} cm'.encode()))
    bad = tamper(out, tmp_path / 'bad.pdf', edit)
    r = audit(source, bad, 1, proposal, record)
    chain = [x for x in r['approved'] if x['what'] == 'chain']
    assert r['verdict'] == 'fail' and chain and all(x['status'] == 'fail' for x in chain)


@pytest.mark.parametrize('change, reason', [
    # An id of another edit (or reused from an earlier one) is no approval of this one.
    (lambda p, s: p.update(chain=['following-element-0123456789abcdef']), "approved ids that are not this proposal's candidates"),
    (lambda p, s: p.update(chain=[]), 'moved without approval'),
    (lambda p, s: s.update(chain_moved=[]), 'approved but not moved'),
    (lambda p, s: s.update(proposal_sha256='0' * 64), 'the save record is not of this proposal'),
])
def test_an_approval_that_does_not_match_fails(moved, change, reason):
    source, out, proposal, record = moved
    p, s = copy.deepcopy(proposal), copy.deepcopy(record)
    change(p, s)
    r = audit(source, out, 1, p, s)
    assert r['verdict'] == 'fail' and r['approval']['status'] == 'fail' and any(reason in x for x in r['approval']['reasons'])


def test_an_unchecked_change_kind_is_never_a_pass(tmp_path):
    # A paragraph with a following paragraph; its translation recorded as column flow's (per line): not checked here.
    from test_paragraph_frame_tagged import tagged
    from test_paragraph_frame_placed_follower import FOLLOWING
    source, _ = tagged(tmp_path, [], lines=FOLLOWING, tree=False)
    out = tmp_path / 'out.pdf'
    proposal, record = saved(source, out, chain=[])
    assert audit(source, out, 1, proposal, record)['verdict'] == 'pass'
    assert [c['kind'] for c in change_set(proposal, record, followers_area(source, proposal))] == ['text', 'translate']
    s = copy.deepcopy(record)
    s['follower_translation'] = 'per line (column flow)'
    r = audit(source, out, 1, proposal, s)
    assert r['verdict'] in ('unverified', 'fail') and r['unchecked'] == ['per-line translation']


def test_a_new_size_or_colour_in_the_edited_paragraph_fails_quality(separated, tmp_path):
    source, out, proposal, record = separated
    frame = pymupdf.Rect(proposal['evidence']['inferred']['frame'])
    bad = tamper(out, tmp_path / 'bad.pdf', lambda page, doc: page.insert_text(frame.tl + (2, 14), 'X', fontsize=16, color=(1, 0, 0)))
    r = audit(source, bad, 1, proposal, record)
    assert r['quality']['size_and_colour_kept'] is False and r['quality']['status'] == 'fail'
