"""Layout fidelity of B2 edits: old (main) and new B2 on one frozen manifest.

  .venv/bin/python -m prototypes.paragraph_frame.fidelity_evaluate --phase freeze --fusei <477.pdf> --pdfjs <dir>
  .venv/bin/python -m prototypes.paragraph_frame.fidelity_evaluate --phase run --label old|new --code <tree> --root tmp/<dir> \
      --fusei <477.pdf> --pdfjs <dir>
  .venv/bin/python -m prototypes.paragraph_frame.fidelity_evaluate --phase collect --roots old=tmp/<a> new=tmp/<b>

``freeze`` (run on main, before the implementation) writes the manifest: for
each case the paragraph as main infers it (text, box, drawn lines) and the
edits, each checked to select exactly one occurrence in the paragraph.
``run`` saves every edit with the B2 of ``--code`` (a source tree; each edit
in its own process) and audits it: the existing safety and quality audit
(indesign_evaluate.audit, both renderers, region, extraction, search), the
structure audit of tagged_evaluate, continued editing from the saved PDF
alone, and the fidelity measures below. ``collect`` writes the compact
comparison.

Fidelity is measured on the drawn glyphs only (PyMuPDF), independently of the
engine: the characters of the paragraph in the source and in the saved PDF
are aligned by text (difflib); an aligned character is unchanged text. For
each one the measures record whether its glyph kept its origin (within 0.01
pt), its font and size; the number of drawn lines; and whether each line
break of the source outside the edited text is still a line break.
"""
import argparse
import difflib
import json
from pathlib import Path
import subprocess
import sys
import time
import pymupdf

MANIFEST = Path('evaluations/paragraph_frame_fidelity/manifest.json')
RESULTS = MANIFEST.parent / 'results.json'

# (case, document, page, anchor, purpose, edits); an edit is (kind, old, new);
# 'reedit' edits apply to the output of the edit named in ``after``.
CASES = [
    ('C01', 'osaka_fusei_477', 7, '令和5年の大', 'InDesign, ragged CJK, 5 lines', [
        ('identity', '悩みを抱えている人は', '悩みを抱えている人は'),
        ('same', '悩みを抱えている人は', '悩みを持っている人は'),
        ('shorten', '悩まず専門機関に', '悩まず機関に'),
        ('lengthen', 'ひとりで悩まず', 'ひとりで悩まず、ひとりで抱えず'),
        ('reedit:lengthen', 'ひとりで悩まず、ひとりで抱えず', 'ひとりで悩まず')]),
    ('C02', 'osaka_fusei_477', 7, '賃貸住宅の退', 'InDesign, justified CJK, 5 lines', [
        ('identity', 'トラブルが発生しています', 'トラブルが発生しています'),
        ('same', '損耗などの補修や修繕', '損耗などの修繕や補修'),
        ('shorten', '損耗などの補修や修繕の費用を', '修繕の費用を'),
        ('lengthen', 'ぜひご活用ください。', 'ぜひご活用ください。ぜひご相談ください。'),
        ('reedit:lengthen', 'ぜひご活用ください。ぜひご相談ください。', 'ぜひご活用ください。')]),
    ('C03', 'osaka_fusei_477', 7, '学歴・職務経', 'InDesign, justified CJK, 2 lines', [
        ('identity', '警察行政職を除き', '警察行政職を除き'),
        ('same', '学歴・職務経験不問', '職務経験・学歴不問'),
        ('shorten', '不問。警察行政職を除き、日本国籍', '不問。日本国籍'),
        ('lengthen', 'HPまで。', 'HPまで。学歴不問。'),
        ('reedit:lengthen', 'HPまで。学歴不問。', 'HPまで。')]),
    ('C04', 'osaka_fusei_477', 7, '令和7年（2025年）3月1日', 'InDesign, header text in a page-local Form XObject', [
        ('identity', '3月1日', '3月1日'),
        ('same', '3月1日', '3月7日'),
        ('shorten', '（2025年）3月', '3月'),
        ('lengthen', '3月1日', '3月21日'),
        ('reedit:lengthen', '3月21日', '3月1日')]),
    ('C05', 'issue17730', 1, 'There ', 'Antenna House, justified Latin, 4 lines', [
        ('identity', 'two types of links', 'two types of links'),
        ('same', 'two types of links', 'two kinds of links'),
        ('shorten', 'may be either an ID', 'may be an ID'),
        ('lengthen', 'or a website.', 'or a website or a file.'),
        ('lengthen-wrap', 'a specific position in', 'a specific position or page in'),
        ('reedit:lengthen-wrap', 'a specific position or page in', 'a specific position in')]),
    ('C06', 'word_osaka_fire_notice', 1, 'なお、標記災', 'Word (tagged P/Span), first-line indent, 2 lines', [
        ('identity', '地方公共団体', '地方公共団体'),
        ('same', '円滑かつ適切に', '適切かつ円滑に'),
        ('shorten', '被災されていない', '被災していない'),
        ('lengthen', '実施いただく', '実施していただく'),
        ('reedit:lengthen', '実施していただく', '実施いただく')]),
    ('C07', 'word_osaka_guideline', 1, '標記につきま', 'Word (tagged), first-line indent, 2 lines', [
        ('identity', '国立感染症研究所', '国立感染症研究所'),
        ('same', '作成されました', '送付されました'),
        ('shorten', '研究所において', '研究所で'),
        ('lengthen', '参考に送付', '参考までに送付'),
        ('reedit:lengthen', '参考までに送付', '参考に送付')]),
    ('C08', 'lo_enterprise_en', 2, 'Calc is a', 'LibreOffice, ragged Latin with a line-final hyphen, 6 lines', [
        ('identity', 'number crunchers', 'number crunchers'),
        ('same', 'range of', 'array of'),
        ('shorten', 'intuitive and easy to learn', 'easy to learn'),
        ('lengthen', 'advanced functions.', 'advanced and intuitive functions.'),
        ('lengthen-wrap', 'designed with the user', 'designed with the end user'),
        ('reedit:lengthen', 'advanced and intuitive functions.', 'advanced functions.')]),
    ('C09', 'lo_migration_ja', 1, 'LibreOfficeは自由ソ', 'LibreOffice, Japanese with Latin, first-line indent, 4 lines', [
        ('identity', '個人向け生産性', '個人向け生産性'),
        ('same', '文書作成およびデータ処理', 'データ処理および文書作成'),
        ('shorten', '六つの豊富な機能の', '豊富な機能の'),
        ('lengthen', '描画ソフトDraw、', '描画ソフトDraw、表計算ソフトCalc、'),
        ('reedit:lengthen', '描画ソフトDraw、表計算ソフトCalc、', '描画ソフトDraw、')]),
    ('C10', 'prefilled_f1040', 1, 'Head of household', 'LiveCycle (tagged, IDTree), marker on an offset baseline, 3 lines', [
        ('identity', 'Head of household', 'Head of household'),
        ('same', 'enter this', 'write this'),
        ('shorten', '(See instructions.) If', 'If'),
        ('lengthen', 'child’s name here.', 'child’s full name here.'),
        ('reedit:lengthen', 'child’s full name here.', 'child’s name here.')]),
    ('C11', 'issue14297', 1, 'Cumulative effects', 'InDesign placed PDF (MCIDs without a tree), 3 lines', [
        ('identity', 'Cumulative effects', 'Cumulative effects'),
        ('same', 'changes in accounting', 'changes of accounting'),
        ('shorten', 'changes in accounting policies', 'policy changes'),
        ('lengthen', 'accounting policies', 'accounting policies in effect'),
        ('reedit:lengthen', 'accounting policies in effect', 'accounting policies')]),
    ('C12', '160F-2019', 1, 'cette transmission ne peut', 'Distiller (tagged TD without /Type), 1 line', [
        ('identity', 'ne peut se faire', 'ne peut se faire'),
        ('same', 'par voie', 'sur voie'),
        ('shorten', 'ne peut se faire que', 'se fait uniquement'),
        ('lengthen', 'voie électronique', 'voie électronique uniquement'),
        ('reedit:shorten', 'se fait uniquement', 'ne peut se faire que')]),
    ('C13', 'tracemonkey', 2, 'Bytecodes', 'TeX figure label in a Form XObject under reflected Tm and CTM', [
        ('identity', 'Bytecodes', 'Bytecodes'),
        ('shorten', 'Bytecodes', 'Bytecode'),
        ('reedit:shorten', 'Bytecode', 'Bytecodes')]),
    ('C14', 'canvas', 1, 'HTML5 Canvas', 'single-line Latin title', [
        ('identity', 'Canvas Cheat Sheet', 'Canvas Cheat Sheet'),
        ('same', 'v1.1', 'v1.5'),
        ('shorten', 'HTML5 Canvas Cheat', 'Canvas Cheat'),
        ('lengthen', 'v1.1', 'v1.1.5'),
        ('reedit:lengthen', 'v1.1.5', 'v1.1')]),
    ('C15', 'lo_migration_ja', 1, 'LibreOfficeは、', 'LibreOffice, Japanese with Latin, 4 lines (second paragraph)', [
        ('identity', 'あらゆる種類の', 'あらゆる種類の'),
        ('same', 'デンマーク、コペンハーゲン', 'コペンハーゲン、デンマーク'),
        ('shorten', '個人であっても、組織であったとしても', '個人であっても'),
        ('lengthen', '病院システムなど', '病院システムや政府など'),
        ('reedit:lengthen', '病院システムや政府など', '病院システムなど')]),
]


def source_of(doc, args):
    if doc == 'osaka_fusei_477':
        return Path(args.fusei)
    if doc.startswith(('word_', 'lo_')):
        return Path('evaluations/realpdf/corpus') / f'{doc}.pdf'
    return Path(args.pdfjs) / f'{doc}.pdf'


def sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --- fidelity measures (engine-independent) ---------------------------------

def glyphs(path, page, region):
    """Drawn glyphs whose box centre lies in ``region``, in reading order (rows by baseline, then x)."""
    with pymupdf.open(path) as d:
        found = []
        for b in d[page - 1].get_text('rawdict')['blocks']:
            for line in b.get('lines', []):
                for s in line['spans']:
                    for ch in s['chars']:
                        x0, y0, x1, y1 = ch['bbox']
                        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                        if region[0] <= cx <= region[2] and region[1] <= cy <= region[3] and ch['c'].strip():
                            found.append(dict(c=ch['c'], x=ch['origin'][0], y=ch['origin'][1], font=s['font'], size=round(s['size'], 3)))
    rows = []
    for g in sorted(found, key=lambda g: g['y']):
        if rows and abs(g['y'] - rows[-1][0]['y']) <= .35 * min(g['size'], rows[-1][0]['size']):
            rows[-1].append(g)
        else:
            rows.append([g])
    ordered = []
    for n, row in enumerate(rows):
        for g in sorted(row, key=lambda g: g['x']):
            ordered.append(dict(g, line=n))
    return ordered


def fidelity(source, output, page, box, edit_old, edit_new, paragraph, frame=None, grow=0.):
    """Unchanged glyphs kept, lines and line breaks of one save (see module doc).

    The saved paragraph is read in the source box widened to the approved
    frame's left and right edges and extended by ``grow`` points (the lines
    the proposal adds).
    """
    region = [box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3]
    before = glyphs(source, page, region)
    wide = list(region)
    if frame:
        wide = [min(region[0], frame[0] - 1), region[1], max(region[2], frame[2] + 1), region[3]]
    wide[3] += max(0., grow)
    after = glyphs(output, page, wide)
    a, b = ''.join(g['c'] for g in before), ''.join(g['c'] for g in after)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    pairs = [(i + k, j + k) for i, j, n in matcher.get_matching_blocks() for k in range(n)]
    # The edited characters in the source text (no whitespace): its span.
    bare = ''.join(ch for ch in paragraph if ch.strip())
    at = bare.find(''.join(ch for ch in edit_old if ch.strip()))
    span = (at, at + len(''.join(ch for ch in edit_old if ch.strip()))) if at >= 0 else None
    kept = moved = restyled = 0
    prefix_total = prefix_kept = 0
    deltas = []
    for i, j in pairs:
        g, h = before[i], after[j]
        d = max(abs(g['x'] - h['x']), abs(g['y'] - h['y']))
        deltas.append(d)
        same = d <= .01
        kept += same
        moved += not same
        restyled += (g['font'], g['size']) != (h['font'], h['size'])
        if span and i < span[0]:
            prefix_total += 1
            prefix_kept += same
    breaks = [i for i in range(1, len(before)) if before[i]['line'] != before[i - 1]['line']]
    index = dict(pairs)
    outside = [i for i in breaks if not span or not (span[0] < i <= span[1])]
    kept_breaks = sum(1 for i in outside if i in index and index[i] > 0 and (index[i] - 1) in index.values()
                      and after[index[i]]['line'] != after[index[i] - 1]['line'])
    unchanged = len(pairs)
    return dict(source_chars=len(a), output_chars_in_region=len(b), unchanged=unchanged, kept=kept, moved=moved, restyled=restyled,
                kept_ratio=round(kept / unchanged, 4) if unchanged else None,
                before_edit=dict(total=prefix_total, kept=prefix_kept),
                max_delta=round(max(deltas), 3) if deltas else None,
                source_lines=len({g['line'] for g in before}), output_lines=len({g['line'] for g in after}),
                source_breaks_outside_edit=len(outside), breaks_kept=kept_breaks)


# --- phases -----------------------------------------------------------------

def phase_freeze(args):
    from prototypes.paragraph_frame.auto import propose
    cases, documents = [], {}
    for cid, doc, page, anchor, purpose, edits in CASES:
        path = source_of(doc, args)
        documents.setdefault(doc, dict(sha256=sha(path), path=str(path) if 'evaluations/' in str(path) else '(scratch download, not committed)'))
        p = propose(str(path), page, anchor, anchor)
        lines = p['evidence']['observed']['lines'][:p['source_lines']]
        box = [min(l['bbox'][0] for l in lines), min(l['bbox'][1] for l in lines), max(l['bbox'][2] for l in lines), max(l['bbox'][3] for l in lines)]
        text = p['paragraphs'][0]
        rows = []
        for kind, old, new in edits:
            base = text
            if kind.startswith('reedit:'):
                prior = next(r for r in rows if r['kind'] == kind.split(':', 1)[1])
                base = prior['expected_text']
            if base.count(old) != 1:
                raise SystemExit(f'{cid} {kind}: {old!r} occurs {base.count(old)} times in the paragraph')
            rows.append(dict(kind=kind, old=old, new=new, after=kind.split(':', 1)[1] if kind.startswith('reedit:') else None,
                             expected_text=base.replace(old, new, 1)))
        cases.append(dict(id=cid, document=doc, page=page, anchor=anchor, purpose=purpose, paragraph=text,
                          box=[round(v, 2) for v in box], alignment=p['evidence']['inferred']['alignment'],
                          lines=[dict(text=l['text'], baseline=round(l['baseline'], 3), x=round(l['x'], 3), pen_end=round(l['pen_end'], 3)) for l in lines],
                          edits=rows))
        print(cid, doc, page, len(lines), 'lines', flush=True)
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    manifest = dict(schema='paragraph-frame-fidelity-manifest-1', baseline_commit='8c60fe3 (main after PR #91)', frozen_at=commit,
                    policy=('Frozen before the implementation. Old B2 is main 8c60fe3; new B2 is this branch. Every edit is a real text '
                            'change except "identity" (same string), which measures what an edit that changes nothing changes. '
                            'Outcomes are reported per edit; refusals never count as success.'),
                    measures=('saved; safety and quality (indesign_evaluate.audit, unchanged); structure (tagged_evaluate.structure_audit); '
                              'continued editing from the saved PDF; fidelity: unchanged glyphs at their source origin (0.01 pt), font and size, '
                              'drawn lines, and source line breaks outside the edited text kept'),
                    documents=documents, cases=cases)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')


def locate(source, page, old, box, code):
    """The occurrence of ``old`` whose paragraph's first line lies in ``box`` (as the engine counts)."""
    script = f'''
import json,sys
sys.path.insert(0,{str(code)!r})
from pdfeditor.content_stream import ContentPage
from pdfeditor.marked_content import observe_marked_content
from pdfeditor.page_proposal import _Analysis
from prototypes.paragraph_frame.auto_detect import group_text, observe
c=ContentPage({str(source)!r},{page})
_,_,groups=observe(c,observe_marked_content({str(source)!r},{page}),_Analysis({str(source)!r},{page}))
box={box!r};k=0;found=0
for g in groups:
    t=group_text(c,g)[0];x0,y0,x1,y1=g[0]['bbox']
    inside=box[0]-3<=(x0+x1)/2<=box[2]+3 and box[1]-3<=(y0+y1)/2<=box[3]+3
    at=0
    while (pos:=t.find({old!r},at))>=0:
        if inside:
            found=1;break
        k+=1;at=pos+1
    if found:break
print(json.dumps(k if found else 0))
'''
    run = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=code)
    return json.loads(run.stdout.strip().splitlines()[-1]) if run.returncode == 0 and run.stdout.strip() else 0


def edit(code, source, page, old, new, output, occurrence):
    command = [sys.executable, '-m', 'prototypes.paragraph_frame.generalization_cli', 'reedit', '--source', str(Path(source).resolve()), '--page', str(page),
               '--old', old, '--new', new, '--output', str(Path(output).resolve()), '--acknowledge-inference', '--occurrence', str(occurrence)]
    started = time.perf_counter()
    run = subprocess.run(command, capture_output=True, cwd=code)
    seconds = round(time.perf_counter() - started, 2)
    if run.returncode:
        last = [l for l in run.stderr.decode().splitlines() if l.strip()]
        return dict(outcome='refused', reason=last[-1] if last else 'unknown', seconds=seconds)
    load = lambda p: json.loads(Path(p).read_text())
    return dict(outcome='saved', seconds=seconds, proposal=load(Path(output).with_suffix('.proposal.json')), save=load(Path(output).with_suffix('.save.json')))


def reopen(code, output, page, new, paragraph):
    """Continued editing from the saved PDF alone, by the B2 of ``code`` (tagged_evaluate.reopen_unique)."""
    script = f'''
import json,sys
sys.path.insert(0,{str(code)!r})
from prototypes.paragraph_frame.tagged_evaluate import reopen_unique
print(json.dumps(reopen_unique({str(Path(output).resolve())!r},{page},{new!r},{paragraph!r})))
'''
    run = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=code)
    lines = run.stdout.strip().splitlines()
    return json.loads(lines[-1]) if run.returncode == 0 and lines else dict(ok=False, reason='reopen failed: ' + run.stderr.strip()[-200:])


def phase_run(args):
    from prototypes.paragraph_frame.indesign_evaluate import audit
    from prototypes.paragraph_frame.tagged_evaluate import structure_audit, supplementary
    manifest = json.loads(MANIFEST.read_text())
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    code = Path(args.code).resolve()
    results = {}
    only = set(args.only or [])
    for case in manifest['cases']:
        if only and case['id'] not in only:
            continue
        source = source_of(case['document'], args)
        outputs, rows = {}, {}
        for e in case['edits']:
            name = e['kind']
            folder = root / case['id'] / name.replace(':', '-')
            folder.mkdir(parents=True, exist_ok=True)
            output = folder / 'out.pdf'
            for p in (output, output.with_suffix('.proposal.json'), output.with_suffix('.save.json')):
                if p.exists():
                    p.unlink()
            start = outputs.get(e['after']) if e['after'] else source
            if start is None:
                rows[name] = dict(outcome='not run', reason='the previous step was not saved')
                print(case['id'], name, 'not run', flush=True)
                continue
            occurrence = locate(start, case['page'], e['old'], case['box'], code)
            result = edit(code, start, case['page'], e['old'], e['new'], output, occurrence)
            record = dict(outcome=result['outcome'], seconds=result['seconds'], occurrence=occurrence)
            if result['outcome'] == 'refused':
                record['reason'] = result['reason']
            else:
                outputs[name] = output
                proposal, save = result['proposal'], result['save']
                inferred = proposal['evidence']['inferred']
                frame = inferred.get('frame')
                x0, y0, x1, y1 = case['box']
                pitch = (case['lines'][1]['baseline'] - case['lines'][0]['baseline']) if len(case['lines']) > 1 else (y1 - y0) * 1.2
                grow = len(proposal['lines']) - len(case['lines'])
                if e['after']:
                    # The re-edited paragraph is the previous output's (its added lines included).
                    y1 += max(0, rows[e['after']]['grow']) * pitch
                    grow = max(grow, rows[e['after']]['grow'])
                region = [x0 - 3, y0 - 3, x1 + 3, y1 + 3 + max(0, grow) * pitch]
                record.update(lines=[l['text'] for l in proposal['lines']], alignment=inferred.get('alignment'), frame=frame,
                              audit=audit(start, output, case['page'], region, e['old'], e['new'], folder, occurrence=occurrence),
                              structure=structure_audit(start, output, case['page'], e['expected_text'], []),
                              supplementary=supplementary(start, output, case['page'], region, e['old'], e['new']),
                              grow=grow,
                              fidelity=fidelity(start, output, case['page'], [x0, y0, x1, y1], e['old'], e['new'],
                                                case['paragraph'] if not e['after'] else rows[e['after']]['expected'], frame, max(0, grow) * pitch),
                              expected=e['expected_text'],
                              continued_editing=reopen(code, output, case['page'], e['new'], e['expected_text']),
                              save={k: save.get(k) for k in ('drawn_in', 'stored_conditions', 'followers_repainted', 'source_glyphs_removed',
                                                             'layout', 'kept_glyphs')})
                if e['after']:
                    # Round trip: the restored paragraph against the original source.
                    record['round_trip'] = fidelity(source, output, case['page'], case['box'], e['new'], e['new'], case['paragraph'], frame, max(0, grow) * pitch)
            rows[name] = record
            a = record.get('audit') or {}
            f = record.get('fidelity') or {}
            print(case['id'], name, record['outcome'], record.get('reason', '')[-90:], a.get('failed'),
                  f and (f['kept'], f['unchanged'], f['source_lines'], f['output_lines'], f['breaks_kept'], f['source_breaks_outside_edit']), flush=True)
        results[case['id']] = rows
        (root / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str) + '\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', required=True, choices=['freeze', 'run', 'collect'])
    parser.add_argument('--fusei')
    parser.add_argument('--pdfjs')
    parser.add_argument('--code', default='.')
    parser.add_argument('--root')
    parser.add_argument('--label')
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--roots', nargs='*')
    args = parser.parse_args()
    if args.phase == 'freeze':
        phase_freeze(args)
    elif args.phase == 'run':
        phase_run(args)
    else:
        phase_collect(args)


def phase_collect(args):
    raise SystemExit('collect: written after the runs')


if __name__ == '__main__':
    main()
