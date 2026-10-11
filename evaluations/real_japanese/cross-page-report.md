# Existing Japanese body across occupied pages

**REAL-WORLD CROSS-PAGE OWNED BODY EDITING VALIDATED — PASS**

Source: unmodified [LibreOfficeへの移行](https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf), ten pages.
Original SHA-256: `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`.
Base: merged PR #64, `45bcdae5e7828490f14d66b746f4a3ac74b0deeb`.
Runner: [cross_page_edit.py](cross_page_edit.py), using public B1 joint proposal/accept and existing T2 editing APIs.
PDFs, font binaries, derived font caches and raw observations stay untracked.

## Source selection and authority

This is an occupied-page result: the original PDF already contains all three source fragments below.
No page is created or preprocessed, and no occupied text is confirmed as an empty continuation destination.

| Logical paragraph | Existing page / observed lines | Characters | Source owner | Original baselines (pt) |
| --- | --- | ---: | --- | --- |
| A, first part | 3 / `p3-l26`–`p3-l28` | 162 | `slot-0` | 728.5, 750.700012, 772.900024 |
| A, continuation | 4 / `p4-l1`–`p4-l2` | 102 | `slot-1` | 142, 164.200012 |
| B, following paragraph | 4 / `p4-l3`–`p4-l6` | 197 | `slot-2` | 192.400024, 212.799988, 233.299988, 253.700012 |

A discusses ODF/PDF document exchange. Its sentence ends on page 3 with `閲覧したり印刷` and continues on page 4
with `したりしたいだけならば`. The next indented paragraph, beginning `それぞれのドキュメントについて`, explains why
format choice matters for interoperability and ownership. This textual relationship and the exact paragraph
partition are caller-declared and explicitly accepted. Coordinates alone do not assert relatedness.
There is no interposed heading/body/footnote at the joined seam. The next heading on page 4,
`組織内の「技術的リーダー」を特定する`, remains foreign, as do all other text, logos, paths, links and annotations.

B1 independently derives and reproduces each page. The joint acceptance re-observes both original page bindings,
requires ordered exact source-part coverage, and proves all nine lines and each painted glyph in one T2 no-edit
plan. Each source fragment retains its own snapshot, selection and current-revision owner. A concatenates
exactly to 264 characters; no newline or space is invented at the page seam.

| Region | Page | Bounds (pt) | Layout x / width | First baseline |
| --- | ---: | --- | --- | ---: |
| `page-3` | 3 | [56.799999, 702.641479, 546.869500, 781.299988] | 56.799999 / 490.069501 | 728.5 |
| `page-4` | 4 | [56.799999, 108.200012, 543.010762, 284.161987] | 56.799999 / 486.210763 | 142 |

Both widths come from independently bounded observed wrap intervals, not an expanded tolerance or a guessed
page width. The page-4 bottom is bounded by the unchanged heading. A owns original source slots in both regions;
B independently owns its original page-4 slot. Growth uses those authorities and the existing scheduler/allocation.

## Production change

The blocker was the single-page B1 bootstrap and its one-slot-per-paragraph reproduction lookup, not the T2
allocator. `owned_flow_proposal.py` composes existing `confirm_story` / `confirm_shared_flow` arguments from
freshly verified B1 page plans. Page-namespaced logical styles prevent local style-ID collisions. The page-specific
font choices, paragraph groups, caller relationship, source bindings and joint reproduction are recorded in the
acceptance receipt. No T2 schema, source writer or layout engine changes.

`page_proposal._reproduce_fragments` now proves exact source text and allocation per slot, including multiple
slots of one logical paragraph. It retains the existing line/baseline/width and per-glyph origin comparisons.
Independent successful page proofs do not suffice: an unchanged joint plan that pulls text into spare space
on the preceding page is refused. Joined seams that skip unselected text are also refused.

Existing source-adjacency, source code/resource/GID retention, destination graphics state/clip, follows,
Transaction and atomic readback remain authoritative. No tagged-tree protection is relaxed.

## Actual edits and placement

First edit in A, **19 → 39 characters**, expanding the full paragraph **264 → 284**:

```text
before: 他組織に文書を渡す必要があったとしても
after:  取引先などの他組織に文書を渡し、内容の確認や印刷を依頼する必要があったとしても
```

The first three lines remain on page 3. Page 4's preexisting continuation grows **2 → 3 lines**.
Its new lines are:

```text
があったとしても、彼らが文書を閲覧したり印刷したりしたいだけならば、PDFフォーマットをファイル交
換に利用できます。最終版であれば読み込み専用のPDFでよいですし、ハイブリッドPDFであれば
LibreOfficeで編集もでき情報の劣化もありません。
```

A occupies logical ranges `[0,160)` on page 3 and `[160,284)` on page 4. The newly observed page boundary is
`必要` / `があったとしても`. Page-3 baselines stay unchanged; page-4 A baselines become
142, 164.200012, **186.400024** pt. B moves **22.20001220703125 pt**, to
214.600037, 235, 255.5, 275.900024 pt. Its original four line breaks and all **197** original glyphs survive.
The A→B baseline gap remains **28.20001220703125 pt**. B's unequal source pitches remain
20.399963, 20.5, 20.400024 pt. First-line x remains 67.300003 pt; continuation x remains 56.799999 pt
(a 10.500004 pt indent). The next heading remains at its original location, with disjoint ink bounds.

The reopened second edit changes B's `自由を制限` → `利用の自由を制限` (**5 → 8** characters), taking B from
197 to 200 characters without adding a fifth line. The runner then reverses both edits, regrows A, and performs
a no-op save. Reversing restores the original Unicode; it does not resurrect deleted font codes or require
original page partitioning after a substitute has been used (shortened A has ranges `[0,160)` and `[160,264)`).

## Fonts and independent checks

Source subsets remain source resources. Explicitly chosen OFL BIZ UDMincho supplies replacements and text
crossing a source-slot boundary: SHA-256
`468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`, face 0.
This is B3's explicit new-glyph substitution, **not source appearance equivalence**. Names do not qualify it.

The first edit retains **428** source glyphs and provides **53** glyphs: page-3 A retains 129/provides 31;
page-4 A retains 102/provides 22; B retains all 197/provides zero. Each retained current-source glyph is checked
for original PDF code, font resource/program identity and GID. Each provided glyph's embedded outline and
nominal advance are compared against the selected provider independently of the production layout.

For every saved revision, the runner independently checks:

- MuPDF Unicode per owned slot and pypdf whole-page Unicode (only pypdf whitespace is normalized).
- Actual glyph origins against the plan, glyph bounds within accepted regions, line text/baselines and gaps.
- Exact accounting of all painted region glyphs against restored owner selections: no ghost or duplicate text.
- All ten pages' foreign text positions/bounds, drawings, images, links and annotation fingerprints.
- **Zero** changed MuPDF pixels outside the two regions at 144 dpi, including eight entirely unchanged pages.
- PDF version/page count, operator nesting and original untagged structural status; no structure is stripped.
- Original PDF SHA-256 unchanged, atomic overflow refusal, and persisted model restoration.

The initial unchanged-follower translation error is **0.000000000000 pt**. The largest saved-glyph origin error
across all revisions is **0.000015258789 pt** (independent audit threshold 0.002 pt; production reproduction uses
its unchanged existing geometry tolerance). Both edited pages also have **zero** exterior pixel differences
in an independent Poppler render. Source and edited page-4 body crops were visually inspected: natural wrapping,
preserved indent, translated follower and unchanged following heading. The explicit provider change is visibly
distinguishable for some generated text; it is not concealed as an identical source font.

| Saved revision | A lines on pages 3 + 4 | B characters / lines | B shift (pt) | Retained / provided glyphs | Reopen |
| --- | --- | --- | ---: | --- | --- |
| grow | 3 + 3 | 197 / 4 | 22.200012 | 428 / 53 | restored |
| second | 3 + 3 | 200 / 4 | 22.200012 | 476 / 8 | restored |
| shorten | 3 + 2 | 197 / 4 | 0.000000 | 425 / 36 | restored |
| regrow | 3 + 3 | 197 / 4 | 22.200012 | 430 / 51 | restored |
| noop | 3 + 3 | 197 / 4 | 22.200012 | 481 / 0 | restored |

All five revisions preserve the 28.200012 pt paragraph baseline gap. Shortening returns B to its original
baseline and removes the added line; regrowth adds that line once. The last no-op save has identical whole-PDF
MuPDF raster to the previous saved revision. Overflow beyond the accepted regions refuses with
`paragraphs exceed all explicitly confirmed shared regions`, leaving neither PDF nor sidecar.
The original SHA-256 remains unchanged.

## Reproduce and regression tests

Obtain the URL above without rewriting it and place it at `evaluations/realpdf/corpus/lo_migration_ja.pdf`.
Obtain the BIZ UDMincho file matching the SHA above (see [font coverage report](font-coverage-report.md)). Then:

```sh
.venv/bin/python -m evaluations.real_japanese.cross_page_edit \
  --output tmp/cross-page/validation --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The output directory must not exist. It contains proposal/acceptance, original shared state, five saved PDFs,
sidecars and detailed writer reports, plus `summary.json` and Poppler before/after renders. The source and font
binaries and generated artifacts are not committed.

The minimal generated fixture has an existing paragraph split over two pages and an independent follower,
quantized font widths, individual Tm gaps, unequal leading and foreign fixed text. Thirteen tests cover no-op
raster identity, grow/re-edit/shorten/regrow, current-revision ownership, source-part order/coverage, stale PDF,
changed bindings, over-capacity atomic refusal, interrupted seams, unobserved inter-paragraph gaps and an
unselected follower that must remain foreign.

Final-HEAD checks (exact commit/counts in the PR):

```sh
.venv/bin/python -m pytest -q -n 4 tests/test_cross_page_owned_flow.py tests/test_owned_body_reflow.py \
  tests/test_page_proposal.py tests/test_tagged_page_proposal.py tests/test_rich_layout.py \
  tests/test_source_adjacency_flow.py tests/test_body_reflow.py tests/test_source_adjacency_continuation.py \
  tests/test_shared_flow.py tests/test_continuation.py tests/test_continuation_caller_to_shared_flow.py \
  tests/test_source_ownership.py
.venv/bin/python -m pytest -q -n 4
```

## Remaining boundary

This result joins a paragraph already continuing across consecutive occupied pages and its independent,
observably spaced follower. It does not infer the missing gap between separate paragraphs on opposite sides
of a page break, silently classify intervening headers/footnotes, or let one paragraph overwrite another's
source slot. Those cases need an explicit paragraph-boundary/authority contract. Arbitrary tagged structures,
new pages, automatic destination discovery, and unsupported font providers remain outside this change.
