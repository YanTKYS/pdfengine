# Real Japanese body reflow: Okinawa items (7) and (8)

**FIRST REAL-WORLD JAPANESE BODY REFLOW VALIDATED — PASS**

Linux, Python 3.12; base main `546af051ad7f0739c6ccc5fdee8a24cc97d34495`
(PR #60 merged). The original published PDF is the input, without preprocessing.
Both revisions use public propose → explicit accept → replace → edit → save →
restored reopen; the second edit starts from the first restored state.

## Source and selection evidence

- [Okinawa procurement announcement](https://www.pref.okinawa.lg.jp/_res/projects/default_project/_page_/001/035/156/01_koukoku.pdf), page 2, section 5.
- Original SHA-256: `bbaa2b12eeaf1c7eac2dc91aca03c00da4ed948e7c14d99d879ff4c3083fd0b3`.
- Observed lines explicitly selected: `p2-l44`, `p2-l45`, `p2-l46`.
- Two paragraphs: item (7), one line; item (8), two lines.
- Both markers start at x **70.919998 pt**. Both bodies and the existing continuation
  start at x **88.919998 pt**: an observed **18 pt hanging indent**.
- Consecutive `(7)` / `(8)` markers, matching 10.559789 pt observed size,
  matching marker/body origins, aligned continuation, strictly descending
  nonoverlapping lines and the existing soft wrap provide a candidate grouping.
  The exact source programs are the embedded MS Mincho / Arial subsets, including
  the existing mixed-font space. These facts are evidence, not automatic authority:
  only the explicitly selected lines can enter the proposal, and explicit acceptance
  plus source/structure/metric/no-edit proofs is required before either paragraph moves.
- The PDF is untagged. Existing B2 structural checks are still applied; tagged scope
  is not broadened. No scopes or trees are removed or manufactured.
- The table, heading, items (1)–(6), page number and every other paint remain foreign.

This is the same irregularly positioned body as PR #60, now including its actual
following paragraph. It tests a common word-processor pattern: numbered paragraphs
with mixed-font marker spaces, manually positioned glyphs and hanging continuations.
It was not substituted with an easier heading or prepared document.

## Blocker and production change

The earlier generic alignment heuristic separated item (8)'s continuation into
another column: its 18 pt offset exceeds the generic first-line alignment range.
The proposal then omitted that selected line and had no defensible width. Simply
merging it would still fail because negative first-line indents were unsupported.

`page_proposal` now recognizes a bounded candidate only for explicitly selected,
consecutive numbered paragraphs with a witnessed common body/continuation origin.
It never adds unselected text. Other explicit selections now fail closed if the
inferred main column would silently omit any selected line. Existing region and
wrapped-width inference operate on the complete candidate; they are not bypassed.

The existing attributed layout's `first_line_indent` now supports a signed offset:
`-18` puts the first visual line at the marker position while continuations use the
body x. The writer's ink envelope includes that declared overhang and still checks
page bounds and foreign content. T2 permits a negative policy only on its existing
single-region, source-adjacency, left-layout path, and only when the first origin
is inside the explicitly confirmed region. Legacy and semantic paths remain refused.
No new layout engine, paragraph allocator or structure writer was added.

PR #60's retained codes/GIDs and same-source-adjacency spacing remain active.
Replacement/new boundaries use qualified provider shaping. The unchanged T2
`follows`, allocation and dependency scheduling move the accepted follower. Every
revision rebinds current source ownership and uses Transaction, one atomic save,
readback verification and restored reopen. No tolerance was increased.

## Region and relationships

| Quantity | Measured value / evidence |
|---|---|
| Region bounds | `[70.919998, 687.084229, 544.079800, 768.698059]` pt |
| Upper / lower boundary | fixed item (6) / fixed page number |
| Body x / continuation width | 88.919998 / 455.159801 pt |
| Observed width interval | `[449.879807, 460.439796)` pt |
| Chosen width | existing wrapped-line interval midpoint; margin symmetry does not fit |
| Hanging first-line indent | -18 pt for both paragraphs |
| Line pitch | 19.680237 pt, from item (8)'s existing continuation |
| Follows baseline gap | 19.679993 pt, from item (7)'s last to item (8)'s first line |
| Original item (7) baseline | 704.640015 pt |
| Original item (8) baselines | 724.320007 / 744.000244 pt |
| Both edited revisions, item (7) | 704.640015 / 724.320251 pt |
| Both edited revisions, item (8) | 744.000244 / 763.680481 pt |
| Item (8) displacement | **+19.680237 pt** |

The follower is inside the accepted flow because its complete two-line paragraph
was selected and proved. The region was recomputed with that ownership, ending at
the still-fixed page number; no foreign object was promoted or pushed down merely
because it was below the edit. Further growth beyond this region is refused.

## Actual text and wraps

Original item (7):

```text
(7) その他詳細は、「仕様書」及び「応募要領」による。
```

Revision 1 replaces `その他詳細` (5 characters) with
`応募に関する手続、提出書類の内容及び企画提案のその他詳細` (28 characters).
Actual visual lines (no authored newline is inserted):

```text
(7) 応募に関する手続、提出書類の内容及び企画提案のその他詳細は、「仕様書」及び「応募要領」
    による。
```

After restored reopen, revision 2 replaces `仕様書` (3) with `仕様書の内容` (6):

```text
(7) 応募に関する手続、提出書類の内容及び企画提案のその他詳細は、「仕様書の内容」及び「応募
    要領」による。
```

The indentation in these code blocks illustrates x alignment; the PDF uses the
measured 18 pt offset. Ordinary Japanese wrapping permits the split inside
`応募要領`; kinsoku is enforced by the existing engine. Both revisions add exactly
one line to item (7). Item (8)'s Unicode and its two original break positions remain:

```text
(8) 応募要領等については、沖縄県のWebサイト「沖縄県小中学校次世代型校務支援システム製品選
    定業務」のページからダウンロードすること。
```

Logical terminal spaces remain in the state; layout-trimmed terminal spaces are
not painted, as before. The original paragraph text, all logical text and exact
line ranges are recorded by the runner.

## Verification

| Check | Revision 1 | Revision 2 |
|---|---:|---:|
| Item (7) / item (8) line counts | 2 / 2 | 2 / 2 |
| Item (7) retained / supplied glyphs | 23 / 28 | 48 / 6 |
| Item (8) retained / supplied glyphs | 69 / 0 | 69 / 0 |
| Largest actual-versus-plan origin error | 0.0000194000 pt | 0.0000289917 pt |
| Region-exterior MuPDF changed pixels, 144 dpi | 0 | 0 |
| Restored reopen | pass | pass |

- MuPDF Unicode equals the logical text at its actual visual line ranges;
  independent pypdf extraction agrees after extraction-whitespace normalization.
- All actual glyph bounds are inside the accepted region. Actual origins agree
  with every planned glyph, including all four baselines and hanging starts.
- Retained source Unicode/GIDs are matched by occurrence against the prior revision.
  The unchanged follower is also checked as a uniform translation of the original
  source glyphs; maximum translation error is 0.0000000000 pt, within the existing geometry contract.
- All foreign Unicode/origins/bounds, drawings and images are identical. Every
  other page's MuPDF raster is identical. The table and footer stay fixed.
- PDF version is preserved; all pages pass PDF 1.x operator nesting. The original
  and saved PDFs remain untagged. The original SHA remains identical.
- Poppler before/after full-page renders were visually inspected: readable text,
  aligned continuation lines, no clipping or overlap, and an unmoved page number.
- Overlong replacement fails with `paragraphs exceed all explicitly confirmed
  shared regions`; neither PDF nor sidecar is published. Nonconsecutive selection
  and a region override crossing the footer are also refused.

The caller supplies byte-identical original embedded font programs, with unchanged
metric verification. SHA-256 values: MS Mincho
`88c3a4ac5ab893810a6ed5f43efa21bab83bb5d58a2de0e8e4ad432a98439e49`, Arial
`bb244b70b266bd673ca80299643fecf5ee95377ce47daa9b6f4b48132ce76013`.
The edits use characters actually present in those subsets. This does not claim
new fallback, font-name substitution or support for arbitrary missing characters.

## Reproduce and regressions

Keep the published original at `evaluations/realpdf/corpus/word_okinawa_procurement.pdf`,
verify the SHA above, and use a fresh output directory:

```sh
.venv/bin/python -u -m evaluations.real_japanese.body_reflow --output tmp/okinawa-reflow
.venv/bin/python -m pytest -q -ra -n 4 tests/test_body_reflow.py tests/test_rich_layout.py tests/test_page_proposal.py tests/test_targeted_page_proposal.py tests/test_tagged_page_proposal.py tests/test_source_adjacency_flow.py tests/test_shared_flow.py
.venv/bin/python -m pytest -q -ra -n 4
```

`summary.json` contains measurements; `proposal.json`, both operation reports,
PDF/sidecar pairs, unmodified fonts and Poppler PNGs stay in the ignored output.
No third-party PDF/font binaries are committed. Final-HEAD focused and full-suite
counts and skips are recorded in the PR.

The generated-font regression reproduces numbered markers, an 18.06 pt hanging
indent and irregular retained gaps. It checks exact no-op raster, two saved/reopened
edits, a real two-line layout for each paragraph, the 20 pt follower displacement,
an independently specified 26 pt follows gap, unchanged foreign pixels, overflow
atomicity, foreign collision, invalid selection/segmentation and out-of-region
hanging policies. Existing B2 tagged lifecycle tests remain in the focused suite.

## Remaining boundaries

This result is bounded to one page and one proven shared region. Recognition is
intentionally narrow: explicitly selected ASCII parenthesized consecutive decimal
markers, matching origins and size, and at least one witnessed continuation.
Unmatched list patterns, arbitrary paragraph membership, ambiguous columns,
intervening paints, broader tagged ownership and cross-page growth stay refused.
The remaining capacity cannot hold another added line here; page creation or
moving the footer is not authorized. Missing subset glyphs still need a qualified
provider (B3). No T3 or decoration scope was added.
