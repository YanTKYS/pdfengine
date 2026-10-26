"""Paragraphs whose followers hold an inline placed object (issue #97).

  PYTHONPATH=. .venv/bin/python prototypes/paragraph_frame/follower_survey.py <pdf> <page> <out.json>

Every paragraph group of the page (auto_detect.observe) is detected as the
target of an unchanged-text edit (the selection of indesign_survey). For each
target the survey records its own fixed inline objects and, per following
paragraph that a growing edit would move, its text and fixed objects (the
glyph IDs, the marked-content tag and whether the sequence is in a Form
XObject). A refusal of the detection is recorded as such.
"""
import json
import sys


def main():
    from pdfeditor.content_stream import ContentPage
    from pdfeditor.marked_content import observe_marked_content
    from pdfeditor.page_proposal import _Analysis
    from prototypes.paragraph_frame.auto_detect import detect, group_text, observe
    source, page, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    c = ContentPage(source, page)
    try:
        rows, parents, groups = observe(c, observe_marked_content(source, page), _Analysis(source, page))
        texts = [group_text(c, g)[0] for g in groups]
    finally:
        c.close()
    whole = '\n'.join(texts)
    result = []
    for text in texts:
        stripped = text.strip()
        selection = None
        if len(stripped) >= 6:
            start = text.index(stripped[0])
            selection = next((text[start:start + n] for n in range(6, len(text) - start + 1) if whole.count(text[start:start + n]) == 1), None)
        if selection is None:
            continue
        row = dict(text=text[:60], selection=selection)
        try:
            c2, paragraphs, spec, at, evidence = detect(source, page, selection)
            try:
                describe = lambda p: dict(text=p.text[:40], fixed=len(p.fixed), scopes=[dict(tag=s.get('tag'), invocation=bool(s['invocation']), glyphs=len(
                    [i for i in p.fixed])) for s in p.scopes if s['kind'] == 'fixed'])
                row.update(outcome='detected', mode=spec.get('mode'), target=describe(paragraphs[0]), followers=[describe(p) for p in paragraphs[1:]])
            finally:
                c2.close()
        except Exception as exc:
            row.update(outcome='refused', reason=f'{type(exc).__name__}: {exc}'[:200])
        result.append(row)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
