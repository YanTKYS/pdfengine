"""Generate a five-page source PDF and demonstrate the required A-E edits.

The source is drawn with explicit baselines, without using the editor's
inference, metrics wrapper or line breaker. Run: python -m examples.demo
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf

from pdfeditor.engine import edit_pdf


CASES = [
    {"id": "A", "label": "2文字から4文字へ", "lines": ["申請"],
     "after": "申請書類"},
    {"id": "B", "label": "1行から複数行へ", "lines": ["申請期限は9月10日です。"],
     "after": "申請書類の提出期限は10月15日までです。必要事項を記入し、添付書類と一緒に窓口へ提出してください。"},
    {"id": "C", "label": "複数行を短く詰め直す", "lines": [
        "申請書類の提出期限は10月15日までです。", "必要事項を記入し、添付書類と一緒に", "窓口へ提出してください。"],
     "after": "提出期限は10月15日です。"},
    {"id": "D", "label": "日本語と比例幅の英数字", "lines": ["型番Wi-2026の受付は9月10日です。"],
     "after": "型番Wi-2026とWWW-15の受付は10月15日までです。ID: iii111を確認してください。"},
    {"id": "E", "label": "埋め込みフォントの維持", "lines": ["申請期限は9月10日です。"],
     "after": "新規登録書類の提出期限は10月15日です。"},
]


def _text(page, font, x, y, text, size=12, color=(.12, .17, .23)):
    writer = pymupdf.TextWriter(page.rect)
    writer.append((x, y), text, font=font, fontsize=size)
    writer.write_text(page, color=color)


def create_source(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    font = pymupdf.Font("cjk")
    for index, case in enumerate(CASES, 1):
        page = document.new_page(width=595, height=500)
        page.draw_rect((0, 0, 595, 8), color=None, fill=(.10, .40, .48))
        _text(page, font, 56, 47, "PDF TEXT REFLOW / ENGINE PROTOTYPE", 10, (.25, .40, .46))
        _text(page, font, 56, 82, f"CASE {case['id']}  {case['label']}", 20)
        _text(page, font, 56, 109, "編集対象領域", 10, (.32, .40, .46))
        page.draw_rect((56, 126, 348, 330), color=None, fill=(.95, .97, .98))
        page.draw_rect((56, 126, 348, 330), color=(.59, .72, .76), width=.7)
        for row, line in enumerate(case["lines"]):
            _text(page, font, 72, 158 + row * 19, line, 12)
        page.draw_rect((386, 145, 518, 228), color=(.30, .56, .61), fill=(.89, .94, .93))
        page.draw_line((398, 213), (441, 172), color=(.19, .45, .48), width=2)
        page.draw_line((441, 172), (505, 198), color=(.19, .45, .48), width=2)
        _text(page, font, 386, 254, "固定された図形", 11)
        _text(page, font, 56, 371, "この文章と右側の図形は移動しません。", 11)
        _text(page, font, 56, 410, "本文は文字として保存され、選択・検索できます。", 10, (.32, .40, .46))
        _text(page, font, 516, 470, f"{index} / 5", 10, (.32, .40, .46))
    document.set_metadata({"title": "Independent PDF text reflow test source", "author": "PDF region editor PoC"})
    document.save(path, deflate=True)
    document.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("output/pdf"))
    args = parser.parse_args()
    directory = args.output_dir
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "original.pdf"
    if source.exists():
        raise SystemExit("Choose a new --output-dir; existing samples are preserved.")
    create_source(source)
    reports = []
    for page, case in enumerate(CASES, 1):
        report = edit_pdf(source, directory / f"case-{case['id']}.pdf", page=page,
                          find="".join(case["lines"]), replacement=case["after"])
        (directory / f"case-{case['id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        reports.append(report)
    (directory / "evaluation.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{ "case": case["id"], "lines": [report["old_line_count"], report["new_line_count"]],
                       "font_preserved": report["font"]["preserved"]} for case, report in zip(CASES, reports)], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
