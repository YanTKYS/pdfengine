"""Read-only critical-path probe for docs/acrobat-critical-path.md.

Calls production APIs only; never edits pdfeditor/. Builds small Japanese
source PDFs with an embedded Type0/Identity-H TrueType font and classifies the
representative scenarios through the current entry points:

* one-shot attributed edit (`paragraph.edit_paragraph`);
* persistent shared flow v2 (`confirm_story` -> `confirm_shared_flow` ->
  `edit_shared_flow` -> `open_shared_flow` -> second edit);
* semantic layout entry (`semantic_layout.confirm_semantic_layout`).

Every call supplies the caller declarations those APIs require today
(glyph selection, width, region bounds, policies, font provider). The probe
records which declarations were needed; it does not claim user-intent-only
editing.

Usage: python -I evaluations/critical_path/scenario_probe.py OUT_DIR --font PATH.ttf
The font must be a TrueType (glyf) font with Japanese coverage (e.g. IPAGothic).
"""
import argparse
import hashlib
import json
from pathlib import Path

import pymupdf
from fontTools.ttLib import TTFont

from pdfeditor.attributed import inspect_paragraph
from pdfeditor.paragraph import edit_paragraph
from pdfeditor.selection import make_selection
from pdfeditor.shared_flow import confirm_shared_flow, edit_shared_flow, open_shared_flow
from pdfeditor.story_flow import confirm_story
from pdfeditor import semantic_layout as semantic
from pdfeditor.semantic_measure import font_policy

LONG = '各種申請書および添付資料一式ならびに関係書類の写しを含むすべての書類'


class Probe:
    def __init__(self, root, font):
        self.root, self.font = Path(root), str(Path(font).resolve())
        self.root.mkdir(parents=True, exist_ok=True)
        tt = TTFont(self.font)
        self.cmap, self.tt = tt.getBestCmap(), tt
        self.results = []

    def hexs(self, text):
        return '<' + ''.join(f'{self.tt.getGlyphID(self.cmap[ord(c)]):04X}' for c in text) + '>'

    def pdf(self, name, lines, *, tagged=False, clip=False, flip=False, larger=None):
        """lines: (x, baseline from page top, size, text). `larger` = (a, b) span set 3pt larger."""
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), '申', fontname='F1', fontfile=self.font)  # embeds /F1
        xref = page.get_contents()[0]
        body = ['0 0 595 842 re W n'] if clip else []
        if flip:
            body.append('1 0 0 -1 0 842 cm')
        for i, (x, y, size, text) in enumerate(lines):
            tm = f'1 0 0 -1 {x} {y} Tm' if flip else f'1 0 0 1 {x} {842 - y} Tm'
            if larger and i == 0:
                a, b = larger
                ops = (f'BT /F1 {size} Tf {tm} {self.hexs(text[:a])} Tj /F1 {size + 3} Tf {self.hexs(text[a:b])} Tj '
                       f'/F1 {size} Tf {self.hexs(text[b:])} Tj ET')
            else:
                ops = f'BT /F1 {size} Tf {tm} {self.hexs(text)} Tj ET'
            body.append(f'/P <</MCID {i}>> BDC {ops} EMC' if tagged else ops)
        doc.update_stream(xref, ('\n'.join(body) + '\n').encode())
        path = self.root / (name + '.pdf')
        doc.save(path)
        doc.close()
        return path

    def record(self, scenario, path, declarations, fn):
        try:
            detail, status = fn(), 'PASS-with-declarations'
        except Exception as exc:  # noqa: BLE001 - classification probe
            detail, status = f'{type(exc).__name__}: {exc}', 'REFUSED'
        self.results.append(dict(scenario=scenario, path=path, status=status,
                                 caller_declarations=declarations, detail=detail))
        print(f'[{status}] {scenario} via {path}: {detail}')

    # -- entry points -------------------------------------------------------
    def one_shot(self, src, width, edits, fonts, out, max_bottom=800):
        para = inspect_paragraph(src, make_selection(src, line_ids=['p1-l1'], explicit_width=width))
        edit_paragraph(src, self.root / (out + '.pdf'), para, edits, fonts=fonts, max_bottom=max_bottom)
        return self.lines(self.root / (out + '.pdf'))

    def story(self, src, pid, line_id, baseline):
        para = inspect_paragraph(src, make_selection(src, line_ids=[line_id], explicit_width=300))
        return confirm_story(src, {'part': dict(page=1, bounds=[70, 100, 374, 400], paragraph=para, paint_relations=[],
            layout=dict(x=72, baseline=baseline, width=300, max_bottom=400, min_line_height=16, first_line_indent=0))},
            paragraph_id=pid, chain=['part'], protected_regions={},
            styles={s['id']: dict(provider=dict(path=self.font), provider_relation='substituted') for s in para['styles']},
            style_assignments={'part': {s['id']: s['id'] for s in para['styles']}},
            typing_style_id=para['styles'][0]['id'])

    def shared(self, src, tag, paragraphs, edit):
        """paragraphs: {pid: (line_id, baseline)}; edit: (pid, start, end, text)."""
        stories = {pid: self.story(src, pid, line, base) for pid, (line, base) in paragraphs.items()}
        order = list(paragraphs)
        state = confirm_shared_flow(src, stories, flow_id='probe-' + tag, paragraph_order=order,
            regions={'R': dict(page=1, bounds=[70, 100, 376, 400], x=72, width=300,
                               first_baseline=min(b for _, b in paragraphs.values()))},
            region_order=['R'], slot_regions={pid: {'part': 'R'} for pid in order},
            paragraph_policies={pid: dict(min_line_height=16, first_line_indent=0, keep_together=False,
                break_before='auto', break_after='auto', empty=dict(kind='reserve-line', ascent=10, descent=3))
                for pid in order},
            follows=[dict(before=a, after=b, minimum_baseline_gap=30, region_start='reset-to-region-baseline')
                     for a, b in zip(order, order[1:])], protected_regions={})
        style = lambda st, pid: next(iter(st['paragraphs'][pid]['style_registry']))
        pid, a, b, text = edit
        rev1, rev2 = self.root / f'{tag}-rev1', self.root / f'{tag}-rev2'
        edit_shared_flow(src, state, rev1.with_suffix('.pdf'), rev1.with_suffix('.json'),
                         {pid: dict(edits=[dict(start=a, end=b, text=text, style_id=style(state, pid))])})
        model = json.loads(rev1.with_suffix('.json').read_text())
        if open_shared_flow(rev1.with_suffix('.pdf'), model)['status'] != 'restored':
            raise RuntimeError('rev1 does not reopen')
        edit_shared_flow(rev1.with_suffix('.pdf'), model, rev2.with_suffix('.pdf'), rev2.with_suffix('.json'),
                         {order[0]: dict(edits=[dict(start=0, end=0, text='再', style_id=style(model, order[0]))])})
        again = open_shared_flow(rev2.with_suffix('.pdf'), json.loads(rev2.with_suffix('.json').read_text()))
        if again['status'] != 'restored':
            raise RuntimeError('rev2 does not reopen')
        return dict(reopened='restored twice', lines=self.lines(rev2.with_suffix('.pdf')))

    def semantic(self, pdf, sidecar):
        model = json.loads(Path(sidecar).read_text())
        statement = dict(text=model['paragraphs']['A']['logical']['text'],
            style=dict(font_size='12', horizontal_scale='1', rise='0', tracking='0', word_spacing='0',
                       spacing_intent='confirmed-inline'),
            font=dict(sha=hashlib.sha256(Path(self.font).read_bytes()).hexdigest(), policy=font_policy()),
            body_style_id='body', edges=[], region_id='R')
        return semantic.confirm_semantic_layout(pdf, model, slot_id='slot-0', semantic=statement)['slots']['slot-0']['semantic']['version']

    @staticmethod
    def lines(path):
        with pymupdf.open(path) as doc:
            return [[round(line['spans'][0]['origin'][1], 1), ''.join(s['text'] for s in line['spans'])]
                    for block in doc[0].get_text('dict')['blocks'] for line in block.get('lines', [])]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('out', type=Path)
    parser.add_argument('--font', required=True)
    args = parser.parse_args()
    p = Probe(args.out, args.font)
    fonts = {'body': dict(path=p.font)}
    one = ['selection line id', 'explicit width', 'max_bottom', 'font file for new text']
    flow = ['selection line id', 'explicit width', 'container bounds/layout', 'style provider font file',
            'style assignment', 'shared region bounds', 'paragraph policies', 'follows gap']

    s1 = p.pdf('one-line', [(72, 700, 12, '申請書を提出してください。')])
    p.record('S1 申請書→各種申請書', 'edit_paragraph', one,
             lambda: p.one_shot(s1, 300, [dict(start=0, end=3, text='各種申請書', font_id='body')], fonts, 's1'))
    p.record('S1 without explicit width', 'edit_paragraph', one[:1] + one[2:],
             lambda: p.one_shot(s1, None, [dict(start=0, end=3, text='各種申請書', font_id='body')], fonts, 's1-w'))
    p.record('S1 without font for new text', 'edit_paragraph', one[:3],
             lambda: p.one_shot(s1, 300, [dict(start=0, end=3, text='各種申請書')], {}, 's1-f'))
    p.record('S2 1 line -> 2 lines', 'edit_paragraph', one,
             lambda: p.one_shot(s1, 160, [dict(start=0, end=3, text='各種申請書および添付資料', font_id='body')], fonts, 's2'))

    top = p.pdf('flow-one', [(72, 120, 12, '申請書を提出してください。')])
    p.record('S1 persistent', 'shared flow v2', flow, lambda: p.shared(top, 's1', {'A': ('p1-l1', 120)}, ('A', 0, 3, '各種申請書')))
    p.record('S2 persistent wrap', 'shared flow v2', flow, lambda: p.shared(top, 's2', {'A': ('p1-l1', 120)}, ('A', 0, 3, LONG)))
    p.record('S4 Japanese mid-sentence, different length', 'shared flow v2', flow,
             lambda: p.shared(top, 's4', {'A': ('p1-l1', 120)}, ('A', 4, 6, '必ず提出')))
    mixed = p.pdf('flow-mixed', [(72, 120, 12, '本日は重要なお知らせがあります。')], larger=(3, 5))
    p.record('S3 mixed sizes in one paragraph (one-shot)', 'edit_paragraph', one,
             lambda: p.one_shot(mixed, 300, [dict(start=7, end=11, text='ご案内事項', font_id='body')], fonts, 's3', 400))
    p.record('S3 mixed sizes in one paragraph (persistent)', 'shared flow v2', flow,
             lambda: p.shared(mixed, 's3', {'A': ('p1-l1', 120)}, ('A', 7, 11, 'ご案内事項')))
    two = p.pdf('flow-two', [(72, 120, 12, '申請書を提出してください。'), (72, 150, 12, '期限は十月十五日です。')])
    p.record('S5 upper paragraph grows, lower follows', 'shared flow v2', flow,
             lambda: p.shared(two, 's5', {'A': ('p1-l1', 120), 'B': ('p1-l2', 150)}, ('A', 0, 3, LONG)))
    for name, kw in (('tagged (BDC/EMC)', dict(tagged=True)), ('page clip', dict(clip=True)), ('y-flip CTM', dict(flip=True))):
        tag = name.split()[0].replace('-', '')
        variant = p.pdf('variant-' + tag, [(72, 120, 12, '申請書を提出してください。')], **kw)
        p.record(f'S1 on {name} page (one-shot)', 'edit_paragraph', one,
                 lambda v=variant, t=tag: p.one_shot(v, 300, [dict(start=0, end=3, text='各種申請書', font_id='body')],
                                                     fonts, 'v-' + t, 400))
        p.record(f'S1 on {name} page (persistent)', 'shared flow v2', flow,
                 lambda v=variant, t=tag: p.shared(v, 'v-' + t, {'A': ('p1-l1', 120)}, ('A', 0, 3, '各種申請書')))
    p.record('S1 text in the semantic layer', 'confirm_semantic_layout', flow + ['complete semantic statement'],
             lambda: p.semantic(args.out / 's1-rev1.pdf', args.out / 's1-rev1.json'))
    (args.out / 'results.json').write_text(json.dumps(p.results, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
