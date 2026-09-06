"""Run with a Python runtime which already provides pypdf; no PDF mutation."""
import json
from pathlib import Path
import sys

import pypdf

result = {"library": f"pypdf {pypdf.__version__}", "pages": []}
try:
    reader = pypdf.PdfReader(sys.argv[1])
    if reader.is_encrypted:
        reader.decrypt("")
    result["pages"] = [page.extract_text() for page in reader.pages]
except Exception as exc:
    result["error"] = f"{type(exc).__name__}: {exc}"
Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
