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

### Independent review of PR #37 — Claude Opus 5.5

Reviewed head `74309256d620c85079fe676fa02273ad575065a8` (base `b97c6b8…`):
**PASS WITH NON-BLOCKING NOTES — DESIGN/EVIDENCE ONLY; NOT READY**
(layout runtime NOT READY, paint runtime NOT READY, B1-L/B1-L-M open).

- The 102 focused tests passed on Linux/Python 3.12.3/PyMuPDF 1.27.2.3.
- `layout_observation` was rerun to a scratch output. Only the `python` version field
  differed from the tracked summary.
- L1–L3 and B1-L-M are confirmed against the code: `layout_attributed` measures width
  with ink inset/overhang, and `source_ink` falls back to trace bbox. Advance-only closure
  is refuted.
- No blocking findings. Non-blocking notes:
  - a monotone ratchet in scaled semantic size/scale over six saves
  - undocumented spacing outcomes: Tz/Tm scaled geometry, Tw intent loss
  - the bounds_source scope of B1-L-M
  - candidates E/F
  - small candidate and evaluator nits

The tracked summary, evaluator and tests are unchanged.
Details: [docs §16.8](../../docs/anchored-paint-ownership.md#168-independent-review--claude-opus-55).


## Canonical measurement — 2026-10-04

Base `8f9bf6e9e5288b73046ff0c769fa337cde4c537e` includes merged PR #37.
**NOT READY — DESIGN/EVIDENCE ONLY.** Both layout and paint runtime remain NOT READY.
See [design §17](../../docs/anchored-paint-ownership.md#17-canonical-measurement-and-current-only-verification--2026-10-04)
and [separate compact summary](measurement-authority-summary.json).

From repository root, using a new work directory:

```powershell
.venv\Scripts\python.exe -m evaluations.anchors.measurement_observation --work evaluations/anchors/runs/measurement-reproduce --output evaluations/anchors/runs/measurement-reproduce/summary.json
.venv\Scripts\python.exe -m pytest -q tests/test_measurement_authority_design.py tests/test_rich_layout.py tests/test_spacing.py tests/test_style_confirmation.py tests/test_shaped_font.py
```

The evaluator creates a deterministic synthetic static TT font, needs no external
original or Poppler, and uses the unmodified runtime. Re-edit workers consume only
the current PDF/model/request and assets, never previous reports or a save history.
Raw PDFs/fonts/full per-stage observations remain ignored. The compact summary
samples first/last lifecycle glyphs; their full raw observations have SHA digests.

- `physical_observation`: 64 lifecycle saves (four font/CTM cases, first/change/
  growth/reflow each followed by noop1/2/3), 14 boundary saves, 8 combined-style
  saves and 2 separate identity-Tw saves. This is 88 actual saves. Source trace
  ink, embedded vertical normalization, style ratchet and Tw loss are observations
  of the current runtime, not fabricated candidate output.
- `logical_authority_candidate`: exact ASCII operator arithmetic, full metric
  tuple, explicit empty metrics, confirmed adjacent edges and rational left-only
  layout. Eight semantic records × three fresh processes produce 24 exact replays.
  Source widths are witnessed rational inputs, not a general PDF dictionary
  lexical extractor. No PDF writer, runtime replacement, sidecar or schema exists.
- `verification_candidate`: actual current subset used-glyph metric/outline
  samples, bounds inventory and a symbolic 11-field mutation model. These do not
  authenticate complete font, empty-style or spacing-intent association.
- `gates` / `verdict`: individual design gates derive NOT READY. Model determinism
  cannot close the three unproven binding/intent/lifecycle gates.

All 88 plan→saved trace origin checks pass ≤0.002 pt, maximum
0.0000152587890625 pt. This is not a noop-preservation pass: the embedded identity
fixture moves by 2.4 pt and the boundary noise-band example by 16 pt. Exact
canonicality is separate from placement accuracy. No rounding or grid correction
is used to hide these failures. The noise-band width is a diagnostic midpoint,
not a candidate authority rule.

The checked-in evidence retains the completed physical run `runs/measurement-02`
while reassessing the pure model, current font witnesses and gates after refinements.
No hidden normalization save was added. The reproduction command above runs both
layers from scratch. 27 new tests + 94 focused regressions = **121 passed**;
22 affected pure/probe tests also passed after refinements. A final focused rerun
passed all 121; the two strengthened style-ratchet tests then passed separately. Full suite and external
Windows/LibreOffice original validation were not run. Runtime digest unchanged:
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.
PR #36/#37 historical evidence, evaluators and independent review remain intact.

### Independent review of canonical measurement

Claude Opus 5.5 reviewed HEAD `9c68d31c4bcbaad5fb4fc205b24a8d1360441a19`: **PASS WITH NON-BLOCKING NOTES —
DESIGN/EVIDENCE ONLY; NOT READY** (layout and paint runtime NOT READY). Focused tests: 121 passed. The evaluator
rerun into a scratch directory produced a summary identical to the tracked one. Full suite and external originals
were not run. Findings and the next scope are in
[design §17.10](../../docs/anchored-paint-ownership.md#1710-independent-review--claude-opus-55).


## Current semantic binding — 2026-10-04

Base `f749bb893ac883e235ce73d5ea1aa2a5bdc1fddd`, PR #38 merge and independent
review verified. **NOT READY — DESIGN/EVIDENCE ONLY**, both runtimes NOT READY.
[Design §18](../../docs/anchored-paint-ownership.md#18-current-semantic-witness-binding--2026-10-04)
and [separate summary](semantic-binding-summary.json).

```powershell
.venv\Scripts\python.exe -m evaluations.anchors.semantic_binding_observation --work evaluations/anchors/runs/semantic-binding-reproduce --output evaluations/anchors/runs/semantic-binding-reproduce/summary.json
.venv\Scripts\python.exe -m pytest -q tests/test_semantic_binding_design.py tests/test_measurement_authority_design.py tests/test_source_ownership.py tests/test_empty_element.py tests/test_style_confirmation.py tests/test_shaped_font.py
```

Use a new work directory. No external font/document, Poppler or runtime changes
are needed. This is a fixture PDF builder plus read-only design binder, not a
runtime PDF serializer/verifier, schema or authentication service. It consumes a
narrow complete-page language, static simple unhinted TT, one body style, and
A/B/space/newline. Unsupported objects, context, fonts/styles and record history
refuse. `/W` entries are restricted to integer operands; source float parsing is
not used as exact metric authority. The full current file is independently bound.

Nine fixtures save once and reopen in three fresh processes each. Workers receive
only current PDF, current record/receipt, current asset, a separately supplied test
authority and independent target. Raw artifacts and the ephemeral test key remain
in the ignored work directory; keys and receipt tags are absent from the tracked
summary. The test key is **not** a production trust store or proof of user approval.
The issuer is a deliberately unrestricted fixture oracle; production confirmation
issuance and atomic edit publication are explicitly OPEN in layer C.

`positive` records actual extracted current font/program/interval witnesses,
accuracy and exact rebind comparisons. `negative` records 30 refusal cases.
`intent_ambiguity` shows byte-identical Tw/edge PDFs: physical-only matching passes
but the independent confirmation rejects a resealed swap. `gates` separates A
model determinism, B admissibility and C authenticated binding. All scoped fixture
checks pass; the two open design obligations derive NOT READY. Accuracy never
overwrites missing authority. Fixed six-place output decimal spelling does not
change exact logical values or feed renderer output back into authority.

Current font samples allow subset bytes and current GIDs to differ from the asset.
A test-development CID→`.notdef` mutation exposed a missing check when its outline
and width matched A; current nonzero GID/cmap/Unicode correspondence now refuses it.
This is included in the negatives, not silently relabeled as a passing run.

Maximum plan→saved origin discrepancy is 0.000006103515630684342 pt, below 0.002 pt.
No new raster equality or runtime save/noop/authorized-edit lifecycle claim is made.
The lifecycle is authenticated **read-only rebind**, conditional on a separate
trusted confirmation input. Prior PR #36–#38 evidence/review bytes are preserved.
Runtime SHA-256:
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.
Full suite and external original validation: not run.

Validation: **171 passed** (29 new design tests + 142 focused regressions).
The 29 new tests were rerun after final scope checks.
Final tracked evidence is the clean `runs/semantic-binding-05` run. Earlier runs
were evaluator development; no earlier PDF or report feeds a current rebind.

### Independent review of semantic binding

Claude Opus 5.5 reviewed HEAD `218698957c61f3cb36aadca65971d31882959a60`: **PASS WITH NON-BLOCKING NOTES —
DESIGN/EVIDENCE ONLY; NOT READY** (layout and paint runtime NOT READY). Focused tests: 170 passed, 1 skipped (Poppler absent in the review environment). The evaluator
rerun into a scratch directory produced a summary identical to the tracked one (9 positives, 27 exact rebinds, 30/30
negatives refused). Main judgement: B1-L-C needs a closed semantic-transition model with the trusted API caller as
the boundary, not persistent signatures or receipts. Full suite and external originals were not run. Details in
[design §18.10](../../docs/anchored-paint-ownership.md#1810-independent-review--claude-opus-55).


## Authorized layout publication — 2026-10-04

Base `1f5a7f0d70d4365eb0d6fac7cbebd647a3dd6ace` includes merged PR #39/review.
**DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR** within the narrow contract
in [§19](../../docs/anchored-paint-ownership.md#19-authorized-layout-publication-under-p4--2026-10-04).
Both layout and paint runtimes remain NOT READY. [Separate summary](publication-summary.json).

```powershell
.venv\Scripts\python.exe -m evaluations.anchors.publication_observation --work evaluations/anchors/runs/publication-reproduce --output evaluations/anchors/runs/publication-reproduce/summary.json
.venv\Scripts\python.exe -m pytest -q tests/test_authorized_publication_design.py
.venv\Scripts\python.exe -m pytest -q tests/test_semantic_binding_design.py tests/test_transaction.py tests/test_mutation.py tests/test_editable.py tests/test_source_ownership.py -k "not poppler_noop_pixels"
```

Use a new work directory. The evaluator creates only synthetic PDFs/font assets
and named temporary publication targets under that root. No external original,
network service, login, key store or authorization receipt is needed. P4 trusts
the API caller and explicit request; historical §18 HMAC code is not invoked.

The pure state machine and fixture builder are **not runtime API/schema/layout/
serializer/Transaction replacements**. Current physical checks use the existing
strict whole-page synthetic oracle; a separate actual owned-island probe uses
`owned_body`, context/containment, `operators()` spans and MutationProgram. This
is an explicitly separated implementation-adapter design, not a claim of an
integrated runtime island layout writer. Existing ownership regression tests
supply additional coverage. No marker auto-adoption or regex runtime binding.

Evidence: 17 independent repeated requested transitions, four further edge
transitions, explicit alternate font, no-op PDF-byte change without semantic
change, 15 negatives, 13 pre-commit model failures. An additional test covers
post-commit failure with a complete new pair. Six real unmodified `_publish`
probes show why two direct public links are insufficient when rollback fails.
Ten proposed private-directory probes leave either no public new bundle or
both new files; private cleanup failure is recorded, not concealed.

Selected publication scope: a new directory, same local filesystem, exclusive
caller, both verified artifacts private until one directory rename. No overwrite,
no arbitrary independent public paths, no network filesystem guarantee, no crash/
power-loss durability claim. A directory container is not a third receipt/manifest.
A failed private cleanup can leave ignored staging files; they are never current.
The legacy runtime publication behavior is unchanged.

Summary gates derive the scoped DESIGN READY verdict. Exact semantic values never
come back from output decimals. Accuracy ≤0.002 pt is independent of authorization
and publication; maximum origin discrepancy is 0.000006103515630684342 pt.
Full suite and external original validation not run. Poppler-only regression is
explicitly deselected; there is no new raster equality claim.
Runtime digest unchanged:
`d22fb0482e25e3d9a37bdce05bf9a3447f7aa331e684410b8d8dea5ca1f35dea`.
Historical docs/reviews/evidence from PR #36–#39 remain preserved.

Validation: **55 new design tests + 111 focused regressions = 166 passed**.
One Poppler-only test was deliberately deselected; full suite and external-original
validation were not run. Final evidence is the clean `runs/publication-04` run.


PR #40 small-change revision (2026-10-04): publication READY now requires every
expected outcome in both orders, including complete new contents on success and
after-rename failure. Missing evidence and a never-publishing implementation fail.
Ownership READY includes body/context/foreign-byte preservation, parsed spans and
all three refusal probes. `body_style_id` is a semantic label only; its explicit
rename leaves the canonical "body" associations, metrics and PDF bytes unchanged.
The normal, empty and all-space cases are tested.
Validation after revision: **89 passed = 60 publication design + 29 semantic binding
regressions**. The earlier 111 focused-regression result remains historical; the
remaining runtime regressions were not rerun for these evaluator/docs-only fixes.
Evidence regenerated in `runs/publication-05`; runtime digest unchanged. Full suite
and external originals not run. Independent Opus review remains the next step;
layout and paint runtime remain NOT READY.

### Independent review of authorized layout publication

Claude Opus 5.5 reviewed HEAD `f1e91ca5d6f61b5824a5ca2c23901cc6b663a3da`: **REQUEST CHANGES** — one blocking
design gap (B1-L-O). The runtime surface and the source-output owner record that must be published with the PDF are
undefined: source-output ownership exists only in shared-flow v2 sidecars, while the design publishes only
PDF + semantic record. P4, the transition classes, the semantic diff gate, candidate re-verification and the
directory-rename publication contract otherwise hold. Tests: 171 passed, 1 deselected (60 design + 111 regressions). The evaluator rerun reproduced
`publication-summary.json` with 0 differences. Layout and paint runtime remain NOT READY. Details in
[design §19.9](../../docs/anchored-paint-ownership.md#199-independent-review--claude-opus-55).
