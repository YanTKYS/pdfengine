"""Every paragraph of a page as an edit target: would one more line reach the lines below it, and would the chain hold (issue #103)?

  PYTHONPATH=. .venv/bin/python prototypes/paragraph_frame/chain_survey.py <pdf> <page> <out.json>

For each paragraph group (auto_detect.observe) with a unique selection, the
detection of B2 and chain.plan for a translation of one inferred line height:
no chain (the growth reaches nothing below, or nothing can follow), the chain
with its elements, or why it does not hold. Evaluation only; no PDF is written.
"""
import json, sys
from pathlib import Path


def main():
    from pdfeditor.content_stream import ContentPage
    from pdfeditor.marked_content import observe_marked_content
    from pdfeditor.page_proposal import _Analysis
    from prototypes.paragraph_frame.auto_detect import detect, group_text, observe
    from prototypes.paragraph_frame import chain
    from prototypes.paragraph_frame.auto import _glyph_area
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
        if len(stripped) < 6:
            continue
        start = text.index(stripped[0])
        selection = next((text[start:start + n] for n in range(6, len(text) - start + 1) if whole.count(text[start:start + n]) == 1), None)
        if selection is None:
            continue
        row = dict(text=text[:40], selection=selection)
        try:
            c2, paragraphs, spec, at, evidence = detect(source, page, selection)
            try:
                if spec.get('mode') == 'flow':
                    row['outcome'] = 'flow'
                else:
                    delta = spec['line_height']
                    found = chain.plan(c2, paragraphs, spec, evidence, delta, followers=[_glyph_area(c2, p) for p in paragraphs[1:]])
                    row.update(outcome='no chain' if found is None else 'chain', line_height=round(delta, 2), followers=len(paragraphs) - 1)
                    if found:
                        row.update(elements=[u['row']['text'][:30] for u in found['units']], unavailable=found['unavailable'], needed=found['needed'])
            finally:
                c2.close()
        except Exception as exc:
            row.update(outcome='refused', reason=f'{type(exc).__name__}: {exc}'[:160])
        result.append(row)
        Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
