# Semantic lifecycle external validation report

> Windows execution has not been performed by the PR that added this harness. This report is evidence only for the execution it describes.

- **Verdict:** `PASS`
- **Windows execution:** yes — this run executed on Windows
- **Executed:** 2026-10-05T09:56:25.589792+00:00 → 2026-10-05T09:56:33.302271+00:00
- **Platform:** Windows-11-10.0.26200-SP0; Python 3.12.14; PyMuPDF 1.27.2.3; fontTools 4.64.0; uharfbuzz 0.55.0; pypdf 6.10.0
- **Poppler:** pdftoppm version 26.07.0; **LibreOffice:** unavailable
- **Repository:** `452ba772526424b5f7365c1efea88b582337cf20` (dirty: False); runtime digest `dd4fff7b3e70fb65804fc7a82fdd1f2253bca8b52d47079ec265df4395aeb84e`

## Inputs

| Input | File | SHA-256 |
|---|---|---|
| PDF | `document.pdf` (1 pages; producer 'pypdf'; LibreOffice claimed: False) | `037893f6ddc5aab9977f0f545e26b4ce4d8392453ec7c819447ada9cb66f9971` |
| sidecar | `shared-flow.json` | `e2fba2596b6ce2073fe7471dc833b2aeee86f3c8f5084e92e88dba8742918799` |
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
| `edit` | yes | **PASS** |  |
| `style` | yes | **PASS** |  |
| `font` | yes | **PASS** |  |
| `noop` | yes | **PASS** |  |
| `publish_a` | yes | **PASS** |  |
| `edit_from_bundle_a` | yes | **PASS** |  |
| `font_back` | yes | **PASS** |  |
| `publish_b` | yes | **PASS** |  |
| `negatives` | yes | **PASS** |  |
| `continuity` | yes | **PASS** |  |
| `raster_mupdf` | yes | **PASS** |  |
| `raster_poppler` | no | **PASS** |  |
| `inputs_preserved` | yes | **PASS** |  |

## Revisions

| Revision | Text | Size | Tracking | Tw | Edges | Font | Provenance | Body ops | PDF SHA |
|---|---|---|---|---|---|---|---|---|---|
| `confirmed` | `A B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 28 | `037893f6ddc5` |
| `candidate-01-edit` | `AA B` | 12 | 0 | 0 | 0 | `ab1a678f8f5d` | source-confirmed | 18 | `0183a34219ae` |
| `candidate-02-style` | `AA B` | 12 | 1/4 | 0 | 0 | `ab1a678f8f5d` | caller-confirmed-current-semantic | 18 | `a95412468910` |
| `candidate-03-font-b` | `AA B` | 12 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 18 | `1f129b16654a` |
| `candidate-04-noop` | `AA B` | 12 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 18 | `783f4fe1962a` |
| `bundle-a` | `AA B` | 12 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 18 | `783f4fe1962a` |
| `candidate-05-edit-from-bundle-a` | `BAA B` | 12 | 1/4 | 0 | 0 | `bf5783fb6fde` | caller-confirmed-current-semantic | 20 | `1d18dfcef473` |
| `candidate-06-font-a` | `BAA B` | 12 | 1/4 | 0 | 0 | `ab1a678f8f5d` | caller-confirmed-current-semantic | 20 | `c54f9764e442` |
| `bundle-b` | `BAA B` | 12 | 1/4 | 0 | 0 | `ab1a678f8f5d` | caller-confirmed-current-semantic | 20 | `c54f9764e442` |

## Artifacts

- `result.json` — machine-readable result (schema in the README)
- `artifacts/` — candidates (`candidate-*/semantic-candidate-*/`), `bundle-a/`, `bundle-b/`, `raster/`
- `logs/harness.log` — stage log with tracebacks of refused/failed stages
