# Next-page ownership extension from a restored edited revision

**Real-PDF verdict: NOT VALIDATED — the evaluation was not run.**

`REAL-WORLD CROSS-PAGE POST-EDIT EXTENSION VALIDATED — PASS` is **not** claimed. The production path and
its fixture lifecycle are implemented and tested (below). The real runner could not obtain its source PDF.

Starting main: `b22ea6a1fc6486215488995b5b0ebc06186f837c` (PR #70 merged).

## Why the real evaluation did not run

The selected source is the unmodified ten-page **LibreOfficeへの移行** migration paper
(https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf,
SHA-256 `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`). The PDF is not committed
(third-party binary). In this session's container the egress proxy refused `CONNECT` to
`wiki.documentfoundation.org`, `web.archive.org` and every other corpus host in `evaluations/sources.json`
(Kyoto, Okinawa, Osaka, Wakayama, Takeo, Niigata, fccid.io, ...). Only GitHub hosts were reachable; the BIZ
UDMincho provider (SHA-256 `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`) downloaded and
verified. No substitute document was used, and no measured value below is attributed to the real PDF.

## Selected case and why

Pages 8→9 of the migration paper. Page 8 ends the complete innovator paragraph A (`p8-l24`–`p8-l27`, ending
`望むタイプでもあります。`); page 9 begins the complete early-adopter paragraph B (`p9-l1`–`p9-l3`) in the
same communication-strategy section. PR #68 measured that nothing unselected lies at this seam and that a
page-9 boundary candidate (ordinal 0) yields a qualified continuation above B. Page 10 starts a different
section and is **not** adopted; C–H on page 9 also stay foreign.

Planned lifecycle in `next_page_extension.py` (all operations start from the saved edited revision):

1. Accept only A on page 8 (single-page B1, explicit BIZ UDMincho new-glyph substitution).
2. Replace `望むタイプでもあります。` with `望むタイプでもあります。試行の結果を共有します。` (fits A's fourth
   line), save, reopen `restored`.
3. The PR #70 54-character growth (`SHORT`) must refuse: `paragraphs exceed all explicitly confirmed shared regions`;
   no PDF/sidecar is published.
4. Re-observe page 9 of the **saved** revision; propose B as a new owner with `next_page`: region `page-9`,
   page break A→B with the caller-selected same-page gap 26.4 pt (PR #68 policy, not a page-coordinate
   difference), and a `vacated_continuation` `release-9` (`[56.8, 110, 540, 174]`, first baseline 122 pt) on the
   current page-9 boundary ordinal 0, releasing only B. Confirming the same rectangle as empty must refuse.
   Accepting the proposal on the original PDF must refuse as a stale revision.
5. Accept; verify A's text/styles/slot binding/source-output identity are unchanged; extension no-op must be
   whole-document pixel-identical in MuPDF (144 dpi) and Poppler (1600 px), with source code/GID/font identity.
6. The same growth must then succeed: A continues on page 9 at 122 pt in `release-9`, B moves below it, the
   Transaction witness names B's removal. Growth to PR #69's `LONG` must still refuse (no further capacity).
7. Second edit of B, shortening (continuation dormant), regrowth (same generated slot), final no-op; each save
   reopens `restored`; foreign text/drawings/images/links/annotations and region-exterior pixels unchanged;
   original SHA unchanged; final exterior Poppler comparison zero.

Run, once the host is reachable:

```bash
.venv/bin/python -m evaluations.real_japanese.next_page_extension \
  --output tmp/next-page/validation --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The runner asserts every condition above and writes the PASS verdict only if all of them hold.
It was imported successfully but **has not executed against the real PDF**.

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

## Focused tests

Linux, Python 3.13, final production code; **172 distinct focused cases passed, zero failures**:

- New `test_next_page_extension.py`: **10 passed, 368.38 s** (lifecycle + nine refusals), then the three-page
  generated-identity case **1 passed, 40.60 s** after its fixture fix (an unselected `FIXED CONTENT` line between
  the pages was correctly refused by the seam check; the shared `make_source` helper gained an optional
  `fixed_pages` argument whose default is unchanged).
- Cross-page owned flow, page-break reflow, original-source extension, restored same-page extension and
  vacated-space (all importers of the changed modules/helper): **51 passed, 1145.82 s** (run concurrently with the next batch).
- Source ownership, source-adjacency continuation/flow, flow Transaction, Transaction, shared flow and owned-body
  reflow: **110 passed, 319.93 s**.

Full suite: **not run** (task instruction and `CLAUDE.md`). Real-PDF evaluation: **not run** (source host refused).
