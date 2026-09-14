"""Plan, mutate, save once, verify once.

A transaction collects plans for one source revision. Each plan proves its own
source facts (no-op replay, removal, counterfactual paint provenance) and
declares byte mutations, the glyphs it consumes or moves, the paints it
changes and the page area it may alter. The transaction checks the planned
final state as a whole, applies every mutation to each page program together,
writes one PDF, verifies that PDF once against all plans, and exposes the
identity map from the source revision to the saved one.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from pathlib import Path

import pymupdf

from .backend import PdfError
from .composition import _observations, _pixels_equal, _require_removal, _signature
from .content_stream import ContentPage
from .mutation import IdentityMap, MutationProgram
from .paint_provenance import _load_catalog, interpreted_paints
from .pdf_save import font_fingerprints, publish_program
from .replay import compare_glyphs, ensure_destination
from .selection import source_sha


TEXT_KINDS = ('fill-text', 'stroke-text', 'ignore-text')


def _nontext(events):
    from .elements import _paint_value
    return [_paint_value(e) for e in events if e['kind'] not in TEXT_KINDS]


def _shifted_glyph(glyph, dx, dy):
    value = dict(glyph)
    value['origin'] = [glyph['origin'][0] + dx, glyph['origin'][1] + dy]
    a, b, c, d = glyph['bbox']
    value['bbox'] = [a + dx, b + dy, c + dx, d + dy]
    return value


class Plan:
    """What one editing operation contributes to a page transaction.

    ``mutations`` are the byte replacements; ``removal_mutations`` describe the
    same operation with only removals, for a removal checkpoint. ``consumed``
    are source glyph IDs that disappear, ``moved`` maps glyph IDs to their
    translation, ``paint_changes`` maps interpreted paint indices to their
    planned replacements (``None`` removes one), ``new_glyphs`` are expected
    generated glyphs in stream order, ``affected`` is the area whose pixels may
    change and ``final_rects`` are the areas the plan occupies afterwards.
    """
    kind = 'plan'

    def __init__(self, owner=None):
        self.owner = owner
        self.page = None
        self.mutations = []
        self.removal_mutations = []
        self.consumed = set()
        self.moved = {}
        self.paint_changes = {}
        self.planned_paths = {}
        self.path_anchors = {}
        self.consumed_paths = set()
        self.new_glyphs = []
        self.affected = None
        self.final_rects = []
        self.background_paths = set()
        self.covering_paths = set()
        self.font_builders = {}
        self.font_subsets = {}

    def check(self, page, *, exclude_glyphs, paint_changes):
        """Final-state obstacle checks; other plans' changes are already applied."""

    def exempt(self, other):
        """A plan may be covered by paint it declares as its own background.

        The covering plan moves or expands that paint; the covered plan's own
        obstacle check has already proven containment in the final geometry.
        """
        return bool(other.covering_paths & self.background_paths)

    def report(self, result):
        return {}

    def close(self):
        pass


class PageTransaction:
    def __init__(self, transaction, number):
        if type(number) is not int or not 1 <= number <= transaction.page_count:
            raise PdfError('transaction page is outside the document')
        self.transaction = transaction
        self.number = number
        self.content = ContentPage(transaction.source, number)
        if self.content.page.rotation:
            raise PdfError('transactions require unrotated page coordinates')
        self.program = MutationProgram(self.content.streams[-self.content.page.xref])
        self.plans = []
        self.reserved_aliases = set()
        self._observation = self._glyphs = self._catalog = None

    @property
    def source(self):
        return self.transaction.source

    @property
    def observation(self):
        if self._observation is None:
            self._observation = interpreted_paints(self.source, self.number)
        return self._observation

    @property
    def glyphs(self):
        if self._glyphs is None:
            self._glyphs = _observations(self.content.page)
        return self._glyphs

    @property
    def catalog(self):
        if self._catalog is None:
            self._catalog = _load_catalog(self.source, self.number)[0]
        return self._catalog

    def reserve_font_alias(self, prefix):
        existing = self.content.pdf_page['/Resources'].get_object().get('/Font', {})
        n = 1
        while f'/{prefix}{n}' in existing or f'/{prefix}{n}' in self.reserved_aliases:
            n += 1
        alias = f'/{prefix}{n}'
        self.reserved_aliases.add(alias)
        return alias

    def add(self, plan):
        if plan.page is not None:
            raise PdfError('a plan belongs to one page transaction')
        for mutation in plan.mutations:
            self.program.add(mutation)
        plan.page = self
        self.plans.append(plan)
        return plan

    def _merged(self):
        consumed, moved, changes = set(), {}, {}
        for plan in self.plans:
            if consumed & plan.consumed or set(moved) & plan.consumed or consumed & set(plan.moved) or set(moved) & set(plan.moved):
                raise PdfError('two plans claim the same source glyph occurrence')
            if set(changes) & set(plan.paint_changes):
                raise PdfError('two plans change the same interpreted paint')
            consumed |= plan.consumed
            moved.update(plan.moved)
            changes.update(plan.paint_changes)
        return consumed, moved, changes

    def check(self):
        consumed, moved, changes = self._merged()
        for plan in self.plans:
            others = (consumed | set(moved)) - plan.consumed - set(plan.moved)
            foreign = {i: v for i, v in changes.items() if i not in plan.paint_changes}
            plan.check(self, exclude_glyphs=frozenset(others), paint_changes=foreign)
        for a, b in combinations(self.plans, 2):
            if a.exempt(b) or b.exempt(a):
                continue
            for first in a.final_rects:
                for second in b.final_rects:
                    if first.intersects(second, .001):
                        raise PdfError('planned elements overlap in the final page state')

    def expected_glyphs(self):
        consumed, moved, _ = self._merged()
        kept = []
        for index, glyph in enumerate(self.glyphs):
            if index in consumed:
                continue
            if index in moved:
                dx, dy = moved[index]
                kept.append(_shifted_glyph(glyph, dx, dy))
            else:
                kept.append(glyph)
        return kept

    def expected_paints(self):
        _, _, changes = self._merged()
        result = []
        for index, event in enumerate(self.observation['events']):
            if index in changes:
                result.extend({k: v for k, v in value.items() if k != 'seqno'} for value in (changes[index] or []))
            else:
                result.append(event)
        return result

    def masks(self):
        return [plan.affected for plan in self.plans if plan.affected is not None]

    def font_builders(self):
        result = {}
        for plan in self.plans:
            for alias, builder in plan.font_builders.items():
                if alias in result:
                    raise PdfError('two plans add the same font alias')
                result[alias] = builder
        return result

    def close(self):
        for plan in self.plans:
            plan.close()
        self.content.close()


class TransactionResult:
    def __init__(self, transaction, output):
        self.source = transaction.source
        self.output = Path(output)
        self.source_sha256 = transaction.source_sha256
        self.output_sha256 = source_sha(output)
        self.pages = {}
        self._identities = {}
        self.byte_edits = {number: page.program.edits() for number, page in transaction.pages.items()}
        for number, page in transaction.pages.items():
            self.pages[number] = page

    def plans(self, number):
        return self.pages[number].plans if number in self.pages else []

    def identity(self, number) -> IdentityMap:
        """Identity map for any page; pages outside the transaction have no mutations."""
        if number not in self._identities:
            page = self.pages.get(number)
            if page is None:
                self._identities[number] = IdentityMap.from_edits(self.source, self.output, number, [])
            else:
                planned, anchors, consumed = {}, {}, set()
                for plan in page.plans:
                    planned.update(plan.planned_paths)
                    anchors.update(plan.path_anchors)
                    consumed |= plan.consumed_paths
                self._identities[number] = IdentityMap(self.source, self.output, page.content,
                    ContentPage(self.output, number), page.program,
                    path_anchors=anchors, consumed_paths=consumed, planned_paints=planned)
        return self._identities[number]

    def mutation_map(self):
        """Persisted identity record per page: byte edits plus complete mutation records."""
        return {number: dict(byte_edits=edits, mutations=self.pages[number].program.records())
                for number, edits in self.byte_edits.items()}

    def close(self):
        for identity in self._identities.values():
            identity.close()
        self._identities = {}


class Transaction:
    """One source revision, one set of plans, one saved output."""

    def __init__(self, source):
        self.source = Path(source).resolve()
        self.source_sha256 = source_sha(self.source)
        self.document = pymupdf.open(self.source)
        if self.document.needs_pass:
            raise PdfError('password required')
        self.page_count = len(self.document)
        self.pages = {}
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def page(self, number) -> PageTransaction:
        if number not in self.pages:
            self.pages[number] = PageTransaction(self, number)
        return self.pages[number]

    def close(self):
        for page in self.pages.values():
            page.close()
        self.pages = {}
        if self.document is not None:
            self.document.close()
            self.document = None

    def _verify(self, document, expected_kept, expected_new, expected_paints, masks, old_fonts, added_fonts):
        if (len(document) != self.page_count or document.permissions != self.document.permissions
                or document.metadata.get('encryption') != self.document.metadata.get('encryption')):
            raise PdfError('transaction changed page count or security')
        for number, page in self.pages.items():
            pno = number - 1
            remaining = defaultdict(list)
            for glyph in expected_kept[number]:
                remaining[_signature(glyph)].append(glyph)
            new, kept = [], []
            for glyph in _observations(document[pno]):
                candidates = remaining[_signature(glyph)]
                match = next((i for i, old in enumerate(candidates) if compare_glyphs([old], [glyph])['passed']), None)
                if match is None:
                    new.append(glyph)
                else:
                    candidates.pop(match)
                    kept.append(glyph)
            if any(remaining.values()) or not compare_glyphs(expected_kept[number], kept)['passed']:
                raise PdfError('transaction changed, removed or moved glyphs outside its plans')
            cursor = 0
            for plan in expected_new[number]:
                chunk = new[cursor:cursor + len(plan.new_glyphs)]
                cursor += len(plan.new_glyphs)
                if ''.join(g['unicode'] for g in chunk) != ''.join(g['unicode'] for g in plan.new_glyphs):
                    raise PdfError('saved generated Unicode differs from the line plan')
                painted = [g for g in chunk if g['glyph_id'] >= 0]
                if len(painted) != len(plan.new_glyphs):
                    raise PdfError('saved generated glyph count differs from the line plan')
                for actual, wanted in zip(painted, plan.new_glyphs):
                    if (actual['glyph_id'] != wanted['glyph_id'] or abs(actual['size'] - wanted['trace_size']) > .002
                            or actual['paint_type'] != 0 or actual['opacity'] != 1 or actual['color'] != wanted['color']
                            or max(abs(a - b) for a, b in zip(actual['origin'], wanted['origin'])) > .002):
                        raise PdfError('saved generated glyph ID, origin or paint differs from its plan')
                    if wanted.get('font') is not None and actual['font'] != wanted['font']:
                        raise PdfError('retained text no longer uses its original font')
            if cursor != len(new):
                raise PdfError('transaction produced glyphs outside its plans')
            aliases = {alias[1:] for alias in added_fonts[number]}
            current = font_fingerprints(document, pno)
            if [f for f in current if f[0][3] not in aliases] != old_fonts[number]:
                raise PdfError('an existing font resource changed')
            for alias, digest_value in added_fonts[number].items():
                added = [f for f in current if f[0][3] == alias[1:]]
                if len(added) != 1 or added[0][1] != digest_value:
                    raise PdfError('new font differs from the verified subset')
            observed = interpreted_paints(document.tobytes(), number)
            if observed['errors']:
                raise PdfError(observed['errors'][0])
            from .elements import _close
            if not _close(_nontext(expected_paints[number]), _nontext(observed['events'])):
                raise PdfError('saved non-text paint differs from the transaction plan')
        for index in range(len(document)):
            if not _pixels_equal(self.document[index], document[index], masks.get(index + 1) or None):
                raise PdfError('transaction changed pixels outside its planned areas')

    def commit(self, output, *, removal_output=None) -> TransactionResult:
        if self.committed:
            raise PdfError('a transaction commits once')
        if not self.pages or not any(page.plans for page in self.pages.values()):
            raise PdfError('transaction has no plans')
        output = ensure_destination(output, self.source)
        if removal_output is not None:
            removal_output = ensure_destination(removal_output, self.source)
            if removal_output == output:
                raise PdfError('output and removal checkpoint must be distinct')
        if source_sha(self.source) != self.source_sha256:
            raise PdfError('source revision changed while planning')
        programs, builders, kept, new, paints, masks, old_fonts, added = {}, {}, {}, {}, {}, {}, {}, {}
        for number, page in self.pages.items():
            page.check()
            programs[number - 1] = page.program.apply()
            builders[number - 1] = page.font_builders()
            kept[number] = page.expected_glyphs()
            new[number] = sorted((p for p in page.plans if p.new_glyphs), key=lambda p: min(m.start for m in p.mutations))
            paints[number] = page.expected_paints()
            masks[number] = page.masks()
            old_fonts[number] = font_fingerprints(self.document, number - 1)
            added[number] = {}
            for plan in page.plans:
                added[number].update(plan.font_subsets)
        publish_program(self.source, None, programs, output,
                        lambda document: self._verify(document, kept, new, paints, masks, old_fonts, added),
                        font_builders=builders)
        if removal_output is not None:
            removals = {}
            for number, page in self.pages.items():
                program = MutationProgram(page.program.source)
                for plan in page.plans:
                    for mutation in plan.removal_mutations:
                        program.add(mutation)
                removals[number - 1] = program.apply()

            def verify_removed(document):
                for number, page in self.pages.items():
                    consumed, moved, changes = page._merged()
                    untouched = [g for i, g in enumerate(page.glyphs) if i not in consumed and i not in moved]
                    _require_removal(untouched, document[number - 1])
                    observed = interpreted_paints(document.tobytes(), number)
                    remaining = [e for i, e in enumerate(page.observation['events']) if i not in changes]
                    from .elements import _close
                    if observed['errors'] or not _close(_nontext(remaining), _nontext(observed['events'])):
                        raise PdfError('removing the planned elements changed other paint or scope')
            publish_program(self.source, None, removals, removal_output, verify_removed)
        self.committed = True
        return TransactionResult(self, output)
