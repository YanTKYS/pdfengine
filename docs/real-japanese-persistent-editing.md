# Real Japanese PDF persistent editing

**FIRST REAL-WORLD JAPANESE PDF EDITING VALIDATED — PASS.** The validated case is
one heading in an existing Kyoto City PDF. It is not a body-paragraph or general
Word/tagged-PDF result. Runtime starts at main `7ce8cd43912510325bfcd43be444383e8a4b580c`.

## Real source and result

- Public original: [Kyoto City questions and answers](https://www.city.kyoto.lg.jp/kankyo/cmsfiles/contents/0000311/311254/kaitou_hp.pdf),
  local corpus `word_kyoto_questions.pdf`, two pages.
- Original SHA-256: `61ef6bb9aaf0e6c6099fc7b4b67de804d311855ec0265d4648d197fb75dfad46`.
- Target: page 1, observed `p1-l1`, `質問に対する回答 ` (including the original trailing space).
- First edit: `質問` → `寄せられた質問`; saved heading `寄せられた質問に対する回答 `.
- Reopen returns `restored`. Second edit: `回答` → `回答について`;
  saved heading `寄せられた質問に対する回答について `. Second reopen also returns `restored`.
- Both edits change length and use the public `propose_page_flow` → `accept_page_flow`
  → `replace_in_flow` → `edit_shared_flow` → `open_shared_flow` path. No manually
  constructed flow, style conversion, replacement PDF, repaired tags or weakened guard.
- Region comes from existing evidence: x=85.0800018311 pt, width=425.1600036621 pt,
  baseline=98.3999938965 pt; bounds `[85.0800018311, 0, 510.2400054932, 106.8395538330]`.
  The lower boundary is foreign content. A longer threefold heading that would wrap
  is refused with `paragraphs exceed all explicitly confirmed shared regions`.
  No wrap output is published; the foreign boundary is not expanded.

The actual Windows run and all assertions are reproduced by
[`evaluations/real_japanese/evaluate.py`](../evaluations/real_japanese/evaluate.py).
[`summary.json`](../evaluations/real_japanese/summary.json) contains measured results,
exact provider identity, spacing evidence and per-revision audits. External PDFs,
font binaries, modified PDFs and sidecars are not committed.

## Measured candidate decision

The first actual whole-page B1 refusal was `multiple-columns` for every candidate
below, before fonts or tagged ownership were considered. The aggregate column
heuristic chains headings, table rows, blank lines and footers on these pages;
removing that guard would not make them editable.

| Original target | Unicode observation | Independent blockers after target inspection | Decision |
|---|---|---|---|
| Takeo notice p2 body, lines 4–5 | Readable Japanese | Orphan MCIDs; different non-inline state; proposed area intersects backgrounds/images | Keep refused |
| Osaka guideline p1 body, lines 13/16 | Readable Japanese | Several structure owners, Span/ActualText patterns; different non-inline state; line 16 also lacks a width candidate | Keep refused |
| Osaka fire notice p1 body, lines 13–14 | Readable Japanese; title has duplicated extraction | Mixed P/Span/ActualText owners and different non-inline state | Keep refused |
| Ubiquiti p1 | Lower-priority English document | Existing structural probe: NonStruct/MCR, not the B2 leaf-P subset | Not a Japanese success candidate |
| Okinawa procurement p2 item (7), line 44 | Exact Japanese plus Arial space | Targeting resolves global geometry; exact MS Mincho/Arial metrics qualify; no-edit reproduction still refuses irregular Tm positioning | Do not reinterpret as uniform justification |
| Kyoto questions p1 heading, line 1 | Exact original Japanese | Targeting resolves geometry; two fonts qualify metrically, so explicit provider choice is needed | Actual two-edit persistent lifecycle passes |

The initial wider survey also inspected Wakayama, LibreOffice and Kyoto body lines.
Osaka symposium contains control-character/gibberish extraction despite zero U+FFFD;
absence of replacement characters alone was not treated as Unicode correctness.
A short heading remains meaningful real content; the successful scope is not enlarged
by synthetic regression results.

Okinawa item (7) has zero Tc/Tw but Tm adjustments including roughly 0.12 pt contractions,
a 5.40 pt punctuation contraction and a differently positioned Arial space. Its total
measured width is about 7.37 pt below nominal including trailing space. The source-spacing
observer classifies this as irregular, not a justified-layout witness. The unchanged
no-edit reproduction gate refuses it. B6 and broader tagged ownership remain work ahead.

## Small reusable production change

`propose_page_flow(..., line_ids=[...])` and CLI `propose-page --line ID` allow the
caller to identify consecutive **observed** lines. IDs can be obtained with
`inspect_selection_source`; selected text appears in the proposal for review.

Only candidate paragraph/column inference uses the selected lines. Every other line,
path, image and nonhorizontal glyph remains foreign content. Duplicate, unknown,
out-of-page, reversed and nonconsecutive IDs are rejected. Multiple columns within
the target, collisions, unsupported tags, provider mismatches, source proof failures
and no-edit layout mismatch remain refusals.

The optional target and its caller-selected provenance are bound in the existing
proposal digest and recomputed against the source SHA/page observation at acceptance.
Re-sealing altered target evidence does not authorize it. Ordinary whole-page behavior
and sidecar schemas are unchanged. No changes to T2, source ownership, Transaction,
font qualification, marked-content preservation, structure trees or ActualText semantics.

## Font fidelity and preservation evidence

The source subset identifies itself internally as MS Gothic Version 5.32; source
resource name `CIDFont+F1` alone was insufficient. Both MS Gothic and MS Mincho match
the nine observed advances. The caller chooses installed `msgothic.ttc`, face 0,
SHA-256 `4bde3e6392b96910fb59094c6c1a4dbfae18fee78d0bf13dc30616837c4f95db`.

Beyond the unchanged runtime metric qualification, the evaluation compares normalized
glyf coordinates as exact fractions, contour endpoints and flags against the embedded
program for all nine observed glyphs, including space: **9/9 exact matches** for Gothic.
Mincho has different outlines for all eight nonspace glyphs and was not selected.
This is an explicit evidence-based provider choice, not a font-name substitution.

For **both** saved revisions:

- Expected changed Unicode is independently extracted by MuPDF; reopen is `restored`.
- All foreign text codepoints, origins and bounding boxes match the original.
- All drawings match geometrically and stylistically; paint sequence numbers may shift.
- Image dictionaries/digests match (the target source has no images).
- At 144 dpi, every MuPDF pixel outside the accepted region is identical; the entire
  other page is identical. No tolerance or extra audit margin is used.
- No StructTreeRoot or StructParents exists before/after. All pages have complete
  marked-content observation, zero scopes and zero MCIDs. No tree is stripped or manufactured.
- The source file retains its exact original SHA-256.

Poppler-rendered complete before/after pages were also inspected visually: the heading
is legible and extended, with no clipping or collision; body text and rules remain fixed.
Windows execution is real evidence. It does not establish a Linux font installation,
Word application/export validation, Japanese body reflow or arbitrary tagged editing.

## Regression and verification

The new minimal fixture reproduces a table/multiple-column area plus a separately
selectable lower Japanese paragraph. It demonstrates two edits, restored reopen,
natural wrap and unchanged fixed pixels/drawing through the same production path.
It also reproduces a source Tm contraction and requires the existing reproduction
refusal. These are explicitly synthetic regression fixtures, not the real-PDF evidence.

Targeted tests cover the lifecycle, target validation and tampering, foreign collision,
columns inside the target, orphan tags, irregular positioning and CLI usage.
Focused run: **69 passed** (12 new + 19 B1 + 38 B2), Windows, `PYTHONUTF8=1`.
The final full-suite result and exact tested commit are reported with the pull request.

Reproduce the actual corpus case on the recorded Windows font installation:

```powershell
$env:PYTHONUTF8='1'
.venv\Scripts\python.exe -u -m evaluations.real_japanese.evaluate --output tmp/kyoto-real-edit
```

The output directory must be new. Missing source bytes or a nonmatching provider is
an error/refusal, never a synthetic replacement or a skipped real-case success.
