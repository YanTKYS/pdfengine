# Real Japanese font coverage: Okinawa body

**REAL-WORLD JAPANESE FONT COVERAGE EXPANDED — PASS**

Linux / Python 3.12, 2026-10-10; base main
`a740f6fe848f4eef756803731248d6f28922a5ab` (PR #61 merged).
The unmodified published PDF went through production proposal → explicit font
choice/accept → replace → T2 edit/save → restored reopen → second edit/save →
restored reopen. Both revisions also demonstrate real body wrap and push-down.

[Machine-readable measurements](font-coverage-summary.json) / [runner](font_coverage.py).
Third-party PDF/font binaries and rendered images are local, ignored artifacts.

## Input and actual missing coverage

- [Okinawa procurement announcement](https://www.pref.okinawa.lg.jp/_res/projects/default_project/_page_/001/035/156/01_koukoku.pdf), page 2, items (7)/(8).
- Original SHA-256: `bbaa2b12eeaf1c7eac2dc91aca03c00da4ed948e7c14d99d879ff4c3083fd0b3`.
- Same three explicit source lines as B4: `p2-l44`, `p2-l45`, `p2-l46`.
  The [B4 report](body-reflow-report.md) records the hanging-list, width, foreign
  boundaries and ownership evidence. No easier document or preprocessing was used.
- Embedded Japanese MS Mincho subset, xref 22: **346 cmap codepoints**, SHA
  `88c3a4ac5ab893810a6ed5f43efa21bab83bb5d58a2de0e8e4ad432a98439e49`.
- Mixed-font space uses the embedded Arial subset, xref 27: 7 cmap codepoints,
  SHA `bb244b70b266bd673ca80299643fecf5ee95377ce47daa9b6f4b48132ce76013`.
- Added characters absent from **both the original PDF's extracted text and
  the Japanese subset's actual cmap**:

| Character | Codepoint |
|---|---|
| 受 | U+53D7 |
| 口 | U+53E3 |
| 期 | U+671F |
| 確 | U+78BA |
| 窓 | U+7A93 |
| 追 | U+8FFD |
| 送 | U+9001 |
| 郵 | U+90F5 |
| 限 | U+9650 |

The source subset therefore cannot supply the requested input. Previously the
caller also had to extract source programs manually, and proposal could only
choose providers through source-metric qualification. The new path separates
unchanged-source identity from the authority to supply new glyphs.

## Explicit provider and production changes

The evaluation installs the public TrueType
[BIZUDMincho-Regular.ttf](https://raw.githubusercontent.com/google/fonts/main/ofl/bizudmincho/BIZUDMincho-Regular.ttf)
in an additional searched directory. License: SIL OFL 1.1, BIZ UDMincho Project
Authors; fsType 0. Face **0**, SHA and serialized instance SHA:
`468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`.

This provider passes the unchanged source advance qualification for the observed
characters, but **is not the same font program or appearance**. The runner checks
that the outline of shared character `申` differs. Equal metrics do not authorize
silent substitution: the proposal is `needs-choice`, and acceptance explicitly
names this SHA/face with `relation=substituted`. Added text appears slightly
heavier in the full-page Poppler render; this is the approved alternate face,
not a claim of MS Mincho outline equivalence.

- `page_proposal` discovers source programs into a content-addressed cache and
  searches the existing OS roots. It reuses strict metric qualification and
  prefers an eligible byte-identical source program. Explicit file/root inventories
  retain their prior scope unless `include_embedded_fonts=True` is specified.
- Optional `new_text` by logical style inventories new-character coverage, actual
  HarfBuzz shaping, visible glyphs and editable embedding permissions through the
  existing `ShapedFont`. Other programs require explicit substitution even if their
  metrics match. No name-based selection or implicit fallback is added.
- Substitution requires witnessed zero-Tc/Tw source placement. Accept rebuilds
  the normal T2 state and proves the same three no-edit lines and every glyph.
  Source adjacency, original codes/resources and identity mappings are reused.
  Replacement glyphs use the selected font; unedited glyphs do not get redrawn in it.
- The style registry persists choice provenance, SHA, face, instance SHA, checked
  input and `source-equivalence-not-proven`. Reopen checks that identity, provider
  bytes, and generated resource/provider records. A changed/missing file or
  inconsistent selection returns `needs_confirmation`.
- CLI: `propose-page --new-text query.json --font-cache DIR`, optionally
  `--include-embedded-fonts` with explicit roots/files; then
  `accept-page --substitute-provider STYLE=SHA:INDEX`.
- The original font metric verifier, B1/B2 source ownership, T2 follows/allocation,
  Transaction, atomic output/readback and tagged guards remain. No tolerance,
  region or font coverage is fabricated. No new layout/font selection engine.

## Actual edits and line placement

Original item (7):

```text
(7) その他詳細は、「仕様書」及び「応募要領」による。
```

Revision 1 replaces `その他詳細` (5 characters) with
`応募に関する申請期限、受付窓口及び郵送書類の確認その他詳細` (29):

```text
(7) 応募に関する申請期限、受付窓口及び郵送書類の確認その他詳細は、「仕様書」及び「応募要
    領」による。
```

Revision 2 starts from the restored revision 1 and replaces `仕様書` (3) with
`仕様書の追加内容` (8), adding the previously absent `追`:

```text
(7) 応募に関する申請期限、受付窓口及び郵送書類の確認その他詳細は、「仕様書の追加内容」及び
    「応募要領」による。
```

The spaces shown before continuations denote the measured hanging indent, not
inserted logical spaces. Item (8)'s Unicode and original two line breaks remain:

```text
(8) 応募要領等については、沖縄県のWebサイト「沖縄県小中学校次世代型校務支援システム製品選
    定業務」のページからダウンロードすること。
```

| Measurement | Both saved revisions |
|---|---|
| Accepted bounds | `[70.919998, 687.084229, 544.079800, 768.698059]` pt |
| Marker / continuation x | 70.919998 / 88.919998 pt (18 pt hanging indent) |
| Item (7) lines | 1 → **2** |
| Item (7) baselines | 704.640015 / 724.320251 pt |
| Item (8) baselines | 744.000244 / 763.680481 pt |
| Item (8) displacement from original | **+19.680237 pt** |
| Original and preserved follows gap | **19.679993 pt** |
| Follower glyph translation error | **0 pt** |

## Independent saved-PDF audit

| Check | Revision 1 | Revision 2 |
|---|---|---|
| Reopen status | `restored` | `restored` |
| Retained glyphs / newly supplied glyphs | 92 / 29 | 118 / 8 |
| Retained code, resource, GID, program SHA against previous revision | exact | exact |
| New saved glyph outlines and advances against selected provider | exact | exact |
| Maximum glyph origin error against plan | 0.000014823 pt | 0.000019836 pt |
| MuPDF target Unicode | exact | exact |
| Independent pypdf Unicode | exact, extraction whitespace ignored | exact, extraction whitespace ignored |
| All glyph trace bounds inside accepted region | yes | yes |
| Outside-region MuPDF changed pixels at 144 dpi | **0** | **0** |
| Other pages' full raster / foreign text origins and bounds | exact | exact |
| Drawings / images | unchanged | unchanged |
| Source SHA | unchanged | unchanged |

Generated glyphs from revision 1 become ordinary retained source glyphs on the
second edit: their saved resources/programs are kept too. Terminal layout-trimmed
spaces retain logical Unicode; they are not counted as painted glyphs.
The original and outputs are untagged; no structure tree is created or removed.
PDF 1.x operator nesting and marked-content observations remain valid. Poppler
renders before/after show legible new characters, expected two-line text,
unchanged surrounding table/body and no collision with the fixed page number.

Further growth is refused (`paragraphs exceed all explicitly confirmed shared
regions`) without publishing PDF/sidecar. Nonconsecutive selection and an override
that intersects the fixed footer are also refused.

## Reproduction and tests

Obtain the published PDF at `evaluations/realpdf/corpus/word_okinawa_procurement.pdf`
and the linked OFL font in `tmp/b3/fonts/`; verify the SHAs above. Run from the checkout:

```sh
.venv/bin/python -m evaluations.real_japanese.font_coverage \
  --output tmp/okinawa-font-coverage --font-root tmp/b3/fonts
```

The output directory must be new. The runner invokes production embedded-font
discovery, not an evaluation-only extraction/provider injection. It writes the
proposal, explicit acceptance receipt, two revisions/reports, full-page images
and summary. `font-coverage-summary.json` records the actual local evaluation3 run.

`tests/test_font_coverage.py` generates tiny TrueType fonts. Its 12 cases cover
missing subset characters, metric-mismatched new providers, equal-metric alternate
outlines, ambiguity/explicit choice, no-op raster identity, two edits and push-down,
missing glyph atomic refusal, restricted embedding, malformed fonts/TTC headers,
invalid face indices, provider/selection tampering and revocation, the CLI, and
the recorded default axes of a discovered variable TrueType provider.
The focused command also covers B1/B2, adjacency, B4 and shaping:

```sh
.venv/bin/python -m pytest -q -ra -n 4 tests/test_font_coverage.py \
  tests/test_page_proposal.py tests/test_body_reflow.py tests/test_source_adjacency_flow.py \
  tests/test_targeted_page_proposal.py tests/test_tagged_page_proposal.py tests/test_shaped_font.py
.venv/bin/python -m pytest -q -ra -n 5
```

Final-HEAD test counts and skips are recorded in the PR. No third-party binaries
are committed. CFF/OTF new-glyph providers, unsupported shaping and substitution
with nonzero Tc/Tw remain outside this bounded path. Providers/cache files must
remain at their recorded locations; this is not a portable font-bundling system.
Further growth in this particular body is blocked by the fixed footer and the
single accepted region, requiring proved continuation capacity rather than a
font change or an enlarged rectangle.
