# Acrobat-like editing — current critical path

**Current-state document.** This file holds only the current critical path. Rewrite it whenever the state changes. It
is not a history log; history is in [continuation-checkpoint.md](continuation-checkpoint.md).

- State: main `30e5fe74b7426989d04ce1f915790cdedd6e9a5b` plus the B1 front door (`pdfeditor/page_proposal.py`,
  CLI `propose-page` / `accept-page` / `replace-text`), 2026-10-07.
- **B1 — ordinary-page editable-state bootstrap: COMPLETE** for the scope in §4.
- Evidence: the source code; [tests/test_page_proposal.py](../tests/test_page_proposal.py) on a generated Word-like
  Japanese A4 page; and a read-only probe that calls production APIs only, with a real font (IPAGothic)
  ([scenario_probe.py](../evaluations/critical_path/scenario_probe.py),
  [probe-results.json](../evaluations/critical_path/probe-results.json)). Linux, Python 3.12.3, lockfile versions.
  **No Windows run and no real-world PDF corpus were used** (cloud only).

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
→ propose_page_flow(pdf, page)            read-only proposal: observation + evidence + candidate interpretation
→ accept_page_flow(pdf, proposal)          the one explicit act; returns the unchanged pdfengine-shared-flow-2 state
→ replace_in_flow(state, find, replacement)   edit request only; writes nothing
→ edit_shared_flow(...)                    the existing T2 writer: Transaction, ownership, one save, one verification
→ open_shared_flow(...) == restored        → next replace_in_flow / edit_shared_flow
```

The CLI is the same path: `propose-page` → `accept-page` → `replace-text` (README).

| Stage | Code | What it now does without hand-written input | What still needs the caller |
|---|---|---|---|
| Observe | `backend.extract_page`, `selection.observation_lines`, `inference._line_groups/_paragraphs` | Lines, paragraph candidates, the main column, foreign content (other text, paths, images) | — |
| Propose | `page_proposal._build` | Paragraph selections, column x, available width (§3.1), region bounds, line pitch, first-line indent, follows gaps, logical styles, metric-verified font providers | Review only |
| Verify the proposal | `page_proposal._reproduce` → `shared_flow.plan_shared_flow(state, {})` | The exact T2 state is built and planned with no edits. It must reproduce every observed line break, baseline and line width, or the proposal is refused. | — |
| Accept | `accept_page_flow` → `confirm_story` → `confirm_shared_flow` | Rechecks the binding, recomputes the proposal, applies explicit choices and overrides, and returns the T2 state | One call; a provider choice only when several fonts verify |
| Edit | `replace_in_flow` → `edit_shared_flow` | Finds the unique occurrence across the flow, takes its single logical style, and writes through the unchanged T2 path | Find and replacement text |
| Reopen and continue | `open_shared_flow` | `restored`, or `needs_confirmation` (fail closed) | — |

The semantic layer (T3, `shared-flow-3`) is unchanged. It still admits only `A`, `B`, space and newline, and it is
not on the replacement path.

### 2.1 What the runtime builds automatically for a PDF opened for the first time

For an untagged, horizontal, one-column page whose body font is installed with identical metrics, it builds everything
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
| Shared flow v2: multi-paragraph allocation, push-down by gap, fail-closed reopen, re-edit | `shared_flow.py` | Fixture: P3 moves 232 → 250 pt keeping the 27 pt gap |
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
  and every baseline and line width within `elements._close`, the shared-flow geometry tolerance. Otherwise the
  proposal is refused, and nothing on the page can move silently.
- **Binding.** The proposal carries the source SHA-256, the page, the page observation hash and a canonical digest.
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
| S6 | S1 on a tagged PDF (Word "Save as PDF" default) | **REFUSED** at proposal (`unsupported-for-persistent-flow: tagged-page`) | B2 |

**Supported scope of PASS:**

- one page;
- horizontal text in one column of non-overlapping paragraphs;
- untagged;
- left-aligned, naturally spaced source lines;
- every body font program installed with identical metrics;
- no foreign content inside the column band.

The title, header, footer, images and other text are fixed, and the fixture shows their pixels unchanged after two
saves.

## 5. Remaining ordinary-PDF boundaries (all fail closed)

| Boundary | Where it is refused |
|---|---|
| Tagged pages: per-line MCID marked content breaks the common paragraph state (`attributed.py:115`), and source ownership refuses marked content (`source_ownership.py:134`) | Proposal refusal `paragraph-evidence` / `unsupported-for-persistent-flow` |
| Justified or spaced source lines (Japanese Word's default body alignment is 両端揃え) | `layout-not-reproduced` or `shared-flow-confirmation` (varying tracking) |
| No installed font with identical metrics (Linux servers, CFF/OTF fonts, missing fonts) | `unresolved: no-metric-verified-provider` |
| Several columns, tables, or foreign content inside the column | `multiple-columns`, `region` |
| Growth past the proposed region (margin symmetry or foreign boundary) | Existing T2 overflow refusal |
| Headings in another size, footers and other blocks are foreign, not editable in the same flow | By design (one column) |
| Single-line-only columns: the line pitch evidence is the line box (no leading evidence) | Proposed with the label `observed-line-box` |
| Rotated pages, vertical text beside the column | `rotated-page`, `nonhorizontal-text`, `no-horizontal-text` |

## 6. Ranked blockers (after B1)

### B1 — Ordinary-page editable-state bootstrap: COMPLETE

Done for the supported scope in §4. Remaining B1-adjacent work is listed as its own blocker below, never by relaxing
B1's proofs.

### #1 B2 — Tagged (marked-content) pages in the persistent route

- **Current limit:** two existing guards refuse tagged pages.
  - Per-line `/P <</MCID n>> BDC … EMC` gives each line a different non-inline state, so `inspect_paragraph` refuses a
    multi-line paragraph.
  - `source_ownership.context` refuses an owner inside marked content.
- **What the user cannot do:** keep editing a Word "Save as PDF" document (tagged by default) or a PDF/UA public
  document.
- **Evidence:**
  - fixture test `test_tagged_page_is_refused_for_the_persistent_route`;
  - probe S6;
  - the earlier real Word evidence in [attributed-editing.md](attributed-editing.md): a Takeo Word PDF was refused
    because the MCID history changes inside the selection.
- **PDFs unlocked:** tagged Word and Office exports and PDF/UA documents, the ordinary case for Japanese public
  documents.
- **Prerequisite:** B1 (done), so tagged pages are reachable without hand-written state.
- **Risk:** medium. Marked content must be treated as structure, not paint state, and the island must keep its MCID
  spans and the ParentTree backlink. `marked_content.py` already reads ParentTree read-only.
- **Reuse:** the B1 proposal, T2, ownership witness and Transaction stay; only the marked-content rules change.

### #2 B6 — Justified (両端揃え) source paragraphs in the proposal

- **Current limit:** B1 reproduces left-aligned, naturally spaced lines only. Spread lines fail reproduction, and
  varying per-line `Tc` makes tracking unknown.
- **Why it ranks here:** Japanese Word's default body alignment is 両端揃え. At an A4 / 30 mm measure, 40 full-width
  10.5 pt characters leave 5.2 pt of slack, which justification spreads over the line.
- **Reuse:** high. The machinery already exists:
  - `spacing.py` proves justify candidates from operator evidence;
  - `alignment.py` / [confirmed-alignment.md](confirmed-alignment.md) let T2 lay out `justify` / `character`.

  B6 is a proposal extension (propose the alignment and its evidence), not a new engine.
- **Unverified:** how often real Word output is spread like this needs a Windows real-PDF run. That run would also
  re-rank B2 against B6.

### #3 B3 — Font fidelity without an identical installed font

- **Current limit:** when no installed font has identical metrics, the proposal is `unresolved`. T2 also re-renders
  retained text with the provider. Only `glyf` TrueType providers are accepted.
- **PDFs unlocked:** pages whose body font is not installed or is CFF/OTF. Untouched glyphs would keep their embedded
  program.
- **Reuse:** the retained-glyph path of `ParagraphShaper` and the identity map.

### #4 B4 — Layout beyond one column and one region

Multi-column, tables, growth past the proposed region, and pushing foreign content. Acrobat parity does not require
most of this.

### #5 B5 — Real text in the semantic layer (T3)

Not on the replacement path; decorations, publication and style reinterpretation on real text come after B3.

## 7. Dependency graph

```
B1 bootstrap (COMPLETE)
├→ B2 tagged ownership ──────┐
├→ B6 justified proposal ────┼→ Word/Office-produced Japanese PDFs edit at PASS
├→ B3 font fidelity ─────────┘   (B3 also decides servers / CFF fonts)
└→ B4 layout scope (partial)
        B3 → B5 semantic real text
```

## 8. NEXT BLOCKER

```
NEXT BLOCKER: B2 — tagged (marked-content) pages in the persistent route
```

When it is removed, tagged ordinary documents (Word "Save as PDF", PDF/UA exports) move from REFUSED to the B1 path.

It comes before B6 for three reasons:

- it is backed by real-PDF evidence already in the repository;
- it is refused by two independent existing guards;
- B6 is an extension of the B1 proposal using existing justify support, and its real-world prevalence is not yet
  measured.

The next Windows real-PDF validation should measure both. If it shows that most target PDFs are untagged but
justified, B6 goes first.

## 9. Next PR (B2) — scope

- **Paragraph state.** Treat a marked-content span that wraps whole lines as structure, not non-inline paint state.
  The paragraph keeps one non-inline state when the only difference between lines is their MCID span.
- **Ownership.** Allow the owned island inside marked content only with a structure-preserving rule:
  - the rewritten text stays inside its original `BDC … EMC` spans, or one span chosen by an explicit rule;
  - MCIDs and the ParentTree/StructTree backlinks are unchanged, verified by `marked_content.py`;
  - the witness includes the marked-content context.
- **Proposal.** `propose_page_flow` stops refusing tagged pages and labels the marked-content evidence.
- **Acceptance criterion.** The tagged variant of the B1 fixture edits through propose → accept → replace → reopen →
  second edit, and the structure tree still resolves every MCID.
- **Refusals that stay:**
  - ActualText and optional content;
  - artifacts that cross paragraphs;
  - marked content that splits a line.

## 10. Not next (deliberately)

- More decoration kinds or variants.
- Widening the T3 text scope.
- Multi-column inference before B2/B6.
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

## 12. Baseline

The full suite for this state is recorded in
[continuation-checkpoint.md](continuation-checkpoint.md#b1-ordinary-page-bootstrap--2026-10-07).
