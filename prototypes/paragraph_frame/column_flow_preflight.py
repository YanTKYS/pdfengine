"""Freeze the column-flow evaluation manifest before the implementation is evaluated.

Inputs are the generated LibreOffice documents (column_fixtures) and real PDFs
already in the local corpus or scratch downloads. For every source the
manifest records the sha256 and a drawn-line witness (text, x range,
baseline) read with PyMuPDF, independent of the engine. LibreOffice output is
not byte-reproducible (timestamps), so a regenerated fixture is accepted when
its witness matches.

Expected outcomes, expected logical texts and the allowed movement ranges come
from the fixture definitions and the observed columns, never from the engine.

  .venv/bin/python -m prototypes.paragraph_frame.column_flow_preflight \
      --fixtures tmp/column-fixtures --fusei <path to 477.pdf> \
      --out evaluations/paragraph_frame_column_flow/manifest.json
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import pymupdf
from . import column_fixtures as fixtures

SCHEMA = 'paragraph-frame-column-flow-1'


def witness(path, page):
    d = pymupdf.open(path)
    try:
        lines = []
        for b in d[page - 1].get_text('dict')['blocks']:
            for line in b.get('lines', []):
                text = ''.join(s['text'] for s in line['spans'])
                if text.strip():
                    lines.append(dict(text=text, x0=round(line['bbox'][0], 1), x1=round(line['bbox'][2], 1),
                                      baseline=round(line['spans'][0]['origin'][1], 2), size=round(line['spans'][0]['size'], 2)))
        return lines
    finally:
        d.close()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def columns(lines, size=10.5):
    """Observed body columns (oracle only): x ranges and the shared deepest baseline."""
    body = [l for l in lines if abs(l['size'] - size) < .1]
    cols = []
    for l in sorted(body, key=lambda l: l['x0']):
        for col in cols:
            if abs(col['x0'] - l['x0']) < 2:
                col['x1'] = max(col['x1'], l['x1']); col['top'] = min(col['top'], l['baseline']); col['bottom'] = max(col['bottom'], l['baseline'])
                break
        else:
            cols.append(dict(x0=l['x0'], x1=l['x1'], top=l['baseline'], bottom=l['baseline']))
    bottom = max(c['bottom'] for c in cols)
    return [dict(c, allowed=[c['x0'] - .5, round(c['top'] - 10.5, 1), c['x1'] + .5, round(bottom + 3.5, 1)]) for c in cols]


def body(doc):
    return [p for p in doc['body'] if isinstance(p, str)]


def edited(paragraphs, index, old, new):
    text = paragraphs[index]
    if text.count(old) != 1:
        raise SystemExit(f'ambiguous or missing old text {old!r} in paragraph {index}')
    return paragraphs[:index] + [text.replace(old, new)] + paragraphs[index + 1:]


JA_GROW = ('体験できます。', '体験できます。各会場では、備蓄物資の点検方法や家族との連絡方法も確認できます。')
JA_SHORT = ('避難所では、受付の手順や間仕切りの設置、備蓄物資の配布方法を実際に体験できます。', '避難所の運営も体験できます。')
JA_TAIL = ('定期的に入れ替えるようにしてください。', '定期的に入れ替えるようにしてください。会場では、備蓄物資の点検方法も確認できます。')
JA_FIT = ('前日の午後五時まで', '前日の午後三時まで')
JA_FLOOD = ('一緒に確認します。', '一緒に確認します。' + '各会場では、備蓄物資の点検方法や家族との連絡方法も確認できます。' * 14)
JA_P5 = ('回答は会場の受付で行えます。', '回答は会場の受付で行えます。会場では、備蓄物資の点検方法も確認できます。')
EN_GROW = ('at all times.', 'at all times. Holds placed before the closure will be kept for the whole of the work.')
EN_SHORT = ('without steps. Wireless access will also be improved on every floor.', 'without steps.')
EN_FIT = ('first of June', 'first of July')


def sequence(name, doc, steps):
    """Steps of (paragraph index, (old, new), approved continuation candidates, expected outcome)."""
    text = body(doc)
    result = []
    for index, (old, new), approved, outcome, purpose in steps:
        after = edited(text, index, old, new) if outcome != 'refused' else text
        result.append(dict(paragraph=index, old=old, new=new, approve_continuation=approved, expected_outcome=outcome, purpose=purpose,
                           expected_target=after[index] if outcome != 'refused' else None,
                           expected_flow_text=after if outcome != 'refused' else None,
                           allowed_moving_paragraphs=list(range(index, len(text))) if outcome != 'refused' else []))
        text = after
    return dict(document=name, steps=result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--fusei', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    version = subprocess.run(['soffice', '--version'], capture_output=True, text=True).stdout.strip()
    generator = sha(Path(fixtures.__file__))
    documents = {}
    for name, (doc, tagged) in fixtures.DOCUMENTS.items():
        path = args.fixtures / f'{name}.pdf'
        lines = witness(path, 1)
        documents[name] = dict(kind='generated', generator='prototypes/paragraph_frame/column_fixtures.py', generator_sha256=generator,
                               libreoffice=version, sha256_at_freeze=sha(path), page=1, tagged=tagged, witness=lines,
                               columns=columns(lines), paragraphs=body(doc))
    enterprise = Path('evaluations/realpdf/corpus/lo_enterprise_en.pdf')
    documents['lo_enterprise_en'] = dict(kind='real', path=str(enterprise), sha256=sha(enterprise), page=2, tagged=False,
                                         source_url='https://wiki.documentfoundation.org/images/3/36/EN_Enterprise.pdf',
                                         witness=witness(enterprise, 2), columns=columns(witness(enterprise, 2), 12.0),
                                         traits=['three independent columns, each with its own headings',
                                                 'column 2 is white text over a full-height image',
                                                 'column 3 starts at the page top, unlike columns 1 and 2'])
    documents['osaka_fusei_477'] = dict(kind='real', path='(scratch download, not committed)', sha256=sha(args.fusei), page=7, tagged=False,
                                        source_url='https://www.pref.osaka.lg.jp/documents/63847/477.pdf',
                                        witness=witness(args.fusei, 7),
                                        traits=['Adobe InDesign 16.4 newsletter (府政だより 2025-03), untagged',
                                                'three columns of independent articles with their own headings',
                                                'body text in Form XObjects with a scaled text matrix',
                                                'column 3 last article ends mid-sentence at the column bottom (continues off this page)'])
    j, e = fixtures.JA, fixtures.EN
    sequences = [
        sequence('gen_columns_ja', j, [
            (1, JA_GROW, [], 'flow', 'growth beyond column 1 pushes the following text into column 2'),
            (1, JA_GROW[::-1], [], 'flow', 'restore: the following text returns to column 1'),
            (1, JA_SHORT, [], 'flow', 'shortening pulls the column-2 text back into column 1'),
            (5, JA_TAIL, [], 'flow', 'edit a paragraph that now starts in column 1 and continues in column 2')]),
        sequence('gen_columns_ja_tagged', j, [
            (1, JA_GROW, [], 'flow', 'tagged: one P spans the column break; growth pushes text into column 2'),
            (1, JA_GROW[::-1], [], 'flow', 'restore'),
            (1, JA_SHORT, [], 'flow', 'shortening pulls text back'),
            (5, JA_TAIL, [], 'flow', 'edit a later paragraph after the moves')]),
        sequence('gen_columns_en', e, [
            (2, EN_GROW, [], 'flow', 'Latin justified growth pushes text into column 2'),
            (2, EN_GROW[::-1], [], 'flow', 'restore'),
            (1, EN_SHORT, [], 'flow', 'shortening pulls column-2 text back into column 1')]),
        sequence('gen_columns_en_tagged', e, [
            (2, EN_GROW, [], 'flow', 'tagged Latin growth'),
            (2, EN_GROW[::-1], [], 'flow', 'restore'),
            (1, EN_SHORT, [], 'flow', 'shortening pulls text back')]),
        sequence('gen_columns_ja_boundary', fixtures.JA_BOUNDARY, [
            (4, JA_P5, [], 'refused', 'column break on a paragraph boundary (untagged): only a candidate; refused without approval'),
            (4, JA_P5, ['column 0->1'], 'flow', 'the same edit after explicit approval of the candidate continuation'),
            (4, JA_P5[::-1], [], 'flow', 'reopen: the stored approved flow is used without approving again')]),
        sequence('gen_columns_ja_boundary_tagged', fixtures.JA_BOUNDARY, [
            (4, JA_P5, [], 'flow', 'tagged: consecutive P siblings determine the reading order across the break')]),
    ]
    singles = [
        dict(id='J-fit', document='gen_columns_ja', paragraph=3, old=JA_FIT[0], new=JA_FIT[1], expected_outcome='saved-in-column',
             purpose='replacement that fits its own lines: nothing else moves', allowed_moving_paragraphs=[3]),
        dict(id='E-fit', document='gen_columns_en', paragraph=0, old=EN_FIT[0], new=EN_FIT[1], expected_outcome='saved-in-column',
             purpose='Latin replacement that fits its own lines', allowed_moving_paragraphs=[0]),
        dict(id='J-capacity', document='gen_columns_ja', paragraph=0, old=JA_FLOOD[0], new=JA_FLOOD[1], expected_outcome='refused',
             expected_reason='capacity', purpose='growth beyond both columns is refused; no font shrinking, no new region'),
        dict(id='J-heading', document='gen_columns_ja_heading', paragraph=4, old=JA_P5[0], new=JA_P5[1], expected_outcome='refused',
             expected_reason='capacity', purpose='column 2 starts with another article heading: never a continuation region'),
        dict(id='J-image', document='gen_columns_ja_image', paragraph=4, old=JA_P5[0], new=JA_P5[1], expected_outcome='refused',
             expected_reason='capacity', purpose='column 2 starts with a drawing: never a continuation region'),
        dict(id='R-enterprise-fit', document='lo_enterprise_en', old='for a simple memo', new='for a simple note', expected_outcome='saved-in-column',
             purpose='real PDF: in-column same-length edit (PR #88 E1); no column flow may be chosen'),
        dict(id='R-enterprise-grow', document='lo_enterprise_en', old='Writer will make it look great.', new='Writer will make it look great and keep it consistent.',
             expected_outcome='refused', expected_reason='capacity',
             purpose='real PDF (PR #88 E3): column 2 is an unrelated column over an image; it must not receive the text'),
        dict(id='R-fusei-grow', document='osaka_fusei_477', old='なお、過去の総合募集', new='なお、過去の総合募集の結果', expected_outcome='refused',
             purpose='real PDF: overset article at the column bottom; the neighbouring columns are other articles'),
    ]
    recognition = {
        'gen_columns_ja': [dict(from_column=0, to_column=1, expected='determined')],
        'gen_columns_ja_tagged': [dict(from_column=0, to_column=1, expected='determined')],
        'gen_columns_en': [dict(from_column=0, to_column=1, expected='determined')],
        'gen_columns_en_tagged': [dict(from_column=0, to_column=1, expected='determined')],
        'gen_columns_ja_boundary': [dict(from_column=0, to_column=1, expected='candidate')],
        'gen_columns_ja_boundary_tagged': [dict(from_column=0, to_column=1, expected='determined')],
        'gen_columns_ja_heading': [dict(from_column=0, to_column=1, expected='not-linked')],
        'gen_columns_ja_image': [dict(from_column=0, to_column=1, expected='not-linked')],
        'lo_enterprise_en': [dict(from_column=0, to_column=1, expected='not-linked'), dict(from_column=1, to_column=2, expected='not-linked')],
        'osaka_fusei_477': [dict(from_column=0, to_column=1, expected='not-linked'), dict(from_column=1, to_column=2, expected='not-linked')],
    }
    manifest = dict(
        schema=SCHEMA, baseline_commit=subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip(),
        policy=dict(
            frozen_before_implementation=True, no_case_replacement=True,
            inputs='PDF path, page, selected text, replacement, optional explicit approval of a candidate continuation id. No oracle geometry, no prior PDF, no sidecar, no manual coordinates.',
            sequences='Each step proposes, approves and saves from the PDF written by the previous step only.',
            allowed_movement='Only paragraphs from the edited one to the end of the flow may move, only inside the observed columns of the flow (columns[].allowed), never below the deepest original line of the flow; everything else must stay byte-identical in drawing.',
            generated_vs_real='Generated LibreOffice PDFs prove genuine threaded columns. Their success is reported separately and is not evidence of general real-PDF success.',
            real_corpus='No reachable real PDF shows same-page column continuation: lo_enterprise_en has independent columns, lo_newfeatures_ja wraps around a picture, osaka_fusei_477 has article boxes. Real PDFs are used for in-column edits and must-refuse cases.',
            unavailable='wakayama, okinawa, fccid, courts, cruel.org, takeo, niigata, kyoto, www.city.osaka, arxiv and www.documentfoundation.org were denied by the session network policy (HTTP 403).'),
        documents=documents, sequences=sequences, singles=singles, recognition=recognition)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(args.out, len(sequences), 'sequences', len(singles), 'singles')


if __name__ == '__main__':
    main()
