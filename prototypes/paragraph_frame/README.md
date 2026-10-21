# Paragraph / frame reconstruction comparison

This bounded experiment compares unchanged `pdfeditor` public APIs (A) with
an independent paragraph/region writer (B). It is not a production editor.
The [results report](../../docs/architecture-poc-results.md) distinguishes
saved edits, safety checks, search quality and lifecycle results.

Inputs, exact occurrences, two paragraph boundaries, permitted frame and font
substitution were frozen in [manifest.json](../../evaluations/architecture_poc/manifest.json)
in commit `35b06b6`, before either arm ran. Do not change those conditions to
make an edit pass. `preflight.py` observes originals; it does not run an edit.

B uses visual first-line indentation to recover two horizontal paragraphs.
The caller supplies the page, frame, left/right edges, first-line indentation,
first baseline, leading and paragraph gap. The PDF-alone path uses **only these
geometric settings and the explicitly chosen font**, not original glyph IDs,
owner IDs, source PDF or sidecar text. This is bounded manual frame recovery,
not automatic discovery of arbitrary document flow. The optional sidecar adds
revision/text checking; it is not needed to reconstruct the saved PDF.

## Reproduce

From the repository root, with Python 3.12 and Poppler `pdftotext`/`pdftoppm`:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
```

Obtain only the two original PDFs from the URLs in `evaluations/sources.json`;
verify SHA-256 before using them. For example:

```sh
.venv/bin/python - <<'PY'
import hashlib, json, pathlib, urllib.request
for s in json.load(open('evaluations/sources.json')):
    if s['id'] not in ('word_osaka_guideline', 'lo_migration_ja'):
        continue
    p = pathlib.Path(s['path'])
    data = p.read_bytes() if p.exists() else urllib.request.urlopen(s['source_url']).read()
    assert hashlib.sha256(data).hexdigest() == s['sha256'], s['id']
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_bytes(data)
PY
.venv/bin/python -m prototypes.paragraph_frame.preflight --output tmp/observed-poc-manifest.json
cmp evaluations/architecture_poc/manifest.json tmp/observed-poc-manifest.json
.venv/bin/python -m pytest -q tests/test_paragraph_frame_poc.py
.venv/bin/python -m prototypes.paragraph_frame.evaluate --root tmp/poc-new-run
```

Use a **new artifact directory** for initial edits. B refuses to replace an
existing PDF; initial and lifecycle stages also reject existing outputs.
`evaluate` verifies source/font SHA, materializes PyMuPDF's bundled Droid Sans
Fallback, runs the four independent requests and audits every page at 144 dpi
with both renderers. Every re-edit and no-op save uses a separate process.
Neither source PDFs, the font, generated PDFs nor raster images belong in git.

The experiment can be resumed by stage without repeating saves:

```sh
# Already saved initial PDFs, lifecycle has not yet run:
.venv/bin/python -m prototypes.paragraph_frame.evaluate --root tmp/poc-new-run --stage audit
.venv/bin/python -m prototypes.paragraph_frame.evaluate --root tmp/poc-new-run --stage lifecycle
# Read-only refresh if an audit implementation changed:
.venv/bin/python -m prototypes.paragraph_frame.evaluate --root tmp/poc-new-run --stage refresh
# Verify current guards/layout against initial saved B evidence without saving:
.venv/bin/python -m prototypes.paragraph_frame.qualify --root tmp/poc-new-run
```

`baseline.py` uses `propose_edit_intent` and the public page-flow
proposal/acceptance/replacement/save APIs. It supplies the same frame and
explicit `substituted` font choice to every logical style. The read-only
`_Analysis` adapter obtains observed line IDs; it creates no owners or slots.
`engine.py` reuses byte provenance and verified TrueType subset building,
not the owner-based edit contracts. UAX14 line breaks come from `uniseg`;
HarfBuzz/fontTools provide glyphs/metrics/subsets; one pypdf writer performs
source removal and local retagging. No redaction or white rectangle is used.

`audit.py` does not import the writer/paragraph model. It checks exact drawn
text and full-page MuPDF/Poppler extraction, original resources and foreign
glyphs/paint/links/annotations, structure backlinks/ParentTree/ActualText,
line bands/kinsoku, native search, and fixed-mask all-page raster differences.
Only extraction line edges are stripped. Original extractor-inferred spaces
(e.g. LibreOffice's `16% `) are separately accepted as an inherited variant;
meaningful internal spaces are not globally erased.

B writes a staged candidate, reads it back, then uses one same-filesystem
`os.replace` for the PDF. Semantic and content changes use the same writer/save.
The optional JSON sidecar is written afterwards: **PDF + sidecar are not a
crash-atomic bundle**. There is no fsync durability guarantee. Failed planning
or staged readback does not publish a new PDF. Audit-failed candidates remain
local evaluation artifacts; `saved` never means `audit passed`.

Known scope limits: two opaque black, horizontal LTR paragraphs in one
unrotated frame; ordinary page text; simple Span/P structure; editable
TrueType fonts; no page crossing, Form text, mixed semantic references,
IDTree, complex marked-content semantics or automatic font matching.
No GUI or production module is changed. Commercial SDK arm C is untested.
