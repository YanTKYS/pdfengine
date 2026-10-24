"""Marked content and logical structure of an edited paragraph.

The drawing of a paragraph is rewritten in its content stream; its meaning
lives in marked-content sequences (BDC/BMC … EMC) and, in a tagged PDF, in
the structure tree that owns them through MCIDs. This module decides, from
the source alone, what happens to each of them, and proves afterwards that
the structure tree is unchanged except for the replacement text it allows.

Each sequence that draws a glyph of the paragraph is one of:

* a **container**: it encloses the whole paragraph and draws other content
  too (a placed PDF, a layer, an artifact, an MCID of a table cell that draws
  several visual paragraphs, an MCID without a structure tree). It is kept
  as it is; the new drawing is inserted inside it, so it stays in the same
  structure element.
* **re-emitted**: it lies inside the paragraph. Its BDC/EMC is removed with
  the old glyphs and written again around the new glyphs that belong to it
  (``kind`` mcid: an MCID, possibly emptied by the edit; plain: a sequence
  without semantics; atom: replacement text such as /Span <</ActualText>>,
  kept only around exactly its old glyphs).
* **fixed**: an inline placed object inside a line (its own clip, paths or
  images). Its operators stay untouched; the relayout must keep it in place.

Anything else (a sequence that crosses the paragraph boundary, nested
re-emitted sequences, an MCID in a Form XObject, a link, an alternate
description) is refused. In a tagged PDF the re-emitted MCIDs must be the
complete, ordered content of one block element (or of its inline leaves), so
that the paragraph text and the element text are the same thing.
"""
from pdfeditor.content_stream import operators
from pypdf.generic import IndirectObject, NameObject
from .engine import Refused

# Structure types whose content is a run of text (ISO 32000-1 14.8.4).
BLOCK = {'/P', '/H', '/H1', '/H2', '/H3', '/H4', '/H5', '/H6', '/TD', '/TH', '/LBody', '/Lbl', '/Caption', '/BlockQuote',
         '/Note', '/Title', '/TOCI', '/Div', '/Sect', '/Art', '/Part', '/Document', '/Index'}
INLINE = {'/Span', '/Quote', '/Code', '/Em', '/Strong', '/Sub'}
# Entries kept untouched; /ActualText is rewritten, /Alt and /E would turn stale.
KEPT = {'/Type', '/S', '/P', '/K', '/Pg', '/Lang', '/A', '/C', '/T', '/ID', '/R', '/ActualText'}
PAINT = {'re', 'f', 'F', 'f*', 'B', 'B*', 'b', 'b*', 'S', 's', 'sh', 'Do', 'BI', 'W', 'W*'}


def role(reader, element):
    """Standard structure type of an element, following the RoleMap."""
    kind = element.get('/S')
    root = reader.trailer['/Root'].get('/StructTreeRoot')
    rolemap = root.get_object().get('/RoleMap') if root is not None else None
    seen = set()
    while rolemap is not None and kind in rolemap and kind not in seen:
        seen.add(kind)
        kind = rolemap[kind]
    return kind


def block_of(reader, owner):
    """The block element whose text an owner draws: the owner itself, or the block parent of an inline leaf."""
    kind = role(reader, owner)
    if kind in BLOCK:
        return owner
    parent = owner.get('/P')
    if kind in INLINE and parent is not None and role(reader, parent.get_object()) in BLOCK:
        return parent.get_object()
    return None


def _hits(c, s):
    lo, hi = s['content_byte_range']
    invocation = tuple(tuple(i) for i in s['invocation'])
    return {i for e in c.events if e.stream_xref == s['stream_xref'] and e.invocation == invocation
            and lo <= e.operator.start < hi for ch in e.chars for i in ch.source_orders}


def _paints(c, s):
    """Whether a sequence draws or clips anything besides text."""
    lo, hi = s['content_byte_range']
    return any(o.name in PAINT for o in operators(c.streams[s['stream_xref']][lo:hi]))


def analyse(c, marks, ids):
    """``(scopes, parent, containers, fixed)`` of the paragraph drawn by glyphs ``ids``, or a refusal.

    ``scopes`` are the re-emitted sequences in source order, each a copy of
    the observed scope with ``kind``; ``parent`` the block element (object
    number) whose content they are, or None; ``containers`` the enclosing
    sequences, outermost first; ``fixed`` the glyph IDs of fixed inline objects.
    """
    chosen = set(ids)
    reader = c.reader
    tree = reader.trailer['/Root'].get('/StructTreeRoot')
    containers, scopes, fixed = [], [], set()
    for s in marks['scopes']:
        hits = _hits(c, s)
        if not hits & chosen:
            continue
        a = s['association']
        props = s['properties'] if isinstance(s['properties'], dict) else {}
        bound = a['status'] == 'tree_backed'
        # An MCID means nothing without a structure tree (an untagged PDF that
        # kept the tags of a placed page): such a sequence is plain content.
        plain = a['status'] == 'unmarked' or (a['status'] == 'orphan_mcid' and tree is None)
        if not (bound or plain):
            raise Refused('unbound marked content')
        if chosen <= hits and (hits != chosen or not bound):
            # Replacement text for the whole sequence would turn stale.
            if not bound and {'/ActualText', '/Alt', '/E'} & set(props):
                raise Refused('replacement-text marked content requires a span adapter')
            if bound:
                if s['invocation']:
                    raise Refused('marked content inside a Form XObject requires a semantic adapter')
                _shared_owner(reader, a)
            containers.append(dict(s, kind='container'))
            continue
        if not hits <= chosen:
            raise Refused('paragraph crosses a marked-content sequence')
        if bound and s['invocation']:
            raise Refused('marked content inside a Form XObject requires a semantic adapter')
        if plain and _paints(c, s):
            # An inline placed object (an icon in a line): kept where it is.
            fixed |= hits
            scopes.append(dict(s, kind='fixed'))
            continue
        kind = 'mcid' if bound else 'atom' if {'/ActualText', '/Alt', '/E'} & set(props) else 'plain'
        scopes.append(dict(s, kind=kind))
    # A previous edit may leave an ordered empty MCID: its structure content
    # item remains part of the paragraph element though no glyph selects it.
    blocks = {block_of(reader, reader.get_object(s['association']['owner_reference']['xref'])).indirect_reference.idnum
              for s in scopes if s['kind'] == 'mcid' and block_of(reader, reader.get_object(s['association']['owner_reference']['xref'])) is not None}
    if len(blocks) == 1:
        target = next(iter(blocks))
        listed = {s['id'] for s in scopes}
        for s in marks['scopes']:
            a = s['association']
            if s['id'] in listed or a['status'] != 'tree_backed' or s['invocation'] or _hits(c, s):
                continue
            block = block_of(reader, reader.get_object(a['owner_reference']['xref']))
            if block is not None and block.indirect_reference.idnum == target:
                scopes.append(dict(s, kind='mcid'))
        scopes.sort(key=lambda s: (s['stream_xref'], s['begin_byte_range'][0]))
    ids_of = {x['id'] for x in containers}
    for s in scopes:
        if set(s['enclosing_scope_ids']) - ids_of:
            raise Refused('mixed or nested marked-content scope')
    for s in containers:
        if set(s['enclosing_scope_ids']) - ids_of:
            raise Refused('mixed or nested marked-content scope')
    emitted = [s for s in scopes if s['kind'] != 'fixed']
    bound = [s for s in emitted if s['kind'] == 'mcid']
    covered = set().union(*(_hits(c, s) for s in emitted)) if emitted else set()
    loose = chosen - covered - fixed
    shared = [s for s in containers if s['association']['status'] == 'tree_backed']
    parent = None
    if bound:
        if loose:
            raise Refused('tagged paragraph mixes structured and unmarked glyphs')
        if shared:
            raise Refused('mixed or nested marked-content scope')
        parent = _structure(c, marks, bound, emitted)
    elif tree is not None and not shared and not any(s['tag'] == '/Artifact' for s in containers):
        # A tagged document whose paragraph is neither structure content nor an artifact.
        raise Refused('tagged selected glyphs lack semantic association')
    containers.sort(key=lambda s: len(s['enclosing_scope_ids']))
    return emitted + [s for s in scopes if s['kind'] == 'fixed'], parent, tuple(containers), tuple(sorted(fixed))


def _shared_owner(reader, a):
    """An MCID that draws this paragraph and more: its element keeps its content, no replacement text may turn stale."""
    owner = reader.get_object(a['owner_reference']['xref'])
    node = owner
    for _ in range(64):
        if node is None or node.get('/Type') == '/StructTreeRoot':
            break
        kind = role(reader, node)
        if kind not in BLOCK | INLINE and kind not in ('/Table', '/TR', '/THead', '/TBody', '/TFoot', '/L', '/LI'):
            raise Refused(f'unsupported structure role {kind}')
        if {'/ActualText', '/Alt', '/E'} & set(node.keys()):
            raise Refused('a shared marked-content sequence has replacement text or an alternate description')
        parent = node.get('/P')
        node = parent.get_object() if parent is not None else None
    if block_of(reader, owner) is None:
        raise Refused(f'unsupported structure role {role(reader, owner)}')


def _structure(c, marks, bound, emitted):
    """The block element whose complete content the re-emitted MCIDs are; checks roles, entries and order."""
    reader = c.reader
    blocks, leaves = set(), {}
    for s in bound:
        a = s['association']
        owner = reader.get_object(a['owner_reference']['xref'])
        block = block_of(reader, owner)
        if block is None:
            raise Refused(f'unsupported structure role {role(reader, owner)}')
        for node in (owner, block):
            extra = set(node.keys()) - KEPT
            if extra:
                raise Refused('unsupported structure entries ' + ','.join(sorted(extra)))
        if set(s['properties']) - {'/MCID', '/Lang', '/ActualText'}:
            raise Refused('unsupported scope semantics')
        blocks.add(block.indirect_reference.idnum)
    if len(blocks) != 1:
        raise Refused('multiple semantic paragraph parents')
    parent = blocks.pop()
    block = reader.get_object(parent)
    # Replacement text or a description on an enclosing element (a table, a
    # section) covers this text too; the edit would leave it stale.
    node, depth = block.get('/P'), 0
    while node is not None and depth < 64:
        node = node.get_object()
        if node.get('/Type') == '/StructTreeRoot':
            break
        if {'/ActualText', '/Alt', '/E'} & set(node.keys()):
            raise Refused('an enclosing structure element has replacement text or an alternate description')
        node, depth = node.get('/P'), depth + 1

    def kids(node):
        k = node.get('/K')
        if k is None:
            return []
        k = k.get_object() if hasattr(k, 'get_object') else k
        return list(k) if isinstance(k, list) else [k]

    def item(k):
        obj = k.get_object() if hasattr(k, 'get_object') else k
        if isinstance(obj, int):
            return ('mcid', int(obj))
        if isinstance(obj, dict) and obj.get('/Type') == '/MCR':
            if '/Stm' in obj:
                raise Refused('marked-content reference to another stream')
            return ('mcid', int(obj['/MCID']))
        if isinstance(obj, dict) and obj.get('/Type') == '/OBJR':
            raise Refused('a link or annotation belongs to the paragraph')
        if isinstance(obj, dict) and '/S' in obj:
            return ('span', obj.indirect_reference.idnum)
        raise Refused('unsupported paragraph child')
    actual = [item(k) for k in kids(block)]
    expected = []
    for s in bound:
        owner = s['association']['owner_reference']['xref']
        if owner == parent:
            expected.append(('mcid', s['association']['mcid']))
            continue
        if not expected or expected[-1] != ('span', owner):
            expected.append(('span', owner))
        leaves.setdefault(owner, []).append(s['association']['mcid'])
    if actual != expected:
        raise Refused('paragraph child order/coverage mismatch')
    for owner, mcids in leaves.items():
        leaf = reader.get_object(owner)
        if [item(k) for k in kids(leaf)] != [('mcid', m) for m in mcids]:
            raise Refused('span child order/coverage mismatch')
        if len(mcids) > 1 and '/ActualText' in leaf:
            raise Refused('ActualText on a multi-sequence span requires a span-level adapter')
    # Replacement text that is true now is kept true by the writer.
    for s in bound:
        owner = reader.get_object(s['association']['owner_reference']['xref'])
        if '/ActualText' in owner and str(owner['/ActualText']) != drawn(c, [s]):
            raise Refused('source ActualText differs from drawing')
    if '/ActualText' in block and str(block['/ActualText']) != drawn(c, emitted):
        raise Refused('source ActualText differs from drawing')
    return parent


def drawn(c, scopes):
    out = []
    for s in scopes:
        lo, hi = s['content_byte_range']
        invocation = tuple(tuple(i) for i in s['invocation'])
        out.append(''.join(ch.text for e in c.events if e.stream_xref == s['stream_xref'] and e.invocation == invocation
                           and lo <= e.operator.start < hi for ch in e.chars))
    return ''.join(out)


def canonical(reader):
    """The structure tree as positions, for comparing a source with a written file.

    Elements are named by their path from the root, pages by index, content
    items by MCID; every entry except /P, /K, /Pg is serialized (references
    resolved, cycles cut). Returns (elements, parent tree, id tree, role map).
    """
    root = reader.trailer['/Root'].get('/StructTreeRoot')
    if root is None:
        return None
    root = root.get_object()
    pages = {p.indirect_reference.idnum: n for n, p in enumerate(reader.pages)}
    # Annotations by their page and position in its /Annots (object numbers change on writing).
    annotations = {}
    for n, p in enumerate(reader.pages):
        for k, ref in enumerate(p.get('/Annots') or []):
            if hasattr(ref, 'idnum'):
                annotations[ref.idnum] = (n, k)
    paths, elements = {}, {}

    def value(v, depth=0):
        if depth > 32:
            return '…'
        if isinstance(v, IndirectObject):
            if v.idnum in paths:
                return ('element', paths[v.idnum])
            if v.idnum in pages:
                return ('page', pages[v.idnum])
            v = v.get_object()
        if isinstance(v, dict):
            return {str(k): value(x, depth + 1) for k, x in v.items() if k not in ('/P', '/K', '/Pg', '/Parent', '/Kids', '/Annots')}
        if isinstance(v, list):
            return [value(x, depth + 1) for x in v]
        if isinstance(v, (bytes, bytearray)):
            return v.hex()
        return str(v)

    def walk(node, path):
        obj = node.get_object() if hasattr(node, 'get_object') else node
        if isinstance(obj, int):
            return ('mcid', int(obj))
        if not isinstance(obj, dict):
            return ('other', str(obj))
        if obj.get('/Type') == '/MCR':
            return ('mcr', int(obj['/MCID']), value(obj.get('/Pg')), value(obj.get('/Stm')))
        if obj.get('/Type') == '/OBJR':
            target = obj.raw_get('/Obj')
            where = annotations.get(target.idnum) if hasattr(target, 'idnum') else None
            return ('objr', value(obj.get('/Pg')), ('annotation',) + where if where else value(target))
        if hasattr(node, 'idnum'):
            paths[node.idnum] = path
        k = obj.get('/K')
        k = k.get_object() if hasattr(k, 'get_object') else k
        children = list(k) if isinstance(k, list) else ([] if k is None else [k])
        elements[path] = dict(page=value(obj.raw_get('/Pg')) if '/Pg' in obj else None,
                              kids=[walk(child, path + (n,)) for n, child in enumerate(children)])
        return ('element', path)
    walk(root, ())
    # Entries after every element has a path (references between elements).
    stack = [(root, ())]
    seen = set()
    while stack:
        node, path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        elements[path]['entries'] = value(node)
        k = node.get('/K')
        k = k.get_object() if hasattr(k, 'get_object') else k
        for n, child in enumerate(list(k) if isinstance(k, list) else ([] if k is None else [k])):
            obj = child.get_object() if hasattr(child, 'get_object') else child
            if isinstance(obj, dict) and obj.get('/Type') not in ('/MCR', '/OBJR') and (path + (n,)) in elements:
                stack.append((obj, path + (n,)))
    parent_tree = {}

    def entries(node):
        node = node.get_object()
        nums = node.get('/Nums', [])
        for n in range(0, len(nums), 2):
            parent_tree[int(nums[n])] = value(nums[n + 1])
        for kid in node.get('/Kids', []):
            entries(kid)
    if '/ParentTree' in root:
        entries(root['/ParentTree'])
    ids = {}

    def names(node):
        node = node.get_object()
        items = node.get('/Names', [])
        for n in range(0, len(items), 2):
            ids[str(items[n])] = value(items[n + 1])
        for kid in node.get('/Kids', []):
            names(kid)
    if '/IDTree' in root:
        names(root['/IDTree'])
    return dict(elements=elements, parent_tree=parent_tree, ids=ids, paths=paths,
                role_map=value(root.get('/RoleMap')), class_map=value(root.get('/ClassMap')))


def changes(before, after):
    """Paths of elements whose entries differ, and whether anything else differs."""
    if before is None or after is None:
        return ([], before != after)
    other = (before['parent_tree'] != after['parent_tree'] or before['ids'] != after['ids'] or before['role_map'] != after['role_map']
             or before['class_map'] != after['class_map'] or set(before['elements']) != set(after['elements']))
    changed = []
    for path, element in before['elements'].items():
        new = after['elements'].get(path)
        if new is None or new['kids'] != element['kids'] or new['page'] != element['page']:
            other = True
            continue
        if new['entries'] != element['entries']:
            a = {k: v for k, v in element['entries'].items() if k != '/ActualText'}
            b = {k: v for k, v in new['entries'].items() if k != '/ActualText'}
            if a != b:
                other = True
            changed.append(path)
    return changed, other
