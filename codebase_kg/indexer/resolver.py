"""Resolve imports and calls against entities found in the same scan.

Resolution is lexical and structural. It does not infer types, evaluate
decorators, or follow ``getattr``, ``super()``, or calls on local instances.
"""

from __future__ import annotations

from dataclasses import dataclass

from codebase_kg.ids import call_id
from codebase_kg.indexer.extractor import FileExtract, RawCall
from codebase_kg.models import CallRecord, Entity, EntityKind, ImportRecord, ResolutionStatus

_BUILTINS = frozenset(dir(__import__("builtins")))


@dataclass
class _Binding:
    kind: str
    target: str


def resolve_scan(extracts: list[FileExtract], repo_id: str) -> tuple[list[Entity], list[ImportRecord], list[CallRecord]]:
    """Bind imports and calls for every successfully parsed file."""

    entities: list[Entity] = []
    imports: list[ImportRecord] = []
    raw_by_caller: dict[str, list[RawCall]] = {}
    for extract in extracts:
        entities.extend(extract.entities)
        imports.extend(extract.imports)
        for raw in extract.raw_calls:
            raw_by_caller.setdefault(raw.caller_qualified_name, []).append(raw)

    by_qn = {entity.qualified_name: entity for entity in entities}
    modules = {entity.qualified_name: entity for entity in entities if entity.kind is EntityKind.MODULE}
    classes = {entity.qualified_name: entity for entity in entities if entity.kind is EntityKind.CLASS}
    children: dict[str, list[Entity]] = {}
    methods_of: dict[str, dict[str, Entity]] = {}
    for entity in entities:
        if entity.parent_id:
            children.setdefault(entity.parent_id, []).append(entity)
        if entity.kind is EntityKind.METHOD and entity.parent_qualified_name:
            methods_of.setdefault(entity.parent_qualified_name, {})[entity.name] = entity

    module_bindings, binding_events = _module_bindings(imports, entities, modules)
    _resolve_class_bases(classes, binding_events, children, by_qn)
    calls: list[CallRecord] = []
    for entity in entities:
        for raw in raw_by_caller.get(entity.qualified_name, []):
            calls.append(
                _resolve_call(
                    raw,
                    caller=entity,
                    repo_id=repo_id,
                    by_qn=by_qn,
                    modules=modules,
                    classes=classes,
                    children=children,
                    methods_of=methods_of,
                    module_bindings=module_bindings,
                )
            )
    calls.sort(key=lambda item: (item.caller_qualified_name, item.lineno, item.ordinal, item.raw_name))
    return entities, imports, calls


def render_import_statement(record: ImportRecord) -> str:
    """Rebuild a readable import statement from a record."""

    if record.is_from_import:
        prefix = "." * record.level + (record.source_module or "")
        name = record.imported_name or "*"
        alias = f" as {record.alias}" if record.alias else ""
        return f"from {prefix} import {name}{alias}"
    alias = f" as {record.alias}" if record.alias else ""
    return f"import {record.source_module}{alias}"


def _module_bindings(
    imports: list[ImportRecord],
    entities: list[Entity],
    modules: dict[str, Entity],
) -> tuple[dict[str, dict[str, _Binding]], dict[str, list[tuple[int, int, str, object]]]]:
    """Return the final module bindings and the ordered events that produced them.

    Function bodies run after the module is executed, so calls use the final
    map. Class bases are evaluated immediately, so they replay events up to
    the class statement.
    """

    bindings: dict[str, dict[str, _Binding]] = {module_qn: {} for module_qn in modules}
    events: dict[str, list[tuple[int, int, str, object]]] = {module_qn: [] for module_qn in modules}
    for record in imports:
        if not record.is_module_level or record.module_qualified_name not in events:
            continue
        events[record.module_qualified_name].append((record.lineno, record.ordinal, "import", record))
    for entity in entities:
        if entity.kind not in {EntityKind.FUNCTION, EntityKind.CLASS}:
            continue
        if entity.parent_qualified_name != entity.module_qualified_name:
            continue
        events.setdefault(entity.module_qualified_name, []).append((entity.start_line, 0, "entity", entity))

    for module_qn, module_events in events.items():
        module_events.sort(key=lambda item: (item[0], item[1], 0 if item[2] == "entity" else 1))
        current = bindings[module_qn]
        module_entity = modules[module_qn]
        for _lineno, _ordinal, kind, payload in module_events:
            if kind == "entity":
                entity = payload
                assert isinstance(entity, Entity)
                current[entity.name] = _Binding("entity", entity.qualified_name)
                continue
            record = payload
            assert isinstance(record, ImportRecord)
            _bind_import(current, record, module_entity, modules, {item.qualified_name: item for item in entities})
    return bindings, events


def _bind_import(
    bindings: dict[str, _Binding],
    record: ImportRecord,
    module_entity: Entity,
    modules: dict[str, Entity],
    by_qn: dict[str, Entity],
) -> None:
    if record.imported_name == "*":
        return
    if not record.is_from_import:
        target = record.source_module or ""
        full = _longest_in_repo_module(target, modules)
        if full is not None:
            record.resolved_module_qn = full
            record.resolved_module_id = modules[full].id
        if record.alias:
            bindings[record.local_name] = _Binding("module" if target in modules else "external", target if target in modules else target)
            if target in modules:
                bindings[record.local_name] = _Binding("module", target)
            return
        root = target.split(".")[0] if target else ""
        if root and _module_in_repo(root, modules):
            bindings[root] = _Binding("module", root)
        elif root:
            bindings[root] = _Binding("external", root)
        return

    resolved_module = _resolve_from_module(module_entity, record, modules)
    if resolved_module and resolved_module in modules:
        record.resolved_module_qn = resolved_module
        record.resolved_module_id = modules[resolved_module].id
    entity_qn = f"{resolved_module}.{record.imported_name}" if resolved_module and record.imported_name else None
    if entity_qn and entity_qn in by_qn:
        target = by_qn[entity_qn]
        record.resolved_entity_qn = target.qualified_name
        record.resolved_entity_id = target.id
        if target.kind is EntityKind.MODULE:
            bindings[record.local_name] = _Binding("module", target.qualified_name)
        else:
            bindings[record.local_name] = _Binding("entity", target.qualified_name)
        return
    external_target = ".".join(
        part for part in ("." * record.level + (record.source_module or ""), record.imported_name) if part
    )
    bindings[record.local_name] = _Binding("external", external_target or record.local_name)


def _longest_in_repo_module(dotted: str, modules: dict[str, Entity]) -> str | None:
    """Return the longest prefix of ``dotted`` that is an indexed module."""

    if not dotted:
        return None
    parts = dotted.split(".")
    for length in range(len(parts), 0, -1):
        candidate = ".".join(parts[:length])
        if candidate in modules:
            return candidate
    return None


def _resolve_from_module(module_entity: Entity, record: ImportRecord, modules: dict[str, Entity]) -> str | None:
    if record.level == 0:
        candidate = record.source_module
        if candidate and _module_in_repo(candidate, modules):
            return candidate
        return None

    parts = module_entity.qualified_name.split(".")
    if not module_entity.is_package:
        parts = parts[:-1]
    if record.level - 1 > len(parts):
        return None
    if record.level == 1:
        base = parts
    else:
        base = parts[: len(parts) - (record.level - 1)]
    if record.source_module:
        candidate_parts = [*base, *record.source_module.split(".")]
    else:
        candidate_parts = base
    if not candidate_parts:
        return None
    candidate = ".".join(candidate_parts)
    if candidate in modules or _module_in_repo(candidate, modules):
        return candidate
    return None


def _module_in_repo(name: str, modules: dict[str, Entity]) -> bool:
    if name in modules:
        return True
    prefix = name + "."
    return any(module_name.startswith(prefix) for module_name in modules)


def _resolve_call(
    raw: RawCall,
    *,
    caller: Entity,
    repo_id: str,
    by_qn: dict[str, Entity],
    modules: dict[str, Entity],
    classes: dict[str, Entity],
    children: dict[str, list[Entity]],
    methods_of: dict[str, dict[str, Entity]],
    module_bindings: dict[str, dict[str, _Binding]],
) -> CallRecord:
    base = CallRecord(
        id=call_id(repo_id, caller.qualified_name, raw.lineno, raw.ordinal, raw.raw_name),
        repo_id=repo_id,
        file_path=caller.file_path,
        caller_id=caller.id,
        caller_qualified_name=caller.qualified_name,
        lineno=raw.lineno,
        ordinal=raw.ordinal,
        raw_name=raw.raw_name,
        status=ResolutionStatus.UNRESOLVED,
        reason=raw.dynamic_reason or "unresolved",
    )
    if raw.parts is None:
        return base

    parts = raw.parts
    if caller.kind is EntityKind.METHOD and parts[0] in {"self", "cls"}:
        return _resolve_self(base, caller, parts, classes, methods_of, by_qn)

    bindings = _scope_bindings(caller, module_bindings, children, by_qn)
    head, rest = parts[0], parts[1:]
    if head in bindings and bindings[head].kind == "entity" and not rest:
        return _resolved(base, by_qn[bindings[head].target])
    if not rest and head in _BUILTINS and head not in bindings:
        base.status = ResolutionStatus.BUILTIN
        base.reason = "Python builtin; not stored as a repository entity"
        return base
    binding = bindings.get(head)
    if binding is None:
        base.reason = "name not bound in the lexically visible module scope"
        return base
    if not rest:
        if binding.kind == "entity" and binding.target in by_qn:
            return _resolved(base, by_qn[binding.target])
        if binding.kind == "module":
            base.reason = "call target is a module, not a function or class"
            return base
        base.reason = "external name; not an entity in this repository"
        return base
    if binding.kind == "external":
        base.reason = "external module attribute is not resolved"
        return base
    if binding.kind == "module":
        return _walk_module(base, binding.target, rest, by_qn, modules)
    if binding.kind == "entity":
        target = by_qn.get(binding.target)
        if target and target.kind is EntityKind.CLASS and len(rest) == 1:
            method = _find_method(target.qualified_name, rest[0], classes, methods_of)
            if method is not None:
                return _resolved(base, method)
        base.reason = "attribute access on a value or instance is not resolved"
        return base
    base.reason = "unresolved"
    return base


def _resolve_self(
    base: CallRecord,
    caller: Entity,
    parts: list[str],
    classes: dict[str, Entity],
    methods_of: dict[str, dict[str, Entity]],
    by_qn: dict[str, Entity],
) -> CallRecord:
    if len(parts) != 2:
        base.reason = "only a single attribute on self or cls is resolved"
        return base
    if not caller.parent_qualified_name:
        base.reason = "method has no enclosing class"
        return base
    method = _find_method(caller.parent_qualified_name, parts[1], classes, methods_of)
    if method is None:
        base.reason = "method not found on the class or its statically known bases"
        return base
    return _resolved(base, by_qn[method.qualified_name])


def _resolve_class_bases(
    classes: dict[str, Entity],
    events: dict[str, list[tuple[int, int, str, object]]],
    children: dict[str, list[Entity]],
    by_qn: dict[str, Entity],
) -> None:
    """Link bases that are simple names already bound when the class statement runs."""

    for cls in classes.values():
        bound = _bindings_before_class(cls, events, children, by_qn)
        seen: set[str] = set()
        for expression in cls.bases:
            if not expression.replace(".", "").isidentifier():
                continue
            target = _lookup_base(expression, bound, by_qn)
            if target is None or target.id in seen or target.id == cls.id:
                continue
            seen.add(target.id)
            cls.resolved_base_ids.append(target.id)
            cls.resolved_base_qns.append(target.qualified_name)


def _bindings_before_class(
    cls: Entity,
    events: dict[str, list[tuple[int, int, str, object]]],
    children: dict[str, list[Entity]],
    by_qn: dict[str, Entity],
) -> dict[str, _Binding]:
    bound: dict[str, _Binding] = {}
    for lineno, _ordinal, kind, payload in events.get(cls.module_qualified_name, []):
        if lineno > cls.start_line or (lineno == cls.start_line and kind == "entity"):
            break
        if kind == "entity" and isinstance(payload, Entity):
            bound[payload.name] = _Binding("entity", payload.qualified_name)
        elif kind == "import" and isinstance(payload, ImportRecord) and payload.local_name != "*":
            _bind_import_snapshot(bound, payload, by_qn)
    if cls.parent_id:
        for sibling in children.get(cls.parent_id, []):
            if sibling.id == cls.id or sibling.start_line >= cls.start_line:
                continue
            if sibling.kind in {EntityKind.CLASS, EntityKind.FUNCTION}:
                bound[sibling.name] = _Binding("entity", sibling.qualified_name)
    return bound


def _bind_import_snapshot(bound: dict[str, _Binding], record: ImportRecord, by_qn: dict[str, Entity]) -> None:
    """Replay a module-level import using the ids already chosen by ``_bind_import``."""

    if record.resolved_entity_qn:
        target = by_qn.get(record.resolved_entity_qn)
        kind = "module" if target is not None and target.kind is EntityKind.MODULE else "entity"
        bound[record.local_name] = _Binding(kind, record.resolved_entity_qn)
        return
    if record.alias and record.resolved_module_qn:
        bound[record.local_name] = _Binding("module", record.resolved_module_qn)
        return
    if not record.is_from_import:
        root = (record.source_module or "").split(".")[0]
        if root and record.resolved_module_qn:
            bound[root] = _Binding("module", root)
        elif root:
            bound[root] = _Binding("external", root)


def _lookup_base(expression: str, bound: dict[str, _Binding], by_qn: dict[str, Entity]) -> Entity | None:
    parts = expression.split(".")
    binding = bound.get(parts[0])
    if binding is None or binding.kind != "entity" and binding.kind != "module":
        return None
    if binding.kind == "entity" and len(parts) == 1:
        target = by_qn.get(binding.target)
        return target if target is not None and target.kind is EntityKind.CLASS else None
    if binding.kind == "module":
        candidate = ".".join([binding.target, *parts[1:]]) if parts[1:] else binding.target
    else:
        # Imported class plus an attribute is not treated as a base class.
        return None
    target = by_qn.get(candidate)
    if target is not None and target.kind is EntityKind.CLASS:
        return target
    return None


def _find_method(
    class_qn: str,
    name: str,
    classes: dict[str, Entity],
    methods_of: dict[str, dict[str, Entity]],
    seen: set[str] | None = None,
) -> Entity | None:
    """Walk in-repo bases that were resolved for this scan.

    The walk is a straightforward parent chain, not Python's C3 MRO, and it
    never invents methods inherited from external classes.
    """

    seen = seen or set()
    if class_qn in seen:
        return None
    seen.add(class_qn)
    found = methods_of.get(class_qn, {}).get(name)
    if found is not None:
        return found
    cls = classes.get(class_qn)
    if cls is None:
        return None
    for base_qn in cls.resolved_base_qns:
        found = _find_method(base_qn, name, classes, methods_of, seen)
        if found is not None:
            return found
    return None


def _scope_bindings(
    caller: Entity,
    module_bindings: dict[str, dict[str, _Binding]],
    children: dict[str, list[Entity]],
    by_qn: dict[str, Entity],
) -> dict[str, _Binding]:
    bindings = dict(module_bindings.get(caller.module_qualified_name, {}))
    for child in children.get(caller.id, []):
        if child.kind in {EntityKind.FUNCTION, EntityKind.CLASS}:
            bindings[child.name] = _Binding("entity", child.qualified_name)
    parent = by_qn.get(caller.parent_qualified_name or "")
    if parent is not None and parent.kind in {EntityKind.FUNCTION, EntityKind.METHOD}:
        bindings[parent.name] = _Binding("entity", parent.qualified_name)
        for sibling in children.get(parent.id, []):
            if sibling.kind is EntityKind.FUNCTION:
                bindings[sibling.name] = _Binding("entity", sibling.qualified_name)
    return bindings


def _walk_module(
    base: CallRecord,
    module_qn: str,
    rest: list[str],
    by_qn: dict[str, Entity],
    modules: dict[str, Entity],
) -> CallRecord:
    if module_qn not in modules and not any(name.startswith(module_qn + ".") for name in modules):
        base.reason = "imported module is not part of this repository"
        return base
    current = module_qn
    for attr in rest[:-1]:
        nxt = f"{current}.{attr}"
        if nxt in modules:
            current = nxt
            continue
        base.reason = "module attribute path is not present in this repository"
        return base
    candidate = f"{current}.{rest[-1]}"
    if candidate in by_qn:
        return _resolved(base, by_qn[candidate])
    if candidate in modules:
        base.reason = "call target is a module, not a function or class"
        return base
    base.reason = "attribute not found in the imported module"
    return base


def _resolved(base: CallRecord, callee: Entity) -> CallRecord:
    base.status = ResolutionStatus.RESOLVED
    base.reason = None
    base.callee_id = callee.id
    base.callee_qualified_name = callee.qualified_name
    return base
