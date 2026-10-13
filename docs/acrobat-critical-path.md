# Acrobat-like editing — current critical path

**Current-state document.** This file holds only the current critical path. Rewrite it whenever the state changes. It
is not a history log; history is in [continuation-checkpoint.md](continuation-checkpoint.md).

- State: starting main `045e3551c4a576d40e4d00efbea60a9d8707ba7b` (PR #72). `append_to_region` can now carry an
  explicitly approved placement policy for its first follower: a minimum gap and an in-region baseline floor.
- **Approved follower spacing: implemented and pinned by synthetic regressions. NOT validated on a real PDF.** The
  real-PDF evaluation `evaluations/real_japanese/follower_spacing.py` exists but has **not been run**. The session
  network could not reach the LibreOffice source PDF.
  - With the approved floor, the PR #72 synthetic case no longer refuses on shortening: D stays at its approved
    126.9 pt floor, outside the release region.
  - Without a policy, the PR #72 rule still applies and the same edit still refuses.
  - [Report (not run)](../evaluations/real_japanese/follower-spacing-report.md).
- **REAL-WORLD SEQUENTIAL OWNERSHIP EXTENSION VALIDATED — PASS.** On one edited history, the PR #71 next-page path
  and then the PR #70 same-page path extend ownership twice, each from the latest saved revision.

  Measured on LibreOffice pages 8→9 (original SHA-256 unchanged):
  - A page-8-only edit (A at 176 characters) refuses the 54-character growth. Adopting B on page 9 with release
    `release-9` lets A continue at 122 pt and moves B 142 → 148.4 pt, saved `restored`.
  - From that revision the 164 → 282-character growth refuses, because the unowned C bounds the region at
    200.271454 pt.
  - Appending C–H (886 glyphs, current IDs from 153, adoptions naming the grown SHA) keeps A, B, markers, the
    generated slot, destinations and fonts. The release owners stay exactly B, and stale-revision accepts
    refuse.
  - The no-op reproduces 1,238 glyphs and is pixel-identical on all ten pages in MuPDF and Poppler.
  - The refused growth then succeeds: A continues on two page-9 lines, and B–H move 20.400024 pt. B's ink reaches
    210.777155 pt and intersects 46 glyph bounds of the formerly unowned C.
  - Second edit, shortening, regrowth and the final no-op all reopen `restored`. Outside-region MuPDF and Poppler
    changes are 0, and foreign content is unchanged.
  - A three-page sequential next-page case passes on synthetic fixtures only.

  [Measured sequential report](../evaluations/real_japanese/sequential-extension-report.md).
- **REAL-WORLD CROSS-PAGE POST-EDIT EXTENSION VALIDATED — PASS.** A restored edited flow whose last source region is
  on page *n* adopts complete current owners on page *n*+1. It needs an explicit caller-selected same-page gap, a
  source-region floor and one explicit continuation authority (a named-owner release or an independently confirmed
  empty destination). The previously refused growth then continues onto page *n*+1 and moves the adopted followers.
  Existing owners and generated slots keep their identities.

  Measured on LibreOffice pages 8→9 (original SHA-256 `13665875…a5f3`, unchanged):
  - A page-8-only saved edit (A at 176 characters) refuses the 54-character growth.
  - Current-revision approval of B (146 glyphs, current IDs 0–145) with release region `release-9` is accepted.
    Occupied-as-empty and stale-revision requests refuse.
  - The extension no-op is pixel-identical on all ten pages in MuPDF and Poppler (322 glyphs, origin error 0).
  - The same growth then succeeds: A 206 characters = 199 on page 8 + `に役立てます。` at 122 pt on page 9. B moves
    142 → 148.4 pt, and its release is witnessed in the same Transaction.
  - Growth beyond the accepted capacity still refuses.
  - Second edit, shortening (continuation empty, B back to 142 pt), regrowth (same generated slot) and the final
    no-op all reopen `restored`.
  - Outside the edited regions, MuPDF and Poppler changed pixels are 0, and foreign content is unchanged.

  [Measured next-page extension report](../evaluations/real_japanese/next-page-extension-report.md).
- **REAL-WORLD POST-EDIT OWNERSHIP EXTENSION VALIDATED — PASS:** LibreOffice pages 8–9 start from a
  saved/restored edited A+B flow with an existing generated continuation. The larger replacement refuses;
  current-revision approval adds C–H (886 glyphs), preserving old owners, text/styles and generated identity.
  All 28 current lines reproduce; the extension no-op is pixel-identical in MuPDF and Poppler on all ten pages.
  The same growth then uses newly approved capacity and moves C–H by 20.400024 pt. Six further saves restore
  through second edit, shortening, regrowth and no-op; both exterior raster comparisons are zero and the original
  SHA is unchanged. Native source Tm preservation fixes a measured no-op precision loss without relaxing checks.
  [Measured restored-extension report](../evaluations/real_japanese/post-edit-extension-report.md).
- **REAL-WORLD EXTENDED JOINT REFLOW VALIDATED — PASS** for LibreOffice migration pages 8–9.
  A+B alone refuse the long replacement at the C boundary (region bottom 200.271454 pt). Explicit extension
  adds C–H as six independent source owners (886 glyphs), re-proves all 27 original lines and qualifies the
  larger body region. The same edit then moves B's ink beyond the old bound, intersecting 46 original C
  glyph bounds while C moves safely. Five saved revisions restore, both raster engines preserve the exterior,
  and the source SHA is unchanged. [Measured extension report](../evaluations/real_japanese/extended-joint-report.md).
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
- **REAL-WORLD JAPANESE FONT COVERAGE EXPANDED — PASS** on the same Okinawa body.
  Source subsets are discovered automatically; an explicitly selected OFL BIZ UDMincho provider supplies
  characters absent from the source subset. Both saved revisions restore, including B4 wrap/push-down.
  Retained codes, GIDs and font program bytes remain original; added glyphs have the selected provider
  outlines, with no claim of source appearance equivalence. [Font coverage report](../evaluations/real_japanese/font-coverage-report.md).
- **REAL-WORLD VACATED-SPACE REFLOW VALIDATED — PASS:** LibreOffice pages 8–9 explicitly own the complete
  innovator paragraph A and all seven following adopter-category/conclusion paragraphs B–H, as eight distinct
  source owners. All 27 original lines/glyphs reproduce. A grows 164 → 282 characters, four → six lines;
  its second continued line occupies B's original glyph space, beyond the earlier one-line empty prefix.
  B–H move 26.800024 pt in the first edit, preserving observed follows gaps and 1032 original follower glyphs.
  Conditional authority binds B's current source ownership to its removal and safe final placement in the same
  Transaction; the ordinary empty-destination check still refuses the occupied region. Original no-op and five
  lifecycle saves restore, including second edit, shorten, regrow and no-op; all exterior pixels and foreign
  content remain unchanged. [Measured vacated-space report](../evaluations/real_japanese/vacated-space-report.md).
- **REAL-WORLD CROSS-PAGE PARAGRAPH REFLOW VALIDATED — PASS:** LibreOffice pages 8–9 contain independent
  innovator/early-adopter paragraphs (164 and 146 characters). An explicit source-page-break floor and caller
  same-page gap preserve all seven original lines/glyphs. A grows to 206 characters, from four to five lines,
  using its separately confirmed empty page-9 prefix; B moves 6.4 pt in its own source region, retaining all
  146 original glyphs in the first edit. Five saves restore through second edit, shorten, regrow and no-op.
  All ten pages' foreign content is preserved, MuPDF/Poppler exterior changes are zero, and the original SHA
  is unchanged. [Independent page-break report](../evaluations/real_japanese/page-break-report.md).
- **REAL-WORLD CROSS-PAGE OWNED BODY EDITING VALIDATED — PASS:** the same LibreOffice paper already
  contains paragraph A on pages 3 and 4 (162 + 102 characters), followed by independent paragraph B (197).
  Joint B1 acceptance proves all nine original lines/glyphs and preserves three distinct source owners.
  A grows 264 → 284 characters, its page-4 part gains a line, and B moves 22.200012 pt while keeping
  its 28.200012 pt follows gap and all 197 original glyphs. Five saves restore through grow, follower edit,
  shorten, regrow and no-op; all ten pages' foreign content and outside-region raster stay unchanged.
  [Cross-page report](../evaluations/real_japanese/cross-page-report.md). This joins an existing page-spanning
  paragraph and its observed follower; unobserved gaps require the explicit source-page-break policy described below.
- **REAL-WORLD OWNED BODY REFLOW AND CONTINUATION VALIDATED — PASS:** the LibreOffice migration paper's
  page-9 final two body paragraphs reproduce six complete source lines. Extending the first paragraph
  from 107 to 474 characters makes eleven lines; the existing follower moves 163.199707 pt, preserving
  its 26.5 pt paragraph gap, with two lines on page 9 and its remainder on confirmed page-10 space.
  Five saves (grow, edit the continued follower, shorten, regrow, no-op) restore; outside-region pixels
  remain unchanged, and shortening removes the next-page text completely.
  The same section's explanatory paragraph and conclusion are explicitly selected together; unrelated
  paragraphs, logos and the next section remain foreign. [Owned-body report](../evaluations/real_japanese/owned-body-report.md).
- **REAL-WORLD MULTI-REGION PDF EDITING VALIDATED — PASS** for Kyoto City's unmodified Q&A PDF.
  The final two lines of the page-1 shipping note grow to three lines plus one on page 2, above the
  untouched next section. The caller explicitly confirms that region and its nonidentity-CTM boundary.
  Five revisions (grow, generated-slot edit, shorten, regrow, no-op) restore; region-exterior raster is
  identical throughout. This is **BOUNDED caller-confirmed continuation**, not automatic destination discovery.
  [Measured continuation report](../evaluations/real_japanese/continuation-report.md).
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
→ propose_page_flow(pdf, page, line_ids=...)  optional observed target; source unchanged, derived font cache
→ accept_page_flow(pdf, proposal)          the one explicit act; returns the unchanged pdfengine-shared-flow-2 state
→ optional continuation review → explicit destination confirmation → confirm_shared_flow_continuations(...)
→ replace_in_flow(state, find, replacement)   edit request only; writes nothing
→ edit_shared_flow(...)                    the existing T2 writer: Transaction, ownership, one save, one verification
→ open_shared_flow(...) == restored        → next replace_in_flow / edit_shared_flow
```

The continuation attachment is a module API; the ordinary B1 CLI is: `propose-page [--line ID ...]` → `accept-page` → `replace-text` (README).
Target IDs come from `inspect_selection_source`; they must be distinct, consecutive observed lines in page order.
Targeting chooses only the candidate text. Every unselected line and paint stays foreign, including content
elsewhere on a multi-column/table page. All width, region, source, font and reproduction guards still run.

| Stage | Code | What it now does without hand-written input | What still needs the caller |
|---|---|---|---|
| Observe | `backend.extract_page`, `selection.observation_lines`, `inference._line_groups/_paragraphs` | Lines, paragraph candidates, the main column, foreign content (other text, paths, images) | — |
| Propose | `page_proposal._build` | Paragraph selections, column x, available width (§3.1), region bounds, line pitch, first-line indent, follows gaps, logical styles, metric-verified font providers | Review; optionally choose consecutive observed lines |
| Verify the proposal | `page_proposal._reproduce` → `shared_flow.plan_shared_flow(state, {})` | The exact T2 state is built and planned with no edits. It must reproduce every observed line break, baseline, line width and painted glyph origin, or the proposal is refused. | — |
| Accept | `accept_page_flow` → `confirm_story` → `confirm_shared_flow` | Rechecks the binding, recomputes the proposal, applies explicit choices and overrides, and returns the T2 state | One call; choose ambiguous providers or explicitly approve a new-glyph substitute |
| Edit | `replace_in_flow` → `edit_shared_flow` | Finds the unique occurrence across the flow, takes its single logical style, and writes through the unchanged T2 path | Find and replacement text |
| Reopen and continue | `open_shared_flow` | `restored`, or `needs_confirmation` (fail closed) | — |

The semantic layer (T3, `shared-flow-3`) is unchanged. It still admits only `A`, `B`, space and newline, and it is
not on the replacement path.

### 2.1 What the runtime builds automatically for a PDF opened for the first time

For untagged or supported tree-backed tagged horizontal text in one column, either inferred from the page or selected by observed line IDs, with metric-qualified source/installed programs or an explicitly selected new-glyph substitute on the retained path, it builds everything
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
  - Observed lines give a lower bound: every observed line must fit. For interpreted zero-Tc/Tw horizontal
    text, source-occurrence-bound PDF operator advances define the pen end. MuPDF trace rectangle widths
    can differ from rounded PDF /W advances; they remain geometry evidence, not replacement pen metrics.
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
  requires source adjacency and an origin inside every accepted region's bounds. Width comes from
  the existing wrap interval, line pitch from the witnessed continuation, and follows from source baselines.
- **Fonts.** Candidates come from the installed-font roots of the platform (Windows `%WINDIR%\Fonts` and the per-user
  font folder; macOS system, library and user folders; Linux XDG data dirs and `~/.fonts`), or from injected
  roots or files. Default discovery also extracts unchanged embedded programs into a content-addressed
  cache; explicit inventories opt in with `include_embedded_fonts=True`. A metric provider qualifies only
  when, for every observed glyph of that font program, all of these hold:
  - the glyph is present with an outline;
  - its em advance equals the embedded program's `hmtx` advance (exact rational);
  - its PDF `/W` (or `/Widths`) equals `hmtx × 1000 / upem` within half of the written decimal quantum.

  The last rule is a derived quantization bound, not a tuning tolerance.

  The existing `ShapedFont` writer must also accept the font. Names only order the verified candidates.

  | Verified candidates | Result |
  |---|---|
  | Byte-identical embedded source program among qualified candidates | Preferred automatically |
  | One other candidate, no new-text substitution requested | Proposed under existing metric contract |
  | Several | `needs-choice`: the caller names one by SHA-256 |
  | None | `unresolved`, unless an explicitly chosen new-glyph substitute qualifies |

  `new_text={logical_style: proposed_unicode}` additionally inventories actual coverage, HarfBuzz shaping,
  visible outlines and editable embedding rights. A different program is never auto-selected in this mode,
  even when its advances match. Acceptance requires SHA + face + `relation=substituted`; the resulting
  source-adjacency policy retains unchanged glyphs and passes the same no-edit proof. The logical style
  records selection provenance, SHA, face, serialized instance SHA, checked text and the absence of a
  source-appearance equivalence claim. These identities and generated resources are rechecked on reopen.
  The CLI exposes `--new-text`, `--font-cache`, `--include-embedded-fonts` and `--substitute-provider`.
  Derived source programs and external providers must remain available at their recorded paths; missing
  or changed bytes give `needs_confirmation`. Existing v2 states remain supported; old proposals must be regenerated.
- **Reproduction.** The candidate T2 state, planned with no edits, must reproduce every observed line start exactly,
  and every baseline, line width and painted glyph origin within the unchanged `elements._close` tolerance.
  Glyph occurrences are matched by source Unicode offset, not nearest position or text search.
  Opposite interior gap errors cannot cancel merely because line widths match. Layout-trimmed spaces keep
  their logical Unicode; non-whitespace glyphs cannot disappear.
- **Irregular source spacing.** With observed zero Tc/Tw and irregular source gaps, the proposal exposes
  `text_placement=preserve-source-adjacency`. Explicit acceptance records this optional paragraph policy in
  the v2 contract, with default left layout. Current owned slots can span explicitly confirmed regions.
  Validated logical edits project occurrence offsets into each allocated fragment: only glyphs already owned
  by that current physical slot retain source codes/resources and same-source-line adjacency through
  `ParagraphShaper`. Text crossing a slot boundary uses the confirmed provider; source resource names
  never confer destination drawing authority. Saved generated slots become current sources on reopen. Replacement text and new boundaries use qualified providers and
  nominal/source inline metrics. Gap adjustments are not transported across an edit or a newly wrapped edge.
  The source is re-observed on every revision; no unchecked gap array is trusted from a sidecar. Existing
  real Okinawa items (7)/(8) demonstrate wrap/push-down in a jointly accepted region. Kyoto demonstrates
  cross-page continuation, local generated-slot re-edit and dormant-slot deletion/reactivation. Tagged
  source adjacency still cannot cross independently owned continuation regions.
- **Unequal source leading.** The retained path also recognizes two complete, unchanged, consecutive source
  lines by occurrence identity and obtains their baseline advance from the current PDF. Only that vertical
  edge is retained. A replacement, a changed wrap or a slot transition uses the confirmed ordinary minimum
  line height, proposed as the smallest observed pitch for this source-adjacency paragraph. Both that floor
  and the previous descent plus next ascent remain hard minima, and all region/ink checks run.
  This reproduces LibreOffice's 21.6/20.4 and 20.4/20.5 pt line gaps without positional sidecar arrays or a new
  layout engine. The shared allocator, local planner and writer all use the same shaper witness.
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

- one page for intent-only B1; explicit cross-page source joins or source-page-break policies through `owned_flow_proposal`;
  an explicitly reviewed and confirmed additional empty region for bounded continuation;
- horizontal text in one column of non-overlapping paragraphs;
- untagged, or balanced page-level `/P` MCID scopes whose complete ordered bundle belongs to one leaf `/P` StructElem;
- left-aligned naturally spaced lines, or individually reproduced zero-Tc/Tw irregular source adjacency;
- metric-qualified source/installed programs, or an explicitly selected covering TrueType provider for new glyphs
  with witnessed zero-Tc/Tw retained-source placement;
- no foreign content inside the column band.

The title, header, footer, images and other text are fixed, and the fixture shows their pixels unchanged after two
saves.

## 5. Remaining ordinary-PDF boundaries (all fail closed)

| Boundary | Where it is refused |
|---|---|
| Tagged structures outside B2: unrelated owners, Span/run trees, MCR children, ActualText/OC, editable Artifact, extra properties, nested scopes, split lines, Forms, malformed trees or nonzero object generations | Structural proof refuses; no stripping, repair or tree mutation |
| Spacing not reproducible by qualified natural shaping or the witnessed-adjacency path; varying/unknown tracking | `layout-not-reproduced` or `shared-flow-confirmation`; no automatic justify |
| No covering, embedding-permitted TrueType new-glyph provider; unsupported CFF/OTF provider; no reproducible retained path | `unresolved` / qualification refusal; no silent fallback |
| Several columns/tables inside the selected candidate, or foreign content inside its region | `multiple-columns`, `region`; explicit target lines can isolate a safe heading/body elsewhere |
| Growth past all explicitly accepted regions | Existing T2 overflow refusal; continuation requires independent destination authority |
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
- Source font programs remain byte-identical and pass unchanged metric qualification. The B3 path below
  discovers these programs automatically and separately authorizes new glyphs outside their coverage.

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

### B3 — New glyphs beyond embedded subsets: COMPLETE for the bounded TrueType path

- Production discovery inventories original embedded programs and OS fonts. Original programs are preferred
  only after unchanged metric qualification. No manual subset extraction is needed by the new real runner.
- An explicit SHA/face choice authorizes a different TrueType provider for new glyphs. Coverage, actual shaping,
  editable embedding rights and outlines qualify that provider independently of source metrics; equal metrics
  alone never prove equal appearance. Unchanged source glyphs keep their codes, GIDs, resources and adjacency.
- Okinawa's 346-codepoint Japanese subset lacks the added characters. OFL BIZ UDMincho supplies them, with
  two restored revisions and actual body wrap/push-down. Independent output audits compare original resources
  and codes for retained glyphs and selected-provider outlines/advances for new glyphs.
- SHA, face, instance and explicit-choice provenance survive save/reopen; absence, mutation or inconsistent
  generated identity requires confirmation. No cache/provider file is bundled into the sidecar.
- Remaining font boundaries: CFF/OTF new-glyph providers, unsupported shaping, nonzero-Tc/Tw substitution,
  and portable provider recovery. These do not block the demonstrated TrueType source-retention path.

### Verified-region continuation: COMPLETE for the bounded untagged source-adjacency path

- `confirm_shared_flow_continuations` attaches caller-supplied, already confirmed destinations to an
  accepted uncomposed v2 flow. It revalidates source/program authority, preserves existing ownership,
  and requires exactly unchanged no-edit fragments with no new slots. No inferred whitespace is authority.
- The existing allocator, paragraph writer, generated-block ownership, Transaction and readback are reused.
  Per-slot edits retain only current local occurrences; cross-slot text is generated with the accepted provider.
- Shortening empties the existing generated block; regrowth reuses its identity. The Kyoto lifecycle proves
  restored reopen five times, source-retained identity, CTM compensation and zero foreign raster changes.
- Bounds, clip, stale ownership, output ink, capacity and tagged structure guards remain. No new page,
  foreign-content movement or mid-composition destination attachment is implemented.

### Cross-page occupied-body bootstrap: bounded source-owner composition

- `propose_owned_flow` accepts sealed B1 page proposals plus ordered logical paragraph/part groups and a
  caller-declared textual relationship. Each page must explicitly select observed lines; none is an empty destination.
- `accept_owned_flow_report` rechecks each page and its font inventory, requires explicit per-page provider
  substitution choices, and composes ordinary `confirm_story` / `confirm_shared_flow` arguments.
- Original containers remain distinct source owners. Logical style IDs are page-namespaced; source resource
  names and GIDs are never copied to another page. Cross-slot glyphs use the explicitly qualified provider.
- Joint no-edit reproduction checks each slot's exact original text and breaks, baselines, widths and every
  painted glyph origin. Independent successful page proofs alone are insufficient.
- The same allocator, source-adjacency writer, follows, current-revision ownership, Transaction and readback
  perform edits. No new writer, layout engine, persistence schema or generated empty slot is introduced.
- The [cross-page evaluation](../evaluations/real_japanese/cross-page-report.md) records the real scope;
  [regressions](../tests/test_cross_page_owned_flow.py) cover lifecycle, stale ownership, invalid groups,
  interrupted seams, joint reproduction and atomic over-capacity refusal.

### Independent source-page paragraphs: explicit region floor and same-page follows

- `page_breaks` binds the independently owned paragraphs to their original source regions on consecutive pages.
  The caller chooses the same-page gap; different pages' y coordinates are never subtracted to invent one.
- T2 retains `break_before=next-region` and adds a source-region floor to the follows edge. No-edit keeps B's
  original page start; after A continues onto B's physical page, the gap can push B down within its own region.
- Prefix continuation has separate existing review/confirmation authority. B's region can be narrowed through
  qualified B1 geometry overrides to keep the regions disjoint. Occupied B text is never an empty destination.
- The optional follows contract is persisted in the existing v2 model and rechecked against independent source
  owners and actual saved occupancy. Original edge behavior and all writer/Transaction guards are retained.
- [Measured independent-paragraph result](../evaluations/real_japanese/page-break-report.md) and
  [ten regression cases](../tests/test_page_break_reflow.py) cover wrap/push-down, five restored saves,
  dormant-prefix cleanup, missing authority, insufficient capacity and invalid page-break contracts.

### Extending original accepted joint ownership

- `propose_owned_flow_extension` takes an accepted, uncomposed source flow plus enlarged explicit B1 selections.
  Old logical paragraphs must remain an unchanged ordered prefix with the same page/glyph/text identities;
  additional complete followers are named individually. A larger rectangle with unchanged owners is insufficient.
- The sealed review shows added text, lines, glyph IDs, bounds and baselines, old/new region proposals,
  geometry overrides, page-break and continuation policies, and the caller-declared textual relationship.
  Proposal creation does not alter the original accepted flow or authorize new paint.
- `accept_owned_flow_report` restores the base against the current source and recomputes the review, then
  runs the existing per-page B1/font checks, source-owner composition and joint no-edit reproduction.
  Its receipt connects the original model, reviewed additions and resulting shared-flow model.
- T2 allocation, follows, source adjacency, conditional owner-removal obligations, Transaction and readback
  are reused unchanged. This original-source route remains available alongside the restored-revision route below. [Measured extension report](../evaluations/real_japanese/extended-joint-report.md).

### Extending a restored edited revision

- The same `propose_owned_flow_extension` entry point accepts a restored edited state, a B1 selection of
  **new followers only**, and `append_to_region` naming the last existing source region. This bounded path
  adopts complete consecutive paragraphs on that page; it does not reinterpret existing generated text as source.
- The sealed review binds the current PDF SHA/model, new text/glyph IDs and qualified region, explicit textual
  relationship, and the **currently observed** last-to-first baseline gap. Acceptance revalidates these witnesses.
- Existing logical text/styles, source-output creation and marker identities, generated slot IDs, destination
  witnesses and generated-font ownership are carried forward. Only confirmed capacity metadata changes.
  Newly adopted source slots have current-revision creation records, sealed into the shared contract. They
  may coexist with owned output markers until their first save initializes their own source-output blocks.
- Standalone B1 reproduction is deferred to mandatory **joint** reproduction, because a standalone new owner
  cannot claim the existing marker inventory. Font qualification, glyph coverage and foreign-content guards
  still run; every old/new glyph and every current line must reproduce before acceptance returns.
- The ordinary T2 writer and Transaction remove/rewrite each named owner, retain conditional release obligations,
  and rebind source/generated ownership after saving. Stale evidence, overlapping owners, skipped text, new
  no-op continuation slots, changed no-op positions and insufficient capacity refuse.

### Extending a restored edited revision onto the next existing page

- The same entry point takes `next_page` instead of `append_to_region`. The B1 selection names complete
  paragraphs on the page **after** the last paragraph's last source region; that page must hold no confirmed
  region or destination yet. Owners, Unicode, glyph IDs and baselines are re-observed in the current revision.
- The review witnesses the current page seam: every line partly owned refuses; the last owned line on page *n*
  must belong to the last paragraph; unselected text below it, or above the first adopted line on page *n*+1,
  in the column band refuses. It records the seam lines but **never subtracts different pages' y coordinates**:
  the edge uses the caller's `page_break` gap with `source-region-floor-and-same-page-follow`.
- Continuation authority for the last paragraph is explicit and optional. `vacated_continuation` releases only
  adopted next-page owners and is re-proved by `_confirm_destination` on the current revision (geometry,
  emptiness except those owners, boundary, graphics state, CTM, clip). `continuation` takes an empty destination
  confirmed on the current revision. Without either, the extension adds owners but the growth still refuses.
- Acceptance runs the full standalone B1 proof of the new page and the joint no-edit proof of every current
  line. Existing slots, bindings, source-output identities, destinations, destination bindings and generated fonts
  are unchanged; only the new region, owners, policies, follows and destination are added.
- A released adopted owner is uninitialized until its first save; the vacated-space proof now carries its
  sealed `source_adoptions` record, so its removal can be proved in the same Transaction before that save.
- Refusals: stale revision, changed review, non-finite gap, skipped seam text, release of an existing owner,
  an unreleased owner or fixed paint inside the continuation region, missing continuation authority and
  growth beyond the accepted capacity. Failures publish neither PDF nor sidecar.
  [Regressions](../tests/test_next_page_extension.py).

### Sequential extensions on one restored edited history

No new API is involved. The existing `next_page` and `append_to_region` extensions can be chained on the same
history, each from the latest saved, restored revision; nothing is re-accepted from the original.

- After a `next_page` extension, the flow's last region is the adopted next-page source region. A later
  `append_to_region` on that region adopts the following current owners on that page, and a later `next_page` adopts
  owners on the page after it.
- Each extension re-observes the current revision:
  - the new owners take current glyph IDs, after the generated continuation glyphs;
  - their `source_adoptions` name the revision they were approved on;
  - the review is bound to the base model. Accepting the proposal on the original PDF or on an earlier saved
    revision refuses as a stale revision.
- Earlier owners, source-output markers, generated slots (and their creation bindings), destinations, destination
  bindings and generated fonts are carried unchanged.
- **Release authority is never widened.** An existing continuation keeps exactly the release owners it was confirmed
  with. A newly adopted owner is not added to that set.
- Without a placement policy, a newly adopted follower uses its **current** observed gap (the PR #70 rule). If the
  paragraph above it has already moved down toward it, that gap is compressed. Shortening later moves the follower
  above its original position. When that would place it inside an existing release region, which only the named
  owners may occupy, the edit refuses and publishes nothing. This is fail-closed, and it is pinned by a regression.
  An approved follower placement (below) avoids this.
- A further `next_page` gets its own explicitly confirmed continuation destination and release owners on the new
  page. Each continuation releases only the owner it names. Occupied text is never treated as empty space, and the
  released owner's removal is proved in the same Transaction as its first save.
- [Regressions](../tests/test_sequential_extension.py).

### Approved follower placement for `append_to_region`

The current placement and the future placement policy are reviewed separately. Neither is inferred from an earlier
revision or from the original PDF.

- `append_to_region` still takes a region ID (the PR #70 current-gap rule). It now also takes exactly
  `{region_id, incoming}`, where `incoming` is
  `{before, after, minimum_baseline_gap, baseline_floor, placement='approved-baseline-floor-and-same-region-follow'}`.
  - `before` must be the last confirmed paragraph and `after` the first adopted follower.
  - The gap and floor must be positive and finite.
  - The floor is a page coordinate in that region, from its first baseline to its bottom (after the extension).
  - No other key is accepted; in particular, no release owner can be added through this request.
- **Current witness.** The review records the last owned baseline, the follower's current first baseline and their
  gap on this revision.
- **Approved policy.** The follower's first line is placed at `max(previous last baseline + gap (and descent + ascent),
  floor)`. A policy that does not place the follower exactly where it is now refuses: the extension no-op would move
  it. Examples are the original larger gap, a floor below with a smaller gap, or a floor above the current position.
- **T2 contract.** The edge is stored as `baseline_floor: {region_id, baseline}` on the sealed `follows` relation.
  - The allocator `_follow_baseline` and the validator `_verify_placement` apply it only to the paragraph head in
    that region.
  - `_policies` refuses a floor outside the follower's own first source region, outside the region's capacity, or on
    a source page-break edge.
  - A floor only lowers the follower. It creates no capacity or paint authority, and release owners stay unchanged.
  - Collision, capacity and Transaction guards are unchanged. A lower floor that still lets the follower enter a
    release region refuses exactly as before.
- **Synthetic lifecycle (PR #72 fixture, 116 pt release region).** D is approved with its current 126.9 pt baseline
  as floor and an 18 pt gap, and the following stages all pass:
  1. The extension no-op is pixel-identical.
  2. Shortening returns C to 80 pt and keeps D at 126.9 pt; the PR #72 rule would put D at 118.4 pt and refuse.
  3. Regrowth pushes D to C's last baseline + 18 pt.
  4. A second edit, a second shortening and the final no-op all save and reopen `restored`.
  5. Markers, the generated slot's creation binding, destinations and follows are unchanged, and the exterior raster
     is equal.
  6. Growth beyond capacity refuses.
- **Refusals.** Non-finite, zero or negative gap or floor; an unapproved placement, edge or request shape; extra
  release keys; a policy that moves the no-op; a floor outside capacity; a stale base or stale acceptance revision;
  modified review evidence; a request that no longer matches its review; a tampered floor region or a floor on a
  page-break edge. Failures publish neither PDF nor sidecar.
- [Regressions](../tests/test_sequential_extension.py).

### B5 — Real text in the semantic layer (T3)

Not on the replacement path; decorations, publication and style reinterpretation on real text come after B3.

## 7. Dependency graph

```
B1 bootstrap (COMPLETE)
├→ B2 same-owner tagged ownership (COMPLETE, narrow subset)
├→ B6 witnessed real-body source adjacency (COMPLETE, bounded)
├→ B3 explicit new-glyph coverage (COMPLETE, bounded TrueType path)
├→ B4 real body wrap + following-paragraph capacity (COMPLETE, bounded)
├→ verified existing-region continuation (COMPLETE, bounded untagged path)
├→ cross-page source paragraph joins (COMPLETE, bounded untagged path)
├→ independent source-page paragraph reflow (COMPLETE, explicit floor/gap)
├→ joint-owner vacated-space continuation (COMPLETE, explicit fixed capacity and Transaction obligations)
├→ explicit additional-owner approval from original source (COMPLETE, append-only followers)
├→ current-revision additional-owner approval (COMPLETE, bounded last-region followers)
├→ current-revision next-page owner approval (COMPLETE; real-PDF VALIDATED, LibreOffice pages 8→9)
├→ sequential extensions on one restored history (COMPLETE: next page then same page; three-page next pages on
│  fixtures)
└→ approved follower placement for append_to_region (COMPLETE on fixtures; real-PDF evaluation NOT run)
        B3 → B5 semantic real text
```

Real Office coverage additionally requires broader tagged structure patterns;
that boundary is not solved by B6 or B3.

## 8. NEXT BLOCKER

```
NEXT BLOCKER: real-PDF validation of the approved follower placement (LibreOffice pages 8–9). The evaluation
script exists; the source PDF host was unreachable from the implementing session
```

The follower spacing policy is implemented and pinned on synthetic fixtures (§6). It still has to be measured on the
real PDF:
- after shortening, C should stay at its approved 209.299988 pt floor; under PR #72 it was 202.800012 pt;
- the extension no-op should be pixel-identical in MuPDF and Poppler.

The policy keeps at least the approved gap when the paragraph above moves down. It cannot restore an original gap
larger than the current one without moving the follower in the no-op, so such a policy refuses.

A third page is covered only by synthetic fixtures; no real three-page continuation of a related body has been
validated. Capacity remains explicit and bounded:
- adoption covers only the next page after the last source region, and no page already holding regions;
- there is one release/empty continuation, for the last paragraph.

The implementation does not:
- choose related paragraphs automatically;
- add pages;
- change an existing continuation's release-owner set;
- resolve cyclic source-occupancy dependencies.

Tagged source owners cannot cross independent continuation regions.

## 9. Next PR scope

1. Run `python -m evaluations.real_japanese.follower_spacing --output tmp/follower --font <BIZUDMincho-Regular.ttf>`
   alone, with the pinned font (SHA-256 `468ee6d9…`), and record the result.
2. Then validate a real three-page related body if a suitable document is found.

Do not convert occupied text into an empty destination or infer document-wide relationships. Page 10 of the
LibreOffice paper is a separate section and stays foreign.

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
Current continuation evidence, regression coverage and reproducible commands:
[continuation report](../evaluations/real_japanese/continuation-report.md).
Whole owned-body evidence and final-HEAD commands:
[owned-body report](../evaluations/real_japanese/owned-body-report.md).
Final full-suite counts and the exact tested commit are recorded in the current PR.
