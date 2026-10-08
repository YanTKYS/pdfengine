# B2: structure-preserving persistent tagged editing

Starting main: `b395150d41e2cbddfb10b15d0eaeb9f5db202029` (PR #57).
Branch: `codex/tagged-persistent-editing`. Implementer: GPT-6 Astra.

The B1 proposal/accept route now admits a deliberately narrow tree-backed
paragraph contract. The existing T2 allocator, paragraph writer, mutation
planner and Transaction perform replacement, wrapping and push-down. No
StructureTree writer, MCID allocator, repair pass or T2 schema upgrade exists.

## Evidence and supported contract

`marked_content.observe_marked_content` remains the only structure observer.
Its new `paragraph_structure` proof requires:

- Balanced, unnested page-content `/P <</MCID n>> BDC ... EMC` envelopes;
  inline or named properties must resolve to exactly the MCID property.
- Nonnegative MCIDs unique in their page namespace; one ParentTree lookup
  and exactly one owner `/K` backlink per item.
- One indirect leaf `/P` StructElem for the entire paragraph. Its `/K` is
  precisely the ordered integer MCIDs (a scalar integer is also accepted).
- A valid, singly backlinked ancestor chain reaching `/StructTreeRoot`,
  with PDF Name objects for types and roles. Parent child indices are bound,
  so changing logical sibling order changes the structural identity.
- No selected unmarked span, Form invocation, scope splitting a visual line,
  foreign/unproven glyph or non-text paint inside an owned envelope.
- No ActualText, OC, Artifact, extra marked-content property, nested scope,
  semantic replacement/expansion, structure namespace, or layout attribute
  affecting the paragraph/ancestors. Unsupported patterns are refused.
- Generation-zero source references for persistent tagged saves.

The bundle records ordered scope IDs, byte ranges, original MCIDs, tags,
namespace, StructParents, owner references/roles, ordered ancestors and
backlink evidence. `identity_sha256` excludes changing stream locations;
`bundle_sha256` includes current ranges. These are evidence, never permission
to own text. Existing selection, source-replay/removal proofs, ownership,
protected-content checks and Transaction remain mandatory.

## MCID policy and writer

Option B is used: **all new text goes into the first existing MCID envelope;
the other existing envelopes remain as empty structural sequences**. This is
safe only because the whole ordered bundle is the complete content of one
leaf paragraph owner with no semantic or layout overrides. Distinct owners,
mixed child StructElems and MCR dictionaries remain unsupported.

Option A would need a new stable fragment-to-scope allocation convention and
additional boundary shaping proofs without providing more semantics for this
same-owner subset. Arbitrary unrelated owners cannot use Option B: moving
their text into one owner's MCID would change logical ownership. They refuse.

Every original BDC/EMC token and property representation stays untouched.
The existing source island is inserted inside the first scope. Its body is
still the exact canonical `q BT ... ET Q` group grammar; arbitrary marked
nesting is not added to the body grammar. The old text operators become
nonpainting numeric TJ displacements, preserving surrounding source state.
Later saves replace only the proven island body, so wrappers do not grow.

There is no relation between visual line count and MCID count after reflow:
one MCID can contain two or more visual lines; a two-MCID paragraph can shrink
to one line with its second sequence empty. Empty paragraphs retain the
existing nonpainting insertion/style witnesses and can be typed into again.

`attributed.py` removes marked history from its **comparison only after this
proof succeeds**; every other non-inline state and clip still matches exactly.
If the proof fails, existing one-shot editing retains its previous exact-state
path. Persistent ownership independently requires the proof and refuses.

`source_ownership.context` admits marked depth one only through a verified
bundle path. Initial creation, every current witness, rebind and reopen
reobserve the PDF. The current record binds the whole structural bundle plus
the original program/block/range/context evidence. Untagged records retain
their exact fields; `pdfengine-shared-flow-2` and source record version 1 stay.

One save-adapter change was necessary: ordinary pypdf traversal cloning
renumbered the tree after generated fonts entered page resources (observed
owner xref 15 became 34 on the second edit). Pinned pypdf 6.10's public
`PdfWriter(reader, full=True)` preserves existing IDs while producing a full
rewrite. It is used for generation-zero tagged documents; ordinary untagged
and unsupported-generation legacy one-shot saves keep the old path. Existing
reachability pruning still removes superseded resource/stream objects. This
is not incremental saving, structure mutation or a second writer.

## Proposal, acceptance and persistence

Each supported proposal paragraph exposes `marked_content` with status
`supported-tree-backed`, MCIDs, roles, owner references and bounded evidence.
Unsupported structures produce explicit refusal details. The schema remains
`pdfengine-page-flow-proposal-1`. Acceptance recomputes all evidence from the
current PDF; a re-sealed forged proposal is still rejected. The unchanged
`plan_shared_flow(..., {})` reproduction gate verifies original line breaks,
baselines and widths before acceptance.

## Representative fixture and checks

The generated A4 Japanese fixture retains B1's generated TrueType font,
title, three body paragraphs and footer. Its structure is:

```
StructTreeRoot -> Document -> P title [0]
                            P body1 [1, 2]
                            P body2 [3]
                            P body3 [4, 5]
                            P footer [6]
page StructParents = 0
ParentTree /Nums [0 [title, body1, body1, body2, body3, body3, footer]]
```

Each owner has `/Pg` pointing to the page and an integer-array `/K`; every
observed MCID is `tree_backed` with `backlink_verified = true`.

The production lifecycle test performs:

1. Propose, reproduce and accept; replace `申請書` with `各種申請書`.
2. Save, compare structure objects and refs, reopen `restored`.
3. Lengthen P2: one visual line becomes two (205/223 pt); P3 moves from
   232/250 to 250/268 pt, keeping the confirmed 27 pt interparagraph gap.
   P2 still has **one MCID, 3**.
4. Save, verify structure, reopen `restored`; shrink P1 from two lines to one.
   P1 still has **two MCIDs, 1 and 2**, the second empty.
5. No-op save; decoded page program stays identical. Empty P1, save/reopen,
   regrow P1, save/reopen again.

Every saved revision checks complete observation, exactly MCIDs 0–6, unique
tree-backed mappings, StructParents, parent links, and unchanged serialized
StructTreeRoot, ParentTree, Document and all five paragraph objects including
indirect references. All revisions have seven BDC/EMC pairs and three owner
markers. Title/footer pixel samples are identical. P1/P3 text and positions
are unchanged by the first edit; P3 changes only placement after push-down.
Repeated identical edits from the same revision produce identical programs
and source-ownership records. These are visual **and** structural checks.

## Refusal matrix

`tests/test_tagged_page_proposal.py` covers orphan MCIDs, absent ParentTree,
duplicate number-tree keys, bad StructParents, out-of-array MCID, invalid
StructElem, missing/duplicate/wrong-page backlink, missing ancestor backlink,
wrong ancestor/root types, missing role, string masquerading as name,
ActualText on BDC or owner, OC, Artifact, layout attributes, structure
namespace, reversed K order, MCR dictionaries, malformed BDC/EMC, nested
empty Artifact, duplicate MCID, extra properties, unrelated owners,
split visual line, Form XObject and different non-inline paint state.
Stale PDF proposals, re-sealed structural proposals, modified saved structure
and direct current-witness revalidation are tested. None repairs the input.

## Real Word evidence and limits

The read-only [probe](../evaluations/tagged/structure_probe.py) and
[results](../evaluations/tagged/structure-probe.json) ran on Windows against
existing local corpus files. No source PDF or font was added to Git.

| Source/page | Observation | B2 subset |
|---|---|---|
| Takeo notice p2 | 169 balanced scopes, orphan MCIDs, no tree-backed owner | Refused |
| Osaka guideline p1 | 97 balanced tree-backed scopes; direct integer K and arrays, per-run Span owners, ActualText | Refused |
| Osaka fire notice p1 | 47 balanced tree-backed scopes; P and Span owners, mixed integer/StructElem K, Lang and ActualText | Refused |
| Ubiquiti p1 | One tree-backed scope, NonStruct owner, MCR dictionary K | Refused |

These files confirm that MCIDs cannot be assumed to be paragraph identities
and that ActualText can live on StructElem, not just BDC properties. No whole
owner bundle in these four pages meets the deliberately narrow writer
contract. This PR does **not** claim these real documents are now editable,
or that the supported synthetic pattern's prevalence has been measured.

Windows fixture lifecycle validation and Windows real-corpus read-only
structure validation were performed. **WINDOWS REAL WORD TAGGED EDIT
LIFECYCLE VALIDATION NOT PERFORMED.** No Word application/export run was made.

## Validation and unchanged foundations

Set `PYTHONUTF8=1` (inherited by parallel workers). Run focused tests with `python -m pytest tests/test_tagged_page_proposal.py
tests/test_page_proposal.py tests/test_marked_content.py tests/test_source_ownership.py
tests/test_attributed.py -n 4 -q -ra`, and the complete suite with
`python -m pytest -n 4 -q -ra`.

The only existing B1 test edit normalizes discovered paths with
`Path.relative_to(...).as_posix()` so its exact file/order assertion works on
Windows. Production font discovery is unchanged and no test is skipped to
hide the previous slash-only assertion. UTF-8 mode handles existing Japanese
test sidecars. Windows sandbox hard-link restrictions required running the
existing atomic save tests outside the sandbox, within workspace temp paths.

T3, semantic measurement, decoration/publication behavior, Transaction,
layout allocation, B3 font qualification and B6 justification are unchanged.
Final test counts, commit and PR are recorded in the PR and final report.
