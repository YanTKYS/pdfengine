# Semantic lifecycle external validation (Windows / LibreOffice)

> **Windows execution has not been performed by the PR that added this harness (2026-10-05).**
> Cloud runs of the harness (Linux, synthetic fixtures) test the harness itself. They are not Windows or LibreOffice
> evidence. A run is Windows evidence only when its `result.json` has `"windows_execution": true`.

[`windows_validation.py`](windows_validation.py) runs the narrow semantic lifecycle (docs §20–§23) on an external
target.
- **Runtime APIs only:** it calls the existing runtime APIs and records their behaviour. It does not change semantic
  authority, ownership or publication.
- **Inputs are read-only:** its inputs are never modified, and their SHA-256 is checked before and after the run.
- **Failures are never turned into success:** every runtime exception is recorded as REFUSED or FAIL.

Background and contracts: [docs §24](../../docs/anchored-paint-ownership.md#24-windows-external-semantic-lifecycle-validation-preparation--2026-10-05).

## Commands

```powershell
$H = ".\.venv\Scripts\python.exe -m evaluations.semantic_lifecycle.windows_validation"
# Fonts: unhinted A/B/space subsets of installed fonts (local derivatives, never committed),
# or deterministic synthetic fonts when --from-a/--from-b are omitted.
iex "$H prepare-fonts --output-dir tmp\semantic-windows\fonts --from-a C:\Windows\Fonts\arial.ttf --from-b C:\Windows\Fonts\times.ttf"
# Source + explicit preparation spec. Either the in-scope synthetic control, or your own LibreOffice PDF + spec.json.
iex "$H prepare-synthetic --output-dir tmp\semantic-windows\src"
# Shared-flow v2 owner sidecar for exactly one source slot (existing APIs; the spec is caller-confirmed, never inferred).
iex "$H prepare --source-pdf tmp\semantic-windows\src\source.pdf --spec tmp\semantic-windows\src\spec.json --font-a tmp\semantic-windows\fonts\font-a.ttf --output-dir tmp\semantic-windows\prep"
# The lifecycle. The output directory must not exist; results are never overwritten.
iex "$H run --pdf tmp\semantic-windows\prep\document.pdf --sidecar tmp\semantic-windows\prep\shared-flow.json --font-a tmp\semantic-windows\fonts\font-a.ttf --font-b tmp\semantic-windows\fonts\font-b.ttf --output-dir evaluations\semantic_lifecycle\runs\<run-name>"
```

`run` also accepts:
- `--slot-id`, required when the sidecar has more than one slot (ambiguity is refused, never guessed);
- `--plan plan.json`, which overrides the requested transitions.

The default plan is `{"edit": {"start": 0, "end": 0, "text": "A"}, "style": {"tracking": "1/4"}, "next_edit": {"start": 0, "end": 0, "text": "B"}}`. A value outside the confirmed region is refused by the runtime, and that refusal is recorded.

## Preparation spec (`prepare --spec`)

All values are caller-confirmed PDF points (y down), read from `python -m pdfeditor observe PDF --page N --json catalog.json`:

```json
{"page": 1, "glyph_ids": [0, 1], "explicit_width": 150,
 "region": {"bounds": [18, 40, 200, 250], "x": 20, "width": 150, "first_baseline": 200},
 "layout": {"max_bottom": 220, "min_line_height": 22, "first_line_indent": 0},
 "empty": {"ascent": 10, "descent": 3}, "initial_text": "A B"}
```

`glyph_ids` select the one source paragraph, which becomes the single owned slot. `initial_text` is written by the
first owner-creating save; it must stay within the A/B/space/newline scope.

## Stages, statuses, verdict, exit codes

Stages run in this order: `preflight`, `baseline`, `confirm`, `edit`, `style`, `font`, `noop`, `publish_a`,
`edit_from_bundle_a` (the published bundle A is the input), `font_back`, `publish_b`, `negatives` (mixed A/B pairs
must be refused), `continuity` (creation evidence, owner identity), `raster_mupdf`, `raster_poppler` (optional),
`inputs_preserved`.

| Status | Meaning |
|---|---|
| `PASS` | expected success (for `negatives`: the runtime refused as it must) |
| `REFUSED` | the runtime refused for a scope or safety reason (a `PdfError`); not a failure |
| `SKIPPED` | not applicable to the input or environment, or a prerequisite stage did not pass |
| `FAIL` | the behaviour differs from the expectation, or a non-`PdfError` exception occurred |

| Verdict | Exit | When |
|---|---|---|
| `PASS` | 0 | every required stage passed (`raster_poppler` may be SKIPPED) |
| `FAIL` | 1 | any stage failed |
| — | 2 | usage error, missing input file, or the output directory already exists (nothing is written) |
| `UNSUPPORTED_TARGET` | 3 | the runtime refused at `preflight`, `baseline` or `confirm`, before any mutation |
| `REFUSED` | 3 | the runtime refused a later lifecycle stage |
| `INCOMPLETE` | 4 | a required stage was skipped without a refusal or failure (e.g. no `--font-b`) |

## Output layout

```
<output-dir>/
  result.json            machine-readable result (commit this)
  report.md              human summary (commit this)
  artifacts/             confirmed/, candidate-0N-*/semantic-candidate-*/, bundle-a/, bundle-b/, raster/*.png (local only)
  logs/harness.log       stage log with tracebacks (local only)
```

Under `evaluations/semantic_lifecycle/runs/<run-name>/`, `.gitignore` admits only `result.json` and `report.md`.
PDFs, PNGs, fonts and logs stay local; their SHA-256 values are in `result.json`.

## `result.json` (schema_version 1)

The format is additive: readers must ignore unknown keys. It is validation evidence only and is never mixed into a
runtime sidecar.

| Key | Content |
|---|---|
| `schema_version`, `executed_at`, `finished_at`, `disclaimer` | identity of the run |
| `windows_execution` | `true` only when `platform.system() == "Windows"` |
| `environment` | `os` (system/release/version/platform/machine), `python`, `pymupdf`, `fonttools`, `uharfbuzz`, `pypdf`, `poppler` {available, version}, `libreoffice` ({version, source} or `"unavailable"`), `repository` {commit, dirty, runtime_digest} |
| `inputs` | `pdf` {filename, sha256, page_count, producer, creator, libreoffice_claimed}; `sidecar` {filename, sha256}; `fonts` {font_a, font_b: {filename, sha256}}. File names only, no absolute paths. A Producer string is informational, never authority. |
| `plan` | requested edit/style/next_edit |
| `target` | slot_id, page, paragraph_id, region_id, sidecar_schema, semantic_version, owner_state, source_font_name, generated_fonts, text_length |
| `stages[]` | {id, required, status, error, details}; `details` holds stage evidence (requests, diffs, byte identity, fresh-process status, expected refusals, raster hashes, creation-evidence digests) |
| `revisions{name}` | pdf/sidecar paths relative to the output dir, their SHA-256 and sizes; `owner` {marker_id, created_from_sha256, program_sha256, block_sha256, range, pdf_sha256}; `semantic` {text, font_sha256, provider_sha256, provider_name, font_size, tracking, rise, horizontal_scale, word_spacing, edges, provenance, version, canonical}; `island` {sha256, size, operators} |
| `verdict` | as above |
