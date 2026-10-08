"""Read-only marked-content provenance, separate from graphics state and layout.

An MCID identifies a source content item, not a paragraph or a movable object.
Only a checked ParentTree and StructureTree backlink establish a structural
association. Even that association makes no claim about editing ownership.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

from .backend import PdfError
from .content_stream import operators


def _object(value):
    return value.get_object() if hasattr(value, "get_object") else value


def _reference(value):
    ref = value if isinstance(value, IndirectObject) else getattr(value, "indirect_reference", None)
    return {"xref": ref.idnum, "generation": ref.generation} if ref is not None else None


def _same_reference(a, b):
    first, second = _reference(a), _reference(b)
    return first is not None and first == second


def _plain(value, seen=None, depth=0):
    """JSON-safe properties without following reference cycles indefinitely."""
    seen = set() if seen is None else set(seen)
    ref = _reference(value)
    key = (ref["xref"], ref["generation"]) if ref else ("direct", id(value))
    if key in seen or depth > 16:
        return {"reference": ref, "unexpanded": True}
    seen.add(key)
    value = _object(value)
    if isinstance(value, dict):
        return {str(k): _plain(v, seen, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, seen, depth + 1) for v in value]
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _child_evidence(value):
    """Describe K operands without expanding referenced pages or tree cycles."""
    if isinstance(value, IndirectObject):
        return {'reference': _reference(value)}
    if isinstance(value, dict):
        return {str(k): _child_evidence(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_child_evidence(v) for v in value]
    return _plain(value)


class _Structure:
    def __init__(self, root):
        self.root = _object(root.get("/StructTreeRoot"))
        self.parents = {}
        self.error = None
        if self.root is None:
            return
        try:
            self._number_tree(self.root.get("/ParentTree"), set())
        except Exception as exc:
            self.error = str(exc)

    def _number_tree(self, reference, seen):
        if reference is None:
            raise ValueError("StructureTree has no ParentTree")
        node = _object(reference)
        identity = id(node)
        if identity in seen:
            raise ValueError("ParentTree contains a cycle or repeated node")
        if len(seen) >= 10000:
            raise ValueError("ParentTree observation limit")
        seen.add(identity)
        if not isinstance(node, dict):
            raise ValueError("ParentTree node is not a dictionary")
        numbers = _object(node.get("/Nums", []))
        if not isinstance(numbers, list) or len(numbers) % 2:
            raise ValueError("ParentTree Nums is not a key/value array")
        for index in range(0, len(numbers), 2):
            key = numbers[index]
            if isinstance(key, bool) or not isinstance(key, int) or key < 0 or key in self.parents:
                raise ValueError("ParentTree has an invalid or duplicate key")
            self.parents[key] = numbers[index + 1]
        for child in _object(node.get("/Kids", [])):
            self._number_tree(child, seen)

    def _role(self, role):
        declared = str(role) if role is not None else None
        resolved = declared
        if role is not None and not isinstance(role, NameObject):
            return {'declared': declared, 'mapped': None, 'error': 'structure role is not a PDF name'}
        roles = _object(self.root.get("/RoleMap", {}))
        visited = set()
        while resolved in roles:
            if resolved in visited:
                return {"declared": declared, "mapped": None, "error": "RoleMap cycle"}
            visited.add(resolved)
            if not isinstance(roles[resolved], NameObject):
                return {'declared': declared, 'mapped': None, 'error': 'RoleMap target is not a PDF name'}
            resolved = str(roles[resolved])
        return {"declared": declared, "mapped": resolved}

    def _ancestors(self, owner):
        result, seen = [], set()
        reference = owner
        while reference is not None:
            node = _object(reference)
            if not isinstance(node, dict) or id(node) in seen or len(result) >= 128:
                raise ValueError("invalid or cyclic StructureTree parent chain")
            seen.add(id(node))
            parent = node.get('/P')
            if parent is not None:
                children = _object(_object(parent).get('/K', []))
                children = children if isinstance(children, list) else [children]
                reachable = sum(_same_reference(reference, child) for child in children) == 1
            else:
                reachable = _same_reference(reference, self.root)
                children = []
            result.append({"reference": _reference(reference), "type": str(node.get("/Type")),
                           "type_is_name": isinstance(node.get('/Type'), NameObject),
                           "role": self._role(node.get("/S")), "page_reference": _reference(node.get("/Pg")),
                           "language": _plain(node.get("/Lang")),
                           "parent_backlink_verified": reachable,
                           "parent_child_index": next((i for i, child in enumerate(children)
                                                       if _same_reference(reference, child)), None),
                           "unsupported_semantics": sorted(k for k in ('/ActualText', '/Alt', '/E', '/A', '/C', '/OC',
                                                                        '/NS', '/Phoneme', '/PhoneticAlphabet') if k in node)})
            if _same_reference(reference, self.root):
                return result
            reference = node.get("/P")
        raise ValueError("StructureTree parent chain does not reach its root")

    def association(self, properties, container, page, namespace):
        mcid = properties.get("/MCID")
        if mcid is None:
            return {"status": "unmarked", "evidence": "no MCID property"}
        result = {"status": "unknown", "mcid": _plain(mcid),
                  "namespace": namespace, "evidence": "observed source properties"}
        if isinstance(mcid, bool) or not isinstance(mcid, int) or mcid < 0:
            return dict(result, reason="MCID must be a nonnegative integer")
        if self.root is None:
            return dict(result, status="orphan_mcid", reason="document has no StructureTree")
        key = container.get("/StructParents")
        result["struct_parents"] = _plain(key)
        if self.error:
            return dict(result, reason=self.error)
        if key is None:
            return dict(result, status="orphan_mcid", reason="content namespace has no StructParents key")
        if isinstance(key, bool) or not isinstance(key, int) or key < 0:
            return dict(result, reason="StructParents is not a nonnegative integer")
        if key not in self.parents:
            return dict(result, status="orphan_mcid", reason="ParentTree has no namespace entry")
        entries = _object(self.parents[key])
        if not isinstance(entries, list) or mcid >= len(entries):
            return dict(result, reason="ParentTree namespace array does not contain the MCID")
        owner = entries[mcid]
        node = _object(owner)
        if not isinstance(owner, IndirectObject) or not isinstance(node, dict) or node.get("/Type") != "/StructElem":
            return dict(result, reason="ParentTree item is not an indirect StructElem")
        result["owner_reference"] = _reference(owner)
        try:
            result["ancestors"] = self._ancestors(owner)
            result["owner_role"] = self._role(node.get("/S"))
            inherited_page = None
            parent = owner
            for _ in result["ancestors"]:
                obj = _object(parent)
                if obj.get("/Pg") is not None:
                    inherited_page = obj.get("/Pg")
                    break
                parent = obj.get("/P")
            children = _object(node.get("/K", []))
            children = children if isinstance(children, list) else [children]
            result['owner_children'] = _child_evidence(children)
            matches = 0
            for child in children:
                item = _object(child)
                if isinstance(item, int) and not isinstance(item, bool):
                    # Integer K children address the inherited page stream.
                    if namespace["kind"] == "page" and item == mcid and _same_reference(inherited_page, page):
                        matches += 1
                elif isinstance(item, dict) and item.get("/Type") == "/MCR" and item.get("/MCID") == mcid:
                    target_page = item.get("/Pg", inherited_page)
                    target_stream = item.get("/Stm")
                    if not _same_reference(target_page, page):
                        continue
                    if namespace["kind"] == "page" and target_stream is None:
                        matches += 1
                    elif namespace["kind"] == "form" and _same_reference(target_stream, container):
                        matches += 1
            if matches != 1:
                return dict(result, reason="StructureTree K does not have one matching content-item backlink",
                            backlink_verified=False)
            return dict(result, status="tree_backed", backlink_verified=True,
                        evidence="ParentTree lookup and StructureTree K backlink",
                        policy="structural association only; editing ownership and flow remain undecided")
        except Exception as exc:
            return dict(result, reason=str(exc), backlink_verified=False)


def observe_marked_content(source, page=1):
    """Return source-bound operator ranges, active scopes and checked tag links.

    Page byte offsets use pypdf's decoded, merged /Contents program, exactly as
    ContentPage does. Form byte offsets address the decoded Form definition;
    invocation tuples distinguish repeated paints of that shared definition.
    Marked-content stacks are independent of q/Q and of Form-local stacks.
    """
    if type(page) is not int or page < 1:
        raise PdfError("page must be a positive integer")
    reader = PdfReader(source)
    if reader.is_encrypted and not reader.decrypt(""):
        raise PdfError("password required")
    if page > len(reader.pages):
        raise PdfError("page is outside the document")
    pdf_page = reader.pages[page - 1]
    page_ref = _reference(pdf_page)
    structure = _Structure(reader.trailer["/Root"])
    result = {"schema_version": 1, "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
              "page": page, "page_reference": page_ref, "complete": True,
              "streams": [], "scopes": [], "operators": [], "diagnostics": [],
              "policy": "Observed source scope and structural association; no inferred or user-confirmed ownership"}

    def issue(reason, **context):
        result["complete"] = False
        result["diagnostics"].append(dict(reason=reason, **context))

    def walk(container, resources, data, xref, invocation, enclosing, definition_path, kind):
        if len(invocation) > 16 or len(result["operators"]) > 250000:
            issue("marked-content observation limit", invocation=invocation)
            return
        namespace = {"kind": kind, "reference": _reference(container)}
        context = {"stream_xref": xref, "invocation": invocation}
        result["streams"].append(dict(context, namespace=namespace,
            decoded_sha256=hashlib.sha256(data).hexdigest(), decoded_length=len(data),
            enclosing_scope_ids=enclosing, struct_parents=_plain(container.get("/StructParents"))))
        try:
            parsed = list(operators(data))
        except Exception as exc:
            issue("source tokenization failed: " + str(exc), **context)
            return
        local = []
        for index, operator in enumerate(parsed):
            op_id = f"s{xref}-o{index}" + "".join(f"@{item[1]}" for item in invocation)
            row = dict(context, id=op_id, operator_index=index, operator=operator.name,
                       byte_range=[operator.start, operator.end], active_scope_ids=enclosing + local.copy(),
                       local_scope_ids=local.copy(), enclosing_scope_ids=enclosing)
            result["operators"].append(row)
            name, args = operator.name, operator.args
            if name in ("BMC", "BDC"):
                scope = dict(context, id=f"mc{len(result['scopes'])}", namespace=namespace,
                             begin_operator_id=op_id, begin_byte_range=row["byte_range"],
                             end_byte_range=None, content_byte_range=[operator.end, len(data)],
                             enclosing_scope_ids=enclosing + local.copy())
                properties, property_error = {}, None
                if len(args) != (2 if name == "BDC" else 1) or not isinstance(args[0], NameObject):
                    property_error = "invalid marked-content operands"
                elif name == "BDC":
                    raw = args[1]
                    if isinstance(raw, NameObject):
                        scope["property_resource_name"] = str(raw)
                        dictionary = _object(resources.get("/Properties", {}))
                        raw = dictionary.get(raw) if isinstance(dictionary, dict) else None
                        scope["property_reference"] = _reference(raw)
                    properties = _object(raw)
                    if not isinstance(properties, dict):
                        properties, property_error = {}, "marked-content properties could not be resolved"
                scope["tag"] = str(args[0]) if args else None
                scope["properties"] = _plain(properties)
                scope["association"] = (structure.association(properties, container, pdf_page, namespace)
                    if property_error is None else {"status": "unknown", "reason": property_error})
                if property_error:
                    issue(property_error, operator_id=op_id, **context)
                result["scopes"].append(scope)
                local.append(scope["id"])
            elif name == "EMC":
                if args or not local:
                    issue("unmatched EMC or invalid EMC operands", operator_id=op_id, **context)
                else:
                    scope = result["scopes"][int(local.pop()[2:])]
                    scope["end_byte_range"] = row["byte_range"]
                    scope["content_byte_range"][1] = operator.start
            elif name == "Do":
                if len(args) != 1:
                    issue("invalid Do operands", operator_id=op_id, **context)
                    continue
                xobjects = _object(resources.get("/XObject", {}))
                reference = xobjects.get(args[0]) if isinstance(xobjects, dict) else None
                form = _object(reference)
                if not isinstance(form, dict) or form.get("/Subtype") != "/Form":
                    continue
                ref = _reference(reference)
                if ref is None or ref["xref"] in definition_path:
                    issue("Form definition has no indirect identity or recurses", operator_id=op_id, **context)
                    continue
                child_resources = _object(form.get("/Resources", resources))
                walk(form, child_resources, form.get_data(), ref["xref"],
                     invocation + [[xref, index, str(args[0])]], enclosing + local.copy(),
                     definition_path + [ref["xref"]], "form")
        if local:
            issue("unterminated marked-content sequence", scope_ids=local, **context)

    contents = pdf_page.get_contents()
    data = contents.get_data() if contents is not None else b""
    result["page_program_sha256"] = hashlib.sha256(data).hexdigest()
    walk(pdf_page, _object(pdf_page.get("/Resources", {})), data, -page_ref["xref"], [], [], [], "page")
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def paragraph_structure(content, selected, *, offset=None):
    """Prove the narrow, leaf-/P, same-owner MCID bundle (never ownership).

    All original envelopes stay in place. The first receives all new text;
    subsequent envelopes remain empty. Thus visual lines never allocate MCIDs.
    An offset is used only to reobserve an already owned island, including an
    empty one; the caller separately proves marker and glyph containment.
    """
    if not any(b.operator.name in ('BDC', 'BMC', 'EMC') for b in content.boundaries):
        return None
    if not hasattr(content, '_marked_observation'):
        content._marked_observation = observe_marked_content(content.source, content.page.number + 1)
    observed = content._marked_observation
    events = [e for e in content.events if any(set(c.source_orders) & selected for c in e.chars)]
    scopes = observed['scopes']
    def active(position):
        return [s for s in scopes if not s['invocation'] and
                s['content_byte_range'][0] <= position < s['content_byte_range'][1]]
    stacks = [active(e.operator.start) for e in events]
    if offset is not None:
        stacks.append(active(offset))
    if not any(stacks):
        return None
    def refuse(reason):
        raise PdfError('unsupported marked structure: ' + reason)
    if not observed['complete']:
        refuse('malformed BDC/EMC')
    if any(generation not in (0, 65535) and entries for generation, entries in content.reader.xref.items()):
        refuse('persistent tagged output requires generation-zero object references')
    if any(e.invocation for e in events) or any(len(s) != 1 for s in stacks):
        refuse('nested, mixed unmarked, or Form content')
    first = min((s[0] for s in stacks), key=lambda s: s['begin_byte_range'][0])
    owner = first['association'].get('owner_reference')
    if owner is None or any(s[0]['association'].get('owner_reference') != owner for s in stacks):
        refuse('paragraph needs one tree-backed owner')
    bundle = [s for s in scopes if s['association'].get('owner_reference') == owner]
    if any(any(parent['id'] in s['enclosing_scope_ids'] for parent in bundle) for s in scopes):
        refuse('nested marked content inside paragraph envelope')
    mcids = []
    for s in bundle:
        a = s['association']
        if (s['invocation'] or s['namespace']['kind'] != 'page' or s['enclosing_scope_ids'] or
                s['end_byte_range'] is None or s['tag'] != '/P' or set(s['properties']) != {'/MCID'} or
                a['status'] != 'tree_backed' or a.get('backlink_verified') is not True or
                a.get('owner_role') != {'declared': '/P', 'mapped': '/P'}):
            refuse('requires plain /P MCID page envelopes and verified leaf /P owner')
        if any(not p['parent_backlink_verified'] or p['unsupported_semantics'] for p in a['ancestors']):
            refuse('ancestor backlink or semantic/layout override')
        ancestors = a['ancestors']
        if (ancestors[-1]['type'] != '/StructTreeRoot' or any(not p['type_is_name'] for p in ancestors) or
                any(p['type'] != '/StructElem' or
                not isinstance(p['role']['mapped'], str) or not p['role']['mapped'].startswith('/')
                for p in ancestors[:-1])):
            refuse('invalid ancestor type or role')
        mcids.append(a['mcid'])
    if len(set(mcids)) != len(mcids):
        refuse('duplicate MCID')
    if any(sum(s['namespace'] == first['namespace'] and s['properties'].get('/MCID') == mcid
               for s in scopes) != 1 for mcid in mcids):
        refuse('duplicate MCID in namespace')
    if first != bundle[0]:
        refuse('paragraph does not start in its first MCID')
    # Integer K only: MCR and mixed StructElem children remain observed, but
    # are outside this writer's contract. Exact order proves logical order.
    children = first['association']['owner_children']
    if children != mcids or any(not isinstance(i, int) or isinstance(i, bool) for i in children):
        refuse('owner K must be exactly the ordered integer MCID bundle')
    for event in content.events:
        if any(s in bundle for s in active(event.operator.start)) and not event.invocation:
            ids = {i for c in event.chars for i in c.source_orders}
            if event.error or any(not c.source_orders for c in event.chars) or ids - selected:
                refuse('foreign or unproven text shares the owner')
    # A scope may contain a whole line or several whole lines, never split a
    # visual line across structural items.
    line_scopes = {}
    for event, stack in zip(events, stacks):
        for c in event.chars:
            for i in set(c.source_orders) & selected:
                y = round(content.actual[i]['origin'][1], 4)
                line_scopes.setdefault(y, set()).add(stack[0]['id'])
    if any(len(s) != 1 for s in line_scopes.values()):
        refuse('marked content splits a visual line')
    # Unknown paints in an envelope cannot silently become text ownership.
    allowed = set('q Q BT ET Tf Tz Tc Tw Ts TL Tr Tm Td TD T* Tj TJ g rg k G RG K BDC EMC'.split())
    if any(o['operator'] not in allowed and any(s['id'] in o['active_scope_ids'] for s in bundle)
           for o in observed['operators']):
        refuse('non-text operator in structural envelope')
    records = [{k: s[k] for k in ('id', 'tag', 'namespace', 'begin_byte_range', 'end_byte_range',
                'content_byte_range', 'properties', 'association')} for s in bundle]
    semantic = [{k: s[k] for k in ('tag', 'namespace', 'properties', 'association')} for s in bundle]
    return dict(status='supported-tree-backed', policy='first-existing-mcid-with-empty-siblings',
                scopes=records, mcids=mcids, identity_sha256=_digest(semantic),
                bundle_sha256=_digest(records))
