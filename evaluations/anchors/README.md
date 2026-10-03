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
