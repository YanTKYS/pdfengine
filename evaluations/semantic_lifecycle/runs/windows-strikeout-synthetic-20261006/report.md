# Semantic lifecycle external validation report

> Windows execution has not been performed by the PR that added this harness. This report is evidence only for the execution it describes.

- **Verdict:** `PASS`
- **Windows execution:** yes — this run executed on Windows
- **Executed:** 2026-10-06T14:47:40.862667+00:00 → 2026-10-06T14:48:09.310518+00:00
- **Platform:** Windows-11-10.0.26200-SP0; Python 3.12.14; PyMuPDF 1.27.2.3; fontTools 4.64.0; uharfbuzz 0.55.0; pypdf 6.10.0
- **Poppler:** pdftoppm version 26.07.0; **LibreOffice:** unavailable
- **Repository:** `8d3ddbf5fdda8b997ed4aab7f13518b0c2ff5225` (dirty: False); runtime digest `98e71404148d8e69779103a27922c40a65c8f04fed04b2d5adbb8a40d34c23d6`

## Inputs

| Input | File | SHA-256 |
|---|---|---|
| PDF | `document.pdf` (1 pages; producer 'pypdf'; LibreOffice claimed: False) | `037893f6ddc5aab9977f0f545e26b4ce4d8392453ec7c819447ada9cb66f9971` |
| sidecar | `shared-flow.json` | `b8ea31eeb91630305f305eba623f6405c67c4f64a2a89156a0f85e5607e548e2` |
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
| `underline_add` | yes | **PASS** |  |
| `mixed_strikeout_add` | yes | **PASS** |  |
| `mixed_noop` | yes | **PASS** |  |
| `mixed_strikeout_remove` | yes | **PASS** |  |
| `publish_b` | yes | **PASS** |  |
| `v3_to_v4_control` | yes | **PASS** |  |
| `negatives` | yes | **PASS** |  |
| `refusals` | yes | **PASS** |  |
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
| `s00-text-baseline` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 16 | `cee22a4b2a24` |
| `s01-add` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 25 | `2561a2d3d767` |
| `s02-noop-1` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 25 | `2561a2d3d767` |
| `s03-noop-2` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 25 | `2561a2d3d767` |
| `s04-edit-remap` | `AA B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 27 | `96d7fa51008c` |
| `s05-recipe` | `AA B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 27 | `95eb92533eb9` |
| `s06-style` | `AA B` | 13 | 1/4 | 0 | 0 | `ab1a678f8f5d` | caller-confirmed-current-semantic | 27 | `b68cf6ae63f0` |
| `s07-font-b` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 27 | `76307fcaa53f` |
| `bundle-a` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 27 | `76307fcaa53f` |
| `s08-remove-from-bundle-a` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 18 | `0eb267e8f195` |
| `s09-underline-add` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 27 | `a0191da45abb` |
| `s10-mixed-strikeout-add` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 33 | `d7b1349fb970` |
| `s11-mixed-noop` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 33 | `d7b1349fb970` |
| `s12-mixed-strikeout-remove` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 27 | `a0191da45abb` |
| `bundle-b` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 27 | `a0191da45abb` |
| `v3c-01-underline-add` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 25 | `99a48d82e44f` |
| `v3c-02-strikeout-add` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 31 | `352746aeec11` |
| `control-01-edit` | `AA B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 18 | `d9f33d3e91df` |
| `control-02-style` | `AA B` | 13 | 1/4 | 0 | 0 | `ab1a678f8f5d` | caller-confirmed-current-semantic | 18 | `0abf6ae1fd55` |
| `control-03-font-b` | `AA B` | 13 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 18 | `3aaafbd3203f` |

## Strikeout lifecycle

> Windows strikeout execution has not been performed by the PR that added the strikeout mode.

- **Validation kind:** `semantic-strikeout-lifecycle`; **strikeout runtime:** True; **Windows strikeout execution:** yes — this run executed on Windows
- **Semantic version transition:** [2, 4] (explicit strikeout `decorations.add`)

| Revision | Transition | Version | Decorations (kind range) | Rectangles (PDF x0 yu x1 yl) | Sides | Paint group | Text group | MuPDF |
|---|---|---|---|---|---|---|---|---|
| `confirmed` |  → | 2 | — | — | — | `none` | `6602eaaad898` | `88755aad520c` |
| `s00-text-baseline` | D 2→2 | 2 | — | — | — | `none` | `ea99442e3b91` | `88755aad520c` |
| `s01-add` | E 2→4 | 4 | strikeout [0,3) | 20.017578 63.6 39.359375 63 | above | `ae5329bfe172` | `ea99442e3b91` | `2f15ae5c4262` |
| `s02-noop-1` | D 4→4 | 4 | strikeout [0,3) | 20.017578 63.6 39.359375 63 | above | `ae5329bfe172` | `ea99442e3b91` | `2f15ae5c4262` |
| `s03-noop-2` | D 4→4 | 4 | strikeout [0,3) | 20.017578 63.6 39.359375 63 | above | `ae5329bfe172` | `ea99442e3b91` | `2f15ae5c4262` |
| `s04-edit-remap` | D 4→4 | 4 | strikeout [0,4) | 20.017578 63.6 47.363281 63 | above | `00fc7ccbee62` | `0a275874520e` | `f647f09853bf` |
| `s05-recipe` | E 4→4 | 4 | strikeout [0,4) | 20.017578 63 47.363281 62.4 | above | `ffb20cfdf7e7` | `0a275874520e` | `7c59a22fbf3c` |
| `s06-style` | E 4→4 | 4 | strikeout [0,4) | 20.019043 63.25 50.393555 62.6 | above | `81328d2a5f07` | `9fc58909b344` | `10ecfe1b8569` |
| `s07-font-b` | E 4→4 | 4 | strikeout [0,4) | 20 63.25 51.447266 62.6 | above | `fb60ed8e4675` | `398918b4a9b8` | `65dbbb4bfbfb` |
| `bundle-a` |  → | 4 | strikeout [0,4) | 20 63.25 51.447266 62.6 | above | `fb60ed8e4675` | `398918b4a9b8` | `65dbbb4bfbfb` |
| `s08-remove-from-bundle-a` | E 4→4 | 4 | none | — | — | `none` | `398918b4a9b8` | `640d42c376fc` |
| `s09-underline-add` | E 4→4 | 4 | underline [0,1) | 20 58.591667 29.638184 57.833333 | at_or_below | `62931f5221fa` | `398918b4a9b8` | `83130fa1bc18` |
| `s10-mixed-strikeout-add` | E 4→4 | 4 | underline [0,1), strikeout [3,4) | 20 58.591667 29.638184 57.833333; 42.776367 63.9 51.447266 63.25 | at_or_below, above | `019bace279fc` | `398918b4a9b8` | `185bee751703` |
| `s11-mixed-noop` | D 4→4 | 4 | underline [0,1), strikeout [3,4) | 20 58.591667 29.638184 57.833333; 42.776367 63.9 51.447266 63.25 | at_or_below, above | `019bace279fc` | `398918b4a9b8` | `185bee751703` |
| `s12-mixed-strikeout-remove` | E 4→4 | 4 | underline [0,1) | 20 58.591667 29.638184 57.833333 | at_or_below | `62931f5221fa` | `398918b4a9b8` | `83130fa1bc18` |
| `bundle-b` |  → | 4 | underline [0,1) | 20 58.591667 29.638184 57.833333 | at_or_below | `62931f5221fa` | `398918b4a9b8` | `83130fa1bc18` |
| `v3c-01-underline-add` | E 2→3 | 3 | underline [0,1) | 20.017578 58.7 28.021484 58 | at_or_below | `fe05d2df6d87` | `ea99442e3b91` | `08880d6ac407` |
| `v3c-02-strikeout-add` | E 3→4 | 4 | underline [0,1), strikeout [2,3) | 20.017578 58.7 28.021484 58; 31.355469 63.6 39.359375 63 | at_or_below, above | `9ed877caf867` | `ea99442e3b91` | `0d6aed431e04` |
| `control-01-edit` | D 2→2 | 2 | — | — | — | `none` | `0a275874520e` | `915fc976108d` |
| `control-02-style` | E 2→2 | 2 | — | — | — | `none` | `9fc58909b344` | `bc315cc391b2` |
| `control-03-font-b` | E 2→2 | 2 | — | — | — | `none` | `398918b4a9b8` | `640d42c376fc` |

| Check | Result | Meaning |
|---|---|---|
| no-op #1 / #2 | yes, yes, yes, yes, yes | PDF bytes, operators, owner block, semantic state, decoration evidence identical |
| edit remap | [[0, 3]], [[0, 4]] | before → after ranges (production rule) |
| recipe | yes, yes, yes, yes | x unchanged, y changed, kind kept, authority kept |
| style | yes, 0.0 | decorations kept; max em geometry error (pt) |
| font | yes, yes, yes, yes, yes, yes | provider B, generated font B, recipe/range kept, y unchanged, x follows B |
| publication A | yes, yes, restored, yes, yes, yes, yes, yes, yes | bytes, fresh process, version, decorations, owner, provider, canonical, raster |
| remove (from bundle A) | yes, yes, yes, yes | decorations [], version 4, no paint group, text group kept |
| mixed v4 | yes, yes, yes, yes, yes, yes | v4, one list, current:i, one group, order, underline below / strikeout above |
| mixed no-op | yes, yes, yes, yes, yes | as no-op #1/#2 (PDF bytes recorded) |
| mixed strikeout remove | yes, yes, yes | v4, underline only, body equals the underline-only state |
| publication B | yes, yes, restored, yes, yes | bytes, fresh process, bundle A immutable, underline only |
| v3 → v4 control | yes, yes, yes, yes, yes | v2→v3, v3→v4, underline exact, canonical, v3 not rewritten |
| MuPDF 144 dpi | text_only_differs_from_strikeout: yes, strikeout_equals_noop_1_and_2: yes, recipe_differs_from_previous: yes, style_differs_from_previous: yes, font_differs_from_previous: yes, removed_equals_text_only_control: yes, mixed_differs_from_underline_only: yes, mixed_differs_from_strikeout_only: yes, mixed_equals_mixed_noop: yes, strikeout_removal_returns_to_underline_only: yes, candidate_a_equals_bundle_a: yes, candidate_b_equals_bundle_b: yes | PASS |
| Poppler 144 dpi | text_only_differs_from_strikeout: yes, strikeout_equals_noop_1_and_2: yes, removed_equals_text_only_control: yes, mixed_differs_from_underline_only: yes, mixed_equals_mixed_noop: yes, strikeout_removal_returns_to_underline_only: yes, candidate_a_equals_bundle_a: yes, candidate_b_equals_bundle_b: yes | PASS |

Physical separation (§31.6, PDF y-up, serialized): strikeout upper 63.6 > baseline 60: yes

Expected refusals (the runtime must refuse each):

- A.pdf+B.json: `source output ownership: shared flow PDF revision changed`
- B.pdf+A.json: `source output ownership: shared flow PDF revision changed`
- mixed.pdf+underline-only.json: `source output ownership: shared flow PDF revision changed`
- underline-only.pdf+mixed.json: `source output ownership: shared flow PDF revision changed`
- resealed kind change (strikeout → underline): `underline recipe is outside its exact bounds`
- resealed recipe sign change: `strikeout recipe is outside its exact bounds`
- paint geometry tamper: `source output ownership: shared flow PDF revision changed`
- paint geometry tamper, owner witness rebound (production island check): `semantic version 4 body is not the canonical text and decoration body`
- strikeout separation (-1/1000000000 em) (planner): `strikeout is not physically above its baseline after the output decimal policy`
- strikeout with a positive offset (planner): `strikeout recipe is outside its exact bounds`
- underline with a negative offset (planner): `underline recipe is outside its exact bounds`
- overlap: strikeout on the underline range (planner): `decorations overlap or are not in canonical order`
- overlap: strikeout nesting the underline (planner): `decorations overlap or are not in canonical order`

- **Final state (last bundle):** semantic version 4, decorations [{'kind': 'underline', 'start': 0, 'end': 1, 'start_affinity': 'outside', 'end_affinity': 'outside', 'recipe': {'offset_em': '13/120', 'thickness_em': '7/120'}, 'id': 'current:0'}], paint group present

## Artifacts

- `result.json` — machine-readable result (schema in the README)
- `artifacts/` — `s0N-*/`, `s1N-*/`, `v3c-0N-*/`, `control-0N-*/` (semantic-candidate-*), `bundle-a/`, `bundle-b/`, `refusals/`, `tamper/`, `raster/`
- `logs/harness.log` — stage log with tracebacks of refused/failed stages
