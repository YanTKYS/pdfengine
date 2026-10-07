# Acrobat-like editing — current critical path

**Current-state document.** This file holds only the current critical path. Rewrite it whenever the state changes. It
is not a history log; history is in [continuation-checkpoint.md](continuation-checkpoint.md).

- Evaluated main: `7ea0cf94410494c35585c84e1caa4200d18d9941` (PR #55 merged), 2026-10-07.
- Runtime changes in this review: none (`pdfeditor/` unchanged).
- Evidence: the source code on main, plus a read-only probe that calls production APIs only:
  [scenario_probe.py](../evaluations/critical_path/scenario_probe.py) and
  [probe-results.json](../evaluations/critical_path/probe-results.json). The probe used Linux, Python 3.12.3, the
  lockfile versions and IPAGothic (TrueType).

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
| **PASS** | A production path does it from user intent only: the PDF, the target text, the replacement, and the fonts installed on the machine. |
| **BOUNDED** | A production path does it, but only when the caller hand-writes declarations: glyph selection, available width, region bounds, line and gap policies, and a font file for every style. |
| **REFUSED** | A production path exists and an explicit guard refuses this input (fail closed, original unchanged). |
| **UNSUPPORTED** | No production path exists. |

## 2. The end-to-end path that exists today

pdfengine has three persistent tiers. Only the first two can hold real text.

| Tier | Entry | Persistent state | Real text? |
|---|---|---|---|
| T1 single paragraph | `edit-paragraph --editable-state` → `edit-document` (CLI) | `pdfengine-editable-2` | Yes |
| T2 shared flow v2 | `confirm_story` → `confirm_shared_flow` → `edit_shared_flow` → `open_shared_flow` (Python API) | `pdfengine-shared-flow-2` plus source-output owner markers | Yes |
| T3 semantic layout | `confirm_semantic_layout` → `plan_semantic_transition` → L2 writer → L3 publication | `pdfengine-shared-flow-3` (semantic record v2/v3/v4) | **No: `A`, `B`, space and newline only** |

Path traced (T2, the tier that reaches S5):

| Stage | Code | Production-ready | Caller declaration | Fails on an ordinary PDF |
|---|---|---|---|---|
| Observe | `selection.inspect_selection_source`, `inference.infer_boxes` | Glyph/Run/Line catalog, paragraph candidates | — | Available width is never inferred (`inference.py` docstring; `model.py:43`) |
| Select | `selection.make_selection` | SHA + observation-hash binding, horizontal only | **Glyph/line IDs, width** | Without a width, `WidthUnknownError` |
| Paragraph snapshot | `attributed.inspect_paragraph` | Unicode, style intervals, source bindings, spacing/alignment candidates | Line joiner | ActualText/OC spans, non-opaque fill, mixed clips (`attributed.py:104-123`) |
| Story | `story_flow.confirm_story`, `story_styles.confirm_registry` | Style registry with observed witnesses | **Container bounds and layout, a font file per style, style assignment, typing style** | Names never imply a font (`story_styles.py:133`) |
| Shared flow | `shared_flow.confirm_shared_flow` | Multi-paragraph allocation, regions, follows, continuation destinations | **Region bounds and width, first baseline, paragraph policies, follows gaps, protected regions** | — |
| Edit and layout | `shared_flow._plan` → `paragraph.plan_paragraph_edit` → `rich_layout.layout_attributed` | HarfBuzz shaping, Japanese wrap, push-down of the following paragraphs | Unicode range edits plus `style_id` | Overflow past confirmed regions (`shared_flow.py:431`, `:460`) |
| Mutation | `transaction.Transaction`, `mutation.MutationProgram`, `source_ownership` | No-op replay proof, removal proof, one save, one verification, identity map, owned markers | — | **Marked content (tagged PDF): `source output needs an unmarked, path-free page-level context` (`source_ownership.py:134`)** |
| Save | `edit_shared_flow` | New PDF + sidecar (two paths, not an atomic bundle) | — | — |
| Reopen and re-edit | `open_shared_flow` → `edit_shared_flow` | `restored` or `needs_confirmation` (fail closed) | — | — |
| Semantic (T3) | `semantic_layout._scope`, `_payload`, `_island` | Exact rational layout, canonical island, decorations, atomic publication | **Complete semantic statement** | Any real text (`semantic_measure.py:18`); more than one paragraph, slot, region or style; non-left alignment; clipped or non-identity entry (`semantic_layout.py:117-140`, `:419`) |

### What the runtime can build automatically for a PDF opened for the first time

**Automatic (observation only):**

- glyph, run and line catalogs;
- paragraph candidates (`inference._paragraphs`);
- for a chosen glyph set, Unicode with style intervals and source bindings;
- alignment and spacing candidates;
- paint/element candidates;
- continuation boundary candidates.

**Not automatic. Every one is a hand-written caller declaration:**

- which glyphs form a paragraph;
- the available width;
- the region top and bottom;
- the line height and paragraph gaps;
- the logical style assignment;
- a font file for every style, including retained text in T2;
- the paragraph order and the `follows` relations.

**Semantic state (T3)** cannot be built for any ordinary PDF. It needs a T2 slot that is already owned, plus a complete
caller statement, and its text scope is `{A, B, space, newline}`.

**Conclusion:** the engine below the front door handles real Japanese text well. The front door itself does not exist.
This is the largest gap.

## 3. Current proven foundation (do not rebuild)

| Foundation | Where | Proven by |
|---|---|---|
| Source-bound selection and snapshots (PDF SHA + page observation hash) | `selection.py`, `attributed.py` | Stale input refused |
| Source facts proven before any write: no-op replay, removal, foreign glyph preservation | `paragraph.plan_paragraph_edit` | Probe: tagged, clip and flip pages pass the one-shot path |
| Attributed shaping: retained source codes plus supplied-font HarfBuzz runs, Japanese wrap | `paragraph.ParagraphShaper`, `rich_layout` | Probe S1–S4 |
| One Transaction: plan, mutate, save once, verify once, identity map | `transaction.py`, `mutation.py` | Every probe write |
| Shared flow v2: multi-paragraph allocation, push-down by confirmed gap, fail-closed reopen, re-edit | `shared_flow.py` | Probe S5: the lower paragraph moves from 150 to 166 pt and the flow reopens `restored` twice |
| Current-revision source ownership (markers, witness, containment, rebind) | `source_ownership.py` | Probe: persistent re-edit |
| Generated-font ownership records | `shared_flow._verify_generated_fonts` | No-op re-saves do not grow |
| Semantic authority layers A/B/C, canonical island writer, paint ownership of owned decorations | `semantic_layout.py`, `semantic_writer.py`, `semantic_paint.py` | Windows lifecycle validations (underline, strikeout) |
| Atomic bundle publication (semantic bundles only) | `semantic_publication.py` | Windows validation |

The decoration lifecycle (contract → runtime → publication → Windows) proved paint ownership, semantic authority,
Transaction and publication. **It is finished as a proof; do not grow it by adding more decoration kinds.**

## 4. Representative scenarios on current main

Probe results come from [probe-results.json](../evaluations/critical_path/probe-results.json). Every PASS in the probe
needed the declarations listed there.

| # | Scenario | T1/T2 (real text) | T3 semantic | Evidence |
|---|---|---|---|---|
| S0 | Open an ordinary PDF and replace "申請書" with "各種申請書" from intent only | **UNSUPPORTED** | UNSUPPORTED | No API turns (PDF, page, target, replacement) into a selection, region and font. Without a width: `WidthUnknownError`. Without a font: `new text needs an explicit font` (`paragraph.py:126`). |
| S1 | Same style: "申請書" → "各種申請書" | **BOUNDED** | REFUSED | edit_paragraph PASS; shared flow v2 save → reopen → second edit PASS. T3: `semantic text is outside the A/B/space/newline scope`. |
| S2 | One line grows and wraps to two | **BOUNDED** | REFUSED | edit_paragraph: two lines at 700 / 716.8; shared flow: two lines at 120 / 136, reopened. |
| S3 | Mixed styles in one paragraph (probe: 12 pt and 15 pt runs) | **BOUNDED** | REFUSED (`one body style`) | One-shot keeps the retained source glyphs per style. In T2, a font file is needed for every logical style. A real bold/regular pair is the same code path (style intervals), but was not probed. |
| S4 | Japanese mid-sentence replacement of a different length | **BOUNDED** | REFUSED | Shared flow: "提出" → "必ず提出", reopened. |
| S5 | The upper paragraph grows and the lower one keeps its relation | **BOUNDED** (T2, untagged only) | REFUSED (one paragraph) | T2 moves the lower paragraph by the confirmed gap. T1 never moves neighbours; it fits or refuses on collision. |
| S6 | S1 on a tagged PDF (`/P <</MCID n>> BDC … EMC`, Word's default export) | one-shot BOUNDED, **persistent REFUSED** | REFUSED | `source output needs an unmarked, path-free page-level context` |

Nothing reaches PASS today. Every working scenario depends on hand-written declarations.

## 5. Scope guards (inventory and class)

Classes:

- **A** — must be lifted to reach the summit.
- **B** — lifting it greatly widens the set of real PDFs.
- **C** — later.
- **D** — keep it for safety.

| Guard (code) | Class |
|---|---|
| Available width never inferred, `WidthUnknownError` (`model.py:43`) | **A** — replace with an evidence-labelled *proposal* that the caller accepts. Never infer silently. |
| A font file is required for new text and, in T2, for the whole paragraph (`paragraph.py:126`; `reflow_font_policy=explicit_provider_for_each_logical_style_in_entire_story`) | **A** — the provider must be resolvable without hand-writing paths. Name-only matching stays forbidden. |
| Selection, region, policy and follows are all hand-written (`confirm_story`, `confirm_shared_flow`) | **A** |
| Marked content refused in source ownership (`source_ownership.py:134`) | **B** |
| T2 re-renders retained text with the provider (the original embedded glyphs are dropped after the first persistent edit) | **B** — fidelity when no matching font is installed |
| Providers must be TrueType `glyf` (`shaped_font.py:53`); no CFF/OTF | **B** |
| Overflow past confirmed regions refused; no automatic continuation or new page (`shared_flow.py:431`, `:460`) | **C** |
| Semantic T3 scope: A/B text, `MAX_TEXT=256`, one paragraph/slot/region/style, left only, static unhinted TT, identity unclipped entry, `REFUSED_OPERATIONS` | **C** — not on the replacement critical path |
| Non-left alignment and new glyphs need confirmed tracking (`paragraph.py:175`, `:224`) | **C** — the bootstrap can propose the value; the guard itself stays |
| Horizontal text only (`selection.py:86`); combining marks, RTL and Indic refused (`fonts.validate_simple_text`) | **C** |
| Rectangular clips only; device gray/RGB/CMYK only; `Tr 0`; opacity 1 (`source_ownership.context`) | **D** for now — widen only with proofs |
| Positive horizontal transforms; opaque fill; one clip per paragraph; ActualText/OC refused (`attributed.py:104-123`) | **D** |
| Source no-op replay, removal proof, foreign glyph and paint containment, Transaction overlap, SHA binding of sidecars, fail-closed reopen | **D** — these are the safety core |

## 6. Ranked blockers

### #1 B1 — Ordinary-page editable-state bootstrap (front door)

- **Current limit:** each of these must be hand-written by the caller:
  - glyph selection;
  - available width;
  - region bounds;
  - line height and gaps;
  - follows relations;
  - style assignment;
  - a font *file path* for every style.

  The engine computes paragraph candidates (`inference._paragraphs`) and style intervals, but nothing turns them into
  a confirmable T1/T2 state.
- **What the user cannot do:** open a PDF and edit it. Today only an expert who writes JSON coordinates can edit.
- **Code:** `model.py:43` (`WidthUnknownError`), `paragraph.py:126`, `story_flow.confirm_story` (required `layout`,
  `bounds`, `styles`), `shared_flow.confirm_shared_flow` (`regions`, `paragraph_policies`, `follows`),
  `story_styles.py:133`.
- **Why the summit needs it:** the first step of the summit is "open an ordinary PDF". Without a front door, no
  improvement deeper in the engine reaches a user.
- **PDFs unlocked:** untagged, horizontal, single-column text pages with an embedded TrueType body font whose installed
  counterpart can be verified, for example:
  - Word documents printed with Microsoft Print to PDF;
  - LibreOffice exports made without the Tagged PDF option;
  - other untagged producers with an embedded TrueType body font.

  For those pages, S1–S5 go from BOUNDED to PASS (acceptance in one step).
- **Prerequisite:** none.
- **Implementation risk:** medium. Inference is heuristic: it over-merged paragraphs in the
  [real-PDF corpus evaluation](realpdf-evaluation.md).
  This risk is held by:
  - making the result a proposal with evidence labels;
  - requiring one explicit acceptance;
  - running the unchanged T2 validators and writers after acceptance.
- **Reuse:** very high. Observation, inference, `inspect_paragraph`, `confirm_registry`, `confirm_story`,
  `confirm_shared_flow`, `edit_shared_flow`, `Transaction` and `ShapedFont` all stay unchanged. The caller-workflow
  pattern of continuation (inspection → review → decision → confirmation request → explicit confirm) is reused.

### #2 B2 — Tagged (marked-content) PDFs in persistent ownership

- **Current limit:** `source_ownership.context` refuses any paragraph whose first operator sits inside `BDC/BMC … EMC`.
  The one-shot path accepts tagged pages, but T2 cannot create an owner on them.
- **What the user cannot do:** keep editing a Word "Save as PDF" document (tagged by default) or an accessibility-tagged
  public document.
- **Code:** `source_ownership.py:134`; probe S6.
- **Why the summit needs it:** the summit includes "reopen and keep editing" on an *ordinary* PDF. Tagged Word output
  is the ordinary case in Japanese local government.
- **PDFs unlocked:** tagged Word and Office exports, and PDF/UA documents. Owned islands must stay inside their
  original MCID span. Structure-tree integrity becomes part of the witness.
- **Prerequisite:** B1, so that the unlocked pages are reachable without hand-written state.
- **Implementation risk:** medium. A marked-content-preserving island grammar is needed, with the MCID and its
  ParentTree backlink unchanged. `marked_content.py` already reads ParentTree read-only.
- **Reuse:** high. Owner markers, witness, containment and Transaction stay; only the context rule and the grammar
  around the island change.

### #3 B3 — Font fidelity in the persistent path

- **Current limit:** the T2 policy `explicit_provider_for_each_logical_style_in_entire_story` re-renders retained text
  with the provider. Only `glyf` TrueType providers are accepted (`shaped_font.py:53`). The embedded program is never
  reused for retained glyphs in T2.
- **What the user cannot do:** keep the original look of untouched words when the original font is not installed, for
  example on Linux servers or with Mac and Adobe CFF/OTF fonts such as Hiragino, Kozuka and Source Han.
- **Why the summit needs it:** "keep the existing formatting as far as possible".
- **PDFs unlocked:** PDFs whose body font is not installed or is CFF. Only edited spans change face.
- **Prerequisite:** B1, whose provider verification decides when B3 is needed.
- **Implementation risk:** medium to high. T2 must keep retained source codes across re-edits, which the one-shot
  `ParagraphShaper` already does.
- **Reuse:** high. The retained-glyph path in `ParagraphShaper.shape` and the identity map already exist.

### #4 B4 — Layout scope beyond confirmed regions

- **Current limit:** overflow is refused. Content outside the flow (tables, images, other columns) is fixed. There is no
  automatic continuation and no new page.
- **What the user cannot do:** grow text past the confirmed region, or push down non-paragraph content.
- **Why the summit needs it:** only partly. Acrobat itself reflows inside one text box.
- **Prerequisite:** B1.
- **Risk:** high. **Reuse:** continuation destinations and the boundary review pipeline.

### #5 B5 — Real text in the semantic layer (T3)

- **Current limit:** text `{A, B, space, newline}`; `MAX_TEXT=256`; one paragraph, slot, region and style; static
  unhinted TT; no shaping (`semantic_measure.py:18-48`).
- **What the user cannot do:** apply decorations, style reinterpretation or bundle publication to real text.
- **Why the summit needs it:** it does not need it for replacement. T2 already saves, reopens and re-edits real text.
- **Prerequisite:** B1 and B3. Shaping-aware exact measurement is needed.
- **Risk:** high. **Reuse:** the authority layers, the writer and publication.

The candidate directions from the request map to these blockers as follows:

- **A (ordinary PDF → editable state)** is B1.
- **B (multi-style)** already works in T1/T2 (S3 BOUNDED); only T3 refuses it, which is B5.
- **C (multiple paragraphs, slots, regions)** already works in T2 (S5); only T3 refuses it, which is B5.
- **D (reflow scope)** is B4 for the part beyond confirmed regions; next line and next paragraph already work.
- **E (fonts and characters)** splits: the A/B scope is T3 only (B5); provider resolution belongs to B1; face fidelity
  and CFF are B3.

## 7. Dependency graph

```
B1 bootstrap (proposal → one acceptance → T2 state)
├→ B2 tagged ownership ──┐
├→ B3 font fidelity ─────┼→ Acrobat-like replacement on ordinary PDFs (S0–S6 PASS)
└→ B4 layout scope (partial; Acrobat parity does not need it)
        B3 → B5 semantic real text (decorations and publication on real text; after the summit path)
```

## 8. NEXT BLOCKER

```
NEXT BLOCKER: B1 — ordinary-page editable-state bootstrap
```

When this blocker is removed:

- untagged single-column PDFs (for example Word via Print to PDF, or LibreOffice without Tagged PDF) move from "editable only by an expert
  who hand-writes selection IDs, widths, region bounds, policies and font paths" to "editable by accepting one
  proposal";
- S1–S5 move from BOUNDED to PASS on those pages.

B2, B3 and B4 all work on states that B1 produces. Without B1, their gains also reach only hand-written callers. That
is why B1 goes first.

It does **not** come first because the design is cleaner. It comes first because every other improvement is invisible
to a user until a first-opened PDF can reach a T2 state.

## 9. Exact next PR

**Title:** Propose and accept an editable shared flow for an ordinary page.

**Scope.** One page, horizontal text, one column. The result is a T2 state.

1. `propose_page_flow(source, page, *, font_candidates)` is read-only. It returns a proposal bound to the PDF SHA-256
   and the page observation hash. For each proposed paragraph it records:
   - the glyph selection (from `inference` line grouping and `_paragraphs`);
   - the first baseline, the first-line indent and the line height (median ordinary gap);
   - the paragraph gap to the next paragraph, giving `follows`;
   - one column region: x0 from the left edges; width from wrapped lines, then sibling paragraphs, then page-margin
     symmetry, never wider than foreign content to the right; top from the first ascent; bottom from the nearest
     foreign content below, or margin symmetry;
   - style intervals with logical styles grouped by identical observed inline attributes;
   - for each style, **metric-verified provider candidates**: a candidate in `font_candidates` qualifies only when,
     for every observed glyph of that style, the advance width (`/W` or `/Widths` against `hmtx` for the same Unicode)
     and the glyph's presence match. Names may order the candidates; they never qualify one.

   Every value carries an evidence label, for example `wrapped-line-right-edge` or `margin-symmetry`.

   A proposal refuses or excludes, with reasons:
   - a page with overlapping columns;
   - non-horizontal glyphs;
   - unmapped Unicode;
   - a paragraph whose style has zero or more than one verified provider (the caller may still name one explicitly);
   - a tagged page (reported up front as B2; one-shot editing of tagged pages stays available through the existing
     T1 path).
2. `accept_page_flow(source, proposal, *, paragraphs, providers=None, overrides=None)` is the single explicit
   acceptance. It rechecks the proposal binding and builds exactly the `confirm_story` and `confirm_shared_flow`
   arguments a hand-writing caller would pass. It returns the unchanged `pdfengine-shared-flow-2` state from
   `confirm_shared_flow`. An override replaces a proposed value explicitly and is recorded in the acceptance receipt.
3. `replace_in_flow(state, paragraph_id, find, replacement)` turns a unique find string into the existing Unicode-range
   edit request for `edit_shared_flow`. Zero or several matches are refused.
4. CLI: `propose-page`, `accept-page` and `replace-text`. They are thin wrappers that write new files only.

**Production files likely touched.**

- New: `pdfeditor/page_proposal.py`.
- `pdfeditor/cli.py` (new commands).
- Read-only reuse:
  - `inference.py`, `selection.py`, `attributed.py`;
  - `story_flow.py`, `story_styles.py`, `shared_flow.py`;
  - `shaped_font.py`, `content_stream.py` (`/W` and `/Widths` widths).
- No change to `source_ownership.py`, `transaction.py`, any semantic module, or the sidecar schemas.

**Representative fixture.** A generated Word-like A4 page (untagged) with:

- an embedded TrueType body font (subset) whose full counterpart is in `font_candidates`;
- a title at 14 pt;
- three Japanese body paragraphs at 10.5 pt, wrapped to a 72 pt margin, with first-line indents;
- a page-number footer.

A Windows external run on a real Print-to-PDF document follows in the validation step of the same PR. It is not a
separate design PR.

**New tests (`tests/test_page_proposal.py`).**

- The proposal on the fixture gives the expected paragraphs, region, policies, styles and one verified provider, with
  the evidence labels. It is deterministic and binds to the SHA.
- Acceptance followed by `edit_shared_flow` for S1 (paragraph 2: "申請書" → "各種申請書"), then `open_shared_flow`
  `restored`, then a second edit for S2+S5 (paragraph 2 grows by a line and paragraph 3 moves by the observed gap),
  then `restored`. Title and footer pixels are unchanged.
- The accepted state is byte-identical to the state a hand-written caller produces from the same values (backward
  compatibility).
- Refusals:
  - a stale PDF or a stale observation;
  - an edited proposal;
  - an unverified or ambiguous provider;
  - a provider with mismatched widths;
  - overlapping columns;
  - a tagged page on the T2 route (with the B2 reason);
  - a find string matching zero or several times;
  - a proposed region that collides with foreign content.

**Acceptance criterion.** On current main, a caller who holds only the fixture PDF, the page number, "申請書",
"各種申請書" and a font candidate directory has **no production path**. After the PR, that caller gets:

- through propose → accept → `replace_in_flow` → `edit_shared_flow`, a saved PDF whose paragraph 2 reads the replaced
  text, wrapped naturally;
- paragraph 3 moved by the observed gap;
- a pair that reopens `restored` and accepts a second edit.

No coordinates, selection IDs or font paths are written by hand.

**Refusals that remain:**

- tagged pages in T2 (B2);
- unverified fonts, unless the caller names a provider explicitly (B3);
- multiple columns and overlapping layouts;
- overflow past the proposed region (B4);
- rotated or vertical text;
- every D guard;
- the T3 semantic scope (B5).

**Failure isolation.** The proposal is read-only. Acceptance writes nothing; it returns the same validated T2 state.
Every write still goes through the unchanged `edit_shared_flow` and `Transaction`. A wrong proposal can, at worst,
produce a state that a person accepted; it cannot bypass any guard.

**Explicitly out of scope:**

- tagged ownership;
- keeping retained source glyphs in T2;
- CFF providers;
- multi-column and table layout;
- page-level reflow;
- any T3 or decoration change;
- automatic acceptance without the explicit accept call.

## 10. Not next (deliberately)

- More decoration kinds or variants: highlight, overline, border, decoration colour, underline variants.
- Widening the T3 semantic text scope before B1 and B3. T2 already carries real text.
- Split or join paragraphs, cross-paragraph edits, region reassignment in T3.
- Atomic publication for T2. The PDF + sidecar pair works today; publication can be added later.

## 11. DO NOT REDESIGN

1. **Source binding.** Selections, snapshots and sidecars are bound to the PDF SHA-256 and the page observation hash.
   A stale input gives `needs_confirmation`, never a silent edit.
2. **Source proofs before writes.** No-op replay, removal proof and preservation of foreign glyphs and paint
   (`plan_paragraph_edit`).
3. **One Transaction.** Plan, mutate, save once, verify once; `MutationProgram` and `IdentityMap`. No intermediate PDF
   saves.
4. **Current-revision source ownership.** Markers locate bytes; witness, containment and rebind authorize them.
   Unclaimed markers never grant ownership.
5. **Shared flow v2 allocation.** Final allocation comes before mutation; `follows` gaps; fail-closed reopen;
   generated-font ownership records.
6. **Trust model P4.** The engine never infers authority silently. A proposal is evidence; only an explicit caller act
   confirms it. Names never imply fonts or traits.
7. **Retained-glyph shaping in one-shot edits.** Source codes for unchanged text; HarfBuzz runs for supplied text.
8. **Semantic authority layers A/B/C, the canonical island writer, decoration paint ownership and L3 publication.**
   Extend their scope; do not rebuild them.
9. **Exact rational layout in T3.** Its separation from renderer observation stays.

## 12. Baseline

The full suite on main `7ea0cf9` (Linux, Python 3.12.3, lockfile, `-n 4`) is recorded in
[continuation-checkpoint.md](continuation-checkpoint.md#acrobat-critical-path-reset--2026-10-07).
