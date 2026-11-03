"""A text box's clip extended down with its paragraph on explicit approval, with its panel (issue #118)."""
import json
import re

import pymupdf
import pytest

from test_paragraph_panel import FILL, GROW, OUTLINE, glyphs, panel_pdf
from prototypes.paragraph_frame.approved_audit import audit
from prototypes.paragraph_frame.auto import approve, propose, save
from prototypes.paragraph_frame.engine import Refused

# The paragraph (baselines 60, 76, 92) on a panel from 46 to 97 (page y), its text clipped to the box's inner
# rectangle from 48 to 95 (PDF y 205, height 47), as Word draws a text box.
CLIP = b'36 205 192 47 re W* n\n'
BOX = FILL + OUTLINE + b'q ' + CLIP
END = b'Q\n'


def box_pdf(tmp_path, before=BOX, after=END, name='box.pdf'):
    return panel_pdf(tmp_path, before, after, name=name)


def evidence(source, old=GROW[0], new=GROW[1]):
    return propose(str(source), 1, old, new)['evidence']


def ids(source, old=GROW[0], new=GROW[1]):
    ev = evidence(source, old, new)
    return [c['id'] for c in (ev.get('clips') or {}).get('candidates', [])], [c['id'] for c in (ev.get('panels') or {}).get('candidates', [])]


def edit(source, out, clips=None, panels=None, old=GROW[0], new=GROW[1], write=True):
    """Save approving ``clips`` and ``panels`` (default: every listed one); (proposal, save record), records written beside ``out``."""
    listed = ids(source, old, new)
    clips = listed[0] if clips is None else clips
    panels = listed[1] if panels is None else panels
    accepted = approve(str(source), propose(str(source), 1, old, new, clips=clips, panels=panels), acknowledge_inference=True)
    record = save(str(source), accepted, str(out))
    if write:
        out.with_suffix('.proposal.json').write_text(json.dumps(accepted['proposal'], ensure_ascii=False))
        out.with_suffix('.save.json').write_text(json.dumps(record, ensure_ascii=False, default=str))
    return accepted['proposal'], record


def clip_rects(path):
    with pymupdf.open(path) as d:
        data = b''.join(d.xref_stream(x) for x in d[0].get_contents()).decode('latin1')
    return re.findall(r'([\d.]+ [\d.]+ [\d.]+ [\d.]+) re\s+(W\*?) n', data)


def test_the_clip_and_its_panel_extend_together_on_both_approvals(tmp_path):
    source = box_pdf(tmp_path)
    ev = evidence(source)
    [clip], [panel] = ev['clips']['candidates'], ev['panels']['candidates']
    # Down by the added line (16 pt): the clip from 95 to 111, the panel from 97 to 113; each names the other.
    assert clip['old'] == [36, 48, 228, 95] and clip['new'] == [36, 48, 228, 111] and clip['extension'] == 16 and clip['rule'] == ['W*']
    assert panel['new'] == [34, 46, 230, 113] and clip['requires'] == [panel['id']] and panel['requires'] == [clip['id']]
    assert clip['holds'] == dict(text_shows=3, paragraph_shows=3, drawings=0) and clip['band'] == [36, 95, 228, 111]
    # Without both approvals: refused, nothing written.
    for clips, panels, reason in (([], [], 'capacity'), ([clip['id']], [], 'panel approved too'), ([], [panel['id']], 'clip extension approved too')):
        with pytest.raises(Refused, match=reason):
            edit(source, tmp_path / 'no.pdf', clips, panels)
        assert not (tmp_path / 'no.pdf').exists()
    out = tmp_path / 'out.pdf'
    proposal, record = edit(source, out)
    assert [x['new'] for x in record['clips_extended']] == [[36, 48, 228, 111]] and [x['new'] for x in record['panels_extended']] == [[34, 46, 230, 113]]
    # Only the operands moved; the rule is kept; the added line shows inside the clip.
    assert clip_rects(out) == [('36 189 192 63', 'W*')]
    with pymupdf.open(out) as d:
        assert d[0].get_text(clip=pymupdf.Rect(36, 95, 228, 111)).strip() == proposal['lines'][-1]['text'].strip()
    a = audit(source, out, 1, proposal, record)
    assert a['verdict'] == 'pass' and a['mode'] == 'layout' and [x['what'] for x in a['approved'] if x['status'] == 'pass'] == ['panel', 'clip', 'text']


def test_every_instance_of_the_clip_is_extended(tmp_path):
    # Word sets the box's clip once for the box and again around each text show: every instance moves.
    source = box_pdf(tmp_path, BOX + b'q ' + CLIP, END + END)
    [clip] = evidence(source)['clips']['candidates']
    assert len(clip['operators']) == 2
    edit(source, tmp_path / 'out.pdf')
    assert clip_rects(tmp_path / 'out.pdf') == [('36 189 192 63', 'W*')] * 2


def test_a_clip_inside_its_panel_alone_needs_no_panel_approval(tmp_path):
    # No panel holds the box: the clip extension alone is listed and enough.
    source = box_pdf(tmp_path, b'q ' + CLIP)
    ev = evidence(source)
    [clip] = ev['clips']['candidates']
    assert 'requires' not in clip and not (ev.get('panels') or {}).get('candidates')
    edit(source, tmp_path / 'out.pdf')
    assert clip_rects(tmp_path / 'out.pdf') == [('36 189 192 63', 'W*')]


@pytest.mark.parametrize('before, after, reason', [
    # A drawing under the clip (a box below its bottom, hidden): the extension would show it.
    (BOX + b'0 0 1 rg 40 190 20 10 re f\n', END, 'holds a drawing'),
    # Another clip that would cut the extended one (its bottom at page 100).
    (FILL + OUTLINE + b'q 0 200 300 100 re W n q ' + CLIP, END + END, 'cut by another clip'),
    # Something below the box where the clip and the panel would go.
    (BOX, END + b'0 G 1 w 20 190 m 260 190 l S\n', 'would meet'),
])
def test_what_cannot_be_extended_is_refused_with_its_reason(tmp_path, before, after, reason):
    source = box_pdf(tmp_path, before, after)
    found = evidence(source).get('clips') or {}
    assert not found.get('candidates') and reason in (found.get('unavailable') or '')


@pytest.mark.parametrize('clip', [
    b'q 36 205 192 47 re 40 150 10 10 re W* n\n',                              # two subpaths
    b'q 1 0 .1 1 0 0 cm 36 205 192 47 re W* n 1 0 -.1 1 0 0 cm\n',             # skewed
], ids=['two subpaths', 'skewed'])
def test_a_clip_that_is_not_one_rectangle_is_never_extended(tmp_path, clip):
    # No clip candidate; whatever else is listed, the save is refused (the new text cannot be proven to show).
    source = box_pdf(tmp_path, FILL + OUTLINE + clip)
    assert not (evidence(source).get('clips') or {}).get('candidates')
    with pytest.raises(Refused):
        edit(source, tmp_path / 'out.pdf', [], None)
    assert not (tmp_path / 'out.pdf').exists()


def test_text_hidden_under_the_clip_refuses_it(tmp_path):
    # Text drawn under the clip below its bottom (not shown now): extended, the clip would show it.
    hidden = b'BT /Fixture 11 Tf 150 192 Td <0030> Tj ET\n'
    source = box_pdf(tmp_path, BOX, hidden + END)
    found = evidence(source).get('clips') or {}
    assert not found.get('candidates') and 'reaches past its bottom' in (found.get('unavailable') or '')


def test_an_id_of_another_edit_is_no_approval(tmp_path):
    source = box_pdf(tmp_path)
    other = ids(source, GROW[0], GROW[1] + 'です')
    with pytest.raises(Refused, match='not a candidate'):
        edit(source, tmp_path / 'out.pdf', other[0], ids(source)[1])


def test_a_pdf_changed_after_the_proposal_is_refused(tmp_path):
    source = box_pdf(tmp_path)
    clips, panels = ids(source)
    proposal = propose(str(source), 1, *GROW, clips=clips, panels=panels)
    # The same file changed (a mark drawn far away): the approval named the source as proposed.
    with pymupdf.open(source) as d:
        d[0].draw_rect(pymupdf.Rect(250, 250, 260, 260), color=(1, 0, 0))
        d.save(tmp_path / 'changed.pdf')
    (tmp_path / 'changed.pdf').replace(source)
    with pytest.raises(Exception):
        save(str(source), approve(str(source), proposal, acknowledge_inference=True), str(tmp_path / 'out.pdf'))
    assert not (tmp_path / 'out.pdf').exists()


def _tamper(path, out, old, new):
    with pymupdf.open(path) as d:
        xref = d[0].get_contents()[0]
        data = d.xref_stream(xref)
        assert old in data
        d.update_stream(xref, data.replace(old, new))
        d.save(out)
    return out


@pytest.mark.parametrize('change, reason', [
    ((b'36 189 192 63 re', b'36 185 192 67 re'), 'set 0 times where it was approved'),     # extended further than approved
    ((b'36 189 192 63 re', b'36 189 150 63 re'), 'clips differ from the source'),          # narrowed
    ((b'Q\n', b'0 0 1 rg 40 192 20 6 re f\nQ\n'), 'shows more than the new text'),        # a drawing shown in the added band
])
def test_a_clip_changed_beyond_the_approval_fails_the_audit(tmp_path, change, reason):
    source = box_pdf(tmp_path)
    out = tmp_path / 'out.pdf'
    proposal, record = edit(source, out)
    bad = _tamper(out, tmp_path / 'bad.pdf', *change)
    a = audit(source, bad, 1, proposal, record)
    clip = next(x for x in a['approved'] if x['what'] == 'clip')
    assert a['verdict'] == 'fail' and clip['status'] == 'fail' and any(reason in r for r in clip['reasons']), clip


def test_the_saved_pdf_alone_finds_its_clip_and_needs_a_new_approval_to_grow_again(tmp_path):
    source = box_pdf(tmp_path)
    first = tmp_path / 'first.pdf'
    edit(source, first)
    longer = (GROW[1], GROW[1] + '受け付けます。期限までに必ず提出してください。')
    ev = propose(str(first), 1, *longer)['evidence']
    assert ev['conditions']['status'] == 'applied' and [x['status'] for x in ev['conditions']['clips']] == ['drawn as approved']
    [clip] = ev['clips']['candidates']
    assert clip['old'][3] == 111 and clip['new'][3] > 111
    # The first edit's approval does not carry: refused without the new ids.
    with pytest.raises(Refused):
        edit(first, tmp_path / 'no.pdf', [], [], *longer)
    edit(first, tmp_path / 'second.pdf', None, None, *longer)
    assert float(clip_rects(tmp_path / 'second.pdf')[0][0].split()[3]) > 63


def test_a_clip_changed_after_the_save_is_not_taken_as_approved_on_reopen(tmp_path):
    source = box_pdf(tmp_path)
    out = tmp_path / 'out.pdf'
    edit(source, out)
    bad = _tamper(out, tmp_path / 'bad.pdf', b'36 189 192 63 re W*', b'36 189 192 63 re W')
    status = propose(str(bad), 1, GROW[1], GROW[0])['evidence']['conditions']['status']
    assert status.startswith('stale: an approved clip extension is not drawn as approved') and 'rule' in status


def test_the_edit_back_takes_the_clip_and_panel_back_only_on_approval(tmp_path):
    source = box_pdf(tmp_path)
    out = tmp_path / 'out.pdf'
    edit(source, out)
    back = (GROW[1], GROW[0])
    ev = propose(str(out), 1, *back)['evidence']
    [clip], [panel] = ev['clips']['candidates'], ev['panels']['candidates']
    assert clip['new'] == [36, 48, 228, 95] and clip['extension'] == -16 and panel['new'][3] == 97
    # Not approved: the clip and the panel stay as they are.
    edit(out, tmp_path / 'kept.pdf', [], [], *back)
    assert clip_rects(tmp_path / 'kept.pdf') == [('36 189 192 63', 'W*')]
    edit(out, tmp_path / 'back.pdf', None, None, *back)
    [(rect, rule)] = clip_rects(tmp_path / 'back.pdf')
    assert [round(float(v), 4) for v in rect.split()] == [36, 205, 192, 47] and rule == 'W*'
    assert glyphs(tmp_path / 'back.pdf') == glyphs(source)
    with pymupdf.open(source) as a, pymupdf.open(tmp_path / 'back.pdf') as b:
        assert a[0].get_pixmap(dpi=144).samples == b[0].get_pixmap(dpi=144).samples


def test_a_panel_that_cannot_grow_refuses_the_clip(tmp_path):
    # The box's panel filled twice (not one fill and its outline): the clip would pass a panel that cannot follow.
    source = box_pdf(tmp_path, FILL + FILL + OUTLINE + b'q ' + CLIP)
    found = evidence(source).get('clips') or {}
    assert not found.get('candidates') and 'panel cannot grow' in found['unavailable'] and 'filled 2 times' in found['unavailable']


def test_a_clip_goes_back_no_higher_than_it_was(tmp_path):
    # Edited back to a text shorter than the original: the clip is offered back to where it was, not higher.
    source = box_pdf(tmp_path)
    out = tmp_path / 'out.pdf'
    edit(source, out)
    [clip] = propose(str(out), 1, GROW[1], '申請書')['evidence']['clips']['candidates']
    assert clip['new'][3] == 95 and clip['extension'] == -16


def _tamper_nth(path, out, old, new, n):
    """``path`` with the ``n``-th (0-based) occurrence of ``old`` in its content replaced by ``new``."""
    with pymupdf.open(path) as d:
        xref = d[0].get_contents()[0]
        data = d.xref_stream(xref)
        at = -1
        for _ in range(n + 1):
            at = data.index(old, at + 1)
        d.update_stream(xref, data[:at] + new + data[at + len(old):])
        d.save(out)
    return out


@pytest.fixture
def twice(tmp_path):
    """The box's clip set twice (for the box, then around the text, as Word does), extended on approval."""
    source = box_pdf(tmp_path, BOX + b'q ' + CLIP, END + END)
    out = tmp_path / 'out.pdf'
    proposal, record = edit(source, out)
    return source, out, proposal, record


def test_every_instance_kept_passes_and_is_recorded_with_its_count(twice):
    source, out, proposal, record = twice
    a = audit(source, out, 1, proposal, record)
    assert next(x for x in a['approved'] if x['what'] == 'clip')['status'] == 'pass'
    ev = propose(str(out), 1, GROW[1], GROW[0])['evidence']['conditions']
    [stored] = ev['clips']
    assert ev['status'] == 'applied' and stored['status'] == 'drawn as approved' and stored['count'] == 2
    assert [x['rule'] for x in stored['instances']] == ['W*', 'W*']


@pytest.mark.parametrize('new, audit_reason, stale_reason', [
    (b'', 'set 1 times where it was approved, 2 times', 'set 1 times, approved 2 times'),             # one instance removed
    (b'36 185 192 67 re W* n', 'set 1 times where it was approved, 2 times', 'set 1 times, approved 2 times'),   # resized
    (b'36 189 192 63 re W n', 'clips differ from the source', 'rule changed (instance 2)'),            # its rule changed
], ids=['removed', 'resized', 'rule'])
def test_an_instance_removed_resized_or_set_by_another_rule_fails_and_is_stale(twice, tmp_path, new, audit_reason, stale_reason):
    # (PR #119 review.) Only the inner instance (around the text) changes; the outer one still holds the text extended.
    source, out, proposal, record = twice
    bad = _tamper_nth(out, tmp_path / 'bad.pdf', b'36 189 192 63 re W* n', new, 1)
    a = audit(source, bad, 1, proposal, record)
    clip = next(x for x in a['approved'] if x['what'] == 'clip')
    assert a['verdict'] == 'fail' and clip['status'] == 'fail' and any(audit_reason in r for r in clip['reasons']), clip['reasons']
    status = propose(str(bad), 1, GROW[1], GROW[0])['evidence']['conditions']['status']
    assert status.startswith('stale: an approved clip extension is not drawn as approved') and stale_reason in status, status


def test_an_instance_added_fails_and_is_stale(twice, tmp_path):
    # A third instance of the extended rectangle around the text: not what was approved.
    source, out, proposal, record = twice
    bad = _tamper_nth(out, tmp_path / 'bad.pdf', b'36 189 192 63 re W* n', b'36 189 192 63 re W* n 36 189 192 63 re W* n', 1)
    a = audit(source, bad, 1, proposal, record)
    assert next(x for x in a['approved'] if x['what'] == 'clip')['status'] == 'fail'
    status = propose(str(bad), 1, GROW[1], GROW[0])['evidence']['conditions']['status']
    assert 'set 3 times, approved 2 times' in status, status
