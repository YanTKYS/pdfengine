"""Regressions for independent evidence checks, separate from PDF backend tests."""
import pytest

from evaluations.backend.stage1 import independent_text_audit


def test_removal_search_normalizes_both_sides_and_requires_source_match():
    original = {"pages": ["Header\nAB CD\nFooter"]}
    removed = {"pages": ["Header\nFooter"]}
    audit = independent_text_audit(original, original, removed, "AB CD")
    assert audit["independent_text_equal"]
    assert audit["independent_removal_check_passed"]
    assert not independent_text_audit(original, original, removed, "unobserved")["independent_removal_check_passed"]


def test_whitespace_cannot_hide_remaining_selected_text():
    original = {"pages": ["AB CD"]}
    removed = {"pages": ["A B\nCD"]}
    audit = independent_text_audit(original, original, removed, "AB CD")
    assert audit["selected_unicode_present_before_removal"]
    assert not audit["removed_selected_unicode_absent"]
    assert not audit["independent_removal_check_passed"]


@pytest.mark.parametrize("removed", [{"error": "extractor failed"}, {}, {"pages": []}, {"pages": [None]}, {"pages": ["", ""]}])
def test_failed_or_incomplete_removal_extraction_cannot_pass(removed):
    original = {"pages": ["AB CD"]}
    assert not independent_text_audit(original, original, removed, "AB CD")["independent_removal_check_passed"]


def test_empty_selection_and_failed_replay_extraction_do_not_pass():
    original = {"pages": ["AB CD"]}
    assert not independent_text_audit(original, original, {"pages": [""]}, " \n")["independent_removal_check_passed"]
    assert not independent_text_audit(original, {"pages": original["pages"], "error": "partial extraction"}, {"pages": [""]}, "AB CD")["independent_text_equal"]
