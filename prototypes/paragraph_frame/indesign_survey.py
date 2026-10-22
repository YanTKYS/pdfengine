"""Per-paragraph proposal survey; runs against whichever code base is on PYTHONPATH.

  PYTHONPATH=<checkout> .venv/bin/python prototypes/paragraph_frame/indesign_survey.py <pdf> <page> <out.json>

Every paragraph group of the page (auto_detect.observe) is proposed with an
unchanged-text edit: the selection is the shortest prefix of the paragraph
text, at least 6 characters, that occurs once on the page; the replacement
equals the selection. The outcome is "proposed" or the refusal message.
"""
import json
import sys
import time


def main():
    from pdfeditor.content_stream import ContentPage
    from pdfeditor.marked_content import observe_marked_content
    from pdfeditor.page_proposal import _Analysis
    from prototypes.paragraph_frame.auto import propose
    from prototypes.paragraph_frame.auto_detect import group_text, observe
    source, page, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    c = ContentPage(source, page)
    try:
        rows, parents, groups = observe(c, observe_marked_content(source, page), _Analysis(source, page))
        texts = [group_text(c, g)[0] for g in groups]
        boxes = [[min(r['bbox'][0] for r in g), min(r['bbox'][1] for r in g), max(r['bbox'][2] for r in g), max(r['bbox'][3] for r in g)] for g in groups]
    finally:
        c.close()
    whole = '\n'.join(texts)
    result = []
    for text, box in zip(texts, boxes):
        stripped = text.strip()
        if len(stripped) < 6:
            result.append(dict(text=text, bbox=box, outcome='skipped', reason='shorter than 6 characters'))
            continue
        start = text.index(stripped[0])
        selection = None
        for n in range(6, len(text) - start + 1):
            candidate = text[start:start + n]
            if whole.count(candidate) == 1:
                selection = candidate
                break
        if selection is None:
            result.append(dict(text=text, bbox=box, outcome='skipped', reason='no unique selection'))
            continue
        began = time.perf_counter()
        try:
            proposal = propose(source, page, selection, selection)
            result.append(dict(text=text, bbox=box, selection=selection, outcome='proposed', lines=len(proposal['lines']),
                               seconds=round(time.perf_counter() - began, 2)))
        except Exception as exc:  # a refusal or an interpretation failure, both are outcomes
            result.append(dict(text=text, bbox=box, selection=selection, outcome='refused', reason=f'{type(exc).__name__}: {exc}'[:300],
                               seconds=round(time.perf_counter() - began, 2)))
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
