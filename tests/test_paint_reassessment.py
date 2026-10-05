"""Paint blocker reassessment (§26): read-only evidence on the L1–L3 semantic surface.

No paint runtime exists. A prototype underline formatter in this file derives
rectangles only from the exact semantic plan (`semantic_layout._derive` over the
stored payload, the current asset and the confirmed region) plus a fixed exact
recipe. It never reads rendered paths, MuPDF traces or earlier output. The
probe asks whether the old B1/B1-L feedback loops are gone on this surface:
same semantic state ⇒ same paint bytes, from the sidecar alone. It is not an
owned paint writer, a grammar extension or a verifier.
"""
from fractions import Fraction as F
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from pdfeditor import semantic_island as island
from pdfeditor import semantic_layout as semantic
from pdfeditor import semantic_writer as writer
from pdfeditor import source_ownership as owned
from test_semantic_authority import alternate_font
from test_semantic_layout import make_flow, statement

ROOT = Path(__file__).resolve().parents[1]
RECIPE = dict(offset='13/10', thickness='7/10')  # exact creation recipe (page y-down, below the baseline)
PATH_OPERATORS = {'m', 'l', 'h', 'f', 'f*', 'F', 're', 'n', 'c', 'v', 'y', 'S', 's', 'B', 'b'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def plan_of(sidecar):
    """Exact plan recomputed from the stored sidecar and its current asset only (no PDF is opened)."""
    state = json.loads(Path(sidecar).read_text(encoding='utf-8'))
    sid, slot = next(iter(state['slots'].items()))
    record = slot['semantic']
    font = semantic._asset(state, slot, record['payload'], record.get('current'))
    try:
        derived, plan = semantic._derive(state, slot, record['payload'], font)
    finally:
        font.font.close()
    assert derived == record['derived']
    return state, plan


def page_top(pdf):
    """Current page frame (MediaBox top), the same constant the island writer reads; not paint output."""
    with pymupdf.open(pdf) as document:
        box = document[0].mediabox
        assert box.x0 == 0 and box.y0 == 0 and document[0].rotation == 0
        return str(F(str(box.y1)))


def underline(plan, page_top, start, end, recipe=RECIPE):
    """Prototype: one rectangle per line over the painted glyphs in [start, end), boundary spaces excluded."""
    offset, thickness = F(recipe['offset']), F(recipe['thickness'])
    out = 'q\n'
    for line in plan['lines']:
        glyphs = [g for g in plan['emitted'] if line['start'] <= g['offset'] < line['end']
                  and start <= g['offset'] < end]
        while glyphs and glyphs[0]['text'] == ' ':
            glyphs.pop(0)
        while glyphs and glyphs[-1]['text'] == ' ':
            glyphs.pop()
        if not glyphs:
            continue
        left = F(glyphs[0]['origin'][0])
        right = F(glyphs[-1]['origin'][0]) + F(glyphs[-1]['advance'])
        top = F(page_top) - (F(line['baseline']) + offset)
        bottom = top - thickness
        for n, (x, y) in enumerate([(left, top), (right, top), (right, bottom), (left, bottom)]):
            out += f"{island.decimal(x)} {island.decimal(y)} {'m' if n == 0 else 'l'}\n"
        out += 'h\nf\n'
    return (out + 'Q\n').encode('ascii')


@pytest.fixture(scope='module')
def chain(tmp_path_factory):
    root = tmp_path_factory.mktemp('paint-reassessment')
    _, font_a = make_flow(root / 'main')
    main = root / 'main'
    font_b = root / 'font-b.ttf'
    font_b.write_bytes(alternate_font())
    v3 = semantic.confirm_semantic_layout(main / 'rev1.pdf', main / 'rev1.json', slot_id='slot-0',
                                          semantic=statement(font_a))
    (main / 'v3.json').write_text(json.dumps(v3), encoding='utf-8')
    rev = {'confirmed': dict(pdf=main / 'rev1.pdf', sidecar=main / 'v3.json')}

    def build(name, parent, request, asset=None):
        result = writer.build_semantic_candidate(rev[parent]['pdf'], rev[parent]['sidecar'], request,
                                                 workspace=root / 'work', asset=asset)
        rev[name] = dict(pdf=result['pdf'], sidecar=result['sidecar'])

    edge = dict(left=2, right=3, delta='6/5', operator='TJ', semantics='confirmed-adjacent-pair',
                boundary_policy='suppress-at-line-end')
    build('save1', 'confirmed', dict(operation='save'))
    build('save2', 'save1', dict(operation='save'))
    build('save3', 'save2', dict(operation='save'))
    build('edit', 'save3', dict(operation='edit', start=1, end=1, text='A'))           # "AA B"
    build('edit_noop', 'edit', dict(operation='save'))
    build('scaled', 'edit_noop', dict(operation='reinterpret',
                                      changes=dict(font_size='37/3', horizontal_scale='4/5', tracking='1/4')))
    build('scaled_noop1', 'scaled', dict(operation='save'))
    build('scaled_noop2', 'scaled_noop1', dict(operation='save'))
    build('tw', 'scaled_noop2', dict(operation='reinterpret', changes=dict(word_spacing='6/5')))
    build('tw_noop', 'tw', dict(operation='save'))
    build('edge', 'tw_noop', dict(operation='reinterpret', changes=dict(word_spacing='0', edges=[edge])))
    build('edge_noop', 'edge', dict(operation='save'))
    build('font_b', 'edge_noop', dict(operation='reinterpret', changes=dict(font=sha(font_b))), font_b)
    build('font_b_noop', 'font_b', dict(operation='save'))
    return rev


def bodies(chain, name, ranges=((0, None),)):
    state, plan = plan_of(chain[name]['sidecar'])
    slot = next(iter(state['slots'].values()))
    top = page_top(chain[name]['pdf'])
    text = slot['semantic']['payload']['text']
    return [underline(plan, top, a, len(text) if b is None else b) for a, b in ranges]


def test_semantic_surface_has_no_paint_ownership_today():
    # Code facts the reassessment depends on (also covered by test_source_ownership/test_semantic_*):
    # the owned source-output body admits text groups only, so paint cannot live inside it unchanged.
    assert not PATH_OPERATORS & owned.BODY_OPERATORS
    owned.grammar(b'q BT [] TJ ET Q\n')
    with pytest.raises(Exception, match='grammar'):
        owned.grammar(b'q BT [] TJ ET Q\nq 0 0 m 1 0 l 1 1 l 0 1 l h f Q\n')


@pytest.mark.parametrize('group', [
    ('save1', 'save2', 'save3'),
    ('edit', 'edit_noop'),
    ('scaled', 'scaled_noop1', 'scaled_noop2'),
    ('tw', 'tw_noop'),
    ('edge', 'edge_noop'),
    ('font_b', 'font_b_noop'),
])
def test_paint_geometry_from_the_exact_plan_is_a_noop_fixed_point(chain, group):
    """B1/B1-L: same semantic state ⇒ byte-identical prototype paint, including first → noop and scale."""
    ranges = ((0, None), (0, 1), (1, None))
    results = [bodies(chain, name, ranges) for name in group]
    assert all(r == results[0] for r in results), group
    for body in results[0]:
        assert body.startswith(b'q\n') and body.endswith(b'f\nQ\n')


def test_semantic_changes_change_paint_geometry_and_tw_edge_stay_physically_equal(chain):
    base, edit, scaled = (bodies(chain, n)[0] for n in ('save1', 'edit', 'scaled'))
    assert len({base, edit, scaled}) == 3  # range growth and style change move the rectangle
    tw, edge, font_b = (bodies(chain, n)[0] for n in ('tw', 'edge', 'font_b'))
    assert tw == edge  # Tw 6/5 and a confirmed edge of 6/5 give the same positions, so the same paint
    assert tw != bodies(chain, 'scaled')[0] and font_b != edge  # Tw moves B; font B has other advances


def test_paint_geometry_needs_only_the_current_sidecar_and_asset(chain):
    """Current-only: a fresh process recomputes the same bytes from the sidecar, asset and page frame alone.

    No earlier PDF, report, rendered path or trace is an input.
    """
    code = ('import json, sys; sys.path.insert(0, "tests"); '
            'from test_paint_reassessment import plan_of, underline; '
            'state, plan = plan_of(sys.argv[1]); slot = next(iter(state["slots"].values())); '
            'print(json.dumps(underline(plan, sys.argv[2], 0, len(slot["semantic"]["payload"]["text"])).decode()))')
    for name in ('save1', 'scaled_noop2', 'edge_noop'):
        out = subprocess.run([sys.executable, '-c', code, str(chain[name]['sidecar']), page_top(chain[name]['pdf'])],
                             capture_output=True,
                             text=True, check=True, cwd=ROOT)
        assert json.loads(out.stdout).encode('ascii') == bodies(chain, name)[0], name


def test_island_text_witness_is_unchanged_by_the_probe(chain):
    """The probe is read-only: every revision still reopens restored and canonical."""
    for name in ('save3', 'edit_noop', 'scaled_noop2', 'tw_noop', 'edge_noop', 'font_b_noop'):
        result = semantic.open_semantic_flow(chain[name]['pdf'], chain[name]['sidecar'])
        assert result['status'] == 'restored' and result['island']['canonical'], name
