"""In-memory graph used by unit tests and local runs without Neo4j.

Behavior here is the reference for repository replacement, entity lookup, and
cosine retrieval. :class:`codebase_kg.graph.neo4j_store.Neo4jGraphStore` persists
the same payload.
"""

from __future__ import annotations

import math

from codebase_kg.models import (
    CallLink,
    ChunkHit,
    ChunkRecord,
    EntityDetail,
    EntitySummary,
    GraphNode,
    GraphPayload,
    GraphRelationship,
    ImportLink,
    IssueView,
    RepositoryDetail,
    RepositorySummary,
    UnresolvedLink,
)


class InMemoryGraphStore:
    """Process-local stand-in for the Neo4j store."""

    def __init__(self) -> None:
        self.repositories: dict[str, dict[str, object]] = {}
        self.nodes: dict[str, GraphNode] = {}
        self.relationships: list[GraphRelationship] = []
        self.chunks: dict[str, dict[str, object]] = {}

    def close(self) -> None:
        """Nothing to release."""

    def ping(self) -> bool:
        """The in-memory store is always reachable."""

        return True

    def ensure_schema(self) -> None:
        """Schema constraints apply only to Neo4j."""

    def replace_repository(self, payload: GraphPayload) -> None:
        """Replace every node that belongs to this repository and insert ``payload``."""

        repo_id = str(payload.repository["id"])
        self._drop_repo(repo_id)
        self.repositories[repo_id] = dict(payload.repository)
        for node in payload.nodes:
            self.nodes[node.id] = node
        self.relationships.extend(payload.relationships)

    def set_embedding_count(self, repo_id: str, count: int) -> None:
        """Record how many search chunks the repository has, clearing them at zero."""

        if repo_id in self.repositories:
            self.repositories[repo_id]["embedding_count"] = count
        if count == 0:
            self.chunks = {key: value for key, value in self.chunks.items() if value.get("repo_id") != repo_id}

    def upsert_chunks(self, chunks: list[ChunkRecord]) -> None:
        """Insert or replace search chunks and refresh the repository count."""

        if not chunks:
            return
        repo_ids: set[str] = set()
        for chunk in chunks:
            properties = dict(chunk.properties)
            properties["embedding"] = list(chunk.embedding)
            self.chunks[chunk.id] = properties
            repo_ids.add(chunk.repo_id)
        for repo_id in repo_ids:
            count = sum(1 for item in self.chunks.values() if item.get("repo_id") == repo_id)
            if repo_id in self.repositories:
                self.repositories[repo_id]["embedding_count"] = count

    def list_repositories(self) -> list[RepositorySummary]:
        summaries = [self._summary(properties) for properties in self.repositories.values()]
        return sorted(summaries, key=lambda item: item.name)

    def get_repository(self, repo_id: str) -> RepositoryDetail | None:
        properties = self.repositories.get(repo_id)
        if properties is None:
            return None
        parse_errors = [
            IssueView(path=str(node.properties["path"]), message=str(node.properties["message"]))
            for node in self._labeled("ParseError")
            if node.properties.get("repo_id") == repo_id
        ]
        skipped = [
            IssueView(
                path=str(node.properties["path"]),
                message=str(node.properties["reason"]),
                is_directory=bool(node.properties.get("is_directory")),
            )
            for node in self._labeled("SkippedPath")
            if node.properties.get("repo_id") == repo_id
        ]
        parse_errors.sort(key=lambda item: item.path)
        skipped.sort(key=lambda item: item.path)
        return RepositoryDetail(self._summary(properties), parse_errors[:200], skipped[:500])

    def list_entities(
        self,
        repo_id: str,
        *,
        kind: str | None,
        name: str | None,
        file: str | None,
        offset: int,
        limit: int,
    ) -> tuple[list[EntitySummary], int]:
        rows = []
        for node in self._labeled("Entity"):
            props = node.properties
            if props.get("repo_id") != repo_id:
                continue
            if kind is not None and props.get("kind") != kind:
                continue
            if name is not None and name not in str(props.get("name")) and name not in str(props.get("qualified_name")):
                continue
            if file is not None and file not in str(props.get("file_path")):
                continue
            rows.append(props)
        rows.sort(key=lambda props: (str(props["file_path"]), int(props["start_line"]), str(props["qualified_name"])))
        page = rows[offset : offset + limit]
        return [_entity_summary(props) for props in page], len(rows)

    def get_entity(self, entity_id: str) -> EntityDetail | None:
        node = self.nodes.get(entity_id)
        if node is None or "Entity" not in node.labels:
            return None
        summary = _entity_summary(node.properties)
        return EntityDetail(
            summary=summary,
            source_hash=_optional_str(node.properties.get("source_hash")),
            bases=[str(item) for item in node.properties.get("bases", [])],
            is_async=bool(node.properties.get("is_async")),
            callers=self.callers(entity_id) or [],
            callees=self._resolved_callees(entity_id),
            unresolved_calls=self._unresolved(entity_id),
            imports=self._imports(str(node.properties.get("module_id") or "")),
        )

    def callers(self, entity_id: str) -> list[CallLink] | None:
        if self.nodes.get(entity_id) is None or "Entity" not in self.nodes[entity_id].labels:
            return None
        links: list[CallLink] = []
        for relationship in self.relationships:
            if relationship.type != "CALLS" or relationship.end_id != entity_id:
                continue
            caller = self.nodes.get(relationship.start_id)
            if caller is None:
                continue
            links.append(_call_link(caller.properties, relationship.properties))
        links.sort(key=lambda item: (item.qualified_name, item.lineno, item.raw_name))
        return links

    def callees(self, entity_id: str) -> tuple[list[CallLink], list[UnresolvedLink]] | None:
        if self.nodes.get(entity_id) is None or "Entity" not in self.nodes[entity_id].labels:
            return None
        return self._resolved_callees(entity_id), self._unresolved(entity_id)

    def similarity_search(self, repo_id: str, embedding: list[float], k: int, min_score: float) -> list[ChunkHit]:
        """Rank this repository's chunks by cosine similarity."""

        scored: list[ChunkHit] = []
        for properties in self.chunks.values():
            if properties.get("repo_id") != repo_id:
                continue
            vector = properties.get("embedding")
            if not isinstance(vector, list):
                continue
            score = cosine(embedding, [float(item) for item in vector])
            if score < min_score:
                continue
            scored.append(_chunk_hit(properties, score))
        scored.sort(key=lambda item: (-item.score, item.qualified_name))
        return scored[:k]

    def _drop_repo(self, repo_id: str) -> None:
        removed = {node_id for node_id, node in self.nodes.items() if node.properties.get("repo_id") == repo_id}
        for node_id in removed:
            del self.nodes[node_id]
        self.relationships = [
            item for item in self.relationships if item.start_id not in removed and item.end_id not in removed
        ]
        self.chunks = {key: value for key, value in self.chunks.items() if value.get("repo_id") != repo_id}

    def _labeled(self, label: str) -> list[GraphNode]:
        return [node for node in self.nodes.values() if label in node.labels]

    def _resolved_callees(self, entity_id: str) -> list[CallLink]:
        links: list[CallLink] = []
        for relationship in self.relationships:
            if relationship.type != "CALLS" or relationship.start_id != entity_id:
                continue
            callee = self.nodes.get(relationship.end_id)
            if callee is None:
                continue
            links.append(_call_link(callee.properties, relationship.properties))
        links.sort(key=lambda item: (item.qualified_name, item.lineno, item.raw_name))
        return links

    def _unresolved(self, entity_id: str) -> list[UnresolvedLink]:
        links: list[UnresolvedLink] = []
        for node in self._labeled("CallSite"):
            props = node.properties
            if props.get("caller_id") != entity_id or props.get("status") == "resolved":
                continue
            links.append(
                UnresolvedLink(
                    lineno=int(props["lineno"]),
                    raw_name=str(props["raw_name"]),
                    status=str(props["status"]),
                    reason=_optional_str(props.get("reason")),
                )
            )
        links.sort(key=lambda item: (item.lineno, item.raw_name))
        return links

    def _imports(self, module_id: str) -> list[ImportLink]:
        links: list[ImportLink] = []
        for node in self._labeled("Import"):
            props = node.properties
            if props.get("module_id") != module_id:
                continue
            target = _optional_str(props.get("resolved_entity_qn")) or _optional_str(props.get("resolved_module_qn"))
            links.append(
                ImportLink(
                    lineno=int(props["lineno"]),
                    statement=str(props.get("statement") or ""),
                    target_qualified_name=target,
                    in_repository=bool(props.get("resolved_entity_id") or props.get("resolved_module_id")),
                )
            )
        links.sort(key=lambda item: (item.lineno, item.statement))
        return links

    def _summary(self, properties: dict[str, object]) -> RepositorySummary:
        return RepositorySummary(
            id=str(properties["id"]),
            name=str(properties["name"]),
            root_path=str(properties["root_path"]),
            indexed_at=str(properties["indexed_at"]),
            schema_version=int(properties["schema_version"]),
            file_count=int(properties["file_count"]),
            indexed_file_count=int(properties["indexed_file_count"]),
            entity_count=int(properties["entity_count"]),
            import_count=int(properties["import_count"]),
            resolved_call_count=int(properties["resolved_call_count"]),
            unresolved_call_count=int(properties["unresolved_call_count"]),
            builtin_call_count=int(properties["builtin_call_count"]),
            parse_error_count=int(properties["parse_error_count"]),
            skipped_count=int(properties["skipped_count"]),
            embedding_count=int(properties.get("embedding_count") or 0),
        )


def cosine(left: list[float], right: list[float]) -> float:
    """Return cosine similarity, or ``0.0`` when a vector is empty or mismatched."""

    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _entity_summary(properties: dict[str, object]) -> EntitySummary:
    return EntitySummary(
        id=str(properties["id"]),
        kind=str(properties["kind"]),
        name=str(properties["name"]),
        qualified_name=str(properties["qualified_name"]),
        repository_id=str(properties["repo_id"]),
        file_path=str(properties["file_path"]),
        start_line=int(properties["start_line"]),
        end_line=int(properties["end_line"]),
        signature=_optional_str(properties.get("signature")),
        docstring=_optional_str(properties.get("docstring")),
    )


def _call_link(properties: dict[str, object], relationship: dict[str, object]) -> CallLink:
    return CallLink(
        entity_id=str(properties["id"]),
        name=str(properties["name"]),
        qualified_name=str(properties["qualified_name"]),
        kind=str(properties["kind"]),
        file_path=str(properties["file_path"]),
        lineno=int(relationship["lineno"]),
        raw_name=str(relationship["raw_name"]),
    )


def _chunk_hit(properties: dict[str, object], score: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=str(properties["id"]),
        entity_id=str(properties["entity_id"]),
        score=score,
        text=str(properties.get("text") or ""),
        qualified_name=str(properties.get("qualified_name") or ""),
        name=str(properties.get("name") or ""),
        kind=str(properties.get("kind") or ""),
        file_path=str(properties.get("file_path") or ""),
        start_line=int(properties.get("start_line") or 0),
        end_line=int(properties.get("end_line") or 0),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
