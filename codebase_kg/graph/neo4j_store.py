"""Neo4j persistence for schema version 1.

Writes are idempotent merges inside a transaction. User-controlled values are
bound as parameters. If the server cannot create a vector index, embeddings are
still stored on ``SearchChunk`` nodes and ranked in-process.
"""

from __future__ import annotations

import logging
from typing import Any

from codebase_kg.errors import GraphUnavailable
from codebase_kg.graph.memory import cosine
from codebase_kg.graph.schema import (
    CONSTRAINTS,
    DELETE_CHUNKS,
    DELETE_REPO_GRAPH,
    FETCH_EMBEDDINGS,
    GET_ENTITY,
    GET_REPOSITORY,
    INDEXES,
    LIST_CALLEES,
    LIST_CALLERS,
    LIST_ENTITIES,
    LIST_IMPORTS,
    LIST_PARSE_ERRORS,
    LIST_REPOSITORIES,
    LIST_SKIPPED,
    LIST_UNRESOLVED,
    MERGE_CHUNKS,
    MERGE_REPOSITORY,
    MERGE_SCHEMA_META,
    SCHEMA_VERSION,
    SET_EMBEDDING_COUNT,
    VECTOR_QUERY,
    COUNT_ENTITIES,
    label_query,
    relationship_query,
    vector_index_statement,
)
from codebase_kg.models import (
    CallLink,
    ChunkHit,
    ChunkRecord,
    EntityDetail,
    EntitySummary,
    GraphPayload,
    ImportLink,
    IssueView,
    RepositoryDetail,
    RepositorySummary,
    UnresolvedLink,
)

logger = logging.getLogger(__name__)
_BATCH = 100
_CHUNK_BATCH = 40


class Neo4jGraphStore:
    """Official Neo4j driver wrapper. Construct with :meth:`from_settings`."""

    def __init__(self, driver: Any, database: str, embedding_dimensions: int) -> None:
        self._driver = driver
        self._database = database
        self._dimensions = embedding_dimensions
        self.vector_ready = False

    @classmethod
    def from_settings(cls, settings: Any) -> Neo4jGraphStore:
        """Open a driver from application settings. The password is not logged."""

        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        )
        return cls(driver, settings.neo4j_database, settings.openai_embedding_dimensions)

    def close(self) -> None:
        self._driver.close()

    def ping(self) -> bool:
        """Return whether a trivial query succeeds. Failures are not logged with secrets."""

        try:
            with self._session() as session:
                record = session.run("RETURN 1 AS ok").single()
            return bool(record and record["ok"] == 1)
        except Exception as exc:
            logger.warning("Neo4j ping failed (%s)", type(exc).__name__)
            return False

    def ensure_schema(self) -> None:
        """Create constraints, indexes, and the vector index when the server supports it."""

        try:
            with self._session() as session:
                for statement in (*CONSTRAINTS, *INDEXES):
                    session.run(statement).consume()
                session.run(
                    MERGE_SCHEMA_META,
                    id="codebase-kg",
                    version=SCHEMA_VERSION,
                    dimensions=self._dimensions,
                ).consume()
                self.vector_ready = self._ensure_vector_index(session)
        except GraphUnavailable:
            raise
        except Exception as exc:
            self._raise_driver_error(exc)

    def replace_repository(self, payload: GraphPayload) -> None:
        """Delete the previous graph for this repository and merge the new payload."""

        try:
            with self._session() as session:
                session.execute_write(lambda tx: self._write_payload(tx, payload))
        except Exception as exc:
            self._raise_driver_error(exc)

    def set_embedding_count(self, repo_id: str, count: int) -> None:
        try:
            with self._session() as session:
                session.execute_write(lambda tx: self._write_embedding_count(tx, repo_id, count))
        except Exception as exc:
            self._raise_driver_error(exc)

    def upsert_chunks(self, chunks: list[ChunkRecord]) -> None:
        if not chunks:
            return
        try:
            with self._session() as session:
                session.execute_write(lambda tx: self._write_chunks(tx, chunks))
        except Exception as exc:
            self._raise_driver_error(exc)

    def list_repositories(self) -> list[RepositorySummary]:
        try:
            with self._session() as session:
                records = session.execute_read(lambda tx: list(tx.run(LIST_REPOSITORIES)))
        except Exception as exc:
            self._raise_driver_error(exc)
        return [_summary(_node(record)) for record in records]

    def get_repository(self, repo_id: str) -> RepositoryDetail | None:
        try:
            with self._session() as session:
                return session.execute_read(lambda tx: self._read_repository(tx, repo_id))
        except Exception as exc:
            self._raise_driver_error(exc)

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
        params = {"repo_id": repo_id, "kind": kind, "name": name, "file": file, "offset": offset, "limit": limit}
        try:
            with self._session() as session:
                total_record = session.execute_read(lambda tx: tx.run(COUNT_ENTITIES, **params).single())
                records = session.execute_read(lambda tx: list(tx.run(LIST_ENTITIES, **params)))
        except Exception as exc:
            self._raise_driver_error(exc)
        total = int(total_record["total"]) if total_record else 0
        return [_entity_summary(_node(record)) for record in records], total

    def get_entity(self, entity_id: str) -> EntityDetail | None:
        try:
            with self._session() as session:
                return session.execute_read(lambda tx: self._read_entity(tx, entity_id))
        except Exception as exc:
            self._raise_driver_error(exc)

    def callers(self, entity_id: str) -> list[CallLink] | None:
        detail = self.get_entity(entity_id)
        if detail is None:
            return None
        return detail.callers

    def callees(self, entity_id: str) -> tuple[list[CallLink], list[UnresolvedLink]] | None:
        detail = self.get_entity(entity_id)
        if detail is None:
            return None
        return detail.callees, detail.unresolved_calls

    def similarity_search(self, repo_id: str, embedding: list[float], k: int, min_score: float) -> list[ChunkHit]:
        """Prefer the Neo4j vector index, then fill from stored embeddings if needed."""

        hits: list[ChunkHit] = []
        if self.vector_ready:
            try:
                hits = self._vector_query(repo_id, embedding, k, min_score)
            except Exception as exc:
                logger.warning("Vector query failed (%s); falling back to stored embeddings", type(exc).__name__)
                self.vector_ready = False
                hits = []
        if len(hits) >= k:
            return hits[:k]
        try:
            fallback = self._scan_embeddings(repo_id, embedding, min_score)
        except Exception as exc:
            self._raise_driver_error(exc)
        merged = {item.chunk_id: item for item in fallback}
        for item in hits:
            current = merged.get(item.chunk_id)
            if current is None or item.score > current.score:
                merged[item.chunk_id] = item
        ordered = sorted(merged.values(), key=lambda item: (-item.score, item.qualified_name))
        return ordered[:k]

    def _session(self) -> Any:
        return self._driver.session(database=self._database)

    def _ensure_vector_index(self, session: Any) -> bool:
        statement = vector_index_statement(self._dimensions)
        try:
            session.run(statement).consume()
            return True
        except Exception as exc:
            logger.warning("Vector index is unavailable (%s). Embeddings will be ranked in-process.", type(exc).__name__)
            return False

    def _write_payload(self, tx: Any, payload: GraphPayload) -> None:
        repo_id = str(payload.repository["id"])
        tx.run(MERGE_REPOSITORY, id=repo_id, properties=dict(payload.repository)).consume()
        tx.run(DELETE_REPO_GRAPH, repo_id=repo_id).consume()
        grouped: dict[tuple[str, ...], list[dict[str, object]]] = {}
        for node in payload.nodes:
            grouped.setdefault(node.labels, []).append({"id": node.id, "properties": dict(node.properties)})
        for labels, rows in grouped.items():
            query = label_query(labels)
            for batch in _batches(rows, _BATCH):
                tx.run(query, rows=batch).consume()
        by_type: dict[str, list[dict[str, object]]] = {}
        for relationship in payload.relationships:
            row: dict[str, object] = {
                "start_id": relationship.start_id,
                "end_id": relationship.end_id,
                **relationship.properties,
            }
            by_type.setdefault(relationship.type, []).append(row)
        for rel_type, rows in by_type.items():
            query = relationship_query(rel_type)
            for batch in _batches(rows, _BATCH):
                tx.run(query, rows=batch).consume()

    def _write_chunks(self, tx: Any, chunks: list[ChunkRecord]) -> None:
        rows = []
        repo_ids: set[str] = set()
        for chunk in chunks:
            properties = dict(chunk.properties)
            properties["embedding"] = [float(value) for value in chunk.embedding]
            rows.append({"id": chunk.id, "entity_id": chunk.entity_id, "properties": properties})
            repo_ids.add(chunk.repo_id)
        for batch in _batches(rows, _CHUNK_BATCH):
            tx.run(MERGE_CHUNKS, rows=batch).consume()
        for repo_id in repo_ids:
            count_record = tx.run(
                "MATCH (c:SearchChunk {repo_id: $repo_id}) RETURN count(c) AS total",
                repo_id=repo_id,
            ).single()
            total = int(count_record["total"]) if count_record else 0
            tx.run(SET_EMBEDDING_COUNT, repo_id=repo_id, count=total).consume()

    def _write_embedding_count(self, tx: Any, repo_id: str, count: int) -> None:
        if count == 0:
            tx.run(DELETE_CHUNKS, repo_id=repo_id).consume()
        tx.run(SET_EMBEDDING_COUNT, repo_id=repo_id, count=count).consume()

    def _read_repository(self, tx: Any, repo_id: str) -> RepositoryDetail | None:
        record = tx.run(GET_REPOSITORY, id=repo_id).single()
        if record is None:
            return None
        parse_errors = [
            IssueView(path=str(row["path"]), message=str(row["message"]))
            for row in tx.run(LIST_PARSE_ERRORS, repo_id=repo_id)
        ]
        skipped = [
            IssueView(path=str(row["path"]), message=str(row["message"]), is_directory=bool(row["is_directory"]))
            for row in tx.run(LIST_SKIPPED, repo_id=repo_id)
        ]
        return RepositoryDetail(_summary(_node(record)), parse_errors, skipped)

    def _read_entity(self, tx: Any, entity_id: str) -> EntityDetail | None:
        record = tx.run(GET_ENTITY, id=entity_id).single()
        if record is None:
            return None
        props = _node(record)
        callers = [_call_link(row) for row in tx.run(LIST_CALLERS, id=entity_id)]
        callees = [_call_link(row) for row in tx.run(LIST_CALLEES, id=entity_id)]
        unresolved = [
            UnresolvedLink(
                lineno=int(row["lineno"]),
                raw_name=str(row["raw_name"]),
                status=str(row["status"]),
                reason=None if row["reason"] is None else str(row["reason"]),
            )
            for row in tx.run(LIST_UNRESOLVED, id=entity_id)
        ]
        imports = []
        module_id = props.get("module_id")
        if module_id:
            for row in tx.run(LIST_IMPORTS, module_id=module_id):
                imp = _node(row)
                target = imp.get("resolved_entity_qn") or imp.get("resolved_module_qn")
                imports.append(
                    ImportLink(
                        lineno=int(imp["lineno"]),
                        statement=str(imp.get("statement") or ""),
                        target_qualified_name=None if target is None else str(target),
                        in_repository=bool(imp.get("resolved_entity_id") or imp.get("resolved_module_id")),
                    )
                )
        return EntityDetail(
            summary=_entity_summary(props),
            source_hash=None if props.get("source_hash") is None else str(props.get("source_hash")),
            bases=[str(item) for item in props.get("bases") or []],
            is_async=bool(props.get("is_async")),
            callers=callers,
            callees=callees,
            unresolved_calls=unresolved,
            imports=imports,
        )

    def _vector_query(self, repo_id: str, embedding: list[float], k: int, min_score: float) -> list[ChunkHit]:
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(
                    tx.run(
                        VECTOR_QUERY,
                        repo_id=repo_id,
                        embedding=embedding,
                        min_score=min_score,
                        k=k,
                        candidate_limit=max(k * 20, 100),
                    )
                )
            )
        return [_hit_from_row(row, float(row["score"])) for row in records]

    def _scan_embeddings(self, repo_id: str, embedding: list[float], min_score: float) -> list[ChunkHit]:
        with self._session() as session:
            records = session.execute_read(lambda tx: list(tx.run(FETCH_EMBEDDINGS, repo_id=repo_id)))
        hits: list[ChunkHit] = []
        for row in records:
            vector = row["embedding"]
            if vector is None:
                continue
            score = cosine(embedding, [float(value) for value in vector])
            if score < min_score:
                continue
            hits.append(_hit_from_row(row, score))
        return hits

    def _raise_driver_error(self, exc: Exception) -> None:
        if exc.__class__.__module__.startswith("neo4j"):
            logger.warning("Neo4j operation failed (%s)", type(exc).__name__)
            raise GraphUnavailable("The graph database is unavailable.") from exc
        raise exc


def _batches(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _node(record: Any) -> dict[str, Any]:
    value = record["node"]
    return dict(value)


def _summary(properties: dict[str, Any]) -> RepositorySummary:
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


def _entity_summary(properties: dict[str, Any]) -> EntitySummary:
    return EntitySummary(
        id=str(properties["id"]),
        kind=str(properties["kind"]),
        name=str(properties["name"]),
        qualified_name=str(properties["qualified_name"]),
        repository_id=str(properties["repo_id"]),
        file_path=str(properties["file_path"]),
        start_line=int(properties["start_line"]),
        end_line=int(properties["end_line"]),
        signature=None if properties.get("signature") is None else str(properties.get("signature")),
        docstring=None if properties.get("docstring") is None else str(properties.get("docstring")),
    )


def _call_link(row: Any) -> CallLink:
    return CallLink(
        entity_id=str(row["entity_id"]),
        name=str(row["name"]),
        qualified_name=str(row["qualified_name"]),
        kind=str(row["kind"]),
        file_path=str(row["file_path"]),
        lineno=int(row["lineno"]),
        raw_name=str(row["raw_name"]),
    )


def _hit_from_row(row: Any, score: float) -> ChunkHit:
    return ChunkHit(
        chunk_id=str(row["id"]),
        entity_id=str(row["entity_id"]),
        score=score,
        text=str(row["text"] or ""),
        qualified_name=str(row["qualified_name"] or ""),
        name=str(row["name"] or ""),
        kind=str(row["kind"] or ""),
        file_path=str(row["file_path"] or ""),
        start_line=int(row["start_line"] or 0),
        end_line=int(row["end_line"] or 0),
    )
