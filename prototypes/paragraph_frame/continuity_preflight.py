"""Freeze continuous-editing sequences and unused-PDF requests before implementation.

Everything here is evaluation input or an evaluation-only oracle. B2 receives
only the PDF path, page, selected text, replacement and an optional explicit
substitute approval; oracle geometry and glyph IDs never enter B2.
"""
import hashlib
import json
from pathlib import Path
from pdfeditor.content_stream import ContentPage
from pdfeditor.page_proposal import _Analysis

DEST = Path('evaluations/paragraph_frame_continuity/manifest.json')
PRIOR = Path('evaluations/paragraph_frame_generalization/manifest.json')

# Each step edits the PDF saved by the previous step, from the PDF alone.
SEQUENCES = [
    dict(id='Q1', document='newfeatures', tagged=False, purpose='PR #87 N2: shorten to one line, then grow again',
         steps=[('追加・更新された幾つかの注目の新機能', '主な新機能', 'shorten-to-one-line'),
                ('主な新機能', '追加・更新された幾つかの注目の新機能', 'restore-original-text'),
                ('ご紹介します。', 'ご紹介します。導入前に変更点を確認し、普段の作業で必要な機能を試してください。', 'grow-beyond-original'),
                ('普段の作業で必要な機能', '日常業務でよく使う機能', 'replace-other-text')]),
    dict(id='Q2', document='osaka', tagged=True, purpose='Word P/Span: shorten to one line, follower moves up and back',
         steps=[('国立感染症研究所においてガイドラインが作成されましたので、参考に送付いたします。', 'ガイドラインを送付します。', 'shorten-to-one-line'),
                ('ガイドラインを送付します。', '国立感染症研究所においてガイドラインが作成されましたので、参考に送付いたします。', 'restore-original-text'),
                ('参考に送付いたします。', '参考に送付いたします。内容を確認のうえ、関係者への周知をお願いします。', 'grow-beyond-original'),
                ('関係者への周知', '会員への周知', 'replace-other-text')]),
    dict(id='Q3', document='fire', tagged=True, purpose='Word direct-P MCIDs: shorten to one line, two followers move',
         steps=[('地域の実情に応じ、別紙に留意の上、感染症対策を円滑かつ適切に実施いただくようお願いします。', '感染症対策をお願いします。', 'shorten-to-one-line'),
                ('感染症対策をお願いします。', '地域の実情に応じ、別紙に留意の上、感染症対策を円滑かつ適切に実施いただくようお願いします。', 'restore-original-text'),
                ('お願いします。', 'お願いします。関係機関と情報を共有してください。', 'grow-beyond-original'),
                ('円滑かつ適切に', '迅速かつ確実に', 'replace-other-text')]),
]

# Unused originals. Oracle paragraphs are line IDs of the current analysis;
# frames bound the evaluation-only exterior-pixel mask and movement envelope.
NEW_DOCUMENTS = {
    'enterprise': dict(source='lo_enterprise_en', page=2, traits=[
        'LibreOffice 5.1 Draw, untagged, English, landscape', 'three justified columns sharing baselines',
        'TJ word-space justification', 'bold heading runs', 'column 2 is white text over a full-column image',
        'line-end soft hyphenation (cre-/ating, profes-/sional, de-/ploying)'],
        regions={
            'col1': dict(lines=[['p2-l14', 'p2-l17', 'p2-l20', 'p2-l23', 'p2-l25', 'p2-l28', 'p2-l31'], ['p2-l35'],
                                ['p2-l38', 'p2-l41', 'p2-l44', 'p2-l47', 'p2-l50', 'p2-l53']], frame=[27, 192, 257, 428]),
            'col1-calc': dict(lines=[['p2-l38', 'p2-l41', 'p2-l44', 'p2-l47', 'p2-l50', 'p2-l53']], frame=[27, 324, 257, 428]),
            'col2-draw': dict(lines=[['p2-l39', 'p2-l42', 'p2-l45', 'p2-l48', 'p2-l51', 'p2-l54'], ['p2-l56'], ['p2-l58', 'p2-l60', 'p2-l62'],
                                     ['p2-l64'], ['p2-l67', 'p2-l69', 'p2-l71']], frame=[311, 324, 540, 598]),
            'col3-deploy': dict(lines=[['p2-l27', 'p2-l30'], ['p2-l33', 'p2-l34', 'p2-l37', 'p2-l40'],
                                       ['p2-l43', 'p2-l46', 'p2-l49', 'p2-l52', 'p2-l55'],
                                       ['p2-l57', 'p2-l59', 'p2-l61', 'p2-l63', 'p2-l65', 'p2-l66', 'p2-l68', 'p2-l70']], frame=[592, 255, 812, 598]),
        }),
    'symposium': dict(source='word_osaka_symposium', page=1, traits=[
        'Ghostscript 9.27 re-distilled Word notice, untagged, Japanese', 'five Generic CID fonts with Identity-H',
        'no ToUnicode: every glyph extracts as U+FFFD', 'first-line indent, justified Japanese body'],
        regions={
            'body1': dict(lines=[['p1-l20', 'p1-l21', 'p1-l22', 'p1-l23']], frame=[83, 343, 516, 418],
                          visual_text='令和７年１月26日（日）に仙台国際センター（宮城県仙台市）において、公益社団法人日本医師会・公益社団法人日本獣医師会・厚生労働省による連携シンポジウム「人・動物におけるカビが関わる疾病」を別添のとおり開催することとなりました。'),
            'body4': dict(lines=[['p1-l33', 'p1-l34']], frame=[83, 586, 516, 623],
                          visual_text='つきましては、関係者の皆様へ本シンポジウムの開催について広く周知いただき、多くの方々に御参加いただけるよう御配慮の程よろしくお願いします。'),
        }),
}

NEW_REQUESTS = [
    ('E1', 'enterprise', 'col1', 'same-length', 'for a simple memo', 'for a simple note'),
    ('E2', 'enterprise', 'col3-deploy', 'shorten', 'can be selected according to', 'depends on'),
    ('E3', 'enterprise', 'col1', 'grow-wrap', 'Writer will make it look great.', 'Writer will make it look great and keep it consistent.'),
    ('E4', 'enterprise', 'col1', 'cross-line', "It's simple enough to use", 'It is simple enough to use'),
    ('E5', 'enterprise', 'col2-draw', 'white-text-over-image', 'from a quick sketch', 'from a rough sketch'),
    ('E6', 'enterprise', 'col1-calc', 'hyphenated-paragraph', 'First-time users find it', 'New users find it'),
    ('S1', 'symposium', 'body1', 'same-length-no-unicode', '別添のとおり', '下記のとおり'),
    ('S2', 'symposium', 'body4', 'shorten-no-unicode', '多くの方々に御参加いただけるよう御配慮の程よろしくお願いします。', '御参加をお願いします。'),
]


def ranges(ids):
    """Compact ordered glyph IDs as [first, last] runs."""
    runs = []
    for i in ids:
        if runs and runs[-1][1] + 1 == i:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return runs


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    prior = json.loads(PRIOR.read_text())
    sources = {s['id']: s for s in json.loads(Path('evaluations/sources.json').read_text())}
    for spec in prior['documents'].values():
        assert sha(spec['path']) == spec['sha256'], spec['id']
    result = dict(schema='paragraph-frame-continuity-1', baseline_commit='279736f',
                  regression_manifest=dict(path=str(PRIOR), sha256=sha(PRIOR), cases=[r['id'] for r in prior['requests']]),
                  policy=dict(frozen_before_implementation=True, no_case_replacement=True,
                              b2_inputs='PDF path, page, selected text, replacement, optional explicit substitute approval. '
                                        'No oracle geometry, no prior PDF, no sidecar, no manual coordinates.',
                              sequences='Each step proposes, approves and saves from the PDF written by the previous step only, '
                                        'in a separate process. The original PDF is never an input after step 1.',
                              stability='Compare each saved target paragraph with the original: continuation left edge, first-line x, '
                                        'right capacity, line pitch, font resource/program, size, colour and follower positions. '
                                        'After restore-original-text the original line text, x and baselines are the expected layout.',
                              unavailable='wakayama, okinawa and fcc_chrome hosts were denied by the session network policy (HTTP 403) '
                                          'after domains were requested; reachable unused sources were used instead.'),
                  font=prior['font'], documents={}, sequences=[], requests=[])
    for key in ('osaka', 'fire', 'newfeatures'):
        spec = prior['documents'][key]
        # The per-glyph oracle stays in the prior manifest (referenced by SHA-256).
        result['documents'][key] = dict({k: spec[k] for k in ('id', 'path', 'sha256', 'source_url', 'page', 'tagged', 'frame')},
                                        oracle=f'{PRIOR}#documents.{key}',
                                        paragraphs=[dict(text=p['text'], source_line_baselines=p['source_line_baselines'])
                                                    for p in spec['paragraphs']])
    for seq in SEQUENCES:
        text = result['documents'][seq['document']]['paragraphs'][0]['text']
        steps = []
        for old, new, kind in seq['steps']:
            assert text.count(old) == 1, (seq['id'], old, text)
            text = text.replace(old, new)
            steps.append(dict(old=old, new=new, category=kind, expected_target=text))
        assert steps[1]['expected_target'] == result['documents'][seq['document']]['paragraphs'][0]['text']
        result['sequences'].append(dict(seq, steps=steps))
    for key, doc in NEW_DOCUMENTS.items():
        source = sources[doc['source']]
        assert sha(source['path']) == source['sha256'], key
        analysis = _Analysis(source['path'], doc['page'])
        by_id = {l['id']: l for l in analysis.lines}
        content = ContentPage(source['path'], doc['page'])
        try:
            regions = {}
            for name, region in doc['regions'].items():
                paragraphs = []
                for ids in region['lines']:
                    glyphs = [i for line in ids for i in by_id[line]['glyph_ids'] if content.actual[i]['unicode'].strip()
                              or i != by_id[line]['glyph_ids'][0]]
                    rows = [''.join(content.actual[i]['unicode'] for i in by_id[line]['glyph_ids']).strip() for line in ids]
                    logical = rows[0]
                    for row in rows[1:]:
                        # A Latin line break implies a word space unless the line ends
                        # with a hyphen (soft/hard hyphen is ambiguous and kept as drawn).
                        joint = ' ' if logical[-1:].isascii() and row[:1].isascii() and not logical.endswith('-') else ''
                        logical += joint + row
                    paragraphs.append(dict(line_ids=ids, drawn_text=''.join(content.actual[i]['unicode'] for i in glyphs), text=logical,
                                           glyph_ids=ranges(glyphs), source_line_baselines=[round(by_id[line]['baseline'], 3) for line in ids],
                                           line_x=[round(by_id[line]['x'], 3) for line in ids],
                                           line_pen_end=[round(by_id[line]['pen_end'], 3) for line in ids]))
                regions[name] = dict(frame=region['frame'], paragraphs=paragraphs,
                                     **({'visual_text': region['visual_text']} if 'visual_text' in region else {}))
        finally:
            content.close()
        result['documents'][key] = dict(id=source['id'], path=source['path'], sha256=source['sha256'], source_url=source['source_url'],
                                        page=doc['page'], tagged=False, role='unused-holdout', traits=doc['traits'], regions=regions)
    for ident, key, region, kind, old, new in NEW_REQUESTS:
        reg = result['documents'][key]['regions'][region]
        text = reg.get('visual_text') or reg['paragraphs'][0]['text']
        assert text.count(old) == 1, (ident, old, text)
        unicode_text = reg['paragraphs'][0]['text']
        result['requests'].append(dict(id=ident, document=key, region=region, category=kind, old=old, new=new,
                                       expected=text.replace(old, new),
                                       selectable_by_unicode=unicode_text.count(old) == 1))
    DEST.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    # Keep short numeric arrays on one line.
    import re
    text = re.sub(r'\[\s+([-0-9.,\s]+?)\s+\]', lambda m: '[' + ', '.join(v.strip() for v in m.group(1).split(',')) + ']', text)
    DEST.write_text(text + '\n')
    print(json.dumps({r['id']: r['selectable_by_unicode'] for r in result['requests']}, ensure_ascii=False))
    for key in ('enterprise', 'symposium'):
        for name, reg in result['documents'][key]['regions'].items():
            print(key, name, [p['text'][:60] for p in reg['paragraphs']])


if __name__ == '__main__':
    main()
