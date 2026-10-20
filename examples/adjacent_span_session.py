"""UI-independent state for the adjacent-Span editing GUI PoC.

The session only connects user gestures to the existing contracts: a drag
between two glyphs becomes a ``make_selection`` bound to the source SHA and
page observation; proposing, accepting and saving call
``propose_edit_intent``, ``accept_edit_intent`` and ``edit_edit_intent``
unchanged (single-Span or adjacent-Span route, chosen by the engine). It
never corrects owners, routes, rectangles,
fonts or boundaries. Changing the selection or replacement discards the
proposal and acceptance; a proposal or acceptance computed for older inputs
is discarded. One step runs at a time, and while a save runs (it publishes
files) the selection and replacement cannot change.

When a proposal is refused because the original embedded font lacks
characters, the session records them (``missing``). Only then may the user
choose a local font file (``set_substitute``); it is passed to the engine as
an explicit ``relation='substituted'`` approval for those characters only.
Changing the selection or replacement also discards that choice.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import threading

import pymupdf

from pdfeditor.edit_intent import accept_edit_intent, edit_edit_intent, open_edit_result, propose_edit_intent
from pdfeditor.backend import PdfError
from pdfeditor.selection import inspect_selection_source, make_selection, source_sha


class Refused(PdfError):
    """A refusal raised by the session before or instead of an engine call."""

    def __init__(self, message, *, missing=None):
        super().__init__(message)
        self.missing = missing


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class AdjacentEditSession:
    """One source PDF page, one selection and replacement, one proposal/acceptance/save."""

    def __init__(self, source, *, page=1, work_dir=None):
        self.source = Path(source).resolve()
        if not self.source.is_file():
            raise Refused('PDF not found: %s' % self.source)
        self.page = page
        self.sha256 = source_sha(self.source)
        with pymupdf.open(self.source) as document:
            if not 1 <= page <= len(document):
                raise Refused('page %d is outside the document' % page)
            current = document[page - 1]
            # Screen and glyph coordinates share the unrotated page space only.
            if current.rotation or current.cropbox != current.mediabox:
                raise Refused('rotated or cropped pages are not supported by this PoC')
            self.size = (current.rect.width, current.rect.height)
        observation = inspect_selection_source(self.source, page)
        self.glyphs = [dict(id=g['source_order'], text=g['text'],
                            bbox=(g['bbox']['x0'], g['bbox']['y0'], g['bbox']['x1'], g['bbox']['y1']))
                       for g in observation['glyphs'] if g['source_order'] >= 0]
        if [g['id'] for g in self.glyphs] != list(range(len(self.glyphs))):
            raise Refused('the page observation does not number glyphs in content order')
        self.work_dir = Path(work_dir) if work_dir is not None else Path(tempfile.mkdtemp(prefix='pdfengine-gui-'))
        self._lock = threading.Lock()
        self.generation = 0
        self.selection = self.selected_text = None
        self.replacement = ''
        self.proposal = self.acceptance = self.result = None
        # Characters the original font lacks (from a refused proposal) and
        # the user's explicit substitute choice for exactly those characters.
        self.missing = self.substitute = None
        self.running = None

    # -- coordinates and selection --------------------------------------------

    @staticmethod
    def to_pdf(x, y, zoom):
        """Screen pixel of a page rendered with Matrix(zoom, zoom) to a PDF point (top-left origin)."""
        return x / zoom, y / zoom

    @staticmethod
    def to_screen(rect, zoom):
        return tuple(v * zoom for v in rect)

    def glyph_at(self, x, y):
        """The single glyph whose box holds the PDF point; none or several is refused."""
        hits = [g['id'] for g in self.glyphs
                if g['bbox'][0] <= x <= g['bbox'][2] and g['bbox'][1] <= y <= g['bbox'][3]]
        if not hits:
            raise Refused('no glyph at this point')
        if len(hits) > 1:
            raise Refused('several glyphs overlap at this point (%s); select inside one glyph' %
                          ''.join(self.glyphs[i]['text'] for i in hits))
        return hits[0]

    def select(self, start, end):
        """Select the content-order glyph run from the glyph at ``start`` to the glyph at ``end`` (PDF points).

        Every new gesture first discards the previous selection, proposal and acceptance.
        """
        with self._lock:
            self._require_editable_inputs()
            self._invalidate()
            self.selection = self.selected_text = None
            self.missing = self.substitute = None
        first, last = sorted((self.glyph_at(*start), self.glyph_at(*end)))
        ids = list(range(first, last + 1))
        selection = make_selection(self.source, self.page, glyph_ids=ids)
        text = ''.join(self.glyphs[i]['text'] for i in ids)
        with self._lock:
            self.selection, self.selected_text = selection, text
        return dict(glyph_ids=ids, text=text, boxes=[self.glyphs[i]['bbox'] for i in ids])

    def set_replacement(self, text):
        if text != self.replacement:
            with self._lock:
                self._require_editable_inputs()
                self._invalidate()
                self.replacement = text
                self.missing = self.substitute = None

    def set_substitute(self, path):
        """The user's explicit choice of a local font for the reported missing characters."""
        path = Path(path).resolve()
        if not path.is_file():
            raise Refused('font file not found: %s' % path)
        sha = _sha(path)
        with self._lock:
            self._require_editable_inputs()
            if self.running is not None:
                raise Refused('%s is still running' % self.running)
            if not self.missing:
                raise Refused('a substitute font is chosen only for characters the original font lacks')
            self._invalidate()
            self.substitute = dict(path=str(path), sha256=sha, font_index=0, relation='substituted')
        return dict(self.substitute, missing=self.missing)

    def _require_editable_inputs(self):
        if self.running == 'save':
            raise Refused('saving is in progress; the selection and replacement stay fixed until it finishes')

    def _invalidate(self):
        self.generation += 1
        self.proposal = self.acceptance = self.result = None

    def _begin(self, step, *required):
        """Start one step; return the generation and the inputs it works on, read together."""
        with self._lock:
            if self.running is not None:
                raise Refused('%s is still running' % self.running)
            for name in required:
                if getattr(self, name) is None:
                    raise Refused({'selection': 'select glyphs first', 'proposal': 'check the edit proposal first',
                                   'acceptance': 'accept the proposal first'}[name])
            self.running = step
            return self.generation, dict(selection=self.selection, replacement=self.replacement,
                                         proposal=self.proposal, acceptance=self.acceptance,
                                         substitute=self.substitute)

    def _end(self):
        with self._lock:
            self.running = None

    def _commit(self, token, **values):
        with self._lock:
            if token != self.generation:
                raise Refused('the selection or replacement changed while working; the result was discarded')
            for name, value in values.items():
                setattr(self, name, value)

    def _check_source(self):
        if source_sha(self.source) != self.sha256:
            raise Refused('the PDF changed on disk; open it again')

    # -- proposal, acceptance, save -------------------------------------------

    def propose(self):
        token, inputs = self._begin('propose', 'selection')
        try:
            if not inputs['replacement']:
                raise Refused('enter the replacement text first')
            self._check_source()
            options = dict(substitute=inputs['substitute']) if inputs['substitute'] else {}
            proposal = propose_edit_intent(self.source, inputs['selection'], inputs['replacement'],
                                           work_dir=self.work_dir, **options)
            if proposal['status'] == 'refused':
                missing = proposal['routes'].get('single-span', {}).get('missing')
                if missing and not inputs['substitute']:
                    self._commit(token, missing=missing)
                raise Refused('the engine refused this edit: ' + proposal['reason'], missing=missing)
            self._commit(token, proposal=proposal)
            return review(proposal)
        finally:
            self._end()

    def accept(self, option=None):
        token, inputs = self._begin('accept', 'proposal')
        try:
            self._check_source()
            acceptance = accept_edit_intent(self.source, inputs['proposal'], work_dir=self.work_dir, option=option)
            self._commit(token, acceptance=acceptance)
            return acceptance
        finally:
            self._end()

    def save(self, output, sidecar):
        """Publish the accepted edit; the inputs it was accepted for cannot change until it returns."""
        token, inputs = self._begin('save', 'acceptance')
        try:
            output, sidecar = Path(output).resolve(), Path(sidecar).resolve()
            if output == sidecar or self.source in (output, sidecar):
                raise Refused('the output PDF, sidecar and source must be three different files')
            for path in (output, sidecar):
                if path.exists():
                    raise Refused('%s already exists; choose a new file' % path)
            self._check_source()
            result = edit_edit_intent(self.source, inputs['acceptance'], output, sidecar, work_dir=self.work_dir)
            reopened = open_edit_result(output, sidecar)
            substitutes = result.get('state', {}).get('font_binding', {}).get('substitutes', {})
            value = dict(route=result['route'], after=result['after'], follower_dx=result.get('follower_dx'),
                         provenance=result.get('provenance'),
                         substitutes={a: dict(family=r['family'], characters=''.join(r['glyphs']))
                                      for a, r in substitutes.items()},
                         output=str(output), sidecar=str(sidecar), output_sha256=_sha(output),
                         sidecar_sha256=_sha(sidecar), reopen=reopened['status'], reopen_reason=reopened.get('reason'))
            self._commit(token, result=value)
            return value
        finally:
            self._end()


ROUTES = {'single-span': '単一Span', 'adjacent-span': '隣接Span'}


def review(proposal):
    """What the user must see before accepting, plus PDF-space overlays."""
    found = proposal['normalized']
    base = dict(status=proposal['status'], route=proposal['route'], selected_text=found['selected_text'],
                replacement=found['replacement'], changed_text=found['changed_text'], inserted=found['inserted'],
                rule=found['rule'], selection_owners=[o['mcid'] for o in found['selection_owners']])
    if proposal['route'] == 'adjacent-span':
        return dict(_adjacent_review(proposal['routes']['adjacent-span']['proposal']), **base)
    route, owner = proposal['routes']['single-span'], found['owner']
    r = route['region']
    font = route['font'] or {}
    confirm = ['owner and changed characters', 'owner rectangle (its own glyph extent)', 'pinned original font program']
    substitute = route.get('substitute')
    if substitute is not None:
        substitute = dict(substitute, missing=font.get('missing'))
        confirm.append('substitute font for the missing characters only (appearance may differ)')
    return dict(base, first=dict(owner=owner['owner'], mcid=owner['mcid'], text=owner['text']), second=None,
                logical_before=owner['text'], logical_after=route['after'], options=[], chosen=None, candidates=[],
                follower=dict(mcid=None, owner=None, approved=False, note='single-Span route'),
                font=dict(font_xref=font.get('font_xref'), family=font.get('family'),
                          program_sha256=font.get('program_sha256')),
                substitute=substitute, column=None, regions=[r], follower_region=None,
                requires_confirmation=confirm,
                overlays=[dict(kind='first', rect=(r['x0'], r['y0'], r['x1'], r['y1']))])


def _adjacent_review(proposal):
    """The adjacent-Span part of a review (an ``adjacent_intent`` proposal)."""
    evidence = proposal['regions']['evidence']
    owners = proposal['owners']
    options = []
    for index in proposal['options']:
        c = proposal['candidates'][index]
        options.append(dict(index=index, boundary=c['boundary'], relocated=c['relocated'], after=c['after'],
                            follower_dx=c['follower_dx'], natural_break=c['natural_break'],
                            widths=c['widths'], width_limits=c['width_limits']))
    first, second = proposal['regions']['owners']
    overlays = [dict(kind='first', rect=(first['x0'], first['y0'], first['x1'], first['y1'])),
                dict(kind='second', rect=(second['x0'], second['y0'], second['x1'], second['y1']))]
    if proposal['regions']['follower']:
        f = proposal['regions']['follower']
        overlays.append(dict(kind='follower', rect=(f['x0'], f['y0'], f['x1'], f['y1'])))
    low, high = evidence['wrap_window']
    overlays.append(dict(kind='wrap-window', rect=(low, first['y0'], high, first['y1'])))
    for s in evidence['supports']:
        overlays.append(dict(kind='support', rect=(s['right'] - 1, s['baseline'] - 10, s['right'] + 1, s['baseline'] + 2),
                             label=s['unicode']))
    return dict(status=proposal['status'], selected_text=proposal['selected_text'], replacement=proposal['replacement'],
                first=owners['first'], second=owners['second'], parent=owners['parent'],
                logical_before=proposal['logical_before'], logical_after=proposal['logical_after'],
                original_boundary=proposal['original_boundary'], options=options, chosen=proposal['chosen'],
                candidates=[dict(relocated_count=c['relocated_count'], status=c['status'], reason=c.get('reason'))
                            for c in proposal['candidates']],
                follower={k: proposal['follower'].get(k) for k in ('mcid', 'owner', 'approved', 'note')},
                font=dict(font_xref=proposal['font']['font_xref'], family=proposal['font']['family'],
                          program_sha256=proposal['font']['program_sha256']),
                column=dict(basis=evidence['basis'], right=evidence['column_right'], wrap_window=evidence['wrap_window'],
                            next_character=evidence['next_character'], supports=evidence['supports'],
                            ignored=evidence['ignored_beyond_window']),
                regions=proposal['regions']['owners'], follower_region=proposal['regions']['follower'],
                requires_confirmation=proposal['requires_confirmation'], overlays=overlays)


def review_text(value):
    """Plain Japanese lines for the review pane."""
    def owner(o):
        return 'MCID%s（構造要素 %d）「%s」' % (o['mcid'], o['owner']['xref'], o['text'])
    route = ROUTES[value['route']]
    lines = ['状態: ' + ('提案（経路: %s）' % route if value['status'] == 'proposed'
                       else '境界候補が複数あります（経路: %s）。1つ選んでください' % route),
             '選択: 「%s」 → 置換: 「%s」' % (value['selected_text'], value['replacement']),
             '実際の変更: 「%s」 → 「%s」（%s）' % (value['changed_text'], value['inserted'], value['rule']),
             '選択範囲のowner: ' + '、'.join('MCID%s' % m for m in value['selection_owners']),
             '編集対象: ' + owner(value['first'])]
    if value['second'] is not None:
        lines.append('後続owner: ' + owner(value['second']))
    lines += ['編集前: ' + value['logical_before'], '編集後: ' + value['logical_after']]
    if value['route'] == 'adjacent-span':
        lines.append('元の境界: %d文字目' % value['original_boundary'])
    for o in value['options']:
        mark = '★' if o['index'] == value['chosen'] else '・'
        dx = '%.2fpt' % o['follower_dx'] if o['follower_dx'] is not None else 'なし'
        lines.append('%s候補%d: 境界%d、再配置「%s」、follower移動 %s、自然な改行位置: %s' % (
            mark, o['index'], o['boundary'], o['relocated'], dx, 'はい' if o['natural_break'] else 'いいえ'))
        lines.append('    先行: %s' % o['after'][0])
        lines.append('    後続: %s' % o['after'][1])
    for c in value['candidates']:
        if c['status'] != 'qualified':
            lines.append('不採用: 再配置%d文字 — %s' % (c['relocated_count'], c['reason']))
    f = value['follower']
    lines.append('空白follower: ' + ('MCID%s を位置のみ移動' % f['mcid'] if f['approved'] else 'なし（%s）' % f['note']))
    if value['font']['program_sha256']:
        lines.append('%s: xref %d %s（program SHA-256 %s…）' % (
            '元フォント' if value.get('substitute') else 'フォント', value['font']['font_xref'], value['font']['family'],
            value['font']['program_sha256'][:16]))
    else:
        lines.append('フォント: 新しい文字はありません（元のglyphだけ）')
    sub = value.get('substitute')
    if sub:
        lines += ['元フォントにない文字: 「%s」' % sub['missing'],
                  '代替フォント: %s（%s、face %d、SHA-256 %s…、relation=%s）' % (
                      sub['family'], sub['file'], sub['font_index'], sub['sha256'][:16], sub['relation']),
                  '代替フォントで描く文字: 「%s」（%d glyph。ほかの新しい文字は元フォント）' % (sub['characters'], sub['new']),
                  '注意: 代替フォントの字形は元のフォントと見た目が異なる場合があります。縮小や枠の拡張はしません。']
    column = value['column'] or dict(right=None, supports=[], ignored=[])
    if column['right'] is not None:
        lines.append('カラム右端: %.2fpt（根拠: %s、改行窓 %.2f〜%.2fpt、次の文字「%s」）' % (
            column['right'], column['basis'], column['wrap_window'][0], column['wrap_window'][1],
            column['next_character']))
    for s in column['supports']:
        lines.append('    裏付けの行末: 「%s」 右端 %.2fpt（baseline %.2f）' % (s['unicode'], s['right'], s['baseline']))
    for s in column['ignored']:
        lines.append('    窓の外として無視: 「%s」 右端 %.2fpt' % (s['unicode'], s['right']))
    for name, r in zip(('先行', '後続') if value['second'] is not None else ('編集',), value['regions']):
        lines.append('%s矩形: (%.2f, %.2f)–(%.2f, %.2f)' % (name, r['x0'], r['y0'], r['x1'], r['y1']))
    if value['follower_region']:
        r = value['follower_region']
        lines.append('follower矩形: (%.2f, %.2f)–(%.2f, %.2f)' % (r['x0'], r['y0'], r['x1'], r['y1']))
    lines.append('確認事項: ' + '、'.join(value['requires_confirmation']))
    return lines
