# Next-page ownership extension from a restored edited revision

**REAL-WORLD CROSS-PAGE POST-EDIT EXTENSION VALIDATED — PASS**

Starting main: `b22ea6a1fc6486215488995b5b0ebc06186f837c` (PR #70 merged). Evaluated code: PR #71 head
`f8f636f97fd43d523e20ecbe1fdb366403ec8925`, unchanged. No production or evaluator change was needed.

Source: the unmodified ten-page **LibreOfficeへの移行** migration paper.
- URL: https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf
- SHA-256: `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`, identical before and after the
  evaluation.
- New-glyph provider: `BIZUDMincho-Regular.ttf` from google/fonts, SHA-256
  `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`.

Neither file is committed.

## Environment and command

Run on Windows 11 (10.0.26200, AMD64):
- Python 3.12.14, from a fresh `.venv` built with `requirements.lock.txt` and `pip install -e ".[test]"`;
- PyMuPDF 1.27.2.3, fontTools 4.64.0, uharfbuzz 0.55.0, pypdf 6.10.0;
- Poppler `pdftoppm` 26.07.0 (already installed, on `PATH` for the session).

```powershell
$env:PYTHONUTF8 = "1"   # the final verdict line contains U+2014; the default cp932 console cannot print it
.\.venv\Scripts\python.exe -m evaluations.real_japanese.next_page_extension `
  --output tmp/next-page/validation-2 --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The evidence run (`validation-2`) took 34 min 28 s and exited **0**, printing the PASS verdict.

An earlier identical run into `validation` executed every assertion and wrote the same PASS `summary.json`. It then
exited 1 at the very last statement, because Python could not print U+2014 (`—`) to the cp932 console. That was an
environment issue, not an evaluation failure, and was fixed with `PYTHONUTF8=1` without touching code.

The two runs agree on the following:
- the saved PDFs of every stage are **byte-identical** (`saved.pdf` through `5-noop.pdf`, SHA-256 values below);
- the summaries differ only in the proposal digest, which covers the font-cache path inside each output directory.

## Bootstrap and refusal before extension

The initial acceptance owns only the complete page-8 innovator paragraph A (`p8-l24`–`p8-l27`, 164 characters).
It uses an explicit BIZ UDMincho substitution for new glyphs.

The first edit replaces `望むタイプでもあります。` with `望むタイプでもあります。試行の結果を共有します。`:
- A grows from 164 to **176 characters** and still fits four page-8 lines (baselines 705.599976, 726.700012,
  747.100037 and 767.500061 pt).
- The saved revision has SHA-256 `11de0dc5d5a0fca3f048d53c6160c80a92fbcc7da515e25ea04ec8b21a081c76` and reopens
  `restored`. Its only region is `R1` on page 8.

The 54-character cross-page replacement then refuses on this saved revision with
`paragraphs exceed all explicitly confirmed shared regions`. Neither the attempted PDF nor its sidecar is
published:

> 望むタイプでもあります。試行の結果を共有し、利用者の疑問や改善の提案を記録することで、次の移行に役立てます。

## Current-revision next-page approval

Page 9 of the **saved** revision is observed again. Paragraph B is the complete early-adopter paragraph
(`p9-l1`–`p9-l3`, 146 characters, current glyph IDs 0–145). Its page observation is
`afe5f91b…1ddf1`, and its snapshot names the saved SHA.

It is proposed as a new owner with:
- region `page-9`: bounds `[56.8, 108.2, 538.2, 200.271454]`, first baseline 142 pt, width 481.4 pt;
- the caller's A→B page break: 26.4 pt with `source-region-floor-and-same-page-follow`;
- the vacated continuation `release-9`: `[56.8, 110, 540, 174]`, first baseline 122 pt. It sits on the current page-9
  program boundary ordinal 0 (`boundary-0a9c000f79022441eb448b11`) and releases only B.

Three expected refusals were recorded:
- confirming the occupied rectangle as an empty destination: `composed text collides with an unselected glyph`;
- accepting the proposal on the original PDF: `owned-flow proposal belongs to another PDF revision`;
- (before extension, above) the cross-page growth.

Acceptance on the saved revision:
- The receipt is schema `pdfengine-owned-flow-acceptance-1`, proposal digest `2216c479…18b4` and state model
  `3b13e115…1e90`.
- The joint no-edit reproduction (`plan_shared_flow`, no edits) reports `reproduced` for 7 lines.
- A's paragraph, `slot-0` binding, `slot-0` source-output record and the generated fonts compare equal to the
  restored state.
- B becomes `slot-1`, a new `uninitialized` source owner whose selection names the saved SHA.
- The flow regions are `R1`, `release-9` and `page-9`. Destination `A-next` targets `release-9`.

## Extension no-op

The extension no-op saves and reopens `restored`. It preserves all **322 current glyphs** (176 + 146): code,
resource, font program and GID are equal, and the maximum origin error is **0.0 pt**.

**MuPDF (144 dpi) and Poppler (1600-pixel page scale) are pixel-identical on all ten pages.** Foreign text,
drawings, images, links and annotations are equal. A stays on four page-8 lines, and B stays at baselines 142,
162.400024 and 182.900024 pt.

## Same growth after extension

The previously refused 54-character replacement now succeeds from the extended revision:

| Paragraph / slot | Page | Characters | Lines | Baselines (pt) | Retained / provided glyphs |
|---|---|---:|---:|---|---|
| A `slot-0` | 8 | 199 | 4 | 705.599976, 726.700012, 747.100037, 767.500061 | 152 / 47 |
| A `continuation-3faffb690d270cf0bf3f4ea8` | 9 | 7 (`に役立てます。`) | 1 | **122** | 0 / 7 |
| B `slot-1` | 9 | 146 | 3 | **148.4**, 168.800024, 189.300024 | 146 / 0 |

- A grows **176 → 206 characters**. Its continuation starts at the `release-9` first baseline, 122 pt.
- B moves **142 → 148.4 pt (6.4 pt)**: A's continuation baseline 122 pt plus the caller's 26.4 pt gap. Its last
  baseline moves from 182.900024 to 189.300024 pt.
- The release Transaction witness names exactly `slot-1` as the released source owner, with all **146** of its
  glyphs consumed. The witness was produced in the same Transaction as the first save of that adopted owner.
- Maximum glyph-origin error is 0.000024 pt. Outside the edited regions, MuPDF changed pixels are **0** on all ten
  pages, and foreign content is equal.

Growth beyond the accepted capacity, to the PR #69 130-character `LONG` replacement, still refuses on this revision
with `paragraphs exceed all explicitly confirmed shared regions`. Nothing is published.

## Save/reopen lifecycle

Every save starts from the previous saved revision and reopens `restored`:
- the source-output records (marker identity) of `slot-0` and `slot-1` are immutable through every stage;
- `continuation_destinations` stays equal to the accepted one;
- the generated slot keeps its creation binding from the grow stage on.

| Stage | Edit | A (page 8 / page 9) | B | Saved SHA-256 | Max origin error | Outside MuPDF pixels |
|---|---|---|---|---|---:|---:|
| 0 extension no-op | — | 176 / — | 146 chars, 142 pt | `11e72c689b36d8f0fc3922674e5423684a1f519dbca3c1151c32d3d2a0037cc3` | 0.0 | 0 (whole document identical, MuPDF + Poppler) |
| 1 grow | A + 30 chars | 199 / 7 | 146, 148.4 pt | `cd857af23b8dc67a1512c80bba5b591d748743eb04e4b2666175060138e679fe` | 0.000024 | 0 |
| 2 second | B `変化を肯定的` → `新しい変化を肯定的` | 199 / 7 | 149 chars (140 retained + 9 provided), 148.4 → last 189.200049 pt | `89a7b8b2b975d66e1869e61b6672ec645886e0ce4f43fc5c5cd17536910d01ae` | 0.000024 | 0 |
| 3 shorten | A back to 164; B reverted | 164 / **0** (continuation empty, occupancy `None`) | 146, back to **142 pt** (last 182.800049) | `30d850df6bbdc5534fc5c00321ef02759b9c7557895cb1efd8f4fbf85aa3db98` | 0.000022 | 0 |
| 4 regrow | A + 42 chars again | 199 / 7 (same generated slot) | 146, 148.4 pt | `8d7c7a46cd7f8cbf4f02de99064a39bb690a2a4807dcea4acea80a6cdb7ae87d` | 0.000024 | 0 |
| 5 final no-op | — | 199 / 7 | 146, 148.4 pt | `18d13a5600f76a0538fadc87995b16ca8308ad9c9db693c711b98cc3ca93104b` | 0.000024 | 0 (whole document MuPDF identical to stage 4) |

On every stage the audit reports the following:
- Unicode exact in MuPDF and pypdf;
- retained codes, resources, GIDs and programs equal;
- provided outlines and advances equal to the provider;
- foreign text, drawings, images, links and annotations equal;
- all ten pages checked;
- untagged structure, PDF version/count and operator nesting preserved.

The final original-vs-final Poppler comparison outside the edited regions has **0 changed pixels**. The original
PDF's SHA-256 is unchanged.

## Selected case and why

Pages 8→9 of the migration paper. Page 8 ends the complete innovator paragraph A (`p8-l24`–`p8-l27`, ending
`望むタイプでもあります。`). Page 9 begins the complete early-adopter paragraph B (`p9-l1`–`p9-l3`) in the
same communication-strategy section.

PR #68 measured that nothing unselected lies at this seam, and that a page-9 boundary candidate (ordinal 0) yields a
qualified continuation above B. Page 10 starts a different section and is **not** adopted. C–H on page 9 also stay
foreign.

## Production contract

`propose_owned_flow_extension(saved, restored_state, [b1_page], paragraphs=..., relationship=..., next_page=...)`
dispatches to `post_edit_extension` for a restored edited allocation.

- **Current re-observation.** The B1 page proposal is built on the saved revision. New owners take current
  glyph IDs, Unicode and baselines; nothing is copied from original page numbers or old glyph IDs.
- **Order and seam.** The page must be the next page after the last paragraph's last source region, and must
  hold no region or destination of this flow. On page *n* the last owned line (source or generated) must belong
  to the last paragraph and nothing unselected may follow it in the column band; on page *n*+1 nothing
  unselected may precede the first adopted line. Partly owned lines refuse.
- **Spacing.** `page_break` is the caller's same-page gap with `source-region-floor-and-same-page-follow`. The
  seam baselines are recorded as evidence only; no gap is derived from them.
- **Paint authority.** `vacated_continuation` names a region on page *n*+1, released adopted owners and the
  continuation confirmation. `_confirm_destination` re-proves it on the current revision: insertion boundary,
  graphics state, CTM, clip and emptiness except exactly those owners. `continuation` accepts an empty
  destination already confirmed on the current revision. Releasing an existing owner, an unreleased owner or fixed
  paint inside the region refuses.
- **Identity.** Existing slots, snapshots, source-output markers, destination and binding records and generated
  fonts are carried unchanged. New owners get current-revision `source_adoptions`. The vacated-space proof now
  carries the adoption record of an uninitialized released owner, so its removal is proved in the same
  Transaction before its first save.
- **Proofs.** Full standalone B1 qualification and reproduction of the new page, then the mandatory joint no-edit
  reproduction of every current line, including existing generated continuations.

## Fixture evidence (synthetic PDFs; not a real-PDF claim)

`tests/test_next_page_extension.py`:

- A+B owned on page 1 only, B edited and restored. Growth of A refuses. After adopting C and D on page 2 with a
  release region, the no-op is pixel-identical on both pages; the same growth moves B to the page-1 bottom,
  continues its last line at 62 pt on page 2 and moves C from 80 to 88.5 pt (26.5 pt caller gap). The first
  growth releases the still-uninitialized adopted C in the same Transaction. Second edit, shortening (continuation
  dormant, C back to its 80 pt floor) and regrowth reopen `restored`; exterior raster is unchanged on every save.
- Three pages: an existing generated A continuation on page 2 keeps its slot, creation binding, destination and
  binding while C on page 3 is adopted with an independently confirmed **empty** continuation and a qualified
  `region_top` override; B's growth then continues on page 3 and moves C.
- Nine refusal cases, each publishing nothing on edit refusals: stale revision, changed review, non-finite gap,
  skipped seam text, release of an existing owner, unreleased adopted owner inside the region, fixed paint inside
  the region, missing continuation authority and growth beyond the accepted capacity.

## Focused tests (PR #71 record)

These focused results are the PR #71 implementation record (Linux). This real-PDF validation changed no code, so
they were not rerun here.


Linux, Python 3.13, final production code; **172 distinct focused cases passed, zero failures**:

- New `test_next_page_extension.py`: **10 passed, 368.38 s** (lifecycle + nine refusals), then the three-page
  generated-identity case **1 passed, 40.60 s** after its fixture fix (an unselected `FIXED CONTENT` line between
  the pages was correctly refused by the seam check; the shared `make_source` helper gained an optional
  `fixed_pages` argument whose default is unchanged).
- Cross-page owned flow, page-break reflow, original-source extension, restored same-page extension and
  vacated-space (all importers of the changed modules/helper): **51 passed, 1145.82 s** (run concurrently with the next batch).
- Source ownership, source-adjacency continuation/flow, flow Transaction, Transaction, shared flow and owned-body
  reflow: **110 passed, 319.93 s**.

Full suite: **not run** (task instruction and `CLAUDE.md`). Real-PDF evaluation: **run and PASS** (above).
