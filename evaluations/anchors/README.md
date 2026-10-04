# Anchored paint ownership observation

[Design and verdict](../../docs/anchored-paint-ownership.md): **NOT READY**.
[Compact evidence](paint-ownership-summary.json) records current runtime behavior,
not a prototype ownership parser/writer. Base: `0e468ff7fd3abd76612b5de6cffd2444f8135a2f`.

## Reproduce on Windows

Run from the repository root with the existing development environment:

```powershell
$env:PATH='C:\Users\agri0\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin;'+$env:PATH
.venv\Scripts\python.exe -m evaluations.anchors.paint_ownership --work evaluations/anchors/runs/paint-ownership-NEW --output evaluations/anchors/paint-ownership-summary.json
```

The work directory must not exist. Local raw artifacts are ignored: synthetic
PDFs, font assets, current sidecars, observer reports and renderer images.
Public evidence omits font paths and raw extraction. `summarize_geometry` reads
the saved current snapshots/programs to add path-token inventory, geometry and
the original KEEP suffix state/glyph comparison; it never rewrites a PDF.
The recorded run used `runs/paint-ownership-01`; these extra measurements were
added by that read-only summarizer after the same run, without repeating edits.

Ten fixtures cover active no-op x3, change/no-op, refused full empty,
disjoint groups, two text styles, multiple segments, two paragraphs on one
page, fixed background, intervening foreign path with marker-looking comments,
inherited q/line state, nonidentity scale/translation, rectangular clip and
partial-group deletion/reinsertion. A final additional case uses separate black
.7 and blue .9 underlines with different text styles. Two-paragraph observations use existing
ordinary editable planning/binding APIs within one Transaction, with two
existing-format sidecars carried in an evaluator-only JSON list.

There are 29 successful saves (10 initial, 19 fresh-process re-edits), one
fresh-process empty refusal, and 16 no-op renderer comparisons across both
pages. The worker reads only current PDF/current sidecar(s)/font assets plus
the requested edit. Previous report/PDF/mutation records are not inputs.
The observer uses before/after files to reconcile mutation deltas and compare
pixels; it never passes its provenance reconstruction back to the editor.

All 16 MuPDF/Poppler comparisons passed at 144 dpi. Poppler was 26.07.0;
Python 3.12.14, PyMuPDF 1.27.2.3. The inherited renderer harness requires
Poppler for this recorded check. CTM no-op geometry/payload hashes changed
despite zero pixel diff. Full empty was refused with neither file published,
so empty-noop/regrow are explicitly unreachable, not reported as passing.

## Focused tests

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_paint_ownership_observation.py tests/test_anchors.py tests/test_editable.py tests/test_transaction.py::test_anchored_decoration_and_follows_share_one_identity_map tests/test_transaction.py::test_paragraph_report_records_rebuild_the_identity_map
```

Final coverage: **6 helper/guard tests + 28 existing focused tests = 34 unique
passing tests**. Existing focused tests were run once with the initial helper
set; only the helper file was rerun after its two fixture-input corrections.
The full-delete test now supplies the explicit style ID needed before reaching
the dormant guard; the private-flag test supplies `state: owned` before reaching
the ordinary owner guard. No runtime fix was made for those test setup errors.

The new late-failure test injects a paint rebind error after the temporary PDF
is saved and verifies that neither public file nor the temporary directory
survives. Existing tests cover rollback of our first link if second publication
fails. This is exception rollback, not a crash-atomic two-file filesystem commit.

No full suite or external LibreOffice original was run. Runtime digest remains
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.

## Independent review

Claude Opus 5.5 reviewed head `ef43c729c9875ebbb0980a899e0ab68344a2f60a`: **PASS — NOT READY CONFIRMED**.
The evaluator observes current runtime output only (token spans are explicitly not owned ranges; no
ownership parser or writer). Summary values were reconciled, including no-op +2,028/+192 =
text +1,784/+172 + decoration +244/+20. Focused tests were rerun: 34 passed (138.48s).
Full suite and external originals were not run. Findings, including the added blocker B4, are in
[docs §14](../../docs/anchored-paint-ownership.md#14-独立レビュー結果).

## PR #35 design gates — 2026-10-04

New scope: **NARROW V1**; verdict: **NOT READY**, with measured blocker B1-L
(current planned glyph geometry changes on first/change-to-noop round trips).
The historical PR #35 summary/evaluator and Opus review above are unchanged.
See [docs §15](../../docs/anchored-paint-ownership.md#15-b1b4-design-gates--2026-10-04)
and [new evidence](paint-contract-summary.json).

```powershell
$env:PATH='C:\Users\agri0\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin;'+$env:PATH
.venv\Scripts\python.exe -m evaluations.anchors.paint_contract --work evaluations/anchors/runs/paint-contract-NEW --output evaluations/anchors/paint-contract-summary.json
.venv\Scripts\python.exe -m pytest -q tests/test_paint_contract_design.py tests/test_anchors.py::test_range_projection_and_boundary_affinity tests/test_editable.py::test_failed_second_publication_rolls_back_only_our_outputs tests/test_transaction.py::test_anchored_decoration_and_follows_share_one_identity_map tests/test_paint_ownership_observation.py::test_late_emitted_path_rebind_failure_keeps_publication_empty tests/test_paint_ownership_observation.py::test_ordinary_source_output_owner_guard_stays_closed
```

Recorded work directory: `runs/paint-contract-01`. The script reuses PR #35's
synthetic fixture builder and fresh-process **current runtime** worker. It
captures planned origins/advances/line allocation/style IDs and source font
metrics; no old report or geometry is supplied to the runtime re-edit worker.
Observer comparisons use the reports only after the corresponding save.

`canonical_geometry.py` is a pure, small rectangle formatter experiment. Inputs
are the fixed creation recipe, current planned page-coordinate rows and exact
source-decimal CTM. Recipe/local vertices use an explicit 1e-6 grid and exact
rational half-even rounding; there is no negative zero/exponent spelling.
Prototype bodies are **never installed in a PDF**. Determinism for identical
inputs passes, but actual reopen inputs drift and produce different bodies.
This is not a runtime ownership parser/writer or a claim of canonical owned
PDF output. Renderer comparisons apply only to unmodified current-runtime saves.

`paint_boundary_analysis.py` reads existing source boundaries, including
ExtGState with opacity 1 but nondefault BM, page Group, pending path/clip,
marked/compatibility/text scopes and rectangle/compound/curve clips. A positive
boundary result still needs full containment/clip geometry/ownership proofs.
`paint_contract.py` also contains symbolic inventory/lifecycle/identity models.
Their validated-ownership flag is a stated precondition; it grants no PDF rights.

Evidence: 12 geometry saves, 10 fresh-process re-edits, 8 dual-renderer no-op
comparisons across both pages (all pixel differences zero), 14 read-only
boundary cases, and the same-page A/B current-sidecar failure sequence.
The A/B sequence has one coordinated initial save, two independent A saves
and one refused stale B edit; it demonstrates why scoped marker inventory
alone cannot repair a stale paragraph sidecar. The proposed policy permits
one independent paint owner **per PDF document**, with no automatic rebase.

The final current-run summary was enriched by read-only report/PDF analysis
of the same saves for font metrics; the B edit refusal/A second save and
boundary/policy probes were added independently. No geometry save was retried
or normalized to hide the failing byte gate. The command above runs all stages.

Validation: **6 new design/evidence tests + 5 existing focused tests = 11
passed**. New pure tests were rerun after exact tie-rounding/font-evidence
assertions were added. Full suite and external LibreOffice original were not
run. Runtime digest stays
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.
Raw PDFs/fonts/reports/rasters are ignored. Only the explicit compact summary
is tracked (force-added by exact filename); no ignore policy was broadened.

### Independent review of PR #36 — Claude Opus 5.5

Reviewed head `5e60936959400f8865eac3673f158faa38bf5624` (base `fdba3dc…`):
**PASS WITH NON-BLOCKING NOTES — DESIGN/EVIDENCE ONLY; RUNTIME NOT READY.**
B1-L remains open. The 11 focused tests passed on Linux/Python 3.12.3/PyMuPDF 1.27.2.3.
`paint_contract` was rerun to a scratch output, with only the Windows `DEFAULT_POPPLER` path
redirected to `/usr/bin/pdftoppm` via a wrapper. The rerun reproduced the tracked summary
exactly (0 field differences), so B1-L is not Windows-specific. Full suite and external
originals were not run. No blocking findings. Non-blocking notes cover:

- the additional B1-L triggers: source-line/adjacency change and float32 position dependence
  of trace differences
- legacy mutations overlapping paint-domain blocks
- the creation recipe's authority
- the termination lexical check

The tracked summary, evaluator and tests are unchanged.
Details: [docs §15.11](../../docs/anchored-paint-ownership.md#1511-independent-review--claude-opus-55).


## B1-L layout authority — 2026-10-04

Base `b97c6b8dbba33176f25554ae35618a2ae2568941`, PR #36 merge verified.
**NOT READY.** This adds independent layout design evidence; the PR #36
paint contract, summary and review remain unchanged.

```powershell
.venv\Scripts\python.exe -m evaluations.anchors.layout_observation --work evaluations/anchors/runs/layout-authority-reproduce --output evaluations/anchors/runs/layout-authority-reproduce/summary.json
.venv\Scripts\python.exe -m pytest -q tests/test_layout_authority_design.py tests/test_rich_layout.py tests/test_spacing.py tests/test_style_confirmation.py tests/test_attributed.py
```

Use a new work directory. On other platforms use that environment's Python;
this evaluator has no Poppler/Windows path dependency. It reuses the prior
synthetic Courier and supplied CJK font fixture. No external documents/fonts
are committed. Raw per-stage reports and `physical-observations.json` record
all glyph metrics/provider/source adjacency and semantic/physical style
separately. `layout-authority-summary.json` is the separate compact evidence.

`physical_observation` contains actual unmodified runtime save/reopen evidence
and explicitly labeled read-only layout probes. `logical_candidate` is a pure
preconfirmed ASCII advance-cell experiment: fresh processes receive only the
current record. It does **not** bind a PDF, implement rich_layout, prove subset
font equivalence, or certify a runtime fixed point. Rational cells include
source nominal widths and actual supplied-font integer shaped metrics, with
tracking/word spacing distinct. No snapping, hidden save or tolerance-based
canonical comparison is performed. Physical observations never feed this
candidate as a reopened layout authority.

48 saves: 24 lifecycle (22 fresh re-edits) and 24 spacing (12 fresh re-edits).
24 lifecycle stages cover first/change→noop1/2/3, deletion, active-range growth
and explicit width reflow under identity and .83/.91 + translation CTMs.
Spacing cases cover default/Tc/Tw/Tz/TJ/Tm plus trailing whitespace under both
CTMs. Accuracy compares actual planned origins to saved trace via bound IDs,
and source positions to independent decimal fixture oracles; no raster checks
are run by this evaluator. The fixed-advance ink probe demonstrates a change
in actual `layout_attributed` wrapping at one fixed target width, despite
identical advances. The three nearby width inputs are separate semantic inputs.

17 new design/evidence tests + 85 existing focused tests = **102 passed**;
6 affected pure tests rerun after semantic numeric normalization also passed.
Final evidence: `runs/layout-authority-04/summary.json` copied to the tracked
summary; earlier local runs were evaluator development, not normalization saves.
Runtime digest remains
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.
Full suite and external Windows/LibreOffice original validation not run.

[Design §16](../../docs/anchored-paint-ownership.md#16-b1-l-canonical-layout-authority-evidence--2026-10-04)
compares A–D, defines left-only scope, semantic comparison, unresolved
source intent/font/ink authority and the minimum next design task. Future
paint obligations remain tracked in §15.11.
