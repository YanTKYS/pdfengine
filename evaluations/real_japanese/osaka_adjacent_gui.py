"""Osaka guideline through the adjacent-Span GUI PoC: において -> におきまして.

  .venv/bin/python -m evaluations.real_japanese.osaka_adjacent_gui --mode session
  xvfb-run -a <python with tkinter> -m evaluations.real_japanese.osaka_adjacent_gui --mode gui
``session`` drives the UI-independent session; ``gui`` starts the Tk app and
drives it with canvas mouse events, typed text and button presses, taking a
screenshot (ImageMagick ``import``) at each stage. Each run saves into a new
directory and compares the outputs with the PR #81 result.
"""
import argparse
import json
from pathlib import Path
import subprocess
import time

from pdfeditor.selection import source_sha

SOURCE = Path('evaluations/realpdf/corpus/word_osaka_guideline.pdf')
SOURCE_SHA = '9070ff5c5defd634b65967e36b9996bf092deb6ec753561ea9188433259145fe'
SELECTED, REPLACEMENT = 'において', 'におきまして'
ANCHOR = None  # first glyph of the occurrence when the drawn text occurs more than once
EARLIER = Path('tmp/osaka-adjacent-intent-quality')


def occurrence(session):
    """Glyphs of the single drawn occurrence; a pointer would be required for more."""
    drawn = [g['text'] for g in session.glyphs]
    starts = [i for i in range(len(drawn)) if ''.join(drawn[i:i + len(SELECTED)]) == SELECTED]
    if ANCHOR is not None:
        assert ANCHOR in starts, 'the anchor glyph does not start the selected text'
        starts = [ANCHOR]
    assert len(starts) == 1, 'ambiguous display text needs a pointer position'
    return starts[0], starts[0] + len(SELECTED) - 1


def center(box):
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def compare(output, sidecar):
    result = {}
    for name, ours in (('pdf', output), ('sidecar', sidecar)):
        earlier = EARLIER / ('edited.pdf' if name == 'pdf' else 'edited.json')
        result[name] = dict(sha256=source_sha(ours),
                            pr81_sha256=source_sha(earlier) if earlier.exists() else None,
                            equal=earlier.exists() and source_sha(earlier) == source_sha(ours))
    return result


def run_session(root):
    from examples.adjacent_span_session import AdjacentEditSession, review_text
    started = time.monotonic()
    session = AdjacentEditSession(SOURCE, work_dir=root / 'work')
    first, last = occurrence(session)
    chosen = session.select(center(session.glyphs[first]['bbox']), center(session.glyphs[last]['bbox']))
    assert chosen['text'] == SELECTED
    session.set_replacement(REPLACEMENT)
    shown = session.propose()
    proposed = time.monotonic()
    session.accept()
    accepted = time.monotonic()
    saved = session.save(root / 'edited.pdf', root / 'edited.json')
    finished = time.monotonic()
    (root / 'review.txt').write_text('\n'.join(review_text(shown)) + '\n', encoding='utf-8')
    return dict(selection=chosen['glyph_ids'], selected_text=chosen['text'], review=shown, saved=saved,
                elapsed_seconds=dict(propose=proposed - started, accept=accepted - proposed, save=finished - accepted))


def run_gui(root):
    import tkinter as tk
    from examples.adjacent_span_gui import App, ZOOM

    def pump(app, until, limit=180):
        stop = time.monotonic() + limit
        while time.monotonic() < stop:
            app.root.update()
            if until():
                return
            time.sleep(.05)
        raise AssertionError('GUI did not finish in time')

    def shot(name):
        subprocess.run(['import', '-window', 'root', str(root / (name + '.png'))], check=True)

    def states(app):
        return {name: str(getattr(app, name).cget('state')) for name in
                ('open_button', 'propose_button', 'accept_button', 'save_button', 'entry')}

    window = tk.Tk()
    window.geometry('1500x900+0+0')
    app = App(window, str(SOURCE), choose_outputs=lambda source: (root / 'edited.pdf', root / 'edited.json'))
    pump(app, lambda: window.winfo_ismapped())
    log = dict(initial=states(app))
    # A press/release on empty paper selects nothing and says why.
    app.canvas.event_generate('<ButtonPress-1>', x=20, y=20)
    app.canvas.event_generate('<ButtonRelease-1>', x=20, y=20)
    pump(app, lambda: True)
    assert app.session.selection is None
    log['empty_click'] = dict(status=app.status.cget('text'), states=states(app))
    first, last = occurrence(app.session)
    press = [c * ZOOM for c in center(app.session.glyphs[first]['bbox'])]
    release = [c * ZOOM for c in center(app.session.glyphs[last]['bbox'])]
    app.canvas.event_generate('<ButtonPress-1>', x=round(press[0]), y=round(press[1]))
    app.canvas.event_generate('<ButtonRelease-1>', x=round(release[0]), y=round(release[1]))
    pump(app, lambda: True)
    log['selected_label'] = app.selected.cget('text')
    assert app.session.selected_text == SELECTED, log['selected_label']
    app.entry.insert(0, REPLACEMENT)
    pump(app, lambda: True)
    log['before_proposal'] = states(app)
    shot('1-selected')
    started = time.monotonic()
    app.propose_button.invoke()
    log['while_proposing'] = states(app)
    # A disabled 「PDFを開く」 ignores the press; an enabled one would block on its dialog here.
    log['open_pressed_while_proposing'] = str(app.open_button.invoke())
    pump(app, lambda: app.busy is None)
    proposed = time.monotonic()
    assert app.review is not None and app.review['status'] == 'proposed', app.status.cget('text')
    log['after_proposal'] = states(app)
    log['review_text'] = app.text.get('1.0', 'end').strip().splitlines()
    shot('2-proposal')
    app.accept_button.invoke()
    pump(app, lambda: app.busy is None)
    accepted = time.monotonic()
    assert app.session.acceptance is not None, app.status.cget('text')
    log['after_acceptance'] = states(app)
    # Editing the replacement after acceptance discards the acceptance.
    app.entry.insert('end', 'X')
    pump(app, lambda: True)
    assert app.session.acceptance is None and app.session.proposal is None
    log['after_changed_replacement'] = states(app)
    app.entry.delete(len(REPLACEMENT), 'end')
    pump(app, lambda: True)
    app.propose_button.invoke()
    pump(app, lambda: app.busy is None)
    app.accept_button.invoke()
    pump(app, lambda: app.busy is None)
    assert app.session.acceptance is not None, app.status.cget('text')
    accepted = time.monotonic()
    app.save_button.invoke()
    pump(app, lambda: app.busy is None)
    finished = time.monotonic()
    saved = app.session.result
    assert saved is not None and saved['reopen'] == 'restored', app.status.cget('text')
    log['after_save'] = states(app)
    log['status'] = app.status.cget('text')
    log['shown'] = str(app.shown)
    shot('3-saved')
    window.destroy()
    return dict(selection=app.session.selection['glyph_ids'], selected_text=app.session.selected_text,
                saved=saved, gui=log,
                elapsed_seconds=dict(propose=proposed - started, accept=accepted - proposed, save=finished - accepted))


def main():
    global SELECTED, REPLACEMENT, ANCHOR
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('session', 'gui'), required=True)
    parser.add_argument('--root')
    parser.add_argument('--selected', default=SELECTED)
    parser.add_argument('--replacement', default=REPLACEMENT)
    parser.add_argument('--anchor', type=int)
    args = parser.parse_args()
    SELECTED, REPLACEMENT, ANCHOR = args.selected, args.replacement, args.anchor
    root = Path(args.root or 'tmp/osaka-adjacent-gui-' + args.mode)
    root.mkdir(parents=True, exist_ok=False)
    assert source_sha(SOURCE) == SOURCE_SHA
    value = run_session(root) if args.mode == 'session' else run_gui(root)
    assert source_sha(SOURCE) == SOURCE_SHA
    saved = value['saved']
    value.update(mode=args.mode, source_sha256=SOURCE_SHA, comparison=compare(Path(saved['output']), Path(saved['sidecar'])))
    (root / 'summary.json').write_text(json.dumps(value, ensure_ascii=False, indent=2, default=list) + '\n', encoding='utf-8')
    print(json.dumps({k: value[k] for k in ('mode', 'selection', 'selected_text', 'comparison', 'elapsed_seconds')},
                     ensure_ascii=False, indent=2))
    print('reopen:', saved['reopen'], 'after:', saved['after'], 'follower_dx:', saved['follower_dx'])


if __name__ == '__main__':
    main()
