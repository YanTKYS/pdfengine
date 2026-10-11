# Whole related body paragraphs: reflow, push-down and continuation

Base main `2667fe173538b2244204bc27fd16560e3dc2c0bd` (PR #63 merged), Linux / Python 3.12,
2026-10-11. [Runner](owned_body_edit.py) / [measurements](owned-body-summary.json).
No third-party PDF/font binaries or raster artifacts are committed.

**REAL-WORLD OWNED BODY REFLOW AND CONTINUATION VALIDATED — PASS** for the bounded,
explicitly accepted two-paragraph case below. All five saved revisions reopen `restored`.

## Source and ownership decision

Unmodified **LibreOfficeへの移行**, Japanese migration whitepaper:
[public PDF](https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf).
The re-fetched bytes match the existing corpus SHA:
`13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`.
There are 10 original pages; none is added or preprocessed.

Kyoto's three-line note was considered as a whole. The following text is a different section, and its
preceding material is a shipping list, so it is not silently promoted into a joint body flow. Okinawa's
numbered body is already covered by B4 and ends on the last page at its fixed footer. The existing
LibreOffice corpus supplies related explanatory body paragraphs with first-line indents, unequal leading,
mixed source subsets and a meaningful page-order continuation.

The caller selects **all six lines p9-l18 through p9-l23**:

- P1 (107 characters, 3 lines): the paragraph explaining laggards/conservative traditionalists.
- P2 (123 characters, 3 lines): the concluding paragraph beginning “変化管理コミュニケーション計画の作成にこれらの戦略を利用すると”.

Both belong to the “集中的コミュニケーション戦略” section, which starts on page 8. P2 explicitly refers
back to the strategies discussed in P1 and the preceding category explanations. Both are complete indented
paragraphs ending in `。`, use 10.5 pt Japanese text (P1 also has 12 pt Latin `16%`), and share the same body
column and unobstructed text area. There is no intervening heading, table, annotation or footnote.
The earlier category paragraphs remain foreign. The next independent section begins on page 10.

This semantic decision is made by the caller after observing the document, **not inferred from proximity**.
B1 records the explicit line selection, reconstructs disjoint source glyph selections and styles, proposes
the paragraph relationship, and revalidates everything on explicit accept. Six source lines and every glyph
must reproduce before either paragraph becomes editable. The PDF is untagged; this is not a tree-ownership
claim. Existing B2 tagged guards remain unchanged.

## Actual blockers and production changes

The existing joint B1/T2 ownership and allocator can represent these two paragraphs, but no-edit reproduction
previously rejected the source. Two measured causes prevented acceptance:

1. B1 selected one median minimum line height. P1's actual gaps are **21.600037 and 20.399963 pt**;
   P2's are **20.400024 and 20.5 pt**. Replacing each pair with a constant changes source baselines.
2. B1 treated a MuPDF trace glyph rectangle width as the PDF text pen advance. The source uses rounded
   `/W` entries. For P1's first line, the old inferred extent was 471.432785 pt versus the interpreted
   pen extent 471.430488 pt, a **0.002297 pt** difference. Every glyph origin can be right while these
   two different metrics disagree. Increasing the tolerance would hide the wrong comparison.

Production now reads zero-Tc/Tw horizontal advances from the interpreted source operators, bound to observed
occurrences. These determine B1 pen ends and wrap-width evidence; trace boxes remain bounds evidence.
Source font metric qualification is unchanged.

`ParagraphShaper.source_line_gap` recognizes two **complete unchanged consecutive source lines** by the
identities of their retained occurrences. It obtains their baseline difference from the current PDF, not a
sidecar array. B1 sets the source-adjacency paragraph's minimum line height to its smallest observed pitch
(rather than a median that exceeds some source edges). The existing attributed layout accepts a witnessed
edge while retaining both that minimum and the hard preceding-descent plus following-ascent floor.
Changed/replaced/wrapped lines and region transitions use the ordinary confirmed line-height policy.
Shared allocation, local planning and actual writing use the same
witness. This extends the accepted source-adjacency path; no new engine, writer or unrestricted gap policy
is introduced. New glyph ink, region bounds and all foreign content checks still run.

All source subsets lack a usable Unicode cmap. Existing B3 explicitly selects face 0 of OFL 1.1
[BIZUDMincho-Regular.ttf](https://raw.githubusercontent.com/google/fonts/main/ofl/bizudmincho/BIZUDMincho-Regular.ttf),
SHA `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`, to supply new glyphs.
Original retained glyphs use their actual source codes/resources/GIDs/programs. The substitute has a different,
heavier appearance; equal font names or metrics are never claimed. `抵抗します。` crosses source subset styles,
so its replacement explicitly names confirmed logical `style-1` (10.5 pt) in addition to the B3 provider choice.

## Regions and concrete edit

R1 is the unchanged B1 proposal, not an artificially reduced capacity:
page 9, `[56.799999, 501.071472, 542.274242, 781.299988]`, width 485.474243 pt.
Both paragraph starts have **10.500004 pt indentation** (x 67.300003); continuation lines use x 56.799999.
The source paragraph relation is a **26.5 pt baseline gap**.

The caller explicitly reviews page-10 R2:
`[56.799999, 110, 542.274242, 137]`, first baseline 122 pt, same column/width.
It lies below the protected license logo and above the next independent heading. A candidate extending to
145 pt is correctly rejected because the heading's stroke-text paint envelope starts near 141.0678 pt.
The accepted existing boundary is `w → boundary → q`, ordinal 0, q depth 0, identity CTM, no inherited clip.
Existing geometry review → confirmation request → destination confirmation → flow attachment is used.
The destination belongs **only to P2**, so P1 has no authority to occupy it.

Replace `抵抗します。` (6 characters) with 373 characters, preserving that sentence and adding a concrete
plan for listening to users, training, support and communicating progress. P1 becomes **474 characters**.
The full before/after text and exact line breaks are in the measured summary and executable runner.

First saved revision:

| Fragment | Before | After |
|---|---|---|
| P1, page 9 | 3 lines at 526.099976 / 547.700012 / 568.099976 | 11 lines, first two baselines retained, then 20.399963 pt pitch through 731.299683 |
| P2, page 9 | 3 lines at 594.599976 / 615 / 635.5 | first 99 characters in 2 lines at 757.799683 / 778.199707 |
| P2, page 10 | absent | remaining 24 characters at baseline 122 |

Thus the **existing follower moves 163.199707 pt**; its first two source lines retain their **20.400024 pt**
gap and their original codes/font programs. The paragraph gap stays 26.5 pt. The complete P2 text is unchanged
on this first save. The continued text is:

```text
いが作られる前に、不信と批評が蔓延するからです。
```

This text used to occupy page 9 as part of P2. It is not a newly authored independent paragraph or a rerun
of empty-space insertion alone. PR #63's current-slot rule generates cross-slot glyphs using the approved
provider; after saving they become current owned source glyphs at their new location.
The first save retains 101 original P1 glyphs and all 99 P2 glyphs that stay on page 9. It provides 373
replacement glyphs in P1 and 24 continued glyphs on page 10; those 24 do not claim the original font identity.

## Verification and lifecycle

The runner checks each saved revision against the untouched original for all 10 pages: independent pypdf
Unicode, MuPDF glyph Unicode/positions/bounds, exact retained code/resource/GID/program identity, provider
outlines/advances, foreign text/drawings/images/links/annotations, region-exterior 144 dpi raster, valid
operator nesting, PDF version/page count and original SHA. Unedited pages are compared in full.
Poppler before/after images are also rendered, compared outside both regions and visually inspected.
The maximum saved-glyph origin error over all five revisions is **0.000015259 pt**. The first save's 99
retained follower glyphs undergo an exact common translation (measured error **0 pt**). All 10 pages have
zero region-exterior MuPDF pixel changes; Poppler also has zero exterior changes on pages 9 and 10.

The lifecycle grows P1, edits `不信と批評` → `不信や誤解と批評` inside the saved generated P2 slot,
shortens P1 and undoes P2's edit, regrows P1, then saves without edits. Shortening must leave the destination
dormant and page 10 raster-identical to the original; regrowth must reuse the slot; no-op must be pixel-identical
to its input. Exact per-stage measurements and outcomes are recorded in the summary.

| Save | P1 page-9 lines | P2 page-9/page-10 lines | P2 displacement | Reopen |
|---|---:|---:|---:|---|
| Grow | 11 | 2 / 1 | 163.199707 pt | restored |
| Edit continued P2 | 11 | 2 / 1 | 163.199707 pt | restored |
| Shorten | 3 | 3 / 0 | 0 pt | restored |
| Regrow | 11 | 2 / 1 | 163.199707 pt | restored |
| No-op | 11 | 2 / 1 | 163.199707 pt | restored |

Shortening preserves original Unicode, but a replaced line uses the confirmed minimum leading and substitute;
it does not promise to recover the original source font for already replaced glyphs.
P1 returns to its original three baselines and P2 starts at its original 594.599976 pt baseline. P2's last
baseline becomes 635.400024 pt (original 635.5), because the former continuation line is now recomposed using
the 20.400024 pt minimum. Untouched complete-line relationships retain their witnessed gaps; the paragraph
gap remains exactly 26.5 pt.

## Safety, regression and remaining boundary

`tests/test_owned_body_reflow.py` reproduces quantized PDF widths and unequal leading in a minimal two-paragraph
fixture. It tests source no-op pixel identity, grow/shorten/regrow/reopen, actual follower displacement and gap
retention. It also refuses unselected-follower overflow, expansion into a fixed footer, and nonconsecutive
selection. Existing source-adjacency, B4, continuation and tagged tests remain part of validation.
The attributed-layout regression also proves that witnessed gaps cannot bypass the confirmed minimum,
font extents or bottom containment.

This result covers **two whole paragraphs initially owned on one page**, with an existing follower pushed
down and partly continued onto another explicitly confirmed region. Joining already-occupied source regions
on different pages is not implemented. It also does not move independent headings, footnotes, tables or logos,
create pages, rewrite tagged trees, or authorize P1 to use P2's continuation. Further growth safely refuses.
Doubling the long replacement refuses with `final allocation needs a missing paragraph destination slot;
new continuation is not authorized`, producing neither output PDF nor state file.

Reproduce with the corpus PDF and matching font available locally (fresh output directory):

```sh
.venv/bin/python -m evaluations.real_japanese.owned_body_edit \
  --output tmp/owned-body/validated-run2 --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

Final-HEAD tests:

```sh
.venv/bin/python -m pytest -q -n 4 tests/test_owned_body_reflow.py tests/test_page_proposal.py \
  tests/test_tagged_page_proposal.py tests/test_rich_layout.py tests/test_source_adjacency_flow.py tests/test_body_reflow.py \
  tests/test_source_adjacency_continuation.py tests/test_shared_flow.py tests/test_continuation.py \
  tests/test_continuation_caller_to_shared_flow.py tests/test_source_ownership.py
.venv/bin/python -m pytest -q -n 4
```

Exact tested commit and final-HEAD counts are recorded in the pull request.
