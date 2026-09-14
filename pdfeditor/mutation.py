"""Source identity across content-stream mutations.

An identity is a position in the exact source revision: the page program, an
operator's byte offset and a component inside it (a painted character or a
path paint). A ``MutationProgram`` holds every byte replacement applied to one
page program at once and maps offsets forward through them. Nothing is bound
by geometry: a mapped component is re-verified by its witness (font resource,
character code, Unicode, glyph ID, or the exact operation bytes) and any
consumed, missing or non-unique correspondence fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .backend import PdfError
from .content_stream import ContentPage


@dataclass
class Mutation:
    """One byte replacement of ``source[start:end]`` by ``data``.

    ``anchors`` name offsets inside ``data`` where an emitted operator begins,
    so its identity is known before the program is applied: ``op`` marks the
    original operator preserved inside a wrapper, ``rewritten`` a rewritten
    text operator whose retained characters are listed in ``chars`` (old
    character index to new index), and free names such as ``glyph:3`` or
    ``paint:0`` mark generated operators.
    """
    start: int
    end: int
    data: bytes
    kind: str = 'mutation'
    anchors: dict = field(default_factory=dict)
    chars: dict | None = None
    owner: str | None = None

    def edit(self):
        """The byte-position part only: enough to map offsets outside mutations."""
        value = dict(start=self.start, end=self.end, length=len(self.data))
        if 'op' in self.anchors:
            value['preserved_event_offset'] = self.anchors['op']
        return value

    def record(self):
        """The complete identity record: every anchor and retained character."""
        return dict(self.edit(), kind=self.kind, owner=self.owner, anchors=dict(self.anchors),
                    chars=None if self.chars is None else {str(k): v for k, v in self.chars.items()})

    @classmethod
    def from_record(cls, record, data: bytes):
        anchors = dict(record.get('anchors') or {})
        if 'preserved_event_offset' in record:
            if anchors.get('op', record['preserved_event_offset']) != record['preserved_event_offset']:
                raise PdfError('mutation record disagrees about its preserved operator')
            anchors['op'] = record['preserved_event_offset']
        chars = record.get('chars')
        if chars is not None:
            chars = {int(k): int(v) for k, v in chars.items()}
        if len(data) != record['length']:
            raise PdfError('mutation record length does not match the saved bytes')
        return cls(record['start'], record['end'], data, kind=record.get('kind', 'mutation'),
                   anchors=anchors, chars=chars, owner=record.get('owner'))


def _conflicts(a: Mutation, b: Mutation) -> bool:
    """Two mutations conflict when they overlap, or when an insertion touches another.

    Non-empty replacements may be adjacent (one ends where the next starts).
    A zero-length insertion has no source bytes of its own, so its order
    relative to a mutation starting or ending at the same offset would be a
    convention rather than provenance: such insertions are refused.
    """
    if a.start < b.end and b.start < a.end:
        return True
    if a.start == a.end or b.start == b.end:
        return a.start <= b.end and b.start <= a.end
    return False


class MutationProgram:
    """All mutations of one page program, applied together."""

    def __init__(self, source: bytes):
        self.source = source
        self.mutations: list[Mutation] = []

    def add(self, mutation: Mutation) -> Mutation:
        if not 0 <= mutation.start <= mutation.end <= len(self.source):
            raise PdfError('mutation range is outside the source program')
        for name, offset in mutation.anchors.items():
            if not isinstance(name, str) or not 0 <= offset <= len(mutation.data):
                raise PdfError('mutation anchor is outside its emitted bytes')
        for other in self.mutations:
            if _conflicts(other, mutation):
                raise PdfError('mutations overlap or an insertion touches another mutation; one operator cannot have two owners')
        self.mutations.append(mutation)
        self.mutations.sort(key=lambda m: (m.start, m.end))
        return mutation

    def containing(self, offset: int) -> Mutation | None:
        for mutation in self.mutations:
            if mutation.start <= offset < mutation.end:
                return mutation
        return None

    def _delta_before(self, offset: int, *, exclude: Mutation | None = None) -> int:
        return sum(len(m.data) - (m.end - m.start) for m in self.mutations
                   if m is not exclude and m.end <= offset)

    def map_offset(self, offset: int) -> int:
        """Forward-map a source offset; a consumed offset has no successor."""
        if type(offset) is not int or not 0 <= offset <= len(self.source):
            raise PdfError('source offset is outside the program')
        mutation = self.containing(offset)
        if mutation is not None:
            if offset == mutation.start and 'op' in mutation.anchors:
                return self.anchor(mutation, 'op')
            raise PdfError('source operator was consumed by a mutation')
        return offset + self._delta_before(offset)

    def anchor(self, mutation: Mutation, name: str) -> int:
        if mutation not in self.mutations:
            raise PdfError('anchor belongs to a mutation outside this program')
        if name not in mutation.anchors:
            raise PdfError(f'mutation has no emitted operator named {name}')
        return mutation.start + self._delta_before(mutation.start, exclude=mutation) + mutation.anchors[name]

    def apply(self) -> bytes:
        data = self.source
        for mutation in sorted(self.mutations, key=lambda m: m.start, reverse=True):
            data = data[:mutation.start] + mutation.data + data[mutation.end:]
        return data

    def edits(self) -> list[dict]:
        """Byte-position map only (``byte_edits``)."""
        return [m.edit() for m in self.mutations]

    def records(self) -> list[dict]:
        """Complete, serializable mutation records (``mutation_map``)."""
        return [m.record() for m in self.mutations]

    def mutation_at(self, start: int) -> Mutation:
        for mutation in self.mutations:
            if mutation.start == start:
                return mutation
        raise PdfError('no mutation starts at the given source offset')


def map_offset(offset, edits):
    """Map through recorded byte edits (the serialized form of a program)."""
    program = MutationProgram(b'\0' * (max([offset] + [e['end'] for e in edits]) + 1))
    for edit in edits:
        anchors = {'op': edit['preserved_event_offset']} if 'preserved_event_offset' in edit else {}
        program.add(Mutation(edit['start'], edit['end'], b'\0' * edit['length'], anchors=anchors))
    return program.map_offset(offset)


def text_ref(content, glyph_id: int) -> dict:
    """The source position of one observed glyph: operator offset and character."""
    for event in content.events:
        for index, char in enumerate(event.chars):
            if glyph_id in char.source_orders:
                if event.error:
                    raise PdfError(event.error)
                if event.invocation:
                    raise PdfError('glyph identity inside a Form XObject cannot be mapped through page mutations')
                return dict(offset=event.operator.start, char=index, paint=char.source_orders.index(glyph_id),
                            code=char.code.hex(), unicode=char.text, font=event.state.font.name, event=event)
    raise PdfError('glyph has no unique text-operator provenance')


def _events_by_offset(content) -> dict:
    result = {}
    for event in content.events:
        if event.invocation:
            continue
        if event.operator.start in result:
            raise PdfError('two text operators share one program offset')
        result[event.operator.start] = event
    return result


def _witness(before, after, old: int, new: int, ref: dict, char) -> None:
    if char.code.hex() != ref['code'] or char.text != ref['unicode']:
        raise PdfError('mapped glyph code or Unicode differs from its source witness')
    a, b = before.actual[old], after.actual[new]
    if a['gid'] != b['gid'] or a['span']['font'] != b['span']['font'] or a['span']['type'] != b['span']['type']:
        raise PdfError('mapped glyph ID, font or paint differs from its source witness')


def map_glyphs(before, after, program: MutationProgram, glyph_ids) -> dict[int, int]:
    """Map observed glyph IDs of one page revision to the next, or fail closed."""
    events = _events_by_offset(after)
    result = {}
    for old in glyph_ids:
        ref = text_ref(before, old)
        mutation = program.containing(ref['offset'])
        index = ref['char']
        if mutation is None:
            offset = program.map_offset(ref['offset'])
        elif ref['offset'] == mutation.start and mutation.chars is not None:
            if index not in mutation.chars:
                raise PdfError('glyph was removed by its mutation and has no successor')
            offset = program.anchor(mutation, 'rewritten')
            index = mutation.chars[index]
        elif ref['offset'] == mutation.start and 'op' in mutation.anchors:
            offset = program.anchor(mutation, 'op')
        else:
            raise PdfError('glyph provenance was consumed by a mutation')
        event = events.get(offset)
        if event is None or event.error or index >= len(event.chars):
            raise PdfError('no text operator survives at the mapped source offset')
        if event.state.font.name != ref['font']:
            raise PdfError('mapped text operator uses a different font resource')
        char = event.chars[index]
        if ref['paint'] >= len(char.source_orders):
            raise PdfError('mapped character has fewer paint observations than its source')
        new = char.source_orders[ref['paint']]
        _witness(before, after, old, new, ref, char)
        result[old] = new
    if len(set(result.values())) != len(result):
        raise PdfError('glyph mapping is not one-to-one')
    return result


def emitted_glyphs(after, program: MutationProgram, mutation: Mutation, names) -> list[int]:
    """Observed glyph IDs of generated single-character text operators."""
    events = _events_by_offset(after)
    result = []
    for name in names:
        event = events.get(program.anchor(mutation, name))
        if event is None or event.error or len(event.chars) != 1 or len(event.chars[0].source_orders) != 1:
            raise PdfError('generated text operator has no unique paint observation')
        result.append(event.chars[0].source_orders[0])
    if len(set(result)) != len(result):
        raise PdfError('generated glyph binding is not one-to-one')
    return result


def _paths_by_offset(catalog) -> dict:
    result = {}
    for path in catalog['paths']:
        if path['invocation']:
            continue
        if path['merged_range'][0] in result:
            raise PdfError('two path paints share one program offset')
        result[path['merged_range'][0]] = path
    return result


def map_path(before_catalog, after_catalog, program: MutationProgram, path_id: str, *,
             anchor: tuple[Mutation, str] | None = None) -> str:
    """Map one source path ID forward; the operator bytes or emitted anchor witness it."""
    matches = [p for p in before_catalog['paths'] if p['id'] == path_id]
    if len(matches) != 1:
        raise PdfError('unknown or ambiguous source path ID')
    path = matches[0]
    if path['invocation']:
        raise PdfError('path identity inside a Form XObject cannot be mapped through page mutations')
    a, b = path['merged_range']
    mutation = program.containing(a)
    if anchor is not None:
        target, name = anchor
        if mutation is not target:
            raise PdfError('emitted paint anchor does not replace this source path')
        offset = program.anchor(target, name)
    elif mutation is None:
        offset = program.map_offset(a)
        applied = program.apply()
        if applied[offset:offset + (b - a)] != program.source[a:b]:
            raise PdfError('path operator bytes changed at the mapped offset')
    else:
        raise PdfError('path operator was consumed by a mutation without an emitted successor')
    successor = _paths_by_offset(after_catalog).get(offset)
    if successor is None:
        raise PdfError('no path paint survives at the mapped source offset')
    if anchor is None and successor['operation_sha256'] != path['operation_sha256']:
        raise PdfError('path operation differs from its source witness')
    return successor['id']


def emitted_paths(after_catalog, program: MutationProgram, mutation: Mutation, names) -> list[str]:
    """Path IDs of generated paint operators, by their emitted anchors."""
    by_offset = _paths_by_offset(after_catalog)
    result = []
    for name in names:
        path = by_offset.get(program.anchor(mutation, name))
        if path is None:
            raise PdfError('generated paint operator has no path in the saved program')
        result.append(path['id'])
    if len(set(result)) != len(result):
        raise PdfError('generated paint binding is not one-to-one')
    return result


class IdentityMap:
    """Source identities of one page, mapped from one saved revision to the next."""

    def __init__(self, source, output, before, after, program: MutationProgram, *, owns_before=False,
                 owns_after=True, path_anchors=None, consumed_paths=(), planned_paints=None):
        self.source, self.output = source, output
        self.before, self.after, self.program = before, after, program
        self.owns_before, self.owns_after = owns_before, owns_after
        self.path_anchors = dict(path_anchors or {})
        self.consumed_paths = set(consumed_paths)
        self.planned_paints = dict(planned_paints or {})
        self._catalogs = {}

    @classmethod
    def from_records(cls, source, output, page, records, *, path_anchors=None, consumed_paths=(), planned_paints=None):
        """Rebuild the map of a saved page from persisted mutation records.

        Records carry every anchor and retained-character map, so glyphs kept
        inside rewritten operators and generated glyphs or paints are mapped
        exactly as in the original transaction. Plain ``byte_edits`` are
        accepted too, but then only offsets outside mutations and preserved
        operators can be followed. The saved program must be reproduced.
        """
        before, after = ContentPage(source, page), ContentPage(output, page)
        try:
            data, saved = before.streams[-before.page.xref], after.streams[-after.page.xref]
            program = MutationProgram(data)
            delta = 0
            for record in sorted(records, key=lambda e: (e['start'], e['end'])):
                start = record['start'] + delta
                program.add(Mutation.from_record(record, saved[start:start + record['length']]))
                delta += record['length'] - (record['end'] - record['start'])
            if program.apply() != saved:
                raise PdfError('recorded byte edits do not reproduce the saved page program')
        except Exception:
            before.close()
            after.close()
            raise
        return cls(source, output, before, after, program, owns_before=True, path_anchors=path_anchors,
                   consumed_paths=consumed_paths, planned_paints=planned_paints)

    @classmethod
    def from_edits(cls, source, output, page, edits):
        """Rebuild from byte edits or full records; see ``from_records``."""
        return cls.from_records(source, output, page, edits)

    @property
    def page(self):
        return self.before.page.number + 1

    def catalog(self, which):
        if which not in self._catalogs:
            from .paint_provenance import _load_catalog
            self._catalogs[which] = _load_catalog(self.source if which == 'before' else self.output, self.page)[0]
        return self._catalogs[which]

    def map_offset(self, offset):
        return self.program.map_offset(offset)

    def map_glyphs(self, glyph_ids):
        return map_glyphs(self.before, self.after, self.program, glyph_ids)

    def emitted_glyphs(self, mutation, names):
        return emitted_glyphs(self.after, self.program, mutation, names)

    def map_path(self, path_id):
        if path_id in self.consumed_paths:
            raise PdfError('source path was replaced by generated paint and has no single successor')
        return map_path(self.catalog('before'), self.catalog('after'), self.program, path_id,
                        anchor=self.path_anchors.get(path_id))

    def emitted_paths(self, mutation, names):
        return emitted_paths(self.catalog('after'), self.program, mutation, names)

    def close(self):
        if self.owns_after:
            self.after.close()
        if self.owns_before:
            self.before.close()
