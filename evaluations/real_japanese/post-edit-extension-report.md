# Ownership extension from a restored edited revision

**REAL-WORLD POST-EDIT OWNERSHIP EXTENSION VALIDATED — PASS**

Starting main: `d5e27d28dbeb6fca547e5979ed5c826500bcba85` (PR #69 merged).

Source: the unmodified ten-page **LibreOfficeへの移行** migration paper.
URL: https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf
SHA-256: `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`.

## Current-revision transition

The initial ownership is only the complete innovator paragraph A on page 8 (164 characters) and the following
early-adopter paragraph B on page 9 (146 characters). C–H remain fixed foreign text. The initial acceptance
uses the existing page-break floor and 26.4 pt A→B gap, and the conditional `A-vacated` continuation whose
release obligation names B. The long replacement used in PR #69 does not fit this limited flow.

First, a shorter replacement grows A, creates its page-9 continuation and moves B. The PDF and sidecar are saved
and reopened `restored`. All subsequent extension operations start from **this edited revision**. They do not
recreate an enlarged flow from the original or replay the first edit.

C–H are six complete following paragraphs in the same communication-strategy section: subsequent adopter
categories and the section conclusion. The caller explicitly declares this textual relationship. Their current
line/glyph IDs, Unicode, fonts and geometry are observed again in the saved PDF. Page 10's new section, other
page text, logos, drawings and images stay foreign. The additional region comes from the qualified B1 selection;
capacity does not arise from an arbitrary larger rectangle.

## Initial saved edit and refusal

Replace `望むタイプでもあります。` (12 characters) with:

> 望むタイプでもあります。試行の結果を共有し、利用者の疑問や改善の提案を記録することで、次の移行に役立てます。

This 54-character replacement grows A from **164 to 206 characters**, four to five lines. Four lines stay on
page 8; seven characters continue on page 9 at baseline **122 pt**. B moves from 142 to **148.4 pt**, a **6.4 pt**
translation, with its last baseline at **189.300024 pt**. The saved revision SHA-256 is
`61f3650b1987c5f2ffe00660fe1105939151efcd73b401b50b4d1fff24d3be0e` and reopens `restored`.

Replacing that short addition with the 130-character text below refuses on this saved revision:
`paragraphs exceed all explicitly confirmed shared regions`. Neither attempted output is published.

> 望むタイプでもあります。試行の結果を共有し、利用者の疑問や改善の提案を記録することで、次の移行に役立てます。担当者は実際の文書を使って操作を試し、変更の理由と利用方法を説明します。得られた意見を整理し、部門間で知識を共有することで、組織全体の移行を支援します。

## Accepted additional ownership

The current page-9 selection is **p9-l5–p9-l24**, six independent owners C–H (147, 196, 147, 166, 107 and
123 glyphs; **886 total**). For example, C begins at current glyph ID **153**, not original glyph ID 146:
the seven already generated continuation glyphs precede it in this revision. Each new snapshot and source-output
creation record names the saved SHA above. Old source slots `slot-0`/`slot-1` and generated slot
`continuation-7ce532042f2745006082baf3` remain the same owners.

The page-9 source region grows from bottom **200.271454** to **781.299988 pt**, and its qualified width from
481.400002 to **485.364495 pt**. The old 142 pt source-region baseline floor remains. B→C uses the **current
19.999963 pt** observed gap, rather than the original 26.399963 pt gap before B's 6.4 pt movement. C→H gaps
remain their currently observed values. All **28 current lines**, including the existing continuation, reproduce
before acceptance returns. Existing A/B text/styles, source markers, destination witnesses and generated-font
records compare equal at acceptance.

The extension no-op saves and reopens `restored`, initializes only the six additional source-output markers,
and preserves all **1,238 current glyphs**: code, resource, font program, GID and origin (maximum origin delta
**0.0 pt**). Both **MuPDF at 144 dpi and Poppler at 1600-pixel page scale are pixel-identical on all ten pages**.
The old A/B marker identities and generated continuation identity remain unchanged.

## Same larger edit after extension

The previously refused replacement succeeds from the saved edited revision after acceptance. A grows
**206 → 282 characters**, five → six lines: 199 characters/four lines on page 8 and **83 characters/two lines**
on page 9 (baselines **122 and 142.400024 pt**). B moves from 148.4 to **168.800024 pt**, another **20.400024 pt**.
C–H move by the same amount; C starts at **229.700012 pt**, and H ends at **655.900024 pt**. A→B stays 26.4 pt;
B→C stays the newly accepted 19.999963 pt, and the other follows gaps/indents/source line spacing remain intact.

B's planned ink reaches **210.777155 pt**, beyond the previous **200.271454 pt** capacity boundary. Thus the
added capacity is actually used by existing text: saved B's actual bounds intersect **46 glyph bounds** of
previously unowned C, and its actual bbox bottom is **211.171539 pt**; this is not just empty-prefix continuation. The grow revision
reopens `restored`, with maximum glyph-origin error **0.000030517578 pt**, no foreign-content changes and zero
outside-region MuPDF changed pixels on all ten pages. The saved page-9 crop was visually inspected: the two
continued lines precede B, then C and the later paragraphs; indentation and paragraph ordering remain clear.

## Save/reopen lifecycle

After the initial limited-flow save, the extension no-op, larger edit, second edit, shortening and regrowth all
reopen `restored`. The second edit replaces B's `変化を肯定的` with `新しい変化を肯定的` (6 → 9 characters).
Shortening reverses both textual replacements: A returns to 164 characters/four lines, and the same generated
slot becomes dormant with no glyph IDs. Regrowth reactivates that exact slot, retaining its creation binding.
Every source-output marker's immutable identity remains unchanged across these saves.

The accepted **current** B→C gap remains 19.999963 pt. Shortening does not promise historical original positions:
B returns to baseline 142, while C is at 202.800012 pt. In addition to the initially accepted 6.4 pt gap change,
B's edited line boundary uses the existing 20.400024 pt minimum pitch instead of its original 20.5 pt edge.
Reversing Unicode does not recover discarded original codes or line-adjacency edges. Unchanged C–H glyphs retain
their source programs/codes/GIDs and their follows relationships.

All six post-extension lifecycle saves, plus the initial limited-flow save, reopen `restored`:

| Stage | Retained / provided glyphs | B first baseline (pt) | C first baseline (pt) | Maximum origin error (pt) |
| --- | ---: | ---: | ---: | ---: |
| extension-noop | 1238 / 0 | 148.400000 | 209.299988 | 0.000024414062 |
| grow | 1184 / 130 | 168.800024 | 229.700012 | 0.000030517578 |
| second | 1308 / 9 | 168.800024 | 229.600037 | 0.000030517578 |
| shorten | 1178 / 18 | 142.000000 | 202.800012 | 0.000036621094 |
| regrow | 1184 / 130 | 168.800024 | 229.600037 | 0.000030517578 |
| noop | 1314 / 0 | 168.800024 | 229.600037 | 0.000030517578 |

Every stage checks exact Unicode and glyph coverage, source-retained codes/resources/programs/GIDs, provided
outlines/advances, planned versus saved glyph origins/bounds, foreign text/drawings/images/links/annotations,
operator nesting, PDF version/page count and restored bindings. All ten pages have **zero exterior MuPDF pixel
changes** throughout. Final comparison against the unmodified original gives **zero exterior Poppler changes**
on all ten pages. The final no-op is also whole-document MuPDF pixel-identical. Source SHA-256 is unchanged.

The generated slot's creation binding and every source-output immutable identity remain constant through the
whole lifecycle. Source A/B styles and generated-font ownership were unchanged at acceptance and the extension
no-op. Shortening leaves no generated glyphs; regrowth reuses the same slot. Evaluation artifacts are under
`tmp/post-edit/validation/` (ignored), including summary, per-save reports, sidecars, renderer comparisons and crops.

## Production contract

`propose_owned_flow_extension` dispatches restored edited allocations to a bounded append path. The new B1
selection contains only the additional paragraphs; `append_to_region` names the last existing source region.
The sealed proposal records the current PDF/model hashes, additional text and positions, explicit relationship,
previous/proposed regions and currently observed B→C baseline gap. Proposing changes no authority.

Acceptance verifies the base and new B1 evidence again, imports only the new independent stories, and keeps the
old flow, logical text/styles, source-output identities, generated slot, destination bindings and generated-font
records. Region width/bottom metadata may expand after explicit acceptance. Existing snapshots and physical
layouts do not change. New source-output creation records name the current revision and newly measured snapshot;
`source_adoptions` binds these records into the shared contract. The first save initializes their own markers.

A standalone new B1 model cannot claim preexisting source-output markers. Consequently only standalone reproduction
is deferred: all font/selection/geometry qualification still runs, then **mandatory joint no-edit reproduction**
checks all existing and newly adopted glyph origins, line starts, widths and baselines. There is no tolerance
change or reproduction bypass. Generated slots never become new source slots.

The existing T2 planner/writer, conditional vacated-space obligations, Transaction and atomic PDF/sidecar readback
remain authoritative. Missing removal, overlapping owners, foreign collisions and insufficient capacity refuse.
A conditional region continues to name only its original release owners: adopting C does not silently add C to
B's release authority. A synthetic shortening case that would pull C into that region is explicitly rejected.

## Native no-op coordinate precision

Independent full-document Poppler comparison exposed a real precision blocker that MuPDF's raster did not show:
one retained character (`ら` on page 9) changed 85 pixels. Its original text matrix used **84.2, 452.4**, while
reconstructing coordinates from MuPDF's single-precision trace produced **84.1999969482, 452.399993896**.
The glyph code, font and observed float origin were equal, but the native PDF operands were not.

The existing paragraph writer now keeps the source event's native text matrix for its first retained character
when the planned origin is **exactly unchanged**, the CTM and rise are identical, and the matrix basis matches.
Moved glyphs and new text still use the ordinary planned matrix. This is a generic preservation rule, not a PDF
or character-specific exception; no tolerance changes and no no-op copy/save bypass were introduced. A minimal
regression asserts exact native `Tm` operands across save/reopen. All writes still use the original Transaction.

## Reproduce

```bash
.venv/bin/python -m evaluations.real_japanese.post_edit_extension \
  --output tmp/post-edit/validation \
  --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The runner supports `--resume` for its already saved initial revision and acceptance artifacts. This resumes the
same current PDF/model, never an original-source enlarged flow. The explicit new-glyph substitute is BIZ UDMincho,
SHA-256 `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`, face 0. Source-font appearance
equivalence is not claimed. No real PDF, third-party font or generated PDF binary is committed.

## Scope remaining

This transition appends complete followers on the last existing source region's page. Appending owners on a new
page/region, changing an existing continuation's release-owner set, cyclic movement and automatic semantic
selection remain outside its contract. Safety failures publish neither PDF nor sidecar. Full suite is intentionally
not run, following the task and `CLAUDE.md`.

## Focused regression tests

Final production code: **243 distinct focused cases passed**, zero failures, across these disjoint runs:

- Post-edit extension, source-adjacency flow/continuation, source ownership, attributed editing, spacing,
  alignment and paragraph CLI: **135 passed, 259.31 s**.
- Original ownership extension, vacated-space, cross-page owned flow, page-break reflow, shared flow,
  flow Transaction, Transaction and continuation: **107 passed, 153.83 s**.
- Additional adoption-certificate guard: **1 passed, 26.19 s**. Removing the certificate or naming a generated
  slot as an adopted source owner refuses, even with recalculated model hashes.

These runs include **11 new regression cases**. Refusals cover stale PDF revision, changed review, duplicate
generated slot, overlap with an existing owner, modified source evidence, skipped unselected text, explicit header
protection, capacity overflow and an unauthorized conditional-region overlap. Failed edits leave neither PDF nor
sidecar. The native-precision fixture requires exact source `Tm` operands after save/reopen.

Full suite: **not run**, explicitly requested. PR #67's subset fixtures remain unchanged.
