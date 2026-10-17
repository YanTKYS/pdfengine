# Real Japanese three-page sequential reflow

Validation is in progress. This report does not yet claim a complete three-page PASS.

Starting main: `f93c8f47bc8d2c277b01464178d75f3e1cc23e89` (PR #75 merged).

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
