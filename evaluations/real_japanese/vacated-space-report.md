# Joint-owner vacated-space reflow

**REAL-WORLD VACATED-SPACE REFLOW VALIDATED — PASS**

Source: the unmodified ten-page **LibreOfficeへの移行** public migration paper.
URL: https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf
SHA-256: `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`.
Starting main: `aff104134c41db7f00b85f8417234344303d442b`.

## Selected content and capacity

A is the complete innovator paragraph on page 8, observed lines p8-l24–p8-l27 (164 characters).
Page 9 contains the following adopter-category explanations and communication-strategy conclusion.
All seven complete paragraphs, p9-l1–p9-l23, are explicitly accepted as B–H, each with its own source slot.
This textual relationship is caller declared, not inferred from proximity. Page 8's other text, page 10's new
section, logos, drawings, images and other page content remain foreign.

The earlier B-only selection cannot move far enough: the next unselected paragraph begins at baseline 209.3 pt.
Here B–H are explicitly owned together; their original follows gaps and individual source adjacency are retained.
The last selected paragraph originally ends at baseline 635.5 pt, leaving bounded room below the owned text.

A's conditional page-9 capacity starts at baseline 122 pt, inside bounds `[56.799999,110,540,174]`.
Unlike the earlier empty prefix y=110–130, it overlaps B's **original** glyph positions at baselines 142 and
162.400024 pt. Ordinary empty-destination confirmation refuses these bounds. Only B (`slot-1`) is named as
releasing source owner; other accepted paragraphs do not automatically confer release authority.
The same observed page-program boundary (ordinal 0, after `w`, before `q`) supplies the independent graphics-state
and paint-order authority. CTM, clip and page-boundary checks remain unchanged.

Joint acceptance reproduces **all 27 original lines and glyph positions**. Paragraph character counts are
A=164, B=146, C=147, D=196, E=147, F=166, G=107, H=123. The source page-9 region remains the B1-derived
`[56.799999,108.200012,542.164495,781.299988]`; it is not enlarged for this edit. Original B→C through G→H
baseline gaps are 26.399963, 28.200012, 28.200012, 26.399994, 26.499969 and 26.5 pt.
A→B uses the explicitly accepted 26.4 pt same-page policy and B's original region floor, not a cross-page y difference.

## Production contract

`propose_owned_flow(..., vacated_continuations=[...])` records the caller's selected region, its position before
B's original region, the exact releasing paragraph IDs and insertion confirmation arguments. Explicit acceptance:

1. Rebuilds each B1 page proposal, verifies fonts and source binding, and confirms every individual source owner.
2. Binds the conditional destination to immutable source-owner identities and the specific same-page source region.
3. Revalidates each owner's current editable binding and source-output witness before exempting **only those glyphs**
   from the capacity's obstacle check. Mixed paints containing foreign glyphs remain protected.
4. Proves the no-edit allocation and glyph positions are unchanged. Overlap is permitted only between this conditional
   capacity and the region of its named source owner; fixed protected content and other regions remain excluded.

The existing T2 allocator, source-page-break floor, follows and paragraph writer calculate the final placements.
Every creation or rewrite of the conditional continuation carries a current-revision owner witness into its
`ParagraphPlan`. `PageTransaction.check` requires the named source owner to have an independent paragraph plan,
matching source-output identity, complete consumption of its old glyphs and removal mutations in **that same transaction**.
It checks final occupied rectangles before the existing transaction-wide collision, removal, raster and readback checks.
A writer report records the PDF revision, authority digest, used bounds, old glyph IDs, removal mutations and the
owner's final bounds. Missing removal or overlapping final placement refuses before publication.

On reopen, original source slots retain current-revision source-output ownership; the generated continuation retains
its own block markers, destination witness and font records. It never becomes the owner of B's original glyphs.
Shortening rewrites/removes its paint through the same writer; regrowth reuses that generated slot.

No alternative writer or layout engine was added. Ordinary empty continuation behavior and PR #67's provider-font
subset fixture are unchanged. New text uses the explicitly approved BIZ UDMincho provider, SHA-256
`468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`, face 0; source appearance equivalence is not claimed.
No real PDF or third-party font binary is committed.

## Measured first edit

Replace `望むタイプでもあります。` (12 characters) with:

> 望むタイプでもあります。試行の結果を共有し、利用者の疑問や改善の提案を記録することで、次の移行に役立てます。担当者は実際の文書を使って操作を試し、変更の理由と利用方法を説明します。得られた意見を整理し、部門間で知識を共有することで、組織全体の移行を支援します。

The replacement is 130 characters; A grows **164 → 282 characters, four → six lines**. Four lines / 199 characters
remain on page 8. The remaining 83 characters occupy two lines on page 9, at baselines **122 and 142.400024 pt**.
The latter line uses B's original space: its planned ink intersects **39 original B glyph bounds**; actual saved A
glyph bounds intersect 40. This exceeds the former empty prefix's one-line capacity. Final A/B glyph bounds are disjoint.

B moves from **142 to 168.800024 pt**, a **26.800024 pt** translation. Its original three lines, all 146 glyphs,
font resources/programs/codes/GIDs, first-line indent and unequal leading are retained in this first edit.
C–H move by the same amount and preserve their original follows gaps; H ends at baseline **662.300024 pt**, inside
its unchanged B1 region (bottom 781.299988). A→B's accepted gap remains **26.4 pt**.

The Transaction schedules source removal/rewrite H→G→F→E→D→C→B before creating A's continuation, with all byte
mutations published together. Its conditional certificate names B's 146 old glyphs, their removal mutations,
B's final glyph rectangles and A's actual used rectangles. First edit: **1184 retained / 130 provided glyphs**.
Maximum glyph-origin error is **0.000036621094 pt**; unchanged-B translation error is **0.000024414063 pt**,
within existing geometry checks. No tolerance was changed. No-op from the original is whole-document pixel-identical.
The first edited revision reopens `restored`; all ten pages preserve foreign content and outside-region MuPDF differences are zero.
Page-9 before/after crops were visually inspected: A continues above B, the inherited indent and paragraph spacing
remain clear, and provider appearance differences on added text remain explicit.

The reopened second edit changes B's `変化を肯定的` → `新しい変化を肯定的` (6 → 9 characters), keeping three lines.
Shortening reverses both edits, removes all continued A glyphs and returns B's first baseline to 142 pt.
Reversing text restores Unicode, not deleted historical font codes or leading: B's edited last-line edge uses the
existing 20.400024 pt minimum pitch instead of the original 20.5 pt edge. Consequently C–H are 0.099976 pt higher
after that reversal; their follows gaps remain exactly the accepted values. This is the existing source-adjacency
rule for an edited boundary, not a new tolerance or an attempt to recover unretained source glyphs.

## Completed save/reopen lifecycle

The original no-op preserves all **1196 glyphs**, including codes, font resources/programs and GIDs,
with **0.0 pt** origin error and a pixel-identical whole-document raster. After that baseline, all five
edited lifecycle revisions reopen `restored`:

| Revision | B first-baseline shift (pt) | Retained / provided glyphs | Maximum origin error (pt) | Exterior MuPDF changed pixels |
| --- | ---: | ---: | ---: | ---: |
| Grow A | 26.800024 | 1184 / 130 | 0.000036621094 | 0 |
| Second edit in B | 26.800024 | 1308 / 9 | 0.000036621094 | 0 |
| Shorten A and B | 0 | 1178 / 18 | 0.000021972656 | 0 |
| Regrow A | 26.800024 | 1184 / 130 | 0.000036621094 | 0 |
| Final no-op | 26.800024 | 1314 / 0 | 0.000036621094 | 0 |

Shortening leaves the continuation slot dormant with no glyphs; regrowth reuses the same slot, with no residual
or double drawing. The final no-op is pixel-identical to the preceding revision. Final exterior Poppler comparisons
also report **0 changed pixels on both pages 8 and 9**. The original PDF SHA-256 remains unchanged.

A further over-capacity edit (three copies of the long replacement) refuses with
`final allocation needs a missing paragraph destination slot; new continuation is not authorized`.
Neither output PDF nor sidecar is published. Independent identity and occupied-space intersection audits are
recorded alongside the lifecycle summary as `original-noop-identity.json` and `released-space.json`.

## Reproduce

Keep the original PDF at `evaluations/realpdf/corpus/lo_migration_ja.pdf` and the approved font available locally:

```sh
.venv/bin/python -m evaluations.real_japanese.vacated_space_edit \
  --output tmp/vacated/validation --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The output directory must not exist. Review, proposal, acceptance, initial state, writer reports, saved revisions,
independent renders and `summary.json` are untracked artifacts. Each revision audits Unicode with MuPDF and pypdf,
glyph origins/bounds/codes/GIDs/font programs, provider outlines/advances, foreign content on all ten pages, region-exterior
MuPDF pixels, PDF structure and restored reopen. The last revision also receives an exterior Poppler comparison.

## Validation scope

Focused tests cover the new lifecycle and refusal cases, existing continuation/shared flow/source ownership,
source adjacency, independent page-break reflow, boundary insertion, tagged guards and Transaction rollback.
The related regression run passed **234 tests in 153.67 s**, including eight new cases. The additional clip/CTM
selection passed **8 tests in 24.90 s**. Commands:

```sh
.venv/bin/python -m pytest -q -n 3 tests/test_vacated_space.py tests/test_continuation.py \
  tests/test_shared_flow.py tests/test_source_ownership.py tests/test_page_break_reflow.py \
  tests/test_cross_page_owned_flow.py tests/test_transaction.py tests/test_flow_transaction.py \
  tests/test_source_adjacency_continuation.py tests/test_boundary_destination.py tests/test_tagged_page_proposal.py
.venv/bin/python -m pytest -q -n 2 \
  tests/test_clip_boundary.py::test_the_destination_must_lie_inside_the_certified_rectangle \
  tests/test_clip_boundary.py::test_paint_the_clip_hides_is_still_an_obstacle \
  tests/test_clip_boundary.py::test_a_re_edit_whose_ink_reaches_the_clip_edge_is_refused \
  tests/test_clip_boundary.py::test_a_changed_clip_is_not_the_same_authority \
  tests/test_ctm_compensation.py::test_lifecycle_keeps_the_compensated_block_through_edits_and_noops \
  tests/test_ctm_compensation.py::test_a_changed_ctm_or_inverse_is_not_the_same_authority
```

**Full suite is intentionally not run**, as requested and according to `CLAUDE.md`.

## Remaining bounds

This is explicit, fixed-capacity conditional ownership. It neither discovers related paragraphs automatically nor
adds pages or enlarges capacity when full. Only selected current source owners may release space; unselected text
and nontext paint remain obstacles. Tagged source ownership crossing independent continuation regions remains
unsupported. Cyclic source occupancy dependencies and insufficient final capacity still refuse.
