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

The pinned provider is `BIZUDMincho-Regular.ttf`, SHA-256 `468ee6d9…5af2`. The copy on google/fonts `main` now has a
different SHA-256 (`b3e03aaa…cda3`), so the evaluation would refuse it. The pinned revision has to be fetched from
the repository history.

No numbers below are measured on the real PDF.

## Command (to run alone; PR #72 needed 134 min and hit `MemoryError` when run in parallel)

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
