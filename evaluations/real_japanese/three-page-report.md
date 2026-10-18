# Real Japanese three-page sequential reflow

The full validation is incomplete. The [limited no-op follow-up](three-page-noop-report.md)
resolved the page-2 and page-3 differences: the saved third-page extension now
reopens `restored` with zero changed pixels on all 13 pages in both renderers.
The subsequent **B growth onto page 3 passes**, with C moved down and zero changes
outside approved regions on all 13 pages in both renderers. B's bounded
shorten/regrow pair also passes, including reuse of the same continuation and
exact C glyph/render placement after regrowth. A's repaired shorten/regrow pair
also **passes**: all 13 pages match the good input in both renderers, and the
actual owned glyph coordinates/bounds match exactly. The earlier failed pair
and its diagnosis are retained below as historical evidence.
Further re-edit lifecycle and final no-op are unrun; **REAL-WORLD THREE-PAGE SEQUENTIAL
REFLOW remains NOT VALIDATED**.

Starting main: `f93c8f47bc8d2c277b01464178d75f3e1cc23e89` (PR #75 merged).

## A shorten/regrow with witnessed relative spacing — PASS

**REAL-PDF THREE-PAGE A SHORTEN/REGROW — PASS** (2026-10-18).
Starting commit: `570dc71def7ac408333030e6d69d0299064019e7`.
Implementation/evaluator/regression commit:
`37c8b913f12227048de6f10da05031f1259b7000`.

Exactly two real-PDF edits resume the good `regrow-B.pdf` and its bound sidecar:
`regrow-B → shorten-A-spacing-fixed → regrow-A-spacing-fixed`.
The input PDF SHA is
`121541869471fd0ea28f32d11c8faf4547d1bb17846fc206cea5bf264cc6ca71`;
sidecar SHA is
`2cb13bbb21d612de77c2c2847b1337a05689c7e8d3a246f16c0699b2e944c9c5`.
Input hashes, the existing 13-page audit, both occupied continuations and
`restored` are checked before editing. No earlier qualification, ownership
extension, no-op or B edit is repeated. Legacy `shorten-A`, `regrow-A` and the
diagnostic artifacts are preserved; their PDF hashes remain unchanged.

### Production change and current-revision proof

`pdfeditor/paragraph.py` permits a baseline-only translation to use candidate
lines when text is unedited and x, width, indentation and minimum height are
unchanged. Every candidate still requires the current paragraph's retained-unit
and style identity, exact complete line ranges, and native Tm reproduction for
every current source glyph with the same CTM, basis and rise. A stale or altered
candidate cannot authorize a location merely because it appears in a sidecar.

The proven current baselines are expressed relative to their first line. These
offsets supply the existing layout's line gaps. Normal minimum-height and ink
leading clamps, wrapping, paragraph gap/floor, region and collision checks still
apply. The final layout must have exactly the witnessed line ranges and equal
`target_first + source_offset` at every line. Translating the resulting positions
back must exactly recover every proven current baseline. Both comparisons use
exact binary64 equality, without a new tolerance. Subtracting at another origin
can change an offset's final binary64 bit; requiring an exactly reversible
translation handles that case without ignoring an output-coordinate difference.
An unrepresentable translation or a clamp that changes the proven positions
refuses. Changed text/styles/wrapping use normal layout or refuse.

The ordinary saved `physical_layout.lines` record the translated coordinates.
On the next open/edit these are proved again against that revision's native
operators before deriving offsets. There is no historical PDF authority or new
history store. `shared_flow.py` retains its remeasurement at the final follower
baseline; only its comment changes. Writer position guards and Transaction,
ownership and publication behavior are unchanged. The evaluator adds the bounded
`a-spacing-fixed` phase, keeping all previous output names untouched.

An empty candidate list uses the ordinary empty-layout path: it has no first
line or relative position to prove. This compatibility guard and its focused
empty/no-op test were added after the real run. It does not change the nonempty
spacing proof used in that run; the emptied A slot has text edits and therefore
no source-line candidate. No additional real-PDF save was needed or performed.

### Measured placement

All values below are writer baselines in pt. Every D1–B line passes exact forward
translation and inverse-translation checks in both saved stages. After regrowth,
all their planned baselines also equal the initial `regrow-B` values.

| D1 line | Input / fixed regrowth | Fixed shortening |
| --- | ---: | ---: |
| 1 | 126.4 | 116.41998291015625 |
| 2 | 151.95999755859376 | 141.97998046875 |
| 3 | 177.639990234375 | 167.65997314453125 |
| 4 | 203.19998779296876 | 193.219970703125 |
| 5 | 228.7599853515625 | 218.77996826171875 |
| 6 | 254.43997802734376 | 244.4599609375 |
| 7 | 279.9999755859375 | 270.01995849609375 |
| 8 | 305.5900024414062 | 295.6099853515625 |
| 9 | 331.2699951171875 | 321.28997802734375 |

The critical D1 line-2→3 gap remains **25.67999267578125 pt**. D1's first-line
movement is −9.980017089843756 pt, then the exact inverse. Its minimum line
height remains **25.55999755859375 pt**.

| Follower | Characters / lines on page 2 | Fixed short first / last | Input / fixed regrowth first / last |
| --- | --- | --- | --- |
| D2 | 87 / 3 | 346.8499755859375 / 398.0899963378906 | 356.8299926757812 / 408.07001342773435 |
| D3 | 158 / 5 | 423.6499938964844 / 526.0299682617188 | 433.6300109863281 / 536.0099853515625 |
| D4 | 153 / 5 | 551.7099609375 / 654.0699462890625 | 561.6899780273437 / 664.0499633789062 |
| B | 148 / 4 | 679.6599731445312 / 756.4559936523438 | 689.639990234375 / 766.4360107421875 |

A changes **126→86→126 characters**. Shortening has 86 characters in three
page-1 lines and no page-2 glyphs: `P1-next` occupancy is `None`, its source glyph
selection is empty, and its logical range is `[86,86]`. Regrowth has 111 characters
in three page-1 lines plus 15 characters in one page-2 line at baseline **100 pt**,
using the same `continuation-5627be817d85c2462eda17cb` slot and `[111,126]` range.
B remains 181 characters/five lines, including 33 characters/one line on page 3
at **100 pt** in the same `continuation-afdebcf635632e109f04ecb8` slot.
C remains 130 characters/129 glyphs/four lines, with baselines
`[126.4,151.95999755859376,177.63992919921876,203.19998779296876]`.

### Independent audit and identity

Each save reopens **`restored`** and passes all 13 pages of the independent
outside-confirmed-regions audit: **MuPDF 0 / Poppler 0 changed pixels**, at
144 dpi. After shortening, page 3's complete raster and its 162 owned glyphs
equal `regrow-B`. After regrowth, the comparison uses the complete pages with
no region masks: **all 13 pages are pixel-identical in both renderers**.
All 1,136 owned glyphs (111 page 1, 863 page 2, 162 page 3) have exactly equal
Unicode, GID, physical origin and bounds. In particular D1–B's maximum physical
position/bounds difference is **0 pt**, replacing the legacy
0.0001220703125 pt drift. Page-2 Poppler changes improve **5,424→0**; MuPDF stays 0.
This result is independently measured, not inferred from writer baselines.

Logical text returns to the input. Source-output marker identities, generated
slot creation bindings, existing slot IDs and continuation destination contracts
are unchanged. `P1-next` releases only `slot-1` (D1); `B-next` releases only
`slot-6` (C). Neither authority is created or broadened. Retained/reused glyph
codes, GIDs, font resources/programs, foreign text, drawings and fixed elements
pass the existing independent checks. Images, links and annotations remain
empty, as in this original. Provider identity is preserved; replaced generated
subsets follow the existing glyph-level font audit described below.
The original PDF SHA remains
`70ce8722348b21c10a7035a5b6dd09dc641b032d7ec33588e9bc8cb711ef17f2`.

### Tests, command, timing and artifacts

**26 distinct focused tests pass**, with no skips: 20 tests covering witnessed
positions and retained-source serialization (19 passed in the 7.17 s batch, then
the corrected logical-snapshot roundtrip fixture passed in 1.39 s); five direct
source-adjacency, line-height/bounds, approved-floor and release-collision cases
passed in 142.54 s. These 25 passed before real saves. The additional empty
candidate/no-op regression passed in 0.82 s. The initial exact-offset-subtraction
check exposed the binary64 reversibility issue above; the final regression also
retains the existing 1e-9 clamped-candidate refusal. Stale evidence, style change,
wrap/height/bounds changes and non-reversible translations are covered.

```bash
.venv/bin/python -m pytest -q tests/test_witnessed_source_line_positions.py tests/test_retained_source_positions.py
.venv/bin/python -m pytest -q \
  tests/test_source_adjacency_flow.py::test_noedit_reproduces_every_glyph_without_justify \
  tests/test_source_adjacency_flow.py::test_two_edits_wrap_push_following_and_preserve_foreign \
  tests/test_rich_layout.py::test_witnessed_line_gaps_keep_minimum_height_metrics_and_bottom_limits \
  tests/test_sequential_extension.py::test_approved_follower_floor_keeps_d_out_of_the_release_region \
  tests/test_sequential_extension.py::test_low_approved_floor_still_refuses_the_release_region_collision
.venv/bin/python -m evaluations.real_japanese.three_page_edit \
  --output tmp/courts-three-page/run-01 --phase a-spacing-fixed
```

Runtime: Linux, Python **3.12.14**, PyMuPDF **1.27.2.3**, Poppler **26.05.0**.
The single real phase took **362.614758 s** including input verification;
shortening took **167.258913 s**, regrowth **183.597211 s**, each including its
save/readback and audits. Heavy tests and real evaluation ran serially. The
nonempty current-D1 proof was also checked read-only before either save.

| Output | SHA-256 |
| --- | --- |
| `shorten-A-spacing-fixed.pdf` | `17e140ee06f6742f0434b55082a911b18cfb1c83a1b0af882427b82654b913cd` |
| `shorten-A-spacing-fixed.json` | `93a26993f43a1f19731eb173008d34cd6f71299ac4fc1f5f53a5e6a43646209c` |
| `regrow-A-spacing-fixed.pdf` | `7e58e00309a903865dce1c0006e4cdbde7546046aa7a9d894228be6800fffaab` |
| `regrow-A-spacing-fixed.json` | `a062bf5f33b24416a1e92844a242631f94a2712ecbc417a0341bb7d89f13b8ce` |

The local run directory retains each stage's report, audit, destination proofs,
input comparison and summary, plus `a-spacing-fixed-summary.json`; PDFs/fonts/tmp
are not committed. Per-line evidence for all D1–B lines is in each new summary's
`relative_spacing`. The previous failed pair is not repaired or relabelled.
Additional re-edits, final no-op and the remaining lifecycle are **unrun**; the
full three-page verdict remains **NOT VALIDATED**. No full suite, PR or merge is
performed. This completes only the bounded A shorten/regrow repair.

## Bounded investigation of A return-position drift — historical unresolved checkpoint

Starting at `d3be83a47f33fe6a808b6521ba0c52157bdad27e`, read-only comparison
of the three existing PDFs, sidecars and writer plans identifies **two distinct
stages of loss**. No production change or additional PDF save is performed.

1. On shortening, D1's baseline changes, so `source_line_candidates` rejects the
   otherwise unchanged paragraph. Shared measurement, local measurement and the
   writer therefore use `resolved.lines` from float32 PDF observations instead
   of the proven saved writer baselines. The first differing interval is between
   lines 2 and 3: the original planned gap is **25.67999267578125 pt**, whereas
   its observed gap is **25.679931640625 pt**. The shortened PDF actually writes
   that smaller interval. Its third baseline is **167.659912109375 pt**, rather
   than the rigid-translation result **167.65997314453125 pt**.
2. Subsequent observed gaps and the unchanged **25.55999755859375 pt** minimum
   are accumulated again. For example, the original observed line-6→7 gap is
   **25.5599365234375 pt** and is clamped to that minimum. The shortened seventh
   baseline becomes **270.02001953125 pt**. Returning its first baseline to
   126.4 yields the already measured **280.0000366210937 pt**, not the original
   **279.9999755859375 pt**. The last shortened line is also reobserved as
   321.29010009765625 instead of its writer plan's 321.2900695800781 pt. Further
   accumulation produces D1's final **331.2701171875 pt**, whose excess is then
   propagated through the unchanged follower gap/floor rules to D2, D3, D4 and B.

This is not a new failure of source-Tm serialization: D1's original seventh-line
native Tm y is **561.920043945**, already shortened to **571.899963379**, and
returned to **561.919921875**. The displacement begins in layout before writing.
The existing no-op precision fixes remain intact. The first two D1 lines return
exactly; later lines do not. The full per-line plan/observation/native-Tm evidence
is `tmp/courts-three-page/run-01/a-return-spacing-diagnosis.json`, generated by
the preserved read-only `tmp/a-return-spacing-diagnosis.py` probe.

The important limitation of the required resume input is that **shortening has
already changed the actual line spacing**. Its binding stores only the current
writer lines and current observations; `previous_model_sha256` and source-adoption
records contain hashes, not recoverable historical line coordinates. Reusing
the shortened writer plan under a new baseline would preserve its current
spacing but cannot recover the original spacing. Conversely, simply taking the
old `regrow-B` coordinates would not reproduce the current shortened native Tm
and would bypass the current-revision proof requirement.

A bounded future repair can preserve proven relative line spacing before an
unchanged follower is moved, then validate its translated result against current
minimum height, ink metrics, wrap, gap/floor and bounds. That alone cannot undo
the loss in the already saved `shorten-A`. Resuming this legacy revision also
needs an explicit historical-reference validation contract, or a separately
authorized reconstruction of shortening from the good input with the repaired
path. Neither is introduced as an implicit coordinate override here. This is
the handoff boundary for a focused Astra investigation, rather than a speculative
arithmetic patch followed by a long save attempt.

Existing PDF/sidecar hashes in the failed-pair section remain unchanged, including
the original SHA. The measured difference therefore remains **5,424 → 5,424**
Poppler pixels on page 2; no fixed output exists. All prior all-page outside-region
checks, page-3 equality, restored states and identity/release checks remain valid
as historical evidence, not a new validation run. No focused tests are run because
production code is unchanged; no new real-PDF evaluation, further edit, no-op,
full suite, PR or merge is executed. **A shorten/regrow remains FAIL**, and further
re-edit validation remains blocked on the return-position repair.

## A shorten/regrow — FAIL at final input comparison

Starting at `d4aa6394453759eaa2f10738b8739e3c8d52b592`, the new
`--phase a-short-regrow` performs exactly two saves:
`regrow-B → shorten-A → regrow-A`. The existing input PDF SHA is
`121541869471fd0ea28f32d11c8faf4547d1bb17846fc206cea5bf264cc6ca71`;
its sidecar SHA is
`2cb13bbb21d612de77c2c2847b1337a05689c7e8d3a246f16c0699b2e944c9c5`.
Both match the prior summary and 13-page audit, and the input opens `restored`.
No qualification, extension, earlier edit or no-op is replayed. Production code
and all previous precision repairs are unchanged.

**Shortening passes.** Replacing `LONG → FIND` empties the existing A continuation
and returns D1's first baseline to the approved page-2 start. B's full text and
its page-3 continuation remain intact. The entire third page has exactly equal
162 owned glyphs and zero raster differences in both renderers versus `regrow-B`.
Only after this gate passes does the phase replace `FIND → LONG` on the saved
shortened revision.

**Regrowth saves and restores, but fails the final equality requirement.**
Both per-edit independent audits pass on all 13 pages outside the unchanged
confirmed regions. The final unmasked comparison with `regrow-B` instead finds
5,424 changed Poppler pixels on page 2, bounds `(170, 539, 1076, 563)` at 144 dpi.
MuPDF is pixel-identical on all 13 pages; Poppler pages 1 and 3–13 are also exact.
The evaluator exits 1 at that final comparison and does not emit a PASS verdict.

| Measurement | Input `regrow-B` | `shorten-A` | `regrow-A` |
| --- | --- | --- | --- |
| A characters / total lines | 126 / 4 | 86 / 3 | 126 / 4 |
| A page-1 characters / lines | 111 / 3 | 86 / 3 | 111 / 3 |
| A page-2 continuation | 15 characters, 1 line, baseline 100 pt | 0 characters/glyphs/lines, occupancy `None`, range `[86,86)` | same 15 characters, 1 line, baseline 100 pt, range `[111,126)` |
| D1 first baseline (pt) | 126.4 | 116.41998291015625 | 126.4 |
| D1 last planned baseline (pt) | 331.2699951171875 | 321.2900695800781 | 331.2701171875 |
| B logical characters / total lines | 181 / 5 | 181 / 5 | 181 / 5 |
| B first page-2 baseline (pt) | 689.639990234375 | 679.6600952148438 | 689.6401428222656 |
| B page-3 continuation | 33 characters, 1 line, baseline 100 pt | unchanged, nonempty | unchanged, nonempty |
| C first baseline (pt) | 126.4 | 126.4 | 126.4 |

All logical paragraphs return exactly to the input text. Both generated-slot
occupancies and ranges also return exactly. C's complete planned baselines stay
`[126.4, 151.95999755859376, 177.63992919921876, 203.19998779296876]` throughout.
Its 129 glyphs and B's 33 page-3 glyphs are physically identical to the input
after each operation, as is page-3 rendering in both renderers.

Page 2 is different: 83 of D1's 305 glyph positions and all 86 D2, 157 D3,
152 D4 and 148 page-2 B positions differ after regrowth, with maximum observed
origin displacement **0.0001220703125 pt**. A's 15 continuation glyphs match
exactly. For example, D1's seventh planned baseline changes from
`279.9999755859375` to `280.0000366210937`; its last baseline changes by
`0.0001220703125` pt, and that discrepancy propagates through the followers.
This is a real placement/raster difference, not merely unequal plan metadata.
The region and line coincide with the historical 5,424-pixel failure, but this
trigger involves moving the followers up and back, rather than an unchanged
no-op. The current saved-line candidate path requires unchanged placement;
the previous no-op repair has not been removed. A separate bounded follow-up
must establish how to preserve witnessed line spacing through follower movement.
No serializer, tolerance, region mask or production code is changed here, and
no third edit/save is attempted.

Both saves preserve source-output marker identities, existing slots and creation
bindings, destination contracts, page ownership and independent release owners:
`P1-next → slot-1 (D1)` only and `B-next → slot-6 (C)` only. A reuses
`continuation-5627be817d85c2462eda17cb`, whose creation source remains
`26e8c6411db21a425eaff8a3d5ac4e485ed7c1fe393787ad7f50e303bebbf09f`
and destination-contract SHA remains
`0113cb683f98cca208a677d5c20323706305e07f6599f4789f0320e030e6b29f`.
B reuses `continuation-afdebcf635632e109f04ecb8`; its creation binding remains
the value documented below. Same-transaction release proofs pass for the
occupied destinations. The two independent audits each verify 1,084 retained or
reused glyphs' exact code/GID/resource/program; approved provider glyph counts
are 12 and 52 respectively. Original foreign glyphs, drawings and fixed elements
are unchanged, with both renderers showing zero outside-region pixels on all
13 pages. Images, links and annotations remain empty in this original.

| Output | PDF SHA-256 | Sidecar SHA-256 |
| --- | --- | --- |
| `shorten-A` | `15b6254e6ad0f06af196219204f37e2f796321bce5dabc304f74a80f6ad0cb50` | `3dd24f49b2be86230111e937c5cbb87fbd6e6dc52cacdcefd3b97391eadc9371` |
| `regrow-A` | `41cbba8b8349af74d2ebb53517647af034bd327ca60bee4fa4c953355ebddcb1` | `739db4cd51f904f8804d7e1cfccbb3e5eb92ef5d87e9ffc8f64a10a4aa931acb` |

Shortening takes **176.651983 s** including its comparison. Total phase time is
**383.689788 s**, exit 1. The regrowth timer was interrupted by the failing
comparison before its stage summary could be written. Its request-file through
completed-comparison-file timestamps span approximately **194.791991 s**; this
is an artifact-derived interval, not an instrumented operation duration.
The evaluator now writes the measured comparison verdict and stage summary
before raising on a failed gate; this reporting-only improvement is syntax
checked without rerunning the saves. The original SHA is still
`70ce8722348b21c10a7035a5b6dd09dc641b032d7ec33588e9bc8cb711ef17f2`.

Evidence remains in `tmp/courts-three-page/run-01`: both PDF/sidecar/report/audit
sets, `shorten-A-summary.json`, both `*-input-comparison.json` files,
`regrow-A-diagnosis.json`, destination proofs and the post-run failure summary
`a-short-regrow-summary.json`. The log is `tmp/courts-three-page-a-short-regrow.log`.
Only read-only glyph aggregation and a page-2 raster diagnosis follow the
two saves. Syntax compilation and whitespace checks pass; production-focused
tests, full suite, extra saves, further edits, final no-op, PR and merge are not
performed. **The A shorten/regrow PASS is not declared.**

```bash
.venv/bin/python -u -m evaluations.real_japanese.three_page_edit \
  --output tmp/courts-three-page/run-01 --phase a-short-regrow
```

## B shorten/regrow — bounded PASS

**REAL-PDF THREE-PAGE SHORTEN/REGROW — PASS.** Starting at
`ef8dd78f542cb31fc79eaa99858d65d2895914dc`, this follow-up performs exactly two
edit/saves: `grown-3 → shorten-B → regrow-B`. The input PDF/sidecar hashes match
the growth summary and completed 13-page audit below; it reopens `restored`.
The new `b-short-regrow` phase rejects existing output artifacts, shortens
`B_LONG → B_FIND`, and only after that save, restored state and full audit pass
applies `B_FIND → B_LONG` to the shortened saved revision. It replays no earlier
qualification, extension, no-op or growth. No production change is needed.

| Measurement | Initial `grown-3` | `shorten-B` | `regrow-B` |
| --- | --- | --- | --- |
| B characters / lines | 181 / 5 | 138 / 4 | 181 / 5 |
| B page-2 characters / lines | 148 / 4 | 138 / 4 | 148 / 4 |
| B page-3 characters / glyphs / lines | 33 / 33 / 1 | 0 / 0 / 0 | 33 / 33 / 1 |
| B page-3 logical range | `[148,181)` | `[138,138)` | `[148,181)` |
| B page-3 occupancy / baseline | occupied / 100 pt | `None` / none | occupied / 100 pt |
| C characters / glyphs / lines | 130 / 129 / 4 | 130 / 129 / 4 | 130 / 129 / 4 |
| C first baseline | 126.4 pt | 116.41998291015625 pt | 126.4 pt |

The short text has 138 characters, rather than the original pre-growth 139,
because the earlier explicitly authorized growth already removed its trailing
space. This pair performs no further trimming. B's four page-2 baselines remain
exactly the values in the growth section below. On regrowth its full text,
fragment ranges, line counts, baselines and continuation occupancy equal the
initial growth. Shortening leaves no text or glyph IDs in the dormant page-3
slot, and C's first baseline equals the existing page-3 source-region floor
exactly; no tolerance is introduced.

C's complete **planned** baselines are:

| Line | Initial growth (pt) | Shortened (pt) | Regrown (pt) |
| --- | ---: | ---: | ---: |
| 1 | 126.4 | 116.41998291015625 | 126.4 |
| 2 | 151.95999755859376 | 141.97998046875 | 151.95999755859376 |
| 3 | 177.639990234375 | 167.659912109375 | 177.63992919921876 |
| 4 | 203.19998779296876 | 193.219970703125 | 203.19998779296876 |

The regrown third planned baseline differs from the initial plan by
**−0.00006103515625 pt**. This is recorded, not rounded away or described as
bit-identical planning. In contrast, all **129 actual C glyphs** have exactly
equal Unicode/GID/origin/bounds between initial growth and regrowth, with maximum
origin delta **0.0 pt**. A separate read-only whole-page-3 comparison at 144 dpi
also gives **0 changed pixels in MuPDF and 0 in Poppler**, including both B and C.
It takes 0.741829 s and performs no save. These physical-placement checks are the
final equality gate, added to the reproduction phase after verifying the saved
results; the two edits are not rerun. The original phase's provisional PASS is
accepted only after this additional gate passes.

Both stages preserve slot
`continuation-afdebcf635632e109f04ecb8`, its creation binding and its destination
contract. Its creation source remains
`45bf2173d2f4f1d583284dfcad37c4cacbfb4bbb0c52dbfc097de173ff55d6e1`, and its
destination-contract SHA remains
`707a11a5cdff34fd1fc101e3c826950e5d6bdd010726f2edf175207326423e27`.
All existing slot identities, immutable source markers and generated creation
bindings are preserved. `P1-next` remains released only by D1 (`slot-1`), and
`B-next` only by C (`slot-6`); their destination contracts remain unchanged.
A's continuation occupancy is unchanged. Regrowth's destination proof records
the same-transaction release and safe final C placement.

Each output reopens **`restored`** and independently passes **all 13 pages with
0 changed pixels outside confirmed regions in both renderers at 144 dpi**.
Foreign glyph geometry/code/GID/font program and drawings remain equal; the
original's images, links and annotations remain empty. Each audit checks 1,081
retained/reused glyphs for exact code/GID/resource/program equality; approved
provider glyph checks number 12 after shortening and 55 after regrowth. The
provider specification/program is unchanged. Page-2 `/PRF2` is regenerated for
the replaced B glyphs; its regrown subset SHA equals the initial growth subset
SHA. This is not an exception for retained glyphs, whose program equality remains
mandatory. Page-3 `/PRF1` and the remaining registry entries are unchanged.

| Saved output | PDF SHA-256 | Sidecar SHA-256 | Operation time |
| --- | --- | --- | ---: |
| `shorten-B` | `9866f31094b31b3442fbf5b8942ffec3506783ea6249060250c30b4128ff23ce` | `ae797aa7a53aa114e91f3c9bc3ae04ed367a96dd28f7b32b680383a41c9990ba` | 168.396367 s |
| `regrow-B` | `121541869471fd0ea28f32d11c8faf4547d1bb17846fc206cea5bf264cc6ca71` | `2cb13bbb21d612de77c2c2847b1337a05689c7e8d3a246f16c0699b2e944c9c5` | 179.656200 s |

The CLI phase completes in **360.235449 s**, exit 0, including input validation
and both stages. The separate read-only placement comparison is timed above.
Original SHA-256 remains
`70ce8722348b21c10a7035a5b6dd09dc641b032d7ec33588e9bc8cb711ef17f2`.
All output artifacts are under `tmp/courts-three-page/run-01`: each stage retains
its PDF, sidecar, request, report, audit and summary; regrowth also retains its
destination proof. `regrow-B-placement-equality.json` links the extra physical
placement evidence, and `b-short-regrow-summary.json` records the bounded verdict.
The execution log is `tmp/courts-three-page-b-short-regrow.log`.

```bash
.venv/bin/python -u -m evaluations.real_japanese.three_page_edit \
  --output tmp/courts-three-page/run-01 --phase b-short-regrow
```

Syntax compilation and whitespace checks pass. No extra edit/save, test suite,
A edit, further re-edit lifecycle, additional no-op, PR or merge is performed.
The full three-page sequential-reflow verdict remains **NOT VALIDATED**.

## Measured third-page growth — bounded PASS

This follow-up starts at `ccdced1459058a21d91890eb6db9bebcb8aacebe` and performs
**one real edit/save**, using the already verified
`extension-3-noop-roundtrip.pdf` and its sidecar. Its SHA matches both the sidecar
and the completed 13-page, 144-dpi whole-no-op audit; opening returns `restored`.
Initial qualification, ownership extensions, earlier edits and no-op saves are
not replayed. No production change is needed.

The exact request matches `before-page-3-refusal.json`, which records a capacity
refusal with neither PDF nor sidecar published. It replaces B offsets **126–139**,
including the explicitly approved trailing space, with the existing `B_LONG`
text under `style-3`. The newly added `grow-3` phase refuses any pre-existing
`grown-3*` artifact, calls the existing `grow`, `save` and `used_destination`
checks once, and stops before the lifecycle.

| Measurement | Before | After |
| --- | --- | --- |
| B logical characters | 139 | 181 |
| B total lines | 4 | 5 |
| B page-2 fragment | 139 characters, 138 glyphs, 4 lines | 148 characters/glyphs, 4 lines |
| B page-3 continuation | unused | 33 characters/glyphs, 1 line, range `[148,181)`, baseline 100 pt |
| C logical characters / glyphs / lines | 130 / 129 / 4 | 130 / 129 / 4 |

B's page-2 baselines remain
`[689.639990234375, 715.3199829101562, 740.87998046875, 766.4360107421875]`.
Its occupied page-3 slot is `continuation-afdebcf635632e109f04ecb8`, in `release-3`
under destination `B-next`. C's four baselines move as follows:

| C line | Before (pt) | After (pt) |
| --- | ---: | ---: |
| 1 | 116.41998291015625 | 126.4 |
| 2 | 141.97998046875 | 151.95999755859376 |
| 3 | 167.65997314453125 | 177.639990234375 |
| 4 | 193.219970703125 | 203.19998779296876 |

Each moves down **9.980017089843756 pt**. The destination proof records C's
removal and safe final placement in the same transaction that creates B's
continuation. Release owners stay independent: `P1-next → slot-1 (D1)` only,
`B-next → slot-6 (C)` only. Existing slot identities, immutable source markers,
generated creation bindings and destination contracts remain intact. The new
B continuation's creation binding names the exact input PDF revision. A's
existing page-2 continuation and its creation identity remain in place.

The output saves and reopens **`restored`**. The completed independent audit
reports **0 changed pixels outside confirmed regions on all 13 pages**, in both
MuPDF and Poppler at 144 dpi. All-page foreign Unicode/geometry/code/GID/font
program and drawing checks pass; images, links and annotations are empty in this
original. It checks **1,081 retained/reused glyph occurrences** for exact
code/GID/resource/program equality and **55 provider glyphs** for approved
program, outline and advance. Existing fonts remain unchanged; the edit adds
two owned subsets, `/PRF2` on page 2 and `/PRF1` on page 3, from the unchanged
approved provider. Maximum planned-versus-observed origin error is
0.00003662109375568434 pt, with no tolerance changes.

| Artifact | SHA-256 |
| --- | --- |
| Input PDF | `45bf2173d2f4f1d583284dfcad37c4cacbfb4bbb0c52dbfc097de173ff55d6e1` |
| Output `grown-3.pdf` | `029cd5f552df88ce9f83e3ca2e7dc1d471d230237e738a0c4eabcc9ab0e17226` |
| Output `grown-3.json` sidecar | `1a30c58d6f82eea76e41e935c3e14e68c01eefe6e278ea52b3cbf1d29c09cb2f` |
| Unchanged original | `70ce8722348b21c10a7035a5b6dd09dc641b032d7ec33588e9bc8cb711ef17f2` |

The CLI phase completes in **186.108990 s**, exit 0. The narrower timer inside
the phase records **185.991619 s** through summary construction; these are two
timers of the same single run, not separate attempts. Evidence is preserved under
`tmp/courts-three-page/run-01/grown-3` (`-input.json`, `-request.json`, PDF,
sidecar, `-report.json`, `-audit.json`, `-destination-proof.json`, `-summary.json`).
The log is `tmp/courts-three-page-grow-3.log`.

The scoped command was:

```bash
.venv/bin/python -u -m evaluations.real_japanese.three_page_edit \
  --output tmp/courts-three-page/run-01 --phase grow-3
```

The evaluator passed syntax compilation and whitespace checks. No new tests or
production changes were needed. No further edit/save, shortened/regrown/re-edited
lifecycle, broad regression, full suite, PR or merge is performed in this
follow-up. This bounded growth result does not establish the full lifecycle PASS.

## Original and approved scope

The original is the Japanese courts' public judgment **97044**, 13 pages:
<https://www.courts.go.jp/assets/hanrei/hanrei-pdf-97044.pdf>.
Its SHA-256 is `70ce8722348b21c10a7035a5b6dd09dc641b032d7ec33588e9bc8cb711ef17f2`.
The downloaded bytes are used unchanged; no PDF conversion, regeneration or font
substitution prepares the source. Editing examples below are test replacements,
not a representation of the court's decision.

The related body spans section 1's factual history. Rendered pages and coordinates
were reviewed before qualification. Each source owner is an independent complete
paragraph; no split paragraph is guessed or joined across a page boundary.

| Page | Original observed lines | Accepted owners | Scope |
| --- | --- | --- | --- |
| 1 | `p1-l25`–`p1-l27` | A (`P1`) | “なお、Ａは、気仙沼市に対して…” through “入居することはなかった。” |
| 2 | `p2-l1`–`p2-l26` | D1, D2, D3, D4, B | Five consecutive factual paragraphs, starts at lines 1, 10, 13, 18, 23 |
| 3 | `p3-l1`–`p3-l4` | C | Complete paragraph ⑹; the next paragraph and section 2 remain foreign |

The page 1 title, reasons heading, earlier paragraphs, later page 3 paragraphs and
all remaining pages stay foreign. Page numbers remain fixed foreign paint.
Explicit `inspect_fixed_foreign` witnesses bind the current PDF, exact observed
footer glyphs/codes and bounded margin geometry independently at both seams.
They confer no editable capacity or release authority. All source/destination
regions stop at or above the exact footer ink top, **775.18798828125 pt**.

The original embedded MS-Mincho program supplies both retained glyphs and explicit
new-glyph coverage (program SHA-256
`6075dfa89ba064a9275d4c7bfab1c3e2d414abf4e19deaafcd2788cd19501652`).
B1 qualification precedes editing: original-code/font/operator provenance,
reproduction of the selected lines, explicit source style confirmation and
provider coverage all pass. The actual page 2 and page 3 adoptions are reobserved
from their respective saved edited revisions, not from these original probes.

## Three bounded production changes

1. `propose_page_flow` accepts explicit `source_style_confirmations` for existing
   physical style IDs. The existing per-glyph Tc/Ts witness contract verifies every
   supplied value, and acceptance recomputes it from the bound current PDF. This
   connects the existing confirmation mechanism to B1; it never replaces unknown
   tracking with zero. Irregular source TJ gaps remain source adjacency, while new
   glyphs use the explicitly confirmed style. Tw remains zero in this path.
2. Continuation can inspect a directly displayed page's known transparency group
   with `/I` omitted. ISO 32000-1:2008 §11.4.7/Table 147 defines normal page output
   as effectively isolated ([official specification](https://raw.githubusercontent.com/adobe/dc-acrobat-sdk-docs/master/docs/pdfstandards/PDF32000_2008.pdf),
   physical pages 348 and 358). The original group dictionary remains bound. Explicit
   unsupported flags, unknown color spaces and malformed PDF booleans still
   refuse; this does not authorize Form XObject groups. PDF BooleanObject values
   are interpreted as their PDF values, not Python object truthiness.
3. The source-position witness now follows original text operators and advances
   for later glyphs in Tj/TJ, preserving native decimal Tm placement when the exact
   occurrence remains in place. Trace matching, layout and source identity still
   use their existing observations. The existing same-position/CTM/rise/basis
   guard prevents the witness from restoring a moved glyph to its old location.
   No raster tolerance or geometric bound was relaxed.

The third change resolves a measured no-op failure: MuPDF reported 0 changed
pixels but Poppler reported 172 for one retained “件” on page 2. Its original
operator-derived position was `(465.25, 239.09)` in PDF coordinates; rewriting the
float32 trace position as a new Tm crossed a raster threshold. Preserving original
operator arithmetic makes both renderers identical on every page. The failed
no-op and diagnostic one-glyph experiment are excluded from successful evidence.

## Preservation and authority gates

Each saved edit reopens `restored`. Existing source markers, generated creation
bindings and continuation destinations remain stable through both adoptions and
later edits. The page 2 release belongs only to D1; the page 3 release belongs only
to C. Actual nonempty generated occupancy and the same-Transaction release witness
must agree, and the source followers must move down from their adopted baselines.

Each extension immediately performs a whole-document no-op comparison in MuPDF
and Poppler at 144 dpi. Every edited stage checks all 13 pages outside the approved
regions with zero changed pixels, plus exact foreign Unicode/GID/position/bounds,
original codes/resource names/font programs, drawings, images, links and annotations.
The source has drawings but no images, links or annotations; the latter checks
exercise empty equality here and do not establish preservation of nonempty examples.
Every painted owned glyph is compared independently against its glyph plan.
Retained/reused occurrences must keep exact code/GID/resource/program equality;
new occurrences must match the approved provider's outline and advance.

No-op saves and ownership extensions preserve the complete generated-font
registry. An actual text replacement may regenerate a subset whose generated
occurrences were replaced, including reuse of its resource alias. That is not an
exception for retained glyphs: their program equality remains mandatory in the
independent audit. Provider specifications/program hashes are unchanged, and
before/after registry changes are recorded with the glyph-level audit evidence.

Capacity and unauthorized-owner refusals must publish neither PDF nor sidecar.
The original SHA must remain unchanged. A long request is applied unchanged before
and after its required ownership extension; refusal and request JSON preserve the
exact payload and input revision hashes.

## Explicit trailing-space edit and remaining limit

The original A and B each end in a literal single space carrying a separate zero-Tc
style (A after the first edit: offsets 89–90; B: 138–139). The initial growth request
preserved that space, which became a trimmed-only style in a new fragment; readback
refused because it had no painted output witness. No PDF/sidecar was published.
General support for such cross-fragment dormant whitespace styles was not added.

The successful growth request explicitly includes that owned trailing space in
the replacement range and deletes it. This is an ordinary specified text edit,
not source preprocessing or automatic cleanup. It removes no body or foreign
character outside that range. The revised exact request is first tested against
the pre-extension saved revision and must refuse, then retried after extension.
The earlier rejected growth is not counted as successful evidence.

## Reproduction

Acquire the unchanged original into the registered corpus path, then use a fresh
output directory for `initial`. Resume phases require that existing saved history.
Poppler is mandatory; unavailable renderer checks cannot silently pass.

```bash
mkdir -p evaluations/realpdf/corpus
curl --fail --location \
  https://www.courts.go.jp/assets/hanrei/hanrei-pdf-97044.pdf \
  --output evaluations/realpdf/corpus/courts_97044.pdf
.venv/bin/python -m evaluations.real_japanese.three_page_edit \
  --output tmp/courts-three-page/fresh --phase initial
.venv/bin/python -m evaluations.real_japanese.three_page_edit \
  --output tmp/courts-three-page/fresh --phase extend-2
.venv/bin/python -m evaluations.real_japanese.three_page_edit \
  --output tmp/courts-three-page/fresh --phase extend-3
.venv/bin/python -m evaluations.real_japanese.three_page_edit \
  --output tmp/courts-three-page/fresh --phase lifecycle
```

The evaluator verifies the pinned source hash, extracts the embedded provider,
qualifies B1, saves every revision/report, audits both renderers and writes a final
summary only after all gates pass. Full suite is not required and was not run.
