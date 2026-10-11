# Real Japanese body continuation across verified regions

**REAL-WORLD MULTI-REGION PDF EDITING VALIDATED — PASS**

Linux / Python 3.12, 2026-10-10; base main
`348856eac4c40e71789f82d8c18778d51b833fb4` (PR #62 merged).
[Runner](continuation_edit.py) / [machine-readable measurements](continuation-summary.json).
Third-party PDFs, fonts, rendered images and revision sidecars remain ignored local artifacts.

## Selection and authority

The unmodified [Kyoto City Q&A PDF](https://www.city.kyoto.lg.jp/kankyo/cmsfiles/contents/0000311/311254/kaitou_hp.pdf)
has two pages. SHA-256:
`61ef6bb9aaf0e6c6099fc7b4b67de804d311855ec0265d4648d197fb75dfad46`.
Select page 1 lines **p1-l34/p1-l35**, the final two lines of the three-line shipping note.
The preceding note line remains foreign; this evaluation does not claim ownership of the whole note.
This is ordinary Japanese body text with irregular zero-Tc/Tw source placement, not the previously edited heading.

Okinawa was inspected first: its items (7)/(8) are on the final page of the two-page PDF, above the fixed
page number. No next page exists. The previous LibreOffice continuation source was also checked, but its
wiki.documentfoundation.org download was blocked by this environment's host allowlist. A configuration
draft adds that host; this result does not depend on that download or claim to revalidate LibreOffice.
Kyoto is already in the real corpus and offers a meaningful reading-order continuation: the end of page 1
can continue above the next section on page 2 without moving that section or any of its tables.

B1 infers the original R1; **no source rewriting, region shrinking or overrides** are used. Source accept
reproduces both original lines and every glyph. The evaluator explicitly chooses page 2's top region R2
and boundary 0 after its opening `cm`, before the first `q`. This is a deliberate caller decision to use part
of the top margin; whitespace alone does not authorize placement, and semantic destination selection is
not automatic. The production geometry review, confirmation-request builder, destination confirmation
and flow attachment revalidate the decision before editing.

| Region | Page | Bounds (pt, top-down) | Baselines (pt) | Capacity |
|---|---:|---|---|---|
| R1 | 1 | [106.080002, 684.399902, 505.367836, 753.838753] | 700.919983, 718.919983, 736.919983 | 3 lines; originally 2 |
| R2 | 2 | [106.080002, 54, 505.367836, 82] | 70.919983 | 1 line |

Both use x 106.080002 and width 399.287834 pt. The page-2 heading stays at baseline approximately
97.919983 pt, **27 pt below** the continuation. Its ink starts at 88.8395 pt, outside R2.
The selected boundary has q depth 0, no inherited rectangular clip and a **nonidentity CTM**:
`[0.75, 0, 0, -0.75, 0, 841.9200439453125]`. Existing source-decimal inverse-CTM proof and isolated
q/BT/ET/Q block handling apply. The destination precedes all 38 original page-2 paint operators.
No original text, image or drawing is made movable.

## Production change

The blocker was a blanket source-adjacency restriction to one slot/region and a whole-paragraph local edit
mapping that could not allocate fragments. The implementation keeps the existing layout and writer:

- `confirm_shared_flow_continuations` appends already-confirmed, source-bound destinations to an accepted,
  **uncomposed v2** flow. New regions require explicit order and matching authority. Before/after no-edit
  fragments must be exactly equal and must allocate no new slot. Current source and program witnesses,
  destination geometry, ownership, bounds and protected content are all checked by existing validators.
- Validated logical edits project source **occurrence offsets**, not matching text, into each allocated fragment.
  A fragment retains only occurrences owned by its current physical slot. Gaps persist only for still-adjacent
  source characters on their source line. Characters crossing slots are generated using the confirmed provider;
  a source resource/code is never transplanted into another page as implied drawing authority.
- The existing allocator, follows, paragraph planner/writer, generated-block ownership, Transaction and atomic
  readback handle layout and publication. Saved generated slots are current owned sources on reopen.
  Shortening uses their existing dormant-block deletion path; regrowing reuses the slot identity.
- Tagged source adjacency cannot cross independently owned slots/destinations; no MCID/tree rewriting was
  added. Same-owner B2 and B4 regressions remain. No tolerance, clip, region or foreign-content rule was relaxed.

Original source program `CIDFont+F2` (250 cmap codepoints) SHA:
`221ec5662d19ca76dc62a66ba59356985353a622f6fd17324e71b13831a9a5c7`.
Source discovery and metric qualification are unchanged. New glyphs use an explicit B3 substitution:
[BIZUDMincho-Regular.ttf](https://raw.githubusercontent.com/google/fonts/main/ofl/bizudmincho/BIZUDMincho-Regular.ttf),
OFL 1.1, face 0, SHA
`468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`.
The selected provider appears heavier than the source in Poppler. This is an approved different appearance,
not font equivalence. Current same-slot retained glyphs keep their codes, resources, GIDs and font bytes.

## Actual text and placement

Original selected text (62 Unicode characters including its terminal space):

```text
100ｇ以内／店舗ですが、送付物が変更となる可能性があるほか、複数店舗分を本部に一括送付する場合はこの限りではありません。
```

The first edit replaces `この限りではありません。` (12 characters) with the following 99 characters,
growing the selected logical text to 149 characters:

```text
この限りではありません。送付物の内容や数量に変更がある場合は、発送前に本市と必要な資料を確認してください。複数店舗分をまとめて発送する場合も、送付物の数量及び発送方法について事前に確認してください。
```

Actual page-1 lines after wrapping:

```text
100ｇ以内／店舗ですが、送付物が変更となる可能性があるほか、複数店舗分を本部
に一括送付する場合はこの限りではありません。送付物の内容や数量に変更がある
場合は、発送前に本市と必要な資料を確認してください。複数店舗分をまとめて発
```

The confirmed page-2 region receives:

```text
送する場合も、送付物の数量及び発送方法について事前に確認してください。
```

There is no invented newline in the logical text. The allocator splits at character 113. The Japanese word
`発送` straddles the page boundary, a legal character break. The note's existing 18 pt line pitch is kept.
Page 2 is above the existing next section; neither its heading nor following content moves.

After reopening, an edit entirely inside that generated slot replaces `事前に確認してください。`
with `事前に確認をお願いします。` (12 → 13 characters), making the full text 150 characters.

| Saved revision | R1/R2 lines | Retained glyphs R1/R2 | Reopen |
|---|---|---|---|
| grow | 3 / 1 | 49 / 0 | restored |
| second local edit in generated slot | 3 / 1 | 113 / 23 | restored |
| shorten to original selected text | 2 / 0 | 49 / 0 | restored |
| regrow | 3 / 1 | 49 / 0 | restored |
| no-op | 3 / 1 | 113 / 35 | restored |

Retained counts refer to the immediate input revision. The generated slot keeps ID
`continuation-635a4b031517e67a558b525b`; after shortening it is dormant with zero glyphs.
The **entire second page then equals the original MuPDF raster**. Regrowth produces no duplicate block;
the final no-op has identical pixels on both pages to the preceding revision.

## Independent checks and limitations

Every revision checks MuPDF glyph Unicode against actual planned line fragments and independently checks
whole-page pypdf Unicode (whitespace normalized). All glyph ink bounds are inside the accepted regions.
The largest glyph-origin error across all saves is **0.0000445557 pt**, below the existing unchanged 0.002 pt
contract. Retained code/resource/GID/program identity is exact; generated outlines and em advances equal
the explicitly selected provider. Layout-trimmed terminal whitespace remains in logical text.

Foreign text Unicode, origins and bounds, drawings, and images are unchanged. At **144 dpi**, MuPDF
region-exterior changed pixels are **0 on both pages for every revision**. Poppler before/after renders
were visually inspected: the new line fits above the untouched page-2 heading, with no collision or ghosting;
the substitute's heavier appearance remains visible. Page count and PDF version are unchanged, operator
nesting is valid, and both input/output are untagged (no StructTreeRoot). Source SHA is unchanged.

Doubling the long replacement exceeds both accepted regions and **REFUSEs before publishing PDF/sidecar**.
The available next-page region has only one line of capacity. More space would require another independently
confirmed region or explicit ownership of related existing paragraphs; this work does not move the fixed next
section, create pages, infer destinations automatically, or attach destinations to already-composed models.
Tagged cross-owner continuation and unavailable provider recovery remain refused.

## Reproduce and regressions

Place the source URL bytes at `evaluations/realpdf/corpus/word_kyoto_questions.pdf` and the SHA-matching
font in a local directory (neither is committed). The runner checks both SHA values:

```sh
.venv/bin/python -m evaluations.real_japanese.continuation_edit \
  --output tmp/continuation/kyoto-run2 \
  --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The output directory must not exist. It contains five PDFs/sidecars, plans, acceptance/review records,
summary and optional Poppler PNGs. The committed summary is from the completed run above.

`test_source_adjacency_continuation.py` adds four minimal regression cases: the five-stage retained/generated
lifecycle; unapproved/insufficient capacity and stale ownership with atomic failure; mismatched geometry and
inherited clip; tagged source ownership refusal. Existing tests cover same-slot adjacency, B4 follows,
continuation boundary authority, generated output and source ownership.

Final-HEAD focused command:

```sh
.venv/bin/python -m pytest -q -n 4 tests/test_source_adjacency_continuation.py \
  tests/test_source_adjacency_flow.py tests/test_body_reflow.py tests/test_shared_flow.py \
  tests/test_continuation.py tests/test_continuation_caller_to_shared_flow.py \
  tests/test_continuation_geometry_review.py tests/test_source_ownership.py
.venv/bin/python -m pytest -q -n 4
```

Final-HEAD counts, zero-failure results and exact tested commit are recorded in the pull request.
