"""Marked-content and structure-tree shape of every paragraph group on a page.

  .venv/bin/python -m prototypes.paragraph_frame.tagged_diagnose <pdf> <page> <out.json>

For each paragraph group (auto_detect.observe) the record lists the
marked-content scopes that draw its glyphs (tag, properties, MCID binding,
whether the scope stays inside the paragraph), the structure elements that
own them (role chain up to the root, attributes), and derived structure
classes. It reads only; nothing is proposed or written.
"""
import json
import sys
from collections import Counter


def roles(ancestors):
    return [a['role'].get('mapped') or a['role'].get('declared') for a in ancestors]


def describe(source, page):
    from pdfeditor.content_stream import ContentPage
    from pdfeditor.marked_content import observe_marked_content
    from pdfeditor.page_proposal import _Analysis
    from .auto_detect import group_text, observe
    marks = observe_marked_content(source, page)
    c = ContentPage(source, page)
    try:
        rows, parents, groups = observe(c, marks, _Analysis(source, page))
        root = c.reader.trailer['/Root']
        tree = root.get('/StructTreeRoot')
        tree = tree.get_object() if tree is not None else None

        def inside(s, e):
            return (e.stream_xref == s['stream_xref'] and e.invocation == tuple(tuple(i) for i in s['invocation'])
                    and s['content_byte_range'][0] <= e.operator.start < s['content_byte_range'][1])
        hits = {s['id']: {i for e in c.events if inside(s, e) for ch in e.chars for i in ch.source_orders} for s in marks['scopes']}
        by_id = {s['id']: s for s in marks['scopes']}
        result = []
        for g in groups:
            ids = [i for r in g for i in r['glyph_ids']]
            chosen = set(ids)
            text = group_text(c, g)[0]
            scopes = []
            covered = set()
            for s in marks['scopes']:
                h = hits[s['id']]
                if not h & chosen:
                    continue
                covered |= h & chosen
                a = s['association']
                props = s['properties'] if isinstance(s['properties'], dict) else {}
                entry = dict(id=s['id'], tag=s['tag'], properties=sorted(props), status=a['status'], reason=a.get('reason'),
                             mcid=a.get('mcid'), in_form=bool(s['invocation']), depth=len(s['enclosing_scope_ids']),
                             enclosing_tags=[by_id[x]['tag'] for x in s['enclosing_scope_ids']],
                             inside_paragraph=h <= chosen, covers_paragraph=chosen <= h, glyphs=len(h), glyphs_in_paragraph=len(h & chosen))
                if a['status'] == 'tree_backed':
                    owner = c.reader.get_object(a['owner_reference']['xref'])
                    entry.update(owner=a['owner_reference']['xref'], roles=roles(a['ancestors']),
                                 owner_keys=sorted(k for k in owner if k not in ('/Type', '/K', '/P', '/Pg', '/S')),
                                 parent_keys=sorted(k for k in c.reader.get_object(owner.raw_get('/P').idnum) if k not in ('/Type', '/K', '/P', '/Pg', '/S'))
                                 if hasattr(owner.raw_get('/P'), 'idnum') else [])
                scopes.append(entry)
            result.append(dict(text=text[:80], glyphs=len(ids), uncovered=len(chosen - covered), scopes=scopes,
                               classes=classes(scopes, len(chosen - covered), tree)))
        return dict(page=page, has_tree=tree is not None, parent_tree=bool(tree is not None and '/ParentTree' in tree),
                    role_map=sorted(str(k) for k in (tree.get('/RoleMap') or {})) if tree is not None else [],
                    paragraphs=result)
    finally:
        c.close()


def classes(scopes, uncovered, tree):
    """Structure classes of one paragraph (several may apply)."""
    out = set()
    if not scopes:
        out.add('no marked content' if not uncovered else 'unmarked glyphs')
        return sorted(out)
    if uncovered:
        out.add('glyphs outside any marked content')
    for s in scopes:
        if s['tag'] == '/Artifact':
            out.add('artifact')
            continue
        if s['status'] == 'unmarked':
            if {'/ActualText', '/Alt'} & set(s['properties']):
                out.add('unmarked replacement text (ActualText/Alt BDC)')
            elif s['tag'] == '/Span':
                out.add('unmarked /Span BDC')
            else:
                out.add('unmarked container ' + s['tag'])
            continue
        if s['status'] != 'tree_backed':
            out.add('MCID without structure tree' if tree is None else 'MCID not bound: ' + str(s['reason'])[:60])
            continue
        if s['in_form']:
            out.add('MCID in Form XObject')
        if not s['inside_paragraph']:
            out.add('one MCID spans several paragraphs')
        if s['depth'] and any(t != '/Artifact' for t in s['enclosing_tags']):
            out.add('nested marked content')
        leaf = s['roles'][0]
        if leaf not in ('/P', '/Span'):
            out.add('leaf role ' + leaf)
        if '/ActualText' in s['owner_keys']:
            out.add('ActualText on StructElem')
        if '/Alt' in s['owner_keys'] or '/E' in s['owner_keys']:
            out.add('Alt/E on StructElem')
        if '/Lang' in s['owner_keys'] or '/Lang' in s['parent_keys']:
            out.add('Lang')
        extra = set(s['properties']) - {'/MCID', '/Lang', '/ActualText'}
        if extra:
            out.add('BDC properties ' + ','.join(sorted(extra)))
    backed = [s for s in scopes if s['status'] == 'tree_backed']
    if len(backed) > 1:
        out.add('several MCIDs')
        parents = {tuple(s['roles'][1:2]) + (s.get('owner') if s['roles'][0] == '/P' else None,) for s in backed}
        owners = {s['owner'] for s in backed}
        if len(owners) > 1:
            out.add('several structure elements')
    return sorted(out)


def main():
    source, page, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    record = describe(source, page)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    print(Counter(c for p in record['paragraphs'] for c in p['classes']).most_common())


if __name__ == '__main__':
    main()
