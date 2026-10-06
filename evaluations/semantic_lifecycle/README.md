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

## Underline mode (`run --mode underline`, docs §29)

> **Windows underline execution has not been performed by the PR that added this mode (2026-10-06).** Cloud runs
> (Linux, synthetic fixtures) test the harness only. A run is Windows evidence only when its `result.json` has
> `"windows_execution": true`; an underline run also has `"mode": "underline"` and `"underline_runtime": true`.

`run` without `--mode` (or with `--mode text`) is the text-only lifecycle above, unchanged. `--mode underline` runs the
narrow semantic underline lifecycle (docs §28) on the same `prepare` output. It calls the same runtime APIs only and
never changes semantic version 3, the decoration schema, the paint grammar, the Transaction or ownership.

### Windows command (copy as is; PowerShell)

```powershell
# Clean checkout of the merge commit, Python 3.12, lockfile (as in the repository README).
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install -e ".[test]"

$P = ".\.venv\Scripts\python.exe"
$H = "-m", "evaluations.semantic_lifecycle.windows_validation"
$W = "tmp\semantic-underline-windows"
$RUN = "evaluations\semantic_lifecycle\runs\windows-underline-synthetic-$(Get-Date -Format yyyyMMdd)"
& $P @H prepare-fonts --output-dir "$W\fonts" --from-a C:\Windows\Fonts\arial.ttf --from-b C:\Windows\Fonts\times.ttf
& $P @H prepare-synthetic --output-dir "$W\src"
& $P @H prepare --source-pdf "$W\src\source.pdf" --spec "$W\src\spec.json" --font-a "$W\fonts\font-a.ttf" --output-dir "$W\prep"
# Optional: Poppler for the second renderer, for this session only (raster_poppler is SKIPPED without it).
# $env:PATH = "C:\path\to\poppler\Library\bin;$env:PATH"
& $P @H run --mode underline --pdf "$W\prep\document.pdf" --sidecar "$W\prep\shared-flow.json" --font-a "$W\fonts\font-a.ttf" --font-b "$W\fonts\font-b.ttf" --output-dir $RUN
$LASTEXITCODE
```

Expected for this synthetic control on Windows: `PASS`, exit 0 (not claimed until it has run). Commit only
`$RUN\result.json` and `$RUN\report.md`; never rerun into an existing directory and never edit `result.json`.

### Default underline plan

```json
{"underline_add": {"start": 0, "end": 3, "recipe": {"offset_em": "13/120", "thickness_em": "7/120"}},
 "underline_edit": {"start": 1, "end": 1, "text": "A"},
 "underline_recipe": {"offset_em": "1/10", "thickness_em": "1/20"},
 "underline_style": {"font_size": "13", "tracking": "1/4"}}
```

The add recipe is the §27/§28 validation recipe. `--plan` overrides any of these keys; a request the runtime refuses
(for example a recipe that breaks the line box) is recorded as `REFUSED`.

### Underline stages

`preflight`, `baseline`, `confirm` (as in text mode), then:

| Stage | Revision | Expectation (else FAIL) |
|---|---|---|
| `text_baseline` | `u00-text-baseline` | canonical L2 save, semantic version 2, no decorations, no underline group |
| `add` | `u01-add` | explicit `decorations.add`: E, version 2 → 3, current authority unchanged, decoration `current:0` exactly as requested, underline group inserted, text group unchanged |
| `noop_1`, `noop_2` | `u02-noop-1`, `u03-noop-2` | D, version 3; owned body, operators, rectangles, decorations, semantic payload/authority/derived, owner block SHA and MuPDF raster identical (PDF byte identity recorded) |
| `edit_remap` | `u04-edit-remap` | D; decorations equal the §27.7 remap of the edit (default `[0,3)` → `[0,4)`) |
| `recipe` | `u05-recipe` | E; text and x endpoints unchanged, y geometry changed, authority unchanged |
| `style` | `u06-style` | E; decorations unchanged, only `style.*` changed, exact em geometry from the written bytes |
| `font` | `u07-font-b` | E with `--font-b`; provider = font B, generated font = font B, recipe and y geometry unchanged, left edges on the new glyph origins (SKIPPED without `--font-b` → `INCOMPLETE`) |
| `publish_a` | `bundle-a` | L3 publication of the last candidate; byte identity, fresh-process reopen, version 3 and decorations restored, raster identical |
| `remove_from_bundle_a` | `u08-remove-from-bundle-a` | input is `artifacts/bundle-a/*`; explicit `decorations.remove`; version stays 3, `decorations = []`, no underline group, text group unchanged |
| `publish_b` | `bundle-b` | as `publish_a`; bundle A unchanged; version 3 text-only body |
| `negatives` | — | A.pdf+B.json and B.pdf+A.json refused |
| `tamper` | — | on copies under `artifacts/tamper/`: a resealed decoration-recipe tamper and a paint-geometry tamper are refused |
| `continuity` | — | creation evidence and owner identity constant |
| `text_only_control` | `control-0N-*` | the same edit/style/font requests on version 2, never decorated |
| `raster_mupdf` | — | 144 dpi: baseline ≠ add; add = no-op 1 = no-op 2; recipe, style and font each ≠ previous; removed = text-only control; candidate A = bundle A; candidate B = bundle B |
| `raster_poppler` | — | optional (SKIPPED without `pdftoppm`): baseline ≠ add, add = no-ops, candidates = bundles, removed = control |
| `inputs_preserved` | — | always |

The "differs" expectations hold for the default plan, whose recipe, size and font changes each move painted geometry
by at least 0.1 pt. A custom `--plan` that does not move pixels makes that check FAIL; it never passes silently.

### `result.json` additions (still `schema_version` 1; additive)

| Key | Content |
|---|---|
| `mode`, `validation_kind`, `underline_runtime` | `text`/`semantic-text-lifecycle`/`false` or `underline`/`semantic-underline-lifecycle`/`true` (also present in text mode) |
| `underline_disclaimer` | underline mode only |
| `revisions{name}.underline` | `semantic_version`, `decorations` (`null` for version 1/2), `decoration_count`, `ranges`, `recipes`, `rectangle_count`, `rectangles_pdf` (the written `x0 yu x1 yl` operands), `paint_fill`, `text_tm_y`, `text_tm_x`, `body_sha256`, `body_split`, `text_body_sha256`, `paint_body_sha256` (`null` when there is no underline group), `paint_body_size` (0 then), `raster_mupdf_sha256` |
| `revisions{name}.transition` | built revisions only: `parent`, `classification`, `version`, `next_version`, `authority_preserved` |
| `stages[].details` | per stage: the checks in the table above, `paint_transition` (`insert`/`replace`/`remove`/`none`, observed from the parent and candidate underline groups), ranges/rectangles before and after, expected refusal reasons, raster digests |

The text and underline groups are split with the runtime's own v3 body grammar; rectangles are read from the written
bytes, not from internal plans.
