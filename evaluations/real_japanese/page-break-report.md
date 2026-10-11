# Independent Japanese paragraphs across a source page break

**REAL-WORLD CROSS-PAGE PARAGRAPH REFLOW VALIDATED — PASS**

Source: unmodified [LibreOfficeへの移行](https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf), ten pages.
SHA-256: `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`.
Base main: `5896ac5e5fdce1b311c1c605b3b4ab5c0923dca9` (merged #65).
Runner: [page_break_edit.py](page_break_edit.py). No PDF or third-party font binary is committed.

## Selection and original ownership

Page 8 ends with a complete paragraph about innovators; page 9 begins a new, indented paragraph about early
adopters. They are consecutive category explanations within the same communication-strategy section, not two
fragments of one sentence. The caller declares this textual relationship and accepts two independent source
owners. Page 6→7 was less suitable because fixed footnotes intervene. No unselected text intervenes at 8→9.

| Owner | Original page and line selection | Characters / lines | Original baselines (pt) |
| --- | --- | --- | --- |
| A / `slot-0` | 8, `p8-l24`–`p8-l27` | 164 / 4 | 705.599976, 726.700012, 748.299988, 768.700012 |
| B / `slot-1` | 9, `p9-l1`–`p9-l3` | 146 / 3 | 142, 162.400024, 182.900024 |

A begins `イノベーターは常に新しいものを試したくて仕方がなく` and ends `むタイプでもあります。`.
B begins `アーリーアダプターは典型的なオピニオンリーダーで` and ends `変化を肯定的な明かりと捉えています。`.
Both original selections are whole paragraphs. The next paragraph beginning `アーリーアダプターはあらゆるところにいます`
on page 9 is unselected and fixed. All other text, logos, drawings, images, links and annotations remain foreign.

The joint no-edit proof reproduces all **7 lines**, exact text, line breaks, baselines, widths and every painted
glyph origin. The source owners stay independent; no A/B concatenation or invented newline occurs.

## Independent region authority

| Region | Page | Bounds (pt) | First baseline | Authority |
| --- | ---: | --- | ---: | --- |
| A source `page-8` | 8 | [56.799999, 680.671509, 541.718242, 781.299988] | 705.599976 | Original paragraph A |
| A continuation `prefix-9` | 9 | [56.799999, 110, 538.200001, 130] | 122 | Independently reviewed and confirmed empty region |
| B source `page-9` | 9 | [56.799999, 130, 538.200001, 200.271454] | 142 | Original paragraph B |

A's source width is 484.918243 pt, bounded by observed wrapping. Page-9 widths are 481.400002 pt.
B's ordinary B1 region is narrowed at its top to 130 pt through the existing checked geometry override;
its bottom is limited by the next fixed paragraph. No source region is expanded to force acceptance.

The prefix is actually empty in the original PDF, including paint envelopes. The existing continuation review
checks its geometry, and the caller selects page-program boundary ordinal 0 (`w → boundary → q`). Confirmation
revalidates the original program, graphics state, insertion order and clip contract. This boundary has no
inherited clip; no clip is removed. A gets this separate authority; B's text is never exempted from the empty
region proof. Both page-9 regions remain disjoint. The prefix has capacity for one continued line in this case.

## Explicit page-break policy and production implementation

The blocker had two parts: B1 refused an unobserved inter-paragraph gap at a page seam, and the existing T2
region reset discarded the predecessor's position even when it had just continued onto the follower's page.

`propose_owned_flow` now accepts an explicit `page_breaks` policy, checked page geometry overrides, and separately
confirmed prefix continuations. The receipt records those choices with the page bindings and joint reproduction.
A finite positive **26.4 pt same-page baseline gap** is the caller's layout choice, consistent with the surrounding
body's paragraph spacing; it is **not** a measurement between the bottom of page 8 and top of page 9.

T2 reuses `break_before=next-region`, region allocation and follows. An optional `source_break` on the confirmed
follows edge binds A's last original source region to B's first original source region on consecutive pages.
The source-region floor prevents B from moving into unused earlier capacity. If A still ends on page 8, B starts
at its original 142 pt on page 9. If A continues onto page 9, B's first baseline becomes:

```text
max(B region first baseline,
    A last baseline + max(caller same-page gap, A descent + B ascent))
```

Here that is `max(142, 122 + 26.4) = 148.4 pt`. Saved placement verification recomputes the same contract from
current occupancies. The edge and source-region identities are included in the existing immutable flow digest.
Existing edges retain their original reset semantics. No writer, layout engine or automatic page generation is added.
Tagged-source restrictions, source adjacency, font qualification, current-revision binding, Transaction, atomic save
and generated-slot readback stay in force.

## Actual editing

Replace A's final phrase, **12 → 54 characters**, making A **164 → 206**:

```text
before: 望むタイプでもあります。
after:  望むタイプでもあります。試行の結果を共有し、利用者の疑問や改善の提案を記録することで、次の移行に役立てます。
```

A occupies `[0,199)` on page 8 and `[199,206)` on page 9. Its continuation is `に役立てます。` at baseline 122 pt.
A grows from **4 to 5 lines** (four original-page lines plus one continued line). Its page-8 planned baselines are
705.599976, 726.700012, 747.100037, 767.500061 pt. Complete unchanged source lines retain observed leading;
changed line boundaries use the existing confirmed 20.400024 pt minimum pitch. This is normal edited-paragraph
layout, not a tolerance change.

B's first save is a translation of **6.4 pt**; planned baselines are 148.4, 168.800024, 189.300024 pt.
All **146** original B glyphs, their codes, font resources/programs, GIDs, three line breaks, original unequal
20.400024/20.5 pt leading and 10.500004 pt first-line indent survive this initial push-down. Nothing on page 9
below the selected paragraph is moved.

The reopened second edit changes B's `変化を肯定的` → `新しい変化を肯定的` (**6 → 9 characters**, B 146 → 149).
B remains three lines. The edited last-line boundary uses its confirmed minimum pitch, so its planned last
baseline is 189.200049 pt. The runner reverses both edits, regrows A, then performs a no-op save. Shortening
returns B's first baseline to 142 pt and makes the prefix slot dormant. It restores original Unicode, not deleted
source-font codes or the historical leading of retyped lines; no original-appearance undo is claimed.

## Independent validation

The first edit retains **298** source glyphs and generates **54**: A retains 152 and generates 47 on page 8,
its page-9 continuation generates 7, and B retains 146/generates none. New glyphs use explicitly accepted BIZ
UDMincho, SHA-256 `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`, face 0.
This is B3 substitution, not a source appearance-equivalence assertion.

Each saved revision is independently checked for:

- MuPDF owned-slot Unicode and pypdf whole-page Unicode (pypdf whitespace normalization only).
- Glyph origins and bounds, actual PDF codes/resources/programs/GIDs for source-retained glyphs, and provider
  outlines/advances for generated glyphs. Exact owned-region glyph accounting catches ghosts and double drawing.
- Actual line breaks, paragraph order, planned baselines and the independently measured translation of B.
- All ten pages' foreign text positions/bounds, drawings, images, links and annotation fingerprints.
- Region-exterior MuPDF raster at 144 dpi, masking the separate approved regions rather than the full page.
- PDF version, ten-page count, operator nesting and original untagged structural status.
- Restored reopen, subsequent editing, generated-slot dormancy/reactivation, and original source SHA immutability.

The before/after page-9 body crops were visually inspected: A's continued line appears in the confirmed blank
prefix, B keeps its indent and moves down, and the next unselected paragraph stays fixed. Provider appearance
changes on new text remain visible and explicit. Lifecycle results and measured errors are recorded below.

## Measured lifecycle result

All five saves reopen **restored**. The maximum glyph-origin error and the first unchanged-B translation error
are both **0.0000244140625 pt**, below the existing 0.002 pt audit bound. No production tolerance changed.
All ten pages preserve foreign content; region-exterior MuPDF changes are **0 pixels** for every revision.
Independent Poppler before/after checks on pages 8 and 9 also report **0 exterior changed pixels**.
The original PDF SHA-256 remains unchanged.

| Stage | A lines: page 8 + page 9 | B characters / lines | B first-baseline shift (pt) | Retained / provided glyphs | Reopen |
| --- | --- | --- | ---: | --- | --- |
| grow | 4 + 1 | 146 / 3 | 6.400000 | 298 / 54 | restored |
| second | 4 + 1 | 149 / 3 | 6.400000 | 346 / 9 | restored |
| shorten | 4 + 0 | 146 / 3 | 0.000000 | 292 / 18 | restored |
| regrow | 4 + 1 | 146 / 3 | 6.400000 | 298 / 54 | restored |
| noop | 4 + 1 | 146 / 3 | 6.400000 | 352 / 0 | restored |

The no-op save is pixel-identical to the preceding saved revision over the entire document. Shortening removes
all generated-prefix paint, and regrowth produces it once. A still-longer replacement refuses with
`final allocation needs a missing paragraph destination slot; new continuation is not authorized`, leaving
neither output PDF nor sidecar: B's owned region is not borrowed for A.

## Reproduce and test

Obtain the source at the URL above without rewriting it and place it at `evaluations/realpdf/corpus/lo_migration_ja.pdf`.
Use the font matching the SHA above (see [font coverage report](font-coverage-report.md)). Then run:

```sh
.venv/bin/python -m evaluations.real_japanese.page_break_edit \
  --output tmp/page-break/validation --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The output directory must not exist. It receives review/proposal/acceptance evidence, five PDFs and sidecars,
writer reports, `summary.json`, and independent Poppler before/after renders. Artifacts and binaries are untracked.

Ten focused regression cases use a minimal fixture with separate complete A/B paragraphs on two pages, a confirmed empty prefix, irregular Tm
placement, quantized widths, unequal source leading and fixed foreign text. It checks original-page no-op raster,
longer A and translated B, reopened second edit, shorten/regrow/dormant-slot cleanup, missing/invalid page-break
policy, absent continuation authority, insufficient capacity, overlapping regions, forged region floor and an
attempt to assign the predecessor's prefix to the follower.

Final-HEAD commands (exact commit/results in the PR):

```sh
.venv/bin/python -m pytest -q -n 4 tests/test_page_break_reflow.py tests/test_cross_page_owned_flow.py \
  tests/test_owned_body_reflow.py tests/test_page_proposal.py tests/test_tagged_page_proposal.py \
  tests/test_rich_layout.py tests/test_source_adjacency_flow.py tests/test_body_reflow.py \
  tests/test_source_adjacency_continuation.py tests/test_shared_flow.py tests/test_continuation.py \
  tests/test_continuation_caller_to_shared_flow.py tests/test_source_ownership.py
.venv/bin/python -m pytest -q -n 4
```

## Remaining blocker

A's continuation authority is limited to the independently empty prefix. More A text cannot borrow B's source
slot or expand its own region just because B might move. A page with no such empty capacity needs an explicit
vacated-space authority and current-revision proof before redistribution can proceed. Intervening unselected
headers/footnotes, broader tagged ownership and automatic page creation remain unsupported here.
