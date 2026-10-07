# Critical-path scenario probe

This is a read-only probe for [docs/acrobat-critical-path.md](../../docs/acrobat-critical-path.md). It calls production
APIs only and never changes `pdfeditor/`.

```bash
python -I evaluations/critical_path/scenario_probe.py OUT_DIR --font /path/to/japanese-truetype.ttf
```

The recorded [probe-results.json](probe-results.json) comes from the B1 branch (based on main `30e5fe7`) on Linux. The setup was:

- Python 3.12.3;
- the lockfile versions;
- IPAGothic (`ipag.ttf`, TrueType) as both the embedded source font and the supplied provider.

## How to read the results

- `PASS-with-declarations` means the path worked only with every hand-written caller declaration listed in
  `caller_declarations`. It is the **BOUNDED** status in the critical-path document, not PASS.
- `PASS-intent-only` means the B1 front door (`propose_page_flow` → `accept_page_flow` → `replace_in_flow` →
  `edit_shared_flow`, then reopen) edited the page from the PDF, the page, the find/replacement text and a font
  candidate set only. This is the **PASS** status.
- `REFUSED` lines show the guard that stopped the edit. In every refusal, the original was left unchanged.
