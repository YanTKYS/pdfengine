# Approved follower spacing on one restored edited history

**REAL-WORLD FOLLOWER SPACING POLICY VALIDATED — PASS.** Measured on the unmodified Japanese
LibreOffice migration PDF, pages 8→9, with all ten pages audited. The cloud download blocker was
resolved by reusing the checksum-matching original already present on Windows. No production code,
evaluator assertions, tolerances, or font qualification rules changed for this validation.

## Reproduction and environment

- Evaluated PR #73 head: `ecdadef817be83f84f7e593c7ad188182e4e3ab2`; starting main:
  `045e3551c4a576d40e4d00efbea60a9d8707ba7b` (PR #72).
- Original: [LibreOfficeへの移行](https://wiki.documentfoundation.org/images/archive/8/84/20130907172916%21MigrationLibreOffice-ja.pdf),
  local `evaluations/realpdf/corpus/lo_migration_ja.pdf`.
  SHA-256 `13665875311aae3a4115016c65190957b3e1aef945c7b88c58437ca6b14ea5f3` verified before and after.
- Font: `tmp/b3/fonts/BIZUDMincho-Regular.ttf`, SHA-256
  `468ee6d9b149ca144809e03841bf18740ecf014e055a00da6ecaf1aaf4165af2`, from the
  [pinned google/fonts revision](https://raw.githubusercontent.com/google/fonts/63833b7d10bb1f86a8f0b91cba2d3ae1f68d1aa3/ofl/bizudmincho/BIZUDMincho-Regular.ttf).
  No substitute font or PDF version was used; neither binary is committed.
- Windows, Python 3.12.14, PyMuPDF 1.27.2.3 / MuPDF 1.27.2, pypdf 6.10.0,
  Poppler `pdftoppm` 26.07.0; repository `.venv`, `PYTHONUTF8=1`.
- Start **2026-10-13 19:05:44.646 JST**, finish **20:56:39.530 JST**;
  **6654.876 seconds (1 h 50 min 54.876 s), exit 0**. The evaluator ran alone without concurrent heavy tests.

```powershell
$env:PYTHONUTF8 = '1'
.venv/Scripts/python.exe -u -m evaluations.real_japanese.follower_spacing `
  --output tmp/follower/validation-win-20261013 `
  --font tmp/b3/fonts/BIZUDMincho-Regular.ttf
```

Use a new output directory for another run. Local evidence: `tmp/follower/validation-win-20261013/summary.json`,
per-stage `*-report.json`, PDFs and sidecars; sibling `.log` and `-run.json` record output and timing.
[Compact measured evidence](follower-spacing-windows-summary.json) is committed without external binaries.

## Measured history and placement

The evaluator rebuilt the PR #72 history: own A on page 8, edit and save; adopt B on page 9 with
release-only continuation `A-next`; grow A and save/reopen; then append C–H from that **current saved
revision**, without re-accepting the original PDF. Before the second extension, further A growth refused.

The current witness and approved policy remain separate:

| Value | Measured / explicitly approved value (pt) |
| --- | ---: |
| Current B last baseline | 189.3000244140625 |
| Current C first baseline | 209.29998779296875 |
| Current B→C gap | 19.999963378906244 |
| Approved minimum B→C gap | 19.999963378906244 |
| Approved C baseline floor, region `page-9` | 209.29998779296875 |

C's line also occurs at that baseline in the original PDF; that comparison is audit evidence only.
The original 26.4 pt gap is **not** substituted for the current gap: that policy refused because it
would move the extension no-op to 215.7000244140625 pt instead of 209.29998779296875 pt.

| Saved stage | B first | B last | C first | Reopen |
| --- | ---: | ---: | ---: | --- |
| Extension no-op | 148.4 | 189.300024 | 209.299988 | restored |
| Shorten A (`SHORT → FIND`) | 142.0 | 182.900024 | 209.299988 | restored |
| Regrow A (`FIND → LONG`) | 168.800024 | 209.700049 | 229.700012 | restored |
| Second edit, B (`SECOND`) | 168.800024 | 209.600073 | 229.600037 | restored |
| Final no-op | 168.800024 | 209.600073 | 229.600037 | restored |

On shortening, the generated continuation becomes empty, B returns to 142 pt, and C stays at the
approved floor. **C no longer rises to PR #72's historical 202.800012 pt.** D–H also keep their no-op
baselines exactly: D 279, E 369.20001220703125, F 436.5, G 526.0999755859375, H 594.5999755859375 pt.
On regrowth and the second edit, C follows B's last baseline at the approved minimum gap; the existing
0.002 pt comparison bound was unchanged. Full-precision values are in the compact evidence.

## Preservation and authority

- **Extension no-op: all ten pages pixel-identical in MuPDF (144 dpi) and Poppler (1600 px scale).**
  Independent original-identity audit retains 1238 glyphs with 0 pt origin error.
- Final no-op: all ten pages MuPDF pixel-identical.
- Separate post-run visual check: shortened page 9 and the original page 9 are entirely Poppler
  pixel-identical (1131×1600 RGB, 0 changed pixels), also reviewed visually. This supplements the main evaluator.
- Every stage: foreign text, drawings, images, links and annotations equal; all ten pages checked;
  outside-approved-region MuPDF changed pixels **0**. Original-versus-final Poppler outside-region
  changed pixels **0**. Unicode extraction, retained codes/resources/GIDs/font programs, and supplied
  outlines/advances pass the independent audits.
- Second acceptance carries forward A/B paragraphs and bindings, existing source-output records,
  generated-slot creation binding, continuation destinations, destination bindings and generated fonts.
  Each saved stage preserves immutable source-output identities, generated-slot creation identity,
  destinations, follows and expected texts. Reopened states pass existing ownership validators.
  Generated fonts remain exactly unchanged across extension no-op; edited revisions retain proved ownership.
- Release owners remain exactly `['slot-1']` (B). Appending C–H grants no authority to occupy A's release
  region. Source structure checks pass and the original SHA is unchanged.
- Page 10 is a separate section and remains foreign. This is a bounded two-page related-body result,
  not arbitrary document-wide layout or automatic relationship inference.

## Measured refusals

| Request | Actual refusal / publication result |
| --- | --- |
| Treat occupied B text as empty continuation capacity | `composed text collides with an unselected glyph`; confirmation refused before writing |
| Grow A before C–H ownership extension | `paragraphs exceed all explicitly confirmed shared regions`; neither `blocked-1.pdf` nor `.json` published |
| Approve original 26.4 pt gap on compressed current revision | Extension no-op would move C; proposal refused |
| Accept against stale original/first-saved revisions | `owned-flow proposal belongs to another PDF revision` |
| Grow final H beyond confirmed capacity | `paragraphs exceed all explicitly confirmed shared regions`; neither `capacity.pdf` nor `.json` published |

A separate small probe reuses the final real saved state and requests a redraw after adding C to
`A-next`'s release owners without approval. It recomputes only the transport checksum, leaving the
confirmed contract unchanged. The runtime refuses the changed contract and publishes neither
`unauthorized.pdf` nor `unauthorized.json`; details are in the compact evidence. This is an actual
authority-tampering refusal on the real revision, distinct from the synthetic low-floor collision test.

The precise low-floor collision remains fixture evidence: with the 116 pt release region, a floor of
110 pt passes extension no-op but shortening would put D at 118.4 pt inside another owner's release
region, so the edit refuses. This real PDF was not altered to manufacture that geometry.

## Regression scope

This follow-up changes documentation and measured evidence only. **No new pytest run and no full suite**
were performed. The existing **917 focused passes** are prior PR implementation evidence. The cloud
follow-up's **3 passed in 258.61 s** on `c57d9f6` are historical, covering:

- `test_approved_follower_floor_keeps_d_out_of_the_release_region`
- `test_low_approved_floor_still_refuses_the_release_region_collision`
- `test_follower_placement_policy_refusals`

They were not rerun without a production change. Newly measured here: the complete real-PDF evaluator,
the saved-state authority refusal, and single-page visual review of the shortened page. The remaining
real-document coverage gap is a suitable related body continuing across three pages; current three-page
coverage is synthetic. No new PR, merge, or authority widening is involved.
