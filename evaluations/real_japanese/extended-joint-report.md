# Incremental approval of additional joint source owners

**REAL-WORLD EXTENDED JOINT REFLOW VALIDATED — PASS**
Starting main: `0bd7ed335e12ae93deb6d68465f01f5c53a2ab43`.

## Source and selection

The unmodified ten-page **LibreOfficeへの移行** paper is the same source used in the preceding occupied-space
validation: https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf.
SHA-256: `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`.

The initial acceptance owns only A (page 8, lines 24–27, the complete innovator paragraph) and B
(page 9, lines 1–3, the first complete early-adopter paragraph). C–H remain foreign. The page-9 region is
bounded by the first unselected C glyphs; B cannot move through them. The occupied continuation rectangle
`[56.799999,110,540,174]`, starting at baseline 122, conditionally names only B as its releasing owner.
The existing page-program boundary at ordinal 0 (`w` → boundary → `q`) is separately checked.

The extension explicitly selects C–H, page-9 lines 4–23: the remaining adopter-category explanations and their
communication-strategy conclusion. These six complete paragraphs have 147, 196, 147, 166, 107 and 123 characters,
respectively (886 added glyphs). The textual relationship is declared by the caller, not inferred from coordinates.
The next section on page 10 and all unselected page content remain foreign. No page is added or preprocessed.

The same conditional continuation rectangle is used before and after extension. The added capacity comes from
accepting six additional source owners and their existing body region, allowing B and its followers to move safely.
It is not an enlarged independently empty destination.

The limited acceptance reproduces seven source lines, but the requested replacement refuses with
`paragraphs exceed all explicitly confirmed shared regions`, publishing neither PDF nor sidecar.
The expanded acceptance reproduces all 27 source lines. Measured region bounds:

| Region | Limited acceptance | Extended acceptance |
| --- | --- | --- |
| Page 8 A | `[56.799999,680.671509,541.718242,781.299988]` | unchanged |
| Page 9 body | `[56.799999,108.200012,538.200001,200.271454]` | `[56.799999,108.200012,542.164495,781.299988]` |
| Conditional continuation | `[56.799999,110,540,174]` | unchanged |

The page-9 bottom increases by **581.028534 pt** after C–H are explicitly selected. Its usable width is
independently requalified from all selected wrapped lines (481.400002 → 485.364495 pt); source glyph positions
and breaks must still reproduce. Remaining foreign content and margin bounds continue to limit the region.

## Production path

`propose_owned_flow_extension(source, accepted_model, page_proposals, ...)` takes an original, uncomposed
accepted shared flow and enlarged explicit B1 selections. It checks that the old logical paragraphs remain an
unchanged ordered prefix, with exactly the same per-page source glyph IDs and text. Only complete following source
paragraphs may be appended. A larger rectangle with the same owners is not this extension operation.

The proposal seals the original accepted model plus a review listing retained paragraphs, additional paragraphs'
text/line IDs/glyph IDs/bounds/baselines, previous regions, proposed B1 regions, explicit geometry overrides,
page-break policy, continuation requests and textual relationship. Producing it changes no editing authority.

`accept_owned_flow_report` restores the original model against the current source, recomputes the review, and
then runs the existing B1 page rebuild/font qualification, independent source-owner composition and joint glyph-level
no-edit reproduction for the entire new selection. The receipt binds the review, base model hash and new model hash.
Existing editable snapshots and source-output witnesses identify each accepted source owner independently.

The result uses ordinary T2 allocation/follows/source adjacency and the PR #68 conditional capacity contract.
The old B glyphs must be removed by B's matching plan in the same Transaction before A can use their original
space. Additional C–H ownership gives no exemption to unselected glyphs or nontext paints. CTM, clip, insertion
boundary, final paint collisions, atomic publication/readback and current-revision reopen remain unchanged.

This intentionally reaccepts from the original PDF. It does not modify ownership in an already edited allocation,
merge partial paragraphs, discover semantic relationships, infer extra pages or expand capacity automatically.

## Edit and independent audits

The exact same A replacement is attempted against both acceptances:

- Before: `望むタイプでもあります。`
- After: `望むタイプでもあります。試行の結果を共有し、利用者の疑問や改善の提案を記録することで、次の移行に役立てます。担当者は実際の文書を使って操作を試し、変更の理由と利用方法を説明します。得られた意見を整理し、部門間で知識を共有することで、組織全体の移行を支援します。`

Added text uses explicitly approved BIZ UDMincho, face 0, SHA-256
`468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`.
Appearance equivalence to the source font is not claimed. Retained glyphs keep their source codes, GIDs and font programs.

The runner requires the limited edit to refuse without an output PDF or sidecar, then performs original no-op,
grow, a reopened second edit in B (`変化を肯定的` → `新しい変化を肯定的`), shorten both edits, regrow and final no-op.
It asserts that B's final ink actually exceeds the former region bottom, rather than merely recording a larger
proposal. All added paragraphs retain their text and independently owned glyph provenance.

Audits cover MuPDF/pypdf Unicode, glyph origins/bounds/codes/GIDs/resources/programs, provider outlines and advances,
foreign text/drawings/images/links/annotations on all ten pages, PDF structure, restored reopen and source SHA.
MuPDF exterior raster comparison runs per revision; final Poppler exterior comparison is mandatory.
The source no-op checks all 1196 glyph identities and positions; the final no-op checks whole-document raster equality.

Text reversal does not restore deleted historical glyph codes or leading. As in #68, editing B's line edge selects
the existing minimum pitch, leaving C–H 0.099976 pt higher after shortening, while the accepted follows gaps remain
unchanged. B's first baseline returns to its source floor. This is distinct from the exact original no-op proof.

## Measured first edit

A grows **164 → 282 characters, four → six lines**: 199 characters/four lines remain on page 8;
83 characters continue on page 9 at baselines **122 and 142.400024 pt**. B's first baseline moves
**142 → 168.800024 pt**; its third baseline becomes **209.700049 pt**. Its planned ink ends at
**210.777155 pt**, beyond the old region bottom **200.271454 pt**. Independent saved-glyph inspection
finds B's actual bounds end at **211.171539 pt** and intersect **46 original C glyph bounds**.
C was foreign before extension and is now independently owned and moved safely; its contribution is observable.
The additional occupied-space audit is saved as `new-capacity-evidence.json`.

C–H move by the same **26.800024 pt** on the first edit and retain all 886 original glyph identities.
The original B→C through G→H baseline gaps remain 26.399963, 28.200012, 28.200012, 26.399994, 26.499969,
26.5 pt. The accepted A→B gap remains 26.4 pt. H's last baseline is 662.300024 pt, within the confirmed region.

The original no-op preserves **1196 source glyph identities** with **0.0 pt** origin error and whole-document
raster equality. The first saved edit reopens `restored`, retains 1184 glyphs and provides 130 new glyphs.
Maximum origin error is **0.000036621094 pt** under the existing unchanged check. Unicode, source font/code/GID
identity, provider outlines/advances, all-ten-page foreign content and PDF structure checks pass; exterior MuPDF
changed pixels are **0**. Before/after page-9 body crops were visually inspected: the continuation sits above B,
original follower indentation and spacing remain clear, and the added provider appearance remains explicitly different.
All lifecycle and final Poppler checks also pass, as recorded below.

## Completed lifecycle

| Stage | Reopen | B first-baseline shift (pt) | Retained / provided glyphs | Maximum origin error (pt) | Exterior MuPDF pixels changed |
| --- | --- | ---: | ---: | ---: | ---: |
| grow | restored | 26.800024 | 1184 / 130 | 0.000036621094 | 0 |
| second | restored | 26.800024 | 1308 / 9 | 0.000036621094 | 0 |
| shorten | restored | 0.000000 | 1178 / 18 | 0.000021972656 | 0 |
| regrow | restored | 26.800024 | 1184 / 130 | 0.000036621094 | 0 |
| noop | restored | 26.800024 | 1314 / 0 | 0.000036621094 | 0 |

All five saves restore. Shortening leaves the continuation dormant with zero glyphs; regrowth reuses its identity.
Final no-op is whole-document pixel-identical to regrowth. Final exterior Poppler differences are **0 on both
pages 8 and 9**, independently of the per-revision MuPDF comparisons. Original PDF SHA-256 remains unchanged.
The additional C-space intersection audit and final production extension-review recomputation were also checked
independently against these actual PDFs/source evidence and recorded alongside the lifecycle summary.

Further growth beyond the expanded accepted capacity still refuses with
`final allocation needs a missing paragraph destination slot; new continuation is not authorized`, publishing neither PDF nor sidecar.

## Reproduce

Keep third-party PDFs/fonts outside version control. With the PDF in `evaluations/realpdf/corpus/lo_migration_ja.pdf`:

```sh
.venv/bin/python -m evaluations.real_japanese.extended_joint_edit \
  --output tmp/extended/validation --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The output directory must not exist. Artifacts include the limited acceptance/refusal, extension proposal and
receipt, saved revisions, independent raster/font/glyph audits and summary. No PDF/font binary is committed.

## Tests and remaining boundary

Related regressions: **242 passed, 0 failed in 221.16 s**, including the first eight extension cases.
Final extension regressions after the region-order correction: **9 passed, 0 failed in 59.08 s**.
The latter reruns those eight cases and adds one cross-page source-fragment/JSON-key-order case (243 distinct tests total).
These cover same-edit refusal/success, no-op and persistent lifecycle, changed base/review/order/old or new owner
evidence, unchanged-owner rectangle enlargement, and foreign-capacity refusal. Existing continuation, shared flow,
source ownership, page-break/cross-page reflow, vacated space, Transaction, source adjacency, boundary insertion and
tagged guards are included. Command:

```sh
.venv/bin/python -m pytest -q -n 3 tests/test_owned_flow_extension.py tests/test_vacated_space.py \
  tests/test_continuation.py tests/test_shared_flow.py tests/test_source_ownership.py \
  tests/test_page_break_reflow.py tests/test_cross_page_owned_flow.py tests/test_transaction.py \
  tests/test_flow_transaction.py tests/test_source_adjacency_continuation.py \
  tests/test_boundary_destination.py tests/test_tagged_page_proposal.py
```

Environment smoke: **3 passed, 5 deselected in 13.24 s** (`test_vacated_space.py -k unowned_or_unverified`).
Initial development run: **6 passed in 98.16 s** before the additional refusal cases were added.
The final targeted command was `.venv/bin/python -m pytest -q -n 3 tests/test_owned_flow_extension.py`.
**Full suite is not run**, as requested and under `CLAUDE.md`.
PR #67's subset font fixtures are unchanged.

The next boundary is expansion after a saved edit: this path deliberately requires the same original, uncomposed
source. Existing fixed approved bounds still apply after acceptance. Automatic document-wide ownership discovery,
new pages, arbitrary tagged continuation and cyclic occupancy dependencies remain unsupported; insufficient final
capacity and foreign collisions still refuse.
