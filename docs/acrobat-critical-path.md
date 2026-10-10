# Acrobat-like editing — current critical path

**Current-state document.** This file holds only the current critical path. Rewrite it whenever the state changes. It
is not a history log; history is in [continuation-checkpoint.md](continuation-checkpoint.md).

- State: starting main `546af051ad7f0739c6ccc5fdee8a24cc97d34495` plus bounded real-body reflow, 2026-10-10.
- **B1 — ordinary-page editable-state bootstrap: COMPLETE** for the scope below.
- **B2 — tagged-PDF persistent editing: COMPLETE for the verified same-owner leaf-P subset.**
  This does not claim support for arbitrary Office/PDF-UA structures.
- Evidence: [B1 tests](../tests/test_page_proposal.py),
  [B2 tests](../tests/test_tagged_page_proposal.py), and
  [implementation/evidence report](tagged-persistent-editing.md).
  The B2 fixture lifecycle ran on Windows. The existing real corpus received
  [read-only structural inspection](../evaluations/tagged/structure-probe.json).
- **FIRST REAL-WORLD JAPANESE BODY TEXT EDITING VALIDATED — PASS** for Okinawa
  procurement page 2, item (7). Two different-length edits use public B1/T2 and
  restore after each save. Irregular Td/Tm gaps are retained as source adjacency,
  not inferred tracking or justification. [Measured body report](../evaluations/real_japanese/body-report.md).
  The one-line result is now extended by B4 below.
- **FIRST REAL-WORLD JAPANESE BODY REFLOW VALIDATED — PASS** for the same Okinawa
  page, explicitly selected items (7) and (8). Item (7) grows from one to two lines;
  item (8) moves down 19.680237 pt, keeping its two lines, 18 pt hanging indent and
  19.679993 pt follows gap. Two saved revisions reopen `restored`; all region-exterior
  pixels remain identical. [Measured reflow report](../evaluations/real_japanese/body-reflow-report.md).
- **FIRST REAL-WORLD JAPANESE PDF EDITING VALIDATED — PASS**, previously bounded to one
  existing Kyoto City PDF heading: `質問に対する回答` on page 1. A caller-selected
  observed line goes through public propose → accept → replace → edit → restored
  reopen → second edit → restored reopen. Both edits change text length.
  [Measured report](real-japanese-persistent-editing.md) / [reproducible runner](../evaluations/real_japanese/evaluate.py).
  The original PDF is unchanged; foreign text, drawings, images, all outside-region
  MuPDF pixels and the entire second page are unchanged. This is a heading result,
  **not a body-paragraph, justified-text, arbitrary-tagged-PDF or Word-export claim**.
- Earlier B1 Linux real-font probe: [scenario_probe.py](../evaluations/critical_path/scenario_probe.py)
  / [probe-results.json](../evaluations/critical_path/probe-results.json). Its S6 refusal describes pre-B2 behavior.

## 1. Summit definition

The summit is this user experience. Everything else serves it.

```
open an ordinary existing PDF (never prepared for pdfengine)
→ select existing body text
→ replace it with text of a different length
→ the paragraph rewraps naturally; following text keeps its relation
→ font, size, position and surrounding layout are kept as far as possible
→ save
→ reopen and keep editing
```

**Status labels used below.** A label always names the path it applies to.

| Label | Meaning |
|---|---|
| **PASS** | A production path does it from user intent only: the PDF, the page, the target text, the replacement, the installed fonts, and one explicit acceptance of a proposal. |
| **BOUNDED** | A production path does it only when the caller hand-writes declarations (selection, width, region, policies, font files). |
| **REFUSED** | A production path exists and an explicit guard refuses this input (fail closed, original unchanged). |
| **UNSUPPORTED** | No production path exists. |

## 2. The end-to-end path that exists today

```
ordinary PDF
→ propose_page_flow(pdf, page, line_ids=...)  optional observed target; read-only evidence + interpretation
→ accept_page_flow(pdf, proposal)          the one explicit act; returns the unchanged pdfengine-shared-flow-2 state
→ replace_in_flow(state, find, replacement)   edit request only; writes nothing
→ edit_shared_flow(...)                    the existing T2 writer: Transaction, ownership, one save, one verification
→ open_shared_flow(...) == restored        → next replace_in_flow / edit_shared_flow
```

The CLI is the same path: `propose-page [--line ID ...]` → `accept-page` → `replace-text` (README).
Target IDs come from `inspect_selection_source`; they must be distinct, consecutive observed lines in page order.
Targeting chooses only the candidate text. Every unselected line and paint stays foreign, including content
elsewhere on a multi-column/table page. All width, region, source, font and reproduction guards still run.

| Stage | Code | What it now does without hand-written input | What still needs the caller |
|---|---|---|---|
| Observe | `backend.extract_page`, `selection.observation_lines`, `inference._line_groups/_paragraphs` | Lines, paragraph candidates, the main column, foreign content (other text, paths, images) | — |
| Propose | `page_proposal._build` | Paragraph selections, column x, available width (§3.1), region bounds, line pitch, first-line indent, follows gaps, logical styles, metric-verified font providers | Review; optionally choose consecutive observed lines |
| Verify the proposal | `page_proposal._reproduce` → `shared_flow.plan_shared_flow(state, {})` | The exact T2 state is built and planned with no edits. It must reproduce every observed line break, baseline, line width and painted glyph origin, or the proposal is refused. | — |
| Accept | `accept_page_flow` → `confirm_story` → `confirm_shared_flow` | Rechecks the binding, recomputes the proposal, applies explicit choices and overrides, and returns the T2 state | One call; a provider choice only when several fonts verify |
| Edit | `replace_in_flow` → `edit_shared_flow` | Finds the unique occurrence across the flow, takes its single logical style, and writes through the unchanged T2 path | Find and replacement text |
| Reopen and continue | `open_shared_flow` | `restored`, or `needs_confirmation` (fail closed) | — |

The semantic layer (T3, `shared-flow-3`) is unchanged. It still admits only `A`, `B`, space and newline, and it is
not on the replacement path.

### 2.1 What the runtime builds automatically for a PDF opened for the first time

For untagged or supported tree-backed tagged horizontal text in one column, either inferred from the page or selected by observed line IDs, whose font is installed with identical metrics, it builds everything
the T2 state needs:

- the paragraphs and their order;
- the width and the region;
- the line pitch, first-line indents and paragraph gaps;
- the logical styles and their font providers.

The caller only accepts. Every value carries an evidence label. None of them is authority until the explicit accept.

## 3. Current proven foundation (do not rebuild)

| Foundation | Where | Proven by |
|---|---|---|
| **B1 front door: evidence-labelled proposal, one explicit acceptance, unchanged T2 state** | `page_proposal.py` | `test_page_proposal.py`; probe S0–S5 `PASS-intent-only` |
| Source-bound selection and snapshots (PDF SHA + page observation hash) | `selection.py`, `attributed.py` | Stale input refused |
| Source proofs before writes: no-op replay, removal, foreign glyph preservation | `paragraph.plan_paragraph_edit` | Every write |
| Attributed HarfBuzz shaping and Japanese wrap | `paragraph.ParagraphShaper`, `rich_layout` | Probe S1–S5 |
| One Transaction: plan, mutate, save once, verify once, identity map | `transaction.py`, `mutation.py` | Every write |
| Shared flow v2: multi-paragraph allocation, push-down by gap, fail-closed reopen, re-edit; optional source-adjacency retention | `shared_flow.py` | Existing flow fixtures and `test_source_adjacency_flow.py` |
| Current-revision source ownership | `source_ownership.py` | Persistent re-edit |
| Semantic authority layers, canonical island writer, decoration paint ownership, L3 publication | `semantic_*.py` | Windows lifecycle validations |

### 3.1 How B1 decides values (the safety argument)

- **Width is never the observed content width.**
  - Observed lines give a lower bound: every observed line must fit.
  - Each trusted wrap gives an upper bound: the line plus the next legal break unit must not fit.
  - A wrap after sentence-final punctuation is not trusted, because it may be a paragraph end.
  - Within those bounds, page geometry chooses the value: `margin-symmetry` first, then
    `foreign-content-boundary`, then the interval midpoint. The value never crosses foreign content to the right.
- **Paragraph boundaries from inference are a candidate, not truth.** Once the measure is fixed, a line break that
  leaves room for the next unit cannot be a soft wrap. It becomes a paragraph boundary, labelled
  `break-not-explained-by-width`. This fixed a real over-merge found by the probe.
- **Region.** The top and bottom come from the nearest foreign content above and below in the column band, or from
  margin symmetry. A region that would intersect any foreign content is refused. Every foreign item becomes a
  `fixed` protected region in the T2 state.
- **Selected numbered body paragraphs.** Consecutive `(n)` markers with the same marker/body origins,
  matching size, nonoverlapping lines and an existing aligned continuation can form a candidate hanging list.
  Only explicitly selected lines are included. No-edit reproduction proves every line and glyph; acceptance
  remains explicit. A selection that leaves any chosen line outside the candidate column is refused.
  The existing signed first-line offset places the marker left of the continuation x; a negative T2 policy
  requires the one-region source-adjacency path and an origin inside the accepted bounds. Width comes from
  the existing wrap interval, line pitch from the witnessed continuation, and follows from source baselines.
- **Fonts.** Candidates come from the installed-font roots of the platform (Windows `%WINDIR%\Fonts` and the per-user
  font folder; macOS system, library and user folders; Linux XDG data dirs and `~/.fonts`), or from injected
  roots or files. A candidate qualifies only when, for every observed glyph of that font program, all of these hold:
  - the glyph is present with an outline;
  - its em advance equals the embedded program's `hmtx` advance (exact rational);
  - its PDF `/W` (or `/Widths`) equals `hmtx × 1000 / upem` within half of the written decimal quantum.

  The last rule is a derived quantization bound, not a tuning tolerance.

  The existing `ShapedFont` writer must also accept the font. Names only order the verified candidates.

  | Verified candidates | Result |
  |---|---|
  | One | Proposed |
  | Several | `needs-choice`: the caller names one by SHA-256 |
  | None | `unresolved` (B3) |
- **Reproduction.** The candidate T2 state, planned with no edits, must reproduce every observed line start exactly,
  and every baseline, line width and painted glyph origin within the unchanged `elements._close` tolerance.
  Glyph occurrences are matched by source Unicode offset, not nearest position or text search.
  Opposite interior gap errors cannot cancel merely because line widths match. Layout-trimmed spaces keep
  their logical Unicode; non-whitespace glyphs cannot disappear.
- **Irregular source spacing.** With observed zero Tc/Tw and irregular source gaps, the proposal exposes
  `text_placement=preserve-source-adjacency`. Explicit acceptance records this optional paragraph policy in
  the v2 contract. A paragraph has one current source slot in one region, default left layout and no continuation
  destinations. Existing glyph codes/resources and gaps between still-adjacent same-source-line characters
  use `ParagraphShaper`'s retained path. Replacement text and new boundaries use qualified providers and
  nominal/source inline metrics. Gap adjustments are not transported across an edit or a newly wrapped edge.
  The source is re-observed on every revision; no unchecked gap array is trusted from a sidecar. Existing
  real Okinawa items (7)/(8) now demonstrate wrap/push-down in a jointly accepted region.
- **Binding.** The proposal carries the source SHA-256, the page, the page observation hash and a canonical digest.
  Optional target line IDs and their caller-selected provenance are included and re-observed at acceptance.
  Acceptance recomputes the proposal from the PDF and the recorded font inputs and requires the same digest. An
  edited proposal is refused, even when re-sealed. Changes are explicit `overrides`:
  - `width` — must stay inside the observed bounds;
  - `region_top` / `region_bottom`;
  - `paragraph_starts`;
  - a consecutive `paragraph_ids` selection.

  The acceptance receipt records them as `caller-override`. The T2 sidecar schema is unchanged.

## 4. Representative scenarios

| # | Scenario | Status after B1 | Evidence |
|---|---|---|---|
| S0 | Open an ordinary PDF and replace "申請書" with "各種申請書" from intent only | **PASS** (supported scope) | Fixture test `test_ordinary_page_edits_through_propose_accept_replace`; probe `PASS-intent-only` |
| S1 | Same style: "申請書" → "各種申請書" | **PASS** | P2 reads "各種申請書を提出してください。" at its original baseline; other lines unchanged |
| S2 | One line grows and wraps to two | **PASS** | Fixture: P2 wraps at 205 / 223 pt at the proposed 425.2 pt measure |
| S3 | Mixed styles in one paragraph | **PASS** for mixed sizes of one font program (probe). Bold/regular with two font programs needs a verified provider for each; this was not probed. | Probe `S3 intent only` |
| S4 | Japanese mid-sentence replacement of a different length | **PASS** | Probe "提出" → "必ず提出" |
| S5 | The upper paragraph grows and the lower one keeps its relation | **PASS** | Fixture: P3 232 → 250 pt (27 pt gap kept); probe: P2 150 → 162 pt (30 pt gap) |
| S6 | Persistent tagged replacement, reflow and re-edit | **PASS** for the verified same-owner leaf-P subset; other patterns **REFUSED** | B2 lifecycle: MCID/tree identity plus pixels |

**Supported scope of PASS:**

- one page;
- horizontal text in one column of non-overlapping paragraphs;
- untagged, or balanced page-level `/P` MCID scopes whose complete ordered bundle belongs to one leaf `/P` StructElem;
- left-aligned naturally spaced lines, or individually reproduced zero-Tc/Tw irregular source adjacency;
- every body font program installed with identical metrics;
- no foreign content inside the column band.

The title, header, footer, images and other text are fixed, and the fixture shows their pixels unchanged after two
saves.

## 5. Remaining ordinary-PDF boundaries (all fail closed)

| Boundary | Where it is refused |
|---|---|
| Tagged structures outside B2: unrelated owners, Span/run trees, MCR children, ActualText/OC, editable Artifact, extra properties, nested scopes, split lines, Forms, malformed trees or nonzero object generations | Structural proof refuses; no stripping, repair or tree mutation |
| Spacing not reproducible by qualified natural shaping or the witnessed-adjacency path; varying/unknown tracking | `layout-not-reproduced` or `shared-flow-confirmation`; no automatic justify |
| No installed font with identical metrics (Linux servers, CFF/OTF fonts, missing fonts) | `unresolved: no-metric-verified-provider` |
| Several columns/tables inside the selected candidate, or foreign content inside its region | `multiple-columns`, `region`; explicit target lines can isolate a safe heading/body elsewhere |
| Growth past the proposed region (margin symmetry or foreign boundary) | Existing T2 overflow refusal |
| Headings in another size, footers and other blocks are foreign, not editable in the same flow | By design (one column) |
| Single-line-only columns: the line pitch evidence is the line box (no leading evidence) | Proposed with the label `observed-line-box` |
| Rotated pages, vertical text beside the column | `rotated-page`, `nonhorizontal-text`, `no-horizontal-text` |

## 6. Ranked blockers (after B1/B2)

### B1 — Ordinary-page editable-state bootstrap: COMPLETE

Done for the supported scope in §4. Remaining B1-adjacent work is listed as its own blocker below, never by relaxing
B1's proofs.

### B2 — Tagged persistent editing: COMPLETE for the supported subset

- `marked_content.paragraph_structure` proves the ordered MCID bundle and existing tree links.
- Only this proof separates structural scopes from attributed non-inline paint state.
- Option B puts reflowed text in the first existing MCID; other same-owner sequences stay empty.
  No MCID is invented and no StructTree/ParentTree object changes.
- Current-revision source witnesses reobserve and bind the bundle; the unchanged T2 writer and
  Transaction provide first save, reopen, wrap/push-down, second save and re-edit.
- The save adapter uses pypdf's full-load, full-rewrite mode to preserve original indirect IDs
  on supported tagged PDFs. Ordinary traversal cloning would renumber the tree on later saves.
- Refusal boundaries remain explicit. Existing Osaka pages have per-run Span owners and
  ActualText/mixed children; none of the four probed pages qualifies as a complete supported
  bundle. A broader Office claim needs additional structure-preserving policies and evidence.

### B6 — Witnessed irregular source adjacency: COMPLETE for the bounded path

- Okinawa page 2 item (7) now reproduces every glyph and completes two length-changing edits,
  save/reopen/re-edit through B1/T2. The old all-provider plan discarded observed adjacency:
  visible nominal width 272.208455 pt versus witnessed 264.839803 pt.
- `spacing.py` still classifies the source as irregular/unknown alignment. Its approximately
  -0.12 pt gaps, -5.40 pt punctuation contraction and mixed-font space remain measured facts.
- `story_styles.source_edits` preserves source offsets; `ParagraphShaper` supplies existing
  code/GID/advance retention. T2 allocation, source ownership, Transaction and atomic validation stay active.
- Source font programs are supplied explicitly, byte-identical to the PDF's embedded subsets, and pass
  unchanged metric qualification. This is not automatic font discovery or general subset expansion.

### B4 — Real body reflow and following-paragraph movement: COMPLETE for the bounded path

- Okinawa items (7)/(8), selected together, reproduce all three original lines and glyphs.
- Consecutive numbered paragraphs with a witnessed hanging continuation are proposed as a complete column;
  explicit selections cannot silently lose a line to another inferred column.
- Existing attributed layout supports the signed first-line offset. T2 bounds the overhang to the accepted
  region; its existing follows/allocation, ownership and Transaction handle push-down and both saves.
- Item (7) grows to two lines, item (8) moves down one measured 19.680237 pt pitch. The original
  19.679993 pt follows gap, the follower's breaks and all fixed foreign content remain unchanged.
- The accepted region ends at the fixed footer (768.698059 pt). Additional growth is refused;
  no automatic page creation, footer movement or crossing an unaccepted region is claimed.
- Evidence and exact scope: [body-reflow report](../evaluations/real_japanese/body-reflow-report.md).

### #1 B3 — Font fidelity without an identical installed font

- **Current limit:** when no supplied or installed font has identical metrics, the proposal is `unresolved`.
  Natural T2 policies regenerate through the provider; the source-adjacency policy retains unchanged codes.
  Only `glyf` TrueType providers are accepted. The real evaluation supplies unmodified embedded subsets,
  whose coverage limits replacement characters; there is no implicit fallback or invented cmap.
- **PDFs unlocked:** pages whose body font is not installed or is CFF/OTF. Untouched glyphs would keep their embedded
  program.
- **Reuse:** the retained-glyph path of `ParagraphShaper` and the identity map.

### #2 B5 — Real text in the semantic layer (T3)

Not on the replacement path; decorations, publication and style reinterpretation on real text come after B3.

## 7. Dependency graph

```
B1 bootstrap (COMPLETE)
├→ B2 same-owner tagged ownership (COMPLETE, narrow subset)
├→ B6 witnessed real-body source adjacency (COMPLETE, bounded)
├→ B3 font fidelity
└→ B4 real body wrap + following-paragraph capacity (COMPLETE, bounded)
        B3 → B5 semantic real text
```

Real Office coverage additionally requires broader tagged structure patterns;
that boundary is not solved by B6 or B3.

## 8. NEXT BLOCKER

```
NEXT BLOCKER: B3 — font fidelity and coverage beyond an available identical provider
```

B4 now passes actual body wrap/push-down and two restored revisions. The runner explicitly supplies
unmodified embedded subsets; missing replacement characters remain refused. This is not general font
fallback, CFF support, arbitrary numbered-list inference, cross-page growth or broader Office ownership.

## 9. Next PR scope

Advance font fidelity/coverage on a concrete real editing case while retaining metric and outline proofs.
Broader tagged trees and overflow into another proven region are separate bounded follow-ups; no fixed
foreign content may move without accepted ownership. Preserve B1's glyph-level reproduction, B6's
source adjacency and B4's paragraph relationships.

## 10. Not next (deliberately)

- More decoration kinds or variants.
- Widening the T3 text scope.
- General multi-column/table inference beyond the selected real-body case.
- T2 atomic publication.

## 11. DO NOT REDESIGN

1. **Source binding** (PDF SHA-256 + page observation hash; stale gives `needs_confirmation`).
2. **Source proofs before writes** (no-op replay, removal, foreign preservation).
3. **One Transaction** (`MutationProgram`, `IdentityMap`, one save, one verification).
4. **Current-revision source ownership.**
5. **Shared flow v2 allocation and validators** (B1 builds their arguments; it never bypasses them).
6. **Trust model P4.** A proposal is evidence; only `accept_page_flow` confirms. Names never qualify fonts. Widths are
   never observed content widths.
7. **B1 reproduction rule.** A proposal must reproduce the unedited page through the T2 no-op plan.
8. **Retained-glyph shaping in one-shot edits.**
9. **Semantic authority layers, canonical writer, decoration paint ownership, L3 publication; exact rational layout in
   T3.**

## 12. Validation

B1 baseline: [continuation checkpoint](continuation-checkpoint.md#b1-ordinary-page-bootstrap--2026-10-07).
B2 contract, reproducible commands, Windows evidence and limitations: [B2 report](tagged-persistent-editing.md).
Current targeted/B1/B2 focused regression: **69 passed** (12 + 19 + 38), Windows.
Current Linux body reflow evidence and reproducible regression/full-suite commands:
[body reflow report](../evaluations/real_japanese/body-reflow-report.md). The earlier Windows count above is historical,
not the result of this change; final-HEAD test counts are recorded in the pull request.
The [real-case report](real-japanese-persistent-editing.md) and
[measured summary](../evaluations/real_japanese/summary.json) record both actual saved
revisions, restored reopen, exact provider outlines and independent preservation audits.
Final full-suite counts and the exact tested commit are recorded in the current PR.
