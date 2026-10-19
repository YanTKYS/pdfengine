# Osaka administrative body editing — REFUSED

Measured 2026-10-19 on main `613d04feb41327b70a79cba74144237a784a73bc`
(PR #76 merged), branch `validate-osaka-body-edit`. The starting tree was clean.
No production changes, successful saves, no-op saves, tests or PR creation.
The existing three-page judgment evaluation was not rerun.

## Sources and bounded inspection

Both registered originals already existed locally; SHA-256 matched before and
after inspection. No download, conversion or font substitution was performed.
Each PDF has one page, so inspection covers **two files / one page each**.

| Source | Original SHA-256 |
| --- | --- |
| `word_osaka_guideline` | `9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe` |
| `word_osaka_fire_notice` | `02036efa843b1cbf18e8454583e9a160ed7d635e9d054fb639d6ff1f96ab3afa` |

URLs and paths remain those in `evaluations/sources.json`. The first document is
a Word LTSC notice about an infection-surveillance guideline. The fallback is a
notice about infection measures following a forest fire. Both have ordinary
related body paragraphs followed by other body paragraphs, not merely headings.

## B1 proposals

The normal `propose_page_flow(source, 1, include_embedded_fonts=True)` call
refuses both full pages as `multiple-columns`: header/address material overlaps
the main column vertically (`p1-l1` and `p1-l4`, respectively).

One explicit body-only selection was then tried per document, using the same
production proposal API. No width, paragraph-start, gap, region, tracking,
baseline, font-file or provider overrides were supplied. **These manual line-ID
selections are additional caller declarations** and cannot establish intent-only
PASS even if accepted. Both still refuse before acceptance/reproduction:

| Source | Selected body | Observed characters / physical lines | Proposal time: default / body |
| --- | --- | --- | --- |
| guideline | `p1-l14`–`p1-l16`: 「標記につきまして…」 then 「つきましては…」 | 50 / 2, then 43 / 1 | 0.157 / 0.762 s |
| fire notice | `p1-l13`–`p1-l18`: 「標記災害に際して…」「なお…」「また…」 | 76 / 2, 67 / 2, 71 / 2 | 0.118 / 0.620 s |

Character counts above include observed trailing spaces; they are not approved
logical paragraph snapshots. Guideline baselines are
`[346.1299743652344,364.1299743652344]`, then `382.1299743652344` pt
(approximately 18 pt pitch). Fire-notice baselines are
`[284.57000732421875,304.3699951171875]`,
`[324.16998291015625,343.969970703125]`,
`[363.7699890136719,383.5699768066406]` (approximately 19.8 pt pitch).
No usable layout region or reproduction proof is produced.

## Direct refusal and provenance

Both selected proposals report:

- `unsupported-for-persistent-flow`: `tagged-page: source output: unsupported
  marked structure: paragraph needs one tree-backed owner`.
- `paragraph-evidence`: `selected style spans have different non-inline graphics
  state` for each proposed paragraph.

The narrow persistent tagged path in `marked_content.paragraph_structure`
requires every selected marked scope to resolve to one tree-backed owner. The
selected guideline body spans eight distinct owner references; the fire-notice
body spans six. B1 specifically refuses the first body paragraph's owner proof.
`SourceParagraph` then retains marked-content state in its exact paint-state
comparison because structural equivalence was not proved; differing marked
scopes cause the second refusal. Removing marked-content entries diagnostically
leaves one common `other` state in each selection. This is **not** evidence that
the marked scopes may safely be discarded or merged.

Both documents expose embedded Japanese TrueType / `Identity-H` resources.
The selected 93 and 214 source glyphs, respectively, all have unique native-code
matches; neither selected body has ambiguous source-order matches or interpreter
errors. Thus the observed body refusal is tagged ownership, not the older `/H`
CMap problem. A court-specific whole-page code-audit helper additionally stops
on an ambiguous original-code match in the fire notice; selected-body mapping
was checked separately, and remaining page mappings were not investigated.

## Outcome and stop boundary

**REFUSED** under `docs/acrobat-critical-path.md`: an existing production path
explicitly fails closed. This is neither PASS nor BOUNDED editing success.
`accept_page_flow_report` is not called on a refused proposal. Reproduction is
`not-run`; replacement text, capacity/wrapping, follower movement and output
font/foreign-content/raster audits are **unvalidated**. No PDF or sidecar is
published; output hashes and after-edit character/line counts do not exist.
Original hashes remain unchanged. MuPDF/Poppler no-op comparison is not run
because no edit was accepted or saved.

Progress requires a bounded tagged-source ownership design supporting these
multi-owner paragraph envelopes while preserving their structure/marked state.
Changing line width or supplying another font does not address this proof.
No such design change or guard bypass is attempted here. Qualification evidence
is retained in `tmp/osaka-body-qualification/`: four proposal JSON files and
`source-diagnostics.json`. The timings above measure the four proposal calls,
not a fabricated end-to-end editing runtime. Stop after the second candidate;
no additional PDF search or PR is performed.
