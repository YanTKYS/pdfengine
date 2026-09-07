"""Evaluation must consume independent evidence and fail when it is missing."""
from copy import deepcopy
import json
from types import SimpleNamespace

from PIL import Image
import pymupdf
import pytest

from evaluations.backend import followup
from pdfeditor.model import Rect
from pdfeditor.selection import make_selection


def test_removal_checkpoint_preserves_identical_header_on_other_pages():
    before = {'pages':['DATE body', 'DATE another page']}
    removed = {'pages':[' body', 'DATE another page']}
    assert followup.independent_edit_audit(before, removed, 1, 'DATE', '')['passed']
    with pytest.raises(followup.EvaluationFailure):
        followup.independent_edit_audit(before, {'pages':[' body', ' another page']}, 1, 'DATE', '')


@pytest.fixture
def source_pdf(tmp_path):
    source = tmp_path / 'source.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=100, height=100)
        page.insert_text((10, 30), 'ABCDE', fontsize=10)
        document.save(source)
    return source


def good_render_audit():
    return {
        'before': {'rendered': True}, 'after': {'rendered': True},
        'poppler_diff_with_1pt_margin': {'size_changed': False, 'outside_gt8_pixels': 0},
        'page_count_equal': True, 'outside_page_text_equal': True,
        'outside_page_mupdf_pixels_equal': True,
    }


def test_poppler_difference_reads_poppler_png_even_when_mupdf_pages_are_identical(source_pdf, tmp_path, monkeypatch):
    output = tmp_path / 'same.pdf'
    output.write_bytes(source_pdf.read_bytes())

    def fake_poppler(executable, pdf, page, path, dpi):
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new('RGB', (200, 200), 'white')
        if pdf == output:
            image.putpixel((150, 150), (0, 0, 0))
        image.save(path)
        return {'rendered': True, 'returncode': 0}

    monkeypatch.setattr(followup, 'poppler_render', fake_poppler)
    report = followup.render_audit(source_pdf, output, 1, tmp_path / 'audit', (1, 1, 5, 5))
    assert report['mupdf_page_pixels'] is True
    assert report['poppler_diff']['all_changed_pixels'] == 1
    assert report['poppler_diff_with_1pt_margin']['outside_gt8_pixels'] == 1
    with pytest.raises(followup.EvaluationFailure, match='Poppler difference'):
        followup.require_render_audit(report)


@pytest.mark.parametrize('failure', ['renderer', 'missing_difference', 'size', 'page_count', 'outside_text', 'outside_pixels', 'extractor', 'incorrect_text'])
def test_audit_failure_never_reports_success_or_safe_backend_refusal(source_pdf, tmp_path, monkeypatch, failure):
    manifest = make_selection(source_pdf, glyph_ids=list(range(5)))
    case = {'id': 'audit_fixture', 'source': str(source_pdf), 'selection': manifest}
    stage1 = tmp_path / 'stage1'
    (stage1 / case['id']).mkdir(parents=True)
    (stage1 / case['id'] / 'gate.json').write_text(json.dumps({}), encoding='utf8')
    monkeypatch.setattr(followup, 'STAGE1', stage1)
    edits = {}

    def fake_edit(source, output, selection, replacement, *, stage, stage1_report):
        output.write_bytes(source.read_bytes() + b'\n% distinct saved output\n')
        edits[output] = replacement
        return {'stage': stage, 'selection': selection}

    render = good_render_audit()
    if failure == 'renderer':
        render['after']['rendered'] = False
    elif failure == 'missing_difference':
        render.pop('poppler_diff_with_1pt_margin')
    elif failure == 'size':
        render['poppler_diff_with_1pt_margin'] = {'size_changed': True}
    elif failure == 'page_count':
        render['page_count_equal'] = False
    elif failure == 'outside_text':
        render['outside_page_text_equal'] = False
    elif failure == 'outside_pixels':
        render['outside_page_mupdf_pixels_equal'] = False

    def fake_extract(python, path, output):
        if failure == 'extractor':
            return {'error': 'independent extractor unavailable'}
        if path == source_pdf:
            return {'pages': ['ABCDE']}
        return {'pages': ['ABCDE' if failure == 'incorrect_text' else edits[path]]}

    monkeypatch.setattr(followup, 'edit_slots', fake_edit)
    monkeypatch.setattr(followup, 'render_audit', lambda *args: deepcopy(render))
    monkeypatch.setattr(followup, 'independent_text', fake_extract)
    result = followup.run_one(case, {'status': 'passed'}, tmp_path / 'run')
    for stage in ('2', '3'):
        item = result['stages'][stage]
        assert item['status'] == 'failed_evaluation'
        assert item['classification'] == 'evaluation_failure'
        assert item['output_created'] is True


def test_changed_graphics_on_an_unedited_page_fail_even_when_text_is_identical(source_pdf, tmp_path, monkeypatch):
    two_pages = tmp_path / 'two-pages.pdf'
    with pymupdf.open(source_pdf) as document:
        page = document.new_page(width=100, height=100)
        page.insert_text((10, 30), 'Unchanged text', fontsize=10)
        document.save(two_pages)
    output = tmp_path / 'changed-background.pdf'
    with pymupdf.open(two_pages) as document:
        document[1].draw_rect(pymupdf.Rect(10, 50, 30, 70), fill=(1, 0, 0))
        document.save(output)

    def fake_poppler(executable, pdf, page, path, dpi):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (200, 200), 'white').save(path)
        return {'rendered': True, 'returncode': 0}

    monkeypatch.setattr(followup, 'poppler_render', fake_poppler)
    audit = followup.render_audit(two_pages, output, 1, tmp_path / 'audit', (10, 10, 30, 30))
    assert audit['page_count_equal'] is True
    assert audit['outside_page_text_equal'] is True
    assert audit['outside_page_mupdf_pixels_equal'] is False
    with pytest.raises(followup.EvaluationFailure, match='pixels on unedited pages'):
        followup.require_render_audit(audit)

    # A shorter output must produce False instead of indexing beyond its pages.
    audit = followup.render_audit(two_pages, source_pdf, 1, tmp_path / 'shorter-audit', (10, 10, 30, 30))
    assert audit['page_count_equal'] is False
    assert audit['outside_page_mupdf_pixels_equal'] is False
    audit = followup.render_audit(two_pages, source_pdf, 2, tmp_path / 'missing-target-audit', (10, 10, 30, 30))
    assert audit['mupdf_page_pixels'] is False
    assert audit['outside_page_mupdf_pixels_equal'] is False


@pytest.mark.parametrize(('source', 'old', 'new', 'output'), [
    ('prefix A B suffix', 'A B', 'A B', 'prefix A B suffix'),
    ('prefix OLD suffix', 'OLD', 'OLDER', 'prefix OLDER suffix'),
    ('OLD and OLD', 'OLD', 'NEW', 'OLD and NEW'),
    ('prefix A\nB suffix', 'A B', 'XY', 'prefix X\nY suffix'),
])
def test_independent_edit_allows_noop_containment_duplicate_and_linebreaks(source, old, new, output):
    report = followup.independent_edit_audit({'pages': [source, 'other page']},
                                            {'pages': [output, 'other page']}, 1, old, new)
    assert report['passed']


@pytest.mark.parametrize(('before', 'after', 'old', 'new'), [
    ({'pages': ['prefix absent']}, {'pages': ['prefix NEW']}, 'OLD', 'NEW'),
    ({'pages': ['prefix OLD']}, {'pages': ['prefix OLD']}, 'OLD', 'NEW'),
    ({'pages': ['OLD', 'keep']}, {'pages': ['NEW', 'changed']}, 'OLD', 'NEW'),
    ({'pages': ['OLD']}, {'pages': ['NEW', 'extra']}, 'OLD', 'NEW'),
    ({'pages': ['OLD']}, {'error': 'failed'}, 'OLD', 'NEW'),
])
def test_independent_edit_requires_source_control_expected_output_and_other_pages(before, after, old, new):
    with pytest.raises(followup.EvaluationFailure):
        followup.independent_edit_audit(before, after, 1, old, new)


def test_stage4_mask_uses_verified_new_text_bounds_not_whole_available_width():
    resolved = SimpleNamespace(bbox=Rect(10, 10, 20, 20))
    report = {'stage': 4, 'new_bbox': {'x0': 10, 'y0': 10, 'x1': 35, 'y1': 40},
              'widths': {'explicitly_supplied_width': 400}}
    assert followup.edit_audit_bounds(resolved, report, 4) == (10, 10, 35, 40)
    assert followup.edit_audit_bounds(resolved, report, 3) == (10, 10, 20, 20)
    with pytest.raises(followup.EvaluationFailure, match='bounds'):
        followup.edit_audit_bounds(resolved, {'stage': 4}, 4)
    report['new_bbox']['x1'] = float('nan')
    with pytest.raises(followup.EvaluationFailure, match='bounds'):
        followup.edit_audit_bounds(resolved, report, 4)
