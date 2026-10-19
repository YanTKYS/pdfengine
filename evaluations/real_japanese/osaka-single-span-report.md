# Osaka single-Span ActualText editing — BOUNDED

Measured 2026-10-19 on branch `investigate-osaka-tagged-body`, based on
`dfc610bc9e794bd492cbefe354c905706f897d2b` (including merged PR #76).
The preceding [structural investigation](osaka-tagged-body-report.md) and
[qualification report](osaka-body-qualification-report.md) remain unchanged.

## Result and scope

**BOUNDED — real edit, atomic ActualText update, restored save and saved no-op
validated.** This is an explicitly selected single-owner internal deletion,
not ordinary intent-only B1 PASS or multi-owner paragraph reflow.

Source: [Osaka guideline notice](https://www.pref.osaka.lg.jp/documents/63648/guideline_tsuuchi.pdf),
`evaluations/realpdf/corpus/word_osaka_guideline.pdf`, one page.
Original SHA-256 before/after:
`9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe`.
The evaluation-only wording change is not an official corrected notice.

- Parent P58; target leaf Span `62 0 R`, MCID 28; siblings59–61 and63–64 stay foreign.
- Original displayed text and ActualText:
  `国立感染症研究所においてガイドラインが作成されましたので、参考に送`
- Edited displayed text and ActualText:
  `国立感染症研究所においてガイドラインが作成されたので、参考に送`
- Delete logical offsets `[23,25)` (`まし`); **33 → 31 characters**, one line before/after.
- Caller declares the complete observed owner selection, width348.94000244140625pt,
  and rectangle `(176.05999755859375,335,525,351)`pt, then explicitly accepts the proposal.
  No font substitution/provider, sibling movement, new MCID or cross-owner redistribution.

## Contract and implementation

`marked_content.leaf_span_structure` proves a separate `single-span-actualtext-1`
contract: one complete, unique page MCID owned by a leaf Span, exact original
ActualText/displayed Unicode equality, valid ParentTree and ancestor backlinks,
ordered leaf siblings, one style/visual line and one original rectangular clip.
Ancestor semantic overrides, absent/mismatched leaf ActualText, multi-MCID targets,
partial/cross-owner selection, unsupported envelope/nesting and malformed trees refuse.
BDC tag `/P` is distinguished from StructElem role `/Span`.

`page_proposal.propose_single_span` / `accept_single_span` expose explicit proposal
and approval. `span_edit` reuses source replay/removal and ParagraphPlan. Initial
no-edit planned output is rendered in memory and must reproduce the entire source
with MuPDF; no original PDF is changed. The new persistent sidecar is
`pdfengine-single-span-actualtext-1`, separate from unchanged shared-flow-2/B2.
`open_single_span` reobserves the current revision and returns `restored`.
Only one internal deletion (or saved no-op) is supported; insertion, free wrapping,
owner aggregation and paragraph push-down remain outside this contract.

`source_ownership` binds the complete owner witness to the consumed/generated
text. `Transaction` allows exactly one semantic plan, rejects removal-only
publication, and supplies a compare-and-set ActualText update to the existing
`pdf_save` adapter. The same ID-preserving pypdf writer applies stream and leaf
ActualText changes before its single serialization. Original ActualText and the
structure graph are checked first. Serialized structure, displayed text, MCID,
owner and clip are reobserved before publication. PDF/sidecar readback precedes
the existing staged publication mechanism. No second writer or semantic postprocess.

The original `q re W* n BT ... ET Q` envelope and clip remain intact; only selected
text operators change. The clip is the original page rectangle
`0.000008871 0 595.32 841.92 re W* n`. The preceding comma's nominal box overlaps
the selected source box by about0.12pt. The first preflight conservatively refused
that overlap (zero saves). The final guard allows only overlap already contained
in the observed selected box, while retaining the existing per-glyph ink collision
proof for both no-edit and editing plans. It grants no new foreign capacity.
Positive nominal-box and negative actual-ink collision regressions cover this.
No transparency/outline engine or B2 guard was relaxed.

## Measurements

Exactly **two successful real saves**, with no full-history or three-page rerun:

| Operation | Readback | MuPDF144dpi | Poppler144dpi | Time including audit |
| --- | --- | --- | --- | --- |
| Internal deletion + ActualText | restored | exterior0pixels | exterior0pixels | 3.796839s |
| Saved-revision no-op | restored | whole-page0pixels | whole-page0pixels | 3.022220s |

Qualification:2.135582s; complete evaluation:8.954641s. No threshold or extra
mask margin is used; the edit mask is the explicitly approved rectangle.
The saved no-op retains the existing page program, rather than retypesetting it;
planned source reproduction is independently checked at proposal time.

All97 MCIDs remain unique and tree-backed. The other96 envelopes, displayed
glyphs (code/GID/font program/resource/origin/bounds), ActualText and associations
are exact. The31 retained target glyphs keep original code/GID/font, with positions
adjusted by the deletion. Target ActualText equals the new displayed Unicode.
Structure/ParentTree/reference IDs/child order are exact except the authorized
leaf ActualText value. Clip unchanged. Original source SHA unchanged.
The final no-op observation equals the edited observation, including glyph positions.

pypdf full-load emitted warnings for old object IDs4,28,33,167 during saved no-op.
A read-only traversal of each PDF's trailer graph found164 reachable objects and
**zero missing references** in source, edited and no-op. None of these four IDs is
reachable in either output (source ID4 was the replaced content stream). The
warning is recorded rather than treated as a rendering failure or suppressed.

## Focused tests

**101 distinct passed, 2 skipped**, reported by the implementing Astra agent and
cross-checked against collected test node IDs; no full suite:

- `tests/test_single_span_actualtext.py`:28 passed. Final27-test batch2.64s,
  additional compare-and-set refusal1test0.46s. Covers lifecycle/no-op/re-edit,
  exact structure/clip, owner boundaries, semantic mismatches, stale approvals,
  region/ink guards, atomic verification failure and no published partial output.
- `tests/test_tagged_page_proposal.py`:38 passed, unchanged B2 cases. The initial
  combined batch with25 new tests was63passed in85.86s; overlapping counts are
  not added again.
- `tests/test_pdf_save.py`, `tests/test_transaction.py`, `tests/test_marked_content.py`:
  35 passed,2 skipped (AES provider unavailable). Duration not retained.

Real evaluator:
`.venv/bin/python -m evaluations.real_japanese.osaka_span_actualtext`.
It refuses to overwrite its two successful output PDFs. Failed preflight did not
consume a successful-save allowance. Runtime:Linux, Python3.12.14,
PyMuPDF1.27.2.3, pypdf6.10.0, Poppler26.05.0.

## Output digests and remaining limits

- `tmp/osaka-single-span-actualtext/edited.pdf`: `fde1b163ddbfcbbfe3d3fc6c3505139be7f932e4a972a9f2ad1583ee163a4f9e`
- `tmp/osaka-single-span-actualtext/edited.json`: `6cf7aac7085451d26e35b91c9f58d1a243c6dd2423ca1a74ee6e9a7b07c61831`
- `tmp/osaka-single-span-actualtext/noop.pdf`: `c46ea77f07fc6a34d14e2149ac4707beabfd85e1e49f9d65202abb81d0691b20`
- `tmp/osaka-single-span-actualtext/noop.json`: `f4e6393baf0023643661e6ef21f1916a65809f7283ed14fecf1afcb35f8c04f5`

Machine-readable measurements: [summary](osaka-single-span-summary.json).
PDFs, sidecars, fonts and rasters stay under ignored tmp/ and are not committed.

This establishes one real Word-derived leaf-owner editing case. Multiple owners,
new glyph insertion, whole paragraphs, inherited ActualText, free reflow and
StructureTree reconstruction remain refused/unsupported by this narrow path.
No claim of arbitrary Word/PDF-UA editing or assistive-technology interoperability
is made beyond the exact checked structure and Unicode/ActualText contract.
