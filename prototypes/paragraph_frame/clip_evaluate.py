"""Word text boxes: the text clip extended down on explicit approval, with its panel (issue #118).

  .venv/bin/python -m prototypes.paragraph_frame.clip_evaluate --phase freeze
  .venv/bin/python -m prototypes.paragraph_frame.clip_evaluate --phase run --root <dir>
  .venv/bin/python -m prototypes.paragraph_frame.clip_evaluate --phase collect --root <dir>

``freeze`` (before the implementation) records each case's input (hash),
page, selection and edit, the text box as the page draws it (the fill and
outline of its panel, every rectangular clip holding its text: operator,
rule, q depth, how many text shows each holds), the lines of the edited
paragraph, what lies below the box, and why the save is refused now. The
expected outcome is written by hand in CASES.

``run``, per case, with this tree's B2: the edit, then the edit back from the
saved PDF alone. Each step lists the candidates its proposal offers (clip and
panel extensions, chain elements, line decorations), tries the save without
any approval, with each kind of candidate alone, then with exactly the listed
ones (panel_evaluate.chain_step with the clips); an approval is never carried
to the next step. A saved step keeps the existing audit beside the
approved-change audit. Missing inputs are never a success.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pymupdf

from pdfeditor.content_stream import operators

from . import eval_records as er
from .fidelity_evaluate import source_of

MANIFEST = Path('evaluations/paragraph_clip/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'

CASES = [
    # Microsoft Word: a text box (its fill and outline two `re` operators); every text show inside it sets the
    # box's inner rectangle as its clip (`re W* n`, in its own q/Q, inside an outer q/Q with the same clip).
    dict(id='W1', group='target', document='word_osaka_fire_notice', page=1, selection='直通：03-3595-2257',
         edit=dict(old='直通：03-3595-2257', new='直通：03-3595-2257 直通：03-3595-2257'), panel=[373.75, 428.2, 544.0, 497.65],
         expect='saved on approving the clip extension and the panel extension of its text box (bottom only, by the growth); '
                'refused without either; the edit back from the saved PDF alone saved'),
    dict(id='W2', group='target', document='word_osaka_guideline', page=1, selection='06-4397-3205',
         edit=dict(old='06-4397-3205', new='06-4397-3205 06-6944-9156'), panel=[352.3, 638.0, 521.2, 701.75],
         expect='as W1'),
]


def _box(path, page, panel):
    """The text box as the page draws it: the panel's fill and outline, the rectangular clips inside it."""
    with pymupdf.open(path) as d:
        p = d[page - 1]
        data = b''.join(d.xref_stream(x) for x in p.get_contents())
        height = p.rect.height
        below = pymupdf.Rect(panel[0] - 2, panel[3], panel[2] + 2, panel[3] + 60)
        under = dict(text=[[round(v, 2) for v in b['bbox']] for b in p.get_text('dict')['blocks'] if pymupdf.Rect(b['bbox']).intersects(below)],
                     drawings=[[round(v, 2) for v in x['rect']] for x in p.get_drawings() if x['rect'].intersects(below)],
                     images=[[round(v, 2) for v in x['bbox']] for x in p.get_image_info() if pymupdf.Rect(x['bbox']).intersects(below)],
                     annotations=[[round(v, 2) for v in a.rect] for a in p.annots()],
                     links=[[round(v, 2) for v in link['from']] for link in p.get_links() if pymupdf.Rect(link['from']).intersects(below)])
    ops = list(operators(data))
    page_rect = lambda x, y, w, h: [round(x, 2), round(height - y - h, 2), round(x + w, 2), round(height - y, 2)]
    inside = lambda r: r[0] >= panel[0] - .5 and r[1] >= panel[1] - .5 and r[2] <= panel[2] + .5 and r[3] <= panel[3] + .5
    depth, paints, clips, open_clips = 0, [], [], []
    for k, op in enumerate(ops):
        if op.name == 'q':
            depth += 1
        elif op.name == 'Q':
            depth -= 1
            open_clips = [c for c in open_clips if c['depth'] <= depth]
        elif op.name == 're' and k + 1 < len(ops):
            r = page_rect(*map(float, op.args))
            nxt = ops[k + 1].name
            if not inside(r):
                continue
            if nxt in ('W', 'W*') and k + 2 < len(ops) and ops[k + 2].name == 'n':
                c = dict(rect=r, rule=nxt, depth=depth, offset=op.start, operator=data[op.start:op.end].decode('latin1'), shows=0)
                clips.append(c)
                open_clips.append(c)
            elif nxt in ('f', 'f*', 'F', 'S', 's', 'B', 'B*'):
                paints.append(dict(rect=r, paint=nxt, depth=depth, offset=op.start, operator=data[op.start:op.end].decode('latin1')))
        elif op.name in ('Tj', 'TJ', "'", '"'):
            for c in open_clips:
                c['shows'] += 1
    rects = sorted({tuple(c['rect']) for c in clips})
    return dict(paints=paints, clips=dict(rectangles=[list(r) for r in rects], count=len(clips), rules=sorted({c['rule'] for c in clips}),
                                          depths=sorted({c['depth'] for c in clips}), shows=[c['shows'] for c in clips],
                                          first=clips[0] if clips else None),
                below_60pt=under)


def phase_freeze(args):
    from .auto import approve, propose, save
    from .engine import Refused
    documents, cases = {}, []
    work = Path(args.root or 'tmp/clip-freeze')
    work.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        path = source_of(case['document'], args)
        if not path.exists():
            cases.append(dict(case, status='input missing'))
            continue
        documents[case['document']] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        out = dict(case)
        p = propose(str(path), case['page'], case['edit']['old'], case['edit']['new'])
        out['paragraph'] = p['paragraphs'][0]
        out['source_lines'] = [dict(text=l['text'], baseline=l['baseline'], bbox=l['bbox']) for l in p['evidence']['observed']['lines'][:p['source_lines']]]
        out['proposed_lines'] = [dict(text=l['text'], baseline=round(l['baseline'], 3)) for l in p['lines']]
        out['frame'] = [round(v, 2) for v in p['evidence']['inferred']['frame']]
        out['leading_basis'] = p['evidence']['inferred']['leading_basis']
        out['panels_evidence'] = p['evidence'].get('panels')
        try:
            save(str(path), approve(str(path), p, acknowledge_inference=True), str(work / f"{case['id']}.pdf"))
            out['now'] = dict(outcome='saved')
        except Refused as exc:
            out['now'] = dict(outcome='refused', reason=str(exc)[-300:])
        out['box'] = _box(path, case['page'], case['panel'])
        cases.append(out)
        print(case['id'], out['now'], out['box']['clips']['count'], out['box']['clips']['rectangles'], flush=True)
    import subprocess
    rev = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(schema='paragraph-clip-manifest-1', frozen_at=rev,
                    policy=('Frozen before the implementation (issue #118). A case saved without approving its clip extension, or with '
                            'only one of the clip and panel extensions approved, is a failure; a refusal, a missing input or a fixture is '
                            'never counted as a real-PDF success. Practical success follows eval_records.practical (issue #116).'),
                    documents=documents, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, default=str) + '\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--phase', choices=('freeze', 'run', 'collect'), required=True)
    p.add_argument('--root')
    p.add_argument('--fusei')
    p.add_argument('--pdfjs')
    args = p.parse_args()
    dict(freeze=phase_freeze)[args.phase](args)


if __name__ == '__main__':
    main()
