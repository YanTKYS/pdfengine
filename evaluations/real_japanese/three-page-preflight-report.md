# Real Japanese PDF three-page preflight

**REAL-WORLD THREE-PAGE SEQUENTIAL REFLOW — NOT VALIDATED.** The requested two sequential
next-page extensions have not been demonstrated on a real PDF. This report records a real
saved-edit boundary refusal, not a three-page success. No production code or safety contract changed.

Starting main: `fbfaa7db79bba853c06c655614854bc9cfdd1930` (PR #73 merged).
The earlier LibreOffice two-page results remain valid within their documented scope.

## Candidate selection

Existing corpus entries were inspected first. Public parliamentary PDFs were then inspected to
look for longer Japanese prose. The following observations bounded this search; they do not prove
that every public PDF, or every passage in the longer candidates, is unsuitable.

| Material | Pages | Observation / selection decision |
| --- | ---: | --- |
| Osaka `guideline_tsuuchi.pdf` | 1 | Too short. |
| Okinawa procurement notice | 2 | Too short; existing body-edit evidence remains useful. |
| LibreOffice migration | 10 | Pages 8–9 are the established related-body case; page 10 is a different section. Earlier pages contain headings, footnotes and paragraphs split across pages, rather than three independently adoptable consecutive page owners. Do not manufacture paragraph boundaries at those splits. |
| LibreOffice 4.1 new features | 8 | Illustrated feature sections and headings; no suitable three-page body selected. |
| Wakayama drone-training proposal guidelines | 6 | Consecutive presentation cautions ④→⑤ on pages 4→5 provide a genuine next-page relationship suitable for a boundary probe. Page 5 later starts section 8, which continues onto page 6 before section 9; this is not a three-page continuation of the selected cautions. |
| [衆憲資第78号](https://www.shugiin.go.jp/internet/itdb_kenpou.nsf/html/kenpou/shukenshi078.pdf/$File/shukenshi078.pdf) | 42 | Reviewed body passages include headings, references and footnotes. The constitutional-text appendix spans only pages 40–41. No qualifying three-page flow selected. SHA-256 `99b188f9feaabc2376e6ac0da4c8c3881ec8f9c32f74938111094d6b7093fd92`. |
| [衆憲資第104号](https://www.shugiin.go.jp/internet/itdb_kenpou.nsf/html/kenpou/shukenshi104.pdf/$File/shukenshi104.pdf) | 71 | Long related subject matter, but reviewed passages contain quotations, section boundaries, footnotes and page numbers. No qualifying three-page flow selected. SHA-256 `fa47c026accf3db63720303380eb293ba238a81c80c4e9b1b2cebabffd1cb101`. |

Other attempted corpus downloads (Takeo, Niigata and Kyoto) returned HTTP 403 in this environment;
they were not evaluated. The downloaded Wakayama original and pinned font were available and
verified, so the measured refusal below is not a network/dependency failure.

## Reproduction

- Original: [令和７年度県職員ドローン技術向上研修業務 公募型プロポーザル実施要領](https://www.pref.wakayama.lg.jp/prefg/020400/d00220063_d/fil/jissiyouryou.pdf).
  Place at `evaluations/realpdf/corpus/word_wakayama_guidelines.pdf`.
- Original SHA-256: `ccd4d63d09331ff6c6636ba6f2cc4c3398a8ff714abefe727cbd0051a6d5aa7c`.
- Font: `tmp/b3/fonts/BIZUDMincho-Regular.ttf`, SHA-256
  `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`, from the
  [pinned google/fonts revision](https://raw.githubusercontent.com/google/fonts/63833b7d10bb1f86a8f0b91cba2d3ae1f68d1aa3/ofl/bizudmincho/BIZUDMincho-Regular.ttf).
- Linux x86_64, Python 3.12.14, PyMuPDF 1.27.2.3 / MuPDF 1.27.2, Poppler 26.05.0.
- Completed run: **2026-10-15 00:15:11.993–00:15:47.183 JST; 35.190 s; exit 0**.
  Exit 0 means the refusal probe and its preservation assertions completed, not that three-page reflow passed.
- The evaluator ran alone. No heavy lifecycle tests ran concurrently.

```bash
PYTHONUTF8=1 .venv/bin/python -m evaluations.real_japanese.three_page_preflight \
  --output tmp/three-page/validation-03 \
  --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

Use a fresh output directory. The runner refuses to reuse an existing directory and verifies both
asset hashes. PDFs, sidecars, proposals and per-save reports remain in that ignored directory.
[Committed measured summary](three-page-preflight-summary.json) records the actual successful probe run.
Neither external PDF nor font binaries are committed.

## Real edit and current-revision ownership

The public B1 proposal selects page 4 line `p4-l37`, the complete caution:

> ④ 応募者は、他の応募者のプレゼンテーションを傍聴することはできない。

It accepts `P1` / `slot-0` in region `R1`, with explicit BIZ UDMincho substitution for new text.
The original selected glyph IDs are 1021–1056. Replace `できない。` with `できません。`, save,
reopen, save a no-op and reopen again. Both statuses are **`restored`**.

| Stage / owner | Characters (including source spaces) | Lines | First/last baseline (pt) | Result |
| --- | ---: | ---: | ---: | --- |
| Original page 4 `P1` | 36 | 1 | 755.8800048828125 | B1 accepted |
| Edited page 4 `P1` | 37 | 1 | 755.8800048828125 | saved / restored |
| Saved no-op `P1` | 37 | 1 | 755.8800048828125 | saved / restored |
| Requested longer `P1` | 120 | No final allocation | No final allocation | capacity REFUSE |
| Proposed page 5 `B` (local `P1`) | 25 | 1 | 70.08000183105469 | observed, **not adopted** |
| Page 6 | No source owner accepted | — | — | not attempted |

The longer replacement appends 83 characters to the saved paragraph. It raises
`paragraphs exceed all explicitly confirmed shared regions`; neither `capacity-refused.pdf` nor
`capacity-refused.json` exists. No page-5 or page-6 space has been granted.

Page 5 is then **reobserved from the saved no-op revision**, not from the original. Its line `p5-l1`
is the next caution `⑤ 指定の時間に遅れた場合は、審査対象としない。`, current glyph IDs 3–27.
The proposal SHA exactly matches current PDF SHA
`6dbb7995f73eeb848a0cfb2d5e8efaa27aff4cfa7ad1dd2c9ba304a0b9e1c561`.

The attempted request names page-5 owner `B` alone as release owner, destination `A-next`,
release region `release-5` at `[96.72000122070312, 45, 498.6000061035156, 80]`, first baseline 50 pt,
and an explicit page-program insertion boundary. Its proposed minimum same-page gap is 26.4 pt;
the selected page-5 source-region floor is 70.08000183105469 pt. These are **requested, not accepted**
values: the seam check refuses before continuation geometry, reproduction or capacity can qualify them.
There are no granted destinations, no generated continuation slots and no release authority.

## Exact boundary refusal

`propose_owned_flow_extension` raises:

```text
page seam skips unselected text after the last confirmed paragraph
```

The current page-4 footer is line `p4-l38`, text `4 `, glyph IDs `[0, 1]`, baseline
**791.0399780273438 pt**, bounds
`[302.0400085449219, 783.4186401367188, 310.3584899902344, 793.3790283203125]`.
It lies below the owned line and overlaps the body's horizontal band
`[96.72000122070312, 498.6000061035156]`.

`post_edit_extension._page_boundary` witnesses an uninterrupted source-owner seam. Its `_band`
test is horizontal; it does not discard foreign lines merely because they lie below the editable
region (whose bottom is 780.2877883911133 pt). Thus the page number blocks this request even though
it is outside the authorized drawing area. The state remains exactly unchanged after refusal.
The proposal API does not publish PDF/sidecar files.

This is an existing conservative contract, not a diagnosed production regression. Ignoring numeric
footers, broadening the release-owner set, or selecting headings/footer text as body would change
the authority/relationship model to force the example through. None was done. In addition, this
particular caution sequence does not continue onto page 6, independently of the footer blocker.

## Measured preservation (bounded to the two page-4 saves)

- Original → edited: **0 changed pixels outside the accepted region on all six pages**, in
  MuPDF at 144 dpi and Poppler at 1600 px maximum dimension.
- Edited → no-op: **all six pages pixel-identical in both renderers**. This is a pre-extension
  no-op, not the requested post-extension no-op.
- Both comparisons: foreign glyphs including exact origins/bounds, drawings, images, links and
  annotations compare equal. Source-output marker immutable identity and generated font ownership
  remain equal through the no-op. Page-4 codes, GIDs, font resource names and program hashes are equal.
- The maximum no-op extracted glyph-origin drift inside the owned text is
  **0.000030517578125 pt** (one glyph's x coordinate). This is reported, not called exact origin equality;
  both raster comparisons remain strictly zero. Foreign coordinates compare exactly.
- Original SHA is unchanged. Capacity rejection publishes neither output; the extension proposal
  grants no ownership and does not mutate the saved state.

Two evaluator-development runs preceded the completed run: the first incorrectly included owned
glyph coordinates in the *foreign* exact-coordinate comparison; the second reached report assembly
but indexed absent optional `continuation_destinations`. The evaluator now excludes owned text only
from the foreign audit, still compares the **entire** no-op raster exactly, independently checks
no-op glyph/font identity, and treats absent destinations as empty. No production assertion or
tolerance was changed. Each run used a separate output directory.

## Tests and remaining work

The existing synthetic three-page lifecycle test passed **1 test in 59.00 s**. It remains synthetic
evidence only. Two existing seam-protection tests passed **2 tests in 20.56 s** (serially after the real probe).

```bash
PYTHONUTF8=1 .venv/bin/python -m pytest -q \
  tests/test_sequential_extension.py::test_three_page_sequential_next_page_extensions --durations=1
PYTHONUTF8=1 .venv/bin/python -m pytest -q \
  'tests/test_next_page_extension.py::test_next_page_extension_refuses_without_authority[skipped]' \
  'tests/test_cross_page_owned_flow.py::test_join_needs_joint_reproduction_and_uninterrupted_seam[intervening-text]' \
  --durations=2
```

The full suite and the prior 917-test selection were not rerun. Production code is unchanged.

Still unmeasured on a real PDF: two accepted next-page extensions on the same saved history;
formerly refused growth using both new pages; independent page-3 release ownership; generated-slot
creation and destination identity across both extensions; post-extension no-op reproduction; and
three-page re-edit/shorten/regrow/restored lifecycle. A suitable three-page body with eligible seams
is still required. This report does not establish that such a PDF cannot be found.
