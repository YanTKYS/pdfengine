# Sequential ownership extensions on one restored edited history

**REAL-WORLD SEQUENTIAL OWNERSHIP EXTENSION VALIDATED — PASS**

Starting main: `fe339acb0986b56f55656db7db1195679c602dc9` (PR #71 merged). No production code changed.

The existing PR #71 `next_page` path and the PR #70 `append_to_region` path chain on the same edited history. Each
extension starts from the latest saved, restored revision; nothing is re-accepted from the original.

## Source and command

Source: the unmodified ten-page **LibreOfficeへの移行** migration paper.
- URL: https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf
- SHA-256: `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3`, identical before and after the
  evaluation.
- New-glyph provider: `BIZUDMincho-Regular.ttf` (google/fonts), SHA-256
  `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`.

Neither file is committed.

The run was on Windows 11 (10.0.26200):
- Python 3.12.14 in a fresh `.venv` from `requirements.lock.txt`;
- PyMuPDF 1.27.2.3, fontTools 4.64.0, uharfbuzz 0.55.0, pypdf 6.10.0;
- Poppler `pdftoppm` 26.07.0 on `PATH`.

```powershell
$env:PYTHONUTF8 = "1"   # the verdict line contains U+2014
.\.venv\Scripts\python.exe -m evaluations.real_japanese.sequential_extension `
  --output tmp/sequential/run-2 --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

The evidence run (`run-2`) ran alone and exited **0** after 134 min.

An earlier run (`run-1`) completed the first extension, both refusals, the second acceptance and its no-op. It then
hit `MemoryError` inside fontTools during the grow stage's final validation. That run shared the machine with three
lifecycle tests running in parallel. The grow stage re-run alone from `run-1`'s saved no-op revision succeeded in
702 s at about 590 MB, and the complete rerun stayed between 70 MB and 1.1 GB. This was an environment limit, not a
production defect, and nothing was changed to obtain `run-2`.

## 1. Page-8-only edit

- The flow owns only the complete innovator paragraph A (`p8-l24`–`p8-l27`).
- Replacing `望むタイプでもあります。` with `望むタイプでもあります。試行の結果を共有します。` grows A from 164 to
  **176 characters** on four page-8 lines.
- Saved revision `11de0dc5d5a0fca3f048d53c6160c80a92fbcc7da515e25ea04ec8b21a081c76` reopens `restored`.
- The 54-character growth refuses with `paragraphs exceed all explicitly confirmed shared regions`, and nothing is
  published.

## 2. First extension: the next page (PR #71 path)

Page 9 of the saved revision is re-observed. B (`p9-l1`–`p9-l3`, 146 glyphs) is adopted as `slot-1` in region
`page-9`, with:
- the caller's 26.4 pt A→B page break;
- release region `release-9` (`[56.8, 110, 540, 174]`, first baseline 122 pt) on page-program boundary ordinal 0,
  releasing only B.

The following were refused:
- confirming B's occupied rectangle as empty: `composed text collides with an unselected glyph`;
- accepting on the original PDF: `owned-flow proposal belongs to another PDF revision`.

Acceptance reports the joint reproduction `reproduced` (7 lines).

The growth then succeeds and is saved as `cd857af23b8dc67a1512c80bba5b591d748743eb04e4b2666175060138e679fe`, which
reopens `restored`:
- A has **206 characters**: 199 on page 8, plus `に役立てます。` in generated slot
  `continuation-3faffb690d270cf0bf3f4ea8` at **122 pt** on page 9;
- B moves **142 → 148.4 pt**;
- the release Transaction witness names exactly `slot-1`, with all 146 glyphs.

`saved.pdf` and this grown revision are byte-identical to the PR #71 evaluation's `saved.pdf` and `1-grow.pdf`.

## 3. Refusal before the second extension

From the grown revision, A's further growth to the 130-character `LONG` text (164 → 282 characters) refuses with
`paragraphs exceed all explicitly confirmed shared regions`. Nothing is published. The unowned C directly below B
bounds the `page-9` region at **200.271454 pt**.

## 4. Second extension: the same page (PR #70 path)

A new B1 proposal is built on the **grown** revision for the current lines `p9-l5`–`p9-l24`. It is appended to region
`page-9` as six new owners:

| Owner | Slot | Glyphs | First current glyph ID |
|---|---|---:|---:|
| C | `slot-2` | 147 | 153 |
| D | `slot-3` | 196 | 300 |
| E | `slot-4` | 147 | 496 |
| F | `slot-5` | 166 | 643 |
| G | `slot-6` | 107 | 809 |
| H | `slot-7` | 123 | 916 |

That is **886 glyphs** in total. C starts at current glyph ID 153, not the original 146, because the seven generated
continuation glyphs come first in this revision. Every new `source_adoptions` record and selection names the grown
SHA `cd857af2…`. B→C uses the **current** gap, 19.999963 pt.

Accepting the second proposal on the original PDF, and on the first saved revision `saved.pdf`, both refuse as
stale revisions.

At acceptance:
- The joint no-edit reproduction covers **28 current lines**, including the generated continuation line.
- A and B paragraphs and slot bindings, source-output records, the generated slot's creation binding,
  `continuation_destinations`, `destination_bindings` and `generated_fonts` all compare equal to the grown
  revision's.
- **The release owners of `A-next` stay exactly `['slot-1']`.** The new owners do not widen the existing release
  authority, and B's adoption record is unchanged.

## 5. Extension no-op

The no-op saves `3ee598a896875a4fea8a1cdb4fe67395ed0f95206c23b3b79eadd2d242d4dec6` and reopens `restored`:
- it preserves all **1,238 current glyphs**: code, resource, font program and GID are equal, and the origin error is
  0.0 pt;
- **MuPDF (144 dpi) and Poppler (1600-pixel page scale) are pixel-identical on all ten pages.**

The current placement is reproduced exactly:
- A: four lines on page 8, then the 7-character continuation at 122 pt;
- B: 148.4, 168.800024 and 189.300024 pt;
- C–H at their current first baselines: 209.299988, 279.0, 369.200012, 436.5, 526.099976 and 594.599976 pt.

## 6. The previously refused growth after the second extension

`SHORT → LONG` succeeds, saving `080859466622f2dd7326485fe2bc702ca1710d0d65fc4aef6b85cec00c53c07c`, which reopens
`restored`:

| Paragraph | Before (no-op) | After growth | Movement |
|---|---|---|---:|
| A | 199 + 7 characters; continuation 1 line at 122 | 199 + **83** characters; continuation **2 lines** at 122 and 142.400024 | — |
| B | 148.4 – 189.300024 | 168.800024 – 209.700049 | +20.400024 |
| C | 209.299988 – 250.799988 | 229.700012 – 271.200012 | +20.400024 |
| D | 279.0 – 341.0 | 299.400024 – 361.400024 | +20.400024 |
| E | 369.200012 – 410.100006 | 389.600037 – 430.500031 | +20.400025 |
| F | 436.5 – 499.600006 | 456.900024 – 520.000031 | +20.400024 |
| G | 526.099976 – 568.099976 | 546.5 – 588.5 | +20.400024 |
| H | 594.599976 – 635.5 | 615.0 – 655.900024 | +20.400024 |

The newly owned capacity is actually used:
- B's planned ink reaches **210.777155 pt**, beyond the 200.271454 pt bound that refused before the second extension;
- the saved B glyph bounds intersect **46 glyph bounds of the previously unowned C**.

The edit is also verified as follows:
- the release witness still names only `slot-1`;
- maximum glyph-origin error is 0.000031 pt;
- on all ten pages, foreign content is unchanged and MuPDF shows zero changed pixels outside the edited regions.

## 7. Save/reopen lifecycle

Every stage starts from the previous saved revision and reopens `restored`. Throughout:
- the source-output identities of all eight owners are unchanged;
- the generated slot keeps its creation binding;
- the destinations stay unchanged;
- paragraph texts equal the expected texts.

| Stage | Edit | A (page 8 / page 9) | B first → last | C first | Saved SHA-256 | Max origin error (pt) |
|---|---|---|---|---:|---|---:|
| 0 extension no-op | — | 199 / 7 | 148.4 → 189.300024 | 209.299988 | `3ee598a896875a4fea8a1cdb4fe67395ed0f95206c23b3b79eadd2d242d4dec6` | 0.000024 |
| 1 grow | A `SHORT` → `LONG` | 199 / 83 (2 lines) | 168.800024 → 209.700049 | 229.700012 | `080859466622f2dd7326485fe2bc702ca1710d0d65fc4aef6b85cec00c53c07c` | 0.000031 |
| 2 second | B `変化を肯定的` → `新しい変化を肯定的` (149 characters) | 199 / 83 | 168.800024 → 209.600073 | 229.600037 | `28d8d4f88efc7ce49273240dfc7d09289d7a5c1f964c2dc38019299a004e5683` | 0.000031 |
| 3 shorten | A back to 164; B reverted | 164 / **0** (continuation empty) | **142.0** → 182.800049 | 202.800012 | `072c9410fcc7095068e93cb132ed302969f0e00141642d43056f832ac091ef4a` | 0.000037 |
| 4 regrow | A 164 → 282 | 199 / 83 (same generated slot) | 168.800024 → 209.600073 | 229.600037 | `aa4642b009dede5109e70fa896421218ee3c56ac5e4c42bc956ff8523328bc6f` | 0.000031 |
| 5 final no-op | — | 199 / 83 | 168.800024 → 209.600073 | 229.600037 | `3da0f00141de6aeb3baecb896d0fda7c618b8f7e19253e18e9fa931c4afaa8e5` | 0.000031 |

On every stage the audit reports:
- outside-region MuPDF changed pixels 0 on all ten pages;
- foreign text, drawings, images, links and annotations equal;
- Unicode exact;
- retained codes, resources, GIDs and programs equal;
- provided outlines and advances equal;
- untagged structure and operator nesting preserved.

The final no-op is whole-document MuPDF pixel-identical to the regrow revision. The final original-vs-final Poppler
comparison outside the edited regions has **0 changed pixels**.

**Observed limitation (no refusal here).** After shortening, C sits at 202.800012 pt, **6.5 pt above** its original
209.299988 pt. The second extension adopted C at its current 20.0 pt gap below the moved B; its original gap was
26.4 pt. On this page C stays below the 174 pt release region (C's top is about 194 pt), so nothing refuses. In the
synthetic regression, where the release region reaches the follower, the same effect makes the edit refuse
(`collides with an unselected glyph`) and publish nothing. This is recorded as the next blocker.

## Three-page case (synthetic only)

`tests/test_sequential_extension.py::test_three_page_sequential_next_page_extensions` uses a synthetic three-page
PDF with no further production change:
1. a saved page-1 edit;
2. a `next_page` adoption of B on page 2 with release `A-next`, then growth onto page 2, saved;
3. from that revision, a `next_page` adoption of C on page 3 with its own release destination `B-next`;
4. B continues on page 3 at 62 pt, and C moves 80 → 88.5 pt;
5. second edit, shortening, regrowth and the final no-op all reopen `restored`, keeping the earlier generated slot's
   creation binding and every source-output identity.

**No real three-page related body has been validated.**

## Regressions and focused tests

`tests/test_sequential_extension.py` (3 tests) passed on Windows: **3 passed, 817.94 s**. It ran concurrently with
`run-1`.
- next-page then same-page extension on one history;
- refusal when a newly adopted follower would enter an existing release region;
- three-page sequential next-page extensions.

No production module changed, so no other focused tests were required by `CLAUDE.md`. The full suite was not run.
