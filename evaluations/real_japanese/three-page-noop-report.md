# Third-page extension no-op precision repair

**Scope: extension-3-noop only.** This follow-up stops after the saved extension's
no-op and directly related regressions. Three-page growth and the remaining
shorten/regrow/re-edit lifecycle are not executed here. **REAL-WORLD THREE-PAGE
SEQUENTIAL REFLOW remains NOT VALIDATED.** No PR is created for this follow-up.

Starting checkpoint: `35a3e3f` (preserved WIP on `validate-real-three-page-reflow`).

## Diagnosis

The unchanged courts original 97044 remains SHA-256
`70ce8722348b21c10a7035a5b6dd09dc641b032d7ec33588e9bc8cb711ef17f2`.
The input is the existing saved `grown-2.pdf`, SHA-256
`471075481a135ec49a12c8a83220596598b13c2a84d621d10ce071e2662c5939`.
The existing `extension-3-accepted.json` binds exactly that revision. Initial B1,
ownership adoption and the earlier edits are not replayed.

The previous `extension-3-noop.pdf` saved and reopened `restored`, but independent
Poppler comparison failed: **5,424 pixels**, all in one page-2 line, pixel bounds
`(170, 539, 1076, 563)` at 144 dpi. MuPDF page 2 had zero changed pixels. The affected
line begins “成２８年９月までに本件住宅の６０６号室” and contains 36 glyphs.

The original and output source codes, GIDs, font resources and programs agreed.
All 853 retained page-2 occurrences were checked; 616 had small Tm changes, but
only this line crossed Poppler's raster boundary. D1's first baseline stayed at
126.4 pt. Its later baselines drifted because `source_line_gap` reobserved float32
positions, then `rich_layout` clamped the observed gaps to the unchanged minimum
25.55999755859375 pt and accumulated them again.

| D1 measurement | Saved input plan | Failed no-op plan |
| --- | ---: | ---: |
| Affected line baseline | 279.9999755859375 | 280.0000366210937 |
| Last baseline | 331.2699951171875 | 331.27008666992185 |
| Affected native Tm y | 561.920043945 | 561.919921875 |

The earlier 172-pixel precision issue involved a later glyph within an original
TJ operator. This failure begins earlier, in repeated layout of already generated
lines. Merely widening the writer's same-position guard would hide the cause and
allow repeated layout drift; that guard is unchanged.

## Bounded repair

A generated slot's saved line positions are **candidates**, not trusted geometry.
The candidate path requires no local edits and unchanged x, width, first baseline,
indent and minimum line height. Every logical unit must retain its exact current
occurrence, every complete line range must match, and every glyph must reproduce
its current native Tm with the writer's exact CTM, rise, basis and number
serialization. A failed proof uses the existing observed-gap path.

The final laid-out ranges and baselines must also equal the proven candidates
exactly. A candidate can map to the same current Tm yet fall below the minimum
line height; if the existing clamp changes its final position, the operation
refuses before publication. No positional epsilon is added. Minimum line height,
ink leading, paragraph gap/floor, bounds, source ownership, font/code preservation
and the writer's same-position test remain unchanged.

The shared planner, local measurement and actual writer use the same proof. A
changed follower baseline is remeasured through the normal layout path. No
sidecar array can authorize another rendered position merely by resealing its
checksum or claiming generated provenance.

A read-only probe of the actual saved D1–B owners, including the final guard,
reproduces **all 26 existing writer baselines exactly (maximum difference 0.0 pt)**.

## Measured checks

The final fixture batch passed **13 tests in 4.94 s**, no skips:

```bash
.venv/bin/python -m pytest -q \
  tests/test_witnessed_source_line_positions.py \
  tests/test_retained_source_positions.py --tb=short
```

Eleven new cases exercise the old observed-gap drift, exact native Tm replay,
MuPDF and Poppler no-op equality, altered baseline/range/missing-line candidates,
changed placement policies and edited occurrences. A 1e-9 pt candidate alteration
that round-trips to the same native Tm but fails the final clamp check refuses with
no PDF or sidecar. Two existing precision cases cover later-TJ positions and an
actual moved retained occurrence.

During fixture development, the first run had four fixture argument errors and
six passes. After supplying required empty metrics, three affected cases passed;
the positive case exposed an unintended width-edge wrap in the synthetic fixture.
A six-glyph line with explicit spare width retains the same nine baseline values
and avoids that unrelated horizontal boundary. The corrected ten-case batch
passed in 3.39 s; adding the clamp guard/regression yielded the final thirteen-case
result above. These reruns are not counted as additional distinct tests.

The one permitted real no-op rerun **failed** after **158.380476 s**, exit 1.
The target page-2 difference is resolved, but the complete no-op is not validated:

| Page | MuPDF changed pixels | Poppler changed pixels |
| --- | ---: | ---: |
| 1 | 0 | 0 |
| 2 | 0 | 0 |
| 3 | 0 | **225 — FAIL** |
| 4–13 | 0 (read-only follow-up) | 0 (read-only follow-up) |

The audit raises at its first failing comparison. All preceding page-1/2 glyph,
foreign and renderer checks passed; page-3 glyph/foreign and MuPDF checks passed
before Poppler failed. The audit itself stops there; these are partial glyph-preservation results,
not a completed all-page audit. A separate read-only comparison of the existing
PDFs covers pages 4–13 at 144 dpi in both renderers: all changes are 0, in
7.029793 s. Thus every page has renderer evidence, with only the existing page-3
Poppler difference remaining. This comparison does not repeat a save or resume
the aborted glyph audit.
D1's saved output baselines now exactly equal the input plan's nine baselines.

The new PDF and sidecar saved and reopened `restored`. Their PDF SHA-256 is
`c3f855ca6c590f7334fbb47bfe1acb7ef734e1a35520bf90edabff665cc1a7af`.
The original SHA is still unchanged. Existing source markers, creation bindings,
continuation destinations, provider specifications and generated-font registry
checks passed before the independent audit. The new stem is
`tmp/courts-three-page/run-01/extension-3-noop-stable`; the previous failure and
both sets of PDF/sidecar/report/images remain available. The failed audit does
not emit a successful audit JSON.

Per the explicit one-rerun limit, investigation and production changes stop here.
A read-only comparison of the already saved previous no-op's page 3 establishes
that this difference predates the repair: old no-op versus input is 225 pixels,
new no-op versus input is the same 225 pixels, and old versus new is **0 pixels**.
Both input comparisons have bounds `(525, 212, 546, 235)` at 144 dpi. The earlier
whole-document audit had stopped on page 2 before measuring page 3. This comparison
performs no edit/save and is not another real no-op attempt. The cause of the
remaining page-3 difference is not investigated in this follow-up.

Three directly affected existing tests subsequently passed in **8.06 s**:

```bash
.venv/bin/python -m pytest -q \
  tests/test_source_adjacency_flow.py::test_noedit_reproduces_every_glyph_without_justify \
  tests/test_source_adjacency_flow.py::test_two_edits_wrap_push_following_and_preserve_foreign \
  tests/test_rich_layout.py::test_witnessed_line_gaps_keep_minimum_height_metrics_and_bottom_limits \
  --tb=short
```

Together with the 13-test batch, these are **16 distinct passing tests**, no skips.
No additional real no-op, growth, lifecycle or expanded regression is executed.
[Machine-readable measured result](three-page-noop-summary.json).

Environment: Linux x86_64, Python 3.12.14, PyMuPDF 1.27.2.3 / MuPDF 1.27.2,
Poppler 26.05.0, pypdf 6.10.0. Earlier interrupted phases were not timed as one
continuous evaluation; no total duration is inferred. Full suite and broad
regressions are not run.
