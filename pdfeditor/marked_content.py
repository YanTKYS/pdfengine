"""Read-only marked-content provenance, separate from graphics state and layout.

An MCID identifies a source content item, not a paragraph or a movable object.
Only a checked ParentTree and StructureTree backlink establish a structural
association. Even that association makes no claim about editing ownership.
"""
from __future__ import annotations

import hashlib
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
        roles = _object(self.root.get("/RoleMap", {}))
        visited = set()
        while resolved in roles:
            if resolved in visited:
                return {"declared": declared, "mapped": None, "error": "RoleMap cycle"}
            visited.add(resolved)
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
            result.append({"reference": _reference(reference), "type": str(node.get("/Type")),
                           "role": self._role(node.get("/S")), "page_reference": _reference(node.get("/Pg")),
                           "language": _plain(node.get("/Lang"))})
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
