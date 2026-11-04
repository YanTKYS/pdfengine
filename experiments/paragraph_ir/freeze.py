"""Fix the cases of the ParagraphIR PoC before it is written (issue #124).

  .venv/bin/python -m experiments.paragraph_ir.freeze

Takes 6 of the 16 fixed cases of issue #122 (experiments/route_poc/cases.json:
input digest, page, click, selection, typed text, the current approach's
committed result) and records why each is in the set, what was found about its
cause before the PoC was written, the edit sequence and the evaluation
conditions. Writes experiments/paragraph_ir/cases.json. Nothing is added or
removed afterwards.
"""
import json
from pathlib import Path
import subprocess

ROUTE = Path('experiments/route_poc/cases.json')
OUT = Path('experiments/paragraph_ir/cases.json')

# (id, role, why, what was found before the PoC (code/real-PDF checks on 2026-11-04)).
CHOSEN = [
    ('S-T1', 'refused by the current approach',
     'pdfTeX English, justified paragraph, a word replaced (shorter)',
     'B2 refuses: "implicit line-join space has no glyph in the source font" (the Type1 subset has no space glyph; TeX '
     'positions words by displacement). Every new character is encodable with the original font resource. Untagged.'),
    ('R07', 'refused by the current approach',
     'InDesign, three centered lines in a rounded panel beside a QR code, longer',
     'B2 infers a one-line frame [572.66, 720.46, 657.66, 730.55] and treats the next centered lines as followers that '
     'cannot move ("a line decoration would leave or enter a background"). Every new character is encodable with the '
     'original font resource (HiraginoUDSansStd-W6, Type0). Optional-content marked content (BDC) on the page.'),
    ('R10', 'refused by the current approach',
     'InDesign map legend: an item (white badge text + name) in a narrow column, longer; the lines below share text operators',
     'B2 refuses: "staged glyph collides with protected filled-vector"; the line below cannot move: "a text-showing '
     'operator draws glyphs of the line and other text" (ラトビアリトアニア). The clicked glyphs are in the badge font '
     '(UDShinGoPro-DeBold, white), which cannot encode the name\'s characters; the name is in another resource.'),
    ('S-L1', 'saved but not practical with the current approach',
     'LibreOffice Japanese, same length (a date)',
     'B2 saves; not practical: search and copy of the edited text fail (the source line has positional gaps the '
     'extractors read as spaces). The new character 上 is not encodable with the original resource (IPAPMincho subset); '
     'IPAPMincho is not installed here.'),
    ('S-I1', 'practical with the current approach (control)',
     'InDesign Japanese, same length (a year); a tall glyph of the next line overlaps the paragraph',
     'B2 practical. Every new character is encodable with the original resource. The route of issue #122 removed the '
     'next line\'s glyph (redaction by box). Optional-content BDCs, no MCID.'),
    ('S-W2', 'practical with the current approach (control)',
     'Word Japanese, tagged page, shorter (a phrase deleted)',
     'B2 practical. Every new character is encodable with the original resource (MS-Gothic subset). Tagged (MCIDs).'),
]

SEQUENCE = [
    dict(step='import', what='click + selection -> ParagraphIR, once, from the source PDF'),
    dict(step='E1', what='the case edit (old -> new) on the IR; written back to PDF 1'),
    dict(step='E2', what='on the same IR (no re-import): new -> old; written back to PDF 2 from the source'),
    dict(step='undo', what='on the same IR: undo E2 then E1; the IR equals the imported one (text, runs, layout)'),
    dict(step='alone', what='PDF 1 alone: a fresh import from PDF 1 (no IR state), new -> old; written to PDF 3'),
]

CONDITIONS = [
    'saved: the paragraph text after the edit is drawn where the IR lays it out (MuPDF, whitespace aside)',
    'protection (outside the paragraph): every source glyph not owned by the IR at its origin; drawings, images, '
    'annotations, links, other pages unchanged; no pixel differing outside the paragraph area (MuPDF 72/150 dpi, '
    'Poppler 72 dpi)',
    'structure: no MCID lost; on a tagged page the new text inside the paragraph\'s marked content; optional-content '
    'membership kept',
    'search and copy: search_copy.verified (issues #114/#116; as_in_source never passes)',
    'appearance: the original font resource (no substitute font); size and colour kept',
    'placement: lines on the IR\'s baselines, at the paragraph\'s left edge (or centre for centered text)',
    'practical = all of the above; a check not made is null and never a success (docs/development-rules.md, '
    'docs/practical-search.md unchanged)',
    'approvals: growth into empty space inside the paragraph\'s container only, approved per edit; nothing else',
    'refusal: an unapproved change, a collision, a lost glyph or broken structure is refused before anything is '
    'written (the output is not created, an existing one untouched)',
    'counted apart: E1 saved / practical, IR re-edit (E2), undo, PDF-alone re-edit, restore (E2 and alone equal the '
    'source: page text and pixels), refusal',
]


def main():
    route = {c['id']: c for c in json.loads(ROUTE.read_text())['cases']}
    cases = []
    for cid, role, why, found in CHOSEN:
        c = route[cid]
        cases.append(dict(id=cid, role=role, why=why, found_before=found, document=c['document'], page=c['page'],
                          sha256=c['sha256'], click=c['click'], old=c['old'], new=c['new'], kind=c['kind'],
                          current_b2=c['current']))
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    OUT.write_text(json.dumps(dict(
        schema='paragraph-ir-cases-1', frozen_at=rev, taken_from=str(ROUTE),
        policy=('Fixed before the ParagraphIR PoC is written (issue #124): 6 of the 16 cases of issue #122, three the '
                'current approach refuses, one it saves without practical success, two practical controls. No case is '
                'added, removed or replaced by a fixture afterwards. A missing input is not run, never a success.'),
        current=('B2: the committed results (every listed candidate approved). pdfeditor paths (propose-page with the '
                 'paragraph lines selected, accept-page, replace-text; edit-paragraph) are run on the same cases after '
                 'freezing, with their manual inputs recorded.'),
        sequence=SEQUENCE, conditions=CONDITIONS, cases=cases), ensure_ascii=False, indent=1) + '\n')
    print([c['id'] for c in cases])


if __name__ == '__main__':
    main()
