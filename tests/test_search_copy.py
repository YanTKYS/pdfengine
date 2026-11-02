"""Search and copy of an edited paragraph's new text, measured apart, with negative cases (issue #114)."""
import re

import pymupdf
import pytest

from provider_font import cjk_provider_bytes
from prototypes.paragraph_frame.search_copy import measure

# A paragraph of three lines: the replacement 補助事業の申請書 runs from the end of line 1 into line 2.
LINE1, LINE2, LINE3 = '本年度は新しい補助事業の', '申請書を窓口で配布しま', 'す。'
EXPECTED = LINE1 + LINE2 + LINE3
NEW = '補助事業の申請書'
AT = EXPECTED.index(NEW)
FRAME = [30, 40, 280, 110]
BASELINES = [60, 76, 92]


def page(tmp_path, lines, name='out.pdf', extra=(), edit=None, x=40):
    """``lines`` drawn at ``x`` on the baselines (and ``extra`` (x, y, text) elsewhere); ``edit(doc, page)`` afterwards."""
    path = tmp_path / name
    d = pymupdf.open()
    p = d.new_page(width=300, height=300)
    p.insert_font(fontname='Fixture', fontbuffer=cjk_provider_bytes())
    for y, text in zip(BASELINES, lines):
        if text:
            p.insert_text((x, y), text, fontsize=11, fontname='Fixture')
    for x, y, text in extra:
        p.insert_text((x, y), text, fontsize=11, fontname='Fixture')
    if edit:
        edit(d, p)
    d.save(path)
    d.close()
    return path


def run(path, expected=EXPECTED, new=NEW, at=AT):
    return measure(path, 1, expected, new, at, FRAME, BASELINES)


def test_a_replacement_over_two_lines_is_found_per_line_and_copied_but_not_by_one_search(tmp_path):
    m = run(page(tmp_path, [LINE1, LINE2, LINE3]))
    # One search does not cross a line: false, never "found"; each line's part is found where it is drawn.
    assert m['search_full'] is False and m['search_full_hits'] == dict(total=0, on_target=0, elsewhere=0, lines=2)
    assert m['search_per_line'] is True and [p['text'] for p in m['search_per_line_parts']] == ['補助事業の', '申請書']
    assert m['extraction_mupdf'] is True and m['extraction_poppler'] is True and m['copy_equivalent'] is True
    assert m['clipboard_manual'] is None


def test_a_replacement_on_one_line_is_found_by_one_search(tmp_path):
    new = '申請書を窓口'
    m = run(page(tmp_path, [LINE1, LINE2, LINE3]), new=new, at=EXPECTED.index(new))
    assert m['search_full'] is True and m['search_per_line'] is None and m['copy_equivalent'] is True


@pytest.mark.parametrize('lines, broken', [
    # A character of the replacement missing.
    (['本年度は新しい補助事業の', '申請を窓口で配布しま', 'す。'], 'missing'),
    # Two characters in another order.
    (['本年度は新しい補助事業の', '請申書を窓口で配布しま', 'す。'], 'order'),
    # A similar-looking character (肋 for 助).
    (['本年度は新しい補肋事業の', '申請書を窓口で配布しま', 'す。'], 'similar'),
])
def test_a_wrong_character_fails_every_measure(tmp_path, lines, broken):
    m = run(page(tmp_path, lines))
    assert m['search_full'] is False and m['search_per_line'] is False
    assert m['extraction_mupdf'] is False and m['copy_equivalent'] is False and 'reason' in m


@pytest.mark.parametrize('lines', [
    # One character drawn after the paragraph's end.
    [LINE1, LINE2, LINE3 + '余'],
    # Two, after a drawn space.
    [LINE1, LINE2, LINE3 + ' 余計'],
])
def test_characters_drawn_after_the_expected_text_fail_every_measure(tmp_path, lines):
    m = run(page(tmp_path, lines))
    assert m['search_full'] is False and m['search_per_line'] is False
    assert m['extraction_mupdf'] is False and m['extraction_poppler'] is False and m['copy_equivalent'] is False
    assert 'drawn but not expected' in m['reason'] and '余' in m['reason']


def test_text_on_the_same_lines_outside_the_frame_is_not_the_paragraphs(tmp_path):
    # Other text on line 1 right of the frame (x1 280) and on line 3 left of it (x0 30): not the paragraph's.
    m = run(page(tmp_path, [LINE1, LINE2, LINE3], extra=[(285, BASELINES[0], '注'), (8, BASELINES[2], '※')]))
    assert 'reason' not in m and m['search_per_line'] is True
    assert m['extraction_mupdf'] is True and m['extraction_poppler'] is True and m['copy_equivalent'] is True


def test_the_replacement_found_elsewhere_only_is_no_success(tmp_path):
    # The paragraph draws its lines wrong (its second line lacks the new text); the same words stand elsewhere.
    lines = ['本年度は新しい補助事業の', '申請書を窓口で配布しま', 'す。']
    m = run(page(tmp_path, lines, extra=[(40, 200, '補助事業の申請書')]))
    assert m['search_full'] is False and m['search_full_hits']['elsewhere'] == 1 and m['search_full_hits']['on_target'] == 0


def test_the_first_line_right_and_the_next_wrong_fails_per_line(tmp_path):
    # The first line's part (補助事業の) is drawn right; the next line's (申請書) is not.
    m = run(page(tmp_path, ['本年度は新しい補助事業の', '申込書を窓口で配布しま', 'す。']))
    assert m['search_per_line'] is False and m['copy_equivalent'] is False and '申' in m['reason']


def test_parts_on_lines_that_are_not_consecutive_are_no_per_line_success(tmp_path):
    # The replacement's two parts on lines 1 and 3, an unrelated line between them.
    expected = '本年度は新しい補助事業の' + '申請書を窓口で配布します。'
    lines = ['本年度は新しい補助事業の', '別の行です。', '申請書を窓口で配布します。']
    m = measure(page(tmp_path, lines), 1, expected, NEW, expected.index(NEW), FRAME, BASELINES)
    assert m['search_per_line'] is False and m['extraction_mupdf'] is False and m['copy_equivalent'] is False


def _tounicode(edit):
    """Rewrite the embedded font's ToUnicode CMap with ``edit(cmap bytes) -> bytes`` (None removes it)."""
    def change(d, p):
        for f in p.get_fonts(full=True):
            font = d.xref_object(f[0])
            m = re.search(r'/ToUnicode (\d+) 0 R', font)
            if not m:
                continue
            xref = int(m.group(1))
            data = edit(d.xref_stream(xref))
            if data is None:
                d.xref_set_key(f[0], 'ToUnicode', 'null')
            else:
                d.update_stream(xref, data)
    return change


def test_glyphs_without_a_unicode_mapping_are_readable_but_not_searchable(tmp_path):
    m = run(page(tmp_path, [LINE1, LINE2, LINE3], edit=_tounicode(lambda data: None)))
    assert m['search_full'] is False and m['search_per_line'] is False and m['copy_equivalent'] is False


def test_a_glyph_mapped_to_another_character_is_copied_garbled(tmp_path):
    # 申 (U+7533) copied as 由 (U+7531).
    m = run(page(tmp_path, [LINE1, LINE2, LINE3], edit=_tounicode(lambda data: data.replace(b'<7533>', b'<7531>'))))
    assert m['copy_equivalent'] is False and m['extraction_mupdf'] is False and m['search_per_line'] is False


def test_a_latin_replacement_over_lines_joins_at_the_word_space(tmp_path):
    # The word space at the line end is drawn (the extractors may drop it): the line break reads as it.
    expected = 'Linking to an internal destination or an external destination'
    new = 'destination or an external'
    lines = ['Linking to an internal destination ', 'or an external destination']
    m = measure(page(tmp_path, lines + ['']), 1, expected, new, expected.index(new), FRAME, BASELINES)
    # MuPDF's search reads the line break as a space: one search finds it over both lines.
    assert m['search_full'] is True and m['search_full_hits']['on_target'] == 2 and m['search_per_line'] is True
    assert m['extraction_mupdf'] is True and m['extraction_poppler'] is True and m['copy_equivalent'] is True


def test_a_positional_gap_of_the_source_is_reported_as_the_sources(tmp_path):
    # The source draws a space the paragraph's text lacks between 補助事業 and の (as an extractor reads a
    # positional gap); the output keeps it where it was: the same in both.
    source = page(tmp_path, ['本年度は新しい補助事業\u3000の', LINE2, LINE3], name='source.pdf')
    m = measure(source, 1, EXPECTED, NEW, AT, FRAME, BASELINES, source,
                dict(text=EXPECTED, frame=FRAME, baselines=BASELINES))
    assert m['gaps'] == [dict(line=0, between='業の', from_source=True)]
    assert m['search_per_line'] is False and m['copy_equivalent'] is False
    assert m['search_per_line_parts'][0]['as_in_source'] is True
    # (MuPDF reads the drawn U+3000; Poppler drops it here.)
    detail = m['extraction_mupdf_detail']
    assert detail['ok'] is False and detail['whitespace_only'] is True and detail['as_in_source'] is True


@pytest.mark.parametrize('lines, x', [
    # The output's gap at another place (between 度 and は), not where the source has its own.
    (['本年度\u3000は新しい補助事業の', LINE2, LINE3], 40),
    # The same gap, but the line drawn elsewhere: its glyphs are not the source's.
    (['本年度は新しい補助事業\u3000の', LINE2, LINE3], 44),
])
def test_a_failure_of_the_same_kind_elsewhere_is_not_the_sources(tmp_path, lines, x):
    # The source fails extraction with a whitespace-only difference too, but not this one: unconfirmed, never true.
    source = page(tmp_path, ['本年度は新しい補助事業\u3000の', LINE2, LINE3], name='source.pdf')
    m = measure(page(tmp_path, lines, x=x), 1, EXPECTED, NEW, AT, FRAME, BASELINES, source,
                dict(text=EXPECTED, frame=FRAME, baselines=BASELINES))
    detail = m['extraction_mupdf_detail']
    assert detail['ok'] is False and detail['whitespace_only'] is True
    assert detail['as_in_source'] is None and detail['as_in_source_note'] == '由来未確認'
    assert m['extraction_poppler_detail'].get('as_in_source') is not True
    for g in m['gaps']:
        assert g['from_source'] is False
    for part in m['search_per_line_parts']:
        assert part.get('as_in_source') in (None, True) and (part['found'] or part['as_in_source'] is None)
