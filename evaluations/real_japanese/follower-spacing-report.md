# Approved follower spacing on one restored edited history

**NOT VALIDATED: the real-PDF evaluation has not been run.** The verdict
`REAL-WORLD FOLLOWER SPACING POLICY VALIDATED — PASS` is **not** claimed.

Starting main: `045e3551c4a576d40e4d00efbea60a9d8707ba7b` (PR #72 merged).

## Why it was not run

The implementing session's egress policy denied the source host:
- `wiki.documentfoundation.org:443` returned `CONNECT tunnel failed, response 403`;
- the `web.archive.org` mirror was denied the same way.

The unmodified **LibreOfficeへの移行** PDF (SHA-256 `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`)
was therefore unavailable.

The implementing session also lacked the pinned provider. The follow-up below resolved that prerequisite;
the source PDF remains unavailable in this cloud environment.

No numbers below are measured on the real PDF.

## Cloud follow-up (2026-10-13 UTC)

Inspected and tested PR #73 head `c57d9f6e4f7bba21b9fa505b4e7ab153b8308b07`, on the existing
`claude/festive-brown-1jx2e1` branch. No production code, evaluator assertions or tolerances were changed.

- An HTTPS request to the [exact original PDF URL](https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf)
  at 09:23 UTC failed with
  `curl: (56) CONNECT tunnel failed, response 403`. No PDF bytes were downloaded.
- The required local source is `evaluations/realpdf/corpus/lo_migration_ja.pdf`, SHA-256
  `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`.
  It was absent from the checkout and available local artifact locations.
- **Pinned font obtained and SHA-256 verified.**
  `tmp/b3/fonts/BIZUDMincho-Regular.ttf` has SHA-256
  `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`.
  Its reproducible [google/fonts revision URL](https://raw.githubusercontent.com/google/fonts/63833b7d10bb1f86a8f0b91cba2d3ae1f68d1aa3/ofl/bizudmincho/BIZUDMincho-Regular.ttf)
  is pinned to commit `63833b7d10bb1f86a8f0b91cba2d3ae1f68d1aa3`; no substitute font was used.
- Linux, Python **3.12.14**, PyMuPDF **1.27.2.3** / MuPDF **1.27.2**, Poppler `pdftoppm` **26.05.0**;
  the repository `.venv` uses `requirements.lock.txt`. The evaluator imports and its `--help` command succeeds.
- The environment network draft adds `wiki.documentfoundation.org`; saving the draft alone does not apply it
  to the running environment. The original must become reachable, or the checksum-matching file must be supplied,
  before the real-PDF run can start.

**Real-PDF result: NOT VALIDATED (unrun because the source is missing), not a measured PASS or FAIL.**
C's pre/post-shortening baselines, the approved gap/floor on this revision, all-page MuPDF/Poppler identity,
outside-region pixel counts, real save/reopen and ownership checks, unchanged-original SHA and atomic refusals
have not been measured in this follow-up. The 209.299988 pt and 202.800012 pt values below are prior PR #72
evidence/expectations, not new measurements. The PR remains blocked on this required validation.

### Focused synthetic checks in this environment

**3 passed in 258.61 s (4 min 18 s)** on the unchanged PR head above:

```bash
PYTHONUTF8=1 .venv/bin/python -m pytest -q \
  tests/test_sequential_extension.py::test_approved_follower_floor_keeps_d_out_of_the_release_region \
  tests/test_sequential_extension.py::test_low_approved_floor_still_refuses_the_release_region_collision \
  tests/test_sequential_extension.py::test_follower_placement_policy_refusals \
  --durations=3
```

These existing fixtures exercise approved-floor shortening/regrowth and repeated restored saves,
unchanged markers/destination identity/exterior raster, refusal of unauthorized release-region paint with
neither PDF nor sidecar published, capacity refusal, and invalid/stale/tampered policies. They establish
that the relevant tests run in this Linux environment; they do not replace the required real-PDF evidence.
The previous 917-test run was not repeated. The full suite and real-PDF evaluator were not run here;
there is no real-evaluation duration or output directory to report. Documentation whitespace checks pass.

## Command (to run alone; PR #72 needed 134 min and hit `MemoryError` when run in parallel)

Linux, from the checkout; always use a new output directory:

```bash
PYTHONUTF8=1 .venv/bin/python -m evaluations.real_japanese.follower_spacing \
  --output "tmp/follower/validation-$(date -u +%Y%m%dT%H%M%SZ)" \
  --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

Existing Windows command:

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m evaluations.real_japanese.follower_spacing `
  --output tmp/follower/run-1 --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

## What the script does

The script is `follower_spacing.py`. It reuses the PR #72 measurement code: `first_extension`, `second_extension`,
`refuse`, `audit`, `audit_original_noop`, `pixel_equal` and `poppler`. It omits two steps that PR #72 already measured
and that this PR does not change: the refused growth before the first extension, and the separate `grow` stage
after the second extension.

1. The PR #72 history:
   - A on page 8 is edited and saved;
   - B on page 9 is adopted with release `release-9`;
   - A grows to `SHORT`, which moves B 142 → 148.4 pt, and the result is saved and reopened;
   - the `SHORT → LONG` growth refuses.
2. **Second extension with an approved policy for C.** The script records the current witness:
   - B's last owned baseline;
   - C's current first baseline (209.299988 pt in PR #72);
   - their gap (≈ 20.0 pt).

   The approved policy is a floor at C's current baseline, with a minimum gap equal to the current gap. As audit
   only, the script records that C's line has the same baseline in the original PDF.

   A policy with the original 26.4 pt gap must refuse at review, because the extension no-op would move C.
3. **Acceptance.** It must carry forward unchanged:
   - A and B paragraphs, slot bindings, source-output records and the generated slot's creation binding;
   - destinations, destination bindings and generated fonts;
   - release owners exactly `['slot-1']`.

   The follow edge must carry `baseline_floor = {page-9, C's current baseline}`.
4. **Extension no-op.** The full `audit`, `audit_original_noop`, and MuPDF and Poppler whole-document pixel identity.
5. **Shorten** (`SHORT → FIND`, A back to 164 characters):
   - the continuation is empty and B returns to 142.0 pt;
   - C stays at the floor, and D–H stay at their no-op baselines.
   - **Comparison:** under PR #72's current-gap rule, C went to 202.800012 pt (6.5 pt above its original position).
6. **Regrow** (`FIND → LONG`) and **second edit** (B `SECOND`): C = B's last baseline + the approved gap, below the
   floor.
7. **Final no-op:** whole-document MuPDF pixel identity.
8. **Lifecycle checks.** Every stage reopens `restored` and keeps unchanged:
   - source-output identities and the generated creation binding;
   - destinations and follows;
   - the expected texts.

   The per-stage audit covers foreign content, outside-region pixels, Unicode, codes, GIDs and programs.
9. **Unsafe growth.** Growth of H beyond the confirmed capacity must refuse and publish nothing.
10. **Final checks.** Original-vs-final Poppler outside the edited regions shows 0 changed pixels, and the original
    SHA-256 is unchanged.

## What is validated instead (synthetic only)

`tests/test_sequential_extension.py` runs the PR #72 fixture with the same structure:
- a page-1 edit;
- a next-page adoption of C with release region `release-2` reaching 116 pt;
- A's growth moves C, compressing the C→D gap from 26.5 to 18 pt;
- an `append_to_region` adoption of D.

These are fixtures, not the real document. See `docs/acrobat-critical-path.md` §6 for the measured behaviour and
the pull request for the test counts.
