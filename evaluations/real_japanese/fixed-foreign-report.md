# Explicit fixed margins at saved-flow page seams

**REAL-WORLD THREE-PAGE SEQUENTIAL REFLOW — NOT VALIDATED.** No real three-page
edited history was saved. This change delivers a bounded production capability:
explicitly witnessed, protected fixed margins can remain foreign without blocking
the next-page seam. The three-page editing evidence below uses a synthetic fixture;
it must not be presented as real-PDF validation.

Starting main: `9105e1ff617c2014c81953b0e9bb182b83a0e375` (PR #74 merged).
Existing PR #71–#73 LibreOffice measurements remain unchanged.

## Production improvement and scope

Previously, an unowned footer in the selected column below the final body line
always blocked `_page_boundary`, even when it was independently protected and
outside every editable region. The default request still refuses. A caller can now
observe explicitly reviewed whole header/footer lines with
`pdfeditor.post_edit_extension.inspect_fixed_foreign`, then include those witnesses
in `next_page.fixed_foreign` on the normal `propose_owned_flow_extension` path.

The proof binds the current PDF SHA, page-observation SHA, exact line and glyph IDs,
rendered GIDs/font/origin/bounds, and interpreted original character codes/font
resources. The full PDF SHA binds the original font programs and graphics state as
well. Review and acceptance independently re-observe the proof; resealing an altered
proposal cannot bless stale or altered observations.

The caller declares a body rectangle containing the central 75% of the page height.
Fixed ink must lie wholly above or below it in the corresponding outer eighth.
Every existing source/destination and proposed source region must fit inside the
declared body. This deliberately narrow geometry is a capability limit, not an
automatic classification of page numbers. The caller must verify the document's
meaning. Interior text and headings, partially owned lines, duplicates, unnecessary
proofs, unsupported original-code mappings and missing protected regions refuse.

Fixed glyphs remain unowned, protected and untouched. They grant no free capacity,
destination, release owner or font ownership. Existing checks still reject proposed
source/destination regions entering fixed paint. The normal joint reproduction,
source removal and same-Transaction release proof, gap/floor policy, destination
validation, atomic publication and readback paths are reused unchanged.

## Real document selection

The existing corpus was reviewed first. LibreOffice migration page 10 remains a
different section. The Wakayama cautions do not continue onto page 6 and were **not
retried** as a three-page candidate. Osaka's two additional notices each have one
page. Parliamentary reference 78 uses legacy `90ms-RKSJ-H` fonts in the considered
passage; reference 73-2 is scanned in the inspected pages. Reference 104 has supported
Identity-H body fonts, but the considered pages 46–47 contain a tiled quotation
border and native underline paint. Its page-46 B1 region collided with five image
tiles. These decorations were not treated as vacant space or implicitly adopted.
The initial reference-104 provider override also lacked qualification for new text;
that probe-choice error is not a production defect or evidence of a successful edit.

A genuine related three-page body was found in Eric S. Raymond's *The Cathedral and
the Bazaar*, Japanese translation by Hiroo Yamagata, section 10「フリーソフトの社会的な意義」.
Text extraction and rendered pages 22–24 were inspected. Both seams are paragraph
boundaries. The heading on page 22 precedes the selected paragraph and stays foreign;
there is no intervening heading, footnote or different section at either seam.

- Original URL: <https://cruel.org/freeware/cathedral.pdf>
- Local path: `evaluations/realpdf/corpus/cathedral_ja.pdf`
- SHA-256: `4a4fc57797d000eef36858db9c985133a946f383cee031565332ef98a029014f`
- 40 pages; original PDF bytes were used unchanged.
- Candidate source on page 22: `p22-l26`–`p22-l29`, beginning “Carl Harrisとかれのかつてのpopclientもそうだったし”
  and ending “中で、ソフトがどう発展するかという話。”
- Candidate source on page 23: `p23-l1`–`p23-l33`, seven complete paragraphs, beginning
  “『人月の神話』でフレッド・ブルックスは” and ending “最初の人間だったわけだ。”
- Candidate source on page 24: `p24-l1`–`p24-l4`, the first complete paragraph, beginning
  “安いインターネットは、Linuxモデルの発展にとっての必要条件”. Later text stays foreign.

These are **candidate selections, not accepted owners**. Page-23/24 observations
are original-document qualification evidence, not saved-revision adoptions. No
claim of sequential expansion follows from observing three original pages.

| Page | Selected characters / glyphs | Lines | First / last baseline (pt) | Owner / after-edit result |
| --- | ---: | ---: | --- | --- |
| 22 | 168 / 168 | 4 | 663.247681 / 722.152893 | first B1 owner REFUSE; no edited result |
| 23 | 1,336 / 1,336 | 33 | 123.253830 / 751.616333 | original observations only; not adopted |
| 24 | 147 / 147 | 4 | 123.253830 / 182.159058 | original observations only; not adopted |

All three explicitly observed footers have baseline **780.0723266601562 pt** and
bounds `[292.17718505859375, 771.2410888671875, 303.08636474609375, 782.1502685546875]`.
They map exactly to `/F14` source bytes `32 32`, `32 33`, `32 34`, respectively.
Their line IDs are `p22-l30`, `p23-l34`, `p24-l33`; the reviewed body rectangle is
`[0, 0, 595, 760]`. The new inspector successfully produces these current-revision
witnesses despite the unsupported Japanese body font. This is observation only,
not a seam approval: first-owner qualification has already blocked editing.

## Reproducible qualification

```bash
PYTHONUTF8=1 .venv/bin/python -m evaluations.real_japanese.fixed_foreign_qualification \
  --output tmp/fixed-foreign/validation-01 \
  --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The runner requires a new output directory, verifies the original hash and pinned
BIZ UDMincho hash `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`,
records each selected line/glyph/baseline and explicit footer witness, then attempts
public B1 proposal and acceptance. The provider is not used to rewrite the source
PDF or repair unsupported source codes. An unexpected future B1 success stops the
probe and requires an actual edit evaluation instead of silently declaring PASS.

The initial owner refuses with:

```text
unsupported-for-persistent-flow:
  backend cannot establish unique text-operator provenance for every selected glyph
paragraph-evidence: P1 (p22-l26):
  backend cannot establish unique text-operator provenance for every selected glyph
```

The Japanese source fonts are non-embedded Type0 Ryumin-Light-H/GothicBBB-Medium-H
with `/H` encoding. The current content interpreter supports Identity-H CID code
mapping, not this legacy CMap. This is a source-font qualification limitation,
not a network, provider-file, encoding-of-the-Python-process or memory failure.
Broader CMap support and synthetic rewriting of this PDF were not added as exceptions.

No owner, minimum gap, baseline floor, continuation destination or release authority
was accepted. There are no after-edit character counts/baselines, restored saves,
no-op or exterior raster measurements for this real PDF. Refusal occurs before any
edit/publication call; this is not a measured atomic-save failure. Publication
refusals remain covered by focused production regressions.

## Validation record

- Real qualification: **2026-10-15 08:57:25.906 UTC**, **1.979309 s**, exit **0**.
  Exit 0 means the expected qualification refusal and original-SHA assertion were
  reproduced; it does **not** mean real three-page reflow passed.
- Environment: Linux x86_64 (kernel 6.18.44, glibc 2.41), Python **3.12.14**,
  PyMuPDF **1.27.2.3**, MuPDF **1.27.2**, Poppler **26.05.0**.
- [Compact measured evidence](fixed-foreign-summary.json). Full observations,
  witnesses and the refused B1 proposal are in the fresh ignored output directory.
- Focused batch: **44 passed in 533.69 s**. This batch included the initial lifecycle
  assertions; the strengthened lifecycle is separately rerun below.
- Final lifecycle batch: **2 passed in 140.73 s**, including the strengthened
  fixed-margin lifecycle (**76.31 s** call) and the unchanged existing three-page
  sequential lifecycle (**61.08 s** call). One node overlaps the first batch;
  these are **45 distinct tests**, not 46 distinct tests. No production changes
  followed the runs. `git diff --check` also passes.

```bash
.venv/bin/python -m pytest -q \
  tests/test_fixed_foreign_extension.py \
  tests/test_next_page_extension.py \
  tests/test_post_edit_extension.py --durations=5 --tb=short

.venv/bin/python -m pytest -q \
  tests/test_fixed_foreign_extension.py::test_two_current_seams_preserve_fixed_paint_through_saved_revisions \
  tests/test_sequential_extension.py::test_three_page_sequential_next_page_extensions \
  --durations=5 --tb=short
```

The evaluator and test batches ran **serially**. Full suite and the earlier 917-test
batch were not run. No prior lost-session output is counted as a completed result.

## Synthetic editing measurements — not real-PDF evidence

The new regression uses three native Japanese fixture pages with embedded source
fonts, a fixed header and a fixed footer. Both extensions are observed from saved
edited revisions. Both growth requests first refuse without PDF/sidecar publication,
then the identical requests succeed after their respective explicit extensions.

| Fixture operation | Actual layout / result |
| --- | --- |
| Extend page 2, then grow page-1 `P1` | continuation baseline **62 pt** on page 2; owner `B` moves **80 → 88.5 pt** |
| Extend page 3 from that saved revision, then grow `B` | independent continuation baseline **62 pt** on page 3; owner `C` moves **80 → 88.5 pt** |
| Shorten `B` | its page-3 continuation becomes empty; `C` returns to **80 pt** |
| Regrow `B` | same generated identity paints at **62 pt** again; `C` returns to **88.5 pt** |
| Shorten/regrow `P1` | page-2 continuation empties and is reused; both continuations are occupied after regrowth |
| Release authority | page-2 destination releases only `B`; page-3 destination releases only `C`; sets never widen |
| No-op after each extension and final no-op | **0 changed pixels**, all three pages, MuPDF and Poppler at 72 dpi |
| All edited-stage region exteriors | **0 changed pixels**, all three pages, MuPDF at 72 dpi |
| Save/reopen | every save is **`restored`** |

Source-output identities, existing generated creation bindings, destination bindings,
font registries and protected regions survive the extensions. Fixed native text's
Unicode/GID/origin/bounds, original code/font resource and embedded font-program
bytes remain equal through all saved edits. The original fixture SHA stays unchanged.
The page-3 continuation includes a same-Transaction release witness naming only `C`.

Negative tests cover default and incomplete margin requests; resealed SHA, page,
line, glyph, code, font and coordinate tampering; stale saved-revision evidence;
interior text; unowned/protected requirements; source/destination collisions; and
a numeric footer lacking exact original-code observations. Existing focused tests
exercise publication refusals and normal next-page/same-page behavior.

Poppler is installed in this measured run; all three independent no-op comparisons
ran. The helper emits a visible NOT RUN warning if Poppler is unavailable elsewhere.
The edited-stage exterior helper measures MuPDF only. Neither these fixture checks
nor real footer observation establishes real-document image/link/annotation preservation
through a three-page edit. That full real-PDF lifecycle remains the blocker.
