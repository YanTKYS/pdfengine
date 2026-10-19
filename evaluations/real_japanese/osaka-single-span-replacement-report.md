# Osaka single-Span replacement/insertion — BOUNDED

Recorded 2026-10-20 (Asia/Tokyo). Branch `single-span-replacement`, based on
merged PR #77 main `636e9f8112d61cf8f8914bb7ca10804d4c018611`.
The [previous deletion report](osaka-single-span-report.md) and its outputs are
preserved; its real evaluation was not rerun.

## Real result

**BOUNDED — nonempty replacement, atomic ActualText update, restored save and
saved-revision no-op validated.** Selection, fixed width/region, provider
path/SHA/face and explicit proposal acceptance are caller declarations. This is
not intent-only B1 PASS, arbitrary-font editing or multi-owner reflow.

Source: [Osaka guideline notice](https://www.pref.osaka.lg.jp/documents/63648/guideline_tsuuchi.pdf),
`evaluations/realpdf/corpus/word_osaka_guideline.pdf`, page1, Span62/MCID28 underP58.
Original SHA before/after:
`9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe`.
This wording change is an evaluation artifact, not an official corrected notice.

Original displayed Unicode and ActualText:
`国立感染症研究所においてガイドラインが作成されましたので、参考に送`

New displayed Unicode and ActualText:
`国立感染症研究所においてガイドラインが作成されていたので、参考に送`

The single edit replaces offsets `[19,28)` with `作成されていたので`.
All9 replacement characters are newly shaped; this is not a deletion workaround.
24 retained glyphs keep their original code/GID/font resource/program.
33→33 characters, one line. The first candidate qualified; no alternative was tried.

Layout width347.318447265625pt fits the existing348.94000244140625pt width.
Approved rectangle remains `(176.05999755859375,335,525,351)`pt. No font shrink,
region expansion, new MCID, sibling movement or cross-owner redistribution.

## Implementation and font proof

Production changes are confined to `pdfeditor/span_edit.py`. Existing
`plan_paragraph_edit`, ParagraphShaper, font-resource creation, collision/clip
checks, single-owner witness and Transaction ActualText compare-and-set are reused.
One internal replacement, insertion or deletion is allowed per save; no-op remains.
Insertion at the owner endpoints, wrapping, owner aggregation and capacity
excess still refuse. Acceptance/schema and `single-span-actualtext-1` ownership
remain unchanged. Existing deletion sidecars remain usable.

`plan_single_span_edit` supplies read-only shaping/capacity qualification.
A nonempty edit requires an explicit provider `path`, SHA-256 and face0.
This bounded implementation accepts **only the exact original embedded program**;
another provider is refused rather than silently substituted. Source `/F1` contains
all needed Unicode characters. The extracted program stays under ignored tmp/.

- Original/provider SHA:
  `5e5ded281865f30d096f0f2b8b55969b1f80ea9396cc4e98af9acba406f2eddf`
- Shaped instance SHA:
  `7443a3982f11668db9d24914395658256a23cbc72724de176b1ce84108388df5`
- Embedded generated subset SHA:
  `3bf4d00e11b1745fcb2d2b2a3cdeda02a126c8dd8477e8469425a10424cfb63d`
- New glyph resource `/PRF1`, xref173; independent CID codes0001–0009.

The9 generated GIDs are3128,6210,2121,2176,2138,2104,2131,2146,2139.
Each maps to one Unicode character. Independent audit verifies the saved CID/code,
GID, embedded outline, hmtx advance and PDF width against the pinned provider.
Each provider advance is256font units, nominal PDF width1000 and planned
advance10.56pt. Per-glyph outline hashes are in the compact summary.
FontTools reports dropping the source `meta` table during subsetting; the generated
program has its own hash and is not claimed byte-identical to the provider.
The original embedded programs remain byte-identical.

Saved native/generated font resources may represent the same approved appearance.
A new sidecar font binding records the original resource/xref/program/appearance
and writer-produced subset records. Reopen verifies program hashes and each
painted generated Unicode/GID/outline/advance against the original provider.
Arbitrary mixed source styles remain refused. Forged matching subset hashes cannot
hide changed outlines/advances. No font-name fallback is used.

## Structural and drawing audit

All97 MCIDs stay unique/tree-backed. Target ownerID/MCID, ParentTree/backlinks,
child order and full structure graph remain exact except the authorized leaf
ActualText value. The other96 envelopes, ActualText, glyph code/GID/font/positions
are exact. All4 foreign drawings and original font programs are unchanged.
The original clip and graphics wrappers remain; only owned text operators change.
Both serialized results restore, and final no-op observations equal the replacement.
Original SHA is unchanged.

Exactly **two successful real saves**:

| Operation | Readback | MuPDF144dpi | Poppler144dpi | Time including audit |
| --- | --- | --- | --- | --- |
| Nonempty replacement | restored | exterior0pixels | exterior0pixels | 6.068518s |
| Saved-revision no-op | restored | whole-page0pixels | whole-page0pixels | 4.702624s |

Qualification3.667245s; total14.438386s. Exterior mask is the unchanged approved
rectangle with intersecting raster cells, no extra margin or tolerance change.
No-op keeps the saved program unchanged. No additional real saves were performed.

pypdf logged undefined old IDs4,28,33,167 during full-load no-op, as in the prior
case. Read-only traversal of both output trailer graphs found172reachable objects,
zero missing references and none of these warning IDs reachable. All four output
hashes and both `restored` states were independently rechecked without saving.

## Focused validation

**123 distinct passed, 2 skipped**:

- `.venv/bin/python -m pytest -q tests/test_single_span_actualtext.py`:
  50passed in12.86s. Covers equal/shorter/longer nonempty replacement, insertion,
  deletion, save/no-op/re-edit across native/generated style boundaries, retained
  codes, generatedfont proof, coverage/provider/region/clip/structure/stale/semantic
  refusals, forged subset outline/advance and partial-publication failure.
- `.venv/bin/python -m pytest -q tests/test_tagged_page_proposal.py tests/test_transaction.py tests/test_pdf_save.py tests/test_marked_content.py`:
  73passed,2skipped in108.03s. Skips require unavailable AES provider.

Counts/timings are the completed Astra execution; collection counts were checked
on handoff. Earlier28/43-test overlapping batches are not added again. No full
suite, prior real deletion lifecycle or three-page evaluation was rerun.

Real evaluation:
`.venv/bin/python -m evaluations.real_japanese.osaka_span_replacement`.
Outputs: `tmp/osaka-single-span-replacement/`. The script refuses to overwrite
successful output PDFs. Runtime remains Linux/Python3.12.14, PyMuPDF1.27.2.3,
pypdf6.10.0 and Poppler26.05.0.

## Output SHA-256 and limits

- `tmp/osaka-single-span-replacement/replacement.pdf`: `06c33c997f6963516f6c59b48669723878b9259f14f6d878d7753cfabefb6cb2`
- `tmp/osaka-single-span-replacement/replacement.json`: `cb51dff117fedbf505e03fd3d6fd834b8cfe540956ef9f7845ce55ffc7498bd6`
- `tmp/osaka-single-span-replacement/noop.pdf`: `d2923d688d9250958b3d2f4bfdb96802e42e388513f2d18ce5795105d0cbce55`
- `tmp/osaka-single-span-replacement/noop.json`: `75a99c4b6e87acf8ce449b037099f189a0716a4e01afdfd348dbdc591bd4f848`

[Machine-readable measurements](osaka-single-span-replacement-summary.json)
include newglyph evidence. PDF/font/sidecar/rasters stay uncommitted in tmp/.

Replacement/insertion growth is proven in fixtures; the real evaluation uses
same-length nonempty replacement. Alternate programs, contextual multi-glyph
shaping, multiple owners, line wrapping and automatic follower movement remain
outside this bounded path. There is no claim of arbitrary Word/PDF-UA editing.
