"""Minimal Tk GUI PoC: select glyphs, review the adjacent-Span proposal, accept, save.

Run: python -m examples.adjacent_span_gui [PDF]
Drag from one glyph to another on page 1, type the replacement, press
「編集案を確認」, review the owners, boundary, follower, font and rectangles
(drawn over the page), press 「この内容で承認」 and then 「PDFを保存」. All
decisions are made by ``examples.adjacent_span_session`` and the engine.
"""
from __future__ import annotations

import base64
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import pymupdf

from examples.adjacent_span_session import AdjacentEditSession, review_text

ZOOM = 1.5
COLORS = {'selection': '#e6b800', 'first': '#1f77b4', 'second': '#2ca02c', 'follower': '#ff7f0e',
          'wrap-window': '#9467bd', 'support': '#d62728'}
LEGEND = {'first': '先行owner矩形', 'second': '後続owner矩形', 'follower': 'follower矩形',
          'wrap-window': '改行窓', 'support': 'カラム端の裏付け'}


def default_outputs(source):
    """Ask for the output PDF; the sidecar is the same name with .json."""
    path = filedialog.asksaveasfilename(title='保存先PDF', defaultextension='.pdf', confirmoverwrite=False,
                                        initialdir=str(Path(source).parent),
                                        initialfile=Path(source).stem + '-edited.pdf',
                                        filetypes=[('PDF', '*.pdf')])
    return (Path(path), Path(path).with_suffix('.json')) if path else None


class App:
    def __init__(self, root, path=None, *, choose_outputs=default_outputs):
        self.root, self.choose_outputs = root, choose_outputs
        self.session = self.review = None
        self.shown = None
        self.busy = None
        self.results = queue.Queue()
        self.press = None
        root.title('pdfengine 隣接Span編集 PoC')
        top = ttk.Frame(root); top.pack(fill='x')
        self.open_button = ttk.Button(top, text='PDFを開く', command=self.ask_open)
        self.open_button.pack(side='left')
        self.view_button = ttk.Button(top, text='生成PDFを表示', command=self.toggle_view, state='disabled')
        self.view_button.pack(side='left')
        self.path_label = ttk.Label(top, text='PDF未選択'); self.path_label.pack(side='left', padx=8)
        body = ttk.Frame(root); body.pack(fill='both', expand=True)
        holder = ttk.Frame(body); holder.pack(side='left', fill='both', expand=True)
        self.canvas = tk.Canvas(holder, width=900, height=800, background='#808080')
        yscroll = ttk.Scrollbar(holder, orient='vertical', command=self.canvas.yview)
        xscroll = ttk.Scrollbar(holder, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side='right', fill='y'); xscroll.pack(side='bottom', fill='x')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.bind('<ButtonPress-1>', self.on_press)
        self.canvas.bind('<ButtonRelease-1>', self.on_release)
        side = ttk.Frame(body, padding=6); side.pack(side='right', fill='y')
        ttk.Label(side, text='選択した文字列').pack(anchor='w')
        self.selected = ttk.Label(side, text='（未選択）', foreground='#1f3a93'); self.selected.pack(anchor='w')
        ttk.Label(side, text='置換後の文字列').pack(anchor='w', pady=(8, 0))
        self.replacement = tk.StringVar()
        self.replacement.trace_add('write', lambda *_: self.on_replacement())
        self.entry = ttk.Entry(side, textvariable=self.replacement, width=40); self.entry.pack(anchor='w')
        self.propose_button = ttk.Button(side, text='編集案を確認', command=self.propose)
        self.propose_button.pack(anchor='w', pady=4)
        self.text = tk.Text(side, width=70, height=30, wrap='char'); self.text.pack(fill='y', expand=True)
        self.options = ttk.Frame(side); self.options.pack(anchor='w')
        self.option = tk.IntVar(value=-1)
        buttons = ttk.Frame(side); buttons.pack(anchor='w', pady=4)
        self.accept_button = ttk.Button(buttons, text='この内容で承認', command=self.accept)
        self.accept_button.pack(side='left')
        self.save_button = ttk.Button(buttons, text='PDFを保存', command=self.save)
        self.save_button.pack(side='left', padx=4)
        self.status = ttk.Label(side, text='', wraplength=520); self.status.pack(anchor='w')
        legend = ttk.Frame(side); legend.pack(anchor='w', pady=(6, 0))
        for kind, label in LEGEND.items():
            tk.Label(legend, text='■ ' + label, foreground=COLORS[kind]).pack(side='left')
        if path:
            self.open(path)
        self.refresh()

    # -- documents ----------------------------------------------------------------

    def ask_open(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(filetypes=[('PDF', '*.pdf')])
        if path:
            self.open(path)

    def open(self, path):
        if self.busy:
            return
        try:
            self.session = AdjacentEditSession(path)
        except Exception as exc:
            self.session = None
            self.say('開けません: %s' % exc, error=True)
            return self.refresh()
        self.review = None
        self.replacement.set('')
        self.selected.configure(text='（未選択）')
        self.show(self.session.source)
        self.write([])
        self.say('1ページ目の文字をドラッグして選択してください。')
        self.refresh()

    def show(self, path):
        with pymupdf.open(path) as document:
            pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), alpha=False)
            self.image = tk.PhotoImage(data=base64.b64encode(pixmap.tobytes('png')))
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, image=self.image, anchor='nw', tags='page')
        self.canvas.configure(scrollregion=(0, 0, pixmap.width, pixmap.height))
        self.shown = Path(path).resolve()
        self.path_label.configure(text=('表示中（元PDF）: ' if self.showing_source() else '表示中（生成PDF）: ') + str(self.shown))
        self.draw()

    def toggle_view(self):
        result = self.session.result if self.session else None
        if result:
            self.show(self.session.source if self.shown == Path(result['output']) else result['output'])
            self.refresh()

    def showing_source(self):
        return self.session is not None and self.shown == self.session.source

    # -- drawing ----------------------------------------------------------------

    def rect(self, box, kind, *, fill=True, width=2):
        x0, y0, x1, y1 = AdjacentEditSession.to_screen(box, ZOOM)
        self.canvas.create_rectangle(x0, y0, x1, y1, outline=COLORS[kind], width=width, tags='overlay',
                                     fill=COLORS[kind] if fill else '', stipple='gray25' if fill else '')

    def draw(self):
        self.canvas.delete('overlay')
        if not self.showing_source():
            return
        if self.session.selection is not None:
            for i in self.session.selection['glyph_ids']:
                self.rect(self.session.glyphs[i]['bbox'], 'selection', width=1)
        if self.review is not None:
            for overlay in self.review['overlays']:
                self.rect(overlay['rect'], overlay['kind'], fill=overlay['kind'] in ('selection', 'support'))

    def write(self, lines):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('end', '\n'.join(lines))
        self.text.configure(state='disabled')

    def say(self, message, *, error=False):
        self.status.configure(text=message, foreground='#b00020' if error else '#202020')

    # -- user input -------------------------------------------------------------

    def on_press(self, event):
        self.press = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    def on_release(self, event):
        if self.busy or self.session is None or not self.showing_source() or self.press is None:
            return
        start = AdjacentEditSession.to_pdf(*self.press, ZOOM)
        end = AdjacentEditSession.to_pdf(self.canvas.canvasx(event.x), self.canvas.canvasy(event.y), ZOOM)
        self.press = None
        self.review = None
        try:
            chosen = self.session.select(start, end)
            self.selected.configure(text='「%s」（glyph %d〜%d）' % (chosen['text'], chosen['glyph_ids'][0],
                                                                  chosen['glyph_ids'][-1]))
            self.say('置換後の文字列を入力して「編集案を確認」を押してください。')
        except Exception as exc:
            self.selected.configure(text='（未選択）')
            self.say('選択できません: %s' % exc, error=True)
        self.clear_proposal()

    def on_replacement(self):
        if self.session is not None and not self.busy:
            if self.replacement.get() != self.session.replacement:
                self.session.set_replacement(self.replacement.get())
                self.review = None
                self.clear_proposal()

    def clear_proposal(self):
        for child in self.options.winfo_children():
            child.destroy()
        self.option.set(-1)
        self.write([])
        self.draw()
        self.refresh()

    # -- background work --------------------------------------------------------

    def run(self, label, work, done):
        if self.busy:
            return
        self.busy = label
        self.say('処理中: %s…（操作はできません）' % label)
        self.refresh()
        threading.Thread(target=lambda: self.results.put((done, *self._call(work))), daemon=True).start()
        self.root.after(100, self.poll)

    @staticmethod
    def _call(work):
        try:
            return work(), None
        except Exception as exc:
            return None, exc

    def poll(self):
        try:
            done, value, error = self.results.get_nowait()
        except queue.Empty:
            self.root.after(100, self.poll)
            return
        self.busy = None
        done(value, error)
        self.refresh()

    def propose(self):
        self.run('編集案を作成', self.session.propose, self.proposed)

    def proposed(self, value, error):
        if error is not None:
            self.review = None
            self.write(['編集案は作成されませんでした（編集は行いません）。', str(error)])
            self.say('拒否されました: %s' % error, error=True)
            return self.draw()
        self.review = value
        self.write(review_text(value))
        for child in self.options.winfo_children():
            child.destroy()
        for option in value['options']:
            ttk.Radiobutton(self.options, text='候補%d: 「%s」／「%s」' % (option['index'], option['after'][0][-8:],
                                                                    option['after'][1][:8]),
                            value=option['index'], variable=self.option, command=self.refresh).pack(anchor='w')
        self.option.set(value['chosen'] if value['chosen'] is not None else -1)
        self.say('内容と矩形を確認し、よければ「この内容で承認」を押してください。')
        self.draw()

    def accept(self):
        option = self.option.get()
        if self.review['status'] == 'proposed':
            option = None
        self.run('承認', lambda: self.session.accept(option), self.accepted)

    def accepted(self, value, error):
        if error is not None:
            self.say('承認できません: %s' % error, error=True)
        else:
            self.say('承認しました。「PDFを保存」で出力します。')

    def save(self):
        chosen = self.choose_outputs(self.session.source)
        if chosen:
            output, sidecar = chosen
            self.run('保存', lambda: self.session.save(output, sidecar), self.saved)

    def saved(self, value, error):
        if error is not None:
            self.say('保存できません: %s' % error, error=True)
            return
        self.show(value['output'])
        self.write(review_text(self.review) + ['', '保存: ' + value['output'], 'sidecar: ' + value['sidecar'],
                   'PDF SHA-256: ' + value['output_sha256'], 'sidecar SHA-256: ' + value['sidecar_sha256'],
                   '再open: ' + value['reopen'] + ('（%s）' % value['reopen_reason'] if value['reopen_reason'] else '')])
        self.say('保存しました。再openの結果: %s' % value['reopen'], error=value['reopen'] != 'restored')

    # -- widget state -----------------------------------------------------------

    def refresh(self):
        s = self.session
        idle = not self.busy and s is not None
        self.open_button.configure(state='disabled' if self.busy else 'normal')
        ready = idle and self.showing_source()
        self.entry.configure(state='normal' if ready else 'disabled')
        self.propose_button.configure(state='normal' if ready and s.selection is not None and s.replacement else 'disabled')
        choice = self.review is not None and (self.review['status'] == 'proposed' or self.option.get() >= 0)
        self.accept_button.configure(state='normal' if ready and s.proposal is not None and choice
                                     and s.acceptance is None else 'disabled')
        self.save_button.configure(state='normal' if ready and s.acceptance is not None and s.result is None else 'disabled')
        for child in self.options.winfo_children():
            child.configure(state='normal' if ready and s.acceptance is None else 'disabled')
        self.view_button.configure(state='normal' if idle and s.result else 'disabled',
                                   text='元PDFを表示' if s and s.result and not self.showing_source() else '生成PDFを表示')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = tk.Tk()
    App(root, argv[0] if argv else None)
    root.mainloop()


if __name__ == '__main__':
    main()
