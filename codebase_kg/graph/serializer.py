"""Project a scan onto schema version 1.

The payload contains ids, metadata, and relationship endpoints. It does not
contain source listings. Re-indexing the same tree produces the same nodes and
relationships aside from ``indexed_at``.
"""

from __future__ import annotations

from codebase_kg.graph.schema import SCHEMA_VERSION
from codebase_kg.ids import issue_id
from codebase_kg.models import (
    Entity,
    EntityKind,
    GraphNode,
    GraphPayload,
    GraphRelationship,
    ResolutionStatus,
    ScanResult,
)

_KIND_LABELS = {
    EntityKind.MODULE: ("Entity", "Module"),
    EntityKind.CLASS: ("Entity", "Class"),
    EntityKind.FUNCTION: ("Entity", "Function"),
    EntityKind.METHOD: ("Entity", "Method"),
}


def serialize_scan(scan: ScanResult) -> GraphPayload:
    """Convert ``scan`` into mergeable nodes and relationships."""

    nodes: list[GraphNode] = []
    relationships: list[GraphRelationship] = []
    entities_by_id = {entity.id: entity for entity in scan.entities}

    for file_record in scan.files:
        nodes.append(
            GraphNode(
                id=file_record.id,
                labels=("File",),
                properties={
                    "id": file_record.id,
                    "repo_id": scan.repo_id,
                    "path": file_record.path,
                    "source_hash": file_record.source_hash,
                    "line_count": file_record.line_count,
                    "parse_ok": file_record.parse_ok,
                },
            )
        )
        relationships.append(GraphRelationship("CONTAINS", scan.repo_id, file_record.id, {}))

    for entity in scan.entities:
        nodes.append(_entity_node(entity))
        relationships.append(GraphRelationship("DEFINES", entity.file_id, entity.id, {}))
        if entity.parent_id and entity.parent_id in entities_by_id:
            parent = entities_by_id[entity.parent_id]
            if parent.kind is EntityKind.CLASS and entity.kind is EntityKind.METHOD:
                relationships.append(GraphRelationship("CONTAINS", parent.id, entity.id, {}))
            elif parent.kind in {EntityKind.MODULE, EntityKind.FUNCTION, EntityKind.METHOD}:
                if not (parent.kind is EntityKind.MODULE and entity.kind is EntityKind.METHOD):
                    relationships.append(GraphRelationship("DEFINES", parent.id, entity.id, {}))
        for base_id in entity.resolved_base_ids:
            relationships.append(GraphRelationship("INHERITS", entity.id, base_id, {}))

    for record in scan.imports:
        nodes.append(
            GraphNode(
                id=record.id,
                labels=("Import",),
                properties={
                    "id": record.id,
                    "repo_id": record.repo_id,
                    "file_id": record.file_id,
                    "file_path": record.file_path,
                    "module_id": record.module_id,
                    "module_qualified_name": record.module_qualified_name,
                    "lineno": record.lineno,
                    "ordinal": record.ordinal,
                    "level": record.level,
                    "is_from_import": record.is_from_import,
                    "is_module_level": record.is_module_level,
                    "source_module": record.source_module,
                    "imported_name": record.imported_name,
                    "alias": record.alias,
                    "local_name": record.local_name,
                    "resolved_module_id": record.resolved_module_id,
                    "resolved_module_qn": record.resolved_module_qn,
                    "resolved_entity_id": record.resolved_entity_id,
                    "resolved_entity_qn": record.resolved_entity_qn,
                    "statement": record.statement,
                },
            )
        )
        relationships.append(
            GraphRelationship("HAS_IMPORT", record.module_id, record.id, {"import_id": record.id})
        )
        targets: list[str] = []
        if record.resolved_module_id:
            targets.append(record.resolved_module_id)
        if record.resolved_entity_id and record.resolved_entity_id not in targets:
            targets.append(record.resolved_entity_id)
        for target_id in targets:
            relationships.append(
                GraphRelationship(
                    "IMPORTS",
                    record.module_id,
                    target_id,
                    {"import_id": record.id, "lineno": record.lineno, "statement": record.statement},
                )
            )

    for call in scan.calls:
        nodes.append(
            GraphNode(
                id=call.id,
                labels=("CallSite",),
                properties={
                    "id": call.id,
                    "repo_id": call.repo_id,
                    "file_path": call.file_path,
                    "caller_id": call.caller_id,
                    "caller_qualified_name": call.caller_qualified_name,
                    "lineno": call.lineno,
                    "ordinal": call.ordinal,
                    "raw_name": call.raw_name,
                    "status": call.status.value,
                    "reason": call.reason,
                    "callee_id": call.callee_id,
                    "callee_qualified_name": call.callee_qualified_name,
                },
            )
        )
        relationships.append(
            GraphRelationship("CALL_SITE", call.caller_id, call.id, {"call_site_id": call.id})
        )
        if call.status is ResolutionStatus.RESOLVED and call.callee_id:
            relationships.append(
                GraphRelationship(
                    "CALLS",
                    call.caller_id,
                    call.callee_id,
                    {
                        "ordinal": call.ordinal,
                        "lineno": call.lineno,
                        "raw_name": call.raw_name,
                        "call_site_id": call.id,
                    },
                )
            )

    for issue in scan.parse_errors:
        node_id = issue_id("parse_error", scan.repo_id, issue.path, issue.message)
        nodes.append(
            GraphNode(
                id=node_id,
                labels=("ParseError",),
                properties={
                    "id": node_id,
                    "repo_id": scan.repo_id,
                    "path": issue.path,
                    "message": issue.message,
                },
            )
        )
        file_match = next((item for item in scan.files if item.path == issue.path), None)
        if file_match is not None:
            relationships.append(GraphRelationship("HAS_PARSE_ERROR", file_match.id, node_id, {}))

    for skipped in scan.skipped:
        node_id = issue_id("skipped", scan.repo_id, skipped.path, skipped.reason)
        nodes.append(
            GraphNode(
                id=node_id,
                labels=("SkippedPath",),
                properties={
                    "id": node_id,
                    "repo_id": scan.repo_id,
                    "path": skipped.path,
                    "reason": skipped.reason,
                    "is_directory": skipped.is_directory,
                },
            )
        )

    nodes.sort(key=lambda node: (node.labels, node.id))
    relationships.sort(key=lambda rel: (rel.type, rel.start_id, rel.end_id, _rel_sort_key(rel.properties)))
    repository = {
        "id": scan.repo_id,
        "repo_id": scan.repo_id,
        "name": scan.repo_name,
        "root_path": scan.root_path,
        "indexed_at": scan.indexed_at,
        "schema_version": SCHEMA_VERSION,
        "file_count": len(scan.files),
        "indexed_file_count": scan.indexed_file_count,
        "entity_count": len(scan.entities),
        "import_count": len(scan.imports),
        "resolved_call_count": scan.resolved_call_count,
        "unresolved_call_count": scan.unresolved_call_count,
        "builtin_call_count": scan.builtin_call_count,
        "parse_error_count": len(scan.parse_errors),
        "skipped_count": len(scan.skipped),
        "embedding_count": 0,
    }
    return GraphPayload(schema_version=SCHEMA_VERSION, repository=repository, nodes=nodes, relationships=relationships)


def _entity_node(entity: Entity) -> GraphNode:
    return GraphNode(
        id=entity.id,
        labels=_KIND_LABELS[entity.kind],
        properties={
            "id": entity.id,
            "repo_id": entity.repo_id,
            "kind": entity.kind.value,
            "name": entity.name,
            "qualified_name": entity.qualified_name,
            "file_id": entity.file_id,
            "file_path": entity.file_path,
            "start_line": entity.start_line,
            "end_line": entity.end_line,
            "docstring": entity.docstring,
            "source_hash": entity.source_hash,
            "parent_id": entity.parent_id,
            "parent_qualified_name": entity.parent_qualified_name,
            "module_id": entity.module_id,
            "module_qualified_name": entity.module_qualified_name,
            "signature": entity.signature,
            "bases": list(entity.bases),
            "decorators": list(entity.decorators),
            "is_async": entity.is_async,
            "is_package": entity.is_package,
            "resolved_base_ids": list(entity.resolved_base_ids),
            "resolved_base_qns": list(entity.resolved_base_qns),
        },
    )


def _rel_sort_key(properties: dict[str, object]) -> str:
    return "|".join(f"{key}={properties[key]}" for key in sorted(properties))
