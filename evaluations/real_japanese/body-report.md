# Real Japanese body editing: Okinawa item (7)

**FIRST REAL-WORLD JAPANESE BODY TEXT EDITING VALIDATED — PASS**

Linux / Python 3.12.14, starting main `494f85def65ba5b76a44e56937b926541668b0f9`
(PR #59 merged). This is the previously refused real body, not a substituted
heading or synthetic success. The result covers one body line; real wrapping
and movement of the following paragraph are not claimed.

## Original and actual edits

- [Published Okinawa procurement PDF](https://www.pref.okinawa.lg.jp/_res/projects/default_project/_page_/001/035/156/01_koukoku.pdf)
- SHA-256: `bbaa2b12eeaf1c7eac2dc91aca03c00da4ed948e7c14d99d879ff4c3083fd0b3`
- Corpus: `evaluations/realpdf/corpus/word_okinawa_procurement.pdf`
- Page 2, observed line `p2-l44`, section 5 item (7).

| Revision | Logical body text (original trailing space omitted here) | Operation | Reopen |
|---|---|---|---|
| Original | `(7) その他詳細は、「仕様書」及び「応募要領」による。` | — | — |
| 1 | `(7) 応募に関するその他詳細は、「仕様書」及び「応募要領」による。` | `その他詳細` → `応募に関するその他詳細`, 5 → 11 characters | `restored` |
| 2 | `(7) 応募に関するその他詳細は、「仕様書の内容」及び「応募要領」による。` | `仕様書` → `仕様書の内容`, 3 → 6 characters | `restored` |

Both use `propose_page_flow` → `accept_page_flow` → `replace_in_flow` →
`edit_shared_flow` → save → `open_shared_flow`, with the second edit made from
the first reopened state. No manual flow, source-PDF repair, tag removal or
internal acceptance bypass is used. Original SHA remains identical.

## Why the previous production path refused

The original has readable Unicode and qualified metrics. Tc and Tw are both
zero. Reinspection finds **29 Tj events, 22 Td and 6 Tm operators** between
the first and last selected text-show operators (the initial Tm is before this
interval). Thus both Td and Tm position the text; it is not literally a separate
Tm before every glyph. The source observer records the resulting gaps, including about
-0.1198 pt adjustments, a -5.3998 pt punctuation adjustment, and a -0.6556 pt
adjustment after an Arial space. Total excess versus nominal advances is
-7.370217 pt including the trailing space. Alignment evidence remains **unknown**;
the spacing distribution is **irregular**, never inferred justify.

The previous T2 allocation converted the entire paragraph to new provider
glyphs, losing the source adjacency that the one-shot attributed editor already
knows how to retain. With the actual source font programs, its no-edit visible
width is **272.208455 pt**. The source-adjacent visible width is **264.839803 pt**.
No fixed tolerance could legitimately explain this difference.

## Production change and scope

The proposal exposes `text_placement=preserve-source-adjacency` when zero-Tc/Tw
source spacing is irregular. One explicit acceptance records the policy in the
existing v2 paragraph contract. This is an optional, backward-compatible policy;
existing natural-provider policies and the T3 path are unchanged.

T2 now passes the validated source-range edits to the existing `ParagraphShaper`
and paragraph writer for this policy. Retained characters use their current
source code, GID and font resource. Their observed gap is used only if the next
character is still their adjacent source occurrence, on the same original line
and the same output line. At replacement boundaries and new line edges the
existing shaper uses nominal/source inline advances; new text uses the explicitly
qualified provider and normal Unicode wrap/kinsoku. No gap is guessed for a new
pair. Multiple edits use actual source offsets, not text matching.

This policy requires one current source slot per paragraph, one shared region,
default left layout and no continuation destinations. Multiple paragraphs can
share that region and preserve their explicit follows gaps. Per-revision source
binding and ownership remain authoritative; no stored array of unverified gaps
is accepted. Source replay/removal proofs, Transaction, PDF readback validation,
atomic publication and restored-reopen checks remain active.

The no-edit gate is **stronger**: it checks every painted glyph origin, in addition
to line starts, baselines and widths, with the existing 0.002 pt geometry contract.
Compensating interior spacing errors cannot pass merely by preserving line width.

## Font identity and limits

The evaluation caller extracts the original embedded programs **without changing
their bytes** and supplies those local files through `font_candidates`. They
retain their original cmap, glyphs and advances; the existing qualification checks
every observed glyph, exact rational embedded hmtx advance and PDF width quantum.
This is not name-based substitution, a cmap repair, or a new production fallback.

| Original xref | Original program | SHA-256 of the supplied bytes |
|---|---|---|
| 22 | embedded MS Mincho subset | `88c3a4ac5ab893810a6ed5f43efa21bab83bb5d58a2de0e8e4ad432a98439e49` |
| 27 | embedded Arial subset | `bb244b70b266bd673ca80299643fecf5ee95377ce47daa9b6f4b48132ce76013` |

Both replacements are within the original subset's available character coverage.
Arbitrary unseen characters still require a suitable supplied font; automatic
embedded-font discovery, subset expansion and CFF support remain outside this PR.
No PDF, font, image or sidecar binary is committed.

## Independent checks and measurements

Accepted bounds: `[70.919998, 687.084229, 524.356003, 715.249146]` pt;
available width **453.436005 pt**, from margin symmetry constrained by foreign
content. Original and both saved baselines: **704.640015 pt**.

| Check | Revision 1 | Revision 2 |
|---|---:|---:|
| Visible line width | 329.093162 pt | 361.011649 pt |
| Line count | 1 | 1 |
| Retained / supplied glyphs | 23 / 11 | 31 / 6 |
| Largest actual-versus-planned origin error | 0.0000126434 pt | 0.0000122070 pt |
| Changed MuPDF pixels outside accepted region at 144 dpi | 0 | 0 |
| Unicode via MuPDF | exact | exact |
| Independent pypdf extraction | exact, ignoring extractor whitespace | exact, ignoring extractor whitespace |
| All text trace bounds inside accepted region | yes | yes |
| Foreign text Unicode/origins/bounds | unchanged | unchanged |
| Drawings / images / other pages' pixels | unchanged | unchanged |
| PDF 1.x operator nesting / original version | valid / retained | valid / retained |
| Save and restored reopen | pass | pass |

Every retained glyph's codepoint/GID is checked against the previous revision's
actual source occurrence. The source is untagged; both outputs retain the absence
of StructTreeRoot/StructParents and complete, empty marked-content scope lists.
Poppler full-page before/after images were rendered and visually inspected:
item (7) is legible, aligned and unclipped, and nearby items (6)/(8) and the table
remain in place. This is Linux evidence, not a Windows application/export test.

## Wrapping and next blocker

The runner additionally requests a replacement long enough to require wrapping.
The region ends at **715.249146 pt**, the top of fixed foreign item (8). The
next line would exceed that region; the shared-flow capacity guard refuses it
with `paragraphs exceed all explicitly confirmed shared regions`. No output is
published and no region is enlarged. Real wrap/push-down is therefore **REFUSED**.

The synthetic regression does wrap the first paragraph and moves the next one,
keeping the explicit follows relationship, through two saved/reopened revisions.
That is regression evidence, not a real-PDF reflow claim. The next real task is a
proven region including the following body paragraphs, with any hanging-list
indent and structural ownership handled explicitly.

## Reproduction and tests

Download the public original to the corpus path and verify the SHA above, then:

```sh
.venv/bin/python -u -m evaluations.real_japanese.body_edit --output tmp/okinawa-body-new
.venv/bin/python -m pytest -q tests/test_source_adjacency_flow.py tests/test_targeted_page_proposal.py tests/test_page_proposal.py tests/test_tagged_page_proposal.py tests/test_spacing.py tests/test_alignment.py
.venv/bin/python -m pytest -q -ra -n 4
```

Use a fresh output directory. The runner writes measured `summary.json`, raw
proposal/operation reports, both saved PDF/sidecar pairs, unchanged extracted
font programs and Poppler before/after images to the ignored output directory.
It fails if the original SHA, provider qualification, actual edit, audit or reopen
does not pass. The last command uses development-only pytest-xdist 3.8.0;
the repository's ordinary `pytest -q -ra` command is also supported.

Regression coverage includes mixed-font spaces, 0.12 pt contractions, compressed
punctuation, exact no-op raster, two edits, wrap/push-down, repeated-character
source offsets, overflow with no output, empty/retype/no-op, stale/tampered models,
glyph-level reproduction refusal, and retained B2 tree/MCID identity across two
saves. Final-HEAD focused/full-suite counts and any skips are recorded in the PR.
