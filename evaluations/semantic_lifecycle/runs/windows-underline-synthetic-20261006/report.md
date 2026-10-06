# Semantic lifecycle external validation report

> Windows execution has not been performed by the PR that added this harness. This report is evidence only for the execution it describes.

- **Verdict:** `PASS`
- **Windows execution:** yes — this run executed on Windows
- **Executed:** 2026-10-06T10:06:32.238915+00:00 → 2026-10-06T10:06:50.945693+00:00
- **Platform:** Windows-11-10.0.26200-SP0; Python 3.12.14; PyMuPDF 1.27.2.3; fontTools 4.64.0; uharfbuzz 0.55.0; pypdf 6.10.0
- **Poppler:** pdftoppm version 26.07.0; **LibreOffice:** unavailable
- **Repository:** `769a0a9f4b4b497d1c157ece1129167d1f8ec838` (dirty: False); runtime digest `4b6f9925159160c9ad63cee19f082728bfadf8c70548678fd89d40a3665659e3`

## Inputs

| Input | File | SHA-256 |
|---|---|---|
| PDF | `document.pdf` (1 pages; producer 'pypdf'; LibreOffice claimed: False) | `037893f6ddc5aab9977f0f545e26b4ce4d8392453ec7c819447ada9cb66f9971` |
| sidecar | `shared-flow.json` | `6637f79c18b423be162ccea1d8f5fb1eca55de2ab5fba0a289536344d91e43f0` |
| font_a | `font-a.ttf` | `ab1a678f8f5ddf565ac313e203f7fde276cc75a10cee7d68b223025eaf8317f2` |
| font_b | `font-b.ttf` | `bf5783fb6fded1696e9350a1cd7a611cf4398c941cc17819a906ce8c7a30c9bc` |

## Target

slot `slot-0`, page 1, sidecar `pdfengine-shared-flow-2`, source font `Courier`, generated fonts ['/PRF1']

## Stages

| Stage | Required | Status | Note |
|---|---|---|---|
| `preflight` | yes | **PASS** |  |
| `baseline` | yes | **PASS** |  |
| `confirm` | yes | **PASS** |  |
| `text_baseline` | yes | **PASS** |  |
| `add` | yes | **PASS** |  |
| `noop_1` | yes | **PASS** |  |
| `noop_2` | yes | **PASS** |  |
| `edit_remap` | yes | **PASS** |  |
| `recipe` | yes | **PASS** |  |
| `style` | yes | **PASS** |  |
| `font` | yes | **PASS** |  |
| `publish_a` | yes | **PASS** |  |
| `remove_from_bundle_a` | yes | **PASS** |  |
| `publish_b` | yes | **PASS** |  |
| `negatives` | yes | **PASS** |  |
| `tamper` | yes | **PASS** |  |
| `continuity` | yes | **PASS** |  |
| `text_only_control` | yes | **PASS** |  |
| `raster_mupdf` | yes | **PASS** |  |
| `raster_poppler` | no | **PASS** |  |
| `inputs_preserved` | yes | **PASS** |  |

## Revisions

| Revision | Text | Size | Tracking | Tw | Edges | Font | Provenance | Body ops | PDF SHA |
|---|---|---|---|---|---|---|---|---|---|
| `confirmed` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 28 | `037893f6ddc5` |
| `u00-text-baseline` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 16 | `cee22a4b2a24` |
| `u01-add` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 25 | `7b2717682e7a` |
| `u02-noop-1` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 25 | `7b2717682e7a` |
| `u03-noop-2` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 25 | `7b2717682e7a` |
| `u04-edit-remap` | `AA B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 27 | `9eb0438a30a8` |
| `u05-recipe` | `AA B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 27 | `504e9266ec7c` |
| `u06-style` | `AA B` | 13 | 1/4 | 0 | 0 | `ab1a678f8f5d` | caller-confirmed-current-semantic | 27 | `3beb79a4d06d` |
| `u07-font-b` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 27 | `4e75aae05369` |
| `bundle-a` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 27 | `4e75aae05369` |
| `u08-remove-from-bundle-a` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 18 | `0eb267e8f195` |
| `bundle-b` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 18 | `0eb267e8f195` |
| `control-01-edit` | `AA B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 18 | `d9f33d3e91df` |
| `control-02-style` | `AA B` | 13 | 1/4 | 0 | 0 | `ab1a678f8f5d` | caller-confirmed-current-semantic | 18 | `0abf6ae1fd55` |
| `control-03-font-b` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 18 | `3aaafbd3203f` |

## Underline lifecycle

> Windows underline execution has not been performed by the PR that added the underline mode.

- **Validation kind:** `semantic-underline-lifecycle`; **Windows underline execution:** yes — this run executed on Windows
- **Semantic version transition:** [2, 3] (explicit `decorations.add`)

| Revision | Transition | Version | Decorations | Rectangles (PDF x0 yu x1 yl) | Paint group | Text group | MuPDF |
|---|---|---|---|---|---|---|---|
| `confirmed` |  → | 2 | — | — | `none` | `6602eaaad898` | `88755aad520c` |
| `u00-text-baseline` | D 2→2 | 2 | — | — | `none` | `ea99442e3b91` | `88755aad520c` |
| `u01-add` | E 2→3 | 3 | [0,3) | 20.017578 58.7 39.359375 58 | `a7c898bfc2f6` | `ea99442e3b91` | `bc359c7fca2a` |
| `u02-noop-1` | D 3→3 | 3 | [0,3) | 20.017578 58.7 39.359375 58 | `a7c898bfc2f6` | `ea99442e3b91` | `bc359c7fca2a` |
| `u03-noop-2` | D 3→3 | 3 | [0,3) | 20.017578 58.7 39.359375 58 | `a7c898bfc2f6` | `ea99442e3b91` | `bc359c7fca2a` |
| `u04-edit-remap` | D 3→3 | 3 | [0,4) | 20.017578 58.7 47.363281 58 | `a2a795ccf1ee` | `0a275874520e` | `dc796a779cb3` |
| `u05-recipe` | E 3→3 | 3 | [0,4) | 20.017578 58.8 47.363281 58.2 | `243cfe3b0429` | `0a275874520e` | `2a2618804299` |
| `u06-style` | E 3→3 | 3 | [0,4) | 20.019043 58.7 50.393555 58.05 | `3c794ec59f89` | `9fc58909b344` | `b640f4b93c11` |
| `u07-font-b` | E 3→3 | 3 | [0,4) | 20 58.7 51.447266 58.05 | `ec58687ff23f` | `398918b4a9b8` | `c0bf17e5b984` |
| `bundle-a` |  → | 3 | [0,4) | 20 58.7 51.447266 58.05 | `ec58687ff23f` | `398918b4a9b8` | `c0bf17e5b984` |
| `u08-remove-from-bundle-a` | E 3→3 | 3 | none | — | `none` | `398918b4a9b8` | `640d42c376fc` |
| `bundle-b` |  → | 3 | none | — | `none` | `398918b4a9b8` | `640d42c376fc` |
| `control-01-edit` | D 2→2 | 2 | — | — | `none` | `0a275874520e` | `915fc976108d` |
| `control-02-style` | E 2→2 | 2 | — | — | `none` | `9fc58909b344` | `bc315cc391b2` |
| `control-03-font-b` | E 2→2 | 2 | — | — | `none` | `398918b4a9b8` | `640d42c376fc` |

| Check | Result | Meaning |
|---|---|---|
| no-op #1 / #2 | yes, yes, yes, yes | PDF bytes, owner block, semantic state, underline evidence identical |
| edit remap | [[0, 3]], [[0, 4]] | before → after ranges |
| recipe | yes, yes, yes | x unchanged, y changed, authority kept |
| style | yes, 0.0 | decorations kept; max em geometry error (pt) |
| font | yes, yes, yes, yes | provider B, recipe kept, y unchanged, generated font B |
| publication A | yes, restored, yes | bytes, fresh process, raster |
| remove (from bundle A) | yes, yes, yes, yes | decorations [], version 3, no paint group, text group kept |
| publication B | yes, restored, yes, yes | bytes, fresh process, bundle A immutable, text-only |
| MuPDF 144 dpi | baseline_differs_from_add: yes, add_equals_noop_1_and_2: yes, recipe_differs_from_previous: yes, style_differs_from_previous: yes, font_differs_from_previous: yes, removed_equals_text_only_control: yes, candidate_a_equals_bundle_a: yes, candidate_b_equals_bundle_b: yes | PASS |
| Poppler 144 dpi | baseline_differs_from_add: yes, add_equals_noop_1_and_2: yes, candidate_a_equals_bundle_a: yes, candidate_b_equals_bundle_b: yes, removed_equals_text_only_control: yes | PASS |

Expected refusals (the runtime must refuse each):

- A.pdf+B.json: `source output ownership: shared flow PDF revision changed`
- B.pdf+A.json: `source output ownership: shared flow PDF revision changed`
- resealed decoration recipe tamper: `semantic version 3 body is not the canonical text and underline body`
- paint geometry tamper: `source output ownership: shared flow PDF revision changed`

- **Final state (last bundle):** semantic version 3, decorations [], paint group absent

## Artifacts

- `result.json` — machine-readable result (schema in the README)
- `artifacts/` — `u0N-*/`, `control-0N-*/` (semantic-candidate-*), `bundle-a/`, `bundle-b/`, `tamper/`, `raster/`
- `logs/harness.log` — stage log with tracebacks of refused/failed stages
